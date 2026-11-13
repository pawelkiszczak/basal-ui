import json
from datetime import datetime

import pytest

from basal_ui import schedules, settings
from tests import test_ui_batch

client = test_ui_batch.client  # reuse the real basal-run and child fixture


def definition(inputs, outputs, **extra):
    return {
        "id": "nightly",
        "model": "mini",
        "input_dir": str(inputs),
        "output_dir": str(outputs),
        "interval": {"every_minutes": 1},
        **extra,
    }


def item(state="s"):
    return json.dumps({"state": state, "question": "q", "options": ["a", "b"]}) + "\n"


def test_route_to_real_batch_history_and_usage(client, child, tmp_path):
    inputs, outputs = tmp_path / "inputs", tmp_path / "outputs"
    inputs.mkdir()
    outputs.mkdir()
    source = inputs / "one.jsonl"
    source.write_text(item())
    response = client.post("/api/schedules", json=definition(inputs, outputs))
    assert response.status_code == 201, response.text
    before = source.read_bytes()
    preview = client.get("/api/schedules/nightly/preview").json()
    assert preview["file"] == str(source) and preview["inspection"]["first"]["format"] == "simple"
    assert source.read_bytes() == before and list(outputs.iterdir()) == [] and child.seen == []
    scheduler = client.app.state.schedules
    result = scheduler.run_due(1000)
    assert result["status"] == "started"
    job = scheduler.active[2]
    job.thread.join(30)
    assert not job.thread.is_alive()
    scheduler.run_due(1001)
    assert (outputs / "one.answers.jsonl").exists()
    run = client.get("/api/schedules/nightly/runs").json()["runs"][0]
    assert (run["rows"], run["errors"], run["model"]) == (1, 0, "mini")
    assert run["duration"] == pytest.approx(job.state()["finished"] - job.state()["started"])
    assert client.get("/api/status").json()["jobs"]["batch"]["phase"] == "done"
    assert client.get("/api/jobs").json()["jobs"]["batch"]["phase"] == "done"
    assert client.get("/api/logs", params={"source": "job:batch"}).json()["lines"]
    assert len(child.seen) == 1
    history = client.get("/api/history").json()
    assert any(row["source"] == "schedule" for row in history["items"])
    assert client.get("/api/stats").json()["models"]["mini"]["requests"] == 1
    assert client.get("/api/traffic").json()["requests"] == 1
    assert scheduler.run_due(1062)["status"] == "no_new_files"
    assert client.get("/api/schedules/nightly/preview").json()["file"] is None
    assert len(child.seen) == 1


def test_bad_input_line_failures_pause_and_resume(client, tmp_path):
    inputs, outputs = tmp_path / "i", tmp_path / "o"
    inputs.mkdir()
    outputs.mkdir()
    for name in ("a", "b", "c"):
        (inputs / (name + ".jsonl")).write_text("\n\nnot json\n")
    assert client.post("/api/schedules", json=definition(inputs, outputs)).status_code == 201
    scheduler = client.app.state.schedules
    scheduler.run_due(1000)
    records = client.get("/api/schedules/nightly/runs").json()["runs"]
    assert len(records) == 3 and all("line 3" in row["error"] for row in records)
    assert client.get("/api/schedules").json()["schedules"][0]["paused"]
    assert client.put("/api/schedules/nightly", json={"paused": False}).status_code == 200
    assert settings.schedule_state("nightly")["failures"] == 0


def test_skip_is_not_a_checkpoint_and_run_now_is_truthful(client, tmp_path):
    inputs, outputs = tmp_path / "i", tmp_path / "o"
    inputs.mkdir()
    outputs.mkdir()
    (inputs / "a.jsonl").write_text(item())
    spec = definition(inputs, outputs, model="missing", paused=True)
    assert client.post("/api/schedules", json=spec).status_code == 201
    result = client.post("/api/schedules/nightly/run").json()
    assert result["status"] == "skipped"
    assert client.get("/api/schedules/nightly/runs").json()["runs"][0]["skipped"] == "model not loaded"
    assert settings.schedule_state("nightly").get("processed", {}) == {}
    assert client.get("/api/status").json()["jobs"].get("batch") is None


