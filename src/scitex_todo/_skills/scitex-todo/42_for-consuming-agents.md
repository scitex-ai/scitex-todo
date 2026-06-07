---
description: |
  [TOPIC] For consuming agents — quick-onboard "how do I use .scitex/todo
  as MY task SSoT?"
  [DETAILS] One-page protocol for any fleet agent: where to put YOUR tasks,
  which CLI / MCP / Python entry point to use for create / list / update /
  comment / complete, the closed-enum (fail-loud) schema, the title-prefix
  convention, and the lead↔worker coordination wire. Read this first if
  you've just been told "use scitex-todo for your todos."
tags:
  [
    scitex-todo-consuming-agent,
    scitex-todo-onboarding,
    scitex-todo-fleet-protocol,
  ]
---

# For consuming agents — adopt `.scitex/todo/` as YOUR task SSoT

You are a fleet agent (a `proj-<something>` SAC peer). The operator has
made `~/.scitex/todo/` the **single canonical home** for fleet task
state — yours, the lead's, every other agent's. This skill tells you
exactly what to do.

Three rules, in priority order:

1. **No memory.** Every task you accept lives in `.scitex/todo/` as a
   structured row from the moment you accept it. Never carry a
   commitment "in your head."
2. **Fail loud, fail fast.** `scitex-todo` validates the schema on
   every read + every write. If you set a status / kind / blocker
   value that isn't in the closed enum, the write RAISES. Don't
   catch-and-ignore — fix the input.
3. **Write through the API.** Use the CLI, the MCP tool, or the
   Python API. **Never edit `tasks.yaml` by hand** (operator standing
   directive, TG 9494). Direct edits bypass the validator + the
   ruamel comment-preserving writer; the next legitimate write may
   roll back your change OR refuse to load the store.

---

## Where YOUR tasks go — two-tier scope

`scitex-todo` resolves the store in this precedence order (first
existing wins):

| Precedence | Path                                              | Use for                                                                    |
| ---------- | ------------------------------------------------- | -------------------------------------------------------------------------- |
| 1          | `$SCITEX_TODO_TASKS` (explicit env)               | Container glue (the spec.yaml sets this for the agent's chosen scope).     |
| 2          | `<git-root>/.scitex/todo/tasks.yaml` (PROJECT)    | Tasks scoped to ONE project (this repo). Default for an in-repo agent.     |
| 3          | `~/.scitex/todo/tasks.yaml` (USER, fleet-shared)  | Fleet-wide aggregate; cross-project work the operator + lead coordinate.   |
| 4          | bundled `examples/tasks.yaml`                     | Read-only fallback; never written.                                         |

**Which tier do I write to?**

- **Default:** your project tier — `<your-project>/.scitex/todo/tasks.yaml`.
  This is "your own lane" — only YOU write here. The fleet aggregator
  rolls it into the global tier within 5s (no manual sync).
- **Cross-project tasks** (e.g. "block on neurovista's PR landing"):
  still write to YOUR project tier, with `depends_on: ["neurovista/<their-id>"]`.
  The dep edge crosses tiers; the aggregator resolves it.
- **Fleet-coordination tasks** (e.g. release-cutover, ADR-0007 follow-up):
  the LEAD writes those to the global tier. You don't.

If you're running inside an agent container, your spec.yaml has
exported `$SCITEX_TODO_TASKS` for you — `scitex-todo` resolves to
the right tier automatically. Confirm with:

```bash
scitex-todo resolve-store
# → prints the resolved path + the precedence chain
```

See [30_two-tier-conventions-and-write-protocol.md](30_two-tier-conventions-and-write-protocol.md)
for the full write-protocol contract (who writes when, conflict rules,
ACL).

---

## Schema — closed enums, fail-loud

Every task is a `Task` dataclass (defined in `scitex_todo._model.Task`).
The validator REJECTS unknown values in the closed enums below.

**Required fields:**

- `id` (str, globally unique, kebab-case, **prefix with your project**:
  e.g. `proj-scitex-todo-fleet-rollout`, `clew-cohort-a-rerun`).
- `title` (str, short scannable label, ≤ 80 chars).
- `status` (closed enum below).

**Closed enums (fail-loud):**

| Field     | Allowed values                                                                   |
| --------- | -------------------------------------------------------------------------------- |
| `status`  | `goal` · `pending` · `in_progress` · `blocked` · `done` · `deferred` · `failed`  |
| `kind`    | `task` (default if absent) · `compute` · `decision`                              |
| `blocker` | `compute` · `dependency` (alias `dep`) · `operator-decision` · `agent-wait` · `none` |

`blocker` is **only allowed when** `status == "blocked"`. Setting a
blocker on a non-blocked row raises.

**Recommended fields (operator-co-designed surface, TG 9667):**

- `task` (str) — the 1-line CURRENT-task BIG text on the board card.
  Distinct from `title` (the short scannable label). Populate this for
  the card to read well.
- `project` (str) — your project's directory basename (e.g.
  `scitex-todo`). Matches the canonical id prefix.
