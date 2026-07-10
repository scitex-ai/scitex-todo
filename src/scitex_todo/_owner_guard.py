#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fail-loud OWNER / CREATOR identity enforcement (P1 fleet-identity rollout).

Closes ``todo-failloud-rollout-fleet-identity-createform-20260626``.

INCIDENT (2026-07-10): an agent whose project doc still said "you are
proj-scitex-dev" passed ``agent="proj-scitex-dev"`` explicitly on every card
it created. ``proj-scitex-dev`` was not a live/registered identity, so every
digest / nudge / card-event enqueued to its inbox was silently lost — nobody
drains an inbox for a name nobody recognizes. 75 cards across 10 dead
``proj-*`` identities were orphaned this way; the agents' own
``SCITEX_TODO_AGENT_ID`` env was correct the WHOLE time — they trusted a
stale doc over the env var. Operator directive (verbatim):
"エージェントIDは環境変数を使用させてください、強制が理想です" — agent IDs must
come from the environment variable; enforcement is ideal.

Three rules
-----------
1. DEFAULT-RESOLVE ``created_by`` from ``$SCITEX_TODO_AGENT_ID`` when
   omitted. Already implemented at the call site
   (:func:`scitex_todo._store._resolve_creator_or_raise`) — this module does
   not duplicate it. Deliberately NOT extended to the card OWNER
   (``assignee`` / ``agent``): ``_store.add_task`` already requires an
   EXPLICIT, non-blank owner
   (``tests/scitex_todo/test__owner_ssot.py::test_add_task_raises_when_owner_missing``)
   — an even stricter anti-silent-owner policy than "default to me" would
   be. Relaxing it to auto-self-assign on omission would be a silent
   regression of that existing, deliberate, tested contract, so it is left
   untouched here.

2. VALIDATE THE OWNER EXISTS — :func:`check_owner_known`. Raises
   :class:`scitex_todo._model.TaskValidationError` when an explicit owner
   resolves to no registered :class:`scitex_todo._users.User`
   (:func:`scitex_todo._users.resolve_user` returns ``None``), naming the
   bad value plus near-match hints (mechanical alias-collapse + fuzzy
   match against registered names).

3. DO NOT FORGE AUTHORSHIP — :func:`check_created_by_not_forged`. Raises
   when an explicit ``created_by`` differs (after canonical-identity
   collapse) from the caller's own resolved ``$SCITEX_TODO_AGENT_ID``. An
   agent may ASSIGN work to anyone; it may not CLAIM someone else wrote
   the card.

Enforcement gate — READ BEFORE FLIPPING IT ON
----------------------------------------------
Both (2) and (3) are gated behind the SAME switch
:mod:`scitex_todo._users._identity` already ships for exactly this reason —
:func:`scitex_todo._users.strict_identity_enabled`
(``SCITEX_TODO_STRICT_IDENTITY``, truthy ``1``/``true``/``yes``/``on``) —
DEFAULT OFF. That module was written, then never wired to anything (verified:
no other module in ``src/`` calls ``resolve_identity`` / imports
``ENV_STRICT_IDENTITY`` before this change). This module is that wiring.

Default-OFF is a DELIBERATE, VERIFIED choice, not caution for its own sake:

* The production store (``~/.scitex/todo/tasks.yaml``, checked read-only on
  2026-07-10, never written) has exactly ONE registered user
  (``u_f9db029ca1d5`` / ``"scitex-todo"``). Enforcing (2) unconditionally
  today would reject every real assignee in the fleet except that one
  name — a worse outage than the incident it exists to prevent.
* The existing test suite intentionally simulates MULTIPLE creator
  identities (``"alice"``, ``"dave"``, ``"operator"``, ...) in a single
  process while an autouse fixture pins
  ``SCITEX_TODO_AGENT_ID=agent:test-suite``
  (``tests/scitex_todo/conftest.py::_default_resolvable_creator``), and
  ``test__store.py::test_add_task_stores_created_by_explicit`` explicitly
  documents + asserts "explicit author wins over the env/login chain".
  Enforcing (3) unconditionally would fail that test plus ~20 others across
  ``test__inbox.py`` / ``test__notify_dispatch.py`` /
  ``test__store_card_events.py`` / ``test__mcp_server.py`` / the ``_cli``
  tests — a demonstrated regression, not a hypothetical one.

Turning ``SCITEX_TODO_STRICT_IDENTITY=1`` on is therefore a DEPLOYMENT
decision for whoever has populated the ``_users`` registry with the fleet's
real identities (see ``register_user`` / ``add_alias``) — this change does
not flip it by itself. See the task report for the full rationale.

