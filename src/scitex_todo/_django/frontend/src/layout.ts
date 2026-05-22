/** Dagre layout: turn the backend graph payload into React Flow nodes/edges.
 *
 * Only `depends_on` edges drive the top->bottom DAG layout (dependencies above
 * dependents). `blocks` edges are drawn as inhibition arrows but excluded from
 * layout so they don't fight the ranking. Within a rank, nodes are ordered by
 * `priority` (lower = earlier) when present.
 *
 * Uncategorized tasks (see `partitionNodes`) are excluded from the graph
 * entirely; they are rendered separately in the bordered staging pool.
 *
 * Nested-graph drill-down: at any given moment the canvas shows ONE scope —
 * either the top-level (`scope: null` → nodes whose `parent` is null/absent)
 * or a parent's children (`scope: <parent-id>` → nodes whose `parent`
 * equals that id). `buildFlow` and `partitionNodes` are both
 * scope-parameterized so the same renderer drives every level. Edges
 * between visible nodes are kept; edges that cross the scope boundary are
 * dropped (siblings only). See `scopeNodes` and `nodeHasChildren`.
 */

import type { CSSProperties } from "react";
import dagre from "dagre";
import { MarkerType, type Edge, type Node } from "@xyflow/react";
import type { GraphNode, GraphPayload, StatusColor } from "./types/board";
import { INHIBITION_EDGE_TYPE } from "./InhibitionEdge";

const NODE_W = 200;
const NODE_H = 60;

/** Edge colors.
 *
 * `depends_on` (→) is the default neutral arrow; `blocks` (⊣) is rendered by
 * the custom `InhibitionEdge` component (full-length solid line + perpendicular
 * tee at the target endpoint, NO text label — see InhibitionEdge.tsx for why
 * we own the rendering instead of using `markerEnd: url(#…)`). Both edges share
 * the same body weight so they read as siblings — only the color and the
 * end-cap (arrowhead vs tee) distinguish them.
 */
export const EDGE_COLOR_DEPENDS = "#607d8b";
export const EDGE_COLOR_BLOCKS = "#c62828";

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

/** Restrict `graph.nodes` to those visible at the given drill-down scope.
 *
 * `scope === null` ⇒ top-level: nodes whose `parent` is null/undefined.
 * `scope === <id>` ⇒ children of <id>: nodes whose `parent` equals <id>.
 *
 * A node whose `parent` references an id not present in `graph.nodes` is
 * "orphaned"; we hoist it to the top level so it stays reachable even if
 * the operator deletes the umbrella mid-edit (same lenient stance as edges
 * to unknown ids, which the backend drops).
 */
export function scopeNodes(
  graph: GraphPayload,
  scope: string | null,
): GraphNode[] {
  const ids = new Set(graph.nodes.map((n) => n.id));
  return graph.nodes.filter((n) => {
    const parent = n.parent ?? null;
    if (scope === null) {
      // Top-level: explicit `parent: null` OR an orphaned reference.
      return parent === null || !ids.has(parent);
    }
    return parent === scope;
  });
}

/** Does `node` have at least one child task (any `parent === node.id`)?
 *
 * Used by the click handler to decide between drill-down (has children) and
 * the markdown detail drawer (leaf node — existing #9 behavior).
 */
export function nodeHasChildren(graph: GraphPayload, nodeId: string): boolean {
  return graph.nodes.some((n) => n.parent === nodeId);
}

/** Split nodes into the connected dependency graph vs the uncategorized pool.
 *
 * Scope-parameterized: operates only on nodes visible at the given drill-down
 * scope. Edges between two scope-visible nodes count for "connected"; edges
 * crossing the scope boundary are ignored (a child node connected ONLY to a
 * sibling at another level is treated as disconnected for THIS view).
 *
 * A node is "uncategorized" (belongs in the staging pool) when EITHER:
 *   - its note is exactly "uncategorized", OR
 *   - it has no dependency edges connecting it to the graph at this scope.
 */
export function partitionNodes(
  graph: GraphPayload,
  scope: string | null = null,
): {
  graphNodes: GraphNode[];
  poolNodes: GraphNode[];
} {
  const visible = scopeNodes(graph, scope);
  const visibleIds = new Set(visible.map((n) => n.id));

  const connected = new Set<string>();
  for (const e of graph.edges) {
    if (visibleIds.has(e.source) && visibleIds.has(e.target)) {
      connected.add(e.source);
      connected.add(e.target);
    }
  }

  const graphNodes: GraphNode[] = [];
  const poolNodes: GraphNode[] = [];
  for (const n of visible) {
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

export function buildFlow(
  graph: GraphPayload,
  scope: string | null = null,
): {
  nodes: Node[];
  edges: Edge[];
} {
  const { graphNodes } = partitionNodes(graph, scope);
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
    // Affordance: a "▾" glyph after the title hints that clicking this node
    // will drill in (vs. opening the markdown drawer for a leaf). Cheap +
    // unambiguous; no extra DOM, survives React Flow's default label render.
    const drillHint = nodeHasChildren(graph, n.id) ? " ▾" : "";
    const label = `${n.title}${drillHint}${prio}`;
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
      // depends_on: default smoothstep edge with an arrowhead marker.
      // blocks:    custom `inhibition` edge (InhibitionEdge.tsx) — same body
      //            line as depends_on but with a perpendicular tee instead of
      //            an arrowhead, and NO text label (bar alone carries it).
      return {
        id: `e${i}-${e.source}-${e.target}`,
        source: e.source,
        target: e.target,
        type: isBlock ? INHIBITION_EDGE_TYPE : "smoothstep",
        animated: false,
        style: {
          stroke: isBlock ? EDGE_COLOR_BLOCKS : EDGE_COLOR_DEPENDS,
          strokeWidth: 2,
        },
        markerEnd: isBlock
          ? undefined
          : { type: MarkerType.ArrowClosed, color: EDGE_COLOR_DEPENDS },
      };
    });

  return { nodes, edges };
}
