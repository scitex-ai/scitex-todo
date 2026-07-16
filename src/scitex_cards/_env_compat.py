#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``SCITEX_CARDS_*`` / ``SCITEX_TODO_*`` environment dual-read (transition).

The package rename (scitex-todo -> scitex-cards, 2026-07-16) renames every
environment-variable prefix from ``SCITEX_TODO_`` to ``SCITEX_CARDS_``. The
codebase still READS the old names at ~50 sites; rewriting them all inside
the mechanical rename PR would tangle two risky changes into one diff. So
this module — imported FIRST by ``scitex_cards.__init__`` — MIRRORS every
``SCITEX_CARDS_<X>`` value onto ``SCITEX_TODO_<X>`` in this process's
environment:

- a shell that already exports the NEW names (the operator's ``.envrc``)
  works today, and child processes inherit the mirrored pair;
- the un-cutover fleet exporting only OLD names keeps working, with ONE
  deprecation warning per process (operator-requested, 2026-07-16);
- when BOTH names are set to different values, the NEW name wins, loudly.

The read sites migrate to the new names in the store-engine stage; this
mirror then inverts and finally dies together with the ``scitex_todo`` shim.
"""

from __future__ import annotations

import logging
import os
from typing import MutableMapping

logger = logging.getLogger(__name__)

NEW_PREFIX = "SCITEX_CARDS_"
OLD_PREFIX = "SCITEX_TODO_"


def mirror_env(environ: MutableMapping[str, str] = os.environ) -> None:
    """Mirror ``SCITEX_CARDS_*`` onto ``SCITEX_TODO_*`` (new names win).

    Also emits one aggregate deprecation warning when old-prefix names are
    in use without a new-prefix twin — the signal the operator asked for to
    find un-migrated exports during the transition window.
    """
    for new in [k for k in environ if k.startswith(NEW_PREFIX)]:
        old = OLD_PREFIX + new[len(NEW_PREFIX) :]
        if environ.get(old) not in (None, environ[new]):
            logger.warning(
                "%s=%r overrides %s=%r (the SCITEX_CARDS_* name wins)",
                new,
                environ[new],
                old,
                environ[old],
            )
        environ[old] = environ[new]

    old_only = sorted(
        k
        for k in environ
        if k.startswith(OLD_PREFIX)
        and NEW_PREFIX + k[len(OLD_PREFIX) :] not in environ
    )
    if old_only:
        logger.warning(
            "deprecated SCITEX_TODO_* environment names in use (%s); "
            "rename them to SCITEX_CARDS_* — the old prefix is honoured "
            "for one transition window only",
            ", ".join(old_only),
        )


mirror_env()

# EOF
