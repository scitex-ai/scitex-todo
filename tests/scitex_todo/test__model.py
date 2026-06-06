#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the task model + loader/validator (no mocks; real tmp files)."""

from __future__ import annotations

import contextlib

import pytest

from scitex_todo import TaskValidationError
from scitex_todo._model import load_tasks, save_tasks


def _write(tmp_path, text):
    """Write a tasks.yaml under tmp_path and return its path."""
    path = tmp_path / "tasks.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_load_tasks_returns_validated_list_in_order(tmp_path):
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n"
        "  - {id: a, title: First, status: done}\n"
        "  - {id: b, title: Second, status: pending}\n",
    )
    # Act
    tasks = load_tasks(store)
    # Assert
    assert [t["id"] for t in tasks] == ["a", "b"]


def test_load_tasks_accepts_goal_status(tmp_path):
    # Arrange
    store = _write(tmp_path, "tasks:\n  - {id: north, title: Big Goal, status: goal}\n")
    # Act
    tasks = load_tasks(store)
    # Assert
    assert tasks[0]["status"] == "goal"


def test_load_tasks_raises_on_duplicate_id(tmp_path):
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n"
        "  - {id: dup, title: One, status: done}\n"
        "  - {id: dup, title: Two, status: done}\n",
    )
    # Act
    ctx = pytest.raises(TaskValidationError)
    # Assert
    with ctx:
        load_tasks(store)


def test_load_tasks_raises_on_bad_status(tmp_path):
    # Arrange
    store = _write(tmp_path, "tasks:\n  - {id: x, title: X, status: wibble}\n")
    # Act
    ctx = pytest.raises(TaskValidationError)
    # Assert
    with ctx:
        load_tasks(store)


def test_load_tasks_raises_on_missing_title(tmp_path):
    # Arrange
    store = _write(tmp_path, "tasks:\n  - {id: notitle, status: pending}\n")
    # Act
    ctx = pytest.raises(TaskValidationError)
    # Assert
    with ctx:
        load_tasks(store)


def test_load_tasks_raises_on_missing_id(tmp_path):
    # Arrange
    store = _write(tmp_path, "tasks:\n  - {title: No Id, status: pending}\n")
    # Act
    ctx = pytest.raises(TaskValidationError)
    # Assert
    with ctx:
        load_tasks(store)


def test_load_tasks_raises_when_tasks_not_a_list(tmp_path):
    # Arrange
    store = _write(tmp_path, "tasks: not-a-list\n")
    # Act
    ctx = pytest.raises(TaskValidationError)
    # Assert
    with ctx:
        load_tasks(store)


def test_load_tasks_raises_on_missing_file(tmp_path):
    # Arrange
    missing = tmp_path / "nope.yaml"
    # Act
    ctx = pytest.raises(FileNotFoundError)
    # Assert
    with ctx:
        load_tasks(missing)


def test_load_tasks_accepts_integer_priority(tmp_path):
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n  - {id: a, title: First, status: done, priority: 3}\n",
    )
    # Act
    tasks = load_tasks(store)
    # Assert
    assert tasks[0]["priority"] == 3


def test_load_tasks_raises_on_non_integer_priority(tmp_path):
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n  - {id: a, title: First, status: done, priority: high}\n",
    )
    # Act
    ctx = pytest.raises(TaskValidationError)
    # Assert
    with ctx:
        load_tasks(store)


def test_load_tasks_rejects_boolean_priority(tmp_path):
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n  - {id: a, title: First, status: done, priority: true}\n",
    )
    # Act
    ctx = pytest.raises(TaskValidationError)
    # Assert
    with ctx:
        load_tasks(store)


def test_save_tasks_round_trip_preserves_priority(tmp_path):
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n  - {id: a, title: First, status: done}\n",
    )
    tasks = load_tasks(store)
    tasks[0]["priority"] = 7
    # Act
    save_tasks(tasks, store)
    reloaded = load_tasks(store)
    # Assert
    assert reloaded[0]["priority"] == 7


