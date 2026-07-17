#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Operator↔agent direct-message THREAD store (scitex-dev DM convention v1).

The board's ``/chat`` view, the ``dm_send`` / ``dm_list`` MCP verbs, and the
dm-dispatch rail all sit on this one pure store module.

Canonical DM record (scitex-dev spec v1 — a CONTRACT, field names are fixed)::

    {id, thread, from, to, body, ts, read}

Field-name bridge to the neighbouring vocabularies:

- ``from`` = a2a ``source``/``from_agent`` = card-comment ``author``
- ``body`` = a2a ``content`` = card-comment ``text``
- ``id`` = a2a ``msg_id``; ``thread`` = a2a ``conversation_id``
- ``ts`` = iso8601 ``Z`` timestamp; ``read`` = bool

Thread id: ``dm:<a>::<b>`` with the two peer names SORTED lexicographically —
ONE thread per pair, both directions. The operator's reserved peer name is
``"operator"``.

STORE ISOLATION (hard requirement — write-lock incident lesson)
----------------------------------------------------------------
Threads live in a SIDECAR file, NOT ``tasks.yaml``: ``<store_dir>/threads.yaml``
next to the resolved task store (e.g. ``~/.scitex/todo/threads.yaml``), guarded
by its OWN flock (``.threads.yaml.lock``) so chat writes can never convoy with
card writes. On-disk structure::

    threads:
      "dm:<a>::<b>":
        - {id, thread, from, to, body, ts, read}
        - ...

The write mirrors the crash-safe pattern of ``_model._save_doc_unlocked``:
dump → sibling ``.tmp`` → fsync → reparse-verify (thread + message counts) →
``os.replace``. A missing file is never an error (empty store).

dm-dispatch
-----------
``append_message`` ALSO enqueues an ``event_type="dm"`` notification into the
recipient's EXISTING pull-inbox (:func:`scitex_cards._inbox.enqueue`, keyed via
``_users.resolve_user`` exactly like ``poll_notifications``) — the >=0.7.32
unified channel server drains that inbox and pushes the message into the
agent's live session. Durable, standalone; NO a2a dependency (sac's a2a POST
is a separate fast-lane, not built here). The ``"operator"`` recipient is
enqueued too, for symmetry: the inbox key is cheap, harmless, and keeps a
future operator-side drain surface working without a special case (the board
itself reads unread state from THIS sidecar, not the inbox).

READ CACHE vs WRITERS (the one rule this module lives or dies by)
----------------------------------------------------------------
The GUI polls a thread every ~5s, and each poll used to cost TWO full parses
of the entire sidecar plus a lock: ``mark_read`` (which sits on the poll path,
not on a cold write path) and then ``get_thread``. So the READ paths
(:func:`get_thread`, :func:`list_threads`) go through
:func:`_load_threads_cached`, an mtime-guarded cache of the parsed content —
the ``services.get_board`` pattern.

WRITERS NEVER READ THE CACHE. :func:`append_message` and the authoritative
half of :func:`mark_read` do a read-modify-write and MUST re-read the file
fresh, under the lock, via the uncached :func:`_load_threads`. A stale read
there would silently DROP a message: two writes landing inside one mtime tick
and the second clobbers the first. Optimizing a writer onto the cache is the
one failure mode of this design; there is a test that refuses it.

