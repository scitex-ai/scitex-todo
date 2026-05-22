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
import { buildFlow } from "./layout";
import type { GraphPayload } from "./types/board";

export function GraphView({ graph }: { graph: GraphPayload }) {
  const { nodes, edges } = useMemo<{ nodes: Node[]; edges: Edge[] }>(
    () => buildFlow(graph),
    [graph],
  );

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      fitView
      nodesDraggable={false}
      nodesConnectable={false}
      elementsSelectable={true}
      proOptions={{ hideAttribution: true }}
    >
      <Background />
      <Controls showInteractive={false} />
      <MiniMap pannable zoomable />
    </ReactFlow>
  );
}
