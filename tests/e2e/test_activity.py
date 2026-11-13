"""Browser regressions for oldest-first history draining and log-buffer restarts."""

import json
import os
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest

from basal_ui import settings
from tests.e2e import test_pages

browser = test_pages.browser  # pytest fixture shared with test_pages


@pytest.fixture
def page(browser, ui):
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    tab = context.new_page()
    tab.set_default_timeout(10_000)
    tab.goto(ui.url + "/")
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
    test_pages.view(page, "history")
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
    test_pages.view(page, "history")
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
    test_pages.manager_tab(page, "library")
    page.locator('[data-manager-tab="calibration"]').click()
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
    test_pages.view(page, "logs")
    page.wait_for_function("() => document.querySelector('#logOutput').textContent.includes('old buffer line')")
    page.evaluate("() => loadLogs()")
    page.wait_for_function("() => document.querySelector('#logOutput').textContent.includes('new buffer line')")
    assert "old buffer line" not in page.locator("#logOutput").inner_text()


def test_calibration_layout_refresh_and_refit_capability(page):
    test_pages.manager_tab(page, "calibration")
    state = {"layout": "canonical"}

    def calibrations(route):
        route.fulfill(
            json={
                "selected": None,
                "calibrate_layout": False,
                "default_layout": "canonical",
                "shipped": {"file": "shipped", "context": {"layout": state["layout"], "mode": "eager"}},
                "files": [
                    {"file": "canonical", "layout": "canonical", "rows": "canonical.rows.jsonl", "when": 1},
                    {"file": "qfirst", "layout": "qfirst", "rows": "qfirst.rows.jsonl", "when": 1},
                ],
            }
        )

    page.wait_for_function("() => document.querySelector('#calibrationModel').value")
    model = page.locator("#calibrationModel").input_value()
    page.route("**/api/calibrations?**", calibrations)
    page.evaluate("() => { clearTimeout(pollTimer); loadCalibrations(); }")
    page.locator('[data-refit="canonical.rows.jsonl"]').wait_for()
    assert page.locator('[data-refit="canonical.rows.jsonl"]').is_enabled()
    assert page.locator('[data-refit="qfirst.rows.jsonl"]').is_disabled()
    assert page.locator("tr", has=page.locator('[data-refit="qfirst.rows.jsonl"]')).locator(".reason").is_visible()
    state["layout"] = "qfirst"
    page.evaluate(
        """id => { renderStatus({...statusCache, models:[{id,state:'ready',options:{layout:'qfirst'}}]}); clearTimeout(pollTimer); }""",
        model,
    )
    page.wait_for_function(
        "() => document.querySelector('#calibrationFiles').textContent.includes('Fit layout: qfirst')"
    )
    assert page.locator("#runCalibration").is_disabled()
    assert page.locator('[data-refit="canonical.rows.jsonl"]').is_enabled()
    state["layout"] = "canonical"
    page.evaluate("() => { renderStatus({...statusCache, models:[]}); clearTimeout(pollTimer); }")
    page.wait_for_function(
        "() => document.querySelector('#calibrationFiles').textContent.includes('Fit layout: canonical')"
    )
    assert page.locator("#runCalibration").is_enabled()
    page.route("**/api/models/options", lambda route: route.fulfill(json={}))
    state["layout"] = "qfirst"
    page.evaluate(
        """id => {
      optionsState={id,save:true,data:{},media:false};
      collectOptions=()=>({layout:'qfirst'}); document.querySelector('#loadOptions').showModal();
    }""",
        model,
    )
    page.locator("#optionsApply").click()
    page.wait_for_function(
        "() => document.querySelector('#calibrationFiles').textContent.includes('Fit layout: qfirst')"
    )
    assert page.locator("#runCalibration").is_disabled()


