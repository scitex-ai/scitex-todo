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

# CSS half of board_v3 was extracted from the inline <style> block on
# 2026-06-12 (squash-regression root-cause fix; see GITIGNORED/
# REFACTORING.md). CSS-specific signature pins now read from the
# concatenated static files instead of from board_v3.html, while
# JS / HTML pins keep reading the template directly.
BOARD_CSS_DIR = (
    Path(scitex_todo.__file__).parent
    / "_django"
    / "static"
    / "scitex_todo"
    / "board_v3"
)


@pytest.fixture(scope="module")
def board_text() -> str:
    """Read the board template once per test module."""
    return BOARD_TEMPLATE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def css_text() -> str:
    """Concatenate every extracted board_v3 CSS file.

    Returns one big string so signature pins can ``assert x in css_text``
    without caring which of the 5 (currently 6) files the rule landed in.
    """
    return "\n".join(
        css_path.read_text(encoding="utf-8")
        for css_path in sorted(BOARD_CSS_DIR.glob("*.css"))
    )


# -----------------------------------------------------------------------------
# P1 — search-as-launcher (PR #86)
# -----------------------------------------------------------------------------


class TestP1SearchAsLauncher:
    """Pins for the PR #86 search-as-launcher feature."""

    def test_attach_search_keyboard_launcher_defined(self, board_text):
        # Arrange
        # Act
        # Assert
        assert "function attachSearchKeyboardLauncher" in board_text

    def test_attach_search_keyboard_launcher_called(self, board_text):
        # Arrange
        # Act
        # Assert
        assert "attachSearchKeyboardLauncher()" in board_text

    def test_kbd_hint_in_search_placeholder_board_text_contains(self, board_text):
        # Lead `032e41545fcf4ab4b98d864ec1770249` 2026-06-12: the
        # standalone `.filt-search-kbd` pill was operator-judged
        # noise and folded into the search input's placeholder per
        # operator "just write the kbd in the search box". The pin
        # now asserts the hint is INSIDE the placeholder text on
        # `#f-search`, so the affordance is still discoverable but
        # without the extra DOM chrome.
        # Arrange
        # Act
        # Assert
        assert "/ to focus" in board_text, (
            "search input must hint at the '/' keyboard shortcut in "
            "its placeholder (operator UX, lead a2a "
            "`032e41545fcf4ab4b98d864ec1770249`)"
        )

    def test_kbd_hint_in_search_placeholder_board_text_contains_2(self, board_text):
        # Lead `032e41545fcf4ab4b98d864ec1770249` 2026-06-12: the
        # standalone `.filt-search-kbd` pill was operator-judged
        # noise and folded into the search input's placeholder per
        # operator "just write the kbd in the search box". The pin
        # now asserts the hint is INSIDE the placeholder text on
        # `#f-search`, so the affordance is still discoverable but
        # without the extra DOM chrome.
        # Arrange
        # Act
        # Assert
        assert (
            "Esc to blur" in board_text
        ), "search input must hint at 'Esc to blur' in its placeholder"

    def test_search_input_min_width_bumped(self, css_text):
        # The P1 CSS bump to 320px is what makes the search the PRIMARY
        # go-to. A regression to 180px (the pre-P1 narrow form) reverts
        # the affordance. (CSS pin — extracted to 02-card.css 2026-06-12.)
        # Arrange
        # Act
        # Assert
        assert "min-width: 320px" in css_text


# -----------------------------------------------------------------------------
# P7 — self-named project-umbrella card filter (PR #87)
# -----------------------------------------------------------------------------


class TestP7SelfNamedCard:
    """Pins for the PR #87 self-named project-umbrella filter."""

    def test_is_self_named_project_card_defined(self, board_text):
        # Arrange
        # Act
        # Assert
        assert "function isSelfNamedProjectCard" in board_text

    def test_filter_applied_in_render(self, board_text):
        # The fix is the call site inside render() — the function alone
        # doesn't help if the filter loop doesn't invoke it.
        # Arrange
        # Act
        # Assert
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
        # Arrange
        # Act
        # Assert
        assert ".slice(0, 12)" not in board_text

    def test_prompt_move_to_new_project_defined(self, board_text):
        # Arrange
        # Act
        # Assert
        assert "function promptMoveToNewProject" in board_text

    def test_new_project_button_text_present(self, board_text):
        # The legacy "+ New project…" fallback button (used when the
        # scitex-ui Combobox is unavailable). Either spelling acceptable.
        # Arrange
        # Act
        # Assert
        assert "New project" in board_text


