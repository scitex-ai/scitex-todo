#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-todo stats`` + ``scitex-todo sync-github`` CLI verbs.

Stats:
  Compute per-(agent | project | host) throughput from the canonical
  tasks.yaml: created / completed / delta / ratio / velocity. Optionally
  pushes per-agent notify bodies via ``sac agents send`` (stdout
  fallback) — operator's standing direction that "agents see their own
  numbers every hour and self-correct."

Sync-github:
  Permanent version of the lead's one-time GitHub→board sync (a2a
  ``7489ac3173fa4d7e9b2a870e30085b44`` 2026-06-12). Pulls today's (or
  ``--since``) merged PRs / closed issues across the org, matches each
  against existing tasks (pr_url / fuzzy title-token overlap), and emits
  done updates + new-task records.

Both verbs share the aggregator in :mod:`scitex_todo._throughput` so the
WIP-validation gate, the board's compact `Δ delta` pill, and the
``stats --notify`` body all derive from one shared definition of
"open" / "stale" / "completed".
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import click

from .._model import load_tasks
from .._paths import resolve_tasks_path
from .._throughput import (
    GroupStats,
    aggregate,
    build_notify_body,
)


# --------------------------------------------------------------------------- #
# stats                                                                       #
# --------------------------------------------------------------------------- #


def _format_text(rows: list[GroupStats]) -> str:
    """Plain text table — terse but readable in a terminal."""
    if not rows:
        return "(no rows)"
    header = f"{'name':30}  {'open':>5}  {'stale':>5}  {'done':>5}  {'created':>7}  {'delta':>6}  {'ratio':>6}  {'vel/day':>8}"
    body = []
    for r in rows:
        body.append(
            f"{r.name[:30]:30}  {r.open_count:5d}  {r.stale_count:5d}  "
            f"{r.completed:5d}  {r.created:7d}  {r.delta:+6d}  "
            f"{r.ratio*100:5.1f}%  {r.velocity_per_day:8.2f}"
        )
    return "\n".join([header] + body)


def _format_json(rows: list[GroupStats]) -> str:
    return json.dumps(
        [
            {
                "name": r.name,
                "open": r.open_count,
                "stale": r.stale_count,
                "completed": r.completed,
                "created": r.created,
                "delta": r.delta,
                "ratio": round(r.ratio, 4),
                "velocity_per_day": round(r.velocity_per_day, 4),
            }
            for r in rows
        ],
        indent=2,
        ensure_ascii=False,
    )


