#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI noun group ``scitex-todo db`` — shadow-SQLite operability verbs (S0).

STAGE S0 (RFC #348): the SQLite DB is a SHADOW bootstrapped from the canonical
YAML store; nothing reads it as truth yet. These verbs are the operability
surface:

  * ``db path``            — print the resolved shadow-DB path.
  * ``db verify``          — open the DB, check user_version + table counts.
  * ``db import --from-yaml`` — (re)bootstrap the DB from ``tasks.yaml``.

The group token is a NOUN per the SciTeX noun-verb CLI convention. Attached to
the root group via :func:`register`, mirroring the sibling ``migration`` /
``health`` modules so the over-budget ``_main.py`` stays untouched.
"""

from __future__ import annotations

import json

import click


def register(main: click.Group) -> None:
    """Attach the ``db`` noun group to the root group."""
    main.add_command(db_group)


@click.group(
    "db",
    help=(
        "Shadow-SQLite store verbs (SQLite migration S0, RFC #348).\n\n"
        "The DB is a SHADOW bootstrapped from the canonical tasks.yaml; the "
        "YAML stays the source of truth and no read/write path uses the DB "
        "yet. `db path` prints the resolved DB location, `db verify` checks "
        "schema health, and `db import --from-yaml` (re)builds the DB from "
        "the YAML (idempotent, never modifies the YAML)."
    ),
)
def db_group() -> None:
    """The ``db`` noun group."""


_DB_OPTION = click.option(
    "--db",
    "db_path",
    default=None,
    help="Explicit DB path (default: $SCITEX_TODO_DB, else ~/.scitex/todo/todo.db).",
)


@db_group.command(
    "path",
    help=(
        "Print the resolved shadow-DB path.\n\n"
        "Precedence: --db arg > $SCITEX_TODO_DB > local_state.user_path "
        "('todo','todo.db'). Delegates the user tier to the ecosystem "
        "resolver (never a re-rolled project/user precedence).\n\n"
        "Example:\n"
        "  scitex-todo db path"
    ),
)
@_DB_OPTION
def db_path_cmd(db_path: str | None) -> None:
    """Print the resolved DB path."""
    from .._db import resolve_db_path

    click.echo(str(resolve_db_path(db_path)))


@db_group.command(
    "verify",
    help=(
        "Open the shadow DB and verify its schema health.\n\n"
        "Checks PRAGMA user_version, the schema_meta version, presence of "
        "every expected table (with row counts), and PRAGMA quick_check. "
        "Exit 0 when healthy, else 1. Pass --json for the raw report.\n\n"
        "Example:\n"
        "  scitex-todo db verify\n"
        "  scitex-todo db verify --json"
    ),
)
@_DB_OPTION
@click.option("--json", "as_json", is_flag=True, help="Emit the raw report as JSON.")
def db_verify_cmd(db_path: str | None, as_json: bool) -> None:
    """Verify the DB schema + integrity."""
    from .._db import verify

    report = verify(db_path)
    if as_json:
        click.echo(json.dumps(report))
        raise SystemExit(0 if report["ok"] else 1)

    status = "OK" if report["ok"] else "UNHEALTHY"
    click.echo(f"# scitex-todo db verify: {status} — {report['path']}")
    if not report["exists"]:
        click.echo("[FAIL] db does not exist yet (run `db import --from-yaml`)")
        raise SystemExit(1)
    click.echo(
        f"  user_version={report['user_version']} "
        f"schema_version={report['schema_version']} "
        f"quick_check={report['quick_check']} source={report['source']}"
    )
    for name, count in report["tables"].items():
        click.echo(f"  {name}: {count}")
    raise SystemExit(0 if report["ok"] else 1)


@db_group.command(
    "import",
    help=(
        "Bootstrap the shadow DB from the canonical YAML store.\n\n"
        "Reads tasks.yaml (tasks + users + inboxes) and the threads.yaml "
        "sidecar and rebuilds every DB table in one transaction. Idempotent "
        "(re-run = same state). The YAML is opened READ-ONLY and never "
        "modified. Requires --from-yaml (the only S0 source).\n\n"
        "Example:\n"
        "  scitex-todo db import --from-yaml\n"
        "  scitex-todo db import --from-yaml --tasks /path/to/tasks.yaml --json"
    ),
)
@click.option(
    "--from-yaml",
    "from_yaml",
    is_flag=True,
    help="Import from the YAML store (the only source in S0). Required.",
)
@click.option(
    "--tasks",
    "tasks_path",
    default=None,
    help="Path to tasks.yaml (default: user store / $SCITEX_TODO_TASKS_YAML_SHARED).",
)
@_DB_OPTION
@click.option("--json", "as_json", is_flag=True, help="Emit the import summary as JSON.")
def db_import_cmd(
    from_yaml: bool,
    tasks_path: str | None,
    db_path: str | None,
    as_json: bool,
) -> None:
    """(Re)bootstrap the shadow DB from the YAML store."""
    if not from_yaml:
        raise click.ClickException(
            "`db import` requires --from-yaml (the only source in S0)."
        )
    from .._db_bootstrap import import_from_yaml

    summary = import_from_yaml(tasks_path=tasks_path, db_path=db_path)
    if as_json:
        click.echo(json.dumps(summary))
        return
    click.echo(
        f"# imported YAML -> shadow DB\n"
        f"  yaml: {summary['yaml_path']}\n"
        f"  db:   {summary['db_path']}\n"
        f"  tasks={summary['tasks']} comments={summary['comments']} "
        f"edges={summary['edges']} roles={summary['roles']}\n"
        f"  users={summary['users']} user_names={summary['user_names']} "
        f"notifications={summary['notifications']} messages={summary['messages']}"
    )


# EOF
