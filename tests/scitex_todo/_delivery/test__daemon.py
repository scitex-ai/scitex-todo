#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the always-on delivery daemon (slice 2).

Real round-trips, NO mocks (STX-NM / PA-306): a real ``tmp_path`` store, real
notifications seeded via :func:`scitex_todo._inbox.enqueue`, a real
``recipients.yaml``, REAL fake channels, and a REAL ``threading.Event`` stop
seam + injected no-op ``sleep`` + injected ``now_fn`` so the loop never sleeps
for real and never depends on wall-clock.

Covers the spec's required cases:
* daemon runs N iterations (injected ``sleep`` no-op + ``max_iterations``) with
  a real fake channel — deliver runs each tick + notifications are delivered.
* single-instance: acquiring the daemon lock twice fails fast (the second call
  raises ``DaemonAlreadyRunning`` WITHOUT running the loop).
* flock release: after ``run_notifyd`` returns (stop tripped) AND after it
  raises mid-loop, the lock is free (a subsequent acquire succeeds) + the
  pidfile is gone — clean release on BOTH exit paths.
* terminal re-report: seed a ``failed_terminal`` ledger entry, run enough ticks
  to cross ``terminal_report_every``, assert ``report_terminal_misses`` returns
  it + the re-report WARNING fires AND is THROTTLED (not every tick).
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import threading

import yaml

from scitex_todo._delivery._daemon import (
    DaemonAlreadyRunning,
    _SingleInstanceLock,
    pidfile_path,
    report_terminal_misses,
    run_notifyd,
)
from scitex_todo._delivery._ledger import Ledger, TERMINAL_STATUS, _make_key
from scitex_todo._delivery._pidfile import (
    assess_liveness,
    heartbeat_age_seconds,
    local_identity,
    parse as parse_pidfile,
    writer_is_local,
)
from scitex_todo._inbox import enqueue, poll_inbox

from ._fakes import RecorderChannel


# --------------------------------------------------------------------------- #
# helpers                                                                     #
# --------------------------------------------------------------------------- #
def _store(tmp_path):
    return tmp_path / "tasks.yaml"


def _write_recipients(tmp_path, mapping: dict) -> None:
    path = tmp_path / "recipients.yaml"
    path.write_text(yaml.safe_dump({"users": mapping}), encoding="utf-8")


def _seed(store, recipient: str, *, card_id="c1", ts="2026-06-27T10:00:00Z"):
    rec = enqueue(
        recipient,
        event_type="reassigned",
        card_id=card_id,
        body=f"Card {card_id} reassigned to you",
        actor="bob",
        ts=ts,
        store=store,
    )
    assert rec is not None
    return rec["id"]


def _seed_terminal(store, recipient: str, note_id: str, channel: str) -> None:
    """Directly seed a ``failed_terminal`` ledger entry (a standing comm-miss)."""
    led = Ledger.load(store)
    led._entries[_make_key(recipient, note_id, channel)] = {
        "status": TERMINAL_STATUS,
        "attempts": 5,
        "last_ts": "2026-06-27T10:00:00Z",
        "next_eligible_ts": None,
        "detail": "transport permanently down",
    }
    led._save()


# --------------------------------------------------------------------------- #
# (1) daemon runs N iterations, delivers each tick                            #
# --------------------------------------------------------------------------- #
def test_daemon_runs_n_iterations_and_delivers(tmp_path):
    store = _store(tmp_path)
    note_id = _seed(store, "u_alice")
    _write_recipients(tmp_path, {"u_alice": {"channels": [{"kind": "log"}]}})

    recorder = RecorderChannel(name="log")
    sleeps: list[float] = []
    t0 = _dt.datetime(2026, 6, 27, 10, 0, 0, tzinfo=_dt.timezone.utc)
    ticks = {"n": 0}

    def _now():
        # Advance the clock past any backoff window each tick (deterministic).
        ticks["n"] += 1
        return t0 + _dt.timedelta(hours=ticks["n"])

    result = run_notifyd(
        store=store,
        interval=120.0,
        channels={"log": recorder},
        sleep=lambda s: sleeps.append(s),
        now_fn=_now,
        max_iterations=3,
        terminal_report_every=0,
    )

    assert result["iterations"] == 3
    assert result["stopped_by"] == "max_iterations"
    # The note is delivered on the FIRST tick; later ticks are ledger no-ops
    # (already sent) — deliver_pending ran every tick but only sent once.
    assert result["totals"]["sent"] == 1
    assert len(recorder.calls) == 1
    assert recorder.calls[0]["notification"]["id"] == note_id
    # The injected sleep was a no-op (never blocked for real); between the 3
    # ticks the loop did NOT sleep after the last (max_iterations short-circuit).
    assert all(s == 120.0 for s in sleeps)
    # The pidfile is cleaned up on normal exit.
    assert not pidfile_path(store).exists()


