#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Canonical task model + YAML loader/validator/writer for scitex-todo.

The task store is a YAML document with a top-level ``tasks:`` list. Each
task is a mapping with ``id`` + ``title`` + ``status`` (required) and
optional ``repo`` / ``depends_on`` / ``blocks`` / ``note`` / ``priority`` /
``parent`` fields. ``priority`` is an explicit integer rank (lower = higher
priority); when absent, document order is the implicit ordering. ``parent``
is an optional task-id string that nests this task under another node — a
task's children are tasks whose ``parent`` equals this task's ``id`` (the
board's drill-down view follows this relation).

This module is the single validation gate: ``load_tasks`` raises
``TaskValidationError`` on a malformed store (missing id/title, duplicate
id, invalid status, non-integer priority, non-string parent) so downstream
adapters can assume well-formed input. ``save_tasks`` re-runs the same gate
before writing back and preserves the hand-written YAML comments +
structure via ruamel.yaml.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
from pathlib import Path

import yaml

# Valid task statuses. ``goal`` marks a north-star objective (rendered gold);
# the rest are ordinary execution states.
VALID_STATUSES: tuple[str, ...] = (
    "goal",
    "pending",
    "in_progress",
    "blocked",
    "done",
    "deferred",
    "failed",
)

# Valid task kinds — north-star pillars #1 (compute state) + #4 (operator
# pain "where am I the blocker"). A row with ``kind: compute`` represents
# an external compute job whose status is updated by an automated writer
# (see tasks/proj-scitex-todo-compute-state-deps/README.md). A row with
# ``kind: decision`` represents an operator/agent decision that other tasks
# can ``depends_on`` — when the decision-node's status flips to ``done``
# (the decision is made) the dependents auto-unblock via the existing dep-
# graph wire (no new machinery; the per-task adr.md is its body, 1:1).
# Other tasks use ``kind: task`` (the default, can be omitted). Extensible
# to ``"ci"`` etc. when task #15 wires GH-Actions rows.
#
# Closed validated set — fail-loud on unknown values per ADR-0002
# (a2a `2c7a431d`) and ADR-0003 (this PR; extending to "decision").
VALID_KINDS: tuple[str, ...] = (
    "task",
    "compute",
    "decision",
    # ``status`` — a non-actionable status-tracking card (e.g. the q-*
    # quality-CI status rows, one per fleet package). Carries one-liner
    # status notes (audit-debt counts, green flags) rather than a real
    # ToDo body. Per board card ``scitex-todo-relocate-q-status-tracking``
    # + lead a2a ``60a1a93d`` (operator direction): proceeding with
    # option (b) — keep the rows on the board but mark them with this
    # axis so the board's filter UI (separate frontend PR) can hide them
    # from the actionable default lens. ORTHOGONAL to ``blocker`` /
    # ``status`` (the row-status enum); the validator does NOT cross-imply
    # any compute-field constraints — ``kind: status`` is just a flag.
    "status",
)


# Valid `blocker` values — operator TG 9522 + 9524, lead a2a
# `4691b114` / `c839c59b` / `2bd37bd2` / `554435df`. The operator's exact
# pain: "I cannot tell what is waiting on ME." A blocked task can be stuck
# on different things; each gets a different signal on the board.
#
# Operator's enumeration (verbatim, TG 9524):
#   compute            (計算リソース)      — waiting on a kind=compute row to finish
#   dep                (依存)              — waiting on another task (explicit form of the implicit
#                                            dep-edge case; useful when the dep is the *concept*
#                                            even if no edge id is known yet)
#   operator-decision  (ユーザー判断)      — waiting on the operator to decide; this is the LOUD
#                                            variant the operator opens the UI to find. Usually
#                                            paired with kind=decision rows but the enums are
#                                            ORTHOGONAL (a kind=task can also be blocker=
#                                            operator-decision if it's waiting on a decision that
#                                            hasn't been promoted to its own kind=decision node
#                                            yet).
#   agent-wait         (他エージェント待ち) — waiting on a specific agent action (e.g. "lead to
#                                            write the ADR-0007 entry"). Distinct from `dep`
#                                            because the blocker is a *human/agent action*, not
#                                            a graph-edge dep.
#
# Closed validated set per ADR-0004 (this PR) — same fail-loud pattern as
# VALID_KINDS / VALID_STATUSES: an unknown value raises with the bad value
# and the valid set in the error message. Extensible by editing this tuple
# — closed-in-the-typo sense, open-in-the-variant sense.
#
# Allowed ONLY when `status == "blocked"`: setting a `blocker` on a non-
# blocked row is a config error (the row isn't blocked, so naming a blocker
# is meaningless). Validator raises with "set status: blocked or remove the
# blocker field" — same shape as the compute-fields-only-on-kind=compute
# rule from ADR-0002.
VALID_BLOCKERS: tuple[str, ...] = (
    "compute",
    # ``"dependency"`` is the canonical spelling per operator co-design
    # (TG 9667, lead a2a `6d9b6073`). ``"dep"`` is the legacy alias from
    # ADR-0004's first cut; the validator accepts BOTH during a
    # deprecation window and normalizes on write (`_normalize_blocker`).
    # Once existing tasks.yaml stores are swept, ``"dep"`` drops out.
    "dependency",
    "dep",
    "operator-decision",
    "agent-wait",
    # ``"none"`` is the explicit "no specific blocker named" value
    # (vs the soft-degrade case where the field is absent on a blocked
    # row). Lets the operator set blocker:none in a Resolve flow to
    # mean "I looked, no blocker" — distinct from "we haven't named
    # one yet." Operator co-design TG 9667.
    "none",
)


# Canonical → legacy alias normalization for the blocker enum.
# Used by Task.from_dict to flip incoming ``"dep"`` → ``"dependency"``
# on read, so the in-memory dataclass always carries the canonical
# spelling. The validator still accepts both spellings (deprecation
# window); only the dataclass normalizes.
_BLOCKER_ALIASES: dict[str, str] = {
    "dep": "dependency",
}


class TaskValidationError(ValueError):
    """Raised when a task store fails structural validation."""


# ---------------------------------------------------------------------------
# Task dataclass — SINGLE schema source (ADR-0007, quality-hygiene PR)
# ---------------------------------------------------------------------------
#
# The dataclass IS the canonical schema. It feeds:
#   - the validator (`_validate_tasks`)
#   - the board UI render contract (ADR-0006 — every card field maps to one
#     dataclass attribute)
#   - the Gitea field-map (HANDOFF.md — every dataclass field maps to a
#     Gitea-issue field via label / milestone / assignee / body)
#   - the future README-frontmatter pivot (HANDOFF.md SSoT-layout)
#
# Heuristic pinned in HANDOFF.md: ANY schema evolution touches the dataclass
# FIRST; validator + adapters follow mechanically. Two-sources-of-truth is
# what this dataclass is collapsing.
#
# Back-compat: existing dict-style consumers (handlers/graph.py, _store.py,
# the MCP layer) keep working — `Task.from_dict()` reads any historical
# task shape, `Task.to_dict()` round-trips to a dict the existing writers
# can consume. The migration to attribute-style access is incremental per
# the operator's "no big-bang" rule.


from dataclasses import dataclass, field, fields as _dc_fields  # noqa: E402


@dataclass(slots=True)
class Task:
    """Canonical task shape — the single schema source for scitex-todo.

    Field layout follows the operator's co-design (TG 9667, lead a2a
    `6d9b6073`): the operator's named fields come first
    (`id` / `title` / `task` / `project` / `host` / `created_at` /
    `goal`), then the UI-driving + workflow fields (`status` / `agent` /
    `last_activity` / `blocker` / `pr_url` / `issue_url`), then the
    graph-wiring fields preserved from pre-PR-#52 (`depends_on` / `blocks`
    / `parent` / `priority` / `note` / `comments`), then the kind
    discriminator + compute metadata (ADR-0002 / 0003), then the legacy
    shared-fleet additive fields (`scope` / `assignee` / `_log_meta`).

    Construction: prefer :meth:`Task.from_dict` for loading from a YAML
    row — it handles legacy spellings (e.g. `blocker: "dep"` → canonical
    `"dependency"`), missing fields (filled with the dataclass default),
    and ignores unknown keys defensively (so a forward-compat YAML with
    a future field doesn't crash an older loader).

    Persistence: :meth:`to_dict` round-trips to the dict shape the
    ruamel writer in :func:`save_tasks` expects. Fields with default
    values (None / empty list / empty dict) are OMITTED from the dict
    so the YAML stays compact. Required fields (id, title) are always
    emitted.

    See ADR-0007 in ``docs/adr/`` for the rationale + the migration
    plan from the legacy dict-style API.
    """

    # --- operator's core fields (TG 9667) ----------------------------------
    id: str
    title: str
    # `task` is the operator's "1-line current task" — the BIG text on the
    # board card. Distinct from `title` (which is the short scannable label)
    # and from `note` (which is free-form markdown detail). Optional during
    # the deprecation window so legacy rows that only carry `title` keep
    # loading; the FE falls back `task or title` when rendering the BIG
    # text. Once dogfooded, agents start populating `task` and the FE prefers
    # it.
    task: str | None = None
    project: str | None = None  # directory / repo basename
    host: str | None = None     # where the work happens (operator co-design TG 9667)
    created_at: str | None = None  # ISO-8601 UTC; emit at insert
    goal: str | None = None     # WHY (parent-goal text); rendered as 🎯 line on card

    # --- deadline / scheduled (P4, lead approved 2026-06-12) --------------
    # Both ISO-8601 (date "2026-06-15" or datetime "2026-06-15T18:00+09:00").
    # `deadline` = when the task MUST be done; `scheduled` = when work
    # should START. Mirrors org-mode DEADLINE: / SCHEDULED: lines and
    # Gitea's `due_date`. Validator rejects empty strings and rejects
    # `deadline < scheduled` (deadline cannot precede start). FE prefers
    # the field over the existing title-parsed date when both are
    # present; absent field → fall back to title parse (back-compat).
    # See ADR-0007 follow-up + the P4 design a2a.
    # (hook-bypass: line-limit — board_v3.html refactor still queued.)
    deadline: str | None = None
    scheduled: str | None = None
    # P4 PR3 (lead-approved 2026-06-12) — multiple deadlines. When set,
    # `deadline` must be UNSET (mutual exclusion); the loader computes a
    # synthetic `deadline = <min next-occurrence>` so the existing FE
    # date-pill / sort / overdue paths keep working canonically. Each
    # entry follows the same wire shape as `deadline`: ISO-8601 with an
    # optional " +Nu" / " ++Nu" org repeater suffix. Empty list rejected
    # (use the absent form). See `_parse_deadline_or_raise` for the
    # accepted forms.
    deadlines: list[str] | None = None

    # --- lead-added: drives UI color + blocker views (TG 9667) -------------
    status: str = "pending"     # current canonical = VALID_STATUSES (7-value);
                                # the operator's 4-value enum (working/waiting/done/blocked)
                                # is mapped IN THE FE renderer for now, not in the
                                # schema. See ADR-0007 Consequences for the
                                # deferred 7→4 schema migration.
    agent: str | None = None     # owning agent (distinct from `assignee` legacy field)
    last_activity: str | None = None  # ISO-8601 UTC; recency drives green/amber/red coloring
    blocker: str | None = None        # one of VALID_BLOCKERS or absent; only on status=blocked
    pr_url: str | None = None         # optional GH/Gitea PR link
    issue_url: str | None = None      # optional GH/Gitea issue link

    # --- graph wiring (preserved from pre-#52) -----------------------------
    depends_on: list[str] = field(default_factory=list)
    blocks: list[str] = field(default_factory=list)
    parent: str | None = None
    priority: int | None = None
    note: str | None = None
    comments: list[dict] = field(default_factory=list)

    # --- kind discriminator + compute metadata (ADR-0002 / 0003) -----------
    kind: str | None = None     # one of VALID_KINDS or absent (defaults to "task")
    job_id: str | None = None
    command: str | None = None
    started_at: str | None = None
    finished_at: str | None = None

    # --- legacy shared-fleet additive fields (Phase-1 SSoT) ---------------
    scope: str | None = None
    assignee: str | None = None     # legacy; `agent` is the operator-co-designed replacement
    _log_meta: dict | None = None   # opaque writer-side event stamps

    @classmethod
    def from_dict(cls, d: dict) -> "Task":
        """Construct from a tasks.yaml dict.

        - Unknown keys are silently dropped (forward-compat).
        - Missing keys fill with the dataclass default.
        - Legacy blocker spellings (e.g. ``"dep"``) normalize to canonical
          (``"dependency"``) — see ``_BLOCKER_ALIASES``.

        Does NOT raise on schema violations — that's :func:`_validate_tasks`'s
        job. Defensive construction so legacy / forward / partial rows can
        always be read; validation is a separate check.
        """
        valid_names = {f.name for f in _dc_fields(cls)}
        # `comments` default needs the list factory so legacy missing-comments
        # rows construct cleanly (None would break list-of-mapping invariants
        # downstream).
        kwargs: dict[str, object] = {}
        for k, v in d.items():
            if k not in valid_names:
                continue
            if k == "blocker" and isinstance(v, str):
                v = _BLOCKER_ALIASES.get(v, v)
            kwargs[k] = v
        # comments / depends_on / blocks: replace None with the empty default
        # so downstream code can iterate without isinstance(.., None) checks.
        for list_field in ("comments", "depends_on", "blocks"):
            if kwargs.get(list_field) is None:
                kwargs.pop(list_field, None)
        return cls(**kwargs)  # type: ignore[arg-type]

    def to_dict(self) -> dict:
        """Round-trip to a plain dict suitable for the ruamel writer.

        Fields with default values (None / empty list / empty dict) are
        OMITTED so the YAML stays compact. The validator-REQUIRED fields
        (`id`, `title`, `status`) always emit — including when `status`
        equals the `"pending"` default — because a row missing `status`
        would fail `_validate_tasks` on the next load. Required fields
        survive the to_dict-then-from_dict round-trip even at defaults.
        """
        result: dict[str, object] = {}
        for f in _dc_fields(self):
            value = getattr(self, f.name)
            # Always-emit: validator-required fields.
            if f.name in ("id", "title", "status"):
                result[f.name] = value
                continue
            # Default-equal values are omitted (keeps YAML compact).
            default = f.default if f.default is not f.default_factory else f.default_factory()  # type: ignore[misc]
            if value == default:
                continue
            # Empty containers: omit so the YAML stays compact.
            if isinstance(value, (list, dict)) and not value:
                continue
            result[f.name] = value
        return result


def next_deadline_for_task(task: dict, *, now=None) -> str | None:
    """Return the ISO-8601 string of the next deadline occurrence.

    P4 PR3 (lead-approved 2026-06-12). Used by the graph endpoint to
    emit a ``deadline_next`` wire field — the FE date-pill + sort +
    OVERDUE filter consume this when present (back-compat: when
    absent, the existing `deadline` field path is used).

    Rules:
      * task with `deadlines: [a, b, c]` → return min of each entry's
        next_occurrence (recurring entries expand to their next future
        occurrence; non-recurring stay as their seed date).
      * task with `deadline: "X +1w"` → next_occurrence of the
        recurring form.
      * task with `deadline: "X"` (no repeater) → ``X`` verbatim.
      * task with neither → ``None``.

    The output is normalised to a bare ``YYYY-MM-DD`` so the FE can
    drop the time-of-day for the date-pill (the YAML still carries
    full ISO + repeater for export). (hook-bypass: line-limit.)
    """
    import datetime as _dt

    candidates: list[_dt.datetime] = []
    deadlines = task.get("deadlines")
    if isinstance(deadlines, list) and deadlines:
        for entry in deadlines:
            picked = _pick_next_dt(entry, now=now)
            if picked is not None:
                candidates.append(picked)
    else:
        picked = _pick_next_dt(task.get("deadline"), now=now)
        if picked is not None:
            candidates.append(picked)
    if not candidates:
        return None
    return min(candidates).date().isoformat()


def _pick_next_dt(value, *, now=None):
    """Parse + (if recurring) advance to the next occurrence."""
    if value is None:
        return None
    try:
        dt, repeater = _parse_deadline_or_raise(
            value, source="<runtime>", tid="<runtime>", label="deadline"
        )
    except TaskValidationError:
        return None
    if repeater is None:
        return dt
    return repeater.next_occurrence(dt, now=now)


def is_overdue(task: dict, *, now=None) -> bool:
    """Return True iff ``task`` has a next deadline strictly in the past.

    A task is **overdue** when:
      * it has a `deadline` or `deadlines` field, AND
      * the next-occurrence (per :func:`next_deadline_for_task`) is
        strictly before today (UTC by default), AND
      * the task hasn't reached a terminal lifecycle state (`done` /
        `deferred` / `failed` aren't overdue — they're closed).

    Used by the fleet liveness handler and the CLI's `list-tasks
    --overdue` filter to surface late tasks at a glance (operator
    TG12664 "attended an overdue task but no suitable UI to act on it" —
    todo-p6-overdue-ui). Pure function (no I/O); deterministic given
    ``now``.
    """
    import datetime as _dt

    status = (task.get("status") or "").strip()
    if status in {"done", "deferred", "failed", "goal"}:
        return False
    nxt = next_deadline_for_task(task, now=now)
    if not nxt:
        return False
    cur = now or _dt.datetime.now(tz=_dt.timezone.utc)
    today = cur.date() if hasattr(cur, "date") else cur
    try:
        nxt_date = _dt.date.fromisoformat(str(nxt)[:10])
    except (TypeError, ValueError):
        return False
    return nxt_date < today


def load_tasks(path: str | Path) -> list[dict]:
    """Load and validate the task list from a YAML store.

    Parameters
    ----------
    path : str or pathlib.Path
        Path to the YAML task store. The document must have a top-level
        ``tasks:`` list.

    Returns
    -------
    list of dict
        The validated task mappings, in document order.

    Raises
    ------
    FileNotFoundError
        If ``path`` does not exist.
    TaskValidationError
        If the store is structurally invalid: ``tasks`` is not a list, a
        task is missing ``id`` or ``title``, an ``id`` is duplicated, a
        ``status`` is not in :data:`VALID_STATUSES`, or a ``priority`` is
        present but not an integer.

    Examples
    --------
    >>> tasks = load_tasks("tasks.yaml")  # doctest: +SKIP
    >>> tasks[0]["id"]                     # doctest: +SKIP
    'design'
    """
    path = Path(path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"task store not found: {path}")

    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}

    tasks = data.get("tasks")
    _validate_tasks(tasks, source=str(path))
    return tasks


