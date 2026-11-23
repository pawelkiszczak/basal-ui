# 0.2.0

This release adds model orchestration, scheduled inference, and new ways to build, serve and revisit Decisions.

## Interface

- Share popover surfaces across Tools, Appearance and Playground Request settings. Escape closes either header menu and restores focus, theme selection returns focus to Appearance, Space remains the native checkbox toggle, and Enter on a form checkbox does not submit the form. [Tools in light English](images/issue-144-controls.png) and [Appearance in dark Polish](images/issue-144-appearance-dark-pl.png).

## Models and inference

- Filter **All models** by name, download/load state, model compatibility, run availability and memory fit; clear all filters in one action. Setup and Unload use the shared secondary-action styling.
- Show a translated, screen-reader-announced pending status during initial loading and foreground actions, including while sending a Decision. See the [English](images/loading-progress.png) and [Polish](images/loading-progress-pl.png) captures.
- Create named escalation and Blend [Cascades](USAGE.md#cascades) from ready local models. Escalation supports an
  optional chat fallback. Playground and API responses identify the answering tier. Blend and fallback answers carry
  no automation certificate. Escalation without fallback also works in Batch.
- Attach [external Decision servers](USAGE.md#decision-servers-and-comparison-models) for Comparison in the Playground
  and Batch. History records the originating server, and Comparison confidence is uncalibrated.
- Attach curated chat models such as `Qwen/Qwen3-4B` using the Engine. Attached models appear in the Library while
  their calibration and compatibility are validated separately. [Attached models](USAGE.md#attached-models)
- Fine-tune on labelled Decisions with private inputs and outputs, cancellable training, held-out reports,
  base-associated adapter selection, and guarded cleanup. The host must support private adapter output.
- Schedule Batch over folders of JSONL files with local-time intervals, protected outputs, and run history. On Engines
  with Batch resume support, choose Resume or Restart after an interruption. Set the unanswered-line limit and use
  Engine question presets with automatic, Polish or English wording. [Batch](USAGE.md#batch)
- Explain each Suggested card's compatibility, memory-fit and slot-ranking reasons. Keep **Browse all models** available
  when no suggestion qualifies; memory estimates are labeled advisory. [Model suggestions](USAGE.md#browser-app-pick-a-model-for-your-hardware-and-try-it)

## Playground and History

- Build Decisions with editable Engine question sets, conditional questions, answer sets, abstention targets,
  and experimental rank questions when the installed Engine provides them.
- Fetch a public HTTP(S) page into editable State with provenance. Replay restores the recorded State without a
  second fetch and preserves native question fields and truncation details.
- Generate API examples beside the request inputs and copy the displayed snippet with its own control.
- Record a microphone clip as PCM WAV in the Playground. History can retain bounded audio copies when explicitly
  enabled, and Replay retrieves retained clips on demand. [Recording audio](USAGE.md#recording-audio-in-the-playground)
- Keep the Playground media picker within its panel and wrap full selected filenames; style **Compare models** as a
  secondary action.
- Import MCP tool catalogues into tool questions and shortlist locally. Import request and load presets from public URLs.
- Keep Load options tied to the latest model and dialog, including immediate reopen. Send Engine errors to the matching
  question cards, distinguish overload, and retain persistent error toasts.

## Engine, calibration, and operations

- Support Engine `>=2.4.1,<2.9` with a source pin of **2.8.0**. Capabilities are probed at runtime. Release CI covers
  Engine 2.4.1, 2.5.0, 2.5.1, 2.6.0, and 2.8.0 on Ubuntu and macOS.
- Load options follow the installed Engine's CLI. They include long-lane and SOAM choices, server limits, second reads,
  prepared kernels, graph memory, quantization, cache controls, and MLX options where supported. Earlier Saved settings
  and presets are migrated. Prepared kernels are optional for a cached model. [Load options](USAGE.md#load-options)
- Fit Calibration using one snapshot of the model revision, Execution mode, and prompt layout. Keep compatible shipped
  thresholds, inspect conformal levels, and preserve layout when refitting saved rows. Calibration warnings identify
  mismatches and invalid metadata. Benchmarks include the Identity gate, option-order checks, and active adapter/MLX
  settings where supported. [Calibration and benchmarks](USAGE.md#custom-calibration)
- Explain the choice between Shipped and Selected calibration with an accessible worked example of the Engine's
  temperature scaling, labelled input and how to return to Shipped calibration.
- Use API-server MCP model routing on supported Engines. Gateway forwarding preserves typed errors and Retry-After,
  reports upstream read timeouts as 504, and cancels forwarding on disconnect. The Dashboard reports stalled calls and
  lane counts. Admins can read Prometheus metrics at `/metrics`. [API reference](API.md)
- Add dated Dashboard traffic ranges with compact, persisted Decision aggregates and offline gaps. Use dated counts for
  window rates, keep lifetime Usage separate, and show the effective time range for overlapping source buckets.
  Remember the existing 1-second refresh cadence or choose 2, 5, 15 or 30 seconds.
- Keep admin credentials out of child processes, authenticate the Batch proxy per job, and bound Comparison requests by
  time and response size. Reserve Cascade ports through spawn and validate router health. Reject symlinked scheduled
  inputs and outputs, and keep Attached models removable after changing the models directory.
- Keep status snapshots coherent across model unloads, return Cascade error codes at the API top level, and reject
  unsupported text codecs at the Fetch boundary.
- Enforce the microphone recording size limit while chunks arrive, before buffering and decoding the full recording.
- Group the API server's MCP setting with dependency availability, the `/mcp` endpoint and a copy-ready client snippet;
  explain how to install the optional extra when it is missing. [API server](USAGE.md#api-server)
- Unload idle models after a configurable timeout while preserving remembered loads. Local model children start with
  detached stdin. Startup errors show a bounded log excerpt, with full output available in Logs.
- Preserve discovered Comparison models when an upstream returns a malformed listing.
- Improve keyboard navigation, zoom, responsive layouts, hover behavior, colour contrast, and Polish localization.
  Inspect follows the saved language and supports keyboard History activation, reduced motion, and forced colors.
- Run the release CI matrix after merges to `stage` and `main`. [Release process](../CONTRIBUTING.md#releasing)

# 0.1.0

Stable release, tag `v0.1.0`. Includes the review fixes and Studio redesign with the selected contributor-inspired
interactions. [Verification and remaining hardware limits](E2E.md#selected-studio-features-gate-2026-11-02) are
recorded separately; stable versioning does not certify untested hardware or model accuracy.

- Publish package version `0.1.0` through PyPI trusted publishing. The GitHub `pypi` environment permits only `v*`
  tags and requires maintainer approval without an administrator bypass. Release tags must come from `main`;
  `v0.1.0` is a stable GitHub release, not a prerelease. [Release process](../CONTRIBUTING.md#releasing)

- Apply the Studio layout throughout the app: Dashboard landing, primary task navigation, Tools and Appearance
  disclosures, paired Playground panels with Request settings, and responsive light/dark English/Polish views.
  Model manager separates Library, Usage by model, Benchmarks and Calibration. The new `/api/traffic?minutes=15|60`
  reports real process-local recorded Decision traffic, including input tokens/s, requests/s, latency, errors and
  public endpoint attribution. Lifetime `/api/stats` stays compatible and shared with Manager; reset scope is explicit.
  No prototype/demo data or layout switcher ships. The Engine range is unchanged.
- Keep Dashboard chart/model/endpoint nodes stable during live updates so scrolling and selected endpoint text are
  preserved. Add a shared **Load downloaded model** action across views, including Dashboard, with supported
  text/media loads and **Use** for the current view without discarding its input. Playground attachments and
  marked-element questions survive model loads/selections; incompatible media cannot be sent accidentally.
- Explain Suggested's real constraints and tier/format heuristics; add detected local Backend choices with
  mode-specific estimates and native multimodal suggestions even when media dependencies need installation.
  Manual Backend choices cannot bypass a stored FP8/NVFP4 checkpoint's hardware requirements.
- Add **Backends → Engine add-ons** for explicit media/GGUF dependency installation through `uv`, with maintenance
  guards, logs, cancellation and restart guidance. Preserve the installed Engine and existing package versions;
  never fetch the unrelated PyPI `basal` package. System toolchains remain operator-managed.
- Integrate the selected contributor-inspired Studio designs: Appearance A with Green/Orange skins, inline
  Choice editor A, two-model comparison A, protocol-tabbed API examples B, categorized Settings C, Quick actions C,
  Batch path guidance B and calibration/evidence explanation B. Comparisons make two real Decisions and show
  per-model errors independently; unsupported protocol conversions are refused rather than losing request fields. Invalid
  option-key drafts preserve cost columns through language changes, and editing raw JSON invalidates copied examples.
  Path inspection is explicit, bounded and read-only; it previews the first request, not a full dataset validation.
  Correct MCP examples to the tested Engine's HTTP-only gateway connection and first-loaded-model routing.
  Ideas from [#9](https://github.com/pawelkiszczak/basal-ui/pull/9) and
  [#11](https://github.com/pawelkiszczak/basal-ui/pull/11) are selectively adapted, not merged wholesale;
  [contributors](../CONTRIBUTORS.md) are credited by GitHub nickname. Engine pins remain unchanged.

- basal-ui is its own repository and package: import `basal_ui`, use the `basal-ui` command, and install the
  `basal-ui` distribution separately from the Engine. [Installation](../README.md#install)
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
- The update check covers basal-ui and the engine: Hardware reports a newer basal-ui release, a newer engine
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
