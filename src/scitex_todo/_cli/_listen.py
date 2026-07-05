#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI verb ``scitex-todo listen`` — the persistent HTTP listen server.

scitex-todo's equivalent of ``sac listen``: a small always-on Starlette +
uvicorn daemon exposing a public ``/v1/health`` probe and a bearer-gated
``/v1/notify`` intake, while running the existing delivery + reminder loop in
its lifespan. Bare ``scitex-todo listen`` runs it in the foreground (what a
systemd ``ExecStart`` calls).

* ``scitex-todo listen`` — run the server foreground (blocking).
* ``scitex-todo listen status [--json]`` — pidfile / port / health report.
* ``scitex-todo listen stop`` — SIGTERM the running server (single-instance).
* ``scitex-todo listen print-token`` — print the resolved bearer token
  (so a peer channel can be configured to push to this door).

Requires the ``[listen]`` extra (starlette + uvicorn); a missing dep fails
loud with the install hint rather than a raw ImportError.
"""

from __future__ import annotations

import logging

import click


def register(main: click.Group) -> None:
    """Attach the ``listen`` group to the root group."""
    main.add_command(listen_group)


def _require_server_deps() -> None:
    """Fail loud with an actionable hint if starlette/uvicorn are absent."""
    try:
        import starlette  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError as exc:  # noqa: BLE001
        raise click.ClickException(
            "the listen server needs the [listen] extra — install with:\n"
            "  pip install 'scitex-todo[listen]'\n"
            f"(missing: {exc.name})"
        ) from exc


@click.group(
    "listen",
    invoke_without_command=True,
    help=(
        "Run scitex-todo's persistent HTTP listen server (foreground).\n\n"
        "Bare `scitex-todo listen` binds the server — public GET /v1/health + "
        "bearer-gated POST /v1/notify — and runs the delivery loop in its "
        "lifespan, until SIGTERM/SIGINT. Single-instance via a flock pidfile.\n\n"
        "Verbs:\n"
        "  status        pidfile / port / health report\n"
        "  stop          SIGTERM the running server\n"
        "  print-token   print the resolved bearer token\n\n"
        "Examples:\n"
        "  scitex-todo listen --port 7979\n"
        "  scitex-todo listen status --json\n"
        "  scitex-todo listen stop"
    ),
)
@click.option("--host", default=None, help="Bind host (default: 127.0.0.1).")
@click.option(
    "--port", type=int, default=None,
    help="Bind port (default: $SCITEX_TODO_LISTEN_PORT or 7979).",
)
@click.option(
    "--tasks", "tasks_path", default=None,
    help="Path to tasks.yaml (default resolution chain). Resolves the inbox, "
    "token, and pidfile dirs.",
)
@click.option(
    "--token-file", default=None,
    help="Bearer-token file (default: <runtime>/tokens/listen-<host>.token).",
)
@click.option(
    "--interval", type=float, default=120.0, show_default=True,
    help="Seconds between embedded delivery-loop ticks.",
)
@click.option(
    "--allow-non-loopback", is_flag=True,
    help="Permit binding a non-loopback host (deliberate opt-in).",
)
@click.option(
    "--no-delivery-loop", is_flag=True,
    help="Serve the HTTP door only; do NOT run the embedded delivery loop.",
)
@click.pass_context
def listen_group(
    ctx: click.Context,
    host: "str | None",
    port: "int | None",
    tasks_path: "str | None",
    token_file: "str | None",
    interval: float,
    allow_non_loopback: bool,
    no_delivery_loop: bool,
) -> None:
    """The ``listen`` group — bare invocation runs the server foreground."""
    if ctx.invoked_subcommand is not None:
        return

    _require_server_deps()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    from .._listen._run import DEFAULT_HOST, run_server

    try:
        run_server(
            host=host or DEFAULT_HOST,
            port=port,
            token_file=token_file,
            store=tasks_path,
            allow_non_loopback=allow_non_loopback,
            run_delivery_loop=not no_delivery_loop,
            interval=interval,
        )
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc


@listen_group.command("status", help="Report pidfile / port / health status.")
@click.option("--host", default=None, help="Probe host (default: 127.0.0.1).")
@click.option("--port", type=int, default=None, help="Probe port.")
@click.option("--tasks", "tasks_path", default=None, help="Path to tasks.yaml.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON.")
def status_cmd(
    host: "str | None", port: "int | None", tasks_path: "str | None", as_json: bool
) -> None:
    """Print the listen server's liveness status."""
    from .._listen._run import DEFAULT_HOST, server_status

    st = server_status(host=host or DEFAULT_HOST, port=port, store=tasks_path)
    if as_json:
        import json

        click.echo(json.dumps(st, indent=2))
        return
    running = st["port_bound"] and st["health_ok"]
    click.echo(f"# scitex-todo listen: {'UP' if running else 'DOWN'}")
    click.echo(f"#   pidfile     : {st['pidfile']}")
    click.echo(f"#   pid         : {st['pid']} (alive={st['pid_alive']})")
    click.echo(f"#   port {st['port']}   : bound={st['port_bound']}")
    click.echo(f"#   health probe: {st['health_ok']}")


@listen_group.command("stop", help="SIGTERM the running listen server.")
@click.option("--tasks", "tasks_path", default=None, help="Path to tasks.yaml.")
def stop_cmd(tasks_path: "str | None") -> None:
    """Send SIGTERM to the pid recorded in the listen pidfile."""
    import os
    import signal

    from .._listen._run import listen_pidfile_path

    pidfile = listen_pidfile_path(tasks_path)
    try:
        pid = int(pidfile.read_text(encoding="utf-8").strip())
    except (OSError, ValueError) as exc:
        raise click.ClickException(
            f"no running listen server found (pidfile {pidfile}: {exc})"
        ) from exc
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        raise click.ClickException(
            f"pidfile {pidfile} names pid {pid}, but no such process "
            f"(stale pidfile — the server is not running)."
        ) from None
    click.echo(f"# sent SIGTERM to scitex-todo listen (pid {pid})")


@listen_group.command(
    "print-token", help="Print the resolved bearer token (peer configuration)."
)
@click.option("--tasks", "tasks_path", default=None, help="Path to tasks.yaml.")
@click.option("--token-file", default=None, help="Bearer-token file override.")
def print_token_cmd(tasks_path: "str | None", token_file: "str | None") -> None:
    """Ensure + print the bearer token so a peer can be configured to push."""
    from .._listen.tokens import ensure_token

    click.echo(ensure_token(token_file, store=tasks_path))


# EOF
