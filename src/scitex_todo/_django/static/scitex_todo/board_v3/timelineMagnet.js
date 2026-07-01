/* timelineMagnet.js — "magnet / snap pointer to nearest scatter dot" for the
 * board_v3 "Timeline" raster (operator request: dots are small + dense, so
 * precise pointing is hard). Factored OUT of timeline.js (at the per-file line
 * cap, same reason as timelinePack.js / timelineGeo.js / timelineGate.js /
 * timelineSelect.js / timelineControls.js).
 *
 * What it does — on mousemove over a raster `.tl-svg`, it finds the NEAREST
 * dot (Euclidean, in the SVG's own px user space) within a max radius and
 * treats that dot as active WITHOUT the operator having to land the cursor
 * pixel-precisely on it. The magnet-active dot then:
 *   • drives the EXISTING dependency-subgraph hover-highlight — it calls
 *     STX.timelineControls.applyDotHighlight / .clearHighlight (the same code
 *     the real `.tl-dot` hover uses), so the magnet REUSES that path, never
 *     forks it;
 *   • shows its tooltip — the dot already carries an SVG <title>, so pointing
 *     "at" it via the magnet is enough for the native tooltip; we also mirror
 *     the title into the SVG's `aria`/`data-magnet-title` for good measure;
 *   • is the CLICK target — a plain (non-drag) click while a dot is magnet-
 *     active opens that card via the page's openDetail(), matching the dot's
 *     own inline `onclick="openDetail('<id>')"`;
 *   • slightly ENLARGES (grows radius) via a `.tl-dot--magnet` class so it
 *     feels "grabbed", restoring when the magnet moves away.
 *
 * Perf — the nearest scan is O(n) but rebuilt + run at most once per animation
 * frame (requestAnimationFrame throttle), which is fine for hundreds of dots.
 * No voronoi library.
 *
 * Robustness — everything is wired via event delegation on #columns and
 * re-queries `.tl-dot` fresh on each frame, so it survives the raster's ~5s
 * innerHTML auto-refresh re-render (never binds to stale nodes). It does NOT
 * touch timelineSelect.js's capture-phase click/dblclick/contextmenu or
 * timeline.js's inline onclick, so open-detail + multi-select + marquee stay
 * intact; the magnet only ACTS on a background click when no dot was under the
 * cursor AND the click wasn't the tail of a marquee drag.
 *
 * The pure nearest-dot math (`nearestDotWithin`) is node-testable and exported
 * via window.STX.timelineMagnet + module.exports (mirrors the siblings).
 * Loaded as a classic <script defer> AFTER timelineControls.js (whose highlight
 * API it uses). No build step — served static.
 */
"use strict";

