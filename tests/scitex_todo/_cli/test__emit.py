#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the `emit-event` + `resolve-card` producer CLI verbs (no mocks).

The generic no-import shell-out seam fleet producers (scitex-dev's C7
``released`` / C8 ``pulled`` + future) use to emit canonical card-events
WITHOUT importing :mod:`scitex_todo`. Real ``tmp_path`` store, real users
via ``register_user``, real cards via ``add_task``, CLI driven through
``CliRunner.invoke(main, [...])`` exactly like the sibling CLI tests — no
mocks (STX-NM / PA-306). Covers:

* ``emit-event --type pulled --repo X`` (no card_id) → exits 0; the C4
  consumer no-ops (nothing to resolve), asserted via the JSON summary.
* ``emit-event --type released --card-id C --repo R --version V --actor A``
  on a card with a subscriber → the event reaches the standalone INBOX of
  that subscriber (end-to-end: emit → bus → C4 → inbox; asserted via
  ``poll_inbox`` AND the printed ``notify.enqueued``).
* ``emit-event --type bogus`` → fails loud (non-zero, names valid types).
* ``resolve-card --repo R`` → prints the ids of cards with repo=R; empty
  output when none; honors ``--kind`` / ``--status`` filters.
"""

from __future__ import annotations

import json

from click.testing import CliRunner

from scitex_todo._cli import main
from scitex_todo._inbox import poll_inbox
from scitex_todo._store import add_task
from scitex_todo._users import register_user


def _store_path(tmp_path) -> str:
    """Path string to a fresh tasks.yaml under tmp_path."""
    return str(tmp_path / "tasks.yaml")


def _summary(output: str) -> dict:
    """Parse the emit-event JSON summary from CLI ``output``.

    The verb prints the JSON summary as its FINAL line. When the optional
    push rail runs under ``SCITEX_TODO_PUSH_DRY_RUN=1`` it also writes a
    dev/test banner to stdout BEFORE the JSON (a production producer never
    sets dry-run, so the JSON is the only stdout there). Parse the last
    non-empty line so the test is robust to that dev banner.
    """
    lines = [ln for ln in output.splitlines() if ln.strip()]
    return json.loads(lines[-1])


# --------------------------------------------------------------------------- #
# emit-event — repo-level (no card_id) → quiet no-op                          #
# --------------------------------------------------------------------------- #
def test_emit_event_pulled_no_card_exits_zero(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    # Act — a repo-level `pulled` with --repo and NO --card-id.
    result = runner.invoke(
        main,
        ["emit-event", "--type", "pulled", "--repo", "owner/repo", "--tasks", store],
    )
    # Assert
    assert result.exit_code == 0, result.output


def test_emit_event_pulled_no_card_is_quiet_noop(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    # Act
    result = runner.invoke(
        main,
        ["emit-event", "--type", "pulled", "--repo", "owner/repo", "--tasks", store],
    )
    summary = _summary(result.output)
    # Assert — the C4 consumer ran but had no card to resolve recipients
    # against, so nothing was enqueued / delivered (the intended quiet no-op).
    assert summary["kind"] == "card-event"
    assert summary["notify"]["card_id"] is None
    assert summary["notify"]["enqueued"] == []
    assert summary["notify"]["delivered"] == []
    assert "no-card-id" in summary["notify"]["skipped"]


# --------------------------------------------------------------------------- #
# emit-event — end-to-end: emit → bus → C4 → standalone inbox                  #
# --------------------------------------------------------------------------- #
def test_emit_event_released_reaches_subscriber_inbox(tmp_path, env):
    # Arrange — a registered SUBSCRIBER on a card. The C3 default rule for
    # `released` is `[subscribers]` (a release announcement), so the
    # subscriber `eve` is the recipient; the owner `alice` is NOT notified
    # for a release by default. Push rail dry-run so the optional
    # accelerator never touches the network (the standalone inbox rail is
    # the one under test).
    runner = CliRunner()
    store = _store_path(tmp_path)
    alice = register_user(kind="agent", names=["alice"], store=store)
    eve = register_user(kind="human", names=["eve"], store=store)
    # created_by == owner so the setup `created` event self-excludes the actor
    # (creator isn't notified of their own creation) — keeps alice's inbox empty
    # so the `released`-to-subscriber assertion is not polluted.
    add_task(store=store, id="card-rel", title="x", agent="alice", subscribers=["eve"], created_by="alice")
    env.set("SCITEX_TODO_PUSH_DRY_RUN", "1")
    # Act — a `released` card-event for the card, caused by `ci` (the actor
    # is never self-notified; here the actor is neither alice nor eve).
    result = runner.invoke(
        main,
        [
            "emit-event",
            "--type", "released",
            "--card-id", "card-rel",
            "--repo", "owner/repo",
            "--version", "v1.2.3",
            "--actor", "ci",
            "--tasks", store,
        ],
    )
    # Assert — exit 0 and the printed summary reports the enqueue to the
    # subscriber only.
    assert result.exit_code == 0, result.output
    summary = _summary(result.output)
    assert summary["notify"]["event_type"] == "released"
    assert summary["notify"]["enqueued"] == [eve.id]
    # Assert end-to-end — the subscriber's standalone INBOX really received
    # it; the owner (not a `released` recipient) stays empty.
    eve_inbox = poll_inbox(eve.id, store=store)
    assert [r["event_type"] for r in eve_inbox] == ["released"]
    assert [r["card_id"] for r in eve_inbox] == ["card-rel"]
    assert poll_inbox(alice.id, store=store) == []


def test_emit_event_released_actor_is_not_notified(tmp_path, env):
    # Arrange — alice is a SUBSCRIBER (so she WOULD be a `released` recipient)
    # AND the actor. The actor (the cause of the event) must never be
    # self-notified, so her inbox must stay empty even though her role matches.
    runner = CliRunner()
    store = _store_path(tmp_path)
    alice = register_user(kind="agent", names=["alice"], store=store)
    add_task(store=store, id="card-rel", title="x", agent="bob", subscribers=["alice"])
    env.set("SCITEX_TODO_PUSH_DRY_RUN", "1")
    # Act — alice causes the release.
    result = runner.invoke(
        main,
        [
            "emit-event",
            "--type", "released",
            "--card-id", "card-rel",
            "--actor", "alice",
            "--tasks", store,
        ],
    )
    # Assert — alice caused it, so her inbox stays empty (actor dropped).
    assert result.exit_code == 0, result.output
    summary = _summary(result.output)
    assert summary["notify"]["enqueued"] == []
    assert poll_inbox(alice.id, store=store) == []


def test_emit_event_extra_payload_parsed(tmp_path, env):
    # Arrange — --extra KEY=VALUE flows into the event envelope; with no
    # recipient it stays a quiet no-op but must still exit 0.
    runner = CliRunner()
    store = _store_path(tmp_path)
    # Act
    result = runner.invoke(
        main,
        [
            "emit-event",
            "--type", "deployed",
            "--repo", "owner/repo",
            "--extra", "service=api",
            "--extra", "region=us-east-1",
            "--tasks", store,
        ],
    )
    # Assert
    assert result.exit_code == 0, result.output


def test_emit_event_malformed_extra_nonzero(tmp_path, env):
    # Arrange — --extra missing '=' fails loud (no silent drop).
    runner = CliRunner()
    store = _store_path(tmp_path)
    # Act
    result = runner.invoke(
        main,
        ["emit-event", "--type", "pulled", "--repo", "r", "--extra", "noequals",
         "--tasks", store],
    )
    # Assert
    assert result.exit_code != 0
    assert "Traceback" not in result.output


# --------------------------------------------------------------------------- #
# emit-event — fail-loud on an unknown type                                   #
# --------------------------------------------------------------------------- #
def test_emit_event_bogus_type_fails_loud(tmp_path, env):
    # Arrange
    runner = CliRunner()
    store = _store_path(tmp_path)
    # Act
    result = runner.invoke(
        main, ["emit-event", "--type", "bogus", "--repo", "r", "--tasks", store]
    )
    # Assert — non-zero, no traceback, AND the error names the valid set.
    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "bogus" in result.output
    # A representative valid type is listed so the producer can self-correct.
    assert "released" in result.output


# --------------------------------------------------------------------------- #
# resolve-card — repo → card id(s)                                            #
# --------------------------------------------------------------------------- #
def test_resolve_card_prints_matching_ids(tmp_path, env):
    # Arrange — two cards on the same repo, one on another.
    runner = CliRunner()
    store = _store_path(tmp_path)
    add_task(store=store, id="c-a", title="A", assignee="agent:t", repo="owner/repo")
    add_task(store=store, id="c-b", title="B", assignee="agent:t", repo="owner/repo")
    add_task(store=store, id="c-c", title="C", assignee="agent:t", repo="owner/other")
    # Act
    result = runner.invoke(
        main, ["resolve-card", "--repo", "owner/repo", "--tasks", store]
    )
    # Assert — exactly the two matching ids, one per line.
    assert result.exit_code == 0, result.output
    ids = set(result.output.split())
    assert ids == {"c-a", "c-b"}


def test_resolve_card_empty_when_none_match(tmp_path, env):
    # Arrange — no card carries the queried repo.
    runner = CliRunner()
    store = _store_path(tmp_path)
    add_task(store=store, id="c-a", title="A", assignee="agent:t", repo="owner/other")
    # Act
    result = runner.invoke(
        main, ["resolve-card", "--repo", "owner/repo", "--tasks", store]
    )
    # Assert — exit 0, empty output.
    assert result.exit_code == 0, result.output
    assert result.output.strip() == ""


def test_resolve_card_status_filter(tmp_path, env):
    # Arrange — same repo, different statuses.
    runner = CliRunner()
    store = _store_path(tmp_path)
    add_task(
        store=store, id="c-pending", title="P", assignee="agent:t",
        repo="owner/repo", status="pending",
    )
    add_task(
        store=store, id="c-done", title="D", assignee="agent:t",
        repo="owner/repo", status="done",
    )
    # Act — only the pending card should match.
    result = runner.invoke(
        main,
        ["resolve-card", "--repo", "owner/repo", "--status", "pending", "--tasks", store],
    )
    # Assert
    assert result.exit_code == 0, result.output
    assert result.output.split() == ["c-pending"]


def test_resolve_card_then_emit_with_resolved_id(tmp_path, env):
    # Arrange — the documented producer flow: resolve-card to find the card,
    # then emit-event --card-id <that id>. End-to-end proof the two verbs
    # compose. `released` notifies subscribers (C3 default), so the recipient
    # alice is a SUBSCRIBER on the card.
    runner = CliRunner()
    store = _store_path(tmp_path)
    alice = register_user(kind="agent", names=["alice"], store=store)
    add_task(
        store=store, id="c-rel", title="x", agent="bob",
        subscribers=["alice"], repo="owner/repo",
    )
    env.set("SCITEX_TODO_PUSH_DRY_RUN", "1")
    # Act 1 — resolve repo → card id.
    resolve = runner.invoke(
        main, ["resolve-card", "--repo", "owner/repo", "--tasks", store]
    )
    card_id = resolve.output.strip()
    # Act 2 — emit a released event for that card, caused by `ci` (not alice).
    emit = runner.invoke(
        main,
        ["emit-event", "--type", "released", "--card-id", card_id,
         "--repo", "owner/repo", "--version", "v1", "--actor", "ci",
         "--tasks", store],
    )
    # Assert — subscriber alice received it via the inbox.
    assert resolve.exit_code == 0 and card_id == "c-rel"
    assert emit.exit_code == 0, emit.output
    assert [r["event_type"] for r in poll_inbox(alice.id, store=store)] == ["released"]


# EOF
