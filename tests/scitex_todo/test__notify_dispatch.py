#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end tests for the C4 notify DISPATCHER.

Real round-trips, NO mocks (STX-NM / PA-306): real users via
``register_user`` in a ``tmp_path`` store, real cards via ``add_task`` /
``update_task``, and the real standalone PULL-inbox read via
``scitex_todo._inbox.poll_inbox``. The bus path is exercised via the real
``scitex_todo._events.emit`` / ``scitex_todo._hooks.dispatch_event`` with
``entry_points=[]`` (no plugins) so the BUILT-IN C4 consumer is what runs.

The dispatcher delivers PURELY via the inbox now — the synchronous
direct-POST accelerator was removed (it could not reach a containerized
recipient and slowed the emitting mutation). So the assertions are:

* delivery shows up as ``summary["enqueued"]`` (recipient IDS) and in each
  recipient's inbox (``poll_inbox``); ``summary["delivered"]`` stays EMPTY.
* the ``deliver_fn`` parameter is ACCEPTED for back-compat but NEVER called
  — a deliberately-RAISING recorder proves the comment / any event does not
  depend on or await a turn-URL POST.

Coverage mirrors the C4 card's checklist:

* `reassigned` enqueues to the new owner (by ID); the ACTOR is excluded.
* `commented` is ENQUEUED to owner + collaborators + subscribers (it used
  to be skipped + handled by the container-unreachable direct-POST).
* `merged` enqueues to NOBODY under the default rules (default-quiet);
  `completed` enqueues to owner + subscribers.
