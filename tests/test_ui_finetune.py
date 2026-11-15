"""Fine-tuning admission, ownership and result contracts."""

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from basal_ui import engine, finetune, settings
from tests.test_ui_app import HW, MINI, make_fleet


def labels(path):
    row = {"state": "synthetic", "question": "Choose", "options": ["a", "b"], "gold": 0}
    path.write_text("".join(json.dumps({**row, "group": f"g{i}"}) + "\n" for i in range(30)))


def report():
    return {
        "train_decisions": 2,
        "heldout_lines": 1,
        "scored": 1,
        "skipped": {},
        "top1_before": 0.0,
        "top1_after": 0.0,
        "steps": 1,
        "seconds": 0,
    }


def test_native_input_validation_and_immutable_snapshot(tmp_path):
    if not engine.feature("finetune"):
        assert finetune.engine.feature("finetune") is False
        return
    source = tmp_path / "labels.jsonl"
    labels(source)
    original = source.read_text()
    temp, snapshot, summary = finetune._snapshot(str(source), 0.25)
    try:
        source.write_text("changed")
        assert snapshot.read_text() == original
        assert summary["training_candidates"] > 0 and summary["heldout_lines"] > 0
        assert snapshot.stat().st_mode & 0o077 == 0
    finally:
        temp.cleanup()
    source.write_text(original + '{"state": "missing questions"}\n')
    with pytest.raises(ValueError, match="line 31"):
        finetune._snapshot(str(source), 0.25)
    source.write_text('{"n": 1e999}\n')
    with pytest.raises(ValueError, match="line 1"):
        finetune._snapshot(str(source), 0.25)


def test_report_zero_is_not_missing_and_bad_counts_are_rejected(tmp_path):
    path = tmp_path / "finetune.json"
    path.write_text(json.dumps(report() | {"output": "/untrusted"}))
    parsed = finetune._report(path)
    assert parsed["top1_before"] == parsed["top1_after"] == 0.0
    assert "output" not in parsed
    path.write_text(json.dumps(report() | {"scored": 0}))
    with pytest.raises(ValueError, match="inconsistent"):
        finetune._report(path)
    path.write_text(json.dumps(report() | {"steps": True}))
    with pytest.raises(ValueError, match="steps"):
        finetune._report(path)


@pytest.mark.parametrize(
    "body",
    [
        {"epochs": True},
        {"lr": float("nan")},
        {"rank": 1.5},
        {"alpha": False},
        {"holdout": 1},
        {"max_len": 0},
        {"max_steps": True},
        {"grad_checkpointing": 1},
    ],
)
def test_invalid_parameters_are_rejected(body):
    with pytest.raises(ValueError):
        finetune._parameters(body)