def test_calibration_tracks_real_saved_and_active_layout(page, ui, text_model):
    model = text_model["id"]
    active = ui.model(model)
    if active and active["state"] != "ready":
        pytest.skip("the model must be ready or unloaded to restore its state")
    original = json.loads(ui.settings.read_text()) if ui.settings.exists() else {}
    saved = original.get("load_options", {}).get(model, {})
    try:
        ui.unload(model)
        ui.put("/api/models/options", {"id": model, "options": {"layout": "canonical"}})
        test_pages.manager_tab(page, "calibration")
        page.select_option("#calibrationModel", model)
        page.wait_for_function(
            "() => document.querySelector('#calibrationFiles').textContent.includes('Fit layout: canonical')"
        )
        page.evaluate("id => openLoadOptions(id, false, 'save')", model)
        page.locator("#opt-layout").evaluate("el => el.closest('details').open = true")
        page.select_option("#opt-layout", "qfirst")
        page.locator("#optionsApply").click()
        page.wait_for_function(
            "() => document.querySelector('#calibrationFiles').textContent.includes('Fit layout: qfirst')"
        )
        assert ui.get("/api/calibrations", params={"id": model})["shipped"]["context"]["layout"] == "qfirst"
        ui.post("/api/load", {"id": model, "options": {"layout": "canonical"}})
        ui.wait_model(model)
        page.wait_for_function(
            "() => document.querySelector('#calibrationFiles').textContent.includes('Fit layout: canonical')",
            timeout=30000,
        )
        assert ui.get("/api/calibrations", params={"id": model})["shipped"]["context"]["layout"] == "canonical"
        page.screenshot(path="/tmp/basal-issue-19-calibration.png")
        ui.unload(model)
        page.wait_for_function(
            "() => document.querySelector('#calibrationFiles').textContent.includes('Fit layout: qfirst')",
            timeout=30000,
        )
        assert ui.get("/api/calibrations", params={"id": model})["shipped"]["context"]["layout"] == "qfirst"
    finally:
        try:
            ui.unload(model)
            ui.put("/api/models/options", {"id": model, "options": saved})
            if active:
                ui.post("/api/load", {"id": model, "options": active["options"], "media": active["media_requested"]})
                restored = ui.wait_model(model)
                assert restored and restored["state"] == "ready", restored
        finally:
            # Preserve the session's original auto-load ordering and entries for other models.
            with patch.dict(os.environ, {"BASAL_UI_CONFIG": str(ui.settings)}):

                @settings._locked
                def restore_remembered():
                    current = settings.load()
                    if "last_models" in original:
                        current["last_models"] = original["last_models"]
                    else:
                        current.pop("last_models", None)
                    settings.save(current)

                restore_remembered()


def test_calibration_error_and_mismatch_states_keep_focus(page):
    test_pages.manager_tab(page, "calibration")
    page.wait_for_function("() => document.querySelector('#calibrationModel').value")
    data = {
        "selected": "fitted.json",
        "calibrate_layout": False,
        "default_layout": "canonical",
        "layout_mismatch_code": "calibration_layout_mismatch",
        "shipped": {"file": "shipped.json", "context": {"layout": "qfirst", "mode": "eager", "active": True}},
        "files": [{"file": "fitted.json", "layout": "canonical", "rows": "fitted.rows.jsonl", "when": 1}],
    }
    page.evaluate("data => renderCalibrationFiles(document.querySelector('#calibrationModel').value,data)", data)
    assert page.locator("#runCalibration").is_disabled()
    assert "certified thresholds will not apply" in page.locator("#calibrationFiles").inner_text()
    radio = page.locator('input[name="calibrationUse"][value="fitted.json"]')
    radio.focus()
    page.evaluate("data => renderCalibrationFiles(document.querySelector('#calibrationModel').value,data)", data)
    assert radio.evaluate("el => document.activeElement === el")
    page.screenshot(path="/tmp/basal-issue19-ui-synthetic-mismatch.png")
    data["shipped"] = {"error": "Invalid model metadata"}
    data["layout_mismatch_code"] = None
    page.evaluate("data => renderCalibrationFiles(document.querySelector('#calibrationModel').value,data)", data)
    assert page.locator("#runCalibration").is_disabled()
    assert "Invalid model metadata" in page.locator("#calibrationFiles").inner_text()
    assert page.locator('input[name="calibrationUse"]').count() == 1
    assert radio.evaluate("el => document.activeElement === el")
    page.screenshot(path="/tmp/basal-issue19-ui-synthetic-error.png")


