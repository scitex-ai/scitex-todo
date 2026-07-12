#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the standalone notification-delivery loop (slice 1).

Real round-trips, NO mocks (STX-NM / PA-306): a real ``tmp_path`` store,
real notifications seeded via :func:`scitex_cards._inbox.enqueue`, a real
``recipients.yaml`` written into the store dir, and REAL fake channels
(``RecorderChannel`` / ``FlakyChannel``) injected through the
``channels=`` / ``extra_providers=`` seams.

Covers the spec's required cases:
* (a) a notification is delivered once and not re-delivered (ledger dedup).
* (b) a raising channel is recorded failed, becomes retry-eligible after
      backoff, and a later run re-attempts + can succeed.
* (c) ``should_deliver_now``=False yields ``skipped`` and does NOT consume
      the user's inbox ``seen`` cursor.
* (d) the loop NEVER flips inbox ``seen`` (unseen count unchanged after a run).
"""

from __future__ import annotations

import datetime as _dt

import yaml

from scitex_cards._delivery import _recipients
from scitex_cards._delivery._ledger import BASE_BACKOFF_SEC, MAX_ATTEMPTS, Ledger
from scitex_cards._delivery._loop import deliver_pending
from scitex_cards._inbox import enqueue, poll_inbox

from ._fakes import AlwaysFailChannel, FlakyChannel, RecorderChannel


# --------------------------------------------------------------------------- #
# helpers                                                                     #
# --------------------------------------------------------------------------- #
def _store(tmp_path):
    return tmp_path / "tasks.yaml"


def _write_recipients(tmp_path, mapping: dict) -> None:
    """Write ``recipients.yaml`` next to the store with ``{users: mapping}``."""
    path = tmp_path / "recipients.yaml"
    path.write_text(yaml.safe_dump({"users": mapping}), encoding="utf-8")


def _seed(store, recipient: str, *, card_id="c1", ts="2026-06-27T10:00:00Z"):
    """Enqueue one notification into ``recipient``'s inbox; return its id."""
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


# --------------------------------------------------------------------------- #
# (a) delivered once, not re-delivered — ledger dedup                         #
# --------------------------------------------------------------------------- #
def test_delivered_once_then_not_redelivered(tmp_path):
    store = _store(tmp_path)
    note_id = _seed(store, "u_alice")
    _write_recipients(tmp_path, {"u_alice": {"channels": [{"kind": "log"}]}})

    recorder = RecorderChannel(name="log")

    s1 = deliver_pending(store=store, channels={"log": recorder})
    assert s1["sent"] == 1
    assert s1["failed"] == 0
    assert len(recorder.calls) == 1
    assert recorder.calls[0]["recipient"] == "u_alice"
    assert recorder.calls[0]["notification"]["id"] == note_id

    # Second run: ledger says already-sent → no re-delivery, no new call.
    s2 = deliver_pending(store=store, channels={"log": recorder})
    assert s2["sent"] == 0
    assert s2["outcomes"] == []
    assert len(recorder.calls) == 1  # unchanged

    led = Ledger.load(store)
    assert led.already_done("u_alice", note_id, "log") is True


# --------------------------------------------------------------------------- #
# (b) raising channel → failed, retry-eligible after backoff, later succeeds  #
# --------------------------------------------------------------------------- #
def test_raising_channel_recorded_failed_then_retried_succeeds(tmp_path):
    store = _store(tmp_path)
    note_id = _seed(store, "u_bob")
    _write_recipients(tmp_path, {"u_bob": {"channels": [{"kind": "log"}]}})

    flaky = FlakyChannel(name="log", fail_times=1)
    t0 = _dt.datetime(2026, 6, 27, 10, 0, 0, tzinfo=_dt.timezone.utc)

    # First run: channel raises → recorded failed.
    s1 = deliver_pending(store=store, channels={"log": flaky}, now=t0)
    assert s1["failed"] == 1
    assert s1["sent"] == 0
    led = Ledger.load(store)
    assert led.already_done("u_bob", note_id, "log") is False
    assert led.has_failure("u_bob", note_id, "log") is True

    # Immediately after: NOT yet retry-eligible (within backoff window).
    assert led.retry_eligible("u_bob", note_id, "log", t0) is False
    # A run "now" is a silent no-op (no new attempt).
    s_noop = deliver_pending(store=store, channels={"log": flaky}, now=t0)
    assert s_noop["outcomes"] == []
    assert flaky.attempts == 1  # not re-attempted

    # After the backoff window: retry-eligible, and the now-recovered
    # channel succeeds.
    later = t0 + _dt.timedelta(seconds=BASE_BACKOFF_SEC + 1)
    led2 = Ledger.load(store)
    assert led2.retry_eligible("u_bob", note_id, "log", later) is True

    s2 = deliver_pending(store=store, channels={"log": flaky}, now=later)
    assert s2["sent"] == 1
    assert flaky.attempts == 2
    assert Ledger.load(store).already_done("u_bob", note_id, "log") is True


