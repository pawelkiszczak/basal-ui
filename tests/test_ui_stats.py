import json
import threading
import time

import pytest

from basal_ui.stats import Stats


@pytest.fixture
def stats(tmp_path):
    s = Stats(tmp_path / "stats.json")
    yield s
    s.close()


@pytest.fixture(autouse=True)
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))


def test_best_answer_probability_per_question_type(stats):
    stats.record(
        "m",
        {
            "answers": {
                "c": {"type": "choice", "choice": "a", "confidence": 0.8},
                "n": {"type": "noul", "noul": 0.1, "confidence": 0.9},
                "s": {"type": "multi", "selected": ["x"], "set_confidence": 0.5},
                "t": {"type": "tool", "tool": "search", "probabilities": {"search": 0.6, "none": 0.4}},
                "u": {"type": "choice", "choice": "b"},
            },
            "usage": {"input_tokens": 30},
        },
        10.0,
    )
    stats.record("m", {"answers": [{"type": "predicate", "name": "p", "probability": 0.2}]}, 20.0)
    m = stats.snapshot()["models"]["m"]
    assert (m["requests"], m["questions"], m["errors"], m["input_tokens"]) == (2, 6, 0, 30)
    assert m["avg_best_prob"] == pytest.approx((0.8 + 0.9 + 0.5 + 0.6 + 0.8) / 5)
    assert m["by_type"]["choice"] == {"questions": 2, "avg_best_prob": pytest.approx(0.8)}  # the answer without one
    assert m["by_type"]["predicate"]["avg_best_prob"] == pytest.approx(0.8)
    assert m["avg_latency_ms"] == 15.0


def test_errors_and_latency_percentiles(stats):
    for ms in range(1, 101):
        stats.record("m", {"answers": {}}, float(ms))
    stats.record("m", {"error": "bad request"}, 1.0, ok=False)
    m = stats.snapshot()["models"]["m"]
    assert (m["requests"], m["errors"], m["avg_best_prob"]) == (101, 1, None)
    assert (m["p50_ms"], m["p95_ms"]) == (50.0, 95.0)


def test_persistence_round_trip_and_reset_of_one_model(tmp_path, stats):
    stats.record("a", {"answers": {"q": {"type": "noul", "confidence": 0.7}}}, 5.0)
    stats.record("b", {"answers": {}}, 5.0)
    stats.add_benchmark("a", {"p50_ms": 1.0})
    stats.flush()
    again = Stats(tmp_path / "stats.json")
    assert again.snapshot() == stats.snapshot()
    again.reset("a")
    assert list(again.snapshot()["models"]) == ["b"]
    assert again.snapshot()["benchmarks"] == {"a": [{"p50_ms": 1.0}]}  # benchmark results are not statistics
    again.close()
    assert list(Stats(tmp_path / "stats.json").snapshot()["models"]) == ["b"]


def test_benchmark_results_keep_the_last_twenty(stats):
    for i in range(22):
        stats.add_benchmark("a", {"run": i})
    assert [r["run"] for r in stats.snapshot()["benchmarks"]["a"]] == list(range(2, 22))


def test_corrupt_file_starts_empty(tmp_path):
    (tmp_path / "stats.json").write_text("{not json")
    s = Stats(tmp_path / "stats.json")
    assert s.snapshot() == {"models": {}, "benchmarks": {}}
    s.close()


def test_two_instances_on_one_file_add_up(tmp_path):
    a, b = Stats(tmp_path / "stats.json"), Stats(tmp_path / "stats.json")
    a.record("m", {"answers": {"q": {"confidence": 0.5}}}, 10.0)
    b.record("m", {"answers": {"q": {"confidence": 1.0}}}, 30.0)
    b.record("n", {"answers": {}}, 1.0)
    a.flush()
    b.flush()
    a.reset("n")  # removes n in the file, keeps m's merged counts
    b.add_benchmark("m", {"p50_ms": 1.0})
    merged = Stats(tmp_path / "stats.json").snapshot()
    assert merged["models"]["m"]["requests"] == 2 and merged["models"]["m"]["avg_best_prob"] == 0.75
    assert "n" not in merged["models"] and merged["benchmarks"] == {"m": [{"p50_ms": 1.0}]}
    for s in (a, b):
        s.close()


