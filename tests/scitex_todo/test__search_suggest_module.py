#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pins on the autocomplete / Tab-completion engine shipped under
``static/scitex_todo/board_v3/searchSuggest.js``.

The JS module's own behaviour is covered by
``tests/scitex_todo/test__search_suggest.js`` (``node --test``); this
Python test class is a static-content pin so a refactor that
accidentally drops the API surface (the operator's photographed flow,
the closed enums, the DRY-with-searchQuery contract) trips CI on the
Python side too.

Operator TG 12318, lead a2a ``e09e0c886eb94e509f8daa87c23dca2a``
(2026-06-12). Mirrors the pattern of ``test__search_query_module.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import scitex_todo

SEARCH_SUGGEST_JS = (
    Path(scitex_todo.__file__).parent
    / "_django"
    / "static"
    / "scitex_todo"
    / "board_v3"
    / "searchSuggest.js"
)


@pytest.fixture(scope="module")
def js_text() -> str:
    return SEARCH_SUGGEST_JS.read_text(encoding="utf-8")


class TestSearchSuggestModuleSurface:
    """Static pins on the autocomplete module's public API."""

    def test_suggest_module_exists_on_disk(self):
        # Arrange
        # Act
        # Assert
        assert SEARCH_SUGGEST_JS.exists()

    def test_token_at_cursor_exported(self, js_text):
        # Arrange
        # Act
        # Assert
        assert "tokenAtCursor" in js_text

    def test_key_suggestions_exported(self, js_text):
        # Arrange
        # Act
        # Assert
        assert "keySuggestions" in js_text

    def test_value_suggestions_exported(self, js_text):
        # Arrange
        # Act
        # Assert
        assert "valueSuggestions" in js_text

    def test_apply_suggestion_exported(self, js_text):
        # Arrange
        # Act
        # Assert
        assert "applySuggestion" in js_text

    def test_format_suggestion_exported(self, js_text):
        # Arrange
        # Act
        # Assert
        assert "formatSuggestion" in js_text

    def test_compute_suggestions_exported(self, js_text):
        # Arrange
        # Act
        # Assert
        assert "computeSuggestions" in js_text


class TestDryWithSearchQuery:
    """The suggestion engine must REUSE the qualifier list + closed enums
    from ``searchQuery.js`` rather than redefining them — the lead's
    explicit "don't re-define" directive on this slice. We pin that by
    grepping for the import + the absence of a local QUALIFIERS literal."""

    def test_loads_searchquery_module(self, js_text):
        # The module must source its qualifier vocabulary from
        # ``searchQuery.js``; either a require() (node) or a globalThis
        # fallback (browser script-tag) is acceptable.
        # Arrange
        # Act
        # Assert
        assert "searchQuery" in js_text

    def test_no_local_qualifiers_literal(self, js_text):
        # Lead directive: don't redefine the QUALIFIERS dictionary
        # locally. The KEY_HINTS table is allowed (display-only), but
        # the strategies / fields must come from searchQuery.js.
        # We pin via the absence of `strategy: "substring"` (a
        # tell-tale of a redefined QUALIFIERS spec literal).
        # Arrange
        # Act
        # Assert
        assert 'strategy: "substring"' not in js_text


class TestClosedEnumsRespected:
    """status/kind value suggestions must respect ``_model.py``'s closed
    enums. The module sources them from searchQuery.js; we still pin the
    spec status/kind strings appear so a future grep refactor catches
    drift."""

    def test_status_routed_through_enum(self, js_text):
        # The valueSuggestions impl must special-case the enum branch.
        # Arrange
        # Act
        # Assert
        assert 'strategy === "enum"' in js_text

    def test_priority_special_cased(self, js_text):
        # priority: hint set with operators must appear.
        # Arrange
        # Act
        # Assert
        assert '"priority"' in js_text


class TestOperatorPhotographedFlow:
    """Pin the two flows the lead's brief calls out verbatim:
       `pro` + Tab -> `project:`
       `project:pap` + Tab -> `project:paper-scitex-clew`
    The behavior is exercised in test__search_suggest.js; here we pin
    the relevant strings the implementation must carry."""

    def test_tab_completion_supported(self, js_text):
        # Arrange
        # Act
        # Assert
        assert "applySuggestion" in js_text

    def test_value_with_space_auto_quoted(self, js_text):
        # The implementation auto-wraps values containing whitespace
        # so the searchQuery tokenizer keeps them intact.
        # Arrange
        # Act
        # Assert
        assert "needsQuote" in js_text


class TestKeyHintsCoverAllQualifiers:
    """Every known qualifier should have a one-line hint string so the
    dropdown's secondary-text column is never blank."""

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
    def test_key_hint_present(self, js_text, qualifier):
        # Arrange
        # Act
        # Assert
        assert f'{qualifier}:' in js_text or f'"{qualifier}"' in js_text
