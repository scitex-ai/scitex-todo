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

from pathlib import Path

import scitex_todo
from scitex_dev.jobs import JobSpec


def _repo_root() -> Path | None:
    """Return the scitex-todo git checkout root, or ``None`` if not a checkout.

    Walks up from ``scitex_todo.__file__`` (the installed package location)
    until it finds a directory containing a ``.git`` entry. ``.git`` is a
    *directory* in a normal clone and a *file* in a git worktree, so we
    accept either via ``Path.exists()`` rather than ``is_dir()``.

    Returns ``None`` when no ``.git`` is found — that is the signature of a
    non-editable / PyPI (``site-packages``) install. A released install must
    NEVER try to ``git pull``, so callers treat ``None`` as "do not register
    the dev-sync timer".
    """
    start = Path(scitex_todo.__file__).resolve().parent
    for candidate in (start, *start.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


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

    The list ALSO carries a conditional **dev-sync timer**
    (``scitex-todo.dev-sync``) — see the in-body comment for why — that
    is appended only when the package is an editable git checkout.
    """
    jobs = [
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
        # ci-watch (operator decoupled-pollers override, dev msg
        # `96afacc7` 2026-06-15) — RECORD-ONLY: poll every 5 min,
        # diff against ci-state.json, log per-repo transitions, update
        # the cache. No bus emission, no a2a sends — SAC owns the
        # delivery side via its OWN independent poller. Two pollers,
        # different cadences, each STANDALONE: todo down → sac still
        # delivers; sac down → todo still records.
        JobSpec(
            name="scitex-todo.ci-watch",
            kind="cron",
            # 5-field cron: every 5 min. Matches the cadence dev
            # locked in the contract; SAC's independent poller can
            # run slower (10 / 15 / 30) without breaking parity since
            # the dedupe key (head_sha, overall) is content-keyed.
            schedule="*/5 * * * *",
            command="scitex-todo ci-watch --once",
            description=(
                "scitex-todo ci-watch — record-only CI poller. Polls "
                "the configured fleet repos every 5 min, diffs vs "
                "~/.scitex/todo/ci-state.json, logs per-repo "
                "transitions (newly-green / newly-red / still-pending). "
                "Operator decoupled-pollers lane (no SAC dependency)."
            ),
            restart_policy="no",
            timeout_sec=180,
        ),
    ]

    # dev-sync timer — keep the editable dashboard checkout current with
    # origin/develop. WHY: merges land on origin/develop, but the board
    # dashboard (kind="service" above) is served from THIS editable git
    # checkout. Django runserver imports the on-disk source, so until the
    # checkout is pulled the board shows STALE code — the operator hit
    # exactly this (a merged fix invisible on the live board because the
    # checkout was behind). A periodic `git pull --ff-only` closes that
    # gap deterministically: --ff-only can only fast-forward, so it
    # NO-OPS / errors loud if the branch ever diverged — it can never
    # clobber local work. Registered ONLY on an editable git checkout
    # (REPO_ROOT has a .git); on a non-editable / PyPI install
    # (`_repo_root()` is None) we skip it entirely so a released install
    # never tries to git-pull.
    repo_root = _repo_root()
    if repo_root is not None:
        jobs.append(
            JobSpec(
                name="scitex-todo.dev-sync",
                # kind="timer" — periodic systemd --user Timer + oneshot
                # Service. on_unit_active_sec carries the cadence;
                # restart_policy MUST stay "no" (timers fire oneshot
                # services, Restart= does not apply). Confirmed against the
                # JobSpec contract + the `sac.accounts-refresh` timer
                # example in scitex_dev/jobs/__init__.py.
                kind="timer",
                # schedule optional for timers; cadence comes from
                # on_unit_active_sec below. Leave empty.
                schedule="",
                # Every 2 minutes — fresh enough that the board is never
                # meaningfully behind origin, cheap enough to be invisible.
                on_unit_active_sec="2min",
                # OnBootSec — settle network-online.target before the first
                # pull tries to reach origin.
                on_boot_sec="30s",
                command=(
                    f"git -C {repo_root} pull --ff-only origin develop"
                ),
                description=(
                    "scitex-todo dev-sync — ff-pull origin/develop into the "
                    "editable checkout the board serves, every 2 min, so the "
                    "live dashboard never shows stale code. --ff-only never "
                    "clobbers local work."
                ),
                restart_policy="no",
                timeout_sec=60,
            )
        )

    return jobs


# EOF
