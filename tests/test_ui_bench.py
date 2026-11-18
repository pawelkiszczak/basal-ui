import asyncio
import json
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from basal_ui import bench, catalog, settings
from basal_ui.app import create_app
from basal_ui.catalog import Variant
from basal_ui.fleet import Fleet, Runner
from basal_ui.hardware import Hardware
from basal_ui.history import History
from basal_ui.jobs import Cancelled, Job, LogBuffer
from basal_ui.stats import Stats

HW = Hardware("cpu", "cpu", 1024**4, None, None, frozenset())


def write_set(path, golds):
    path.write_text(
        "\n".join(
            json.dumps({"state": f"s{i}", "question": "q?", "options": ["a", "b"], "gold": g})
            for i, g in enumerate(golds)
        )
        + "\n"
    )
    return path


class AnswersOptionOne:
    """A fleet whose model always prefers option_1."""

    def __init__(self):
        self.sources = []

    async def adecide(self, model_id, body, path="/v1/systemone", source="api", client=None):
        self.sources.append(source)
        await asyncio.sleep(0.001)
        return httpx.Response(
            200,
            json={
                "answers": {"q0": {"probabilities": {"option_1": 0.7, "option_2": 0.3}}},
                "usage": {"input_tokens": 10},
            },
            request=httpx.Request("POST", "http://x"),
        )


def test_run_benchmark_measures_latency_throughput_and_accuracy(tmp_path):
    fleet = AnswersOptionOne()
    paths = [str(write_set(tmp_path / "two.jsonl", [0, 1]))]
    job = Job("benchmark", "m", "two", LogBuffer())
    phases = []
    job.progress = lambda phase, done=0, total=0: phases.append(phase)
    result = asyncio.run(bench.run_benchmark(fleet, "m", paths, "two", job, n_seq=4, n_conc=10, concurrency=3))
    assert result["accuracy"] == {"overall": 0.5, "by_file": {"two": 0.5}}
    assert (result["model"], result["set"], result["items"], result["errors"]) == ("m", "two", 2, 0)
    assert 0 < result["p50_ms"] <= result["p95_ms"] and result["mean_ms"] > 0
    assert result["decisions_per_s"] > 0 and result["input_tokens_per_s"] == pytest.approx(
        result["decisions_per_s"] * 10
    )
    assert set(fleet.sources) == {"benchmark"} and len(fleet.sources) == bench.WARMUP + 4 + 10
    assert phases[0] == "warm-up" and "sequential" in phases and phases[-1] == "concurrent"


def test_bundled_set_and_custom_set_validation(tmp_path):
    label, paths = bench.resolve_set("bundled")
    assert label == "bundled" and len(bench.load_items(paths)) == 44
    for spec, reason in (
        (str(tmp_path / "missing.jsonl"), "does not exist"),
        (str(write_set(tmp_path / "small.jsonl", [0, 1, 0])), "3 items"),
        (str(tmp_path), "at least 10"),
    ):
        with pytest.raises(ValueError, match=reason):
            bench.resolve_set(spec)
    (tmp_path / "bad.jsonl").write_text('{"state": "x"\n')
    with pytest.raises(ValueError, match="bad.jsonl:1: not JSON"):
        bench.resolve_set(str(tmp_path / "bad.jsonl"))
    (tmp_path / "set").mkdir()
    write_set(tmp_path / "set" / "one.jsonl", [0] * 6)
    write_set(tmp_path / "set" / "two.jsonl", [1] * 6)
    assert bench.resolve_set(str(tmp_path / "set"))[1] == [
        str(tmp_path / "set" / f) for f in ("one.jsonl", "two.jsonl")
    ]


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        ({"state": "s", "question": "q", "options": ["only"]}, r"rows\.jsonl:2: expected"),
        ({"state": "s", "options": ["a", "b"]}, r"rows\.jsonl:2: expected"),
        ({"state": "s", "question": "q", "options": ["a", "b"], "gold": 2}, r"rows\.jsonl:2: gold must index"),
        ({"state": "s", "question": "q", "options": ["a", "b"], "gold": True}, r"rows\.jsonl:2: gold must index"),
    ],
)
def test_a_malformed_benchmark_row_names_its_line(tmp_path, row, reason):
    good = {"state": "s", "question": "q", "options": ["a", "b"], "gold": 1}
    path = tmp_path / "rows.jsonl"
    path.write_text(json.dumps(good) + "\n" + json.dumps(row) + "\n")
    with pytest.raises(ValueError, match=reason):
        bench.load_items([path])


