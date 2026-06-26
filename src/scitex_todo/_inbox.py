#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone per-recipient pull-inbox for card-message delivery (no sac).

scitex-todo MUST deliver card-messages to its members with ZERO dependency
on scitex-agent-container (sac). The existing push rail
(:func:`scitex_todo._push.deliver`) POSTs directly to an agent's turn URL —
which CANNOT reach a *containerized* agent (the agent subscribes outbound to
a bus; a direct inbound POST is refused). The standalone-safe delivery model
is therefore **PULL**: the C4 dispatcher ENQUEUEs a notification record into
the recipient's inbox here, and the recipient's scitex-todo client POLLs the
board (via the ``poll_notifications`` MCP tool or, later, an HTTP endpoint)
for its pending notifications. The sac push rail stays an OPTIONAL parallel
ACCELERATOR for host-reachable agents — never a dependency.

Storage
-------
Inboxes live in the SAME YAML store file as tasks + users, under a
top-level ``inboxes:`` key (a sibling of ``tasks:`` / ``users:``), a mapping
keyed by recipient id::

    inboxes:
      u_3f9a1c0b7e42:
        - id: n_a1b2c3d4e5f6
          event_type: reassigned
          card_id: c1
          body: "Card c1 reassigned to you (by bob)"
          actor: bob
          ts: 2026-06-26T14:10:44Z
          seen: false
      dave:                 # raw-name fallback (unregistered owner)
        - {...}

The write path reuses the task store's
:func:`scitex_todo._model._store_lock` advisory lock and a ruamel
round-trip writer, so the hand-written ``tasks:`` payload + its inline
comments + the ``users:`` section all survive every inbox-side write
untouched (and vice versa). There is NO separate inbox file — mirrors how
:mod:`scitex_todo._users._store` persists the ``users:`` section.

