# ADR-0006: Full board UI spec — filter bar + per-project columns + BLOCKING YOU panel + GUI→code wiring

## Status

Accepted (2026-06-07)

## Context

The board's UX has been evolving in tight operator-feedback loops over
2026-06-06 / 2026-06-07. By 2026-06-07 a coherent **full spec** has
emerged that the operator is actively co-designing (TG 9522 / 9524 /
9667 / 9671 + lead a2a `8d066f54`). The pieces — filter axes, project
columns, blocker views, color-by-status+liveness, GUI-to-code wiring,
cross-host / cross-agent sharing, ACL, tags — have each been discussed
individually; this ADR is the consolidated **specification** so a
future maintainer (or a re-implementer in scitex-ui as the productize
step lands) has one place to read what the board MUST do.

Prior ADRs in scope:
- ADR-0001 — scitex-todo as the fleet's universal task layer.
- ADR-0002 — `kind` as a closed Literal enum, fail-loud.
- ADR-0003 — `kind: "decision"` for decision-nodes as first-class
  graph nodes.
- ADR-0004 — `blocker` as a closed Literal enum orthogonal to `kind`.
- ADR-0005 — fleet-liveness panel + SSH-fanout watcher.

This ADR layers on top of those, focused on what the OPERATOR SEES
and HOW THE GUI WIRES BACK INTO CODE.

## Decision

The scitex-todo board renders ONE PAGE with three regions and a
strict GUI-to-code wiring contract.

### Region 1 — Filter bar (top)

A single horizontal strip carrying every filter axis the operator
named, plus the blocking-me toggle:

| Filter axis | Source                           | Effect                                  |
| ----------- | -------------------------------- | --------------------------------------- |
| project     | Task.project                     | column visibility AND in-column filter  |
| host        | Task.host                        | filter (any host)                       |
| status      | Task.status (working/waiting/done/blocked) | filter (any one status)       |
| tag         | Task.tags (list)                 | filter by tag membership                |
| blocker     | Task.blocker (enum + "no blocker") | filter by blocker variant              |
| agent       | Task.agent                       | filter by owning agent                  |
| 🚧 blocking me toggle | computed predicate     | shortcut for status=blocked AND blocker=operator-decision |

All filters AND-compose. Search box (free-text over task / goal /
title / id) is a v1.1 candidate; not in v1.

### Region 2 — Center: 6 project columns

The 6 active streams (Clew paper / NeuroVista paper / SciTeX-Hub /
Ripple-WM paper / scitex-todo / scitex-dogfooding — definitive list
operator TG 9666 + 9671) render as fixed columns. Each column header
shows the project name + a task-count pill. Each task = ONE CARD
inside its project's column.

Card layout (from operator's co-designed Task dataclass, ADR-0007 the
quality-hygiene PR; field-by-field render contract):

```
┌─────────────────────────────────────┐
│ ▌ STATUS-LABEL      last 7s         │ ← color rail (left, 4px) +
│ ▌ 🎯 goal text (italic, muted)      │   status uppercase chip +
│ ▌ TASK TEXT IN BIG LINE             │   last_activity recency
│ ▌ host-pill         [PR-link]       │   🎯 prefix on goal
│ ▌ [tag] [tag] [tag]                 │   tags color-coded
│ ▌ 🚧 blocker: <variant>             │   blocker pill (gold on operator-decision)
└─────────────────────────────────────┘
```

Color logic (left rail + status chip):
- `status: working` → green (drives green halo on liveness in the
  fleet-liveness panel; combined with ADR-0003's recency: stale →
  amber → red).
- `status: waiting` → amber.
- `status: blocked` → red, **pulsing** (1.8s ease-in-out animation).
- `status: done` → grey.

Card click → opens the DETAIL DRAWER (Region 4 below).

### Region 3 — Right: BLOCKING YOU panel

A fixed-width (340px on wide screens, collapses on narrow) right-side
panel filtering to the STRICT predicate `status=="blocked" AND
blocker=="operator-decision"`. Per ADR-0004: do NOT dilute the lens
with transitive dependents.

Each row renders:
- the task text (big),
- project · host · created_at meta line (muted, monospaced),
- ⚖️ unblocks N impact badge (forward-closure count from ADR-0003),
- ⏱ wait-time since the task entered status=blocked (gold accent),
- ACTIONS: [Resolve → notify <agent>] (green primary) + [Open] (secondary).

