#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S2 READ PATH — the two backends must be INDISTINGUISHABLE, or it does not ship.

WHAT THIS FILE IS FOR
---------------------
``list_tasks`` costs ~830 ms on the live 1,452-card / 5.8 MB store, and a FILTERED
call costs the same (~730 ms for 152 of 1,452 cards) because the whole YAML document
is parsed and only THEN filtered in Python. The cost is the PARSE, not the query. The
SQLite mirror already indexes every field ``list_tasks`` filters on, so the fix is an
indexed lookup instead of a 5.8 MB parse.

But a read backend that serves subtly-different cards is FAR worse than a slow one:
slow is visible, wrong is not, and the fleet reads this store to decide what to work
on. So the bar for shipping is not "fast" and not "plausible" — it is IDENTICAL:
same cards, same order, same fields, same values, byte-for-byte through JSON.

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
from pathlib import Path

import pytest

from scitex_todo import _store, _store_read_sqlite
from scitex_todo._db import ENV_DB
from scitex_todo._db_bootstrap import import_from_yaml
from scitex_todo._store_read_sqlite import BACKEND_SQLITE, ENV_READ_BACKEND
from scitex_todo._yaml import safe_dump

_YESTERDAY = (_dt.date.today() - _dt.timedelta(days=3)).isoformat()
_TOMORROW = (_dt.date.today() + _dt.timedelta(days=30)).isoformat()


