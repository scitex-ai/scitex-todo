#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI verb ``scitex-todo health`` — the package-level health doctor.

A BROAD store / identity / delivery health check (distinct from
``scitex-todo mcp doctor``, which only checks the fastmcp install). Thin
wrapper over :func:`scitex_cards._health.health`: it prints a human-readable
report by default, or the raw standard-shape JSON with ``--json``.

Exit code mirrors health: ``0`` when every check is ok, ``1`` otherwise — so
``scitex-todo health`` is usable as a shell gate / CI probe.
"""

from __future__ import annotations

import json

import click

from ._compat import deprecated_alias, spec_command_kwargs

#: Version that removes the Phase-W ``health`` alias (doctrine §5).
_REMOVE_IN = "0.20.0"


def register(main: click.Group) -> None:
    """Attach ``doctor`` (+ the Phase-W ``health`` alias) to the root group.

    ``health`` is a noun; doctrine §1 forbids it as a bare top-level leaf and
    names ``doctor`` as the sanctioned intransitive exception for exactly this
    command shape (04_exceptions.md). The module's own docstring already
    called it "the health doctor" — the name now says so too.
    """
    main.add_command(doctor_cmd)
    deprecated_alias(main, "health", target="doctor", remove_in=_REMOVE_IN)


@click.command(
    "doctor",
    **spec_command_kwargs(
        summary="Run the package health doctor: store / agent-id / delivery.",
        description=(
            "Broader than `mcp doctor` (which only checks the fastmcp "
            "install): verifies the resolved task store is canonical + "
            "readable/writable, the agent id resolves, the notifyd delivery "
            "daemon is alive, this agent's channel inbox is draining, and "
            "the channel server is present. Exit 0 when all checks pass, "
            "else 1 — so it is usable as a shell gate / CI probe.",
        ),
        examples=(
            ("{prog} doctor", "Human-readable report."),
            ("{prog} doctor --json", "Raw standard-shape JSON."),
        ),
    ),
)
@click.option(
    "--tasks",
    "tasks_path",
    default=None,
    help="Path to tasks.yaml (default: project -> user -> bundled example, "
    "or $SCITEX_TODO_TASKS_YAML_SHARED).",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the raw standard-shape JSON report.",
)
def doctor_cmd(tasks_path: str | None, as_json: bool) -> None:
    """Print the health report (human or JSON) and exit non-zero if unhealthy."""
    from .._health import health

    report = health(store=tasks_path)
    if as_json:
        click.echo(json.dumps(report))
        raise SystemExit(0 if report["ok"] else 1)

    status = "OK" if report["ok"] else "UNHEALTHY"
    click.echo(f"# scitex-todo health: {status} — {report['summary']}")
    for check in report["checks"]:
        mark = "ok  " if check["ok"] else "FAIL"
        click.echo(f"[{mark}] {check['name']}: {check['detail']}")
        if check["hint"]:
            click.echo(f"        hint: {check['hint']}")
    raise SystemExit(0 if report["ok"] else 1)


# EOF
