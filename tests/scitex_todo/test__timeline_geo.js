/* test__timeline_geo.js — node --test unit tests for the pure time→pixel
 * geometry shipped in
 * ``src/scitex_todo/_django/static/scitex_todo/board_v3/timelineGeo.js``.
 *
 * Run from the repo root:
 *   node --test tests/scitex_todo/test__timeline_geo.js
 *
 * Extracted from timeline.js so that file stays under the per-file line cap;
 * these tests pin the geometry the raster relies on. JS not TS — mirrors the
 * served vanilla path (see test__timeline_pack.js).
 */
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const G = require(
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
    "timelineGeo.js",
  ),
);

test("ms: parses ISO strings, null on junk", () => {
  assert.equal(G.ms("1970-01-01T00:00:00.000Z"), 0);
  assert.equal(G.ms("not a date"), null);
  assert.equal(G.ms(null), null);
  assert.equal(G.ms(123), null);
});

test("timeToX: clamps to [0, w] and scales linearly", () => {
  assert.equal(G.timeToX(0, 0, 100, 200), 0); // at start
  assert.equal(G.timeToX(100, 0, 100, 200), 200); // at end
  assert.equal(G.timeToX(50, 0, 100, 200), 100); // midpoint
  assert.equal(G.timeToX(-10, 0, 100, 200), 0); // before start → 0
  assert.equal(G.timeToX(999, 0, 100, 200), 200); // after end → w
  assert.equal(G.timeToX(50, 100, 100, 200), null); // zero span → null
});

test("barGeo: null when no start; open span uses min(now, we)", () => {
  assert.equal(G.barGeo(null, null, 0, 100, 50, 200), null);
  const g = G.barGeo(0, null, 0, 100, 50, 200); // ongoing, now=50
  assert.ok(g && g.x === 0 && g.width === 100);
});

test("barGeo: out-of-window span → null", () => {
  assert.equal(G.barGeo(200, 300, 0, 100, 50, 200), null); // starts after we
});

test("makeTicks: returns count ticks spanning [0, w]", () => {
  const ticks = G.makeTicks(0, 100, 200, 6);
  assert.equal(ticks.length, 6);
  assert.equal(ticks[0].x, 0);
  assert.equal(ticks[5].x, 200);
  assert.equal(typeof ticks[0].label, "string");
});

test("makeTicks: degenerate inputs → empty", () => {
  assert.deepEqual(G.makeTicks(0, 0, 200, 6), []); // zero span
  assert.deepEqual(G.makeTicks(0, 100, 200, 1), []); // count < 2
});

test("relTime: human relative strings", () => {
  const now = 1_000_000_000_000;
  assert.equal(G.relTime(null, now), "");
  assert.equal(G.relTime(now, now), "just now");
  assert.equal(G.relTime(now - 5 * 60000, now), "5m ago");
  assert.equal(G.relTime(now - 3 * 3600000, now), "3h ago");
  assert.equal(G.relTime(now - 24 * 3600000, now), "yesterday");
  assert.equal(G.relTime(now - 5 * 24 * 3600000, now), "5d ago");
});

test("pad2: zero-pads to two digits", () => {
  assert.equal(G.pad2(3), "03");
  assert.equal(G.pad2(12), "12");
});
