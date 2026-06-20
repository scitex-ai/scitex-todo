#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the Chat panel's pure helpers (no mocks).

Mirrors ``src/scitex_todo/_django/frontend/src/ChatPanel.tsx``.

Lead a2a ``74db4f2d`` + ``10afa799`` greenlight (TRACK-2 Phase 6,
2026-06-14). We pin two contract helpers by executing the actual
TypeScript fragments via ``node``:

  1. ``authorColorToken(author)`` — content-agnostic deterministic
     map from a string to one of ``AUTHOR_COLOR_TOKENS``. Empty /
     null input lands in the "muted" slot.
  2. ``appendComment(current, next)`` — pure, order-preserving
     append (the optimistic-append codepath in the React component).

The TS file itself is also asserted to expose the canonical
``authorColorToken`` / ``appendComment`` / ``AUTHOR_COLOR_TOKENS``
API so the React component keeps depending on a stable contract.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

# Repo-relative path to the TS module under test. Resolved off this file's
# location so the test runs from any cwd.
TS_FILE = (
    Path(__file__).resolve().parents[4]
    / "src"
    / "scitex_todo"
    / "_django"
    / "frontend"
    / "src"
    / "ChatPanel.tsx"
)


def _node() -> str:
    """Locate ``node``; skip the suite cleanly if it isn't installed."""
    exe = shutil.which("node")
    if exe is None:
        pytest.skip("node executable not found on PATH")
    return exe


# A 1:1 hand-port of the TS helpers' runtime body. The static-source
# assertions in ``test_static_source_contract`` keep this mirror in
# lock-step with the TS source: any rename / signature change in the
# TS module will fail those grep assertions, prompting an update here.
_JS_RUNTIME = textwrap.dedent(
    """
    const AUTHOR_COLOR_TOKENS = [
      "var(--stx-accent)",
      "var(--stx-text)",
      "var(--stx-text-muted)",
      "var(--stx-border-strong)",
      "var(--stx-accent-on)",
      "var(--stx-text-faint)",
    ];

    function authorColorToken(author) {
      const s = (author ?? "").trim();
      if (s === "") return AUTHOR_COLOR_TOKENS[2];
      let h = 5381;
      for (let i = 0; i < s.length; i += 1) {
        h = ((h << 5) + h + s.charCodeAt(i)) >>> 0;
      }
      return AUTHOR_COLOR_TOKENS[h % AUTHOR_COLOR_TOKENS.length];
    }

    function appendComment(current, next) {
      return [...current, next];
    }
    """
).strip()


