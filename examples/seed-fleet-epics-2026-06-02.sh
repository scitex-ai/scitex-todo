#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# seed-fleet-epics-2026-06-02.sh
#
# Seeds the lead's eight fleet-wide epics (E1-E8) into the canonical
# scitex-todo store as `claimable` tasks:
#
#   E1  sac ACL fleet-group + grant CLI + skill  (proj-scitex-agent-container)
#   E2  sac locator: where / list --all-hosts / lineage
#   E3  sac agents migrate
#   E4  hub -> NAS standup
#   E5  orochi -> MBA (arm64 SIF)
#   E6  staging env + local port-forward
#   E7  sac writable-creds fix
#   E8  ongoing scitex package bugfixes
#
# Each epic is added with:
#   - scope:     project:sac (E1, E2, E3, E7) | project:hub (E4) |
#                project:orochi (E5) | project:fleet (E6, E8)
#   - assignee:  the lead's named owner OR empty (claimable from queue)
#   - status:    pending
#   - priority:  integer (lower = earlier; we leave room for the operator
#                to drag-reorder on the board afterwards)
#   - note:      one-paragraph context the operator-overnight directive
#                gave the agent fleet on 2026-06-01
#
# Idempotency: `scitex-todo add` raises `TaskValidationError` on a
# duplicate `id`, so re-running this script after a partial success is
# safe — it will fail-loudly on the first already-seeded epic and you can
# resume from there. To reseed from scratch, run
# `scitex-todo update <id> --status deferred` first or hand-edit the store.
#
# Prerequisites:
#   pip install 'scitex-todo[mcp]>=0.3.0'
#   scitex-todo where     # confirm the resolved store path
#   scitex-todo init --shared    # if ~/.scitex/todo/tasks.yaml doesn't exist
#
# Usage:
#   bash examples/seed-fleet-epics-2026-06-02.sh
#
# Background: this is the third deliverable in the operator-overnight
# universal-task-layer mission (see docs/adr/0001-universal-task-layer.md
# and docs/CHEATSHEET-fleet-todo.md). Running this script is what turns
# the operator's directive into claimable rows that the fleet can pick up.
# ──────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# Where to store: respects the usual precedence chain (CLI --tasks env
# SCITEX_TODO_TASKS, project, user, bundled). To target an alternate
# store, set SCITEX_TODO_TASKS before invoking.
TODO="${SCITEX_TODO:-scitex-todo}"

echo "Seeding fleet epics into:"
"$TODO" where
echo

# ── E1 ────────────────────────────────────────────────────────────────────────
"$TODO" add e1-sac-acl \
    "sac ACL: fleet-group + grant CLI + skill" \
    --scope project:sac \
    --assignee agent:proj-scitex-agent-container \
    --status pending \
    --priority 10 \
    --repo sac \
    --note "Operator overnight 2026-06-01: stand up the sac access-control surface — fleet-group abstraction (a set of agents acting as one principal), 'grant' CLI verb to assign capabilities to a group, and a sac skill that documents the workflow for an agent to request + receive a grant. Owner proj-scitex-agent-container has the SAC environment context to ship this. Acceptance: group creation + grant idempotent; skill renders via 'sac skills list'; round-trip 'grant -> use' covered by a real (no-mock) sac-listen integration test."

# ── E2 ────────────────────────────────────────────────────────────────────────
"$TODO" add e2-sac-locator \
    "sac locator: 'where', 'list --all-hosts', 'lineage'" \
    --scope project:sac \
    --status pending \
    --priority 20 \
    --repo sac \
    --note "Operator overnight 2026-06-01: 'I want to know where each agent is, on every host.' Three CLI verbs: 'sac where <agent>' (resolves to host + container + pid), 'sac list --all-hosts' (paginated table across the fleet, not just the listen-local), 'sac lineage <agent>' (parent->child spawn chain). Acceptance: works against a 2-host listen mesh; degrades gracefully when one host is unreachable (skip + warn, never fail-stop)."

