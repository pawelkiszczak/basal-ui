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
              document.body.append(comparison);
              return {
                headerFits: document.querySelector('header').scrollWidth <= window.innerWidth,
                alignment: [...comparison.querySelectorAll('.cmp')].map(chart =>
                  ['.hdr:nth-child(3)', '.val'].map(s => getComputedStyle(chart.querySelector(s)).textAlign))
              };
            }""",
            direction,
        )
        assert actual == {"headerFits": True, "alignment": [["end", "end"], ["end", "end"]]}
        browser.close()
