#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Mirror-dir smoke test for ``_adapters._in_process_pubsub``.

Satisfies PS-202 (src-tests-mirror-dir) for the adapters tree. The
adapter's wider behavioural coverage (literal channel match, suffix
glob, idempotent subscribe, handler-exception isolation) lives in
``tests/scitex_todo/test__ports_and_adapters.py`` so the contracts are
exercised against ALL the bundled adapters in one place. This file
keeps the mirror requirement satisfied without duplicating coverage.
"""

from __future__ import annotations


def test_in_process_pubsub_is_importable():
    # Arrange — no setup; the import is the contract under test.
    # Act
    from scitex_todo._adapters._in_process_pubsub import InProcessPubSub

    # Assert
    assert callable(InProcessPubSub)
