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

Registry fallback (lead a2a ``90acf63b4276422cbe9270cd936b2b45``,
2026-06-12): when neither env is set we query the sac listen daemon
at ``SCITEX_TODO_SAC_LISTEN_URL`` (default ``http://127.0.0.1:7878``)
for ``GET /agents`` and derive the turn URL from the matching row's
``turn_url`` (preferred) or ``a2a_port`` (HTTP-only contract — no
sac CLI / Python imports). The dispatch fields aren't on the row
shape as of 2026-06-12; agent-container is the owner of that field
addition. The code is wired and waits.

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

#: Lead-defined env (a2a `90acf63b4276422cbe9270cd936b2b45`,
#: 2026-06-12): point at the sac listen daemon's HTTP control-plane.
#: Named per scitex env-var convention (no ``SAC_`` rename — the
#: package must not appear to import sac). Defaults to the in-container
#: loopback URL the sac runtime sets up at agent boot.
ENV_SAC_LISTEN = "SCITEX_TODO_SAC_LISTEN_URL"
#: Bearer-token env, matching the value sac listen populates in every
#: agent container. We read the existing ``SAC_LISTEN_BEARER`` so no
#: extra wiring is required from agent-container's side; if it ever
#: changes, the env name is a single-line update here.
ENV_SAC_BEARER = "SAC_LISTEN_BEARER"
DEFAULT_SAC_LISTEN = "http://127.0.0.1:7878"
#: Aggressive: a slow registry must not block per-agent nudge fan-out.
#: 2 s is enough for a same-host loopback round-trip; anything longer
#: would stall the whole cron sweep.
SAC_REGISTRY_TIMEOUT_S = 2.0

#: Default per-POST timeout in seconds. Env-overridable.
#:
#: Why 30 (not 5):
#:
#: SAC's ``/v1/turn`` runs the agent's turn SYNCHRONOUSLY before
#: responding (up to its own ``timeout_s=120`` budget). With the prior
#: 5 s client cap, the cron's POST timed out before the receiver had
#: even queued the turn — proj-scitex-dev's ``session.jsonl`` had ZERO
#: nudge entries after the wire-shape fix landed (lead a2a
#: ``0b59485f`` 2026-06-13, P3a(c) pilot).
#:
#: There is no fast-ack path on ``/v1/turn`` (no ``wait=false`` /
#: ``dispatch_only=true`` / ``async=true`` flag — probed); so until
#: the receiver grows one, the pragmatic stopgap is a longer client
#: budget AND treating "we waited a long time + the response stream
#: never closed" as DISPATCHED rather than failure. 30 s lets typical
#: short turns complete cleanly; longer turns flip to
#: ``reason="dispatched"`` (still ok=True) so the cron doesn't fail
#: the whole batch over one slow turn.
#:
#: Callers can override per-call with ``deliver(..., timeout=120.0)``
#: when they need the receiver's full response payload.
ENV_PUSH_TIMEOUT_S = "SCITEX_TODO_PUSH_TIMEOUT_S"
DEFAULT_TIMEOUT_S = 30.0

#: Short per-POST timeout for INTERACTIVE callers (the board's comment
#: relay) that run INSIDE an HTTP request the operator is waiting on. The
#: cron/nudge path keeps the long :data:`DEFAULT_TIMEOUT_S`; the relay
#: runs in-line on ``POST /comment`` so a 30 s receiver stall = a 30 s
#: board hang (operator-reported P1, 2026-06-25). 2 s covers a same-host
#: loopback; past that we return FAST and toast "could not notify".
NOTIFY_TIMEOUT_S = 2.0


def _default_timeout_s() -> float:
    """Lookup the per-POST timeout, honoring ``ENV_PUSH_TIMEOUT_S``.

    Done at call-time (not module-import time) so tests can override
    the env between cases without re-importing the module.
    """
    raw = os.environ.get(ENV_PUSH_TIMEOUT_S)
    if raw is None:
        return DEFAULT_TIMEOUT_S
    try:
        return float(raw)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_S


def _slug(agent: str) -> str:
    """Match claude-code-telegrammer's env-slug convention."""
    return agent.upper().replace("-", "_").replace("/", "_")


def _turn_url_from_registry(agent: str) -> str | None:
    """Resolve ``agent``'s turn URL via the sac listen daemon's HTTP registry.

    HTTP-only contract (lead a2a `8e51b1e0` + `90acf63b`): we never
    import the sac CLI or Python — the registry shape is exchanged
    purely over HTTP. Reads:

      * ``SCITEX_TODO_SAC_LISTEN_URL`` — base URL of the sac listen
        daemon (default ``http://127.0.0.1:7878``).
      * ``SAC_LISTEN_BEARER`` — bearer token the sac runtime sets in
        every agent container.

    Endpoint: ``GET <base>/agents`` returns ``{"agents": [{name,
    ...}]}``. The dispatch fields we recognise on each row, in order
    of precedence:

      * ``turn_url`` (string) — full URL, used verbatim.
      * ``a2a_port`` (int)   — derive ``http://<base-host>:<port>/v1/turn``.

    Returns:
      * The resolved URL (str) when the row exists AND carries one of
        the dispatch fields.
      * ``None`` when the registry is unreachable, the bearer isn't
        set, the agent isn't in the list, or the row lacks dispatch
        fields. The caller then falls through to the next precedence
        step (today, that means fail-loud "no-turn-url-configured" —
        the same shape as the env-miss path).

    Note: the dispatch fields (``turn_url`` / ``a2a_port``) are NOT
    yet exposed on the ``/agents`` row in the sac listen daemon as of
    2026-06-12. Agent-container is the owner of that field addition
    (lead-confirmed via a2a `90acf63b`); this function ships ready to
    consume them the moment they appear. Until then every agent falls
    through, and the failure mode is unchanged from the prior code.
    """
    bearer = os.environ.get(ENV_SAC_BEARER, "").strip()
    if not bearer:
        # No bearer → we're not inside a sac-managed container OR sac
        # is down. Either way, registry path can't help — silent miss.
        return None
    base = os.environ.get(ENV_SAC_LISTEN, DEFAULT_SAC_LISTEN).strip().rstrip("/")
    if not base:
        return None
    url = f"{base}/agents"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {bearer}",
            "User-Agent": "scitex-todo/_push (registry-lookup)",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=SAC_REGISTRY_TIMEOUT_S) as resp:
            data = json.load(resp)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        # DEBUG: this is a quiet fallback, not an alert. The caller
        # surfaces the user-visible failure when ALL precedence steps
        # miss.
        logger.debug(
            "[scitex-todo._push] sac registry %s unreachable: %s", url, e,
        )
        return None
    except json.JSONDecodeError as e:
        logger.debug(
            "[scitex-todo._push] sac registry %s returned non-JSON: %s",
            url, e,
        )
        return None
    rows = data.get("agents", []) if isinstance(data, dict) else []
    for row in rows:
        if not isinstance(row, dict) or row.get("name") != agent:
            continue
        explicit = row.get("turn_url")
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip()
        port = row.get("a2a_port")
        if isinstance(port, int) and port > 0:
            from urllib.parse import urlparse
            parsed = urlparse(base)
            host = parsed.hostname or "127.0.0.1"
            return f"http://{host}:{port}/v1/turn"
        # Row exists but lacks dispatch fields — known gap until
        # agent-container ships the field. Treat as miss.
        return None
    return None


