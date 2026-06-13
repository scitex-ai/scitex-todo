#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the PR-merge recording mandate in the canonical scitex-todo
skill (fleet-adoption multiplier #3, lead a2a `0cdca03a`).

The skill is propagated into every agent via `scitex-todo skills
propagate` (PR #161). If these load-bearing phrases drift, every
agent's read-on-boot mandate weakens — so we pin them to a test that
runs in every CI cycle. No mocks (STX-NM / PA-306); just reads the
shipped files.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Resolve the skill file via the package install path so the test
# follows the actual file shipped in the wheel, not a stale checkout
# copy.
from scitex_todo._cli._skills import _skills_root  # type: ignore[attr-defined]

SKILL_DIR = _skills_root()
SKILL_MD = SKILL_DIR / "scitex-todo" / "SKILL.md"
LEAF_MD = SKILL_DIR / "scitex-todo" / "60_pr-merge-recording-mandate.md"


# === SKILL.md contains the load-bearing mandate section ====================


def test_skill_md_has_pr_merge_mandate_header():
    # Arrange / Act
    text = SKILL_MD.read_text(encoding="utf-8")
    # Assert
    assert "MANDATE — record evidence at PR-merge" in text


def test_skill_md_mandate_specifies_done_with_pr_url():
    # Arrange / Act
    text = SKILL_MD.read_text(encoding="utf-8")
    # Assert — the exact CLI verb must appear so an agent reading on
    # boot can grep it directly.
    assert "scitex-todo done <card-id> --pr-url" in text


def test_skill_md_mandate_states_pr_url_is_required():
    # Arrange / Act
    text = SKILL_MD.read_text(encoding="utf-8")
    # Assert — the required-not-optional phrase is the hard-rule signal.
    assert "REQUIRED, not optional" in text


def test_skill_md_mandate_cites_completion_rationale():
    # Arrange / Act
    text = SKILL_MD.read_text(encoding="utf-8")
    # Assert — the operator's 完了率 metric is the WHY; if the phrase
    # drops, agents lose the reason to follow the mandate.
    assert "完了率" in text


# === Leaf doc 60_pr-merge-recording-mandate.md exists =======================


def test_leaf_doc_exists():
    # Arrange / Act / Assert
    assert LEAF_MD.exists()


def test_leaf_doc_documents_no_pr_path():
    # Arrange / Act
    text = LEAF_MD.read_text(encoding="utf-8")
    # Assert — the "no-PR completion" alternative must be reachable.
    assert "no-PR completion" in text


def test_leaf_doc_documents_bulk_catchup_verb():
    # Arrange / Act
    text = LEAF_MD.read_text(encoding="utf-8")
    # Assert — the sync-github fallback is the documented catch-up path.
    assert "scitex-todo sync-github" in text


def test_leaf_doc_cites_lead_provenance():
    # Arrange / Act
    text = LEAF_MD.read_text(encoding="utf-8")
    # Assert — provenance line keeps the audit trail discoverable for
    # the next maintainer / restart.
    assert "0cdca03a" in text
