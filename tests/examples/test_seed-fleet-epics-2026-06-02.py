#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test that ``examples/seed-fleet-epics-2026-06-02.sh`` runs end-to-end.

Real subprocess invocation against a tmp_path store — no mocks
(STX-NM). Verifies the 8 fleet epics (E1–E8) land with the expected
scope / assignee / status distribution after the script completes.

Skips cleanly when the Phase-1 write surface isn't installed on the
PATH (the script depends on `scitex-todo add`, which is Phase-1 only —
the test is the canonical integration check between the script and the
CLI).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "examples"
    / "seed-fleet-epics-2026-06-02.sh"
)

# Set once at import; pytest will skip every test in this module if the
# binary isn't available (e.g. when running the test suite on a develop
# checkout that doesn't yet have PR #14's `scitex-todo add` verb merged).
_CLI = shutil.which("scitex-todo")
_HAS_ADD = False
if _CLI:
    # Probe `scitex-todo add --help` to distinguish a Phase-0 CLI (no add
    # verb yet) from the Phase-1 CLI that supports it. Both exit 0 on
    # --help; the Phase-0 CLI would emit "No such command 'add'".
    _probe = subprocess.run(
        [_CLI, "add", "--help"],
        capture_output=True,
        text=True,
    )
    _HAS_ADD = (
        _probe.returncode == 0
        and "Append a new task" in (_probe.stdout + _probe.stderr)
    )

pytestmark = pytest.mark.skipif(
    not _HAS_ADD,
    reason="scitex-todo `add` verb not on PATH (Phase-1 not installed)",
)


@pytest.fixture
def seeded_store(tmp_path):
    """Run the seed script against a fresh tmp store; return the store path."""
    store = tmp_path / "tasks.yaml"
    # The script uses $SCITEX_TODO to find the binary and the precedence
    # chain via $SCITEX_TODO_TASKS. We force both so the test is hermetic.
    env = os.environ.copy()
    env["SCITEX_TODO"] = _CLI
    env["SCITEX_TODO_TASKS"] = str(store)
    # `scitex-todo init --shared` writes to $SCITEX_DIR/todo/tasks.yaml,
    # NOT to $SCITEX_TODO_TASKS — so we redirect $SCITEX_DIR to tmp_path
    # to keep `init` hermetic too (it doesn't touch the operator's real
    # ~/.scitex/todo on this host).
    env["SCITEX_DIR"] = str(tmp_path)
    # Drop scope/agent envs the host may have set, otherwise the script's
    # output would be filtered.
    env.pop("SCITEX_TODO_SCOPE", None)
    env.pop("SCITEX_TODO_AGENT", None)

    # Act
    result = subprocess.run(
        ["bash", str(_SCRIPT)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    # Assert (the fixture itself, so failure modes surface as fixture errors)
    assert result.returncode == 0, (
        f"seed script exited {result.returncode}\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    return store


def test_seed_script_exits_zero(seeded_store):
    # Fixture already asserted exit code; this test documents the contract.
    assert seeded_store.exists()


def test_seed_script_creates_eight_epic_ids(seeded_store):
    # Arrange
    with seeded_store.open() as handle:
        tasks = yaml.safe_load(handle)["tasks"]
    # Act
    ids = {t["id"] for t in tasks}
    # Assert
    expected = {
        "e1-sac-acl",
        "e2-sac-locator",
        "e3-sac-agents-migrate",
        "e4-hub-nas-standup",
        "e5-orochi-mba-arm64",
        "e6-staging-env",
        "e7-sac-writable-creds",
        "e8-scitex-bugfixes",
    }
    assert ids == expected


def test_seed_script_sets_expected_scopes(seeded_store):
    # Arrange — the per-epic scope distribution the script promises in
    # its leading comment-block: 4×sac, 1×hub, 1×orochi, 2×fleet.
    with seeded_store.open() as handle:
        tasks = yaml.safe_load(handle)["tasks"]
    # Act
    by_scope: dict[str, int] = {}
    for task in tasks:
        scope = task.get("scope") or ""
        by_scope[scope] = by_scope.get(scope, 0) + 1
    # Assert
    assert by_scope == {
        "project:sac": 4,
        "project:hub": 1,
        "project:orochi": 1,
        "project:fleet": 2,
    }


def test_seed_script_pre_claims_e1_to_agent_container(seeded_store):
    # Arrange — E1 is the only pre-claimed epic per the lead's brief
    # (assignee = agent:proj-scitex-agent-container); the other seven
    # are claimable.
    with seeded_store.open() as handle:
        tasks = yaml.safe_load(handle)["tasks"]
    by_id = {t["id"]: t for t in tasks}
    # Assert E1 is pre-claimed
    assert by_id["e1-sac-acl"].get("assignee") == "agent:proj-scitex-agent-container"
    # Assert every other epic is claimable (no assignee)
    for eid in (
        "e2-sac-locator",
        "e3-sac-agents-migrate",
        "e4-hub-nas-standup",
        "e5-orochi-mba-arm64",
        "e6-staging-env",
        "e7-sac-writable-creds",
        "e8-scitex-bugfixes",
    ):
        assert "assignee" not in by_id[eid], (
            f"{eid!r} should be claimable but has assignee="
            f"{by_id[eid].get('assignee')!r}"
        )


def test_seed_script_all_epics_start_pending(seeded_store):
    # Arrange
    with seeded_store.open() as handle:
        tasks = yaml.safe_load(handle)["tasks"]
    # Assert
    assert all(t.get("status") == "pending" for t in tasks)


def test_seed_script_priorities_monotonic_10_to_80(seeded_store):
    # Arrange — the script assigns 10/20/.../80 so the operator has
    # room to drag-reorder on the board.
    with seeded_store.open() as handle:
        tasks = yaml.safe_load(handle)["tasks"]
    # Act
    priorities = [t.get("priority") for t in tasks]
    # Assert
    assert priorities == [10, 20, 30, 40, 50, 60, 70, 80]


def test_seed_script_every_epic_has_note(seeded_store):
    # Arrange — the operator-overnight brief lives in `note`, so every
    # claiming agent has the context without a separate handoff.
    with seeded_store.open() as handle:
        tasks = yaml.safe_load(handle)["tasks"]
    # Assert
    for task in tasks:
        assert task.get("note"), f"{task['id']!r} missing required `note`"


# EOF
