import json
import time

import pytest
from fastapi.testclient import TestClient

from basal_ui import engine, settings
from basal_ui.app import create_app
from basal_ui.fleet import Fleet, Runner
from basal_ui.hardware import Hardware
from basal_ui.history import History
from basal_ui.stats import Stats

HW = Hardware("cpu", "cpu", 1024**4, None, None, frozenset())


@pytest.fixture
def client(tmp_path, monkeypatch, child):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    fleet = Fleet(
        models_dir_fn=lambda: tmp_path,
        platform_fn=lambda: "cpu",
        settings_mod=settings,
        hardware_fn=lambda: HW,
        stats=Stats(tmp_path / "s.json"),
        history=History(tmp_path / "h.jsonl"),
    )
    runner = Runner()
    runner.state, runner.model, runner.base_url = "ready", "mini", child.url
    runner.models_entry = {"early_exit": []}
    fleet.runners["mini"] = runner
    app = create_app(
        catalog_fn=lambda **_: ([], "test"),
        hardware_fn=lambda: HW,
        fleet=fleet,
        stats=fleet.stats,
        history=fleet.history,
    )
    with TestClient(app, base_url="http://127.0.0.1") as c:
        c.fleet = fleet
        yield c


def wait_batch(client, timeout=30):
    deadline = time.monotonic() + timeout
    while client.get("/api/status").json()["jobs"]["batch"]["running"] and time.monotonic() < deadline:
        time.sleep(0.05)
    return client.get("/api/status").json()["jobs"]["batch"]


def test_batch_answers_every_line_and_serves_the_rows_and_the_file(client, child, tmp_path):
    """basal-run runs as a subprocess against the model's child; its summary is the job's result."""
    source = tmp_path / "items.jsonl"
    source.write_text(
        "\n".join(
            json.dumps({"id": f"i{n}", "state": "s", "question": "q?", "options": ["a", "b"], "gold": g})
            for n, g in enumerate([0, 1, 0])
        )
        + "\n"
    )
    assert client.get("/api/batch/rows").status_code == 404
    started = client.post("/api/batch", json={"id": "mini", "input": str(source), "concurrency": 2})
    assert started.status_code == 202
    job = wait_batch(client)
    assert job["phase"] == "done" and (job["done"], job["total"]) == (3, 3), job
    result = job["result"]
    assert (result["items"], result["errors"], result["accuracy"]) == (3, 0, round(2 / 3, 4))
    assert result["output"] == str(tmp_path / "items.answers.jsonl")
    assert [path for path, _ in child.seen] == ["/v1/systemone"] * 3  # straight to the child
    rows = client.get("/api/batch/rows", params={"limit": 2}).json()
    assert rows["total"] == 3 and [r["id"] for r in rows["rows"]] == ["i0", "i1"]
    assert [r["correct"] for r in client.get("/api/batch/rows", params={"offset": 2}).json()["rows"]] == [True]
    download = client.get("/api/batch/download")
    assert download.status_code == 200 and len(download.text.splitlines()) == 3
    assert client.get("/api/stats").json()["models"] == {}  # not counted in the statistics


def test_a_comparison_batch_sends_the_servers_model_name_and_key(client, child, tmp_path):
    """basal-run sends neither a model nor a key: the job's loopback proxy adds both before the Decision server."""
    from basal_ui.fleet import AttachedServerRunner

    server = {"id": "srv", "name": "local", "url": child.url, "base_url": child.url}
    runner = AttachedServerRunner(server, {"id": "remote"}, settings)
    runner.state = "ready"
    settings.set_secret("srv", "secret-key")
    source = tmp_path / "items.jsonl"
    source.write_text(json.dumps({"state": "s", "question": "q?", "options": ["a", "b"], "gold": 0}) + "\n")
    client.fleet.runners[runner.model] = runner
    started = client.post("/api/batch", json={"id": runner.model, "input": str(source)})
    assert started.status_code == 202, started.text
    job = wait_batch(client)
    assert job["phase"] == "done" and job["result"]["errors"] == 0, job
    assert [(path, body["model"]) for path, body in child.seen] == [("/v1/systemone", "remote")]
    assert child.auth == ["Bearer secret-key"]


