"""Card surfaces and navigation state styles in Chromium."""

import pytest

sync_api = pytest.importorskip("playwright.sync_api")


def test_card_surfaces_and_navigation(ui):
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        page = context.new_page()
        page.goto(ui.url + "/")
        page.wait_for_function("() => typeof statusCache !== 'undefined' && statusCache && modelData.length > 0")
        page.evaluate("""() => {
          setRequest({state: 'Example', questions: {choice: {type: 'choice', instructions: 'Choose',
            criteria: {a: 'A', b: 'B'}}}});
        }""")
        style = "el => { const s = getComputedStyle(el); return {radius: s.borderRadius, shadow: s.boxShadow, border: s.borderTopWidth, duration: s.transitionDuration, property: s.transitionProperty, outline: s.outlineStyle, background: s.backgroundColor}; }"
        for theme in ("light", "dark"):
            page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
            page.locator('[data-view="models"]').click()
            page.wait_for_selector(".suggestion-card")
            for selector, radius in (("#models .suggestion-card", "10px"), ("#models .card", "16px")):
                surface = page.locator(selector).first.evaluate(style)
                assert surface["radius"] == radius and surface["shadow"] == "none" and surface["border"] != "0px"
            nav = page.locator('[data-view="dashboard"]')
            page.wait_for_timeout(250)
            normal = nav.evaluate(style)
            nav.hover()
            page.wait_for_timeout(250)
            hovered = nav.evaluate(style)
            assert normal["background"] != hovered["background"]
            assert normal["duration"] == "0.15s" and normal["property"] == "all"
            page.locator('[data-view="models"]').focus()
            page.keyboard.press("Shift+Tab")
            assert nav.evaluate('el => el.matches(":focus-visible")')
            assert nav.evaluate(style)["outline"] != "none"
            nav.click()
            page.wait_for_timeout(250)
            assert "active" in nav.get_attribute("class")
            assert nav.evaluate(style)["background"] != normal["background"]
            page.locator('[data-view="playground"]').click()
            question = page.locator(".question").first.evaluate(style)
            assert question["radius"] == "9px" and question["shadow"] == "none" and question["border"] == "1px"
        page.emulate_media(reduced_motion="reduce")
        assert page.locator('[data-view="dashboard"]').evaluate(style)["duration"] == "1e-05s"
        context.close()
        browser.close()
