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


def _host(hardware_fn):
    hw = hardware_fn()
    unified = hw.platform == "apple" and platform.machine() == "arm64"
    ram_total = hw.system_ram_bytes
    available = hardware._available_bytes()
    ram_used = max(0, ram_total - available) if ram_total is not None and available is not None else None
    gpu = []
    if hw.platform == "cuda":
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
        "accounting": "resident",
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
        models = fleet.status().get("models", [])
    except Exception:  # noqa: BLE001 — one unavailable runtime must not hide other observations
        basis = "rss_excludes_gpu" if unified else "resident"
        return {
            "runtime": "basal",
            "label": "Fleet",
            "status": "unavailable",
            "accounting": basis,
            "process_bytes": None,
            "models": [],
            "note": "Fleet memory could not be read.",
        }

    native = [model for model in models if model.get("kind") == "basal" and model.get("state") != "idle"]
    ready = [model for model in native if model.get("state") == "ready"]
    observations = []
    for model in ready:
        rss = model.get("rss_bytes")
        observations.append(
            {
                "id": model.get("id", ""),
                "bytes": rss if type(rss) is int and rss >= 0 else None,
                "pool": "unified" if unified else "ram",
                "context_bytes": None,
                "cache_bytes": _cache_bytes(model["base_url"]) if model.get("base_url") else None,
                "cold_cache_bytes": None,
            }
        )
    if ready:
        process_bytes = (
            sum(model["bytes"] for model in observations)
            if all(model["bytes"] is not None for model in observations)
            else None
        )
        status = "ok"
    elif native:
        process_bytes = None
        status = "unreachable" if any(model.get("state") == "error" for model in native) else "unavailable"
    else:
        process_bytes, status = 0, "empty"
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
        try:
            observation = provider()
        except Exception:  # noqa: BLE001 — isolate optional runtime failures
            name = getattr(provider, "runtime", getattr(provider, "__name__", "runtime"))
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

    totals = [runtime["process_bytes"] for runtime in runtimes if runtime["status"] == "ok"]
    other = (
        max(0, host["ram_used"] - sum(totals))
        if host["ram_used"] is not None
        and all(total is not None for total in totals)
        and all(runtime["accounting"] == host["accounting"] for runtime in runtimes if runtime["status"] == "ok")
        else None
    )
    return {"time": time.time(), "host": host, "runtimes": runtimes, "other": other}
