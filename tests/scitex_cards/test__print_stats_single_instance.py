#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Single-instance flock guard on ``print-stats --notify``.

Third store-size daemon of the 2026-07-08 incident
(incident-todo-wake-watcher-interval2-spiral-20260708): the managed
``*/10`` notify cron (``print-stats --by agent --notify --nudge-quiet``)
re-derives per-agent rollups from the ~9 MB / ~930-card store. A run that
overruns the 10-min period OVERLAPS the next tick and runs STACK. The cure
is a NON-BLOCKING ``flock`` on the side-effecting notify path only — the
cron/one-shot analogue of the wake-watcher lock (#344) and the MCP inbox
drain guard (#345).

Real fakes, NO mocks (STX-NM): a real ``tmp_path`` YAML store, a REAL
``flock`` held by the test, and a plain call-counter SPY wrapping the real
``scitex_cards._push.deliver`` to prove whether the notify path was entered.
AAA structure.
"""

from __future__ import annotations

import yaml
from click.testing import CliRunner

import scitex_cards._cli._stats as _stats
import scitex_cards._push as _push
from scitex_cards._cli._main import main
from scitex_cards._singleflight import notify_lock_path, single_instance


def _write_store(path) -> None:
    """Minimal real tasks.yaml with one owned card ``load_tasks`` accepts."""
    doc = {
        "tasks": [
            {
                "id": "t1",
                "title": "Task one",
                "status": "in_progress",
                "agent": "proj-x",
            }
        ]
    }
    with open(path, "w", encoding="utf-8") as handle:
        yaml.safe_dump(doc, handle, sort_keys=False)


def _deliver_spy(monkeypatch):
    """Install a call-counter that WRAPS the real ``deliver`` (no mock).

    Returns the mutable ``calls`` list so a test can assert whether the
    notify/push path was entered. ``proj-x`` has no configured turn URL, so
    the wrapped real ``deliver`` returns ``no-turn-url-configured`` WITHOUT
    any network I/O — the spy observes real behaviour, it does not fake it.
    """
    calls: list = []
    real = _push.deliver

    def spy(agent, body, **kwargs):
        calls.append(agent)
        return real(agent, body, **kwargs)

    monkeypatch.setattr(_push, "deliver", spy)
    return calls


def _load_tasks_spy(monkeypatch):
    """Install a call-counter that WRAPS the real store parse (``load_tasks``).

    This is the assertion the 0.7.47 test was MISSING. The bug was that the
    expensive per-agent rollup — which begins by parsing the ~9 MB store via
    ``load_tasks`` — ran ABOVE the flock guard, so two overlapping ``--notify``
    ticks BOTH parsed the store concurrently even though the push at the end
    was serialized. A spy on the PUSH cannot catch that (the push is skipped
    either way once the lock is held); only a spy on the STORE PARSE proves the
    expensive work did not run. Wraps the real ``_stats.load_tasks`` (the name
    ``_rollup`` calls) — no mock, real parse still happens when it is invoked.
    """
    loads: list = []
    real = _stats.load_tasks

    def spy(path):
        loads.append(str(path))
        return real(path)

    monkeypatch.setattr(_stats, "load_tasks", spy)
    return loads


# --------------------------------------------------------------------------- #
# lock HELD -> --notify skips cleanly, does NO store parse / rollup / push      #
# --------------------------------------------------------------------------- #


def test_notify_skips_when_lock_is_held(tmp_path, monkeypatch):
    # Arrange — a prior run's flock is still held on the resolved lockfile.
    store = tmp_path / "tasks.yaml"
    _write_store(store)
    calls = _deliver_spy(monkeypatch)
    loads = _load_tasks_spy(monkeypatch)
    with single_instance(notify_lock_path(str(store))) as acquired:
        assert acquired  # the test itself holds the lock
        # Act — the cron path runs while a prior run still holds the lock.
        result = CliRunner().invoke(
            main, ["print-stats", "--by", "agent", "--notify", "--tasks", str(store)]
        )

    # Assert — clean skip (exit 0), and the notify/push path was NOT entered.
    assert result.exit_code == 0, result.output
    assert "a prior run still holds the lock" in result.output
    assert "# Notify push" not in result.output
    assert calls == []  # spy proves deliver() was never called
    # CRITICAL regression (0.7.48): the EXPENSIVE store parse / rollup must NOT
    # run when the lock is held. The 0.7.47 bug computed the rollup ABOVE the
    # guard, so this would have been >= 1. Guard now wraps the parse → ZERO.
    assert loads == []  # spy proves load_tasks() / the rollup never ran


# --------------------------------------------------------------------------- #
# lock FREE -> --notify runs the notify path                                   #
# --------------------------------------------------------------------------- #


def test_notify_runs_when_lock_is_free(tmp_path, monkeypatch):
    # Arrange — no prior holder.
    store = tmp_path / "tasks.yaml"
    _write_store(store)
    calls = _deliver_spy(monkeypatch)
    loads = _load_tasks_spy(monkeypatch)

    # Act
    result = CliRunner().invoke(
        main, ["print-stats", "--by", "agent", "--notify", "--tasks", str(store)]
    )

    # Assert — the notify path ran, parsed the store, and pushed for the agent.
    assert result.exit_code == 0, result.output
    assert "# Notify push" in result.output
    assert "proj-x" in calls
    assert loads != []  # the rollup DID parse the store (lock was free)


# --------------------------------------------------------------------------- #
# plain read is UNGUARDED — runs even while the lock is held                   #
# --------------------------------------------------------------------------- #


def test_plain_read_is_not_guarded_by_the_lock(tmp_path, monkeypatch):
    # Arrange — hold the notify lock, then run a PLAIN print-stats (no --notify).
    store = tmp_path / "tasks.yaml"
    _write_store(store)
    calls = _deliver_spy(monkeypatch)
    loads = _load_tasks_spy(monkeypatch)

    with single_instance(notify_lock_path(str(store))) as acquired:
        assert acquired
        # Act — an interactive read must NOT be blocked by the notify lock.
        result = CliRunner().invoke(
            main, ["print-stats", "--by", "agent", "--tasks", str(store)]
        )

    # Assert — the table printed read-only; no notify path, no skip line.
    assert result.exit_code == 0, result.output
    assert "proj-x" in result.output
    assert "# Notify push" not in result.output
    assert "a prior run still holds the lock" not in result.output
    assert calls == []
    # The plain read is UNGUARDED: it parses the store even while the notify
    # lock is held (interactive reads must never be blocked/skipped).
    assert loads != []


# --------------------------------------------------------------------------- #
# the lock is released after the run                                          #
# --------------------------------------------------------------------------- #


def test_lock_is_released_after_notify_run(tmp_path, monkeypatch):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _write_store(store)
    _deliver_spy(monkeypatch)

    # Act — a first --notify run acquires and RELEASES the lock on exit.
    first = CliRunner().invoke(
        main, ["print-stats", "--by", "agent", "--notify", "--tasks", str(store)]
    )
    assert first.exit_code == 0, first.output
    assert "# Notify push" in first.output

    # Assert — a subsequent --notify acquires cleanly (proves release), and the
    # test can itself take the flock now that the run has released it.
    second = CliRunner().invoke(
        main, ["print-stats", "--by", "agent", "--notify", "--tasks", str(store)]
    )
    assert second.exit_code == 0, second.output
    assert "# Notify push" in second.output
    assert "a prior run still holds the lock" not in second.output

    with single_instance(notify_lock_path(str(store))) as acquired:
        assert acquired