# -----------------------------------------------------------------------------
# P2 + P9 — filter UX collapse + sort-by (PR #89)
# -----------------------------------------------------------------------------


class TestP2P9FilterAndSort:
    """Pins for the PR #89 filter-UX collapse + sort-by control."""

    def test_filter_popover_class_present(self, css_text):
        # CSS pin — extracted to 04-collapse-and-groups.css 2026-06-12.
        # Arrange
        # Act
        # Assert
        assert "filt-popover" in css_text

    def test_render_active_filter_chips_defined(self, board_text):
        # Arrange
        # Act
        # Assert
        assert "function renderActiveFilterChips" in board_text

    def test_clear_one_filter_defined(self, board_text):
        # Arrange
        # Act
        # Assert
        assert "function clearOneFilter" in board_text

    def test_state_sort_field_present(self, board_text):
        # Arrange
        # Act
        # Assert
        assert "STATE.sort" in board_text

    def test_sort_comparator_defined(self, board_text):
        # Arrange
        # Act
        # Assert
        assert "function _sortComparator" in board_text


# -----------------------------------------------------------------------------
# P10 — GROUPS (PR #91)
# -----------------------------------------------------------------------------


class TestP10Groups:
    """Pins for the PR #91 project-groups feature."""

    def test_state_group_by_present(self, board_text):
        # Arrange
        # Act
        # Assert
        assert "STATE.groupBy" in board_text

    def test_render_group_strip_defined(self, board_text):
        # Arrange
        # Act
        # Assert
        assert "function renderGroupStrip" in board_text

    def test_apply_group_clustering_defined(self, board_text):
        # Arrange
        # Act
        # Assert
        assert "function _applyGroupClustering" in board_text

    def test_group_spans_all_mount_present(self, board_text):
        # The spans_all banner mounts here above the columns grid.
        # Arrange
        # Act
        # Assert
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
        # Arrange
        # Act
        # Assert
        assert "t.deadline" in board_text


class TestP4MultiRecurringFE:
    """Pins for the P4 PR3 multi/recurring FE consumer."""

    def test_date_info_reads_deadline_next(self, board_text):
        # The server expands recurring + multi to a single
        # `deadline_next` ISO; the FE must prefer it over `deadline`
        # when present.
        # Arrange
        # Act
        # Assert
        assert "t.deadline_next" in board_text

    def test_extract_repeater_suffix_helper_defined(self, board_text):
        # Arrange
        # Act
        # Assert
        assert "function _extractRepeaterSuffix" in board_text

    def test_first_recurring_deadline_helper_defined(self, board_text):
        # Arrange
        # Act
        # Assert
        assert "function _firstRecurringDeadline" in board_text


# -----------------------------------------------------------------------------
# P11b — Combobox consumer (PR #94)
# -----------------------------------------------------------------------------


class TestP11bComboboxConsumer:
    """Pins for the PR #94 Combobox layer + Combobox-driven move-picker."""

    def test_combobox_css_static_load(self, board_text):
        # Arrange
        # Act
        # Assert
        assert "combobox.css" in board_text

    def test_combobox_js_static_load(self, board_text):
        # Arrange
        # Act
        # Assert
        assert "combobox.js" in board_text

    def test_attach_combobox_defined(self, board_text):
        # Arrange
        # Act
        # Assert
        assert "function attachCombobox" in board_text

    def test_open_move_to_combobox_defined(self, board_text):
        # Arrange
        # Act
        # Assert
        assert "function _openMoveToCombobox" in board_text


# -----------------------------------------------------------------------------
# Search qualifier syntax (operator TG 12315/12316, lead a2a 7dde227a)
# -----------------------------------------------------------------------------


