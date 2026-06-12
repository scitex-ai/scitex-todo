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

import datetime
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
    """POST create -> append a new task. Body: ``{title, status?, ...}``.

    ``title`` is required; ``status`` defaults to ``pending``. The id is
    generated from the title (unique within the store). Returns the created
    task plus its ``store_path``.
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

    tasks = list(board.tasks)
    taken = {t["id"] for t in tasks}
    task = {
        "id": _slug_id(title.strip(), taken),
        "title": title.strip(),
        "status": payload.get("status") or "pending",
    }
    _apply_fields(task, {k: v for k, v in payload.items() if k != "status"})
    tasks.append(task)

    err = _save(tasks, board)
    if err:
        return err
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

    Body: ``{id, text, author?}``. The server stamps ``ts`` (ISO-8601 UTC)
    and defaults ``author`` to the supplied value, else ``$USER``, else
    ``"user"``. Append-only: it reads the task's current ``comments`` list and
    adds one entry, so concurrent comments from other agents are not clobbered
    the way a wholesale rewrite would. Unknown id -> 404. Returns the appended
    comment plus the task's new comment count.
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

    tasks = list(board.tasks)
    task = next((t for t in tasks if t["id"] == task_id), None)
    if task is None:
        return JsonResponse({"error": f"no task with id {task_id!r}"}, status=404)

    comment = {
        "ts": datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat(),
        "author": author.strip(),
        "text": text.strip(),
    }
    existing = task.get("comments")
    task["comments"] = ([*existing] if isinstance(existing, list) else []) + [comment]

    err = _save(tasks, board)
    if err:
        return err
    logger.info(
        "[scitex-todo] comment on %s by %s in %s",
        task_id,
        author,
        board.store_path,
    )

    # PR (g) comment-relay (lead a2a `9e710ab074ef4bf3a615be41793e0c51`,
    # operator TG12611 2026-06-12): when the comment author is NOT
    # the task's owning agent, push the full body to the owner via
    # the same wire the nudge button uses. Best-effort — relay failure
    # does NOT fail the comment write. Relay outcome surfaces in the
    # response so the UI can toast.
    relay = _maybe_relay_comment(task, comment)

    return JsonResponse(
        {
            "comment": comment,
            "count": len(task["comments"]),
            "store_path": str(board.store_path),
            "relay": relay,
        }
    )


def _maybe_relay_comment(task: dict, comment: dict) -> dict:
    """If ``comment.author != task.agent``, push the comment to the
    owning agent via the same wire the nudge button uses.

    Returns a dict the JSON response includes so the UI can render a
    toast: ``{"sent": bool, "wire": "sac"|"stdout"|"skip:<reason>",
    "target": "<agent>"}``.
    """
    target = (task.get("agent") or "").strip()
    author = (comment.get("author") or "").strip()
    if not target:
        return {"sent": False, "wire": "skip:no-agent", "target": ""}
    if author == target:
        return {"sent": False, "wire": "skip:self-comment", "target": target}

    body = (
        f"📝 comment on {task['id']} from {author!r}:\n\n"
        f"{comment.get('text', '')}\n\n"
        f"---\nReply via `scitex-todo comment {task['id']} "
        f"\"<your reply>\" --author {target}` (or MCP `add_comment` / "
        f"`comment_task`)."
    )

    from ..._push import deliver

    result = deliver(
        target, body,
        kind="comment-relay",
        task_id=task["id"],
    )
    logger.info(
        "[scitex-todo] comment relay %s → %s wire=%s reason=%s (ok=%s)",
        task["id"], target, result.get("wire"), result.get("reason"),
        result.get("ok"),
    )
    return {
        "sent": result.get("ok", False),
        "wire": result.get("wire"),
        "reason": result.get("reason"),
        "target": target,
    }



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