def test_record_during_reset_remains_in_delta_and_is_not_lost(stats, monkeypatch):
    from basal_ui import settings

    write = settings._write_private
    started = threading.Event()
    workers = []

    def record_before_persist(path, text):
        if not workers:

            def record():
                started.set()
                stats.record("m", {"answers": {}}, 2.0, endpoint="/api/decide")

            worker = threading.Thread(target=record)
            workers.append(worker)
            worker.start()
            assert started.wait(1)
        write(path, text)

    monkeypatch.setattr(settings, "_write_private", record_before_persist)
    stats.reset()
    workers[0].join(timeout=1)
    assert not workers[0].is_alive()
    assert stats.snapshot()["models"]["m"]["requests"] == 1
    stats.flush()
    assert stats.snapshot()["models"]["m"]["requests"] == 1
    assert stats.traffic_snapshot(15)["requests"] == 1


def test_snapshot_reads_another_instances_flushed_counters(tmp_path):
    a, b = Stats(tmp_path / "shared.json"), Stats(tmp_path / "shared.json")
    b.record("m", {"answers": {}}, 1.0)
    b.flush()
    assert a.snapshot()["models"]["m"]["requests"] == 1
    a.close()
    b.close()


def test_reset_discards_another_instances_unflushed_counts(tmp_path):
    a, b = Stats(tmp_path / "shared.json"), Stats(tmp_path / "shared.json")
    try:
        b.record("m", {"answers": {}}, 1.0)  # counted before the reset, not flushed yet
        time.sleep(0.01)
        a.reset()
        assert b.snapshot()["models"] == {}
        b.flush()
        assert Stats(tmp_path / "shared.json").snapshot()["models"] == {}
        time.sleep(0.01)
        b.record("m", {"answers": {}}, 1.0)  # after the reset: counted
        b.flush()
        assert Stats(tmp_path / "shared.json").snapshot()["models"]["m"]["requests"] == 1
    finally:
        a.close()
        b.close()


@pytest.mark.parametrize("model_id", [None, "m"])
def test_records_after_another_instances_reset_do_not_share_the_old_delta(tmp_path, model_id):
    a, b = Stats(tmp_path / "shared.json"), Stats(tmp_path / "shared.json")
    try:
        b.record("m", {"answers": {"q": {"type": "choice", "confidence": 0.2}}, "usage": {"input_tokens": 7}}, 10)
        b.record("n", {"answers": {}}, 3)
        a.reset(model_id)
        b.record("m", {"answers": {"q": {"type": "choice", "confidence": 0.8}}, "usage": {"input_tokens": 11}}, 20)
        current = b.snapshot()["models"]
        assert current["m"]["requests"] == 1
        assert current["m"]["questions"] == 1
        assert current["m"]["input_tokens"] == 11
        assert current["m"]["avg_latency_ms"] == 20
        assert current["m"]["avg_best_prob"] == 0.8
        assert ("n" in current) == (model_id == "m")
        b.flush()
        assert a.snapshot()["models"] == current
    finally:
        a.close()
        b.close()


def test_process_traffic_aggregates_weighted_latency_and_bounded_samples(stats):
    for latency in range(1, 102):
        stats.record(
            "m",
            {"usage": {"input_tokens": 2}},
            float(latency),
            ok=latency != 101,
            endpoint="/api/decide",
        )
    traffic = stats.traffic_snapshot(15)
    assert (traffic["scope"], traffic["requests"], traffic["errors"], traffic["input_tokens"]) == (
        "process",
        101,
        1,
        202,
    )
    assert traffic["avg_latency_ms"] == 51
    assert traffic["p95_ms"] == 97
    assert traffic["latency_sample_limit"] == 100
    assert traffic["endpoints"][0]["endpoint"] == "/api/decide"
    assert traffic["endpoints"][0]["method"] == "POST"
    assert traffic["endpoints"][0]["p95_ms"] == 97
    assert set(stats.snapshot()) == {"models", "benchmarks"}


