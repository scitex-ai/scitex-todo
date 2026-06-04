#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Board service -- resolve + load the task store with a small mtime cache.

Mirrors figrecipe's ``services.get_or_create_editor`` shape but read-only: the
board never mutates the store in this MVP, so the "board" is just the validated
task list plus its resolved path and mtime. The cache avoids re-reading the
YAML on every poll while still picking up external edits (cache is keyed by
path and invalidated when the file's mtime changes).
"""

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# In-process cache: store_path_str -> (BoardState, last_access_time)
_board_cache: Dict[str, Tuple["BoardState", float]] = {}
_CACHE_TTL_SECONDS = 3_600  # 1 hour


@dataclass
class BoardState:
    """A resolved, validated task store snapshot."""

    tasks: list
    store_path: Path
    mtime: float


def get_board(tasks_path: Optional[str] = None) -> BoardState:
    """Resolve the task store, load + validate it, and cache by mtime.

    Parameters
    ----------
    tasks_path : str or None
        Optional explicit store path. When ``None``, the standard
        project -> user -> bundled resolution chain is used.

    Returns
    -------
    BoardState
        The validated task list plus the resolved path and its mtime.
    """
    from scitex_todo._model import load_tasks
    from scitex_todo._paths import resolve_tasks_path

    _cleanup_expired()

    resolved = resolve_tasks_path(tasks_path)
    key = str(resolved)
    mtime = resolved.stat().st_mtime if resolved.exists() else 0.0

    cached = _board_cache.get(key)
    if cached is not None:
        board, _ = cached
        if board.mtime == mtime:
            _board_cache[key] = (board, time.time())
            return board

    tasks = load_tasks(resolved)
    board = BoardState(tasks=tasks, store_path=resolved, mtime=mtime)
    _board_cache[key] = (board, time.time())
    logger.info("[scitex-todo] Loaded board from %s (%d tasks)", resolved, len(tasks))
    return board


def _cleanup_expired() -> None:
    """Drop board snapshots untouched for longer than the TTL."""
    now = time.time()
    expired = [
        k for k, (_, ts) in _board_cache.items() if now - ts > _CACHE_TTL_SECONDS
    ]
    for k in expired:
        _board_cache.pop(k, None)


def _reset_cache() -> None:
    """Clear the cache (test hook)."""
    _board_cache.clear()


# EOF
