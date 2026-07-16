#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The `scitex-cards` console script must stay wired to the same CLI.

The operator is moving the package name to `scitex-cards` (matching scitex-hub's
"Cards" surface) and has already put it in his GUI startup list. This entry point
ships the BINARY half of the bridge ahead of the full rename: same code, second
name.

It is deliberately NOT sufficient on its own — his loop also requires
`~/proj/scitex-cards/` to exist as a directory — but the binary half belongs in
the package, and if it silently disappears in the rename his startup breaks with
"scitex-cards: not found" and no explanation.
"""

import tomllib
from pathlib import Path


def _scripts() -> dict:
    root = Path(__file__).resolve().parents[1]
    with (root / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)["project"]["scripts"]


def test_scitex_cards_console_script_exists():
    assert "scitex-cards" in _scripts()


def test_scitex_cards_points_at_the_same_cli():
    """Second name, same entry point — not a fork."""
    scripts = _scripts()
    assert scripts["scitex-cards"] == scripts["scitex-todo"]


def test_scitex_cards_console_script_still_exists():
    """The old name must keep working — the whole fleet still calls it."""
    assert "scitex-todo" in _scripts()
