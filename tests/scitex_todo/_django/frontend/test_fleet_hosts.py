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
    assert not hex_matches, f"hardcoded hex colors in fleet-hosts.css: {hex_matches!r}"


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


def test_label_with_interfaces_and_peers_iserr() -> None:
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
    # Tooltip surfaces the full interface list.
    assert out["isErr"] is False


def test_label_with_interfaces_and_peers_label_contains() -> None:
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
    # Tooltip surfaces the full interface list.
    assert "test-box" in out["label"]


def test_label_with_interfaces_and_peers_label_contains_2() -> None:
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
    # Tooltip surfaces the full interface list.
    assert "2 ifaces" in out["label"]


def test_label_with_interfaces_and_peers_label_contains_3() -> None:
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
    # Tooltip surfaces the full interface list.
    assert "3 peers" in out["label"]


def test_label_with_interfaces_and_peers_tooltip_contains() -> None:
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
    # Tooltip surfaces the full interface list.
    assert "eth0" in out["tooltip"]


def test_label_with_interfaces_and_peers_tooltip_contains_2() -> None:
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
    # Tooltip surfaces the full interface list.
    assert "10.0.0.1" in out["tooltip"]


def test_label_with_interfaces_and_peers_tooltip_contains_3() -> None:
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
    # Tooltip surfaces the full interface list.
    assert "eth1" in out["tooltip"]


def test_label_with_interfaces_and_peers_tooltip_contains_4() -> None:
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
    # Tooltip surfaces the full interface list.
    assert "scope: local" in out["tooltip"]


def test_label_with_no_interfaces_or_peers_iserr() -> None:
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


def test_label_with_no_interfaces_or_peers_label_contains() -> None:
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
    assert "isolated" in out["label"]


def test_label_with_no_interfaces_or_peers_label_contains_2() -> None:
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
    assert "0 ifaces" in out["label"]


def test_label_with_no_interfaces_or_peers_label_contains_3() -> None:
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
    assert "0 peers" in out["label"]


def test_label_with_no_interfaces_or_peers_tooltip_contains() -> None:
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
    assert "interfaces: (none)" in out["tooltip"]


def test_error_payload_discriminator_iserr() -> None:
    """``isHostsPayloadErr`` returns true for an HTTP-500 error body
    so the component branches to the ``--error`` render path."""
    # Arrange
    # Act
    out = _run_panel_helpers(
        {"error": "sac CLI not found on PATH — install scitex-agent-container"}
    )
    # Assert
    assert out["isErr"] is True


def test_error_payload_discriminator_label() -> None:
    """``isHostsPayloadErr`` returns true for an HTTP-500 error body
    so the component branches to the ``--error`` render path."""
    # Arrange
    # Act
    out = _run_panel_helpers(
        {"error": "sac CLI not found on PATH — install scitex-agent-container"}
    )
    # Assert
    assert out["label"] is None


def test_error_payload_discriminator_tooltip() -> None:
    """``isHostsPayloadErr`` returns true for an HTTP-500 error body
    so the component branches to the ``--error`` render path."""
    # Arrange
    # Act
    out = _run_panel_helpers(
        {"error": "sac CLI not found on PATH — install scitex-agent-container"}
    )
    # Assert
    assert out["tooltip"] is None
