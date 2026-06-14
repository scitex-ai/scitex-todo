#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scitex-todo wake-watcher — push side of the self-consuming board loop.

Polls the YAML store on a fixed interval (default 2s — matches the
existing :mod:`AutoRefresh.tsx` ``/rev`` poll cadence), diffs against
the previous snapshot, and POSTs to the owning agent's local sac a2a
``/v1/turn`` endpoint when:

  * a NEW task is added (``task.id`` absent from the previous snapshot);
  * a comment is appended (``len(task.comments)`` grew);
  * status flipped (``previous.status != current.status``).

Wake payload is small + stable so a future Gitea-webhook variant can
emit the same shape::

    {
      "trigger":      "scitex-todo-watcher",
      "trigger_kind": "task_added" | "comment" | "status_changed",
      "task_id":      "<id>",
      "task_title":   "<title>",
      "summary":      "<short human-readable change description>",
      "store_path":   "/scitex-todo/tasks.yaml"
    }

The agent's harness (e.g. claude-code container) handles the rest:
runs ``scitex-todo next --mine --json``, picks the top task, works it,
flips status, comments. See ``_skills/scitex-todo/32_agent-self-
consumption-loop.md`` for the canonical 7-step loop.

Agent registry (where to find each peer's a2a port):

  (iii) PRIMARY  — auto-discover via the ``sac a2a_peers`` MCP /
        local registry. The watcher imports lazily so a missing sac
        does not crash the loop.
  (i)  FALLBACK — a ``agents:`` top-level list in tasks.yaml. Each
        entry: ``{name: proj-scitex-todo, a2a_port: 41234}``. Used
        when the sac peer table is unreachable.

Per-agent debounce: at most ONE wake per ``min_wake_interval`` seconds
per agent. Prevents a hot-loop when an agent comments on its own task
several times in quick succession.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_S: float = 2.0
DEFAULT_MIN_WAKE_INTERVAL_S: float = 30.0
DEFAULT_REQUEST_TIMEOUT_S: float = 1.5
WAKE_PATH: str = "/v1/turn"


@dataclass
class WakeRecord:
    """A single wake event the watcher decided to fire (or DID fire).

    Surfaced from :func:`detect_changes` so the caller (the polling
    loop or a test) can decide what to do — tests just collect the
    records; the live loop forwards them to :func:`post_wake`.
    """

    agent: str
    trigger_kind: str  # task_added | comment | status_changed
    task_id: str
    task_title: str
    summary: str

    def to_payload(self, *, store_path: str) -> dict:
        return {
            "trigger": "scitex-todo-watcher",
            "trigger_kind": self.trigger_kind,
            "task_id": self.task_id,
            "task_title": self.task_title,
            "summary": self.summary,
            "store_path": store_path,
        }


@dataclass
class WatcherState:
    """In-memory snapshot the polling loop diffs against.

    Stores the previous-pass task list keyed by id so the diff is O(N)
    each tick. Reset on first load (the first tick of any watcher run
    seeds the snapshot without firing any wakes — otherwise the
    operator would get a deluge of "everything looks new" wakes at
    startup).
    """

    snapshot: dict[str, dict] = field(default_factory=dict)
    seeded: bool = False
    # Per-agent debounce: agent name -> last-wake timestamp.
    last_wake_at: dict[str, float] = field(default_factory=dict)


def detect_changes(
    state: WatcherState,
    tasks: Iterable[dict],
    *,
    now: Optional[float] = None,
    min_wake_interval_s: float = DEFAULT_MIN_WAKE_INTERVAL_S,
) -> list[WakeRecord]:
    """Diff `tasks` against the watcher's previous snapshot.

    Updates ``state.snapshot`` in place. On the first call ``state``
    is just SEEDED (no wakes returned) — the watcher only fires on
    transitions, not on its own bootup.
    """
    now = time.monotonic() if now is None else now
    new_snapshot: dict[str, dict] = {}
    wakes: list[WakeRecord] = []

    for task in tasks:
        if not isinstance(task, dict):
            continue
        tid = task.get("id")
        if not tid:
            continue
        new_snapshot[tid] = task

        if not state.seeded:
            continue  # first pass — seed only

        prev = state.snapshot.get(tid)
        agent = task.get("agent") or task.get("assignee")
        if not agent:
            continue  # unassigned tasks have nowhere to wake to

        # task added
        if prev is None:
            wakes.append(
                WakeRecord(
                    agent=agent,
                    trigger_kind="task_added",
                    task_id=str(tid),
                    task_title=str(task.get("title") or ""),
                    summary=f"new task assigned to {agent}",
                )
            )
            continue

        # comment appended
        prev_comments = prev.get("comments") or []
        cur_comments = task.get("comments") or []
        if len(cur_comments) > len(prev_comments):
            latest = cur_comments[-1] if cur_comments else {}
            author = (
                latest.get("author") if isinstance(latest, dict) else None
            ) or "<unknown>"
            text = (latest.get("text") if isinstance(latest, dict) else None) or ""
            short = text[:120].replace("\n", " ")
            wakes.append(
                WakeRecord(
                    agent=agent,
                    trigger_kind="comment",
                    task_id=str(tid),
                    task_title=str(task.get("title") or ""),
                    summary=f"comment by {author}: {short}",
                )
            )

        # status flipped
        if prev.get("status") != task.get("status"):
            wakes.append(
                WakeRecord(
                    agent=agent,
                    trigger_kind="status_changed",
                    task_id=str(tid),
                    task_title=str(task.get("title") or ""),
                    summary=(
                        f"status: {prev.get('status')!r} -> "
                        f"{task.get('status')!r}"
                    ),
                )
            )

    state.snapshot = new_snapshot
    state.seeded = True

    # Per-agent debounce — drop wakes that fire within min_wake_interval
    # of a prior wake for the same agent.
    kept: list[WakeRecord] = []
    for w in wakes:
        last = state.last_wake_at.get(w.agent, 0.0)
        if now - last < min_wake_interval_s:
            logger.debug(
                "wake-watcher: debounced %s on %s (last %.1fs ago)",
                w.trigger_kind, w.agent, now - last,
            )
            continue
        kept.append(w)
        state.last_wake_at[w.agent] = now
    return kept


def post_wake(
    a2a_port: int,
    payload: dict,
    *,
    timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S,
) -> bool:
    """POST a wake payload to ``http://127.0.0.1:<port>/v1/turn``.

    Returns ``True`` on a 2xx response, ``False`` on any failure
    (connection refused / non-2xx / timeout). The watcher logs + skips
    on failure; the next polling tick re-derives the diff so the wake
    is NOT queued.
    """
    url = f"http://127.0.0.1:{a2a_port}{WAKE_PATH}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError) as exc:
        logger.info("wake-watcher: POST to %s failed: %s", url, exc)
        return False


def resolve_agent_port(
    agent: str,
    *,
    static_agents: Optional[list[dict]] = None,
) -> Optional[int]:
    """Resolve an agent's a2a port via (iii) sac auto-discover, then (i)
    a static ``agents:`` list passed in from the watcher.

    Returns the port int, or ``None`` if the agent is unknown.
    """
    # (iii) sac auto-discover — lazy import so a missing sac doesn't
    # crash the watcher loop. The sac MCP / table exposes ``a2a_peers``
    # mapping agent name -> port; if the API surface changes the
    # try / except keeps the watcher running.
    try:  # pragma: no cover - relies on optional dep
        from sac import a2a_peers  # type: ignore[import-not-found]

        for peer in a2a_peers() or []:
            if peer.get("name") == agent:
                port = peer.get("a2a_port") or peer.get("port")
                if isinstance(port, int):
                    return port
    except Exception:  # pragma: no cover - sac not installed / network err
        logger.debug("wake-watcher: sac peer lookup unavailable", exc_info=True)

    # (i) static fallback — pass an ``agents:`` list from tasks.yaml.
    if static_agents:
        for entry in static_agents:
            if not isinstance(entry, dict):
                continue
            if entry.get("name") == agent:
                port = entry.get("a2a_port")
                if isinstance(port, int):
                    return port
    return None


def run_watcher_once(
    path: str | Path,
    state: WatcherState,
    *,
    now: Optional[float] = None,
    min_wake_interval_s: float = DEFAULT_MIN_WAKE_INTERVAL_S,
    post: bool = True,
) -> list[WakeRecord]:
    """Single tick: load the store, detect changes, optionally POST wakes.

    Test-friendly entry point — tests can call this with `post=False`
    to assert the wake list without standing up a real HTTP server.

    Returns the list of wakes that ACTUALLY would have fired this tick
    (post-debounce). When ``post=True``, also forwards them to
    :func:`post_wake` — the return list is the same regardless.
    """
    # Lazy import: keep watcher importable without the rest of the
    # package's heavy YAML / Django modules in scope.
    from scitex_todo._model import load_tasks

    path = Path(path).expanduser()
    try:
        tasks = load_tasks(path)
    except Exception as exc:  # pragma: no cover - validation drift
        logger.warning("wake-watcher: load failed, skip tick: %s", exc)
        return []

    wakes = detect_changes(
        state,
        tasks,
        now=now,
        min_wake_interval_s=min_wake_interval_s,
    )
    if post:
        # Static agents fallback — read once per tick so the file
        # stays the SSoT for both task data + agent registry.
        static_agents = _load_static_agents(path)
        for w in wakes:
            port = resolve_agent_port(w.agent, static_agents=static_agents)
            if port is None:
                logger.info(
                    "wake-watcher: no port for %s, skip wake (%s on %s)",
                    w.agent, w.trigger_kind, w.task_id,
                )
                continue
            post_wake(port, w.to_payload(store_path=str(path)))
    return wakes


def _load_static_agents(path: Path) -> list[dict]:
    """Read the top-level ``agents:`` list from the YAML store, if any.

    Lazy + tolerant — a missing key returns ``[]``; a malformed file
    is swallowed so the watcher keeps running.
    """
    try:
        import yaml

        with path.open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        raw = data.get("agents") or []
        return [a for a in raw if isinstance(a, dict)]
    except Exception:  # pragma: no cover - defensive
        return []


def run_watcher_forever(
    path: str | Path,
    *,
    interval_s: float = DEFAULT_INTERVAL_S,
    min_wake_interval_s: float = DEFAULT_MIN_WAKE_INTERVAL_S,
) -> None:  # pragma: no cover - infinite loop
    """Run the watcher in a polling loop until interrupted.

    Drives the live ``scitex-todo watch --push`` CLI entry. Tests use
    :func:`run_watcher_once` directly.
    """
    state = WatcherState()
    while True:
        run_watcher_once(
            path, state, min_wake_interval_s=min_wake_interval_s
        )
        time.sleep(interval_s)


# EOF
