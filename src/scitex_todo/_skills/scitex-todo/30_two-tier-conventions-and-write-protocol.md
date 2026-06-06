---
description: |
  [TOPIC] Two-tier conventions + write protocol — project-level vs global
  [DETAILS] How the fleet uses scitex-todo as the shared SSoT: per-project
  `<project>/.scitex/todo/` (each agent owns its own lane) rolls up into the
  global `~/.scitex/todo/` (fleet-wide aggregate the board renders). Who
  writes what, when, and with which conflict rules — the load-bearing
  contract for the fleet migration off the lead's in-memory TaskList onto
  the persistent board.
tags: [scitex-todo-conventions, scitex-todo-write-protocol, scitex-todo-fleet]
---

# Two-tier conventions + write protocol

scitex-todo is the fleet's **shared dependency map** (HANDOFF.md
NORTH STAR, operator 9501 + 9667 + 9671 + 9674). For that to work
without per-agent silos OR cross-agent drift, the package follows a
**two-tier convention**:

- **Project tier** — every project / agent owns its own
  `<project>/.scitex/todo/` directory, writes its tasks there.
- **Global tier** — `~/.scitex/todo/` is the fleet-wide aggregate; the
  board renders from it; the operator + lead write here when they
  coordinate cross-project.

This skill documents BOTH tiers + the write protocol that connects
them. Once a project adopts this convention, the fleet can read each
other's task state from one place + the board surfaces the whole map.

## Tier 1 — project-level `<project>/.scitex/todo/`

Every project (= every git repo where an agent works) gets its own
local-state directory under `.scitex/todo/`, mirroring the global
shape. The agent owning the project writes here for its OWN tasks.

```
<project-root>/.scitex/todo/
├── tasks.yaml                  ← project-scope tasks (Task dataclass)
└── tasks/
    └── <task-id>/
        ├── README.md           ← the Issue body (what/why/how, markdown)
        └── adr.md              ← append-only ADR-template decision log
```

This is gitignored by default (`.gitignore` should include
`.scitex/`). The reason: per-project task state evolves daily and
isn't versioned source; it's working notes + the agent's own
checklist. **Don't commit it to the project's git repo** — let the
fleet aggregator (Tier 2) pick it up.

**What goes in project-level `tasks.yaml`**:

- Tasks the project agent owns (its own work queue).
- Per-task `agent` field = the owning agent's name (matches the
  agent's sac spec.yaml name).
- Per-task `project` field = the project directory basename.
- Cross-project dependencies expressed via `depends_on: [<task-id>]`
  where the target id may live in ANOTHER project's tasks.yaml — the
  graph builder is lenient on dangling refs until the aggregator
  resolves them (Tier 2).

**What goes in `tasks/<task-id>/README.md`**:

The Issue body — what / why / how. Free-form markdown. Referenced
from the task's `note` field. Locked filename per operator TG 9511 /
lead a2a `dd1da069`.

**What goes in `tasks/<task-id>/adr.md`**:

Append-only ADR-template decision log per the SciTeX ADR convention
(see `~/.claude/skills/scitex/general/04_docs/05_adr.md`). One ADR
template entry per significant task-scoped decision (lifecycle flip,
plan change). Cross-cutting / repo-architectural decisions live in
the OWNING REPO's `docs/adr/NNNN-*.md` per the two-tier ADR
placement (HANDOFF.md SSoT-layout section); the per-task adr.md
carries a one-line cross-link.

## Tier 2 — global `~/.scitex/todo/`

The fleet aggregate. Same directory shape as Tier 1, with one
critical difference: **every project agent's tasks roll up into THIS
file**.

```
~/.scitex/todo/
├── tasks.yaml                  ← AGGREGATE of every project's tasks
├── tasks/
│   └── <task-id>/
│       ├── README.md           ← long-form, often authored at the
│       │                         global tier (e.g. for cross-project
│       │                         decisions / fleet-coordination tasks)
│       └── adr.md              ← per-task decision log
├── agents.json                 ← (machine-written by sac-status-writer
│                                  sidecar, ADR-0005; fleet-liveness
│                                  panel reads it)
└── board-venv/                 ← operator-side venv hosting the board
```

