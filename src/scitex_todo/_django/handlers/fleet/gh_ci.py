#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GitHub CI status adapter for the fleet dashboard.

For a given ``owner/name`` slug, returns a small JSON-friendly summary of
the latest CI run on the repo's default branch by shelling out to
``gh`` (the same binary the operator uses interactively, so auth comes
for free via ``gh auth login``).

Why ``gh`` and not the REST API directly?

- ``gh`` already handles auth (the operator has it configured)
- It transparently uses the user's preferred host (github.com vs. GHES)
- Rate-limit headers are surfaced via stderr — fail-loud comes for free

Failure mode is uniform — any of these RAISE
:class:`FleetAdapterError`:

- ``gh`` binary missing from ``PATH``
- ``gh`` exits non-zero (auth missing, 404, rate limit, …)
- ``gh`` returns malformed JSON or an empty payload
- the repo has no commits on its default branch (genuinely unusable)

The view layer catches per-repo so one dead adapter doesn't blank the
whole pills strip.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from typing import Any

from ._errors import FleetAdapterError

# Subprocess timeout for one ``gh`` call. Two calls per repo
# (default-branch resolution + check-runs fetch) so the worst-case wall
# time per repo is roughly 2 * _GH_TIMEOUT seconds.
_GH_TIMEOUT = 10


def _gh_binary() -> str:
    """Locate ``gh``; raise FleetAdapterError if absent."""
    exe = shutil.which("gh")
    if exe is None:
        raise FleetAdapterError(
            "gh CLI not found on PATH — install GitHub CLI and run "
            "`gh auth login` to enable the fleet CI-status pills."
        )
    return exe


def _gh_json(args: list[str]) -> Any:
    """Run ``gh`` with ``args``, parse stdout as JSON, raise on any
    failure mode (non-zero exit, timeout, malformed JSON, empty body).

    The error message includes the command and a trimmed stderr so the
    operator can copy-paste and reproduce.
    """
    exe = _gh_binary()
    cmd = [exe, *args]
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=_GH_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise FleetAdapterError(
            f"gh call timed out after {_GH_TIMEOUT}s: {' '.join(cmd)}"
        ) from exc
    except OSError as exc:
        raise FleetAdapterError(
            f"gh call failed to start: {' '.join(cmd)}: {exc}"
        ) from exc

    if proc.returncode != 0:
        stderr_excerpt = (proc.stderr or "").strip().splitlines()
        # Keep the message single-line-ish so the FE tooltip can show it.
        excerpt = " | ".join(stderr_excerpt[:3]) or "(no stderr)"
        raise FleetAdapterError(
            f"gh exited {proc.returncode} for {' '.join(cmd)}: {excerpt}"
        )

    out = (proc.stdout or "").strip()
    if not out:
        raise FleetAdapterError(
            f"gh returned empty body for {' '.join(cmd)}"
        )
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise FleetAdapterError(
            f"gh returned malformed JSON for {' '.join(cmd)}: {exc}"
        ) from exc


def _default_branch(slug: str) -> str:
    """Return the repo's default branch name (e.g. ``main``)."""
    payload = _gh_json(
        ["repo", "view", slug, "--json", "defaultBranchRef"]
    )
    if not isinstance(payload, dict):
        raise FleetAdapterError(
            f"unexpected payload shape from `gh repo view {slug}`: "
            f"expected object, got {type(payload).__name__}"
        )
    ref = payload.get("defaultBranchRef")
    if not isinstance(ref, dict) or not ref.get("name"):
        raise FleetAdapterError(
            f"`gh repo view {slug}` returned no defaultBranchRef.name — "
            f"repo may be empty or unauthorized: {payload!r}"
        )
    return str(ref["name"])


def _overall_from_checks(checks: list[dict]) -> str:
    """Reduce a list of check-runs to a single overall status.

    Precedence (the operator wants to see the worst news first):

    1. any ``failure`` / ``timed_out`` / ``cancelled`` → ``"failure"``
    2. any not-yet-completed (``queued`` / ``in_progress``) → ``"pending"``
    3. all completed with ``success`` / ``neutral`` / ``skipped`` → ``"success"``
    4. anything else → ``"unknown"``
    """
    if not checks:
        # No checks at all on the head SHA — the repo simply has no CI
        # configured for this branch. That's a legitimate steady state,
        # not an adapter error; the UI shows it as "unknown" (grey).
        return "unknown"

    has_pending = False
    has_failure = False
    all_terminal_good = True
    bad_conclusions = {"failure", "timed_out", "cancelled", "action_required"}
    good_conclusions = {"success", "neutral", "skipped"}

    for c in checks:
        status = (c.get("status") or "").lower()
        conclusion = (c.get("conclusion") or "").lower()
        if status != "completed":
            has_pending = True
            all_terminal_good = False
            continue
        if conclusion in bad_conclusions:
            has_failure = True
            all_terminal_good = False
        elif conclusion not in good_conclusions:
            all_terminal_good = False

    if has_failure:
        return "failure"
    if has_pending:
        return "pending"
    if all_terminal_good:
        return "success"
    return "unknown"


def fetch_repo_ci_status(repo_slug: str) -> dict[str, Any]:
    """Fetch the CI summary for one repo.

    Returns a dict shaped like::

        {
          "slug":     "owner/name",
          "branch":   "main",
          "head_sha": "abc123...",
          "checks":   [{"name": ..., "status": ..., "conclusion": ...}, ...],
          "overall":  "success" | "failure" | "pending" | "unknown",
        }

    Raises :class:`FleetAdapterError` on any upstream / parsing failure;
    the caller (the view) is responsible for trapping it per-repo.
    """
    if not isinstance(repo_slug, str) or "/" not in repo_slug:
        raise FleetAdapterError(
            f"invalid repo slug {repo_slug!r} — expected 'owner/name'"
        )

    branch = _default_branch(repo_slug)
    # `gh api` against the check-runs endpoint for the branch HEAD. The
    # ``--paginate`` flag concatenates pages so we don't miss large
    # workflow fans; the API returns ``check_runs`` (snake-case) and the
    # head SHA on each run.
    api_path = f"repos/{repo_slug}/commits/{branch}/check-runs"
    payload = _gh_json(["api", api_path, "--paginate"])

    if not isinstance(payload, dict):
        raise FleetAdapterError(
            f"unexpected payload from `gh api {api_path}`: "
            f"expected object, got {type(payload).__name__}"
        )
    runs_raw = payload.get("check_runs")
    if runs_raw is None:
        # The repo has zero check runs for this branch HEAD. Surface as
        # an empty checks list + "unknown" overall, not as an error.
        runs_raw = []
    if not isinstance(runs_raw, list):
        raise FleetAdapterError(
            f"`gh api {api_path}` returned non-list check_runs: "
            f"{type(runs_raw).__name__}"
        )

    head_sha = ""
    checks: list[dict[str, str]] = []
    for run in runs_raw:
        if not isinstance(run, dict):
            continue
        # Every check_run carries head_sha; we report the most recent
        # one (they should all match for a single branch HEAD, but be
        # defensive — take the first non-empty).
        if not head_sha and isinstance(run.get("head_sha"), str):
            head_sha = run["head_sha"]
        checks.append(
            {
                "name": str(run.get("name") or ""),
                "status": str(run.get("status") or ""),
                "conclusion": str(run.get("conclusion") or ""),
            }
        )

    return {
        "slug": repo_slug,
        "branch": branch,
        "head_sha": head_sha,
        "checks": checks,
        "overall": _overall_from_checks(checks),
    }


__all__ = ["fetch_repo_ci_status"]

# EOF