Empty state: `Nothing waiting on you. ✓` (italic, muted) — the
operator's win condition.

### Region 4 — Card-click detail drawer (modal-ish)

Centered modal overlay (backdrop dimmed). Renders the full Task
dataclass field-by-field as a KV table:

- title (top, large)
- meta line: `<project> · <host> · agent <agent>` (monospaced, muted)
- KV rows: `status`, `blocker`, `last_activity`, `created_at`, `goal`,
  `tags`, `pr_url`, `issue_url`
- Actions: `[Close]` + `[Resolve → notify agent]`

Editing (status, assignee, tags) is in v1.1. v1 displays + Resolve
only — the operator-decision flow is the load-bearing path.

### GUI→CODE wiring (operator's explicit ask, TG 9667 + 9671)

The board is NOT a static dashboard. Operator actions flow into code:

| GUI action                          | Write path                                                                |
| ----------------------------------- | ------------------------------------------------------------------------- |
| Card click                          | (read) load Task by id from the store                                     |
| Edit status / assignee in drawer    | POST `/task/<id>` → `_store.update_task()` → ruamel writes `tasks.yaml`   |
| **"Resolve" on a BLOCKING YOU row** | (1) `status: done`, `blocker: null` in `tasks.yaml`. (2) FIRE an `a2a notify` to `Task.agent` carrying `{task_id, resolution: "operator-resolved", ts}`. (3) Optionally append a `comments[]` entry capturing the resolution. The owning agent picks the a2a up on its next turn and unblocks any dependent work. |
| Filter change                       | client-side only — re-renders from in-memory state                        |
| Tag add/remove on a card            | (v1.1) POST `/task/<id>/tags` → store write                               |

The "Resolve → notify agent" path is the **load-bearing GUI→code
loop** — it closes the "operator is the blocker" cycle bidirectionally
in one click. The board becomes the operator's primary mechanism for
unblocking agents, not just an observation surface.

Backend wire (the data flow direction):
- READ: `sac-status-writer` sidecar (SSH-fanout per ADR-0002) +
  `tasks.yaml` writes from agents → board reads via `/graph`,
  `/agents`, `/rev`, `/agents-rev`. Auto-refresh 5s.
- WRITE: operator GUI POSTs → Django handlers → `_store.py` ruamel
  writes → AutoRefresh delivers to every viewer within 5s. Agents
  receive a2a notifications synchronously on resolve-click.

### Cross-machine + cross-agent sharing (operator TG 9671)

