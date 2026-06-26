#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""User model + validation for the standalone scitex-todo user registry.

See :mod:`scitex_todo._users` for the package-level overview (storage
decision, id format, standalone constraint). This module holds the closed
``VALID_USER_KINDS`` set, the :class:`User` dataclass (the single schema
source — mirrors how ``_model.Task`` is the task schema), and the fail-loud
:func:`validate_user` gate (mirrors ``_model._validate_tasks``).

Standalone constraint: the only cross-module reference is
:func:`scitex_todo._ports.canonical_agent_id`, reused PURELY as a local
``host@name`` string normaliser/validator — NO sac import, NO runtime pull.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from dataclasses import fields as _dc_fields

from .._ports import AgentIdentityError, canonical_agent_id, parse_agent_id

#: Closed, validated set of user kinds — fail-loud on unknown values,
#: mirroring ``_model.VALID_KINDS`` / ``VALID_STATUSES``. A ``human`` has a
#: board identity but no agent runtime; an ``agent`` may additionally carry
#: a ``host_at_name`` join key. Extensible by editing this tuple
#: (closed-in-the-typo sense).
VALID_USER_KINDS: tuple[str, ...] = ("human", "agent")


class UserValidationError(ValueError):
    """Raised when a user record fails structural validation.

    Mirrors :class:`scitex_todo._model.TaskValidationError`: the message
    always echoes the offending value plus the valid set / expected shape,
    per the fail-loud SciTeX convention.
    """


@dataclass(slots=True)
class User:
    """A board member — human or agent — with a stable id and alias list.

    Attributes
    ----------
    id : str
        Stable, generated, never-reused handle (``u_`` + 12 hex chars). The
        durable identity a card owner string resolves to; survives renames.
    kind : str
        One of :data:`VALID_USER_KINDS` (``"human"`` / ``"agent"``).
    names : list[str]
        Display-name aliases — the current name PLUS any historical names
        (e.g. a pre-rename ``proj-*`` alias kept so old card references
        still resolve). At least one non-empty string; unique across the
        whole registry (no two users share a name).
    host_at_name : str | None
        Optional canonical ``host@name`` join key (validated via
        :func:`scitex_todo._ports.canonical_agent_id` when present). The
        eventual sac identity bridge will MATCH on this, but this package
        never pulls runtime — it only stores/validates the string.
    notify : dict
        Reserved, opaque per-user notify-config bag (default ``{}``). This
        package stores and round-trips it verbatim and does NOT interpret
        its contents — the notify-config layer is a separate concern.
    turn_url : str | None
        Optional explicit delivery endpoint — the agent's HTTP turn URL
        that :func:`scitex_todo._push.deliver` POSTs a board event to. When
        present it is used verbatim (highest precedence in
        :func:`user_turn_url`). ``None`` (the default) means "not pinned";
        the URL may then be derived from :attr:`a2a_port`. The shape
        matches sac's ``agent_registered`` bus envelope, whose consumer (a
        separate card) populates this field — this package only stores it.
    a2a_port : int | None
        Optional a2a listen port. When set (and no explicit
        :attr:`turn_url`), :func:`user_turn_url` derives the endpoint as
        ``http://<host>:<a2a_port>/v1/turn`` where ``<host>`` is the host
        half of :attr:`host_at_name` (loopback when the id is bare). This
        is the field sac's ``agent_registered`` envelope carries alongside
        ``host_at_name``; absent → ``None`` (backward-compatible).
    created_at : str
        ISO-8601 UTC timestamp stamped at registration.
    """

    id: str
    kind: str
    names: list[str] = field(default_factory=list)
    host_at_name: str | None = None
    notify: dict = field(default_factory=dict)
    turn_url: str | None = None
    a2a_port: int | None = None
    created_at: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> "User":
        """Construct from a ``users:`` row, dropping unknown keys.

        Defensive (forward-compat): unknown keys are ignored, missing keys
        fall back to the dataclass default, and ``None`` list/dict values
        are replaced with the empty default so downstream code can iterate
        without ``None`` checks. Does NOT validate — that is
        :func:`validate_user`'s job (the read path calls it separately).
        """
        valid_names = {f.name for f in _dc_fields(cls)}
        kwargs: dict[str, object] = {
            k: v for k, v in d.items() if k in valid_names
        }
        if kwargs.get("names") is None:
            kwargs.pop("names", None)
        if kwargs.get("notify") is None:
            kwargs.pop("notify", None)
        return cls(**kwargs)  # type: ignore[arg-type]

    def to_dict(self) -> dict:
        """Round-trip to a plain dict for the ruamel writer.

        Emits ``id`` / ``kind`` / ``names`` / ``created_at`` always; omits
        ``host_at_name`` when ``None``, ``notify`` when empty, and the
        delivery-endpoint fields (``turn_url`` / ``a2a_port``) when ``None``
        so the YAML stays compact (symmetry with ``Task.to_dict``).
        """
        result: dict[str, object] = {
            "id": self.id,
            "kind": self.kind,
            "names": list(self.names),
            "created_at": self.created_at,
        }
        if self.host_at_name is not None:
            result["host_at_name"] = self.host_at_name
        if self.notify:
            result["notify"] = dict(self.notify)
        if self.turn_url is not None:
            result["turn_url"] = self.turn_url
        if self.a2a_port is not None:
            result["a2a_port"] = self.a2a_port
        return result


