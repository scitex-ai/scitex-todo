#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Hook-consumer entry-point contract for the scitex-todo board.

Lead a2a `6fff33d6` + `fbffb879`, 2026-06-14 — operator-mandated. Loose
coupling between scitex-todo (the board / SSoT) and event PRODUCERS
(SAC's push-hook, dev's merge-Action, future federated producers).
The board never knows what produces an event; it just accepts the
canonical wire shape and records it.

## Entry-point group

    scitex_todo.hooks

External producers register a Python callable as a plugin under this
group. Each plugin is invoked for every event with the canonical
event dict. Example consumer-side wire (in another package's
`pyproject.toml`):

.. code-block:: toml

   [project.entry-points."scitex_todo.hooks"]
   my-notifier = "my_pkg.hooks:on_event"

The callable signature is::

    def on_event(event: dict) -> None: ...

Plugin failures are caught + logged but never propagate — one bad
plugin must NOT silently break the board's own record-keeping.

## Handler ordering + criticality (dev coordination 2026-06-14)

Handlers MAY declare two optional function attributes to influence
dispatch:

.. code-block:: python

    def on_event(event: dict) -> None:
        ...

    on_event.priority = 10     # int, default 100. LOWER = runs FIRST.
    on_event.critical = True   # bool, default False. If True and the
                               # handler RAISES, the dispatcher aborts
                               # the chain and re-raises so the
                               # producer (HTTP/CLI) sees a 500 /
                               # non-zero exit.

Sort order is ``(priority asc, entry-point-name asc)`` — stable
across packaging-metadata implementations.

The `ci-result` event chain uses both attributes: dev's owner-map
handler (priority=10, critical=True) mutates ``event["owner"]``
BEFORE SAC's a2a-delivery handler (priority=200) reads it.
Delivering a verdict to a wrong-or-no agent is worse than no
delivery, hence the critical attribute on the owner-map.

The event dict is passed BY REFERENCE through every handler — early
handlers may mutate it for later handlers to consume.

## Canonical event kinds

Two event kinds drive the inbound-write contract today. New kinds
can be added later by extending the dispatcher; the wire shape is
forward-compatible (unknown keys are ignored by `event_validate`).

### `push` event

