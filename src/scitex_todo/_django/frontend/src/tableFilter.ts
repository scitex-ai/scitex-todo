/** Table-view row filter: hide structural umbrella cards by default.
 *
 * The board carries TWO classes of cards:
 *   1. Actionable task rows — the operator's daily lens (kind=task / compute /
 *      decision; kind null defaults to "task").
 *   2. Structural cards — quality-axis status rows (kind=status), goal /
 *      umbrella anchors (kind=goal). These have a real designed role for
 *      quality aggregation + dependency anchors in the Graph + Column views,
 *      but in the flat Table view they're pure noise (operator complaint via
 *      lead a2a 510a58d4).
 *
 * The Table view defaults to actionable-only — flip the toggle in the
 * toolbar to bring structural cards back. The Graph + Column views are
 * UNTOUCHED (every card still shows there per the existing dep-graph
 * contract).
 *
 * Operator pain: TG-level "Table view is cluttered by q-* and umbrellas."
 * Designed roles preserved: cards still exist, still resolve as graph
 * targets, still anchor dependencies — just not rendered in the flat
 * triage surface unless the operator opts in.
 */

/** Closed set of `kind` values treated as "structural" — non-actionable cards
 *  whose only job is graph anchoring / aggregation, not execution. Kept as a
 *  top-level constant so the test module + the filter share one source. */
export const STRUCTURAL_KINDS = new Set<string>(["status", "goal"]);

/** True when the row should be rendered in the Table view.
 *
 *  - `showStructural === true` → all rows pass (operator opted in).
 *  - Row has no `kind` (null / undefined / absent) → ALWAYS visible. Absent
 *    kind defaults to "task" per `types/board.ts`, which is actionable.
 *  - Row's `kind` is in `STRUCTURAL_KINDS` → hidden.
 *  - Anything else → visible.
 */
export function isVisibleRow(
  row: { kind?: string | null },
  showStructural: boolean,
): boolean {
  if (showStructural) return true;
  const k = row.kind;
  if (k == null) return true;
  return !STRUCTURAL_KINDS.has(k);
}

/** Filter helper for an array of rows. Pure; preserves order. */
export function filterStructuralRows<T extends { kind?: string | null }>(
  rows: T[],
  showStructural: boolean,
): T[] {
  return rows.filter((r) => isVisibleRow(r, showStructural));
}
