#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mutation-side Python API for the scitex-todo task store.

Sits on top of ``_model.load_tasks`` / ``_model.save_tasks`` to give agents,
the CLI verbs, the MCP tools, and (later) the web GUI write handlers a
single, well-tested surface for:

    add_task        Append a new task to the store.
    update_task     Mutate fields of an existing task by id.
    complete_task   Convenience: set status='done' + stamp completion meta.
    list_tasks      Filter by scope / assignee / status.
    summary         Count tasks per status (and by scope/assignee).

Every mutation runs through :func:`_model.save_tasks`, which holds an
``fcntl.flock``-based mutex on a sibling lock file so two concurrent writers
cannot interleave (PHASE 1 prereq for the cross-host sync substrate — see
``GITIGNORED/ARCHITECTURE.md`` Req 2).

The filter functions are read-only and do NOT lock — they snapshot the
store via :func:`_model.load_tasks` and apply the filter in memory.

Design constraints
------------------
- **Generic** (Req 8): scope/assignee/status are free-form strings. The
  helpers don't know what an "agent" is.
- **Centralized** (Req 3): the default store is whatever
  :func:`_paths.resolve_tasks_path` returns; callers can override with an
  explicit ``store=`` path. The user-scope default
  (``~/.scitex/todo/tasks.yaml``) covers Req 7.
- **Shared with scopes** (Req 1): ``$SCITEX_TODO_SCOPE`` provides the
  default value for ``list_tasks(scope=...)`` when the caller doesn't pass
  one explicitly. Pass ``scope=""`` (empty string) to ignore the env
  default and see everything.
