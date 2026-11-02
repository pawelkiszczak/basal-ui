"""Microphone recording in Chromium (opt-in real-model E2E)."""

import os

import pytest

from tests.e2e.support import UI

sync_api = pytest.importorskip("playwright.sync_api", reason="install basal[e2e] and run: playwright install chromium")


@pytest.fixture
def recording_ui(tmp_path):
    app = UI(tmp_path).start()
    yield app
    left = app.stop()
    assert not left, f"basal-serve children left running after microphone recording E2E: {left}"


def test_playground_recording_adds_a_wav_attachment(recording_ui):
    ui = recording_ui
    models = ui.get("/api/models")["models"]
    model_id = os.environ.get("BASAL_E2E_AUDIO_MODEL")
    if model_id:
        model = next((item for item in models if item["id"] == model_id), None)
        if (
            model is None
            or not model["downloaded"]
            or not model.get("media_mode")
            or "audio" not in model.get("media", [])
        ):
            pytest.fail(f"BASAL_E2E_AUDIO_MODEL={model_id} is not a downloaded, media-ready audio model")
    else:
        candidates = sorted(
            (
                item
                for item in models
                if item["downloaded"] and item.get("media_mode") and "audio" in item.get("media", [])
            ),
            key=lambda item: item.get("weights_bytes") or 0,
        )
        if not candidates:
            pytest.skip("no downloaded audio-capable model is available (set BASAL_E2E_AUDIO_MODEL)")
        model = candidates[0]
    ui.post("/api/load", {"id": model["id"], "media": True})
    loaded = ui.wait_model(model["id"])
    assert loaded and loaded["state"] == "ready", loaded

    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            args=["--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"]
        )
        context = browser.new_context(permissions=["microphone"])
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(ui.url + "/")
        page.wait_for_function("() => typeof statusCache !== 'undefined' && statusCache && modelData.length > 0")
        wav = page.evaluate(
            """async () => {
              const context = new AudioContext({sampleRate: 8000});
              const buffer = context.createBuffer(2, 2, 8000);
              buffer.getChannelData(0).set([0.25, 1]);
              buffer.getChannelData(1).set([0.5, 1]);
              const blob = encodeWav(buffer), bytes = new DataView(await blob.arrayBuffer());
              const result = {type: blob.type, size: blob.size, rate: bytes.getUint32(24, true),
                channels: bytes.getUint16(22, true), first: bytes.getInt16(44, true), second: bytes.getInt16(46, true)};
              await context.close();
              return result;
            }"""
        )
        assert wav == {
            "type": "audio/wav",
            "size": 48,
            "rate": 8000,
            "channels": 1,
            "first": 12287,
            "second": 32767,
        }
        page.select_option("#playgroundModel", model["id"])
        record = page.locator("#recordAudio")
        record.wait_for(state="visible")
        record.click()
        page.get_by_role("status").get_by_text("Recording ·", exact=False).wait_for()
        page.wait_for_timeout(750)
        record.click()
        attachment = page.locator("#mediaItems audio.media-audio")
        attachment.wait_for()
        assert page.locator(".media-name").inner_text().startswith("recording-")
        assert page.evaluate("() => selectedMedia[0].file.type") == "audio/wav"
        assert page.evaluate("() => selectedMedia[0].file.size <= 20 * 1024 * 1024")
        assert not errors, errors
        context.close()
        browser.close()
