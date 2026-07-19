#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`scitex-todo reconcile-merged-prs` — auto-close cards whose linked PR merged.

Terminal twin of the periodic JobSpec (``_jobs_provider.py``). Scans open
cards (pending / in_progress / blocked) that carry a ``pr_url``, asks GitHub
whether the PR is merged, and — for the merged ones — flips the card to
``done`` plus an audit comment. DRY-RUN by default (report only); ``--apply``
performs the mutation (mirrors scitex-dev's board-mutation pattern — no
silent auto-close).

The merge-state check + the pure decision logic live in
:mod:`scitex_cards._reconcile_prs`; this module is thin CLI plumbing.
"""

from __future__ import annotations

import json

import click

from .._paths import resolve_tasks_path
from .._reconcile_prs import reconcile_merged_prs
from ._compat import deprecated_alias, spec_command_kwargs

#: Version that removes the Phase-W ``reconcile-merged-prs`` alias (§5).
_REMOVE_IN = "0.20.0"


@click.command(
    "sync-merged-prs",
    **spec_command_kwargs(
        summary="Close cards whose linked PR (pr_url) has MERGED.",
        description=(
            "Scans pending / in_progress / blocked cards with a pr_url; for "
            "each, checks the PR merge-state (gh, then a curl GitHub REST "
            "fallback) and — when merged — flips the card to `done` with an "
            "audit comment. DRY-RUN by default; pass --apply to mutate.",
        ),
        examples=(
            ("{prog} sync-merged-prs", "Dry-run report."),
            ("{prog} sync-merged-prs --apply --yes", "Actually close them."),
        ),
    ),
)
@click.option(
    "--apply",
    "apply",
    is_flag=True,
    help=(
        "Actually flip merged-PR cards to `done` + comment. Without this "
        "flag the verb is DRY-RUN (report only)."
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Force the report-only pass (the default; explicit for §2 parity).",
)
@click.option(
    "-y",
    "--yes",
    is_flag=True,
    help="Skip confirmation (no-op today — the pass is non-interactive).",
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
    help="Emit the summary as JSON (machine-readable).",
)
def sync_merged_prs_cmd(
    apply: bool, dry_run: bool, yes: bool, tasks_path, as_json: bool
) -> None:
    """Run the sync pass and print the summary (dry-run by default)."""
    _ = yes  # accepted for §2 compliance; the pass is non-interactive
    if dry_run:
        # An explicit --dry-run always wins over --apply: when a caller says
        # both, the safe reading is the one that does not mutate the board.
        apply = False
    resolved = resolve_tasks_path(tasks_path)
    result = reconcile_merged_prs(resolved, apply=apply)

    if as_json:
        click.echo(json.dumps(result.to_dict(), indent=2))
        return

    if apply:
        click.echo(
            f"# sync-merged-prs (APPLIED): closed {len(result.closed)} "
            f"card(s) whose PR merged"
        )
        for c in result.closed:
            click.echo(f"  closed {c['id']:55} | {c['pr_url']}")
    else:
        click.echo(
            f"# sync-merged-prs (DRY-RUN): {len(result.would_close)} "
            f"card(s) would close (pass --apply to mutate)"
        )
        for c in result.would_close:
            click.echo(f"  would-close {c['id']:50} | {c['pr_url']}")

    if result.skipped:
        summary = ", ".join(f"{k}={v}" for k, v in sorted(result.skipped.items()))
        click.echo(f"# skipped: {summary}")


def register(main: click.Group) -> None:
    """Attach `sync-merged-prs` (+ the Phase-W `reconcile-merged-prs` alias).

    `reconcile` is not a canonical verb (doctrine §1f maps it to
    `sync-<object>`), and the object was already in the old name — so the
    rename is purely the verb token.
    """
    main.add_command(sync_merged_prs_cmd, name="sync-merged-prs")
    deprecated_alias(
        main,
        "reconcile-merged-prs",
        target="sync-merged-prs",
        remove_in=_REMOVE_IN,
    )


# EOF