def test_save_tasks_round_trip_preserves_comments(tmp_path):
    # Arrange
    path = tmp_path / "tasks.yaml"
    path.write_text(
        "# top-of-file comment kept verbatim\n"
        "tasks:\n"
        "  - id: a  # inline task comment\n"
        "    title: First\n"
        "    status: done\n",
        encoding="utf-8",
    )
    tasks = load_tasks(path)
    tasks[0]["priority"] = 1
    # Act
    save_tasks(tasks, path)
    rewritten = path.read_text(encoding="utf-8")
    # Assert
    assert "# top-of-file comment kept verbatim" in rewritten


def test_save_tasks_preserves_inline_comment(tmp_path):
    # Arrange
    path = tmp_path / "tasks.yaml"
    path.write_text(
        "tasks:\n"
        "  - id: a  # inline task comment\n"
        "    title: First\n"
        "    status: done\n",
        encoding="utf-8",
    )
    tasks = load_tasks(path)
    tasks[0]["priority"] = 2
    # Act
    save_tasks(tasks, path)
    rewritten = path.read_text(encoding="utf-8")
    # Assert
    assert "# inline task comment" in rewritten


def test_save_tasks_raises_on_bad_priority_type(tmp_path):
    # Arrange
    store = _write(tmp_path, "tasks:\n  - {id: a, title: First, status: done}\n")
    tasks = load_tasks(store)
    tasks[0]["priority"] = "high"
    # Act
    ctx = pytest.raises(TaskValidationError)
    # Assert
    with ctx:
        save_tasks(tasks, store)


def test_save_tasks_does_not_write_when_validation_fails(tmp_path):
    # Arrange
    store = _write(tmp_path, "tasks:\n  - {id: a, title: First, status: done}\n")
    before = store.read_text(encoding="utf-8")
    bad = [{"id": "a", "title": "First", "status": "bogus"}]
    with contextlib.suppress(TaskValidationError):
        save_tasks(bad, store)
    # Act
    after = store.read_text(encoding="utf-8")
    # Assert
    assert after == before


def test_save_tasks_writes_fresh_store_when_absent(tmp_path):
    # Arrange
    target = tmp_path / "nested" / "new.yaml"
    tasks = [{"id": "a", "title": "First", "status": "pending", "priority": 1}]
    # Act
    save_tasks(tasks, target)
    reloaded = load_tasks(target)
    # Assert
    assert reloaded[0]["id"] == "a"


_PARENT_STORE_TEXT = (
    "tasks:\n"
    "  - {id: hub, title: Hub, status: goal}\n"
    "  - {id: child, title: Child, status: pending, parent: hub}\n"
)


def test_load_tasks_reads_parent_id_on_child(tmp_path):
    # Arrange — additive-optional `parent` is a task-id string identifying the
    # node this task nests under (drill-down view follows this relation).
    store = _write(tmp_path, _PARENT_STORE_TEXT)
    # Act
    by_id = {t["id"]: t for t in load_tasks(store)}
    # Assert
    assert by_id["child"]["parent"] == "hub"


def test_load_tasks_leaves_parentless_task_without_parent(tmp_path):
    # Arrange
    store = _write(tmp_path, _PARENT_STORE_TEXT)
    # Act
    by_id = {t["id"]: t for t in load_tasks(store)}
    # Assert
    assert by_id["hub"].get("parent") is None


def test_load_tasks_treats_missing_parent_as_optional(tmp_path):
    # Arrange — pre-`parent` YAML must keep loading unchanged.
    store = _write(
        tmp_path,
        "tasks:\n  - {id: solo, title: Solo, status: pending}\n",
    )
    # Act
    tasks = load_tasks(store)
    # Assert
    assert "parent" not in tasks[0]


def test_load_tasks_raises_on_non_string_parent(tmp_path):
    # Arrange — a non-string parent (here: an int) is a structural fault.
    store = _write(
        tmp_path,
        "tasks:\n  - {id: a, title: First, status: done, parent: 7}\n",
    )
    # Act
    ctx = pytest.raises(TaskValidationError)
    # Assert
    with ctx:
        load_tasks(store)


