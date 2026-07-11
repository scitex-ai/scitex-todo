#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the notifyd sweeps (``scitex_todo._delivery._sweeps``).

The liveness sweep is the one signal that must NOT be ignorable, so it now has
a scheduled home inside notifyd. Two properties matter and are covered here
against REAL objects (no mocks): the sweep runs on its OWN low cadence (never
in the 60 s delivery path), and a raising sweep NEVER kills the delivery loop.

The raising case is driven with a REAL failure (a malformed store makes
``load_tasks`` raise inside the guard) plus a raising fake substituted for the
sweep function the loop calls — the loop, not the sweep, is under test there.
"""

from __future__ import annotations

import datetime as _dt

import yaml

from scitex_todo._delivery import _daemon
from scitex_todo._delivery._sweeps import (
    DEFAULT_NUDGE_SWEEP_MINUTES,
    ENV_NUDGE_SWEEP_MINUTES,
    _nudge_sweep_due,
    _nudge_sweep_minutes,
    _run_stale_nudge_sweep,
)
from scitex_todo._inbox import enqueue

from ._fakes import RecorderChannel

T0 = _dt.datetime(2026, 7, 11, 10, 0, 0, tzinfo=_dt.timezone.utc)


def _write_recipients(tmp_path, mapping: dict) -> None:
    (tmp_path / "recipients.yaml").write_text(
        yaml.safe_dump({"users": mapping}), encoding="utf-8"
    )


class TestCadence:
    def test_never_run_is_due(self):
        assert _nudge_sweep_due(None, T0, minutes=30.0)

    def test_not_due_before_the_cadence_elapses(self):
        assert not _nudge_sweep_due(
            T0, T0 + _dt.timedelta(minutes=29), minutes=30.0
        )

    def test_due_once_the_cadence_elapses(self):
        assert _nudge_sweep_due(T0, T0 + _dt.timedelta(minutes=30), minutes=30.0)

    def test_non_positive_cadence_disables_the_sweep(self):
        assert not _nudge_sweep_due(None, T0, minutes=0.0)

    def test_cadence_env_override(self, monkeypatch):
        monkeypatch.setenv(ENV_NUDGE_SWEEP_MINUTES, "45")
        assert _nudge_sweep_minutes() == 45.0
        monkeypatch.setenv(ENV_NUDGE_SWEEP_MINUTES, "not-a-number")
        assert _nudge_sweep_minutes() == DEFAULT_NUDGE_SWEEP_MINUTES


class TestSweepIsFailSoft:
    def test_malformed_store_does_not_raise(self, tmp_path):
        # REAL failure: the store is not parseable, so load_tasks raises inside
        # the sweep. The guard must swallow it (the delivery pass follows).
        store = tmp_path / "tasks.yaml"
        store.write_text("{{{ not yaml", encoding="utf-8")
        _run_stale_nudge_sweep(store=store, now=T0)  # must not raise


class TestNotifydLoop:
    def test_raising_sweep_does_not_kill_the_delivery_loop(
        self, tmp_path, monkeypatch
    ):
        store = tmp_path / "tasks.yaml"
        rec = enqueue(
            "u_alice",
            event_type="reassigned",
            card_id="c1",
            body="Card c1 reassigned to you",
            actor="bob",
            ts="2026-07-11T09:00:00Z",
            store=store,
        )
        assert rec is not None
        _write_recipients(tmp_path, {"u_alice": {"channels": [{"kind": "log"}]}})

        calls: list[int] = []

        def _boom(*, store, now):
            calls.append(1)
            raise RuntimeError("sweep exploded")

        # The LOOP is under test, not the sweep: substitute a sweep that raises
        # OUT (the real one swallows) and assert the loop still delivers.
        monkeypatch.setattr(_daemon, "_run_stale_nudge_sweep", _boom)

        recorder = RecorderChannel(name="log")
        ticks = {"n": 0}

        def _now():
            ticks["n"] += 1
            return T0 + _dt.timedelta(hours=ticks["n"])

        result = _daemon.run_notifyd(
            store=store,
            interval=60.0,
            channels={"log": recorder},
            sleep=lambda _s: None,
            now_fn=_now,
            max_iterations=2,
            terminal_report_every=0,
            nudge_sweep_minutes=1.0,  # due every tick (the clock jumps 1 h)
        )

        assert calls  # the raising sweep really ran
        assert result["iterations"] == 2
        assert result["totals"]["sent"] >= 1
        # The notification really went out on the channel despite the raise —
        # detection dying must never stop delivery.
        assert [c["recipient"] for c in recorder.calls] == ["u_alice"]

    def test_sweep_disabled_by_zero_cadence(self, tmp_path, monkeypatch):
        store = tmp_path / "tasks.yaml"
        _write_recipients(tmp_path, {})
        calls: list[int] = []

        def _count(*, store, now):
            calls.append(1)

        monkeypatch.setattr(_daemon, "_run_stale_nudge_sweep", _count)

        _daemon.run_notifyd(
            store=store,
            interval=60.0,
            channels={},
            sleep=lambda _s: None,
            now_fn=lambda: T0,
            max_iterations=3,
            terminal_report_every=0,
            nudge_sweep_minutes=0.0,
        )
        assert calls == []

    def test_sweep_runs_once_per_cadence_not_every_tick(self, tmp_path, monkeypatch):
        store = tmp_path / "tasks.yaml"
        _write_recipients(tmp_path, {})
        calls: list[_dt.datetime] = []

        def _count(*, store, now):
            calls.append(now)

        monkeypatch.setattr(_daemon, "_run_stale_nudge_sweep", _count)

        ticks = {"n": 0}

        def _now():
            # Each TICK advances 10 min (now_fn is called several times a tick;
            # the sweep sees a monotonically rising clock either way).
            ticks["n"] += 1
            return T0 + _dt.timedelta(minutes=10 * ticks["n"])

        _daemon.run_notifyd(
            store=store,
            interval=60.0,
            channels={},
            sleep=lambda _s: None,
            now_fn=_now,
            max_iterations=4,
            terminal_report_every=0,
            nudge_sweep_minutes=600.0,
        )
        # The injected clock never advances past the 600 min cadence, so the
        # sweep fires ONCE (the first tick) across 4 delivery ticks — it is not
        # in the hot path.
        assert len(calls) == 1

# EOF