def test_daily_catchup_and_empty_scan_clock(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    scheduler = schedules.Schedules(settings, object(), None)
    spec = scheduler.create(definition(tmp_path, tmp_path, interval={"daily_at": "10:00"}, catch_up=True))
    settings.update_schedule_state(
        "nightly", lambda state: state.update(last_scan=datetime(2026, 11, 4, 10).timestamp())
    )
    assert scheduler._due(spec, datetime(2026, 11, 6, 9))
    scheduler.run_due(datetime(2026, 11, 6, 9))
    assert not scheduler._due(spec, datetime(2026, 11, 6, 9, 1))
    assert scheduler._due(spec, datetime(2026, 11, 6, 10))
    spec["catch_up"] = False
    assert not scheduler._due(spec, datetime(2026, 11, 8, 9))


def test_history_stem_rotation_and_corruption(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "alpha.json"))
    scheduler = schedules.Schedules(settings, object(), None)
    spec = scheduler.create(definition(tmp_path, tmp_path))
    for n in range(8):
        row = {"run_id": str(n), "schedule": "nightly", "file": "x" * 180000, "size": n, "mtime": n}
        settings.append_schedule_run(row, spec, checkpoint=True)
    assert settings.schedule_history_path().name == "alpha.schedules.jsonl"
    assert settings.schedule_history_path().stat().st_size <= 1024 * 1024
    assert len(settings.schedule_state("nightly")["processed"]) == 1
    with pytest.raises(ValueError, match="rotation limit"):
        settings.append_schedule_run({"run_id": "oversized", "error": "x" * (1024 * 1024)}, spec)
    assert settings.schedule_history()[-1]["run_id"] == "7"
    settings.schedule_history_path().write_text("bad json\n")
    with pytest.raises(ValueError):
        settings.schedule_history()
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "beta.json"))
    assert settings.schedule_history() == []


def test_lifecycle_owns_timer(tmp_path, monkeypatch):
    import threading

    from fastapi.testclient import TestClient

    from basal_ui import app as app_module
    from tests.test_ui_app import HW, make_fleet

    class TimerTarget:
        active = None

        def __init__(self):
            self.called = threading.Event()

        def run_due(self):
            self.called.set()

        def _finish_active(self):
            pass

    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    monkeypatch.setattr(app_module, "SCHEDULE_INTERVAL_S", 0.001)
    fleet = make_fleet(tmp_path)
    target = TimerTarget()
    app = app_module.create_app(
        catalog_fn=lambda **_: ([], "test"),
        hardware_fn=lambda: HW,
        fleet=fleet,
        stats=fleet.stats,
        history=fleet.history,
        schedules=target,
    )
    with TestClient(app, base_url="http://127.0.0.1"):
        timer = app.state.schedule_thread
        assert target.called.wait(2)
        assert timer.is_alive()
    assert not timer.is_alive()


def test_invalid_values_rejected(tmp_path):
    for interval in (
        {"every_minutes": True},
        {"every_minutes": 0},
        {"every_minutes": float("inf")},
        {"daily_at": "24:00"},
    ):
        with pytest.raises(ValueError):
            schedules.validate(definition(tmp_path, tmp_path, interval=interval))
    with pytest.raises(ValueError):
        schedules.validate(definition(tmp_path, tmp_path, concurrency=True))


def test_queue_drains_and_manual_batch_cannot_start(client, child, tmp_path, monkeypatch):
    import threading

    inputs, outputs = tmp_path / "i", tmp_path / "o"
    inputs.mkdir()
    outputs.mkdir()
    for name in ("b", "a"):
        (inputs / (name + ".jsonl")).write_text(item(name))
    assert (
        client.post("/api/schedules", json=definition(inputs, outputs, interval={"every_minutes": 60})).status_code
        == 201
    )
    fleet = client.app.state.schedules.fleet
    called = threading.Event()
    original = fleet.decide_runner

    def held(*args, **kwargs):
        called.set()
        return original(*args, **kwargs)

    monkeypatch.setattr(fleet, "decide_runner", held)
    child.gate.clear()
    scheduler = client.app.state.schedules
    assert scheduler.run_due(1000)["status"] == "started"
    assert called.wait(10)
    manual = client.post("/api/batch", json={"id": "mini", "input": str(inputs / "a.jsonl")})
    assert manual.status_code == 409
    child.gate.set()
    first = scheduler.active[2]
    first.thread.join(30)
    assert not first.thread.is_alive()
    assert scheduler.run_due(1001)["status"] == "started"
    second = scheduler.active[2]
    second.thread.join(30)
    assert not second.thread.is_alive()
    scheduler.run_due(1002)
    assert [body["state"] for _, body in child.seen] == ["a", "b"]
    assert len(client.get("/api/schedules/nightly/runs").json()["runs"]) == 2


