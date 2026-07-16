/* test__timeline_pack.js — node --test unit tests for the deterministic
 * beeswarm sub-row packer shipped in
 * ``src/scitex_cards/_django/static/scitex_cards/board_v3/timelinePack.js``.
 *
 * Run from the repo root:
 *   node --test tests/scitex_cards/test__timeline_pack.js
 *
 * No external deps; uses node's built-in test runner (>=18). JS, not TS —
 * the module under test ships as a plain <script> for the Django template
 * (board_v3 raster Timeline), so the test surface stays in the same language.
 * (The React TimelineView packing got separate coverage in PR #243; this is
 * the served vanilla path.)
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const MOD = require(
  path.resolve(
    __dirname,
    "..",
    "..",
    "src",
    "scitex_cards",
    "_django",
    "static",
    "scitex_cards",
    "board_v3",
    "timelinePack.js",
  ),
);

const { packRows } = MOD;

// helper: derive rowCount used count from a result
function rowsUsed(res) {
  return new Set(res.rows).size;
}

test("packRows: empty input → no rows", () => {
  const res = packRows([], 2, 12);
  assert.deepEqual(res.rows, []);
  assert.equal(res.rowCount, 0);
});

test("packRows: single item → row 0", () => {
  const res = packRows([{ x: 10, w: 5 }], 2, 12);
  assert.deepEqual(res.rows, [0]);
  assert.equal(res.rowCount, 1);
});

test("packRows: non-overlapping items share row 0", () => {
  // item A spans [0,10], gap 2 → right edge 12; B starts at 20 (>=12) → row 0
  const res = packRows(
    [
      { x: 0, w: 10 },
      { x: 20, w: 10 },
    ],
    2,
    12,
  );
  assert.deepEqual(res.rows, [0, 0]);
  assert.equal(res.rowCount, 1);
});

test("packRows: overlapping items get different rows", () => {
  // A [0,10]→edge 12; B starts at 5 (<12) → must open row 1
  const res = packRows(
    [
      { x: 0, w: 10 },
      { x: 5, w: 10 },
    ],
    2,
    12,
  );
  assert.equal(res.rowCount, 2);
  assert.notEqual(res.rows[0], res.rows[1]);
});

test("packRows: three mutually overlapping items → three rows", () => {
  const res = packRows(
    [
      { x: 0, w: 100 },
      { x: 10, w: 100 },
      { x: 20, w: 100 },
    ],
    2,
    12,
  );
  assert.equal(res.rowCount, 3);
  assert.deepEqual(new Set(res.rows).size, 3);
});

test("packRows: lowest-row reuse — A,B overlap; C clears A so reuses row 0", () => {
  // A [0,10]→edge 12, B [5,10]→row1 edge 17, C starts at 12 (>=12) reuses row0
  const res = packRows(
    [
      { x: 0, w: 10 },
      { x: 5, w: 10 },
      { x: 12, w: 5 },
    ],
    2,
    12,
  );
  assert.equal(res.rows[0], 0);
  assert.equal(res.rows[1], 1);
  assert.equal(res.rows[2], 0); // C reuses the freed row 0
  assert.equal(res.rowCount, 2);
});

test("packRows: deterministic — same input → identical output", () => {
  const items = [
    { x: 3, w: 4 },
    { x: 1, w: 4 },
    { x: 2, w: 4 },
    { x: 8, w: 1 },
  ];
  const a = packRows(items, 2, 12);
  const b = packRows(items, 2, 12);
  assert.deepEqual(a.rows, b.rows);
  assert.equal(a.rowCount, b.rowCount);
});

test("packRows: input order is preserved (rows aligned to input index)", () => {
  // Provide items out of x-order; rows[] must align to INPUT order, not sorted.
  const res = packRows(
    [
      { x: 100, w: 5 }, // index 0, late start
      { x: 0, w: 5 }, // index 1, early start
    ],
    2,
    12,
  );
  // both non-overlapping → both row 0, but array length/order matches input
  assert.equal(res.rows.length, 2);
  assert.equal(res.rows[0], 0);
  assert.equal(res.rows[1], 0);
});

test("packRows: does not mutate the input array order", () => {
  const items = [
    { x: 5, w: 2 },
    { x: 1, w: 2 },
  ];
  packRows(items, 2, 12);
  assert.equal(items[0].x, 5); // still original order
  assert.equal(items[1].x, 1);
});

test("packRows: MAX_ROWS cap respected — 20 stacked items clamp to cap", () => {
  const items = [];
  for (let i = 0; i < 20; i++) items.push({ x: i, w: 1000 }); // all overlap
  const res = packRows(items, 2, 5);
  assert.ok(res.rowCount <= 5, "rowCount must not exceed cap");
  // every assigned row index must be within [0, cap)
  res.rows.forEach((r) => {
    assert.ok(r >= 0 && r < 5, "row " + r + " out of capped range");
  });
});

test("packRows: NaN / missing x treated as 0, w as 0 (no crash)", () => {
  const res = packRows([{ x: NaN, w: NaN }, { x: 0, w: 0 }, {}], 2, 12);
  assert.equal(res.rows.length, 3);
  res.rows.forEach((r) => assert.ok(Number.isInteger(r)));
});

test("packRows: default gap/cap when omitted", () => {
  const res = packRows([{ x: 0, w: 1 }]);
  assert.deepEqual(res.rows, [0]);
  assert.equal(res.rowCount, 1);
});
