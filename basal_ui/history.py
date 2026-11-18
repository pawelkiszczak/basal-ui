"""The decisions basal-ui answered (playground, API, MCP) with their requests and responses, newest kept: a JSONL file
<settings stem>.history.jsonl next to the settings file (0600; media bytes are replaced by their size)."""

import contextlib
import json
import os
import re
import threading
import time
import uuid
from collections import deque
from pathlib import Path

from . import history_audio
from .stats import answer_items, best_probability

PREVIEW = 160


def _strip_media(value, context=None, root=False):
    """Replace media payload strings with their character count, without changing ordinary request data."""
    if isinstance(value, dict):
        kind = value.get("type")
        native = context in ("native", "alias")
        decision = context == "decision" and kind in ("input_image", "input_audio")
        out = {}
        for key, item in value.items():
            media_value = isinstance(item, str) and (
                (native and key == "data")
                or (context == "payload" and key in ("data", "url"))
                or (decision and key in ("data", "image_url", "audio_url", "content"))
            )
            if media_value:
                out[key] = f"<{len(item)} bytes>"
            elif root and key == "media":
                out[key] = _strip_media(item, "native")
            elif root and key in ("images", "audio"):
                out[key] = _strip_media(item, "alias")
            elif root and key == "input":
                out[key] = _strip_media(item, "decision")
            elif decision and key in ("input_audio", "image_url", "audio_url"):
                out[key] = _strip_media(item, "payload")
            else:
                out[key] = _strip_media(item, "decision" if context == "decision" else None)
        return out
    if isinstance(value, list):
        return [_strip_media(item, context) for item in value]
    if context == "alias" and isinstance(value, str):
        return f"<{len(value)} bytes>"
    return value


def _contains_redacted_media(value, context=None, root=False):
    if isinstance(value, dict):
        kind = value.get("type")
        native = context in ("native", "alias")
        decision = context == "decision" and kind in ("input_image", "input_audio")
        for key, item in value.items():
            relevant = (
                (native and key == "data")
                or (context == "payload" and key in ("data", "url"))
                or (decision and key in ("data", "image_url", "audio_url", "content"))
            )
            if relevant and isinstance(item, str) and re.fullmatch(r"<\d+ bytes>", item):
                return True
            child_context = None
            if root and key == "media":
                child_context = "native"
            elif root and key in ("images", "audio"):
                child_context = "alias"
            elif root and key == "input":
                child_context = "decision"
            elif decision and key in ("input_audio", "image_url", "audio_url"):
                child_context = "payload"
            next_context = child_context if child_context is not None else "decision" if context == "decision" else None
            if _contains_redacted_media(item, next_context):
                return True
        return False
    if isinstance(value, list):
        return any(_contains_redacted_media(item, context) for item in value)
    return context == "alias" and isinstance(value, str) and re.fullmatch(r"<\d+ bytes>", value) is not None


def _best(answer: dict):
    for key in ("choice", "action", "tool", "label", "score"):
        if key in answer and answer[key] is not None:
            return str(answer[key])
    for key in ("selected", "labels", "tools"):  # multi, multi-label classify, multi tool
        if isinstance(answer.get(key), list):
            return ", ".join(map(str, answer[key]))
    for key in ("noul", "probability"):  # systemone noul, decisions-API predicate: the probability of yes
        if isinstance(answer.get(key), (int, float)):
            return "true" if answer[key] >= 0.5 else "false"
    return None


def _preview(body: dict) -> str:
    state = body.get("state", body.get("input", ""))
    text = state if isinstance(state, str) else json.dumps(state, ensure_ascii=False)
    return text[:PREVIEW]


def make_record(model, source, body, status, response_json, latency_ms) -> dict:
    answers = response_json.get("answers") if isinstance(response_json, dict) else None
    usage = response_json.get("usage") if isinstance(response_json, dict) else None
    questions = body.get("questions")
    items = answer_items(answers)
    return {
        "id": "h_" + uuid.uuid4().hex,
        "ts": time.time(),
        "model": model,
        "source": source,
        "status": status,
        "latency_ms": round(latency_ms, 2),
        "input_tokens": usage.get("input_tokens") if isinstance(usage, dict) else None,
        "questions": len(questions) if isinstance(questions, (dict, list)) else None,
        "state_preview": _preview(_strip_media(body, root=True)),
        "answers": {
            name: {"type": a.get("type"), "best": _best(a), "confidence": best_probability(a)} for name, a in items
        },
        "deferred": any(a.get("deferred") is True for _, a in items),
        "truncated": bool(usage.get("truncated")) if isinstance(usage, dict) else False,
        "request": _strip_media(body, root=True),
        "response": response_json,
    }