def test_output_creation_race_preserves_existing_data(client, child, tmp_path, monkeypatch):
    import threading

    inputs, outputs = tmp_path / "i", tmp_path / "o"
    inputs.mkdir()
    outputs.mkdir()
    (inputs / "a.jsonl").write_text(item())
    assert client.post("/api/schedules", json=definition(inputs, outputs)).status_code == 201
    scheduler = client.app.state.schedules
    called = threading.Event()
    original = scheduler.fleet.decide_runner

    def held(*args, **kwargs):
        called.set()
        return original(*args, **kwargs)

    monkeypatch.setattr(scheduler.fleet, "decide_runner", held)
    child.gate.clear()
    assert scheduler.run_due(1000)["status"] == "started"
    assert called.wait(10)
    destination = outputs / "a.answers.jsonl"
    destination.write_text("do not overwrite")
    child.gate.set()
    job = scheduler.active[2]
    job.thread.join(30)
    assert not job.thread.is_alive()
    scheduler.run_due(1001)
    assert destination.read_text() == "do not overwrite"
    assert client.get("/api/schedules/nightly/runs").json()["runs"][0]["error"]


def test_cancel_does_not_replace_output(client, child, tmp_path, monkeypatch):
    import threading

    inputs, outputs = tmp_path / "i", tmp_path / "o"
    inputs.mkdir()
    outputs.mkdir()
    (inputs / "a.jsonl").write_text(item())
    destination = outputs / "a.answers.jsonl"
    destination.write_text("prior answers")
    assert client.post("/api/schedules", json=definition(inputs, outputs, overwrite=True)).status_code == 201
    scheduler = client.app.state.schedules
    called = threading.Event()
    original = scheduler.fleet.decide_runner

    def held(*args, **kwargs):
        called.set()
        return original(*args, **kwargs)

    monkeypatch.setattr(scheduler.fleet, "decide_runner", held)
    child.gate.clear()
    assert scheduler.run_due(1000)["status"] == "started"
    assert called.wait(10)
    client.delete("/api/batch")
    child.gate.set()
    job = scheduler.active[2]
    job.thread.join(30)
    assert not job.thread.is_alive()
    scheduler.run_due(1001)
    assert destination.read_text() == "prior answers"
    assert client.get("/api/schedules/nightly/runs").json()["runs"][0]["cancelled"]


def test_concurrent_process_updates_preserve_schedules(tmp_path, monkeypatch):
    import os
    import subprocess
    import sys

    path = tmp_path / "settings.json"
    env = {**os.environ, "BASAL_UI_CONFIG": str(path)}
    program = 'import sys; from basal_ui import settings; settings.update_schedules(lambda rows: rows.append({"id": sys.argv[1]}))'
    children = [subprocess.Popen([sys.executable, "-c", program, str(i)], env=env) for i in range(4)]
    assert [child.wait(timeout=30) for child in children] == [0] * 4
    monkeypatch.setenv("BASAL_UI_CONFIG", str(path))
    assert {row["id"] for row in settings.schedules()} == {"0", "1", "2", "3"}


def test_edit_and_delete_do_not_drop_active_completion(client, child, tmp_path, monkeypatch):
    import threading

    inputs, outputs = tmp_path / "i", tmp_path / "o"
    inputs.mkdir()
    outputs.mkdir()
    (inputs / "a.jsonl").write_text(item("first"))
    (inputs / "b.jsonl").write_text(item("second"))
    assert (
        client.post("/api/schedules", json=definition(inputs, outputs, interval={"every_minutes": 60})).status_code
        == 201
    )
    scheduler = client.app.state.schedules
    started = threading.Event()
    original = scheduler.fleet.decide_runner

    def held(*args, **kwargs):
        started.set()
        return original(*args, **kwargs)

    monkeypatch.setattr(scheduler.fleet, "decide_runner", held)
    child.gate.clear()
    assert scheduler.run_due(1000)["status"] == "started"
    assert started.wait(10)
    assert client.put("/api/schedules/nightly", json={"pattern": "no-match-*"}).status_code == 200
    child.gate.set()
    job = scheduler.active[2]
    job.thread.join(30)
    assert not job.thread.is_alive()
    scheduler.run_due(1001)
    assert len(child.seen) == 1
    assert client.get("/api/schedules/nightly/runs").json()["runs"][0]["rows"] == 1
    assert str((inputs / "a.jsonl").resolve()) in settings.schedule_state("nightly")["processed"]
    assert client.delete("/api/schedules/nightly").status_code == 200
    assert client.get("/api/schedules/nightly/runs").json()["runs"][0]["rows"] == 1


