"""The pages of basal-ui in Chromium (Playwright, basal[e2e]): every view renders without a script error, the
Playground builds, sends and renders every question type, the load options dialog, Batch, History, Logs and Inspect."""

import json
import re
from pathlib import Path

import pytest

from tests.e2e.support import CHOICE, EXAMPLES, UI, advanced_requests

sync_api = pytest.importorskip("playwright.sync_api", reason="install basal[e2e] and run: playwright install chromium")

VIEWS = {
    "dashboard": "Dashboard",
    "overview": "Hardware",
    "models": "Models",
    "manager": "Model manager",
    "playground": "Playground",
    "history": "History",
    "backends": "Backends",
    "batch": "Batch",
    "inspect": "Inspect",
    "api": "API server",
    "logs": "Logs",
}
MANAGER_TABS = ("library", "usage", "benchmarks", "calibration")
WAIT = 120_000  # ms: a decision of a large model, a model start


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def page(browser, ui, text_model):
    """A fresh tab on basal-ui with the text model loaded; fails the test on any uncaught script error."""
    ui.load(text_model["id"])
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    tab = context.new_page()
    errors = []
    tab.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    tab.on(
        "console",
        lambda m: (
            errors.append(f"console: {m.text}")
            if m.type == "error" and "Failed to load resource" not in m.text
            else None
        ),
    )  # 4xx answers the page expects are not errors
    tab.set_default_timeout(WAIT)
    tab.goto(ui.url + "/")
    tab.wait_for_function("() => typeof statusCache !== 'undefined' && statusCache && modelData.length > 0")
    yield tab
    context.close()
    assert not errors, errors


def view(page, name):
    destination = page.locator(f'.side-nav [data-view="{name}"]')
    if not destination.is_visible():
        disclosure = page.locator(".nav-tools")
        if not disclosure.locator("summary").evaluate("(el) => el.parentElement.open"):
            disclosure.locator("summary").click()
    destination.click()
    sync_api.expect(page.locator(f'section.view[data-page="{name}"]').first).to_be_visible()
    assert not page.locator(".nav-tools").evaluate("(el) => el.open")


def open_request_settings(page):
    disclosure = page.locator("details.request-settings")
    if not disclosure.evaluate("(el) => el.open"):
        disclosure.locator("summary").click()


def open_appearance_controls(page):
    disclosure = page.locator(".appearance-controls details")
    if not disclosure.evaluate("(el) => el.open"):
        disclosure.locator("summary").click()


def manager_tab(page, name):
    view(page, "manager")
    tab = page.locator(f'[data-manager-tab="{name}"]')
    tab.click()
    sync_api.expect(tab).to_have_attribute("aria-selected", "true")
    sync_api.expect(page.locator(f"#manager-{name}")).to_be_visible()
    for other in MANAGER_TABS:
        if other != name:
            sync_api.expect(page.locator(f"#manager-{other}")).to_be_hidden()


@pytest.fixture
def isolated_ui(tmp_path):
    app = UI(tmp_path).start()
    yield app
    left = app.stop()
    assert not left, f"basal-serve children left running after Dashboard E2E: {left}"


def test_dashboard_default_records_real_decisions_and_resets_model_traffic(browser, isolated_ui, text_model):
    model_id = text_model["id"]
    isolated_ui.load(model_id)
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    page.set_default_timeout(WAIT)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(isolated_ui.url + "/")
        page.wait_for_function("() => typeof statusCache !== 'undefined' && statusCache && modelData.length > 0")
        sync_api.expect(page.locator("#dashboard")).to_be_visible()
        before = isolated_ui.get("/api/traffic", params={"minutes": 15})
        assert before["requests"] == before["input_tokens"] == 0
        assert isolated_ui.get("/api/stats")["models"].get(model_id) is None

        view(page, "playground")
        page.select_option("#playgroundModel", model_id)
        page.evaluate("request => setRequest(request)", CHOICE)
        with page.expect_response(lambda response: response.url.endswith("/api/decide"), timeout=WAIT) as decision:
            page.click("#sendButton")
        assert decision.value.status == 200, decision.value.text()
        sync_api.expect(page.locator("#results article.answer")).to_be_visible()

        view(page, "dashboard")
        page.wait_for_function(
            r"() => Number(document.querySelector('#traffic-requests-note').textContent.match(/\d+/)?.[0]) > 0",
            timeout=WAIT,
        )
        traffic = isolated_ui.get("/api/traffic", params={"minutes": 15})
        stats = isolated_ui.get("/api/stats")["models"][model_id]
        assert traffic["requests"] == 1 and traffic["input_tokens"] > 0
        assert traffic["requests_per_s"] > 0 and traffic["input_tokens_per_s"] > 0
        assert len(traffic["endpoints"]) == 1
        endpoint = traffic["endpoints"][0]
        assert (endpoint["endpoint"], endpoint["method"], endpoint["requests"], endpoint["errors"]) == (
            "/api/decide",
            "POST",
            1,
            0,
        )
        assert re.search(r"[1-9]\d*(?:\.\d+)?|0\.\d*[1-9]", page.locator("#traffic-requests").inner_text())
        assert re.search(r"[1-9]\d*(?:\.\d+)?|0\.\d*[1-9]", page.locator("#traffic-tokens").inner_text())
        endpoint_row = page.locator("#trafficEndpoints tr").filter(has_text="/api/decide")
        sync_api.expect(endpoint_row).to_be_visible()
        sync_api.expect(endpoint_row).to_contain_text("POST")
        sync_api.expect(endpoint_row).to_contain_text("1")

        page.select_option("#trafficWindow", "60")
        sync_api.expect(page.locator("#traffic-requests")).not_to_have_text("—", timeout=WAIT)
        sync_api.expect(page.locator("#traffic-requests-note")).to_contain_text("1")
        sync_api.expect(endpoint_row).to_be_visible()
        page.locator('[data-traffic-metric="requests_per_s"]').click()
        sync_api.expect(page.locator('[data-traffic-metric="requests_per_s"]')).to_have_attribute(
            "aria-pressed", "true"
        )

        manager_tab(page, "usage")
        reset_control = page.locator(f'#statsTable [data-reset="{model_id}"]')
        sync_api.expect(reset_control).to_be_visible()
        model_row = reset_control.locator("xpath=ancestor::tr")
        for value in (stats["requests"], stats["input_tokens"]):
            sync_api.expect(model_row).to_contain_text(str(value))
        view(page, "dashboard")
        dashboard_models = page.locator("#trafficModels")
        sync_api.expect(dashboard_models).to_be_visible()
        for value in (stats["requests"], stats["input_tokens"]):
            sync_api.expect(dashboard_models).to_contain_text(str(value))

        model_article = dashboard_models.locator(f'.traffic-model:has(strong[title="{model_id}"])')
        sync_api.expect(model_article.locator(".traffic-model-requests")).to_have_text(str(stats["requests"]))
        sync_api.expect(model_article.locator(".traffic-model-tokens")).to_have_text(str(stats["input_tokens"]))

        isolated_ui.delete(f"/api/stats/{model_id}")
        assert isolated_ui.get("/api/stats")["models"].get(model_id) is None
        assert isolated_ui.get("/api/traffic", params={"minutes": 15})["requests"] == 0
        page.wait_for_function(
            r"() => Number(document.querySelector('#traffic-requests-note').textContent.match(/\d+/)?.[0]) === 0",
            timeout=WAIT,
        )
        sync_api.expect(page.locator("#trafficEndpoints")).not_to_contain_text("/api/decide")
        assert not errors, errors
    finally:
        context.close()


