#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The always-on delivery daemon (slice 2) — a single-instance notify loop.

Slice 1 shipped :func:`scitex_todo._delivery.deliver_pending` (ONE pass) and
the ``scitex-todo deliver`` one-shot verb. This module wraps that pass in a
long-running, signal-aware loop so notifications keep flowing without an
external cron:

* :func:`run_notifyd` ticks ``deliver_pending`` every ``interval`` seconds,
  logging a one-line per-tick summary, until a stop event is set (by SIGTERM/
  SIGINT, by a test, or by ``max_iterations``).
* SINGLE-INSTANCE: the daemon holds a NON-BLOCKING exclusive ``flock`` on a
  dedicated pidfile (``<store_dir>/notifyd.pid``) for its WHOLE lifetime. A
  second daemon fails fast with :class:`DaemonAlreadyRunning` instead of
  running concurrently and double-sending. The lock is released + the pidfile
  removed on EVERY exit path (normal stop, signal, exception) via ``try /
  finally`` so a crash never strands a stale lock that blocks restart.
* TERMINAL re-surfacing: a notification whose retry budget is exhausted is a
  permanent comm-miss. The loop's per-item stderr line scrolls past, so the
  daemon periodically (every ``terminal_report_every`` ticks) re-scans the
  ledger via :func:`report_terminal_misses` and logs a THROTTLED WARNING that
  re-surfaces every outstanding comm-miss — a long-undeliverable user is never
  forgotten, but the warning does not spam every tick.
* TICK RESILIENCE: each tick's work is wrapped so an unexpected error (ledger
  corruption, a disk/clock fault) is logged with a traceback and the loop
  CONTINUES to the next tick rather than dying. Combined with the unit's
  ``Restart=on-failure`` this self-heals under both foreground and systemd runs.

Test seams (NO mocks): inject ``sleep`` (a no-op so tests never sleep for
real), ``now_fn`` (deterministic clock), a ``stop`` event (tripped after K
ticks), ``max_iterations`` (bounded run), and ``channels`` (real fake
transports). Everything observable is logged + returned.
"""

from __future__ import annotations

import datetime as _dt
import fcntl
import logging
import os
import signal
import threading
import time
from pathlib import Path

from .._inbox import _resolved_store
from ._ledger import TERMINAL_STATUS, Ledger, _KEY_SEP, ledger_path
from ._loop import deliver_pending

logger = logging.getLogger("scitex_todo.delivery.notifyd")

#: Default seconds between delivery ticks.
DEFAULT_INTERVAL = 120.0

#: Default cadence (in ticks) for the throttled terminal-miss re-report.
DEFAULT_TERMINAL_REPORT_EVERY = 10

#: Pidfile name; a sibling of the task store inside ``<store_dir>``.
PIDFILE_NAME = "notifyd.pid"


class DaemonAlreadyRunning(RuntimeError):
    """Raised when a second daemon tries to start while one already holds the lock."""


def pidfile_path(store: str | Path | None = None) -> Path:
    """Resolve the daemon pidfile: ``<store_dir>/notifyd.pid``.

    ``<store_dir>`` is the parent of the resolved task store, so the pidfile
    lives beside ``tasks.yaml`` + the delivery ledger under whichever scope
    the store resolved to.
    """
    return _resolved_store(store).parent / PIDFILE_NAME


class _SingleInstanceLock:
    """A NON-BLOCKING exclusive ``flock`` on the pidfile, held for the daemon's life.

    Unlike :func:`scitex_todo._model._store_lock` (a blocking per-write lock
    released at context exit), this lock is acquired ONCE at daemon start with
    ``LOCK_NB`` so a second daemon fails fast rather than queueing behind the
    first. The fd is kept open for the whole run; releasing it (and removing
    the pidfile) is what frees the slot for a restart.
    """

    def __init__(self, path: Path):
        self._path = path
        self._fd = None

    def acquire(self) -> None:
        """Take the lock + stamp our pid, or raise :class:`DaemonAlreadyRunning`."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # `a+` keeps any prior content until we truncate; the advisory flock is
        # what actually guards single-instance (the bytes are just for humans).
        fd = self._path.open("a+")
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError) as exc:
            fd.close()
            existing = self._read_pid_text()
            raise DaemonAlreadyRunning(
                f"another scitex-todo notifyd already holds {self._path} "
                f"(pid {existing or 'unknown'}); refusing to start a second "
                f"instance ({type(exc).__name__})"
            ) from exc
        # We own it — record our pid for human/ops visibility.
        fd.seek(0)
        fd.truncate()
        fd.write(f"{os.getpid()}\n")
        fd.flush()
        self._fd = fd

    def _read_pid_text(self) -> str:
        try:
            return self._path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    def release(self) -> None:
        """Release the flock and remove the pidfile — idempotent.

        Called from the daemon's ``finally`` so a normal stop, a signal, AND
        an exception mid-loop all leave the slot clean. Best-effort: a failure
        to remove the pidfile never masks the original exit reason.
        """
        fd = self._fd
        self._fd = None
        if fd is not None:
            try:
                fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                fd.close()
            except OSError:
                pass
        try:
            self._path.unlink(missing_ok=True)
        except OSError:
            pass


