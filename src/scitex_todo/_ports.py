#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Extension ports — the four interfaces that let `scitex-todo` stay
standalone while fleet-specific behaviour plugs in.

Architectural backbone per operator TG 9678 + lead a2a `fae53b8e`:
`scitex-todo` IS a standalone package that knows nothing about
`sac`, `a2a`, SSH-fanout, the 6-stream fleet — but exposes EXTENSION
PORTS through which that fleet-specific behaviour can plug in. Clean
architecture / dependency-inversion.

Three layers:

```
┌──────────────────────────────────────────────────────────────────┐
│  FLEET ADAPTERS — implement these ports against sac / SSH / a2a /│
│  git. Live OUTSIDE this package (e.g. `scitex-todo-fleet`).      │
└──────────────────────────────────────────────────────────────────┘
                                ↑ implements
┌──────────────────────────────────────────────────────────────────┐
│  EXTENSION PORTS (this module) — `typing.Protocol` interfaces.   │
└──────────────────────────────────────────────────────────────────┘
                                ↑ used-by
┌──────────────────────────────────────────────────────────────────┐
│  CORE — the rest of `scitex_todo`. ZERO knowledge of fleet/sac.  │
│  Consumes ports via constructor injection on `create_board(...)`.│
└──────────────────────────────────────────────────────────────────┘
```

The core ships with default no-op / single-host implementations in
:mod:`scitex_todo._adapters` (LocalFileSync, InProcessPubSub,
NullLiveness, OpenACL) so :command:`pip install scitex-todo` is
independently usable. Fleet deployments inject the real adapters.

See ADR-0006 in ``docs/adr/`` for the full design rationale,
deployment wiring examples, and the lead-approved Consequences.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Protocol, runtime_checkable

if TYPE_CHECKING:
    # Import-time circular avoidance — Task is the shared payload type;
    # we only need it for static typing here.
    pass


# ---------------------------------------------------------------------------
# TaskSyncPort — where does the durable data live, and how does it sync?
# ---------------------------------------------------------------------------


@runtime_checkable
class TaskSyncPort(Protocol):
    """Durable storage + cross-host sync of the task store.

    The core writes through this port; the adapter decides what
    "durable" means and how (if at all) state propagates across hosts.

    **Default impl**: :class:`scitex_todo._adapters.LocalFileSync` —
    atomic ruamel write to ``~/.scitex/todo/tasks.yaml``, no cross-host
    awareness. A single-user installing ``scitex-todo`` gets a working
    local board with this default.

    **Fleet impl** (lives outside this package, e.g.
    ``scitex_todo_fleet.GitTaskSyncAdapter``): write → git commit → push
    to GitHub. Other hosts pull. Cross-host pull is a side-loop in the
    adapter; the core never sees it.

    The adapter is free to add caching / batching / mtime-fingerprinting;
    the core only requires that ``load()`` and ``save()`` are atomic and
    that ``reload_if_changed()`` returns True when the underlying store
    has been mutated since the last ``load()``.
    """

    def load(self) -> list[dict]:
        """Return the current task list (oldest-first document order).

        Each task is a plain dict matching the schema validated by
        :func:`scitex_todo._model.load_tasks`. The adapter MAY enforce
        additional validation; the core's :class:`Task` dataclass is the
        canonical shape.
        """
        ...

    def save(self, tasks: list[dict]) -> None:
        """Persist the (already-mutated) task list atomically.

        MUST round-trip preserve any hand-written YAML comments and key
        order in the existing store (ruamel-style). MUST be safe to call
        concurrently with another writer on the same host (file-lock or
        equivalent).
        """
        ...

    def reload_if_changed(self) -> bool:
        """Detect external mutations; return True if the store changed.

        Used by the board's AutoRefresh poll loop. The default impl
        compares mtime against the last-loaded snapshot; fleet impls MAY
        consult a sidecar `/agents-rev` style fingerprint endpoint.
        """
        ...


# ---------------------------------------------------------------------------
# NotificationPort — pub/sub for "task changed" events
# ---------------------------------------------------------------------------


