#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``reassign_all`` — bulk owner change: one lock, one batch event.

The bulk-reassignment primitive ``sac agents rename`` needs
(``todo-reassign-all-bulk-primitive``). Every card owned by ``old_owner``
moves to ``new_owner`` in ONE atomic locked write; ONE canonical
``reassigned_batch`` event is emitted for the whole cohort (NOT one
``reassigned`` per card — that would be a notification flood).

Per-card semantics mirror :func:`scitex_todo._store.reassign_task`
EXACTLY: ``agent = assignee = new_owner``, ``scope = "agent:<new>"``, an
audit comment ``"reassigned <old> -> <new> by <actor>"``.

The event is captured via the documented in-process ``entry_points=``
injection seam (a real fake handler) — no mocks, no monkeypatch
(STX-NM / PA-306). AAA pattern.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scitex_todo._model import load_tasks
from scitex_todo._store import add_task, reassign_all


# === In-process injection seam (real fake handler, no mocks) ===============


class _Capturing:
    """Concrete fake entry-point handler that records every event."""

    def __init__(self):
        self.events: list[dict] = []

    def __call__(self, event: dict) -> None:
        self.events.append(dict(event))


class _FakeEP:
    def __init__(self, name: str, fn):
        self.name = name
        self._fn = fn

    def load(self):
        return self._fn


def _eps(sink: _Capturing) -> list[_FakeEP]:
    return [_FakeEP("captor", sink)]


def _card_events(sink: _Capturing, ev_type: str | None = None) -> list[dict]:
    out = [e for e in sink.events if e.get("kind") == "card-event"]
    if ev_type is not None:
        out = [e for e in out if e.get("type") == ev_type]
    return out


def _by_id(store, tid: str) -> dict:
    return [t for t in load_tasks(store) if t["id"] == tid][0]


# === N cards owned by X all move to Y in ONE call ==========================


def test_all_cards_of_old_owner_move_to_new_owner(tmp_path: Path):
    # Arrange — three cards owned by proj-old.
    store = tmp_path / "tasks.yaml"
    for cid in ("c-1", "c-2", "c-3"):
        add_task(store=store, id=cid, title=cid, agent="proj-old")
    # Act
    result = reassign_all(store, "proj-old", "proj-new", by="operator")
    # Assert — every card's owner fields moved together.
    for cid in ("c-1", "c-2", "c-3"):
        t = _by_id(store, cid)
        assert t["agent"] == "proj-new"
        assert t["assignee"] == "proj-new"
        assert t["scope"] == "agent:proj-new"
    assert result["count"] == 3
    assert result["changed"] is True
    assert sorted(result["card_ids"]) == ["c-1", "c-2", "c-3"]
    assert result["from_owner"] == "proj-old"
    assert result["to_owner"] == "proj-new"
    assert result["actor"] == "operator"


def test_each_moved_card_gets_audit_comment(tmp_path: Path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    for cid in ("c-1", "c-2"):
        add_task(store=store, id=cid, title=cid, agent="proj-old")
    # Act
    reassign_all(store, "proj-old", "proj-new", by="operator")
    # Assert — the identical reassign_task audit comment on each card.
    for cid in ("c-1", "c-2"):
        texts = [c.get("text") for c in _by_id(store, cid).get("comments") or []]
        assert any(
            "reassigned proj-old -> proj-new by operator" in (x or "") for x in texts
        )


# === EXACTLY ONE reassigned_batch event, NOT N events ======================


def test_emits_exactly_one_batch_event_not_n(tmp_path: Path):
    # Arrange — four cards owned by proj-old.
    store = tmp_path / "tasks.yaml"
    for cid in ("c-1", "c-2", "c-3", "c-4"):
        add_task(store=store, id=cid, title=cid, agent="proj-old")
    sink = _Capturing()
    # Act
    reassign_all(store, "proj-old", "proj-new", by="operator", entry_points=_eps(sink))
    # Assert — ONE batch event, and ZERO per-card `reassigned` events.
    batch = _card_events(sink, "reassigned_batch")
    assert len(batch) == 1
    assert _card_events(sink, "reassigned") == []


def test_batch_event_payload_carries_count_and_card_ids(tmp_path: Path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    for cid in ("c-1", "c-2", "c-3"):
        add_task(store=store, id=cid, title=cid, agent="proj-old")
    sink = _Capturing()
    # Act
    reassign_all(store, "proj-old", "proj-new", by="operator", entry_points=_eps(sink))
    # Assert — the single event models the ACT, not the rows.
    e = _card_events(sink, "reassigned_batch")[0]
    assert e["from_owner"] == "proj-old"
    assert e["to_owner"] == "proj-new"
    assert e["actor"] == "operator"
    assert e["count"] == 3
    assert sorted(e["card_ids"]) == ["c-1", "c-2", "c-3"]


# === Idempotent — no matches ⇒ count 0, changed False, no event ============


def test_no_matches_is_noop_no_event(tmp_path: Path):
    # Arrange — nobody is owned by ghost.
    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="c-1", title="x", agent="proj-keep")
    sink = _Capturing()
    # Act
    result = reassign_all(store, "ghost", "proj-new", by="operator", entry_points=_eps(sink))
    # Assert — no move, no event, untouched card.
    assert result["count"] == 0
    assert result["changed"] is False
    assert result["card_ids"] == []
    assert _card_events(sink) == []
    assert _by_id(store, "c-1")["agent"] == "proj-keep"


def test_second_call_after_move_is_noop(tmp_path: Path):
    # Arrange — after a move, the old owner has no cards left.
    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="c-1", title="x", agent="proj-old")
    reassign_all(store, "proj-old", "proj-new")
    sink = _Capturing()
    # Act — re-running the same move touches nothing.
    result = reassign_all(store, "proj-old", "proj-new", entry_points=_eps(sink))
    # Assert
    assert result["count"] == 0
    assert result["changed"] is False
    assert _card_events(sink) == []


