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
    settings.update_schedule_state(
        "nightly",
        lambda state: state.update(scan={"spec": spec, "at": 100, "forced": False, "items": [str(source), "next"]}),
    )
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
        "_matched": str(source),
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
    assert settings.schedule_state("nightly")["scan"]["items"] == ["next"]
    recovered = restarted.runs("nightly")["runs"][0]
    assert recovered["reason"] == "recovered after interruption" and recovered["duration"] is None
    schedules.Schedules(settings, object(), None)
    assert len(settings.schedule_history()) == 1


@pytest.mark.parametrize(
    ("handle", "wait", "error", "recovered"),
    [(42, 0x102, 0, False), (42, 0, 0, True), (0, None, 5, False), (0, None, 87, True), (42, 0xFFFFFFFF, 0, False)],
)
def test_windows_publication_recovery_queries_process_without_signalling(
    tmp_path, monkeypatch, handle, wait, error, recovered
):
    import ctypes
    import os
    from types import SimpleNamespace

    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    scheduler = schedules.Schedules(settings, object(), None)
    spec = scheduler.create(definition(tmp_path, tmp_path))
    source, output = tmp_path / "input.jsonl", tmp_path / "input.answers.jsonl"
    source.write_text(item())
    stat = source.stat()
    row = {
        "run_id": "published",
        "schedule": spec["id"],
        "model": "mini",
        "file": str(source),
        "at": 123,
        "duration": 0,
        "size": stat.st_size,
        "mtime": stat.st_mtime_ns,
        "output": str(output),
        "rows": 1,
        "errors": 0,
    }
    temporary = tmp_path / ".basal-ui-schedule-recovery.jsonl"
    temporary.write_text("answers")
    settings.begin_schedule_publication(spec, row, temporary)
    os.replace(temporary, output)

    class Function:
        def __init__(self, function):
            self.function = function

        def __call__(self, *args):
            return self.function(*args)

    closed = []

    def open_process(access, inherit, pid):
        assert (access, inherit, pid) == (0x00100000, False, os.getpid())
        return handle

    def wait_for_process(actual_handle, timeout):
        assert (actual_handle, timeout) == (handle, 0)
        return wait

    kernel = SimpleNamespace(
        OpenProcess=Function(open_process),
        WaitForSingleObject=Function(wait_for_process),
        CloseHandle=Function(closed.append),
    )
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: kernel, raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: error, raising=False)
    monkeypatch.setattr(settings.sys, "platform", "win32")
    monkeypatch.setattr(os, "kill", lambda *_: pytest.fail("process was signalled"))
    settings.recover_schedule_publications()
    assert closed == ([handle] if handle else [])
    assert bool(settings.schedule_history()) is recovered
    assert bool(settings.load().get("schedule_publications")) is not recovered
    if recovered:
        assert settings.schedule_state("nightly")["processed"][str(source)] == [stat.st_size, stat.st_mtime_ns]
    assert output.read_text() == "answers"


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


def test_forced_rescan_queues_file_arriving_during_active_job(client, child, tmp_path, monkeypatch):
    import threading

    inputs, outputs = tmp_path / "i", tmp_path / "o"
    inputs.mkdir()
    outputs.mkdir()
    (inputs / "a.jsonl").write_text(item("a"))
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
    assert scheduler.run_now("nightly", 1000.5)["status"] == "no_new_files"
    (inputs / "b.jsonl").write_text(item("b"))
    assert scheduler.run_now("nightly", 1001)["status"] == "queued"
    assert scheduler.run_now("nightly", 1002)["status"] == "queued"
    child.gate.set()
    scheduler.active[2].thread.join(30)
    assert scheduler.run_due(1003)["status"] == "started"
    scheduler.active[2].thread.join(30)
    scheduler.run_due(1004)
    assert [body["state"] for _, body in child.seen] == ["a", "b"]


