"""Toast behavior in Chromium without loading a model."""

import os

import pytest

sync_api = pytest.importorskip("playwright.sync_api")


def test_toasts(ui):
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.clock.install()
        page.goto(ui.url)
        page.wait_for_function("() => typeof statusCache !== 'undefined' && statusCache")
        page.evaluate("clearTimeout(pollTimer)")
        assert page.locator("#toastStack").get_attribute("aria-live") == "polite"

        page.evaluate("toast('Synthetic error', 'error')")
        error = page.locator(".toast.error")
        page.clock.fast_forward(5000)
        assert error.count() == 1
        bounds = error.bounding_box()
        assert bounds and bounds["y"] >= 0 and bounds["y"] + bounds["height"] <= 900
        if os.environ.get("BASAL_TOAST_SCREENSHOT"):
            page.screenshot(path=os.environ["BASAL_TOAST_SCREENSHOT"])
        error.get_by_role("button", name="Dismiss").focus()
        page.keyboard.press("Enter")
        assert error.count() == 0

        page.evaluate("toast('Synthetic information')")
        info = page.locator(".toast.success")
        page.clock.fast_forward(1000)
        info.hover()
        page.clock.fast_forward(5000)
        info.focus()
        page.mouse.move(0, 0)
        page.clock.fast_forward(5000)
        assert info.count() == 1
        info.hover()
        info.evaluate("element => element.blur()")
        page.clock.fast_forward(5000)
        assert info.count() == 1
        page.mouse.move(0, 0)
        page.clock.fast_forward(3001)
        assert info.count() == 0
        page.evaluate("toast('Unpaused information')")
        page.clock.fast_forward(3999)
        assert info.count() == 1
        page.clock.fast_forward(2)
        assert info.count() == 0

        page.route("**/api/status", lambda route: route.fulfill(status=503, json={"detail": "Synthetic failure"}))
        page.evaluate("refreshStatus()")
        sync_api.expect(error).to_have_count(1)
        page.evaluate("refreshStatus()")
        page.wait_for_timeout(100)
        assert error.count() == 1
        error.get_by_role("button", name="Dismiss").click()
        page.evaluate("refreshStatus()")
        page.wait_for_timeout(100)
        assert error.count() == 0
        page.unroute("**/api/status")
        page.evaluate("refreshStatus()")
        page.wait_for_function("() => !statusFailureNotified")
        page.route("**/api/status", lambda route: route.fulfill(status=503, json={"detail": "Synthetic failure"}))
        page.evaluate("refreshStatus()")
        sync_api.expect(error).to_have_count(1)

        context.close()
        context = browser.new_context()
        page = context.new_page()
        page.add_init_script("localStorage.setItem('basal-ui.lang', 'pl')")
        page.goto(ui.url)
        page.evaluate("toast('Błąd testowy', 'error')")
        assert page.get_by_role("button", name="Odrzuć").count() == 1
        context.close()
        browser.close()
