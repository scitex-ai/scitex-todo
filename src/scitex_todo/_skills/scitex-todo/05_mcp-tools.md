---
description: |
  [TOPIC] MCP Tools Reference
  [DETAILS] scitex-todo's FastMCP tool surface — Convention A (tool_name ==
  Python API name). Each tool is a thin async wrapper around the matching
  `scitex_todo.<api>` function and returns a JSON string of the same shape
  the Python API returns.
tags: [scitex-todo-mcp-tools]
---

# MCP Tools

```bash
scitex-todo mcp list-tools -vv     # enumerate registered tools
scitex-todo mcp doctor             # self-diagnose the install
scitex-todo mcp install            # print Claude Code config snippet
scitex-todo mcp start              # launch the FastMCP server (stdio)
```

## Tool surface (Convention A)

Tool name matches the Python API name 1:1 (no `scitex_todo_` prefix). Every
tool returns `json.dumps(...)` of the dict / list the matching Python API
returns.

| Tool | Python API | Purpose |
|---|---|---|
| `add_task` | `scitex_todo.add_task` | Append a new task to the store. |
| `update_task` | `scitex_todo.update_task` | Mutate fields of an existing task. |
| `complete_task` | `scitex_todo.complete_task` | Mark done + stamp `_log_meta.completed_{at,by}`. |
| `list_tasks` | `scitex_todo.list_tasks` | Filter the store by scope / assignee / status. |
| `summarize_tasks` | `scitex_todo.summarize_tasks` | Counts by status / scope / assignee. |
| `resolve_store` | `scitex_todo.resolve_store` | Resolved store path + the precedence chain. |
| `todo_skills_list` | (skills introspection) | List bundled agent skills (file names). |
| `todo_skills_get` | (skills introspection) | Get the content of one bundled skill by name. |

## Store resolution (every tool)

`tasks_path` argument → `$SCITEX_TODO_TASKS_YAML_SHARED` → `<git-root>/.scitex/todo/tasks.yaml`
→ `~/.scitex/todo/tasks.yaml` → bundled example. See
[20_env-vars.md](20_env-vars.md).

## Scope filtering

`list_tasks` / `summarize_tasks` honor `$SCITEX_TODO_SCOPE` as the default
`scope` value. Pass `scope=""` (empty string) to opt out of that env
default and see every task. The agent-facing convention these tools
respect is documented in [02_quick-start.md](02_quick-start.md).

## Discovering tool names at runtime

```bash
scitex-todo mcp list-tools --json | jq '.[].name'
```

See `general/03_interface_03_mcp/` for the ecosystem-wide MCP tool grammar.
