#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the operator↔agent DM thread store (:mod:`scitex_cards._threads`).

Covers the scitex-dev DM convention v1 contract:

  - append → get_thread round-trip with the canonical record shape
    ``{id, thread, from, to, body, ts, read}``.
  - sorted thread key: ``thread_key(a, b) == thread_key(b, a)``.
  - STORE ISOLATION: threads live in the ``threads.yaml`` SIDECAR next to
    the tasks store, never inside ``tasks.yaml``, with their own lockfile.
  - crash-safe write: the sidecar reparses cleanly after writes.
  - mark_read (all-for-reader and id-scoped) + list_threads unread counts.
  - dm-dispatch: append_message enqueues an ``event_type="dm"`` record into
    the recipient's EXISTING pull-inbox in the real tasks.yaml store.

Real tmp_path stores throughout; no mocks (STX-NM / PA-306). AAA pattern.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from scitex_cards import _threads
from scitex_cards._inbox import poll_inbox
from scitex_cards._threads import (
    append_message,
    get_thread,
    list_threads,
    mark_read,
    thread_key,
    threads_path,
)
from scitex_cards._yaml import safe_load


@pytest.fixture(autouse=True)
def _clear_read_cache():
    """Keep the module-level read cache from leaking across tests.

    Entries are keyed by store path and each test gets its own tmp_path, so
    collisions are unlikely — but the cache is process-global state and a
    hermetic suite must not depend on that luck.
    """
    _threads._READ_CACHE.clear()
    yield
    _threads._READ_CACHE.clear()


@pytest.fixture()
def store(tmp_path: Path, env) -> Path:
    """A real tmp tasks.yaml store (threads sidecar lands next to it).

    Git autocommit is disabled so the inbox write path stays fast and
    deterministic under test.
    """
    env.set("SCITEX_TODO_STORE_GIT_AUTOCOMMIT", "0")
    path = tmp_path / "tasks.yaml"
    path.write_text("tasks: []\n", encoding="utf-8")
    return path


# === thread key ============================================================


def test_thread_key_sorts_peers_lexicographically():
    # Arrange / Act
    key = thread_key("zeta", "alpha")
    # Assert
    assert key == "dm:alpha::zeta"


def test_thread_key_is_direction_agnostic():
    # Arrange / Act / Assert
    assert thread_key("operator", "agent-x") == thread_key("agent-x", "operator")


def test_peers_of_inverts_thread_key():
    # Arrange
    key = thread_key("operator", "agent-x")
    # Act
    peers = _threads.peers_of(key)
    # Assert
    assert peers == ("agent-x", "operator")


# === append / get round-trip ===============================================


def test_append_then_get_thread_round_trip(store):
    # Arrange / Act
    sent = append_message("operator", "agent-x", "hello there", store=store)
    got = get_thread("agent-x", "operator", store=store)
    # Assert — canonical record shape, both directions resolve the thread.
    assert got == [sent]
    assert set(sent) == {"id", "thread", "from", "to", "body", "ts", "read"}
    assert sent["from"] == "operator"
    assert sent["to"] == "agent-x"
    assert sent["body"] == "hello there"
    assert sent["thread"] == "dm:agent-x::operator"
    assert sent["read"] is False
    assert sent["id"].startswith("m_")
    assert sent["ts"].endswith("Z")


def test_append_preserves_chronological_order(store):
    # Arrange
    append_message("operator", "agent-x", "first", store=store)
    append_message("agent-x", "operator", "second", store=store)
    # Act
    msgs = get_thread("operator", "agent-x", store=store)
    # Assert
    assert [m["body"] for m in msgs] == ["first", "second"]


def test_append_rejects_empty_body(store):
    # Arrange / Act / Assert
    with pytest.raises(ValueError):
        append_message("operator", "agent-x", "   ", store=store)


def test_get_thread_on_missing_sidecar_returns_empty(store):
    # Arrange — no message ever appended, sidecar absent.
    assert not threads_path(store).exists()
    # Act / Assert — never raises.
    assert get_thread("operator", "ghost", store=store) == []
    assert list_threads(store=store) == {}


# === store isolation (sidecar, own lock) ===================================


