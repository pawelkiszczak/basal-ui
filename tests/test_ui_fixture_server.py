"""scripts/ui_fixture_server.py against the basal-ui code it plugs into. The fixture runs the real Fleet and Runner and
replaces only the hooks in its adapter (the catalog's disk functions and Runner._build_command): these tests fail when
one of them no longer matches what it replaces, instead of the a11y gate or the screenshots drifting silently."""

import importlib.util
import inspect
import time
from pathlib import Path

import pytest

from basal_ui import catalog, settings
from basal_ui.fleet import Runner, _child_settings

SCRIPT = Path(__file__).parents[1] / "scripts/ui_fixture_server.py"


@pytest.fixture(scope="module")
def fixture_mod():
    spec = importlib.util.spec_from_file_location("ui_fixture_server", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # no side effects: the server's setup is in main()
    return module


def params(fn, skip=0):
    return list(inspect.signature(fn).parameters)[skip:]


def test_each_adapter_hook_has_the_signature_of_what_it_replaces(fixture_mod, tmp_path):
    disk = fixture_mod.FakeDisk(tmp_path)
    for name in fixture_mod.CATALOG_HOOKS:
        assert params(getattr(disk, name)) == params(getattr(catalog, name)), name
    assert params(fixture_mod.child_command) == ["runner", *params(Runner._build_command, skip=1)]


def test_install_replaces_only_the_adapter_hooks_and_restores_them(fixture_mod, tmp_path):
    before = {name: getattr(catalog, name) for name in fixture_mod.CATALOG_HOOKS}
    build, start = Runner.__dict__["_build_command"], Runner.__dict__["_start"]
    disk = fixture_mod.FakeDisk(tmp_path)
    restore = fixture_mod.install(disk)
    try:
        assert all(getattr(catalog, name) == getattr(disk, name) for name in fixture_mod.CATALOG_HOOKS)
        assert Runner._build_command is fixture_mod.child_command
        assert Runner.__dict__["_start"] is start  # the real load sequence runs
    finally:
        restore()
    assert {name: getattr(catalog, name) for name in fixture_mod.CATALOG_HOOKS} == before
    assert Runner.__dict__["_build_command"] is build


def wait_ready(fleet, model_id, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        runner = fleet.runners.get(model_id)
        if runner is not None and runner.status()["state"] in ("ready", "error"):
            return runner
        time.sleep(0.05)
    pytest.fail(f"{model_id} did not reach ready in {timeout} s")


def test_a_fixture_load_runs_the_real_runner_to_ready_and_unloads(fixture_mod, tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    disk = fixture_mod.FakeDisk(tmp_path / "models")
    restore = fixture_mod.install(disk)
    fleet = None
    try:
        settings.set_models_dir(str(disk.root))
        variants = {v.id: v for v in fixture_mod.fixture_variants()}
        hw = fixture_mod.fixture_hardware()
        mini, omni = variants[fixture_mod.MINI], variants[fixture_mod.OMNI]
        disk.snapshot(omni)  # mini is not on disk: its load goes through Downloads and FakeDisk.download
        fleet = fixture_mod.make_fleet(tmp_path, hw)
        fleet.load(catalog.assess(mini, hw))
        fleet.load(catalog.assess(omni, hw), media=True)
        text, media = wait_ready(fleet, mini.id), wait_ready(fleet, omni.id)
        for runner, v in ((text, mini), (media, omni)):
            assert runner.status()["state"] == "ready", runner.status()
            assert runner.process is not None and runner.process.poll() is None  # a real child process
            assert runner.command[2:5] == ["--child", "--name", v.id]
            assert runner.loaded_path == str(disk.on_disk[v.id])
            assert runner.serve_snapshot == _child_settings(settings.serve_settings(), None)
            assert runner.models_entry["certified"] == fixture_mod.CERTIFIED
            assert runner.models_entry["mode"] == runner.mode
        assert media.media == list(omni.media) and media.mode == "eager"
        assert {m["id"] for m in settings.last_models()} == {mini.id, omni.id}  # remembered for auto-load
        response = fleet.decide(mini.id, {"state": "s", "questions": {"q": {"type": "choice", "options": ["a", "b"]}}})
        assert response.status_code == 200 and set(response.json()["answers"]["q"]["probabilities"]) == {"0", "1"}
        processes = [text.process, media.process]
        fleet.shutdown()
        fleet = None
        for process in processes:
            process.wait(10)  # unloading ends the children
    finally:
        if fleet is not None:
            fleet.shutdown()
        restore()


def test_rng_runs_on_every_supported_python(fixture_mod):
    """int.from_bytes needs its byte order before Python 3.11 (requires-python is >=3.10)."""
    rng = fixture_mod.Rng("seed")
    values = [rng.random() for _ in range(70_000)]  # past 2**16 draws: the counter is not limited to two bytes
    assert all(0 <= x < 1 for x in values)
    assert fixture_mod.Rng("seed").random() == values[0]