def test_daemon_stops_on_event_after_k_ticks(tmp_path):
    """A stop event tripped from inside (via sleep seam) ends the loop cleanly."""
    store = _store(tmp_path)
    _seed(store, "u_bob")
    _write_recipients(tmp_path, {"u_bob": {"channels": [{"kind": "log"}]}})

    recorder = RecorderChannel(name="log")
    stop = threading.Event()
    seen = {"ticks": 0}

    def _sleep(_s):
        # Trip the stop event after the 2nd tick — proves the cooperative
        # stop seam ends the loop without a real sleep.
        seen["ticks"] += 1
        if seen["ticks"] >= 2:
            stop.set()

    result = run_notifyd(
        store=store,
        channels={"log": recorder},
        stop=stop,
        sleep=_sleep,
        terminal_report_every=0,
    )
    assert result["stopped_by"] == "stop_event"
    assert result["iterations"] == 2
    assert not pidfile_path(store).exists()


# --------------------------------------------------------------------------- #
# (2) single-instance: second daemon fails fast, never runs the loop         #
# --------------------------------------------------------------------------- #
def test_single_instance_second_daemon_fails_fast(tmp_path):
    store = _store(tmp_path)
    _seed(store, "u_carol")
    _write_recipients(tmp_path, {"u_carol": {"channels": [{"kind": "log"}]}})

    # Hold the lock as a "running first daemon".
    first = _SingleInstanceLock(pidfile_path(store))
    first.acquire()
    try:
        recorder = RecorderChannel(name="log")
        # A second daemon must FAIL FAST without delivering anything.
        raised = False
        try:
            run_notifyd(
                store=store,
                channels={"log": recorder},
                sleep=lambda _s: None,
                max_iterations=5,
            )
        except DaemonAlreadyRunning as exc:
            raised = True
            assert "already" in str(exc).lower()
        assert raised, "second daemon should have raised DaemonAlreadyRunning"
        # The loop NEVER ran — no delivery happened.
        assert recorder.calls == []
    finally:
        first.release()
    # After the first releases, the slot is free again + pidfile gone.
    assert not pidfile_path(store).exists()
    second = _SingleInstanceLock(pidfile_path(store))
    second.acquire()
    second.release()


# --------------------------------------------------------------------------- #
# (3) flock release on BOTH exit paths (normal stop + exception mid-loop)     #
# --------------------------------------------------------------------------- #
def test_flock_released_after_normal_stop(tmp_path):
    store = _store(tmp_path)
    _seed(store, "u_dave")
    _write_recipients(tmp_path, {"u_dave": {"channels": [{"kind": "log"}]}})

    run_notifyd(
        store=store,
        channels={"log": RecorderChannel(name="log")},
        sleep=lambda _s: None,
        max_iterations=1,
        terminal_report_every=0,
    )
    # Lock free + pidfile gone after a normal stop.
    assert not pidfile_path(store).exists()
    again = _SingleInstanceLock(pidfile_path(store))
    again.acquire()
    again.release()


