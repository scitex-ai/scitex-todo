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
    DEFAULT_TIMEOUT_S,
    ENV_DRY_RUN,
    ENV_MAP,
    ENV_PUSH_TIMEOUT_S,
    ENV_SAC_BEARER,
    ENV_SAC_LISTEN,
    PER_AGENT_PREFIX,
    _default_timeout_s,
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


@contextmanager
def _registry_server(payload: dict, response_code: int = 200):
    """Spawn a localhost sac-listen-shaped registry server.

    Responds to ``GET /agents`` with ``payload`` (already a Python
    dict, encoded as JSON on each request). Any other method or path
    returns 404. Bearer token is accepted unconditionally — auth is
    sac's concern, not this test's. Used by the registry-lookup
    precedence-3 tests.
    """

    body = json.dumps(payload).encode("utf-8")

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if self.path != "/agents":
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(response_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a, **kw):  # silence test noise
            return

    port = _free_port()
    httpd = http.server.HTTPServer(("127.0.0.1", port), Handler)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        httpd.shutdown()


# --------------------------------------------------------------------------- #
# turn_url_for                                                                #
# --------------------------------------------------------------------------- #


class TestTurnUrlFor:
    """Env resolution: canonical JSON map then per-agent fallback."""

    def test_json_map_returns_canonical_url(self, env):
        # Arrange
        env.set(
            ENV_MAP,
            json.dumps({"alpha": "https://canonical/turn/alpha"}),
        )
        # Act
        url = turn_url_for("alpha")
        # Assert
        assert url == "https://canonical/turn/alpha"

    def test_per_agent_env_fallback(self, env):
        # Arrange
        env.delete(ENV_MAP)
        env.set(PER_AGENT_PREFIX + "PROJ_BETA", "https://b/")
        # Act
        url = turn_url_for("proj-beta")
        # Assert
        assert url == "https://b/"

    def test_no_url_returns_none(self, env):
        # Arrange
        env.delete(ENV_MAP)
        # (no per-agent env set)
        # Act
        url = turn_url_for("ghost")
        # Assert
        assert url is None

    def test_malformed_json_does_not_raise(self, env):
        # Arrange
        env.set(ENV_MAP, "{not json")
        # Act
        url = turn_url_for("anything")
        # Assert
        assert url is None


# --------------------------------------------------------------------------- #
# turn_url_for — registry lookup (precedence 3)                              #
# --------------------------------------------------------------------------- #


class TestRegistryLookup:
    """The sac listen daemon's /agents endpoint as a precedence-3
    fallback. End-to-end via a real localhost http.server (no mocks
    per STX-NM / PA-306).

    Field shape we honor on a row:
      * ``turn_url`` (str) — used verbatim.
      * ``a2a_port`` (int) — derive ``http://<base-host>:<port>/v1/turn``.

    Neither is on the sac listen row shape as of 2026-06-12 — the
    field addition is agent-container's side. These tests pin the
    contract so the code is ready the moment they land.
    """

    def _clear_env(self, env):
        """Strip env precedence 1+2 so registry path is the only winner."""
        env.delete(ENV_MAP)
        # Also clear any per-agent env that might leak in via the shell.
        for k in list(os.environ):
            if k.startswith(PER_AGENT_PREFIX):
                env.delete(k)

    def test_explicit_turn_url_field_is_returned_verbatim(self, env):
        # Arrange
        self._clear_env(env)
        env.set(ENV_SAC_BEARER, "any-token")
        payload = {
            "agents": [
                {"name": "proj-alpha", "turn_url": "https://explicit/v1/turn/alpha"},
            ]
        }
        with _registry_server(payload) as base:
            env.set(ENV_SAC_LISTEN, base)
            # Act
            url = turn_url_for("proj-alpha")
        # Assert
        assert url == "https://explicit/v1/turn/alpha"

    def test_a2a_port_derives_loopback_turn_url(self, env):
        # Arrange
        self._clear_env(env)
        env.set(ENV_SAC_BEARER, "any-token")
        payload = {"agents": [{"name": "proj-beta", "a2a_port": 19007}]}
        with _registry_server(payload) as base:
            env.set(ENV_SAC_LISTEN, base)
            # Act
            url = turn_url_for("proj-beta")
        # Assert
        assert url == "http://127.0.0.1:19007/v1/turn"

    def test_row_without_dispatch_fields_returns_none(self, env):
        # Arrange — today's actual sac listen row shape (the gap).
        self._clear_env(env)
        env.set(ENV_SAC_BEARER, "any-token")
        payload = {
            "agents": [
                {
                    "name": "proj-gamma",
                    "config": "/some/path/spec.yaml",
                    "pid": 1234,
                    "started_at": "2026-06-12T00:00:00Z",
                    "screen": "proj-gamma",
                },
            ]
        }
        with _registry_server(payload) as base:
            env.set(ENV_SAC_LISTEN, base)
            # Act
            url = turn_url_for("proj-gamma")
        # Assert — known gap until agent-container ships the field.
        assert url is None

    def test_agent_not_in_registry_returns_none(self, env):
        # Arrange
        self._clear_env(env)
        env.set(ENV_SAC_BEARER, "any-token")
        payload = {"agents": [{"name": "proj-other", "a2a_port": 19999}]}
        with _registry_server(payload) as base:
            env.set(ENV_SAC_LISTEN, base)
            # Act
            url = turn_url_for("proj-ghost")
        # Assert
        assert url is None

    def test_missing_bearer_short_circuits(self, env):
        # Arrange — no bearer → we don't even reach out.
        self._clear_env(env)
        env.delete(ENV_SAC_BEARER)
        # Point at an unbound port: if we DID reach out, we'd get a
        # transport error; the short-circuit means we never try.
        env.set(ENV_SAC_LISTEN, f"http://127.0.0.1:{_free_port()}")
        # Act
        url = turn_url_for("proj-alpha")
        # Assert
        assert url is None

    def test_unreachable_registry_returns_none_silently(self, env):
        # Arrange — bearer set, listen URL points at no server.
        self._clear_env(env)
        env.set(ENV_SAC_BEARER, "any-token")
        env.set(ENV_SAC_LISTEN, f"http://127.0.0.1:{_free_port()}")
        # Act
        url = turn_url_for("proj-alpha")
        # Assert
        assert url is None

    def test_env_precedence_wins_over_registry(self, env):
        # Arrange — both env map AND registry would resolve; env wins.
        env.set(
            ENV_MAP,
            json.dumps({"proj-alpha": "https://env-pin/turn"}),
        )
        env.set(ENV_SAC_BEARER, "any-token")
        payload = {
            "agents": [
                {"name": "proj-alpha", "turn_url": "https://registry/turn"},
            ]
        }
        with _registry_server(payload) as base:
            env.set(ENV_SAC_LISTEN, base)
            # Act
            url = turn_url_for("proj-alpha")
        # Assert
        assert url == "https://env-pin/turn"


