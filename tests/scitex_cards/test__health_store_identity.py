#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`health` reports when the store and the DB's stamp disagree.

The store identity lives in two places — the launcher env and the DB's
provenance stamp — with nothing keeping them in step. A mismatch makes the
ownership guard refuse EVERY write, correctly, but the symptom is a total write
outage with no monitor.

On 2026-07-19 the MCP server resolved ~/.scitex/cards/tasks.yaml (deleted during
the cutover) while the DB was stamped ~/.scitex/todo/tasks.yaml. Every write
through the surface other agents use was refused and nothing reported it:
`store_canonical` answers "does a parseable file exist there", not "can this
process write".
"""

from __future__ import annotations

import pathlib

import pytest

from scitex_cards._db_bootstrap import import_from_yaml
from scitex_cards._health import health

_SEED = "tasks:\n- id: t\n  title: T\n  status: done\n  assignee: x\n  created_by: x\n"


def _check(report: dict, name: str) -> dict:
    return {c["name"]: c for c in report["checks"]}[name]


@pytest.fixture()
def two_stores(tmp_path, env):
    """Two real stores and a DB bootstrapped from the FIRST of them."""
    a = tmp_path / "a" / "tasks.yaml"
    a.parent.mkdir()
    a.write_text(_SEED)
    b = tmp_path / "b" / "tasks.yaml"
    b.parent.mkdir()
    b.write_text(_SEED)
    env.set("SCITEX_CARDS_DB", str(tmp_path / "cards.db"))
    env.set("SCITEX_TODO_DB", str(tmp_path / "cards.db"))
    import_from_yaml(tasks_path=str(a))
    return a, b


def test_matching_store_and_stamp_is_ok(two_stores):
    """The normal case must not raise a false alarm."""
    # Arrange
    store_a, _ = two_stores

    # Act
    check = _check(health(store=str(store_a)), "store_identity")

    # Assert
    assert check["ok"] is True, check["detail"]


def test_a_mismatch_is_reported_as_a_write_outage(two_stores):
    """The 2026-07-19 shape: resolved store != stamped store."""
    # Arrange
    _, store_b = two_stores

    # Act
    check = _check(health(store=str(store_b)), "store_identity")

    # Assert
    assert check["ok"] is False
    assert "STORE IDENTITY MISMATCH" in check["detail"]


def test_the_hint_names_both_ways_to_resolve_it(two_stores):
    """A failing check must say what to DO, not merely that something is wrong."""
    # Arrange
    _, store_b = two_stores

    # Act
    check = _check(health(store=str(store_b)), "store_identity")

    # Assert
    assert "--as-store" in check["hint"]
    assert "SCITEX_CARDS_TASKS_YAML_SHARED" in check["hint"]


def test_a_missing_db_is_not_an_alarm(tmp_path, env):
    """Nothing to disagree with yet — a fresh install must report clean."""
    # Arrange
    store = tmp_path / "tasks.yaml"
    store.write_text(_SEED)
    env.set("SCITEX_CARDS_DB", str(tmp_path / "absent.db"))
    env.set("SCITEX_TODO_DB", str(tmp_path / "absent.db"))

    # Act
    check = _check(health(store=str(store)), "store_identity")

    # Assert
    assert check["ok"] is True, check["detail"]


# EOF