def test_restart_recovers_daily_scan_queue_after_first_file(client, child, tmp_path):
    inputs, outputs = tmp_path / "i", tmp_path / "o"
    inputs.mkdir()
    outputs.mkdir()
    for name in ("a", "b"):
        (inputs / f"{name}.jsonl").write_text(item(name))
    assert (
        client.post("/api/schedules", json=definition(inputs, outputs, interval={"daily_at": "10:00"})).status_code
        == 201
    )
    scheduler = client.app.state.schedules
    assert scheduler.run_due(datetime(2026, 11, 6, 10))["status"] == "started"
    scheduler.active[2].thread.join(30)
    scheduler._finish_active()
    restarted = schedules.Schedules(settings, scheduler.fleet, scheduler.start_batch)
    assert restarted.run_due(datetime(2026, 11, 6, 11))["status"] == "started"
    restarted.active[2].thread.join(30)
    restarted.run_due(datetime(2026, 11, 6, 11, 1))
    assert [body["state"] for _, body in child.seen] == ["a", "b"]
    assert not restarted._due(restarted.get("nightly"), datetime(2026, 11, 6, 11, 2))


@pytest.mark.parametrize("first", [True, False])
def test_nested_json_is_recorded_at_physical_line_without_inference(client, child, tmp_path, first):
    import sys

    inputs, outputs = tmp_path / "i", tmp_path / "o"
    inputs.mkdir()
    outputs.mkdir()
    depth = sys.getrecursionlimit() * 16
    nested = '{"state":' + "[" * depth + "0" + "]" * depth + ',"question":"q","options":["a","b"]}'
    (inputs / "a.jsonl").write_text(("\n" if first else item() + "\n") + nested + "\n")
    assert client.post("/api/schedules", json=definition(inputs, outputs)).status_code == 201
    assert client.app.state.schedules.run_due(1000)["status"] == "failed"
    row = client.get("/api/schedules/nightly/runs").json()["runs"][0]
    assert f"line {2 if first else 3}" in row["error"]
    assert child.seen == [] and list(outputs.iterdir()) == []


