#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the listen-server bearer-token store (real files, no mocks)."""

from __future__ import annotations

import os
import stat

from scitex_todo._listen.tokens import (
    ENV_LISTEN_TOKEN,
    ensure_token,
    read_token,
)


def test_ensure_token_mints_persists_and_is_idempotent(tmp_path):
    path = tmp_path / "listen.token"
    tok1 = ensure_token(path)
    assert tok1 and len(tok1) >= 20
    assert path.exists()
    # A second call returns the SAME token (reused, not re-minted).
    tok2 = ensure_token(path)
    assert tok2 == tok1
    # And a bare read sees it too.
    assert read_token(path) == tok1


def test_token_file_is_0600(tmp_path):
    path = tmp_path / "listen.token"
    ensure_token(path)
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600


def test_env_override_wins_and_writes_no_file(tmp_path, monkeypatch):
    path = tmp_path / "listen.token"
    monkeypatch.setenv(ENV_LISTEN_TOKEN, "env-supplied-secret")
    tok = ensure_token(path)
    assert tok == "env-supplied-secret"
    # Env override must NOT create a file (operator owns that secret).
    assert not path.exists()
    assert read_token(path) == "env-supplied-secret"


def test_read_token_missing_file_is_none(tmp_path):
    assert read_token(tmp_path / "nope.token") is None


# EOF