"""

from __future__ import annotations

import datetime as _dt
import getpass
import os
from pathlib import Path

from ._model import (
    VALID_STATUSES,
    TaskValidationError,
    _save_tasks_unlocked,
    _store_lock,
    load_tasks,
    save_tasks,
)
from ._paths import resolve_tasks_path

#: Env var name an agent sets to scope its default `list_tasks` / `summary`
#: view. The CLI's `--scope` flag overrides this; pass `scope=""` in the
#: Python API to see the unfiltered store.
ENV_SCOPE = "SCITEX_TODO_SCOPE"

#: Env var name carrying the agent's identity. Used as the default
#: `completed_by` when :func:`complete_task` doesn't get an explicit `by=`.
ENV_AGENT = "SCITEX_TODO_AGENT"


class TaskNotFoundError(KeyError):
    """Raised when an update/complete target id is not in the store."""


# --------------------------------------------------------------------------- #
# Internal helpers                                                            #
# --------------------------------------------------------------------------- #
def _resolved_store(store: str | Path | None) -> Path:
    """Resolve a store path argument through the precedence chain.

    ``None`` ⇒ apply the full resolution chain (`_paths.resolve_tasks_path`).
    Explicit path ⇒ used as-is (must exist for reads; will be created for
    fresh writes by :func:`_model.save_tasks`).
    """
    return resolve_tasks_path(store) if store is None else Path(store).expanduser()


def _default_scope(arg: str | None) -> str | None:
    """Resolve a scope argument, honoring ``$SCITEX_TODO_SCOPE`` as the
    default.

    ``None`` (caller didn't pass anything) → env var if set, else ``None``
    (no filter).
    Empty string ``""`` → caller explicitly opted out of filtering.
    Non-empty string → used as-is.
    """
    if arg is None:
        env = os.environ.get(ENV_SCOPE)
        return env if env else None
    if arg == "":
        return None
    return arg


def _default_agent(arg: str | None) -> str:
    """Resolve a `completed_by` argument with the env→login→unknown chain.

    Per ``GITIGNORED/QUESTIONS.md`` #2 the precedence is
    ``$SCITEX_TODO_AGENT`` → ``getpass.getuser()`` → ``"unknown"`` (final
    fallback handles environments where login info isn't available).
    """
    if arg:
        return arg
    env = os.environ.get(ENV_AGENT)
    if env:
        return env
    try:
        return getpass.getuser()
    except Exception:  # pragma: no cover — extremely rare environments
        return "unknown"


def _utc_now_iso() -> str:
    """ISO-8601 UTC timestamp with second resolution and the ``Z`` suffix.

    Trims the microseconds (the operator reads these on the board; second
    resolution is plenty) and uses the canonical ``Z`` suffix rather than
    ``+00:00`` so the string round-trips losslessly through YAML.
    """
    return (
        _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _match(
    task: dict,
    *,
    scope: str | None = None,
    assignee: str | None = None,
    status: str | None = None,
    statuses: list[str] | None = None,
    agent: str | None = None,
    project: str | None = None,
    host: str | None = None,
    blocker: str | None = None,
    kind: str | None = None,
    id_prefix: str | None = None,
    blocking_me: bool = False,
    overdue: bool = False,
) -> bool:
    """String-equality + predicate filter. ``None`` / empty = no constraint.

    Filter semantics (per PR #66 / ADR-0008 D2 + D10):
      - ``status`` (single) and ``statuses`` (multi) are OR-combined: a
        row matches if its status is in ``set([status]) ∪ set(statuses)``
        (after dropping ``None``).
      - ``blocker="__none"`` matches rows with no blocker field (the
        explicit "no blocker named" filter the board's `/graph` uses).
      - ``kind=None`` is NO filter; ``kind="task"`` matches both
        explicit ``"task"`` AND ``absent`` rows (since absent ≡ "task"
        per ADR-0002).
      - ``id_prefix`` is a substring match on the front of ``id`` —
        the cheap "find my project's rows" without remembering exact
        ids.
      - ``blocking_me`` is the BLOCKING-YOU predicate (board-v3 panel):
        ``status == "blocked" AND blocker == "operator-decision"``.
        Composes with the other filters via AND.
    """
    if scope is not None and task.get("scope") != scope:
        return False
    if assignee is not None and task.get("assignee") != assignee:
        return False
    if agent is not None and task.get("agent") != agent:
        return False
    if project is not None and task.get("project") != project:
        return False
    if host is not None and task.get("host") != host:
        return False
    if blocker is not None:
        if blocker == "__none":
            if task.get("blocker"):
                return False
        elif task.get("blocker") != blocker:
            return False
    if kind is not None:
        eff = task.get("kind") or "task"
        if eff != kind:
            return False
    if id_prefix and not str(task.get("id", "")).startswith(id_prefix):
        return False
    # Union the single + multi status constraints. None and empty list
    # collapse to "no constraint." When BOTH are provided, the union is
    # checked (so callers can extend an existing single-status default).
    allowed: set[str] = set()
    if status is not None:
        allowed.add(status)
    if statuses:
        allowed.update(statuses)
    if allowed and task.get("status") not in allowed:
        return False
    if blocking_me and not (
        task.get("status") == "blocked" and task.get("blocker") == "operator-decision"
    ):
        return False
    if overdue:
        from ._model import is_overdue as _is_overdue

        if not _is_overdue(task):
            return False
    return True


# --------------------------------------------------------------------------- #
# Public API                                                                  #
# --------------------------------------------------------------------------- #
def add_task(
    store: str | Path | None = None,
    *,
    id: str,
    title: str,
    status: str = "pending",
    scope: str | None = None,
    assignee: str | None = None,
    priority: int | None = None,
    parent: str | None = None,
    note: str | None = None,
    depends_on: list[str] | None = None,
    blocks: list[str] | None = None,
    repo: str | None = None,
    **extras,
) -> dict:
    """Append a new task to ``store`` and persist via :func:`save_tasks`.

    Returns the inserted task mapping (a fresh dict, not the underlying
    YAML node) for convenient round-trip use by callers — the CLI prints
    it, the MCP tools serialize it as the JSON result.

    The ``**extras`` keyword catches operator-co-designed Task dataclass
    fields (``task`` / ``project`` / ``host`` / ``agent`` / ``goal`` /
    ``last_activity`` / ``blocker`` / ``pr_url`` / ``issue_url`` / ``kind``
    + compute metadata ``job_id`` / ``command`` / ``started_at`` /
    ``finished_at``) without an explosion of named parameters. ``None``
    values are dropped; non-``None`` values flow into the new task dict
    and the writer's validator gates closed enums (``status`` / ``kind``
    / ``blocker``) — typos raise ``TaskValidationError`` with the bad
    value and the valid set. Unknown keys are accepted at this layer
    (forward-compat); the validator decides whether they're shape-valid.

    Raises
    ------
    TaskValidationError
        On duplicate id or any other structural fault — `save_tasks`
        re-runs the full validation gate before touching disk.
    """
    resolved = _resolved_store(store)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    new: dict = {"id": id, "title": title, "status": status}
    # D11 partial-fix (ADR-0008): auto-stamp ``created_at`` +
    # ``last_activity`` at insert time. ``created_at`` is the immutable
    # insert stamp; ``last_activity`` starts equal and ticks on every
    # subsequent successful update_task. Callers can override by passing
    # the field explicitly (e.g. importers replaying historical state).
    _stamp = _utc_now_iso()
    new["created_at"] = _stamp
    new["last_activity"] = _stamp
    if scope is not None:
        new["scope"] = scope
    if assignee is not None:
        new["assignee"] = assignee
    if priority is not None:
        new["priority"] = priority
    if parent is not None:
        new["parent"] = parent
    if note is not None:
        new["note"] = note
    if depends_on is not None:
        new["depends_on"] = list(depends_on)
    if blocks is not None:
        new["blocks"] = list(blocks)
    if repo is not None:
        new["repo"] = repo
    # Operator-co-designed surface (TG 9667) + compute metadata
    # (ADR-0002). Forwarded through **extras so callers don't have to
    # match a long explicit parameter list and the writer's validator
    # gates the closed enums.
    for key, value in extras.items():
        if value is None:
            continue
        new[key] = value

    # Lock for the FULL read-modify-write — without this, two concurrent
    # writers each load a stale snapshot and the second `save_tasks` call
    # silently clobbers the first writer's insert. See
    # tests/scitex_todo/test__store.py::test_two_concurrent_writers...
    with _store_lock(resolved):
        tasks = load_tasks(resolved) if resolved.exists() else []
        # WIP-validation gate (operator standing direction via lead a2a
        # `d99b8de6839d46e586e4ee692f43c1d9` + ``5acfbb5d0db44db8a7fa4f70c399d539``,
        # 2026-06-12). Count the new task's agent's open tasks (status NOT
        # in {done, goal}) BEFORE the append; WARN to stderr at the
        # threshold, HARD REFUSE (raise) at 2x. Goal-tier umbrellas are
        # excluded — they accumulate by design, not by WIP-failure.
        # Direct YAML hand-edits bypass this gate by design (CLI/MCP
        # path enforcement only — operator wants the CLI/MCP path made
        # fat so hand-edits are unnecessary, not policed).
        agent_for_wip = new.get("agent")
        if agent_for_wip and new.get("status") not in _wip_excluded_statuses():
            from ._throughput import evaluate_wip

            rep = evaluate_wip(tasks, agent_for_wip)
            if rep is not None and rep.is_refuse:
                raise TaskValidationError(
                    f"WIP gate refuses add: {rep.agent} already has "
                    f"{rep.open_count} open tasks (>= 2 × limit "
                    f"{rep.limit}). Close existing tasks before adding "
                    f"more — see SCITEX_TODO_WIP_LIMIT env."
                )
            if rep is not None and rep.is_warn:
                import sys

                print(
                    f"WARN: WIP gate — {rep.agent} now has "
                    f"{rep.open_count + 1} open tasks (limit {rep.limit}). "
                    f"Completion is not keeping up with creation; close "
                    f"existing before adding more.",
                    file=sys.stderr,
                )
        tasks.append(new)
        _save_tasks_unlocked(tasks, resolved)
    return dict(new)


def _wip_excluded_statuses() -> frozenset[str]:
    """Re-export from ``_throughput`` so the gate's exclusion list stays
    a single source of truth (lead-confirmed ``5acfbb5d`` — exclude
    ``done`` + ``goal``)."""
    from ._throughput import WIP_EXCLUDED_STATUSES

    return WIP_EXCLUDED_STATUSES


def update_task(
    store: str | Path | None = None,
    task_id: str | None = None,
    **fields,
) -> dict:
    """Update fields of the task with id ``task_id``; return the merged dict.

    Any keyword argument becomes a field on the task. Passing ``None`` for
    a field DELETES it (matches the operator's mental model: "clear the
    scope" = `update_task(..., scope=None)`). To leave a field untouched,
    just omit it.

    Raises
    ------
    TaskNotFoundError
        If no task matches ``task_id``.
    TaskValidationError
        If the resulting mutation is structurally invalid.
    """
    if not task_id:
        raise TypeError("update_task() requires a non-empty task_id")
    resolved = _resolved_store(store)
    result: dict | None = None
    transitioned_to_done = False
    with _store_lock(resolved):
        tasks = load_tasks(resolved)
        for task in tasks:
            if task.get("id") == task_id:
                prior_status = task.get("status")
                for key, value in fields.items():
                    if value is None:
                        task.pop(key, None)
                    else:
                        task[key] = value
                # D11 partial-fix (ADR-0008): auto-stamp ``last_activity``
                # on every successful mutation (drives the recency-color
                # signal on the board). Skip if the caller passed an
                # explicit ``last_activity`` field this call — their
                # value wins over the auto-stamp.
                if "last_activity" not in fields:
                    task["last_activity"] = _utc_now_iso()
                _save_tasks_unlocked(tasks, resolved)
                result = dict(task)
                transitioned_to_done = (
                    fields.get("status") == "done" and prior_status != "done"
                )
                break
    if result is None:
        raise TaskNotFoundError(f"task id {task_id!r} not found in {resolved}")
    # Active-unblock DRIVE (ADR-0009) — a direct status→done via
    # update_task() drives the same unblock as complete_task(). Outside
    # the lock; the handler's per-card token dedupe makes a double-path
    # (e.g. update_task then complete_task) idempotent.
    if transitioned_to_done:
        _emit_unblock_for_dependents(resolved, task_id, by=None)
    return result


def complete_task(
    store: str | Path | None = None,
    task_id: str | None = None,
    *,
    by: str | None = None,
) -> dict:
    """Mark ``task_id`` as ``done`` and stamp ``_log_meta.completed_{at,by}``.

    Idempotent per ``GITIGNORED/QUESTIONS.md`` #3: re-completing a
    ``done`` task is a no-op (timestamps stay frozen from the first
    completion). Pass ``by=`` to override the
    ``$SCITEX_TODO_AGENT`` → ``$USER`` → ``"unknown"`` precedence chain.

    Returns the (post-mutation) task mapping.

    Raises
    ------
    TaskNotFoundError
        If no task matches ``task_id``.
    """
    if not task_id:
        raise TypeError("complete_task() requires a non-empty task_id")
    resolved = _resolved_store(store)
    result: dict | None = None
    transitioned = False
    with _store_lock(resolved):
        tasks = load_tasks(resolved)
        for task in tasks:
            if task.get("id") == task_id:
                if task.get("status") == "done":
                    # Idempotent: don't refresh the stamp, just return.
                    # No unblock emit — re-completing changed nothing.
                    return dict(task)
                task["status"] = "done"
                log_meta = task.get("_log_meta")
                if not isinstance(log_meta, dict):
                    log_meta = {}
                    task["_log_meta"] = log_meta
                log_meta["completed_at"] = _utc_now_iso()
                log_meta["completed_by"] = _default_agent(by)
                _save_tasks_unlocked(tasks, resolved)
                result = dict(task)
                transitioned = True
                break
    if result is None:
        raise TaskNotFoundError(f"task id {task_id!r} not found in {resolved}")
    # Active-unblock DRIVE (ADR-0009) — OUTSIDE the file lock (the emit
    # re-loads the store + may comment on dependents, which take the
    # same lock). Only on a real pending→done transition.
    if transitioned:
        _emit_unblock_for_dependents(resolved, task_id, by=by)
    return result


def _emit_unblock_for_dependents(
    tasks_path: Path,
    completed_id: str,
    *,
    by: str | None = None,
    entry_points=None,
) -> list[str]:
    """Active-unblock DRIVE (ADR-0009).

    A card just flipped to ``done``. Find its DIRECT dependents whose
    last blocking dependency this completion just cleared — i.e. the
    dependents that are NOW runnable — and emit ONE ``unblock`` event
    naming them. A consumer (SAC) notifies each unblocked card's
    assignee + subscribers (*"your task is now unblocked"*).

    "Direct dependent" = a card D with ``completed_id`` in its
    ``depends_on``, OR a card D that ``completed_id`` lists in its
    ``blocks``. "Now runnable" reuses :func:`_runnable.runnable_tasks`
    verbatim (DRY — same dep-satisfied predicate the dispatcher uses):
    a direct dependent that is runnable now could not have been runnable
    before (``completed_id`` was an unresolved upstream), so its presence
    in the runnable set means *this* completion unblocked it.

    Returns the unblocked card ids (possibly empty). Best-effort: this is
    called AFTER the completion is durably saved, so any compute/bus
    error is caught + logged, never raised — feedback must not break the
    done transition it reports on.
    """
    try:
        from . import _hooks, _model, _runnable

        tasks = _model.load_tasks(tasks_path)
        unlocker = next(
            (t for t in tasks if isinstance(t, dict) and t.get("id") == completed_id),
            None,
        )
        downstream_via_blocks = set(unlocker.get("blocks") or ()) if unlocker else set()
        dependents = {
            t.get("id")
            for t in tasks
            if isinstance(t, dict)
            and t.get("id")
            and (
                completed_id in (t.get("depends_on") or ())
                or t.get("id") in downstream_via_blocks
            )
        }
        if not dependents:
            return []
        runnable_now = {t.get("id") for t in _runnable.runnable_tasks(tasks).tasks}
        unblocked = sorted(str(i) for i in (dependents & runnable_now) if i)
        if not unblocked:
            return []
        _hooks.dispatch_event(
            {
                "kind": "unblock",
                "unlocker_id": completed_id,
                "card_ids": unblocked,
                "author": _default_agent(by),
                "unblocked_at": _utc_now_iso(),
            },
            # Pass the SAME store so the built-in `_handle_unblock` writes
            # the `[unblocked]` comment to this store, not the default one.
            store=tasks_path,
            entry_points=entry_points,
        )
        return unblocked
    except Exception:  # noqa: BLE001 — unblock drive must not break `done`
        import logging

        logging.getLogger(__name__).warning(
            "unblock drive failed for completed card %r", completed_id, exc_info=True
        )
        return []


def list_tasks(
    store: str | Path | None = None,
    *,
    scope: str | None = None,
    assignee: str | None = None,
    status: str | None = None,
    # PR #66 additions per ADR-0008 D2 / D10:
    statuses: list[str] | None = None,
    agent: str | None = None,
    project: str | None = None,
    host: str | None = None,
    blocker: str | None = None,
    kind: str | None = None,
    id_prefix: str | None = None,
    blocking_me: bool = False,
    overdue: bool = False,
) -> list[dict]:
    """Snapshot the store, then filter by any combination of fields.

    Filter semantics:
    - ``scope=None`` (default): use ``$SCITEX_TODO_SCOPE`` if set, else
      no filter. ``scope=""`` opts out of the env default explicitly.
    - ``assignee`` / ``agent`` / ``project`` / ``host`` / ``status``:
      ``None`` = no filter; any string = exact match. (Generic Req 8 —
      no fuzzy / glob; callers compose.)
    - ``statuses`` (list) AND ``status`` (single) are OR-combined.
    - ``blocker="__none"`` matches rows with no blocker field; any other
      value is an exact match (closed-enum gating at the CLI layer).
    - ``kind="task"`` matches both explicit ``"task"`` AND absent rows
      (since absent ≡ ``"task"`` per ADR-0002).
    - ``id_prefix`` matches the front of ``id`` (cheap project-rollup
      lookup without exact id).
    - ``blocking_me=True`` is the board's BLOCKING-YOU predicate
      (``status == "blocked" AND blocker == "operator-decision"``);
      composes with the other filters via AND.

    The returned list contains fresh dicts, safe to mutate without
    affecting the on-disk store (no save here).
    """
    resolved = _resolved_store(store)
    tasks = load_tasks(resolved)
    scope_eff = _default_scope(scope)
    return [
        dict(t)
        for t in tasks
        if _match(
            t,
            scope=scope_eff,
            assignee=assignee,
            status=status,
            statuses=statuses,
            agent=agent,
            project=project,
            host=host,
            blocker=blocker,
            kind=kind,
            id_prefix=id_prefix,
            blocking_me=blocking_me,
            overdue=overdue,
        )
    ]


def summarize_tasks(
    store: str | Path | None = None,
    *,
    scope: str | None = None,
    assignee: str | None = None,
) -> dict:
    """Return numeric progress counts grouped by status, scope, assignee.

    Output shape (always present keys):

    ::

        {
          "store": "/abs/path/to/tasks.yaml",
          "total": int,
          "by_status": {<status>: int, ...},  # one key per VALID_STATUSES
          "by_scope": {<scope|"">: int, ...},
          "by_assignee": {<assignee|"">: int, ...},
        }

    Tasks with no scope / assignee bucket under the empty string ``""``.
    The ``by_status`` map is densified to all :data:`VALID_STATUSES` so
    consumers (web UI, progress widgets) don't have to special-case
    zero-count keys.
    """
    resolved = _resolved_store(store)
    tasks = load_tasks(resolved)
    scope_eff = _default_scope(scope)
    by_status: dict[str, int] = {s: 0 for s in VALID_STATUSES}
    by_scope: dict[str, int] = {}
    by_assignee: dict[str, int] = {}
    total = 0
    for task in tasks:
        if not _match(task, scope=scope_eff, assignee=assignee, status=None):
            continue
        total += 1
        st = task.get("status")
        if st in by_status:
            by_status[st] += 1
        sc = task.get("scope") or ""
        by_scope[sc] = by_scope.get(sc, 0) + 1
        asg = task.get("assignee") or ""
        by_assignee[asg] = by_assignee.get(asg, 0) + 1
    return {
        "store": str(resolved),
        "total": total,
        "by_status": by_status,
        "by_scope": by_scope,
        "by_assignee": by_assignee,
    }


def resolve_store(store: str | Path | None = None) -> dict:
    """Return the resolved task store path and the precedence chain.

    Mirrors the data the `scitex-todo resolve-store` CLI verb and the
    `resolve_store` MCP tool emit. Keeping a Python API by the same name
    as the MCP tool satisfies audit §6 (Convention A: tool_name == api_name).

    Output shape::

        {
          "resolved":         "/abs/path/to/tasks.yaml",
          "explicit":         <the `store` arg you passed, or None>,
          "env_tasks":        <value of $SCITEX_TODO_TASKS, or None>,
          "user_store":       "/abs/path/to/~/.scitex/todo/tasks.yaml",
          "bundled_example":  "/abs/path/to/bundled/example.yaml",
          "pkg_short":        "scitex_todo",
          "exists":           bool,
        }
    """
    import os

    from ._paths import (
        ENV_TASKS,
        PKG_SHORT,
        _user_root,
        bundled_example,
        resolve_tasks_path,
    )

    resolved = resolve_tasks_path(
        store if isinstance(store, (str, type(None))) else str(store)
    )
    return {
        "resolved": str(resolved),
        "explicit": str(store) if store is not None else None,
        "env_tasks": os.environ.get(ENV_TASKS),
        "user_store": str(_user_root() / "tasks.yaml"),
        "bundled_example": str(bundled_example()),
        "pkg_short": PKG_SHORT,
        "exists": Path(resolved).exists(),
    }


def get_task(
    store: str | Path | None = None,
    task_id: str | None = None,
) -> dict:
    """Return a single task by id, or raise ``TaskNotFoundError``.

    Companion to ``add_task`` / ``update_task`` / ``list_tasks`` — the
    natural "read one" verb every CRUD surface expects but the Python
    API was missing (PR #56 audit gap). The MCP wrapper exposes this as
    ``get_task`` per Convention A.
    """
    from . import _model

    tasks_path = _resolved_store(store)
    if not task_id:
        raise ValueError("get_task: 'task_id' is required")
    with _model._store_lock(tasks_path):
        tasks = _model.load_tasks(tasks_path)
        for t in tasks:
            if t.get("id") == task_id:
                return dict(t)
    raise TaskNotFoundError(f"task id {task_id!r} not found in {tasks_path}")


def delete_task(
    store: str | Path | None = None,
    task_id: str | None = None,
) -> dict:
    """Remove a task + scrub references to it. Returns the lossless
    payload the client can pass to ``restore_task`` for Undo.

    The board v3 Delete-with-Undo flow uses this via ``handlers/crud.py``;
    exposing the same operation here lets MCP agents do the same delete +
    later undo without round-tripping HTTP.

    Returns ``{"removed": <full task dict>, "refs": [<refs scrubbed>]}``
    where each ref is the id of another task whose depends_on / blocks /
    parent pointed at the deleted task (the client passes this back to
    restore_task to lossless-revert).
    """
    from . import _model

    tasks_path = _resolved_store(store)
    if not task_id:
        raise ValueError("delete_task: 'task_id' is required")
    with _model._store_lock(tasks_path):
        tasks = _model.load_tasks(tasks_path)
        target = None
        keep: list = []
        for t in tasks:
            if t.get("id") == task_id:
                target = dict(t)
            else:
                keep.append(t)
        if target is None:
            raise TaskNotFoundError(f"task id {task_id!r} not found in {tasks_path}")
        refs: list[str] = []
        for t in keep:
            mutated = False
            if isinstance(t.get("depends_on"), list) and task_id in t["depends_on"]:
                t["depends_on"] = [d for d in t["depends_on"] if d != task_id]
                if not t["depends_on"]:
                    t.pop("depends_on", None)
                mutated = True
            if isinstance(t.get("blocks"), list) and task_id in t["blocks"]:
                t["blocks"] = [b for b in t["blocks"] if b != task_id]
                if not t["blocks"]:
                    t.pop("blocks", None)
                mutated = True
            if t.get("parent") == task_id:
                t.pop("parent", None)
                mutated = True
            if mutated:
                refs.append(t.get("id"))
        _model._save_tasks_unlocked(keep, tasks_path)
    return {"removed": target, "refs": refs}


def restore_task(
    store: str | Path | None = None,
    task: dict | None = None,
    refs: list[str] | None = None,
) -> dict:
    """Undo a ``delete_task``: re-insert the task at its original id.

    Idempotent on duplicate id — raises ``ValueError`` if the id is
    already present (use ``update_task`` to mutate; this verb is the
    Delete-Undo partner only).
    """
    from . import _model

    tasks_path = _resolved_store(store)
    if not isinstance(task, dict) or not task.get("id"):
        raise ValueError("restore_task: 'task' must be a dict with 'id'")
    tid = task["id"]
    with _model._store_lock(tasks_path):
        tasks = _model.load_tasks(tasks_path)
        if any(t.get("id") == tid for t in tasks):
            raise ValueError(f"restore_task: id {tid!r} already present")
        tasks.append(dict(task))
        _model._save_tasks_unlocked(tasks, tasks_path)
    # refs are descriptive (the client passes them through so callers can
    # see which tasks had been mutated; we don't reverse-apply them since
    # the depends_on / blocks values were just stripped, not stored).
    return {"task": task, "refs": list(refs or [])}


def comment_task(
    store: str | Path | None = None,
    task_id: str | None = None,
    text: str | None = None,
    by: str | None = None,
    kind: str | None = None,
    entry_points=None,
) -> dict:
    """Append an entry to ``task.comments[]`` (the established Issue-
    activity-log shape from skill 30, Gitea-compatible field).

    `by` overrides the $SCITEX_TODO_AGENT → $USER precedence used by
    add_task / complete_task.

    `kind` is an optional feedback-ring / event tag (e.g. ``push`` /
    ``done`` / ``card-message``) stamped onto the entry so the board can
    render "how the card was routed" (operator 2026-06-17). Lenient: the
    model only requires ``text``, so the extra key round-trips cleanly.

    `entry_points` is forwarded to :func:`scitex_todo._hooks.dispatch_event`
    for the ``card-message`` bus emit below: an explicit iterable of
    entry-point-shaped objects to receive the event instead of the ones
    discovered from packaging metadata. ``None`` (the default) uses the
    real installed plugins. This is the in-process injection seam used by
    in-process consumers and by no-mock tests (PA-306-compliant) that
    observe the emitted event via a real fake handler.
    """
    from . import _model

    tasks_path = _resolved_store(store)
    if not task_id:
        raise ValueError("comment_task: 'task_id' is required")
    if not text or not str(text).strip():
        raise ValueError("comment_task: 'text' is required")
    author = _default_agent(by)
    entry = {
        "author": author,
        "ts": _utc_now_iso(),
        "text": str(text),
    }
    if kind:
        entry["kind"] = str(kind)
    with _model._store_lock(tasks_path):
        tasks = _model.load_tasks(tasks_path)
        target = None
        for t in tasks:
            if t.get("id") == task_id:
                target = t
                break
        if target is None:
            raise TaskNotFoundError(f"task id {task_id!r} not found in {tasks_path}")
        comments = target.setdefault("comments", [])
        # Pre-append snapshot of comment authors — forms the
        # `collaborators` list of the card-message event below.
        prior_authors = [
            c.get("author")
            for c in comments
            if isinstance(c, dict) and isinstance(c.get("author"), str)
        ]
        comments.append(entry)
        _model._save_tasks_unlocked(tasks, tasks_path)
        owner = target.get("agent") or target.get("assignee")
        # Persistent role lists (ADR-0009) — captured under the lock so
        # the bus emit below works off a consistent snapshot.
        persistent_collaborators = [
            c for c in (target.get("collaborators") or []) if isinstance(c, str) and c
        ]
        persistent_subscribers = [
            s for s in (target.get("subscribers") or []) if isinstance(s, str) and s
        ]

    # card-message bus emit (lead a2a `1e8e33d0`, 2026-06-14) — done
    # OUTSIDE the file lock so a slow bus handler can't extend the
    # lock-hold and starve other writers. Comment is already on disk;
    # bus errors are caught + logged so an external handler failure
    # (e.g. SAC unreachable) never bubbles up to the producer.
    try:
        from . import _hooks

        collaborators: list[str] = []
        seen: set[str] = set()
        if owner:
            seen.add(owner)
        seen.add(author)
        for a in list(prior_authors) + persistent_collaborators:
            if a and a not in seen:
                collaborators.append(a)
                seen.add(a)

        # Effective notify list (ADR-0009): the card's explicit
        # subscribers if any, else default to owner + collaborators.
        # P2's consumer fans the card-message to these. (Creator-auto-
        # subscribe is a later phase — needs an author param on add_task.)
        subscribers: list[str] = []
        sub_seen: set[str] = set()
        candidate_subs = persistent_subscribers or (
            ([owner] if owner else []) + collaborators
        )
        for s in candidate_subs:
            if s and s not in sub_seen:
                subscribers.append(s)
                sub_seen.add(s)

        _hooks.dispatch_event(
            {
                "kind": "card-message",
                "card_id": task_id,
                "author": author,
                "body": str(text),
                "owner": owner,
                "collaborators": collaborators,
                "subscribers": subscribers,
                "created_at": entry["ts"],
            },
            entry_points=entry_points,
        )
    except Exception:  # noqa: BLE001 — bus must not break comment_task
        import logging

        logging.getLogger(__name__).warning(
            "comment_task: card-message bus dispatch failed for %r",
            task_id,
            exc_info=True,
        )
    return {"task_id": task_id, "comment": entry}


def set_edge(
    store: str | Path | None = None,
    action: str | None = None,
    kind: str | None = None,
    source: str | None = None,
    target: str | None = None,
) -> dict:
    """Add or remove a depends_on / blocks edge.

    ``action`` in {"add", "remove"}. ``kind`` in {"depends_on", "blocks"}.
    Mutates ``tasks[source][kind]`` (adding/removing ``target``).
    """
    from . import _model

    if action not in ("add", "remove"):
        raise ValueError("set_edge: action must be 'add' or 'remove'")
    if kind not in ("depends_on", "blocks"):
        raise ValueError("set_edge: kind must be 'depends_on' or 'blocks'")
    if not source or not target:
        raise ValueError("set_edge: 'source' and 'target' are required")
    if source == target:
        raise ValueError("set_edge: self-edge is forbidden")
    tasks_path = _resolved_store(store)
    with _model._store_lock(tasks_path):
        tasks = _model.load_tasks(tasks_path)
        src_task = next((t for t in tasks if t.get("id") == source), None)
        tgt_task = next((t for t in tasks if t.get("id") == target), None)
        if src_task is None:
            raise TaskNotFoundError(f"set_edge: unknown source id {source!r}")
        if tgt_task is None:
            raise TaskNotFoundError(f"set_edge: unknown target id {target!r}")
        edges = src_task.get(kind) or []
        if action == "add" and target not in edges:
            edges = list(edges) + [target]
        elif action == "remove":
            edges = [e for e in edges if e != target]
        if edges:
            src_task[kind] = edges
        else:
            src_task.pop(kind, None)
        _model._save_tasks_unlocked(tasks, tasks_path)
    return {"action": action, "kind": kind, "source": source, "target": target}


def _set_list_member(
    tasks_path: Path,
    task_id: str,
    field: str,
    who: str,
    action: str,
) -> dict:
    """Idempotent add / remove of ``who`` in ``task[field]`` (a str list).

    Adds only if absent; removes every occurrence. Drops the key when the
    list becomes empty (same convention as :func:`set_edge` on edges, so
    the YAML stays sparse). Stamps ``last_activity``. Returns the task.
    """
    with _store_lock(tasks_path):
        tasks = load_tasks(tasks_path)
        for task in tasks:
            if task.get("id") == task_id:
                members = [m for m in (task.get(field) or []) if m != who]
                if action == "add":
                    members.append(who)
                if members:
                    task[field] = members
                else:
                    task.pop(field, None)
                task["last_activity"] = _utc_now_iso()
                _save_tasks_unlocked(tasks, tasks_path)
                return dict(task)
    raise TaskNotFoundError(f"task id {task_id!r} not found in {tasks_path}")


def set_collaborator(
    store: str | Path | None = None,
    *,
    task_id: str | None = None,
    who: str | None = None,
    action: str = "add",
) -> dict:
    """Add or remove ``who`` on a card's ``collaborators`` (ADR-0009).

    ``action`` in {"add", "remove"}. Adding a collaborator ALSO subscribes
    them (the ADR default — subscribers ⊇ collaborators), so they get
    feedback by default. Removing a collaborator leaves their subscription
    intact; call :func:`set_subscriber` with ``action="remove"`` to also
    stop their notices. Returns the (post-mutation) task mapping.
    """
    if not task_id or not who:
        raise ValueError("set_collaborator: 'task_id' and 'who' are required")
    if action not in ("add", "remove"):
        raise ValueError("set_collaborator: action must be 'add' or 'remove'")
    tasks_path = _resolved_store(store)
    task = _set_list_member(tasks_path, task_id, "collaborators", who, action)
    if action == "add":
        task = _set_list_member(tasks_path, task_id, "subscribers", who, "add")
    return task


def set_subscriber(
    store: str | Path | None = None,
    *,
    task_id: str | None = None,
    who: str | None = None,
    action: str = "add",
) -> dict:
    """Add or remove ``who`` on a card's ``subscribers`` — the notify list
    (ADR-0009).

    ``action`` in {"add", "remove"}. Anyone may unsubscribe — even a
    collaborator (the ADR's "always unsubscribable" rule): a ``remove``
    here drops them from the notify list without touching collaborators.
    Returns the (post-mutation) task mapping.
    """
    if not task_id or not who:
        raise ValueError("set_subscriber: 'task_id' and 'who' are required")
    if action not in ("add", "remove"):
        raise ValueError("set_subscriber: action must be 'add' or 'remove'")
    tasks_path = _resolved_store(store)
    return _set_list_member(tasks_path, task_id, "subscribers", who, action)


def resolve_task(
    store: str | Path | None = None,
    task_id: str | None = None,
    actor: str | None = None,
) -> dict:
    """Flip a task from ``status=blocked`` (typically ``blocker=operator-
    decision``) to ``done`` and clear the blocker. Appends an audit
    comment naming the actor.

    Idempotent on already-resolved tasks (re-resolves are no-ops, just
    log a "noop" comment).
    """
    from . import _model

    if not task_id:
        raise ValueError("resolve_task: 'task_id' is required")
    who = _default_agent(actor)
    tasks_path = _resolved_store(store)
    with _model._store_lock(tasks_path):
        tasks = _model.load_tasks(tasks_path)
        target = next((t for t in tasks if t.get("id") == task_id), None)
        if target is None:
            raise TaskNotFoundError(f"resolve_task: unknown id {task_id!r}")
        was_done = target.get("status") == "done"
        target["status"] = "done"
        target.pop("blocker", None)
        comments = target.setdefault("comments", [])
        comments.append(
            {
                "author": who,
                "ts": _utc_now_iso(),
                "text": (
                    "[resolve (noop — already done)]"
                    if was_done
                    else "[RESOLVED via mcp.resolve_task] flipped status='blocked'->done, blocker cleared."
                ),
            }
        )
        _model._save_tasks_unlocked(tasks, tasks_path)
    # Active-unblock DRIVE (ADR-0009) — resolving a blocker card to done
    # can free its dependents too. Outside the lock; skip the noop
    # (already-done) path. Handler token-dedupe keeps it idempotent.
    if not was_done:
        _emit_unblock_for_dependents(tasks_path, task_id, by=who)
    return {"task_id": task_id, "actor": who, "task": dict(target)}


def reopen_task(
    store: str | Path | None = None,
    task_id: str | None = None,
    by: str | None = None,
) -> dict:
    """Un-resolve a task — flip ``status=done`` back to ``blocked`` with
    ``blocker=operator-decision`` (the original LOUD halo state). Used
    by the board v3 Resolve→Undo loop.
    """
    from . import _model

    if not task_id:
        raise ValueError("reopen_task: 'task_id' is required")
    who = _default_agent(by)
    tasks_path = _resolved_store(store)
    with _model._store_lock(tasks_path):
        tasks = _model.load_tasks(tasks_path)
        target = next((t for t in tasks if t.get("id") == task_id), None)
        if target is None:
            raise TaskNotFoundError(f"reopen_task: unknown id {task_id!r}")
        target["status"] = "blocked"
        target["blocker"] = "operator-decision"
        comments = target.setdefault("comments", [])
        comments.append(
            {
                "author": who,
                "ts": _utc_now_iso(),
                "text": "[REOPENED via mcp.reopen_task] flipped status='done'->blocked, blocker=operator-decision restored.",
            }
        )
        _model._save_tasks_unlocked(tasks, tasks_path)
    return {"task_id": task_id, "by": who, "task": dict(target)}


__all__ = [
    "ENV_AGENT",
    "ENV_SCOPE",
    "TaskNotFoundError",
    "TaskValidationError",
    "add_task",
    "comment_task",
    "complete_task",
    "delete_task",
    "get_task",
    "list_tasks",
    "reopen_task",
    "resolve_store",
    "resolve_task",
    "restore_task",
    "set_collaborator",
    "set_edge",
    "set_subscriber",
    "summarize_tasks",
    "update_task",
]

# EOF
