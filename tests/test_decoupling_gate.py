#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S7 decoupling gate: scitex-cards never couples to sac or the telegrammer.

Operator hard rule (さいいんふら, 2026-07-16): scitex-cards must be
independently usable — no single point of failure, no import of
scitex-agent-container (sac) or claude-code-telegrammer, ever. Cross-package
integration happens through PORTS (entry-point groups the OTHER side
registers into; see ``_ports.py``), so the dependency arrow always points
AT this package, never out of it.

Two layers, because each catches what the other cannot:

* the AST scan proves no module TEXT contains a forbidden import — including
  imports hidden inside functions (lazy imports), which a runtime probe only
  catches if the function runs;
* the runtime probe proves the exercised CRUD surface loads none of the
  forbidden modules as a side effect — including dynamic ``__import__``
  tricks the AST scan cannot see.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "scitex_cards"

#: Root module names scitex-cards must NEVER import. ``scitex_todo`` is
#: deliberately absent: that is our own shim, not a foreign package.
FORBIDDEN_ROOTS = {
    "scitex_agent_container",
    "claude_code_telegrammer",
    "sac",
}


def _imported_roots(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name.split(".")[0], node.lineno
        elif isinstance(node, ast.ImportFrom):
            # level>0 = relative import — inside this package by definition.
            if node.level == 0 and node.module:
                yield node.module.split(".")[0], node.lineno


def test_no_module_imports_sac_or_telegrammer_anywhere():
    """AST scan of every source module — comments/docstrings cannot fool it."""
    # Arrange
    offenders = []
    # Act
    for py in sorted(SRC.rglob("*.py")):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for root, lineno in _imported_roots(tree):
            if root in FORBIDDEN_ROOTS:
                offenders.append(f"{py.relative_to(SRC)}:{lineno} imports {root}")
    # Assert
    assert not offenders, (
        "scitex-cards must stay decoupled (operator hard rule); "
        "forbidden imports found:\n" + "\n".join(offenders)
    )


def test_crud_surface_runs_without_loading_forbidden_modules(tmp_path, monkeypatch):
    """Exercise the real store surface; no forbidden module may load laterally."""
    # Arrange — hermetic env: pin BOTH the store and the identity, so the
    # test neither reads a live store nor silently depends on the agent id
    # of whoever runs it (it passed locally and failed on CI for exactly
    # that reason: add_task resolves its creator from the env).
    monkeypatch.setenv(
        "SCITEX_TODO_TASKS_YAML_SHARED", str(tmp_path / "tasks.yaml")
    )
    monkeypatch.setenv("SCITEX_TODO_AGENT_ID", "decoupling-gate-test")
    from scitex_cards import _store

    # Act — a representative write/read/mutate cycle.
    _store.add_task(
        tmp_path / "tasks.yaml", id="t", title="t", status="deferred", agent="a"
    )
    _store.list_tasks(tmp_path / "tasks.yaml")
    _store.comment_task(tmp_path / "tasks.yaml", "t", "standalone", by="a")
    _store.complete_task(tmp_path / "tasks.yaml", "t", by="a")
    # Assert
    loaded = {m.split(".")[0] for m in sys.modules}
    hit = loaded & FORBIDDEN_ROOTS
    assert not hit, f"CRUD surface lazily loaded forbidden module(s): {hit}"
