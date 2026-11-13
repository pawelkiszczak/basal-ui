import json
import time

import pytest
from fastapi.testclient import TestClient

from basal_ui import settings
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
    assert client.get("/api/history").json()["items"] == []
    assert client.get("/api/traffic").json()["requests"] == 0


@pytest.mark.parametrize("kind", ["native", "cascade"])
def test_manual_batch_rejects_runner_replaced_after_validation(client, child, tmp_path, monkeypatch, kind):
    source = tmp_path / "items.jsonl"
    source.write_text('{"state":"s","question":"q","options":["a","b"]}\n')
    fleet = client.app.state.schedules.fleet
    checked = fleet.runners["mini"]
    if kind == "cascade":
        checked.models_entry = {"kind": "cascade", "early_exit": []}
        checked.spec = {"kind": "escalate", "fallback": None}
    replacement = Runner()
    replacement.state, replacement.model, replacement.base_url = "ready", "mini", child.url
    replacement.models_entry = {"kind": "cascade", "early_exit": []}
    replacement.spec = {"kind": "blend", "fallback": None}
    original = fleet.start_model_job

    def replace(*args, **kwargs):
        with fleet.lock:
            fleet.runners["mini"] = replacement
        return original(*args, **kwargs)

    monkeypatch.setattr(fleet, "start_model_job", replace)
    response = client.post("/api/batch", json={"id": "mini", "input": str(source)})
    assert response.status_code == 409 and "model changed during job preparation" in response.text
    assert child.seen == [] and fleet.jobs.get("batch") is None and not fleet.leases


def test_manual_batch_executes_on_leased_runner_after_name_rebound(client, child, tmp_path, monkeypatch):
    import threading

    from basal_ui import batch

    source = tmp_path / "items.jsonl"
    source.write_text('{"state":"s","question":"q","options":["a","b"]}\n')
    fleet = client.app.state.schedules.fleet
    checked = fleet.runners["mini"]
    replacement = Runner()
    replacement.state, replacement.model, replacement.base_url = "ready", "mini", "http://127.0.0.1:1"
    replacement.models_entry = {"early_exit": []}
    entered, release = threading.Event(), threading.Event()
    original = batch.run_batch

    def hold(job, *args):
        entered.set()
        assert release.wait(30)
        return original(job, *args)

    monkeypatch.setattr(batch, "run_batch", hold)
    try:
        assert client.post("/api/batch", json={"id": "mini", "input": str(source)}).status_code == 202
        assert entered.wait(30)
        with fleet.lock:
            fleet.runners["mini"] = replacement
        release.set()
        job = fleet.jobs.get("batch")
        job.thread.join(30)
        assert not job.thread.is_alive() and job.state()["phase"] == "done"
        assert len(child.seen) == 1
        assert client.get("/api/stats").json()["models"] == {}
        assert client.get("/api/history").json()["items"] == []
        assert client.get("/api/traffic").json()["requests"] == 0
    finally:
        release.set()
        with fleet.lock:
            fleet.runners["mini"] = checked


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
