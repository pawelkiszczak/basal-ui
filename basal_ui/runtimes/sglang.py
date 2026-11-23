"""Read-only observations of a local SGLang Runtime server."""

import os
import shlex
import subprocess
import time

import httpx

from . import _probe

_PROBE_DEADLINE = 1.5
_PROCESS_QUERY = ["ps", "-eo", "pid=,ppid=,comm=,args="]


def _root_pid() -> int | None:
    """Find an SGLang launcher or worker using executable names, not command substrings."""
    try:
        output = subprocess.run(_PROCESS_QUERY, capture_output=True, text=True, timeout=5, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    launchers = []
    workers = []
    for line in output.splitlines():
        fields = line.split(None, 3)
        if len(fields) != 4 or not fields[0].isdigit():
            continue
        pid, _, command, arguments = fields
        try:
            argv = shlex.split(arguments)
        except ValueError:
            continue
        executable = os.path.basename(argv[0]) if argv else os.path.basename(command)
        title = os.path.basename(command)
        module_launcher = any(
            argv[index] == "-m" and argv[index + 1] == "sglang.launch_server" for index in range(len(argv) - 1)
        )
        if (
            executable == "sglang.launch_server"
            or module_launcher
            or (executable == "sglang" and len(argv) > 1 and argv[1] == "serve")
        ):
            launchers.append(int(pid))
        elif executable in ("sglang::scheduler", "sglang::detokenizer") or title in (
            "sglang::scheduler",
            "sglang::detokenizer",
        ):
            workers.append(int(pid))
    return (launchers or workers or [None])[0]


def _process_bytes(excluded_pids: set[int] | frozenset[int]) -> int | None:
    root_pid = _root_pid()
    return _probe.process_tree_rss(root_pid, excluded_pids) if root_pid is not None else None


def observe(excluded_pids: set[int] | frozenset[int] = frozenset()) -> dict | None:
    """Report a confirmed SGLang model and process RSS, or None when absent."""
    from .. import memory

    started = time.monotonic()
    origin = None
    note = None
    try:
        origin = _probe.loopback_origin(os.environ.get("BASAL_UI_SGLANG_URL", "http://127.0.0.1:30000"))
    except ValueError:
        note = "SGLang URL is invalid; process RSS is still measured."

    info = None
    status = "unavailable"
    if origin is not None:
        try:
            try:
                data = _probe.get_json(_probe.endpoint(origin, "/model_info"), _PROBE_DEADLINE, started)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 404:
                    raise
                data = _probe.get_json(_probe.endpoint(origin, "/get_model_info"), _PROBE_DEADLINE, started)
            if isinstance(data, dict) and isinstance(data.get("model_path"), str) and data["model_path"].strip():
                info = data
        except (httpx.TransportError, TimeoutError):
            status = "unreachable"
        except (httpx.HTTPError, ValueError):
            pass
        else:
            status = "ok" if info is not None else "unavailable"

    process_bytes = _process_bytes(excluded_pids)
    accounting = memory.rss_accounting()
    if info is None:
        if process_bytes is None:
            return None
        return {
            "runtime": "sglang",
            "label": "SGLang",
            "status": status,
            "accounting": accounting,
            "process_bytes": process_bytes,
            "models": [],
            "note": note or "SGLang model information is unavailable. Process RSS includes runtime overhead.",
        }

    model_id = info["model_path"].strip()
    return {
        "runtime": "sglang",
        "label": "SGLang",
        "status": "ok",
        "accounting": accounting,
        "process_bytes": process_bytes,
        "models": [
            {
                "id": model_id,
                "bytes": None,
                "pool": None,
                "context_bytes": None,
                "cache_bytes": None,
                "cold_cache_bytes": None,
            }
        ],
        "note": "Byte-valued SGLang model memory is not available. Process RSS includes runtime overhead.",
    }
