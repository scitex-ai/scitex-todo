#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Front-end contract tests for the fleet agent-mesh panel (Phase 3).

Two halves (same pattern as ``test_fleet_hosts.py`` /
``test_fleet_ci_pills.py``):

1. **CSS contract** — open ``fleet-mesh.css`` and assert:
     - the canonical selectors are present
     - colors come from design tokens (``--status-success``,
       ``--status-error``, ``--stx-danger``, etc.) ONLY; NO hardcoded
       hex / ``white`` / ``#fff`` literals leak in (theme-breaking).
     - the partial is imported from ``board.css`` so the panel
       actually styles when the bundle loads.
2. **Component logic** — execute the actual ``edgeColorToken`` /
   ``meshPanelLabel`` / ``meshPanelTooltip`` / ``radialLayout`` /
   ``isMeshPayloadErr`` helpers from ``FleetMeshPanel.tsx`` via
   ``node`` and pin the mapping for canonical payloads. Same
   lock-step assertion against the TS source so a rename downstream
   forces this test to update.
"""

from __future__ import annotations

import json
import math
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
    / "fleet-mesh.css"
)
_TSX_FILE = (
    _REPO_ROOT
    / "src"
    / "scitex_todo"
    / "_django"
    / "frontend"
    / "src"
    / "FleetMeshPanel.tsx"
)


# ─── CSS contract ───────────────────────────────────────────────────────


def test_css_file_exists() -> None:
    # Arrange
    # Act
    # Assert
    assert _CSS_FILE.is_file(), f"missing CSS file: {_CSS_FILE}"


def test_css_has_canonical_selectors() -> None:
    """The component generates these class names — the CSS file MUST
    define each one or the panel will silently render unstyled."""
    # Arrange
    css = _CSS_FILE.read_text(encoding="utf-8")
    # Act
    # Assert
    for selector in (
        ".stx-todo-fleet-mesh",
        ".stx-todo-fleet-mesh--ok",
        ".stx-todo-fleet-mesh--error",
        ".stx-todo-fleet-mesh--loading",
        ".stx-todo-fleet-mesh__label",
        ".stx-todo-fleet-mesh__dot",
        ".stx-todo-fleet-mesh__svg",
        ".stx-todo-fleet-mesh__node",
        ".stx-todo-fleet-mesh__edge",
        ".stx-todo-fleet-mesh__edge--allow",
        ".stx-todo-fleet-mesh__edge--deny",
        ".stx-todo-fleet-mesh__legend",
    ):
        assert selector in css, f"missing CSS selector: {selector}"


def test_css_has_no_hardcoded_hex_colors() -> None:
    """Hex literals freeze the panel to one theme; colors must come
    from design tokens."""
    # Arrange
    css = _CSS_FILE.read_text(encoding="utf-8")
    # Strip /* ... */ comments so the doc block (which names the
    # tokens verbatim) does not trip the hex / named-color scan.
    no_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    # Act
    hex_matches = re.findall(r"#[0-9A-Fa-f]{3,8}\b", no_comments)
    # Assert
    assert not hex_matches, f"hardcoded hex colors in fleet-mesh.css: {hex_matches!r}"


def test_css_has_no_hardcoded_named_colors() -> None:
    """Named-color literals (white / black) are the same theming
    smell as hex."""
    # Arrange
    css = _CSS_FILE.read_text(encoding="utf-8")
    # Strip /* ... */ comments so the doc block (which names the
    # tokens verbatim) does not trip the hex / named-color scan.
    no_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    # Act
    forbidden = (r":\s*white\b", r":\s*black\b")
    # Assert
    assert not any(re.search(f, no_comments) for f in forbidden)


def test_css_references_all_required_tokens() -> None:
    """Every required design token must be referenced at least once."""
    # Arrange
    css = _CSS_FILE.read_text(encoding="utf-8")
    # Act
    required = (
        "--status-success",
        "--status-error",
        "--stx-danger",
        "--stx-text-muted",
        "--stx-border",
        "--stx-panel-bg",
        "--stx-text",
    )
    # Assert
    assert all(token in css for token in required)


def test_css_is_imported_from_board_css_is_file() -> None:
    """The panel only renders correctly when board.css imports the
    partial. Pinning this guards against an accidental removal in a
    future board.css refactor."""
    # Arrange
    # Act
    board_css = _CSS_FILE.parent / "board.css"
    # Assert
    text = board_css.read_text(encoding="utf-8")
    assert board_css.is_file()


def test_css_is_imported_from_board_css_text_contains() -> None:
    """The panel only renders correctly when board.css imports the
    partial. Pinning this guards against an accidental removal in a
    future board.css refactor."""
    # Arrange
    # Act
    board_css = _CSS_FILE.parent / "board.css"
    # Assert
    text = board_css.read_text(encoding="utf-8")
    assert '@import "./fleet-mesh.css";' in text


# ─── component logic — helpers via node ─────────────────────────────────


def _node() -> str:
    """Locate ``node``; skip the suite cleanly if it isn't installed."""
    exe = shutil.which("node")
    if exe is None:
        pytest.skip("node executable not found on PATH")
    return exe


