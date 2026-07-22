#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A correct install can be bypassed entirely. These tests prove we notice.

Regression cover for the 2026-07-22 incident, reported by the ``dotfiles``
agent. That container had scitex-cards 0.17.5 correctly installed — and
``bin/scitex-cards`` was a 224-byte shim importing ``scitex_todo._cli`` from the
superseded 0.13.5 distribution, because BOTH distributions declare console
scripts of the same names and the last install owns the name.

0.13.5 predates the SQLite store, so it ignored ``SCITEX_CARDS_STORE_BACKEND``
and fell through YAML precedence to the BUNDLED EXAMPLE inside site-packages:
that agent read 17 fixture rows where the board held 2,308, and its writes
landed in a package file nothing reads and any reinstall erases.

WHY A SEPARATE CHECK, next to ``install_honest`` rather than folded into it:
every version probe was RIGHT. The distribution really was current, the
metadata really did describe the code beside it. The only wrong thing was which
module the SCRIPT reached for, and no version string can express that.
"""

from __future__ import annotations

from scitex_cards._install_probe import check_console_scripts_not_shadowed

#: The shape of a healthy console script (308 bytes as installed).
GOOD_SCRIPT = """#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
from scitex_cards._cli import main
if __name__ == "__main__":
    sys.exit(main())
"""

#: The shape of the shadowed one (224 bytes, as found on dotfiles' agent).
SHADOWED_SCRIPT = """#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys
from scitex_todo._cli import main
if __name__ == "__main__":
    sys.exit(main())
"""


def _install_fake_script(tmp_path, monkeypatch, name, body):
    """Put an executable ``name`` carrying ``body`` alone on PATH."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / name
    script.write_text(body, encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    return script


def test_detects_the_shadowed_console_script(tmp_path, monkeypatch):
    """The incident itself: the script on PATH runs the superseded package."""
    # Arrange
    _install_fake_script(tmp_path, monkeypatch, "scitex-cards", SHADOWED_SCRIPT)
    # Act
    result = check_console_scripts_not_shadowed()
    # Assert
    assert result["ok"] is False
    assert result["hint"]


def test_the_failure_names_the_import_target_it_found(tmp_path, monkeypatch):
    """A verdict is not enough — the report must name the thing.

    The incident survived every version check precisely because those answered
    with a healthy-looking summary. If this check said only "not ok", the
    reader would be back to guessing.
    """
    # Arrange
    _install_fake_script(tmp_path, monkeypatch, "scitex-cards", SHADOWED_SCRIPT)
    # Act
    detail = check_console_scripts_not_shadowed()["detail"]
    # Assert
    assert "scitex_todo" in detail, "the offending import target is not named"
    assert "scitex-cards" in detail, "the offending script is not named"


def test_accepts_a_correct_console_script(tmp_path, monkeypatch):
    """A healthy script must not be flagged — a check that always fails is noise."""
    # Arrange
    _install_fake_script(tmp_path, monkeypatch, "scitex-cards", GOOD_SCRIPT)
    # Act
    result = check_console_scripts_not_shadowed()
    # Assert
    assert result["ok"] is True
    assert "scitex_cards" in result["detail"]


def test_no_script_on_path_is_not_a_failure(tmp_path, monkeypatch):
    """Library/MCP-only installs have no script at all; absent is not damage."""
    # Arrange
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("PATH", str(empty))
    # Act
    result = check_console_scripts_not_shadowed()
    # Assert
    assert result["ok"] is True
    assert "nothing to shadow" in result["detail"]


def test_an_unreadable_script_is_reported_not_passed(tmp_path, monkeypatch):
    """A script whose target cannot be read is an UNANSWERED question.

    Answering it "healthy" is how a check quietly stops checking — the same
    shape as the version probes that vouched for the shimmed install.
    """
    # Arrange
    _install_fake_script(
        tmp_path, monkeypatch, "scitex-cards", "#!/bin/sh\nexec something-else\n"
    )
    # Act
    result = check_console_scripts_not_shadowed()
    # Assert
    assert result["ok"] is False
    assert "undetermined" in result["detail"]


def test_the_compat_alias_is_checked_too(tmp_path, monkeypatch):
    """``scitex-todo`` is ours as well; a shadowed alias is the same incident."""
    # Arrange
    _install_fake_script(tmp_path, monkeypatch, "scitex-todo", SHADOWED_SCRIPT)
    # Act
    result = check_console_scripts_not_shadowed()
    # Assert
    assert result["ok"] is False
    assert "scitex-todo" in result["detail"]


def test_a_shadowed_alias_is_caught_beside_a_healthy_primary(tmp_path, monkeypatch):
    """Half-shadowed is the realistic state, and the worst to eyeball.

    Checking `scitex-cards` by hand and finding it correct proves nothing about
    the alias, which routes to the same CLI and is what much of the fleet's
    muscle memory still types.
    """
    # Arrange
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name, body in (
        ("scitex-cards", GOOD_SCRIPT),
        ("scitex-todo", SHADOWED_SCRIPT),
    ):
        script = bin_dir / name
        script.write_text(body, encoding="utf-8")
        script.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir))
    # Act
    result = check_console_scripts_not_shadowed()
    # Assert
    assert result["ok"] is False
    assert "scitex-todo" in result["detail"]


# EOF
