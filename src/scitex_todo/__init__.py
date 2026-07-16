#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deprecated import alias: ``scitex_todo`` -> :mod:`scitex_cards`.

The package was renamed 2026-07-16 (operator directive). This shim keeps
``import scitex_todo`` and ``import scitex_todo.<submodule>`` working for one
transition window by aliasing BOTH to the very same module objects as their
``scitex_cards`` counterparts — one import, one module state, never a second
copy (a duplicate module execution would fork singletons: locks, caches,
entry-point registries).

Mechanism: this module replaces itself in ``sys.modules`` with the canonical
package and installs a meta-path finder that resolves any ``scitex_todo.X``
import to the already-imported ``scitex_cards.X``.
"""

from __future__ import annotations

import importlib
import importlib.abc
import importlib.util
import sys
import warnings

import scitex_cards as _canonical

_OLD = "scitex_todo"
_NEW = "scitex_cards"

warnings.warn(
    "the 'scitex_todo' import name was renamed to 'scitex_cards' "
    "(2026-07-16); this alias ships for one transition window only — "
    "switch imports to 'scitex_cards'",
    DeprecationWarning,
    stacklevel=2,
)


class _AliasLoader(importlib.abc.Loader):
    """Hand the import system the ALREADY-imported canonical module."""

    def __init__(self, real_name: str) -> None:
        self._real_name = real_name

    def create_module(self, spec):
        return importlib.import_module(self._real_name)

    def exec_module(self, module) -> None:
        """The canonical module is already executed; nothing to run."""


class _AliasFinder(importlib.abc.MetaPathFinder):
    """Resolve ``scitex_todo`` / ``scitex_todo.*`` to ``scitex_cards`` twins."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname != _OLD and not fullname.startswith(_OLD + "."):
            return None
        real_name = _NEW + fullname[len(_OLD) :]
        return importlib.util.spec_from_loader(
            fullname, _AliasLoader(real_name), is_package=True
        )


# One finder is enough; match by name so a re-executed shim never stacks a
# second copy (class objects differ between executions of this file).
if not any(type(f).__name__ == "_AliasFinder" for f in sys.meta_path):
    sys.meta_path.insert(0, _AliasFinder())

# Replace THIS module with the canonical package: after `import scitex_todo`,
# ``scitex_todo is scitex_cards`` holds and every attribute (including
# already-imported submodules) resolves identically under both names.
sys.modules[_OLD] = _canonical

# EOF
