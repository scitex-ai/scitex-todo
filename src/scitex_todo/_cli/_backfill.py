#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""`scitex-todo backfill-merged-prs` — fallback DONE bookkeeping.

Lead a2a ``ba90ba35``, 2026-06-15 (task #9 PR2). Closes the 0%
bookkeeping rate gap independently from dev's ywatanabe1989-throttled
emitter. Runs on the non-throttled scitex-ai host as a scheduled
cron sweep (or one-shot operator-invoked recovery).

This is the CLI verb wrapper around
:func:`scitex_todo._pr_merged_backfill.backfill_merged_prs`.

## Examples

  scitex-todo backfill-merged-prs --once
      Single sweep over the configured repos with the default 7-day
      window. Exits 0 if no errors, 1 if any per-PR error or rate-limit.

  scitex-todo backfill-merged-prs --since-days 30 --once
      One-shot historical import (operator-driven recovery).

  scitex-todo backfill-merged-prs --once --json
      Same sweep, but emit the summary as JSON on stdout for piping
      into shell tooling. Stderr keeps the human-readable log.

## Repo list

Sources the same ``fleet.ci_status.repos`` list the existing
``ci-watch`` poller uses (``~/.scitex/todo/dashboard.yaml``), or the
``SCITEX_TODO_FLEET_CI_REPOS=owner/a,owner/b`` env override. Lead-
locked policy: stay aligned with the ci-watch repo set so the two
pollers cover the same fleet.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Iterable

import click


def register(main: click.Group) -> None:
    """Attach the ``backfill-merged-prs`` verb to the root CLI."""
    main.add_command(backfill_merged_prs_cmd)


def _resolve_repos() -> list[str]:
    """Mirror :func:`scitex_todo._cli._ci_watch._run_one_sweep` repo loading."""
    env = os.environ.get("SCITEX_TODO_FLEET_CI_REPOS")
    if env:
        return [r.strip() for r in env.split(",") if r.strip() and "/" in r]
    try:
        from .._django.handlers.fleet._config import fleet_config_load
    except ImportError:
        return []
    try:
        cfg = fleet_config_load()
    except Exception:  # noqa: BLE001
        return []
    repos = ((cfg.get("fleet") or {}).get("ci_status") or {}).get("repos") or []
    return [r for r in repos if isinstance(r, str) and "/" in r]


@click.command(
    "backfill-merged-prs",
    help=(
        "Reconcile the _hooks_processed.py ledger against merged PRs on "
        "GitHub. Marks any merged PR not yet in the ledger as DONE on "
        "the matching card(s) via the existing _handle_done path, then "
        "records the ledger entry with source='poll'.\n\n"
        "Lead-locked policy: ANY merged PR -> DONE (CI green/red is a "
        "separate pill surface, not the bookkeeping gate).\n\n"
        "Designed for cron use: --once runs ONE sweep + exits; absence "
        "of --once loops with --interval (default 600s)."
    ),
)
@click.option(
    "--once",
    is_flag=True,
    help="Run ONE sweep then exit (cron mode). Skip the loop.",
)
@click.option(
    "--interval",
    type=int,
    default=600,
    show_default=True,
    help="Loop interval in seconds. Ignored when --once is set.",
)
@click.option(
    "--since-days",
    type=int,
    default=7,
    show_default=True,
    help=(
        "Lookback window for closed-PR enumeration. Operators can "
        "extend for one-shot historical imports."
    ),
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the per-sweep summary as JSON on stdout.",
)
def backfill_merged_prs_cmd(
    once: bool, interval: int, since_days: int, as_json: bool
) -> None:
    """Poll merged PRs + reconcile against the dedup ledger."""
    import time

    while True:
        summary = _one_sweep(since_days=since_days, as_json=as_json)
        if once:
            sys.exit(0 if summary["errors"] == 0 and not summary["rate_limited"] else 1)
        time.sleep(max(int(interval), 60))


def _one_sweep(*, since_days: int, as_json: bool) -> dict:
    """One reconciliation pass. Returns the summary dict."""
    from .._pr_merged_backfill import backfill_merged_prs

    repos = _resolve_repos()
    if not repos:
        click.echo(
            "# backfill-merged-prs: no repos configured "
            "(SCITEX_TODO_FLEET_CI_REPOS env or "
            "~/.scitex/todo/dashboard.yaml fleet.ci_status.repos)",
            err=True,
        )
        empty = {
            "scanned": 0,
            "already_processed": 0,
            "newly_processed": 0,
            "cards_marked": 0,
            "no_card_match": 0,
            "errors": 0,
            "rate_limited": False,
        }
        if as_json:
            click.echo(json.dumps(empty, indent=2))
        return empty

    summary = backfill_merged_prs(
        repos=repos, since_days=since_days,
    )
    # Stderr — human-readable. Stdout — machine-readable if requested.
    click.echo(
        f"# backfill-merged-prs: repos={len(repos)} "
        f"scanned={summary['scanned']} "
        f"already_processed={summary['already_processed']} "
        f"newly_processed={summary['newly_processed']} "
        f"cards_marked={summary['cards_marked']} "
        f"no_card_match={summary['no_card_match']} "
        f"errors={summary['errors']} "
        f"rate_limited={summary['rate_limited']}",
        err=True,
    )
    if as_json:
        click.echo(json.dumps(dict(summary), indent=2))
    return summary
