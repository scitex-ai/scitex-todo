#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pin the shape of scitex-todo's ``scitex_dev.jobs`` leaf entry point.

The provider is a one-line contract — but it is the single declaration
``scitex-dev ecosystem up`` reads to know that scitex-todo's board
needs to be a long-running ``--user`` systemd unit on TCP 8051. A
typo in the schedule / kind / command / port silently breaks the
operator's primary daily UI surface, so we pin every field.

Real fakes only (PA-306 / STX-NM*) — no ``unittest.mock``,
no ``monkeypatch``.
"""

from __future__ import annotations

from pathlib import Path

import scitex_todo
from scitex_todo._jobs_provider import provide_jobs, _repo_root


def test_provide_jobs_returns_at_least_one_jobspec():
    # Arrange
    # Act
    jobs = provide_jobs()
    # Assert
    assert len(jobs) >= 1


def test_provide_jobs_includes_the_board_dashboard():
    # Arrange
    # Act
    names = {j.name for j in provide_jobs()}
    # Assert
    assert "scitex-todo.dashboard" in names


def _board() -> object:
    return next(j for j in provide_jobs() if j.name == "scitex-todo.dashboard")


def test_board_kind_is_service():
    # Arrange
    # Act
    job = _board()
    # Assert — service kind = long-running unit, NOT a periodic timer.
    assert job.kind == "service"


def test_board_command_invokes_scitex_todo_board():
    # Arrange
    # Act
    job = _board()
    # Assert
    assert "scitex-todo board" in job.command


def test_board_command_pins_port_8051():
    # Arrange
    # Act
    job = _board()
    # Assert — the operator's primary UI is documented at
    # http://127.0.0.1:8051/; the canonical entry point in
    # _cli/_main.py also defaults to 8051.
    assert "--port 8051" in job.command


def test_board_schedule_is_empty():
    # Arrange
    # Act
    job = _board()
    # Assert — services aren't scheduled; JobSpec.validate() requires
    # schedule="" for kind="service".
    assert job.schedule == ""


def test_board_restart_policy_is_on_failure():
    # Arrange
    # Act
    job = _board()
    # Assert — board is the operator's daily UI; lost availability is
    # paged via the restart cycle (and the master reconcile unit on
    # boot).
    assert job.restart_policy == "on-failure"


def test_board_on_boot_sec_is_set():
    # Arrange
    # Act
    job = _board()
    # Assert — a non-None OnBootSec lets systemd defer startup until
    # network-online.target settles.
    assert job.on_boot_sec is not None


def test_board_description_mentions_the_canonical_url():
    # Arrange
    # Act
    job = _board()
    # Assert — `scitex-dev ecosystem cron list` / `systemd list` show
    # the description; mentioning 127.0.0.1:8051 makes the unit's
    # purpose obvious from a one-line summary.
    assert "8051" in job.description


# --------------------------------------------------------------------- #
# dev-sync timer — ff-pull origin/develop so the board never serves      #
# stale code. Registered ONLY on an editable git checkout.               #
# --------------------------------------------------------------------- #
#
# Real fakes only (PA-306): we drive ``_repo_root`` detection with a REAL
# temp directory tree (a real ``.git`` dir, a real package file) by
# pointing ``scitex_todo.__file__`` at it for the duration of the test and
# restoring it after — a genuine attribute swap, not ``unittest.mock``.


def _build_pkg_tree(root: Path, *, with_git: bool) -> Path:
    """Materialise a real on-disk package tree under ``root``.

    Returns the path to the fake ``scitex_todo/__init__.py`` that we point
    ``scitex_todo.__file__`` at. When ``with_git`` is True a real ``.git``
    directory sits at ``root`` (the checkout root); otherwise the tree
    mimics a ``site-packages`` install with no VCS metadata anywhere above.
    """
    if with_git:
        (root / ".git").mkdir()
    pkg = root / "src" / "scitex_todo"
    pkg.mkdir(parents=True)
    init = pkg / "__init__.py"
    init.write_text("# fake package marker\n")
    return init


def _provide_jobs_with_pkg_at(init_file: Path) -> list:
    """Run ``provide_jobs`` with ``scitex_todo.__file__`` swapped to ``init_file``."""
    original = scitex_todo.__file__
    try:
        scitex_todo.__file__ = str(init_file)
        return provide_jobs()
    finally:
        scitex_todo.__file__ = original


def test_dashboard_always_present_even_without_git(tmp_path):
    # Arrange — a tree with NO .git (mimics a PyPI install).
    init = _build_pkg_tree(tmp_path, with_git=False)
    # Act
    names = {j.name for j in _provide_jobs_with_pkg_at(init)}
    # Assert — the dashboard is unconditional.
    assert "scitex-todo.dashboard" in names


def test_repo_root_none_when_no_git(tmp_path):
    # Arrange
    init = _build_pkg_tree(tmp_path, with_git=False)
    original = scitex_todo.__file__
    try:
        scitex_todo.__file__ = str(init)
        # Act / Assert — no .git anywhere above => None (released install).
        assert _repo_root() is None
    finally:
        scitex_todo.__file__ = original


def test_repo_root_found_when_git_present(tmp_path):
    # Arrange
    init = _build_pkg_tree(tmp_path, with_git=True)
    original = scitex_todo.__file__
    try:
        scitex_todo.__file__ = str(init)
        # Act / Assert — walks up to the dir containing .git.
        assert _repo_root() == tmp_path.resolve()
    finally:
        scitex_todo.__file__ = original


def test_dev_sync_absent_without_git_checkout(tmp_path):
    # Arrange — non-editable install: no .git => no git-pull timer.
    init = _build_pkg_tree(tmp_path, with_git=False)
    # Act
    names = {j.name for j in _provide_jobs_with_pkg_at(init)}
    # Assert — a released install must NEVER try to git-pull.
    assert "scitex-todo.dev-sync" not in names


def test_dev_sync_present_with_git_checkout(tmp_path):
    # Arrange — editable checkout: real .git at the root.
    init = _build_pkg_tree(tmp_path, with_git=True)
    # Act
    names = {j.name for j in _provide_jobs_with_pkg_at(init)}
    # Assert
    assert "scitex-todo.dev-sync" in names


def _dev_sync_at(tmp_path: Path):
    init = _build_pkg_tree(tmp_path, with_git=True)
    jobs = _provide_jobs_with_pkg_at(init)
    return next(j for j in jobs if j.name == "scitex-todo.dev-sync")


def test_dev_sync_kind_is_timer(tmp_path):
    # Arrange / Act
    job = _dev_sync_at(tmp_path)
    # Assert — periodic timer, not a long-running service or crontab line.
    assert job.kind == "timer"


def test_dev_sync_has_periodic_cadence(tmp_path):
    # Arrange / Act
    job = _dev_sync_at(tmp_path)
    # Assert — on_unit_active_sec carries the timer cadence (every 2 min).
    assert job.on_unit_active_sec == "2min"


def test_dev_sync_command_is_ff_only_pull_of_develop(tmp_path):
    # Arrange / Act
    job = _dev_sync_at(tmp_path)
    # Assert — ff-only can never clobber local work; pulls develop into
    # the detected checkout root.
    assert "pull --ff-only origin develop" in job.command
    assert str(tmp_path.resolve()) in job.command


def test_dev_sync_restart_policy_is_no(tmp_path):
    # Arrange / Act
    job = _dev_sync_at(tmp_path)
    # Assert — JobSpec.validate() requires restart_policy="no" for timers.
    assert job.restart_policy == "no"


# EOF
