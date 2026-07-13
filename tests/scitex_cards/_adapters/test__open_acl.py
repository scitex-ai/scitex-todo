#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mirror-dir smoke test for ``_adapters._open_acl``.

See ``test__in_process_pubsub.py`` for the mirror-dir rationale.
"""

from __future__ import annotations


def test_open_acl_is_importable():
    # Arrange — no setup; the import is the contract under test.
    # Act
    from scitex_cards._adapters._open_acl import OpenACL

    # Assert
    assert callable(OpenACL)
