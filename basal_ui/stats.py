"""Per-model Decision statistics, benchmarks, and dated traffic and memory history in `<settings stem>.stats.json`.
Several basal-ui processes sharing one settings file merge updates under its lock."""

import contextlib
import json
import threading
import time
from pathlib import Path

from .traffic import Traffic

SAMPLES = 1000  # latencies kept per model for the percentiles
BENCHMARKS = 20  # results kept per model (benchmarks, load tests, determinism checks, mode comparisons)
FLUSH_S = 5
HISTORY_MINUTE_S = 24 * 60 * 60
HISTORY_HOUR_S = 90 * 24 * 60 * 60
HISTORY_RETENTION_S = 5 * 365 * 24 * 60 * 60


def answer_items(answers) -> list[tuple[str, dict]]:
    """(name, answer) pairs of /v1/systemone answers (a dict by name) or /v1/decisions answers (a list)."""
    if isinstance(answers, dict):
        return [(name, a) for name, a in answers.items() if isinstance(a, dict)]
    if isinstance(answers, list):
        return [(str(a.get("name", i)), a) for i, a in enumerate(answers) if isinstance(a, dict)]
    return []


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def best_probability(answer: dict):
    """The probability of the returned answer: confidence (choice, noul, score, act), set_confidence (multi), the
    larger side of a predicate's probability, else the largest probability (tool); None when there is none."""
    for key in ("confidence", "set_confidence"):
        if _number(answer.get(key)):
            return float(answer[key])
    if _number(answer.get("probability")):
        return max(float(answer["probability"]), 1 - float(answer["probability"]))
    probabilities = answer.get("probabilities")
    if isinstance(probabilities, dict):
        values = [v for v in probabilities.values() if _number(v)]
        if values:
            return float(max(values))
    return None


def _percentile(samples, q):
    if not samples:
        return None
    ordered = sorted(samples)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def _empty():
    return {
        "requests": 0,
        "questions": 0,
        "errors": 0,
        "input_tokens": 0,
        "latency_ms_sum": 0.0,
        "latency_samples": [],
        "best_prob_sum": 0.0,
        "best_prob_n": 0,
        "first_used": None,
        "last_used": None,
        "by_type": {},
    }


def _read(path: Path) -> dict:
    """The stats file; reset_at holds when it was last reset: "*" (every model) or a model id -> unix time."""
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded.get("models"), dict) and isinstance(loaded.get("benchmarks"), dict):
            if not isinstance(loaded.get("reset_at"), dict):
                loaded["reset_at"] = {}
            _history(loaded)
            _memory_history(loaded)
            return loaded
    except (OSError, ValueError, AttributeError):
        pass
    return {
        "models": {},
        "benchmarks": {},
        "reset_at": {},
        "traffic_history": {"collection_started": None, "buckets": {}},
        "memory_history": {"collection_started": None, "buckets": {}},
    }


def _history(data):
    value = data.get("traffic_history")
    if not isinstance(value, dict) or not isinstance(value.get("buckets"), dict):
        value = {"collection_started": None, "buckets": {}}
        data["traffic_history"] = value
    return value


def _bucket_start(key) -> int:
    value = str(key)
    return int(value[1:] if value[:1] in ("h", "d") else value)


def _rollup_buckets(buckets: dict, now: float, merge) -> None:
    retention_cutoff, hourly_cutoff, minute_cutoff = (
        now - HISTORY_RETENTION_S,
        now - HISTORY_HOUR_S,
        now - HISTORY_MINUTE_S,
    )
    for key in sorted(tuple(buckets), key=_bucket_start):
        start = _bucket_start(key)
        if start < retention_cutoff:
            del buckets[key]
            continue
        if start < hourly_cutoff and key[:1] != "d":
            target = buckets.setdefault(f"d{start // 86400 * 86400}", {})
        elif start < minute_cutoff and key[:1] not in ("h", "d"):
            target = buckets.setdefault(f"h{start // 3600 * 3600}", {})
        else:
            continue
        merge(target, buckets[key])
        del buckets[key]


