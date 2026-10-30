# Retain the static UI with Basecoat

Use locally bundled Basecoat styles for the existing HTML and JavaScript UI, retaining sidebar navigation and the fork's serving workflows. The evaluated React component template would introduce a frontend build and migration of existing behavior; the selected Basecoat design improves presentation while keeping the Python distribution self-contained. Production users need neither Node nor an external component CDN.

## Styling update — 2026-10-30

The static, self-contained HTML/JavaScript architecture remains in effect. The shared
[design system](../design-system.md) supersedes the Basecoat styling decision: the page
no longer loads its global stylesheet. Application-owned tokens and components now
control layout, spacing, typography and states. This removes conflicting global card
and control layouts without introducing a frontend build or changing Engine contracts.