def test_threads_live_in_sidecar_not_tasks_yaml(store):
    # Arrange / Act
    append_message("operator", "agent-x", "hi", store=store)
    # Assert — sidecar exists next to tasks.yaml and holds the thread...
    sidecar = threads_path(store)
    assert sidecar == store.parent / "threads.yaml"
    assert sidecar.exists()
    doc = safe_load(sidecar.read_text(encoding="utf-8"))
    assert "dm:agent-x::operator" in doc["threads"]
    # ...while tasks.yaml carries NO threads section (isolation).
    tasks_doc = safe_load(store.read_text(encoding="utf-8"))
    assert "threads" not in tasks_doc


def test_threads_use_their_own_lockfile(store):
    # Arrange / Act
    append_message("operator", "agent-x", "hi", store=store)
    # Assert — a separate lock sentinel, not the tasks.yaml one.
    assert (store.parent / ".threads.yaml.lock").exists()


# === crash-safe write ======================================================


def test_sidecar_reparses_cleanly_after_writes(store):
    # Arrange — several writes across two threads.
    for i in range(3):
        append_message("operator", "agent-x", f"msg {i}", store=store)
    append_message("agent-y", "operator", "other thread", store=store)
    # Act — reparse the raw bytes exactly like the verify step does.
    doc = safe_load(threads_path(store).read_text(encoding="utf-8"))
    # Assert — full structure survives, no tmp sidecar left behind.
    assert len(doc["threads"]) == 2
    assert len(doc["threads"]["dm:agent-x::operator"]) == 3
    assert not (store.parent / ".threads.yaml.tmp").exists()


# === mark_read / list_threads ==============================================


def test_mark_read_all_for_reader(store):
    # Arrange — two inbound to operator, one outbound.
    append_message("agent-x", "operator", "one", store=store)
    append_message("agent-x", "operator", "two", store=store)
    append_message("operator", "agent-x", "reply", store=store)
    key = thread_key("operator", "agent-x")
    # Act
    flipped = mark_read(key, "operator", store=store)
    # Assert — only the reader's inbound messages flip.
    assert flipped == 2
    msgs = get_thread("operator", "agent-x", store=store)
    assert [m["read"] for m in msgs] == [True, True, False]
    # Idempotent.
    assert mark_read(key, "operator", store=store) == 0


def test_mark_read_scoped_to_ids(store):
    # Arrange
    first = append_message("agent-x", "operator", "one", store=store)
    append_message("agent-x", "operator", "two", store=store)
    key = first["thread"]
    # Act
    flipped = mark_read(key, "operator", ids=[first["id"]], store=store)
    # Assert
    assert flipped == 1
    msgs = get_thread("operator", "agent-x", store=store)
    assert [m["read"] for m in msgs] == [True, False]


def test_list_threads_summarizes_unread_and_last(store):
    # Arrange
    append_message("agent-x", "operator", "ping", store=store)
    last = append_message("agent-x", "operator", "ping again", store=store)
    # Act
    summary = list_threads(store=store)
    # Assert
    row = summary["dm:agent-x::operator"]
    assert row["peers"] == ("agent-x", "operator")
    assert row["count"] == 2
    assert row["unread"] == {"agent-x": 0, "operator": 2}
    assert row["last"]["id"] == last["id"]


# === dm-dispatch → recipient inbox =========================================


def test_append_message_enqueues_dm_into_recipient_inbox(store):
    # Arrange / Act
    sent = append_message("operator", "agent-x", "please check PR", store=store)
    # Assert — the recipient's pull-inbox in the REAL tasks store carries the
    # dm record (raw-name key: agent-x is not in the users registry).
    inbox = poll_inbox("agent-x", store=store)
    assert len(inbox) == 1
    rec = inbox[0]
    assert rec["event_type"] == "dm"
    assert rec["card_id"] == sent["thread"]
    assert rec["body"] == "please check PR"
    assert rec["actor"] == "operator"
    assert rec["ts"] == sent["ts"]


def test_agent_reply_enqueues_into_operator_inbox_for_symmetry(store):
    # Arrange / Act — documented decision: the operator inbox is enqueued
    # too (cheap + keeps a future operator-side drain surface working).
    append_message("agent-x", "operator", "done, PR is green", store=store)
    # Assert
    inbox = poll_inbox("operator", store=store)
    assert len(inbox) == 1
    assert inbox[0]["event_type"] == "dm"
    assert inbox[0]["actor"] == "agent-x"


