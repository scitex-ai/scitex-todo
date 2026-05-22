/** Dagre layout: turn the backend graph payload into React Flow nodes/edges.
 *
 * Only `depends_on` edges drive the top->bottom DAG layout (dependencies above
 * dependents). `blocks` edges are drawn as inhibition arrows but excluded from
 * layout so they don't fight the ranking. Within a rank, nodes are ordered by
 * `priority` (lower = earlier) when present.
 *
 * Uncategorized tasks (see `partitionNodes`) are excluded from the graph
 * entirely; they are rendered separately in the bordered staging pool.
 */

import type { CSSProperties } from "react";
import dagre from "dagre";
import { MarkerType, type Edge, type Node } from "@xyflow/react";
import type { GraphNode, GraphPayload, StatusColor } from "./types/board";

const NODE_W = 200;
const NODE_H = 60;

export function nodeStyle(color: StatusColor | undefined): CSSProperties {
  const c = color ?? { fill: "#eceff1", stroke: "#90a4ae", dashed: false };
  return {
    background: c.fill,
    border: `2px ${c.dashed ? "dashed" : "solid"} ${c.stroke}`,
    borderRadius: 8,
    padding: "8px 10px",
    width: NODE_W,
    color: "#222",
    fontSize: 12,
    textAlign: "center",
  };
}

/** Split nodes into the connected dependency graph vs the uncategorized pool.
 *
 * A node is "uncategorized" (belongs in the staging pool) when EITHER:
 *   - its note is exactly "uncategorized", OR
 *   - it has no dependency edges connecting it to the graph: it declares no
 *     `depends_on` AND no other task references it via `depends_on`/`blocks`.
 *
 * Edge participation is derived from `graph.edges` so the rule matches whatever
 * the backend actually emitted (a `depends_on`/`blocks` to a missing id is
 * dropped server-side, so such a node is correctly treated as disconnected).
 */
export function partitionNodes(graph: GraphPayload): {
  graphNodes: GraphNode[];
  poolNodes: GraphNode[];
} {
  const connected = new Set<string>();
  for (const e of graph.edges) {
    connected.add(e.source);
    connected.add(e.target);
  }

  const graphNodes: GraphNode[] = [];
  const poolNodes: GraphNode[] = [];
  for (const n of graph.nodes) {
    const taggedUncategorized = (n.note ?? "").trim() === "uncategorized";
    const disconnected = !connected.has(n.id);
    if (taggedUncategorized || disconnected) {
      poolNodes.push(n);
    } else {
      graphNodes.push(n);
    }
  }
  return { graphNodes, poolNodes };
}

export function buildFlow(graph: GraphPayload): {
  nodes: Node[];
  edges: Edge[];
} {
  const { graphNodes } = partitionNodes(graph);
  const inGraph = new Set(graphNodes.map((n) => n.id));

  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: "TB", nodesep: 40, ranksep: 70 });
  g.setDefaultEdgeLabel(() => ({}));

  for (const n of graphNodes) {
    g.setNode(n.id, { width: NODE_W, height: NODE_H });
  }
  for (const e of graph.edges) {
    if (
      e.kind === "depends_on" &&
      inGraph.has(e.source) &&
      inGraph.has(e.target)
    ) {
      g.setEdge(e.source, e.target);
    }
  }
  dagre.layout(g);

  const nodes: Node[] = graphNodes.map((n) => {
    const pos = g.node(n.id);
    const prio = n.priority != null ? ` · p${n.priority}` : "";
    const label = `${n.title}${prio}`;
    return {
      id: n.id,
      position: {
        x: (pos?.x ?? 0) - NODE_W / 2,
        y: (pos?.y ?? 0) - NODE_H / 2,
      },
      data: { label },
      style: nodeStyle(graph.status_colors[n.status]),
      // READ-ONLY board (MVP): no drag handlers yet.
      draggable: false,
      connectable: false,
    };
  });

  const edges: Edge[] = graph.edges
    .filter((e) => inGraph.has(e.source) && inGraph.has(e.target))
    .map((e, i) => {
      const isBlock = e.kind === "blocks";
      return {
        id: `e${i}-${e.source}-${e.target}`,
        source: e.source,
        target: e.target,
        type: "smoothstep",
        animated: false,
        label: isBlock ? "⊣ blocks" : undefined,
        style: {
          stroke: isBlock ? "#c62828" : "#607d8b",
          strokeWidth: 2,
          strokeDasharray: isBlock ? "6 4" : undefined,
        },
        markerEnd: isBlock
          ? undefined
          : { type: MarkerType.ArrowClosed, color: "#607d8b" },
      };
    });

  return { nodes, edges };
}
