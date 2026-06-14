#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI verbs ``scitex-todo hook push|done`` — the producer-side wire
for shell-driven event producers.

Equivalent to the HTTP endpoints `/hooks/push` and `/hooks/done` but
callable from shell scripts that already have direct CLI access.
Lead a2a `6fff33d6` + `fbffb879`, 2026-06-14.
"""

from __future__ import annotations

import json
import sys

import click


def register(main: click.Group) -> None:
    """Attach the ``hook`` subgroup to the root CLI."""
    main.add_command(hook_group)


@click.group(
    "hook",
    help=(
        "Hook-consumer wire (loose-coupling contract for SAC's push-"
        "hook + dev's merge-Action).\n\n"
        "Two verbs: ``push`` (record a git push event) + ``done`` "
        "(record a PR-merge done event). Both idempotent. See also "
        "the HTTP twins: POST /hooks/push, POST /hooks/done."
    ),
)
def hook_group() -> None:
    """The ``hook`` noun group."""


@hook_group.command(
    "push",
    help=(
        "Record a git-push event on the board.\n\n"
        "Reads a canonical push-event JSON payload from `--payload "
        "<FILE>` or stdin (when --payload is `-`). Idempotently "
        "appends a comment to each named card.\n\n"
        "Example:\n"
        "  scitex-todo hook push --payload event.json"
    ),
)
@click.option(
    "--payload", "payload_path", required=True,
    type=click.Path(exists=False),
    help="Path to a JSON file with the push event payload, or '-' for stdin.",
)
def hook_push_cmd(payload_path: str) -> None:
    _run_hook(payload_path, expected_kind="push")


@hook_group.command(
    "done",
    help=(
        "Record a PR-merge / done event on the board.\n\n"
        "Reads a canonical done-event JSON payload from `--payload "
        "<FILE>` or stdin (when --payload is `-`). Idempotently "
        "flips each named card to status=done with pr_url stamped.\n\n"
        "Example:\n"
        "  scitex-todo hook done --payload event.json"
    ),
)
@click.option(
    "--payload", "payload_path", required=True,
    type=click.Path(exists=False),
    help="Path to a JSON file with the done event payload, or '-' for stdin.",
)
def hook_done_cmd(payload_path: str) -> None:
    _run_hook(payload_path, expected_kind="done")


def _run_hook(payload_path: str, *, expected_kind: str) -> None:
    """Shared body for the push / done verbs."""
    from .._hooks import HookEventError, dispatch_event, event_validate

    if payload_path == "-":
        text = sys.stdin.read()
    else:
        with open(payload_path, encoding="utf-8") as handle:
            text = handle.read()
    try:
        body = json.loads(text or "{}")
    except json.JSONDecodeError as exc:
        raise click.ClickException(f"invalid JSON: {exc}") from None
    if not isinstance(body, dict):
        raise click.ClickException("payload must be a JSON object")
    body.setdefault("kind", expected_kind)
    if body.get("kind") != expected_kind:
        raise click.ClickException(
            f"verb expects kind={expected_kind!r}; payload declared "
            f"kind={body.get('kind')!r}"
        )
    try:
        event = event_validate(body)
    except HookEventError as exc:
        raise click.ClickException(str(exc)) from None
    summary = dispatch_event(event)
    click.echo(json.dumps(summary, default=str))


# EOF
