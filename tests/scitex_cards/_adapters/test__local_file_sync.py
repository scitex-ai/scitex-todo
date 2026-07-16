#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mirror-dir smoke test for ``_adapters._local_file_sync``.

See ``test__in_process_pubsub.py`` for the mirror-dir rationale. The
adapter's behavioural coverage lives in
``tests/scitex_cards/test__ports_and_adapters.py``.
"""

from __future__ import annotations


def test_local_file_sync_is_importable():
    # Arrange — no setup; the import is the contract under test.
    # Act
    from scitex_cards._adapters._local_file_sync import LocalFileSync

    # Assert
    assert callable(LocalFileSync)
