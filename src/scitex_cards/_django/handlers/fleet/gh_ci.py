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


def _gh_json(args: list[str], timeout: int = _GH_TIMEOUT) -> Any:
    """Run ``gh`` with ``args``, parse stdout as JSON, raise on any
    failure mode (non-zero exit, timeout, malformed JSON, empty body).

    ``timeout`` defaults to the per-call REST budget; the bulk GraphQL
    path passes a longer one (one request, many repos resolved server-side).
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
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise FleetAdapterError(
            f"gh call timed out after {timeout}s: {' '.join(cmd)}"
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
        raise FleetAdapterError(f"gh returned empty body for {' '.join(cmd)}")
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise FleetAdapterError(
            f"gh returned malformed JSON for {' '.join(cmd)}: {exc}"
        ) from exc


def _default_branch(slug: str) -> str:
    """Return the repo's default branch name (e.g. ``main``)."""
    payload = _gh_json(["repo", "view", slug, "--json", "defaultBranchRef"])
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


# --------------------------------------------------------------------------
# Bulk GraphQL path — ONE request for MANY repos.
# --------------------------------------------------------------------------
#
# The per-repo REST path above costs TWO gh calls (default-branch +
# check-runs) PER repo. The fleet pills poll every 30s; at ecosystem scale
# (~70 repos) that is ~140 REST calls per poll → it blows GitHub's
# 5,000-requests/hr limit and the strip starts erroring.
#
# GitHub's GraphQL API bills by node cost, not per call: aliasing N repos
# into ONE query returns every repo's default-branch CI rollup for ~1
# point (measured: cost=1 for a 5-repo batch). One query every 30s for the
# whole ecosystem is ~120 points/hr — a rounding error against the
# 5,000-point/hr budget. So the pills + poller use this bulk path; the
# per-repo `fetch_repo_ci_status` stays for lazy on-demand per-check detail.

# Repos per GraphQL request. One query handles the whole ecosystem fine,
# but we chunk so an unusually large watch-list stays under GitHub's
# per-query node ceiling.
_GRAPHQL_CHUNK = 50

# Longer timeout for the bulk call — still ONE request, but it resolves
# many repos server-side.
_GRAPHQL_TIMEOUT = 20


def _valid_slug_part(part: str) -> bool:
    """True if ``part`` is a safe GitHub owner/repo token.

    Restricting to ``[alnum] . _ -`` means a stray config entry can never be
    interpolated into the GraphQL string as anything but a literal name.
    """
    return bool(part) and all(ch.isalnum() or ch in "._-" for ch in part)


def _split_slug(slug: str) -> tuple[str, str] | None:
    """Split ``owner/name`` into validated parts, or ``None`` if malformed."""
    if not isinstance(slug, str) or slug.count("/") != 1:
        return None
    owner, name = slug.split("/", 1)
    if not _valid_slug_part(owner) or not _valid_slug_part(name):
        return None
    return owner, name


def _overall_from_rollup(state: str | None) -> str:
    """Map a GraphQL ``statusCheckRollup.state`` to our 4-value overall.

    GitHub's ``StatusState`` enum is EXPECTED / ERROR / FAILURE / PENDING /
    SUCCESS. ``None`` = the head commit has no rollup (no CI on the branch),
    the same "unknown / grey" steady state the REST path returns for an
    empty check list.
    """
    s = (state or "").upper()
    if s == "SUCCESS":
        return "success"
    if s in ("FAILURE", "ERROR"):
        return "failure"
    if s in ("PENDING", "EXPECTED"):
        return "pending"
    return "unknown"


def _build_graphql_query(chunk: list[str]) -> str:
    """Build one aliased GraphQL query for a chunk of validated slugs."""
    lines = ["{", "  rateLimit { cost remaining }"]
    for i, slug in enumerate(chunk):
        owner, name = slug.split("/", 1)
        lines.append(
            f'  r{i}: repository(owner: "{owner}", name: "{name}") {{ '
            "nameWithOwner defaultBranchRef { name target { "
            "... on Commit { oid statusCheckRollup { state } } } } }"
        )
    lines.append("}")
    return "\n".join(lines)


