"""Inspect accessibility in Chromium using synthetic prompts and a pending explanation."""

import pytest

sync_api = pytest.importorskip("playwright.sync_api")


def test_inspect_visual_accessibility(ui, tmp_path):
    with sync_api.sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.goto(ui.url + "/viz/")
        page.wait_for_function("() => booted")
        page.evaluate("""() => {
          const order = {segments:[{kind:'template', text:'Synthetic prompt template'}],
            n_tokens:3, perm:[0,1], letters:['A','B']};
          const result = document.createElement('div'); result.className = 'result';
          result.append(promptView({orders:[order, order]}));
          result.insertAdjacentHTML('beforeend', `<div class="rhead"><span class="qname">Question</span></div>
            <div class="step"><div class="cmp"><span class="hdr">Comparison</span></div>
            <div class="chart"><svg><text x="0" y="12">Chart</text></svg></div>
            <div class="foot">Timing</div></div>`);
          document.querySelector('#out').replaceChildren(result);
          document.querySelector('#tip').innerHTML = '<div class="t">Tooltip</div>';
          document.querySelector('#tip').style.display = 'block';
          loadRequest({state:'Synthetic example', questions:{q:{type:'choice', instructions:'Choose',
            criteria:{a:'First', b:'Second'}}}});
        }""")
        audit = """() => {
          const lum = c => c.match(/[\\d.]+/g).slice(0,3).map(Number).map(x => x/255)
            .map(x => x <= .04045 ? x/12.92 : ((x+.055)/1.055)**2.4)
            .reduce((a,x,i) => a+x*[.2126,.7152,.0722][i],0);
          return [...document.querySelectorAll('.hint,.prompt .template,.rhead .qname,.cmp .hdr,.chart text,.foot,.tip .t')]
            .filter(e => e.getClientRects().length)
            .map(e => {let bg=e; while(bg && getComputedStyle(bg).backgroundColor === 'rgba(0, 0, 0, 0)') bg=bg.parentElement;
              const style=getComputedStyle(e), a=lum(e.tagName === 'text' ? style.fill : style.color),
                b=lum(getComputedStyle(bg).backgroundColor);
              return {text:e.textContent, ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05)};});
        }"""
        for theme in ("light", "dark"):
            page.evaluate("theme => document.documentElement.dataset.theme = theme", theme)
            ratios = page.evaluate(audit)
            assert ratios and all(item["ratio"] >= 4.5 for item in ratios), (theme, ratios)
            page.screenshot(path=str(tmp_path / f"inspect-{theme}.png"))
        page.emulate_media(reduced_motion="reduce")
        assert page.locator(".cursor").evaluate("e => getComputedStyle(e).animationName") == "none"
        page.evaluate("""() => { const original = window.fetch; window.fetch = (url, ...args) =>
          url === 'api/explain' ? new Promise(() => {}) : original(url, ...args); }""")
        page.locator("#go").click()
        sync_api.expect(page.locator(".loading")).to_contain_text("Running the model")
        assert page.locator(".spin").evaluate("e => getComputedStyle(e).display") == "none"
        page.screenshot(path=str(tmp_path / "inspect-reduced-loading.png"))
        page.locator(".result").evaluate("e => e.classList.remove('stale')")
        page.emulate_media(forced_colors="active")
        first, second = page.locator(".tabs button").all()
        first.focus()
        page.keyboard.press("Tab")
        assert second.evaluate("e => document.activeElement === e")
        page.keyboard.press("Enter")
        assert "on" in (second.get_attribute("class") or "")
        assert second.evaluate("e => parseFloat(getComputedStyle(e).borderTopWidth)") >= 2
        assert first.evaluate("e => parseFloat(getComputedStyle(e).borderTopWidth)") == 0
        assert second.evaluate("e => getComputedStyle(e).outlineStyle") != "none"
        first.focus()
        page.screenshot(path=str(tmp_path / "inspect-forced-colors.png"))
        browser.close()
