#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Process runner + status for the listen server.

* :func:`run_server` — resolve the bearer token, take a NON-BLOCKING flock
  single-instance lock on ``<runtime>/listen.pid``, build the app, and hand it
  to ``uvicorn.run`` (which blocks until SIGTERM/SIGINT). The lock is released
  + the pidfile removed on every exit path.
* :func:`server_status` — report pidfile / PID-liveness / port-bound / HTTP
  ``/v1/health`` reachability for ``scitex-todo listen status``.

Single-instance reuses the SAME flock primitive the notifyd daemon uses
(:class:`scitex_todo._delivery._daemon._SingleInstanceLock`) so behaviour is
identical — no second lock implementation to keep in sync. The listen server
gets its OWN pidfile (``listen.pid``) distinct from ``notifyd.pid`` so the two
processes' single-instance guards are independent.
"""

from __future__ import annotations

import logging
import os
import socket
from pathlib import Path

logger = logging.getLogger(__name__)

#: Default loopback bind host — the door is host-local by default.
DEFAULT_HOST = "127.0.0.1"

#: Default port. Env ``SCITEX_TODO_LISTEN_PORT`` overrides. Deliberately
#: distinct from sac's 7878 and the board's 8051 — three independent services.
DEFAULT_PORT = 7979

#: Env var overriding the default bind port.
ENV_LISTEN_PORT = "SCITEX_TODO_LISTEN_PORT"

#: Pidfile name (sibling of notifyd.pid under the store runtime dir).
PIDFILE_NAME = "listen.pid"


def default_port() -> int:
    """Resolve the bind port: ``$SCITEX_TODO_LISTEN_PORT`` → :data:`DEFAULT_PORT`."""
    raw = os.environ.get(ENV_LISTEN_PORT)
    if raw is None:
        return DEFAULT_PORT
    try:
        return int(raw)
    except (TypeError, ValueError):
        return DEFAULT_PORT


def listen_pidfile_path(store: "str | Path | None" = None) -> Path:
    """Resolve ``<store runtime>/listen.pid`` for ``store``."""
    from .._paths import runtime_dir

    return runtime_dir(store) / PIDFILE_NAME


def run_server(
    *,
    host: str = DEFAULT_HOST,
    port: "int | None" = None,
    token: "str | None" = None,
    token_file: "str | Path | None" = None,
    store: "str | Path | None" = None,
    allow_non_loopback: bool = False,
    run_delivery_loop: bool = True,
    interval: float = 120.0,
) -> None:
    """Serve the listen app (blocking) under a single-instance lock.

    Refuses a non-loopback ``host`` unless ``allow_non_loopback`` is set
    (fail-loud: binding the notify door to a public interface must be a
    DELIBERATE opt-in, never an accident). Resolves the bearer token
    (:func:`scitex_todo._listen.tokens.ensure_token`) unless one is passed
    explicitly, takes the ``listen.pid`` flock, then ``uvicorn.run``.
    """
    from ._server import create_app
    from .tokens import ensure_token

    if host not in ("127.0.0.1", "::1", "localhost") and not allow_non_loopback:
        raise RuntimeError(
            f"refusing to bind the listen door to non-loopback host {host!r} "
            f"without allow_non_loopback=True — the notify door is loopback-"
            f"only by default (pass --allow-non-loopback to override)."
        )

    resolved_port = port if port is not None else default_port()
    resolved_token = token if token else ensure_token(token_file, store=store)

    # Single-instance: reuse the notifyd flock primitive with our OWN pidfile.
    from .._delivery._daemon import DaemonAlreadyRunning, _SingleInstanceLock

    lock = _SingleInstanceLock(listen_pidfile_path(store))
    try:
        lock.acquire()
    except DaemonAlreadyRunning as exc:
        raise RuntimeError(str(exc)) from exc

    app = create_app(
        token=resolved_token,
        store=store,
        run_delivery_loop=run_delivery_loop,
        interval=interval,
    )
    logger.info(
        "scitex-todo listen starting: pid=%d bind=%s:%d pidfile=%s",
        os.getpid(),
        host,
        resolved_port,
        listen_pidfile_path(store),
    )
    try:
        import uvicorn

        uvicorn.run(app, host=host, port=resolved_port, log_level="info", ws="none")
    finally:
        lock.release()


def _pid_alive(pid: int) -> bool:
    """True if a process with ``pid`` currently exists (signal 0 probe)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


def _read_pidfile(path: Path) -> "int | None":
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None


def _port_bound(host: str, port: int) -> bool:
    """True if a TCP connect to ``host:port`` succeeds (something is bound)."""
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return True
    except OSError:
        return False


def server_status(
    *,
    host: str = DEFAULT_HOST,
    port: "int | None" = None,
    store: "str | Path | None" = None,
) -> dict:
    """Report listen-server liveness for ``scitex-todo listen status``.

    Returns ``{pidfile, pid, pid_alive, port, port_bound, health_ok}`` —
    ``health_ok`` is a real ``GET /v1/health`` round-trip (the public probe),
    ``None`` when the port isn't bound (nothing to poll).
    """
    resolved_port = port if port is not None else default_port()
    pidfile = listen_pidfile_path(store)
    pid = _read_pidfile(pidfile)
    bound = _port_bound(host, resolved_port)
    health_ok: "bool | None" = None
    if bound:
        health_ok = _health_probe(host, resolved_port)
    return {
        "pidfile": str(pidfile),
        "pid": pid,
        "pid_alive": _pid_alive(pid) if pid is not None else False,
        "port": resolved_port,
        "port_bound": bound,
        "health_ok": health_ok,
    }


def _health_probe(host: str, port: int) -> bool:
    """GET /v1/health via stdlib urllib; True on a 200 with ``ok: true``."""
    import json
    import urllib.request

    url = f"http://{host}:{port}/v1/health"
    try:
        with urllib.request.urlopen(url, timeout=2.0) as resp:  # noqa: S310 loopback
            if resp.status != 200:
                return False
            data = json.loads(resp.read().decode("utf-8"))
        return bool(data.get("ok"))
    except Exception:  # noqa: BLE001 — any failure = not healthy
        return False


__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "ENV_LISTEN_PORT",
    "PIDFILE_NAME",
    "default_port",
    "listen_pidfile_path",
    "run_server",
    "server_status",
]

# EOF
