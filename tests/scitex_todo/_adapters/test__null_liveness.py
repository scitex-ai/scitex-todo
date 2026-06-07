#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mirror-dir smoke test for ``_adapters._null_liveness``.

See ``test__in_process_pubsub.py`` for the mirror-dir rationale.
"""

from __future__ import annotations


def test_null_liveness_is_importable():
    # Arrange — no setup; the import is the contract under test.
    # Act
    from scitex_todo._adapters._null_liveness import NullLiveness

    # Assert
    assert callable(NullLiveness)
