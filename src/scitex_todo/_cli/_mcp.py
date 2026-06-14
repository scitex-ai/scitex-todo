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
import pathlib
import sys

import click

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

    # ── install ───────────────────────────────────────────────────────── #
    @mcp_group.command(
        "install",
        help=(
            "Print or apply the Claude Code MCP install snippet.\n\n"
            "Without --apply this command PRINTS the JSON snippet for\n"
            "manual paste-in. With --apply, it MERGES the snippet into\n"
            "the target ``.mcp.json`` file (default ``~/.mcp.json``),\n"
            "idempotently — re-running is a no-op when the entry is\n"
            "already present. Other servers' entries are preserved.\n\n"
            "Example (print):\n  scitex-todo mcp install --format raw\n\n"
            "Example (fleet P3a — write into the user-scope .mcp.json):\n"
            "  scitex-todo mcp install --apply --dry-run\n"
            "  scitex-todo mcp install --apply -y\n\n"
            "Example (project-scope .mcp.json):\n"
            "  scitex-todo mcp install --apply --to ./.mcp.json\n\n"
            "Example (fleet host-store pin — P3a wire-up so containerized\n"
            "agents resolve the shared host tasks.yaml regardless of\n"
            "$HOME / symlink state):\n"
            "  scitex-todo mcp install --apply --to to_home/.mcp.json \\\n"
            "    --env-tasks-path /home/agent/.scitex/todo/tasks.yaml -y"
        ),
    )
    @click.option(
        "--format",
        "fmt",
        type=click.Choice(["claude-code", "raw"]),
        default="claude-code",
        show_default=True,
    )
    @click.option(
        "--apply",
        "do_apply",
        is_flag=True,
        help=(
            "Write the snippet into the target ``.mcp.json`` file"
            " (default ``~/.mcp.json``). Idempotent + non-destructive."
        ),
    )
    @click.option(
        "--to",
        "target_path",
        type=click.Path(dir_okay=False, file_okay=True, writable=True, resolve_path=False),
        default=None,
        help=(
            "Override the target file (default ``~/.mcp.json``)."
            " Only meaningful with --apply."
        ),
    )
    @click.option(
        "--env-tasks-path",
        "env_tasks_path",
        type=str,
        default=None,
        help=(
            "Pin SCITEX_TODO_TASKS in the snippet's `env` block — the MCP\n"
            "subprocess uses this path as the task-store via the normal\n"
            "resolution chain (explicit env > project > user > example).\n"
            "Fleet P3a use case: when this CLI is run by agent-container\n"
            "to seed every container's ``to_home/.mcp.json``, the pinned\n"
            "path makes the wire-up self-documenting and immune to $HOME\n"
            "or symlink drift in any container. Omit to leave the entry\n"
            "without an env block (back-compat default)."
        ),
    )
    @click.option(
        "--dry-run",
        is_flag=True,
        help=(
            "With --apply: print what would be written WITHOUT touching"
            " the file. Without --apply: print the snippet (same as default)."
        ),
    )
    @click.option(
        "-y",
        "--yes",
        is_flag=True,
        help=(
            "With --apply: skip the confirmation prompt. Without"
            " --apply: no-op (print-only path needs no confirmation)."
        ),
    )
    def install(fmt, do_apply, target_path, env_tasks_path, dry_run, yes) -> None:
        entry: dict = {
            "command": _CLI_NAME,
            "args": ["mcp", "start"],
        }
        # P3a host-store wire-up: when an explicit task-store path is
        # provided, pin it in the MCP entry's `env` block. The MCP server
        # subprocess picks up SCITEX_TODO_TASKS via the normal resolution
        # chain (env beats project/user scopes), so a containerized agent
        # ends up reading the shared host store regardless of its $HOME or
        # symlink state. Keeping this OPT-IN preserves back-compat with
        # the existing snippet shape.
        if env_tasks_path:
            entry["env"] = {"SCITEX_TODO_TASKS": env_tasks_path}
        snippet = {"mcpServers": {_CLI_NAME: entry}}

        if not do_apply:
            # Print-only path (back-compat).
            if fmt == "raw":
                click.echo(json.dumps(snippet["mcpServers"]))
                return
            click.echo(json.dumps(snippet, indent=2))
            return

        # --apply: MERGE the entry into the target file. Fleet P3a path.
        target = (
            pathlib.Path(target_path).expanduser()
            if target_path
            else pathlib.Path.home() / ".mcp.json"
        )

        existing: dict = {}
        if target.exists():
            try:
                existing = json.loads(target.read_text(encoding="utf-8") or "{}")
            except json.JSONDecodeError as exc:
                raise click.ClickException(
                    f"target {target} exists but is not valid JSON: {exc}"
                ) from None
            if not isinstance(existing, dict):
                raise click.ClickException(
                    f"target {target} root is not a JSON object "
                    f"(got {type(existing).__name__})"
                )

        merged = dict(existing)
        servers = dict(merged.get("mcpServers") or {})
        before_entry = servers.get(_CLI_NAME)
        servers[_CLI_NAME] = snippet["mcpServers"][_CLI_NAME]
        merged["mcpServers"] = servers

        changed = before_entry != servers[_CLI_NAME]
        action = (
            "noop (entry already present)"
            if not changed
            else ("would update" if before_entry is not None else "would create")
        )

        new_text = json.dumps(merged, indent=2) + "\n"

        if dry_run:
            click.echo(f"# dry-run: --apply target={target} action={action}")
            click.echo(new_text)
            return

        if not yes and changed:
            click.confirm(
                f"Apply scitex-todo MCP entry to {target} ({action})?",
                abort=True,
            )

        if not changed:
            click.echo(f"# noop: {target} already has the scitex-todo entry")
            return

        # Backup the existing file before write (best-effort; failure to
        # backup does NOT abort the write — but if the source was unreadable
        # we already failed above with ClickException).
        if target.exists():
            backup = target.with_suffix(target.suffix + ".bak")
            try:
                backup.write_text(
                    target.read_text(encoding="utf-8"), encoding="utf-8"
                )
            except OSError:
                pass

        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(new_text, encoding="utf-8")
        click.echo(f"# applied: {target} updated ({action})")

    # ── install-fleet ─────────────────────────────────────────────────── #
    @mcp_group.command(
        "install-fleet",
        help=(
            "Apply the scitex-todo MCP entry to EVERY agent's "
            "``to_home/.mcp.json`` under an agents directory.\n\n"
            "Fleet P3a unblock (lead a2a `1ab212f3`, 2026-06-14). Walks "
            "``<agents-dir>/*/to_home/.mcp.json`` and runs the idempotent "
            "merge for each. Files that don't exist yet are CREATED; "
            "existing files preserve every sibling MCP server entry.\n\n"
            "Example:\n"
            "  scitex-todo mcp install-fleet \\\n"
            "    --agents-dir ~/.dotfiles/src/.scitex/agent-container/agents \\\n"
            "    --env-tasks-path /home/agent/.scitex/todo/tasks.yaml -y"
        ),
    )
    @click.option(
        "--agents-dir", "agents_dir",
        type=click.Path(file_okay=False, dir_okay=True, resolve_path=False),
        required=True,
        help="Directory of per-agent subdirs each carrying `to_home/.mcp.json`.",
    )
    @click.option(
        "--env-tasks-path", "env_tasks_path",
        type=str, default=None,
        help="Pin SCITEX_TODO_TASKS in every emitted entry's env block.",
    )
    @click.option("--dry-run", is_flag=True, help="Print planned per-agent action; no writes.")
    @click.option("-y", "--yes", "yes", is_flag=True, help="Skip per-agent prompts.")
    def install_fleet(agents_dir, env_tasks_path, dry_run, yes) -> None:
        """Apply the scitex-todo MCP entry to every agent's to_home/.mcp.json."""
        agents_root = pathlib.Path(agents_dir).expanduser()
        if not agents_root.is_dir():
            raise click.ClickException(
                f"agents-dir does not exist or is not a directory: {agents_root}"
            )
        entry: dict = {"command": _CLI_NAME, "args": ["mcp", "start"]}
        if env_tasks_path:
            entry["env"] = {"SCITEX_TODO_TASKS": env_tasks_path}

        agent_count = applied = noop = 0
        errors: list[str] = []
        for agent_dir in sorted(agents_root.iterdir()):
            if not agent_dir.is_dir():
                continue
            agent_count += 1
            target = agent_dir / "to_home" / ".mcp.json"
            try:
                action, changed = _fleet_apply_one(target, entry, dry_run=dry_run)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{agent_dir.name}: {exc}")
                click.echo(f"# error: {agent_dir.name} — {exc}", err=True)
                continue
            prefix = "# dry-run:" if dry_run else "# applied:"
            click.echo(f"{prefix} {agent_dir.name}/to_home/.mcp.json — {action}")
            if changed:
                applied += 1
            else:
                noop += 1
        click.echo(
            f"# fleet sweep: agents={agent_count} "
            f"{'would-update' if dry_run else 'updated'}={applied} "
            f"noop={noop} errors={len(errors)}",
            err=True,
        )
        if errors and not yes:
            raise click.ClickException(
                f"install-fleet completed with {len(errors)} error(s)"
            )

    return mcp_group


