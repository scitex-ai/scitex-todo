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
    return JsonResponse(
        {
            "comment": comment,
            "count": len(task["comments"]),
            "store_path": str(board.store_path),
        }
    )


def handle_resolve(request, board):
    """POST resolve -> the BLOCKING YOU panel's resolve flow (ADR-0006 GUI→code).

    Body: ``{id, actor?}``. Flips the task to ``status=done, blocker=null``
    and appends a ``comments[]`` entry recording the resolution + actor.
    This is the load-bearing GUI→code loop the operator explicitly named
    (TG 9522 + 9667 + 9671 + 9674): clicking Resolve on a BLOCKING YOU
    row writes to ``tasks.yaml`` so the dependent agent's depends_on
    auto-unblocks within 5s via AutoRefresh (or via push when the SacChannel
    notification adapter lands in the live-data PR per ADR-0006).

    The notification-port publish step is in scope but uses PR #55's
    default ``InProcessPubSub`` adapter today; the fleet adapter
    (SacChannelNotificationAdapter routing through the wake-generalize
    bus) drops in via dependency injection later.

    Unknown id -> 404. Already-resolved (status=done) -> 200 idempotent
    no-op so a double-click doesn't double-publish.
    """
    if request.method != "POST":
        return JsonResponse({"error": "resolve endpoint requires POST"}, status=405)
    payload, err = _parse_body(request)
    if err:
        return err

    task_id = payload.get("id")
    if not isinstance(task_id, str) or not task_id:
        return JsonResponse({"error": "resolve requires 'id'"}, status=400)

    actor = payload.get("actor")
    if not isinstance(actor, str) or not actor.strip():
        actor = os.environ.get("USER") or "operator"

    tasks = list(board.tasks)
    task = next((t for t in tasks if t["id"] == task_id), None)
    if task is None:
        return JsonResponse({"error": f"no task with id {task_id!r}"}, status=404)

    # Idempotent: already-done = no-op success (a double-click on the FE
    # button shouldn't double-publish or fail-noisily).
    if task.get("status") == "done":
        return JsonResponse(
            {
                "id": task_id,
                "status": "done",
                "noop": True,
                "store_path": str(board.store_path),
            }
        )

    # Capture pre-resolve state for the comment trail.
    prior_status = task.get("status")
    prior_blocker = task.get("blocker")

    task["status"] = "done"
    # Pop rather than set-None: the schema validator rejects blocker on
    # non-blocked rows, so leaving blocker present after status=done would
    # round-trip-fail the next load.
    task.pop("blocker", None)

    resolve_comment = {
        "ts": datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat(),
        "author": actor.strip(),
        "text": (
            f"[RESOLVED via board-v3] flipped status={prior_status!r}→done"
            + (
                f", blocker={prior_blocker!r}→null"
                if prior_blocker
                else ""
            )
            + ". Owning agent will pick up via AutoRefresh / NotificationPort.publish."
        ),
    }
    existing = task.get("comments")
    task["comments"] = (
        [*existing] if isinstance(existing, list) else []
    ) + [resolve_comment]

    err = _save(tasks, board)
    if err:
        return err

    # Notification publish — uses scitex_todo._adapters.InProcessPubSub default
    # from PR #55. The fleet wires SacChannelNotificationAdapter via
    # constructor injection in the create_board factory (deferred — ADR-0007
    # follow-up #5). Today the publish is a no-op-for-other-processes; the
    # YAML write is the durable path agents pick up via 5s AutoRefresh.
    try:
        from scitex_todo._adapters import InProcessPubSub

        # Local in-process bus — singleton per process. Other agents on
        # this process (if any) get the event; cross-process gets it via
        # the YAML write + AutoRefresh until SacChannel adapter lands.
        _BUS = getattr(handle_resolve, "_BUS", None)
        if _BUS is None:
            _BUS = InProcessPubSub()
            handle_resolve._BUS = _BUS  # type: ignore[attr-defined]
        channel = f"scitex-todo:task:{task.get('project', 'unknown')}/{task_id}"
        _BUS.publish(channel, {
            "task_id": task_id,
            "changes": {"status": "done", "blocker": None},
            "ts": resolve_comment["ts"],
            "actor": actor.strip(),
        })
    except Exception:  # noqa: BLE001 — publish-failure is non-fatal
        logger.exception("[scitex-todo] resolve notify-publish failed (non-fatal)")

    logger.info(
        "[scitex-todo] RESOLVED %s by %s in %s",
        task_id,
        actor,
        board.store_path,
    )
    return JsonResponse(
        {
            "id": task_id,
            "status": "done",
            "prior_status": prior_status,
            "prior_blocker": prior_blocker,
            "comment": resolve_comment,
            "store_path": str(board.store_path),
        }
    )


