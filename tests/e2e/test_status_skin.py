"""Status colors and labels in both skins, using synthetic model and job state."""

import pytest

sync_api = pytest.importorskip("playwright.sync_api")


@pytest.mark.parametrize("skin", ["green", "orange"])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_status_skin(ui, tmp_path, skin, theme):
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000}, reduced_motion="reduce")

        def synthetic_status(route):
            response = route.fetch()
            status = response.json()
            status["models"] = [
                {
                    "id": "synthetic/model",
                    "state": "ready",
                    "health": {"reachable": True, "running": 1, "queued": 0},
                    "inflight": [{"source": "playground", "seconds": 1}],
                    "options": {},
                    "entry": {},
                }
            ]
            status["jobs"] = {"batch": {"running": True, "model": "synthetic/model"}}
            route.fulfill(response=response, json=status)

        page.add_init_script(
            f"localStorage.setItem('basal-ui.skin', '{skin}'); localStorage.setItem('basal-ui.theme', '{theme}');"
        )
        page.route("**/api/status", synthetic_status)
        page.goto(ui.url)
        page.wait_for_function("() => statusCache && modelData.length && typeof renderStatus === 'function'")
        page.add_style_tag(content=".status-dot { transition: none !important; animation: none !important; }")
        page.evaluate(
            """([skin, theme]) => {
              StudioShell.applySkin(skin); applyTheme(theme);
              const s = structuredClone(statusCache);
              s.models = [{id:'synthetic/model', state:'ready', health:{reachable:true,running:1,queued:0},
                           inflight:[{source:'playground',seconds:1}], options:{}, entry:{}}];
              s.jobs = {batch:{running:true,model:'synthetic/model'}};
              document.querySelector('[data-view="manager"]').click();
              statusSequence++; refreshStatus=async()=>{}; window.syntheticRender=renderStatus; renderStatus(s);
              renderStatus=()=>{}; clearTimeout(pollTimer);
              document.querySelector('#status .section-head').insertAdjacentHTML('beforeend',
                calibrationChip({calibration:'validated'}, {}) + calibrationChip({calibration:'unvalidated'}, {}) +
                '<span class="badge fit-too_large">wrong</span>');
            }""",
            [skin, theme],
        )
        sync_api.expect(page.locator("#footerState")).to_contain_text("answering")
        sync_api.expect(page.locator("#inferenceNow")).to_contain_text("running")
        sync_api.expect(page.locator("#loadedModels .state-pill")).to_have_text("ready · running")
        sync_api.expect(page.locator("#status .fit-fits")).to_have_text("calibrated")
        ratios = page.evaluate(
            """() => {
              const rgb = value => value.match(/[\\d.]+/g).slice(0,3).map(Number);
              const luminance = c => rgb(c).map(x => {x/=255; return x<=.04045?x/12.92:((x+.055)/1.055)**2.4})
                .reduce((a,x,i)=>a+x*[.2126,.7152,.0722][i],0);
              const bg = el => {while(el) {const c=getComputedStyle(el).backgroundColor;
                if(c!=='rgba(0, 0, 0, 0)' && c!=='transparent') return c; el=el.parentElement;}};
              const ratio = (a,b) => {const x=luminance(a), y=luminance(b); return (Math.max(x,y)+.05)/(Math.min(x,y)+.05)};
              return [...document.querySelectorAll('#footerDot, #inferenceNow .status-dot.active, #loadedModels .status-dot.ready, #status .badge')]
                .map(el=>({name:el.className, fg:getComputedStyle(el).backgroundColor, bg:bg(el.parentElement), ratio:ratio(el.matches('.status-dot')?getComputedStyle(el).backgroundColor:getComputedStyle(el).color,
                  el.matches('.status-dot')?bg(el.parentElement):bg(el))}));
            }"""
        )
        assert len(ratios) == 7
        for item in ratios:
            assert item["ratio"] >= (3 if "status-dot" in item["name"] else 4.5), item
        color = ratios[0]["fg"]
        assert "ready" in ratios[0]["name"]
        assert (
            color
            == {
                ("green", "light"): "rgb(50, 100, 80)",
                ("green", "dark"): "rgb(163, 211, 181)",
                ("orange", "light"): "rgb(165, 71, 39)",
                ("orange", "dark"): "rgb(239, 176, 139)",
            }[(skin, theme)]
        )
        page.screenshot(path=str(tmp_path / f"{skin}-{theme}.png"), animations="disabled")
        page.evaluate("""() => {const s=structuredClone(statusCache); s.models=[{id:'synthetic/model',state:'error'}];
          s.jobs={}; window.syntheticRender(s); clearTimeout(pollTimer)}""")
        sync_api.expect(page.locator("#footerState")).to_have_text("error")
        assert "error" in page.locator("#footerDot").get_attribute("class")
        page.evaluate("""() => {const s=structuredClone(statusCache);
          s.models.push({id:'synthetic/loading',state:'starting'}); window.syntheticRender(s); clearTimeout(pollTimer)}""")
        sync_api.expect(page.locator("#footerState")).to_have_text("error · loading…")
        assert "error" in page.locator("#footerDot").get_attribute("class")
        browser.close()