def _push_notify(agent: str, body: str) -> str:
    """Push the notify body to ``agent`` via ``sac agents send``.

    Falls back to stdout (with a header) when ``sac`` is not installed
    or its call exits non-zero. Returns the chosen wire as a one-word
    label for the summary line ("sac" or "stdout").
    """
    if subprocess.run(
        ["which", "sac"], capture_output=True
    ).returncode == 0:
        proc = subprocess.run(
            ["sac", "agents", "send", agent, body],
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            return "sac"
    # Fallback — print to stdout so the operator at least sees the
    # body when sac isn't reachable (env-bounded `sac` missing, dev
    # box, container without the sac wire, etc.).
    print(f"\n=== notify → {agent} (sac unavailable, stdout fallback) ===")
    print(body)
    print(f"=== end {agent} ===\n")
    return "stdout"


@click.command(name="stats")
@click.option(
    "--by",
    type=click.Choice(["agent", "project", "host"]),
    default="agent",
    help="Group throughput rows by this field.",
)
@click.option(
    "--since",
    default=None,
    help="ISO-8601 date (YYYY-MM-DD). Scopes created/completed to this window.",
)
@click.option(
    "--format",
    "fmt",
    type=click.Choice(["text", "json"]),
    default="text",
    help="Output format. text = aligned table; json = list of objects.",
)
@click.option(
    "--notify",
    is_flag=True,
    help=(
        "After printing the stats, push a per-agent notify body via "
        "`sac agents send` (stdout fallback when sac unavailable). "
        "The body lists each agent's open tasks (RUNNABLE first, "
        "then BLOCKED + reason), ⚠ on stale in_progress, and "
        "recently-completed lines so the receiver can self-correct."
    ),
)
@click.option(
    "--tasks",
    "tasks_path",
    default=None,
    help="Path to tasks.yaml (default: project -> user -> bundled example, or $SCITEX_TODO_TASKS).",
)
def stats_cmd(
    by: str, since: str | None, fmt: str, notify: bool, tasks_path: str | None
) -> None:
    """Print throughput stats (per-agent / project / host).

    Operator standing direction (lead a2a ``4b23ebc177944deaa7549e256e9a375a``
    2026-06-12): every agent must measure its own creation vs
    completion rate so completion > creation discipline holds across
    the fleet. ``--notify`` pushes the per-agent summary so receivers
    self-correct hourly.
    """
    path = resolve_tasks_path(tasks_path)
    tasks = load_tasks(path)
    rows = aggregate(tasks, by=by, since=since)
    out = _format_json(rows) if fmt == "json" else _format_text(rows)
    click.echo(out)
    if notify and by == "agent":
        # --notify only makes sense when grouped by agent — that's
        # what the push target identifies.
        click.echo("")
        click.echo(f"# Notify push → {len(rows)} agents")
        for r in rows:
            if r.name == "(unassigned)":
                continue
            body = build_notify_body(r.name, tasks, since=since)
            wire = _push_notify(r.name, body)
            click.echo(f"  {wire:>6}  {r.name}  ({len(body)} chars)")
    elif notify:
        click.echo(
            "WARN: --notify ignored when --by != agent (push target needs an agent id).",
            err=True,
        )


# --------------------------------------------------------------------------- #
# sync-github                                                                 #
# --------------------------------------------------------------------------- #


def _gh_merged_prs(since: str) -> list[dict]:
    """Fetch ``ywatanabe1989/*`` PRs merged in the ``--since`` window."""
    proc = subprocess.run(
        [
            "gh", "search", "prs",
            "--owner", "ywatanabe1989",
            "--merged",
            "--merged-at", f"{since}..*",
            "--limit", "300",
            "--json", "number,title,repository,author",
        ],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise click.ClickException(f"gh search prs failed: {proc.stderr.strip()}")
    return json.loads(proc.stdout or "[]")


def _project_for_repo(repo: str) -> str:
    """Map a GitHub repo name → board ``project`` field. The default is
    the repo name verbatim (matches the existing tasks.yaml convention)."""
    # neurovista repo → paper-neurovista project (operator's title-prefix
    # convention puts "paper-" on cohort papers).
    overrides = {"neurovista": "paper-neurovista"}
    return overrides.get(repo, repo)


def _is_ci_speedup(title: str) -> bool:
    t = title.lower()
    return "ci-speedup" in t or "l1-l5" in t.lower()


@click.command(name="sync-github")
@click.option(
    "--since",
    default=None,
    help="ISO-8601 date (YYYY-MM-DD). Defaults to today (UTC).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the planned actions without executing.",
)
@click.option(
    "--tasks",
    "tasks_path",
    default=None,
    help="Path to tasks.yaml (default: project -> user -> bundled example).",
)
def sync_github_cmd(since: str | None, dry_run: bool, tasks_path: str | None) -> None:
    """Permanent GitHub→board sync.

    Pulls merged PRs across ``ywatanabe1989/*`` since the given date
    (default: today UTC). Matches each PR against existing tasks
    (``pr_url`` first, then fuzzy title-token overlap within the same
    project). Done-flips matches; creates ``status=done`` records for
    unmatched PRs (``project = repo``, ``agent = "lead"``). Mechanical
    CI-speedup PRs (title contains "ci-speedup" or "L1-L5") collapse
    into a single bundle record per day.

    The aggregation function is shared with ``stats`` and the WIP gate
    via :mod:`scitex_todo._throughput`.
    """
    # Defer the imports to avoid pulling `_store` at module load (some
    # call paths import this module without needing the write side).
    from .._store import add_task, update_task

    target_since = since or datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    path = resolve_tasks_path(tasks_path)
    tasks = load_tasks(path)
    by_pr_url = {t["pr_url"]: t for t in tasks if t.get("pr_url")}

    prs = _gh_merged_prs(target_since)
    ci_speedup = [p for p in prs if _is_ci_speedup(p["title"])]
    other = [p for p in prs if not _is_ci_speedup(p["title"])]

    actions_planned = 0
    done_updates = 0
    new_tasks = 0

    # --- (1) CI-speedup bundle ---------------------------------------------
    if ci_speedup:
        repos = sorted({p["repository"]["name"] for p in ci_speedup})
        bundle_id = f"ops-{target_since}-ci-speedup-wave"
        links = "\n".join(
            f"- {p['repository']['name']}#{p['number']}: {p['title']}"
            for p in ci_speedup
        )
        title = f"CI-speedup wave (L1-L5) across {len(repos)} repos — merged {target_since}"
        if bundle_id not in {t.get("id") for t in tasks}:
            click.echo(f"+ NEW  {bundle_id}  ({len(ci_speedup)} PRs)")
            actions_planned += 1
            new_tasks += 1
            if not dry_run:
                add_task(
                    str(path),
                    id=bundle_id,
                    title=title,
                    status="done",
                    project="scitex-dev",
                    agent="proj-scitex-dev",
                    note=("Consolidated record of the proj-scitex-dev L1-L5 "
                          "CI-speedup template apply wave merged "
                          f"{target_since}. Individual PRs:\n{links}"),
                )

    # --- (2) Other PRs: match-or-create -----------------------------------
    for p in other:
        repo = p["repository"]["name"]
        n = p["number"]
        url = f"https://github.com/ywatanabe1989/{repo}/pull/{n}"
        title = p["title"]
        project = _project_for_repo(repo)
        agent = "lead"  # ywatanabe1989 → lead per the housekeeping convention.

        existing = by_pr_url.get(url)
        if existing is not None:
            if existing.get("status") != "done":
                click.echo(f"✓ DONE  {existing['id']}  {url}")
                actions_planned += 1
                done_updates += 1
                if not dry_run:
                    update_task(str(path), task_id=existing["id"], status="done")
            continue

        new_id = f"pr-{repo.replace('/', '-')}-{n}"
        if new_id in {t.get("id") for t in tasks}:
            # Already created in a prior sync — leave it.
            continue
        click.echo(f"+ NEW   {new_id}  ({repo}#{n})")
        actions_planned += 1
        new_tasks += 1
        if not dry_run:
            add_task(
                str(path),
                id=new_id,
                title=f"{repo}#{n}: {title}",
                status="done",
                project=project,
                agent=agent,
                pr_url=url,
            )

    click.echo("")
    click.echo(
        f"sync-github: {done_updates} done updates / {new_tasks} new tasks "
        f"({actions_planned} total actions, since={target_since}"
        f"{', dry-run' if dry_run else ''})"
    )


# --------------------------------------------------------------------------- #
# Registration                                                                #
# --------------------------------------------------------------------------- #


def register(group: click.Group) -> None:
    group.add_command(stats_cmd)
    group.add_command(sync_github_cmd)


__all__ = ["register", "stats_cmd", "sync_github_cmd"]
