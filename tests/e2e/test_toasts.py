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
        page.clock.fast_forward(3500)
        assert info.count() == 1
        page.clock.fast_forward(501)
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


def test_failed_load_toast_is_visible(ui):
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=2)
        page = context.new_page()
        page.goto(ui.url)
        page.wait_for_function("() => statusCache && (catalogData || modelCatalogError)")
        page.evaluate("""() => {
            clearTimeout(pollTimer);
            modelData = [{id: 'synthetic/model', family: 'Synthetic', tier: 'Test', label: 'Test',
                downloaded: true, runnable: true, media_mode: false, media: [], downloaded_bytes: 0}];
            renderDiskModels();
            showView('manager');
        }""")
        page.route("**/api/load", lambda route: route.fulfill(status=500, json={"detail": "Synthetic load failure"}))
        with page.expect_request("**/api/load"):
            page.locator('#diskModels .load-model[data-id="synthetic/model"]').click()
        error = page.locator(".toast.error")
        sync_api.expect(error).to_have_count(1)
        sync_api.expect(error).to_contain_text("Synthetic load failure")
        button = error.get_by_role("button", name="Dismiss")

        for width, height in [(1440, 900), (390, 844)]:
            page.set_viewport_size({"width": width, "height": height})
            for scroll in (0, 500):
                page.evaluate("y => window.scrollTo({top: y, behavior: 'instant'})", scroll)
                page.wait_for_timeout(500)
                assert page.evaluate("window.scrollY > 0") == (scroll > 0)
                for element in (error, button):
                    bounds = element.bounding_box()
                    assert bounds and bounds["x"] >= 0 and bounds["y"] >= 0
                    assert bounds["x"] + bounds["width"] <= width
                    assert bounds["y"] + bounds["height"] <= height
                    hits = element.evaluate("""el => {
                        const r = el.getBoundingClientRect();
                        return [[r.left+8,r.top+8],[r.right-8,r.bottom-8]].map(([x,y]) => {
                            const hit = document.elementFromPoint(x,y);
                            return {ok: el === hit || el.contains(hit), hit: hit?.outerHTML.slice(0,120), x, y};
                        });
                    }""")
                    assert all(hit["ok"] for hit in hits), (width, scroll, bounds, hits)
                if os.environ.get("BASAL_TOAST_PROOF_DIR"):
                    page.screenshot(path=f"{os.environ['BASAL_TOAST_PROOF_DIR']}/toast-{width}-{scroll}.png")
                if width == 1440 and scroll == 0 and os.environ.get("BASAL_TOAST_SCREENSHOT"):
                    error.screenshot(path=os.environ["BASAL_TOAST_SCREENSHOT"])
        button.focus()
        page.keyboard.press("Enter")
        assert error.count() == 0
        context.close()
        browser.close()
