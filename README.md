# basal-ui

**A local browser app for [basal](https://github.com/rkinas/basal) decision models: pick a model your machine can
run, load it, ask typed questions with calibrated confidence, and serve it to your own apps.**

[![CI](https://github.com/pawelkiszczak/basal-ui/actions/workflows/ci.yml/badge.svg)](https://github.com/pawelkiszczak/basal-ui/actions/workflows/ci.yml)
[![Nightly](https://github.com/pawelkiszczak/basal-ui/actions/workflows/nightly.yml/badge.svg)](https://github.com/pawelkiszczak/basal-ui/actions/workflows/nightly.yml)
[![PyPI](https://img.shields.io/pypi/v/basal-ui)](https://pypi.org/project/basal-ui/)
[![Python](https://img.shields.io/badge/python-3.10%E2%80%933.13-blue)](https://pypi.org/project/basal-ui/)
[![Engine](https://img.shields.io/badge/engine-basal%20%3E%3D2.4.1%2C%3C2.5-6b5b95)](https://github.com/rkinas/basal)
[![License](https://img.shields.io/github/license/pawelkiszczak/basal-ui)](LICENSE)

![basal-ui tour: load basal-2.0-mini in Model manager, then ask it a question in the Playground](https://raw.githubusercontent.com/pawelkiszczak/basal-ui/main/docs/images/tour.gif)

In the style of LM Studio: basal-ui detects your hardware, lists every basal model on Hugging Face with what this
machine can run, downloads the one you choose and starts a private `basal-serve` for each loaded model.

## What it does

- **Dashboard**: recorded Decision traffic in input tokens/s and requests/s, latency, errors, endpoint traffic and
  loaded-model activity. Recent traffic is local to this app process; lifetime model usage is shared with Model manager.

  <img src="https://raw.githubusercontent.com/pawelkiszczak/basal-ui/main/docs/images/dashboard.png" width="900" alt="Studio Dashboard: real Decision throughput, model activity and endpoint traffic">

- **Models**: every basal model on Hugging Face, with what this machine can run and a suggestion per use; download and
  load with one click.

  <img src="https://raw.githubusercontent.com/pawelkiszczak/basal-ui/main/docs/images/models.png" width="900" alt="Models: suggested and all basal variants with what this machine can run">

- a **Playground** for decisions, with a builder for every question type, evidence and calibrated confidence; images
  and voice clips for basal-omni, with numbered elements marked on a screenshot.

  <img src="https://raw.githubusercontent.com/pawelkiszczak/basal-ui/main/docs/images/playground.png" width="900" alt="Playground: a choice question answered with calibrated confidence">

- a **Model manager** with separate **Library**, **Usage by model**, **Benchmarks** and **Calibration** tabs.
  Loaded models, memory and on-disk variants stay in Library; measurement and tuning controls no longer crowd it.

  <img src="https://raw.githubusercontent.com/pawelkiszczak/basal-ui/main/docs/images/model-manager.png" width="900" alt="Studio Model manager: Library with live inference, memory and loaded models">

- **batch** inference over a JSONL file;
- **Inspect**, which embeds `basal-viz` to show how a decision is made, layer by layer;
- an **API server** with named API keys, CORS, request ids, typed errors, OpenAI Decisions translation and MCP — one
  port for every loaded model.

  <img src="https://raw.githubusercontent.com/pawelkiszczak/basal-ui/main/docs/images/api-server.png" width="900" alt="API server: one port for every loaded model, keys and MCP">

## Install

Release candidate `0.1.0rc1` is being prepared on `dev` and is not yet published on PyPI. Its planned release tag is
`v0.1.0rc1`. Until publication, run from source (requires [uv](https://docs.astral.sh/uv/) and `make`):

```bash
git clone --branch dev https://github.com/pawelkiszczak/basal-ui.git
cd basal-ui
make install
make run                    # then open http://127.0.0.1:7860
```

Engine extras are optional. For MLX on Apple Silicon, install the extra after `make install`:

```bash
uv pip install "basal[mlx] @ https://github.com/rkinas/basal/archive/refs/tags/v2.4.1.tar.gz"
```

### Release candidate package (after publication)

Once `0.1.0rc1` is available on PyPI, install that exact version. It is a prerelease, not a stable release:

```bash
uv venv
uv pip install "basal @ https://github.com/rkinas/basal/archive/refs/tags/v2.4.1.tar.gz" "basal-ui==0.1.0rc1"
uv run --no-sync basal-ui    # then open http://127.0.0.1:7860
```

Or as a standalone tool:

```bash
uv tool install "basal-ui==0.1.0rc1" --with "basal @ https://github.com/rkinas/basal/archive/refs/tags/v2.4.1.tar.gz"
```

The engine extras of your machine are chosen on the *engine* install, not on basal-ui, for example
`uv pip install "basal[mlx] @ https://github.com/rkinas/basal/archive/refs/tags/v2.4.1.tar.gz"`
on Apple Silicon (`mlx`), or `basal[gguf]`, `basal[fp8]`, `basal[vllm]` elsewhere; `basal[media]` for images and audio
on basal-omni. Use `basal-ui[mcp]==0.1.0rc1` for the UI API server's MCP endpoint. basal-ui does not declare the Engine
as a dependency: PyPI rejects direct-URL requirements, and the PyPI package named `basal` is an unrelated project. Instead
basal-ui checks for the Engine at startup and refuses to start without it, printing the install command above.

## Supported engine range

basal-ui runs on upstream engine releases `>=2.4.1,<2.5` (see `basal_ui/engine.py`). An engine outside that range is
refused with the install command of a supported release.

## Experimental engine

To try an engine change that has not shipped in a supported release (for example a proposal branch from the fork
[pawelkiszczak/basal](https://github.com/pawelkiszczak/basal)):

```bash
uv pip install "basal @ git+https://github.com/pawelkiszczak/basal@<topic-branch>"
BASAL_UI_ENGINE_UNCHECKED=1 basal-ui
```

basal-ui starts and shows a persistent **experimental engine** badge in the top bar with the engine's version (and
commit). Features the engine does not provide yet stay hidden or greyed out with their reason.

## Documentation

| | |
|---|---|
| [docs/USAGE.md](docs/USAGE.md) | the browser app end to end |
| [docs/API.md](docs/API.md) | basal-ui's API server: keys, CORS, errors, OpenAI Decisions, MCP, clients |
| [docs/E2E.md](docs/E2E.md) | setup, coverage and the [Studio redesign verification](docs/E2E.md#studio-redesign-gate-2026-10-30) |
| [docs/BACKENDS.md](docs/BACKENDS.md) | backend inventory, machine/model compatibility and isolated Inference tests |
| [docs/WHATS_NEW.md](docs/WHATS_NEW.md) | the changelog |
| [CONTRIBUTING.md](CONTRIBUTING.md) | dev setup, code style, tests, [branches and CI](CONTRIBUTING.md#branches-and-ci) (`dev` → `stage` → `main`), pull requests, the Engine-proposal pipeline, [releasing](CONTRIBUTING.md#releasing) |
| [CONTRIBUTORS.md](CONTRIBUTORS.md) | who built basal-ui and the work it stands on |
| [SECURITY.md](SECURITY.md) | supported versions and private vulnerability reports |
| [rkinas/basal](https://github.com/rkinas/basal) | the engine |

## License

Apache-2.0. basal-ui by Paweł Kiszczak; it wraps the [basal engine](https://github.com/rkinas/basal) by Remigiusz
Kinas, also Apache-2.0. See [NOTICE](NOTICE) for bundled fonts and UI libraries.
