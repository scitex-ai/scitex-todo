#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CURRENCY gate — an outdated or broken install ERRORS, never warns.

Companion to the store-local MIN-CLIENT-VERSION FLOOR (``_min_client_version.py``,
"FLOOR #548") — that gate is the OFFLINE backstop: it enforces THIS process's
version against a floor stamped INTO the store, on every DB connection, with
zero network. THIS gate is the freshness+integrity check: it compares the
INSTALLED distribution against the latest release AND validates its payload
(ambiguous dist-info / missing RECORD files — the incident class this closes),
via scitex-dev's dedicated staleness module. Operator directive: outdated or
broken invocations must ERROR, not warn — same ruling as FLOOR #548, applied
at the two process ENTRY points (CLI, MCP server) rather than at DB-open.

DECOUPLING. scitex-dev is an OPTIONAL dependency (the ``currency`` extra) —
a standalone scitex-cards install without scitex-dev keeps working exactly as
before; this gate is then simply a no-op. Never promote it to a hard
dependency.
"""

from __future__ import annotations


def check_currency() -> None:
    """Raise if this install is stale or its payload is broken (CURRENCY gate).

    Provided by scitex-dev >= 0.34.0; silently a no-op when scitex-dev is
    absent so scitex-cards stays standalone (decoupling rule).
    """
    try:
        from scitex_dev.staleness import ensure_current
    except ImportError:
        return
    ensure_current("scitex-cards")


__all__ = ["check_currency"]

# EOF