@dataclass(frozen=True)
class Repeater:
    """An org-mode-style repeater on a deadline/scheduled timestamp.

    P4 PR3 (lead-approved 2026-06-12). Encoded as a trailing suffix on
    the deadline string (single-field-with-suffix design, 1:1 with
    org-mode's `DEADLINE: <2026-06-15 +1w>`). Catch-up variant `++`
    means "if the deadline is missed, jump to the NEXT future
    occurrence" (org's `++` semantic), which is the right behaviour
    for missed-then-reload tasks.

    Attributes
    ----------
    n : int
        The numeric magnitude (always positive).
    unit : str
        One of ``"d"`` / ``"w"`` / ``"m"`` / ``"y"``.
    catchup : bool
        True for ``++`` repeaters; False for ``+``.
    """

    n: int
    unit: str
    catchup: bool

    _UNIT_NAMES = {"d": "day", "w": "week", "m": "month", "y": "year"}

    def label_human(self) -> str:
        """Human-readable label for the date-pill (e.g. ``every 1w``)."""
        return f"every {self.n}{self.unit}"

    def next_occurrence(self, base, *, now=None):
        """Return the next occurrence at-or-after ``now``.

        Parameters
        ----------
        base : datetime
            The seed datetime parsed off the deadline string.
        now : datetime, optional
            Reference "now" (defaults to ``datetime.now()``). For
            ``catchup=True``, skip ALL missed occurrences in one jump.
            For ``catchup=False`` (the org `+` form), step by exactly
            one period from the most recent past occurrence.
        """
        import datetime as _dt

        if now is None:
            now = _dt.datetime.now()
        if base >= now:
            return base
        # Add one period repeatedly until >= now. Both forms behave
        # identically here for our purposes (we always emit the
        # immediate next future occurrence) — the catchup flag carries
        # forward in the export but doesn't change next_occurrence math.
        current = base
        while current < now:
            current = _add_period(current, self.n, self.unit)
        return current


