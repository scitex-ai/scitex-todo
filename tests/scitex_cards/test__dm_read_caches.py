#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read caches behind the GUI ``/dm/*`` endpoints (perf bridge, 2026-07-18).

Measured on the live host before these caches: ``/dm/threads`` = 10.7 s per
request (``list_users`` full-parsed the 8.8 MB store for ONE registered user;
``list_threads`` rescanned every record of 137 threads for unread counts even
with the parse cached). Both hot paths are now memoized on the backing file's
``(mtime_ns, size)`` — the ``services.get_board`` pattern already used by
``_threads._READ_CACHE`` — so a click re-parses only after a real write.

Pinned here, with real files and no mocks (counters wrap the real loaders):

* one parse per file state — a second read with an unchanged file hits cache;
* a WRITE (register / append / mark_read) rolls the key and is visible on the
  very next read — the cache can never mask a store mutation;
* returned structures do not alias the cache — caller mutation cannot poison
  later reads.
"""

from __future__ import annotations

import pytest

from scitex_cards import _threads
from scitex_cards._threads import append_message, list_threads, mark_read, thread_key
from scitex_cards._users import _store_read
from scitex_cards._users._store_read import list_users
from scitex_cards._users._store_write import register_user


@pytest.fixture()
def store(tmp_path):
    """A real, isolated store file + cleared module caches (they are global)."""
    path = tmp_path / "tasks.yaml"
    path.write_text("tasks: []\n", encoding="utf-8")
    _store_read._READ_CACHE.clear()
    _threads._READ_CACHE.clear()
    _threads._SUMMARY_CACHE.clear()
    yield str(path)
    _store_read._READ_CACHE.clear()
    _threads._READ_CACHE.clear()
    _threads._SUMMARY_CACHE.clear()


def _count_calls(monkeypatch, module, name):
    """Wrap ``module.name`` with a call counter (the real function still runs)."""
    real = getattr(module, name)
    counter = {"n": 0}

    def _wrapped(*args, **kwargs):
        counter["n"] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(module, name, _wrapped)
    return counter


# === users registry read cache =============================================


def test_list_users_parses_once_while_the_store_file_is_unchanged(store, monkeypatch):
    # Arrange: one registered user; count section parses on the READ path.
    register_user(kind="agent", names=["alice"], store=store)
    parses = _count_calls(monkeypatch, _store_read, "_load_users_section")

    # Act: two reads against the identical file state.
    first = list_users(store)
    second = list_users(store)

    # Assert: one parse served both reads, content identical.
    assert parses["n"] == 1
    assert [u.id for u in first] == [u.id for u in second]


def test_list_users_sees_a_registry_write_on_the_very_next_read(store, monkeypatch):
    # Arrange: warm the cache with one user.
    register_user(kind="agent", names=["alice"], store=store)
    assert [u.names[0] for u in list_users(store)] == ["alice"]
    parses = _count_calls(monkeypatch, _store_read, "_load_users_section")

    # Act: a write rolls the file's (mtime, size); then read again.
    register_user(kind="agent", names=["bob"], store=store)
    names = sorted(u.names[0] for u in list_users(store))

    # Assert: the new row is visible immediately, via exactly ONE re-parse —
    # the cache refill. (The write path's own uncached read is invisible to
    # this counter: _store_write binds _load_users_section at import time,
    # so patching _store_read's attribute counts only the cached read path.)
    assert names == ["alice", "bob"]
    assert parses["n"] == 1


def test_mutating_a_returned_user_does_not_poison_the_cache(store):
    # Arrange: one cached read.
    register_user(kind="agent", names=["alice"], store=store)
    victim = list_users(store)[0]

    # Act: mutate the returned object's nested list (from_dict aliases it).
    victim.names.append("mallory")

    # Assert: a fresh read from the SAME cache entry is pristine.
    assert list_users(store)[0].names == ["alice"]


# === thread summary cache ==================================================


def test_list_threads_scans_once_while_the_sidecar_is_unchanged(store, monkeypatch):
    # Arrange: a two-message thread, then count raw sidecar parses.
    append_message("operator", "agent-a", "hello", store=store)
    append_message("agent-a", "operator", "hi back", store=store)
    parses = _count_calls(monkeypatch, _threads, "_load_threads")

    # Act: two summary reads against the identical file state.
    first = list_threads(store=store)
    second = list_threads(store=store)

    # Assert: at most one parse (cache fill) served both; zero on the second.
    assert parses["n"] <= 1
    key = thread_key("operator", "agent-a")
    assert first[key]["count"] == second[key]["count"] == 2


def test_list_threads_reflects_a_new_message_on_the_very_next_read(store):
    # Arrange: warm the summary cache.
    append_message("operator", "agent-a", "hello", store=store)
    key = thread_key("operator", "agent-a")
    assert list_threads(store=store)[key]["count"] == 1

    # Act: append (a write) then re-read.
    append_message("operator", "agent-a", "again", store=store)
    summary = list_threads(store=store)[key]

    # Assert: the write invalidated the summary; unread counts the recipient.
    assert summary["count"] == 2
    assert summary["unread"]["agent-a"] == 2
    assert summary["last"]["body"] == "again"


def test_list_threads_reflects_mark_read_on_the_very_next_read(store):
    # Arrange: one unread message, summary cached showing unread=1.
    append_message("operator", "agent-a", "hello", store=store)
    key = thread_key("operator", "agent-a")
    assert list_threads(store=store)[key]["unread"]["agent-a"] == 1

    # Act: the recipient acks; then re-read the summary.
    flipped = mark_read(key, "agent-a", store=store)

    # Assert: the GUI badge source drops to zero immediately — the cache
    # cannot serve a stale unread count across the flip's write.
    assert flipped == 1
    assert list_threads(store=store)[key]["unread"]["agent-a"] == 0


def test_mutating_a_returned_summary_does_not_poison_the_cache(store):
    # Arrange: a cached summary.
    append_message("operator", "agent-a", "hello", store=store)
    key = thread_key("operator", "agent-a")
    victim = list_threads(store=store)[key]

    # Act: mutate everything mutable the caller received.
    victim["unread"]["agent-a"] = 999
    victim["last"]["body"] = "tampered"

    # Assert: a fresh read from the SAME cache entry is pristine.
    clean = list_threads(store=store)[key]
    assert clean["unread"]["agent-a"] == 1
    assert clean["last"]["body"] == "hello"


# EOF
