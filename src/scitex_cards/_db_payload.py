#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The CARD PAYLOAD: how a card survives a round-trip through SQLite intact.

WHY A PAYLOAD COLUMN AT ALL — THE MEASUREMENT THAT DECIDED IT
--------------------------------------------------------------
The obvious S2 read is "SELECT the typed columns and rebuild the dict from them."
It is wrong, and the live store says so out loud (measured 2026-07-13, 1,452 cards):

  * **22 distinct card keys are not in the column mapping at all** — ``deferred_at``
    (20 cards), ``subagent`` (8), ``blocked_by`` (3), ``completed_at``,
    ``tasks_path``, ``canonical_spec``, ``next_action``, and a whole family of
    ad-hoc ``note_*`` fields agents invent as they work. A column-based rebuild
    DROPS every one of them, silently. The card still looks right.
  * **711 distinct key ORDERS** exist across the cards. A column-based rebuild
    imposes one order on all of them, so anything that serializes a card (the CLI
    printing JSON, an API response) changes shape.

Neither would fail a count check, and neither would fail a "looks plausible" read.
They would just be wrong — and being wrong is strictly worse than being slow,
because slow is visible and wrong is not.

So: **the typed columns are the INDEX; ``card_json`` is the TRUTH.** SQL filters
on the indexed columns (that is the entire point — an indexed lookup instead of a
5.8 MB parse), and the row we hand back is decoded from the verbatim payload. The
read is exact BY CONSTRUCTION, not by a mapping someone has to remember to update
when a new field appears. A field this file has never heard of round-trips anyway.

WHY IT IS STILL FAST
--------------------
JSON decoding the matched rows is a fraction of the YAML parse it replaces, and
— unlike the YAML parse — it is paid only on the rows the query actually returns.
That is what makes filtering finally mean something: today
``list_tasks(assignee=...)`` costs the same as listing everything, because the
cost is the parse, not the query.
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

#: Name of the payload column on ``tasks``. Imported by the read guard, which
#: checks this exact name against ``PRAGMA table_info`` — the artifact, not a stamp.
CARD_JSON_COL = "card_json"


def json_or_none(value) -> str | None:
    """Serialize a non-empty list/dict to compact JSON, else ``None``.

    The side-car encoder (``deadlines`` / ``_log_meta`` / ``users.notify``). Kept
    here beside the payload encoder so both JSON policies live in one file.
    """
    if value in (None, [], {}):
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def card_payload_json(row: dict) -> str | None:
    """The card, VERBATIM, as JSON — the blob an S2 read reconstructs from.

    STRICT: no ``default=str`` coercion. A card that cannot round-trip through JSON
    losslessly (an exotic scalar, a non-string mapping key) yields ``None`` — and
    that ``NULL`` is LOAD-BEARING: it is what makes the read guard refuse the whole
    DB and fall back to YAML, instead of quietly handing back a card whose fields
    changed shape on the way through. A coercing encoder would have hidden it.

    ``sort_keys`` is deliberately NOT set: the YAML read gives callers the card's
    own key order, so this must too.
    """
    try:
        return json.dumps(row, ensure_ascii=False)
    except (TypeError, ValueError):
        logger.error(
            "!! CARD %r DOES NOT ROUND-TRIP THROUGH JSON — storing a NULL payload. "
            "The SQLite READ backend will REFUSE this DB (falling back to YAML) "
            "rather than serve a lossy copy of it. Your card is fine and the "
            "canonical YAML store is untouched.",
            row.get("id"),
        )
        return None


def card_from_payload(blob: str) -> dict:
    """Decode one ``card_json`` blob back into a card mapping."""
    return json.loads(blob)


__all__ = [
    "CARD_JSON_COL",
    "card_from_payload",
    "card_payload_json",
    "json_or_none",
]

# EOF