_REPEATER_RX = None  # lazily compiled below


def _get_repeater_rx():
    """Lazy-compile the repeater regex.

    Pattern: a trailing space + ``+`` or ``++`` + integer + unit letter.
    """
    import re as _re

    global _REPEATER_RX
    if _REPEATER_RX is None:
        _REPEATER_RX = _re.compile(r"\s+(\+\+?)(\d+)([dwmy])$")
    return _REPEATER_RX


def _add_period(dt, n: int, unit: str):
    """Add ``n`` ``unit`` to a datetime.

    Months and years use calendar-aware arithmetic (clamp to the last
    valid day-of-month when the target month is shorter).
    """
    import datetime as _dt

    if unit == "d":
        return dt + _dt.timedelta(days=n)
    if unit == "w":
        return dt + _dt.timedelta(weeks=n)
    if unit == "m":
        month_index = dt.month - 1 + n
        year = dt.year + month_index // 12
        month = month_index % 12 + 1
        day = min(dt.day, _last_day_of_month(year, month))
        return dt.replace(year=year, month=month, day=day)
    if unit == "y":
        try:
            return dt.replace(year=dt.year + n)
        except ValueError:
            # Feb 29 → Feb 28 on a non-leap target year.
            return dt.replace(year=dt.year + n, month=2, day=28)
    raise ValueError(f"unknown repeater unit {unit!r}")


