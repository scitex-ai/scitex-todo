---
description: |
  [TOPIC] CLI Reference
  [DETAILS] scitex-todo CLI subcommands (Click, noun-verb) — render-graph,
  list-tasks, board, plus the standard introspection / completion / skills
  commands and universal flags (--help-recursive, --json).
tags: [scitex-todo-cli-reference]
---

# CLI Reference

```bash
scitex-todo --help
scitex-todo --help-recursive    # flattened help for every subcommand
```

| Command | Purpose |
|---|---|
| `scitex-todo render-graph [-o PNG] [--tasks PATH] [--print-mermaid]` | Render the dependency graph to PNG (or print mermaid source). |
| `scitex-todo list-tasks [--tasks PATH] [--json]` | List resolved tasks (id / status / title). |
| `scitex-todo board [--port N] [--tasks PATH] [--no-browser]` | Launch the read-only web board (needs the `[web]` extra). |
| `scitex-todo list-python-apis [-v/-vv/-vvv] [--json]` | Introspect the public Python API. |
| `scitex-todo mcp list-tools [--json]` | List MCP tools (none yet — surface on the roadmap). |
| `scitex-todo skills {list,get,install}` | List / print / install the bundled agent skills. |
| `scitex-todo install-shell-completion [--shell bash\|zsh\|fish]` | Install tab-completion (cache-file pattern). |
| `scitex-todo print-shell-completion [--shell ...]` | Print the completion script for eval/sourcing. |

## Universal flags

- `-h`, `--help` — usage with an example (every command).
- `--help-recursive` — flatten help for all subcommands (top level).
- `--json` — machine-readable output on every data-reading command.
- `-V`, `--version` — print `scitex-todo/X.Y.Z`.

## Store resolution

Every command resolves the task store the same way: `--tasks` →
`$SCITEX_TODO_TASKS` → `<git-root>/.scitex/todo/tasks.yaml` →
`~/.scitex/todo/tasks.yaml` → bundled example. See
[20_env-vars.md](20_env-vars.md).

See `general/03_interface_02_cli/` for the ecosystem-wide CLI grammar.
