"""Read-only LM Studio memory observation."""

import json
import os
import subprocess
import time
from pathlib import Path

import httpx

from . import _probe

API_ORIGIN = "http://127.0.0.1:1234"
CONFIG_PATH = Path.home() / ".lmstudio" / ".internal" / "http-server-config.json"
PROCESS_NAMES = {"LM Studio", "lms", "llmster"}


def _api_origin() -> str:
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return API_ORIGIN
    port = config.get("port") if isinstance(config, dict) else None
    if type(port) is not int or not 1 <= port <= 65535:
        return API_ORIGIN
    return f"http://127.0.0.1:{port}"


def _root_pid() -> int | None:
    if os.name != "posix":
        return None
    try:
        output = subprocess.run(
            ["ps", "-axo", "pid=,comm="], capture_output=True, text=True, timeout=5, check=True
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    app_pids = []
    service_pids = []
    for line in output.splitlines():
        fields = line.strip().split(maxsplit=1)
        if len(fields) != 2 or not fields[0].isdigit():
            continue
        name = fields[1].rsplit("/", 1)[-1]
        if name == "LM Studio":
            app_pids.append(int(fields[0]))
        elif name in {"lms", "llmster"} or name.startswith("lmstudio-"):
            service_pids.append(int(fields[0]))
    candidates = app_pids or service_pids
    return min(candidates) if candidates else None


def observe() -> dict | None:
    """Read loaded models and LM Studio process RSS without changing runtime state."""
    from .. import memory

    started = time.monotonic()
    accounting = memory.rss_accounting()
    root_pid = _root_pid()
    process_bytes = _probe.process_tree_rss(root_pid) if root_pid is not None else None
    base = {
        "runtime": "lmstudio",
        "label": "LM Studio",
        "accounting": accounting,
        "process_bytes": process_bytes,
        "models": [],
    }
    try:
        url = _probe.endpoint(_probe.loopback_origin(_api_origin()), "/api/v1/models")
        payload = _probe.get_json(url, 1.0, started)
    except (httpx.ConnectError, httpx.TimeoutException, TimeoutError) as exc:
        if process_bytes is None:
            return None
        return {
            **base,
            "status": "unreachable",
            "note": f"LM Studio API transport failed ({exc}). Process RSS includes runtime overhead.",
        }
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        if process_bytes is None:
            return None
        return {
            **base,
            "status": "unavailable",
            "note": f"LM Studio API response is unusable ({exc}). Process RSS includes runtime overhead.",
        }

    if not isinstance(payload, dict):
        return (
            None
            if process_bytes is None
            else {**base, "status": "unavailable", "note": "LM Studio returned an unreadable model list."}
        )

    records = payload.get("models")
    if not isinstance(records, list):
        return (
            None
            if process_bytes is None
            else {**base, "status": "unavailable", "note": "LM Studio returned an unreadable model list."}
        )

    models = []
    readable = 0
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("loaded_instances"), list):
            continue
        key = record.get("key")
        if not isinstance(key, str) or not key:
            continue
        readable += 1
        for instance in record["loaded_instances"]:
            if not isinstance(instance, dict):
                continue
            instance_id = instance.get("instance_id") or instance.get("identifier") or instance.get("id")
            if not isinstance(instance_id, str) or not instance_id:
                continue
            models.append(
                {
                    "id": instance_id,
                    "name": key,
                    "bytes": None,
                    "pool": None,
                    "context_bytes": None,
                    "cache_bytes": None,
                    "cache_pool": None,
                    "cold_cache_bytes": None,
                }
            )
    if records and not readable:
        return {**base, "status": "unavailable", "note": "LM Studio returned no readable model records."}
    return {
        **base,
        "status": "ok",
        "models": models,
        "note": (
            "Loaded model memory and cache usage are not exposed. Process RSS includes runtime overhead."
            if models
            else "No loaded models. Process RSS includes runtime overhead."
        ),
    }
