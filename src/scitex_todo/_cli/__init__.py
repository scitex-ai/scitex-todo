#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-todo`` command-line interface package.

Public entry point is ``main`` (wired to the ``scitex-todo`` console script).
The command tree is split across focused modules:

    _main         root group + core verbs (render-graph, list-tasks, board)
    _introspect   list-python-apis, mcp list-tools           (§1a)
    _completion   install-/print-shell-completion            (§1a)
    _skills       skills {list, get, install}                (§1a)
"""

from __future__ import annotations

from ._main import main

__all__ = ["main"]

# EOF
