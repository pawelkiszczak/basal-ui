import json
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from basal import calibrate
from fastapi.testclient import TestClient

from basal_ui import calibration, catalog, engine, settings
from basal_ui.app import create_app
from basal_ui.catalog import Variant
from basal_ui.fleet import Fleet, Runner
from basal_ui.hardware import Hardware
from basal_ui.history import History
from basal_ui.stats import Stats

APPLE = Hardware("apple", "mps", 1024**4, None, None, frozenset({"mps"}))
HW = Hardware("cpu", "cpu", 1024**4, None, None, frozenset())
MINI = Variant("mini", "Remek/basal-2.0-mini", "basal-2.0", "mini", "bf16", "bf16", None, 100, False)
FITTED = {
    "prompt": 2,
    "temperature_per_prim": {"choice": 1.2},
    "engine": "eager",
    "fitted_on": {"decisions": 20, "per_type": {"choice": 20}},
    "gates": {"automated_at": {"0.05": 0.4}},
    "thresholds": {"0.05": {"per_type": {"choice": {"confidence": 0.93}}}},
}


@pytest.fixture(autouse=True)
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    model = tmp_path / "model"
    model.mkdir()
    (model / "CALIBRATION.json").write_text(json.dumps({"temperature_per_prim": {"choice": 1.1}}))
    monkeypatch.setattr(catalog, "is_downloaded", lambda variant, cache_dir, revision=None: model)

    def start(self, assessment, gen, media=False, cache_dir=None, serve=None):
        with self.lock:
            if gen == self.gen:
                self.state, self.base_url, self.ready_at = "ready", "http://127.0.0.1:1", time.time()
                self.loaded_path = str(self.model_path or model)
                self.serve_snapshot = {"log_decisions": "", "max_len": 4096, "calibration": self._calibration(MINI)}
                self.command = self._build_command(MINI, model, assessment, False, 1, settings.serve_settings())

    monkeypatch.setattr(Runner, "_start", start)
    return model


def wait_job(client, kind="calibrate"):
    deadline = time.monotonic() + 5
    while client.get("/api/status").json()["jobs"][kind]["running"] and time.monotonic() < deadline:
        time.sleep(0.01)
    return client.get("/api/status").json()["jobs"][kind]


def test_calibrate_fits_against_a_temporary_child_lists_and_selects_the_file(tmp_path, monkeypatch, config):
    release, calls = threading.Event(), []

    def fake_main(argv):
        calls.append(argv)
        release.wait(5)
        print("report line")
        out = argv[argv.index("--out") + 1]
        with open(out, "w") as f:
            json.dump({**FITTED, "fitted_on": {**FITTED["fitted_on"], "layout": "qfirst"}}, f)

    monkeypatch.setattr(calibrate, "main", fake_main)
    fleet = Fleet(
        models_dir_fn=lambda: tmp_path,
        platform_fn=lambda: "cpu",
        settings_mod=settings,
        hardware_fn=lambda: HW,
        stats=Stats(tmp_path / "s.json"),
        history=History(tmp_path / "h.jsonl"),
    )
    app = create_app(
        catalog_fn=lambda **_: ([MINI], "test"),
        hardware_fn=lambda: HW,
        fleet=fleet,
        stats=fleet.stats,
        history=fleet.history,
    )
    labelled = tmp_path / "labelled.jsonl"
    labelled.write_text('{"state": "s", "question": "q?", "options": ["a", "b"], "gold": 0}\n')
    settings.set_load_options("mini", {"layout": "qfirst"})
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.post("/api/load", json={"id": "mini", "options": {"layout": "qfirst"}}).status_code == 202
        missing = client.post("/api/calibrate", json={"id": "mini", "input": str(tmp_path / "none.jsonl")})
        assert missing.status_code == 400 and "cannot read" in missing.json()["detail"]
        assert client.post("/api/calibrate", json={"id": "mini", "input": str(labelled)}).status_code == 202
        deadline = time.monotonic() + 5
        while not calls and time.monotonic() < deadline:
            time.sleep(0.01)
        assert "calibrate" in fleet.temporary  # the uncalibrated child, without --api-keys, counted in the budget
        child = fleet.temporary["calibrate"].command
        assert "--no-calibration" in child and "--api-keys" not in child
        assert child[child.index("--layout") + 1] == "qfirst"
        busy = client.post("/api/calibrate", json={"id": "mini", "input": str(labelled)})
        assert busy.status_code == 409 and busy.json()["detail"] == "a calibrate job is already running"
        release.set()
        job = wait_job(client)
        assert job["phase"] == "done", job["error"]
        assert calls[0][calls[0].index("--url") + 1] == "http://127.0.0.1:1"
        assert calls[0][calls[0].index("--model") + 1] == str(config)
        assert calls[0][calls[0].index("--layout") + 1] == "qfirst"
        result = job["result"]
        assert result["report"] == "report line" and result["thresholds"] == {"0.05": {"choice": 0.93}}
        assert fleet.temporary == {}

        listing = client.get("/api/calibrations", params={"id": "mini"}).json()
        assert listing["selected"] is None and [f["file"] for f in listing["files"]] == [result["file"]]
        assert listing["shipped"]["context"]["layout"] == "qfirst"
        report = client.get("/api/calibrations/report", params={"id": "mini", "file": result["file"]})
        assert report.json() == {"report": "report line\n"}
        stranger = client.post("/api/calibrations/select", json={"id": "mini", "file": str(tmp_path / "x.json")})
        assert stranger.status_code == 400

        runner = fleet.runners["mini"]
        assert runner.status()["restart_required"] is False
        assert (
            client.post("/api/calibrations/select", json={"id": "mini", "file": result["file"]}).json()["selected"]
            == result["file"]
        )
        assert runner.status()["restart_required"] is True  # the running child has the shipped calibration
        command = runner._build_command(MINI, config, catalog.assess(MINI, HW), False, 1, settings.serve_settings())
        assert command[command.index("--calibration") + 1] == result["file"]
        client.post("/api/calibrations/select", json={"id": "mini", "file": None})
        assert runner.status()["restart_required"] is False


