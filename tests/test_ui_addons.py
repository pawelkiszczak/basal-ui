"""Safe Engine add-on inventory, provenance, and maintenance behavior."""

import threading
import time
from types import SimpleNamespace

import pytest

from basal_ui import addons
from basal_ui.jobs import Jobs

SOURCE = "git+https://github.com/example/basal.git@" + "a" * 40


class Downloads:
    def any_alive(self):
        return False


class Fleet:
    def __init__(self):
        self.lock = threading.RLock()
        self.stopping = False
        self.runners = {}
        self.temporary = {}
        self.downloads = Downloads()
        self.inflight = {}
        self.reserved = {}
        self.retiring = {}
        self.leases = {}
        self.engine_addon_installing = False
        self._engine_addon_job_target = None
        self.jobs = Jobs({}, lambda *args: None, start_lock=self.lock, start_guard=self._guard_job_start)

    def _guard_job_start(self, kind, target):
        if self.engine_addon_installing and (kind != "engine-addon" or target != self._engine_addon_job_target):
            raise RuntimeError("jobs are unavailable during Engine add-on installation")


def manager(hw=None, extras=frozenset({"media", "gguf"})):
    fleet = Fleet()
    hardware = hw or SimpleNamespace(platform="cpu", engines=frozenset())
    engine_info = lambda: ("2.4.1", SOURCE, extras)
    requirements = lambda extra: ("pillow>=10",) if extra == "media" else ("llama-cpp-python>=0.3",)
    return addons.AddonManager(fleet, lambda: hardware, engine_info, lambda: ["uv"], requirements)


def test_git_engine_source_uses_installed_commit_for_display():
    direct = {
        "url": "https://github.com/a-fork/basal.git",
        "vcs_info": {"vcs": "git", "commit_id": "b" * 40, "requested_revision": "feature"},
    }
    source = addons._recorded_source(direct)
    assert source.startswith("git+https://github.com/a-fork/basal.git@" + "b" * 40)
    assert "PEP 610 identity sha256:" in source


def test_unhashed_engine_archive_source_is_safe_because_it_is_never_reinstalled():
    direct = {"url": "https://example.test/basal.whl", "archive_info": {}}
    assert addons._recorded_source(direct).startswith(direct["url"] + " ")
    with pytest.raises(RuntimeError, match="query parameters"):
        addons._recorded_source({"url": "https://example.test/basal.whl?token=secret"})


def test_archive_source_preserves_its_recorded_hash_for_display():
    direct = {
        "url": "https://example.test/basal.whl",
        "archive_info": {"hashes": {"sha256": "c" * 64}},
    }
    assert addons._recorded_source(direct).startswith("https://example.test/basal.whl#sha256=" + "c" * 64)


@pytest.mark.parametrize("project_url", [None, "https://github.com/rkinas/basal"])
def test_engine_info_accepts_installed_unhashed_rkinas_archive_without_reinstalling(monkeypatch, project_url):
    class Metadata(dict):
        def get_all(self, name, default=None):
            return {"Provides-Extra": ["media", "gguf"], "Project-URL": []}.get(name, default)

    distribution = SimpleNamespace(
        metadata=Metadata({"Home-page": project_url} if project_url else {}),
        files=["basal/server.py"],
        version="2.4.1",
        read_text=lambda name: '{"url":"https://download.example/basal.whl","archive_info":{}}',
    )
    monkeypatch.setattr(addons.importlib.metadata, "distribution", lambda name: distribution)

    version, source, extras = addons._engine_info()
    assert version == "2.4.1"
    assert source.startswith("https://download.example/basal.whl")
    assert "PEP 610 identity sha256:" in source
    assert extras == {"media", "gguf"}


