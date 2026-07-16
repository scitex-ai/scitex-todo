#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Default :class:`IdentityACLPort` — everyone can do everything.

Acceptable for single-user standalone installs. The fleet's sac-fleet-
groups ACL adapter (out of package) replaces this with real group-
gated authority when task #2 (``e1-sac-fleet-acl``) lands.
"""

from __future__ import annotations


class OpenACL:
    """All-allow :class:`scitex_cards._ports.IdentityACLPort` implementation.

    Both :meth:`can_read` and :meth:`can_write` unconditionally return
    True. Substitutable for the fleet's group-gated adapter without
    touching core code.

    Examples
    --------
    >>> acl = OpenACL()
    >>> acl.can_read("anyone", {})
    True
    >>> acl.can_write("anyone", {}, "any-field")
    True
    """

    def can_read(self, actor: str, task: dict) -> bool:  # noqa: ARG002
        return True

    def can_write(
        self, actor: str, task: dict, field: str  # noqa: ARG002
    ) -> bool:
        return True
