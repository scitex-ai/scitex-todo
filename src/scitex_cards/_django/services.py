#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Board service -- resolve + load the task store with a small mtime cache.

Mirrors figrecipe's ``services.get_or_create_editor`` shape but read-only: the
board never mutates the store in this MVP, so the "board" is just the validated
task list plus its resolved path and mtime. The cache avoids re-reading the
YAML on every poll while still picking up external edits (cache is keyed by
path and invalidated when the file's mtime changes).

Per-project lane UNION (operator-validated requirement, lead a2a
`1ceec0ef` + `40c0a42d`, 2026-06-13)
-------------------------------------
Skill 30 describes a two-tier model: a global user-scope store
PLUS hand-curated per-project lanes (``~/proj/<repo>/.scitex/todo/
tasks.yaml``). Pre-this-change the loader resolved to ONE store at a
time, so the operator's nv-lessons (and 31 other neurovista cards
that live in ``~/proj/neurovista/.scitex/todo/tasks.yaml``) were
INVISIBLE on the board.

The board's task set is now the UNION of:
  - the global store (resolved by :func:`resolve_tasks_path`), AND
  - every per-project lane discovered by globbing
    :data:`SCITEX_TODO_LANE_GLOBS` (default
    ``~/proj/*/.scitex/todo/tasks.yaml``).

Collision policy: **project-lane wins** on duplicate ids. Rationale:
human hand-curation beats agent auto-write. Each collision is logged
(``logger.warning``) so silent overrides are visible.

Resilience: a malformed per-project lane is SKIPPED + LOGGED — the
board still renders the rest. Per-lane crash-loud, never whole-view.
"""

import glob
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# In-process cache: store_path_str -> (BoardState, last_access_time)
_board_cache: Dict[str, Tuple["BoardState", float]] = {}
_CACHE_TTL_SECONDS = 3_600  # 1 hour

#: Env override for the per-project lane discovery glob. Comma-separated.
#: Default ``~/proj/*/.scitex/todo/tasks.yaml`` covers the operator's
#: layout; other hosts can override (e.g. ``~/work/*/.scitex/todo/
#: tasks.yaml``) without code change.
ENV_LANE_GLOBS = "SCITEX_TODO_LANE_GLOBS"
DEFAULT_LANE_GLOBS = "~/proj/*/.scitex/todo/tasks.yaml"


@dataclass
class BoardState:
    """A resolved, validated task store snapshot.

    The ``tasks`` list is the UNION of the global store + every
    discovered per-project lane. ``store_path`` is the global store
    path (the resolution anchor); ``mtime`` is the MAX mtime across
    every source so the cache invalidates whenever ANY lane writes.

    ``mtime`` is REPORTED, not trusted: it is part of the ``/rev`` wire
    contract the frontend polls, and it stays a float for that reason.
    Cache invalidation keys off ``sig`` instead — see below.
    """

    tasks: list
    store_path: Path
    mtime: float
    #: CACHE IDENTITY — per source ``(mtime_ns, size, inode)``; global first,
    #: then the lanes in a stable order.
    #:
    #: WHY NOT ``mtime``: it is a float of SECONDS, and a filesystem whose
    #: timestamps are 1-second granular (CI's is — a write and the stat that
    #: follows it returned the SAME value) reports an unchanged mtime across a
    #: real write. Keying the cache on that means the board can answer a STRICT
    #: read with a pre-write board: read-your-own-writes silently broken, which
    #: is exactly what the chat POST depends on. Caught by
    #: test_a_strict_caller_is_never_served_stale on py3.13.
    #:
    #: THE RULE, stated precisely, because the loose version breaks working
    #: code: never use ``st_mtime`` as an EQUALITY key. Sorting by it and doing
    #: age arithmetic with it are unaffected — a tie or a sub-second error
    #: changes nothing there. Equality is where granularity becomes
    #: correctness. (Sharpened by scitex-agent-container, who found the same
    #: defect in their credential watcher.)
    #:
    #: Size and inode are BOTH needed; see ``_stat_sig`` for why size alone
    #: leaves a same-length edit invisible.
    #:
    #: Defaulted so existing constructions keep working; a board built without
    #: a sig simply never matches a cache probe, which fails CLOSED (rebuild)
    #: rather than serving something stale.
    sig: tuple = ()
    # P10 (lead a2a 2026-06-12) — user-defined project clusters loaded
    # from the same YAML store. Empty list when the store has no
    # ``groups:`` key (back-compat). See :mod:`scitex_cards._groups`.
    groups: list = None  # type: ignore[assignment]
    #: Paths of every per-project lane successfully unioned. Empty when
    #: no lanes were discovered or all were skipped. Useful for the FE
    #: footer ("loaded N lanes") + the test harness.
    lane_paths: List[Path] = field(default_factory=list)


def _discover_lanes() -> List[Path]:
    """Return the list of per-project lane paths to union into the board.

    Honors :data:`ENV_LANE_GLOBS` (comma-separated) when set; otherwise
    uses :data:`DEFAULT_LANE_GLOBS`. Each entry is expanded via
    ``Path.expanduser`` and then ``glob.glob`` so ``~`` works as
    expected and `*` matches per-repo dirs. Non-existent / non-file
    entries are filtered out silently (a never-created lane is not a
    failure — it just isn't a source).
    """
    raw = os.environ.get(ENV_LANE_GLOBS)
    if raw is None:
        raw = DEFAULT_LANE_GLOBS
    # An explicitly-empty env opts out of lane discovery entirely
    # (useful for tests + for hosts that don't want the rollup).
    patterns = [p.strip() for p in raw.split(",") if p.strip()]
    out: List[Path] = []
    for pattern in patterns:
        expanded = str(Path(pattern).expanduser())
        for match in glob.glob(expanded):
            mpath = Path(match)
            if mpath.is_file():
                out.append(mpath)
    return sorted(set(out))


def _stat_sig(path: Path) -> tuple:
    """``(mtime_ns, size, inode)`` for one file — the unit of cache identity.

    Pairing the stamp with the SIZE is what makes this survive a filesystem
    with 1-second timestamp granularity, where a write and the stat after it
    can report the same mtime. See ``BoardState.sig``.

    SIZE ALONE IS NOT ENOUGH, and it is worth being exact about why rather
    than claiming this closes the hole: ``st_mtime_ns`` is nanosecond-TYPED,
    not nanosecond-ACCURATE — on a filesystem that stamps whole seconds the
    sub-second digits are simply zero. So (mtime_ns, size) still collides for
    a SAME-LENGTH edit inside one granule: flipping a priority ``1`` -> ``2``,
    or swapping a status for another of equal length, rewrites the file
    without moving either component.

    INODE closes that. Every write to this store lands via atomic
    ``os.replace`` of a freshly-written temp file, which allocates a NEW
    inode — so the inode changes on every write regardless of what the clock
    or the length did. Inode numbers can be recycled, so this is not a
    guarantee in the abstract; but a collision would need the same timestamp
    granule AND an identical size AND a recycled inode at once.

    NEVER RAISES. A lane can be deleted between being loaded and being
    fingerprinted, and the board must not 500 because a file went away — the
    surrounding code is deliberately per-lane fail-soft and this has to match
    it. A vanished file yields ``(0, 0, 0)``, which DIFFERS from whatever it
    was, so the cache correctly invalidates instead of pinning the old board.
    """
    try:
        st = path.stat()
        return (st.st_mtime_ns, st.st_size, st.st_ino)
    except OSError:
        return (0, 0, 0)


def _load_lane_safe(path: Path):
    """Load one per-project lane; return ``(tasks, mtime)`` on success,
    ``(None, None)`` on failure (logged at WARNING — never raise).

    Per-lane crash-loud, never whole-view: a single malformed YAML in
    one repo must not blank the operator's board.
    """
    from scitex_cards._model import load_tasks

    try:
        tasks = load_tasks(path)
        mtime = path.stat().st_mtime
        return tasks, mtime
    except Exception as exc:  # noqa: BLE001 — intentional broad catch
        logger.warning(
            "[scitex-todo] skipping malformed per-project lane %s: %s",
            path,
            exc,
        )
        return None, None


#: Board keys whose background refresh is already in flight. Guards against a
#: refresh storm: the operator's browser polls every few seconds, and without
#: this every poll during a slow rebuild would spawn another rebuild.
_refreshing: set = set()
_refresh_guard = threading.Lock()

#: Stale-while-revalidate on the human board view. On by default; set
#: ``SCITEX_CARDS_BOARD_SWR=0`` to force blocking rebuilds (debugging, or a
#: deployment that would rather be slow than one cycle behind).
_ENV_SWR = "SCITEX_CARDS_BOARD_SWR"


def _swr_enabled() -> bool:
    return os.environ.get(_ENV_SWR, "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _kick_board_refresh(
    key, resolved, lane_tasks_by_path, effective_mtime, effective_sig=()
) -> None:
    """Rebuild this board off the request path, once at a time.

    Fail-soft by construction: if the rebuild raises, the cache keeps the
    older board and the next request tries again — a background refresh must
    never be able to blank the operator's board.
    """
    with _refresh_guard:
        if key in _refreshing:
            return
        _refreshing.add(key)

    def _run():
        from scitex_cards._groups import load_groups

        try:
            global_tasks = _load_global_tasks(resolved) if resolved.exists() else []
            unioned = _union_tasks(global_tasks, lane_tasks_by_path)
            task_ids = {t["id"] for t in unioned if isinstance(t, dict) and t.get("id")}
            groups = load_groups(resolved, task_ids=task_ids)
            fresh = BoardState(
                tasks=unioned,
                store_path=resolved,
                mtime=effective_mtime,
                sig=effective_sig,
                groups=groups,
                lane_paths=list(lane_tasks_by_path),
            )
            _board_cache[key] = (fresh, time.time())
        except Exception:  # noqa: BLE001 — never break the served board
            logger.warning(
                "[scitex-todo] background board refresh failed; keeping the "
                "previous board (next request retries)",
                exc_info=True,
            )
        finally:
            with _refresh_guard:
                _refreshing.discard(key)

    threading.Thread(
        target=_run, name=f"board-refresh-{Path(key).name}", daemon=True
    ).start()


def _load_global_tasks(path: Path) -> list:
    """Global store rows — from the SQLite mirror when it is provably fresh.

    THE BOARD'S HOT READ. The canonical YAML is ~9 MB and parses in **4.6 s**
    on the live store (0.01 s once cached) — and the cache is invalidated by
    every store write, so during ordinary fleet activity nearly every board
    request pays the full parse. That is what the operator experiences as a
    30-second board (measured 2026-07-18: ``/graph`` 19.3 s under write
    pressure vs 0.40 s warm). The mirror serves the same rows from indexed
    storage instead.

    The guard is :func:`scitex_cards._store_read_sqlite.enabled`, which FAILS
    CLOSED — an absent, stale, or incapable mirror falls back to the canonical
    YAML rather than showing the fleet cards that are quietly wrong. Rows come
    back from the verbatim ``card_json`` payload in document order, so the two
    paths are row-identical; a divergence would be a mirror bug, not a
    projection difference, and the freshness stamp exists to catch exactly
    that.
    """
    from scitex_cards._model import load_tasks

    try:
        from scitex_cards import _store_read_sqlite as _sq

        if _sq.enabled(path):
            return _sq.list_tasks_sqlite(path)
    except Exception:  # noqa: BLE001 — the board must never fail to render
        logger.warning(
            "[scitex-todo] sqlite board read failed; falling back to YAML",
            exc_info=True,
        )
    return load_tasks(path)


def _union_tasks(global_tasks: list, lane_tasks_by_path: Dict[Path, list]) -> list:
    """Union global + per-project lanes; project-lane wins on id collision.

    Returns a fresh list, leftmost-first by id so the FE's deterministic
    ordering doesn't shuffle on every load. Logs every collision so
    silent overrides are visible.
    """
    seen: Dict[str, dict] = {}
    seen_origin: Dict[str, str] = {}
    # Seed with global first.
    for t in global_tasks:
        tid = t.get("id") if isinstance(t, dict) else None
        if not tid:
            continue
        seen[tid] = t
        seen_origin[tid] = "global"
    # Lane order is sorted (see _discover_lanes), so the iteration is
    # deterministic. Project lane wins on collision; LOG the override.
    for lane_path, lane_tasks in lane_tasks_by_path.items():
        for t in lane_tasks:
            tid = t.get("id") if isinstance(t, dict) else None
            if not tid:
                continue
            if tid in seen:
                logger.warning(
                    "[scitex-todo] id %r collision — %s overrides %s",
                    tid,
                    lane_path,
                    seen_origin[tid],
                )
            seen[tid] = t
            seen_origin[tid] = str(lane_path)
    return list(seen.values())


def get_board(
    tasks_path: Optional[str] = None, *, allow_stale: bool = False
) -> BoardState:
    """Resolve the task store, load + validate it, and cache by mtime.

    ``allow_stale`` opts THIS call into stale-while-revalidate: when the store
    has moved on, the cached board is returned immediately and the rebuild
    runs behind the response. It is OFF by default and must stay that way —
    an endpoint that writes and then reads back (the chat POST is the live
    example) MUST see its own write, and a caller that opts in without that
    property is a read-your-own-writes bug. Only the human BOARD read opts
    in, because a self-refreshing view one cycle behind is invisible while a
    31-second wait is not.

    Returns a :class:`BoardState` whose ``tasks`` field is the UNION of
    the global store and every per-project lane discovered by
    :func:`_discover_lanes`. Collision policy: project-lane wins (logged).
    ``mtime`` is MAX across every source so the cache invalidates
    whenever ANY source's YAML rolls forward.

    Parameters
    ----------
    tasks_path : str or None
        Optional explicit store path. When ``None``, the standard
        project -> user -> bundled resolution chain is used. The explicit
        path becomes the GLOBAL source; lanes are still discovered + unioned.

    Returns
    -------
    BoardState
        The validated, unioned task list + the global-store anchor +
        the per-source MAX mtime + the lane paths actually consumed.
    """
    from scitex_cards._groups import load_groups
    from scitex_cards._model import load_tasks
    from scitex_cards._paths import resolve_tasks_path

    _cleanup_expired()

    resolved = resolve_tasks_path(tasks_path)
    global_mtime = resolved.stat().st_mtime if resolved.exists() else 0.0

    # Discover + load every per-project lane. Build a stable lane-mtime
    # dict so the cache key reflects the WHOLE source set.
    discovered = _discover_lanes()
    lane_tasks_by_path: Dict[Path, list] = {}
    lane_mtimes: List[float] = []
    successful_lanes: List[Path] = []
    for lane_path in discovered:
        tasks, mt = _load_lane_safe(lane_path)
        if tasks is None:
            continue
        lane_tasks_by_path[lane_path] = tasks
        lane_mtimes.append(mt)
        successful_lanes.append(lane_path)

    # Effective mtime = MAX(global, *lanes). REPORTED value only — it feeds
    # the /rev wire contract the frontend polls. Invalidation uses `sig`.
    effective_mtime = max([global_mtime] + lane_mtimes) if lane_mtimes else global_mtime

    # CACHE IDENTITY: per-source (mtime_ns, size), global first then lanes in
    # a stable order. Unlike the max() above this changes when ANY source
    # changes in EITHER direction — a lane shrinking, or a write landing
    # inside the same 1-second timestamp granule, both move it. See
    # BoardState.sig for the failure this replaces.
    effective_sig = (
        (_stat_sig(resolved) if resolved.exists() else (0, 0)),
        tuple(sorted(_stat_sig(p) for p in successful_lanes)),
    )

    # Cache key is the GLOBAL path; the lane set is implicit (any lane
    # change moves effective_sig, which the cache check sees).
    key = str(resolved)
    cached = _board_cache.get(key)
    if cached is not None:
        board, _ = cached
        if board.sig and board.sig == effective_sig:
            _board_cache[key] = (board, time.time())
            return board
        # STALE-WHILE-REVALIDATE. The store changed, so this cached board is
        # a few seconds behind — but rebuilding it costs a full parse of the
        # multi-MB store (measured 4.6s, and the whole board request 31s cold
        # on the live store), and the fleet writes every few seconds, so a
        # blocking rebuild means the operator pays that on nearly every view.
        #
        # A BOARD IS A LIVE VIEW, NOT A DECISION READ: it self-refreshes every
        # few seconds and shows a LIVE badge, so serving data that is one
        # refresh-cycle old is invisible, while a 31-second wait is not. The
        # refresh runs behind this response and the NEXT poll picks it up.
        #
        # Deliberately NOT applied to agent reads (list_tasks and friends go
        # through the store API, never here) — an agent deciding what to work
        # on must not act on a stale slice. This is the human-view path only.
        if allow_stale and _swr_enabled():
            _kick_board_refresh(
                key, resolved, lane_tasks_by_path, effective_mtime, effective_sig
            )
            return board

    global_tasks = _load_global_tasks(resolved) if resolved.exists() else []
    unioned = _union_tasks(global_tasks, lane_tasks_by_path)

    # P10: load + validate groups against the GLOBAL store only.
    # Per-project lanes don't currently carry groups; future PR can
    # union ``groups:`` similarly if the operator requests it.
    task_ids = {t["id"] for t in unioned if isinstance(t, dict) and t.get("id")}
    groups = load_groups(resolved, task_ids=task_ids)

    board = BoardState(
        tasks=unioned,
        store_path=resolved,
        mtime=effective_mtime,
        sig=effective_sig,
        groups=groups,
        lane_paths=successful_lanes,
    )
    _board_cache[key] = (board, time.time())
    logger.info(
        "[scitex-todo] Loaded board from %s + %d lane(s) (%d total tasks, %d groups)",
        resolved,
        len(successful_lanes),
        len(unioned),
        len(groups),
    )
    return board


def _cleanup_expired() -> None:
    """Drop board snapshots untouched for longer than the TTL."""
    now = time.time()
    expired = [
        k for k, (_, ts) in _board_cache.items() if now - ts > _CACHE_TTL_SECONDS
    ]
    for k in expired:
        _board_cache.pop(k, None)


def _reset_cache() -> None:
    """Clear the cache (test hook)."""
    _board_cache.clear()


# EOF
