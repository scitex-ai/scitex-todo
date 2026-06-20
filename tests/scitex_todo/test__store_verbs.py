#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the under-covered ``_store`` CRUD verbs.

Coverage audit (proj-scitex-todo overnight 2026-06-13, lead a2a
`1397f103`) flagged `_store.py` at 54% — 146 of 319 statements
uncovered. The big test file `tests/scitex_todo/test__store.py`
exercises `add_task` / `update_task` / `complete_task` /
`list_tasks` / `summarize_tasks` end-to-end, but the seven other
public verbs the ``__all__`` exports had NO dedicated tests:

  - ``get_task`` — single-row lookup with not-found
  - ``delete_task`` — remove + scrub references (depends_on / blocks
    / parent); return lossless payload for restore
  - ``restore_task`` — Delete-Undo partner; idempotent on duplicate id
  - ``comment_task`` — append to ``comments[]`` (Issue-activity-log)
  - ``set_edge`` — add / remove a ``depends_on`` / ``blocks`` edge
    (incl. unknown-id + self-edge rejection)
  - ``resolve_task`` — flip ``blocked`` → ``done``, clear blocker,
    audit comment, idempotent on already-resolved
  - ``reopen_task`` — un-resolve ``done`` → ``blocked`` with
    ``blocker=operator-decision``, audit comment