def test_flock_released_after_exception_midloop(tmp_path):
    """An exception OUTSIDE the per-tick guard still releases the lock.

    The per-tick body self-heals (see ``test_tick_exception_self_heals_*``), so
    to exercise the finally-release guarantee we raise from the ``sleep`` seam —
    a structural point that is intentionally NOT swallowed — and assert the lock
    + pidfile are cleaned up even though ``run_notifyd`` propagates.
    """
    store = _store(tmp_path)
    _seed(store, "u_eve")
    _write_recipients(tmp_path, {"u_eve": {"channels": [{"kind": "log"}]}})

    boom = RuntimeError("sleep blew up between ticks")

    def _explode(_s):
        raise boom

    raised = False
    try:
        run_notifyd(
            store=store,
            channels={"log": RecorderChannel(name="log")},
            sleep=_explode,  # raises OUTSIDE the per-tick guard → propagates
            max_iterations=3,
        )
    except RuntimeError as exc:
        raised = True
        assert exc is boom
    assert raised, "an exception outside the tick guard must propagate"
    # CRITICAL: even on a crash, the lock is released + pidfile removed so a
    # restart is never blocked by a stale lock.
    assert not pidfile_path(store).exists()
    again = _SingleInstanceLock(pidfile_path(store))
    again.acquire()
    again.release()


def test_tick_exception_self_heals_and_continues(tmp_path, caplog):
    """A raising tick is logged WITH a traceback and the daemon CONTINUES.

    now_fn() runs inside the per-tick guard, so a tick that raises every time
    must not kill the daemon — it logs and proceeds, finishing all ticks and
    releasing the lock cleanly (tick resilience, scitex-dev review note).
    """
    store = _store(tmp_path)
    _seed(store, "u_judy")
    _write_recipients(tmp_path, {"u_judy": {"channels": [{"kind": "log"}]}})

    boom = RuntimeError("clock blew up mid-tick")

    def _explode():
        raise boom  # inside the per-tick guard → caught + logged + continue

    with caplog.at_level(logging.ERROR, logger="scitex_todo.delivery.notifyd"):
        result = run_notifyd(
            store=store,
            channels={"log": RecorderChannel(name="log")},
            sleep=lambda _s: None,
            now_fn=_explode,
            max_iterations=3,
            terminal_report_every=0,
        )

    # Every tick raised, yet the daemon ran all 3 and stopped cleanly.
    assert result["iterations"] == 3
    assert result["stopped_by"] == "max_iterations"
    # Each failing tick was logged (not silently swallowed).
    tick_errors = [
        r for r in caplog.records if "continuing to next tick" in r.getMessage()
    ]
    assert len(tick_errors) == 3
    # Lock cleaned up despite the repeated tick failures.
    assert not pidfile_path(store).exists()


# --------------------------------------------------------------------------- #
# (4) terminal re-report: throttled re-surfacing of standing comm-misses       #
# --------------------------------------------------------------------------- #
def test_report_terminal_misses_returns_seeded_entry(tmp_path):
    store = _store(tmp_path)
    _seed(store, "u_frank")  # touch the store so the dir resolves
    _seed_terminal(store, "u_frank", "n_deadbeef", "telegram")

    misses = report_terminal_misses(store)
    assert len(misses) == 1
    m = misses[0]
    assert m["recipient"] == "u_frank"
    assert m["note_id"] == "n_deadbeef"
    assert m["channel"] == "telegram"
    assert m["attempts"] == 5
    assert m["detail"] == "transport permanently down"


def test_terminal_re_report_fires_and_is_throttled(tmp_path, caplog):
    store = _store(tmp_path)
    _seed(store, "u_grace")
    _write_recipients(tmp_path, {"u_grace": {"channels": [{"kind": "log"}]}})
    _seed_terminal(store, "u_grace", "n_cafebabe", "telegram")

    every = 3
    with caplog.at_level(logging.WARNING, logger="scitex_todo.delivery.notifyd"):
        run_notifyd(
            store=store,
            channels={"log": RecorderChannel(name="log")},
            sleep=lambda _s: None,
            max_iterations=7,
            terminal_report_every=every,
        )

    # The re-report header WARNING fires only on ticks divisible by `every`
    # (ticks 3 and 6 over 7 iterations) — THROTTLED, not every tick.
    headers = [
        r for r in caplog.records if "OUTSTANDING terminal comm-miss" in r.message
    ]
    assert len(headers) == 2, f"expected 2 throttled re-reports, got {len(headers)}"
    # The per-miss WARNING names the undelivered note so the operator can act.
    detail_lines = [
        r for r in caplog.records if "n_cafebabe" in r.getMessage()
    ]
    assert detail_lines, "the outstanding comm-miss note id must be re-surfaced"