The `tasks.yaml` lives at `~/.scitex/todo/tasks.yaml` on the
operator's host (current SSoT per HANDOFF.md). Cross-host
visibility is the north-star pillar #3 (cross-host sync via
GitHub) — until that lands, the operator's-host board is the
single canonical viewer + writer. The fleet-liveness panel
(ADR-0005) handles the cross-host READ-side (SSH-fanout to each
peer's `sac agents list --json`). The WRITE-side (operator edits
on his host writing to the agent's tasks) flows via the same
shared YAML once cross-host sync lands.

Cross-AGENT sharing is the default today: every agent + the
operator reads and writes the SAME `tasks.yaml`. No per-agent
silos. This is the HANDOFF.md SSoT DATA LAYOUT contract — the
write-here table specifies WHICH file each kind of change goes
to, but every agent sees every file.

### ACL — wire to the sac group model (task #2)

The sac fleet has a shared-`fleet` ACL group (task #2:
`e1-sac-fleet-acl`, queued). When that lands, scitex-todo grafts
onto it:

- READ ACL: every fleet-group member sees every task by default
  (the current state). Future per-task `acl: {read: [<groups>]}`
  field gates per-task visibility when needed.
- WRITE ACL: ditto with `acl: {write: [<groups>]}` for fields
  the operator wants to restrict (e.g. status mutation on a
  decision-node restricted to operator + lead).
- Validator extension: if `acl` is present, type-check the
  groups list against `sac fleet groups list --json`; fail-loud
  on unknown group names.

NOT in scope for v1 (no acl: field validators yet) — but the
schema reserves the field name + documents the future shape.

### Tags

`Task.tags: list[str]` — free-form string labels. The validator
type-checks (each entry non-empty string) but does NOT enforce a
closed enum (per the YAGNI rule in HANDOFF.md). Suggested
conventions baked into the prototype:

- priority: `P0` / `P1` / `P2` (color: gold)
- type: `paper` / `infra` / `release` (color: purple)
- stream-specific tags (free-form): `cohort-a`, `pac-line`,
  `cutover`, etc. (color: green)

Filter axis "tag" in the filter bar narrows to a single tag at
a time in v1; multi-tag conjunction is a v1.1 candidate.

## Consequences

**Positive:**

- The operator's "I want to SEE the fleet state" ask is structurally
  satisfied: one page, big-text, color-by-status, eye-magnet
  BLOCKING YOU panel.
- The Resolve-with-notify-agent path makes the board the primary
  GUI→code lever — clicking a button updates the YAML AND fires an
  a2a, so the operator's decision flows directly into agent action.
- The render contract (Task → card) is fully spelled out;
  re-implementing the board (e.g. as the productize-step's scitex-ui
  module) is a translation, not a redesign.
- ACL + tags + filtering each have a documented insertion point so
  v1.1 / v1.2 work doesn't need new ADRs — just new validators +
  UI surfaces hanging off this spec.
- The same dataclass feeds: validator (`_validate_tasks`), UI render
  (this ADR), Gitea field-map (HANDOFF.md), future README-frontmatter
  (HANDOFF.md). ONE source of schema truth.

**Negative:**

- The full UI is a significant build relative to today's drag-graph
  + table view. Mitigated by incrementalism: the v3 static
  prototype at `/tmp/scitex-todo-prototype/prototype-v3.html` (served
  on `:8052`) is the deliverable for visual review; the live wire-up
  lands across two PRs (quality-hygiene = Task dataclass + write
  endpoints; fleet-liveness = SSH-fanout + `/agents` endpoint).
- 6 fixed columns assume the fleet's stream-count is stable around
  6. If a 7th stream appears (e.g. a new paper line) the columns
  reflow; >10 streams the layout breaks. Acceptable: the fleet's
  stream count is operator-curated, slowly changing.
- The a2a-notify-on-resolve coupling makes the GUI flow load-bearing
  for agent unblocking. If sac's a2a transport is down, the YAML
  write still lands (the agent picks it up via AutoRefresh on its
  side); the a2a is the FAST path, the YAML is the durable path.

## Notes

- Operator's primary pains pinned for the design rationale:
  - "返事が来ない＝私にとって死んだのと同じ" (TG 9576) → drives the
    fleet-liveness panel + the color-by-recency logic (ADR-0003 +
    -0005).
  - "what's waiting on ME" (TG 9522) → drives the BLOCKING YOU
    panel + the operator-decision gold-LOUD halo on cards (ADR-0004).
  - "decision is a node" (TG 9524) → drives kind=decision + the
    decision-node ADR per-task (ADR-0003).
  - "文字でいろいろやられてもわからない" (TG 9666) → drives the
    visual-first layout (this ADR).
  - "cross-machine + cross-agent + ACL + tags + filtering" (TG 9671)
    → drives the filter bar + the sharing model + the ACL
    insertion point (this ADR).

- Per-task `adr.md` for this work lives in `tasks/proj-scitex-todo-
  full-board-ui-spec/` (new task to be added at PR open). The
  per-task ADR tracks the implementation rollout; this repo-arch
  ADR is the lasting spec.

- Visual reference: `/tmp/scitex-todo-prototype/prototype-v3.html`
  (served on `:8052`). v1/v2/v3 history kept in the same dir as
  the design evolves: v1 = stream-strip prototype; v2 = card
  renderer from operator schema; v3 = full UI spec (this ADR).

- Implementation across THREE PRs (locked order):
  1. `feat/task-dataclass-and-strict-validators` — the dataclass
     + write endpoints + Resolve→notify wiring (the quality-hygiene
     PR). Builds the data layer this UI consumes.
  2. `feat/board-full-ui-v3` — the FE rewrite delivering the
     filter bar + columns + BLOCKING YOU + drawer + Resolve button.
  3. `feat/fleet-liveness-panel` — the SSH-fanout watcher + the
     per-host header dot-strip / Fleet tab (ADR-0005). Lays
     alongside or after the FE rewrite depending on operator-
     review pace.
