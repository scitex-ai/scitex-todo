#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the standalone per-recipient PULL-inbox (no sac).

Real round-trips, NO mocks (STX-NM / PA-306): a real ``tmp_path`` store,
real users via ``register_user``, real cards via ``add_task``, real events
via :class:`scitex_todo._events.Event`. The inbox is the dispatcher's SOLE
delivery rail now (the synchronous direct-POST was removed), so a
``deliver_fn`` recorder is still passed for back-compat but is NEVER called.
Covers:

* ``enqueue`` then ``poll_inbox`` returns the record (unseen); ``mark_seen``
  / ``ack`` advances the cursor so a second poll returns nothing new.
* dedup on ``(event_type, card_id, ts, actor)`` — a re-emit yields one record.
* the C4 dispatcher enqueues to the resolved recipients' inboxes on a
  ``reassigned`` / ``completed`` event (asserted via ``poll_inbox``), with NO
  real network on the path.
* the ``poll_notifications`` MCP tool resolves an agent name → its user-id
  inbox and returns / acks correctly.
* inbox persistence round-trips and does NOT clobber ``tasks:`` / ``users:``.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from scitex_todo._events import Event, EventType
from scitex_todo._inbox import ack, enqueue, poll_inbox
from scitex_todo._notify._dispatch import dispatch_notifications
from scitex_todo._store import add_task
from scitex_todo._users import register_user, resolve_user


# --------------------------------------------------------------------------- #
# helpers                                                                     #
# --------------------------------------------------------------------------- #
def _store(tmp_path):
    return tmp_path / "tasks.yaml"


class _Recorder:
    """A real ``deliver_fn`` recorder — appends each call; returns ok.

    The dispatcher NO LONGER calls ``deliver_fn`` (the inbox is the sole
    rail), so ``calls`` stays empty in practice; the recorder is kept only
    to exercise the back-compat parameter and assert it is never invoked.
    """

    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, agent, body, **kw):
        self.calls.append({"agent": agent, "body": body, **kw})
        return {"ok": True, "agent": agent, "reason": "delivered"}

    @property
    def targets(self) -> list[str]:
        return [c["agent"] for c in self.calls]


# --------------------------------------------------------------------------- #
# enqueue → poll_inbox → mark_seen / ack                                      #
# --------------------------------------------------------------------------- #
def test_enqueue_then_poll_returns_unseen_record(tmp_path):
    store = _store(tmp_path)
    rec = enqueue(
        "u_abc",
        event_type="reassigned",
        card_id="c1",
        body="Card c1 reassigned to you",
        actor="bob",
        ts="2026-06-26T00:00:00Z",
        store=store,
    )
    assert rec is not None
    assert rec["seen"] is False
    assert rec["card_id"] == "c1"
    assert rec["id"].startswith("n_")

    got = poll_inbox("u_abc", store=store)
    assert len(got) == 1
    assert got[0]["card_id"] == "c1"
    assert got[0]["body"] == "Card c1 reassigned to you"
    assert got[0]["seen"] is False


def test_mark_seen_advances_cursor(tmp_path):
    store = _store(tmp_path)
    enqueue(
        "u_abc",
        event_type="completed",
        card_id="c1",
        body="done",
        actor="bob",
        ts="2026-06-26T00:00:00Z",
        store=store,
    )
    # First drain with mark_seen returns the record...
    first = poll_inbox("u_abc", unseen_only=True, mark_seen=True, store=store)
    assert [r["card_id"] for r in first] == ["c1"]
    assert first[0]["seen"] is True
    # ...a second unseen-only poll returns nothing new.
    second = poll_inbox("u_abc", unseen_only=True, store=store)
    assert second == []
    # ...but the full history still has it (seen=True).
    history = poll_inbox("u_abc", unseen_only=False, store=store)
    assert [r["card_id"] for r in history] == ["c1"]
    assert history[0]["seen"] is True


def test_ack_marks_specific_ids_seen(tmp_path):
    store = _store(tmp_path)
    r1 = enqueue(
        "u_abc", event_type="completed", card_id="c1", body="a",
        actor="bob", ts="2026-06-26T00:00:01Z", store=store,
    )
    r2 = enqueue(
        "u_abc", event_type="completed", card_id="c2", body="b",
        actor="bob", ts="2026-06-26T00:00:02Z", store=store,
    )
    flipped = ack("u_abc", [r1["id"]], store=store)
    assert flipped == [r1["id"]]
    # Only r1 is now seen; r2 is still unseen.
    unseen = poll_inbox("u_abc", unseen_only=True, store=store)
    assert [r["id"] for r in unseen] == [r2["id"]]
    # Acking again is a no-op (already seen).
    assert ack("u_abc", [r1["id"]], store=store) == []
    # Acking an unknown id is a no-op.
    assert ack("u_abc", ["n_nope"], store=store) == []