def _traffic_bucket(target, source):
    for model_id, row in source.items():
        entry = target.setdefault(model_id, _traffic_row(row.get("first_used")))
        entry["first_used"] = min(filter(None, (entry.get("first_used"), row.get("first_used"))), default=None)
        _add_traffic(entry, row)


def _memory_row():
    return {"total_bytes": 0, "max_bytes": 0, "samples": 0}


def _add_memory(target, row):
    target["total_bytes"] += row["total_bytes"]
    target["max_bytes"] = max(target["max_bytes"], row["max_bytes"])
    target["samples"] += row["samples"]


def _memory_bucket(target, source):
    for name, row in source.items():
        _add_memory(target.setdefault(name, _memory_row()), row)


def _memory_history(data):
    value = data.get("memory_history")
    if not isinstance(value, dict) or not isinstance(value.get("buckets"), dict):
        value = {"collection_started": None, "buckets": {}}
        data["memory_history"] = value
    return value


def _memory_measurement(row, value):
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        row["total_bytes"] += value
        row["max_bytes"] = max(row["max_bytes"], value)
        row["samples"] += 1


def _recorded_memory(sample):
    time_s = sample.get("time", time.time())
    start = int(time_s // 60) * 60
    rows = {}
    host = sample.get("host") or {}
    _memory_measurement(rows.setdefault("host", _memory_row()), host.get("ram_used"))
    _memory_measurement(rows.setdefault("other", _memory_row()), sample.get("other"))
    for runtime in sample.get("runtimes", ()):  # a measured total counts whatever the runtime's API status
        _memory_measurement(
            rows.setdefault(f"runtime:{runtime['runtime']}", _memory_row()), runtime.get("process_bytes")
        )
    return start, time_s, rows


def _merge_traffic(data, delta):
    history = _history(data)
    if delta and history["collection_started"] is None:
        history["collection_started"] = min(row["first_used"] for models in delta.values() for row in models.values())
    reset_at, buckets = data["reset_at"], history["buckets"]
    for start, models in delta.items():
        target = buckets.setdefault(str(start), {})
        for model_id, row in models.items():
            if not _cleared(reset_at, model_id, row):
                _traffic_bucket(target, {model_id: row})
    now = time.time()
    _rollup_buckets(buckets, now, _traffic_bucket)
    for models in buckets.values():
        for row in models.values():
            row.pop("first_used", None)


def _merge_memory(data, delta):
    history = _memory_history(data)
    if delta:
        history["collection_started"] = min(
            filter(None, (history["collection_started"], min(sample["time"] for sample in delta.values()))),
            default=None,
        )
    buckets = history["buckets"]
    for start, sample in delta.items():
        _memory_bucket(buckets.setdefault(str(start), {}), sample["rows"])
    _rollup_buckets(buckets, time.time(), _memory_bucket)


def _traffic_row(first_used):
    return {
        "requests": 0,
        "errors": 0,
        "input_tokens": 0,
        "latency_ms_sum": 0.0,
        "latency_ms_max": 0.0,
        "first_used": first_used,
    }


def _add_traffic(target, row):
    for key in ("requests", "errors", "input_tokens", "latency_ms_sum"):
        target[key] += row[key]
    target["latency_ms_max"] = max(target["latency_ms_max"], row["latency_ms_max"])


def _age_traffic(buckets, now, keep_first_used=False):
    retention_cutoff = now - HISTORY_RETENTION_S
    hourly_cutoff = now - HISTORY_HOUR_S
    minute_cutoff = now - HISTORY_MINUTE_S
    for key in sorted(tuple(buckets), key=_bucket_start):
        start = _bucket_start(key)
        prefix = str(key)[:1]
        bucket_seconds = {"h": 3600, "d": 86400}.get(prefix, 60)
        if start + bucket_seconds <= retention_cutoff:
            del buckets[key]
            continue
        if start + bucket_seconds <= hourly_cutoff and prefix != "d":
            target = buckets.setdefault(f"d{start // 86400 * 86400}", {})
        elif start + bucket_seconds <= minute_cutoff and prefix not in ("h", "d"):
            target = buckets.setdefault(f"h{start // 3600 * 3600}", {})
        else:
            continue
        for model_id, row in buckets[key].items():
            entry = target.setdefault(model_id, _traffic_row(row.get("first_used")))
            _add_traffic(entry, row)
            if row.get("first_used") is not None:
                entry["first_used"] = min(filter(None, (entry["first_used"], row["first_used"])))
        del buckets[key]
    for key in tuple(buckets):
        if not buckets[key]:
            del buckets[key]
        elif not keep_first_used:
            for row in buckets[key].values():
                row.pop("first_used", None)


def _merge_traffic(data, delta, now=None):
    history = _history(data)
    reset_at = data["reset_at"]
    eligible = [
        row["first_used"]
        for models in delta.values()
        for model_id, row in models.items()
        if not _cleared(reset_at, model_id, row)
    ]
    global_reset = reset_at.get("*", 0)
    if history["collection_started"] is not None:
        eligible.append(max(history["collection_started"], global_reset))
    if eligible:
        history["collection_started"] = min(eligible)
    buckets = history["buckets"]
    for start, models in delta.items():
        target = buckets.setdefault(str(start), {})
        for model_id, row in models.items():
            if not _cleared(reset_at, model_id, row):
                entry = target.setdefault(model_id, _traffic_row(row["first_used"]))
                _add_traffic(entry, row)
                if entry.get("first_used") is not None and row.get("first_used") is not None:
                    entry["first_used"] = min(entry["first_used"], row["first_used"])
    _age_traffic(buckets, time.time() if now is None else now)


def _cleared(reset_at: dict, model_id: str, entry: dict) -> bool:
    """True when a delta entry started before the file's last reset of that model (or of everything): another
    process reset the counts this process had not flushed yet."""
    return (entry["first_used"] or 0) < max(reset_at.get("*", 0), reset_at.get(model_id, 0))


def _merge(into: dict, delta: dict) -> None:
    """Add the counters of delta (one model's record) to into."""
    for key in ("requests", "questions", "errors", "input_tokens", "latency_ms_sum", "best_prob_sum", "best_prob_n"):
        into[key] += delta[key]
    into["latency_samples"] = [*into["latency_samples"], *delta["latency_samples"]][-SAMPLES:]
    into["first_used"] = min(filter(None, (into["first_used"], delta["first_used"])), default=None)
    into["last_used"] = max(filter(None, (into["last_used"], delta["last_used"])), default=None)
    for kind, t in delta["by_type"].items():
        target = into["by_type"].setdefault(kind, {"questions": 0, "best_prob_sum": 0.0, "best_prob_n": 0})
        for key in target:
            target[key] += t[key]


class Stats:
    """Counts go into `delta` and are merged into the file under the settings lock at every flush, so two basal-ui
    processes on one settings file add up instead of overwriting each other; `data` mirrors the file."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.write_lock = threading.Lock()
        self.data = _read(self.path)
        self.delta: dict = {}
        self.traffic_delta: dict = {}
        self.memory_delta: dict = {}
        self.traffic = Traffic()
        self._stop = threading.Event()
        self._flusher = threading.Thread(target=self._flush_loop, daemon=True)
        self._flusher.start()

    def record(
        self, model_id: str, response_json, latency_ms: float, ok: bool = True, endpoint=None, method="POST"
    ) -> None:
        from . import settings

        @settings._locked
        def record():
            # Observe resets and stamp this Decision under the same inter-process lock as reset(). A pending delta
            # from before a reset must be discarded before adding a later Decision, not together with it at flush.
            reset_at = _read(self.path)["reset_at"]
            with self.lock:
                now = time.time()
                start = int(now // 60) * 60
                if endpoint is not None:
                    self.traffic.record(
                        model_id, endpoint, method, latency_ms, 200 if ok else 500, response_json, now=now
                    )
                rows = self.traffic_delta.setdefault(start, {})
                row = rows.get(model_id)
                if row is None or _cleared(reset_at, model_id, row):
                    row = rows[model_id] = _traffic_row(now)
                row["first_used"] = min(row["first_used"], now)
                usage = response_json.get("usage") if isinstance(response_json, dict) else None
                tokens = usage.get("input_tokens", 0) if isinstance(usage, dict) else 0
                _add_traffic(
                    row,
                    {
                        "requests": 1,
                        "errors": int(not ok),
                        "input_tokens": (
                            tokens if isinstance(tokens, int) and not isinstance(tokens, bool) and tokens > 0 else 0
                        ),
                        "latency_ms_sum": latency_ms,
                        "latency_ms_max": latency_ms,
                    },
                )
                entry = self.delta.get(model_id)
                if entry is None or _cleared(reset_at, model_id, entry):
                    entry = self.delta[model_id] = _empty()
                entry["first_used"] = entry["first_used"] or now
                entry["last_used"] = now
                entry["requests"] += 1
                entry["latency_ms_sum"] += latency_ms
                entry["latency_samples"] = [*entry["latency_samples"], round(latency_ms, 2)][-SAMPLES:]
                if not ok or not isinstance(response_json, dict):
                    entry["errors"] += 1
                    return
                usage = response_json.get("usage")
                if isinstance(usage, dict) and isinstance(usage.get("input_tokens"), int):
                    entry["input_tokens"] += usage["input_tokens"]
                for _name, answer in answer_items(response_json.get("answers")):
                    kind = answer.get("type") if isinstance(answer.get("type"), str) else "other"
                    by_type = entry["by_type"].setdefault(
                        kind, {"questions": 0, "best_prob_sum": 0.0, "best_prob_n": 0}
                    )
                    entry["questions"] += 1
                    by_type["questions"] += 1
                    p = best_probability(answer)
                    if p is not None:
                        for target in (entry, by_type):
                            target["best_prob_sum"] += p
                            target["best_prob_n"] += 1

        record()

    def record_memory_sample(self, sample: dict) -> None:
        start, time_s, rows = _recorded_memory(sample)
        with self.lock:
            bucket = self.memory_delta.setdefault(start, {"time": time_s, "rows": {}})
            bucket["time"] = min(bucket["time"], time_s)
            _memory_bucket(bucket["rows"], rows)

    def _update_file(self, change) -> None:
        """Under the settings lock: merge pending counters and histories, apply change, then write atomically."""
        from . import settings

        def go():
            with self.lock:
                delta, self.delta = self.delta, {}
                traffic_delta, self.traffic_delta = self.traffic_delta, {}
                memory_delta, self.memory_delta = self.memory_delta, {}
            data = _read(self.path)
            try:
                for model_id, entry in delta.items():
                    if not _cleared(data["reset_at"], model_id, entry):
                        _merge(data["models"].setdefault(model_id, _empty()), entry)
                _merge_traffic(data, traffic_delta)
                _merge_memory(data, memory_delta)
                change(data)
                settings._write_private(self.path, json.dumps(data))
            except BaseException:
                with self.lock:
                    for model_id, entry in delta.items():
                        _merge(self.delta.setdefault(model_id, _empty()), entry)
                    for start, models in traffic_delta.items():
                        target = self.traffic_delta.setdefault(start, {})
                        for model_id, row in models.items():
                            current = target.get(model_id)
                            if current is None:
                                target[model_id] = row
                            else:
                                current["first_used"] = min(current["first_used"], row["first_used"])
                                _add_traffic(current, row)
                    _age_traffic(self.traffic_delta, time.time(), keep_first_used=True)
                    for start, sample in memory_delta.items():
                        target = self.memory_delta.setdefault(start, {"time": sample["time"], "rows": {}})
                        target["time"] = min(target["time"], sample["time"])
                        _memory_bucket(target["rows"], sample["rows"])
                raise
            with self.lock:
                self.data = data

        with self.write_lock:
            settings._locked(go)()

    def add_benchmark(self, model_id: str, result: dict) -> None:
        def change(data):
            runs = data["benchmarks"].get(model_id, [])
            data["benchmarks"][model_id] = [*runs, result][-BENCHMARKS:]

        self._update_file(change)

    def snapshot(self) -> dict:
        from . import settings

        def read():
            data = _read(self.path)
            with self.lock:
                self.data = data
                merged = json.loads(json.dumps(data))
                for model_id, entry in self.delta.items():
                    if not _cleared(data["reset_at"], model_id, entry):
                        _merge(merged["models"].setdefault(model_id, _empty()), entry)
            return merged

        merged = settings._locked(read)()

        def avg(total, n):
            return total / n if n else None

        models = {}
        for model_id, e in merged["models"].items():
            models[model_id] = {
                **{k: e[k] for k in ("requests", "questions", "errors", "input_tokens", "first_used", "last_used")},
                "avg_latency_ms": avg(e["latency_ms_sum"], e["requests"]),
                "p50_ms": _percentile(e["latency_samples"], 0.5),
                "p95_ms": _percentile(e["latency_samples"], 0.95),
                "avg_best_prob": avg(e["best_prob_sum"], e["best_prob_n"]),
                "by_type": {
                    kind: {"questions": t["questions"], "avg_best_prob": avg(t["best_prob_sum"], t["best_prob_n"])}
                    for kind, t in e["by_type"].items()
                },
            }
        return {"models": models, "benchmarks": merged["benchmarks"]}

    def reset(self, model_id: str | None = None) -> None:
        from . import settings

        def go():
            with self.lock:
                if model_id is None:
                    previous = (self.delta, self.traffic_delta)
                    self.delta, self.traffic_delta = {}, {}
                else:
                    previous_delta = {model_id: self.delta.pop(model_id)} if model_id in self.delta else {}
                    previous_traffic = {}
                    for start, rows in tuple(self.traffic_delta.items()):
                        row = rows.pop(model_id, None)
                        if row is not None:
                            previous_traffic[start] = {model_id: row}
                        if not rows:
                            del self.traffic_delta[start]
                    previous = previous_delta, previous_traffic
                try:
                    data = _read(self.path)
                    if model_id is None:
                        data["models"].clear()
                        now = time.time()
                        history = _history(data)
                        history["buckets"].clear()
                        history["collection_started"] = now
                        data["reset_at"]["*"] = now
                    else:
                        data["models"].pop(model_id, None)
                        buckets = _history(data)["buckets"]
                        for key, models in tuple(buckets.items()):
                            models.pop(model_id, None)
                            if not models:
                                del buckets[key]
                        data["reset_at"][model_id] = time.time()
                    settings._write_private(self.path, json.dumps(data))
                except BaseException:
                    if model_id is None:
                        self.delta, self.traffic_delta = previous
                    else:
                        self.delta.update(previous[0])
                        for start, rows in previous[1].items():
                            self.traffic_delta.setdefault(start, {}).update(rows)
                    raise
                self.data = data
                self.traffic.reset(model_id, now=time.time())

        with self.write_lock:
            settings._locked(go)()

    def traffic_snapshot(self, minutes: int) -> dict:
        return self.traffic.snapshot(minutes)

    def traffic_history(self, time_range: str) -> dict:
        from . import settings

        if time_range not in ("1h", "24h", "7d", "30d", "all"):
            raise ValueError("range must be 1h, 24h, 7d, 30d, or all")
        now = time.time()
        duration = {"1h": 3600, "24h": 86400, "7d": 604800, "30d": 2592000}.get(time_range)

        def read():
            data = _read(self.path)
            with self.lock:
                _merge_traffic(data, self.traffic_delta, now)
                for model_id, entry in self.delta.items():
                    if not _cleared(data["reset_at"], model_id, entry):
                        _merge(data["models"].setdefault(model_id, _empty()), entry)
                self.data = data
            return data

        data = settings._locked(read)()
        history = _history(data)
        collection_started = history["collection_started"]
        window_start = (
            max(collection_started if collection_started is not None else now, now - duration)
            if duration
            else collection_started
        )
        series = []
        for key, models in sorted(history["buckets"].items(), key=lambda item: _bucket_start(item[0])):
            start = _bucket_start(key)
            bucket_seconds = {"h": 3600, "d": 86400}.get(str(key)[:1], 60)
            if window_start is not None and start + bucket_seconds <= window_start:
                continue
            if start > now:
                continue
            rows = list(models.values())
            requests = sum(row["requests"] for row in rows)
            if not requests:
                continue
            latency_sum = sum(row["latency_ms_sum"] for row in rows)
            series.append(
                {
                    "start": start,
                    "bucket_seconds": bucket_seconds,
                    "requests": requests,
                    "errors": sum(row["errors"] for row in rows),
                    "input_tokens": sum(row["input_tokens"] for row in rows),
                    "avg_latency_ms": latency_sum / requests,
                    "max_latency_ms": max(row["latency_ms_max"] for row in rows),
                }
            )
        if duration and series and window_start is not None and series[0]["start"] < window_start:
            window_start = series[0]["start"]
        lifetime = {"requests": 0, "errors": 0, "input_tokens": 0, "latency_ms_sum": 0.0}
        for model in data["models"].values():
            for key in ("requests", "errors", "input_tokens"):
                lifetime[key] += model[key]
            lifetime["latency_ms_sum"] += model["latency_ms_sum"]
        latency_sum = lifetime.pop("latency_ms_sum")
        lifetime["avg_latency_ms"] = latency_sum / lifetime["requests"] if lifetime["requests"] else None
        return {
            "range": time_range,
            "collection_started": collection_started,
            "window_start": window_start,
            "window_end": now,
            "series": series,
            "lifetime": lifetime,
        }

    def memory_history(self, time_range: str) -> dict:
        from . import settings

        if time_range not in ("1h", "24h", "7d", "30d", "all"):
            raise ValueError("range must be 1h, 24h, 7d, 30d, or all")
        now = time.time()
        duration = {"1h": 3600, "24h": 86400, "7d": 604800, "30d": 2592000}.get(time_range)

        def read():
            data = _read(self.path)
            with self.lock:
                delta = json.loads(json.dumps(self.memory_delta))
            return data, delta

        data, delta = settings._locked(read)()
        _merge_memory(data, delta)
        history = _memory_history(data)
        collection_started = history["collection_started"]
        window_start = max(collection_started or now, now - duration) if duration else collection_started
        series = []
        for key, values in sorted(history["buckets"].items(), key=lambda item: _bucket_start(item[0])):
            start = _bucket_start(key)
            bucket_seconds = {"h": 3600, "d": 86400}.get(key[:1], 60)
            if (window_start is not None and start + bucket_seconds < window_start) or start > now:
                continue
            rows = {
                name: {
                    "avg_bytes": row["total_bytes"] / row["samples"],
                    "max_bytes": row["max_bytes"],
                    "samples": row["samples"],
                }
                for name, row in values.items()
                if row.get("samples")
            }
            runtimes = {name.removeprefix("runtime:"): row for name, row in rows.items() if name.startswith("runtime:")}
            if rows:
                series.append(
                    {
                        "start": start,
                        "bucket_seconds": bucket_seconds,
                        "host": rows.get("host"),
                        "other": rows.get("other"),
                        "runtimes": runtimes,
                    }
                )
        return {
            "range": time_range,
            "collection_started": collection_started,
            "window_start": window_start,
            "window_end": now,
            "series": series,
        }

    def flush(self) -> None:
        with self.lock:
            if not self.delta and not self.traffic_delta and not self.memory_delta:
                return
        self._update_file(lambda data: None)

    def _flush_loop(self):
        while not self._stop.wait(FLUSH_S):
            with contextlib.suppress(OSError):  # the delta is kept; the next flush retries
                self.flush()

    def close(self) -> None:
        self._stop.set()
        self.flush()
