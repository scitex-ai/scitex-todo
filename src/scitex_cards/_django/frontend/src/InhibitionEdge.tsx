/** Custom React Flow edge for `blocks` relationships — `----|` (T-bar / ⊣).
 *
 * Visual contract (operator 3682, "blocker edge v2"):
 *   - Body line: SAME length, weight, and style as a `depends_on` arrow
 *     (smoothstep path, solid stroke, strokeWidth 2). The only differences
 *     vs depends_on are the color and the end-cap.
 *   - End-cap: a SOLID perpendicular bar at the target endpoint — biology /
 *     circuit notation for inhibition / "blocks". Read as "this blocks that"
 *     at a glance.
 *   - NO text label. The bar alone carries the semantic; the redundant red
 *     "blocks" word from v1 was visually noisy (operator feedback).
 *
 * Why a custom edge instead of `markerEnd: url(#…)`?
 *   PR #5 used an SVG <marker> end-cap defined in a sibling <svg><defs>
 *   alongside React Flow's canvas. Cross-SVG `url(#id)` references resolve
 *   inconsistently across browsers / build pipelines, and React Flow also
 *   shortens edge paths by the marker's `markerWidth` — which made the v1
 *   T-bar read as a "short red dashed stub" rather than a full line + tee.
 *   Owning the path + the perpendicular bar in one component eliminates both
 *   issues and lets the body match `depends_on` exactly.
 */

import {
  BaseEdge,
  getSmoothStepPath,
  Position,
  type EdgeProps,
} from "@xyflow/react";
import { EDGE_COLOR_BLOCKS } from "./layout";

/** Half-length (px) of the perpendicular end-bar.
 *
 * Sized to roughly match the visual weight of the default `MarkerType.ArrowClosed`
 * arrowhead on `depends_on` edges so the two edge kinds read as siblings.
 */
const TEE_HALF = 9;

/** Stroke width — kept in lockstep with the depends_on edge body in layout.ts. */
const EDGE_STROKE_WIDTH = 2;

/** Tee bar is slightly thicker so the inhibition cap reads clearly at zoom. */
const TEE_STROKE_WIDTH = 2.5;

/** Render a perpendicular line at the target endpoint.
 *
 * Orientation follows React Flow's `targetPosition`: TB layout arrives from
 * `Position.Top` so the bar is horizontal; LR layout would arrive from
 * `Position.Left` and the bar would be vertical. Falls back to horizontal.
 */
function teeCoords(
  targetX: number,
  targetY: number,
  targetPosition: Position,
): { x1: number; y1: number; x2: number; y2: number } {
  const horizontal =
    targetPosition === Position.Top || targetPosition === Position.Bottom;
  if (horizontal) {
    return {
      x1: targetX - TEE_HALF,
      y1: targetY,
      x2: targetX + TEE_HALF,
      y2: targetY,
    };
  }
  return {
    x1: targetX,
    y1: targetY - TEE_HALF,
    x2: targetX,
    y2: targetY + TEE_HALF,
  };
}

export function InhibitionEdge({
  sourceX,
  sourceY,
  sourcePosition,
  targetX,
  targetY,
  targetPosition,
  style,
  // Intentionally swallow markerEnd / markerStart so they CANNOT reach
  // BaseEdge. React Flow's EdgeWrapper resolves an edge's `markerEnd` (set
  // in layout.ts) into a `url('#…')` string and passes it as a prop to the
  // custom edge — if we forwarded that prop, BaseEdge would render the
  // depends_on arrowhead at the target endpoint AND our perpendicular tee
  // would draw at the same spot, producing the overlapping ↦+⊣ artifact
  // the operator reported on 2026-05-22. The inhibition edge's end-cap is
  // the tee ALONE; no arrowhead, ever. Renamed with a leading underscore
  // so TS treats them as deliberately unused.
  markerEnd: _markerEnd,
  markerStart: _markerStart,
}: EdgeProps) {
  void _markerEnd;
  void _markerStart;

  const [edgePath] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  });

  const bar = teeCoords(targetX, targetY, targetPosition);

  // BaseEdge handles the body path (the React Flow way to draw an edge);
  // the perpendicular tee is a separate <line> drawn at the same position.
  // Body and tee share EDGE_COLOR_BLOCKS so the whole edge reads as one mark.
  //
  // markerEnd / markerStart are explicitly set to `undefined` on BaseEdge —
  // belt-and-braces alongside the destructure-and-swallow above, so even a
  // future caller that adds `...rest` cannot smuggle a marker through.
  return (
    <>
      <BaseEdge
        path={edgePath}
        style={{
          stroke: EDGE_COLOR_BLOCKS,
          strokeWidth: EDGE_STROKE_WIDTH,
          ...style,
        }}
        markerEnd={undefined}
        markerStart={undefined}
      />
      <line
        x1={bar.x1}
        y1={bar.y1}
        x2={bar.x2}
        y2={bar.y2}
        stroke={EDGE_COLOR_BLOCKS}
        strokeWidth={TEE_STROKE_WIDTH}
        strokeLinecap="round"
      />
    </>
  );
}

/** Edge type id — referenced from layout.ts and registered in GraphView.tsx. */
export const INHIBITION_EDGE_TYPE = "inhibition";
