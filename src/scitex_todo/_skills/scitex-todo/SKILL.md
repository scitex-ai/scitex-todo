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

### Campaign helpers (10+)
- [10_campaign-tracking.md](10_campaign-tracking.md) — companion tools
  (`check_releases.py`, `campaign_report.py`) under `~/.scitex/todo/`
  for multi-package release/audit campaigns

### Meta (20+)
- [20_env-vars.md](20_env-vars.md) — environment variables and local state