def test_benchmark_routes_run_one_at_a_time_and_store_the_result(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    release = threading.Event()

    async def fake_run(fleet, model_id, paths, label, job):
        job.progress("sequential", 1, 30)
        while not release.is_set():
            if job.cancel.is_set():
                raise Cancelled()
            await asyncio.sleep(0.01)
        return {"model": model_id, "set": label, "p50_ms": 1.0}

    monkeypatch.setattr(bench, "run_benchmark", fake_run)
    stats = Stats(tmp_path / "stats.json")
    fleet = Fleet(
        models_dir_fn=lambda: tmp_path,
        platform_fn=lambda: "cpu",
        settings_mod=settings,
        hardware_fn=lambda: HW,
        stats=stats,
        history=History(tmp_path / "h.jsonl"),
    )
    runner = Runner()
    runner.state, runner.model = "ready", "mini"
    fleet.runners["mini"] = runner
    app = create_app(
        catalog_fn=lambda **_: ([], "test"), hardware_fn=lambda: HW, fleet=fleet, stats=stats, history=fleet.history
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.post("/api/benchmark", json={"id": "nope"}).status_code == 404
        too_small = client.post(
            "/api/benchmark", json={"id": "mini", "set": str(write_set(tmp_path / "s.jsonl", [0, 1]))}
        )
        assert too_small.status_code == 400 and "at least 10" in too_small.json()["detail"]
        started = client.post("/api/benchmark", json={"id": "mini"})
        assert started.status_code == 202 and started.json()["running"] is True
        busy = client.post("/api/benchmark", json={"id": "mini"})
        assert busy.status_code == 409 and busy.json()["detail"] == "a benchmark job is already running"

        def job():
            return client.get("/api/status").json()["jobs"]["benchmark"]

        deadline = time.monotonic() + 3
        while job()["phase"] != "sequential" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert job()["phase"] == "sequential"
        release.set()
        deadline = time.monotonic() + 3
        while job()["running"] and time.monotonic() < deadline:
            time.sleep(0.01)
        assert job()["phase"] == "done" and job()["result"]["p50_ms"] == 1.0
        assert client.get("/api/benchmarks").json() == {"mini": [{"model": "mini", "set": "bundled", "p50_ms": 1.0}]}
        assert {"id": "job:benchmark", "state": "done"} in client.get("/api/logs").json()["sources"]

        release.clear()
        client.post("/api/benchmark", json={"id": "mini"})
        assert client.delete("/api/benchmark").status_code == 200
        deadline = time.monotonic() + 3
        while job()["running"] and time.monotonic() < deadline:
            time.sleep(0.01)
        assert job()["phase"] == "cancelled"


def make_app(tmp_path, monkeypatch, hw=HW, variants=(), base_url="http://child"):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    fleet = Fleet(
        models_dir_fn=lambda: tmp_path,
        platform_fn=lambda: "cpu",
        settings_mod=settings,
        hardware_fn=lambda: hw,
        stats=Stats(tmp_path / "stats.json"),
        history=History(tmp_path / "h.jsonl"),
    )
    runner = Runner()
    runner.state, runner.model, runner.base_url, runner.models_entry = "ready", "mini", base_url, {"early_exit": []}
    fleet.runners["mini"] = runner
    app = create_app(
        catalog_fn=lambda **_: (list(variants), "test"),
        hardware_fn=lambda: hw,
        fleet=fleet,
        stats=fleet.stats,
        history=fleet.history,
    )
    return app, fleet


def finished(client, timeout=60):
    deadline = time.monotonic() + timeout
    while client.get("/api/status").json()["jobs"]["benchmark"]["running"] and time.monotonic() < deadline:
        time.sleep(0.01)
    return client.get("/api/status").json()["jobs"]["benchmark"]


def test_load_test_and_determinism_check_against_the_model(tmp_path, monkeypatch, child):
    """basal-loadtest runs as a subprocess against the model's child; the JSON it writes is the result."""
    child.reply = {"answers": {"q0": {"probabilities": {"option_1": 0.7, "option_2": 0.3}}}}  # deterministic
    app, fleet = make_app(tmp_path, monkeypatch, base_url=child.url)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        bad = client.post("/api/benchmark", json={"id": "mini", "kind": "loadtest", "params": {"n_seq": 0}})
        assert bad.status_code == 400 and "n_seq" in bad.json()["detail"]
        no_exit = client.post(
            "/api/benchmark", json={"id": "mini", "kind": "loadtest", "params": {"early_exit": "0.99"}}
        )
        assert no_exit.status_code == 400 and "no early exits" in no_exit.json()["detail"]
        params = {"n_seq": 4, "n_conc": 6, "concurrency": 3}
        assert (
            client.post("/api/benchmark", json={"id": "mini", "kind": "loadtest", "params": params}).status_code == 202
        )
        result = finished(client)["result"]
        assert result["kind"] == "loadtest" and result["errors"] == 0 and result["p50_ms"] > 0
        assert result["decisions_per_s"] > 0 and result["params"]["n_seq"] == 4
        check = {"id": "mini", "kind": "determinism", "params": {"determinism": 5, "concurrency": 2}}
        assert client.post("/api/benchmark", json=check).status_code == 202
        d = finished(client)["result"]
        assert d["kind"] == "determinism" and d["determinism"]["identical"] == d["determinism"]["requests"] == 5
        assert [r["kind"] for r in client.get("/api/benchmarks").json()["mini"]] == ["loadtest", "determinism"]


BF16 = Variant("bf16", "Remek/basal-2.0-mini", "basal-2.0", "mini", "bf16", "bf16", None, 4 * 1024**3, False)


def test_mode_comparison_runs_basal_bench_and_reserves_its_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "is_downloaded", lambda variant, cache_dir, revision=None: tmp_path)
    built, reserved = [], []

    def fake_bench(job, module, argv, out_path=None, on_json=None):  # basal-bench: one row per mode, in order
        modes = argv[argv.index("--modes") + 1 : argv.index("--n")]
        for i, mode in enumerate(modes):
            if mode == "ollama":
                raise RuntimeError("basal-bench exited (1): no Ollama server")
            built.append(mode)
            reserved.append(dict(fleet.reserved))
            row = {"mode": mode, "reference_mode": modes[0], "n": 20, "lat1_ms": 1.0, "lat2_ms": 2.0, "dec_s": 10.0}
            on_json(row if i == 0 else {**row, "agree": 0.95})

    monkeypatch.setattr(bench, "run_tool", fake_bench)
    app, fleet = make_app(tmp_path, monkeypatch, variants=[BF16])
    with TestClient(app, base_url="http://127.0.0.1") as client:
        selectable = client.get("/api/modes", params={"id": "bf16"}).json()["modes"]
        assert selectable[:2] == ["eager-fp32", "eager"] and "fast" not in selectable  # a CPU machine
        unknown = client.post("/api/benchmark", json={"id": "bf16", "kind": "modes", "modes": ["fast"]})
        assert unknown.status_code == 400 and "fast cannot run here" in unknown.json()["detail"]
        body = {"id": "bf16", "kind": "modes", "modes": ["eager-fp32", "eager"], "n": 20, "lat_n": 5}
        assert client.post("/api/benchmark", json=body).status_code == 202
        result = finished(client)["result"]
        assert built == ["eager-fp32", "eager"] and result["kind"] == "modes" and result["items"] == 20
        assert [r.get("agree") for r in result["modes"]] == [None, 0.95]
        est = catalog.assess(BF16, HW).est_memory_bytes
        assert reserved[0] == {"modes": 2 * est} and fleet.reserved == {}  # eager-fp32: twice the bf16 estimate
        settings.update_memory({"budget_bytes": 2 * 1024**3 + est, "reserve_bytes": 0})
        over = client.post("/api/benchmark", json=body)
        assert over.status_code == 409 and over.json()["detail"].startswith("over memory budget")
        assert client.post("/api/benchmark", json={**body, "modes": ["eager", "ollama"]}).status_code == 202
        rows = finished(client)["result"]["modes"]
        assert rows[0]["mode"] == "eager" and rows[1] == {
            "mode": "ollama",
            "error": "basal-bench exited (1): no Ollama server",
        }


def test_identity_gate_runs_engine_tool_and_saves_gate_results(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "is_downloaded", lambda variant, cache_dir, revision=None: tmp_path)
    monkeypatch.setattr(
        "basal_ui.options.available_modes",
        lambda *args: [{"mode": "fast", "available": True, "reason": None}],
    )
    rows = [{"path": "plain", "gate": "pass", "max_dp": 0.001}, {"path": "soam", "gate": "FAIL", "max_dp": 0.02}]
    seen = []

    def fake_tool(job, module, argv, out_path=None, **kwargs):
        seen.extend(argv)
        with open(out_path, "w") as out:
            json.dump({"rows": rows}, out)
        return {"rows": rows}

    monkeypatch.setattr(bench, "run_tool", fake_tool)
    app, fleet = make_app(tmp_path, monkeypatch, variants=[BF16])
    settings.set_load_options(
        "bf16", {"attn": "fa3", "merge": "on", "lean": "pass2", "tree": "flex", "gemma_attn": "math"}
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post("/api/benchmark", json={"id": "bf16", "kind": "identity", "dtype": "bf16"})
        assert response.status_code == 202, response.text
        result = finished(client)["result"]
        assert seen[:6] == ["--identity", "--model", str(tmp_path), "--dtype", "bfloat16", "--out"]
        assert seen[7:] == [
            "--attn",
            "fa3",
            "--merge",
            "on",
            "--lean",
            "pass2",
            "--tree",
            "flex",
            "--gemma-attn",
            "math",
        ]
        assert client.get("/api/benchmarks").json()["bf16"][-1]["rows"] == rows
        assert result["rows"] == rows and result["pass"] is False
        assert result["engine"] and fleet.reserved == {}


def test_order_check_is_refused_without_engine_feature(tmp_path, monkeypatch):
    from basal_ui import engine

    monkeypatch.setattr(catalog, "is_downloaded", lambda variant, cache_dir, revision=None: tmp_path)
    monkeypatch.setitem(engine.FEATURES, "order_check", lambda: False)
    app, _ = make_app(tmp_path, monkeypatch, variants=[BF16])
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post("/api/benchmark", json={"id": "bf16", "kind": "order_check"})
        assert response.status_code == 400 and "Engine 2.5" in response.json()["detail"]


def test_identity_rejects_empty_results_and_releases_reservation(tmp_path, monkeypatch):
    class Reserved:
        released = False
        stats = None

        def release(self, kind):
            assert kind == "identity"
            self.released = True

    fleet = Reserved()
    monkeypatch.setattr(bench, "run_tool", lambda *args, **kwargs: {"rows": []})
    with pytest.raises(RuntimeError, match="invalid identity gate output"):
        bench.identity_job(Job("benchmark", "m", "test", LogBuffer()), fleet, BF16, tmp_path, "float32", {})
    assert fleet.released


@pytest.mark.parametrize("delta", [float("nan"), float("inf"), -0.1, True])
def test_identity_rejects_invalid_delta(tmp_path, monkeypatch, delta):
    class FleetStub:
        stats = None

        def release(self, kind):
            assert kind == "identity"

    monkeypatch.setattr(bench, "run_tool", lambda *a, **k: {"rows": [{"path": "p", "gate": "pass", "max_dp": delta}]})
    with pytest.raises(RuntimeError, match="invalid identity"):
        bench.identity_job(Job("benchmark", "m", "test", LogBuffer()), FleetStub(), BF16, tmp_path, "float32", {})


@pytest.mark.parametrize("result", [None, {}, {"mode": "mlx", "n": 1, "by_k": {}}])
def test_order_check_rejects_malformed_report(tmp_path, monkeypatch, result):
    class FleetStub:
        stats = None
        released = False

        def release(self, kind):
            assert kind == "order_check"
            self.released = True

    fleet = FleetStub()
    monkeypatch.setattr(bench, "run_tool", lambda *a, **k: result)
    with pytest.raises(RuntimeError, match="invalid order check"):
        bench.order_check_job(Job("benchmark", "m", "test", LogBuffer()), fleet, BF16, tmp_path)
    assert fleet.released


@pytest.mark.parametrize(
    "lean,tree,effective",
    [
        ("pass2", "flex", ("pass2", "flex")),
        ("auto", "auto", ("pass2", "flex")),
        ("auto", "dense", ("off", "dense")),
        ("on", "auto", ("on", "dense")),
        ("off", "auto", ("off", "dense")),
        ("pass2", "auto", ("pass2", "flex")),
        ("auto", "flex", ("pass2", "flex")),
    ],
)
def test_identity_arguments_pass_native_parser(monkeypatch, tmp_path, lean, tree, effective):
    import argparse

    from basal import identity

    parsed = []
    parse = argparse.ArgumentParser.parse_args

    class Parsed(Exception):
        pass

    def capture(parser, argv):
        parsed.append(parse(parser, argv))
        raise Parsed()

    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", capture)
    options = {"attn": "fa3", "merge": "on", "lean": lean, "tree": tree, "gemma_attn": "math"}
    argv = bench.identity_args(tmp_path, "float32", options, tmp_path / "out.json")
    with pytest.raises(Parsed):
        identity.main(argv[1:])
    assert (parsed[0].dtype, parsed[0].attn, parsed[0].merge, parsed[0].lean, parsed[0].tree, parsed[0].gemma_attn) == (
        "float32",
        "fa3",
        "on",
        *effective,
        "math",
    )


def test_identity_reservation_conflict_cancel_and_maintenance(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "is_downloaded", lambda *a, **k: tmp_path)
    monkeypatch.setattr(
        "basal_ui.options.available_modes", lambda *a: [{"mode": "fast", "available": True, "reason": None}]
    )
    started, release = threading.Event(), threading.Event()

    def tool(job, module, argv, out_path=None):
        started.set()
        assert release.wait(5)
        job.check()
        return {"rows": [{"path": "plain", "gate": "pass", "max_dp": 0.0}]}

    monkeypatch.setattr(bench, "run_tool", tool)
    app, fleet = make_app(tmp_path, monkeypatch, variants=[BF16])
    body = {"id": "bf16", "kind": "identity", "dtype": "fp32"}
    with TestClient(app, base_url="http://127.0.0.1") as client:
        try:
            assert client.post("/api/benchmark", json=body).status_code == 202
            assert started.wait(5)
            assert fleet.reserved == {"identity": 2 * catalog.assess(BF16, HW).est_memory_bytes}
            assert client.post("/api/benchmark", json=body).status_code == 409
            assert client.delete("/api/benchmark").status_code == 200
            release.set()
            fleet.jobs.get("benchmark").thread.join(5)
            assert fleet.jobs.get("benchmark").state()["phase"] == "cancelled"
            assert fleet.reserved == {}
            fleet.engine_addon_installing = True
            assert client.post("/api/benchmark", json=body).status_code == 409
            assert fleet.reserved == {}
        finally:
            release.set()
            fleet.engine_addon_installing = False
            fleet.jobs.join(5)


def test_engine_check_tool_failure_releases_memory(tmp_path, monkeypatch):
    class FleetStub:
        stats = None
        released = []

        def release(self, kind):
            self.released.append(kind)

    def fail(*args, **kwargs):
        raise RuntimeError("Native tool failed")

    fleet = FleetStub()
    monkeypatch.setattr(bench, "run_tool", fail)
    for kind in ("identity", "order_check"):
        with pytest.raises(RuntimeError, match="Native tool failed"):
            if kind == "identity":
                bench.identity_job(Job("benchmark", "m", "test", LogBuffer()), fleet, BF16, tmp_path, "float32", {})
            else:
                bench.order_check_job(Job("benchmark", "m", "test", LogBuffer()), fleet, BF16, tmp_path)
    assert fleet.released == ["identity", "order_check"]
