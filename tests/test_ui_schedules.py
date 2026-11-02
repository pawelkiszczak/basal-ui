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
        runner.run_now(spec["id"], 2000 + number)
    saved = runner.get(spec["id"])
    assert saved["paused"] is True
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