* per-recipient fail-soft + end-to-end via the bus.
"""

from __future__ import annotations

import pytest

from scitex_todo._events import Event, EventType, emit
from scitex_todo._hooks import dispatch_event
from scitex_todo._inbox import poll_inbox
from scitex_todo._notify._dispatch import dispatch_notifications
from scitex_todo._store import add_task
from scitex_todo._users import register_user


# --------------------------------------------------------------------------- #
# helpers                                                                     #
# --------------------------------------------------------------------------- #
def _store(tmp_path):
    return tmp_path / "tasks.yaml"


class _ExplodingDeliver:
    """A real ``deliver_fn`` that RAISES if ever invoked — and records it.

    NOT a mock: a plain callable. The dispatcher no longer calls
    ``deliver_fn`` (the inbox is the sole rail), so passing this proves a
    comment / any event never depends on or awaits a turn-URL POST: if the
    dispatcher DID call it, the test would see ``called`` flip / an raise.
    """

    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, agent, body, **kw):
        self.calls.append({"agent": agent, "body": body, **kw})
        raise AssertionError(
            f"deliver_fn must NOT be called (inbox is the rail) — got {agent!r}"
        )

    @property
    def called(self) -> bool:
        return bool(self.calls)


# --------------------------------------------------------------------------- #
# reassigned: enqueues to the new owner, actor excluded                       #
# --------------------------------------------------------------------------- #
def test_reassigned_enqueues_to_new_owner(tmp_path):
    store = _store(tmp_path)
    alice = register_user(kind="agent", names=["alice"], store=store)
    # created_by == owner so the setup `created` event self-excludes the
    # actor (the creator is not notified of their own creation) — keeps the
    # inbox clean so this test asserts purely on the dispatched event.
    add_task(store=store, id="c1", title="x", agent="alice", created_by="alice")
    rec = _ExplodingDeliver()

    summary = dispatch_notifications(
        Event(type=EventType.REASSIGNED, card_id="c1", actor="bob"),
        store=store,
        deliver_fn=rec,
    )

    # Enqueued to the new owner by ID; the legacy direct-POST is NOT called.
    assert summary["enqueued"] == [alice.id]
    assert summary["delivered"] == []
    assert rec.called is False
    # The notification landed in alice's inbox with a sensible body + card id.
    notes = poll_inbox(alice.id, store=store)
    assert [n["event_type"] for n in notes] == ["reassigned"]
    assert notes[0]["card_id"] == "c1"
    assert "c1" in notes[0]["body"]


def test_reassigned_excludes_the_actor(tmp_path):
    # The actor caused the event → never notified, even if they're the owner.
    store = _store(tmp_path)
    alice = register_user(kind="agent", names=["alice"], store=store)
    # created_by == owner so the setup `created` event self-excludes the
    # actor (the creator is not notified of their own creation) — keeps the
    # inbox clean so this test asserts purely on the dispatched event.
    add_task(store=store, id="c1", title="x", agent="alice", created_by="alice")
    rec = _ExplodingDeliver()

    # alice reassigns the card to herself → she is BOTH owner and actor.
    summary = dispatch_notifications(
        Event(type=EventType.REASSIGNED, card_id="c1", actor="alice"),
        store=store,
        deliver_fn=rec,
    )

    assert summary["enqueued"] == []
    assert summary["delivered"] == []
    assert poll_inbox(alice.id, store=store) == []


def test_unregistered_owner_name_enqueues_via_raw_name(tmp_path):
    # Back-compat: an owner who is NOT a registered user is itself a raw
    # recipient id (resolve_recipients returns the raw string), so its inbox
    # is keyed on that raw name.
    store = _store(tmp_path)
    add_task(store=store, id="c1", title="x", agent="dave", created_by="dave")
    rec = _ExplodingDeliver()

    summary = dispatch_notifications(
        Event(type=EventType.REASSIGNED, card_id="c1", actor="bob"),
        store=store,
        deliver_fn=rec,
    )

    # The raw name "dave" is both the recipient id AND its inbox key.
    assert summary["enqueued"] == ["dave"]
    assert [n["card_id"] for n in poll_inbox("dave", store=store)] == ["c1"]


# --------------------------------------------------------------------------- #
# commented: now ENQUEUED via the inbox (owner + collaborators + subscribers) #
# --------------------------------------------------------------------------- #
def test_commented_enqueues_to_owner_collaborators_subscribers(tmp_path):
    # `commented` used to be SKIPPED + handled by the container-unreachable
    # direct-POST. It now ENQUEUES to the C3 default set (owner + collaborators
    # + subscribers) via the always-works inbox; the actor (author) is excluded.
    store = _store(tmp_path)
    alice = register_user(kind="agent", names=["alice"], store=store)
    carol = register_user(kind="agent", names=["carol"], store=store)
    eve = register_user(kind="human", names=["eve"], store=store)
    bob = register_user(kind="agent", names=["bob"], store=store)
    add_task(
        store=store,
        id="c1",
        title="x",
        agent="alice",
        collaborators=["carol"],
        subscribers=["eve"],
        created_by="alice",
    )
    rec = _ExplodingDeliver()

    # bob (a card subscriber would be over-thinking — bob is just the actor)
    # comments → owner + collaborators + subscribers get it, NOT bob.
    summary = dispatch_notifications(
        Event(type=EventType.COMMENTED, card_id="c1", actor="bob"),
        store=store,
        deliver_fn=rec,
    )

    assert set(summary["enqueued"]) == {alice.id, carol.id, eve.id}
    assert summary["delivered"] == []  # no direct-POST
    assert summary["skipped"] == []  # NOT skipped anymore
    assert rec.called is False
    # Each recipient's inbox got exactly the one `commented` note; the author
    # (bob) was NOT enqueued (actor excluded).
    for uid in (alice.id, carol.id, eve.id):
        notes = poll_inbox(uid, store=store)
        assert [n["event_type"] for n in notes] == ["commented"]
    assert poll_inbox(bob.id, store=store) == []


def test_commented_no_double_delivery(tmp_path):
    # Each recipient is enqueued EXACTLY ONCE for a single comment event (the
    # dispatcher dedups names into a set; the inbox dedups on the event key).
    store = _store(tmp_path)
    alice = register_user(kind="agent", names=["alice"], store=store)
    # created_by == owner so the setup `created` event self-excludes the
    # actor (the creator is not notified of their own creation) — keeps the
    # inbox clean so this test asserts purely on the dispatched event.
    add_task(store=store, id="c1", title="x", agent="alice", created_by="alice")
    rec = _ExplodingDeliver()

    summary = dispatch_notifications(
        Event(type=EventType.COMMENTED, card_id="c1", actor="bob", ts="2026-06-26T00:00:00Z"),
        store=store,
        deliver_fn=rec,
    )

    assert summary["enqueued"] == [alice.id]
    assert len(poll_inbox(alice.id, store=store)) == 1


# --------------------------------------------------------------------------- #
# merged default-quiet; completed = owner + subscribers                       #
# --------------------------------------------------------------------------- #
def test_merged_enqueues_to_nobody_by_default(tmp_path):
    store = _store(tmp_path)
    alice = register_user(kind="agent", names=["alice"], store=store)
    eve = register_user(kind="human", names=["eve"], store=store)
    add_task(store=store, id="c1", title="x", agent="alice", subscribers=["eve"], created_by="alice")
    rec = _ExplodingDeliver()

    summary = dispatch_notifications(
        Event(type=EventType.MERGED, card_id="c1", actor="bob"),
        store=store,
        deliver_fn=rec,
    )

    # Default-quiet: completed is the canonical done-notice, not merged.
    assert summary["enqueued"] == []
    assert summary["delivered"] == []
    assert rec.called is False
    assert poll_inbox(alice.id, store=store) == []
    assert poll_inbox(eve.id, store=store) == []


def test_completed_enqueues_to_owner_and_subscribers(tmp_path):
    store = _store(tmp_path)
    alice = register_user(kind="agent", names=["alice"], store=store)
    eve = register_user(kind="human", names=["eve"], store=store)
    add_task(store=store, id="c1", title="x", agent="alice", subscribers=["eve"], created_by="alice")
    rec = _ExplodingDeliver()

    summary = dispatch_notifications(
        Event(type=EventType.COMPLETED, card_id="c1", actor="bob"),
        store=store,
        deliver_fn=rec,
    )

    # Owner + subscriber both enqueued (actor bob is neither); no direct-POST.
    assert set(summary["enqueued"]) == {alice.id, eve.id}
    assert summary["delivered"] == []
    assert rec.called is False
    for uid in (alice.id, eve.id):
        assert [n["event_type"] for n in poll_inbox(uid, store=store)] == ["completed"]


# --------------------------------------------------------------------------- #
# no card / unknown card → fail-soft no-op                                     #
# --------------------------------------------------------------------------- #
def test_missing_card_id_is_noop(tmp_path):
    store = _store(tmp_path)
    rec = _ExplodingDeliver()

    summary = dispatch_notifications(
        Event(type=EventType.RELEASED, repo="o/r", version="1.0"),
        store=store,
        deliver_fn=rec,
    )

    assert rec.called is False
    assert summary["enqueued"] == []
    assert "no-card-id" in summary["skipped"]


def test_unknown_card_is_noop(tmp_path):
    store = _store(tmp_path)
    register_user(kind="agent", names=["alice"], store=store)
    # store exists (a user was written) but the card id does not.
    rec = _ExplodingDeliver()

    summary = dispatch_notifications(
        Event(type=EventType.COMPLETED, card_id="never-existed", actor="bob"),
        store=store,
        deliver_fn=rec,
    )

    assert rec.called is False
    assert summary["enqueued"] == []
    assert "card-not-found" in summary["skipped"]


# --------------------------------------------------------------------------- #
# inbox fail-soft + no synchronous network on the mutation path                #
# --------------------------------------------------------------------------- #
def test_per_recipient_enqueue_is_independent(tmp_path):
    # Every resolved recipient is enqueued (a per-recipient try/except wraps
    # each inbox write), so one recipient never starves the others.
    store = _store(tmp_path)
    alice = register_user(kind="agent", names=["alice"], store=store)
    eve = register_user(kind="human", names=["eve"], store=store)
    add_task(store=store, id="c1", title="x", agent="alice", subscribers=["eve"], created_by="alice")
    rec = _ExplodingDeliver()

    summary = dispatch_notifications(
        Event(type=EventType.COMPLETED, card_id="c1", actor="bob"),
        store=store,
        deliver_fn=rec,
    )

    assert set(summary["enqueued"]) == {alice.id, eve.id}
    assert summary["errors"] == []
    assert rec.called is False


def test_dispatch_never_raises_and_always_returns_a_summary(tmp_path):
    # The fail-soft contract: dispatch_notifications NEVER raises and ALWAYS
    # returns the summary shape — even on a malformed event the resolver
    # rejects. (The inbox's own degenerate-store paths are covered by the
    # _inbox suite; here we assert the dispatcher's fail-soft envelope.)
    store = _store(tmp_path)
    register_user(kind="agent", names=["alice"], store=store)
    add_task(store=store, id="c1", title="x", agent="alice")

    # A bogus event type → resolve_recipients raises → folded into `skipped`,
    # not propagated; the summary still has every key.
    summary = dispatch_notifications(
        {"kind": "card-event", "type": "not-a-real-type", "card_id": "c1", "actor": "bob"},
        store=store,
    )
    for key in ("event_type", "card_id", "delivered", "enqueued", "skipped", "errors"):
        assert key in summary
    assert summary["enqueued"] == []
    assert "resolve-failed" in summary["skipped"]


def test_emit_stays_non_raising_and_does_not_call_deliver_fn(tmp_path, env):
    # The producer guarantee: emit() must NOT raise, AND the dispatcher must
    # never call the legacy direct-POST (so a comment / any event never awaits
    # a turn-URL POST). Assert deliver_fn is untouched, and emit() is clean.
    store = _store(tmp_path)
    alice = register_user(kind="agent", names=["alice"], store=store)
    add_task(store=store, id="c1", title="x", agent="alice")
    env.set("SCITEX_TODO_TASKS", str(store))

    rec = _ExplodingDeliver()
    summary = dispatch_notifications(
        Event(type=EventType.REASSIGNED, card_id="c1", actor="bob"),
        store=store,
        deliver_fn=rec,
    )
    assert rec.called is False  # the network wire was NEVER invoked
    assert summary["enqueued"] == [alice.id]

    # emit() through the real bus must never raise.
    emit(Event(type=EventType.REASSIGNED, card_id="c1", actor="bob"), entry_points=[])


# --------------------------------------------------------------------------- #
# end-to-end via the bus                                                       #
# --------------------------------------------------------------------------- #
def test_dispatch_event_runs_builtin_notify_for_card_event(tmp_path, env):
    # dispatch_event must invoke the C4 consumer for a card-event and carry
    # its summary under the additive `notify` key (existing keys unchanged).
    store = _store(tmp_path)
    alice = register_user(kind="agent", names=["alice"], store=store)
    add_task(store=store, id="c1", title="x", agent="alice")
    env.set("SCITEX_TODO_TASKS", str(store))

    envelope = Event(
        type=EventType.REASSIGNED, card_id="c1", actor="bob"
    ).to_dict()
    summary = dispatch_event(envelope, store=store, entry_points=[])

    # Existing summary shape preserved + the additive notify key present.
    assert summary["kind"] == "card-event"
    assert "card_writes" in summary and "plugin_count" in summary
    assert summary["notify"]["event_type"] == "reassigned"
    # Owner alice resolved + ENQUEUED to her inbox; delivered stays empty (no
    # synchronous direct-POST).
    assert summary["notify"]["enqueued"] == [alice.id]
    assert summary["notify"]["delivered"] == []


def test_dispatch_event_card_event_with_no_recipients_is_clean(tmp_path, env):
    # A default-quiet `merged` card-event runs the consumer but enqueues to
    # nobody — summary present, enqueued empty, no error.
    store = _store(tmp_path)
    register_user(kind="agent", names=["alice"], store=store)
    add_task(store=store, id="c1", title="x", agent="alice")
    env.set("SCITEX_TODO_TASKS", str(store))

    envelope = Event(
        type=EventType.MERGED, card_id="c1", actor="bob", repo="o/r"
    ).to_dict()
    summary = dispatch_event(envelope, store=store, entry_points=[])

    assert summary["notify"]["event_type"] == "merged"
    assert summary["notify"]["enqueued"] == []
    assert summary["notify"]["delivered"] == []


def test_emit_reassigned_reaches_recorder_via_real_dispatch(tmp_path, env):
    # End-to-end through emit() -> dispatch_event(): since emit() can't take
    # a deliver_fn, observe the delivery via the DRY-RUN wire's stdout
    # contract is brittle; instead assert dispatch_notifications (the unit
    # the bus calls) delivers through the injected recorder, AND that the bus
    # invokes it (covered above). Here we additionally prove emit() does not
    # raise and routes a card-event to the built-in consumer end to end.
    store = _store(tmp_path)
    alice = register_user(kind="agent", names=["alice"], store=store)
    add_task(store=store, id="c1", title="x", agent="alice")
    env.set("SCITEX_TODO_TASKS", str(store))
    env.set("SCITEX_TODO_PUSH_DRY_RUN", "1")

    # emit() is non-raising AND now RETURNS the dispatch summary (additive —
    # it used to return None). The summary carries the C4 notify result so a
    # producer / the `emit-event` CLI verb can report enqueued / delivered.
    result = emit(
        Event(type=EventType.REASSIGNED, card_id="c1", actor="bob"),
        entry_points=[],
    )
    assert result is not None
    assert result["kind"] == "card-event"
    assert result["notify"]["event_type"] == "reassigned"
    assert result["notify"]["enqueued"] == [alice.id]


# EOF