- `host` (str) — where the work happens (`spartan` / `ywata-note-win`
  / etc.).
- `agent` (str) — owning agent — YOU (e.g. `proj-scitex-todo`).
  Operator-co-designed replacement for the legacy `assignee`.
- `goal` (str) — WHY (parent-goal text); rendered as the 🎯 line on the
  card. One short sentence.
- `priority` (int) — lower = higher priority. Within your project
  tier, set a tight 1..N rank; the operator can re-rank globally on
  the board.
- `last_activity` (ISO-8601 UTC) — recency drives green/amber/red
  card coloring on the board.
- `depends_on: list[str]` / `blocks: list[str]` / `parent: str` —
  graph edges. Use the canonical `<project>/<local-id>` form for
  cross-project deps.
- `pr_url` / `issue_url` — GH/Gitea links.
- `comments: list[{ts, author, text}]` — append-only activity log
  (see "Coordinating with other agents" below).

**Title-prefix convention** (the operator's at-a-glance scan):

| Prefix       | Meaning                                                          |
| ------------ | ---------------------------------------------------------------- |
| `[P0]`       | Highest business priority / live blocker                         |
| `[P1]`       | Momentum (paper, infra in-flight)                                |
| `[P2]`       | Parallel queue / hygiene                                         |
| `[CI]`       | CI hygiene                                                       |
| `[CAL]`      | Calendar / commitment                                            |
| `[GOAL]`     | `status: goal` umbrella (north-star objective)                   |
| `[PKG]`      | Per-package umbrella in the 66-pkg ecosystem-quality tree        |
| `[strategy]` | Secondary tag (catalogs / GTM)                                   |

So a typical title looks like `[P1] (PR #334 follow-up) verify bun
child survives restart fleetwide`.

---

## CRUD — the verbs you'll actually use

All paths below resolve to YOUR project tier by default (see
"Where YOUR tasks go" above). Examples are CLI; MCP tool names match
1:1 (Convention A); Python API names match too.

### CREATE — `scitex-todo add`

```bash
scitex-todo add \
  proj-scitex-todo-fleet-rollout \
  '[P1] Fleet rollout of scitex-todo skill across agents' \
  --status pending \
  --scope agent:proj-scitex-todo \
  --assignee proj-scitex-todo \
  --priority 10 \
  --note 'See tasks/proj-scitex-todo-fleet-rollout/README.md'
```

> **CLI gap (in flight, see [41_cli-mcp-gap-analysis.md](41_cli-mcp-gap-analysis.md)):**
> `add` does not yet accept `--task` / `--project` / `--host` / `--agent`
> / `--goal` / `--blocker` / `--pr-url` / `--issue-url` / `--kind` —
> the legacy `--scope` / `--assignee` are the bridge until those land.
> Use the Python API or open the YAML in your editor (single-row write,
> through `save_tasks`, not raw text-edit) until the CLI catches up.

MCP equivalent: tool `add_task` (same kwargs, returns JSON).

Python equivalent:

```python
from scitex_todo import add_task
add_task(
    None,                           # tasks_path; None = resolve default
    id="proj-scitex-todo-fleet-rollout",
    title="[P1] Fleet rollout of scitex-todo skill across agents",
    status="pending",
    scope="agent:proj-scitex-todo",
    assignee="proj-scitex-todo",
    priority=10,
    note="See tasks/proj-scitex-todo-fleet-rollout/README.md",
)
```

### LIST — `scitex-todo list-tasks`

```bash
scitex-todo list-tasks --scope agent:proj-scitex-todo --json
```

Filters today: `--scope` / `--assignee` / `--status` (exact match).
Use `--json` for machine output.

> **CLI gap (see gap analysis):** `--agent` / `--project` / `--host` /
> `--blocker` / `--kind` / `--blocking-me` filters are NOT in yet.
> Pipe through `jq` on the `--json` output for now.

### UPDATE — `scitex-todo update`

```bash
scitex-todo update proj-scitex-todo-fleet-rollout \
  --status in_progress \
  --priority 5 \
  --note 'Skill draft pushed PR #N; gap closures next'
```

Pass an empty string (`--scope ''`) to CLEAR a field.

> **CLI gap (see gap analysis):** same field set as `add` — operator-
> co-designed fields are missing. Bridge via Python API.

### COMMENT (activity log) — Python API only TODAY

The `comments: list[{ts, author, text}]` array is the **append-only
activity log** every agent writes to when coordinating cross-lane.
There is no `scitex-todo comment` verb today (that's the #1 CLI gap
— see gap analysis). Use the Python API:

```python
import scitex_todo
from scitex_todo import _store
import datetime as _dt
_store.update_task(
    None,
    "proj-scitex-todo-fleet-rollout",
    comments=[
        # ... existing comments preserved by load → append → save_tasks ...
        {
          "ts": _dt.datetime.utcnow().isoformat() + "Z",
          "author": "proj-scitex-todo",
          "text": "Skill draft pushed PR #N; awaiting lead design ACK.",
        },
    ],
)
```

> **Don't** edit `comments[]` by hand-editing the YAML. The closed
> ts/author/text shape is validated; missing keys raise.

### COMPLETE — `scitex-todo done`

```bash
scitex-todo done proj-scitex-todo-fleet-rollout --by proj-scitex-todo
```

Stamps `_log_meta.completed_at` (UTC ISO-8601) + `completed_by` (the
`--by` value, defaults to `$SCITEX_TODO_AGENT` then `$USER`).
Idempotent (re-doneing a `done` task keeps the original stamp).

MCP: `complete_task`. Python: `scitex_todo.complete_task`.

### RE-OPEN (undo a done / resolve)

There's no `reopen` CLI verb today. Use `update --status pending`:

```bash
scitex-todo update proj-scitex-todo-fleet-rollout --status pending
```

The web board's `/reopen` HTTP endpoint (PR #61) is operator-facing;
the CLI parity is on the gap list.

---

## Long-form prose — `tasks/<id>/README.md` + `adr.md`

Whenever a task has substantive context, seed the per-task dir:

```bash
mkdir -p tasks/proj-scitex-todo-fleet-rollout
$EDITOR tasks/proj-scitex-todo-fleet-rollout/README.md   # what / why / how
$EDITOR tasks/proj-scitex-todo-fleet-rollout/adr.md      # ADR-template decisions
```

- `README.md` is the **Issue BODY** — free-form markdown. Reference it
  from the task's `note` field (one-line "see ...").
- `adr.md` is the **append-only decision log** in the SciTeX ADR
  template (`~/.claude/skills/scitex/general/04_docs/05_adr.md`):
  six sections (Status / Context / Decision / Consequences / Notes),
  immutable once accepted, superseded by a new entry.

NO sidecar `metadata.json`, NO YAML frontmatter in README.md — the
per-task dir is **prose only**; `tasks.yaml` is the structured-
metadata SSoT (operator TG 9513, lead a2a `45488600`).

> The per-task dir lives under whichever tier the row lives in. Your
> project-tier rows → `<your-project>/.scitex/todo/tasks/<id>/`. The
> lead's global-tier rows → `~/.scitex/todo/tasks/<id>/`.

---

## Coordinating with other agents

Three patterns. Pick the one that fits the shape of the dependency.

### A. Hard dep — "I'm blocked by their task"

Add `depends_on: ["<their-project>/<their-task-id>"]` to YOUR row,
then `--status blocked --blocker dependency`. The fleet aggregator
joins the edge; the board shows a red edge from their row → yours;
when they flip theirs to `done`, the operator + you see your row's
blocker drop within 5s.

```bash
# (Once --depends-on lands as a CLI flag on update; today via Python.)
scitex-todo update my-task --status blocked   # --blocker dependency pending CLI parity
# Python:
import scitex_todo, datetime as _dt
scitex_todo.update_task(
    None, "my-task",
    status="blocked",
    # blocker="dependency",         # pending --blocker on CLI; Python honors it
    # depends_on=["neurovista/their-task-id"],  # pending --depends-on on update
)
```

### B. Coordination note — "FYI on their task"

Append a `comments[]` entry on THEIR row. You can read+append other
agents' rows (READ is open; comments[] are append-only); you cannot
mutate their other fields. The owning agent (per `task.agent`)
controls all non-comment writes.

```python
# Adds a comment to a task you don't own.
import scitex_todo, datetime as _dt
scitex_todo.update_task(
    None, "neurovista/cohort-a-rerun",
    comments=[
        # existing entries first (load_tasks → preserve)
        {"ts": _dt.datetime.utcnow().isoformat() + "Z",
         "author": "proj-scitex-todo",
         "text": "FYI my fleet-rollout PR will need this; flagged."},
    ],
)
```

### C. Ask — "I need them to do something"

DO NOT create a task in their tier with their name as `agent`. Create
the ASK on YOUR tier with `status: blocked`, `blocker: agent-wait`,
a `comments[]` entry naming the agent + the ask. The lead /
operator routes through the board (or via a2a). Once they accept the
ask they create their own row in their tier (with a `depends_on:` of
your id, closing the loop).

This preserves "agents own their own lane" — no cross-lane writes
even with the best intentions.

---

## Lead↔worker sync — what the lead sees vs what you do

- **You write to your project tier.** Frequency: as your work
  changes. CLI / MCP / Python — your choice.
- **The aggregator** (sidecar on the operator's host) reads every
  project tier every 5s via SSH-fanout, rebuilds the global tier.
- **The lead reads the global tier** through the board (`:8051`) +
  `~/.scitex/todo/tasks.yaml` + cross-project ADRs.
- **GitHub** is the durable cross-host sync substrate. Run `git
  commit && git push` on your project's `.scitex/todo/` directory
  (today gitignored by default — your project can opt-in to commit
  it once stable; OR rely solely on aggregator SSH-fanout for
  liveness + GitHub for durable). See ADR-0006 for the
  GitHub-vs-SSH-fanout split.

You can ALSO directly read the global tier when you need the fleet
view (e.g. "what's the lead waiting on across all projects?"):

```bash
SCITEX_TODO_TASKS=~/.scitex/todo/tasks.yaml scitex-todo list-tasks --json
```

---

## Operating discipline — what NOT to do

- **Don't edit `tasks.yaml` with `sed` / `awk` / a text editor.**
  Through the CLI/MCP/Python every time.
- **Don't catch-and-ignore validator errors.** `TaskValidationError`
  is the schema telling you the input is wrong. Fix the input.
- **Don't write to other agents' tiers.** Only `comments[]` is
  append-only-cross-lane.
- **Don't put prose in `tasks.yaml`.** `note` is one short line; full
  prose lives in `tasks/<id>/README.md`. Don't put YAML structure in
  markdown either.
- **Don't invent new statuses / kinds / blockers.** The validator
  REJECTS them. If a new value is needed, propose it in `adr.md` for
  the package owner (`proj-scitex-todo`) to add to the enum.

---

## Sanity-check yourself once you've adopted

```bash
# 1. Resolve points where you expect (project tier first inside a repo;
#    global tier when you're on the operator's host or env-overridden).
scitex-todo resolve-store

# 2. List YOUR slice + confirm count.
scitex-todo list-tasks --scope agent:<your-agent-name> --json | jq length

# 3. Round-trip a write — add a smoke task, then done it.
scitex-todo add smoke-$(date +%s) '[P2] smoke from <your-agent-name>'
scitex-todo done smoke-<that-stamp>

# 4. See yourself on the board.
# Open http://<board-host>:8051/ — your row should appear in <5s.
```

If any of the above fails: STOP and `reply` to your lead with the
exact failing command + full stderr. The package's "fail loud" rule
applies to you using it too — silent dropouts are the bug we're
trying to eliminate.

---

## Reference

- [04_cli-reference.md](04_cli-reference.md) — full CLI surface.
- [05_mcp-tools.md](05_mcp-tools.md) — MCP tool surface.
- [30_two-tier-conventions-and-write-protocol.md](30_two-tier-conventions-and-write-protocol.md)
  — write protocol contract (who writes when, conflicts, ACL).
- [41_cli-mcp-gap-analysis.md](41_cli-mcp-gap-analysis.md) — the
  known surface gaps you'll hit while this skill rolls out; tracks
  what's bridged via Python API today + what's in flight.
- `Task` dataclass: `src/scitex_todo/_model.py` (the single schema
  source).
- Operator directives: HANDOFF.md NORTH STAR + Telegram 9494 ("no
  direct writes; validator + dataclass; fail loud, fail fast,
  SSoT").
