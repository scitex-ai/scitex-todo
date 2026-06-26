#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Turn-URL resolution for scitex-todo's push delivery.

One cohesive responsibility: *where do I deliver a board event for this
agent?* The actual HTTP wire (``deliver`` + boot announcement) lives in
:mod:`scitex_todo._push`, which re-exports this module's public surface so
``from scitex_todo._push import turn_url_for`` (and the env constants) keep
resolving unchanged.

Resolution precedence (see :func:`turn_url_for`):

  0. scitex-todo's OWN user registry (``users:`` section of the task
     store) — file-local, NO bearer, the reliable PRIMARY source.
  1. ``SCITEX_TODO_AGENT_TURN_URLS`` JSON map (operator-pinned).
  2. ``SCITEX_TODO_TURN_URL_<SLUG>`` per-agent env (telegrammer wire).
  3. sac listen daemon's ``/agents`` HTTP registry (HTTP-only contract,
     locked by lead a2a `8e51b1e0` / `ffc6629c80`).
  4. None → caller falls through to fail-loud "no-turn-url-configured".

Step 0 is the durable root fix (card
``todo-push-turn-url-from-user-registry-20260626``): the board OWNS the
registry, so that file-local, bearer-free source is consulted FIRST. It is
NOT a pull from sac — it reads scitex-todo's own ``users:`` rows (no sac
import, no HTTP, no bearer); the endpoint fields are populated by explicit
registration and (later) by sac's ``agent_registered`` bus consumer, a
separate card.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

ENV_MAP = "SCITEX_TODO_AGENT_TURN_URLS"
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


def _slug(agent: str) -> str:
    """Match claude-code-telegrammer's env-slug convention."""
    return agent.upper().replace("-", "_").replace("/", "_")


def _turn_url_from_user_registry(agent: str) -> str | None:
    """Resolve ``agent``'s turn URL from scitex-todo's OWN user registry.

    File-local, NO-bearer PRIMARY source (step 0 of :func:`turn_url_for`):
    resolves ``agent`` to a :class:`scitex_todo._users.User` and returns its
    :func:`scitex_todo._users.user_turn_url` (explicit ``turn_url`` else one
    derived from ``a2a_port`` + the host in ``host_at_name``). NOT a sac
    pull — reads the ``users:`` store section; no sac import, no HTTP, no
    bearer (the endpoint is populated by registration + sac's
    ``agent_registered`` bus consumer, a separate card). ``_users`` is
    imported LAZILY to avoid an import cycle. Any failure → ``None`` so the
    caller falls through to the env / sac-registry chain (fail-loud ``None``
    preserved).
    """
    try:
        from ._users import resolve_user, user_turn_url

        user = resolve_user(agent)
        return user_turn_url(user) if user is not None else None
    except Exception as exc:  # noqa: BLE001 — registry must never break delivery
        logger.debug(
            "[scitex-todo._turn_url] user-registry lookup for %r failed: %s",
            agent, exc,
        )
        return None


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
        step (today, the file-local user registry is tried FIRST; this
        sac HTTP path is now a last-resort fallback).

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
            "User-Agent": "scitex-todo/_turn_url (registry-lookup)",
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
            "[scitex-todo._turn_url] sac registry %s unreachable: %s", url, e,
        )
        return None
    except json.JSONDecodeError as e:
        logger.debug(
            "[scitex-todo._turn_url] sac registry %s returned non-JSON: %s",
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

    Lookup order:
      0. scitex-todo's OWN user registry (``users:`` section) — file-local,
         NO bearer, reliable PRIMARY source. See
         :func:`_turn_url_from_user_registry`. (Root fix, card
         ``todo-push-turn-url-from-user-registry-20260626``.)
      1. ``SCITEX_TODO_AGENT_TURN_URLS`` JSON map entry (operator-pinned).
      2. ``SCITEX_TODO_TURN_URL_<SLUG>`` per-agent env (telegrammer wire).
      3. sac listen daemon's ``/agents`` HTTP registry — derive from the
         row's ``turn_url`` (preferred) or ``a2a_port``. See
         :func:`_turn_url_from_registry` (HTTP-only contract, locked by
         lead a2a `8e51b1e0` / `ffc6629c80`).
      4. None — caller falls through to fail-loud "no-turn-url-configured".

    Step 0 is the durable root fix: the board OWNS the registry, so that
    file-local, bearer-free source is consulted FIRST. The env steps stay
    operator-overrides; the sac HTTP registry remains a last-resort
    fallback. The final fail-loud ``None`` is unchanged.
    """
    registry_user_url = _turn_url_from_user_registry(agent)
    if registry_user_url:
        return registry_user_url
    raw = os.environ.get(ENV_MAP)
    if raw:
        try:
            mapping = json.loads(raw)
        except json.JSONDecodeError as exc:
            logger.error(
                "[scitex-todo._turn_url] %s is not valid JSON — ignoring: %s",
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


__all__ = [
    "ENV_MAP",
    "PER_AGENT_PREFIX",
    "ENV_SAC_LISTEN",
    "ENV_SAC_BEARER",
    "DEFAULT_SAC_LISTEN",
    "SAC_REGISTRY_TIMEOUT_S",
    "turn_url_for",
]

# EOF