def _cards() -> list[dict]:
    """Cards shaped like the live store's — including the parts no column maps.

    Every value here is one the VALIDATOR accepts, because the YAML backend
    re-validates on read: a fixture the YAML path cannot even load proves nothing.
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
def store(tmp_path, monkeypatch):
    """A YAML store + a mirror imported from it, with the caches cleared."""
    monkeypatch.setenv(ENV_DB, str(tmp_path / "todo.db"))
    monkeypatch.delenv("SCITEX_TODO_SCOPE", raising=False)
    path = tmp_path / "tasks.yaml"
    path.write_text(safe_dump({"tasks": _cards()}))
    import_from_yaml(path, tmp_path / "todo.db")
    _store_read_sqlite.reset_cache()
    return path


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
    os.environ.pop(ENV_READ_BACKEND, None)
    _store_read_sqlite.reset_cache()
    yaml_rows = _store.list_tasks(store_path, **kw)

    os.environ[ENV_READ_BACKEND] = BACKEND_SQLITE
    _store_read_sqlite.reset_cache()
    try:
        # The guard must actually be SATISFIED — otherwise this whole file would
        # silently compare the YAML backend against itself and prove nothing.
        assert _store_read_sqlite.enabled(store_path), "guard refused a healthy mirror"
        sqlite_rows = _store.list_tasks(store_path, **kw)
    finally:
        os.environ.pop(ENV_READ_BACKEND, None)
        _store_read_sqlite.reset_cache()
    return yaml_rows, sqlite_rows


def assert_identical(store_path, **kw) -> list[dict]:
    yaml_rows, sqlite_rows = _both(store_path, **kw)
    assert _fingerprint(sqlite_rows) == _fingerprint(yaml_rows), (
        f"BACKENDS DIVERGED for {kw!r}\n"
        f"  yaml  : {[r.get('id') for r in yaml_rows]}\n"
        f"  sqlite: {[r.get('id') for r in sqlite_rows]}"
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
    assert_identical(store, **kw)


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
    assert_identical(store, **{**a, **b})


# --------------------------------------------------------------------------
# The specific traps — each one is a way the SQLite path could be quietly wrong
# --------------------------------------------------------------------------
def test_unknown_fields_survive_the_round_trip(store):
    """The 22 keys no column maps. A column-based rebuild drops them SILENTLY."""
    rows = assert_identical(store, id_prefix="alpha-2")
    card = rows[0]
    assert card["deferred_at"] == "2026-07-02T00:00:00Z"
    assert card["subagent"] == "sub-7"
    assert card["note_DONE"] == "the ad-hoc note fields agents really invent"
    assert card["blocked_by"] == "someone"


def test_key_order_survives_the_round_trip(store):
    """Card 'alpha-2' declares `status` BEFORE `id`. 711 such orders exist live."""
    yaml_rows, sqlite_rows = _both(store, id_prefix="alpha-2")
    assert list(sqlite_rows[0].keys()) == list(yaml_rows[0].keys())
    assert list(sqlite_rows[0].keys())[0] == "status"


def test_document_order_is_preserved(store):
    """`row_order` exists for exactly this. Not sorted by id, not by insertion."""
    rows = assert_identical(store)
    assert [r["id"] for r in rows] == [c["id"] for c in _cards()]


def test_blocker___none__is_not_the_enum_none(store):
    """`__none` means "no blocker NAMED". `none` is a real, TRUTHY enum member.

    `_match` tests FALSINESS (`if task.get("blocker")`), so only an absent (or
    empty) blocker matches `__none` — a card explicitly blocked on `none` does not.
    A naive `blocker IS NULL OR blocker = 'none'` clause would swallow it.
    """
    rows = assert_identical(store, blocker="__none")
    ids = {r["id"] for r in rows}
    assert "alpha-2" in ids, "a card with NO blocker key must match __none"
    assert "beta_1" not in ids, "blocker='none' is TRUTHY — must NOT match __none"
    assert "alpha-1" not in ids

    rows = assert_identical(store, blocker="none")
    assert [r["id"] for r in rows] == ["beta_1"]


def test_id_prefix_underscore_is_not_a_sql_wildcard(store):
    """`LIKE 'beta_%'` would ALSO match 'beta-2'. `startswith` must not."""
    rows = assert_identical(store, id_prefix="beta_")
    assert [r["id"] for r in rows] == ["beta_1"]


def test_kind_task_matches_absent_kinds_too(store):
    """absent ≡ "task" (ADR-0002) — so a kind="task" filter must return both."""
    rows = assert_identical(store, kind="task")
    ids = {r["id"] for r in rows}
    assert "beta_1" in ids, "explicit kind='task'"
    assert {"beta-2", "delta-1"} <= ids, "absent kind must fold to 'task'"
    assert "alpha-2" not in ids  # kind="compute"


def test_overdue_excludes_recurring_and_terminal(store):
    """A RECURRING deadline is NEVER overdue; a terminal card never is either."""
    rows = assert_identical(store, overdue=True)
    assert [r["id"] for r in rows] == ["gamma-1"]


def test_scope_env_default_and_optout(store, monkeypatch):
    """`scope=None` honours $SCITEX_TODO_SCOPE; `scope=""` opts out. Both backends."""
    monkeypatch.setenv("SCITEX_TODO_SCOPE", "agent:agent-b")
    rows = assert_identical(store)  # scope=None -> env applies
    assert {r["id"] for r in rows} == {"alpha-2"}

    rows = assert_identical(store, scope="")  # explicit opt-out
    assert len(rows) == len(_cards())


# --------------------------------------------------------------------------
# The guard — the flag alone must never be enough
# --------------------------------------------------------------------------
def test_backend_is_off_by_default(store, monkeypatch):
    monkeypatch.delenv(ENV_READ_BACKEND, raising=False)
    assert _store_read_sqlite.enabled(store) is False


def test_guard_refuses_a_missing_db(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_READ_BACKEND, BACKEND_SQLITE)
    monkeypatch.setenv(ENV_DB, str(tmp_path / "absent.db"))
    _store_read_sqlite.reset_cache()
    path = tmp_path / "tasks.yaml"
    path.write_text(safe_dump({"tasks": _cards()}))

    assert _store_read_sqlite.enabled(path) is False
    # and the caller still gets correct cards, from YAML
    assert len(_store.list_tasks(path)) == len(_cards())


def test_guard_refuses_a_STALE_db_and_still_serves_correct_cards(store, monkeypatch):
    """THE failure this whole guard exists for.

    The YAML is canonical and ANYTHING may write it — an agent on an older build, a
    process with the dual-write mirror off (the default!), a hand-edit. None of those
    touch the mirror. The DB then stays perfectly well-formed and quietly WRONG.
    """
    monkeypatch.setenv(ENV_READ_BACKEND, BACKEND_SQLITE)
    _store_read_sqlite.reset_cache()
    assert _store_read_sqlite.enabled(store) is True  # fresh: guard says yes

    # Now write the YAML behind the mirror's back.
    cards = _cards() + [
        {"id": "ghost-1", "title": "written without mirroring", "status": "goal"}
    ]
    store.write_text(safe_dump({"tasks": cards}))
    _store_read_sqlite.reset_cache()

    assert _store_read_sqlite.enabled(store) is False, "a stale mirror must be REFUSED"
    rows = _store.list_tasks(store)
    assert [r["id"] for r in rows] == [c["id"] for c in cards], "must fall back to YAML"


def test_guard_refuses_a_db_with_no_payload_column(store, monkeypatch, tmp_path):
    """A v1 DB: right schema, right indexes, `quick_check ok` — and NO payloads.

    Reconstructing cards from the typed columns alone would drop every field the
    schema does not name. Refuse, do not improvise.
    """
    import sqlite3

    db = tmp_path / "todo.db"
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("UPDATE tasks SET card_json = NULL WHERE id = 'alpha-2'")
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setenv(ENV_READ_BACKEND, BACKEND_SQLITE)
    _store_read_sqlite.reset_cache()
    assert _store_read_sqlite.enabled(store) is False
    assert len(_store.list_tasks(store)) == len(_cards())


def test_guard_refuses_when_the_code_cannot_write_payloads(store, monkeypatch):
    """The 135-second lesson: a flag whose safety depends on a code version must
    VERIFY THAT CODE AT RUNTIME — by SYMBOL, never by version string."""
    from scitex_todo import _db_bootstrap

    monkeypatch.setenv(ENV_READ_BACKEND, BACKEND_SQLITE)
    # Simulate an older build whose mirror has no payload column.
    monkeypatch.setattr(
        _db_bootstrap,
        "TASK_INSERT_COLS",
        tuple(c for c in _db_bootstrap.TASK_INSERT_COLS if c != "card_json"),
    )
    _store_read_sqlite.reset_cache()

    assert _store_read_sqlite.enabled(store) is False
    assert len(_store.list_tasks(store)) == len(_cards())


def test_a_lossy_mirror_is_refused(tmp_path, monkeypatch):
    """A duplicate card id means the mirror holds FEWER rows than the doc.

    And here is the sharp part, which only showed up once this was actually run: the
    YAML path RAISES on a duplicate id (the validator refuses the store outright),
    while the mirror silently collapses the two rows into one. So without the card-
    count guard, switching the backend on would have converted a LOUD, correct
    failure into a QUIET, wrong answer — the store would simply have come back one
    card short, forever, and every equality check on the cards that ARE present
    would have passed.

    A backend swap must not change WHICH failures are visible.
    """
    from scitex_todo._task import TaskValidationError

    monkeypatch.setenv(ENV_DB, str(tmp_path / "todo.db"))
    monkeypatch.setenv(ENV_READ_BACKEND, BACKEND_SQLITE)
    path = tmp_path / "tasks.yaml"
    path.write_text(
        safe_dump(
            {
                "tasks": [
                    {"id": "dupe", "title": "first", "status": "goal"},
                    {"id": "dupe", "title": "second", "status": "done"},
                ]
            }
        )
    )
    import_from_yaml(path, tmp_path / "todo.db")
    _store_read_sqlite.reset_cache()

    # The mirror really is lossy: 2 cards in, 1 row out.
    import sqlite3

    conn = sqlite3.connect(str(tmp_path / "todo.db"))
    try:
        assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1
    finally:
        conn.close()

    assert _store_read_sqlite.enabled(path) is False, "a lossy mirror must be REFUSED"
    # ...and the caller gets the YAML path's LOUD failure, not a quietly short list.
    with pytest.raises(TaskValidationError, match="duplicate task id"):
        _store.list_tasks(path)


def test_a_relative_store_path_is_still_the_same_store(store, monkeypatch):
    """A mirror stamped via a RELATIVE path must still be recognised via an absolute
    one — the two spellings are ONE store.

    Found by the benchmark, not by me: `db import ./tasks.yaml` stamped a relative
    `yaml_path`, and the reader (resolving absolutely) then declared "the DB mirrors a
    DIFFERENT store" and refused a perfectly good mirror. It failed SAFE — fell back to
    YAML, correct but slow — which is exactly why it would have been easy never to
    notice. Paths must be compared CANONICALLY.
    """
    monkeypatch.setenv(ENV_READ_BACKEND, BACKEND_SQLITE)
    monkeypatch.chdir(store.parent)
    import_from_yaml(Path("tasks.yaml"), store.parent / "todo.db")  # RELATIVE stamp
    _store_read_sqlite.reset_cache()

    assert _store_read_sqlite.enabled(store) is True, "absolute read of a relative stamp"
    assert _store_read_sqlite.enabled(Path("tasks.yaml")) is True, "relative read"


def test_refusal_is_logged_once_not_per_call(store, monkeypatch, caplog):
    """`list_tasks` runs on every poll of every agent. An ERROR per call is noise,
    and noise that fires constantly trains its reader to ignore the channel."""
    monkeypatch.setenv(ENV_READ_BACKEND, BACKEND_SQLITE)
    monkeypatch.setenv(ENV_DB, str(store.parent / "absent.db"))
    _store_read_sqlite.reset_cache()

    with caplog.at_level("ERROR", logger="scitex_todo._store_read_sqlite"):
        for _ in range(5):
            _store.list_tasks(store)

    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1, f"expected ONE refusal log, got {len(errors)}"

# EOF
