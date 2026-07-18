#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-cards stop-hook`` — the Claude Code Stop hook, emitted directly.

WHY THIS LIVES IN CARDS AND NOT IN THE RUNTIME (operator, 2026-07-18): the
runtime's first version of this parsed ``may-stop``'s stdout and its numbered
stderr hints, which made cards' output format a public API the runtime
depended on — the mirror image of the coupling we had just deleted in the
other direction. Cards owns both ends here: it knows what work exists AND
what a useful next instruction reads like, so the format stays private and
can change freely. The runtime's remaining job is registration into
``.claude/settings.json`` and nothing else.

THE CONTRACT (Claude Code Stop hook): print a JSON object on stdout.
``{"decision": "block", "reason": "..."}`` refuses the stop AND feeds
``reason`` back as the agent's next instruction. An empty object allows it.

THE INVARIANT THIS SERVES (ADR-0012, sac's wording): while an agent's board
holds runnable work, that agent is EXECUTING. Idle-with-work-pending is not a
state the system passes through and repairs — it is a state the design makes
UNREACHABLE. So the reason is not a scolding; it is the NEXT ACTION, because
a refusal that does not say what to do next leaves the agent stopped-but-
refused, which is still idle.

FAIL-OPEN, DELIBERATELY. Any error — unreadable store, missing agent id,
malformed card — allows the stop. An agent wedged because the task store had
a bad day is worse than an agent that stopped early: the first is invisible
and self-inflicted, the second is caught by the failure-net sweep. Never let
this hook be the reason an agent cannot finish.
"""

from __future__ import annotations

import json
import sys

import click

#: Cap on items named in the reason. The reason becomes the agent's next
#: instruction, and an instruction listing forty cards is not an instruction.
_MAX_ITEMS = 5


def _reason_for(verdict: dict) -> str:
    """Render the verdict as an instruction the agent can act on."""
    items = verdict.get("items") or []
    shown = items[:_MAX_ITEMS]
    lines = [
        f"{i}. {it.get('card_id', '(no id)')} — {it.get('reason', '')}"
        f" — {it.get('next_action', '')}".rstrip(" —")
        for i, it in enumerate(shown, start=1)
    ]
    more = len(items) - len(shown)
    if more > 0:
        lines.append(f"{len(shown) + 1}. (+{more} more runnable item(s))")
    idle = verdict.get("idle_seconds")
    idle_note = f" You have been idle {idle}s." if idle else ""
    head = (
        f"You still have {len(items)} runnable item(s) on the board.{idle_note}"
        " Do not stop — take the next one now, or reconcile it (close it,"
        " block it with a NAMED gate, or defer it with a stated reason) so the"
        " board tells the truth:"
    )
    tail = (
        "Pick ONE and act on it in this turn. If something genuinely blocks"
        " you that is not on a card, write that card."
    )
    return "\n".join([head, *lines, tail])


@click.command("stop-hook")
@click.option(
    "--agent",
    default=None,
    help="Agent to check (default: $SCITEX_CARDS_AGENT_ID / $SCITEX_TODO_AGENT_ID).",
)
@click.option(
    "--tasks", "tasks_path", default=None, help="Store override (default: resolved)."
)
def stop_hook_cmd(agent, tasks_path):
    """Emit Claude Code Stop-hook JSON: block while runnable work remains."""
    try:
        from .._may_stop import may_stop
        from .._store import _default_agent

        verdict = may_stop(_default_agent(agent), tasks_path)
        if verdict.get("runnable"):
            click.echo(
                json.dumps({"decision": "block", "reason": _reason_for(verdict)})
            )
        else:
            click.echo(json.dumps({}))
    except Exception as exc:  # noqa: BLE001 — fail-open is the whole design
        # Never block on our own failure. Say so on stderr so the silence is
        # explainable, but let the agent stop.
        print(
            f"scitex-cards stop-hook: allowing stop, detector failed "
            f"({type(exc).__name__}: {exc})",
            file=sys.stderr,
        )
        click.echo(json.dumps({}))


def register(main) -> None:
    main.add_command(stop_hook_cmd)


__all__ = ["register", "stop_hook_cmd"]

# EOF
