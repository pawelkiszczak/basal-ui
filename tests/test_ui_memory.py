import pytest
from fastapi.testclient import TestClient

from basal_ui import memory
from tests.test_ui_app import app_for

HOST = {"ram_total": 100, "ram_used": 90, "unified": False, "gpu": [], "accounting": "resident"}


def _observation(accounting="resident", process_bytes=10, model_bytes=10, **model_fields):
    return {
        "runtime": "sample",
        "label": "Sample runtime",
        "status": "ok",
        "accounting": accounting,
        "process_bytes": process_bytes,
        "models": [
            {
                "id": "sample-model",
                "bytes": model_bytes,
                "pool": "ram",
                "context_bytes": None,
                "cache_bytes": None,
                "cold_cache_bytes": None,
                **model_fields,
            }
        ],
        "note": None,
    }


def _fleet(models=()):
    return type("FleetStub", (), {"memory_snapshot": lambda self: list(models)})()


def test_non_unified_fleet_rss_reports_system_remainder(monkeypatch):
    monkeypatch.setattr(memory, "_host", lambda _: HOST)
    monkeypatch.setattr(memory, "_cache_bytes", lambda _: None)
    fleet = _fleet([{"id": "model", "kind": "basal", "state": "ready", "rss_bytes": 10}])

    result = memory.snapshot(fleet, [], hardware_fn=lambda: None)

    assert result["runtimes"][0]["accounting"] == "resident"
    assert result["runtimes"][0]["process_bytes"] == 10
    assert result["other"] == 80


def test_incompatible_accounting_hides_remainder(monkeypatch):
    monkeypatch.setattr(memory, "_host", lambda _: HOST)

    result = memory.snapshot(_fleet(), [lambda: _observation("api")], hardware_fn=lambda: None)

    assert result["other"] is None


def test_unified_fleet_rss_is_counted_once_and_remainder_is_unavailable(monkeypatch):
    host = {**HOST, "unified": True}
    monkeypatch.setattr(memory, "_host", lambda _: host)
    monkeypatch.setattr(memory, "_cache_bytes", lambda _: None)
    fleet = _fleet([{"id": "model", "kind": "basal", "state": "ready", "rss_bytes": 25}])

    result = memory.snapshot(fleet, [], hardware_fn=lambda: None)

    runtime = result["runtimes"][0]
    assert runtime["process_bytes"] == 25
    assert runtime["models"][0]["bytes"] == 25
    assert runtime["models"][0]["pool"] == "unified"
    assert runtime["accounting"] == "rss_excludes_gpu"
    assert "RSS excludes GPU allocations" in runtime["note"]
    assert result["host"]["gpu"] == []
    assert result["other"] is None


def test_unified_empty_fleet_leaves_all_host_memory_to_other_apps(monkeypatch):
    monkeypatch.setattr(memory, "_host", lambda _: {**HOST, "unified": True})

    result = memory.snapshot(_fleet(), [], hardware_fn=lambda: None)

    assert result["runtimes"][0]["status"] == "empty"
    assert result["runtimes"][0]["accounting"] == "rss_excludes_gpu"
    assert result["other"] == 90  # measured zero needs no shared accounting basis


def test_failed_provider_isolated_and_zero_remains_measured(monkeypatch):
    monkeypatch.setattr(memory, "_host", lambda _: HOST)

    def fail():
        raise RuntimeError("offline")

    measured = lambda: _observation(process_bytes=0, model_bytes=0, cache_bytes=0)
    result = memory.snapshot(_fleet(), [fail, measured], hardware_fn=lambda: None)

    assert result["runtimes"][1]["status"] == "unavailable"
    assert result["runtimes"][2]["process_bytes"] == 0
    assert result["runtimes"][2]["models"][0]["bytes"] == 0
    assert result["runtimes"][2]["models"][0]["cache_bytes"] == 0
    assert result["other"] is None


def test_unknown_component_stays_none_and_disables_remainder(monkeypatch):
    monkeypatch.setattr(memory, "_host", lambda _: HOST)

    result = memory.snapshot(
        _fleet(), [lambda: _observation(process_bytes=None, model_bytes=None)], hardware_fn=lambda: None
    )

    assert result["runtimes"][1]["process_bytes"] is None
    assert result["runtimes"][1]["models"][0]["bytes"] is None
    assert result["runtimes"][1]["models"][0]["context_bytes"] is None
    assert result["runtimes"][1]["models"][0]["cache_bytes"] is None
    assert result["other"] is None


