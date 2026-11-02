"""basal-ui runs on a supported engine release; anything else is refused with the install command, unless the user
opts in to an experimental engine."""

import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import unquote

import pytest
from packaging.version import Version

from basal_ui import engine

ROOT = Path(__file__).parents[1]


def fake_engine(monkeypatch, version, commit=None):
    monkeypatch.setattr(engine, "installed", lambda: (version, commit))


def test_a_supported_release_runs_as_is(monkeypatch):
    fake_engine(monkeypatch, engine.PINNED_RELEASE)
    monkeypatch.delenv(engine.UNCHECKED_ENV, raising=False)
    assert engine.check() == {
        "version": engine.PINNED_RELEASE,
        "commit": None,
        "supported": engine.SUPPORTED_RANGE,
        "experimental": False,
    }


@pytest.mark.parametrize("version", ["2.6.0", "2.4.0", "not-a-version"])
def test_an_engine_outside_the_range_is_refused_unless_unchecked(monkeypatch, version):
    fake_engine(monkeypatch, version)
    monkeypatch.delenv(engine.UNCHECKED_ENV, raising=False)
    with pytest.raises(SystemExit, match=f"found {version}.*BASAL_UI_ENGINE_UNCHECKED=1"):
        engine.check()
    monkeypatch.setenv(engine.UNCHECKED_ENV, "1")
    assert engine.check()["experimental"] is True


def test_a_branch_install_in_the_range_is_experimental(monkeypatch):
    fake_engine(monkeypatch, engine.PINNED_RELEASE, "0123456789abcdef")
    assert engine.check() == {
        "version": engine.PINNED_RELEASE,
        "commit": "0123456789abcdef",
        "supported": engine.SUPPORTED_RANGE,
        "experimental": True,
    }


def test_a_missing_engine_or_the_unrelated_pypi_basal_names_the_install(monkeypatch):
    real = importlib.util.find_spec
    monkeypatch.setattr(engine.importlib.util, "find_spec", lambda name: None if name == "basal.server" else real(name))
    with pytest.raises(SystemExit, match=re.escape(engine.ENGINE_URL)):
        engine.check()


def test_the_command_refuses_before_importing_the_engine(tmp_path):
    """`basal-ui` (basal_ui.__main__) checks first: an unsupported engine never gets as far as basal_ui.app."""
    fake = tmp_path / "basal"
    (fake / "server.py").parent.mkdir()
    (fake / "__init__.py").write_text('__version__ = "9.0.0"\n')
    (fake / "server.py").write_text("raise RuntimeError('basal_ui.app imported the engine')\n")
    result = subprocess.run(
        [sys.executable, "-m", "basal_ui"],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": f"{tmp_path}:{engine.__file__.rsplit('/basal_ui/', 1)[0]}", "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 1
    assert f"basal-ui supports engine {engine.SUPPORTED_RANGE}, found 9.0.0" in result.stderr


def test_every_pin_names_the_tested_release():
    """basal_ui/engine.py holds the engine release; uv's source, the install lines and CI must name the same one
    (bumping it: CONTRIBUTING.md, "Bumping the engine")."""
    assert engine.PINNED_RELEASE in engine.SUPPORTED
    source = re.search(r'^basal = \{ url = "([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M).group(1)
    assert source == engine.ENGINE_URL, "pyproject.toml [tool.uv.sources] basal names another release"
    for doc in ("README.md", "docs/E2E.md", "CONTRIBUTING.md"):
        text = unquote((ROOT / doc).read_text())
        tags = set(re.findall(r"archive/refs/tags/v(\S+?)\.tar\.gz", text))
        assert tags <= {engine.PINNED_RELEASE}, f"{doc} installs engine {tags}"
        assert tags or doc == "CONTRIBUTING.md", f"{doc} lost its install line"
        ranges = set(re.findall(r">=\d[\d.]*,<\d[\d.]*", text))
        assert ranges <= {engine.SUPPORTED_RANGE}, f"{doc} names range {ranges}"
    ci = (ROOT / ".github/workflows/ci.yml").read_text()  # actions are pinned with `# vX.Y.Z` comments: not engines
    assert not re.search(r"rkinas/basal\S*?v\d+\.\d+\.\d+", ci), (
        "ci.yml names an engine tag; its matrix comes from TESTED_RELEASES"
    )


def test_every_tested_release_is_in_the_range():
    """CI runs every TESTED_RELEASES entry: each must start without BASAL_UI_ENGINE_UNCHECKED."""
    for version in engine.TESTED_RELEASES:
        assert engine.supported(version), version


NEW_IN_2_5 = ("adapter", "viz_gemma4", "mcp_models", "mlx_recurrent", "int8", "viz_mlx")


def test_feature_probes_match_the_installed_engine():
    """Each probe tells the tested releases apart: all on with 2.5.0, all off with 2.4.x (CI runs both)."""
    assert set(NEW_IN_2_5) == set(engine.FEATURES)
    found = {name: engine.feature(name) for name in engine.FEATURES}
    assert all(isinstance(value, bool) for value in found.values()), found
    version = Version(engine.installed()[0])
    expected = version >= Version("2.5.0")
    if version.release[:2] not in ((2, 4), (2, 5)):
        pytest.skip(f"engine {version} is not a tested release line")
    assert found == dict.fromkeys(NEW_IN_2_5, expected)


def test_a_probe_of_an_engine_module_that_cannot_be_imported_is_off(monkeypatch):
    real = engine.importlib.import_module

    def import_module(name):
        if name == "basal.mcp_server":
            raise ImportError("no mcp")
        return real(name)

    monkeypatch.setattr(engine.importlib, "import_module", import_module)
    assert engine.feature("mcp_models") is False
    assert engine._parameter("basal.engine", "NoSuchBackend.__init__", "compute") is False
