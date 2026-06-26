#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C5 — store mutations emit canonical card-events + atomic reassign primitive.

The card-event/notification foundation epic, card
``cenf-c5-store-event-producers-20260626`` (+ the reassign-verb child
``todo-reassign-verb-with-owner-notify-20260626``). The mutating
:mod:`scitex_todo._store` verbs now ALSO emit a canonical
:class:`scitex_todo._events.Event` onto the hook bus, and a new
:func:`scitex_todo._store.reassign_task` primitive does an atomic
owner-change.

Mutation → event mapping under test:

    add_task        → ``created``
    comment_task    → ``commented``       (IN ADDITION to ``card-message``)
    update_task     → ``status_changed``  on a non-done flip; ``completed``
                      on a flip to done
    complete_task   → ``completed``
    resolve_task    → ``status_changed`` {from,to:done}
    reassign_task   → ``reassigned`` {from_owner,to_owner}

EMIT-ONLY: there is intentionally NO consumer yet (delivery is C4, a
separate card). Tests capture the emitted card-event via the documented
in-process ``entry_points=`` injection seam (a real fake handler) — no
mocks, no monkeypatch (STX-NM / PA-306). AAA pattern.
"""

from __future__ import annotations

from pathlib import Path

from scitex_todo._model import load_tasks
from scitex_todo._store import (
    add_task,
    comment_task,
    complete_task,
    reassign_task,
    resolve_task,
    update_task,
)


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
    """Only the C1 canonical card-events (the bus also fans legacy kinds —
    e.g. ``card-message`` from comment_task — to the same plugin set)."""
    out = [e for e in sink.events if e.get("kind") == "card-event"]
    if ev_type is not None:
        out = [e for e in out if e.get("type") == ev_type]
    return out


# === add_task → created ====================================================


def test_add_task_emits_exactly_one_created(tmp_path: Path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    sink = _Capturing()
    # Act
    add_task(store=store, id="c-1", title="x", entry_points=_eps(sink))
    # Assert
    created = _card_events(sink, "created")
    assert len(created) == 1


def test_created_event_carries_card_id(tmp_path: Path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    sink = _Capturing()
    # Act
    add_task(store=store, id="c-1", title="x", entry_points=_eps(sink))
    # Assert
    assert _card_events(sink, "created")[0]["card_id"] == "c-1"


def test_created_event_actor_is_creating_user(tmp_path: Path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    sink = _Capturing()
    # Act — explicit created_by becomes the event actor.
    add_task(
        store=store, id="c-1", title="x", created_by="operator",
        entry_points=_eps(sink),
    )
    # Assert
    assert _card_events(sink, "created")[0]["actor"] == "operator"


# === comment_task → commented (+ the legacy card-message still fires) ======


def test_comment_task_emits_commented(tmp_path: Path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="c-1", title="x")
    sink = _Capturing()
    # Act
    comment_task(
        store=store, task_id="c-1", text="hi", by="operator",
        entry_points=_eps(sink),
    )
    # Assert
    assert len(_card_events(sink, "commented")) == 1


def test_comment_task_still_emits_card_message(tmp_path: Path):
    # Arrange — the legacy `card-message` dispatch MUST stay intact
    # (additive, not a replacement).
    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="c-1", title="x")
    sink = _Capturing()
    # Act
    comment_task(
        store=store, task_id="c-1", text="hi", by="operator",
        entry_points=_eps(sink),
    )
    # Assert — both the legacy kind AND the new canonical event fired.
    assert any(e.get("kind") == "card-message" for e in sink.events)
    assert any(
        e.get("kind") == "card-event" and e.get("type") == "commented"
        for e in sink.events
    )


def test_commented_event_carries_body_and_actor(tmp_path: Path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="c-1", title="x")
    sink = _Capturing()
    # Act
    comment_task(
        store=store, task_id="c-1", text="hello there", by="alice",
        entry_points=_eps(sink),
    )
    # Assert
    e = _card_events(sink, "commented")[0]
    assert e["card_id"] == "c-1" and e["actor"] == "alice" and e["body"] == "hello there"


# === update_task → status_changed / completed =============================


def test_status_flip_emits_status_changed_with_from_to(tmp_path: Path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="c-1", title="x", status="pending")
    sink = _Capturing()
    # Act
    update_task(store, "c-1", status="in_progress", entry_points=_eps(sink))
    # Assert
    e = _card_events(sink, "status_changed")[0]
    assert (e["from"], e["to"]) == ("pending", "in_progress")


def test_update_to_done_emits_completed_not_status_changed(tmp_path: Path):
    # Arrange — a flip to done is modelled as a `completed` event ONLY
    # (no duplicate `status_changed`).
    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="c-1", title="x", status="in_progress")
    sink = _Capturing()
    # Act
    update_task(store, "c-1", status="done", entry_points=_eps(sink))
    # Assert
    assert len(_card_events(sink, "completed")) == 1
    assert _card_events(sink, "status_changed") == []


def test_update_without_status_change_emits_no_event(tmp_path: Path):
    # Arrange — touching a non-status field does NOT emit a status event.
    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="c-1", title="x", status="pending")
    sink = _Capturing()
    # Act
    update_task(store, "c-1", note="just a note", entry_points=_eps(sink))
    # Assert
    assert _card_events(sink, "status_changed") == []
    assert _card_events(sink, "completed") == []


def test_update_status_to_same_value_emits_no_event(tmp_path: Path):
    # Arrange — re-setting status to its current value is not a flip.
    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="c-1", title="x", status="pending")
    sink = _Capturing()
    # Act
    update_task(store, "c-1", status="pending", entry_points=_eps(sink))
    # Assert
    assert _card_events(sink) == []


# === complete_task → completed =============================================


def test_complete_task_emits_completed(tmp_path: Path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="c-1", title="x", status="in_progress")
    sink = _Capturing()
    # Act
    complete_task(store, "c-1", by="operator", entry_points=_eps(sink))
    # Assert
    e = _card_events(sink, "completed")
    assert len(e) == 1 and e[0]["card_id"] == "c-1" and e[0]["actor"] == "operator"


def test_recomplete_done_task_emits_no_event(tmp_path: Path):
    # Arrange — first completion transitions; the second is an idempotent
    # no-op and must emit nothing.
    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="c-1", title="x", status="in_progress")
    complete_task(store, "c-1")
    sink = _Capturing()
    # Act
    complete_task(store, "c-1", entry_points=_eps(sink))
    # Assert
    assert _card_events(sink) == []


# === resolve_task → status_changed {from, to: done} =======================


def test_resolve_task_emits_status_changed_to_done(tmp_path: Path):
    # Arrange — a blocked card resolved to done.
    store = tmp_path / "tasks.yaml"
    add_task(
        store=store, id="c-1", title="x", status="blocked",
        blocker="operator-decision",
    )
    sink = _Capturing()
    # Act
    resolve_task(store, "c-1", actor="operator", entry_points=_eps(sink))
    # Assert
    e = _card_events(sink, "status_changed")
    assert len(e) == 1 and (e[0]["from"], e[0]["to"]) == ("blocked", "done")


def test_resolve_already_done_emits_no_event(tmp_path: Path):
    # Arrange — resolving an already-done card is a noop (no event).
    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="c-1", title="x", status="done")
    sink = _Capturing()
    # Act
    resolve_task(store, "c-1", actor="operator", entry_points=_eps(sink))
    # Assert
    assert _card_events(sink) == []


# === reassign_task — atomic owner change + reassigned event ================


def test_reassign_sets_agent_assignee_and_scope_atomically(tmp_path: Path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="c-1", title="x", agent="proj-old")
    # Act
    reassign_task(store, "c-1", "proj-new", by="operator")
    # Assert — all three owner fields move together in one write.
    t = [t for t in load_tasks(store) if t["id"] == "c-1"][0]
    assert t["agent"] == "proj-new"
    assert t["assignee"] == "proj-new"
    assert t["scope"] == "agent:proj-new"


def test_reassign_appends_audit_comment(tmp_path: Path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="c-1", title="x", agent="proj-old")
    # Act
    reassign_task(store, "c-1", "proj-new", by="operator")
    # Assert
    t = [t for t in load_tasks(store) if t["id"] == "c-1"][0]
    texts = [c.get("text") for c in t.get("comments") or []]
    assert any(
        "reassigned proj-old -> proj-new by operator" in (x or "") for x in texts
    )


def test_reassign_emits_reassigned_with_from_to_owner(tmp_path: Path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="c-1", title="x", agent="proj-old")
    sink = _Capturing()
    # Act
    reassign_task(store, "c-1", "proj-new", by="operator", entry_points=_eps(sink))
    # Assert
    e = _card_events(sink, "reassigned")
    assert len(e) == 1
    assert e[0]["card_id"] == "c-1"
    assert e[0]["from_owner"] == "proj-old"
    assert e[0]["to_owner"] == "proj-new"
    assert e[0]["actor"] == "operator"


def test_reassign_to_same_owner_is_noop_no_event(tmp_path: Path):
    # Arrange — current owner already proj-old.
    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="c-1", title="x", agent="proj-old")
    before = [t for t in load_tasks(store) if t["id"] == "c-1"][0]
    n_comments_before = len(before.get("comments") or [])
    sink = _Capturing()
    # Act
    result = reassign_task(
        store, "c-1", "proj-old", by="operator", entry_points=_eps(sink)
    )
    # Assert — no write (no new audit comment), no event, changed=False.
    after = [t for t in load_tasks(store) if t["id"] == "c-1"][0]
    assert result["changed"] is False
    assert len(after.get("comments") or []) == n_comments_before
    assert _card_events(sink, "reassigned") == []


def test_reassign_is_idempotent_second_call_noop(tmp_path: Path):
    # Arrange — reassign once; a second identical reassign is a no-op.
    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="c-1", title="x", agent="proj-old")
    reassign_task(store, "c-1", "proj-new", by="operator")
    sink = _Capturing()
    # Act
    result = reassign_task(
        store, "c-1", "proj-new", by="operator", entry_points=_eps(sink)
    )
    # Assert
    assert result["changed"] is False
    assert _card_events(sink, "reassigned") == []


def test_reassign_from_unassigned_records_placeholder(tmp_path: Path):
    # Arrange — a card with no owner at all.
    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="c-1", title="x")
    sink = _Capturing()
    # Act
    reassign_task(store, "c-1", "proj-new", by="operator", entry_points=_eps(sink))
    # Assert — from_owner is None on the event; the card is now owned.
    e = _card_events(sink, "reassigned")[0]
    assert e["from_owner"] is None and e["to_owner"] == "proj-new"
    t = [t for t in load_tasks(store) if t["id"] == "c-1"][0]
    assert t["agent"] == "proj-new"


# === FAIL-SOFT proof — a raising handler must not break the mutation =======


def _bad_eps() -> list[_FakeEP]:
    def _boom(_event):
        raise RuntimeError("handler exploded")

    return [_FakeEP("boom", _boom)]


def test_add_task_persists_even_when_emit_raises(tmp_path: Path):
    # Arrange / Act — a raising handler must not break add_task.
    store = tmp_path / "tasks.yaml"
    inserted = add_task(store=store, id="c-1", title="x", entry_points=_bad_eps())
    # Assert — returned normally AND the card is durably on disk.
    assert inserted["id"] == "c-1"
    assert [t for t in load_tasks(store) if t["id"] == "c-1"]


def test_complete_task_persists_even_when_emit_raises(tmp_path: Path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="c-1", title="x", status="in_progress")
    # Act — must NOT raise.
    complete_task(store, "c-1", entry_points=_bad_eps())
    # Assert — the done transition persisted.
    t = [t for t in load_tasks(store) if t["id"] == "c-1"][0]
    assert t["status"] == "done"


def test_update_status_persists_even_when_emit_raises(tmp_path: Path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="c-1", title="x", status="pending")
    # Act — must NOT raise.
    update_task(store, "c-1", status="in_progress", entry_points=_bad_eps())
    # Assert — the flip persisted.
    t = [t for t in load_tasks(store) if t["id"] == "c-1"][0]
    assert t["status"] == "in_progress"


def test_comment_task_persists_even_when_emit_raises(tmp_path: Path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="c-1", title="x")
    # Act — must NOT raise (covers BOTH the card-message and commented
    # emits in one shot — the bad handler receives both).
    comment_task(store=store, task_id="c-1", text="hi", entry_points=_bad_eps())
    # Assert — the comment landed on disk.
    t = [t for t in load_tasks(store) if t["id"] == "c-1"][0]
    assert any(c.get("text") == "hi" for c in t.get("comments") or [])


def test_reassign_persists_even_when_emit_raises(tmp_path: Path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    add_task(store=store, id="c-1", title="x", agent="proj-old")
    # Act — must NOT raise.
    result = reassign_task(store, "c-1", "proj-new", by="operator", entry_points=_bad_eps())
    # Assert — the owner change persisted and the call returned normally.
    assert result["changed"] is True
    t = [t for t in load_tasks(store) if t["id"] == "c-1"][0]
    assert t["agent"] == "proj-new" and t["scope"] == "agent:proj-new"


# EOF
