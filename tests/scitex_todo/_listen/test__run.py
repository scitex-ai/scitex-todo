#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the listen-server runner helpers (guards, ports, status)."""

from __future__ import annotations

import pytest

from scitex_todo._listen._run import (
    DEFAULT_PORT,
    ENV_LISTEN_PORT,
    default_port,
    listen_pidfile_path,
    run_server,
    server_status,
)


def test_default_port_env_override(monkeypatch):
    assert default_port() == DEFAULT_PORT
    monkeypatch.setenv(ENV_LISTEN_PORT, "9123")
    assert default_port() == 9123
    monkeypatch.setenv(ENV_LISTEN_PORT, "not-an-int")
    assert default_port() == DEFAULT_PORT  # bad value falls back


def test_run_server_refuses_non_loopback_without_optin(tmp_path):
    store = tmp_path / "tasks.yaml"
    with pytest.raises(RuntimeError, match="non-loopback"):
        run_server(host="0.0.0.0", store=store, token="x")


def test_listen_pidfile_is_under_runtime_dir(tmp_path):
    store = tmp_path / "tasks.yaml"
    p = listen_pidfile_path(store)
    assert p.name == "listen.pid"
    assert "runtime" in p.parts


def test_status_of_not_running_server(tmp_path):
    store = tmp_path / "tasks.yaml"
    # Use a very unlikely port so nothing is actually bound.
    st = server_status(port=59999, store=store)
    assert st["port"] == 59999
    assert st["port_bound"] is False
    assert st["health_ok"] is None  # not probed when nothing is bound
    assert st["pid"] is None
    assert st["pid_alive"] is False


# EOF
