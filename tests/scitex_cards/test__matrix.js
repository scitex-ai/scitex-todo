/* test__matrix.js — node --test unit tests for the urgency×importance
 * matrix layout shipped in
 * ``src/scitex_cards/_django/static/scitex_cards/board_v3/14-matrix.js``.
 *
 * Run from the repo root:
 *   node --test tests/scitex_cards/test__matrix.js
 *
 * No external deps; node's built-in runner (>=18). Same shape as
 * test__timeline_pack.js: require the REAL served module, not a mirror of
 * it — a mirror can drift from the file the browser loads and then both
 * "pass" while disagreeing.
 *
 * Contract covered (ADR-0011 §8 + the build plan approved on card
 * scitex-cards-gui-matrix-view-20260717):
 *   1. quadrantOf — the operator's numbering, NOT the textbook one.
 *   2. isAxisValue / axesOf — unscored is unscored; never coerced.
 *   3. partition / occupancy — counts, incl. the unscored bucket.
 *   4. byRank / cellsOf — deterministic order under the 5s poller.
 *   5. matrixHtml — 25 cells, escaping, and the honest-degradation rule.
 *   6. The module computes NO score — rank is the engine's output.
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");
const fs = require("node:fs");

const MOD_PATH = path.resolve(
  __dirname,
  "..",
  "..",
  "src",
  "scitex_cards",
  "_django",
  "static",
  "scitex_cards",
  "board_v3",
  "14-matrix.js",
);

const MX = require(MOD_PATH);

const card = (id, u, i, extra) =>
  Object.assign({ id: id, title: id, status: "queued" }, extra || {}, {
    urgency: u,
    importance: i,
  });

/* ── 1. Quadrants ─────────────────────────────────────────────────────── */

test("quadrantOf follows the operator's enumeration", () => {
  // I urgent∧important / II important∧¬urgent / III urgent∧¬important /
  // IV neither. II vs III is the one that matters: the ADR requires II to
  // outrank III, so mixing them up inverts the whole point of the view.
  assert.equal(MX.quadrantOf(5, 5), "I");
  assert.equal(MX.quadrantOf(1, 5), "II");
  assert.equal(MX.quadrantOf(5, 1), "III");
  assert.equal(MX.quadrantOf(1, 1), "IV");
});

test("the threshold is inclusive: 3 is HIGH on both axes", () => {
  assert.equal(MX.QUADRANT_THRESHOLD, 3);
  assert.equal(MX.isHigh(3), true);
  assert.equal(MX.isHigh(2), false);
  assert.equal(MX.quadrantOf(3, 3), "I");
  assert.equal(MX.quadrantOf(2, 3), "II");
  assert.equal(MX.quadrantOf(3, 2), "III");
  assert.equal(MX.quadrantOf(2, 2), "IV");
});

/* ── 2. Scored vs unscored — the honest-degradation rule ──────────────── */

test("isAxisValue accepts only in-scale integers", () => {
  [1, 2, 3, 4, 5].forEach((v) => assert.equal(MX.isAxisValue(v), true, `${v}`));
  // Out of scale, wrong type, or not an integer => NOT an axis value. `0`
  // and `"3"` are the ones that would silently mis-place a card.
  [0, 6, -1, 2.5, NaN, Infinity, "3", null, undefined, true, {}].forEach((v) =>
    assert.equal(MX.isAxisValue(v), false, `${JSON.stringify(v)}`),
  );
});

test("axesOf requires BOTH axes — one alone cannot place a card", () => {
  assert.deepEqual(MX.axesOf(card("a", 4, 2)), { urgency: 4, importance: 2 });
  assert.equal(MX.axesOf({ id: "b", urgency: 4 }), null);
  assert.equal(MX.axesOf({ id: "c", importance: 4 }), null);
  assert.equal(MX.axesOf({ id: "d" }), null);
  assert.equal(MX.axesOf(null), null);
});

