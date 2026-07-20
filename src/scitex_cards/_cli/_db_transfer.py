#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-cards db`` verbs that MOVE data: ``import`` / ``export`` /
``create-snapshot``.

Split out of ``_cli/_db.py`` (512-line cap) along the seam the group's own
help text already drew: read-only verbs live in
:mod:`scitex_cards._cli._db_inspect`, and everything that writes a file lives
here — together with the snapshot shrink-refusal guard, whose whole reason to
exist is that this is the half that can record a catastrophe.
"""

from __future__ import annotations

import json
import re

import click

from ._compat import spec_command_kwargs
from ._db_options import DB_OPTION, DRY_RUN_OPTION, YES_OPTION

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


@click.command(
    "import",
    **spec_command_kwargs(
        summary="Bootstrap the shadow DB from the canonical YAML store.",
        description=(
            "Reads tasks.yaml (tasks + users + inboxes) and the threads.yaml "
            "sidecar and rebuilds every DB table in one transaction. "
            "Idempotent (re-run = same state). The YAML is opened READ-ONLY "
            "and never modified. Requires --from-yaml (the only S0 source).",
        ),
        examples=(
            ("{prog} db import --from-yaml", "Rebuild from the resolved store."),
            (
                "{prog} db import --from-yaml --dry-run",
                "Name the source, write nothing.",
            ),
        ),
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
    help="Path to tasks.yaml (default: user store / $SCITEX_CARDS_TASKS_YAML_SHARED).",
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
@DB_OPTION
@DRY_RUN_OPTION
@YES_OPTION
@click.option(
    "--json", "as_json", is_flag=True, help="Emit the import summary as JSON."
)
def db_import_cmd(
    from_yaml: bool,
    tasks_path: str | None,
    as_store: str | None,
    db_path: str | None,
    dry_run: bool,
    yes: bool,
    as_json: bool,
) -> None:
    """(Re)bootstrap the shadow DB from the YAML store."""
    _ = yes  # accepted for §2 compliance; this verb never prompts
    if not from_yaml:
        raise click.ClickException(
            "`db import` requires --from-yaml (the only source in S0)."
        )
    from .._db import resolve_db_path
    from .._db_bootstrap import import_from_yaml
    from .._paths import resolve_tasks_path

    if dry_run:
        # Name BOTH ends. "would import" without saying from where and to
        # where is not a dry run, it is a reassurance.
        click.echo(
            f"# dry-run: would rebuild every table of "
            f"{resolve_db_path(db_path)} from {resolve_tasks_path(tasks_path)} "
            f"(the YAML is opened read-only either way)"
        )
        return

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


@click.command(
    "export",
    **spec_command_kwargs(
        summary="Export the DB to YAML text (ADR-0010 backup/audit rail).",
        description=(
            "Every record is reconstructed from its VERBATIM json payload "
            "(card_json / record_json) — never from typed columns — so the "
            "export is exact by construction. REFUSES loudly if any row has "
            "no payload (a pre-v3 DB: re-run `db import --from-yaml` first).",
        ),
        examples=(
            ("{prog} db export", "Export beside the DB."),
            ("{prog} db export --out /tmp/tasks.yaml --json", "Pick the path."),
        ),
    ),
)
@DB_OPTION
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
@DRY_RUN_OPTION
@YES_OPTION
@click.option("--json", "as_json", is_flag=True, help="Emit the export report as JSON.")
def db_export_cmd(
    db_path: str | None,
    out_path: str | None,
    threads_out: str | None,
    dry_run: bool,
    yes: bool,
    as_json: bool,
) -> None:
    """Export the DB to YAML snapshot files."""
    _ = yes  # accepted for §2 compliance; this verb never prompts
    from .._db import resolve_db_path

    if dry_run:
        click.echo(
            f"# dry-run: would export {resolve_db_path(db_path)} to "
            f"{out_path or '<db_dir>/export/tasks.yaml'} "
            f"(+ the threads sidecar); the DB is read-only either way"
        )
        return

    from .._db_export import export_yaml

    report = export_yaml(db_path=db_path, out=out_path, threads_out=threads_out)
    if as_json:
        click.echo(json.dumps(report))
        return
    _echo_export_report(report)


@click.command(
    "create-snapshot",
    **spec_command_kwargs(
        summary="Export the DB to the snapshot dir and git-commit the export.",
        description=(
            "The ADR-0010 backup rail: git tracks an EXPORT, never live data, "
            "so no git operation can ever roll back the live store. "
            "Initialises the snapshot dir as its own git repo on first run.",
        ),
        examples=(
            ("{prog} db create-snapshot", "Snapshot to the default dir."),
            ("{prog} db create-snapshot --push", "Also push it off-site."),
        ),
    ),
)
@DB_OPTION
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
@DRY_RUN_OPTION
@YES_OPTION
@click.option(
    "--json", "as_json", is_flag=True, help="Emit the snapshot report as JSON."
)
def db_create_snapshot_cmd(  # noqa: PLR0913 — one flag per rail behaviour
    db_path: str | None,
    snap_dir: str | None,
    refresh: bool,
    push: bool,
    allow_shrink: bool,
    dry_run: bool,
    yes: bool,
    as_json: bool,
) -> None:
    """Export to the snapshot dir and commit the export in its own git repo."""
    import subprocess
    from pathlib import Path

    from .._db import resolve_db_path
    from .._db_export import export_yaml

    _ = yes  # accepted for §2 compliance; this verb never prompts

    root = (
        Path(snap_dir).expanduser()
        if snap_dir
        else resolve_db_path(db_path).parent / "snapshots"
    )

    if dry_run:
        click.echo(
            f"# dry-run: would export {resolve_db_path(db_path)} into {root} "
            f"and commit it there"
            + (" (after refreshing the DB from the YAML)" if refresh else "")
            + (" then push" if push else "")
        )
        return

    if refresh:
        from .._db_bootstrap import import_from_yaml

        summary = import_from_yaml(db_path=db_path)
        if not as_json:
            click.echo(
                f"# refreshed DB from YAML: {summary['yaml_path']} -> "
                f"{summary['db_path']} ({summary['tasks']} tasks)"
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


__all__ = ["db_create_snapshot_cmd", "db_export_cmd", "db_import_cmd"]

# EOF
