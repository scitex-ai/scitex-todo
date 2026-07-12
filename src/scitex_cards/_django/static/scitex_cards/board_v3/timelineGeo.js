/* timelineGeo.js — pure time→pixel geometry for the board_v3 "Timeline"
 * raster (By Agent / By Project views).
 *
 * Ported from frontend/src/timelineHelpers.ts. No DOM: factored out of
 * timeline.js so that file stays under the per-file line cap AND so the
 * geometry is unit-testable in plain node (mirrors timelinePack.js /
 * searchQuery.js: a browser <script> that also `module.exports`).
 *
 * Loaded as a classic <script defer> BEFORE timeline.js; publishes
 * window.STX.timelineGeo, which timeline.js's IIFE captures at init. No build.
 */
"use strict";

(function () {
  function ms(s) {
    if (s == null || typeof s !== "string") return null;
    var t = Date.parse(s.trim());
    return Number.isFinite(t) ? t : null;
  }
  function timeToX(ts, ws, we, w) {
    if (![ts, ws, we, w].every(Number.isFinite)) return null;
    var span = we - ws;
    if (span <= 0) return null;
    if (ts <= ws) return 0;
    if (ts >= we) return w;
    return ((ts - ws) / span) * w;
  }
  function barGeo(started, ended, ws, we, now, w) {
    if (started == null) return null;
    var eff = ended != null ? ended : Math.min(now, we);
    if (eff < ws || started > we) return null;
    var x1 = timeToX(Math.max(started, ws), ws, we, w);
    var x2 = timeToX(Math.min(eff, we), ws, we, w);
    if (x1 == null || x2 == null) return null;
    return { x: x1, width: Math.max(x2 - x1, 0) };
  }
  function pad2(n) {
    return String(n).padStart(2, "0");
  }
  function tickLabel(t, spanMs) {
    var d = new Date(t);
    // HH:MM for short spans; MM/DD for week+/month so labels don't repeat
    // uselessly across days.
    if (spanMs <= 36 * 3600 * 1000)
      return pad2(d.getHours()) + ":" + pad2(d.getMinutes());
    return pad2(d.getMonth() + 1) + "/" + pad2(d.getDate());
  }
  function makeTicks(ws, we, w, count) {
    if (![ws, we, w].every(Number.isFinite) || count < 2) return [];
    var span = we - ws;
    if (span <= 0) return [];
    var out = [];
    for (var i = 0; i < count; i++) {
      out.push({
        x: (w * i) / (count - 1),
        label: tickLabel(ws + (span * i) / (count - 1), span),
      });
    }
    return out;
  }
  function relTime(t, now) {
    if (t == null) return "";
    var s = Math.max(0, now - t);
    var m = Math.floor(s / 60000);
    if (m < 1) return "just now";
    if (m < 60) return m + "m ago";
    var h = Math.floor(m / 60);
    if (h < 24) return h + "h ago";
    var day = Math.floor(h / 24);
    if (day === 1) return "yesterday";
    if (day < 30) return day + "d ago";
    var mo = Math.floor(day / 30);
    return mo < 12 ? mo + "mo ago" : Math.floor(day / 365) + "y ago";
  }

  var _api = {
    ms: ms,
    timeToX: timeToX,
    barGeo: barGeo,
    pad2: pad2,
    tickLabel: tickLabel,
    makeTicks: makeTicks,
    relTime: relTime,
  };
  if (typeof globalThis !== "undefined") {
    globalThis.STX = globalThis.STX || {};
    globalThis.STX.timelineGeo = _api;
  }
  if (typeof module !== "undefined" && module.exports) {
    module.exports = _api;
  }
})();