def test_load_tasks_raises_on_empty_string_parent(tmp_path):
    # Arrange — explicit empty-string parent is ambiguous; reject so the
    # operator sees the typo rather than getting a silently top-level node.
    store = _write(
        tmp_path,
        'tasks:\n  - {id: a, title: First, status: done, parent: ""}\n',
    )
    # Act
    ctx = pytest.raises(TaskValidationError)
    # Assert
    with ctx:
        load_tasks(store)


def test_save_tasks_round_trip_preserves_parent(tmp_path):
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n"
        "  - {id: hub, title: Hub, status: goal}\n"
        "  - {id: child, title: Child, status: pending, parent: hub}\n",
    )
    tasks = load_tasks(store)
    # Act — touch an unrelated field and rewrite; `parent` must survive.
    for task in tasks:
        if task["id"] == "child":
            task["priority"] = 2
    save_tasks(tasks, store)
    reloaded = load_tasks(store)
    # Assert
    child = next(t for t in reloaded if t["id"] == "child")
    assert child["parent"] == "hub"


def test_load_tasks_treats_missing_comments_as_optional(tmp_path):
    # Arrange — pre-`comments` YAML must keep loading unchanged.
    store = _write(
        tmp_path,
        "tasks:\n  - {id: a, title: First, status: done}\n",
    )
    # Act
    tasks = load_tasks(store)
    # Assert
    assert "comments" not in tasks[0]


def test_load_tasks_accepts_valid_comments(tmp_path):
    # Arrange — a comment with a non-empty text is valid.
    store = _write(
        tmp_path,
        "tasks:\n"
        "  - id: a\n"
        "    title: First\n"
        "    status: done\n"
        "    comments:\n"
        "      - {ts: '2026-01-01T00:00:00+00:00', author: alice, text: hi}\n",
    )
    # Act
    tasks = load_tasks(store)
    # Assert
    assert tasks[0]["comments"][0]["text"] == "hi"


def test_load_tasks_raises_on_non_list_comments(tmp_path):
    # Arrange — comments must be a list, not a scalar.
    store = _write(
        tmp_path,
        "tasks:\n  - {id: a, title: First, status: done, comments: nope}\n",
    )
    # Act
    ctx = pytest.raises(TaskValidationError)
    # Assert
    with ctx:
        load_tasks(store)


def test_load_tasks_raises_on_comment_missing_text(tmp_path):
    # Arrange — each comment needs a non-empty string `text`.
    store = _write(
        tmp_path,
        "tasks:\n"
        "  - id: a\n"
        "    title: First\n"
        "    status: done\n"
        "    comments:\n"
        "      - {author: alice}\n",
    )
    # Act
    ctx = pytest.raises(TaskValidationError)
    # Assert
    with ctx:
        load_tasks(store)


# --------------------------------------------------------------------------- #
# Phase 1 additions — scope / assignee / _log_meta validation                 #
# --------------------------------------------------------------------------- #
def test_load_accepts_scope(tmp_path):
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n"
        "  - id: a\n"
        "    title: A\n"
        "    status: pending\n"
        "    scope: agent:proj-scitex-todo\n"
        "    assignee: agent:lead\n",
    )
    # Act
    tasks = load_tasks(store)
    # Assert
    assert tasks[0]["scope"] == "agent:proj-scitex-todo"


def test_load_accepts_assignee(tmp_path):
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n"
        "  - id: a\n"
        "    title: A\n"
        "    status: pending\n"
        "    scope: agent:proj-scitex-todo\n"
        "    assignee: agent:lead\n",
    )
    # Act
    tasks = load_tasks(store)
    # Assert
    assert tasks[0]["assignee"] == "agent:lead"


def test_load_rejects_non_string_scope(tmp_path):
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n"
        "  - {id: a, title: A, status: pending, scope: 42}\n",
    )
    # Act
    ctx = pytest.raises(TaskValidationError, match="non-string scope")
    # Assert
    with ctx:
        load_tasks(store)


def test_load_rejects_empty_string_assignee(tmp_path):
    # Arrange
    store = _write(
        tmp_path,
        'tasks:\n  - {id: a, title: A, status: pending, assignee: ""}\n',
    )
    # Act
    ctx = pytest.raises(TaskValidationError, match="assignee")
    # Assert
    with ctx:
        load_tasks(store)


