"""Browser regression for overlapping Load options requests, without loading a model."""

import socket
import threading
import time

import pytest
import uvicorn

from basal_ui import catalog, settings
from basal_ui.app import create_app

sync_api = pytest.importorskip("playwright.sync_api")


@pytest.fixture
def options_server(tmp_path, monkeypatch):
    monkeypatch.setenv("BASAL_UI_CONFIG", str(tmp_path / "settings.json"))
    settings.set_check_updates(False)
    settings.set_auto_load(False)
    variants = [
        catalog.Variant(name, "Remek/basal-2.0-mini", "basal-2.0", "mini", "bf16", "bf16", None, 3190000000, False)
        for name in ("mini", "other")
    ]
    app = create_app(catalog_fn=lambda **_: (variants, "test"))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started
        yield f"http://127.0.0.1:{sock.getsockname()[1]}"
    finally:
        server.should_exit = True
        thread.join(20)
        sock.close()
        assert not thread.is_alive()


@pytest.fixture
def options_page(options_server):
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1280, "height": 900}, service_workers="block")
        context.route_web_socket("**/*", lambda ws: ws.close())
        context.route(
            "**/*",
            lambda route: route.continue_() if route.request.url.startswith(options_server + "/") else route.abort(),
        )
        page = context.new_page()
        try:
            page.goto(options_server + "/#models")
            page.wait_for_function("() => typeof catalogData !== 'undefined' && catalogData")
            page.click("#allModelsTab")
            page.locator('#modelsTable .options-model[data-id="other"][data-action="save"]').first.wait_for()
            yield page
        finally:
            context.close()
            browser.close()


def hold_first(page, pattern):
    held = []

    def hold(route):
        if not held:
            held.append((route, route.fetch()))
        else:
            route.continue_()

    page.route(pattern, hold)
    page.expose_function("heldReady", lambda: bool(held))
    return held


def wait_held(page):
    page.wait_for_function("() => window.heldReady()", timeout=10000)


@pytest.mark.parametrize("delayed", ["options", "presets"])
def test_latest_load_options_survives_out_of_order_responses(options_page, delayed):
    page = options_page
    pattern = "**/api/models/options?id=mini" if delayed == "options" else "**/api/presets"
    held = hold_first(page, pattern)
    page.evaluate("() => { window.firstOptions = openLoadOptions('mini', false, 'load'); }")
    wait_held(page)
    page.locator('#modelsTable .options-model[data-id="other"][data-action="save"]').first.click()
    sync_api.expect(page.locator("#loadOptions")).to_be_visible()
    sync_api.expect(page.locator("#optionsModel")).to_contain_text("other")
    sync_api.expect(page.locator("#optionsApply")).to_have_text("Save")
    page.fill("#opt-revision", "newer")
    held[0][0].fulfill(response=held[0][1])
    page.evaluate("() => window.firstOptions")
    sync_api.expect(page.locator("#optionsModel")).to_contain_text("other")
    sync_api.expect(page.locator("#optionsApply")).to_have_text("Save")
    assert page.input_value("#opt-revision") == "newer"
    with page.expect_request(
        lambda request: request.url.endswith("/api/models/options") and request.method == "PUT"
    ) as saved:
        page.click("#optionsApply")
    assert saved.value.post_data_json["id"] == "other"
    sync_api.expect(page.locator("#loadOptions")).to_be_hidden()


def test_closing_dialog_invalidates_pending_options(options_page):
    page = options_page
    page.locator('#modelsTable .options-model[data-id="other"][data-action="save"]').first.click()
    sync_api.expect(page.locator("#loadOptions")).to_be_visible()
    held = hold_first(page, "**/api/models/options?id=mini")
    page.evaluate("() => { window.firstOptions = openLoadOptions('mini', false, 'load'); }")
    wait_held(page)
    page.evaluate("""() => { window.dialogClosed = new Promise(resolve =>
        document.querySelector('#loadOptions').addEventListener('close', resolve, {once: true})); }""")
    page.click("#optionsCancel")
    page.evaluate("() => window.dialogClosed")
    held[0][0].fulfill(response=held[0][1])
    page.evaluate("() => window.firstOptions")
    sync_api.expect(page.locator("#loadOptions")).to_be_hidden()


def test_stale_options_error_is_suppressed_but_current_error_is_shown(options_page):
    page = options_page
    held = hold_first(page, "**/api/models/options?id=mini")
    page.evaluate("() => { window.firstOptions = act(() => openLoadOptions('mini', false, 'load')); }")
    wait_held(page)
    page.locator('#modelsTable .options-model[data-id="other"][data-action="save"]').first.click()
    sync_api.expect(page.locator("#loadOptions")).to_be_visible()
    held[0][0].fulfill(status=400, json={"detail": "stale options failure"})
    page.evaluate("() => window.firstOptions")
    sync_api.expect(page.locator("#optionsModel")).to_contain_text("other")
    assert page.get_by_text("stale options failure", exact=False).count() == 0
    page.click("#optionsCancel")
    page.route(
        "**/api/models/options?id=mini",
        lambda route: route.fulfill(status=400, json={"detail": "current options failure"}),
    )
    page.locator('#modelsTable .options-model[data-id="mini"][data-action="save"]').first.click()
    sync_api.expect(page.locator("#toastStack .error")).to_contain_text("current options failure")