def test_symlink_input_keeps_matching_name(client, tmp_path):
    inputs, outputs = tmp_path / "i", tmp_path / "o"
    inputs.mkdir()
    outputs.mkdir()
    source = tmp_path / "target.jsonl"
    source.write_text(item())
    (inputs / "export.jsonl").symlink_to(source)
    assert client.post("/api/schedules", json=definition(inputs, outputs)).status_code == 201
    scheduler = client.app.state.schedules
    assert scheduler.run_due(1000)["status"] == "started"
    job = scheduler.active[2]
    job.thread.join(30)
    assert not job.thread.is_alive()
    scheduler.run_due(1001)
    assert (outputs / "export.answers.jsonl").is_file()
    assert not (outputs / "target.answers.jsonl").exists()


def test_publication_recovers_checkpoint_after_restart(tmp_path, monkeypatch):
    import os

    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    scheduler = schedules.Schedules(settings, object(), None)
    spec = scheduler.create(definition(tmp_path, tmp_path))
    source, output = tmp_path / "input.jsonl", tmp_path / "input.answers.jsonl"
    source.write_text(item())
    signature = source.stat()
    row = {
        "run_id": "published",
        "schedule": spec["id"],
        "model": "mini",
        "file": str(source),
        "at": 123,
        "duration": 0,
        "size": signature.st_size,
        "mtime": signature.st_mtime_ns,
        "output": str(output),
        "rows": 1,
        "errors": 0,
    }
    temporary = tmp_path / ".basal-ui-schedule-recovery.jsonl"
    temporary.write_text("new answers")
    settings.begin_schedule_publication(spec, row, temporary)
    os.replace(temporary, output)
    data = settings.load()
    data["schedule_publications"]["published"]["owner_pid"] = 2**30
    settings.save(data)
    restarted = schedules.Schedules(settings, object(), None)
    assert settings.schedule_state("nightly")["processed"][str(source)] == [signature.st_size, signature.st_mtime_ns]
    recovered = restarted.runs("nightly")["runs"][0]
    assert recovered["reason"] == "recovered after interruption" and recovered["duration"] is None
    schedules.Schedules(settings, object(), None)
    assert len(settings.schedule_history()) == 1


