# Comparison models are restricted, not capability-flagged

Other Decision servers now answer System One locally (llama.cpp `llama-server`, Ollama ≥ 0.35, vLLM, SGLang,
`transformers serve`, LocalAI) and as hosted APIs (TypeSafe Jev, OpenAI Decisions), with their own decision
checkpoints. The planned basal-ui integration will attach such a server by URL (plus an API key and an explicit
off-machine consent for non-loopback hosts) and list its models as **Comparison models**: they will take part in the
Playground two-model comparison, Batch accuracy runs (`basal-run --url`), History and Usage, and nothing else. They
will never be Suggested, loaded, calibrated, inspected or benchmarked, and a request that uses a basal-only field
(`multi`, `act`, `tool`, task types, `facts`, evidence, `max_error`) will be refused with the reason rather than sent
with the field dropped.

The alternative — treating them as ordinary models with a capability flag on every surface (load options, Suggested
fit, Calibration, Inspect, Benchmarks, memory budget) — was rejected: the Engine's `--mode llamacpp`/`--mode ollama`
are not System One proxies but basal-prompt readouts over basal GGUFs (`basal/engine.py` `LlamaCppBackend`), the
catalog, fit estimates and load options are `basal-serve` flags end to end, and no other Decision server offers a
`basal-calibrate` equivalent, so the only honest comparison is accuracy on the user's labelled data, latency and
memory. The restriction keeps every basal-only control true without a second convention. Consequence: a user who
wants to *serve* a non-basal model through basal-ui's API server will be told it is a Comparison model; that is a product
boundary, not a bug. Evidence: `docs/research/similar-solutions-2026-11.md`.

Amendment (Engine 2.5): `basal-attach` serves a chat model that was never trained on basal inside `basal-serve`, and
`basal-calibrate` fits it. Such a model is an **Attached model**; planned basal-ui integration will treat it as a normal
catalog entry (Library, Calibration, Usage, API server), not a Comparison model. The restriction in this ADR applies
only to models answered by a Decision server other than the Engine.
