#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reminder sidecar STATE persistence — load/save only, no sweep logic.

Extracted from :mod:`scitex_todo._reminders` (which stays the sweep
sequencing engine) so the sidecar I/O — a distinct concern the module's own
design notes already called out — lives in one focused, pure-ish module.
The engine imports these back; the public API (``scitex_todo._reminders
.load_reminder_state`` / ``.save_reminder_state``) is unchanged.

State shape: ``{"owners": {owner_name: {count, last_at}}, "cards":
{card_id: {escalated, creator_escalated, digest_count}}}`` — see
:mod:`scitex_todo._reminders` for what each field means and who writes it.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

#: Sidecar file (sibling of ``tasks.yaml``) holding the reminder state.
REMINDER_SIDECAR_NAME = "reminders.yaml"


def _sidecar_path(store: str | Path | None) -> Path:
    """``reminders.yaml`` under the store's ``runtime/`` dir (scitex convention)."""
    from ._paths import runtime_dir

    return runtime_dir(store) / REMINDER_SIDECAR_NAME


def load_reminder_state(store: str | Path | None = None) -> dict[str, dict]:
    """Load the reminder sidecar → ``{"owners": {...}, "cards": {...}}``.

    Missing / unreadable / malformed sidecar → empty sections (fail-soft: a
    bad sidecar must never break a sweep). Always returns both sections so
    callers can index them without guarding. A legacy ``cards:``-only sidecar
    loads leniently (only the ``escalated`` latch is still meaningful; the
    per-owner cadence rebuilds from the first sweep — no migration needed).
    """
    import yaml

    from ._yaml import safe_load

    path = _sidecar_path(store)
    empty = {"owners": {}, "cards": {}}
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return empty
    except OSError as exc:  # noqa: BLE001 — unreadable sidecar must not break the sweep
        logger.warning("reminders: cannot read %s: %s", path, exc)
        return empty
    try:
        data = safe_load(text) or {}
    except yaml.YAMLError as exc:
        logger.warning("reminders: malformed %s: %s", path, exc)
        return empty
    if not isinstance(data, dict):
        return empty
    owners = data.get("owners")
    cards = data.get("cards")
    return {
        "owners": owners if isinstance(owners, dict) else {},
        "cards": cards if isinstance(cards, dict) else {},
    }


def save_reminder_state(state: dict[str, dict], store: str | Path | None = None) -> None:
    """Atomically persist the reminder sidecar (temp + ``os.replace``)."""
    import yaml

    path = _sidecar_path(store)
    payload = {
        "owners": state.get("owners") or {},
        "cards": state.get("cards") or {},
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            yaml.safe_dump(payload, sort_keys=True, allow_unicode=True),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except OSError as exc:  # noqa: BLE001 — a failed state write must not break delivery
        logger.warning("reminders: cannot write %s: %s", path, exc)


__all__ = [
    "REMINDER_SIDECAR_NAME",
    "load_reminder_state",
    "save_reminder_state",
]

# EOF