class TestSearchQualifierSyntax:
    """Pins for the GitHub-style ``project:foo`` / ``status:blocked`` /
    ``kind:compute`` search syntax. The operator was photographed typing
    ``project: paper-scitex-clew`` (TG 12315/12316) and expected the
    qualifier to filter — i.e. they assumed GitHub-style behaviour. This
    test class makes sure the wiring stays in.
    """

    def test_search_query_js_static_load(self, board_text):
        # The parser ships as static/scitex_todo/board_v3/searchQuery.js
        # — board_v3.html must pull it in via {% static %}.
        # Arrange
        # Act
        # Assert
        assert "board_v3/searchQuery.js" in board_text

    def test_render_qualifier_hints_defined(self, board_text):
        # The hint-pill renderer must exist in the page logic.
        # Arrange
        # Act
        # Assert
        assert "function renderQualifierHints" in board_text

    def test_hint_pill_container_present(self, board_text):
        # And the <div id="filt-qhints"> the renderer writes into.
        # Arrange
        # Act
        # Assert
        assert 'id="filt-qhints"' in board_text

    def test_search_input_advertises_qualifier_syntax_board_text_contains(
        self, board_text
    ):
        # Placeholder + title should mention the new qualifier syntax so
        # the operator's expectation (GitHub-style) is met without docs.
        # Arrange
        # Act
        # Assert
        assert "project:" in board_text

    def test_search_input_advertises_qualifier_syntax_board_text_contains_2(
        self, board_text
    ):
        # Placeholder + title should mention the new qualifier syntax so
        # the operator's expectation (GitHub-style) is met without docs.
        # Arrange
        # Act
        # Assert
        assert "status:" in board_text

    def test_fuzzy_match_delegates_to_parser(self, board_text):
        # Sanity pin: the fuzzy-match function must consult
        # window.STX.searchQuery so a future squash that strips the
        # delegation reverts the operator pain.
        # Arrange
        # Act
        # Assert
        assert "window.STX.searchQuery" in board_text

    def test_hint_pill_css_defined(self, css_text):
        # CSS pin — `.filt-qhint` lives in the extracted filterbar stylesheet.
        # Arrange
        # Act
        # Assert
        assert ".filt-qhint" in css_text


# -----------------------------------------------------------------------------
# PR(h) Stage 1 — multi-select + bulk status change
# (board card todo-multiselect-batch-ops, lead a2a 1ebc792c)
# -----------------------------------------------------------------------------


