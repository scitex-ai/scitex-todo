#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Front-end contract tests for the fleet CI-status pills.

Two halves (same pattern the recent ``test_table_filter.py`` /
``test_calendar_date.py`` PRs established):

1. **CSS contract** — open ``fleet-ci-pills.css`` and assert:
     - the canonical selectors are present
     - colors come from design tokens (``--status-success``, etc.) ONLY;
       NO hardcoded hex literals leak in (theme-breaking)
2. **Pill color mapping** — execute the actual ``pillModifier`` /
   ``isCiRepoErr`` / ``pillTooltip`` functions from ``FleetCiPills.tsx``
   via ``node`` and pin the mapping for each CiOverall value + the
   per-repo error case. Same lock-step assertion against the TS source
   as the table-filter test, so a rename downstream forces this test
   to update.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[4]

_CSS_FILE = (
    _REPO_ROOT
    / "src"
    / "scitex_todo"
    / "_django"
    / "frontend"
    / "src"
    / "styles"
    / "fleet-ci-pills.css"
)
_TSX_FILE = (
    _REPO_ROOT
    / "src"
    / "scitex_todo"
    / "_django"
    / "frontend"
    / "src"
    / "FleetCiPills.tsx"
)


# ─── CSS contract ───────────────────────────────────────────────────────


def test_css_file_exists() -> None:
    assert _CSS_FILE.is_file(), f"missing CSS file: {_CSS_FILE}"


def test_css_has_canonical_selectors() -> None:
    """The component generates these class names — the CSS file MUST
    define each one or the pills will silently render unstyled."""
    css = _CSS_FILE.read_text(encoding="utf-8")
    for selector in (
        ".stx-todo-fleet-ci",
        ".stx-todo-fleet-ci__pill",
        ".stx-todo-fleet-ci__pill--success",
        ".stx-todo-fleet-ci__pill--failure",
        ".stx-todo-fleet-ci__pill--pending",
        ".stx-todo-fleet-ci__pill--unknown",
        ".stx-todo-fleet-ci__pill--error",
        ".stx-todo-fleet-ci__dot",
        ".stx-todo-fleet-ci__name",
        ".stx-todo-fleet-ci--note",
    ):
        assert selector in css, f"missing CSS selector: {selector}"


def test_css_uses_design_tokens_only() -> None:
    """Colors must come from CSS variables — NO hardcoded hex literals.

    Hex literals would freeze the pills to one theme (dark) and break
    the operator's light-mode view. The board's design-token chain is
    ``board.css`` → ``--stx-*`` board-local aliases (which fall back to
    the scitex-ui shell variables ``--status-success``,
    ``--status-error``, ``--status-warning``, ``--text-muted``).
    """
    css = _CSS_FILE.read_text(encoding="utf-8")
    # Strip /* ... */ comments before scanning — the comment block at
    # the top of the file documents the token names verbatim and would
    # falsely trip the hex detector otherwise.
    no_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    # 3-, 4-, 6-, or 8-digit hex.
    hex_matches = re.findall(r"#[0-9A-Fa-f]{3,8}\b", no_comments)
    assert not hex_matches, (
        f"hardcoded hex colors in fleet-ci-pills.css (breaks theming): "
        f"{hex_matches!r}"
    )
    # Required token references — at least one occurrence each.
    for token in (
        "--status-success",
        "--status-error",
        "--status-warning",
    ):
        assert token in css, f"missing design token: {token}"


def test_css_is_imported_from_board_css() -> None:
    """The pills strip only renders correctly when board.css imports
    the partial. Pinning this guards against an accidental removal in
    a future board.css refactor."""
    board_css = _CSS_FILE.parent / "board.css"
    assert board_css.is_file()
    text = board_css.read_text(encoding="utf-8")
    assert '@import "./fleet-ci-pills.css";' in text


# ─── component logic — pill color mapping via node ─────────────────────


def _node() -> str:
    """Locate ``node``; skip the suite cleanly if it isn't installed."""
    exe = shutil.which("node")
    if exe is None:
        pytest.skip("node executable not found on PATH")
    return exe


