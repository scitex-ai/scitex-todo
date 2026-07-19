#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI noun group ``scitex-cards cards`` — the card QUERY surface.

Doctrine §1 (``02_subcommand-structure-noun-verb.md``) is
``<cli> <noun> [<noun> …] <verb>``: the last token is the verb, every token
before it is a noun. ``cards list`` is that shape — ``cards`` the resource
category, ``list`` the canonical read verb — exactly like ``sac agents list``.

WHAT THIS REPLACES. Four top-level leaves each answered a different question
about the same resource, and three of them were not verbs at all:

    scitex-cards runnable    ->  scitex-cards cards list --runnable
    scitex-cards blocked     ->  scitex-cards cards list --blocked
    scitex-cards next        ->  scitex-cards cards list --next
    scitex-cards summary     ->  scitex-cards cards list --summary

``runnable`` / ``blocked`` are ADJECTIVES and ``next`` is a determiner; none
of them says what the command DOES. They are properties of the cards being
listed, so they are flags on the listing verb. The old names survive one
deprecation window as hidden Phase-W aliases (doctrine §5) because external
callers depend on them — notably the fleet's Stop hook, which shells
``scitex-cards runnable --agent <id> --json``.

BEHAVIOUR IS FORWARDED, NOT REIMPLEMENTED. Each mode invokes the existing
command callback through ``ctx.invoke``, so stdout, stderr and exit codes are
byte-identical to the old verb. A query surface that "mostly" matches the one
its callers were written against is a silent breakage.
"""

from __future__ import annotations

import click

from ._compat import deprecated_path_alias, spec_command_kwargs, spec_group_kwargs
from ._loop import next_cmd
from ._runnable import blocked_cmd, runnable_cmd
from ._write import summary_cmd

#: Version that removes the Phase-W aliases (doctrine §5 — every phase names
#: its deadline).
_REMOVE_IN = "0.20.0"

_MODES = ("runnable", "blocked", "next", "summary")


@click.group(
    "cards",
    **spec_group_kwargs(
        summary="Query the card store (the resource this CLI is about).",
        description=(
            "`cards list` is the single read surface: the --runnable / "
            "--blocked / --next / --summary flags select WHICH view of "
            "the store you want, instead of four separate top-level "
            "leaves named after adjectives.",
        ),
        command_categories=(("Core", ("list",)),),
    ),
)
def cards_group() -> None:
    """The ``cards`` noun group."""


@cards_group.command(
    "list",
    **spec_command_kwargs(
        summary="List cards; the mode flags select the view.",
        description=(
            "Modes are mutually exclusive. --runnable is the dispatcher's "
            "batch view (respects depends_on closure); --blocked is its "
            "inverse and names the reason each card is parked; --next "
            "picks the single top card for one agent; --summary prints "
            "counts by status / scope / assignee. With no mode flag, "
            "prints the filtered card table.",
        ),
        examples=(
            ("{prog} cards list --runnable --mine --json", "My dispatchable queue."),
            ("{prog} cards list --next --mine --json", "The single next pick."),
            ("{prog} cards list --summary", "Counts by status / scope / assignee."),
        ),
    ),
)
@click.option(
    "--runnable", "runnable", is_flag=True, help="Cards the dispatcher can pick up now."
)
@click.option(
    "--blocked", "blocked", is_flag=True, help="Cards that are NOT runnable, plus why."
)
@click.option(
    "--next", "next_", is_flag=True, help="The single next card for one agent."
)
@click.option(
    "--summary", "summary", is_flag=True, help="Counts by status / scope / assignee."
)
@click.option(
    "--tasks",
    "tasks_path",
    default=None,
    help="Path to the store (default: the standard resolution chain).",
)
@click.option(
    "--agent", default=None, help="Agent name. Mutually exclusive with --mine."
)
@click.option(
    "--mine", "use_mine", is_flag=True, help="Filter on $SCITEX_CARDS_AGENT_ID."
)
@click.option(
    "--group",
    "group",
    default=None,
    help="Dispatch-cluster name; '' = ungrouped only (--runnable / --blocked).",
)
@click.option(
    "--scope", default=None, help="Filter to this scope (--summary / default view)."
)
@click.option(
    "--project", default=None, help="Scope to one project (--next / default view)."
)
@click.option(
    "--status",
    "statuses",
    multiple=True,
    help="Match `status` exactly; repeat for a multi-status filter (default view).",
)
@click.option(
    "--auto-claim",
    is_flag=True,
    help="With --next: atomically flip the pick to in_progress (race-free).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.pass_context
def cards_list_cmd(  # noqa: PLR0913 — one flag per view; the surface IS the API
    ctx: click.Context,
    runnable: bool,
    blocked: bool,
    next_: bool,
    summary: bool,
    tasks_path: str | None,
    agent: str | None,
    use_mine: bool,
    group: str | None,
    scope: str | None,
    project: str | None,
    statuses: tuple,
    auto_claim: bool,
    as_json: bool,
) -> None:
    """List cards in the requested view."""
    selected = [
        name for name, on in zip(_MODES, (runnable, blocked, next_, summary)) if on
    ]
    if len(selected) > 1:
        raise click.UsageError(
            "Pass at most ONE of --runnable / --blocked / --next / --summary "
            f"(got {', '.join('--' + s for s in selected)})."
        )

    if runnable:
        ctx.invoke(
            runnable_cmd,
            tasks_path=tasks_path,
            agent=agent,
            use_mine=use_mine,
            group=group,
            as_json=as_json,
        )
    elif blocked:
        ctx.invoke(
            blocked_cmd,
            tasks_path=tasks_path,
            agent=agent,
            use_mine=use_mine,
            group=group,
            as_json=as_json,
        )
    elif next_:
        ctx.invoke(
            next_cmd,
            tasks_path=tasks_path,
            assignee=agent,
            use_mine=use_mine,
            project=project,
            auto_claim=auto_claim,
            as_json=as_json,
        )
    elif summary:
        ctx.invoke(
            summary_cmd,
            scope=scope,
            assignee=agent,
            as_json=as_json,
            tasks_path=tasks_path,
        )
    else:
        from ._admin import list_tasks_filtered

        list_tasks_filtered(
            scope,
            agent,
            None,
            as_json,
            tasks_path,
            statuses=list(statuses) or None,
            project=project,
        )


def register(main: click.Group) -> None:
    """Attach the ``cards`` group + the Phase-W aliases for the old leaves."""
    main.add_command(cards_group)
    for mode in _MODES:
        deprecated_path_alias(
            main,
            mode,
            path=("cards", "list"),
            extra_args=(f"--{mode}",),
            remove_in=_REMOVE_IN,
        )


# EOF