Escape hatch
------------
``allow_unknown_owner=True`` (or ``SCITEX_TODO_ALLOW_UNKNOWN_OWNER`` truthy)
bypasses ONLY rule (2), even with strict mode on. It exists SOLELY so a
steward's migration/repair tooling can still write to a card that ALREADY
points at a dead identity — exactly the cards rule (2) now prevents from
being CREATED — e.g. reassigning cards off a dead ``proj-*`` owner during a
repair sweep. Using it for normal card creation / update / reassignment is a
BUG: it defeats the very check this module exists to provide.
"""

from __future__ import annotations

import difflib
import os

from ._model import TaskValidationError

#: Caller identity env var (mirrors ``_store.ENV_AGENT``; re-declared here so
#: this module has zero import-time dependency on ``_store``, which is
#: already grandfathered over the 512-line cap).
ENV_AGENT = "SCITEX_TODO_AGENT_ID"

#: Escape hatch — see module docstring. OFF by default.
ENV_ALLOW_UNKNOWN_OWNER = "SCITEX_TODO_ALLOW_UNKNOWN_OWNER"


def _truthy(raw: str | None) -> bool:
    return raw is not None and raw.strip().lower() in {"1", "true", "yes", "on"}


def enforcement_enabled() -> bool:
    """Whether rules (2)/(3) actually raise. See module docstring "gate"."""
    from ._users import strict_identity_enabled

    return strict_identity_enabled()


def escape_hatch_enabled(explicit: bool = False) -> bool:
    """Whether the narrow migration/repair bypass is active for this call."""
    return bool(explicit) or _truthy(os.environ.get(ENV_ALLOW_UNKNOWN_OWNER))


def check_owner_known(
    owner: str | None,
    *,
    store=None,
    allow_unknown_owner: bool = False,
) -> None:
    """Rule (2): fail loud when ``owner`` resolves to no registered user.

    No-op when: enforcement is off (:func:`enforcement_enabled`), the escape
    hatch is engaged, or ``owner`` is blank (blank-owner rejection is a
    SEPARATE, always-on check at the call site — see ``_store.add_task``).
    """
    if not (isinstance(owner, str) and owner.strip()):
        return
    if not enforcement_enabled():
        return
    if escape_hatch_enabled(allow_unknown_owner):
        return

    owner = owner.strip()
    from ._users import canonical_identity, load_users, resolve_user

    if resolve_user(owner, store=store) is not None:
        return

    users = load_users(store)
    known_names = sorted({n for u in users for n in u.names})
    hints: list[str] = []
    mechanical = canonical_identity(owner, strict=False)
    if mechanical != owner:
        hints.append(mechanical)
    for close in difflib.get_close_matches(owner, known_names, n=3, cutoff=0.6):
        if close not in hints:
            hints.append(close)

    hint_text = f" Did you mean one of {hints!r}?" if hints else ""
    raise TaskValidationError(
        f"unknown owner {owner!r} — no registered scitex-todo identity "
        f"matches it.{hint_text} A card owned by an unregistered identity "
        f"has its notifications SILENTLY DISCARDED — nobody drains an inbox "
        f"for a name nobody recognizes (the exact 2026-07-10 incident: 75 "
        f"cards orphaned under dead proj-* owners). Register the owner via "
        f"scitex_todo._users.register_user (or add_alias if this is a "
        f"renamed existing identity) before assigning a card to it. Genuine "
        f"migration/repair tooling that must touch a card ALREADY pointing "
        f"at a dead identity may pass allow_unknown_owner=True (or set "
        f"SCITEX_TODO_ALLOW_UNKNOWN_OWNER=1) — using this escape hatch for "
        f"normal card creation/assignment is a bug."
    )


def check_created_by_not_forged(
    created_by: str | None,
    *,
    allow_unknown_owner: bool = False,
) -> None:
    """Rule (3): fail loud when explicit ``created_by`` != caller's identity.

    No-op when: enforcement is off, the escape hatch is engaged,
    ``created_by`` is blank, or ``$SCITEX_TODO_AGENT_ID`` is unset (nothing
    to compare against — the explicit value is the only source of truth in
    that case; ``_store._resolve_creator_or_raise`` separately enforces
    "creator must be resolvable at all", unconditionally).
    """
    if not (isinstance(created_by, str) and created_by.strip()):
        return
    if not enforcement_enabled():
        return
    if escape_hatch_enabled(allow_unknown_owner):
        return

    env_val = os.environ.get(ENV_AGENT)
    if not (isinstance(env_val, str) and env_val.strip()):
        return

    from ._users import canonical_identity

    mine = canonical_identity(env_val.strip(), strict=False)
    theirs = canonical_identity(created_by.strip(), strict=False)
    if mine == theirs:
        return
    raise TaskValidationError(
        f"created_by={created_by!r} does not match the caller's own "
        f"resolved identity ({env_val!r} via $SCITEX_TODO_AGENT_ID) — an "
        f"agent may ASSIGN a card to anyone but may NOT CLAIM that someone "
        f"else wrote it. Omit created_by (it defaults from "
        f"$SCITEX_TODO_AGENT_ID) or pass a value matching your own "
        f"identity."
    )


def payload_identity_error(payload: dict, *, store=None) -> str | None:
    """One-shot rule (2)/(3) check for a raw web-form payload dict.

    Thin convenience for HTTP handlers that mutate a task dict directly
    (bypassing ``_store.add_task`` / ``update_task``, e.g.
    ``_django.handlers.crud.handle_update``) so they don't have to import
    :class:`TaskValidationError` themselves just to surface a 400. Returns
    the error message string when ``payload``'s ``assignee`` / ``agent`` /
    ``created_by`` fails a check, or ``None`` when clean.
    """
    try:
        for owner_field in ("assignee", "agent"):
            if payload.get(owner_field):
                check_owner_known(payload[owner_field], store=store)
        if payload.get("created_by"):
            check_created_by_not_forged(payload["created_by"])
    except TaskValidationError as exc:
        return str(exc)
    return None


__all__ = [
    "ENV_AGENT",
    "ENV_ALLOW_UNKNOWN_OWNER",
    "check_created_by_not_forged",
    "check_owner_known",
    "enforcement_enabled",
    "escape_hatch_enabled",
    "payload_identity_error",
]

# EOF
