"""What a path typed into the UI points at, before a job uses it (POST /api/paths/check): browsers do not reveal the
paths of picked or dropped files, so the Batch, Calibration and decision-log fields name files on the server machine.
Only a JSON file's shape is shown: its first row's field names and, for a row batch or calibration can read, a short
preview of that row's request fields. Nothing of any other file's content is returned."""

import codecs
import contextlib
import errno
import json
import os
import stat
from pathlib import Path

KINDS = ("jsonl", "json", "output", "append", "dir")  # a JSONL input, a JSON file (a calibration to import), a file
# to write (replaced) or to append to (the decision log), a directory to keep files in (created if missing: models)
LINE_CAP = 100_000  # lines counted at most; a bigger file reports lines_capped
COUNT_CAP = 64 * 1024 * 1024  # bytes of a JSONL file read to count its lines at most; then lines_capped too
FIRST_LINE_CAP = 4 * 1024 * 1024  # a longer first row is not parsed: line_too_long
JSON_CAP = 32 * 1024 * 1024  # a JSON file larger than this is not parsed
CHUNK = 1024 * 1024
PREVIEW_CHARS = 300
FIELD_CAP = 30
PREVIEW_FIELDS = ("id", "state", "question", "type", "options", "questions", "gold", "expected")  # a request's own


def _row_shape(item):
    """The first row of a JSONL input: its fields, the format batch and calibration read, and whether it has gold;
    None for any other object."""
    questions = item.get("questions")
    if isinstance(questions, dict):
        types = [q.get("type", "?") for q in questions.values() if isinstance(q, dict)]
        types = sorted({t for t in types if isinstance(t, str)})  # a malformed type is left out, never an error
        labelled = any(isinstance(q, dict) and "gold" in q for q in questions.values())
        shape = {"format": "request", "questions": len(questions), "types": types[:FIELD_CAP]}
    elif "options" in item:
        kind = item.get("type", "choice")
        shape = {"format": "simple", "questions": 1, "types": [kind] if isinstance(kind, str) else []}
        labelled = "gold" in item
    else:
        return None
    preview = json.dumps({k: item[k] for k in PREVIEW_FIELDS if k in item}, ensure_ascii=False)
    return {
        **shape,
        "fields": list(item)[:FIELD_CAP],
        "labelled": labelled or "gold" in item or "expected" in item,
        "preview": preview[:PREVIEW_CHARS] + ("…" if len(preview) > PREVIEW_CHARS else ""),
    }


