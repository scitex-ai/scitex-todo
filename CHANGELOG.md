# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project adheres to
[Semantic Versioning](https://semver.org/).

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
