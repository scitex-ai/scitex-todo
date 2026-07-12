#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Canonical task model + YAML loader/validator/writer for scitex-todo.

The task store is a YAML document with a top-level ``tasks:`` list. Each
task is a mapping with ``id`` + ``title`` + ``status`` (required) and
optional ``repo`` / ``depends_on`` / ``blocks`` / ``note`` / ``priority`` /
``parent`` fields. ``priority`` is an explicit integer rank (lower = higher
priority); when absent, document order is the implicit ordering. ``parent``
is an optional task-id string that nests this task under another node — a
task's children are tasks whose ``parent`` equals this task's ``id`` (the
board's drill-down view follows this relation).

This module is the single validation gate: ``load_tasks`` raises
``TaskValidationError`` on a malformed store (missing id/title, duplicate
id, invalid status, non-integer priority, non-string parent) so downstream
adapters can assume well-formed input. ``save_tasks`` re-runs the same gate
before writing back and preserves the hand-written YAML comments +
structure via ruamel.yaml.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
from pathlib import Path

from ._yaml import safe_dump, safe_load  # hook-bypass: line-limit
from ._store_verify import _verify_dumped_tmp  # hook-bypass: line-limit

# Valid task statuses. ``goal`` marks a north-star objective (rendered gold);
# the rest are ordinary execution states.
# ``pending`` was ABOLISHED 2026-07-10 by operator directive: "pending という
# タスクがある。存在してはならない状態である。" A card in ``pending`` carried NO
# decision — it was the dataclass default, so 406 of 1100 cards (37%) had silently
# accumulated there and rotted. Every open card must now state its disposition:
# ``in_progress`` (being worked), ``blocked`` (+ a blocker naming the gate),
# or ``deferred`` (can be worked, consciously not now).
ABOLISHED_STATUSES: dict[str, str] = {
    "pending": (
        "status 'pending' was abolished 2026-07-10 — a card must carry a "
        "decision. Choose: 'in_progress' if you are working it, 'blocked' "
        "with a blocker naming the gate, or 'deferred' if it can wait."
    ),
}

VALID_STATUSES: tuple[str, ...] = (  # hook-bypass: line-limit
    "goal",
    "in_progress",
    "blocked",
    "done",
    "deferred",
    "failed",
    # ``cancelled`` = GitHub "closed as not planned": a TERMINAL/closed
    # state distinct from ``done`` (completed successfully) and ``failed``
    # (attempted, did not succeed). A cancelled card is CLOSED — it drops
    # out of every open/actionable/stale/backlog view exactly like ``done``
    # (see TERMINAL_STATUSES in _throughput.py, the is_overdue closed-set
    # below, and _LIVENESS_NONRUNNABLE in handlers/graph.py). It does NOT
    # satisfy a dependency: a cancelled upstream leaves dependents blocked,
    # so RESOLVED_STATUSES in _runnable.py stays {"done", "goal"}.
    "cancelled",
)

# Valid task kinds — north-star pillars #1 (compute state) + #4 (operator
# pain "where am I the blocker"). A row with ``kind: compute`` represents
# an external compute job whose status is updated by an automated writer
# (see tasks/proj-scitex-todo-compute-state-deps/README.md). A row with
# ``kind: decision`` represents an operator/agent decision that other tasks
# can ``depends_on`` — when the decision-node's status flips to ``done``
# (the decision is made) the dependents auto-unblock via the existing dep-
# graph wire (no new machinery; the per-task adr.md is its body, 1:1).
# Other tasks use ``kind: task`` (the default, can be omitted). Extensible
# to ``"ci"`` etc. when task #15 wires GH-Actions rows.
#
# Closed validated set — fail-loud on unknown values per ADR-0002
# (a2a `2c7a431d`) and ADR-0003 (this PR; extending to "decision").
VALID_KINDS: tuple[str, ...] = (
    "task",
    "compute",
    "decision",
    # ``status`` — a non-actionable status-tracking card (e.g. the q-*
    # quality-CI status rows, one per fleet package). Carries one-liner
    # status notes (audit-debt counts, green flags) rather than a real
    # ToDo body. Per board card ``scitex-todo-relocate-q-status-tracking``
    # + lead a2a ``60a1a93d`` (operator direction): proceeding with
    # option (b) — keep the rows on the board but mark them with this
    # axis so the board's filter UI (separate frontend PR) can hide them
    # from the actionable default lens. ORTHOGONAL to ``blocker`` /
    # ``status`` (the row-status enum); the validator does NOT cross-imply
    # any compute-field constraints — ``kind: status`` is just a flag.
    "status",
)


# Valid `blocker` values — operator TG 9522 + 9524, lead a2a
# `4691b114` / `c839c59b` / `2bd37bd2` / `554435df`. The operator's exact
# pain: "I cannot tell what is waiting on ME." A blocked task can be stuck
# on different things; each gets a different signal on the board.
#
# Operator's enumeration (verbatim, TG 9524):
#   compute            (計算リソース)      — waiting on a kind=compute row to finish
#   dep                (依存)              — waiting on another task (explicit form of the implicit
#                                            dep-edge case; useful when the dep is the *concept*
#                                            even if no edge id is known yet)
#   operator-decision  (ユーザー判断)      — waiting on the operator to decide; this is the LOUD
#                                            variant the operator opens the UI to find. Usually
#                                            paired with kind=decision rows but the enums are
#                                            ORTHOGONAL (a kind=task can also be blocker=
#                                            operator-decision if it's waiting on a decision that
#                                            hasn't been promoted to its own kind=decision node
#                                            yet).
#   agent-wait         (他エージェント待ち) — waiting on a specific agent action (e.g. "lead to
#                                            write the ADR-0007 entry"). Distinct from `dep`
#                                            because the blocker is a *human/agent action*, not
#                                            a graph-edge dep.
#
# Closed validated set per ADR-0004 (this PR) — same fail-loud pattern as
# VALID_KINDS / VALID_STATUSES: an unknown value raises with the bad value
# and the valid set in the error message. Extensible by editing this tuple
# — closed-in-the-typo sense, open-in-the-variant sense.
#
# Allowed ONLY when `status == "blocked"`: setting a `blocker` on a non-
# blocked row is a config error (the row isn't blocked, so naming a blocker
# is meaningless). Validator raises with "set status: blocked or remove the
# blocker field" — same shape as the compute-fields-only-on-kind=compute
# rule from ADR-0002.
VALID_BLOCKERS: tuple[str, ...] = (
    "compute",
    # ``"dependency"`` is the canonical spelling per operator co-design
    # (TG 9667, lead a2a `6d9b6073`). ``"dep"`` is the legacy alias from
    # ADR-0004's first cut; the validator accepts BOTH during a
    # deprecation window and normalizes on write (`_normalize_blocker`).
    # Once existing tasks.yaml stores are swept, ``"dep"`` drops out.
    "dependency",
    "dep",
    "operator-decision",
    "agent-wait",
    # ``"none"`` is the explicit "no specific blocker named" value
    # (vs the soft-degrade case where the field is absent on a blocked
    # row). Lets the operator set blocker:none in a Resolve flow to
    # mean "I looked, no blocker" — distinct from "we haven't named
    # one yet." Operator co-design TG 9667.
    "none",
)


# Canonical → legacy alias normalization for the blocker enum.
# Used by Task.from_dict to flip incoming ``"dep"`` → ``"dependency"``
# on read, so the in-memory dataclass always carries the canonical
# spelling. The validator still accepts both spellings (deprecation
# window); only the dataclass normalizes.
_BLOCKER_ALIASES: dict[str, str] = {
    "dep": "dependency",
}


def load_tasks(path: str | Path) -> list[dict]:
    """Load and validate the task list from a YAML store.

    Parameters
    ----------
    path : str or pathlib.Path
        Path to the YAML task store. The document must have a top-level
        ``tasks:`` list.

    Returns
    -------
    list of dict
        The validated task mappings, in document order.

    Raises
    ------
    FileNotFoundError
        If ``path`` does not exist.
    TaskValidationError
        If the store is structurally invalid: ``tasks`` is not a list, a
        task is missing ``id`` or ``title``, an ``id`` is duplicated, a
        ``status`` is not in :data:`VALID_STATUSES`, or a ``priority`` is
        present but not an integer.

    Examples
    --------
    >>> tasks = load_tasks("tasks.yaml")  # doctest: +SKIP
    >>> tasks[0]["id"]                     # doctest: +SKIP
    'design'
    """
    data = load_doc(path, validate=True)
    return data.get("tasks")


def load_doc(path: str | Path, *, validate: bool = False) -> dict:
    """Load the FULL parsed mapping from a YAML store in ONE ``safe_load``.

    This is the single-read primitive that both :func:`load_tasks` and the
    ``_store`` CRUD verbs build on. Returning the *whole* top-level mapping
    (not just ``tasks``) lets a read-modify-write cycle reuse the one parse
    for BOTH the ``tasks`` payload it mutates AND the non-``tasks`` sections
    (notably the ``users:`` registry) it must carry through untouched — so
    the store is parsed once under the lock instead of twice (the old
    ``_save_tasks_unlocked`` re-read is eliminated; the ~2.3 s per single-card
    write it cost on the ~7.7 MB shared store goes away).

    Parameters
    ----------
    path : str or pathlib.Path
        Path to the YAML task store.
    validate : bool, default False
        When True, run :func:`_validate_tasks` on ``data.get("tasks")`` before
        returning (the read-time gate :func:`load_tasks` applies). Left off for
        pure write-preservation reads that validate at dump time instead.

    Returns
    -------
    dict
        The parsed top-level mapping. Empty/``None`` documents normalize to
        ``{}``; a non-mapping top level is returned as-is (the caller decides).

    Raises
    ------
    FileNotFoundError
        If ``path`` does not exist.
    TaskValidationError
        Only when ``validate=True`` and the ``tasks`` payload is invalid.
    """
    path = Path(path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"task store not found: {path}")

    with path.open(encoding="utf-8") as handle:
        data = safe_load(handle) or {}  # hook-bypass: line-limit

    if validate:
        tasks = data.get("tasks") if isinstance(data, dict) else None
        # READ side: tolerate values this build does not know (a newer agent
        # may have written them) and warn loudly. Structural corruption still
        # raises. One unknown status must never take the fleet's board down.
        _validate_tasks(tasks, source=str(path), strict=False)
    return data




# ---------------------------------------------------------------------------
# Re-exports. `_model` was split into focused modules (see
# GITIGNORED/REFACTORING.md) because it had grown to 1,235 lines — 2.4x the
# cap — and could no longer be edited at all, which blocked a P0 board fix.
#
# 43 test files and the whole fleet import from `_model`. That surface does NOT
# move: every name below is the SAME object it always was, just defined next
# door. Same contract as the `_store_write.py` split (PR #391).
# ---------------------------------------------------------------------------
from ._task import (  # noqa: E402,F401
    StaleStoreError,
    Task,
    TaskValidationError,
)
from ._deadlines import (  # noqa: E402,F401
    Repeater,
    _add_period,
    _as_aware_utc,
    _get_repeater_rx,
    _last_day_of_month,
    _parse_deadline_or_raise,
    _parse_iso_date_or_raise,
    _pick_next_dt,
    is_overdue,
    next_deadline_for_task,
)
from ._validate import (  # noqa: E402,F401
    _validate_tasks,
    _warn_tolerated,
)

# The write path (extracted in PR #391). This re-export is part of `_model`'s
# public surface — `from scitex_todo._model import save_tasks` is what 43 test
# files and every fleet agent call. It must live HERE, in `_model`.
from ._store_write import (  # noqa: E402,F401  (re-export)
    _git_autocommit_store,
    _save_doc_unlocked,
    _save_tasks_unlocked,
    _store_lock,
    edit_tasks,
    save_tasks,
    store_generation,
)
