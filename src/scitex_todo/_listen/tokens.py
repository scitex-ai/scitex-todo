#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bearer-token generation / storage for the listen server.

The listen server is loopback-bound but still bearer-gated (defense in depth
+ so a future non-loopback bind is safe by construction). The token is a
single host-wide secret stored on disk with ``0600`` perms, mirroring sac's
``~/.scitex/agent-container/tokens/listen-<host>.token`` shape — here it lives
under scitex-todo's own runtime dir: ``<store runtime>/tokens/listen-<host>.token``.

* :func:`default_token_path` — where the token file lives for a given store.
* :func:`ensure_token` — read the existing token, or mint + persist a fresh
  one (``0600``) if absent. Idempotent: a second call returns the same value.
* :func:`read_token` — read an existing token (``None`` if absent/unreadable).

An explicit ``SCITEX_TODO_LISTEN_TOKEN`` env var, when set, WINS over the file
(so an operator / systemd unit can inject the secret without a file) — same
"env overrides file" posture the rest of the package uses.
"""

from __future__ import annotations

import logging
import os
import secrets
import socket
from pathlib import Path

logger = logging.getLogger(__name__)

#: Env var that, when set non-empty, overrides the on-disk token file.
ENV_LISTEN_TOKEN = "SCITEX_TODO_LISTEN_TOKEN"

#: Subdirectory (under the store runtime dir) holding token files.
_TOKENS_SUBDIR = "tokens"

#: Bytes of entropy in a freshly-minted token (URL-safe base64 → ~43 chars).
_TOKEN_ENTROPY_BYTES = 32


def _hostname() -> str:
    """Short hostname used to name the token file (best-effort)."""
    try:
        return socket.gethostname().split(".")[0] or "localhost"
    except OSError:  # noqa: BLE001 — a hostname lookup failure must not break start
        return "localhost"


def default_token_path(store: "str | Path | None" = None) -> Path:
    """Resolve ``<store runtime>/tokens/listen-<host>.token`` for ``store``.

    Uses the same ``runtime/`` convention (non-git-tracked runtime state) the
    notifyd pidfile + delivery ledger already use, so all listen-server state
    lives beside them under whichever scope the store resolved to.
    """
    from .._paths import runtime_dir

    return runtime_dir(store) / _TOKENS_SUBDIR / f"listen-{_hostname()}.token"


def read_token(path: "str | Path | None" = None, *, store: "str | Path | None" = None) -> "str | None":
    """Return the current token: env override → file at ``path`` → ``None``.

    ``path`` defaults to :func:`default_token_path`. A missing / unreadable
    file yields ``None`` (fail-soft on the read side; :func:`ensure_token` is
    the write path that guarantees a value).
    """
    env = os.environ.get(ENV_LISTEN_TOKEN)
    if env:
        return env
    p = Path(path).expanduser() if path is not None else default_token_path(store)
    try:
        tok = p.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return tok or None


def ensure_token(
    path: "str | Path | None" = None, *, store: "str | Path | None" = None
) -> str:
    """Return the token, minting + persisting a fresh one (``0600``) if absent.

    Precedence: the ``SCITEX_TODO_LISTEN_TOKEN`` env override wins and is
    returned WITHOUT writing a file (the operator owns that secret). Otherwise
    an existing file is reused; a missing file is created with a fresh
    high-entropy token at ``0600``. Idempotent — concurrent-ish callers may
    race to create the file, so a create failure falls back to re-reading.
    """
    env = os.environ.get(ENV_LISTEN_TOKEN)
    if env:
        return env
    p = Path(path).expanduser() if path is not None else default_token_path(store)
    existing = read_token(p)
    if existing:
        return existing
    token = secrets.token_urlsafe(_TOKEN_ENTROPY_BYTES)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        # Create restricted from the start: write via a 0600 fd so the secret
        # is never briefly world-readable between create and chmod.
        fd = os.open(str(p), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(token + "\n")
    except OSError as exc:  # noqa: BLE001 — a racing writer may have won; re-read
        again = read_token(p)
        if again:
            return again
        raise RuntimeError(
            f"cannot create listen token at {p}: {exc}"
        ) from exc
    return token


__all__ = [
    "ENV_LISTEN_TOKEN",
    "default_token_path",
    "ensure_token",
    "read_token",
]

# EOF
