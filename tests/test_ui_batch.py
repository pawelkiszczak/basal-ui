import json
import os
import sys
import time
from pathlib import Path
from types import ModuleType

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
    assert response.json()["reason_code"] == "batch_presets_unavailable"


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


@pytest.mark.skipif(not engine.feature("presets"), reason="Engine presets unavailable")
def test_preset_materializes_native_requests_and_preserves_source(tmp_path, monkeypatch):
    from basal.presets import questions
    from basal.run import summarise, to_request

    from basal_ui import batch
    from basal_ui.jobs import Job, LogBuffer

    source = tmp_path / "input.jsonl"
    original = '{"id":"one","state":"Help me"}\n{"id":"two","state":"s","question":"q","options":["a","b"]}\n'
    source.write_text(original)
    temporary = []

    def native_boundary(job, module, argv):
        path = Path(argv[argv.index("--input") + 1])
        temporary.append(path)
        items = [json.loads(line) for line in path.read_text().splitlines()]
        assert items[0]["questions"] == questions("triage", "en", "Help me")
        assert "questions" not in items[1]
        request = to_request(items[0], preset="triage", lang="en")
        answers = {name: {"type": "choice"} for name in request["questions"]}
        assert summarise(items[0], {"answers": answers})["answers"] == answers
        return {"items": 2, "errors": 0}

    monkeypatch.setattr(batch, "run_tool", native_boundary)
    fleet = type("Fleet", (), {"ready": lambda self, model: type("Runner", (), {"base_url": "http://localhost"})()})()
    result = batch.run_batch(
        Job("batch", "m", "test", LogBuffer()), fleet, "m", source, tmp_path / "out", preset="triage", lang="en"
    )
    assert result["input"] == str(source) and source.read_text() == original
    assert not temporary[0].exists()


def test_preset_materialization_cancellation_cleans_temp(tmp_path, monkeypatch):
    import basal

    from basal_ui import batch
    from basal_ui.jobs import Cancelled, Job, LogBuffer

    source = tmp_path / "input.jsonl"
    source.write_text('{"state":"one"}\n{"state":"two"}\n')
    created = []
    real_temp = batch.tempfile.TemporaryDirectory

    def temporary(*args, **kwargs):
        context = real_temp(*args, **kwargs)
        created.append(Path(context.name))
        return context

    monkeypatch.setattr(batch.tempfile, "TemporaryDirectory", temporary)
    monkeypatch.setattr(batch, "run_tool", lambda *a, **k: pytest.fail("cancelled job launched Native tool"))
    job = Job("batch", "m", "test", LogBuffer())

    def questions(name, lang, state):
        job.cancel.set()
        return {
            "urgency": {
                "type": "choice",
                "instructions": "Choose urgency",
                "criteria": {"low": "Low", "high": "High"},
            },
        }

    presets = ModuleType("basal.presets")
    presets.questions = questions
    monkeypatch.setitem(sys.modules, "basal.presets", presets)
    monkeypatch.setattr(basal, "presets", presets, raising=False)
    fleet = type("Fleet", (), {"ready": lambda self, model: type("Runner", (), {"base_url": "http://localhost"})()})()
    with pytest.raises(Cancelled):
        batch.run_batch(job, fleet, "m", source, tmp_path / "out", preset="triage", lang="en")
    assert created and not created[0].exists()
    assert source.read_text() == '{"state":"one"}\n{"state":"two"}\n'
    assert not (tmp_path / "out").exists()


def test_batch_choice_and_command(client, tmp_path, monkeypatch):
    from basal_ui import batch

    monkeypatch.setattr(batch, "capabilities", lambda: {"restart": True, "stop_after": True})
    source = tmp_path / "input.jsonl"
    source.write_text('{"question":"q","options":["a","b"]}\n')
    out = tmp_path / "out.jsonl"
    part = tmp_path / "out.jsonl.part"
    part.write_text('{"answer":1}\n')
    calls = []

    def fake_run(job, module, args):
        calls.append(args)
        return {"items": 1}

    monkeypatch.setattr(batch, "run_tool", fake_run)
    body = {"id": "mini", "input": str(source), "output": str(out)}
    assert client.post("/api/batch", json=body).json()["code"] == "batch_choice"
    assert part.read_text() == '{"answer":1}\n'
    assert client.post("/api/batch", json={**body, "choice": "resume", "concurrency": 2}).status_code == 202
    assert wait_batch(client)["phase"] == "done"
    assert "--restart" not in calls[-1] and calls[-1][-1] == "2"
    assert client.post("/api/batch", json={**body, "choice": "restart", "stop_after": 0}).status_code == 202
    assert wait_batch(client)["phase"] == "done"
    assert calls[-1][-3:] == ["--restart", "--stop-after", "0"]
    part.unlink()
    sidecar = tmp_path / "out.jsonl.part.json"
    sidecar.write_text("{}")
    assert client.post("/api/batch", json=body).json()["code"] == "batch_choice"
    assert client.post("/api/batch", json={**body, "choice": "resume"}).status_code == 409
    assert sidecar.read_text() == "{}"
    assert client.post("/api/batch", json={**body, "stop_after": -1}).status_code == 400


