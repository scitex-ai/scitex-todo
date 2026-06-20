#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pins on the GitHub-style search-qualifier parser module shipped under
``static/scitex_todo/board_v3/searchQuery.js``.

The JS module's own behaviour is covered by
``tests/scitex_todo/test__search_query.js`` (``node --test``); this Python
test class is a static-content pin so a refactor that accidentally drops
the API surface (the operator's photographed query, the closed enums,
the multi-qualifier AND semantics) trips CI on the Python side too.

Operator TG 12315 / 12316, lead a2a 7dde227a (2026-06-12).
"""

from __future__ import annotations

from pathlib import Path

import pytest

import scitex_todo

SEARCH_QUERY_JS = (
    Path(scitex_todo.__file__).parent
    / "_django"
    / "static"
    / "scitex_todo"
    / "board_v3"
    / "searchQuery.js"
)


@pytest.fixture(scope="module")
def js_text() -> str:
    return SEARCH_QUERY_JS.read_text(encoding="utf-8")


class TestSearchQueryModuleSurface:
    """Static pins on the parser module's public API."""

    def test_query_module_exists_on_disk(self):
        # Arrange
        # Act
        # Assert
        assert SEARCH_QUERY_JS.exists()

    def test_parse_search_query_exported(self, js_text):
        # Arrange
        # Act
        # Assert
        assert "parseSearchQuery" in js_text

    def test_matches_search_query_exported(self, js_text):
        # Arrange
        # Act
        # Assert
        assert "matchesSearchQuery" in js_text

    def test_tokenize_symbol_is_exported(self, js_text):
        # Arrange
        # Act
        # Assert
        assert "function tokenize" in js_text


class TestQualifierDictionary:
    """Every qualifier from the spec must be wired up."""

    @pytest.mark.parametrize(
        "qualifier",
        [
            "project",
            "repo",
            "agent",
            "assignee",
            "status",
            "kind",
            "parent",
            "scope",
            "id",
            "priority",
            "host",
        ],
    )
    def test_qualifier_keyword_present_in_source(self, js_text, qualifier):
        # Each qualifier name should appear as a key in the QUALIFIERS
        # dictionary literal — substring pin is enough; the JS test
        # suite verifies the actual matching.
        # Arrange
        # Act
        # Assert
        assert qualifier in js_text


class TestClosedEnumsMirror:
    """The closed enums must mirror ``_model.py``'s ``VALID_STATUSES``
    + ``VALID_KINDS`` — fail-loud if the operator adds a new state and
    forgets to thread it through to the FE parser."""

    def test_valid_statuses_synced(self, js_text):
        # Arrange
        from scitex_todo._model import VALID_STATUSES

        # Act
        # Assert
        for status in VALID_STATUSES:
            assert (
                f'"{status}"' in js_text
            ), f"VALID_STATUSES drift: {status!r} not in searchQuery.js"

    def test_valid_kinds_synced(self, js_text):
        # Arrange
        from scitex_todo._model import VALID_KINDS

        # Act
        # Assert
        for kind in VALID_KINDS:
            assert (
                f'"{kind}"' in js_text
            ), f"VALID_KINDS drift: {kind!r} not in searchQuery.js"


class TestSpaceAfterColonTolerance:
    """The operator's photographed pattern was
    ``project: paper-scitex-clew`` (space after colon). The tokenizer
    must handle that case — pin via a substring search for the
    tolerance comment so a future refactor doesn't quietly drop it."""

    def test_tolerates_space_after_colon(self, js_text):
        # The tokenizer's "if (value === '')" branch is what eats the
        # following whitespace + next token; pin its presence.
        # Arrange
        # Act
        # Assert
        assert 'if (value === ""' in js_text or "if (value === '')" in js_text
