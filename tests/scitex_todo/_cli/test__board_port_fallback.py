#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the port-fallback hardening of ``scitex-todo board``.

Incident: a board process was serving on its port, but the pidfile
(``~/.scitex/todo/board.pid``) was STALE (its pid dead while a different,
untracked board held the port), so ``status`` reported NOT-running and
``stop``/``restart`` couldn't act on the live board.

This module covers the new fallback:

  - ``_board_pid_on_port`` finds the PID holding a port (and returns None
    when nothing's there / when the port tools are unavailable).
  - the cmdline-marker GUARD: a non-board process on the port is REJECTED
    (we must never kill a stranger).
  - ``_board_resolve_pid`` prefers a valid pidfile, else falls back to a
    verified port-found board and cleans up the stale pidfile.
  - ``status`` / ``stop`` (dry-run) surface the untracked-pidfile board.

No mocks (STX-NM / PA-306): a real bound socket reserves the port, and a
real harmless subprocess stands in for "a board on the port" — the
subprocess argv carries a board marker so the cmdline guard matches it.
We only ever kill subprocesses WE spawned.
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import time

import pytest
from click.testing import CliRunner

from scitex_todo._cli import main
from scitex_todo._cli._board import (
    _board_cmdline_is_board,
    _board_pid_on_port,
    _board_resolve_pid,
    _board_write_pid,
)

_HAVE_PORT_TOOL = bool(
    shutil.which("lsof") or shutil.which("ss") or shutil.which("fuser")
)
_needs_port_tool = pytest.mark.skipif(
    not _HAVE_PORT_TOOL,
    reason="no lsof/ss/fuser available to introspect listening ports",
)


@pytest.fixture
def pidfile_path(env, tmp_path):
    """Point the pidfile at a tmp location (env override)."""
    pf = tmp_path / "board.pid"
    env.set("SCITEX_TODO_BOARD_PIDFILE", str(pf))
    yield pf


def _spawn_marked_listener(marker: str) -> tuple[subprocess.Popen, int]:
    """Spawn a real subprocess that binds+listens on an ephemeral port.

    ``marker`` is embedded in the child's argv so the cmdline guard can
    (or cannot) match it. The child prints its bound port on stdout, then
    sleeps holding the socket open. Returns ``(proc, port)``.
    """
    code = (
        "import socket,sys,time\n"
        "s=socket.socket();s.bind(('127.0.0.1',0));s.listen(1)\n"
        "print(s.getsockname()[1]);sys.stdout.flush()\n"
        "time.sleep(60)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code, marker],
        stdout=subprocess.PIPE,
        text=True,
    )
    line = proc.stdout.readline().strip()
    port = int(line)
    return proc, port


def _terminate(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


# === _board_pid_on_port =====================================================


class TestPidOnPort:
    """The port lookup finds OUR board PID and ignores empty ports."""

    def test_returns_none_when_nothing_listening(self):
        # Arrange — reserve a port then immediately release it so it's free.
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        # Act
        found = _board_pid_on_port(port)
        # Assert — nothing is listening there.
        assert found is None

    @_needs_port_tool
    def test_finds_marked_board_listener_pid(self):
        # Arrange — a real subprocess holds a port with a board marker.
        proc, port = _spawn_marked_listener("scitex_todo_board")
        try:
            # Act
            found = _board_pid_on_port(port)
            # Assert
            assert found == proc.pid
        finally:
            _terminate(proc)

    @_needs_port_tool
    def test_rejects_non_board_process_on_port(self):
        # Arrange — same listener but WITHOUT a board marker in argv.
        proc, port = _spawn_marked_listener("totally-unrelated-process")
        try:
            # Act
            found = _board_pid_on_port(port)
            # Assert — the cmdline guard refuses to claim a stranger.
            assert found is None
        finally:
            _terminate(proc)


# === cmdline guard ==========================================================


class TestCmdlineGuard:
    """``_board_cmdline_is_board`` matches our marker and nothing else."""

    def test_marked_process_is_recognised(self):
        # Arrange
        proc, _ = _spawn_marked_listener("scitex_todo_board")
        try:
            # Act / Assert
            assert _board_cmdline_is_board(proc.pid) is True
        finally:
            _terminate(proc)

    def test_unmarked_process_is_rejected(self):
        # Arrange
        proc, _ = _spawn_marked_listener("some-other-server")
        try:
            # Act / Assert
            assert _board_cmdline_is_board(proc.pid) is False
        finally:
            _terminate(proc)

    def test_dead_pid_is_rejected(self):
        # Arrange — a PID highly unlikely to exist.
        # Act / Assert
        assert _board_cmdline_is_board(999999) is False


# === _board_resolve_pid =====================================================


class TestResolvePid:
    """Pidfile takes precedence; otherwise the verified port fallback."""

    def test_valid_pidfile_wins_without_port_lookup(self, pidfile_path):
        # Arrange — a live pidfile (this test runner's own pid).
        import os

        _board_write_pid(os.getpid())
        # Act — port is irrelevant; pidfile is valid.
        pid, untracked = _board_resolve_pid(0)
        # Assert
        assert pid == os.getpid() and untracked is False

    def test_returns_none_when_nothing_anywhere(self, pidfile_path):
        # Arrange — no pidfile, free port.
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        # Act
        pid, untracked = _board_resolve_pid(port)
        # Assert
        assert pid is None and untracked is False

    @_needs_port_tool
    def test_stale_pidfile_falls_back_to_port_board(self, pidfile_path):
        # Arrange — stale pidfile (dead pid) + a real marked board on port.
        pidfile_path.parent.mkdir(parents=True, exist_ok=True)
        pidfile_path.write_text("999999")
        proc, port = _spawn_marked_listener("scitex_todo_board")
        try:
            # Act
            pid, untracked = _board_resolve_pid(port)
            # Assert — fell back to the real board; stale pidfile removed.
            assert pid == proc.pid
            assert untracked is True
            assert not pidfile_path.exists()
        finally:
            _terminate(proc)


# === status / stop surface the untracked board =============================


class TestVerbsSurfaceUntracked:
    """`status` reports the untracked board; `stop --dry-run` names it."""

    @_needs_port_tool
    def test_status_reports_untracked_port_board(self, pidfile_path):
        # Arrange — no pidfile; a marked board listens on a port.
        proc, port = _spawn_marked_listener("scitex_todo_board")
        try:
            # Act
            result = CliRunner().invoke(
                main, ["board", "status", "--port", str(port)]
            )
            # Assert — reported as running, flagged as untracked.
            assert "running" in result.output
            assert "untracked" in result.output
            assert f"pid {proc.pid}" in result.output
        finally:
            _terminate(proc)

    @_needs_port_tool
    def test_stop_dry_run_names_the_port_board(self, pidfile_path):
        # Arrange — no pidfile; a marked board listens on a port.
        proc, port = _spawn_marked_listener("scitex_todo_board")
        try:
            # Act — dry-run does NOT signal anything.
            result = CliRunner().invoke(
                main,
                ["board", "stop", "--port", str(port), "--dry-run"],
            )
            # Assert — it identifies the real pid + the untracked note.
            assert f"pid {proc.pid}" in result.output
            assert "untracked" in result.output
            # And the process is still alive (dry-run signalled nothing).
            time.sleep(0.1)
            assert proc.poll() is None
        finally:
            _terminate(proc)


# === stop fallback actually SIGTERMs the port board ========================


class TestStopFallbackTerminates:
    """`stop` SIGTERMs the port-found board when the pidfile is stale.

    We only ever kill a subprocess WE spawned. NOTE: this works because
    the dummy is owned by the SAME user as the test runner — a cross-user
    kill would be denied by the OS (that's a kernel limit, not a bug).
    """

    @_needs_port_tool
    def test_stop_terminates_untracked_port_board(self, pidfile_path):
        # Arrange — stale pidfile + a real marked board on the port.
        pidfile_path.parent.mkdir(parents=True, exist_ok=True)
        pidfile_path.write_text("999999")
        proc, port = _spawn_marked_listener("scitex_todo_board")
        try:
            # Act
            CliRunner().invoke(main, ["board", "stop", "--port", str(port)])
            # Wait briefly for SIGTERM to land.
            for _ in range(50):
                if proc.poll() is not None:
                    break
                time.sleep(0.1)
            # Assert — the dummy board was stopped; stale pidfile cleaned.
            assert proc.poll() is not None
            assert not pidfile_path.exists()
        finally:
            _terminate(proc)