def _parse_graphql_repo(node: Any, slug: str) -> dict[str, Any]:
    """Map one GraphQL ``repository`` node to the per-repo pills shape."""
    if not isinstance(node, dict):
        # null node — repo not found or not visible to the token.
        return {
            "slug": slug,
            "error": "repository not found or not accessible",
        }
    ref = node.get("defaultBranchRef")
    if not isinstance(ref, dict):
        # Empty repo / no default branch — legitimate; render as unknown.
        return {
            "slug": slug,
            "branch": "",
            "head_sha": "",
            "checks": [],
            "overall": "unknown",
        }
    target_raw = ref.get("target")
    target = target_raw if isinstance(target_raw, dict) else {}
    rollup = target.get("statusCheckRollup")
    state = rollup.get("state") if isinstance(rollup, dict) else None
    return {
        "slug": slug,
        "branch": str(ref.get("name") or ""),
        "head_sha": str(target.get("oid") or ""),
        # Bulk path returns the rollup overall only; the individual check
        # rows are a lazy per-repo `fetch_repo_ci_status` call on demand.
        "checks": [],
        "overall": _overall_from_rollup(state),
    }


def _gh_graphql(query: str, timeout: int = _GRAPHQL_TIMEOUT) -> dict[str, Any]:
    """Run ``gh api graphql`` and return the parsed response body.

    GraphQL is partial-success by design: when SOME aliases fail (e.g. a
    repo doesn't exist) GitHub returns the resolved data ALONGSIDE an
    ``errors`` array, and ``gh`` exits non-zero. We therefore parse the
    body REGARDLESS of exit code and only raise when there is no usable
    ``data`` object (gh missing, auth dead, network, malformed query) — so
    one missing repo never blanks the whole batch.
    """
    exe = _gh_binary()
    cmd = [exe, "api", "graphql", "-f", f"query={query}"]
    try:
        proc = subprocess.run(
            cmd, check=False, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired as exc:
        raise FleetAdapterError(f"gh graphql timed out after {timeout}s") from exc
    except OSError as exc:
        raise FleetAdapterError(f"gh graphql failed to start: {exc}") from exc

    body: Any = None
    out = (proc.stdout or "").strip()
    if out:
        try:
            body = json.loads(out)
        except json.JSONDecodeError:
            body = None
    # Usable data (full OR partial) → return it; the caller maps per-alias
    # nulls to error entries via the `errors` array.
    if isinstance(body, dict) and isinstance(body.get("data"), dict):
        return body
    # No usable data → a genuine whole-batch failure. Surface GraphQL error
    # messages if present, else the gh stderr.
    if isinstance(body, dict) and body.get("errors"):
        msgs = "; ".join(
            str(e.get("message")) for e in body["errors"][:3] if isinstance(e, dict)
        )
        raise FleetAdapterError(f"gh graphql errored: {msgs}")
    stderr = " | ".join((proc.stderr or "").strip().splitlines()[:3])
    raise FleetAdapterError(
        f"gh graphql exited {proc.returncode}: {stderr or '(no stderr)'}"
    )


def fetch_many_ci_status(slugs: list[str]) -> list[dict[str, Any]]:
    """Fetch CI summaries for MANY repos in one (chunked) GraphQL request.

    Returns one entry per input slug, in input order, each shaped exactly
    like :func:`fetch_repo_ci_status` (``slug`` / ``branch`` / ``head_sha``
    / ``checks`` / ``overall``) OR a ``{"slug", "error"}`` sub-document —
    the SAME contract the view already renders, so nothing downstream
    changes.

    Per-repo robustness mirrors the view's old per-repo trap: a malformed
    slug or a missing repo becomes an error entry rather than blanking the
    batch. A WHOLE-batch failure (gh missing, auth dead, network) still
    raises :class:`FleetAdapterError`.
    """
    results: dict[str, dict[str, Any]] = {}
    valid: list[str] = []
    for slug in slugs:
        if _split_slug(slug) is None:
            results[slug] = {
                "slug": slug,
                "error": "invalid slug — expected 'owner/name'",
            }
        else:
            valid.append(slug)

    for start in range(0, len(valid), _GRAPHQL_CHUNK):
        chunk = valid[start : start + _GRAPHQL_CHUNK]
        body = _gh_graphql(_build_graphql_query(chunk))
        data = body.get("data") or {}
        # Map GraphQL per-alias errors (e.g. NOT_FOUND) back to their slug
        # so a missing repo shows its real reason, not a generic null.
        err_by_alias: dict[str, str] = {}
        for e in body.get("errors") or []:
            if not isinstance(e, dict):
                continue
            path = e.get("path")
            if isinstance(path, list) and path and isinstance(path[0], str):
                err_by_alias[path[0]] = str(e.get("message") or "GraphQL error")
        for i, slug in enumerate(chunk):
            alias = f"r{i}"
            node = data.get(alias)
            if node is None and alias in err_by_alias:
                results[slug] = {"slug": slug, "error": err_by_alias[alias]}
            else:
                results[slug] = _parse_graphql_repo(node, slug)

    return [results[s] for s in slugs]


__all__ = ["fetch_repo_ci_status", "fetch_many_ci_status"]

# EOF
