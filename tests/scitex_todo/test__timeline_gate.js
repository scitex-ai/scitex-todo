/* test__timeline_gate.js — node --test unit tests for the anti-flash gate
 * shipped in
 * ``src/scitex_todo/_django/static/scitex_todo/board_v3/timelineGate.js``.
 *
 * Run from the repo root:
 *   node --test tests/scitex_todo/test__timeline_gate.js
 *
 * The gate stops the board_v3 Timeline raster from rebuilding (and flashing /
 * resetting scroll) on an identical ~4s /timeline auto-refresh. We cover the
 * pure rasterSig() builder and the stateful makeGate() skip/reset behaviour;
 * the scroll snapshot/restore (DOM-touching) is exercised with a tiny fake
 * canvas. JS not TS — mirrors the served vanilla path (see test__timeline_pack).
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
    "timelineGate.js",
  ),
);

const { rasterSig, makeGate, noopGate } = MOD;

test("rasterSig: identical inputs → identical sig", () => {
  const cache = { events: [{ id: 1 }], lanes: ["a"] };
  assert.equal(
    rasterSig("agent", "1d", cache, null),
    rasterSig("agent", "1d", cache, null),
  );
});

test("rasterSig: payload change flips the sig", () => {
  const a = rasterSig("agent", "1d", { events: [{ id: 1 }] }, null);
  const b = rasterSig("agent", "1d", { events: [{ id: 2 }] }, null);
  assert.notEqual(a, b);
});

test("rasterSig: window change flips the sig (same payload)", () => {
  const cache = { events: [] };
  assert.notEqual(
    rasterSig("agent", "1d", cache, null),
    rasterSig("agent", "1w", cache, null),
  );
});

test("rasterSig: view change flips the sig (same payload)", () => {
  const cache = { events: [] };
  assert.notEqual(
    rasterSig("agent", "1d", cache, null),
    rasterSig("project", "1d", cache, null),
  );
});

test("rasterSig: error state differs from data state", () => {
  assert.notEqual(
    rasterSig("agent", "1d", { events: [] }, null),
    rasterSig("agent", "1d", { events: [] }, "boom"),
  );
});

test("rasterSig: circular cache → still returns a string (never crashes)", () => {
  const c = {};
  c.self = c;
  const sig = rasterSig("agent", "1d", c, null);
  assert.equal(typeof sig, "string");
});

test("makeGate: first render is never 'unchanged'", () => {
  const g = makeGate();
  assert.equal(g.unchanged("sig-1"), false);
});

test("makeGate: same sig after mark → unchanged (skip rebuild)", () => {
  const g = makeGate();
  g.mark("sig-1");
  assert.equal(g.unchanged("sig-1"), true);
});

test("makeGate: a different sig → not unchanged (redraw)", () => {
  const g = makeGate();
  g.mark("sig-1");
  assert.equal(g.unchanged("sig-2"), false);
});

test("makeGate: reset() forces a redraw even on the same sig", () => {
  const g = makeGate();
  g.mark("sig-1");
  g.reset();
  assert.equal(g.unchanged("sig-1"), false);
});

// Minimal fake canvas: a .tl-scroll element with mutable scroll offsets.
function fakeCanvas(top, left) {
  const el = { scrollTop: top, scrollLeft: left };
  return {
    el: el,
    querySelector: function (sel) {
      return sel === ".tl-scroll" ? el : null;
    },
  };
}

test("makeGate: snapshot+restore round-trips .tl-scroll offsets", () => {
  const g = makeGate();
  const before = fakeCanvas(120, 40);
  g.snapshot(before);
  // simulate innerHTML swap recreating .tl-scroll at the top-left
  const after = fakeCanvas(0, 0);
  g.restore(after);
  assert.equal(after.el.scrollTop, 120);
  assert.equal(after.el.scrollLeft, 40);
});

test("makeGate: restore without a prior snapshot is a no-op", () => {
  const g = makeGate();
  const after = fakeCanvas(0, 0);
  g.restore(after); // no snapshot taken → must not throw / not change
  assert.equal(after.el.scrollTop, 0);
});

test("makeGate: snapshot with no .tl-scroll → restore is a no-op", () => {
  const g = makeGate();
  const noScroll = { querySelector: () => null };
  g.snapshot(noScroll);
  const after = fakeCanvas(5, 5);
  g.restore(after);
  assert.equal(after.el.scrollTop, 5); // unchanged
});

test("noopGate: always rebuilds, never captures scroll", () => {
  const g = noopGate();
  g.mark("x");
  assert.equal(g.unchanged("x"), false);
  // snapshot/restore are no-ops and must not throw
  const c = fakeCanvas(9, 9);
  g.snapshot(c);
  g.restore(c);
  assert.equal(c.el.scrollTop, 9);
});
