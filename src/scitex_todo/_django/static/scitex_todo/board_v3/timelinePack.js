/* timelinePack.js — deterministic beeswarm sub-row packing for the
 * board_v3 "Timeline" raster (By Agent / By Project views).
 *
 * Pure geometry, no DOM. Factored out of timeline.js so it stays under the
 * per-file line cap AND so the packing algorithm is unit-testable in plain
 * node (mirrors searchQuery.js: a browser <script> that also `module.exports`).
 *
 * THE PROBLEM it solves: every event in a lane was drawn at the same vertical
 * lane centre, so time-overlapping markers stacked on top of each other and
 * the operator "can't see them". packRows assigns each marker a sub-row so
 * co-located markers fan out vertically instead of occluding.
 *
 * Loaded as a classic <script defer> BEFORE timeline.js (it publishes
 * window.STX.timelinePack which timeline.js consumes). No build step.
 *
 * NOTE: the React TimelineView.tsx got an equivalent change in PR #243; this
 * is the SEPARATE vanilla path actually served on board_v3.
 */
"use strict";

(function () {
  // Greedy interval partitioning. Items are {x, w} (left px + width px).
  // Sort a COPY by x ascending (stable tie-break on original index), then
  // place each item in the LOWEST sub-row whose last item's right edge
  // (x + w + gap) has cleared this item's x; else open a new row.
  //
  // A MAX_ROWS cap keeps a pathological lane from exploding the SVG: once
  // every row is open, overflow items clamp onto the row that frees up
  // earliest (smallest current right edge).
  //
  // Returns { rows, rowCount } where `rows[i]` is the 0-based sub-row of
  // input item i (input order preserved), and `rowCount` is how many rows
  // the lane needs (>= 0). Deterministic for a given input.
  function packRows(items, gap, maxRows) {
    var n = items == null ? 0 : items.length;
    var rows = new Array(n).fill(0);
    if (n === 0) return { rows: rows, rowCount: 0 };
    var g = typeof gap === "number" && Number.isFinite(gap) ? gap : 2;
    var cap = typeof maxRows === "number" && maxRows >= 1 ? maxRows : 12;

    // stable order: indices sorted by x asc, NaN-safe, original-index tiebreak
    var order = items.map(function (_, i) {
      return i;
    });
    order.sort(function (a, b) {
      var xa = items[a] && items[a].x;
      var xb = items[b] && items[b].x;
      var na = !Number.isFinite(xa);
      var nb = !Number.isFinite(xb);
      if (na && nb) return a - b;
      if (na) return -1;
      if (nb) return 1;
      if (xa !== xb) return xa - xb;
      return a - b;
    });

    var rowEnds = []; // right edge of last item placed on each open row
    var rowCount = 0;
    order.forEach(function (idx) {
      var it = items[idx] || {};
      var x = Number.isFinite(it.x) ? it.x : 0;
      var w = Number.isFinite(it.w) ? Math.max(it.w, 0) : 0;
      var placed = -1;
      for (var r = 0; r < rowCount; r++) {
        if (rowEnds[r] <= x) {
          placed = r;
          break;
        }
      }
      if (placed === -1) {
        if (rowCount < cap) {
          placed = rowCount;
          rowCount += 1;
          rowEnds[placed] = -Infinity;
        } else {
          // capped — clamp onto the earliest-freeing existing row
          placed = 0;
          for (var k = 1; k < rowCount; k++) {
            if (rowEnds[k] < rowEnds[placed]) placed = k;
          }
        }
      }
      rows[idx] = placed;
      rowEnds[placed] = x + w + g;
    });
    return { rows: rows, rowCount: rowCount };
  }

  var _api = { packRows: packRows };
  if (typeof globalThis !== "undefined") {
    globalThis.STX = globalThis.STX || {};
    globalThis.STX.timelinePack = _api;
  }
  if (typeof module !== "undefined" && module.exports) {
    module.exports = _api;
  }
})();
