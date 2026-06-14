#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Root ``scitex-todo`` group and core verbs (render-graph, list-tasks, board).

The §1a introspection / completion / skills groups live in sibling modules and
are attached to ``main`` at the bottom of this file.
"""

from __future__ import annotations

import json
import sys

import click

from .. import __version__
from .._mermaid import build_mermaid
from .._model import load_tasks
from .._paths import resolve_tasks_path
from .._render import render

_ROOT_EPILOG = (
    "Task store resolution (first existing wins): an explicit --tasks path, "
    "then $SCITEX_TODO_TASKS, then the project store "
    "<git-root>/.scitex/todo/tasks.yaml, then the user store "
    "~/.scitex/todo/tasks.yaml (relocatable via $SCITEX_DIR), then the bundled "
    "generic example. See the README 'Where your task data lives' section."
)


# --------------------------------------------------------------------------- #
# Top-level group (--help-recursive / --json universal flags)                 #
# --------------------------------------------------------------------------- #
def _iter_commands(cmd, ctx, prefix):
    """Yield ``(prefix, command, context)`` for ``cmd`` and every descendant."""
    yield prefix, cmd, ctx
    if isinstance(cmd, click.Group):
        for name, sub in sorted(cmd.commands.items()):
            sub_ctx = click.Context(sub, info_name=name, parent=ctx)
            yield from _iter_commands(sub, sub_ctx, f"{prefix} {name}")


def _command_tree(cmd, ctx):
    """Return a JSON-serializable ``{name, help, options, commands}`` tree."""
    node = {
        "name": ctx.info_name,
        "help": (cmd.help or "").strip(),
        "options": [p.opts[-1] for p in cmd.params if isinstance(p, click.Option)],
        "commands": {},
    }
    if isinstance(cmd, click.Group):
        for name, sub in sorted(cmd.commands.items()):
            sub_ctx = click.Context(sub, info_name=name, parent=ctx)
            node["commands"][name] = _command_tree(sub, sub_ctx)
    return node


def _emit_help_recursive(ctx, as_json):
    """Print flattened help (or the command tree as JSON) for every subcommand."""
    if as_json:
        click.echo(json.dumps(_command_tree(ctx.command, ctx), indent=2))
        return
    blocks: list[str] = []
    for prefix, cmd, sub_ctx in _iter_commands(ctx.command, ctx, ctx.info_name):
        blocks.append(f"### {prefix}\n{cmd.get_help(sub_ctx)}")
    click.echo("\n\n".join(blocks))


@click.group(
    invoke_without_command=True,
    help=f"scitex-todo (v{__version__}) — canonical YAML task store + adapters.",
    epilog=_ROOT_EPILOG,
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.option(
    "--help-recursive",
    "help_recursive",
    is_flag=True,
    help="Show help for every subcommand, flattened.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit machine-readable JSON (the command tree for the top level).",
)
@click.version_option(__version__, "-V", "--version", prog_name="scitex-todo")
@click.pass_context
def main(ctx: click.Context, help_recursive: bool, as_json: bool) -> None:
    """scitex-todo CLI entry point."""
    if help_recursive or as_json:
        _emit_help_recursive(ctx, as_json=as_json)
        ctx.exit(0)
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())
        ctx.exit(0)


# --------------------------------------------------------------------------- #
# render-graph                                                                #
# --------------------------------------------------------------------------- #
@main.command(
    "render-graph",
    help=(
        "Render the task dependency graph to a PNG.\n\n"
        "Example:\n"
        "  scitex-todo render-graph --tasks ./.scitex/todo/tasks.yaml -o tasks.png"
    ),
)
@click.option(
    "--tasks",
    "tasks_path",
    default=None,
    help="Path to tasks.yaml (default: project -> user -> bundled example, "
    "or $SCITEX_TODO_TASKS).",
)
@click.option(
    "-o",
    "--output",
    default="tasks.png",
    show_default=True,
    help="Output PNG path.",
)
@click.option(
    "--print-mermaid",
    is_flag=True,
    help="Print the generated mermaid source to stdout and exit (no render).",
)
def render_graph_cmd(tasks_path: str | None, output: str, print_mermaid: bool) -> None:
    """Render the resolved task store to a dependency PNG."""
    resolved = resolve_tasks_path(tasks_path)
    tasks = load_tasks(resolved)
    mermaid_src = build_mermaid(tasks)

    if print_mermaid:
        sys.stdout.write(mermaid_src)
        return

    engine = render(mermaid_src, output)
    click.echo(f"{output}  (rendered via {engine}; source: {resolved})")


# --------------------------------------------------------------------------- #
# list-tasks                                                                  #
# --------------------------------------------------------------------------- #
@main.command(
    "list-tasks",
    help=(
        "List tasks with optional filters.\n\n"
        "Without any filter, prints the same plain-text table / JSON array\n"
        "as before (backward-compatible). With one or more filters,\n"
        "matches are AND-composed.\n\n"
        "Examples:\n"
        "  scitex-todo list-tasks --assignee proj-scitex-todo --json\n"
        "  scitex-todo list-tasks --project scitex-todo --status pending --status in_progress\n"
        "  scitex-todo list-tasks --blocking-me\n"
        "  scitex-todo list-tasks --id-prefix proj-scitex-\n"
        "  scitex-todo list-tasks --blocker __none  # rows with no blocker"
    ),
)
@click.option(
    "--tasks",
    "tasks_path",
    default=None,
    help="Path to tasks.yaml (default: project -> user -> bundled example, "
    "or $SCITEX_TODO_TASKS).",
)
@click.option(
    "--scope",
    default=None,
    help="Match `scope` exactly (use '' to ignore $SCITEX_TODO_SCOPE).",
)
@click.option("--assignee", default=None, help="Match `assignee` exactly (PRIMARY linking field today).")
@click.option(
    "--agent",
    default=None,
    help="Match `agent` exactly (forward-compat alias for --assignee).",
)
@click.option("--project", default=None, help="Match `project` exactly.")
@click.option("--host", default=None, help="Match `host` exactly.")
@click.option(
    "--blocker",
    default=None,
    help="Match `blocker` exactly; `__none` matches rows with no blocker.",
)
@click.option(
    "--kind",
    default=None,
    help="Match `kind` exactly; `task` matches both explicit and absent rows.",
)
@click.option(
    "--id-prefix",
    "id_prefix",
    default=None,
    help="Match the front of `id` (cheap project-rollup lookup).",
)
@click.option(
    "--blocking-me",
    "blocking_me",
    is_flag=True,
    help="Predicate: status=blocked AND blocker=operator-decision (BLOCKING-YOU panel).",
)
@click.option(
    "--overdue",
    is_flag=True,
    help=(
        "Predicate: tasks past their next deadline AND not in a terminal "
        "lifecycle state (done / deferred / failed / goal). Uses the "
        "deadline / deadlines schema + repeater rules from "
        "scitex_todo._model.is_overdue (PR #125, todo-p6-overdue-ui)."
    ),
)
@click.option(
    "--status",
    "statuses",
    multiple=True,
    help="Match `status` exactly. Repeat for multi-status filter.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the resolved tasks as a JSON array.",
)
def list_tasks_cmd(
    tasks_path: str | None,
    scope: str | None,
    assignee: str | None,
    agent: str | None,
    project: str | None,
    host: str | None,
    blocker: str | None,
    kind: str | None,
    id_prefix: str | None,
    blocking_me: bool,
    overdue: bool,
    statuses: tuple,
    as_json: bool,
) -> None:
    """Print the resolved task list (filtered or not)."""
    # Normalize: click's multiple=True returns a tuple; the helper
    # signature takes a list[str] | None. Empty tuple = no constraint.
    statuses_list: list[str] | None = list(statuses) if statuses else None
    # Did the caller pass ANY filter? Drive the dispatch off this.
    has_filter = any(
        v is not None for v in (
            scope, assignee, agent, project, host, blocker, kind, id_prefix,
        )
    ) or bool(statuses_list) or blocking_me or overdue

    if has_filter:
        from ._admin import list_tasks_filtered

        list_tasks_filtered(
            scope,
            assignee,
            # Legacy positional `status` (single) is None when --status
            # is empty / multi; the multi case feeds `statuses=`.
            None,
            as_json,
            tasks_path,
            statuses=statuses_list,
            agent=agent,
            project=project,
            host=host,
            blocker=blocker,
            kind=kind,
            id_prefix=id_prefix,
            blocking_me=blocking_me,
            overdue=overdue,
        )
        return
    # Plain path — backward-compatible plain table / JSON array.
    resolved = resolve_tasks_path(tasks_path)
    tasks = load_tasks(resolved)
    if as_json:
        click.echo(json.dumps(tasks))
        return
    click.echo(f"# {resolved}  ({len(tasks)} tasks)")
    for task in tasks:
        click.echo(f"{task['id']:<24} {task['status']:<12} {task['title']}")


# --------------------------------------------------------------------------- #
# board <verb>                                                                #
# --------------------------------------------------------------------------- #
# Lifecycle verbs: start / stop / restart / status (operator TG12949/12950/
# 12951 via lead a2a `b5726672`). Pre-this-change `scitex-todo board` was
# a bare NOUN that launched directly — CLI noun-verb violation, and the
# operator had no clean way to restart after a card/source change ("port
# already in use" trap).
#
# Pidfile at ``~/.scitex/todo/board.pid`` so stop/restart/status are
# reliable across terminals. Bare ``scitex-todo board`` (no subcommand)
# stays back-compat: forwards to ``board start`` with a DeprecationWarning
# to stderr — operator's muscle memory survives, audit-cli flags it for
# eventual removal.

from pathlib import Path as _Path

BOARD_PIDFILE = _Path.home() / ".scitex" / "todo" / "board.pid"


def _board_pidfile() -> _Path:
    """Return the pidfile path (function so tests can override via env)."""
    import os as _os
    override = _os.environ.get("SCITEX_TODO_BOARD_PIDFILE")
    if override:
        return _Path(override)
    return BOARD_PIDFILE


def _board_pid_alive(pid: int) -> bool:
    """``os.kill(pid, 0)`` is the POSIX 'is this PID up?' probe."""
    import os as _os
    try:
        _os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    except OSError:
        return False
    return True


def _board_read_pid() -> int | None:
    """Read the pidfile; return None when absent/unreadable/dead."""
    pf = _board_pidfile()
    if not pf.exists():
        return None
    try:
        pid = int(pf.read_text().strip())
    except (OSError, ValueError):
        return None
    if not _board_pid_alive(pid):
        # Stale pidfile from a crashed process — clean it up.
        try:
            pf.unlink()
        except OSError:
            pass
        return None
    return pid


def _board_write_pid(pid: int) -> None:
    """Write the pidfile, creating parent dirs as needed."""
    pf = _board_pidfile()
    pf.parent.mkdir(parents=True, exist_ok=True)
    pf.write_text(str(pid))


def _board_run_server(
    tasks_path: str | None, port: int, no_browser: bool,
) -> None:
    """Foreground-blocking server start (the historical board_cmd body).

    Writes the pidfile BEFORE handing off to Django's runserver loop and
    removes it on exit (clean shutdown via Ctrl-C, OR exception). Other
    terminals can `board stop` against the pidfile to SIGTERM us.
    """
    import os as _os

    try:
        import django  # noqa: F401
    except ImportError:
        raise click.ClickException(
            "The board needs the web extra. Install it with:\n"
            "  pip install scitex-todo[web]"
        ) from None

    _os.environ.setdefault(
        "DJANGO_SETTINGS_MODULE", "scitex_todo._django.settings",
    )
    import django as _dj
    _dj.setup()
    from django.core.management import call_command

    args = ["scitex_todo_board", "--port", str(port)]
    if tasks_path:
        args += ["--tasks", tasks_path]
    if no_browser:
        args += ["--no-browser"]

    _board_write_pid(_os.getpid())
    try:
        call_command(*args)
    finally:
        pf = _board_pidfile()
        try:
            if pf.exists():
                pf.unlink()
        except OSError:
            pass


@main.group(
    "board",
    invoke_without_command=True,
    help=(
        "Manage the dependency-graph board (start/stop/restart/status).\n\n"
        "The ``board`` noun REQUIRES an explicit verb — bare "
        "``scitex-todo board`` hard-errors with a redirect (operator "
        "directive TG 13316: noun-verb CLI convention, no bare-noun "
        "back-compat).\n\n"
        "Examples:\n"
        "  scitex-todo board start --port 8051\n"
        "  scitex-todo board restart\n"
        "  scitex-todo board status\n"
        "  scitex-todo board stop"
    ),
)
@click.pass_context
def board_group(ctx: click.Context) -> None:
    """The ``board`` noun group — REQUIRES an explicit verb.

    Click runs the group function FIRST, then dispatches the subcommand
    if one is named. When no subcommand is named, we HARD-ERROR with a
    redirect message + exit 2 (Click's standard usage-error code) so
    every existing call site is forced to migrate.

    Operator-direct directive TG 13316 (relayed by lead a2a
    ``c36b0d1e``): the previous deprecation-warn-and-forward path
    (PR #139, v0.7.6) hid the violation from audit tools — replacing it
    with a hard error makes the noun-verb convention enforceable across
    the fleet.

    In-tree call sites updated in this same PR: ``_jobs_provider.py``.
    Host-side systemd unit ``scitex-todo.dashboard.service`` ExecStart
    also needs the same migration — flagged for lead's host-side pass.
    """
    if ctx.invoked_subcommand is not None:
        # User typed `scitex-todo board start/stop/...` — let Click route
        # to the subcommand.
        return
    # Bare `scitex-todo board` — HARD ERROR.
    click.echo(
        "ERROR: `scitex-todo board` (no verb) is no longer supported.\n"
        "Operator directive TG 13316 — noun-verb CLI convention. Use:\n"
        "  scitex-todo board start [--port N] [--no-browser]\n"
        "  scitex-todo board stop\n"
        "  scitex-todo board restart\n"
        "  scitex-todo board status",
        err=True,
    )
    ctx.exit(2)


@board_group.command(
    "start",
    help=(
        "Launch the board server (blocking, foreground). Writes a "
        "pidfile at ~/.scitex/todo/board.pid so other terminals can "
        "`board stop` / `board restart`. Requires the web extra: "
        "pip install scitex-todo[web]\n\n"
        "Example:\n"
        "  $ scitex-todo board start --port 8051"
    ),
)
@click.option(
    "--tasks", "tasks_path", default=None,
    help="Path to tasks.yaml (default: project -> user -> bundled, "
    "or $SCITEX_TODO_TASKS).",
)
@click.option(
    "--port", type=int, default=8051, show_default=True,
    help="Server port.",
)
@click.option(
    "--no-browser", is_flag=True,
    help="Don't open a browser automatically.",
)
@click.option(
    "--dry-run", is_flag=True,
    help="Print the planned launch (port + tasks + browser flag) "
    "without starting the server. Required by SciTeX §2 audit on "
    "mutating verbs.",
)
@click.option(
    "-y", "--yes", "assume_yes", is_flag=True,
    help="Skip the interactive confirmation (no-op today; `start` is "
    "non-interactive). Accepted per SciTeX §2 audit on mutating verbs.",
)
def board_start_cmd(
    tasks_path: str | None, port: int, no_browser: bool,
    dry_run: bool, assume_yes: bool,
) -> None:
    """Foreground start. Pidfile written; removed on clean shutdown.

    Example:
      $ scitex-todo board start --port 8051
    """
    _ = assume_yes  # accepted for §2 compliance; non-interactive verb.
    # Guard rail: refuse to start if another board is already up so we
    # don't fight over the pidfile or the port.
    existing = _board_read_pid()
    if existing is not None:
        raise click.ClickException(
            f"board is already running (pid {existing}). Use "
            "`scitex-todo board stop` or `restart`."
        )
    if dry_run:
        click.echo(
            f"# dry-run: would start board on port {port}, "
            f"tasks={tasks_path or '<default-resolution>'}, "
            f"no-browser={bool(no_browser)} "
            f"(pidfile: {_board_pidfile()})",
        )
        return
    _board_run_server(tasks_path, port, no_browser)


@board_group.command(
    "stop",
    help=(
        "Stop the running board via the pidfile (SIGTERM).\n\n"
        "Example:\n"
        "  $ scitex-todo board stop"
    ),
)
@click.option(
    "--timeout", type=float, default=5.0, show_default=True,
    help="Seconds to wait for graceful exit before SIGKILL.",
)
@click.option(
    "--dry-run", is_flag=True,
    help="Print the planned action without sending SIGTERM. Required "
    "by SciTeX §2 audit on mutating verbs.",
)
@click.option(
    "-y", "--yes", "assume_yes", is_flag=True,
    help="Skip the interactive confirmation (no-op today; `stop` is "
    "non-interactive). Accepted per SciTeX §2 audit on mutating verbs.",
)
def board_stop_cmd(timeout: float, dry_run: bool, assume_yes: bool) -> None:
    """Read the pidfile, SIGTERM, wait, escalate to SIGKILL if needed.

    Example:
      $ scitex-todo board stop
    """
    _ = assume_yes  # accepted for §2 compliance; non-interactive verb.
    if dry_run:
        pid = _board_read_pid()
        if pid is None:
            click.echo("# dry-run: board is not running (no pidfile / stale).")
        else:
            click.echo(
                f"# dry-run: would SIGTERM pid {pid} "
                f"(timeout {timeout}s, then SIGKILL).",
            )
        return
    import os as _os
    import signal as _signal
    import time as _time

    pid = _board_read_pid()
    if pid is None:
        click.echo("# board is not running (no pidfile / stale).")
        return
    try:
        _os.kill(pid, _signal.SIGTERM)
    except OSError as e:
        raise click.ClickException(f"could not SIGTERM pid {pid}: {e}")
    # Poll for graceful exit.
    deadline = _time.time() + timeout
    while _time.time() < deadline:
        if not _board_pid_alive(pid):
            click.echo(f"# stopped board (pid {pid}).")
            # Clean up pidfile (the foreground process's finally
            # also tries to remove it; this is idempotent).
            pf = _board_pidfile()
            try:
                if pf.exists():
                    pf.unlink()
            except OSError:
                pass
            return
        _time.sleep(0.1)
    # Still alive — escalate.
    try:
        _os.kill(pid, _signal.SIGKILL)
        click.echo(
            f"# board did not exit in {timeout}s; sent SIGKILL to pid {pid}.",
            err=True,
        )
    except OSError as e:
        raise click.ClickException(f"could not SIGKILL pid {pid}: {e}")
    pf = _board_pidfile()
    try:
        if pf.exists():
            pf.unlink()
    except OSError:
        pass


@board_group.command(
    "restart",
    help=(
        "Stop the running board (if any) + start a fresh one. The shape "
        "the operator + lead need to reload after a card/source change."
        "\n\nExample:\n  $ scitex-todo board restart"
    ),
)
@click.option("--tasks", "tasks_path", default=None,
              help="Path to tasks.yaml.")
@click.option("--port", type=int, default=8051, show_default=True,
              help="Server port.")
@click.option("--no-browser", is_flag=True,
              help="Don't open a browser automatically.")
@click.option(
    "--dry-run", is_flag=True,
    help="Print the planned stop+start without acting. Required by "
    "SciTeX §2 audit on mutating verbs.",
)
@click.option(
    "-y", "--yes", "assume_yes", is_flag=True,
    help="Skip the interactive confirmation (no-op; `restart` is "
    "non-interactive). Accepted per SciTeX §2 audit on mutating verbs.",
)
@click.pass_context
def board_restart_cmd(
    ctx: click.Context,
    tasks_path: str | None, port: int, no_browser: bool,
    dry_run: bool, assume_yes: bool,
) -> None:
    """Stop then start. Both go through the same pidfile contract.

    Example:
      $ scitex-todo board restart
    """
    _ = assume_yes  # accepted for §2 compliance; non-interactive verb.
    if dry_run:
        pid = _board_read_pid()
        prefix = "running" if pid else "not running"
        click.echo(
            f"# dry-run: would stop (currently {prefix}) then start "
            f"on port {port}, "
            f"tasks={tasks_path or '<default-resolution>'}, "
            f"no-browser={bool(no_browser)}",
        )
        return
    # `stop` is a no-op if nothing's running — that's fine.
    ctx.invoke(board_stop_cmd, timeout=5.0, dry_run=False, assume_yes=True)
    ctx.invoke(
        board_start_cmd,
        tasks_path=tasks_path, port=port, no_browser=no_browser,
        dry_run=False, assume_yes=True,
    )


@board_group.command(
    "status",
    help=(
        "Print whether the board is running + its pid + the pidfile path."
        "\n\nExample:\n  $ scitex-todo board status\n"
        "  $ scitex-todo board status --json"
    ),
)
@click.option(
    "--json", "as_json", is_flag=True,
    help="Emit a JSON object (machine-readable). Required by SciTeX "
    "§2 audit on read verbs.",
)
def board_status_cmd(as_json: bool) -> None:
    """One-line status read off the pidfile.

    Example:
      $ scitex-todo board status
      $ scitex-todo board status --json
    """
    import json as _json
    pid = _board_read_pid()
    pf = _board_pidfile()
    running = pid is not None
    if as_json:
        click.echo(_json.dumps({
            "running": running,
            "pid": pid,
            "pidfile": str(pf),
        }))
        return
    if not running:
        click.echo(f"# board is NOT running (pidfile: {pf})")
        return
    click.echo(f"# board is running (pid {pid}, pidfile: {pf})")


# --------------------------------------------------------------------------- #
# index <verb> — SQLite derived-index lifecycle (PR-B of Stage 2 plan,        #
# lead a2a `aa02fb0e`).                                                       #
# --------------------------------------------------------------------------- #


@main.group(
    "index",
    help=(
        "Manage the SQLite derived-index "
        "(~/.scitex/todo/.tasks.index.sqlite).\n\n"
        "YAML stays authoritative; the index is a rebuildable read cache."
    ),
)
def index_group() -> None:
    """The ``index`` noun group — verbs rebuild + info."""


@index_group.command(
    "rebuild",
    help=(
        "Drop + repopulate the SQLite index from the YAML source(s) — "
        "global store + every discovered per-project lane (PR #137 "
        "union policy).\n\n"
        "Example:\n"
        "  $ scitex-todo index rebuild -y"
    ),
)
@click.option(
    "--dry-run", is_flag=True,
    help="Print what would be rebuilt (source paths + projected row "
    "count) without touching the index. Required by SciTeX §2 audit on "
    "mutating verbs.",
)
@click.option(
    "-y", "--yes", "assume_yes", is_flag=True,
    help="Skip the interactive confirmation. Required when the planned "
    "action would mutate the index and stdin is a TTY.",
)
def index_rebuild_cmd(dry_run: bool, assume_yes: bool) -> None:
    """Drop + repopulate the SQLite index from the YAML source(s).

    Example:
      $ scitex-todo index rebuild -y
    """
    import sys as _sys

    from scitex_todo._index import index_path, rebuild_index
    from scitex_todo._django.services import _discover_lanes
    from scitex_todo._paths import resolve_tasks_path

    global_path = resolve_tasks_path(None)
    lane_paths = _discover_lanes()
    target = index_path()

    if dry_run:
        click.echo(
            f"# dry-run: would rebuild {target}\n"
            f"#   global: {global_path}\n"
            f"#   lanes ({len(lane_paths)}):"
        )
        for lp in lane_paths:
            click.echo(f"#     - {lp}")
        return

    if not assume_yes and _sys.stdin.isatty():
        raise click.ClickException(
            "`index rebuild` mutates the SQLite index. Pass -y / --yes "
            "to confirm, or --dry-run to preview."
        )
    stats = rebuild_index(global_path, lane_paths)
    click.echo(
        f"# rebuilt {target}: {stats['total']} tasks "
        f"({stats['global']} global + {stats['lanes']} lane, "
        f"{stats['skipped']} skipped)"
    )


@index_group.command(
    "info",
    help=(
        "Print one-line / JSON status of the SQLite index "
        "(row count, last_index_at, schema version, lane count).\n\n"
        "Example:\n"
        "  $ scitex-todo index info\n"
        "  $ scitex-todo index info --json"
    ),
)
@click.option(
    "--json", "as_json", is_flag=True,
    help="Emit machine-readable JSON. Required by SciTeX §2 audit on "
    "read verbs.",
)
def index_info_cmd(as_json: bool) -> None:
    """Read-side report on the SQLite index.

    Example:
      $ scitex-todo index info
      $ scitex-todo index info --json
    """
    import json as _json

    from scitex_todo._index import info

    payload = info()
    if as_json:
        click.echo(_json.dumps(payload))
        return
    if not payload["exists"]:
        click.echo(f"# index does not exist yet: {payload['path']}")
        click.echo("# run `scitex-todo index rebuild -y` to populate.")
        return
    click.echo(
        f"# index: {payload['path']}\n"
        f"#   rows: {payload['rows']}\n"
        f"#   schema version: {payload['index_version']}\n"
        f"#   last index at: {payload['last_index_at']}\n"
        f"#   yaml mtime: {payload['yaml_mtime']}\n"
        f"#   lane count: {payload['lane_count']}"
    )


# --------------------------------------------------------------------------- #
# migration <verb> — directory-card enforcement migration (PR-D).             #
# Operator directive 2026-06-13 (via lead a2a 3cf31901): canonical card =     #
# tasks/<id>/ directory; flat tasks.yaml writes forbidden. Two-phase rollout: #
# `migration plan` is the read-side dry-run scanner; `migration apply` is the #
# operator-blessed mutation. The group token is a NOUN (`migration`) per the  #
# SciTeX noun-verb CLI convention (audit-cli §1, non-leaf nodes are nouns).   #
# --------------------------------------------------------------------------- #


@main.group(
    "migration",
    help=(
        "Directory-card migration verbs (operator directive 2026-06-13).\n\n"
        "`migration plan` is the read-side dry-run scanner emitted to the "
        "operator for review. `migration apply` (gated, operator-blessed) "
        "performs the actual flat-to-directory conversion."
    ),
)
def migration_group() -> None:
    """The ``migration`` noun group."""


@migration_group.command(
    "plan",
    help=(
        "Scan every discovered lane + the global store; emit a plan "
        "classifying each row as CANONICAL or NEEDS_* (DIR / NOTE / TITLE "
        "/ COMMENT). NO writes. The output is the artifact shown to the "
        "operator before any real `migration apply`.\n\n"
        "Example:\n"
        "  $ scitex-todo migration plan --json\n"
        "  $ scitex-todo migration plan --markdown"
    ),
)
@click.option(
    "--json", "as_json", is_flag=True,
    help="Emit the plan as JSON (machine-readable). Required by SciTeX "
    "§2 audit on read verbs.",
)
@click.option(
    "--markdown", "as_md", is_flag=True,
    help="Emit the plan as Markdown (operator-facing review document).",
)
def migration_plan_cmd(as_json: bool, as_md: bool) -> None:
    """Read-side dry-run scanner.

    Example:
      $ scitex-todo migration plan --json
      $ scitex-todo migration plan --markdown
    """
    import json as _json

    from scitex_todo._migration import render_markdown, scan_all_lanes

    fleet = scan_all_lanes()
    if as_md:
        click.echo(render_markdown(fleet))
        return
    if as_json:
        click.echo(_json.dumps(fleet.to_dict(), indent=2))
        return
    # Default: short summary.
    top = fleet.to_dict()
    click.echo(
        f"# migration plan: {top['lane_count']} lane(s), "
        f"{top['total_rows']} rows — "
        f"{top['canonical_rows']} canonical, "
        f"{top['needs_migration_rows']} need migration. "
        f"Pass --json or --markdown for detail."
    )


@migration_group.command(
    "apply",
    help=(
        "Run the directory-card migration: for every row that needs it, "
        "write tasks/<id>/README.md (atomic + bytes-equal verified) "
        "THEN strip the migrated fields from tasks.yaml. Per-lane git "
        "commit at end. Operator-blessed for ALL 7 lanes (2026-06-13).\n\n"
        "Example:\n"
        "  $ scitex-todo migration apply --dry-run\n"
        "  $ scitex-todo migration apply -y"
    ),
)
@click.option(
    "--dry-run", is_flag=True,
    help="Print the planned actions without touching disk. Required "
    "by SciTeX §2 audit on mutating verbs.",
)
@click.option(
    "-y", "--yes", "assume_yes", is_flag=True,
    help="Skip the interactive confirmation. Required when the planned "
    "action would mutate the store.",
)
@click.option(
    "--json", "as_json", is_flag=True,
    help="Emit per-lane counts + per-row outcomes as JSON.",
)
def migration_apply_cmd(
    dry_run: bool, assume_yes: bool, as_json: bool,
) -> None:
    """Run the migration across every discovered lane + global store.

    Example:
      $ scitex-todo migration apply --dry-run
      $ scitex-todo migration apply -y
    """
    import json as _json
    import sys as _sys

    from scitex_todo._migration import apply_all_lanes

    if not dry_run and not assume_yes and _sys.stdin.isatty():
        raise click.ClickException(
            "`migration apply` mutates lane YAMLs + writes README.md files. "
            "Pass -y / --yes to confirm, or --dry-run to preview."
        )

    results = apply_all_lanes(dry_run=dry_run)

    if as_json:
        click.echo(_json.dumps(
            [r.to_dict() for r in results], indent=2,
        ))
        return

    # Human summary.
    total_written = 0
    total_updated = 0
    total_skipped = 0
    for lr in results:
        click.echo(
            f"# {lr.lane_path}: written={lr.written_count} "
            f"updated={lr.updated_count} skipped={lr.skipped_count} "
            f"git_committed={lr.git_committed} "
            f"({lr.git_skip_reason or 'ok'})"
        )
        total_written += lr.written_count
        total_updated += lr.updated_count
        total_skipped += lr.skipped_count
    click.echo(
        f"# TOTAL: written={total_written} updated={total_updated} "
        f"skipped={total_skipped}"
        + (" (DRY-RUN — no disk changes)" if dry_run else "")
    )


# --------------------------------------------------------------------------- #
# Attach the §1a sub-groups (defined in sibling modules).                     #
# --------------------------------------------------------------------------- #
from . import _completion, _introspect, _loop, _mcp, _runnable, _skills, _stats, _write  # noqa: E402

_introspect.register(main)
_completion.register(main)
_skills.register(main)
# `stats` + `sync-github` (operator standing direction via lead a2a
# `4b23ebc1` / `7489ac31` / `6f24a752` / `5263c8d9` / `02b71bd0` /
# `130cc5ac`, 2026-06-12). Shared aggregator in `_throughput.py`.
_stats.register(main)
# Phase 1 mutation/admin verbs: add / update / done / list / summary /
# where / init / sync(stub). See GITIGNORED/ARCHITECTURE.md.
_write.register(main)
# Phase 1 MCP subgroup — §3 required four (start / doctor / list-tools /
# install). The module itself loads cleanly without fastmcp installed;
# individual verbs print a clear install hint when fastmcp is missing.
_mcp.register(main)
# P3b + P3d (lead-approved 2026-06-12) — self-consuming board loop.
# `scitex-todo next` returns the top runnable task for an agent;
# `scitex-todo watch --push` is the push side that wakes agents on
# new/commented/changed tasks. See _skills/scitex-todo/32_*.md for the
# 7-step agent self-consumption pattern.
_loop.register(main)
# T1.2 (lead a2a `74db4f2d`, 2026-06-14) — the parallelism dispatcher's
# batch runnable view. Sister to `next` (single pick); respects
# depends_on closure. See _runnable.py for the predicate.
_runnable.register(main)


if __name__ == "__main__":
    main()

# EOF