test("a card with no axes is UNSCORED, never coerced to a coordinate", () => {
  // The regression this pins: rendering an unscored card at 0,0 or at a
  // default 3,3 fabricates an operator judgement nobody made.
  const p = MX.partition([card("scored", 1, 1), { id: "bare", title: "bare" }]);
  assert.deepEqual(p.scored.map((n) => n.id), ["scored"]);
  assert.deepEqual(p.unscored.map((n) => n.id), ["bare"]);
});

/* ── 3. Occupancy ─────────────────────────────────────────────────────── */

test("occupancy counts every quadrant plus the unscored bucket", () => {
  const occ = MX.occupancy([
    card("i1", 5, 5),
    card("i2", 3, 3),
    card("ii1", 1, 4),
    card("iii1", 4, 1),
    card("iv1", 2, 2),
    { id: "u1" },
    { id: "u2", urgency: 9, importance: 9 },
  ]);
  assert.deepEqual(occ, { I: 2, II: 1, III: 1, IV: 1, unscored: 2 });
});

test("occupancy of an empty set is all zeros, not a crash", () => {
  assert.deepEqual(MX.occupancy([]), { I: 0, II: 0, III: 0, IV: 0, unscored: 0 });
  assert.deepEqual(MX.occupancy(null), { I: 0, II: 0, III: 0, IV: 0, unscored: 0 });
});

/* ── 4. Ordering — must not shuffle under the 5s poller ───────────────── */

test("byRank puts rank 1 first and sinks unranked cards below ranked", () => {
  const nodes = [
    { id: "c", rank: 3 },
    { id: "a", rank: 1 },
    { id: "z" },
    { id: "b", rank: 2 },
  ];
  assert.deepEqual(nodes.slice().sort(MX.byRank).map((n) => n.id), [
    "a",
    "b",
    "c",
    "z",
  ]);
});

test("ties break by id so the render is deterministic across polls", () => {
  // /rev polls every 5s and re-renders; a non-deterministic sort would make
  // cards jump under the operator's cursor mid-drag (PR 2).
  const a = [{ id: "b" }, { id: "a" }, { id: "c" }];
  const first = a.slice().sort(MX.byRank).map((n) => n.id);
  const second = a.slice().reverse().sort(MX.byRank).map((n) => n.id);
  assert.deepEqual(first, ["a", "b", "c"]);
  assert.deepEqual(second, ["a", "b", "c"]);
});

test("cellsOf buckets by exact (urgency, importance) and sorts each cell", () => {
  const cells = MX.cellsOf([
    card("late", 2, 4, { rank: 9 }),
    card("next", 2, 4, { rank: 1 }),
    card("other", 5, 1),
  ]);
  assert.deepEqual(Object.keys(cells).sort(), ["2,4", "5,1"]);
  assert.deepEqual(cells["2,4"].map((n) => n.id), ["next", "late"]);
});

/* ── 5. Render ────────────────────────────────────────────────────────── */

test("matrixHtml renders all 25 cells with their axis coordinates", () => {
  const html = MX.matrixHtml([]);
  const cells = html.match(/class="mx-cell"/g) || [];
  assert.equal(cells.length, 25);
  // Every (u,i) pair present exactly once.
  for (let u = 1; u <= 5; u++) {
    for (let i = 1; i <= 5; i++) {
      const pat = `data-urgency="${u}" data-importance="${i}"`;
      assert.equal(html.includes(pat), true, `missing cell ${u},${i}`);
    }
  }
});

test("importance descends the page so quadrant I is top-right", () => {
  // The operator drew the quadrants; the render must match the drawing.
  const html = MX.matrixHtml([]);
  const rows = [...html.matchAll(/class="mx-row" data-importance="(\d)"/g)].map(
    (m) => m[1],
  );
  assert.deepEqual(rows, ["5", "4", "3", "2", "1"]);
});

test("a scored card lands in its own cell and carries its id", () => {
  const html = MX.matrixHtml([card("only", 4, 5)]);
  const cell = html.match(
    /data-urgency="4" data-importance="5" data-count="(\d)"/,
  );
  assert.equal(cell[1], "1");
  assert.equal(html.includes('data-id="only"'), true);
});

