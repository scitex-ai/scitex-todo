#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression tests: notifyd liveness ACROSS a PID-namespace boundary.

THE BUG (observed live, 2026-07-13). notifyd runs on the BARE HOST under
systemd; fleet agents run in CONTAINERS that share the task store — and hence
the pidfile — through a bind-mount, but in a DIFFERENT PID namespace. The health
check read the host's pid out of the shared pidfile and probed it with
``os.kill(pid, 0)``. The host's pid does not exist in the container's ``/proc``,
so the probe raised ``ProcessLookupError`` and health reported::

    stale notifyd pidfile: pid 3285778 is not running

while that very pid was alive and ticking on the host. **A pid is only
meaningful inside the PID namespace that issued it.** A check that reports
FAILED on a healthy system, permanently, trains its reader to ignore the
channel — so when the daemon really dies, nobody looks.

Note the hostname is NOT a usable discriminator on this fleet: apptainer shares
the UTS namespace, so ``socket.gethostname()`` inside the container returns the
SAME name as the bare host. The namespace inode (``/proc/self/ns/pid``) is the
identity that actually issues pids, and it is what the fix compares.

NO mocks (STX-NM / PA-306): real pidfiles on a real ``tmp_path``, a real dead
pid obtained by really running and reaping a real subprocess, our own real live
pid, and real identity dicts.
"""

from __future__ import annotations

import datetime as _dt
import os
import subprocess
import sys

from scitex_todo._delivery import _pidfile
from scitex_todo._delivery._pidfile import (
    assess_liveness,
    local_identity,
    parse,
    render,
    writer_is_local,
)

NOW = _dt.datetime(2026, 7, 13, 12, 0, 0, tzinfo=_dt.timezone.utc)


# --------------------------------------------------------------------------- #
# helpers — real processes, real files, no mocks                             #
# --------------------------------------------------------------------------- #
def _really_dead_pid() -> int:
    """Run a real process to completion and REAP it — its pid is truly gone."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _foreign_identity() -> dict[str, str]:
    """Identity of a DIFFERENT PID namespace — same hostname on purpose.

    Same host, same boot id, different namespace: exactly the apptainer shape
    that defeats a hostname-only check.
    """
    mine = local_identity()
    return {
        "host": mine["host"],
        "boot_id": mine["boot_id"],
        "pid_ns": "pid:[4026531836]-not-ours",
        "container": "0",
    }


