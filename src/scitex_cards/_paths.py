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

There is NO FOURTH TIER. A bundled ``scitex_cards/examples/tasks.yaml`` used to
sit at the end of this chain, and that made a packaged demo file eligible to
become the fleet's board — which it did on 2026-07-19, when the canonical store
was archived by the SQLite cutover and resolution settled on a file inside
site-packages. An unresolvable store now returns the canonical path that does
not exist, so the loader raises ``FileNotFoundError`` on it: a stated
configuration error rather than a blank board to start writing into.

There is DELIBERATELY no project-scope (``<git-root>/.scitex/todo/tasks.yaml``)
layer for the data store: a process run with cwd inside ANY repo (notably
scitex-todo's OWN deploy checkout) must reach the same canonical store, never a
stale per-repo copy. (Incident 2026-07-06: the board, run from
``~/proj/scitex-todo``, silently read a week-stale project ``tasks.yaml`` that
shadowed the canonical user store.) The reminders ``config.yaml`` — a CONFIG,
not data — DOES keep its project-override layer in :mod:`scitex_cards._config`.

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

#: package short name (``scitex-cards`` with the ``scitex-`` prefix stripped).
#: It names the user-scope directory: ``~/.scitex/<PKG_SHORT>``.
#:
#: WAS "todo" until 2026-07-19 — the rename landed everywhere EXCEPT here, and
#: this one stale word blocked every card write for any process without an
#: explicit store variable. Measured before the change:
#:
#:     resolve_tasks_path(None) -> ~/.scitex/todo/tasks.yaml   (this default)
#:     the live DB is stamped   -> ~/.scitex/cards/tasks.yaml
#:     add_task(...)            -> RuntimeError, write REFUSED
#:
#: #509's guard was correct to refuse: writing a store whose identity disagrees
#: with the database is exactly the fork that destroyed 2142 cards that morning.
#: The bug was that the compiled-in DEFAULT pointed at a path the database
#: disowns, so the guard fired on the healthy case.
#:
#: MIGRATION, checked rather than assumed before changing this: ~/.scitex/cards
#: held the real store, its backups and archives (52 entries); ~/.scitex/todo
#: held one leftover `runtime` directory and no store. So this moves the default
#: ONTO the data rather than away from it, and it makes the default agree with
#: the DB stamp instead of contradicting it. An installation that genuinely kept
#: a store under ~/.scitex/todo must set $SCITEX_CARDS_TASKS_YAML_SHARED, which
#: has always won outright over this default.
PKG_SHORT = "cards"

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
    """REMOVED — no YAML task store ships inside the wheel any more.

    This returned ``scitex_cards/examples/tasks.yaml`` and
    :func:`resolve_tasks_path` used it as the LAST RESORT of the precedence
    chain, which made a packaged fixture eligible to become the fleet's board.
    That is not hypothetical: on 2026-07-19, with the canonical store archived
    by the SQLite cutover, resolution walked past every real candidate and
    settled here — and the live database's provenance stamp was rewritten to
    name a file inside ``site-packages``.

    A fallback that can silently promote demo data to production data is not a
    convenience, it is a trap, and it is exactly the shape the operator ruled
    out: 「YAML カードなんて examples からも捨てろ」. Kept as a raising stub so an
    external caller gets a stated reason rather than an ImportError.
    """
    raise RuntimeError(
        "there is no bundled example task store any more (removed 2026-07-19). "
        "The store is SQLite; a YAML fixture must never be resolvable as the "
        "board. Set $SCITEX_CARDS_DB, or bootstrap explicitly with "
        "`scitex-cards db import --from-yaml <file>`."
    )


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
        The first existing task store in precedence order. There is NO bundled
        fallback: when nothing resolves, the user path is returned so the loader
        raises ``FileNotFoundError`` on it rather than silently nominating a
        packaged fixture as the fleet's board.

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

    # NO FALLBACK. Returning the bundled example here is what let a packaged
    # fixture be nominated as the fleet's board (see :func:`bundled_example`).
    # An unresolvable store is a configuration error the caller must see, not a
    # blank board to start writing into — the loader raises FileNotFoundError
    # on this path, which is the honest answer.
    return user


def refuse_ambient_store_creation(
    resolved: str | Path, explicit: str | Path | None = None
) -> None:
    """Refuse to MANUFACTURE a board at a path nobody named.

    A write against a store that does not exist is ambiguous: it is either
    "first run, please bootstrap" or "I resolved the wrong path". Answering it
    by CREATING the store always assumes the first, and the second is the one
    that costs a board.

    Measured 2026-07-20, before this guard::

        store path: <somewhere>/tasks.yaml     (does not exist)
        add_task(id="decoy-card", ...)         -> returned normally
        filesystem after                       -> a 241-byte one-card store

    That is how a decoy accumulates. Three sac cron jobs, each reading
    ``FileNotFoundError`` as "no such card yet" and calling ``add_task``,
    grew a five-card document at an ambiently-resolved path; the hourly
    ``db snapshot --refresh`` then imported it as canonical and reconcile
    deleted the 2160 cards absent from it. The conflation was sac's; the
    manufacturing was OURS, and either half alone breaks the chain.

    So: an EXPLICIT destination may be created (a caller that names a path has
    stated its intent, and that is how tests, imports and deliberate bootstraps
    work). An AMBIENT one may not — nothing named it, so a missing file there
    is far more likely to mean the resolution is wrong than that the fleet has
    no board yet.

    Parameters
    ----------
    resolved : str or pathlib.Path
        The store path the write is about to be performed against.
    explicit : str or pathlib.Path or None
        The caller's explicit destination, if any. When given, creation is
        permitted — naming the path IS the opt-in.

    Raises
    ------
    RuntimeError
        When ``resolved`` does not exist and nothing named it.
    """
    path = Path(resolved)
    if path.exists() or explicit is not None or os.environ.get(ENV_TASKS):
        return
    raise RuntimeError(
        f"REFUSING to create a task store at {path}: it does not exist, and "
        f"nothing named it — the path came from the ambient default "
        f"(~/.scitex/{PKG_SHORT}/tasks.yaml). Writing here would MANUFACTURE a "
        f"new board containing only this one card, which then looks like a "
        f"real store to anything that imports it.\n"
        f"If you meant to write to the fleet board, your store resolution is "
        f"wrong: set ${ENV_TASKS} to the real store, or pass the path "
        f"explicitly.\n"
        f"If you genuinely want a NEW empty board here, create it deliberately "
        f"first: `scitex-cards db export --out {path}` from the store you want "
        f"to seed from, or `touch {path}` for a truly blank one."
    )


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
