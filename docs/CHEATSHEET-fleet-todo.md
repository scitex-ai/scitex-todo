# scitex-todo — fleet cheatsheet

**Audience.** Every agent, every host, every human in the SciTeX fleet.

**One-line summary.** `scitex-todo` is the fleet's shared task store. The
canonical data is a YAML file at `~/.scitex/todo/tasks.yaml`. You read and
write it from CLI, from Python (`import scitex_todo`), from MCP tools, or
from the web board at `http://127.0.0.1:8051/`. Filter your view to your
own slice via the `scope` and `assignee` fields.

> **Status note (2026-06-02).** The write surface described below
> (`add` / `update` / `done` / `summary` / MCP tools / `_store.py` Python
> API + `scope` / `assignee` schema fields) lives on Phase-1 PR #14, which
> is OPEN and pending rebase onto develop. Treat sections marked
> 🟡 PHASE-1 as **available the moment PR #14 merges**. Sections marked
> ✅ LIVE work on `develop` today. The web board and YAML store have been
> live for weeks — what Phase 1 adds is the agent-facing *write* surface.

---

## 1 — One-time setup per agent / host

```bash
# 1.1 Install
pip install 'scitex-todo[mcp]'   # [mcp] extra is needed for the MCP server

# 1.2 Where do your tasks live? (read-only — won't create files)
scitex-todo where                # ✅ LIVE — prints resolved path + precedence chain

# 1.3 Tell the package who you are
#     (these envs make all read verbs default to YOUR slice)
export SCITEX_TODO_SCOPE='agent:<your-name>'    # 🟡 PHASE-1
export SCITEX_TODO_AGENT='agent:<your-name>'    # 🟡 PHASE-1 — used to stamp `completed_by`

# 1.4 First-time create of the shared store on a fresh host (idempotent)
scitex-todo init --shared        # 🟡 PHASE-1
```

**Convention for `<your-name>`.** Pick the literal sac peer name (e.g.
`agent:proj-scitex-todo`, `agent:lead`, `agent:hub-ops`). Humans use
`user:operator`, `user:ywatanabe`, etc.

**Scope label conventions** (free-form strings — not enums):

| Prefix          | Use for                                          | Example                          |
| --------------- | ------------------------------------------------ | -------------------------------- |
| `agent:<name>`  | The sac peer or single-agent identity            | `agent:proj-scitex-todo`         |
| `project:<name>`| A project / repo team                            | `project:scitex-clew`            |
| `host:<name>`   | **A specific host (cross-host axis — §6)**       | `host:wsl2-dev`, `host:mba-arm64`|
| `user:<name>`   | A human                                          | `user:operator`                  |
| `private`       | Operator-only memos                              | `private`                        |

`host:<hostname>` is **first-class** for the cross-host 串刺し view —
see §6 (storage axes) and §7.6 (cross-host workflow). Tag a task with
`scope: host:$(hostname)` when it's locally rooted (e.g. local env
setup, host-specific config) so cross-host filters keep it in its lane.

---

## 2 — The CLI (start here)

### 2.1 Read

```bash
scitex-todo list                                  # all tasks (filtered by $SCITEX_TODO_SCOPE if set)
scitex-todo list --scope ""                       # opt out of env-default filter (see EVERYTHING)
scitex-todo list --assignee agent:lead            # tasks owned by the lead
scitex-todo list --status in_progress             # what's actively being worked
scitex-todo list --status pending --json          # JSON for scripting
scitex-todo summary --scope project:scitex-clew   # counts by status for one project
```

### 2.2 Write

```bash
# Create a task you intend to do yourself
scitex-todo add e1-acl-cli "sac ACL fleet-group + grant CLI" \
    --scope project:sac --assignee agent:proj-scitex-agent-container \
    --priority 3 --note "see lead's E1 brief"

# Claim a task from someone else's queue
scitex-todo update e1-acl-cli --assignee agent:<me> --status in_progress

# Mark it done (stamps completed_at + completed_by automatically)
scitex-todo done e1-acl-cli

# Mark done on someone's behalf (override the env default)
scitex-todo done e1-acl-cli --by 'user:operator'

# Clear a field (empty string)
scitex-todo update e1-acl-cli --scope ''
```

