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


def _pin_to_scratch() -> Path:
    """Point every store-selecting variable at a throwaway directory."""
    scratch = Path(tempfile.mkdtemp(prefix="scitex-cards-tests-"))
    os.environ["SCITEX_CARDS_DB"] = str(scratch / "cards.db")
    os.environ["SCITEX_TODO_DB"] = str(scratch / "cards.db")
    os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"] = str(scratch / "tasks.yaml")
    os.environ["SCITEX_TODO_TASKS_YAML_SHARED"] = str(scratch / "tasks.yaml")
    return scratch


# Executed at IMPORT of this conftest — before collection, therefore before any
# test module imports scitex_cards. A fixture would already be too late for the
# import-time env read in _env_compat.
_SCRATCH = _pin_to_scratch()


@pytest.fixture(scope="session")
def scratch_store_root() -> Path:
    """The throwaway store directory this run is pinned to (for assertions)."""
    return _SCRATCH


@pytest.fixture(autouse=True)
def _store_env_stays_pinned() -> None:
    """Re-assert the pin between tests.

    A test that deletes rather than overrides one of these (``monkeypatch.delenv``,
    or a stray ``os.environ.pop``) would silently hand the NEXT test the
    user-canonical default — the live board. Restoring it every test keeps the
    guarantee for the whole session rather than only for the first test.
    """
    for name in _STORE_ENV_VARS:
        if not os.environ.get(name):
            _pin_to_scratch()
            break


# EOF