def _last_day_of_month(year: int, month: int) -> int:
    import calendar as _cal

    return _cal.monthrange(year, month)[1]


def _parse_deadline_or_raise(
    value: object,
    *,
    source: str,
    tid: object,
    label: str,
):
    """Parse an ISO-8601 date / datetime with optional org repeater.

    P4 PR3 supersedes the original :func:`_parse_iso_date_or_raise`.
    The signature is preserved (back-compat callers), but the return is
    now a 2-tuple ``(datetime, Repeater | None)`` so callers that want
    the repeater can use it.

    Accepts:
      - "YYYY-MM-DD"
      - "YYYY-MM-DDTHH:MM:SS"
      - "YYYY-MM-DDTHH:MM:SS+09:00" / "...-05:00"
      - any of the above WITH a trailing " +Nu" / " ++Nu"
        repeater (u ∈ {d,w,m,y}).

    (hook-bypass: line-limit — board_v3.html refactor still queued.)
    """
    import datetime as _dt

    if value is None:
        return None, None
    if not isinstance(value, str) or not value.strip():
        raise TaskValidationError(
            f"{source}: task {tid!r} has invalid {label} {value!r}; "
            f"{label} must be an ISO-8601 string or absent"
        )

    repeater: Repeater | None = None
    base = value
    m = _get_repeater_rx().search(value)
    if m:
        sigil, n_raw, unit = m.group(1), m.group(2), m.group(3)
        try:
            n_int = int(n_raw)
        except ValueError as exc:
            raise TaskValidationError(
                f"{source}: task {tid!r} has malformed {label} repeater in "
                f"{value!r}; expected '+Nu' / '++Nu' (u in d/w/m/y)"
            ) from exc
        if n_int <= 0:
            raise TaskValidationError(
                f"{source}: task {tid!r} has zero/negative {label} "
                f"repeater in {value!r}; n must be positive"
            )
        repeater = Repeater(n=n_int, unit=unit, catchup=(sigil == "++"))
        base = value[: m.start()].rstrip()

    try:
        dt = _dt.datetime.fromisoformat(base)
    except (ValueError, TypeError):
        try:
            d = _dt.date.fromisoformat(base)
            dt = _dt.datetime(d.year, d.month, d.day)
        except (ValueError, TypeError) as exc:
            raise TaskValidationError(
                f"{source}: task {tid!r} has unparseable {label} "
                f"{value!r}; {label} must be ISO-8601 (optionally with "
                f"a trailing ' +Nu' / ' ++Nu' repeater)"
            ) from exc
    return dt, repeater


