"""Process-local rolling telemetry for recorded Decisions; no request data or persistence."""

import threading
import time

BUCKET_SECONDS = 60
BUCKETS = 60
LATENCY_SAMPLE_LIMIT = 100


class Traffic:
    """Keep at most 60 minute buckets, with bounded latency samples per endpoint and bucket."""

    def __init__(self):
        self.lock = threading.Lock()
        self.observed_since = time.time()
        self.buckets = {}

    @staticmethod
    def _entry():
        return {"requests": 0, "errors": 0, "input_tokens": 0, "latency_ms_sum": 0.0, "samples": []}

    def _prune(self, now):
        oldest = int(now // BUCKET_SECONDS - (BUCKETS - 1)) * BUCKET_SECONDS
        newest = int(now // BUCKET_SECONDS) * BUCKET_SECONDS
        for start in tuple(self.buckets):
            if start < oldest or start > newest:
                del self.buckets[start]

    def record(self, model_id, endpoint, method, latency_ms, status, data, now=None):
        now = time.time() if now is None else now
        start = int(now // BUCKET_SECONDS) * BUCKET_SECONDS
        usage = data.get("usage") if isinstance(data, dict) else None
        tokens = usage.get("input_tokens", 0) if isinstance(usage, dict) else 0
        tokens = tokens if isinstance(tokens, int) and not isinstance(tokens, bool) and tokens > 0 else 0
        with self.lock:
            self._prune(now)
            models = self.buckets.setdefault(start, {})
            endpoints = models.setdefault(model_id, {})
            entry = endpoints.setdefault((endpoint, method), self._entry())
            entry["requests"] += 1
            entry["errors"] += status >= 400
            entry["input_tokens"] += tokens
            entry["latency_ms_sum"] += latency_ms
            entry["samples"] = [*entry["samples"], latency_ms][-LATENCY_SAMPLE_LIMIT:]

    def reset(self, model_id=None, now=None):
        now = time.time() if now is None else now
        with self.lock:
            if model_id is None:
                self.buckets.clear()
                self.observed_since = now
            else:
                for start, models in tuple(self.buckets.items()):
                    models.pop(model_id, None)
                    if not models:
                        del self.buckets[start]

    def snapshot(self, minutes, now=None):
        now = time.time() if now is None else now
        window_start = now - minutes * 60
        with self.lock:
            self._prune(now)
            observed_since = self.observed_since
            buckets = {
                start: {
                    model: {key: dict(value, samples=list(value["samples"])) for key, value in entries.items()}
                    for model, entries in models.items()
                }
                for start, models in self.buckets.items()
            }
        first = max(
            int(window_start // BUCKET_SECONDS) * BUCKET_SECONDS,
            int(now // BUCKET_SECONDS - (BUCKETS - 1)) * BUCKET_SECONDS,
            int(observed_since // BUCKET_SECONDS) * BUCKET_SECONDS,
        )
        last = int(now // BUCKET_SECONDS) * BUCKET_SECONDS
        elapsed = max(0.0, now - max(window_start, observed_since, first))
        totals = self._entry()
        endpoint_totals = {}
        series = []
        for start in range(first, last + 1, BUCKET_SECONDS):
            bucket = self._entry()
            for models in buckets.get(start, {}).values():
                for (endpoint, method), entry in models.items():
                    for key in ("requests", "errors", "input_tokens", "latency_ms_sum"):
                        bucket[key] += entry[key]
                        totals[key] += entry[key]
                    totals["samples"] = [*totals["samples"], *entry["samples"]][-(LATENCY_SAMPLE_LIMIT * BUCKETS) :]
                    group = endpoint_totals.setdefault((endpoint, method), self._entry())
                    for key in ("requests", "errors", "input_tokens", "latency_ms_sum"):
                        group[key] += entry[key]
                    group["samples"] = [*group["samples"], *entry["samples"]][-LATENCY_SAMPLE_LIMIT * BUCKETS :]
            bucket_start = max(start, window_start, observed_since)
            bucket_end = min(start + BUCKET_SECONDS, now)
            bucket_elapsed = max(0.0, bucket_end - bucket_start)
            if start + BUCKET_SECONDS > window_start and start <= now:
                series.append(
                    {
                        "start": start,
                        "requests": bucket["requests"],
                        "errors": bucket["errors"],
                        "input_tokens": bucket["input_tokens"],
                        "requests_per_s": bucket["requests"] / bucket_elapsed if bucket_elapsed else 0.0,
                        "input_tokens_per_s": bucket["input_tokens"] / bucket_elapsed if bucket_elapsed else 0.0,
                    }
                )
        endpoints = []
        for (endpoint, method), entry in sorted(endpoint_totals.items()):
            endpoints.append(
                {
                    "endpoint": endpoint,
                    "method": method,
                    "requests": entry["requests"],
                    "errors": entry["errors"],
                    "input_tokens": entry["input_tokens"],
                    "requests_per_s": entry["requests"] / elapsed if elapsed else 0.0,
                    "input_tokens_per_s": entry["input_tokens"] / elapsed if elapsed else 0.0,
                    "avg_latency_ms": entry["latency_ms_sum"] / entry["requests"] if entry["requests"] else None,
                    "p95_ms": self._percentile(entry["samples"], 0.95),
                }
            )
        return {
            "scope": "process",
            "window_seconds": minutes * 60,
            "bucket_seconds": BUCKET_SECONDS,
            "window_start": window_start,
            "window_end": now,
            "observed_since": observed_since,
            "requests": totals["requests"],
            "errors": totals["errors"],
            "input_tokens": totals["input_tokens"],
            "requests_per_s": totals["requests"] / elapsed if elapsed else 0.0,
            "input_tokens_per_s": totals["input_tokens"] / elapsed if elapsed else 0.0,
            "avg_latency_ms": totals["latency_ms_sum"] / totals["requests"] if totals["requests"] else None,
            "p95_ms": self._percentile(totals["samples"], 0.95),
            "latency_sample_limit": LATENCY_SAMPLE_LIMIT,
            "series": series,
            "endpoints": endpoints,
        }

    @staticmethod
    def _percentile(samples, q):
        if not samples:
            return None
        return sorted(samples)[min(len(samples) - 1, int(q * len(samples)))]
