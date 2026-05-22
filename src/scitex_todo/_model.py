#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Canonical task model + YAML loader/validator/writer for scitex-todo.

The task store is a YAML document with a top-level ``tasks:`` list. Each
task is a mapping with ``id`` + ``title`` + ``status`` (required) and
optional ``repo`` / ``depends_on`` / ``blocks`` / ``note`` / ``priority``
fields. ``priority`` is an explicit integer rank (lower = higher priority);
when absent, document order is the implicit ordering.

This module is the single validation gate: ``load_tasks`` raises
``TaskValidationError`` on a malformed store (missing id/title, duplicate
id, invalid status, non-integer priority) so downstream adapters can assume
well-formed input. ``save_tasks`` re-runs the same gate before writing back
and preserves the hand-written YAML comments + structure via ruamel.yaml.
"""

from __future__ import annotations

from pathlib import Path

import yaml

# Valid task statuses. ``goal`` marks a north-star objective (rendered gold);
# the rest are ordinary execution states.
VALID_STATUSES: tuple[str, ...] = (
    "goal",
    "pending",
    "in_progress",
    "blocked",
    "done",
    "deferred",
    "failed",
)


class TaskValidationError(ValueError):
    """Raised when a task store fails structural validation."""


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
    path = Path(path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"task store not found: {path}")

    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    tasks = data.get("tasks")
    _validate_tasks(tasks, source=str(path))
    return tasks


def _validate_tasks(tasks: object, source: str) -> None:
    """Validate a task list in place, raising on the first structural fault.

    The single gate shared by :func:`load_tasks` (read side) and
    :func:`save_tasks` (write side) so a bad mutation can never round-trip
    through the writer.

    Parameters
    ----------
    tasks : object
        The candidate ``tasks`` value (must be a list of mappings).
    source : str
        A label for error messages (the store path or ``"<save_tasks>"``).

    Raises
    ------
    TaskValidationError
        On any structural fault — see :func:`load_tasks`.
    """
    if not isinstance(tasks, list):
        raise TaskValidationError(f"{source}: top-level 'tasks' must be a list")

    seen: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict):
            raise TaskValidationError(
                f"{source}: each task must be a mapping: {task!r}"
            )
        tid = task.get("id")
        if not tid:
            raise TaskValidationError(
                f"{source}: a task is missing required 'id': {task!r}"
            )
        if tid in seen:
            raise TaskValidationError(f"{source}: duplicate task id {tid!r}")
        seen.add(tid)
        if not task.get("title"):
            raise TaskValidationError(
                f"{source}: task {tid!r} is missing required 'title'"
            )
        status = task.get("status")
        if status not in VALID_STATUSES:
            raise TaskValidationError(
                f"{source}: task {tid!r} has invalid status {status!r}; "
                f"must be one of {VALID_STATUSES}"
            )
        priority = task.get("priority")
        # bool is an int subclass — reject it explicitly so `priority: true`
        # is a clear error rather than a silent 1.
        if priority is not None and (
            isinstance(priority, bool) or not isinstance(priority, int)
        ):
            raise TaskValidationError(
                f"{source}: task {tid!r} has non-integer priority {priority!r}; "
                f"priority must be an integer or absent"
            )


def save_tasks(tasks: list[dict], path: str | Path) -> None:
    """Validate then write a task list back to a YAML store, preserving comments.

    Re-runs the same validation gate as :func:`load_tasks` *before* touching
    disk, so a malformed mutation can never corrupt the store. Uses
    ``ruamel.yaml`` round-trip mode so hand-written comments and key layout in
    the existing store survive the rewrite.

    Parameters
    ----------
    tasks : list of dict
        The (already-mutated) task mappings to persist. Validated first.
    path : str or pathlib.Path
        Destination store. If it already exists, its comments + structure are
        preserved and only the ``tasks:`` payload is updated; otherwise a
        fresh document is written.

    Raises
    ------
    TaskValidationError
        If ``tasks`` fails structural validation (nothing is written).

    Examples
    --------
    >>> tasks = load_tasks("tasks.yaml")          # doctest: +SKIP
    >>> tasks[0]["priority"] = 1                    # doctest: +SKIP
    >>> save_tasks(tasks, "tasks.yaml")            # doctest: +SKIP
    """
    from ruamel.yaml import YAML

    path = Path(path).expanduser()
    _validate_tasks(tasks, source="<save_tasks>")

    yaml_rt = YAML()
    yaml_rt.preserve_quotes = True
    # Match the bundled store's hand layout (two-space block indent, lists
    # indented under their key) so a round-trip is a minimal diff.
    yaml_rt.indent(mapping=2, sequence=4, offset=2)

    existing_doc = None
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            loaded = yaml_rt.load(handle)
        if isinstance(loaded, dict):
            existing_doc = loaded

    if existing_doc is not None:
        # Merge the caller's task data into the round-trip-loaded structure by
        # id, so per-item and inline comments attached to the original nodes
        # survive. New ids are appended; removed ids are dropped.
        doc = existing_doc
        old_seq = doc.get("tasks") if isinstance(doc.get("tasks"), list) else []
        old_by_id = {t["id"]: t for t in old_seq if isinstance(t, dict) and t.get("id")}
        merged = _merge_tasks_into_seq(tasks, old_by_id)
        doc["tasks"] = merged
    else:
        # No existing store (or a non-mapping top level): write a fresh doc.
        doc = {"tasks": tasks}

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        yaml_rt.dump(doc, handle)


def _merge_tasks_into_seq(tasks: list[dict], old_by_id: dict) -> list:
    """Build the new task sequence, reusing comment-bearing old nodes by id.

    For each task in ``tasks``: if an old node with the same id exists, mutate
    that node (so its attached comments survive) by syncing keys to the new
    data; otherwise use the new mapping as-is. Order follows ``tasks``.
    """
    merged: list = []
    for task in tasks:
        old = old_by_id.get(task.get("id"))
        if old is None:
            merged.append(task)
            continue
        # Sync the old comment-bearing node's keys to the new values.
        for key, value in task.items():
            old[key] = value
        for stale_key in [k for k in list(old.keys()) if k not in task]:
            del old[stale_key]
        merged.append(old)
    return merged


# EOF
