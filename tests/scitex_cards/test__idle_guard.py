#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stop-hook idle guard — block silent abandonment of claimed (in_progress) work.

Real fakes, NO mocks: plain task dicts, a real tmp_path store for the
load-path, an injected ``now``. AAA.
"""

from __future__ import annotations

import datetime as _dt
import io

import pytest

from scitex_cards import _idle_guard


_NOW = _dt.datetime(2026, 6, 30, 12, 0, 0, tzinfo=_dt.timezone.utc)


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    for var in ("SCITEX_TODO_STALE_ACTIVE_HOURS", "SCITEX_TODO_AGENT_ID", "SCITEX_TODO_TASKS_YAML_SHARED"):
        monkeypatch.delenv(var, raising=False)


def _t(*, id, owner, status, hours_ago, now=_NOW):
    last = (now - _dt.timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": id, "title": f"card {id}", "status": status, "agent": owner,
            "last_activity": last}


# === stale_in_progress — the abandonment set =================================


def test_only_stale_in_progress_for_the_agent():
    tasks = [
        _t(id="hot", owner="alice", status="in_progress", hours_ago=10),   # stale → IN
        _t(id="fresh", owner="alice", status="in_progress", hours_ago=0.1),  # fresh → out
        _t(id="blkd", owner="alice", status="blocked", hours_ago=10),       # parked → out
        _t(id="pend", owner="alice", status="pending", hours_ago=99),        # backlog → out
        _t(id="other", owner="bob", status="in_progress", hours_ago=10),     # not alice → out
    ]
    cards = _idle_guard.stale_in_progress("alice", tasks, now=_NOW, stale_hours=2.0)
    assert [c.id for c in cards] == ["hot"]


def test_no_agent_yields_empty():
    tasks = [_t(id="x", owner="alice", status="in_progress", hours_ago=10)]
    assert _idle_guard.stale_in_progress("", tasks, now=_NOW, stale_hours=2.0) == []


# === evaluate — (block, reason) ==============================================


def _store(tmp_path, tasks):
    import yaml

    p = tmp_path / "tasks.yaml"
    p.write_text(yaml.safe_dump({"tasks": tasks}), encoding="utf-8")
    return p


def test_evaluate_blocks_on_stale_in_progress(tmp_path):
    store = _store(tmp_path, [_t(id="c1", owner="alice", status="in_progress", hours_ago=10)])
    block, reason = _idle_guard.evaluate("alice", store=store, now=_NOW, stale_hours=2.0)
    assert block is True
    assert "c1" in reason
    assert "DO NOT STOP" in reason


def test_evaluate_allows_when_no_stale_in_progress(tmp_path):
    store = _store(tmp_path, [
        _t(id="fresh", owner="alice", status="in_progress", hours_ago=0.1),
        _t(id="pend", owner="alice", status="pending", hours_ago=99),
    ])
    block, reason = _idle_guard.evaluate("alice", store=store, now=_NOW, stale_hours=2.0)
    assert block is False
    assert reason == ""


# === main — Stop-hook exit codes =============================================


def _silence_stdin(monkeypatch):
    monkeypatch.setattr(_idle_guard.sys, "stdin", io.StringIO(""))


def test_main_blocks_with_exit_2(tmp_path, monkeypatch, capsys):
    store = _store(tmp_path, [_t(id="c1", owner="alice", status="in_progress", hours_ago=10)])
    monkeypatch.setenv("SCITEX_TODO_TASKS_YAML_SHARED", str(store))
    monkeypatch.setenv("SCITEX_TODO_STALE_ACTIVE_HOURS", "2")
    _silence_stdin(monkeypatch)

    rc = _idle_guard.main(["--agent", "alice"])

    assert rc == 2
    assert "c1" in capsys.readouterr().err


def test_main_allows_with_exit_0(tmp_path, monkeypatch):
    # No in_progress card → no claimed work to abandon → allow stop. Uses only a
    # pending card so the result is independent of the wall clock (main() reads
    # the real `now`, so an in_progress fixture anchored to a fixed past time
    # would flake once it aged past the stale threshold).
    store = _store(tmp_path, [_t(id="pend", owner="alice", status="pending", hours_ago=99)])
    monkeypatch.setenv("SCITEX_TODO_TASKS_YAML_SHARED", str(store))
    _silence_stdin(monkeypatch)

    assert _idle_guard.main(["--agent", "alice"]) == 0


def test_main_no_agent_allows(tmp_path, monkeypatch):
    _silence_stdin(monkeypatch)
    # No --agent, no SCITEX_TODO_AGENT_ID → cannot attribute work → allow stop.
    assert _idle_guard.main([]) == 0


def test_main_failsoft_allows_on_error(monkeypatch):
    # A broken store path makes load_tasks raise; the guard must NOT trap (exit 0).
    monkeypatch.setenv("SCITEX_TODO_TASKS_YAML_SHARED", "/no/such/dir/tasks.yaml")
    _silence_stdin(monkeypatch)
    assert _idle_guard.main(["--agent", "alice"]) == 0


# EOF
