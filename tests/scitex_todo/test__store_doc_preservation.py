#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Guard the non-``tasks`` section-preservation property of the write path.

Perf fix (2026-07): the CRUD read-modify-write verbs used to parse the store
TWICE per write — once via ``load_tasks`` for the payload they mutate, then a
second ``safe_load`` re-read inside ``_save_tasks_unlocked`` whose ONLY job was
to recover the non-``tasks`` top-level sections (notably the ``users:``
registry) so they survived the rewrite. The fix loads the FULL doc ONCE under
the lock (``load_doc``) and threads it through to ``_save_doc_unlocked`` — so
the second parse is gone but the preservation property MUST be identical.

These tests pin that property: a ``users:`` (and any other) top-level section
present on disk at CRUD-call time survives every mutation verb, and the doc
still round-trips (the mutated task is present after reload).

Real fixtures, no mocks (STX-NM / PA-306).
"""

from __future__ import annotations

from scitex_todo import _model, _store
from scitex_todo._yaml import safe_dump


def _seed_store_with_users(path, tasks, users):
    """Write a store file that has BOTH a ``tasks:`` list and a ``users:``
    registry, the way the real shared store looks. Fixture-only direct write."""
    path.parent.mkdir(parents=True, exist_ok=True)
    norm = [{"status": "pending", **t} for t in tasks]  # status is required
    with path.open("w", encoding="utf-8") as handle:
        safe_dump({"tasks": norm, "users": users, "meta": {"seed": "v1"}}, handle)


class TestSectionPreservationAcrossCRUD:
    """The ``users:`` / ``meta:`` sections survive each write verb."""

    def test_users_survives_add_task(self, tmp_path):
        # Arrange — a store with a pre-existing users registry.
        store = tmp_path / "tasks.yaml"
        _seed_store_with_users(
            store,
            tasks=[{"id": "a", "title": "A"}],
            users={"alice": {"role": "dev"}, "bob": {"role": "qa"}},
        )
        # Act
        _store.add_task(store, id="b", title="B", assignee="agent:test")
        # Assert — users unchanged, both tasks present.
        doc = _model.load_doc(store)
        assert doc["users"] == {"alice": {"role": "dev"}, "bob": {"role": "qa"}}
        assert doc["meta"] == {"seed": "v1"}
        assert {t["id"] for t in doc["tasks"]} == {"a", "b"}

    def test_users_survives_update_task(self, tmp_path):
        # Arrange
        store = tmp_path / "tasks.yaml"
        _seed_store_with_users(
            store,
            tasks=[{"id": "a", "title": "A", "status": "pending"}],
            users={"alice": {"role": "dev"}},
        )
        # Act
        _store.update_task(store, task_id="a", status="in_progress")
        # Assert
        doc = _model.load_doc(store)
        assert doc["users"] == {"alice": {"role": "dev"}}
        task = next(t for t in doc["tasks"] if t["id"] == "a")
        assert task["status"] == "in_progress"

    def test_users_survives_delete_task(self, tmp_path):
        # Arrange
        store = tmp_path / "tasks.yaml"
        _seed_store_with_users(
            store,
            tasks=[{"id": "a", "title": "A"}, {"id": "b", "title": "B"}],
            users={"carol": {"role": "lead"}},
        )
        # Act
        _store.delete_task(store, task_id="a")
        # Assert
        doc = _model.load_doc(store)
        assert doc["users"] == {"carol": {"role": "lead"}}
        assert {t["id"] for t in doc["tasks"]} == {"b"}

    def test_users_survives_comment_task(self, tmp_path):
        # Arrange
        store = tmp_path / "tasks.yaml"
        _seed_store_with_users(
            store,
            tasks=[{"id": "a", "title": "A"}],
            users={"dave": {"role": "dev"}},
        )
        # Act
        _store.comment_task(store, task_id="a", text="hello", by="dave")
        # Assert
        doc = _model.load_doc(store)
        assert doc["users"] == {"dave": {"role": "dev"}}
        task = next(t for t in doc["tasks"] if t["id"] == "a")
        assert any(c.get("text") == "hello" for c in task["comments"])


class TestRoundTrip:
    """The mutated payload is present after reload (write actually lands)."""

    def test_added_task_present_after_reload(self, tmp_path):
        store = tmp_path / "tasks.yaml"
        _store.add_task(store, id="x", title="X", assignee="agent:test")
        tasks = _model.load_tasks(store)
        assert any(t["id"] == "x" for t in tasks)


class TestDocPrimitives:
    """Direct unit coverage of the new ``load_doc`` / ``_save_doc_unlocked``."""

    def test_load_doc_returns_full_mapping(self, tmp_path):
        store = tmp_path / "tasks.yaml"
        _seed_store_with_users(
            store, tasks=[{"id": "a", "title": "A"}], users={"u": 1}
        )
        doc = _model.load_doc(store)
        assert doc.get("users") == {"u": 1}
        assert doc.get("meta") == {"seed": "v1"}
        assert [t["id"] for t in doc["tasks"]] == ["a"]

    def test_save_doc_unlocked_preserves_extra_sections(self, tmp_path):
        # Arrange — an already-parsed doc with extra sections; mutate tasks.
        store = tmp_path / "tasks.yaml"
        _seed_store_with_users(
            store, tasks=[{"id": "a", "title": "A"}], users={"u": 1}
        )
        with _model._store_lock(store):
            doc = _model.load_doc(store)
            new_tasks = list(doc["tasks"]) + [
                {"id": "b", "title": "B", "status": "pending"}
            ]
            _model._save_doc_unlocked(doc, store, tasks=new_tasks)
        # Assert
        reloaded = _model.load_doc(store)
        assert reloaded["users"] == {"u": 1}
        assert reloaded["meta"] == {"seed": "v1"}
        assert {t["id"] for t in reloaded["tasks"]} == {"a", "b"}

    def test_save_tasks_unlocked_wrapper_still_preserves_sections(self, tmp_path):
        # The thin back-compat wrapper (only holds `tasks`, not the doc) must
        # STILL recover the on-disk `users:` section via its single re-read.
        store = tmp_path / "tasks.yaml"
        _seed_store_with_users(
            store, tasks=[{"id": "a", "title": "A"}], users={"u": 2}
        )
        with _model._store_lock(store):
            _model._save_tasks_unlocked(
                [
                    {"id": "a", "title": "A", "status": "pending"},
                    {"id": "c", "title": "C", "status": "pending"},
                ],
                store,
            )
        reloaded = _model.load_doc(store)
        assert reloaded["users"] == {"u": 2}
        assert reloaded["meta"] == {"seed": "v1"}
        assert {t["id"] for t in reloaded["tasks"]} == {"a", "c"}

    def test_on_disk_users_at_calltime_survives_locked_crud(self, tmp_path):
        # Read-under-lock semantics: the `users:` present on disk WHEN the
        # locked CRUD call begins is the version that survives — the write
        # verb reads the full doc under the lock and rewrites it whole, so a
        # concurrent writer's users-block that was already committed to disk
        # at call-time is not clobbered by the tasks-only mutation.
        store = tmp_path / "tasks.yaml"
        _seed_store_with_users(
            store, tasks=[{"id": "a", "title": "A"}], users={"e": {"x": 1}}
        )
        # Mutate a DIFFERENT axis (tasks) via a CRUD verb.
        _store.update_task(store, task_id="a", priority=1)
        # The users block on disk at call-time is intact post-write.
        doc = _model.load_doc(store)
        assert doc["users"] == {"e": {"x": 1}}
