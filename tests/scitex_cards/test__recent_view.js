/* test__recent_view.js — node --test unit tests for the Recent view's
 * pure-helper module shipped at
 * ``src/scitex_cards/_django/static/scitex_cards/board_v3/recentSort.js``.
 *
 * Run from the repo root:
 *   node --test tests/scitex_cards/test__recent_view.js
 *
 * Covers the load-bearing behaviours per the operator's spec
 * (TG msg 513, 2026-06-12):
 *   - Newest-first sort, created_at preferred over first comment ts
 *   - First-comment fallback when created_at is absent
 *   - Unknown-timestamp rows sink to the bottom (stable by id)
 *   - NEW badge cutoff (<24h) classification
 *   - 24-72h "recent" tint classification
 *   - countNewIn24h matches the title-bar badge
 *   - Default 30-day lookback filter (keeps unknown-ts rows)
 *
 * No external deps; uses node's built-in test runner (>=18). Mirrors the
 * established searchQuery.js + test__search_query.js pattern.
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
    "scitex_cards",
    "_django",
    "static",
    "scitex_cards",
    "board_v3",
    "recentSort.js",
  ),
);

const {
  parseIso,
  earliestCommentTs,
  taskTimestamp,
  classifyRecency,
  sortByRecency,
  countNewIn24h,
  relativeTimestamp,
  filterDefaultLookback,
  HOUR_MS,
  NEW_CUTOFF_MS,
  DEFAULT_LOOKBACK_MS,
} = MOD;

/** Fixed reference "now" so tests are stable. */
const NOW = new Date("2026-06-12T12:00:00.000Z");

const iso = (offsetMs) => new Date(NOW.getTime() + offsetMs).toISOString();

test("parseIso returns Date for an ISO string", () => {
  const d = parseIso("2026-06-12T11:00:00Z");
  assert.ok(d instanceof Date);
  assert.equal(d.toISOString(), "2026-06-12T11:00:00.000Z");
});

test("parseIso returns null for null / empty / non-string", () => {
  assert.equal(parseIso(null), null);
  assert.equal(parseIso(undefined), null);
  assert.equal(parseIso(""), null);
  assert.equal(parseIso(42), null);
});

test("parseIso returns null for an unparseable string", () => {
  assert.equal(parseIso("not-a-date"), null);
});

test("earliestCommentTs picks the earliest of multiple comments", () => {
  const task = {
    id: "t",
    comments: [
      { ts: iso(-2 * HOUR_MS) },
      { ts: iso(-10 * HOUR_MS) },
      { ts: iso(-5 * HOUR_MS) },
    ],
  };
  const e = earliestCommentTs(task);
  assert.equal(e.toISOString(), iso(-10 * HOUR_MS));
});

test("earliestCommentTs returns null when no comments are parseable", () => {
  assert.equal(earliestCommentTs({ id: "t", comments: [] }), null);
  assert.equal(earliestCommentTs({ id: "t" }), null);
  assert.equal(earliestCommentTs({ id: "t", comments: [{ ts: "bad" }] }), null);
});

test("taskTimestamp prefers created_at over comments", () => {
  const task = {
    id: "t",
    created_at: iso(-1 * HOUR_MS),
    comments: [{ ts: iso(-100 * HOUR_MS) }],
  };
  const got = taskTimestamp(task);
  assert.equal(got.source, "created_at");
  assert.equal(got.ts.toISOString(), iso(-1 * HOUR_MS));
});

test("taskTimestamp falls back to the earliest comment when created_at absent", () => {
  const task = {
    id: "t",
    comments: [{ ts: iso(-48 * HOUR_MS) }, { ts: iso(-72 * HOUR_MS) }],
  };
  const got = taskTimestamp(task);
  assert.equal(got.source, "comment");
  assert.equal(got.ts.toISOString(), iso(-72 * HOUR_MS));
});

test("taskTimestamp returns null source when neither created_at nor comments are present", () => {
  const got = taskTimestamp({ id: "t" });
  assert.equal(got.ts, null);
  assert.equal(got.source, null);
});

test("classifyRecency: <24h => 'new'", () => {
  const task = { id: "t", created_at: iso(-1 * HOUR_MS) };
  assert.equal(classifyRecency(task, NOW), "new");
});

test("classifyRecency: <24h boundary (just under 24h) is still 'new'", () => {
  const task = {
    id: "t",
    created_at: iso(-(NEW_CUTOFF_MS - 1)),
  };
  assert.equal(classifyRecency(task, NOW), "new");
});

test("classifyRecency: exactly 24h => 'recent' (not 'new')", () => {
  const task = { id: "t", created_at: iso(-NEW_CUTOFF_MS) };
  assert.equal(classifyRecency(task, NOW), "recent");
});

test("classifyRecency: 48h => 'recent'", () => {
  const task = { id: "t", created_at: iso(-48 * HOUR_MS) };
  assert.equal(classifyRecency(task, NOW), "recent");
});

