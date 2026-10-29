# Security policy

## Supported versions

Only the latest basal-ui release on PyPI receives security fixes.
The `0.1.0` development snapshot is not yet published on PyPI; its
[recorded verification](docs/E2E.md#verified-dev-gate-2026-10-29) is not a stable-release or cross-hardware security
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
