#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CRUD handlers — create / update / delete a task in the YAML store.

These extend the first write path (``priority``) to full create/update/delete
driven by the board's right-click menu and editable detail drawer. Each
handler loads the current task list off ``board``, mutates it, then round-trips
the whole store via :func:`scitex_todo.save_tasks` (preserving hand-written
comments through the ruamel writer) and resets the in-process cache.

The store is shared by all agents, so every handler re-reads through ``board``
(freshly loaded per request by ``api_dispatch``) and writes the full list —
keeping the read-modify-write window as small as the request. The mutation
logic is intentionally thin so it can later sit on top of a richer shared
store API (PR #14) without changing the HTTP surface.

Field set mirrors the task model: ``id`` + ``title`` + ``status`` (required)
and optional ``repo`` / ``depends_on`` / ``blocks`` / ``note`` / ``priority``
/ ``parent``. ``save_tasks`` is the single validation gate, so a bad mutation
surfaces as a 400 rather than corrupting the store.
"""

from __future__ import annotations

import json
import logging
import os
import re

from django.http import JsonResponse

logger = logging.getLogger(__name__)

# Fields a client may set on create / patch on update. ``id`` is server-owned
# (generated on create, immutable on update) so it is deliberately excluded.
_EDITABLE_FIELDS = (
    "title",
    "status",
    "priority",
    "note",
    "repo",
    "parent",
    "depends_on",
    "blocks",
    # Operator-co-designed fields (Task dataclass, PR #56). Operator was
    # losing edits silently — `project` (and the rest) were not in this
    # tuple, so handle_update returned 200 with the field untouched, and
    # the card-drag (PR #77, TG 385) became a visual no-op (operator TG
    # 453 reproducer: dragged 学会 GTM card business → calendar, toast
    # said success, card stayed put). Whitelist now mirrors every
    # writable field on the Task dataclass.
    "project",
    "agent",
    "task",
    "host",
    "goal",
    "pr_url",
    "issue_url",
    "last_activity",
    "created_at",
    "scope",
    "assignee",
    "blocker",
    "kind",
    "job_id",
    "command",
    "started_at",
    "finished_at",
)


def _parse_body(request):
    """Decode a JSON object body, or return (None, error_response)."""
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, JsonResponse({"error": f"invalid JSON body: {exc}"}, status=400)
    if not isinstance(payload, dict):
        return None, JsonResponse({"error": "body must be a JSON object"}, status=400)
    return payload, None


def _slug_id(title: str, taken: set[str]) -> str:
    """Derive a stable, unique task id from a title.

    Lowercase, non-alphanumerics collapsed to single hyphens, trimmed to a
    sane length; a numeric suffix is appended on collision so two cards with
    the same title never share an id.
    """
    base = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48]
    base = base or "task"
    if base not in taken:
        return base
    n = 2
    while f"{base}-{n}" in taken:
        n += 1
    return f"{base}-{n}"


def _save(tasks, board):
    """Validate + persist, resetting the cache. Returns an error response or None."""
    from scitex_todo import TaskValidationError
    from scitex_todo._model import save_tasks

    from ..services import _reset_cache

    try:
        save_tasks(tasks, board.store_path)
    except TaskValidationError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    _reset_cache()
    return None


def _apply_fields(task: dict, payload: dict) -> None:
    """Copy editable fields from ``payload`` onto ``task`` in place.

    Only keys present in the payload are touched (PATCH semantics). An empty
    string / empty list / None clears an optional field by removing it, so the
    YAML stays free of empty scaffolding.
    """
    for key in _EDITABLE_FIELDS:
        if key not in payload:
            continue
        value = payload[key]
        if key in ("title", "status"):
            task[key] = value
        elif value in (None, "", []):
            task.pop(key, None)
        else:
            task[key] = value


def handle_create(request, board):
    """POST create -> append a new task. Body: ``{title, assignee, status?, ...}``.

    Operator constitution (no silent fallbacks): EVERY card has a creator AND an
    owner. This is the web sibling of :func:`scitex_todo._store.add_task`; it
    DELEGATES the write to ``add_task`` (instead of hand-building the dict — the
    old bug gave a UI card a BLANK creator + no required owner) so the UI path
    reuses the same fail-loud + ``agent==assignee`` lock-step + ``created_by``
    stamp. Owner (``assignee`` or ``agent``) REQUIRED -> 400 otherwise.
    ``created_by`` defaults payload -> ``$SCITEX_TODO_AGENT`` -> ``"operator"``
    (the board is the operator's surface), never blank. ``status`` defaults
    ``pending``; the id is unique across the union view (global + lanes).
    """
    if request.method != "POST":
        return JsonResponse({"error": "create endpoint requires POST"}, status=405)
    payload, err = _parse_body(request)
    if err:
        return err

    title = payload.get("title")
    if not isinstance(title, str) or not title.strip():
        return JsonResponse(
            {"error": "create requires a non-empty 'title'"}, status=400
        )

    # REQUIRE an owner up front so the rejection is a clean 400 the modal toasts
    # (not the 500 api_dispatch turns add_task's TaskValidationError into).
    # Accept either field the form may send; add_task locks agent==assignee.
    def _clean(v):
        return v.strip() if isinstance(v, str) else v

    owner = _clean(payload.get("assignee")) or _clean(payload.get("agent"))
    if not owner:
        return JsonResponse(
            {"error": "assignee is required — pick an owner"}, status=400
        )
    # Default the creator to the operator (the board's identity) when neither
    # payload nor env names one — never blank. add_task re-validates this.
    created_by = (
        _clean(payload.get("created_by"))
        or os.environ.get("SCITEX_TODO_AGENT")
        or "operator"
    )
    # Unique id across the union view (global + lanes) the board renders, so a
    # UI id never collides with a lane task though the write lands global.
    taken = {t["id"] for t in board.tasks if isinstance(t, dict) and t.get("id")}
    new_id = _slug_id(title.strip(), taken)
    # Forward the other editable form fields (project/note/...) as extras,
    # dropping empties (UI clear-on-empty semantics keep the YAML sparse).
    _handled = {"title", "status", "assignee", "agent", "created_by"}
    extra_fields = {
        k: payload[k]
        for k in _EDITABLE_FIELDS
        if k in payload and k not in _handled and payload[k] not in (None, "", [])
    }

    from scitex_todo import TaskValidationError
    from scitex_todo._store import add_task

    from ..services import _reset_cache

    try:
        task = add_task(
            board.store_path,
            id=new_id,
            title=title.strip(),
            status=payload.get("status") or "pending",
            assignee=owner,
            created_by=created_by,
            **extra_fields,
        )
    except TaskValidationError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    _reset_cache()
    logger.info("[scitex-todo] created task %s in %s", task["id"], board.store_path)
    return JsonResponse({"task": task, "store_path": str(board.store_path)})


def handle_update(request, board):
    """POST update -> patch an existing task. Body: ``{id, <fields...>}``.

    Only the provided editable fields change; ``id`` is immutable. Returns the
    updated task. Unknown id -> 404.
    """
    if request.method != "POST":
        return JsonResponse({"error": "update endpoint requires POST"}, status=405)
    payload, err = _parse_body(request)
    if err:
        return err

    task_id = payload.get("id")
    if not isinstance(task_id, str) or not task_id:
        return JsonResponse({"error": "update requires 'id'"}, status=400)

    tasks = list(board.tasks)
    task = next((t for t in tasks if t["id"] == task_id), None)
    if task is None:
        return JsonResponse({"error": f"no task with id {task_id!r}"}, status=404)

    _apply_fields(task, payload)

    err = _save(tasks, board)
    if err:
        return err
    logger.info("[scitex-todo] updated task %s in %s", task_id, board.store_path)
    return JsonResponse({"task": task, "store_path": str(board.store_path)})


def handle_delete(request, board):
    """POST delete -> remove a task and scrub references to it.

    Body: ``{id}``. The task is removed, and any other task's ``depends_on`` /
    ``blocks`` entry pointing at it is dropped, and any ``parent`` equal to it
    is cleared — so the store never keeps a dangling edge. Unknown id -> 404.
    """
    if request.method != "POST":
        return JsonResponse({"error": "delete endpoint requires POST"}, status=405)
    payload, err = _parse_body(request)
    if err:
        return err

    task_id = payload.get("id")
    if not isinstance(task_id, str) or not task_id:
        return JsonResponse({"error": "delete requires 'id'"}, status=400)

    tasks = list(board.tasks)
    removed = next((t for t in tasks if t["id"] == task_id), None)
    if removed is None:
        return JsonResponse({"error": f"no task with id {task_id!r}"}, status=404)

    # Record the references we scrub from OTHER tasks so an undo (restore) can
    # put them back exactly — making delete fully reversible.
    scrubbed_refs: list[dict] = []
    remaining = [t for t in tasks if t["id"] != task_id]
    for t in remaining:
        for edge in ("depends_on", "blocks"):
            refs = t.get(edge)
            if isinstance(refs, list) and task_id in refs:
                pruned = [r for r in refs if r != task_id]
                if pruned:
                    t[edge] = pruned
                else:
                    t.pop(edge, None)
                scrubbed_refs.append({"id": t["id"], "field": edge})
        if t.get("parent") == task_id:
            t.pop("parent", None)
            scrubbed_refs.append({"id": t["id"], "field": "parent"})

    err = _save(remaining, board)
    if err:
        return err
    logger.info("[scitex-todo] deleted task %s from %s", task_id, board.store_path)
    return JsonResponse(
        {
            "deleted": task_id,
            # `removed` (the full task dict) + `refs` let the frontend offer a
            # lossless Undo via the `restore` endpoint.
            "removed": dict(removed),
            "refs": scrubbed_refs,
            "store_path": str(board.store_path),
        }
    )


def handle_restore(request, board):
    """POST restore -> re-insert a previously deleted task (undo for delete).

    Body: ``{task: {<full task dict>}, refs: [{id, field}, ...]}``. Appends the
    task (unless its id already exists) and re-adds each scrubbed reference
    (``other[field]`` gains the task id back, or ``parent`` is restored).
    Validates via ``save_tasks``. Returns the restored id.
    """
    if request.method != "POST":
        return JsonResponse({"error": "restore endpoint requires POST"}, status=405)
    payload, err = _parse_body(request)
    if err:
        return err

    task = payload.get("task")
    if not isinstance(task, dict) or not task.get("id"):
        return JsonResponse(
            {"error": "restore requires a 'task' mapping with an id"},
            status=400,
        )
    tid = task["id"]
    tasks = list(board.tasks)
    by_id = {t["id"]: t for t in tasks}
    if tid not in by_id:
        tasks.append(dict(task))

    for ref in payload.get("refs") or []:
        if not isinstance(ref, dict):
            continue
        owner = by_id.get(ref.get("id"))
        field = ref.get("field")
        if owner is None:
            continue
        if field == "parent":
            owner["parent"] = tid
        elif field in ("depends_on", "blocks"):
            lst = owner.get(field)
            lst = list(lst) if isinstance(lst, list) else []
            if tid not in lst:
                lst.append(tid)
            owner[field] = lst

    err = _save(tasks, board)
    if err:
        return err
    logger.info("[scitex-todo] restored task %s in %s", tid, board.store_path)
    return JsonResponse({"restored": tid, "store_path": str(board.store_path)})


def handle_comment(request, board):
    """POST comment -> append a comment to a task's thread.

    Body: ``{id, text, author?}``. ``author`` defaults to the supplied value,
    else ``$USER``, else ``"user"``. Delegates the append to
    :func:`scitex_todo._store.comment_task` (the SSOT): append-only under the
    store lock — so concurrent comments from other agents are not clobbered —
    which ALSO emits the canonical ``commented`` card-event. The C4 dispatcher
    then ENQUEUES that comment into each resolved recipient's standalone
    PULL-inbox (the always-works rail; a containerized owner PULLs it via
    ``poll_notifications``) — there is NO direct turn-URL POST, which could
    never reach a containerized owner and slowed the write.

    Unknown id -> 404. Returns the appended comment, the new comment count,
    and a ``relay`` toast describing the INBOX QUEUE (the recipient names the
    comment was queued to) rather than a direct-POST result.
    """
    if request.method != "POST":
        return JsonResponse({"error": "comment endpoint requires POST"}, status=405)
    payload, err = _parse_body(request)
    if err:
        return err

    task_id = payload.get("id")
    if not isinstance(task_id, str) or not task_id:
        return JsonResponse({"error": "comment requires 'id'"}, status=400)

    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        return JsonResponse(
            {"error": "comment requires a non-empty 'text'"}, status=400
        )

    author = payload.get("author")
    if not isinstance(author, str) or not author.strip():
        author = os.environ.get("USER") or "user"
    author = author.strip()

    # 404 fast-path: an unknown id is a clean 404, not the TaskNotFoundError
    # comment_task would raise (which api_dispatch turns into a 500).
    if not any(t["id"] == task_id for t in board.tasks):
        return JsonResponse({"error": f"no task with id {task_id!r}"}, status=404)

    from scitex_todo._store import comment_task

    from ..services import _reset_cache
    from ._comment_relay import comment_inbox_toast

    # SSOT append + `commented` emit (→ C4 enqueues to each recipient's inbox).
    result = comment_task(
        store=board.store_path, task_id=task_id, text=text.strip(), by=author
    )
    _reset_cache()
    comment = result["comment"]
    logger.info(
        "[scitex-todo] comment on %s by %s in %s", task_id, author, board.store_path
    )

    # Re-read the freshly-written card (comment_task wrote under its own lock,
    # so board.tasks is stale) for the count + the inbox-queue toast.
    fresh = next((t for t in board.tasks if t["id"] == task_id), None) or {}
    comments = fresh.get("comments")
    count = len(comments) if isinstance(comments, list) else 1

    return JsonResponse(
        {
            "comment": comment,
            "count": count,
            "store_path": str(board.store_path),
            "relay": comment_inbox_toast(fresh, author),
        }
    )


def handle_edge(request, board):
    """POST edge -> add or remove a dependency edge between two tasks.

    Body: ``{action: "add"|"remove", kind: "depends_on"|"blocks", source, target}``
    where ``source``/``target`` use the graph-payload orientation:
      - ``depends_on``: edge points dependency(source) -> dependent(target), so
        the field lives on ``target`` as ``target.depends_on += [source]``.
      - ``blocks``: edge points blocker(source) -> blocked(target), so the
        field lives on ``source`` as ``source.blocks += [target]``.

    Add is idempotent; remove drops the reference. Both endpoints validate that
    the two ids exist (404 otherwise). Mirrors the lenient list-field handling
    used elsewhere (empty list -> key removed).
    """
    if request.method != "POST":
        return JsonResponse({"error": "edge endpoint requires POST"}, status=405)
    payload, err = _parse_body(request)
    if err:
        return err

    action = payload.get("action")
    if action not in ("add", "remove"):
        return JsonResponse(
            {"error": "edge 'action' must be 'add' or 'remove'"}, status=400
        )
    kind = payload.get("kind")
    if kind not in ("depends_on", "blocks"):
        return JsonResponse(
            {"error": "edge 'kind' must be 'depends_on' or 'blocks'"},
            status=400,
        )
    source = payload.get("source")
    target = payload.get("target")
    if not (isinstance(source, str) and source and isinstance(target, str) and target):
        return JsonResponse(
            {"error": "edge requires string 'source' and 'target'"}, status=400
        )
    if source == target:
        return JsonResponse({"error": "edge source and target must differ"}, status=400)

    tasks = list(board.tasks)
    by_id = {t["id"]: t for t in tasks}
    # The field-owning task depends on the edge orientation (see docstring).
    owner_id, other = (target, source) if kind == "depends_on" else (source, target)
    owner = by_id.get(owner_id)
    if owner is None:
        return JsonResponse({"error": f"no task with id {owner_id!r}"}, status=404)
    if other not in by_id:
        return JsonResponse({"error": f"no task with id {other!r}"}, status=404)

    refs = owner.get(kind)
    refs = list(refs) if isinstance(refs, list) else []
    if action == "add":
        if other not in refs:
            refs.append(other)
    else:
        refs = [r for r in refs if r != other]
    if refs:
        owner[kind] = refs
    else:
        owner.pop(kind, None)

    err = _save(tasks, board)
    if err:
        return err
    logger.info(
        "[scitex-todo] edge %s %s %s->%s in %s",
        action,
        kind,
        source,
        target,
        board.store_path,
    )
    return JsonResponse(
        {
            "action": action,
            "kind": kind,
            "source": source,
            "target": target,
            "store_path": str(board.store_path),
        }
    )


# EOF
