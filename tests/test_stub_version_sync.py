#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The deprecation stub's version tracks the main package, release for release.

The stub (stub/scitex-todo/) publishes from the SAME tag as scitex-cards; a
forgotten bump would either fail the upload (duplicate version) or, worse,
leave old `scitex-todo` pins resolving to a stub that requires an older
scitex-cards than the one being released. A version the release ritual can
forget is a version a test must pin.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _version_of(pyproject: Path) -> str:
    m = re.search(r'^version = "([^"]+)"', pyproject.read_text(), re.MULTILINE)
    assert m, f"no version line in {pyproject}"
    return m.group(1)


def test_stub_version_equals_main_version():
    # Arrange
    main_v = _version_of(REPO / "pyproject.toml")
    stub_v = _version_of(REPO / "stub" / "scitex-todo" / "pyproject.toml")
    # Assert
    assert stub_v == main_v, (
        f"stub/scitex-todo is {stub_v} but the main package is {main_v} — "
        "bump BOTH in the release PR (they publish from the same tag)."
    )


def test_stub_depends_on_scitex_cards():
    # Arrange
    text = (REPO / "stub" / "scitex-todo" / "pyproject.toml").read_text()
    # Assert — the whole point of the stub.
    assert re.search(r'dependencies = \["scitex-cards>=', text)


def test_stub_declares_both_console_scripts():
    """The stub MUST recreate both CLIs — it installs LAST in an upgrade.

    Old scitex-todo wheels (0.13.x-0.15.x) own bin/scitex-todo AND
    bin/scitex-cards in their RECORD, so upgrading deletes BOTH — and pip
    processes dependencies first, so scitex-cards' own reinstall cannot save
    them. The stub is the final dist processed in that transaction; its
    script declarations are what puts the binaries back (card
    scitex-cards-alias-destroyed-by-uninstall-order-collision-20260717,
    venv-matrix verified 2026-07-18). Dropping either line silently revives
    the fleet-wide CLI kill.
    """
    # Arrange
    text = (REPO / "stub" / "scitex-todo" / "pyproject.toml").read_text()
    # Assert — both scripts, both pointing at the real CLI.
    assert re.search(r'^scitex-todo = "scitex_cards\._cli:main"$', text, re.MULTILINE)
    assert re.search(r'^scitex-cards = "scitex_cards\._cli:main"$', text, re.MULTILINE)