def handle_reopen(request, board):
    """POST reopen -> undo a prior resolve (board-v3 Undo affordance).

    Body: ``{id, prior_status?, prior_blocker?, actor?}``. Reverses a previous
    ``/resolve``: restores ``status`` to ``prior_status`` (default ``"blocked"``)
    and ``blocker`` to ``prior_blocker`` (default ``"operator-decision"``).
    The schema validator rejects ``blocker`` on non-blocked rows, so when the
    restored status is anything other than ``"blocked"`` the field is dropped.
    Appends a ``[UNDONE via board-v3]`` comment and publishes a notification.
    Unknown id -> 404.

    This is the load-bearing safety net for operator pain TG 9763 (a UI test
    click made the clew card vanish destructively, with no way back). Pairs
    with the v3 board's 2-click confirm + Undo-toast so Resolve stops being
    a trap. Lossless today because notification fan-out is in-process — the
    dependent agent has not been told yet via SacChannel.
    """
    if request.method != "POST":
        return JsonResponse({"error": "reopen endpoint requires POST"}, status=405)
    payload, err = _parse_body(request)
    if err:
        return err

    task_id = payload.get("id")
    if not isinstance(task_id, str) or not task_id:
        return JsonResponse({"error": "reopen requires 'id'"}, status=400)

    actor = payload.get("actor")
    if not isinstance(actor, str) or not actor.strip():
        actor = os.environ.get("USER") or "operator"

    new_status = payload.get("prior_status") or "blocked"
    new_blocker = payload.get("prior_blocker")
    # Default blocker only when restoring to blocked AND caller didn't supply one.
    if new_status == "blocked" and not new_blocker:
        new_blocker = "operator-decision"

    tasks = list(board.tasks)
    task = next((t for t in tasks if t["id"] == task_id), None)
    if task is None:
        return JsonResponse({"error": f"no task with id {task_id!r}"}, status=404)

    pre_status = task.get("status")
    pre_blocker = task.get("blocker")

    task["status"] = new_status
    if new_status == "blocked" and new_blocker:
        task["blocker"] = new_blocker
    else:
        # Schema disallows blocker on non-blocked rows.
        task.pop("blocker", None)

    reopen_comment = {
        "ts": datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat(),
        "author": actor.strip(),
        "text": (
            f"[UNDONE via board-v3] re-opened by {actor.strip()}; "
            f"status={pre_status!r}→{new_status!r}"
            + (
                f", blocker={pre_blocker!r}→{new_blocker!r}"
                if (pre_blocker or new_blocker)
                else ""
            )
            + ". Reverses the prior /resolve. Lossless: SacChannel wake "
            "adapter not wired yet, dependent agent was never notified."
        ),
    }
    existing = task.get("comments")
    task["comments"] = (
        [*existing] if isinstance(existing, list) else []
    ) + [reopen_comment]

    err = _save(tasks, board)
    if err:
        return err

    # Mirror the resolve publish path so any in-process subscriber gets the
    # reopen as a first-class change event.
    try:
        from scitex_todo._adapters import InProcessPubSub

        _BUS = getattr(handle_reopen, "_BUS", None)
        if _BUS is None:
            _BUS = InProcessPubSub()
            handle_reopen._BUS = _BUS  # type: ignore[attr-defined]
        channel = f"scitex-todo:task:{task.get('project', 'unknown')}/{task_id}"
        _BUS.publish(channel, {
            "task_id": task_id,
            "changes": {"status": new_status, "blocker": new_blocker},
            "ts": reopen_comment["ts"],
            "actor": actor.strip(),
            "undo_of": "resolve",
        })
    except Exception:  # noqa: BLE001 — publish-failure is non-fatal
        logger.exception("[scitex-todo] reopen notify-publish failed (non-fatal)")

    logger.info(
        "[scitex-todo] REOPENED %s by %s in %s (status %r->%r)",
        task_id,
        actor,
        board.store_path,
        pre_status,
        new_status,
    )
    return JsonResponse(
        {
            "id": task_id,
            "status": new_status,
            "blocker": new_blocker,
            "prior_status": pre_status,
            "prior_blocker": pre_blocker,
            "comment": reopen_comment,
            "store_path": str(board.store_path),
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