def test_terminal_report_every_zero_disables(tmp_path, caplog):
    store = _store(tmp_path)
    _seed(store, "u_heidi")
    _write_recipients(tmp_path, {"u_heidi": {"channels": [{"kind": "log"}]}})
    _seed_terminal(store, "u_heidi", "n_0badf00d", "telegram")

    with caplog.at_level(logging.WARNING, logger="scitex_todo.delivery.notifyd"):
        run_notifyd(
            store=store,
            channels={"log": RecorderChannel(name="log")},
            sleep=lambda _s: None,
            max_iterations=5,
            terminal_report_every=0,
        )
    headers = [
        r for r in caplog.records if "OUTSTANDING terminal comm-miss" in r.message
    ]
    assert headers == [], "terminal_report_every=0 must disable the re-report"


# --------------------------------------------------------------------------- #
# (5) the daemon NEVER flips the inbox seen cursor (read-only contract)        #
# --------------------------------------------------------------------------- #
def test_daemon_never_flips_inbox_seen(tmp_path):
    store = _store(tmp_path)
    _seed(store, "u_ivan")
    _write_recipients(tmp_path, {"u_ivan": {"channels": [{"kind": "log"}]}})

    before = poll_inbox("u_ivan", unseen_only=True, mark_seen=False, store=store)
    assert len(before) == 1

    run_notifyd(
        store=store,
        channels={"log": RecorderChannel(name="log")},
        sleep=lambda _s: None,
        max_iterations=2,
        terminal_report_every=0,
    )
    after = poll_inbox("u_ivan", unseen_only=True, mark_seen=False, store=store)
    assert len(after) == 1  # delivery is seen-independent.


# --------------------------------------------------------------------------- #
# HEARTBEAT — the only liveness signal that crosses a PID-namespace boundary   #
# --------------------------------------------------------------------------- #
def test_daemon_refreshes_the_pidfile_heartbeat_every_tick(tmp_path):
    """The pidfile is REWRITTEN each tick with our identity + a fresh stamp.

    A reader in another PID namespace (a container sharing the store by
    bind-mount) cannot interpret our pid at all — freshness is all it has. So
    the stamp must actually advance, tick over tick, not just be written once.
    """
    store = _store(tmp_path)
    _write_recipients(tmp_path, {})

    t0 = _dt.datetime(2026, 7, 13, 9, 0, 0, tzinfo=_dt.timezone.utc)
    calls = {"n": 0}

    def _now():
        calls["n"] += 1
        return t0 + _dt.timedelta(seconds=calls["n"])

    # Read the LIVE pidfile from inside the loop (via the sleep seam) — after
    # the run it is deliberately unlinked.
    snapshots: list[dict] = []
    verdicts: list[dict] = []

    def _sleep(_s):
        text = pidfile_path(store).read_text(encoding="utf-8")
        snapshots.append(parse_pidfile(text))
        verdicts.append(assess_liveness(pidfile_path(store)))

    run_notifyd(
        store=store,
        interval=120.0,
        channels={},
        sleep=_sleep,
        now_fn=_now,
        max_iterations=3,
        terminal_report_every=0,
        nudge_sweep_minutes=0,
    )

    assert len(snapshots) == 2  # no sleep after the final (short-circuited) tick
    for snap in snapshots:
        assert snap["pid"] == os.getpid()
        assert snap["host"] == local_identity()["host"]
        assert snap["pid_ns"] == local_identity()["pid_ns"]
        assert float(snap["interval"]) == 120.0
        assert writer_is_local(snap) is True
    # The stamp ADVANCED between ticks — a frozen stamp would read as a dead
    # daemon to every cross-namespace checker.
    ages = [heartbeat_age_seconds(s, t0 + _dt.timedelta(hours=1)) for s in snapshots]
    assert ages[0] > ages[1], f"heartbeat did not advance between ticks: {ages}"
    # And a health probe taken WHILE the daemon runs sees it as alive.
    assert all(v["ok"] and v["state"] == "alive" for v in verdicts)


# EOF
