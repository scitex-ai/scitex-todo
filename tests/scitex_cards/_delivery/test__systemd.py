#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the systemd unit TEMPLATE + operator-gated install helper (slice 2).

The install helper must WRITE the unit file to ``$XDG_CONFIG_HOME/systemd/user``
and NEVER invoke systemctl (host-enablement is operator-gated). We assert by
pointing ``$XDG_CONFIG_HOME`` at a tmp dir + verifying the file content, and by
confirming no ``systemctl`` subprocess is ever spawned (a real
``subprocess.run`` spy installed as a sentinel, NOT a mock of our code under
test — the helper simply has no subprocess call to intercept).

REGRESSION (203/EXEC): the shipped template used a BARE
``ExecStart=scitex-cards notifyd``. systemd does not use the user's login PATH,
and the console script lives in a venv, so the unit died at ``status=203/EXEC``
and had to be hand-patched before it would start. ExecStart must be an
ABSOLUTE, EXISTING, EXECUTABLE path — asserted through the MECHANISM (resolve
from the running interpreter / $PATH), never against a hard-coded machine path.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from scitex_cards._delivery import _systemd


def _exec_start_line(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("ExecStart="):
            return line[len("ExecStart=") :]
    raise AssertionError(f"no ExecStart= line in unit:\n{text}")


def test_render_unit_has_required_conventions():
    text = _systemd.render_unit()
    assert "Type=simple" in text
    assert "Restart=on-failure" in text
    assert "WantedBy=default.target" in text
    assert _exec_start_line(text).endswith(" notifyd")


# --------------------------------------------------------------------------- #
# 203/EXEC regression — ExecStart must be an ABSOLUTE, EXISTING path          #
# --------------------------------------------------------------------------- #
def test_exec_start_is_absolute_existing_and_executable():
    """The exact defect: a bare command systemd cannot find on its empty PATH."""
    exec_start = _systemd.resolve_exec_start()
    program = exec_start.split()[0]
    path = Path(program)

    assert path.is_absolute(), f"ExecStart program {program!r} is not absolute"
    assert path.is_file(), f"ExecStart program {program!r} does not exist"
    assert os.access(path, os.X_OK), f"ExecStart program {program!r} not executable"
    # ...and it is the console script running the notifyd verb.
    assert path.name == "scitex-cards"
    assert exec_start.split()[1:] == ["notifyd"]


def test_rendered_unit_exec_start_is_absolute():
    """The absoluteness must survive into the FILE, not just the helper."""
    program = _exec_start_line(_systemd.render_unit()).split()[0]
    assert Path(program).is_absolute()
    assert Path(program).is_file()


def test_console_script_prefers_the_running_interpreters_bin_dir():
    """A venv install must point the unit at THAT venv, not at some other PATH hit."""
    candidate = Path(sys.executable).parent / "scitex-cards"
    if not (candidate.is_file() and os.access(candidate, os.X_OK)):
        pytest.skip("no console script beside this interpreter to prefer")
    assert _systemd.console_script_path() == candidate


def test_unresolvable_console_script_fails_loudly(tmp_path, monkeypatch):
    """Never write a unit that is GUARANTEED to fail at 203/EXEC — raise instead."""
    # A real (empty) bin dir for a real interpreter path: nothing to find here...
    empty_bin = tmp_path / "empty-venv" / "bin"
    empty_bin.mkdir(parents=True)
    monkeypatch.setattr(sys, "executable", str(empty_bin / "python"))
    # ...and an empty PATH, so the $PATH fallback finds nothing either.
    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))

    with pytest.raises(_systemd.ExecStartUnresolved) as excinfo:
        _systemd.resolve_exec_start()
    # The message must be ACTIONABLE (names the problem and the remedy).
    assert "ABSOLUTE ExecStart" in str(excinfo.value)
    assert "pip install" in str(excinfo.value)

    # And the failure ABORTS the install: no half-written, unstartable unit.
    with pytest.raises(_systemd.ExecStartUnresolved):
        _systemd.install_unit()
    assert not _systemd.unit_path().exists()


# --------------------------------------------------------------------------- #
# install helper — operator-gated, never shells out                          #
# --------------------------------------------------------------------------- #
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

    target = tmp_path / "cfg" / "systemd" / "user" / "scitex-cards-notifyd.service"
    assert result["written"] is True
    assert result["path"] == str(target)
    assert target.exists()
    body = target.read_text(encoding="utf-8")
    assert f"ExecStart={result['exec_start']}" in body
    assert Path(result["exec_start"].split()[0]).is_absolute()
    assert "Restart=on-failure" in body
    # The enable commands are RETURNED for the operator, not executed.
    assert "systemctl --user daemon-reload" in result["enable_commands"]
    assert "enable --now scitex-cards-notifyd.service" in result["enable_commands"]
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
    body = target.read_text(encoding="utf-8")
    assert _exec_start_line(body) == result["exec_start"]
    assert Path(_exec_start_line(body).split()[0]).is_file()


def test_explicit_exec_start_is_honoured(tmp_path, monkeypatch):
    """An explicit override still works (the operator stays in control)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    result = _systemd.install_unit(exec_start="/opt/venv/bin/scitex-cards notifyd")
    assert result["exec_start"] == "/opt/venv/bin/scitex-cards notifyd"
    assert (
        "ExecStart=/opt/venv/bin/scitex-cards notifyd"
        in Path(result["path"]).read_text(encoding="utf-8")
    )


# EOF
