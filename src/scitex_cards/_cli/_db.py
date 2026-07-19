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
import re

import click

#: A snapshot holding less than this FRACTION of the previous one's cards is
#: treated as a catastrophe rather than churn, and refused. Cards are deleted
#: routinely; HALF of them vanishing between two hourly fires is not deletion,
#: it is damage. Deliberately generous — the goal is to catch a wipe, not to
#: police normal cleanup, and `--allow-shrink` covers the real bulk-delete case.
_SHRINK_REFUSAL_RATIO = 0.5

#: The rail's own commit subject, e.g. ``snapshot: 2138 tasks``. Parsed back to
#: recover the previous count, so the check needs no state of its own — the
#: history IS the record.
_SNAPSHOT_SUBJECT_RE = re.compile(r"snapshot:\s*(\d+)\s+tasks")


def _previous_snapshot_count(git) -> int | None:
    """Cards recorded by the most recent snapshot commit, or ``None``.

    ``None`` means "no basis to compare" — a fresh repo, an unreadable log, or
    a subject line that does not parse. Every one of those is a reason to allow
    the snapshot, not to block it: a backup rail must never refuse because its
    own bookkeeping is unfamiliar.
    """
    log = git("log", "-1", "--format=%s")
    if log.returncode != 0:
        return None
    match = _SNAPSHOT_SUBJECT_RE.search(log.stdout or "")
    return int(match.group(1)) if match else None


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
    help="Explicit DB path (default: $SCITEX_CARDS_DB, else ~/.scitex/cards/cards.db).",
)