def test_only_selected_extra_requirements_are_extracted_from_engine_metadata(monkeypatch):
    class Metadata:
        def get_all(self, name, default=None):
            return ["media", "gguf"] if name == "Provides-Extra" else default

    distribution = SimpleNamespace(
        metadata=Metadata(),
        requires=[
            "core-lib>=1",
            "conditional-core>=1; python_version >= '3.10'",
            "Pillow>=10; extra == 'media'",
            "llama-cpp-python>=0.3; extra == 'gguf'",
            "bad @ https://packages.example/bad.whl ; extra == 'media'",
        ],
    )
    monkeypatch.setattr(addons.importlib.metadata, "distribution", lambda name: distribution)
    with pytest.raises(RuntimeError):
        addons._engine_requirements("media")
    distribution.requires.pop()
    assert addons._engine_requirements("media") == ("Pillow>=10",)
    assert addons._engine_requirements("gguf") == ("llama-cpp-python>=0.3",)


def test_system_python_is_refused(monkeypatch):
    monkeypatch.setattr(addons.sys, "prefix", "/usr")
    monkeypatch.setattr(addons.sys, "base_prefix", "/usr")
    with pytest.raises(RuntimeError, match="isolated virtual environment"):
        addons._environment()


def test_fresh_extra_probe_failure_includes_restart_and_log_guidance(monkeypatch):
    monkeypatch.setattr(
        addons.subprocess,
        "run",
        lambda *args, **kwargs: addons.subprocess.CompletedProcess(
            args[0],
            1,
            stdout='{"ok": false, "error": "llama_cpp: ImportError: missing symbol"}\n',
            stderr="",
        ),
    )
    with pytest.raises(RuntimeError, match="llama_cpp"):
        addons._verify_install("gguf", "2.4.1", SOURCE)


def test_inventory_lists_only_declared_and_machine_supported_extras(monkeypatch):
    monkeypatch.setattr(addons, "_extra_installed", lambda extra: False)
    subject = manager(SimpleNamespace(platform="rocm", engines=frozenset({"rocm"})), {"media", "gguf"})
    data = subject.inventory()
    media, gguf = data["extras"]
    assert media["compatible"] is True
    assert gguf["compatible"] is False
    assert gguf["installed"] is False
    assert data["environment"]["python"] == addons.sys.executable


def test_install_requires_explicit_confirmation_and_refuses_active_models(monkeypatch):
    monkeypatch.setattr(addons, "_extra_installed", lambda extra: False)
    subject = manager()
    with pytest.raises(ValueError, match="confirmation"):
        subject.install({"extra": "media"})
    subject.fleet.runners["model"] = object()
    with pytest.raises(RuntimeError, match="Unload all models"):
        subject.install({"extra": "media", "confirmed": True})
    assert subject.fleet.engine_addon_installing is False


def test_install_uses_only_declared_extra_requirements_and_preserves_installed_constraints(monkeypatch):
    monkeypatch.setattr(addons, "_extra_installed", lambda extra: False)
    monkeypatch.setattr(addons, "_verify_install", lambda *args: None)
    monkeypatch.setattr(addons, "_installed_constraints", lambda: "torch==2.5.1\nbasal==2.4.1\n")
    called = {}

    class Process:
        stdout = []
        returncode = 0
        pid = 100

        def wait(self):
            return 0

        def poll(self):
            return self.returncode

    def popen(command, **kwargs):
        called["command"], called["env"] = command, kwargs["env"]
        return Process()

    monkeypatch.setattr(addons.subprocess, "Popen", popen)
    subject = manager()
    result = subject.install({"extra": "media", "confirmed": True})
    deadline = time.monotonic() + 2
    while subject.busy() and time.monotonic() < deadline:
        time.sleep(0.01)
    job = subject.fleet.jobs.get(addons.ADDON_JOB)
    assert job.state()["phase"] == "done"
    assert result["kind"] == addons.ADDON_JOB
    assert called["command"][-1] == "pillow>=10"
    assert not any(str(arg).startswith("basal[") for arg in called["command"])
    assert called["command"][called["command"].index("--python") + 1] == addons.sys.executable
    assert called["command"][called["command"].index("--constraint") + 1].endswith("constraints.txt")
    assert f"Installed Engine 2.4.1 source (not reinstalled): {SOURCE}" in job.log.text()
    assert "2.4.1" in job.log.text()
    assert subject.fleet.engine_addon_installing is False


