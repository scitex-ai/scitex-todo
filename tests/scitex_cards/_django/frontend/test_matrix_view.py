#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Wire-format + template contract tests for the Matrix layout (ADR-0011 §8).

Operator build order (card ``scitex-cards-gui-matrix-view-20260717``):
four quadrants, humans DRAG cards to update the two axes, rank recomputes
(importance weighted ABOVE urgency) and the new order is shared with
agents immediately, quadrant occupancy tracked over time.

This PR is READ-ONLY (PR 1 of 3) and pins three things:

  1. ``/graph`` forwards ``urgency`` / ``importance`` / ``rank`` verbatim,
     so the matrix renders from the SAME wire format as every other layout
     rather than needing a second endpoint.
  2. The axes survive the store round-trip TODAY, before the schema-v5
     dataclass fields land — ``load_tasks`` returns raw YAML mappings and
     the validator has no unknown-key rejection. This is what lets PR 1
     ship ahead of the engine; if it ever stops being true, this test is
     the alarm rather than a silently empty matrix.
  3. The template wires the layout in (toggle + assets + valid layout).

The pure render logic lives in ``tests/scitex_cards/test__matrix.js``
(``node --test``), which requires the REAL served module — see that file.

Mocks-free (STX-NM / PA-306): a real tmp store on disk driven through the
real ``views.api_dispatch`` with a real ``RequestFactory``, pointed at the
store by ``?store=`` (same shape as ``handlers/test_priority.py``); the
template assertions just open the source. Lane-glob isolation comes from
the autouse ``_isolate_host_lane_globs`` fixture in
``tests/scitex_cards/conftest.py`` — do NOT re-set it here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytest.importorskip("django")

from django.test import RequestFactory  # noqa: E402

from scitex_cards._django import views  # noqa: E402
from scitex_cards._django.services import _reset_cache  # noqa: E402


_REPO_ROOT = Path(__file__).resolve().parents[4]

_STATIC = (
    _REPO_ROOT
    / "src"
    / "scitex_cards"
    / "_django"
    / "static"
    / "scitex_cards"
    / "board_v3"
)

_TEMPLATE = (
    _REPO_ROOT
    / "src"
    / "scitex_cards"
    / "_django"
    / "templates"
    / "scitex_cards"
    / "board_v3.html"
)

_MATRIX_JS = _STATIC / "14-matrix.js"
_MATRIX_CSS = _STATIC / "14-matrix.css"


# `scored` carries both axes + an engine rank; `bare` carries neither, which
# is what EVERY card looks like until the schema-v5 work lands.
_STORE_TEXT = (
    "tasks:\n"
    "  - {id: scored, title: A scored card, status: in_progress,"
    " urgency: 4, importance: 5, rank: 1}\n"
    "  - {id: bare, title: A card with no axes, status: in_progress}\n"
)


@pytest.fixture
def store(tmp_path):
    """Write a real tmp task store and reset the board cache around the test."""
    path = tmp_path / "tasks.yaml"
    path.write_text(_STORE_TEXT, encoding="utf-8")
    _reset_cache()
    yield str(path)
    _reset_cache()


def _graph_nodes(store_path):
    """Drive views.api_dispatch for GET /graph and return {id: node}."""
    request = RequestFactory().get(f"/graph?store={store_path}")
    response = views.api_dispatch(request, "graph")
    assert response.status_code == 200, response.content
    payload = json.loads(response.content)
    return {n["id"]: n for n in payload["nodes"]}


# ── 1. The wire format ────────────────────────────────────────────────────


def test_graph_forwards_the_axes_and_rank(store):
    """The matrix's three fields reach the frontend verbatim."""
    nodes = _graph_nodes(store)

    assert nodes["scored"]["urgency"] == 4
    assert nodes["scored"]["importance"] == 5
    assert nodes["scored"]["rank"] == 1


def test_graph_emits_none_not_a_default_for_an_unscored_card(store):
    """An unscored card carries None on every axis — never a coerced value.

    The regression this guards: defaulting a missing axis to 0 (or to the
    scale midpoint) would place the card at a coordinate nobody chose and
    render it as an operator judgement that was never made. 14-matrix.js
    treats None as UNSCORED and puts the card in its tray instead.
    """
    nodes = _graph_nodes(store)

    assert nodes["bare"]["urgency"] is None
    assert nodes["bare"]["importance"] is None
    assert nodes["bare"]["rank"] is None


def test_the_axes_survive_the_store_before_schema_v5_lands(store):
    """Axis keys round-trip TODAY, ahead of the v5 dataclass fields.

    ``load_tasks`` returns the raw YAML mappings (not Task round-trips) and
    the validator rejects no unknown keys, so ``urgency``/``importance``
    reach the handler already. That is precisely what lets this read-only
    PR ship before the rank engine — and if a future strict-schema change
    starts dropping unknown keys, this test fails LOUDLY here instead of
    the matrix silently rendering every card as unscored.
    """
    from scitex_cards._model import load_tasks

    tasks = {t["id"]: t for t in load_tasks(store)}

    assert tasks["scored"]["urgency"] == 4
    assert tasks["scored"]["importance"] == 5
    assert "urgency" not in tasks["bare"]


# ── 2. The template wiring ────────────────────────────────────────────────


def test_the_matrix_is_a_valid_layout_with_a_toggle():
    src = _TEMPLATE.read_text(encoding="utf-8")

    assert 'VALID_LAYOUTS = ["timeline", "wall", "graph", "matrix"]' in src
    assert 'id="f-layout-matrix"' in src
    assert "onLayoutChange('matrix')" in src


def test_the_template_loads_the_matrix_assets():
    src = _TEMPLATE.read_text(encoding="utf-8")

    assert "board_v3/14-matrix.js" in src
    assert "board_v3/14-matrix.css" in src


def test_the_matrix_render_falls_back_when_the_module_is_absent():
    """Same stance as Wall: a deferred script that hasn't executed must not
    blank the GUI — the dispatch guards on window.STX.matrix."""
    src = _TEMPLATE.read_text(encoding="utf-8")

    assert 'layout === "matrix" && window.STX && window.STX.matrix' in src


# ── 3. The theme + lane contracts ─────────────────────────────────────────


def test_matrix_css_uses_theme_tokens_not_hardcoded_colors():
    """Dark/light rides entirely on the scitex-ui var(--…) tokens.

    Dark mode is the default the operator reads in; a hardcoded literal
    would burn a light-mode colour into their view.
    """
    css = _MATRIX_CSS.read_text(encoding="utf-8")
    decls = re.findall(
        r"^\s*(?:background|color|border[a-z-]*)\s*:\s*([^;]+);", css, re.M
    )

    assert decls, "expected colour declarations to check"
    for decl in decls:
        if re.search(r"#[0-9a-fA-F]{3,8}\b", decl) or re.search(
            r"\b(?:white|black)\b", decl
        ):
            pytest.fail(f"hardcoded colour in 14-matrix.css: {decl!r}")


def test_the_matrix_module_does_not_implement_the_rank_engine():
    """The lane boundary, pinned in Python too.

    ADR-0011 §1: rank is COMPUTED by the engine (scitex-cards' package
    lane), never asserted and never recomputed in the GUI. The equivalent
    JS-side guard lives in test__matrix.js; this one makes the boundary
    visible to a Python-only test run.
    """
    src = _MATRIX_JS.read_text(encoding="utf-8")

    assert "w_i" not in src
    assert "w_u" not in src
