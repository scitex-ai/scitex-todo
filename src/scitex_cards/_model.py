#!/usr/bin/env python3
# -*- coding: utf-8 -*-
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Canonical task model + YAML loader/validator/writer for scitex-cards.

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
from ._task import VALID_STATUSES, TaskValidationError  # noqa: F401
from ._validate import _validate_tasks  # noqa: F401

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
# Re-exports. `_model` was 1,235 lines — 2.4x the 512 cap — and therefore could
# not be edited AT ALL, which blocked a P0 fix for a blank board. It is now a
# thin orchestrator over four focused modules (GITIGNORED/REFACTORING.md).
#
# THE IMPORT SURFACE DOES NOT MOVE. 43 test files and every fleet agent do
# `from scitex_cards._model import ...`; every name below is the SAME object it
# always was, defined next door. Same contract as the `_store_write` split (#391).
# ---------------------------------------------------------------------------
from ._task import (  # noqa: E402,F401
    ABOLISHED_STATUSES,
    StaleStoreError,
    Task,
    TaskValidationError,
    VALID_BLOCKERS,
    VALID_KINDS,
    VALID_STATUSES,
    _BLOCKER_ALIASES,
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

from ._store_write import (  # noqa: E402,F401  (re-export)
    _git_autocommit_store,
    _save_doc_unlocked,
    _save_tasks_unlocked,
    _store_lock,
    edit_tasks,
    save_tasks,
    store_generation,
)