Real fixtures (no mocks per STX-NM / PA-306); ``RequestFactory``
isn't needed — these are pure Python-API tests.
"""

from __future__ import annotations

import pytest

from scitex_todo import _store


# === get_task ===============================================================


class TestGetTask:
    """Single-row lookup; raises ``TaskNotFoundError`` on miss."""

    def test_returns_the_matching_dict(self, tmp_path):
        # Arrange
        store = tmp_path / "tasks.yaml"
        _store.add_task(store, id="a", title="A")
        # Act
        found = _store.get_task(store, task_id="a")
        # Assert
        assert found["id"] == "a"

    def test_unknown_id_raises_not_found(self, tmp_path):
        # Arrange
        store = tmp_path / "tasks.yaml"
        _store.add_task(store, id="a", title="A")
        # Act
        # Assert
        with pytest.raises(_store.TaskNotFoundError):
            _store.get_task(store, task_id="missing")

    def test_missing_task_id_arg_raises(self, tmp_path):
        # Arrange
        store = tmp_path / "tasks.yaml"
        _store.add_task(store, id="a", title="A")
        # Act
        # Assert
        with pytest.raises(ValueError):
            _store.get_task(store, task_id=None)


# === delete_task ============================================================


class TestDeleteTask:
    """Remove the row + scrub references; return the lossless payload."""

    def test_removes_the_target_row(self, tmp_path):
        # Arrange
        store = tmp_path / "tasks.yaml"
        _store.add_task(store, id="a", title="A")
        _store.add_task(store, id="b", title="B")
        # Act
        _store.delete_task(store, task_id="a")
        # Assert
        assert {r["id"] for r in _store.list_tasks(store, scope="")} == {"b"}

    def test_returns_the_removed_task(self, tmp_path):
        # Arrange
        store = tmp_path / "tasks.yaml"
        _store.add_task(store, id="a", title="A")
        # Act
        result = _store.delete_task(store, task_id="a")
        # Assert
        assert result["removed"]["id"] == "a"

    def test_scrubs_depends_on_reference(self, tmp_path):
        # Arrange — `b` depends on `a`; deleting `a` strips it from b.
        store = tmp_path / "tasks.yaml"
        _store.add_task(store, id="a", title="A")
        _store.add_task(store, id="b", title="B", depends_on=["a"])
        # Act
        _store.delete_task(store, task_id="a")
        # Assert
        b = _store.get_task(store, task_id="b")
        assert "depends_on" not in b

    def test_scrubs_blocks_reference(self, tmp_path):
        # Arrange — `a` blocks `b`; deleting `a` strips it from b.
        store = tmp_path / "tasks.yaml"
        _store.add_task(store, id="a", title="A")
        _store.add_task(store, id="b", title="B", blocks=["a"])
        # Act
        _store.delete_task(store, task_id="a")
        # Assert
        b = _store.get_task(store, task_id="b")
        assert "blocks" not in b

    def test_scrubs_parent_reference(self, tmp_path):
        # Arrange — `b.parent = a`; deleting `a` strips parent.
        store = tmp_path / "tasks.yaml"
        _store.add_task(store, id="a", title="A")
        _store.add_task(store, id="b", title="B", parent="a")
        # Act
        _store.delete_task(store, task_id="a")
        # Assert
        b = _store.get_task(store, task_id="b")
        assert "parent" not in b

    def test_returns_refs_for_undo(self, tmp_path):
        # Arrange
        store = tmp_path / "tasks.yaml"
        _store.add_task(store, id="a", title="A")
        _store.add_task(store, id="b", title="B", depends_on=["a"])
        # Act
        result = _store.delete_task(store, task_id="a")
        # Assert — `b` was the mutated ref; refs contains its id.
        assert "b" in result["refs"]

    def test_unknown_id_raises_not_found(self, tmp_path):
        # Arrange
        store = tmp_path / "tasks.yaml"
        _store.add_task(store, id="a", title="A")
        # Act
        # Assert
        with pytest.raises(_store.TaskNotFoundError):
            _store.delete_task(store, task_id="missing")


# === restore_task ===========================================================


class TestRestoreTask:
    """Delete-Undo partner — re-insert a previously-deleted dict."""

    def test_round_trips_a_delete(self, tmp_path):
        # Arrange — delete `a`, then restore from the returned payload.
        store = tmp_path / "tasks.yaml"
        _store.add_task(store, id="a", title="A")
        removed = _store.delete_task(store, task_id="a")["removed"]
        # Act
        _store.restore_task(store, task=removed, refs=[])
        # Assert
        assert _store.get_task(store, task_id="a")["id"] == "a"

    def test_duplicate_id_raises(self, tmp_path):
        # Arrange
        store = tmp_path / "tasks.yaml"
        _store.add_task(store, id="a", title="A")
        # branch.
        # Act
        # Assert
        with pytest.raises(ValueError):
            _store.restore_task(
                store,
                task={"id": "a", "title": "A", "status": "pending"},
            )

    def test_missing_task_arg_raises(self, tmp_path):
        # Arrange
        store = tmp_path / "tasks.yaml"
        # Act
        # Assert
        with pytest.raises(ValueError):
            _store.restore_task(store, task=None)

    def test_missing_id_in_task_arg_raises(self, tmp_path):
        # Arrange
        store = tmp_path / "tasks.yaml"
        # Act
        # Assert
        with pytest.raises(ValueError):
            _store.restore_task(store, task={"title": "noid"})


# === comment_task ===========================================================


class TestCommentTask:
    """Append a structured entry to ``comments[]``."""

    def test_appends_a_comment_entry(self, tmp_path):
        # Arrange
        store = tmp_path / "tasks.yaml"
        _store.add_task(store, id="a", title="A")
        # Act
        _store.comment_task(store, task_id="a", text="hello", by="me")
        # Assert
        comments = _store.get_task(store, task_id="a").get("comments") or []
        assert len(comments) == 1

    def test_comment_carries_text(self, tmp_path):
        # Arrange
        store = tmp_path / "tasks.yaml"
        _store.add_task(store, id="a", title="A")
        # Act
        _store.comment_task(store, task_id="a", text="hi", by="me")
        # Assert
        comments = _store.get_task(store, task_id="a")["comments"]
        assert comments[0]["text"] == "hi"

    def test_empty_text_raises(self, tmp_path):
        # Arrange
        store = tmp_path / "tasks.yaml"
        _store.add_task(store, id="a", title="A")
        # Act
        # Assert
        with pytest.raises(ValueError):
            _store.comment_task(store, task_id="a", text="", by="me")

    def test_unknown_id_raises_not_found(self, tmp_path):
        # Arrange
        store = tmp_path / "tasks.yaml"
        _store.add_task(store, id="a", title="A")
        # Act
        # Assert
        with pytest.raises(_store.TaskNotFoundError):
            _store.comment_task(store, task_id="missing", text="hi", by="me")


# === set_edge ===============================================================


class TestSetEdge:
    """Add / remove a ``depends_on`` or ``blocks`` edge between two ids."""

    def test_add_depends_on_inserts_edge(self, tmp_path):
        # Arrange
        store = tmp_path / "tasks.yaml"
        _store.add_task(store, id="a", title="A")
        _store.add_task(store, id="b", title="B")
        # Act
        _store.set_edge(
            store,
            action="add",
            kind="depends_on",
            source="b",
            target="a",
        )
        # Assert
        assert _store.get_task(store, task_id="b")["depends_on"] == ["a"]

    def test_remove_depends_on_strips_edge(self, tmp_path):
        # Arrange
        store = tmp_path / "tasks.yaml"
        _store.add_task(store, id="a", title="A")
        _store.add_task(store, id="b", title="B", depends_on=["a"])
        # Act
        _store.set_edge(
            store,
            action="remove",
            kind="depends_on",
            source="b",
            target="a",
        )
        # Assert — list becomes empty → key removed entirely.
        assert "depends_on" not in _store.get_task(store, task_id="b")

    def test_add_blocks_inserts_edge(self, tmp_path):
        # Arrange
        store = tmp_path / "tasks.yaml"
        _store.add_task(store, id="a", title="A")
        _store.add_task(store, id="b", title="B")
        # Act
        _store.set_edge(
            store,
            action="add",
            kind="blocks",
            source="a",
            target="b",
        )
        # Assert
        assert _store.get_task(store, task_id="a")["blocks"] == ["b"]

    def test_invalid_action_raises(self, tmp_path):
        # Arrange
        store = tmp_path / "tasks.yaml"
        # Act
        # Assert
        with pytest.raises(ValueError):
            _store.set_edge(
                store,
                action="upsert",
                kind="depends_on",
                source="a",
                target="b",
            )

    def test_invalid_kind_raises(self, tmp_path):
        # Arrange
        store = tmp_path / "tasks.yaml"
        # Act
        # Assert
        with pytest.raises(ValueError):
            _store.set_edge(
                store,
                action="add",
                kind="related_to",
                source="a",
                target="b",
            )

    def test_self_edge_raises(self, tmp_path):
        # Arrange
        store = tmp_path / "tasks.yaml"
        # Act
        # Assert
        with pytest.raises(ValueError):
            _store.set_edge(
                store,
                action="add",
                kind="depends_on",
                source="a",
                target="a",
            )

    def test_missing_endpoints_raises(self, tmp_path):
        # Arrange
        store = tmp_path / "tasks.yaml"
        # Act
        # Assert
        with pytest.raises(ValueError):
            _store.set_edge(
                store,
                action="add",
                kind="depends_on",
                source="",
                target="b",
            )

    def test_unknown_source_raises_not_found(self, tmp_path):
        # Arrange
        store = tmp_path / "tasks.yaml"
        _store.add_task(store, id="a", title="A")
        # Act
        # Assert
        with pytest.raises(_store.TaskNotFoundError):
            _store.set_edge(
                store,
                action="add",
                kind="depends_on",
                source="missing",
                target="a",
            )

    def test_unknown_target_raises_not_found(self, tmp_path):
        # Arrange
        store = tmp_path / "tasks.yaml"
        _store.add_task(store, id="a", title="A")
        # Act
        # Assert
        with pytest.raises(_store.TaskNotFoundError):
            _store.set_edge(
                store,
                action="add",
                kind="depends_on",
                source="a",
                target="missing",
            )


# === resolve_task ===========================================================


class TestResolveTask:
    """Flip ``blocked`` → ``done``, clear blocker, append audit comment."""

    def test_flips_status_to_done(self, tmp_path):
        # Arrange
        store = tmp_path / "tasks.yaml"
        _store.add_task(
            store,
            id="a",
            title="A",
            status="blocked",
            blocker="operator-decision",
        )
        # Act
        _store.resolve_task(store, task_id="a", actor="op")
        # Assert
        assert _store.get_task(store, task_id="a")["status"] == "done"

    def test_clears_blocker_field(self, tmp_path):
        # Arrange
        store = tmp_path / "tasks.yaml"
        _store.add_task(
            store,
            id="a",
            title="A",
            status="blocked",
            blocker="operator-decision",
        )
        # Act
        _store.resolve_task(store, task_id="a", actor="op")
        # Assert
        assert "blocker" not in _store.get_task(store, task_id="a")

    def test_appends_audit_comment(self, tmp_path):
        # Arrange
        store = tmp_path / "tasks.yaml"
        _store.add_task(
            store,
            id="a",
            title="A",
            status="blocked",
            blocker="operator-decision",
        )
        # Act
        _store.resolve_task(store, task_id="a", actor="op")
        # Assert
        comments = _store.get_task(store, task_id="a").get("comments") or []
        assert any("RESOLVED" in (c.get("text") or "") for c in comments)

    def test_idempotent_noop_on_already_done(self, tmp_path):
        # Arrange — task already at done; second resolve must NOT raise
        # and must append a "noop" comment.
        store = tmp_path / "tasks.yaml"
        _store.add_task(store, id="a", title="A", status="done")
        # Act
        _store.resolve_task(store, task_id="a", actor="op")
        # Assert
        comments = _store.get_task(store, task_id="a").get("comments") or []
        assert any("noop" in (c.get("text") or "") for c in comments)

    def test_unknown_id_raises_not_found(self, tmp_path):
        # before resolve_task gets to its own not-found branch.
        # Arrange
        store = tmp_path / "tasks.yaml"
        _store.add_task(store, id="a", title="A")
        # Act
        # Assert
        with pytest.raises(_store.TaskNotFoundError):
            _store.resolve_task(store, task_id="missing", actor="op")


# === reopen_task ============================================================


class TestReopenTask:
    """Un-resolve ``done`` → ``blocked`` + restore ``operator-decision``."""

    def test_flips_status_to_blocked(self, tmp_path):
        # Arrange
        store = tmp_path / "tasks.yaml"
        _store.add_task(store, id="a", title="A", status="done")
        # Act
        _store.reopen_task(store, task_id="a", by="op")
        # Assert
        assert _store.get_task(store, task_id="a")["status"] == "blocked"

    def test_restores_operator_decision_blocker(self, tmp_path):
        # Arrange
        store = tmp_path / "tasks.yaml"
        _store.add_task(store, id="a", title="A", status="done")
        # Act
        _store.reopen_task(store, task_id="a", by="op")
        # Assert
        assert _store.get_task(store, task_id="a")["blocker"] == "operator-decision"

    def test_appends_audit_comment(self, tmp_path):
        # Arrange
        store = tmp_path / "tasks.yaml"
        _store.add_task(store, id="a", title="A", status="done")
        # Act
        _store.reopen_task(store, task_id="a", by="op")
        # Assert
        comments = _store.get_task(store, task_id="a").get("comments") or []
        assert any("REOPENED" in (c.get("text") or "") for c in comments)

    def test_unknown_id_raises_not_found(self, tmp_path):
        # Arrange
        store = tmp_path / "tasks.yaml"
        _store.add_task(store, id="a", title="A")
        # Act
        # Assert
        with pytest.raises(_store.TaskNotFoundError):
            _store.reopen_task(store, task_id="missing", by="op")
