"""axe-core over every view of basal-ui, light and dark, at 1440 and 390 px: no serious or critical violations.

The page is served by scripts/ui_fixture_server.py (fake hardware, catalog and basal-serve), so no model is needed and
this module runs without BASAL_E2E=1: uv run --no-sync pytest tests/e2e/test_a11y.py. axe-core 4.10.2 (MPL-2.0) is
vendored in tests/e2e/assets/; it is injected into the page, never served by basal-ui."""

import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from tests.e2e.support import ROOT, free_port

sync_api = pytest.importorskip("playwright.sync_api", reason="install basal[e2e] and run: playwright install chromium")

REAL_MODELS = False  # tests/e2e/conftest.py: not gated by BASAL_E2E
AXE = (Path(__file__).parent / "assets" / "axe.min.js").read_text()
VIEWS = (
    "overview",
    "models",
    "manager",
    "backends",
    "playground",
    "batch",
    "inspect",
    "api",
    "history",
    "logs",
    "settings",
)
RUN_AXE = """async () => (await axe.run(document, {runOnly: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa',
  'best-practice']})).violations.filter(v => ['serious', 'critical'].includes(v.impact))
  .map(v => `${v.id} (${v.impact}): ${v.nodes.slice(0, 3).map(n => n.target.join(' ')).join(' | ')}`)"""


@pytest.fixture(scope="module")
def fixture_url():
    port = free_port()
    server = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "ui_fixture_server.py"), "--port", str(port), "--preload"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 60
        while True:
            try:
                if httpx.get(url + "/api/status", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if server.poll() is not None or time.monotonic() > deadline:
                pytest.fail(f"scripts/ui_fixture_server.py did not start (exit {server.poll()})")
            time.sleep(0.25)
        yield url
    finally:
        server.terminate()
        server.wait(timeout=10)


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            instance = p.chromium.launch()
        except sync_api.Error as error:
            pytest.skip(f"Chromium is not installed (uv run --no-sync playwright install chromium): {error}")
        yield instance
        instance.close()


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_every_view_has_no_serious_or_critical_axe_violations(browser, fixture_url, theme, width):
    context = browser.new_context(viewport={"width": width, "height": 900})
    context.add_init_script(f"localStorage.setItem('basal-ui.theme', '{theme}')")
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(fixture_url + "/#overview")
    page.wait_for_function("() => typeof statusCache !== 'undefined' && statusCache && modelData.length > 0")
    page.add_script_tag(content=AXE)
    found = {}
    for view in VIEWS:
        page.evaluate("view => showView(view, true)", view)
        page.wait_for_timeout(400)  # the view's first fetches render
        assert page.title().endswith(" · basal")
        if view != "overview":  # the view shown first: navigating to it again leaves the focus where it is
            assert page.evaluate("() => document.activeElement.id") == "pageTitle"
        if violations := page.evaluate(RUN_AXE):
            found[view] = violations
    context.close()
    assert not errors, errors
    assert not found, found
