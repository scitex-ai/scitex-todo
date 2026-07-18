#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the DM thread pane's pure render planning (no mocks).

Mirrors ``src/scitex_cards/_django/static/scitex_cards/chat/chat_diff.js``.

These tests ``require()`` the shipped module and run the REAL functions
under node. There is deliberately no hand-ported copy of the logic here:
``chat_diff.js`` is DOM-free plain JS, so node can import the actual
file, and the single source of truth stays the file the browser loads.

What is pinned:

  1. ``planRender(rendered, messages)`` — the append/rebuild/noop
     decision behind incremental repainting of the thread.
  2. ``shouldStickToBottom(...)`` — the scroll-anchoring rule (follow new
     messages only when already at the bottom).
  3. ``messageKey`` / ``messageFingerprint`` — the identity and
     change-detection primitives the plan is built on.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

# Repo-relative path to the JS module under test. Resolved off this file's
# location so the test runs from any cwd.
JS_FILE = (
    Path(__file__).resolve().parents[6]
    / "src"
    / "scitex_cards"
    / "_django"
    / "static"
    / "scitex_cards"
    / "chat"
    / "chat_diff.js"
)


def _node() -> str:
    """Locate ``node``; skip the suite cleanly if it isn't installed."""
    exe = shutil.which("node")
    if exe is None:
        pytest.skip("node executable not found on PATH")
    return exe


