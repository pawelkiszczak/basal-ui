# Preset URL import: focused proof

Base: dev `629740138999eb1ef26233de226287d7fd4b8ea4`. The network backend is derived from issue 28 PR 79 source `50cbd69e31361a83027472f46d7b7a7ceb6e7a58`; `basal_ui/fetch.py` is a shared integration overlap. The preset worker is fixed by the server, and the public Fetch default remains separate. Merge this overlap carefully if PR 79 lands first; rollback must retain its backend when another consumer uses it.

- A focused pytest run returned **26 passed, 2 deselected**: preset route and existing preset validation plus PR 79's backend address, redirect, TLS/Host, bounds, parsing and worker lifecycle tests. The initial broader selection also included PR 79's app Fetch route and Decision provenance tests: **26 passed, 2 failed**, because those unrelated features are deliberately absent from this independent branch. They were excluded by name in the backend-only rerun.
- A separate real worker route check returned **1 passed**: loopback URL refused before settings mutation. The transport tests use mocked DNS and connection objects; the route merge test injects a manager and persists into an owned temporary settings file. A valid JSON pack over 200,000 characters is exercised in the mocked transport, not over a real network.
- Ruff checks and formatting checks passed for the changed Python files. `git diff --check` passed.
- Headless Chromium at 320 × 800 exercised keyboard activation, Cancel, refused-address error and success in English and Polish, with no horizontal document overflow. The app used an injected deterministic catalog, a synthetic fetch manager and fresh settings with update checks disabled. Loopback-only socket guards, a loopback proxy bypass with a dead proxy for other destinations, browser request routing and blocked service workers constrained the fixture. Screenshots are `docs/images/presets-url-en-320.png` and `docs/images/presets-url-pl-320.png`; they illustrate the Settings surface, not a real public fetch. The browser and server were closed after each run.

All commands used the shared read-only issue 22 environment via `UV_PROJECT_ENVIRONMENT`, `uv run --no-sync`, workspace imports (pytest configuration or `PYTHONPATH`), owned cache, temporary, config and HF directories, `HF_HUB_OFFLINE=1`, and `PYTHONDONTWRITEBYTECODE=1`. No installation or real external network was used. Python/JS LSP was unavailable; exact-symbol searches traced the existing preset handler and callers.

Imports run load then request entries through the existing per-item update path. A later invalid entry can leave earlier entries saved; same-name values replace, null deletes, unrelated entries survive, and the request root `model` is removed. Strict JSON numbers follow the borrowed Fetch fidelity policy; invalid precision is refused. JSON packs are limited to 1 MiB before parsing and do not inherit the Playground's 200,000-character State limit.

Not run: hosted CI, real public-network transport, real-model E2E, screen reader, native 200% zoom and physical-device testing. No model was loaded. These focused checks do not establish release acceptance.

## Commands and proposed PR body

The focused Python commands, with the environment described above, were:

```sh
uv run --no-sync pytest -q tests/test_ui_preset_url.py tests/test_ui_app.py::test_presets_validate_and_strip_request_model /Users/pawelkiszczak/orca/workspaces/basal-ui/issue-28-fetch-web-nov15/tests/test_ui_fetch.py -k 'not local_route_and_shutdown and not decision_history'
uv run --no-sync pytest -q tests/test_ui_preset_url.py::test_real_worker_refuses_loopback_before_mutation
uv run --no-sync ruff check basal_ui/app.py basal_ui/fetch.py basal_ui/preset_fetch.py tests/test_ui_preset_url.py
uv run --no-sync ruff format --check basal_ui/app.py basal_ui/fetch.py basal_ui/preset_fetch.py tests/test_ui_preset_url.py
uv run --no-sync python .local-issue32/ui_proof.py
```

The first command preceded the addition of the separate worker test; the temporary UI harness was removed after the captures. The initial broader selection used `-k 'not fetch_route'`, which did not exclude the two unrelated tests described above.

Suggested title: `Import presets from a URL`

Suggested body:

> **tl;dr:** Import a shared JSON preset pack from Settings or `POST /api/presets/import-url`, using the reviewed public-address guard and a 1 MiB response limit.
>
> **Assumptions:** Entries merge in file-import order through the existing per-entry validation; an error can leave earlier entries saved. Numbers that cannot survive browser JSON transport are refused. No registry or automatic updates are added.
>
> **Engine impact:** None; no Engine pin or model behavior changes.
>
> **Baseline and observed proof:** PR 79 supplies the reviewed network backend; the focused backend and preset checks passed, as did isolated English and Polish 320 px browser checks. See `docs/research/issue-32-url-import-proof.md` for exact results and unrun gates.
>
> **Door / Blast Radius:** The new endpoint downloads anonymous JSON through a fixed owned worker and updates only named preset entries. `basal_ui/fetch.py` overlaps PR 79 and requires integration with that shared backend; retain it on rollback if another consumer uses it.
