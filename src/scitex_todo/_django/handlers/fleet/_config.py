#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dashboard configuration loader.

The fleet dashboard reads its watched-repo list from
``~/.scitex/todo/dashboard.yaml`` under the key path
``fleet.ci_status.repos`` (a list of ``owner/name`` GitHub slugs). The
env var ``SCITEX_TODO_FLEET_CI_REPOS=slug1,slug2`` is the override hook
(handy for CI tests and for the operator to flip the set without editing
a file).

Architectural principles in play:

- NO hardcoded proper nouns. There is no
  ``["scitex-todo","scitex-dev",…]`` literal in this module — the
  config is the only source of truth.
- Absence is NOT an error. A fresh install has no dashboard.yaml; that
  means "no repos configured" and the UI hides the pills strip gracefully.
- Malformed YAML IS an error — fail-loud per the harness contract so
  the operator does not stare at a blank strip wondering why their
  config is being ignored.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ._errors import FleetAdapterError

# Canonical config path. Kept here as a module-level constant so tests
# can monkeypatch ``HOME`` and the function picks it up via
# ``Path.home()`` without having to redefine the literal.
_CONFIG_REL = Path(".scitex") / "todo" / "dashboard.yaml"

# Env override — comma-separated slugs. Whitespace around commas is
# tolerated (operator may copy-paste from a shell history).
_ENV_REPOS = "SCITEX_TODO_FLEET_CI_REPOS"


def _config_path() -> Path:
    """Resolve the canonical config path. Pure function — testable."""
    return Path.home() / _CONFIG_REL


def _split_env(raw: str) -> list[str]:
    """Parse the env-override string into a clean slug list.

    Empty entries (trailing/leading commas, double commas) are dropped
    so the operator can be sloppy.
    """
    return [s.strip() for s in raw.split(",") if s.strip()]


def _load_yaml(path: Path) -> dict:
    """Parse the YAML at ``path``; raise :class:`FleetAdapterError` on
    a broken file (caller already checked the file exists)."""
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover — yaml is a hard dep
        raise FleetAdapterError(
            f"PyYAML is required to load {path}: {exc}"
        ) from exc

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FleetAdapterError(
            f"failed to read dashboard config {path}: {exc}"
        ) from exc

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise FleetAdapterError(
            f"malformed YAML in dashboard config {path}: {exc}"
        ) from exc

    if data is None:
        # Empty file is the same as no file — "no repos configured".
        return {}
    if not isinstance(data, dict):
        raise FleetAdapterError(
            f"dashboard config {path} must be a mapping at the top level, "
            f"got {type(data).__name__}"
        )
    return data


def fleet_config_load() -> dict[str, Any]:
    """Return the dashboard config as a nested ``dict``.

    Resolution order (later overrides earlier):

    1. ``~/.scitex/todo/dashboard.yaml`` if present (otherwise empty
       config, NOT an error)
    2. ``SCITEX_TODO_FLEET_CI_REPOS`` env var — when set, replaces
       ``fleet.ci_status.repos`` regardless of file contents

    The returned shape is always normalized to::

        {"fleet": {"ci_status": {"repos": [<slug>, ...]}}}

    so the view code can read it without defensive ``.get()`` chains.
    """
    path = _config_path()
    if path.is_file():
        data = _load_yaml(path)
    else:
        data = {}

    # Normalize the nested shape.
    fleet = data.get("fleet") if isinstance(data.get("fleet"), dict) else {}
    ci = fleet.get("ci_status") if isinstance(fleet.get("ci_status"), dict) else {}
    repos_raw = ci.get("repos") if isinstance(ci.get("repos"), list) else []
    repos = [str(r) for r in repos_raw if isinstance(r, str) and r.strip()]

    env_override = os.environ.get(_ENV_REPOS)
    if env_override is not None:
        repos = _split_env(env_override)

    return {"fleet": {"ci_status": {"repos": repos}}}


__all__ = ["fleet_config_load"]

# EOF
