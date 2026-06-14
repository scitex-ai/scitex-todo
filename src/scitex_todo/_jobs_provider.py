#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scitex-todo leaf provider for the ``scitex_dev.jobs`` federation.

Declares scitex-todo's own pieces of the ecosystem-aggregator
contract (lead a2a ``c2908456`` / ``d35f5ae6``, 2026-06-11): one
``scitex_dev.jobs`` entry point per leaf package, each returning the
``list[JobSpec]`` the leaf wants registered. scitex-dev's
``ecosystem up`` then installs / enables them as one set with no
per-package systemctl ceremony.

scitex-todo's only piece today is the **board** web dashboard — the
live ``http://127.0.0.1:8051/`` view of the shared
``~/.scitex/todo/tasks.yaml`` board that every sac agent reads from
and writes to. The board is the operator's primary daily surface;
having it brought up + kept alive at the systemd layer
(``Restart=on-failure``) is what makes the cross-fleet feedback loop
actually visible.

Wiring (in ``pyproject.toml``)
------------------------------
::

    [project.entry-points."scitex_dev.jobs"]
    scitex-todo = "scitex_todo._jobs_provider:provide_jobs"

After install, ``scitex-dev ecosystem up --yes`` materialises
``~/.config/systemd/user/scitex-todo.dashboard.service`` and brings
it up. The master ``scitex-dev-ecosystem-reconcile.service``
(installed via ``ecosystem up --install-master-unit``) keeps it
reconciled on every boot.
"""

from __future__ import annotations

from scitex_dev.jobs import JobSpec


def provide_jobs() -> list[JobSpec]:
    """Return the JobSpec list scitex-todo contributes to the federation.

    One entry: the board dashboard.

    Choices baked into the JobSpec:

    * ``kind="service"`` — the board is a long-running unit (Django
      runserver listening on TCP 8051), NOT a periodic task. ``service``
      means systemd writes a ``Type=simple`` ``.service`` (no ``.timer``)
      with ``Restart=`` from ``restart_policy``.
    * ``restart_policy="on-failure"`` — the board MUST come back if
      the Python process crashes. The operator notices a missing 8051
      board immediately (his daily inbox is the board UI); ``Restart=
      on-failure`` plus the master reconcile unit on boot keeps the
      MTBF on operator-visible loss measured in seconds, not hours.
    * ``on_boot_sec="15s"`` — short delay after boot before starting,
      enough for network-online.target to settle. Materialised by
      scitex-dev's systemd builder as an ``ExecStartPre=/bin/sleep 15``.
    * ``timeout_sec=30`` — bounds the start-up phase; if Django can't
      bind 8051 in 30 s something else is on the port and the operator
      needs to see the failure now, not after a 90 s default wait.
    * ``schedule=""`` — required to be empty for ``kind="service"``;
      ``JobSpec.validate()`` raises if we forget. (Services are NOT
      scheduled — they run continuously.)
    * ``name="scitex-todo.dashboard"`` — package-prefixed so the unit
      file becomes ``scitex-todo.dashboard.service`` and the operator
      can grep ``systemctl --user list-units 'scitex-todo.*'`` to see
      every scitex-todo-owned unit at a glance.
    """
    return [
        JobSpec(
            name="scitex-todo.dashboard",
            kind="service",
            schedule="",
            command="scitex-todo board start --port 8051",
            description=(
                "scitex-todo board start — read-only live view of the "
                "shared ~/.scitex/todo/tasks.yaml at http://127.0.0.1:8051/"
            ),
            on_boot_sec="15s",
            restart_policy="on-failure",
            timeout_sec=30,
        ),
        # P3b + P3d (lead-approved 2026-06-12) — wake-watcher. The push
        # side of the self-consuming board loop: polls tasks.yaml,
        # detects new/commented/changed tasks, POSTs /v1/turn to the
        # owning agent's a2a port. Pairs with `scitex-todo next --mine`
        # (pull side) + the agent self-consumption loop sub-skill (32).
        # kind=service + Restart=on-failure: an absent watcher means
        # operator drops a request and nobody wakes up — that's exactly
        # the failure mode the loop exists to prevent, so a crash MUST
        # be restarted automatically.
        JobSpec(
            name="scitex-todo.wake-watcher",
            kind="service",
            schedule="",
            command="scitex-todo watch --push --interval 2",
            description=(
                "scitex-todo wake-watcher — push side of the "
                "self-consuming board loop. POSTs /v1/turn to the "
                "owning agent on new/commented/changed tasks."
            ),
            on_boot_sec="20s",
            restart_policy="on-failure",
            timeout_sec=30,
        ),
        # PR (h) (operator standing direction via lead a2a
        # `19d575415ae6422abdff9224b6a0c8de` + `9e710ab074ef4bf3a615be41793e0c51`,
        # 2026-06-12). 10-min structural-nudge cron — every 10 min, push
        # a per-agent body summary (RUNNABLE-first list + recent done)
        # via scitex-todo's self-contained HTTP push wire (`_push.deliver`),
        # plus a separate quiet-nudge if any open in_progress task has
        # gone untouched for > SCITEX_TODO_NUDGE_QUIET_MIN minutes
        # (default 10). Structural feedback loop: silence + in_progress
        # → escalation, no manual lead intervention required.
        JobSpec(
            name="scitex-todo.notify",
            # `cron` (the JobSpec valid set is `cron|service|timer`).
            # 5-field cron schedule (min hour dom mon dow): every 10 min.
            kind="cron",
            schedule="*/10 * * * *",
            command=(
                "scitex-todo print-stats --by agent "
                "--notify --nudge-quiet"
            ),
            description=(
                "scitex-todo throughput pulse — pushes per-agent open "
                "list + quiet-nudge every 10 min. Pairs with the "
                "operator's TG12608 nudge button + TG12618 channel "
                "vision: the cron is the STRUCTURAL feedback path; "
                "the UI button is the manual override."
            ),
            # cron has no boot concept (the systemd .timer fires on
            # the next scheduled tick), so on_boot_sec stays None.
            restart_policy="no",
            timeout_sec=60,
        ),
    ]


# EOF
