#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Unit + integration coverage for the cheap post-dump store-write verify.

Fix B2 replaced the full ``safe_load`` construct-reparse in
``_save_doc_unlocked`` (which cost ~2.3 s / ~159k objects on the live 9.2 MB
store) with a two-part cheap integrity check in
``_store_verify._verify_dumped_tmp``: a byte-length check + a libyaml
EVENT-SCAN reparse to ``StreamEndEvent``. This module pins scitex-dev's
THREE non-negotiables for that change:

  (a) A truncation-MID-SCALAR corruption that is the SAME byte length as a
      valid dump: the length check PASSES yet the event-scan CATCHES it —
      proving the event-scan is load-bearing, not redundant with length.
  (b) The 2026-06-13 guard intent: a tmp truncated mid-document (the incident
      shape — file ended mid-string) RAISES, i.e. the check proves the bytes
      are FULLY reparseable (reach StreamEnd), not merely non-empty.
  (c) The event-scan MUST reach stream-end, not stop at the first event: a
      valid (large / multi-doc) file is accepted only after full consumption;
      a file whose first events parse fine but then hits EOF mid-stream RAISES.

Plus the happy path (a normal valid write verifies AND promotes via
os.replace) and the byte-length short-write guard.

Real tmp files, NO mocks (STX-NM / PA-306).
"""

from __future__ import annotations

import time

import pytest
import yaml

from scitex_cards import _model
from scitex_cards._store_verify import StoreWriteVerifyError, _verify_dumped_tmp
from scitex_cards._yaml import _SAFE_LOADER, safe_dump


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _valid_doc(n: int = 1) -> dict:
    """A structurally valid store doc with ``n`` tasks."""
    return {
        "tasks": [
            {"id": f"t{i}", "title": f"Title {i}", "status": "pending"}
            for i in range(n)
        ],
        "users": {"alice": {"role": "dev"}},
    }


def _write(path, text: str) -> None:
    """Write raw text to ``path`` (byte-exact, utf-8)."""
    with path.open("w", encoding="utf-8") as handle:
        handle.write(text)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
class TestHappyPath:
    def test_valid_dump_verifies_without_raising(self, tmp_path):
        dumped = safe_dump(_valid_doc(5))
        tmp = tmp_path / ".tasks.yaml.tmp"
        _write(tmp, dumped)
        # Returns None, does not raise.
        assert _verify_dumped_tmp(tmp, dumped) is None

    def test_save_doc_unlocked_promotes_on_valid_write(self, tmp_path, monkeypatch):
        # End-to-end: the write path still atomically promotes (os.replace)
        # a valid doc into the canonical slot.
        monkeypatch.setenv("SCITEX_TODO_STORE_GIT_AUTOCOMMIT", "0")
        store = tmp_path / "tasks.yaml"
        doc = _valid_doc(3)
        with _model._store_lock(store):
            _model._save_doc_unlocked(doc, store)
        # The canonical file exists, the tmp sidecar is gone, content round-trips.
        assert store.exists()
        assert not (tmp_path / ".tasks.yaml.tmp").exists()
        reloaded = _model.load_doc(store)
        assert {t["id"] for t in reloaded["tasks"]} == {"t0", "t1", "t2"}
        assert reloaded["users"] == {"alice": {"role": "dev"}}


# ---------------------------------------------------------------------------
# (a) same-length truncation MID-SCALAR: length passes, event-scan catches
# ---------------------------------------------------------------------------
class TestSameLengthMidScalarCorruption:
    """The event-scan is load-bearing beyond the byte-length check."""

    def test_mid_scalar_corruption_same_length_is_caught(self, tmp_path):
        # A valid dump whose LAST scalar is `status: pending`.
        good = safe_dump({"tasks": [{"id": "a", "title": "A", "status": "pending"}]})
        assert "status: pending" in good

        # Corrupt IN PLACE, byte-for-byte same length: replace the first char
        # of the last scalar's value with a double-quote. This opens a
        # double-quoted scalar that is never closed before EOF -> the parser
        # errors mid/at-end-of-scalar (exactly the 2026-06-13 shape) even
        # though the file is the SAME LENGTH as the valid dump.
        bad = good.replace("status: pending", 'status: "ending', 1)
        assert len(bad.encode("utf-8")) == len(good.encode("utf-8"))  # same length
        assert bad != good

        tmp = tmp_path / ".tasks.yaml.tmp"
        _write(tmp, bad)

        # 1) The byte-length check ALONE would PASS: on-disk size == len(bad).
        #    (Prove it: a length-only assertion is satisfied.)
        import os

        assert os.stat(tmp).st_size == len(bad.encode("utf-8"))

        # 2) Yet _verify_dumped_tmp still RAISES — the event-scan catches the
        #    mid-scalar corruption the length check cannot see.
        with pytest.raises(StoreWriteVerifyError):
            _verify_dumped_tmp(tmp, bad)

    def test_the_corruption_really_is_unparseable(self, tmp_path):
        # Independent confirmation that the crafted `bad` bytes are genuinely
        # malformed YAML (so the test above isn't passing for a spurious
        # reason) — a plain safe_load also rejects them.
        good = safe_dump({"tasks": [{"id": "a", "title": "A", "status": "pending"}]})
        bad = good.replace("status: pending", 'status: "ending', 1)
        with pytest.raises(yaml.YAMLError):
            yaml.load(bad, Loader=_SAFE_LOADER)


# ---------------------------------------------------------------------------
# (b) truncated mid-document (the 2026-06-13 incident shape) -> RAISES
# ---------------------------------------------------------------------------
class TestTruncatedMidDocument:
    """Prove the written bytes are FULLY reparseable, not merely non-empty."""

    def test_truncated_mid_scalar_raises(self, tmp_path):
        # Classic incident shape: the file ends in the MIDDLE of a quoted
        # scalar (the canonical file "ended mid-string at line ~2784"). The
        # opening quote is never closed -> the parser cannot reach StreamEnd.
        truncated = "tasks:\n- id: a\n  title: 'unterminated scalar that never clo"
        tmp = tmp_path / ".tasks.yaml.tmp"
        _write(tmp, truncated)
        with pytest.raises(StoreWriteVerifyError):
            _verify_dumped_tmp(tmp, truncated)

    def test_truncation_of_a_real_dump_raises(self, tmp_path):
        # Take a REAL valid dump whose last field is a quoted scalar, then cut
        # it off inside that scalar. Length matches what we pass as `dumped`
        # (so the length check passes) — the event-scan is what rejects it.
        doc = {
            "tasks": [
                {
                    "id": "a",
                    "title": "A",
                    # A value with ': ' forces pyyaml to quote it, so a
                    # truncation inside it leaves an unterminated quoted scalar.
                    "note": "colon: forces quoting so truncation breaks parse",
                    "status": "pending",
                }
            ]
        }
        good = safe_dump(doc)
        # Cut off the trailing portion so we end inside the quoted `note`.
        cut = good[: good.index("forces")]  # ends mid-quoted-scalar
        tmp = tmp_path / ".tasks.yaml.tmp"
        _write(tmp, cut)
        with pytest.raises(StoreWriteVerifyError):
            _verify_dumped_tmp(tmp, cut)


# ---------------------------------------------------------------------------
# (c) event-scan must REACH stream-end, not stop at the first event
# ---------------------------------------------------------------------------
class TestReachesStreamEnd:
    def test_large_valid_doc_accepted_after_full_consumption(self, tmp_path):
        # A large multi-item doc: it is accepted (no raise) ONLY because the
        # scan consumes every event through StreamEndEvent. If the loop stopped
        # at the first event this doc could not be distinguished from one that
        # truncates after item 1 (see next test).
        dumped = safe_dump(_valid_doc(2000))
        tmp = tmp_path / ".tasks.yaml.tmp"
        _write(tmp, dumped)
        assert _verify_dumped_tmp(tmp, dumped) is None

    def test_first_events_ok_then_eof_mid_stream_raises(self, tmp_path):
        # The first several events parse fine (StreamStart, DocStart, mapping
        # start, the 'tasks' key, sequence start, the first task's scalars)
        # and THEN the stream ends mid-way through the second task's quoted
        # scalar. A first-event-only check would wrongly accept this; the
        # to-StreamEnd scan RAISES.
        partial = (
            "tasks:\n"
            "- id: a\n"
            "  title: A\n"
            "  status: pending\n"
            "- id: b\n"
            "  title: 'unterminated for b"  # EOF mid-quoted-scalar
        )
        tmp = tmp_path / ".tasks.yaml.tmp"
        _write(tmp, partial)

        # Sanity: the early events DO parse before the failure point — proving
        # this is a "parses a few events then hits EOF" case, not an immediate
        # syntax error at event 0.
        events = []
        with pytest.raises(yaml.YAMLError):
            with tmp.open(encoding="utf-8") as fh:
                for ev in yaml.parse(fh, Loader=_SAFE_LOADER):
                    events.append(ev)
        assert len(events) >= 4  # got past the stream/doc/mapping start events
        assert not any(isinstance(e, yaml.events.StreamEndEvent) for e in events)

        # And the helper rejects it.
        with pytest.raises(StoreWriteVerifyError):
            _verify_dumped_tmp(tmp, partial)


# ---------------------------------------------------------------------------
# Byte-length short-write guard
# ---------------------------------------------------------------------------
class TestByteLengthGuard:
    def test_short_write_raises_on_length_mismatch(self, tmp_path):
        # The on-disk file is SHORTER than the string we intended to write
        # (short / partial / disk-full write). The length check alone rejects
        # it before the event-scan even runs.
        dumped = safe_dump(_valid_doc(3))
        tmp = tmp_path / ".tasks.yaml.tmp"
        _write(tmp, dumped[: len(dumped) // 2])  # only half the bytes landed
        with pytest.raises(StoreWriteVerifyError) as exc:
            _verify_dumped_tmp(tmp, dumped)
        assert "size" in str(exc.value)


# ---------------------------------------------------------------------------
# Perf note (informational) — evidences that the event-scan verify is cheaper
# than the full ``safe_load`` construct-reparse it replaces.
# ---------------------------------------------------------------------------
class TestPerfShape:
    def test_event_scan_verify_is_faster_than_full_safe_load(self, tmp_path):
        # The whole point of Fix B2: prove parseability WITHOUT constructing
        # the document's Python object graph. On a synthetic realistic-shape
        # store the event-scan is measured ~2.4x faster than the old full
        # ``safe_load`` construct-reparse (see PR body for numbers). We assert
        # the RELATIVE property — event-scan strictly faster than the old
        # approach — rather than an absolute wall-clock ceiling, so the test
        # evidences the improvement without being flaky on loaded CI.
        import io

        # Realistic card shape (notes + comments) so the construct cost the old
        # path paid is represented, not a degenerate all-tiny-scalars doc.
        doc = {
            "tasks": [
                {
                    "id": f"t{i}",
                    "title": f"Title {i}",
                    "status": "pending",
                    "note": "lorem ipsum dolor sit amet " * 12,
                    "comments": [
                        {"text": "a comment here " * 4, "author": "x", "ts": "2026-01-01"}
                        for _ in range(3)
                    ],
                }
                for i in range(700)
            ],
            "users": {"alice": {"role": "dev"}},
        }
        dumped = safe_dump(doc)
        tmp = tmp_path / ".tasks.yaml.tmp"
        _write(tmp, dumped)

        # OLD approach: full safe_load construct-reparse (what we replaced).
        t0 = time.perf_counter()
        yaml.load(io.StringIO(dumped), Loader=_SAFE_LOADER)
        t_full_load = time.perf_counter() - t0

        # NEW approach: the event-scan verify.
        t0 = time.perf_counter()
        _verify_dumped_tmp(tmp, dumped)
        t_event_scan = time.perf_counter() - t0

        # The event-scan must be strictly cheaper than the full construct it
        # supersedes (measured ~2.4x; assert only < to stay non-flaky).
        assert t_event_scan < t_full_load
