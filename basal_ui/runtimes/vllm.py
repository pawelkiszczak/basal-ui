"""Read-only memory observations for a locally served vLLM instance."""

import ipaddress
import math
import os
import re
from urllib.parse import urlsplit

import httpx

from .. import memory

_URL = "BASAL_UI_VLLM_URL"
_DEFAULT_URL = "http://127.0.0.1:8000"
_CACHE_METRICS = ("vllm:gpu_cache_usage_perc", "vllm:kv_cache_usage_perc")


def _local_url():
    value = os.environ.get(_URL, _DEFAULT_URL).rstrip("/")
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        local = hostname == "localhost" or (hostname is not None and ipaddress.ip_address(hostname).is_loopback)
        if (
            parsed.scheme == "http"
            and local
            and parsed.port
            and not parsed.username
            and not parsed.password
            and not parsed.path
            and not parsed.query
            and not parsed.fragment
        ):
            return value
    except ValueError:
        pass
    return None


def _models(payload):
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list) or not data:
        return None
    if not all(
        isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and item["id"]
        and isinstance(item.get("max_model_len"), int)
        and not isinstance(item["max_model_len"], bool)
        and item["max_model_len"] > 0
        for item in data
    ):
        return None
    return data


def _cache_usage(text):
    for name in _CACHE_METRICS:
        for line in text.splitlines():
            match = re.match(rf"^{re.escape(name)}(?:\{{[^}}]*\}})?\s+([0-9.eE+-]+)(?:\s|$)", line)
            if match:
                try:
                    value = float(match.group(1))
                except ValueError:
                    continue
                if math.isfinite(value) and 0 <= value <= 1:
                    return value * 100
    return None


def observe() -> dict | None:
    """Return an observation only when vLLM is identified by its model metadata or process."""
    base_url = _local_url()
    model_data = None
    try:
        if base_url is not None:
            response = httpx.get(base_url + "/v1/models", timeout=0.75)
            response.raise_for_status()
            model_data = _models(response.json())
    except (httpx.HTTPError, ValueError):
        pass

    process_bytes = memory.process_rss(r"\bvllm\b")
    if model_data is None and process_bytes is None:
        return None

    accounting = memory.rss_accounting()
    models = [
        {
            "id": item["id"],
            "bytes": None,
            "pool": None,
            "context_bytes": None,
            "cache_bytes": None,
            "cold_cache_bytes": None,
        }
        for item in model_data or []
    ]
    cache_usage = None
    if model_data is not None:
        try:
            response = httpx.get(base_url + "/metrics", timeout=0.75)
            response.raise_for_status()
            cache_usage = _cache_usage(response.text)
        except httpx.HTTPError:
            pass

    if model_data is None:
        status = "unreachable"
        note = "vLLM process RSS includes runtime overhead; the local model API is unavailable."
    else:
        status = "ok"
        note = (
            f"vLLM KV-cache usage: {cache_usage:.1f}%. Process RSS includes vLLM runtime overhead."
            if cache_usage is not None
            else "vLLM KV-cache usage is not reported in bytes. Process RSS includes vLLM runtime overhead."
        )
    return {
        "runtime": "vllm",
        "label": "vLLM",
        "status": status,
        "accounting": accounting,
        "process_bytes": process_bytes,
        "models": models,
        "note": note,
    }