def _summary(record: dict) -> dict:
    return {k: v for k, v in record.items() if k not in ("request", "response", "_audio", "_audio_expired")}


def replay_fields(request: dict) -> dict:
    """Build a Playground request from a recorded native or Decisions API request."""
    from .compat import decision_body, systemone_media

    if not isinstance(request, dict):
        return {
            "replay_request": None,
            "replay_error": "This recorded request is not a JSON object and cannot be replayed.",
        }
    if _contains_redacted_media(request, root=True):
        return {
            "replay_request": None,
            "replay_error": "History removed the original media attachments. Add them again before sending this request.",
        }
    try:
        if "input" in request:
            replay, _, _ = decision_body(request)
            if request.get("model") is not None:
                replay["model"] = request["model"]
        else:
            replay = systemone_media(request) if "audio" in request or "images" in request else request
        return {"replay_request": replay, "replay_error": None}
    except (TypeError, ValueError) as exc:
        detail = exc.args[0] if exc.args else exc
        message = detail[1] if isinstance(detail, tuple) and len(detail) > 1 else str(detail)
        return {"replay_request": None, "replay_error": f"This recorded request cannot be replayed: {message}"}


class History:
    """Appends go to the file under the settings lock; a rotation rewrites the file from its own last `keep` lines,
    so a second basal-ui process on the same settings file loses nothing (its records are in the file, not in this
    process's memory, which only shows what this process recorded or loaded at startup)."""

    def __init__(self, path: Path, keep: int = 2000):
        self.path = Path(path)
        self.keep = keep
        self.lock = threading.Lock()
        self.records: deque = deque(maxlen=keep)
        self.lines = 0
        self._stamp = None
        for record in self._read_file():
            self.records.append(record)

    def _file_stamp(self):
        try:
            info = self.path.stat()
        except FileNotFoundError:
            return ()
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns

    def _read_file(self, strict=False) -> list:
        """Read valid rows, failing closed on corrupt metadata when audio ownership is involved."""
        out = []
        seen = set()
        lines = 0
        protect_audio = strict and history_audio.storage_present(self.path)
        try:
            with self.path.open("rb") as stream:
                for line in stream:
                    lines += 1
                    try:
                        record = json.loads(line)
                    except ValueError as exc:
                        if protect_audio:
                            raise OSError("History metadata is invalid") from exc
                        continue
                    if not isinstance(record, dict) or not isinstance(record.get("id"), str) or not record["id"]:
                        if protect_audio:
                            raise OSError("History metadata is invalid")
                        continue
                    if strict and (
                        record["id"] in seen
                        or ("_audio" in record and not history_audio.valid_ref(record, record["_audio"]))
                    ):
                        if protect_audio:
                            raise OSError("History metadata is invalid")
                        continue
                    seen.add(record["id"])
                    out.append(record)
        except FileNotFoundError:
            pass
        except OSError:
            if strict:
                raise
        self.lines = lines
        return out

    def append(self, record: dict, body=None, keep_audio=False) -> None:
        from . import settings

        def write():
            capture_audio = (
                body is not None
                and (keep_audio() if callable(keep_audio) else keep_audio)
                and any(history_audio.audio_paths(body))
            )
            if not capture_audio and not history_audio._pending(self.path):
                self._refresh_locked()
                if self.lines < self.keep + 500:
                    self._append_line(record)
                    self.records.append(record)
                    self._stamp = self._file_stamp()
                    return
            existing = self._read_file(strict=True)
            history_audio.cleanup(self.path, existing)
            reservation = None
            committed = False
            try:
                if capture_audio:
                    reservation = history_audio.reserve(self.path, record, body, existing)
                evicted = reservation[2] if reservation else []
                candidate = []
                evicted_ids = {r["id"] for r in evicted}
                for old in existing:
                    if old["id"] in evicted_ids:
                        old = {k: v for k, v in old.items() if k != "_audio"}
                        old["_audio_expired"] = True
                    candidate.append(old)
                self._append_record(record, candidate, existing, evicted, bool(reservation))
                committed = True
                self._stamp = None
                self.records.append(record)
                history_audio.cleanup(self.path, self._read_file(strict=True))
                if reservation:
                    history_audio.fill(self.path, record, reservation)
            finally:
                if reservation:
                    history_audio.close_reservation(self.path, record, reservation, committed)

        with self.lock:
            settings._locked(write)()

    def _append_record(self, record, candidate, existing, evicted, atomic):
        from . import settings

        records = [*candidate, record]
        expired = []
        if self.lines + 1 > self.keep + 500:
            expired = existing[: max(0, len(existing) + 1 - self.keep)]
            records = records[-self.keep :]
        history_audio.journal_add(self.path, [*evicted, *expired])
        if atomic or expired:
            settings._write_private(self.path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records))
            self.lines = len(records)
        else:
            self._append_line(record)

    def _append_line(self, record):
        line = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a+b") as stream:
            if stream.seek(0, os.SEEK_END):
                stream.seek(-1, os.SEEK_END)
                if stream.read(1) != b"\n":
                    line = b"\n" + line
            stream.write(line)
        self.lines += 1

    def detail(self, record_id, replay=False):
        from . import settings

        def read():
            record = next((r for r in self._read_file() if r["id"] == record_id), None)
            if record is None:
                return None
            public = {k: v for k, v in record.items() if k != "_audio"}
            request = record.get("request", {})
            if record.get("_audio_expired"):
                public.pop("_audio_expired", None)
                return {
                    **public,
                    "replay_request": None,
                    "replay_error": "History audio is unavailable or expired.",
                    "replay_error_code": "history_audio_unavailable",
                }
            if not record.get("_audio"):
                return {**public, **replay_fields(request)}
            # Mask audio placeholders to check for independently missing images.
            masked = json.loads(json.dumps(request))
            for path, _ in history_audio.audio_paths(masked):
                target = masked
                for part in path[:-1]:
                    target = target[part]
                target[path[-1]] = "audio retained"
            if _contains_redacted_media(masked, root=True):
                return {**public, **replay_fields(request)}
            try:
                fd = history_audio._directory(self.path)
                try:
                    available = history_audio.available(fd, record)
                    restored = history_audio.restore(fd, record) if replay and available else None
                finally:
                    os.close(fd)
            except OSError:
                available, restored = False, None
            if available and not replay:
                return {**public, "replay_request": None, "replay_error": None, "replay_requires_audio": True}
            if restored is None:
                return {
                    **public,
                    "replay_request": None,
                    "replay_error": "History audio is unavailable or expired.",
                    "replay_error_code": "history_audio_unavailable",
                }
            return {**public, **replay_fields(restored)}

        with self.lock:
            return settings._locked(read)()

    def _refresh_locked(self):
        stamp = self._file_stamp()
        if stamp != self._stamp:
            self.records = deque(self._read_file(strict=True), maxlen=self.keep)
            self._stamp = stamp

    def _refresh(self):
        from . import settings

        with contextlib.suppress(OSError):
            settings._locked(self._refresh_locked)()

    def list(self, model=None, source=None, q=None, limit=100, before=None, after=None, order="desc") -> list[dict]:
        with self.lock:
            self._refresh()
            records = list(self.records)
        ids = [r["id"] for r in records]
        if before is not None:
            records = records[: ids.index(before)] if before in ids else []
        if after is not None:
            records = records[ids.index(after) + 1 :] if after in ids else records
        needle = q.lower() if q else None
        out = []
        for record in records if order == "asc" else reversed(records):
            if model and record.get("model") != model or source and record.get("source") != source:
                continue
            if (
                needle
                and needle not in (str(record.get("state_preview", "")) + " " + str(record.get("model", ""))).lower()
            ):
                continue
            out.append(_summary(record))
            if len(out) >= limit:
                break
        return out

    def get(self, record_id: str) -> dict | None:
        with self.lock:
            self._refresh()
            return next((r for r in self.records if r["id"] == record_id), None)

    def models(self) -> "list[str]":  # `list` here is the method above
        with self.lock:
            self._refresh()
            return sorted({r["model"] for r in self.records if isinstance(r.get("model"), str)})

    def clear(self) -> int:
        from . import settings

        def clear_disk():
            records = self._read_file(strict=True)
            history_audio.cleanup(self.path, records)
            history_audio.journal_add(self.path, records)
            settings._write_private(self.path, "")
            self.records.clear()
            self.lines = 0
            self._stamp = None
            history_audio.cleanup(self.path, [])
            return min(len(records), self.keep)

        with self.lock:
            n = settings._locked(clear_disk)()
            self.records.clear()
            self.lines = 0
            return n
