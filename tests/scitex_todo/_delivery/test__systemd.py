#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the systemd unit TEMPLATE + operator-gated install helper (slice 2).

The install helper must WRITE the unit file to ``$XDG_CONFIG_HOME/systemd/user``
and NEVER invoke systemctl (host-enablement is operator-gated). We assert by
pointing ``$XDG_CONFIG_HOME`` at a tmp dir + verifying the file content, and by
confirming no ``systemctl`` subprocess is ever spawned (a real
``subprocess.run`` spy installed as a sentinel, NOT a mock of our code under
test — the helper simply has no subprocess call to intercept).
"""

from __future__ import annotations

import subprocess

from scitex_todo._delivery import _systemd


def test_render_unit_has_required_conventions():
    text = _systemd.render_unit()
    assert "Type=simple" in text
    assert "ExecStart=scitex-todo notifyd" in text
    assert "Restart=on-failure" in text
    assert "WantedBy=default.target" in text


def test_install_unit_writes_to_xdg_config_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    # Spy on subprocess.run so we PROVE no systemctl is ever shelled out.
    calls: list = []
    real_run = subprocess.run

    def _spy_run(*args, **kwargs):
        calls.append((args, kwargs))
        return real_run(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _spy_run)

    result = _systemd.install_unit()

    target = tmp_path / "cfg" / "systemd" / "user" / "scitex-todo-notifyd.service"
    assert result["written"] is True
    assert result["path"] == str(target)
    assert target.exists()
    body = target.read_text(encoding="utf-8")
    assert "ExecStart=scitex-todo notifyd" in body
    assert "Restart=on-failure" in body
    # The enable commands are RETURNED for the operator, not executed.
    assert "systemctl --user daemon-reload" in result["enable_commands"]
    assert "enable --now scitex-todo-notifyd.service" in result["enable_commands"]
    # NO systemctl subprocess was spawned.
    assert calls == []


def test_install_unit_does_not_overwrite_without_force(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    first = _systemd.install_unit()
    assert first["written"] is True

    target = _systemd.unit_path()
    target.write_text("MANUALLY EDITED — keep me\n", encoding="utf-8")

    second = _systemd.install_unit()  # no force
    assert second["written"] is False
    assert second["existed"] is True
    # The hand-edited content survived (we did not clobber it).
    assert "MANUALLY EDITED" in target.read_text(encoding="utf-8")


def test_install_unit_force_overwrites(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    _systemd.install_unit()
    target = _systemd.unit_path()
    target.write_text("stale\n", encoding="utf-8")

    result = _systemd.install_unit(force=True)
    assert result["written"] is True
    assert "ExecStart=scitex-todo notifyd" in target.read_text(encoding="utf-8")


# EOF
