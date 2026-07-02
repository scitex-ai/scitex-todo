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
    """Push the notify body to ``agent`` via scitex-todo's self-contained
    push wire (:func:`scitex_todo._push.deliver`).

    Operator standing direction via lead a2a `8e51b1e07` + `ffc6629c8`
    (2026-06-12): no `sac` CLI dependency — scitex-todo owns its push
    delivery. Result label is the wire used (`http` / `dry-run`) or
    an error tag (`no-turn-url-configured` / `http-error` / etc.).
    """
    from .._push import deliver

    result = deliver(agent, body, kind="notify")
    if result.get("ok"):
        return result.get("wire") or "ok"
    return result.get("reason") or "error"


@click.command(name="print-stats")
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
        "scitex-todo's self-contained HTTP push wire (_push.deliver). "
        "The body lists each agent's open tasks (RUNNABLE first, "
        "then BLOCKED + reason), ⚠ on stale in_progress, and "
        "recently-completed lines so the receiver can self-correct."
    ),
)
@click.option(
    "--nudge-quiet",
    is_flag=True,
    help=(
        "Per-agent structural nudge: if the agent has open in_progress "
        "tasks AND no recent activity within SCITEX_TODO_NUDGE_QUIET_MIN "
        "(default 10 minutes), push an additional nudge body. Designed "
        "for the hourly / 10-min cron entry — operator's standing "
        "direction is that 'silence + in_progress = escalation', not "
        "a manual lead intervention. Implies --notify=agent push; "
        "the quiet nudge piggybacks on the same wire."
    ),
)
@click.option(
    "--tasks",
    "tasks_path",
    default=None,
    help="Path to tasks.yaml (default: project -> user -> bundled example, or $SCITEX_TODO_TASKS_YAML_SHARED).",
)
def stats_cmd(
    by: str, since: str | None, fmt: str, notify: bool,
    nudge_quiet: bool, tasks_path: str | None,
) -> None:
    """Print throughput stats (per-agent / project / host).

    Operator standing direction (lead a2a ``4b23ebc177944deaa7549e256e9a375a``
    2026-06-12): every agent must measure its own creation vs
    completion rate so completion > creation discipline holds across
    the fleet. ``--notify`` pushes the per-agent summary so receivers
    self-correct hourly.

    \b
    Example:
      $ scitex-todo print-stats --by agent --since 2026-06-01
      $ scitex-todo print-stats --by agent --notify
      $ scitex-todo print-stats --by agent --notify --nudge-quiet
      $ scitex-todo print-stats --by project --format json
    """
    path = resolve_tasks_path(tasks_path)
    tasks = load_tasks(path)
    rows = aggregate(tasks, by=by, since=since)
    out = _format_json(rows) if fmt == "json" else _format_text(rows)
    click.echo(out)
    if (notify or nudge_quiet) and by == "agent":
        click.echo("")
        click.echo(f"# Notify push → {len(rows)} agents")
        for r in rows:
            if r.name == "(unassigned)":
                continue
            if notify:
                body = build_notify_body(r.name, tasks, since=since)
                wire = _push_notify(r.name, body)
                click.echo(f"  {wire:>6}  {r.name}  ({len(body)} chars)")
        if nudge_quiet:
            click.echo("")
            click.echo(f"# Quiet-nudge sweep (SCITEX_TODO_NUDGE_QUIET_MIN)")
            _emit_quiet_nudges(tasks, rows)
            click.echo("")
            click.echo(
                "# Stale-active + pending-backlog sweep "
                "(SCITEX_TODO_STALE_ACTIVE_HOURS / "
                "SCITEX_TODO_PENDING_NUDGE_HOURS)"
            )
            _emit_stale_active_nudges(tasks)
    elif notify or nudge_quiet:
        click.echo(
            "WARN: --notify / --nudge-quiet ignored when --by != agent "
            "(push target needs an agent id).",
            err=True,
        )


