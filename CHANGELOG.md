# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project adheres to
[Semantic Versioning](https://semver.org/).

## [0.5.9] - 2026-06-12 — Filterbar reorganization (3-group layout)

Operator UX feedback (lead a2a `b48f7c2c438b464698183d2e95d3bb04`,
2026-06-12): `current UI/UX is terrible` — the filterbar grew to
~108 px tall because every control sat in a single `display: flex;
flex-wrap: wrap` row and the wrap order was chaotic. Reorg into three
explicit groups so the placement is intentional, not flex-wrap-roulette.

### Changed

- **HTML**: wrap filterbar children in `.fb-left` (identity:
  title + version + LIVE chip) / `.fb-center` (search input +
  autocomplete suggest dropdown) / `.fb-right` (Layout segment +
  Sort + Filters popover + Recent count + Group + Add Task +
  blocking-me + project-hide + hidden + Reload).
- **Second row** `.filterbar-chips` — active filter chips
  (`#filt-chips`) + qualifier hint pills (`#filt-qhints`) moved off
  the main row into a slim band shown ONLY when populated (via
  `:has(...:empty)` selector). Default state collapses to a single
  ~48 px row.
- **CSS**: `.filterbar { display: flex; min-height: 48px }` with NO
  top-level wrap. The `.fb-right` group wraps internally on narrow
  viewports so the identity + search row stays intact regardless of
  how many right-side controls are visible.
- **Removed** the `margin-left: auto` hack from `.toggle-block` —
  explicit grouping now controls position; the auto-margin pushed
  this single button to the right edge in the old layout, which was
  the source of the asymmetric wrapping the operator photographed.

## [0.5.8] - 2026-06-12 — Graph view edges fix + fleetstrip removal + search-kbd pill folded into placeholder

Operator-reported regressions on `/`, lead-approved fixes:

### Fixed — Graph layout had no edges

- Lead `c212aa72bb0a4161b4faa8e81d508bc8` / `8af2a4a65fe94c9aa0e5f774598127a0`.
  PR #108's Graph view tried to read `t.depends_on` / `t.blocks` per-node
  and emitted a 400-node, 0-edge layout (41 552 px tall — operator
  element-inspector confirmed). The `/graph` endpoint doesn't expose
  those per-node — it returns them aggregated at top-level
  (`STATE.graph.edges`, 26 entries in the operator's live store).
  Hierarchical `parent` edges (111 of them, per-node) were also
  missing.
- Fix: `_renderGraphView` now consumes `STATE.graph.edges` directly
  for the depends_on / blocks set, and walks `t.parent` for the
  hierarchical edge set. Edges are visually distinguished — solid
  arrows for depends_on / blocks, dashed for parent. Graph is
  filtered to the connected component (nodes touched by ≥1 edge);
  disconnected nodes render in Column / Table layouts only.
- New empty-state: when 0 edges among the visible scope, the canvas
  shows a friendly explanation pointing at the `depends_on` / `blocks`
  / `parent` YAML encoding so the operator can fix the data.

### Removed — empty fleetstrip + standalone kbd-hint pill

- Lead `032e41545fcf4ab4b98d864ec1770249`. The `div#fleetstrip`
  rendered as `Content: none` because the payload never populated
  `STATE.graph.fleet` — operator: "i dont need this". The element +
  the orphan `renderFleetStrip()` helper are removed; fleet-liveness
  lives in the lead's periodic reports.
- The standalone `span.filt-search-kbd` "press / to focus" pill is
  removed. The same hint is now inline in the search input's
  placeholder text — operator: "just write the kbd in the search box".

## [0.5.7] - 2026-06-12 — User lane normalization + tighten left space + age pill + finish BLOCKING-YOU removal

Lead-HOLD-approved follow-up to 0.5.6 (PR #107, rebased on top of the
P0 LAYOUT-axis + Recent-sort merge):

### Changed — User lane normalization

- **The 360 px BLOCKING-YOU right-side aside is now FULLY removed.**
  0.5.6's render-refactor removed the JS that populated `#block-rows`
  but left the `<aside id="right-panel">` HTML in the template — the
  operator saw "loading…" forever in the right sidebar. This PR
  finishes the job: the `<aside>` block + the mobile `#by-fab` toggle
  + the `toggleByDrawer` / `updateByFabBadge` helpers are all
  removed. Operator-decision-blocked tasks live in the synthesized
  `user` lane in `_renderColumnView` (a normal column with normal
  width, drag-reorder, pin, column-context-menu).

### Changed — Left space tightened

- Board overrides the scitex-ui standalone shell so the
  `ws-ai-pane` (console / chat), `ws-worktree-pane` (file tree), and
  `ws-viewer-pane` (file viewer) are `display: none`. The kanban
  doesn't need any of those, and the operator reported "left empty
  space" eating the columns area. The `ws-module-pane` (board
  content) now uses the full viewport width.

### Added — Card age pill

- Each card carries a `⏳ Nd` pill in the header next to
  `last <activity>`. Stale color buckets:
  `today` mint-green "new" / `fresh 1–6d` muted / `aging 7–29d`
  amber / `stale 30–89d` orange / `rotten ≥90d` saturated red.
  Source is `created_at` (preferred) with `last_activity` fallback;
  null when neither parses (back-compat: legacy data shows no pill
  instead of `NaN`).
- CSS in `board_v3/02-card.css` (`.age-pill` + 5 modifiers, same
  shape as the existing `.date-pill` family).

## [0.5.6] - 2026-06-12 — Board v0.5.4 P0: empty-pill fix + LAYOUT axis + Recent sort

Lead-prioritized fix after PR #105 verification miss surfaced two
still-broken symptoms on `/` (the operator's primary board page):

### Fixed — cards rendered as empty pills on every lane

- Diagnosed against the live store at `:8051`: `business` lane's 28
  cards rendered with `offsetHeight = 24 px`; `scitex-dev` 68 cards at
  18 px; `paper-scitex-clew` 15 cards at 42 px. Root cause: `.col-body`
  is `display: flex; flex-direction: column` and `.main { height:
  100%; overflow: hidden }` (added in 22b6a6f to keep the BLOCKING-YOU
  aside from stretching). Inside a bounded flex column container,
  child `.card` items defaulted to `flex-shrink: 1` and compressed
  down to the card-status row when content exceeded container height.
- Fix: `.card { flex-shrink: 0 }` in `board_v3/02-card.css`. Cards
  keep their natural content height; the existing `.col-body
  { overflow-y: auto }` lets excess content scroll inside the column.
  Verified live by injecting the rule via Playwright `add_style_tag`:
  every lane's first card grew from 18-42 px back to 111-150 px.

### Added — LAYOUT axis (Graph | Column | Table) + Recent sort

Lead design ruling (TG 12461, operator-confirmed): the board renders
the SAME data along two orthogonal axes — LAYOUT (Graph | Column |
Table) sits in the filterbar; TIME (Recent) is a SORT mode in the
existing Sort dropdown, applies across all layouts.

- **LAYOUT switcher** — three segmented buttons in the filterbar.
  Persisted in `localStorage["scitex-todo:layout"]`.
  - `📋 Column` — the existing kanban (default).
  - `📑 Table` — flat rows view, sortable, click a row to open the
    detail drawer. Status / Title / Project / Blocker / Priority /
    Last activity columns.
  - `📊 Graph` — depends_on / blocks mermaid graph, lazy-loads
    `mermaid@10` from jsdelivr the first time the operator switches
    to it.
- **Recent sort mode** — `Recent (newest first) 🆕` option added to
  the existing `#f-sort` dropdown. Cards sort by `last_activity →
  created_at` desc; cards with activity in the last 24 h get a gold
  `NEW` badge in `.card-top`. The badge renders across every layout
  when sort = recent. Persisted in `localStorage["scitex-todo:sort"]`.
- **🆕 N new in 24 h pill** — always-visible filterbar indicator
  showing how many of the currently-visible cards moved in the last
  day. Click to set Sort = Recent. Hidden when zero.

CSS lives in a new sibling file `board_v3/06-layout-and-recent.css`
(keeps the per-file CSS under the 512-line hook limit). Linked from
`board_v3.html`'s `{% block extra_css %}`.

## [0.5.4] - 2026-06-12 — Board v0.5.3 display fix (template leak + bundle/template food)

Operator-reported regression after the 0.5.3 release:

- Multi-line `{# … #}` comment block leaked verbatim ("`{# searchQuery.js …
  #}`") into the rendered HTML at the top of every board page.
- Cards in every lane rendered as empty pills (no text) — `board_v3/*.css`
  had been wiped from the static dir.
- View toggle (Graph / Table / Recent) was invisible — the React SPA bundle
  was out of sync with the TypeScript source.

### Fixed

- **Template comment leak** (PR #105). Replaced the two multi-line `{# … #}`
  blocks in `board_v3.html` with `{% comment %} … {% endcomment %}`.
  Django's `{# … #}` is single-line only; multi-line blocks render their
  body as page text. Already pinned by
  `test_standalone_template_does_not_leak_django_comment` in
  `tests/scitex_todo/_django/test_views.py`.
- **Bundle/template food (root cause)** (PR #105). The vite config wrote
  into `../static/scitex_todo` with `emptyOutDir: true`, which wiped the
  SIBLINGS of `assets/` on every rebuild — `favicon.svg`,
  `board_v3/*.css`, and `board_v3/searchQuery.js`/`searchSuggest.js` are
  all tracked-in-git static assets consumed by the live `board_v3.html`
  template. We now scope `outDir` to the `assets/` subdir, so a rebuild
  only ever touches the React SPA bundle and never the board_v3 statics.
- **Bundle rebuild from current TS source** (PR #105). Clean
  `npm install` + `vite build` ran against the post-#104 source so the
  shipped `assets/index.js` / `assets/index.css` matches the TypeScript
  source (the Graph / Table / Recent toggle ships with the bundle).

## [0.5.3] - 2026-06-12 — Board UX wave + self-consuming loop + deadline schema

Captures every PR that landed between 0.5.2 and develop tip (operator
TG 12028 / 12038 / 12081 wave).

### Added — Board UX

- **Search-as-launcher** (PR #86). `#f-search` becomes the primary
  filterbar control; press `/` from anywhere to focus, `Esc` to blur.
  Purple-haloed at rest, brighter on focus, kbd-hint chip advertises
  the affordance.
- **Filter UX collapse + active chips + sort-by** (PR #89). Six
  filter dropdowns hide behind a single `🔧 Filters (N active)`
  popover; active filters render as removable chips; new sort-by
  selector (deadline / priority / status / project / last_activity /
  title).
- **Self-named project-umbrella cards hidden** (PR #87). A card
  whose title matches its column name is suppressed inside that
  column.
- **Move-picker lists ALL projects + Create-new** (PR #88 + #94).
  Right-click → Move picker is a Combobox over every project in the
  store with `+ Create '<query>'`.
- **Combobox primitive in scitex-ui** (scitex-ui #36 + #37, consumed
  via PR #94). Fuzzy-typeahead select layered over the six filterbar
  dropdowns + the move-picker; pure-JS bundle for Django-template
  consumers.
- **Project GROUPS** (PR #91). User-defined clusters of projects;
  new top-level `groups:` key in `tasks.yaml`; `spans_all: true`
  banner above the grid; group-header rows between clusters.

### Added — Deadlines + org-mode bridge

- **`deadline` + `scheduled` fields on `Task`** (PR #92). ISO-8601
  strings; validator rejects empty / unparseable / `deadline <
  scheduled`.
- **Org-mode export adapter** (PR #93). `build_org(tasks) → str`
  emits `DEADLINE:` / `SCHEDULED:` + properties drawer. 17 tests.
- **Multi + recurring deadlines** (PR #97). Org-style repeater suffix
  on the single field (`+1w` / `++2m` catch-up); optional `deadlines:
  list[str]` mutually exclusive with the single field; server emits a
  synthetic `deadline_next` for FE consumption.

### Added — Self-consuming board loop (operator TG 12038)

- **`scitex-todo next` CLI verb** (PR #95). Canonical "what to pick
  up next" predicate. `--mine` reads `SCITEX_TODO_AGENT`;
  `--auto-claim` atomic-flips to `in_progress` + stamps a starting
  comment in one write.
- **`scitex-todo watch --push` CLI verb** (PR #95). Polls tasks.yaml,
  diffs, POSTs `/v1/turn` to the owning agent's a2a port on
  new / commented / status-changed tasks. Watcher declared as a
  second `kind=service` JobSpec.
- **Agent self-consumption loop sub-skill (32)** + **MANDATE block in
  `SKILL.md`** (PR #90 + #95).

### Fixed

- **P1 + P7 regressions restored** (PR #96). The P10/P11 squash wave
  silently dropped P1 #86 + P7 #87 from develop; PR #96 restores both
  and pins **24 substring signatures** in
  `tests/scitex_todo/test__board_v3_signatures.py` so future squash
  drops fail CI instead.

### Notes for operators

After upgrading: `systemctl --user restart scitex-todo.dashboard`.
The new `scitex-todo.wake-watcher` unit needs
`systemctl --user reset-failed scitex-todo.wake-watcher` followed by
`systemctl --user enable --now scitex-todo.wake-watcher`.

## [0.4.2] - 2026-06-08 — Crash-safe store + version label + Uncategorized column

Patch release in response to the 2026-06-08 autoassign-parallel-run
data-loss incident: roughly 130 operator-added tasks lost when two
concurrent autoassign scripts were SIGTERM'd mid-`save_tasks` dump
and the store was left half-written. This release closes the bug at
the store layer + makes the live release visible on the board.

### Fixed (crash-safety, lead a2a `3b0df14a`)
- **Atomic write in `save_tasks`** — dump now goes to a sibling `.tmp`
  file, fsync, then `os.replace(tmp, tasks.yaml)`. POSIX-atomic; a
  SIGTERM/SIGKILL mid-dump can no longer leave the canonical file
  half-written. The pre-existing `fcntl.flock` on the sidecar lockfile
  is unchanged.
- **Git auto-commit on every save** — lazy-initializes a `.git` inside
  the store directory on first save_tasks call, then commits each
  successful write. Operator gets time-travel: `git -C ~/.scitex/todo
  log` + `git show <sha>:tasks.yaml` to restore any prior state.
  Best-effort: a git failure never blocks the actual save.

### Added (board v3)
- **`scitex-todo vX.Y.Z` page title + header** (operator TG 407). The
  live `__version__` is read off the package import and rendered in
  both the `<title>` tag (browser tab) and the in-page H1. No second
  source of truth to drift on release.
- **"Uncategorized" replaces "Ungrouped"** (operator TG 405). The
  no-project column label aligns with the legacy "Uncategorized pool"
  convention from PR #4 and reads as plain English. Internal grouping
  key + filter dropdown both updated.

### Notes for operators
After upgrading: restart your `scitex-todo board` systemd unit.
`~/.scitex/todo` becomes a git repo on the first board write — the
operator can `git -C ~/.scitex/todo log` immediately, no extra setup.
Any future corruption is recoverable via standard git commands.

## [0.4.1] - 2026-06-08 — Board v3 horizontal layout + column pin + drag-reorder + fleet-liveness

Patch release on top of 0.4.0 to unblock operator UX (TG 370) the
moment they saw 0.4.0 live: project columns stacked vertically with
many projects + no way to reorder / prioritize them.

### Fixed (board v3)
- **Columns now lay out side-by-side with horizontal scroll.** The
  previous CSS grid `repeat(auto-fit, minmax(220px, 1fr))` wrapped
  many-column boards into a 40,000px tall stack (operator's element-
  inspector dump confirmed 39929px height). Switched to a single-row
  flex strip with `overflow-x: auto`; each column is a fixed 280px
  wide. Kanban / Trello / Linear convention.

### Added (board v3)
- **Column drag-to-reorder.** Each column section is `draggable`;
  drop on another column inserts BEFORE that target. Order persists
  in `localStorage` under `scitex-todo:col-order` (per-browser
  preference, no backend change).
- **Column pin (📍 / 📌).** Per-column pin button in the header.
  Pinned columns float to the LEFT of the strip regardless of drag
  order. Persists in `localStorage` under `scitex-todo:col-pinned`.
- **Fleet-liveness dot-strip** (PR #75) — one colored dot per agent
  in the filter bar, gold/green/blue/grey by status, click toggles
  the agent filter. Powered by a new `fleet` summary on `/graph`
  (additive — no schema change).

## [0.4.0] - 2026-06-08 — Board v3 + scitex-ui shell + task-harvest skill

The shared-fleet board matures into a real Django app: the live
**board v3** (kanban + BLOCKING-YOU panel + Resolve → notify wire) is
promoted to the package root and now extends the **scitex-ui shell**
so it picks up the Alt+I element-inspector + shared chrome for free.
The **Task dataclass** becomes the single schema source. The
**task-harvest skill** documents the operator-commissioned backlog
burn-down loop (2-state model, 4-value blocker enum, root-blocker
walk, `scitex-dev cron` registration). Compute-state-deps + decision-
nodes + ports skeleton land for the north-star roadmap.

### Added (board)
- **Board v3** — live Django board (kanban-style columns, status
  filters, BLOCKING-YOU panel, Resolve → a2a notify wire). Promoted to
  root URL (`/`); legacy GraphView demoted to `/legacy/`. (#57, #58.)
- **scitex-ui shell integration** — board v3 extends
  `scitex_ui/standalone_shell.html`, so Alt+I element-inspector +
  shared chrome work the same way on board v3 as on the legacy
  GraphView. Compatibility with scitex-hub register-as-module via
  `scitex_app._django.ScitexAppConfig` preserved. (#69.)
- **CRUD endpoints** on the Django backend (`/create`, `/update`,
  `/delete`, `/comment`, `/edge`, `/restore`, `/priority`, `/resolve`)
  — see `handlers/crud.py`; UI wiring on board v3 ships incrementally.
- **Board v3 Resolve safety** — 2-click confirm + Undo toast + new
  `/reopen` endpoint so an accidental Resolve is recoverable. (#61.)
- **Board v3 comments + priority + hide** — Word-style comment thread
  + per-card priority up/down + hide button. (#62.)
- **ESC closes the detail modal** (operator TG 265). (#59.)
- **Drill-down clarity** — empty-state explainer, Pool label, Back
  button + region labels (Board / Drill / Canvas / Pool) + count
  breakdown (Total·Showing·Nested·Pool). (#50, #51.)
- **Hover affordance** — replace parent-node tilt with a "⊞ Drill in"
  hover-hint pill (operator TG 245). (#53.)

### Added (schema / Task dataclass)
- **Task dataclass = single schema source** (#56). All schema
  validation flows through one dataclass; `_validate_tasks` consumes
  it; the Gitea adapter + the future README-frontmatter SSoT both
  consume the same shape. 9 new operator fields (`task` /
  `last_activity` / `host` / `pr_url` / `issue_url` / `agent` /
  `project` / `goal` / `created_at`) land.
- **D11 stamping** (#67) — `created_at` is auto-stamped on `add_task`;
  `last_activity` is auto-stamped on `update_task`.
- **Field-flag expansion for `add` / `update`** + closed-enum CLI
  validation (#65). Every operator-facing field is now a `--flag` on
  the CLI; closed enums (`status` / `kind` / `blocker`) reject typos
  at write time.
- **Compute-state-deps north-star pillar #1** (#52) — `kind` enum
  (`task` / `compute`) + compute metadata (`job_id` / `host` /
  `command` / `started_at` / `finished_at`) + ⚙ glyph + KV table.
  Compute jobs (Spartan / SIF builds / CI) become first-class graph
  nodes that external watchers can flip done.
- **Decision-nodes + closed BlockerKind enum** (#54) — `kind: decision`
  + ⚖️ glyph + LOUD operator-decision halo + "unblocks N" impact badge
  + 👤 awaiting-you lens. North-star pillar #4.
- **Core / Extension Ports / Fleet Adapters skeleton** (#55) — ADR-0006
  backbone for the open-source / fleet-adapter split.

### Added (skills)
- **`11_adopting-from-a-project`** (#60) — 30-second adoption how-to
  for project agents to write their tasks into the shared board.
- **`40_task-harvest`** (#70, #72) — operator-commissioned backlog
  burn-down protocol: 2-state model (BLOCKED + reason + dependency
  from a 4-value enum vs RUNNABLE), 2-phase sweep cycle (Phase 1
  re-check blockers + walk `task-dependency` chains to their LEAF
  root-blocker; Phase 2 escalate every RUNNABLE task to its owning
  agent via a2a), lead-centric funnel routing, and registration as a
  `scitex-dev cron` JobSpec.

### Fixed
- **`scitex-todo board --tasks PATH`** now actually pins the server's
  store (was previously a no-op for the Django subprocess — only the
  browser URL query was set). (#46.)
- **Audit pipeline unblocked** — TQ002 / TQ007 + PS-202 / PS-204
  violations fixed. (#68.)

### Notes for operators
After upgrading: restart your `scitex-todo board` systemd unit so the
board picks up the scitex-ui-shell extension. Alt+I + element-
inspector work immediately after restart. CRUD UI on board v3 wires
to the existing endpoints incrementally — Resolve + Priority +
Comment + Hide already land in this release; full Create / Update /
Delete UI ships in a follow-up patch.

## [0.3.0] - 2026-06-04 — Phase 1 MVP: shared-fleet TODO

The universal-task-layer FLOOR for the agent fleet. Every agent can
read/write the same YAML store across hosts, the board at
http://127.0.0.1:8051 aggregates everyone's tasks for the operator,
and the Python API / CLI / MCP surface follows scitex-dev audit
conventions (Convention A: tool_name == python_api_name).

### Added
- **Per-task `scope` / `assignee` fields** (additive-optional, free-form
  strings). Convention is `agent:<name>` / `project:<name>` / `private`
  but the schema doesn't enforce it (Req 8: be generic).
- **`_log_meta` mapping** — opaque event-stamp dict; `complete_task` writes
  `completed_at` (ISO-8601 UTC, `Z`-suffixed, second resolution) +
  `completed_by`. Phase-2 progress-history substrate.
- **Mutation Python API** (`scitex_todo._store`, re-exported from
  `scitex_todo`): `add_task`, `update_task`, `complete_task`, `list_tasks`,
  `summarize_tasks`, `resolve_store`, `TaskNotFoundError`, `ENV_SCOPE`,
  `ENV_AGENT`. The public top-level surface is narrowed to these six
  task-store functions (plus errors / env constants) to satisfy audit §6
  (Convention A: tool_name == python_api_name). The mermaid / render /
  model / paths helpers remain importable from their submodules
  (`scitex_todo._mermaid`, `scitex_todo._render`, `scitex_todo._model`,
  `scitex_todo._paths`).
- **CLI write / admin verbs**: `add`, `update`, `done`, `summary`, plus
  `list-tasks` (extended with `--scope` / `--assignee` / `--status`
  filters; backward-compatible default output for existing `list-tasks`
  users), `resolve-store`, `init-store [--shared|--project]`,
  `sync-store [--dry-run|--apply]` (Phase-1 stub). Mutating verbs
  (`add`, `update`, `init-store`, `sync-store`, `mcp start`, `mcp install`)
  accept `--dry-run` + `-y`/`--yes` per audit §2. The pre-audit names
  `list` / `where` / `init` / `sync` were renamed per audit §1 (bare
  transitive verbs at the top level need an object noun).
- **MCP server** (`scitex_todo._mcp_server`) behind the new `[mcp]` extra
  (`fastmcp>=2.0`). Eight tools — six task-store tools follow
  Convention A (tool_name == python_api_name, no prefix): `add_task`,
  `update_task`, `complete_task`, `list_tasks`, `summarize_tasks`,
  `resolve_store`; plus `todo_skills_list` / `todo_skills_get` for
  bundled-skill discovery. `import scitex_todo` works fine without the
  extra installed.
- **`mcp` CLI subgroup** — §3 required four (`start`, `doctor`,
  `list-tools`, `install`). Prefers `scitex_dev._mcp_cli` when present;
  hand-rolled fallback otherwise.
- **`fcntl.flock` mutex** on `save_tasks` (and the new mutators in
  `_store`) holding the full read-modify-write cycle. Phase-1 prereq for
  the Phase-2 cross-host sync substrate (Req 2).

### Documented
- `GITIGNORED/ARCHITECTURE.md` — Phase-0 9-requirement → mechanism map.
- `GITIGNORED/QUESTIONS.md` — open defaults for the operator/lead.
- `GITIGNORED/PROPOSAL_scitex-dev-ecosystem-register.md` — paste-apply
  diff for the lead so `scitex_dev.ECOSYSTEM` includes `scitex-todo`
  (Req 6).

### Test surface
- +47 real tests (no mocks). The two-subprocess concurrent-writer test
  proves the lock serializes interleaved inserts (the failure caught
  while writing it was the source of the `_save_tasks_unlocked` split —
  the lock has to wrap the full read-modify-write, not just the write).

## [0.2.0] - 2026-05-27

### Added
- Web board (read-only React-Flow dependency graph) served by Django:
  `scitex-todo board` (needs the `[web]` extra). Nodes colored by status,
  `depends_on` arrows, `blocks` inhibition edges, clickable cards, and
  nested-graph drill-down via a new `parent` task field.
- Drag-reorder write path: the board's `POST /priority` handler persists a new
  ordering back to the YAML store (preserving comments via ruamel) — the first
  agent↔user GUI write surface. `save_tasks` is now public.
- §1a CLI introspection: `list-python-apis` (with the additive `-v/-vv/-vvv`
  ladder) and `mcp list-tools`, both with `--json`.
- Shell completion: `install-shell-completion` / `print-shell-completion`
  (bash/zsh/fish) using the static cache-file pattern.
- Agent skills: bundled `_skills/scitex-todo/` (installation, quick-start,
  python-api, cli-reference, env-vars) plus a self-contained
  `skills {list, get, install}` CLI group.
- `python -m scitex_todo` entry point; `.env.example`; `examples/` with a
  matching `tests/examples/` smoke test; cross-package integration gate.

### Changed
- **CLI verbs renamed** to noun-verb compounds (audit §1): `render` →
  `render-graph`, `list` → `list-tasks` (now with `--json`). Added top-level
  `--help-recursive` and `--json`.
- `_cli.py` split into a focused `_cli/` package (`_main`, `_introspect`,
  `_completion`, `_skills`).
- README rebuilt to the canonical SciTeX layout (logo, badges, Problem/Solution,
  Architecture diagram, Interfaces, footer); `docs/roadmap.md` refreshed.
- Added GitHub Actions: `tests`, `import-smoke`, `quality`, tag-driven
  `release` (PyPI via OIDC + GitHub Release), and the CLA gate.
- Test suite reorganized to mirror `src/` and to satisfy the test-quality rules
  (one assertion per test, AAA markers).

[0.2.0]: https://github.com/ywatanabe1989/scitex-todo/releases/tag/v0.2.0

## [0.1.0] - 2026-05-22

### Added
- Canonical YAML task store: top-level `tasks:` list with `id` / `title` /
  `status` (required) and optional `repo` / `depends_on` / `blocks` / `note`.
- `load_tasks` — validating loader (`TaskValidationError` on missing id/title,
  duplicate id, or invalid status). Statuses: `goal`, `pending`,
  `in_progress`, `blocked`, `done`, `deferred`, `failed`.
- Mermaid adapter: `build_mermaid` renders `flowchart TB` with `depends_on`
  arrows, `blocks` inhibition edges (`-- blocks --x`), and per-status colors
  (goal = gold `#ffe082`).
- Renderer: `render` (mmdc-first with auto-discovered puppeteer/playwright
  chromium and `--no-sandbox`; `kroki.io` fallback).
- Task-store path resolution following the SciTeX local-state convention:
  explicit path -> `$SCITEX_TODO_TASKS` -> project `.scitex/todo/tasks.yaml`
  -> user `~/.scitex/todo/tasks.yaml` -> bundled generic example.
- CLI `scitex-todo` (Click, noun-verb): `render`, `list`.
- Bundled generic example task store at `scitex_todo/examples/tasks.yaml`.

[0.1.0]: https://github.com/ywatanabe1989/scitex-todo/releases/tag/v0.1.0
