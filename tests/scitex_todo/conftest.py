#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared fixtures for ``tests/scitex_todo/`` and its subdirectories.

PA-306 forbids ``monkeypatch`` because pytest's monkeypatch fixture is treated
as a mock by the audit. This module ships an ``env`` fixture that does the
same job — set / clear environment variables with proper test-scoped cleanup —
without using monkeypatch under the hood.

The fixture is intentionally minimal: just ``set(key, value)`` and
``delete(key)``. Tests that previously did
``monkeypatch.setenv("SCITEX_TODO_AGENT", "agent:test")`` now do
``env.set("SCITEX_TODO_AGENT", "agent:test")``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import pytest


@dataclass
class _EnvHelper:
    """Captures the original env-var state + cwd and restores them on teardown.

    Implements just the slice of monkeypatch's API the scitex-todo test
    suite actually uses: ``set`` / ``delete`` (env vars) and ``chdir``
    (process working directory). New keys are removed on teardown;
    previously-set keys are restored to their original value; cwd is
    restored to the directory active at fixture entry.
    """

    _saved: dict[str, str | None] = field(default_factory=dict)
    _cwd_saved: str | None = None

    def _remember(self, key: str) -> None:
        if key not in self._saved:
            self._saved[key] = os.environ.get(key)

    def set(self, key: str, value: str) -> None:
        """Set ``os.environ[key] = value`` for the duration of the test."""
        self._remember(key)
        os.environ[key] = value

    def delete(self, key: str) -> None:
        """Remove ``os.environ[key]`` if present (no-op when absent)."""
        self._remember(key)
        os.environ.pop(key, None)

    def chdir(self, path) -> None:
        """Switch process cwd to ``path``; restored on fixture teardown.

        Captures the cwd lazily on first call so tests that don't change
        cwd pay no setup cost.
        """
        if self._cwd_saved is None:
            self._cwd_saved = os.getcwd()
        os.chdir(str(path))

    def restore(self) -> None:
        """Restore every touched env var + cwd to its pre-fixture value."""
        for key, prior in self._saved.items():
            if prior is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prior
        self._saved.clear()
        if self._cwd_saved is not None:
            try:
                os.chdir(self._cwd_saved)
            finally:
                self._cwd_saved = None


@pytest.fixture
def env():
    """Test-scoped env-var helper (PA-306-compliant monkeypatch replacement).

    Yields an :class:`_EnvHelper` whose ``set`` / ``delete`` methods stay
    valid for the duration of the test; on teardown every touched key is
    restored to its pre-test value.
    """
    helper = _EnvHelper()
    try:
        yield helper
    finally:
        helper.restore()

# EOF
