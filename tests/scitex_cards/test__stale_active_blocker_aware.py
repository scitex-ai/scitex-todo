#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The stale sweep must not nudge an owner about work they cannot move.

Regression cover for the 2026-07-12 finding: ``detect_stale_active`` keyed on
STATUS alone (``{in_progress, blocked}``) and ignored the BLOCKER, so every
blocked card nudged its owner every 2 h — including cards blocked on a
dependency, a compute job, another agent, or an operator decision. All 8 of
scitex-cards's own "stale" cards were externally blocked; not one was
actionable. 12 identical nudges a day about work you are powerless to advance
is not a signal, it is training to ignore the channel — which is precisely how
the REAL nudge gets missed.

The fix partitions the ``blocked`` rows:
  * owner-actionable  -> the TIGHT clock ("why haven't you moved this?")
  * externally blocked -> the LENIENT clock ("has your blocker cleared?")
"""

from __future__ import annotations

import datetime as _dt

import pytest

from scitex_cards._stale_active import (
    EXTERNAL_BLOCKERS,
    blocked_external_nudge_line,
    detect_blocked_external,
    detect_stale_active,
    is_externally_blocked,
    is_owner_actionable,
    stale_active_nudge_line,
)

NOW = _dt.datetime(2026, 7, 12, 12, 0, 0, tzinfo=_dt.timezone.utc)
LONG_AGO = "2026-07-01T00:00:00Z"  # ~11 days -> stale on ANY threshold here


def _card(cid: str, status: str, blocker: str | None = None, **kw) -> dict:
    t = {
        "id": cid,
        "title": cid,
        "status": status,
        "agent": "alice",
        "last_activity": kw.pop("last_activity", LONG_AGO),
    }
    if blocker is not None:
        t["blocker"] = blocker
    t.update(kw)
    return t


# --------------------------------------------------------------------------
# The classifier.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("blocker", sorted(EXTERNAL_BLOCKERS))
def test_every_external_blocker_is_outside_the_owners_control(blocker):
    """Each blocker in the enum's external set marks the card un-actionable."""
    assert is_externally_blocked(_card("c", "blocked", blocker)) is True
    assert is_owner_actionable(_card("c", "blocked", blocker)) is False


def test_legacy_dep_alias_is_classified_external_not_dropped_to_tight_clock():
    """``dep`` is the legacy spelling of ``dependency``.

    A not-yet-normalized row must NOT fall through to the tight clock — that
    would nudge the owner every 2 h purely because of a spelling variant.
    """
    assert is_externally_blocked(_card("c", "blocked", "dep")) is True


def test_blocked_with_no_blocker_named_stays_owner_actionable():
    """Nobody said WHY it is blocked — and saying so IS the owner's job."""
    assert is_externally_blocked(_card("c", "blocked")) is False
    assert is_owner_actionable(_card("c", "blocked")) is True


def test_blocked_none_is_owner_actionable():
    """Explicit ``none`` means "I looked, there is no blocker" -> owner's move."""
    assert is_externally_blocked(_card("c", "blocked", "none")) is False
    assert is_owner_actionable(_card("c", "blocked", "none")) is True


def test_in_progress_is_owner_actionable():
    assert is_owner_actionable(_card("c", "in_progress")) is True


def test_done_is_not_active_at_all():
    assert is_owner_actionable(_card("c", "done")) is False


# --------------------------------------------------------------------------
# The two sweeps.
# --------------------------------------------------------------------------


def test_tight_sweep_never_reports_an_externally_blocked_card_however_old():
    """The core regression: an owner is not nagged about a wall they cannot move."""
    tasks = [_card("blocked-on-dep", "blocked", "dependency")]
    assert detect_stale_active(tasks, now=NOW) == {}


def test_tight_sweep_still_reports_forgotten_in_progress_work():
    """The true signal must survive the fix — this is what the sweep is FOR."""
    got = detect_stale_active([_card("forgotten", "in_progress")], now=NOW)
    assert [c.id for c in got["alice"]] == ["forgotten"]


def test_tight_sweep_reports_blocked_with_no_reason_given():
    got = detect_stale_active([_card("why-blocked", "blocked")], now=NOW)
    assert [c.id for c in got["alice"]] == ["why-blocked"]


