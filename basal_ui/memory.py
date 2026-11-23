"""Live memory observations from the host, Fleet and optional runtime providers."""

import math
import os
import platform
import re
import subprocess
import time

import httpx

from . import hardware

PROVIDERS = []


def register_provider(path: str) -> None:
    """Register a dotted provider path, imported only when a snapshot is observed."""
    PROVIDERS.append(path)


def _provider(provider):
    if isinstance(provider, str):
        from importlib import import_module

        module, name = provider.rsplit(".", 1)
        return getattr(import_module(module), name)
    return provider


def process_rss(pattern: str) -> int | None:
    """Summed resident bytes of processes whose command line matches the regex `pattern`, including runtime overhead.
    None when no process matches or ps is unavailable. On unified-memory hosts RSS excludes GPU allocations."""
    if os.name != "posix":
        return None
    try:
        out = subprocess.run(["ps", "-Ao", "rss=,args="], capture_output=True, text=True, timeout=5, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    regex = re.compile(pattern)
    sizes = []
    for line in out.splitlines():
        rss, _, args = line.strip().partition(" ")
        if rss.isdigit() and regex.search(args):
            sizes.append(int(rss) * 1024)
    return sum(sizes) if sizes else None


def rss_accounting() -> str:
    """Accounting basis of process RSS here: on Apple unified memory RSS omits Metal/MLX GPU allocations."""
    return "rss_excludes_gpu" if platform.system() == "Darwin" and platform.machine() == "arm64" else "resident"


def _host(hardware_fn):
    hw = hardware_fn()
    unified = "strix" in hw.engines or (hw.platform == "apple" and platform.machine() == "arm64")
    ram_total = hw.system_ram_bytes
    available = hardware._available_bytes()
    ram_used = max(0, ram_total - available) if ram_total is not None and available is not None else None
    if platform.system() == "Linux":
        ram_used = None  # MemAvailable includes reclaimable file cache unlike process RSS.
    gpu = []
    if hw.platform == "cuda" and not unified:
        gpu.append(
            {
                "name": hw.device_name,
                "total": hw.memory_bytes,
                "used": max(0, hw.memory_bytes - hw.available_bytes) if hw.available_bytes is not None else None,
            }
        )
    return {
        "ram_total": ram_total,
        "ram_used": ram_used,
        "unified": unified,
        "gpu": gpu,
        "accounting": "rss_excludes_gpu" if unified else "resident",
    }


def _cache_bytes(url):
    try:
        response = httpx.get(url + "/metrics", timeout=1)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    for line in response.text.splitlines():
        match = re.match(r"^basal_state_cache_bytes(?:\{[^}]*\})?\s+([0-9.eE+-]+)(?:\s|$)", line)
        if match:
            try:
                value = float(match.group(1))
            except ValueError:
                return None
            if math.isfinite(value) and value >= 0:
                return int(value)
            return None
    return None


def _fleet(fleet, unified):
    try:
        models = fleet.memory_snapshot()
    except Exception:  # noqa: BLE001 — one unavailable runtime must not hide other observations
        return {
            "runtime": "basal",
            "label": "Fleet",
            "status": "unavailable",
            "accounting": "rss_excludes_gpu" if unified else "resident",
            "process_bytes": None,
            "models": [],
            "note": "Fleet memory could not be read.",
        }
    native = [model for model in models if model.get("kind") == "basal" and model.get("state") != "idle"]
    ready = [model for model in native if model.get("state") == "ready"]
    measured = [model for model in native if type(model.get("rss_bytes")) is int and model["rss_bytes"] >= 0]
    observations = [
        {
            "id": model.get("id", ""),
            "bytes": model["rss_bytes"] if type(model.get("rss_bytes")) is int and model["rss_bytes"] >= 0 else None,
            "pool": "unified" if unified else "ram",
            "context_bytes": None,
            "cache_bytes": _cache_bytes(model["base_url"]) if model.get("base_url") else None,
            "cache_pool": "vram" if model.get("mode") == "cuda" else ("unified" if unified else "ram"),
            "cold_cache_bytes": None,
        }
        for model in ready
    ]
    process_bytes = sum(model["rss_bytes"] for model in measured) if measured else (0 if not native else None)
    status = (
        "ok"
        if ready
        else (
            "unreachable"
            if any(model.get("state") == "error" for model in native)
            else ("unavailable" if native else "empty")
        )
    )
    return {
        "runtime": "basal",
        "label": "Fleet",
        "status": status,
        "accounting": "rss_excludes_gpu" if unified else "resident",
        "process_bytes": process_bytes,
        "models": observations,
        "note": (
            "Process RSS excludes GPU allocations on unified-memory hosts. "
            "Engine state cache is measured only when active; context bytes and "
            "disk-backed cold cache are not measured."
            if unified
            else "Engine state cache is measured only when active; context bytes and "
            "disk-backed cold cache are not measured."
        ),
    }


def snapshot(fleet, providers, hardware_fn=hardware.detect):
    """Return live host and runtime memory measurements, preserving unknown values as None."""
    try:
        host = _host(hardware_fn)
    except Exception:  # noqa: BLE001 — host probes are best-effort
        host = {"ram_total": None, "ram_used": None, "unified": False, "gpu": [], "accounting": "resident"}
    runtimes = [_fleet(fleet, host["unified"])]
    for provider in providers:
        name = (
            provider.rsplit(".", 1)[-1]
            if isinstance(provider, str)
            else getattr(provider, "runtime", getattr(provider, "__name__", "runtime"))
        )
        try:
            provider = _provider(provider)
            observation = provider()
        except Exception:  # noqa: BLE001 — isolate optional runtime failures
            runtimes.append(
                {
                    "runtime": name,
                    "label": name,
                    "status": "unavailable",
                    "accounting": "unknown",
                    "process_bytes": None,
                    "models": [],
                    "note": "Runtime memory observation is unavailable.",
                }
            )
            continue
        if observation is not None:
            runtimes.append(observation)

    # Subtract every measured runtime whatever its API status; any unknown runtime total makes the remainder unknown.
    # Measured zero needs no shared basis.
    measured = [runtime for runtime in runtimes if runtime["process_bytes"]]
    other = (
        max(0, host["ram_used"] - sum(runtime["process_bytes"] for runtime in measured))
        if host["ram_used"] is not None
        and all(runtime["process_bytes"] is not None for runtime in runtimes)
        and all(runtime["accounting"] == host["accounting"] for runtime in measured)
        else None
    )
    return {"time": time.time(), "host": host, "runtimes": runtimes, "other": other}


from .runtimes import vllm  # noqa: E402  (runtime providers call process_rss defined above)

PROVIDERS.append(vllm.observe)
