#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""T1.2 — `runnable()` Python API (the parallelism-engine dispatcher).

Sister to :func:`_next.next_task`, but BATCH (returns the FULL runnable
set, not just the top pick) AND respects `depends_on` (transitive
upstream closure). Lead a2a `74db4f2d`, 2026-06-14 — TRACK 1
(dependency-aware tickets) dispatch backbone.

Filter (RUNNABLE-NOW):
  * ``status`` ∈ {``pending``, ``in_progress``}
  * ``blocker`` is None (an explicitly-blocked row is not runnable)
  * EVERY id in ``depends_on`` references a task whose ``status`` ∈
    {``done``, ``goal``}. A dep on a not-yet-finished task means
    NOT-RUNNABLE-YET. Unknown ids (no matching task in the store) are
    permissive — they fall outside the runnable engine's scope and
    leave the row runnable (matches the same lenient stance the
    graph builder takes on unknown-id edges).
  * For each task Z whose ``blocks: [...]`` list contains this task's
    id: Z must also be in {``done``, ``goal``}. Mirrors `depends_on`
    semantically — explicit "Z blocks this" is the same as "this
    depends_on Z."
  * Optional ``agent`` filter (matches ``agent`` OR legacy
    ``assignee``).
  * Optional ``group`` filter (matches the T1.1 `group` field).

Sort key (lowest = first to dispatch):
  1. ``priority`` ASC, ``None`` ranks LAST.
  2. ``last_activity`` DESC.
  3. ``created_at`` DESC.
  4. ``id`` ASC (deterministic tiebreak).

Distinct from :func:`_next.next_task`:
  - `next_task` returns a SINGLE pick (the agent's "one thing to work
    on now"). It DOES NOT inspect `depends_on` today — assumes the
    operator/lead curates the queue. Kept for back-compat with the
    self-consumption loop.
  - `runnable_tasks` returns the FULL list, respects `depends_on`,
    is what the parallelism dispatcher (lead-side) consumes via the
    `scitex-todo runnable` CLI / `/runnable` endpoint.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional


#: Status values eligible for runnable-pickup.
RUNNABLE_STATUSES: frozenset[str] = frozenset({"pending", "in_progress"})

#: Status values that SATISFY a dependency (upstream task is "done enough").
RESOLVED_STATUSES: frozenset[str] = frozenset({"done", "goal"})


@dataclass(frozen=True)
class RunnableSet:
    """Result of :func:`runnable_tasks` — the picked list + filter stats.

    Attributes
    ----------
    tasks : list[dict]
        The runnable rows, sorted by the standard priority / recency
        key. Verbatim from the store; the caller chooses how much of
        each row to surface.
    candidate_count : int
        How many rows matched the BASE filter (status + blocker)
        BEFORE the dep-check + agent/group filters. Diagnostic.
    blocked_by_deps_count : int
        How many BASE candidates were dropped because their
        ``depends_on`` chain contained a not-yet-resolved upstream.
        Diagnostic — lets the dispatcher say "X tasks would be
        runnable if Y upstream finished."
    """

    tasks: list[dict]
    candidate_count: int
    blocked_by_deps_count: int


def runnable_tasks(
    tasks: Iterable[dict],
    *,
    agent: Optional[str] = None,
    group: Optional[str] = None,
) -> RunnableSet:
    """Return the FULL runnable set, optionally filtered by agent / group.

    Parameters
    ----------
    tasks : iterable of dict
        The full task list (e.g. ``load_tasks(path)``). Inspecting the
        whole list is required for the dep-check (we resolve
        ``depends_on`` ids against the same list).
    agent : str, optional
        Match against task ``agent`` OR legacy ``assignee``. ``None`` =
        no agent filter (all agents).
    group : str, optional
        Match against the T1.1 ``group`` field. ``None`` = no group
        filter. Empty string is treated as "ungrouped only" so a
        dispatcher can ask for the residual non-cluster tasks.

    Returns
    -------
    RunnableSet
        The sorted runnable rows + diagnostic counts.
    """
    snapshot = [t for t in tasks if isinstance(t, dict)]
    status_by_id: dict[str, str] = {
        t.get("id"): t.get("status") for t in snapshot if t.get("id")
    }
    # Build a reverse map: for every id X, find the set of Z where
    # Z.blocks contains X. We use it to enforce "Z blocks X means X
    # waits for Z."
    blocks_into: dict[str, list[str]] = {}
    for t in snapshot:
        z_id = t.get("id")
        if not z_id:
            continue
        for x_id in t.get("blocks") or ():
            blocks_into.setdefault(x_id, []).append(z_id)

    base_candidates: list[dict] = []
    runnable: list[dict] = []
    blocked_by_deps = 0

    for t in snapshot:
        if not _passes_base_filter(t):
            continue
        if agent is not None and not _matches_agent(t, agent):
            continue
        if group is not None and not _matches_group(t, group):
            continue
        base_candidates.append(t)

        if _deps_satisfied(t, status_by_id, blocks_into):
            runnable.append(t)
        else:
            blocked_by_deps += 1

    runnable.sort(key=_sort_key)
    return RunnableSet(
        tasks=runnable,
        candidate_count=len(base_candidates),
        blocked_by_deps_count=blocked_by_deps,
    )


def _passes_base_filter(task: dict) -> bool:
    if task.get("status") not in RUNNABLE_STATUSES:
        return False
    if task.get("blocker"):
        return False
    return True


def _matches_agent(task: dict, agent: str) -> bool:
    return task.get("agent") == agent or task.get("assignee") == agent


def _matches_group(task: dict, group: str) -> bool:
    # An EMPTY-string `group=""` means "ungrouped only" (residual
    # filter for the dispatcher). Any non-empty value is a literal
    # match against the task's `group` field.
    if group == "":
        return not task.get("group")
    return task.get("group") == group


def _deps_satisfied(
    task: dict,
    status_by_id: dict[str, str],
    blocks_into: dict[str, list[str]],
) -> bool:
    """Every upstream task is in {done, goal}.

    Unknown ids (no matching task in the store) are PERMISSIVE — same
    lenient stance as the graph-builder on unknown-id edges. The
    validator's ref-integrity check covers the consistency case.
    """
    for upstream_id in task.get("depends_on") or ():
        upstream_status = status_by_id.get(upstream_id)
        if upstream_status is None:
            continue  # unknown id — permissive
        if upstream_status not in RESOLVED_STATUSES:
            return False
    # blocks-side: tasks whose `blocks: [...]` mention this task's id.
    own_id = task.get("id")
    if own_id is not None:
        for upstream_id in blocks_into.get(own_id, ()):
            upstream_status = status_by_id.get(upstream_id)
            if upstream_status is None:
                continue
            if upstream_status not in RESOLVED_STATUSES:
                return False
    return True


def _sort_key(task: dict) -> tuple:
    """Same ordering as :func:`_next.next_task._sort_key` for parity."""
    priority = task.get("priority")
    priority_rank = 10_000_000 if priority is None else int(priority)
    last_activity = task.get("last_activity") or ""
    created_at = task.get("created_at") or ""
    return (
        priority_rank,
        _NegStr(last_activity),
        _NegStr(created_at),
        str(task.get("id") or ""),
    )


@dataclass(frozen=True)
class _NegStr:
    """Wraps a string to invert its lexical comparison (for DESC sorts)."""

    value: str

    def __lt__(self, other: "_NegStr") -> bool:  # type: ignore[override]
        return self.value > other.value

    def __eq__(self, other: object) -> bool:  # type: ignore[override]
        return isinstance(other, _NegStr) and self.value == other.value


# EOF
