#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the GitHub CI status adapter (no mocks — STX-NM/PA-306).

Covers BOTH the config loader and the gh-CLI adapter:

- ``FleetAdapterError`` is the documented exception class for the whole
  fleet adapter family (the harness contract callers and tests pin on).
- Config loader: missing file = empty repos list (NOT raise — "no repos
  configured" is a valid steady state for a fresh install). Malformed
  YAML RAISES. Env override
  ``SCITEX_TODO_FLEET_CI_REPOS=a,b`` replaces the file-sourced list.
- gh adapter: calling ``fetch_repo_ci_status`` on a non-existent slug
  RAISES (skipped cleanly if the test runner has no ``gh`` auth — we
  refuse to hardcode test-only credentials).

No mocks AND no ``monkeypatch`` — PA-306 forbids the latter (audit
treats pytest's monkeypatch as a mock). Env / cwd manipulation goes
through the suite's :func:`env` fixture
(see ``tests/scitex_todo/conftest.py``).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from scitex_todo._django.handlers.fleet import (
    FleetAdapterError,
    fetch_repo_ci_status,
    fleet_config_load,
)
from scitex_todo._django.handlers.fleet import _config as fleet_config_mod


# ─── FleetAdapterError shape ────────────────────────────────────────────


def test_fleet_adapter_error_is_runtime_error_subclass() -> None:
    """The adapter family's failure class is a ``RuntimeError`` subclass
    so callers that ``except RuntimeError`` (e.g. broad Django error
    middleware) keep working — but a dedicated subclass lets the view
    + tests pin behavior with ``pytest.raises(FleetAdapterError)``."""
    assert issubclass(FleetAdapterError, RuntimeError)
    assert FleetAdapterError is not RuntimeError


# ─── Config loader ──────────────────────────────────────────────────────


def _isolate_home(env, tmp_path):
    """Point ``Path.home()`` at ``tmp_path`` and clear the env override.

    Each test owns its own HOME so we never read the operator's real
    ``~/.scitex/todo/dashboard.yaml``. Uses the PA-306-compliant ``env``
    helper from the shared conftest (NOT monkeypatch).
    """
    env.set("HOME", str(tmp_path))
    env.delete("SCITEX_TODO_FLEET_CI_REPOS")


def test_config_missing_file_returns_empty_repos(env, tmp_path) -> None:
    """A fresh install has no ``dashboard.yaml`` and that is NOT an
    error — "no repos configured" is a valid steady state and the UI
    hides the pills strip gracefully."""
    _isolate_home(env, tmp_path)
    out = fleet_config_load()
    assert out == {"fleet": {"ci_status": {"repos": []}}}


def test_config_malformed_yaml_raises(env, tmp_path) -> None:
    """A broken config IS fail-loud so the operator does not stare at
    an empty strip wondering why their config is being ignored."""
    _isolate_home(env, tmp_path)
    cfg_dir = tmp_path / ".scitex" / "todo"
    cfg_dir.mkdir(parents=True)
    # Unterminated mapping — YAML parser bails.
    (cfg_dir / "dashboard.yaml").write_text(
        "fleet:\n  ci_status:\n    repos: [a, b,\n",
        encoding="utf-8",
    )
    with pytest.raises(FleetAdapterError) as excinfo:
        fleet_config_load()
    # The message must name the file path so the operator can find it.
    assert "dashboard.yaml" in str(excinfo.value)


def test_config_env_override_replaces_file_list(env, tmp_path) -> None:
    """The env var trumps the file — handy for tests AND for the
    operator to flip the set without editing the file."""
    _isolate_home(env, tmp_path)
    cfg_dir = tmp_path / ".scitex" / "todo"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "dashboard.yaml").write_text(
        "fleet:\n  ci_status:\n    repos:\n      - file/one\n      - file/two\n",
        encoding="utf-8",
    )
    env.set("SCITEX_TODO_FLEET_CI_REPOS", "env/aaa, env/bbb")
    out = fleet_config_load()
    assert out["fleet"]["ci_status"]["repos"] == ["env/aaa", "env/bbb"]


def test_config_env_override_without_file(env, tmp_path) -> None:
    """The env override alone is enough — no file required."""
    _isolate_home(env, tmp_path)
    env.set("SCITEX_TODO_FLEET_CI_REPOS", "owner/x,owner/y")
    out = fleet_config_load()
    assert out["fleet"]["ci_status"]["repos"] == ["owner/x", "owner/y"]


def test_config_env_override_empty_string_means_no_repos(
    env, tmp_path
) -> None:
    """Empty env override yields an empty list (not a one-item ``""``)
    — the operator can intentionally disable the strip with an empty
    env value."""
    _isolate_home(env, tmp_path)
    env.set("SCITEX_TODO_FLEET_CI_REPOS", "")
    out = fleet_config_load()
    assert out["fleet"]["ci_status"]["repos"] == []


def test_config_reads_file_when_env_unset(env, tmp_path) -> None:
    """File-sourced list shines through when the env override is unset."""
    _isolate_home(env, tmp_path)
    cfg_dir = tmp_path / ".scitex" / "todo"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "dashboard.yaml").write_text(
        "fleet:\n  ci_status:\n    repos:\n      - file/one\n      - file/two\n",
        encoding="utf-8",
    )
    out = fleet_config_load()
    assert out["fleet"]["ci_status"]["repos"] == ["file/one", "file/two"]


