#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""THE TEST SUITE CANNOT REACH THE LIVE STORE. Enforced here, not by discipline.

On 2026-07-19 this suite rebuilt the fleet's production database from its own
fixtures THREE TIMES in one session:

    2,136 cards -> 21     (mirror write path)
    2,138 cards -> 1      (canonical write path)
    2,138 cards -> 3      (canonical read path, via one `comment_task`)

All three were recovered from the snapshot repo's git history. All three had
the same enabling condition, and it is not any of the three bugs that were
fixed afterwards: **a test that never sets ``$SCITEX_CARDS_DB``**. With the
variable unset, ``resolve_db_path(None)`` walks its precedence chain to the
user-canonical path — which IS the real board — and every in-code ownership
guard then sees a perfectly legitimate write to the store it was told to use.
No guard can refuse that, because from inside the code there is nothing wrong
with it.

So the barrier belongs HERE, in the harness, above the code under test. A rule
enforced inside the thing being tested cannot bound the damage that thing can
do; a rule in the harness cannot be reached by any future change to resolution
order, precedence, backend selection, or env compat.

WHY ``autouse`` + ``session`` + ``os.environ`` RATHER THAN ``monkeypatch``:
the pinning must be in place before the first test imports ``scitex_cards``
(``_env_compat.mirror_env()`` runs at import time and reads the environment),
and it must also be inherited by SUBPROCESSES — the concurrency tests pass
``env=os.environ.copy()`` to real child processes, which is precisely how the
first wipe happened. ``monkeypatch`` is per-test and would leave the gap open
during collection and in any test that forgets it.

Per-test overrides still work exactly as before: a test that sets ``ENV_DB``
via ``monkeypatch.setenv`` shadows this for its own duration. This fixture only
supplies a SAFE DEFAULT where there previously was a dangerous one.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

#: Every env name that can point the package at a store. All are pinned, so a
#: half-applied rename cannot leave one of them aimed at the live board.
_STORE_ENV_VARS = (
    "SCITEX_CARDS_DB",
    "SCITEX_TODO_DB",
    "SCITEX_CARDS_TASKS_YAML_SHARED",
    "SCITEX_TODO_TASKS_YAML_SHARED",
)

#: Env names that select WHICH BACKEND is canonical. These are CLEARED, not
#: pinned. Pinning the store path while inheriting the backend selector makes
#: the suite's behaviour depend on the developer's shell: a maintainer who has
#: exported SCITEX_CARDS_STORE_BACKEND=sqlite (as anyone working the cutover
#: does) flips every test into DB-canonical mode against a scratch DB that was
#: never created, and they all fail with "canonical store ... does not exist".
#: A test that WANTS canonical mode sets this itself; the default must be the
#: same everywhere.
_BACKEND_ENV_VARS = (
    "SCITEX_CARDS_STORE_BACKEND",
    "SCITEX_TODO_STORE_BACKEND",
    "SCITEX_CARDS_READ_BACKEND",
    "SCITEX_TODO_READ_BACKEND",
)


def _pin_to_scratch() -> Path:
    """Point every store-selecting variable at a throwaway directory."""
    scratch = Path(tempfile.mkdtemp(prefix="scitex-cards-tests-"))
    _point_env_at(scratch)
    for name in _BACKEND_ENV_VARS:
        os.environ.pop(name, None)
    return scratch


def _point_env_at(scratch: Path) -> None:
    """Aim every store-selecting variable at ``scratch``."""
    os.environ["SCITEX_CARDS_DB"] = str(scratch / "cards.db")
    os.environ["SCITEX_TODO_DB"] = str(scratch / "cards.db")
    os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"] = str(scratch / "tasks.yaml")
    os.environ["SCITEX_TODO_TASKS_YAML_SHARED"] = str(scratch / "tasks.yaml")


