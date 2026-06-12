#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scitex-todo's self-contained push delivery wire.

Operator standing direction (lead a2a `8e51b1e072d14e2a81f5171cb5aca9f8`
+ `ffc6629c80e4462a8401fb7e4ebb7240`, 2026-06-12, operator TG12617): the
package must NOT depend on the `sac` CLI for outbound notifications.
Push delivery lives inside scitex-todo itself; the contract is HTTP,
not Python imports (ecosystem doctrine).

Operator TG12618 follow-up: the long-term architecture mirrors
claude-code-telegrammer — a dedicated stdio MCP channel + a board-event
poller per agent, wired via per-agent `.mcp.json`. That is queued as
PR (j) in the implementation roadmap. THIS module is the v1
**HTTP-push** half: a one-shot wake of the agent's turn URL with the
board-event body. Agents that read MCP inbox first (via the future
channel) will pick up the same event from both sides.

Configuration
-------------
``SCITEX_TODO_AGENT_TURN_URLS`` — JSON object mapping agent ids to
their turn URLs (any HTTP endpoint that accepts a JSON ``POST``):

    SCITEX_TODO_AGENT_TURN_URLS='{
        "proj-scitex-todo": "https://sac.scitex/v1/turn/proj-scitex-todo",
        "lead": "https://sac.scitex/v1/turn/lead"
    }'

Per-agent fallback ``SCITEX_TODO_TURN_URL_<AGENT_SLUG>`` (agent slug
upper-case + hyphens → underscores) — same shape as
claude-code-telegrammer's `TURN_URL` env.

Loud-but-not-fatal policy (lead-confirmed)
------------------------------------------
* No URL for an agent → return ``{ok: False, reason:
  "no-turn-url-configured"}`` and log ERROR. Callers (UI / cron /
  CLI) decide what to surface; ``handle_nudge`` returns the
  ``ok=False`` to the UI toast and ``handle_comment``'s relay leaves
  a visible marker on the comment.
* HTTP 4xx/5xx → return ``{ok: False, reason: "http-error",
  status: <code>}``; never raise out into the request handler.
* Network exception / timeout → same shape, ``reason:
  "transport-error", error: <str>``.
* ``SCITEX_TODO_PUSH_DRY_RUN=1`` → no network; print the body to
  stdout for dev / test; returns ``ok=True, wire="dry-run"``.

Boot-time announcement (``announce_missing_at_boot``) lists the
agents in ``tasks.yaml`` that don't have a configured URL so the
operator can fix the gap before the first event.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

ENV_MAP = "SCITEX_TODO_AGENT_TURN_URLS"
ENV_DRY_RUN = "SCITEX_TODO_PUSH_DRY_RUN"
PER_AGENT_PREFIX = "SCITEX_TODO_TURN_URL_"

DEFAULT_TIMEOUT_S = 5.0


def _slug(agent: str) -> str:
    """Match claude-code-telegrammer's env-slug convention."""
    return agent.upper().replace("-", "_").replace("/", "_")


def turn_url_for(agent: str) -> str | None:
    """Resolve the turn URL for ``agent``. Returns None when not configured.

    Lookup order:
      1. ``SCITEX_TODO_AGENT_TURN_URLS`` JSON map entry (canonical).
      2. ``SCITEX_TODO_TURN_URL_<SLUG>`` per-agent env (fallback,
         matches the telegrammer wire).
    """
    raw = os.environ.get(ENV_MAP)
    if raw:
        try:
            mapping = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error(
                "[scitex-todo._push] %s is not valid JSON — ignoring: %s",
                ENV_MAP, exc,
            )
            mapping = {}
        if isinstance(mapping, dict):
            url = mapping.get(agent)
            if isinstance(url, str) and url.strip():
                return url.strip()
    per_agent = os.environ.get(PER_AGENT_PREFIX + _slug(agent))
    if per_agent and per_agent.strip():
        return per_agent.strip()
    return None


def _now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).replace(microsecond=0).isoformat()


def deliver(
    agent: str,
    body: str,
    *,
    kind: str = "notify",
    task_id: str | None = None,
    store_path: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
) -> dict:
    """Deliver ``body`` to ``agent`` via the configured turn URL.

    Returns a result dict suitable for inclusion in a JsonResponse:

      {
        "ok": bool,
        "agent": str,
        "wire": "http" | "dry-run",
        "kind": str,
        "url": "<url>" | None,
        "status": <http-status> or None,
        "reason": <one of "delivered" | "no-turn-url-configured"
                          | "http-error" | "transport-error"
                          | "dry-run">,
      }
    """
    # Dev / test escape hatch.
    if os.environ.get(ENV_DRY_RUN) == "1":
        print(
            f"\n=== scitex-todo PUSH dry-run → {agent} ({kind}) ===\n"
            f"{body}\n=== end {agent} ===\n",
            flush=True,
        )
        return {
            "ok": True,
            "agent": agent,
            "wire": "dry-run",
            "kind": kind,
            "url": None,
            "status": None,
            "reason": "dry-run",
        }

    url = turn_url_for(agent)
    if not url:
        logger.error(
            "[scitex-todo._push] no turn URL configured for agent=%r "
            "(set %s or %s%s)",
            agent, ENV_MAP, PER_AGENT_PREFIX, _slug(agent),
        )
        return {
            "ok": False,
            "agent": agent,
            "wire": "http",
            "kind": kind,
            "url": None,
            "status": None,
            "reason": "no-turn-url-configured",
        }

    payload = {
        "agent": agent,
        "kind": kind,
        "source": "scitex-todo",
        "body": body,
        "task_id": task_id,
        "store_path": store_path,
        "ts": _now_iso(),
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "User-Agent": "scitex-todo/_push (channel)",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.getcode()
    except urllib.error.HTTPError as e:
        logger.error(
            "[scitex-todo._push] HTTP %s from %s for agent=%s: %s",
            e.code, url, agent, e.reason,
        )
        return {
            "ok": False, "agent": agent, "wire": "http", "kind": kind,
            "url": url, "status": e.code, "reason": "http-error",
        }
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        logger.error(
            "[scitex-todo._push] transport error to %s for agent=%s: %s",
            url, agent, e,
        )
        return {
            "ok": False, "agent": agent, "wire": "http", "kind": kind,
            "url": url, "status": None, "reason": "transport-error",
            "error": str(e),
        }

    return {
        "ok": True, "agent": agent, "wire": "http", "kind": kind,
        "url": url, "status": status, "reason": "delivered",
    }


def announce_missing_at_boot(tasks: list[dict]) -> list[str]:
    """Log a WARN listing the distinct agents in ``tasks`` that have no
    turn URL configured. Called once at board startup.

    Returns the list of missing agents so callers (tests, the boot
    hook) can assert / surface the gap themselves.
    """
    agents = sorted({
        (t.get("agent") or "").strip()
        for t in tasks
        if (t.get("agent") or "").strip()
    })
    missing = [a for a in agents if turn_url_for(a) is None]
    if missing:
        logger.warning(
            "[scitex-todo._push] %d agent(s) without turn URL — "
            "nudge + comment-relay will return ok=False for them: %s",
            len(missing), ", ".join(missing),
        )
    return missing


__all__ = [
    "ENV_MAP",
    "ENV_DRY_RUN",
    "PER_AGENT_PREFIX",
    "DEFAULT_TIMEOUT_S",
    "turn_url_for",
    "deliver",
    "announce_missing_at_boot",
]