def test_batch_refuses_output_that_resolves_to_input(client, tmp_path):
    source = tmp_path / "same.jsonl"
    source.write_text('{"state":"s","question":"q","options":["a","b"]}\n')
    response = client.post("/api/batch", json={"id": "mini", "input": str(source), "output": str(source)})
    assert response.status_code == 400 and "must not be the input file" in response.json()["detail"]


def test_cancelling_a_batch_stops_basal_run_and_writes_no_output(client, child, tmp_path):
    child.gate.clear()  # the child holds every request until the test opens it
    source = tmp_path / "items.jsonl"
    source.write_text('{"state": "s", "question": "q?", "options": ["a", "b"]}\n')
    assert client.post("/api/batch", json={"id": "mini", "input": str(source)}).status_code == 202
    deadline = time.monotonic() + 30
    while not child.seen and time.monotonic() < deadline:  # basal-run is running and waiting for the answer
        time.sleep(0.05)
    assert client.delete("/api/batch").status_code == 200
    job = wait_batch(client)
    child.gate.set()
    assert job["phase"] == "cancelled"
    assert not (tmp_path / "items.answers.jsonl").exists()


@pytest.mark.parametrize(
    "body, status, message",
    [
        ({"id": "mini", "input": "/nonexistent/x.jsonl"}, 400, "cannot read"),
        ({"id": "mini", "input": "BAD"}, 400, "line 1"),
        ({"id": "other", "input": "GOOD"}, 404, "not loaded"),
        ({"id": "mini", "input": "GOOD", "early_exit": "0.99"}, 400, "no early exits"),
        ({"id": "mini", "input": "GOOD", "output": "/nonexistent/dir/out.jsonl"}, 400, "cannot write"),
    ],
)
def test_batch_refuses_bad_input(client, tmp_path, body, status, message):
    good, bad = tmp_path / "good.jsonl", tmp_path / "bad.jsonl"
    good.write_text('{"state": "s", "question": "q?", "options": ["a", "b"]}\n')
    bad.write_text("not json\n")
    body = {**body, "input": {"GOOD": str(good), "BAD": str(bad)}.get(body["input"], body["input"])}
    response = client.post("/api/batch", json=body)
    assert response.status_code == status and message in response.json()["detail"]


@pytest.mark.skipif(not engine.feature("presets"), reason="Engine 2.5 presets")
def test_batch_presets_api_lists_engine_names(client):
    from basal.presets import names

    response = client.get("/api/batch/presets")
    assert response.status_code == 200
    assert response.json() == {"available": True, "presets": names(), "langs": ["auto", "pl", "en"]}


def test_batch_presets_api_reports_feature_unavailable(monkeypatch, client):

    monkeypatch.setitem(engine.FEATURES, "presets", lambda: False)
    response = client.get("/api/batch/presets")
    assert response.status_code == 200
    assert response.json()["available"] is False
    assert response.json()["presets"] == response.json()["langs"] == []
    assert "needs engine 2.5" in response.json()["reason"]


@pytest.mark.skipif(not engine.feature("presets"), reason="Engine 2.5 presets")
def test_batch_preset_argv_and_no_preset_argv(client, tmp_path, monkeypatch):
    from basal.presets import names

    from basal_ui import batch

    captured = []
    monkeypatch.setattr(batch, "run_tool", lambda job, module, argv: captured.append(argv) or {"items": 1, "errors": 0})
    for i, body in enumerate(({"preset": names()[0], "lang": "pl"}, {}, {"preset": names()[0]})):
        source = tmp_path / f"preset-{i}.jsonl"
        source.write_text(
            '{"state":"A user asks for help."}\n' if i != 1 else '{"state":"s","question":"q","options":["a","b"]}\n'
        )
        response = client.post("/api/batch", json={"id": "mini", "input": str(source), **body})
        assert response.status_code == 202
        assert wait_batch(client)["phase"] == "done"
    assert ["--preset", names()[0], "--lang", "pl"] == captured[0][-4:]
    assert "--preset" not in captured[1] and "--lang" not in captured[1]
    assert ["--preset", names()[0]] == captured[2][-2:]
    assert "--lang" not in captured[2]


