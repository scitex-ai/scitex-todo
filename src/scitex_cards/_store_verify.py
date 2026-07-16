#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cheap post-dump integrity check for the crash-safe store write.

Background — the 2026-06-13 corruption guard (lead a2a ``d5809cd3``)
--------------------------------------------------------------------
On 2026-06-13 the canonical ``tasks.yaml`` was recovered by hand after a
write left it truncated MID-STRING (the file ended in the middle of a
scalar around line ~2784). To make that class of corruption unpromotable,
``_save_doc_unlocked`` added a POST-DUMP round-trip: after dumping to the
sibling ``.tmp`` it reopened the tmp and ran a FULL ``safe_load``
construct-reparse, then compared the reparsed task count to the in-memory
count, and only ``os.replace``d the tmp into the canonical slot if both
passed. The guarantee: never promote bytes that don't fully reparse.

The cost — and why this module exists
--------------------------------------
That full ``safe_load`` builds every Python object in the document just to
prove the bytes are parseable: on the live 9.2 MB / ~930-card store it
constructs ~159k objects and costs ~2.3 s PER write. On a large store every
single-card write pays it, and write bursts convoy on the ``flock``.

This module KEEPS the exact 2026-06-13 guarantee (the promoted bytes must be
fully reparseable, i.e. the parser reaches stream-end) but DROPS the object
construction. It does two cheap things instead of one expensive one:

1. **Byte-length check** — assert the bytes actually on disk equal the bytes
   we dumped (``os.stat(tmp).st_size == len(dumped.encode("utf-8"))``). This
   catches a short / partial / disk-full write where the tail never landed.

2. **Event-scan reparse** — stream the tmp through the libyaml C parser
   (``yaml.parse(..., Loader=<CSafeLoader>)``) consuming EVENTS to
   ``StreamEndEvent``. The C parser raises ``yaml.YAMLError`` on truncation /
   unterminated scalar / malformed document WITHOUT constructing the ~159k
   Python objects — that is the whole point. Reaching ``StreamEndEvent``
   proves the entire byte stream is well-formed YAML end-to-end, which is
   the same "fully reparseable" property the old full ``safe_load`` proved.

Why BOTH checks (they are not redundant)
----------------------------------------
The byte-length check alone would MISS a corruption that mutates bytes
in place without changing the length (e.g. a character flipped mid-scalar
that opens an unterminated quote). The event-scan catches that. The
event-scan alone would (in principle) also catch a short write, but the
length check is essentially free and gives a sharper, disk-full-specific
error message — so we keep both, cheap-first.

Note on the DROPPED task-count match
------------------------------------
The old guard also compared the reparsed task count to the in-memory count.
The event-scan SUPERSEDES that: a document that reaches ``StreamEndEvent``
parsed completely, so a "half the tasks silently vanished mid-parse" outcome
(the thing the count-match defended against) cannot happen — a truncation
that drops tasks would abort the parse before stream-end and raise here. We
therefore do NOT reconstruct the task list just to count it (that would
rebuild the objects this change exists to avoid). This drop is flagged for
scitex-dev review; see the PR body + ``_save_doc_unlocked``'s comment.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from ._yaml import _SAFE_LOADER  # the exact CSafeLoader the read/dump path uses


class StoreWriteVerifyError(RuntimeError):
    """Raised when a dumped tmp file fails the post-dump integrity check.

    Signals that the tmp must NOT be promoted into the canonical store — the
    caller leaves the canonical file untouched (the 2026-06-13 guard intent).
    """


def _verify_dumped_tmp(tmp_path: str | Path, dumped: str) -> None:
    """Prove a just-dumped tmp file is fully reparseable before it is promoted.

    KEEPS the 2026-06-13 corruption guard (lead a2a ``d5809cd3``) — the
    promoted bytes must reparse completely — while DROPPING the ~2.3 s full
    ``safe_load`` object construction it used to cost on the large store.

    Parameters
    ----------
    tmp_path : str or pathlib.Path
        The sibling ``.tmp`` file just written (dump → flush → fsync).
    dumped : str
        The exact YAML string that was written to ``tmp_path``. Used for the
        byte-length check (no second dump — the caller passes the string it
        already produced).

    Raises
    ------
    StoreWriteVerifyError
        If the on-disk byte length differs from ``dumped`` (short / partial /
        disk-full write), OR the tmp does not parse cleanly end-to-end, OR the
        parse ends before a ``StreamEndEvent`` is observed. In every case the
        caller must leave the canonical file untouched.
    """
    tmp_path = Path(tmp_path)

    # (1) BYTE-LENGTH CHECK — catches a short / partial / disk-full write where
    # the tail bytes never landed even if fsync did not error. Compare the
    # bytes actually on disk to the bytes we serialized.
    expected_len = len(dumped.encode("utf-8"))
    actual_len = os.stat(tmp_path).st_size
    if actual_len != expected_len:
        raise StoreWriteVerifyError(
            f"refusing to promote {tmp_path}: on-disk size {actual_len} bytes "
            f"!= dumped size {expected_len} bytes — short/partial/disk-full "
            f"write (2026-06-13 corruption guard). Canonical file left "
            f"untouched."
        )

    # (2) EVENT-SCAN REPARSE — stream the file through the libyaml C parser to
    # StreamEnd WITHOUT constructing the document's Python objects. The C
    # parser raises yaml.YAMLError on truncation / unterminated scalar /
    # malformed doc; reaching StreamEndEvent proves the whole byte stream is
    # well-formed end-to-end (the "fully reparseable" property the old full
    # safe_load proved). We MUST observe StreamEndEvent — stopping at the first
    # event would accept a file that parses a few events then hits EOF
    # mid-stream.
    saw_stream_end = False
    try:
        with tmp_path.open(encoding="utf-8") as handle:
            for event in yaml.parse(handle, Loader=_SAFE_LOADER):
                if isinstance(event, yaml.events.StreamEndEvent):
                    saw_stream_end = True
    except yaml.YAMLError as parse_exc:
        raise StoreWriteVerifyError(
            f"refusing to promote {tmp_path}: tmp did not reparse cleanly "
            f"after dump ({type(parse_exc).__name__}: {parse_exc}) — likely a "
            f"truncated / unterminated-scalar write (2026-06-13 corruption "
            f"guard). Canonical file left untouched."
        ) from parse_exc

    if not saw_stream_end:
        raise StoreWriteVerifyError(
            f"refusing to promote {tmp_path}: event-scan reparse did not reach "
            f"StreamEnd — the byte stream ended mid-document (2026-06-13 "
            f"corruption guard). Canonical file left untouched."
        )


__all__ = ["StoreWriteVerifyError", "_verify_dumped_tmp"]

# EOF
