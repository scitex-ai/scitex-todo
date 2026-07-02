---
description: |
  [TOPIC] Fleet-wide scitex-todo MCP rollout — the canonical `.mcp.json`
  block and the binding "MCP-only for durable todos" mandate.
  [WHEN] Read on every agent boot. This is the leaf that locks in WHY
  scitex-todo is the single durable-todo wire AND HOW to wire it from a
  Claude-Code container — one copy-pasteable JSON block, one env var,
  one smoke test. Agent-container drops this `.mcp.json` into every
  agent's `to_home/.mcp.json` per the P3a wave (lead-dispatched).
  [HOW] Copy the JSON below into your `~/.mcp.json` (or the
  agent-container `to_home/_base/.mcp.json` for fleet rollout). Set
  `SCITEX_TODO_AGENT_ID=<your-peer-name>` in the agent's env. Verify with
  `scitex-todo mcp doctor`.
tags:
  [
    scitex-todo-fleet-rollout,
    scitex-todo-mcp-canonical,
    scitex-todo-p3a,
  ]
---

# Fleet MCP rollout — the canonical wire

scitex-todo is **THE fleet's single source of truth** for durable /
cross-session / cross-agent todos (operator standing directive +
lead-confirmed mandate in [SKILL.md](./SKILL.md#-mandate--single-source-of-truth-operator--lead-2026-06-12)).
This leaf is the **canonical fleet-rollout artifact**: one
copy-pasteable `.mcp.json` block, one mandatory env var, one smoke
test. Drop this into every agent's `to_home/.mcp.json` and the MCP
wire is live.

## ⚑ Mandate (binding)

1. **All durable todos go through the scitex-todo MCP.** Every
   `proj-*` agent, the lead, and the operator write to the same
   `~/.scitex/todo/tasks.yaml` via the wire below. There is **one**
   shared store; there are **no** parallel formats.

2. **No private todo files.** Do not create
   `GITIGNORED/FUTURE/*.md`, `GITIGNORED/TODO.md`,
   `GITIGNORED/RUNNING/*.md`, or any other markdown / YAML /
   JSON / SQLite stand-in for the durable backlog. The harness
   `TaskList` is in-session SCRATCH ONLY — anything that should
   survive the turn goes in scitex-todo.

3. **CLI is an equivalent fallback, not a parallel path.** Inside a
   Claude-Code agent container the MCP wire is preferred. The
   `scitex-todo` CLI executes the same Python API and writes the
   same store; pick whichever is more ergonomic, but never invent a
   third wire.

If you have legacy notes outside scitex-todo, migrate the ACTIONABLE
rows on the next turn that touches them (see [11_adopting-from-a-project.md](./11_adopting-from-a-project.md)).

## The canonical `.mcp.json` block

Paste this verbatim into your agent's `~/.mcp.json` (or, for the fleet
rollout, into `agent-container/to_home/_base/.mcp.json`):

```json
{
  "mcpServers": {
    "scitex-todo": {
      "command": "scitex-todo",
      "args": ["mcp", "start"]
    }
  }
}
```

That's the entire wire. The `scitex-todo` CLI ships in the
`scitex-todo[mcp]` extra (see [01_installation.md](./01_installation.md));
`mcp start` launches the FastMCP stdio server the Claude-Code harness
talks to.

If you already have other MCP servers in `~/.mcp.json`, merge the
`scitex-todo` key under the existing `mcpServers` map — do **not**
overwrite the file.

## Required + recommended env

Every write tags the agent via env vars. **Set `SCITEX_TODO_AGENT_ID`
before the MCP server starts** — a missing tag is a config bug, not
a soft default.

| Var | Required? | Value | Effect |
|---|---|---|---|
| `SCITEX_TODO_AGENT_ID` | **YES** | `proj-<your-peer-name>` | Stamps every write's `_log_meta.created_by` / `updated_by`. The board's "by agent" lens, throughput stats, and notify routing all key off this. |
| `SCITEX_TODO_SCOPE` | recommended | `agent:proj-<your-peer-name>` | Default scope for `list_tasks` / `summarize_tasks` so the agent sees its own slice by default. Pass `scope=""` to opt out per-call. |
| `SCITEX_TODO_TASKS_YAML_SHARED` | only if non-default | Absolute path to `tasks.yaml` | Pins the store. Default resolution chain (explicit → env → project → user → bundled) usually picks the right one without this. |

For agent-container's `to_home/_base/.mcp.json` rollout, the per-agent
`SCITEX_TODO_AGENT_ID` value is templated from the agent's name; see the
P3a wiring on the agent-container side.

## Tool surface — 15 today (CLI parity reached via PR #144)

| Tool | Python API | Purpose |
|---|---|---|
| `add_task` | `scitex_todo.add_task` | Append a new task. |
| `update_task` | `scitex_todo.update_task` | Mutate fields of an existing task. |
| `complete_task` | `scitex_todo.complete_task` | Mark done + stamp `_log_meta.completed_*`. |
| `list_tasks` | `scitex_todo.list_tasks` | Filter by scope / assignee / status / etc. |
| `summarize_tasks` | `scitex_todo.summarize_tasks` | Counts by status / scope / assignee. |
| `resolve_store` | `scitex_todo.resolve_store` | Resolved path + the precedence chain. |
| `get_task` | `scitex_todo.get_task` | Return one task by id. |
| `delete_task` | `scitex_todo.delete_task` | Soft-delete (restorable). |
| `restore_task` | `scitex_todo.restore_task` | Undo `delete_task`. |
| `comment_task` | `scitex_todo.comment_task` | Append a comment to `comments[]`. |
| `set_edge` | `scitex_todo.set_edge` | Add / remove a `depends_on` / `blocks` edge. |
| `resolve_task` | `scitex_todo.resolve_task` | Mark a blocker resolved. |
| `reopen_task` | `scitex_todo.reopen_task` | Reopen a `done` / `failed` task. |
| `todo_skills_list` | (skills introspection) | List bundled skills. |
| `todo_skills_get` | (skills introspection) | Get one bundled skill by name. |
| `add_comment` *(deferred)* | `scitex_todo.add_comment` | Convention-A alias for `comment_task`. PR #64 not yet merged; CLI parity was reached via PR #144 (`scitex-todo comment`) which wraps the existing `comment_task` MCP tool — agents needing the activity-append do NOT have to wait. |

Discover the live set at runtime:

```bash
scitex-todo mcp list-tools --json | jq '.[].name'
```

## Install — one command

```bash
# Recommended (uv resolver, fast):
uv pip install -U 'scitex-todo[mcp]>=0.7.1'

# Plain pip equivalent:
pip install -U 'scitex-todo[mcp]>=0.7.1'
```

Version pin: **≥ 0.7.1** is the PR #115 floor — that's the release that
ships the 10-minute structural-nudge cron + `--nudge-quiet` flag the
fleet feedback loop depends on. Earlier `0.5.x` works but lacks the
nudge wire.

## Smoke test — 30 seconds end-to-end

```bash
# 1. The CLI resolves + the FastMCP server self-diagnoses.
scitex-todo --version
scitex-todo mcp doctor
# expected: status: ok / fastmcp: 3.x / tools: 15
# (PR #64 would add `add_comment` as #16; deferred — CLI parity reached via PR #144)

# 2. The MCP wire reaches the live store.
SCITEX_TODO_AGENT_ID=proj-<you> scitex-todo list-tasks --by-agent --json | head

# 3. The agent can write.
SCITEX_TODO_AGENT_ID=proj-<you> scitex-todo add \
    proj-<you>-mcp-smoke-$(date +%s) \
    '[P2] smoke: confirm MCP wire' \
    --scope agent:proj-<you> \
    --assignee proj-<you> \
    --agent proj-<you>

# 4. Tear it down.
SCITEX_TODO_AGENT_ID=proj-<you> scitex-todo done proj-<you>-mcp-smoke-<stamp>
```

Pass all four = wire is live. Any failure: post the exact command +
full stderr to your lead via a2a, then stop. Do NOT retry-loop.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `mcp doctor` → "fastmcp: MISSING" | `[mcp]` extra not installed | `pip install -U 'scitex-todo[mcp]>=0.7.1'` |
| Writes land but `_log_meta.created_by` is unset / wrong | `SCITEX_TODO_AGENT_ID` missing or stale in the agent's env | Set it BEFORE the MCP server boots; restart the harness. |
| `mcp doctor` → "tools: 0" | FastMCP version skew | Bump fastmcp to ≥ 3.0; rebuild the venv. |
| `list_tasks` returns the whole store, not your slice | `SCITEX_TODO_SCOPE` unset | Export `SCITEX_TODO_SCOPE=agent:proj-<you>`. |
| Your `SCITEX_TODO_TASKS_YAML_SHARED` points at a stale file | precedence chain picked an earlier tier | `scitex-todo resolve-store` prints the resolved path + the chain. |

## Cross-references

- [SKILL.md](./SKILL.md) — the entry leaf + the operator-mandate context.
- [05_mcp-tools.md](./05_mcp-tools.md) — full tool schema reference.
- [20_env-vars.md](./20_env-vars.md) — store-resolution chain detail.
- [42_for-consuming-agents.md](./42_for-consuming-agents.md) — full
  onboarding for a fresh consuming agent (CLI emphasis).
- `general/03_interface_03_mcp/` — ecosystem-wide MCP tool grammar.
