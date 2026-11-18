# Contributing to basal-ui

## Dev setup

```bash
make install                 # uv sync: dev group (engine pinned by [tool.uv.sources]) + mcp extra
make engine-pin              # the pinned engine release (basal_ui/engine.py PINNED_RELEASE)
make engine REF=v2.4.2       # later: another release tag
make engine REF=feat/engine-adapter-upstream   # or any ref of the fork pawelkiszczak/basal
make lint                    # ruff check + ruff format --check
make format                  # ruff format
make test                    # the unit tests (tests/, not tests/e2e)
make e2e                     # BASAL_E2E=1: real models on real hardware; docs/E2E.md
make run                     # start basal-ui
make screenshots             # refresh docs/images (see "Screenshots")
```

`make install` syncs the environment from the lockfile; the dev group's `[tool.uv.sources]` pin decides which engine
release it installs. `make engine REF=…` switches to another release-tag archive or a fork ref for a trial — a later
`make install` restores the pinned tag. An Engine outside `SUPPORTED_RANGE` starts only with
`BASAL_UI_ENGINE_UNCHECKED=1` (see the README, "Experimental engine").

## Code style

- Python: 120 columns; Ruff (`F,I,B,UP,SIM`, `[tool.ruff.lint]` in `pyproject.toml`) and `ruff format`; run
  `make lint` / `make format`. `uv run --no-sync pre-commit install` (optional; CI runs the same checks). Follow the
  surrounding code: docstrings that say what a function returns and why, no speculative abstractions.
- Frontend: plain JavaScript and CSS in `basal_ui/static/` (no build step). Every UI string is English in the code and
  passed through `t()` / `tp()`; its Polish translation goes into `basal_ui/static/i18n.js`.
  `tests/test_ui_i18n.py` fails on a string without one.
- Server errors stay English; translatable explanations carry a message code alongside the English text.

## Tests

- **Unit tests** (`make test`, `tests/test_*.py`): every route and behaviour with stubbed `basal-serve` children; fast,
  no models, run on every CI job. Add one for each behaviour a user or API client can observe: answers, refusals,
  boundaries, state transitions.
- **End-to-end gate** (`make e2e`, `tests/e2e/`, [docs/E2E.md](docs/E2E.md)): a real basal-ui, real models, the pages in
  Chromium. Add to it when the behaviour depends on a real engine or a real browser; a test that needs hardware the
  machine lacks skips with the reason.
- No tests of wiring (that a function calls another, that a field is copied), of wording, or of mocks echoing
  themselves.

