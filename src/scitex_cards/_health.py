#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Package-level HEALTH check for scitex-cards (the ``health`` doctor).

One PURE function, :func:`health`, aggregates a fixed set of store / identity /
delivery checks and returns a machine-readable report in the cross-package
standard shape shared with sac/cct::

    {
      "package": "scitex-cards",
      "ok": <bool: true iff EVERY check ok>,
      "checks": [ {"name", "ok", "detail", "hint"}, ... ],
      "summary": <str>,
    }

Contract
--------
* Every FAILING check carries an ACTIONABLE ``hint`` (the exact next step). A
  passing check may leave ``hint`` ``None``.
* :func:`health` NEVER raises: a check that errors internally is reported as
  ``ok=false`` with the error captured in its ``hint`` — no silent pass, no
  vague error, no exception out of the function.

Why this exists (0.7.32 incident)
---------------------------------
The unified ``mcp start`` server once starved its own ``initialize`` handshake
when the inbox poll loop ran blocking store IO inline on the event loop — every
fleet agent showed the ``scitex-cards`` server "not connected". The
``channel_drain`` check below (large unseen backlog with ``seen==0``) turns that
class of failure into a one-command diagnosis.

Testability
-----------
:func:`health` accepts explicit ``store`` and ``agent_id`` params so tests are
HERMETIC — a real ``tmp_path`` YAML store and a literal agent id, no dependence
on the process environment. The thin MCP / CLI wrappers pass ``None`` (resolve
from env).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from . import _inbox
from ._dual_write import check_mirror_healthy
from ._install_probe import check_install_honest
from ._mcp_channel import recipient_keys, resolve_agent_id

#: Unseen-notification backlog above which — combined with ``seen == 0`` (the
#: agent has NEVER drained) — the channel is judged stuck. A high unseen count
#: with any ``seen > 0`` is a working, merely-busy inbox, so it stays ``ok``.
UNSEEN_BACKLOG_THRESHOLD = 50

#: The exact drain-stuck remediation (kept verbatim per the cross-package spec).
_DRAIN_HINT = (
    "channel not draining — ensure `scitex-cards mcp start` is running for this "
    "agent with SCITEX_TODO_AGENT_ID set (needs >=0.7.32 where the poll loop no "
    "longer starves the handshake)"
)


# --------------------------------------------------------------------------- #
# Individual checks — each returns {ok, detail, hint}; may raise (wrapped).    #
# --------------------------------------------------------------------------- #
def _check_store_canonical(store: str | Path | None) -> dict[str, Any]:
    """Resolve the task store and verify it is the canonical, healthy store.

    ok when the store resolves to the canonical user/shared path (no
    project/cwd shadow), exists, is readable + writable, and parses as YAML
    with a top-level ``tasks`` key. Shadow detection only fires when the store
    is resolved via the precedence chain (``store is None``); an EXPLICIT store
    (tests, ``--tasks``) is taken as the intended target.
    """
    from ._paths import ENV_TASKS, bundled_example, _user_root, resolve_tasks_path
    from ._yaml import safe_load

    resolved = resolve_tasks_path(store)
    if store is None:
        env_tasks = os.environ.get(ENV_TASKS)
        canonical = (
            Path(env_tasks).expanduser() if env_tasks else _user_root() / "tasks.yaml"
        )
        if resolved == bundled_example():
            return {
                "ok": False,
                "detail": f"resolved to the bundled example store {resolved}",
                "hint": (
                    "no personal task store found — create "
                    f"{canonical} (add a task, or `mkdir -p {canonical.parent}` "
                    "with a `tasks: []` YAML), or set "
                    "SCITEX_TODO_TASKS_YAML_SHARED to your shared store"
                ),
            }
        if resolved != canonical:
            return {
                "ok": False,
                "detail": (
                    f"a project/cwd store {resolved} shadows the canonical "
                    f"store {canonical}"
                ),
                "hint": (
                    f"the project store {resolved} shadows the canonical "
                    f"{canonical} — set SCITEX_TODO_TASKS_YAML_SHARED={canonical}, "
                    "or run from a directory without a project .scitex/todo"
                ),
            }

    if not resolved.exists():
        return {
            "ok": False,
            "detail": f"store {resolved} does not exist",
            "hint": (
                f"create the store: `mkdir -p {resolved.parent}` and write a "
                f"`tasks: []` YAML at {resolved} (or add a task via scitex-cards)"
            ),
        }
    if not os.access(resolved, os.R_OK):
        return {
            "ok": False,
            "detail": f"store {resolved} is not readable",
            "hint": f"fix permissions so {resolved} is readable (e.g. chmod u+r)",
        }
    if not os.access(resolved, os.W_OK):
        return {
            "ok": False,
            "detail": f"store {resolved} is not writable",
            "hint": f"fix permissions so {resolved} is writable (e.g. chmod u+w)",
        }
    try:
        with resolved.open(encoding="utf-8") as handle:
            data = safe_load(handle) or {}
    except Exception as exc:  # noqa: BLE001 — a parse fail is a reportable state
        return {
            "ok": False,
            "detail": f"store {resolved} did not parse as YAML ({exc})",
            "hint": f"fix the YAML syntax in {resolved} ({type(exc).__name__}: {exc})",
        }
    if not isinstance(data, dict) or "tasks" not in data:
        return {
            "ok": False,
            "detail": f"store {resolved} has no top-level 'tasks' key",
            "hint": f"add a top-level `tasks:` list to {resolved}",
        }
    return {
        "ok": True,
        "detail": f"canonical store {resolved} (exists, readable, writable, parses)",
        "hint": None,
    }