(function () {
  // ── pure: nearest dot within a max radius ───────────────────────────
  // px, py  : pointer position, in the SAME coordinate space as each dot's
  //           {x, y} (for the raster: SVG user-space px, i.e. cx/cy).
  // dots    : array of { x, y, id } (id may be any value; returned as-is).
  // maxR    : inclusive max Euclidean distance to count as a "snap"; a
  //           non-finite / non-positive maxR means "no radius limit".
  // Returns the id of the single nearest dot, or null when none is in range.
  // Ties (equal distance) resolve to the FIRST such dot in array order — the
  // scan keeps a strict `<` so an earlier equal-distance dot wins.
  function nearestDotWithin(px, py, dots, maxR) {
    if (!Array.isArray(dots) || dots.length === 0) return null;
    if (!Number.isFinite(px) || !Number.isFinite(py)) return null;
    var hasCap = Number.isFinite(maxR) && maxR > 0;
    var capSq = hasCap ? maxR * maxR : Infinity;
    var bestId = null;
    var bestSq = Infinity;
    for (var i = 0; i < dots.length; i++) {
      var d = dots[i];
      if (!d) continue;
      var dx = d.x - px;
      var dy = d.y - py;
      if (!Number.isFinite(dx) || !Number.isFinite(dy)) continue;
      var sq = dx * dx + dy * dy;
      if (sq > capSq) continue;
      if (sq < bestSq) {
        bestSq = sq;
        bestId = d.id != null ? d.id : null;
      }
    }
    return bestId;
  }

  // Publish the pure helper early (node + page).
  var _api = { nearestDotWithin: nearestDotWithin };
  if (typeof globalThis !== "undefined") {
    globalThis.STX = globalThis.STX || {};
    globalThis.STX.timelineMagnet = _api;
  }
  if (typeof module !== "undefined" && module.exports) {
    module.exports = _api;
    return; // node import path: no DOM wiring
  }
  if (typeof document === "undefined") return;

  // ── DOM wiring (delegated on #columns; survives raster rebuilds) ────
  // Max snap radius in SVG px. Dots are r=7 (r=10 on hover); ~24px keeps the
  // magnet forgiving without stealing clicks from a genuinely empty region.
  var MAX_R = 24;

  function _host() {
    return document.getElementById("columns");
  }
  function _isTimeline() {
    return (
      typeof window.STATE !== "undefined" &&
      window.STATE &&
      window.STATE.layout === "timeline"
    );
  }
  function _controls() {
    return (
      (typeof window.STX !== "undefined" &&
        window.STX &&
        window.STX.timelineControls) ||
      null
    );
  }

  // The dot's stable id — prefer the explicit data-card-id timeline.js emits,
  // fall back to the inline openDetail('<id>') onclick (mirrors the siblings).
  function _dotId(el) {
    if (!el) return null;
    if (el.dataset && el.dataset.cardId != null) return el.dataset.cardId;
    var oc = el.getAttribute && el.getAttribute("onclick");
    var m = oc && oc.match(/openDetail\((['"])(.*?)\1\)/);
    return m ? m[2] : null;
  }

  // Pointer → SVG user-space px (same derivation timelineSelect's marquee uses:
  // cx/cy are unitless px there, so a bounding-rect delta suffices — no viewBox
  // dependency).
  function _svgPoint(svg, e) {
    var r = svg.getBoundingClientRect();
    return { x: e.clientX - r.left, y: e.clientY - r.top };
  }

  // Per-svg magnet state, keyed off the live element so a raster rebuild (new
  // SVG node) naturally drops the stale reference.
  var _active = { svg: null, dot: null };

  function _restore() {
    if (_active.dot) _active.dot.classList.remove("tl-dot--magnet");
    if (_active.svg) {
      var ctl = _controls();
      if (ctl && ctl.clearHighlight) ctl.clearHighlight(_active.svg);
      _active.svg.removeAttribute("data-magnet-title");
    }
    _active.svg = null;
    _active.dot = null;
  }

  // Snap to `dot` inside `svg`: grow it, drive the shared highlight, mirror the
  // tooltip. No-op when it's already the active dot.
  function _snap(svg, dot) {
    if (_active.dot === dot && _active.svg === svg) return;
    if (_active.dot && _active.dot !== dot)
      _active.dot.classList.remove("tl-dot--magnet");
    _active.svg = svg;
    _active.dot = dot;
    dot.classList.add("tl-dot--magnet");
    var ctl = _controls();
    if (ctl && ctl.applyDotHighlight) ctl.applyDotHighlight(svg, dot);
    var titleEl = dot.querySelector && dot.querySelector("title");
    if (titleEl && titleEl.textContent)
      svg.setAttribute("data-magnet-title", titleEl.textContent.split("\n")[0]);
  }

  // Build the {x, y, id, el} list for a fresh scan (re-queried every frame so
  // we never touch stale nodes from a prior render).
  function _collect(svg) {
    var out = [];
    var nodes = svg.querySelectorAll(".tl-dot");
    for (var i = 0; i < nodes.length; i++) {
      var el = nodes[i];
      var cx = parseFloat(el.getAttribute("cx"));
      var cy = parseFloat(el.getAttribute("cy"));
      if (!Number.isFinite(cx) || !Number.isFinite(cy)) continue;
      out.push({ x: cx, y: cy, id: i, el: el });
    }
    return out;
  }

  // ── rAF-throttled mousemove scan ────────────────────────────────────
  var _pending = null; // last mousemove event awaiting a frame
  var _rafId = 0;

  function _frame() {
    _rafId = 0;
    var e = _pending;
    _pending = null;
    if (!e || !_isTimeline() || !e.target || !e.target.closest) {
      _restore();
      return;
    }
    var svg = e.target.closest(".tl-svg");
    if (!svg) {
      _restore(); // moved off the raster (onto controls / legend / gaps)
      return;
    }
    var dots = _collect(svg);
    if (!dots.length) {
      _restore();
      return;
    }
    var p = _svgPoint(svg, e);
    var idx = nearestDotWithin(p.x, p.y, dots, MAX_R);
    if (idx == null) {
      _restore();
      return;
    }
    _snap(svg, dots[idx].el);
  }

  function _onMove(e) {
    if (!_isTimeline()) return;
    _pending = e;
    if (!_rafId) {
      _rafId =
        typeof requestAnimationFrame === "function"
          ? requestAnimationFrame(_frame)
          : setTimeout(_frame, 16);
    }
  }

  function _onLeave(e) {
    // Cursor left #columns entirely (or the raster) → drop the magnet so a
    // stale dot doesn't stay grabbed. relatedTarget still inside → ignore.
    var to = e && e.relatedTarget;
    if (to && to.closest && to.closest("#columns")) return;
    _pending = null;
    _restore();
  }

  // ── click forwarding (magnet region → openDetail) ───────────────────
  // A plain click while a dot is magnet-active but the pointer is NOT on a real
  // dot (empty region "near" it) opens that card, matching the dot's inline
  // onclick=openDetail. Bubbling phase so timelineSelect's CAPTURE-phase
  // handler (single-click toggle-select on a real dot, background-click clear)
  // runs first and untouched; we only act when the target wasn't a dot, so a
  // direct dot click still routes to select/openDetail exactly as before.
  function _onClick(e) {
    if (!_isTimeline()) return;
    if (!_active.dot || !_active.svg) return;
    if (!e.target || !e.target.closest) return;
    // Real dot under the cursor → its own handlers own this click.
    if (e.target.closest(".tl-dot")) return;
    // Must be inside the same raster the magnet is active on.
    if (e.target.closest(".tl-svg") !== _active.svg) return;
    var id = _dotId(_active.dot);
    if (id == null) return;
    if (typeof window.openDetail === "function") {
      e.preventDefault();
      e.stopPropagation();
      window.openDetail(id);
    }
  }

  function _install() {
    var host = _host() || document;
    if (host.__tlMagnetWired) return; // idempotent (load-race guard)
    host.__tlMagnetWired = true;
    host.addEventListener("mousemove", _onMove, false);
    host.addEventListener("mouseleave", _onLeave, false);
    // Bubbling phase, AFTER timelineSelect's capture handler.
    host.addEventListener("click", _onClick, false);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", _install);
  } else {
    _install();
  }
})();
