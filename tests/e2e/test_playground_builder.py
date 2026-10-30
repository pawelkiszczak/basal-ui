"""The Playground's question builder, Compare and Copy as code in a real browser, on scripts/ui_fixture_server.py (no
model): option rows go out exactly as typed for every question type, Compare reads each type's own answer field, the
snippets are valid and never inline media, and the histogram labels a range of equal values.

Runs without BASAL_E2E=1 like test_a11y.py: uv run --no-sync pytest tests/e2e/test_playground_builder.py."""

import json

import pytest

from tests.e2e.test_a11y import browser, fixture_url, sync_api  # noqa: F401 (pytest fixtures)

pytestmark = pytest.mark.fixture_server


@pytest.fixture(scope="module")
def page(browser, fixture_url):  # noqa: F811
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(fixture_url + "/#playground")
    page.wait_for_function("() => typeof statusCache !== 'undefined' && statusCache && modelData.length > 0")
    page.evaluate("() => showView('playground', true)")
    yield page
    context.close()
    assert not errors, errors


def card_with(page, q):
    """One question card built from `q`; returns its index."""
    page.evaluate("q => setRequest({state: 'Order 1 arrived broken.', questions: {q}})", q)
    return page.locator(".question").last


def fill_rows(card, rows):
    """Types rows [(key | None, description)] into the card's first option field, replacing what is there."""
    field = card.locator(".option-field").first
    while field.locator(".opt-remove").count():
        field.locator(".opt-remove").first.click()
    for key, desc in rows:
        field.locator(".add-option").click()
        row = field.locator(".option-row").last
        if key is not None:
            row.locator(".opt-key").fill(key)
        row.locator(".opt-desc").fill(desc)
    return field


def built(page):
    return page.evaluate("() => buildRequest().questions.q")


def card_error(page):
    return page.evaluate("() => document.querySelector('.question')._error")


def test_keyless_rows_that_look_keyed_stay_keyless_for_choice_and_act(page):
    for kind in ("choice", "act"):
        card = card_with(page, {"type": kind, "instructions": "Refund?", "criteria": {"a": "x", "b": "y"}})
        fill_rows(card, [(None, "Refund: full amount"), (None, "Refund: partial")])
        q = built(page)
        assert q["criteria"] == {"0": "Refund: full amount", "1": "Refund: partial"}, kind
        assert q["option_keys"] == "hide"
        # a re-render (type round trip) keeps them keyless: the key boxes stay empty
        page.evaluate("() => renderOptionRows(document.querySelector('.question'))")
        assert card.locator(".opt-key").evaluate_all("xs => xs.map(x => x.value)") == ["", ""]


def test_keyless_rows_that_look_keyed_stay_a_list_for_multi_classify_and_verify(page):
    for kind, field in (("multi", "criteria"), ("classify", "labels"), ("verify", "claims")):
        card = card_with(page, {"type": kind, "instructions": "", field: ["a", "b"]})
        fill_rows(card, [(None, "Refund: full amount"), (None, "Refund: partial")])
        assert built(page)[field] == ["Refund: full amount", "Refund: partial"], kind


def test_explicit_keys_round_trip_and_survive_a_type_change(page):
    card = card_with(page, {"type": "choice", "instructions": "Refund?", "criteria": {}})
    fill_rows(card, [("full", "Refund: full amount"), ("partial", "Refund: partial")])
    assert built(page)["criteria"] == {"full": "Refund: full amount", "partial": "Refund: partial"}
    assert built(page)["option_keys"] == "show"
    card.locator(".q-type").select_option("multi")
    assert built(page)["criteria"] == {"full": "Refund: full amount", "partial": "Refund: partial"}


