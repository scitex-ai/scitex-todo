#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Default :class:`LivenessPort` — returns an empty agent list.

Standalone installs have no fleet to enumerate; the board's Fleet tab
shows an empty list + a "watcher not installed" hint. The fleet's
SSH-fanout liveness adapter (out of package) replaces this with real
per-host agent state.
"""

from __future__ import annotations

from typing import Any


class NullLiveness:
    """No-op :class:`scitex_todo._ports.LivenessPort` implementation.

    :meth:`list_agents` returns an empty list. The board's FE detects
    the empty payload and renders a friendly empty-state hint instead
    of falsely-coloured dots.

    Examples
    --------
    >>> NullLiveness().list_agents()
    []
    """

    def list_agents(self) -> list[dict[str, Any]]:
        return []
