#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Canonical task model + YAML loader/validator for scitex-todo.

The task store is a YAML document with a top-level ``tasks:`` list. Each
task is a mapping with ``id`` + ``title`` + ``status`` (required) and
optional ``repo`` / ``depends_on`` / ``blocks`` / ``note`` fields.

This module is the single validation gate: ``load_tasks`` raises
``TaskValidationError`` on a malformed store (missing id/title, duplicate
id, invalid status) so downstream adapters can assume well-formed input.
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
        task is missing ``id`` or ``title``, an ``id`` is duplicated, or a
        ``status`` is not in :data:`VALID_STATUSES`.

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
    if not isinstance(tasks, list):
        raise TaskValidationError(f"{path}: top-level 'tasks' must be a list")

    seen: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict):
            raise TaskValidationError(f"{path}: each task must be a mapping: {task!r}")
        tid = task.get("id")
        if not tid:
            raise TaskValidationError(
                f"{path}: a task is missing required 'id': {task!r}"
            )
        if tid in seen:
            raise TaskValidationError(f"{path}: duplicate task id {tid!r}")
        seen.add(tid)
        if not task.get("title"):
            raise TaskValidationError(
                f"{path}: task {tid!r} is missing required 'title'"
            )
        status = task.get("status")
        if status not in VALID_STATUSES:
            raise TaskValidationError(
                f"{path}: task {tid!r} has invalid status {status!r}; "
                f"must be one of {VALID_STATUSES}"
            )
    return tasks


# EOF
