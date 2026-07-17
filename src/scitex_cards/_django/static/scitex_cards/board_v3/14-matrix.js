/* 14-matrix.js — the urgency×importance matrix layout for the
 * scitex-cards GUI (ADR-0011 §8).
 *
 * Operator build order (card scitex-cards-gui-matrix-view-20260717):
 *   four quadrants — I urgent∧important / II important∧¬urgent /
 *   III urgent∧¬important / IV neither; humans DRAG cards to update the
 *   two axes; rank recomputes (importance weighted ABOVE urgency) and the
 *   new order is immediately shared with agents; quadrant occupancy is
 *   tracked over time (maximize I+II).
 *
 * PR 2 adds DRAG: cards render `draggable`, and `dropAxes()` turns a drop
 * onto a grid cell into the two axis values the client POSTs to /rescore.
 * It STILL never computes rank and never scores — rank is the engine's
 * output (scitex-cards, ADR-0011 §1); a second scoring implementation here
 * would render plausibly and lie the moment the engine's weights moved, so
 * there deliberately isn't one. `dropAxes` reads coordinates, it does not
 * rank. Occupancy-over-time (PR 3) is audit-trail replay.
 *
 * LAYOUT — a 5×5 grid of (urgency, importance) cells, not four boxes of
 * lists. The axes are the stored fact (1–5 each); the quadrant is DERIVED
 * by a threshold and drawn, never stored (agreed with scitex-cards: two
 * sources of truth for one fact would drift on the first weight change).
 * The grid also gives PR 2 exact drop targets — a cell IS an (urgency,
 * importance) pair, so a drag sets both axes with no pixel arithmetic.
 *
 * Pure + node-testable (same shape as timelinePack.js / 11-sticky-wall.js).
 * Publishes window.STX.matrix; board_v3.html's render() dispatch falls back
 * to Timeline when absent.
 */
"use strict";

