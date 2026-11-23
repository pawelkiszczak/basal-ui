"""Read-only memory observations for a local oMLX server."""

import json
import time
from pathlib import Path

import httpx

from . import _probe

_DEFAULT_PORT = 8000
_DEADLINE = 0.75
_RSS_PATTERN = r"^(?:.*/)?omlx-server(?:\s|$)"

_NOTE = (
    "Process RSS includes oMLX runtime overhead and excludes GPU allocations on unified-memory hosts. "
    "Per-model memory and context bytes are not reported."
)
_HEALTH_NOTE = (
    "Loaded models: {count}; their identities are unavailable. The oMLX-reported model-memory total is a "
    "planned resident-size estimate, separate from process RSS."
)
_UNAVAILABLE_NOTE = (
    "oMLX memory API is unavailable; process RSS includes oMLX runtime overhead and excludes GPU allocations "
    "on unified-memory hosts. Model and cache details could not be read."
)


def _settings_path():
    return Path.home() / ".omlx" / "settings.json"


def _origin():
    try:
        settings = json.loads(_settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        settings = {}
    server = settings.get("server") if isinstance(settings, dict) else None
    if not isinstance(server, dict):
        server = {}
    port = server.get("port")
    port = port if type(port) is int and 1 <= port <= 65535 else _DEFAULT_PORT
    host = server.get("host", server.get("bind", "127.0.0.1"))
    host = f"[{host}]" if isinstance(host, str) and ":" in host and not host.startswith("[") else host
    scheme = server.get("scheme", "http")
    return _probe.loopback_origin(f"{scheme}://{host}:{port}")


def _bytes(value):
    return value if type(value) is int and value >= 0 else None


def _api_snapshot(origin, deadline, started):
    payload = _probe.get_json(_probe.endpoint(origin, "/admin/api/stats"), deadline, started)
    if not isinstance(payload, dict):
        return None
    active = payload.get("active_models")
    runtime_cache = payload.get("runtime_cache")
    if not isinstance(active, dict) or not isinstance(active.get("models"), list):
        return None
    cache_rows = runtime_cache.get("models", []) if isinstance(runtime_cache, dict) else []
    cache_by_id = (
        {row["id"]: row for row in cache_rows if isinstance(row, dict) and isinstance(row.get("id"), str)}
        if isinstance(cache_rows, list)
        else {}
    )
    models = []
    for row in active["models"]:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str):
            continue
        if row.get("is_loading") is True:
            models.append({"id": row["id"], "state": "loading"})
            continue
        if row.get("is_loading") is not False:
            continue
        cache = cache_by_id.get(row["id"], {})
        distributed = cache.get("cache_tier") == "rank-prompt-snapshot"
        models.append(
            {
                "id": row["id"],
                "bytes": _bytes(row.get("actual_size")),
                "size_estimated": False,
                "pool": "unified",
                "context_bytes": None,
                "cache_bytes": None if distributed else _bytes(cache.get("hot_cache_size_bytes")),
                "cold_cache_bytes": None if distributed else _bytes(cache.get("total_size_bytes")),
            }
        )
    return models


def _health_snapshot(origin, deadline, started):
    payload = _probe.get_json(_probe.endpoint(origin, "/health"), deadline, started)
    if not isinstance(payload, dict) or payload.get("status") not in ("healthy", "loading"):
        return None
    pool = payload.get("engine_pool")
    if not isinstance(pool, dict):
        return None
    loaded_count = _bytes(pool.get("loaded_count"))
    model_count = _bytes(pool.get("model_count"))
    model_memory = _bytes(pool.get("current_model_memory"))
    if loaded_count is None or model_count is None or loaded_count > model_count:
        return None
    return payload["status"], loaded_count, model_memory


def _health_models(loaded_count, model_memory):
    return [
        {
            "id": f"{loaded_count} loaded models",
            "aggregate": loaded_count,
            "bytes": model_memory,
            "size_estimated": True,
            "pool": "unified",
            "context_bytes": None,
            "cache_bytes": None,
            "cold_cache_bytes": None,
        }
    ]


def _observation(status, process_bytes, models, note):
    return {
        "runtime": "omlx",
        "label": "oMLX",
        "status": status,
        "accounting": "rss_excludes_gpu",
        "process_bytes": process_bytes,
        "models": models,
        "note": note,
    }


def observe() -> dict | None:
    """Return a local oMLX observation, or None when no oMLX process is present."""
    from ..memory import process_rss

    process_bytes = process_rss(_RSS_PATTERN)
    started = time.monotonic()
    try:
        origin = _origin()
    except (ValueError, TypeError):
        return _observation("unavailable", process_bytes, [], _UNAVAILABLE_NOTE) if process_bytes is not None else None
    try:
        models = _api_snapshot(origin, _DEADLINE, started)
    except (httpx.ConnectError, httpx.TimeoutException, TimeoutError):
        return _observation("unreachable", process_bytes, [], _UNAVAILABLE_NOTE) if process_bytes is not None else None
    except (httpx.HTTPError, ValueError):
        models = None
    if models is not None:
        return _observation("ok", process_bytes, models, _NOTE)
    try:
        health = _health_snapshot(origin, _DEADLINE, started)
    except (httpx.ConnectError, httpx.TimeoutException, TimeoutError):
        return _observation("unreachable", process_bytes, [], _UNAVAILABLE_NOTE) if process_bytes is not None else None
    except (httpx.HTTPError, ValueError):
        health = None
    if health is not None:
        _, loaded_count, model_memory = health
        models = _health_models(loaded_count, model_memory)
        status = "ok"
        return _observation(status, process_bytes, models, _HEALTH_NOTE)
    if process_bytes is None:
        return None
    return _observation("unavailable", process_bytes, [], _UNAVAILABLE_NOTE)