test("classifyRecency: 72h+ => 'older'", () => {
  const task = { id: "t", created_at: iso(-100 * HOUR_MS) };
  assert.equal(classifyRecency(task, NOW), "older");
});

test("classifyRecency: no timestamp => 'unknown'", () => {
  assert.equal(classifyRecency({ id: "t" }, NOW), "unknown");
});

test("sortByRecency: newest created_at first", () => {
  const tasks = [
    { id: "older", created_at: iso(-100 * HOUR_MS) },
    { id: "newest", created_at: iso(-1 * HOUR_MS) },
    { id: "middle", created_at: iso(-10 * HOUR_MS) },
  ];
  const out = sortByRecency(tasks);
  assert.deepEqual(
    out.map((t) => t.id),
    ["newest", "middle", "older"],
  );
});

test("sortByRecency: comment fallback ranks below an equally-recent created_at row", () => {
  const tasks = [
    {
      id: "comment-row",
      comments: [{ ts: iso(-2 * HOUR_MS) }],
    },
    { id: "ts-row", created_at: iso(-3 * HOUR_MS) },
  ];
  const out = sortByRecency(tasks);
  // comment-row's effective ts (-2h) is newer than ts-row's (-3h).
  assert.deepEqual(
    out.map((t) => t.id),
    ["comment-row", "ts-row"],
  );
});

test("sortByRecency: unknown timestamps sink to the bottom and stay stable by id", () => {
  const tasks = [
    { id: "z-unknown" },
    { id: "fresh", created_at: iso(-1 * HOUR_MS) },
    { id: "a-unknown" },
  ];
  const out = sortByRecency(tasks);
  assert.deepEqual(
    out.map((t) => t.id),
    ["fresh", "a-unknown", "z-unknown"],
  );
});

test("sortByRecency: does not mutate input", () => {
  const tasks = [
    { id: "b", created_at: iso(-2 * HOUR_MS) },
    { id: "a", created_at: iso(-1 * HOUR_MS) },
  ];
  const snapshot = tasks.map((t) => t.id);
  sortByRecency(tasks);
  assert.deepEqual(
    tasks.map((t) => t.id),
    snapshot,
  );
});

test("countNewIn24h counts only the <24h tasks", () => {
  const tasks = [
    { id: "new1", created_at: iso(-1 * HOUR_MS) },
    { id: "new2", created_at: iso(-12 * HOUR_MS) },
    { id: "recent", created_at: iso(-48 * HOUR_MS) },
    { id: "older", created_at: iso(-200 * HOUR_MS) },
    { id: "unknown" },
  ];
  assert.equal(countNewIn24h(tasks, NOW), 2);
});

test("countNewIn24h returns 0 on empty or undefined input", () => {
  assert.equal(countNewIn24h([], NOW), 0);
  assert.equal(countNewIn24h(undefined, NOW), 0);
});

test("relativeTimestamp formats minutes / hours / days / months", () => {
  assert.equal(
    relativeTimestamp(new Date(NOW.getTime() - 5 * 60 * 1000), NOW),
    "5m ago",
  );
  assert.equal(
    relativeTimestamp(new Date(NOW.getTime() - 2 * HOUR_MS), NOW),
    "2h ago",
  );
  assert.equal(
    relativeTimestamp(new Date(NOW.getTime() - 24 * HOUR_MS), NOW),
    "yesterday",
  );
  assert.equal(
    relativeTimestamp(new Date(NOW.getTime() - 3 * 24 * HOUR_MS), NOW),
    "3 days ago",
  );
  assert.equal(
    relativeTimestamp(new Date(NOW.getTime() - 60 * 24 * HOUR_MS), NOW),
    "2 months ago",
  );
});

test("relativeTimestamp handles null + 'just now' + future", () => {
  assert.equal(relativeTimestamp(null, NOW), "no timestamp");
  assert.equal(
    relativeTimestamp(new Date(NOW.getTime() - 5 * 1000), NOW),
    "just now",
  );
  assert.equal(
    relativeTimestamp(new Date(NOW.getTime() + 10 * 1000), NOW),
    "just now",
  );
});

test("filterDefaultLookback drops known-older-than-30d rows", () => {
  const tasks = [
    { id: "fresh", created_at: iso(-1 * HOUR_MS) },
    {
      id: "month-old",
      created_at: iso(-(DEFAULT_LOOKBACK_MS - HOUR_MS)),
    },
    {
      id: "two-months",
      created_at: iso(-(DEFAULT_LOOKBACK_MS + 30 * 24 * HOUR_MS)),
    },
  ];
  const out = filterDefaultLookback(tasks, NOW);
  assert.deepEqual(out.map((t) => t.id).sort(), ["fresh", "month-old"]);
});

test("filterDefaultLookback keeps unknown-timestamp rows (so they don't disappear)", () => {
  const tasks = [
    { id: "fresh", created_at: iso(-1 * HOUR_MS) },
    { id: "no-ts" },
  ];
  const out = filterDefaultLookback(tasks, NOW);
  assert.deepEqual(out.map((t) => t.id).sort(), ["fresh", "no-ts"]);
});