def _bootstrap_empty_db(db_path: Path) -> None:
    """Create an EMPTY, schema-complete database at ``db_path``.

    SQLite is the store now, so a test that writes a card needs a database the
    way it used to need a ``tasks.yaml``. Pinning the variable was enough when
    the DB was a mirror that could be absent; against the real store an absent
    file is a hard, correct refusal ("canonical store ... does not exist"), and
    every write test would fail on configuration rather than on behaviour.

    Imported INSIDE the function on purpose: this module is imported before any
    test touches ``scitex_cards``, and importing the package at conftest import
    time would run ``_env_compat.mirror_env()`` before :func:`_pin_to_scratch`
    has aimed the variables — reading the developer's real environment instead
    of the scratch one.
    """
    from scitex_cards._db import connect, init_schema

    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        init_schema(conn)
        conn.commit()
    finally:
        conn.close()


# Executed at IMPORT of this conftest — before collection, therefore before any
# test module imports scitex_cards. A fixture would already be too late for the
# import-time env read in _env_compat.
_SCRATCH = _pin_to_scratch()


@pytest.fixture(scope="session")
def scratch_store_root() -> Path:
    """The throwaway store directory this run is pinned to (for assertions)."""
    return _SCRATCH


@pytest.fixture(autouse=True)
def _store_env_stays_pinned(tmp_path_factory) -> None:
    """Give every test its OWN empty database, and re-assert the pin.

    TWO JOBS, both load-bearing.

    (1) RE-ASSERT THE PIN. A test that deletes rather than overrides one of
    these (``monkeypatch.delenv``, or a stray ``os.environ.pop``) would
    silently hand the NEXT test the user-canonical default — the live board.
    Restoring it every test keeps the guarantee for the whole session rather
    than only for the first test.

    (2) A FRESH DATABASE PER TEST, which is new and is what the cutover
    requires. A single session-wide database cannot serve this suite: the store
    carries an identity, and a test that passes its own ``tmp_path`` store is
    refused by a database already stamped for a different one — correctly, since
    writing store A into store B's database replaces B's rows with A's. Sharing
    one database between tests would therefore either break them or force the
    ownership guard off, and the guard is the thing that stopped this suite
    rebuilding the fleet's production database three times on 2026-07-19.

    Per-test isolation removes the collision instead of arbitrating it, and it
    buys real isolation as a side effect: no test can observe another's rows.

    Still ``os.environ`` rather than ``monkeypatch``: the concurrency tests pass
    ``env=os.environ.copy()`` to real child processes, and those children must
    inherit this test's database. That inheritance is precisely how the first
    wipe happened, so it is not incidental.
    """
    scratch = tmp_path_factory.mktemp("store")
    _point_env_at(scratch)
    _bootstrap_empty_db(scratch / "cards.db")


def seed_db_from_doc(doc, db_path, *, threads=None, as_store=None):
    """Populate a fresh database from an IN-MEMORY document. Returns the summary.

    THE REPLACEMENT FOR ``import_from_yaml`` IN TESTS. That function read a doc
    off a YAML file and rebuilt the DB from it; it is deleted, because SQLite is
    the only store and there is no YAML to read. Tests that used it to *seed* a
    database (build a doc, write YAML, import) now build the same doc and call
    this — which reaches the SAME surviving primitive (``_rebuild_from_doc``),
    so every downstream assertion about schema / columns / counts is unchanged.

    Use this to SEED. Do NOT use it to test importing — the import path is gone;
    a test whose subject was "importing YAML" has no subject and should be
    deleted, not rerouted here.

    ``threads`` (the ``{thread_key: [msgs]}`` map, i.e. ``threads_doc["threads"]``)
    additionally rebuilds the ``messages`` table, exactly as the old import did
    when it loaded the ``threads.yaml`` sidecar.

    Returns ``{"db_path", "tasks", "comments", ...}``. NOTE: there is no
    ``"yaml_path"`` key — nothing was read from YAML. A test that asserted on
    ``summary["yaml_path"]`` is asserting a fact that no longer exists; drop that
    line (it is not a weakened assertion, it is a removed one).
    """
    from scitex_cards._db import connect, init_schema
    from scitex_cards._db_bootstrap import _rebuild_from_doc, _stamp_meta

    conn = connect(str(db_path))
    try:
        init_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        summary = _rebuild_from_doc(conn, doc, threads=threads)
        summary["db_path"] = str(db_path)
        _stamp_meta(conn, "test-seed")
        conn.commit()
    finally:
        conn.close()
    return summary


# EOF