@pytest.mark.parametrize("partial,phase", [(True, "stopped_resumable"), (False, "error")])
def test_batch_failed_run_preserves_partial(client, tmp_path, monkeypatch, partial, phase):
    from basal_ui import batch

    monkeypatch.setattr(batch, "capabilities", lambda: {"restart": True, "stop_after": True})
    source = tmp_path / "input.jsonl"
    source.write_text('{"question":"q","options":["a","b"]}\n')
    out = tmp_path / "out.jsonl"
    part = tmp_path / "out.jsonl.part"

    def fail(job, module, args):
        if partial:
            part.write_text('{"answer":1}\n\n{"incomplete":')
        raise RuntimeError("server unavailable")

    monkeypatch.setattr(batch, "run_tool", fail)
    if partial:
        monkeypatch.setattr(type(part), "read_bytes", lambda _: pytest.fail("checkpoint must be streamed"))
    assert client.post("/api/batch", json={"id": "mini", "input": str(source), "output": str(out)}).status_code == 202
    job = wait_batch(client)
    assert job["phase"] == phase and "server unavailable" in job["error"]
    if partial:
        assert job["done"] == 1 and part.exists()
        assert job["result"] == {
            "id": "mini",
            "input": str(source),
            "output": str(out),
            "concurrency": 16,
            "early_exit": None,
            "stop_after": 16,
        }


def test_installed_engine_resumes_after_cancel(client, child, tmp_path):
    from basal_ui import batch

    if not batch.capabilities()["restart"]:
        pytest.skip("installed Engine does not support Batch resume")
    child.gate.clear()
    source = tmp_path / "resume.jsonl"
    source.write_text('{"state":"s","question":"q","options":["a","b"]}\n' * 4)
    out = tmp_path / "answers.jsonl"
    part = tmp_path / "answers.jsonl.part"
    body = {"id": "mini", "input": str(source), "output": str(out), "concurrency": 1}
    try:
        assert client.post("/api/batch", json=body).status_code == 202
        deadline = time.monotonic() + 30
        while not child.seen and time.monotonic() < deadline:
            time.sleep(0.05)
        assert child.seen
        client.delete("/api/batch")
        assert wait_batch(client)["phase"] == "cancelled"
        assert part.exists()
    finally:
        child.gate.set()
    assert client.post("/api/batch", json=body).status_code == 409
    assert client.post("/api/batch", json={**body, "choice": "resume", "concurrency": 2}).status_code == 202
    assert wait_batch(client)["phase"] == "done"
    assert len(out.read_text().splitlines()) == 4


@pytest.mark.parametrize("suffix", [".part", ".part.json", ".part.json.tmp"])
@pytest.mark.parametrize("alias", ["direct", "symlink", "hardlink"])
def test_checkpoint_collision_refused(client, tmp_path, monkeypatch, suffix, alias):
    from basal_ui import batch

    monkeypatch.setattr(batch, "capabilities", lambda: {"restart": True, "stop_after": True})
    out = tmp_path / "answers"
    artifact = tmp_path / ("answers" + suffix)
    artifact.write_text('{"question":"q","options":["a","b"]}\n')
    source = artifact
    if alias != "direct":
        source = tmp_path / "input"
        if alias == "symlink":
            source.symlink_to(artifact)
        else:
            source.hardlink_to(artifact)
    response = client.post("/api/batch", json={"id": "mini", "input": str(source), "output": str(out)})
    assert response.status_code == 400
    assert "must be distinct" in response.json()["detail"] or "hard links" in response.json()["detail"]
    assert artifact.read_text() == '{"question":"q","options":["a","b"]}\n'


def test_output_checkpoint_alias_and_stale_choice_refused(client, tmp_path, monkeypatch):
    from basal_ui import batch

    monkeypatch.setattr(batch, "capabilities", lambda: {"restart": True, "stop_after": True})
    source = tmp_path / "input"
    source.write_text('{"question":"q","options":["a","b"]}\n')
    out = tmp_path / "answers"
    body = {"id": "mini", "input": str(source), "output": str(out), "choice": "restart"}
    assert client.post("/api/batch", json=body).status_code == 409
    out.write_text("old output")
    part = tmp_path / "answers.part"
    part.hardlink_to(out)
    assert client.post("/api/batch", json=body).status_code == 400
    assert out.read_text() == "old output"


