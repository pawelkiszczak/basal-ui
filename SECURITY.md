# Security policy

## Supported versions

Only the latest basal-ui release on PyPI receives security fixes.
Release candidate `0.1.0rc1` is not yet published on PyPI; it is not a stable release. The
[recorded development verification](docs/E2E.md#studio-refinement-gate-2026-10-30) is not a stable-release or cross-hardware security
certification. Use [private reporting](#reporting-a-vulnerability) for development findings too.

## Reporting a vulnerability

Report it privately through GitHub's **Report a vulnerability** button on
[pawelkiszczak/basal-ui](https://github.com/pawelkiszczak/basal-ui/security/advisories/new). Do not open a public
issue. You get a response within 7 days.

## Scope

basal-ui is a local tool. Its UI, its API server and every model's `basal-serve` listen on loopback by default, and
loopback is not isolation between the users of one machine: any local account can reach a keyless API server. Exposing
the API server on the network (`0.0.0.0`) without **Require an API key**, or running basal-ui on a shared host, is
outside the threat model. Vulnerabilities in the model engine belong to [rkinas/basal](https://github.com/rkinas/basal).

The local **Engine add-ons** controls can install packages into the app's virtual environment after explicit
confirmation. Keep that environment dedicated to basal-ui; its maintenance lock cannot stop other programs using
the same interpreter. Only the installed Engine's declared `media`/`gguf` dependencies are accepted, with existing
package versions constrained. See [installation safety and cancellation limits](docs/BACKENDS.md#installing-engine-add-ons).