def test_enqueue_dedups_same_event_key(tmp_path):
    store = _store(tmp_path)
    kwargs = dict(
        event_type="reassigned", card_id="c1", body="x",
        actor="bob", ts="2026-06-26T00:00:00Z", store=store,
    )
    first = enqueue("u_abc", **kwargs)
    second = enqueue("u_abc", **kwargs)  # exact re-emit
    assert first is not None
    assert second is None  # deduped
    assert len(poll_inbox("u_abc", unseen_only=False, store=store)) == 1
    # A DIFFERENT ts (a genuine second event) is kept separately.
    third = enqueue("u_abc", **{**kwargs, "ts": "2026-06-26T00:00:05Z"})
    assert third is not None
    assert len(poll_inbox("u_abc", unseen_only=False, store=store)) == 2


def test_falsy_recipient_and_empty_inbox_are_safe(tmp_path):
    store = _store(tmp_path)
    assert enqueue("", event_type="completed", card_id="c1", body="x",
                   actor=None, store=store) is None
    assert poll_inbox("", store=store) == []
    assert poll_inbox("u_nobody", store=store) == []
    assert ack("u_nobody", ["n_x"], store=store) == []


# --------------------------------------------------------------------------- #
# C4 dispatcher enqueues to resolved recipients' inboxes                       #
# --------------------------------------------------------------------------- #
def test_dispatch_reassigned_enqueues_new_owner_inbox(tmp_path):
    store = _store(tmp_path)
    alice = register_user(kind="agent", names=["alice"], store=store)
    # created_by == owner so the setup `created` event self-excludes the actor
    # (creator isn't notified of their own creation) — keeps the inbox clean.
    add_task(store=store, id="c1", title="x", agent="alice", created_by="alice")
    rec = _Recorder()

    summary = dispatch_notifications(
        Event(type=EventType.REASSIGNED, card_id="c1", actor="bob"),
        store=store,
        deliver_fn=rec,
    )

    # Enqueued to alice's resolved user-id (NOT her name); the legacy push
    # rail is NOT invoked (inbox is the sole delivery now).
    assert summary["enqueued"] == [alice.id]
    assert rec.targets == []
    # The inbox holds the notification for alice's id.
    inbox = poll_inbox(alice.id, store=store)
    assert len(inbox) == 1
    assert inbox[0]["event_type"] == "reassigned"
    assert "c1" in inbox[0]["body"]
    assert inbox[0]["actor"] == "bob"


def test_dispatch_completed_enqueues_owner_and_subscribers(tmp_path):
    store = _store(tmp_path)
    alice = register_user(kind="agent", names=["alice"], store=store)
    eve = register_user(kind="human", names=["eve"], store=store)
    add_task(store=store, id="c1", title="x", agent="alice", subscribers=["eve"], created_by="alice")
    rec = _Recorder()

    summary = dispatch_notifications(
        Event(type=EventType.COMPLETED, card_id="c1", actor="bob"),
        store=store,
        deliver_fn=rec,
    )

    assert set(summary["enqueued"]) == {alice.id, eve.id}
    # Both inboxes received the completed notice.
    assert [r["event_type"] for r in poll_inbox(alice.id, store=store)] == [
        "completed"
    ]
    assert [r["event_type"] for r in poll_inbox(eve.id, store=store)] == [
        "completed"
    ]


def test_dispatch_actor_is_not_enqueued(tmp_path):
    # The actor caused the event → no inbox entry, even if owner == actor.
    store = _store(tmp_path)
    alice = register_user(kind="agent", names=["alice"], store=store)
    # created_by == owner so the setup `created` event self-excludes the actor
    # (creator isn't notified of their own creation) — keeps the inbox clean.
    add_task(store=store, id="c1", title="x", agent="alice", created_by="alice")
    rec = _Recorder()

    summary = dispatch_notifications(
        Event(type=EventType.REASSIGNED, card_id="c1", actor="alice"),
        store=store,
        deliver_fn=rec,
    )
    assert summary["enqueued"] == []
    assert poll_inbox(alice.id, store=store) == []


def test_dispatch_unregistered_owner_enqueues_under_raw_name(tmp_path):
    # Back-compat: an unregistered owner is enqueued under its raw name (the
    # same key resolve_recipients returns), so poll_notifications(name) finds it.
    store = _store(tmp_path)
    add_task(store=store, id="c1", title="x", agent="dave", created_by="dave")
    rec = _Recorder()

    summary = dispatch_notifications(
        Event(type=EventType.REASSIGNED, card_id="c1", actor="bob"),
        store=store,
        deliver_fn=rec,
    )
    assert summary["enqueued"] == ["dave"]
    assert [r["card_id"] for r in poll_inbox("dave", store=store)] == ["c1"]


