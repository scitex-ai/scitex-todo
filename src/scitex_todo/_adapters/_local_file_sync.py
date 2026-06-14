#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Default :class:`TaskSyncPort` — atomic local YAML file with mtime fingerprinting.

No cross-host awareness. The fleet's git-backed sync adapter (out of
package) replaces this. Standalone installs get a working board with
just this default.
"""

from __future__ import annotations

import os
from pathlib import Path

from .._model import load_tasks, save_tasks


class LocalFileSync:
    """Read + write the task store as a local YAML file.

    Atomic via :func:`scitex_todo._model.save_tasks` (ruamel round-trip +
    advisory ``fcntl.flock`` on a sibling ``.lock`` sentinel). Change
    detection via the file's mtime (cheap, polling-friendly).

    Implements :class:`scitex_todo._ports.TaskSyncPort`.

    Parameters
    ----------
    path : str or :class:`pathlib.Path`
        Path to the YAML task store.

    Examples
    --------
    >>> sync = LocalFileSync("~/.scitex/todo/tasks.yaml")  # doctest: +SKIP
    >>> tasks = sync.load()                                  # doctest: +SKIP
    >>> sync.reload_if_changed()  # False unless someone else wrote     # doctest: +SKIP
    False
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path).expanduser()
        # Last-observed mtime, populated by load(); reload_if_changed()
        # compares to detect external writes since the last successful load.
        self._last_mtime: float | None = None

    def load(self) -> list[dict]:
        """Return the validated task list. Updates the mtime snapshot."""
        tasks = load_tasks(self._path)
        try:
            self._last_mtime = self._path.stat().st_mtime
        except FileNotFoundError:
            # load_tasks raises on missing path so we shouldn't reach here,
            # but be defensive — a future bundled-example fallback in
            # load_tasks would put us in this branch with a path that
            # doesn't exist on disk.
            self._last_mtime = None
        return tasks

    def save(self, tasks: list[dict]) -> None:
        """Atomic ruamel-preserved write. Refreshes the mtime snapshot."""
        save_tasks(tasks, self._path)
        try:
            self._last_mtime = self._path.stat().st_mtime
        except FileNotFoundError:
            self._last_mtime = None

    def reload_if_changed(self) -> bool:
        """True iff the store has been mutated since the last load/save.

        Cheap: one ``stat()`` call. The board's AutoRefresh poll uses
        this to decide whether to re-fetch the graph.
        """
        try:
            cur = self._path.stat().st_mtime
        except FileNotFoundError:
            return self._last_mtime is not None  # disappeared → "changed"
        if self._last_mtime is None:
            self._last_mtime = cur
            return True
        return cur != self._last_mtime
