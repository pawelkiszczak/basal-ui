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
    page.click("#scheduleCheck")
    test_pages.sync_api.expect(page.locator("#scheduleCheckResult")).to_contain_text("Folders are ready")
    page.click("#scheduleForm button[type=submit]")
    test_pages.sync_api.expect(page.locator("[data-action=run]")).to_be_visible()
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
