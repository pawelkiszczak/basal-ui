# Retain the static UI with Basecoat

Use locally bundled Basecoat styles for the existing HTML and JavaScript UI, retaining sidebar navigation and the fork's serving workflows. The evaluated React component template would introduce a frontend build and migration of existing behavior; the selected Basecoat design improves presentation while keeping the Python distribution self-contained. Production users need neither Node nor an external component CDN.

Navigation update (2026-10-30): Studio replaces the sidebar with primary top navigation and a Tools disclosure.
This changes the layout, not the decision to ship a static, locally bundled UI without Node or a CDN.
See [Usage](../USAGE.md) and the [Studio gate](../E2E.md#studio-refinement-gate-2026-10-30).
