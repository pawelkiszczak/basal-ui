import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from basal_ui import catalog, engine, settings
from basal_ui.app import create_app
from basal_ui.catalog import Variant
from basal_ui.fleet import Fleet
from basal_ui.hardware import Hardware
from basal_ui.history import History
from basal_ui.stats import Stats

APPLE = Hardware("apple", "mps", 1024**4, None, None, frozenset({"mps"}))
MINI = Variant("mini", "Remek/basal-2.0-mini", "basal-2.0", "mini", "bf16", "bf16", None, 100, False)


def app_for(tmp_path, hw=APPLE):
    fleet = Fleet(
        models_dir_fn=lambda: tmp_path,
        platform_fn=lambda: hw.platform,
        settings_mod=settings,
        hardware_fn=lambda: hw,
        stats=Stats(tmp_path / "stats.json"),
        history=History(tmp_path / "history.jsonl"),
    )
    app = create_app(
        catalog_fn=lambda **_: ([MINI], "test"),
        hardware_fn=lambda: hw,
        fleet=fleet,
        stats=fleet.stats,
        history=fleet.history,
    )
    return fleet, app


def test_finetune_runs_with_reserved_memory_and_lists_adapter(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    monkeypatch.setattr(settings, "models_dir", lambda: tmp_path)
    model = tmp_path / "model"
    model.mkdir()
    (model / "basal.json").write_text("{}")
    monkeypatch.setattr(catalog, "is_downloaded", lambda *args, **kwargs: model)
    monkeypatch.setattr(engine, "feature", lambda name: True)
    monkeypatch.setattr("basal_ui.addons._extra_installed", lambda extra: True)
    source = tmp_path / "labelled.jsonl"
    source.write_text('{"state":"s","question":"q?","options":["a","b"],"gold":0}\n')
    fleet, app = app_for(tmp_path)
    calls, reserved = [], []

    def run_tool(job, module, argv, out_path=None):
        calls.append((module, argv))
        reserved.append(dict(fleet.reserved))
        Path(out_path).parent.joinpath("lora").mkdir(parents=True)
        (Path(out_path).parent / "lora" / "adapter_config.json").write_text("{}")
        report = {"top1_before": 0.5, "top1_after": 0.75, "train_decisions": 10, "heldout_lines": 3, "scored": 2}
        Path(out_path).write_text(json.dumps(report))
        return report

    monkeypatch.setattr("basal_ui.jobs.run_tool", run_tool)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post(
            "/api/finetune",
            json={"id": "mini", "input": str(source), "out_name": "support", "epochs": 2.0},
        )
        assert response.status_code == 202, response.text
        deadline = time.monotonic() + 5
        while client.get("/api/status").json()["jobs"]["finetune"]["running"] and time.monotonic() < deadline:
            time.sleep(0.01)
        state = client.get("/api/status").json()["jobs"]["finetune"]
        assert state["phase"] == "done", state
        assert calls[0][0] == "finetune"
        assert calls[0][1] == [
            str(source),
            "--model",
            str(model),
            "--out",
            str(tmp_path / "adapters" / "mini" / "support"),
            "--epochs",
            "2.0",
            "--device",
            "mps",
        ]
        assert reserved[0] == {"finetune": 2 * catalog.assess(MINI, APPLE).est_memory_bytes}
        assert fleet.reserved == {}
        assert client.get("/api/models/options", params={"id": "mini"}).json()["adapters"] == [
            str(tmp_path / "adapters" / "mini" / "support" / "lora")
        ]
        history = client.get("/api/finetune", params={"id": "mini"}).json()
        assert history["results"][0]["top1_after"] == 0.75


def test_finetune_refuses_missing_engine_feature_and_peft(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    monkeypatch.setattr(settings, "models_dir", lambda: tmp_path)
    source = tmp_path / "labelled.jsonl"
    source.write_text('{"state":"s","question":"q?","options":["a","b"],"gold":0}\n')
    _, app = app_for(tmp_path)
    monkeypatch.setattr(engine, "feature", lambda name: False)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        denied = client.post("/api/finetune", json={"id": "mini", "input": str(source), "out_name": "x"})
        assert denied.status_code == 400 and "Engine 2.5" in denied.json()["detail"]
        assert client.get("/api/finetune", params={"id": "mini"}).json()["available"] is False

        monkeypatch.setattr(engine, "feature", lambda name: True)
        monkeypatch.setattr("basal_ui.addons._extra_installed", lambda extra: False)
        denied = client.post("/api/finetune", json={"id": "mini", "input": str(source), "out_name": "x"})
        assert denied.status_code == 400 and "peft" in denied.json()["detail"]


def test_finetune_rejects_unsafe_output_name(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    monkeypatch.setattr(settings, "models_dir", lambda: tmp_path)
    model = tmp_path / "model"
    model.mkdir()
    (model / "basal.json").write_text("{}")
    monkeypatch.setattr(catalog, "is_downloaded", lambda *args, **kwargs: model)
    monkeypatch.setattr(engine, "feature", lambda name: True)
    monkeypatch.setattr("basal_ui.addons._extra_installed", lambda extra: True)
    source = tmp_path / "labelled.jsonl"
    source.write_text('{"state":"s","question":"q?","options":["a","b"],"gold":0}\n')
    _, app = app_for(tmp_path)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        denied = client.post("/api/finetune", json={"id": "mini", "input": str(source), "out_name": "../escape"})
        assert denied.status_code == 400 and "single path segment" in denied.json()["detail"]