def test_shipped_calibration_resolution_uses_preset_and_layout(tmp_path):
    model = tmp_path / "model"
    model.mkdir(exist_ok=True)
    (model / "basal.json").write_text('{"layout":"qfirst"}')
    expected = model / "CALIBRATION.vllm.custom.qfirst.json"
    expected.write_text("{}")
    assert calibration.shipped_file(model, "vllm", "custom", layout="qfirst") == expected
    if engine.feature("adapter"):  # an adapter's own file comes first (engine 2.5.0)
        adapter = tmp_path / "adapter"
        adapter.mkdir()
        own = adapter / "CALIBRATION.vllm.custom.qfirst.json"
        own.write_text("{}")
        assert calibration.shipped_file(model, "vllm", "custom", layout="qfirst", adapter_dir=adapter) == own


def test_calibrate_refuses_an_unknown_model_and_a_model_over_the_budget(tmp_path):
    fleet = Fleet(
        models_dir_fn=lambda: tmp_path,
        platform_fn=lambda: "cpu",
        settings_mod=settings,
        hardware_fn=lambda: HW,
        stats=Stats(tmp_path / "s.json"),
        history=History(tmp_path / "h.jsonl"),
    )
    app = create_app(
        catalog_fn=lambda **_: ([MINI], "test"),
        hardware_fn=lambda: HW,
        fleet=fleet,
        stats=fleet.stats,
        history=fleet.history,
    )
    labelled = tmp_path / "labelled.jsonl"
    labelled.write_text('{"state": "s", "question": "q?", "options": ["a", "b"], "gold": 0}\n')
    settings.update_memory({"reserve_bytes": 1024**4})  # nothing fits
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.post("/api/calibrate", json={"id": "nope", "input": str(labelled)}).status_code == 400
        over = client.post("/api/calibrate", json={"id": "mini", "input": str(labelled)})
        assert over.status_code == 409 and over.json()["detail"].startswith("over memory budget")
        assert (
            client.post("/api/calibrate", json={"id": "mini", "input": str(labelled), "targets": [2]}).status_code
            == 400
        )


def make_app(tmp_path, hw=HW):
    fleet = Fleet(
        models_dir_fn=lambda: tmp_path,
        platform_fn=lambda: hw.platform,
        settings_mod=settings,
        hardware_fn=lambda: hw,
        stats=Stats(tmp_path / "s.json"),
        history=History(tmp_path / "h.jsonl"),
    )
    return fleet, create_app(
        catalog_fn=lambda **_: ([MINI], "test"),
        hardware_fn=lambda: hw,
        fleet=fleet,
        stats=fleet.stats,
        history=fleet.history,
    )


@pytest.mark.skipif(not engine.feature("adapter"), reason="needs an engine that takes --adapter (2.5.0)")
def test_shipped_calibration_context_prefers_running_options_to_saved_options(tmp_path, config):
    adapter = tmp_path / "adapter"
    adapter.mkdir()
    (adapter / "adapter_config.json").write_text('{"peft_type":"LORA","r":1,"lora_alpha":1}')
    (adapter / "adapter_model.safetensors").write_bytes(b"")
    (config / "basal.json").write_text('{"layout":"canonical"}')
    adapter_cal = adapter / "CALIBRATION.vllm.deterministic.json"
    adapter_cal.write_text('{"temperature_per_prim":{"choice":1}}')
    model_cal = config / "CALIBRATION.qfirst.json"
    model_cal.write_text('{"temperature_per_prim":{"choice":2}}')
    settings.set_load_options(
        "mini",
        {
            "mode": "vllm",
            "revision": "saved-rev",
            "vllm_preset": "deterministic",
            "layout": "canonical",
            "adapter": str(adapter),
        },
    )
    fleet, app = make_app(tmp_path)
    settings.set_models_dir(str(tmp_path))
    (config / "config.json").write_text("{}")
    (config / "README.md").write_text("local model")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        pending = client.get("/api/calibrations", params={"id": "mini"}).json()["shipped"]
        assert pending["file"] == str(adapter_cal)
        assert pending["context"] == {
            "revision": "saved-rev",
            "mode": "vllm",
            "quant": None,
            "preset": "deterministic",
            "layout": "canonical",
            "adapter": str(adapter),
            "active": False,
        }
        runner = Runner(models_dir_fn=lambda: tmp_path, platform_fn=lambda: "cpu", settings_mod=settings)
        runner.state, runner.model, runner.mode, runner.options = (
            "ready",
            "mini",
            "eager",
            {"mode": "eager", "revision": "active-rev", "layout": "qfirst"},
        )
        runner.loaded_path = str(config)
        fleet.runners["mini"] = runner
        active = client.get("/api/calibrations", params={"id": "mini"}).json()["shipped"]
        assert active["file"] == str(model_cal)
        assert active["context"]["mode"] == "eager" and active["context"]["adapter"] is None
        assert active["context"]["revision"] == "active-rev" and active["context"]["layout"] == "qfirst"
        assert active["context"]["active"] is True
        card = client.get("/api/models/card", params={"id": "mini"}).json()
        assert card["shipped_calibration"]["temperature_per_prim"] == {"choice": 2}


TASK = {
    "state": "The parcel arrived broken.",
    "questions": {
        "intent": {"type": "classify", "labels": {"complaint": "a complaint", "praise": "praise"}, "gold": "complaint"}
    },
}