def report_terminal_misses(store: str | Path | None = None) -> list[dict]:
    """Scan the ledger for ALL current ``failed_terminal`` entries.

    A terminal entry is a permanent comm-miss: the retry budget was exhausted
    and the notification was never delivered. The loop surfaces each one LOUDLY
    exactly once when it first turns terminal, but that stderr line scrolls
    past. This scan lets the daemon re-surface the standing set periodically so
    a long-undeliverable user is never silently forgotten.

    Returns a list of ``{recipient, note_id, channel, attempts, last_ts,
    detail}`` dicts, one per outstanding terminal entry, sorted for stable
    output. Reads the ledger fresh off disk (it is the sole delivery truth).
    """
    ledger = Ledger.load(store)
    out: list[dict] = []
    for key, entry in ledger._entries.items():  # noqa: SLF001 — same package.
        if entry.get("status") != TERMINAL_STATUS:
            continue
        parts = key.split(_KEY_SEP)
        if len(parts) != 3:
            # Defensive: a hand-mangled key — surface it rather than drop it.
            recipient, note_id, channel = key, "", ""
        else:
            recipient, note_id, channel = parts
        out.append(
            {
                "recipient": recipient,
                "note_id": note_id,
                "channel": channel,
                "attempts": int(entry.get("attempts", 0)),
                "last_ts": entry.get("last_ts"),
                "detail": entry.get("detail"),
            }
        )
    out.sort(key=lambda d: (d["recipient"], d["note_id"], d["channel"]))
    return out


def _log_tick_summary(tick: int, summary: dict) -> None:
    """Log the one-line per-tick delivery summary."""
    logger.info(
        "notifyd tick %d: sent=%d failed=%d skipped=%d failed_terminal=%d "
        "(%d recorded)",
        tick,
        summary.get("sent", 0),
        summary.get("failed", 0),
        summary.get("skipped", 0),
        summary.get("failed_terminal", 0),
        len(summary.get("outcomes", [])),
    )


def _report_terminal_if_due(
    *,
    tick: int,
    every: int,
    store,
) -> None:
    """Every ``every`` ticks, re-surface the standing terminal comm-misses.

    THROTTLED on purpose: re-surfacing every tick would bury the live per-tick
    summaries and train the operator to ignore the warning. Re-surfacing on a
    cadence keeps a long-undeliverable user visible without spam.
    """
    if every <= 0:
        return
    if tick % every != 0:
        return
    misses = report_terminal_misses(store)
    if not misses:
        return
    logger.warning(
        "notifyd: %d OUTSTANDING terminal comm-miss(es) still undelivered "
        "(re-surfaced every %d ticks) — operator must fix the channel/address:",
        len(misses),
        every,
    )
    for m in misses:
        logger.warning(
            "  comm-miss: %s note=%s via %s (attempts=%d, last=%s, detail=%s)",
            m["recipient"],
            m["note_id"],
            m["channel"],
            m["attempts"],
            m["last_ts"],
            m["detail"],
        )


def _install_signal_handlers(stop: threading.Event):
    """Wire SIGTERM/SIGINT to trip the stop event (graceful shutdown).

    Returns a ``restore`` callable that puts the previous handlers back — only
    installed when called from the main thread (signal handlers can only be set
    there; tests that drive the loop off a stop event skip this path).
    """
    if threading.current_thread() is not threading.main_thread():
        return lambda: None

    previous: dict[int, object] = {}

    def _handler(signum, _frame):
        logger.info("notifyd received signal %d — initiating graceful stop", signum)
        stop.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            previous[sig] = signal.signal(sig, _handler)
        except (ValueError, OSError):
            # Not on the main thread / unsupported — skip that signal.
            pass

    def _restore() -> None:
        for sig, handler in previous.items():
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):
                pass

    return _restore


