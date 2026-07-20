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

from . import _cards as _cards_cli
from . import _db as _db_cli
from . import _dev as _dev_cli
from . import _health as _health_cli
from . import _help_wait as _help_wait_cli
from . import _hub as _hub_cli
from ._main import main

_help_wait_cli.register(main)
# `health` — the package-level health doctor (store / agent-id / notifyd /
# channel). Wired here (like help-wait) to keep the over-budget _main.py
# untouched.
_health_cli.register(main)
# `db` — the shadow-SQLite operability noun group (SQLite migration S0,
# RFC #348). Wired here (like health / help-wait) to keep _main.py untouched.
_db_cli.register(main)
# `hub` — provisioning + doctor for the remote rail (remote-hub PR-4), plus
# `hub start` (the RPC service; the old top-level `serve` leaf, which was a
# noun wearing a verb's job — doctrine §1/§1e).
_hub_cli.register(main)
# `cards <verb>` — the card QUERY surface. `cards list` subsumes the old
# `runnable` / `blocked` / `next` / `summary` top-level leaves, none of which
# were verbs; each is now a mode flag, with a hidden Phase-W alias.
_cards_cli.register(main)
# `dev <verb>` — the doctrine-§11 maintainer subgroup. Registered LAST: it
# re-parents commands the sibling modules have already mounted on `main`.
_dev_cli.register(main)

__all__ = ["main"]

# EOF
