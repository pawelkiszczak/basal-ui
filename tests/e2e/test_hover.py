"""Hover and touch behavior on mounted controls with synthetic media and History."""

import base64

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/aZkAAAAASUVORK5CYII=")
HISTORY = [
    {
        "t": 0,
        "req": {
            "state": "Synthetic state",
            "questions": {"q": {"type": "choice", "criteria": {"a": "Alpha", "b": "Beta"}}},
        },
        "gold": None,
        "q": "Synthetic question",
        "a": "Alpha",
        "conf": 0.8,
    }
]


def appearance(locator):
    return locator.evaluate("""async e => {
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        const style = getComputedStyle(e);
        return [style.backgroundColor, style.filter];
    }""")


def check_pointer(page, control, touch, path):
    page.mouse.move(0, 0)
    before = appearance(control)
    if touch:
        control.evaluate(
            "e => e.addEventListener('click', event => { event.preventDefault(); event.stopImmediatePropagation(); }, {capture:true, once:true})"
        )
        control.tap()
        page.wait_for_timeout(500)
    else:
        control.hover()
    after = appearance(control)
    assert (before == after) is touch, (before, after)
    control.screenshot(path=str(path))


def test_mounted_hover_and_touch(ui, tmp_path):
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            for touch in (False, True):
                context = browser.new_context(
                    has_touch=touch,
                    is_mobile=touch,
                    reduced_motion="reduce",
                    viewport={"width": 390 if touch else 1440, "height": 1000},
                )
                page = context.new_page()
                assert page.evaluate("matchMedia('(hover: hover)').matches") is not touch
                page.goto(ui.url + "/")
                page.wait_for_function("() => typeof statusCache !== 'undefined' && statusCache")
                page.evaluate("document.documentElement.dataset.theme = 'dark'")
                page.locator("#toggleQuickActions").click()
                if page.locator("#quickActionsRail").is_hidden():
                    page.locator("#toggleQuickActions").click()
                search = page.locator("#quickActionSearch")
                search.fill("Open Playground")
                page.keyboard.press("ArrowDown")
                quick = page.locator('[data-quick-action="playground"]')
                assert quick.evaluate("e => e.matches(':focus-visible')")
                page.keyboard.press("Enter")
                assert page.locator("#playground").is_visible()
                page.locator('.side-nav button[data-view="dashboard"]').click()
                sync_api.expect(page.locator("#dashboard")).to_be_visible()
                nav = page.locator('.side-nav button[data-view="playground"]')
                check_pointer(page, nav, touch, tmp_path / f"nav-{touch}.png")
                nav.tap() if touch else nav.click()
                assert "active" in (nav.get_attribute("class") or "")
                page.mouse.move(0, 0)
                assert appearance(nav)[0] != "rgba(0, 0, 0, 0)"
                nav.screenshot(path=str(tmp_path / f"selected-{touch}.png"))
                assert page.locator("#playground").is_visible()

                # A synthetic media-capable model enables the real file-selection path without loading weights.
                page.evaluate("""() => { clearTimeout(pollTimer); loadedMedia = ['image'];
                    document.querySelector('#playgroundForm').disabled = false;
                    document.querySelector('#mediaField').hidden = false;
                    document.querySelector('#mediaInput').disabled = false; }""")
                page.locator("#mediaInput").set_input_files(
                    {"name": "synthetic.png", "mimeType": "image/png", "buffer": PNG}
                )
                remove = page.get_by_role("button", name="Remove synthetic.png")
                sync_api.expect(remove).to_be_visible()
                check_pointer(page, remove, touch, tmp_path / f"media-{touch}.png")
                remove.tap() if touch else remove.click()
                sync_api.expect(remove).to_have_count(0)

                page.goto(ui.url + "/viz/")
                page.wait_for_function("() => booted")
                page.evaluate(
                    "history => { localStorage.setItem('basal-viz-history', JSON.stringify(history)); renderHistory(); }",
                    HISTORY,
                )
                row = page.locator("#hist button, #hist li").last
                sync_api.expect(row).to_contain_text("Synthetic question")
                # Keep the explanation pending to inspect the real disabled state after replay.
                page.evaluate(
                    "() => { const original = window.fetch; window.fetch = (url, ...args) => String(url).includes('api/explain') ? new Promise(() => {}) : original(url, ...args); }"
                )
                check_pointer(page, row, touch, tmp_path / f"history-{touch}.png")
                if not touch and row.evaluate("e => e.tagName === 'BUTTON'"):
                    page.locator("#clearh").focus()
                    page.keyboard.press("Tab")
                    assert row.evaluate("e => e.matches(':focus-visible')")
                    page.keyboard.press("Enter")
                else:
                    row.tap() if touch else row.click()
                sync_api.expect(page.locator("#state")).to_have_value("Synthetic state")
                go = page.locator("#go")
                sync_api.expect(go).to_be_disabled()
                assert float(go.evaluate("e => getComputedStyle(e).opacity")) == 0.55
                go.screenshot(path=str(tmp_path / f"disabled-{touch}.png"))
                clear = page.locator("#clearh")
                page.locator("#addq").focus()
                page.keyboard.press("Tab")
                assert clear.evaluate("e => e.matches(':focus-visible') && getComputedStyle(e).outlineStyle !== 'none'")
                clear.screenshot(path=str(tmp_path / f"focus-{touch}.png"))
                page.keyboard.press("Enter")
                sync_api.expect(page.locator("#hist")).to_contain_text("Decisions you make appear here")
                context.close()
        finally:
            browser.close()
