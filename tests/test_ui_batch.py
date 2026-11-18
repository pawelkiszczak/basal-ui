import json
import os
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
