#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Configure Django for the _django board tests (real settings, no mocks).

Skips the whole _django test package cleanly when Django is not installed
(the web extra is optional), so the core suite still runs on a lean install.
"""

from __future__ import annotations

import importlib.util as _ilu
import sys as _sys
from pathlib import Path as _Path

import pytest

django = pytest.importorskip("django")


def pytest_configure(config):  # noqa: ARG001
    """Point Django at the standalone board settings and call setup() once."""
    import os

    from django.conf import settings

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scitex_cards._django.settings")
    if not settings.configured:
        django.setup()


# === SQLite-cutover test plumbing for _django/** ============================
#
# Two things every _django test needs under the SQLite-only store, provided
# HERE (once) so the per-file migrations stay to plain store-path normalization.
#
# 1. `seed_db_from_doc` re-export. The helper is defined in
#    tests/scitex_cards/conftest.py, but `from conftest import seed_db_from_doc`
#    inside _django/** binds to THIS conftest (the nearest ancestor) and would
#    miss it. Load the shared conftest by path and re-export the symbol so every
#    _django test — at any depth — can `from conftest import seed_db_from_doc`.
_shared = _Path(__file__).resolve().parent.parent / "conftest.py"
_spec = _ilu.spec_from_file_location("_scitex_cards_shared_conftest", _shared)
_mod = _ilu.module_from_spec(_spec)
_sys.modules[_spec.name] = _mod  # register BEFORE exec (py3.12 dataclass lookup)
_spec.loader.exec_module(_mod)
seed_db_from_doc = _mod.seed_db_from_doc


@pytest.fixture(autouse=True)
def _django_store_identity_file_exists():
    """Ensure the pinned store-identity file EXISTS for the board's group loader.

    The store is the canonical DB, but the board's ``get_board`` ->
    ``load_groups(resolved)`` still OPENS the resolved store PATH and raises
    FileNotFoundError when it is absent (swallowed into a 400, so the handler is
    never reached). Under the cutover the DB — not this file — holds the cards;
    an empty marker file at the pinned path satisfies the group loader while
    every card read/write goes to the DB. Runs AFTER the top-level
    ``_store_env_stays_pinned`` (higher conftest → earlier autouse), so it reads
    the already-repointed pinned path for this test.
    """
    import os

    path = _Path(os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"])
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    yield
