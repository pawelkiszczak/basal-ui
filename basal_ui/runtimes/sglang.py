"""Read-only observations of a local SGLang Runtime server."""

import ipaddress
import os
from urllib.parse import urlsplit

import httpx

from .. import memory


def _base_url():
    url = os.environ.get("BASAL_UI_SGLANG_URL", "http://127.0.0.1:30000").rstrip("/")
    try:
        host = urlsplit(url).hostname
        if host == "localhost":
            return url
        if host is not None and ipaddress.ip_address(host).is_loopback:
            return url
    except ValueError:
        pass
    return None


def observe() -> dict | None:
    """Report a confirmed model and process RSS, or None when no SGLang process/server exists."""
    base_url = _base_url()
    if base_url is None:
        return None

    info = None
    try:
        response = httpx.get(base_url + "/get_model_info", timeout=0.75)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and isinstance(data.get("model_path"), str) and data["model_path"].strip():
            info = data
    except (httpx.HTTPError, ValueError):
        pass

    process_bytes = memory.process_rss(r"\bsglang\.launch_server\b")
    accounting = memory.rss_accounting()
    if info is None:
        if process_bytes is None:
            return None
        return {
            "runtime": "sglang",
            "label": "SGLang",
            "status": "unavailable",
            "accounting": accounting,
            "process_bytes": process_bytes,
            "models": [],
            "note": "SGLang model information is unavailable. Process RSS includes runtime overhead.",
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