def _run(js: str) -> str:
    """Run a JS fragment against the real chat_diff.js; return stdout."""
    assert JS_FILE.is_file(), f"module under test missing: {JS_FILE}"
    script = f"const ChatDiff = require({json.dumps(str(JS_FILE))});\n" + js
    proc = subprocess.run(
        [_node(), "--input-type=commonjs", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return proc.stdout.strip()


def _plan(rendered: list[str], messages: list[dict]) -> dict:
    """Call planRender with JSON-round-tripped args and return the plan."""
    out = _run(
        f"const rendered = {json.dumps(rendered)};\n"
        f"const messages = {json.dumps(messages)};\n"
        "console.log(JSON.stringify(ChatDiff.planRender(rendered, messages)));"
    )
    return json.loads(out)


def _fingerprints(messages: list[dict]) -> list[str]:
    """The fingerprints the pane would hold after rendering `messages`."""
    out = _run(
        f"const messages = {json.dumps(messages)};\n"
        "console.log(JSON.stringify("
        "messages.map(ChatDiff.messageFingerprint)));"
    )
    return json.loads(out)


_M1 = {"id": "m_001", "from": "operator", "body": "one", "ts": "2026-07-17T09:00:00Z"}
_M2 = {"id": "m_002", "from": "agent-x", "body": "two", "ts": "2026-07-17T09:01:00Z"}
_M3 = {"id": "m_003", "from": "operator", "body": "three", "ts": "2026-07-17T09:02:00Z"}


# === planRender — append (the common case) =================================


def test_plan_render_first_paint_appends_every_message() -> None:
    """An empty pane appends the whole thread — the open-thread path."""
    # Arrange
    # Act
    plan = _plan([], [_M1, _M2])
    # Assert
    assert plan["mode"] == "append"


def test_plan_render_first_paint_added_carries_all_messages() -> None:
    """The append plan hands back every message to paint."""
    # Arrange
    # Act
    plan = _plan([], [_M1, _M2])
    # Assert
    assert [m["id"] for m in plan["added"]] == ["m_001", "m_002"]


def test_plan_render_new_message_appends_only_the_new_one() -> None:
    """The core property: an arriving message repaints ONLY itself, so
    the bubbles already on screen keep their text selection."""
    # Arrange
    rendered = _fingerprints([_M1, _M2])
    # Act
    plan = _plan(rendered, [_M1, _M2, _M3])
    # Assert
    assert plan["mode"] == "append"


def test_plan_render_new_message_added_excludes_existing() -> None:
    """Only the tail is handed back — the prefix is left alone."""
    # Arrange
    rendered = _fingerprints([_M1, _M2])
    # Act
    plan = _plan(rendered, [_M1, _M2, _M3])
    # Assert
    assert [m["id"] for m in plan["added"]] == ["m_003"]


# === planRender — noop (unchanged poll) ====================================


def test_plan_render_unchanged_thread_is_noop() -> None:
    """A poll returning what is already on screen paints nothing. This is
    the 5s-poll steady state: no DOM churn, no lost selection."""
    # Arrange
    rendered = _fingerprints([_M1, _M2])
    # Act
    plan = _plan(rendered, [_M1, _M2])
    # Assert
    assert plan["mode"] == "noop"


def test_plan_render_empty_thread_stays_noop() -> None:
    """An empty thread polled against an empty pane is a noop."""
    # Arrange
    # Act
    plan = _plan([], [])
    # Assert
    assert plan["mode"] == "noop"


# === planRender — rebuild (divergence) =====================================


def test_plan_render_edited_body_forces_rebuild() -> None:
    """An edited message keeps the thread LENGTH identical, so the old
    count-based check could not see it and the pane showed stale text.
    The fingerprint diff catches it."""
    # Arrange
    rendered = _fingerprints([_M1, _M2])
    edited = dict(_M2, body="two (edited)")
    # Act
    plan = _plan(rendered, [_M1, edited])
    # Assert
    assert plan["mode"] == "rebuild"


def test_plan_render_deleted_message_forces_rebuild() -> None:
    """A shorter thread than the pane holds cannot be an append."""
    # Arrange
    rendered = _fingerprints([_M1, _M2, _M3])
    # Act
    plan = _plan(rendered, [_M1, _M3])
    # Assert
    assert plan["mode"] == "rebuild"


def test_plan_render_reordered_history_forces_rebuild() -> None:
    """A diverged prefix (backfill / reorder) repaints rather than
    guessing — the honest fallback."""
    # Arrange
    rendered = _fingerprints([_M1, _M2])
    # Act
    plan = _plan(rendered, [_M2, _M1])
    # Assert
    assert plan["mode"] == "rebuild"


def test_plan_render_rebuild_reports_no_added() -> None:
    """A rebuild repaints everything, so `added` is empty — the caller
    must not append on top of a rebuild."""
    # Arrange
    rendered = _fingerprints([_M1, _M2])
    # Act
    plan = _plan(rendered, [_M2, _M1])
    # Assert
    assert plan["added"] == []


def test_plan_render_fingerprints_track_the_new_list() -> None:
    """Every plan returns the fingerprints the pane will hold after it is
    applied, so the caller stores them without recomputing."""
    # Arrange
    expected = _fingerprints([_M1, _M2, _M3])
    # Act
    plan = _plan(_fingerprints([_M1, _M2]), [_M1, _M2, _M3])
    # Assert
    assert plan["fingerprints"] == expected


# === messageKey / messageFingerprint =======================================


def test_message_key_prefers_the_record_id() -> None:
    """The sidecar's own id is the stable key when present."""
    # Arrange
    # Act
    out = _run(
        f"console.log(ChatDiff.messageKey({json.dumps(_M1)}));"
    )
    # Assert
    assert out == "m_001"


def test_message_key_falls_back_to_ts_and_sender() -> None:
    """Records written before the id field still get a distinct key."""
    # Arrange
    legacy = {"from": "agent-x", "body": "hi", "ts": "2026-07-17T09:00:00Z"}
    # Act
    out = _run(f"console.log(ChatDiff.messageKey({json.dumps(legacy)}));")
    # Assert
    assert out == "2026-07-17T09:00:00Z|agent-x"


def test_message_fingerprint_changes_with_the_body() -> None:
    """Body is part of the fingerprint — that is what makes an edit
    visible to the diff."""
    # Arrange
    # Act
    same, edited = _fingerprints([_M2, dict(_M2, body="changed")])
    # Assert
    assert same != edited


def test_message_fingerprint_is_stable_for_an_unchanged_record() -> None:
    """The same record fingerprints identically across calls, otherwise
    every poll would look like a change and repaint."""
    # Arrange
    # Act
    first, second = _fingerprints([_M1, dict(_M1)])
    # Assert
    assert first == second


# === shouldStickToBottom ===================================================


def test_stick_to_bottom_when_pane_is_scrolled_to_the_end() -> None:
    """At the bottom, new messages follow down."""
    # Arrange
    # Act
    out = _run(
        "console.log(JSON.stringify("
        "ChatDiff.shouldStickToBottom(600, 1000, 400, 40)));"
    )
    # Assert
    assert json.loads(out) is True


def test_do_not_stick_when_operator_scrolled_up_to_read_history() -> None:
    """Scrolled up, an arriving message must NOT yank the pane down —
    the behavior that made the 5s poll unusable while reading back."""
    # Arrange
    # Act
    out = _run(
        "console.log(JSON.stringify("
        "ChatDiff.shouldStickToBottom(0, 1000, 400, 40)));"
    )
    # Assert
    assert json.loads(out) is False


def test_stick_within_the_threshold_slack() -> None:
    """Just shy of the bottom still counts as "at the bottom", so a
    pixel of drift doesn't strand the operator off the newest message."""
    # Arrange
    # Act
    out = _run(
        "console.log(JSON.stringify("
        "ChatDiff.shouldStickToBottom(580, 1000, 400, 40)));"
    )
    # Assert
    assert json.loads(out) is True


def test_empty_pane_sticks_to_bottom() -> None:
    """A freshly opened thread has nothing to scroll, so it counts as at
    the bottom and lands on the newest message with no special case."""
    # Arrange
    # Act
    out = _run(
        "console.log(JSON.stringify("
        "ChatDiff.shouldStickToBottom(0, 0, 0, 40)));"
    )
    # Assert
    assert json.loads(out) is True


# EOF
