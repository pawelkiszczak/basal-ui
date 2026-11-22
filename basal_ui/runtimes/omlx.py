"""Read-only memory observations for a local oMLX server."""

import json
from pathlib import Path

import httpx

from .. import memory

_DEFAULT_PORT = 8000
_TIMEOUT = 0.75
_RSS_PATTERN = r"(?i)\bomlx-server\b"


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


def _port():
    try:
        settings = json.loads(_settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _DEFAULT_PORT
    server = settings.get("server") if isinstance(settings, dict) else None
    port = server.get("port") if isinstance(server, dict) else None
    return port if type(port) is int and 1 <= port <= 65535 else _DEFAULT_PORT


def _bytes(value):
    return value if type(value) is int and value >= 0 else None


def _api_snapshot(port):
    try:
        response = httpx.get(f"http://127.0.0.1:{port}/admin/api/stats", timeout=_TIMEOUT)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None
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
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or row.get("is_loading") is not False:
            continue
        cache = cache_by_id.get(row["id"], {})
        models.append(
            {
                "id": row["id"],
                "bytes": None,
                "pool": "unified",
                "context_bytes": None,
                "cache_bytes": _bytes(cache.get("hot_cache_size_bytes")),
                "cold_cache_bytes": _bytes(cache.get("total_size_bytes")),
            }
        )
    return models


def _health_snapshot(port):
    try:
        response = httpx.get(f"http://127.0.0.1:{port}/health", timeout=_TIMEOUT)
        if response.status_code not in (200, 503):
            return None
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None
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
            "bytes": model_memory,
            "pool": "unified",
            "context_bytes": None,
            "cache_bytes": None,
            "cold_cache_bytes": None,
        }
    ]


def _health_observation(port, process_bytes):
    health = _health_snapshot(port)
    if health is None:
        return None
    health_status, loaded_count, model_memory = health
    return {
        "runtime": "omlx",
        "label": "oMLX",
        "status": "ok" if loaded_count else "empty" if health_status == "healthy" else "unavailable",
        "accounting": "rss_excludes_gpu",
        "process_bytes": process_bytes,
        "models": _health_models(loaded_count, model_memory),
        "note": _HEALTH_NOTE,
    }


def observe() -> dict | None:
    """Return a local oMLX observation, or None when no oMLX process is present."""
    process_bytes = memory.process_rss(_RSS_PATTERN)
    port = _port()
    models = _api_snapshot(port)
    if models is not None:
        return {
            "runtime": "omlx",
            "label": "oMLX",
            "status": "ok" if models else "empty",
            "accounting": "rss_excludes_gpu",
            "process_bytes": process_bytes,
            "models": models,
            "note": _NOTE,
        }
    health = _health_observation(port, process_bytes)
    if health is not None:
        return health
    if process_bytes is None:
        return None
    return {
        "runtime": "omlx",
        "label": "oMLX",
        "status": "unavailable",
        "accounting": "rss_excludes_gpu",
        "process_bytes": process_bytes,
        "models": [],
        "note": _UNAVAILABLE_NOTE,
    }
