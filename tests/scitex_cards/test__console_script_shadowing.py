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

from scitex_cards._console_script_probe import check_console_scripts_not_shadowed

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


# --------------------------------------------------------------------------- #
# The check must not narrow its own scope in silence.                          #
# --------------------------------------------------------------------------- #
#
# Both tests below cover a defect I shipped in the first draft, found by the
# `dotfiles` agent noticing the SAME defect in their own divergence checker:
# it skipped every file with no counterpart on the live surface, then printed
# a confident total that read as complete coverage — 1,145 files across 24
# hooks uncounted and unmentioned. Their words: a check that quietly narrows
# its own scope and then prints a number is worse than no check, because the
# number gets repeated.


def test_a_shadowed_copy_behind_a_healthy_one_is_reported(tmp_path, monkeypatch):
    """`shutil.which` returns the winner. The loser is the loaded gun.

    A healthy script first on PATH with a shadowed copy behind it is one PATH
    change away from the incident, and the first draft reported it as simply
    "verified" — it never looked past the winner, and never said so.
    """
    # Arrange — healthy first, shadowed second
    first, second = tmp_path / "a", tmp_path / "b"
    for d, body in ((first, GOOD_SCRIPT), (second, SHADOWED_SCRIPT)):
        d.mkdir()
        script = d / "scitex-cards"
        script.write_text(body, encoding="utf-8")
        script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{first}:{second}")
    # Act
    result = check_console_scripts_not_shadowed()
    # Assert
    assert result["ok"] is False, "the shadowed copy behind the winner was ignored"
    assert str(second) in result["detail"], "the shadowed copy is not named"
    assert "scitex_todo" in result["detail"]


def test_names_not_found_are_named_as_unexamined(tmp_path, monkeypatch):
    """Reporting only on what was examined reads as complete coverage.

    With `scitex-cards` present and `scitex-todo` absent, a report mentioning
    only the former implies both were checked.
    """
    # Arrange — only the primary exists
    _install_fake_script(tmp_path, monkeypatch, "scitex-cards", GOOD_SCRIPT)
    # Act
    result = check_console_scripts_not_shadowed()
    # Assert
    assert result["ok"] is True
    assert "scitex-todo" in result["detail"], "the unexamined name is not disclosed"
    assert "unexamined" in result["detail"]


def test_the_same_file_reached_twice_is_counted_once(tmp_path, monkeypatch):
    """A duplicated PATH entry is a benign layout, not drift.

    Inflating the copy count would make an ordinary environment look damaged —
    the false-positive side of the same coin.
    """
    # Arrange
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    script = bin_dir / "scitex-cards"
    script.write_text(GOOD_SCRIPT, encoding="utf-8")
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{bin_dir}")
    # Act
    detail = check_console_scripts_not_shadowed()["detail"]
    # Assert
    assert detail.count(str(script)) == 1, "one real file reported more than once"


# EOF
