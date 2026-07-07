#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Task-store path resolution following the SciTeX local-state convention.

``tasks.yaml`` is a mutable **DATA store** (the canonical record), so it is
USER-CANONICAL: it is NEVER shadowed by a project-scoped file. This is the
ecosystem config-vs-data rule (scitex-config ``local_state``, confirmed by
scitex-dev 2026-07-06): CONFIG may be project-overridden, but a mutable data
store must resolve to the same user file regardless of the working directory.

Resolution order (highest priority first):

    1. an explicit path argument (CLI ``--tasks`` / function arg)
    2. ``$SCITEX_TODO_TASKS_YAML_SHARED`` environment variable
    3. user scope:  ``$SCITEX_DIR/todo/tasks.yaml`` (default ``~/.scitex/todo``)
    4. bundled generic example:  ``scitex_todo/examples/tasks.yaml``

There is DELIBERATELY no project-scope (``<git-root>/.scitex/todo/tasks.yaml``)
layer for the data store: a process run with cwd inside ANY repo (notably
scitex-todo's OWN deploy checkout) must reach the same canonical store, never a
stale per-repo copy. (Incident 2026-07-06: the board, run from
``~/proj/scitex-todo``, silently read a week-stale project ``tasks.yaml`` that
shadowed the canonical user store.) The reminders ``config.yaml`` — a CONFIG,
not data — DOES keep its project-override layer in :mod:`scitex_todo._config`.

The personal data lives under scope 3 — never in the package. The bundled
example (scope 4) is generic and exists only so a fresh install can demo
end-to-end. ``$SCITEX_DIR`` relocates the user-scope root per the ecosystem
convention. A future PR adopts ``scitex_config.local_state.user_path`` as the
shared resolver (scitex-dev's carded standard).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

#: package short name (``scitex-todo`` with the ``scitex-`` prefix stripped).
PKG_SHORT = "todo"

#: env var that overrides the resolved task-store path entirely. The name
#: encodes that it points at the SHARED yaml store.
ENV_TASKS = "SCITEX_TODO_TASKS_YAML_SHARED"

#: previous name of :data:`ENV_TASKS`. Renamed 2026-07-02. The CURRENT var
#: wins: when ``$SCITEX_TODO_TASKS_YAML_SHARED`` is set we IGNORE a stale export
#: of this old name (loud warning, no raise). We fail LOUD only when the current
#: var is NOT set AND this old name is still set — a genuine reliance on the
#: renamed-away var the operator must migrate.
ENV_TASKS_DEPRECATED = "SCITEX_TODO_TASKS"


def _reject_deprecated_env() -> None:
    """Handle a leftover export of the old ``SCITEX_TODO_TASKS`` var.

    The CURRENT var wins. If ``$SCITEX_TODO_TASKS_YAML_SHARED`` is set, the
    stale old name is IGNORED with a loud warning (no raise) so a correctly
    configured store is not disabled by a leftover export. We fail loud ONLY
    when the current var is absent AND the old name is still set.
    """
    if os.environ.get(ENV_TASKS_DEPRECATED) is None:
        return
    if os.environ.get(ENV_TASKS) is not None:
        logger.warning(
            "%s is set but was renamed to %s; the stale value is IGNORED in "
            "favor of the current %s. Unset %s to silence this warning.",
            ENV_TASKS_DEPRECATED,
            ENV_TASKS,
            ENV_TASKS,
            ENV_TASKS_DEPRECATED,
        )
        return
    raise RuntimeError(
        f"{ENV_TASKS_DEPRECATED} was renamed to {ENV_TASKS}; "
        f"unset the old var and set {ENV_TASKS} instead "
        f"(the old name is no longer honoured)."
    )


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

    _reject_deprecated_env()
    env_val = os.environ.get(ENV_TASKS)
    if env_val:
        return Path(env_val).expanduser()

    # DATA store = USER-CANONICAL. NO project-scope layer here (see the module
    # docstring / the 2026-07-06 stale-store incident): the store must resolve
    # to the same user file from any working directory, never a per-repo copy.
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
