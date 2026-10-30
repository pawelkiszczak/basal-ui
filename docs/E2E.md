# End-to-end tests of basal-ui

The release gate of basal-ui. `tests/e2e/` starts a real basal-ui (`python -m basal_ui.app` on a free port, its own
settings directory under pytest's temporary directory), loads real models from your models directory into real
`basal-serve` children, and drives everything a user can do: basal-ui's HTTP API, its API server (keys, both request
shapes, MCP) and the pages in Chromium. The unit tests (`tests/test_ui_*.py`) cover the same routes with stubbed
children; these tests are what catches a broken model start, a page script error or an engine that disagrees.

They are skipped unless `BASAL_E2E=1`, so `pytest` stays fast.

```bash
uv sync --group dev --extra mcp --extra e2e      # the dev group pins the engine release basal-ui is tested with
uv pip install --reinstall-package basal "basal[mlx] @ https://github.com/rkinas/basal/archive/refs/tags/v2.4.1.tar.gz"  # optional: the engine extras of the machine under test (mlx, vllm, media)
uv run --no-sync playwright install chromium     # once: the page tests drive Chromium
BASAL_E2E=1 uv run --no-sync pytest tests/e2e -v -rs  # minutes; -rs lists what this machine skipped and why
```

Run it on every hardware class you ship for (Apple Silicon, an NVIDIA GPU, a CPU-only machine): each run tests the
engines that machine has and lists the rest as skipped.

Install the Engine extras for the host after syncing; `--extra e2e` installs Playwright, not MLX, media dependencies
or another Engine backend. Keep subsequent test commands `--no-sync` so the prepared environment is reused.

## Models

| Fixture | Picked | Used by |
|---|---|---|
| `text_model` | the smallest downloaded, runnable text model, basal-2.0 first (`BASAL_E2E_MODEL`) | almost everything |
| `second_model` | another one (`BASAL_E2E_SECOND_MODEL`) | several models on one API port, memory budget, restart |
| `bf16_model` | bf16 PyTorch weights of basal-2.0 / 1.5 / 1.0 (`BASAL_E2E_BF16_MODEL`) | Inspect, mode comparison |