def test_dispatch_resolves_registered_recipient_to_user_id(store):
    # Arrange — register the recipient so dispatch must key by u_* id
    # (exactly how poll_notifications resolves the drain key).
    from scitex_cards._users import register_user

    user = register_user(kind="agent", names=["agent-reg"], store=store)
    # Act
    append_message("operator", "agent-reg", "hello registered", store=store)
    # Assert — enqueued under the stable id, not the raw name.
    assert len(poll_inbox(user.id, store=store)) == 1
    assert poll_inbox("agent-reg", store=store) == []


# === read cache ============================================================
#
# The GUI polls a thread every ~5s, and mark_read sits on that poll path, so
# the READ paths are served from an mtime-guarded cache of the parsed content.
# The rule the module lives or dies by: WRITERS NEVER READ THAT CACHE. These
# tests are what refuse the regression — not this file's comments.


def _poison_read_cache(path: Path, content: dict) -> None:
    """Install ``content`` as the cached parse of ``path``, stamped with the
    file's CURRENT ``(mtime_ns, size)`` so the guard considers it fresh.

    This reproduces — deterministically, without racing the clock — the exact
    stale-read state a write landing inside one mtime tick would produce on a
    coarse-granularity filesystem.
    """
    stat = path.stat()
    _threads._READ_CACHE[str(path)] = (stat.st_mtime_ns, stat.st_size, content)


def test_append_message_never_reads_the_cache(store: Path) -> None:
    """A writer served from a stale cache would serialize the stale mapping
    and DROP messages. append_message must re-read the file under the lock.
    Read back through the UNCACHED primitive so the assertion itself cannot
    be fooled by the cache."""
    # Arrange — one real message on disk, and a cache claiming the store is
    # empty. A cache-reading writer would believe it.
    append_message("agent-a", "operator", "first", store=store)
    path = threads_path(store)
    _poison_read_cache(path, {})
    # Act
    append_message("agent-a", "operator", "second", store=store)
    # Assert
    on_disk = _threads._load_threads(path)[thread_key("agent-a", "operator")]
    assert [r["body"] for r in on_disk] == ["first", "second"]


def test_mark_read_flip_reads_the_file_not_the_cache(store: Path) -> None:
    """The fast NO may consult the cache; the authoritative flip may not.
    Here the cache sees only ONE of two unread messages — the flip must still
    find both on disk."""
    # Arrange
    append_message("agent-a", "operator", "first", store=store)
    append_message("agent-a", "operator", "second", store=store)
    path = threads_path(store)
    key = thread_key("agent-a", "operator")
    full = _threads._load_threads(path)
    _poison_read_cache(path, {key: [dict(full[key][0])]})
    # Act — the pre-check passes (the cache holds an unread one), so the
    # authoritative half runs against the real file.
    flipped = mark_read(key, "operator", store=store)
    # Assert
    assert flipped == 2


def test_mark_read_does_not_truncate_the_store_from_a_stale_cache(
    store: Path,
) -> None:
    """The save after a flip must not write back the cache's truncated view."""
    # Arrange
    append_message("agent-a", "operator", "first", store=store)
    append_message("agent-a", "operator", "second", store=store)
    path = threads_path(store)
    key = thread_key("agent-a", "operator")
    full = _threads._load_threads(path)
    _poison_read_cache(path, {key: [dict(full[key][0])]})
    # Act
    mark_read(key, "operator", store=store)
    # Assert
    assert len(_threads._load_threads(path)[key]) == 2