def test_traffic_partial_observation_expiry_and_empty_windows(monkeypatch):
    from basal_ui.traffic import Traffic

    monkeypatch.setattr("basal_ui.traffic.time.time", lambda: 1000.0)
    traffic = Traffic()
    empty = traffic.snapshot(15, now=1030)
    assert empty["requests"] == 0 and empty["p95_ms"] is None
    assert empty["requests_per_s"] == 0
    assert all(bucket["start"] + empty["bucket_seconds"] > empty["observed_since"] for bucket in empty["series"])
    traffic.record("m", "/api/decide", "POST", 12, 200, {"usage": {"input_tokens": 4}}, now=1010)
    partial = traffic.snapshot(15, now=1030)
    assert partial["requests"] == 1 and partial["input_tokens"] == 4
    assert partial["requests_per_s"] == pytest.approx(1 / 30)
    assert partial["input_tokens_per_s"] == pytest.approx(4 / 30)
    expired = traffic.snapshot(60, now=5000)
    assert expired["requests"] == 0 and expired["avg_latency_ms"] is None
    assert expired["p95_ms"] is None
    assert len(expired["series"]) <= 60


def test_stats_resets_clear_only_matching_process_traffic(stats):
    stats.record("a", {"usage": {"input_tokens": 1}}, 10, endpoint="/api/decide")
    stats.record("b", {"usage": {"input_tokens": 2}}, 20, endpoint="/v1/systemone")
    assert stats.traffic_snapshot(15)["requests"] == 2
    stats.reset("a")
    local = stats.traffic_snapshot(15)
    assert (local["requests"], local["input_tokens"]) == (1, 2)
    assert local["endpoints"][0]["endpoint"] == "/v1/systemone"
    assert set(stats.snapshot()["models"]) == {"b"}
    stats.reset()
    reset = stats.traffic_snapshot(15)
    assert reset["requests"] == 0 and reset["p95_ms"] is None
    assert reset["requests_per_s"] == 0


def test_dated_traffic_persists_across_restart_without_filling_gaps(tmp_path, monkeypatch):
    from basal_ui.stats import Stats

    now = [1000.0]
    monkeypatch.setattr("basal_ui.stats.time.time", lambda: now[0])
    path = tmp_path / "stats.json"
    first = Stats(path)
    first.record("m", {"usage": {"input_tokens": 4}}, 10, endpoint="/api/decide")
    now[0] = 1240.0
    first.record("m", {"usage": {"input_tokens": 5}}, 30, ok=False, endpoint="/api/decide")
    first.flush()
    first.close()

    restarted = Stats(path)
    try:
        history = restarted.traffic_history("all")
        usage = restarted.snapshot()["models"]["m"]
        assert history["collection_started"] == 1000.0
        assert [row["start"] for row in history["series"]] == [960, 1200]
        assert [row["requests"] for row in history["series"]] == [1, 1]
        assert [row["errors"] for row in history["series"]] == [0, 1]
        assert history["lifetime"] == {"requests": 2, "errors": 1, "input_tokens": 4, "avg_latency_ms": 20.0}
        assert (usage["requests"], usage["errors"], usage["input_tokens"]) == (2, 1, 4)
    finally:
        restarted.close()


def test_dated_traffic_rolls_up_to_hour_and_day(stats, monkeypatch):
    now = 200 * 86400 + 12 * 3600
    current_time = [now]
    monkeypatch.setattr("basal_ui.stats.time.time", lambda: current_time[0])
    events = (
        (now - 91 * 86400 - 120, 100, 30),
        (now - 91 * 86400, 200, 90),
        (now - 25 * 3600 + 60, 1, 10),
        (now - 25 * 3600 + 300, 3, 50),
    )
    for timestamp, tokens, latency in events:
        current_time[0] = timestamp
        stats.record("m", {"usage": {"input_tokens": tokens}}, latency)

    current_time[0] = now
    stats.flush()
    history = json.loads(stats.path.read_text())["traffic_history"]
    buckets = history["buckets"]
    hour_start = (now - 25 * 3600 + 60) // 3600 * 3600
    day_start = (now - 91 * 86400) // 86400 * 86400
    hourly = buckets[f"h{hour_start}"]["m"]
    daily = buckets[f"d{day_start}"]["m"]

    assert set(buckets) == {f"h{hour_start}", f"d{day_start}"}
    assert (hourly["requests"], hourly["input_tokens"], hourly["latency_ms_sum"], hourly["latency_ms_max"]) == (
        2,
        4,
        60.0,
        50.0,
    )
    assert (daily["requests"], daily["input_tokens"], daily["latency_ms_sum"], daily["latency_ms_max"]) == (
        2,
        300,
        120.0,
        90.0,
    )
    assert "first_used" not in hourly and "first_used" not in daily


