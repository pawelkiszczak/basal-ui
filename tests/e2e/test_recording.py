"""Chromium microphone recording and the Decision payload, using a synthetic browser device."""

import base64
import io
import wave

import pytest

from tests.e2e.support import pick

playwright = pytest.importorskip("playwright.sync_api", reason="install basal[e2e] and Chromium")


def test_recording_attaches_wav_and_sends_it(ui):
    model = pick(
        ui,
        "BASAL_E2E_MEDIA_MODEL",
        lambda m: m.get("media_mode") and "audio" in m.get("media", []),
        "no downloaded audio-capable model is available",
    )
    ui.post("/api/load", {"id": model["id"], "media": True})
    loaded = ui.wait_model(model["id"])
    assert loaded and loaded["state"] == "ready", loaded
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch(args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"])
        except playwright.Error as error:
            if "Executable doesn't exist" in str(error):
                pytest.skip("Chromium is not installed")
            raise
        context = browser.new_context(permissions=["microphone"])
        page = context.new_page()
        page.goto(ui.url + "/#playground")
        page.locator("#playgroundModel").select_option(model["id"])
        page.locator("#stateInput").fill("Classify this audio")
        page.locator("#recordAudio").click()
        playwright.expect(page.locator("#recordLevel")).to_be_visible()
        page.wait_for_timeout(1200)
        page.locator("#recordAudio").click()
        playwright.expect(page.locator("#mediaItems audio")).to_have_count(1, timeout=15000)
        with page.expect_request(lambda request: request.url.endswith("/api/decide")) as sent:
            page.locator("#sendButton").click()
        audio = sent.value.post_data_json["media"][0]
        assert audio["type"] == "audio"
        with wave.open(io.BytesIO(base64.b64decode(audio["data"]))) as wav:
            assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 16000)
            assert 0 < wav.getnframes() <= 30 * 16000
        context.close()
        browser.close()
