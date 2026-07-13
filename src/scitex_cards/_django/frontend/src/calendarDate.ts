/** Pure helpers for the Calendar layout (operator TG 13295, relayed by lead
 * a2a 510a58d4): tasks placed on a month grid by their `deadline` or
 * `last_activity` date.
 *
 * Kept side-effect-free so it tests cleanly via plain `node` (mirroring the
 * `test_table_filter.py` pattern shipped in PR #171 — no mocks, no DOM).
 *
 * SCOPE — floor version only:
 *  - `taskDateForCalendar(task)` — pick the canonical calendar date for a
 *    task. Precedence: explicit `deadline` (ISO date) → `last_activity`
 *    (date portion of ISO timestamp) → null (task is skipped).
 *  - `monthGridDays(year, month)` — return the 6×7 day grid for a month
 *    (Sunday-first), with leading/trailing days marked `inMonth=false` so
 *    the view can grey them.
 *  - `tasksByDate(tasks)` — group tasks by their YYYY-MM-DD calendar key.
 *  - `isSameDay(a, b)` / `dateKey(d)` — small primitives reused by the
 *    component and tests.
 *
 * NON-SCOPE — explicitly deferred:
 *  - Recurring `deadlines[]` expansion (closest upcoming) — operator may
 *    ask later; for now we honour `deadline_next` if the FE payload
 *    carries it (server-side computed by `_compute_deadline_next` in
 *    handlers/graph.py) but never expand recurrences here.
 *  - Time-of-day clustering — month-cell granularity is the floor.
 *  - Drag-to-reschedule. */

/** Minimum task shape consumed by the calendar helpers. A subset of
 * `GraphNode` so the helpers stay decoupled from view-only fields. */
export interface CalendarTask {
  id: string;
  deadline?: string | null;
  /** Server-computed next occurrence (recurring + multi expanded). Preferred
   * over `deadline` when present — it already resolves the closest upcoming
   * instance. */
  deadline_next?: string | null;
  last_activity?: string | null;
}

/** One cell in the month grid. `inMonth=false` for leading/trailing
 * neighbour-month days; `isToday=true` for the cell matching `now`. */
export interface CalendarCell {
  date: Date;
  /** YYYY-MM-DD key. Cheap stable identity for React + tasksByDate lookup. */
  key: string;
  inMonth: boolean;
  isToday: boolean;
  /** 0=Sun … 6=Sat. Used by the view to apply a weekend bg shift. */
  weekday: number;
  /** Day-of-month number (1..31). */
  day: number;
}

/** Parse "YYYY-MM-DD" or an ISO-8601 timestamp into a local-noon Date so
 * day-bucket math is timezone-stable (DST shifts can flip midnight by an
 * hour and bump the bucket by a day — anchor at noon to skip that). Returns
 * null on bad / empty input.
 *
 * The "local noon" anchor is the same trick used by every calendar UI
 * (Notion, GitHub project, …) — without it, an ISO date like
 * "2026-06-14" parsed by `new Date()` becomes 00:00 UTC, which in a
 * negative-UTC zone reads as the *previous* day. Anchoring at 12:00
 * local sidesteps the issue. */
export function parseCalendarDate(
  value: string | null | undefined,
): Date | null {
  if (value == null || typeof value !== "string") return null;
  const s = value.trim();
  if (!s) return null;
  // Pick the YYYY-MM-DD prefix off both "2026-06-14" and
  // "2026-06-14T09:30:00Z". Anything that doesn't start with the date
  // shape is rejected.
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(s);
  if (!m) return null;
  const year = Number(m[1]);
  const month = Number(m[2]);
  const day = Number(m[3]);
  if (
    !Number.isFinite(year) ||
    !Number.isFinite(month) ||
    !Number.isFinite(day)
  ) {
    return null;
  }
  if (month < 1 || month > 12 || day < 1 || day > 31) return null;
  const dt = new Date(year, month - 1, day, 12, 0, 0, 0);
  // Round-trip check — `new Date(2026, 1, 30)` silently rolls into March,
  // which we want to reject so a typo'd "2026-02-30" doesn't land on a
  // valid day.
  if (
    dt.getFullYear() !== year ||
    dt.getMonth() !== month - 1 ||
    dt.getDate() !== day
  ) {
    return null;
  }
  return dt;
}

