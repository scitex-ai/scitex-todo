/** React Flow rendering of the task dependency graph.
 *
 * Interaction model:
 *   - Graph nodes are DRAGGABLE. On drag-end, all current node positions
 *     are sorted top→bottom (then left→right within a y-band) to derive a
 *     priority order, which is POSTed to `/priority` via the board store
 *     (`reorderPriority`). The backend writes the YAML via `save_tasks`,
 *     and the store reloads the graph from the canonical source of truth.
 *   - Pool / uncategorized nodes are NOT yet draggable (kept as a static
 *     bordered list); they retain their existing priorities on drag-reorder.
 *   - Edges are not connectable; selection-only is fine.
 *   - Click routing depends on whether the clicked node HAS CHILDREN
 *     (any other task with `parent === node.id`):
 *       * has-children   → DRILL IN (`drillInto(id)`): canvas re-renders to
 *                          that node's child subgraph; the breadcrumb tracks
 *                          the descent so the user can navigate back.
 *       * leaf (default) → open the markdown detail drawer (PR #9 behavior).
 *
 * The local node state mirrors React Flow's drag mutations via
 * `onNodesChange`; the buildFlow result re-seeds it whenever the store
 * graph reloads (so a server reload after a successful reorder snaps the
 * UI to the canonical layout), or whenever the drill-down scope changes.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type MouseEvent as ReactMouseEvent,
} from "react";
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  applyNodeChanges,
  type Edge,
  type EdgeTypes,
  type Node,
  type NodeChange,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import {
  buildFlow,
  nodeChildCount,
  nodeHasChildren,
  nodeStyle,
  parentNodeStyle,
  partitionNodes,
} from "./layout";
import { InhibitionEdge, INHIBITION_EDGE_TYPE } from "./InhibitionEdge";
import { NodeDetailPanelContainer } from "./NodeDetailPanel";
import { useBoardStore } from "./store/useBoardStore";
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

/** Custom edge-type registry — `blocks` edges render via InhibitionEdge.
 *
 * Defined at module scope (not inline in the JSX) so the object identity is
 * stable across renders. React Flow warns when `edgeTypes` is a fresh object
 * each render — it forces a full edge-component re-mount.
 */
const EDGE_TYPES: EdgeTypes = {
  [INHIBITION_EDGE_TYPE]: InhibitionEdge,
};

/** Vertical band (px) within which two nodes are treated as the same "row"
 * for the purposes of ordering, so a small drag wiggle on the y-axis doesn't
 * shuffle priority. Picked to be smaller than the dagre `ranksep`. */
const Y_BAND_PX = 24;

/** Sort node ids by (y, x) screen position into a top-priority-first list. */
export function nodesToPriorityOrder(nodes: Node[]): string[] {
  return [...nodes]
    .sort((a, b) => {
      const dy = a.position.y - b.position.y;
      if (Math.abs(dy) > Y_BAND_PX) return dy;
      return a.position.x - b.position.x;
    })
    .map((n) => n.id);
}

/** Breadcrumb bar above the canvas. Each crumb sets the drill path to a
 * prefix of the current path: `Home` jumps to top-level (depth 0); a parent
 * crumb pops to that level. The current scope's crumb is rendered as
 * non-interactive text so the user can't navigate "to themselves". */
function Breadcrumb({
  graph,
  drillPath,
  drillTo,
}: {
  graph: GraphPayload;
  drillPath: string[];
  drillTo: (depth: number) => void;
}) {
  // Resolve each crumb id to its task title; fall back to the id if the task
  // was deleted underneath us (operator removed the parent mid-edit).
  const titles = useMemo(() => {
    const byId = new Map(graph.nodes.map((n) => [n.id, n.title]));
    return drillPath.map((id) => byId.get(id) ?? id);
  }, [graph.nodes, drillPath]);

  // Hide the bar entirely at the top-level — there's nowhere to navigate.
  // Operator gets a clean canvas when not drilled in.
  if (drillPath.length === 0) return null;

  return (
    <nav className="stx-todo-breadcrumb" aria-label="Drill-down breadcrumb">
      <button
        type="button"
        className="stx-todo-breadcrumb__crumb"
        onClick={() => drillTo(0)}
      >
        Home
      </button>
      {titles.map((title, idx) => {
        const isCurrent = idx === titles.length - 1;
        const separator = (
          <span className="stx-todo-breadcrumb__sep" aria-hidden="true">
            /
          </span>
        );
        if (isCurrent) {
          return (
            <span key={`${drillPath[idx]}-${idx}`}>
              {separator}
              <span
                className="stx-todo-breadcrumb__crumb stx-todo-breadcrumb__crumb--current"
                aria-current="page"
              >
                {title}
              </span>
            </span>
          );
        }
        // depth = idx + 1 keeps the first idx+1 crumbs.
        return (
          <span key={`${drillPath[idx]}-${idx}`}>
            {separator}
            <button
              type="button"
              className="stx-todo-breadcrumb__crumb"
              onClick={() => drillTo(idx + 1)}
            >
              {title}
            </button>
          </span>
        );
      })}
    </nav>
  );
}

/** Bordered staging pool for tasks not connected into the dependency graph.
 *
 * SPACE ONLY for now — listing the uncategorized tasks inside one clearly
 * bordered box. Drag-out / drop-in interactivity is a later phase; nothing
 * here is draggable. Each item IS clickable: a parent (any task whose
 * `parent` references it) DRILLS IN; a leaf opens the same NodeDetailPanel
 * as the graph nodes (markdown note + metadata).
 */