class TestMultiselectBatchOpsStage1:
    """Pins for the PR(h) Stage 1 per-row multi-select + bulk status feature.

    Stage 1 ships ONE bulk op (status change). The other 4 (project
    re-assign / agent re-assign / bulk nudge / bulk hide) come in
    follow-up PRs — this class deliberately does NOT pin them.
    """

    def test_card_select_checkbox_in_card_html(self, board_text):
        # cardHtml(t) must render a per-row checkbox with the
        # `card-select` class so the toolbar can detect / batch-toggle.
        # Arrange
        # Act
        # Assert
        assert 'class="card-select"' in board_text

    def test_card_select_carries_data_task_id(self, board_text):
        # The bulk-action loop walks selected ids — without
        # data-task-id, select-all has nothing to read.
        # Arrange
        # Act
        # Assert
        assert "data-task-id=" in board_text

    def test_multiselect_state_is_a_set_board_text_contains(self, board_text):
        # Selection state is client-side only in window.MULTISELECT.
        # The `new Set()` literal is the load-bearing primitive.
        # Arrange
        # Act
        # Assert
        assert "window.MULTISELECT" in board_text

    def test_multiselect_state_is_a_set_board_text_contains_2(self, board_text):
        # Selection state is client-side only in window.MULTISELECT.
        # The `new Set()` literal is the load-bearing primitive.
        # Arrange
        # Act
        # Assert
        assert "new Set()" in board_text

    def test_toggle_card_selected_helper_defined(self, board_text):
        # Arrange
        # Act
        # Assert
        assert "function toggleCardSelected" in board_text

    def test_toggle_select_all_helper_defined(self, board_text):
        # Arrange
        # Act
        # Assert
        assert "function toggleSelectAll" in board_text

    def test_clear_multiselect_helper_defined(self, board_text):
        # Arrange
        # Act
        # Assert
        assert "function clearMultiselect" in board_text

    def test_bulk_set_status_helper_defined(self, board_text):
        # Arrange
        # Act
        # Assert
        assert "async function bulkSetStatus" in board_text

    def test_bulk_set_status_hits_update_endpoint(self, board_text):
        # v1 reuses the existing single-card /update endpoint in a
        # per-task loop. A future /bulk endpoint can swap in without
        # touching this pin (which only guards against the loop being
        # accidentally dropped).
        # Arrange
        # Act
        # Assert
        assert '"/update"' in board_text

    def test_board_toolbar_mount_present(self, board_text):
        # The toolbar mounts above the columns grid (next to
        # group-spans-all). The id is what renderBoardToolbar() reads.
        # Arrange
        # Act
        # Assert
        assert 'id="board-toolbar"' in board_text

    def test_board_toolbar_select_all_input_present(self, board_text):
        # Arrange
        # Act
        # Assert
        assert 'id="board-toolbar-select-all"' in board_text

    def test_board_toolbar_count_span_present(self, board_text):
        # Arrange
        # Act
        # Assert
        assert 'id="board-toolbar-count"' in board_text

    def test_board_toolbar_status_dropdown_present(self, board_text):
        # Arrange
        # Act
        # Assert
        assert 'id="board-toolbar-status"' in board_text

    def test_board_toolbar_status_options_cover_valid_statuses(self, board_text):
        # The 7-status enum used by the right-click ctx menu —
        # bulk should offer the same set so the operator sees parity.
        # Arrange
        # Act
        # Assert
        for status in (
            "pending",
            "in_progress",
            "blocked",
            "done",
            "deferred",
            "failed",
            "goal",
        ):
            assert f'value="{status}"' in board_text

    def test_toolbar_followup_ops_left_as_commented_hooks(self, board_text):
        # The card asks for 5 bulk ops. Stage 1 ships ONE; the other 4
        # are deliberately left as a TODO sentinel for the follow-up
        # PR. The pin guarantees the next PR has a discoverable
        # landing site.
        # Arrange
        # Act
        # Assert
        assert "TODO(PR-h+1)" in board_text

    def test_board_toolbar_css_class_defined(self, css_text):
        # CSS pin — `.board-toolbar` lives in the extracted layout
        # stylesheet alongside the other board-level chrome.
        # Arrange
        # Act
        # Assert
        assert ".board-toolbar" in css_text

    def test_card_select_css_class_defined(self, css_text):
        # Arrange
        # Act
        # Assert
        assert ".card-select" in css_text


# -----------------------------------------------------------------------------
# Activity bucket badge — render side of working-status decay
# (board card `scitex-todo-working-status-decay-tg12739`, render half of PR #122)
# -----------------------------------------------------------------------------


