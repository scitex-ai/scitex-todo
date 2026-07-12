---
description: |
  [TOPIC] Two-tier conventions + write protocol — project-level vs global
  [DETAILS] How the fleet uses scitex-cards as the shared SSoT: per-project
  `<project>/.scitex/todo/` (each agent owns its own lane) rolls up into the
  global `~/.scitex/todo/` (fleet-wide aggregate the board renders). Who
  writes what, when, and with which conflict rules — the load-bearing
  contract for the fleet migration off the lead's in-memory TaskList onto
  the persistent board.
tags: [scitex-cards-conventions, scitex-cards-write-protocol, scitex-cards-fleet]
---

# Two-tier conventions + write protocol

scitex-cards is the fleet's **shared dependency map** (HANDOFF.md
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

## Architectural backbone — `scitex-cards` is STANDALONE; the fleet plugs in via PORTS

Operator's defining rule (TG 9678, lead a2a `fae53b8e`):

> "scitex-cards はそれだけで独立したパッケージであるべきで、他を知らないが、
> extension port は持っている"
> (scitex-cards MUST be standalone, knows nothing about fleet/sac/
> scitex-specifics, but exposes extension ports through which
> fleet-specific behaviour plugs in.)

This skill describes the *conventions* an adopting agent must follow.
Those conventions are wired through extension ports — the **core
package never imports sac, a2a, SSH-fanout, or the 6-stream list**.
Reading this skill, mentally replace:

- "sync via git + GitHub" ⇒ "the TaskSyncPort impl your fleet installs"
- "publish on `scitex-cards:task:<id>`" ⇒ "NotificationPort.publish"
- "SSH-fanout to peer hosts for liveness" ⇒ "LivenessPort.list_agents"
- "sac fleet groups gate ACL" ⇒ "IdentityACLPort answers"

The fleet-specific implementations live in a SEPARATE package
(e.g. `scitex-cards-fleet` or in the scitex-agent-container glue),
not inside `scitex-cards`. ADR-0006 in `docs/adr/` has the four port
Protocol definitions + the dependency-injection wiring. This skill
documents the **adoption-side** conventions; the **core-side**
contract is in the ADR.

A standalone `pip install scitex-cards` ships with default
implementations (LocalFileSync / InProcessPubSub / NullLiveness /
OpenACL) so the package is independently usable. The conventions
below describe the FLEET deployment shape — operator-host
running aggregator + watcher + board, agents writing project
tiers, etc.

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

The board (`scitex-cards board`) reads from THIS tier. The mermaid
adapter, the MCP tools, every UI surface — all read from
`~/.scitex/todo/tasks.yaml` as the canonical source.

`agents.json` is sidecar-written by the cross-host SSH-fanout watcher
(ADR-0002 in `tasks/proj-scitex-cards-fleet-liveness/adr.md`); the
board's `/agents` endpoint reads it for the fleet-liveness panel.
The file is JSON not YAML because it's machine-generated, never
human-edited.

## Path resolution — project overrides user

Follows the SciTeX local-state-directories ecosystem convention
(see the scitex_dev skill `general/05_paths/01_local-state-dirs`):

```
resolve_tasks_path(): highest precedence wins
  1. $SCITEX_TODO_TASKS_YAML_SHARED env var (if set)
  2. <git-root>/.scitex/todo/tasks.yaml (project tier, if exists)
  3. $SCITEX_DIR/todo/tasks.yaml  (default ~/.scitex/todo/tasks.yaml,
     global tier)
  4. bundled examples/tasks.yaml (last-resort fallback)
```

PathManager handles this — agents use `from scitex_cards._paths import
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
  Task dataclass (see `proj-scitex-cards-quality-hygiene/README.md`
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
   scitex-cards init --here   # writes a starter tasks.yaml
   ```
3. Add tasks via `scitex-cards add` (CLI) or the MCP `add_task` tool;
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
from scitex_cards import resolve_store, list_tasks

