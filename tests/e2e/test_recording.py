"""Chromium microphone recording and the Decision payload, using a synthetic browser device."""

import base64
import io
import wave

import pytest

playwright = pytest.importorskip("playwright.sync_api", reason="install basal[e2e] and Chromium")


def test_recording_attaches_wav_and_sends_it(ui, audio_model):
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
        page.locator("#playgroundModel").select_option(audio_model["id"])
        page.evaluate(
            "request => setRequest(request)",
            {
                "state": "Classify this audio clip",
                "questions": {"sound": {"type": "choice", "criteria": {"speech": "Speech", "tone": "Tone"}}},
            },
        )
        page.locator("#recordAudio").click()
        playwright.expect(page.locator("#recordLevel")).to_be_visible()
        page.wait_for_timeout(1200)
        page.locator("#recordAudio").click()
        playwright.expect(page.locator("#mediaItems audio")).to_have_count(1, timeout=15000)
        with page.expect_response(lambda response: response.url.endswith("/api/decide")) as answered:
            page.locator("#sendButton").click()
        response = answered.value
        assert response.ok, response.text()
        decision = response.json()
        assert decision["answers"]["sound"]["choice"] in ("speech", "tone")
        assert decision["usage"]["media"]["audio"] == 1
        assert decision["usage"]["media"]["media_tokens"] > 0
        audio = response.request.post_data_json["media"][0]
        assert audio["type"] == "audio"
        with wave.open(io.BytesIO(base64.b64decode(audio["data"]))) as wav:
            assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (1, 2, 16000)
            assert 0 < wav.getnframes() <= 30 * 16000
        context.close()
        browser.close()
