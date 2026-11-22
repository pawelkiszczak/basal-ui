"""Read-only memory observations for a locally running Ollama server."""

import ipaddress
import os
from urllib.parse import urlsplit

import httpx

from .. import memory


def _url() -> str:
    host = os.environ.get("OLLAMA_HOST", "127.0.0.1:11434")
    parsed = urlsplit(host if "://" in host else f"http://{host}")
    try:
        address = ipaddress.ip_address(parsed.hostname or "")
        local = address.is_loopback
    except ValueError:
        local = parsed.hostname == "localhost"
    if not local or parsed.scheme not in ("http", "https") or parsed.path not in ("", "/"):
        raise ValueError("OLLAMA_HOST must point to a local Ollama server")
    return f"{parsed.scheme}://{parsed.netloc}"


def _observation(models: list, process_bytes: int | None, accounting: str) -> dict:
    unified = accounting == "rss_excludes_gpu"
    observed = []
    for model in models:
        size = model.get("size")
        size = size if type(size) is int and size >= 0 else None
        vram = model.get("size_vram")
        vram = vram if type(vram) is int and vram >= 0 else None
        valid_split = size is not None and vram is not None and vram <= size
        pool = "unified" if unified else "ram" if vram == 0 else "gpu" if valid_split and vram == size else None
        observed.append(
            {
                "id": model.get("name") or model.get("model") or "Ollama model",
                "bytes": size,
                "pool": pool,
                "context_bytes": None,
                "cache_bytes": None,
                "cold_cache_bytes": None,
            }
        )
    return {
        "runtime": "ollama",
        "label": "Ollama",
        "status": "ok" if observed else "empty",
        "accounting": accounting,
        "process_bytes": process_bytes,
        "models": observed,
        "note": (
            "Process RSS includes runtime overhead. Ollama reports total loaded model size; size_vram is its VRAM "
            "portion. Context length is in tokens, not bytes; cache bytes are not measured."
        ),
    }


def _fallback(
    status: str,
    note: str,
    process_bytes: int | None,
    accounting: str,
    *,
    api_present: bool = False,
) -> dict | None:
    if process_bytes is None and not api_present:
        return None
    return {
        "runtime": "ollama",
        "label": "Ollama",
        "status": status,
        "accounting": accounting,
        "process_bytes": process_bytes,
        "models": [],
        "note": f"{note} Process RSS includes runtime overhead." if process_bytes is not None else note,
    }


def observe() -> dict | None:
    """Read Ollama's local loaded-model list and process RSS without changing runtime state."""
    process_bytes = memory.process_rss(r"\bollama\b")
    accounting = memory.rss_accounting()
    try:
        url = _url()
    except ValueError:
        return _fallback(
            "unavailable",
            "OLLAMA_HOST must point to a local Ollama server.",
            process_bytes,
            accounting,
        )
    try:
        response = httpx.get(url + "/api/ps", timeout=0.75)
        response.raise_for_status()
        payload = response.json()
    except httpx.ConnectError:
        return _fallback("unreachable", "Ollama API is unreachable.", process_bytes, accounting)
    except httpx.TimeoutException:
        return _fallback("unreachable", "Ollama API is unreachable.", process_bytes, accounting)
    except httpx.HTTPStatusError:
        return _fallback(
            "unavailable",
            "Ollama API returned an invalid response.",
            process_bytes,
            accounting,
            api_present=True,
        )
    except httpx.HTTPError:
        return _fallback("unavailable", "Ollama API is unavailable.", process_bytes, accounting)
    except (ValueError, TypeError):
        return _fallback(
            "unavailable",
            "Ollama API returned an invalid response.",
            process_bytes,
            accounting,
            api_present=True,
        )

    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        return _fallback(
            "unavailable",
            "Ollama API returned an invalid response.",
            process_bytes,
            accounting,
            api_present=True,
        )
    models = payload["models"]
    if any(not isinstance(model, dict) for model in models):
        return _fallback(
            "unavailable",
            "Ollama API returned an invalid response.",
            process_bytes,
            accounting,
            api_present=True,
        )
    return _observation(models, process_bytes, accounting)
