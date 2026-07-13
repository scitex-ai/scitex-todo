#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end tests for ``scitex_cards._store_wip`` — the WIP gate's add-path
enforcement, and the emergency-recording exemption it now carries.

Real stores on disk, real ``add_task`` (no mocks; STX-NM / PA-306). AAA +
one-assertion-per-test (STX-TQ002 / STX-TQ007).

The incident these pin (2026-07-12): the operator escalated a P0 and the board
REFUSED the card — "WIP gate refuses add: … Close existing tasks before adding
more." A throughput cap was sitting on the emergency-recording path, and its
cheapest workaround was to close cards you had not finished. Priority <= 1 is
now never gated; a card admitted over the cap says so, on the card.
"""

from __future__ import annotations

import pytest

from scitex_cards._model import TaskValidationError
from scitex_cards._paths import ENV_TASKS
from scitex_cards._store import add_task
from scitex_cards._store_wip import (
    EXEMPT_PRIORITY_MAX,
    OVERRIDE_COMMENT_KIND,
    is_priority_exempt,
)
from scitex_cards._throughput import ENV_WIP_LIMIT


@pytest.fixture()
def over_cap(tmp_path, env, monkeypatch):
    """An agent ``a`` sitting FAR over its refuse threshold (limit 1 → 2x = 2).

    Yields the store path. Seeded by hand-writing the YAML: the gate itself
    refuses seeding past 2x through ``add_task``, which is the point.
    """
    env.set(ENV_WIP_LIMIT, "1")
    store = tmp_path / "tasks.yaml"
    rows = "\n".join(
        f"  - id: wip-{i}\n"
        f"    title: in flight {i}\n"
        f"    status: in_progress\n"
        f"    agent: a\n"
        f"    assignee: a\n"
        for i in range(8)
    )
    store.write_text(f"tasks:\n{rows}")
    # add_task's post-write card-event dispatcher resolves the DEFAULT store,
    # not the one passed in — without this the test would read and write the
    # operator's live ~/.scitex/todo/tasks.yaml.
    monkeypatch.setenv(ENV_TASKS, str(store))
    return store


class TestPriorityExemptPredicate:
    """LOWER priority = MORE urgent. The exemption band is P0/P1."""

    def test_priority_one_is_exempt(self):
        # Arrange / Act / Assert — 21 of 28 incident cards on the live store
        # are priority 1; this is THE value the gate must never block.
        assert is_priority_exempt(1) is True

    def test_priority_zero_is_exempt(self):
        # Arrange / Act / Assert — 0 is the most urgent value present.
        assert is_priority_exempt(0) is True

    def test_priority_three_is_not_exempt(self):
        # Arrange / Act / Assert — ordinary work stays capped.
        assert is_priority_exempt(3) is False

    def test_missing_priority_is_not_exempt(self):
        # Arrange / Act / Assert — the exemption is for DECLARED emergencies.
        assert is_priority_exempt(None) is False

    def test_high_number_is_not_exempt(self):
        # Arrange / Act / Assert — priority 8 is the LEAST important card, not
        # the most; exempting it would have been exactly backwards.
        assert is_priority_exempt(8) is False

    def test_true_is_not_exempt(self):
        # Arrange / Act / Assert — bool is an int in Python; a truthy flag
        # fumbled into `priority` must not buy an emergency exemption.
        assert is_priority_exempt(True) is False

    def test_exempt_band_is_p0_p1(self):
        # Arrange / Act / Assert — pin the constant itself.
        assert EXEMPT_PRIORITY_MAX == 1


class TestIncidentIsAlwaysRecordable:
    """priority <= 1 is never gated — no flag to remember mid-outage."""

    def test_p1_in_progress_card_is_accepted_over_the_cap(self, over_cap):
        # Arrange — agent is at 8 in-flight against a refuse threshold of 2.
        # Act
        rec = add_task(
            id="p0-config-loss",
            title="[P0] fleet-wide config/state-loss hazard",
            status="in_progress",
            priority=1,
            agent="a",
            store=over_cap,
        )
        # Assert
        assert rec["id"] == "p0-config-loss"

    def test_p0_in_progress_card_is_accepted_over_the_cap(self, over_cap):
        # Arrange
        # Act
        rec = add_task(
            id="p0-zero",
            title="[P0] production is on fire",
            status="in_progress",
            priority=0,
            agent="a",
            store=over_cap,
        )
        # Assert
        assert rec["priority"] == 0

    def test_normal_card_is_still_refused_over_the_cap(self, over_cap):
        # Arrange — the cap must still work for ordinary new work.
        # Act / Assert
        with pytest.raises(TaskValidationError, match="WIP gate refuses add"):
            add_task(
                id="ordinary",
                title="just more work",
                status="in_progress",
                priority=3,
                agent="a",
                store=over_cap,
            )

    def test_unprioritised_card_is_still_refused_over_the_cap(self, over_cap):
        # Arrange — no priority at all is not an emergency.
        # Act / Assert
        with pytest.raises(TaskValidationError, match="WIP gate refuses add"):
            add_task(
                id="unprioritised",
                title="just more work",
                status="in_progress",
                agent="a",
                store=over_cap,
            )


class TestBypassIsLoudNotSilent:
    """A card admitted over the cap must SAY SO, so abuse self-reports."""

    def test_bypassed_card_carries_the_audit_stamp(self, over_cap):
        # Arrange
        rec = add_task(
            id="p1-stamped",
            title="[P0] fleet-wide config/state-loss hazard",
            status="in_progress",
            priority=1,
            agent="a",
            store=over_cap,
        )
        # Act
        kinds = [c.get("kind") for c in rec.get("comments") or []]
        # Assert
        assert OVERRIDE_COMMENT_KIND in kinds

    def test_audit_stamp_records_the_wip_count_and_limit(self, over_cap):
        # Arrange
        rec = add_task(
            id="p1-counted",
            title="[P0] fleet-wide config/state-loss hazard",
            status="in_progress",
            priority=1,
            agent="a",
            store=over_cap,
        )
        # Act
        text = rec["comments"][0]["text"]
        # Assert — "8 tasks in_progress at insert time (limit 1; ...)".
        assert "8 tasks in_progress" in text and "limit 1" in text

    def test_audit_stamp_is_persisted_not_just_returned(self, over_cap):
        # Arrange
        add_task(
            id="p1-persisted",
            title="[P0] fleet-wide config/state-loss hazard",
            status="in_progress",
            priority=1,
            agent="a",
            store=over_cap,
        )
        # Act — read it back off disk; the board renders THIS.
        text = over_cap.read_text()
        # Assert
        assert OVERRIDE_COMMENT_KIND in text

    def test_card_under_the_cap_is_not_stamped(self, tmp_path, env, monkeypatch):
        # Arrange — a P1 filed by an agent with room to spare is ordinary; the
        # stamp must mean "a bypass happened", not "someone typed priority 1".
        env.set(ENV_WIP_LIMIT, "10")
        store = tmp_path / "tasks.yaml"
        store.write_text("tasks: []\n")
        monkeypatch.setenv(ENV_TASKS, str(store))
        # Act
        rec = add_task(
            id="p1-roomy",
            title="[P1] urgent but the board is calm",
            status="in_progress",
            priority=1,
            agent="a",
            store=store,
        )
        # Assert
        assert not rec.get("comments")


class TestRefusalMessageIsActionable:
    """The old text ("Close existing tasks before adding more") is what
    pressured agents to falsely close cards during an outage."""

    def test_refusal_names_the_priority_escape_hatch(self, over_cap):
        # Arrange
        # Act
        with pytest.raises(TaskValidationError) as excinfo:
            add_task(
                id="ordinary-2",
                title="just more work",
                status="in_progress",
                agent="a",
                store=over_cap,
            )
        # Assert
        assert "priority <= 1" in str(excinfo.value)

    def test_refusal_names_the_deferred_blocked_escape_hatch(self, over_cap):
        # Arrange
        # Act
        with pytest.raises(TaskValidationError) as excinfo:
            add_task(
                id="ordinary-3",
                title="just more work",
                status="in_progress",
                agent="a",
                store=over_cap,
            )
        # Assert
        assert "deferred or blocked is never gated" in str(excinfo.value)

    def test_refusal_does_not_tell_you_to_close_cards(self, over_cap):
        # Arrange — the falsify-your-board incentive, removed at the source.
        # Act
        with pytest.raises(TaskValidationError) as excinfo:
            add_task(
                id="ordinary-4",
                title="just more work",
                status="in_progress",
                agent="a",
                store=over_cap,
            )
        # Assert
        assert "Close existing tasks before adding more" not in str(excinfo.value)
