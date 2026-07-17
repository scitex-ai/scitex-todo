/* commentDigest.js — render the derived comment scalars /graph now ships.
 *
 * WHY THIS EXISTS
 * ---------------
 * /graph used to ship every card's full `comments[]` thread. Measured
 * 2026-07-17 over 1,837 cards that was 4,409,085 bytes of a ~6 MB payload,
 * re-fetched whole on every store change and rebuilt on the client's main
 * thread. gzip cuts the wire but not the parse or the render, which is what a
 * phone actually feels, and the scitex-cards GUI is read on a phone daily.
 * The server now derives four scalars instead — see
 * handlers/_comment_digest.py:
 *
 *   comment_count, last_comment {author, text}, first_comment_ts,
 *   first_comment_author
 *
 * The full thread is still reachable per card from GET /chat/<card_id>.
 *
 * WHY A SEPARATE FILE
 * -------------------
 * timeline.js sat exactly at the 512-line cap, so this logic could not be
 * grown in place. Extracting the existing comment-render block to here takes
 * timeline.js BELOW the cap — a partial pay-down of board_v3's line debt
 * rather than another `hook-bypass` token (scitex-cards' ruling on
 * todo-board-graph-payload-slim-20260710, 2026-07-17).
 *
 * Loaded as a classic <script defer> BEFORE timeline.js, matching the
 * searchQuery.js / recentSort.js convention. No build step.
 */
"use strict";

(function () {
  var STX = (globalThis.STX = globalThis.STX || {});

  // Length budget for a rendered comment body. Mirrors
  // handlers/_comment_digest.py `LAST_COMMENT_CHARS`: over budget, keep 159
  // chars and append the ellipsis so the result is exactly 160.
  var LAST_COMMENT_CHARS = 160;

  /* Truncate to `n` chars, ellipsis included in the budget.
   *
   * `last_comment.text` already arrives truncated to the same budget, so this
   * is a no-op on server-derived data. It is kept as defence in depth: the
   * render stays correct if a caller ever passes an untruncated string.
   */
  function truncate(s, n) {
    s = String(s == null ? "" : s);
    n = n == null ? LAST_COMMENT_CHARS : n;
    return s.length > n ? s.slice(0, n - 1) + "…" : s;
  }

  /* The Time-view card's inline comment body ("the communication space").
   *
   * `escapeHtml` is injected rather than read off the page globals so this
   * module stays testable outside a browser.
   */
  function lastCommentHtml(task, escapeHtml) {
    var last = (task && task.last_comment) || null;
    if (!last) {
      return '<div class="tl-card-comment tl-card-comment--none">no comments yet</div>';
    }
    return (
      '<div class="tl-card-comment"><span class="tl-card-comment-author">' +
      escapeHtml(last.author || "?") +
      "</span> " +
      escapeHtml(truncate(last.text || "", LAST_COMMENT_CHARS)) +
      "</div>"
    );
  }

  /* The 💬N badge. Empty string when the card has no comments, matching the
   * pre-slim behaviour where a zero-length thread rendered nothing.
   */
  function countHtml(task) {
    var count = (task && task.comment_count) || 0;
    return count ? '<div class="tl-card-count">💬 ' + count + "</div>" : "";
  }

  STX.commentDigest = {
    truncate: truncate,
    lastCommentHtml: lastCommentHtml,
    countHtml: countHtml,
    LAST_COMMENT_CHARS: LAST_COMMENT_CHARS,
  };

  // CommonJS export for the test runner (same dual shape as recentSort.js).
  if (typeof module !== "undefined" && module.exports) {
    module.exports = STX.commentDigest;
  }
})();