def _write_pidfile(dirpath, pid, *, identity, heartbeat, interval=120.0):
    dirpath.mkdir(parents=True, exist_ok=True)
    path = dirpath / "notifyd.pid"
    body = render(pid, interval=interval, now=heartbeat, identity=identity)
    path.write_text(body, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# THE false-negative — a foreign, FRESH pidfile is ALIVE                      #
# --------------------------------------------------------------------------- #
def test_foreign_namespace_with_fresh_heartbeat_is_alive(tmp_path):
    """The exact live false-negative. It must never regress.

    The pid does NOT exist here (really dead), yet the daemon that wrote it is
    ticking in another namespace. Freshness, not identity, decides.
    """
    pidfile = _write_pidfile(
        tmp_path,
        _really_dead_pid(),  # unresolvable in OUR namespace...
        identity=_foreign_identity(),  # ...because it was never OUR pid.
        heartbeat=NOW - _dt.timedelta(seconds=30),  # ticking (interval 120s)
    )

    verdict = assess_liveness(pidfile, now=NOW)

    assert verdict["ok"] is True
    assert verdict["state"] == "alive"
    assert verdict["hint"] is None
    # And it says WHY it did not trust the pid.
    assert "NOT probed" in verdict["detail"]
    assert "stale" not in verdict["detail"].lower()


def test_same_hostname_different_namespace_is_not_local(tmp_path):
    """Hostname alone would have MISSED this: apptainer shares the UTS namespace."""
    foreign = _foreign_identity()
    assert foreign["host"] == local_identity()["host"]  # same name, other namespace
    record = parse(render(1234, now=NOW, identity=foreign))
    assert writer_is_local(record) is False


# --------------------------------------------------------------------------- #
# still FAIL-LOUD — a foreign daemon that STOPPED ticking is dead            #
# --------------------------------------------------------------------------- #
def test_foreign_namespace_with_stale_heartbeat_is_dead_with_hint(tmp_path):
    """A real death across the boundary is still caught, loudly and actionably.

    We deliberately stamp a pid that IS alive here (our own) — so if the check
    ever fell back to probing the pid it would wrongly say "alive". The stale
    heartbeat must decide.
    """
    pidfile = _write_pidfile(
        tmp_path,
        os.getpid(),  # alive in OUR namespace — must not be consulted
        identity=_foreign_identity(),
        heartbeat=NOW - _dt.timedelta(hours=2),
        interval=120.0,
    )

    verdict = assess_liveness(pidfile, now=NOW)

    assert verdict["ok"] is False
    assert verdict["state"] == "dead"
    assert "STALE" in verdict["detail"]
    assert verdict["hint"], "a failing check must carry an actionable hint"
    assert "restart" in verdict["hint"]


def test_staleness_budget_is_a_multiple_of_the_recorded_tick_interval(tmp_path):
    """One slow/missed tick is tolerated; a stopped daemon is not."""
    ident = _foreign_identity()
    # interval=600 => budget 1800s. 900s old is one-and-a-half ticks: ALIVE.
    fresh = _write_pidfile(
        tmp_path / "a",
        4242,
        identity=ident,
        heartbeat=NOW - _dt.timedelta(seconds=900),
        interval=600.0,
    )
    stale = _write_pidfile(
        tmp_path / "b",
        4242,
        identity=ident,
        heartbeat=NOW - _dt.timedelta(seconds=3600),
        interval=600.0,
    )
    assert assess_liveness(fresh, now=NOW)["state"] == "alive"
    assert assess_liveness(stale, now=NOW)["state"] == "dead"


# --------------------------------------------------------------------------- #
# LOCAL namespace — the precise pid probe is preserved                       #
# --------------------------------------------------------------------------- #
def test_local_dead_daemon_is_still_dead_even_with_a_fresh_heartbeat(tmp_path):
    """Fail-loud preserved: in OUR namespace the pid probe is authoritative.

    A daemon that crashed a second after its last stamp leaves a FRESH
    heartbeat behind. Freshness must not paper over a corpse we can actually see.
    """
    pidfile = _write_pidfile(
        tmp_path,
        _really_dead_pid(),
        identity=local_identity(),
        heartbeat=NOW - _dt.timedelta(seconds=1),
    )

    verdict = assess_liveness(pidfile, now=NOW)

    assert verdict["ok"] is False
    assert verdict["state"] == "dead"
    assert "is not running" in verdict["detail"]
    assert "scitex-todo notifyd" in verdict["hint"]


def test_local_live_daemon_is_alive(tmp_path):
    pidfile = _write_pidfile(
        tmp_path,
        os.getpid(),
        identity=local_identity(),
        heartbeat=NOW - _dt.timedelta(seconds=5),
    )
    verdict = assess_liveness(pidfile, now=NOW)
    assert verdict["ok"] is True
    assert verdict["state"] == "alive"


def test_missing_pidfile_is_not_running(tmp_path):
    verdict = assess_liveness(tmp_path / "notifyd.pid", now=NOW)
    assert verdict["ok"] is False
    assert verdict["state"] == "not_running"
    assert "install-unit" in verdict["hint"]


# --------------------------------------------------------------------------- #
# LEGACY pidfiles (bare pid, pre-heartbeat notifyd)                           #
# --------------------------------------------------------------------------- #
def test_legacy_bare_pidfile_alive_pid_is_alive(tmp_path):
    path = tmp_path / "notifyd.pid"
    path.write_text(f"{os.getpid()}\n", encoding="utf-8")
    verdict = assess_liveness(path, now=NOW)
    assert verdict["ok"] is True
    assert verdict["state"] == "alive"


def test_legacy_bare_pidfile_dead_pid_fails_loud_but_names_the_ambiguity(tmp_path):
    """No stamp = no way to know. Stay loud, but say the verdict may be an artefact."""
    path = tmp_path / "notifyd.pid"
    path.write_text(f"{_really_dead_pid()}\n", encoding="utf-8")

    verdict = assess_liveness(path, now=NOW)

    assert verdict["ok"] is False
    assert "no host/namespace stamp" in verdict["detail"]
    # The hint must tell the reader how to make the check trustworthy.
    assert "FALSE" in verdict["hint"]
    assert "Upgrade" in verdict["hint"]


# --------------------------------------------------------------------------- #
# format — backward compatible + round-trips                                  #
# --------------------------------------------------------------------------- #
def test_first_token_is_still_the_bare_pid(tmp_path):
    """Any old reader doing ``int(text.split()[0])`` must keep working."""
    body = render(4242, now=NOW)
    assert int(body.split()[0]) == 4242


def test_render_parse_round_trip():
    ident = local_identity()
    record = parse(render(99, interval=42.0, now=NOW, identity=ident))
    assert record["pid"] == 99
    assert record["host"] == ident["host"]
    assert record["pid_ns"] == ident["pid_ns"]
    assert record["boot_id"] == ident["boot_id"]
    assert float(record["interval"]) == 42.0
    assert _pidfile.heartbeat_age_seconds(record, NOW) == 0.0
    assert writer_is_local(record, ident) is True


def test_naive_heartbeat_stamp_does_not_explode():
    """A naive stamp meeting an aware `now` must not raise (a known repo trap)."""
    record = {"heartbeat": "2026-07-13T11:59:00"}
    assert _pidfile.heartbeat_age_seconds(record, NOW) == 60.0


def test_unparseable_pid_with_fresh_heartbeat_is_alive(tmp_path):
    path = tmp_path / "notifyd.pid"
    path.write_text(
        f"garbage\nhost=elsewhere\npid_ns=pid:[1]\nheartbeat={NOW.isoformat()}\n",
        encoding="utf-8",
    )
    verdict = assess_liveness(path, now=NOW)
    assert verdict["ok"] is True
    assert verdict["state"] == "alive"


# EOF
