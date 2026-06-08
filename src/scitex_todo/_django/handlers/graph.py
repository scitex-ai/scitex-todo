#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Graph + tasks handlers: thin adapters over the scitex-todo Python API.

Zero task logic here — everything delegates to ``scitex_todo``: the store is
resolved + loaded by ``services.get_board`` (which calls ``resolve_tasks_path``
and ``load_tasks``), the mermaid source comes from ``build_mermaid``, and node
colors come from ``STATUS_STYLE``.
"""

from django.http import JsonResponse


def _status_colors() -> dict:
    """Single-source the status -> color map from the core package.

    ``STATUS_STYLE`` maps status -> (fill, stroke, dasharray). The board only
    needs fill + stroke + dashed flag, so project that into a small JSON dict.
    """
    from scitex_todo._mermaid import STATUS_STYLE

    return {
        status: {"fill": fill, "stroke": stroke, "dashed": bool(dash)}
        for status, (fill, stroke, dash) in STATUS_STYLE.items()
    }


def _build_graph(board) -> dict:
    """Build the {nodes, edges, status_colors, ...} payload from a board."""
    from scitex_todo._mermaid import build_mermaid

    ids = {t["id"] for t in board.tasks}

    nodes = [
        {
            "id": t["id"],
            "title": t["title"],
            "status": t["status"],
            "priority": t.get("priority"),
            "note": t.get("note"),
            "repo": t.get("repo"),
            # `parent` is the nesting field that drives the frontend
            # drill-down: children of a node N are tasks whose
            # `parent == N.id`. Emit it verbatim; if it points to an unknown
            # id the frontend treats this task as top-level (same lenient
            # stance as edges to unknown ids).
            "parent": t.get("parent"),
            # Append-only comment thread (list of {ts, author, text}); always
            # a list so the frontend can render / count without null-checks.
            "comments": t.get("comments") or [],
            # `kind` discriminator + compute metadata (north-star pillar #1,
            # validated by `_model._validate_tasks`). `kind: null` over the
            # wire = "task" (the default). FE renders compute affordances
            # (⚙ glyph + KV table) on `kind === "compute"` and decision
            # affordances (⚖️ glyph + LOUD operator-decision halo + impact
            # badge) on `kind === "decision"`.
            "kind": t.get("kind"),
            "job_id": t.get("job_id"),
            "host": t.get("host"),
            "command": t.get("command"),
            "started_at": t.get("started_at"),
            "finished_at": t.get("finished_at"),
            # `blocker` — variant that's blocking a status=blocked row
            # (operator TG 9522 + 9524, ADR-0004). Closed enum
            # `compute|dependency|dep|operator-decision|agent-wait|none`;
            # `null` on non-blocked rows + on blocked rows where the
            # variant hasn't been named yet (soft-degrade — FE renders a
            # generic 🚧 in that case, no extra badge). `dep` is the
            # legacy spelling kept during the deprecation window; `dependency`
            # is canonical per ADR-0007.
            "blocker": t.get("blocker"),
            # Operator-co-designed fields (TG 9667 / ADR-0007). Forwarded
            # verbatim to the FE so the board-v3 layout (filter bar + cards
            # + BLOCKING YOU panel) can render directly from the Task
            # dataclass shape without a second wire format.
            "task": t.get("task"),
            "project": t.get("project"),
            "created_at": t.get("created_at"),
            "goal": t.get("goal"),
            "agent": t.get("agent"),
            "last_activity": t.get("last_activity"),
            "pr_url": t.get("pr_url"),
            "issue_url": t.get("issue_url"),
        }
        for t in board.tasks
    ]

    edges = []
    for t in board.tasks:
        tid = t["id"]
        for dep in t.get("depends_on", []) or []:
            if dep in ids:
                edges.append({"source": dep, "target": tid, "kind": "depends_on"})
        for target in t.get("blocks", []) or []:
            if target in ids:
                edges.append({"source": tid, "target": target, "kind": "blocks"})

    return {
        "nodes": nodes,
        "edges": edges,
        "status_colors": _status_colors(),
        "mermaid": build_mermaid(board.tasks),
        "store_path": str(board.store_path),
        "task_count": len(board.tasks),
        # Fleet liveness — per-agent at-a-glance summary the operator can
        # scan from the board header to answer "who is alive + working on
        # what + blocked on me" without leaving the board (ADR-0008 design,
        # ticket `proj-scitex-todo-fleet-liveness`, operator TG 9576 acute
        # pain: 返事が来ない＝私にとって死んだのと同じ). FIRST SLICE — derived
        # from already-loaded tasks.yaml; the sidecar daemon + cross-host
        # roll-up land in follow-up PRs (no schema change today).
        "fleet": _build_fleet(board.tasks),
    }


# Statuses that exclude a task from the "runnable" count for liveness.
# Mirrors the task-harvest skill's non-runnable set (40_task-harvest.md):
# blocked / done / deferred / failed are not "could be progressed now";
# `goal` rows are umbrella nodes the harvest doesn't escalate either.
_LIVENESS_NONRUNNABLE: frozenset[str] = frozenset(
    {"blocked", "done", "deferred", "failed", "goal"}
)


def _priority_key(t: dict) -> tuple[int, str]:
    """Sort key: priority (lower = earlier; None sinks to the end), then id.

    Tasks without an explicit `priority` should rank LAST so the
    "current_task" derivation prefers explicitly-prioritized rows.
    """
    p = t.get("priority")
    return (10_000_000 if p is None else int(p), str(t.get("id") or ""))


def _last_activity_key(t: dict) -> str:
    """Sort key for "most recent activity": ISO-8601 strings sort lexically.

    Tasks without `last_activity` rank LAST (empty string sorts before any
    real ISO timestamp, so we negate by returning empty when present; the
    consumer reverses ordering). Returns the timestamp str verbatim — the
    `max()` caller uses it as a comparison key, not a parsed datetime.
    """
    return str(t.get("last_activity") or "")


def _build_fleet(tasks: list[dict]) -> list[dict]:
    """Return a list of {agent, status, current_task, ...} summaries.

    Grouping field: `agent` (fall back to `assignee` for older rows that
    pre-date the operator-co-designed field rename — both are forwarded
    to the FE on every node payload too). Tasks WITHOUT an agent are
    excluded so the dot-strip stays small + readable.

    Status precedence (most attention-demanding first), per the
    task-harvest skill's 4-value blocker enum + the operator's
    "blocking-me" lens:
      1. ``blocking-operator``  any task is blocker=operator-decision
      2. ``working``            any task is status=in_progress
      3. ``active``             any task touched within ACTIVE_WINDOW_S
      4. ``idle``               otherwise

    Per-agent fields:
      name                    the agent's id (e.g. proj-paper-scitex-clew)
      status                  one of the four above
      current_task            title of the agent's most-urgent task
      current_task_id         id of the same
      last_activity           max(last_activity) across the agent's tasks
      task_count              total tasks owned
      runnable_count          tasks NOT in the non-runnable set (a proxy
                              for "what's queued waiting to be picked up";
                              feeds the task-harvest sweep's ESCALATE list)
      blocked_count           tasks with status=blocked
      blocking_operator_count tasks with blocker=operator-decision (the
                              "stuck on YOU" subset the operator needs to
                              see jump out)
    """
    by_agent: dict[str, list[dict]] = {}
    for t in tasks:
        a = t.get("agent") or t.get("assignee")
        if not a:
            continue
        by_agent.setdefault(str(a), []).append(t)

    out: list[dict] = []
    for agent, items in sorted(by_agent.items()):
        # Status precedence.
        has_blocking_operator = any(
            t.get("blocker") == "operator-decision" for t in items
        )
        has_in_progress = any(t.get("status") == "in_progress" for t in items)
        last_activity = max(
            (str(t.get("last_activity") or "") for t in items),
            default="",
        )
        if has_blocking_operator:
            status = "blocking-operator"
        elif has_in_progress:
            status = "working"
        elif last_activity:
            status = "active"
        else:
            status = "idle"

        # current_task — prefer in_progress, then most-recent activity, then
        # highest-priority pending. Lets the dot-strip's tooltip answer
        # "what are they on right now" with the most-relevant single row.
        in_progress = [t for t in items if t.get("status") == "in_progress"]
        if in_progress:
            current = sorted(in_progress, key=_priority_key)[0]
        else:
            with_activity = [t for t in items if t.get("last_activity")]
            if with_activity:
                current = max(with_activity, key=_last_activity_key)
            else:
                pending = [t for t in items if t.get("status") == "pending"]
                pool = pending or items
                current = sorted(pool, key=_priority_key)[0]

        out.append(
            {
                "name": agent,
                "status": status,
                "current_task": current.get("task") or current.get("title"),
                "current_task_id": current.get("id"),
                "last_activity": last_activity or None,
                "task_count": len(items),
                "runnable_count": sum(
                    1
                    for t in items
                    if str(t.get("status") or "") not in _LIVENESS_NONRUNNABLE
                ),
                "blocked_count": sum(1 for t in items if t.get("status") == "blocked"),
                "blocking_operator_count": sum(
                    1 for t in items if t.get("blocker") == "operator-decision"
                ),
            }
        )
    return out


def handle_graph(request, board):
    """GET graph -> structured nodes + edges + status colors (+ mermaid)."""
    return JsonResponse(_build_graph(board))


def handle_tasks(request, board):
    """GET tasks -> the raw validated task list (for grids / debugging)."""
    return JsonResponse(
        {"tasks": list(board.tasks), "store_path": str(board.store_path)}
    )


def handle_ping(request, board):
    """GET ping -> health check (no store needed)."""
    return JsonResponse({"status": "ok"})


def handle_rev(request, board):
    """GET rev -> a cheap revision fingerprint of the store.

    Returns the store's ``mtime`` (float) and task ``count`` without building
    the full graph payload, so the frontend can poll this to detect when
    another agent has changed the shared YAML and trigger a refresh. The board
    is loaded mtime-cached, so unchanged stores hit the cache.
    """
    return JsonResponse(
        {
            "mtime": board.mtime,
            "count": len(board.tasks),
            "store_path": str(board.store_path),
        }
    )


# EOF
