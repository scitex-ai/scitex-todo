#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scitex-cards leaf provider for the ``scitex_dev.jobs`` federation.

Declares scitex-cards's own pieces of the ecosystem-aggregator
contract (lead a2a ``c2908456`` / ``d35f5ae6``, 2026-06-11): one
``scitex_dev.jobs`` entry point per leaf package, each returning the
``list[JobSpec]`` the leaf wants registered. scitex-dev's
``ecosystem up`` then installs / enables them as one set with no
per-package systemctl ceremony.

scitex-cards's only piece today is the **board** web dashboard — the
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
    scitex-cards = "scitex_cards._jobs_provider:provide_jobs"

After install, ``scitex-dev ecosystem up --yes`` materialises
``~/.config/systemd/user/scitex-cards.dashboard.service`` and brings
it up. The master ``scitex-dev-ecosystem-reconcile.service``
(installed via ``ecosystem up --install-master-unit``) keeps it
reconciled on every boot.
"""

from __future__ import annotations

from scitex_dev.jobs import JobSpec


def provide_jobs() -> list[JobSpec]:
    """Return the JobSpec list scitex-cards contributes to the federation.

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
    * ``name="scitex-cards.dashboard"`` — package-prefixed so the unit
      file becomes ``scitex-cards.dashboard.service`` and the operator
      can grep ``systemctl --user list-units 'scitex-cards.*'`` to see
      every scitex-cards-owned unit at a glance.
    """
    return [
        JobSpec(
            name="scitex-cards.dashboard",
            kind="service",
            schedule="",
            command="scitex-cards board start --port 8051",
            description=(
                "scitex-cards board start — read-only live view of the "
                "shared ~/.scitex/todo/tasks.yaml at http://127.0.0.1:8051/"
            ),
            on_boot_sec="15s",
            restart_policy="on-failure",
            timeout_sec=30,
        ),
        # P3b + P3d (lead-approved 2026-06-12) — wake-watcher. The push
        # side of the self-consuming board loop: polls tasks.yaml,
        # detects new/commented/changed tasks, POSTs /v1/turn to the
        # owning agent's a2a port. Pairs with `scitex-cards next --mine`
        # (pull side) + the agent self-consumption loop sub-skill (32).
        # kind=service + Restart=on-failure: an absent watcher means
        # operator drops a request and nobody wakes up — that's exactly
        # the failure mode the loop exists to prevent, so a crash MUST
        # be restarted automatically.
        JobSpec(
            name="scitex-cards.wake-watcher",
            kind="service",
            schedule="",
            # --interval 30 (was 2): a 2s interval re-parsed the ~9 MB store
            # faster than the tick finished on a slow host and death-spiraled
            # the fleet on 2026-07-08 (incident-todo-wake-watcher-interval2-
            # spiral). The `watch` command additionally CLAMPS anything below
            # a 10s hard floor, so this value can never foot-gun again.
            command="scitex-cards watch --push --interval 30",
            description=(
                "scitex-cards wake-watcher — push side of the "
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
        # via scitex-cards's self-contained HTTP push wire (`_push.deliver`),
        # plus a separate quiet-nudge if any open in_progress task has
        # gone untouched for > SCITEX_TODO_NUDGE_QUIET_MIN minutes
        # (default 10). Structural feedback loop: silence + in_progress
        # → escalation, no manual lead intervention required.
        #
        # The --nudge-quiet path ALSO runs the stale-active sweep
        # (_stale_active_nudge.sweep_and_nudge): per-OWNER nudge for
        # in_progress/blocked cards untouched > SCITEX_TODO_STALE_ACTIVE_HOURS
        # (default 2 h) over the same push wire. Replaces the manual
        # card-freshness campaign; no new cron — it rides this */10 one.
        JobSpec(
            name="scitex-cards.notify",
            # `cron` (the JobSpec valid set is `cron|service|timer`).
            # 5-field cron schedule (min hour dom mon dow): every 10 min.
            kind="cron",
            schedule="*/10 * * * *",
            command=(
                "scitex-cards print-stats --by agent "
                "--notify --nudge-quiet"
            ),
            description=(
                "scitex-cards throughput pulse — pushes per-agent open "
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
        # ci-watch (operator decoupled-pollers override, dev msg
        # `96afacc7` 2026-06-15) — RECORD-ONLY: poll every 5 min,
        # diff against ci-state.json, log per-repo transitions, update
        # the cache. No bus emission, no a2a sends — SAC owns the
        # delivery side via its OWN independent poller. Two pollers,
        # different cadences, each STANDALONE: todo down → sac still
        # delivers; sac down → todo still records.
        JobSpec(
            # NOTE: the JobSpec NAME is a registry identity (systemd
            # unit / dedupe key), so it keeps its historical spelling;
            # the COMMAND uses the canonical verb (`watch-ci`, renamed
            # from `ci-watch` in the slice-6b verb-rename pilot).
            name="scitex-cards.ci-watch",
            kind="cron",
            # 5-field cron: every 5 min. Matches the cadence dev
            # locked in the contract; SAC's independent poller can
            # run slower (10 / 15 / 30) without breaking parity since
            # the dedupe key (head_sha, overall) is content-keyed.
            schedule="*/5 * * * *",
            command="scitex-cards watch-ci --once",
            description=(
                "scitex-cards watch-ci — record-only CI poller. Polls "
                "the configured fleet repos every 5 min, diffs vs "
                "~/.scitex/todo/ci-state.json, logs per-repo "
                "transitions (newly-green / newly-red / still-pending). "
                "Operator decoupled-pollers lane (no SAC dependency)."
            ),
            restart_policy="no",
            timeout_sec=180,
        ),
        # reconcile-merged-prs (card-freshness automation) — deterministic
        # auto-close. Every ~15 min, scan open cards (pending / in_progress /
        # blocked) that carry a `pr_url`, check whether the linked PR has
        # MERGED (gh on the host, curl GitHub-REST fallback), and flip the
        # merged ones to `done` + an audit comment. `--apply` because the
        # cron IS the mutation path (the verb is DRY-RUN by default for
        # humans); the core is fail-soft (unknown merge-state -> skip, never
        # wrongly close). kind=cron / restart_policy=no: a missed tick just
        # closes on the next one — no long-running process to keep alive.
        JobSpec(
            name="scitex-cards.reconcile-merged-prs",
            kind="cron",
            # 5-field cron (min hour dom mon dow): every 15 min.
            schedule="*/15 * * * *",
            command="scitex-cards reconcile-merged-prs --apply",
            description=(
                "scitex-cards reconcile-merged-prs — periodic card-freshness "
                "automation. Every 15 min, auto-close cards whose linked PR "
                "(pr_url) has merged so nobody hand-updates the board. "
                "Fail-soft: unknown merge-state is skipped, never closed."
            ),
            restart_policy="no",
            timeout_sec=300,
        ),
    ]


# EOF