def test_live_route_returns_snapshot_without_changing_memory_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    monkeypatch.setattr(memory, "_host", lambda _: HOST)
    app, _ = app_for(tmp_path, [], memory_providers=[lambda: _observation()])

    with TestClient(app, base_url="http://127.0.0.1") as client:
        live = client.get("/api/memory/live").json()
        history = client.get("/api/memory/history?range=all")
        invalid_history = client.get("/api/memory/history?range=invalid")
        settings = client.get("/api/memory").json()
    assert history.status_code == 200
    assert history.json()["range"] == "all"
    assert history.json()["series"] == []
    assert invalid_history.status_code == 400
    assert set(live) == {"time", "host", "runtimes", "other"}
    assert live["runtimes"][1]["runtime"] == "sample"
    assert live["runtimes"][1]["models"][0]["bytes"] == 10
    assert settings["settings"]["reserve_bytes"] > 0


def test_process_rss_sums_matching_commands_and_reports_absence(monkeypatch):
    out = "  2048 /usr/local/bin/ollama serve\n1024 /usr/local/bin/ollama runner --model x\n  512 /bin/zsh\n"
    monkeypatch.setattr(memory.subprocess, "run", lambda *a, **k: type("R", (), {"stdout": out})())

    assert memory.process_rss(r"\bollama\b") == 3072 * 1024
    assert memory.process_rss(r"\bvllm\b") is None


def test_remainder_subtracts_measured_process_of_runtime_with_api_down(monkeypatch):
    monkeypatch.setattr(memory, "_host", lambda _: HOST)
    down = {**_observation(process_bytes=30), "status": "unreachable", "models": []}

    result = memory.snapshot(_fleet(), [lambda: down], hardware_fn=lambda: None)

    assert result["other"] == 60


def test_failed_provider_makes_remainder_unknown(monkeypatch):
    monkeypatch.setattr(memory, "_host", lambda _: HOST)

    def fail():
        raise RuntimeError("offline")

    assert memory.snapshot(_fleet(), [fail], hardware_fn=lambda: None)["other"] is None


def test_dotted_provider_is_imported_lazily(monkeypatch):
    import sys
    from types import ModuleType

    module = ModuleType("tests.fake_memory_provider")
    module.observe = lambda: _observation()
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(memory, "_host", lambda _: HOST)

    result = memory.snapshot(_fleet(), ["tests.fake_memory_provider.observe"], hardware_fn=lambda: None)

    assert result["runtimes"][1]["runtime"] == "sample"


def test_runtime_providers_import_before_memory_module():
    import subprocess
    import sys

    subprocess.run(
        [
            sys.executable,
            "-c",
            "import basal_ui.runtimes.omlx, basal_ui.runtimes.ollama; import basal_ui.memory; "
            "assert 'basal_ui.runtimes.omlx.observe' in basal_ui.memory.PROVIDERS",
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def test_history_memory_snapshot_skips_per_model_metrics(monkeypatch):
    monkeypatch.setattr(memory, "_host", lambda _: HOST)
    monkeypatch.setattr(memory, "_cache_bytes", lambda _url: (_ for _ in ()).throw(AssertionError("unexpected probe")))
    fleet = _fleet(
        [{"kind": "basal", "state": "ready", "id": "model", "rss_bytes": 10, "base_url": "http://127.0.0.1"}]
    )

    result = memory.snapshot(fleet, [], hardware_fn=lambda: None, include_models=False)

    assert result["runtimes"][0]["process_bytes"] == 10
    assert result["runtimes"][0]["models"] == []


def test_memory_sampler_uses_fixed_sample_deadlines():
    import threading
    import time

    from basal_ui.memory_sampler import MemorySampler

    samples = []
    sampled = threading.Event()

    class StatsStub:
        def record_memory_sample(self, sample):
            samples.append(sample["time"])
            if len(samples) == 3:
                sampled.set()

    def snapshot():
        time.sleep(0.02)
        return {}

    sampler = MemorySampler(StatsStub(), snapshot, interval=0.05)
    sampler.start()
    try:
        assert sampled.wait(1)
    finally:
        sampler.stop()

    assert samples[1] - samples[0] == pytest.approx(0.05, abs=0.005)
    assert samples[2] - samples[1] == pytest.approx(0.05, abs=0.005)


def test_memory_sampler_stop_does_not_wait_for_or_record_blocked_probe(monkeypatch):
    import threading

    import basal_ui.memory_sampler as memory_sampler

    monkeypatch.setattr(memory_sampler, "STOP_JOIN_S", 0.01)
    started, release = threading.Event(), threading.Event()
    recorded = []

    def snapshot():
        started.set()
        release.wait()
        return {"time": 0}

    class StatsStub:
        def record_memory_sample(self, sample):
            recorded.append(sample)

    sampler = memory_sampler.MemorySampler(StatsStub(), snapshot, interval=0.01)
    sampler.start()
    assert started.wait(1)
    sampler.stop()
    release.set()
    sampler._thread.join(1)

    assert recorded == []
