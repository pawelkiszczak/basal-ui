"""axe-core over every view of basal-ui, light and dark, at 1440 and 390 px: no serious or critical violations.

The page is served by scripts/ui_fixture_server.py (fake hardware, catalog and basal-serve), so no model is needed and
this module runs without BASAL_E2E=1: uv run --no-sync pytest tests/e2e/test_a11y.py. axe-core 4.10.2 (MPL-2.0) is
vendored in tests/e2e/assets/; it is injected into the page, never served by basal-ui."""

import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

from tests.e2e.support import ROOT, free_port

sync_api = pytest.importorskip("playwright.sync_api", reason="install basal[e2e] and run: playwright install chromium")

pytestmark = pytest.mark.fixture_server  # tests/e2e/conftest.py: no real model, so not gated by BASAL_E2E
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
# counts the page's fetches, so a view can be checked once its first requests have answered and rendered
TRACK_FETCHES = """(() => {
  const net = window.__a11yNet = {pending: 0, last: performance.now()}, fetch = window.fetch;
  window.fetch = function (...args) {
    net.pending++; net.last = performance.now();
    return fetch.apply(this, args).finally(() => { net.pending--; net.last = performance.now(); });
  };
})()"""
# the view is settled: no request in flight for 250 ms (its first fetches answered and rendered) and no visible
# "Loading…" placeholder or aria-busy region left in it
VIEW_SETTLED = """view => {
  const net = window.__a11yNet, root = document.getElementById(`view-${view}`);
  if (!net || !root || root.hidden || net.pending > 0 || performance.now() - net.last < 250) return false;
  const visible = e => e.checkVisibility ? e.checkVisibility() : e.offsetParent !== null;
  const busy = [...root.querySelectorAll('[aria-busy="true"]')].some(visible);
  const loading = [...root.querySelectorAll('*')].some(e => !e.children.length && visible(e)
    && /^(Loading|Ładowanie)/.test(e.textContent.trim()));
  return !busy && !loading;
}"""


@pytest.fixture(scope="module")
def fixture_url(tmp_path_factory):
    port = free_port()
    log = tmp_path_factory.mktemp("fixture") / "server.log"
    with open(log, "w") as out:
        server = subprocess.Popen(
            [sys.executable, str(ROOT / "scripts" / "ui_fixture_server.py"), "--port", str(port), "--preload"],
            cwd=ROOT,
            stdout=out,
            stderr=subprocess.STDOUT,
        )
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 60
        while True:  # up, and mini loaded (--preload): the views render a loaded model
            try:
                models = httpx.get(url + "/api/status", timeout=1).json()["models"]
                if any(m["state"] == "ready" for m in models):
                    break
            except (httpx.HTTPError, ValueError, KeyError):
                pass
            if server.poll() is not None or time.monotonic() > deadline:
                pytest.fail(f"scripts/ui_fixture_server.py did not start (exit {server.poll()}):\n{log.read_text()}")
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
            if os.environ.get("BASAL_E2E") == "1":  # the gate (and CI's a11y job) must not pass by skipping
                raise
            pytest.skip(f"Chromium is not installed (uv run --no-sync playwright install chromium): {error}")
        yield instance
        instance.close()


@pytest.mark.parametrize("width", [1440, 390])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_every_view_has_no_serious_or_critical_axe_violations(browser, fixture_url, theme, width):
    context = browser.new_context(viewport={"width": width, "height": 900})
    context.add_init_script(f"localStorage.setItem('basal-ui.theme', '{theme}')")
    context.add_init_script(TRACK_FETCHES)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(fixture_url + "/#overview")
    page.wait_for_function("() => typeof statusCache !== 'undefined' && statusCache && modelData.length > 0")
    page.add_script_tag(content=AXE)
    found = {}
    for view in VIEWS:
        page.evaluate("view => showView(view, true)", view)
        page.wait_for_function(VIEW_SETTLED, arg=view, timeout=15_000)
        assert page.title().endswith(" · basal")
        if view != "overview":  # the view shown first: navigating to it again leaves the focus where it is
            assert page.evaluate("() => document.activeElement.id") == "pageTitle"
        if violations := page.evaluate(RUN_AXE):
            found[view] = violations
    context.close()
    assert not errors, errors
    assert not found, found
