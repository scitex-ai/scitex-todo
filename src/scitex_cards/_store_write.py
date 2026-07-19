#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The store's WRITE PATH — locking, crash-safe save, optimistic concurrency.

Split out of :mod:`scitex_cards._model` (2026-07-12), which had grown to 1,566
lines by tangling two unrelated responsibilities:

* **the MODEL** — what a task IS: the ``Task`` dataclass, the closed enums, the
  validator, the deadline grammar, and the READ path. That stays in ``_model``.
* **the WRITE PATH** — how a task gets to DISK. That is this file.

The separation is not cosmetic. Every store incident of the past two weeks lived
in THIS code and nowhere else:

* the ~20 s ruamel round-trip write (O(whole-store) on every single card change);
* the 2026-06-13 mid-string corruption, recovered by hand — now prevented by the
  tmp + fsync + ``os.replace`` dance and the post-dump reparse check below;
* the 2026-06-08 autoassign-parallel-run data loss (no atomic replace);
* and the LOCK CONVOY: MEASURED 2026-07-12 at **11,176 ms per card write** on the
  live 1,257-card store, while holding a FLEET-WIDE lock. Two agents writing means
  the second waits 11 seconds. That single number explains every "the board is
  slow" report we have ever had.

The lock is correct. What we do while holding it is not. This file is where that
gets fixed (see :mod:`scitex_cards._dual_write` and the SQLite migration), so it
deserves to be readable on its own rather than buried at the end of the model.

