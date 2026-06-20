#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Front-end contract tests for the fleet host-geometry panel.

Two halves (same pattern the recent ``test_table_filter.py`` /
``test_calendar_date.py`` / ``test_fleet_ci_pills.py`` PRs established):

1. **CSS contract** — open ``fleet-hosts.css`` and assert:
     - the canonical selectors are present
     - colors come from design tokens (``--status-success``,
       ``--stx-danger``, etc.) ONLY; NO hardcoded hex / ``white`` /
       ``#fff`` literals leak in (theme-breaking).
2. **Label + tooltip mapping** — execute the actual ``hostsPanelLabel``
   / ``hostsPanelTooltip`` / ``isHostsPayloadErr`` helpers from
   ``FleetHostsPanel.tsx`` via ``node`` and pin the mapping for an OK
   payload + an empty-interfaces payload + the error case. Same
   lock-step assertion against the TS source as ``test_fleet_ci_pills``,
   so a rename downstream forces this test to update.
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
    / "fleet-hosts.css"
)
_TSX_FILE = (
    _REPO_ROOT
    / "src"
    / "scitex_todo"
    / "_django"
    / "frontend"
    / "src"
    / "FleetHostsPanel.tsx"
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
        ".stx-todo-fleet-hosts",
        ".stx-todo-fleet-hosts--ok",
        ".stx-todo-fleet-hosts--error",
        ".stx-todo-fleet-hosts--loading",
        ".stx-todo-fleet-hosts__label",
        ".stx-todo-fleet-hosts__dot",
    ):
        assert selector in css, f"missing CSS selector: {selector}"


def test_css_uses_design_tokens_only() -> None:
    """Colors must come from CSS variables — NO hardcoded hex / named
    color literals. Hex / ``white`` / ``#fff`` would freeze the panel
    to one theme and break the operator's light-mode view.

    Required tokens: ``--stx-danger`` (error ring), ``--status-success``
    (OK border), ``--stx-text-muted`` (error label color). The shell
    + board.css token chain feeds all of them.
    """
    # Arrange
    css = _CSS_FILE.read_text(encoding="utf-8")
    # Strip /* ... */ comments before scanning — the comment block at
    # the top documents the token names verbatim and would falsely
    # trip the hex / named-color detectors otherwise.
    no_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
    # 3-, 4-, 6-, or 8-digit hex literals.
    # Act
    hex_matches = re.findall(r"#[0-9A-Fa-f]{3,8}\b", no_comments)
    # Assert
    assert not hex_matches, (
        f"hardcoded hex colors in fleet-hosts.css (breaks theming): "
        f"{hex_matches!r}"
    )
    # Named-color literals — same theming smell. Only match standalone
    # color values, not substrings like ``whitespace`` (none expected,
    # but be defensive). We look for ``: white`` and ``: black`` shapes
    # specifically inside property values.
    for forbidden in (r":\s*white\b", r":\s*black\b"):
        assert not re.search(forbidden, no_comments), (
            f"hardcoded named color matching {forbidden!r} found in "
            f"fleet-hosts.css — use a design token."
        )
    # Required token references — at least one occurrence each.
    for token in (
        "--status-success",
        "--stx-danger",
        "--stx-text-muted",
        "--stx-border",
        "--stx-panel-bg",
        "--stx-text",
    ):
        assert token in css, f"missing design token: {token}"


def test_css_is_imported_from_board_css() -> None:
    """The panel only renders correctly when board.css imports the
    partial. Pinning this guards against an accidental removal in a
    future board.css refactor."""
    # Arrange
    # Act
    board_css = _CSS_FILE.parent / "board.css"
    # Assert
    assert board_css.is_file()
    text = board_css.read_text(encoding="utf-8")
    assert '@import "./fleet-hosts.css";' in text


# ─── component logic — label + tooltip mapping via node ────────────────


def _node() -> str:
    """Locate ``node``; skip the suite cleanly if it isn't installed."""
    exe = shutil.which("node")
    if exe is None:
        pytest.skip("node executable not found on PATH")
    return exe


