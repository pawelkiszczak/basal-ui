"""Responsive and accessible presentation checks for basal-ui's server-rendered pages."""

import pytest

sync_api = pytest.importorskip("playwright.sync_api", reason="install basal[e2e] and run: playwright install chromium")


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        instance = p.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def page(browser, ui, text_model):
    ui.load(text_model["id"])
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    tab = context.new_page()
    tab.set_default_timeout(120_000)
    errors = []
    tab.on("pageerror", lambda error: errors.append(str(error)))
    try:
        tab.goto(ui.url + "/")
        tab.wait_for_function("() => typeof statusCache !== 'undefined' && statusCache && modelData.length > 0")
        yield tab
    finally:
        context.close()
    assert not errors, errors


def navigate(page, name):
    destination = page.locator(f'.side-nav [data-view="{name}"]')
    if not destination.is_visible():
        disclosure = page.locator(".nav-tools")
        if not disclosure.evaluate("(el) => el.open"):
            disclosure.locator("summary").click()
    destination.click()
    assert not page.locator(".nav-tools").evaluate("(el) => el.open")


def test_all_views_fit_responsive_widths_and_question_controls_stay_named(page, tmp_path):
    views = (
        "dashboard",
        "overview",
        "models",
        "manager",
        "playground",
        "history",
        "backends",
        "batch",
        "inspect",
        "api",
        "logs",
    )
    page.wait_for_function(
        """() => document.querySelector('#trafficActivityCounts').textContent.trim() &&
          document.querySelector('#trafficModels .traffic-model')"""
    )
    page.screenshot(path=str(tmp_path / "basal-1440-dashboard.png"), full_page=True, animations="disabled")
    navigate(page, "playground")
    navigate(page, "dashboard")
    for width in (1440, 1280, 1024, 768, 390):
        page.set_viewport_size({"width": width, "height": 1000})
        for theme in ("light", "dark"):
            appearance = page.locator(".appearance-controls")
            choice = appearance.locator(f'[data-theme-choice="{theme}"]')
            if not choice.is_visible():
                appearance.locator("summary").click()
            choice.click()
            for name in views:
                navigate(page, name)
                overflow = page.evaluate("""() => ({document:document.documentElement.scrollWidth,
                  elements:[...document.querySelectorAll('body *')]
                    .filter(e => e.scrollWidth > e.clientWidth + 1).slice(0,8)
                    .map(e => ({tag:e.tagName,id:e.id,cls:typeof e.className==='string'?e.className:'',
                      right:Math.round(e.getBoundingClientRect().right),width:Math.round(e.getBoundingClientRect().width),
                      scroll:e.scrollWidth,client:e.clientWidth,text:e.textContent.trim().slice(0,40)}))})""")
                assert overflow["document"] <= width, (width, theme, name, overflow)
            navigate(page, "manager")
            for tab in ("library", "usage", "benchmarks", "calibration"):
                button = page.locator(f'[data-manager-tab="{tab}"]')
                button.click()
                sync_api.expect(button).to_have_attribute("aria-selected", "true")
                panel = page.locator(f"#manager-{tab}")
                sync_api.expect(panel).to_be_visible()
                if tab == "calibration":
                    sync_api.expect(page.locator("#calibrationFiles table")).to_be_visible()
                assert panel.evaluate("(el) => el.getBoundingClientRect().right") <= width
                assert page.evaluate("() => document.documentElement.scrollWidth") <= width

    page.set_viewport_size({"width": 390, "height": 844})
    header = page.locator("header.sidebar").bounding_box()
    assert header["height"] < 844 / 2, header
    navigate(page, "playground")
    page.evaluate("""() => {
      addQuestion('matrix', {type:'act', instructions:'', criteria:{approve:'good',reject:'bad'},
        costs:{approve:{approve:0,reject:5},reject:{approve:5,reject:0}}});
      addQuestion('tools', {type:'tool', instructions:'', tools:[], extra:['answer_directly']});
    }""")
    for width in (1440, 1280, 1024, 768, 390):
        page.set_viewport_size({"width": width, "height": 1000})
        for card in page.locator(".question").all():
            card_box = card.bounding_box()
            remove_box = card.locator(".remove-question").bounding_box()
            assert remove_box["x"] + remove_box["width"] <= card_box["x"] + card_box["width"] + 1, width
    assert page.locator(".question label[for]").evaluate_all(
        "labels => labels.every(label => !!document.getElementById(label.htmlFor))"
    )
    assert page.locator(".question input, .question select, .question textarea").evaluate_all("""items => items.every(input =>
      input.getAttribute('aria-label') ||
      (input.id && document.querySelector(`label[for=\"${input.id}\"]`)) ||
      input.closest('label'))""")
    appearance = page.locator(".appearance-controls")
    choice = appearance.locator('[data-theme-choice="light"]')
    if not choice.is_visible():
        appearance.locator("summary").click()
    choice.click()
    page.screenshot(path=str(tmp_path / "basal-390-playground.png"), full_page=True, animations="disabled")