def _open(p):
    """A binary file object of p, refused (not_file) unless it is a regular file: a FIFO or a device is never read and
    opening one does not block (O_NONBLOCK), even if p was swapped for one after the is_file() check."""
    fd = os.open(p, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise IsADirectoryError(errno.EISDIR, "not a regular file", str(p))
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def _count(f, lines, pending, budget, utf8):
    """lines plus the non-blank lines left in f (pending: the line read so far is non-blank and not yet counted), read
    in binary chunks; (lines, capped) where capped means more than LINE_CAP lines or COUNT_CAP bytes. The bytes read
    must be UTF-8, checked by the incremental decoder utf8 (else UnicodeDecodeError: not_text)."""
    while lines < LINE_CAP:
        if budget <= 0:
            return lines + pending, bool(f.read(1))
        chunk = f.read(min(CHUNK, budget))
        if not chunk:
            utf8.decode(b"", final=True)
            return min(lines + pending, LINE_CAP), lines + pending > LINE_CAP
        budget -= len(chunk)
        utf8.decode(chunk)
        *done, rest = chunk.split(b"\n")
        for part in done:
            lines += pending or bool(part.strip())
            pending = False
        pending = pending or bool(rest.strip())
    return LINE_CAP, True


def _parse(data):
    """A JSON object from UTF-8 bytes (UnicodeDecodeError: not_text, to the caller), or None."""
    text = data.decode("utf-8")
    try:
        value = json.loads(text)
    except (ValueError, RecursionError):  # RecursionError: nesting deeper than the parser goes
        return None
    return value if isinstance(value, dict) else None


def _read_jsonl(f, out):
    first, budget = None, COUNT_CAP
    while budget > 0:
        line = f.readline(min(FIRST_LINE_CAP + 1, budget))
        if not line:
            break
        budget -= len(line)
        if line.strip():
            first = line
            break
    if first is None:
        out["lines"] = 0
        return "empty"
    utf8 = codecs.getincrementaldecoder("utf-8")()
    utf8.decode(first)  # incremental: a first line cut by the cap may end inside a character
    whole = first.endswith(b"\n")  # else the rest of the first line is still to come, and counted with it
    out["lines"], capped = _count(f, int(whole), not whole, budget, utf8)
    if capped:
        out["lines_capped"] = True
    if len(first) > FIRST_LINE_CAP:
        return "line_too_long"
    item = _parse(first)
    if item is None:
        return "not_json"
    shape = _row_shape(item)  # None: not a row batch or calibration reads, and nothing of it is returned
    if shape is None:
        return "unknown_format"
    out["first"] = shape
    return None


def _read_json(f, out):
    data = f.read(JSON_CAP + 1)  # bounded whatever st_size says (0 for a procfs or sysfs file)
    if len(data) > JSON_CAP:
        return "too_large"
    item = _parse(data)
    if item is None:
        return "not_json"
    out["fields"] = list(item)[:FIELD_CAP]
    return None


def _problem(p, kind, out):
    """The problem code of a resolved path (None: none); fills out with what the kind reports."""
    if kind == "dir":  # settings.set_models_dir creates a missing directory
        if p.exists():
            code = None if p.is_dir() and os.access(p, os.W_OK) else "not_writable" if p.is_dir() else "not_dir"
        else:
            nearest = next((a for a in p.parents if a.exists()), None)
            code = None if nearest is not None and nearest.is_dir() and os.access(nearest, os.W_OK) else "not_writable"
        out["writable"] = code is None
    elif kind in ("output", "append"):
        parent_ok = p.parent.is_dir() and os.access(p.parent, os.W_OK)
        out["writable"] = parent_ok and not p.is_dir() and (not p.exists() or os.access(p, os.W_OK))
        code = None
        if p.is_dir():
            code = "is_dir"
        elif not p.parent.is_dir():
            code = "dir_missing"
        elif not out["writable"]:
            code = "not_writable"
    elif not p.exists():
        code = "missing"
    elif not p.is_file():
        code = "is_dir" if p.is_dir() else "not_file"
    elif not os.access(p, os.R_OK):
        code = "unreadable"
    else:
        try:
            with _open(p) as f:
                out["size"] = os.fstat(f.fileno()).st_size
                code = _read_jsonl(f, out) if kind == "jsonl" else _read_json(f, out)
        except UnicodeDecodeError:
            code = "not_text"
        except IsADirectoryError:
            code = "not_file"
        except OSError:
            code = "unreadable"
    return code


def check(path, kind):
    """{path (resolved), kind, exists, is_file, is_dir, ok, code} plus, by kind: size / lines / first (jsonl), size /
    fields (json), writable (output, append, dir). code names the problem (None: none), for the UI to explain."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    if not isinstance(path, str) or not path.strip():
        raise ValueError("path must be a non-empty string")
    unknown = {"path": path.strip(), "kind": kind, "exists": False, "is_file": False, "is_dir": False}
    try:
        p = Path(path.strip()).expanduser().resolve()
        with contextlib.suppress(FileNotFoundError, NotADirectoryError):  # pathlib's exists() swallows
            os.stat(p)  # ENAMETOOLONG and ELOOP from Python 3.14: ask the OS, which raises them (invalid)
        out = {"path": str(p), "kind": kind, "exists": p.exists(), "is_file": p.is_file(), "is_dir": p.is_dir()}
        code = _problem(p, kind, out)
    except PermissionError:  # a directory on the way that basal-ui cannot search
        out, code = unknown, "not_writable" if kind in ("output", "append", "dir") else "unreadable"
    except (OSError, RuntimeError):  # a name too long, a symlink loop: not a path the job could use
        out, code = unknown, "invalid"
    return {**out, "ok": code is None, "code": code}