def test_training_reservation_snapshot_and_safe_delete(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    settings.set_models_dir(str(tmp_path))
    fleet = make_fleet(tmp_path, HW)
    addons = SimpleNamespace(inventory=lambda: {"extras": [{"id": "finetune", "installed": True}]})
    service = finetune.FineTune(fleet, settings, lambda: HW, addons)
    if not engine.feature("finetune"):
        assert service.availability()[0] is False
        fleet.shutdown()
        return
    model = tmp_path / "base"
    model.mkdir()
    (model / "basal.json").write_text("{}")
    base_file = model / "weights"
    base_file.write_text("untouched")
    monkeypatch.setattr(finetune.catalog, "is_downloaded", lambda *args, **kwargs: model)
    monkeypatch.setattr(finetune.catalog, "assess", lambda *args: SimpleNamespace(est_memory_bytes=100))
    source = tmp_path / "labels.jsonl"
    labels(source)
    original = source.read_text()
    entered, proceed = threading.Event(), threading.Event()

    def child(job, module, argv, **kwargs):
        assert module == "finetune" and "--force" not in argv
        assert Path(argv[0]).read_text() == original
        assert kwargs["env"]["HF_HUB_OFFLINE"] == "1"
        output = Path(argv[argv.index("--out") + 1])
        assert output.stat().st_mode & 0o077 == 0 and not list(output.iterdir())
        entered.set()
        assert proceed.wait(5)
        job.check()
        (output / "lora").mkdir()
        (output / "lora" / "adapter_config.json").write_text('{"peft_type":"LORA","r":1,"lora_alpha":1}')
        (output / "lora" / "adapter_model.safetensors").write_bytes(b"weights")
        (output / "model.safetensors").write_bytes(b"merged")
        (output / "basal.json").write_text("{}")
        (output / "config.json").write_text("{}")
        (output / "tokenizer.json").write_text("{}")
        (output / "heldout.jsonl").write_text("synthetic")
        (output / "finetune.json").write_text(json.dumps(report() | {"output": "/wrong"}))

    monkeypatch.setattr(finetune, "run_tool", child)
    try:
        state = service.start(MINI, model, {"out_name": "first", "input": str(source), "max_steps": 1})
        assert state["running"] and entered.wait(5)
        source.write_text("changed")
        assert fleet.reserved["finetune"] == 200
        with pytest.raises(RuntimeError, match="active jobs"):
            service.delete(MINI.id, "first")
        with pytest.raises(RuntimeError):
            fleet.while_idle(lambda: None)
        proceed.set()
        fleet.jobs.get("finetune").thread.join(5)
        assert fleet.jobs.get("finetune").state()["phase"] == "done"
        assert "finetune" not in fleet.reserved
        row = service.results(MINI.id)[0]
        assert row["ready"] and row["top1_before"] == 0.0 and row["output"] != "/wrong"
        assert finetune.FineTune(fleet, settings, lambda: HW, addons).results(MINI.id) == [row]
        service.check_adapter(MINI.id, model, row["adapter"])
        with pytest.raises(ValueError, match="different base"):
            service.check_adapter("other", model, row["adapter"])

        class Runner:
            options = {"adapter": row["adapter"]}

        runner = Runner()
        fleet.retiring[runner] = 0
        with pytest.raises(RuntimeError, match="active adapter"):
            service.delete(MINI.id, "first")
        fleet.retiring.clear()
        assert service.delete(MINI.id, "first")["removed_bytes"] > 0
        assert service.results(MINI.id) == [] and base_file.read_text() == "untouched"
    finally:
        proceed.set()
        fleet.shutdown()


def test_output_symlink_is_never_removed(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    settings.set_models_dir(str(tmp_path))
    fleet = make_fleet(tmp_path, HW)
    service = finetune.FineTune(fleet, settings, lambda: HW, None)
    record = {"id": "mini", "name": "one", "root": str(tmp_path.resolve()), "state": "incomplete"}
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("safe")
    parent = tmp_path / "adapters" / "mini"
    parent.mkdir(parents=True)
    (parent / "one").symlink_to(outside, target_is_directory=True)
    settings.put_finetune(record)
    with pytest.raises(ValueError, match="symlink"):
        service.delete("mini", "one")
    assert (outside / "keep").read_text() == "safe"
    fleet.shutdown()


def test_cancelled_training_keeps_recoverable_output(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    settings.set_models_dir(str(tmp_path))
    fleet = make_fleet(tmp_path, HW)
    addons = SimpleNamespace(inventory=lambda: {"extras": [{"id": "finetune", "installed": True}]})
    service = finetune.FineTune(fleet, settings, lambda: HW, addons)
    if not engine.feature("finetune"):
        assert service.availability()[0] is False
        fleet.shutdown()
        return
    model = tmp_path / "base"
    model.mkdir()
    (model / "basal.json").write_text("{}")
    source = tmp_path / "labels.jsonl"
    labels(source)
    monkeypatch.setattr(finetune.catalog, "is_downloaded", lambda *args, **kwargs: model)
    monkeypatch.setattr(finetune.catalog, "assess", lambda *args: SimpleNamespace(est_memory_bytes=100))
    entered = threading.Event()

    def child(job, module, argv, **kwargs):
        output = Path(argv[argv.index("--out") + 1])
        (output / "partial").write_text("partial")
        entered.set()
        assert job.cancel.wait(5)
        job.check()

    monkeypatch.setattr(finetune, "run_tool", child)
    try:
        service.start(MINI, model, {"out_name": "cancelled", "input": str(source)})
        assert entered.wait(5)
        fleet.jobs.cancel("finetune")
        fleet.jobs.get("finetune").thread.join(5)
        assert fleet.jobs.get("finetune").state()["phase"] == "cancelled"
        assert "finetune" not in fleet.reserved
        assert service.results(MINI.id)[0]["ready"] is False
        assert service.delete(MINI.id, "cancelled")["removed_bytes"] == len("partial")
    finally:
        fleet.shutdown()


def test_partial_cleanup_reports_retained_owned_location(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from tests.test_ui_app import app_for

    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    settings.set_models_dir(str(tmp_path))
    app, fleet = app_for(tmp_path, [MINI])
    path = tmp_path / "adapters" / "mini" / "partial"
    path.mkdir(parents=True, mode=0o700)
    (path / "first").write_text("first")
    (path / "second").write_text("second")
    info = path.stat()
    record = {
        "id": "mini",
        "name": "partial",
        "root": str(tmp_path.resolve()),
        "state": "incomplete",
        "device": info.st_dev,
        "inode": info.st_ino,
    }
    settings.put_finetune(record)
    real_remove = finetune._remove_contents

    def fail_after_removing_one(fd):
        import os

        os.unlink("first", dir_fd=fd)
        raise OSError("synthetic cleanup failure")

    monkeypatch.setattr(finetune, "_remove_contents", fail_after_removing_one)
    try:
        with TestClient(app, base_url="http://127.0.0.1") as client:
            response = client.post("/api/finetune/delete", json={"id": "mini", "name": "partial"})
            result = response.json()
            retained = Path(result["retained_path"])
            assert response.status_code == 200 and result["removed_bytes"] == 0
            assert (retained / "second").read_text() == "second"
            assert settings.finetune_results()[0]["state"] == "cleanup_pending"
            assert client.get("/api/finetune", params={"id": "mini"}).json()["results"][0]["retained_path"] == str(
                retained
            )
            assert not fleet.reserved
            monkeypatch.setattr(finetune, "_remove_contents", real_remove)
            assert client.post("/api/finetune/delete", json={"id": "mini", "name": "partial"}).status_code == 200
            assert settings.finetune_results() == []
    finally:
        monkeypatch.setattr(finetune, "_remove_contents", real_remove)
        fleet.shutdown()


@pytest.mark.parametrize("after_commit", [False, True])
def test_registry_failure_restores_intact_output(tmp_path, monkeypatch, after_commit):
    from fastapi.testclient import TestClient

    from tests.test_ui_app import app_for

    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    settings.set_models_dir(str(tmp_path))
    app, fleet = app_for(tmp_path, [MINI])
    path = tmp_path / "adapters" / "mini" / "owned"
    path.mkdir(parents=True, mode=0o700)
    (path / "heldout.jsonl").write_text("synthetic labels")
    info = path.stat()
    record = {
        "id": "mini",
        "name": "owned",
        "root": str(tmp_path.resolve()),
        "state": "incomplete",
        "device": info.st_dev,
        "inode": info.st_ino,
    }
    settings.put_finetune(record)
    original_put = settings.put_finetune

    def fail_put(value):
        if value.get("state") == "cleanup_pending":
            if after_commit:
                original_put(value)
            raise OSError("disk full")
        original_put(value)

    monkeypatch.setattr(settings, "put_finetune", fail_put)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.post("/api/finetune/delete", json={"id": "mini", "name": "owned"})
        assert response.status_code == 400
    assert (path / "heldout.jsonl").read_text() == "synthetic labels"
    assert settings.finetune_results() == [record]
    assert not path.with_name(".owned.deleting").exists()
    fleet.shutdown()


def test_merged_weights_require_index_or_canonical_file(tmp_path):
    for name in ("basal.json", "config.json", "heldout.jsonl", "tokenizer.json"):
        (tmp_path / name).write_text("synthetic")
    shards = ["model-00001-of-00002.safetensors", "model-00002-of-00002.safetensors"]
    for name in shards:
        (tmp_path / name).write_bytes(b"weight")
    with pytest.raises(ValueError, match="merged weights"):
        finetune._complete(tmp_path)
    (tmp_path / shards[0]).unlink()
    with pytest.raises(ValueError, match="merged weights"):
        finetune._complete(tmp_path)
    (tmp_path / shards[0]).write_bytes(b"weight")
    index = tmp_path / "model.safetensors.index.json"
    index.write_text(json.dumps({"weight_map": {"a": shards[0], "b": shards[1]}}))
    finetune._complete(tmp_path)
    (tmp_path / shards[1]).unlink()
    with pytest.raises(ValueError, match="merged weights"):
        finetune._complete(tmp_path)
    index.unlink()
    (tmp_path / "model.safetensors").write_bytes(b"weight")
    finetune._complete(tmp_path)


def test_http_returns_owned_report_and_rejects_wrong_base(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from tests.test_ui_app import SMALL, app_for

    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    settings.set_models_dir(str(tmp_path))
    app, fleet = app_for(tmp_path, [MINI, SMALL])
    model = tmp_path / "base"
    model.mkdir()
    (model / "basal.json").write_text("{}")
    source = tmp_path / "labels.jsonl"
    labels(source)
    monkeypatch.setattr(finetune.catalog, "is_downloaded", lambda *args, **kwargs: model)
    monkeypatch.setattr(
        finetune.catalog,
        "assess",
        lambda variant, *args: SimpleNamespace(variant=variant, mode="eager", est_memory_bytes=100),
    )
    if not engine.feature("finetune"):
        with TestClient(app, base_url="http://127.0.0.1") as client:
            assert client.get("/api/finetune").json()["available"] is False
        return
    monkeypatch.setattr(app.state.finetune, "availability", lambda: (True, None, None))

    def child(job, module, argv, **kwargs):
        output = Path(argv[argv.index("--out") + 1])
        (output / "lora").mkdir()
        (output / "lora" / "adapter_config.json").write_text('{"peft_type":"LORA","r":1,"lora_alpha":1}')
        (output / "lora" / "adapter_model.safetensors").write_bytes(b"adapter")
        (output / "model.safetensors").write_bytes(b"merged")
        (output / "basal.json").write_text("{}")
        (output / "config.json").write_text("{}")
        (output / "tokenizer.json").write_text("{}")
        (output / "heldout.jsonl").write_text("held")
        (output / "finetune.json").write_text(json.dumps(report()))

    monkeypatch.setattr(finetune, "run_tool", child)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        started = client.post("/api/finetune", json={"id": MINI.id, "input": str(source), "out_name": "http"})
        assert started.status_code == 202
        fleet.jobs.get("finetune").thread.join(5)
        row = client.get("/api/finetune", params={"id": MINI.id}).json()["results"][0]
        assert row["ready"] and row["top1_before"] == 0.0
        wrong = client.put("/api/models/options", json={"id": SMALL.id, "options": {"adapter": row["adapter"]}})
        assert wrong.status_code == 400
        assert "different base" in wrong.json()["detail"]
        removed = client.post("/api/finetune/delete", json={"id": MINI.id, "name": "http"})
        assert removed.status_code == 200 and removed.json()["removed_bytes"] > 0
        assert client.get("/api/finetune", params={"id": MINI.id}).json()["results"] == []


def test_attached_registry_discovery_preserves_missing_files_and_certification(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from basal_ui import catalog
    from tests.test_ui_app import app_for

    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    models = settings.set_models_dir(str(tmp_path / "models"))
    model_id = "attached/qwen-qwen3-4b"
    output = models / model_id
    output.mkdir(parents=True)
    (output / "basal.json").write_text(
        json.dumps(
            {
                "prompt": 1,
                "layout": "canonical",
                "attached": {"base": "Qwen/Qwen3-4B", "revision": catalog.ATTACH_REVISION},
                "readout": {"codes": "AB"},
            }
        )
    )
    (output / "CALIBRATION.json").write_text('{"thresholds": {}}')
    (output / "config.json").write_text("{}")
    (output / "tokenizer.json").write_text("{}")
    names = [f"model-{i:05d}-of-00003.safetensors" for i in range(1, 4)]
    (output / "model.safetensors.index.json").write_text(json.dumps({"weight_map": dict(enumerate(names))}))
    for name in names:
        (output / name).write_bytes(b"synthetic")
    data = settings.load()
    data["attached"] = [
        {
            "id": model_id,
            "base": "Qwen/Qwen3-4B",
            "revision": catalog.ATTACH_REVISION,
            "out_dir": str(output),
            "format": 1,
            "created": 1,
        }
    ]
    settings.save(data)
    app, _ = app_for(tmp_path, [MINI])
    with TestClient(app, base_url="http://127.0.0.1") as client:
        row = next(m for m in client.get("/api/models").json()["models"] if m["id"] == model_id)
        assert row["downloaded"] and row["uncalibrated"]
        if not engine.feature("attach"):
            assert not row["runnable"] and row["reason_code"] == "attach_unavailable"
        (output / names[0]).unlink()
        row = next(m for m in client.get("/api/models").json()["models"] if m["id"] == model_id)
        assert not row["downloaded"] and row["uncalibrated"]
        response = client.post("/api/load", json={"id": model_id})
        assert response.status_code in (400, 409)
        assert settings.attached_models()[0]["id"] == model_id


def test_http_finetune_errors_have_actionable_codes(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from tests.test_ui_app import app_for

    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    settings.set_models_dir(str(tmp_path))
    model = tmp_path / "base"
    model.mkdir()
    (model / "basal.json").write_text("{}")
    monkeypatch.setattr(finetune.catalog, "is_downloaded", lambda *args, **kwargs: model)
    app, fleet = app_for(tmp_path, [MINI])
    monkeypatch.setattr(app.state.finetune, "availability", lambda: (True, None, None))
    output = tmp_path / "adapters" / "mini" / "owned"
    output.mkdir(parents=True, mode=0o700)
    info = output.stat()
    settings.put_finetune(
        {
            "id": "mini",
            "name": "owned",
            "root": str(tmp_path.resolve()),
            "state": "incomplete",
            "device": info.st_dev,
            "inode": info.st_ino,
        }
    )

    class Runner:
        options = {"adapter": str(output / "lora")}

    runner = Runner()
    fleet.retiring[runner] = 0
    with TestClient(app, base_url="http://127.0.0.1") as client:
        name = client.post("/api/finetune", json={"id": "mini", "out_name": "../bad"}).json()
        assert name["code"] == "finetune_name"
        parameter = client.post("/api/finetune", json={"id": "mini", "out_name": "new", "rank": True}).json()
        assert parameter["code"] == "finetune_parameter" and parameter["params"]["parameter"] == "rank"
        source = client.post("/api/finetune", json={"id": "mini", "out_name": "new", "input": ""}).json()
        assert source["code"] == "finetune_input"
        missing = client.post("/api/finetune/delete", json={"id": "mini", "name": "unknown"}).json()
        assert missing["code"] == "finetune_missing"
        busy = client.post("/api/finetune/delete", json={"id": "mini", "name": "owned"})
        assert busy.status_code == 409 and busy.json()["code"] == "finetune_active_adapter"
        fleet.retiring.clear()
        moved = output.with_name("moved")
        output.rename(moved)
        output.symlink_to(moved, target_is_directory=True)
        ownership = client.post("/api/finetune/delete", json={"id": "mini", "name": "owned"})
        assert ownership.status_code == 400 and ownership.json()["code"] == "finetune_ownership"
        output.unlink()
        moved.rename(output)


@pytest.mark.parametrize("fail_cleanup", [False, True])
def test_delete_excludes_prepared_admissions_and_releases_barrier(tmp_path, monkeypatch, fail_cleanup):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    settings.set_models_dir(str(tmp_path))
    fleet = make_fleet(tmp_path, HW)
    service = finetune.FineTune(fleet, settings, lambda: HW, None)
    path = tmp_path / "adapters" / "mini" / "held"
    path.mkdir(parents=True, mode=0o700)
    (path / "data").write_text("synthetic")
    info = path.stat()
    settings.put_finetune(
        {
            "id": "mini",
            "name": "held",
            "root": str(tmp_path.resolve()),
            "state": "incomplete",
            "device": info.st_dev,
            "inode": info.st_ino,
        }
    )
    entered, proceed = threading.Event(), threading.Event()
    original = finetune._remove_contents

    def held(fd):
        entered.set()
        assert proceed.wait(5)
        if fail_cleanup:
            raise OSError("synthetic cleanup failure")
        original(fd)

    monkeypatch.setattr(finetune, "_remove_contents", held)
    errors = []

    def delete():
        try:
            service.delete("mini", "held")
        except Exception as exc:
            errors.append(exc)

    thread = threading.Thread(target=delete)
    thread.start()
    try:
        assert entered.wait(5)
        assessment = SimpleNamespace(variant=MINI, est_memory_bytes=1)
        adapter = str(path / "lora")
        quarantine_adapter = str(path.with_name(".held.deleting") / "lora")
        with pytest.raises(RuntimeError, match="deletion"):
            fleet.load(assessment, options={"adapter": adapter})
        with pytest.raises(RuntimeError, match="deletion"):
            fleet.start_temporary("test", assessment, None, options={"adapter": quarantine_adapter})
        with pytest.raises(RuntimeError, match="deletion"):
            fleet.restart(assessment, options={"adapter": adapter})
        with pytest.raises(RuntimeError, match="deletion"):
            fleet.reserve("test", 1)
        with pytest.raises(RuntimeError, match="deletion"):
            fleet.jobs.start("test", "mini", "test", lambda job: None)
    finally:
        proceed.set()
        thread.join(5)
    assert not thread.is_alive() and not errors
    if fail_cleanup:
        with pytest.raises(RuntimeError, match="pending cleanup"):
            fleet.load(assessment, options={"adapter": quarantine_adapter})
    fleet.reserve("test", 1)
    fleet.release("test")
    fleet.shutdown()


@pytest.mark.parametrize("after_commit", [False, True])
def test_final_registry_failure_retains_cleanup_record(tmp_path, monkeypatch, after_commit):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    settings.set_models_dir(str(tmp_path))
    fleet = make_fleet(tmp_path, HW)
    service = finetune.FineTune(fleet, settings, lambda: HW, None)
    path = tmp_path / "adapters" / "mini" / "last"
    path.mkdir(parents=True, mode=0o700)
    (path / "data").write_text("synthetic")
    info = path.stat()
    settings.put_finetune(
        {
            "id": "mini",
            "name": "last",
            "root": str(tmp_path.resolve()),
            "state": "incomplete",
            "device": info.st_dev,
            "inode": info.st_ino,
        }
    )
    remove = settings.remove_finetune

    def fail(*args):
        if after_commit:
            remove(*args)
        raise OSError("disk full")

    monkeypatch.setattr(settings, "remove_finetune", fail)
    with pytest.raises(OSError, match="disk full"):
        service.delete("mini", "last")
    assert settings.finetune_results()[0]["state"] == "cleanup_pending"
    assert service.results("mini")[0]["cleanup_pending"]
    assert not path.exists() and not path.with_name(".last.deleting").exists()
    monkeypatch.setattr(settings, "remove_finetune", remove)
    assert service.delete("mini", "last")["missing"]
    assert settings.finetune_results() == []
    fleet.shutdown()