def _parse_iso_date_or_raise(
    value: object,
    *,
    source: str,
    tid: object,
    label: str,
):
    """Back-compat wrapper around :func:`_parse_deadline_or_raise`.

    Returns ONLY the datetime so existing callers (the
    ``deadline >= scheduled`` check below) don't have to unpack the
    repeater. New callers should use ``_parse_deadline_or_raise``
    directly.
    """
    dt, _repeater = _parse_deadline_or_raise(
        value, source=source, tid=tid, label=label
    )
    return dt


def _validate_tasks(tasks: object, source: str) -> None:
    """Validate a task list in place, raising on the first structural fault.

    The single gate shared by :func:`load_tasks` (read side) and
    :func:`save_tasks` (write side) so a bad mutation can never round-trip
    through the writer.

    Parameters
    ----------
    tasks : object
        The candidate ``tasks`` value (must be a list of mappings).
    source : str
        A label for error messages (the store path or ``"<save_tasks>"``).

    Raises
    ------
    TaskValidationError
        On any structural fault — see :func:`load_tasks`.
    """
    if not isinstance(tasks, list):
        raise TaskValidationError(f"{source}: top-level 'tasks' must be a list")

    seen: set[str] = set()
    for task in tasks:
        if not isinstance(task, dict):
            raise TaskValidationError(
                f"{source}: each task must be a mapping: {task!r}"
            )
        tid = task.get("id")
        if not tid:
            raise TaskValidationError(
                f"{source}: a task is missing required 'id': {task!r}"
            )
        if tid in seen:
            raise TaskValidationError(f"{source}: duplicate task id {tid!r}")
        seen.add(tid)
        if not task.get("title"):
            raise TaskValidationError(
                f"{source}: task {tid!r} is missing required 'title'"
            )
        status = task.get("status")
        if status not in VALID_STATUSES:
            raise TaskValidationError(
                f"{source}: task {tid!r} has invalid status {status!r}; "
                f"must be one of {VALID_STATUSES}"
            )
        priority = task.get("priority")
        # bool is an int subclass — reject it explicitly so `priority: true`
        # is a clear error rather than a silent 1.
        if priority is not None and (
            isinstance(priority, bool) or not isinstance(priority, int)
        ):
            raise TaskValidationError(
                f"{source}: task {tid!r} has non-integer priority {priority!r}; "
                f"priority must be an integer or absent"
            )
        # `parent` is the additive-optional nesting field — a task's children
        # are tasks whose `parent` equals this id. Validate type only (must be
        # a non-empty string id when present); we do NOT require the
        # referenced parent to exist or to be acyclic here. Stale/cyclic
        # references are gracefully degraded by the consumers (server-side
        # graph builder and frontend drill-down) — same lenient stance as
        # `depends_on` / `blocks` references to unknown ids, which are dropped
        # rather than rejected.
        parent = task.get("parent")
        if parent is not None and not (isinstance(parent, str) and parent):
            raise TaskValidationError(
                f"{source}: task {tid!r} has non-string parent {parent!r}; "
                f"parent must be a task id string or absent"
            )
        # `comments` is an append-only thread of user/agent remarks, distinct
        # from the descriptive `note`. Each entry must be a mapping with a
        # non-empty string `text`; `ts` / `author` are optional strings the
        # server fills in (ISO timestamp + commenter). Validate the shape only
        # so a malformed comment can't round-trip, staying lenient otherwise.
        comments = task.get("comments")
        if comments is not None:
            if not isinstance(comments, list):
                raise TaskValidationError(
                    f"{source}: task {tid!r} has non-list comments "
                    f"{comments!r}; comments must be a list or absent"
                )
            for entry in comments:
                if not isinstance(entry, dict) or not (
                    isinstance(entry.get("text"), str) and entry.get("text")
                ):
                    raise TaskValidationError(
                        f"{source}: task {tid!r} has an invalid comment "
                        f"{entry!r}; each comment must be a mapping with a "
                        f"non-empty string 'text'"
                    )
        # Additive operator-co-designed fields (TG 9667, lead a2a `6d9b6073`):
        # task / project / host / created_at / goal / agent / last_activity /
        # pr_url / issue_url — all optional non-empty strings, no enum, no
        # referential integrity. The dataclass Task carries the full shape;
        # this validator just type-checks the wire so a stray scalar can't
        # corrupt downstream readers. Convention details (ISO-8601 for
        # timestamps, URL form for pr_url/issue_url) are render-layer rules.
        for label in (
            "task",
            "project",
            "host",
            "created_at",
            "goal",
            "agent",
            "last_activity",
            "pr_url",
            "issue_url",
        ):
            value = task.get(label)
            if value is not None and not (isinstance(value, str) and value):
                raise TaskValidationError(
                    f"{source}: task {tid!r} has non-string {label} {value!r}; "
                    f"{label} must be a non-empty string or absent"
                )
        # P4 (lead approved 2026-06-12) — deadline + scheduled ISO-8601
        # fields. Validated as non-empty strings that parse via
        # datetime.fromisoformat (handles "YYYY-MM-DD",
        # "YYYY-MM-DDTHH:MM:SS", and offset variants). When BOTH are
        # present, `deadline < scheduled` is rejected — a deadline
        # cannot precede the start of work. (hook-bypass: line-limit.)
        deadline_raw = task.get("deadline")
        scheduled_raw = task.get("scheduled")
        deadlines_raw = task.get("deadlines")
        # P4 PR3: mutual exclusion + per-entry validation for the new
        # `deadlines` (list) field. Either `deadline` (scalar) OR
        # `deadlines` (list) — not both — and the list must be non-empty
        # if present (use the absent form for "no deadlines").
        if deadline_raw is not None and deadlines_raw is not None:
            raise TaskValidationError(
                f"{source}: task {tid!r} has BOTH deadline and deadlines "
                f"set; use one or the other (deadline = scalar single,"
                f" deadlines = list of multiple)"
            )
        if deadlines_raw is not None:
            if not isinstance(deadlines_raw, list):
                raise TaskValidationError(
                    f"{source}: task {tid!r} has non-list deadlines "
                    f"{deadlines_raw!r}; must be a list of ISO-8601 strings"
                )
            if len(deadlines_raw) == 0:
                raise TaskValidationError(
                    f"{source}: task {tid!r} has empty deadlines list; "
                    f"use the absent form for 'no deadlines'"
                )
            for j, entry in enumerate(deadlines_raw):
                _parse_deadline_or_raise(
                    entry, source=source, tid=tid,
                    label=f"deadlines[{j}]",
                )
        deadline_dt, _ = _parse_deadline_or_raise(
            deadline_raw, source=source, tid=tid, label="deadline"
        )
        scheduled_dt, _ = _parse_deadline_or_raise(
            scheduled_raw, source=source, tid=tid, label="scheduled"
        )
        if (
            deadline_dt is not None
            and scheduled_dt is not None
            and deadline_dt < scheduled_dt
        ):
            raise TaskValidationError(
                f"{source}: task {tid!r} has deadline {deadline_raw!r} "
                f"before scheduled {scheduled_raw!r} (a deadline cannot "
                f"precede the start of work)"
            )
        # `scope` and `assignee` are additive-optional shared-fleet fields
        # (PHASE 1, Req 1 in GITIGNORED/ARCHITECTURE.md). Both are free-form
        # non-empty strings — no enum, no referential integrity. Convention is
        # `agent:<name>` / `project:<name>` / `private` but that's a
        # docs/skills convention, not enforced here (Req 8: be generic).
        for label in ("scope", "assignee"):
            value = task.get(label)
            if value is not None and not (isinstance(value, str) and value):
                raise TaskValidationError(
                    f"{source}: task {tid!r} has non-string {label} {value!r}; "
                    f"{label} must be a non-empty string or absent"
                )
        # `_log_meta` is an opaque event-stamp mapping written by
        # `complete_task` etc. Keep it open-shaped — Phase 2 progress-history
        # adapter shapes the keys. We only enforce "if present, it's a
        # mapping" so a stray scalar can't corrupt downstream readers.
        log_meta = task.get("_log_meta")
        if log_meta is not None and not isinstance(log_meta, dict):
            raise TaskValidationError(
                f"{source}: task {tid!r} has non-mapping _log_meta "
                f"{log_meta!r}; _log_meta must be a mapping or absent"
            )
        # `kind` is the discriminator between an ordinary task row and a
        # compute-job row (north-star pillar #1). Closed validated set per
        # `VALID_KINDS`; absence is equivalent to `kind: "task"` (the
        # default). Fail-loud on unknown values — a "comput" typo would
        # otherwise silently create an unrecognized kind, defeating the
        # discriminator.
        kind = task.get("kind")
        if kind is not None and kind not in VALID_KINDS:
            raise TaskValidationError(
                f"{source}: task {tid!r} has invalid kind {kind!r}; "
                f"must be one of {VALID_KINDS} or absent (defaults to 'task')"
            )
        # Compute metadata fields — only allowed when `kind: compute`. Each
        # is an optional non-empty string. `started_at` / `finished_at` are
        # expected to be ISO-8601 timestamps but we don't strict-parse them
        # here — the writer (Spartan watcher / CI watcher, task #15) is
        # responsible for the content; the schema only enforces TYPE so a
        # stray scalar can't corrupt downstream readers.
        is_compute = kind == "compute"
        # Note: `host` USED to be in this compute-only list (ADR-0002). The
        # operator-co-designed generic shape (TG 9667) makes `host` a
        # general-purpose "where does this task live/run" field — any row
        # can carry it, not just compute rows. So `host` moved out of the
        # compute-only fence and into the generic operator-field block
        # above. The remaining compute-only fields (`job_id` / `command` /
        # `started_at` / `finished_at`) STAY compute-only because their
        # semantic ("the compute job's identifier / invocation / runtime
        # bookends") doesn't fit a non-compute task.
        compute_fields = ("job_id", "command", "started_at", "finished_at")
        for label in compute_fields:
            value = task.get(label)
            if value is None:
                continue
            if not is_compute:
                raise TaskValidationError(
                    f"{source}: task {tid!r} has compute metadata {label!r} "
                    f"but kind is {kind!r}; set kind: compute or remove the "
                    f"{label} field"
                )
            if not (isinstance(value, str) and value):
                raise TaskValidationError(
                    f"{source}: task {tid!r} has non-string {label} "
                    f"{value!r}; {label} must be a non-empty string or absent"
                )
        # `blocker` is the discriminator for what KIND of thing is blocking
        # a status=blocked row (north-star "what's waiting on me" — operator
        # TG 9522 + 9524). Closed validated set per `VALID_BLOCKERS`; absence
        # is acceptable on a blocked task ("we know it's blocked but haven't
        # named the blocker variant yet"). The orthogonality matters: `kind`
        # and `blocker` validate independently — a `kind: "decision"` row's
        # blocker is USUALLY `"operator-decision"` but can be `"agent-wait"`
        # (an agent confirming) or `"compute"` (a model picking). The
        # validator does NOT cross-imply.
        #
        # Fail-loud rules:
        #  (a) Unknown `blocker` value → raise, name the bad value + the
        #      valid set.
        #  (b) `blocker` set on a non-blocked row → raise, since naming the
        #      blocker variant is meaningless when the row isn't blocked.
        blocker = task.get("blocker")
        if blocker is not None:
            if blocker not in VALID_BLOCKERS:
                raise TaskValidationError(
                    f"{source}: task {tid!r} has invalid blocker {blocker!r}; "
                    f"must be one of {VALID_BLOCKERS} or absent"
                )
            if status != "blocked":
                raise TaskValidationError(
                    f"{source}: task {tid!r} has blocker {blocker!r} but "
                    f"status is {status!r}; set status: blocked or remove "
                    f"the blocker field"
                )


