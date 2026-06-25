#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared skip guard for fleet tests that need a FUNCTIONAL ``sac`` binary.

``sac`` (scitex-agent-container) is an OPTIONAL fleet dependency of the
STANDALONE ``scitex-todo`` package. A broken or absent ``sac`` must never
red-gate the standalone package, so the happy-path fleet tests that assert
a SUCCESS shape parsed from a live ``sac ... --json`` call must SKIP — not
FAIL — when ``sac`` is unavailable or non-functional.

``shutil.which("sac")`` only proves the binary is on PATH. On a CI runner
``sac`` can be installed yet broken (e.g. a libpython load failure, or
``from scitex_agent_container.cli import cli_entry_point`` blows up), so a
presence check would let these tests RUN and then FAIL. This guard probes
the REAL command and skips unless it exits 0 within a short timeout.

This is a pytest SKIP based on real environment probing — NOT a mock
(the repo forbids mocks, STX-NM/PA-306). The fail-loud-path tests
(assert 500 / FleetAdapterError when sac errors or is missing) need NO
working sac and must keep running, so they do NOT use this guard.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

# Probe timeout. ``sac host list --json`` walks a handful of small YAML
# files + the local interface list, so a few seconds is generous; the
# bound also protects CI from a wedged sac process.
_PROBE_TIMEOUT = 15

# The cheap, side-effect-free invocation that ``fetch_hosts`` itself
# shells. If THIS succeeds, the happy-path hosts tests can run for real.
_PROBE_ARGS = ["host", "list", "--json"]


def sac_functional(binary: str = "sac") -> bool:
    """Return ``True`` iff ``<binary> host list --json`` actually SUCCEEDS.

    Resolution + execution mirror the production adapter
    (``sac_hosts._sac_json``): locate the binary via ``shutil.which`` and
    run the real probe command. Returns ``False`` — i.e. the test should
    SKIP — when the binary is absent (``which`` -> ``None``), fails to
    start (``OSError``), times out, or exits non-zero.

    ``binary`` is the resolution seam: pass a bogus name (absent on PATH)
    or a real always-failing command path to exercise the skip branch
    deterministically without mocks.
    """
    exe = shutil.which(binary)
    if exe is None:
        return False
    try:
        proc = subprocess.run(
            [exe, *_PROBE_ARGS],
            check=False,
            capture_output=True,
            timeout=_PROBE_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


# Probe ONCE at import time so the marker is cheap to apply per-test.
_SAC_FUNCTIONAL = sac_functional()

# Reason surfaced for skipped tests — names the optional dependency and
# the standalone-package principle so the skip is self-explanatory in the
# pytest summary.
SKIP_REASON = (
    "requires a functional `sac` binary (optional fleet dependency); "
    "skipping in this environment — `sac host list --json` is absent or "
    "non-functional"
)

# Apply as ``@requires_functional_sac`` on happy-path tests.
requires_functional_sac = pytest.mark.skipif(
    not _SAC_FUNCTIONAL,
    reason=SKIP_REASON,
)

__all__ = ["sac_functional", "requires_functional_sac", "SKIP_REASON"]

# EOF