def validate_user(user: "User | dict") -> None:
    """Fail-loud structural validation of a user record.

    Accepts either a :class:`User` or a plain ``users:`` dict (the read
    path validates dicts straight off disk). Raises
    :class:`UserValidationError` echoing the bad value on the first fault:

    - ``id`` missing / not a non-empty string,
    - ``kind`` not in :data:`VALID_USER_KINDS`,
    - ``names`` not a list, empty, or containing a non-string / empty
      string, or containing duplicate names within the same user,
    - ``host_at_name`` present but not a non-empty string or not a
      well-formed ``host@name`` (delegated to :func:`canonical_agent_id`),
    - ``notify`` present but not a mapping,
    - ``turn_url`` present but not a non-empty string,
    - ``a2a_port`` present but not a positive int (``bool`` rejected — it
      is an ``int`` subclass but never a valid port).
    """
    d = user.to_dict() if isinstance(user, User) else user
    if not isinstance(d, dict):
        raise UserValidationError(f"user must be a mapping: {d!r}")

    uid = d.get("id")
    if not (isinstance(uid, str) and uid):
        raise UserValidationError(
            f"user is missing a non-empty string 'id': {d!r}"
        )

    kind = d.get("kind")
    if kind not in VALID_USER_KINDS:
        raise UserValidationError(
            f"user {uid!r} has invalid kind {kind!r}; "
            f"must be one of {VALID_USER_KINDS}"
        )

    names = d.get("names")
    if not isinstance(names, list) or not names:
        raise UserValidationError(
            f"user {uid!r} has invalid names {names!r}; "
            f"names must be a non-empty list of strings"
        )
    seen: set[str] = set()
    for name in names:
        if not (isinstance(name, str) and name):
            raise UserValidationError(
                f"user {uid!r} has an invalid name {name!r}; "
                f"each name must be a non-empty string"
            )
        if name in seen:
            raise UserValidationError(
                f"user {uid!r} has duplicate name {name!r} in its names list"
            )
        seen.add(name)

    host_at_name = d.get("host_at_name")
    if host_at_name is not None:
        if not (isinstance(host_at_name, str) and host_at_name):
            raise UserValidationError(
                f"user {uid!r} has invalid host_at_name {host_at_name!r}; "
                f"must be a non-empty 'host@name' string or absent"
            )
        try:
            # Pure local normaliser/validator — NO sac import, NO runtime
            # pull. Just proves the string is a well-formed host@name (or a
            # bare name, which canonical_agent_id accepts as host-unknown).
            canonical_agent_id(host_at_name)
        except AgentIdentityError as exc:
            raise UserValidationError(
                f"user {uid!r} has malformed host_at_name {host_at_name!r}: "
                f"{exc}"
            ) from exc

    notify = d.get("notify")
    if notify is not None and not isinstance(notify, dict):
        raise UserValidationError(
            f"user {uid!r} has non-mapping notify {notify!r}; "
            f"notify must be a mapping or absent"
        )

    turn_url = d.get("turn_url")
    if turn_url is not None and not (isinstance(turn_url, str) and turn_url):
        raise UserValidationError(
            f"user {uid!r} has invalid turn_url {turn_url!r}; "
            f"turn_url must be a non-empty string or absent"
        )

    a2a_port = d.get("a2a_port")
    if a2a_port is not None:
        # ``bool`` is an ``int`` subclass; a True/False port is a bug, not a
        # port number — reject it explicitly before the positive-int check.
        if isinstance(a2a_port, bool) or not isinstance(a2a_port, int) or a2a_port <= 0:
            raise UserValidationError(
                f"user {uid!r} has invalid a2a_port {a2a_port!r}; "
                f"a2a_port must be a positive int or absent"
            )


def user_turn_url(user: "User | dict") -> str | None:
    """Resolve a user's delivery endpoint (turn URL), or ``None``.

    Precedence (the SSOT for "where do I POST a board event for this
    member?"):

    1. An explicit :attr:`User.turn_url` — used verbatim.
    2. Else, when :attr:`User.a2a_port` is set, derive
       ``http://<host>:<a2a_port>/v1/turn`` where ``<host>`` is the host
       half of :attr:`User.host_at_name` (via :func:`parse_agent_id`); a
       bare / absent ``host_at_name`` (host unknown) yields the loopback
       host ``127.0.0.1`` — the same loopback convention
       :func:`scitex_todo._turn_url._turn_url_from_registry` uses when it
       derives from an ``a2a_port``.
    3. Else ``None`` — the member has no configured endpoint.

    Accepts either a :class:`User` or a plain ``users:`` dict (so callers
    can resolve straight off a row without rehydrating). Never raises.
    """
    d = user.to_dict() if isinstance(user, User) else user
    if not isinstance(d, dict):
        return None
    explicit = d.get("turn_url")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    port = d.get("a2a_port")
    # Mirror validate_user's guard: reject bool (int subclass) + non-positive.
    if isinstance(port, bool) or not isinstance(port, int) or port <= 0:
        return None
    host_at_name = d.get("host_at_name")
    host = "127.0.0.1"
    if isinstance(host_at_name, str) and host_at_name.strip():
        try:
            parsed_host, _name = parse_agent_id(host_at_name)
        except AgentIdentityError:
            parsed_host = ""
        if parsed_host:
            host = parsed_host
    return f"http://{host}:{port}/v1/turn"


__all__ = [
    "VALID_USER_KINDS",
    "User",
    "UserValidationError",
    "user_turn_url",
    "validate_user",
]

# EOF