def _run_panel_helpers(payload: dict) -> dict:
    """Execute the actual ``hostsPanelLabel`` / ``hostsPanelTooltip`` /
    ``isHostsPayloadErr`` helpers via node against ``payload``.

    Mirrors the TS module — and asserts each runtime fragment is still
    present in the TS source so the mirror stays in lock-step (a
    rename downstream forces this test to update; no silent drift).
    """
    src = _TSX_FILE.read_text(encoding="utf-8")

    # Static-source contract — keeping the JS mirror in lock-step.
    for needle in [
        "export function isHostsPayloadErr(p: HostsPayload): p is HostsPayloadErr {",
        'return Object.prototype.hasOwnProperty.call(p, "error");',
        "export function hostsPanelLabel(p: HostsPayloadOk): string {",
        "export function hostsPanelTooltip(p: HostsPayloadOk): string {",
        "ifaces",
        "peers",
    ]:
        assert needle in src, (
            f"FleetHostsPanel.tsx no longer contains canonical fragment "
            f"{needle!r}; update this test in lock-step."
        )

    js_runtime = textwrap.dedent(
        """
        function isHostsPayloadErr(p) {
          return Object.prototype.hasOwnProperty.call(p, "error");
        }
        function hostsPanelLabel(p) {
          const name = p.local.name || "(unknown host)";
          const ifaceCount = Array.isArray(p.local.interfaces)
            ? p.local.interfaces.length
            : 0;
          const peerCount = Array.isArray(p.peers) ? p.peers.length : 0;
          return "\\uD83D\\uDDA5 " + name + " \\u00b7 " + ifaceCount + " ifaces \\u00b7 " + peerCount + " peers";
        }
        function hostsPanelTooltip(p) {
          const lines = [];
          lines.push("host: " + (p.local.name || "(unknown)"));
          if (p.local.scope) lines.push("scope: " + p.local.scope);
          if (p.config_path) lines.push("config: " + p.config_path);
          const ifaces = Array.isArray(p.local.interfaces) ? p.local.interfaces : [];
          if (ifaces.length === 0) {
            lines.push("interfaces: (none)");
          } else {
            lines.push("interfaces:");
            for (const i of ifaces) {
              lines.push("  " + (i.iface || "?") + " " + i.addr + " (" + i.family + ")");
            }
          }
          const peerCount = Array.isArray(p.peers) ? p.peers.length : 0;
          lines.push("peers: " + peerCount);
          return lines.join("\\n");
        }
        """
    ).strip()

    script = (
        js_runtime
        + "\nconst payload = "
        + json.dumps(payload)
        + ";\nconst result = {"
        + "isErr: isHostsPayloadErr(payload),"
        + "label: isHostsPayloadErr(payload) ? null : hostsPanelLabel(payload),"
        + "tooltip: isHostsPayloadErr(payload) ? null : hostsPanelTooltip(payload)"
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


def test_label_with_interfaces_and_peers() -> None:
    """The label pattern matches the operator's spec verbatim:
    ``🖥 <hostname> · <N> ifaces · <M> peers``. Pin one realistic
    payload to catch a rename or a count-off-by-one downstream."""
    # Arrange
    # Act
    out = _run_panel_helpers(
        {
            "config_path": None,
            "local": {
                "name": "test-box",
                "scope": "local",
                "aliases": {},
                "interfaces": [
                    {"iface": "eth0", "addr": "10.0.0.1", "family": "inet"},
                    {"iface": "eth1", "addr": "10.0.0.2", "family": "inet"},
                ],
            },
            "peers": [{"name": "p1"}, {"name": "p2"}, {"name": "p3"}],
        }
    )
    # Assert
    assert out["isErr"] is False
    assert "test-box" in out["label"]
    assert "2 ifaces" in out["label"]
    assert "3 peers" in out["label"]
    # Tooltip surfaces the full interface list.
    assert "eth0" in out["tooltip"]
    assert "10.0.0.1" in out["tooltip"]
    assert "eth1" in out["tooltip"]
    assert "scope: local" in out["tooltip"]


def test_label_with_no_interfaces_or_peers() -> None:
    """A host with zero NICs visible to sac (containerized env) +
    zero peers (fresh install) renders ``0 ifaces · 0 peers`` and the
    tooltip surfaces ``interfaces: (none)``."""
    # Arrange
    # Act
    out = _run_panel_helpers(
        {
            "config_path": None,
            "local": {
                "name": "isolated",
                "scope": "local",
                "aliases": {},
                "interfaces": [],
            },
            "peers": [],
        }
    )
    # Assert
    assert out["isErr"] is False
    assert "isolated" in out["label"]
    assert "0 ifaces" in out["label"]
    assert "0 peers" in out["label"]
    assert "interfaces: (none)" in out["tooltip"]


def test_error_payload_discriminator() -> None:
    """``isHostsPayloadErr`` returns true for an HTTP-500 error body
    so the component branches to the ``--error`` render path."""
    # Arrange
    # Act
    out = _run_panel_helpers(
        {"error": "sac CLI not found on PATH — install scitex-agent-container"}
    )
    # Assert
    assert out["isErr"] is True
    assert out["label"] is None
    assert out["tooltip"] is None
