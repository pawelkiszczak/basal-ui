# Basal design system

Basal is a local decision workbench. Every screen makes the current state and the next useful action easy to
find, uses the available width for work rather than decoration, and keeps safety information visible. The
identity is the layered basal mark, Manrope and one brand color; surfaces stay quiet.

## Source of truth

- `basal_ui/static/appearance.js`: skin and theme preferences, loaded without `defer` in `<head>` so the
  document has its final skin and theme before the first paint.
- `basal_ui/static/app.css`: tokens, then primitives, then view compositions, then the catalogue-only `.ds-*`
  rules. Views arrange primitives; they do not define colors, radii or control sizes of their own.
- `basal_ui/static/ui.js`: presentation primitives that never call APIs: `uiIcon`, `emptyState`, `uiNotice`,
  `uiBadge`, navigation/action icon wiring and in-page section navigation (`data-scroll-to`).
- `basal_ui/static/app.js`, `backends.js`: application state and domain renderers. `renderSuggestionCard` is the
  one suggestion composition used by Models, Overview and the catalogue.
- `basal_ui/static/design-system.js`: the optional catalogue at `#design-system`.
- `basal_ui/static/i18n.js`: English source strings and Polish translations.

Fonts (Manrope, IBM Plex Mono) and Lucide 0.468.0 icons are bundled locally with their licences. There is no
build step, CDN, external font request or additional runtime dependency.

## Skins and themes

A **skin** sets the neutral tint and the brand: `--paper`, `--white`, `--surface-raised`, `--surface-subtle`,
`--ink`, `--muted`, `--line`, `--line-strong`, `--accent`, `--accent-solid`, `--accent-light` and `--on-accent`.
A **theme** sets the semantic roles: `--green`, `--amber`, `--danger`, `--info`, `--omni`, each with a `-light`
background, `--on-danger`, `--focus`, the overlay and layer shadow. A skin never redefines a semantic role, so
success, attention, error, destructive actions and focus keep their meaning in every skin.

| Skin | Character | Brand (light / dark) |
| --- | --- | --- |
| Green (default) | jade-green brand on green-grey neutrals | `oklch(0.47 0.105 166)` / `oklch(0.8 0.11 166)` |
| Orange | the original warm basal palette on warm neutrals | `#b34b23` / `#f2a17b` |

The green brand is bluer than the success green (hue 166 against 145) and is used for fills, links and the
active destination; success always appears as a status label or dot with its word.

Preferences are separate `localStorage` keys: `basal-ui.skin` (`green` or `orange`), `basal-ui.theme` (`light`,
`dark`, `system`) and `basal-ui.lang`. A missing or unknown skin resolves to green; changing skin or theme
applies immediately without a reload. Tokens are defined with same-element attribute selectors
(`[data-skin=green][data-theme=dark]`), so any element carrying both attributes is a scoped specimen; the
catalogue uses this to show all four combinations live.

The selectors sit at the bottom of the sidebar as three labelled segmented groups: Skin, Theme and Language.
Below 800 px the sidebar becomes a top bar; the groups stay visible under its status line and wrap rather
than widen the page.

## Foundations

| Role | Rule |
| --- | --- |
| Spacing | 4 px base: 8 related controls, 12 rows and small groups, 16 between panels and in work grids, 20 panel padding, 32 between catalogue sections |
| Type | Manrope. Page title 24/31 720; section 19/26 700; panel title 15/22 700; body 14/21; controls and dense text 13; labels, hints and table text 12–12.5; badges 11.5 (11 px minimum) |
| Code and paths | IBM Plex Mono 12/20; wrap long paths instead of widening the page; tabular figures for numbers |
| Radius | 4 px badges, 6 px controls, 10 px panels, 12 px dialogs |
| Controls | 34 px; compact row actions 28 px (`.sm`); 38 / 32 px below 600 px; 34 × 20 px switches |
| Icons | Lucide, 1.75 px stroke: 16 px in controls, 18 px in navigation, 20 px in identity and empty states, 12 px in badges |
| Depth | Surface levels and lines; shadows only for dialogs and toasts |
| Motion | 120 ms state changes, 240 ms layers, `cubic-bezier(.2,0,0,1)`; none with reduced motion |
| Width | 1440 px workbench maximum; panels reflow at 1240, 1100, 800 and 600 px |