def test_gguf_uses_existing_platform_build_policy(monkeypatch):
    monkeypatch.setenv("CMAKE_ARGS", "-DGGML_CUDA=on")
    monkeypatch.setattr(addons, "_installed_constraints", lambda: "")
    monkeypatch.setattr(addons, "_extra_installed", lambda extra: False)
    monkeypatch.setattr(addons, "_verify_install", lambda *args: None)
    environments, commands = [], []

    class Process:
        stdout = []
        returncode = 0
        pid = 100

        def wait(self):
            return 0

        def poll(self):
            return self.returncode

    monkeypatch.setattr(
        addons.subprocess,
        "Popen",
        lambda command, **kwargs: commands.append(command) or environments.append(kwargs["env"]) or Process(),
    )
    apple = manager(SimpleNamespace(platform="apple", engines=frozenset({"mps"})))
    apple.install({"extra": "gguf", "confirmed": True})
    deadline = time.monotonic() + 2
    while apple.busy() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert environments[-1]["CMAKE_ARGS"] == "-DGGML_METAL=on"
    assert commands[-1][commands[-1].index("--no-binary") : commands[-1].index("--no-binary") + 2] == [
        "--no-binary",
        "llama-cpp-python",
    ]
    assert "--no-cache" in commands[-1]

    cuda = manager(SimpleNamespace(platform="cuda", engines=frozenset({"cuda"})))
    cuda.install({"extra": "gguf", "confirmed": True})
    deadline = time.monotonic() + 2
    while cuda.busy() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert environments[-1]["CMAKE_ARGS"] == "-DGGML_CUDA=on"
    assert commands[-1][commands[-1].index("--no-binary") : commands[-1].index("--no-binary") + 2] == [
        "--no-binary",
        "llama-cpp-python",
    ]
    assert "--no-cache" in commands[-1]

    rocm = manager(SimpleNamespace(platform="cuda", engines=frozenset({"rocm"})))
    rocm.install({"extra": "gguf", "confirmed": True})
    deadline = time.monotonic() + 2
    while rocm.busy() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert "CMAKE_ARGS" not in environments[-1]
    assert commands[-1][commands[-1].index("--no-binary") : commands[-1].index("--no-binary") + 2] == [
        "--no-binary",
        "llama-cpp-python",
    ]
    assert "--no-cache" in commands[-1]
    for command in commands:
        assert command[command.index("--reinstall-package") + 1] == "llama-cpp-python"


def test_install_failure_releases_fleet_maintenance(monkeypatch):
    monkeypatch.setattr(addons, "_installed_constraints", lambda: "")
    monkeypatch.setattr(addons, "_extra_installed", lambda extra: False)

    class Process:
        stdout = []
        returncode = 2
        pid = 100

        def wait(self):
            return 2

        def poll(self):
            return self.returncode

    monkeypatch.setattr(addons.subprocess, "Popen", lambda *args, **kwargs: Process())
    subject = manager()
    subject.install({"extra": "media", "confirmed": True})
    deadline = time.monotonic() + 2
    while subject.busy() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert subject.fleet.jobs.get(addons.ADDON_JOB).state()["phase"] == "error"
    assert subject.fleet.engine_addon_installing is False


def test_cancel_terminates_installer_process_group_and_releases_maintenance(monkeypatch):
    monkeypatch.setattr(addons, "_installed_constraints", lambda: "")
    monkeypatch.setattr(addons, "_extra_installed", lambda extra: False)
    monkeypatch.setattr(addons, "_verify_install", lambda *args: None)
    started, released = threading.Event(), threading.Event()

    class Output:
        def __iter__(self):
            started.set()
            released.wait(2)
            return iter(())

    class Process:
        stdout = Output()
        returncode = None
        pid = 100

        def wait(self):
            self.returncode = -15
            return self.returncode

        def poll(self):
            return self.returncode

    proc = Process()

    def stop(process):
        released.set()
        process.returncode = -15

    monkeypatch.setattr(addons.subprocess, "Popen", lambda *args, **kwargs: proc)
    monkeypatch.setattr(addons, "terminate", stop)
    subject = manager()
    subject.install({"extra": "media", "confirmed": True})
    assert started.wait(2)
    with pytest.raises(RuntimeError, match="already running"):
        subject.install({"extra": "gguf", "confirmed": True})
    subject.cancel()
    deadline = time.monotonic() + 2
    while subject.busy() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert subject.fleet.jobs.get(addons.ADDON_JOB).state()["phase"] == "cancelled"
    assert released.is_set()
    assert subject.fleet.engine_addon_installing is False


