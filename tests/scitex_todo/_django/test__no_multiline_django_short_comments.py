#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression guard against the operator-visible bug fixed today.

Lead a2a `f7a5d37930b9479ca7e53a7e316c132d`, 2026-06-14 — Django's
`{# ... #}` syntax is SINGLE-LINE ONLY; newlines between `{#` and
`#}` are NOT stripped, so the comment leaks as literal text onto the
operator's screen. PR #173 shipped a multi-line `{# #}` block that
leaked; this test pins the no-multi-line rule so the bug class can
NEVER recur silently.

The rule: ANY `{#` token in a template MUST be closed by `#}` on the
SAME line. Cross-line spans are forbidden — use
`{% comment %}...{% endcomment %}` instead.

No mocks (STX-NM / PA-306). Reads the live template files in the
package.
"""

from __future__ import annotations

import re
from pathlib import Path

import scitex_todo


_TEMPLATES_ROOT = (
    Path(scitex_todo.__file__).parent / "_django" / "templates"
)


def _iter_template_files():
    return list(_TEMPLATES_ROOT.rglob("*.html"))


def test_templates_root_actually_exists():
    # Arrange / Act / Assert — guard against a path refactor
    # silently turning this whole module into a no-op.
    assert _TEMPLATES_ROOT.is_dir()


def test_at_least_one_template_present():
    # Arrange / Act / Assert
    assert _iter_template_files()


def test_no_template_contains_multi_line_django_short_comment():
    # Arrange — for each template line, every `{#` token MUST be
    # closed by `#}` on the SAME line. If `{#` appears with no `#}`
    # after it on the same line, the comment spans lines and Django
    # will NOT strip it (single-line-only syntax).
    offenders: list[str] = []
    short_open = "{#"
    short_close = "#}"
    for path in _iter_template_files():
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), start=1):
            idx = 0
            while True:
                start = line.find(short_open, idx)
                if start < 0:
                    break
                close = line.find(short_close, start + len(short_open))
                if close < 0:
                    offenders.append(
                        f"{path.relative_to(_TEMPLATES_ROOT)}:{line_no} — "
                        f"open `{short_open}` with no `{short_close}` on the "
                        f"same line; use `{{% comment %}}...{{% endcomment %}}` "
                        f"for multi-line content."
                    )
                    break  # one report per line is enough
                idx = close + len(short_close)
    # Assert — single, readable failure listing every offender.
    assert not offenders, (
        "Multi-line Django `{# … #}` comments leak as literal text "
        "(operator-visible bug, lead a2a `f7a5d37930b9479ca7e53a7e316c132d`):\n  - "
        + "\n  - ".join(offenders)
    )