def test_job_rechecks_stale_restart(tmp_path):
    from types import SimpleNamespace

    from basal_ui import batch

    fleet = SimpleNamespace(ready=lambda _: SimpleNamespace(base_url="http://127.0.0.1"))
    with pytest.raises(RuntimeError, match="no longer exists"):
        batch.run_batch(
            None, fleet, "mini", tmp_path / "input", tmp_path / "output", choice="restart", restart_supported=True
        )


@pytest.mark.parametrize(
    "first,second", [(".part", ".part.json"), (".part", ".part.json.tmp"), (".part.json", ".part.json.tmp")]
)
@pytest.mark.parametrize("link", ["symlink", "hardlink"])
def test_checkpoint_artifacts_must_not_alias(client, tmp_path, monkeypatch, first, second, link):
    from basal_ui import batch

    monkeypatch.setattr(batch, "capabilities", lambda: {"restart": True, "stop_after": True})
    source = tmp_path / "input"
    source.write_text('{"question":"q","options":["a","b"]}\n')
    out = tmp_path / "answers"
    target, alias = tmp_path / ("answers" + first), tmp_path / ("answers" + second)
    target.write_text("preserve")
    if link == "symlink":
        alias.symlink_to(target)
    else:
        alias.hardlink_to(target)
    response = client.post(
        "/api/batch", json={"id": "mini", "input": str(source), "output": str(out), "choice": "restart"}
    )
    assert response.status_code == 400
    assert target.read_text() == "preserve"


@pytest.mark.parametrize("suffix", [".part", ".part.json", ".part.json.tmp"])
@pytest.mark.parametrize("link", ["symlink", "hardlink"])
def test_checkpoint_link_to_unrelated_file_refused(client, tmp_path, monkeypatch, suffix, link):
    from types import SimpleNamespace

    from basal_ui import batch

    monkeypatch.setattr(batch, "capabilities", lambda: {"restart": True, "stop_after": True})
    source = tmp_path / "input"
    source.write_text('{"question":"q","options":["a","b"]}\n')
    out, target = tmp_path / "answers", tmp_path / "unrelated"
    target.write_text("do not overwrite")
    artifact = tmp_path / ("answers" + suffix)
    if link == "symlink":
        artifact.symlink_to(target)
    else:
        artifact.hardlink_to(target)
    body = {"id": "mini", "input": str(source), "output": str(out), "choice": "restart"}
    response = client.post("/api/batch", json=body)
    assert response.status_code == 400 and "must not be symbolic links or hard links" in response.json()["detail"]
    fleet = SimpleNamespace(ready=lambda _: SimpleNamespace(base_url="http://127.0.0.1"))
    with pytest.raises(ValueError, match="must not be symbolic links or hard links"):
        batch.run_batch(None, fleet, "mini", source, out, choice="restart", restart_supported=True)
    assert target.read_text() == "do not overwrite"


def test_dangling_checkpoint_symlink_refused(client, tmp_path, monkeypatch):
    from basal_ui import batch

    monkeypatch.setattr(batch, "capabilities", lambda: {"restart": True, "stop_after": True})
    source = tmp_path / "input"
    source.write_text('{"question":"q","options":["a","b"]}\n')
    (tmp_path / "answers.part").symlink_to(tmp_path / "missing")
    response = client.post("/api/batch", json={"id": "mini", "input": str(source), "output": str(tmp_path / "answers")})
    assert response.status_code == 400
    assert not (tmp_path / "missing").exists()


@pytest.mark.parametrize("kind", ["cycle", "fifo"])
def test_non_regular_checkpoint_refused_without_opening(client, tmp_path, monkeypatch, kind):
    from types import SimpleNamespace

    from basal_ui import batch

    if kind == "fifo" and not hasattr(os, "mkfifo"):
        pytest.skip("named pipes are unavailable")
    monkeypatch.setattr(batch, "capabilities", lambda: {"restart": True, "stop_after": True})
    source = tmp_path / "input"
    source.write_text('{"question":"q","options":["a","b"]}\n')
    out, part = tmp_path / "answers", tmp_path / "answers.part"
    if kind == "cycle":
        part.symlink_to(part.name)
    else:
        os.mkfifo(part)
    response = client.post("/api/batch", json={"id": "mini", "input": str(source), "output": str(out)})
    assert response.status_code == 400
    fleet = SimpleNamespace(ready=lambda _: SimpleNamespace(base_url="http://127.0.0.1"))
    with pytest.raises(ValueError, match="Batch checkpoint files"):
        batch.run_batch(None, fleet, "mini", source, out, restart_supported=True)


