"""Audit conformance — runs `scitex-dev ecosystem audit-all`
on this package as a normal pytest test.

Wires `scitex-dev ecosystem audit-all scitex-todo` into the pytest suite so
audit drift surfaces in the existing `tests` workflow — no separate audit
workflow needed. The test skips cleanly when scitex-dev is not installed (the
`[dev]` extra is optional) or when SCITEX_DEV_SKIP_AUDIT=1 is set (the
documented remediation bypass).
"""

import shutil

import pytest


def test_audit_all_clean():
    if shutil.which("scitex-dev") is None:
        pytest.skip(
            "scitex-dev not installed — add `scitex-dev[cli-audit]` "
            "to [project.optional-dependencies.dev]"
        )
    from scitex_dev.testing import audit_all_for_package

    audit_all_for_package("scitex-todo")
