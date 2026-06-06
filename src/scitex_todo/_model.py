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
    "dep",
    "operator-decision",
    "agent-wait",
)


class TaskValidationError(ValueError):
    """Raised when a task store fails structural validation."""


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
        compute_fields = ("job_id", "host", "command", "started_at", "finished_at")
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
    with path.open("w", encoding="utf-8") as handle:
        yaml_rt.dump(doc, handle)


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
