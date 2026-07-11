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

You are a fleet agent (a SAC peer) **OR the lead**.
The operator has made `.scitex/todo/` the **single canonical home**
for fleet task state — yours, the lead's, every other agent's, the
operator's own. This skill tells you exactly what to do.

This skill is the **teaching surface** — wire it as a `required_skill`
in your spec.yaml (see [§ Propagation](#propagation--the-path-mechanism)
below) and it auto-loads on every agent boot.

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

## Your first task, in 30 seconds (fresh agent quick-start)

```bash
# 1. Confirm scitex-todo is installed + which tier it resolves to.
scitex-todo --version
scitex-todo resolve-store                  # prints the resolved path + chain

# 2. Add a smoke task to YOUR slice.
scitex-todo add <you>-smoke-$(date +%s) \
    '[P2] smoke: confirm I can write to .scitex/todo' \
    --scope agent:<you> \
    --assignee <you> \
    --status pending

# 3. List your slice + confirm the row is there.
scitex-todo list-tasks --scope agent:<you> --json | jq '.[].id'

# 4. Mark it done.
scitex-todo done <you>-smoke-<that-stamp> --by <you>

# 5. See yourself on the board.
# Open http://<board-host>:8051/  — your row is visible within 5s.
```

If any step fails: STOP and `reply` to your lead with the exact
failing command + full stderr. Don't retry-loop. ([§ Operating
discipline](#operating-discipline--what-not-to-do))

---

## The two roots — `~/.scitex/todo/` vs `<git-root>/.scitex/todo/`

**This is the most important section.** Get this wrong and your tasks
land in the wrong place or get overwritten.

There are exactly TWO roots. They serve different purposes:

| Root                                | Scope                  | Who writes                                                                  | When to use                                                                                                            |
| ----------------------------------- | ---------------------- | --------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------- |
| `<git-root>/.scitex/todo/`          | PROJECT-LOCAL          | ONLY the agent who owns this repo (the project's lead-of-record)            | Tasks scoped to ONE project (this repo's code). 99 % of a worker agent's writes go here.                               |
| `~/.scitex/todo/`                   | USER-GLOBAL, fleet-shared | The lead, the operator, the aggregator sidecar, and (rarely) a worker agent on cross-project asks | Cross-project / fleet-coordination tasks: release cutovers, multi-repo decisions, the operator's "what's blocking me?" panel. |

**Plain-language rule (the one the operator wants every agent to know):**

- _"Is this task only about MY repo?"_ → **project-local** root
  (`<git-root>/.scitex/todo/tasks.yaml`).
- _"Does this task involve more than one repo, or is it a
  fleet-coordination ask the lead or operator needs to see?"_ →
  **user-global** root (`~/.scitex/todo/tasks.yaml`).

When in doubt, write to your **project-local** root — the aggregator
sidecar rolls it up into the user-global root within 5s, so the lead
+ operator still see it on the board. The cost of being wrong is
zero if you stayed local.

### How precedence picks one for you

`scitex-todo` resolves the store in this order; first existing wins:

| # | Path                                                  | Role                                                                       |
| - | ----------------------------------------------------- | -------------------------------------------------------------------------- |
| 1 | `$SCITEX_TODO_TASKS_YAML_SHARED` (explicit env)                   | Container glue (spec.yaml sets this for the agent's chosen scope).         |
| 2 | `<git-root>/.scitex/todo/tasks.yaml` (PROJECT-LOCAL)  | Default when you `cd <a repo>` — auto-picks the project-local root.         |
| 3 | `~/.scitex/todo/tasks.yaml` (USER-GLOBAL)             | Default when you're outside a git repo (or on the operator's host).         |
| 4 | bundled `examples/tasks.yaml`                         | Read-only fallback; never written.                                          |

Confirm where you're about to write BEFORE you write:

```bash
scitex-todo resolve-store
# → prints {resolved: <path>, chain: [...]}
```

### Forcing one root explicitly

Sometimes you NEED to override the precedence. The two patterns:

```bash
# Write into the user-global root from inside a repo:
scitex-todo --tasks ~/.scitex/todo/tasks.yaml add fleet-cutover-2026Q3 \
    '[P0] [GOAL] Fleet release cutover 2026Q3' ...

# Or env-pin for a session (useful in scripts):
SCITEX_TODO_TASKS_YAML_SHARED=~/.scitex/todo/tasks.yaml scitex-todo list-tasks --json
```

If you're running inside an agent container, the spec.yaml has
exported `$SCITEX_TODO_TASKS_YAML_SHARED` for you. The container glue chose
the right tier; trust it unless an explicit cross-tier need says
otherwise.

### What lives at each root (the per-task-dir layout repeats)

Both roots have the **same shape** — `tasks.yaml` + a `tasks/<id>/`
directory holding `README.md` (Issue body) + `adr.md` (decision log).
The shape repeats because the rules repeat: structured YAML for
the graph + metadata; markdown prose for the per-task body + the
decision log. See [§ Long-form prose](#long-form-prose--tasksidreadmemd--adrmd)
below.

See [30_two-tier-conventions-and-write-protocol.md](30_two-tier-conventions-and-write-protocol.md)
for the full write-protocol contract (who writes when, aggregator
rules, conflict resolution, ACL).

---

## Schema — closed enums, fail-loud

Every task is a `Task` dataclass (defined in `scitex_todo._model.Task`).
The validator REJECTS unknown values in the closed enums below.

**Required fields:**

- `id` (str, globally unique, kebab-case, **prefix with your project**:
  e.g. `scitex-todo-fleet-rollout`, `clew-cohort-a-rerun`).
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

- `assignee` (str) — **PRIMARY agent-linking field. Set this to YOUR
  agent name** (e.g. `scitex-todo`). The lead's empirical
  dogfood (2026-06-07) confirms `scitex-todo list-tasks --assignee
  <agent-id>` filters correctly — this is THE field that lets every
  consumer (lead, board, you) ask "show me agent X's open tasks."
  Forward-compat: the dataclass also has an `agent` field as the
  operator-co-designed long-term replacement; the migration is
  staged (CLI gains `--agent` as alias, deprecates `--assignee`)
  but TODAY you write `assignee`.
- `task` (str) — the 1-line CURRENT-task BIG text on the board card.
  Distinct from `title` (the short scannable label). Populate this for
  the card to read well.
- `project` (str) — your project's directory basename (e.g.
  `scitex-todo`). Matches the canonical id prefix.
- `host` (str) — where the work happens (`spartan` / `ywata-note-win`
  / etc.).
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
  scitex-todo-fleet-rollout \
  '[P1] Fleet rollout of scitex-todo skill across agents' \
  --status pending \
  --scope agent:scitex-todo \
  --assignee scitex-todo \
  --priority 10 \
  --note 'See tasks/scitex-todo-fleet-rollout/README.md'
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
    id="scitex-todo-fleet-rollout",
    title="[P1] Fleet rollout of scitex-todo skill across agents",
    status="pending",
    scope="agent:scitex-todo",
    assignee="scitex-todo",
    priority=10,
    note="See tasks/scitex-todo-fleet-rollout/README.md",
)
```

### LIST — `scitex-todo list-tasks`

```bash
scitex-todo list-tasks --scope agent:scitex-todo --json
```

Filters today: `--scope` / `--assignee` / `--status` (exact match).
Use `--json` for machine output.

> **CLI gap (see gap analysis):** `--agent` / `--project` / `--host` /
> `--blocker` / `--kind` / `--blocking-me` filters are NOT in yet.
> Pipe through `jq` on the `--json` output for now.

### UPDATE — `scitex-todo update`

```bash
scitex-todo update scitex-todo-fleet-rollout \
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
    "scitex-todo-fleet-rollout",
    comments=[
        # ... existing comments preserved by load → append → save_tasks ...
        {
          "ts": _dt.datetime.utcnow().isoformat() + "Z",
          "author": "scitex-todo",
          "text": "Skill draft pushed PR #N; awaiting lead design ACK.",
        },
    ],
)
```

> **Don't** edit `comments[]` by hand-editing the YAML. The closed
> ts/author/text shape is validated; missing keys raise.

### COMPLETE — `scitex-todo done`

```bash
scitex-todo done scitex-todo-fleet-rollout --by scitex-todo
```

Stamps `_log_meta.completed_at` (UTC ISO-8601) + `completed_by` (the
`--by` value, defaults to `$SCITEX_TODO_AGENT_ID` then `$USER`).
Idempotent (re-doneing a `done` task keeps the original stamp).

MCP: `complete_task`. Python: `scitex_todo.complete_task`.

### RE-OPEN (undo a done / resolve)

There's no `reopen` CLI verb today. Use `update --status pending`:

```bash
scitex-todo update scitex-todo-fleet-rollout --status pending
```

The web board's `/reopen` HTTP endpoint (PR #61) is operator-facing;
the CLI parity is on the gap list.

---

## Long-form prose — `tasks/<id>/README.md` + `adr.md`

Whenever a task has substantive context, seed the per-task dir:

```bash
mkdir -p tasks/scitex-todo-fleet-rollout
$EDITOR tasks/scitex-todo-fleet-rollout/README.md   # what / why / how
$EDITOR tasks/scitex-todo-fleet-rollout/adr.md      # ADR-template decisions
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
         "author": "scitex-todo",
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

