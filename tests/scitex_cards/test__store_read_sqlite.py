#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S2 READ PATH — the two filter backends must be INDISTINGUISHABLE, or it does not ship.

WHAT THIS FILE IS FOR
---------------------
``list_tasks`` serves its rows from the SQLite store two ways: the indexed SQL path
(:func:`scitex_cards._store_read_sqlite.list_tasks_sqlite`, the primary read) and the
Python-predicate path (:func:`scitex_cards._store_list._match` over
:func:`load_tasks`, the fallback a build that cannot mirror payloads still uses). Both
reconstruct cards from the SAME ``tasks.card_json`` payload in the SAME
``ORDER BY row_order``; the ONLY thing that differs between them is HOW each filter is
expressed — an SQL ``WHERE`` clause versus a Python ``if``.

But a read path that serves subtly-different cards is FAR worse than a slow one: slow
is visible, wrong is not, and the fleet reads this store to decide what to work on. So
the bar is not "fast" and not "plausible" — it is IDENTICAL: same cards, same order,
same fields, same values, byte-for-byte through JSON, for every filter combination.

These tests are that proof. :func:`assert_identical` compares the two backends' full
result sets — including key ORDER, which plain ``==`` on dicts would silently forgive
— across a matrix of filter combinations, with the tricky ones (``blocker="__none"``,
``blocking_me``, ``overdue`` incl. a recurring deadline, ``statuses`` + ``status``,
``scope=""`` vs ``scope=None``, ``id_prefix`` with an underscore) called out by name.

THE FIXTURE IS ADVERSARIAL ON PURPOSE
-------------------------------------
It is built from what the LIVE store actually contains, not from what the schema
wishes it contained (measured 2026-07-13):

* **Unknown fields.** 22 card keys in the live store map to NO column —
  ``deferred_at`` (20 cards), ``subagent`` (8), ``blocked_by`` (3),
  ``completed_at``, ``tasks_path``, and a family of ad-hoc ``note_*`` fields agents
  invent as they work. A column-based rebuild drops every one of them SILENTLY. The
  fixture carries them, so that bug cannot pass.
* **Key order.** 711 distinct key orders exist across the live cards. The fixture
  cards deliberately disagree about field order.
* **An underscore in an id.** ``_`` is a single-char WILDCARD in SQL ``LIKE``, so a
  ``LIKE 'note_%'`` prefix filter would also match ``note-x``. The fixture has both.
