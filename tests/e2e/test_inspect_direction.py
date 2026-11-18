"""Inspect alignment mirrors and the narrow header wraps in both directions."""

import pytest

sync_api = pytest.importorskip("playwright.sync_api")


@pytest.mark.parametrize("direction", ["ltr", "rtl"])
def test_inspect_direction(ui, direction):
    with sync_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 320, "height": 800})
        page.goto(f"{ui.url}/viz/")
        page.wait_for_function("booted")
        actual = page.evaluate(
            """direction => {
              document.documentElement.dir = direction;
              const comparison = averageView({
                options: ['Synthetic'], orders: [{canon: [0.8]}, {canon: [0.7]}],
                mean: [0.75], calibrated: [0.75], temperature: 1, answer: {index: 0}
              });
              const decision = decisionView({
                options: ['Yes', 'No'], thresholds: {'0.05': 0.75},
                answer: {confidence: 0.75}
              }, {why: 'synthetic', color: 'red'});
              document.body.append(comparison, decision);
              const center = node => {
                const rect = node.getBoundingClientRect();
                return (rect.left + rect.right) / 2;
              };
              const meter = decision.querySelector('.meter'), track = meter.querySelector('.track').getBoundingClientRect();
              const fill = meter.querySelector('.fill').getBoundingClientRect();
              const atStart = direction === 'rtl' ? fill.right === track.right : fill.left === track.left;
              const labels = decision.firstChild.children;
              return {
                scaleFits: atStart && Math.abs(center(meter.querySelector('.dot')) - (track.left + track.right)/2) < 1 &&
                  (direction === 'rtl' ? center(labels[0]) > center(labels[1]) : center(labels[0]) < center(labels[1])),
                headerFits: document.querySelector('header').scrollWidth <= window.innerWidth,
                alignment: [...comparison.querySelectorAll('.cmp')].map(chart =>
                  ['.hdr:nth-child(3)', '.val'].map(s => getComputedStyle(chart.querySelector(s)).textAlign))
              };
            }""",
            direction,
        )
        assert actual == {"scaleFits": True, "headerFits": True, "alignment": [["end", "end"], ["end", "end"]]}
        browser.close()
