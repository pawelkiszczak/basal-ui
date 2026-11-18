"""Real model and browser proof for scheduled and manual Batch."""

import json

from tests.e2e import test_pages

browser = test_pages.browser
page = test_pages.page


def test_scheduled_and_manual_batch_in_browser(page, ui, text_model, tmp_path):
    inputs, outputs = tmp_path / "inputs", tmp_path / "outputs"
    inputs.mkdir()
    outputs.mkdir()
    item = {"state": "A synthetic ticket", "question": "Choose", "options": ["yes", "no"]}
    source = inputs / "ticket.jsonl"
    source.write_text(json.dumps(item) + "\n")
    test_pages.view(page, "batch")
    assert page.locator("#batchRunTab").is_visible()
    assert page.locator("#batchSchedulesTab").is_visible()
    page.click("#batchSchedulesTab")
    page.select_option("#scheduleModel", text_model["id"])
    page.fill("#scheduleInputDir", str(inputs))
    page.fill("#scheduleOutputDir", str(outputs))
    page.fill("#scheduleEvery", "0")
    page.select_option("#scheduleInterval", "daily")
    assert page.locator("#scheduleEvery").is_disabled()
    page.select_option("#scheduleInterval", "minutes")
    assert page.locator("#scheduleEvery").is_enabled() and page.input_value("#scheduleEvery") == "0"
    page.select_option("#scheduleInterval", "daily")
    page.fill("#scheduleDaily", "10:30")
    page.click("#scheduleCheck")
    test_pages.sync_api.expect(page.locator("#scheduleCheckResult")).to_contain_text("Folders are ready")
    page.click("#scheduleForm button[type=submit]")
    test_pages.sync_api.expect(page.locator("[data-action=run]")).to_be_visible()
    assert ui.get("/api/schedules")["schedules"][0]["interval"] == {"daily_at": "10:30"}
    page.click("[data-action=preview]")
    test_pages.sync_api.expect(page.locator("[data-preview]")).to_contain_text("A synthetic ticket")
    page.click("[data-action=run]")
    job = ui.wait_job("batch")
    assert job["phase"] == "done", job
    assert (outputs / "ticket.answers.jsonl").is_file()
    schedule_id = ui.get("/api/schedules")["schedules"][0]["id"]
    ui.post(f"/api/schedules/{schedule_id}/run")
    run = ui.get(f"/api/schedules/{schedule_id}/runs")["runs"][0]
    assert (run["rows"], run["errors"]) == (1, 0)
    assert ui.get("/api/history", params={"source": "schedule"})["items"]
    count = ui.get("/api/stats")["models"][text_model["id"]]["requests"]
    page.click("#batchRunTab")
    page.fill("#batchInput", str(source))
    page.fill("#batchOutput", str(tmp_path / "manual.answers.jsonl"))
    page.click("#runBatch")
    page.wait_for_function(
        "path => statusCache?.jobs?.batch?.phase === 'done' && statusCache.jobs.batch.result?.output === path",
        arg=str(tmp_path / "manual.answers.jsonl"),
    )
    test_pages.sync_api.expect(page.locator("#downloadBatch")).to_be_visible()
    assert (tmp_path / "manual.answers.jsonl").is_file()
    assert ui.get("/api/stats")["models"][text_model["id"]]["requests"] == count


def test_schedule_selector_filters_unsupported_cascades_without_replacing_saved_selection(
    page, ui, text_model, tmp_path
):
    inputs, outputs = tmp_path / "cascade-in", tmp_path / "cascade-out"
    inputs.mkdir()
    outputs.mkdir()
    assert (
        ui.post(
            "/api/schedules",
            {
                "id": "saved-cascade",
                "model": "blend",
                "input_dir": str(inputs),
                "output_dir": str(outputs),
                "interval": {"every_minutes": 60},
                "paused": True,
            },
        )["id"]
        == "saved-cascade"
    )
    test_pages.view(page, "batch")
    page.click("#batchSchedulesTab")
    test_pages.sync_api.expect(page.locator("[data-action=edit][data-id=saved-cascade]")).to_be_visible()
    state = page.evaluate("""() => {
        const models=[...statusCache.models,
          {id:'blend',state:'ready',kind:'cascade',cascade_kind:'blend',cascade_has_fallback:false},
          {id:'fallback',state:'ready',kind:'cascade',cascade_kind:'escalate',cascade_has_fallback:true},
          {id:'escalate',state:'ready',kind:'cascade',cascade_kind:'escalate',cascade_has_fallback:false}];
        statusCache.models=models; document.dispatchEvent(new CustomEvent('app:status',{detail:{models}}));
        const select=document.querySelector('#scheduleModel');
        const visible=[...select.options].map(option=>option.value);
        document.querySelector('[data-action=edit][data-id=saved-cascade]').click();
        const selected=select.querySelector('option[value=blend]');
        return {visible,selected:select.value,disabled:selected.disabled};
    }""")
    assert "blend" not in state["visible"] and "fallback" not in state["visible"]
    assert "escalate" in state["visible"]
    assert state["selected"] == "blend" and state["disabled"]
    page.click("#scheduleForm button[type=submit]")
    test_pages.sync_api.expect(page.locator("#scheduleError")).to_contain_text("Batch unavailable for this Cascade")
    page.click("#scheduleCancel")
    ui.delete("/api/schedules/saved-cascade")