@pytest.mark.parametrize("route", ["listing", "fit", "card"])
def test_calibration_keeps_revision_and_layout_together_during_load(tmp_path, monkeypatch, route):
    saved = tmp_path / "saved-rev"
    active = tmp_path / "active-rev"
    for model in (saved, active):
        model.mkdir()
        (model / "basal.json").write_text('{"layout":"canonical"}')
        (model / "README.md").write_text("local model")
        for name in ("CALIBRATION.json", "CALIBRATION.qfirst.json"):
            (model / name).write_text(json.dumps(FITTED))
    (saved / "CALIBRATION.qfirst.json").write_text(json.dumps({**FITTED, "temperature_per_prim": {"choice": 9}}))
    settings.set_load_options("mini", {"revision": "saved-rev", "layout": "canonical"})
    fleet, app = make_app(tmp_path)
    runner = Runner(models_dir_fn=lambda: tmp_path, platform_fn=lambda: "cpu", settings_mod=settings)
    runner.state, runner.model, runner.mode, runner.options = (
        "ready",
        "mini",
        "eager",
        {"revision": "active-rev", "layout": "qfirst"},
    )

    def downloaded(variant, cache_dir, revision=None):
        with fleet.lock:
            fleet.runners["mini"] = runner
        return {"saved-rev": saved, "active-rev": active}[revision]

    def fit(argv):
        model = argv[argv.index("--model") + 1]
        layout = argv[argv.index("--layout") + 1] if "--layout" in argv else "canonical"
        Path(argv[argv.index("--out") + 1]).write_text(
            json.dumps({**FITTED, "fitted_on": {"model": model, "layout": layout}})
        )

    monkeypatch.setattr(calibrate, "main", fit)
    labelled = tmp_path / "labelled.jsonl"
    labelled.write_text(json.dumps(TASK) + "\n")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        monkeypatch.setattr(catalog, "is_downloaded", downloaded)
        if route == "listing":
            response = client.get("/api/calibrations", params={"id": "mini"})
            assert response.status_code == 200
            shipped = response.json()["shipped"]
            context = shipped["context"]
            observed = (str(Path(shipped["file"]).parent), context["revision"], context["layout"])
        elif route == "card":
            response = client.get("/api/models/card", params={"id": "mini"})
            assert response.status_code == 200
            card = response.json()
            assert card["local_dir"] == str(saved)
            assert card["shipped_calibration"]["temperature_per_prim"] == FITTED["temperature_per_prim"]
            observed = (card["local_dir"], "saved-rev", "canonical")
        else:
            response = client.post("/api/calibrate", json={"id": "mini", "input": str(labelled)})
            assert response.status_code == 202
            job = wait_job(client)
            assert job["phase"] == "done", job["error"]
            fitted = json.loads(Path(job["result"]["file"]).read_text())["fitted_on"]
            observed = (fitted["model"], Path(fitted["model"]).name, fitted["layout"])
        assert observed in {
            (str(saved), "saved-rev", "canonical"),
            (str(active), "active-rev", "qfirst"),
        }


