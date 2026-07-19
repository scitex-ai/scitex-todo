#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI noun group ``scitex-cards db`` — shadow-SQLite operability verbs (S0).

STAGE S0 (RFC #348): the SQLite DB is a SHADOW bootstrapped from the canonical
YAML store; nothing reads it as truth yet. These verbs are its operability
surface, split by what they are allowed to do:

  * :mod:`scitex_cards._cli._db_inspect`  — ``show-path`` / ``validate`` /
    ``rehearse``: read-only.
  * :mod:`scitex_cards._cli._db_transfer` — ``import`` / ``export`` /
    ``create-snapshot``: everything that writes a file.

This module is the thin orchestrator: it owns the group, mounts the verbs, and
registers the Phase-W aliases for the three that were renamed to satisfy the
noun-verb doctrine (``path`` and ``snapshot`` were nouns; ``verify`` is a §1f
non-canonical synonym for ``validate``). It re-exports the verb commands so
existing ``from ._db import db_import_cmd``-style imports keep resolving.
"""

from __future__ import annotations

import click

from ._compat import deprecated_alias, spec_group_kwargs
from ._db_inspect import db_rehearse_cmd, db_show_path_cmd, db_validate_cmd
from ._db_transfer import db_create_snapshot_cmd, db_export_cmd, db_import_cmd

#: Version that removes the Phase-W verb aliases on this group (doctrine §5).
_REMOVE_IN = "0.20.0"

#: ``old name -> new name`` for the verbs the doctrine made us rename.
_RENAMED = {
    "path": "show-path",  # `path` is a noun; the verb is `show`
    "verify": "validate",  # §1f: one checking verb ecosystem-wide
    "snapshot": "create-snapshot",  # `snapshot` is a noun; the verb is `create`
}


@click.group(
    "db",
    **spec_group_kwargs(
        summary="Shadow-SQLite store verbs (SQLite migration S0, RFC #348).",
        description=(
            "The DB is a SHADOW bootstrapped from the canonical tasks.yaml; "
            "the YAML stays the source of truth and no read/write path uses "
            "the DB yet. `db show-path` prints the resolved DB location, "
            "`db validate` checks schema health, and `db import --from-yaml` "
            "(re)builds the DB from the YAML (idempotent, never modifies "
            "the YAML).",
        ),
        command_categories=(
            ("Inspect", ("show-path", "validate", "rehearse")),
            ("Move data", ("import", "export", "create-snapshot")),
        ),
    ),
)
def db_group() -> None:
    """The ``db`` noun group."""


for _cmd in (
    db_show_path_cmd,
    db_validate_cmd,
    db_rehearse_cmd,
    db_import_cmd,
    db_export_cmd,
    db_create_snapshot_cmd,
):
    db_group.add_command(_cmd)

for _old, _new in _RENAMED.items():
    deprecated_alias(db_group, _old, target=_new, remove_in=_REMOVE_IN)


def register(main: click.Group) -> None:
    """Attach the ``db`` noun group to the root group."""
    main.add_command(db_group)


__all__ = [
    "db_create_snapshot_cmd",
    "db_export_cmd",
    "db_group",
    "db_import_cmd",
    "db_rehearse_cmd",
    "db_show_path_cmd",
    "db_validate_cmd",
    "register",
]

# EOF
