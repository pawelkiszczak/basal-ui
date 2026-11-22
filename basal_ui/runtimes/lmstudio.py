"""Read-only LM Studio memory observation."""

import json
from pathlib import Path

import httpx

API_URL = "http://127.0.0.1:1234/api/v1/models"
CONFIG_PATH = Path.home() / ".lmstudio" / ".internal" / "http-server-config.json"
PROCESS_PATTERN = r"/Applications/LM Studio\.app|\.lmstudio/"


def _api_url() -> str:
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return API_URL
    port = config.get("port") if isinstance(config, dict) else None
    if type(port) is not int or not 1 <= port <= 65535:
        return API_URL
    return f"http://127.0.0.1:{port}/api/v1/models"


def observe() -> dict | None:
    """Read loaded models and LM Studio process RSS without changing runtime state."""
    from .. import memory

    process_bytes = memory.process_rss(PROCESS_PATTERN)
    if process_bytes is None:
        return None

    accounting = memory.rss_accounting()
    pool = "unified" if accounting == "rss_excludes_gpu" else "ram"
    try:
        response = httpx.get(_api_url(), timeout=0.5, trust_env=False)
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return {
            "runtime": "lmstudio",
            "label": "LM Studio",
            "status": "unreachable",
            "accounting": accounting,
            "process_bytes": process_bytes,
            "models": [],
            "note": (
                "LM Studio API at its configured local port is unavailable; model identity, context bytes, and cache usage are unknown. "
                "Process RSS includes runtime overhead."
            ),
        }

    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        return {
            "runtime": "lmstudio",
            "label": "LM Studio",
            "status": "unavailable",
            "accounting": accounting,
            "process_bytes": process_bytes,
            "models": [],
            "note": "LM Studio returned an unreadable model list. Process RSS includes runtime overhead.",
        }

    models = []
    for model in payload["models"]:
        if not isinstance(model, dict) or not isinstance(model.get("loaded_instances"), list):
            continue
        model_id = model.get("key")
        if not isinstance(model_id, str) or not model_id:
            continue
        for _ in model["loaded_instances"]:
            models.append(
                {
                    "id": model_id,
                    "bytes": None,
                    "pool": pool,
                    "context_bytes": None,
                    "cache_bytes": None,
                    "cold_cache_bytes": None,
                }
            )

    return {
        "runtime": "lmstudio",
        "label": "LM Studio",
        "status": "ok" if models else "empty",
        "accounting": accounting,
        "process_bytes": process_bytes,
        "models": models,
        "note": (
            "Loaded context is reported in tokens, not bytes; model memory and cache usage are not exposed. "
            "Process RSS includes runtime overhead."
            if models
            else "Process RSS includes runtime overhead."
        ),
    }