def test_unpublished_recovery_preserves_prior_output(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    scheduler = schedules.Schedules(settings, object(), None)
    spec = scheduler.create(definition(tmp_path, tmp_path))
    output = tmp_path / "input.answers.jsonl"
    output.write_text("prior answers")
    temporary = tmp_path / ".basal-ui-schedule-recovery.jsonl"
    temporary.write_text("unfinished")
    row = {
        "run_id": "not-published",
        "schedule": spec["id"],
        "model": "mini",
        "file": str(tmp_path / "input.jsonl"),
        "at": 123,
        "duration": 0,
        "size": 5,
        "mtime": 7,
        "output": str(output),
        "rows": 1,
        "errors": 0,
    }
    settings.begin_schedule_publication(spec, row, temporary)
    data = settings.load()
    data["schedule_publications"]["not-published"]["owner_pid"] = 2**30
    settings.save(data)
    schedules.Schedules(settings, object(), None)
    assert output.read_text() == "prior answers"
    assert not temporary.exists()
    assert settings.schedule_state("nightly").get("processed", {}) == {}
    assert settings.schedule_history() == []


def test_cancel_at_publication_preserves_output(client, tmp_path, monkeypatch):
    import threading

    inputs, outputs = tmp_path / "i", tmp_path / "o"
    inputs.mkdir()
    outputs.mkdir()
    (inputs / "a.jsonl").write_text(item())
    output = outputs / "a.answers.jsonl"
    output.write_text("prior")
    assert client.post("/api/schedules", json=definition(inputs, outputs, overwrite=True)).status_code == 201
    ready, release = threading.Event(), threading.Event()
    original = settings.begin_schedule_publication

    def hold(*args):
        original(*args)
        ready.set()
        assert release.wait(10)

    monkeypatch.setattr(settings, "begin_schedule_publication", hold)
    scheduler = client.app.state.schedules
    assert scheduler.run_due(1000)["status"] == "started"
    assert ready.wait(30)
    client.delete("/api/batch")
    release.set()
    job = scheduler.active[2]
    job.thread.join(30)
    assert not job.thread.is_alive()
    scheduler.run_due(1001)
    assert output.read_text() == "prior"
    assert client.get("/api/schedules/nightly/runs").json()["runs"][0]["cancelled"]


def test_scheduled_batch_uses_validated_snapshot(client, child, tmp_path, monkeypatch):
    inputs, outputs = tmp_path / "i", tmp_path / "o"
    inputs.mkdir()
    outputs.mkdir()
    source = inputs / "a.jsonl"
    source.write_text(item("validated"))
    assert client.post("/api/schedules", json=definition(inputs, outputs)).status_code == 201
    scheduler = client.app.state.schedules
    original = scheduler.fleet.start_model_job

    def mutate(*args, **kwargs):
        source.write_text(item("changed"))
        return original(*args, **kwargs)

    monkeypatch.setattr(scheduler.fleet, "start_model_job", mutate)
    assert scheduler.run_due(1000)["status"] == "started"
    job = scheduler.active[2]
    job.thread.join(30)
    assert not job.thread.is_alive()
    scheduler.run_due(1001)
    assert [body["state"] for _, body in child.seen] == ["validated"]
    assert (outputs / "a.answers.jsonl").exists()


def test_invalid_utf8_reports_physical_line(client, tmp_path):
    inputs, outputs = tmp_path / "i", tmp_path / "o"
    inputs.mkdir()
    outputs.mkdir()
    (inputs / "a.jsonl").write_bytes(item().encode() + b"\n\xff\n")
    assert client.post("/api/schedules", json=definition(inputs, outputs)).status_code == 201
    assert client.app.state.schedules.run_due(1000)["status"] == "failed"
    assert "line 3" in client.get("/api/schedules/nightly/runs").json()["runs"][0]["error"]


def test_missing_directory_counts_errors_and_pauses(client, tmp_path):
    inputs, outputs = tmp_path / "i", tmp_path / "o"
    inputs.mkdir()
    outputs.mkdir()
    assert client.post("/api/schedules", json=definition(inputs, outputs)).status_code == 201
    inputs.rmdir()
    scheduler = client.app.state.schedules
    for moment in (1000, 1060, 1120):
        assert scheduler.run_due(moment)["status"] == "failed"
    rows = client.get("/api/schedules/nightly/runs").json()["runs"]
    assert len(rows) == 3 and all("input_dir: missing" in row["error"] for row in rows)
    assert client.get("/api/schedules").json()["schedules"][0]["paused"]
    assert not client.post("/api/schedules/nightly/resume").json()["paused"]
    assert client.post("/api/schedules/nightly/pause").json()["paused"]


def test_output_symlink_to_input_is_refused(client, tmp_path):
    inputs, outputs = tmp_path / "i", tmp_path / "o"
    inputs.mkdir()
    outputs.mkdir()
    source = inputs / "a.jsonl"
    source.write_text(item())
    (outputs / "a.answers.jsonl").symlink_to(source)
    assert client.post("/api/schedules", json=definition(inputs, outputs, overwrite=True)).status_code == 201
    assert client.app.state.schedules.run_due(1000)["status"] == "failed"
    assert source.read_text() == item()
    assert "output must not be the input file" in client.get("/api/schedules/nightly/runs").json()["runs"][0]["error"]


def test_run_now_queues_behind_manual_batch(client, child, tmp_path, monkeypatch):
    import threading

    inputs, outputs = tmp_path / "i", tmp_path / "o"
    inputs.mkdir()
    outputs.mkdir()
    (inputs / "scheduled.jsonl").write_text(item("scheduled"))
    manual = tmp_path / "manual.jsonl"
    manual.write_text(item("manual"))
    assert (
        client.post("/api/schedules", json=definition(inputs, outputs, interval={"every_minutes": 60})).status_code
        == 201
    )
    started = threading.Event()
    child.gate.clear()
    original_wait = child.gate.wait

    def observed_wait(timeout=None):
        started.set()
        return original_wait(timeout)

    monkeypatch.setattr(child.gate, "wait", observed_wait)
    assert client.post("/api/batch", json={"id": "mini", "input": str(manual)}).status_code == 202
    assert started.wait(10)
    assert client.post("/api/schedules/nightly/run").json()["status"] == "queued"
    scheduler = client.app.state.schedules
    manual_job = scheduler.fleet.jobs.get("batch")
    child.gate.set()
    manual_job.thread.join(30)
    assert not manual_job.thread.is_alive()
    assert scheduler.run_due(1001)["status"] == "started"
    scheduled_job = scheduler.active[2]
    scheduled_job.thread.join(30)
    assert not scheduled_job.thread.is_alive()
    scheduler.run_due(1002)
    assert [body["state"] for _, body in child.seen] == ["manual", "scheduled"]
    assert len(client.get("/api/history").json()["items"]) == 1


def test_partial_error_summaries_pause_and_preserve_outputs(client, child, tmp_path):
    inputs, outputs = tmp_path / "i", tmp_path / "o"
    inputs.mkdir()
    outputs.mkdir()
    for name in ("a", "b", "c"):
        (inputs / f"{name}.jsonl").write_text(item(name))
        (outputs / f"{name}.answers.jsonl").write_text("prior")
    child.reply = {"error": "synthetic upstream error"}
    assert client.post("/api/schedules", json=definition(inputs, outputs, overwrite=True)).status_code == 201
    scheduler = client.app.state.schedules
    assert scheduler.run_due(1000)["status"] == "started"
    for offset in (1, 2, 3):
        job = scheduler.active[2]
        job.thread.join(30)
        assert not job.thread.is_alive()
        assert job.state()["phase"] == "done_with_warnings"
        scheduler.run_due(1000 + offset)
    rows = client.get("/api/schedules/nightly/runs").json()["runs"]
    assert len(rows) == 3 and all(row["errors"] == 1 and row["error"] for row in rows)
    assert client.get("/api/schedules").json()["schedules"][0]["paused"]
    assert all((outputs / f"{name}.answers.jsonl").read_text() == "prior" for name in ("a", "b", "c"))


def test_cross_process_publication_recovery(tmp_path, monkeypatch):
    import os
    import subprocess
    import sys

    config = tmp_path / "settings.json"
    monkeypatch.setenv("BASAL_UI_CONFIG", str(config))
    scheduler = schedules.Schedules(settings, object(), None)
    spec = scheduler.create(definition(tmp_path, tmp_path))
    source = tmp_path / "input.jsonl"
    source.write_text(item())
    program = """import os, sys
from pathlib import Path
from basal_ui import settings
root=Path(sys.argv[1]); source=root/'input.jsonl'; output=root/'input.answers.jsonl'
spec=settings.schedules()[0]; stat=source.stat()
row={'run_id':'child-run','schedule':spec['id'],'model':spec['model'],'file':str(source),'at':1,'duration':0,'size':stat.st_size,'mtime':stat.st_mtime_ns,'output':str(output),'rows':1,'errors':0}
temporary=root/'.basal-ui-schedule-child.jsonl'; temporary.write_text('answers')
settings.begin_schedule_publication(spec,row,temporary); os.replace(temporary,output)
"""
    env = {**os.environ, "BASAL_UI_CONFIG": str(config)}
    subprocess.run([sys.executable, "-c", program, str(tmp_path)], env=env, check=True, timeout=30)
    recovered = schedules.Schedules(settings, object(), None)
    assert recovered.runs(spec["id"])["runs"][0]["reason"] == "recovered after interruption"
    assert settings.schedule_state(spec["id"])["processed"][str(source)] == [
        source.stat().st_size,
        source.stat().st_mtime_ns,
    ]


def test_delete_while_running_retains_completion_history(client, child, tmp_path, monkeypatch):
    import threading

    inputs, outputs = tmp_path / "i", tmp_path / "o"
    inputs.mkdir()
    outputs.mkdir()
    (inputs / "a.jsonl").write_text(item())
    assert client.post("/api/schedules", json=definition(inputs, outputs)).status_code == 201
    scheduler = client.app.state.schedules
    started = threading.Event()
    original = scheduler.fleet.decide_runner

    def held(*args, **kwargs):
        started.set()
        return original(*args, **kwargs)

    monkeypatch.setattr(scheduler.fleet, "decide_runner", held)
    child.gate.clear()
    assert scheduler.run_due(1000)["status"] == "started"
    assert started.wait(10)
    assert client.delete("/api/schedules/nightly").status_code == 200
    child.gate.set()
    job = scheduler.active[2]
    job.thread.join(30)
    assert not job.thread.is_alive()
    scheduler.run_due(1001)
    assert (outputs / "a.answers.jsonl").is_file()
    assert client.get("/api/schedules/nightly/runs").json()["runs"][0]["rows"] == 1
    assert client.get("/api/schedules").json()["schedules"] == []
