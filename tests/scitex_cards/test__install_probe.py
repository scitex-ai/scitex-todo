#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The version string can lie. These tests prove the probe catches it when it does.

Regression cover for the 2026-07-12 incident: an orphaned ``.dist-info`` froze
``scitex-cards`` at 0.7.26 while the code actually running was 0.8.7 — thirty
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

from scitex_cards._install_probe import (
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
        "scitex_cards._install_probe._md.version", lambda dist: "1.0.0"
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
        "scitex_cards._install_probe._md.version", lambda dist: "2.0.0"
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
        "scitex_cards._install_probe._md.version", lambda dist: "0.7.26"
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

    monkeypatch.setattr("scitex_cards._install_probe._md.version", boom)

    p = probe_install("fakepkg")  # must not raise

    assert p.probe_error is not None
    assert "exploded" in p.probe_error
    assert p.trustworthy is False, "an unverifiable install is never 'fine'"


def test_unverifiable_is_never_reported_as_honest(fake_pkg, monkeypatch):
    """'I could not check' must never render as 'it is fine'."""
    monkeypatch.setattr(
        "scitex_cards._install_probe._md.version", lambda dist: "1.0.0"
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
        "scitex_cards._install_probe._md.version", lambda dist: "2.0.0"
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


def test_real_scitex_cards_install_is_probeable():
    """Dogfood: probe the package under test. Whatever the shape, it must classify."""
    p = probe_install("scitex-cards")

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
        "scitex_cards._install_probe._md.version", lambda dist: "1.0.0"
    )

    res = check_install_honest("fakepkg")

    assert set(res) == {"ok", "detail", "hint"}
    assert res["ok"] is False
    assert res["hint"], "a failing doctor check must always hint at the next step"


# --------------------------------------------------------------------------
# The THIRD failure mode: disk truth is not process truth.
# --------------------------------------------------------------------------


def test_features_interrogate_the_LOADED_module_not_the_disk(fake_pkg, monkeypatch):
    """The stale-in-memory detector. Found by scitex-dev, 2026-07-12.

    A long-lived process holds module objects in ``sys.modules``. Upgrading the
    files on disk does NOT touch them — so a server can serve stale code while
    its disk, its .dist-info, and this probe all report a current install, each
    of them truthfully, all of them answering the wrong question.

    ``features`` is the ONLY check that sees through this, because ``hasattr``
    reads the LOADED module. Here: the source on disk gains a symbol AFTER the
    module is already imported, and the feature probe correctly reports it ABSENT
    — exactly as it would in a server running pre-upgrade code.
    """
    import fakepkg  # noqa: F401  - force it into sys.modules ("the running process")

    monkeypatch.setattr(
        "scitex_cards._install_probe._md.version", lambda dist: "2.0.0"
    )
    # The DISK now grows a symbol the loaded module does not have — the exact
    # shape of "someone pip-upgraded under a running server".
    (fake_pkg / "src" / "fakepkg" / "__init__.py").write_text(
        "MARKER = True\nPOST_UPGRADE_SYMBOL = True\n", encoding="utf-8"
    )

    p = probe_install("fakepkg", features={"post_upgrade": "fakepkg:POST_UPGRADE_SYMBOL"})

    # Every DISK-level signal says the install is fine...
    assert p.trustworthy is True
    assert p.code_version == "2.0.0"
    # ...but the PROCESS does not have the new code, and only the symbol probe
    # can tell us. This is the finding: a passing disk check is NOT a healthy
    # process, and the remedy here is a RESTART, not an upgrade.
    assert p.features["post_upgrade"] is False


# --------------------------------------------------------------------------
# THE PROBE'S OWN BLIND SPOT — found by the probe FAILING on a live install.
# --------------------------------------------------------------------------


@pytest.fixture
def wheel_pkg_with_two_distinfos(tmp_path, monkeypatch):
    """A site-packages layout with TWO .dist-info dirs for one package.

    The exact shape observed on 2026-07-12: upgrading 0.7.50 -> 0.9.0 in the agent
    venv left the old dist-info sitting next to the new one.
    """
    site = tmp_path / "site-packages"
    pkg = site / "wheelpkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("NEW_SYMBOL = True\n", encoding="utf-8")
    (site / "wheelpkg-0.9.0.dist-info").mkdir()
    (site / "wheelpkg-0.7.50.dist-info").mkdir()  # the orphaned fossil
    monkeypatch.syspath_prepend(str(site))
    yield site
    sys.modules.pop("wheelpkg", None)


def test_two_distinfos_make_the_version_UNTRUSTWORTHY(
    wheel_pkg_with_two_distinfos, monkeypatch
):
    """The bug this probe SHIPPED with, and which it failed to catch on itself.

    A wheel install "usually" means the metadata arrived with the code beside it —
    so the original code set trustworthy=True and code_version=<metadata>. With TWO
    dist-infos that is a COIN TOSS: importlib.metadata returns whichever it finds
    first, which on the live box was the 0.7.50 FOSSIL while the code was 0.9.0.

    The probe reported trustworthy=True and code_version=0.7.50. Both wrong. It
    blessed a lying install — the precise failure it exists to prevent.
    """
    # metadata picks the FOSSIL, exactly as it did live
    monkeypatch.setattr(
        "scitex_cards._install_probe._md.version", lambda dist: "0.7.50"
    )

    p = probe_install("wheelpkg")

    assert p.kind == KIND_WHEEL
    assert p.trustworthy is False, "an AMBIGUOUS version must never be trusted"


def test_two_distinfos_refuse_to_guess_a_code_version(
    wheel_pkg_with_two_distinfos, monkeypatch
):
    """"I cannot tell which is real" must not render as a confident number."""
    monkeypatch.setattr(
        "scitex_cards._install_probe._md.version", lambda dist: "0.7.50"
    )

    p = probe_install("wheelpkg")

    assert p.code_version is None


def test_two_distinfos_name_both_and_the_repair(
    wheel_pkg_with_two_distinfos, monkeypatch
):
    monkeypatch.setattr(
        "scitex_cards._install_probe._md.version", lambda dist: "0.7.50"
    )

    p = probe_install("wheelpkg")

    assert "AMBIGUOUS" in p.detail
    assert "0.9.0" in p.detail and "0.7.50" in p.detail
    assert "rm -rf" in (p.hint or "")  # names the actual repair


def test_the_symbol_probe_still_tells_the_truth_when_the_version_cannot(
    wheel_pkg_with_two_distinfos, monkeypatch
):
    """The content check is what saved the content checker.

    On the live box, `features` correctly reported the new symbols PRESENT while
    the version string still said 0.7.50. That is the whole argument for symbol
    probing over version reading, demonstrated against the probe's own bug.
    """
    monkeypatch.setattr(
        "scitex_cards._install_probe._md.version", lambda dist: "0.7.50"
    )

    p = probe_install("wheelpkg", features={"new": "wheelpkg:NEW_SYMBOL"})

    assert p.features["new"] is True  # the CODE is new, whatever the version claims


def test_a_single_distinfo_is_still_trustworthy(tmp_path, monkeypatch):
    """The fix must not make every wheel install suspect — only ambiguous ones."""
    site = tmp_path / "site-packages"
    pkg = site / "solopkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("X = 1\n", encoding="utf-8")
    (site / "solopkg-1.0.0.dist-info").mkdir()
    monkeypatch.syspath_prepend(str(site))
    monkeypatch.setattr(
        "scitex_cards._install_probe._md.version", lambda dist: "1.0.0"
    )

    try:
        p = probe_install("solopkg")
        assert p.trustworthy is True
        assert p.code_version == "1.0.0"
    finally:
        sys.modules.pop("solopkg", None)
