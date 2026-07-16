#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Process/pidfile helpers for the ``scitex-todo board`` lifecycle CLI.

Extracted from :mod:`scitex_cards._cli._board` to keep that module under
the 512-line cap and to give the pidfile + port-resolution logic a
cohesive home. ``_board`` re-imports the public names so call sites
(and tests) are unchanged.

The port fallback (added for the stale-pidfile incident): when a board
is genuinely serving on the configured port but the pidfile is dead or
missing (e.g. an untracked board process holds the port), ``stop`` /
``restart`` / ``status`` can still find and act on the REAL process. The
cmdline-marker guard in :func:`_board_cmdline_is_board` is what makes
that safe — a foreign process holding the port is never reported, so it
is never signalled.

OS LIMIT (not a bug): a resolved PID can only be signalled if it is
owned by the SAME user as the caller. The kernel denies cross-user kill;
``stop`` surfaces that as a clear error rather than silently succeeding.
"""

from __future__ import annotations

from pathlib import Path as _Path

BOARD_PIDFILE = _Path.home() / ".scitex" / "todo" / "board.pid"

# Markers we expect in a board process's /proc/<pid>/cmdline. The board
# is launched via Django's ``call_command("scitex_cards_board", ...)``
# (see ``_board.board_run_server``), so the management-command name
# appears in the live process's argv. We require one of these before we
# ever signal a port-found PID — NEVER touch an unrelated process that
# merely happens to hold the port.
_BOARD_CMDLINE_MARKERS = ("scitex_cards_board", "scitex_cards._django")


def _board_pidfile() -> _Path:
    """Return the pidfile path (function so tests can override via env)."""
    import os as _os

    override = _os.environ.get("SCITEX_TODO_BOARD_PIDFILE")
    if override:
        return _Path(override)
    return BOARD_PIDFILE


def _board_pid_alive(pid: int) -> bool:
    """``os.kill(pid, 0)`` is the POSIX 'is this PID up?' probe."""
    import os as _os

    try:
        _os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False
    return True


def _board_read_pid() -> int | None:
    """Read the pidfile; return None when absent/unreadable/dead."""
    pf = _board_pidfile()
    if not pf.exists():
        return None
    try:
        pid = int(pf.read_text().strip())
    except (OSError, ValueError):
        return None
    if not _board_pid_alive(pid):
        # Stale pidfile from a crashed process — clean it up.
        try:
            pf.unlink()
        except OSError:
            pass
        return None
    return pid


def _board_write_pid(pid: int) -> None:
    """Write the pidfile, creating parent dirs as needed."""
    pf = _board_pidfile()
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(str(pid))


def _board_cmdline_is_board(pid: int) -> bool:
    """True iff ``/proc/<pid>/cmdline`` looks like a scitex-todo board.

    The cmdline-marker guard is what makes the port fallback SAFE: a
    foreign process holding the configured port is NOT ours and must
    never be signalled. We read the NUL-separated argv and require one
    of :data:`_BOARD_CMDLINE_MARKERS`. If /proc is unavailable (non-Linux)
    or unreadable, we conservatively return False — better to under-claim
    than to kill a stranger.
    """
    try:
        raw = _Path(f"/proc/{pid}/cmdline").read_bytes()
    except (OSError, ValueError):
        return False
    cmdline = raw.replace(b"\x00", b" ").decode("utf-8", "replace")
    return any(m in cmdline for m in _BOARD_CMDLINE_MARKERS)


def _board_pid_on_port(port: int) -> int | None:
    """Return the PID of the scitex-todo board listening on ``port``.

    Tries the available port-introspection tools in order — ``lsof``,
    ``ss``, then ``fuser`` — and tolerates any of them being absent
    (returns None rather than raising). The found PID is only returned
    after :func:`_board_cmdline_is_board` confirms it is OUR board, so a
    stranger holding the port is never reported (and so never killed by
    the stop/restart fallback).

    OS LIMIT (not a bug): the resolved PID can only later be signalled if
    it is owned by the SAME user as the caller — the kernel denies
    cross-user kill. We may still *find* such a PID here; the SIGTERM
    itself is what fails, surfaced as a clear error by ``stop``.
    """
    import shutil as _shutil
    import subprocess as _subprocess

    def _run(argv: list[str]) -> str | None:
        try:
            out = _subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, _subprocess.SubprocessError):
            return None
        return out.stdout

    pids: list[int] = []

    if _shutil.which("lsof"):
        # `lsof -ti tcp:PORT` → one PID per line (listeners + clients).
        out = _run(["lsof", "-ti", f"tcp:{port}"])
        if out:
            for line in out.split():
                try:
                    pids.append(int(line))
                except ValueError:
                    continue

    if not pids and _shutil.which("ss"):
        # `ss -ltnp` lines look like:
        #   ... *:8051 ... users:(("python",pid=1234,fd=7))
        out = _run(["ss", "-ltnp"])
        if out:
            import re as _re

            for line in out.splitlines():
                if f":{port}" not in line:
                    continue
                for m in _re.finditer(r"pid=(\d+)", line):
                    try:
                        pids.append(int(m.group(1)))
                    except ValueError:
                        continue

    if not pids and _shutil.which("fuser"):
        # `fuser PORT/tcp` → whitespace-separated PIDs on stdout.
        out = _run(["fuser", f"{port}/tcp"])
        if out:
            for tok in out.split():
                try:
                    pids.append(int(tok))
                except ValueError:
                    continue

    # Return the first PID whose cmdline proves it's a board (guard).
    for pid in pids:
        if _board_cmdline_is_board(pid):
            return pid
    return None


def _board_resolve_pid(port: int) -> tuple[int | None, bool]:
    """Resolve the live board PID, with a port fallback.

    Returns ``(pid, untracked)``:
      - ``(pid, False)`` — the pidfile is valid and live (current path,
        unchanged behaviour).
      - ``(pid, True)``  — the pidfile is dead/missing but a verified
        board is serving on ``port``; the stale pidfile is cleaned up.
      - ``(None, False)`` — nothing running anywhere we can see.
    """
    pid = _board_read_pid()
    if pid is not None:
        return pid, False
    # Pidfile dead/missing — fall back to the port (cmdline-verified).
    found = _board_pid_on_port(port)
    if found is not None:
        # Clean up any stale pidfile left behind (`_board_read_pid`
        # already removes a dead one, but a leftover unreadable file or a
        # race could persist — be defensive).
        pf = _board_pidfile()
        try:
            if pf.exists():
                pf.unlink()
        except OSError:
            pass
        return found, True
    return None, False


# EOF