def test_lenient_sweep_reports_the_externally_blocked_card():
    got = detect_blocked_external(
        [_card("blocked-on-dep", "blocked", "dependency")], now=NOW
    )
    assert [c.id for c in got["alice"]] == ["blocked-on-dep"]


def test_lenient_sweep_holds_fire_inside_its_threshold():
    """Blocked an hour ago is not yet worth a "has it cleared?" ping."""
    fresh = _card(
        "just-blocked",
        "blocked",
        "dependency",
        last_activity="2026-07-12T11:30:00Z",  # 30 min before NOW
    )
    assert detect_blocked_external([fresh], now=NOW) == {}


def test_lenient_sweep_ignores_in_progress():
    """in_progress is the tight sweep's business, never the blocker-check's."""
    assert detect_blocked_external([_card("wip", "in_progress")], now=NOW) == {}


# --------------------------------------------------------------------------
# The invariant that makes the split safe.
# --------------------------------------------------------------------------


def test_the_two_sweeps_partition_the_blocked_rows_and_never_double_report():
    """No card may appear in BOTH sweeps.

    If they overlapped, the "fix" would just double the noise it set out to
    remove — the owner would get nagged on the tight clock AND asked about the
    blocker on the lenient one, for the same card.
    """
    tasks = [
        _card("wip", "in_progress"),
        _card("blocked-unexplained", "blocked"),
        _card("blocked-none", "blocked", "none"),
        _card("blocked-dep", "blocked", "dependency"),
        _card("blocked-dep-legacy", "blocked", "dep"),
        _card("blocked-op", "blocked", "operator-decision"),
        _card("blocked-compute", "blocked", "compute"),
        _card("blocked-agent", "blocked", "agent-wait"),
        _card("done", "done"),
    ]
    tight = {c.id for v in detect_stale_active(tasks, now=NOW).values() for c in v}
    lenient = {
        c.id for v in detect_blocked_external(tasks, now=NOW).values() for c in v
    }

    assert tight & lenient == set(), "a card was reported by BOTH sweeps"
    assert tight == {"wip", "blocked-unexplained", "blocked-none"}
    assert lenient == {
        "blocked-dep",
        "blocked-dep-legacy",
        "blocked-op",
        "blocked-compute",
        "blocked-agent",
    }
    # `done` is active in neither.
    assert "done" not in (tight | lenient)


# --------------------------------------------------------------------------
# Wording — the lines must say something the recipient can act on.
# --------------------------------------------------------------------------


def test_blocked_check_line_asks_a_question_it_does_not_order_a_reconcile():
    """Telling an owner to "reconcile" a card they cannot move is an instruction
    they cannot follow. The blocker-check must ASK whether the wall is still up."""
    cards = detect_blocked_external(
        [_card("blocked-on-dep", "blocked", "dependency")], now=NOW
    )["alice"]
    line = blocked_external_nudge_line("alice", cards)

    assert "BLOCKED-CHECK" in line
    assert "has the blocker cleared?" in line
    assert "blocked-on-dep" in line
    # It must NOT borrow the tight sweep's reprimanding verb.
    assert "reconcile" not in line


def test_stale_active_line_names_its_narrowed_scope_honestly():
    """The line used to claim "in_progress/blocked" — which is now a lie, since
    externally-blocked cards are excluded. A nudge that misdescribes its own
    scope teaches the reader to distrust it."""
    cards = detect_stale_active([_card("forgotten", "in_progress")], now=NOW)["alice"]
    line = stale_active_nudge_line("alice", cards)

    assert "STALE-ACTIVE" in line
    assert "you can act on now" in line
    assert "forgotten" in line


def test_line_composers_are_still_importable_from_the_original_module():
    """The split moved them to ``_stale_active_lines``; the re-export must hold
    so notifyd / the CLI / out-of-tree importers keep working unchanged."""
    from scitex_cards._stale_active import (  # noqa: F401
        NUDGE_ID_CAP,
        pending_backlog_nudge_line,
    )

    assert NUDGE_ID_CAP == 12
