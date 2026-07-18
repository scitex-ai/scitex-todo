/* test__dateinfo.js — node --test unit tests for the date helpers extracted
 * from board_v3.html's inline <script> into
 * ``src/scitex_cards/_django/static/scitex_cards/board_v3/15-dateinfo.js``.
 *
 * Run from the repo root:
 *   node --test tests/scitex_cards/test__dateinfo.js
 *
 * Requires the REAL served module — not a hand-ported mirror, which can
 * drift from the file the browser loads and then both "pass" while
 * disagreeing. These functions had ZERO behavioural coverage while they
 * lived inline in the template; this is the first real test of them, and
 * the point of the extraction.
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
    "15-dateinfo.js",
  ),
);

const {
  dateInfo,
  _parseAllDates,
  _extractRepeaterSuffix,
  _firstRecurringDeadline,
} = MOD;

/* ── Source priority: deadline_next > deadline > title ─────────────────── */

test("deadline_next wins and carries the repeater from the raw deadline", () => {
  const r = dateInfo({
    deadline_next: "2099-01-01",
    deadline: "2099-01-01 +1w",
  });
  assert.equal(r.source, "deadline_next");
  assert.equal(r.repeater, "every 1w");
});

test("raw deadline is used when there is no deadline_next", () => {
  const r = dateInfo({ deadline: "2099-01-01" });
  assert.equal(r.source, "deadline");
});

test("a malformed deadline falls through to the title scan", () => {
  // new Date("not a date") is NaN -> the deadline branch is skipped.
  const r = dateInfo({ deadline: "not a date", title: "meet 2099-05-05" });
  assert.equal(r.source, "title");
});

/* ── Title parsing ────────────────────────────────────────────────────── */

test("title scan picks the NEXT future date, not the first written", () => {
  const r = dateInfo({ title: "do by 2099-03-15 and also 2099-02-01" });
  assert.equal(r.source, "title");
  assert.equal(r.date.getMonth(), 1); // February is nearest-future
});

test("a card with no parseable date returns null", () => {
  assert.equal(dateInfo({ title: "nothing here" }), null);
  assert.equal(dateInfo({}), null);
});

test("an impossible calendar date is rejected, not rolled over", () => {
  // JS Date would roll 2099-13-40 into 2100; the month-check rejects it.
  assert.equal(dateInfo({ title: "bad 2099-13-40" }), null);
  assert.equal(dateInfo({ title: "feb 2099-02-30" }), null);
});

test("both YYYY-MM-DD and YYYY/MM/DD are recognized", () => {
  assert.equal(_parseAllDates("a 2099-01-01 b").length, 1);
  assert.equal(_parseAllDates("a 2099/01/01 b").length, 1);
});

test("the /g regex does not leak state across calls", () => {
  // _DATE_RX is a shared /g regex; a missing lastIndex reset would make the
  // second call skip the match. This pins the reset.
  assert.equal(_parseAllDates("2099-01-01").length, 1);
  assert.equal(_parseAllDates("2099-01-01").length, 1);
  assert.equal(_parseAllDates("2099-01-01").length, 1);
});

test("title falls back to the most-recent PAST date when none are future", () => {
  const r = dateInfo({ title: "was 2000-01-01 and 2001-01-01" });
  assert.equal(r.source, "title");
  assert.equal(r.date.getFullYear(), 2001); // most recent past
});

/* ── Repeater suffix parsing ──────────────────────────────────────────── */

test("_extractRepeaterSuffix parses + and ++ forms, else null", () => {
  assert.equal(_extractRepeaterSuffix("2099-01-01 +1w"), "every 1w");
  assert.equal(_extractRepeaterSuffix("2099-01-01 ++2m"), "every 2m");
  assert.equal(_extractRepeaterSuffix("2099-01-01"), null);
  assert.equal(_extractRepeaterSuffix(null), null);
  assert.equal(_extractRepeaterSuffix(""), null);
});

test("_firstRecurringDeadline returns the first recurring entry, else null", () => {
  assert.equal(
    _firstRecurringDeadline({ deadlines: ["2099-01-01", "2099-02-01 +1w"] }),
    "2099-02-01 +1w",
  );
  assert.equal(_firstRecurringDeadline({ deadlines: ["2099-01-01"] }), null);
  assert.equal(_firstRecurringDeadline({}), null);
  assert.equal(_firstRecurringDeadline({ deadlines: "not a list" }), null);
});

/* ── Shape of the returned object ─────────────────────────────────────── */

test("daysFromNow is an integer count and negatives mean overdue", () => {
  // Relative to a fixed far-future/past pair the sign is deterministic even
  // though 'now' is the real clock.
  const future = dateInfo({ deadline: "2099-01-01" });
  assert.ok(future.daysFromNow > 0);
  assert.equal(Math.floor(future.daysFromNow), future.daysFromNow);
  const past = dateInfo({ deadline: "2000-01-01" });
  assert.ok(past.daysFromNow < 0);
});