## Lead-role usage — the lead is a consumer too

The lead (`scitex-lead`) is a first-class consumer of this skill, not
just the worker agents. Operator's explicit standing direction
(2026-06-07): "the lead writes its own board into `.scitex/todo`."
So when this skill is wired into the lead's spec.yaml via
`required_skills`, the lead reads + writes through the same surface.

The lead's writes differ from a worker's in **scope**, not in
**mechanics**:

- **Default root:** **user-global** `~/.scitex/todo/tasks.yaml` (vs
  a worker's project-local). The lead coordinates ACROSS projects,
  so its natural home is the fleet tier.
- **Cross-project rows it owns:** release cutovers, ADRs that touch
  multiple repos, the operator's "BLOCKING YOU" queue, fleet-wide
  campaigns.
- **Per-task assignee:** `assignee: scitex-lead` on rows the lead
  drives; rows the lead REASSIGNS to a worker land with that
  worker's `assignee` value (and the worker takes ownership from
  then on). (Same field reconciliation as workers: `assignee` is
  primary today; `agent` is the forward-compat migration target.)
- **Resolves rows on behalf of the operator:** when the operator
  delegates a Resolve, the lead writes the resolution + an
  `adr.md` Notes entry capturing the rationale + provenance.

The lead can write into a project-local root when it's seeding a
follow-up for a specific repo's agent (then the worker takes over):

