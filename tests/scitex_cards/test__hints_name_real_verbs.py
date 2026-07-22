#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A hint must be runnable as printed. This proves the verbs in ours exist.

WHY THIS EXISTS (2026-07-22). ``sac`` found that ``enforce_ripgrep.sh`` — the
most-fired guard in the fleet container — blocks ``grep -r`` and steers agents
to two remedies that are both unavailable there, so an agent who fully complies
with the guardrail has nowhere correct to go. Applying that rule to our own
surface immediately found the same defect, in a worse place: two health hints
told the reader to run ``scitex-cards db import``, and the ``db`` group has no
``import`` subcommand. Both fire on the STORE IS BROKEN path — the moment the
reader most needs a correct next step.

The promise was worse than the name. "seed from an export" has no CLI surface
at all; recovery from a dump is a Python-level operation. A broken-store agent
was handed an unrunnable command AND a recovery route that does not exist.

WHY THE POSITIVE CONTROL BELOW IS LOAD-BEARING, and not ceremony. The obvious
version of this test — enumerate the CLI, assert every hinted verb is in it —
passes trivially if the enumeration returns nothing. That is the exact failure
this whole class is about: an instrument reporting success while measuring
nothing (``rg`` exits 0 and prints zero matches; a divergence checker skips
1,145 files and prints a confident total). So the control asserts that verbs we
KNOW exist are found, before any hint is judged. If enumeration breaks, the
control fails loudly instead of the suite going green over an unchecked claim.
"""

from __future__ import annotations

import re
from pathlib import Path

import click
import pytest

from scitex_cards._cli import main

SRC = Path(__file__).resolve().parents[2] / "src" / "scitex_cards"

#: Verbs that certainly exist. If ANY of these is missing, enumeration is
#: broken and every result below is meaningless.
CONTROL_VERBS = ("list-tasks", "add", "update", "health")

#: Program names our hints may print. Both are shipped console scripts.
_PROGRAMS = ("scitex-cards", "scitex-todo")


def _all_verbs() -> set[str]:
    """Every command path the CLI actually exposes, e.g. ``{"db verify", ...}``."""

    def walk(group: click.Group, prefix: str = "") -> set[str]:
        found: set[str] = set()
        ctx = click.Context(group)
        for name in group.list_commands(ctx):
            cmd = group.get_command(ctx, name)
            full = f"{prefix}{name}"
            found.add(full)
            if isinstance(cmd, click.Group):
                found |= walk(cmd, full + " ")
        return found

    return walk(main)


def _hinted_verbs() -> dict[str, list[str]]:
    """Backticked ``<program> <verb...>`` invocations in health/probe sources.

    Returns ``{verb_path: [files]}``. Placeholder arguments (``<id>``, ``--flag``)
    are dropped, so only the command path is judged — this test is about whether
    the VERB exists, not about argument validity.
    """
    found: dict[str, list[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        if not any(k in path.name for k in ("health", "probe")):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for snippet in re.findall(r"`([^`\n]{4,160})`", text):
            words = snippet.split()
            if not words or words[0] not in _PROGRAMS:
                continue
            verb: list[str] = []
            for word in words[1:]:
                if word.startswith(("-", "<", "$", "{")):
                    break
                verb.append(word)
            if verb:
                found.setdefault(" ".join(verb), []).append(path.name)
    return found


def test_the_cli_enumeration_actually_works():
    """POSITIVE CONTROL. Without this, an empty enumeration passes everything."""
    # Arrange / Act
    verbs = _all_verbs()
    # Assert
    assert len(verbs) > 20, f"CLI enumeration returned only {len(verbs)} verbs"
    missing = [v for v in CONTROL_VERBS if v not in verbs]
    assert not missing, (
        f"verbs known to exist were not found: {missing}. The enumeration is "
        f"broken, so every other assertion in this file is vacuous."
    )


def test_we_actually_found_hints_to_check():
    """SECOND CONTROL: a regex that matches nothing would also pass silently."""
    # Arrange / Act
    hinted = _hinted_verbs()
    # Assert
    assert hinted, (
        "no hinted commands were extracted from the health/probe sources — "
        "either the sources moved or the extraction broke; either way this "
        "file is no longer checking anything."
    )


def test_every_verb_named_in_a_hint_exists():
    """The rule: a hint must be runnable as printed.

    A reader following a hint is usually in the failure path already. Sending
    them to a command that does not exist costs them the one thing they have
    least of at that moment — a next step they can trust.
    """
    # Arrange
    verbs = _all_verbs()
    hinted = _hinted_verbs()
    # Act
    offenders = [
        f"{verb!r} (named in {', '.join(sorted(set(files)))})"
        for verb, files in sorted(hinted.items())
        if verb not in verbs
    ]
    # Assert
    assert not offenders, (
        "health hints name CLI verbs that do not exist:\n  "
        + "\n  ".join(offenders)
        + "\n\nA hint must be runnable as printed. Fix the hint, or add the "
        "verb — do not leave the reader to discover it in a failure path."
    )


@pytest.mark.parametrize("dead", ["db import", "db restore", "db seed"])
def test_the_check_would_catch_a_dead_verb(dead):
    """Prove this test CAN fail — the shapes that were actually shipped.

    `db import` was live in two hints until 2026-07-22. A guard that cannot
    fail is not a guard, so assert these are genuinely absent from the CLI
    rather than trusting that the check above would have noticed.
    """
    # Arrange / Act / Assert
    assert dead not in _all_verbs(), (
        f"{dead!r} now exists — if it was added deliberately, this test's "
        f"premise is stale and the hint wording should be revisited."
    )


# EOF
