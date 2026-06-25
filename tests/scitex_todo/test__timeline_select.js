/* test__timeline_select.js — node --test unit tests for the pure copy-text
 * formatters shipped in
 * ``src/scitex_todo/_django/static/scitex_todo/board_v3/timelineSelect.js``.
 *
 * Run from the repo root:
 *   node --test tests/scitex_todo/test__timeline_select.js
 *
 * No external deps; uses node's built-in test runner (>=18). JS, not TS —
 * the module ships as a plain <script> for the Django board_v3 template, so
 * the test surface stays in the same language (mirrors test__timeline_pack.js).
 * Only the PURE formatters are covered here; the DOM wiring (selection set,
 * context menu, clipboard) is browser-only and short-circuited under node.
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
    "scitex_todo",
    "_django",
    "static",
    "scitex_todo",
    "board_v3",
    "timelineSelect.js",
  ),
);

const { formatCardCopy, joinCopyBlocks } = MOD;

test("formatCardCopy: labelled lines in id/title/status/assignee/note order", () => {
  const txt = formatCardCopy({
    id: "t-1",
    title: "Fix the raster",
    status: "in_progress",
    assignee: "orochi",
    note: "blocked on review",
  });
  assert.equal(
    txt,
    "id: t-1\ntitle: Fix the raster\nstatus: in_progress\nassignee: orochi\nnote: blocked on review",
  );
});

test("formatCardCopy: falls back to agent when assignee missing", () => {
  const txt = formatCardCopy({
    id: "x",
    title: "T",
    status: "done",
    agent: "ai",
  });
  assert.match(txt, /assignee: ai/);
});

test("formatCardCopy: missing fields → sensible placeholders", () => {
  const txt = formatCardCopy({ id: "only-id" });
  assert.match(txt, /id: only-id/);
  assert.match(txt, /title: only-id/); // title falls back to id
  assert.match(txt, /status: \?/);
  assert.match(txt, /assignee: —/);
  assert.match(txt, /note: —/);
});

test("formatCardCopy: title falls back to task field then id", () => {
  assert.match(
    formatCardCopy({ id: "i", task: "do thing" }),
    /title: do thing/,
  );
});

test("formatCardCopy: null / undefined card does not throw", () => {
  assert.doesNotThrow(() => formatCardCopy(null));
  assert.doesNotThrow(() => formatCardCopy(undefined));
  assert.match(formatCardCopy(null), /title: \(untitled\)/);
});

test("formatCardCopy: numeric id is stringified", () => {
  assert.match(formatCardCopy({ id: 42, title: "n" }), /id: 42/);
});

test("formatCardCopy: whitespace-only note collapses to placeholder", () => {
  assert.match(formatCardCopy({ id: "i", note: "   " }), /note: —/);
});

test("joinCopyBlocks: single card equals formatCardCopy", () => {
  const card = {
    id: "a",
    title: "A",
    status: "done",
    assignee: "z",
    note: "n",
  };
  assert.equal(joinCopyBlocks([card]), formatCardCopy(card));
});

test("joinCopyBlocks: multiple cards separated by a blank line", () => {
  const a = { id: "a", title: "A" };
  const b = { id: "b", title: "B" };
  const out = joinCopyBlocks([a, b]);
  assert.equal(out, formatCardCopy(a) + "\n\n" + formatCardCopy(b));
  // a blank line means two consecutive newlines between blocks
  assert.ok(out.includes("\n\n"));
});

test("joinCopyBlocks: empty / non-array → empty string", () => {
  assert.equal(joinCopyBlocks([]), "");
  assert.equal(joinCopyBlocks(null), "");
  assert.equal(joinCopyBlocks(undefined), "");
});