test("unscored cards render in the tray, never in a cell", () => {
  const html = MX.matrixHtml([{ id: "bare", title: "bare" }]);
  assert.equal(html.includes("mx-tray"), true);
  assert.equal(html.includes('data-count="1"'), true);
  // No cell claims it.
  const populated = [...html.matchAll(/class="mx-cell"[^>]*data-count="([1-9])/g)];
  assert.equal(populated.length, 0);
});

test("no tray is rendered when every card is scored", () => {
  const html = MX.matrixHtml([card("a", 1, 1)]);
  assert.equal(html.includes("mx-tray"), false);
});

test("titles and ids are HTML-escaped", () => {
  // The fallback escaper must actually escape: node has no global
  // escapeHtml, so a pass-through fallback would inject raw markup here
  // and the browser (which HAS escapeHtml) would render something else.
  const html = MX.matrixHtml([
    card('<img src=x onerror="alert(1)">', 3, 3, { title: "<b>bold</b>" }),
  ]);
  assert.equal(html.includes("<img src=x"), false);
  assert.equal(html.includes("<b>bold</b>"), false);
  assert.equal(html.includes("&lt;b&gt;bold&lt;/b&gt;"), true);
});

/* ── 6. The lane boundary — this module must not score ────────────────── */

test("the module exposes no scoring function and no weights", () => {
  // ADR-0011 §1: rank is COMPUTED by the engine (scitex-cards), never
  // asserted, and never recomputed here. A JS-side f(u,i) would be a second
  // implementation that renders plausibly and lies the moment the engine's
  // weights move. This test is the guard on that boundary.
  const exported = Object.keys(MX);
  ["score", "scoreOf", "rankOf", "computeRank", "weights", "W_I", "W_U"].forEach(
    (name) => assert.equal(exported.includes(name), false, `exports ${name}`),
  );
  const src = fs.readFileSync(MOD_PATH, "utf8");
  // The engine's formula is f(u,i) = w_i·i + w_u·u. No arithmetic combining
  // the two axes may appear in this file.
  assert.equal(/w_i|w_u|urgency\s*\*|importance\s*\*/.test(src), false);
});

test("matrixHtml never invents a rank pill for an unranked card", () => {
  const html = MX.matrixHtml([card("a", 1, 1)]);
  assert.equal(html.includes("mx-card__rank"), false);
  const ranked = MX.matrixHtml([card("b", 1, 1, { rank: 7 })]);
  assert.equal(ranked.includes(">#7<"), true);
});

/* ── 7. Drag → re-score (PR 2). dropAxes reads a dropped cell's coordinate;
 * it does NOT score. The event wiring lives in the template (DOM), but this
 * pure translation is unit-tested against the shipped module. ─────────── */

test("cards render draggable so the matrix drag can start", () => {
  const html = MX.matrixHtml([card("a", 3, 3)]);
  assert.equal(html.includes('draggable="true"'), true);
});

test("dropAxes turns a valid cell dataset into integer axes", () => {
  // The DOM hands data-* through as STRINGS; dropAxes coerces + validates.
  assert.deepEqual(MX.dropAxes({ urgency: "4", importance: "5" }), {
    urgency: 4,
    importance: 5,
  });
  assert.deepEqual(MX.dropAxes({ urgency: "1", importance: "1" }), {
    urgency: 1,
    importance: 1,
  });
});

test("dropAxes rejects any target without valid in-scale coordinates", () => {
  // Out of the 1..5 scale, non-integer, missing, or non-cell (the tray, the
  // gaps) -> null. A null result is a NO-OP drop: you cannot un-score, and the
  // verb requires 1..5 regardless.
  assert.equal(MX.dropAxes({ urgency: "0", importance: "3" }), null);
  assert.equal(MX.dropAxes({ urgency: "6", importance: "3" }), null);
  assert.equal(MX.dropAxes({ urgency: "3.5", importance: "3" }), null);
  assert.equal(MX.dropAxes({ urgency: "4" }), null); // importance missing
  assert.equal(MX.dropAxes({}), null);
  assert.equal(MX.dropAxes(null), null);
});