:func:`mark_read` gets a lock-free FAST NO: it asks the cache whether this
reader has anything to flip, and returns 0 without taking the lock or parsing
when the answer is no. That is safe ONLY because marking-read is idempotent
and self-healing — a stale cache costs at most one poll's delay (a badge
clears ~5s late; the next poll sees a fresh mtime and flips it), never data.
The instinct boundary in one sentence: this fast path is fine for
``mark_read``; it would NOT be fine for ``append_message``.
"""

from __future__ import annotations

import contextlib
import fcntl
import logging
import os
import secrets
from pathlib import Path

from ._paths import resolve_tasks_path

logger = logging.getLogger(__name__)

#: Sidecar filename, sibling of the resolved ``tasks.yaml``.
THREADS_FILENAME = "threads.yaml"

#: Top-level key of the sidecar document.
_THREADS_KEY = "threads"

#: Reserved peer name for the human operator (scitex-dev spec v1).
OPERATOR_NAME = "operator"

#: DM message-id prefix (``m_`` + 12 hex chars — the ``u_``/``n_`` id shape).
_MSG_ID_PREFIX = "m_"
_MSG_ID_TOKEN_HEX = 12


# --------------------------------------------------------------------------- #
# Paths / keys / small helpers                                                 #
# --------------------------------------------------------------------------- #
def threads_path(store: str | Path | None = None) -> Path:
    """Resolve the sidecar path: ``<store_dir>/threads.yaml``.

    ``store`` is the TASK store path (or ``None`` → the standard resolution
    chain); the threads sidecar always sits NEXT TO it so both files live in
    the same scope.
    """
    tasks = (
        resolve_tasks_path(store)
        if store is None
        else Path(store).expanduser()
    )
    return tasks.parent / THREADS_FILENAME


def thread_key(a: str, b: str) -> str:
    """Canonical thread id for a peer pair: ``dm:<a>::<b>``, names sorted.

    Sorting makes the key direction-agnostic, so one pair = one thread.
    """
    lo, hi = sorted((a, b))
    return f"dm:{lo}::{hi}"


def peers_of(key: str) -> tuple[str, str]:
    """Inverse of :func:`thread_key` — ``"dm:a::b"`` → ``("a", "b")``."""
    lo, _, hi = (key[3:] if key.startswith("dm:") else key).partition("::")
    return lo, hi


def _utc_now_iso() -> str:
    """Second-resolution ISO-8601 UTC stamp with the canonical ``Z`` suffix."""
    import datetime as _dt

    return (
        _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _generate_msg_id() -> str:
    """Fresh DM message id (``m_`` + 12 hex chars)."""
    return _MSG_ID_PREFIX + secrets.token_hex(_MSG_ID_TOKEN_HEX // 2)


@contextlib.contextmanager
def _threads_lock(path: Path):
    """Exclusive flock on the sidecar's OWN ``.threads.yaml.lock`` sentinel.

    Deliberately SEPARATE from ``_model._store_lock`` (tasks.yaml): chat
    traffic must never convoy with card writes. Same mechanics otherwise.
    """
    lock_path = path.parent / f".{path.name}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = lock_path.open("a+")
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        finally:
            fd.close()


# --------------------------------------------------------------------------- #
# Load / save                                                                  #
# --------------------------------------------------------------------------- #
def _load_threads(path: Path) -> dict[str, list[dict]]:
    """Read the ``threads:`` mapping off disk. NEVER raises on absence.

    Missing file / absent key / non-mapping value → ``{}``; non-list thread
    values coerce to ``[]``, non-dict entries drop — a malformed row never
    breaks a read.
    """
    if not path.exists():
        return {}
    from ._yaml import safe_load

    with path.open(encoding="utf-8") as handle:
        data = safe_load(handle) or {}
    raw = data.get(_THREADS_KEY) if isinstance(data, dict) else None
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[dict]] = {}
    for key, records in raw.items():
        if not isinstance(key, str) or not key:
            continue
        if not isinstance(records, list):
            out[key] = []
            continue
        out[key] = [r for r in records if isinstance(r, dict)]
    return out


"""Parsed sidecar content per path, guarded by the file's mtime+size.