## Primitives

- **Buttons**: one filled `.primary` per task group; outlined default for secondary actions; `.ghost` for quiet
  actions; `.destructive` (red outline) to enter a destructive flow; inside `.dialog-actions` the destructive
  confirmation is filled red. `.sm` for actions repeated on every row; `.icon-only` only with an accessible name.
  Action icons lead their label; navigation arrows follow it (`.dashboard-link`).
- **Fields**: `.field` (label, control, hint or error) inside `.form-grid` / `.serve-fields`; `.field-wide`
  spans the grid; `.input-icon` for paths and search. A field whose control is `hidden` hides with it. A disabled
  control explains its prerequisite in a `.field-hint` next to it.
- **Choices**: checkboxes render as switches; radios use the brand accent; `.tabs` / `.segmented` for views of
  the same content and short mode choices.
- **Status**: `.badge` with `success`, `warning`, `error`, `info` or `neutral` (and the fit classes) carries a
  shape, a color and a word: circle, triangle, octagon, ring. `.chip` is neutral metadata. `.status-dot` marks
  live state in rows.
- **Notices**: `uiNotice({tone, text, action, href})` for contextual information; `.error-box` stays beside the
  input or action that failed.
- **Empty states**: `emptyState({icon, title, description, action, href | button, compact, inline})`. Default
  for an empty collection, `inline` for a row inside a panel, `compact` for summaries. At most one next step: a
  link, or a button handled through `data-empty-action`. Empty, no-match, loading, failed and disabled are
  different conditions with different copy.
- **Tables and lists**: `.table-wrap` owns one bordered, locally scrolling surface with a quiet header;
  `.list` / `.list-row` and `.disk-group` / `.disk-row` for divided rows with identity left and actions right.

## Compositions

- **Panel**: `.card.panel` with `.panel-head` (title, optional `.panel-sub`, actions), body and optional
  `.panel-foot` for settings that belong to the panel. Do not nest panels inside panels.
- **Work toolbar**: `.toolbar` / `.work-toolbar`; filters and view choices start at the left, `.toolbar-spacer`
  pushes the action group right; it wraps instead of clipping. `.toolbar-field` gives a select a visible label.
- **Overview**: machine strip (`.hardware`: identity and four metrics), then a 2:1 grid of Loaded models and the
  featured suggestion beside On disk and API server `.summary-card`s (state in words, one sentence, one link).
- **Model manager**: in-page section navigation, then On disk (rows, models directory in the footer),
  Statistics, Benchmarks and Calibration panels, each with labelled fields and a `.job-actions` row.
- **Backends**: `.backend-card` with an engine icon, status-toned accent (`data-status`) and facts list;
  unavailable engines use a dashed outline. Test results are `.test-row`s.
- **Batch / API server**: form and results (Batch) or settings and keys (API) side by side above 1240 px.

## Catalogue

`#design-system` shows the skins (four scoped specimens), live tokens, typography, spacing and shape, every
control state, the status vocabulary, notices, icons, the compositions above and the state patterns. Examples
render through the same classes and functions as the application with local example data. They never call the
API, start downloads, load models, create keys or change saved settings, and they avoid the action classes that
`app.js` binds globally (`load-model`, `download-model`, `delete-model`, `unload-model`, `options-model`,
`model-card-open`). Example buttons only update the catalogue's own feedback line.

## Remove the catalogue

Remove this one tag from `basal_ui/static/index.html`:

```html
<script src="/static/design-system.js" defer></script>
```

The navigation entry, page and route disappear; a direct `#design-system` URL falls back to Overview. Optionally
delete `design-system.js` and the `.ds-*` rules at the end of `app.css` afterward. Keep `appearance.js`,
`app.css`, `ui.js` and the shared renderers: the application uses them. No backend, API, package or saved
setting changes.

## Verification

Use the [interface verification recipe](E2E.md#interface-design-system-check) for interface changes. Model
inference remains covered by the separate real-model release gate; a UI pass without downloaded models does not
certify inference.

### Short desktop windows

The brand and appearance footer remain visible. The destination list owns any
vertical overflow rather than scrolling skin/theme/language off screen. At heights
up to 760 px, shared navigation spacing becomes compact so all eleven destinations
and the appearance controls fit a 720 px window.
