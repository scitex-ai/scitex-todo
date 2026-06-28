#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the `scitex-todo mcp channel` verb registration.

CliRunner against the real click group (no mocks — STX-NM / PA-306). Verifies
the verb is wired onto the `mcp` group and that its `--help` advertises the
standalone channel server. Also pins the fail-loud agent-id behavior via the
CLI surface.
"""

from __future__ import annotations

from click.testing import CliRunner

from scitex_todo._cli import main


def test_mcp_channel_verb_is_registered():
    runner = CliRunner()
    result = runner.invoke(main, ["mcp", "--help"])
    assert result.exit_code == 0, result.output
    assert "channel" in result.output


def test_mcp_channel_help_describes_standalone_server():
    runner = CliRunner()
    result = runner.invoke(main, ["mcp", "channel", "--help"])
    assert result.exit_code == 0, result.output
    assert "--name" in result.output
    assert "--interval" in result.output


def test_mcp_channel_unresolved_agent_fails_loud(monkeypatch):
    # No agent id in the env and none passed → fail loud (non-zero exit) with
    # an actionable hint, rather than draining an "unknown" inbox.
    monkeypatch.delenv("SCITEX_TODO_AGENT", raising=False)
    runner = CliRunner()
    result = runner.invoke(main, ["mcp", "channel", "--interval", "0.01"])
    assert result.exit_code != 0
    assert "SCITEX_TODO_AGENT" in result.output


# EOF