def _check_agent_id(agent_id: str | None) -> dict[str, Any]:
    """Resolve the agent identity; fail on unset / 'unknown' / bare ``$VAR``."""
    try:
        resolved = resolve_agent_id(agent_id)
    except Exception as exc:  # noqa: BLE001 — unresolved id is a reportable state
        return {
            "ok": False,
            "detail": f"agent id unresolved ({exc})",
            "hint": (
                "set SCITEX_TODO_AGENT_ID=<your-agent-id> (not blank / 'unknown'); "
                'in .mcp.json use the brace form "${SCITEX_TODO_AGENT_ID}" — '
                "Claude Code does not expand bare $VAR"
            ),
        }
    return {"ok": True, "detail": f"agent id resolved: {resolved}", "hint": None}


def _check_notifyd_alive(store: str | Path | None) -> dict[str, Any]:
    """Check the notifyd delivery daemon via its pidfile (real liveness probe).

    The daemon stamps ``<store_dir>/runtime/notifyd.pid`` and holds an flock for
    its lifetime. We read the pid and probe it with ``os.kill(pid, 0)``. Absent
    pidfile ⇒ not running (actionable). A pid that no longer exists ⇒ stale
    pidfile (actionable). A genuinely undeterminable state degrades to ``ok``.
    """
    from ._delivery._daemon import pidfile_path

    pidfile = pidfile_path(store)
    start_hint = (
        "notifyd is not running — start it: `scitex-cards notifyd` (foreground), "
        "or install the systemd user unit: `scitex-cards notifyd install-unit`"
    )
    if not pidfile.exists():
        return {"ok": False, "detail": "no notifyd pidfile", "hint": start_hint}
    text = pidfile.read_text(encoding="utf-8").strip()
    try:
        pid = int(text.split()[0])
    except (ValueError, IndexError):
        # Can't determine — degrade gracefully rather than a false failure.
        return {
            "ok": True,
            "detail": f"notifyd pidfile {pidfile} is unparseable — could not "
            "determine liveness",
            "hint": None,
        }
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return {
            "ok": False,
            "detail": f"stale notifyd pidfile: pid {pid} is not running",
            "hint": start_hint,
        }
    except PermissionError:
        # Process exists but owned by another user — it IS alive.
        return {
            "ok": True,
            "detail": f"notifyd alive (pid {pid}, owned by another user)",
            "hint": None,
        }
    except OSError as exc:
        return {
            "ok": True,
            "detail": f"could not determine notifyd liveness ({exc})",
            "hint": None,
        }
    return {"ok": True, "detail": f"notifyd alive (pid {pid})", "hint": None}


def _check_channel_drain(
    agent_id: str | None, store: str | Path | None, threshold: int
) -> dict[str, Any]:
    """Report unseen vs seen inbox counts for THIS agent; flag a stuck drain."""
    if not agent_id:
        return {
            "ok": True,
            "detail": "agent id unresolved — channel-drain check skipped",
            "hint": None,
        }
    keys = recipient_keys(agent_id, store=store)
    unseen = 0
    total = 0
    for key in keys:
        unseen += len(
            _inbox.poll_inbox(key, unseen_only=True, mark_seen=False, store=store)
        )
        total += len(
            _inbox.poll_inbox(key, unseen_only=False, mark_seen=False, store=store)
        )
    seen = total - unseen
    detail = f"unseen={unseen} seen={seen} (keys={keys})"
    # Working (or merely busy) when the backlog is small OR anything was ever
    # drained. Stuck only when a large backlog has NEVER been drained.
    if unseen <= threshold or seen > 0:
        return {"ok": True, "detail": detail, "hint": None}
    return {"ok": False, "detail": detail, "hint": _DRAIN_HINT}