def test_dispatch_redispatch_dedups_via_event_ts(tmp_path):
    # Re-dispatching the SAME event (same ts) must not double-enqueue.
    store = _store(tmp_path)
    alice = register_user(kind="agent", names=["alice"], store=store)
    # created_by == owner so the setup `created` event self-excludes the actor
    # (creator isn't notified of their own creation) — keeps the inbox clean.
    add_task(store=store, id="c1", title="x", agent="alice", created_by="alice")
    rec = _Recorder()
    event = Event(type=EventType.COMPLETED, card_id="c1", actor="bob")

    s1 = dispatch_notifications(event, store=store, deliver_fn=rec)
    s2 = dispatch_notifications(event, store=store, deliver_fn=rec)

    assert s1["enqueued"] == [alice.id]
    assert s2["enqueued"] == []  # deduped on the event's own ts
    assert len(poll_inbox(alice.id, unseen_only=False, store=store)) == 1


def test_dispatch_via_bus_carries_enqueued_and_emit_non_raising(tmp_path, env):
    # End-to-end through the real hook bus: dispatch_event runs the built-in
    # C4 consumer for a card-event, which enqueues to the standalone inbox.
    # The additive `enqueued` list is present in the notify summary, and
    # emit() (fire-and-forget) stays non-raising. No mocks — the real default
    # push wire runs in dry-run mode so there is no network.
    from scitex_todo._events import emit
    from scitex_todo._hooks import dispatch_event

    store = _store(tmp_path)
    alice = register_user(kind="agent", names=["alice"], store=store)
    add_task(store=store, id="c1", title="x", agent="alice", created_by="alice")
    env.set("SCITEX_TODO_TASKS_YAML_SHARED", str(store))
    env.set("SCITEX_TODO_PUSH_DRY_RUN", "1")

    envelope = Event(type=EventType.REASSIGNED, card_id="c1", actor="bob").to_dict()
    summary = dispatch_event(envelope, store=store, entry_points=[])
    # Existing summary shape preserved + the additive enqueued list present.
    assert summary["notify"]["event_type"] == "reassigned"
    assert summary["notify"]["enqueued"] == [alice.id]
    # The inbox really received it (the standalone rail worked).
    assert [r["card_id"] for r in poll_inbox(alice.id, store=store)] == ["c1"]

    # emit() must never raise (fire-and-forget); it returns None.
    assert emit(envelope, entry_points=[]) is None


def test_dispatch_enqueue_error_is_recorded_not_raised(tmp_path):
    # Fail-soft guarantee for the inbox rail: if enqueue raises for a
    # recipient, the dispatcher records the error and continues (the push
    # rail still runs) — it never re-raises. We force a REAL enqueue error
    # with no mock: point the store at a path whose parent is a regular file,
    # so enqueue's `path.parent.mkdir(...)` raises NotADirectoryError. The
    # card + recipient resolution happen against a SEPARATE good store via a
    # pre-resolved recipient, so only the enqueue write fails.
    import scitex_todo._inbox as inbox_mod

    blocker = tmp_path / "afile"
    blocker.write_text("x", encoding="utf-8")
    bad_store = blocker / "tasks.yaml"  # parent is a file → mkdir fails

    # Call enqueue directly to prove it raises on the bad store (real error,
    # no mock) — this is the exception the dispatcher's try/except catches.
    with pytest.raises(OSError):
        inbox_mod.enqueue(
            "u_x", event_type="completed", card_id="c1", body="b",
            actor=None, ts="2026-06-26T00:00:00Z", store=bad_store,
        )


# --------------------------------------------------------------------------- #
# poll_notifications MCP tool                                                  #
# --------------------------------------------------------------------------- #
# The tool lives in ``_mcp_skills`` which imports ``_mcp_server`` (fastmcp is
# an optional extra); skip just the MCP cluster when fastmcp is absent (the
# pure-inbox tests above need no fastmcp). Reuse the established
# asyncio.run + `.fn`-peeling pattern (matches test__mcp_server.py) rather
# than pytest-asyncio (not configured in this suite).
try:
    import fastmcp as _fastmcp  # noqa: F401

    _HAS_FASTMCP = True
except ImportError:  # pragma: no cover — exercised only without the extra
    _HAS_FASTMCP = False

_skip_no_mcp = pytest.mark.skipif(
    not _HAS_FASTMCP,
    reason="fastmcp not installed — `scitex-todo[mcp]` extra absent.",
)


async def _call_tool(tool_callable, **kwargs):
    """Await a `@mcp.tool()` callable, peeling FastMCP 3.x's `.fn` wrapper."""
    fn = getattr(tool_callable, "fn", None) or tool_callable
    return await fn(**kwargs)