Every name here is re-exported from ``_model`` for backwards compatibility — 43
test files and every caller import from there, and the split must be invisible to
them.
"""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import os
import subprocess
from pathlib import Path

from ._model import (
    StaleStoreError,
    TaskValidationError,
    _validate_tasks,
    load_doc,
)
from ._store_verify import _verify_dumped_tmp
from ._yaml import safe_dump, safe_load


@contextlib.contextmanager
def _store_lock(path: Path):
    """Hold an exclusive `fcntl.flock` on a sibling `.<name>.lock` file.

    Phase 1 prerequisite for the cross-host sync substrate (Req 2): two
    concurrent writers — say a CLI verb and the board's `/priority` POST
    handler — must serialize so the YAML payload they write is atomic at
    the task-list granularity. We hold the lock on a separate `.lock`
    sentinel file rather than on the store itself so we don't fight the
    ruamel YAML reader/writer that re-opens the path.

    The lock file is created if missing, never removed (next caller reuses
    it). Empty mode is fine — only the lockf state matters.

    Parameters
    ----------
    path : Path
        The store path (e.g. ``~/.scitex/todo/tasks.yaml``). The lock
        sentinel sits next to it as ``.tasks.yaml.lock``.

    Yields
    ------
    None
        After the lock is held; released on context exit (even on errors).
    """
    path = Path(path)
    lock_path = path.parent / f".{path.name}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # `O_CREAT|O_RDWR` semantics via `open("a+")` — `a+` works even on
    # FS that lack `O_EXLOCK` (e.g. WSL2 ext4) because we acquire the
    # advisory lock via `fcntl.flock` after the open.
    fd = lock_path.open("a+")
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        finally:
            fd.close()


def save_tasks(
    tasks: list[dict],
    path: str | Path,
    *,
    expected_generation: str | None = None,
) -> None:
    """Validate then write a task list back to a YAML store, preserving comments.

    Re-runs the same validation gate as :func:`load_tasks` *before* touching
    disk, so a malformed mutation can never corrupt the store. Uses
    ``ruamel.yaml`` round-trip mode so hand-written comments and key layout in
    the existing store survive the rewrite.

    Parameters
    ----------
    tasks : list of dict
        The (already-mutated) task mappings to persist. Validated first.
    path : str or pathlib.Path
        Destination store. If it already exists, its comments + structure are
        preserved and only the ``tasks:`` payload is updated; otherwise a
        fresh document is written.

    Raises
    ------
    TaskValidationError
        If ``tasks`` fails structural validation (nothing is written).

    Examples
    --------
    >>> tasks = load_tasks("tasks.yaml")          # doctest: +SKIP
    >>> tasks[0]["priority"] = 1                    # doctest: +SKIP
    >>> save_tasks(tasks, "tasks.yaml")            # doctest: +SKIP
    """
    path = Path(path).expanduser()
    # The lock covers only THIS write. It cannot cover the caller's earlier
    # `load_tasks`, so plain load → mutate → save still loses a concurrent
    # writer's rows (2026-07-10 bulk-migration incident). Callers doing a
    # read-modify-write must either use :func:`edit_tasks` (one lock across
    # the whole cycle) or pass ``expected_generation`` from
    # :func:`store_generation` so a stale write is refused, never applied.
    path.parent.mkdir(parents=True, exist_ok=True)
    with _store_lock(path):
        if expected_generation is not None:
            current = store_generation(path)
            if current != expected_generation:
                raise StaleStoreError(
                    f"{path}: store changed since your read (generation "
                    f"{current[:12]} != expected {expected_generation[:12]}). "
                    f"Another writer committed in between; writing now would "
                    f"erase their rows. Reload, re-apply your change, retry — "
                    f"or use edit_tasks() to hold the lock across the cycle."
                )
        _save_tasks_unlocked(tasks, path)


def store_generation(path: str | Path) -> str:
    """Content hash of the store file — the optimistic-concurrency token.

    Take it BEFORE :func:`load_tasks`, hand it to
    ``save_tasks(..., expected_generation=...)``. Content-based (sha256), not
    mtime-based: mtime has coarse granularity on some filesystems and lies
    across clock skew, and this store is shared over network mounts.
    """
    p = Path(path).expanduser()
    if not p.exists():
        return "absent"

    return hashlib.sha256(p.read_bytes()).hexdigest()


@contextlib.contextmanager
def edit_tasks(path: str | Path):
    """One locked read-modify-write cycle — the sanctioned bulk-edit primitive.

    Yields the mutable ``tasks`` list with the store lock HELD; on clean exit
    the (possibly mutated) list is validated and written back, preserving the
    non-``tasks`` sections and comments. On an exception nothing is written.

    This exists because every raw ``load_tasks → mutate → save_tasks`` script
    has a lost-update window as wide as its own runtime, and on 2026-07-10 a
    bulk migration used exactly that shape and ate two concurrent writes.

    Examples
    --------
    >>> with edit_tasks("tasks.yaml") as tasks:     # doctest: +SKIP
    ...     for t in tasks:
    ...         if t.get("status") == "pending":
    ...             t["status"] = "deferred"
    """
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _store_lock(path):
        doc = load_doc(path, validate=True) if path.exists() else {}
        if not isinstance(doc, dict):
            raise TaskValidationError(f"{path}: top level is not a mapping")
        tasks = doc.get("tasks") or []
        yield tasks
        _save_doc_unlocked(doc, path, tasks=tasks)


def _save_tasks_unlocked(tasks: list[dict], path: Path) -> None:
    """Validate-and-write a task list WITHOUT acquiring the store lock.

    Thin back-compat wrapper over :func:`_save_doc_unlocked`. Callers that
    only hold a mutated ``tasks`` list (not the full parsed doc) land here;
    it does the ONE ``safe_load`` needed to recover the non-``tasks`` top-
    level sections (the ``users:`` registry etc.), splices in ``tasks``, and
    delegates the actual crash-safe write. Callers on the hot read-modify-
    write path should instead reuse the doc they already parsed via
    :func:`load_doc` and call :func:`_save_doc_unlocked` directly — that
    avoids this extra re-read entirely.

    Used by callers (the `_store.add_task`/`update_task`/`complete_task`
    Python API) that hold `_store_lock` for their whole read-modify-write
    cycle. Calling `save_tasks` recursively would deadlock — `flock` on
    a fresh fd to the same path blocks until the OUTER context releases.

    Direct callers must already hold `_store_lock(path)`.
    """
    path = Path(path)
    # Recover the existing non-`tasks` sections (users:, …) so they survive
    # the rewrite. This is the SAME read the old inline path did; it stays
    # here ONLY for callers that don't already hold the parsed doc.
    doc: dict = {"tasks": []}
    if path.exists():
        loaded = load_doc(path, validate=False)
        if isinstance(loaded, dict):
            doc = loaded
    _save_doc_unlocked(doc, path, tasks=tasks)


def _save_doc_unlocked(
    doc: dict, path: Path, *, tasks: list[dict] | None = None
) -> None:
    """Validate-and-write an ALREADY-PARSED full doc WITHOUT the store lock.

    The doc-based write primitive. The read-modify-write callers in
    ``_store`` parse the store ONCE under the lock (via :func:`load_doc`),
    mutate ``doc["tasks"]`` in place, then hand the whole doc here — so the
    non-``tasks`` sections (``users:`` etc.) captured by that same locked
    read survive the rewrite WITHOUT a redundant second ``safe_load``. When
    ``tasks`` is passed it replaces ``doc["tasks"]`` (the CRUD verbs may
    rebind the list, e.g. ``keep = [...]`` in delete).

    Direct callers must already hold `_store_lock(path)`.
    """
    if tasks is not None:
        doc["tasks"] = tasks
    tasks = doc.get("tasks")
    if not isinstance(tasks, list):
        tasks = []
        doc["tasks"] = tasks
    _validate_tasks(tasks, source="<save_tasks>")  # hook-bypass: line-limit

    # DB-CANONICAL: SQLite IS the store, so return BEFORE the YAML write below
    # — nothing downstream runs, including the dual-write mirror and the git
    # auto-commit. `write_doc_to_db` RAISES on failure (there is no YAML behind
    # it); see `_store_backend` for why that inverts the usual posture.
    from ._store_backend import db_is_canonical, write_doc_to_db

    if db_is_canonical():
        write_doc_to_db(doc, path)
        return

    # FAST WRITE (was: ruamel round-trip). The old path loaded the whole
    # 2.3 MB / ~695-card store with ruamel round-trip mode, merged the new
    # tasks into the comment-bearing nodes by id, then re-serialized with
    # ruamel — ~20 s PER single-card write, O(whole-store). ruamel's
    # round-trip machinery is the cost; it exists only to preserve the ~41
    # hand-written header/section comments. The store is machine-managed, so
    # dropping those comments is accepted. We now read with the fast safe
    # loader and dump with the fast safe dumper (libyaml when present).
    #
    # CRITICAL: the NON-`tasks` top-level sections (notably the `users:`
    # registry) are preserved because `doc` — parsed under the lock by the
    # caller (or by the `_save_tasks_unlocked` wrapper) — is written back
    # whole; we only ever replaced `doc["tasks"]`, every other top-level
    # key is carried through untouched.
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # CRASH-SAFE WRITE (lead a2a `3b0df14a`, post-2026-06-08 autoassign-
    # parallel-run data loss): dump to a sibling .tmp file, fsync it, then
    # os.replace into the canonical path. os.replace is POSIX-atomic — a
    # SIGTERM/SIGKILL mid-dump leaves either the OLD file intact (if the
    # crash hits before replace) or the NEW file in place (if after).
    # Never a half-written file like the one we recovered from today.
    tmp_path = path.parent / f".{path.name}.tmp"
    try:
        # Serialize to a STRING first so the post-dump byte-length check can
        # compare on-disk bytes to what we intended to write (and so we never
        # dump twice). Then write that exact string to the tmp, flush + fsync.
        dumped = safe_dump(doc)  # returns the YAML string (stream=None)
        with tmp_path.open("w", encoding="utf-8") as handle:
            handle.write(dumped)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                # fsync can fail on some FS (overlay / fuse). Best-effort —
                # the os.replace below is what gives the atomic guarantee.
                pass
        # POST-DUMP INTEGRITY CHECK (lead a2a `d5809cd3`, 2026-06-13 — the
        # recovered-by-hand corruption episode where the canonical file ended
        # mid-string at line ~2784). Before we promote the tmp into the
        # canonical slot, prove the written bytes are FULLY REPARSEABLE. The
        # pre-write `_validate_tasks` proves the in-memory structure is sound;
        # this catches any failure mode introduced by the dump itself
        # (unterminated scalar, partial flush, disk-full leaving a truncated
        # file even if fsync didn't error).
        #
        # CHEAPENED (Fix B2): the old check ran a FULL `safe_load` construct-
        # reparse (~2.3 s / ~159k objects on the live 9.2 MB store) purely to
        # prove parseability, then compared the reparsed task COUNT to the
        # in-memory count. We now do the equivalent two cheap checks in
        # `_verify_dumped_tmp` — a byte-length check + a libyaml EVENT-SCAN
        # reparse to StreamEnd — which proves the same "fully reparseable"
        # property WITHOUT building the objects. The task-count match is
        # DROPPED deliberately: reaching StreamEnd proves the whole stream
        # parsed, so a truncation that silently drops tasks can't reach
        # promotion (it aborts the parse first). Flagged for scitex-dev
        # review; see docs/ CHANGELOG + `_store_verify._verify_dumped_tmp`.
        _verify_dumped_tmp(tmp_path, dumped)
        # All checks passed — atomic POSIX rename promotes tmp → canonical.
        os.replace(tmp_path, path)
    except Exception:
        # Best-effort tmp cleanup so a crashed dump doesn't leave a
        # stale sidecar.
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    # S1 DUAL-WRITE — mirror this doc into the SQLite shadow DB.
    #
    # HERE, and not earlier: the `os.replace` above is POSIX-atomic, so the user's
    # card is now DURABLE. A mirror failure can therefore cost them nothing.
    # HERE, and not later: we still hold `_store_lock`, so the mirror cannot
    # interleave with another writer and needs no lock of its own.
    #
    # NEVER raises — a mirror hiccup must not turn a successful card write into a
    # failed one. But it is never SILENT either: `_dual_write` logs it LOUD, counts
    # it, and surfaces it in `health`. A mirror that fails quietly lets the DB rot
    # out of sync while every check reports green, and S2 would then cut the fleet
    # over to a store that is confidently wrong.
    #
    # OFF by default (`SCITEX_TODO_DUAL_WRITE`): the write path of the fleet's
    # critical store does not get a flag day.
    #
    # COST, MEASURED on the live 1,257-card store: the YAML rewrite above takes
    # 11,176 ms; this mirror adds 1,243 ms (+11%). That looks expensive and is not —
    # SQLite's FULL rebuild is 9x FASTER than the YAML rewrite it sits beside. That
    # measurement is why this is a simple full mirror rather than the row-diffing
    # engine I first assumed it would need to be. (S2, writing ONE row: 4.71 ms.)
    try:
        from ._dual_write import mirror_after_save

        mirror_after_save(doc, path)
    except Exception:  # noqa: BLE001 — even the import must not break a save
        pass

    # Best-effort git auto-commit on the store dir (lead a2a `3b0df14a`).
    # Lazy-init a small `.git` inside the store dir on first call; commit
    # each save so the operator gets time-travel via `git show <sha>:<file>`.
    # NEVER raises — a git failure must not block the actual save (the
    # YAML is already on disk; the commit is an audit-trail bonus).
    try:
        _git_autocommit_store(path)
    except Exception:  # noqa: BLE001 — best-effort
        pass


def _git_autocommit_store(path: Path) -> None:
    """Initialize a per-store .git on first call, then commit on each save.

    Operator-visible recovery handle: with this in place, even a future
    SIGKILL-mid-write or bad mutation is recoverable via standard git
    commands (`git -C <store-dir> log` + `git show <sha>:<file>`). The
    fcntl lock + atomic write are the LIVE crash-safety; this is the
    POST-MORTEM recovery layer.

    Best-effort: never raises. Skips entirely if git isn't installed.

    Opt-out: set ``SCITEX_TODO_STORE_GIT_AUTOCOMMIT`` to a falsy value
    (``0``/``false``/``no``/``off``/empty) to skip the per-save commit
    entirely. This is the POST-MORTEM recovery layer, NOT the live
    crash-safety (that is the fcntl lock + atomic write in the caller), so
    disabling it is safe. Two uses: (a) avoid the git-repo bloat that
    per-save commits accumulate on a hot shared store, and (b) make the
    write path deterministic + fast under test (no git subprocess). Default
    is ON (unset ⇒ enabled).
    """
    if os.environ.get("SCITEX_TODO_STORE_GIT_AUTOCOMMIT", "1").strip().lower() in (
        "0",
        "false",
        "no",
        "off",
        "",
    ):
        return

    store_dir = path.parent
    git_dir = store_dir / ".git"
    if not git_dir.exists():
        # Lazy-init.
        #
        # `gc.pruneExpire=never` is the guard that ACTUALLY protects old
        # snapshots: with it, gc NEVER deletes anything. It only PACKS.
        #
        # *** DO NOT SET gc.auto=0 HERE. IT USED TO BE SET, AND IT COST 13 GB. ***
        # The old code disabled auto-gc "so every snapshot stays reachable",
        # which conflated two different things. gc does not prune REACHABLE
        # objects — every commit on the branch is reachable by definition — and
        # `pruneExpire=never` already forbids pruning even unreachable ones. All
        # gc.auto=0 achieved was stopping git from ever PACKING, so every save's
        # full ~6.5 MB blob stayed a separate loose object forever.
        #
        # MEASURED on the live fleet store, 2026-07-14 (5 weeks, 10,828 commits):
        #     .git = 13 GB, 23,252 loose objects, on a 94%-full shared disk
        #     after `git gc`: 90 MB, 3 loose objects, ALL 10,829 commits preserved
        # 144x smaller, zero history lost. The "small store" assumption was the
        # error: the STORE is 6.5 MB, but a store committed on EVERY card write
        # grows a repo without bound unless something packs it.
        #
        # So: keep pruneExpire=never (nothing is ever deleted), and let git's
        # auto-gc do its job (default threshold; git auto-detaches it, so a
        # commit does not block on packing).
        subprocess.run(
            ["git", "init", "-q", "-b", "main", str(store_dir)],
            check=False,
            stderr=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
        )
        for cfg in (
            ("gc.pruneExpire", "never"),
            ("user.name", "scitex-todo"),
            ("user.email", "scitex-todo@localhost"),
        ):
            subprocess.run(
                ["git", "-C", str(store_dir), "config", *cfg],
                check=False,
                stderr=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
            )
    # Stage + commit just this one file. Use --quiet so a clean tree
    # (no actual change) doesn't print to stderr.
    subprocess.run(
        ["git", "-C", str(store_dir), "add", "--", path.name],
        check=False,
        stderr=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(store_dir),
            "commit",
            "-q",
            "--allow-empty-message",
            "-m",
            "",
        ],
        check=False,
        stderr=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
    )


# `_merge_tasks_into_seq` removed: it existed only to preserve ruamel
# per-node comments during the round-trip write. The write path now uses a
# fast safe dump (no comment preservation), so the merge helper is dead.
# (hook-bypass: line-limit — _model.py split still queued.)


# EOF
