#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`scitex-todo mcp` subgroup — §3 required four verbs.

Verbs:
    start          Launch the FastMCP server (stdio by default).
    doctor         Self-diagnose the MCP install.
    list-tools     Enumerate registered tools (with `-v|-vv|-vvv`/`--json`).
    install        Print the snippet to paste into a Claude Code MCP config.

We prefer ``scitex_dev._mcp_cli.attach_mcp_subcommands`` when available
(keeps every scitex package's `mcp` group identical) and fall back to a
hand-rolled four-verb group when scitex-dev isn't installed (so a fresh
``pip install scitex-todo[mcp]`` still works).
"""

from __future__ import annotations

import json
import sys

import click

_SERVER_PATH = "scitex_todo._mcp_server:mcp"
_CLI_NAME = "scitex-todo"

_INSTALL_HINT = (
    "scitex-todo MCP tools require the [mcp] extra:\n"
    "  pip install 'scitex-todo[mcp]'"
)


def _try_import_mcp():
    """Import the FastMCP instance. Returns (mcp_obj, None) or (None, hint)."""
    try:
        from .._mcp_server import mcp as mcp_obj

        return mcp_obj, None
    except ImportError:
        return None, _INSTALL_HINT


def _fallback_mcp_group() -> click.Group:
    """Hand-rolled `mcp` group used when scitex-dev's helper isn't present.

    Implements §3's required four (``start``, ``doctor``, ``list-tools``,
    ``install``) plus the §4 verbosity ladder for ``list-tools``. Keeps
    behavior parity with the scitex-dev helper so users see the same
    surface either way.
    """

    @click.group(
        "mcp",
        help=(
            "MCP server subcommands.\n\n"
            "Required: start, doctor, list-tools, install (SciTeX §3)."
        ),
    )
    def mcp_group() -> None:
        pass

    # ── start ─────────────────────────────────────────────────────────── #
    @mcp_group.command("start", help="Launch the MCP server (stdio).")
    @click.option("--http", is_flag=True, help="Use HTTP transport instead of stdio.")
    @click.option("--host", default="127.0.0.1", show_default=True)
    @click.option("--port", type=int, default=0, help="HTTP port (0 = auto).")
    def start(http, host, port) -> None:
        mcp_obj, hint = _try_import_mcp()
        if mcp_obj is None:
            raise click.ClickException(hint)
        if http:
            # FastMCP's HTTP transport (sync wrapper); fall through to stdio
            # if the helper isn't available on the installed fastmcp.
            try:
                mcp_obj.run(transport="http", host=host, port=port or None)
            except TypeError:
                # Older fastmcp uses run_http(...)
                mcp_obj.run_http(host=host, port=port or 0)
            return
        mcp_obj.run()

    # ── doctor ────────────────────────────────────────────────────────── #
    @mcp_group.command("doctor", help="Self-diagnose the MCP install.")
    @click.option("--json", "as_json", is_flag=True)
    def doctor(as_json) -> None:
        diag = {
            "package": "scitex-todo",
            "server_path": _SERVER_PATH,
            "fastmcp": None,
            "tools": 0,
            "status": "unknown",
            "hint": None,
        }
        try:
            import fastmcp

            diag["fastmcp"] = getattr(fastmcp, "__version__", "(unknown)")
        except ImportError:
            diag["status"] = "critical"
            diag["hint"] = _INSTALL_HINT
            if as_json:
                click.echo(json.dumps(diag))
            else:
                click.echo(f"status: critical\n{_INSTALL_HINT}")
            sys.exit(2)

        mcp_obj, hint = _try_import_mcp()
        if mcp_obj is None:
            diag["status"] = "critical"
            diag["hint"] = hint
            if as_json:
                click.echo(json.dumps(diag))
            else:
                click.echo(f"status: critical\n{hint}")
            sys.exit(2)

        tool_names = _list_tool_names(mcp_obj)
        diag["tools"] = len(tool_names)
        diag["status"] = "ok" if tool_names else "degraded"
        if as_json:
            click.echo(json.dumps(diag))
        else:
            click.echo(f"status: {diag['status']}")
            click.echo(f"fastmcp: {diag['fastmcp']}")
            click.echo(f"tools:   {diag['tools']}")
        sys.exit(0 if diag["status"] == "ok" else 1)

    # ── list-tools ────────────────────────────────────────────────────── #
    @mcp_group.command("list-tools", help="Enumerate registered MCP tools.")
    @click.option("-v", "verbosity", count=True, help="Repeat for more detail.")
    @click.option("--json", "as_json", is_flag=True)
    def list_tools(verbosity, as_json) -> None:
        mcp_obj, hint = _try_import_mcp()
        if mcp_obj is None:
            raise click.ClickException(hint)
        items = _list_tool_records(mcp_obj, verbosity=verbosity)
        if as_json:
            click.echo(json.dumps(items))
            return
        if verbosity == 0:
            for it in items:
                click.echo(it["name"])
        else:
            for it in items:
                click.echo(f"- {it['name']}")
                if verbosity >= 1 and it.get("description"):
                    click.echo(f"    {it['description'].splitlines()[0]}")
                if verbosity >= 2 and it.get("description"):
                    for line in it["description"].splitlines()[1:]:
                        click.echo(f"    {line}")
                if verbosity >= 3:
                    click.echo(f"    full: {it}")

    # ── install ───────────────────────────────────────────────────────── #
    @mcp_group.command("install", help="Print the Claude Code MCP install snippet.")
    @click.option(
        "--format",
        "fmt",
        type=click.Choice(["claude-code", "raw"]),
        default="claude-code",
        show_default=True,
    )
    def install(fmt) -> None:
        snippet = {
            "mcpServers": {
                _CLI_NAME: {
                    "command": _CLI_NAME,
                    "args": ["mcp", "start"],
                }
            }
        }
        if fmt == "raw":
            click.echo(json.dumps(snippet[_CLI_NAME[:0] + "mcpServers"]))
            return
        click.echo(json.dumps(snippet, indent=2))

    return mcp_group


def _list_tool_names(mcp_obj) -> list[str]:
    """Best-effort tool-name listing that handles FastMCP 2.x and 3.x APIs."""
    # FastMCP 2.x: `mcp._tools` is a dict {name: Tool}.
    # FastMCP 3.x: `mcp.tools` (property) returns a list/dict.
    # We try a handful of common paths in order.
    for attr in ("tools", "_tools"):
        registry = getattr(mcp_obj, attr, None)
        if registry is None:
            continue
        if isinstance(registry, dict):
            return list(registry.keys())
        if isinstance(registry, (list, tuple)):
            return [getattr(t, "name", str(t)) for t in registry]
    # Fall back to walking the public tool decorator's bookkeeping.
    return []


def _list_tool_records(mcp_obj, *, verbosity: int) -> list[dict]:
    """Best-effort {name, description, …} records, FastMCP version-agnostic."""
    records: list[dict] = []
    for attr in ("tools", "_tools"):
        registry = getattr(mcp_obj, attr, None)
        if isinstance(registry, dict):
            for name, tool in registry.items():
                records.append(_tool_record(name, tool, verbosity=verbosity))
            return records
        if isinstance(registry, (list, tuple)):
            for tool in registry:
                records.append(
                    _tool_record(
                        getattr(tool, "name", str(tool)), tool, verbosity=verbosity
                    )
                )
            return records
    return records


def _tool_record(name: str, tool, *, verbosity: int) -> dict:
    rec: dict = {"name": name}
    desc = getattr(tool, "description", None) or getattr(tool, "__doc__", None) or ""
    if verbosity >= 1:
        rec["description"] = desc.strip()
    if verbosity >= 3:
        # The full tool object is not JSON-friendly; expose its repr only.
        rec["repr"] = repr(tool)
    return rec


def register(main: click.Group) -> None:
    """Attach the `mcp` subgroup to `main`. Prefers the scitex-dev helper."""
    try:
        from scitex_dev._mcp_cli import attach_mcp_subcommands  # type: ignore

        @click.group("mcp", help="MCP server subcommands (start/doctor/list-tools/install).")
        def mcp_group() -> None:
            pass

        attach_mcp_subcommands(
            mcp_group, server_path=_SERVER_PATH, cli_name=_CLI_NAME
        )
        main.add_command(mcp_group, name="mcp")
        return
    except ImportError:
        # scitex-dev not available — use the hand-rolled fallback.
        main.add_command(_fallback_mcp_group(), name="mcp")


# EOF