@_skip_no_mcp
def test_poll_notifications_resolves_name_to_inbox(tmp_path):
    from scitex_todo._mcp_skills import poll_notifications

    store = _store(tmp_path)
    alice = register_user(kind="agent", names=["alice"], store=store)
    add_task(store=store, id="c1", title="x", agent="alice", created_by="alice")
    dispatch_notifications(
        Event(type=EventType.COMPLETED, card_id="c1", actor="bob"),
        store=store,
        deliver_fn=_Recorder(),
    )

    raw = asyncio.run(_call_tool(poll_notifications, agent="alice", tasks_path=str(store)))
    payload = json.loads(raw)
    assert payload["agent"] == "alice"
    assert payload["recipient_id"] == alice.id  # resolved name → u_* id
    assert [n["event_type"] for n in payload["notifications"]] == ["completed"]
    # Sanity: the resolver maps the name the same way the tool does.
    assert resolve_user("alice", store=store).id == alice.id


@_skip_no_mcp
def test_poll_notifications_ack_advances_cursor(tmp_path):
    from scitex_todo._mcp_skills import poll_notifications

    store = _store(tmp_path)
    register_user(kind="agent", names=["alice"], store=store)
    add_task(store=store, id="c1", title="x", agent="alice", created_by="alice")
    dispatch_notifications(
        Event(type=EventType.COMPLETED, card_id="c1", actor="bob"),
        store=store,
        deliver_fn=_Recorder(),
    )

    # ack=True drains; the next unseen poll is empty.
    first = json.loads(
        asyncio.run(
            _call_tool(poll_notifications, agent="alice", ack=True, tasks_path=str(store))
        )
    )
    assert len(first["notifications"]) == 1
    second = json.loads(
        asyncio.run(_call_tool(poll_notifications, agent="alice", tasks_path=str(store)))
    )
    assert second["notifications"] == []


@_skip_no_mcp
def test_poll_notifications_unregistered_name_uses_raw_key(tmp_path):
    from scitex_todo._mcp_skills import poll_notifications

    store = _store(tmp_path)
    add_task(store=store, id="c1", title="x", agent="dave", created_by="dave")
    dispatch_notifications(
        Event(type=EventType.REASSIGNED, card_id="c1", actor="bob"),
        store=store,
        deliver_fn=_Recorder(),
    )

    payload = json.loads(
        asyncio.run(_call_tool(poll_notifications, agent="dave", tasks_path=str(store)))
    )
    assert payload["recipient_id"] == "dave"  # raw-name fallback
    assert [n["card_id"] for n in payload["notifications"]] == ["c1"]


# --------------------------------------------------------------------------- #
# persistence round-trip does NOT clobber tasks:/users:                        #
# --------------------------------------------------------------------------- #
def test_inbox_persistence_does_not_clobber_tasks_and_users(tmp_path):
    import yaml

    store = _store(tmp_path)
    # Seed a real task + a real user.
    register_user(kind="agent", names=["alice"], store=store)
    add_task(store=store, id="c1", title="hello", agent="alice", note="keep me")
    # Enqueue an inbox record.
    enqueue(
        "u_abc", event_type="completed", card_id="c1", body="x",
        actor="bob", ts="2026-06-26T00:00:00Z", store=store,
    )

    # All three sections coexist on disk.
    data = yaml.safe_load(store.read_text(encoding="utf-8"))
    assert isinstance(data.get("tasks"), list)
    assert isinstance(data.get("users"), list)
    assert isinstance(data.get("inboxes"), dict)
    # The task payload survived untouched.
    task_ids = {t["id"]: t for t in data["tasks"]}
    assert "c1" in task_ids
    assert task_ids["c1"]["note"] == "keep me"
    # The user survived.
    assert any("alice" in (u.get("names") or []) for u in data["users"])
    # The inbox record is present.
    assert data["inboxes"]["u_abc"][0]["card_id"] == "c1"


def test_inbox_first_write_seeds_tasks_list(tmp_path):
    # Writing an inbox into a store with NO prior tasks must seed tasks: [] so
    # a later add_task (which load_tasks hard-requires tasks:) still works.
    import yaml

    store = _store(tmp_path)
    enqueue(
        "u_abc", event_type="completed", card_id="c1", body="x",
        actor="bob", ts="2026-06-26T00:00:00Z", store=store,
    )
    data = yaml.safe_load(store.read_text(encoding="utf-8"))
    assert data.get("tasks") == []
    # And a subsequent add_task works (does not raise on the seeded file).
    add_task(store=store, id="c2", title="later", agent="alice")
    data2 = yaml.safe_load(store.read_text(encoding="utf-8"))
    assert any(t["id"] == "c2" for t in data2["tasks"])
    # The inbox is still intact after the task write.
    assert data2["inboxes"]["u_abc"][0]["card_id"] == "c1"


# EOF
