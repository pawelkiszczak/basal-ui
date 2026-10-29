# Contributors

The people who made basal-ui and the work it stands on. Taken from the git history and the upstream projects; if you
are missing or listed wrongly, open a pull request against `dev` that fixes this file.

## basal-ui

| Contributor | GitHub nickname | Contribution |
|---|---|---|
| Paweł Kiszczak | [@pawelkiszczak](https://github.com/pawelkiszczak) | author and maintainer: the browser app, the API server, the fleet of `basal-serve` children, the tests, the docs |
| Greg Brzezinka | [@agentGreg](https://github.com/agentGreg) | merged History context-preservation fix ([#4](https://github.com/pawelkiszczak/basal-ui/pull/4)) and older-family e2e regression fix ([#5](https://github.com/pawelkiszczak/basal-ui/pull/5)); integrated with broader regressions in [#6](https://github.com/pawelkiszczak/basal-ui/pull/6) |

## The basal engine

basal-ui runs on the [basal engine](https://github.com/rkinas/basal) and contains no model code itself
([ADR 0003](docs/adr/0003-separate-repo-on-upstream-engine.md)). Its history starts in a fork of the engine, so the
engine's early commits are part of this repository's history too.

| Contributor | GitHub nickname | Contribution |
|---|---|---|
| Remigiusz Kinas | [@rkinas](https://github.com/rkinas) | author of the basal engine and the basal models (`Remek/basal-*`) basal-ui serves |
| lukaspoloki | [@lukaspoloki](https://github.com/lukaspoloki) | basal-viz in the engine ([rkinas/basal#2](https://github.com/rkinas/basal/pull/2)), the inspection app basal-ui embeds at `/viz` |

## Bundled third-party work

Fonts and UI libraries shipped in `basal_ui/static/` (Manrope, IBM Plex Mono, Basecoat) are listed with their licenses
in [NOTICE](NOTICE).

## Becoming a contributor

Read [CONTRIBUTING.md](CONTRIBUTING.md): pull requests go to `dev`, one topic each, with CI green. Add yourself to the
basal-ui table above in your first merged pull request.
