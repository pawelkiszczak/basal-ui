# basal-ui API server

One port for every model loaded in basal-ui. The API server inside basal-ui checks the API keys and CORS, stamps
request ids, translates OpenAI Decisions and forwards each request to the private `basal-serve` of the model it
names; the request and response bodies are the engine's own, so the full request body schema, endpoint list and
question-type reference live in the [engine's API reference](https://github.com/rkinas/basal/blob/main/docs/API.md).
Turning the server on, creating keys and the settings table: [USAGE.md](USAGE.md#api-server).

Trust boundary: the API server and every model's `basal-serve` listen on loopback by default, and loopback is not
isolation from other users of the same machine — any local account can reach a keyless server. Serve without a key
only on a host whose users you trust. Backend URLs and interpreters set in a model's load options (`ollama_url`,
`llamacpp_url`, `think_url`, `think_python`) are contacted or run with basal-ui's own privileges.


## Model server limits and health

The model options endpoint reports availability of each server limit in `limits_available`. The status response reports stalled health and `engine_call_s` when supplied by the Engine, plus its `long` running and queued counts. Total running and queued include the long lane. Engine refusals preserve their status and code on System One and Decisions routes, including aliases; `Retry-After` is forwarded when present. The forwarding read timeout is at least the Active request timeout plus 30 seconds.

## Local app management API

`GET /api/memory` and `PUT /api/memory` expose `idle_unload_minutes` under memory settings: `null` disables idle unloading (default), otherwise a whole integer from 1 through 9,007,199,254,740,991 minutes is required. Changing the timeout starts a fresh countdown for already-loaded models. `GET /api/status` reports idle unloads in `idle_unloads`, keyed by model ID with `code` (`idle_unload`), `minutes` and Unix time `at`; a new load clears its entry. Idle unloading keeps remembered models and does not reload on a request.


These routes live on the **UI port** (default `7860`), not the external API-server gateway port.

`GET /api/models?backend=automatic` returns the model catalog and suggestions. `recommendation_backends` contains
`{id,name}` choices for Automatic and detected machine-compatible local Backends; `recommendation_backend` is the
selected id. Unknown/unavailable ids return 400. The existing `models` rows keep their normal assessment; selecting
a Backend changes `suggestions`, not Saved load settings or the global catalog filter.

Each suggestion carries `slot`, `id`, English `why` plus `why_code`/`why_params`, `mode`, `media`,
`est_memory_bytes`, `fit` and `fit_now`. A multimodal suggestion also carries `media_available`, nullable
`media_reason`, `media_reason_code`/`media_reason_params`, and `media_setup` (`#backends` when dependencies are
missing). A missing add-on is not represented as a working media load. `recommendation_note` explicitly identifies
format/tier rankings as heuristics rather than benchmarks. Physical-memory estimates do not reserve capacity;
`POST /api/load` separately enforces configured budget/reserve and active commitments.

| Route | Contract |
|---|---|
| `GET /api/addons` | Current Python environment and `media`/`gguf`/`finetune` inventory: declaration, compatibility, installed/import status, Engine version/source, selected requirements, command and coded unavailability reason. Unavailable provenance produces a coded inventory explanation, not a replacement Engine. |
| `POST /api/addons` | `{"extra":"media","confirmed":true}` (or `gguf` / `finetune`) starts `engine-addon`; 202 with job state. Invalid extra/confirmation is 400; unsafe/busy environment is 409, with `code: addon_install_refused` and diagnostic parameters. Package names/commands/URLs cannot be supplied by the request. |
| `DELETE /api/addons` | Requests cancellation through the existing Jobs API. |
| `GET /api/attach` | Returns `{"available":boolean,"bases":[...]}`; bases are empty unless `engine.feature("attach")` is available. |
| `POST /api/attach` | `{"base":"Qwen/Qwen3-4B","revision":null,"format":1}` starts the normal base download pinned to `1cfa9a7208912126459214e8b04321603b3df60c` and an `attach` job; format is 1 by default or 2, subject to the Engine tokenizer check. Unsupported bases/format are 400, duplicate or busy job is 409, unavailable Engine feature is 400. |
| `DELETE /api/attach` | Cancels the attach job; an in-flight shared download drains before the job stops. |
| `DELETE /api/attach/{id}` | Removes the record and verified owned directory; returns owned `freed_bytes` and `retained_directory` if ownership cannot be verified. Returns 409 while busy and 404 for unknown ids. Shared base weights remain cached. |

If removal persists the record change but cleanup fails, the response includes `cleanup_error: true`,
`retained_path` naming the staging directory, and conservative `freed_bytes: 0`. Inspect the retained directory
before removing it manually; linked base weights remain in the cache.

The attach job and download progress appear in `GET /api/status` under `jobs["attach"]` and `downloads[base]`;
its log is available at `GET /api/logs?source=job%3Aattach`. The generated model is listed by `GET /api/models`
and can be loaded and calibrated like other catalog entries.

Progress is in `GET /api/status` → `jobs["engine-addon"]`; logs use
`GET /api/logs?source=job%3Aengine-addon` and its existing cursor/generation contract. Success returns
`result: {"restart_required":true,"extra":"media","engine_version":"..."}`. While installation is active,
model-mutating UI routes return 409 / `addon_busy`; Fleet leases and job starts also enforce maintenance atomically.
The installer changes only declared extra dependencies in the current virtual environment, constrains existing
versions, never requests/reinstalls the Engine and verifies its identity afterward. Package changes are not
transactionally rolled back on failure/cancellation. See [the operator precautions](BACKENDS.md#installing-engine-add-ons).
These privileged local routes retain the UI's Host/Origin protections; loopback is not isolation from other OS users.

### Fetch a public page for Playground

`POST /api/fetch` on the UI port accepts `{"url":"https://example.com/"}` and returns `url`, `final_url`,
`content_type`, actual response `bytes`, `truncated` and typed `state`. It fetches anonymously without cookies,
credentials, proxy settings or automatic redirect following. Every destination is checked for public unicast
addresses before connecting; mixed public and non-public DNS results are refused. Responses are limited to 4 MiB,
three redirects and 10 seconds for the entire operation, and text is truncated at 200,000 characters. JSON remains
structured and is refused if invalid, nonfinite, contains a number that cannot survive the browser round trip, or is too large for the state limit; unsupported content types and
content encodings are refused. Errors have English `detail` and a translation `code`. Cancelling or disconnecting
the request and shutting down the app terminate the owned fetch process.

`POST /api/decide` accepts optional top-level `source_url` as Playground metadata. History stores it with the
recorded request, but it is removed before forwarding to the model; nested State fields remain unchanged. The
external gateway and model API do not define this UI-only metadata as a request field.

### Fine-tuning

`GET /api/finetune` reports availability and known model IDs; `?id=<model>` also returns that model's results, including incomplete or missing outputs. Availability reasons use `finetune_engine`, `finetune_addon`, `finetune_restart` or `finetune_platform`. The records survive an Engine downgrade. A ready row contains trusted `name`, `output`, `base` and `adapter` paths plus Native's `train_decisions`, `heldout_lines`, `scored`, `skipped`, `top1_before`, `top1_after`, `steps` and `seconds`. Null accuracy means no scored decisions; zero accuracy is a value. A training report is not a selectable calibration.

`POST /api/finetune` accepts `id`, `input`, `out_name`, `epochs`, `lr`, `rank`, `alpha`, `holdout`, `max_len`, `grad_checkpointing` and optional `max_steps`; it returns 202 and the `finetune` job state with preflight counts. Input is semantically checked and copied privately before admission. The job uses local weights offline, reserves twice the estimated memory until its child exits, writes a private output without `--force`, and logs to `job:finetune`. `DELETE /api/finetune` requests cancellation.

`POST /api/finetune/delete` takes a server-known `id` and `name`, never a client path. It rejects active adapter users and jobs; a successful response reports `removed_bytes` as logical regular-file bytes removed, not physical disk reclaimed. Before cleanup, a `cleanup_pending` record is persisted with the existing ownership information. Incomplete cleanup reports zero and a server-derived `retained_path`; the pending result remains visible after reload and can be deleted again by its ID and name. Registry errors retain recoverable ownership, including when the files are already gone. `GET /api/models/options` includes ready, base-associated `adapters` while retaining the custom adapter field. Saved options take effect on the next load; a running child retains its Active settings. Errors carry specific `finetune_*` message codes and diagnostic parameters.

### Batch presets

`GET /api/batch/presets` returns the Engine's ready question names and supported language values:
`{"available":true,"presets":["..."],"langs":["auto","pl","en"]}`. On Engine 2.4.1 it returns
`available: false`, empty lists, a `reason`, `reason_code` and installed `version`; it does not import the 2.5-only preset module.

`POST /api/batch` accepts optional `preset` (one of those names) and `lang` (`auto`, `pl` or `en`). `auto` is the
default and is inferred by the Engine from each state's language; explicit `pl` or `en` passes `--lang`. With a
preset selected, JSONL lines with neither `questions` nor `question` are expanded with the Engine's preset questions
into a private temporary input for basal-run. The original input is unchanged.
Unknown presets or unsupported language values return HTTP 400.

### Batch resume

`GET /api/batch/capabilities` reports boolean `restart` and `stop_after` probes for the installed `basal-run`.
`POST /api/batch` accepts `choice` (`resume` or `restart`) and a non-negative integer `stop_after` (default 16;
0 writes individual errors). With resume support, a `.part` or `.part.json` at the output returns 409 with
`code: batch_choice` unless a choice is supplied. An orphan sidecar cannot be resumed; Restart explicitly replaces
it. Resume is checked by the Engine against input, request options and server identity. A nonzero exit retaining a
`.part` reports `phase: stopped_resumable`, `done`, `total` and the Engine's error in the Batch job in
`GET /api/status`; its `result` retains the original Batch request for an explicitly confirmed Restart. Otherwise failure remains `error`. Older Engines retain their previous Batch behavior.

### Decision servers

`POST /api/decision-servers` accepts `{"name":"Local", "url":"http://127.0.0.1:11434", "key":null, "consent":false}` and returns 201. `consent: true` is required for hosts other than loopback and link-local; missing consent returns 400 with `CONSENT_REQUIRED`. URLs containing credentials, query strings or fragments are refused. `GET /api/decision-servers` lists server state and reported models without keys; `DELETE /api/decision-servers/{id}` removes the server, its models and key. Secrets are held in an owner-only `<settings stem>.decision-secrets.json` sidecar and are never exported in settings.

Settings and the secret sidecar are each replaced atomically under the settings lock, with rollback on ordinary write
failures. A process crash between the two replacements can leave a stale sidecar entry or a server without its key;
the pair is not a cross-file transaction.

`GET /api/status` marks model rows `kind: basal` or `kind: comparison`; a Comparison row may include `reported_memory_bytes` and never consumes the basal budget. Comparison models may be sent to the UI's `/api/decide` and `/api/batch` only. Requests outside the comparison subset return `UNSUPPORTED_FOR_COMPARISON` before upstream inference; Comparison Batch includes the source line in the refusal. Playground and Comparison Batch Decisions are recorded with `server` in History and attributed to the Comparison model in Usage and rolling traffic. Ordinary basal Batch remains unrecorded.

Comparison choice and score responses must include a complete probability distribution over the requested options. Distributions must total 1 within an absolute tolerance of 0.001; a reported noul value must agree with its true probability within the same tolerance. Probabilities are not normalized or rewritten; invalid responses return 502.

The API server gateway `/v1/models`, health, explicit model routing and MCP exclude Comparison models. For an endpoint ending in `/v1/decisions`, a 404 on `/v1/models` uses the server display name as the upstream model name; only the representable subset is translated.
### Batch path inspection

`POST /api/paths/check` inspects a path on the **app server's machine**, not the browser's machine:

```json
{"path":"/Users/me/requests.jsonl","kind":"jsonl","preset":"triage"}
```

`kind` is `jsonl` or `output`. An invalid kind/path, including a NUL character, returns HTTP 400. A well-formed
inspection request returns HTTP 200 even when the file is missing or unsuitable: inspect `ok` and `code`.
Common facts are `path`, `kind`, `exists`, `is_file`, `is_dir`, `ok` and `code`.

For JSONL input, the response reports byte size, non-empty line count and whether that count is capped. It
inspects the first non-empty row for request/label shape, question types and a preview; it does **not** validate
every request in the file. Scanning stops at 100,000 non-empty rows or 64 MiB; the first row must fit within 1 MiB.
A blank prefix exhausting the scan limit is not reported as an empty file. Preview text is limited to 300 characters
plus an ellipsis when truncated.
When the selected Batch preset is included as `preset`, a state-only first row is previewed as a request using that
preset's questions. Without it, the same row is unsuitable.

For output, the response reports parent-directory existence, writability and whether an existing file would be
overwritten. These are advisory facts at inspection time; permissions and files can change before Batch runs.
Inspection never writes files or runs inference. This privileged local route has the same Host/Origin boundary
as the other management routes; it is not a filesystem sandbox.

### Benchmarks

`POST /api/benchmark` accepts `{"id":"<downloaded model id>","kind":"identity","dtype":"bf16"}` or
`{"id":"<downloaded model id>","kind":"order_check"}`. Identity `dtype` is `fp32` or `bf16` (required) and
requires the fast execution mode to be available for that model and host. `order_check` requires Engine 2.5's
`order_check` feature probe; otherwise the request returns HTTP 400. Both start the shared `benchmark` job, reserve
memory while running, write logs under `job:benchmark`, and save results with `GET /api/benchmarks`. Identity keeps
per-path outcomes, deltas and Engine identity; Order check stores the Native report in `report`. The accepted
benchmark kinds are `benchmark`, `loadtest`, `determinism`, `modes`, `identity` and `order_check`.

## Several models behind basal-ui

basal-ui's API server ([USAGE.md](USAGE.md#api-server)) serves every model loaded in the app on one port and
routes by the request's `model`, which is therefore **required** there, on `/v1/systemone`, `/v1/basal`,
`/v1/decisions` and `/decisions`. Use a model id from `GET /v1/models` (the variant repository id, for example
`Remek/basal-2.0-mini-MLX-8bit`). The body is otherwise the engine's own System One body; the answer is the model's
response.

| Case | Status | `code` |
|---|---|---|
| no `model` (or not a string) | 400 | `MODEL_REQUIRED` — the message lists the loaded models |
| `model` not loaded or not ready | 404 | `MODEL_NOT_FOUND` — the message lists the loaded models |
| the model was unloaded while the request arrived | 409 | `MODEL_NOT_READY` |
| its `basal-serve` cannot be reached | 502 | `UPSTREAM_ERROR` |
| no child response within the forwarding read timeout | 504 | `UPSTREAM_TIMEOUT` |
| `Content-Type` is not `application/json` | 415 | `UNSUPPORTED_MEDIA_TYPE` |
| the body is over 256 MiB | 413 | `REQUEST_TOO_LARGE` |
| a browser `Origin` outside the **CORS origins** setting | 403 | `ORIGIN_REFUSED` |

Errors use the basal envelope (`{"error", "code"}`) on the System One paths and the OpenAI envelope on the Decisions
paths, like basal-serve's own errors.

The forwarding read-inactivity timeout is 600 seconds, not a total request deadline. Connection, write and pool
timeouts remain 120 seconds. When an active Engine `request_timeout` is available in the Runner's load options,
the read timeout is at least that deadline plus 30 seconds; exposing that Engine option is tracked by
[#65](https://github.com/pawelkiszczak/basal-ui/issues/65). Supported Engines 2.4.1–2.5.1 do not expose that
deadline, so 600 seconds is a fixed fallback and does not guarantee completion for every workload. Engine-supplied
errors retain their status and code; `UPSTREAM_TIMEOUT` identifies a timeout generated by basal-ui. The same read
timeout applies to Playground and MCP forwarding. Benchmarks retain their own 300-second HTTPX timeout.
Configure client timeouts accordingly.

After a gateway client disconnects, basal-ui cancels its forwarding await, closes its upstream HTTP connection,
releases its lease and records a 499 `CLIENT_DISCONNECTED` failure in history and error counts. A read timeout is
recorded as 504. Neither event guarantees that the Engine stopped computing. In supported Engine sources
([2.4.1](https://github.com/rkinas/basal/blob/v2.4.1/basal/server.py),
[2.5.0](https://github.com/rkinas/basal/blob/v2.5.0/basal/server.py),
[2.5.1](https://github.com/rkinas/basal/blob/v2.5.1/basal/server.py)), `systemone` awaits `srv.serve` without
monitoring client disconnect, while model calls run in an executor. Reliable per-request cancellation and removal
of queued work therefore require Engine support; basal-ui does not terminate a shared model process to cancel one
Decision. A disconnect before forwarding starts is not recorded as a Decision. If the upstream response or timeout
has already arrived and its record is being persisted, that outcome is recorded instead of 499 even if the client
disconnects during persistence; 499 identifies cancellation while forwarding is still in progress.

`GET /v1/models` lists the ready models in both shapes: `models` holds each model's basal-serve entry (as above) and
`data` the OpenAI list (`{"id", "object": "model", "created", "owned_by": "basal"}`), with `"object": "list"`.
`GET /health` answers `{"status": "ok", "models": [ids]}`.

`GET /v1/stats` returns basal-ui's statistics: `{"models": {id: {"requests", "questions", "errors", "input_tokens",
"avg_latency_ms", "p50_ms", "p95_ms", "avg_best_prob", "first_used", "last_used", "by_type": {type: {"questions",
"avg_best_prob"}}}}, "benchmarks": {id: [results]}}`. With **Require an API key** it needs an admin key (only
basal-ui's internal key is one; user keys get 403), otherwise it is open like `/v1/keys`.

### Prometheus metrics

`GET /metrics` returns Prometheus 0.0.4 text on the API server port. When keys are required, it requires an admin key, like `/v1/stats`. Lifetime counters can decrease after a statistics reset. Rolling 15-minute counts are gauges because they can decrease as the window advances or after a reset. Each ready native Engine child's available text metrics are included with its loaded ID as a `model` label. Samples are grouped by metric family, including histogram and summary subseries. Children that do not respond within two seconds, return a non-text response or do not support metrics are skipped; Cascades and Comparison models are never scraped.

The basal-ui metric names are:

| Metric | Labels | Type | Unit |
|---|---|---|---|
| `basal_ui_requests_total` | `model` | counter | requests |
| `basal_ui_questions_total` | `model` | counter | questions |
| `basal_ui_errors_total` | `model` | counter | failed requests |
| `basal_ui_input_tokens_total` | `model` | counter | input tokens |
| `basal_ui_latency_seconds` | `model`, `quantile` (`0.5` or `0.95`) | gauge | seconds |
| `basal_ui_latency_seconds_avg` | `model` | gauge | seconds |
| `basal_ui_traffic_requests_total` | `endpoint`, `method` | gauge | requests in 15 minutes |
| `basal_ui_traffic_errors_total` | `endpoint`, `method` | gauge | failed requests in 15 minutes |
| `basal_ui_models_loaded` | none | gauge | ready models |

Latency quantiles use at most 1,000 retained samples per model, not the complete lifetime distribution.
Average latency uses lifetime recorded requests. Unavailable latency values are omitted.

To enable authenticated scraping, create a secret file accessible to both basal-ui and Prometheus and start basal-ui with its contents in `BASAL_UI_ADMIN_KEY` (set **Require an API key** in the API server settings):

```sh
umask 077
uv run --no-project python -c 'import secrets; print(secrets.token_urlsafe(32))' > /secure/path/basal-admin-key
BASAL_UI_ADMIN_KEY="$(cat /secure/path/basal-admin-key)" basal-ui
```

Keep the secret file readable only by the operator and Prometheus. Set the environment variable before starting basal-ui; an unset or empty value generates a random internal key. Only its digest is saved in the per-process key file; it is not exposed in the UI. Restart basal-ui to change it. Prometheus uses the same secret file below (omit `authorization` when keys are disabled):

```yaml
scrape_configs:
  - job_name: basal-ui
    static_configs:
      - targets: ["127.0.0.1:8000"]
    authorization:
      type: Bearer
      credentials_file: /secure/path/basal-admin-key
```

### Recent Decision traffic

The local UI app (not the API-server gateway) exposes `GET /api/traffic?minutes=15` and
`GET /api/traffic?minutes=60`. Unsupported integer windows return HTTP 400 with an English error; invalid query syntax
uses FastAPI's HTTP 422 response. This is an in-memory, process-local view of **recorded Decision attempts**, not all
HTTP traffic. An attempted child forward that fails (including a transport/connection error) counts as a request and
an error; its latency includes the failed attempt. UI polling, UI/gateway rejections before a child forward, Batch,
benchmarks and private Inference tests do not contribute. Playground is attributed to `POST /api/decide`; gateway
rows retain the public `POST` path (`/v1/systemone`, `/v1/basal`, `/v1/decisions` or `/decisions`) rather than the
translated child path. MCP uses `CALL /mcp` (one invocation, not necessarily one HTTP request).

```json
{
  "scope": "process",
  "window_seconds": 900,
  "bucket_seconds": 60,
  "window_start": 1730000000.0,
  "window_end": 1730000900.0,
  "observed_since": 1729999900.0,
  "requests": 3,
  "errors": 1,
  "input_tokens": 240,
  "requests_per_s": 0.0033333333,
  "input_tokens_per_s": 0.2666666667,
  "avg_latency_ms": 120.0,
  "p95_ms": 170.0,
  "latency_sample_limit": 100,
  "series": [
    {
      "start": 1730000880.0,
      "requests": 1,
      "errors": 0,
      "input_tokens": 80,
      "requests_per_s": 0.05,
      "input_tokens_per_s": 4.0
    }
  ],
  "endpoints": [
    {
      "endpoint": "/api/decide",
      "method": "POST",
      "requests": 1,
      "errors": 0,
      "input_tokens": 80,
      "requests_per_s": 0.0011111111,
      "input_tokens_per_s": 0.0888888889,
      "avg_latency_ms": 80.0,
      "p95_ms": 80.0
    }
  ]
}
```

Timestamp and rate fields are JSON numbers; counters and `window_seconds`, `bucket_seconds`, and
`latency_sample_limit` are integers. `avg_latency_ms` and `p95_ms` are numbers or `null` when no sample exists.
`series` is ordered oldest to newest; its rows use Unix minute starts and may include zero-count minutes, but never
complete minutes before observation began. Rates use the actual observed elapsed time, capped to the selected window,
this process's `observed_since`, and the retained bucket span, not a model generation-speed measure. Latencies use
request-weighted means and sampled p95: at most 100 latency values per model, endpoint and minute are retained; each
returned aggregate uses at most 6000 samples. The percentile is explicitly approximate under high traffic.
Only the latest 60 minute buckets are retained. The first/last bucket can cover a partial requested window because
counters are bucketed by minute; a 60-minute query returns at most those 60 retained minute buckets.
`input_tokens` is Engine-reported usage when present; missing usage contributes zero and is never estimated.

`GET /api/stats` and `/v1/stats` keep their existing lifetime response shape and cross-process persistence. Deleting
all stats or one model at `/api/stats` (or `/api/stats/{model_id}`) also clears matching rolling data in that app
process only; it does not clear another process's in-memory window. Rolling telemetry is not persisted or merged.

### MCP routing

With Engine 2.5.0 the streamable-HTTP MCP `decide` and `decide_many` tools take an optional `model` argument and a
`list_models` tool returns `{"models": [...]}`, the ids of the loaded models. Without `model` they answer with the
**first loaded model**. With Engine 2.4.1 their schemas have neither: they always answer with the first loaded model;
use `GET /v1/models` for inventory and the System One/OpenAI Decisions routes for named-model routing. On 2.4.1
Playground refuses to generate an MCP example for a different selected model rather than silently changing its target;
on 2.5.0 the example passes the selected model as `model`. `GET /api/status` reports which applies as
`serving.mcp_models`.

Use the running gateway's `/mcp` HTTP endpoint with an initialized MCP client. With Engine 2.5.0 the Engine's stdio
bridge reaches the gateway too: `basal-mcp --url http://HOST:PORT --model MODEL_ID [--api-key KEY]` sends `model` and
the bearer key with every request. The 2.4.1 `basal-mcp` accepts neither `--model` nor an API-key option and does not
send the gateway's required `model` field.

## Errors and request IDs

System One and `/v1/basal` use an envelope with a string `error` and machine-readable `code`. Validation failures also
include `detail`:

```json
{"error":"question 'q': 1 options (supported: 2..255)","code":"INVALID_REQUEST",
 "detail":[{"loc":["body"],"msg":"question 'q': 1 options (supported: 2..255)","type":"value_error"}]}
```

| Status | Code | Route / meaning |
|---|---|---|
| 400 | `INVALID_JSON` | System One or Decisions: malformed JSON or body is not an object |
| 400 | `INVALID_REQUEST` | Decisions: invalid input or question shape / limits |
| 400 | `unsupported_parameter` | Decisions: unsupported top-level parameter (`param` names it) |
| 401 | `UNAUTHORIZED` | Any protected route: missing or invalid API key |
| 403 | `FORBIDDEN` | `/v1/keys`: a non-admin key requested key usage |
| 503 | `OUT_OF_MEMORY` | CUDA, MPS, or Metal GPU memory exhaustion |
| 422 | `INVALID_REQUEST` | System One validation / media failure, or translated Decisions inference validation |
| 500 | `INTERNAL_ERROR` | Unexpected server failure; details are logged to stderr |

A malformed body example is `400 {"error":"malformed JSON","code":"INVALID_JSON"}`. On `/v1/decisions` and
`/decisions`, errors use the OpenAI envelope:

```json
{"error":{"message":"unsupported parameter: temperature","type":"invalid_request_error",
 "param":"temperature","code":"unsupported_parameter"}}
```

Decisions input/parameter errors return 400; translated inference validation returns 422; authentication failures
return 401; unexpected server failures are 500. Auth errors use `authentication_error`, server errors use
`server_error`, and request errors use `invalid_request_error`. The `error` message remains useful for display. Before
this API change every failure returned 422 with `{"error":"..."}`. Now malformed JSON is 400, GPU memory exhaustion is 503, unexpected internal
failures are 500, and responses include `code`; the existing `error` string is unchanged for the same failure.

Every HTTP response, including errors and `/health`, has `x-request-id` and `x-typesafe-request-id` with the same
value. A valid incoming `x-request-id` (1–128 ASCII letters, digits, `.`, `_`, `:`, or `-`) is echoed in both; an
invalid or absent value is replaced with `req_<uuid4 hex>`. CORS exposes both headers.

## Authentication and CORS

By default the API has no key requirement. Keys are created in the UI (API server view, **Require an API key**;
[USAGE.md](USAGE.md#api-server)); the API server reads them from the key file next to its settings, in this JSON format:

```json
{"keys":[{"name":"laptop","sha256":"<64 lowercase hexadecimal SHA-256 characters>","admin":false}]}
```

Create a random secret and its stored digest without basal-ui:

```bash
python -c 'import hashlib,secrets; s="bsl_"+secrets.token_urlsafe(32); print("secret:",s); print("sha256:",hashlib.sha256(s.encode()).hexdigest())'
```

Put the printed secret in your secret store and only the digest in the key file. Send the secret as
`Authorization: Bearer <secret>` or `X-API-Key: <secret>`. `/health` is always open. Every authentication attempt
checks the key file's modification time, size and inode; changes reload it immediately, and unchanged contents are
revalidated on the first request more than two seconds after the last read. Removing a key revokes it without an API
server restart. Key creation and revocation synchronize live instance files using literal settings filename stems,
including filenames containing glob characters.
`GET /v1/keys` requires an `admin: true` key and reports each key's name and in-memory `last_used` timestamp (null
until used); it does not return secrets or hashes.

The **CORS origins** setting (API server view) takes browser origins, one per line, or `*`. CORS permits GET, POST and OPTIONS and the headers
`Authorization`, `Content-Type`, `X-API-Key`, `X-Request-Id`, `Mcp-Session-Id` and `Mcp-Protocol-Version`. It exposes `Mcp-Session-Id`, `x-request-id` and `x-typesafe-request-id`. Preflight
requests are handled before authentication; allowed origins receive CORS headers, and a foreign origin is rejected
with HTTP 400 and no allow-origin header. Any other request that carries an `Origin` header (a browser page) from an
origin not in the list is refused with 403 `ORIGIN_REFUSED` before the key check and before it reaches a model, so a
web page cannot run decisions on a keyless server; `GET /health` stays open. Clients outside a browser send no
`Origin` and are unaffected.

## OpenAI Decisions compatibility

`POST /v1/decisions` and `POST /decisions` translate the OpenAI Decisions request to System One and translate the
answer back. The served model name is used in the response.

| Decisions request | System One translation | Decisions answer |
|---|---|---|
| `input` string or user messages | `state`; `input_text` parts joined with two newlines | — |
| `input_image` data URL | Image item in `media` | — |
| `predicate` | `noul` | `{type, name, probability}` |
| `choice` | `choice` with criteria keyed by `str(value)` | `{type, name, choice, probabilities, confidence}` |
| `score` | `score` with ordered levels | `{type, name, score, probabilities, confidence}` |

The request accepts `model`, `input`, `questions`, `safety_identifier`, `metadata` and `store`. The last three are
accepted but do not affect inference. Other top-level parameters and unknown question fields are rejected as
`unsupported_parameter` or invalid request. Messages must have role `user` or omit role. Content may be a string or an
array of `input_text` and `input_image` parts. Images must be base64 `data:` URLs; `detail` is ignored and remote URLs
are not fetched. Empty text with image input is accepted if the underlying model accepts it.

There must be 1–200 questions. Each requires `type` and non-empty `instructions`; `name` is optional and must be
unique when provided. Omitted names appear as JSON `null`. A `choice` needs 2–255 values, each a string or boolean;
values must be unique after converting to strings, and original values (including booleans) are restored in answers.
A `score` needs 2–10 levels. These Decisions limits may exceed basal's own per-prompt-format option limit: prompt format
1 supports 2–10 options and format 2 supports 2–255 (the `ollama` mode limit is 20). The serving format must still
accept the translated question.

Full request and verified response:

```json
{
  "model":"basal",
  "input":[{"role":"user","content":[{"type":"input_text","text":"Customer wrote: I was charged twice for order #4411 and want one charge refunded today."}]}],
  "questions":[
    {"type":"predicate","name":"is_refund_request","instructions":"Is the customer asking for a refund?"},
    {"type":"choice","name":"needs_human","instructions":"Should a human agent take over?","choices":[{"value":true,"description":"billing dispute that needs a person"},{"value":false}]},
    {"type":"score","instructions":"How frustrated is the customer?","levels":[{"label":"calm"},{"label":"mildly annoyed"},{"label":"angry","description":"uses hostile language"}]}
  ]
}
```

```json
{
  "id":"dec_e7849d58…","object":"decision","created":1792523317,"model":"Remek/basal-2.0-mini-MLX-8bit",
  "answers":[
    {"type":"predicate","name":"is_refund_request","probability":0.8828},
    {"type":"choice","name":"needs_human","choice":true,"probabilities":[{"value":true,"probability":0.9199},{"value":false,"probability":0.0801}],"confidence":0.9199},
    {"type":"score","name":null,"score":1.0627,"probabilities":[{"value":0,"label":"calm","probability":0.1620},{"value":1,"label":"mildly annoyed","probability":0.6132},{"value":2,"label":"angry","probability":0.2248}],"confidence":0.6132}
  ],
  "usage":{"input_tokens":395,"input_tokens_details":{"cached_tokens":0},"output_tokens":0,"output_tokens_details":{"reasoning_tokens":0},"total_tokens":395}
}
```

## Client setups

### TypeSafe Python SDK

Install `typesafe-sdk` and import `typesafe_sdk`. The verified run used version 0.7.5:

```bash
uv pip install typesafe-sdk==0.7.5
export TYPESAFE_BASE_URL=http://127.0.0.1:8765
export TYPESAFE_API_KEY="<secret>"
export TYPESAFE_DEFAULT_MODEL=Remek/basal-2.0-mini-MLX-8bit
export NO_PROXY=localhost,127.0.0.1
```

`TypeSafeClient().system_one(...)` returned `SystemOneResponse` with typed Choice/Noul/Score answers; the response
request id matched `x-typesafe-request-id`. `client.models.list()` returned the served model. Wrong keys raised
`TypeSafeAuthenticationError` (401); an invalid one-option choice raised `TypeSafeUnprocessableEntityError` (422)
with the server message and request id. When basal is open, the SDK still requires a non-empty `TYPESAFE_API_KEY`;
use a placeholder such as `local`.

### OpenAI Python SDK

Verified with OpenAI Python SDK 3.35.1 (the Decisions API is available in SDK versions >= 3.26):

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8765/v1", api_key="<secret>")
result = client.decisions.create(
    model="Remek/basal-2.0-mini-MLX-8bit",
    input="Export fails in Safari but works in Chrome.",
    questions=[
        {
            "type": "score",
            "name": "severity",
            "instructions": "How severe is the issue?",
            "levels": [{"label": "minor"}, {"label": "major"}, {"label": "blocking"}],
        },
        {
            "type": "choice",
            "name": "team",
            "instructions": "Which team should investigate?",
            "choices": [{"value": "frontend"}, {"value": "backend"}],
        },
    ],
)
```

This returned score and choice answers; a wrong key raised `openai.AuthenticationError` (401).

### LiteLLM

LiteLLM's self-hosted vLLM System One provider reaches the API server as `hosted_vllm/<served name>` with `api_base` (the
server root or `/v1`; both work). Verified with LiteLLM 1.106.1 against basal-ui's API server with a required key.

Python SDK, both request formats (LiteLLM translates the OpenAI shape to System One for this provider):

```python
import litellm

r = litellm.systemone(
    model="hosted_vllm/basal-2.0-mini",
    api_base="http://127.0.0.1:8000",
    api_key="<secret>",
    state="Customer: charged twice, wants a refund today.",
    questions={"refund": {"type": "noul", "instructions": "Is this a refund request?"}},
)
print(r.answers["refund"].noul)

d = litellm.decisions(
    model="hosted_vllm/basal-2.0-mini",
    api_base="http://127.0.0.1:8000",
    api_key="<secret>",
    input="Customer: charged twice, wants a refund today.",
    questions=[{"type": "predicate", "name": "refund", "instructions": "Is this a refund request?"}],
)
print(d.answers[0].probability)
```

A wrong key surfaces as `litellm.AuthenticationError` carrying the API server's `{"error": ..., "code": "UNAUTHORIZED"}`.

Proxy: the entry below (the same one the basal-ui Connect panel shows) served both `/v1/systemone` and
`/v1/decisions` through `litellm --config config.yaml`. The proxy refuses to start without its own master key
(`LITELLM_MASTER_KEY`); clients then authenticate to the proxy with that key, and the proxy uses `api_key` below
towards basal.

```yaml
model_list:
  - model_name: basal-2.0-mini
    litellm_params:
      model: hosted_vllm/basal-2.0-mini
      api_base: http://127.0.0.1:8000
      api_key: <secret>          # any non-empty value when the API server runs without keys
```

### Python (`httpx`)

The gateway needs `model` in every request. `basal.client.Basal` (engine 2.4.1) takes neither a model nor an API key
(`Basal(url, timeout, early_exit, facts)`), so it cannot reach basal-ui's API server; post the System One body instead:

```python
import httpx

answer = httpx.post(
    "http://127.0.0.1:8000/v1/systemone",
    headers={"Authorization": "Bearer <secret>"},
    json={
        "model": "Remek/basal-2.0-mini-MLX-8bit",
        "state": "A user asks for a refund.",
        "questions": {
            "team": {
                "type": "choice",
                "instructions": "Which team?",
                "criteria": {"billing": "Billing", "support": "Support"},
            }
        },
    },
    timeout=120,
).json()
```

### curl

```bash
curl http://127.0.0.1:8000/v1/systemone -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <secret>' \
  -d '{"model":"<loaded model id, e.g. Remek/basal-2.0-mini-MLX-8bit>","state":"Choose a team","questions":{"route":{"type":"choice","instructions":"Which team?","criteria":{"billing":"Billing","support":"Support"}}}}'

curl http://127.0.0.1:8000/v1/decisions -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer <secret>' \
  -d '{"model":"<loaded model id, e.g. Remek/basal-2.0-mini-MLX-8bit>","input":"Choose a team","questions":[{"type":"choice","instructions":"Which team?","choices":[{"value":"billing"},{"value":"support"}]}]}'
```

### Verified compatibility

The transcript-backed checks used a single-model basal-serve 2.3.0 with basal-2.0-mini MLX 8-bit on Apple M4 Max;
on basal-ui's API server every request also names its `model`. TypeSafe Python
SDK `typesafe-sdk` 0.7.5, OpenAI SDK 3.35.1 and LiteLLM 1.106.1 (SDK and proxy) worked against that server.

| Ecosystem | Matches | Differences and notes |
|---|---|---|
| TypeSafe System One | `/v1/systemone`, Bearer auth, named questions and answers, request-id header | `model` is optional and not validated; basal reports the served name. Basal adds question types, usage fields and calibration metadata. |
| vLLM 0.32 | System One route and vLLM-style `images`/`audio` data URL aliases | Remote media URLs are rejected; basal limits requests to 8 items and 20 MiB each; the model/backend must support media. |
| Ollama / llama.cpp | System One request and answer shape through basal's server integrations | Basal-specific question types and extensions may not be supported by those servers; option limits depend on the selected backend and prompt format. |
| Ollaya | System One question/answer wire shape | Basal-specific types/fields and response usage may differ. |
| OpenAI Decisions | `/v1/decisions` and `/decisions` request/answer translation | Only predicate, choice and score are translated; see limits above. Safety/metadata/store fields are accepted but ignored. Errors use route-specific OpenAI envelopes. |
## Custom calibration

`POST /api/calibrate` starts a calibration job for `id` and a local labelled `input`; `targets` is a list of
`max_error` values. Its optional `advanced` object accepts `conformal` (a list of numeric coverage levels strictly
between 0 and 1) and `keep_shipped` (a list drawn from `choice`, `noul`, and `score`). These options are rejected
when the installed `basal-calibrate` parser does not declare them. Kept types require compatible temperatures and
per-type thresholds in the Shipped calibration or the explicitly compared file; `compare: "none"` cannot keep any.
A loaded model's Active load settings and snapshot are used; otherwise its Saved load settings are used.
`GET /api/calibrations?id=...` reports option availability in `options`. `GET /api/calibrations/keep-options?id=...&compare=...`
returns a `types` object mapping eligible question types to their compared file, using `shipped`, `none`, or a file
path for `compare`. The start request rechecks eligibility. `GET /api/calibrations/report?id=...&file=...` returns
its original `report` text and, when present, `details` with fitted identity, conformal results and per-type threshold
metadata. Old reports without those fields retain the original response shape.
### Playground question sets
`GET /api/question-sets` lists Engine set names, available languages and question counts.
`POST /api/question-sets/{name}` accepts JSON with `lang` (`auto`, `pl` or `en`, default `auto`), string `state`
(default empty), and optional numeric `max_error` strictly between 0 and 1. It returns the Engine's questions unchanged;
Auto delegates language guessing to the Engine. An unknown name returns 404, invalid inputs 400, and both routes return
503 when the Engine lacks question sets.
## Preset URL import
`POST /api/presets/import-url` accepts `{"url": "https://example.com/presets.json"}` and returns
`{"presets": {"load": {}, "request": {}}, "count": 0}` with the resulting presets and processed entry count.
Only public HTTP(S) addresses and `application/json` responses up to 1 MiB are accepted; redirects are checked too.
Entries use the same validation, replacement and deletion rules as `PUT /api/presets`, in load then request order.
A failed entry can leave earlier entries saved. Request preset root `model` is removed.
### MCP tool import and shortlist
`POST /api/mcp/tools` accepts `{ "url": "http://localhost:PORT/mcp", "token": "..." }` (token optional) and returns `{ "tools": [...] }`. It initializes a streamable HTTP MCP session and follows up to 20 pages within a 10-second operation and 4 MiB response budget. Explicit loopback servers are allowed; other hosts must resolve exclusively to public unicast addresses, and bearer tokens for remote hosts require HTTPS. The token is operation-only; failures include an English `detail` and a `code`.
`POST /api/tools/shortlist` accepts `{ "tools": [...], "query": "...", "k": 40 }` and returns ranked original definitions in `tools`. Ranking uses local BM25 and does not call a model or an MCP tool.
Load options use the installed Engine parser's `long_lane` choices and default. `soam` accepts `auto`, `on`, or `off`; `auto` sends no flag. Legacy `long_lane: true` stays on and `false` inherits the installed Engine's default. If a load preset uses `long_lane: auto` on an Engine without that choice, it uses the installed Engine's default. Legacy boolean `soam` values migrate to `auto` or `off`. A model status may include `calibration_origin_warning` and `calibration_origin_warning_code` when the Engine reports a calibration fitted on other model files.