def turn_url_for(agent: str) -> str | None:
    """Resolve the turn URL for ``agent``. Returns None when not configured.

    Lookup order (lead a2a `90acf63b`, 2026-06-12):
      1. ``SCITEX_TODO_AGENT_TURN_URLS`` JSON map entry (operator-pinned).
      2. ``SCITEX_TODO_TURN_URL_<SLUG>`` per-agent env (telegrammer wire).
      3. sac listen daemon's ``/agents`` HTTP registry — derive from
         the row's ``turn_url`` (preferred) or ``a2a_port``. See
         :func:`_turn_url_from_registry` for the contract.
      4. None — caller falls through to fail-loud
         "no-turn-url-configured".

    The registry step (3) keeps the package's HTTP-only contract: we
    never import the sac CLI or sac's Python, we talk to its listen
    daemon. That contract is locked by lead a2a `8e51b1e0` /
    `ffc6629c80`.
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
    registry_url = _turn_url_from_registry(agent)
    if registry_url:
        return registry_url
    return None


def _now_iso() -> str:
    return _dt.datetime.now(tz=_dt.timezone.utc).replace(microsecond=0).isoformat()


def _timeout_result(
    agent: str, kind: str, url: str, err: BaseException,
    timeout: float, dispatched_is_ok: bool,
) -> dict:
    """Result dict for a client read-timeout, shared by both except-branches.

    ``dispatched_is_ok=True`` (background/cron) → ``ok=True,
    reason="dispatched"`` (request body fully sent; receiver mid-turn).
    ``dispatched_is_ok=False`` (interactive relay) → ``ok=False,
    reason="timeout"`` — surfaced FAST so the board toasts loud.
    """
    reason = "dispatched" if dispatched_is_ok else "timeout"
    logger.warning(
        "[scitex-todo._push] %s read-timeout after %.1fs for agent=%s "
        "→ reason=%s. %s",
        url, timeout, agent, reason, err,
    )
    return {
        "ok": dispatched_is_ok, "agent": agent, "wire": "http",
        "kind": kind, "url": url, "status": None, "reason": reason,
        "error": str(err),
    }


def deliver(
    agent: str,
    body: str,
    *,
    kind: str = "notify",
    task_id: str | None = None,
    store_path: str | None = None,
    timeout: float | None = None,
    dispatched_is_ok: bool = True,
) -> dict:
    """Deliver ``body`` to ``agent`` via the configured turn URL.

    ``timeout`` defaults to :data:`DEFAULT_TIMEOUT_S` (env-overridable
    via ``SCITEX_TODO_PUSH_TIMEOUT_S``). The receiver's ``/v1/turn``
    runs the turn synchronously, so a client read-timeout does NOT
    imply the request was lost — the receiver may still be processing
    it. We treat the timeout case as DISPATCHED success (``ok=True,
    reason="dispatched"``) so a single slow turn can't fail the whole
    nudge batch. Callers that need the receiver's full response payload
    can pass an explicit long ``timeout`` (e.g. 120.0).

    ``dispatched_is_ok`` (default ``True``) governs the read-timeout
    verdict. Background callers keep the default (timeout → ``ok=True,
    reason="dispatched"``). INTERACTIVE callers (the comment relay) pass
    ``False`` + a short ``timeout`` (:data:`NOTIFY_TIMEOUT_S`) so a
    timeout returns FAST with ``ok=False, reason="timeout"`` and the
    board toasts a LOUD failure instead of hanging ~30 s and silently
    claiming success (operator P1, 2026-06-25; fail-fast/fail-loud).

    Returns a result dict suitable for inclusion in a JsonResponse:

      {
        "ok": bool,
        "agent": str,
        "wire": "http" | "dry-run",
        "kind": str,
        "url": "<url>" | None,
        "status": <http-status> or None,
        "reason": <one of "delivered" | "dispatched" |
                          "no-turn-url-configured" |
                          "http-error" | "transport-error" |
                          "dry-run">,
      }
    """
    if timeout is None:
        timeout = _default_timeout_s()
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

    # ``text`` is the field SAC's /v1/turn (and claude-code-telegrammer's
    # TURN_URL) expects; ``body`` is scitex-todo's historical name. We send
    # BOTH so the wire is back-compat: consumers that key off ``text`` (SAC,
    # the telegrammer) succeed, and any older consumer keying off ``body``
    # still works. Without this alias the SAC receiver returns
    # ``HTTP 400 missing or empty 'text' field`` and the whole nudge chain
    # is dead on arrival (proj-scitex-todo P3a(c) pilot, 2026-06-13 — see
    # lead a2a ``8afe659e``).
    payload = {
        "agent": agent,
        "kind": kind,
        "source": "scitex-todo",
        "text": body,
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
    except TimeoutError as e:
        # Receiver's /v1/turn runs the turn synchronously up to ~120 s;
        # we wait `timeout` (default 30 s, env-overridable). If we hit
        # the cap, the request body was already fully sent in the first
        # second or two of that budget — the receiver is mid-turn, NOT
        # unreachable. Background callers treat that as DISPATCHED
        # success so the cron doesn't fail the whole nudge batch over
        # one slow turn (lead a2a `0b59485f`, 2026-06-13). Interactive
        # callers (`dispatched_is_ok=False`) fail loud instead.
        return _timeout_result(agent, kind, url, e, timeout, dispatched_is_ok)
    except (urllib.error.URLError, OSError) as e:
        # Distinguish a wrapped TimeoutError (the urlopen socket layer
        # often raises a TimeoutError nested inside URLError) from a real
        # transport failure (connection refused / DNS / SSL handshake).
        nested = getattr(e, "reason", None)
        if isinstance(nested, TimeoutError) or "timed out" in str(e):
            return _timeout_result(
                agent, kind, url, e, timeout, dispatched_is_ok
            )
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
    "ENV_PUSH_TIMEOUT_S",
    "PER_AGENT_PREFIX",
    "ENV_SAC_LISTEN",
    "ENV_SAC_BEARER",
    "DEFAULT_SAC_LISTEN",
    "SAC_REGISTRY_TIMEOUT_S",
    "DEFAULT_TIMEOUT_S",
    "NOTIFY_TIMEOUT_S",
    "turn_url_for",
    "deliver",
    "announce_missing_at_boot",
]
