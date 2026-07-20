#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Options shared by every ``scitex-cards db`` verb.

Declared once, in a module both verb halves (``_db_inspect`` / ``_db_transfer``)
import, so the two files cannot drift apart on the flags that identify the DB
or gate a mutation. The §2 pair (``--dry-run`` / ``--yes``) lives here for the
same reason: five call sites hand-rolling the same two options is exactly how
"the flag means something slightly different over there" starts.
"""

from __future__ import annotations

import click

#: Which database. Every verb in the group takes it.
DB_OPTION = click.option(
    "--db",
    "db_path",
    default=None,
    help="Explicit DB path (default: $SCITEX_CARDS_DB, else ~/.scitex/cards/cards.db).",
)

#: §2 — every mutating verb reports before it acts.
DRY_RUN_OPTION = click.option(
    "--dry-run",
    is_flag=True,
    help="Report what WOULD change and exit 0 without writing anything.",
)

#: §2 — every mutating verb accepts an explicit go-ahead. A no-op today
#: (these verbs never prompt); present so a caller's `--yes` is not an error.
YES_OPTION = click.option(
    "-y",
    "--yes",
    is_flag=True,
    help="Skip confirmation (no-op today — these verbs are non-interactive).",
)

__all__ = ["DB_OPTION", "DRY_RUN_OPTION", "YES_OPTION"]

# EOF
