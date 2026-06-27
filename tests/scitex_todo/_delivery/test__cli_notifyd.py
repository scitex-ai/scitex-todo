#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the ``scitex-todo notifyd`` CLI verb (slice 2).

Uses click's ``CliRunner`` against the real root group — no mocks. Covers:
* ``notifyd --once`` runs a single real delivery pass and exits (no daemon).
* ``notifyd install-unit`` writes the unit to a tmp ``$XDG_CONFIG_HOME`` and
  prints the operator-gated enable commands WITHOUT running systemctl.
"""

from __future__ import annotations

import subprocess

import yaml
from click.testing import CliRunner

from scitex_todo._cli._main import main
from scitex_todo._inbox import enqueue


def _seed(store, recipient="u_cli"):
    enqueue(
        recipient,
        event_type="reassigned",
        card_id="c1",
        body="hi",
        actor="a",
        ts="2026-06-27T10:00:00Z",
        store=store,
    )


def test_notifyd_once_runs_single_pass(tmp_path):
    store = tmp_path / "tasks.yaml"
    _seed(store)
    (tmp_path / "recipients.yaml").write_text(
        yaml.safe_dump({"users": {"u_cli": {"channels": [{"kind": "log"}]}}}),
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(
        main,
        ["notifyd", "--tasks", str(store), "--once"],
    )
    assert result.exit_code == 0, result.output
    assert "notifyd --once" in result.output
    assert "sent=1" in result.output


def test_notifyd_install_unit_writes_and_no_systemctl(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    calls: list = []
    real_run = subprocess.run
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: (calls.append((a, k)), real_run(*a, **k))[1],
    )

    runner = CliRunner()
    result = runner.invoke(main, ["notifyd", "install-unit"])
    assert result.exit_code == 0, result.output

    target = tmp_path / "cfg" / "systemd" / "user" / "scitex-todo-notifyd.service"
    assert target.exists()
    assert "wrote systemd user unit" in result.output
    assert "systemctl --user daemon-reload" in result.output
    # The tool printed the commands but never SHELLED OUT to systemctl.
    assert calls == []


# EOF
