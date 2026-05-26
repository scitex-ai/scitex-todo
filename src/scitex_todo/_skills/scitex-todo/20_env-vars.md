---
description: |
  [TOPIC] Environment Variables & Local State
  [DETAILS] SCITEX_TODO_TASKS pins the task store; SCITEX_DIR relocates the
  user-scope ~/.scitex root. Both are optional — the resolution chain falls
  back to the bundled example.
tags: [scitex-todo-env-vars]
---

# Environment Variables & Local State

| Name                | Default                      | Purpose                                              |
|---------------------|------------------------------|------------------------------------------------------|
| `SCITEX_TODO_TASKS` | (unset)                      | Absolute path to the task store; wins over the project/user/bundled fallback (a `--tasks` flag still overrides it). |
| `SCITEX_DIR`        | `~/.scitex`                  | Relocates the user-scope state root, so the user store becomes `$SCITEX_DIR/todo/tasks.yaml`. |

Copy [`.env.example`](../../../../.env.example) to `.env` at your project root
to set these; CLI flags always override env vars.

## Store resolution order (first existing wins)

1. explicit `--tasks` path
2. `$SCITEX_TODO_TASKS`
3. project scope: `<git-root>/.scitex/todo/tasks.yaml`
4. user scope: `~/.scitex/todo/tasks.yaml` (relocatable via `$SCITEX_DIR`)
5. the bundled generic example (`scitex_todo/examples/tasks.yaml`)

## Local state directories

| Path                                  | Scope         | Purpose                  |
|---------------------------------------|---------------|--------------------------|
| `~/.scitex/todo/tasks.yaml`           | user-global   | your personal task store |
| `<proj-root>/.scitex/todo/tasks.yaml` | project-local | per-repo task store      |

See `general/01_ecosystem_04_environment-variables.md` and
`general/01_ecosystem_06_local-state-directories.md`.