def test_a_duplicate_key_is_an_accessible_error_that_stops_the_request(page):
    card = card_with(page, {"type": "choice", "instructions": "Refund?", "criteria": {}})
    field = fill_rows(card, [("refund", "full amount"), ("refund", "partial")])
    hint = field.locator(".option-hint")
    sync_api.expect(hint).to_be_visible()
    sync_api.expect(hint).to_contain_text("Duplicate key refund")
    assert "warning" not in (hint.get_attribute("class") or "")  # shown as an error
    keys = field.locator(".opt-key")
    hint_id = hint.get_attribute("id")
    for i in range(2):
        assert keys.nth(i).get_attribute("aria-invalid") == "true"
        assert keys.nth(i).get_attribute("aria-describedby") == hint_id
    assert "Duplicate key refund" in card_error(page)
    sync_api.expect(card.locator(".q-error")).to_be_hidden()  # said once, by the rows, not again by the card
    assert page.evaluate("() => validatedRequest()") is None  # not sent, nothing dropped silently
    keys.nth(1).fill("partial")
    assert card_error(page) is None
    assert built(page)["criteria"] == {"refund": "full amount", "partial": "partial"}
    # classify labels given as objects: names may hold spaces, but not repeat
    card = card_with(page, {"type": "classify", "labels": [{"name": "a b", "rubric": "x"}, {"name": "c"}]})
    assert built(page)["labels"] == [{"name": "a b", "rubric": "x"}, {"name": "c"}]
    fill_rows(card, [("very good", "x"), ("very good", "y")])
    assert "Duplicate key very good" in card_error(page)


@pytest.mark.parametrize("kind", ["noul", "act-yesno"])
def test_a_no_description_alone_is_sent_as_no(page, kind):
    q = {"type": "noul", "instructions": "Broken?"} if kind == "noul" else {"type": "act", "base": "noul"}
    card = card_with(page, q)
    rows = card.locator(".option-row")
    assert rows.locator(".option-tag").all_inner_texts() == ["Yes (true)", "No (false)"]
    rows.nth(1).locator(".opt-desc").fill("it arrived intact")
    assert built(page)["criteria"] == {"false": "it arrived intact"}
    page.evaluate("() => renderOptionRows(document.querySelector('.question'))")
    assert card.locator(".opt-desc").evaluate_all("xs => xs.map(x => x.value)") == ["", "it arrived intact"]
    rows.nth(0).locator(".opt-desc").fill("it is broken")
    assert built(page)["criteria"] == {"true": "it is broken", "false": "it arrived intact"}


def test_score_levels_and_unedited_requests_round_trip(page):
    card = card_with(page, {"type": "score", "criteria": ["bad", "ok: fine", "good"]})
    assert built(page)["criteria"] == ["bad", "ok: fine", "good"]
    card.locator(".opt-desc").nth(1).fill("ok: fine enough")
    assert built(page)["criteria"] == ["bad", "ok: fine enough", "good"]
    hidden = {"type": "choice", "criteria": {"0": "Refund: full", "1": "Refund: partial"}, "option_keys": "hide"}
    card = card_with(page, hidden)
    card.locator(".opt-desc").nth(1).fill("Refund: none")  # edited: still keyless under hidden keys
    assert built(page)["criteria"] == {"0": "Refund: full", "1": "Refund: none"}


def test_row_tags_are_translated(browser, fixture_url):  # noqa: F811
    context = browser.new_context()
    context.add_init_script("localStorage.setItem('basal-ui.lang', 'pl')")
    page = context.new_page()
    try:
        page.goto(fixture_url + "/#playground")
        page.wait_for_function("() => typeof statusCache !== 'undefined' && statusCache && modelData.length > 0")
        card = card_with(page, {"type": "noul", "instructions": "Broken?"})
        assert card.locator(".option-tag").all_inner_texts() == ["Tak (true)", "Nie (false)"]
        assert card.locator(".opt-desc").first.get_attribute("aria-label") == "Tak (true)"
    finally:
        context.close()


