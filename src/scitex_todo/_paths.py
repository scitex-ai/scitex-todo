#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Task-store path resolution following the SciTeX local-state convention.

Resolution order (highest priority first):

    1. an explicit path argument (CLI ``--tasks`` / function arg)
    2. ``$SCITEX_TODO_TASKS`` environment variable
    3. project scope:  ``<git-root>/.scitex/todo/tasks.yaml``
    4. user scope:     ``$SCITEX_DIR/todo/tasks.yaml`` (default ``~/.scitex/todo``)
    5. bundled generic example:  ``scitex_todo/examples/tasks.yaml``

The personal data lives under scopes 3 and 4 — never in the package. The
bundled example (scope 5) is generic and exists only so a fresh install can
demo end-to-end. ``$SCITEX_DIR`` relocates the user-scope root per the
ecosystem convention.
"""

from __future__ import annotations

import os
from pathlib import Path

#: package short name (``scitex-todo`` with the ``scitex-`` prefix stripped).
PKG_SHORT = "todo"

#: env var that overrides the resolved task-store path entirely.
ENV_TASKS = "SCITEX_TODO_TASKS"


def _user_root() -> Path:
    """User-scope ``.scitex/todo`` root, honouring ``$SCITEX_DIR``."""
    base = os.environ.get("SCITEX_DIR")
    root = Path(base).expanduser() if base else Path.home() / ".scitex"
    return root / PKG_SHORT


def _find_git_root(start: Path) -> Path | None:
    """Walk up from ``start`` looking for a ``.git`` directory."""
    cur = start.resolve()
    for parent in (cur, *cur.parents):
        if (parent / ".git").exists():
            return parent
    return None


def bundled_example() -> Path:
    """Path to the generic example task store shipped inside the wheel."""
    return Path(__file__).resolve().parent / "examples" / "tasks.yaml"


def resolve_tasks_path(explicit: str | Path | None = None) -> Path:
    """Resolve which task store to use, following the precedence chain.

    Parameters
    ----------
    explicit : str or pathlib.Path or None
        An explicit path (e.g. a CLI ``--tasks`` flag). When given and it
        exists, it wins outright.

    Returns
    -------
    pathlib.Path
        The first existing task store in precedence order. Falls back to the
        bundled generic example if no personal store is found.

    Examples
    --------
    >>> p = resolve_tasks_path()           # doctest: +SKIP
    >>> p.name                              # doctest: +SKIP
    'tasks.yaml'
    """
    if explicit is not None:
        cand = Path(explicit).expanduser()
        if cand.exists():
            return cand
        # An explicit-but-missing path is a user error — surface it as-is so
        # the loader raises a clear FileNotFoundError on that path.
        return cand

    env_val = os.environ.get(ENV_TASKS)
    if env_val:
        return Path(env_val).expanduser()

    git_root = _find_git_root(Path.cwd())
    if git_root is not None:
        project = git_root / ".scitex" / PKG_SHORT / "tasks.yaml"
        if project.exists():
            return project

    user = _user_root() / "tasks.yaml"
    if user.exists():
        return user

    return bundled_example()


#: Subdirectory of the store dir holding NON-git-tracked runtime state
#: (pidfiles, the delivery ledger, the reminder sidecar). scitex convention:
#: runtime state lives under ``runtime/`` (gitignored), never scattered in the
#: store root. Superseded files go to ``.old/<timestamp>/`` instead.
RUNTIME_DIRNAME = "runtime"


def runtime_dir(store: str | Path | None = None, *, create: bool = True) -> Path:
    """Return ``<store_dir>/runtime`` — the home for non-tracked runtime state.

    ``<store_dir>`` is the parent of the resolved task store, so the runtime
    dir tracks whichever scope the store resolved to. Created on demand
    (``create=True``) so callers can write into it without a prior mkdir.
    """
    d = resolve_tasks_path(store).parent / RUNTIME_DIRNAME
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


# EOF
