/* timelineGate.js — anti-flash gate for the board_v3 "Timeline" raster
 * (By Agent / By Project views).
 *
 * THE PROBLEM it solves: the board auto-refreshes ~every 4s and the Timeline
 * re-fetches GET /timeline each tick. On a quiet board the payload is
 * byte-identical, yet timeline.js rebuilds canvas.innerHTML EVERY tick — which
 * FLASHES the raster and resets its scroll container to the top. When the
 * operator scrolls down to read lower lanes it yanks them back up a few
 * seconds later. (The sibling board_v3.html gate keys on /graph, which changes
 * constantly on a busy board, so it does NOT cover the Timeline — the Timeline
 * must self-gate on its OWN /timeline payload.)
 *
 * Two pieces:
 *   • rasterSig(view, windowKey, cache, error) — a deterministic string that
 *     folds the view (agent/project) + window (1d/1w/1m) + parsed /timeline
 *     payload (or the error). Pure, node-testable.
 *   • makeGate() — a tiny stateful helper around that sig: `unchanged(sig)`
 *     tells timeline.js to skip the rebuild; `mark(sig)` records the sig that
 *     was just rendered; `reset()` invalidates it (called on a user-driven
 *     view/window switch so a real change always redraws). It also carries the
 *     scroll snapshot/restore so the ONE real rebuild preserves the operator's
 *     scroll: snapshot reads scrollTop/scrollLeft off the .tl-scroll container
 *     (overflow:auto per 09-timeline.css — both axes scroll there: horizontal
 *     for the wide SVG, vertical for tall lane lists), and restore re-queries
 *     it after the innerHTML assignment (which recreates that element) and
 *     puts the offsets back.
 *
 * Factored out of timeline.js so that file stays under the per-file line cap
 * AND so the sig is unit-testable in plain node (mirrors timelinePack.js /
 * searchQuery.js: a browser <script> that also `module.exports`). Loaded as a
 * classic <script defer> BEFORE timeline.js; publishes window.STX.timelineGate.
 */
"use strict";

(function () {
  // Build a stable signature for the raster's current render inputs. Equal
  // sigs ⇒ an identical render ⇒ safe to skip the rebuild. We stringify the
  // parsed payload so any field change (events / edges / lanes / window
  // bounds) flips it; view + window are folded in so a user-driven switch
  // always differs even if the payload momentarily matches.
  function rasterSig(view, windowKey, cache, error) {
    var data;
    if (error != null && error !== "") {
      data = "E:" + String(error);
    } else {
      try {
        data = "D:" + JSON.stringify(cache);
      } catch (e) {
        // non-serialisable → never-matching sig so we err toward redrawing
        // rather than silently going stale.
        data = "D:?" + Date.now();
      }
    }
    return String(view) + "|" + String(windowKey) + "|" + data;
  }

  // Stateful gate. `last` is the sig of the currently-rendered raster; null ⇒
  // force a redraw. `snap` holds the scroll offsets captured before a rebuild.
  function makeGate() {
    var last = null;
    var snap = null;
    return {
      unchanged: function (sig) {
        return last !== null && sig === last;
      },
      mark: function (sig) {
        last = sig;
      },
      reset: function () {
        last = null;
      },
      // Read scroll offsets off the .tl-scroll container under `canvas`.
      snapshot: function (canvas) {
        var el = canvas && canvas.querySelector(".tl-scroll");
        snap = el ? { top: el.scrollTop, left: el.scrollLeft } : null;
      },
      // Re-query .tl-scroll (innerHTML recreated it) and restore the offsets.
      restore: function (canvas) {
        if (!snap) return;
        var el = canvas && canvas.querySelector(".tl-scroll");
        if (el) {
          el.scrollTop = snap.top;
          el.scrollLeft = snap.left;
        }
        snap = null;
      },
    };
  }

  // A do-nothing gate (always rebuilds, no scroll capture) for the defensive
  // fallback in timeline.js when this sibling somehow didn't load.
  function noopGate() {
    return {
      unchanged: function () {
        return false;
      },
      mark: function () {},
      reset: function () {},
      snapshot: function () {},
      restore: function () {},
    };
  }

  var _api = { rasterSig: rasterSig, makeGate: makeGate, noopGate: noopGate };
  if (typeof globalThis !== "undefined") {
    globalThis.STX = globalThis.STX || {};
    globalThis.STX.timelineGate = _api;
  }
  if (typeof module !== "undefined" && module.exports) {
    module.exports = _api;
  }
})();
