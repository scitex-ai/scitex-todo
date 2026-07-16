#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared fail-loud exception for fleet adapters.

Every fleet adapter (CI status, hosts, mesh, timing, chat, …) raises
:class:`FleetAdapterError` when its upstream is unreachable or returns
malformed data. The Django view catches it per-repo so a single dead
adapter does not blank the whole dashboard, but the error string is
surfaced verbatim to the front-end so the operator can see what broke.

A dedicated subclass (rather than reusing ``RuntimeError`` directly) lets
callers and tests pin behavior with ``pytest.raises(FleetAdapterError)``
without accidentally catching unrelated runtime errors from third-party
code (e.g. ``json.JSONDecodeError`` is a ``ValueError``, ``gh`` failures
go through ``subprocess.CalledProcessError`` — we normalize them all to
this one type at the adapter boundary).
"""

from __future__ import annotations


class FleetAdapterError(RuntimeError):
    """Raised by any fleet adapter when its upstream cannot be consulted.

    Examples that map to this exception:

    - ``gh`` binary not installed / not on PATH
    - ``gh api`` exits non-zero (auth, 404, rate limit, …)
    - upstream JSON cannot be parsed
    - the dashboard YAML config exists but is malformed

    Notes that do NOT map to this exception:

    - the config file is absent — that means "no repos configured", a
      valid steady state where the UI hides the pills strip
    - the watched-repo list is empty — same as above
    """


__all__ = ["FleetAdapterError"]

# EOF