def test_load_accepts_log_meta_mapping(tmp_path):
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n"
        "  - id: a\n"
        "    title: A\n"
        "    status: done\n"
        "    _log_meta:\n"
        "      completed_at: '2026-05-27T10:00:00Z'\n"
        "      completed_by: agent:test\n",
    )
    # Act
    tasks = load_tasks(store)
    # Assert
    assert tasks[0]["_log_meta"]["completed_by"] == "agent:test"


def test_load_rejects_non_mapping_log_meta(tmp_path):
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n  - {id: a, title: A, status: done, _log_meta: 'oops'}\n",
    )
    # Act
    ctx = pytest.raises(TaskValidationError, match="_log_meta")
    # Assert
    with ctx:
        load_tasks(store)


def test_save_tasks_round_trip_preserves_log_meta_completed_by(tmp_path):
    """A done task's `_log_meta.completed_by` survives a save_tasks rewrite."""
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n"
        "  - id: a\n"
        "    title: A\n"
        "    status: done\n"
        "    _log_meta:\n"
        "      completed_at: '2026-05-27T10:00:00Z'\n"
        "      completed_by: agent:original\n",
    )
    tasks = load_tasks(store)
    tasks[0]["priority"] = 1
    # Act
    save_tasks(tasks, store)
    reloaded = load_tasks(store)[0]
    # Assert
    assert reloaded["_log_meta"]["completed_by"] == "agent:original"


def test_save_tasks_round_trip_preserves_log_meta_completed_at(tmp_path):
    """A done task's `_log_meta.completed_at` survives a save_tasks rewrite."""
    # Arrange
    store = _write(
        tmp_path,
        "tasks:\n"
        "  - id: a\n"
        "    title: A\n"
        "    status: done\n"
        "    _log_meta:\n"
        "      completed_at: '2026-05-27T10:00:00Z'\n"
        "      completed_by: agent:original\n",
    )
    tasks = load_tasks(store)
    tasks[0]["priority"] = 1
    # Act
    save_tasks(tasks, store)
    reloaded = load_tasks(store)[0]
    # Assert
    assert reloaded["_log_meta"]["completed_at"] == "2026-05-27T10:00:00Z"


# ---------------------------------------------------------------------------
# kind: Literal["task", "compute"] (north-star pillar #1 — compute-state deps)
# Closed validated enum per lead a2a `2c7a431d` — fail-loud on unknown values
# so a "comput" typo can't silently create an unrecognized kind.
# ---------------------------------------------------------------------------

def test_load_tasks_kind_defaults_to_task_when_absent(tmp_path):
    """Absence of `kind` is equivalent to `kind: task` (the default)."""
    store = _write(
        tmp_path,
        "tasks:\n  - {id: t1, title: Plain, status: pending}\n",
    )
    tasks = load_tasks(store)
    # The loader doesn't synthesize a value — `kind` is simply absent on plain
    # rows. Downstream consumers treat absence as "task".
    assert "kind" not in tasks[0]


def test_load_tasks_accepts_kind_task(tmp_path):
    store = _write(
        tmp_path,
        "tasks:\n  - {id: t1, title: Plain, status: pending, kind: task}\n",
    )
    tasks = load_tasks(store)
    assert tasks[0]["kind"] == "task"


def test_load_tasks_accepts_kind_compute_with_metadata(tmp_path):
    store = _write(
        tmp_path,
        "tasks:\n"
        "  - id: spartan-pac\n"
        "    title: 'compute: PAC SLE multi-lane'\n"
        "    status: in_progress\n"
        "    kind: compute\n"
        "    job_id: '25754194'\n"
        "    host: spartan\n"
        "    command: srun -p h100 -t 12:00:00 python pac/sle.py\n"
        "    started_at: '2026-06-06T03:14:00Z'\n",
    )
    tasks = load_tasks(store)
    assert tasks[0]["kind"] == "compute"
    assert tasks[0]["job_id"] == "25754194"
    assert tasks[0]["host"] == "spartan"
    assert tasks[0]["started_at"] == "2026-06-06T03:14:00Z"


