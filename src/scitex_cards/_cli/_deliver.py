#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI verb ``scitex-todo deliver`` — one-shot notification delivery pass.

Runs :func:`scitex_cards._delivery.deliver_pending` once: read every
configured recipient's pending notifications and hand them to the channels
configured for that user, recording outcomes in the delivery ledger.

This one-shot command is slice 1's "always-on" stand-in — it is
cron/loop-runnable (run it on a timer to keep notifications flowing). The
long-running daemon + systemd unit are a LATER slice and are intentionally
NOT built here.
"""

from __future__ import annotations

import json

import click

from ._compat import deprecated_alias, spec_command_kwargs

#: Version that removes the Phase-W ``deliver`` alias (doctrine §5).
_REMOVE_IN = "0.20.0"


def register(main: click.Group) -> None:
    """Attach ``deliver-notifications`` (+ the Phase-W ``deliver`` alias)."""
    main.add_command(deliver_notifications_cmd)
    deprecated_alias(
        main, "deliver", target="deliver-notifications", remove_in=_REMOVE_IN
    )


@click.command(
    "deliver-notifications",
    **spec_command_kwargs(
        summary="Run ONE notification-delivery pass (cron/loop-runnable).",
        description=(
            "Reads each configured recipient's pending notifications "
            "(read-only — never touches their `seen` cursor) and hands them "
            "to the channels in recipients.yaml, recording outcomes in the "
            "delivery ledger so nothing is double-sent.",
        ),
        examples=(
            ("{prog} deliver-notifications", "One pass over the resolved store."),
            ("{prog} deliver-notifications --json", "Machine-readable summary."),
        ),
    ),
)
@click.option(
    "--tasks",
    "tasks_path",
    default=None,
    help="Path to tasks.yaml (default: project -> user -> bundled example, "
    "or $SCITEX_TODO_TASKS_YAML_SHARED). Resolves the inbox + ledger + recipients dir.",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    help="Emit the delivery summary as JSON (machine-readable).",
)
def deliver_notifications_cmd(tasks_path: str | None, as_json: bool) -> None:
    """Run one delivery pass and print the summary."""
    from .._delivery import deliver_pending

    summary = deliver_pending(store=tasks_path)

    if as_json:
        click.echo(json.dumps(summary))
        return

    click.echo(
        f"# delivery: sent={summary['sent']} "
        f"failed={summary['failed']} "
        f"failed_terminal={summary['failed_terminal']} "
        f"skipped={summary['skipped']} "
        f"({len(summary['outcomes'])} item(s) recorded this run)"
    )
    if summary["failed_terminal"]:
        click.echo(
            f"# WARNING: {summary['failed_terminal']} notification(s) gave up "
            "after max attempts (comm-miss) — see stderr / delivery_ledger.yaml"
        )
    for item in summary["outcomes"]:
        click.echo(
            f"  {item['outcome']:<8} {item['recipient']} "
            f"{item['notification_id']} via {item['channel']}"
        )


# EOF
