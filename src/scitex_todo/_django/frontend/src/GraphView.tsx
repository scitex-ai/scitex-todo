/** React Flow rendering of the task dependency graph (read-only, MVP). */

import { useMemo } from "react";
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  buildFlow,
  EDGE_COLOR_BLOCKS,
  EDGE_MARKER_INHIBIT_ID,
  nodeStyle,
  partitionNodes,
} from "./layout";
import type { GraphPayload } from "./types/board";

/** Dark-theme tokens for React Flow chrome (minimap / controls / background). */
const FLOW_DARK = {
  // MiniMap: dark surface so it doesn't flash white against the board.
  miniMapBg: "#1b1b29",
  miniMapMask: "rgba(20, 20, 32, 0.78)",
  miniMapNode: "#3a3a52",
  miniMapNodeStroke: "#9b7fd6",
  // Background dots.
  bgDots: "#33334a",
};

/** Inhibition T-bar end-cap for `blocks` edges (⊣ — biology / circuit notation).
 *
 * Rendered as a hidden inline <svg><defs> next to the React Flow canvas; edges
 * reference it by url(#stx-todo-inhibit) via markerEnd. Same body line as
 * depends_on, but the perpendicular bar reads as "this blocks that" at a
 * glance — visually distinct from the → arrowhead.
 *
 * Sizing: markerUnits="userSpaceOnUse" keeps the bar a constant on-screen size
 * regardless of edge strokeWidth. orient="auto" rotates it so the bar is
 * always perpendicular to the edge's local direction at its endpoint.
 */
function InhibitionMarkerDefs() {
  return (
    <svg
      width={0}
      height={0}
      style={{ position: "absolute", overflow: "hidden" }}
      aria-hidden="true"
    >
      <defs>
        <marker
          id={EDGE_MARKER_INHIBIT_ID}
          viewBox="-6 -8 12 16"
          refX={0}
          refY={0}
          markerWidth={14}
          markerHeight={14}
          orient="auto"
          markerUnits="userSpaceOnUse"
        >
          <line
            x1={0}
            y1={-7}
            x2={0}
            y2={7}
            stroke={EDGE_COLOR_BLOCKS}
            strokeWidth={2.5}
            strokeLinecap="round"
          />
        </marker>
      </defs>
    </svg>
  );
}

/** Bordered staging pool for tasks not connected into the dependency graph.
 *
 * SPACE ONLY for now — listing the uncategorized tasks inside one clearly
 * bordered box. Drag-out / drop-in interactivity is a later phase; nothing
 * here is draggable.
 */
function UncategorizedPool({ graph }: { graph: GraphPayload }) {
  const poolNodes = useMemo(() => partitionNodes(graph).poolNodes, [graph]);
  if (poolNodes.length === 0) return null;

  return (
    <aside className="stx-todo-pool" aria-label="Uncategorized tasks">
      <div className="stx-todo-pool__title">Uncategorized</div>
      <div className="stx-todo-pool__items">
        {poolNodes.map((n) => {
          const prio = n.priority != null ? ` · p${n.priority}` : "";
          return (
            <div
              key={n.id}
              className="stx-todo-pool__item"
              style={nodeStyle(graph.status_colors[n.status])}
            >
              {n.title}
              {prio}
            </div>
          );
        })}
      </div>
    </aside>
  );
}

export function GraphView({ graph }: { graph: GraphPayload }) {
  const { nodes, edges } = useMemo<{ nodes: Node[]; edges: Edge[] }>(
    () => buildFlow(graph),
    [graph],
  );

  return (
    <div className="stx-todo-flow">
      <InhibitionMarkerDefs />
      <ReactFlow
        nodes={nodes}
        edges={edges}
        fitView
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable={true}
        proOptions={{ hideAttribution: true }}
      >
        <Background color={FLOW_DARK.bgDots} />
        <Controls showInteractive={false} />
        <MiniMap
          pannable
          zoomable
          maskColor={FLOW_DARK.miniMapMask}
          nodeColor={FLOW_DARK.miniMapNode}
          nodeStrokeColor={FLOW_DARK.miniMapNodeStroke}
          style={{ background: FLOW_DARK.miniMapBg }}
        />
      </ReactFlow>
      <UncategorizedPool graph={graph} />
    </div>
  );
}
