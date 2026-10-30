# Basal design system

Basal is a local decision workbench. The interface should make the current state and
next useful action easy to find. It retains the product's identity, with a warm orange
brand accent on neutral surfaces. Success, warning, error, information and focus have
separate semantic colors in both light and dark themes.

## Source of truth

- `basal_ui/static/app.css`: semantic tokens, shared primitives and view compositions,
  in that order. No global vendor stylesheet is loaded.
- `basal_ui/static/ui.js`: `uiIcon`, `emptyState`, shared navigation and action icons.
- `basal_ui/static/app.js`: application state and domain renderers. `renderSuggestionCard`
  renders the same presentation for application suggestions and isolated catalogue examples.
- `basal_ui/static/design-system.js`: optional interactive catalogue at `#design-system`.
  It registers its navigation, route metadata and page. Examples use local state and
  never start downloads, load models, create keys or call the API.
- `basal_ui/static/i18n.js`: English source strings and Polish translations.

Fonts and Lucide 0.468.0 icons are bundled locally. There is no build step, external
font request, icon CDN or additional runtime dependency.

## Foundations

| Role | Rule |
| --- | --- |
| Spacing | 4 px base; 8 px related controls, 12 px small groups, 20–24 px panels, 40 px catalogue sections |
| Typography | Manrope: page title 28/35 at 750, section 21/28 at 700, component 15/22 at 700, body 14/22, supporting text and controls 12/19 |
| Code and paths | IBM Plex Mono, 12/20; wrap long paths rather than widening the page |
| Controls | 36 px desktop, 40 px mobile actions; labelled inputs and useful disabled placeholders |
| Radius | 6 px controls, 10 px panels, 14 px dialogs |
| Icons | Lucide, 1.75 px stroke, 16 px actions, 18 px navigation, 21 px empty states |
| Color | Orange for brand/action; green success, amber attention, red error, blue information/focus; metadata stays neutral |
| Width | 1440 px maximum workbench; panels and controls reflow instead of overflowing the page |

Use `--paper`, `--white`, `--surface-raised`, `--surface-subtle`, `--ink`, `--muted`,
`--line`, `--line-strong`, `--accent`, `--accent-solid`, `--on-accent`, `--green`,
`--amber`, `--danger`, `--info` and `--focus`. Each semantic color has a paired soft
background token. Never use the brand color for destructive actions.

## Components and composition

One primary action per task group; outlined secondary actions; links for navigation.
Action icons precede their labels and navigation arrows follow them. Tabs and short
mode choices use text. Icon-only controls need an accessible name.

Use a panel for a coherent task, not for every text fragment. Section headings,
spacing and dividers provide hierarchy. Numbered page eyebrows are removed. Long
format, engine and calibration explanations belong in contextual disclosures.

`emptyState({icon, title, description, action, href, compact, inline})` is the shared
empty-state renderer. Use the default for an empty collection, `compact` for a small
summary and `inline` for workbench summaries. State what will appear and how to begin.
Do not present an error, disabled service, empty filtered result and loading state as
the same condition. Loading preserves the layout; errors retain recoverable input.

Forms use `.field` and `.form-grid`/`.serve-fields`. Use real labels, a 6 px label gap
and 20 px grid gaps. `.controls` owns action spacing. `.work-toolbar` owns log actions;
buttons do not become full-width card children accidentally.

Focus uses `--focus` and a visible outline. Dialogs use native modal behavior and
Escape/Cancel. Theme and locale preferences remain independent. Reduced motion is
respected. Mobile compositions start below 800 px, with single-column forms below
600 px. The navigation strip scrolls locally; tables scroll inside their own container.

## Remove the catalogue

Remove this one tag from `basal_ui/static/index.html`:

```html
<script src="/static/design-system.js" defer></script>
```

The navigation, page and route then disappear. A direct `#design-system` URL falls
back to Overview. Optionally delete `design-system.js` and the catalogue-only `.ds-*`
styles afterward. Keep `app.css`, `ui.js` and the shared domain renderers: they style
and render the application itself. No backend, API, package or saved settings change
is necessary.

## Verification

Use the [interface verification recipe](E2E.md#interface-design-system-check) for this
UI change. Model inference remains covered by the separate real-model release gate;
a UI pass without downloaded models does not certify inference.
