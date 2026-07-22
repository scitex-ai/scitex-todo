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
* the runtime probe proves the exercised CRUD surface still WORKS when those
  packages are absent — the no-single-point-of-failure half of the rule,
  which no amount of source reading can establish.

The runtime probe deliberately does NOT assert "no forbidden module is in
``sys.modules``". A port PROVIDER loading is the design succeeding, not
failing: sac registers a handler into our ``scitex_todo.hooks`` group, so a
write legitimately imports ``scitex_agent_container`` via ``ep.load()``. The
arrow still points at us — we never name it. Asserting on ``sys.modules``
flagged that as a violation and failed on correct code (2026-07-22).
"""

from __future__ import annotations

import ast
import contextlib
import importlib
import importlib.abc
import sys
from pathlib import Path

import pytest

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


class _ForbiddenModuleBlocker(importlib.abc.MetaPathFinder):
    """Make every forbidden root UNIMPORTABLE, as if it were not installed."""

    def find_spec(self, fullname, path=None, target=None):  # noqa: ARG002
        if fullname.split(".")[0] in FORBIDDEN_ROOTS:
            raise ImportError(
                f"{fullname} is deliberately unavailable: the decoupling gate "
                f"removed it to prove scitex-cards runs without it"
            )
        return None


@contextlib.contextmanager
def _forbidden_roots_uninstallable():
    """Simulate a machine where sac/the telegrammer are simply not present.

    Both halves matter: the finder blocks FUTURE imports, and purging
    ``sys.modules`` defeats the cache so an already-loaded copy cannot satisfy
    one. Everything is restored on exit — other tests share this process.
    """
    blocker = _ForbiddenModuleBlocker()
    stashed = {
        name: mod
        for name, mod in sys.modules.items()
        if name.split(".")[0] in FORBIDDEN_ROOTS
    }
    for name in stashed:
        del sys.modules[name]
    sys.meta_path.insert(0, blocker)
    try:
        yield
    finally:
        sys.meta_path.remove(blocker)
        sys.modules.update(stashed)


def test_crud_surface_survives_absence_of_forbidden_modules(tmp_path, monkeypatch):
    """The CRUD surface must complete with sac/the telegrammer UNINSTALLABLE.

    WHY THIS, AND NOT "no forbidden module is in ``sys.modules``" (the shape
    this test had until 2026-07-22): that assertion measured the wrong thing
    and failed on correct code. ``sac`` registers a card-event delivery
    handler into our ``scitex_todo.hooks`` entry-point group, so the FIRST
    write loads ``scitex_agent_container`` through ``ep.load()`` — the PORTS
    mechanism working exactly as designed, with the dependency arrow still
    pointing at us (we never name sac; the AST scan above proves that, and it
    is the layer that enforces the "never import" half of the rule).

    The property the operator's rule actually protects is NO SINGLE POINT OF
    FAILURE: scitex-cards must work when those packages are absent. So assert
    that directly. This is strictly stronger than the old probe — it fails if
    any code path hard-requires a forbidden package, including one that only
    reveals itself when the import raises (e.g. if
    ``_hooks._plugins``'s ``except Exception`` around ``ep.load()`` were ever
    narrowed or removed).
    """
    # Arrange — hermetic env: pin BOTH the store and the identity, so the
    # test neither reads a live store nor silently depends on the agent id
    # of whoever runs it (it passed locally and failed on CI for exactly
    # that reason: add_task resolves its creator from the env).
    #
    # BOTH identity variables, because ``_env_compat`` gives the
    # ``SCITEX_CARDS_*`` name precedence: pinning only the ``SCITEX_TODO_*``
    # one leaves the test reading the runner's real agent id whenever
    # ``SCITEX_CARDS_AGENT_ID`` is exported — which it is for every agent in
    # this fleet, so the pin was silently inert exactly where it was needed.
    monkeypatch.setenv("SCITEX_TODO_AGENT_ID", "decoupling-gate-test")
    monkeypatch.setenv("SCITEX_CARDS_AGENT_ID", "decoupling-gate-test")
    store = tmp_path / "tasks.yaml"
    from scitex_cards import _store

    # Act + Assert — the cycle itself is the assertion: any hard requirement
    # on a forbidden package surfaces here as an ImportError.
    with _forbidden_roots_uninstallable():
        # A gate that cannot fail is not a gate: prove the blocker BITES,
        # otherwise a broken blocker would make everything below vacuous.
        for root in sorted(FORBIDDEN_ROOTS):
            with pytest.raises(ImportError):
                importlib.import_module(root)

        _store.add_task(store, id="t", title="t", status="deferred", agent="a")
        _store.list_tasks(store)
        _store.comment_task(store, "t", "standalone", by="a")
        _store.complete_task(store, "t", by="a")

        # Nothing may have slipped back in through a cached reference.
        leaked = {m.split(".")[0] for m in sys.modules} & FORBIDDEN_ROOTS
        assert not leaked, f"forbidden module(s) loaded despite blocker: {leaked}"
