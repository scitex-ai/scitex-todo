/* 13-timeline-hover.js — row hover feedback for the Timeline raster.
 *
 * Operator 2026-07-13: "time line view ではマウスホバーに合わせてその行を
 * ハイライトする visual feedback を用意してください。その行の scatter だけ半径が
 * 大きくなるとかもいいと思います。"
 *
 * Both: the hovered lane's background lights up AND every dot in that lane
 * grows. Deliberately CHEAP — the raster is a live SVG that rebuilds itself
 * on a timer, so a hover must never touch data or trigger a re-render:
 *
 *   - ONE delegated `mouseover`/`mouseout` listener on #columns, attached
 *     once at load. It survives every raster rebuild (the listener is on the
 *     canvas, not on the SVG nodes), so timeline.js needs no re-attach hook.
 *   - The handler only toggles CSS classes on at most one lane's elements.
 *     No layout read, no innerHTML, no fetch. `mousemove` is NOT used —
 *     mouseover fires once per element entry, so moving inside a lane costs
 *     nothing after the first event.
 *   - The visual itself (fill + radius + transition) is pure CSS in
 *     09-timeline.css (`.tl-lane-bg--hover`, `.tl-dot--lanehover`).
 *
 * Requires `data-lane` on the lane background rects + the dots — emitted by
 * timeline.js. Self-wiring; no exports.
 */
"use strict";

(function () {
  var HOVER_LANE = null;

  function paint(lane, on) {
    var canvas = document.getElementById("columns");
    if (!canvas || lane == null) return;
    // CSS.escape guards lane names with quotes/brackets (agent ids can carry
    // "(unassigned)"), which would otherwise break the attribute selector.
    var esc = window.CSS && CSS.escape ? CSS.escape(lane) : lane;
    var sel = '[data-lane="' + esc + '"]';
    var nodes;
    try {
      nodes = canvas.querySelectorAll(sel);
    } catch (e) {
      return; // unescapable lane name — skip the effect rather than throw
    }
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      if (el.classList.contains("tl-lane-bg")) {
        el.classList.toggle("tl-lane-bg--hover", on);
      } else if (el.classList.contains("tl-dot")) {
        el.classList.toggle("tl-dot--lanehover", on);
      }
    }
  }

  function laneOf(target) {
    if (!target || !target.closest) return null;
    var el = target.closest("[data-lane]");
    return el ? el.getAttribute("data-lane") : null;
  }

  function onOver(ev) {
    var lane = laneOf(ev.target);
    if (lane === HOVER_LANE) return; // same lane — nothing to repaint
    if (HOVER_LANE !== null) paint(HOVER_LANE, false);
    HOVER_LANE = lane;
    if (lane !== null) paint(lane, true);
  }

  function onLeave() {
    if (HOVER_LANE === null) return;
    paint(HOVER_LANE, false);
    HOVER_LANE = null;
  }

  function attach() {
    var canvas = document.getElementById("columns");
    if (!canvas) return;
    canvas.addEventListener("mouseover", onOver);
    canvas.addEventListener("mouseleave", onLeave);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", attach);
  } else {
    attach();
  }
})();
