#!/usr/bin/env python3
# -*- coding: utf-8 -*-
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

from ._store_verify import _verify_dumped_tmp  # hook-bypass: line-limit
from ._task import VALID_STATUSES, TaskValidationError  # noqa: F401
from ._validate import _validate_tasks  # noqa: F401
from ._yaml import safe_dump, safe_load  # hook-bypass: line-limit


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

    # DB-CANONICAL: read the doc FROM SQLITE, not from a file that no longer
    # exists. This is not an optimisation — it is what makes the mode safe.
    #
    # WITHOUT IT, EVERY WRITE ERASES THE BOARD, and the mechanism is worth
    # spelling out because it is silent and total. The CRUD verbs are
    # read-modify-write: they call this function, mutate `doc["tasks"]`, and
    # hand the whole doc to the writer. If this still read the (absent) YAML it
    # would return `{}`, so the "modify" step would build a document holding
    # ONLY the new card, and `mirror_doc_incremental` — which diffs the doc
    # against the DB and deletes what is missing — would remove every other
    # card. Measured on a scratch store during the cutover: writing a second
    # card left exactly one row. On the live board that is 2065 cards down to 1,
    # with no error raised anywhere.
    try:
        from ._store_backend import db_is_canonical
    except Exception:  # noqa: BLE001 — undecidable means "not canonical"
        db_is_canonical = None
    if db_is_canonical is not None and db_is_canonical():
        from ._db_export import export_doc

        data = export_doc(None)[0] or {}
        if validate:
            _validate_tasks(
                data.get("tasks") if isinstance(data, dict) else None,
                source=f"<sqlite:{path}>",
                strict=False,
            )
        return data

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
from ._task import (  # noqa: E402,F401
    _BLOCKER_ALIASES,
    ABOLISHED_STATUSES,
    VALID_BLOCKERS,
    VALID_KINDS,
    StaleStoreError,
    Task,
)
from ._validate import (  # noqa: E402,F401
    _warn_tolerated,
)

# ---------------------------------------------------------------------------
# `_store_write` re-exports — LAZY, and the laziness is load-bearing.
#
# `_store_write` imports FROM `_model` (StaleStoreError, load_doc, ...), so an
# eager `from ._store_write import ...` here closes an import CYCLE that only
# survives in ONE direction:
#
#   import scitex_cards._model         -> _model runs to here, pulls in
#                                        _store_write, which imports _model back
#                                        — already in sys.modules with everything
#                                        above this line bound. FINE.
#
#   import scitex_cards._store_write   -> _store_write runs to its line 42, pulls
#                                        in _model, which reaches HERE and asks
#                                        _store_write for names it has not defined
#                                        yet (it is only 42 lines in).
#                                        ImportError. NOT FINE.
#
# So the package worked only because nothing ever imported `_store_write` first
# — every real path reaches it through `_model` or the public API. That is luck,
# not design, and it broke a live data-repair script on 2026-07-14: an external
# caller whose FIRST scitex_cards import was `from scitex_cards._store_write import
# edit_tasks` got an ImportError blaming a circular import rather than their call.
#
# PEP 562 module __getattr__ defers the import to first ATTRIBUTE ACCESS, by
# which time both modules are fully initialised. `from scitex_cards._model import
# save_tasks` still works — `from X import Y` falls back to X.__getattr__.
#
# DO NOT "simplify" this back to a top-level import. The cycle is real; this is
# what breaks it. tests/scitex_cards/test__import_order.py imports each module
# first IN A SUBPROCESS and fails if any order raises.
# ---------------------------------------------------------------------------
_STORE_WRITE_EXPORTS = frozenset(
    {
        "_git_autocommit_store",
        "_save_doc_unlocked",
        "_save_tasks_unlocked",
        "_store_lock",
        "edit_tasks",
        "save_tasks",
        "store_generation",
    }
)


def __getattr__(name: str):
    """Resolve the `_store_write` re-exports on first access (PEP 562)."""
    if name in _STORE_WRITE_EXPORTS:
        from . import _store_write

        value = getattr(_store_write, name)
        globals()[name] = value  # cache: __getattr__ runs once per name
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted([*globals(), *_STORE_WRITE_EXPORTS])
