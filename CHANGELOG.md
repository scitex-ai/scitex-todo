# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and the project adheres to
[Semantic Versioning](https://semver.org/).

## [0.7.29] - 2026-07-02 — standalone user-delivery rail, notify/reminder engine, user registry + identity, and the release-pipeline fix

First successful PyPI publish since 0.7.10 — the release pipeline had been
broken (see Fixed below), so the accumulated work below shipped only now.

### Added

- **Standalone user-delivery rail**: scitex-todo's own notification path —
  channels + a delivery ledger + an always-on `notifyd` daemon (with a systemd
  unit) + a standalone MCP channel-notification server. Users-first, with no
  dependency on scitex-agent-container.
- **Notify / reminder engine**: nag-until-closed reminders with per-owner
  digest cadence, an owner allowlist for phased rollout, operator escalation
  for high-priority stale cards, and liveness-triggered escalation to the card
  creator when an assignee is unreachable.
- **User registry + canonical identity resolver**: collapses owner naming
  drift (host@name aliases) so notifications resolve to the right inbox;
  assignee liveness surfaced at assign time.
- **Model**: `cancelled` status (closed-as-not-planned terminal state).
- **Idle-guard Stop-hook**: blocks going idle while in-progress work is
  abandoned.
- **Fleet payload**: surfaces the waiting-on-operator queue (ids + SSOT count).

### Changed

- **Standalone decoupling from scitex-agent-container**: removed the sac
  listen-daemon HTTP fallback for turn URLs (zero runtime sac coupling) and
  reworded sibling-system names out of standalone-claim docstrings.
- **Store performance**: replaced the ruamel round-trip writer with a fast
  C-backed safe dump; config + reminders sidecar reads use the fast loader.
- **Board runtime state** now lives under `<store>/runtime/`.
- **Board v3 UX**: bigger timeline scatters with marquee select + Ctrl/Cmd+C
  copy + right-click menu; tighter left gutter; timeline edge legend on hover.
- **CI**: pytest-matrix serialized so one PR can't saturate all three runners.

### Fixed

- **Release pipeline**: the `publish` job declared `permissions: id-token: write`
  only, which defaults every other scope (including `contents`) to `none`, so
  `actions/checkout` could not clone the private repo ("Repository not found").
  Added `contents: read`. This had broken every tagged release since 0.7.10.
- **Fail-loud on unresolved actor/author** at task creation (no `getuser`
  fallback); board create-form requires creator/assignee.
- **Multi-select toolbar** no longer stretches to full column height.
- **Reminders**: parked-blocked cards excluded from the per-owner nag digest;
  store resolved before the notifyd reminder sweep.
- **Channel delivery**: drains producer-matching keys (raw name + resolved
  user-id); mutation store threaded into card-event emit so notifications
  actually enqueue.
- **Board**: falls back to the port-found board when the pidfile is stale.

## [0.7.28] - 2026-06-26 — board UX (timeline beeswarm/anti-flash, marker copy, user roles) + CI off paid runners

### Added

- **Timeline marker multi-select + copy + right-click menu** (`timelineSelect.js`):
  click markers to select, right-click for a menu, copy selected cards' contents
  to the clipboard.
- **Card detail user roles**: the detail drawer shows Creator / Assignee /
  Collaborators / Subscribers in user vocabulary; a `created_by` field is now
  captured at task creation (CLI `--created-by` + MCP), back-compatible with
  legacy rows; the `/graph` node payload emits `created_by` / `collaborators` /
  `subscribers`.
- **`help-wait` / `help-clear`** verbs + MCP tools (also in 0.7.27) — the SSOT
  card primitive the agent-waiting escalation hook calls.

### Changed

- **Timeline no longer flashes / jumps to top**: the raster skips its rebuild
  when the `/timeline` payload is unchanged and preserves scroll position; the
  main board likewise skips redraw on unchanged `/graph` and keeps scroll.
- **Comment posting is non-blocking + fail-loud**: the in-request agent relay
  uses a short (2s) timeout instead of 30s and surfaces a loud toast on a
  notify failure (comment is still saved).
- **CI moved off GitHub paid runners**: `cla.yml` + `auto-merge-to-develop.yaml`
  now run on self-hosted Spartan (auto-merge's `gh` calls rewritten as `curl`
  REST since Spartan has no `gh`); `newb-docs-quality` disabled (docker-only,
  pending apptainer). No workflow uses `ubuntu-latest`.

### Fixed

- **Fleet-adapter tests** skip (not fail) when `sac` is absent/non-functional,
  so a broken optional dependency can't red-gate CI (also in 0.7.27).

## [0.7.27] - 2026-06-25 — Timeline beeswarm + `help-wait` verb + sac-decoupled CI

### Added

- **Timeline beeswarm y-packing** (PR #245). In the board_v3 Timeline raster,
  time-overlapping markers in a lane used to render at the same vertical
  center and occlude each other. A deterministic sub-row packer
  (`timelinePack.js::packRows` — greedy interval partitioning, capped at
  `MAX_ROWS`) now fans co-located markers into stacked sub-rows and grows the
  lane to fit, so every task is visible. x/time math and the time-axis are
  unchanged.
- **`scitex-todo help-wait` / `help-clear`** CLI verbs + `help_wait` /
  `help_clear` MCP tools (PR #242). First-class "an agent is waiting on the
  operator" card semantics (`help-<agent>-waiting`, `status=blocked`,
  `blocker=operator-decision`), idempotent atomic upsert / resolve. Lifts the
  card shape out of the dotfiles Notification hook so scitex-todo owns the
  single source of truth; the hook becomes a thin trigger that calls the verb.

### Changed

- **Fleet-adapter tests decoupled from the live `sac` binary** (PR #244). The
  happy-path hosts tests now SKIP (not FAIL) when `sac` is absent or
  non-functional, via a shared probe guard — so a broken/missing optional
  fleet dependency can never red-gate the standalone package's CI. Fail-loud
  adapter-error tests still run (they need no working sac).

## [0.7.25] - 2026-06-15 — `scitex-todo ci-watch` (record-only CI poller)

### Added

- **`scitex-todo ci-watch`** + **`scitex-todo.ci-watch` cron JobSpec**
  (PR #206, lead a2a `b4c10158` / operator decoupled-pollers override
  via dev a2a `96afacc7`). Record-only CI poller — server-side
  `*/5 * * * *` cron that sweeps every repo in
  `dashboard.yaml → fleet.ci_status.repos` (or env override
  `SCITEX_TODO_FLEET_CI_REPOS=owner/a,owner/b`), diffs against the
  local state cache at `~/.scitex/todo/ci-state.json` (override via
  `SCITEX_TODO_CI_STATE`), classifies the transition
  (`first-seen` / `newly-green` / `newly-red` / `still-pending` /
  `unchanged`), and logs one stderr line per repo.

  Lane: **todo records, SAC delivers** — todo writes no a2a sends
  and emits no bus events; SAC has its own independent poller for the
  delivery side. Either side can crash without breaking the other.
  The dedupe key (`head_sha`, `overall`) is content-keyed so SAC's
  poller can run at a different cadence (10 / 15 / 30 min) without
  breaking parity.

  CLI:

      scitex-todo ci-watch --once                # cron mode (one sweep)
      scitex-todo ci-watch --interval 600        # loop with custom cadence
      scitex-todo ci-watch --once --dry-run      # plan + summary, no state write
      SCITEX_TODO_FLEET_CI_REPOS=owner/a scitex-todo ci-watch --once

  Wired into the ecosystem federation via `_jobs_provider.py`; after
  `scitex-dev ecosystem up`, the `scitex-todo.ci-watch.timer`
  systemd-user unit fires every 5 min. 18 mock-free tests
  (classifier purity, state load/save round-trip + atomic-write, CLI
  dry-run, JobSpec registration).

## [0.7.24] - 2026-06-14 — `scitex-todo mcp install-fleet` (P3a one-liner)

### Added

- **`scitex-todo mcp install-fleet --agents-dir <DIR>`** (PR #204,
  lead a2a `1ab212f3`). One-shot fleet sweep — walks every
  ``<agents-dir>/*/to_home/.mcp.json`` (the agent-container spec
  convention) and idempotently applies the scitex-todo MCP entry to
  each. Sibling MCP server entries preserved; per-agent corrupt JSON
  reported + sweep continues; final summary line carries
  ``agents=N updated=K noop=M errors=E``. Closes the missing-MCP gap
  that ripple-wm hit (had to a2a-relay through me for card add
  because their container's `.mcp.json` was bare). 12 mock-free
  CliRunner tests.

  Sweep one-liner for agent-container:

      scitex-todo mcp install-fleet \\
          --agents-dir ~/.dotfiles/src/.scitex/agent-container/agents \\
          --env-tasks-path /home/agent/.scitex/todo/tasks.yaml -y

  Mirrors the single-file ``install --apply`` semantics (PR #155 +
  #158) — same backup, same idempotency, same env-pin.

## [0.7.23] - 2026-06-14 — Board v3: time-based view (sort + group by time)

### Added

- **Sort by time + Group by time on the v3 board** (PR #201
  cherry-picked via #202; lead a2a `ff1441d7`, operator request
  「時間でのビュー」). The v3 board at `/` (the operator's home view)
  now exposes time-based controls in the existing
  `.stx-todo-filterbar__group--view` group:
  - Sort dropdown extends with `created_at` + `completed_at` options
    (newest first) plus the reworked `last_activity` comparator.
  - New "Group by time" checkbox (`#stx-toggle-group-by-time`) folds
    each project column's cards under collapsible bucket headers:
    TODAY / THIS WEEK / THIS MONTH / OLDER. State persists in
    localStorage (`scitex-todo:group-by-time`,
    `scitex-todo:time-buckets-collapsed`).
  - New `board_v3/08-time-grouping.css` with token-only styling
    (bucket headers, chevrons, collapsed state, body left-rail).
  - 43 mock-free test cases pin the bucket classifier + sort-key
    helper + CSS contract.

  The existing Time View raster (PR #186) on `/legacy/` is
  untouched — this is a complementary control on the v3 board so
  the operator can sort/group by time WITHOUT switching to the
  React-SPA route.

### Notes for ops

PR #201 originally landed on `main` (subagent missed `--base develop`).
#202 cherry-picked the change onto develop and re-fixed the multi-line
Django comment that the cherry-pick re-introduced (regression caught
by `test__no_multiline_django_short_comments.py` from PR #199).

## [0.7.22] - 2026-06-14 — Hotfix: operator-visible Django template comment leak

### Fixed

- **board_v3 template comment leaked as literal text** (PR #199,
  lead a2a `f7a5d37930b9479ca7e53a7e316c132d`). Django's
  ``{# … #}`` syntax is single-line only — newlines between ``{#``
  and ``#}`` are NOT stripped, so the multi-line block at
  ``board_v3.html:200-208`` (introduced in PR #173) rendered as
  visible text on the board UI. Converted to
  ``{% comment %}…{% endcomment %}`` (multi-line safe). New
  regression test (``tests/scitex_todo/_django/test__no_multiline_django_short_comments.py``)
  walks every ``.html`` under ``_django/templates/`` and asserts
  every ``{#`` closes with ``#}`` on the same line — bug class
  pinned. Operator reported live; hotfix-released same hour.

## [0.7.21] - 2026-06-14 — Hook bus: ordering + card-message feedback channel

Two enhancements that close the **operator↔card↔owner+collaborators
feedback ring** Phase 6 was missing. Cross-package coordination via
the existing `scitex_todo.hooks` entry-point bus — no new poller, no
inter-package import.

### Added

- **Handler ordering primitives** (PR #196). Two optional function
  attributes on hooks-bus handlers:
  - `on_event.priority = <int>` (default 100; LOWER runs FIRST).
  - `on_event.critical = True` (default False; if True and the
    handler raises, dispatcher aborts the chain and re-raises so the
    producer's HTTP/CLI wrapper translates to 500 / non-zero exit).
  Sort key is `(priority asc, entry-point-name asc)` — stable.
  Mutation visible by reference (early handlers' mutations land for
  late handlers). Plugin LOAD failures (ImportError on `ep.load()`)
  logged as `"load: <msg>"` in `plugin_errors`; chain continues.
  Each error entry now carries `priority` + `critical` metadata so
  the producer can see the failure context. 11 mock-free tests.
  Designed with dev for the ci-result chain (owner-map priority=10
  critical=True before SAC's delivery at priority=200).
- **`card-message` event kind** (PR #197). Every comment landing on
  a card via `_store.comment_task` fans out a `card-message` event
  on the bus. Payload: `{kind, card_id, body, author, owner,
  collaborators, created_at}`. Owner resolution falls back
  `card.agent → card.assignee → null`. Collaborators is the
  pre-append snapshot of distinct comment authors, deduped,
  EXCLUDING owner AND new author (SAC must not echo). Emit happens
  OUTSIDE the file-lock so slow handlers can't starve writers; bus
  errors are caught + logged so external handler failure (SAC
  unreachable, missing entry-point) never breaks the producer's
  comment-save. 15 mock-free tests.
  Surfaces emit: `/chat/<card_id>` POST, `scitex-todo comment` CLI,
  MCP `comment_task` tool, Python API direct calls.

### Provenance

PR #196 + #197. Lead a2a `0ab1d9fd` (ci-result ordering coordination
with dev) + `1e8e33d0` (card-message feedback channel — Phase 6
extends to active routing). Both follow the same loose-coupling
pattern: todo = producer, SAC = consumer, no cross-package import.

## [0.7.20] - 2026-06-14 — 🎯 TRACK 2 dashboard mission COMPLETE (6/6 surfaces)

Closes the operator-mandated fleet-dashboard mission. The board at
:8051 is now the ONE screen the operator watches: tasks (existing)
+ CI status + host geometry + agent mesh + ACL + timing telemetry
+ chat. All six surfaces honor the same architectural principles:
fail-loud / registry-sourced / no hardcoded proper nouns / no mocks.

### Added

- **Phase 6 — Chat surface** (PR #194). Operator↔agent thread view
  over the existing per-card `comments[]` substrate. New
  `_django/handlers/chat.py` with `GET /chat/<card_id>` (returns
  comments + title) and `POST /chat/<card_id>` (validates
  non-empty text, calls `_store.comment_task`, returns the appended
  comment). 404 on unknown card_id; 400 on empty text; 405 on
  PUT/DELETE. New `ChatPanel.tsx` mounts inside the existing
  NodeDetailPanel drawer — bubble layout with author-color hash,
  30s auto-poll for new comments, fail-loud error pill + toast on
  write failure. Author default from `SCITEX_TODO_AGENT` env. 45
  new mock-free tests (16 backend + 8 JS predicate + 21 CSS/wiring).
  TODOs: RW-perm gating, WebSocket push, markdown rendering,
  @-mentions / threading / reactions / attachments.

### Mission complete — 6/6 TRACK-2 surfaces

| # | Surface              | PR    | Adapter source                          |
|---|----------------------|-------|------------------------------------------|
| 1 | CI status pills      | #178  | `gh api repos/.../check-runs`            |
| 2 | Host geometry        | #185  | `sac host list --json`                   |
| 3 | Agent mesh + ACL     | #189  | `sac a2a list --json` + `... grants`     |
| 4 | Timing backend       | #191  | card `_log_meta` timestamps              |
| 5 | Timing chart UI      | #192  | `/fleet/timing`                          |
| 6 | Chat surface         | #194  | per-card `comments[]`                    |

The board reads from authoritative registries; it never duplicates
state. Every adapter raises `FleetAdapterError` on missing data;
the UI surfaces a visible error state instead of silently degrading.

### Provenance

PR #194. Lead a2a `74db4f2d` + `10afa799` (vision); operator's
"one screen, watch the whole fleet, self-improvement" intent
realized end-to-end.

## [0.7.19] - 2026-06-14 — Phase 4 + 5: timing telemetry (backend + chart UI)

5 / 6 TRACK-2 dashboard surfaces shipped. Last remaining: Phase 6
chat. Operator's "record what took how long → self-improvement"
intent now visible end-to-end on the board.

### Added

- **Phase 4 — Timing telemetry backend** (PR #191). New
  `_django/handlers/fleet/timing.py`: pure
  `compute_timing(tasks, *, window_days=30)` derives three durations
  per task (`created_to_started` / `started_to_done` /
  `created_to_done`) from existing card timestamps (no state
  duplication), then aggregates per agent / project / group with
  median + p95 + median-queue. `_django/handlers/fleet/timing_view.py`
  exposes `GET /fleet/timing?window_days=N` (200 OK; 405 on POST;
  500 on store-read failure — fail-loud). `<ungrouped>` sentinel for
  null groups; `n_tasks_missing_timestamps` diagnostic surfaces
  done cards with broken `_log_meta`. 23 mock-free tests (16 pure +
  7 view). Phase 4.b gaps flagged inline: a2a-log scraping for
  per-turn agent durations, histograms / CDF arrays, p50/p75/p99
  knobs.
- **Phase 5 — Timing chart UI** (PR #192). New
  `FleetTimingPanel.tsx`: collapsed `📊 timing` pill in the STATUS
  toolbar group; click to expand. WINDOW (7d/30d/90d) + GROUP-BY
  (Agent/Project/Group) controls + inline SVG bar chart, one row
  per key with median + p95 bars. Sort by p95 desc so the
  bottleneck rides at the top. Tooltip carries `n_tasks_done` +
  `median_queue_s`. Footer carries `n_tasks_in_window` +
  `n_tasks_missing_timestamps`. 60s poll. Fail-loud on adapter
  error. 17 mock-free CSS/helper tests.

### Provenance

PR #191 + #192. Lead a2a `74db4f2d` + `10afa799`. Subagent execution
on both phases; Phase 5 subagent terminated mid-flight + the parent
agent finished the commit/push/PR.

## [0.7.18] - 2026-06-14 — Phase 3: agent mesh + ACL graph

### Added

- **Phase 3 — Agent mesh + ACL graph** (PR #189). New
  `_django/handlers/fleet/sac_mesh.py` adapter reads `sac a2a list
  --json` (peer registry) + `sac a2a grants --json` (comms_grants
  ACL). New `/fleet/mesh` Django endpoint. New `FleetMeshPanel.tsx`
  with an inline-SVG radial graph: nodes = agents, edges = grants,
  allow=`--status-success` green, deny=`--status-error` muted red.
  Mounted in the toolbar STATUS group. 26 new mock-free tests (10
  adapter + 4 view + 12 FE CSS/helper) + 119-test broader fleet
  suite green.
- **Phase 3.b TODOs captured inline** (will land in a follow-up):
  - `comms_blocks` has no listing CLI yet → deny edges not wired
    (the shape already supports `allow: false`).
  - No heartbeat-freshness threshold in `sac a2a list` → status is
    `online` / `unknown`, never `offline`.
  - `state.db` path not surfaced → `config_path` returns null.

### Provenance

PR #189. Lead a2a `74db4f2d`. 3/6 TRACK-2 dashboard surfaces shipped
(CI / hosts / mesh). Remaining: timing telemetry + chat surface.

## [0.7.17] - 2026-06-14 — Hook-consumer contract + Time View + Phase 2 hosts

Wave 2 of the fleet-dashboard mission. The hook-consumer contract
is the operator-mandated "green static record pipe" — SAC's
push-hook + dev's merge-Action will call scitex-todo's API to
auto-record progress/DONE on the board.

### Added — Hook-consumer (loose-coupling contract)

- **`scitex_todo.hooks` entry-point group** (PR #187, lead a2a
  `6fff33d6` + `fbffb879`, operator-mandated). External producers
  register a plugin callable under this group:
  `def on_event(event: dict) -> None`.
- **Three converging wire surfaces** (producers pick one):
  - **HTTP**: `POST /hooks/push`, `POST /hooks/done`. Idempotent.
    405 on GET, 400 on bad shape / kind-mismatch.
  - **CLI**: `scitex-todo hook push --payload <FILE|->` /
    `scitex-todo hook done --payload <FILE|->`.
  - **Python**: `from scitex_todo._hooks import dispatch_event`.
- **Canonical event payloads**:
  - push: `{kind, repo, branch, commit_sha, author?, message?,
    card_ids?}`
  - done: `{kind, repo, pr_number, pr_url, author?, merged_at?,
    card_ids?}`
- **Built-in handlers run BEFORE plugins**:
  - push → idempotent comment-append (dedupe via full commit_sha
    substring match).
  - done → idempotent `pr_url` stamp + `status=done` flip (noop if
    already done with matching pr_url).
- **Plugin failures are caught + logged** — one bad plugin can NOT
  silently break the board's own record-keeping.
- 29 mock-free tests (validator fail-loud + handler idempotency +
  HTTP contract).

### Added — Dashboard surfaces

- **Time View** (PR #186, operator-direct via lead a2a `d0f7a0e3`).
  Live SVG raster timeline as the 5th LAYOUT toggle. Horizontal
  axis = TIME (1h/6h/24h/7d window); lanes by agent OR group; bars
  fade-out on done; depends_on/blocks edges drawn as connecting
  lines; click-through to the existing NodeDetailPanel. 30s poll.
  17 backend + 15 frontend mock-free tests. Pan/zoom/WebSocket are
  flagged TODOs for future iterations.
- **Phase 2 — Host geometry** (PR #185, lead a2a `74db4f2d` +
  `10afa799`). `sac host list --json` adapter + `/fleet/hosts`
  endpoint + `FleetHostsPanel.tsx` mounted next to the CI pills.
  Fail-loud on missing `sac` CLI (FleetAdapterError → HTTP 500).
  Phase 2.b cpu/mem/SLURM enrichment landing site marked with
  `TODO(phase-2.b)`. 14 + 47 = 61 tests green.

### Provenance

PR #185 + #186 + #187. Lead a2a `74db4f2d` (vision) + `6fff33d6`
(hook-consumer mandate) + `d0f7a0e3` (Time View). Multiplier-#3
dogfooded on every PR.

## [0.7.16] - 2026-06-14 — TRACK 1 COMPLETE: parallelism-engine dispatch backbone

Completes the **dependency-aware ticket** track the operator/lead
vision (a2a `74db4f2d` + `10afa799`) named as the parallelism
engine. Combined with the v0.7.15 TRACK-2 Phase-1 CI pills, this
release closes Wave 1 of the fleet-dashboard mission.

### Added — TRACK 1 (parallelism-engine backbone)

- **T1.2 — `runnable_tasks()` API + `scitex-todo runnable` CLI**
  (PR #181). Batch runnable view (sister to `next_task`'s single
  pick) respecting `depends_on` + reverse-`blocks` closure +
  optional agent + group filter. Diagnostic counts
  (`candidate_count`, `blocked_by_deps_count`) let the dispatcher
  distinguish "queue empty" from "queue blocked." 22 mock-free
  tests.
- **T1.3 — `blocked_tasks()` inverse view + `scitex-todo blocked`
  CLI** (PR #182). For every NOT-runnable task, name WHY
  (`explicit-blocker` / `manual-block` / `depends-on` /
  `reverse-blocks`) + the chain of upstream ids. `by_reason`
  histogram for observability. 20 mock-free tests.
- **T1.4 — `/runnable` + `/blocked-batch` Django endpoints**
  (PR #183). JSON HTTP twins of the CLI verbs so the dispatcher
  consumes the data over HTTP. POST returns 405; fail-loud on
  load_tasks errors. 12 mock-free RequestFactory tests.

TRACK 1 wave list:
- T1.1 #179 (group field, in v0.7.15)
- T1.2 #181 (runnable API + CLI)
- T1.3 #182 (blocked inverse + CLI)
- T1.4 #183 (HTTP endpoints)

The lead-side dispatcher can now drive parallel work across agents
and groups end-to-end via either CLI or HTTP.

### Provenance

PR #181 + #182 + #183. Lead a2a `74db4f2d`. TRACK 2 (fleet
dashboard) continues in parallel — Phase 2 host geometry queued.

## [0.7.15] - 2026-06-14 — Fleet-dashboard Phase 1 (CI pills) + TRACK-1 `group` field

Operator vision (lead a2a `74db4f2d` + `10afa799`): scitex-todo
becomes the ONE fleet dashboard + dependency-aware ticket backbone.
This is wave 1 of two parallel tracks.

### Added — TRACK 2 (Fleet Dashboard)

- **Phase 1 — CI-status pills + Phase-0 registry-reader harness**
  (PR #178). New `_django/handlers/fleet/` package: `FleetAdapterError`
  (fail-loud on missing data, no silent fallback), `fleet_config_load`
  (reads `~/.scitex/todo/dashboard.yaml` or env
  `SCITEX_TODO_FLEET_CI_REPOS=owner/name,...`; NO hardcoded slugs),
  `gh_ci.fetch_repo_ci_status` (`gh repo view` for default branch +
  `gh api .../check-runs`). New `/fleet/ci-status` Django endpoint
  with per-repo error trap (200 with `error` field per bad repo, 500
  on malformed config). Front-end `FleetCiPills.tsx` polls every 30s,
  per-repo green/red/amber/grey pill bound to scitex-ui status
  tokens. 33 fleet tests + full 277-task Django suite green. Pattern
  established for Phases 2-6 (hosts / mesh / timing / chart / chat).

### Added — TRACK 1 (Parallelism-engine backbone)

- **T1.1 — `group` field on Task** (PR #179, lead a2a `74db4f2d`).
  Optional `group: str | None` on the Task dataclass. The
  parallelism-engine dispatcher will ask
  `runnable(group=<G>)` so independent (dep-free) tasks within a
  group run concurrently per the operator's model. Free-form
  non-empty string; absent = ungrouped. Validator extends the
  existing scope/assignee non-empty-string loop. New `--group` CLI
  flag on `add` + `update` (empty string clears). Distinct from
  `_groups.py:Group` (project-cluster viewer aggregation). 15
  mock-free tests pin the dataclass shape, validator, Python API,
  and CLI wiring. Follow-up chain: T1.2 (`runnable()` API + CLI),
  T1.3 (`scitex-todo blocked` introspection), T1.4 (`/runnable` +
  `/blocked-batch` endpoints).

### Architectural principles enforced

- **fail-loud / no-silent-fallback** — adapters RAISE on missing
  data; no stubs.
- **registry-sourced** — read from authoritative GitHub via `gh`;
  scitex-todo doesn't duplicate state.
- **NO hardcoded proper nouns** — watched-repo list is fully
  config-driven; no `["scitex-todo","scitex-dev",...]` literals in
  source.

### Provenance

PR #178 + #179. Lead a2a `74db4f2d` + `10afa799` (refined brief
+ Q&A). Phase-1 subagent execution; T1.1 main-thread.

## [0.7.14] - 2026-06-13 — CLI: bare `board` hard-errors (noun-verb enforcement)

### Changed (BREAKING)

- **`scitex-todo board` (no verb) HARD-ERRORS** (PR #176, op TG 13316
  via lead a2a `c36b0d1e`). PR #139 (v0.7.6) had kept it as a
  deprecation-warn-and-forward to `board start`, but that path HID
  the noun-verb violation from audit tools. Bare invocation now exits
  2 + emits a redirect message naming the canonical replacements:

  ```
  ERROR: `scitex-todo board` (no verb) is no longer supported.
  Operator directive TG 13316 — noun-verb CLI convention. Use:
    scitex-todo board start [--port N] [--no-browser]
    scitex-todo board stop
    scitex-todo board restart
    scitex-todo board status
  ```

  In-tree call site migrated: `_jobs_provider.py`'s
  `scitex-todo.dashboard` JobSpec command now reads
  `scitex-todo board start --port 8051`. External call sites (the
  host systemd unit `scitex-todo.dashboard.service` ExecStart + any
  launcher script) need the same migration on the host side. Until
  they do, restarting them will exit 2 + log the redirect — which IS
  the operator's intended forcing function, but coordinate with the
  host-side deploy to avoid disruption.

  14 mock-free CliRunner tests pin the contract (exit code 2,
  redirect message, no forwarding, flags-don't-bypass).

## [0.7.13] - 2026-06-13 — Board UI wave-2: header declutter + Calendar view (4th LAYOUT)

Completes the operator-direct board UI overhaul (lead a2a `d1af161e`
+ `510a58d4`). With the v0.7.12 theme + Table-filter fixes, the
operator's board screenshot complaints (white scrollbar, white
dropdowns, cluttered Table view, cluttered toolbar) are end-to-end
addressed; new Calendar view satisfies op TG 13295.

### Added

- **Toolbar declutter** (PR #173) — the board's overcrowded toolbar
  is reorganized into 3 logical groups + a primary-action zone:
  `view` (LAYOUT toggle / Sort / Group), `search` (Search bar +
  Filters), `status` ("N new" badge / Reload / hide-project), and a
  brand-accent `+Add Task` primary action separated by a divider.
  Responsive wrap at ≤780px. All scitex-ui token-bound (no
  hardcoded colors). Behavior preserved — every original control id
  survives so existing onclick / event handlers / localStorage keys
  keep working. 31 mock-free tests pin the CSS contract + structural
  presence.
- **Calendar view — 4th LAYOUT** (PR #174, op TG 13295) — month grid
  (7×6) with task chips placed by `deadline_next` →
  `deadline` → `last_activity` precedence (pure-function helper
  `taskDateForCalendar` in `calendarDate.ts` for testability). Today
  gets accent ring, past days muted, weekends subtle bg-shift, Today
  pill snaps back to current month, prev/next nav. Chips click-thru
  to the existing NodeDetailPanel drawer. Token-bound; deferrals
  flagged for future PRs (drag-reschedule, week/day view, recurring
  expansion beyond server-provided `deadline_next`, inline edit
  on cell click, full a11y grid contract). 9 mock-free tests pin
  the date-assignment logic + grid generation.

### Provenance

PR #173 + #174 from the operator's design-intent directive +
TG 13295. Subagent-pair execution; both subagents dogfooded
multiplier-#3 (recorded their cards with `--pr-url` post-merge).

## [0.7.12] - 2026-06-13 — Board UI: themed scrollbar+dropdowns + Table-view structural filter

Two operator-direct UI fixes (lead a2a `510a58d4`, op TG screenshot
of the board's white scrollbar + un-themed dropdowns + cluttered
Table view). Header declutter + Calendar view follow in v0.7.13.

### Fixed

- **Themed scrollbar + `<select>`/`<option>` dropdowns** (PR #170) —
  the board's white-in-dark-mode scrollbar and OS-default white
  dropdowns now bind to scitex-ui shell tokens (`var(--col-bg)` /
  `var(--text)` / `var(--border)` / `var(--purple)`). Two layers:
  global `.stx-todo-board, *` fallback in `board.css` + a new
  `board_v3/00-theme-scrollbar-select.css` loaded FIRST in the
  template. 13 CSS-contract tests pin the rule set.

### Added

- **Table view: hide structural cards by default** (PR #171) — the
  `kind=status` quality-axis rows (8 q-*) and `kind=goal` umbrella
  rows (proj-clew / proj-todo / pool-* / ywatanabe-operator-anchor)
  are FILTERED OUT of the Table view by default; a "Show structural
  cards" checkbox in the toolbar flips them back on. Graph + Column
  views are unchanged — they keep showing every card per the
  existing dependency-graph contract. New `tableFilter.ts` helper
  exposes `STRUCTURAL_KINDS` + `isVisibleRow` so the filter is
  pure-function-testable. 5 new TS+Python tests.

### Provenance

PR #170 + #171 from the lead's a2a `d1af161e` (board UI overhaul)
triage. Subagent-pair execution model — one PR each, isolated
worktrees, multiplier-#3 dogfooded (both subagents recorded their
card with `--pr-url` post-merge).

## [0.7.11] - 2026-06-13 — Skill mandate: never hand-edit tasks.yaml

### Added

- **Canonical skill mandate: NEVER hand-edit `tasks.yaml`** (PR #168,
  lead a2a `02c8a4ae`). Folds into the bundled `scitex-todo` skill
  alongside the SSoT MANDATE and the multiplier-#3 PR-merge recording
  mandate. The 2026-06-13 corruption episode traced to a hand-edit
  bypassing the API. Rule: always use the CLI / MCP / Python API; the
  flock + atomic-rename + post-dump-validate path is the only safe
  write. Emergency-repair exception documented (already-broken file
  with backup-first / parse-verify-after / report-to-lead protocol).
  Propagates to every agent's required_skills via `scitex-todo skills
  propagate` (PR #161 mechanism), so every fleet agent reads it on
  boot. 4 mock-free file-content tests pin the load-bearing phrases.

## [0.7.10] - 2026-06-13 — Durable writer safety + CLI: --blocker '' clear

### Fixed

- **Writer: post-dump round-trip validation** (PR #166, lead a2a
  `d5809cd3`) — after the 2026-06-13 corruption episode where
  `~/.scitex/todo/tasks.yaml` was found truncated mid-string at line
  ~2784 and recovered by hand. Audit: the existing writer already had
  pre-write `_validate_tasks`, atomic-rename (tmp + fsync +
  `os.replace`), `fcntl.flock`, and tmp-cleanup-on-error. NEW LAYER:
  before `os.replace`, the writer now REPARSES the just-dumped tmp
  file from disk via ruamel and verifies both (a) it parses cleanly
  and (b) the reparsed task count matches the in-memory count. Either
  failure aborts with a `RuntimeError` and the canonical file is left
  untouched — never promote suspect bytes into the SSoT. 7 mock-free
  subprocess-based tests pin the contract (kill-mid-dump leaves
  canonical byte-identical; failed pre-write doesn't create a
  canonical file).
- **CLI: `--blocker ''`/`'none'` clears the field** (PR #165). Dev
  a2a (via lead `f5a54f85`): the strict `_BLOCKER_CHOICE` rejected
  `""` and `"none"` at parse time so there was no CLI form for
  clearing a card's blocker — `campaign-*` cards needing to flip a
  blocker off couldn't be closed from the CLI. New
  `_BlockerOrClearParamType` on the UPDATE verb honours both
  sentinels; ADD verb keeps the strict closed enum (you can't clear
  on insert). 7 mock-free CliRunner tests.

### Provenance

PR #166 + #165. Lead a2a `d5809cd3` + `f5a54f85`. The writer-safety
fix is the structural fix for SSoT-write hazard; the CLI clear-gap
fix closes the dogfooded blocker that surfaced from dev's reconcile.

## [0.7.9] - 2026-06-13 — Fleet-adoption multiplier #3: PR-merge recording mandate

Closes the **board-recording gap** surfaced by the 2026-06-13 reconciliation
pass (199 PRs merged in 24h vs ~5 board completions — structural, not a
hygiene problem). Adds a LOAD-BEARING mandate to the canonical scitex-todo
skill that propagates to every fleet agent via `skills propagate` (#161).

### Added

- **PR-merge recording mandate** (PR #163) — new `## ⚑ MANDATE — record
  evidence at PR-merge / issue-close time` section in `SKILL.md` + a
  sister leaf `60_pr-merge-recording-mandate.md` with the CLI/API/MCP
  verb table, no-PR alternative, bulk catch-up verb (`sync-github
  --since <date> -y`), anti-pattern list, and provenance. Hard rule:
  `scitex-todo done <card-id> --pr-url <merged-PR-URL>` IMMEDIATELY at
  PR-merge time; bare `done` without `--pr-url` is the recording-gap.
  8 mock-free file-content tests pin the load-bearing phrases so they
  can't drift silently. Lead a2a `0cdca03a` approved as fleet-adoption
  multiplier #3, sister to #160 (TaskCreate-redirect hook) and #161
  (skill propagation manifest).

### Provenance

PR #163 (`feat/skill-pr-url-mandate`). Diagnostic source:
`/work/GITIGNORED/RECONCILE_TRACE.json` — the 2026-06-13 reconciliation
pass.

## [0.7.8] - 2026-06-13 — Fleet-adoption multipliers (PreToolUse hook + skill propagation)

Ships the two **fleet-adoption multipliers** so every other agent in the
fleet uses scitex-todo correctly without per-agent buy-in. Lead a2a
`1b5c3b4d` prioritized both over the UX cards because they move the
operator's single-shared-store doctrine forward across the WHOLE fleet
in one bump.

### Added

- **Bundled PreToolUse hook** (PR #160): a bash script in the skill
  bundle (`_skills/scitex-todo/hooks/pre-tool-use/`) that any agent
  drops into `~/.claude/hooks/pre-tool-use/` and immediately gets
  the redirect. Intercepts Claude Code's built-in `TaskCreate`,
  `TaskUpdate`, `TaskList` — exits non-zero with a clear stderr
  redirect to the equivalent scitex-todo CLI verb. ENFORCES the
  doctrine, not just warns. Opt-out: `CC_ALLOW_CLAUDE_TASKLIST=1`
  for rare legit uses. 8 mock-free subprocess tests.
- **Canonical skill manifest + `scitex-todo skills propagate`**
  (PR #161): `_skills/manifest.yaml` lists which scitex-todo skill
  IDs every fleet agent should require. `scitex-todo skills
  propagate --agents-dir <DIR>` walks a tree of agent-container
  `spec.yaml` files and idempotently appends those IDs to each
  agent's `required_skills` list (ruamel.yaml round-trip preserves
  comments; SciTeX audit-cli §2 `--dry-run` + `-y`). Supports both
  `metadata.labels.skills` (v3) and `spec.required_skills` (older)
  shapes. 16 mock-free CliRunner tests.
- **Runbook leaf §22 — fleet-wide skill propagation**: documents
  the canonical manifest path + the agent-container integration.

### Provenance

PR #160 + #161 — fleet-adoption multipliers off the lead a2a
`1b5c3b4d` triage. Co-located with the existing P3a chain
(PR #155 / #156 / #158 / #159) so a single PyPI bump unlocks the
WHOLE single-shared-store + agent-redirect story for agent-container.

## [0.7.7] - 2026-06-13 — P3a fleet host-store wire-up + board-reconciliation verbs

Cuts the **P3a throughput unlock** (host scitex-todo store reachable from
every containerized agent, write-safety via flock-scoped RMW) into a
pull-able PyPI release so agent-container can bake the wire into
`to_home/.mcp.json`. agent-container a2a `e330b084` confirmed
`/home/agent/.scitex/todo` bind is fleet-wide; dev a2a
`dd971b57` + `932ea837` independently verified the host's 632-task
corpus is visible from their container. Also rolls up the
board-reconciliation verb sweep landed over 2026-06-13.

### Added

- **`scitex-todo mcp install [--apply] --env-tasks-path <abs/path>`**
  (PR #158) — when set, pins `SCITEX_TODO_TASKS` in the generated
  `.mcp.json` entry's `env` block. Belt-and-suspenders for the
  bind-mount-based host-store resolution; makes the wire-up
  self-documenting in the generated config. Operator P3a, lead a2a
  `a579358e` + `d7789963`. agent-container's one-liner:
  `scitex-todo mcp install --apply --to to_home/.mcp.json --env-tasks-path /home/agent/.scitex/todo/tasks.yaml -y`.
- **`scitex-todo mcp install --apply`** (PR #155) — idempotent
  `.mcp.json` merge; the foundation #158 builds on. P3a fleet
  enablement.
- **`scitex-todo stale-list`** (PR #157) — terminal twin of the
  board's `🧹 Stale` panel + `/stale` HTTP endpoint. Lets agents
  reconcile from the CLI without opening the board.
- **`/stale` + `/archive` board endpoints + `🧹 Stale` layout +
  per-row Archive button** (PR #153 backend + #154 frontend) —
  recurring stale-review surface; 128 / ~218 candidate cards
  flagged for operator review at landing.
- **`scitex-todo close <id> --reason ...`** (PR #151) — close-stale-
  with-reason verb (board-reconciliation gap fix); writes
  `status=deferred` + a `[CLOSED]` activity comment.
- **`scitex-todo comment <id> <text>`** (PR #144) — CLI wrapping
  `_store.comment_task` (the PR #64 replacement).
- **Per-row multi-select + bulk status change on the board**
  (PR #150) — PR(h) Stage 1.
- **`kind=status` axis** (PR #146) — non-actionable quality-tracking
  cards; renders distinct from `kind=task` on the board.
- **Activity-bucket badge** (PR #148) — color cards by
  `last_activity` recency (fresh / warm / stale); pairs with PR #122
  backend decay.
- **Directory-card scanner + plan CLI** (PR #142) — PR-D Stage 1,
  operator-direct.

### Docs

- **Runbook §7.5 — fleet MCP enablement via `mcp install --apply`**
  (PR #156) — the P3a chain end-to-end recipe.
- **Board-reconciliation runbook — canonical verbs for fleet sweep**
  (PR #152) — covers the new close / comment / stale-list verbs.
- **Skill refresh — comment verb + kind=status + SSoT write-here**
  (PR #149) — keeps the bundled agent skill in lock-step with the
  current CLI.
- **Container/host tasks.yaml divergence audit** (PR #143) — the
  audit that became the P3a brief.

### Provenance

PR #158 (`feat/mcp-install-apply-env-tasks-path`), lead a2a
`a579358e` + `d7789963` + `f9c78d48` (the write-safety
follow-up — model: single shared file + flock-scoped RMW). Co-tested
with proj-scitex-dev (container end-to-end) and
proj-scitex-agent-container (fleet bind-mount confirmation,
canonical path lock-in).

## [0.7.6] - 2026-06-13 — board lifecycle verbs (start/stop/restart/status + pidfile)

Operator-direct TG12949/12950/12951 (via lead a2a `b5726672`).
`scitex-todo board` was a bare NOUN that directly LAUNCHED — CLI
noun-verb violation, AND no clean way to restart after a card/source
change (`port already in use` was the trap).

### Added

- **`scitex-todo board <verb>` lifecycle CLI** (PR #139):
  - `board start [--port --tasks --no-browser] [--dry-run] [-y]` —
    foreground launch, writes `~/.scitex/todo/board.pid` (env-
    overridable via `SCITEX_TODO_BOARD_PIDFILE`).
  - `board stop [--timeout] [--dry-run] [-y]` — SIGTERM the pidfile
    PID; escalate to SIGKILL on timeout.
  - `board restart [--port --tasks --no-browser] [--dry-run] [-y]` —
    stop + start. THIS is the operator's "reload after a source
    change" shape.
  - `board status [--json]` — one-line / JSON read of the pidfile +
    liveness probe.
- SciTeX audit-cli §2 (mutating-verb `--dry-run` + `--yes/-y`) and §4
  (concrete Example blocks) compliance landed in the same PR.

### Changed

- Bare `scitex-todo board` (no verb) stays back-compat: forwards to
  `board start` with a stderr DEPRECATION line. Operator's muscle
  memory survives; the alias will be removed in a future minor bump.

### Provenance

PR #139 (`feat/board-lifecycle-verbs`), lead a2a `b5726672`,
operator-direct TG12949/12950/12951.

## [0.7.5] - 2026-06-13 — per-project lane UNION + board UX rescue + /graph perf

Three operator-visible improvements landed via the overnight
Stage 0-1 chain:

### Added

- **`services.get_board()` UNIONS the global store + every per-project
  lane** (`~/proj/*/.scitex/todo/tasks.yaml`, comma-sep override via
  `SCITEX_TODO_LANE_GLOBS`). Skill 30's two-tier rollup is finally
  delivered; the operator's hand-curated `nv-lessons` + 31 other
  neurovista cards become visible on the board (lead a2a
  `1ceec0ef` / `40c0a42d`). Collision policy: project-lane wins on
  id, logged at WARNING. Malformed lane is SKIPPED + logged — the
  board renders the rest. (PR #137)
- **Empty-state banner on the board** when active filters narrow the
  result set to 0 cards (operator TG12911 — "filtering by nv-lessons
  does NOT work at all"). The banner offers a one-click "Clear all
  filters" so a 0-match state can't read as a broken filter. (PR #135)
- **mtime-keyed in-process cache on `/graph` payload** — skips the
  full `_build_graph` rebuild (mermaid + nodes + edges + fleet +
  groups) on cache hits, ~50-100 ms saved per /graph request on a
  500-task store. Cache invalidates on any source mtime change
  (PR #136, plays naturally with the new lane-union mtime = MAX).

### Internal

- `BoardState.lane_paths` exposes the successfully-consumed per-project
  lanes so the FE / tests / future indexer can see what was unioned.
- Suite-wide test isolation: `tests/scitex_todo/conftest.py` autouse
  fixture pins `SCITEX_TODO_LANE_GLOBS=""` by default so existing
  fixture-pure tests don't pick up the test runner's host lanes.

### Provenance

PRs #135, #136, #137. Lead a2a `aa02fb0e` (Stage 2 design ACK) +
`1ceec0ef` / `40c0a42d` (lane-union ACK). YAML SSoT invariant
preserved throughout: read-side union only, no writes.

## [0.7.4] - 2026-06-12 — `_push.deliver` semantics: 30 s timeout + dispatched-on-read-timeout

Third (and likely last) cron-pilot hotfix. The 0.7.3 fix made the
receiver accept the body, but the client gave up too early: SAC's
`/v1/turn` runs the agent turn synchronously (up to ~120 s), and the
5 s client cap aborted before any turn could land in
`session.jsonl`. Probed `/v1/turn` for a fast-ack flag —
`wait=false`/`dispatch_only=true`/`async=true` all reject — so the
pragmatic stopgap (lead a2a `0b59485f`) is to give the client more
time AND treat the client-side read-timeout as "request was already
fully sent, receiver is mid-turn = dispatched success" so one slow
turn can't fail the nudge batch.

### Fixed

- **`DEFAULT_TIMEOUT_S` 5.0 → 30.0**, env-overridable via
  `SCITEX_TODO_PUSH_TIMEOUT_S`. Reflects the receiver's actual
  budget so short ack-style turns complete cleanly.
- **Read-timeout treated as `DISPATCHED` success**
  (`ok=True, reason="dispatched"`), not `transport-error`. By the
  time the client read-timeout fires, the request body has long
  since been fully transmitted; treating it as success stops one
  slow turn from failing the whole `*/10` nudge batch. Connection-
  refused / DNS / SSL handshake errors still surface as
  `transport-error`.

### Tests

Real localhost `http.server` round-trips (no mocks, STX-NM / PA-306):

- `test_read_timeout_treated_as_dispatched_ok` — handler accepts the
  request body then sleeps past the client timeout; pre-fix this
  returned `reason=transport-error`, post-fix it returns
  `reason=dispatched`.
- `test_default_timeout_env_override` — `SCITEX_TODO_PUSH_TIMEOUT_S`
  reflected at call-time.
- `test_default_timeout_falls_back_to_constant_when_env_unset` — bare
  case yields `DEFAULT_TIMEOUT_S`.

### Followup (out of scope)

Long-term: sac-listen should grow a real fast-ack endpoint
(e.g. `POST /v1/turn/dispatch` returning 202 + an async session id).
The pragmatic stopgap here can then be reverted.

### Provenance

PR #123 (`fix/push-timeout-env`), lead a2a `0b59485f` (root-fix
directive: not just a bigger timeout but DISPATCHED-success
semantics), proj-scitex-todo overnight mission.

## [0.7.3] - 2026-06-12 — `_push.deliver` payload aliases `text` to `body` (SAC /v1/turn unblocked)

Second hotfix found via the P3a(c) cron pilot. The 0.7.2 fix made the
cron survive its tick, but the POST then failed at the *receiver*:
SAC's `/v1/turn` (and `claude-code-telegrammer`'s TURN_URL) require a
`text=<msg>` field, while `_push.deliver` only sent `body=<msg>`. The
receiver returned `HTTP 400 "missing or empty 'text' field"`, so the
whole nudge chain still produced zero delivered turns.

### Fixed

- **`_push.deliver` now sends BOTH `text` and `body`** in the payload.
  `text` satisfies SAC + the telegrammer; `body` stays for back-compat
  with any pre-existing consumer keying off scitex-todo's historical
  name.

### Tests

Real localhost `http.server` round-trips (no mocks, STX-NM / PA-306):

- `test_post_carries_text_field_aliased_to_body` — the payload
  round-trip pins both fields.
- `test_succeeds_against_text_strict_receiver` — end-to-end against a
  stdlib `HTTPServer` that mimics SAC's 400-on-missing-text
  validation; pre-fix this returned `reason=http-error`, post-fix
  it returns `reason=delivered`.

### Provenance

PR #120 (`fix/push-text-alias`), lead a2a `8afe659e` (SPLIT directive
from the decay PR so the delivery fix ships first), proj-scitex-todo
overnight mission.

## [0.7.2] - 2026-06-12 — coerce naive ISO timestamps to UTC-aware (unblocks `--notify` cron)

Hotfix for the 10-min structural-nudge cron shipped in 0.7.1. The
P3a(c) cron pilot caught a `TypeError: can't subtract offset-naive
and offset-aware datetimes` raised by `_throughput._hours_since` on
the first `tasks.yaml` row whose `last_activity` was serialized
without a timezone suffix (e.g. `"2026-06-08T00:42:30"` vs.
`"2026-06-08T00:42:30Z"`). The cron then died silently every tick
BEFORE any POST fired, so no agent ever received a structural nudge.

### Fixed

- **`_throughput._parse_iso` always returns UTC-aware.** Naive ISO
  strings are coerced to UTC — the canonical assumption for
  `tasks.yaml` timestamps. One offending row no longer kills the
  entire `--notify` / `--nudge-quiet` sweep.

### Tests

- `TestNotifyBody::test_naive_last_activity_does_not_crash` —
  composes a notify body for a task whose `last_activity` lacks a
  timezone suffix.
- `TestParseIso::test_naive_string_coerces_to_utc_aware` — direct
  unit check on the helper.

### Provenance

PR #118 (`fix/parse-iso-utc-coerce`), lead-ACK a2a `cfbade6b` /
`f556b755`, proj-scitex-todo overnight mission.

## [0.7.1] - 2026-06-12 — 10-min structural-nudge cron + `--nudge-quiet` flag

Operator standing direction (lead a2a `19d575415a` + revision
`9e710ab074ef4bf3a615be41793e0c51`, 2026-06-12): the structural
feedback loop must push per-agent nudges every 10 minutes, not on
manual lead intervention. The 10-min threshold is the operator's
"silence + in_progress = escalation" rule from TG12600.

### Added

- **New `--nudge-quiet` flag on `scitex-todo print-stats`.** Per-agent
  sweep: if any open `in_progress` task hasn't been touched in
  `SCITEX_TODO_NUDGE_QUIET_MIN` (default 10) minutes, push a
  quiet-nudge body via `_push.deliver(kind="quiet-nudge")` — the
  same self-contained HTTP push wire 0.7.0 introduced. Composes the
  full per-agent open list (RUNNABLE first, BLOCKED after) so the
  recipient sees the full picture, not just the stalled row.
- **`scitex-todo.notify` JobSpec** in `_jobs_provider.provide_jobs`.
  `kind="oneshot"` + `schedule="*:0/10"` → systemd runs it every 10
  minutes via the existing `scitex-dev ecosystem up` federation.
  Command: `scitex-todo print-stats --by agent --notify --nudge-quiet`.
  Pairs with the v0.7.0 UI nudge button: the cron is the STRUCTURAL
  feedback path; the button is the manual override.

### Out of scope

- Stdio MCP channel server + board-event poller (operator TG12618
  long-term plan) — tracked as PR (j) in the queue.

## [0.7.0] - 2026-06-12 — Self-contained push channel + nudge button + comment relay

Operator standing direction (lead a2a `f16b0d2a` + `9e710ab0` +
`8e51b1e0` + `ffc6629c80e4462a8401fb7e4ebb7240`, 2026-06-12,
operator TG12608 / TG12611 / TG12617): scitex-todo must NOT depend on
the `sac` CLI for outbound notifications. The package owns its own
push delivery, the contract is HTTP (not Python imports), and silent
fallbacks are forbidden — failures must be loud-but-not-fatal so the
operator can fix the config without breaking the running board.

### Added — `src/scitex_todo/_push.py` (self-contained HTTP push wire)

- `deliver(agent, body, *, kind=..., task_id=..., store_path=...)` —
  resolves the agent's turn URL from `SCITEX_TODO_AGENT_TURN_URLS`
  (JSON map, canonical) or `SCITEX_TODO_TURN_URL_<AGENT_SLUG>` (per-
  agent fallback, same shape as claude-code-telegrammer's
  `TURN_URL`). POSTs a JSON envelope (`agent` / `kind` / `body` /
  `task_id` / `store_path` / `ts` / `source: scitex-todo`) and
  returns a structured result with `ok`, `wire`, `reason`,
  `status`. No `sac` dependency.
- `SCITEX_TODO_PUSH_DRY_RUN=1` short-circuits to stdout; useful in
  test / dev.
- `announce_missing_at_boot(tasks)` lists distinct agents in the
  store that have no turn URL configured; emits a single WARN log
  at board startup. Operator can iterate the config without a board
  restart per agent.

### Added — `POST /nudge` Django endpoint + UI button (PR g)

- New handler `_django/handlers/nudge.py` registered as the `nudge`
  endpoint. Body `{"agent": "<name>"}`. Composes the same per-agent
  body the `stats --notify` cron uses (`build_notify_body`) + an
  appended ACTION ask ("push or BLOCKED within 15 min"), then
  invokes `_push.deliver(agent, body, kind="nudge")`.
- Per-agent in-process cooldown (`COOLDOWN_SECONDS = 5 * 60`)
  matches the operator's spec; cooldown hit → HTTP 429 with the
  remaining seconds.
- UI: per-column `🔔` button (next to the existing `📌 pin` button).
  Click resolves the column's PRIMARY agent (modal agent among
  the column's tasks) and POSTs `/nudge`. Toast surfaces every
  result branch — success / no-turn-url-configured / http-error /
  cooldown-active / no-agent-attribution.

### Changed — Comment-relay hook on `POST /comment` (PR g)

- When a comment's `author != task.agent`, `handle_comment` invokes
  `_push.deliver(target, body, kind="comment-relay", task_id=...)`
  AFTER the write succeeds. Best-effort; relay failure does NOT fail
  the comment write. Relay outcome surfaces in the response so the
  UI can render a toast ("📨 relayed → <agent>" / failure marker).
- Comment-relay body invites the agent to reply via
  `scitex-todo comment <task-id>` (CLI) or `add_comment` / `comment_task`
  (MCP) — both surfaces are already available in v0.5.x.

### Changed — `print-stats --notify` migrated to `_push.deliver`

- `_cli/_stats.py::_push_notify` now calls `_push.deliver(agent,
  body, kind="notify")` instead of `subprocess.run(["sac",
  "agents", "send", ...])`. Same per-agent body as before; the wire
  swap is transparent to callers.

### Changed — Board boot announce (`board_v3_page`)

- Once per process, the board page logs a WARN listing the agents
  in the store with no turn URL configured. Single-shot via
  `_TURN_URL_ANNOUNCED` module flag.

### Tests

- `tests/scitex_todo/test__push.py` — 12 tests against a localhost
  `http.server` capture (no mocks, STX-NM / PA-306). Covers env
  resolution (JSON map + per-agent fallback + malformed JSON +
  missing), HTTP 200 / 4xx / transport-error, dry-run, and
  `announce_missing_at_boot`.

### Out of scope

- Dedicated stdio MCP channel + board-event poller mirroring
  claude-code-telegrammer's `~/proj/claude-code-telegrammer` shape —
  operator TG12618 long-term plan. Tracked as PR (j) in the queue.

## [0.6.0] - 2026-06-12 — `stats` CLI + WIP-validation gate + `sync-github` verb + `--notify` push

Operator standing direction via lead a2a `4b23ebc1` + `7489ac31` +
`6f24a752` + `5263c8d9` + `02b71bd0` + `130cc5ac` + `d99b8de6` +
`5acfbb5d` (2026-06-12): the fleet must measure its own creation vs
completion rate, push the per-agent numbers hourly so receivers
self-correct, hard-throttle add-task at 2× the agent's WIP limit, and
absorb GitHub merges back into the canonical board automatically.

### Added — `scitex-todo print-stats`

- New CLI: `scitex-todo stats [--by agent|project|host] [--since
  YYYY-MM-DD] [--format text|json] [--notify]`.
- Per-group rows: `name / open / stale / created / completed / delta
  / ratio / velocity_per_day`. Source = canonical `tasks.yaml`. The
  `created_at` field anchors the window; `last_activity` anchors the
  `done` projection; `in_progress` rows older than
  `SCITEX_TODO_STALE_HOURS` (default 24) count as `stale`.
- `--notify` (agent grouping only): for each agent, push a body via
  `sac agents send <agent> <body>` (stdout fallback when `sac`
  unavailable). Body layout: HEADER (counts + ratio) → RUNNABLE
  tasks first, then BLOCKED (depends_on-gate / blocker-reason),
  capped at 10 + `+ N more`, then a RECENT DONE section. `⚠` marks
  stale in_progress so receivers see neglected work at a glance.

### Added — `scitex-todo sync-github`

- New CLI: `scitex-todo sync-github [--since YYYY-MM-DD] [--dry-run]`.
- Permanent version of the lead's 2026-06-12 one-time GitHub→board
  sync. Pulls `ywatanabe1989/*` merged PRs in the window, matches by
  `pr_url` (and creates new `status=done` records for unmatched PRs),
  collapses mechanical CI-speedup PRs (`title contains "ci-speedup"
  | "L1-L5"`) into a single bundle task per day.
- Designed for the scitex-dev cron registry's hourly poll — the lead
  registers the JOB_REGISTRY entry; this PR ships the verb itself.

### Added — WIP-validation gate on the write side

- `_store.add_task` now consults `_throughput.evaluate_wip(tasks,
  agent)` BEFORE the append. The agent's open-task count (`status
  NOT IN {done, goal}`) drives:
  - `>= SCITEX_TODO_WIP_LIMIT` (default 20) → WARN to stderr.
  - `>= 2 × SCITEX_TODO_WIP_LIMIT` → `TaskValidationError` HARD
    REFUSE; the message names the agent + the count + the limit.
- Goal-tier umbrellas (`status == "goal"`) are explicitly excluded
  per lead-confirm `5acfbb5d`.
- The gate is CLI/MCP/Python-path only — direct YAML hand-edits
  bypass it by design (operator wants the normal path made fat so
  hand-edits are unnecessary, not policed).

### Added — `_throughput.py` shared aggregator

- New module `src/scitex_todo/_throughput.py` — the single source of
  truth for "open" / "stale" / "completed" / "RUNNABLE" / "BLOCKED"
  semantics across the three new surfaces (stats CLI, WIP gate,
  notify body). The dependency classifier (`classify()`) is
  operator-confirmed defensive: an `depends_on` reference to a task
  id that doesn't exist returns `BLOCKED(→ unknown:<id>)` rather
  than silently treating it as RUNNABLE (lead-confirmed `130cc5ac`).
- 26 unit tests in `tests/scitex_todo/test__throughput.py` covering
  `aggregate` (groupings, status semantics, stale flag, unassigned
  rendering), `classify` (RUNNABLE / BLOCKED / unknown-dep
  defensive / status-blocked precedence), the WIP thresholds
  (warn / refuse / agent-attribution short-circuit), and the
  `--notify` body (RUNNABLE-first sort, truncation, ⚠ on stale,
  recent-done section).

## [0.5.9] - 2026-06-12 — Filterbar reorganization (3-group layout)

Operator UX feedback (lead a2a `b48f7c2c438b464698183d2e95d3bb04`,
2026-06-12): `current UI/UX is terrible` — the filterbar grew to
~108 px tall because every control sat in a single `display: flex;
flex-wrap: wrap` row and the wrap order was chaotic. Reorg into three
explicit groups so the placement is intentional, not flex-wrap-roulette.

### Changed

- **HTML**: wrap filterbar children in `.fb-left` (identity:
  title + version + LIVE chip) / `.fb-center` (search input +
  autocomplete suggest dropdown) / `.fb-right` (Layout segment +
  Sort + Filters popover + Recent count + Group + Add Task +
  blocking-me + project-hide + hidden + Reload).
- **Second row** `.filterbar-chips` — active filter chips
  (`#filt-chips`) + qualifier hint pills (`#filt-qhints`) moved off
  the main row into a slim band shown ONLY when populated (via
  `:has(...:empty)` selector). Default state collapses to a single
  ~48 px row.
- **CSS**: `.filterbar { display: flex; min-height: 48px }` with NO
  top-level wrap. The `.fb-right` group wraps internally on narrow
  viewports so the identity + search row stays intact regardless of
  how many right-side controls are visible.
- **Removed** the `margin-left: auto` hack from `.toggle-block` —
  explicit grouping now controls position; the auto-margin pushed
  this single button to the right edge in the old layout, which was
  the source of the asymmetric wrapping the operator photographed.

## [0.5.8] - 2026-06-12 — Graph view edges fix + fleetstrip removal + search-kbd pill folded into placeholder

Operator-reported regressions on `/`, lead-approved fixes:

### Fixed — Graph layout had no edges

- Lead `c212aa72bb0a4161b4faa8e81d508bc8` / `8af2a4a65fe94c9aa0e5f774598127a0`.
  PR #108's Graph view tried to read `t.depends_on` / `t.blocks` per-node
  and emitted a 400-node, 0-edge layout (41 552 px tall — operator
  element-inspector confirmed). The `/graph` endpoint doesn't expose
  those per-node — it returns them aggregated at top-level
  (`STATE.graph.edges`, 26 entries in the operator's live store).
  Hierarchical `parent` edges (111 of them, per-node) were also
  missing.
- Fix: `_renderGraphView` now consumes `STATE.graph.edges` directly
  for the depends_on / blocks set, and walks `t.parent` for the
  hierarchical edge set. Edges are visually distinguished — solid
  arrows for depends_on / blocks, dashed for parent. Graph is
  filtered to the connected component (nodes touched by ≥1 edge);
  disconnected nodes render in Column / Table layouts only.
- New empty-state: when 0 edges among the visible scope, the canvas
  shows a friendly explanation pointing at the `depends_on` / `blocks`
  / `parent` YAML encoding so the operator can fix the data.

### Removed — empty fleetstrip + standalone kbd-hint pill

- Lead `032e41545fcf4ab4b98d864ec1770249`. The `div#fleetstrip`
  rendered as `Content: none` because the payload never populated
  `STATE.graph.fleet` — operator: "i dont need this". The element +
  the orphan `renderFleetStrip()` helper are removed; fleet-liveness
  lives in the lead's periodic reports.
- The standalone `span.filt-search-kbd` "press / to focus" pill is
  removed. The same hint is now inline in the search input's
  placeholder text — operator: "just write the kbd in the search box".

## [0.5.7] - 2026-06-12 — User lane normalization + tighten left space + age pill + finish BLOCKING-YOU removal

Lead-HOLD-approved follow-up to 0.5.6 (PR #107, rebased on top of the
P0 LAYOUT-axis + Recent-sort merge):

### Changed — User lane normalization

- **The 360 px BLOCKING-YOU right-side aside is now FULLY removed.**
  0.5.6's render-refactor removed the JS that populated `#block-rows`
  but left the `<aside id="right-panel">` HTML in the template — the
  operator saw "loading…" forever in the right sidebar. This PR
  finishes the job: the `<aside>` block + the mobile `#by-fab` toggle
  + the `toggleByDrawer` / `updateByFabBadge` helpers are all
  removed. Operator-decision-blocked tasks live in the synthesized
  `user` lane in `_renderColumnView` (a normal column with normal
  width, drag-reorder, pin, column-context-menu).

### Changed — Left space tightened

- Board overrides the scitex-ui standalone shell so the
  `ws-ai-pane` (console / chat), `ws-worktree-pane` (file tree), and
  `ws-viewer-pane` (file viewer) are `display: none`. The kanban
  doesn't need any of those, and the operator reported "left empty
  space" eating the columns area. The `ws-module-pane` (board
  content) now uses the full viewport width.

### Added — Card age pill

- Each card carries a `⏳ Nd` pill in the header next to
  `last <activity>`. Stale color buckets:
  `today` mint-green "new" / `fresh 1–6d` muted / `aging 7–29d`
  amber / `stale 30–89d` orange / `rotten ≥90d` saturated red.
  Source is `created_at` (preferred) with `last_activity` fallback;
  null when neither parses (back-compat: legacy data shows no pill
  instead of `NaN`).
- CSS in `board_v3/02-card.css` (`.age-pill` + 5 modifiers, same
  shape as the existing `.date-pill` family).

## [0.5.6] - 2026-06-12 — Board v0.5.4 P0: empty-pill fix + LAYOUT axis + Recent sort

Lead-prioritized fix after PR #105 verification miss surfaced two
still-broken symptoms on `/` (the operator's primary board page):

### Fixed — cards rendered as empty pills on every lane

- Diagnosed against the live store at `:8051`: `business` lane's 28
  cards rendered with `offsetHeight = 24 px`; `scitex-dev` 68 cards at
  18 px; `paper-scitex-clew` 15 cards at 42 px. Root cause: `.col-body`
  is `display: flex; flex-direction: column` and `.main { height:
  100%; overflow: hidden }` (added in 22b6a6f to keep the BLOCKING-YOU
  aside from stretching). Inside a bounded flex column container,
  child `.card` items defaulted to `flex-shrink: 1` and compressed
  down to the card-status row when content exceeded container height.
- Fix: `.card { flex-shrink: 0 }` in `board_v3/02-card.css`. Cards
  keep their natural content height; the existing `.col-body
  { overflow-y: auto }` lets excess content scroll inside the column.
  Verified live by injecting the rule via Playwright `add_style_tag`:
  every lane's first card grew from 18-42 px back to 111-150 px.

### Added — LAYOUT axis (Graph | Column | Table) + Recent sort

Lead design ruling (TG 12461, operator-confirmed): the board renders
the SAME data along two orthogonal axes — LAYOUT (Graph | Column |
Table) sits in the filterbar; TIME (Recent) is a SORT mode in the
existing Sort dropdown, applies across all layouts.

- **LAYOUT switcher** — three segmented buttons in the filterbar.
  Persisted in `localStorage["scitex-todo:layout"]`.
  - `📋 Column` — the existing kanban (default).
  - `📑 Table` — flat rows view, sortable, click a row to open the
    detail drawer. Status / Title / Project / Blocker / Priority /
    Last activity columns.
  - `📊 Graph` — depends_on / blocks mermaid graph, lazy-loads
    `mermaid@10` from jsdelivr the first time the operator switches
    to it.
- **Recent sort mode** — `Recent (newest first) 🆕` option added to
  the existing `#f-sort` dropdown. Cards sort by `last_activity →
  created_at` desc; cards with activity in the last 24 h get a gold
  `NEW` badge in `.card-top`. The badge renders across every layout
  when sort = recent. Persisted in `localStorage["scitex-todo:sort"]`.
- **🆕 N new in 24 h pill** — always-visible filterbar indicator
  showing how many of the currently-visible cards moved in the last
  day. Click to set Sort = Recent. Hidden when zero.

CSS lives in a new sibling file `board_v3/06-layout-and-recent.css`
(keeps the per-file CSS under the 512-line hook limit). Linked from
`board_v3.html`'s `{% block extra_css %}`.

## [0.5.4] - 2026-06-12 — Board v0.5.3 display fix (template leak + bundle/template food)

Operator-reported regression after the 0.5.3 release:

- Multi-line `{# … #}` comment block leaked verbatim ("`{# searchQuery.js …
  #}`") into the rendered HTML at the top of every board page.
- Cards in every lane rendered as empty pills (no text) — `board_v3/*.css`
  had been wiped from the static dir.
- View toggle (Graph / Table / Recent) was invisible — the React SPA bundle
  was out of sync with the TypeScript source.

### Fixed

- **Template comment leak** (PR #105). Replaced the two multi-line `{# … #}`
  blocks in `board_v3.html` with `{% comment %} … {% endcomment %}`.
  Django's `{# … #}` is single-line only; multi-line blocks render their
  body as page text. Already pinned by
  `test_standalone_template_does_not_leak_django_comment` in
  `tests/scitex_todo/_django/test_views.py`.
- **Bundle/template food (root cause)** (PR #105). The vite config wrote
  into `../static/scitex_todo` with `emptyOutDir: true`, which wiped the
  SIBLINGS of `assets/` on every rebuild — `favicon.svg`,
  `board_v3/*.css`, and `board_v3/searchQuery.js`/`searchSuggest.js` are
  all tracked-in-git static assets consumed by the live `board_v3.html`
  template. We now scope `outDir` to the `assets/` subdir, so a rebuild
  only ever touches the React SPA bundle and never the board_v3 statics.
- **Bundle rebuild from current TS source** (PR #105). Clean
  `npm install` + `vite build` ran against the post-#104 source so the
  shipped `assets/index.js` / `assets/index.css` matches the TypeScript
  source (the Graph / Table / Recent toggle ships with the bundle).

## [0.5.3] - 2026-06-12 — Board UX wave + self-consuming loop + deadline schema

Captures every PR that landed between 0.5.2 and develop tip (operator
TG 12028 / 12038 / 12081 wave).

### Added — Board UX

- **Search-as-launcher** (PR #86). `#f-search` becomes the primary
  filterbar control; press `/` from anywhere to focus, `Esc` to blur.
  Purple-haloed at rest, brighter on focus, kbd-hint chip advertises
  the affordance.
- **Filter UX collapse + active chips + sort-by** (PR #89). Six
  filter dropdowns hide behind a single `🔧 Filters (N active)`
  popover; active filters render as removable chips; new sort-by
  selector (deadline / priority / status / project / last_activity /
  title).
- **Self-named project-umbrella cards hidden** (PR #87). A card
  whose title matches its column name is suppressed inside that
  column.
- **Move-picker lists ALL projects + Create-new** (PR #88 + #94).
  Right-click → Move picker is a Combobox over every project in the
  store with `+ Create '<query>'`.
- **Combobox primitive in scitex-ui** (scitex-ui #36 + #37, consumed
  via PR #94). Fuzzy-typeahead select layered over the six filterbar
  dropdowns + the move-picker; pure-JS bundle for Django-template
  consumers.
- **Project GROUPS** (PR #91). User-defined clusters of projects;
  new top-level `groups:` key in `tasks.yaml`; `spans_all: true`
  banner above the grid; group-header rows between clusters.

### Added — Deadlines + org-mode bridge

- **`deadline` + `scheduled` fields on `Task`** (PR #92). ISO-8601
  strings; validator rejects empty / unparseable / `deadline <
  scheduled`.
- **Org-mode export adapter** (PR #93). `build_org(tasks) → str`
  emits `DEADLINE:` / `SCHEDULED:` + properties drawer. 17 tests.
- **Multi + recurring deadlines** (PR #97). Org-style repeater suffix
  on the single field (`+1w` / `++2m` catch-up); optional `deadlines:
  list[str]` mutually exclusive with the single field; server emits a
  synthetic `deadline_next` for FE consumption.

### Added — Self-consuming board loop (operator TG 12038)

- **`scitex-todo next` CLI verb** (PR #95). Canonical "what to pick
  up next" predicate. `--mine` reads `SCITEX_TODO_AGENT`;
  `--auto-claim` atomic-flips to `in_progress` + stamps a starting
  comment in one write.
- **`scitex-todo watch --push` CLI verb** (PR #95). Polls tasks.yaml,
  diffs, POSTs `/v1/turn` to the owning agent's a2a port on
  new / commented / status-changed tasks. Watcher declared as a
  second `kind=service` JobSpec.
- **Agent self-consumption loop sub-skill (32)** + **MANDATE block in
  `SKILL.md`** (PR #90 + #95).

### Fixed

- **P1 + P7 regressions restored** (PR #96). The P10/P11 squash wave
  silently dropped P1 #86 + P7 #87 from develop; PR #96 restores both
  and pins **24 substring signatures** in
  `tests/scitex_todo/test__board_v3_signatures.py` so future squash
  drops fail CI instead.

### Notes for operators

After upgrading: `systemctl --user restart scitex-todo.dashboard`.
The new `scitex-todo.wake-watcher` unit needs
`systemctl --user reset-failed scitex-todo.wake-watcher` followed by
`systemctl --user enable --now scitex-todo.wake-watcher`.

## [0.4.2] - 2026-06-08 — Crash-safe store + version label + Uncategorized column

Patch release in response to the 2026-06-08 autoassign-parallel-run
data-loss incident: roughly 130 operator-added tasks lost when two
concurrent autoassign scripts were SIGTERM'd mid-`save_tasks` dump
and the store was left half-written. This release closes the bug at
the store layer + makes the live release visible on the board.

### Fixed (crash-safety, lead a2a `3b0df14a`)
- **Atomic write in `save_tasks`** — dump now goes to a sibling `.tmp`
  file, fsync, then `os.replace(tmp, tasks.yaml)`. POSIX-atomic; a
  SIGTERM/SIGKILL mid-dump can no longer leave the canonical file
  half-written. The pre-existing `fcntl.flock` on the sidecar lockfile
  is unchanged.
- **Git auto-commit on every save** — lazy-initializes a `.git` inside
  the store directory on first save_tasks call, then commits each
  successful write. Operator gets time-travel: `git -C ~/.scitex/todo
  log` + `git show <sha>:tasks.yaml` to restore any prior state.
  Best-effort: a git failure never blocks the actual save.

### Added (board v3)
- **`scitex-todo vX.Y.Z` page title + header** (operator TG 407). The
  live `__version__` is read off the package import and rendered in
  both the `<title>` tag (browser tab) and the in-page H1. No second
  source of truth to drift on release.
- **"Uncategorized" replaces "Ungrouped"** (operator TG 405). The
  no-project column label aligns with the legacy "Uncategorized pool"
  convention from PR #4 and reads as plain English. Internal grouping
  key + filter dropdown both updated.

### Notes for operators
After upgrading: restart your `scitex-todo board` systemd unit.
`~/.scitex/todo` becomes a git repo on the first board write — the
operator can `git -C ~/.scitex/todo log` immediately, no extra setup.
Any future corruption is recoverable via standard git commands.

## [0.4.1] - 2026-06-08 — Board v3 horizontal layout + column pin + drag-reorder + fleet-liveness

Patch release on top of 0.4.0 to unblock operator UX (TG 370) the
moment they saw 0.4.0 live: project columns stacked vertically with
many projects + no way to reorder / prioritize them.

### Fixed (board v3)
- **Columns now lay out side-by-side with horizontal scroll.** The
  previous CSS grid `repeat(auto-fit, minmax(220px, 1fr))` wrapped
  many-column boards into a 40,000px tall stack (operator's element-
  inspector dump confirmed 39929px height). Switched to a single-row
  flex strip with `overflow-x: auto`; each column is a fixed 280px
  wide. Kanban / Trello / Linear convention.

### Added (board v3)
- **Column drag-to-reorder.** Each column section is `draggable`;
  drop on another column inserts BEFORE that target. Order persists
  in `localStorage` under `scitex-todo:col-order` (per-browser
  preference, no backend change).
- **Column pin (📍 / 📌).** Per-column pin button in the header.
  Pinned columns float to the LEFT of the strip regardless of drag
  order. Persists in `localStorage` under `scitex-todo:col-pinned`.
- **Fleet-liveness dot-strip** (PR #75) — one colored dot per agent
  in the filter bar, gold/green/blue/grey by status, click toggles
  the agent filter. Powered by a new `fleet` summary on `/graph`
  (additive — no schema change).

## [0.4.0] - 2026-06-08 — Board v3 + scitex-ui shell + task-harvest skill

The shared-fleet board matures into a real Django app: the live
**board v3** (kanban + BLOCKING-YOU panel + Resolve → notify wire) is
promoted to the package root and now extends the **scitex-ui shell**
so it picks up the Alt+I element-inspector + shared chrome for free.
The **Task dataclass** becomes the single schema source. The
**task-harvest skill** documents the operator-commissioned backlog
burn-down loop (2-state model, 4-value blocker enum, root-blocker
walk, `scitex-dev cron` registration). Compute-state-deps + decision-
nodes + ports skeleton land for the north-star roadmap.

### Added (board)
- **Board v3** — live Django board (kanban-style columns, status
  filters, BLOCKING-YOU panel, Resolve → a2a notify wire). Promoted to
  root URL (`/`); legacy GraphView demoted to `/legacy/`. (#57, #58.)
- **scitex-ui shell integration** — board v3 extends
  `scitex_ui/standalone_shell.html`, so Alt+I element-inspector +
  shared chrome work the same way on board v3 as on the legacy
  GraphView. Compatibility with scitex-hub register-as-module via
  `scitex_app._django.ScitexAppConfig` preserved. (#69.)
- **CRUD endpoints** on the Django backend (`/create`, `/update`,
  `/delete`, `/comment`, `/edge`, `/restore`, `/priority`, `/resolve`)
  — see `handlers/crud.py`; UI wiring on board v3 ships incrementally.
- **Board v3 Resolve safety** — 2-click confirm + Undo toast + new
  `/reopen` endpoint so an accidental Resolve is recoverable. (#61.)
- **Board v3 comments + priority + hide** — Word-style comment thread
  + per-card priority up/down + hide button. (#62.)
- **ESC closes the detail modal** (operator TG 265). (#59.)
- **Drill-down clarity** — empty-state explainer, Pool label, Back
  button + region labels (Board / Drill / Canvas / Pool) + count
  breakdown (Total·Showing·Nested·Pool). (#50, #51.)
- **Hover affordance** — replace parent-node tilt with a "⊞ Drill in"
  hover-hint pill (operator TG 245). (#53.)

### Added (schema / Task dataclass)
- **Task dataclass = single schema source** (#56). All schema
  validation flows through one dataclass; `_validate_tasks` consumes
  it; the Gitea adapter + the future README-frontmatter SSoT both
  consume the same shape. 9 new operator fields (`task` /
  `last_activity` / `host` / `pr_url` / `issue_url` / `agent` /
  `project` / `goal` / `created_at`) land.
- **D11 stamping** (#67) — `created_at` is auto-stamped on `add_task`;
  `last_activity` is auto-stamped on `update_task`.
- **Field-flag expansion for `add` / `update`** + closed-enum CLI
  validation (#65). Every operator-facing field is now a `--flag` on
  the CLI; closed enums (`status` / `kind` / `blocker`) reject typos
  at write time.
- **Compute-state-deps north-star pillar #1** (#52) — `kind` enum
  (`task` / `compute`) + compute metadata (`job_id` / `host` /
  `command` / `started_at` / `finished_at`) + ⚙ glyph + KV table.
  Compute jobs (Spartan / SIF builds / CI) become first-class graph
  nodes that external watchers can flip done.
- **Decision-nodes + closed BlockerKind enum** (#54) — `kind: decision`
  + ⚖️ glyph + LOUD operator-decision halo + "unblocks N" impact badge
  + 👤 awaiting-you lens. North-star pillar #4.
- **Core / Extension Ports / Fleet Adapters skeleton** (#55) — ADR-0006
  backbone for the open-source / fleet-adapter split.

### Added (skills)
- **`11_adopting-from-a-project`** (#60) — 30-second adoption how-to
  for project agents to write their tasks into the shared board.
- **`40_task-harvest`** (#70, #72) — operator-commissioned backlog
  burn-down protocol: 2-state model (BLOCKED + reason + dependency
  from a 4-value enum vs RUNNABLE), 2-phase sweep cycle (Phase 1
  re-check blockers + walk `task-dependency` chains to their LEAF
  root-blocker; Phase 2 escalate every RUNNABLE task to its owning
  agent via a2a), lead-centric funnel routing, and registration as a
  `scitex-dev cron` JobSpec.

### Fixed
- **`scitex-todo board --tasks PATH`** now actually pins the server's
  store (was previously a no-op for the Django subprocess — only the
  browser URL query was set). (#46.)
- **Audit pipeline unblocked** — TQ002 / TQ007 + PS-202 / PS-204
  violations fixed. (#68.)

### Notes for operators
After upgrading: restart your `scitex-todo board` systemd unit so the
board picks up the scitex-ui-shell extension. Alt+I + element-
inspector work immediately after restart. CRUD UI on board v3 wires
to the existing endpoints incrementally — Resolve + Priority +
Comment + Hide already land in this release; full Create / Update /
Delete UI ships in a follow-up patch.

## [0.3.0] - 2026-06-04 — Phase 1 MVP: shared-fleet TODO

The universal-task-layer FLOOR for the agent fleet. Every agent can
read/write the same YAML store across hosts, the board at
http://127.0.0.1:8051 aggregates everyone's tasks for the operator,
and the Python API / CLI / MCP surface follows scitex-dev audit
conventions (Convention A: tool_name == python_api_name).

### Added
- **Per-task `scope` / `assignee` fields** (additive-optional, free-form
  strings). Convention is `agent:<name>` / `project:<name>` / `private`
  but the schema doesn't enforce it (Req 8: be generic).
- **`_log_meta` mapping** — opaque event-stamp dict; `complete_task` writes
  `completed_at` (ISO-8601 UTC, `Z`-suffixed, second resolution) +
  `completed_by`. Phase-2 progress-history substrate.
- **Mutation Python API** (`scitex_todo._store`, re-exported from
  `scitex_todo`): `add_task`, `update_task`, `complete_task`, `list_tasks`,
  `summarize_tasks`, `resolve_store`, `TaskNotFoundError`, `ENV_SCOPE`,
  `ENV_AGENT`. The public top-level surface is narrowed to these six
  task-store functions (plus errors / env constants) to satisfy audit §6
  (Convention A: tool_name == python_api_name). The mermaid / render /
  model / paths helpers remain importable from their submodules
  (`scitex_todo._diagram`, `scitex_todo._diagram`, `scitex_todo._model`,
  `scitex_todo._paths`).
- **CLI write / admin verbs**: `add`, `update`, `done`, `summary`, plus
  `list-tasks` (extended with `--scope` / `--assignee` / `--status`
  filters; backward-compatible default output for existing `list-tasks`
  users), `resolve-store`, `init-store [--shared|--project]`,
  `sync-store [--dry-run|--apply]` (Phase-1 stub). Mutating verbs
  (`add`, `update`, `init-store`, `sync-store`, `mcp start`, `mcp install`)
  accept `--dry-run` + `-y`/`--yes` per audit §2. The pre-audit names
  `list` / `where` / `init` / `sync` were renamed per audit §1 (bare
  transitive verbs at the top level need an object noun).
- **MCP server** (`scitex_todo._mcp_server`) behind the new `[mcp]` extra
  (`fastmcp>=2.0`). Eight tools — six task-store tools follow
  Convention A (tool_name == python_api_name, no prefix): `add_task`,
  `update_task`, `complete_task`, `list_tasks`, `summarize_tasks`,
  `resolve_store`; plus `todo_skills_list` / `todo_skills_get` for
  bundled-skill discovery. `import scitex_todo` works fine without the
  extra installed.
- **`mcp` CLI subgroup** — §3 required four (`start`, `doctor`,
  `list-tools`, `install`). Prefers `scitex_dev._mcp_cli` when present;
  hand-rolled fallback otherwise.
- **`fcntl.flock` mutex** on `save_tasks` (and the new mutators in
  `_store`) holding the full read-modify-write cycle. Phase-1 prereq for
  the Phase-2 cross-host sync substrate (Req 2).

### Documented
- `GITIGNORED/ARCHITECTURE.md` — Phase-0 9-requirement → mechanism map.
- `GITIGNORED/QUESTIONS.md` — open defaults for the operator/lead.
- `GITIGNORED/PROPOSAL_scitex-dev-ecosystem-register.md` — paste-apply
  diff for the lead so `scitex_dev.ECOSYSTEM` includes `scitex-todo`
  (Req 6).

### Test surface
- +47 real tests (no mocks). The two-subprocess concurrent-writer test
  proves the lock serializes interleaved inserts (the failure caught
  while writing it was the source of the `_save_tasks_unlocked` split —
  the lock has to wrap the full read-modify-write, not just the write).

## [0.2.0] - 2026-05-27

### Added
- Web board (read-only React-Flow dependency graph) served by Django:
  `scitex-todo board` (needs the `[web]` extra). Nodes colored by status,
  `depends_on` arrows, `blocks` inhibition edges, clickable cards, and
  nested-graph drill-down via a new `parent` task field.
- Drag-reorder write path: the board's `POST /priority` handler persists a new
  ordering back to the YAML store (preserving comments via ruamel) — the first
  agent↔user GUI write surface. `save_tasks` is now public.
- §1a CLI introspection: `list-python-apis` (with the additive `-v/-vv/-vvv`
  ladder) and `mcp list-tools`, both with `--json`.
- Shell completion: `install-shell-completion` / `print-shell-completion`
  (bash/zsh/fish) using the static cache-file pattern.
- Agent skills: bundled `_skills/scitex-todo/` (installation, quick-start,
  python-api, cli-reference, env-vars) plus a self-contained
  `skills {list, get, install}` CLI group.
- `python -m scitex_todo` entry point; `.env.example`; `examples/` with a
  matching `tests/examples/` smoke test; cross-package integration gate.

### Changed
- **CLI verbs renamed** to noun-verb compounds (audit §1): `render` →
  `render-graph`, `list` → `list-tasks` (now with `--json`). Added top-level
  `--help-recursive` and `--json`.
- `_cli.py` split into a focused `_cli/` package (`_main`, `_introspect`,
  `_completion`, `_skills`).
- README rebuilt to the canonical SciTeX layout (logo, badges, Problem/Solution,
  Architecture diagram, Interfaces, footer); `docs/roadmap.md` refreshed.
- Added GitHub Actions: `tests`, `import-smoke`, `quality`, tag-driven
  `release` (PyPI via OIDC + GitHub Release), and the CLA gate.
- Test suite reorganized to mirror `src/` and to satisfy the test-quality rules
  (one assertion per test, AAA markers).

[0.2.0]: https://github.com/ywatanabe1989/scitex-todo/releases/tag/v0.2.0

## [0.1.0] - 2026-05-22

### Added
- Canonical YAML task store: top-level `tasks:` list with `id` / `title` /
  `status` (required) and optional `repo` / `depends_on` / `blocks` / `note`.
- `load_tasks` — validating loader (`TaskValidationError` on missing id/title,
  duplicate id, or invalid status). Statuses: `goal`, `pending`,
  `in_progress`, `blocked`, `done`, `deferred`, `failed`.
- Mermaid adapter: `build_mermaid` renders `flowchart TB` with `depends_on`
  arrows, `blocks` inhibition edges (`-- blocks --x`), and per-status colors
  (goal = gold `#ffe082`).
- Renderer: `render` (mmdc-first with auto-discovered puppeteer/playwright
  chromium and `--no-sandbox`; `kroki.io` fallback).
- Task-store path resolution following the SciTeX local-state convention:
  explicit path -> `$SCITEX_TODO_TASKS` -> project `.scitex/todo/tasks.yaml`
  -> user `~/.scitex/todo/tasks.yaml` -> bundled generic example.
- CLI `scitex-todo` (Click, noun-verb): `render`, `list`.
- Bundled generic example task store at `scitex_todo/examples/tasks.yaml`.

[0.1.0]: https://github.com/ywatanabe1989/scitex-todo/releases/tag/v0.1.0
