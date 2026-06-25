/** Pure helpers for the Timeline layout (operator-direct ask, TG;
 * relayed by lead a2a ``d0f7a0e3``, 2026-06-14): live raster timeline of
 * the whole fleet on ONE screen.
 *
 * Kept side-effect-free so it tests cleanly via plain `node` (same
 * pattern as `calendarDate.ts` / `tableFilter.ts` — no transpiler
 * dependency, no DOM).
 *
 * SCOPE — floor only:
 *  - `groupEventsByLane(events)` — bucket events by their `lane` field so
 *    the view can render each lane as one raster row.
 *  - `timeToX(ts, windowStart, windowEnd, width)` — linear time-to-x
 *    mapping; events left of `windowStart` clamp to 0, right of
 *    `windowEnd` clamp to `width`.
 *  - `eventBarGeometry(ev, windowStart, windowEnd, now, width)` — the
 *    (x, width) pair for a single event bar, honouring "still running"
 *    via `now` when `ended_at` is null.
 *  - `parseTimelineTs(value)` — lenient ISO parser; returns a numeric ms
 *    epoch or null.
 *
 * NON-SCOPE — explicitly deferred per the operator brief:
 *  - Pan / zoom / drag-to-reschedule (kept static).
 *  - WebSocket push (polling is fine — 30s).
 *  - Sub-second resolution. */

/** Minimum event shape consumed by the helpers. A subset of the wire
 * `TimelineEvent` so the helpers stay decoupled from view-only fields. */
export interface TimelineEventLike {
  id: string;
  lane: string;
  started_at: string | null;
  ended_at: string | null;
}

/** Parse an ISO timestamp into milliseconds since epoch. Returns null on
 * empty / non-string / unparseable input — the caller decides how to
 * handle a missing value (typically "skip the row"). Same lenient
 * tolerance as `calendarDate.parseCalendarDate` so the FE survives
 * legacy / partial rows. */
export function parseTimelineTs(
  value: string | null | undefined,
): number | null {
  if (value == null || typeof value !== "string") return null;
  const s = value.trim();
  if (!s) return null;
  // Date.parse tolerates `Z` suffix + ms precision + naive ISO strings.
  // It returns NaN on bad input — guard with Number.isFinite.
  const ms = Date.parse(s);
  if (!Number.isFinite(ms)) return null;
  return ms;
}

/** Bucket events by their `lane` field. Returns a Map keyed by lane name
 * with the events in insertion order so the FE's draw loop is stable
 * across polls. */
export function groupEventsByLane<T extends TimelineEventLike>(
  events: T[],
): Map<string, T[]> {
  const out = new Map<string, T[]>();
  for (const e of events ?? []) {
    const lane = e.lane;
    const list = out.get(lane);
    if (list) list.push(e);
    else out.set(lane, [e]);
  }
  return out;
}

/** Linear time-to-x mapping. Events before `windowStart` clamp to 0,
 * events after `windowEnd` clamp to `width`. Returns null when the
 * window is degenerate (start >= end) or the inputs are non-finite —
 * the caller skips drawing the bar in that case. */
export function timeToX(
  tsMs: number,
  windowStartMs: number,
  windowEndMs: number,
  width: number,
): number | null {
  if (
    !Number.isFinite(tsMs) ||
    !Number.isFinite(windowStartMs) ||
    !Number.isFinite(windowEndMs) ||
    !Number.isFinite(width)
  ) {
    return null;
  }
  const span = windowEndMs - windowStartMs;
  if (span <= 0) return null;
  if (tsMs <= windowStartMs) return 0;
  if (tsMs >= windowEndMs) return width;
  const frac = (tsMs - windowStartMs) / span;
  return frac * width;
}

/** Geometry (x + width) for a single event bar.
 *
 * Inputs:
 *  - `started`, `ended` — ms epoch; ended=null = still running.
 *  - `windowStart`, `windowEnd`, `now` — ms epoch.
 *  - `width` — the raster's drawable width in pixels.
 *
 * The "now-cap" trick: a still-running event extends from `started` to
 * `now` (NOT `windowEnd`) — that way the bar grows visibly as the poll
 * comes back. Completed events stop at `ended` even if the cell is past
 * `now` (the window ages out completed bars naturally).
 *
 * Returns null when the event has no `started` timestamp or its
 * effective interval lies entirely outside the window. */
