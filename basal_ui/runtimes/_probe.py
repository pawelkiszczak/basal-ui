"""Shared helpers for bounded loopback runtime observations."""

import ipaddress
import time
from urllib.parse import urlsplit, urlunsplit

import httpx


def loopback_origin(value: str) -> str:
    """Validate an HTTP(S) loopback origin and return its normalized origin URL."""
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("runtime URL must be an HTTP(S) loopback origin")
    if parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise ValueError("runtime URL must be an origin without path, query, or fragment")
    try:
        address = ipaddress.ip_address(parsed.hostname)
        loopback = address.is_loopback
    except ValueError:
        loopback = parsed.hostname.lower() == "localhost"
    if not loopback:
        raise ValueError("runtime URL must use a loopback host")
    return urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))


def endpoint(origin: str, path: str) -> str:
    """Build an endpoint URL from an already validated loopback origin."""
    return loopback_origin(origin) + "/" + path.lstrip("/")


def timeout_for(deadline: float, started: float | None = None) -> httpx.Timeout:
    """Create HTTPX timeouts capped by seconds remaining in a provider-wide deadline."""
    remaining = deadline - (time.monotonic() - started) if started is not None else deadline
    if remaining <= 0:
        raise TimeoutError("runtime probe deadline exceeded")
    return httpx.Timeout(remaining)


def get_json(url: str, deadline: float, started: float) -> dict:
    """GET and decode JSON with a provider-wide wall-clock timeout budget."""
    remaining = deadline - (time.monotonic() - started)
    if remaining <= 0:
        raise TimeoutError("runtime probe deadline exceeded")
    with httpx.Client(trust_env=False, timeout=httpx.Timeout(remaining)) as client:
        response = client.get(url)
        response.raise_for_status()
        if time.monotonic() - started >= deadline:
            raise TimeoutError("runtime probe deadline exceeded")
        return response.json()


def process_tree_rss(root_pid: int, excluded_pids: set[int] | frozenset[int] = frozenset()) -> int | None:
    """Sum RSS for a verified root process and descendants, excluding Fleet-owned PIDs."""
    import os
    import subprocess

    if os.name != "posix" or root_pid <= 0:
        return None
    try:
        out = subprocess.run(
            ["ps", "-eo", "pid=,ppid=,rss="], capture_output=True, text=True, timeout=5, check=True
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    processes = {}
    for line in out.splitlines():
        fields = line.split()
        if len(fields) == 3 and all(field.isdigit() for field in fields):
            pid, ppid, rss = map(int, fields)
            processes[pid] = (ppid, rss * 1024)
    if root_pid not in processes:
        return None
    included = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, (ppid, _) in processes.items():
            if ppid in included and pid not in included:
                included.add(pid)
                changed = True
    return sum(processes[pid][1] for pid in included - excluded_pids)
