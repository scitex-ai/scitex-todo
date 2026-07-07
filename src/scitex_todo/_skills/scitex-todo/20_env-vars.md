---
description: |
  [TOPIC] Environment Variables & Local State
  [DETAILS] SCITEX_TODO_TASKS_YAML_SHARED pins the task store; SCITEX_DIR relocates the
  user-scope ~/.scitex root. Both are optional — the resolution chain falls
  back to the bundled example.
tags: [scitex-todo-env-vars]
---

# Environment Variables & Local State

| Name                | Default                      | Purpose                                              |
|---------------------|------------------------------|------------------------------------------------------|
| `SCITEX_TODO_TASKS_YAML_SHARED` | (unset)                      | Absolute path to the task store; wins over the project/user/bundled fallback (a `--tasks` flag still overrides it). |
| `SCITEX_TODO_AGENT_ID` | (unset)                   | This agent's identity — stamps every write's `created_by`/`updated_by`, keys the channel inbox, and is the `--mine` filter. Fail-loud when unresolved. (Renamed 2026-07-02 from the now-rejected `SCITEX_TODO_AGENT`.) **Headless lever:** leave it UNSET and `scitex-todo mcp start` runs TOOLS-ONLY — the inbox poll loop is not started and the session receives ZERO channel pushes. This is the intended mode for solver / headless capsules that must not receive unsolicited pushes. |
| `SCITEX_TODO_CHANNEL_SOURCE` | `stodo` | `mcp channel` `meta.source` (drives the `<- stodo` render — the fleet's short sender-identity label, deliberately distinct from the `scitex-todo` agent id). Overridden by `--name`. |
| `SCITEX_TODO_CHANNEL_INTERVAL` | `5.0`             | `mcp channel` poll interval (seconds) between inbox drains. Overridden by `--interval`. |
| `SCITEX_DIR`        | `~/.scitex`                  | Relocates the user-scope state root, so the user store becomes `$SCITEX_DIR/todo/tasks.yaml`. |

Copy [`.env.example`](../../../../.env.example) to `.env` at your project root
to set these; CLI flags always override env vars.

## Store resolution order (first existing wins)

1. explicit `--tasks` path
2. `$SCITEX_TODO_TASKS_YAML_SHARED`
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
