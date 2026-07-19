#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The backup rail refuses to record a wipe as if it were a snapshot.

On 2026-07-19 the live DB was destroyed (2,138 cards -> 53) and the rail did
exactly as told: committed ``snapshot: 53 tasks`` as HEAD, one commit after
``snapshot: 2138 tasks``, silently. The rail was WORKING — that is the point. A
backup that faithfully records a catastrophe with no alarm stops being a safety
net and becomes a propagation mechanism.
"""

from __future__ import annotations

import pathlib

import pytest
from click.testing import CliRunner

from scitex_cards._cli._db import db_group
from scitex_cards._db_bootstrap import import_from_yaml


def _seed(src: pathlib.Path, n: int) -> None:
    src.write_text(
        "tasks:\n"
        + "".join(
            f"- id: t{i}\n  title: T{i}\n  status: done\n"
            f"  assignee: x\n  created_by: x\n"
            for i in range(n)
        )
    )


@pytest.fixture()
def rail(tmp_path, env):
    """A snapshot repo with one healthy 100-card snapshot already committed."""
    db = tmp_path / "cards.db"
    env.set("SCITEX_CARDS_DB", str(db))
    env.set("SCITEX_TODO_DB", str(db))
    src = tmp_path / "tasks.yaml"
    snaps = tmp_path / "snapshots"
    _seed(src, 100)
    import_from_yaml(tasks_path=str(src))
    result = CliRunner().invoke(db_group, ["snapshot", "--dir", str(snaps)])
    assert result.exit_code == 0, result.output
    return src, snaps


def test_a_collapsed_card_count_is_refused(rail):
    """The 2026-07-19 shape: the store is wiped, the rail must NOT record it."""
    # Arrange
    src, snaps = rail
    _seed(src, 3)
    import_from_yaml(tasks_path=str(src))

    # Act
    result = CliRunner().invoke(db_group, ["snapshot", "--dir", str(snaps)])

    # Assert
    assert result.exit_code != 0, "a wipe must not be snapshotted silently"
    assert "collapsed from 100 to 3" in result.output


def test_allow_shrink_permits_a_genuine_bulk_delete(rail):
    """A real bulk delete is legitimate — the guard must be overridable."""
    # Arrange
    src, snaps = rail
    _seed(src, 3)
    import_from_yaml(tasks_path=str(src))

    # Act
    result = CliRunner().invoke(
        db_group, ["snapshot", "--dir", str(snaps), "--allow-shrink"]
    )

    # Assert
    assert result.exit_code == 0, result.output


def test_ordinary_growth_is_never_blocked(rail):
    """The guard must not police normal churn, only catastrophe."""
    # Arrange
    src, snaps = rail
    _seed(src, 140)
    import_from_yaml(tasks_path=str(src))

    # Act
    result = CliRunner().invoke(db_group, ["snapshot", "--dir", str(snaps)])

    # Assert
    assert result.exit_code == 0, result.output


# EOF