function UncategorizedPool({
  graph,
  scope,
}: {
  graph: GraphPayload;
  scope: string | null;
}) {
  const poolNodes = useMemo(
    () => partitionNodes(graph, scope).poolNodes,
    [graph, scope],
  );
  const selectNode = useBoardStore((s) => s.selectNode);
  const drillInto = useBoardStore((s) => s.drillInto);
  if (poolNodes.length === 0) return null;

  return (
    <aside className="stx-todo-pool" aria-label="Uncategorized tasks">
      <div className="stx-todo-pool__title">Uncategorized</div>
      <div className="stx-todo-pool__items">
        {poolNodes.map((n) => {
          const prio = n.priority != null ? ` · p${n.priority}` : "";
          const kids = nodeChildCount(graph, n.id);
          const hasChildren = kids > 0;
          // Affordance parity with the graph nodes: a parent button shows
          // a leading "▸" disclosure glyph + trailing "▸N" child-count
          // badge, gets a thicker bordered/halo style via parentNodeStyle,
          // and flips its className so board.css can switch the hover
          // cursor (zoom-in vs. pointer) and tooltip wording.
          const baseStyle = nodeStyle(graph.status_colors[n.status]);
          const style = hasChildren ? parentNodeStyle(baseStyle) : baseStyle;
          const onClick = () =>
            hasChildren ? drillInto(n.id) : selectNode(n.id);
          return (
            <button
              type="button"
              key={n.id}
              className={
                hasChildren
                  ? "stx-todo-pool__item stx-todo-pool__item--parent"
                  : "stx-todo-pool__item stx-todo-pool__item--leaf"
              }
              style={style}
              onClick={onClick}
              title={hasChildren ? "Drill in" : "Details"}
              aria-label={
                hasChildren
                  ? `Drill into ${n.title} (${kids} ${
                      kids === 1 ? "child" : "children"
                    })`
                  : `Open details for ${n.title}`
              }
            >
              {hasChildren ? `▸ ${n.title}  ▸${kids}` : n.title}
              {prio}
            </button>
          );
        })}
      </div>
    </aside>
  );
}

export function GraphView({ graph }: { graph: GraphPayload }) {
  const reorderPriority = useBoardStore((s) => s.reorderPriority);
  const saving = useBoardStore((s) => s.saving);
  const selectNode = useBoardStore((s) => s.selectNode);
  const drillPath = useBoardStore((s) => s.drillPath);
  const drillInto = useBoardStore((s) => s.drillInto);
  const drillTo = useBoardStore((s) => s.drillTo);

  // Current drill-down scope = deepest crumb (null at top-level).
  const scope = drillPath.length > 0 ? drillPath[drillPath.length - 1] : null;

  // Seed from buildFlow each time the canonical graph payload OR scope
  // changes — that covers initial load, reload-after-save, AND drill
  // in/out. Node positions are then mutated locally as the user drags
  // (via `onNodesChange`).
  const seeded = useMemo<{ nodes: Node[]; edges: Edge[] }>(
    () => buildFlow(graph, scope),
    [graph, scope],
  );
  const [nodes, setNodes] = useState<Node[]>(seeded.nodes);
  const [edges, setEdges] = useState<Edge[]>(seeded.edges);

  useEffect(() => {
    setNodes(seeded.nodes);
    setEdges(seeded.edges);
  }, [seeded]);

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    setNodes((current) => applyNodeChanges(changes, current));
  }, []);

  /** When the user finishes a drag, recompute the priority order from the
   * resulting node positions and persist via the backend. The functional
   * setNodes form reads the freshest positions without depending on `nodes`
   * in the callback identity. */
  const onNodeDragStop = useCallback(() => {
    setNodes((current) => {
      const order = nodesToPriorityOrder(current);
      // Fire-and-forget: the store handles re-fetch + error rollback.
      void reorderPriority(order);
      return current;
    });
  }, [reorderPriority]);

  /** Click on a graph node:
   *   - has-children → DRILL IN (re-render the canvas to the child subgraph).
   *   - leaf         → open the markdown detail drawer (PR #9 behavior).
   *
   * React Flow fires this on mouseup AFTER the (possibly zero-distance)
   * drag, so single clicks reliably reach here. The drag-end handler does
   * its own persistence work and they don't conflict — a true click that
   * doesn't move past the dnd threshold results in a no-op
   * `onNodeDragStop` (the order is unchanged, so the POSTed array equals
   * the current state). */
  const onNodeClick = useCallback(
    (_event: ReactMouseEvent, node: Node) => {
      if (nodeHasChildren(graph, node.id)) {
        drillInto(node.id);
      } else {
        selectNode(node.id);
      }
    },
    [graph, drillInto, selectNode],
  );

  return (
    <div className={`stx-todo-flow${saving ? " stx-todo-flow--saving" : ""}`}>
      <Breadcrumb graph={graph} drillPath={drillPath} drillTo={drillTo} />
      <ReactFlow
        nodes={nodes}
        edges={edges}
        edgeTypes={EDGE_TYPES}
        onNodesChange={onNodesChange}
        onNodeDragStop={onNodeDragStop}
        onNodeClick={onNodeClick}
        fitView
        nodesDraggable={true}
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
      <UncategorizedPool graph={graph} scope={scope} />
      <NodeDetailPanelContainer />
    </div>
  );
}