(function (global) {
  /* Escape fallback. Unlike 11-sticky-wall.js's `_esc`, the fallback here
   * actually escapes rather than passing the string through: this module is
   * required directly by node tests where `global.escapeHtml` is undefined,
   * and a fallback that no-ops would make the tests assert on markup that is
   * not what a browser (which HAS escapeHtml) renders. */
  var _esc =
    global.escapeHtml ||
    function (x) {
      return String(x == null ? "" : x)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
    };

  /* The axis scale, per ADR-0011 §8: urgency and importance are 1–5. */
  var AXIS_MIN = 1;
  var AXIS_MAX = 5;

  /* The quadrant threshold — the ONE place the four quadrants are defined.
   *
   * A value is HIGH when it is >= 3, i.e. {3,4,5} high and {1,2} low. With a
   * 5-point scale there is no split that is symmetric AND puts the midpoint
   * on a side, so this is a judgement, not a derivation: the operator's
   * directive is to MAXIMIZE quadrants I+II, and an inclusive threshold
   * keeps mid-importance work in the important half rather than quietly
   * demoting it to III/IV. Rendering-only — it never reaches the store, and
   * scitex-cards can overrule it without a migration.
   */
  var QUADRANT_THRESHOLD = 3;

  function isHigh(v) {
    return Number(v) >= QUADRANT_THRESHOLD;
  }

  /* Quadrant numerals follow the operator's enumeration, NOT the textbook
   * ordering — I urgent∧important, II important∧¬urgent, III urgent∧
   * ¬important, IV neither. */
  function quadrantOf(urgency, importance) {
    var u = isHigh(urgency);
    var i = isHigh(importance);
    if (u && i) return "I";
    if (i) return "II";
    if (u) return "III";
    return "IV";
  }

  /* An axis value is only usable when it is an integer within scale. Anything
   * else — absent, null, "4", 0, 9, NaN — is UNSCORED, never coerced. A card
   * silently rendered at a wrong coordinate is worse than one shown as
   * unscored: it is a claim nobody made. */
  function isAxisValue(v) {
    return (
      typeof v === "number" &&
      isFinite(v) &&
      Math.floor(v) === v &&
      v >= AXIS_MIN &&
      v <= AXIS_MAX
    );
  }

  /* Both axes must be present. One axis alone cannot place a card. */
  function axesOf(node) {
    if (!node) return null;
    if (!isAxisValue(node.urgency) || !isAxisValue(node.importance)) return null;
    return { urgency: node.urgency, importance: node.importance };
  }

  function partition(nodes) {
    var scored = [];
    var unscored = [];
    (nodes || []).forEach(function (n) {
      if (axesOf(n)) scored.push(n);
      else unscored.push(n);
    });
    return { scored: scored, unscored: unscored };
  }

  /* Sort by the engine's rank when present (1 = next). Cards without a rank
   * sink below ranked ones, then sort by id so the render is deterministic
   * and does not shuffle under the 5s poller. */
  function byRank(a, b) {
    var ra = typeof a.rank === "number" && isFinite(a.rank) ? a.rank : Infinity;
    var rb = typeof b.rank === "number" && isFinite(b.rank) ? b.rank : Infinity;
    if (ra !== rb) return ra - rb;
    return String(a.id || "").localeCompare(String(b.id || ""));
  }

  /* Bucket nodes into cells keyed "u,i". Only scored nodes land here. */
  function cellsOf(nodes) {
    var out = {};
    partition(nodes).scored.forEach(function (n) {
      var ax = axesOf(n);
      var key = ax.urgency + "," + ax.importance;
      (out[key] = out[key] || []).push(n);
    });
    Object.keys(out).forEach(function (k) {
      out[k].sort(byRank);
    });
    return out;
  }

  /* Occupancy by quadrant — the read model PR 3 will render over time from
   * the audit trail. Exposed now because it is the same pure count the
   * header pills need, and because "are we living in II?" should be
   * answerable from the current render, not only from history. */
  function occupancy(nodes) {
    var out = { I: 0, II: 0, III: 0, IV: 0, unscored: 0 };
    var p = partition(nodes);
    out.unscored = p.unscored.length;
    p.scored.forEach(function (n) {
      var ax = axesOf(n);
      out[quadrantOf(ax.urgency, ax.importance)] += 1;
    });
    return out;
  }

  function _cardHtml(n) {
    var rank =
      typeof n.rank === "number" && isFinite(n.rank)
        ? '<span class="mx-card__rank">#' + _esc(n.rank) + "</span>"
        : "";
    return (
      '<div class="mx-card" draggable="true" data-id="' +
      _esc(n.id) +
      '" data-card-id="' +
      _esc(n.id) +
      '" data-status="' +
      _esc(n.status || "") +
      '" title="' +
      _esc(n.title || "") +
      '">' +
      rank +
      '<span class="mx-card__title">' +
      _esc(n.title || n.id) +
      "</span>" +
      "</div>"
    );
  }

  function _cellHtml(cells, u, i) {
    var key = u + "," + i;
    var items = cells[key] || [];
    var q = quadrantOf(u, i);
    return (
      '<div class="mx-cell" data-quadrant="' +
      q +
      '" data-urgency="' +
      u +
      '" data-importance="' +
      i +
      '" data-count="' +
      items.length +
      '">' +
      items.map(_cardHtml).join("") +
      "</div>"
    );
  }

  /* The unscored tray. ADR-0011 §8 gives no coordinate for a card with no
   * axes, and inventing one (0,0 / a default 3,3) would fabricate an
   * operator judgement. Until the engine ships and cards carry axes this
   * tray holds EVERY card — that is the honest read, not a broken view. */
  function _trayHtml(unscored) {
    if (!unscored.length) return "";
    return (
      '<div class="mx-tray" data-count="' +
      unscored.length +
      '"><div class="mx-tray__head">Unscored — ' +
      unscored.length +
      " card" +
      (unscored.length === 1 ? "" : "s") +
      " with no urgency/importance yet" +
      '</div><div class="mx-tray__body">' +
      unscored.slice().sort(byRank).map(_cardHtml).join("") +
      "</div></div>"
    );
  }

  function _occupancyHtml(occ) {
    var order = ["I", "II", "III", "IV"];
    return (
      '<div class="mx-occ" role="status" aria-label="Quadrant occupancy">' +
      order
        .map(function (q) {
          return (
            '<span class="mx-occ__pill" data-quadrant="' +
            q +
            '">' +
            q +
            " <b>" +
            occ[q] +
            "</b></span>"
          );
        })
        .join("") +
      (occ.unscored
        ? '<span class="mx-occ__pill" data-quadrant="unscored">unscored <b>' +
          occ.unscored +
          "</b></span>"
        : "") +
      "</div>"
    );
  }

  /* Build the whole layout. `nodes` is the filter bar's already-filtered
   * set — the matrix applies no predicates of its own. */
  function matrixHtml(nodes, opts) {
    opts = opts || {};
    var cells = cellsOf(nodes);
    var occ = occupancy(nodes);
    var rows = [];
    // Importance descends down the page: row 5 (most important) first, so
    // quadrant I sits top-right and II top-left, as the operator drew them.
    for (var i = AXIS_MAX; i >= AXIS_MIN; i--) {
      var row = [];
      for (var u = AXIS_MIN; u <= AXIS_MAX; u++) {
        row.push(_cellHtml(cells, u, i));
      }
      rows.push(
        '<div class="mx-row" data-importance="' + i + '">' + row.join("") + "</div>"
      );
    }
    return (
      '<div class="mx-wrap">' +
      _occupancyHtml(occ) +
      '<div class="mx-plane" data-threshold="' +
      QUADRANT_THRESHOLD +
      '">' +
      '<div class="mx-axis mx-axis--y" aria-hidden="true">importance →</div>' +
      '<div class="mx-grid">' +
      rows.join("") +
      "</div>" +
      '<div class="mx-axis mx-axis--x" aria-hidden="true">urgency →</div>' +
      "</div>" +
      _trayHtml(partition(nodes).unscored) +
      "</div>"
    );
  }

  /* Drag → re-score (PR 2): translate a drop onto a grid cell into the two
   * axis values the `rescore_task` verb needs. `dataset` is the dropped
   * cell's DOMStringMap (its data-urgency / data-importance, which the grid
   * was built to carry — a cell IS an (u,i) pair, so no pixel arithmetic).
   * Coerces to number and validates against the scale; returns
   * {urgency, importance} of ints, or null for a non-cell or a target with
   * no valid coordinates — e.g. the unscored tray, which has no axes: you
   * cannot un-score by dragging, the verb requires 1..5.
   *
   * This is the ONLY new logic PR 2 adds to the module and it deliberately
   * does NOT score — it reads coordinates; rank stays the engine's, computed
   * server-side. Kept here (not in the template) so node can test the drop
   * arithmetic against the SHIPPED file. */
  function dropAxes(dataset) {
    if (!dataset) return null;
    var u = Number(dataset.urgency);
    var i = Number(dataset.importance);
    if (!isAxisValue(u) || !isAxisValue(i)) return null;
    return { urgency: u, importance: i };
  }

  var _api = {
    AXIS_MIN: AXIS_MIN,
    AXIS_MAX: AXIS_MAX,
    QUADRANT_THRESHOLD: QUADRANT_THRESHOLD,
    isHigh: isHigh,
    isAxisValue: isAxisValue,
    quadrantOf: quadrantOf,
    axesOf: axesOf,
    partition: partition,
    byRank: byRank,
    cellsOf: cellsOf,
    occupancy: occupancy,
    matrixHtml: matrixHtml,
    dropAxes: dropAxes,
  };
  if (typeof globalThis !== "undefined") {
    globalThis.STX = globalThis.STX || {};
    globalThis.STX.matrix = _api;
  }
  if (typeof module !== "undefined" && module.exports) {
    module.exports = _api;
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