The board (`scitex-todo board`) reads from THIS tier. The mermaid
adapter, the MCP tools, every UI surface — all read from
`~/.scitex/todo/tasks.yaml` as the canonical source.

`agents.json` is sidecar-written by the cross-host SSH-fanout watcher
(ADR-0002 in `tasks/proj-scitex-todo-fleet-liveness/adr.md`); the
board's `/agents` endpoint reads it for the fleet-liveness panel.
The file is JSON not YAML because it's machine-generated, never
human-edited.

## Path resolution — project overrides user

Follows the SciTeX local-state-directories ecosystem convention
(see the scitex_dev skill `general/05_paths/01_local-state-dirs`):

```
resolve_tasks_path(): highest precedence wins
  1. $SCITEX_TODO_TASKS env var (if set)
  2. <git-root>/.scitex/todo/tasks.yaml (project tier, if exists)
  3. $SCITEX_DIR/todo/tasks.yaml  (default ~/.scitex/todo/tasks.yaml,
     global tier)
  4. bundled examples/tasks.yaml (last-resort fallback)
```

PathManager handles this — agents use `from scitex_todo._paths import
resolve_tasks_path` and never hand-construct the path. The same
function is called by the CLI's default `--tasks` and by the Django
board's runserver subprocess (after PR #46's env-export fix).

**Project tier OVERRIDES user tier** by default. An agent running
inside `<project>/` sees its own project's `tasks.yaml` first; the
global aggregate is the fallback. This is the "agent's own lane"
behaviour — the agent doesn't accidentally pollute the global file
with its scratch work.

## Write protocol — who writes when

The crux of the two-tier convention. Lead a2a `93e314b2` directly
captured this; documenting it here as the binding rule.

| Actor          | Tier     | When                                                              | What                                                                       |
| -------------- | -------- | ----------------------------------------------------------------- | -------------------------------------------------------------------------- |
| project agent  | project  | task create / status change / blocker change / comment            | OWN tasks: `tasks.yaml` + `tasks/<id>/README.md` + `tasks/<id>/adr.md`     |
| project agent  | global   | rarely; only when explicitly asked by lead/operator               | own tasks the lead promoted to fleet-level visibility                      |
| aggregator     | global   | continuously (sidecar, every 5s or on file-watch)                 | rolls project-level rows up into the global `tasks.yaml`                   |
| sac-status-writer (sidecar) | global   | every 5s on operator's host                          | `agents.json` (fleet-liveness payload; never `tasks.yaml`)                 |
| lead           | global   | fleet-coordination tasks; resolving operator-blockers             | cross-project tasks + ADR-template decision entries on cross-project rows  |
| operator (UI)  | global   | Resolve-button on BLOCKING YOU panel; re-prioritize via GUI       | status flips (status=done, blocker=null); priority changes; tag edits      |

### Project-agent rules (the "owns its own lane" contract)

- An agent writes tasks where `task.agent == <its-own-name>` to its
  project tier. It does NOT write to OTHER agents' tasks (no
  cross-lane writes).
- Status flips on its own tasks are FAIL-LOUD-validated against the
  Task dataclass (see `proj-scitex-todo-quality-hygiene/README.md`
  for the dataclass; ADR-0002/-0003/-0004 for the closed enums).
- If an agent wants to push a task to ANOTHER agent (e.g. "I need
  the SIF agent to rebuild"), it creates a row with
  `assignee = <other-agent>` + an entry in `comments[]` describing
  the ask. It does NOT directly edit the other agent's tasks.yaml.
  The aggregator routes the row to the right home.

### Aggregator rules (continuous roll-up)

- Polls every project's `<project>/.scitex/todo/tasks.yaml` (the
  set of project dirs is discovered from `sac fleet projects` or a
  config list — TBD; v1 = explicit list in the operator-host
  config).
- Reads each project tier; deduplicates by `task.id` (id MUST be
  globally unique across the fleet — convention: prefix with
  the project name, e.g. `clew-cohort-a-rerun`).
