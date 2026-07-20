#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI noun group ``scitex-cards dev`` — the maintainer surface.

Doctrine §11 (``18_dev-subgroup-and-ecosystem-placement.md``): every package
CLI groups its developer/maintainer-facing commands under one ``dev`` noun, so
that ``<cli> --help`` shows the DOMAIN surface and an agent auditing any
package knows to look in exactly one place for the plumbing.

The tie-break the doctrine gives: a command an END USER runs to *use* the
package never goes under ``dev``; a command only a package developer or
maintainer runs always does. By that test:

* ``list-python-apis`` — introspecting this package's own Python API.
* ``skills``           — export / install the bundled agent skills.
* ``migration``        — the directory-card enforcement migration (plan/apply).

``list-python-apis`` is ALSO left mounted at the top level, not aliased away:
the §1a auditor still requires the legacy top-level name (07_audit-cli.md
records this explicitly — "the auditor still checks the legacy names until
slice 4"). One command object, two mount points; nothing is duplicated.
``skills`` and ``migration`` take the §11 migration path instead — hidden
Phase-W aliases at the top level, forwarding to the ``dev``-nested form.
"""

from __future__ import annotations

import click

from ._compat import deprecated_path_alias, spec_group_kwargs

#: Version that removes the Phase-W top-level aliases (doctrine §5).
_REMOVE_IN = "0.20.0"


@click.group(
    "dev",
    **spec_group_kwargs(
        summary="Maintainer-facing commands (doctrine §11 `dev` subgroup).",
        description=(
            "Plumbing a package DEVELOPER runs, folded out of the "
            "user-facing top level: Python-API introspection, the bundled "
            "agent skills, and the store migration verbs.",
        ),
        command_categories=(
            ("Introspection", ("list-python-apis", "skills")),
            ("Migration", ("migration",)),
        ),
    ),
)
def dev_group() -> None:
    """The ``dev`` noun group."""


def register(main: click.Group) -> None:
    """Mount the ``dev`` group and re-home the maintainer commands under it.

    Called AFTER the sibling modules have registered their commands on
    ``main``, so the command objects already exist and are simply re-parented
    (``skills`` / ``migration``) or dual-mounted (``list-python-apis``).
    """
    ctx = click.Context(main)

    # Dual-mount: `dev list-python-apis` is canonical, the top-level name stays
    # visible because the §1a auditor still requires it there.
    api_cmd = main.get_command(ctx, "list-python-apis")
    if api_cmd is not None:
        dev_group.add_command(api_cmd, "list-python-apis")

    # Re-home: the top-level name becomes a hidden Phase-W alias.
    for name in ("skills", "migration"):
        cmd = main.get_command(ctx, name)
        if cmd is None:
            continue
        dev_group.add_command(cmd, name)
        main.commands.pop(name, None)
        deprecated_path_alias(main, name, path=("dev", name), remove_in=_REMOVE_IN)

    main.add_command(dev_group)


# EOF