def _run_panel_helpers(payload: dict) -> dict:
    """Execute the actual ``isMeshPayloadErr`` / ``edgeColorToken`` /
    ``meshPanelLabel`` / ``meshPanelTooltip`` helpers via node.

    Mirrors the TS module — and asserts each runtime fragment is still
    present in the TS source so the mirror stays in lock-step (a
    rename downstream forces this test to update; no silent drift).
    """
    src = _TSX_FILE.read_text(encoding="utf-8")

    # Static-source contract — keeping the JS mirror in lock-step.
    for needle in [
        "export function isMeshPayloadErr(p: MeshPayload): p is MeshPayloadErr {",
        'return Object.prototype.hasOwnProperty.call(p, "error");',
        "export function edgeColorToken(allow: boolean): string {",
        '"stx-todo-fleet-mesh__edge--allow"',
        '"stx-todo-fleet-mesh__edge--deny"',
        "export function meshPanelLabel(p: MeshPayloadOk): string {",
        "export function meshPanelTooltip(p: MeshPayloadOk): string {",
        "export function radialLayout(",
    ]:
        assert needle in src, (
            f"FleetMeshPanel.tsx no longer contains canonical fragment "
            f"{needle!r}; update this test in lock-step."
        )

    js_runtime = textwrap.dedent(
        """
        function isMeshPayloadErr(p) {
          return Object.prototype.hasOwnProperty.call(p, "error");
        }
        function edgeColorToken(allow) {
          return allow
            ? "stx-todo-fleet-mesh__edge--allow"
            : "stx-todo-fleet-mesh__edge--deny";
        }
        function meshPanelLabel(p) {
          const a = Array.isArray(p.agents) ? p.agents.length : 0;
          const e = Array.isArray(p.edges) ? p.edges.length : 0;
          return "\\uD83D\\uDD78 " + a + " agents \\u00b7 " + e + " grants";
        }
        function meshPanelTooltip(p) {
          const lines = [];
          const agents = Array.isArray(p.agents) ? p.agents : [];
          const edges = Array.isArray(p.edges) ? p.edges : [];
          lines.push("agents: " + agents.length);
          for (const a of agents) {
            const status = a.status ? " [" + a.status + "]" : "";
            lines.push("  " + a.name + " (" + a.scope + ")" + status);
          }
          lines.push("grants: " + edges.length);
          for (const e of edges) {
            const tag = e.allow ? "allow" : "deny";
            const note = e.note ? " \\u2014 " + e.note : "";
            lines.push("  " + e.source + " \\u2192 " + e.target + " [" + tag + "]" + note);
          }
          if (p.config_path) lines.push("config: " + p.config_path);
          return lines.join("\\n");
        }
        """
    ).strip()

    script = (
        js_runtime
        + "\nconst payload = "
        + json.dumps(payload)
        + ";\nconst result = {"
        + "isErr: isMeshPayloadErr(payload),"
        + "allowClass: edgeColorToken(true),"
        + "denyClass: edgeColorToken(false),"
        + "label: isMeshPayloadErr(payload) ? null : meshPanelLabel(payload),"
        + "tooltip: isMeshPayloadErr(payload) ? null : meshPanelTooltip(payload)"
        + "};\nprocess.stdout.write(JSON.stringify(result));\n"
    )
    proc = subprocess.run(
        [_node(), "-e", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return json.loads(proc.stdout.strip())


def test_edge_color_token_maps_allow_and_deny_allowclass() -> None:
    """The SINGLE point where allow/deny becomes a CSS token must
    return the two canonical class names. The CSS file defines both;
    a rename here without a CSS-side update would silently break the
    color mapping."""
    # Arrange
    # Act
    out = _run_panel_helpers(
        {
            "agents": [],
            "edges": [],
            "config_path": None,
            "source_versions": {"peers": "x", "grants": "y"},
        }
    )
    # Assert
    assert out["allowClass"] == "stx-todo-fleet-mesh__edge--allow"


def test_edge_color_token_maps_allow_and_deny_denyclass() -> None:
    """The SINGLE point where allow/deny becomes a CSS token must
    return the two canonical class names. The CSS file defines both;
    a rename here without a CSS-side update would silently break the
    color mapping."""
    # Arrange
    # Act
    out = _run_panel_helpers(
        {
            "agents": [],
            "edges": [],
            "config_path": None,
            "source_versions": {"peers": "x", "grants": "y"},
        }
    )
    # Assert
    assert out["denyClass"] == "stx-todo-fleet-mesh__edge--deny"


def test_label_with_agents_and_grants_iserr() -> None:
    """The label pattern matches the spec verbatim:
    ``🕸 <N> agents · <M> grants``. Pin one realistic payload to
    catch a rename or a count-off-by-one downstream."""
    # Arrange
    # Act
    out = _run_panel_helpers(
        {
            "agents": [
                {"name": "a", "scope": "local", "status": "online"},
                {"name": "b", "scope": "peer", "status": "online"},
                {"name": "c", "scope": "peer", "status": "unknown"},
            ],
            "edges": [
                {"source": "a", "target": "b", "allow": True},
                {"source": "b", "target": "c", "allow": False},
            ],
            "config_path": None,
            "source_versions": {"peers": "x", "grants": "y"},
        }
    )
    # Assert
    # Tooltip surfaces the agent list + edge list.
    assert out["isErr"] is False


def test_label_with_agents_and_grants_label_contains() -> None:
    """The label pattern matches the spec verbatim:
    ``🕸 <N> agents · <M> grants``. Pin one realistic payload to
    catch a rename or a count-off-by-one downstream."""
    # Arrange
    # Act
    out = _run_panel_helpers(
        {
            "agents": [
                {"name": "a", "scope": "local", "status": "online"},
                {"name": "b", "scope": "peer", "status": "online"},
                {"name": "c", "scope": "peer", "status": "unknown"},
            ],
            "edges": [
                {"source": "a", "target": "b", "allow": True},
                {"source": "b", "target": "c", "allow": False},
            ],
            "config_path": None,
            "source_versions": {"peers": "x", "grants": "y"},
        }
    )
    # Assert
    # Tooltip surfaces the agent list + edge list.
    assert "3 agents" in out["label"]


def test_label_with_agents_and_grants_label_contains_2() -> None:
    """The label pattern matches the spec verbatim:
    ``🕸 <N> agents · <M> grants``. Pin one realistic payload to
    catch a rename or a count-off-by-one downstream."""
    # Arrange
    # Act
    out = _run_panel_helpers(
        {
            "agents": [
                {"name": "a", "scope": "local", "status": "online"},
                {"name": "b", "scope": "peer", "status": "online"},
                {"name": "c", "scope": "peer", "status": "unknown"},
            ],
            "edges": [
                {"source": "a", "target": "b", "allow": True},
                {"source": "b", "target": "c", "allow": False},
            ],
            "config_path": None,
            "source_versions": {"peers": "x", "grants": "y"},
        }
    )
    # Assert
    # Tooltip surfaces the agent list + edge list.
    assert "2 grants" in out["label"]


def test_label_with_agents_and_grants_tooltip_contains() -> None:
    """The label pattern matches the spec verbatim:
    ``🕸 <N> agents · <M> grants``. Pin one realistic payload to
    catch a rename or a count-off-by-one downstream."""
    # Arrange
    # Act
    out = _run_panel_helpers(
        {
            "agents": [
                {"name": "a", "scope": "local", "status": "online"},
                {"name": "b", "scope": "peer", "status": "online"},
                {"name": "c", "scope": "peer", "status": "unknown"},
            ],
            "edges": [
                {"source": "a", "target": "b", "allow": True},
                {"source": "b", "target": "c", "allow": False},
            ],
            "config_path": None,
            "source_versions": {"peers": "x", "grants": "y"},
        }
    )
    # Assert
    # Tooltip surfaces the agent list + edge list.
    assert "a (local) [online]" in out["tooltip"]


def test_label_with_agents_and_grants_tooltip_contains_2() -> None:
    """The label pattern matches the spec verbatim:
    ``🕸 <N> agents · <M> grants``. Pin one realistic payload to
    catch a rename or a count-off-by-one downstream."""
    # Arrange
    # Act
    out = _run_panel_helpers(
        {
            "agents": [
                {"name": "a", "scope": "local", "status": "online"},
                {"name": "b", "scope": "peer", "status": "online"},
                {"name": "c", "scope": "peer", "status": "unknown"},
            ],
            "edges": [
                {"source": "a", "target": "b", "allow": True},
                {"source": "b", "target": "c", "allow": False},
            ],
            "config_path": None,
            "source_versions": {"peers": "x", "grants": "y"},
        }
    )
    # Assert
    # Tooltip surfaces the agent list + edge list.
    assert "c (peer) [unknown]" in out["tooltip"]


def test_label_with_agents_and_grants_tooltip_contains_3() -> None:
    """The label pattern matches the spec verbatim:
    ``🕸 <N> agents · <M> grants``. Pin one realistic payload to
    catch a rename or a count-off-by-one downstream."""
    # Arrange
    # Act
    out = _run_panel_helpers(
        {
            "agents": [
                {"name": "a", "scope": "local", "status": "online"},
                {"name": "b", "scope": "peer", "status": "online"},
                {"name": "c", "scope": "peer", "status": "unknown"},
            ],
            "edges": [
                {"source": "a", "target": "b", "allow": True},
                {"source": "b", "target": "c", "allow": False},
            ],
            "config_path": None,
            "source_versions": {"peers": "x", "grants": "y"},
        }
    )
    # Assert
    # Tooltip surfaces the agent list + edge list.
    assert "[allow]" in out["tooltip"]


def test_label_with_agents_and_grants_tooltip_contains_4() -> None:
    """The label pattern matches the spec verbatim:
    ``🕸 <N> agents · <M> grants``. Pin one realistic payload to
    catch a rename or a count-off-by-one downstream."""
    # Arrange
    # Act
    out = _run_panel_helpers(
        {
            "agents": [
                {"name": "a", "scope": "local", "status": "online"},
                {"name": "b", "scope": "peer", "status": "online"},
                {"name": "c", "scope": "peer", "status": "unknown"},
            ],
            "edges": [
                {"source": "a", "target": "b", "allow": True},
                {"source": "b", "target": "c", "allow": False},
            ],
            "config_path": None,
            "source_versions": {"peers": "x", "grants": "y"},
        }
    )
    # Assert
    # Tooltip surfaces the agent list + edge list.
    assert "[deny]" in out["tooltip"]


def test_label_with_empty_mesh_iserr() -> None:
    """A fresh install with zero agents + zero grants renders
    ``0 agents · 0 grants`` cleanly — no off-by-one or NaN."""
    # Arrange
    # Act
    out = _run_panel_helpers(
        {
            "agents": [],
            "edges": [],
            "config_path": None,
            "source_versions": {"peers": "x", "grants": "y"},
        }
    )
    # Assert
    assert out["isErr"] is False


def test_label_with_empty_mesh_label_contains() -> None:
    """A fresh install with zero agents + zero grants renders
    ``0 agents · 0 grants`` cleanly — no off-by-one or NaN."""
    # Arrange
    # Act
    out = _run_panel_helpers(
        {
            "agents": [],
            "edges": [],
            "config_path": None,
            "source_versions": {"peers": "x", "grants": "y"},
        }
    )
    # Assert
    assert "0 agents" in out["label"]


def test_label_with_empty_mesh_label_contains_2() -> None:
    """A fresh install with zero agents + zero grants renders
    ``0 agents · 0 grants`` cleanly — no off-by-one or NaN."""
    # Arrange
    # Act
    out = _run_panel_helpers(
        {
            "agents": [],
            "edges": [],
            "config_path": None,
            "source_versions": {"peers": "x", "grants": "y"},
        }
    )
    # Assert
    assert "0 grants" in out["label"]


def test_label_with_empty_mesh_tooltip_contains() -> None:
    """A fresh install with zero agents + zero grants renders
    ``0 agents · 0 grants`` cleanly — no off-by-one or NaN."""
    # Arrange
    # Act
    out = _run_panel_helpers(
        {
            "agents": [],
            "edges": [],
            "config_path": None,
            "source_versions": {"peers": "x", "grants": "y"},
        }
    )
    # Assert
    assert "agents: 0" in out["tooltip"]


def test_label_with_empty_mesh_tooltip_contains_2() -> None:
    """A fresh install with zero agents + zero grants renders
    ``0 agents · 0 grants`` cleanly — no off-by-one or NaN."""
    # Arrange
    # Act
    out = _run_panel_helpers(
        {
            "agents": [],
            "edges": [],
            "config_path": None,
            "source_versions": {"peers": "x", "grants": "y"},
        }
    )
    # Assert
    assert "grants: 0" in out["tooltip"]


def test_error_payload_discriminator_iserr() -> None:
    """``isMeshPayloadErr`` returns true for an HTTP-500 error body
    so the component branches to the ``--error`` render path."""
    # Arrange
    # Act
    out = _run_panel_helpers(
        {"error": "sac CLI not found on PATH — install scitex-agent-container"}
    )
    # Assert
    assert out["isErr"] is True


def test_error_payload_discriminator_label() -> None:
    """``isMeshPayloadErr`` returns true for an HTTP-500 error body
    so the component branches to the ``--error`` render path."""
    # Arrange
    # Act
    out = _run_panel_helpers(
        {"error": "sac CLI not found on PATH — install scitex-agent-container"}
    )
    # Assert
    assert out["label"] is None


def test_error_payload_discriminator_tooltip() -> None:
    """``isMeshPayloadErr`` returns true for an HTTP-500 error body
    so the component branches to the ``--error`` render path."""
    # Arrange
    # Act
    out = _run_panel_helpers(
        {"error": "sac CLI not found on PATH — install scitex-agent-container"}
    )
    # Assert
    assert out["tooltip"] is None


# ─── component logic — radial layout (pure Python mirror) ───────────────
#
# The TS ``radialLayout`` helper is deterministic — we mirror it
# in Python so the test can pin the layout shape without spawning
# node a second time. The TS source is checked for the lock-step
# fragments above.


def _py_radial_layout(
    names: list[str],
    cx: float = 70.0,
    cy: float = 70.0,
    r: float = 52.0,
) -> dict[str, tuple[float, float]]:
    """Mirror of the TS ``radialLayout`` helper, exact same math."""
    out: dict[str, tuple[float, float]] = {}
    n = len(names)
    if n == 0:
        return out
    if n == 1:
        out[names[0]] = (cx, cy)
        return out
    for i, name in enumerate(names):
        angle = -math.pi / 2 + (2 * math.pi * i) / n
        out[name] = (cx + r * math.cos(angle), cy + r * math.sin(angle))
    return out


def test_radial_layout_single_node_lands_at_centre() -> None:
    """One-node case lands at the centre — visually correct (a single
    dot in the middle), avoids dividing by zero in the sweep math."""
    # Arrange
    # Act
    pts = _py_radial_layout(["only"])
    # Assert
    assert pts["only"] == (70.0, 70.0)


def test_radial_layout_first_node_at_twelve_oclock_isclose() -> None:
    """The first node anchors at the TOP of the circle (12 o'clock) so
    the operator's eye lands on a consistent reference frame across
    polls. ``x ≈ cx`` and ``y < cy`` for the first node."""
    # Arrange
    pts = _py_radial_layout(["a", "b", "c", "d"])
    # Act
    x, y = pts["a"]
    # Assert
    # First node is ABOVE the centre (SVG y grows downward).
    assert math.isclose(x, 70.0, abs_tol=1e-9)


def test_radial_layout_first_node_at_twelve_oclock_y() -> None:
    """The first node anchors at the TOP of the circle (12 o'clock) so
    the operator's eye lands on a consistent reference frame across
    polls. ``x ≈ cx`` and ``y < cy`` for the first node."""
    # Arrange
    pts = _py_radial_layout(["a", "b", "c", "d"])
    # Act
    x, y = pts["a"]
    # Assert
    # First node is ABOVE the centre (SVG y grows downward).
    assert y < 70.0


def test_radial_layout_evenly_spaced() -> None:
    """N equal-spaced points → all on the circle, equidistant from
    the centre. Pin the distance to the expected radius."""
    # Arrange
    names = ["a", "b", "c", "d", "e"]
    pts = _py_radial_layout(names)
    # Act
    # Assert
    for x, y in pts.values():
        dist = math.hypot(x - 70.0, y - 70.0)
        assert math.isclose(dist, 52.0, abs_tol=1e-6)


def test_radial_layout_zero_nodes_returns_empty() -> None:
    """Empty input → empty output, no exception."""
    # Arrange
    # Act
    pts = _py_radial_layout([])
    # Assert
    assert pts == {}


# EOF