def test_refit_button_works_when_saved_snapshot_is_unavailable(page, ui, text_model):
    model = text_model["id"]
    if ui.model(model):
        pytest.skip("model must be unloaded to test an unavailable Saved snapshot")
    original = json.loads(ui.settings.read_text()) if ui.settings.exists() else {}
    saved = original.get("load_options", {}).get(model, {})
    directory = ui.settings.parent / "calibrations" / model.replace("/", "--")
    directory.mkdir(parents=True, exist_ok=True)
    source = directory / "test-unavailable-snapshot.json"
    rows = source.with_suffix(".rows.jsonl")
    assert not source.exists() and not rows.exists()
    fitted = {
        "temperature_per_prim": {"choice": 1},
        "fitted_on": {"model": model, "revision": "original", "layout": "qfirst"},
    }
    header = {
        "basal_calibrate_rows": 1,
        "fmt": 2,
        "kind": "mlx",
        "quant": None,
        "model": model,
        "engine": "mlx",
        "data": "test.jsonl",
        "requests": 1,
        "baseline": None,
        "orders": 1,
    }
    row = {
        "line": 1,
        "req": 0,
        "name": "q",
        "prim": "choice",
        "gold": 0,
        "keys": ["0", "1"],
        "group": "1",
        "family": "q",
        "p": [0.99, 0.01],
    }
    source.write_text(json.dumps(fitted))
    rows.write_text(json.dumps(header) + "\n" + json.dumps(row) + "\n")
    result_path = None
    job_started = False
    try:
        ui.put("/api/models/options", {"id": model, "options": {"revision": "issue19-unavailable-revision"}})
        listing = ui.get("/api/calibrations", params={"id": model})
        assert listing["shipped"] is None
        test_pages.manager_tab(page, "calibration")
        page.wait_for_function("id => modelData.some(m => m.id === id)", arg=model)
        page.evaluate(
            """id => {
          const model = modelData.find(m => m.id === id);
          model.downloaded = false; model.has_calibration_rows = true;
          calibrationSignature = ''; renderCalibrationModels();
        }""",
            model,
        )
        page.select_option("#calibrationModel", model)
        button = page.locator(f'[data-refit="{rows}"]')
        button.wait_for()
        assert button.is_enabled()
        with page.expect_response(
            lambda response: response.url.endswith("/api/calibrate") and response.request.method == "POST"
        ) as started:
            button.click()
        assert started.value.status == 202
        job_started = True
        job = ui.wait_job("calibrate")
        assert job["phase"] == "done_with_warnings", job
        result_path = Path(job["result"]["file"])
        metadata = json.loads(result_path.read_text())["fitted_on"]
        assert (metadata["model"], metadata["revision"], metadata["layout"]) == (model, "original", "qfirst")
        assert ui.model(model) is None
    finally:
        if job_started and result_path is None:
            job = ui.status()["jobs"].get("calibrate")
            if job and job["running"]:
                ui.delete("/api/calibrate")
                job = ui.wait_job("calibrate")
            if job and (job.get("result") or {}).get("file"):
                result_path = Path(job["result"]["file"])
        ui.put("/api/models/options", {"id": model, "options": saved})
        source.unlink(missing_ok=True)
        rows.unlink(missing_ok=True)
        if result_path:
            result_path.unlink(missing_ok=True)
            result_path.with_suffix(".report.txt").unlink(missing_ok=True)


def test_calibration_transition_restores_session_on_failure(page, ui, text_model, monkeypatch):
    model = text_model["id"]
    if ui.model(model):
        pytest.skip("model must be unloaded for this restoration check")
    before = json.loads(ui.settings.read_text()) if ui.settings.exists() else {}

    def fail_after_change(*args):
        raise AssertionError("injected failure")

    try:
        active = ui.load(model, {"layout": "canonical"})
        remembered = json.loads(ui.settings.read_text()).get("last_models")
        monkeypatch.setattr(test_pages, "manager_tab", fail_after_change)
        with pytest.raises(AssertionError, match="injected failure"):
            test_calibration_tracks_real_saved_and_active_layout(page, ui, text_model)
        after = json.loads(ui.settings.read_text())
        assert after.get("last_models") == remembered
        assert after.get("load_options", {}).get(model, {}) == before.get("load_options", {}).get(model, {})
        restored = ui.model(model)
        assert restored["state"] == "ready" and restored["options"] == active["options"]
    finally:
        ui.unload(model)
        with patch.dict(os.environ, {"BASAL_UI_CONFIG": str(ui.settings)}):

            @settings._locked
            def restore_remembered():
                current = settings.load()
                if "last_models" in before:
                    current["last_models"] = before["last_models"]
                else:
                    current.pop("last_models", None)
                settings.save(current)

            restore_remembered()
