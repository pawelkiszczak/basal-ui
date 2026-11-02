"""Scheduled Batch runs over changed JSONL files in configured folders."""

import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

MAX_HISTORY_BYTES = 1024 * 1024


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

    def list(self):
        return self.settings.schedules()

    def create(self, body):
        spec = validate(body)
        with self.lock:
            rows = self.settings.schedules()
            if any(row["id"] == spec["id"] for row in rows):
                raise ValueError(f"schedule {spec['id']!r} already exists")
            self.settings.set_schedules([*rows, spec])
        return spec

    def update(self, schedule_id, body):
        old = self.get(schedule_id)
        spec = validate({**old, **body, "id": schedule_id})
        with self.lock:
            rows = self.settings.schedules()
            self.settings.set_schedules([spec if row["id"] == schedule_id else row for row in rows])
        return spec

    def delete(self, schedule_id):
        with self.lock:
            rows = self.settings.schedules()
            if not any(row["id"] == schedule_id for row in rows):
                raise KeyError(schedule_id)
            self.settings.set_schedules([row for row in rows if row["id"] != schedule_id])
            self.failures.pop(schedule_id, None)

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
        today_slot = datetime.combine(current.date(), datetime.strptime(target, "%H:%M").time()).timestamp()
        if spec["catch_up"]:
            return current.timestamp() >= today_slot and last.date() < current.date()
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

    def _finish_active(self):
        if self.active is None:
            return False
        schedule_id, path, signature, output, job, started = self.active
        state = job.state() if hasattr(job, "state") else job
        if state["running"]:
            return True
        record = {"schedule": schedule_id, "at": started, "file": str(path), "output": str(output)}
        if state["phase"] in ("done", "done_with_warnings"):
            record["rows"] = (state.get("result") or {}).get("items", 0)
            self.failures[schedule_id] = 0
        else:
            record["error"] = state.get("error") or state["phase"]
            count = self.failures.get(schedule_id, self._prior_failures(schedule_id)) + 1
            self.failures[schedule_id] = count
            if count >= 3:
                specs = self.settings.schedules()
                for spec in specs:
                    if spec["id"] == schedule_id:
                        spec["paused"], spec["paused_reason"] = True, "paused after three consecutive failed runs"
                self.settings.set_schedules(specs)
        self._record({**record, "size": signature[0], "mtime": signature[1]})
        self.active = None
        return False

    def run_due(self, now=None, schedule_id=None, force=False):
        now = time.time() if now is None else now
        with self.lock:
            if self._finish_active():
                return None
            for spec in self.settings.schedules():
                if schedule_id is not None and spec["id"] != schedule_id:
                    continue
                if (spec["paused"] and not force) or not self._due(spec, now, force):
                    continue
                try:
                    files = sorted(Path(spec["input_dir"]).glob(spec["pattern"]), key=lambda p: p.name)
                except OSError as exc:
                    self._record(
                        {"schedule": spec["id"], "at": self._time(now).timestamp(), "file": None, "error": str(exc)}
                    )
                    continue
                processed = {
                    (row.get("file"), row.get("size"), row.get("mtime"))
                    for row in self._history()
                    if row.get("schedule") == spec["id"]
                }
                for path in files:
                    try:
                        stat = path.stat()
                        if not path.is_file():
                            continue
                    except OSError:
                        continue
                    signature = stat.st_size, stat.st_mtime
                    if (str(path), *signature) in processed:
                        continue
                    output = Path(spec["output_dir"]) / f"{path.stem}.answers.jsonl"
                    if not spec["overwrite"] and output.exists():
                        self._record(
                            {
                                "schedule": spec["id"],
                                "at": self._time(now).timestamp(),
                                "file": str(path),
                                "skipped": "output exists",
                                "size": signature[0],
                                "mtime": signature[1],
                            }
                        )
                        continue
                    try:
                        self.fleet.ready(spec["model"])
                    except (KeyError, RuntimeError):
                        self._record(
                            {
                                "schedule": spec["id"],
                                "at": self._time(now).timestamp(),
                                "file": str(path),
                                "skipped": "model not loaded",
                                "size": signature[0],
                                "mtime": signature[1],
                            }
                        )
                        continue
                    try:
                        launched = self.start_batch(spec, path, output)
                    except RuntimeError as exc:
                        if "already running" in str(exc):
                            return None
                        self._record(
                            {
                                "schedule": spec["id"],
                                "at": self._time(now).timestamp(),
                                "file": str(path),
                                "error": str(exc),
                                "size": signature[0],
                                "mtime": signature[1],
                            }
                        )
                        count = self.failures.get(spec["id"], self._prior_failures(spec["id"])) + 1
                        self.failures[spec["id"]] = count
                        if count >= 3:
                            self.update(
                                spec["id"],
                                {"paused": True, "paused_reason": "paused after three consecutive failed runs"},
                            )
                        continue
                    job = self.fleet.jobs.get("batch")
                    self.active = (spec["id"], path, signature, output, job or launched, self._time(now).timestamp())
                    return launched
            return None

    def run_now(self, schedule_id, now=None):
        self.get(schedule_id)
        return self.run_due(time.time() if now is None else now, schedule_id, force=True)
