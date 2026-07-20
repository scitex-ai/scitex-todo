#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DB-level backup and restore — recovery that never touches YAML.

WHY THIS EXISTS. Until now every recovery path in this package ran through YAML:
`db export` wrote it, `db import --from-yaml` read it back, `db snapshot` git-tracked
it. That worked — it is how the board was restored three times on 2026-07-20 — but it
is incompatible with the operator's ruling that the database is the single source of
truth and YAML is not used at all. Removing the YAML import WITHOUT this module would
leave one store and no way back from it, which is a worse failure than the one the
ruling fixes.

So: a DB is backed up to a DB, and restored from a DB. No second representation, and
nothing that a reconcile could mistake for a source.

DESIGN NOTES, each one paid for on 2026-07-20:

- `VACUUM INTO` rather than a file copy. It takes a consistent snapshot of a live
  WAL database without stopping writers; `cp` of a WAL db can capture a torn state.

- The shrink floor is evaluated BEFORE anything is replaced. The existing snapshot
  rail evaluates its floor AFTER `import_from_yaml` has already rebuilt the live DB,
  so a correct refusal still arrives too late to save the board. Here the counts are
  read from both databases first and the destination is untouched until they pass.

- Restore ALWAYS archives what it is about to replace, to `.old/<timestamp>/`. A
  restore that is itself irreversible is not a recovery tool.

- Counts are read from the ARTEFACTS, never inferred from a command succeeding.
"""

from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import click

#: A restore holding less than this FRACTION of the destination's cards is treated
#: as a catastrophe rather than a rollback, and refused. Same reasoning and same
#: number as the snapshot rail's floor: deletions are routine, HALF the board
#: vanishing is damage. `--allow-shrink` covers the genuine rollback-to-smaller case.
_SHRINK_REFUSAL_RATIO = 0.5

#: Tables a file must have to be accepted as a cards database. A restore source that
#: is merely "a sqlite file" is not good enough — restoring an unrelated database
#: over the board would be silent, total, and indistinguishable from success.
_REQUIRED_TABLES = ("tasks", "schema_meta")


def _count_tasks(db: Path) -> int | None:
    """Rows in ``tasks``, or ``None`` when the file cannot answer.

    ``None`` means "could not ask" and is NEVER collapsed into 0 — a missing file, an
    unreadable file and an empty board are three different facts, and treating the
    first two as "zero cards" is how a probe reports a catastrophe that did not
    happen (or misses one that did).
    """
    if not db.exists():
        return None
    try:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
            names = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if not set(_REQUIRED_TABLES).issubset(names):
                return None
            return int(conn.execute("SELECT count(*) FROM tasks").fetchone()[0])
    except sqlite3.Error:
        return None


def _archive(db: Path) -> Path | None:
    """Copy ``db`` under ``.old/<utc-timestamp>/`` and return where it went."""
    if not db.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest_dir = db.parent / ".old" / stamp
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / db.name
    shutil.copy2(db, dest)
    return dest


@click.command("backup")
@click.option(
    "--db", "db_path", default=None, help="Source DB (default: the resolved store DB)."
)
@click.option("--out", "out_path", required=True, help="Destination .db file to write.")
@click.option(
    "--force",
    is_flag=True,
    help="Overwrite an existing destination. Without it, an existing file is refused.",
)
def backup_cmd(db_path: str | None, out_path: str, force: bool) -> None:
    """Write a consistent DB-to-DB snapshot with sqlite ``VACUUM INTO``.

    Safe against a live board: VACUUM INTO snapshots a WAL database without
    stopping writers, unlike copying the file.
    """
    from .._db import resolve_db_path

    src = Path(resolve_db_path(db_path))
    out = Path(out_path).expanduser()

    if not src.exists():
        raise click.ClickException(
            f"REFUSING to back up {src}: it does not exist. Nothing was written. "
            f"Check the resolved DB with `scitex-cards db path`."
        )
    if out.exists() and not force:
        raise click.ClickException(
            f"REFUSING to overwrite {out}: it already exists. Pass --force to "
            f"replace it, or choose a new path. A backup that silently clobbers "
            f"an older backup destroys the history you are keeping it for."
        )
    if out.exists():
        out.unlink()
    out.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(src) as conn:
        conn.execute("VACUUM INTO ?", (str(out),))

    # Read the count back off the ARTEFACT — not from "the command did not raise".
    written = _count_tasks(out)
    if written is None:
        raise click.ClickException(
            f"BACKUP WROTE {out} BUT IT DOES NOT READ BACK AS A CARDS DATABASE. "
            f"Treat it as unusable. This is reported rather than swallowed because "
            f"a backup you cannot restore from is worse than no backup."
        )
    click.echo(
        f"# backed up DB -> DB\n  from: {src}\n  to:   {out}\n  tasks: {written}"
    )


@click.command("restore")
@click.option("--from", "src_path", required=True, help="Backup .db file to restore.")
@click.option(
    "--db",
    "db_path",
    default=None,
    help="Destination (default: the resolved store DB).",
)
@click.option(
    "--allow-shrink",
    is_flag=True,
    help=(
        "Restore even if the backup holds far fewer cards than the live board. "
        "Needed for a genuine rollback; without it a large drop is refused."
    ),
)
def restore_cmd(src_path: str, db_path: str | None, allow_shrink: bool) -> None:
    """Replace the store DB with a backup, archiving what it replaces.

    Every check runs BEFORE the destination is touched. The existing snapshot rail
    evaluates its floor after the destructive step, so its refusal arrives too late
    to save anything; that ordering is not repeated here.
    """
    from .._db import resolve_db_path

    src = Path(src_path).expanduser()
    dest = Path(resolve_db_path(db_path))

    incoming = _count_tasks(src)
    if incoming is None:
        raise click.ClickException(
            f"REFUSING to restore from {src}: it is missing, unreadable, or not a "
            f"cards database (needs tables {', '.join(_REQUIRED_TABLES)}). Nothing "
            f"was changed. Restoring an unrelated sqlite file over the board would "
            f"be silent and total."
        )

    current = _count_tasks(dest)
    if (
        not allow_shrink
        and current is not None
        and current > 0
        and incoming < current * _SHRINK_REFUSAL_RATIO
    ):
        raise click.ClickException(
            f"REFUSING to restore: the backup holds {incoming} cards but the live "
            f"board holds {current} ({incoming * 100 // current}%). Nothing was "
            f"changed.\n"
            f"If this rollback is intended, re-run with --allow-shrink. If it is "
            f"not, you are about to overwrite a healthy board with a stale or "
            f"damaged backup — check `scitex-cards db path` first."
        )

    archived = _archive(dest)
    tmp = dest.with_suffix(dest.suffix + ".restoring")
    with sqlite3.connect(src) as conn:
        conn.execute("VACUUM INTO ?", (str(tmp),))
    tmp.replace(dest)

    # Verify from the artefact, not from the absence of an exception.
    final = _count_tasks(dest)
    click.echo(
        f"# restored DB <- DB\n"
        f"  from:     {src} ({incoming} tasks)\n"
        f"  to:       {dest} ({final} tasks)\n"
        f"  replaced: {current if current is not None else 'nothing'} tasks\n"
        f"  archived: {archived or 'nothing to archive'}"
    )
    if final != incoming:
        raise click.ClickException(
            f"RESTORE VERIFICATION FAILED: expected {incoming} tasks, the restored "
            f"database reads {final}. The previous database is at {archived}."
        )


def register(db_group: click.Group) -> None:
    """Attach the recovery verbs to the ``db`` noun group."""
    db_group.add_command(backup_cmd)
    db_group.add_command(restore_cmd)