@db_group.command(
    "path",
    help=(
        "Print the resolved DB path.\n\n"
        "Precedence: --db arg > $SCITEX_CARDS_DB > $SCITEX_TODO_DB "
        "(deprecated, warned) > local_state.user_path('cards','cards.db'). "
        "Delegates the user tier to the ecosystem resolver (never a "
        "re-rolled project/user precedence).\n\n"
        "Example:\n"
        "  scitex-cards db path"
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
@click.option(
    "--as-store",
    "as_store",
    default=None,
    help=(
        "Stamp the DB as the store for THIS path instead of the imported file. "
        "Use when restoring from a backup/snapshot: the source file is where the "
        "DATA came from, not what the DB IS."
    ),
)
@_DB_OPTION
@click.option(
    "--json", "as_json", is_flag=True, help="Emit the import summary as JSON."
)
def db_import_cmd(
    from_yaml: bool,
    tasks_path: str | None,
    as_store: str | None,
    db_path: str | None,
    as_json: bool,
) -> None:
    """(Re)bootstrap the shadow DB from the YAML store."""
    if not from_yaml:
        raise click.ClickException(
            "`db import` requires --from-yaml (the only source in S0)."
        )
    from .._db_bootstrap import import_from_yaml

    summary = import_from_yaml(
        tasks_path=tasks_path, db_path=db_path, as_store=as_store
    )
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


def _echo_export_report(report: dict) -> None:
    """Print an export's counts — a silent bulk export leaves no audit trace."""
    click.echo(
        f"# exported DB -> YAML\n"
        f"  db:      {report['db']}\n"
        f"  tasks:   {report['tasks_yaml']}  ({report['tasks']} tasks, "
        f"{report['users']} users, {report['notifications']} notifications)\n"
        f"  threads: {report['threads_yaml']}  ({report['threads']} threads, "
        f"{report['messages']} messages)"
    )


@db_group.command(
    "export",
    help=(
        "Export the DB to YAML text (ADR-0010 backup/audit rail).\n\n"
        "Every record is reconstructed from its VERBATIM json payload "
        "(card_json / record_json) — never from typed columns — so the "
        "export is exact by construction. REFUSES loudly if any row has no "
        "payload (a pre-v3 DB: re-run `db import --from-yaml` first).\n\n"
        "Example:\n"
        "  scitex-cards db export\n"
        "  scitex-cards db export --out /tmp/tasks.yaml --json"
    ),
)
@_DB_OPTION
@click.option(
    "--out",
    "out_path",
    default=None,
    help="tasks.yaml output path (default: <db_dir>/export/tasks.yaml).",
)
@click.option(
    "--threads-out",
    "threads_out",
    default=None,
    help="threads.yaml output path (default: beside --out).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit the export report as JSON.")
def db_export_cmd(
    db_path: str | None,
    out_path: str | None,
    threads_out: str | None,
    as_json: bool,
) -> None:
    """Export the DB to YAML snapshot files."""
    from .._db_export import export_yaml

    report = export_yaml(db_path=db_path, out=out_path, threads_out=threads_out)
    if as_json:
        click.echo(json.dumps(report))
        return
    _echo_export_report(report)


@db_group.command(
    "snapshot",
    help=(
        "Export the DB to the snapshot dir and git-commit the export.\n\n"
        "The ADR-0010 backup rail: git tracks an EXPORT, never live data, so "
        "no git operation can ever roll back the live store. Initialises the "
        "snapshot dir as its own git repo on first run.\n\n"
        "Example:\n"
        "  scitex-cards db snapshot\n"
        "  scitex-cards db snapshot --dir ~/.scitex/cards/snapshots"
    ),
)
@_DB_OPTION
@click.option(
    "--dir",
    "snap_dir",
    default=None,
    help="Snapshot directory (default: <db_dir>/snapshots; its own git repo).",
)
@click.option(
    "--refresh",
    is_flag=True,
    help=(
        "Rebuild the DB from the canonical YAML first (import), then "
        "snapshot. The honest pre-cutover cadence: import IS the freshness "
        "step while the yaml is still canonical; after the flip, drop it."
    ),
)
@click.option(
    "--push",
    is_flag=True,
    help=(
        "Push the snapshot repo to its remote after committing. No remote "
        "configured = reported local-only (exit 0); a FAILED push exits 1 — "
        "the rail's job is the off-site copy, so a silent local-only "
        "success would be a lie."
    ),
)
@click.option(
    "--allow-shrink",
    is_flag=True,
    help=(
        "Snapshot even if the card count collapsed vs the previous snapshot. "
        "Needed for a genuine bulk delete or a deliberately fresh store; "
        "WITHOUT it a large drop is refused, because a backup that silently "
        "records a wipe buys confidence in a destroyed board."
    ),
)
@click.option(
    "--json", "as_json", is_flag=True, help="Emit the snapshot report as JSON."
)
def db_snapshot_cmd(
    db_path: str | None,
    snap_dir: str | None,
    refresh: bool,
    push: bool,
    allow_shrink: bool,
    as_json: bool,
) -> None:
    """Export to the snapshot dir and commit the export in its own git repo."""
    import subprocess
    from pathlib import Path

    from .._db import resolve_db_path
    from .._db_export import export_yaml

    if refresh:
        from .._db_bootstrap import import_from_yaml

        summary = import_from_yaml(db_path=db_path)
        if not as_json:
            click.echo(
                f"# refreshed DB from YAML: {summary['yaml_path']} -> "
                f"{summary['db_path']} ({summary['tasks']} tasks)"
            )

    root = (
        Path(snap_dir).expanduser()
        if snap_dir
        else resolve_db_path(db_path).parent / "snapshots"
    )
    root.mkdir(parents=True, exist_ok=True)

    report = export_yaml(
        db_path=db_path,
        out=root / "tasks.yaml",
        threads_out=root / "threads.yaml",
    )

    def _git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            check=False,
        )

    if not (root / ".git").exists():
        _git("init", "-q")
        _git("config", "user.name", "scitex-cards")
        _git("config", "user.email", "cards@scitex.ai")
    # A BACKUP MUST NOT RECORD A CATASTROPHE WITHOUT SAYING SO.
    #
    # On 2026-07-19 the live DB was destroyed (2,138 cards -> 53) and the rail
    # did exactly what it was told: it snapshotted the wreck and committed
    # "snapshot: 53 tasks" as HEAD, one commit after "snapshot: 2138 tasks",
    # silently. The rail was WORKING — that is the point. A backup that
    # faithfully records a wipe with no alarm stops being a safety net and
    # becomes a propagation mechanism: anyone restoring from HEAD afterwards
    # gets the destroyed board, and retention eventually ages out the good one.
    #
    # Git history saved the recovery that day. History is not a plan.
    previous = _previous_snapshot_count(_git)
    now = int(report.get("tasks") or 0)
    if (
        not allow_shrink
        and previous is not None
        and previous > 0
        and now < previous * _SHRINK_REFUSAL_RATIO
    ):
        raise click.ClickException(
            f"REFUSING to snapshot: the card count collapsed from {previous} to "
            f"{now} ({now * 100 // previous}% of the previous snapshot). A backup "
            f"that records a wipe without comment is worse than no backup — it "
            f"buys confidence in a destroyed board.\n"
            f"If the store really did shrink this much (a bulk delete, a fresh "
            f"store), re-run with --allow-shrink. If it did NOT, the live store "
            f"is damaged: recover it BEFORE snapshotting, or this commit becomes "
            f"the newest 'good' state."
        )

    _git("add", "-A")
    committed = _git("commit", "-q", "-m", f"snapshot: {report['tasks']} tasks")
    # exit 1 with nothing staged = no changes since the last snapshot — a
    # legitimate outcome, reported as such rather than swallowed.
    report["committed"] = committed.returncode == 0
    report["snapshot_dir"] = str(root)

    if push:
        has_remote = bool(_git("remote").stdout.strip())
        if not has_remote:
            # Local-only mode is legitimate BEFORE a remote is wired; the
            # report says so instead of pretending an off-site copy exists.
            report["pushed"] = False
            report["push_detail"] = "no remote configured — snapshot is local-only"
        else:
            # -u origin HEAD: works on the FIRST push to a freshly-wired
            # remote (no upstream yet) and every push after.
            pushed = _git("push", "-q", "-u", "origin", "HEAD")
            report["pushed"] = pushed.returncode == 0
            report["push_detail"] = (pushed.stderr or pushed.stdout).strip()
            if not report["pushed"]:
                # A failed push means the backup did NOT go off-site. That is
                # the rail's whole job — fail LOUD so the cron tick reads red.
                _emit = (
                    json.dumps(report)
                    if as_json
                    else (
                        f"::error:: snapshot committed LOCALLY but push FAILED: "
                        f"{report['push_detail']}"
                    )
                )
                click.echo(_emit)
                raise SystemExit(1)

    if as_json:
        click.echo(json.dumps(report))
        return
    _echo_export_report(report)
    state = "committed" if report["committed"] else "no changes since last snapshot"
    click.echo(f"  snapshot: {root} ({state})")


@db_group.command(
    "rehearse",
    help=(
        "Cutover rehearsal: prove yaml -> cards.db -> yaml is exact.\n\n"
        "Freezes (copies) the store + threads sidecar, imports into a "
        "throwaway DB, exports, and deep-compares every section. "
        "READ-ONLY on the live store. Exit 0 iff ALL sections equal; "
        "a failing rehearsal keeps its workdir as evidence.\n\n"
        "Example:\n"
        "  scitex-cards db rehearse\n"
        "  scitex-cards db rehearse --json"
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


# EOF
