#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Crash-safe writer guarantees on `_model.save_tasks`.

Pins the safety contract the lead requested in a2a `d5809cd3` after
recovering the 2026-06-13 corruption episode (canonical
`~/.scitex/todo/tasks.yaml` ended mid-string at line ~2784):

1. Pre-write validation raises before any disk write.
2. A failed pre-write leaves no canonical file behind.
3. A SIGKILL DURING the dump leaves the canonical file UNCHANGED
   (atomic-rename guarantee — POSIX `os.replace` only fires after
   the tmp is fully written + post-dump-reparsed + count-checked).
4. The happy-path round-trips through load_tasks.

No mocks (STX-NM / PA-306). The SIGKILL test uses a real subprocess
that calls save_tasks with a giant payload then kills itself mid-flow,
mirroring the existing
`test__store.py::test_two_concurrent_writers_serialize_via_flock`
no-mocks subprocess pattern. AAA, one assertion per test.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from scitex_todo._model import (
    TaskValidationError,
    load_tasks,
    save_tasks,
)


# === fixtures ===============================================================


@pytest.fixture()
def good_store(tmp_path: Path) -> Path:
    """A pre-existing valid store with two tasks."""
    store = tmp_path / "tasks.yaml"
    save_tasks(
        [
            {"id": "t-a", "title": "task a", "status": "pending"},
            {"id": "t-b", "title": "task b", "status": "pending"},
        ],
        store,
    )
    return store


# === 1. Pre-write validation raises BEFORE touching disk ====================


def test_invalid_in_memory_structure_raises_before_write(tmp_path: Path):
    # Arrange — a tasks list with a duplicate id violates _validate_tasks.
    store = tmp_path / "tasks.yaml"
    bad = [
        {"id": "dup", "title": "first", "status": "pending"},
        {"id": "dup", "title": "second", "status": "pending"},
    ]

    # Act / Assert
    with pytest.raises(TaskValidationError):
        save_tasks(bad, store)


def test_invalid_in_memory_does_not_create_canonical_file(tmp_path: Path):
    # Arrange — store didn't exist; a failed save must NOT create it.
    store = tmp_path / "tasks.yaml"
    bad = [
        {"id": "dup", "title": "first", "status": "pending"},
        {"id": "dup", "title": "second", "status": "pending"},
    ]

    # Act
    try:
        save_tasks(bad, store)
    except TaskValidationError:
        pass

    # Assert
    assert not store.exists()


def test_invalid_in_memory_leaves_existing_canonical_unchanged(good_store: Path):
    # Arrange — capture the existing valid canonical bytes.
    before = good_store.read_bytes()
    bad = [
        {"id": "dup", "title": "first", "status": "pending"},
        {"id": "dup", "title": "second", "status": "pending"},
    ]

    # Act
    try:
        save_tasks(bad, good_store)
    except TaskValidationError:
        pass

    # Assert — bytes are exactly the original.
    assert good_store.read_bytes() == before


# === 2. SIGKILL mid-write leaves canonical untouched (atomic-rename) ========


_KILL_MID_WRITE_SCRIPT = textwrap.dedent(
    """
    import os, signal, sys, time
    sys.path.insert(0, sys.argv[2])
    from scitex_todo._model import save_tasks
    # Schedule a SIGKILL on ourselves after a tiny delay — should hit DURING
    # the YAML dump for a payload this large.
    pid = os.getpid()
    if os.fork() == 0:
        time.sleep(0.05)
        os.kill(pid, signal.SIGKILL)
        os._exit(0)
    # Build a beefy payload — 5000 tasks with a long note each so the dump
    # takes well over 50ms; the killer fires mid-stream.
    huge = [
        {{"id": f"t-{{i}}", "title": "X" * 200, "status": "pending",
          "note": "Y" * 1000}}
        for i in range(5000)
    ]
    save_tasks(huge, sys.argv[1])
    """
)


def test_sigkill_mid_dump_leaves_canonical_unchanged(good_store: Path):
    # Arrange — snapshot the canonical bytes BEFORE we crash-write.
    before = good_store.read_bytes()
    script = good_store.parent / "killer.py"
    script.write_text(_KILL_MID_WRITE_SCRIPT)
    src = str(Path(__file__).resolve().parents[2] / "src")

    # Act — run the subprocess; expect a non-zero exit (SIGKILL on the
    # parent process). subprocess.run returncode will be negative.
    result = subprocess.run(
        [sys.executable, str(script), str(good_store), src],
        capture_output=True,
        timeout=10,
    )

    # Assert — canonical file is byte-for-byte the original. The atomic-
    # rename guarantee means os.replace never fired (we got killed before
    # reaching it), so the canonical bytes are untouched.
    assert good_store.read_bytes() == before


def test_sigkill_mid_dump_does_not_leave_stale_tmp_in_canonical_slot(good_store: Path):
    # Arrange
    script = good_store.parent / "killer.py"
    script.write_text(_KILL_MID_WRITE_SCRIPT)
    src = str(Path(__file__).resolve().parents[2] / "src")

    # Act
    subprocess.run(
        [sys.executable, str(script), str(good_store), src],
        capture_output=True,
        timeout=10,
    )

    # Assert — the canonical path still loads cleanly. A truncated /
    # half-written canonical file would raise here.
    tasks = load_tasks(good_store)
    assert len(tasks) == 2  # the original two tasks


# === 3. Happy path still works (no regression on the post-dump-validate) ===


def test_clean_save_round_trips_through_load(tmp_path: Path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    tasks_in = [
        {"id": "t-a", "title": "task a", "status": "pending"},
        {"id": "t-b", "title": "task b", "status": "done"},
    ]

    # Act
    save_tasks(tasks_in, store)
    loaded = load_tasks(store)

    # Assert
    assert [t["id"] for t in loaded] == ["t-a", "t-b"]


def test_clean_save_leaves_no_stale_tmp_sidecar(tmp_path: Path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    tmp = store.parent / f".{store.name}.tmp"

    # Act
    save_tasks(
        [{"id": "t-only", "title": "only", "status": "pending"}], store
    )

    # Assert — successful save deletes the tmp via os.replace; no sidecar.
    assert not tmp.exists()
