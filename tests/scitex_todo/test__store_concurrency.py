#!/usr/bin/env python3
"""Lost-update protection for raw read-modify-write cycles.

Pins the fix for incident-bulk-migration-lost-write-race-20260710: a bulk
migration used plain ``load_tasks → mutate → save_tasks`` and silently erased
two concurrent writers' mutations, because nothing tied the write to the read
it was based on. Two primitives close it:

* ``save_tasks(..., expected_generation=...)`` — optimistic concurrency: a
  write based on a stale read is REFUSED (StaleStoreError), never applied.
* ``edit_tasks(path)`` — one lock across the whole cycle, so there is no
  window at all.
"""

from __future__ import annotations

import pytest

from scitex_todo._model import (
    StaleStoreError,
    edit_tasks,
    load_tasks,
    save_tasks,
    store_generation,
)


def _seed(tmp_path, n=2):
    p = tmp_path / "tasks.yaml"
    rows = "\n".join(
        f"  - {{id: t{i}, title: T{i}, status: deferred}}" for i in range(n)
    )
    p.write_text(f"tasks:\n{rows}\n")
    return p


class TestOptimisticConcurrency:
    def test_stale_write_is_refused(self, tmp_path):
        # Arrange — writer A reads, then writer B commits.
        store = _seed(tmp_path)
        gen_a = store_generation(store)
        tasks_a = load_tasks(store)
        tasks_b = load_tasks(store)
        tasks_b.append({"id": "from-b", "title": "B's row", "status": "deferred"})
        save_tasks(tasks_b, store)

        # Act / Assert — A's write is based on a world that no longer exists.
        tasks_a[0]["priority"] = 1
        with pytest.raises(StaleStoreError):
            save_tasks(tasks_a, store, expected_generation=gen_a)

        # And B's row survived — the refusal is what protected it.
        assert any(t["id"] == "from-b" for t in load_tasks(store))

    def test_fresh_write_passes(self, tmp_path):
        # Arrange
        store = _seed(tmp_path)
        gen = store_generation(store)
        tasks = load_tasks(store)
        tasks[0]["priority"] = 1
        # Act — nothing intervened, so the token still matches.
        save_tasks(tasks, store, expected_generation=gen)
        # Assert
        assert load_tasks(store)[0]["priority"] == 1

    def test_without_token_behaviour_is_unchanged(self, tmp_path):
        # Arrange — the token is opt-in; existing callers keep working.
        store = _seed(tmp_path)
        tasks = load_tasks(store)
        tasks[0]["priority"] = 3
        # Act
        save_tasks(tasks, store)
        # Assert
        assert load_tasks(store)[0]["priority"] == 3

    def test_generation_of_missing_store(self, tmp_path):
        # Arrange / Act / Assert — stable sentinel, no raise.
        assert store_generation(tmp_path / "nope.yaml") == "absent"


class TestEditTasks:
    def test_mutation_persists_on_clean_exit(self, tmp_path):
        # Arrange
        store = _seed(tmp_path)
        # Act
        with edit_tasks(store) as tasks:
            for t in tasks:
                t["priority"] = 2
        # Assert
        assert all(t["priority"] == 2 for t in load_tasks(store))

    def test_nothing_written_on_exception(self, tmp_path):
        # Arrange
        store = _seed(tmp_path)
        before = store_generation(store)
        # Act
        with pytest.raises(RuntimeError, match="boom"):
            with edit_tasks(store) as tasks:
                tasks[0]["priority"] = 9
                raise RuntimeError("boom")
        # Assert — the half-done mutation never reached disk.
        assert store_generation(store) == before

    def test_preserves_non_tasks_sections(self, tmp_path):
        # Arrange — the users: registry must survive a tasks-only edit.
        store = tmp_path / "tasks.yaml"
        store.write_text(
            "users:\n  - {id: u1, name: someone}\ntasks:\n"
            "  - {id: t0, title: T, status: deferred}\n"
        )
        # Act
        with edit_tasks(store) as tasks:
            tasks[0]["priority"] = 1
        # Assert
        text = store.read_text()
        assert "u1" in text
        assert load_tasks(store)[0]["priority"] == 1

    def test_append_inside_cycle(self, tmp_path):
        # Arrange — the migration shape: read all, add rows, write back.
        store = _seed(tmp_path, n=1)
        # Act
        with edit_tasks(store) as tasks:
            tasks.append({"id": "new", "title": "added", "status": "deferred"})
        # Assert
        assert {t["id"] for t in load_tasks(store)} == {"t0", "new"}