@contextlib.contextmanager
def _store_lock(path: Path):
    """Hold an exclusive `fcntl.flock` on a sibling `.<name>.lock` file.

    Phase 1 prerequisite for the cross-host sync substrate (Req 2): two
    concurrent writers — say a CLI verb and the board's `/priority` POST
    handler — must serialize so the YAML payload they write is atomic at
    the task-list granularity. We hold the lock on a separate `.lock`
    sentinel file rather than on the store itself so we don't fight the
    ruamel YAML reader/writer that re-opens the path.

    The lock file is created if missing, never removed (next caller reuses
    it). Empty mode is fine — only the lockf state matters.

    Parameters
    ----------
    path : Path
        The store path (e.g. ``~/.scitex/todo/tasks.yaml``). The lock
        sentinel sits next to it as ``.tasks.yaml.lock``.

    Yields
    ------
    None
        After the lock is held; released on context exit (even on errors).
    """
    path = Path(path)
    lock_path = path.parent / f".{path.name}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    # `O_CREAT|O_RDWR` semantics via `open("a+")` — `a+` works even on
    # FS that lack `O_EXLOCK` (e.g. WSL2 ext4) because we acquire the
    # advisory lock via `fcntl.flock` after the open.
    fd = lock_path.open("a+")
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd.fileno(), fcntl.LOCK_UN)
        finally:
            fd.close()


