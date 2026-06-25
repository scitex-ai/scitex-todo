/* timeline.js — board_v3 "Timeline" layout (operator TODO 2026-06-17).
 *
 * An own-data layout (sibling of the Stale layout) with three views,
 * chosen via a selector, over a day / week / month window:
 *
 *   • By Agent   — SVG time-raster, one row per agent, bars = task spans.
 *   • By Project — same raster, rows keyed by the task's project.
 *   • Simple     — a RICH per-task list (operator's "1b" choice): each task
 *                  is a card showing its status + latest comment INLINE
 *                  (the "communication space"), positioned newest-first.
 *
 * The two rasters pull GET /timeline?window_hours=N&lane_by=agent|project
 * (the server projects lanes + bar geometry). The simple list projects
 * STATE.graph.nodes by time client-side so it can show comments (which the
 * /timeline wire payload omits).
 *
 * Loaded as a classic <script defer> AFTER board_v3's inline extra_js, so
 * it shares the page globals (STATE, escapeHtml, openDetail, bucket,
 * render, toast). It exposes its entry points on `window` so the inline
 * render() dispatch + the generated onclick handlers can reach them.
 *
 * No build step — served static like searchQuery.js / recentSort.js.
 */
"use strict";

(function () {
  // Day / Week / Month → window_hours. Matches the backend cap (≤ 3 months).
  var WINDOWS = { "1d": 24, "1w": 168, "1m": 720 };

  // Layout constants for the raster SVG.
  var LANE_H = 26; // px floor per lane row (a 1-row lane keeps this height)
  var BAR_INSET = 4; // vertical padding inside a lane
  var LABEL_W = 150; // lane-label gutter width
  var AXIS_H = 22; // top time-axis height
  var TICKS = 6; // axis tick count (incl. both ends)
  var SUB_ROW_H = 18; // px per beeswarm sub-row inside a lane
  var SUB_ROW_GAP = 2; // px gap two markers need to share one sub-row
  var MAX_ROWS = 12; // cap on sub-rows per lane (overflow clamps)

  // Deterministic beeswarm sub-row packer (pure; lives in timelinePack.js so
  // this file stays under the line cap and the algorithm is node-testable).
  // Falls back to a single-row no-op if the sibling script hasn't loaded.
  var _pack =
    (typeof globalThis !== "undefined" &&
      globalThis.STX &&
      globalThis.STX.timelinePack &&
      globalThis.STX.timelinePack.packRows) ||
    function (items) {
      return { rows: new Array(items.length).fill(0), rowCount: 1 };
    };

  // Persisted view + window selections (mirror STATE.sort/layout stickiness).
  function _ls(key, dflt) {
    try {
      return localStorage.getItem(key) || dflt;
    } catch (e) {
      return dflt;
    }
  }
  var TL = {
    cache: null, // last /timeline payload (raster views only)
    error: null,
    view: _ls("scitex-todo:tl-view", "agent"), // agent | project | simple
    windowKey: _ls("scitex-todo:tl-window", "1d"), // 1d | 1w | 1m
  };
  // Expose for the inline autoRefresh hook (read-only use there).
  window._TL = TL;

  // ── pure geometry (ported from frontend/src/timelineHelpers.ts) ──────
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

  // ── controls row (shared by all three views) ─────────────────────────
  function controlsHtml(countLabel) {
    function opt(val, cur, label) {
      return (
        '<option value="' +
        val +
        '"' +
        (val === cur ? " selected" : "") +
        ">" +
        label +
        "</option>"
      );
    }
    var err = TL.error
      ? '<span class="tl-error" title="' +
        escapeHtml(TL.error) +
        '">! ' +
        escapeHtml(TL.error) +
        "</span>"
      : "";
    return (
      '<div class="tl-controls">' +
      '<label class="tl-ctl">View ' +
      '<select onchange="setTimelineView(this.value)" ' +
      'title="Rows by agent or project, or a rich per-task list">' +
      opt("agent", TL.view, "By Agent") +
      opt("project", TL.view, "By Project") +
      opt("simple", TL.view, "Simple (per task)") +
      "</select></label>" +
      '<label class="tl-ctl">Window ' +
      '<select onchange="setTimelineWindow(this.value)" ' +
      'title="How far back the timeline reaches">' +
      opt("1d", TL.windowKey, "Day") +
      opt("1w", TL.windowKey, "Week") +
      opt("1m", TL.windowKey, "Month") +
      "</select></label>" +
      '<span class="tl-count">' +
      escapeHtml(countLabel) +
      "</span>" +
      err +
      "</div>"
    );
  }

  // ── data: raster views fetch /timeline ───────────────────────────────
  function loadTimeline() {
    var hrs = WINDOWS[TL.windowKey] || 24;
    var laneBy = TL.view === "project" ? "project" : "agent";
    fetch("/timeline?window_hours=" + hrs + "&lane_by=" + laneBy)
      .then(function (r) {
        if (!r.ok)
          return r
            .json()
            .catch(function () {
              return { error: r.statusText };
            })
            .then(function (b) {
              throw new Error(b.error || "timeline " + r.status);
            });
        return r.json();
      })
      .then(function (payload) {
        TL.cache = payload;
        TL.error = null;
        if (STATE.layout === "timeline" && TL.view !== "simple") render();
      })
      .catch(function (e) {
        TL.error = (e && e.message) || String(e);
        if (STATE.layout === "timeline" && TL.view !== "simple") render();
      });
  }

  // ── raster view (agent / project) ────────────────────────────────────
  function renderRaster(canvas) {
    if (TL.cache === null && !TL.error) {
      canvas.innerHTML =
        '<div class="tl-wrap">' +
        controlsHtml("loading…") +
        '<div class="loading">loading timeline…</div></div>';
      loadTimeline();
      return;
    }
    var p = TL.cache || { events: [], edges: [], lanes: [] };
    var ws = ms(p.window_start);
    var we = ms(p.window_end);
    var now = Date.now();
    var lanes = p.lanes || [];
    var width = Math.max(
      320,
      (canvas.getBoundingClientRect().width || 900) - LABEL_W - 28,
    );
    // index events by lane for stable row order
    var byLane = {};
    lanes.forEach(function (l) {
      byLane[l] = [];
    });
    (p.events || []).forEach(function (ev) {
      if (byLane[ev.lane]) byLane[ev.lane].push(ev);
    });

    // SCATTER: ONE dot per task at its start time within its lane. Co-located
    // markers used to stack at the lane centre and occlude; now each lane runs
    // a deterministic beeswarm packer (packRows) so overlapping markers fan
    // out into sub-rows. Lanes thus have VARIABLE height + a CUMULATIVE top —
    // we walk a running `cursor` from AXIS_H, not i*LANE_H.
    var dots = [];
    var dotById = {};
    var laneTops = []; // y of each lane's top edge (aligned to `lanes`)
    var laneHeights = []; // each lane's px height
    var cursor = AXIS_H;
    lanes.forEach(function (lane, li) {
      var geos = []; // {ev, x, w} per visible event (x = clamped start px)
      (byLane[lane] || []).forEach(function (ev) {
        var g = barGeo(ms(ev.started_at), ms(ev.ended_at), ws, we, now, width);
        if (g) geos.push({ ev: ev, x: g.x, w: g.width });
      });
      var packed = _pack(geos, SUB_ROW_GAP, MAX_ROWS);
      var laneH = Math.max(LANE_H, Math.max(1, packed.rowCount) * SUB_ROW_H);
      laneTops[li] = cursor;
      laneHeights[li] = laneH;
      geos.forEach(function (it, k) {
        var dot = {
          cx: LABEL_W + it.x,
          cy: cursor + (packed.rows[k] || 0) * SUB_ROW_H + SUB_ROW_H / 2,
          ev: it.ev,
        };
        dots.push(dot);
        dotById[it.ev.id] = dot;
      });
      cursor += laneH;
    });
    var total = cursor + 6;
    var ticks = makeTicks(ws, we, width, TICKS);

    // axis
    var svg = "";
    svg += '<g class="tl-axis">';
    ticks.forEach(function (tk) {
      var x = LABEL_W + tk.x;
      svg +=
        '<line class="tl-tickline" x1="' +
        x +
        '" x2="' +
        x +
        '" y1="' +
        (AXIS_H - 4) +
        '" y2="' +
        total +
        '"></line>' +
        '<text class="tl-ticktext" x="' +
        x +
        '" y="' +
        (AXIS_H - 8) +
        '" text-anchor="middle">' +
        escapeHtml(tk.label) +
        "</text>";
    });
    svg += "</g>";
    // lane stripes + labels
    svg += '<g class="tl-lanes">';
    lanes.forEach(function (lane, i) {
      var yTop = laneTops[i];
      var laneH = laneHeights[i];
      svg +=
        '<rect class="tl-lane-bg' +
        (i % 2 === 0 ? " tl-lane-bg--even" : "") +
        '" x="0" y="' +
        yTop +
        '" width="' +
        (LABEL_W + width) +
        '" height="' +
        laneH +
        '"></rect>' +
        '<text class="tl-lane-label" x="8" y="' +
        (yTop + laneH / 2 + 4) +
        '">' +
        escapeHtml(_truncate(lane, 22)) +
        "</text>";
    });
    svg += "</g>";
    // dependency lines (drawn before the dots) — connect dot centres
    svg += '<g class="tl-edges">';
    (p.edges || []).forEach(function (e) {
      var s = dotById[e.source];
      var t = dotById[e.target];
      if (!s || !t) return;
      svg +=
        '<line class="tl-edge tl-edge--' +
        (e.kind === "blocks" ? "blocks" : "depends") +
        '" x1="' +
        s.cx +
        '" y1="' +
        s.cy +
        '" x2="' +
        t.cx +
        '" y2="' +
        t.cy +
        '"></line>';
    });
    svg += "</g>";
    // dots — ONE per task (the scatter). Click → detail drawer; hover →
    // the <title> tooltip. Completed dots fade; live (still-running) ones
    // keep a bright ring so you can spot what's being processed.
    svg += '<g class="tl-dots">';
    dots.forEach(function (d) {
      var done = d.ev.ended_at != null;
      var title =
        d.ev.title +
        "\nstatus: " +
        d.ev.status +
        (d.ev.started_at ? "\nstarted: " + d.ev.started_at : "") +
        (d.ev.ended_at ? "\ncompleted: " + d.ev.ended_at : "");
      svg +=
        '<circle class="tl-dot tl-dot--' +
        bucket(d.ev.status) +
        (done ? " tl-dot--done" : " tl-dot--live") +
        '" cx="' +
        d.cx +
        '" cy="' +
        d.cy +
        '" r="5" onclick="openDetail(\'' +
        escapeHtml(String(d.ev.id)) +
        "')\"><title>" +
        escapeHtml(title) +
        "</title></circle>";
    });
    svg += "</g>";

    var count = (p.events || []).length + " events";
    canvas.innerHTML =
      '<div class="tl-wrap">' +
      controlsHtml(count) +
      (lanes.length
        ? '<div class="tl-scroll"><svg class="tl-svg" width="' +
          (LABEL_W + width) +
          '" height="' +
          total +
          '" role="img" aria-label="Fleet timeline raster">' +
          svg +
          "</svg></div>"
        : '<div class="loading">no activity in this window 🌙</div>') +
      "</div>";
  }

  // ── simple view (rich per-task cards from STATE.graph) ───────────────
  function renderSimple(canvas) {
    var nodes = (STATE.graph && STATE.graph.nodes) || [];
    var now = Date.now();
    var cutoff = now - (WINDOWS[TL.windowKey] || 24) * 3600 * 1000;
    var rows = nodes
      .map(function (t) {
        var act = ms(t.last_activity) || ms(t.created_at);
        return { t: t, act: act };
      })
      .filter(function (r) {
        return r.act != null && r.act >= cutoff;
      })
      .sort(function (a, b) {
        return b.act - a.act;
      });

    var cards = rows
      .map(function (r) {
        var t = r.t;
        var comments = Array.isArray(t.comments) ? t.comments : [];
        var last = comments.length ? comments[comments.length - 1] : null;
        var commentHtml = last
          ? '<div class="tl-card-comment"><span class="tl-card-comment-author">' +
            escapeHtml(last.author || "?") +
            "</span> " +
            escapeHtml(_truncate(last.text || "", 160)) +
            "</div>"
          : '<div class="tl-card-comment tl-card-comment--none">no comments yet</div>';
        var meta = [
          t.project ? escapeHtml(t.project) : null,
          t.agent ? "@" + escapeHtml(t.agent) : null,
          t.priority ? "#" + escapeHtml(String(t.priority)) : null,
        ]
          .filter(Boolean)
          .join(" · ");
        return (
          '<div class="tl-card tl-card--' +
          bucket(t.status) +
          '" onclick="openDetail(\'' +
          escapeHtml(String(t.id)) +
          "')\">" +
          '<div class="tl-card-top">' +
          '<span class="tl-card-status">' +
          escapeHtml(t.status || "") +
          "</span>" +
          '<span class="tl-card-time">' +
          escapeHtml(relTime(r.act, now)) +
          "</span>" +
          "</div>" +
          '<div class="tl-card-title">' +
          escapeHtml(t.title || t.task || t.id) +
          "</div>" +
          (meta ? '<div class="tl-card-meta">' + meta + "</div>" : "") +
          commentHtml +
          (comments.length
            ? '<div class="tl-card-count">💬 ' + comments.length + "</div>"
            : "") +
          "</div>"
        );
      })
      .join("");

    canvas.innerHTML =
      '<div class="tl-wrap">' +
      controlsHtml(rows.length + " tasks") +
      (rows.length
        ? '<div class="tl-simple">' + cards + "</div>"
        : '<div class="loading">no activity in this window 🌙</div>') +
      "</div>";
  }

  function _truncate(s, n) {
    s = String(s == null ? "" : s);
    return s.length > n ? s.slice(0, n - 1) + "…" : s;
  }

  // ── entry point (called by the inline render() dispatch) ─────────────
  function renderTimeline(canvas) {
    if (TL.view === "simple") renderSimple(canvas);
    else renderRaster(canvas);
  }

  // ── control setters (called by the generated <select> onchange) ──────
  function setTimelineView(v) {
    TL.view = v;
    try {
      localStorage.setItem("scitex-todo:tl-view", v);
    } catch (e) {}
    if (v === "simple") render();
    else {
      TL.cache = null;
      loadTimeline();
      render();
    }
  }
  function setTimelineWindow(k) {
    TL.windowKey = k;
    try {
      localStorage.setItem("scitex-todo:tl-window", k);
    } catch (e) {}
    if (TL.view === "simple") render();
    else {
      TL.cache = null;
      loadTimeline();
      render();
    }
  }

  // Called by the inline autoRefreshTick on a store change so the raster
  // refreshes live (the simple view re-renders from STATE.graph anyway).
  function timelineOnStoreChange() {
    if (STATE.layout === "timeline" && TL.view !== "simple") loadTimeline();
  }

  // Auto dynamic update (operator 2026-06-17): keep the timeline fresh +
  // FLOWING as time passes. A self-gating timer re-fetches the raster (its
  // window is now-relative, so dots drift leftward each tick) or re-renders
  // the simple list — ONLY while the Timeline layout is active. On by
  // default, no toggle. Skips while a dot/card is hovered so the tooltip the
  // operator is reading isn't yanked out from under them.
  var TL_LIVE_MS = 5000;
  setInterval(function () {
    if (typeof STATE === "undefined" || !STATE || STATE.layout !== "timeline")
      return;
    if (document.querySelector(".tl-dot:hover, .tl-card:hover")) return;
    if (TL.view === "simple") render();
    else loadTimeline();
  }, TL_LIVE_MS);

  // Publish the entry points the inline board code + onclick handlers use.
  window._renderTimelineView = renderTimeline;
  window.setTimelineView = setTimelineView;
  window.setTimelineWindow = setTimelineWindow;
  window.loadTimeline = loadTimeline;
  window.timelineOnStoreChange = timelineOnStoreChange;
})();