def _fleet_apply_one(target, entry: dict, *, dry_run: bool):
    """Apply / merge the scitex-todo MCP entry into ONE target file.

    Shared body for ``install-fleet``. Same rules as the single-file
    ``install --apply``: existing JSON preserved + sibling mcpServers
    kept; idempotent re-application is a noop; dry-run prints the
    planned action without touching disk. Returns
    ``(action_label, changed)``.
    """
    existing: dict = {}
    if target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError as exc:
            raise RuntimeError(
                f"target {target} exists but is not valid JSON: {exc}"
            ) from None
        if not isinstance(existing, dict):
            raise RuntimeError(
                f"target {target} root is not a JSON object "
                f"(got {type(existing).__name__})"
            )
    merged = dict(existing)
    servers = dict(merged.get("mcpServers") or {})
    before_entry = servers.get(_CLI_NAME)
    servers[_CLI_NAME] = entry
    merged["mcpServers"] = servers
    changed = before_entry != entry
    action = (
        "noop (entry already present)"
        if not changed
        else ("would-update" if before_entry is not None else "would-create")
    )
    if dry_run or not changed:
        return action, changed
    if target.exists():
        backup = target.with_suffix(target.suffix + ".bak")
        try:
            backup.write_text(
                target.read_text(encoding="utf-8"), encoding="utf-8",
            )
        except OSError:
            pass
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    return ("updated" if before_entry is not None else "created"), True


