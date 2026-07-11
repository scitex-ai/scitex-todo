#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Layered config.yaml — user base + project override, knob resolution.

Real files in ``tmp_path``, no mocks: we point ``config_paths`` at concrete
temp files and assert the merge + the interval-resolution precedence.
"""

from __future__ import annotations

import pytest

from scitex_todo import _config


def _paths(monkeypatch, *paths):
    monkeypatch.setattr(_config, "config_paths", lambda: list(paths))


# === layering: project overrides user, key-by-key ==========================


def test_absent_files_yield_empty_config(tmp_path, monkeypatch):
    _paths(monkeypatch, tmp_path / "missing.yaml")
    assert _config.load_config() == {}
    assert _config.reminders_config() == {}


def test_project_overrides_user_key_by_key(tmp_path, monkeypatch):
    user = tmp_path / "user.yaml"
    project = tmp_path / "project.yaml"
    user.write_text(
        "reminders:\n  interval_minutes: 5\n  escalate_after: 3\n",
        encoding="utf-8",
    )
    # Project overrides ONLY interval_minutes; escalate_after is inherited.
    project.write_text("reminders:\n  interval_minutes: 1\n", encoding="utf-8")
    _paths(monkeypatch, user, project)

    cfg = _config.reminders_config()
    assert cfg["interval_minutes"] == 1   # project wins
    assert cfg["escalate_after"] == 3     # inherited from user


def test_malformed_file_is_ignored(tmp_path, monkeypatch):
    bad = tmp_path / "bad.yaml"
    bad.write_text("reminders: [this is not a mapping\n", encoding="utf-8")
    _paths(monkeypatch, bad)
    assert _config.reminders_config() == {}


# === interval resolution: card > config > default =========================


def test_default_interval_when_nothing_set(tmp_path, monkeypatch):
    _paths(monkeypatch)
    assert _config.resolve_interval_minutes(None) == _config.DEFAULT_INTERVAL_MINUTES


def test_config_interval_used_when_no_card_override(tmp_path, monkeypatch):
    cfg = {"interval_minutes": 2}
    assert _config.resolve_interval_minutes({"id": "c1"}, cfg) == 2.0


def test_card_override_beats_config(tmp_path, monkeypatch):
    cfg = {"interval_minutes": 5}
    card = {"id": "c1", "reminder_interval_minutes": 1}
    assert _config.resolve_interval_minutes(card, cfg) == 1.0


def test_non_positive_values_fall_through(tmp_path, monkeypatch):
    cfg = {"interval_minutes": 0}                 # invalid → ignored
    card = {"id": "c1", "reminder_interval_minutes": -3}  # invalid → ignored
    assert _config.resolve_interval_minutes(card, cfg) == _config.DEFAULT_INTERVAL_MINUTES


def test_bool_is_not_a_number(tmp_path, monkeypatch):
    # bool is an int subclass; it must NOT be accepted as an interval.
    cfg = {"interval_minutes": True}
    assert _config.resolve_interval_minutes(None, cfg) == _config.DEFAULT_INTERVAL_MINUTES


# EOF
