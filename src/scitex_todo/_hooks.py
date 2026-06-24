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
#:
#:   - ``push``         git push event (SAC's push-hook).
#:   - ``done``         PR merge event (dev's GitHub Action).
#:   - ``card-message`` operator/agent comment on a card. Emitted
#:                      automatically by :func:`scitex_todo._store.comment_task`
#:                      so any comment landing — via the chat panel,
#:                      the ``scitex-todo comment`` CLI verb, or the
#:                      MCP ``comment_task`` tool — fans out through
#:                      the bus. SAC's consumer a2a-delivers to the
#:                      card's owner + collaborators (lead a2a
#:                      ``1e8e33d0``, 2026-06-14).
#:   - ``unblock``      a card that others ``depends_on`` flipped to
#:                      ``done``, so its dependents are now runnable.
#:                      Emitted by :func:`scitex_todo._store.complete_task`
#:                      (the active-unblock DRIVE, ADR-0009). Carries the
#:                      ``unlocker_id`` (the finished card) and ``card_ids``
#:                      (the newly-unblocked dependents). The built-in
#:                      handler records a ``[unblocked]`` ROUTE comment on
#:                      each; SAC's consumer notifies their assignee +
#:                      subscribers ("your task is now unblocked").
VALID_EVENT_KINDS = frozenset({"push", "done", "card-message", "unblock"})


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
        raise HookEventError(f"event must be a JSON object, got {type(event).__name__}")
    kind = event.get("kind")
    if kind not in VALID_EVENT_KINDS:
        raise HookEventError(
            f"unknown event kind {kind!r}; must be one of {sorted(VALID_EVENT_KINDS)}"
        )

    def _require(field: str) -> str:
        val = event.get(field)
        if not isinstance(val, str) or not val:
            raise HookEventError(
                f"{kind} event: {field!r} must be a non-empty string (got {val!r})"
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
    elif kind == "card-message":
        # The card-id of the card the comment landed on. Required.
        out["card_id"] = _require("card_id")
        # Required body — the comment text itself. Validator pins
        # non-empty so a producer can't fan an empty notification.
        out["body"] = _require("body")
        out["author"] = event.get("author")  # optional but ~always set
        # Owner = the agent the card is assigned to. Nullable when the
        # card has no agent/assignee field — SAC's handler can still
        # fan to collaborators in that case.
        out["owner"] = event.get("owner")
        # Collaborators = everyone else SAC should fan to. Coerce to
        # list[str] of non-empty strings; empty list is valid.
        collaborators = event.get("collaborators") or []
        if not isinstance(collaborators, list):
            raise HookEventError(
                f"card-message event: 'collaborators' must be a list "
                f"(got {type(collaborators).__name__})"
            )
        norm_collab: list[str] = []
        for c in collaborators:
            if not isinstance(c, str) or not c:
                raise HookEventError(
                    f"card-message event: 'collaborators' entry "
                    f"{c!r} is not a non-empty string"
                )
            norm_collab.append(c)
        out["collaborators"] = norm_collab
        # subscribers — optional notify list (ADR-0009). Same shape as
        # collaborators; empty/absent is valid (the consumer falls back
        # to owner + collaborators).
        subscribers = event.get("subscribers") or []
        if not isinstance(subscribers, list):
            raise HookEventError(
                f"card-message event: 'subscribers' must be a list "
                f"(got {type(subscribers).__name__})"
            )
        norm_subs: list[str] = []
        for s in subscribers:
            if not isinstance(s, str) or not s:
                raise HookEventError(
                    f"card-message event: 'subscribers' entry "
                    f"{s!r} is not a non-empty string"
                )
            norm_subs.append(s)
        out["subscribers"] = norm_subs
        out["created_at"] = event.get("created_at")
        # `card-message` does NOT use `card_ids` (singular `card_id`
        # above); return early so the trailing card_ids normalisation
        # block doesn't add an empty list to the payload.
        return out
    elif kind == "unblock":
        # The card that just flipped to done (the "unlocker"). Required
        # so consumers can say *who* unblocked the dependents.
        out["unlocker_id"] = _require("unlocker_id")
        out["author"] = event.get("author")
        out["unblocked_at"] = event.get("unblocked_at")
        # `card_ids` here = the newly-unblocked dependents; normalised
        # by the shared block below (NOT returned early).
    # card_ids — optional for push/done/unblock; coerce to list[str] of
    # non-empty strings. Anything else is malformed.
    card_ids = event.get("card_ids") or []
    if not isinstance(card_ids, list):
        raise HookEventError(
            f"{kind} event: 'card_ids' must be a list (got {type(card_ids).__name__})"
        )
    norm_cards: list[str] = []
    for c in card_ids:
        if not isinstance(c, str) or not c:
            raise HookEventError(
                f"{kind} event: 'card_ids' entry {c!r} is not a non-empty string"
            )
        norm_cards.append(c)
    out["card_ids"] = norm_cards
    return out


def dispatch_event(
    event: dict,
    *,
    store: Any | None = None,
    entry_points: Iterable | None = None,
) -> dict:
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
    entry_points : iterable, optional
        Explicit set of plugin entry points to run instead of the ones
        discovered from packaging metadata. Each item must be entry-
        point-shaped: a ``.name`` attribute and a ``.load()`` method
        returning the handler callable. ``None`` (the default) reads the
        real :data:`ENTRY_POINT_GROUP` group via :func:`_iter_entry_points`.
        This is the in-process injection seam (mirrors scitex-dev's
        ``load_plugins(entry_points_iter=...)``): in-process producers
        that can't ship packaging metadata, and tests that need a real
        fake handler, pass a concrete list here — no monkeypatch of
        ``importlib.metadata`` required (PA-306-compliant).
    """
    kind = event["kind"]
    card_writes: list[dict] = []
    if kind == "push":
        card_writes = _handle_push(event, store=store)
    elif kind == "done":
        card_writes = _handle_done(event, store=store)
    elif kind == "unblock":
        card_writes = _handle_unblock(event, store=store)

    plugin_count, plugin_errors = _run_plugins(event, entry_points=entry_points)
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
    text = (f"[push] {repo} @ {commit_sha[:10]}: {msg} [sha={commit_sha}]").strip()
    for card_id in event["card_ids"]:
        # Idempotency: if any existing comment text mentions this
        # commit_sha, the push has already been recorded — noop.
        if _push_already_recorded(card_id, commit_sha, store=store):
            out.append({"card_id": card_id, "action": "already-recorded"})
            continue
        try:
            _store.comment_task(
                store=store, task_id=card_id, text=text, by=author, kind="push"
            )
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


def _handle_unblock(event: dict, *, store: Any | None) -> list[dict]:
    """Built-in `unblock` handler — idempotent `[unblocked]` comment per card.

    Records on each newly-runnable dependent that ``unlocker_id`` (the
    card that just finished) cleared its last blocking dependency. The
    actual *notification* of the dependent's assignee + subscribers is a
    consumer concern (SAC's plugin); this only writes the durable trail
    so the board shows why a card became runnable even with no consumer.
    """
    out: list[dict] = []
    unlocker_id = event["unlocker_id"]
    by = event.get("author") or "<unknown>"
    # The unlocker id is the dedupe token — re-emitting the same unblock
    # (e.g. a `done` event replayed) must not append duplicate comments.
    token = f"[unblocked by {unlocker_id}]"
    for card_id in event["card_ids"]:
        if _comment_token_present(card_id, token, store=store):
            out.append({"card_id": card_id, "action": "already-recorded"})
            continue
        try:
            _store.comment_task(
                store=store, task_id=card_id, text=token, by=by, kind="unblock"
            )
            out.append({"card_id": card_id, "action": "comment-appended"})
        except _store.TaskNotFoundError:
            out.append({"card_id": card_id, "action": "card-not-found"})
    return out


def _comment_token_present(
    card_id: str,
    token: str,
    *,
    store: Any | None,
) -> bool:
    """True iff some existing comment on ``card_id`` contains ``token``."""
    try:
        existing = _store.get_task(store=store, task_id=card_id)
    except (AttributeError, _store.TaskNotFoundError):
        return False
    if existing is None:
        return False
    for c in existing.get("comments") or ():
        if not isinstance(c, dict):
            continue
        if token in (c.get("text") or ""):
            return True
    return False


def _push_already_recorded(
    card_id: str,
    commit_sha: str,
    *,
    store: Any | None,
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


def _run_plugins(
    event: dict, *, entry_points: Iterable | None = None
) -> tuple[int, list[dict]]:
    """Discover + invoke every plugin registered under
    :data:`ENTRY_POINT_GROUP`. Failures are caught + logged.

    ``entry_points`` overrides discovery with an explicit iterable of
    entry-point-shaped objects (``.name`` + ``.load()``); ``None`` reads
    the real group via :func:`_iter_entry_points`. See
    :func:`dispatch_event` for the rationale (in-process injection seam,
    PA-306-compliant).
    """
    plugin_errors: list[dict] = []
    eps = _iter_entry_points() if entry_points is None else entry_points
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
    for ep in eps:
        name = ep.name
        try:
            fn: Callable[[dict], None] = ep.load()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "scitex_todo.hooks plugin %r failed to load: %s",
                name,
                exc,
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
                "scitex_todo.hooks plugin %r (priority=%d critical=%s) raised: %s",
                name,
                prio,
                critical,
                exc,
            )
            plugin_errors.append(
                {
                    "plugin": name,
                    "priority": prio,
                    "critical": critical,
                    "error": str(exc),
                }
            )
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