def test_dashboard_unavailable_stats_and_status_recover(browser, isolated_ui, text_model):
    model_id = text_model["id"]
    isolated_ui.load(model_id)
    context = browser.new_context(viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    page.set_default_timeout(WAIT)
    try:
        page.goto(isolated_ui.url + "/")
        page.wait_for_function("() => typeof statusCache !== 'undefined' && statusCache && modelData.length > 0")
        view(page, "playground")
        page.select_option("#playgroundModel", model_id)
        page.evaluate("request => setRequest(request)", CHOICE)
        with page.expect_response(lambda response: response.url.endswith("/api/decide"), timeout=WAIT) as decision:
            page.click("#sendButton")
        assert decision.value.status == 200, decision.value.text()
        stats = isolated_ui.get("/api/stats")["models"][model_id]
        assert stats["requests"] > 0 and stats["input_tokens"] > 0

        def unavailable_stats(route):
            if route.request.method == "GET":
                route.fulfill(status=503, content_type="application/json", body='{"detail":"temporarily unavailable"}')
            else:
                route.continue_()

        view(page, "dashboard")
        page.route("**/api/stats", unavailable_stats)
        with page.expect_response(
            lambda response: response.url.endswith("/api/stats") and response.status == 503,
            timeout=WAIT,
        ):
            page.reload()
        page.wait_for_function("() => typeof statusCache !== 'undefined' && statusCache && modelData.length > 0")
        sync_api.expect(page.locator(".traffic-model-requests")).to_have_text("—")
        sync_api.expect(page.locator(".traffic-model-tokens")).to_have_text("—")
        sync_api.expect(page.locator("#traffic-requests")).not_to_have_text("—", timeout=WAIT)

        page.unroute("**/api/stats", unavailable_stats)
        page.reload()
        page.wait_for_function(
            """model => {
              const requests = document.querySelector('.traffic-model-requests');
              const tokens = document.querySelector('.traffic-model-tokens');
              return requests && tokens && requests.textContent.trim() !== '—' &&
                tokens.textContent.trim() !== '—' && document.querySelector('.traffic-model strong')?.title === model;
            }""",
            arg=model_id,
        )
        model_article = page.locator(f'.traffic-model:has(strong[title="{model_id}"])')
        sync_api.expect(model_article.locator(".traffic-model-requests")).to_have_text(str(stats["requests"]))
        sync_api.expect(model_article.locator(".traffic-model-tokens")).to_have_text(str(stats["input_tokens"]))

        def unavailable_status(route):
            if route.request.method == "GET":
                route.fulfill(status=503, content_type="application/json", body='{"detail":"temporarily unavailable"}')
            else:
                route.continue_()

        page.route("**/api/status", unavailable_status)
        with page.expect_response(
            lambda response: response.url.endswith("/api/status") and response.status == 503,
            timeout=WAIT,
        ):
            page.evaluate("() => refreshStatus()")
        page.wait_for_function(
            """() => document.querySelector('#trafficModels .traffic-model') === null &&
              document.querySelector('#trafficActivityCounts').textContent.trim() === '' &&
              ['#traffic-tokens','#traffic-requests','#traffic-latency','#traffic-errors']
                .every(selector => document.querySelector(selector).textContent.trim() === '—')"""
        )

        page.unroute("**/api/status", unavailable_status)
        page.evaluate("() => refreshStatus()")
        page.wait_for_function(
            """model => {
              const article = [...document.querySelectorAll('#trafficModels .traffic-model')]
                .find(el => el.querySelector('strong')?.title === model);
              return article !== undefined;
            }""",
            arg=model_id,
        )
    finally:
        context.close()


def test_manager_tab_routes_follow_click_keyboard_reload_and_back(page):
    manager_tab(page, "library")
    benchmarks = page.locator('[data-manager-tab="benchmarks"]')
    benchmarks.click()
    sync_api.expect(page.locator("#manager-benchmarks")).to_be_visible()
    sync_api.expect(page).to_have_url(re.compile(r"#manager/benchmarks$"))

    benchmarks.focus()
    page.keyboard.press("ArrowRight")
    calibration = page.locator('[data-manager-tab="calibration"]')
    sync_api.expect(calibration).to_have_attribute("aria-selected", "true")
    sync_api.expect(page.locator("#manager-calibration")).to_be_visible()
    sync_api.expect(page).to_have_url(re.compile(r"#manager/calibration$"))

    page.reload()
    sync_api.expect(page.locator('[data-manager-tab="calibration"]')).to_have_attribute("aria-selected", "true")
    sync_api.expect(page.locator("#manager-calibration")).to_be_visible()

    page.go_back()
    sync_api.expect(page.locator('[data-manager-tab="benchmarks"]')).to_have_attribute("aria-selected", "true")
    sync_api.expect(page.locator("#manager-benchmarks")).to_be_visible()
    sync_api.expect(page).to_have_url(re.compile(r"#manager/benchmarks$"))


def test_every_view_renders(page, text_model):
    sync_api.expect(page.locator("#dashboard")).to_be_visible()
    for name in VIEWS:
        view(page, name)
    for name in MANAGER_TABS:
        manager_tab(page, name)
    manager_tab(page, "library")
    sync_api.expect(page.locator("#loadedModels")).to_contain_text(text_model["id"])
    sync_api.expect(page.locator("#footerState")).to_contain_text("ready")


def test_visible_ui_text_meets_wcag_contrast_in_both_themes(page):
    def low_contrast():
        return page.evaluate("""() => {
          const rgb = value => {
            const m = value.match(/[\\d.]+/g);
            return m ? m.slice(0,3).map(Number) : null;
          };
          const alpha = value => value.startsWith('rgba(') ? Number(value.match(/,\\s*([\\d.]+)\\s*\\)$/)?.[1] ?? 1) : 1;
          const luminance = c => {
            const s = c.map(v => v / 255).map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4);
            return .2126*s[0] + .7152*s[1] + .0722*s[2];
          };
          const visible = el => el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
          const targets = [...document.querySelectorAll('button,.badge,.chip,[role="tab"],.side-nav button,[role="alert"],.banner')];
          return targets.filter(el => visible(el) && !(el instanceof HTMLButtonElement && el.disabled) && el.textContent.trim()).flatMap(el => {
            const style = getComputedStyle(el), fg = rgb(style.color);
            const ancestors=[];for(let node=el;node;node=node.parentElement)ancestors.unshift(node);
            let bg=[255,255,255], opacity=1;
            for(const node of ancestors){
              const computed=getComputedStyle(node), color=computed.backgroundColor, a=alpha(color), c=rgb(color);
              if(c)bg=c.map((v,i)=>v*a+bg[i]*(1-a));
              opacity*=Number(computed.opacity);
            }
            if (!fg) return [];
            const displayed=fg.map((v,i)=>v*opacity+bg[i]*(1-opacity));
            const ratio = (Math.max(luminance(displayed), luminance(bg)) + .05) / (Math.min(luminance(displayed), luminance(bg)) + .05);
            const size = parseFloat(style.fontSize), bold = parseInt(style.fontWeight, 10) >= 700;
            const required = size >= 24 || (bold && size >= 18.66) ? 3 : 4.5;
            return ratio + .01 < required ? [{text: el.textContent.trim().slice(0,60), ratio, required, fg: style.color, bg}] : [];
          });
        }""")

    for theme in ("light", "dark"):
        appearance = page.locator(".appearance-controls")
        choice = appearance.locator(f'[data-theme-choice="{theme}"]')
        if not choice.is_visible():
            appearance.locator("summary").click()
        choice.click()
        page.wait_for_timeout(300)
        for name in VIEWS:
            view(page, name)
            assert not low_contrast(), f"{theme} {name}: {low_contrast()}"
            if name == "models":
                page.click("#allModelsTab")
                assert not low_contrast(), f"{theme} models/all: {low_contrast()}"
        for name in MANAGER_TABS:
            manager_tab(page, name)
            assert not low_contrast(), f"{theme} manager/{name}: {low_contrast()}"
    page.evaluate("() => applyTheme('system')")


@pytest.mark.parametrize("request_", advanced_requests(), ids=lambda r: r["id"])
def test_the_playground_builds_sends_and_renders_each_question_type(page, text_model, request_):
    view(page, "playground")
    page.select_option("#playgroundModel", text_model["id"])
    index = page.evaluate("id => examples.findIndex(e => e.file === 'advanced' && e.id === id)", request_["id"])
    page.select_option("#examples", str(index))
    built = page.evaluate("() => buildRequest()")
    assert built["state"] == request_["state"]
    assert built["questions"] == request_["questions"]  # exactly the request: the builder never rewrites it
    page.click("#sendButton")
    page.wait_for_selector("#results article.answer")
    sync_api.expect(page.locator("#decideError")).to_be_hidden()
    articles = page.locator("#results article.answer")
    sync_api.expect(articles).to_have_count(len(request_["questions"]))
    q_type = next(iter(request_["questions"].values()))["type"]
    sync_api.expect(articles.first.locator("h3 .chip").first).to_have_text(q_type)
    if q_type in ("compress", "extract"):
        sync_api.expect(page.locator("#results .source-view mark").first).to_be_visible()
    if q_type == "verify":
        sync_api.expect(articles.first.locator("tbody tr")).to_have_count(3)


def test_playground_request_names_media_and_history_replay_are_lossless_until_send(page, text_model):
    view(page, "playground")
    page.evaluate("() => { addQuestion(); addQuestion(); }")
    assert page.evaluate("() => [...document.querySelectorAll('.q-name')].slice(-2).map(x => x.value)") == [
        "question2",
        "question3",
    ]

    page.evaluate("() => { document.querySelector('.q-name').value = '__proto__'; }")
    assert page.evaluate("() => Object.hasOwn(buildRequest().questions, '__proto__')")

    request = {
        "model": text_model["id"],
        "state": "inspect",
        "questions": {"q": {"type": "choice", "instructions": "Choose", "criteria": {"a": None, "b": None}}},
        "media": [{"type": "image", "data": "aGVsbG8="}],
        "detail": "high",
    }
    page.evaluate("r => setRequest(r)", request)
    assert page.evaluate("() => selectedModel") == text_model["id"]
    assert page.evaluate("() => [selectedMedia.length, $('#mediaDetail').value, buildRequest().media[0].data]") == [
        1,
        "high",
        "aGVsbG8=",
    ]
    unsupported = {**request, "vendor_extension": {"keep": True}}
    page.evaluate("r => setRequest(r)", unsupported)
    assert page.locator("#editJsonToggle").is_checked()
    sync_api.expect(page.locator("#mediaError")).to_be_visible()
    assert page.evaluate("() => JSON.parse($('#requestJson').value).vendor_extension") == {"keep": True}
    assert page.evaluate("() => selectedMedia.length") == 0
    for unsupported_value in (
        {"media": "opaque"},
        {"media": [{"type": "image", "data": "aGVsbG8=", "name": "x.png"}]},
        {"lang": "fr"},
    ):
        page.evaluate("([base,extra]) => setRequest({...base,...extra})", [request, unsupported_value])
        assert page.locator("#editJsonToggle").is_checked()
        assert page.evaluate("() => JSON.parse($('#requestJson').value)") == {**request, **unsupported_value}

    page.evaluate(
        "r => setRequest(r)",
        {"state": "inspect", "questions": {"q": request["questions"]["q"], "q2": request["questions"]["q"]}},
    )
    page.locator(".q-name").first.fill("same")
    page.locator(".q-name").nth(1).fill("same")
    decide_calls = []

    def reject_decision(route):
        decide_calls.append(1)
        route.fulfill(status=500, body="unexpected send")

    page.route("**/api/decide", reject_decision)
    page.click("#sendButton")
    sync_api.expect(page.locator("#decideError")).to_contain_text("non-blank and unique")
    assert page.locator(".q-name:invalid").count() == 1
    assert not decide_calls

    page.locator(".q-name").nth(1).fill("")
    page.click("#sendButton")
    sync_api.expect(page.locator("#decideError")).to_contain_text("non-blank and unique")
    assert not decide_calls
    preset_calls = []

    def count_presets(route):
        if route.request.method == "PUT":
            preset_calls.append(route.request.method)
        route.continue_()

    page.route("**/api/presets", count_presets)
    page.click("#saveRequestPreset")
    page.fill("#presetName", "invalid")
    page.click("#presetNameSave")
    assert "PUT" not in preset_calls

    page.evaluate(
        "([model,r]) => replayRequest({model,replay_request:r,replay_error:null})",
        [text_model["id"], {"state": "replayed", "questions": {"q": request["questions"]["q"]}}],
    )
    assert page.locator("#stateInput").input_value() == "replayed"
    assert page.locator("#results article.answer").count() == 0
    assert not decide_calls


def test_history_preserves_data_fields_through_recording_and_browser_replay(page, ui, text_model):
    """PR #4: recording and Replay must not rewrite ordinary fields named data."""
    request = {
        "state": {"data": "The customer reports a privacy leak.", "ticket": "e2e-data-field"},
        "questions": {
            "q": {
                "type": "choice",
                "instructions": "Choose the kind of problem.",
                "criteria": {"data": "A data or privacy problem", "other": "Other"},
            }
        },
    }
    view(page, "playground")
    page.evaluate("request => setRequest(request)", request)
    with page.expect_response(lambda response: "/api/decide" in response.url, timeout=WAIT) as sent:
        page.click("#sendButton")
    assert sent.value.status == 200
    assert sent.value.json()["answers"]["q"]["choice"] in request["questions"]["q"]["criteria"]

    def latest_record():
        items = ui.get(
            "/api/history", params={"model": text_model["id"], "source": "playground", "q": "e2e-data-field"}
        )["items"]
        return ui.get(f"/api/history/{items[0]['id']}")

    recorded = latest_record()
    assert recorded["request"]["state"] == request["state"]
    assert recorded["request"]["questions"] == request["questions"]
    assert recorded["replay_error"] is None
    assert recorded["replay_request"]["questions"] == request["questions"]

    view(page, "history")
    row = page.locator(f'tr[data-history="{recorded["id"]}"]')
    row.locator(".history-open").click()
    page.locator(f'tr[data-history="{recorded["id"]}"] + .history-detail .replay-request').click()
    sync_api.expect(page.locator('section.view[data-page="playground"]')).to_be_visible()
    replay = page.evaluate("() => $('#editJsonToggle').checked ? JSON.parse($('#requestJson').value) : buildRequest()")
    assert replay["state"] == request["state"]
    assert replay["questions"] == request["questions"]
    with page.expect_response(lambda response: "/api/decide" in response.url, timeout=WAIT) as resent:
        page.click("#sendButton")
    assert resent.value.status == 200
    assert latest_record()["request"]["questions"] == request["questions"]


def test_server_messages_translate_in_polish_and_remain_english(page):
    page.evaluate("localStorage.setItem('basal-ui.lang', 'pl')")
    page.reload()
    page.wait_for_function("() => typeof statusCache !== 'undefined' && modelData.length > 0")
    fixture = page.evaluate("""async () => {
      const data = await fetch('/api/models').then(r => r.json());
      const source = data.models.find(m => m.runnable);
      let unavailable = data.models.find(m => !m.runnable && m.reason_code &&
        ['basal-1.0', 'basal-1.5'].includes(m.family));
      if (!unavailable) {
        unavailable = {...source, id: '__e2e_older_unavailable__', family: 'basal-1.5', port: 'GGUF',
          label: 'GGUF Q8_0', runnable: false,
          reason: 'this GGUF conversion ships no tokenizer or calibration and its model card names no base model',
          reason_code: 'gguf_base', reason_params: {}};
        data.models.push(unavailable);
      }
      const packed = {...source, id: '__e2e_packed__', family: 'basal-2.0', tier: 'mini',
        label: 'MLX 8-bit', runnable: true};
      const multimodal = {...source, id: '__e2e_multimodal__', family: 'basal-omni-1.0',
        tier: 'mini', runnable: true};
      data.models.push(packed, multimodal);
      const packedParams = {tier: 'mini', label: 'MLX 8-bit', estimate: 3.7, memory: 6.4};
      const packedText = 'mini in MLX 8-bit: needs about 3.7 GB of 6.4 GB available; about 1.5x faster with --mlx-packed (Load options)';
      const multimodalParams = {kinds: ['image', 'audio'], mode: 'eager'};
      const multimodalText = 'images and audio, served with --mode eager';
      data.suggestions.push(
        {slot: 'recommended', id: packed.id, why: packedText, why_code: 'suggest_packed', why_params: packedParams},
        {slot: 'multimodal', id: multimodal.id, why: multimodalText,
          why_code: 'suggest_multimodal', why_params: multimodalParams});
      renderModels(data);
      return {data, unavailable,
              suggestion: data.suggestions.find(s => s.why_code),
              packed: data.suggestions.find(s => s.id === packed.id),
              multimodal: data.suggestions.find(s => s.id === multimodal.id)};
    }""")
    unavailable, suggestion = fixture["unavailable"], fixture["suggestion"]
    assert unavailable and suggestion
    assert unavailable["family"] in {"basal-1.0", "basal-1.5"}
    view(page, "models")
    page.click("#allModelsTab")
    page.check("#notRunnableToggle")
    page.check("#olderToggle")
    translated_reason = page.evaluate("m => ts(m.reason, m.reason_code, m.reason_params)", unavailable)
    assert translated_reason != unavailable["reason"]
    reason = page.locator("#modelsTable .reason").filter(has_text=translated_reason)
    sync_api.expect(reason.first).to_be_visible()
    sync_api.expect(reason.first).to_have_text(translated_reason)

    page.click("#suggestedTab")
    translated_why = page.evaluate("s => ts(s.why, s.why_code, s.why_params)", suggestion)
    assert translated_why != suggestion["why"]
    sync_api.expect(page.locator("#suggested .why").filter(has_text=translated_why).first).to_be_visible()
    for key in ("packed", "multimodal"):
        card = fixture[key]
        translated = page.evaluate("s => ts(s.why, s.why_code, s.why_params)", card)
        assert translated != card["why"]
        sync_api.expect(page.locator("#suggested .why").filter(has_text=translated).first).to_be_visible()
    assert "obrazy i audio" in page.evaluate("s => ts(s.why, s.why_code, s.why_params)", fixture["multimodal"])
    assert "--mlx-packed" in page.evaluate("s => ts(s.why, s.why_code, s.why_params)", fixture["packed"])
    assert (
        page.evaluate("""() => ts('mini in bf16: needs about — of a 8.6 GB budget',
      'suggest_recommended_budget', {tier: 'mini', label: 'bf16', estimate: null, memory: 8.6})""")
        == "mini w formacie bf16: potrzeba około — z budżetu 8.6 GB"
    )

    options_target = page.evaluate("""async () => {
      for (const m of modelData.filter(x => x.runnable)) {
        const data = await fetch(`/api/models/options?id=${encodeURIComponent(m.id)}`).then(r => r.json());
        const mode = data.modes.find(x => x.reason_code);
        if (mode) return {id: m.id, mode, text: ts(mode.reason, mode.reason_code, mode.reason_params)};
      }
      return null;
    }""")
    assert options_target
    page.click("#allModelsTab")
    page.locator(f'#modelsTable .options-model[data-id="{options_target["id"]}"]').click()
    sync_api.expect(page.locator("#loadOptions")).to_be_visible()
    sync_api.expect(
        page.locator("#loadOptions .option-mode.unavailable small").filter(has_text=options_target["text"]).first
    ).to_be_visible()
    assert options_target["text"] != options_target["mode"]["reason"]

    page.evaluate("localStorage.setItem('basal-ui.lang', 'en')")
    page.reload()
    page.wait_for_function("() => typeof statusCache !== 'undefined' && modelData.length > 0")
    page.evaluate("data => renderModels(data)", fixture["data"])
    view(page, "models")
    page.click("#allModelsTab")
    page.check("#notRunnableToggle")
    page.check("#olderToggle")
    sync_api.expect(page.locator("#modelsTable .reason").filter(has_text=unavailable["reason"]).first).to_be_visible()
    page.click("#suggestedTab")
    sync_api.expect(page.locator("#suggested .why").filter(has_text=suggestion["why"]).first).to_be_visible()
    page.click("#allModelsTab")
    page.locator(f'#modelsTable .options-model[data-id="{options_target["id"]}"]').click()
    sync_api.expect(
        page.locator("#loadOptions .option-mode.unavailable small")
        .filter(has_text=options_target["mode"]["reason"])
        .first
    ).to_be_visible()


UNTRANSLATED = """() => {  // visible text and labels that are a key of the Polish dictionary, shown in English
  const shown = el => el && el.getClientRects().length > 0;
  const english = s => Object.hasOwn(PL, s) && PL[s] !== s;
  const out = new Set(), walk = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n; (n = walk.nextNode());) { const s = n.nodeValue.trim(); if (s && shown(n.parentElement) && english(s)) out.add(s); }
  for (const a of ['title', 'placeholder', 'aria-label'])
    for (const el of document.querySelectorAll(`[${a}]`)) { const s = el.getAttribute(a).trim(); if (shown(el) && english(s)) out.add(`@${a}: ${s}`); }
  return [...out];
}"""


def test_polish_ui_leaves_no_frontend_text_in_english(page, text_model):
    page.evaluate("localStorage.setItem('basal-ui.lang', 'pl')")
    page.reload()
    page.wait_for_function("() => typeof statusCache !== 'undefined' && modelData.length > 0")
    assert page.evaluate("() => [document.documentElement.lang, LANG]") == ["pl", "pl"]
    left = {}
    appearance = page.locator(".appearance-controls")
    appearance.locator("summary").click()
    left["appearance"] = page.evaluate(UNTRANSLATED)
    appearance.locator("summary").click()
    disclosure = page.locator(".nav-tools")
    if not disclosure.evaluate("(el) => el.open"):
        disclosure.locator("summary").click()
    left["Tools navigation"] = page.evaluate(UNTRANSLATED)
    disclosure.locator("summary").click()
    for name in VIEWS:
        view(page, name)
        page.wait_for_timeout(300)
        if name == "history":
            page.evaluate("""() => renderHistory(['mcp','api','playground'].map((source,i) => ({
              id:'stub'+i,ts:1,model:'Remek/basal-2.0-mini',source,state_preview:'test',
              answers:{},latency_ms:1,status:'ok'
            })))""")
        left[name] = page.evaluate(UNTRANSLATED)
        if name == "models":
            for tab in ("#suggestedTab", "#allModelsTab"):
                page.click(tab)
                left[f"models {tab}"] = page.evaluate(UNTRANSLATED)
    for tab in MANAGER_TABS:
        manager_tab(page, tab)
        page.wait_for_timeout(300)
        if tab == "benchmarks":
            page.evaluate("""() => {
              statsData.benchmarks = {stub: [{kind:'modes', model:'Remek/basal-2.0-mini',
                set:'bundled', when:1, modes:[{mode:'eager',dec_s:2}]}]};
              renderBenchmarkResults();
            }""")
        left[f"manager {tab}"] = page.evaluate(UNTRANSLATED)
    view(page, "models")
    page.click("#allModelsTab")
    page.locator(f'#modelsTable .options-model[data-id="{text_model["id"]}"]').click()
    sync_api.expect(page.locator("#loadOptions")).to_be_visible()
    left["load options"] = page.evaluate(UNTRANSLATED)
    page.click("#optionsCancel")
    page.evaluate("id => openModelCard(id)", text_model["id"])
    sync_api.expect(page.locator("#modelCardDialog")).to_be_visible()
    left["model card"] = page.evaluate(UNTRANSLATED)
    assert not {key: value for key, value in left.items() if value}, left


def test_model_manager_disk_rows_stack_without_overflow_at_mobile_width(page, text_model):
    page.set_viewport_size({"width": 390, "height": 844})
    view(page, "manager")
    rows = page.locator("#diskModels .disk-row")
    sync_api.expect(rows.filter(has=page.locator(f'[data-id="{text_model["id"]}"]'))).to_be_visible()
    for index in range(rows.count()):
        row = rows.nth(index)
        assert row.locator(".disk-info").evaluate("(el) => el.getBoundingClientRect().width") > 100
        assert row.locator(".disk-actions").evaluate("""el => {
          const row = el.closest('.disk-row'), a = el.getBoundingClientRect(), b = row.getBoundingClientRect();
          return a.left >= b.left && a.right <= b.right;
        }""")


def test_media_example_loads_with_a_notice_and_draws_element_probabilities(page, text_model):
    view(page, "playground")
    page.select_option("#playgroundModel", text_model["id"])
    index = page.evaluate("() => examples.findIndex(e => e.id === 'media-01-checkout-elements')")
    page.select_option("#examples", str(index))
    sync_api.expect(page.locator("#mediaError")).to_contain_text("Load with media")  # a text model takes no images
    assert page.evaluate("() => selectedMedia.map(m => m.file.name)") == ["checkout.png"]
    sync_api.expect(page.locator("#mediaField")).to_be_visible()
    sync_api.expect(page.locator("#mediaInput")).to_be_disabled()
    decide_calls = []
    page.route("**/api/decide", lambda route: (decide_calls.append(1), route.fulfill(status=500, body="sent")))
    page.click("#sendButton")
    sync_api.expect(page.locator("#decideError")).to_contain_text("does not take images")  # refused before sending
    assert not decide_calls
    page.evaluate("() => { loadedMedia = ['image', 'audio']; }")  # as on a media model: evidence is what is refused
    open_request_settings(page)

    page.check("#editJsonToggle")
    page.evaluate("""() => { const r = JSON.parse($('#requestJson').value); r.questions.click_target.evidence = true;
      $('#requestJson').value = JSON.stringify(r); }""")
    page.click("#sendButton")
    sync_api.expect(page.locator("#decideError")).to_contain_text('turn off "Evidence spans" on click_target')
    assert not decide_calls
    page.evaluate("() => { loadedMedia = []; }")
    page.evaluate("""() => renderResults({answers: {click_target: {type: 'choice', choice: '1',
      probabilities: {'1': 0.7, '2': 0.2, '3': 0.05, '4': 0.05}}}}, buildRequest())""")
    overlay = page.locator("#results .image-source svg")
    assert overlay.locator("rect").count() == 4
    assert "checkout.png" in overlay.get_attribute("aria-label")
    sync_api.expect(overlay.locator("text").first).to_have_text("1 · 70.0%")
    page.select_option(
        "#examples", str(page.evaluate("() => examples.findIndex(e => e.id === 'media-04-voice-complaint')"))
    )
    sync_api.expect(page.locator("#mediaItems audio.media-audio")).to_have_count(1)
    assert page.evaluate("() => selectedMedia.map(m => m.file.name)") == ["complaint_pl.wav"]


RUBRIC = {
    "state": "Od wczoraj nie mogę zalogować się do aplikacji.",
    "questions": {
        "category": {
            "type": "classify",
            "option_keys": "show",
            "labels": [{"name": "bug", "rubric": "błąd aplikacji"}, {"name": "billing"}],
        },
        "dept": {"type": "choice", "instructions": "Który dział?", "criteria": ["it", "sales"]},
    },
}


def test_the_builder_keeps_what_it_cannot_write_in_its_own_form(page):
    view(page, "playground")
    page.evaluate("r => setRequest(r)", RUBRIC)
    assert page.evaluate("() => buildRequest().questions") == RUBRIC["questions"]
    cards = page.locator(".question")
    cards.nth(0).locator(".q-instructions-input").fill("Jaka to kategoria?")  # an edit elsewhere on the card
    cards.nth(1).locator(".q-instructions-input").fill("Do którego działu?")
    built = page.evaluate("() => buildRequest().questions")
    assert built["category"]["labels"] == RUBRIC["questions"]["category"]["labels"]  # still {name, rubric} objects
    assert built["category"]["option_keys"] == "show" and built["category"]["instructions"] == "Jaka to kategoria?"
    assert built["dept"]["criteria"] == ["it", "sales"] and "option_keys" not in built["dept"]
    cards.nth(0).locator('[data-f="labels"]').fill("bug: błąd aplikacji\nbilling: płatności\nother")
    assert page.evaluate("() => buildRequest().questions.category.labels") == [
        {"name": "bug", "rubric": "błąd aplikacji"},
        {"name": "billing", "rubric": "płatności"},
        {"name": "other"},
    ]


def test_no_automation_claim_where_the_certificate_does_not_hold(page, text_model):
    """A media answer (calibration fitted on text) or one with unvalidated calibration is never "Automatable"."""
    view(page, "playground")
    probe = """([model, calibration]) => {
      const m = statusCache.models.find(x => x.id === model);
      const saved = m.entry; m.entry = {...saved, certified: {"0.01": {choice: 0.5}, "0.05": {choice: 0.4}},
                                        temperature_per_prim: {choice: 1.2}};
      renderResults({answers: {q: {type: 'choice', choice: 'a', probabilities: {a: 0.99, b: 0.01}, confidence: 0.99,
                                   ...(calibration ? {calibration} : {})}}},
                    {model, state: 's', questions: {q: {type: 'choice', criteria: {a: 'A', b: 'B'}}}});
      m.entry = saved;
      return document.querySelector('#results .chips.safety').textContent;
    }"""
    for calibration in ("text", "unvalidated"):
        strip = page.evaluate(probe, [text_model["id"], calibration])
        assert "Automatable" not in strip and "Not certified for this answer" in strip
    probe = """([model, answer, warning]) => {
      const m=statusCache.models.find(x=>x.id===model), saved=m.entry;
      m.entry={...saved,certified:{"0.01":{choice:0.5},"0.05":{choice:0.4}},temperature_per_prim:{choice:1.2},
               ...(warning?{calibration_warning:warning}:{})};
      renderResults({answers:{q:{type:'choice',choice:'a',probabilities:{a:0.99,b:0.01},confidence:0.99,...answer}}},
        {model,state:'s',questions:{q:{type:'choice',criteria:{a:'A',b:'B'}}}});
      m.entry=saved; return document.querySelector('#results .chips.safety').textContent;
    }"""
    for answer, warning in (({"deferred": True}, None), ({"max_error": "0.01"}, None), ({}, "missing calibration")):
        strip = page.evaluate(probe, [text_model["id"], answer, warning])
        assert "Automatable" not in strip and "review" in strip


def test_string_state_is_preserved_until_the_state_field_is_edited(page):
    view(page, "playground")
    literal = '{"not": "json"}'
    page.evaluate("state => setRequest({state, questions: {}})", literal)
    assert page.evaluate("() => buildRequest().state") == literal
    page.evaluate("() => setRequest({state: {structured: true}, questions: {}})")
    assert page.evaluate("() => buildRequest().state") == {"structured": True}
    page.evaluate("state => setRequest({state, questions: {}})", literal)
    page.fill("#stateInput", '{"edited": true}')
    assert page.evaluate("() => buildRequest().state") == {"edited": True}


def test_edit_json_rebuilds_the_builder_and_keeps_invalid_json_open(page):
    view(page, "playground")
    open_request_settings(page)

    page.check("#editJsonToggle")
    page.fill("#requestJson", "{ invalid")
    page.click("#editJsonToggle")  # stays checked: the toggle refuses to leave invalid JSON
    sync_api.expect(page.locator("#editJsonToggle")).to_be_checked()
    sync_api.expect(page.locator("#editJsonArea")).to_be_visible()
    sync_api.expect(page.locator("#mediaError")).to_contain_text("Invalid request JSON")
    request = {
        "state": "builder from edited JSON",
        "questions": {"priority": {"type": "choice", "criteria": ["low", "high"]}},
        "facts": "auto",
        "lang": "pl",
    }
    page.fill("#requestJson", json.dumps(request))
    page.uncheck("#editJsonToggle")
    sync_api.expect(page.locator("#builder")).to_be_visible()
    assert page.evaluate("() => buildRequest()")["questions"] == request["questions"]
    assert page.evaluate("() => buildRequest().state") == request["state"]


def test_the_builder_switches_types_and_edits_a_cost_matrix(page):
    view(page, "playground")
    page.evaluate("() => setRequest({state: 'Paczka nie dotarła.', questions: {}})")
    page.evaluate("() => addQuestion()")
    card = page.locator(".question").first
    card.locator(".q-type").select_option("act")
    card.locator('[data-f="criteria"]').fill("late: opóźnienie\nlost: zaginięcie")
    card.locator('[data-cost-mode="matrix"]').click()
    sync_api.expect(card.locator(".cost-table thead th")).to_have_text(["Action", "late", "lost", ""])
    card.locator(".add-action").click()
    row = card.locator(".cost-table tbody tr").last
    row.locator(".cost-action").fill("human")
    for cell in row.locator(".cost-cell").all():
        cell.fill("2")
    costs = page.evaluate("() => buildRequest().questions.question.costs")
    assert costs["human"] == {"late": 2, "lost": 2} and costs["defer"] == {"late": 1, "lost": 1}
    cell = row.locator(".cost-cell").first
    cell.fill("")
    assert page.evaluate("() => document.querySelector('.question')._error") == "every cost cell needs a number"
    costs = page.evaluate("() => buildRequest().questions.question.costs")
    assert "late" not in costs["human"]
    sync_api.expect(card.locator(".q-error")).to_contain_text("every cost cell needs a number")
    card.locator(".q-type").select_option("tool")
    card.locator('[data-f="tools"]').fill("not json")
    page.click("#sendButton")
    sync_api.expect(page.locator("#decideError")).to_contain_text("tools must be a JSON array")


def test_safety_strip_and_inspect_button_on_a_decision(page, ui, text_model):
    view(page, "playground")
    page.evaluate("r => setRequest(r)", CHOICE)
    page.click("#sendButton")
    strip = page.locator("#results .chips.safety")
    sync_api.expect(strip.first).to_contain_text(
        re.compile("Automatable|Below the certified|No certificate|uncalibrated|calibrated")
    )
    sync_api.expect(page.locator("#inspectDecision")).to_be_visible()
    if "context" in (ui.model(text_model["id"])["entry"] or {}):  # the meter needs the engine's context policy
        sync_api.expect(page.locator("#results")).to_contain_text("Longest prompt")


def test_the_load_options_dialog_lists_the_modes(page, text_model):
    manager_tab(page, "library")  # the loaded model's restart options
    page.click(f'#loadedModels .options-model[data-id="{text_model["id"]}"]')
    dialog = page.locator("#loadOptions")
    sync_api.expect(dialog).to_be_visible()
    sync_api.expect(page.locator("#optionsApply")).to_have_text("Restart")
    sync_api.expect(page.locator("#optionsSetupNote")).to_be_hidden()
    sync_api.expect(dialog.locator(f'input[name="optMode"][value="{text_model["mode"]}"]')).to_be_checked()
    assert dialog.locator('input[name="optMode"]').count() == 16
    assert dialog.locator('input[name="optMode"][disabled]').count() >= 1
    sync_api.expect(dialog.locator("#optExperimentalNote")).not_to_be_empty()
    for key, modes in (("gemma_attn", "fast"), ("eager_attn", "eager"), ("merge", "fast"), ("ple_offload", "eager")):
        field = dialog.locator(f'[data-opt="{key}"]')  # each switch shows for the backend that reads it
        assert modes in field.get_attribute("data-modes").split() and (
            "eager" in field.get_attribute("data-modes").split()
        ) == (modes == "eager")
        if text_model["mode"] not in field.get_attribute("data-modes").split():
            sync_api.expect(field).to_be_hidden()
    page.click("#optionsCancel")
    sync_api.expect(dialog).to_be_hidden()


def test_models_set_up_without_loading_and_the_model_manager_loads(page, ui, text_model):
    view(page, "manager")  # the fixture loaded the model: its row on disk offers Unload
    row = page.locator("#diskModels .disk-row", has=page.locator(f'[data-id="{text_model["id"]}"]'))
    sync_api.expect(row.locator(".unload-model")).to_be_visible()
    view(page, "models")
    page.click("#allModelsTab")
    loads = []
    page.route("**/api/load", lambda route: (loads.append(1), route.fulfill(status=500, body="loaded")))
    page.click(f'#modelsTable .options-model[data-action="save"][data-id="{text_model["id"]}"]')
    sync_api.expect(page.locator("#optionsApply")).to_have_text("Save")
    sync_api.expect(page.locator("#optionsSetupNote")).to_be_visible()
    sync_api.expect(page.locator("#optionsSetupNote")).to_contain_text("Media loads choose their mode")
    sync_api.expect(page.locator("#optionsRemember")).to_be_hidden()
    page.fill("#opt-revision", "main")
    page.click("#optionsApply")
    sync_api.expect(page.locator("#loadOptions")).to_be_hidden()
    try:
        saved = ui.get("/api/models/options", params={"id": text_model["id"]})
        assert saved["saved"] and saved["options"]["revision"] == "main" and not loads
    finally:
        ui.put("/api/models/options", {"id": text_model["id"], "options": {}})


def test_models_tabs_and_manager_loaded_section(page, text_model):
    manager_tab(page, "library")
    sync_api.expect(page.locator("#loadedModels")).to_contain_text(text_model["id"])
    for tab in MANAGER_TABS:
        manager_tab(page, tab)
    view(page, "models")
    assert page.locator('#models [role="tab"]').all_text_contents() == ["Suggested", "All models"]
    sync_api.expect(page.locator("#suggestedTab")).to_have_attribute("aria-selected", "true")
    page.evaluate("localStorage.setItem('basal-ui.tab', 'loaded')")
    page.reload()
    page.wait_for_function("() => typeof statusCache !== 'undefined' && statusCache && modelData.length > 0")
    view(page, "models")
    sync_api.expect(page.locator("#suggestedTab")).to_have_attribute("aria-selected", "true")


def test_batch_view_runs_a_file_and_lists_the_answers(page, text_model, tmp_path):
    view(page, "batch")
    page.select_option("#batchModel", text_model["id"])
    page.fill("#batchInput", str(EXAMPLES / "questions.jsonl"))
    page.fill("#batchOutput", str(tmp_path / "answers.jsonl"))
    page.fill("#batchConcurrency", "4")
    page.click("#runBatch")
    sync_api.expect(page.locator("#downloadBatch")).to_be_visible()
    sync_api.expect(page.locator("#batchRows tbody tr")).to_have_count(20)
    sync_api.expect(page.locator("#batchSummary")).to_contain_text("20 items")


# the language buttons reload the page: until the new page's scripts run, LANG and statusCache do not exist
SWITCHED = """lang => typeof LANG !== 'undefined' && typeof statusCache !== 'undefined' && LANG === lang
  && !sessionStorage.getItem('basal-ui.language-draft') && statusCache && modelData.length > 0"""


def test_playground_builder_draft_survives_language_changes_once(page, text_model):
    view(page, "playground")
    request = {
        "model": text_model["id"],
        "state": "Review this order",
        "facts": "auto",
        "lang": "en",
        "questions": {
            "decision": {"type": "choice", "instructions": "What next?", "criteria": {"buy": "Buy", "wait": "Wait"}},
            "risk": {"type": "noul", "instructions": "Is it risky?"},
        },
        "media": [{"type": "image", "data": "aGVsbG8="}],
    }
    page.evaluate("r => setRequest(r)", request)
    open_appearance_controls(page)

    page.locator('[data-lang-choice="pl"]').click()
    page.wait_for_function(SWITCHED, arg="pl")
    assert page.evaluate(
        "() => [selectedModel, $('#stateInput').value, $('#factsToggle').checked, $('#langSelect').value]"
    ) == [text_model["id"], "Review this order", True, "en"]
    assert page.evaluate(
        "() => JSON.parse(buildRequest().questions.decision.criteria ? JSON.stringify(buildRequest().questions.decision.criteria) : '{}')"
    ) == {"buy": "Buy", "wait": "Wait"}
    assert page.evaluate("() => selectedMedia.length") == 0
    sync_api.expect(page.locator("#mediaError")).to_contain_text("dołącz media ponownie")
    open_appearance_controls(page)

    page.locator('[data-lang-choice="en"]').click()
    page.wait_for_function(SWITCHED, arg="en")
    assert page.evaluate(
        "() => [selectedModel, $('#stateInput').value, $('#factsToggle').checked, $('#langSelect').value]"
    ) == [text_model["id"], "Review this order", True, "en"]
    assert page.evaluate("() => buildRequest().questions.decision.criteria") == {"buy": "Buy", "wait": "Wait"}
    assert page.evaluate("() => Object.keys(buildRequest().questions)") == ["decision", "risk"]
    assert page.evaluate("() => selectedMedia.length") == 0
    page.reload()
    page.wait_for_function("() => statusCache && modelData.length > 0")
    sync_api.expect(page.locator("#stateInput")).to_have_value("")


def test_playground_edit_json_draft_keeps_invalid_text(page):
    view(page, "playground")
    open_request_settings(page)

    page.check("#editJsonToggle")
    text = '{"state": "unfinished", "questions": ['
    page.fill("#requestJson", text)
    open_appearance_controls(page)

    page.locator('[data-lang-choice="pl"]').click()
    page.wait_for_function(SWITCHED, arg="pl")
    sync_api.expect(page.locator("#requestJson")).to_have_value(text)
    assert page.locator("#editJsonToggle").is_checked()
    page.reload()
    page.wait_for_function("() => statusCache && modelData.length > 0")
    assert not page.locator("#editJsonToggle").is_checked()
    assert "unfinished" not in page.locator("#requestJson").input_value()
    sync_api.expect(page.locator("#builder")).to_be_visible()


@pytest.mark.parametrize("draft_case", ["blank-name", "duplicate-names", "invalid-tools", "invalid-schema"])
def test_playground_incomplete_builder_draft_survives_language_switch_once(page, draft_case):
    view(page, "playground")
    if draft_case == "blank-name":
        page.evaluate(
            "() => { $('#questions').innerHTML = ''; addQuestion('first', {type:'choice', criteria:{yes:'Yes'}}); $('.q-name').value = ''; }"
        )
    elif draft_case == "duplicate-names":
        page.evaluate(
            "() => { $('#questions').innerHTML = ''; addQuestion('same', {type:'choice', criteria:{yes:'Yes'}}); addQuestion('same', {type:'noul'}); }"
        )
    elif draft_case == "invalid-tools":
        page.evaluate(
            "() => { $('#questions').innerHTML = ''; addQuestion('tools', {type:'tool', tools:[{name:'lookup'}]}); }"
        )
        page.fill('.question [data-f="tools"]', '{"unfinished":')
    else:
        page.evaluate(
            "() => { $('#questions').innerHTML = ''; addQuestion('schema', {type:'extract', schema:{type:'object', properties:{}}}); }"
        )
        page.fill('.question [data-f="schema"]', '{"unfinished":')

    open_appearance_controls(page)

    page.locator('[data-lang-choice="pl"]').click()
    page.wait_for_function(SWITCHED, arg="pl")
    if draft_case == "blank-name":
        sync_api.expect(page.locator(".question .q-name")).to_have_value("")
    elif draft_case == "duplicate-names":
        assert page.locator(".question .q-name").evaluate_all("(els) => els.map(el => el.value)") == ["same", "same"]
    elif draft_case == "invalid-tools":
        sync_api.expect(page.locator('.question [data-f="tools"]')).to_have_value('{"unfinished":')
    else:
        sync_api.expect(page.locator('.question [data-f="schema"]')).to_have_value('{"unfinished":')

    page.reload()
    page.wait_for_function("() => statusCache && modelData.length > 0")
    sync_api.expect(page.locator("#stateInput")).to_have_value("")
    assert page.locator(".question .q-name").evaluate_all("(els) => els.map(el => el.value)") == ["question"]
    assert not page.locator("#mediaError").is_visible()


def test_playground_language_draft_preserves_blank_act_matrix_rows(page):
    view(page, "playground")
    page.evaluate(
        "() => { $('#questions').innerHTML = ''; addQuestion('decision', {type:'act', criteria:{buy:'Buy', wait:'Wait'}, costs:{wrong:10}}); }"
    )
    page.locator('.question [data-cost-mode="matrix"]').click()
    page.locator(".question .cost-action").nth(0).fill("")
    page.locator(".question .cost-action").nth(1).fill("custom")
    page.locator(".question tbody tr").nth(1).locator(".cost-cell").nth(0).fill("17")
    page.locator(".question tbody tr").nth(1).locator(".cost-cell").nth(1).fill("23")

    open_appearance_controls(page)
    page.locator('[data-lang-choice="pl"]').click()
    page.wait_for_function(SWITCHED, arg="pl")
    assert page.locator(".question .cost-action").evaluate_all("(els) => els.map(el => el.value)") == ["", "custom"]
    assert page.locator(".question tbody tr").evaluate_all(
        "(rows) => rows.map(row => [...row.querySelectorAll('.cost-cell')].map(cell => cell.value))"
    ) == [["0", "10"], ["17", "23"]]


def test_history_and_logs_views(page, ui, text_model):
    ui.decide(text_model["id"], CHOICE)
    view(page, "history")
    rows = page.locator("#historyTable tr.history-row")
    sync_api.expect(rows.first).to_be_visible()
    rows.first.click()
    sync_api.expect(page.locator("#historyTable .history-detail .history-request")).to_contain_text('"questions"')
    view(page, "logs")
    sync_api.expect(page.locator("#logSources")).to_contain_text(text_model["id"].split("/")[-1])


def test_inspect_view_embeds_basal_viz_and_explains_a_playground_decision(page, ui, bf16_model):
    view(page, "inspect")
    page.select_option("#inspectModel", bf16_model["id"])
    page.click("#startInspect")
    frame_el = page.locator("#vizFrame")
    sync_api.expect(frame_el).to_be_visible(timeout=600_000)
    frame = page.frame_locator("#vizFrame")
    sync_api.expect(frame.locator("#modelchip")).to_contain_text(bf16_model["id"])
    view(page, "playground")
    request = {**CHOICE, "state": "Umowa podpisana 3 marca 2025.", "facts": "auto", "lang": "pl"}
    page.evaluate("r => setRequest(r)", request)
    page.click("#sendButton")
    page.click("#inspectDecision")
    sync_api.expect(page.locator("#pageTitle")).to_have_text("Inspect")
    sync_api.expect(frame.locator("#out .result").first).to_be_visible()
    sync_api.expect(frame.locator("#out")).to_contain_text("Do którego działu")
    sync_api.expect(frame.locator("#out .result").first).to_contain_text("Polish prompt")
    sync_api.expect(frame.locator("#out .result .prompt")).to_contain_text("Fakty pomocnicze")
    notice = page.locator("#inspectNotice")
    sync_api.expect(notice).to_be_visible()
    if page.input_value("#playgroundModel") != bf16_model["id"]:  # the served decision came from other weights
        sync_api.expect(notice).to_contain_text("weights: served")
    page.click("#stopInspect")
    sync_api.expect(frame_el).to_be_hidden()
    assert ui.get("/api/inspect")["state"] == "idle"


def test_the_inspect_handoff_names_every_difference_and_refuses_what_it_cannot_explain(page, text_model):
    """inspectHandoff on a served model whose engine, option orders and temperatures differ from the inspector's."""
    probe = """([model, request]) => {
      const saved = statusCache, served = statusCache.models.find(x => x.id === model);
      statusCache = {...saved, inspect: {model, state: 'ready', mode: 'eager', device: 'mps', orders: 1,
                                         temperatures: {choice: 9}},
                     models: [{...served, mode: 'mlx', options: {think: true},
                               entry: {...served.entry, orders: 2, temperature_per_prim: {choice: 1.2}}}]};
      try { return inspectHandoff({model, ...request}); } finally { statusCache = saved; }
    }"""
    mixed = {
        "state": "s",
        "think": "auto",
        "early_exit": "0.99",
        "facts": "auto",
        "questions": {
            "dept": {"type": "choice", "criteria": {"a": "A", "b": "B"}},
            "intent": {"type": "classify", "labels": {"x": "X", "y": "Y"}},
        },
    }
    handoff = page.evaluate(probe, [text_model["id"], mixed])
    assert list(handoff["body"]["questions"]) == ["dept"] and handoff["body"]["facts"] == "auto"
    assert not {"model", "think", "early_exit"} & set(handoff["body"])
    notes = " | ".join(handoff["notes"])
    for part in (
        "intent: task questions are left out",
        "engine: served mlx, explained eager on mps",
        "option orders: served 2, explained 1",
        "calibration: the served temperatures differ",
        "thinking: explained without the reasoning",
        "early exit 0.99",
    ):
        assert part in notes, notes
    assert (
        "remove the media to inspect this request"
        in page.evaluate(probe, [text_model["id"], {**mixed, "media": [{"type": "image"}]}])["blocked"]
    )
    only_tasks = {"state": "s", "questions": {"intent": mixed["questions"]["intent"]}}
    assert "only task questions" in page.evaluate(probe, [text_model["id"], only_tasks])["blocked"]
    other = page.evaluate(
        """([model, request]) => {
      const saved = statusCache; statusCache = {...saved, inspect: {model: 'Remek/basal-1.0-max', state: 'ready'}};
      try { return inspectHandoff({model, ...request}); } finally { statusCache = saved; }
    }""",
        [text_model["id"], mixed],
    )
    assert "the inspector runs Remek/basal-1.0-max" in other["blocked"]


def test_the_tool_builder_sets_the_selection_of_several_tools(page):
    view(page, "playground")
    tools = [{"name": "refund", "description": "zwrot"}, {"name": "track", "description": "śledzenie"}]
    request = {
        "state": "Chcę zwrotu i numeru przesyłki.",
        "questions": {
            "next": {
                "type": "tool",
                "tools": tools,
                "multi": True,
                "objective": "f1",
                "min": 1,
                "max": 2,
                "exclusive": ["refund"],
                "fallback": "track",
                "fallback_threshold": 0.3,
            }
        },
    }
    page.evaluate("r => setRequest(r)", request)
    assert page.evaluate("() => buildRequest().questions") == request["questions"]
    card = page.locator(".question").first
    sync_api.expect(card.locator(".multi-tool-only")).to_be_visible()
    card.locator('[data-f="max"]').fill("1")
    card.locator('[data-f="objective"]').select_option("threshold")
    card.locator('[data-f="threshold"]').fill("0.6")
    built = page.evaluate("() => buildRequest().questions.next")
    assert built["max"] == 1 and built["threshold"] == 0.6 and "objective" not in built
    assert built["exclusive"] == ["refund"] and built["fallback"] == "track"
    card.locator('[data-f="multi"]').uncheck()
    sync_api.expect(card.locator(".multi-tool-only")).to_be_hidden()
    built = page.evaluate("() => buildRequest().questions.next")
    assert not {"multi", "min", "max", "exclusive", "fallback", "threshold"} & set(built)


def test_the_calibration_view_imports_a_file_fitted_elsewhere(page, ui, text_model, tmp_path):
    shipped = ui.get("/api/calibrations", params={"id": text_model["id"]})["shipped"]["file"]
    manager_tab(page, "calibration")
    page.select_option("#calibrationModel", text_model["id"])
    page.fill("#calibrationImportPath", shipped)
    sync_api.expect(page.locator("#calibrationFiles table")).to_be_visible()
    before = page.locator("#calibrationFiles .chip", has_text="imported").count()
    page.click("#importCalibration")
    sync_api.expect(page.locator("#calibrationImportPath")).to_have_value("")  # done: the field is cleared
    sync_api.expect(page.locator("#calibrationFiles .chip", has_text="imported")).to_have_count(before + 1)
    row = page.locator("#calibrationFiles tbody tr", has=page.locator(".chip", has_text="imported")).first
    sync_api.expect(row.locator("td").nth(3)).to_contain_text("5%")  # the shipped file's certified thresholds
    page.fill("#calibrationImportPath", str(tmp_path / "none.json"))
    page.click("#importCalibration")
    sync_api.expect(page.locator(".toast.error").last).to_contain_text("cannot read")


def test_theme_toggle_persists_and_sets_the_document_theme(page):
    appearance = page.locator(".appearance-controls")
    if not appearance.locator('[data-theme-choice="dark"]').is_visible():
        appearance.locator("summary").click()
    appearance.locator('[data-theme-choice="dark"]').click()
    assert page.evaluate("() => [document.documentElement.dataset.theme, localStorage.getItem('basal-ui.theme')]") == [
        "dark",
        "dark",
    ]


def test_control_enter_sends_a_playground_decision(page):
    view(page, "playground")
    page.evaluate("""() => {
      api = async (path, options) => {
        window.sentDecision = path === '/api/decide' && options.method === 'POST';
        return {answers: {q: {type: 'choice', choice: 'yes'}}, usage: {}};
      };
      $('#editJsonToggle').checked = true;
      $('#requestJson').value = JSON.stringify({state: 'state', questions: {q: {type: 'choice', criteria: ['yes']}}});
    }""")
    page.locator("body").click(position={"x": 5, "y": 5})
    page.keyboard.press("Control+Enter")
    page.wait_for_function("() => window.sentDecision === true")


def test_question_mark_opens_shortcut_help(page):
    page.locator("body").click(position={"x": 5, "y": 5})
    page.keyboard.press("?")
    sync_api.expect(page.locator("#shortcutsDialog")).to_be_visible()
    sync_api.expect(page.locator("#shortcutsDialog")).to_contain_text("Ctrl/⌘ + Enter")


def test_alt_digit_opens_the_nth_page_by_key_code(page):
    page.locator("body").click(position={"x": 5, "y": 5})
    page.keyboard.press("Alt+2")
    sync_api.expect(page.locator("#pageTitle")).to_have_text("Models")
    # macOS Option+3 types "£" / "#": the key changes with the layout, the code does not
    page.evaluate("""() => document.body.dispatchEvent(new KeyboardEvent('keydown',
      {key: '£', code: 'Digit3', altKey: true, bubbles: true}))""")
    sync_api.expect(page.locator("#pageTitle")).to_have_text("Model manager")


CENTRED = """sel => {  // where an open modal dialog sits in the viewport
  const d = document.querySelector(sel), r = d.getBoundingClientRect();
  return {open: d.open, shown: r.width > 0 && r.height > 0,
          inside: r.left >= 0 && r.top >= 0 && r.right <= innerWidth + 0.5 && r.bottom <= innerHeight + 0.5,
          dx: Math.round((r.left + r.right) / 2 - innerWidth / 2), dy: Math.round((r.top + r.bottom) / 2 - innerHeight / 2)};
}"""
DIALOGS = {  # every modal of basal-ui and how it opens (without waiting on the user)
    "#confirmDialog": "() => { void confirmDialog({title: 'Delete?', description: 'x', confirmLabel: 'Delete'}); }",
    "#modelCardDialog": "id => openModelCard(id)",
    "#loadOptions": "id => openLoadOptions(id, false, 'save')",
    "#shortcutsDialog": "() => $('#shortcutsDialog').showModal()",
    "#presetNameDialog": "() => $('#presetNameDialog').showModal()",
    "#reportDialog": "() => { $('#reportText').textContent = 'report'; $('#reportDialog').showModal(); }",
    "#backendTestDialog": "() => $('#backendTestDialog').showModal()",
    "#mediaLightbox": "() => $('#mediaLightbox').showModal()",
}


@pytest.mark.parametrize("size", [(1440, 900), (390, 844)], ids=["desktop", "phone"])
def test_every_modal_opens_centred_in_the_viewport_from_any_view(page, text_model, size):
    page.set_viewport_size({"width": size[0], "height": size[1]})
    view(page, "overview")  # the calibration report lives in Model manager: it must show from any view
    for selector, open_it in DIALOGS.items():
        page.evaluate(open_it, text_model["id"])
        page.wait_for_function("s => document.querySelector(s).open", arg=selector)
        page.wait_for_timeout(250)  # the model card and load options fade in
        assert page.evaluate(CENTRED, selector) == {"open": True, "shown": True, "inside": True, "dx": 0, "dy": 0}, (
            selector
        )
        page.evaluate("s => document.querySelector(s).close()", selector)


DIALOG_LAYOUT = """([dialog, body, header, footer]) => {
  const d = document.querySelector(dialog), b = document.querySelector(body), r = d.getBoundingClientRect();
  const top = document.querySelector(header).getBoundingClientRect(), bottom = document.querySelector(footer).getBoundingClientRect();
  const before = [top.top, bottom.bottom]; b.scrollTop = b.scrollHeight;
  const after = [document.querySelector(header).getBoundingClientRect().top, document.querySelector(footer).getBoundingClientRect().bottom];
  return {inViewport: r.top >= 0 && r.bottom <= innerHeight && r.left >= 0 && r.right <= innerWidth,
          centred: Math.abs((r.left + r.right) / 2 - innerWidth / 2) < 2, dialogScrolls: d.scrollHeight > d.clientHeight + 1,
          bodyScrolls: b.scrollHeight > b.clientHeight && b.scrollTop > 0, fixedEnds: before.every((v, i) => Math.abs(v - after[i]) < 1),
          footerShown: bottom.bottom <= innerHeight};
}"""


@pytest.mark.parametrize("size", [(1440, 900), (390, 844)], ids=["desktop", "phone"])
def test_options_dialog_keeps_its_header_and_actions_while_the_options_scroll(page, text_model, size):
    page.set_viewport_size({"width": size[0], "height": size[1]})
    page.evaluate("id => openLoadOptions(id, false, 'save')", text_model["id"])
    sync_api.expect(page.locator("#loadOptions")).to_be_visible()
    page.evaluate(
        "() => document.querySelectorAll('#optionsForm details').forEach(d => { d.open = true; })"
    )  # all expanded
    page.wait_for_timeout(500)  # Basecoat animates a <details> opening
    layout = page.evaluate(
        DIALOG_LAYOUT, ["#loadOptions", "#optionsForm", "#loadOptions > h3", "#loadOptions .dialog-actions"]
    )
    assert layout == {
        "inViewport": True,
        "centred": True,
        "dialogScrolls": False,
        "bodyScrolls": True,
        "fixedEnds": True,
        "footerShown": True,
    }
    switches = [s.bounding_box() for s in page.locator("#optionsForm input[type=checkbox]:visible").all()]
    assert switches and all(s["height"] < 32 for s in switches)  # the compact field sizing leaves the switches alone
    page.click("#optionsCancel")
    page.evaluate("id => openLoadOptions(id, false, 'save')", text_model["id"])
    assert page.evaluate("() => $('#optionsForm').scrollTop") == 0  # a reopened dialog starts at the top


def test_model_card_keeps_its_header_while_the_card_scrolls(page, text_model):
    page.set_viewport_size({"width": 1440, "height": 700})
    page.evaluate("id => openModelCard(id)", text_model["id"])
    sync_api.expect(page.locator("#modelCardDialog")).to_be_visible()
    page.evaluate("() => document.querySelectorAll('#modelCardBody details').forEach(d => { d.open = true; })")
    page.wait_for_timeout(500)  # Basecoat animates a <details> opening
    layout = page.evaluate(
        DIALOG_LAYOUT, ["#modelCardDialog", "#modelCardBody", ".model-card-head", ".model-card-head"]
    )
    assert layout["inViewport"] and layout["centred"] and not layout["dialogScrolls"]
    assert layout["bodyScrolls"] and layout["fixedEnds"]
    title = page.locator("#modelCardTitle").inner_text()
    assert title.count("·") == 2 and title.split(" · ")[0] == text_model["family"]  # family · tier · format, once
    facts = page.locator(".model-card-facts dt").all_inner_texts()
    assert not {"Family", "Tier", "Port"} & set(facts)


OPEN_SECTIONS = "() => [...document.querySelectorAll('#optionsForm details.option-section')].filter(d => d.open).map(d => d.dataset.section)"


def test_options_sections_collapse_open_on_changes_and_remember_the_users_choice(page, text_model):
    page.evaluate("id => openLoadOptions(id, false, 'save')", text_model["id"])
    sync_api.expect(page.locator("#loadOptions")).to_be_visible()
    sections = page.evaluate(
        "() => [...document.querySelectorAll('#optionsForm details.option-section')].map(d => d.dataset.section)"
    )
    assert len(sections) >= 6 and "Batching" in sections
    assert page.evaluate(OPEN_SECTIONS) == ["Engine"]  # defaults: only the engine is open
    batch = page.locator('#optionsForm details[data-section="Batching"]')
    sync_api.expect(batch.locator("#opt-max_batch")).to_be_hidden()
    # a saved non-default value opens its section
    page.evaluate("() => renderOptionsForm({...optionsState.data.defaults, max_batch: 8})")
    assert page.evaluate(OPEN_SECTIONS) == ["Engine", "Batching"]
    # a collapsed section still saves what it holds
    page.evaluate("() => { $('#opt-max_batch').value = '12'; }")
    batch.locator("summary").click()  # collapse it
    sync_api.expect(batch.locator("#opt-max_batch")).to_be_hidden()
    assert page.evaluate("() => collectOptions().max_batch") == 12
    # the user's toggles are kept for the next dialog (also for another model), the defaults are not
    page.locator('#optionsForm details[data-section="Engine"] > summary').click()
    page.locator('#optionsForm details[data-section="Caching"] > summary').press("Enter")
    page.click("#optionsCancel")
    page.evaluate("id => openLoadOptions(id, false, 'save')", text_model["id"])
    assert page.evaluate(OPEN_SECTIONS) == ["Caching"]
    page.click("#optionsCancel")


def test_model_card_long_blocks_start_collapsed(page, text_model):
    page.evaluate("id => openModelCard(id)", text_model["id"])
    sync_api.expect(page.locator("#modelCardDialog")).to_be_visible()
    blocks = page.locator("#modelCardBody details")
    assert blocks.count() >= 1
    assert page.evaluate("() => [...document.querySelectorAll('#modelCardBody details')].every(d => !d.open)")
    sync_api.expect(blocks.first.locator("pre")).to_be_hidden()
    blocks.first.locator("summary").click()
    sync_api.expect(blocks.first.locator("pre")).to_be_visible()
    sync_api.expect(page.locator("#modelCardBody .model-card-facts")).to_be_visible()  # the facts stay in view


def test_model_card_dialog_renders_api_card_data(page):
    page.evaluate("""() => {
      api = async () => ({
        id: 'community/model', repo: 'community/model', url: 'https://huggingface.co/community/model',
        label: 'Model label', author: 'community', community: true, base_repo: 'base/model',
        family: 'basal-2.0', tier: 'mini', port: 'MLX', size_bytes: 1000, media: [],
        downloaded: true, revisions: ['abc'], local_dir: '/models/model', basal_json: {prompt_format: 'chat'},
        shipped_calibration: {thresholds: {'0.01': {choice: 0.9}}},
        assessment: {mode: 'mlx', runnable: true, fit: 'fits', fit_now: 'fits', est_memory_bytes: 2000},
        loaded: null, readme: 'A model README.'
      });
      openModelCard('community/model');
    }""")
    sync_api.expect(page.locator("#modelCardDialog")).to_contain_text("Model label")
    sync_api.expect(page.locator("#modelCardDialog")).to_contain_text("base/model")
    sync_api.expect(page.locator("#modelCardDialog")).to_contain_text("A model README.")


def test_result_views_drop_descriptions_that_only_repeat_the_key(page):
    criteria = {"88412": "88412", "4": "[4] Payment status", "7": "[7]", "9": "[9]x", "none": "Nie podano numeru"}
    page.evaluate(
        """c => renderResults({answers: {q: {type: 'choice', choice: '4', confidence: 0.6,
      probabilities: {'4': 0.6, '88412': 0.2, '7': 0.1, '9': 0.05, none: 0.05}}}},
      {questions: {q: {type: 'choice', criteria: c}}})""",
        criteria,
    )
    sync_api.expect(page.locator("#results .answer .headline")).to_have_text("4 · Payment status")
    bars = page.locator("#results .prob > span").all_inner_texts()
    assert bars == ["4 · Payment status", "88412", "7", "9 · [9]x", "none · Nie podano numeru"]
    assert "4 · [4]" not in page.locator("#results").inner_text()


ANSWERING = """([model, batch]) => {  // the status of this basal-ui with requests and a batch in flight (no polling)
  const s = structuredClone(statusCache), m = s.models.find(x => x.id === model);
  m.inflight = [{source: 'playground', seconds: 2.4}, {source: 'api', seconds: 0.3}];
  m.health = {reachable: true, running: 2, queued: 1};
  s.jobs = batch ? {batch: {kind: 'batch', running: true, model, phase: 'running', phase_code: 'phase_running',
                            phase_params: {}, done: 3, total: 10}} : {};
  renderStatus(s); clearTimeout(pollTimer);
}"""


def test_manager_library_shows_inference_in_flight_and_idle_otherwise(page, text_model):
    manager_tab(page, "library")
    sync_api.expect(page.locator("#inferenceNow")).to_be_visible()
    rows = page.locator("#inferenceNow .inference-row")
    sync_api.expect(rows).to_have_count(1)
    sync_api.expect(rows.first).to_contain_text("Idle")
    assert page.locator("#inferenceNow .status-dot.active").count() == 0
    page.evaluate(ANSWERING, [text_model["id"], True])
    sync_api.expect(rows).to_have_count(2)
    sync_api.expect(rows.nth(0)).to_contain_text(text_model["id"])
    sync_api.expect(rows.nth(0)).to_contain_text("2 running · 1 queued · Playground 1 · API server 1 · longest 2.4 s")
    sync_api.expect(rows.nth(1)).to_contain_text("Batch")
    sync_api.expect(rows.nth(1)).to_contain_text("3 / 10")
    assert page.locator("#inferenceNow .status-dot.active").count() == 2
    sync_api.expect(
        page.locator(f'#loadedModels .model-row:has-text("{text_model["id"]}") .status-dot.active')
    ).to_have_count(1)
    sync_api.expect(page.locator("#footerState")).to_contain_text("1 answering")
    assert "active" in page.locator("#footerDot").get_attribute("class")
    # basal-serve's counters not read yet (zeros) while basal-ui waits on requests: the requests are what is shown
    page.evaluate(
        """m => { const s = structuredClone(statusCache); const x = s.models.find(y => y.id === m);
      x.inflight = [{source: 'playground', seconds: 0.5}, {source: 'playground', seconds: 0.2}];
      x.health = {reachable: true, running: 0, queued: 0}; s.jobs = {}; renderStatus(s); clearTimeout(pollTimer); }""",
        text_model["id"],
    )
    sync_api.expect(rows).to_have_count(1)
    sync_api.expect(rows.first).to_contain_text("2 requests · Playground 2 · longest 0.5 s")
    assert "running" not in rows.first.inner_text()
    page.evaluate(
        """m => { const s = structuredClone(statusCache); const x = s.models.find(y => y.id === m);
      x.inflight = []; x.health = {reachable: true, running: 0, queued: 0}; s.jobs = {}; renderStatus(s); clearTimeout(pollTimer); }""",
        text_model["id"],
    )
    sync_api.expect(rows).to_have_count(1)
    sync_api.expect(rows.first).to_contain_text("Idle")
    assert page.locator(".status-dot.active").count() == 0
    assert "answering" not in page.locator("#footerState").inner_text()


def test_manager_inference_after_a_decision_and_in_polish(page, ui, text_model):
    ui.decide(text_model["id"], CHOICE)
    manager_tab(page, "library")
    sync_api.expect(page.locator("#inferenceNow")).to_contain_text("last decision")  # the idle line says when
    assert page.evaluate("() => !$('#manager-library').hidden")
    page.evaluate("localStorage.setItem('basal-ui.lang', 'pl')")
    page.reload()
    page.wait_for_function("() => typeof statusCache !== 'undefined' && statusCache?.models?.length > 0")
    manager_tab(page, "library")
    page.evaluate(ANSWERING, [text_model["id"], True])
    rows = page.locator("#inferenceNow .inference-row")
    sync_api.expect(rows.nth(0)).to_contain_text(
        "2 w toku · 1 w kolejce · Playground 1 · Serwer API 1 · najdłuższe 2.4 s"
    )
    sync_api.expect(page.locator("#inference .section-index")).to_have_text("WNIOSKOWANIE")


def test_a_running_batch_shows_in_manager_library(page, ui, text_model, tmp_path):
    many = tmp_path / "many.jsonl"
    many.write_text((EXAMPLES / "questions.jsonl").read_text() * 50)
    ui.post(
        "/api/batch",
        {"id": text_model["id"], "input": str(many), "output": str(tmp_path / "out.jsonl"), "concurrency": 1},
    )
    try:
        manager_tab(page, "library")
        row = page.locator("#inferenceNow .inference-row", has_text="Batch")
        sync_api.expect(row).to_be_visible(timeout=10_000)
        sync_api.expect(row).to_contain_text(" / 1000")  # done / total of the real job
    finally:
        ui.delete("/api/batch")
        ui.wait_job("batch")


def test_usage_line_includes_branches_and_lane(page):
    page.evaluate("""() => renderResults(
      {answers: {q: {type: 'choice', choice: 'yes'}},
       usage: {latency_ms: 1, branches: 3, second_order_branches: 2, lane: 'long'}},
      {model: selectedModel, questions: {q: {type: 'choice'}}}
    )""")
    line = page.locator("#results .answer-meta").first
    sync_api.expect(line).to_contain_text("branches: 3")
    sync_api.expect(line).to_contain_text("second_order_branches: 2")
    sync_api.expect(line).to_contain_text("lane: long")


def test_load_preset_save_and_apply_round_trip(page, text_model):
    manager_tab(page, "library")
    page.click(f'#loadedModels [data-id="{text_model["id"]}"].options-model')
    dialog = page.locator("#loadOptions")
    sync_api.expect(dialog).to_be_visible()
    page.fill("#opt-revision", "test-revision")
    page.click("#saveLoadPreset")
    page.fill("#presetName", "e2e-load-preset")
    page.click("#presetNameSave")
    sync_api.expect(dialog).to_contain_text("e2e-load-preset")
    page.select_option("#loadPresetSelect", "e2e-load-preset")
    page.fill("#opt-revision", "")
    page.click("#applyLoadPreset")
    sync_api.expect(page.locator("#opt-revision")).to_have_value("test-revision")
    page.click("#deleteLoadPreset")
    sync_api.expect(dialog.locator("#loadPresetSelect")).not_to_contain_text("e2e-load-preset")
    page.click("#optionsCancel")


@pytest.mark.parametrize("width", [1440, 390])
def test_dashboard_live_updates_preserve_scroll_and_copy_selection(page, ui, text_model, width):
    ui.post("/api/decide", {**CHOICE, "model": text_model["id"]})
    page.set_viewport_size({"width": width, "height": 700})
    view(page, "dashboard")
    endpoint = page.locator("#trafficEndpoints code").filter(has_text="/api/decide")
    sync_api.expect(endpoint).to_be_visible()
    endpoint.scroll_into_view_if_needed()
    endpoint.evaluate("""node => {
      const range = document.createRange(); range.selectNodeContents(node);
      const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
    }""")
    before = page.evaluate("scrollY")
    assert before > 100
    for _ in range(3):
        with page.expect_response(lambda response: "/api/traffic?" in response.url):
            pass
        assert abs(page.evaluate("scrollY") - before) < 3
        assert page.evaluate("window.getSelection().toString()") == "/api/decide"
    page.evaluate("window.getSelection().removeAllRanges()")


def test_downloaded_model_loads_from_dashboard_and_batch_without_losing_input(browser, isolated_ui, text_model):
    model_id = text_model["id"]
    context = browser.new_context(viewport={"width": 1280, "height": 800})
    page = context.new_page()
    page.set_default_timeout(WAIT)
    try:
        page.goto(isolated_ui.url)
        page.wait_for_function("() => typeof catalogData !== 'undefined' && catalogData && modelData.length")

        def load_here():
            page.click("#openModelLoader")
            row = page.locator(".model-loader-row").filter(has=page.get_by_text(model_id, exact=True))
            with page.expect_response(lambda response: response.url.endswith("/api/load")) as loaded:
                row.locator(".load-model:not([data-media])").click()
            assert loaded.value.status == 202
            page.wait_for_function(
                "id => statusCache.models.some(model => model.id === id && model.state === 'ready')",
                arg=model_id,
                timeout=WAIT,
            )
            row.locator(".use-loaded-model").click()
            sync_api.expect(page.locator("#modelLoaderDialog")).to_be_hidden()

        load_here()
        sync_api.expect(page.locator(f'#trafficModels strong[title="{model_id}"]')).to_be_visible()
        for name in ("models", "playground", "api", "backends"):
            view(page, name)
            page.click("#openModelLoader")
            row = page.locator(".model-loader-row").filter(has=page.get_by_text(model_id, exact=True))
            row.locator(".use-loaded-model").click()
            if name == "playground":
                assert page.input_value("#playgroundModel") == model_id
        for panel, selector in (("benchmarks", "#benchmarkModel"), ("calibration", "#calibrationModel")):
            manager_tab(page, panel)
            page.click("#openModelLoader")
            page.locator(".model-loader-row").filter(has=page.get_by_text(model_id, exact=True)).locator(
                ".use-loaded-model"
            ).click()
            assert page.input_value(selector) == model_id

        view(page, "inspect")
        page.click("#openModelLoader")
        if text_model["inspect_reason"]:
            assert not page.locator(".model-loader-row").filter(has=page.get_by_text(model_id, exact=True)).is_visible()
        page.locator('button[data-close="modelLoaderDialog"]').click()

        view(page, "batch")
        page.fill("#batchInput", "/tmp/preserved-requests.jsonl")
        isolated_ui.unload(model_id)
        page.wait_for_function("id => !statusCache.models.some(model => model.id === id)", arg=model_id)
        load_here()
        assert page.input_value("#batchModel") == model_id
        assert page.input_value("#batchInput") == "/tmp/preserved-requests.jsonl"
    finally:
        context.close()


def test_backend_recommendation_load_preserves_saved_revision_and_settings(browser, isolated_ui, text_model):
    backend = "mlx" if text_model["port"].startswith("MLX") else "torch"
    data = isolated_ui.get("/api/models", params={"backend": backend})
    downloaded = {model["id"] for model in data["models"] if model["downloaded"]}
    choice = next((item for item in data["suggestions"] if not item["media"] and item["id"] in downloaded), None)
    if choice is None:
        pytest.skip("No downloaded text recommendation for the model's installed Backend")
    model_id = choice["id"]
    isolated_ui.put("/api/models/options", {"id": model_id, "options": {"revision": "main", "orders": "1"}})
    before = isolated_ui.get("/api/models/options", params={"id": model_id})["options"]
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    page.set_default_timeout(WAIT)
    try:
        page.goto(isolated_ui.url + "/#models")
        page.wait_for_function("() => typeof catalogData !== 'undefined' && catalogData")
        page.select_option("#recommendationBackend", backend)
        page.wait_for_function("backend => catalogData.recommendation_backend === backend", arg=backend)
        page.click("#suggestedTab")
        page.locator(f'#suggested .load-model[data-id="{model_id}"]').first.click()
        sync_api.expect(page.locator("#loadOptions")).to_be_visible()
        assert page.input_value("#opt-revision") == "main"
        sync_api.expect(page.locator(f'input[name="optMode"][value="{choice["mode"]}"]')).to_be_checked()
        sync_api.expect(page.locator("#optionsRemember")).not_to_be_checked()
        page.click("#optionsApply")
        page.wait_for_function(
            "id => statusCache.models.some(model => model.id === id && model.state === 'ready')",
            arg=model_id,
            timeout=WAIT,
        )
        loaded = next(model for model in isolated_ui.get("/api/status")["models"] if model["id"] == model_id)
        assert loaded["mode"] == choice["mode"]
        assert loaded["options"]["revision"] == "main"
        assert loaded["options"]["orders"] == "1"
        assert isolated_ui.get("/api/models/options", params={"id": model_id})["options"] == before
    finally:
        context.close()


@pytest.mark.parametrize("with_options", [False, True])
def test_loading_from_batch_preserves_the_hidden_playground_media_draft(
    browser, isolated_ui, text_model, second_model, with_options
):
    isolated_ui.load(text_model["id"])
    context = browser.new_context(viewport={"width": 1280, "height": 900})
    page = context.new_page()
    page.set_default_timeout(WAIT)

    def previous_media_capability(route):
        # Exercise client-side draft transitions without claiming image inference from these text checkpoints.
        response = route.fetch()
        payload = response.json()
        for model in payload["models"]:
            if model["id"] == text_model["id"]:
                model["media"] = ["image"]
        route.fulfill(response=response, json=payload)

    page.route("**/api/status", previous_media_capability)
    try:
        page.goto(isolated_ui.url + "/#playground")
        page.wait_for_function("() => typeof catalogData !== 'undefined' && catalogData && !$('#mediaInput').disabled")
        page.evaluate("request => setRequest(request)", CHOICE)
        page.fill("#stateInput", "Preserve this Playground draft while loading in Batch.")
        page.set_input_files(
            "#mediaInput", str(Path(__file__).resolve().parents[2] / "basal_ui/examples/media/checkout.png")
        )
        sync_api.expect(page.locator(".media-item img")).to_be_visible()
        before = page.evaluate("buildRequest()")

        view(page, "batch")
        page.click("#openModelLoader")
        row = page.locator(".model-loader-row").filter(has=page.get_by_text(second_model["id"], exact=True))
        if with_options:
            row.locator(".options-model").click()
            page.click("#optionsApply")
        else:
            row.locator(".load-model:not([data-media])").click()
        page.wait_for_function(
            "id => statusCache.models.some(model => model.id === id && model.state === 'ready')",
            arg=second_model["id"],
            timeout=WAIT,
        )
        if with_options:
            page.click("#openModelLoader")
        row.locator(".use-loaded-model").click()

        view(page, "playground")
        sync_api.expect(page.locator(".media-item img")).to_be_visible()
        assert page.input_value("#stateInput") == before["state"]
        assert page.evaluate("buildRequest().media") == before["media"]
        assert page.evaluate("buildRequest().questions") == before["questions"]
        assert page.evaluate("loadedMedia") == []
        posted = []
        page.on(
            "request",
            lambda request: posted.append(request) if request.url.endswith("/api/decide") else None,
        )
        page.click("#sendButton")
        sync_api.expect(page.locator("#decideError")).to_be_visible()
        assert posted == []  # Preserved media must not be sent to a model without image support.
    finally:
        context.close()
