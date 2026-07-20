#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""READ-ONLY ``scitex-cards db`` verbs: ``show-path`` / ``validate`` / ``rehearse``.

Split out of ``_cli/_db.py`` when converting the group's help to ``CliHelp``
specs pushed that module past the 512-line cap. The seam is the one the
group's own help text already drew: verbs that only LOOK at the DB live here;
verbs that MOVE data live in :mod:`scitex_cards._cli._db_transfer`.

Nothing in this module writes — `rehearse` copies the store to a throwaway
workdir first, precisely so the live store stays untouched.
"""

from __future__ import annotations

import json

import click

from ._compat import spec_command_kwargs
from ._db_options import DB_OPTION


@click.command(
    "show-path",
    **spec_command_kwargs(
        summary="Print the resolved DB path.",
        description=(
            "Precedence: --db arg > $SCITEX_CARDS_DB > $SCITEX_TODO_DB "
            "(deprecated, warned) > local_state.user_path('cards','cards.db'). "
            "Delegates the user tier to the ecosystem resolver (never a "
            "re-rolled project/user precedence).",
        ),
        examples=(
            ("{prog} db show-path", "Where does the shadow DB live?"),
            ("{prog} db show-path --json", "The same, machine-readable."),
        ),
    ),
)
@DB_OPTION
@click.option("--json", "as_json", is_flag=True, help="Emit {'path': ...} as JSON.")
def db_show_path_cmd(db_path: str | None, as_json: bool) -> None:
    """Print the resolved DB path."""
    from .._db import resolve_db_path

    resolved = str(resolve_db_path(db_path))
    click.echo(json.dumps({"path": resolved}) if as_json else resolved)


@click.command(
    "validate",
    **spec_command_kwargs(
        summary="Open the shadow DB and validate its schema health.",
        description=(
            "Checks PRAGMA user_version, the schema_meta version, presence "
            "of every expected table (with row counts), and PRAGMA "
            "quick_check. Exit 0 when healthy, else 1.",
        ),
        examples=(
            ("{prog} db validate", "Human-readable health report."),
            ("{prog} db validate --json", "Raw report."),
        ),
    ),
)
@DB_OPTION
@click.option("--json", "as_json", is_flag=True, help="Emit the raw report as JSON.")
def db_validate_cmd(db_path: str | None, as_json: bool) -> None:
    """Validate the DB schema + integrity."""
    from .._db import verify

    report = verify(db_path)
    if as_json:
        click.echo(json.dumps(report))
        raise SystemExit(0 if report["ok"] else 1)

    status = "OK" if report["ok"] else "UNHEALTHY"
    click.echo(f"# db validate: {status} — {report['path']}")
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


@click.command(
    "rehearse",
    **spec_command_kwargs(
        summary="Cutover rehearsal: prove yaml -> cards.db -> yaml is exact.",
        description=(
            "Freezes (copies) the store + threads sidecar, imports into a "
            "throwaway DB, exports, and deep-compares every section. "
            "READ-ONLY on the live store. Exit 0 iff ALL sections are equal; "
            "a failing rehearsal keeps its workdir as evidence.",
        ),
        examples=(
            ("{prog} db rehearse", "Rehearse against the resolved store."),
            ("{prog} db rehearse --json", "Machine-readable verdict."),
        ),
    ),
)
@click.option(
    "--tasks",
    "tasks_path",
    default=None,
    help="Store to rehearse against (default: resolved store).",
)
@click.option(
    "--workdir", default=None, help="Rehearsal dir (default: fresh temp dir)."
)
@click.option(
    "--keep", is_flag=True, help="Keep the workdir even when the rehearsal passes."
)
@click.option(
    "--json", "as_json", is_flag=True, help="Emit the verdict report as JSON."
)
def db_rehearse_cmd(tasks_path, workdir, keep, as_json):
    """Run the frozen-copy equivalence rehearsal (the R4 cutover gate)."""
    from .._db_rehearse import rehearse

    report = rehearse(tasks_path=tasks_path, workdir=workdir, keep=keep)
    if as_json:
        click.echo(json.dumps(report))
        raise SystemExit(0 if report["equal"] else 1)
    verdict = "EQUAL" if report["equal"] else "NOT EQUAL"
    click.echo(f"# db rehearse: {verdict} — {report['store']}")
    for name, ok in report["sections"].items():
        click.echo(f"  {name}: {'ok' if ok else 'MISMATCH'}")
    click.echo(
        f"  tasks={report['tasks']} users={report['users']} "
        f"inbox_recipients={report['inbox_recipients']} threads={report['threads']} "
        f"(import {report['import_s']}s / export {report['export_s']}s)"
    )
    if not report["equal"]:
        click.echo(f"  evidence kept in: {report['workdir']}")
        if report["mismatch_sample"]:
            click.echo(f"  mismatched task ids: {report['mismatch_sample']}")
    raise SystemExit(0 if report["equal"] else 1)


__all__ = ["db_rehearse_cmd", "db_show_path_cmd", "db_validate_cmd"]

# EOF
