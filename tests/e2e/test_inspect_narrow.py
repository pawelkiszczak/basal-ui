"""Synthetic long labels in the served Inspect page at a narrow CSS viewport."""

import pytest

sync_api = pytest.importorskip("playwright.sync_api")


def test_inspect_narrow_labels(ui):
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 320, "height": 900})
        page.goto(ui.url + "/viz/")
        page.evaluate("""() => {
          const label = 'Długi opis opcji do sprawdzenia zawijania tekstu, który powinien być widoczny w całości.';
          const req = {state: 'Przykładowy stan', questions: {q: {type: 'choice', instructions: 'Pytanie',
            criteria: {a: label, b: 'Inna opcja'}}}};
          document.querySelector('#modelchip').textContent = 'Remek/basal-2.0-mini · mps · bfloat16 · 24 layers · prompt format chat · 2 option orders';
          loadRequest(req);
          store.set('basal-viz-history', [{t: Date.now(), q: label, a: label, conf: .75, req}]);
          renderHistory();
          const order = {letters: ['A', 'B'], perm: [0, 1], letter_probs: [.75, .25], canon: [.75, .25], top_tokens: []};
          const q = {options: [label, 'Inna opcja'], orders: [order, order], layers: [[.5, .5], [.75, .25]],
            calibrated: [.75, .25], mean: [.75, .25], temperature: 1, answer: {index: 0, confidence: .75}};
          const out = document.querySelector('#out');
          const result = h('div', {class: 'result'}, h('div', {class: 'step'}, h('div', {class: 'body'},
            layerChart(q), readoutView(q), averageView(q))));
          out.replaceChildren(result);
        }""")
        page.wait_for_selector(".legend i")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.evaluate("""() => [...document.querySelectorAll('.hist .q, .hist .a, .legend i, .hbars .txt, .cmp .txt')]
          .every(e => e.scrollWidth <= e.clientWidth && e.scrollHeight <= e.clientHeight)""")
        page.evaluate("""() => showTip({pageX: 300, pageY: 300}, 'Warstwa',
          [{color: 'red', value: '75%', label: 'Długi opis opcji do sprawdzenia zawijania tekstu'}])""")
        assert page.evaluate("""() => { const r = document.querySelector('#tip').getBoundingClientRect();
          return r.left >= 0 && r.right <= innerWidth; }""")
        page.add_style_tag(content="html { font-size: 24px }")
        assert page.evaluate("getComputedStyle(document.body).fontSize") == "21px"
        browser.close()