class TestActivityBucketBadge:
    """Pins for the per-card activity-bucket badge.

    Backend half (PR #122) added the working/stale/active/idle decay
    derivation in ``_build_fleet``. This render-side feature surfaces
    the same RECENCY signal on each card via a tiny dot badge whose
    bucket is computed from ``t.last_activity`` freshness (hours):

      fresh : <= 1 h   -- bright green  (live activity)
      warm  : 1-24 h   -- amber         (recent but quieting)
      stale : > 24 h   -- muted grey    (decayed)

    Fixes the operator pain "manual working color stays lit
    indefinitely" (TG 12739) on a per-card basis.
    """

    def test_activity_badge_html_defined(self, board_text):
        # The render function must exist.
        # Arrange
        # Act
        # Assert
        assert "function activityBadgeHtml" in board_text

    def test_activity_bucket_helper_defined(self, board_text):
        # Bucketing is its own helper so the wired-in call stays clean.
        # Arrange
        # Act
        # Assert
        assert "function _activityBucket" in board_text

    def test_activity_hours_helper_defined(self, board_text):
        # Time-since helper reads `t.last_activity` and returns hours.
        # Arrange
        # Act
        # Assert
        assert "function _activityHoursSince" in board_text

    def test_activity_badge_wired_into_card_top(self, board_text):
        # The badge must actually render on each card, not just be defined.
        # A regression that ships only the helper without the call site
        # silently hides the feature.
        # Arrange
        # Act
        # Assert
        assert "${activityBadgeHtml(t)}" in board_text

    def test_activity_badge_reads_last_activity_field(self, board_text):
        # The derivation must read the PR #122 schema field, not invent
        # a new one. Pinning the field name keeps the FE in sync with
        # the backend (`_build_fleet` precedence rules).
        # Arrange
        # Act
        # Assert
        assert "t.last_activity" in board_text

    def test_activity_badge_css_defined(self, css_text):
        # CSS pin — `.activity-badge` lives in the extracted card stylesheet.
        # Arrange
        # Act
        # Assert
        assert ".activity-badge" in css_text

    def test_activity_badge_fresh_modifier_present(self, css_text):
        # Arrange
        # Act
        # Assert
        assert ".activity-badge--fresh" in css_text

    def test_activity_badge_warm_modifier_present(self, css_text):
        # Arrange
        # Act
        # Assert
        assert ".activity-badge--warm" in css_text

    def test_activity_badge_stale_modifier_present(self, css_text):
        # Arrange
        # Act
        # Assert
        assert ".activity-badge--stale" in css_text


# -----------------------------------------------------------------------------
# Stale Review FE panel — operator-requested recurring stale-cards review
# (operator via lead a2a 2026-06-13; backend half is PR #153)
# -----------------------------------------------------------------------------


class TestStaleReviewPanel:
    """Pins for the Stale Review layout + Archive button.

    Backend half is PR #153 (/stale + /archive endpoints). This FE
    half adds a 4th layout button ("🧹 Stale"), a fetch+render
    function that pulls from /stale, a per-row Archive button that
    POSTs to /archive (HTTP twin of CLI `close --reason` PR #151),
    and a small toolbar with the days + include_no_timestamp knobs.
    """

    def test_stale_layout_button_in_filterbar(self, board_text):
        # Arrange
        # Act
        # Assert
        assert 'id="f-layout-stale"' in board_text

    def test_stale_layout_button_glyph(self, board_text):
        # Arrange
        # Act
        # Assert
        assert "🧹 Stale" in board_text

    def test_stale_render_helper_defined(self, board_text):
        # Arrange
        # Act
        # Assert
        assert "function _renderStaleView" in board_text

    def test_stale_render_dispatched_from_render(self, board_text):
        # Arrange
        # Act
        # Assert
        assert "_renderStaleView(canvas)" in board_text

    def test_stale_fetch_target_endpoint(self, board_text):
        # Arrange
        # Act
        # Assert
        assert '"/scitex-todo/stale?"' in board_text

    def test_archive_helper_defined(self, board_text):
        # Arrange
        # Act
        # Assert
        assert "async function archiveStaleCard" in board_text

    def test_archive_post_target_endpoint(self, board_text):
        # Arrange
        # Act
        # Assert
        assert '"/scitex-todo/archive"' in board_text

    def test_archive_requires_reason(self, board_text):
        # Arrange
        # Act
        # Assert
        assert "Archive requires a non-empty reason" in board_text

    def test_stale_toolbar_days_input_present(self, board_text):
        # Arrange
        # Act
        # Assert
        assert 'id="stale-days"' in board_text

    def test_stale_toolbar_include_no_timestamp_checkbox_present(self, board_text):
        # Arrange
        # Act
        # Assert
        assert 'id="stale-incnotime"' in board_text

    def test_stale_wrap_css_class_defined(self, css_text):
        # Arrange
        # Act
        # Assert
        assert ".stale-wrap" in css_text

    def test_stale_archive_btn_css_class_defined(self, css_text):
        # Arrange
        # Act
        # Assert
        assert ".stale-archive-btn" in css_text


# EOF