A fixture with no model on disk skips its tests; `BASAL_E2E_DOWNLOAD=1` downloads `Remek/basal-2.0-mini` (bf16,
3.2 GB: runnable on CUDA, Apple Silicon and the CPU) through basal-ui's Download button instead. Other variables:
`BASAL_E2E_MODELS_DIR` (the models directory to use; default: basal-ui's default, the Hugging Face cache) and
`BASAL_E2E_LOAD_TIMEOUT` (seconds for a model start or a job; default 1800, since `--mode fast` compiles for minutes).
On a fresh Apple Silicon machine, `Remek/basal-2.0-mini-MLX-8bit` and `Remek/basal-2.0-mini` cover every test that
the hardware allows.

## What is tested

| File | Feature group | Checks |
|---|---|---|
| `test_lifecycle.py` | start, auto-load, shutdown | a load with options is remembered; a new basal-ui on the same settings loads it again with them; Ctrl+C (SIGINT) leaves no `basal-serve` child |
| `test_models.py` | several models, API server | two models behind one port with a required key: `/v1/models`, `MODEL_REQUIRED`, 401 / 404, request ids, OpenAI Decisions, `/v1/stats` closed to user keys, MCP `list_models` / `decide` when MCP is installed; statistics, history (API and MCP), key last-use; the memory budget refusing a load; restart onto a new child, logs, Download refusing a model on disk, unload |
| `test_safety.py` | decision safety | the entry's `certified` thresholds and `context`; `act` with `max_error` deferred below the certified threshold (or without one); a state over the context truncated with consistent token counts; History's `deferred` / `truncated` flags |
| `test_question_types.py` | every question type | each `advanced.jsonl` request (multi, act, tool, classify, verify, compress, extract) answers in its own shape: keys, sets, costs, verdicts, kept units and their offsets, extracted values and their spans; per-type statistics; `facts` and `lang`; evidence spans or its refusal |
| `test_calibration.py` | custom calibration | task questions (classify) fitted with the engine in basal-ui's process and its memory released; a file of another model imported with basal-serve's warning and its report; a fit against a temporary uncalibrated child, its report and listing, the child's memory released; selecting the file: Restart to apply, `--calibration`, no calibration warning; back to the shipped file; cancel; refused inputs (with the problem line) |
| `test_engine_features.py` | 2.3.1 integration | real CPU packed and MLX packed loads, inference and restart; child health idle counters; Chromium with explicitly simulated 422/503/500 responses, missing/unreachable counters, warning rendering, packed and cache help |
| `test_load_options.py` | load options | every mode of `MODE_ORDER` with reasons for the unavailable ones and the default available; options in the command; `--context-overflow refuse` returning 422; invalid / unavailable options refused; basal-serve's own refusal (prefix cache with `--deterministic`) on the model's row; remembered options used by a plain load |
| `test_backends.py` | the backend manager | an explicit inference test of a downloaded model on an installed backend in an isolated app; browser navigation, filtering and dialogs ([BACKENDS.md](BACKENDS.md#verification)) |
| `test_batch.py` | batch | every line answered in order, the summary, paging, the download; not counted in the statistics; cancel without an output file; refused files |
| `test_measurements.py` | benchmarks | the preset (44 items), a load test (questions per request), a determinism check on a model loaded with `deterministic` (identical answers), two engine modes in process agreeing on 90% or more with the memory released |
| `test_inspect.py` | Inspect | basal-viz at `/viz` for a bf16 model: info, an explanation that matches basal-serve's probabilities, memory counted then released; other ports refused |
| `test_experimental.py` | thinking, early exit | refused where they cannot run; with basal-2.0 4.5B and vLLM: forced reasoning with its text, and `think: off`; with basal-1.0 bf16 on CUDA: every early-exit level |
| `test_pages.py` | the pages (Chromium) | every view without a script error; WCAG contrast of visible text in both themes; the Playground building each advanced request exactly as written, sending it and rendering its type's view (source highlighting, claim tables); the builder keeping what its fields cannot write (rubric labels, `option_keys`) until edited; no automation claim on media or unvalidated answers; a media example on a text model (its file names, the notice, the element probabilities over the screenshot, the audio player); the builder's type switch, cost matrix, multi-tool set options and inline errors; the safety strip; the load options dialog with each switch scoped to its backend, load-option presets, the Setup note and the revision chip; Model manager section order and the two Models tabs; the Playground draft kept across the language switch (including invalid JSON and the media notice); Batch; History and Logs, including real-model recording, browser Replay and resubmission preserving structured state and choice descriptions named `data`; importing a calibration in the Calibration block; Inspect embedding basal-viz and explaining a Playground decision with what differs, and the handoff's notes and refusals; the model card, the theme switch, keyboard shortcuts and the usage line's branches / lane; Polish and English server-coded model reasons (including unavailable older families), suggestion explanations and options-mode reasons; no dictionary string left in English in the Polish UI |
| `test_elements.py` | marked image elements (Chromium) | natural-pixel box coordinates despite display scaling; drawing, tiny-box rejection, movement, resizing, edge clamping, deletion/renumbering and the 26-box limit; labels, linked choice question, read-only options, clear/delete cleanup, original-image restore, marked PNG badge and re-edit; one-image restriction; clean decide payload and probability overlay; saved-request round trip; evidence disabled with media and re-enabled without it |
| `test_activity.py` | history, calibration and logs polling (Chromium) | oldest-first history draining of more than one page per poll; the 500-row live-only limit and older-page pagination across idle polls; delayed calibration responses never replacing the selected model's controls; the Logs view clearing old lines when a buffer's generation changes |
| `test_visual.py` | responsive presentation (Chromium) | every view fits 1440–390 px without horizontal overflow in both themes; the question controls stay contained and keep their names |

A test that needs hardware or a model this machine does not have is skipped with the reason (for example the CUDA
graph modes on Apple Silicon, thinking without vLLM, early exit without a CUDA GPU).

## Verified dev gate 2026-10-29

The reviewed changes are integrated in
[`f97ad69`](https://github.com/pawelkiszczak/basal-ui/commit/f97ad69cb62e627e686081433ab6624a4ce3165e)
through merged [PR #6](https://github.com/pawelkiszczak/basal-ui/pull/6), including
[@agentGreg's PR #4](https://github.com/pawelkiszczak/basal-ui/pull/4) and
[PR #5](https://github.com/pawelkiszczak/basal-ui/pull/5). Its tree matches the locally tested
[`c9b7661`](https://github.com/pawelkiszczak/basal-ui/commit/c9b7661e12c54c6e3d7738a51436675a2545f86f).
This is a development verification record, not a release: no promotion to `stage`/`main` or PyPI publication was performed.

Host: Apple Silicon (arm64), macOS, Python 3.13.11, Engine 2.4.1 with its MLX extra and Chromium. The models were
already cached; no weights were downloaded.

| Check | Observed result |
|---|---|
| Full e2e before the contributor follow-up | 111 passed, 5 skipped |
| Full e2e after the contributor follow-up | 112 passed, 5 skipped |
| Unit suite (`make test`) | 314 passed, 3 skipped; 88% coverage |
| Ruff lint and format check (`make lint`) | Passed |
| [Post-merge CI](https://github.com/pawelkiszczak/basal-ui/actions/runs/40956690254) | Ubuntu/macOS × Python 3.10/3.13 × Engine 2.4.1, plus `releases` and `ci-ok`: passed |

After the setup above, reproduce the real-model run with:

```sh
HF_HUB_OFFLINE=1 \
BASAL_E2E_MODEL=Remek/basal-2.0-mini-MLX-8bit \
BASAL_E2E_SECOND_MODEL=Remek/basal-2.0-mini \
BASAL_E2E_BF16_MODEL=Remek/basal-2.0-mini \
make e2e ARGS='-v -rs --tb=short'
```

The five e2e skips were:

- Thinking: requires a downloaded basal-2.0 4.5B model and vLLM.
- Early exit: requires basal-1.0 bf16 on a CUDA GPU.
- Three context-policy checks: Engine 2.4.1's `GET /v1/models` did not report the required policy metadata.

The History regression sends ordinary structured `state.data` and a choice description named `data` through a real
model, persisted History, browser Replay and resubmission without changing them. The translated-reason regression
always supplies an older unavailable model and enables **Older families** in both Polish and English passes.
Nested audio/image payload redaction is covered by unit tests; it does not add audio input support to the Decisions API.
Browser HTTP-error, health-warning and polling edge cases use deterministic fixtures, not physical GPU failures.
Passing this Apple Silicon run does not validate NVIDIA/Strix/TPU hardware or replace the e2e gate on `stage`.

## Writing more

Use the session `ui` fixture and its helpers (`tests/e2e/support.py`): `ui.load(id, options)` brings a model to ready
with exactly those options (restarting it if needed), `ui.run_job(path, body, kind)` starts a background job and waits
for it, `ui.detail(method, path, body, status)` returns the message of a refused request. Tests share one basal-ui,
so each one loads what it needs and puts back what it changes (options, calibration selection, memory settings).

## Focused 2.3.1 gate

Run `uv run --no-sync pytest -q tests/test_ui_engine_features.py` for simulated capability, option lifecycle (including auto-load),
health caching/missing fields, both HTTP error envelopes, gateway/Playground forwarding, Batch error records, and
calibration warning tests. Run the hardware/browser gate with existing local models only:

```sh
BASAL_E2E=1 HF_HUB_OFFLINE=1 \
BASAL_E2E_MODEL=Remek/basal-2.0-mini-MLX-8bit \
BASAL_E2E_BF16_MODEL=Remek/basal-2.0-mini \
uv run --no-sync pytest -q tests/e2e/test_engine_features.py
```

The example selects Apple Silicon models; change the selections for another host. MLX checks skip with a reason
when unavailable. CPU packed runs on the host CPU, even on an Apple machine. The browser error/health-warning edge
cases use simulated responses, so their success does not prove a physical GPU OOM or NVIDIA graph acceptance.
NVIDIA compact/fine and sm_120 routing still require hardware-specific acceptance; capability tests alone do not
prove the kernels. Calibration jobs may finish as `done_with_warnings`; the release helper treats that as completed
work and the tests must inspect its warnings/thresholds instead of treating completion as certification.

## Interface design system check

For presentation changes, run `make run` on loopback and use the in-app browser.
Do not save settings, start inference or create credentials during this read-only UI check.

1. Visit Overview, Models, Model manager, Backends, Playground, Batch, Inspect,
   API server, History, Logs and Design System. Check console errors, visible labels,
   disabled prerequisites and empty states. Model discovery and hardware checks must
   still come from the real local APIs.
2. On Models, open Setup, inspect its engine choices and collapsed sections, then
   cancel. On History, filter the empty result; clear the filter afterward. In Logs,
   check the single toolbar and source selection.
3. In Design System, switch its example tabs by click and arrow keys, open and close
   its example dialog with Escape, and use a sample action. Only catalogue feedback
   should change; `/api/status` must show no new downloads or loaded models.
4. Check all views at 1093 px and 390 px, both light/dark themes, both Green/Orange skins and
   English/Polish. The page must not overflow horizontally; tables and navigation may scroll locally.
   Reset the temporary viewport and restore the initial theme/skin/locale afterward.
5. Skins: with no `basal-ui.skin` entry the page loads green; choose Orange and reload with throttled
   rendering to confirm no green frame appears first; set `localStorage['basal-ui.skin']='purple'` and
   reload to confirm the green fallback. Changing skin or theme must not reload the page or reset a
   Playground draft. At 390 px switch all three preferences from the top bar without page overflow.
   Status labels, destructive buttons and the focus ring must look the same in both skins.
6. To verify catalogue removal, temporarily omit its script tag, reload and verify
   that the other views work and `#design-system` falls back to Overview. Restore the
   tag for the requested delivery.
7. Run `make lint`, `make test`, JavaScript syntax checks (`node --check` on changed
   scripts), and `git diff --check`. No frontend build is needed.

This recipe checks interface behavior with no downloaded model. It does not replace
real-model tests for loading, inference, calibration, Batch execution or inspection.

### Local interface evidence — 2026-10-30

The following table records the first pass; the second-pass evidence follows it.

Apple M5 / macOS, source based on `f37fe44d765f9e4ce6aab1b77fcea43e833e63c8`,
local `ui/design-system-refresh` candidate, served at `127.0.0.1:7860`:

| Criterion | Evidence | Observed result / limit |
| --- | --- | --- |
| Consistent views and responsive controls | In-app browser, all 11 views at 1093 px dark and 390 px light; English and Polish spot checks | No page-level horizontal overflow, no blank visible selectors, no duplicate IDs or console errors |
| Action and state hierarchy | Visual inspection of hardware, suggestions, manager, backends, Playground, Batch, API, History and Logs | Correct Apple capitalization; shared spacing/icons; left-aligned Playground; labelled Batch controls; actionable/compact empty states |
| Existing setup workflow | Models → Setup → engine controls → Cancel | Real options API and modal work; no settings saved |
| Catalogue interactions | Tab click, arrow-key selection, dialog open/Escape, example action | Correct tab content and local feedback; no model downloads or loaded models |
| Catalogue removal | Omit catalogue script, reload direct catalogue URL, navigate to Batch, restore script | Overview fallback, ten application destinations, no console errors; full catalogue restored |
| Repository checks | `make lint`, `make test`, `node --check` on changed scripts, `git diff --check` | 314 passed, 3 skipped; lint/syntax/diff checks passed |
| Real-model execution | Not run for this presentation change | No downloaded model; inference, Batch execution and inspection are not certified by these checks |

### Second-pass integration evidence — 2026-10-30

Based on `9f5cc7f` on `ui/design-system-refresh`, Apple M5 / macOS,
local loopback app. Claude Opus 5.5 implemented the rework; the integrating agent
performed the executable checks below and corrected short-window navigation,
Batch field proportions and the duplicate empty-library action.

| Criterion | Evidence | Result / limit |
| --- | --- | --- |
| Complete view coverage | 88 in-app browser cases: 11 views × 2 skins × 2 themes × 2 widths (390/1093 px) | No page overflow, blank visible selectors or console errors |
| Polish reflow | All 11 views at 390 px in Polish | No page overflow; skin and theme controls stay accessible |
| Short-window sidebar | 1280 × 720 browser geometry and visual check | Brand, all destinations and appearance footer visible; long navigation can scroll independently |
| Preference behavior | Node VM: defaults, saved values, invalid values, independent updates, unavailable storage; browser skin switch and reload | Default/fallback green; orange persists; theme remains independent; in-progress catalogue text survives skin/theme changes |
| Real setup flow | Models → Setup → Cancel | Model options opened with real local data; no settings saved |
| Catalogue interactions and isolation | Tabs, arrow keys, native dialog/Escape, sample model action; subsequent real status read | Local feedback only; zero loaded models/downloads, API server off |
| Catalogue removal | Omit its script, reload direct catalogue URL, restore | Falls back to Overview; ten core destinations, no console error |
| Repository checks | Full unit suite, lint, syntax on all changed JS, final i18n and diff checks | 314 passed, 3 skipped; all targeted checks passed |
| Real-model gate and contrast audit | Not run | No model is downloaded. No claim of inference certification or a complete WCAG audit |

The obsolete E2E assertion for the removed inference eyebrow now checks that the
live inference region remains visible. Its real-model execution is still deferred
to the separate hardware gate.
