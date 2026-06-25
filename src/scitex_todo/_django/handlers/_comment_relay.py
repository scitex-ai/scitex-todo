#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Comment-relay wire — push a board comment to the card's owning agent.

Extracted from :mod:`crud` (the file hit the line cap) so the relay
stays a small, self-contained concern. Called by ``handle_comment``
after the comment is already persisted; relay failure NEVER fails the
write.

Operator P1 (2026-06-25): posting a comment must NOT hang the board
~30 s when the owner's ``/v1/turn`` is slow/unreachable, and a notify
failure must be VISIBLE (loud toast), not swallowed. So this relay:

* uses a SHORT per-POST timeout (:data:`scitex_todo._push.NOTIFY_TIMEOUT_S`,
  2 s) instead of the 30 s background default, and
* passes ``dispatched_is_ok=False`` so a read-timeout returns
  ``ok=False, reason="timeout"`` FAST rather than silently claiming a
  "dispatched" success.

The returned dict rides back in the ``/comment`` JSON response under
``relay`` so the board JS can toast the outcome.

NB: the polling ``scitex-todo.wake-watcher`` ALSO POSTs the owner's
``/v1/turn`` when it sees ``len(comments)`` grow — so the owner is woken
on BOTH paths. That redundancy is intentional and harmless: this relay
is the INTERACTIVE path (gives the operator immediate, toast-able
feedback) while the watcher is the BACKGROUND reliability path (fires
even when the board process never ran the relay, e.g. a CLI/MCP
comment). Both use short timeouts now, so neither can hang the other.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def maybe_relay_comment(task: dict, comment: dict) -> dict:
    """If ``comment.author != task.agent``, push the comment to the
    owning agent via the same wire the nudge button uses.

    Returns a dict the JSON response includes so the UI can render a
    toast: ``{"sent": bool, "wire": ..., "reason": ..., "error": ...,
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

    from ..._push import NOTIFY_TIMEOUT_S, deliver

    # SHORT timeout + fail-loud — see module docstring. Comment is
    # already on disk; this push is best-effort feedback, not the write.
    result = deliver(
        target, body,
        kind="comment-relay",
        task_id=task["id"],
        timeout=NOTIFY_TIMEOUT_S,
        dispatched_is_ok=False,
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
        "error": result.get("error"),
        "target": target,
    }


__all__ = ["maybe_relay_comment"]

# EOF