export function eventBarGeometry(
  startedMs: number | null,
  endedMs: number | null,
  windowStartMs: number,
  windowEndMs: number,
  nowMs: number,
  width: number,
): { x: number; width: number } | null {
  if (startedMs == null) return null;
  // "Live" bars extend to min(now, windowEnd).
  const effectiveEnd = endedMs != null ? endedMs : Math.min(nowMs, windowEndMs);
  // Drop bars whose entire interval is outside the window.
  if (effectiveEnd < windowStartMs || startedMs > windowEndMs) return null;
  const xStart = timeToX(
    Math.max(startedMs, windowStartMs),
    windowStartMs,
    windowEndMs,
    width,
  );
  const xEnd = timeToX(
    Math.min(effectiveEnd, windowEndMs),
    windowStartMs,
    windowEndMs,
    width,
  );
  if (xStart == null || xEnd == null) return null;
  // A zero-duration event still gets a tiny visible mark so the operator
  // sees the spike. The component's CSS clamps min-width via
  // `min(width, 2px)` style for nicer rendering.
  const w = Math.max(0, xEnd - xStart);
  return { x: xStart, width: w };
}

/** Format an ms-epoch as an HH:MM tick label. Pure / locale-free so the
 * test mirror needs no locale plumbing. */
export function formatHhMm(ms: number): string {
  if (!Number.isFinite(ms)) return "";
  const d = new Date(ms);
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  return `${hh}:${mm}`;
}

/** Greedy interval partitioning ("swimlane packing"): pack a set of
 * horizontal intervals into the fewest vertical sub-rows such that no two
 * intervals in the same row overlap. This is the deterministic, beeswarm
 * answer to "overlapping task bars sit on top of each other" — instead of
 * random y-jitter, time-colliding bars are spread down into distinct
 * sub-rows so none occlude another.
 *
 * Algorithm: process intervals in a STABLE order (x ascending, ties by id
 * ascending) so the layout is identical across polls. For each interval,
 * place it in the LOWEST row whose last-placed interval ends — accounting
 * for `gap` px of breathing room — at or before this interval's `x`. If no
 * existing row fits, open a new one. This is the classic greedy
 * activity-selection / interval-graph colouring; it uses the minimum
 * number of rows for the given set.
 *
 * Inputs:
 *  - `intervals` — `{ id, x, width }` per bar (pixels). Not mutated.
 *  - `gap` — minimum horizontal pixel gap between two bars sharing a row.
 *
 * Returns `{ rowById, rowCount }` where `rowById` maps each interval id to
 * its 0-based row index and `rowCount` is the number of rows used (0 for
 * empty input). Pure / side-effect-free so it tests cleanly via `node`. */
export function packIntervalsIntoRows(
  intervals: { id: string; x: number; width: number }[],
  gap = 2,
): { rowById: Map<string, number>; rowCount: number } {
  const rowById = new Map<string, number>();
  const list = (intervals ?? []).slice();
  // Stable order so the packing is reproducible across polls: x ascending,
  // tie-break by id ascending. Sort a COPY — never mutate the caller's array.
  list.sort((a, b) =>
    a.x !== b.x ? a.x - b.x : a.id < b.id ? -1 : a.id > b.id ? 1 : 0,
  );
  // `rowEnds[r]` = the right edge (x + width + gap) of the last interval
  // placed in row `r`. A row fits the current interval when its end is at
  // or before the interval's left edge.
  const rowEnds: number[] = [];
  for (const it of list) {
    let placed = -1;
    for (let r = 0; r < rowEnds.length; r++) {
      if (rowEnds[r] <= it.x) {
        placed = r;
        break;
      }
    }
    if (placed === -1) {
      placed = rowEnds.length;
      rowEnds.push(0);
    }
    rowEnds[placed] = it.x + it.width + gap;
    rowById.set(it.id, placed);
  }
  return { rowById, rowCount: rowEnds.length };
}

/** Build evenly-spaced tick positions across the window. Returns an
 * array of `{ x, label }` pairs the SVG renders as the time axis.
 *
 * `count` is the number of ticks INCLUDING both endpoints (so 5 ticks
 * over a 4-hour window = at 0/1/2/3/4 hours). Pure function. */
export function makeTicks(
  windowStartMs: number,
  windowEndMs: number,
  width: number,
  count: number,
): { x: number; label: string }[] {
  if (
    !Number.isFinite(windowStartMs) ||
    !Number.isFinite(windowEndMs) ||
    !Number.isFinite(width) ||
    count < 2
  ) {
    return [];
  }
  const span = windowEndMs - windowStartMs;
  if (span <= 0) return [];
  const out: { x: number; label: string }[] = [];
  for (let i = 0; i < count; i++) {
    const t = windowStartMs + (span * i) / (count - 1);
    out.push({
      x: (width * i) / (count - 1),
      label: formatHhMm(t),
    });
  }
  return out;
}

// EOF