def test_load_tasks_raises_on_unknown_kind(tmp_path):
    """`comput` typo (or any value not in VALID_KINDS) is fail-loud."""
    store = _write(
        tmp_path,
        "tasks:\n  - {id: x, title: X, status: pending, kind: comput}\n",
    )
    with pytest.raises(TaskValidationError) as exc_info:
        load_tasks(store)
    # Error must NAME the bad value + the valid set so the writer can fix it.
    assert "comput" in str(exc_info.value)
    assert "task" in str(exc_info.value) and "compute" in str(exc_info.value)


def test_load_tasks_raises_on_compute_metadata_without_kind(tmp_path):
    """Setting job_id/host/etc. on a non-compute row is a config error.

    The lead's `kind` discriminator is what tells the writer-side watcher
    "this row is mine to update". Allowing compute metadata on a plain task
    would silently break that contract — fail-loud instead.
    """
    store = _write(
        tmp_path,
        "tasks:\n  - {id: x, title: X, status: pending, job_id: '12345'}\n",
    )
    with pytest.raises(TaskValidationError) as exc_info:
        load_tasks(store)
    assert "job_id" in str(exc_info.value)
    assert "kind: compute" in str(exc_info.value)


def test_load_tasks_raises_on_compute_metadata_with_kind_task(tmp_path):
    """`job_id` (a TRUE compute-only field) on a kind=task row fails-loud.

    Note: pre-PR-#57, `host` was also in the compute-only fence and was
    used as the example here. Per operator co-design TG 9667, `host` is
    now a generic field allowed on any row (it's the universal "where
    does this live/run" handle). The compute-only fence still covers
    job_id / command / started_at / finished_at — those four have a
    compute-job-specific semantic that doesn't fit other kinds.
    """
    store = _write(
        tmp_path,
        "tasks:\n  - {id: x, title: X, status: pending, kind: task, job_id: '42'}\n",
    )
    with pytest.raises(TaskValidationError) as exc_info:
        load_tasks(store)
    assert "job_id" in str(exc_info.value)


def test_load_tasks_allows_host_on_kind_task_row(tmp_path):
    """`host` is GENERIC (operator TG 9667) — allowed on any row, not just compute."""
    store = _write(
        tmp_path,
        "tasks:\n  - {id: x, title: X, status: pending, host: ywata-note-win}\n",
    )
    # No kind set + host present → valid; host is generic.
    assert load_tasks(store)[0]["host"] == "ywata-note-win"


def test_load_tasks_raises_on_non_string_compute_field(tmp_path):
    store = _write(
        tmp_path,
        "tasks:\n"
        "  - id: bad\n"
        "    title: bad\n"
        "    status: pending\n"
        "    kind: compute\n"
        "    job_id: 25754194\n",  # int, not string
    )
    with pytest.raises(TaskValidationError) as exc_info:
        load_tasks(store)
    assert "job_id" in str(exc_info.value)
    assert "non-string" in str(exc_info.value)


def test_save_tasks_round_trips_kind_and_compute_metadata(tmp_path):
    """ruamel round-trip preserves `kind:` + compute fields with comments."""
    store = _write(
        tmp_path,
        "# preserved header\n"
        "tasks:\n"
        "  - id: c1\n"
        "    title: 'compute: example'\n"
        "    status: in_progress\n"
        "    kind: compute\n"
        "    job_id: '99'\n"
        "    host: spartan\n"
        "    command: echo hi\n",
    )
    tasks = load_tasks(store)
    tasks[0]["status"] = "done"
    tasks[0]["finished_at"] = "2026-06-06T13:30:00Z"
    save_tasks(tasks, store)
    reloaded = load_tasks(store)[0]
    assert reloaded["kind"] == "compute"
    assert reloaded["job_id"] == "99"
    assert reloaded["finished_at"] == "2026-06-06T13:30:00Z"
    # Comment must survive.
    assert "# preserved header" in store.read_text()


# ---------------------------------------------------------------------------
# kind="decision" — decision-nodes are first-class graph nodes (north-star
# pillar #4, operator TG 9524). Extends VALID_KINDS from ADR-0002.
# ---------------------------------------------------------------------------