# ─── gh adapter ─────────────────────────────────────────────────────────


def _gh_authed() -> bool:
    """True iff ``gh`` is on PATH AND ``gh auth status`` succeeds.

    The adapter test refuses to hardcode test-only credentials, so it
    self-skips on a CI machine without auth.
    """
    exe = shutil.which("gh")
    if exe is None:
        return False
    try:
        proc = subprocess.run(
            [exe, "auth", "status"],
            check=False,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


_AUTHED = _gh_authed()


@pytest.mark.skipif(
    not _AUTHED, reason="gh CLI not installed or not authenticated"
)
def test_nonexistent_repo_raises() -> None:
    """Calling the adapter on a slug that does not exist on GitHub
    must surface as ``FleetAdapterError`` — the fail-loud contract."""
    with pytest.raises(FleetAdapterError):
        # A slug that almost-certainly resolves to 404. We avoid using a
        # name that COULD be claimed later by namespacing it under a
        # uuid-like path.
        fetch_repo_ci_status(
            "ywatanabe1989/scitex-todo-test-does-not-exist-xyz123"
        )


def test_invalid_slug_shape_raises_without_gh() -> None:
    """Slug validation happens before we touch ``gh``, so this test
    is gh-independent — it pins the input contract on every CI box."""
    with pytest.raises(FleetAdapterError) as excinfo:
        fetch_repo_ci_status("not-a-slug")
    assert "owner/name" in str(excinfo.value)


def test_gh_missing_binary_raises(env) -> None:
    """Surfacing "gh not installed" must NOT silently fall back to an
    empty-checks success — that would lie to the operator about fleet
    health. Simulate the missing binary by clobbering PATH."""
    env.set("PATH", "")
    from scitex_todo._django.handlers.fleet import gh_ci as gh_ci_mod

    with pytest.raises(FleetAdapterError) as excinfo:
        gh_ci_mod.fetch_repo_ci_status("owner/name")
    assert "gh" in str(excinfo.value).lower()


# ─── overall-reducer pure unit tests ────────────────────────────────────


@pytest.mark.parametrize(
    "checks,expected",
    [
        ([], "unknown"),
        (
            [
                {"status": "completed", "conclusion": "success"},
                {"status": "completed", "conclusion": "success"},
            ],
            "success",
        ),
        (
            [
                {"status": "completed", "conclusion": "success"},
                {"status": "completed", "conclusion": "failure"},
            ],
            "failure",
        ),
        (
            [
                {"status": "in_progress", "conclusion": None},
                {"status": "completed", "conclusion": "success"},
            ],
            "pending",
        ),
        (
            [{"status": "completed", "conclusion": "timed_out"}],
            "failure",
        ),
        (
            [
                {"status": "completed", "conclusion": "neutral"},
                {"status": "completed", "conclusion": "skipped"},
            ],
            "success",
        ),
    ],
)
def test_overall_reducer(checks, expected) -> None:
    """The reducer is the heart of the pill color — failure beats
    pending beats success. Pin every branch explicitly so the FE color
    mapping stays in lock-step with what the back-end emits."""
    from scitex_todo._django.handlers.fleet.gh_ci import _overall_from_checks

    assert _overall_from_checks(checks) == expected


# ─── module-surface contract ────────────────────────────────────────────


def test_config_module_constant_paths() -> None:
    """Lock the config-path constant so a rename downstream forces a
    test update. Operators search for this literal when debugging."""
    assert str(fleet_config_mod._CONFIG_REL) == str(
        Path(".scitex") / "todo" / "dashboard.yaml"
    )
    assert fleet_config_mod._ENV_REPOS == "SCITEX_TODO_FLEET_CI_REPOS"


def test_module_has_documented_attrs() -> None:
    """The harness ``__init__`` exports its public API verbatim — pin
    the surface so downstream waves (hosts / mesh / …) can ``from
    scitex_todo._django.handlers.fleet import FleetAdapterError`` and
    expect that name to be stable."""
    from scitex_todo._django.handlers import fleet as fleet_pkg

    for attr in (
        "FleetAdapterError",
        "fleet_config_load",
        "fetch_repo_ci_status",
        "fleet_ci_status_view",
    ):
        assert hasattr(fleet_pkg, attr), f"missing public name: {attr}"
