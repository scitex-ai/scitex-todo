#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for the slice-3 Telegram delivery channel.

NO mocks (STX-NM / PA-306): the wire is exercised through a REAL fake
``http_post`` recorder/responder injected via the ``http_post=`` seam. Covers
the spec's required cases:

* (a) 200 → ``sent``, and the POST got the right URL (token in path) +
      ``chat_id`` + ``text``.
* (b) 429 with ``Retry-After`` → ``failed`` with ``retry_after`` set.
* (c) 403 → ``failed`` with ``retry_after`` None.
* (d) a urllib raise inside ``deliver`` → ``failed`` (caught here).
* (e) missing token → ``failed`` with the clear, fail-loud detail.

Plus: 5xx is retryable, and a 429 with the hint only in the JSON body still
parses ``retry_after`` from ``parameters.retry_after``.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import urllib.error

from scitex_todo._delivery._channel import Status
from scitex_todo._delivery._channels.telegram import TOKEN_ENV, TelegramChannel


class FakeHttp:
    """A REAL recorder/responder for the ``http_post`` seam (not a mock).

    Records every ``(url, data)`` call and returns a pre-programmed
    ``(status_code, headers, body)`` triple — or RAISES a pre-programmed
    exception to exercise the network-error path.
    """

    def __init__(self, *, status=200, headers=None, body="", raises=None):
        self.status = status
        self.headers = headers or {}
        self.body = body
        self.raises = raises
        self.calls: list[dict] = []

    def __call__(self, url: str, data: bytes):
        self.calls.append({"url": url, "data": data})
        if self.raises is not None:
            raise self.raises
        return self.status, self.headers, self.body


def _note(body="Card c1 reassigned to you", **over):
    n = {
        "id": "n_1",
        "event_type": "reassigned",
        "card_id": "c1",
        "body": body,
    }
    n.update(over)
    return n


# --------------------------------------------------------------------------- #
# (a) 200 → sent, with the right URL + chat_id + text                         #
# --------------------------------------------------------------------------- #
def test_success_200_is_sent_with_correct_payload():
    http = FakeHttp(status=200, body='{"ok": true}')
    chan = TelegramChannel(token="TKN123", http_post=http)

    result = chan.deliver(
        recipient="u_alice", address="555000", notification=_note()
    )

    assert result.status is Status.SENT
    assert result.channel == "telegram"
    assert result.retry_after is None

    assert len(http.calls) == 1
    call = http.calls[0]
    # Token is in the URL PATH segment (/bot<TOKEN>/sendMessage).
    parsed = urlparse(call["url"])
    assert parsed.scheme == "https"
    assert parsed.netloc == "api.telegram.org"
    assert parsed.path == "/botTKN123/sendMessage"
    # The form body carries chat_id + the notification text.
    form = parse_qs(call["data"].decode("utf-8"))
    assert form["chat_id"] == ["555000"]
    assert form["text"] == ["Card c1 reassigned to you"]


def test_text_falls_back_to_event_and_card_when_body_empty():
    http = FakeHttp(status=200, body='{"ok": true}')
    chan = TelegramChannel(token="TKN", http_post=http)
    chan.deliver(recipient="u", address="1", notification=_note(body=""))
    form = parse_qs(http.calls[0]["data"].decode("utf-8"))
    assert form["text"] == ["[reassigned] card c1"]


# --------------------------------------------------------------------------- #
# (b) 429 with Retry-After header → failed + retry_after set                  #
# --------------------------------------------------------------------------- #
def test_429_with_retry_after_header_sets_retry_after():
    http = FakeHttp(
        status=429,
        headers={"Retry-After": "37"},
        body='{"ok": false, "error_code": 429}',
    )
    chan = TelegramChannel(token="TKN", http_post=http)
    result = chan.deliver(recipient="u", address="9", notification=_note())

    assert result.status is Status.FAILED
    assert result.retry_after == 37.0
    assert "429" in (result.detail or "")


def test_429_with_json_parameters_retry_after_is_parsed():
    body = json.dumps(
        {"ok": False, "error_code": 429, "parameters": {"retry_after": 60}}
    )
    http = FakeHttp(status=429, headers={}, body=body)
    chan = TelegramChannel(token="TKN", http_post=http)
    result = chan.deliver(recipient="u", address="9", notification=_note())

    assert result.status is Status.FAILED
    assert result.retry_after == 60.0


# --------------------------------------------------------------------------- #
# (c) 403 → failed, retry_after None                                          #
# --------------------------------------------------------------------------- #
def test_403_is_failed_without_retry_after():
    http = FakeHttp(
        status=403,
        body='{"ok": false, "error_code": 403, "description": "bot blocked"}',
    )
    chan = TelegramChannel(token="TKN", http_post=http)
    result = chan.deliver(recipient="u", address="9", notification=_note())

    assert result.status is Status.FAILED
    assert result.retry_after is None
    assert "403" in (result.detail or "")


def test_other_4xx_is_failed_without_retry_after():
    http = FakeHttp(status=400, body='{"ok": false, "error_code": 400}')
    chan = TelegramChannel(token="TKN", http_post=http)
    result = chan.deliver(recipient="u", address="9", notification=_note())
    assert result.status is Status.FAILED
    assert result.retry_after is None
    assert "400" in (result.detail or "")


# --------------------------------------------------------------------------- #
# 5xx → failed (retryable), no retry_after                                    #
# --------------------------------------------------------------------------- #
def test_5xx_is_failed_retryable_without_retry_after():
    http = FakeHttp(status=502, body="bad gateway")
    chan = TelegramChannel(token="TKN", http_post=http)
    result = chan.deliver(recipient="u", address="9", notification=_note())
    assert result.status is Status.FAILED
    assert result.retry_after is None
    assert "502" in (result.detail or "")


# --------------------------------------------------------------------------- #
# (d) network/urllib raise inside deliver → caught as failed                  #
# --------------------------------------------------------------------------- #
def test_network_error_is_caught_as_failed():
    http = FakeHttp(raises=urllib.error.URLError("connection reset"))
    chan = TelegramChannel(token="TKN", http_post=http)
    result = chan.deliver(recipient="u", address="9", notification=_note())
    assert result.status is Status.FAILED
    assert result.retry_after is None
    assert "transport error" in (result.detail or "")


# --------------------------------------------------------------------------- #
# (e) missing token → failed with the clear, fail-loud detail                 #
# --------------------------------------------------------------------------- #
def test_missing_token_is_failed_with_clear_detail(monkeypatch):
    # No override token AND env var absent → fail-loud.
    monkeypatch.delenv(TOKEN_ENV, raising=False)
    http = FakeHttp(status=200)
    chan = TelegramChannel(http_post=http)
    result = chan.deliver(recipient="u", address="9", notification=_note())

    assert result.status is Status.FAILED
    assert result.retry_after is None
    assert TOKEN_ENV in (result.detail or "")
    assert "no telegram bot token" in (result.detail or "")
    # The wire was never touched (no token → no POST).
    assert http.calls == []


def test_token_resolved_lazily_from_env(monkeypatch):
    monkeypatch.setenv(TOKEN_ENV, "ENVTKN")
    http = FakeHttp(status=200, body='{"ok": true}')
    chan = TelegramChannel(http_post=http)  # no override
    result = chan.deliver(recipient="u", address="9", notification=_note())
    assert result.status is Status.SENT
    assert urlparse(http.calls[0]["url"]).path == "/botENVTKN/sendMessage"


# EOF
