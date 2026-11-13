"""Read-only, bounded inspection of paths entered in the Batch controls."""

import codecs
import contextlib
import errno
import json
import os
import stat
from pathlib import Path

KINDS = ("jsonl", "output", "input_dir", "output_dir")
LINE_CAP = 100_000
COUNT_CAP = 64 * 1024 * 1024
FIRST_LINE_CAP = 1024 * 1024
CHUNK = 1024 * 1024
FIELD_CAP = 30
PREVIEW_CHARS = 300
PREVIEW_FIELDS = ("id", "state", "question", "type", "options", "questions", "gold", "expected")


def _open(path):
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise IsADirectoryError(errno.EISDIR, "not a regular file", str(path))
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def _count(file, lines, pending, budget, decoder):
    while lines < LINE_CAP:
        if budget <= 0:
            return lines + pending, bool(file.read(1))
        chunk = file.read(min(CHUNK, budget))
        if not chunk:
            decoder.decode(b"", final=True)
            return min(lines + pending, LINE_CAP), lines + pending > LINE_CAP
        budget -= len(chunk)
        decoder.decode(chunk)
        *complete, rest = chunk.split(b"\n")
        for part in complete:
            lines += pending or bool(part.strip())
            pending = False
        pending = pending or bool(rest.strip())
    return LINE_CAP, True


def _row_shape(item):
    if "state" not in item:
        return None, "row must contain state"
    questions = item.get("questions")
    if "questions" in item:
        if not isinstance(questions, dict) or not questions:
            return None, "questions must be a non-empty object"
        if any(not isinstance(question, dict) for question in questions.values()):
            return None, "every question must be an object"
        from .compat import validate_systemone

        try:
            validate_systemone(item)
        except ValueError as exc:
            return None, str(exc)
        types = sorted(
            {question.get("type", "?") for question in questions.values() if isinstance(question.get("type", "?"), str)}
        )
        labelled = any("gold" in question for question in questions.values())
        shape = {"format": "request", "questions": len(questions), "types": types[:FIELD_CAP]}
    elif "options" in item:
        question, options = item.get("question"), item["options"]
        if not isinstance(question, str):
            return None, "question must be a string"
        if not isinstance(options, list) or len(options) < 2:
            return None, "options must be a list with at least two items"
        kind = item.get("type", "choice")
        shape = {"format": "simple", "questions": 1, "types": [kind] if isinstance(kind, str) else []}
        labelled = "gold" in item
    else:
        return None, "row must contain questions or options"
    preview = json.dumps({key: item[key] for key in PREVIEW_FIELDS if key in item}, ensure_ascii=False)
    return (
        {
            **shape,
            "fields": list(item)[:FIELD_CAP],
            "labelled": labelled or "gold" in item or "expected" in item,
            "preview": preview[:PREVIEW_CHARS] + ("…" if len(preview) > PREVIEW_CHARS else ""),
        },
        None,
    )


def _read_jsonl(file, result):
    first, budget = None, COUNT_CAP
    while budget > 0:
        line = file.readline(min(FIRST_LINE_CAP + 1, budget))
        if not line:
            break
        budget -= len(line)
        if line.strip():
            first = line
            break
    if first is None:
        result["lines"] = 0
        if budget <= 0:
            result["lines_capped"] = True
            return "scan_limit"
        return "empty"
    decoder = codecs.getincrementaldecoder("utf-8")()
    decoder.decode(first)
    whole = first.endswith(b"\n")
    result["lines"], capped = _count(file, int(whole), not whole, budget, decoder)
    if capped:
        result["lines_capped"] = True
    if len(first) > FIRST_LINE_CAP:
        return "line_too_long"
    try:
        item = json.loads(first.decode("utf-8"))
    except (ValueError, RecursionError):
        return "not_json"
    if not isinstance(item, dict):
        return "not_json"
    shape, error = _row_shape(item)
    if shape is None:
        result["schema_error"] = error
        return "invalid_schema"
    result["first"] = shape
    return None


def _problem(path, kind, result):
    if kind in ("input_dir", "output_dir"):
        if not path.exists():
            return "missing"
        if not path.is_dir():
            return "not_dir"
        mode = os.R_OK | os.X_OK if kind == "input_dir" else os.W_OK | os.X_OK
        if not os.access(path, mode):
            return "unreadable" if kind == "input_dir" else "not_writable"
        return None
    if kind == "output":
        parent_ok = path.parent.is_dir() and os.access(path.parent, os.W_OK)
        exists = path.exists()
        regular = path.is_file() if exists else True
        result["writable"] = parent_ok and regular and not path.is_dir() and (not exists or os.access(path, os.W_OK))
        result["overwrite"] = exists and regular
        if path.is_dir():
            return "is_dir"
        if exists and not regular:
            return "not_file"
        if not path.parent.is_dir():
            return "dir_missing"
        return None if result["writable"] else "not_writable"
    if not path.exists():
        return "missing"
    if not path.is_file():
        return "is_dir" if path.is_dir() else "not_file"
    if not os.access(path, os.R_OK):
        return "unreadable"
    try:
        with _open(path) as file:
            result["size"] = os.fstat(file.fileno()).st_size
            return _read_jsonl(file, result)
    except UnicodeDecodeError:
        return "not_text"
    except IsADirectoryError:
        return "not_file"
    except OSError:
        return "unreadable"


def check(path, kind):
    """Inspect one actual JSONL input or output file path without writing to it."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    if not isinstance(path, str) or not path.strip():
        raise ValueError("path must be a non-empty string")
    unknown = {"path": path.strip(), "kind": kind, "exists": False, "is_file": False, "is_dir": False}
    try:
        resolved = Path(path.strip()).expanduser().resolve()
        with contextlib.suppress(FileNotFoundError, NotADirectoryError):
            os.stat(resolved)
        result = {
            "path": str(resolved),
            "kind": kind,
            "exists": resolved.exists(),
            "is_file": resolved.is_file(),
            "is_dir": resolved.is_dir(),
        }
        code = _problem(resolved, kind, result)
    except PermissionError:
        result, code = unknown, "not_writable" if kind in ("output", "output_dir") else "unreadable"
    except (OSError, RuntimeError):
        result, code = unknown, "invalid"
    return {**result, "ok": code is None, "code": code}
