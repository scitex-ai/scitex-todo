#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fast, safe YAML reads — the SSOT loader for every store READ path.

``yaml.safe_load`` uses pyyaml's PURE-PYTHON ``SafeLoader`` by default, which
is dramatically slower than the libyaml-backed ``CSafeLoader`` on the sizes the
shared store reaches: an ~800 KB / ~500-card ``tasks.yaml`` parses in ~0.13 s
with ``CSafeLoader`` vs ~1.8 s pure-python (~14x). Hot read paths re-parse the
full store per call (e.g. ``_users.load_users`` on every ``resolve_user``), so
that gap compounds — a single board comment re-read the store several times and
took ~12.7 s before this loader was introduced.

``CSafeLoader`` has IDENTICAL safe semantics to ``SafeLoader`` (no arbitrary
Python-object construction) — it is a pure speed swap. When libyaml is not
built into the installed pyyaml, :data:`_SAFE_LOADER` falls back to the
pure-python ``SafeLoader`` so behaviour is unchanged, just slower.

Reads only. WRITES keep their ruamel round-trip (comment/key-order preserving);
this module is not involved there.
"""

from __future__ import annotations

from typing import IO, Any

import yaml

#: Fastest available SAFE loader: libyaml ``CSafeLoader`` when present, else the
#: pure-python ``SafeLoader``. Resolved once at import.
_SAFE_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)


def safe_load(stream: str | bytes | IO[Any]) -> Any:
    """Drop-in for :func:`yaml.safe_load` using the fastest safe loader.

    Accepts the same inputs as ``yaml.safe_load`` (a string, bytes, or an open
    text/binary file handle) and returns the same parsed structure.
    """
    return yaml.load(stream, Loader=_SAFE_LOADER)


__all__ = ["safe_load"]

# EOF
