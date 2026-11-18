"""Inspect uses the stored language without translating model data or server errors."""

import pytest

playwright = pytest.importorskip("playwright.sync_api")


@pytest.mark.parametrize("language", ["en", "pl"])
@pytest.mark.parametrize("embedded", [False, True])
def test_inspect_language(ui, language, embedded):
    request = {
        "state": "Synthetic state",
        "questions": {
            "q": {"type": "choice", "instructions": "Synthetic question?", "criteria": {"a": "Alpha", "b": "Beta"}}
        },
    }
    info = {
        "model": "synthetic/model",
        "device": "cpu",
        "dtype": "bf16",
        "layers": 2,
        "prompt_format": "test",
        "orders": 1,
    }
    order = {
        "segments": [{"kind": "state", "text": "Synthetic state"}],
        "n_tokens": 3,
        "perm": [0, 1],
        "letters": ["A", "B"],
        "letter_probs": [0.8, 0.2],
        "top_tokens": [["A", 0.8]],
        "canon": [0.8, 0.2],
    }
    question = {
        "name": "q",
        "question": "Synthetic question?",
        "kind": "choice",
        "type": "choice",
        "lang": "en",
        "options": ["Alpha", "Beta"],
        "keys": ["a", "b"],
        "answer": {"index": 0, "key": "a", "confidence": 0.8},
        "thresholds": {},
        "orders": [order],
        "layers": [[0.6, 0.4], [0.8, 0.2]],
        "calibrated": [0.8, 0.2],
        "mean": [0.8, 0.2],
        "temperature": 1.0,
        "timing_ms": {"packed": 10, "separate_with_lens": 20},
        "packed_max_diff": 0.0,
        "served_max_diff": 0.0,
    }
    with playwright.sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context()
        context.add_init_script(f"localStorage.setItem('basal-ui.lang', '{language}')")
        context.route("**/viz/api/info", lambda route: route.fulfill(json=info))
        context.route(
            "**/viz/api/examples",
            lambda route: route.fulfill(json=[{"file": "synthetic.json", "id": "example", "request": request}]),
        )
        context.route(
            "**/viz/api/explain",
            lambda route: route.fulfill(json={"model": "synthetic/model", "total_ms": 30, "questions": [question]}),
        )
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(ui.url + ("/" if embedded else "/viz/"), wait_until="networkidle")
        if embedded:
            page.evaluate("""() => { renderInspectState = () => {}; const frame = document.querySelector('#vizFrame');
                frame.hidden = false; frame.src = '/viz/'; frame.style.width = '1400px';
                frame.style.height = '850px'; document.body.append(frame); }""")
            frame = page.frame_locator("#vizFrame")
        else:
            frame = page
        playwright.expect(frame.locator("#qs input").first).to_be_visible()
        assert frame.locator("html").get_attribute("lang") == language
        playwright.expect(frame.locator("#out")).to_contain_text(
            "Wybierz przykład" if language == "pl" else "Pick an example"
        )
        loading = frame.locator("#go").evaluate(
            "button => { button.click(); return document.querySelector('.loading').textContent; }"
        )
        assert ("Model pracuje" if language == "pl" else "Running the model") in loading
        playwright.expect(frame.locator("#out")).to_contain_text(
            "Co odczytuje model" if language == "pl" else "What the model reads"
        )
        playwright.expect(frame.locator("#out")).to_contain_text("Synthetic question?")
        playwright.expect(frame.locator("#out")).to_contain_text("Alpha")
        context.unroute("**/viz/api/explain")
        calls = []

        def server_error(route):
            calls.append(route.request.post_data)
            route.fulfill(status=500, json={"error": "Server diagnostic"})

        context.route("**/viz/api/explain", server_error)
        frame.locator("#go").click()
        playwright.expect(frame.locator(".err")).to_have_text(
            ("Błąd: " if language == "pl" else "Error: ") + "Server diagnostic"
        )
        frame.locator("body").evaluate(
            "() => loadRequest({state: 'synthetic', questions: {raw: {type: 'custom', value: 42}}})"
        )
        raw = frame.get_by_label("JSON pytania" if language == "pl" else "Question JSON", exact=True)
        raw.fill("{")
        frame.locator("#go").click()
        playwright.expect(frame.locator(".err")).to_have_text(
            "Błąd: Niepoprawny JSON pytania." if language == "pl" else "Error: Invalid question JSON."
        )
        assert len(calls) == 1
        raw.fill('{"type":"custom","value":42}')
        frame.locator("#go").click()
        playwright.expect(frame.locator(".err")).to_have_text(
            ("Błąd: " if language == "pl" else "Error: ") + "Server diagnostic"
        )
        assert len(calls) == 2
        assert not errors
        context.close()
        browser.close()