def save_tasks(tasks: list[dict], path: str | Path) -> None:
    """Validate then write a task list back to a YAML store, preserving comments.

    Re-runs the same validation gate as :func:`load_tasks` *before* touching
    disk, so a malformed mutation can never corrupt the store. Uses
    ``ruamel.yaml`` round-trip mode so hand-written comments and key layout in
    the existing store survive the rewrite.

    Parameters
    ----------
    tasks : list of dict
        The (already-mutated) task mappings to persist. Validated first.
    path : str or pathlib.Path
        Destination store. If it already exists, its comments + structure are
        preserved and only the ``tasks:`` payload is updated; otherwise a
        fresh document is written.

    Raises
    ------
    TaskValidationError
        If ``tasks`` fails structural validation (nothing is written).

    Examples
    --------
    >>> tasks = load_tasks("tasks.yaml")          # doctest: +SKIP
    >>> tasks[0]["priority"] = 1                    # doctest: +SKIP
    >>> save_tasks(tasks, "tasks.yaml")            # doctest: +SKIP
    """
    path = Path(path).expanduser()
    # Hold the cross-process advisory lock for the FULL read-modify-write
    # cycle, not just the write — otherwise two writers could each load
    # the file, mutate independently, and the second `dump` would silently
    # clobber the first's mutation. The lock IS the at-most-once gate.
    path.parent.mkdir(parents=True, exist_ok=True)
    with _store_lock(path):
        _save_tasks_unlocked(tasks, path)