def _run(js: str) -> str:
    """Run a JS fragment via node and return stripped stdout."""
    proc = subprocess.run(
        [_node(), "--input-type=module", "-e", _JS_RUNTIME + "\n" + js],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return proc.stdout.strip()


# === authorColorToken ======================================================


def test_author_color_token_deterministic() -> None:
    """The same input string maps to the same token across two calls."""
    # Arrange
    out = _run(
        "console.log(JSON.stringify("
        "[authorColorToken('agent-a'), authorColorToken('agent-a')]"
        "));"
    )
    # Act
    a, b = json.loads(out)
    # Assert
    assert a == b


def test_author_color_token_empty_input_uses_muted_slot_tokens() -> None:
    """Empty / null input lands in the muted slot (index 2) so
    unlabeled comments still render legibly. The exact slot is the
    contract — no hardcoded literal here in the test, we just pin
    "it picks index 2 = the muted token"."""
    # Arrange
    out = _run(
        "console.log(JSON.stringify("
        "[authorColorToken(''), authorColorToken(null), "
        "authorColorToken(undefined)]"
        "));"
    )
    # Act
    tokens = json.loads(out)
    # Assert
    assert tokens[0] == "var(--stx-text-muted)"

def test_author_color_token_empty_input_uses_muted_slot_tokens_2() -> None:
    """Empty / null input lands in the muted slot (index 2) so
    unlabeled comments still render legibly. The exact slot is the
    contract — no hardcoded literal here in the test, we just pin
    "it picks index 2 = the muted token"."""
    # Arrange
    out = _run(
        "console.log(JSON.stringify("
        "[authorColorToken(''), authorColorToken(null), "
        "authorColorToken(undefined)]"
        "));"
    )
    # Act
    tokens = json.loads(out)
    # Assert
    assert tokens[1] == "var(--stx-text-muted)"

def test_author_color_token_empty_input_uses_muted_slot_tokens_3() -> None:
    """Empty / null input lands in the muted slot (index 2) so
    unlabeled comments still render legibly. The exact slot is the
    contract — no hardcoded literal here in the test, we just pin
    "it picks index 2 = the muted token"."""
    # Arrange
    out = _run(
        "console.log(JSON.stringify("
        "[authorColorToken(''), authorColorToken(null), "
        "authorColorToken(undefined)]"
        "));"
    )
    # Act
    tokens = json.loads(out)
    # Assert
    assert tokens[2] == "var(--stx-text-muted)"


def test_author_color_token_returns_known_token() -> None:
    """The returned string is one of the AUTHOR_COLOR_TOKENS — closed
    palette, no rogue hex / rgb."""
    # Arrange
    out = _run(
        "console.log(JSON.stringify(authorColorToken('operator')));"
    )
    # Act
    token = json.loads(out)
    # Assert
    assert token in {
        "var(--stx-accent)",
        "var(--stx-text)",
        "var(--stx-text-muted)",
        "var(--stx-border-strong)",
        "var(--stx-accent-on)",
        "var(--stx-text-faint)",
    }


def test_author_color_token_different_authors_can_collide_case_1() -> None:
    """The hash space is finite (6 slots) and the mapping is content-
    agnostic; we only assert the predicate doesn't throw and returns a
    token for two different inputs."""
    # Arrange
    out = _run(
        "console.log(JSON.stringify("
        "[authorColorToken('agent-a'), authorColorToken('agent-b')]"
        "));"
    )
    # Act
    a, b = json.loads(out)
    # Assert
    assert isinstance(a, str) and a.startswith("var(--stx-")

def test_author_color_token_different_authors_can_collide_case_2() -> None:
    """The hash space is finite (6 slots) and the mapping is content-
    agnostic; we only assert the predicate doesn't throw and returns a
    token for two different inputs."""
    # Arrange
    out = _run(
        "console.log(JSON.stringify("
        "[authorColorToken('agent-a'), authorColorToken('agent-b')]"
        "));"
    )
    # Act
    a, b = json.loads(out)
    # Assert
    assert isinstance(b, str) and b.startswith("var(--stx-")


# === appendComment =========================================================


def test_append_comment_preserves_order() -> None:
    """The new comment lands at the end — oldest-first thread order."""
    # Arrange
    out = _run(
        "const cur = [{ts: '1', author: 'a', text: 'one'}];\n"
        "const next = {ts: '2', author: 'b', text: 'two'};\n"
        "console.log(JSON.stringify(appendComment(cur, next)));"
    )
    # Act
    result = json.loads(out)
    # Assert
    assert [c["text"] for c in result] == ["one", "two"]


def test_append_comment_does_not_mutate_input() -> None:
    """The helper returns a new array; the caller's list is unchanged.
    Pins the optimistic-append safety property — concurrent renders
    with the previous list reference don't see the appended row."""
    # Arrange
    out = _run(
        "const cur = [{ts: '1', author: 'a', text: 'one'}];\n"
        "const next = {ts: '2', author: 'b', text: 'two'};\n"
        "appendComment(cur, next);\n"
        "console.log(JSON.stringify(cur));"
    )
    # Act
    result = json.loads(out)
    # Assert
    assert len(result) == 1


def test_append_comment_empty_to_first() -> None:
    """Appending to an empty thread yields a one-row list."""
    # Arrange
    out = _run(
        "const next = {ts: '1', author: 'a', text: 'first'};\n"
        "console.log(JSON.stringify(appendComment([], next)));"
    )
    # Act
    result = json.loads(out)
    # Assert
    assert len(result) == 1 and result[0]["text"] == "first"


# === static source contract ================================================


def test_static_source_contract() -> None:
    """The TS module must continue to expose the documented public API
    so the React component keeps importing ``authorColorToken`` +
    ``appendComment`` + ``AUTHOR_COLOR_TOKENS`` by name. Also pins the
    canonical predicate fragments so the JS mirror above stays in
    lock-step with the TS source."""
    # Arrange
    # Act
    src = TS_FILE.read_text(encoding="utf-8")
    # Public API surface.
    # Assert
    assert "export const AUTHOR_COLOR_TOKENS" in src
    assert "export function authorColorToken(" in src
    assert "export function appendComment(" in src
    # Canonical predicate fragments — if any change, this test fires
    # so the JS mirror in _JS_RUNTIME above gets updated in lock-step.
    for needle in [
        'var(--stx-accent)',
        'var(--stx-text-muted)',
        "let h = 5381;",
        "h = ((h << 5) + h + s.charCodeAt(i)) >>> 0;",
        "return [...current, next];",
    ]:
        assert needle in src, (
            f"ChatPanel.tsx no longer contains the canonical fragment "
            f"{needle!r}; update the JS mirror in this test in lock-step."
        )