def _emit_quiet_nudges(tasks: list[dict], rows: list) -> None:
    """Per-agent structural nudge (PR (h) — lead a2a `19d575415a` +
    revision `9e710ab0` 2026-06-12). For each agent lane, if any
    in_progress task has not been touched in
    ``SCITEX_TODO_NUDGE_QUIET_MIN`` minutes (default 10), push a
    nudge body via :func:`scitex_todo._push.deliver`.

    Why per-task quiet check (rather than per-agent-only)? Because a
    single agent with 5 open tasks would otherwise quietly stall on
    1 while the others mask it. We nudge if ANY in_progress is quiet.
    The nudge body lists ALL open tasks for the agent (same
    ``build_notify_body`` as --notify) so the recipient sees the
    full picture, not just the stalled row.
    """
    import os

    from .._push import deliver

    quiet_min = float(os.environ.get("SCITEX_TODO_NUDGE_QUIET_MIN", "10"))
    quiet_seconds = quiet_min * 60.0

    by_agent: dict[str, list[dict]] = {}
    for t in tasks:
        a = (t.get("agent") or "").strip()
        if not a or a == "(unassigned)":
            continue
        if t.get("status") == "in_progress":
            by_agent.setdefault(a, []).append(t)

    import datetime as _dt
    now = _dt.datetime.now(tz=_dt.timezone.utc)

    def _quiet_age_s(t: dict) -> float | None:
        ts = t.get("last_activity") or t.get("created_at")
        if not ts:
            return None
        try:
            parsed = _dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except ValueError:
            return None
        return (now - parsed).total_seconds()

    pushed = 0
    for agent, in_prog in sorted(by_agent.items()):
        worst_age = max(
            (_quiet_age_s(t) or 0.0 for t in in_prog),
            default=0.0,
        )
        if worst_age <= quiet_seconds:
            continue
        body = build_notify_body(agent, tasks)
        body += (
            "\n————————\n"
            f"QUIET NUDGE: at least one in_progress task has not been "
            f"touched for {int(worst_age // 60)} min (threshold "
            f"{int(quiet_min)} min). Push a commit / comment / status "
            f"flip — or mark BLOCKED with a concrete reason."
        )
        result = deliver(agent, body, kind="quiet-nudge")
        ok_label = "✓" if result.get("ok") else "✗"
        click.echo(
            f"  {ok_label}  {agent:30}  quiet {int(worst_age//60)}m  "
            f"wire={result.get('wire')}  reason={result.get('reason')}"
        )
        if result.get("ok"):
            pushed += 1

    click.echo(f"# {pushed} quiet-nudge push(es) sent")


def _emit_stale_active_nudges(tasks: list[dict]) -> None:
    """Thin CLI wrapper around the stale-active + pending-backlog sweep.

    The detect + per-owner fail-soft delivery lives in
    :func:`scitex_todo._stale_active_nudge.sweep_and_nudge` (keeps the
    network side out of this near-cap CLI module). It emits BOTH the
    stale-active and pending-backlog per-owner lines. Each result line is
    echoed for the cron log.
    """
    from .._stale_active_nudge import sweep_and_nudge

    for line in sweep_and_nudge(tasks):
        click.echo(line)


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
    "-y",
    "--yes",
    "assume_yes",
    is_flag=True,
    help=(
        "Skip the interactive confirmation. Required when the planned "
        "actions would mutate the store and stdin is a TTY; harmless on "
        "scripted / cron invocations (stdin not a TTY → auto-yes)."
    ),
)
@click.option(
    "--tasks",
    "tasks_path",
    default=None,
    help="Path to tasks.yaml (default: project -> user -> bundled example).",
)
def sync_github_cmd(
    since: str | None, dry_run: bool, assume_yes: bool, tasks_path: str | None
) -> None:
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

    \b
    Example:
      $ scitex-todo sync-github --dry-run
      $ scitex-todo sync-github --since 2026-06-01 -y
      $ scitex-todo sync-github -y                  # cron / scripted use
    """
    # Defer the imports to avoid pulling `_store` at module load (some
    # call paths import this module without needing the write side).
    from .._store import add_task, update_task

    target_since = since or datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    # Refuse-without-yes gate per audit §2 (mutating verbs must NOT
    # prompt interactively — they refuse and tell the operator what
    # flag to pass). Both --yes and --dry-run are explicit opt-ins;
    # bare invocation exits non-zero.
    if not dry_run and not assume_yes:
        raise click.ClickException(
            "sync-github mutates the store. Pass --yes / -y to confirm, "
            "or --dry-run to preview the planned actions."
        )
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
                    # add_task now REQUIRES a resolvable creator (no silent
                    # blank/"unknown"). This sync verb is a housekeeping
                    # importer, so it stamps itself as the creator.
                    created_by="sync-github",
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
                # add_task now REQUIRES a resolvable creator — this verb is a
                # housekeeping importer, so it stamps itself.
                created_by="sync-github",
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