def run_notifyd(
    store: str | Path | None = None,
    *,
    interval: float = DEFAULT_INTERVAL,
    channels: dict | None = None,
    stop: threading.Event | None = None,
    sleep=time.sleep,
    now_fn=None,
    max_iterations: int | None = None,
    terminal_report_every: int = DEFAULT_TERMINAL_REPORT_EVERY,
) -> dict:
    """Run the always-on delivery loop until stopped.

    Acquires the single-instance lock, then loops: each tick runs ONE
    :func:`deliver_pending` pass, logs a one-line summary, periodically
    re-surfaces standing terminal comm-misses (throttled), and sleeps
    ``interval`` between ticks. Stops when ``stop`` is set (by SIGTERM/SIGINT,
    a test, or ``max_iterations``). The lock is ALWAYS released + the pidfile
    removed on exit (normal, signal, or exception).

    Parameters
    ----------
    store : str | Path | None
        Task-store override; resolves the inbox + ledger + recipients +
        pidfile dir.
    interval : float
        Seconds slept between ticks (passed to ``sleep``). Tests inject a
        no-op ``sleep`` so this never blocks.
    channels : dict | None
        Injected channel mapping (TEST seam) forwarded to ``deliver_pending``;
        ``None`` → entry-point-discovered channels.
    stop : threading.Event | None
        Cooperative stop flag, checked each iteration. Default: a fresh event.
    sleep : callable
        ``sleep(seconds)`` between ticks (TEST seam → ``lambda _: None``).
    now_fn : callable | None
        ``() -> datetime`` for the per-tick ``now`` (deterministic backoff in
        tests). Default: aware UTC now.
    max_iterations : int | None
        Stop after this many ticks (TEST seam). ``None`` → run until ``stop``.
    terminal_report_every : int
        Re-surface standing terminal comm-misses every N ticks (throttle).
        ``<= 0`` disables the re-report.

    Returns
    -------
    dict
        ``{iterations, totals, stopped_by}`` — total ticks run, summed
        sent/failed/skipped/failed_terminal counts, and why it stopped
        (``"stop_event" | "max_iterations"``).
    """
    stop = stop or threading.Event()
    now_fn = now_fn or (lambda: _dt.datetime.now(_dt.timezone.utc))

    lock = _SingleInstanceLock(pidfile_path(store))
    lock.acquire()  # raises DaemonAlreadyRunning — BEFORE the try/finally so a
    # failed acquire never releases a lock we do not own.

    restore_signals = _install_signal_handlers(stop)

    totals = {"sent": 0, "failed": 0, "skipped": 0, "failed_terminal": 0}
    iterations = 0
    stopped_by = "stop_event"

    logger.info(
        "notifyd started: pid=%d store=%s interval=%.1fs "
        "terminal_report_every=%d",
        os.getpid(),
        _resolved_store(store),
        interval,
        terminal_report_every,
    )

    try:
        while not stop.is_set():
            if max_iterations is not None and iterations >= max_iterations:
                stopped_by = "max_iterations"
                break
            iterations += 1
            # TICK RESILIENCE: a single bad tick must NEVER kill an always-on
            # daemon. deliver_pending is already fail-soft per recipient, but a
            # ledger/disk/clock error could still raise — catch it, log with a
            # traceback, and continue to the next tick. This self-heals under
            # BOTH foreground `scitex-todo notifyd` AND systemd (which also has
            # Restart=on-failure as a second safety net).
            try:
                summary = deliver_pending(
                    store=store,
                    channels=channels,
                    now=now_fn(),
                )
                for key in totals:
                    totals[key] += summary.get(key, 0)
                _log_tick_summary(iterations, summary)
                _report_terminal_if_due(
                    tick=iterations,
                    every=terminal_report_every,
                    store=store,
                )
            except Exception:  # noqa: BLE001 — one bad tick != kill the daemon
                logger.exception(
                    "notifyd tick %d raised; continuing to next tick", iterations
                )

            # Re-check stop BEFORE sleeping so a stop set during the tick (or
            # by max_iterations) ends the loop without an extra wait.
            if stop.is_set():
                break
            if max_iterations is not None and iterations >= max_iterations:
                stopped_by = "max_iterations"
                break
            sleep(interval)
        else:
            stopped_by = "stop_event"
    finally:
        restore_signals()
        lock.release()
        logger.info(
            "notifyd stopped (%s): iterations=%d totals=%s",
            stopped_by,
            iterations,
            totals,
        )

    return {
        "iterations": iterations,
        "totals": totals,
        "stopped_by": stopped_by,
    }


__all__ = [
    "DEFAULT_INTERVAL",
    "DEFAULT_TERMINAL_REPORT_EVERY",
    "DaemonAlreadyRunning",
    "PIDFILE_NAME",
    "pidfile_path",
    "report_terminal_misses",
    "run_notifyd",
]

# EOF
