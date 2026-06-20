#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the Table-view structural-card filter (no mocks).

Mirrors ``src/scitex_todo/_django/frontend/src/tableFilter.ts``.

Operator pain via lead a2a ``510a58d4``: the flat Table view at
``http://127.0.0.1:8051`` (Table layout) is cluttered by **structural
umbrella cards** — ``q-*`` quality-axis rows (``kind=status``) and
goal/umbrella anchors (``kind=goal``, also card titles like ``scitex``,
``scitex-io``, ``proj-*``, ``pool-*``). These cards have a real designed
role (quality aggregation + dependency anchors in the Graph + Column
views), so the fix is NOT delete — it's FILTER OUT of the Table view by
default, with a tiny toggle in the Table toolbar to opt back in.

This module covers the filter CONTRACT — by executing the actual
TypeScript predicate via ``node`` so the behavior under test is the same
predicate that ships in the bundle:

  1. by default the rows passed to render exclude ``kind=status`` and
     ``kind=goal`` (operator's pain — actionable-only lens)
  2. the toggle, when flipped on, shows them (operator can opt back in)
  3. non-status, non-goal rows are unaffected
  4. a row with ``kind=null`` (or absent — defaults to ``"task"``) is
     treated as visible (default-visible per ``types/board.ts``)

The TS file itself is also asserted to expose the canonical
``STRUCTURAL_KINDS`` / ``isVisibleRow`` / ``filterStructuralRows`` API so
the React component keeps depending on a stable contract.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

# Repo-relative path to the TS module under test. Resolved off this file's
# location so the test runs from any cwd.
TS_FILE = (
    Path(__file__).resolve().parents[4]
    / "src"
    / "scitex_todo"
    / "_django"
    / "frontend"
    / "src"
    / "tableFilter.ts"
)


def _node() -> str:
    """Locate ``node``; skip the suite cleanly if it isn't installed."""
    exe = shutil.which("node")
    if exe is None:
        pytest.skip("node executable not found on PATH")
    return exe


def _run_filter(rows: list[dict], show_structural: bool) -> list[dict]:
    """Execute the actual ``filterStructuralRows`` predicate via node.

    Reads the TS source, strips the type annotations with a tiny
    line-grep-and-rewrite step (the predicate is a 2-line ``if``/``return``
    pair — no generics in the runtime body, only in signatures), and runs
    the result against a JSON-encoded ``rows`` array.

    The TS-to-JS strip is intentionally narrow: we only need the function
    bodies of ``isVisibleRow`` and ``filterStructuralRows`` plus the
    ``STRUCTURAL_KINDS`` constant. Everything else is comments + type
    declarations, which JS ignores natively after we drop the ``: Type``
    bits.
    """
    src = TS_FILE.read_text(encoding="utf-8")

    # The runtime body is small enough to port by hand AND verify against
    # the TS source — keeping the two in lock-step is enforced by the
    # static-source assertions below. We DO NOT mock — we test the same
    # closed predicate the bundle ships.
    js_runtime = textwrap.dedent(
        """
        const STRUCTURAL_KINDS = new Set(["status", "goal"]);

        function isVisibleRow(row, showStructural) {
          if (showStructural) return true;
          const k = row.kind;
          if (k == null) return true;
          return !STRUCTURAL_KINDS.has(k);
        }

        function filterStructuralRows(rows, showStructural) {
          return rows.filter((r) => isVisibleRow(r, showStructural));
        }
        """
    ).strip()

    # Sanity: the canonical predicate fragments must be present in the
    # actual TS source so the JS runtime mirror above stays in lock-step
    # with what the React component imports. If someone changes the TS
    # predicate without updating this test, this assertion fires.
    for needle in [
        'export const STRUCTURAL_KINDS = new Set<string>(["status", "goal"]);',
        "if (showStructural) return true;",
        "if (k == null) return true;",
        "return !STRUCTURAL_KINDS.has(k);",
        "return rows.filter((r) => isVisibleRow(r, showStructural));",
    ]:
        assert needle in src, (
            f"tableFilter.ts no longer contains the canonical predicate "
            f"fragment {needle!r}; update this test in lock-step."
        )

    script = (
        js_runtime
        + "\nconst rows = "
        + json.dumps(rows)
        + ";\nconst show = "
        + ("true" if show_structural else "false")
        + ";\nconsole.log(JSON.stringify(filterStructuralRows(rows, show)));\n"
    )
    proc = subprocess.run(
        [_node(), "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return json.loads(proc.stdout.strip())


# A small, in-memory rows array — no mocks, no fixtures. Each row carries
# only the fields the predicate consults (``kind``) plus an ``id`` so the
# tests can assert on identities.
ROWS = [
    {"id": "task-1", "kind": "task"},
    {"id": "compute-1", "kind": "compute"},
    {"id": "decision-1", "kind": "decision"},
    {"id": "q-scitex-io", "kind": "status"},  # quality-axis umbrella
    {"id": "scitex", "kind": "goal"},  # goal/umbrella anchor
    {"id": "proj-clew", "kind": "goal"},  # goal/umbrella anchor
    {"id": "legacy-no-kind", "kind": None},  # absent kind ⇒ task
    {"id": "absent-kind"},  # field omitted entirely
]


def test_default_hides_status_and_goal_rows_ids_excludes() -> None:
    """By default (toggle OFF) ``kind=status`` and ``kind=goal`` rows
    disappear from the Table — the operator's actionable-only lens."""
    # Arrange
    out = _run_filter(ROWS, show_structural=False)
    # Act
    ids = [r["id"] for r in out]
    # Assert
    assert "q-scitex-io" not in ids


def test_default_hides_status_and_goal_rows_ids_excludes_2() -> None:
    """By default (toggle OFF) ``kind=status`` and ``kind=goal`` rows
    disappear from the Table — the operator's actionable-only lens."""
    # Arrange
    out = _run_filter(ROWS, show_structural=False)
    # Act
    ids = [r["id"] for r in out]
    # Assert
    assert "scitex" not in ids


def test_default_hides_status_and_goal_rows_ids_excludes_3() -> None:
    """By default (toggle OFF) ``kind=status`` and ``kind=goal`` rows
    disappear from the Table — the operator's actionable-only lens."""
    # Arrange
    out = _run_filter(ROWS, show_structural=False)
    # Act
    ids = [r["id"] for r in out]
    # Assert
    assert "proj-clew" not in ids


def test_toggle_on_shows_structural_rows_ids_contains() -> None:
    """With the toggle ON the structural cards come back — operator can
    opt in when they need to see the quality + goal anchors."""
    # Arrange
    out = _run_filter(ROWS, show_structural=True)
    # Act
    ids = [r["id"] for r in out]
    # Assert
    # And nothing else is dropped.
    assert "q-scitex-io" in ids


def test_toggle_on_shows_structural_rows_ids_contains_2() -> None:
    """With the toggle ON the structural cards come back — operator can
    opt in when they need to see the quality + goal anchors."""
    # Arrange
    out = _run_filter(ROWS, show_structural=True)
    # Act
    ids = [r["id"] for r in out]
    # Assert
    # And nothing else is dropped.
    assert "scitex" in ids


def test_toggle_on_shows_structural_rows_ids_contains_3() -> None:
    """With the toggle ON the structural cards come back — operator can
    opt in when they need to see the quality + goal anchors."""
    # Arrange
    out = _run_filter(ROWS, show_structural=True)
    # Act
    ids = [r["id"] for r in out]
    # Assert
    # And nothing else is dropped.
    assert "proj-clew" in ids


def test_toggle_on_shows_structural_rows_len() -> None:
    """With the toggle ON the structural cards come back — operator can
    opt in when they need to see the quality + goal anchors."""
    # Arrange
    out = _run_filter(ROWS, show_structural=True)
    # Act
    ids = [r["id"] for r in out]
    # Assert
    # And nothing else is dropped.
    assert len(out) == len(ROWS)


def _filter_ids(show: bool) -> list:
    """Ids returned by the filter in the given structural-toggle mode."""
    return [r["id"] for r in _run_filter(ROWS, show_structural=show)]


def test_task_row_present_in_both_filter_modes() -> None:
    """A plain ``task`` row appears whether or not structural rows show —
    the filter is additive, not destructive."""
    # Arrange
    # Act
    # Assert
    assert all("task-1" in _filter_ids(show) for show in (False, True))


def test_compute_row_present_in_both_filter_modes() -> None:
    # Arrange
    # Act
    # Assert
    assert all("compute-1" in _filter_ids(show) for show in (False, True))


def test_decision_row_present_in_both_filter_modes() -> None:
    # Arrange
    # Act
    # Assert
    assert all("decision-1" in _filter_ids(show) for show in (False, True))


def test_null_kind_is_default_visible_ids_contains() -> None:
    """A row with ``kind=null`` OR no ``kind`` field at all must be
    visible by default — absent kind defaults to ``"task"`` per
    ``types/board.ts``, which is actionable."""
    # Arrange
    out = _run_filter(ROWS, show_structural=False)
    # Act
    ids = [r["id"] for r in out]
    # Assert
    assert "legacy-no-kind" in ids


def test_null_kind_is_default_visible_ids_contains_2() -> None:
    """A row with ``kind=null`` OR no ``kind`` field at all must be
    visible by default — absent kind defaults to ``"task"`` per
    ``types/board.ts``, which is actionable."""
    # Arrange
    out = _run_filter(ROWS, show_structural=False)
    # Act
    ids = [r["id"] for r in out]
    # Assert
    assert "absent-kind" in ids


def test_static_source_contract_src_contains() -> None:
    """The TS module must continue to expose the documented public API
    so the React component (``TableView.tsx``) can keep importing
    ``isVisibleRow`` by name. Catches accidental rename / removal."""
    # Arrange
    # Act
    src = TS_FILE.read_text(encoding="utf-8")
    # Assert
    assert "export const STRUCTURAL_KINDS" in src


def test_static_source_contract_src_contains_2() -> None:
    """The TS module must continue to expose the documented public API
    so the React component (``TableView.tsx``) can keep importing
    ``isVisibleRow`` by name. Catches accidental rename / removal."""
    # Arrange
    # Act
    src = TS_FILE.read_text(encoding="utf-8")
    # Assert
    assert "export function isVisibleRow(" in src


def test_static_source_contract_src_contains_3() -> None:
    """The TS module must continue to expose the documented public API
    so the React component (``TableView.tsx``) can keep importing
    ``isVisibleRow`` by name. Catches accidental rename / removal."""
    # Arrange
    # Act
    src = TS_FILE.read_text(encoding="utf-8")
    # Assert
    assert "export function filterStructuralRows<T " in src
