#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""scitex-todo's own persistent listen server — a first-class peer channel.

scitex-todo is one of three SYMMETRIC peer channels (sac /
claude-code-telegrammer / scitex-todo); each owns an independent, always-on
service and none reaches into another's internals. This package is
scitex-todo's equivalent of sac's ``sac listen`` daemon: a small Starlette +
uvicorn HTTP server that

* exposes a PUBLIC ``GET /v1/health`` liveness probe (no auth), and
* exposes a bearer-gated ``POST /v1/notify`` generic intake — any external
  channel can push "notify this user" into scitex-todo's own pull-inbox
  through the loopback door, exactly the shape sac's ``/v1/notify`` uses; and
* runs the EXISTING delivery + reminder loop (:func:`scitex_todo._delivery
  ._daemon.run_notifyd`) as a background task in its lifespan, so the one
  process both receives pushes AND drives outbound delivery.

Architecture note (mirror, not import): the SHAPE mirrors sac deliberately
(Starlette + uvicorn, ``/v1/health`` public + ``/v1/notify`` bearer, a flock
single-instance pidfile, a lifespan that launches background loops) but this
package has ZERO dependency on — and zero knowledge of — sac. It reuses only
scitex-todo's own primitives (:mod:`scitex_todo._inbox`,
:mod:`scitex_todo._delivery`). The server deps (starlette, uvicorn) live in
the optional ``[listen]`` extra so the package core stays minimal.
"""

from __future__ import annotations

from ._run import DEFAULT_HOST, DEFAULT_PORT, listen_pidfile_path, run_server
from ._server import create_app
from .tokens import default_token_path, ensure_token, read_token

__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_PORT",
    "create_app",
    "default_token_path",
    "ensure_token",
    "listen_pidfile_path",
    "read_token",
    "run_server",
]

# EOF