@runtime_checkable
class NotificationPort(Protocol):
    """Publish + subscribe to task-change events.

    The core publishes on every mutation (status flip, blocker change,
    comment append, …). Subscribers are decoupled from the core via the
    channel name string; the adapter routes the event to the right
    place.

    **Default impl**: :class:`scitex_todo._adapters.InProcessPubSub` —
    a simple in-process callback registry. Fine for a single-host
    standalone installation.

    **Fleet impl** (e.g. ``scitex_todo_fleet.SacChannelNotificationAdapter``):
    publishes on ``scitex-todo:task:<project>/<local-id>`` over the sac
    a2a/channel bus. The wake-generalize + empty-beacon-fix work in
    ``scitex-agent-container`` makes this reliable for waking idle
    agent subscribers — every task update can wake the relevant agent
    (HANDOFF.md north-star pillar #4 synergy).

    Channel naming convention (recommended, not enforced):

    .. code-block:: text

        scitex-todo:task:<project>/<local-id>     — a specific task changed
        scitex-todo:task:<project>/*              — any task in a project
        scitex-todo:task:*                        — every task (UI firehose)
    """

    def publish(self, channel: str, payload: dict[str, Any]) -> None:
        """Publish an event on the named channel.

        Payload shape (convention):

        .. code-block:: python

            {
                "task_id": "<project>/<local-id>",
                "changes": {<field>: <new-value>, ...},
                "ts": "<ISO-8601 UTC>",
                "actor": "<agent or operator name>",
            }
        """
        ...

    def subscribe(
        self, channel: str, handler: Callable[[dict[str, Any]], None]
    ) -> None:
        """Register ``handler`` to be invoked with the payload each time
        an event is published on ``channel`` (or any channel matching the
        adapter's glob semantics; the default impl supports literal
        match + suffix ``*`` glob).

        Implementations SHOULD be idempotent on duplicate subscription
        (same handler + same channel registered twice = single delivery
        per event).
        """
        ...


# ---------------------------------------------------------------------------
# LivenessPort — fleet agent status feed for the liveness panel
# ---------------------------------------------------------------------------


@runtime_checkable
class LivenessPort(Protocol):
    """Surface "are these agents alive?" state for the FE liveness panel.

    The core renders the colored dots / Fleet tab from this output; HOW
    the state is gathered is the adapter's problem.

    **Default impl**: :class:`scitex_todo._adapters.NullLiveness` —
    returns ``[]``. The FE renders an empty Fleet tab + a "watcher not
    installed" hint. Acceptable for standalone single-user installs.

    **Fleet impl** (e.g.
    ``scitex_todo_fleet.SacAgentsLivenessAdapter``): SSH-fanout per
    fleet-liveness task's ADR-0002 to ``sac agents list --json`` on each
    peer host, aggregated into one return value with UNREACHABLE markers
    for peers that fail (per the fail-loud principle from ADR-0005).
    """

    def list_agents(self) -> list[dict[str, Any]]:
        """Return one entry per agent the adapter knows about.

        Entry shape (convention, the FE depends on these keys):

        .. code-block:: python

            {
                "name": "proj-scitex-todo",
                "host": "ywata-note-win",
                "status": "running" | "idle" | "working" | "stopped" | "unreachable",
                "heartbeat": "<ISO-8601 UTC of last heartbeat>",
                "current_task": "<one-line summary>" | None,
                "last_activity": "<relative string, e.g. '12s ago'>",
                "context_pct": <0-100 int or None>,
                "quota_pct": <0-100 int or None>,
                "as_of": "<ISO-8601 UTC of when this row was sampled>",
                "error": <only on status='unreachable'; the SSH/transport error>,
            }
        """
        ...


# ---------------------------------------------------------------------------
# IdentityACLPort — "can ACTOR perform ACTION on TASK?"
# ---------------------------------------------------------------------------


@runtime_checkable
class IdentityACLPort(Protocol):
    """Answer access-control queries before any write.

    The core consults this on every mutation; the adapter decides
    authority. The "actor" string is whatever identity scheme the
    adapter uses (an agent name, an OS username, a Gitea user, etc.).

    **Default impl**: :class:`scitex_todo._adapters.OpenACL` — everyone
    can do everything. Acceptable for single-user installs.

    **Fleet impl** (e.g.
    ``scitex_todo_fleet.SacFleetGroupsACLAdapter``): consults
    ``sac fleet groups list --json`` plus a future per-task
    ``acl: {read: [<groups>], write: [<groups>]}`` field. Wires to
    task #2 (``e1-sac-fleet-acl``) when that lands. Until then, the
    fleet runs with OpenACL too — same default as standalone.
    """

    def can_read(self, actor: str, task: dict) -> bool:
        """True iff ``actor`` may read ``task``."""
        ...

    def can_write(self, actor: str, task: dict, field: str) -> bool:
        """True iff ``actor`` may write ``field`` on ``task``."""
        ...


__all__ = [
    "TaskSyncPort",
    "NotificationPort",
    "LivenessPort",
    "IdentityACLPort",
]
