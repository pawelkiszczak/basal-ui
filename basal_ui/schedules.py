"""Scheduled Batch runs over changed JSONL files in configured folders."""

import contextlib
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
        if type(minutes) is not int or minutes < 1:
            raise ValueError("every_minutes must be an integer of at least 1")
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
    preset = definition.get("preset")
    lang = definition.get("lang", "auto")
    if preset is not None and (not isinstance(preset, str) or not preset):
        raise ValueError("preset must be a non-empty string")
    if lang not in ("auto", "pl", "en"):
        raise ValueError("lang must be one of auto, pl, en")
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
        **({"preset": preset} if preset is not None else {}),
        "lang": lang,
    }


class Schedules:
    def __init__(self, settings_mod, fleet, start_batch):
        self.settings = settings_mod
        self.fleet = fleet
        self.start_batch = start_batch
        self.lock = threading.RLock()
        self.active = None
        self.failures = {}
        self.pending = {}

    def list(self):
        return self.settings.schedules()

    def create(self, body):
        spec = validate(body)

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
                    rows[index] = spec
                    return spec
            raise KeyError(schedule_id)

        with self.lock:
            result = self.settings.update_schedules(change)
            self.pending.pop(schedule_id, None)
            return result

    def delete(self, schedule_id):
        def remove(rows):
            for index, row in enumerate(rows):
                if row["id"] == schedule_id:
                    rows.pop(index)
                    return
            raise KeyError(schedule_id)

        with self.lock:
            self.settings.update_schedules(remove)
            self.failures.pop(schedule_id, None)
            self.pending.pop(schedule_id, None)

    def get(self, schedule_id):
        row = next((row for row in self.settings.schedules() if row["id"] == schedule_id), None)
        if row is None:
            raise KeyError(schedule_id)
        return row

    def runs(self, schedule_id, cursor=0):
        self.get(schedule_id)
        return self.settings.schedule_runs(schedule_id, cursor)

    def _history(self):
        return self.settings.schedule_history()

    @staticmethod
    def _time(now):
        return now if isinstance(now, datetime) else datetime.fromtimestamp(float(now))

    def _due(self, spec, now, force):
        if force:
            return True
        if spec["paused"]:
            return False
        history = [row for row in self._history() if row.get("schedule") == spec["id"]]
        if not history:
            if "every_minutes" in spec["interval"]:
                return True
            target = spec["interval"]["daily_at"]
            current = self._time(now).strftime("%H:%M")
            return current >= target if spec["catch_up"] else current == target
        last = self._time(history[-1]["at"])
        current = self._time(now)
        if "every_minutes" in spec["interval"]:
            return (current.timestamp() - last.timestamp()) >= spec["interval"]["every_minutes"] * 60
        target = spec["interval"]["daily_at"]
        slot_time = datetime.strptime(target, "%H:%M").time()
        today_slot = datetime.combine(current.date(), slot_time).timestamp()
        if spec["catch_up"]:
            latest_slot = today_slot
            if current.timestamp() < today_slot:
                latest_slot = datetime.combine(current.date() - timedelta(days=1), slot_time).timestamp()
            return last.timestamp() < latest_slot
        return current.strftime("%H:%M") == target and last.date() < current.date()

    def _record(self, row):
        self.settings.append_schedule_run(row)

    def _prior_failures(self, schedule_id):
        count = 0
        for row in reversed(self._history()):
            if row.get("schedule") != schedule_id:
                continue
            if row.get("error"):
                count += 1
            elif not row.get("skipped"):
                break
        return count

    def _failure(self, schedule_id):
        count = self.failures.get(schedule_id, self._prior_failures(schedule_id)) + 1
        self.failures[schedule_id] = count
        if count >= 3:
            with contextlib.suppress(KeyError):
                self.update(
                    schedule_id, {"paused": True, "paused_reason": "paused after three consecutive failed runs"}
                )
            self.pending.pop(schedule_id, None)

    def _finish_active(self, now):
        if self.active is None:
            return False
        spec, path, signature, output, job, started = self.active
        state = job.state() if hasattr(job, "state") else job
        if state["running"]:
            return True
        duration = (
            (state["finished"] - state["started"])
            if state.get("finished") is not None
            else (self._time(now).timestamp() - started)
        )
        record = {
            "schedule": spec["id"],
            "model": spec["model"],
            "at": started,
            "duration": max(0, duration),
            "file": str(path),
            "output": str(output),
            "size": signature[0],
            "mtime": signature[1],
            "rows": 0,
            "errors": 0,
        }
        if state["phase"] in ("done", "done_with_warnings"):
            result = state.get("result") or {}
            record["rows"], record["errors"] = result.get("items", 0), result.get("errors", 0)
            self.failures[spec["id"]] = 0
        else:
            record["error"], record["errors"] = state.get("error") or state["phase"], 1
            self._failure(spec["id"])
        self._record(record)
        self.active = None
        return False

    def run_due(self, now=None, schedule_id=None, force=False):
        now = time.time() if now is None else now
        timestamp = self._time(now).timestamp()
        with self.lock:
            if self._finish_active(now):
                return None
            for spec in self.settings.schedules():
                sid = spec["id"]
                if schedule_id is not None and sid != schedule_id:
                    continue
                if spec["paused"] and not force:
                    self.pending.pop(sid, None)
                    continue
                if sid not in self.pending:
                    if not self._due(spec, now, force):
                        continue
                    try:
                        self.pending[sid] = sorted(Path(spec["input_dir"]).glob(spec["pattern"]), key=lambda p: p.name)
                    except OSError as exc:
                        self._failure(sid)
                        self._record(
                            {
                                "schedule": sid,
                                "model": spec["model"],
                                "at": timestamp,
                                "duration": 0,
                                "file": None,
                                "error": str(exc),
                                "rows": 0,
                                "errors": 1,
                            }
                        )
                        continue
                queue = self.pending[sid]
                while queue:
                    path = queue[0]
                    try:
                        stat = path.stat()
                        if not path.is_file():
                            queue.pop(0)
                            continue
                    except OSError:
                        queue.pop(0)
                        continue
                    signature = stat.st_size, stat.st_mtime
                    if self.settings.schedule_processed(sid).get(str(path)) == list(signature):
                        queue.pop(0)
                        continue
                    output = Path(spec["output_dir"]) / f"{path.stem}.answers.jsonl"
                    record = {
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
                    if output.resolve() == path.resolve():
                        self._failure(sid)
                        self._record({**record, "error": "output must not be the input file", "errors": 1})
                        queue.pop(0)
                        if sid not in self.pending:
                            break
                        continue
                    if not spec["overwrite"] and output.exists():
                        self._record({**record, "skipped": "output exists"})
                        queue.pop(0)
                        continue
                    try:
                        self.fleet.ready(spec["model"])
                    except (KeyError, RuntimeError):
                        self._record({**record, "skipped": "model not loaded"})
                        queue.pop(0)
                        continue
                    try:
                        launched = self.start_batch(spec, path, output)
                    except RuntimeError as exc:
                        if "already running" in str(exc):
                            return None
                        self._failure(sid)
                        self._record({**record, "error": str(exc), "errors": 1})
                        queue.pop(0)
                        if sid not in self.pending:
                            break
                        continue
                    queue.pop(0)
                    job = self.fleet.jobs.get("batch")
                    self.active = (spec, path, signature, output, job or launched, timestamp)
                    return launched
                self.pending.pop(sid, None)
            return None

    def run_now(self, schedule_id, now=None):
        self.get(schedule_id)
        return self.run_due(time.time() if now is None else now, schedule_id, force=True)
