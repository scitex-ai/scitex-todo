#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pin the NEVER-hand-edit mandate in the canonical scitex-cards skill.

The 2026-06-13 corruption episode (canonical `~/.scitex/todo/tasks.yaml`
truncated mid-string at line ~2784) traced to a hand-edit bypassing the
API. Lead a2a `02c8a4ae` directed the rule into the canonical skill so
every fleet agent reads it on boot (via the #161 `skills propagate`
mechanism). If a future refactor drops the phrase, every agent silently
loses the read-on-boot guard — pin it here so CI catches the drift.

No mocks (STX-NM / PA-306).
"""

from __future__ import annotations

from scitex_cards._cli._skills import _skills_root  # type: ignore[attr-defined]

SKILL_MD = _skills_root() / "SKILL.md"


def test_skill_md_has_no_hand_edit_mandate_header():
    # Arrange
    # Act
    text = SKILL_MD.read_text(encoding="utf-8")
    # Assert
    assert "MANDATE — NEVER hand-edit" in text


def test_skill_md_names_the_canonical_path():
    # Arrange
    # Act
    text = SKILL_MD.read_text(encoding="utf-8")
    # Assert
    assert "~/.scitex/todo/tasks.yaml" in text


def test_skill_md_documents_emergency_repair_exception():
    # Arrange
    # Act
    text = SKILL_MD.read_text(encoding="utf-8")
    # a hand-edit IS justified (an already-broken file that won't parse).
    # Assert
    assert "Emergency repair exception" in text


def test_skill_md_cites_pr_166_safety_net():
    # Arrange
    # Act
    text = SKILL_MD.read_text(encoding="utf-8")
    # trail discoverable.
    # Assert
    assert "PR-#166" in text or "PR #166" in text
