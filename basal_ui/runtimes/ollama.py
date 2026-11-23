"""Read-only memory observations for a locally running Ollama server."""

import os
import subprocess
import time
from contextlib import suppress
from urllib.parse import urlsplit

import httpx

from . import _probe


def _url() -> str:
    value = os.environ.get("OLLAMA_HOST", "127.0.0.1:11434")
    if value == "::":
        value = "[::]:11434"
    parsed = urlsplit(value if "://" in value else f"http://{value}")
    try:
        port = parsed.port or 11434
    except ValueError as exc:
        raise ValueError("OLLAMA_HOST must point to a local Ollama server") from exc
    host = parsed.hostname
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    if not host:
        raise ValueError("OLLAMA_HOST must point to a local Ollama server")
    display_host = f"[{host}]" if ":" in host else host
    return _probe.loopback_origin(f"{parsed.scheme}://{display_host}:{port}")


def _observation(models: list, process_bytes: int | None, accounting: str) -> dict:
    unified = accounting == "rss_excludes_gpu"
    observed = []
    for model in models:
        name = model.get("name")
        if not isinstance(name, str) or not name.strip():
            name = model.get("model")
        if not isinstance(name, str) or not name.strip():
            continue
        size = model.get("size")
        size = size if type(size) is int and size >= 0 else None
        vram = model.get("size_vram")
        vram = vram if type(vram) is int and vram >= 0 else None
        valid_split = size is not None and vram is not None and vram <= size
        pool = "unified" if unified else "ram" if vram == 0 else "gpu" if valid_split and vram == size else None
        observed.append(
            {
                "id": name,
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
        "status": "ok",
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


def _server_pids() -> list[int]:
    try:
        output = subprocess.run(
            ["ps", "-Ao", "pid=,comm=,args="], capture_output=True, text=True, timeout=5, check=True
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    servers, workers = [], []
    for line in output.splitlines():
        fields = line.strip().split(None, 2)
        if len(fields) != 3 or not fields[0].isdigit():
            continue
        pid, command, args = int(fields[0]), os.path.basename(fields[1]), fields[2].split()
        if command == "ollama_llama_server":
            workers.append(pid)
        elif command == "ollama" and args and os.path.basename(args[0]) == "ollama" and len(args) > 1:
            if args[1] == "serve":
                servers.append(pid)
            elif args[1] == "runner":
                workers.append(pid)
    return servers or workers


def _rss(fleet) -> int | None:
    roots = _server_pids()
    if not roots:
        return None
    excluded = set()
    if fleet is not None:
        with suppress(Exception):
            excluded = {item["pid"] for item in fleet.memory_snapshot() if item.get("pid")}
    measurements = [_probe.process_tree_rss(pid, excluded) for pid in roots]
    if any(value is None for value in measurements):
        return None
    return sum(measurements)


def observe(fleet=None) -> dict | None:
    """Read Ollama's local loaded-model list and process RSS without changing runtime state."""
    from .. import memory

    process_bytes = _rss(fleet)
    accounting = memory.rss_accounting()
    try:
        url = _url()
    except ValueError:
        return _fallback("unavailable", "OLLAMA_HOST must point to a local Ollama server.", process_bytes, accounting)
    started = time.monotonic()
    try:
        version = _probe.get_json(_probe.endpoint(url, "/api/version"), 1.5, started)
        confirmed = isinstance(version, dict) and isinstance(version.get("version"), str) and bool(version["version"])
        payload = _probe.get_json(_probe.endpoint(url, "/api/ps"), 1.5, started)
    except (TimeoutError, httpx.ConnectError, httpx.TimeoutException):
        return _fallback("unreachable", "Ollama API is unreachable.", process_bytes, accounting)
    except httpx.HTTPError:
        return _fallback(
            "unavailable",
            "Ollama API returned an invalid response.",
            process_bytes,
            accounting,
            api_present=bool(_server_pids()),
        )
    except (ValueError, TypeError):
        return _fallback(
            "unavailable",
            "Ollama API returned an invalid response.",
            process_bytes,
            accounting,
            api_present=bool(_server_pids()),
        )
    if not confirmed and not _server_pids():
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        return _fallback(
            "unavailable", "Ollama API returned an invalid response.", process_bytes, accounting, api_present=True
        )
    if any(not isinstance(model, dict) for model in payload["models"]):
        return _fallback(
            "unavailable", "Ollama API returned an invalid response.", process_bytes, accounting, api_present=True
        )
    return _observation(payload["models"], process_bytes, accounting)