store_path = resolve_store(global_=True)  # forces the global tier
all_tasks = list_tasks(path=store_path)
```

The `global_=True` parameter bypasses the project-tier precedence
when an agent specifically wants the FLEET view (e.g. to find a
cross-repo dep, or to render its own dashboard panel).

## Cross-host sync — git-backed durable, SSH-fanout live (ADR-0006)

Two-tier sync model (operator + lead a2a `3d7a20e7`):

| Tier                                | Sync mechanism                                          | Latency  |
| ----------------------------------- | ------------------------------------------------------- | -------- |
| project `<proj>/.scitex/todo/`      | git commits + GitHub push/pull (no peer rsync)         | minutes  |
| global `~/.scitex/todo/tasks.yaml`  | aggregator sidecar rebuilds from SSH-fanout to project tiers | 5s tick  |
| global `~/.scitex/todo/agents.json` | sac-status-writer sidecar rebuilds from SSH-fanout to peer sac registries | 5s tick |

The **project tier is the SSoT** (durable, git-backed); the **global
tier is ephemeral**, rebuilt continuously by sidecars on the
operator's host. Failure modes:

- GitHub down: live SSH-fanout still rebuilds the global; operator
  sees current state; durable sync resumes when GitHub returns.
- SSH-fanout fails for a peer: the global flags that project tier
  UNREACHABLE (per-tier as_of stamp surfaces; not silently omitted).
- Both: stale data with explicit staleness markers per row.

Agents writing their OWN project-tier `tasks.yaml` should
`git commit + git push` after the write (or batch — committer's
choice). The aggregator picks up changes from the SSH-fanout read
within 5s even before the commit; the commit is what makes the
state durable across host restarts.

## Task referencing / citation — `<project>/<local-id>` (ADR-0006)

Stable, fleet-wide task ids use a slash-separated two-segment form:

```
<project>/<local-id>
```

- `<project>` = the project's directory basename (matches `Task.project`).
- `<local-id>` = the agent's chosen string, unique within the project.

Examples:

```
paper-scitex-clew/cohort-a-rerun
scitex-hub/decide-prod-cutover-final-go
scitex-cards/proj-scitex-cards-fleet-liveness
```

**URL scheme** (the board's Django serves it):

```
http://<board-host>:8051/task/<project>/<local-id>       # canonical
http://<board-host>:8051/t/<project>/<local-id>          # short alias
```

Operator / lead / agents cite this URL in chat / a2a / comments.
Pasting it into a markdown comments[] entry auto-links via the
board's renderer. Citation in chat: "see
`paper-scitex-clew/cohort-a-rerun`" is unambiguous across the
fleet.

**Backward-compat**: existing single-segment ids
(`proj-scitex-cards-compute-state-deps`) carry through; the
aggregator stamps `_log_meta.canonical_id = "<project>/<id>"` on
read so the URL works on legacy rows.

## Update → subscriber notification — reuse a2a/channel push (ADR-0006)

When a task is updated (via the board's `_store.update_task`, an
agent's direct YAML edit + commit, or the operator's Resolve button),
the change is **published on the sac channel bus** the fleet already
uses for agent wake-ups:

```
event channel: scitex-cards:task:<project>/<local-id>
payload:       {task_id, changes, ts, actor}
```

**Subscription rules**:

| Subscriber       | Subscribes to                                            | Action on receive                                                            |
| ---------------- | -------------------------------------------------------- | ---------------------------------------------------------------------------- |
| owning agent     | `scitex-cards:task:<own-project>/*`                       | wakes (empty-beacon-fix + wake-generalize) + acts on the change              |
| dependent agent  | `scitex-cards:task:<each-of-its-depends_on-ids>`          | wakes + re-evaluates readiness; auto-unblock if a dep flipped to done       |
| UI (every viewer)| `scitex-cards:task:*` (filtered client-side)              | re-fetches /graph + re-renders affected card / panel                         |
| lead             | `scitex-cards:task:*` firehose                            | logs into _log_meta; no auto-action                                          |
| operator         | (interacts via UI; UI is the subscriber)                 | UI surfaces the change visually                                              |

**Critical synergy**: this rides the SAME push infra being hardened
by the empty-beacon fix (proj-scitex-agent-container) + the
wake-generalize (any-channel-wakes-idle-agent). scitex-cards is one
of the loadiest consumers — every task update is a potential
agent-wake event. **Do NOT invent a parallel notification system.**

**Fallback to polling**: if the push bus is down, subscribers fall
back to the existing 5s `/rev` polling (AutoRefresh.tsx). Push is
the FAST path; poll is the durable path. Same shape as the
GitHub-vs-SSH-fanout split above.

## Cross-reference

- **HANDOFF.md** — SSoT DATA LAYOUT + NORTH STAR pillars #3
  (cross-host sync) and #4 (live/online/shared/machine-readable).
- **ADR-0002** — `kind` enum, fail-loud Literal pattern.
- **ADR-0003** — `kind: "decision"` for first-class decision-nodes.
- **ADR-0004** — `blocker` enum, orthogonal to `kind`.
- **ADR-0005** — fleet-liveness panel + SSH-fanout watcher.
- **ADR-0006** — full board UI spec + GUI→code wiring.
- **`tasks/proj-scitex-cards-quality-hygiene/README.md`** — Task
  dataclass = single schema source.
- **`scitex_dev` skill** `general/05_paths/01_local-state-dirs` —
  the ecosystem local-state directories convention this skill
  inherits from.

## Canonical auto-merge poll — CI-green = `{CLEAN, UNSTABLE}`

Lead a2a `9c4d3dc4` (2026-06-07): a wedge discovered on PR #52 + #55
because the auto-merge poll only treated `CLEAN` as terminal. Both
PRs sat at `UNSTABLE` for 85+ minutes — required checks were green,
but a NON-required check was failing, so the poll slept forever.

**Canonical rule** (every fleet auto-merge loop should treat this
the same — capture for clew / neurovista / ripple / etc. patterns):

> The operator's standing **"CI-green ⇒ auto-merge"** authorization
> applies when `mergeStateStatus` is in `{CLEAN, UNSTABLE}`.
> `UNSTABLE` means a NON-required check is red while all required
> checks pass, which is still mergeable per branch protection.

Bash shape (the bug-fixed version every auto-merge poll should use):

```bash
until s=$(gh pr view "$PR" --json mergeStateStatus -q .mergeStateStatus 2>/dev/null); \
        [ "$s" = "CLEAN" ] \
     || [ "$s" = "UNSTABLE" ] \
     || [ "$s" = "DIRTY" ] \
     || [ "$s" = "BLOCKED" ] \
     || [ "$s" = "HAS_HOOKS" ] \
     || [ "$s" = "BEHIND" ]; do
    sleep 30
done
# Terminal-mergeable: CLEAN or UNSTABLE → gh pr merge --squash
# Non-mergeable terminal: DIRTY (conflicts) / BLOCKED (required check
# failed) / BEHIND (base moved) / HAS_HOOKS (commit hooks failing) →
# investigate, don't auto-merge.
```

**Hygiene caveat** (lead a2a `9c4d3dc4`): `UNSTABLE` hides WHICH
non-required check is failing. Auto-merge on it is correct, but if
the SAME check is unstable across many PRs, the check is probably a
real signal we're ignoring and should be PROMOTED to required.
Flag the pattern — don't let "always UNSTABLE" become invisible.

This pattern is canonical for every fleet auto-merge loop, not just
scitex-cards's. clew / neurovista / ripple / etc. that ship their own
wait-on-CI auto-merge loops should match this shape. Documented here
as the reference for the fleet's dogfood-of-scitex-cards adoption.
