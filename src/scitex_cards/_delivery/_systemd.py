#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""systemd user-unit TEMPLATE + install helper for the notify daemon.

Host-enablement is OPERATOR-GATED: this module only WRITES the unit file to
``~/.config/systemd/user/`` and PRINTS the exact ``systemctl --user`` commands
for the operator to run. It NEVER invokes ``systemctl`` / enables / starts the
service itself — that is a deliberate human gate (mirrors the dashboard unit
convention: ``Type=simple`` + ``Restart=on-failure`` + ``WantedBy=default.
target``).

The unit is fully STANDALONE — its ``ExecStart`` is just the ``scitex-cards
notifyd`` entry point (the foreground run), with no external federation
dependency.
"""

from __future__ import annotations

import os
from pathlib import Path

#: The systemd user-unit filename. Package-prefixed so the operator can grep
#: ``systemctl --user list-units 'scitex-cards*'`` to see every owned unit.
UNIT_NAME = "scitex-cards-notifyd.service"

#: The unit-file TEMPLATE. ``Type=simple`` (long-running foreground process),
#: ``Restart=on-failure`` (a crashed daemon = silent comm-loss; bring it back),
#: ``WantedBy=default.target`` (start on user login) — mirrors the dashboard
#: unit's conventions. ``ExecStart`` is the standalone foreground entry point.
UNIT_TEMPLATE = """\
[Unit]
Description=scitex-cards notify daemon — standalone notification-delivery loop
Documentation=https://github.com/ywatanabe1989/scitex-todo
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={exec_start}
Restart=on-failure
RestartSec=5
TimeoutStartSec=30

[Install]
WantedBy=default.target
"""

#: Default ExecStart — the foreground notifyd run (what systemd supervises).
DEFAULT_EXEC_START = "scitex-cards notifyd"


def user_unit_dir() -> Path:
    """Resolve ``~/.config/systemd/user`` honouring ``$XDG_CONFIG_HOME``.

    Tests point ``$XDG_CONFIG_HOME`` at a tmp dir to assert the helper writes
    there (and does NOT shell out to systemctl).
    """
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "systemd" / "user"


def unit_path() -> Path:
    """Full path to the installed unit file."""
    return user_unit_dir() / UNIT_NAME


def render_unit(exec_start: str = DEFAULT_EXEC_START) -> str:
    """Render the unit-file text for the given ``ExecStart``."""
    return UNIT_TEMPLATE.format(exec_start=exec_start)


def enable_commands() -> str:
    """The exact systemctl commands the OPERATOR runs to enable + start it."""
    return (
        "systemctl --user daemon-reload && "
        f"systemctl --user enable --now {UNIT_NAME}"
    )


def install_unit(
    *,
    exec_start: str = DEFAULT_EXEC_START,
    force: bool = False,
) -> dict:
    """Write the unit file to the user-unit dir. Does NOT run systemctl.

    Parameters
    ----------
    exec_start : str
        The ``ExecStart=`` line body (default: ``scitex-cards notifyd``).
    force : bool
        Overwrite an existing unit file. Without it, an existing file is left
        untouched and the result reports ``written=False``.

    Returns
    -------
    dict
        ``{path, written, existed, enable_commands}`` — caller prints the
        commands for the operator to run by hand.
    """
    path = unit_path()
    existed = path.exists()
    if existed and not force:
        return {
            "path": str(path),
            "written": False,
            "existed": True,
            "enable_commands": enable_commands(),
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_unit(exec_start), encoding="utf-8")
    return {
        "path": str(path),
        "written": True,
        "existed": existed,
        "enable_commands": enable_commands(),
    }


__all__ = [
    "DEFAULT_EXEC_START",
    "UNIT_NAME",
    "UNIT_TEMPLATE",
    "enable_commands",
    "install_unit",
    "render_unit",
    "unit_path",
    "user_unit_dir",
]

# EOF
