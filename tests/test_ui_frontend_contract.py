"""Contracts between the browser app's files and the server: the script load order of index.html, the path problem
codes basal_ui/paths.py returns against the messages js/path-check.js shows, and the message codes the UI maps on."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from basal_ui.hardware import Hardware
from tests.test_ui_app import app_for

ROOT = Path(__file__).parents[1]
STATIC = ROOT / "basal_ui/static"


@pytest.fixture(autouse=True)
def isolated_config(monkeypatch, tmp_path):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))


def scripts():
    return re.findall(r'<script src="/static/([^"]+)" defer></script>', (STATIC / "index.html").read_text())


def test_every_script_loads_once_core_first_and_boot_last():
    """Classic deferred scripts share one global scope and run in document order: a file's top-level code may use only
    what an earlier file defines, and boot.js runs the first render once every listener is registered."""
    order = scripts()
    assert len(order) == len(set(order))
    on_disk = {f"js/{f.name}" for f in (STATIC / "js").glob("*.js")} | {"i18n.js", "elements.js", "backends.js"}
    assert set(order) == on_disk  # no script left out of the page, none loaded that is not shipped
    assert order[:3] == ["i18n.js", "elements.js", "js/core.js"]
    assert order[-1] == "js/boot.js"
    # option-rows.js defines renderOptionRows before load-options.js builds its dialog with it
    assert order.index("js/option-rows.js") < order.index("js/load-options.js")
    # path-check.js attaches to the fields other views build: at DOMContentLoaded, after every deferred script ran
    assert "addEventListener('DOMContentLoaded'" in (STATIC / "js/path-check.js").read_text()


def path_problem_codes():
    """Every problem code basal_ui/paths.py assigns or returns (string literals in `code = …` / `return "…"` lines)."""
    codes = set()
    for line in (ROOT / "basal_ui/paths.py").read_text().splitlines():
        code = line.split("#")[0]
        if re.search(r'\bcode\b\s*=|^\s*return\s+"', code):
            code = re.sub(r'kind in \([^)]*\)|kind == "\w+"', "", code)  # the kinds a condition names are not codes
            codes |= set(re.findall(r'"([a-z]+(?:_[a-z]+)*)"', code))
    return codes


def test_every_path_problem_code_has_a_message_in_the_ui():
    codes = path_problem_codes()
    assert {"missing", "not_json", "not_writable", "invalid"} <= codes  # the scan sees the codes
    block = re.search(r"const PATH_PROBLEMS=\{(.*?)\};", (STATIC / "js/path-check.js").read_text(), re.DOTALL).group(1)
    shown = dict(re.findall(r"(\w+):t\('((?:\\.|[^'])*)'\)", block))
    assert set(shown) == codes  # literal t() values: tests/test_ui_i18n.py checks their Polish translations


def test_memory_errors_carry_a_message_code_and_the_field(tmp_path):
    app, _ = app_for(tmp_path, [], hw=Hardware("cpu", "cpu", 16 * 1024**3, None, None, frozenset(), 5))
    with TestClient(app, base_url="http://127.0.0.1") as client:
        budget = client.put("/api/memory", json={"budget_bytes": 1024})
        assert budget.status_code == 400
        assert budget.json()["code"] == "budget_invalid" and budget.json()["field"] == "budget_bytes"
        assert "budget_bytes" in budget.json()["detail"]  # the English text stays for other clients
        reserve = client.put("/api/memory", json={"reserve_bytes": -1}).json()
        assert (reserve["code"], reserve["field"]) == ("reserve_invalid", "reserve_bytes")
        unknown = client.put("/api/memory", json={"swap": 1}).json()
        assert (unknown["code"], unknown["field"]) == ("unknown_setting", None)
    shell = (STATIC / "js/shell.js").read_text()
    assert "budget_invalid:" in shell and "reserve_invalid:" in shell and "e.message.includes(" not in shell


def test_toasts_shortcuts_and_storage_follow_the_shared_helpers():
    core = (STATIC / "js/core.js").read_text()
    # an error equal to one still shown is counted, not stacked; the stack is capped; a removed toast hands focus on
    assert "toastIdentity" in core and "TOAST_ERRORS" in core and "function removeToast(" in core
    assert "onclick=()=>removeToast(node)" in core
    # a modal dialog owns the keyboard: the global shortcuts return before any of their branches
    keydown = core[core.index("document.addEventListener('keydown',e=>{") :]
    assert keydown.split("\n")[1].strip() == "if(document.querySelector('dialog[open]'))return;"
    # the status poll's error toast leaves once the server answers again
    assert "dismissToast('status')" in (STATIC / "js/status.js").read_text()
    # guarded storage: only core.js's stored()/store() touch localStorage in these files
    for name in ("core", "status", "shell", "settings", "manager", "path-check", "boot"):
        source = (STATIC / f"js/{name}.js").read_text()
        if name == "core":
            source = re.sub(r"const stored=.*\nconst store=.*\n", "", source)
        assert "localStorage" not in source, name
