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

/** How many direct child tasks does `node` have (any `parent === node.id`)?
 *
 * Powers the parent-node drill-down affordance: BEFORE clicking, the user
 * sees the count baked into the rendered label (a "▸N" badge) so they can
 * predict the click will descend into a subgraph instead of opening the
 * markdown drawer. A non-zero count also flips the click-routing branch.
 */
export function nodeChildCount(graph: GraphPayload, nodeId: string): number {
  let count = 0;
  for (const n of graph.nodes) {
    if (n.parent === nodeId) count += 1;
  }
  return count;
}

/** Does `node` have at least one child task (any `parent === node.id`)?
 *
 * Used by the click handler to decide between drill-down (has children) and
 * the markdown detail drawer (leaf node — existing #9 behavior).
 */
export function nodeHasChildren(graph: GraphPayload, nodeId: string): boolean {
  return nodeChildCount(graph, nodeId) > 0;
}

/** Visual decoration applied ON TOP of `nodeStyle` for nodes that have
 * children — they DRILL IN on click rather than open the detail drawer.
 *
 * Goal: one glance tells drill-vs-detail. The combination here is
 * deliberately layered:
 *   - 3px solid border (vs the standard 2px) THICKENS the outline.
 *   - A 3px purple halo (boxShadow inset-equivalent ring) sits OUTSIDE the
 *     border, giving the node an unmistakable "stacked-card" silhouette.
 *   - Bold weight + a subtle gradient overlay on the existing fill nudge
 *     parents away from looking like flat leaf cards.
 *
 * Status color (fill / stroke) is preserved from `nodeStyle()` so the
 * lifecycle signal (pending / done / blocked / goal …) still reads.
 */
export function parentNodeStyle(base: CSSProperties): CSSProperties {
  return {
    ...base,
    // Override border weight — keep the status stroke color via `borderColor`
    // since `border` shorthand wipes anything not present here.
    borderWidth: 3,
    borderStyle: "solid",
    // The halo ring is the strongest "I'm clickable AND I drill in" signal.
    // Color matches the SciTeX accent purple used by the breadcrumb crumbs
    // so the affordance feels like one design language across the board.
    boxShadow:
      "0 0 0 3px rgba(155, 127, 214, 0.45), 0 2px 8px rgba(0, 0, 0, 0.25)",
    fontWeight: 600,
  };
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
    // Parent-node drill-down AFFORDANCE: combine THREE redundant signals so
    // the user knows BEFORE clicking that a parent will drill in (not open
    // the detail drawer):
    //   (1) a leading "▸" glyph in the label   — expand/disclose icon
    //   (2) a trailing "▸N" child-count badge  — concrete count, not "many"
    //   (3) `parentNodeStyle()` override below — thicker border + purple halo
    // Plus the className flips CSS hover/cursor (zoom-in vs. pointer) so the
    // operator gets a fourth cue during hover (see board.css).
    const kids = nodeChildCount(graph, n.id);
    const isParent = kids > 0;
    const label = isParent
      ? `▸ ${n.title}  ▸${kids}${prio}`
      : `${n.title}${prio}`;
    const base = nodeStyle(graph.status_colors[n.status]);
    return {
      id: n.id,
      position: {
        x: (pos?.x ?? 0) - NODE_W / 2,
        y: (pos?.y ?? 0) - NODE_H / 2,
      },
      data: { label },
      style: isParent ? parentNodeStyle(base) : base,
      // Per-node className is forwarded by React Flow onto the wrapper DOM
      // element — used by board.css to set the hover cursor and tooltip
      // ("drill in" vs "details") and to scope a hover halo brighten.
      className: isParent
        ? "stx-todo-node stx-todo-node--parent"
        : "stx-todo-node stx-todo-node--leaf",
      // Drag/connect flags carried verbatim from the prior implementation —
      // the global `nodesDraggable={true}` on the ReactFlow root governs
      // drag-reorder (PR #7), and edges are never user-creatable.
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