def test_fleet_blocks_model_mutation_and_inference_during_environment_write(tmp_path, monkeypatch):
    from tests.test_ui_app import HW, make_fleet

    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    fleet = make_fleet(tmp_path, HW)
    fleet.engine_addon_installing = True
    with pytest.raises(RuntimeError, match="Engine add-on installation"):
        fleet.load(SimpleNamespace(variant=SimpleNamespace(id="model")))
    with pytest.raises(RuntimeError, match="Engine add-on installation"):
        fleet.reserve("benchmark", 1)
    with pytest.raises(RuntimeError, match="Engine add-on installation"), fleet.lease("model"):
        pass
    with pytest.raises(RuntimeError, match="Engine add-on installation"):
        fleet.while_idle(lambda: None)
    with pytest.raises(RuntimeError, match="Engine add-on installation"):
        fleet.stop_temporary("calibration")
    with pytest.raises(RuntimeError, match="jobs are unavailable"):
        fleet.jobs.start("calibrate", "model", "calibrate", lambda job: None)
    fleet.stopping = True
    fleet.shutdown()


def test_unavailable_engine_inventory_returns_a_coded_explanation():
    subject = manager()

    def unavailable():
        raise RuntimeError("Engine distribution is missing")

    subject.engine_info_fn = unavailable
    data = subject.inventory()
    assert data["extras"] == []
    assert data["error_code"] == "addon_inventory_error"
    assert data["error_params"]["message"] == "Engine distribution is missing"
    assert not subject.fleet.jobs.running()


@pytest.mark.parametrize("extra", [None, [], {}, 1, "not-an-addon"])
def test_invalid_extra_never_starts_a_package_mutation(extra):
    subject = manager()
    with pytest.raises(ValueError):
        subject.install({"extra": extra, "confirmed": True})
    assert not subject.fleet.jobs.running()
    assert subject.fleet.engine_addon_installing is False


@pytest.mark.parametrize("boundary", ["constraints", "verification"])
def test_cancellation_during_installer_preparation_or_verification_is_not_success(monkeypatch, boundary):
    entered, released = threading.Event(), threading.Event()
    spawned = []

    def checkpoint():
        entered.set()
        assert released.wait(5)

    def constraints():
        if boundary == "constraints":
            checkpoint()
        return "basal==2.4.1\n"

    def verify(*args):
        if boundary == "verification":
            checkpoint()

    process = SimpleNamespace(stdout=[], returncode=0, wait=lambda: 0, poll=lambda: 0)
    monkeypatch.setattr(addons, "_extra_installed", lambda extra: False)
    monkeypatch.setattr(addons, "_installed_constraints", constraints)
    monkeypatch.setattr(addons, "_verify_install", verify)
    monkeypatch.setattr(addons.subprocess, "Popen", lambda *args, **kwargs: spawned.append(True) or process)
    subject = manager()
    subject.install({"extra": "media", "confirmed": True})
    try:
        assert entered.wait(2)
        subject.cancel()
    finally:
        released.set()
        assert subject.fleet.jobs.join(2) == []
    state = subject.fleet.jobs.get(addons.ADDON_JOB).state()
    assert state["phase"] == "cancelled"
    assert state["result"] is None
    assert subject.fleet.engine_addon_installing is False
    if boundary == "constraints":
        assert spawned == []  # Cancellation before installation must not start a package-mutating command.
