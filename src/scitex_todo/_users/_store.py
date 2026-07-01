#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Persistence + registry API for the standalone user registry.

Users live in the SAME store file as tasks, under a top-level ``users:``
key (a sibling of ``tasks:``). This module reuses the task store's
``_model._store_lock`` advisory lock and a ruamel round-trip writer so the
``tasks:`` payload + inline comments + key order survive every user write
untouched (and vice versa) — there is NO separate users file.

Standalone constraint: ZERO external-runtime / fleet imports. The id format is ``u_`` +
12 hex chars (48 bits, :func:`secrets.token_hex`); ids are generated here,
stable for life, and never reused.
"""

from __future__ import annotations

import secrets
from pathlib import Path

from .._model import _store_lock
from .._paths import resolve_tasks_path
from ._model import User, UserValidationError, validate_user

#: Stable user-id prefix. See package docstring for the full format.
_USER_ID_PREFIX = "u_"

#: Number of hex chars in the random token portion of a user id (48 bits).
_USER_ID_TOKEN_HEX = 12


# --------------------------------------------------------------------------- #
# Internal helpers                                                            #
# --------------------------------------------------------------------------- #
def _resolved_store(store: str | Path | None) -> Path:
    """Resolve a store path through the same chain the task API uses."""
    return resolve_tasks_path(store) if store is None else Path(store).expanduser()


def _utc_now_iso() -> str:
    """ISO-8601 UTC timestamp with the canonical ``Z`` suffix.

    Identical second-resolution shape to ``_store._utc_now_iso`` so user and
    task timestamps are consistent on disk; re-implemented locally to keep
    this package free of an import cycle with the task mutation layer.
    """
    import datetime as _dt

    return (
        _dt.datetime.now(_dt.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _load_users_section(path: Path) -> list[dict]:
    """Read the raw ``users:`` list off disk (absent / non-list → []).

    Uses the fast safe loader (:func:`scitex_todo._yaml.safe_load`) — this is a
    READ-only snapshot re-parsed on every ``resolve_user``, so the libyaml
    speedup matters; the ruamel round-trip is only needed on the WRITE path to
    preserve comments. Validates each row via :func:`validate_user` so a
    malformed registry fails loud on read.
    """
    if not path.exists():
        return []
    from .._yaml import safe_load

    with path.open(encoding="utf-8") as handle:
        data = safe_load(handle) or {}
    users = data.get("users")
    if not isinstance(users, list):
        return []
    out: list[dict] = []
    for row in users:
        if not isinstance(row, dict):
            raise UserValidationError(
                f"{path}: each user must be a mapping: {row!r}"
            )
        validate_user(row)
        out.append(row)
    return out


def _save_users_unlocked(users: list[dict], path: Path) -> None:
    """Write the ``users:`` section, preserving the ``tasks:`` payload.

    Reuses the ruamel round-trip writer so the existing ``tasks:`` list,
    its inline comments, and document key order survive untouched — only
    the ``users:`` key is replaced. Mirrors the atomic tmp-file + os.replace
    + reparse-verify dance in ``_model._save_tasks_unlocked``.

    Direct callers MUST already hold ``_store_lock(path)``.
    """
    import os

    from ruamel.yaml import YAML

    for row in users:
        validate_user(row)

    yaml_rt = YAML()
    yaml_rt.preserve_quotes = True
    yaml_rt.indent(mapping=2, sequence=4, offset=2)

    doc = None
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            loaded = yaml_rt.load(handle)
        if isinstance(loaded, dict):
            doc = loaded
    if doc is None:
        doc = {}
    doc["users"] = users
    # Keep the document valid for ``_model.load_tasks`` even when this is a
    # users-FIRST write (no task ever added yet): that loader hard-requires a
    # top-level ``tasks:`` list, so a file carrying only ``users:`` would make
    # a later ``add_task`` (which calls ``load_tasks`` on the existing file)
    # fail-loud. Seed an empty ``tasks:`` list when absent; never touch an
    # existing one (the round-trip preserves its payload + comments).
    if not isinstance(doc.get("tasks"), list):
        doc["tasks"] = []

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / f".{path.name}.tmp"
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            yaml_rt.dump(doc, handle)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        # Reparse-verify the tmp file before promoting it — never replace
        # the canonical SSOT with bytes that don't round-trip.
        try:
            with tmp_path.open(encoding="utf-8") as verify_handle:
                verify_doc = yaml_rt.load(verify_handle)
        except Exception as verify_exc:  # noqa: BLE001 — any parse fail = abort
            raise RuntimeError(
                f"refusing to replace {path}: tmp file at {tmp_path} did "
                f"not reparse cleanly after dump "
                f"({type(verify_exc).__name__}: {verify_exc}). Canonical "
                f"file left untouched."
            ) from verify_exc
        verify_users = (
            verify_doc.get("users") if isinstance(verify_doc, dict) else None
        )
        if not isinstance(verify_users, list) or len(verify_users) != len(users):
            raise RuntimeError(
                f"refusing to replace {path}: tmp file reparsed with an "
                f"unexpected users payload. Canonical file left untouched."
            )
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _generate_user_id(existing_ids: set[str]) -> str:
    """Generate a fresh stable id not present in ``existing_ids``.

    Re-rolls on the vanishingly rare clash with a currently-registered id.
    Ids of REMOVED users are not tracked here; the 48-bit random token
    makes accidental reuse of a past id astronomically unlikely.
    """
    while True:
        uid = _USER_ID_PREFIX + secrets.token_hex(_USER_ID_TOKEN_HEX // 2)
        if uid not in existing_ids:
            return uid


def _names_index(users: list[dict]) -> dict[str, str]:
    """Map every registered name → its owning user id (uniqueness checks)."""
    index: dict[str, str] = {}
    for u in users:
        for name in u.get("names") or []:
            index[name] = u.get("id")
    return index


# --------------------------------------------------------------------------- #
# Public registry API                                                         #
# --------------------------------------------------------------------------- #
def load_users(store: str | Path | None = None) -> list[User]:
    """Return all registered users (absent ``users:`` section → ``[]``).

    Read-only snapshot — does NOT lock. Each row is validated on read
    (fail-loud on a malformed registry) and returned as a :class:`User`.
    """
    path = _resolved_store(store)
    return [User.from_dict(d) for d in _load_users_section(path)]


def list_users(store: str | Path | None = None) -> list[User]:
    """Alias of :func:`load_users` — return every registered user."""
    return load_users(store)


def get_user(user_id: str, store: str | Path | None = None) -> "User | None":
    """Return the user with id ``user_id``, or ``None`` if not registered."""
    if not user_id:
        return None
    for user in load_users(store):
        if user.id == user_id:
            return user
    return None


def register_user(
    *,
    kind: str,
    names: "list[str] | str",
    host_at_name: str | None = None,
    notify: dict | None = None,
    turn_url: str | None = None,
    a2a_port: int | None = None,
    store: str | Path | None = None,
) -> User:
    """Register a new user with a freshly generated stable id.

    Validates the record, rejects any name that already maps to an existing
    user (names are UNIQUE across the registry — fail-loud), then appends
    and persists atomically under the shared store lock. Returns the
    created :class:`User`.

    Parameters
    ----------
    kind : str
        One of :data:`VALID_USER_KINDS`.
    names : list[str] | str
        One or more display-name aliases (a bare string is accepted as a
        single-element list). At least one non-empty string.
    host_at_name : str, optional
        Optional canonical ``host@name`` join key.
    notify : dict, optional
        Opaque notify-config bag (stored verbatim; default ``{}``).
    turn_url : str, optional
        Optional explicit delivery endpoint (the agent's HTTP turn URL).
        Validated as a non-empty string when present. See
        :func:`scitex_todo._users.user_turn_url` for how it is consumed.
    a2a_port : int, optional
        Optional a2a listen port; when set (and no ``turn_url``) the turn
        URL is derived as ``http://<host>:<a2a_port>/v1/turn``. Validated
        as a positive int when present.
    store : str | Path, optional
        Store path override (default: the resolved task store).

    Raises
    ------
    UserValidationError
        On any structural fault, or if a provided name is already taken by
        another user.
    """
    if isinstance(names, str):
        names = [names]
    names = list(names or [])
    path = _resolved_store(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _store_lock(path):
        users = _load_users_section(path)
        existing_ids = {u.get("id") for u in users if u.get("id")}
        name_owner = _names_index(users)
        for name in names:
            if name in name_owner:
                raise UserValidationError(
                    f"cannot register user: name {name!r} already belongs "
                    f"to user {name_owner[name]!r}"
                )
        new = User(
            id=_generate_user_id(existing_ids),  # type: ignore[arg-type]
            kind=kind,
            names=names,
            host_at_name=host_at_name,
            notify=dict(notify) if notify else {},
            turn_url=turn_url,
            a2a_port=a2a_port,
            created_at=_utc_now_iso(),
        )
        validate_user(new)
        users.append(new.to_dict())
        _save_users_unlocked(users, path)
    return new


def add_alias(
    user_id: str,
    name: str,
    store: str | Path | None = None,
) -> User:
    """Add ``name`` to ``user_id``'s ``names`` list (idempotent).

    No-op (returns the user unchanged) when the name is already one of the
    user's aliases. Rejects (fail-loud) a name that already belongs to a
    DIFFERENT user — names stay unique across the registry. Returns the
    updated :class:`User`.

    Raises
    ------
    UserValidationError
        If ``name`` is empty, ``user_id`` is unknown, or ``name`` already
        belongs to another user.
    """
    if not (isinstance(name, str) and name):
        raise UserValidationError(
            f"add_alias: name must be a non-empty string (got {name!r})"
        )
    path = _resolved_store(store)
    with _store_lock(path):
        users = _load_users_section(path)
        name_owner = _names_index(users)
        owner = name_owner.get(name)
        if owner is not None and owner != user_id:
            raise UserValidationError(
                f"cannot add alias {name!r} to user {user_id!r}: it already "
                f"belongs to user {owner!r}"
            )
        target = next((u for u in users if u.get("id") == user_id), None)
        if target is None:
            raise UserValidationError(
                f"add_alias: unknown user id {user_id!r}"
            )
        current = list(target.get("names") or [])
        if name not in current:
            current.append(name)
            target["names"] = current
            validate_user(target)
            _save_users_unlocked(users, path)
        return User.from_dict(target)


def set_notify(
    user_id: str,
    notify: dict,
    store: str | Path | None = None,
) -> User:
    """Replace ``user_id``'s opaque ``notify`` dict. Returns the updated user.

    The contents are stored verbatim (not interpreted here). Raises
    :class:`UserValidationError` if ``user_id`` is unknown or ``notify`` is
    not a mapping.
    """
    if not isinstance(notify, dict):
        raise UserValidationError(
            f"set_notify: notify must be a mapping (got {notify!r})"
        )
    path = _resolved_store(store)
    with _store_lock(path):
        users = _load_users_section(path)
        target = next((u for u in users if u.get("id") == user_id), None)
        if target is None:
            raise UserValidationError(
                f"set_notify: unknown user id {user_id!r}"
            )
        target["notify"] = dict(notify)
        validate_user(target)
        _save_users_unlocked(users, path)
        return User.from_dict(target)


def touch_user(
    name_or_id: str,
    store: str | Path | None = None,
) -> "User | None":
    """Stamp ``last_seen = now(UTC)`` on the acting agent's registry record.

    The heartbeat write. ``name_or_id`` is resolved via the SAME identity
    seam as everything else (:func:`resolve_user`, then an exact id match),
    so there is NO second identity path. Returns the updated :class:`User`,
    or ``None`` when the actor maps to no registered user (an UNREGISTERED
    actor has no record to stamp — the caller decides whether that is
    tolerable; the heartbeat itself never raises for that case).

    This is scitex-todo's OWN liveness signal — a local write to the local
    registry, NEVER an external-runtime probe.
    """
    if not (isinstance(name_or_id, str) and name_or_id.strip()):
        return None
    key = name_or_id.strip()
    path = _resolved_store(store)
    with _store_lock(path):
        users = _load_users_section(path)
        target = None
        # Resolution order mirrors resolve_user: exact id → name alias →
        # host_at_name join key. One identity seam, no second path.
        for u in users:
            if u.get("id") == key or key in (u.get("names") or []):
                target = u
                break
        if target is None:
            for u in users:
                if u.get("host_at_name") and u.get("host_at_name") == key:
                    target = u
                    break
        if target is None:
            return None
        target["last_seen"] = _utc_now_iso()
        validate_user(target)
        _save_users_unlocked(users, path)
        return User.from_dict(target)


def resolve_user(
    name_or_host_at_name: str,
    store: str | Path | None = None,
) -> "User | None":
    """Resolve a card-owner string to its registered :class:`User`.

    Resolution order:

    1. EXACT match against any user's ``names`` alias (so an OLD name still
       resolves after a rename, as long as the old name was kept in
       ``names``).
    2. Match against any user's ``host_at_name`` join key.
    3. CANONICALISED retry: run the raw string through
       :func:`scitex_todo._users.canonical_identity` (NON-strict) to collapse
       naming drift (``proj-scitex-dev`` -> ``scitex-dev``, ``sac`` ->
       ``scitex-agent-container``) and re-try steps 1/2 on the canonical
       form. This only ever ADDS a resolution that steps 1/2 missed — a
       string that resolved before still resolves the same way — so it is a
       pure, back-compatible widening.

    Returns ``None`` when the string maps to no registered user — callers
    fall back to the raw name string, which preserves back-compat with the
    pre-registry world where owners were just free-form strings.
    """
    if not name_or_host_at_name:
        return None
    users = load_users(store)

    def _exact(key: str) -> "User | None":
        for user in users:
            if key in user.names:
                return user
        for user in users:
            if user.host_at_name and user.host_at_name == key:
                return user
        return None

    hit = _exact(name_or_host_at_name)
    if hit is not None:
        return hit

    # (3) canonicalise (non-strict, reusing the snapshot already loaded) and
    # retry — never raises, only widens.
    from ._identity import canonical_identity

    canonical = canonical_identity(
        name_or_host_at_name, users=users, strict=False
    )
    if canonical != name_or_host_at_name:
        return _exact(canonical)
    return None


__all__ = [
    "add_alias",
    "get_user",
    "list_users",
    "load_users",
    "register_user",
    "resolve_user",
    "set_notify",
    "touch_user",
]

# EOF