# === Cards owned by OTHER agents are untouched =============================


def test_only_matching_owner_moves_others_untouched(tmp_path: Path):
    # Arrange — a mix of owners.
    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="c-1", title="x", agent="proj-old")
    add_task(store=store, id="c-2", title="y", agent="proj-other")
    add_task(store=store, id="c-3", title="z", agent="proj-old")
    # Act
    result = reassign_all(store, "proj-old", "proj-new", by="operator")
    # Assert — only proj-old cards moved; proj-other is untouched.
    assert sorted(result["card_ids"]) == ["c-1", "c-3"]
    assert _by_id(store, "c-1")["agent"] == "proj-new"
    assert _by_id(store, "c-3")["agent"] == "proj-new"
    other = _by_id(store, "c-2")
    assert other["agent"] == "proj-other"
    # Untouched: reassign_all must NOT have rescoped it to the new owner. (The
    # fixture's add_task does not set a scope, so we assert the negative rather
    # than a scope the fixture never established.)
    assert other.get("scope") != "agent:proj-new"


def test_matches_legacy_assignee_only_owner(tmp_path: Path):
    # Arrange — a card owned only via the legacy `assignee` field (no agent).
    store = tmp_path / "tasks.yaml"
    store.write_text(
        "tasks:\n"
        "  - id: c-1\n    title: x\n    status: pending\n    assignee: proj-old\n"
    )
    # Act
    result = reassign_all(store, "proj-old", "proj-new", by="operator")
    # Assert — the assignee-owned card is matched and moved.
    assert result["card_ids"] == ["c-1"]
    t = _by_id(store, "c-1")
    assert t["agent"] == "proj-new"
    assert t["assignee"] == "proj-new"


# === old_owner == new_owner raises ValueError =============================


def test_same_owner_raises_value_error(tmp_path: Path):
    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="c-1", title="x", agent="proj-old")
    with pytest.raises(ValueError):
        reassign_all(store, "proj-old", "proj-old", by="operator")


def test_missing_owners_raise_value_error(tmp_path: Path):
    store = tmp_path / "tasks.yaml"
    with pytest.raises(ValueError):
        reassign_all(store, "", "proj-new")
    with pytest.raises(ValueError):
        reassign_all(store, "proj-old", "")


# === Other fields preserved on moved cards ================================


def test_moved_cards_preserve_other_fields(tmp_path: Path):
    # Arrange — a card with title, status, priority, and an existing comment.
    store = tmp_path / "tasks.yaml"
    add_task(
        store=store, id="c-1", title="keep-me", agent="proj-old",
        status="in_progress", priority=2,
    )
    from scitex_todo._store import comment_task

    comment_task(store=store, task_id="c-1", text="pre-existing", by="alice")
    n_before = len(_by_id(store, "c-1").get("comments") or [])
    # Act
    reassign_all(store, "proj-old", "proj-new", by="operator")
    # Assert — untouched fields survive; the audit comment is ADDED, not
    # a replacement.
    t = _by_id(store, "c-1")
    assert t["title"] == "keep-me"
    assert t["status"] == "in_progress"
    assert t["priority"] == 2
    texts = [c.get("text") for c in t.get("comments") or []]
    assert any("pre-existing" in (x or "") for x in texts)
    assert len(t.get("comments") or []) == n_before + 1


# === FAIL-SOFT — a raising handler must not break the bulk write ==========


def test_persists_even_when_emit_raises(tmp_path: Path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    for cid in ("c-1", "c-2"):
        add_task(store=store, id=cid, title=cid, agent="proj-old")

    def _boom(_event):
        raise RuntimeError("handler exploded")

    bad = [_FakeEP("boom", _boom)]
    # Act — must NOT raise.
    result = reassign_all(store, "proj-old", "proj-new", by="operator", entry_points=bad)
    # Assert — the bulk owner change persisted and the call returned normally.
    assert result["changed"] is True
    assert result["count"] == 2
    for cid in ("c-1", "c-2"):
        assert _by_id(store, cid)["agent"] == "proj-new"


# EOF