All write verbs above are 🟡 PHASE-1 (PR #14).

### 2.3 Cross-host sync (Phase-2 — designed, not yet built)

```bash
scitex-todo sync --dry-run                       # 🟡 PHASE-1 STUB (no-op, prints plan)
scitex-todo sync --apply --remote origin         # 🟠 PHASE-2 — git pull/push (TBD)
```

### 2.4 Visualize

```bash
scitex-todo board                                 # ✅ LIVE — opens http://127.0.0.1:8051
scitex-todo render-graph --format png            # ✅ LIVE — static dependency graph
```

The board has drill-down (click a parent card), drag-reorder (changes
`priority`), drag-connect (creates `depends_on` edges), markdown drawer
(click a leaf), table view, repo filter, search, undo. All ✅ LIVE.

---

## 3 — The Python API (for agent code)

```python
import scitex_todo as todo

# ── read (snapshot, no lock) ────────────────────────────────────
mine = todo.list_tasks(scope="agent:proj-scitex-todo",        # 🟡 PHASE-1
                       status="pending")
counts = todo.summary(scope="project:sac")                    # 🟡 PHASE-1

# ── write (locked via fcntl.flock around full RMW) ───────────────
todo.add_task(id="my-task", title="Implement my-task",         # 🟡 PHASE-1
              scope="agent:proj-scitex-todo",
              assignee="agent:proj-scitex-todo",
              status="pending", priority=5)
todo.update_task(task_id="my-task", status="in_progress")      # 🟡 PHASE-1
todo.complete_task(task_id="my-task")                          # 🟡 PHASE-1
                                                               #   ↑ stamps _log_meta.completed_at + completed_by

# ── load + raw read (always ✅ LIVE) ────────────────────────────
tasks = todo.load_tasks(todo.resolve_tasks_path())
```

**Concurrency.** Every mutator in `_store.py` acquires
`fcntl.flock("<store>.lock")` around the entire read-modify-write so two
concurrent writers (CLI + board POST + sac peer's MCP call) can't
interleave. There is no "atomic compare-and-set" — last writer wins per
field; the design relies on the lock for serialization and on
`_log_meta.completed_at` for cross-host conflict resolution (Phase 2).

---

## 4 — The MCP tool surface (for sac agents and Claude harnesses)

Six tools, all under the `<pkg>_<verb>_<noun>` convention:

| Tool                          | Purpose                                      |
| ----------------------------- | -------------------------------------------- |
| `scitex_todo_add_task`        | Append a new task. Returns the inserted dict as JSON. |
| `scitex_todo_update_task`     | Mutate fields of an existing task. Returns merged dict as JSON. |
| `scitex_todo_complete_task`   | `status=done` + stamp `_log_meta`. Idempotent. |
| `scitex_todo_list_tasks`      | Filter by scope/assignee/status. Returns list as JSON. |
| `scitex_todo_summary`         | Counts by status/scope/assignee. Returns dict as JSON. |
| `scitex_todo_where`           | Resolved store path + precedence chain.      |

All 🟡 PHASE-1 (PR #14). Start the server:

```bash
scitex-todo mcp start            # 🟡 PHASE-1 — FastMCP stdio server
scitex-todo mcp doctor           # 🟡 PHASE-1 — env + dep diagnostic
scitex-todo mcp list-tools       # 🟡 PHASE-1
scitex-todo mcp install          # 🟡 PHASE-1 — wire into local MCP config
```

**Install hint.** If `import scitex_todo._mcp_server` raises ImportError,
you didn't install the `[mcp]` extra. `pip install 'scitex-todo[mcp]'`.

---

## 5 — The HTTP surface (for the web board and remote consumers)

The board's Django app exposes:

| Endpoint                  | Method | Purpose                              | Status |
| ------------------------- | ------ | ------------------------------------ | ------ |
| `/`                       | GET    | The standalone shell (React Flow)    | ✅ LIVE |
| `/graph`                  | GET    | The task graph as JSON               | ✅ LIVE |
| `/priority`               | POST   | Reorder (`{"order": [id, ...]}`) → rewrites YAML | ✅ LIVE |
| `/edges`                  | POST   | Create/delete `depends_on` edges     | ✅ LIVE |
| `/tasks/<id>`             | PATCH  | Field-level update from the drawer   | ✅ LIVE |
| `/messages` *(future)*    | POST   | Operator↔agent chat                  | 🟠 PHASE-3 |

For now, remote consumers (Orochi, scitex-hub) can read `/graph` directly
to render their own task views. Mutating verbs all round-trip through
`_model.save_tasks`, which holds the same `fcntl.flock` mutex as the CLI
and the MCP tools — every adapter is the same writer.

---

## 6 — The store: where the data lives (two axes of cross-cutting)

The operator's mental model — **串刺し** (*kushizashi*, "skewered"
through projects AND hosts) — has two independent axes. Both are
first-class; the precedence chain handles axis 1, the git-backed sync
substrate handles axis 2.

### Axis 1 — across projects (precedence chain on one host)

```
Resolution chain (highest → lowest, first match wins):
  1. $SCITEX_TODO_TASKS (env-var override)
  2. <git-root>/.scitex/todo/tasks.yaml      (project-scope; gitignored — project OVERRIDES user)
  3. $SCITEX_DIR/todo/tasks.yaml             (user-scope; default ~/.scitex/todo — CROSS-PROJECT default)
  4. <package>/examples/tasks.yaml           (bundled demo; fresh-install fallback)
```

The **user-scope path** (`~/.scitex/todo/tasks.yaml`) is the operator's
**cross-PROJECT** store — one place where tasks from every project on
this host live together. Per the SciTeX local-state-directories
convention, a per-project store at `<project>/.scitex/todo/tasks.yaml`
overrides the user-level one when you're inside that project tree. Use
the project-scope for project-internal task lists you don't want in the
cross-cut view; add the dir to the project's `.gitignore`.

### Axis 2 — across hosts (git-backed sync substrate)

`~/.scitex/todo/` is per-host (it lives on each machine's local
filesystem). To make the user-scope store fleet-shared, it is itself a
**git checkout of a private state repo** (default name
`ywatanabe1989-private/scitex-todo-state`); cross-host sync is
`scitex-todo sync --apply` ≈ `git pull --rebase --autostash && git push`
(🟠 PHASE-2 body; the Phase-1 stub already exists).

```
host: wsl2-dev                                 host: mba-arm64
~/.scitex/todo/                                ~/.scitex/todo/
  ├─ .git/  ─────── push/pull ──────────────────► .git/
  └─ tasks.yaml                                   └─ tasks.yaml
                       │
                       ▼  (the private state repo)
              github.com/ywatanabe1989-private/scitex-todo-state
```

After a `sync --apply` on each host, both hosts see one canonical view.
That view is the 串刺し view: read-only-equivalent across the fleet.

### To see what your process is actually pointing at

```bash
scitex-todo where --json         # 🟡 PHASE-1 — prints resolved path + chain
```

### Conflict resolution (Phase 2, designed)

Per-task LWW on `_log_meta.completed_at` (or commit author-date for
non-completion edits). New ids on either side: keep both. The substrate
guarantees forward-progress; the operator can always hand-edit on the
board if a merge picked badly.

---

## 7 — Common workflows

### 7.1 "I'm a sac agent waking up after migration"

```bash
# Container has injected SCITEX_TODO_SCOPE=agent:<me>, SCITEX_TODO_AGENT=agent:<me>
scitex-todo list --status in_progress      # what was I doing?
# pick the top one, read its `note` field for handoff context, resume
```

### 7.2 "I'm an agent claiming a task off the queue"

```bash
scitex-todo list --assignee '' --status pending  # unowned pending work
scitex-todo update <id> --assignee agent:<me> --status in_progress
```

### 7.3 "I'm the operator and I want to leave myself a memo"

```bash
scitex-todo add memo-buy-milk "Pick up milk" --scope private
```

### 7.4 "I'm the lead and I want to broadcast an epic for someone to claim"

```bash
scitex-todo add e1-acl "sac ACL fleet-group + grant CLI" \
    --scope project:sac \
    --assignee agent:proj-scitex-agent-container \
    --priority 2 --status pending \
    --note "Brief: ..."
```

### 7.5 "I want to see one team's progress at a glance"

```bash
scitex-todo summary --scope project:sac
# → totals + by_status + by_scope + by_assignee, JSON-able with --json
```

### 7.6 "Show me the 串刺し view — every project, every host" (cross-host workflow)

The user-scope store is the cross-PROJECT axis; the git-backed sync is
the cross-HOST axis. With both, "every task on every host" is just
`list` with no filter:

```bash
# On any host, after a sync:
scitex-todo sync --apply             # 🟠 PHASE-2 — pull/push the state repo
scitex-todo list --scope ''          # the full 串刺し view (no scope filter)
scitex-todo summary --scope ''       # numeric digest of the same
```

Slice the 串刺し view by host with the `host:<hostname>` scope label
(first-class convention — see §1.3 setup):

```bash
# Tag a task as host-local when I create it
scitex-todo add wsl-ssh-key "regenerate ssh key" --scope "host:$(hostname)"

# What's on this host?
scitex-todo list --scope "host:$(hostname)"

# What's on the MBA?
scitex-todo list --scope "host:mba-arm64"

# Everything everywhere (the operator's cross-cut dashboard)
scitex-todo list --scope ''
```

The web board (`scitex-todo board`) renders the same 串刺し view —
it reads whatever `where` resolves to, so on a host with the
fleet-shared user-scope store, the board IS the fleet board.

### 7.7 "Override the fleet store with a project-local task list"

When you want a project to keep its own task list that doesn't pollute
the cross-project 串刺し view:

```bash
cd ~/proj/scitex-foo
scitex-todo init --project           # 🟡 PHASE-1 — creates ./.scitex/todo/tasks.yaml
echo ".scitex/" >> .gitignore        # don't commit the local task list
scitex-todo list                     # now reads the PROJECT store (overrides user)
scitex-todo where                    # confirms which store the verbs hit
```

Removing the project-scope file (or `cd`-ing outside the project tree)
reverts to the user-scope cross-project view automatically.

---

## 8 — Schema reference

A single task is a YAML mapping. Required: `id`, `title`, `status`. Everything
else is optional and additive (you can always add new fields; old YAML keeps
loading).

```yaml
- id: <unique-string>                        # REQUIRED
  title: <human-readable>                    # REQUIRED
  status: pending|in_progress|blocked|done|deferred|failed|goal  # REQUIRED
  scope: <free-form-string>                  # 🟡 PHASE-1
  assignee: <free-form-string>               # 🟡 PHASE-1
  priority: <integer>                        # ✅ LIVE — lower = earlier
  parent: <task-id>                          # ✅ LIVE — nested graph drill-down
  depends_on: [<task-id>, ...]               # ✅ LIVE
  blocks: [<task-id>, ...]                   # ✅ LIVE
  repo: <free-form-string>                   # ✅ LIVE
  note: |                                    # ✅ LIVE — markdown, drawer-rendered
    <markdown>
  _log_meta:                                 # 🟡 PHASE-1 — opaque event-stamp bag
    completed_at: <ISO-8601 UTC>
    completed_by: <free-form-string>
```

Statuses (`VALID_STATUSES`): `goal`, `pending`, `in_progress`, `blocked`,
`done`, `deferred`, `failed`. The board colors these consistently.

---

## 9 — Troubleshooting

| Symptom                                   | Fix                                                |
| ----------------------------------------- | -------------------------------------------------- |
| `scitex-todo` not found                   | `pip install 'scitex-todo[mcp]'`                   |
| `import scitex_todo._mcp_server` fails    | You didn't install the `[mcp]` extra              |
| `list` returns nothing                    | `$SCITEX_TODO_SCOPE` is filtering you out; try `--scope ''` |
| Concurrent writers seem to lose data      | `fcntl.flock` should serialize them; check that all writers go through `_store.py` / `save_tasks` (NOT raw YAML writes) |
| `add` fails with `TaskValidationError`    | Duplicate `id`, invalid `status`, or `priority` not an int |
| Board doesn't reflect a CLI change        | The board polls `/graph`; refresh the page or wait a few seconds (live auto-refresh is on — PR #24) |

---

## 10 — Where to file bugs / requests

This package: `https://github.com/ywatanabe1989/scitex-todo`. The
`proj-scitex-todo` agent owns it. The lead and operator triage feature
requests on the operator-channel; agents file via sac peer-message to
`proj-scitex-todo`.

<!-- EOF -->
