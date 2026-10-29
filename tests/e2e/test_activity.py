"""Browser regressions for oldest-first history draining and log-buffer restarts."""

import json
from urllib.parse import parse_qs, urlparse

import pytest

from tests.e2e import test_pages

browser = test_pages.browser  # pytest fixture shared with test_pages


@pytest.fixture
def page(browser, ui):
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    tab = context.new_page()
    tab.set_default_timeout(10_000)
    tab.goto(ui.url + "/#overview")
    tab.wait_for_function("() => typeof statusCache !== 'undefined' && statusCache")
    yield tab
    context.close()


def summary(index):
    return {
        "id": f"h{index}",
        "ts": index,
        "model": "test",
        "source": "api",
        "status": 200,
        "latency_ms": 1,
        "state_preview": f"state {index}",
        "answers": {},
        "deferred": False,
        "truncated": False,
    }


def test_history_poll_drains_more_than_one_oldest_first_page(page):
    requests = []

    def history(route):
        params = parse_qs(urlparse(route.request.url).query)
        requests.append(params)
        if params.get("order") == ["asc"]:
            start = int(params["after"][0][1:]) + 1
            rows = [summary(i) for i in range(start, min(start + 100, 300))]
        elif "before" in params:
            end = int(params["before"][0][1:])
            rows = [summary(i) for i in range(end - 1, max(end - 101, -1), -1)]
        else:
            rows = [summary(i) for i in range(149, 49, -1)]
        route.fulfill(json={"items": rows, "models": ["test"]})

    page.route("**/api/history?**", history)
    page.click('.side-nav [data-view="history"]')
    page.wait_for_function("() => document.querySelectorAll('#historyTable tr.history-row').length === 100")
    page.evaluate("() => loadHistory('poll')")
    page.wait_for_function("() => document.querySelectorAll('#historyTable tr.history-row').length === 250")
    assert page.evaluate("() => [historyAfter, historyBefore]") == ["h299", "h50"]
    page.evaluate("() => loadHistory('more')")
    assert page.locator("#historyTable tr.history-row").count() == 300
    assert page.evaluate("() => [historyAfter, historyBefore]") == ["h299", "h0"]
    assert [r.get("after", [None])[0] for r in requests if r.get("order") == ["asc"]] == ["h149", "h249"]


def test_history_keeps_live_cap_and_preserves_paged_rows_across_empty_polls(page):
    def history(route):
        params = parse_qs(urlparse(route.request.url).query)
        if params.get("order") == ["asc"]:
            start = int(params["after"][0][1:]) + 1
            rows = [summary(i) for i in range(start, min(start + 100, 1300)) if i >= 800]
        elif "before" in params:
            end = int(params["before"][0][1:])
            rows = [summary(i) for i in range(end - 1, max(end - 101, -1), -1)]
        else:
            rows = [summary(i) for i in range(799, 699, -1)]
        route.fulfill(json={"items": rows, "models": ["test"]})

    page.route("**/api/history?**", history)
    page.click('.side-nav [data-view="history"]')
    page.wait_for_function("() => historyRows.size === 100")
    page.evaluate("() => loadHistory('poll')")
    page.wait_for_function("() => historyRows.size === 500")
    assert page.evaluate("() => historyBefore") == "h800"

    page.click("#historyMore")
    page.wait_for_function("() => historyRows.size === 600")
    page.evaluate("() => loadHistory('poll')")
    page.wait_for_function("() => historyRows.size === 600")
    assert page.evaluate("() => historyBefore") == "h700"

    for expected_size in range(700, 1301, 100):
        page.click("#historyMore")
        page.wait_for_function(f"() => historyRows.size === {expected_size}")
        page.evaluate("() => loadHistory('poll')")
        assert page.evaluate("() => historyRows.size") == expected_size
    assert page.evaluate("() => [historyRows.size, historyBefore, historyBrowsingOlder]") == [1300, "h0", True]
    assert page.locator("#historyTable tr.history-row").count() == 1300


def test_calibration_responses_and_selection_stay_with_the_displayed_model(page):
    page.click('.side-nav [data-view="manager"]')
    pending_a = []
    selected = []

    def calibrations(route):
        params = parse_qs(urlparse(route.request.url).query)
        model = params["id"][0] if params.get("id") else json.loads(route.request.post_data)["id"]
        if route.request.method == "POST":
            selected.append(json.loads(route.request.post_data)["id"])
        if model == "A" and route.request.method == "GET":
            pending_a.append(route)
            return
        route.fulfill(
            json={
                "selected": None,
                "shipped": None,
                "files": [{"file": f"cal-{model}", "when": 1, "fitted_on": "test"}],
            }
        )

    page.route("**/api/calibrations**", calibrations)
    with page.expect_request(lambda request: "/api/calibrations?id=A" in request.url):
        page.evaluate("""() => {
          modelData=[{id:'A',downloaded:true},{id:'B',downloaded:true}];
          calibrationSignature='';
          renderCalibrationModels();
        }""")
    page.select_option("#calibrationModel", "B")
    page.locator('#calibrationFiles input[value="cal-B"]').wait_for(state="visible")
    assert len(pending_a) == 1

    with page.expect_response(lambda response: "/api/calibrations?id=A" in response.url) as late_a:
        pending_a[0].fulfill(
            json={
                "selected": None,
                "shipped": None,
                "files": [{"file": "cal-A", "when": 1, "fitted_on": "test"}],
            }
        )
    late_a.value.body()
    assert page.locator('#calibrationFiles input[value="cal-B"]').count() == 1
    assert page.locator('#calibrationFiles input[value="cal-A"]').count() == 0

    with page.expect_response(lambda response: "/api/calibrations/select" in response.url):
        page.locator('#calibrationFiles input[name="calibrationUse"][value="cal-B"]').check()
    assert selected == ["B"]


def test_logs_clear_old_lines_when_source_buffer_generation_changes(page):
    def logs(route):
        params = parse_qs(urlparse(route.request.url).query)
        restarted = params.get("generation") == ["first"]
        generation, text = ("second", "new buffer line") if restarted else ("first", "old buffer line")
        route.fulfill(
            json={
                "sources": [{"id": "basal-ui", "state": "ui"}],
                "generation": generation,
                "lines": [{"seq": 1, "ts": 1, "text": text}],
            }
        )

    page.route("**/api/logs?**", logs)
    page.click('.side-nav [data-view="logs"]')
    page.wait_for_function("() => document.querySelector('#logOutput').textContent.includes('old buffer line')")
    page.evaluate("() => loadLogs()")
    page.wait_for_function("() => document.querySelector('#logOutput').textContent.includes('new buffer line')")
    assert "old buffer line" not in page.locator("#logOutput").inner_text()
