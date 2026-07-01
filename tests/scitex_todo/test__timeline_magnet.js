/* test__timeline_magnet.js — node --test unit tests for the pure nearest-dot
 * math shipped in
 * ``src/scitex_todo/_django/static/scitex_todo/board_v3/timelineMagnet.js``.
 *
 * Run from the repo root:
 *   node --test tests/scitex_todo/test__timeline_magnet.js
 *
 * No external deps; uses node's built-in test runner (>=18). JS, not TS — the
 * module ships as a plain <script> for the Django board_v3 template, so the
 * test surface stays in the same language (mirrors test__timeline_geo.js).
 * Only the PURE nearestDotWithin() is covered here; the DOM wiring (rAF scan,
 * highlight reuse, click forwarding) is browser-only and short-circuited under
 * node.
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
    "scitex_todo",
    "_django",
    "static",
    "scitex_todo",
    "board_v3",
    "timelineMagnet.js",
  ),
);

const { nearestDotWithin } = MOD;

test("nearestDotWithin: empty / non-array list → null", () => {
  assert.equal(nearestDotWithin(0, 0, [], 20), null);
  assert.equal(nearestDotWithin(0, 0, null, 20), null);
  assert.equal(nearestDotWithin(0, 0, undefined, 20), null);
});

test("nearestDotWithin: picks the Euclidean-nearest dot in range", () => {
  const dots = [
    { x: 100, y: 100, id: "far" },
    { x: 10, y: 10, id: "near" },
    { x: 50, y: 50, id: "mid" },
  ];
  assert.equal(nearestDotWithin(12, 9, dots, 24), "near");
});

test("nearestDotWithin: out-of-radius → null (no snap)", () => {
  const dots = [{ x: 100, y: 100, id: "a" }];
  // distance is ~141px, well beyond a 24px cap
  assert.equal(nearestDotWithin(0, 0, dots, 24), null);
});

test("nearestDotWithin: inclusive at exactly maxR", () => {
  const dots = [{ x: 20, y: 0, id: "edge" }];
  assert.equal(nearestDotWithin(0, 0, dots, 20), "edge"); // dist == maxR
  assert.equal(nearestDotWithin(0, 0, dots, 19), null); // just outside
});

test("nearestDotWithin: ties resolve to the FIRST dot in array order", () => {
  const dots = [
    { x: 10, y: 0, id: "first" },
    { x: -10, y: 0, id: "second" }, // same distance from origin
  ];
  assert.equal(nearestDotWithin(0, 0, dots, 24), "first");
});

test("nearestDotWithin: no radius cap when maxR is non-finite / non-positive", () => {
  const dots = [{ x: 1000, y: 1000, id: "distant" }];
  assert.equal(nearestDotWithin(0, 0, dots, Infinity), "distant");
  assert.equal(nearestDotWithin(0, 0, dots, 0), "distant");
  assert.equal(nearestDotWithin(0, 0, dots, NaN), "distant");
});

test("nearestDotWithin: bad pointer coords → null", () => {
  const dots = [{ x: 0, y: 0, id: "z" }];
  assert.equal(nearestDotWithin(NaN, 0, dots, 20), null);
  assert.equal(nearestDotWithin(0, Infinity, dots, 20), null);
});

test("nearestDotWithin: skips malformed dots but still finds a valid one", () => {
  const dots = [
    null,
    { x: "nope", y: 5, id: "bad" },
    { x: 5, y: 5, id: "good" },
  ];
  assert.equal(nearestDotWithin(4, 4, dots, 24), "good");
});

test("nearestDotWithin: id returned as-is (numeric index ids work)", () => {
  const dots = [
    { x: 0, y: 0, id: 0 },
    { x: 100, y: 0, id: 1 },
  ];
  assert.equal(nearestDotWithin(98, 0, dots, 24), 1);
});
