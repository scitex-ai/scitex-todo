#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The ``gui`` verb group — the operator's startup script calls it.

Regression cover for the 2026-07-12 incident: his `scitex_start_gui_servers`
loop runs ``<pkg> gui serve &`` across every SciTeX tool, and scitex-cards
exited with "No such command 'gui'", so nothing ever bound :8051 and his board
never came up. The board itself was fine — the VERB did not exist.

The contract these tests pin:
  * `gui serve` EXISTS and is the blocking/headless one (his script backgrounds
    it with `&`; a browser-opening serve would be wrong there)
  * `gui` fronts the SAME board lifecycle — one pidfile, not two racing ones
  * the bare noun hard-errors rather than guessing
"""

from click.testing import CliRunner

from scitex_cards._cli import main


def _run(*args):
    return CliRunner().invoke(main, list(args))


def test_gui_group_is_registered():
    """The whole incident in one assertion: `gui` must exist."""
    assert "gui" in main.commands


def test_gui_exposes_the_four_standard_verbs():
    assert sorted(main.commands["gui"].commands) == [
        "open",
        "serve",
        "status",
        "stop",
    ]


def test_gui_serve_runs():
    """`scitex-cards gui serve` — the exact call his startup loop makes."""
    assert _run("gui", "serve", "--dry-run").exit_code == 0


def test_gui_serve_defaults_to_port_8051():
    """His script expects 8051; `board` already defaults there. Don't drift."""
    assert "8051" in _run("gui", "serve", "--dry-run").output


def test_gui_serve_does_not_open_a_browser():
    """`serve` is headless by contract — `open` is the browser one."""
    assert "no browser" in _run("gui", "serve", "--dry-run").output


def test_gui_serve_accepts_a_host():
    assert _run("gui", "serve", "--host", "0.0.0.0", "--dry-run").exit_code == 0


def test_gui_serve_binds_loopback_by_default():
    """The board is UNAUTHENTICATED and serves every agent's cards."""
    assert "127.0.0.1" in _run("gui", "serve", "--dry-run").output


def test_bare_gui_noun_is_a_usage_error():
    """Noun-verb convention (operator directive TG 13316) — no guessing."""
    assert _run("gui").exit_code == 2


def test_bare_gui_noun_names_the_verbs():
    assert "gui serve" in _run("gui").output


def test_gui_status_shares_the_board_pidfile():
    """`gui` must FRONT the board lifecycle, never run a rival one.

    Two lifecycles racing for one port is the bug this aliasing avoids, so
    pin that the two verbs report from the same pidfile.
    """
    assert _run("gui", "status", "--json").output == (
        _run("board", "status", "--json").output
    )


def test_gui_stop_is_safe_when_nothing_runs():
    assert _run("gui", "stop", "--dry-run").exit_code == 0


def test_board_group_still_exists():
    """`gui` is an ALIAS, not a replacement — `board` stays canonical."""
    assert "board" in main.commands