```bash
# Lead seeding a task into the scitex-todo project tier:
scitex-todo --tasks ~/proj/scitex-todo/.scitex/todo/tasks.yaml \
  add scitex-todo-fleet-rollout \
  '[P0] Fleet rollout of scitex-todo skill across agents' \
  --scope agent:scitex-todo \
  --assignee scitex-todo \
  --status pending
```

After seeding, the row's owning agent (`assignee`) inherits the
write-lane and the lead steps back to monitoring.

## Lead ↔ worker shared-board sync

The shared board is the operator's at-a-glance view of the whole
fleet. Both the lead and every worker need to converge on what it
shows. Three sync wires connect them; understanding all three keeps
you out of "did anyone tell me?" mode.

| Wire                            | Who runs it           | Latency  | Failure mode                                                                                  |
| ------------------------------- | --------------------- | -------- | --------------------------------------------------------------------------------------------- |
| Aggregator sidecar (SSH-fanout) | operator's host       | 5 s tick | Per-tier `as_of` stamp surfaces UNREACHABLE; not silently omitted.                             |
| GitHub `git push/pull`          | every writer          | minutes  | Durable cross-host substrate; survives host restarts; the aggregator is the LIVE complement.   |
| sac channel push (a2a notify)   | the writer            | sub-second | If the bus is down, the aggregator's 5 s poll catches up; push is FAST path, poll is durable. |

