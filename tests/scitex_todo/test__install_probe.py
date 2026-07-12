#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The version string can lie. These tests prove the probe catches it when it does.

Regression cover for the 2026-07-12 incident: an orphaned ``.dist-info`` froze
``scitex-todo`` at 0.7.26 while the code actually running was 0.8.7 — thirty
releases apart, permanently, with nothing reporting a problem. sac reproduced the
same shape independently in its own container (baked dist-info over bound code).

The load-bearing test here is :func:`test_detects_a_fossilised_dist_info` — a
SYNTHETIC FOSSIL. Testing only against a healthy install would prove nothing:
the whole point is the case where metadata and code disagree.
"""

from __future__ import annotations

import sys
import textwrap

import pytest

from scitex_todo._install_probe import (
    KIND_ABSENT,
    KIND_EDITABLE,
    KIND_ORPHANED,
    KIND_WHEEL,
    check_install_honest,
    probe_install,
)


def _make_source_tree(root, pkg: str, version: str) -> None:
    """A minimal editable-install-shaped source tree: pyproject + package."""
    (root / "pyproject.toml").write_text(
        textwrap.dedent(
            f"""
            [project]
            name = "{pkg.replace('_', '-')}"
            version = "{version}"
            """
        ).strip(),
        encoding="utf-8",
    )
    src = root / "src" / pkg
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("MARKER = True\n", encoding="utf-8")


@pytest.fixture
def fake_pkg(tmp_path, monkeypatch):
    """An importable package living in a source tree (the editable shape)."""
    root = tmp_path / "proj"
    root.mkdir()
    _make_source_tree(root, "fakepkg", "2.0.0")
    monkeypatch.syspath_prepend(str(root / "src"))
    yield root
    sys.modules.pop("fakepkg", None)


# --------------------------------------------------------------------------
# THE BUG THIS EXISTS FOR.
# --------------------------------------------------------------------------


def test_detects_a_fossilised_dist_info(fake_pkg, monkeypatch):
    """Metadata says 1.0.0; the code on disk is 2.0.0. The probe must NOT be fooled.

    This is the incident, reproduced: a .dist-info that outlived the code it
    describes. A version-string check reports 1.0.0 and is confidently wrong.
    """
    monkeypatch.setattr(
        "scitex_todo._install_probe._md.version", lambda dist: "1.0.0"
    )

    p = probe_install("fakepkg")

    assert p.kind == KIND_EDITABLE
    assert p.metadata_version == "1.0.0"  # the fossil
    assert p.code_version == "2.0.0"  # the truth
    assert p.honest is False
    assert p.trustworthy is False, "a drifted version string must never be trusted"
    assert "LIES" in p.detail
    assert "FOSSIL" in (p.hint or "")
    # The hint must name the actual repair, not just complain.
    assert "--no-deps" in (p.hint or "")


def test_agreeing_metadata_is_trustworthy(fake_pkg, monkeypatch):
    """When the metadata matches the source, the version string IS usable."""
    monkeypatch.setattr(
        "scitex_todo._install_probe._md.version", lambda dist: "2.0.0"
    )

    p = probe_install("fakepkg")

    assert p.honest is True
    assert p.trustworthy is True
    assert p.code_version == "2.0.0"


# --------------------------------------------------------------------------
# The other ways an install can be untrustworthy.
# --------------------------------------------------------------------------


def test_orphaned_distinfo_with_no_code_is_the_worst_case(monkeypatch):
    """Metadata present, code absent: every version check "passes" against nothing."""
    monkeypatch.setattr(
        "scitex_todo._install_probe._md.version", lambda dist: "0.7.26"
    )

    p = probe_install("ghostpkg")

    assert p.kind == KIND_ORPHANED
    assert p.trustworthy is False
    assert "ORPHANED" in (p.hint or "")
    assert "force-reinstall" in (p.hint or "")


def test_absent_package_is_not_reported_as_orphaned():
    """No metadata AND no code = simply not installed. NOT a fossil.

    Calling this "orphaned" would send the reader hunting for a .dist-info that
    does not exist — a confidently wrong hint, which is the very disease this
    module treats. (Caught by dogfooding the probe on a nonexistent package.)
    """
    p = probe_install("definitely-not-installed-xyz")

    assert p.kind == KIND_ABSENT
    assert p.trustworthy is False
    assert "not installed" in p.detail
    assert "ORPHANED" not in (p.hint or "")
    assert ".dist-info" not in (p.hint or "")


def test_probe_never_raises_even_when_metadata_blows_up(fake_pkg, monkeypatch):
    """A probe that can crash is a probe that gets wrapped in try/except and ignored."""

    def boom(dist):
        raise RuntimeError("metadata backend exploded")

    monkeypatch.setattr("scitex_todo._install_probe._md.version", boom)

    p = probe_install("fakepkg")  # must not raise

    assert p.probe_error is not None
    assert "exploded" in p.probe_error
    assert p.trustworthy is False, "an unverifiable install is never 'fine'"


def test_unverifiable_is_never_reported_as_honest(fake_pkg, monkeypatch):
    """'I could not check' must never render as 'it is fine'."""
    monkeypatch.setattr(
        "scitex_todo._install_probe._md.version", lambda dist: "1.0.0"
    )
    # Remove the source's version claim -> the code's real version is unknowable.
    (fake_pkg / "pyproject.toml").write_text(
        '[project]\nname = "fakepkg"\n', encoding="utf-8"
    )

    p = probe_install("fakepkg")

    assert p.code_version is None
    assert p.honest is False
    assert p.trustworthy is False
    assert "cannot be confirmed" in p.detail


# --------------------------------------------------------------------------
# Content probing — the check that needs no version at all.
# --------------------------------------------------------------------------


def test_features_probe_the_code_directly_bypassing_versions_entirely(
    fake_pkg, monkeypatch
):
    """The strongest check: does the symbol I expect actually exist?

    This answers "is the code I think I deployed really here?" without trusting
    any version string — the only check a fossil cannot defeat.
    """
    monkeypatch.setattr(
        "scitex_todo._install_probe._md.version", lambda dist: "2.0.0"
    )

    p = probe_install(
        "fakepkg",
        features={
            "present": "fakepkg:MARKER",
            "absent": "fakepkg:NOT_THERE",
            "bad_module": "no_such_module_at_all:X",
        },
    )

    assert p.features == {"present": True, "absent": False, "bad_module": False}


def test_real_scitex_todo_install_is_probeable():
    """Dogfood: probe the package under test. Whatever the shape, it must classify."""
    p = probe_install("scitex-todo")

    assert p.kind in (KIND_WHEEL, KIND_EDITABLE, KIND_ORPHANED, KIND_ABSENT)
    assert p.detail  # never silent
    if not p.trustworthy:
        assert p.hint, "an untrustworthy install MUST carry an actionable hint"


# --------------------------------------------------------------------------
# The health-doctor adapter.
# --------------------------------------------------------------------------


def test_health_check_contract(fake_pkg, monkeypatch):
    """The doctor check returns {ok, detail, hint}; ok is False exactly when lying."""
    monkeypatch.setattr(
        "scitex_todo._install_probe._md.version", lambda dist: "1.0.0"
    )

    res = check_install_honest("fakepkg")

    assert set(res) == {"ok", "detail", "hint"}
    assert res["ok"] is False
    assert res["hint"], "a failing doctor check must always hint at the next step"