def test_exited_runner_uses_saved_calibration_settings(tmp_path, monkeypatch, config):
    settings.set_load_options("mini", {"revision": "saved-rev", "layout": "canonical"})
    fleet, app = make_app(tmp_path)
    runner = Runner(models_dir_fn=lambda: tmp_path, platform_fn=lambda: "cpu", settings_mod=settings)
    runner.state, runner.model, runner.mode, runner.options = (
        "ready",
        "mini",
        "eager",
        {"revision": "active-rev", "layout": "qfirst"},
    )
    runner.process = SimpleNamespace(poll=lambda: 1, returncode=1)
    monkeypatch.setattr(
        catalog, "is_downloaded", lambda variant, cache_dir, revision=None: config if revision == "saved-rev" else None
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        fleet.runners["mini"] = runner
        try:
            response = client.get("/api/calibrations", params={"id": "mini"})
            assert response.status_code == 200
            shipped = response.json()["shipped"]
            assert shipped["file"] == str(config / "CALIBRATION.json")
            assert shipped["context"]["revision"] == "saved-rev"
            assert shipped["context"]["layout"] == "canonical"
            assert shipped["context"]["active"] is False
        finally:
            runner.process = None


def test_a_bad_input_is_refused_with_its_line_before_any_model_starts(tmp_path, monkeypatch):
    monkeypatch.setattr(calibrate, "main", lambda argv: pytest.fail("basal-calibrate ran on a bad input"))
    fleet, app = make_app(tmp_path)
    labelled = tmp_path / "labelled.jsonl"
    labelled.write_text('{"state": "s", "question": "q?", "options": ["a", "b"], "gold": 0}\n{"state": "s"}\n')
    with TestClient(app, base_url="http://127.0.0.1") as client:
        bad = client.post("/api/calibrate", json={"id": "mini", "input": str(labelled)})
        assert bad.status_code == 400 and "line 2" in bad.json()["detail"]
        assert "calibrate" not in fleet.temporary


def test_task_questions_calibrate_with_the_engine_in_process_and_reserve_its_memory(tmp_path, monkeypatch, config):
    calls, reserved = [], []

    def fake_main(argv):
        calls.append(argv)
        reserved.append(dict(fleet.reserved))
        out = argv[argv.index("--out") + 1]
        with open(out, "w") as f:
            json.dump({**FITTED, "task_thresholds": {"classify": {"0.05": 0.81}}}, f)
        Path(out).chmod(0o644)
        if "--save-rows" in argv:
            Path(argv[argv.index("--save-rows") + 1]).write_text("{}\n")
            Path(argv[argv.index("--save-rows") + 1]).chmod(0o644)

    monkeypatch.setattr(calibrate, "main", fake_main)
    fleet, app = make_app(tmp_path, APPLE)
    settings.set_load_options(
        "mini", {"mode": "eager", "max_batch": 8, "deterministic": True, "strict_calibration": True, "layout": "qfirst"}
    )
    labelled = tmp_path / "labelled.jsonl"
    labelled.write_text(json.dumps(TASK) + "\n")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert (
            client.post(
                "/api/calibrate",
                json={
                    "id": "mini",
                    "input": str(labelled),
                    "advanced": {
                        "strict": True,
                        "reference": "basal-1.5",
                        "min_per_type": 12,
                        "min_blend": 3,
                        "min_family": 4,
                        "min_per_set": 5,
                        "concurrency": 7,
                        "compare": "none",
                        "save_rows": True,
                    },
                },
            ).status_code
            == 202
        )
        job = wait_job(client)
        assert job["phase"] == "done", job["error"]
        argv = calls[0]
        for flag, value in (
            ("--reference", "basal-1.5"),
            ("--min-per-type", "12"),
            ("--min-blend", "3"),
            ("--min-family", "4"),
            ("--min-per-set", "5"),
            ("--concurrency", "7"),
        ):
            assert argv[argv.index(flag) + 1] == value
        assert argv[argv.index("--layout") + 1] == "qfirst"
        assert "--strict" in argv and "--no-calibration" in argv
        assert "--url" not in argv and argv[argv.index("--mode") + 1] == "eager"
        assert argv[argv.index("--max-batch") + 1] == "8" and "--deterministic" in argv
        assert "--strict-calibration" not in argv  # basal-serve's own, not the engine's
        assert "calibrate" in reserved[0] and "calibrate" not in fleet.reserved  # released after the run
        assert "calibrate" not in fleet.temporary  # no basal-serve child
        assert job["result"]["engine_in_process"] is True
        listed = client.get("/api/calibrations", params={"id": "mini"}).json()["files"][0]
        assert listed["task_thresholds"] == {"classify": {"0.05": 0.81}}
        assert listed["rows"] == str(Path(listed["file"]).with_suffix(".rows.jsonl"))
        if os.name == "posix":
            output = Path(listed["file"])
            for path in (output, output.with_suffix(".report.txt"), Path(listed["rows"])):
                assert path.stat().st_mode & 0o777 == 0o600


def test_a_calibration_fitted_elsewhere_is_imported_selectable_and_warned_about(tmp_path, config):
    _, app = make_app(tmp_path)
    elsewhere = tmp_path / "other" / "CALIBRATION.custom.json"
    elsewhere.parent.mkdir()
    elsewhere.write_text(
        json.dumps({**FITTED, "fitted_on": {**FITTED["fitted_on"], "model": "Remek/basal-2.0-max", "layout": "qfirst"}})
    )
    elsewhere.with_suffix(".report.txt").write_text("their report\n")
    junk = tmp_path / "junk.json"
    junk.write_text('{"hello": 1}')
    with TestClient(app, base_url="http://127.0.0.1") as client:
        refused = client.post("/api/calibrations/import", json={"id": "mini", "path": str(junk)})
        assert refused.status_code == 400 and "not a basal calibration file" in refused.json()["detail"]
        missing = client.post("/api/calibrations/import", json={"id": "mini", "path": str(tmp_path / "none.json")})
        assert missing.status_code == 400
        done = client.post("/api/calibrations/import", json={"id": "mini", "path": str(elsewhere)}).json()
        entry = next(f for f in done["files"] if f["file"] == done["file"])
        assert entry["imported"] and "fitted on Remek/basal-2.0-max" in entry["warning"]
        assert entry["layout"] == "qfirst" and done["shipped"]["context"]["layout"] == "canonical"
        assert (
            client.get("/api/calibrations/report", params={"id": "mini", "file": done["file"]}).json()["report"]
            == "their report\n"
        )
        selected = client.post("/api/calibrations/select", json={"id": "mini", "file": done["file"]}).json()
        assert selected["selected"] == done["file"] and selected["layout_mismatch"]


def test_importing_same_calibration_twice_never_overwrites_file_or_report(tmp_path):
    source = tmp_path / "calibration.json"
    source.write_text(json.dumps(FITTED))
    source.with_suffix(".report.txt").write_text("original report")
    model_dir = tmp_path / "models"
    model_dir.mkdir()

    class Settings:
        @staticmethod
        def calibrations_dir(_model):
            return model_dir

    first = calibration.import_file("mini", Settings, source)
    first.write_text(json.dumps({**FITTED, "engine": "first"}))
    first.with_suffix(".report.txt").write_text("first report")
    second = calibration.import_file("mini", Settings, source)
    assert second != first
    assert json.loads(first.read_text())["engine"] == "first"
    assert first.with_suffix(".report.txt").read_text() == "first report"
    assert json.loads(second.read_text()) == FITTED
    assert second.with_suffix(".report.txt").read_text() == "original report"


def test_from_rows_report_only_skips_child_and_memory_reservation(tmp_path, monkeypatch, config):
    calls = []
    monkeypatch.setattr(calibrate, "main", lambda argv: (calls.append(argv), print("rows report")))
    fleet, app = make_app(tmp_path)
    rows = tmp_path / "fit.rows.jsonl"
    rows.write_text('{"basal_calibrate_rows":1,"model":"org/model"}\n')
    (tmp_path / "fit.json").write_text(json.dumps({**FITTED, "fitted_on": {"model": "org/model"}}))
    settings.update_memory({"reserve_bytes": 1024**4})  # nothing fits: a refit runs no engine, so no check
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post(
            "/api/calibrate",
            json={"id": "mini", "input": str(rows), "from_rows": True, "advanced": {"report_only": True}},
        )
        assert response.status_code == 202
        job = wait_job(client)
    assert job["phase"] == "done", job["error"]
    assert job["result"] == {
        "file": None,
        "report": "warning: saved rows have no recorded baseline; shipped comparison is unavailable\nrows report",
        "report_only": True,
    }
    assert "--from-rows" in calls[0] and "--report-only" in calls[0]
    assert not fleet.temporary and not fleet.reserved


def test_report_only_on_decision_input_uses_the_temporary_child_and_writes_no_file(tmp_path, monkeypatch, config):
    calls, children = [], []

    def fake_main(argv):
        calls.append(argv)
        children.append(set(fleet.temporary))
        print("report only")

    monkeypatch.setattr(calibrate, "main", fake_main)
    fleet, app = make_app(tmp_path)
    labelled = tmp_path / "labelled.jsonl"
    labelled.write_text('{"state": "s", "question": "q?", "options": ["a", "b"], "gold": 0}\n')
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post(
            "/api/calibrate", json={"id": "mini", "input": str(labelled), "advanced": {"report_only": True}}
        )
        assert response.status_code == 202
        job = wait_job(client)
    assert job["phase"] == "done", job["error"]
    assert job["result"] == {"file": None, "report": "report only", "report_only": True}
    assert "--url" in calls[0] and "--report-only" in calls[0]
    assert children == [{"calibrate"}] and not fleet.temporary and fleet.reserved == {}
    assert not list(settings.calibrations_dir("mini").glob("*.report.txt"))


def test_a_missing_compare_or_reference_file_is_refused_before_the_job(tmp_path, monkeypatch):
    monkeypatch.setattr(calibrate, "main", lambda argv: pytest.fail("basal-calibrate ran"))
    fleet, app = make_app(tmp_path)
    labelled = tmp_path / "labelled.jsonl"
    labelled.write_text('{"state": "s", "question": "q?", "options": ["a", "b"], "gold": 0}\n')
    with TestClient(app, base_url="http://127.0.0.1") as client:
        for key in ("reference", "compare"):
            missing = str(tmp_path / f"{key}.json")
            response = client.post(
                "/api/calibrate", json={"id": "mini", "input": str(labelled), "advanced": {key: missing}}
            )
            assert response.status_code == 400
            assert response.json()["detail"] == f"{key}: {missing} is not a readable file"
        assert not fleet.temporary


def test_a_file_fitted_on_a_cache_snapshot_belongs_to_the_served_repo(tmp_path):
    """basal-ui passes basal-calibrate the snapshot directory; the file must name the repo id and commit, or
    basal-serve (served with --name <repo>) would call it another model's file and drop its thresholds."""
    from basal.server import calibration_mismatch

    snapshot = tmp_path / "models--Remek--basal-2.0-mini" / "snapshots" / "cb81b32ce89b"
    snapshot.mkdir(parents=True)
    path = tmp_path / "fitted.json"
    path.write_text(json.dumps({"fitted_on": {"model": str(snapshot), "decisions": 20}, "thresholds": {}}))
    calibration.name_snapshot(path, snapshot)
    cal = json.loads(path.read_text())
    assert cal["fitted_on"] == {"model": "Remek/basal-2.0-mini", "revision": "cb81b32ce89b", "decisions": 20}
    assert calibration_mismatch(cal, "Remek/basal-2.0-mini", snapshot) is None
    local = tmp_path / "my-model"  # a plain directory keeps its path: that is its name
    local.mkdir()
    path.write_text(json.dumps({"fitted_on": {"model": str(local)}}))
    calibration.name_snapshot(path, local)
    assert json.loads(path.read_text())["fitted_on"] == {"model": str(local)}


def test_layout_fit_selection_and_certification(tmp_path, monkeypatch, config):
    calls = []

    def fit(argv):
        calls.append(argv)
        layout = argv[argv.index("--layout") + 1] if "--layout" in argv else "canonical"
        Path(argv[argv.index("--out") + 1]).write_text(json.dumps({**FITTED, "fitted_on": {"layout": layout}}))

    monkeypatch.setattr(calibrate, "main", fit)
    fleet, app = make_app(tmp_path)
    settings.set_load_options("mini", {"layout": "qfirst"})
    labelled = tmp_path / "labelled.jsonl"
    labelled.write_text('{"state":"s","question":"q?","options":["a","b"],"gold":0}\n')
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.post("/api/calibrate", json={"id": "mini", "input": str(labelled)}).status_code == 202
        job = wait_job(client)
        assert job["phase"] == "done", job["error"]
        assert calls[-1][calls[-1].index("--layout") + 1] == "qfirst"
        file = job["result"]["file"]
        listing = client.post("/api/calibrations/select", json={"id": "mini", "file": file}).json()
        assert listing["calibrate_layout"] is True
        assert listing["files"][0]["layout"] == "qfirst" and not listing["layout_mismatch"]
        assert calibration.served_certified({}, config, "eager", {"layout": "qfirst"}, file)
        assert not calibration.served_certified({}, config, "eager", {"layout": "canonical"}, file)
        runner = Runner(models_dir_fn=lambda: tmp_path, platform_fn=lambda: "cpu", settings_mod=settings)
        runner.state, runner.model, runner.mode, runner.options = "ready", "mini", "eager", {"layout": "canonical"}
        fleet.runners["mini"] = runner
        runner.loaded_path = str(config)
        listing = client.get("/api/calibrations", params={"id": "mini"}).json()
        assert listing["layout_mismatch"] and listing["layout_mismatch_code"] == "calibration_layout_mismatch"
        assert client.post("/api/calibrate", json={"id": "mini", "input": str(labelled)}).status_code == 202
        assert wait_job(client)["phase"] == "done"
        assert "--layout" not in calls[-1]
        monkeypatch.setattr(engine, "feature", lambda name: name != "calibrate_layout")
        runner.options["layout"] = "qfirst"
        assert client.get("/api/calibrations", params={"id": "mini"}).json()["calibrate_layout"] is False
        assert client.post("/api/calibrate", json={"id": "mini", "input": str(labelled)}).status_code == 400


def test_refit_preserves_the_layout_of_the_original_rows(tmp_path, monkeypatch, config):
    def fit(argv):
        Path(argv[argv.index("--out") + 1]).write_text(json.dumps(FITTED))

    monkeypatch.setattr(calibrate, "main", fit)
    _, app = make_app(tmp_path)
    original = tmp_path / "fit.json"
    original.write_text(json.dumps({**FITTED, "fitted_on": {"model": "org/model", "layout": "qfirst"}}))
    rows = tmp_path / "fit.rows.jsonl"
    rows.write_text('{"basal_calibrate_rows":1,"model":"org/model"}\n')
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert (
            client.post("/api/calibrate", json={"id": "mini", "input": str(rows), "from_rows": True}).status_code == 202
        )
        job = wait_job(client)
        assert job["phase"] == "done_with_warnings", job["error"]
        assert "shipped comparison is unavailable" in job["result"]["report"]
        assert json.loads(Path(job["result"]["file"]).read_text())["fitted_on"]["layout"] == "qfirst"


def test_canonical_override_requires_layout_capability(tmp_path, monkeypatch, config):
    import basal.prompt

    monkeypatch.setattr(basal.prompt, "model_layout", lambda path: "qfirst")
    monkeypatch.setattr(engine, "feature", lambda name: name != "calibrate_layout")
    settings.set_load_options("mini", {"layout": "canonical"})
    labelled = tmp_path / "labelled.jsonl"
    labelled.write_text('{"state":"s","question":"q?","options":["a","b"],"gold":0}\n')
    _, app = make_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        result = client.post("/api/calibrate", json={"id": "mini", "input": str(labelled)})
        assert result.status_code == 400
        assert result.json()["code"] == "calibration_layout_unsupported"
        listing = client.get("/api/calibrations", params={"id": "mini"}).json()
        assert listing["default_layout"] == "qfirst"
        rows = tmp_path / "fit.rows.jsonl"
        rows.write_text('{"basal_calibrate_rows":1,"model":"org/model"}\n')
        (tmp_path / "fit.json").write_text(
            json.dumps({**FITTED, "fitted_on": {"model": "org/model", "layout": "canonical"}})
        )
        calls = []

        def refit(argv):
            calls.append(argv)
            Path(argv[argv.index("--out") + 1]).write_text(json.dumps(FITTED))

        monkeypatch.setattr(calibrate, "main", refit)
        result = client.post("/api/calibrate", json={"id": "mini", "input": str(rows), "from_rows": True})
        assert result.status_code == 202
        assert wait_job(client)["phase"] == "done_with_warnings"
        assert "--layout" not in calls[0]


def test_canonical_default_needs_no_layout_capability(tmp_path, monkeypatch, config):
    calls = []

    def fit(argv):
        calls.append(argv)
        Path(argv[argv.index("--out") + 1]).write_text(json.dumps(FITTED))

    monkeypatch.setattr(calibrate, "main", fit)
    monkeypatch.setattr(engine, "feature", lambda name: name != "calibrate_layout")
    labelled = tmp_path / "labelled.jsonl"
    labelled.write_text('{"state":"s","question":"q?","options":["a","b"],"gold":0}\n')
    _, app = make_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        assert client.post("/api/calibrate", json={"id": "mini", "input": str(labelled)}).status_code == 202
        assert wait_job(client)["phase"] == "done"
        assert "--layout" not in calls[-1]


@pytest.mark.parametrize("metadata", ['{"layout":"unknown"}', "{", "[]", "null"])
def test_invalid_model_layout_keeps_calibration_listing_available(tmp_path, config, metadata):
    (config / "basal.json").write_text(metadata)
    directory = settings.calibrations_dir("mini")
    directory.mkdir(parents=True, exist_ok=True)
    fitted = directory / "fit.json"
    fitted.write_text(json.dumps(FITTED))
    _, app = make_app(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False) as client:
        response = client.get("/api/calibrations", params={"id": "mini"})
        assert response.status_code == 200
        listing = response.json()
        assert listing["default_layout"] is None
        assert "error" in listing["shipped"]
        assert [entry["file"] for entry in listing["files"]] == [str(fitted)]


@pytest.mark.parametrize("content", [None, "{", "[]", '{"temperature_per_prim": {}}'])
def test_refit_rejects_unknown_original_layout_before_job(tmp_path, content):
    _, app = make_app(tmp_path)
    rows = tmp_path / "fit.rows.jsonl"
    rows.write_text('{"basal_calibrate_rows":1,"model":"org/model"}\n')
    if content is not None:
        (tmp_path / "fit.json").write_text(content)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        result = client.post("/api/calibrate", json={"id": "mini", "input": str(rows), "from_rows": True})
        assert result.status_code == 400
        assert result.json()["code"] == "calibration_rows_layout_unknown"
        assert client.get("/api/status").json()["jobs"].get("calibrate") is None


def test_active_snapshot_survives_cache_ref_change(tmp_path, monkeypatch, config):
    active = tmp_path / "old-snapshot"
    active.mkdir()
    (active / "basal.json").write_text('{"layout":"qfirst"}')
    (active / "CALIBRATION.qfirst.json").write_text(json.dumps(FITTED))
    fleet, app = make_app(tmp_path)
    runner = Runner(models_dir_fn=lambda: tmp_path, platform_fn=lambda: "cpu", settings_mod=settings)
    runner.state, runner.model, runner.mode, runner.loaded_path = "ready", "mini", "eager", str(active)
    runner.options = {}
    with TestClient(app, base_url="http://127.0.0.1") as client:
        fleet.runners["mini"] = runner
        response = client.get("/api/calibrations", params={"id": "mini"}).json()
        assert response["shipped"]["file"] == str(active / "CALIBRATION.qfirst.json")
        assert response["shipped"]["context"]["layout"] == "qfirst"
        fleet.runners.pop("mini")
        assert (
            client.get("/api/calibrations", params={"id": "mini"}).json()["shipped"]["context"]["layout"] == "canonical"
        )


def test_missing_model_snapshot_refits_saved_rows_without_engine_or_reservation(tmp_path, monkeypatch):
    fleet, app = make_app(tmp_path)
    directory = settings.calibrations_dir("mini")
    directory.mkdir(parents=True)
    original = directory / "original.json"
    original.write_text(json.dumps({**FITTED, "fitted_on": {"model": "org/model", "layout": "qfirst"}}))
    rows = original.with_suffix(".rows.jsonl")
    header = {
        "basal_calibrate_rows": 1,
        "fmt": 2,
        "kind": "mlx",
        "quant": None,
        "model": "org/model",
        "engine": "mlx",
        "data": "input.jsonl",
        "requests": 1,
        "baseline": None,
        "orders": 1,
    }
    row = {
        "line": 1,
        "req": 0,
        "name": "q",
        "prim": "choice",
        "gold": 0,
        "keys": ["0", "1"],
        "group": "1",
        "family": "q",
        "p": [0.99, 0.01],
    }
    rows.write_text(json.dumps(header) + "\n" + json.dumps(row) + "\n")
    monkeypatch.setattr(catalog, "is_downloaded", lambda *args, **kwargs: None)
    monkeypatch.setattr(catalog, "downloaded_bytes", lambda *args, **kwargs: None)
    monkeypatch.setattr(fleet, "reserve", lambda *args: pytest.fail("refit reserved memory"))
    monkeypatch.setattr(fleet, "check_budget", lambda *args: pytest.fail("refit checked memory"))
    monkeypatch.setattr(fleet, "start_temporary", lambda *args, **kwargs: pytest.fail("refit started a model"))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        model = client.get("/api/models").json()["models"][0]
        assert not model["downloaded"] and model["has_calibration_rows"]
        listing = client.get("/api/calibrations", params={"id": "mini"}).json()
        assert listing["shipped"] is None and listing["files"][0]["layout"] == "qfirst"
        response = client.post("/api/calibrate", json={"id": "mini", "input": str(rows), "from_rows": True})
        assert response.status_code == 202
        job = wait_job(client)
        assert job["phase"] == "done_with_warnings", job
        fitted = json.loads(Path(job["result"]["file"]).read_text())["fitted_on"]
        assert (fitted["layout"], fitted["model"]) == ("qfirst", "org/model") and "revision" not in fitted
        assert "shipped comparison is unavailable" in job["result"]["report"]
        monkeypatch.setattr(engine, "feature", lambda name: name != "calibrate_layout")
        unsupported = client.post("/api/calibrate", json={"id": "mini", "input": str(rows), "from_rows": True})
        assert unsupported.status_code == 400 and unsupported.json()["code"] == "calibration_layout_unsupported"


def test_refit_rejects_unavailable_saved_baseline(tmp_path, monkeypatch):
    _, app = make_app(tmp_path)
    monkeypatch.setattr(catalog, "is_downloaded", lambda *args, **kwargs: None)
    original = tmp_path / "fit.json"
    original.write_text(json.dumps({**FITTED, "fitted_on": {"model": "org/model"}}))
    rows = original.with_suffix(".rows.jsonl")
    rows.write_text(
        json.dumps({"basal_calibrate_rows": 1, "model": "org/model", "baseline": str(tmp_path / "missing.json")}) + "\n"
    )
    with TestClient(app, base_url="http://127.0.0.1") as client:
        result = client.post("/api/calibrate", json={"id": "mini", "input": str(rows), "from_rows": True})
        assert result.status_code == 400 and result.json()["code"] == "calibration_rows_baseline_unavailable"


def test_in_process_setup_failure_releases_reservation(tmp_path, monkeypatch, config):
    import basal.prompt

    real_model_layout = basal.prompt.model_layout
    calls = 0
    freed = []

    def fail_in_job(path):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise RuntimeError("model metadata became unavailable")
        return real_model_layout(path)

    monkeypatch.setattr(basal.prompt, "model_layout", fail_in_job)
    monkeypatch.setattr(calibration, "free_engine_memory", lambda: freed.append(True))
    fleet, app = make_app(tmp_path, APPLE)
    labelled = tmp_path / "task.jsonl"
    labelled.write_text(json.dumps(TASK) + "\n")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        result = client.post("/api/calibrate", json={"id": "mini", "input": str(labelled)})
        assert result.status_code == 202
        job = wait_job(client)
        assert job["phase"] == "error" and "model metadata became unavailable" in job["error"]
        assert freed == [True] and "calibrate" not in fleet.reserved
        monkeypatch.setattr(basal.prompt, "model_layout", real_model_layout)

        def fail_after_reservation(*args):
            assert "calibrate" in fleet.reserved
            raise RuntimeError("second job reached the Engine")

        monkeypatch.setattr(calibration, "_basal_calibrate", fail_after_reservation)
        result = client.post("/api/calibrate", json={"id": "mini", "input": str(labelled)})
        assert result.status_code == 202
        job = wait_job(client)
        assert job["phase"] == "error" and "second job reached the Engine" in job["error"]
        assert freed == [True, True] and "calibrate" not in fleet.reserved


@pytest.mark.parametrize(
    "header,pair",
    [
        ({"basal_calibrate_rows": 1}, {"model": "org/model"}),
        ({"basal_calibrate_rows": 1, "model": "other/model"}, {"model": "org/model"}),
        (
            {"basal_calibrate_rows": 1, "model": "org/model", "revision": "abc"},
            {"model": "org/model", "revision": "def"},
        ),
    ],
)
def test_refit_rejects_unverified_original_identity(tmp_path, header, pair):
    _, app = make_app(tmp_path)
    original = tmp_path / "identity.json"
    original.write_text(json.dumps({**FITTED, "fitted_on": {**pair, "layout": "qfirst"}}))
    rows = original.with_suffix(".rows.jsonl")
    rows.write_text(json.dumps(header) + "\n")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        result = client.post("/api/calibrate", json={"id": "mini", "input": str(rows), "from_rows": True})
        assert result.status_code == 400 and result.json()["code"] == "calibration_rows_layout_unknown"


def test_saved_rows_comparison_modes_use_engine_semantics(tmp_path, monkeypatch):
    _, app = make_app(tmp_path)
    original = tmp_path / "source.json"
    rows = original.with_suffix(".rows.jsonl")
    original.write_text(json.dumps({**FITTED, "fitted_on": {"model": "org/model", "layout": "qfirst"}}))
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(FITTED))
    custom = tmp_path / "custom.json"
    custom.write_text(
        json.dumps({**FITTED, "temperature_per_prim": {"choice": 1.7}, "fitted_on": {"layout": "qfirst"}})
    )
    header = {
        "basal_calibrate_rows": 1,
        "fmt": 2,
        "kind": "mlx",
        "quant": None,
        "model": "org/model",
        "engine": "mlx",
        "data": "input.jsonl",
        "requests": 1,
        "baseline": str(baseline),
        "orders": 1,
    }
    row = {
        "line": 1,
        "req": 0,
        "name": "q",
        "prim": "choice",
        "gold": 0,
        "keys": ["0", "1"],
        "group": "1",
        "family": "q",
        "p": [0.99, 0.01],
    }
    rows.write_text(json.dumps(header) + "\n" + json.dumps(row) + "\n")
    import os

    expanduser = os.path.expanduser
    monkeypatch.setattr(os.path, "expanduser", lambda value: str(tmp_path) if value == "~" else expanduser(value))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        for compare in ("shipped", "none", "~/custom.json"):
            response = client.post(
                "/api/calibrate",
                json={"id": "mini", "input": str(rows), "from_rows": True, "advanced": {"compare": compare}},
            )
            assert response.status_code == 202, response.text
            job = wait_job(client)
            assert job["phase"] == "done_with_warnings", job
            result = json.loads(Path(job["result"]["file"]).read_text())
            assert result["fitted_on"]["layout"] == "qfirst"
            assert "basal-ui-refit-" not in json.dumps(result) + job["result"]["report"]
            if compare == "shipped":
                assert result["baseline"] == str(baseline)
                assert result["temperature_per_prim"]["choice"] == 1.2
                assert result["thresholds"]["0.05"]["per_type"]["choice"]["confidence"] is None
                assert "thresholds are not compared or kept" in job["result"]["report"]
            elif compare == "none":
                assert result["baseline"] == "none" and not result["temperature_per_prim"]
            else:
                assert result["baseline"] == str(custom) and result["temperature_per_prim"]["choice"] == 1.7