def test_batch_strips_question_metadata_without_rounding_or_touching_input(tmp_path, monkeypatch):
    from decimal import Decimal
    from types import SimpleNamespace

    from basal_ui import batch

    source = tmp_path / "source.jsonl"
    original = (
        '{"state":{"value":0.123456789012345678,"_mcp_source":"business"},'
        '"questions":{"q":{"type":"tool","_mcp_source":{"url":"local"},'
        '"tools":[{"name":"weather","inputSchema":{"_mcp_source":"schema"}}]}}}\r\n'
        "not-json\r\n"
    )
    source.write_bytes(original.encode())
    received = {}

    def run(job, module, args):
        path = Path(args[args.index("--input") + 1])
        received["path"] = path
        received["bytes"] = path.read_bytes()
        received["lines"] = received["bytes"].decode().splitlines()
        return {"items": 2}

    from pathlib import Path

    monkeypatch.setattr(batch, "run_tool", run)
    job = SimpleNamespace(progress=lambda *args: None, check=lambda: None)
    fleet = SimpleNamespace(ready=lambda _: SimpleNamespace(base_url="http://127.0.0.1:1"))
    result = batch.run_batch(job, fleet, "model", source, tmp_path / "out.jsonl")
    row = json.loads(received["lines"][0], parse_float=Decimal)
    assert row["state"]["value"] == Decimal("0.123456789012345678")
    assert row["state"]["_mcp_source"] == "business"
    assert "_mcp_source" not in row["questions"]["q"]
    assert row["questions"]["q"]["tools"][0]["inputSchema"]["_mcp_source"] == "schema"
    assert received["lines"][1] == "not-json" and source.read_bytes() == original.encode()
    assert received["bytes"].endswith(b"\r\nnot-json\r\n")
    assert result["input"] == str(source) and not received["path"].exists()


def test_batch_removes_sanitized_input_when_runner_fails(tmp_path, monkeypatch):
    from pathlib import Path
    from types import SimpleNamespace

    from basal_ui import batch

    source = tmp_path / "source.jsonl"
    source.write_text('{"questions":{"q":{"_mcp_source":{"url":"local"}}}}\n')
    owned = []

    def fail(job, module, args):
        owned.append(Path(args[args.index("--input") + 1]))
        assert owned[0].exists()
        raise RuntimeError("cancelled")

    monkeypatch.setattr(batch, "run_tool", fail)
    with pytest.raises(RuntimeError, match="cancelled"):
        batch.run_batch(
            SimpleNamespace(progress=lambda *args: None, check=lambda: None),
            SimpleNamespace(ready=lambda _: SimpleNamespace(base_url="http://127.0.0.1:1")),
            "model",
            source,
            tmp_path / "out.jsonl",
        )
    assert not owned[0].exists() and source.exists()


def test_ordinary_batch_runs_native_when_temp_copy_unavailable(client, child, tmp_path, monkeypatch):
    from basal_ui import batch

    source = tmp_path / "ordinary.jsonl"
    source.write_text('{"state":"s","question":"q","options":["a","b"]}\n')

    def no_temp(*args, **kwargs):
        raise OSError("temporary storage unavailable")

    monkeypatch.setattr(batch.tempfile, "TemporaryDirectory", no_temp)
    assert client.post("/api/batch", json={"id": "mini", "input": str(source)}).status_code == 202
    job = wait_batch(client)
    assert job["phase"] == "done", job
    assert len(child.seen) == 1 and job["result"]["input"] == str(source)


