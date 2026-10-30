# Browser app: pick a model for your hardware and try it

`basal-ui` is a local web app in the style of LM Studio: it detects the machine, lists every basal model on Hugging
Face with what this machine can do with it, downloads the one you choose, starts `basal-serve` for it and gives you a
playground for decisions. The catalog covers the official `Remek/basal-*` repositories and the community conversions
of `pawelkiszczak` (basal-1.0 GGUF, MLX 8-bit and oMLX oQ6e) and `agentGreg` (basal-1.5-mini MLX 6-bit and bf16),
marked **community**; suggestions use the official ones only. A weights-only GGUF conversion is served with the
tokenizer, prompt format and calibration of its base repository (the model card's `base_model`, pinned to its
`base_model_revision` when the card has one; `--model <base> --gguf <file>`), whose small metadata files are downloaded
with it; one whose card names no base model is listed as not runnable.

Install from the [README's source instructions](../README.md#install) until the first PyPI release. From that checkout:

```bash
make run ARGS='--port 7860'           # then open http://127.0.0.1:7860
```

The Engine install takes the extra of the machine (`basal[mlx]`, `basal[gguf]`, `basal[fp8]`, `basal[vllm]`, …).
The UI's separate `mcp` extra enables its API server's MCP endpoint; `make install` includes it.

The left menu groups the views by task — **Run** (Playground, Batch), **Models** (Discover, Library, Backends),
**Serve** (API server), **Evaluate** (Inspect, History), **System** (Logs, Settings) — and each view has its own
address (`#overview`, `#discover`, `#library`, `#backends`, `#playground`, `#batch`, `#inspect`, `#api`, `#history`,
`#logs`, `#settings`; the older `#models` and `#manager` open Discover and Library), so the browser's back button and
bookmarks work. Until a first model, example and connection are done, Overview starts with a **Get started**
checklist (hardware detected → Download & load the recommended model → try an example in the Playground → connect
your app; the example step counts once a Decision is answered, the connection once the endpoint or a snippet is
copied while the API server runs); it hides when every step is done or after **Dismiss** (kept in the browser).
**?** opens the keyboard shortcuts; **Settings → Single-key shortcuts** turns that single-key shortcut off.
**Overview** shows the hardware, the
**inference** happening now (updated every second while Overview is open: per model the requests basal-ui is waiting
on by source — Playground, API server, MCP, benchmark — with the longest one's time and basal-serve's running / queued
counters, and running batch, benchmark, calibration and backend-test jobs with their progress; otherwise *Idle* and
when the last decision was), the loaded models (one row each: state, a pulsing dot while it answers, mode, media,
estimated → resident memory, requests and average best-answer probability,
Restart / Logs / Unload) above a memory budget bar, the recommended model for this machine and short summaries of the
disk and the API server; the other views hold the features below. **Discover** (formerly Models) opens Suggested
(default) and All models; **Library** (formerly Model manager) lists your models on disk and loaded, each row with its
next step (**Load** → **Use in Playground** / **Copy endpoint** / **Unload**), then statistics, benchmarks and
calibration. **Settings** holds the theme, the language, the models directory, auto-load, the memory budget, update
checks and presets. The menu's footer shows how many models are ready (and how many are answering), their names, the
API server's URL, **Commands** (the command palette), a light/dark theme button and an **Unload all** button that asks
for confirmation. Deleting a model or revoking a key asks for confirmation in a dialog, and actions report their
outcome in a short notification. On a narrow window the menu becomes a top bar. **Details** on every model opens its model
card: repository and author (base repository for a conversion), family, tier, port, size, media, the mode this machine
would use and why, fit, the revisions in the cache, `basal.json`, the shipped calibration of the mode it is served in
with its certified thresholds, resolved for the active model's revision, mode, quantisation, vLLM preset, prompt
layout and adapter (or the saved load options when unloaded), the `GET /v1/models` entry of a loaded model (mode, option orders, prompt format, vLLM
preset, context) and the README (of the `revision` load option when one is set); `basal.json`, the loaded entry and the
README start collapsed. **Settings → Appearance** switches the theme
(light, dark, or the system's) and the language (**English** / **Polski**, kept in the browser; coded UI-owned model, option,
backend and job messages are translated in Polish; uncoded server errors stay English). Keyboard: Ctrl/⌘+Enter sends
the Playground decision, Ctrl/⌘+K opens the command palette (go to a view or Settings, load, use or unload a model,
Download & load a suggestion, toggle the theme, copy the API endpoint; arrows move, Enter runs, Esc closes),
Alt+1…9 (Option+1…9 on macOS, any keyboard layout) opens the first nine views in menu order and Alt+0 (Option+0) the
tenth (Logs), `?` lists the shortcuts. Overview says when a newer basal release
is out (GitHub's latest release of rkinas/basal, checked at most every 6 hours, 10 minutes after a failed check) and
when new models appeared in the Hugging Face catalog (the offline catalog of the models on disk does not count);
**Check for updates** turns the release check off.

- **Hardware.** NVIDIA GPU (name, compute capability, memory), Apple Silicon (MLX's recommended working set) or CPU
  (60% of RAM), the engines installed (chips such as `mlx`, `gguf`, `vllm`, `sglang`, `media`), and the memory
  available now (free GPU memory on NVIDIA; free + inactive + speculative pages from `vm_stat` on macOS, which leaves
  out reclaimable file cache; `MemAvailable` on Linux).
- **All models.** Every `Remek/basal-*` repository and each `.gguf` file of a `-GGUF` repository, with weight size,
  estimated memory (weights × 1.2 + 1 GB), fit against the budget and *fit now* against the available memory (*fits*
  up to 85%, *tight* up to 100%), and the `basal-serve` mode it would run in, or why it cannot run here and which
  extra fixes that. Offline, the list uses one complete cached snapshot per repository: `main` when complete,
  otherwise the newest complete snapshot. Partial shards are never combined across revisions; repositories with no
  complete snapshot are omitted.
- **Suggested**: cards for the models that fit both the budget and the memory available now, a comfortable fit before
  a tight one. *Recommended*: the basal-2.0 family in the format of the machine (after
  [HARDWARE.md](https://github.com/rkinas/basal/blob/main/docs/HARDWARE.md#recommendation): FP8 on NVIDIA GPUs with compute capability 8.9+ other than data-centre
  GPUs and the RTX 4090, bf16 there, `-MLX-8bit` on Apple Silicon, GGUF `Q8_0` on a CPU with `llama-cpp-python`,
  else bf16), 4.5B if it fits now, else mini. *Best quality*: the largest tier that fits now. *Fastest*: mini.
  *Multimodal*: the largest basal-omni checkpoint this machine can serve with media. Without a reading of the
  available memory the budget fit stands in for it.
- **Download** (a model not on disk yet) fetches only what serving needs (the chosen `.gguf` file of a `-GGUF`
  repository; not the 4.5B's `think/` model) into the models directory and shows the progress in the row; nothing is
  started. In **Discover** the primary action of a model that fits the memory available now is **Download & load**
  (one step; **Download only** stays next to it); a model already on disk offers **Load**, a loaded one **Use in
  Playground** and **Copy endpoint**. Each card and row shows the fit as a meter — *est. X GB of Y GB available* with a
  plain verdict (fits comfortably up to 85 %, tight up to 100 %, too large) — and **How is this estimated?** gives the
  arithmetic (weights × 1.2 + the runtime overhead, 1 GB or the graph engine's). On disk a row also offers **Delete**
  (a loaded model cannot be deleted).
- **⚙ Set up next load** (Discover) opens the load options and saves them for the model without loading it; the next
  load uses them. **⚙ Load options** in Library is the same dialog with **Load**, and a loaded model's **⚙ Restart
  options** (Discover and Library) restarts it with the changed options. The dialog opens on **Recommended** (basal-ui's
  execution mode and settings for this machine; the sections stay hidden) unless the model has Saved load settings,
  and **Custom** shows every section with what is *changed from defaults*; a loaded model's Active load settings are
  listed under it. For media, the mode (eager or vLLM) is chosen when loading from Library.
- **Load** (Discover or Library) runs `basal-serve` for the model as a subprocess on a free loopback port.
  **Several models can be loaded at once**: each gets its own private `basal-serve`; a loaded model's row shows its
  state and **Unload**, and Discover shows a *loaded* chip. A load that would exceed the memory budget is refused (below).
- **Load with media** (Library), for the basal-omni-1.0 bf16 checkpoints (mini and max: images and audio; ultra: images; the
  FP8, GGUF and MLX ports and basal-omni-1.1-max have no encoders): `basal-serve --mode eager --media on`, with
  `--device mps` on Apple Silicon and `--device cpu` on a machine without a GPU, or — chosen in the media load's
  ⚙ Load options on an NVIDIA GPU with `basal[vllm]` — `--mode vllm --media on` ([OMNI.md](https://github.com/rkinas/basal/blob/main/docs/OMNI.md#vllm---mode-vllm)).
  Needs the `basal[media]` extra (pillow, soundfile, scipy, torchvision); the row says so when it is missing. A
  remembered text-only engine mode is not applied to a media load.
- **Settings → Models**: the models directory (default: the Hugging Face cache, `HF_HUB_CACHE`) can be set to any
  writable directory; downloads, the downloaded marks, serving and deletes all use it. The setting is kept in
  `~/.config/basal-ui/settings.json` (`BASAL_UI_CONFIG` moves it). The directory cannot change while any model is
  loaded or downloading: the field is then read-only and says so, and it checks a typed path live (exists, or will
  be created).
- **Library**: the variants on disk, with their size and **Load**, **Load with media**, **⚙ Load options** or
  **Unload**, or why the variant cannot run here, and **Delete**: for a `-GGUF` repository only the chosen `.gguf` file
  goes, and the repository once no `.gguf` is left.
- **Playground**: a model selector (the ready models), the bundled examples (`advanced.jsonl`: one request per
  question type; `media.jsonl`: basal-omni requests with the images and voice clips of `basal_ui/examples/media/` — a
  checkout screenshot whose numbered elements are the options of a `choice` (web agent), a Polish invoice, a revenue
  chart, a Polish complaint call and an English appointment call; `media/make_media.py` regenerates the files), a
  question builder for every type (`choice`, `noul`, `score`, `multi`, `act` with simple costs or a
  cost matrix, `tool`, `classify`, `verify`, `compress`, `extract`, with `max_error` and evidence where the type takes
  them), `facts` and `lang` for the request, or the raw JSON request (which carries `"model"`). Options, labels,
  claims, score levels and yes / no descriptions are edited as rows (key and description; levels in order; `noul` and
  yes / no `act` have two fixed rows): **Add option** or Enter adds a row, Alt+↑ / Alt+↓ or the arrow buttons move it,
  × or Backspace in an empty row removes it, and a hint says when the rows will go out as plain text with hidden keys
  0…n (a row without a key or description, or a key with spaces or colons). Question names must be
  non-blank and unique; requests containing fields or media the builder cannot restore stay visible in Edit JSON mode.
  Changing between English and Polish keeps this tab's Playground draft across the page reload; media is not retained
  and must be attached again.
  Each type has its own
  result view; every answer shows whether it was deferred, whether its confidence clears the model's certified 1% / 5%
  thresholds and how it is calibrated: a gauge draws the confidence against the certified 5% and 1% marks, and **Why?**
  explains the verdict with the certified thresholds and whether the Shipped or a Selected calibration is in use; an answer whose calibration does not hold for it (media answers on a
  text-fitted calibration, `unvalidated` files) gets no automation claim. A truncated state is flagged, and the state
  is shown with evidence spans, kept units and extracted fields marked. A request the builder shows but cannot write
  in its own fields (classify labels with rubrics, keyed criteria, `option_keys`) is sent as it was until that field
  is edited; `multi`, multi-label `classify` and multi `tool` take the set options (objective, threshold, min, max;
  multi and tool also fallback and exclusive keys). Requests go to the selected model's `POST /v1/systemone`. With a
  media server, up to 8 images or audio files (20 MiB each) go along as `media`, with the image `detail`. The media
  examples load their files into the media field, with an audio player for clips; on a model without media they load
  with a notice and cannot be sent. Mark elements on one image to add a `choice` question; a box is drawn with the
  pointer or added with **Add box** and moved or resized with the arrow keys (Shift resizes, Delete removes). Boxes are
  saved as numbered overlays in a marked copy, and their probabilities appear over that image after a decision.
  Saved requests retain the original and marked image and boxes. Evidence spans are disabled while media is attached
  because they refer to spans in the state's text; the raw JSON request still rejects evidence with media. Inspect's
  example list leaves the media examples out (the lens reads text only).
  With two or more ready models, **Compare** sends the same request to the selected model and the one picked next to
  it (both through `POST /api/decide`, so both are recorded in History) and shows the answers side by side with, per
  question, each option's probability on both models and the difference in percentage points. **Copy as code** turns
  the request into cURL or Python for `/v1/systemone` or `/v1/decisions` (the predicate, choice and score questions
  the OpenAI translation takes), or an MCP `decide_many` tool call.

## Load options

**⚙ Load options** next to every Load button (**⚙ Set up next load** in Discover, **⚙ Restart options** for a loaded
model) opens the load options of that
model in collapsible sections (Engine and any section holding a non-default value open; a section you open or close
stays that way for the next dialog in the session; a collapsed section still saves its values):
the **engine mode** (every `basal-serve --mode`, the ones this machine or variant cannot use greyed out with
the reason — CUDA graph modes need an NVIDIA GPU, vLLM / SGLang their extra, MLX Apple Silicon, TPU a TPU VM, Strix
an AMD Strix Halo; Strix and TPU require bf16 Llama-architecture weights, not basal-omni's Gemma or recurrent models.
A ROCm GPU, although PyTorch reports CUDA, is offered none of the NVIDIA-only modes — FP8, NVFP4, vLLM and SGLang;
Ollama and llama-server take the URL of the running service), quantisation, attention kernels,
dtype, the vLLM preset, GPU share and KV-sharing fast prefill, the Gemma 4 eager forward, attention and per-layer
embedding offload (`--eager-forward`, `--eager-attn`, `--ple-offload`), the exit heads directory, determinism, option
orders and the `auto` band, batching, the state and prefix caches, the scheduler and long-request lane, the **prompt
layout** (`--layout canonical`, `state_twice` or `qfirst` for prompt-format-2 models trained on it; default: the
model's basal.json; a non-canonical layout is refused with the state or prefix cache, and a loaded row shows it as a
`layout` chip), the **context
policy** (truncate the middle of the state, or refuse with 422; the share kept from the beginning; `--max-len`), the
CUDA graph engine's kernel switches (`--gemma-attn`, `--merge`, `--lean`, `--tree`, `--batches`, `--readout-layer`,
`--template-prefix`, `--fp8-accum`), `--strict-calibration` and a **revision** (a commit, tag or branch of the
repository: downloaded and served as that snapshot, which calibration and Inspect then read too; a **revision** chip
marks the model in the catalog and on its loaded row). Each field shows only for the modes that read it. **Presets**
save a set of options under a name to apply to any model; the Playground saves requests the same way (**Saved
requests** in the examples list), and both export to and import from one `presets.json`. Only the values that differ
from basal-serve's defaults are
passed, and a combination basal-serve refuses shows its own message on the model's row. **Remember for this model**
keeps them for its next plain Load; a restart and auto-load reuse the options the model runs with. The row lists them
as chips. **Experimental**: **Thinking** (`--think auto`, with the reasoning budget, and the Python of an environment
with vLLM or the URL of a running think server when basal-ui's own has no vLLM; the readout-2 calibration, think
weights, LoRA adapter and the think server's vLLM memory, context and log) for basal-2.0 4.5B
([THINKING.md](https://github.com/rkinas/basal/blob/main/docs/THINKING.md)), and the default **early exit** policy of `--mode fast-exit` (basal-1.0 bf16 on CUDA);
both are refused for other models and modes. A model loaded with them gets **Think** (auto / off / force), **Show
reasoning** and **Early exit** in the Playground head, and each answer that thought shows its reasoning tokens and
time, the calibration it used and the confidence it had without thinking.

## Engine integration in 2.3.1

The CPU packed path is available as **mps** with **Device: cpu** (`--mode mps --device cpu`); the default on a
CPU remains eager. For basal-omni on NVIDIA, the default follows the engine: fast on supported sm_120 Blackwell
workstations or Hopper with FlashAttention 3, eager otherwise. Media retains its eager/vLLM paths.

**MLX packed** is off by default. It is available for Llama basal-1.x / basal-2.0 bf16 or MLX models with MLX on
Apple Silicon, without media. It passes `--mlx-packed`; groups whose shared prefix and all suffixes total at most
512 tokens can use packed rows. Longer groups keep the KV-cache path. Static RoPE is required by the engine;
packed arithmetic can change probabilities slightly from the KV-cache path used for shipped calibration.

**Batch rows** exposes `auto` (the engine default), `compact`, `default`, and `fine`. Compact/fine require the CUDA
graph engine. `auto` chooses compact rows where supported, `default` uses padded graphs, and `fine` captures more
shapes at a longer startup cost. Compact rows require compatible attention kernels and additional GPU memory
(about 6 GB extra in upstream RTX 3090 measurements). A kernel-specific refusal appears verbatim on the model row
and in Logs. These settings, MLX packed, device, and the state-cache size survive Remember, Restart, and auto-load.

**State cache (GB)** budgets GPU memory for longest-prefix reuse: growing states reuse their cached beginning;
edits reuse the unchanged prefix and recompute the suffix. This budget competes with weights and graphs; reduce it
if memory is tight. The UI does not report cache hits.

Loaded rows show the child's `/health` running and queued request counters. The UI samples at most once every five
seconds per child with a 0.5-second timeout, without blocking `/api/status`. Missing counters remain null and display
as unavailable; a failed health probe displays unreachable, never idle. These are sampled counts, not live totals.

Playground and Batch distinguish HTTP 422 invalid input, 503 GPU memory exhaustion/service unavailability, and 500
internal errors. The original diagnostic and available request ID are retained. Shorten input or unload another
model after OOM; inspect logs for internal errors. Requests are never automatically retried. Batch output includes
`status`, `code`, and `request_id` on HTTP errors; benchmark logs retain the first diagnostic per HTTP status.

Calibration jobs can **complete with warnings**. The UI shows the engine's actual FIT-label, CERT-confirmation, and
zero-family strict-check warnings from its saved report, also beside the calibration file. A completed job alone
never means certification was confirmed; inspect the report and per-type thresholds. Failed jobs remain failures,
with their log and any report written before failure retained.

## Batch

**Batch** runs `basal-run` from the app: a loaded model, an input JSONL file (simple items or full `/v1/systemone`
requests, [in the engine docs](https://github.com/rkinas/basal/blob/main/docs/SERVING.md#inference-on-a-jsonl-file)), the output file (default `<stem>.answers.jsonl` next to it, e.g. `items.jsonl` → `items.answers.jsonl`), the
concurrency and, for a `--mode fast-exit` model, an early-exit level. The progress, the summary (items, errors,
seconds, items per second, server p50, accuracy over the items with `gold`) and the answers page by page are shown;
**Download answers** saves the output file. Batch requests go straight to the model's basal-serve: they are not
counted in the statistics or the history. One batch runs at a time; its log is **Logs → job:batch**. When the
answers carry `correct` (items with `gold`), a reliability diagram of confidence against the share correct, with its
ECE, is drawn from the first 1000 rows. Under each path field a live check says whether the file exists (or can be
written; the API server's decision log is appended to, not replaced), how many lines it has and the shape of its first
row, with that row's request fields under **First row**. The check is `POST /api/paths/check` with `{"path", "kind"}`
(`jsonl`, `json`, `output`, `append` or `dir`; [API](API.md#browser-app-path-check)); it reads only the first line of a JSONL file (and returns content only when
that line is a simple item or a request) or the top-level keys of a JSON file. It is part of the browser app's own API
on basal-ui's port, not the API server. Browsers do not reveal a picked or dropped file's path, so there is no file
picker: the fields name files on the machine basal-ui runs on.

## Inspect

**Inspect** embeds `basal-viz` ([in the engine docs](https://github.com/rkinas/basal/blob/main/docs/SERVING.md#see-how-a-decision-is-made))
for a downloaded bf16 PyTorch checkpoint of basal-1.0, basal-1.5, basal-2.0 or Gemma 4 basal-omni (not the recurrent
basal-omni-pico; the MLX, GGUF, FP8 and NVFP4 ports cannot be read layer by layer). Gemma 4 basal-omni needs an engine
whose basal-viz reads it (proposed upstream, [rkinas/basal#75](https://github.com/rkinas/basal/pull/75)); on other
engines the Inspect list shows it disabled with that reason. **Start** loads its own model copy into basal-ui's
process (MPS on Apple Silicon, CUDA on NVIDIA, else the CPU; its memory is counted in the budget until **Stop**), with
the shipped calibration or one fitted in basal-ui and the model's own option orders or 1 or 2, and the page appears
below. basal-viz reads text only; requests carrying media are refused. **Inspect this decision** in the Playground
sends the current request there (with `facts` and `lang`) and lists what differs from the served decision: other
weights of the same family and tier, the engine, option orders, calibration temperatures, thinking and early exit
(explained at the final layer), task questions left out. A decision of another model or with only task questions is
refused with the reason.

## Memory budget

The budget is the hardware budget shown under Hardware unless set in **API server → Memory** (at least 1 GiB), and
**Keep free** (default 2 GiB) is held back from it. A load is refused with `409 over memory budget: … loaded + …
needed > …` when the estimates of the models already loaded or loading plus the new model's estimate exceed budget −
keep free; unload a model or raise the budget. The Overview bar shows each model's resident memory (summed over the
`basal-serve` process group with `ps`, so engine workers count; on Windows the estimates stand in), the committed
estimates and the memory available now. On Apple Silicon, MLX and MPS allocations live in unified memory and may show
only partly as resident memory; the estimate is what the budget check uses.

## Statistics and benchmarks

**Library → Statistics** lists, per model, the decisions basal-ui answered (playground, API server, MCP): requests,
questions, errors, average best-answer probability (the answer's `confidence`; `set_confidence` for `multi`; the
largest probability for `tool`), mean and p95 latency (over the last 1000 requests), input tokens and last use, with
one line per question type. They are kept in `<settings stem>.stats.json` next to the settings file, survive restarts
and can be reset per model or all at once. Each model's numbers are stat tiles with a histogram of its last 200
latencies and a bar per question type (Overview shows the same tiles under each loaded model, and the memory budget as a
meter split by model). The newest preset benchmark of each model is drawn as accuracy per file and p50 / mean / p95
latency bars, with sparklines of accuracy, p50 and decisions per second over the kept runs. A reset also discards older, unflushed counts from other instances sharing
the settings file; Decisions recorded after the reset remain counted.

**Benchmark** measures one loaded model at a time: 5 warm-up requests, 30 sequential requests (p50 / p95 / mean
latency) and 120 at concurrency 8 (decisions, questions and input tokens per second, errors). Every item with a `gold`
answer is scored once (accuracy overall and per file). The set is either the bundled examples (44 items in
`basal/examples/`) or your own JSONL file or directory: [BENCHMARK_SETS.md](BENCHMARK_SETS.md). The same block runs
three other measurements, one at a time:

- **Load test**: `basal-loadtest` against the loaded model with your sequential / concurrent counts, concurrency,
  questions per request, synthetic states of about N tokens (with a share of long ones and a seed) and, for a
  `fast-exit` model, an early-exit level.
- **Determinism check**: `basal-loadtest --determinism N`: N requests alone, then the same N mixed with N others; a
  model loaded with **Deterministic** (load options) must return identical probabilities.
- **Compare engine modes**: `basal-bench` in basal-ui's own process for a downloaded model: every chosen mode (the
  ones this machine can run, plus `eager-fp32`, `vllm@deterministic` and `fast-exit@<policy>` where available) is
  loaded, measured (latency with one and both option orders, decisions per second, agreement with the first mode,
  accuracy, load time, memory) and freed in turn. Its memory is counted in the budget while it runs (twice the
  estimate with `eager-fp32`); a mode that fails is listed with its error.

The last 20 results per model stay in the stats file and are shown side by side. Benchmark requests are not counted
in the statistics or the history.

## Custom calibration

**Library → Calibration** runs `basal-calibrate` for a downloaded model on your labelled requests
([CALIBRATION.md](https://github.com/rkinas/basal/blob/main/docs/CALIBRATION.md#input); the simple `basal-run` items with `gold` work too) at the 1% and / or 5%
`max_error` targets. For decision questions basal-ui starts a temporary `basal-serve --no-calibration` of the model on
loopback (its memory is counted in the budget while it runs), calls `basal-calibrate --url` on it and stops it. Task
questions (`classify`, `verify`, `extract` with `gold`) are certified on the answers as served, so with them
`basal-calibrate` runs the engine in basal-ui's process instead (the model's serving mode and load options; its memory
is reserved in the budget until it ends; Cancel takes effect when it finishes). Every problem of the input is reported
with its line before a model is loaded. The job's output is under **Logs → job:calibrate**. The files go to
`calibrations/<org>--<model>/` next to the settings file, each with its report (`.report.txt`); the table compares them
with the shipped file (decisions per type, certified thresholds of the types and tasks, automation on CERT). Below it,
the calibration in use (selected, else shipped) is drawn: its certified 1% / 5% thresholds per type on a 0–1
confidence axis, the share of CERT decisions automated at each target, and the leave-one-family-out ECE per type
against its reference. A reliability diagram is not drawn here: the calibration file keeps no binned confidence data
(Batch draws one for labelled answers).
**Import** copies a file fitted elsewhere (with its report next to it) into the list; one fitted on another model or
revision shows basal-serve's warning. **Use** selects one for the model: basal-ui then starts its `basal-serve` with
`--calibration FILE`, and a loaded model shows **Restart to apply**. **Advanced** passes basal-calibrate's own
controls: `--strict` with a `--reference`, `--min-per-type`, `--min-blend`, `--min-family`, `--min-per-set`,
`--concurrency`, the file to compare with (shipped, none: `--no-calibration`, or a path; a path and a `--reference`
file are checked before the run starts), `--report-only` (the run uses the same engine as a fit, the report opens, no
file is written) and `--save-rows`, which keeps the uncalibrated probabilities next to the file; such a file gets
**Refit**, a `--from-rows` fit on them without loading any model (no load options, no memory check).

## History and logs

**History** lists every decision basal-ui answered — from the playground, the API server and MCP — newest first:
time, model, source, a preview of the state, the answer of each question with its confidence, latency and HTTP
status. Filter by model and source, search the state, open a row for the full request and response (its time is a
button, so rows open from the keyboard and keep focus across refreshes), copy the request
or **Replay in Playground** (a converted request in the Playground with its model selected; replay only fills the form).
Media payloads, including nested image/audio payload objects in recorded Decisions-style requests, are replaced by
their size. Replay is blocked with an explanation when an original attachment is missing; redaction is not a promise
that the API accepts every recorded media shape. Ordinary structured context and question descriptions, including
fields named `data`, are preserved. The last 2000 decisions are kept in `<settings stem>.history.jsonl` (owner-only)
next to the settings file. A live-only History view keeps the newest 500 rows. **More** enters older-page browsing:
explicitly loaded pages and their cursor are kept across polls until the view or filters are reset.
**Clear history** empties the file before clearing the live view; a failed write leaves the existing records available.
`GET /api/history?order=asc` returns oldest first for incremental consumers;
`order=desc` remains the default, and `after` cursors outside the retained window return retained records only.

**Logs** shows the output of each `basal-serve` (the last 1000 lines per model, kept after unload for the last 10
unloaded models) and basal-ui's own event log (loads, downloads, readiness, errors, unloads, API server starts and
stops, benchmarks, auto-load decisions). `#logs/<model id>` opens one model's tab (the Overview row's **Logs** link);
follow mode keeps the view at the newest line; the text can be copied or downloaded. Logs are kept in memory only.
`GET /api/logs` returns a buffer `generation`; send it back as `generation` with `after` to detect a replaced buffer
and receive its retained lines from the start.

## API server

One port serves every loaded model. The API server runs inside basal-ui and forwards each request to the private
`basal-serve` of the model it names, so every request body must carry `"model"`: `400 MODEL_REQUIRED` without it,
`404 MODEL_NOT_FOUND` for a model that is not loaded (both list the loaded models). `GET /v1/models` lists the ready
models (the basal `models` entries and the OpenAI `data` list), `GET /health` names them, and `GET /v1/stats` returns
the statistics above (admin key only when keys are required — that is, basal-ui's own key). Details:
[API.md](API.md#several-models-behind-basal-ui).

| Setting | Default | Effect |
|---|---|---|
| Serve the API | Off | Off, the models answer only basal-ui. On, the API server listens on the configured host and port. |
| Exposure | This computer (`127.0.0.1`) | Local network binds `0.0.0.0`; use this only when remote clients need access. |
| Port | `8000` | TCP port of the API server; valid range is 1024–65535. |
| Require an API key | Off | Requires a named key on every API route except `/health`. Supports `Authorization: Bearer <key>` and `X-API-Key`. |
| Max context length | `4096` | Passed to each vLLM and SGLang `basal-serve` (`512`–`131072`). |
| CORS origins | Empty | Optional allowed browser origins, one per line; `*` allows every origin. |
| Decision log | Empty (off) | Every `basal-serve` appends its decisions (propensity records) to this writable file. |
| MCP at `/mcp` | Off | Adds the MCP streamable-HTTP endpoint when the MCP extra is installed. |
| Auto-load models | Off | Loads every model that was loaded when basal-ui last ran (in load order). |

**Security.** The server is loopback-only by default. Local-network exposure is unauthenticated unless **Require an API
key** is enabled; use a key before selecting **Local network**. Keys are generated with the `bsl_` prefix, stored as
SHA-256 hashes in the settings file and keys file, and their plaintext is shown only once when created. Copy it then;
it cannot be retrieved later. Revoking a key rewrites the live keys file and takes effect without a restart. Each
`basal-serve` child listens on loopback only and takes no key: only basal-ui, which starts it and knows its private
port, reaches it. basal-ui's own internal admin key (the only key `/v1/stats` accepts) is not exposed in the UI.

**Restart server** restarts only the API server (instant; the models stay loaded) and applies the saved host, port,
key, CORS and MCP settings; until then the status line shows **Restart to apply**. **Max context length** and
**Decision log** belong to each `basal-serve`: a model loaded before such a change shows **Restart** in its Overview
row, which reloads only that model. The app checks the chosen bind port before starting the API server and reports
`port <port> on <host> is in use` if another process owns it.

The **Connect** panel provides copy-ready examples for curl (both `/v1/systemone` and `/v1/decisions`), Python
(httpx), TypeSafe and OpenAI SDKs, LiteLLM's `hosted_vllm` provider, and MCP, each with the selected model's id in
`"model"`. When authentication is disabled, examples omit API-key headers. Turn on MCP to use the streamable-HTTP
endpoint at `/mcp`: its `decide` and `decide_many` tools serve the model named in the tool's `model` argument, else
the first loaded model (the engine's MCP tools list no models and its `basal-mcp` bridge takes no `--model` until the
model-argument proposal ships upstream). A matrix above the snippets compares what `/v1/systemone`, `/v1/decisions`
and `/mcp` offer (question types, calibration metadata, evidence, request ids, typed errors, auth), and **MCP
clients** gives the configuration for Claude Desktop (through the `basal-mcp` bridge), Claude Code, Cursor, VS Code
and Open WebUI. Full request and response shapes, error envelopes
and compatibility details are in the [HTTP API reference](API.md).

## Serving models from basal-ui

1. Start basal-ui and load one or more models. In **API server**, turn on **Serve the API**.
2. Choose **This computer** (`127.0.0.1`) for local clients. Choose **Local network** (`0.0.0.0`) only when another
   device must connect; enable **Require an API key** first. An unauthenticated LAN listener is accessible to other
   devices on that network.
3. Set the port (default `8000`). Enable **Require an API key**, then save and click **Restart server**.
4. Under **API keys**, enter a name such as `laptop`, choose **Create key**, and copy the displayed `bsl_...` secret.
   It is shown once; basal-ui cannot display it again.
5. Use **Connect** for a copy-ready client snippet. For example:

   ```python
   import httpx

   r = httpx.post(
       "http://127.0.0.1:8000/v1/systemone",
       headers={"Authorization": "Bearer <copied key>"},
       json={
           "model": "Remek/basal-2.0-mini-MLX-8bit",
           "state": "A user asks for a refund.",
           "questions": {
               "route": {
                   "type": "choice",
                   "instructions": "Which team?",
                   "criteria": {"billing": "Billing", "support": "Support"},
               }
           },
       },
   )
   print(r.json()["answers"]["route"])
   ```

6. To revoke access, choose **Revoke** beside the key and confirm. basal-ui rewrites the key file; the API server
   refuses that key on its next request without a restart.

The default settings file is `~/.config/basal-ui/settings.json`; set `BASAL_UI_CONFIG` to change its path. Next to it,
each running basal-ui writes one key file: `<settings stem>.keys-<pid>.json` (for example, `settings.keys-1234.json`),
the API keys from the settings plus that process's internal admin key, read by the API server. It is owner-only
(`0600`) and holds SHA-256 hashes, never secrets. The `basal-serve` children read no key file at all — loopback and
their private port are their only protection — and the internal key never leaves basal-ui's process. Two basal-ui instances may share one settings
file: a key created or revoked in either is written into every matching live instance's key file, the files of exited
instances are removed, and each instance deletes its own file when it quits.
Filename stems are matched literally, so glob characters such as `[` or `*` in `BASAL_UI_CONFIG` do not prevent
creation or revocation from reaching the running instances.
The statistics (`<stem>.stats.json`) and history (`<stem>.history.jsonl`) live next to it as well; both are written under the settings lock, so two
instances add their counts up and neither rotation drops the other's decisions (each History view shows only the
decisions its own instance answered or found in the file at startup).

**Auto-load.** Enable **Load the models when basal-ui starts** (Settings → Models; saved at once) to remember every model that reached ready (an unload
forgets it; quitting basal-ui does not). On startup, basal-ui loads them in their load order and starts the API server
with the current settings; a model that is unknown or cannot run any more is skipped and noted in the Logs view.

**Troubleshooting.**

- **Port in use:** choose another port, save, and restart the server. basal-ui checks the configured bind address
  before starting the API server and reports `port <port> on <host> is in use`.
- **400 MODEL_REQUIRED / 404 MODEL_NOT_FOUND:** add `"model": "<id>"` with an id from `GET /v1/models` (or the
  Overview) to the body; the model must be loaded and ready.
- **401 Unauthorized:** check that **Require an API key** is on and send the copied secret as
  `Authorization: Bearer <secret>` (or `X-API-Key: <secret>`). Revoked keys stop working immediately; create a new
  key if the original is lost.
- **409 over memory budget:** unload a model, or raise the budget / lower **Keep free** under **Settings → Memory**.
- **CORS preflight returns 400:** add the browser page's exact origin (scheme and host, with any port) to **CORS
  origins**, save, then restart the server. A foreign origin is rejected without an allow-origin header, and a browser
  request (not a preflight) from it is refused with 403 `ORIGIN_REFUSED` before the key check, so a web page cannot
  drive even a keyless server.
- **MCP extra missing:** install `basal-ui[mcp]` in the same environment, then enable MCP and restart the
  server. The UI reports when the extra is unavailable.
- **Settings file is not valid JSON:** the app still opens and shows the settings as empty, but every change is
  refused with the file's path; fix or delete the file by hand, then change settings again.

For API routes, request and response formats, authentication details and compatibility notes, see the
[HTTP API reference](API.md). The Connect panel also includes TypeSafe, OpenAI, LiteLLM and MCP examples.

The browser app binds `127.0.0.1` by default and refuses changes (load, delete, settings) sent from other web origins,
so a web page cannot drive it through the browser; `--host 0.0.0.0` lifts the host check and prints a warning.
Machine-compatibility rules have fixture coverage. Real-model/browser verification and its hardware limits are
recorded in the [verified development gate](E2E.md#verified-dev-gate-2026-10-29).
