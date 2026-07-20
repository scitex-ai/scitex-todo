#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ONE-TIME legacy sidecar migration (transitional — DELETE after the fleet migrates).

Every sidecar in this package now persists as JSON. This is the ONLY module that
still reads a pre-JSON ``.yaml`` sidecar, and it does so exactly once per file:
the first time a sidecar is accessed and no ``.json`` exists yet, it reads the
legacy file, writes the ``.json`` atomically, and renames the ``.yaml`` to
``<name>.yaml.migrated`` so it is never read again. After that every hot read/
write path is JSON-only.

Keeping the legacy read HERE (not scattered as a permanent fallback across each
sidecar's hot path) means the whole transitional surface is one small module to
delete — after which ``rg yaml src`` is empty. Best-effort: any failure leaves
the legacy file untouched and returns ``False``, so a migration hiccup never
breaks the caller (it simply sees no JSON yet).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def migrate_legacy_sidecar(json_path: str | Path) -> bool:
    """Convert a sibling ``.yaml`` sidecar to ``json_path`` ONCE, if needed.

    No-op (returns ``False``) when ``json_path`` already exists or no legacy
    ``.yaml`` sibling is present. On success writes ``json_path`` atomically,
    renames the legacy file to ``<name>.yaml.migrated``, and returns ``True``.
    """
    jp = Path(json_path)
    if jp.exists():
        return False
    legacy = jp.with_suffix(".yaml")
    if not legacy.exists():
        return False
    try:
        from ._yaml import safe_load

        with legacy.open(encoding="utf-8") as handle:
            data = safe_load(handle)
        jp.parent.mkdir(parents=True, exist_ok=True)
        tmp = jp.with_suffix(jp.suffix + ".tmp")
        tmp.write_text(
            json.dumps(
                data if data is not None else {},
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        os.replace(tmp, jp)
        legacy.rename(legacy.with_name(legacy.name + ".migrated"))
        return True
    except Exception as exc:  # noqa: BLE001 — a migration hiccup must not break a read
        logger.warning("legacy sidecar migration failed for %s: %s", legacy, exc)
        return False


__all__ = ["migrate_legacy_sidecar"]

# EOF
