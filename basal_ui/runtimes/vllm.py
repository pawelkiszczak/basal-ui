"""Read-only memory observations for a locally served vLLM instance."""

import contextlib
import os
import re
import subprocess
import time

import httpx

from . import _probe

_URL = "BASAL_UI_VLLM_URL"
_DEFAULT_URL = "http://127.0.0.1:8000"
_DEADLINE = 2.0
_CACHE_METRICS = ("vllm:gpu_cache_usage_perc", "vllm:kv_cache_usage_perc")


def _models(payload):
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return []
    models = []
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not item["id"]:
            continue
        if item.get("parent") is not None or (type(item.get("max_model_len")) is int and item["max_model_len"] > 0):
            models.append(item)
    return models


def _cache_usage(text):
    for name in _CACHE_METRICS:
        results = []
        for line in text.splitlines():
            match = re.match(rf"^{re.escape(name)}(?:\{{([^}}]*)\}})?\s+([0-9.eE+-]+)(?:\s|$)", line)
            if not match:
                continue
            try:
                value = float(match.group(2))
            except ValueError:
                continue
            if not 0 <= value <= 1 or not value < float("inf"):
                continue
            labels = match.group(1) or ""
            engine = re.search(r'(?:^|,)engine="([^"]+)"(?:,|$)', labels)
            results.append({"engine": engine.group(1) if engine else "default", "usage_percent": value * 100})
        if results:
            return results
    return []


def _process_bytes():
    """Measure external vLLM server roots, excluding Fleet's basal-serve process trees."""
    if os.name != "posix":
        return None
    try:
        output = subprocess.run(
            ["ps", "-Ao", "pid=,ppid=,comm=,args="],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None

    processes = {}
    roots = []
    fleet_roots = []
    for line in output.splitlines():
        fields = line.strip().split(maxsplit=3)
        if len(fields) != 4 or not fields[0].isdigit() or not fields[1].isdigit():
            continue
        pid, ppid = int(fields[0]), int(fields[1])
        command, args = fields[2], fields[3]
        processes[pid] = ppid
        argv = args.split()
        executable = os.path.basename(argv[0])
        title = command.rsplit("/", 1)[-1]
        is_vllm_module = len(argv) > 2 and argv[1] == "-m" and argv[2] == "vllm.entrypoints.openai.api_server"
        is_vllm_script = len(argv) > 1 and argv[1].endswith("/vllm/entrypoints/openai/api_server.py")
        is_fleet_vllm = (
            len(argv) > 4
            and argv[1:3] == ["-m", "basal.server"]
            and any(argv[index : index + 2] == ["--mode", "vllm"] for index in range(len(argv) - 1))
        )
        if executable == "basal-serve" or title == "basal-serve" or is_fleet_vllm:
            fleet_roots.append(pid)
        elif executable == "vllm" or title == "vllm" or title.startswith("VLLM::") or is_vllm_module or is_vllm_script:
            roots.append(pid)

    excluded = set(fleet_roots)
    while True:
        descendants = {pid for pid, ppid in processes.items() if ppid in excluded}
        if descendants <= excluded:
            break
        excluded.update(descendants)

    candidates = [pid for pid in roots if pid not in excluded]
    root_set = set(candidates)
    top_roots = []
    for pid in candidates:
        parent = processes[pid]
        while parent and parent not in root_set:
            parent = processes.get(parent, 0)
        if parent not in root_set:
            top_roots.append(pid)

    sizes = [_probe.process_tree_rss(pid, excluded) for pid in top_roots]
    measured = [size for size in sizes if size is not None]
    return sum(measured) if measured else None


def observe() -> dict | None:
    """Return measurements only when a vLLM API or external vLLM process is identified."""
    from .. import memory

    started = time.monotonic()
    try:
        origin = _probe.loopback_origin(os.environ.get(_URL, _DEFAULT_URL))
    except ValueError:
        origin = None
    models = []
    model_status = "unavailable"
    if origin is not None:
        try:
            models = _models(_probe.get_json(_probe.endpoint(origin, "/v1/models"), _DEADLINE, started))
            model_status = "ok" if models else "unavailable"
        except httpx.HTTPStatusError:
            model_status = "unavailable"
        except (httpx.HTTPError, TimeoutError):
            model_status = "unreachable"
        except ValueError:
            model_status = "unavailable"

    version = None
    if origin is not None:
        with contextlib.suppress(httpx.HTTPError, ValueError, TimeoutError):
            version = _probe.get_json(_probe.endpoint(origin, "/version"), _DEADLINE, started)

    cache = []
    metrics_identified = False
    if origin is not None:
        try:
            with httpx.Client(trust_env=False, timeout=_probe.timeout_for(_DEADLINE, started)) as client:
                response = client.get(
                    _probe.endpoint(origin, "/metrics"),
                    timeout=_probe.timeout_for(_DEADLINE, started),
                )
                response.raise_for_status()
                if time.monotonic() - started < _DEADLINE:
                    metrics_identified = bool(re.search(r"^vllm:[A-Za-z0-9_:]+", response.text, re.MULTILINE))
                    cache = _cache_usage(response.text)
        except (httpx.HTTPError, TimeoutError):
            pass

    process_bytes = _process_bytes()
    identity = (isinstance(version, dict) and isinstance(version.get("version"), str) and bool(version["version"])) or (
        metrics_identified
    )
    if not identity and process_bytes is None:
        return None

    return {
        "runtime": "vllm",
        "label": "vLLM",
        "status": model_status,
        "accounting": memory.rss_accounting(),
        "process_bytes": process_bytes,
        "models": [
            {
                "id": item["id"],
                "bytes": None,
                "pool": None,
                "context_bytes": None,
                "cache_bytes": None,
                "cold_cache_bytes": None,
            }
            for item in models
        ],
        "cache": cache,
        "note": (
            "vLLM KV-cache usage by engine: "
            + ", ".join(f"engine {row['engine']} {row['usage_percent']:.1f}%" for row in cache)
            + ". Process RSS includes vLLM runtime overhead."
            if cache
            else "vLLM KV-cache usage is not reported in bytes. Process RSS includes vLLM runtime overhead."
        ),
    }
