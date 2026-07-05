#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bearer-token auth middleware for the listen server.

Mirrors sac's ``BearerAuthMiddleware`` shape: a Starlette
``BaseHTTPMiddleware`` that requires ``Authorization: Bearer <token>`` on every
request EXCEPT the public liveness paths. The token check is a constant-time
:func:`hmac.compare_digest` so a wrong token cannot be timing-probed.

Responses on failure are deliberate + fail-loud:

* missing / malformed ``Authorization`` header → ``401`` ``{"error": "..."}``
* present but wrong token                       → ``403`` ``{"error": "..."}``

Only :data:`PUBLIC_PATHS` bypass the check (the health probe must answer an
un-authenticated liveness poll — same as sac's ``/v1/health``).
"""

from __future__ import annotations

import hmac

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

#: Paths reachable WITHOUT a bearer token (liveness only — never a mutation).
PUBLIC_PATHS: frozenset[str] = frozenset({"/v1/health"})

_BEARER_PREFIX = "Bearer "


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Require ``Authorization: Bearer <token>`` on all but the public paths.

    The expected token is supplied at construction (``token=``). An empty /
    ``None`` expected token is a configuration error — every gated request is
    rejected ``503`` rather than silently allowing all (fail-loud, no silent
    open door).
    """

    def __init__(self, app, *, token: "str | None"):
        super().__init__(app)
        self._token = token or ""

    async def dispatch(self, request: Request, call_next):
        if request.url.path in PUBLIC_PATHS:
            return await call_next(request)

        if not self._token:
            # No configured secret → refuse rather than allow-all. This should
            # never happen (the runner always ensures a token), but a missing
            # secret must fail CLOSED, loudly.
            return JSONResponse(
                {"error": "listen server has no configured bearer token"},
                status_code=503,
            )

        header = request.headers.get("authorization") or ""
        if not header.startswith(_BEARER_PREFIX):
            return JSONResponse(
                {"error": "missing bearer token"}, status_code=401
            )
        presented = header[len(_BEARER_PREFIX):].strip()
        if not hmac.compare_digest(presented, self._token):
            return JSONResponse(
                {"error": "invalid bearer token"}, status_code=403
            )
        return await call_next(request)


__all__ = ["BearerAuthMiddleware", "PUBLIC_PATHS"]

# EOF
