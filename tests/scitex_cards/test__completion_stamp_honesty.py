#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A card that is not `done` must not carry a completion stamp.

WHY THIS FILE EXISTS (2026-07-14): `reopen_task` flipped `status` from `done`
back to `blocked` but left `_log_meta.completed_{at,by}` in place. That is not a
cosmetic leak — `_django/handlers/fleet/timing.py` and `timeline.py` compute
throughput from `completed_at` ALONE and never consult `status`. So a reopened
card was counted as DELIVERED WORK by the throughput surfaces while
simultaneously nagging its owner as OPEN BACKLOG in the digest. One card, two
contradictory facts, depending on who read it.

Five such cards were found on the live board. The health check that exists to
catch exactly this (`_check_terminal_state_honest`) reported ok on all five,
because it knew about `closed_at` and had never been told about `completed_at`.
"""

import yaml

from scitex_cards._health import _CLOSURE_MARKERS, _check_terminal_state_honest
from scitex_cards._store_lifecycle import (
    COMPLETION_STAMP_KEYS,
    clear_completion_stamp,
    complete_task,
    reopen_task,
)


def _store(tmp_path, tasks):
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = tmp_path / "tasks.yaml"
    p.write_text(yaml.safe_dump({"tasks": tasks}, sort_keys=False))
    return p


def _meta(store, task_id):
    doc = yaml.safe_load(store.read_text())
    task = next(t for t in doc["tasks"] if t["id"] == task_id)
    return task, (task.get("_log_meta") or {})


def test_complete_then_reopen_leaves_no_completion_stamp(tmp_path):
    """The regression itself: reopen must un-complete, not just un-status."""
    store = _store(tmp_path, [{"id": "a", "title": "A", "status": "blocked"}])

    complete_task(store, "a", by="tester")
    task, meta = _meta(store, "a")
    assert task["status"] == "done"
    assert meta.get("completed_at"), "complete_task must stamp completed_at"

    reopen_task(store, "a", by="tester")
    task, meta = _meta(store, "a")
    assert task["status"] == "blocked"
    for key in COMPLETION_STAMP_KEYS:
        assert key not in meta, (
            f"reopened card still carries _log_meta.{key} — the throughput "
            "surfaces aggregate on completed_at alone and will count this "
            "open card as delivered work"
        )


def test_reopened_card_is_not_a_zombie_to_the_health_check(tmp_path):
    """End-to-end: the guard must agree the reopened card is honest."""
    store = _store(tmp_path, [{"id": "a", "title": "A", "status": "blocked"}])
    complete_task(store, "a", by="tester")
    reopen_task(store, "a", by="tester")

    assert _check_terminal_state_honest(store)["ok"] is True


def test_health_check_catches_a_completed_at_zombie(tmp_path):
    """The blind spot, pinned: open status + completed_at MUST be caught.

    Before 2026-07-14 this returned ok — which is how five of them survived.
    """
    store = _store(
        tmp_path,
        [
            {
                "id": "zombie",
                "title": "counted as shipped, still nagging",
                "status": "deferred",
                "_log_meta": {"completed_at": "2026-07-01T00:00:00Z"},
            }
        ],
    )
    result = _check_terminal_state_honest(store)
    assert result["ok"] is False
    assert "zombie" in result["detail"]


def test_health_check_still_catches_a_closed_at_zombie(tmp_path):
    """Widening the check must not have dropped the marker it already had."""
    store = _store(
        tmp_path,
        [
            {
                "id": "zombie",
                "title": "closed but open",
                "status": "in_progress",
                "_log_meta": {"closed_at": "2026-07-01T00:00:00Z"},
            }
        ],
    )
    assert _check_terminal_state_honest(store)["ok"] is False


def test_every_closure_marker_is_actually_checked(tmp_path):
    """Add a closure marker without teaching the guard, and this FAILS.

    The original bug was an ENUMERATION that silently claimed to be complete.
    This test makes the enumeration load-bearing: each marker in
    _CLOSURE_MARKERS must, on its own, be enough to convict an open card.
    """
    for marker in _CLOSURE_MARKERS:
        store = _store(
            tmp_path / marker,
            [
                {
                    "id": "z",
                    "title": "z",
                    "status": "deferred",
                    "_log_meta": {marker: "2026-07-01T00:00:00Z"},
                }
            ],
        )
        result = _check_terminal_state_honest(store)
        assert result["ok"] is False, (
            f"_log_meta.{marker} is listed in _CLOSURE_MARKERS but does not "
            "trip the zombie check — the guard's enumeration is a lie"
        )


def test_cancelled_card_carrying_completed_at_is_caught(tmp_path):
    """A TERMINAL card can still lie about throughput.

    `cancelled` is not an open status, so the zombie rule does not see it — it
    does not nag anyone. But fleet/timing.py and timeline.py aggregate on
    `completed_at` ALONE, so a cancelled card carrying the stamp is reported as
    DELIVERED WORK. That is exactly the corruption that marking a killed card
    `done` was supposed to avoid, arrived at by a different road.

    (Real card, 2026-07-14: `sac-keystone` was briefly mislabelled `done`, then
    correctly moved to `cancelled` — and kept its completion stamp. The status
    was fixed; the stamp was not.)
    """
    store = _store(
        tmp_path,
        [
            {
                "id": "killed-but-counted",
                "title": "never built, still counted as shipped",
                "status": "cancelled",
                "_log_meta": {
                    "completed_at": "2026-07-14T10:23:38Z",
                    "completed_by": "somebody",
                },
            }
        ],
    )
    result = _check_terminal_state_honest(store)
    assert result["ok"] is False
    assert "killed-but-counted" in result["detail"]
    assert "DELIVERED WORK" in result["detail"]


def test_a_genuinely_done_card_is_not_flagged(tmp_path):
    """The stamp is CORRECT on a done card — no false positives."""
    store = _store(
        tmp_path,
        [
            {
                "id": "shipped",
                "title": "actually shipped",
                "status": "done",
                "_log_meta": {
                    "completed_at": "2026-07-14T10:00:00Z",
                    "completed_by": "me",
                },
            }
        ],
    )
    assert _check_terminal_state_honest(store)["ok"] is True


def test_clear_completion_stamp_is_idempotent_and_honest():
    """Never invents a stamp, reports truthfully whether it removed one."""
    assert clear_completion_stamp({"id": "x"}) is False
    assert clear_completion_stamp({"id": "x", "_log_meta": {}}) is False

    task = {"id": "x", "_log_meta": {"completed_at": "t", "completed_by": "me"}}
    assert clear_completion_stamp(task) is True
    assert "_log_meta" not in task, "an emptied _log_meta should not linger"
    assert clear_completion_stamp(task) is False


def test_clear_completion_stamp_preserves_unrelated_meta():
    """Only the completion keys go — started_at et al. are someone else's."""
    task = {
        "id": "x",
        "_log_meta": {"completed_at": "t", "started_at": "s"},
    }
    assert clear_completion_stamp(task) is True
    assert task["_log_meta"] == {"started_at": "s"}
