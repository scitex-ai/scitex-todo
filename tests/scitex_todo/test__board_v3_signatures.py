#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression pins for ``board_v3.html`` features.

The board template is a 3700-line monolith edited by every UI PR;
squash-merge conflicts on parallel branches have silently regressed
already-shipped features twice during the 2026-06-12 wave (P1 search-
as-launcher + P7 self-named-card filter both got clobbered between
their merge and the P10/P11 wave landing).

This test pins the SIGNATURE STRINGS of each shipped feature so a
future squash that would silently drop them fails CI instead.

When a feature deliberately changes shape, update the signature in
the matching ``test_<feature>_signatures_present`` test — the pin
documents the intent.

Refactor-friendly: tests look for substrings (not byte-for-byte exact
blocks) so non-behavioural cosmetics + indentation edits stay free.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import scitex_todo

BOARD_TEMPLATE = (
    Path(scitex_todo.__file__).parent
    / "_django"
    / "templates"
    / "scitex_todo"
    / "board_v3.html"
)


@pytest.fixture(scope="module")
def board_text() -> str:
    """Read the board template once per test module."""
    return BOARD_TEMPLATE.read_text(encoding="utf-8")


# -----------------------------------------------------------------------------
# P1 — search-as-launcher (PR #86)
# -----------------------------------------------------------------------------


class TestP1SearchAsLauncher:
    """Pins for the PR #86 search-as-launcher feature."""

    def test_attach_search_keyboard_launcher_defined(self, board_text):
        assert "function attachSearchKeyboardLauncher" in board_text

    def test_attach_search_keyboard_launcher_called(self, board_text):
        assert "attachSearchKeyboardLauncher()" in board_text

    def test_kbd_hint_chip_css_class_defined(self, board_text):
        assert ".filt-search-kbd" in board_text

    def test_kbd_hint_chip_text_present(self, board_text):
        # The visible affordance — "press / to focus" must be in the HTML.
        assert "press <kbd>/</kbd> to focus" in board_text

    def test_search_input_min_width_bumped(self, board_text):
        # The P1 CSS bump to 320px is what makes the search the PRIMARY
        # go-to. A regression to 180px (the pre-P1 narrow form) reverts
        # the affordance.
        assert "min-width: 320px" in board_text


# -----------------------------------------------------------------------------
# P7 — self-named project-umbrella card filter (PR #87)
# -----------------------------------------------------------------------------


class TestP7SelfNamedCard:
    """Pins for the PR #87 self-named project-umbrella filter."""

    def test_is_self_named_project_card_defined(self, board_text):
        assert "function isSelfNamedProjectCard" in board_text

    def test_filter_applied_in_render(self, board_text):
        # The fix is the call site inside render() — the function alone
        # doesn't help if the filter loop doesn't invoke it.
        assert "isSelfNamedProjectCard(t, p)" in board_text


# -----------------------------------------------------------------------------
# P8 — move-picker lists ALL projects + new-project flow (PR #88)
# -----------------------------------------------------------------------------


class TestP8MovePickerAllProjects:
    """Pins for the PR #88 move-picker (post-P11b Combobox fallback path)."""

    def test_no_twelve_item_slice_cap(self, board_text):
        # The pre-P8 cap was ``.slice(0, 12)`` — listing only the first
        # 12 projects out of ~30 in the store. P8 dropped it; PR #94's
        # Combobox layer also lists ALL. A reappearance of the literal
        # slice call means the regression came back.
        assert ".slice(0, 12)" not in board_text

    def test_prompt_move_to_new_project_defined(self, board_text):
        assert "function promptMoveToNewProject" in board_text

    def test_new_project_button_text_present(self, board_text):
        # The legacy "+ New project…" fallback button (used when the
        # scitex-ui Combobox is unavailable). Either spelling acceptable.
        assert "New project" in board_text


# -----------------------------------------------------------------------------
# P2 + P9 — filter UX collapse + sort-by (PR #89)
# -----------------------------------------------------------------------------


class TestP2P9FilterAndSort:
    """Pins for the PR #89 filter-UX collapse + sort-by control."""

    def test_filter_popover_class_present(self, board_text):
        assert "filt-popover" in board_text

    def test_render_active_filter_chips_defined(self, board_text):
        assert "function renderActiveFilterChips" in board_text

    def test_clear_one_filter_defined(self, board_text):
        assert "function clearOneFilter" in board_text

    def test_state_sort_field_present(self, board_text):
        assert "STATE.sort" in board_text

    def test_sort_comparator_defined(self, board_text):
        assert "function _sortComparator" in board_text


# -----------------------------------------------------------------------------
# P10 — GROUPS (PR #91)
# -----------------------------------------------------------------------------


class TestP10Groups:
    """Pins for the PR #91 project-groups feature."""

    def test_state_group_by_present(self, board_text):
        assert "STATE.groupBy" in board_text

    def test_render_group_strip_defined(self, board_text):
        assert "function renderGroupStrip" in board_text

    def test_apply_group_clustering_defined(self, board_text):
        assert "function _applyGroupClustering" in board_text

    def test_group_spans_all_mount_present(self, board_text):
        # The spans_all banner mounts here above the columns grid.
        assert 'id="group-spans-all"' in board_text


# -----------------------------------------------------------------------------
# P4 PR1 — deadline + scheduled FE consumption (PR #92)
# -----------------------------------------------------------------------------


class TestP4DeadlineFE:
    """Pins for the PR #92 deadline field prefer-over-title FE path."""

    def test_date_info_reads_deadline_field(self, board_text):
        # `dateInfo()` must check the schema deadline field BEFORE
        # falling back to the title parse. The substring matches the
        # actual line where the field read happens.
        assert "t.deadline" in board_text


class TestP4MultiRecurringFE:
    """Pins for the P4 PR3 multi/recurring FE consumer."""

    def test_date_info_reads_deadline_next(self, board_text):
        # The server expands recurring + multi to a single
        # `deadline_next` ISO; the FE must prefer it over `deadline`
        # when present.
        assert "t.deadline_next" in board_text

    def test_extract_repeater_suffix_helper_defined(self, board_text):
        assert "function _extractRepeaterSuffix" in board_text

    def test_first_recurring_deadline_helper_defined(self, board_text):
        assert "function _firstRecurringDeadline" in board_text


# -----------------------------------------------------------------------------
# P11b — Combobox consumer (PR #94)
# -----------------------------------------------------------------------------


class TestP11bComboboxConsumer:
    """Pins for the PR #94 Combobox layer + Combobox-driven move-picker."""

    def test_combobox_css_static_load(self, board_text):
        assert "combobox.css" in board_text

    def test_combobox_js_static_load(self, board_text):
        assert "combobox.js" in board_text

    def test_attach_combobox_defined(self, board_text):
        assert "function attachCombobox" in board_text

    def test_open_move_to_combobox_defined(self, board_text):
        assert "function _openMoveToCombobox" in board_text


# EOF
