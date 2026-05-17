# Handoff: dashboard v0

Date: 2026-05-17
Author: Claude (Cowork session)
Scope: First visible cut of the `/cards` dashboard. Single-file HTML, no
backend, no build step, no dependencies.

## What shipped

`dashboard/v0/index.html`. 33 KB. Open in Chrome or Edge.

Feature checklist against the v0 spec:

- Folder picker on first load. Uses `window.showDirectoryPicker()`.
  Handle is persisted in IndexedDB under `agile-cards-dashboard/handles`
  with key `todoRoot`. On subsequent loads the dashboard queries
  permission silently. If still granted, it boots straight to the board.
  If the grant lapsed (Chrome treats permission as per-session by
  default), the splash shows a "Reconnect folder" button that triggers
  `requestPermission` under a user gesture.
- Five columns in spec order: Backlog, Active, Awaiting Amendment Review,
  Done, Blocked. Each shows a count badge in the header.
- Card tiles show title, tier badge (T1-T6, color-coded per the haiku /
  sonnet / opus families with darker variants for extended-thinking
  tiers 2/4/6), project name (last path segment), `claimed_by` when set,
  and the first 100 chars of the `## Context` paragraph.
- Click a card to open a right-side detail panel. Shows full YAML
  frontmatter as a key/value list and the rendered markdown body. Close
  via the X, the backdrop, or Escape.
- HTML5 drag-and-drop between columns. On drop the file is read from the
  source subfolder, its `status:` field is rewritten, it is written to
  the destination subfolder, and the source is removed. The
  `awaiting_amendment_review` field maps to the `amendments/` folder per
  the v1.1 spec asymmetry. Failures surface a toast and the board
  re-renders from disk so visuals never drift from truth.
- Refresh button re-reads every column.
- Empty columns show a quiet "no cards here" line.
- Header has a logo dot, the title, a `v0 read-write demo` tag, the
  currently connected folder name, a "Change folder" link, and the
  Refresh button.

### Parsers

Both parsers are hand-rolled and live inside the single file. They were
exercised against `examples/b001-03-add-rate-limit-middleware.md` and
explicit unit cases for inline markup.

YAML parser handles:

- Top-level `key: value` lines (scalars: string, integer, float, bool,
  null, `~`).
- Quoted strings (single and double).
- Inline empty list `[]`.
- Block lists with `  - item` indented under an empty-valued key.
- Comments. The `#` comment-stripper is quote-aware so a `#` inside
  `"..."` or `'...'` is preserved.

Status rewrite is a targeted regex replace on the `status:` line, so the
rest of the document is preserved byte-for-byte (including comments,
ordering, and trailing whitespace).

Markdown renderer handles:

- ATX headings (h1-h6).
- Paragraphs.
- Unordered (`-` or `*`) and ordered (`1.`) lists, including indented
  continuations.
- Fenced code blocks with optional language tag.
- Inline: bold (`**` and `__`), italic (`*` and `_`), inline code
  (backticks), and `[text](url)` links (opened in a new tab with
  `rel="noopener"`).
- Blockquotes.
- HTML is escaped before inline markup is reintroduced. The card body
  cannot inject script tags via the renderer.

## Verification

47 assertions pass under Node. The harness lives transiently in
`/tmp/test_harness.js` during the build session and is not committed.
The assertions cover:

- The full set of YAML fields on the real example card.
- Status rewrite roundtripped (including the asymmetric
  `awaiting_amendment_review`).
- Project name parsing for both Windows and Unix path styles, plus null
  safety.
- Context extraction with 100-char truncation.
- Markdown renderer over real body content plus targeted inline cases.
- XSS escape so a `<script>` in body text becomes entities.

HTML validity was checked structurally (one root, balanced tags). JS was
parse-checked with `node --check`.

## Known limits

- **Chromium only.** The File System Access API does not ship in Firefox
  or Safari. The dashboard detects `window.showDirectoryPicker` and
  shows a clear "use Chrome or Edge" message instead of a half-broken
  board. No workaround is planned for v0.
- **Permission grants are per session.** Chrome currently does not
  remember the `readwrite` grant across browser restarts even though the
  `FileSystemDirectoryHandle` itself survives in IndexedDB. Each new
  session shows a one-click "Reconnect folder" button. v0+ can attempt
  the `"persistent"` permission upgrade when that ships more broadly.
- **Non-atomic moves.** The FSA has no `move`. A drag is read source ->
  write destination -> delete source. If the browser dies between
  the write and delete, the card exists in both folders. The runner
  contract documents atomic rename as the canonical move primitive, so
  this is a v0 shortcut, not the long-term answer. The future FastAPI
  backend (v1) will issue the actual atomic rename.
- **No file watcher.** Drew has to hit Refresh to see runner-side changes.
  Future versions can poll or use `FileSystemObserver` once it ships.
- **No batch view, no sprint scheduler.** Cards are flat. The
  `_batches/` folder is ignored. No filtering by batch, project, model
  tier, or claimed_by yet. Planned for v0+.
- **`.cards-config.yaml` is ignored.** The per-project config that can
  redirect the runtime data folder is not consulted; Drew picks the
  folder manually.
- **Status field is the only YAML field that gets rewritten on drop.**
  We intentionally do not touch `claimed_by`, `started_at`, etc., even
  though moving from `backlog/` to `active/` would conceptually want
  those set. The runner is the only thing allowed to claim a card, per
  the runner contract. Drag-drop in the dashboard is operator
  intervention, not normal flow.

## Queued for v0+

- Real backend (FastAPI service running locally) so the dashboard works
  in any browser and writes go through the atomic rename primitive.
- Filter bar (by project, batch, tier, claimed_by).
- Batch manifest awareness (`_batches/`): a side panel that lists open
  batches and lets Drew jump to the cards in each.
- Sprint scheduler view (the SKILL.md "future work").
- Optimistic UI on drag without a full re-render.
- A small "Stats" footer (token estimates vs actuals once cards start
  reporting them).
- Live updates without a manual refresh.
- Commit / push hooks once the agile-cards GitHub remote is wired.

## File locations

- Dashboard: `C:\dev\agile-cards\dashboard\v0\index.html`
- This handoff: `C:\dev\agile-cards\dashboard\v0\HANDOFF_2026-05-17_dashboard-v0.md`
- Runtime data Drew should point the picker at:
  `C:\dev\todo\` (currently empty in the working copy).
