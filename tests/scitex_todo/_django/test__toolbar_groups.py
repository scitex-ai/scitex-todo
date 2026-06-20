"""CSS + template contract tests for the toolbar declutter (board UI overhaul
part 2).

Operator complaint via lead a2a `d1af161e` (2026-06-14): the scitex-todo
board filterbar was overcrowded — Search, Filters, Layout toggle, Sort,
"N new" badge, Group, Reload, +Add Task, hide-project all crammed into
one row and visually collided.

The fix is a structural reorganization of the .filterbar children into
three logical groups + a primary action zone, each with breathing room
and a subtle vertical divider:

  .stx-todo-filterbar__group--view     — Layout + Sort + Group
  .stx-todo-filterbar__group--search   — Search + Filters popover
  .stx-todo-filterbar__group--status   — "N new" + Reload + hide-project
  .stx-todo-filterbar__primary         — + Add Task (brand-accent)
  .stx-todo-filterbar__divider         — vertical separator between groups

Visual verification is the operator's job; this module pins the
LOAD-BEARING CONTRACT — selectors, responsive media query, no hardcoded
colors in the new toolbar CSS block. Mocks-free: open the source files
and grep them.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


# --- repo paths -----------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[3]

_TOOLBAR_CSS = (
    _REPO_ROOT
    / "src"
    / "scitex_todo"
    / "_django"
    / "static"
    / "scitex_todo"
    / "board_v3"
    / "07-toolbar-groups.css"
)
_BOARD_V3_TEMPLATE = (
    _REPO_ROOT
    / "src"
    / "scitex_todo"
    / "_django"
    / "templates"
    / "scitex_todo"
    / "board_v3.html"
)


def _read(path: Path) -> str:
    assert path.is_file(), f"expected file missing: {path}"
    return path.read_text(encoding="utf-8")


# ============================================================================
# Half A — CSS contract: every required selector is present
# ============================================================================


@pytest.mark.parametrize(
    "selector",
    [
        ".stx-todo-filterbar__group--view",
        ".stx-todo-filterbar__group--search",
        ".stx-todo-filterbar__group--status",
        ".stx-todo-filterbar__primary",
        ".stx-todo-filterbar__divider",
    ],
)
def test_toolbar_css_declares_group_selector(selector: str) -> None:
    """Every new group / primary / divider class must have at least one
    CSS rule in 07-toolbar-groups.css."""
    # Arrange
    # Act
    css = _read(_TOOLBAR_CSS)
    # Assert
    assert selector in css, f"07-toolbar-groups.css missing rule for {selector!r}"


def test_toolbar_css_responsive_media_query_present_m() -> None:
    """A media query for the narrow-viewport wrap (≤780px) must be
    declared so each group drops to its own row gracefully."""
    # Arrange
    css = _read(_TOOLBAR_CSS)
    # Accept any max-width up to 800px so a future bump to 768/800 still
    # passes — the contract is "there is a responsive wrap rule", not the
    # exact pixel value.
    # Act
    m = re.search(r"@media\s*\([^)]*max-width\s*:\s*(\d+)px\)", css)
    # Assert
    px = int(m.group(1))
    assert m, "07-toolbar-groups.css missing @media (max-width: …px) rule"


def test_toolbar_css_responsive_media_query_present_case_2() -> None:
    """A media query for the narrow-viewport wrap (≤780px) must be
    declared so each group drops to its own row gracefully."""
    # Arrange
    css = _read(_TOOLBAR_CSS)
    # Accept any max-width up to 800px so a future bump to 768/800 still
    # passes — the contract is "there is a responsive wrap rule", not the
    # exact pixel value.
    # Act
    m = re.search(r"@media\s*\([^)]*max-width\s*:\s*(\d+)px\)", css)
    # Assert
    px = int(m.group(1))
    assert 700 <= px <= 800, (
        f"responsive wrap breakpoint out of range: got {px}px, "
        f"expected ~780px per operator spec"
    )


def test_toolbar_css_no_hardcoded_colors() -> None:
    """Every color in the new toolbar CSS must source from a `var(--…)`
    token. The only acceptable hex literals are inside `var(--token,
    #fallback)` fallback slots; bare `#fff` / `white` / raw hex
    assignments are forbidden."""
    # Arrange
    css = _read(_TOOLBAR_CSS)
    # Strip block comments first so explanatory prose ("white in dark mode")
    # doesn't trigger the guard.
    comments_stripped = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    # Strip every `var(--token, #fallback)` substring so the fallback hex
    # inside a var() call doesn't get caught — that's the documented
    # token-default pattern used across the codebase.
    no_var = re.sub(r"var\([^)]*\)", "", comments_stripped, flags=re.DOTALL)
    # Forbidden literals: `#fff` / `#ffffff` / `white` outside of
    # property names. Use a negative lookbehind to keep `white-space:`
    # property names safe.
    pattern = re.compile(
        r"(?<![\w-])" r"(#[0-9a-fA-F]{3,8}\b|\bwhite\b(?!-))",
    )
    # Act
    matches = pattern.findall(no_var)
    # Assert
    assert not matches, (
        f"hardcoded colors found in 07-toolbar-groups.css "
        f"(must use var(--…) tokens): {matches[:5]}"
    )


def test_toolbar_css_balanced_braces() -> None:
    """Edits did not corrupt brace nesting."""
    # Arrange
    # Act
    css = _read(_TOOLBAR_CSS)
    # Assert
    assert css.count("{") == css.count("}"), (
        f"unbalanced braces in {_TOOLBAR_CSS}: "
        f"{css.count('{')} opens vs {css.count('}')} closes"
    )


# ============================================================================
# Half B — template / HTML uses each new group class
# ============================================================================


@pytest.mark.parametrize(
    "selector_class",
    [
        "stx-todo-filterbar__group--view",
        "stx-todo-filterbar__group--search",
        "stx-todo-filterbar__group--status",
        "stx-todo-filterbar__primary",
        "stx-todo-filterbar__divider",
    ],
)
def test_template_renders_group_class(selector_class: str) -> None:
    """board_v3.html must mount each new group / primary / divider class
    on at least one element so the CSS rules actually find targets."""
    # Arrange
    # Act
    html = _read(_BOARD_V3_TEMPLATE)
    # Assert
    assert (
        selector_class in html
    ), f"board_v3.html missing class {selector_class!r} on any element"


def test_template_loads_toolbar_css() -> None:
    """The toolbar declutter CSS must be wired into the template."""
    # Arrange
    # Act
    html = _read(_BOARD_V3_TEMPLATE)
    # Assert
    assert (
        "07-toolbar-groups.css" in html
    ), "board_v3.html never <link>s 07-toolbar-groups.css"


def test_template_view_group_contains_layout_sort_group_m() -> None:
    """The VIEW group must wrap the Layout, Sort, and Group controls so
    the existing behavior is unchanged — only the structural wrapper
    moves."""
    # Arrange
    html = _read(_BOARD_V3_TEMPLATE)
    # Find the VIEW group <div>; capture its inner content up to the
    # next end-of-group comment.
    # Act
    m = re.search(
        r"stx-todo-filterbar__group--view[^>]*>(.*?)\{#\s*end VIEW group",
        html,
        flags=re.DOTALL,
    )
    # Assert
    view_body = m.group(1)
    assert m, "could not locate VIEW group block in template"


def test_template_view_group_contains_layout_sort_group_view_body_contains() -> None:
    """The VIEW group must wrap the Layout, Sort, and Group controls so
    the existing behavior is unchanged — only the structural wrapper
    moves."""
    # Arrange
    html = _read(_BOARD_V3_TEMPLATE)
    # Find the VIEW group <div>; capture its inner content up to the
    # next end-of-group comment.
    # Act
    m = re.search(
        r"stx-todo-filterbar__group--view[^>]*>(.*?)\{#\s*end VIEW group",
        html,
        flags=re.DOTALL,
    )
    # Assert
    view_body = m.group(1)
    assert 'class="filt-layout"' in view_body, "VIEW group missing Layout"


def test_template_view_group_contains_layout_sort_group_view_body_contains_2() -> None:
    """The VIEW group must wrap the Layout, Sort, and Group controls so
    the existing behavior is unchanged — only the structural wrapper
    moves."""
    # Arrange
    html = _read(_BOARD_V3_TEMPLATE)
    # Find the VIEW group <div>; capture its inner content up to the
    # next end-of-group comment.
    # Act
    m = re.search(
        r"stx-todo-filterbar__group--view[^>]*>(.*?)\{#\s*end VIEW group",
        html,
        flags=re.DOTALL,
    )
    # Assert
    view_body = m.group(1)
    assert 'class="filt-sort"' in view_body, "VIEW group missing Sort"


def test_template_view_group_contains_layout_sort_group_view_body_contains_3() -> None:
    """The VIEW group must wrap the Layout, Sort, and Group controls so
    the existing behavior is unchanged — only the structural wrapper
    moves."""
    # Arrange
    html = _read(_BOARD_V3_TEMPLATE)
    # Find the VIEW group <div>; capture its inner content up to the
    # next end-of-group comment.
    # Act
    m = re.search(
        r"stx-todo-filterbar__group--view[^>]*>(.*?)\{#\s*end VIEW group",
        html,
        flags=re.DOTALL,
    )
    # Assert
    view_body = m.group(1)
    assert 'class="filt-groupby"' in view_body, "VIEW group missing Group"


def test_template_primary_zone_contains_add_task_m() -> None:
    """The PRIMARY action zone must wrap the +Add Task button so it pops
    as the right-most, brand-accent element."""
    # Arrange
    html = _read(_BOARD_V3_TEMPLATE)
    # Act
    m = re.search(
        r"stx-todo-filterbar__primary[^>]*>(.*?)\{#\s*end PRIMARY",
        html,
        flags=re.DOTALL,
    )
    # Assert
    primary_body = m.group(1)
    assert m, "could not locate PRIMARY zone block in template"


def test_template_primary_zone_contains_add_task_primary_body_contains() -> None:
    """The PRIMARY action zone must wrap the +Add Task button so it pops
    as the right-most, brand-accent element."""
    # Arrange
    html = _read(_BOARD_V3_TEMPLATE)
    # Act
    m = re.search(
        r"stx-todo-filterbar__primary[^>]*>(.*?)\{#\s*end PRIMARY",
        html,
        flags=re.DOTALL,
    )
    # Assert
    primary_body = m.group(1)
    assert (
        'id="add-task-btn"' in primary_body
    ), "PRIMARY zone missing the +Add Task button"


def test_template_status_group_contains_recent_reload_hideproject_m() -> None:
    """The STATUS group must wrap the 'N new' recent-count pill, Reload
    button, and hide-project toggle so the operator's status-row stays
    together."""
    # Arrange
    html = _read(_BOARD_V3_TEMPLATE)
    # Act
    m = re.search(
        r"stx-todo-filterbar__group--status[^>]*>(.*?)\{#\s*end STATUS group",
        html,
        flags=re.DOTALL,
    )
    # Assert
    status_body = m.group(1)
    assert m, "could not locate STATUS group block in template"


def test_template_status_group_contains_recent_reload_hideproject_status_body_contains() -> (
    None
):
    """The STATUS group must wrap the 'N new' recent-count pill, Reload
    button, and hide-project toggle so the operator's status-row stays
    together."""
    # Arrange
    html = _read(_BOARD_V3_TEMPLATE)
    # Act
    m = re.search(
        r"stx-todo-filterbar__group--status[^>]*>(.*?)\{#\s*end STATUS group",
        html,
        flags=re.DOTALL,
    )
    # Assert
    status_body = m.group(1)
    assert (
        'id="recent-count-pill"' in status_body
    ), "STATUS group missing the 'N new in 24 h' pill"


def test_template_status_group_contains_recent_reload_hideproject_status_body_contains_2() -> (
    None
):
    """The STATUS group must wrap the 'N new' recent-count pill, Reload
    button, and hide-project toggle so the operator's status-row stays
    together."""
    # Arrange
    html = _read(_BOARD_V3_TEMPLATE)
    # Act
    m = re.search(
        r"stx-todo-filterbar__group--status[^>]*>(.*?)\{#\s*end STATUS group",
        html,
        flags=re.DOTALL,
    )
    # Assert
    status_body = m.group(1)
    assert 'id="reload"' in status_body, "STATUS group missing Reload"


def test_template_status_group_contains_recent_reload_hideproject_status_body_contains_3() -> (
    None
):
    """The STATUS group must wrap the 'N new' recent-count pill, Reload
    button, and hide-project toggle so the operator's status-row stays
    together."""
    # Arrange
    html = _read(_BOARD_V3_TEMPLATE)
    # Act
    m = re.search(
        r"stx-todo-filterbar__group--status[^>]*>(.*?)\{#\s*end STATUS group",
        html,
        flags=re.DOTALL,
    )
    # Assert
    status_body = m.group(1)
    assert (
        'id="proj-hide-wrap"' in status_body
    ), "STATUS group missing hide-project toggle"


def test_template_search_group_contains_search_and_filters_m() -> None:
    """The SEARCH/FILTER group must wrap the search input and the
    Filters popover so query-narrowing controls cluster."""
    # Arrange
    html = _read(_BOARD_V3_TEMPLATE)
    # Act
    m = re.search(
        r"stx-todo-filterbar__group--search[^>]*>(.*?)\{#\s*end " r"\.fb-center",
        html,
        flags=re.DOTALL,
    )
    # Assert
    search_body = m.group(1)
    assert m, "could not locate SEARCH/FILTER group block in template"


def test_template_search_group_contains_search_and_filters_search_body_contains() -> (
    None
):
    """The SEARCH/FILTER group must wrap the search input and the
    Filters popover so query-narrowing controls cluster."""
    # Arrange
    html = _read(_BOARD_V3_TEMPLATE)
    # Act
    m = re.search(
        r"stx-todo-filterbar__group--search[^>]*>(.*?)\{#\s*end " r"\.fb-center",
        html,
        flags=re.DOTALL,
    )
    # Assert
    search_body = m.group(1)
    assert 'id="f-search"' in search_body, "SEARCH group missing search input"


def test_template_search_group_contains_search_and_filters_search_body_contains_2() -> (
    None
):
    """The SEARCH/FILTER group must wrap the search input and the
    Filters popover so query-narrowing controls cluster."""
    # Arrange
    html = _read(_BOARD_V3_TEMPLATE)
    # Act
    m = re.search(
        r"stx-todo-filterbar__group--search[^>]*>(.*?)\{#\s*end " r"\.fb-center",
        html,
        flags=re.DOTALL,
    )
    # Assert
    search_body = m.group(1)
    assert (
        'id="filt-popover-wrap"' in search_body
    ), "SEARCH group missing Filters popover"


# ============================================================================
# Behavior preservation — every original toolbar control still exists
# ============================================================================


@pytest.mark.parametrize(
    "needle",
    [
        'id="f-search"',  # Search input
        'id="filt-popover-wrap"',  # Filters popover
        'id="f-layout-graph"',  # Layout: Graph
        'id="f-layout-column"',  # Layout: Column
        'id="f-layout-table"',  # Layout: Table
        'id="f-sort"',  # Sort
        'id="recent-count-pill"',  # 🆕 N new pill
        'id="f-groupby"',  # Group
        'id="reload"',  # Reload
        'id="add-task-btn"',  # + Add Task
        'id="proj-hide-wrap"',  # hide-project toggle
        'id="t-block"',  # 🚧 blocking me
        'id="hidden-wrap"',  # 👁 hidden
    ],
)
def test_template_preserves_every_original_control(needle: str) -> None:
    """The reorg moves controls into wrapper <div>s — it must NOT delete
    any of them. This guards against an accidental drop during the
    structural shuffle."""
    # Arrange
    # Act
    html = _read(_BOARD_V3_TEMPLATE)
    # Assert
    assert needle in html, f"board_v3.html lost an original toolbar control: {needle!r}"
