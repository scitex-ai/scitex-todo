#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""End-to-end tests for ``scitex_todo._push``.

Real HTTP round-trips against a localhost http.server instance — no
mocks (STX-NM / PA-306). AAA + one-assertion-per-test per the
scitex-dev test-quality corpus (STX-TQ002 / STX-TQ007).

Covers:
  * Env resolution (`SCITEX_TODO_AGENT_TURN_URLS` JSON map +
    per-agent `SCITEX_TODO_TURN_URL_<SLUG>` fallback)
  * No-URL → ok=False with reason="no-turn-url-configured"
  * Successful POST → ok=True with the real status code
  * HTTP 4xx/5xx → ok=False with reason="http-error"
  * Transport error (port that no server listens on) →
    ok=False with reason="transport-error"
  * SCITEX_TODO_PUSH_DRY_RUN=1 → ok=True, wire="dry-run"
  * announce_missing_at_boot returns the diff list
"""

from __future__ import annotations

import http.server
import json
import os
import socket
import threading
from contextlib import contextmanager

import pytest

from scitex_todo._push import (
    ENV_DRY_RUN,
    ENV_MAP,
    PER_AGENT_PREFIX,
    announce_missing_at_boot,
    deliver,
    turn_url_for,
)


# --------------------------------------------------------------------------- #
# Helpers — a minimal stdlib HTTP server we can poke from the same process    #
# --------------------------------------------------------------------------- #


class _Capture:
    """Holds the last received POST so tests can assert on it."""

    last_path: str = ""
    last_body: bytes = b""
    response_code: int = 200


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@contextmanager
def _server(capture: _Capture):
    """Spawn a localhost test server returning `capture.response_code`."""

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            capture.last_path = self.path
            capture.last_body = self.rfile.read(length)
            self.send_response(capture.response_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')

        def log_message(self, *a, **kw):  # silence test noise
            return

    port = _free_port()
    httpd = http.server.HTTPServer(("127.0.0.1", port), Handler)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    try:
        yield f"http://127.0.0.1:{port}/turn"
    finally:
        httpd.shutdown()


# --------------------------------------------------------------------------- #
# turn_url_for                                                                #
# --------------------------------------------------------------------------- #


class TestTurnUrlFor:
    """Env resolution: canonical JSON map then per-agent fallback."""

    def test_json_map_returns_canonical_url(self, monkeypatch):
        # Arrange
        monkeypatch.setenv(
            ENV_MAP,
            json.dumps({"alpha": "https://canonical/turn/alpha"}),
        )
        # Act
        url = turn_url_for("alpha")
        # Assert
        assert url == "https://canonical/turn/alpha"

    def test_per_agent_env_fallback(self, monkeypatch):
        # Arrange
        monkeypatch.delenv(ENV_MAP, raising=False)
        monkeypatch.setenv(PER_AGENT_PREFIX + "PROJ_BETA", "https://b/")
        # Act
        url = turn_url_for("proj-beta")
        # Assert
        assert url == "https://b/"

    def test_no_url_returns_none(self, monkeypatch):
        # Arrange
        monkeypatch.delenv(ENV_MAP, raising=False)
        # (no per-agent env set)
        # Act
        url = turn_url_for("ghost")
        # Assert
        assert url is None

    def test_malformed_json_does_not_raise(self, monkeypatch):
        # Arrange
        monkeypatch.setenv(ENV_MAP, "{not json")
        # Act
        url = turn_url_for("anything")
        # Assert
        assert url is None


# --------------------------------------------------------------------------- #
# deliver                                                                     #
# --------------------------------------------------------------------------- #


class TestDeliver:
    """The push wire: HTTP success / 4xx / transport / dry-run / no-url."""

    def test_no_url_returns_explicit_reason(self, monkeypatch):
        # Arrange
        monkeypatch.delenv(ENV_MAP, raising=False)
        monkeypatch.delenv(ENV_DRY_RUN, raising=False)
        # Act
        r = deliver("ghost", "hi", kind="nudge")
        # Assert
        assert r["reason"] == "no-turn-url-configured"

    def test_dry_run_short_circuits_with_dry_run_wire(self, monkeypatch, capsys):
        # Arrange
        monkeypatch.setenv(ENV_DRY_RUN, "1")
        monkeypatch.setenv(ENV_MAP, json.dumps({"a": "http://nope/"}))
        # Act
        r = deliver("a", "hi", kind="notify")
        # Assert
        assert r["wire"] == "dry-run"

    def test_successful_post_returns_ok(self, monkeypatch):
        # Arrange
        cap = _Capture()
        cap.response_code = 200
        monkeypatch.delenv(ENV_DRY_RUN, raising=False)
        with _server(cap) as url:
            monkeypatch.setenv(ENV_MAP, json.dumps({"alpha": url}))
            # Act
            r = deliver("alpha", "ping", kind="nudge")
        # Assert
        assert r["ok"] is True

    def test_post_carries_agent_and_body(self, monkeypatch):
        # Arrange
        cap = _Capture()
        cap.response_code = 200
        monkeypatch.delenv(ENV_DRY_RUN, raising=False)
        with _server(cap) as url:
            monkeypatch.setenv(ENV_MAP, json.dumps({"alpha": url}))
            deliver("alpha", "hi-from-test", kind="nudge")
        # Act
        payload = json.loads(cap.last_body)
        # Assert
        assert (payload["agent"], payload["body"]) == ("alpha", "hi-from-test")

    def test_http_4xx_returns_http_error(self, monkeypatch):
        # Arrange
        cap = _Capture()
        cap.response_code = 404
        monkeypatch.delenv(ENV_DRY_RUN, raising=False)
        with _server(cap) as url:
            monkeypatch.setenv(ENV_MAP, json.dumps({"alpha": url}))
            # Act
            r = deliver("alpha", "ping", kind="nudge")
        # Assert
        assert r["reason"] == "http-error"

    def test_transport_error_when_no_server(self, monkeypatch):
        # Arrange
        port = _free_port()  # bind+release → port currently unbound
        monkeypatch.delenv(ENV_DRY_RUN, raising=False)
        monkeypatch.setenv(
            ENV_MAP,
            json.dumps({"alpha": f"http://127.0.0.1:{port}/turn"}),
        )
        # Act
        r = deliver("alpha", "ping", kind="nudge", timeout=1.0)
        # Assert
        assert r["reason"] == "transport-error"


# --------------------------------------------------------------------------- #
# announce_missing_at_boot                                                    #
# --------------------------------------------------------------------------- #


class TestAnnounceMissing:
    """Boot-time WARN: lists distinct agents with no turn URL."""

    def test_returns_missing_agents(self, monkeypatch):
        # Arrange
        monkeypatch.delenv(ENV_MAP, raising=False)
        tasks = [
            {"agent": "alpha"},
            {"agent": "beta"},
            {"agent": "alpha"},  # dedup
            {"agent": ""},        # ignored
            {"agent": None},      # ignored
        ]
        # Act
        missing = announce_missing_at_boot(tasks)
        # Assert
        assert missing == ["alpha", "beta"]

    def test_configured_agents_dropped_from_missing(self, monkeypatch):
        # Arrange
        monkeypatch.setenv(
            ENV_MAP, json.dumps({"alpha": "http://x/"})
        )
        tasks = [{"agent": "alpha"}, {"agent": "beta"}]
        # Act
        missing = announce_missing_at_boot(tasks)
        # Assert
        assert missing == ["beta"]