@pytest.mark.parametrize(
    "header,pair",
    [
        (
            {"model": "/cache/models--org--model/snapshots/abc", "revision": "def"},
            {"model": "org/model", "revision": "abc"},
        ),
        (
            {"model": "/cache/models--org--model/snapshots/abc"},
            {"model": "/cache/models--org--model/snapshots/abc", "revision": "def"},
        ),
    ],
)
def test_refit_rejects_internal_revision_contradictions(tmp_path, header, pair):
    _, app = make_app(tmp_path)
    original = tmp_path / "identity.json"
    original.write_text(json.dumps({**FITTED, "fitted_on": {**pair, "layout": "qfirst"}}))
    rows = original.with_suffix(".rows.jsonl")
    rows.write_text(json.dumps({"basal_calibrate_rows": 1, **header}) + "\n")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post("/api/calibrate", json={"id": "mini", "input": str(rows), "from_rows": True})
        assert response.status_code == 400 and response.json()["code"] == "calibration_rows_layout_unknown"
        assert "disagree" in response.json()["detail"]


@pytest.mark.parametrize("failure", ["strict", "cancel"])
def test_refit_failed_or_cancelled_after_engine_write_does_not_publish(tmp_path, monkeypatch, failure):
    import basal_ui.app as app_module

    written, release = threading.Event(), threading.Event()

    def fit(argv):
        Path(argv[argv.index("--out") + 1]).write_text(json.dumps(FITTED))
        written.set()
        if failure == "strict":
            assert "--strict" in argv and "--reference" in argv
            raise SystemExit("strict check failed")
        assert release.wait(10)

    monkeypatch.setattr(calibrate, "main", fit)
    monkeypatch.setattr(app_module.time, "strftime", lambda *_: "fixed")
    _, app = make_app(tmp_path)
    original = tmp_path / "source.json"
    original.write_text(json.dumps({**FITTED, "fitted_on": {"model": "org/model", "layout": "qfirst"}}))
    rows = original.with_suffix(".rows.jsonl")
    rows.write_text(json.dumps({"basal_calibrate_rows": 1, "model": "org/model"}) + "\n")
    directory = settings.calibrations_dir("mini")
    directory.mkdir(parents=True)
    existing = directory / "fixed.json"
    existing.write_text('{"existing":"unchanged"}')
    try:
        with TestClient(app, base_url="http://127.0.0.1") as client:
            body = {"id": "mini", "input": str(rows), "from_rows": True}
            if failure == "strict":
                body["advanced"] = {"strict": True, "reference": "basal-1.5"}
            response = client.post("/api/calibrate", json=body)
            assert response.status_code == 202
            assert written.wait(10)
            if failure == "cancel":
                client.delete("/api/calibrate")
                release.set()
            job = wait_job(client)
            assert job["phase"] == ("error" if failure == "strict" else "cancelled")
            assert existing.read_text() == '{"existing":"unchanged"}'
            assert list(directory.glob("*.json")) == [existing]
            assert not list(directory.glob(".calibration-*"))
    finally:
        release.set()


