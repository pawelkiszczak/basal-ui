"""Private, bounded audio blobs owned by History. Call only under the settings lock."""

import contextlib
import hashlib
import json
import os
import re
import stat
import uuid

BUDGET = 256 * 1024 * 1024
_NAME = re.compile(r"h_[0-9a-f]{32}_[0-9a-f]{32}\Z")


def audio_paths(value, context=None, path=()):
    """Find audio payloads using the same media contexts as History redaction."""
    if isinstance(value, dict):
        kind = value.get("type")
        for key, item in value.items():
            child = (*path, key)
            if isinstance(item, str) and (
                (context in ("audio_native", "audio_alias") and key == "data")
                or (context == "audio_payload" and key in ("data", "url"))
                or (context == "decision" and kind == "input_audio" and key in ("data", "audio_url", "content"))
            ):
                yield child, item
            else:
                next_context = None
                if not path and key == "media":
                    next_context = "media"
                elif not path and key == "audio":
                    next_context = "audio_alias"
                elif not path and key == "input":
                    next_context = "decision"
                elif context == "media" and kind == "audio":
                    next_context = "audio_native"
                elif context == "decision" and kind == "input_audio" and key in ("input_audio", "audio_url"):
                    next_context = "audio_payload"
                elif context in ("decision", "audio_payload"):
                    next_context = context
                yield from audio_paths(item, next_context, child)
    elif isinstance(value, list):
        for i, item in enumerate(value):
            if context == "audio_alias" and isinstance(item, str):
                yield (*path, i), item
            else:
                next_context = (
                    "audio_native"
                    if context == "media" and isinstance(item, dict) and item.get("type") == "audio"
                    else context
                )
                yield from audio_paths(item, next_context, (*path, i))


def _directory(path):
    directory = path.with_name(path.name + ".audio")
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    if not stat.S_ISDIR(os.fstat(fd).st_mode) or os.fstat(fd).st_mode & 0o077:
        os.close(fd)
        raise OSError("History audio directory is not private")
    return fd


def valid_ref(record, ref):
    return (
        isinstance(ref, dict)
        and isinstance(ref.get("name"), str)
        and _NAME.fullmatch(ref["name"])
        and ref["name"].startswith(str(record.get("id")) + "_")
        and isinstance(ref.get("size"), int)
        and 0 <= ref["size"] <= BUDGET
        and isinstance(ref.get("sha256"), str)
        and re.fullmatch(r"[0-9a-f]{64}", ref["sha256"])
    )


def _matches(fd, ref):
    if not isinstance(ref.get("dev"), int) or not isinstance(ref.get("ino"), int):
        return False
    try:
        info = os.stat(ref["name"], dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    return (
        stat.S_ISREG(info.st_mode)
        and info.st_nlink == 1
        and info.st_uid == os.getuid()
        and (info.st_dev, info.st_ino) == (ref["dev"], ref["ino"])
    )


def remove(fd, record):
    ref = record.get("_audio")
    if valid_ref(record, ref) and _matches(fd, ref):
        with contextlib.suppress(FileNotFoundError):
            os.unlink(ref["name"], dir_fd=fd)


def usage(fd):
    total = 0
    for name in os.listdir(fd):
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if stat.S_ISREG(info.st_mode):
            total += info.st_size
    return total


def reserve(path, record, body, records):
    values = [value for _, value in audio_paths(body)]
    data = json.dumps(values, ensure_ascii=False).encode("utf-8")
    if len(data) > BUDGET:
        raise OSError("History audio exceeds the storage budget")
    fd = _directory(path)
    blob = None
    try:
        used = usage(fd)
        evicted = []
        for old in records:
            if used + len(data) <= BUDGET:
                break
            ref = old.get("_audio")
            if valid_ref(old, ref) and _matches(fd, ref):
                used -= os.stat(ref["name"], dir_fd=fd, follow_symlinks=False).st_size
                evicted.append(old)
        if used + len(data) > BUDGET:
            raise OSError("History audio storage budget is full")
        name = record["id"] + "_" + uuid.uuid4().hex
        blob = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=fd)
        info = os.fstat(blob)
        record["_audio"] = {
            "name": name,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "dev": info.st_dev,
            "ino": info.st_ino,
        }
        journal_add(path, [record])
        return fd, blob, evicted, data
    except BaseException:
        if blob is not None:
            os.close(blob)
            remove(fd, record)
            record.pop("_audio", None)
        os.close(fd)
        raise


def fill(path, record, reservation):
    fd, blob, _, data = reservation
    ref = record["_audio"]
    if not _matches(fd, ref) or usage(fd) + len(data) > BUDGET:
        raise OSError("History audio storage is unavailable")
    with os.fdopen(os.dup(blob), "wb") as stream:
        stream.write(data)


def close_reservation(path, record, reservation, committed):
    fd, blob, _, _ = reservation
    try:
        if not committed:
            remove(fd, record)
            record.pop("_audio", None)
    finally:
        os.close(blob)
        os.close(fd)


def _journal(path):
    return path.with_name(path.name + ".audio-pending.json")


def _pending(path):
    try:
        value = json.loads(_journal(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except (ValueError, UnicodeError) as exc:
        raise OSError("Invalid History audio cleanup journal") from exc
    if not isinstance(value, list) or any(not isinstance(r, dict) or not valid_ref(r, r.get("_audio")) for r in value):
        raise OSError("Invalid History audio cleanup journal")
    return value


def journal_add(path, records):
    from . import settings

    additions = [{"id": r["id"], "_audio": r["_audio"]} for r in records if valid_ref(r, r.get("_audio"))]
    if additions:
        settings._write_private(_journal(path), json.dumps(_pending(path) + additions))


def cleanup(path, records):
    from . import settings

    pending = _pending(path)
    if not pending:
        return
    live = {r.get("_audio", {}).get("name") for r in records if valid_ref(r, r.get("_audio"))}
    fd = _directory(path)
    try:
        for record in pending:
            if record["_audio"]["name"] not in live:
                remove(fd, record)
    finally:
        os.close(fd)
    settings._write_private(_journal(path), "[]")


def available(fd, record):
    ref = record.get("_audio")
    if not valid_ref(record, ref):
        return False
    try:
        info = os.stat(ref["name"], dir_fd=fd, follow_symlinks=False)
        return _matches(fd, ref) and info.st_size == ref["size"]
    except OSError:
        return False


def restore(fd, record):
    ref = record.get("_audio")
    if not valid_ref(record, ref):
        return None
    try:
        blob = os.open(ref["name"], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=fd)
        with os.fdopen(blob, "rb") as f:
            info = os.fstat(f.fileno())
            if (
                not _matches(fd, ref)
                or (info.st_dev, info.st_ino) != (ref.get("dev"), ref.get("ino"))
                or info.st_nlink != 1
                or info.st_uid != os.getuid()
                or info.st_size != ref["size"]
            ):
                return None
            data = f.read(ref["size"] + 1)
        if hashlib.sha256(data).hexdigest() != ref["sha256"]:
            return None
        values = json.loads(data)
        paths = list(audio_paths(record.get("request", {})))
        if (
            not paths
            or not isinstance(values, list)
            or len(values) != len(paths)
            or not all(isinstance(v, str) for v in values)
        ):
            return None
        request = json.loads(json.dumps(record["request"]))
        for (path, placeholder), value in zip(paths, values, strict=True):
            if placeholder != f"<{len(value)} bytes>":
                return None
            target = request
            for part in path[:-1]:
                target = target[part]
            target[path[-1]] = value
        return request
    except (OSError, ValueError, TypeError, KeyError):
        return None
