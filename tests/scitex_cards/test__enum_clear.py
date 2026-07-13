#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Clearing a CLOSED-ENUM field with `""` deletes the key (does not write "").

The live failure this pins (4-card triage run, 2026-07-12): every write
surface documents *"pass an empty string to CLEAR a field"*, but on the
closed-enum fields a `""` was written LITERALLY and the validator then
rejected the save::

    TaskValidationError: task '...' has invalid blocker '';
    must be one of ('compute','dependency','dep','operator-decision',
    'agent-wait','none') or absent

— so the DOCUMENTED way to clear a blocker was the one way that could not
work, and it failed at SAVE time, aborting the whole bulk batch having
applied nothing.

Real round-trips against a `tmp_path` YAML store — no mocks of the thing
under test (Req STX-NM).
"""

from __future__ import annotations

import pytest
import yaml
from click.testing import CliRunner

from scitex_cards import _model, _store
from scitex_cards._cli import main


def _blocked_card(store, *, task_id="triage-1", blocker="operator-decision"):
    """Insert a real blocked card carrying a blocker."""
    return _store.add_task(
        store,
        id=task_id,
        title="A blocked card",
        status="blocked",
        blocker=blocker,
        assignee="agent:test-suite",
    )


def _reload(store, task_id):
    """Re-read the task FROM DISK — the key question is what got persisted."""
    tasks = _model.load_tasks(store)
    return next(t for t in tasks if t["id"] == task_id)


# --------------------------------------------------------------------------- #
# blocker: "" CLEARS (the reported bug)                                       #
# --------------------------------------------------------------------------- #
def test_blocker_empty_string_deletes_the_key_and_the_save_succeeds(tmp_path):
    # Arrange — a blocked card that names its gate.
    store = tmp_path / "tasks.yaml"
    _blocked_card(store)
    assert _reload(store, "triage-1")["blocker"] == "operator-decision"

    # Act — the DOCUMENTED clear. Must not raise (it used to, at save time).
    merged = _store.update_task(
        store, "triage-1", status="in_progress", blocker=""
    )

    # Assert — the key is ABSENT, not "" and not the "none" workaround.
    assert "blocker" not in merged
    persisted = _reload(store, "triage-1")
    assert "blocker" not in persisted
    assert persisted["status"] == "in_progress"


def test_blocker_empty_string_never_reaches_disk_as_a_value(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _blocked_card(store, task_id="triage-raw")

    # Act
    _store.update_task(store, "triage-raw", status="deferred", blocker="")

    # Assert — read the RAW YAML: no `blocker: ''` key survived the write.
    raw = yaml.safe_load(store.read_text())
    card = next(t for t in raw["tasks"] if t["id"] == "triage-raw")
    assert "blocker" not in card


def test_blocker_whitespace_only_is_also_a_clear(tmp_path):
    # Arrange — "  " is a typo'd "", never a legal enum member.
    store = tmp_path / "tasks.yaml"
    _blocked_card(store, task_id="triage-ws")

    # Act
    merged = _store.update_task(
        store, "triage-ws", status="in_progress", blocker="   "
    )

    # Assert
    assert "blocker" not in merged


# --------------------------------------------------------------------------- #
# The validator is NOT weakened                                               #
# --------------------------------------------------------------------------- #
def test_genuinely_invalid_blocker_still_raises(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _blocked_card(store, task_id="triage-bad")

    # Act / Assert — "" is a delete instruction; "banana" is a bad VALUE.
    with pytest.raises(_model.TaskValidationError) as exc:
        _store.update_task(store, "triage-bad", blocker="banana")
    assert "banana" in str(exc.value)

    # The store is untouched — the rejected write did not half-apply.
    assert _reload(store, "triage-bad")["blocker"] == "operator-decision"


# --------------------------------------------------------------------------- #
# The done-while-blocked guard still fires (regression: do NOT weaken)        #
# --------------------------------------------------------------------------- #
def test_done_while_blocker_still_set_is_still_refused(tmp_path):
    # Arrange — a blocked card with a real blocker.
    store = tmp_path / "tasks.yaml"
    _blocked_card(store, task_id="triage-done")

    # Act / Assert — flipping to done WITHOUT clearing the blocker is
    # incoherent (done-but-blocked) and must still be rejected.
    with pytest.raises(_model.TaskValidationError) as exc:
        _store.update_task(store, "triage-done", status="done")
    assert "blocker" in str(exc.value)

    # And the coherent form — clear the gate in the SAME call — works.
    merged = _store.update_task(
        store, "triage-done", status="done", blocker=""
    )
    assert merged["status"] == "done"
    assert "blocker" not in merged


# --------------------------------------------------------------------------- #
# status is NOT clearable — a card must carry a decision                      #
# --------------------------------------------------------------------------- #
def test_status_cannot_be_cleared_and_says_why(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _blocked_card(store, task_id="triage-status")

    # Act / Assert — refused LOUDLY, not silently ignored, and the message
    # names the reason + the valid set the caller should pick from.
    with pytest.raises(_model.TaskValidationError) as exc:
        _store.update_task(store, "triage-status", status="")
    msg = str(exc.value)
    assert "cannot clear" in msg
    assert "in_progress" in msg and "deferred" in msg

    # The card is untouched — its status survives the refusal.
    assert _reload(store, "triage-status")["status"] == "blocked"


def test_status_clear_is_refused_before_the_store_is_touched(tmp_path):
    # Arrange — the refusal must happen BEFORE the lock/write, so a doomed
    # mutation never partially applies alongside its sibling fields.
    store = tmp_path / "tasks.yaml"
    _blocked_card(store, task_id="triage-early")

    # Act / Assert
    with pytest.raises(_model.TaskValidationError):
        _store.update_task(store, "triage-early", status="", note="new note")

    # The sibling field did NOT land.
    assert "note" not in _reload(store, "triage-early")


# --------------------------------------------------------------------------- #
# kind: "" CLEARS (absent kind == the "task" default)                         #
# --------------------------------------------------------------------------- #
def test_kind_empty_string_deletes_the_key(tmp_path):
    # Arrange — a card mis-filed as kind=status.
    store = tmp_path / "tasks.yaml"
    _store.add_task(
        store,
        id="kind-1",
        title="Mis-filed card",
        status="in_progress",
        kind="status",
        assignee="agent:test-suite",
    )

    # Act — put it back to the default (absent kind == "task").
    merged = _store.update_task(store, "kind-1", kind="")

    # Assert
    assert "kind" not in merged
    assert "kind" not in _reload(store, "kind-1")


# --------------------------------------------------------------------------- #
# add_task (the sibling write path) honours the same ONE rule                 #
# --------------------------------------------------------------------------- #
def test_add_task_empty_enum_is_not_written(tmp_path):
    # Arrange / Act — "" on insert = "no value", never a literal "".
    store = tmp_path / "tasks.yaml"
    inserted = _store.add_task(
        store,
        id="add-1",
        title="Fresh card",
        status="deferred",
        blocker="",
        kind="",
        assignee="agent:test-suite",
    )

    # Assert
    assert "blocker" not in inserted and "kind" not in inserted


def test_add_task_refuses_a_status_less_card(tmp_path):
    # Arrange / Act / Assert — a card cannot be BORN status-less either.
    store = tmp_path / "tasks.yaml"
    with pytest.raises(_model.TaskValidationError):
        _store.add_task(
            store,
            id="add-2",
            title="Statusless",
            status="",
            assignee="agent:test-suite",
        )


# --------------------------------------------------------------------------- #
# The batch case that broke live                                              #
# --------------------------------------------------------------------------- #
def test_bulk_triage_with_a_blocker_clear_does_not_abort_the_batch(tmp_path):
    # Arrange — the shape of the live 4-card triage script: a run of updates
    # where ONE clears a blocker. That one used to raise at save time and
    # abort the whole batch, applying NOTHING.
    store = tmp_path / "tasks.yaml"
    for n in range(4):
        _blocked_card(store, task_id=f"bulk-{n}")

    # Act — the clear sits in the MIDDLE of the batch.
    _store.update_task(store, "bulk-0", status="in_progress", blocker="")
    _store.update_task(store, "bulk-1", status="deferred", blocker="")
    _store.update_task(store, "bulk-2", blocker="agent-wait")
    _store.update_task(store, "bulk-3", status="done", blocker="")

    # Assert — every card in the batch landed.
    assert "blocker" not in _reload(store, "bulk-0")
    assert _reload(store, "bulk-0")["status"] == "in_progress"
    assert "blocker" not in _reload(store, "bulk-1")
    assert _reload(store, "bulk-2")["blocker"] == "agent-wait"
    assert _reload(store, "bulk-3")["status"] == "done"
    assert "blocker" not in _reload(store, "bulk-3")


# --------------------------------------------------------------------------- #
# End-to-end at the CLI surface (the contract is documented THERE too)        #
# --------------------------------------------------------------------------- #
def test_cli_update_blocker_empty_string_clears(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _blocked_card(store, task_id="cli-1")

    # Act
    result = CliRunner().invoke(
        main,
        [
            "update", "cli-1",
            "--status", "in_progress",
            "--blocker", "",
            "--tasks", str(store),
        ],
    )

    # Assert
    assert result.exit_code == 0, result.output
    assert "blocker" not in _reload(store, "cli-1")


def test_cli_update_kind_empty_string_clears(tmp_path):
    # Arrange
    store = tmp_path / "tasks.yaml"
    _store.add_task(
        store,
        id="cli-2",
        title="Mis-filed",
        status="in_progress",
        kind="status",
        assignee="agent:test-suite",
    )

    # Act — previously the strict Choice rejected '' at parse time, so
    # there was no CLI form for this at all.
    result = CliRunner().invoke(
        main,
        ["update", "cli-2", "--kind", "", "--tasks", str(store)],
    )

    # Assert
    assert result.exit_code == 0, result.output
    assert "kind" not in _reload(store, "cli-2")


# EOF
