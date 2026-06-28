#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`scitex-todo mcp` subgroup — §3 required four verbs (+ channel).

Verbs:
    start          Launch the FastMCP server (stdio by default).
    doctor         Self-diagnose the MCP install.
    list-tools     Enumerate registered tools (with `-v|-vv|-vvv`/`--json`).
    install        Print the snippet to paste into a Claude Code MCP config.
    install-fleet  Apply the entry to every agent's to_home/.mcp.json.
    channel        Run the standalone channel-notification server (stdio).

We prefer ``scitex_dev._mcp_cli.attach_mcp_subcommands`` when available
(keeps every scitex package's `mcp` group identical) and fall back to a
hand-rolled group when scitex-dev isn't installed (so a fresh
``pip install scitex-todo[mcp]`` still works). The ``channel`` verb is
scitex-todo's OWN feature (no scitex-dev parallel) and is wired onto the
group in BOTH paths.

The ``install`` / ``install-fleet`` verbs and the FastMCP tool-introspection
helpers were extracted to ``_mcp_install`` / ``_mcp_tools`` to keep this
orchestrator under the module size budget; their public names are re-exported
here so historical import paths keep working.
"""

from __future__ import annotations

import json
import sys

import click

from ._mcp_channel_verb import attach_channel_verb
from ._mcp_install import _fleet_apply_one, attach_install_verbs  # noqa: F401
from ._mcp_tools import (  # noqa: F401
    _list_tool_names,
    _list_tool_records,
    _tool_record,
    _tools_dict,
)

_SERVER_PATH = "scitex_todo._mcp_server:mcp"
_CLI_NAME = "scitex-todo"

_INSTALL_HINT = (
    "scitex-todo MCP tools require the [mcp] extra:\n  pip install 'scitex-todo[mcp]'"
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
    ``install``) plus ``install-fleet`` and the ``channel`` verb. Keeps
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
    @mcp_group.command(
        "start",
        help=(
            "Launch the MCP server (stdio).\n\n"
            "Example:\n  scitex-todo mcp start            # stdio (default)\n"
            "  scitex-todo mcp start --http --port 7700"
        ),
    )
    @click.option("--http", is_flag=True, help="Use HTTP transport instead of stdio.")
    @click.option("--host", default="127.0.0.1", show_default=True)
    @click.option("--port", type=int, default=0, help="HTTP port (0 = auto).")
    @click.option(
        "--dry-run",
        is_flag=True,
        help="Print what would happen (transport/host/port) and exit 0 without launching.",
    )
    @click.option(
        "-y",
        "--yes",
        is_flag=True,
        help="Skip confirmation (no-op for the default stdio path; reserved for HTTP mode).",
    )
    def start(http, host, port, dry_run, yes) -> None:
        _ = yes  # accepted for §2 compliance; no interactive prompt today
        if dry_run:
            transport = "http" if http else "stdio"
            click.echo(
                f"# dry-run: would launch MCP server transport={transport} "
                f"host={host} port={port or 'auto'}"
            )
            return
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
    @mcp_group.command(
        "doctor",
        help=(
            "Self-diagnose the MCP install.\n\n"
            "Example:\n  scitex-todo mcp doctor --json"
        ),
    )
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
    @mcp_group.command(
        "list-tools",
        help=(
            "Enumerate registered MCP tools.\n\n"
            "Example:\n  scitex-todo mcp list-tools -vv"
        ),
    )
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

    # ── install / install-fleet (extracted) ───────────────────────────── #
    attach_install_verbs(mcp_group)
    # ── channel (scitex-todo's own standalone server) ─────────────────── #
    attach_channel_verb(mcp_group)
    return mcp_group


def register(main: click.Group) -> None:
    """Attach the `mcp` subgroup to `main`. Prefers the scitex-dev helper."""
    try:
        from scitex_dev._mcp_cli import attach_mcp_subcommands  # type: ignore

        @click.group(
            "mcp", help="MCP server subcommands (start/doctor/list-tools/install)."
        )
        def mcp_group() -> None:
            pass

        attach_mcp_subcommands(mcp_group, server_path=_SERVER_PATH, cli_name=_CLI_NAME)
        # scitex-todo's OWN channel verb has no scitex-dev parallel — wire it
        # on regardless of which path built the group.
        attach_channel_verb(mcp_group)
        main.add_command(mcp_group, name="mcp")
        return
    except ImportError:
        # scitex-dev not available — use the hand-rolled fallback.
        main.add_command(_fallback_mcp_group(), name="mcp")


# EOF
