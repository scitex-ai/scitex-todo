#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A write must never MANUFACTURE a board at a path nobody named.

The 2026-07-20 chain, measured end to end: sac's `_card_exists` read
`FileNotFoundError` as "no such card yet" rather than "wrong path", called
`add_task`, and scitex-cards obligingly CREATED a store containing that one
card. Three cron jobs doing that grew a five-card document at an ambiently
resolved path; the hourly `db snapshot --refresh` imported it as canonical and
reconcile deleted the 2160 cards absent from it.

The conflation was sac's. The manufacturing was ours. These tests pin our half.
"""

from __future__ import annotations

import pytest

from scitex_cards._paths import ENV_TASKS, refuse_ambient_store_creation


def test_a_write_to_a_nonexistent_ambient_store_is_refused(tmp_path, monkeypatch):
    # ARRANGE — nothing names the store: no explicit arg, no env var.
    monkeypatch.delenv(ENV_TASKS, raising=False)
    absent = tmp_path / "never-created" / "tasks.yaml"

    # ACT / ASSERT — refusing is the whole point.
    with pytest.raises(RuntimeError) as excinfo:
        refuse_ambient_store_creation(absent)

    message = str(excinfo.value)
    assert "REFUSING to create a task store" in message
    # The error must be ACTIONABLE: name the path, and say what to do instead.
    assert str(absent) in message
    assert ENV_TASKS in message


def test_a_write_to_an_explicitly_named_nonexistent_store_is_allowed(
    tmp_path, monkeypatch
):
    # ARRANGE — the caller NAMED the destination; naming it is the opt-in.
    monkeypatch.delenv(ENV_TASKS, raising=False)
    absent = tmp_path / "deliberate" / "tasks.yaml"

    # ACT / ASSERT — must not raise; bootstraps and tests depend on this.
    refuse_ambient_store_creation(absent, explicit=absent)


def test_an_env_named_nonexistent_store_is_allowed(tmp_path, monkeypatch):
    # ARRANGE — an operator who exported the store variable has stated intent
    # just as clearly as one who passed the path.
    absent = tmp_path / "configured" / "tasks.yaml"
    monkeypatch.setenv(ENV_TASKS, str(absent))

    # ACT / ASSERT
    refuse_ambient_store_creation(absent)


def test_an_existing_ambient_store_is_untouched_by_the_guard(tmp_path, monkeypatch):
    # ARRANGE — the ordinary healthy case: the board already exists.
    monkeypatch.delenv(ENV_TASKS, raising=False)
    present = tmp_path / "tasks.yaml"
    present.write_text("tasks: []\n", encoding="utf-8")

    # ACT / ASSERT — the guard is about CREATION, never about writing.
    refuse_ambient_store_creation(present)


def test_add_task_does_not_manufacture_a_board_at_an_ambient_path(
    tmp_path, monkeypatch
):
    """The end-to-end shape that actually happened, as a regression pin.

    Asserts on the FILESYSTEM, not on "nothing was raised" — a probe that
    concludes from an absent exception reports success when it never ran.
    """
    # ARRANGE — point the ambient user root at an empty dir, name nothing.
    import scitex_cards

    monkeypatch.delenv(ENV_TASKS, raising=False)
    monkeypatch.setenv("SCITEX_DIR", str(tmp_path / "scitex"))
    would_be = tmp_path / "scitex" / "cards" / "tasks.yaml"

    # ACT
    with pytest.raises(RuntimeError):
        scitex_cards.add_task(
            id="decoy-card",
            title="written to a store that did not exist",
            assignee="scitex-cards",
            agent="scitex-cards",
        )

    # ASSERT — the artefact, not the exception: no board was invented.
    assert not would_be.exists()
