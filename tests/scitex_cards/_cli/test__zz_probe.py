from __future__ import annotations

from click.testing import CliRunner

from scitex_cards._cli import main
from scitex_cards._store import add_task, get_task, update_task


def test_probe_clear_timestamps():
    add_task(
        id="no-timestamp-card",
        title="undated pending card",
        status="deferred",
        project="business",
        assignee="proj-scitex-lead",
    )
    try:
        update_task(task_id="no-timestamp-card", created_at="", last_activity="")
    except Exception as exc:  # noqa: BLE001
        print("CLEAR_TS_RAISED", repr(exc))
    print("CARD", get_task(task_id="no-timestamp-card"))

    add_task(id="vague-card", title="tbd", status="deferred", assignee="x")
    try:
        update_task(
            task_id="vague-card",
            assignee="",
            agent="",
            created_at="",
            last_activity="",
        )
    except Exception as exc:  # noqa: BLE001
        print("CLEAR_OWNER_RAISED", repr(exc))
    print("VAGUE", get_task(task_id="vague-card"))

    result = CliRunner().invoke(main, ["list-stale"])
    print("OUT", result.output)
    assert False
