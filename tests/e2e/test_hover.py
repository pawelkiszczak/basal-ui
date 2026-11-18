"""Pointer hover regression using synthetic controls and isolated UI settings."""

import pytest

sync_api = pytest.importorskip("playwright.sync_api")


def test_hover_and_touch(ui, tmp_path):
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            for touch in (False, True):
                context = browser.new_context(has_touch=touch, is_mobile=touch, viewport={"width": 1000, "height": 800})
                page = context.new_page()
                for path in ("/", "/viz/"):
                    page.goto(ui.url + path)
                    page.evaluate(
                        """viz => {
                      document.body.innerHTML = viz
                        ? '<button class="primary">Run</button><ul class="hist"><li>Synthetic history</li></ul><div class="tabs"><button class="on">Selected</button></div>'
                        : '<nav class="side-nav"><button>Models</button></nav><button class="load-media-model">Load media</button><div class="tabs"><button class="active">Selected</button></div><div class="quick-action-list"><button>Quick action</button></div>';
                    }""",
                        path == "/viz/",
                    )
                    controls = (
                        ["button.primary", ".hist li"]
                        if path == "/viz/"
                        else [".side-nav button", ".load-media-model", ".quick-action-list button"]
                    )
                    assert page.evaluate("matchMedia('(hover: hover)').matches") is not touch
                    for selector in controls:
                        item = page.locator(selector)
                        before = item.evaluate("e => [getComputedStyle(e).backgroundColor, getComputedStyle(e).filter]")
                        if touch:
                            item.tap()
                            page.wait_for_timeout(500)
                        else:
                            item.hover()
                        after = item.evaluate("e => [getComputedStyle(e).backgroundColor, getComputedStyle(e).filter]")
                        assert (before == after) is touch, (path, selector, before, after)
                        item.screenshot(
                            path=str(
                                tmp_path
                                / f"{'touch' if touch else 'mouse'}-{path.strip('/') or 'app'}-{controls.index(selector)}.png"
                            )
                        )
                        if not touch:
                            page.mouse.move(900, 700)
                    selected = page.locator(".tabs button")
                    assert selected.evaluate("e => getComputedStyle(e).backgroundColor") != "rgba(0, 0, 0, 0)"
                    for _ in range(6):
                        page.keyboard.press("Tab")
                        if page.locator(":focus-visible").count():
                            break
                    focused = page.locator(":focus-visible")
                    assert focused.count() == 1
                    assert focused.evaluate("e => getComputedStyle(e).outlineStyle") != "none"
                    page.screenshot(
                        path=str(
                            tmp_path / f"{'touch' if touch else 'mouse'}-{'viz' if path == '/viz/' else 'app'}.png"
                        )
                    )
                context.close()
        finally:
            browser.close()