def test_reset_pending_model_removes_empty_history_bucket(stats, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("basal_ui.stats.time.time", lambda: now[0])
    stats.record("m", {"usage": {"input_tokens": 4}}, 10, endpoint="/api/decide")
    stats.reset("m")

    assert stats.traffic_delta == {}
    assert stats.traffic_history("all")["series"] == []
    stats.flush()


def test_history_ignores_other_process_pending_rows_after_reset(tmp_path, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("basal_ui.stats.time.time", lambda: now[0])
    a, b = Stats(tmp_path / "shared.json"), Stats(tmp_path / "shared.json")
    try:
        b.record("m", {"usage": {"input_tokens": 4}}, 10, endpoint="/api/decide")
        now[0] = 1001.0
        a.reset("m")
        now[0] = 1002.0
        b.record("m", {"usage": {"input_tokens": 7}}, 20, endpoint="/api/decide")

        history = b.traffic_history("all")
        assert sum(row["requests"] for row in history["series"]) == 1
        assert sum(row["input_tokens"] for row in history["series"]) == 7
        assert history["lifetime"]["requests"] == 1
        assert history["lifetime"]["input_tokens"] == 7
        b.flush()
        assert a.traffic_history("all") == history
    finally:
        a.close()
        b.close()


def test_collection_started_moves_back_to_earlier_flushed_decision(tmp_path, monkeypatch):
    now = [1200.0]
    monkeypatch.setattr("basal_ui.stats.time.time", lambda: now[0])
    a, b = Stats(tmp_path / "shared.json"), Stats(tmp_path / "shared.json")
    try:
        a.record("a", {"usage": {}}, 1)
        a.flush()
        now[0] = 1000.0
        b.record("b", {"usage": {}}, 1)
        b.flush()

        assert a.traffic_history("all")["collection_started"] == 1000.0
    finally:
        a.close()
        b.close()


def test_twenty_four_hour_window_reports_hourly_bucket_boundary(stats, monkeypatch):
    now = 200 * 86400 + 12 * 3600 + 30 * 60 + 30
    current_time = [now - 24 * 3600 - 10 * 60]
    monkeypatch.setattr("basal_ui.stats.time.time", lambda: current_time[0])
    stats.record("m", {"usage": {"input_tokens": 8}}, 5)
    current_time[0] = now
    stats.flush()

    history = stats.traffic_history("24h")
    assert history["series"][0]["bucket_seconds"] == 3600
    assert history["window_start"] == history["series"][0]["start"]
    assert history["series"][0]["requests"] == 1


def test_history_reads_apply_aging_without_a_new_decision(stats, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr("basal_ui.stats.time.time", lambda: now[0])
    stats.record("m", {"usage": {"input_tokens": 8}}, 5)
    stats.flush()
    now[0] += 25 * 3600

    history = stats.traffic_history("all")
    assert history["series"][0]["bucket_seconds"] == 3600


def test_failed_flush_rolls_up_traffic_backlog(stats, monkeypatch):
    from basal_ui import settings

    now = [1000.0]
    monkeypatch.setattr("basal_ui.stats.time.time", lambda: now[0])
    monkeypatch.setattr("basal_ui.stats.HISTORY_MINUTE_S", 120)

    def fail_write(*_args):
        raise OSError("disk full")

    with monkeypatch.context() as patch:
        patch.setattr(settings, "_write_private", fail_write)
        for _ in range(8):
            stats.record("m", {"usage": {"input_tokens": 1}}, 1)
            with pytest.raises(OSError, match="disk full"):
                stats.flush()
            now[0] += 60

    assert len(stats.traffic_delta) <= 4
