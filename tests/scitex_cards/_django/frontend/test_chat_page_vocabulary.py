#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ADR-0011 vocabulary contract for the standalone DM page template.

Guards ``templates/scitex_cards/chat.html`` — the operator↔agent
Direct-messages page (distinct from the board-embedded ``ChatPanel.tsx``
covered by ``test_chat.py``). ADR-0011: user-visible product text says
"scitex-cards GUI", never "board", and never the OLD package name
"scitex-todo".

These pin the exact regressions fixed in card
``scitex-cards-chat-gui-vocabulary-nav-label-20260717``:

  - the window ``<title>`` and header version chip carried the stale
    package name "scitex-todo" even though pyproject renamed it to
    scitex-cards;
  - the nav link read "board" (visible label) via a ``.board-link``
    class.

Mocks-free: open the template source and assert on its text, exactly
like ``test_chat.py``. Reading the source (not a Django render) keeps
the check independent of the editable-install target, and reads THIS
tree's template because the path is anchored to the test file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[4]

_CHAT_HTML = (
    _REPO_ROOT
    / "src"
    / "scitex_cards"
    / "_django"
    / "templates"
    / "scitex_cards"
    / "chat.html"
)


def _read() -> str:
    assert _CHAT_HTML.is_file(), f"expected template missing: {_CHAT_HTML}"
    return _CHAT_HTML.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Stale package name — "scitex-todo" must not appear in user-visible chrome.
# ---------------------------------------------------------------------------


def test_title_uses_current_package_name() -> None:
    """The window <title> names the CURRENT package, scitex-cards."""
    html = _read()
    assert "<title>Chat — scitex-cards v" in html


def test_header_version_chip_uses_current_package_name() -> None:
    """The header version chip names the CURRENT package, scitex-cards."""
    html = _read()
    assert '<span class="ver">scitex-cards v' in html


def test_no_stale_scitex_todo_package_name_in_chrome() -> None:
    """The OLD package name must not linger in the version strings —
    the context var is already correct; only the literal was stale."""
    html = _read()
    assert "scitex-todo v" not in html


# ---------------------------------------------------------------------------
# ADR-0011: never "board" as user-visible product text.
# ---------------------------------------------------------------------------


def test_nav_link_label_says_gui_not_board() -> None:
    """The nav link back to the main view reads "GUI", never "board"."""
    html = _read()
    assert "&larr; GUI" in html
    assert "&larr; board" not in html


def test_nav_link_class_renamed_off_board() -> None:
    """The nav link class carries no "board" vocabulary either — the
    rename is self-contained in this template, so nothing else links it."""
    html = _read()
    assert 'class="gui-link"' in html
    assert "board-link" not in html
    assert "a.gui-link" in html  # the CSS rule was renamed in lockstep


@pytest.mark.parametrize("css_selector", ["header a.gui-link", "header a.gui-link:hover"])
def test_gui_link_css_rules_present(css_selector: str) -> None:
    """Both the base and :hover rules survived the class rename."""
    html = _read()
    assert css_selector in html