- Writes the merged result to `~/.scitex/todo/tasks.yaml` atomically
  (rename pattern, same as ruamel `save_tasks`).
- Conflict resolution: project tier wins for `task.agent ==
  <project-owner>` rows. Global tier wins for rows the LEAD or
  OPERATOR last touched (carry a `_log_meta.last_writer` stamp to
  disambiguate). When two writers race, the later-timestamped
  write wins (last-write-wins on the field-grain).

### Lead rules

- Writes ONLY to the global tier (`~/.scitex/todo/`).
- Writes fleet-coordination tasks the operator and multiple agents
  care about (e.g. release-cutover, shared decisions).
- May resolve a row on behalf of the operator when the operator
  delegates; logs the resolution in `tasks/<id>/adr.md` with
  `Notes` provenance.

### Operator rules (via the UI)

- Sees the global tier through the board.
- The Resolve button in the BLOCKING YOU panel:
  1. Writes `status: done`, `blocker: null` to the row in
     `~/.scitex/todo/tasks.yaml`.
  2. Fires an `a2a notify` to the row's `agent` field with the
     resolution payload.
  3. Optionally appends a `comments[]` entry capturing the
     resolution rationale.
- Re-priority via GUI: writes `priority: <int>` to the row.
- Tag edits via GUI (v1.1): writes `tags: [...]` to the row.

### Conflict / ownership rules

1. **Per-task ownership** is the value of `task.agent`. The owning
   agent has WRITE on every field. Other agents have READ access by
   default + can append `comments[]` but cannot mutate other fields.
2. **Operator + lead** have WRITE on every field on every row
   (sudo, basically).
3. **ACL** (when sac fleet groups land — task #2 / ADR-0006): a
   future `acl: {read: [<groups>], write: [<groups>]}` field gates
   per-task READ/WRITE access. v1 = open (every agent reads
   everything); ACL is the v1.1 hardening pass.
4. **Last-write-wins** at the field grain when two writers race.
   Practically rare because (a) the aggregator runs every 5s and
   (b) the agent → project-tier path is uncontended.

## How a project adopts this convention

1. Add `.scitex/` to the project's `.gitignore`.
2. Initialize the project tier:
   ```bash
   mkdir -p .scitex/todo/tasks
   scitex-todo init --here   # writes a starter tasks.yaml
   ```
3. Add tasks via `scitex-todo add` (CLI) or the MCP `add_task` tool;
   they land in `.scitex/todo/tasks.yaml` by precedence rule.
4. For any task that warrants long-form context: create
   `.scitex/todo/tasks/<task-id>/README.md` (Issue body).
5. For any task-scoped decision worth recording:
   `.scitex/todo/tasks/<task-id>/adr.md` (ADR template entry).
6. The aggregator picks up the project automatically (per the
   discovery rule above); within 5s the row shows up on the
   operator's board.

## How to read this from another project / agent

```python
# In any agent's code, to read the FLEET map:
from scitex_todo import resolve_store, list_tasks

store_path = resolve_store(global_=True)  # forces the global tier
all_tasks = list_tasks(path=store_path)
```

The `global_=True` parameter bypasses the project-tier precedence
when an agent specifically wants the FLEET view (e.g. to find a
cross-repo dep, or to render its own dashboard panel).

## Cross-reference

- **HANDOFF.md** — SSoT DATA LAYOUT + NORTH STAR pillars #3
  (cross-host sync) and #4 (live/online/shared/machine-readable).
- **ADR-0002** — `kind` enum, fail-loud Literal pattern.
- **ADR-0003** — `kind: "decision"` for first-class decision-nodes.
- **ADR-0004** — `blocker` enum, orthogonal to `kind`.
- **ADR-0005** — fleet-liveness panel + SSH-fanout watcher.
- **ADR-0006** — full board UI spec + GUI→code wiring.
- **`tasks/proj-scitex-todo-quality-hygiene/README.md`** — Task
  dataclass = single schema source.
- **`scitex_dev` skill** `general/05_paths/01_local-state-dirs` —
  the ecosystem local-state directories convention this skill
  inherits from.
