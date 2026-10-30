# 0.1.0rc1

Unreleased release candidate (planned tag `v0.1.0rc1`). The review fixes and contributor PRs
[#4](https://github.com/pawelkiszczak/basal-ui/pull/4), [#5](https://github.com/pawelkiszczak/basal-ui/pull/5)
and [#6](https://github.com/pawelkiszczak/basal-ui/pull/6) are merged
on `dev`; [verification and remaining hardware limits](E2E.md#verified-dev-gate-2026-10-29) are recorded separately.
No promotion to `stage`/`main` or PyPI publication was performed for that snapshot.

- Prepare package version `0.1.0rc1` and PyPI trusted publishing. The GitHub `pypi` environment permits only `v*`
  tags and requires maintainer approval without an administrator bypass. Release tags must come from `main`;
  release candidates become GitHub prereleases and are not marked Latest. [Release process](../CONTRIBUTING.md#releasing)

- basal-ui is its own repository and package: import `basal_ui`, the `basal-ui` command, and `pip install basal-ui`
  after the first PyPI release. Until then, use the [source install](../README.md#install).
- It runs on upstream engine releases `>=2.4.1,<2.5` and refuses others, printing the install command, unless
  `BASAL_UI_ENGINE_UNCHECKED=1` is set (the top bar then shows an *experimental engine* badge with the version and
  commit). [README](../README.md#supported-engine-range)
- API keys, CORS, request ids, typed error envelopes and the OpenAI Decisions translation are owned by basal-ui's API
  server, so the engine children it starts are stock `basal-serve` on loopback, without keys.
  [API](API.md)
- Load tests, determinism checks, engine-mode comparisons and Batch run the engine's tools (`basal-loadtest`,
  `basal-bench`, `basal-run`) as subprocesses.
- The LoRA adapter load option appears only with an engine that provides it (≥ 2.5.1), behind
  `engine.feature("adapter")`; on 2.4.x it is greyed out with the reason.
  [Research note](research/lora-adapters-at-load.md)
- The update check covers basal-ui and the engine: the Overview reports a newer basal-ui release, a newer engine
  release inside the supported range, and an engine release this basal-ui does not support yet.
- On engine 2.4.1 basal-ui fills in what the engine does not report itself: the certified thresholds of a loaded
  model come from the calibration file it serves; a file fitted in basal-ui names the model's repo id and commit
  (not the cache path) so `basal-serve` applies it; Inspect explains a `"facts": "auto"` request with the facts the
  served model reads; the Playground's context meter appears once the engine reports its context policy. MCP on the
  API server answers with the first loaded model until the engine's MCP takes a `model` argument.
- **Playground visual elements and media samples:** mark, label, move and resize numbered boxes on an uploaded image;
  save them as a `choice` question and see per-element probabilities over the marked image. Saved requests retain the
  original screenshot and marks. Evidence spans are disabled while media is attached. `basal_ui/examples/media.jsonl`
  adds three generated images (a checkout screenshot with numbered elements, a Polish invoice, a revenue chart) and
  two voice clips (Polish complaint, English appointment) for basal-omni, loaded into the media field with an audio
  player for clips and a notice on a model without media; Inspect skips them. [USAGE.md](USAGE.md#browser-app-pick-a-model-for-your-hardware-and-try-it)
- **Inspect for Gemma 4 basal-omni:** the bf16 basal-omni checkpoints (not the recurrent basal-omni-pico) can be
  inspected once the engine's basal-viz reads them (`engine.feature("viz_gemma4")`, proposed upstream in
  [rkinas/basal#75](https://github.com/rkinas/basal/pull/75)); on 2.4.x they are listed disabled with the reason.
  `/api/models` reports why each variant cannot be inspected (`inspect_reason`). Requests with media are refused.
  [USAGE.md](USAGE.md#inspect)
- **One engine pin:** `basal_ui/engine.py` holds the engine releases basal-ui is tested with (`TESTED_RELEASES`, the
  last one pinned); CI's engine matrix is generated from it and a test checks that `pyproject.toml`, the README and
  the E2E guide install the same release. `make engine-pin` prints it.
  [CONTRIBUTING.md](../CONTRIBUTING.md#bumping-the-engine)
- **API server hardening:** a browser request whose `Origin` is not in the **CORS origins** setting is refused
  (403 `ORIGIN_REFUSED`) before it reaches a model, so a web page cannot run decisions on a keyless server; bodies
  must be `application/json` (415) and at most 256 MiB (413). The page and the embedded inspector refuse framing by
  other sites and are revalidated on every load. [API](API.md#authentication-and-cors)
- **Reliability:** a restart that fails no longer strands its memory reservation, and a concurrent load waits for it;
  shutdown cancels jobs, ends their private children and waits up to 30 s for them, while leaving the event loop free
  to finish inspection workers; a process-group member that ignores SIGTERM is killed; a full disk no longer fails an
  answered decision (it is logged instead); resetting statistics discards older unflushed counts from other instances
  without losing Decisions recorded after the reset; invalid settings JSON is never overwritten.
- **State and UI correctness:** revoked API keys stop working even when the settings filename contains glob characters;
  History preserves structured context during media redaction and preserves its live records when clearing fails;
  delayed calibration responses cannot replace another model's controls or apply its selection to the wrong model.
- **Models:** a sharded download counts as downloaded only with every shard its index names; the offline catalog uses
  one complete snapshot (`main` when complete, otherwise the newest complete snapshot). Backend tests pin the active
  child's actual cache commit and size their selected snapshot. Strix and TPU refuse non-bf16 and non-Llama checkpoints.
  ROCm GPUs are not offered FP8, NVFP4, vLLM or SGLang; a malformed benchmark row fails with its line number.
- **Accessibility:** History rows open from the keyboard (the time is a button with `aria-expanded`) and keep focus
  across live refreshes; element boxes can be added, moved (arrows) and resized (Shift+arrows) without a pointer;
  benchmark and band inputs have labels. Live-only History keeps the newest 500 rows; **More** preserves older pages
  and their pagination cursor across idle polls, and an empty successful poll restores the table after a network error.
- **Development:** Ruff `F,I,B,UP,SIM` with `ruff format` (`make format`, optional pre-commit hooks), a coverage
  report in `make test`, `SECURITY.md`, `CONTRIBUTORS.md`, Dependabot, and CI/release workflows with pinned actions,
  minimal permissions and a release job that checks the tag against `pyproject.toml` and validates nonempty release
  notes before PyPI publication. Changes flow `dev` → `stage` → `main`; CI runs on pushes to and pull requests into all three.
  [CONTRIBUTING.md](../CONTRIBUTING.md#branches-and-ci)
- **Contributor regressions:** [@agentGreg's History report (#4)](https://github.com/pawelkiszczak/basal-ui/pull/4)
  is covered by real-model recording, browser Replay and resubmission of ordinary `data` fields; nested Decisions
  audio/image payloads are redacted and cannot be replayed without their attachments.
  [The older-family e2e fix (#5)](https://github.com/pawelkiszczak/basal-ui/pull/5) enables **Older families** in both
  language passes and always exercises an older unavailable model, independently of the machine's model cache.

- **Design foundation:** the stylesheet is split into `tokens.css`, `components.css` and `views.css` (cascade layers,
  readable source); semantic colours (`--danger`, `--warning`, `--success`, `--info`) make errors, deferrals and
  warnings look like what they are and pass WCAG AA in both themes; muted text is darker, nothing meaningful is set
  below 12 px; the logo keeps basal's red-orange. `app.js` is split into per-view scripts under `static/js/`.
  `scripts/ui_fixture_server.py` runs the app on fake hardware and models for UI work.
- **Accessibility baseline (WCAG 2.2 AA):** a skip link, `<aside>`/`<nav>`/`<main>` landmarks and one `h1` per view
  (cards `h2`, sections `h3`); switching views sets the window title, moves the focus to the view title and announces
  it, and the view hashes no longer scroll the page. Tabs take the arrow, Home and End keys; error toasts are alerts that
  stay until closed, other toasts pause while hovered or focused. Downloads, batch, benchmark, calibration and the
  memory bar are progress bars (or a meter) with values, and jobs announce when they start, finish or fail. The Batch
  fields and the calibration paths have visible labels; Alt/Option+digit no longer fires while typing; the element
  marker lists each box's position and size. `tests/e2e/test_a11y.py` runs axe-core over every view, light and dark,
  at 1440 and 390 px on the fixture server and fails on serious or critical violations. [E2E](E2E.md#accessibility)
- **Clearer navigation and first steps:** the menu is grouped (Run, Models, Serve, Evaluate, System); Models and Model
  manager become **Discover** and **Library** (`#discover`, `#library`; `#models` and `#manager` still work), and a new
  **Settings** view holds the theme, language, models directory, auto-load, memory budget, update checks and presets.
  Discover offers **Download & load** in one step and **Load** for a model on disk, shows the fit as a meter (est.
  memory of the memory available now, with the arithmetic), and opens model cards from a **Details** button; Library
  rows carry Load → Use in Playground / Copy endpoint / Unload. Overview has a dismissible **Get started** checklist,
  the API server a Running / Stopped / Restart needed banner and an *Unsaved changes* mark, the Load options dialog a
  **Recommended / Custom** choice with what changed from the defaults and the Active load settings, and **Unload all**
  always asks first. Ctrl/⌘+K opens a command palette (views, models, actions). [USAGE.md](USAGE.md)
- **Charts and comparison:** a small inline-SVG chart helper (`static/js/charts.js`: meter, bars, histogram,
  sparkline, reliability diagram, threshold strip; colour-blind-safe `--chart-*` tokens in both themes, each chart
  named for assistive technology with its numbers in a hidden table). Overview shows the memory budget split by model
  and stat tiles per loaded model (requests, average confidence, p50 / p95 latency with a histogram of the last 200;
  `latency_recent_ms` in `/api/stats` and `/v1/stats`); Library draws statistics per question type, the newest
  benchmark (accuracy per file, latency) with trends over runs, and the calibration in use (certified thresholds,
  automation on CERT, leave-one-family-out ECE); Batch draws a reliability diagram for labelled answers. The Playground
  **Compares** one request on two loaded models with a per-option probability difference and **Copies as code**
  (cURL / Python for System One and OpenAI Decisions, MCP). The API server compares its three endpoints in a matrix
  and gives MCP client configs for Claude Desktop, Claude Code, Cursor, VS Code and Open WebUI. Path fields check the
  path live. Cards, headings, badges and metadata use one type scale; mono stays for code and data. [USAGE.md](USAGE.md)
- **Review fixes to the redesign:** Copy as code writes valid Python (`True` / `False` / `None`), keeps option and level
  descriptions in OpenAI Decisions and notes the questions and media it cannot carry (the OpenAI items are off when
  none translates); the MCP tool call is `decide_many` with the engine's arguments, and no MCP config passes
  `basal-mcp` flags it does not take (`--api-key`, `--model`). Error text is red (warnings amber), never the accent
  green. Switches and buttons show a 2px focus outline; Send, Compare and the dialog openers keep or report focus
  while they wait. The sidebar scrolls on short screens and the phone nav sticks to the top and shows the current
  view; disclosures show a chevron; the confidence gauge labels its 5% / 1% marks. `#benchmarks` and `#calibration`
  (and the command palette) open those Library blocks. The decision-log path check says an existing log is appended
  to; the path check returns a preview only for rows Batch or Calibration can read. Deferred: Shipped vs Selected
  calibration in Compare, a file picker (browsers do not reveal paths), ES-module views and the remaining `innerHTML`
  templates' move to the `html` helper.
- **Second review round:** a path that is too long or under an unreadable directory is reported in the hint (*not
  valid here*, *cannot read*) instead of an HTTP 500; the models directory has a live check and is read-only, with the
  reason, while a model is loaded. The Overview's loaded-model rows no longer re-render (and drop keyboard focus) on
  every status poll, and Load, Unload and Download keep the focus on the same row. Form fields and switches draw a
  `--control-border` of at least 3:1; Load options names its execution-mode radio group, Calibration its error-rate
  targets (checkboxes, not switches), and the confirm dialog its consequence. On phones the header is one row above
  the menu strip, Tab and anchors stay clear of it and of error toasts, History rows become cards, the endpoint matrix
  scrolls sideways, and Send or Compare scrolls to the answer; Library rows stack by their own width at 200% zoom;
  Compare takes the full width; short laptop screens fit the whole sidebar. **?** can be turned off in Settings.
  Discover's setup of the next load is now **⚙ Set up next load**, a loaded model offers **⚙ Restart options**
  everywhere, and Overview labels its tile *Avg best-answer prob.* as the Library does. The first-run steps count a
  Decision only once answered and a connection only while the API server runs. The stylesheets are larger than the
  old `app.css` (readable, commented source in three files rather than minimal size), and the views stay classic
  `defer` scripts sharing globals; both are deliberate for now.
- **Playground option editor and confidence:** options, labels, claims, levels and yes / no descriptions are edited
  as rows (key and description) instead of `key: description` lines — Enter adds a row, Alt+↑ / Alt+↓ moves it,
  Backspace in an empty row removes it, and a hint says when the rows would be sent with hidden keys. Each certified
  answer draws its confidence against the 5% and 1% marks (labels on two rows when the marks are close, so they never
  overlap in a narrow column) and **Why?** explains the verdict and the calibration in use (Shipped or Selected).
  The fixture server's stand-in answers depend on the model, so Compare shows two models apart.
  [USAGE.md](USAGE.md#browser-app-pick-a-model-for-your-hardware-and-try-it)
- **Docs media:** the README screenshots and tour are refreshed for the new UI (from `scripts/ui_fixture_server.py`,
  1280×800 at 2×, light), with short recordings in `docs/images/ux/`: the first-run tour, the Playground (dark), the
  command palette, a 390 px phone and a keyboard-only pass. `POST /api/paths/check` is documented in
  [API](API.md#browser-app-path-check).

# Before 0.1.0 (in the fork)

- **Basal UI message codes:** model availability, recommendation, backend setup and job-phase explanations retain their English API fields and include additive translation codes for Polish rendering. Server errors remain English.

- For engine 2.3.1 the UI adds CPU packed execution, Blackwell omni routing, opt-in MLX packed rows, graph-batch
  guidance, sampled child load counters, HTTP error diagnostics, and calibration completion warnings.
  [Engine integration](USAGE.md#engine-integration-in-231), [the release gate](E2E.md)

- **`basal-ui` navigation and Playground:** Models now separates Suggested and All; Model manager groups Loaded statistics, benchmarks and calibration before On disk and Models directory. Language changes keep the tab's Playground draft while requiring media to be attached again. Load-option Setup explains that media mode is selected in Model manager. [USAGE.md](USAGE.md#browser-app-pick-a-model-for-your-hardware-and-try-it), [E2E.md](E2E.md#what-is-tested)

- For engine 2.4 the catalog adds **basal-omni-pico** (Qwen3.5, text only) with a "lightest" suggestion; its modes are
  the engine's: `fast` / `fast-nocompile` on CUDA with flash-linear-attention (the default on Hopper), `eager`
  elsewhere, and `fast-exit`, `fp8`, `nvfp4`, `mps` and MLX shown as unavailable with the reason, as are the
  graph-engine options 2.4 refuses for it. `fp8` is unavailable below compute capability 8.9 before loading. The
  memory estimate of the CUDA graph engine adds 4 GiB for graphs and compile workspace (2.3.3-2.3.5 measurements).
  The model card shows the repository's quantisation (`config.json` `quantization_config` / `quantization`,
  `basal.json` `quantization` and `engine`, also before download), the Apple recommendation points to
  `--mlx-packed`, text files are read and written as UTF-8, and the memory of Windows hosts is read with
  `GlobalMemoryStatusEx`.

- For engine 2.4.1 the load options offer the **prompt layout** (`--layout`; default: the model's basal.json), refuse
  a non-canonical layout with the state or prefix cache before loading (basal-serve's own message), and show it as a
  `layout` chip on the loaded row and in the model card. [USAGE.md](USAGE.md#load-options)

- **`basal-ui`**, a local browser app: detects the hardware and the memory available now,
  suggests models that fit (recommended, best quality, fastest, multimodal), lists every basal model with its fit and
  serving mode, downloads the model into a directory of your choice, lists and
  deletes what is on disk, runs `basal-serve` for it and offers a playground for decisions, with images and audio
  for the basal-omni-1.0 checkpoints that have encoders (`--mode eager`, or `--mode vllm` on an NVIDIA GPU with
  the engine's vLLM extra).
  [USAGE.md](USAGE.md#browser-app-pick-a-model-for-your-hardware-and-try-it)
- The app can expose the loaded models as an API server, with optional named API keys, CORS, decision logging,
  MCP and auto-load. [API server walkthrough](USAGE.md#serving-models-from-basal-ui)
- **`basal-ui` community models**: the catalog adds the `pawelkiszczak` (basal-1.0 GGUF, MLX 8-bit, oMLX oQ6e) and
  `agentGreg` (basal-1.5-mini MLX 6-bit, bf16) conversions, marked community; weights-only GGUF files are served with
  their base repository's tokenizer and calibration. [USAGE.md](USAGE.md#browser-app-pick-a-model-for-your-hardware-and-try-it)
- **`basal-ui` model cards, presets, shortcuts, dark theme and update notices**: a card per model (provenance,
  calibration, loaded entry, README), named load-option and request presets with export / import, Ctrl/⌘+Enter, Alt+1…9
  and `?`, light / dark / system theme, a notice for a newer basal release and for new catalog models; load options
  take a `revision`; calibration takes basal-calibrate's advanced controls (`--strict`, `--reference`, minimum counts,
  `--report-only`, `--save-rows` and a `--from-rows` refit); results show `branches`, `second_order_branches` and
  `lane`, loaded rows the prompt format, option orders and vLLM preset.
- **`basal-ui` with several models**: load several models at once, all served on one API port where every request
  names its `"model"` (`GET /v1/models` lists them); a memory budget with a keep-free reserve refuses loads that do not
  fit and shows each model's resident memory; per-model statistics (requests, average best-answer probability,
  latency, tokens, per question type; also over HTTP at `/v1/stats`); benchmarks of a loaded model on the bundled
  examples or your own JSONL set ([BENCHMARK_SETS.md](BENCHMARK_SETS.md)); a **Download** button that only downloads;
  a **History** of every decision (playground, API, MCP) with replay in the playground and a **Logs** view of every
  `basal-serve`. In the fork, `basal-mcp --model` names the model on such a server.
  [USAGE.md](USAGE.md#browser-app-pick-a-model-for-your-hardware-and-try-it)
- **`basal-ui` decision safety**: every playground answer shows whether it was deferred to a person (and the action or
  tool refused), whether its confidence clears the model's certified 1% / 5% thresholds, whether its calibration is
  validated, unvalidated or fitted on text (no automation claim then), and when the state was truncated to the
  context; `GET /v1/models` carries `certified` and `context`. History marks deferred and truncated decisions.
- **`basal-ui` builders for every question type**: `multi`, `act` (simple costs or a cost matrix), `tool` (with the set
  options of several tools), `classify`, `verify`, `compress` and `extract` next to choice / noul / score, with
  `max_error`, evidence, `facts` and `lang` controls, a dedicated result view per type and the state shown with
  evidence spans, kept / dropped units and extracted fields highlighted; a request is sent as given until a field is
  edited; `basal/examples/advanced.jsonl` has one full request per type.
- **`basal-ui` custom calibration**: fit a calibration file on your labelled requests for a downloaded model (a
  temporary uncalibrated `basal-serve` plus `basal-calibrate --url`; with task questions the engine in basal-ui's
  process), or import one fitted elsewhere (with basal-serve's warning when it belongs to another model); read its
  report, compare it with the shipped file and select it per model. Benchmarks and calibrations run as background
  jobs with their own log.
  [USAGE.md](USAGE.md#custom-calibration)
- **`basal-ui` load options**: choose the engine mode per load (with the reason a mode is unavailable on this
  machine; Ollama, llama-server, SGLang, TPU and Strix Halo where detected) and every runtime setting of
  `basal-serve` — quantisation, attention, dtype, determinism, option orders, batching, caching, scheduling, context
  policy, the CUDA graph engine's kernel switches, the Gemma 4 eager forward, `--strict-calibration` and the think
  server; remembered per model and reused by restart and auto-load.
  [USAGE.md](USAGE.md#load-options)
- **`basal-ui` batch inference**: run a JSONL file through a loaded model from the **Batch** view, follow the progress,
  page through the answers and download them. [USAGE.md](USAGE.md#batch)
- **`basal-ui` load tests, determinism checks and engine-mode comparisons**: configurable `basal-loadtest` runs and
  `--determinism` checks against a loaded model, and `basal-bench` mode comparisons of a downloaded model, next to
  the benchmark preset. [USAGE.md](USAGE.md#statistics-and-benchmarks)
- **`basal-ui` Inspect view**: `basal-viz` embedded for a downloaded bf16 model, with **Inspect this decision** from
  the Playground, which lists what differs from the served decision (weights, engine, option orders, calibration,
  thinking, early exit) and refuses what basal-viz cannot explain; `basal-viz` applies `facts: auto` and its page
  uses relative API paths, so it also works under a prefix. [USAGE.md](USAGE.md#inspect)
- **`basal-ui` thinking and early exit**: load basal-2.0 4.5B with thinking or a basal-1.0 `fast-exit` model with a
  default early-exit policy from the load options; the Playground then sends `think`, `think_text` and `early_exit`
  and shows the reasoning of each answer that thought. [USAGE.md](USAGE.md#load-options)
- **`basal-ui` end-to-end tests** (`tests/e2e`, `BASAL_E2E=1`, extra `basal-ui[e2e]`): a real basal-ui with the models on
  disk, through its API, its API server and its pages in Chromium, for every feature above; skipped by default.
  [E2E.md](E2E.md)
