#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""§1a introspection commands: ``list-python-apis`` and ``mcp list-tools``."""

from __future__ import annotations

import json

import click


def _describe_api(module, name: str) -> dict:
    """Introspect ``module.name`` into a name/signature/summary/source record."""
    import inspect

    obj = getattr(module, name)
    signature = ""
    if callable(obj):
        try:
            signature = str(inspect.signature(obj))
        except (TypeError, ValueError):
            signature = "(...)"
    doc = inspect.getdoc(obj) or ""
    summary = doc.splitlines()[0] if doc else ""
    try:
        source = inspect.getsourcefile(obj) or ""
    except TypeError:
        source = ""
    return {
        "name": name,
        "kind": type(obj).__name__,
        "signature": signature,
        "summary": summary,
        "doc": doc,
        "source": source,
    }


@click.command(
    "list-python-apis",
    help=(
        "List the public Python API (scitex_todo.__all__).\n\n"
        "Verbosity is additive: -v adds signatures, -vv adds docstring "
        "summaries, -vvv adds source paths.\n\n"
        "Example:\n  scitex-todo list-python-apis -v"
    ),
)
@click.option(
    "-v",
    "--verbose",
    count=True,
    help="Add detail (repeatable): -v signatures, -vv summaries, -vvv sources.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the API list as JSON (rich records, independent of -v).",
)
def list_python_apis_cmd(verbose: int, as_json: bool) -> None:
    """List the public Python API surface of scitex_todo."""
    import scitex_todo

    names = [n for n in scitex_todo.__all__ if n != "__version__"]
    entries = [_describe_api(scitex_todo, n) for n in names]

    if as_json:
        click.echo(json.dumps(entries))
        return

    for entry in entries:
        if verbose == 0:
            click.echo(entry["name"])
            continue
        click.echo(f"{entry['name']}{entry['signature']}")
        if verbose >= 2 and entry["summary"]:
            click.echo(f"    {entry['summary']}")
        if verbose >= 3 and entry["source"]:
            click.echo(f"    [{entry['source']}]")


@click.group("mcp", help="MCP server introspection for scitex-todo.")
def mcp_grp() -> None:
    """MCP tool surface (scitex-todo ships no MCP server yet)."""


@mcp_grp.command(
    "list-tools",
    help=(
        "List the MCP tools registered by scitex-todo.\n\n"
        "Example:\n  scitex-todo mcp list-tools --json"
    ),
)
@click.option(
    "-v",
    "--verbose",
    count=True,
    help="Add detail (repeatable): -v signatures, -vv summaries, -vvv schema.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the tool list as JSON.",
)
def mcp_list_tools_cmd(verbose: int, as_json: bool) -> None:
    """List MCP tools. scitex-todo has no MCP server yet, so this is empty."""
    tools: list[dict] = []
    if as_json:
        click.echo(json.dumps(tools))
        return
    if not tools:
        click.echo(
            "scitex-todo ships no MCP tools yet (the MCP surface is on the roadmap).",
            err=True,
        )


def register(group: click.Group) -> None:
    """Attach the introspection commands to the root ``group``."""
    group.add_command(list_python_apis_cmd)
    group.add_command(mcp_grp)

# EOF
