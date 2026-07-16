#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the bundled PreToolUse hook that redirects Claude's built-in
``TaskCreate`` / ``TaskUpdate`` / ``TaskList`` tools to scitex-todo
(op-12038 single-shared-store doctrine).

No mocks (STX-NM / PA-306): invoke the real script under bash via
``subprocess.run`` with a JSON event on stdin, exactly as Claude Code
would.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import scitex_cards

HOOK_PATH = (
    Path(scitex_cards.__file__).parent
    / "_skills"
    / "scitex-todo"
    / "hooks"
    / "pre-tool-use"
    / "redirect_claude_tasklist_to_scitex_cards.sh"
)


def _run(payload: str, env_extra: dict[str, str] | None = None):
    """Run the hook with ``payload`` on stdin and return CompletedProcess."""
    env = dict(os.environ)
    # Default-clear the opt-out so tests are deterministic; specific cases
    # set it explicitly via env_extra.
    env.pop("CC_ALLOW_CLAUDE_TASKLIST", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(HOOK_PATH)],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
        timeout=5,
    )


def test_hook_script_exists_at_bundled_path():
    # Arrange
    # Act
    exists = HOOK_PATH.is_file()
    # Assert
    assert exists, f"hook script missing at {HOOK_PATH}"


def test_taskcreate_is_blocked_with_nonzero_exit():
    # Arrange
    payload = '{"tool_name": "TaskCreate"}'
    # Act
    result = _run(payload)
    # Assert
    assert result.returncode != 0


def test_taskupdate_is_blocked_with_nonzero_exit():
    # Arrange
    payload = '{"tool_name": "TaskUpdate"}'
    # Act
    result = _run(payload)
    # Assert
    assert result.returncode != 0


def test_tasklist_is_blocked_with_nonzero_exit():
    # Arrange
    payload = '{"tool_name": "TaskList"}'
    # Act
    result = _run(payload)
    # Assert
    assert result.returncode != 0


def test_read_tool_passes_through_with_zero_exit():
    # Arrange
    payload = '{"tool_name": "Read"}'
    # Act
    result = _run(payload)
    # Assert
    assert result.returncode == 0


def test_bash_tool_passes_through_with_zero_exit():
    # Arrange
    payload = '{"tool_name": "Bash"}'
    # Act
    result = _run(payload)
    # Assert
    assert result.returncode == 0


def test_block_stderr_names_scitex_cards_so_operator_can_confirm_copy():
    # Arrange
    payload = '{"tool_name": "TaskCreate"}'
    # Act
    result = _run(payload)
    # Assert
    assert "scitex-todo" in result.stderr


def test_opt_out_env_var_lets_taskcreate_through():
    # Arrange
    payload = '{"tool_name": "TaskCreate"}'
    # Act
    result = _run(payload, env_extra={"CC_ALLOW_CLAUDE_TASKLIST": "1"})
    # Assert
    assert result.returncode == 0