Hard standalone constraint
---------------------------
ZERO sac / fleet imports. This module is the STANDALONE default delivery
sink and works with zero sac present.
"""

from __future__ import annotations

import logging
import secrets
from pathlib import Path
from typing import Any

from ._model import _store_lock
from ._paths import resolve_tasks_path

logger = logging.getLogger(__name__)

#: Top-level store key holding the per-recipient inboxes mapping.
_INBOXES_KEY = "inboxes"

#: Stable notification-id prefix (``n_`` + 12 hex chars, 48 bits entropy) —
#: mirrors the ``u_`` user-id shape so ids are visually distinguishable.
_NOTIFY_ID_PREFIX = "n_"

#: Number of hex chars in the random token portion of a notification id.
_NOTIFY_ID_TOKEN_HEX = 12


# --------------------------------------------------------------------------- #
# Internal helpers                                                            #
# --------------------------------------------------------------------------- #
def _resolved_store(store: str | Path | None) -> Path:
    """Resolve a store path through the same chain the task/user API uses."""
    return resolve_tasks_path(store) if store is None else Path(store).expanduser()


def _utc_now_iso() -> str:
    """ISO-8601 UTC timestamp with the canonical ``Z`` suffix.

    Identical second-resolution shape to ``_store._utc_now_iso`` /
    ``_users._store._utc_now_iso`` so notification timestamps match task +
    user timestamps on disk; re-implemented locally to keep this module free
    of an import cycle with the mutation layer.
    """
    import datetime as _dt

    return (
        _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _generate_notification_id() -> str:
    """Generate a fresh notification id (``n_`` + 12 hex chars)."""
    return _NOTIFY_ID_PREFIX + secrets.token_hex(_NOTIFY_ID_TOKEN_HEX // 2)


def _load_inboxes_section(path: Path) -> dict[str, list[dict]]:
    """Read the raw ``inboxes:`` mapping off disk (absent / malformed → {}).

    Uses ``yaml.safe_load`` (a read-only snapshot) — the ruamel round-trip
    is only needed on the WRITE path to preserve comments. Defensive: a
    missing file, an absent ``inboxes:`` key, or a non-mapping value all
    yield an empty mapping; per-recipient values that are not lists are
    coerced to ``[]`` so a malformed row never breaks a poll.
    """
    if not path.exists():
        return {}
    import yaml

    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    raw = data.get(_INBOXES_KEY)
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[dict]] = {}
    for recipient_id, records in raw.items():
        if not isinstance(recipient_id, str) or not recipient_id:
            continue
        if not isinstance(records, list):
            out[recipient_id] = []
            continue
        out[recipient_id] = [r for r in records if isinstance(r, dict)]
    return out


def _save_inboxes_unlocked(inboxes: dict[str, list[dict]], path: Path) -> None:
    """Write the ``inboxes:`` section, preserving ``tasks:`` / ``users:``.

    Reuses the ruamel round-trip writer so the existing ``tasks:`` +
    ``users:`` payloads, their inline comments, and document key order
    survive untouched — only the ``inboxes:`` key is replaced. Mirrors the
    atomic tmp-file + os.replace + reparse-verify dance in
    ``_users._store._save_users_unlocked`` / ``_model._save_tasks_unlocked``.

    Direct callers MUST already hold ``_store_lock(path)``.
    """
    import os

    from ruamel.yaml import YAML

    yaml_rt = YAML()
    yaml_rt.preserve_quotes = True
    yaml_rt.indent(mapping=2, sequence=4, offset=2)

    doc = None
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            loaded = yaml_rt.load(handle)
        if isinstance(loaded, dict):
            doc = loaded
    if doc is None:
        doc = {}
    doc[_INBOXES_KEY] = inboxes
    # Keep the document valid for ``_model.load_tasks`` even when this is an
    # inbox-FIRST write (no task ever added yet): that loader hard-requires a
    # top-level ``tasks:`` list, so a file carrying only ``inboxes:`` would
    # make a later ``add_task`` fail-loud. Seed an empty ``tasks:`` list when
    # absent; never touch an existing one (the round-trip preserves it).
    if not isinstance(doc.get("tasks"), list):
        doc["tasks"] = []

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / f".{path.name}.tmp"
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            yaml_rt.dump(doc, handle)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        # Reparse-verify the tmp file before promoting it — never replace
        # the canonical SSOT with bytes that don't round-trip.
        try:
            with tmp_path.open(encoding="utf-8") as verify_handle:
                verify_doc = yaml_rt.load(verify_handle)
        except Exception as verify_exc:  # noqa: BLE001 — any parse fail = abort
            raise RuntimeError(
                f"refusing to replace {path}: tmp file at {tmp_path} did "
                f"not reparse cleanly after dump "
                f"({type(verify_exc).__name__}: {verify_exc}). Canonical "
                f"file left untouched."
            ) from verify_exc
        verify_inboxes = (
            verify_doc.get(_INBOXES_KEY) if isinstance(verify_doc, dict) else None
        )
        if not isinstance(verify_inboxes, dict) or len(verify_inboxes) != len(
            inboxes
        ):
            raise RuntimeError(
                f"refusing to replace {path}: tmp file reparsed with an "
                f"unexpected inboxes payload. Canonical file left untouched."
            )
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _is_duplicate(
    records: list[dict], *, event_type: str, card_id: Any, ts: Any, actor: Any
) -> bool:
    """Return True if ``records`` already holds the (type,card,ts,actor) key.

    The dedup key is ``(event_type, card_id, ts, actor)`` — a re-emit of the
    SAME card-event (same instant, same cause) must not double-enqueue.
    Distinct timestamps (two genuine events) are kept separately.
    """
    for r in records:
        if (
            r.get("event_type") == event_type
            and r.get("card_id") == card_id
            and r.get("ts") == ts
            and r.get("actor") == actor
        ):
            return True
    return False


# --------------------------------------------------------------------------- #
# Public inbox API                                                            #
# --------------------------------------------------------------------------- #
def enqueue(
    recipient_id: str,
    *,
    event_type: str,
    card_id: str,
    body: str,
    actor: str | None,
    ts: str | None = None,
    store: str | Path | None = None,
) -> "dict | None":
    """Append a notification record to ``recipient_id``'s inbox (STANDALONE).

    The standalone delivery sink: always works, no network. Builds a record
    ``{id, event_type, card_id, body, actor, ts, seen: False}`` and appends
    it to the recipient's inbox under the shared store lock.

    Idempotent-ish: dedups on the ``(event_type, card_id, ts, actor)`` key so
    a re-emit of the SAME card-event (same instant + cause) does not
    double-enqueue. A fresh ``ts`` (the default — :func:`_utc_now_iso`) makes
    two genuine events distinct; pass an explicit ``ts`` (the event's own
    timestamp) to make the dedup deterministic across a re-dispatch.

    Parameters
    ----------
    recipient_id : str
        The inbox key — a stable ``u_*`` user id OR a raw-name fallback (the
        same identifier :func:`scitex_todo._notify.resolve_recipients`
        returns). A falsy id is a no-op (returns ``None``).
    event_type : str
        The card-event type (``reassigned`` / ``completed`` / …).
    card_id : str
        The card the event is about.
    body : str
        The human-readable notification text (built by the dispatcher).
    actor : str | None
        Who caused the event (a raw name), or ``None``.
    ts : str | None
        The notification timestamp; defaults to now (``Z``-suffixed ISO).
    store : str | pathlib.Path | None
        Store path override (default: the resolved task store).

    Returns
    -------
    dict | None
        The enqueued record, or ``None`` when nothing was written (a falsy
        ``recipient_id`` or a deduped re-emit).
    """
    if not recipient_id:
        return None
    timestamp = ts if ts is not None else _utc_now_iso()
    path = _resolved_store(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _store_lock(path):
        inboxes = _load_inboxes_section(path)
        records = inboxes.setdefault(recipient_id, [])
        if _is_duplicate(
            records,
            event_type=event_type,
            card_id=card_id,
            ts=timestamp,
            actor=actor,
        ):
            return None
        record = {
            "id": _generate_notification_id(),
            "event_type": event_type,
            "card_id": card_id,
            "body": body,
            "actor": actor,
            "ts": timestamp,
            "seen": False,
        }
        records.append(record)
        _save_inboxes_unlocked(inboxes, path)
        return dict(record)


def poll_inbox(
    recipient_id: str,
    *,
    unseen_only: bool = True,
    mark_seen: bool = False,
    store: str | Path | None = None,
) -> list[dict]:
    """Return ``recipient_id``'s notifications (unseen by default).

    The recipient's PULL read path. Returns a list of notification records
    (each a plain dict). When ``mark_seen`` is set, the returned records are
    flipped ``seen: True`` and persisted atomically under the store lock
    (advancing the cursor) — so a SECOND ``poll_inbox(unseen_only=True)``
    returns nothing new.

    Parameters
    ----------
    recipient_id : str
        The inbox key (``u_*`` id or raw-name fallback). A falsy id or an
        empty inbox yields ``[]``.
    unseen_only : bool
        When ``True`` (default), return only records with ``seen`` falsy;
        when ``False``, return the full history.
    mark_seen : bool
        When ``True``, advance the cursor — flip every RETURNED record to
        ``seen: True`` and persist. Combined with ``unseen_only=True`` this
        is the "drain my unseen notifications" call.
    store : str | pathlib.Path | None
        Store path override (default: the resolved task store).

    Returns
    -------
    list[dict]
        The matching records (a copy each — mutating them does not touch the
        store). Order is append (oldest first).
    """
    if not recipient_id:
        return []
    path = _resolved_store(store)
    if not mark_seen:
        # Read-only fast path — snapshot without locking.
        records = _load_inboxes_section(path).get(recipient_id, [])
        return [
            dict(r) for r in records if (not unseen_only or not r.get("seen"))
        ]
    # mark_seen → read-modify-write under the lock.
    with _store_lock(path):
        inboxes = _load_inboxes_section(path)
        records = inboxes.get(recipient_id, [])
        selected = [r for r in records if (not unseen_only or not r.get("seen"))]
        if not selected:
            return []
        for r in selected:
            r["seen"] = True
        inboxes[recipient_id] = records
        _save_inboxes_unlocked(inboxes, path)
        return [dict(r) for r in selected]


def ack(
    recipient_id: str,
    notification_ids: "list[str] | str",
    store: str | Path | None = None,
) -> list[str]:
    """Mark specific notifications seen (advance the cursor for those ids).

    Idempotent: acking an already-seen or unknown id is a no-op for that id.
    Returns the list of ids actually flipped from unseen → seen (so a caller
    can tell what changed). A falsy ``recipient_id`` or an empty id list is a
    no-op (returns ``[]``).

    Parameters
    ----------
    recipient_id : str
        The inbox key (``u_*`` id or raw-name fallback).
    notification_ids : list[str] | str
        One or more notification ids to mark seen (a bare string is accepted
        as a single-element list).
    store : str | pathlib.Path | None
        Store path override (default: the resolved task store).
    """
    if not recipient_id:
        return []
    if isinstance(notification_ids, str):
        notification_ids = [notification_ids]
    wanted = {nid for nid in (notification_ids or []) if nid}
    if not wanted:
        return []
    path = _resolved_store(store)
    flipped: list[str] = []
    with _store_lock(path):
        inboxes = _load_inboxes_section(path)
        records = inboxes.get(recipient_id, [])
        if not records:
            return []
        for r in records:
            if r.get("id") in wanted and not r.get("seen"):
                r["seen"] = True
                flipped.append(r.get("id"))
        if flipped:
            inboxes[recipient_id] = records
            _save_inboxes_unlocked(inboxes, path)
    return flipped


__all__ = ["ack", "enqueue", "poll_inbox"]

# EOF