A producer (typically SAC's push-hook) saw a git push. Payload::

    {
        "kind": "push",
        "repo": "owner/repo",            # required, non-empty
        "branch": "develop",             # required, non-empty
        "commit_sha": "abc123def...",    # required, non-empty
        "author": "agent-name",          # optional
        "message": "commit message",     # optional
        "card_ids": ["card-id-1", ...]   # optional — board cards to
                                         # annotate; if absent, the
                                         # built-in handler is a noop
                                         # at the card level (plugins
                                         # may still react)
    }

Built-in handler: for each ``card_id``, append a comment to the card
with the commit_sha + message. **Idempotent**: a comment whose text
ALREADY contains the commit_sha is not re-appended.

### `done` event

A producer (typically dev's merge-Action via GitHub) saw a PR merge.
Payload::

    {
        "kind": "done",
        "repo": "owner/repo",            # required, non-empty
        "pr_number": 158,                # required, int
        "pr_url": "https://.../pull/158",# required, non-empty
        "author": "merger",              # optional
        "merged_at": "ISO-8601 UTC",     # optional
        "card_ids": ["card-id-1", ...]   # optional
    }

Built-in handler: for each ``card_id``, set ``pr_url`` + flip
``status`` to ``done``. **Idempotent**: re-call with the same
``pr_url`` on an already-done card with the same pr_url is a noop.

## Wire surface (loose-coupling contract)

Producers can call the consumer via THREE equivalent surfaces — pick
whichever is closest to the producer's process model:

1. **HTTP** — ``POST /hooks/push`` / ``POST /hooks/done`` with the
   payload as JSON body. Used by SAC's push-hook + dev's GitHub
   Action.
2. **CLI** — ``scitex-todo hook push --json <FILE_OR_STDIN>`` and
   ``scitex-todo hook done --json <FILE_OR_STDIN>``. Used by shell
   scripts that already have shell access.
3. **Python** — ``from scitex_todo._hooks import dispatch_event;
   dispatch_event({"kind": "push", ...})``. Used by in-process
   producers.

All three converge on :func:`dispatch_event`, which runs the
built-in handler + every entry-point plugin in turn.
"""

from __future__ import annotations

import importlib.metadata
import logging
from typing import Any, Callable, Iterable

from . import _store

logger = logging.getLogger(__name__)


#: Entry-point group external producers register their plugins under.
ENTRY_POINT_GROUP = "scitex_todo.hooks"

#: Accepted event kinds. Producers that emit any other ``kind`` are
#: rejected at the validate step — fail-loud per Phase-0 doctrine.
VALID_EVENT_KINDS = frozenset({"push", "done"})


class HookEventError(ValueError):
    """A producer sent a malformed event payload.

    Raised by :func:`event_validate` on shape violations. The HTTP +
    CLI wrappers translate this to a 400 / non-zero exit.
    """


def event_validate(event: Any) -> dict:
    """Fail-loud validation of an inbound event payload.

    Returns the normalized event dict on success (string-coerces the
    ``card_ids`` entries, defaults missing optional fields to None).
    Raises :class:`HookEventError` on any structural violation.
    """
    if not isinstance(event, dict):
        raise HookEventError(
            f"event must be a JSON object, got {type(event).__name__}"
        )
    kind = event.get("kind")
    if kind not in VALID_EVENT_KINDS:
        raise HookEventError(
            f"unknown event kind {kind!r}; must be one of "
            f"{sorted(VALID_EVENT_KINDS)}"
        )

    def _require(field: str) -> str:
        val = event.get(field)
        if not isinstance(val, str) or not val:
            raise HookEventError(
                f"{kind} event: {field!r} must be a non-empty string "
                f"(got {val!r})"
            )
        return val

    out: dict[str, Any] = {"kind": kind}
    if kind == "push":
        out["repo"] = _require("repo")
        out["branch"] = _require("branch")
        out["commit_sha"] = _require("commit_sha")
        out["author"] = event.get("author")
        out["message"] = event.get("message")
    elif kind == "done":
        out["repo"] = _require("repo")
        pr_number = event.get("pr_number")
        if not isinstance(pr_number, int):
            raise HookEventError(
                f"done event: 'pr_number' must be an int (got {pr_number!r})"
            )
        out["pr_number"] = pr_number
        out["pr_url"] = _require("pr_url")
        out["author"] = event.get("author")
        out["merged_at"] = event.get("merged_at")
    # card_ids — optional in both kinds; coerce to list[str] of non-
    # empty strings. Anything else is malformed.
    card_ids = event.get("card_ids") or []
    if not isinstance(card_ids, list):
        raise HookEventError(
            f"{kind} event: 'card_ids' must be a list (got "
            f"{type(card_ids).__name__})"
        )
    norm_cards: list[str] = []
    for c in card_ids:
        if not isinstance(c, str) or not c:
            raise HookEventError(
                f"{kind} event: 'card_ids' entry {c!r} is not a "
                f"non-empty string"
            )
        norm_cards.append(c)
    out["card_ids"] = norm_cards
    return out


def dispatch_event(event: dict, *, store: Any | None = None) -> dict:
    """Run the built-in handler + every entry-point plugin for ``event``.

    Returns a summary dict::

        {
            "kind": <event kind>,
            "card_writes": [
                {"card_id", "action": "comment-appended"|"already-recorded"|"completed"|"noop"},
                ...
            ],
            "plugin_count": <N>,
            "plugin_errors": [
                {"plugin": <name>, "error": <str>},
                ...
            ]
        }

    The return shape is the HTTP / CLI response body.

    Parameters
    ----------
    event : dict
        Already-validated event (call :func:`event_validate` first).
    store : Path-like, optional
        Override the task-store path; ``None`` resolves via the
        normal precedence chain.
    """
    kind = event["kind"]
    card_writes: list[dict] = []
    if kind == "push":
        card_writes = _handle_push(event, store=store)
    elif kind == "done":
        card_writes = _handle_done(event, store=store)

    plugin_count, plugin_errors = _run_plugins(event)
    return {
        "kind": kind,
        "card_writes": card_writes,
        "plugin_count": plugin_count,
        "plugin_errors": plugin_errors,
    }


def _handle_push(event: dict, *, store: Any | None) -> list[dict]:
    """Built-in `push` handler — idempotently append a comment per card."""
    out: list[dict] = []
    commit_sha = event["commit_sha"]
    msg = event.get("message") or ""
    author = event.get("author") or "<unknown>"
    repo = event["repo"]
    # Include the FULL commit_sha as a stable token (NOT just the
    # short prefix) so the idempotency check below can find it via
    # substring match. The short prefix is for human readability;
    # the full sha is the dedupe key.
    text = (
        f"[push] {repo} @ {commit_sha[:10]}: {msg} "
        f"[sha={commit_sha}]"
    ).strip()
    for card_id in event["card_ids"]:
        # Idempotency: if any existing comment text mentions this
        # commit_sha, the push has already been recorded — noop.
        if _push_already_recorded(card_id, commit_sha, store=store):
            out.append({"card_id": card_id, "action": "already-recorded"})
            continue
        try:
            _store.comment_task(store=store, task_id=card_id, text=text, by=author)
            out.append({"card_id": card_id, "action": "comment-appended"})
        except _store.TaskNotFoundError:
            # An unknown card id is NOT a producer error (the producer
            # just hinted at a card the operator hasn't created yet);
            # we record a soft noop so the producer can spot it.
            out.append({"card_id": card_id, "action": "card-not-found"})
    return out


def _handle_done(event: dict, *, store: Any | None) -> list[dict]:
    """Built-in `done` handler — idempotent done + pr_url stamp per card."""
    out: list[dict] = []
    pr_url = event["pr_url"]
    by = event.get("author") or "<unknown>"
    for card_id in event["card_ids"]:
        try:
            existing = _store.get_task(store=store, task_id=card_id)
        except (AttributeError, _store.TaskNotFoundError):
            existing = None
        if existing is None:
            out.append({"card_id": card_id, "action": "card-not-found"})
            continue
        # Idempotency: already done AND already carrying this pr_url
        # → noop. Other states are flipped through (handle "race" with
        # operator-manual done that didn't carry pr_url).
        if existing.get("status") == "done" and existing.get("pr_url") == pr_url:
            out.append({"card_id": card_id, "action": "noop"})
            continue
        try:
            _store.update_task(store=store, task_id=card_id, pr_url=pr_url)
            _store.complete_task(store=store, task_id=card_id, by=by)
            out.append({"card_id": card_id, "action": "completed"})
        except _store.TaskNotFoundError:
            out.append({"card_id": card_id, "action": "card-not-found"})
    return out


def _push_already_recorded(
    card_id: str, commit_sha: str, *, store: Any | None,
) -> bool:
    try:
        existing = _store.get_task(store=store, task_id=card_id)
    except (AttributeError, _store.TaskNotFoundError):
        return False
    if existing is None:
        return False
    for c in existing.get("comments") or ():
        if not isinstance(c, dict):
            continue
        text = c.get("text") or ""
        if commit_sha in text:
            return True
    return False


def _run_plugins(event: dict) -> tuple[int, list[dict]]:
    """Discover + invoke every plugin registered under
    :data:`ENTRY_POINT_GROUP`. Failures are caught + logged."""
    plugin_errors: list[dict] = []
    # Materialize the entry-point list FIRST so we can sort by the
    # handler's declared (priority, name) before dispatch. Lead a2a
    # `0ab1d9fd` + dev coordination 2026-06-14 — the ci-result event
    # chain needs dev's owner-map handler (priority=10) to mutate
    # event["owner"] BEFORE SAC's delivery handler (priority=200)
    # reads it.
    #
    # Handlers declare ordering via two OPTIONAL function attributes:
    #
    #   on_event.priority = <int>     # default 100; lower = runs earlier
    #   on_event.critical = True      # default False; if True and the
    #                                 # handler raises, ABORT the chain
    #                                 # and re-raise (the producer's
    #                                 # HTTP/CLI wrapper translates to
    #                                 # 500 / non-zero exit). For
    #                                 # ci-result the owner-map MUST be
    #                                 # critical — delivering a verdict
    #                                 # to a wrong-or-no agent is worse
    #                                 # than no delivery.
    #
    # Tie-break is the entry-point name (lex asc) so the order is
    # stable across packaging-metadata implementations.
    handlers: list[tuple[int, str, Callable[[dict], None]]] = []
    for ep in _iter_entry_points():
        name = ep.name
        try:
            fn: Callable[[dict], None] = ep.load()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "scitex_todo.hooks plugin %r failed to load: %s", name, exc,
            )
            plugin_errors.append({"plugin": name, "error": f"load: {exc}"})
            continue
        prio = int(getattr(fn, "priority", 100))
        handlers.append((prio, name, fn))
    handlers.sort(key=lambda triple: (triple[0], triple[1]))
    count = len(handlers)
    for prio, name, fn in handlers:
        critical = bool(getattr(fn, "critical", False))
        try:
            fn(event)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "scitex_todo.hooks plugin %r (priority=%d critical=%s) "
                "raised: %s", name, prio, critical, exc,
            )
            plugin_errors.append({
                "plugin": name, "priority": prio,
                "critical": critical, "error": str(exc),
            })
            if critical:
                # Abort the chain — downstream handlers don't run.
                raise
    return count, plugin_errors


def _iter_entry_points() -> Iterable:
    """importlib.metadata API varies across Python versions. Wrap the
    safest cross-version surface."""
    try:
        eps = importlib.metadata.entry_points()
    except Exception:  # noqa: BLE001 — packaging surprises
        return []
    # 3.10+: eps is an EntryPoints, supports .select(group=)
    select = getattr(eps, "select", None)
    if callable(select):
        return select(group=ENTRY_POINT_GROUP)
    # 3.9 fallback: dict-like keyed by group.
    return eps.get(ENTRY_POINT_GROUP, [])


# EOF