def test_load_tasks_accepts_kind_decision(tmp_path):
    """`kind: decision` is a valid kind alongside task / compute."""
    store = _write(
        tmp_path,
        "tasks:\n"
        "  - id: decide-x\n"
        "    title: 'decide: X'\n"
        "    status: pending\n"
        "    kind: decision\n",
    )
    tasks = load_tasks(store)
    assert tasks[0]["kind"] == "decision"


def test_load_tasks_decision_kind_uses_existing_statuses(tmp_path):
    """A decision-node's lifecycle uses VALID_STATUSES (pending → done)."""
    # Pending decision — awaiting resolution.
    store = _write(
        tmp_path,
        "tasks:\n  - {id: d1, title: 'decide: a/b', status: pending, kind: decision}\n",
    )
    assert load_tasks(store)[0]["status"] == "pending"


# ---------------------------------------------------------------------------
# blocker: Literal["compute", "dep", "operator-decision", "agent-wait"]
# Operator TG 9522/9524, lead a2a 4691b114/c839c59b/2bd37bd2/554435df.
# ADR-0004: closed validated enum, fail-loud, only on status=blocked rows.
# ---------------------------------------------------------------------------

def test_load_tasks_accepts_blocker_operator_decision_on_blocked(tmp_path):
    """`blocker: operator-decision` valid on a status=blocked task."""
    store = _write(
        tmp_path,
        "tasks:\n"
        "  - {id: x, title: X, status: blocked, blocker: operator-decision}\n",
    )
    assert load_tasks(store)[0]["blocker"] == "operator-decision"


def test_load_tasks_accepts_all_four_blocker_variants(tmp_path):
    """The four operator-named blocker variants each load cleanly."""
    store = _write(
        tmp_path,
        "tasks:\n"
        "  - {id: a, title: A, status: blocked, blocker: compute}\n"
        "  - {id: b, title: B, status: blocked, blocker: dep}\n"
        "  - {id: c, title: C, status: blocked, blocker: operator-decision}\n"
        "  - {id: d, title: D, status: blocked, blocker: agent-wait}\n",
    )
    tasks = load_tasks(store)
    blockers = [t["blocker"] for t in tasks]
    assert blockers == ["compute", "dep", "operator-decision", "agent-wait"]


def test_load_tasks_raises_on_unknown_blocker(tmp_path):
    """A typo (or any value not in VALID_BLOCKERS) is fail-loud."""
    store = _write(
        tmp_path,
        "tasks:\n  - {id: x, title: X, status: blocked, blocker: oprator}\n",
    )
    with pytest.raises(TaskValidationError) as exc_info:
        load_tasks(store)
    assert "oprator" in str(exc_info.value)
    assert "operator-decision" in str(exc_info.value)


def test_load_tasks_raises_on_blocker_with_non_blocked_status(tmp_path):
    """Naming a blocker on a non-blocked task is a config error."""
    store = _write(
        tmp_path,
        "tasks:\n"
        "  - {id: x, title: X, status: in_progress, blocker: operator-decision}\n",
    )
    with pytest.raises(TaskValidationError) as exc_info:
        load_tasks(store)
    assert "blocker" in str(exc_info.value)
    assert "status: blocked" in str(exc_info.value)


def test_load_tasks_blocker_absent_on_blocked_is_acceptable(tmp_path):
    """A blocked task without a `blocker` field is still valid.

    (Documented as a soft-degrade: "we know it's blocked but haven't named
    the variant yet." The board can render a generic 🚧 with no badge.)
    """
    store = _write(
        tmp_path,
        "tasks:\n  - {id: x, title: X, status: blocked}\n",
    )
    tasks = load_tasks(store)
    assert "blocker" not in tasks[0]


def test_load_tasks_blocker_and_kind_are_orthogonal(tmp_path):
    """A kind=decision row can have ANY blocker variant — enums are independent.

    The "decisions usually have blocker=operator-decision" relationship is
    convention, not validator-enforced (ADR-0004 Notes).
    """
    store = _write(
        tmp_path,
        "tasks:\n"
        "  - id: d1\n"
        "    title: 'decide: model-picks-a-or-b'\n"
        "    status: blocked\n"
        "    kind: decision\n"
        "    blocker: compute\n",  # ← decision blocked on a model run; unusual but legal
    )
    t = load_tasks(store)[0]
    assert t["kind"] == "decision"
    assert t["blocker"] == "compute"


