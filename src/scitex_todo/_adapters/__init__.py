#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Default adapter implementations for the four extension ports.

These ship with the standalone package so :command:`pip install scitex-todo`
yields a working local board with no fleet glue installed. Fleet
deployments swap in real implementations from an external package
(e.g. ``scitex-todo-fleet``) via constructor injection on
:func:`scitex_todo.create_board`.

See :mod:`scitex_todo._ports` for the Protocol contracts and
``docs/adr/0006-full-board-ui-spec-filterbar-columns-blocking-you.md``
for the architectural backbone.
"""

from ._in_process_pubsub import InProcessPubSub
from ._local_file_sync import LocalFileSync
from ._null_liveness import NullLiveness
from ._open_acl import OpenACL

__all__ = [
    "InProcessPubSub",
    "LocalFileSync",
    "NullLiveness",
    "OpenACL",
]