# --------------------------------------------------------------------------- #
# deliver                                                                     #
# --------------------------------------------------------------------------- #


class TestDeliver:
    """The push wire: HTTP success / 4xx / transport / dry-run / no-url."""

    def test_no_url_returns_explicit_reason(self, env):
        # Arrange
        env.delete(ENV_MAP)
        env.delete(ENV_DRY_RUN)
        # Act
        r = deliver("ghost", "hi", kind="nudge")
        # Assert
        assert r["reason"] == "no-turn-url-configured"

    def test_dry_run_short_circuits_with_dry_run_wire(self, env, capsys):
        # Arrange
        env.set(ENV_DRY_RUN, "1")
        env.set(ENV_MAP, json.dumps({"a": "http://nope/"}))
        # Act
        r = deliver("a", "hi", kind="notify")
        # Assert
        assert r["wire"] == "dry-run"

    def test_successful_post_returns_ok(self, env):
        # Arrange
        cap = _Capture()
        cap.response_code = 200
        env.delete(ENV_DRY_RUN)
        with _server(cap) as url:
            env.set(ENV_MAP, json.dumps({"alpha": url}))
            # Act
            r = deliver("alpha", "ping", kind="nudge")
        # Assert
        assert r["ok"] is True

    def test_post_carries_agent_and_body(self, env):
        # Arrange
        cap = _Capture()
        cap.response_code = 200
        env.delete(ENV_DRY_RUN)
        with _server(cap) as url:
            env.set(ENV_MAP, json.dumps({"alpha": url}))
            deliver("alpha", "hi-from-test", kind="nudge")
        # Act
        payload = json.loads(cap.last_body)
        # Assert
        assert (payload["agent"], payload["body"]) == ("alpha", "hi-from-test")

    def test_post_carries_text_field_aliased_to_body(self, env):
        # Regression guard: SAC's /v1/turn (and claude-code-telegrammer's
        # TURN_URL) require a `text` key — pre-fix scitex-todo only sent
        # `body`, so the SAC receiver returned HTTP 400 "missing or empty
        # 'text' field" and the whole nudge chain died on arrival
        # (proj-scitex-todo P3a(c) pilot, 2026-06-13; lead a2a 8afe659e).
        # Arrange
        cap = _Capture()
        cap.response_code = 200
        env.delete(ENV_DRY_RUN)
        with _server(cap) as url:
            env.set(ENV_MAP, json.dumps({"alpha": url}))
            deliver("alpha", "hi-from-pilot", kind="notify")
        # Act
        payload = json.loads(cap.last_body)
        # Assert
        assert payload["text"] == "hi-from-pilot"

    def test_succeeds_against_text_strict_receiver(self, env):
        # End-to-end via a real localhost http.server that mimics SAC's
        # /v1/turn validation: 400 when `text` is missing or empty, 200
        # otherwise. Pre-fix this test would fail (HTTP 400 → reason=
        # http-error). With the `text` alias in the payload it passes.
        # Arrange
        received: dict = {}

        class _TextStrictHandler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                try:
                    body = json.loads(raw)
                except json.JSONDecodeError:
                    body = {}
                text = (body.get("text") or "").strip()
                if not text:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error":"missing or empty text field"}')
                    return
                received["text"] = text
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

            def log_message(self, *a, **kw):  # silence test noise
                return

        port = _free_port()
        httpd = http.server.HTTPServer(("127.0.0.1", port), _TextStrictHandler)
        th = threading.Thread(target=httpd.serve_forever, daemon=True)
        th.start()
        env.delete(ENV_DRY_RUN)
        env.set(
            ENV_MAP,
            json.dumps({"alpha": f"http://127.0.0.1:{port}/v1/turn"}),
        )
        try:
            # Act
            result = deliver("alpha", "hi from pilot", kind="notify")
        finally:
            httpd.shutdown()
        # Assert — payload satisfied the text-strict receiver.
        assert result["reason"] == "delivered"

    def test_http_4xx_returns_http_error(self, env):
        # Arrange
        cap = _Capture()
        cap.response_code = 404
        env.delete(ENV_DRY_RUN)
        with _server(cap) as url:
            env.set(ENV_MAP, json.dumps({"alpha": url}))
            # Act
            r = deliver("alpha", "ping", kind="nudge")
        # Assert
        assert r["reason"] == "http-error"

    def test_transport_error_when_no_server(self, env):
        # Arrange
        port = _free_port()  # bind+release → port currently unbound
        env.delete(ENV_DRY_RUN)
        env.set(
            ENV_MAP,
            json.dumps({"alpha": f"http://127.0.0.1:{port}/turn"}),
        )
        # Act
        r = deliver("alpha", "ping", kind="nudge", timeout=1.0)
        # Assert
        assert r["reason"] == "transport-error"

    def test_read_timeout_treated_as_dispatched_ok(self, env):
        # Receiver's /v1/turn runs the turn synchronously up to ~120 s
        # but the cron must not block that long. When the client times
        # out, the request body was already fully sent; the receiver is
        # mid-turn, not unreachable. deliver() flips this to
        # ``ok=True, reason="dispatched"`` so the cron's nudge batch
        # doesn't fail over one slow turn (lead a2a `0b59485f`).
        # Arrange — a real localhost server that accepts the connect +
        # request body but never writes a response, forcing a read
        # timeout on the client side.
        accept_event = threading.Event()

        class _NeverRespondHandler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                # Drain the request body so the client's send phase
                # completes — this mirrors the receiver having
                # accepted the request and started a long turn.
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                accept_event.set()
                # Sleep longer than the client timeout — receiver is
                # "still computing the turn".
                import time

                time.sleep(5)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

            def log_message(self, *a, **kw):
                return

        port = _free_port()
        httpd = http.server.HTTPServer(("127.0.0.1", port), _NeverRespondHandler)
        th = threading.Thread(target=httpd.serve_forever, daemon=True)
        th.start()
        env.delete(ENV_DRY_RUN)
        env.set(
            ENV_MAP,
            json.dumps({"alpha": f"http://127.0.0.1:{port}/v1/turn"}),
        )
        try:
            # Act — sub-second timeout to keep the test fast; the handler
            # sleeps 5 s, so we will time out long before it responds.
            r = deliver("alpha", "ping", kind="notify", timeout=0.5)
        finally:
            httpd.shutdown()
        # Assert — receiver got the request body, so the cron treats
        # this as a successful dispatch even though we never read a
        # response.
        assert r["reason"] == "dispatched"

    def test_default_timeout_env_override(self, env):
        # Arrange — env value parsed at call-time, not module import.
        env.set(ENV_PUSH_TIMEOUT_S, "12.5")
        # Act
        v = _default_timeout_s()
        # Assert
        assert v == 12.5

    def test_default_timeout_falls_back_to_constant_when_env_unset(self, env):
        # Arrange
        env.delete(ENV_PUSH_TIMEOUT_S)
        # Act
        v = _default_timeout_s()
        # Assert
        assert v == DEFAULT_TIMEOUT_S


# --------------------------------------------------------------------------- #
# announce_missing_at_boot                                                    #
# --------------------------------------------------------------------------- #


class TestAnnounceMissing:
    """Boot-time WARN: lists distinct agents with no turn URL."""

    def test_returns_missing_agents(self, env):
        # Arrange
        env.delete(ENV_MAP)
        tasks = [
            {"agent": "alpha"},
            {"agent": "beta"},
            {"agent": "alpha"},  # dedup
            {"agent": ""},  # ignored
            {"agent": None},  # ignored
        ]
        # Act
        missing = announce_missing_at_boot(tasks)
        # Assert
        assert missing == ["alpha", "beta"]

    def test_configured_agents_dropped_from_missing(self, env):
        # Arrange
        env.set(ENV_MAP, json.dumps({"alpha": "http://x/"}))
        tasks = [{"agent": "alpha"}, {"agent": "beta"}]
        # Act
        missing = announce_missing_at_boot(tasks)
        # Assert
        assert missing == ["beta"]