def test_save_tasks_round_trips_decision_kind_and_blocker(tmp_path):
    """ruamel preserves the new fields + a hand-written comment."""
    store = _write(
        tmp_path,
        "# preserved\n"
        "tasks:\n"
        "  - id: decide-hub-cutover\n"
        "    title: 'decide: hub prod-cutover final GO'\n"
        "    status: blocked\n"
        "    kind: decision\n"
        "    blocker: operator-decision\n",
    )
    tasks = load_tasks(store)
    tasks[0]["status"] = "done"   # operator decided
    tasks[0].pop("blocker")        # no longer blocked
    save_tasks(tasks, store)
    reloaded = load_tasks(store)[0]
    assert reloaded["status"] == "done"
    assert "blocker" not in reloaded
    assert reloaded["kind"] == "decision"
    assert "# preserved" in store.read_text()


# ===========================================================================
# Task dataclass — the SINGLE schema source (ADR-0007 / quality-hygiene PR)
# Operator co-design TG 9667 + lead a2a `6d9b6073` + `a62db48c`.
# ===========================================================================


def test_task_dataclass_from_dict_constructs_minimum_shape():
    from scitex_todo._model import Task

    t = Task.from_dict({"id": "x", "title": "X"})
    assert t.id == "x"
    assert t.title == "X"
    assert t.status == "pending"   # default
    assert t.comments == []         # default factory


def test_task_dataclass_from_dict_carries_all_operator_fields():
    from scitex_todo._model import Task

    t = Task.from_dict({
        "id": "x", "title": "X",
        "task": "the BIG line",
        "project": "scitex-todo",
        "host": "ywata-note-win",
        "created_at": "2026-06-07T01:00:00Z",
        "goal": "make the board the fleet's shared SSoT",
        "agent": "proj-scitex-todo",
        "last_activity": "12s ago",
        "pr_url": "https://github.com/ywatanabe1989/scitex-todo/pull/54",
        "issue_url": "https://github.com/ywatanabe1989/scitex-agent-container/issues/324",
    })
    assert t.task == "the BIG line"
    assert t.project == "scitex-todo"
    assert t.host == "ywata-note-win"
    assert t.goal == "make the board the fleet's shared SSoT"
    assert t.pr_url.endswith("/pull/54")


def test_task_dataclass_from_dict_ignores_unknown_keys():
    from scitex_todo._model import Task

    t = Task.from_dict({"id": "x", "title": "X", "future_field": "ok"})
    assert t.id == "x"
    # No error; unknown key dropped (forward-compat).


def test_task_dataclass_from_dict_normalizes_legacy_dep_to_dependency():
    """Legacy `blocker: "dep"` → canonical `"dependency"` on dataclass read."""
    from scitex_todo._model import Task

    t = Task.from_dict({"id": "x", "title": "X", "status": "blocked", "blocker": "dep"})
    assert t.blocker == "dependency"


def test_task_dataclass_to_dict_omits_default_fields():
    from scitex_todo._model import Task

    t = Task(id="x", title="X")
    d = t.to_dict()
    assert d == {"id": "x", "title": "X", "status": "pending"}


def test_task_dataclass_to_dict_omits_empty_lists():
    from scitex_todo._model import Task

    t = Task(id="x", title="X", depends_on=[], blocks=[], comments=[])
    d = t.to_dict()
    assert "depends_on" not in d and "blocks" not in d and "comments" not in d


def test_task_dataclass_round_trip_preserves_fields():
    from scitex_todo._model import Task

    payload = {
        "id": "x", "title": "X", "task": "do the thing",
        "project": "scitex-todo", "host": "ywata", "agent": "proj-scitex-todo",
        "status": "blocked", "blocker": "operator-decision",
        "goal": "ship the board", "depends_on": ["a", "b"],
        "tags": ["P0", "infra"],   # ← unknown key, gets dropped
    }
    t = Task.from_dict(payload)
    d = t.to_dict()
    # Round-trip preserves every known field; unknown `tags` is dropped.
    assert d["task"] == "do the thing"
    assert d["status"] == "blocked"
    assert d["blocker"] == "operator-decision"
    assert d["depends_on"] == ["a", "b"]
    assert "tags" not in d