ANSWERS = {
    # act: the most probable outcome differs, the chosen action (by expected cost) is the same
    "act": (
        {"type": "act", "action": "refund", "answer": "broken", "probabilities": {"broken": 0.6, "fine": 0.4}},
        {"type": "act", "action": "refund", "answer": "fine", "probabilities": {"broken": 0.4, "fine": 0.6}},
        True,
    ),
    # multi: the same most probable label, different selected sets
    "multi": (
        {"type": "multi", "selected": ["a", "b"], "probabilities": {"a": 0.9, "b": 0.6, "c": 0.1}},
        {"type": "multi", "selected": ["a"], "probabilities": {"a": 0.9, "b": 0.4, "c": 0.1}},
        False,
    ),
    "multi-order": (
        {"type": "multi", "selected": ["a", "b"], "probabilities": {"a": 0.9, "b": 0.6}},
        {"type": "multi", "selected": ["b", "a"], "probabilities": {"a": 0.7, "b": 0.8}},
        True,
    ),
    "classify": (
        {"type": "classify", "label": "spam", "scores": {"spam": 0.7, "ham": 0.3}},
        {"type": "classify", "label": "ham", "scores": {"spam": 0.4, "ham": 0.6}},
        False,
    ),
    "classify-multi": (
        {"type": "classify", "labels": ["x", "y"], "scores": {"x": 0.9, "y": 0.8}},
        {"type": "classify", "labels": ["y", "x"], "scores": {"x": 0.7, "y": 0.9}},
        True,
    ),
    "score": (
        {"type": "score", "score": 2.2, "probabilities": {"0": 0.1, "1": 0.2, "2": 0.3, "3": 0.4}},
        {"type": "score", "score": 1.9, "probabilities": {"0": 0.1, "1": 0.2, "2": 0.4, "3": 0.3}},
        True,
    ),
    "noul": ({"type": "noul", "noul": 0.7}, {"type": "noul", "noul": 0.4}, False),
    "choice": (
        {"type": "choice", "choice": "a", "probabilities": {"a": 0.6, "b": 0.4}},
        {"type": "choice", "choice": "a", "probabilities": {"a": 0.5, "b": 0.5}},
        True,
    ),
    "tool": (
        {"type": "tool", "tool": "get_weather", "probabilities": {"get_weather": 0.5, "answer_directly": 0.5}},
        {"type": "tool", "tool": "answer_directly", "probabilities": {"get_weather": 0.5, "answer_directly": 0.5}},
        False,
    ),
    "verify": (
        {"type": "verify", "claims": {"0": {"verdict": "supported"}}},
        {"type": "verify", "claims": {"0": {"verdict": "contradicted"}}},
        False,
    ),
}


@pytest.mark.parametrize("case", list(ANSWERS))
def test_compare_reads_each_types_answer_field(page, case):
    a, b, same = ANSWERS[case]
    out = page.evaluate(
        """([a, b]) => {
          const results = [{status: 'fulfilled', value: {answers: {q: a}}}, {status: 'fulfilled', value: {answers: {q: b}}}];
          const request = {questions: {q: {type: a.type}}};
          const box = document.createElement('div');
          box.innerHTML = compareDiff(['m/a', 'm/b'], results, request).text + compareColumn('m/a', results[0], request).text;
          return {badge: box.querySelector('.compare-diff .badge')?.textContent, table: Boolean(box.querySelector('.mini-table')),
                  pre: Boolean(box.querySelector('section pre'))};
        }""",
        [a, b],
    )
    assert out["badge"] == ("Same answer" if same else "Different answers")
    assert not out["pre"]  # every type is drawn as its single-model view, not raw JSON
    if case.startswith("classify"):
        assert out["table"]  # classify's scores are compared too


def snippet(page, kind):
    return page.evaluate("kind => codeSnippet(kind)", kind)


def test_mcp_snippet_is_a_json_rpc_tools_call(page):
    card_with(page, {"type": "choice", "instructions": "Refund?", "criteria": {"a": "x", "b": "y"}})
    text = snippet(page, "mcp")
    assert text.startswith("# MCP (streamable HTTP, JSON-RPC 2.0)")
    assert "/mcp -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream'" in text
    body = json.loads(text.split("-d '", 1)[1].rsplit("'", 1)[0])
    assert body["jsonrpc"] == "2.0" and body["id"] == 1 and body["method"] == "tools/call"
    assert body["params"]["name"] == "decide_many"
    assert set(body["params"]["arguments"]) == {"state", "questions"}