# ── E3 ────────────────────────────────────────────────────────────────────────
"$TODO" add e3-sac-agents-migrate \
    "sac agents migrate: hand off agent state between hosts" \
    --scope project:sac \
    --status pending \
    --priority 30 \
    --repo sac \
    --note "Operator overnight 2026-06-01: agent migration is the headline payoff of the universal-task-layer. Build 'sac agents migrate <agent> --to <host>' that (a) pauses the source instance, (b) ships its state-db + claimed-task list to the target host, (c) spawns a fresh instance there with the same identity + env, (d) verifies the new instance reads its scitex-todo claims and resumes. Depends on E1 (acl) and the merged scitex-todo Phase-1 write surface (PR #14)."

# ── E4 ────────────────────────────────────────────────────────────────────────
"$TODO" add e4-hub-nas-standup \
    "scitex-hub on NAS standup" \
    --scope project:hub \
    --status pending \
    --priority 40 \
    --repo scitex-hub \
    --note "Operator overnight 2026-06-01: stand up scitex-hub on the NAS so the fleet has a stable, always-on web entry point. Hub registers scitex-{todo,figrecipe,...} as app modules; NAS provides the persistence (no laptop-suspends-kill-the-hub failure mode). Acceptance: hub reachable on the LAN at a known URL; /todo/ embed of scitex-todo board renders the live store; restart survives a NAS reboot."

# ── E5 ────────────────────────────────────────────────────────────────────────
"$TODO" add e5-orochi-mba-arm64 \
    "Orochi on MBA (arm64 SIF)" \
    --scope project:orochi \
    --status pending \
    --priority 50 \
    --repo orochi \
    --note "Operator overnight 2026-06-01: get Orochi running on the MBA via an arm64 Singularity image. Currently Orochi is amd64-only; cross-build the SIF or define a multi-arch build. Acceptance: 'apptainer pull orochi.sif' + 'apptainer run' works on the MBA; the Orochi chat surface reaches the same sac listen + scitex-todo store as the WSL2 dev box."

# ── E6 ────────────────────────────────────────────────────────────────────────
"$TODO" add e6-staging-env \
    "Staging env + local port-forward" \
    --scope project:fleet \
    --status pending \
    --priority 60 \
    --note "Operator overnight 2026-06-01: separate prod fleet from a staging fleet so risky changes don't blast the operator's working environment. Includes per-env sac-listen instances, per-env scitex-todo stores (separate state-repo remotes), and a local-port-forward script that maps staging:8051 -> 8052 on the operator's box for side-by-side board comparison. Acceptance: 'sac --env staging agents list' is disjoint from 'sac --env prod agents list'."

# ── E7 ────────────────────────────────────────────────────────────────────────
"$TODO" add e7-sac-writable-creds \
    "sac writable-creds fix" \
    --scope project:sac \
    --status pending \
    --priority 70 \
    --repo sac \
    --note "Operator overnight 2026-06-01: agents currently hit stale-creds wedges on restart (this proj-scitex-todo restart was caused by exactly that). Root cause: the credentials volume is mounted read-only, so the bearer-refresh path can't persist. Fix: writable-creds mount + atomic-write refresh + a 'sac doctor' diagnostic that flags 'last refresh > 24h ago'. Acceptance: a stale bearer triggers refresh + retry within one tool-call window; no restart needed."

# ── E8 ────────────────────────────────────────────────────────────────────────
"$TODO" add e8-scitex-bugfixes \
    "Ongoing scitex package bugfixes (umbrella)" \
    --scope project:fleet \
    --status pending \
    --priority 80 \
    --note "Operator overnight 2026-06-01: rolling umbrella for the smaller cross-package bugfixes that don't justify their own epic — e.g. scitex-todo HANDOFF.md staleness, scitex-dev audit-all edge cases, README drift across packages. Child tasks should set parent: e8-scitex-bugfixes so the board's drill-down view groups them. Owner-on-demand: whoever has the relevant package context claims the child task; no fixed assignee on the umbrella."

echo
echo "Seeded 8 fleet epics. View on the board:"
echo "  scitex-todo board"
echo "Filter your slice:"
echo "  scitex-todo list --scope project:sac"
echo "  scitex-todo list --assignee agent:proj-scitex-agent-container"

# EOF
