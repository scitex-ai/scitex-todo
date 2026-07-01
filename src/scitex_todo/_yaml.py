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

The WRITE path uses the mirror-image :func:`safe_dump` — a libyaml-backed
``CSafeDumper`` (falling back to pure-python ``SafeDumper``). This replaces the
old ruamel round-trip dump, which re-parsed + re-serialized the entire 2.3 MB
store per single-card write (~20 s). ``CSafeDumper`` serializes the same doc in
well under a second. The trade-off: the ~41 hand-written header/section comments
in the store are NOT preserved. That is accepted — the store is machine-managed,
so the comments carry no data. Key/insertion order IS preserved
(``sort_keys=False``).
"""

from __future__ import annotations

from typing import IO, Any

import yaml

#: Fastest available SAFE loader: libyaml ``CSafeLoader`` when present, else the
#: pure-python ``SafeLoader``. Resolved once at import.
_SAFE_LOADER = getattr(yaml, "CSafeLoader", yaml.SafeLoader)

#: Fastest available SAFE dumper: libyaml ``CSafeDumper`` when present, else the
#: pure-python ``SafeDumper``. Resolved once at import.
_SAFE_DUMPER = getattr(yaml, "CSafeDumper", yaml.SafeDumper)


def safe_load(stream: str | bytes | IO[Any]) -> Any:
    """Drop-in for :func:`yaml.safe_load` using the fastest safe loader.

    Accepts the same inputs as ``yaml.safe_load`` (a string, bytes, or an open
    text/binary file handle) and returns the same parsed structure.
    """
    return yaml.load(stream, Loader=_SAFE_LOADER)


def safe_dump(data: Any, stream: IO[Any] | None = None) -> str | None:
    """Fast, block-style YAML dump using the fastest safe dumper.

    Mirrors :func:`safe_load`. Emits readable block-style YAML preserving
    key/insertion order (``sort_keys=False``), never flow-style collections,
    with unicode written through verbatim and a very wide line so scalars are
    not wrapped mid-value.

    Parameters
    ----------
    data : Any
        Plain ``dict``/``list``/``str``/... structure to serialize. Must NOT
        contain ruamel ``CommentedMap``/``CommentedSeq`` nodes — the safe
        dumper only knows the basic Python types.
    stream : file-like, optional
        If given, YAML is written to it and ``None`` is returned. If omitted,
        the YAML string is returned.
    """
    return yaml.dump(
        data,
        stream,
        Dumper=_SAFE_DUMPER,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        width=4096,
    )


__all__ = ["safe_dump", "safe_load"]

# EOF
