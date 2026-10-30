"""What a path typed into the UI points at, before a job uses it (POST /api/paths/check): browsers do not reveal the
paths of picked or dropped files, so the Batch, Calibration and decision-log fields name files on the server machine.
Only a JSON file's shape is shown: its first row's field names and, for a row batch or calibration can read, a short
preview of that row's request fields. Nothing of any other file's content is returned."""

import json
import os
from pathlib import Path

KINDS = ("jsonl", "json", "output", "append", "dir")  # a JSONL input, a JSON file (a calibration to import), a file
# to write (replaced) or to append to (the decision log), a directory to keep files in (created if missing: models)
LINE_CAP = 100_000  # lines counted at most; a bigger file reports lines_capped
JSON_CAP = 32 * 1024 * 1024  # a JSON file larger than this is not parsed
PREVIEW_CHARS = 300
FIELD_CAP = 30
PREVIEW_FIELDS = ("id", "state", "question", "type", "options", "questions", "gold", "expected")  # a request's own


def _row_shape(item):
    """The first row of a JSONL input: its fields, the format batch and calibration read, and whether it has gold;
    None for any other object."""
    questions = item.get("questions")
    if isinstance(questions, dict):
        types = sorted({q.get("type", "?") for q in questions.values() if isinstance(q, dict)})
        labelled = any(isinstance(q, dict) and "gold" in q for q in questions.values())
        shape = {"format": "request", "questions": len(questions), "types": types[:FIELD_CAP]}
    elif "options" in item:
        shape = {"format": "simple", "questions": 1, "types": [item.get("type", "choice")]}
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


def _read_jsonl(path, out):
    lines, first = 0, None
    with open(path, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            if first is None:
                first = line
            lines += 1
            if lines >= LINE_CAP:
                out["lines_capped"] = True
                break
    out["lines"] = lines
    if first is None:
        return "empty"
    try:
        item = json.loads(first)
    except ValueError:
        return "not_json"
    if not isinstance(item, dict):
        return "not_json"
    shape = _row_shape(item)  # None: not a row batch or calibration reads, and nothing of it is returned
    if shape is None:
        return "unknown_format"
    out["first"] = shape
    return None


def _read_json(path, out):
    if out["size"] > JSON_CAP:
        return "too_large"
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except ValueError:
        return "not_json"
    if not isinstance(data, dict):
        return "not_json"
    out["fields"] = list(data)[:FIELD_CAP]
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
        out["size"] = p.stat().st_size
        try:
            code = _read_jsonl(p, out) if kind == "jsonl" else _read_json(p, out)
        except UnicodeDecodeError:
            code = "not_text"
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
        out = {"path": str(p), "kind": kind, "exists": p.exists(), "is_file": p.is_file(), "is_dir": p.is_dir()}
        code = _problem(p, kind, out)
    except PermissionError:  # a directory on the way that basal-ui cannot search
        out, code = unknown, "not_writable" if kind in ("output", "append", "dir") else "unreadable"
    except (OSError, RuntimeError):  # a name too long, a symlink loop: not a path the job could use
        out, code = unknown, "invalid"
    return {**out, "ok": code is None, "code": code}