def test_successful_refit_publishes_only_corrected_layout(tmp_path, monkeypatch):
    def fit(argv):
        Path(argv[argv.index("--out") + 1]).write_text(json.dumps(FITTED))

    monkeypatch.setattr(calibrate, "main", fit)
    _, app = make_app(tmp_path)
    original = tmp_path / "source.json"
    original.write_text(json.dumps({**FITTED, "fitted_on": {"model": "org/model", "layout": "qfirst"}}))
    rows = original.with_suffix(".rows.jsonl")
    rows.write_text(json.dumps({"basal_calibrate_rows": 1, "model": "org/model"}) + "\n")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post("/api/calibrate", json={"id": "mini", "input": str(rows), "from_rows": True})
        assert response.status_code == 202
        job = wait_job(client)
        assert job["phase"] == "done_with_warnings", job
        output = Path(job["result"]["file"])
        assert json.loads(output.read_text())["fitted_on"]["layout"] == "qfirst"
        assert (
            next(
                f["layout"]
                for f in client.get("/api/calibrations", params={"id": "mini"}).json()["files"]
                if f["file"] == str(output)
            )
            == "qfirst"
        )
        assert not list(output.parent.glob(".calibration-*"))


def test_refit_uses_declared_paired_revision_not_baseline_revision(tmp_path, monkeypatch):
    def fit(argv):
        Path(argv[argv.index("--out") + 1]).write_text(json.dumps(FITTED))

    monkeypatch.setattr(calibrate, "main", fit)
    _, app = make_app(tmp_path)
    original = tmp_path / "source.json"
    original.write_text(
        json.dumps({**FITTED, "fitted_on": {"model": "org/model", "revision": "producer", "layout": "qfirst"}})
    )
    rows = original.with_suffix(".rows.jsonl")
    baseline = tmp_path / "models--org--model" / "snapshots" / "other-revision" / "CALIBRATION.json"
    baseline.parent.mkdir(parents=True)
    baseline.write_text(json.dumps(FITTED))
    rows.write_text(json.dumps({"basal_calibrate_rows": 1, "model": "org/model", "baseline": str(baseline)}) + "\n")
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post("/api/calibrate", json={"id": "mini", "input": str(rows), "from_rows": True})
        assert response.status_code == 202
        job = wait_job(client)
        assert job["phase"] == "done_with_warnings", job
        fitted = json.loads(Path(job["result"]["file"]).read_text())["fitted_on"]
        assert fitted["revision"] == "producer" and fitted["layout"] == "qfirst"