# --------------------------------------------------------------------------- #
# (b2) permanently-down channel → TERMINAL comm-miss, surfaced not dropped     #
# --------------------------------------------------------------------------- #
def test_permanent_failure_becomes_terminal_and_surfaced(tmp_path, capsys):
    store = _store(tmp_path)
    note_id = _seed(store, "u_frank")
    _write_recipients(tmp_path, {"u_frank": {"channels": [{"kind": "log"}]}})

    chan = AlwaysFailChannel(name="log")
    t = _dt.datetime(2026, 6, 27, 10, 0, 0, tzinfo=_dt.timezone.utc)

    # Drive attempts, advancing well past each backoff window between runs.
    terminal_seen = False
    for _ in range(MAX_ATTEMPTS + 3):
        s = deliver_pending(store=store, channels={"log": chan}, now=t)
        if s["failed_terminal"]:
            terminal_seen = True
            assert s["failed_terminal"] == 1
            assert any(o["outcome"] == "failed_terminal" for o in s["outcomes"])
            break
        t = t + _dt.timedelta(hours=2)

    assert terminal_seen
    assert chan.attempts == MAX_ATTEMPTS  # exactly MAX tries, then it gives up.

    # The comm-miss is surfaced LOUDLY to stderr (not silently dropped).
    err = capsys.readouterr().err
    assert "TERMINAL comm-miss" in err
    assert note_id in err

    # Ledger marks it terminal; a later run NEVER re-attempts and does NOT
    # re-warn (no per-run spam) — it is a persistent, queryable comm-miss.
    led = Ledger.load(store)
    assert led.is_terminal("u_frank", note_id, "log") is True
    later = t + _dt.timedelta(days=1)
    s_after = deliver_pending(store=store, channels={"log": chan}, now=later)
    assert s_after["outcomes"] == []
    assert chan.attempts == MAX_ATTEMPTS  # unchanged — terminal item noop'd.
    assert "TERMINAL" not in capsys.readouterr().err  # not re-warned.


# --------------------------------------------------------------------------- #
# (c) should_deliver_now=False → skipped, does NOT consume seen cursor        #
# --------------------------------------------------------------------------- #
def test_policy_gate_false_yields_skipped_and_preserves_seen(tmp_path, env):
    store = _store(tmp_path)
    note_id = _seed(store, "u_carol")
    _write_recipients(tmp_path, {"u_carol": {"channels": [{"kind": "log"}]}})

    recorder = RecorderChannel(name="log")

    # Force the policy gate closed by overriding the module-level function on
    # the loop's import (NOT a mock — a real callable swap restored after).
    from scitex_cards._delivery import _loop as loop_mod

    original = loop_mod.should_deliver_now
    loop_mod.should_deliver_now = lambda user, note: False
    try:
        s = deliver_pending(store=store, channels={"log": recorder})
    finally:
        loop_mod.should_deliver_now = original

    assert s["skipped"] == 1
    assert s["sent"] == 0
    # The channel was NEVER asked to deliver (policy gate is before transport).
    assert recorder.calls == []
    # NOT terminal: the ledger records skipped, never sent/failed.
    led = Ledger.load(store)
    assert led.already_done("u_carol", note_id, "log") is False
    assert led.has_failure("u_carol", note_id, "log") is False

    # The user's inbox seen cursor is UNTOUCHED — the note is still unseen.
    unseen = poll_inbox("u_carol", unseen_only=True, mark_seen=False, store=store)
    assert len(unseen) == 1
    assert unseen[0]["id"] == note_id
    assert unseen[0]["seen"] is False


# --------------------------------------------------------------------------- #
# (d) the loop NEVER flips inbox seen (unseen count unchanged)                #
# --------------------------------------------------------------------------- #
def test_loop_never_flips_inbox_seen(tmp_path):
    store = _store(tmp_path)
    _seed(store, "u_dave", card_id="c1", ts="2026-06-27T10:00:00Z")
    _seed(store, "u_dave", card_id="c2", ts="2026-06-27T11:00:00Z")
    _write_recipients(tmp_path, {"u_dave": {"channels": [{"kind": "log"}]}})

    before = poll_inbox("u_dave", unseen_only=True, mark_seen=False, store=store)
    assert len(before) == 2

    recorder = RecorderChannel(name="log")
    s = deliver_pending(store=store, channels={"log": recorder})
    assert s["sent"] == 2

    # The seen cursor is delivery-independent: still 2 unseen after delivery.
    after = poll_inbox("u_dave", unseen_only=True, mark_seen=False, store=store)
    assert len(after) == 2
    assert {n["id"] for n in before} == {n["id"] for n in after}


# --------------------------------------------------------------------------- #
# missing recipients.yaml → no recipients, no crash                          #
# --------------------------------------------------------------------------- #
def test_missing_recipients_file_is_empty_no_crash(tmp_path):
    store = _store(tmp_path)
    _seed(store, "u_eve")  # a notification exists, but no recipients.yaml.
    recorder = RecorderChannel(name="log")
    s = deliver_pending(store=store, channels={"log": recorder})
    assert s == {
        "sent": 0,
        "failed": 0,
        "skipped": 0,
        "failed_terminal": 0,
        "outcomes": [],
    }
    assert recorder.calls == []


# --------------------------------------------------------------------------- #
# load_recipients parsing (address optional, malformed dropped)              #
# --------------------------------------------------------------------------- #
def test_load_recipients_parses_channels_and_addresses(tmp_path):
    store = _store(tmp_path)
    # Touch the store so the parent dir resolves to tmp_path.
    enqueue(
        "u_x",
        event_type="reassigned",
        card_id="c1",
        body="x",
        actor="a",
        ts="2026-06-27T10:00:00Z",
        store=store,
    )
    _write_recipients(
        tmp_path,
        {
            "u_x": {
                "channels": [
                    {"kind": "log"},
                    {"kind": "telegram", "address": "12345"},
                    {"address": "no-kind-dropped"},  # missing kind → dropped
                ]
            },
            "u_empty": {"channels": []},  # no channels → recipient dropped
        },
    )
    recips = _recipients.load_recipients(store)
    assert [r.user for r in recips] == ["u_x"]
    chans = recips[0].channels
    assert [c.kind for c in chans] == ["log", "telegram"]
    assert chans[0].address == ""  # log needs none
    assert chans[1].address == "12345"


# EOF