def _tools_dict(mcp_obj) -> dict:
    """Return ``{name: tool}`` for a FastMCP server, version-agnostic.

    Self-contained mirror of ``scitex_dev.get_tools_sync`` (this fallback runs
    only when scitex-dev is *not* installed). FastMCP 3.x removed the sync
    ``_tools``/``tools`` attributes and exposes an async ``list_tools()``
    returning a *list* of Tool objects; 2.x exposes ``_tool_manager._tools``
    (dict) / ``_tool_manager.get_tools()``. We try the cheap sync paths first,
    then fall back to running the async API (guarding against a live loop).
    """
    import asyncio

    tm = getattr(mcp_obj, "_tool_manager", None)
    if tm is not None and isinstance(getattr(tm, "_tools", None), dict):
        return dict(tm._tools)
    for attr in ("tools", "_tools"):
        registry = getattr(mcp_obj, attr, None)
        if isinstance(registry, dict):
            return dict(registry)
        if isinstance(registry, (list, tuple)):
            return {getattr(t, "name", str(t)): t for t in registry}

    async def _gather():
        if tm is not None and hasattr(tm, "get_tools"):
            return await tm.get_tools()
        tools = await mcp_obj.list_tools()
        return {getattr(t, "name", str(t)): t for t in tools}

    if getattr(mcp_obj, "list_tools", None) is None and tm is None:
        return {}
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:
        running = None
    try:
        if running is not None and running.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, _gather()).result()
        return asyncio.run(_gather())
    except Exception:
        return {}


def _list_tool_names(mcp_obj) -> list[str]:
    """Names of the tools registered on the FastMCP server (2.x / 3.x)."""
    return list(_tools_dict(mcp_obj).keys())


def _list_tool_records(mcp_obj, *, verbosity: int) -> list[dict]:
    """``{name, description, …}`` records, FastMCP version-agnostic."""
    return [
        _tool_record(name, tool, verbosity=verbosity)
        for name, tool in _tools_dict(mcp_obj).items()
    ]


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

        @click.group(
            "mcp", help="MCP server subcommands (start/doctor/list-tools/install)."
        )
        def mcp_group() -> None:
            pass

        attach_mcp_subcommands(mcp_group, server_path=_SERVER_PATH, cli_name=_CLI_NAME)
        main.add_command(mcp_group, name="mcp")
        return
    except ImportError:
        # scitex-dev not available — use the hand-rolled fallback.
        main.add_command(_fallback_mcp_group(), name="mcp")


# EOF