def test_schedule_reads_fail_closed_for_corrupt_and_transient_unreadable_settings(tmp_path, monkeypatch):
    config = tmp_path / "settings.json"
    monkeypatch.setenv("BASAL_UI_CONFIG", str(config))
    assert settings.schedules() == []
    config.write_text("{broken")
    for action in (
        settings.schedules,
        lambda: settings.schedule_state("x"),
        settings.schedule_history,
        settings.recover_schedule_publications,
        lambda: settings.update_schedules(lambda rows: rows.clear()),
    ):
        with pytest.raises(settings.CorruptSettings):
            action()
    assert config.read_text() == "{broken"
    config.write_text('{"keep": "important", "schedules": []}')
    before = config.read_bytes()
    original = type(config).read_text
    failed = False

    def transient(path, *args, **kwargs):
        nonlocal failed
        if path == config and not failed:
            failed = True
            raise PermissionError("transient read failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(type(config), "read_text", transient)
    with pytest.raises(PermissionError):
        settings.update_schedules(lambda rows: rows.append({"id": "x"}))
    assert config.read_bytes() == before
    config.unlink()
    config.symlink_to(tmp_path / "missing.json")
    with pytest.raises(settings.CorruptSettings):
        settings.schedules()
    assert config.is_symlink()


@pytest.mark.parametrize("change", ["pause", "edit", "delete"])
def test_other_process_change_before_reservation_prevents_start(client, child, tmp_path, monkeypatch, change):
    import os
    import subprocess
    import sys

    inputs, outputs = tmp_path / "i", tmp_path / "o"
    inputs.mkdir()
    outputs.mkdir()
    (inputs / "a.jsonl").write_text(item())
    assert client.post("/api/schedules", json=definition(inputs, outputs)).status_code == 201
    scheduler = client.app.state.schedules
    original = scheduler.fleet.ready

    def mutate_elsewhere(model):
        runner = original(model)
        mutation = {
            "pause": "rows[0].update(paused=True)",
            "edit": "rows[0].update(pattern='other*')",
            "delete": "rows.clear()",
        }[change]
        program = f"from basal_ui import settings; settings.update_schedules(lambda rows: {mutation})"
        subprocess.run([sys.executable, "-c", program], env=os.environ.copy(), check=True, timeout=30)
        return runner

    monkeypatch.setattr(scheduler.fleet, "ready", mutate_elsewhere)
    assert scheduler.run_due(1000)["status"] == "no_new_files"
    rows = client.get("/api/schedules").json()["schedules"]
    if change == "delete":
        assert rows == []
    elif change == "pause":
        assert rows[0]["paused"]
    else:
        assert rows[0]["pattern"] == "other*"
    assert child.seen == [] and client.get("/api/jobs").json()["jobs"].get("batch") is None


def test_cancel_drains_incomplete_proxy_connections_without_abandoning_inference(client, child, tmp_path, monkeypatch):
    import contextlib
    import queue
    import socket
    import threading
    from urllib.parse import urlsplit

    import httpx

    from basal_ui import batch

    inputs, outputs = tmp_path / "i", tmp_path / "o"
    inputs.mkdir()
    outputs.mkdir()
    (inputs / "a.jsonl").write_text(item())
    assert client.post("/api/schedules", json=definition(inputs, outputs)).status_code == 201
    urls, started = queue.Queue(), threading.Event()
    original_proxy = batch.comparison_proxy
    original_decide = client.app.state.schedules.fleet.decide_runner

    @contextlib.contextmanager
    def observe(*args, **kwargs):
        with original_proxy(*args, **kwargs) as url:
            urls.put(url)
            yield url

    def held(*args, **kwargs):
        started.set()
        return original_decide(*args, **kwargs)

    monkeypatch.setattr(batch, "comparison_proxy", observe)
    monkeypatch.setattr(client.app.state.schedules.fleet, "decide_runner", held)
    child.gate.clear()
    scheduler = client.app.state.schedules
    assert scheduler.run_due(1000)["status"] == "started"
    url = urls.get(timeout=10)
    assert started.wait(10)
    target = urlsplit(url)
    header = socket.create_connection((target.hostname, target.port), timeout=3)
    body = socket.create_connection((target.hostname, target.port), timeout=3)
    try:
        header.sendall(b"POST /v1/systemone HTTP/1.1\r\nHost: 127.0.0.1")
        body.sendall(
            f"POST /v1/systemone HTTP/1.1\r\nHost: 127.0.0.1:{target.port}\r\nContent-Length: 100\r\n\r\n{{".encode()
        )
        assert httpx.post(url + "/v1/systemone", headers={"Host": "evil.example"}, json={}).status_code == 403
        client.delete("/api/batch")
        job = scheduler.active[2]
        assert job.thread.is_alive()  # the live inference still owns the runner
        assert scheduler.fleet.leases
        child.gate.set()
        job.thread.join(10)
        assert not job.thread.is_alive()
        assert not [t for t in threading.enumerate() if t.name == "basal-ui-scheduled-batch"]
        header.settimeout(2)
        body.settimeout(2)
        assert header.recv(1) == b"" and body.recv(1) == b""
    finally:
        child.gate.set()
        header.close()
        body.close()


def test_incomplete_proxy_header_hits_absolute_deadline(monkeypatch):
    import socket
    from urllib.parse import urlsplit

    from basal_ui import batch

    monkeypatch.setattr(batch, "PREFLIGHT_TIMEOUT_S", 0.05)
    with batch.comparison_proxy(object(), object(), "model") as url:
        target = urlsplit(url)
        with socket.create_connection((target.hostname, target.port), timeout=2) as connection:
            connection.sendall(b"POST /v1/systemone HTTP/1.1\r\nHost:")
            connection.settimeout(2)
            assert connection.recv(1) == b""


def test_resumed_scan_missing_folder_respects_interval(client, child, tmp_path):
    inputs, outputs = tmp_path / "i", tmp_path / "o"
    inputs.mkdir()
    outputs.mkdir()
    for name in ("b", "c"):
        (inputs / f"{name}.jsonl").write_text(item(name))
    assert (
        client.post("/api/schedules", json=definition(inputs, outputs, interval={"every_minutes": 60})).status_code
        == 201
    )
    scheduler = client.app.state.schedules
    assert scheduler.run_due(1000)["status"] == "started"
    scheduler.active[2].thread.join(30)
    scheduler._finish_active()
    for path in inputs.iterdir():
        path.unlink()
    inputs.rmdir()
    restarted = schedules.Schedules(settings, scheduler.fleet, scheduler.start_batch)
    assert restarted.run_due(1100)["status"] == "failed"
    assert restarted.run_due(1130)["status"] == "no_new_files"
    assert restarted.run_due(1160)["status"] == "no_new_files"
    rows = client.get("/api/schedules/nightly/runs").json()["runs"]
    assert sum(bool(row.get("error")) for row in rows) == 1
    assert not restarted.get("nightly")["paused"]
    assert restarted.run_due(4700)["status"] == "failed"


def test_restart_preserves_accepted_order_and_excludes_later_arrival(client, child, tmp_path):
    inputs, outputs = tmp_path / "i", tmp_path / "o"
    inputs.mkdir()
    outputs.mkdir()
    for name in ("b", "c"):
        (inputs / f"{name}.jsonl").write_text(item(name))
    assert (
        client.post("/api/schedules", json=definition(inputs, outputs, interval={"every_minutes": 60})).status_code
        == 201
    )
    scheduler = client.app.state.schedules
    assert scheduler.run_due(1000)["status"] == "started"
    scheduler.active[2].thread.join(30)
    scheduler._finish_active()
    (inputs / "a.jsonl").write_text(item("a"))
    restarted = schedules.Schedules(settings, scheduler.fleet, scheduler.start_batch)
    assert restarted.run_due(1100)["status"] == "started"
    restarted.active[2].thread.join(30)
    restarted.run_due(1101)
    assert [body["state"] for _, body in child.seen] == ["b", "c"]
    assert restarted.run_due(1102)["status"] == "no_new_files"
    assert restarted.run_due(4600)["status"] == "started"
    restarted.active[2].thread.join(30)
    restarted.run_due(4601)
    assert [body["state"] for _, body in child.seen] == ["b", "c", "a"]


def test_restart_does_not_readmit_settled_skip_when_output_disappears(client, child, tmp_path, monkeypatch):
    import threading

    inputs, outputs = tmp_path / "i", tmp_path / "o"
    inputs.mkdir()
    outputs.mkdir()
    for name in ("b", "c"):
        (inputs / f"{name}.jsonl").write_text(item(name))
    existing = outputs / "b.answers.jsonl"
    existing.write_text("prior answers")
    assert (
        client.post("/api/schedules", json=definition(inputs, outputs, interval={"every_minutes": 60})).status_code
        == 201
    )
    scheduler = client.app.state.schedules
    entered, release = threading.Event(), threading.Event()
    original = scheduler.fleet.decide_runner

    def hold(*args, **kwargs):
        entered.set()
        assert release.wait(30)
        return original(*args, **kwargs)

    monkeypatch.setattr(scheduler.fleet, "decide_runner", hold)
    try:
        assert scheduler.run_due(1000)["status"] == "started"
        assert entered.wait(30)
        assert settings.schedule_state("nightly")["scan"]["items"] == [str(inputs / "c.jsonl")]
        assert str((inputs / "b.jsonl").resolve()) not in settings.schedule_state("nightly").get("processed", {})
        existing.unlink()
        assert client.delete("/api/batch").status_code == 200
        release.set()
        scheduler.active[2].thread.join(30)
        assert not scheduler.active[2].thread.is_alive()
    finally:
        release.set()
        monkeypatch.setattr(scheduler.fleet, "decide_runner", original)
    scheduler.active = None  # simulate loss of the in-memory job during restart
    restarted = schedules.Schedules(settings, scheduler.fleet, scheduler.start_batch)
    assert restarted.run_due(1100)["status"] == "started"
    restarted.active[2].thread.join(30)
    restarted.run_due(1101)
    assert not existing.exists()
    assert [body["state"] for _, body in child.seen][-1] == "c"
    assert restarted.run_due(1102)["status"] == "no_new_files"
    assert restarted.run_due(4600)["status"] == "started"
    restarted.active[2].thread.join(30)
    restarted.run_due(4601)
    assert [body["state"] for _, body in child.seen][-2:] == ["c", "b"]


def test_restart_does_not_readmit_changed_settled_symlink_before_remaining_queue(client, child, tmp_path):
    inputs, outputs = tmp_path / "i", tmp_path / "o"
    inputs.mkdir()
    outputs.mkdir()
    target = tmp_path / "target.jsonl"
    target.write_text(item("b"))
    (inputs / "b.jsonl").symlink_to(target)
    (inputs / "c.jsonl").write_text(item("c"))
    assert (
        client.post(
            "/api/schedules", json=definition(inputs, outputs, interval={"every_minutes": 60}, overwrite=True)
        ).status_code
        == 201
    )
    scheduler = client.app.state.schedules
    assert scheduler.run_due(1000)["status"] == "started"
    scheduler.active[2].thread.join(30)
    scheduler._finish_active()
    assert settings.schedule_state("nightly")["scan"]["items"] == [str(inputs / "c.jsonl")]
    target.write_text(item("changed b"))
    restarted = schedules.Schedules(settings, scheduler.fleet, scheduler.start_batch)
    assert restarted.run_due(1100)["status"] == "started"
    restarted.active[2].thread.join(30)
    restarted.run_due(1101)
    assert [body["state"] for _, body in child.seen] == ["b", "c"]
    assert restarted.run_due(1102)["status"] == "no_new_files"
    assert restarted.run_due(4600)["status"] == "started"
    restarted.active[2].thread.join(30)
    restarted.run_due(4601)
    assert [body["state"] for _, body in child.seen] == ["b", "c", "changed b"]
    assert (outputs / "b.answers.jsonl").is_file()
    assert all("_matched" not in row for row in settings.schedule_history())


def test_stalled_response_delivery_is_drained_after_inference(monkeypatch):
    import contextlib
    import queue
    import socket
    import threading
    from urllib.parse import urlsplit

    import httpx

    from basal_ui import batch

    monkeypatch.setattr(batch, "DELIVERY_TIMEOUT_S", 0.2)
    completed, stop = threading.Event(), threading.Event()
    urls = queue.Queue()

    class Fleet:
        def decide_runner(self, *_args, **_kwargs):
            completed.set()
            return httpx.Response(200, content=b"x" * (32 * 1024 * 1024))

    def serve():
        with batch.comparison_proxy(Fleet(), object(), "model") as url:
            urls.put(url)
            stop.wait(10)

    owner = threading.Thread(target=serve)
    owner.start()
    target = urlsplit(urls.get(timeout=5))
    connection = socket.socket()
    connection.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    connection.settimeout(5)
    connection.connect((target.hostname, target.port))
    try:
        connection.sendall(
            f"POST /v1/systemone HTTP/1.1\r\nHost: 127.0.0.1:{target.port}\r\nContent-Length: 2\r\n\r\n{{}}".encode()
        )
        assert completed.wait(5)
        assert connection.recv(1)
        handlers = [t for t in threading.enumerate() if "process_request_thread" in t.name]
        assert handlers
        for handler in handlers:
            handler.join(5)
            assert not handler.is_alive()
        stop.set()
        owner.join(10)
        assert not owner.is_alive()
        assert not [t for t in threading.enumerate() if t.name == "basal-ui-scheduled-batch"]
    finally:
        stop.set()
        with contextlib.suppress(OSError):
            connection.close()
        owner.join(10)


@pytest.mark.parametrize(
    "kind,fallback,allowed",
    [("blend", None, False), ("escalate", {"url": "https://example.invalid"}, False), ("escalate", None, True)],
)
def test_cascade_batch_eligibility_at_both_routes(client, child, tmp_path, kind, fallback, allowed):
    inputs, outputs = tmp_path / "i", tmp_path / "o"
    inputs.mkdir()
    outputs.mkdir()
    source = inputs / "a.jsonl"
    source.write_text(item())
    scheduler = client.app.state.schedules
    runner = scheduler.fleet.runners["mini"]
    runner.models_entry = {"kind": "cascade", "early_exit": []}
    runner.spec = {"kind": kind, "fallback": fallback}
    assert client.post("/api/schedules", json=definition(inputs, outputs)).status_code == 201
    result = scheduler.run_due(1000)
    if allowed:
        assert result["status"] == "started"
        scheduler.active[2].thread.join(30)
        scheduler.run_due(1001)
        assert len(child.seen) == 1
        history = client.get("/api/history").json()
        stats = client.get("/api/stats").json()
        traffic = client.get("/api/traffic").json()["requests"]
        manual = client.post(
            "/api/batch", json={"id": "mini", "input": str(source), "output": str(tmp_path / "manual.jsonl")}
        )
        assert manual.status_code == 202
        scheduler.fleet.jobs.get("batch").thread.join(30)
        assert len(child.seen) == 2
        assert client.get("/api/history").json() == history
        assert client.get("/api/stats").json() == stats
        assert client.get("/api/traffic").json()["requests"] == traffic
    else:
        assert result["status"] == "failed"
        assert (
            "Batch requires an escalation Cascade without fallback"
            in client.get("/api/schedules/nightly/runs").json()["runs"][0]["error"]
        )
        manual = client.post("/api/batch", json={"id": "mini", "input": str(source)})
        assert manual.status_code == 400 and "Batch requires an escalation Cascade without fallback" in manual.text
        assert child.seen == [] and client.get("/api/jobs").json()["jobs"].get("batch") is None


@pytest.mark.parametrize("failure_stage", ["history_write", "final_settings_save"])
def test_history_interruption_does_not_replay_settled_skip(client, child, tmp_path, monkeypatch, failure_stage):
    inputs, outputs = tmp_path / "i", tmp_path / "o"
    inputs.mkdir()
    outputs.mkdir()
    for name in ("b", "c"):
        (inputs / f"{name}.jsonl").write_text(item(name))
    protected = outputs / "b.answers.jsonl"
    protected.write_text("existing answers")
    assert client.post("/api/schedules", json=definition(inputs, outputs)).status_code == 201
    scheduler = client.app.state.schedules
    write_history, save_settings = settings._write_schedule_history, settings.save
    interrupted = history_written = False

    def write(rows):
        nonlocal interrupted, history_written
        if failure_stage == "history_write" and not interrupted:
            interrupted = True
            raise OSError("history persistence interrupted")
        write_history(rows)
        history_written = True

    def save(data):
        nonlocal interrupted
        if (
            failure_stage == "final_settings_save"
            and history_written
            and not interrupted
            and "schedule_history_pending" not in data
        ):
            interrupted = True
            raise OSError("history persistence interrupted")
        save_settings(data)

    monkeypatch.setattr(settings, "_write_schedule_history", write)
    monkeypatch.setattr(settings, "save", save)
    with pytest.raises(OSError, match="history persistence interrupted"):
        scheduler.run_due(1000)
    assert scheduler.run_due(1001)["status"] == "started"
    job = scheduler.active[2]
    job.thread.join(30)
    assert not job.thread.is_alive()
    scheduler.run_due(1002)
    rows = client.get("/api/schedules/nightly/runs").json()["runs"]
    assert len(rows) == 2
    assert [row["file"] for row in rows] == [str(inputs / "c.jsonl"), str(inputs / "b.jsonl")]
    assert rows[1]["skipped"] == "output exists" and rows[0]["rows"] == 1
    assert protected.read_text() == "existing answers"
    assert [body["state"] for _, body in child.seen] == ["c"]
    assert not client.get("/api/schedules").json()["schedules"][0]["paused"]
