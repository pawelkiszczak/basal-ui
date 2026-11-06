import json

import pytest
from fastapi.testclient import TestClient

from basal_ui import schedules, settings
from basal_ui.app import create_app
from tests.test_ui_app import HW, app_for, make_fleet


class Job:
    def __init__(self, result=None, error=None):
        self.value = {"running": False, "phase": "error" if error else "done", "result": result or {}, "error": error}

    def state(self):
        return self.value


class Jobs:
    def __init__(self, fleet):
        self.fleet = fleet

    def get(self, _kind):
        return self.fleet._job


class Fleet:
    def __init__(self):
        self._job = None
        self.jobs = Jobs(self)
        self.loaded = True

    def ready(self, model):
        if not self.loaded:
            raise KeyError(model)
        return object()


def definition(input_dir, output_dir, **extra):
    return {
        "id": "nightly",
        "model": "basal/mini",
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "interval": {"every_minutes": 1},
        **extra,
    }


def test_changed_files_start_batch_and_are_recorded_once(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    inputs, outputs = tmp_path / "in", tmp_path / "out"
    inputs.mkdir()
    outputs.mkdir()
    source = inputs / "requests.jsonl"
    source.write_text('{"state":"hello"}\n')
    fleet = Fleet()
    calls = []

    def start(spec, path, output):
        calls.append((spec, path, output))
        fleet._job = Job({"items": 1})
        return fleet._job.state()

    runner = schedules.Schedules(settings, fleet, start)
    spec = runner.create(definition(inputs, outputs, preset="triage", lang="pl"))
    assert runner.run_due(1000)["running"] is False
    runner.run_due(1001)
    assert calls == [(spec, source, outputs / "requests.answers.jsonl")]
    assert runner.runs(spec["id"])["runs"][0]["rows"] == 1
    assert runner.run_due(1061) is None
    assert len(calls) == 1


def test_model_missing_records_skip_without_starting(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    inputs, outputs = tmp_path / "in", tmp_path / "out"
    inputs.mkdir()
    outputs.mkdir()
    (inputs / "one.jsonl").write_text('{"state":"x"}\n')
    fleet = Fleet()
    fleet.loaded = False
    runner = schedules.Schedules(settings, fleet, lambda *_: pytest.fail("must not start"))
    spec = runner.create(definition(inputs, outputs))
    runner.run_due(1000)
    row = runner.runs(spec["id"])["runs"][0]
    assert row["skipped"] == "model not loaded"


def test_three_consecutive_start_failures_pause_schedule(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    inputs, outputs = tmp_path / "in", tmp_path / "out"
    inputs.mkdir()
    outputs.mkdir()
    fleet = Fleet()
    runner = schedules.Schedules(settings, fleet, lambda *_: (_ for _ in ()).throw(RuntimeError("failed")))
    spec = runner.create(definition(inputs, outputs))
    for number in range(3):
        (inputs / f"{number}.jsonl").write_text('{"state":"x"}')
        runner.run_due(2000 + number * 60)
        saved = runner.get(spec["id"])
        assert saved["paused"] is (number == 2)
    assert "three consecutive" in saved["paused_reason"]


def test_validation_and_history_rotation(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    with pytest.raises(ValueError, match="path separators"):
        schedules.validate(definition(tmp_path, tmp_path, pattern="../*.jsonl"))
    for index in range(8):
        settings.append_schedule_run({"schedule": "s", "at": index, "file": "x" * 180_000})
    path = settings.schedule_history_path()
    assert path.stat().st_size <= 1024 * 1024
    assert json.loads(path.read_text().splitlines()[-1])["at"] == 7


def test_daily_catch_up_only_runs_when_enabled(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    moment = schedules.datetime(2026, 11, 2, 10, 0)
    spec = schedules.validate(
        {
            "model": "m",
            "input_dir": str(tmp_path),
            "output_dir": str(tmp_path),
            "interval": {"daily_at": "09:00"},
        }
    )
    assert not schedules.Schedules(settings, Fleet(), lambda *_: None)._due(spec, moment, False)
    spec["catch_up"] = True
    assert schedules.Schedules(settings, Fleet(), lambda *_: None)._due(spec, moment, False)


@pytest.mark.parametrize("catch_up", [False, True])
def test_missed_daily_run_catches_up_before_todays_slot(tmp_path, monkeypatch, catch_up):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    runner = schedules.Schedules(settings, Fleet(), lambda *_: None)
    spec = runner.create(definition(tmp_path, tmp_path, interval={"daily_at": "10:00"}, catch_up=catch_up))
    settings.append_schedule_run(
        {"schedule": spec["id"], "at": schedules.datetime(2026, 11, 4, 10).timestamp(), "file": "old.jsonl"}
    )
    restarted = schedules.datetime(2026, 11, 6, 9)
    assert runner._due(spec, restarted, False) is catch_up
    if catch_up:
        settings.append_schedule_run({"schedule": spec["id"], "at": restarted.timestamp(), "file": "caught-up.jsonl"})
        assert not runner._due(spec, restarted, False)
        assert runner._due(spec, schedules.datetime(2026, 11, 6, 10), False)


def test_schedule_routes_create_and_run_now(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    inputs, outputs = tmp_path / "in", tmp_path / "out"
    inputs.mkdir()
    outputs.mkdir()
    (inputs / "one.jsonl").write_text('{"state":"x"}\n')
    app, fleet = app_for(tmp_path, [])
    try:
        with TestClient(app, base_url="http://127.0.0.1") as client:
            response = client.post("/api/schedules", json=definition(inputs, outputs))
            assert response.status_code == 201
            schedule_id = response.json()["id"]
            run = client.post(f"/api/schedules/{schedule_id}/run")
            assert run.status_code == 202
            rows = client.get(f"/api/schedules/{schedule_id}/runs").json()["runs"]
            assert rows[0]["skipped"] == "model not loaded"
            assert client.get("/api/status").json()["jobs"].get("batch") is None
    finally:
        fleet.shutdown()


def test_testclient_run_now_starts_a_visible_batch_job(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    inputs, outputs = tmp_path / "in", tmp_path / "out"
    inputs.mkdir()
    outputs.mkdir()
    (inputs / "one.jsonl").write_text('{"state":"x"}\n')
    fleet = make_fleet(tmp_path)
    fleet.ready = lambda _model: object()

    def start_batch(spec, source, output):
        return fleet.jobs.start("batch", spec["model"], source.name, lambda _job: {"items": 1, "output": str(output)})

    app = create_app(
        catalog_fn=lambda **_: ([], "hub"),
        hardware_fn=lambda: HW,
        fleet=fleet,
        stats=fleet.stats,
        history=fleet.history,
        start_batch=start_batch,
    )
    try:
        with TestClient(app, base_url="http://127.0.0.1") as client:
            bad = client.post("/api/schedules", json={**definition(inputs, outputs), "pattern": "../*.jsonl"})
            assert bad.status_code == 400
            created = client.post("/api/schedules", json=definition(inputs, outputs))
            assert created.status_code == 201
            schedule_id = created.json()["id"]
            assert client.post(f"/api/schedules/{schedule_id}/run").status_code == 202
            job = client.get("/api/status").json()["jobs"]["batch"]
            assert job["model"] == "basal/mini"
            assert job["phase"] in ("starting", "done")
    finally:
        fleet.shutdown()


def test_due_scan_drains_in_order_before_next_interval_and_persists_signatures(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    inputs, outputs = tmp_path / "in", tmp_path / "out"
    inputs.mkdir()
    outputs.mkdir()
    for name in ("b", "a"):
        (inputs / f"{name}.jsonl").write_text('{"state":"x"}')
    fleet = Fleet()
    calls = []

    def start(spec, path, output):
        calls.append(path.name)
        fleet._job = Job({"items": 1, "errors": 0})
        return fleet._job.state()

    runner = schedules.Schedules(settings, fleet, start)
    spec = runner.create(definition(inputs, outputs, overwrite=True, interval={"every_minutes": 60}))
    runner.run_due(1000)
    runner.run_due(1001)
    runner.run_due(1002)
    assert calls == ["a.jsonl", "b.jsonl"]
    rows = runner.runs(spec["id"])["runs"]
    assert [(row["model"], row["duration"], row["rows"], row["errors"]) for row in rows] == [
        ("basal/mini", 1, 1, 0),
        ("basal/mini", 1, 1, 0),
    ]
    for index in range(8):
        settings.append_schedule_run({"schedule": "other", "at": index, "file": "x" * 180_000})
    restarted = schedules.Schedules(settings, fleet, start)
    restarted.run_due(10000)
    assert calls == ["a.jsonl", "b.jsonl"]


def test_schedule_refuses_output_symlink_to_input(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    inputs, outputs = tmp_path / "in", tmp_path / "out"
    inputs.mkdir()
    outputs.mkdir()
    source = inputs / "one.jsonl"
    source.write_text('{"state":"keep"}')
    (outputs / "one.answers.jsonl").symlink_to(source)
    runner = schedules.Schedules(settings, Fleet(), lambda *_: pytest.fail("must not start"))
    spec = runner.create(definition(inputs, outputs, overwrite=True))
    runner.run_due(1000)
    assert source.read_text() == '{"state":"keep"}'
    assert "output must not be the input file" in runner.runs(spec["id"])["runs"][0]["error"]


def test_three_failed_jobs_pause_only_after_third(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    inputs, outputs = tmp_path / "in", tmp_path / "out"
    inputs.mkdir()
    outputs.mkdir()
    fleet = Fleet()

    def start(*_):
        fleet._job = Job(error="failed")
        return fleet._job.state()

    runner = schedules.Schedules(settings, fleet, start)
    spec = runner.create(definition(inputs, outputs))
    for number in range(3):
        (inputs / f"{number}.jsonl").write_text('{"state":"x"}')
        runner.run_due(2000 + number * 60)
        runner.run_due(2001 + number * 60)
        assert runner.get(spec["id"])["paused"] is (number == 2)


@pytest.mark.skipif(__import__("os").name != "posix", reason="selecting subprocess pipes requires POSIX")
def test_schedule_updates_from_separate_processes_preserve_both_changes(tmp_path, monkeypatch):
    import os
    import select
    import subprocess
    import sys
    import time

    config = tmp_path / "settings.json"
    monkeypatch.setenv("BASAL_UI_CONFIG", str(config))
    schedules.Schedules(settings, Fleet(), lambda *_: None).create(definition(tmp_path, tmp_path))
    code = """
import sys
from basal_ui import schedules, settings
runner = schedules.Schedules(settings, None, None)
mode = sys.argv[1]
def validate(body):
    print('entered', flush=True)
    if mode == 'first':
        if not sys.stdin.readline():
            raise RuntimeError('release missing')
    return schedules.validate(body)
print('attempt', flush=True)
if mode == 'second':
    import fcntl, os
    fd = os.open(os.environ['BASAL_UI_CONFIG'] + '.lock', os.O_RDWR)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('contended', flush=True)
        else:
            print('unlocked', flush=True)
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
runner.update('nightly', {'pattern': '*.txt'} if mode == 'first' else {'concurrency': 7}, validate)
print('done', flush=True)
"""
    env = {**os.environ, "BASAL_UI_CONFIG": str(config)}
    workers = []

    def line(worker, timeout=15):
        deadline = time.monotonic() + timeout
        result = bytearray()
        while True:
            ready, _, _ = select.select([worker.stdout], [], [], max(0, deadline - time.monotonic()))
            assert ready, "worker did not reach synchronization point"
            char = os.read(worker.stdout.fileno(), 1)
            assert char, "worker exited before synchronization"
            if char == b"\n":
                return result.decode()
            result.extend(char)

    try:
        first = subprocess.Popen(
            [sys.executable, "-u", "-c", code, "first"],
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        workers.append(first)
        assert line(first) == "attempt"
        assert line(first) == "entered"
        second = subprocess.Popen(
            [sys.executable, "-u", "-c", code, "second"],
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        workers.append(second)
        assert line(second) == "attempt"
        assert line(second) == "contended"
        assert not select.select([second.stdout], [], [], 0.5)[0]
        first.stdin.write(b"release\n")
        first.stdin.flush()
        assert line(first) == "done"
        assert line(second) == "entered"
        assert line(second) == "done"
        for worker in workers:
            assert worker.wait(timeout=15) == 0
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.kill()
            worker.communicate(timeout=5)
    row = settings.schedules()[0]
    assert row["pattern"] == "*.txt" and row["concurrency"] == 7


def test_schedule_runs_are_newest_first_across_pages(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    runner = schedules.Schedules(settings, Fleet(), lambda *_: None)
    runner.create(definition(tmp_path, tmp_path))
    for index in range(105):
        settings.append_schedule_run({"schedule": "nightly", "at": index, "file": str(index)})
    first = runner.runs("nightly")
    second = runner.runs("nightly", first["next_cursor"])
    assert [row["at"] for row in first["runs"]] == list(range(104, 4, -1))
    assert [row["at"] for row in second["runs"]] == [4, 3, 2, 1, 0]
    assert second["next_cursor"] is None


def test_three_scan_failures_pause_schedule(tmp_path, monkeypatch):
    from pathlib import Path

    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    runner = schedules.Schedules(settings, Fleet(), lambda *_: pytest.fail("must not start"))
    runner.create(definition(tmp_path, tmp_path))

    def fail_scan(*_):
        raise OSError("cannot scan")

    monkeypatch.setattr(Path, "glob", fail_scan)
    for number in range(3):
        runner.run_due(1000 + number * 60)
        assert runner.get("nightly")["paused"] is (number == 2)
    rows = runner.runs("nightly")["runs"]
    assert len(rows) == 3
    assert all(row["error"] == "cannot scan" and row["rows"] == 0 and row["errors"] == 1 for row in rows)


def test_run_duration_excludes_delay_until_next_scheduler_poll(tmp_path, monkeypatch):
    from basal_ui import jobs

    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    inputs, outputs = tmp_path / "in", tmp_path / "out"
    inputs.mkdir()
    outputs.mkdir()
    (inputs / "one.jsonl").write_text('{"state":"x"}')
    clock = [1000]
    monkeypatch.setattr(jobs.time, "time", lambda: clock[0])
    fleet = Fleet()

    def start(*_):
        fleet._job = jobs.Job("batch", "basal/mini", "duration", jobs.LogBuffer())
        clock[0] = 1002
        fleet._job.finish("done", {"items": 1, "errors": 0})
        return fleet._job.state()

    runner = schedules.Schedules(settings, fleet, start)
    spec = runner.create(definition(inputs, outputs))
    runner.run_due(1000)
    runner.run_due(2000)
    assert runner.runs(spec["id"])["runs"][0]["duration"] == 2