@pytest.mark.parametrize("checkpoint", [1, 4, 6])
def test_batch_cancellation_at_scan_copy_and_spawn_boundary(tmp_path, monkeypatch, checkpoint):
    import threading
    from types import SimpleNamespace

    from basal_ui import batch
    from basal_ui.jobs import Cancelled

    source = tmp_path / "source.jsonl"
    source.write_text('{"questions":{"q":{"_mcp_source":{"url":"local"}}}}\n{}\n')
    entered, release = threading.Event(), threading.Event()
    spawned, errors = [], []

    class Job:
        calls = 0

        def progress(self, *args):
            pass

        def check(self):
            self.calls += 1
            if self.calls == checkpoint:
                entered.set()
                assert release.wait(5)
                raise Cancelled()

    monkeypatch.setattr(batch.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(batch, "run_tool", lambda *args: spawned.append(True))

    def execute():
        try:
            batch.run_batch(
                Job(),
                SimpleNamespace(ready=lambda _: SimpleNamespace(base_url="http://127.0.0.1:1")),
                "m",
                source,
                tmp_path / "out",
            )
        except Cancelled:
            errors.append("cancelled")

    worker = threading.Thread(target=execute)
    worker.start()
    assert entered.wait(5)
    release.set()
    worker.join(5)
    assert not worker.is_alive() and errors == ["cancelled"] and not spawned
    assert not list(tmp_path.glob("basal-ui-batch-*"))


def test_batch_deep_valid_row_reaches_native_input_without_provenance(tmp_path, monkeypatch):
    from decimal import Decimal
    from pathlib import Path
    from types import SimpleNamespace

    from basal_ui import batch

    state = {"_mcp_source": "business", "value": "leaf"}
    for _ in range(600):
        state = {"child": state}
    body = {
        "state": state,
        "questions": {
            "q": {
                "_mcp_source": {"url": "local"},
                "tools": [{"name": "weather", "inputSchema": {"_mcp_source": "schema"}}],
            }
        },
    }
    row = json.dumps(body).replace('"value": "leaf"', '"value": 0.123456789012345678') + "\n"
    source = tmp_path / "deep.jsonl"
    source.write_text(row)
    observed = []

    def consumer(job, module, args):
        observed.append(json.loads(Path(args[1]).read_text(), parse_float=Decimal))
        return {"items": 1}

    monkeypatch.setattr(batch, "run_tool", consumer)
    batch.run_batch(
        SimpleNamespace(progress=lambda *args: None, check=lambda: None),
        SimpleNamespace(ready=lambda _: SimpleNamespace(base_url="http://127.0.0.1:1")),
        "m",
        source,
        tmp_path / "out",
    )
    sent = observed[0]
    leaf = sent["state"]
    for _ in range(600):
        leaf = leaf["child"]
    assert leaf == {"_mcp_source": "business", "value": Decimal("0.123456789012345678")}
    assert "_mcp_source" not in sent["questions"]["q"]
    assert sent["questions"]["q"]["tools"][0]["inputSchema"]["_mcp_source"] == "schema"
    assert source.read_text() == row


def test_deep_batch_reaches_fake_child_via_native_cli(client, child, tmp_path):
    state = {"_mcp_source": "business"}
    for _ in range(600):
        state = {"child": state}
    body = {
        "state": state,
        "questions": {
            "q": {
                "type": "tool",
                "_mcp_source": {"url": "local"},
                "tools": [{"name": "weather", "inputSchema": {"_mcp_source": "schema"}}],
            }
        },
    }
    original = json.dumps(body) + "\n"
    source = tmp_path / "deep-native.jsonl"
    source.write_text(original)
    assert client.post("/api/batch", json={"id": "mini", "input": str(source)}).status_code == 202
    job = wait_batch(client)
    assert job["phase"] == "done", job
    assert len(child.seen) == 1
    sent = child.seen[0][1]
    assert "_mcp_source" not in sent["questions"]["q"]
    assert sent["questions"]["q"]["tools"][0]["inputSchema"]["_mcp_source"] == "schema"
    leaf = sent["state"]
    for _ in range(600):
        leaf = leaf["child"]
    assert leaf["_mcp_source"] == "business" and source.read_text() == original


def test_rank_passes_through_and_records_history_and_stats(client, child):
    question = {
        "type": "rank",
        "instructions": "Does {candidate} match?",
        "candidates": [{"key": "a", "text": "first", "group": "g"}, {"key": "b", "text": "second"}],
        "prior": {"a": 1.5},
        "top": 2,
    }
    answer = {"type": "rank", "ranking": [{"key": "a", "score": 1.2}], "top": "a", "candidates": 2, "requests": 2}
    child.reply = {"answers": {"q": answer}}
    body = {"state": "test", "questions": {"q": question}}
    response = client.post("/api/decide", json={"model": "mini", **body})
    assert response.json()["answers"]["q"] == answer
    assert child.seen[-1] == ("/v1/systemone", body)
    record = client.get("/api/history").json()["items"][0]
    assert record["answers"]["q"] == {"type": "rank", "best": "a", "confidence": None}
    stored = client.get(f"/api/history/{record['id']}").json()
    assert stored["replay_request"]["questions"]["q"] == question
    assert stored["response"]["answers"]["q"] == answer
    assert client.get("/api/stats").json()["models"]["mini"]["by_type"]["rank"] == {
        "questions": 1,
        "avg_best_prob": None,
    }
