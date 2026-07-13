#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Closed-enum WRITE semantics for the store: what `""` means on an enum field.

EVERY write surface (MCP tool, CLI verb, Python API) documents ONE rule:
*"pass an empty string to CLEAR a field."* That held for the free-text
fields — the surface layers mapped ``"" -> None`` and the store popped the
key — but NOT for the CLOSED-ENUM ones (``blocker`` / ``kind`` / ``status``;
see ``VALID_BLOCKERS`` / ``VALID_KINDS`` / ``VALID_STATUSES`` in `_model`).

A ``""`` that reached the store was written LITERALLY, and the validator then
rejected the save at persist time::

    TaskValidationError: task '...' has invalid blocker '';
    must be one of ('compute','dependency','dep','operator-decision',
    'agent-wait','none') or absent

So the DOCUMENTED way to clear a blocker was the one way that could not work.
Worse, it failed LATE — at save, after the caller had built a mutation it
believed valid — so in a bulk script it aborted the WHOLE batch (hit live
during a 4-card triage run, which died having applied nothing). The
workaround in the wild was ``blocker="none"``: a legal enum member, but the
key stays PRESENT, so it is not the same as clearing.

This module puts the rule in ONE place, consumed by the store's write paths
BEFORE the validator ever sees a value: a clear-sentinel is a DELETE
INSTRUCTION, not a value. The validator therefore stays STRICT — a genuinely
invalid ``blocker="banana"`` still raises — and the contract becomes one rule
rather than per-field trivia.
"""

from __future__ import annotations

from ._model import VALID_STATUSES, TaskValidationError

__all__ = [
    "CLEARABLE_ENUM_FIELDS",
    "UNCLEARABLE_ENUM_FIELDS",
    "is_clear_sentinel",
    "resolve_enum_clears",
]

#: Closed-enum fields that CAN be cleared. Absence is meaningful for both:
#: a card with no ``blocker`` is simply not gated on a named thing, and an
#: absent ``kind`` is equivalent to ``kind: "task"`` (the documented default).
CLEARABLE_ENUM_FIELDS: tuple[str, ...] = ("blocker", "kind")

#: Closed-enum fields that CANNOT be cleared. ``status`` is the card's
#: DECISION, not an optional label — the same reasoning that abolished
#: ``pending`` (a card must say what it is). A status-less row has no lane on
#: the board and silently drops out of every status-filtered view, so
#: "clearing" it would HIDE the card rather than change it. A clear-sentinel
#: on ``status`` is refused LOUDLY, naming the alternatives — never silently
#: ignored, and never passed through as ``""`` for the validator to trip over.
UNCLEARABLE_ENUM_FIELDS: tuple[str, ...] = ("status",)


def is_clear_sentinel(value: object) -> bool:
    """True when ``value`` is the empty-string CLEAR sentinel.

    Whitespace-only counts: ``"  "`` is a typo'd ``""``, never a legal enum
    member, so honouring it as a clear beats writing it for the validator to
    reject.
    """
    return isinstance(value, str) and not value.strip()


def resolve_enum_clears(fields: dict, *, source: str) -> dict:
    """Resolve `""` on a closed-enum field to a DELETE — or a loud refusal.

    Returns a NEW mapping in which a clear-sentinel on a CLEARABLE enum field
    (``blocker`` / ``kind``) has become ``None`` — which the store's write
    paths already treat as *"remove this key"*. Every other field passes
    through untouched.

    Raises
    ------
    TaskValidationError
        On a clear-sentinel for an UNCLEARABLE enum field (``status``). The
        message names WHY it cannot be cleared and what to set instead — the
        caller asked for something incoherent, so they get an error, not a
        silently-dropped request.
    """
    for key in UNCLEARABLE_ENUM_FIELDS:
        if key in fields and is_clear_sentinel(fields[key]):
            raise TaskValidationError(
                f"{source}: cannot clear {key!r} with '' — every task must "
                f"carry a status; it is the card's decision, not an optional "
                f"label. A status-less card has no lane on the board and "
                f"drops out of every status-filtered view, so clearing it "
                f"would HIDE the card rather than change it. Set one of "
                f"{VALID_STATUSES} instead: 'in_progress' if you are working "
                f"it, 'blocked' (with a blocker naming the gate) if you are "
                f"stuck, 'deferred' if it can wait, or 'done' / 'cancelled' "
                f"to close it."
            )
    resolved = dict(fields)
    for key in CLEARABLE_ENUM_FIELDS:
        if key in resolved and is_clear_sentinel(resolved[key]):
            # None = pop the key (the store's existing delete signal).
            # NEVER write "" — that is what reached the validator and blew
            # up the caller's whole batch at save time.
            resolved[key] = None
    return resolved


# EOF
