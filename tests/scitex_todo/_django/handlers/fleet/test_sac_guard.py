#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the fleet skip guard itself (``_sac_guard.sac_functional``).

The guard decouples scitex-todo's CI from the OPTIONAL ``sac`` binary: a
broken/absent ``sac`` must SKIP the fleet happy-path tests, not FAIL the
standalone package. This module covers the DECISION logic of the guard so
the decoupling is itself protected.

No mocks (STX-NM/PA-306). The skip branch is exercised deterministically
with a REAL bogus binary name and a REAL always-failing command (``false``,
located via PATH) — the guard's ``binary`` argument is the seam.
"""

from __future__ import annotations

import shutil

from ._sac_guard import sac_functional


# ─── skip branch: absent binary ─────────────────────────────────────────


def test_sac_functional_returns_false_for_bogus_binary() -> None:
    """A binary that is not on PATH must NOT be treated as functional —
    the guard skips (returns False) rather than letting a happy-path test
    run against a missing dependency."""
    # Arrange — a name that cannot resolve via ``shutil.which``.
    bogus = "sac-definitely-not-a-real-binary-xyz123"
    # Act
    result = sac_functional(binary=bogus)
    # Assert
    assert result is False


# ─── skip branch: present-but-non-functional binary ─────────────────────


def test_sac_functional_returns_false_for_nonzero_exit_binary() -> None:
    """A binary that IS on PATH but exits non-zero for the probe command
    (the present-but-broken CI case) must SKIP — not run-and-fail.

    ``false`` is a real, always-installed POSIX command that ignores its
    args and exits 1, standing in for a broken ``sac`` deterministically
    without a mock."""
    # Arrange — guard only runs if ``false`` is actually available here.
    if shutil.which("false") is None:  # pragma: no cover - POSIX always has it
        import pytest

        pytest.skip("`false` not on PATH in this environment")
    # Act
    result = sac_functional(binary="false")
    # Assert
    assert result is False


# ─── run branch: functional binary ──────────────────────────────────────


def test_sac_functional_returns_true_for_zero_exit_binary() -> None:
    """A binary that IS on PATH and exits 0 for the probe command must be
    treated as functional (return True) so the happy-path tests RUN.

    ``true`` is a real, always-installed POSIX command that ignores its
    args and exits 0, standing in for a working ``sac`` deterministically
    without a mock — proving the non-skip branch is reachable."""
    # Arrange
    if shutil.which("true") is None:  # pragma: no cover - POSIX always has it
        import pytest

        pytest.skip("`true` not on PATH in this environment")
    # Act
    result = sac_functional(binary="true")
    # Assert
    assert result is True


# EOF