# ---------------------------------------------------------------------------
# `dependency` enum rename + `none` value (operator co-design TG 9667)
# ---------------------------------------------------------------------------


def test_load_tasks_accepts_canonical_dependency_blocker(tmp_path):
    """`blocker: "dependency"` (the canonical spelling) loads cleanly."""
    store = _write(
        tmp_path,
        "tasks:\n  - {id: x, title: X, status: blocked, blocker: dependency}\n",
    )
    assert load_tasks(store)[0]["blocker"] == "dependency"


def test_load_tasks_still_accepts_legacy_dep_blocker(tmp_path):
    """Legacy `blocker: "dep"` is still accepted during the deprecation window."""
    store = _write(
        tmp_path,
        "tasks:\n  - {id: x, title: X, status: blocked, blocker: dep}\n",
    )
    # Validator passes; the dict still carries "dep". The Task dataclass
    # normalizes on read; legacy writers that go through save_tasks
    # without converting still produce "dep" until they migrate.
    assert load_tasks(store)[0]["blocker"] == "dep"


def test_load_tasks_accepts_none_blocker(tmp_path):
    """`blocker: "none"` explicitly says "we looked, no blocker named" — distinct from absent."""
    store = _write(
        tmp_path,
        "tasks:\n  - {id: x, title: X, status: blocked, blocker: none}\n",
    )
    assert load_tasks(store)[0]["blocker"] == "none"


# ---------------------------------------------------------------------------
# New operator-co-designed fields — additive validators only.
# ---------------------------------------------------------------------------


def test_load_tasks_accepts_all_new_operator_fields(tmp_path):
    """task / project / host / created_at / goal / agent / last_activity /
    pr_url / issue_url all load cleanly when present."""
    store = _write(
        tmp_path,
        "tasks:\n"
        "  - id: x\n"
        "    title: X\n"
        "    status: pending\n"
        "    task: 'PR #54 in CI'\n"
        "    project: scitex-todo\n"
        "    host: ywata-note-win\n"
        "    created_at: '2026-06-07T01:00:00Z'\n"
        "    goal: ship the board\n"
        "    agent: proj-scitex-todo\n"
        "    last_activity: '12s ago'\n"
        "    pr_url: https://github.com/ywatanabe1989/scitex-todo/pull/54\n"
        "    issue_url: https://github.com/ywatanabe1989/scitex-agent-container/issues/324\n",
    )
    t = load_tasks(store)[0]
    assert t["task"] == "PR #54 in CI"
    assert t["host"] == "ywata-note-win"


def test_load_tasks_raises_on_non_string_task_field(tmp_path):
    """`task: 123` (int) fails-loud — type-check on write."""
    store = _write(
        tmp_path,
        "tasks:\n  - {id: x, title: X, status: pending, task: 123}\n",
    )
    with pytest.raises(TaskValidationError) as exc_info:
        load_tasks(store)
    assert "task" in str(exc_info.value)
    assert "non-string" in str(exc_info.value)


def test_load_tasks_raises_on_non_string_pr_url(tmp_path):
    """`pr_url: 12345` (int) fails-loud — URL must be a string."""
    store = _write(
        tmp_path,
        "tasks:\n  - {id: x, title: X, status: pending, pr_url: 12345}\n",
    )
    with pytest.raises(TaskValidationError) as exc_info:
        load_tasks(store)
    assert "pr_url" in str(exc_info.value)


def test_load_tasks_raises_on_empty_goal_string(tmp_path):
    """`goal: ""` (empty string) fails-loud — non-empty rule."""
    store = _write(
        tmp_path,
        "tasks:\n  - {id: x, title: X, status: pending, goal: \"\"}\n",
    )
    with pytest.raises(TaskValidationError) as exc_info:
        load_tasks(store)
    assert "goal" in str(exc_info.value)

# EOF