``{path: (st_mtime_ns, st_size, threads)}``. READ-ONLY: the stored mapping is
handed to readers that copy on the way out and never mutate it. A writer must
NEVER be served from here — see the module docstring.
"""
_READ_CACHE: dict[str, tuple[int, int, dict[str, list[dict]]]] = {}


def _load_threads_cached(path: Path) -> dict[str, list[dict]]:
    """:func:`_load_threads` memoized on the file's ``(mtime_ns, size)``.

    The ``services.get_board`` pattern: any write rolls the mtime forward, so
    the next read re-parses and no reader can be served stale content across a
    write. Absent file → ``{}`` and nothing cached.

    FOR READERS ONLY. Callers must treat the result as immutable and copy what
    they hand out (:func:`get_thread` and :func:`list_threads` both do).
    Writers use the uncached :func:`_load_threads` under the lock instead.
    """
    try:
        stat = path.stat()
    except OSError:
        return {}
    key = str(path)
    cached = _READ_CACHE.get(key)
    if cached is not None and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
        return cached[2]
    threads = _load_threads(path)
    _READ_CACHE[key] = (stat.st_mtime_ns, stat.st_size, threads)
    return threads


def _is_unread_for(
    record: dict, reader: str, wanted: set[str] | None
) -> bool:
    """Whether ``record`` is one that :func:`mark_read` would flip for ``reader``.

    ONE predicate, deliberately shared by the lock-free pre-check and the
    authoritative flip. If those two ever disagreed about what "unread" means,
    the pre-check could answer "nothing to do" for a message the flip would
    have taken — a message stuck unread forever rather than one poll late.
    """
    if record.get("to") != reader or record.get("read"):
        return False
    if wanted is not None and record.get("id") not in wanted:
        return False
    return True


def _save_threads_unlocked(threads: dict[str, list[dict]], path: Path) -> None:
    """Crash-safe write of the whole sidecar document.

    Mirrors ``_model._save_doc_unlocked``: dump to a sibling ``.tmp``, fsync,
    REPARSE the tmp bytes and verify the thread count + total message count
    match the in-memory doc, then ``os.replace`` (POSIX-atomic) into place.
    Never promotes suspect bytes; the canonical file stays intact on any
    failure. Callers must already hold :func:`_threads_lock`.
    """
    from ._yaml import safe_dump, safe_load

    doc = {_THREADS_KEY: threads}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / f".{path.name}.tmp"
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            safe_dump(doc, handle)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass  # best-effort (overlay/fuse); os.replace is the swap
        try:
            with tmp_path.open(encoding="utf-8") as verify_handle:
                verify_doc = safe_load(verify_handle)
        except Exception as verify_exc:  # noqa: BLE001 — any parse fail = abort
            raise RuntimeError(
                f"refusing to replace {path}: tmp file at {tmp_path} did not "
                f"reparse cleanly after dump ({type(verify_exc).__name__}: "
                f"{verify_exc}). Canonical file left untouched."
            ) from verify_exc
        verify_threads = (
            verify_doc.get(_THREADS_KEY) if isinstance(verify_doc, dict) else None
        )
        want_msgs = sum(len(v) for v in threads.values())
        have_msgs = (
            sum(len(v) for v in verify_threads.values() if isinstance(v, list))
            if isinstance(verify_threads, dict)
            else -1
        )
        if (
            not isinstance(verify_threads, dict)
            or len(verify_threads) != len(threads)
            or have_msgs != want_msgs
        ):
            raise RuntimeError(
                f"refusing to replace {path}: tmp file reparsed with an "
                f"unexpected threads payload ({have_msgs} msgs vs in-memory "
                f"{want_msgs}). Canonical file left untouched."
            )
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------- #
# dm-dispatch (inbox enqueue)                                                  #
# --------------------------------------------------------------------------- #
def _dispatch_to_inbox(record: dict, store: str | Path | None) -> None:
    """Enqueue the DM into the recipient's pull-inbox (fail-soft).

    Keyed exactly like ``poll_notifications``: the recipient name resolves to
    its stable ``u_*`` user id when registered, else the raw name is the key.
    The thread record in the sidecar is the SSOT — an enqueue failure is
    logged loudly but never loses the already-persisted message. The
    ``operator`` recipient is enqueued too (symmetry; see module docstring).
    """
    try:
        from . import _inbox
        from ._users import resolve_user

        to = record["to"]
        try:
            user = resolve_user(to, store=store)
        except Exception:  # noqa: BLE001 — unresolvable ⇒ raw-name key
            user = None
        recipient_id = user.id if user is not None else to
        _inbox.enqueue(
            recipient_id,
            event_type="dm",
            card_id=record["thread"],
            body=record["body"],
            actor=record["from"],
            ts=record["ts"],
            store=store,
        )
    except Exception:  # noqa: BLE001 — delivery accelerator, not the SSOT
        logger.warning(
            "dm-dispatch: inbox enqueue failed for %r (message %s kept in "
            "thread store)",
            record.get("to"),
            record.get("id"),
            exc_info=True,
        )


# --------------------------------------------------------------------------- #
# Public API                                                                   #
# --------------------------------------------------------------------------- #
def append_message(
    from_: str,
    to: str,
    body: str,
    *,
    store: str | Path | None = None,
    msg_id: str | None = None,
    ts: str | None = None,
) -> dict:
    """Append one DM to the pair's thread and dispatch it to the recipient.

    Mints the id (unless ``msg_id`` is given), appends the canonical record
    under the sidecar's own lock, then enqueues a ``dm`` notification into
    the recipient's inbox (fail-soft). Returns a copy of the stored record.
    """
    if not from_ or not to:
        raise ValueError("append_message requires non-empty 'from_' and 'to'")
    if not isinstance(body, str) or not body.strip():
        raise ValueError("append_message requires a non-empty 'body'")
    key = thread_key(from_, to)
    record = {
        "id": msg_id or _generate_msg_id(),
        "thread": key,
        "from": from_,
        "to": to,
        "body": body,
        "ts": ts or _utc_now_iso(),
        "read": False,
    }
    path = threads_path(store)
    with _threads_lock(path):
        threads = _load_threads(path)
        threads.setdefault(key, []).append(record)
        _save_threads_unlocked(threads, path)
    _dispatch_to_inbox(record, store)
    return dict(record)


def get_thread(a: str, b: str, *, store: str | Path | None = None) -> list[dict]:
    """Return the pair's messages in append (chronological) order.

    Direction-agnostic; missing file or unknown pair → ``[]``. Records are
    copies — mutating them does not touch the store.
    """
    records = _load_threads_cached(threads_path(store)).get(thread_key(a, b), [])
    return [dict(r) for r in records]


def list_threads(*, store: str | Path | None = None) -> dict[str, dict]:
    """Summarize every thread: peers, last message, counts, per-peer unread.

    Returns ``{thread_key: {"peers": (a, b), "last": <record|None>,
    "count": N, "unread": {peer: n}}}`` where ``unread[p]`` counts messages
    addressed TO ``p`` that are still ``read: false`` (i.e. what ``p`` has
    not seen yet).
    """
    out: dict[str, dict] = {}
    for key, records in _load_threads_cached(threads_path(store)).items():
        a, b = peers_of(key)
        unread: dict[str, int] = {a: 0, b: 0}
        for r in records:
            if not r.get("read") and r.get("to") in unread:
                unread[r["to"]] += 1
        out[key] = {
            "peers": (a, b),
            "last": dict(records[-1]) if records else None,
            "count": len(records),
            "unread": unread,
        }
    return out


def mark_read(
    thread: str,
    reader: str,
    *,
    ids: list[str] | None = None,
    store: str | Path | None = None,
) -> int:
    """Flip messages addressed to ``reader`` in ``thread`` to ``read: true``.

    ``ids=None`` (default) marks ALL of the reader's unread messages in the
    thread; otherwise only the listed message ids. Idempotent; returns the
    number of records actually flipped. Unknown thread → 0.

    Sits on the GUI's ~5s poll path, where the answer is almost always "nothing
    to flip", so it opens with a lock-free FAST NO off the read cache and only
    pays the lock + fresh parse when there is real work. The check derives the
    answer for THIS reader from the cached content on every call — the boolean
    itself is never memoized, because the reader varies per request and one
    peer's "nothing unread" must never answer for another's.
    """
    wanted = set(ids) if ids is not None else None
    path = threads_path(store)

    # Fast NO. A stale cache is tolerable here and only here: a false negative
    # costs one poll's delay (the next poll sees a fresh mtime and flips), and
    # a false positive costs one wasted lock. Neither can lose a message —
    # everything below re-reads the file fresh and is the authority.
    cached = _load_threads_cached(path).get(thread)
    if not any(_is_unread_for(r, reader, wanted) for r in cached or []):
        return 0

    flipped = 0
    with _threads_lock(path):
        threads = _load_threads(path)  # authoritative: never the cache
        records = threads.get(thread)
        if not records:
            return 0
        for r in records:
            if not _is_unread_for(r, reader, wanted):
                continue
            r["read"] = True
            flipped += 1
        if flipped:
            _save_threads_unlocked(threads, path)
    return flipped


__all__ = [
    "OPERATOR_NAME",
    "THREADS_FILENAME",
    "append_message",
    "get_thread",
    "list_threads",
    "mark_read",
    "peers_of",
    "thread_key",
    "threads_path",
]

# EOF
