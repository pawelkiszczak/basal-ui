# Issue #144 revision review

## Disclosure inventory

The header has two named native `<details>` menus: Tools and Appearance. They share the header-menu group; Escape closes the open menu and returns focus to its summary, and choosing a theme closes Appearance and returns focus there. Playground Request settings is a separate native disclosure with its own placement, width and padding; it now shares the same border, radius, surface and shadow declarations. The mobile Tools menu aligns to the header edge in both English and Polish.

Other native disclosures remain inline and keep their own layout: recommendation help, raw response JSON, inserted question sets, conditional questions, prompt/loaded-entry/README/data/reasoning/comparison details, Cascade fallback, benchmark and calibration help, calibration Advanced, order and mode details, and the Engine option sections. They were inventoried but not restyled as popovers. No loading animation, Models action, API example, or other Playground behavior changed.

## Chromium checks

The preview used the worktree source with isolated settings and a synthetic local port:

```sh
BASAL_UI_CONFIG=/tmp/basal-ui-issue144-rev/settings.json uv run --no-sync python -m basal_ui --port 8813
```

Playwright ran in the built-in Playwright MCP Chromium browser, isolated context `issue144-revision`, at `http://127.0.0.1:8813/`. To reproduce the keyboard checks, focus each header summary and press Enter to open it. In Tools, press Tab to reach Model manager, Enter to navigate, and confirm `#pageTitle` has focus; Escape closes an open header menu and returns focus to its summary. In Appearance, press Enter, Tab through the skin and theme choices to Dark, then Enter; the theme changes, the disclosure closes and focus returns to Appearance. The same Tab/Escape sequence was exercised from Tools and Appearance with Polish labels. Removing the custom Tab handoff made Chromium skip the open Tools buttons and land on Settings, so the `requestAnimationFrame` focus handoff remains necessary.

The browser exposed Tools and Appearance at 1280×560 for the English/light Tools screenshot and the Polish/dark Appearance screenshot. At 320px, both header menus and Request settings were opened in light and dark themes with English and Polish. Each panel stayed within the viewport and document scroll width remained 305px against a 320px inner width. Tools and Appearance measured x=29–289px; Request settings measured x=37–293px. In the dark theme, the two shared surfaces computed to the same 12px radius, 1px line border, surface color and shadow while Request settings kept its column layout and 24px padding.

On Logs, the checked Follow checkbox was toggled off and back on with Space. In Request settings, the unchecked Facts checkbox was toggled on and back off with Space; `:focus-visible` was true with a 2px outline. The environment reported History audio as available, so the disabled checkbox rendering was checked by setting its DOM `disabled` property for this browser-only check; it remained unchecked and unavailable.

The Decision server form was exercised with the synthetic name `Synthetic test` and URL `https://example.test`. Its consent checkbox became visible; pressing Enter while it had focus left it unchecked and did not dispatch a submit, while Space checked it. A browser capture listener prevented any submit from reaching the app during this check. The accessibility snapshot exposed the input as a named native checkbox.

Screenshots: [Tools in light English](../images/issue-144-controls.png) and [Appearance in dark Polish](../images/issue-144-appearance-dark-pl.png).

## Automated checks and limits

`make lint` passed: Ruff reported all checks passed and 112 files already formatted. `make test` passed: 863 passed, 1 skipped, 88% coverage, in 232.01 seconds. Real-model E2E was not run; it needs the configured real model and `basal-serve` release-gate setup described in `docs/E2E.md`. No screen-reader session or hardware/model test was part of this revision.