def test_fast_no_is_derived_per_reader_not_memoized(store: Path) -> None:
    """The cache stores parsed CONTENT; "has unread" is re-derived for each
    reader per call. One peer's "nothing unread" must never answer for the
    other's — both readers here are evaluated against the SAME cache entry."""
    # Arrange — one message each way, then clear only the operator's side.
    append_message("agent-a", "operator", "to-operator", store=store)
    append_message("operator", "agent-a", "to-agent", store=store)
    key = thread_key("agent-a", "operator")
    mark_read(key, "operator", store=store)
    get_thread("agent-a", "operator", store=store)  # warm the cache
    # Act — operator now has nothing (fast NO); agent-a still has one.
    operator_flipped = mark_read(key, "operator", store=store)
    agent_flipped = mark_read(key, "agent-a", store=store)
    # Assert — a per-path memoized boolean would hand the operator's 0 to
    # agent-a and leave its message unread forever.
    assert (operator_flipped, agent_flipped) == (0, 1)


def test_stale_cache_defers_the_flip_by_one_poll(store: Path) -> None:
    """The ACCEPTED cost of the lock-free fast NO, pinned as chosen behavior:
    a stale cache makes this poll a no-op. It is not a bug to be "fixed" by
    putting the writer on the cache — that is the one failure mode."""
    # Arrange — genuinely unread on disk, but the cache says all read.
    append_message("agent-a", "operator", "hello", store=store)
    path = threads_path(store)
    key = thread_key("agent-a", "operator")
    stale = [dict(r, read=True) for r in _threads._load_threads(path)[key]]
    _poison_read_cache(path, {key: stale})
    # Act
    flipped = mark_read(key, "operator", store=store)
    # Assert
    assert flipped == 0


def test_stale_cache_self_heals_on_the_next_poll(store: Path) -> None:
    """...and the deferral is bounded at one poll: the next stamp roll
    re-parses and the message is marked. This is what makes the accepted cost
    a delay rather than a lost flip."""
    # Arrange — take the false-negative poll above, then roll the stamp the
    # way any subsequent write would.
    append_message("agent-a", "operator", "hello", store=store)
    path = threads_path(store)
    key = thread_key("agent-a", "operator")
    stale = [dict(r, read=True) for r in _threads._load_threads(path)[key]]
    _poison_read_cache(path, {key: stale})
    mark_read(key, "operator", store=store)  # the deferred poll
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
    # Act
    flipped = mark_read(key, "operator", store=store)
    # Assert
    assert flipped == 1


def test_get_thread_is_served_from_the_read_cache(store: Path) -> None:
    """Pins that the read path actually consults the cache — otherwise the
    5s poll is still paying a full parse and this whole change is inert."""
    # Arrange
    append_message("agent-a", "operator", "on-disk", store=store)
    path = threads_path(store)
    key = thread_key("agent-a", "operator")
    cached = [dict(r, body="from-cache") for r in _threads._load_threads(path)[key]]
    _poison_read_cache(path, {key: cached})
    # Act
    bodies = [m["body"] for m in get_thread("agent-a", "operator", store=store)]
    # Assert
    assert bodies == ["from-cache"]


def test_list_threads_is_served_from_the_read_cache(store: Path) -> None:
    """Same for the agent-list poll (~10s)."""
    # Arrange
    append_message("agent-a", "operator", "on-disk", store=store)
    path = threads_path(store)
    key = thread_key("agent-a", "operator")
    cached = [dict(r, body="from-cache") for r in _threads._load_threads(path)[key]]
    _poison_read_cache(path, {key: cached})
    # Act
    summary = list_threads(store=store)[key]
    # Assert
    assert summary["last"]["body"] == "from-cache"


def test_a_write_invalidates_the_cached_read(store: Path) -> None:
    """The property that makes the cache safe to read from: any write rolls
    the mtime, so the next read re-parses. No reader sees a stale thread."""
    # Arrange — warm the cache on a one-message thread.
    append_message("agent-a", "operator", "first", store=store)
    get_thread("agent-a", "operator", store=store)
    # Act
    append_message("agent-a", "operator", "second", store=store)
    bodies = [m["body"] for m in get_thread("agent-a", "operator", store=store)]
    # Assert
    assert bodies == ["first", "second"]


def test_cached_read_of_an_absent_sidecar_is_empty(store: Path) -> None:
    """Absent file → {} and nothing cached (the uncached primitive's
    never-raise-on-absence contract must survive the cache)."""
    # Arrange
    path = threads_path(store)
    # Act
    threads = _threads._load_threads_cached(path)
    # Assert
    assert threads == {} and str(path) not in _threads._READ_CACHE


# EOF
