#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""``scitex-cards hub`` (remote-hub PR-4) — doctor's four checks + whoami.

No mocks: a real serve rig on an ephemeral loopback port; the doctor runs
through click's CliRunner against real env; every failure mode is driven by
actually removing its precondition and asserted to carry ITS OWN hint
(constitution §2 — on failure, the next step).

Also pinned: the ``/v1/whoami`` probe (the doctor's check 4 + the cheap
authenticated read): 401 without bearer, 400 without identity, and the echo
returning the declared agent verbatim.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest
from click.testing import CliRunner

from scitex_cards import _server
from scitex_cards._cli._hub import doctor_cmd, provision_cmd


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    store = tmp_path / "tasks.yaml"
    store.write_text("tasks: []\n", encoding="utf-8")
    tokens_dir = tmp_path / "tokens"
    server = _server.make_server(
        store=str(store),
        port=0,
        tokens_dir=tokens_dir,
        audit_path=tmp_path / "logs" / "audit.jsonl",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    url = f"http://127.0.0.1:{port}"
    token_file = tokens_dir / "hub.token"

    monkeypatch.setenv("SCITEX_CARDS_HUB_URL", url)
    monkeypatch.setenv("SCITEX_CARDS_HUB_TOKEN_FILE", str(token_file))
    monkeypatch.delenv("SCITEX_CARDS_HUB_TOKEN", raising=False)
    monkeypatch.setenv("SCITEX_TODO_AGENT_ID", "remote-doctor")

    yield {"url": url, "token": token_file.read_text().strip()}

    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


# === /v1/whoami ============================================================


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_whoami_requires_bearer(rig):
    status, _ = _get(f"{rig['url']}/v1/whoami")
    assert status == 401


def test_whoami_requires_identity(rig):
    status, payload = _get(
        f"{rig['url']}/v1/whoami",
        {"Authorization": f"Bearer {rig['token']}"},
    )
    assert status == 400 and "X-Scitex-Agent" in payload["error"]


def test_whoami_echoes_the_declared_agent(rig):
    status, payload = _get(
        f"{rig['url']}/v1/whoami",
        {
            "Authorization": f"Bearer {rig['token']}",
            "X-Scitex-Agent": "spartan-agent",
        },
    )
    assert (status, payload) == (200, {"agent": "spartan-agent"})


# === hub doctor ============================================================


def _doctor(as_json=True):
    args = ["--json"] if as_json else []
    return CliRunner().invoke(doctor_cmd, args)


def test_doctor_all_green_exits_zero(rig):
    result = _doctor()
    report = json.loads(result.output)
    assert report["ok"] is True
    assert [c["ok"] for c in report["checks"]] == [True, True, True, True]
    assert result.exit_code == 0


def test_doctor_url_unset_fails_with_the_export_hint(rig, monkeypatch):
    monkeypatch.delenv("SCITEX_CARDS_HUB_URL")
    result = _doctor()
    report = json.loads(result.output)
    assert report["ok"] is False
    url_check = report["checks"][0]
    assert url_check["ok"] is False and "SCITEX_CARDS_HUB_URL" in url_check["hint"]
    assert result.exit_code == 1


def test_doctor_missing_token_fails_with_the_provision_hint(rig, monkeypatch):
    monkeypatch.setenv("SCITEX_CARDS_HUB_TOKEN_FILE", "/nonexistent/hub.token")
    result = _doctor()
    report = json.loads(result.output)
    token_check = report["checks"][1]
    assert token_check["ok"] is False and "provision" in token_check["hint"]
    assert result.exit_code == 1


def test_doctor_unreachable_hub_fails_with_the_tunnel_hint(rig, monkeypatch):
    monkeypatch.setenv("SCITEX_CARDS_HUB_URL", "http://127.0.0.1:1")
    result = _doctor()
    report = json.loads(result.output)
    health = next(c for c in report["checks"] if c["name"] == "health_reachable")
    assert health["ok"] is False and "tunnel" in health["hint"]
    assert result.exit_code == 1


def test_doctor_no_identity_fails_check_four(rig, monkeypatch):
    monkeypatch.delenv("SCITEX_TODO_AGENT_ID")
    result = _doctor()
    report = json.loads(result.output)
    echo = next(c for c in report["checks"] if c["name"] == "identity_echo")
    assert echo["ok"] is False and "SCITEX_TODO_AGENT_ID" in echo["hint"]
    assert result.exit_code == 1


def test_doctor_human_output_carries_marks_and_verdict(rig):
    result = _doctor(as_json=False)
    assert "✓" in result.output and "ALL OK" in result.output
    assert result.exit_code == 0


# === hub provision (failure path — the success path needs a real remote) ===


def test_provision_unreachable_host_fails_loud_with_the_ssh_hint(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "scitex_cards._server.default_tokens_dir", lambda: tmp_path / "tokens"
    )
    result = CliRunner().invoke(
        provision_cmd, ["no-such-host-xyzzy"], catch_exceptions=False
    )
    assert result.exit_code != 0
    assert "ssh no-such-host-xyzzy" in result.output
    # The token WAS minted hub-side (mint precedes the copy) — provision
    # failing at transport must not leave the operator guessing which half
    # succeeded; the error names the failing step verbatim.
    assert (tmp_path / "tokens" / "no-such-host-xyzzy.token").exists()


# EOF
