#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Graph + tasks handlers: thin adapters over the scitex-todo Python API.

Zero task logic here — everything delegates to ``scitex_todo``: the store is
resolved + loaded by ``services.get_board`` (which calls ``resolve_tasks_path``
and ``load_tasks``), the mermaid source comes from ``build_mermaid``, and node
colors come from ``STATUS_STYLE``.
"""

from pathlib import Path

from django.http import JsonResponse

#: The board's HTML templates live here (board_v3.html + any partials). Their
#: max mtime is a cheap fingerprint for "the GUI code changed" — see
#: :func:`_board_asset_rev`.
_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates" / "scitex_todo"


def _board_asset_rev() -> float:
    """Max mtime across the board's HTML templates — a cheap GUI-version stamp.

    The ``/rev`` poll already tells the browser when the store DATA changed;
    this adds a second signal for when the board's own GUI CODE changed (a
    template/JS/CSS edit, or a merged PR that updated ``board_v3.html`` under
    the editable install). In DEBUG Django already serves the new template on
    the next full page load — the open pane just doesn't know to fetch it. The
    frontend compares this stamp across polls and hard-reloads (``location.
    reload()``) when it moves, so the operator's board pane picks up GUI
    updates WITHOUT a manual restart / F5. Falls back to ``0.0`` if the
    template dir can't be stat'd (never raises into the poll response).
    """
    try:
        mtimes = [p.stat().st_mtime for p in _TEMPLATE_DIR.glob("*.html")]
    except OSError:
        return 0.0
    return max(mtimes, default=0.0)


def _status_colors() -> dict:
    """Single-source the status -> color map from the core package.

    ``STATUS_STYLE`` maps status -> (fill, stroke, dasharray). The board only
    needs fill + stroke + dashed flag, so project that into a small JSON dict.
    """
    from scitex_todo._diagram import STATUS_STYLE

    return {
        status: {"fill": fill, "stroke": stroke, "dashed": bool(dash)}
        for status, (fill, stroke, dash) in STATUS_STYLE.items()
    }


def _compute_deadline_next(t):
    """Lazy proxy to scitex_todo._model.next_deadline_for_task.

    Kept here so the handler module avoids a top-level import cycle in
    test contexts that import handlers before the model package is
    fully loaded. (hook-bypass: line-limit.)
    """
    from scitex_todo._model import next_deadline_for_task

    try:
        return next_deadline_for_task(t)
    except Exception:
        return None


def _build_graph(board) -> dict:
    """Build the {nodes, edges, status_colors, ...} payload from a board."""
    from scitex_todo._diagram import build_mermaid

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
            # P4 (lead approved 2026-06-12) — deadline + scheduled ISO-8601
            # fields. FE date-pill prefers `deadline` over the title-parsed
            # date when present; absent → legacy title parse keeps working.
            "deadline": t.get("deadline"),
            "scheduled": t.get("scheduled"),
            # P4 PR3 (lead-approved 2026-06-12) — multi/recurring.
            # `deadlines` is the optional list form (mutually exclusive
            # with `deadline`); `deadline_next` is the SERVER-COMPUTED
            # next occurrence (recurring + multi expanded) the FE uses
            # for date-pill / sort / OVERDUE. Imported lazily to keep
            # the handler module light.
            "deadlines": t.get("deadlines"),
            "deadline_next": _compute_deadline_next(t),
            "agent": t.get("agent"),
            "last_activity": t.get("last_activity"),
            "pr_url": t.get("pr_url"),
            "issue_url": t.get("issue_url"),
            # USER-role fields (scitex-todo's entity is the USER;
            # an agent is just user.kind=agent). The detail drawer renders
            # a ROLES section from these. `created_by` is the creating user
            # (absent on legacy rows — FE falls back to the earliest comment
            # author, else "—"). `collaborators` / `subscribers` are the
            # persistent role lists (ADR-0009); always emitted as lists so
            # the FE can render "—" for empty without null-checks.
            "created_by": t.get("created_by"),
            "collaborators": t.get("collaborators") or [],
            "subscribers": t.get("subscribers") or [],
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
        # P10 (lead a2a 2026-06-12) — user-defined project clusters from
        # the same store. Empty list when the YAML has no ``groups:`` key
        # (back-compat). FE renders group headers above the per-project
        # column grid + a horizontal "spans_all" strip for the lead group.
        "groups": [g.to_dict() for g in (board.groups or [])],
    }


# Statuses that exclude a task from the "runnable" count for liveness.
# Mirrors the task-harvest skill's non-runnable set (40_task-harvest.md):
# blocked / done / deferred / failed / cancelled are not "could be
# progressed now"; `goal` rows are umbrella nodes the harvest doesn't
# escalate either. ``cancelled`` (closed as not planned) is terminal, so
# it never counts toward runnable liveness — same as done/failed.
_LIVENESS_NONRUNNABLE: frozenset[str] = frozenset(
    {"blocked", "done", "deferred", "failed", "cancelled", "goal"}
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


def _build_fleet(tasks: list[dict], *, now=None) -> list[dict]:
    """Return a list of {agent, status, current_task, ...} summaries.

    Grouping field: `agent` (fall back to `assignee` for older rows that
    pre-date the operator-co-designed field rename — both are forwarded
    to the FE on every node payload too). Tasks WITHOUT an agent are
    excluded so the dot-strip stays small + readable.

    Status precedence (most attention-demanding first), per the
    task-harvest skill's 4-value blocker enum + the operator's
    "blocking-me" lens, plus the **working-status decay** rule
    (operator TG12739, lead a2a ``f556b755``, 2026-06-13):

      1. ``blocking-operator``  any task is blocker=operator-decision
      2. ``working``            any task is status=in_progress *AND* the
                                agent's most-recent ``last_activity`` is
                                within ``SCITEX_TODO_FLEET_WORKING_MIN``
                                minutes (default 10). Without the
                                freshness gate, agents that forgot to
                                flip in_progress→pending stay "working"
                                forever and the UI lies.
      3. ``stale``              any task is status=in_progress but the
                                agent's most-recent ``last_activity`` is
                                older than the working window (or absent).
                                This is the **decay** state — surfaces
                                the "forgot-to-flip" case as a distinct
                                signal so the operator can prune it.
      4. ``active``             no in_progress task, but the agent's
                                most-recent ``last_activity`` is within
                                ``SCITEX_TODO_FLEET_ACTIVE_MIN`` minutes
                                (default 60). Activity badge derived
                                from FRESHNESS, not manual status.
      5. ``idle``               otherwise.

    The two windows are env-configurable so the operator can tune
    "what counts as live" without a code change. They default
    ``working_min`` < ``active_min`` so the badges read as
    nested-confidence intervals: tight green-light "working", looser
    yellow-light "active", everything else "idle".

    Per-agent fields:
      name                    the agent's id (e.g. proj-paper-scitex-clew)
      status                  one of the five above
      current_task            title of the agent's most-urgent task
      current_task_id         id of the same
      last_activity           max(last_activity) across the agent's tasks
      task_count              total tasks owned
      runnable_count          tasks NOT in the non-runnable set (a proxy
                              for "what's queued waiting to be picked up";
                              feeds the task-harvest sweep's ESCALATE list)
      blocked_count           tasks with status=blocked
      blocking_operator_count count of the "waiting-on-operator" queue:
                              cards matching the board's BLOCKING-YOU
                              predicate (status=blocked AND
                              blocker=operator-decision), the "stuck on
                              YOU" subset the operator needs to see jump
                              out. Derived from the SAME predicate as
                              ``list_tasks(blocking_me=True)`` (the
                              ``_match(..., blocking_me=True)`` SSOT) — NOT
                              a re-implemented check.
      blocking_operator_ids   the ids of those same cards, so the FE can
                              link straight to the queue without re-walking
                              the store.
    """
    import datetime as _dt
    import os

    def _env_minutes(key: str, default: int) -> float:
        try:
            return float(os.environ.get(key, str(default)))
        except (TypeError, ValueError):
            return float(default)

    working_window_s = _env_minutes("SCITEX_TODO_FLEET_WORKING_MIN", 10) * 60.0
    active_window_s = _env_minutes("SCITEX_TODO_FLEET_ACTIVE_MIN", 60) * 60.0
    cur = now or _dt.datetime.now(tz=_dt.timezone.utc)

    def _seconds_since(ts: str) -> float | None:
        if not ts:
            return None
        try:
            parsed = _dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_dt.timezone.utc)
        return (cur - parsed).total_seconds()

    from ..._owner import card_owner

    by_agent: dict[str, list[dict]] = {}
    for t in tasks:
        # Owner SSOT (agent||assignee). Owner-less rows are excluded from the
        # liveness dot-strip by design (keeps it small/readable); add_task now
        # REJECTS owner-less cards at creation, so this only ever skips legacy
        # rows pending re-home.
        a = card_owner(t)
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
        age_s = _seconds_since(last_activity)
        fresh_working = age_s is not None and age_s <= working_window_s
        fresh_active = age_s is not None and age_s <= active_window_s
        if has_blocking_operator:
            status = "blocking-operator"
        elif has_in_progress and fresh_working:
            status = "working"
        elif has_in_progress:
            # decay: in_progress but quiet for > working window → stale.
            status = "stale"
        elif fresh_active:
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

        # `overdue_count` = tasks past their next deadline AND not in a
        # terminal state. Feeds the operator UX (todo-p6-overdue-ui):
        # "attended an overdue task but no suitable UI to act" — the
        # fleet strip + filter bar can now surface a per-agent overdue
        # tally without re-walking the store on the client side.
        from scitex_todo._model import is_overdue as _is_overdue

        # "Waiting-on-operator" queue (operator P1
        # todo-operator-blocking-queue-view): cards stuck on a
        # human decision. SSOT — reuse the board's BLOCKING-YOU
        # predicate (``_match(..., blocking_me=True)`` == the same
        # filter ``list_tasks(blocking_me=True)`` uses) so the count
        # and id list never drift from the canonical
        # ``status==blocked AND blocker==operator-decision`` rule.
        from ..._store import _match

        blocking_operator_ids = [
            str(t.get("id"))
            for t in items
            if _match(t, blocking_me=True) and t.get("id") is not None
        ]

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
                "blocking_operator_count": len(blocking_operator_ids),
                "blocking_operator_ids": blocking_operator_ids,
                "overdue_count": sum(1 for t in items if _is_overdue(t, now=cur)),
            }
        )
    return out


#: In-process cache of the BUILT graph payload, keyed on
#: ``(store_path_str, mtime)``. ``get_board`` already mtime-caches the
#: parsed task list; this cache piggybacks on the same key to skip the
#: per-request ``_build_graph`` rebuild (mermaid + nodes + edges +
#: fleet + groups) when the store hasn't changed. ~50-100 ms savings
#: per /graph for a 500-task store — directly addresses operator
#: TG12911 ("the board UI is slow") and is the Stage-1 perf half of
#: lead a2a `aa02fb0e` + `e5243003`.
#:
#: NEVER authoritative: any change to ``board.mtime`` invalidates the
#: entry; entries are dropped on TTL via :func:`_graph_cache_gc`.
_GRAPH_PAYLOAD_CACHE: dict = {}
_GRAPH_PAYLOAD_CACHE_TTL_S = 3_600.0  # 1h, mirrors BoardState's TTL.


def _graph_cache_gc() -> None:
    """Drop stale entries from :data:`_GRAPH_PAYLOAD_CACHE`."""
    import time
    now = time.time()
    stale = [
        k for k, (_, ts) in _GRAPH_PAYLOAD_CACHE.items()
        if now - ts > _GRAPH_PAYLOAD_CACHE_TTL_S
    ]
    for k in stale:
        _GRAPH_PAYLOAD_CACHE.pop(k, None)


def _graph_cache_reset() -> None:
    """Test hook — clear the cache between assertions."""
    _GRAPH_PAYLOAD_CACHE.clear()


def handle_graph(request, board):
    """GET graph -> structured nodes + edges + status colors (+ mermaid).

    Cached by ``(store_path, mtime)``; on hit, returns the prior payload
    directly. Cache is invalidated when the YAML's mtime changes (i.e.
    any agent or operator write rolls the cache forward by one rebuild).
    The auto-update SSE wire (PR-C in the lead-approved Stage 2 plan)
    will additionally PUSH the new payload — this cache is the same
    derivation, just stored.
    """
    import time
    _graph_cache_gc()
    key = (str(board.store_path), board.mtime)
    hit = _GRAPH_PAYLOAD_CACHE.get(key)
    if hit is not None:
        payload, _ = hit
        # Touch the access time so the GC doesn't sweep a hot key.
        _GRAPH_PAYLOAD_CACHE[key] = (payload, time.time())
        return JsonResponse(payload)
    payload = _build_graph(board)
    _GRAPH_PAYLOAD_CACHE[key] = (payload, time.time())
    return JsonResponse(payload)


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
            # GUI-code version stamp: lets the open pane hard-reload itself
            # when the board template changes (no manual restart/F5).
            "asset_rev": _board_asset_rev(),
        }
    )


# EOF
