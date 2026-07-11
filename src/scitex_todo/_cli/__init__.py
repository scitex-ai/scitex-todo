#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-todo`` command-line interface package.

Public entry point is ``main`` (wired to the ``scitex-todo`` console script).
The command tree is split across focused modules:

    _main         root group + core verbs (render-graph, list-tasks, board)
    _introspect   list-python-apis, mcp list-tools           (§1a)
    _completion   install-/print-shell-completion            (§1a)
    _skills       skills {list, get, install}                (§1a)
    _help_wait    help-wait / help-clear verbs

The bulk of the command tree is attached to ``main`` inside ``_main`` itself.
``help-wait`` / ``help-clear`` are wired here instead because ``_main`` and
``_write`` (the natural mutation-verb home) are both already at their line
budget — registering from this thin package root keeps the new verbs in a
focused module without a disruptive refactor of an unrelated oversized file.
"""

from __future__ import annotations

from ._main import main
from . import _health as _health_cli
from . import _help_wait as _help_wait_cli
from . import _db as _db_cli

_help_wait_cli.register(main)
# `health` — the package-level health doctor (store / agent-id / notifyd /
# channel). Wired here (like help-wait) to keep the over-budget _main.py
# untouched.
_health_cli.register(main)
# `db` — the shadow-SQLite operability noun group (SQLite migration S0,
# RFC #348). Wired here (like health / help-wait) to keep _main.py untouched.
_db_cli.register(main)

__all__ = ["main"]

# EOF