Prepare Chromium and the host's Engine extras as described in [E2E setup](docs/E2E.md); `make install` alone does not
install them. Record the tested commit, hardware, models and skip reasons with an e2e result. The
[2026-10-29 development gate](docs/E2E.md#verified-dev-gate-2026-10-29) records the merged review fixes;
it is not release acceptance on `stage` or validation of other hardware.

## Branches and CI

| Branch | Role | Changes arrive by |
|---|---|---|
| `dev` | integration: every feature and fix lands here first | a pull request from a topic branch |
| `stage` | release candidate: what the next release will ship, where the e2e gate runs | a pull request `dev` → `stage` |
| `main` | released code; `v<version>` tags (including release candidates) are cut from it | a pull request `stage` → `main` |

A fix made on `stage` (found while testing a release candidate) goes back with a pull request `stage` → `dev`, so
`dev` never loses it.

Release CI (`.github/workflows/release-ci.yml`) runs after merges to `stage` and `main`. It runs Ruff and
unit tests on Ubuntu and macOS with Python 3.10 and 3.13 across the tested Engine releases. The `ci-ok` job
summarizes the matrix for the merged commit. Run relevant validation locally before merging a feature PR.
CD uses the `v<version>` tag workflow described in [Releasing](#releasing).

| GitHub branch protection | `dev` | `stage` | `main` |
|---|---|---|---|
| changes go through a pull request | yes | yes | yes |
| applies to admins too | no | yes | yes |
| force pushes and deletion | refused | refused | refused |

No review approval is required: the project has one maintainer, who cannot approve their own pull requests.

## Pull requests

One topic per PR, opened against `dev`. The template (`.github/PULL_REQUEST_TEMPLATE.md`) asks for what and why,
whether the engine range is affected, the tests run (`make lint`, `make test`, and `make e2e` with the hardware for
changes that touch loading, serving or the pages), refreshed screenshots for visible changes and a
`docs/WHATS_NEW.md` bullet.

## Screenshots

The README's screenshots and tour GIF (`docs/images/`) come from `make screenshots` (`scripts/screenshots.py`).
It starts an isolated app, loads cached `Remek/basal-2.0-mini` plus the smallest other downloaded runnable model,
sends real Decisions, and captures Dashboard, Hardware, Models, Playground, all four Manager tabs, API server,
Settings, Batch, API examples and model comparison before recording the tour. Counters and answers come from
that run, not fixtures. Refresh these thirteen screenshots and the GIF after visible UI changes. First follow
the [E2E environment setup](docs/E2E.md); the target does not install dependencies, download models or change
the Engine. Both cached models must fit together. It also needs Chromium
(`uv run --no-sync playwright install chromium`) and `ffmpeg` on `PATH`.

## Card surfaces

Cards in the page use a border to define their surface; reserve the shared shadow for floating overlays.

## Engine proposals

basal-ui depends only on upstream engine releases, so every engine change it needs goes upstream, one at a time; the
UI never blocks on a proposal. The pipeline:

1. **Research note and issue.** Copy `docs/research/TEMPLATE.md` to `docs/research/<topic>.md` (use case,
   proposal, measurements) and open an issue on [rkinas/basal](https://github.com/rkinas/basal) stating the problem
   and an **acceptance check** — the measurable criteria that will decide it, agreed before implementation.
2. **One topic branch, one PR.** Cut the branch on the fork [pawelkiszczak/basal](https://github.com/pawelkiszczak/basal)
   from `upstream/main`, never from the fork's `main`: the fork's `main` only mirrors upstream
   (`git pull --ff-only upstream main`) and is never committed to. Exactly one PR per proposal, referencing the
   issue; rebase when the maintainer names the release it is scheduled for.
3. **Ship the UI control behind a probe.** The basal-ui control for the capability lands behind
   `basal_ui.engine.feature("<name>")` and is tried through the experimental engine route
   (`BASAL_UI_ENGINE_UNCHECKED=1`), so users of supported releases are unaffected.
4. **Close the loop.** When the release enters `SUPPORTED_RANGE` (see "Bumping the engine" below), drop the probe and
   update the research note's **Outcome**.

`docs/adr/0003-separate-repo-on-upstream-engine.md` records why the repo is set up this way.

## Releasing

The published stable version is listed on [PyPI](https://pypi.org/project/basal-ui/) and in
[GitHub Releases](https://github.com/pawelkiszczak/basal-ui/releases). Promotion to `stage` validates a candidate.
Package publication requires a matching tag on `main` and approval of the `pypi` deployment.

1. If the Engine moves, bump it ("Bumping the engine" below). Set `version` in `pyproject.toml` (for example,
   `0.2.0rc1` for a candidate or `0.2.0` for a stable release), run `uv lock`, and finalize the matching
   `docs/WHATS_NEW.md` section under the version heading. Merge the metadata and release notes into `dev`
   before opening the promotion PR.
2. Open the pull request `dev` → `stage`. Run the real-hardware E2E gate on the candidate tree
   (`make e2e`, [E2E guide](docs/E2E.md)) and record the tree, models, hardware, results, and skips in the PR.
   After merging, confirm that `stage` matches the tested tree and that release CI passes for that commit.
   Return any fixes made on `stage` to `dev` through a pull request.
3. After release CI and E2E pass on `stage`, merge the pull request `stage` → `main`.
4. After release CI passes on the merged `main` commit, tag `v<version>` on `main` and push the tag. Before building or publishing, `publish.yml`
   checks that the tagged commit is on `main`, the tag matches `pyproject.toml`, and WHATS_NEW has a nonempty
   `# <version>` section.
5. Review the build and approve the waiting `pypi` deployment in GitHub Actions. It uploads to PyPI using trusted
   publishing, then creates the GitHub release with the validated notes and the same built artifacts.

PEP 440 prereleases (including `rc`, alpha, beta and development releases) are marked as GitHub prereleases and
explicitly excluded from Latest. Stable releases retain GitHub's automatic Latest selection. The GitHub release
command requires the remote tag to exist; it will not silently create one.

After publishing a stable version, update the README's pinned PyPI commands and the source-install section.
The published package version follows the PyPI listing in SECURITY.

### One-time trusted publisher setup

On PyPI's [pending publishers page](https://pypi.org/manage/account/publishing/), add a GitHub publisher with:

| Field | Value |
|---|---|
| PyPI project name | `basal-ui` |
| Owner | `pawelkiszczak` |
| Repository | `basal-ui` |
| Workflow filename | `publish.yml` |
| Environment | `pypi` |

The version is not part of the project name. A pending publisher does not upload a package or reserve the name;
the first successful upload creates the project. No PyPI API token or GitHub secret is needed.

GitHub's [`pypi` environment](https://github.com/pawelkiszczak/basal-ui/settings/environments) allows only **tags**
matching `v*`, not branches. Its required reviewer is [@pawelkiszczak](https://github.com/pawelkiszczak), and
administrator bypass is disabled. Self-approval is allowed because there is one maintainer; this is a deliberate
manual release gate, not a two-person review. The workflow independently enforces that the tagged commit is on
`main`. Keep the PyPI publisher's environment name and the workflow's `environment: pypi` in sync.

### Bumping the engine

`basal_ui/engine.py` holds the engine release basal-ui runs on: `TESTED_RELEASES` (every release CI tests; the last
is the pin, `PINNED_RELEASE`) and `SUPPORTED_RANGE`. CI's engine matrix is generated from `TESTED_RELEASES`. To move:

1. Edit `TESTED_RELEASES` and `SUPPORTED_RANGE` in `basal_ui/engine.py`.
2. Edit the `basal` url in `pyproject.toml` `[tool.uv.sources]` (uv cannot read Python) and run `uv lock`.
3. Edit the install lines and the engine badge in `README.md`, and the install line in `docs/E2E.md`.

`make test` (`tests/test_engine_gate.py::test_every_pin_names_the_tested_release`) fails until they all agree.
