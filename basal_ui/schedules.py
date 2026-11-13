"""Scheduled Batch runs over changed JSONL files in configured folders."""

import fnmatch
import re
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path


def validate(definition):
    if not isinstance(definition, dict):
        raise ValueError("schedule must be an object")
    required = {"model", "input_dir", "output_dir"}
    if required - definition.keys():
        raise ValueError(f"missing schedule fields: {', '.join(sorted(required - definition.keys()))}")
    model = definition["model"]
    if not isinstance(model, str) or not model.strip():
        raise ValueError("model must be a non-empty string")
    paths = {}
    for key in ("input_dir", "output_dir"):
        raw = definition[key]
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(f"{key} must be an absolute directory")
        path = Path(raw).expanduser()
        if not path.is_absolute():
            raise ValueError(f"{key} must be an absolute directory")
        paths[key] = str(path.resolve())
    pattern = definition.get("pattern", "*.jsonl")
    if not isinstance(pattern, str) or not pattern or "/" in pattern or "\\" in pattern or "\0" in pattern:
        raise ValueError("pattern must be a glob without path separators")
    interval = definition.get("interval")
    if not isinstance(interval, dict) or len(interval) != 1:
        raise ValueError("interval must contain every_minutes or daily_at")
    if "every_minutes" in interval:
        minutes = interval["every_minutes"]
        if type(minutes) is not int or not 1 <= minutes <= 525600:
            raise ValueError("every_minutes must be an integer between 1 and 525600")
        interval = {"every_minutes": minutes}
    elif "daily_at" in interval:
        daily = interval["daily_at"]
        if not isinstance(daily, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", daily):
            raise ValueError("daily_at must be a valid HH:MM time")
        interval = {"daily_at": daily}
    else:
        raise ValueError("interval must contain every_minutes or daily_at")
    concurrency = definition.get("concurrency", 16)
    if type(concurrency) is not int or not 1 <= concurrency <= 256:
        raise ValueError("concurrency must be an integer between 1 and 256")
    for key in ("overwrite", "catch_up", "paused"):
        if type(definition.get(key, False)) is not bool:
            raise ValueError(f"{key} must be a boolean")
    schedule_id = definition.get("id") or uuid.uuid4().hex[:12]
    if not isinstance(schedule_id, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,39}", schedule_id):
        raise ValueError("id must be 1..40 lowercase letters, digits or hyphens, starting with a letter or digit")
    return {
        "id": schedule_id,
        "model": model.strip(),
        **paths,
        "pattern": pattern,
        "concurrency": concurrency,
        "interval": interval,
        "overwrite": definition.get("overwrite", False),
        "catch_up": definition.get("catch_up", False),
        "paused": definition.get("paused", False),
        **({"paused_reason": definition["paused_reason"]} if definition.get("paused_reason") else {}),
    }


def candidates(spec):
    return sorted(
        (
            path
            for path in Path(spec["input_dir"]).iterdir()
            if fnmatch.fnmatchcase(path.name, spec["pattern"])
            and not path.name.endswith(".answers.jsonl")
            and not path.name.startswith(".basal-ui-schedule-")
        ),
        key=lambda path: path.name,
    )


class Schedules:
    def __init__(self, settings_mod, fleet, start_batch):
        self.settings, self.fleet, self.start_batch = settings_mod, fleet, start_batch
        self.lock = threading.RLock()
        self.active = None
        self.pending = {}
        self.settings.recover_schedule_publications()

    def list(self):
        return self.settings.schedules()

    def get(self, schedule_id):
        for spec in self.list():
            if spec["id"] == schedule_id:
                return spec
        raise KeyError(schedule_id)

    def create(self, body):
        spec = validate(body)
        spec["_token"] = uuid.uuid4().hex

        def add(rows):
            if any(row["id"] == spec["id"] for row in rows):
                raise ValueError(f"schedule {spec['id']!r} already exists")
            rows.append(spec)
            return spec

        with self.lock:
            return self.settings.update_schedules(add)

    def update(self, schedule_id, body, validator=validate):
        def change(rows):
            for index, old in enumerate(rows):
                if old["id"] == schedule_id:
                    spec = validator({**old, **body, "id": schedule_id})
                    spec["_token"] = old["_token"]
                    if not spec["paused"]:
                        spec.pop("paused_reason", None)
                    rows[index] = spec
                    return spec
            raise KeyError(schedule_id)

        with self.lock:
            spec = self.settings.update_schedules(change, reset_state_id=schedule_id)
            self.pending.pop(schedule_id, None)
            return spec

    def delete(self, schedule_id):
        def remove(rows):
            for index, row in enumerate(rows):
                if row["id"] == schedule_id:
                    rows.pop(index)
                    return
            raise KeyError(schedule_id)

        with self.lock:
            self.settings.update_schedules(remove)
            self.pending.pop(schedule_id, None)

    def runs(self, schedule_id, cursor=0):
        runs = self.settings.schedule_runs(schedule_id, cursor)
        if not runs["runs"] and (cursor == 0 or not self.settings.schedule_runs(schedule_id, 0)["runs"]):
            self.get(schedule_id)
        return runs

    def preview(self, schedule_id):
        from . import paths

        with self.lock:
            spec = self.get(schedule_id)
            directory = paths.check(spec["input_dir"], "input_dir")
            if not directory["ok"]:
                raise ValueError(f"input_dir: {directory['code']}")
            processed = self.settings.schedule_state(schedule_id).get("processed", {})
            for matched in candidates(spec):
                if not matched.is_file():
                    continue
                path = matched.resolve()
                stat = path.stat()
                if processed.get(str(path)) != [stat.st_size, stat.st_mtime_ns]:
                    return {"file": str(matched), "inspection": paths.check(str(path), "jsonl")}
            return {"file": None, "inspection": None}

    @staticmethod
    def _time(now):
        return now if isinstance(now, datetime) else datetime.fromtimestamp(float(now))

    def _due(self, spec, now, force=False):
        if force:
            return True
        if spec["paused"]:
            return False
        state = self.settings.schedule_state(spec["id"])
        last = state.get("last_scan")
        current = self._time(now)
        if "every_minutes" in spec["interval"]:
            return last is None or current.timestamp() - last >= spec["interval"]["every_minutes"] * 60
        slot_time = datetime.strptime(spec["interval"]["daily_at"], "%H:%M").time()
        today_slot = datetime.combine(current.date(), slot_time).timestamp()
        latest_slot = today_slot
        if current.timestamp() < today_slot:
            latest_slot = datetime.combine(current.date() - timedelta(days=1), slot_time).timestamp()
        if spec["catch_up"]:
            return (last is None and current.timestamp() >= today_slot) or (last is not None and last < latest_slot)
        return current.strftime("%H:%M") == spec["interval"]["daily_at"] and (last is None or last < today_slot)

    def _record(self, spec, row, checkpoint=False):
        self.settings.append_schedule_run(row, spec, checkpoint)
        try:
            current = self.get(spec["id"])
            if current.get("paused") and current != spec:
                self.pending.pop(spec["id"], None)
        except KeyError:
            self.pending.pop(spec["id"], None)

    def _finish_active(self):
        if self.active is None:
            return False
        spec, row, job = self.active
        state = job.state()
        if state["running"]:
            return True
        result = state.get("result") or {}
        row.update(
            duration=max(0, state["finished"] - state["started"]),
            rows=result.get("items", 0),
            errors=result.get("errors", 0),
        )
        if state["phase"] == "cancelled":
            row["cancelled"] = "cancelled by user"
        elif state["phase"] not in ("done", "done_with_warnings"):
            row.update(error=state.get("error") or state["phase"], errors=1)
        elif row["errors"]:
            row["error"] = "one or more requests failed"
        self._record(spec, row, checkpoint=True)
        self.active = None
        return False

    def run_due(self, now=None, schedule_id=None, force=False):
        now = time.time() if now is None else now
        timestamp = self._time(now).timestamp()
        with self.lock:
            self.settings.recover_schedule_publications()
            running = self._finish_active()
            outcome = "no_new_files"
            for spec in self.list():
                sid = spec["id"]
                if schedule_id is not None and sid != schedule_id:
                    continue
                forced_queue = self.pending.get(sid, {}).get("forced", False)
                if spec["paused"] and not force and not forced_queue:
                    self.pending.pop(sid, None)
                    continue
                if sid not in self.pending and self._due(spec, now, force):
                    from . import paths

                    try:
                        for key, kind in (("input_dir", "input_dir"), ("output_dir", "output_dir")):
                            facts = paths.check(spec[key], kind)
                            if not facts["ok"]:
                                raise ValueError(f"{key}: {facts['code']}")
                        queue = candidates(spec)
                    except (OSError, ValueError) as exc:
                        row = {
                            "run_id": uuid.uuid4().hex,
                            "schedule": sid,
                            "model": spec["model"],
                            "at": timestamp,
                            "duration": 0,
                            "file": None,
                            "output": None,
                            "rows": 0,
                            "errors": 1,
                            "error": str(exc),
                        }
                        self.settings.update_schedule_state(sid, lambda state: state.update(last_scan=timestamp))
                        self._record(spec, row)
                        outcome = "failed"
                        continue
                    self.settings.update_schedule_state(sid, lambda state: state.update(last_scan=timestamp))
                    self.pending[sid] = {"items": queue, "forced": force, "spec": spec}
                if running:
                    if self.pending.get(sid, {}).get("items"):
                        outcome = "queued"
                    continue
                pending = self.pending.get(sid)
                if pending and pending["spec"] != spec:
                    self.pending.pop(sid, None)
                    continue
                queue = pending["items"] if pending else []
                while queue:
                    matched = queue[0]
                    try:
                        path = matched.resolve()
                        stat = path.stat()
                        if not path.is_file():
                            queue.pop(0)
                            continue
                    except (OSError, RuntimeError) as exc:
                        row = {
                            "run_id": uuid.uuid4().hex,
                            "schedule": sid,
                            "model": spec["model"],
                            "at": timestamp,
                            "duration": 0,
                            "file": str(matched),
                            "output": None,
                            "rows": 0,
                            "errors": 1,
                            "error": str(exc),
                        }
                        self._record(spec, row)
                        queue.pop(0)
                        outcome = "failed"
                        if sid not in self.pending:
                            break
                        continue
                    signature = [stat.st_size, stat.st_mtime_ns]
                    if self.settings.schedule_state(sid).get("processed", {}).get(str(path)) == signature:
                        queue.pop(0)
                        continue
                    output = Path(spec["output_dir"]) / f"{matched.stem}.answers.jsonl"
                    row = {
                        "run_id": uuid.uuid4().hex,
                        "schedule": sid,
                        "model": spec["model"],
                        "at": timestamp,
                        "duration": 0,
                        "file": str(path),
                        "output": str(output),
                        "size": signature[0],
                        "mtime": signature[1],
                        "rows": 0,
                        "errors": 0,
                    }
                    if not spec["overwrite"] and output.exists():
                        self._record(spec, {**row, "skipped": "output exists"})
                        queue.pop(0)
                        outcome = "skipped"
                        continue
                    try:
                        runner = self.fleet.ready(spec["model"])
                    except (KeyError, RuntimeError) as exc:
                        reason = "model not loaded" if isinstance(exc, KeyError) else "model not ready"
                        self._record(spec, {**row, "skipped": reason})
                        queue.pop(0)
                        outcome = "skipped"
                        continue
                    try:
                        job = self.start_batch(spec, path, output, signature, runner, row)
                    except KeyError:
                        self._record(spec, {**row, "skipped": "model not loaded"})
                        queue.pop(0)
                        outcome = "skipped"
                        continue
                    except RuntimeError as exc:
                        if "already running" in str(exc):
                            return {"status": "queued"}
                        if "model changed during job preparation" in str(exc):
                            self._record(spec, {**row, "skipped": "model not ready"})
                            queue.pop(0)
                            outcome = "skipped"
                            continue
                        self._record(spec, {**row, "error": str(exc), "errors": 1}, checkpoint=True)
                        queue.pop(0)
                        outcome = "failed"
                        if sid not in self.pending:
                            break
                        continue
                    queue.pop(0)
                    row["at"] = job.state()["started"]
                    self.active = (spec, row, job)
                    return {"status": "started", "job": job.state()}
                self.pending.pop(sid, None)
            return {"status": outcome}

    def run_now(self, schedule_id, now=None):
        self.get(schedule_id)
        return self.run_due(now, schedule_id, force=True)