@pytest.mark.skipif(not engine.feature("presets"), reason="Engine 2.5 presets")
def test_state_only_batch_input_requires_preset_and_path_preview_accepts_it(client, tmp_path, monkeypatch):
    from basal.presets import names

    from basal_ui import batch

    preset = names()[0]

    monkeypatch.setattr(batch, "run_tool", lambda job, module, argv: {"items": 1, "errors": 0})
    source = tmp_path / "states.jsonl"
    source.write_text('{"state":"A user asks for help."}\n')
    assert client.post("/api/batch", json={"id": "mini", "input": str(source)}).status_code == 400
    no_preset_preview = client.post("/api/paths/check", json={"path": str(source), "kind": "jsonl"})
    assert no_preset_preview.json()["ok"] is False
    preview = client.post("/api/paths/check", json={"path": str(source), "kind": "jsonl", "preset": preset})
    assert preview.status_code == 200
    assert preview.json()["ok"] is True and preview.json()["first"]["questions"] > 0
    response = client.post("/api/batch", json={"id": "mini", "input": str(source), "preset": preset})
    assert response.status_code == 202 and wait_batch(client)["phase"] == "done"


@pytest.mark.skipif(not engine.feature("presets"), reason="Engine 2.5 presets")
def test_batch_rejects_unknown_preset(client, tmp_path):
    source = tmp_path / "items.jsonl"
    source.write_text('{"state":"s","question":"q","options":["a","b"]}\n')
    response = client.post("/api/batch", json={"id": "mini", "input": str(source), "preset": "missing"})
    assert response.status_code == 400 and "unknown preset" in response.json()["detail"]


def test_batch_rejects_unsupported_preset_language(client, tmp_path):
    source = tmp_path / "items.jsonl"
    source.write_text('{"state":"s","question":"q","options":["a","b"]}\n')
    response = client.post("/api/batch", json={"id": "mini", "input": str(source), "lang": "de"})
    assert response.status_code == 400 and "lang must be one of" in response.json()["detail"]


def test_batch_preset_route_unavailable_when_engine_feature_is_missing(monkeypatch, client):

    monkeypatch.setitem(engine.FEATURES, "presets", lambda: False)
    assert client.get("/api/batch/presets").json()["available"] is False


@pytest.mark.parametrize("comparison", [False, True])
def test_scheduled_batch_records_decisions_but_manual_batch_does_not(client, child, tmp_path, comparison):
    model = "mini"
    if comparison:
        from basal_ui.fleet import AttachedServerRunner

        runner = AttachedServerRunner({"id": "srv", "name": "local", "base_url": child.url}, {"id": "remote"}, settings)
        runner.state = "ready"
        client.fleet.runners[runner.model] = runner
        model = runner.model
    inputs, outputs = tmp_path / "input", tmp_path / "output"
    inputs.mkdir()
    outputs.mkdir()
    source = inputs / "one.jsonl"
    source.write_text('{"state":"s","question":"q","options":["a","b"]}\n')
    definition = {
        "id": "nightly",
        "model": model,
        "input_dir": str(inputs),
        "output_dir": str(outputs),
        "interval": {"every_minutes": 1},
    }
    assert client.post("/api/schedules", json=definition).status_code == 201
    scheduler = client.app.state.schedules
    scheduler.run_due(1000)
    job = client.fleet.jobs.get("batch")
    job.thread.join(30)
    assert not job.thread.is_alive() and job.state()["phase"] == "done"
    scheduler.run_due(1001)
    history = client.get("/api/history", params={"source": "schedule"}).json()["items"]
    assert len(history) == 1 and history[0]["model"] == model
    assert client.get("/api/stats").json()["models"][model]["requests"] == 1
    endpoints = client.get("/api/traffic").json()["endpoints"]
    assert [(row["endpoint"], row["method"], row["requests"]) for row in endpoints] == [("/api/schedules", "RUN", 1)]
    run = client.get("/api/schedules/nightly/runs").json()["runs"][0]
    assert run["model"] == model and run["duration"] >= 0 and run["rows"] == 1 and run["errors"] == 0
    assert client.post("/api/batch", json={"id": model, "input": str(source)}).status_code == 202
    job = client.fleet.jobs.get("batch")
    job.thread.join(30)
    assert not job.thread.is_alive() and job.state()["phase"] == "done"
    assert len(client.get("/api/history", params={"source": "schedule"}).json()["items"]) == 1
    assert client.get("/api/stats").json()["models"][model]["requests"] == 1