"""

from __future__ import annotations

import datetime as _dt
import itertools
import json
import os

import pytest

from scitex_cards import _store, _store_read_sqlite
from scitex_cards._model import load_tasks
from scitex_cards._store_list import _default_scope, _match
from scitex_cards._store_read_sqlite import BACKEND_SQLITE, ENV_READ_BACKEND

_YESTERDAY = (_dt.date.today() - _dt.timedelta(days=3)).isoformat()
_TOMORROW = (_dt.date.today() + _dt.timedelta(days=30)).isoformat()


def _cards() -> list[dict]:
    """Cards shaped like the live store's — including the parts no column maps.

    Every value here is one the VALIDATOR accepts, because the load path
    re-validates on read: a fixture the read path cannot even load proves nothing.
    (Checked, not assumed — ``blocker=""`` and ``kind=""`` are REJECTED by
    ``_validate``, so they cannot occur in a real store and are not modelled. The
    SQL still folds them, defensively, but no test can reach that fold.)
    """
    return [
        # key order A; a blocker; a scope; comments
        {
            "id": "alpha-1",
            "title": "alpha one",
            "status": "blocked",
            "blocker": "operator-decision",
            "assignee": "agent-a",
            "agent": "agent-a",
            "scope": "agent:agent-a",
            "project": "proj-x",
            "host": "hostA",
            "repo": "own/repo-x",
            "priority": 2,
            "comments": [
                {"author": "agent-a", "ts": "2026-07-01T00:00:00Z", "text": "hi"}
            ],
        },
        # key order B (status FIRST, id second); NO blocker key -> blocker="__none"
        {
            "status": "in_progress",
            "id": "alpha-2",
            "assignee": "agent-b",
            "title": "alpha two",
            "kind": "compute",
            "agent": "agent-b",
            "scope": "agent:agent-b",
            # UNKNOWN FIELDS: no column maps these. A column-based rebuild loses
            # them, silently. All four are real shapes from the live store.
            "deferred_at": "2026-07-02T00:00:00Z",
            "subagent": "sub-7",
            "note_DONE": "the ad-hoc note fields agents really invent",
            "blocked_by": "someone",
        },
        # blocker="none" — a VALID enum member, and TRUTHY. It must NOT match
        # `__none` (which means "no blocker named"). A `blocker IS NULL OR
        # blocker='none'` clause would wrongly swallow this card.
        {
            "id": "beta_1",
            "title": "beta one (underscore id)",
            "status": "blocked",
            "blocker": "none",
            "assignee": "agent-a",
            "kind": "task",  # explicit "task"
            "_log_meta": {"completed_by": "agent-a"},
        },
        # id that LIKE 'beta_%' would wrongly match but startswith('beta_') must not
        {
            "id": "beta-2",
            "title": "beta two (hyphen id)",
            "status": "done",
            "assignee": "agent-b",
            # kind ABSENT -> must behave as "task" (ADR-0002)
        },
        # OVERDUE: a bare past deadline, non-terminal status
        {
            "id": "gamma-1",
            "title": "overdue for real",
            "status": "in_progress",
            "assignee": "agent-a",
            "deadline": _YESTERDAY,
        },
        # NOT overdue: past deadline but a RECURRING repeater rolls it forward
        {
            "id": "gamma-2",
            "title": "recurring is never overdue",
            "status": "in_progress",
            "assignee": "agent-a",
            "deadline": f"{_YESTERDAY} +1w",
        },
        # NOT overdue: past deadline but TERMINAL status
        {
            "id": "gamma-3",
            "title": "past deadline but done",
            "status": "done",
            "assignee": "agent-a",
            "deadline": _YESTERDAY,
        },
        # NOT overdue: future deadlines (the multi form)
        {
            "id": "gamma-4",
            "title": "future deadline",
            "status": "goal",
            "assignee": "agent-b",
            "deadlines": [_TOMORROW, f"{_TOMORROW} +1m"],
        },
        # a plain card with no kind at all, to round out the kind="task" set
        {
            "id": "delta-1",
            "title": "absent kind folds to task",
            "status": "goal",
            "assignee": "agent-a",
        },
    ]


@pytest.fixture()
def store(env):
    """Seed the canonical SQLite store from the adversarial fixture doc.

    SQLite is the only store now; ``load_tasks`` and ``list_tasks_sqlite`` both read
    the canonical DB the harness pins at ``$SCITEX_CARDS_DB`` and IGNORE any path
    argument. So there is nothing to write to disk — we seed the pinned DB and hand
    back the pinned STORE identity path (``$SCITEX_CARDS_TASKS_YAML_SHARED``), which
    is what the read surface takes as its ``store`` argument.
    """
    from conftest import seed_db_from_doc

    env.delete("SCITEX_TODO_SCOPE")
    seed_db_from_doc({"tasks": _cards()}, os.environ["SCITEX_CARDS_DB"])
    _store_read_sqlite.reset_cache()
    return os.environ["SCITEX_CARDS_TASKS_YAML_SHARED"]


def _fingerprint(rows: list[dict]) -> str:
    """A digest of the FULL result set — order, fields, values, and KEY ORDER.

    ``json.dumps`` without ``sort_keys`` is the point: two dicts with the same
    content but different key order are ``==`` in Python, so a plain equality check
    would forgive a backend that re-orders fields — and everything that serializes a
    card (the CLI's JSON output, an API response) would change shape underneath us.
    This digest does not forgive it.
    """
    return json.dumps(rows, ensure_ascii=False)


def _both(store_path, **kw) -> tuple[list[dict], list[dict]]:
    """Run one query through BOTH live filter backends and return (python, sqlite).

    SQLite is the store, so the old ``SCITEX_TODO_READ_BACKEND`` env toggle no longer
    SWITCHES anything — ``enabled()`` is decided by code capability, not a flag. But
    the two FILTER implementations both still exist and must still agree card-for-
    card: the SQL clauses ``list_tasks_sqlite`` builds (the primary read path) and the
    Python ``_match`` predicate ``list_tasks`` falls back to when the running code
    cannot mirror payloads. Both reconstruct cards from the same ``card_json`` in the
    same ``row_order``, so ONLY the filter differs — which is exactly the parity this
    file proves. Invoke each directly over the one seeded DB, with scope resolved
    identically (``list_tasks`` resolves it once and hands the SAME value to both).
    """
    scope_eff = _default_scope(kw.pop("scope", None))
    _store_read_sqlite.reset_cache()
    sqlite_rows = _store_read_sqlite.list_tasks_sqlite(
        store_path, scope=scope_eff, **kw
    )
    yaml_rows = [
        dict(t) for t in load_tasks(store_path) if _match(t, scope=scope_eff, **kw)
    ]
    return yaml_rows, sqlite_rows


def _divergence(kw, yaml_rows, sqlite_rows) -> str:
    return (
        f"BACKENDS DIVERGED for {kw!r}\n"
        f"  python: {[r.get('id') for r in yaml_rows]}\n"
        f"  sqlite: {[r.get('id') for r in sqlite_rows]}"
    )


def assert_identical(store_path, **kw) -> list[dict]:
    yaml_rows, sqlite_rows = _both(store_path, **kw)
    assert _fingerprint(sqlite_rows) == _fingerprint(yaml_rows), _divergence(
        kw, yaml_rows, sqlite_rows
    )
    return yaml_rows


# --------------------------------------------------------------------------
# The matrix
# --------------------------------------------------------------------------
_SINGLE_FILTERS: list[dict] = [
    {},
    {"assignee": "agent-a"},
    {"assignee": "nobody"},
    {"agent": "agent-b"},
    {"status": "blocked"},
    {"status": "done"},
    {"statuses": ["done", "goal"]},
    {"status": "blocked", "statuses": ["done"]},  # OR-combined, not AND
    {"project": "proj-x"},
    {"host": "hostA"},
    {"repo": "own/repo-x"},
    {"scope": "agent:agent-a"},
    {"scope": ""},
    {"blocker": "operator-decision"},
    {"blocker": "none"},  # the enum member — NOT the same as "__none"
    {"blocker": "__none"},
    {"kind": "task"},
    {"kind": "compute"},
    {"id_prefix": "alpha"},
    {"id_prefix": "beta_"},
    {"id_prefix": "beta"},
    {"blocking_me": True},
    {"overdue": True},
]


@pytest.mark.parametrize("kw", _SINGLE_FILTERS, ids=lambda k: str(sorted(k.items())))
def test_each_filter_alone_is_identical(store, kw):
    # Arrange
    filters = dict(kw)
    # Act
    yaml_rows, sqlite_rows = _both(store, **filters)
    # Assert — same cards, same order, same fields, same KEY order.
    assert _fingerprint(sqlite_rows) == _fingerprint(yaml_rows), _divergence(
        filters, yaml_rows, sqlite_rows
    )


_PAIRS = list(
    itertools.combinations(
        [
            {"assignee": "agent-a"},
            {"status": "blocked"},
            {"kind": "task"},
            {"blocker": "__none"},
            {"id_prefix": "alpha"},
            {"scope": "agent:agent-a"},
            {"overdue": True},
            {"blocking_me": True},
        ],
        2,
    )
)


@pytest.mark.parametrize("a,b", _PAIRS, ids=lambda k: str(sorted(k.items())))
def test_filter_combinations_are_identical(store, a, b):
    # Arrange
    filters = {**a, **b}
    # Act
    yaml_rows, sqlite_rows = _both(store, **filters)
    # Assert — composing filters must not diverge where each alone agreed.
    assert _fingerprint(sqlite_rows) == _fingerprint(yaml_rows), _divergence(
        filters, yaml_rows, sqlite_rows
    )


# --------------------------------------------------------------------------
# The specific traps — each one is a way the SQLite path could be quietly wrong
# --------------------------------------------------------------------------
#: The 22 live keys no column maps. A column-based rebuild drops them SILENTLY,
#: so each is pinned by name rather than in one compound assertion — "an unknown
#: field was lost" is only actionable if it says WHICH, and the four here are
#: four different live shapes (a timestamp, an id, an ad-hoc note, a relation).
def _alpha_2(store) -> dict:
    return assert_identical(store, id_prefix="alpha-2")[0]


def test_unknown_deferred_at_field_survives_the_round_trip(store):
    # Arrange
    card = _alpha_2(store)
    # Act
    value = card["deferred_at"]
    # Assert
    assert value == "2026-07-02T00:00:00Z"


def test_unknown_subagent_field_survives_the_round_trip(store):
    # Arrange
    card = _alpha_2(store)
    # Act
    value = card["subagent"]
    # Assert
    assert value == "sub-7"


def test_ad_hoc_note_field_survives_the_round_trip(store):
    # Arrange
    card = _alpha_2(store)
    # Act
    value = card["note_DONE"]
    # Assert — agents invent these as they work; the mirror must not eat them.
    assert value == "the ad-hoc note fields agents really invent"


def test_unknown_blocked_by_field_survives_the_round_trip(store):
    # Arrange
    card = _alpha_2(store)
    # Act
    value = card["blocked_by"]
    # Assert
    assert value == "someone"


def test_key_order_survives_the_round_trip(store):
    """Card 'alpha-2' declares `status` BEFORE `id`. 711 such orders exist live."""
    # Arrange
    yaml_rows, sqlite_rows = _both(store, id_prefix="alpha-2")
    # Act
    orders = (list(sqlite_rows[0].keys()), list(yaml_rows[0].keys()))
    # Assert — plain `==` on dicts would forgive a re-ordering; this does not.
    assert orders[0] == orders[1]


def test_the_fixture_really_declares_status_before_id(store):
    # Arrange
    _yaml_rows, sqlite_rows = _both(store, id_prefix="alpha-2")
    # Act
    first_key = list(sqlite_rows[0].keys())[0]
    # Assert — without this the key-order test above could pass on a tame card.
    assert first_key == "status"


def test_document_order_is_preserved(store):
    """`row_order` exists for exactly this. Not sorted by id, not by insertion."""
    # Arrange
    expected_ids = [c["id"] for c in _cards()]
    # Act
    rows = assert_identical(store)
    # Assert
    assert [r["id"] for r in rows] == expected_ids


#: `__none` means "no blocker NAMED". `none` is a real, TRUTHY enum member.
#: `_match` tests FALSINESS (`if task.get("blocker")`), so only an absent (or
#: empty) blocker matches `__none` — a card explicitly blocked on `none` does
#: not. A naive `blocker IS NULL OR blocker = 'none'` clause would swallow it,
#: which is why the membership of BOTH filters is pinned card by card below.
def _blocker_none_token_ids(store) -> set:
    return {r["id"] for r in assert_identical(store, blocker="__none")}


def test_a_card_with_no_blocker_key_matches_the_none_token(store):
    # Arrange
    ids = _blocker_none_token_ids(store)
    # Act
    matched = "alpha-2" in ids
    # Assert
    assert matched, "a card with NO blocker key must match __none"


def test_blocker___none__is_not_the_enum_none(store):
    # Arrange
    ids = _blocker_none_token_ids(store)
    # Act
    matched = "beta_1" in ids
    # Assert
    assert not matched, "blocker='none' is TRUTHY — must NOT match __none"


def test_a_named_blocker_does_not_match_the_none_token(store):
    # Arrange
    ids = _blocker_none_token_ids(store)
    # Act
    matched = "alpha-1" in ids
    # Assert
    assert not matched


def test_filtering_on_the_none_enum_member_returns_only_that_card(store):
    # Arrange
    expected_ids = ["beta_1"]
    # Act
    rows = assert_identical(store, blocker="none")
    # Assert — `none` is a filterable value in its own right.
    assert [r["id"] for r in rows] == expected_ids


def test_id_prefix_underscore_is_not_a_sql_wildcard(store):
    """`LIKE 'beta_%'` would ALSO match 'beta-2'. `startswith` must not."""
    # Arrange
    expected_ids = ["beta_1"]
    # Act
    rows = assert_identical(store, id_prefix="beta_")
    # Assert
    assert [r["id"] for r in rows] == expected_ids


#: absent ≡ "task" (ADR-0002), so a kind="task" filter must return the explicit
#: and the absent alike — and nothing else. Three tests, because a filter that
#: is too narrow and one that is too wide are opposite bugs.
def _kind_task_ids(store) -> set:
    return {r["id"] for r in assert_identical(store, kind="task")}


def test_explicit_kind_task_matches_the_task_filter(store):
    # Arrange
    ids = _kind_task_ids(store)
    # Act
    matched = "beta_1" in ids
    # Assert
    assert matched, "explicit kind='task'"


def test_kind_task_matches_absent_kinds_too(store):
    # Arrange
    ids = _kind_task_ids(store)
    # Act
    absent_kind_ids = {"beta-2", "delta-1"}
    # Assert
    assert absent_kind_ids <= ids, "absent kind must fold to 'task'"


def test_a_compute_card_is_excluded_from_the_task_filter(store):
    # Arrange
    ids = _kind_task_ids(store)
    # Act
    matched = "alpha-2" in ids
    # Assert — folding absent to "task" must not fold everything to "task".
    assert not matched


def test_overdue_excludes_recurring_and_terminal(store):
    """A RECURRING deadline is NEVER overdue; a terminal card never is either."""
    # Arrange
    expected_ids = ["gamma-1"]
    # Act
    rows = assert_identical(store, overdue=True)
    # Assert
    assert [r["id"] for r in rows] == expected_ids


def test_scope_none_honours_the_env_default(store, env):
    # Arrange
    env.set("SCITEX_TODO_SCOPE", "agent:agent-b")
    # Act
    rows = assert_identical(store)  # scope=None -> env applies
    # Assert — both backends read the same env default.
    assert {r["id"] for r in rows} == {"alpha-2"}


def test_an_explicit_empty_scope_opts_out_of_the_env_default(store, env):
    # Arrange
    env.set("SCITEX_TODO_SCOPE", "agent:agent-b")
    # Act
    rows = assert_identical(store, scope="")  # explicit opt-out
    # Assert — `scope=""` is not the same as unset, on either backend.
    assert len(rows) == len(_cards())


# --------------------------------------------------------------------------
# The guard — code that cannot write the payload must REFUSE to serve
# --------------------------------------------------------------------------
#: With SQLite as the store there is no YAML to be fresh against and nothing to
#: fall back TO, so the freshness / staleness / missing-DB / lossy-mirror refusals
#: this file once exercised are gone (they asked questions about a document that no
#: longer participates). The ONE runtime check that survives — and matters MORE now
#: — is code capability: a process whose mirror writer has no ``card_json`` column
#: would serve cards with their unknown fields silently stripped, and with no YAML
#: behind it a stripped field is not stale, it is LOST. Each refusal is asserted
#: twice: the guard SAID no, and the caller still got correct cards from the Python
#: fallback path.


#: The 135-second lesson: a flag whose safety depends on a code version must
#: VERIFY THAT CODE AT RUNTIME — by SYMBOL, never by version string. Simulates
#: an older build whose mirror writer has no payload column.
def _code_that_cannot_write_payloads(env, monkeypatch):
    from scitex_cards import _db_bootstrap

    env.set(ENV_READ_BACKEND, BACKEND_SQLITE)
    monkeypatch.setattr(
        _db_bootstrap,
        "TASK_INSERT_COLS",
        tuple(c for c in _db_bootstrap.TASK_INSERT_COLS if c != "card_json"),
    )
    _store_read_sqlite.reset_cache()


def test_guard_refuses_when_the_code_cannot_write_payloads(store, env, monkeypatch):
    # Arrange
    _code_that_cannot_write_payloads(env, monkeypatch)
    # Act
    on = _store_read_sqlite.enabled(store)
    # Assert — the symbol, not a version string, is what is checked.
    assert on is False


def test_an_older_build_still_serves_correct_cards(store, env, monkeypatch):
    # Arrange
    _code_that_cannot_write_payloads(env, monkeypatch)
    # Act
    rows = _store.list_tasks(store)
    # Assert
    assert len(rows) == len(_cards())


#: A duplicate card id means the store holds FEWER rows than the doc: the mirror's
#: `id` PK collapses the two via INSERT OR REPLACE, last write wins. Pinned as its
#: own fact because it is the premise behind why a load never sees two.
def _seed_duplicate_id() -> None:
    from conftest import seed_db_from_doc

    seed_db_from_doc(
        {
            "tasks": [
                {"id": "dupe", "title": "first", "status": "goal"},
                {"id": "dupe", "title": "second", "status": "done"},
            ]
        },
        os.environ["SCITEX_CARDS_DB"],
    )
    _store_read_sqlite.reset_cache()


def _mirror_row_count() -> int:
    import sqlite3

    conn = sqlite3.connect(os.environ["SCITEX_CARDS_DB"])
    try:
        return conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    finally:
        conn.close()


def test_a_duplicate_id_really_collapses_in_the_mirror(env):
    # Arrange
    _seed_duplicate_id()
    # Act
    rows = _mirror_row_count()
    # Assert — 2 cards in, 1 row out: the id PK dedupes, last write wins.
    assert rows == 1


def test_refusal_is_logged_once_not_per_call(store, env, monkeypatch, caplog):
    """`list_tasks` runs on every poll of every agent. An ERROR per call is noise,
    and noise that fires constantly trains its reader to ignore the channel. The one
    refusal the guard still makes — code that cannot write the payload column — must
    therefore log ONCE per reason, not once per call."""
    # Arrange — an older build whose mirror writer has no payload column.
    _code_that_cannot_write_payloads(env, monkeypatch)
    # Act
    with caplog.at_level("ERROR", logger="scitex_cards._store_read_sqlite"):
        for _ in range(5):
            _store.list_tasks(store)
    # Assert
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1, f"expected ONE refusal log, got {len(errors)}"


# EOF
