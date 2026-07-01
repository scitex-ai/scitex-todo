#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the canonical identity resolver (`scitex_todo._users._identity`).

Real fakes only — no mocks (Req STX-NM / PA-306). The pure
:func:`canonical_identity` is exercised with plain ``User`` snapshots; the
store-aware :func:`resolve_identity` round-trips against a ``tmp_path`` YAML
store; the ``SCITEX_TODO_STRICT_IDENTITY`` env gate is flipped via
``monkeypatch.setenv``.
"""

from __future__ import annotations

import pytest

from scitex_todo import _users
from scitex_todo._users import (
    ENV_STRICT_IDENTITY,
    IDENTITY_ALIASES,
    User,
    UnknownIdentityError,
    canonical_identity,
    resolve_identity,
    strict_identity_enabled,
)


# --------------------------------------------------------------------------- #
# (b) alias table — declared synonyms                                         #
# --------------------------------------------------------------------------- #
def test_alias_sac_to_scitex_agent_container():
    assert canonical_identity("sac") == "scitex-agent-container"


def test_alias_orochi_to_scitex_orochi():
    assert canonical_identity("orochi") == "scitex-orochi"


def test_alias_table_seeded_exactly():
    assert IDENTITY_ALIASES["scitex-agent-container"] == ("sac",)
    assert IDENTITY_ALIASES["scitex-orochi"] == ("orochi",)


# --------------------------------------------------------------------------- #
# (c) mechanical normalise — derivable drift                                  #
# --------------------------------------------------------------------------- #
def test_mechanical_strip_proj_prefix():
    assert canonical_identity("proj-scitex-dev") == "scitex-dev"


def test_mechanical_strip_proj_paper_prefix():
    # proj-paper- (longer) must win over proj-.
    assert canonical_identity("proj-paper-neurovista") == "neurovista"


def test_mechanical_strip_trailing_host():
    assert canonical_identity("lead-ywata-note-win") == "lead"


def test_mechanical_strip_prefix_and_host_together():
    assert (
        canonical_identity("proj-scitex-dev-ywata-note-win") == "scitex-dev"
    )


# --------------------------------------------------------------------------- #
# (a) registered-names hit — exact match beats everything                     #
# --------------------------------------------------------------------------- #
def test_registered_name_hit_returns_canonical_first_name():
    users = [
        User(
            id="u_000000000001",
            kind="agent",
            names=["scitex-dev", "proj-scitex-dev"],
        )
    ]
    # An OLD alias resolves to the CURRENT canonical (first) name.
    assert canonical_identity("proj-scitex-dev", users=users) == "scitex-dev"
    assert canonical_identity("scitex-dev", users=users) == "scitex-dev"


def test_registered_id_and_host_at_name_hit():
    users = [
        User(
            id="u_00000000abcd",
            kind="agent",
            names=["neurovista"],
            host_at_name="spartan@neurovista",
        )
    ]
    assert canonical_identity("u_00000000abcd", users=users) == "neurovista"
    assert (
        canonical_identity("spartan@neurovista", users=users) == "neurovista"
    )


def test_registered_hit_beats_alias_table():
    # If someone registers a user whose canonical name is 'sac' itself, the
    # registry wins over the alias table (precedence a before b).
    users = [User(id="u_00000000f00d", kind="agent", names=["sac"])]
    assert canonical_identity("sac", users=users) == "sac"


# --------------------------------------------------------------------------- #
# (d) unknown — strict raises with hint / non-strict returns input           #
# --------------------------------------------------------------------------- #
def test_unknown_strict_raises_with_hint():
    with pytest.raises(UnknownIdentityError) as excinfo:
        canonical_identity("totally-unknown-xyz", strict=True)
    msg = str(excinfo.value)
    assert "totally-unknown-xyz" in msg
    assert "register" in msg or "alias" in msg  # actionable hint


def test_unknown_non_strict_returns_input_unchanged():
    assert canonical_identity("totally-unknown-xyz", strict=False) == (
        "totally-unknown-xyz"
    )


def test_empty_name_always_raises():
    with pytest.raises(UnknownIdentityError):
        canonical_identity("   ", strict=False)


# --------------------------------------------------------------------------- #
# idempotency — canonical(canonical(x)) == canonical(x)                       #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw",
    [
        "sac",
        "orochi",
        "proj-scitex-dev",
        "proj-paper-neurovista",
        "lead-ywata-note-win",
        "already-canonical",
    ],
)
def test_idempotency(raw):
    once = canonical_identity(raw, strict=False)
    twice = canonical_identity(once, strict=False)
    assert once == twice


def test_idempotency_with_registry():
    users = [
        User(
            id="u_000000000001",
            kind="agent",
            names=["scitex-dev", "proj-scitex-dev"],
        )
    ]
    once = canonical_identity("proj-scitex-dev", users=users)
    twice = canonical_identity(once, users=users)
    assert once == twice == "scitex-dev"


# --------------------------------------------------------------------------- #
# env gate — strict defaults OFF                                              #
# --------------------------------------------------------------------------- #
def test_strict_defaults_off(monkeypatch):
    monkeypatch.delenv(ENV_STRICT_IDENTITY, raising=False)
    assert strict_identity_enabled() is False


@pytest.mark.parametrize("val", ["1", "true", "TRUE", "yes", "on"])
def test_strict_env_truthy(monkeypatch, val):
    monkeypatch.setenv(ENV_STRICT_IDENTITY, val)
    assert strict_identity_enabled() is True


@pytest.mark.parametrize("val", ["0", "false", "no", "off", "", "maybe"])
def test_strict_env_falsy(monkeypatch, val):
    monkeypatch.setenv(ENV_STRICT_IDENTITY, val)
    assert strict_identity_enabled() is False


# --------------------------------------------------------------------------- #
# resolve_identity — store-aware wrapper, env-gated strict                    #
# --------------------------------------------------------------------------- #
def test_resolve_identity_registered_hit(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_STRICT_IDENTITY, raising=False)
    store = tmp_path / "tasks.yaml"
    _users.register_user(
        kind="agent",
        names=["scitex-dev", "proj-scitex-dev"],
        store=store,
    )
    assert resolve_identity("proj-scitex-dev", store=store) == "scitex-dev"


def test_resolve_identity_non_strict_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_STRICT_IDENTITY, raising=False)
    store = tmp_path / "tasks.yaml"
    # Unknown + strict OFF (default) → input returned, no raise.
    assert resolve_identity("unregistered-xyz", store=store) == (
        "unregistered-xyz"
    )


def test_resolve_identity_strict_env_flip_raises(tmp_path, monkeypatch):
    monkeypatch.setenv(ENV_STRICT_IDENTITY, "1")
    store = tmp_path / "tasks.yaml"
    with pytest.raises(UnknownIdentityError):
        resolve_identity("unregistered-xyz", store=store)


def test_resolve_identity_alias_via_store(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_STRICT_IDENTITY, raising=False)
    store = tmp_path / "tasks.yaml"
    assert resolve_identity("sac", store=store) == "scitex-agent-container"


# --------------------------------------------------------------------------- #
# resolve_user canonicalised retry (wired seam)                               #
# --------------------------------------------------------------------------- #
def test_resolve_user_canonicalises_drifted_name(tmp_path):
    store = tmp_path / "tasks.yaml"
    created = _users.register_user(
        kind="agent", names=["scitex-dev"], store=store
    )
    # 'proj-scitex-dev' is NOT in names[], but canonicalises to 'scitex-dev'.
    resolved = _users.resolve_user("proj-scitex-dev", store=store)
    assert resolved is not None
    assert resolved.id == created.id


def test_resolve_user_unknown_still_none(tmp_path):
    store = tmp_path / "tasks.yaml"
    _users.register_user(kind="agent", names=["scitex-dev"], store=store)
    assert _users.resolve_user("nobody-here", store=store) is None
