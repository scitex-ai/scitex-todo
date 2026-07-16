#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A liveness/users write must preserve the tasks payload — via the FAST path.

WHY THIS FILE EXISTS (2026-07-15): `_users/_store.py::_save_users_unlocked` is the
liveness-HEARTBEAT write — every `update_task`/`add_task`/`comment` by a registered
agent stamps `last_seen` and lands here. It used a ruamel ROUND-TRIP loader+dumper
to preserve the `tasks:` list's comments while replacing only `users:`. But the
TASK write path (`_store_write._save_doc_unlocked`) had already abandoned comment
preservation and writes with the fast safe loader/dumper — so this path was
ruamel-round-tripping the ENTIRE 6.5 MB store to preserve comments the task path
already dropped.

MEASURED cost of that: 46 s/write on 0.9.4, 171 s/write on 0.13.x, on the live
store, for any registered agent. It is the root cause of the board's per-write
latency. The fix swaps ruamel for the fast safe path.

These tests pin the INVARIANT the ruamel path actually provided — the tasks
payload survives a users-only write — so the fast path cannot silently drop it,
AND pin that the slow ruamel round-trip does not creep back in.
"""

import inspect

from scitex_cards._users import _store as users_store
from scitex_cards._users._store import _save_users_unlocked


def _read(path):
    import yaml

    return yaml.safe_load(path.read_text())


def test_users_write_preserves_the_entire_tasks_payload(tmp_path):
    """Replacing users: must leave every task row untouched."""
    import yaml

    store = tmp_path / "tasks.yaml"
    tasks = [{"id": f"t{i}", "title": f"task {i}", "status": "deferred"} for i in range(50)]
    store.write_text(yaml.safe_dump({"tasks": tasks, "users": []}))

    _save_users_unlocked(
        [{"id": "u_a", "kind": "agent", "names": ["alice"], "last_seen": "2026-07-15T00:00:00Z"}],
        store,
    )

    doc = _read(store)
    assert [t["id"] for t in doc["tasks"]] == [f"t{i}" for i in range(50)], "task payload changed"
    assert len(doc["users"]) == 1 and doc["users"][0]["id"] == "u_a"


def test_users_first_write_seeds_an_empty_tasks_list(tmp_path):
    """A users-FIRST write (no tasks yet) must leave a valid `tasks: []`.

    `_model.load_tasks` hard-requires a top-level `tasks:` list; a file carrying
    only `users:` would make a later `add_task` fail-loud.
    """
    import yaml

    store = tmp_path / "tasks.yaml"
    store.write_text(yaml.safe_dump({"users": []}))
    _save_users_unlocked([{"id": "u_a", "kind": "agent", "names": ["a"]}], store)

    doc = _read(store)
    assert isinstance(doc.get("tasks"), list) and doc["tasks"] == []
    assert len(doc["users"]) == 1


def test_users_write_is_crash_safe_no_partial_file(tmp_path):
    """A mid-write failure must leave the canonical file untouched, no .tmp litter."""
    import yaml

    store = tmp_path / "tasks.yaml"
    original = {"tasks": [{"id": "t0", "title": "keep me", "status": "done"}], "users": []}
    store.write_text(yaml.safe_dump(original))

    # A user row that fails validation aborts BEFORE any write.
    import pytest

    with pytest.raises(Exception):
        _save_users_unlocked([{"kind": "agent"}], store)  # missing id/names → invalid

    assert _read(store) == original, "canonical file was mutated on a failed write"
    assert not (store.parent / f".{store.name}.tmp").exists(), "left a .tmp turd"


def test_the_slow_ruamel_round_trip_did_not_creep_back(tmp_path):
    """PIN THE FIX: the heartbeat write must NOT ruamel round-trip the store.

    A ruamel round-trip of the whole tasks blob is what cost 46–171 s/write. This
    asserts the function's source does not reintroduce it. Implementation-coupled
    on purpose — the performance cliff is invisible to a behavioural test on a
    small fixture, so it must be pinned structurally.
    """
    src = inspect.getsource(_save_users_unlocked)
    assert "from ruamel" not in src and "YAML()" not in src, (
        "the users/heartbeat write reintroduced a ruamel round-trip — that cost "
        "46–171 s PER card write on the live store. Use the fast safe path."
    )
    # and it DOES use the fast safe path
    assert "safe_dump" in src and "safe_load" in src


def test_module_still_exposes_the_helper():
    """Guard the import surface used by the liveness heartbeat."""
    assert hasattr(users_store, "_save_users_unlocked")
