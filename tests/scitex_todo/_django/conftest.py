#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Configure Django for the _django board tests (real settings, no mocks).

Skips the whole _django test package cleanly when Django is not installed
(the web extra is optional), so the core suite still runs on a lean install.
"""

from __future__ import annotations

import pytest

django = pytest.importorskip("django")


def pytest_configure(config):  # noqa: ARG001
    """Point Django at the standalone board settings and call setup() once."""
    import os

    from django.conf import settings

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "scitex_todo._django.settings")
    if not settings.configured:
        django.setup()