/** Pick the canonical calendar date for a task.
 *
 * Precedence (operator TG 13295):
 *  1. `deadline_next` — server-side resolved next occurrence (recurring
 *     + multi expanded). When present this already encodes the operator
 *     ask "render the closest upcoming occurrence", so prefer it.
 *  2. `deadline`      — explicit single deadline.
 *  3. `last_activity` — recency fallback (date portion of an ISO ts).
 *  4. null            — task has no date → skip (don't render).
 *
 * The "closest upcoming occurrence" for recurring tasks is deferred to
 * later PRs except for the cheap case where the backend already
 * pre-computed it via `deadline_next`. */
export function taskDateForCalendar(
  task: CalendarTask | null | undefined,
): Date | null {
  if (task == null) return null;
  const dn = parseCalendarDate(task.deadline_next);
  if (dn) return dn;
  const d = parseCalendarDate(task.deadline);
  if (d) return d;
  const la = parseCalendarDate(task.last_activity);
  if (la) return la;
  return null;
}

/** True iff the two dates refer to the same year-month-day (local). */
export function isSameDay(a: Date | null, b: Date | null): boolean {
  if (a == null || b == null) return false;
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

/** YYYY-MM-DD key for a Date — cheap stable identity used as a Map key. */
export function dateKey(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

/** Build the 6×7 day grid for a month (Sunday-first). Leading days come
 * from the previous month (inMonth=false), trailing days from the next.
 *
 * Always returns 42 cells so the grid height is stable across months
 * (no row-count jitter when switching from Feb to May). */
export function monthGridDays(
  year: number,
  monthIndex: number,
  today?: Date | null,
): CalendarCell[] {
  // Anchor at noon — see `parseCalendarDate` comment.
  const first = new Date(year, monthIndex, 1, 12, 0, 0, 0);
  // first.getDay() is 0..6 (Sun..Sat). That's the count of leading
  // neighbour-month cells needed to land "day 1" on its weekday column.
  const lead = first.getDay();
  const startDate = new Date(first);
  startDate.setDate(first.getDate() - lead);

  const cells: CalendarCell[] = [];
  const now = today ?? new Date();
  for (let i = 0; i < 42; i++) {
    const d = new Date(startDate);
    d.setDate(startDate.getDate() + i);
    cells.push({
      date: d,
      key: dateKey(d),
      inMonth: d.getMonth() === monthIndex && d.getFullYear() === year,
      isToday: isSameDay(d, now),
      weekday: d.getDay(),
      day: d.getDate(),
    });
  }
  return cells;
}

/** Group tasks by their YYYY-MM-DD calendar key. Tasks with no usable
 * date are skipped (operator's "don't show it" rule). */
export function tasksByDate<T extends CalendarTask>(
  tasks: T[],
): Map<string, T[]> {
  const out = new Map<string, T[]>();
  for (const t of tasks ?? []) {
    const d = taskDateForCalendar(t);
    if (d == null) continue;
    const k = dateKey(d);
    const list = out.get(k);
    if (list) list.push(t);
    else out.set(k, [t]);
  }
  return out;
}

/** Month-name labels (English; the rest of the board is English-first too).
 * Exported so the view component and tests share one truth. */
export const MONTH_NAMES = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
] as const;

/** Sunday-first weekday labels (Sun..Sat). The week always starts on
 * Sunday for the floor version — operator can re-spec the locale later. */
export const WEEKDAY_NAMES = [
  "Sun",
  "Mon",
  "Tue",
  "Wed",
  "Thu",
  "Fri",
  "Sat",
] as const;

/** Step a (year, monthIndex) pair forward by `delta` months. Used by the
 * prev/next buttons. Handles year wrap. */
export function shiftMonth(
  year: number,
  monthIndex: number,
  delta: number,
): { year: number; monthIndex: number } {
  const total = year * 12 + monthIndex + delta;
  return { year: Math.floor(total / 12), monthIndex: ((total % 12) + 12) % 12 };
}
