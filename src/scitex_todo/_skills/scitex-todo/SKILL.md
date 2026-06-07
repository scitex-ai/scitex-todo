---
name: scitex-todo
description: |
  [WHAT] Canonical YAML task store with pluggable adapters — validate a
  `tasks:` list (id/title/status + depends_on/blocks/priority/parent) and
  render it as a mermaid dependency graph (PNG), a read-only React-Flow web
  board, or a plain task listing.
  [WHEN] Use when the user wants to "track tasks as a dependency graph",
  "render my todo as a diagram", "show what blocks what", "list tasks from
  tasks.yaml", or "launch the todo board".
  [HOW] `import scitex_todo as todo` for the Python API; `scitex-todo --help`
  for the CLI.
tags: [scitex-todo]
primary_interface: python
interfaces:
  python: 3
  cli: 2
  mcp: 0
  skills: 2
  http: 0
---

# scitex-todo

A canonical YAML task store with pluggable adapters. The YAML document
(top-level `tasks:` list) is the single source of truth; adapters render or
import it. The mermaid adapter (YAML → dependency PNG) and a read-only web
board ship today; org-mode and drag-to-reprioritize are on the roadmap.

The store is resolved in precedence order: explicit `--tasks` →
`$SCITEX_TODO_TASKS` → project `<git-root>/.scitex/todo/tasks.yaml` → user
`~/.scitex/todo/tasks.yaml` → the bundled generic example.

## Sub-skills

### Core (01–09)
- [01_installation.md](01_installation.md) — install + import sanity check
- [02_quick-start.md](02_quick-start.md) — load → build_mermaid → render
- [03_python-api.md](03_python-api.md) — public callables and the schema
- [04_cli-reference.md](04_cli-reference.md) — `scitex-todo` subcommands
- [05_mcp-tools.md](05_mcp-tools.md) — the MCP tool surface (Convention A)

### Workflows (10+)
- [10_campaign-tracking.md](10_campaign-tracking.md) — companion tools
  (`check_releases.py`, `campaign_report.py`) under `~/.scitex/todo/`
  for multi-package release/audit campaigns
- [11_adopting-from-a-project.md](11_adopting-from-a-project.md) — the
  30-second adoption path: how a project agent (clew / neurovista /
  scitex-dev / scitex-hub / ripple-wm / scitex-orochi / scitex-agent-
  container / etc.) writes its tasks to `~/.scitex/todo/tasks.yaml` so
  the operator's live board (http://127.0.0.1:8051/) auto-renders the
  agent's column. Operator-decision blockers + GUI Resolve loop are
  covered here too. **READ THIS FIRST** if your agent doesn't yet
  appear on the operator's board.

### Meta (20+)
- [20_env-vars.md](20_env-vars.md) — environment variables and local state

### Architecture (30+)
- [30_two-tier-conventions-and-write-protocol.md](30_two-tier-conventions-and-write-protocol.md)
  — full fleet spec: project tier vs global tier, write-protocol table
  (who writes when), cross-host sync (git-backed durable + SSH-fanout
  live), task referencing scheme, push-notification model, Core vs
  Extension Ports vs Fleet Adapters architectural backbone. Reference
  spec; for the short how-to, use 11.

### Operations (40+)
- [40_task-harvest.md](40_task-harvest.md) — task harvest:
  blocker-driven backlog consumption. Two-state model (BLOCKED with
  reason+dependency from a 4-value enum vs RUNNABLE), 2-phase harvest
  pass (Phase 1 re-check blockers + walk `task-dependency` chains to
  their LEAF / root blocker; Phase 2 escalate every RUNNABLE task to
  its owning agent), lead-centric funnel routing, and registration as
  a `scitex-dev cron` JobSpec (the ecosystem plugin pattern, same
  shape as `ci-watch` / `quota-keepalive`). Keeps consumption rate >
  arrival rate so the board doesn't drift out of sync with the
  codebase. Name locked by operator TG 332 + 335: must carry "task";
  no "branch" / "graph" metaphors.