### What the WORKER does

- **Writes** to its project-local root (`<git-root>/.scitex/todo/`).
  Frequency: as work changes. CLI / MCP / Python — your choice.
- **Pushes** a sac channel event on `scitex-todo:task:<project>/<id>`
  for high-priority status flips so the lead + operator wake
  immediately (vs the 5 s poll).
- **Optionally `git commit && git push`** on its project's
  `.scitex/todo/` after batches of writes — this is what makes the
  state durable cross-host. Default is gitignored; opt in once your
  project's task store is stable.

### What the LEAD does

- **Reads** the user-global root through the board (`:8051`) +
  `~/.scitex/todo/tasks.yaml` directly + the per-task `adr.md`
  files when context is needed.
- **Writes** to the user-global root for fleet-coordination rows.
- **Subscribes** to the `scitex-todo:task:*` firehose on the sac
  channel bus — every worker write surfaces as a wake-up. Logs into
  `_log_meta`; no auto-action unless the row's a `kind: decision`
  one the lead is on the hook for.
- **Resolves** operator-decision rows on the BLOCKING YOU panel when
  delegated; appends the resolution into the row's `adr.md` Notes.

### What the OPERATOR sees

- The board (`:8051`) — auto-refreshes every 5 s via `/rev` mtime
  poll. AutoRefresh.tsx pulls + re-renders when the count or mtime
  changes.
- The BLOCKING YOU panel — strict predicate
  `status == "blocked" AND blocker == "operator-decision"`. Resolve
  button writes `status: done`, fires a `notify <agent>` a2a, and
  optionally appends a `comments[]` entry.

### Cross-reading the other tier

When a worker needs the fleet view (e.g. "what's the lead waiting on
across all projects?"):

```bash
SCITEX_TODO_TASKS_YAML_SHARED=~/.scitex/todo/tasks.yaml scitex-todo list-tasks --json
```

When the lead needs a specific worker's project tier (e.g. to verify
a row hasn't yet propagated):

```bash
SCITEX_TODO_TASKS_YAML_SHARED=~/proj/<worker-repo>/.scitex/todo/tasks.yaml \
    scitex-todo list-tasks --json
```

Same CLI, same flags — the only thing changing is which root you
read.

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
  the package owner (`scitex-todo`) to add to the enum.

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

## Propagation — the @path mechanism

This skill is the **teaching surface**: the operator's directive
(2026-06-07) is that every fleet agent auto-loads it on boot, so
"how do I file a TODO" is the same answer everywhere.

Mechanism (in the order a fresh agent picks it up):

1. **Pip-install pins the version** — `pip install
   scitex-todo>=<version>` lands the bundled skills under
   `<site-packages>/scitex_todo/_skills/scitex-todo/`.
2. **Agent's `spec.yaml` references the bundled path** under a
   `required_skills:` entry — exact grammar is the SAC container
   glue's domain, but the canonical reference shape is:

   ```yaml
   required_skills:
     - "@scitex_todo:_skills/scitex-todo/40_for-consuming-agents.md"
   ```

   (See [41_cli-mcp-gap-analysis.md § G](41_cli-mcp-gap-analysis.md#g-propagation-the-path-mechanism)
   for the wiring rationale.)
3. **Container boot resolves the reference** — the skill text loads
   into the agent's context; the agent now knows the protocol.
4. **Operator host: `scitex-todo skills install --claude-symlink`**
   back-fills the symlink under `~/.claude/skills/scitex/` so
   Claude Code on the operator's host sees the same skill.

**Versioning**: the skill is **version-pinned via the package**, NOT
edited live. Editing one skill leaf does NOT propagate via spec.yaml
until the consumer pip-bumps `scitex-todo`. That gives the lead a
deterministic rollout — pin the version on one agent at a time,
watch it adopt, broaden once stable.

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