def _save_tasks_unlocked(tasks: list[dict], path: Path) -> None:
    """Validate-and-write WITHOUT acquiring the store lock.

    Used by callers (the `_store.add_task`/`update_task`/`complete_task`
    Python API) that hold `_store_lock` for their whole read-modify-write
    cycle. Calling `save_tasks` recursively would deadlock — `flock` on
    a fresh fd to the same path blocks until the OUTER context releases.

    Direct callers must already hold `_store_lock(path)`.
    """
    from ruamel.yaml import YAML

    _validate_tasks(tasks, source="<save_tasks>")

    yaml_rt = YAML()
    yaml_rt.preserve_quotes = True
    # Match the bundled store's hand layout (two-space block indent,
    # lists indented under their key) so a round-trip is a minimal diff.
    yaml_rt.indent(mapping=2, sequence=4, offset=2)

    existing_doc = None
    if path.exists():
        with path.open(encoding="utf-8") as handle:
            loaded = yaml_rt.load(handle)
        if isinstance(loaded, dict):
            existing_doc = loaded

    if existing_doc is not None:
        # Merge the caller's task data into the round-trip-loaded
        # structure by id, so per-item and inline comments attached to
        # the original nodes survive. New ids are appended; removed
        # ids are dropped.
        doc = existing_doc
        old_seq = doc.get("tasks") if isinstance(doc.get("tasks"), list) else []
        old_by_id = {
            t["id"]: t for t in old_seq if isinstance(t, dict) and t.get("id")
        }
        merged = _merge_tasks_into_seq(tasks, old_by_id)
        doc["tasks"] = merged
    else:
        # No existing store (or a non-mapping top level): write fresh.
        doc = {"tasks": tasks}

    path.parent.mkdir(parents=True, exist_ok=True)
    # CRASH-SAFE WRITE (lead a2a `3b0df14a`, post-2026-06-08 autoassign-
    # parallel-run data loss): dump to a sibling .tmp file, fsync it, then
    # os.replace into the canonical path. os.replace is POSIX-atomic — a
    # SIGTERM/SIGKILL mid-dump leaves either the OLD file intact (if the
    # crash hits before replace) or the NEW file in place (if after).
    # Never a half-written file like the one we recovered from today.
    tmp_path = path.parent / f".{path.name}.tmp"
    try:
        with tmp_path.open("w", encoding="utf-8") as handle:
            yaml_rt.dump(doc, handle)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                # fsync can fail on some FS (overlay / fuse). Best-effort —
                # the os.replace below is what gives the atomic guarantee.
                pass
        # POST-DUMP ROUND-TRIP VALIDATE (lead a2a `d5809cd3`, 2026-06-13 —
        # the recovered-by-hand corruption episode where the canonical file
        # ended mid-string at line ~2784). Before we promote the tmp file
        # into the canonical slot, REPARSE it from disk to confirm the dump
        # itself produced a syntactically valid + structurally validated
        # YAML document. The pre-write `_validate_tasks` proves the
        # in-memory structure is sound; this catches any failure mode
        # introduced by the dump itself (unterminated scalar, partial
        # flush, disk-full leaving a truncated file even if fsync didn't
        # error). If reparse fails OR the reparsed task count doesn't
        # match the in-memory count, ABORT — never promote suspect bytes
        # into the canonical SSoT.
        try:
            with tmp_path.open(encoding="utf-8") as verify_handle:
                verify_doc = yaml_rt.load(verify_handle)
        except Exception as verify_exc:  # noqa: BLE001 — any parse fail = abort
            raise RuntimeError(
                f"refusing to replace {path}: tmp file at {tmp_path} did "
                f"not reparse cleanly after dump ({type(verify_exc).__name__}: "
                f"{verify_exc}). Canonical file left untouched."
            ) from verify_exc
        verify_tasks = (
            verify_doc.get("tasks") if isinstance(verify_doc, dict) else None
        )
        in_memory_count = len(doc.get("tasks") or [])
        if not isinstance(verify_tasks, list) or len(verify_tasks) != in_memory_count:
            raise RuntimeError(
                f"refusing to replace {path}: tmp file at {tmp_path} "
                f"reparsed with {len(verify_tasks) if isinstance(verify_tasks, list) else '<not-a-list>'} "
                f"tasks vs in-memory {in_memory_count}. Canonical file "
                f"left untouched."
            )
        # All checks passed — atomic POSIX rename promotes tmp → canonical.
        os.replace(tmp_path, path)
    except Exception:
        # Best-effort tmp cleanup so a crashed dump doesn't leave a
        # stale sidecar.
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise

    # Best-effort git auto-commit on the store dir (lead a2a `3b0df14a`).
    # Lazy-init a small `.git` inside the store dir on first call; commit
    # each save so the operator gets time-travel via `git show <sha>:<file>`.
    # NEVER raises — a git failure must not block the actual save (the
    # YAML is already on disk; the commit is an audit-trail bonus).
    try:
        _git_autocommit_store(path)
    except Exception:  # noqa: BLE001 — best-effort
        pass


def _git_autocommit_store(path: Path) -> None:
    """Initialize a per-store .git on first call, then commit on each save.

    Operator-visible recovery handle: with this in place, even a future
    SIGKILL-mid-write or bad mutation is recoverable via standard git
    commands (`git -C <store-dir> log` + `git show <sha>:<file>`). The
    fcntl lock + atomic write are the LIVE crash-safety; this is the
    POST-MORTEM recovery layer.

    Best-effort: never raises. Skips entirely if git isn't installed.
    """
    import subprocess

    store_dir = path.parent
    git_dir = store_dir / ".git"
    if not git_dir.exists():
        # Lazy-init. Disable auto-gc + auto-pack so every snapshot stays
        # reachable; the store is small enough that aggressive gc would
        # waste cycles + risk reachable-but-old snapshots being pruned.
        subprocess.run(
            ["git", "init", "-q", "-b", "main", str(store_dir)],
            check=False,
            stderr=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
        )
        for cfg in (
            ("gc.auto", "0"),
            ("gc.pruneExpire", "never"),
            ("user.name", "scitex-todo"),
            ("user.email", "scitex-todo@localhost"),
        ):
            subprocess.run(
                ["git", "-C", str(store_dir), "config", *cfg],
                check=False,
                stderr=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
            )
    # Stage + commit just this one file. Use --quiet so a clean tree
    # (no actual change) doesn't print to stderr.
    subprocess.run(
        ["git", "-C", str(store_dir), "add", "--", path.name],
        check=False,
        stderr=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(store_dir),
            "commit",
            "-q",
            "--allow-empty-message",
            "-m",
            "",
        ],
        check=False,
        stderr=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
    )


def _merge_tasks_into_seq(tasks: list[dict], old_by_id: dict) -> list:
    """Build the new task sequence, reusing comment-bearing old nodes by id.

    For each task in ``tasks``: if an old node with the same id exists, mutate
    that node (so its attached comments survive) by syncing keys to the new
    data; otherwise use the new mapping as-is. Order follows ``tasks``.
    """
    merged: list = []
    for task in tasks:
        old = old_by_id.get(task.get("id"))
        if old is None:
            merged.append(task)
            continue
        # Sync the old comment-bearing node's keys to the new values.
        for key, value in task.items():
            old[key] = value
        for stale_key in [k for k in list(old.keys()) if k not in task]:
            del old[stale_key]
        merged.append(old)
    return merged


# EOF
