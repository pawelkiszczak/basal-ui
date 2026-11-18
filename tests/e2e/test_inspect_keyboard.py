"""Inspect's owned editor keeps its accessible labels and request values through type changes."""

import pytest

sync_api = pytest.importorskip("playwright.sync_api")


def test_editor_labels_and_serialization(ui):
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(f"{ui.url}/viz/")
        page.wait_for_function("booted")
        page.evaluate("""() => loadRequest({state: 'synthetic', questions: {
          q: {type: 'choice', instructions: 'Pick', criteria: {a: 'Alpha', b: 'Beta'}}}})""")
        assert page.get_by_label("Question type").count() == 1
        assert page.get_by_label("Question", exact=True).count() == 1
        assert page.get_by_label("Option keys").count() == 1
        assert page.get_by_label("Option description").count() == 2
        assert page.evaluate("readRequest().questions.q.criteria") == {"a": "Alpha", "b": "Beta"}
        page.get_by_label("Question type").select_option("noul")
        assert page.get_by_label("Option key", exact=True).first.is_disabled()
        assert page.get_by_label("Remove option").first.is_disabled()
        assert page.evaluate("readRequest().questions.q.criteria") == {"true": "Alpha", "false": "Beta"}
        page.get_by_label("Question type").select_option("choice")
        assert page.get_by_label("Option key", exact=True).first.is_enabled()
        page.get_by_label("Remove option").first.click()
        assert page.evaluate("readRequest().questions.q.criteria") == {"false": "Beta"}
        page.evaluate("loadRequest({state: '', questions: {raw: {type: 'custom', value: 42}}})")
        assert page.get_by_label("Question JSON").count() == 1
        assert page.get_by_label("Remove question").count() == 1
        assert page.evaluate("readRequest().questions.raw") == {"type": "custom", "value": 42}
        browser.close()