def test_media_is_never_inlined_in_system_one_snippets(page):
    card_with(page, {"type": "choice", "instructions": "Refund?", "criteria": {"a": "x", "b": "y"}})
    page.evaluate(
        """() => { selectedMedia.push({kind: 'image', data: 'QUFB'.repeat(50000), file: {name: 'photo.png'}}); }"""
    )
    try:
        curl = snippet(page, "curl")
        python = snippet(page, "python")
    finally:
        page.evaluate("() => { selectedMedia.pop(); }")
    for text in (curl, python):
        assert "QUFBQUFB" not in text and len(text) < 5000
    assert curl.startswith("# media:") and '"data":"BASE64_OF_photo.png"' in curl
    assert '"data": media("photo.png")' in python and "def media(path):" in python
    assert python.startswith("import base64\nimport httpx\n")


def test_histogram_of_equal_values_is_one_bin_labelled_with_the_value(page):
    out = page.evaluate(
        """() => {
          const box = document.createElement('div');
          box.innerHTML = chartHistogram({label: 'Latency', values: [50, 50, 50], format: v => `${v} ms`});
          return {rows: [...box.querySelectorAll('tbody tr')].map(r => [...r.cells].map(c => c.textContent)),
                  bars: box.querySelectorAll('rect').length, caption: [...box.querySelectorAll('figcaption span')]
                    .map(s => s.textContent)};
        }"""
    )
    assert out["rows"] == [["50 ms", "50 ms", "3"]]
    assert out["bars"] == 1
    assert out["caption"][0] == "50 ms" and out["caption"][-1] == "50 ms"
    spread = page.evaluate(
        """() => { const box = document.createElement('div');
          box.innerHTML = chartHistogram({label: 'L', values: [0, 10], bins: 2});
          return [...box.querySelectorAll('tbody tr')].map(r => [...r.cells].map(c => c.textContent)); }"""
    )
    assert spread == [["0", "5", "1"], ["5", "10", "1"]]


EVERY_TYPE = {
    "choice": {"type": "choice", "instructions": "Refund?", "criteria": {"full": "Refund: full", "none": "No refund"}},
    "noul": {"type": "noul", "instructions": "Broken?", "criteria": {"false": "intact"}},
    "score": {"type": "score", "instructions": "Damage?", "criteria": ["none", "some", "total"]},
    "multi": {"type": "multi", "instructions": "Issues?", "criteria": {"late": "Late: yes", "broken": "Broken"}},
    "act": {
        "type": "act",
        "instructions": "Act?",
        "criteria": {"a": "x", "b": "y"},
        "costs": {"wrong": 10, "defer": 1},
    },
    "tool": {"type": "tool", "tools": [{"name": "refund", "description": "Refund the order"}]},
    "classify": {"type": "classify", "labels": ["Refund: full", "Refund: partial"], "multi_label": True},
    "verify": {"type": "verify", "claims": ["The order arrived: broken"]},
    "compress": {"type": "compress"},
    "extract": {"type": "extract", "schema": {"type": "object", "properties": {"order": {"type": "string"}}}},
}


@pytest.mark.parametrize("kind", list(EVERY_TYPE))
def test_every_type_round_trips_and_compares_with_the_fixture_answers(page, kind):
    q = EVERY_TYPE[kind]
    card_with(page, q)
    assert built(page) == q
    if kind not in ("tool", "compress", "extract"):  # an edit then an undo of it gives the request back as it was
        desc = page.locator(".question").last.locator(".opt-desc").first
        before = desc.input_value()
        desc.fill(before + " x")
        desc.fill(before)
        assert built(page) == q
    out = page.evaluate(
        """async () => {
          const request = {...buildRequest(), model: selectedModel};
          const value = await api('/api/decide', {method: 'POST', body: JSON.stringify(request)});
          const results = [{status: 'fulfilled', value}, {status: 'fulfilled', value}];
          const box = document.createElement('div');
          box.innerHTML = compareDiff(['m/a', 'm/b'], results, request).text;
          return {badge: box.querySelector('.compare-diff .badge')?.textContent, answer: answerOf(value.answers.q, request.questions.q)};
        }"""
    )
    assert out["answer"] is not None, kind
    assert out["badge"] == "Same answer"
