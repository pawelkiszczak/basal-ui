"""Synthetic Chromium regression checks for Models catalog filters."""

import signal
import subprocess
from pathlib import Path

import pytest

from tests.e2e.support import UI

sync_api = pytest.importorskip("playwright.sync_api", reason="install basal[e2e] and run: playwright install chromium")


@pytest.fixture
def synthetic_ui(tmp_path: Path):
    app = UI(tmp_path).start()
    yield app
    app.proc.send_signal(signal.SIGTERM)
    try:
        app.proc.wait(10)
    except subprocess.TimeoutExpired:
        app.proc.kill()
        app.proc.wait()


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        yield browser
        browser.close()


def test_catalog_filters_keep_compatibility_and_status_distinct(browser, synthetic_ui: UI, request):
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    request.addfinalizer(context.close)
    page = context.new_page()
    page.set_default_timeout(5_000)

    models = [
        {
            "id": "missing-gguf",
            "repo": "Synthetic/gguf",
            "family": "basal-2.0",
            "tier": "mini",
            "port": "GGUF",
            "label": "GGUF Q4",
            "media": [],
            "weights_bytes": 100,
            "est_memory_bytes": 100,
            "fit": "fits",
            "fit_now": "fits",
            "downloaded": True,
            "downloaded_bytes": 100,
            "runnable": False,
            "model_compatible": True,
        },
        {
            "id": "other-incompatible",
            "repo": "Synthetic/other",
            "family": "basal-2.0",
            "tier": "small",
            "port": "GGUF",
            "label": "GGUF Q8",
            "media": [],
            "weights_bytes": 100,
            "est_memory_bytes": 100,
            "fit": "fits",
            "fit_now": "fits",
            "downloaded": False,
            "runnable": False,
            "model_compatible": False,
        },
        {
            "id": "available",
            "repo": "Synthetic/available",
            "family": "basal-2.0",
            "tier": "4.5B",
            "port": "bf16",
            "label": "bf16",
            "media": [],
            "weights_bytes": 100,
            "est_memory_bytes": 100,
            "fit": "fits",
            "fit_now": "fits",
            "downloaded": True,
            "downloaded_bytes": 100,
            "runnable": True,
            "model_compatible": True,
        },
    ]
    catalog = {
        "recommendation_backend": "automatic",
        "recommendation_backends": [{"id": "automatic", "name": "Automatic"}],
        "recommendation_note": "",
        "suggestions": [],
        "models": models,
    }
    status = synthetic_ui.get("/api/status")
    status.update(models=[{"id": "missing-gguf", "state": "starting"}], downloads={"Synthetic/gguf": {}})
    page.route("**/api/models*", lambda route: route.fulfill(json=catalog))
    page.route("**/api/status", lambda route: route.fulfill(json=status))
    page.goto(synthetic_ui.url + "/")
    page.wait_for_function("() => statusAvailable && modelData.length === 3")
    page.evaluate("showView('models', true); selectModelsTab('all')")

    ids = lambda: page.locator("#modelsTable .model-card-open").evaluate_all(
        "nodes => nodes.map(node => node.dataset.id)"
    )
    assert ids() == ["available"]  # default visibility still hides models that cannot run now
    page.select_option("#modelRunAvailabilityFilter", "unavailable")
    page.select_option("#modelCompatibilityFilter", "compatible")
    assert ids() == ["missing-gguf"]  # compatible format despite the absent llama.cpp package
    page.select_option("#modelLoadFilter", "loaded")
    assert ids() == ["missing-gguf"]  # starting entries count as loaded; the filter note explains this
    page.select_option("#modelDownloadFilter", "downloading")
    assert ids() == ["missing-gguf"]

    page.route("**/api/status", lambda route: route.abort())
    page.evaluate("refreshStatus()")
    page.wait_for_function("() => !statusAvailable")
    page.select_option("#modelLoadFilter", "loaded")
    assert ids() == []  # failed polling makes the retained status snapshot unknown, not current
    page.select_option("#modelDownloadFilter", "downloading")
    assert ids() == []
    page.select_option("#modelDownloadFilter", "not-downloaded")
    assert ids() == []
    page.select_option("#modelLoadFilter", "")
    page.select_option("#modelDownloadFilter", "")
    assert ids() == ["missing-gguf"]
    assert page.locator('#modelsTable .delete-model[data-id="missing-gguf"]').count() == 0

    page.evaluate(
        """() => { backendModelFilter = {name: 'Synthetic backend', ids: new Set(['missing-gguf'])};
          document.querySelector('#backendModelFilterNotice').hidden = false; }"""
    )
    page.locator("#modelSearch").fill("no-such-model")
    page.locator("#modelsTable [data-clear-model-filters]").click()
    assert page.evaluate("backendModelFilter") is None
    assert page.locator("#backendModelFilterNotice").is_hidden()
    assert ids() == ["available"]  # reset restores the default run-availability filter
    page.set_viewport_size({"width": 320, "height": 900})
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