def _check_channel_capable() -> dict[str, Any]:
    """ok when ``scitex_cards._mcp_channel`` imports and exposes ``_serve``/``_run``."""
    try:
        from . import _mcp_channel as channel
    except Exception as exc:  # noqa: BLE001 — import failure is a reportable state
        return {
            "ok": False,
            "detail": f"import scitex_cards._mcp_channel failed ({exc})",
            "hint": (
                "upgrade to scitex-cards>=0.7.32: "
                "pip install -U 'scitex-cards[mcp]'"
            ),
        }
    missing = [attr for attr in ("_serve", "_run") if not hasattr(channel, attr)]
    if missing:
        return {
            "ok": False,
            "detail": f"scitex_cards._mcp_channel missing {missing}",
            "hint": (
                "upgrade to scitex-cards>=0.7.32 (the unified tools+channel "
                "server): pip install -U 'scitex-cards[mcp]'"
            ),
        }
    return {
        "ok": True,
        "detail": "scitex_cards._mcp_channel present (_serve/_run)",
        "hint": None,
    }


# --------------------------------------------------------------------------- #
# Aggregator                                                                  #
# --------------------------------------------------------------------------- #
def _run_check(name: str, fn: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Run one check, coercing its result to the standard record + never raising.

    A check that raises is reported as ``ok=false`` with the error in ``hint``
    (never propagated). A failing check with an empty hint gets a fallback hint
    so the "every failing check carries an actionable hint" rule always holds.
    """
    try:
        res = fn()
        ok = bool(res.get("ok"))
        detail = str(res.get("detail", ""))
        hint = res.get("hint")
    except Exception as exc:  # noqa: BLE001 — health must NEVER raise out
        ok = False
        detail = f"{name} check errored: {type(exc).__name__}: {exc}"
        hint = f"internal error in the {name} check: {exc}"
    if not ok and not hint:
        hint = f"{name} failed: {detail}"
    return {"name": name, "ok": ok, "detail": detail, "hint": hint}


def _soft_agent_id(agent_id: str | None) -> str | None:
    """Resolve the agent id, returning ``None`` instead of raising (for drain)."""
    try:
        return resolve_agent_id(agent_id)
    except Exception:  # noqa: BLE001 — absence is handled downstream
        return None


def health(
    *,
    store: str | Path | None = None,
    agent_id: str | None = None,
    unseen_threshold: int = UNSEEN_BACKLOG_THRESHOLD,
) -> dict[str, Any]:
    """Run every scitex-cards health check and return the standard report.

    Parameters
    ----------
    store : str | pathlib.Path | None
        Task-store override. ``None`` resolves via the package precedence chain
        (and enables project-shadow detection); an explicit path is taken as the
        intended store (hermetic tests, ``--tasks``).
    agent_id : str | None
        Agent identity override. ``None`` resolves ``$SCITEX_TODO_AGENT_ID``.
    unseen_threshold : int
        Unseen-backlog ceiling for :func:`_check_channel_drain`.

    Returns
    -------
    dict
        ``{"package", "ok", "checks", "summary"}`` — ``ok`` is true iff every
        check is ok. NEVER raises.
    """
    soft_agent = _soft_agent_id(agent_id)
    checks = [
        _run_check("store_canonical", lambda: _check_store_canonical(store)),
        _run_check("agent_id", lambda: _check_agent_id(agent_id)),
        _run_check("notifyd_alive", lambda: _check_notifyd_alive(store)),
        _run_check(
            "channel_drain",
            lambda: _check_channel_drain(soft_agent, store, unseen_threshold),
        ),
        _run_check("channel_capable", _check_channel_capable),
        # Is our own reported version actually TRUE? An orphaned/stale .dist-info
        # reports a version that outlived the code it describes — and the fleet's
        # drift detector reads exactly that string, so a fossil silently turns the
        # detector off. Verified BY CONTENT, never by the version alone.
        # (Incident 2026-07-12: metadata said 0.7.26 while the code ran 0.8.7.)
        _run_check("install_honest", check_install_honest),
        # S1 DUAL-WRITE: has the SQLite mirror stayed in sync with the canonical
        # YAML? A mirror that fails SILENTLY lets the DB rot out of sync while
        # every other check reports green — and S2 would then cut the fleet over
        # to a store that is confidently wrong. One failure is enough to fail this
        # check: there is no partial credit for a store that is only mostly right.
        _run_check("dual_write_mirror", check_mirror_healthy),
    ]
    ok = all(c["ok"] for c in checks)
    n_ok = sum(1 for c in checks if c["ok"])
    failing = [c["name"] for c in checks if not c["ok"]]
    summary = f"{n_ok}/{len(checks)} checks passed"
    if failing:
        summary += "; failing: " + ", ".join(failing)
    return {
        "package": "scitex-cards",
        "ok": ok,
        "checks": checks,
        "summary": summary,
    }


__all__ = ["UNSEEN_BACKLOG_THRESHOLD", "health"]

# EOF