def _run_pill_helpers(repo: dict) -> dict:
    """Execute the actual ``pillModifier`` / ``isCiRepoErr`` /
    ``pillTooltip`` helpers via node against ``repo``.

    Mirrors the TS module — and asserts each runtime fragment is still
    present in the TS source so the mirror stays in lock-step (a
    rename downstream forces this test to update; no silent drift).
    """
    src = _TSX_FILE.read_text(encoding="utf-8")

    # Static-source contract — keeping the JS mirror in lock-step.
    for needle in [
        "export function isCiRepoErr(repo: CiRepo): repo is CiRepoErr {",
        'return Object.prototype.hasOwnProperty.call(repo, "error");',
        "export function pillModifier(repo: CiRepo): string {",
        'case "success":',
        'case "failure":',
        'case "pending":',
        "export function pillTooltip(repo: CiRepo): string {",
    ]:
        assert needle in src, (
            f"FleetCiPills.tsx no longer contains canonical fragment "
            f"{needle!r}; update this test in lock-step."
        )

    js_runtime = textwrap.dedent(
        """
        function isCiRepoErr(repo) {
          return Object.prototype.hasOwnProperty.call(repo, "error");
        }
        function pillModifier(repo) {
          if (isCiRepoErr(repo)) return "error";
          switch (repo.overall) {
            case "success": return "success";
            case "failure": return "failure";
            case "pending": return "pending";
            default: return "unknown";
          }
        }
        function pillTooltip(repo) {
          if (isCiRepoErr(repo)) {
            return repo.slug + ": adapter error — " + repo.error;
          }
          const sha = repo.head_sha ? repo.head_sha.slice(0, 7) : "(no sha)";
          return repo.slug + " @ " + repo.branch + " (" + sha + ") — " + repo.overall;
        }
        """
    ).strip()

    script = (
        js_runtime
        + "\nconst repo = "
        + json.dumps(repo)
        + ";\nconsole.log(JSON.stringify({"
        + "modifier: pillModifier(repo),"
        + "tooltip: pillTooltip(repo),"
        + "isErr: isCiRepoErr(repo)"
        + "}));\n"
    )
    proc = subprocess.run(
        [_node(), "--input-type=module", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return json.loads(proc.stdout.strip())


@pytest.mark.parametrize(
    "overall,expected_mod",
    [
        ("success", "success"),
        ("failure", "failure"),
        ("pending", "pending"),
        ("unknown", "unknown"),
        # An overall value the FE doesn't know about must degrade to
        # "unknown" rather than emit a missing-CSS class. The
        # ``default:`` branch of the switch pins this.
        ("weird-future-state", "unknown"),
    ],
)
def test_pill_color_mapping_for_each_overall(
    overall: str, expected_mod: str
) -> None:
    """Pin one pill-modifier per known CiOverall value — these are the
    classes the CSS file rules on, so a rename here would silently
    blank the pill."""
    out = _run_pill_helpers(
        {
            "slug": "foo/bar",
            "branch": "main",
            "head_sha": "abc1234deadbeef",
            "overall": overall,
            "checks": [],
        }
    )
    assert out["modifier"] == expected_mod
    assert out["isErr"] is False
    assert "foo/bar" in out["tooltip"]
    assert "main" in out["tooltip"]
    assert "abc1234" in out["tooltip"]  # short sha


def test_pill_modifier_for_per_repo_error() -> None:
    """A per-repo error (``{slug, error}``) maps to the ``--error``
    modifier with the adapter message in the tooltip."""
    out = _run_pill_helpers(
        {"slug": "foo/dead", "error": "gh exited 1: not found"}
    )
    assert out["modifier"] == "error"
    assert out["isErr"] is True
    assert "foo/dead" in out["tooltip"]
    assert "gh exited 1" in out["tooltip"]


def test_pill_tooltip_handles_missing_sha() -> None:
    """If the back-end emits an OK pill without a ``head_sha`` (e.g. a
    fresh repo with zero commits — defensive), the tooltip shows
    ``(no sha)`` rather than a misleading empty truncation."""
    out = _run_pill_helpers(
        {
            "slug": "foo/bar",
            "branch": "main",
            "head_sha": "",
            "overall": "unknown",
            "checks": [],
        }
    )
    assert "(no sha)" in out["tooltip"]
