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
 *
 * The local node state mirrors React Flow's drag mutations via
 * `onNodesChange`; the buildFlow result re-seeds it whenever the store
 * graph reloads (so a server reload after a successful reorder snaps the
 * UI to the canonical layout).
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
import { buildFlow, nodeStyle, partitionNodes } from "./layout";
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

/** Bordered staging pool for tasks not connected into the dependency graph.
 *
 * SPACE ONLY for now — listing the uncategorized tasks inside one clearly
 * bordered box. Drag-out / drop-in interactivity is a later phase; nothing
 * here is draggable. Each item IS clickable: click → opens the same
 * NodeDetailPanel as the graph nodes (markdown note + metadata).
 */
function UncategorizedPool({ graph }: { graph: GraphPayload }) {
  const poolNodes = useMemo(() => partitionNodes(graph).poolNodes, [graph]);
  const selectNode = useBoardStore((s) => s.selectNode);
  if (poolNodes.length === 0) return null;

  return (
    <aside className="stx-todo-pool" aria-label="Uncategorized tasks">
      <div className="stx-todo-pool__title">Uncategorized</div>
      <div className="stx-todo-pool__items">
        {poolNodes.map((n) => {
          const prio = n.priority != null ? ` · p${n.priority}` : "";
          return (
            <button
              type="button"
              key={n.id}
              className="stx-todo-pool__item"
              style={nodeStyle(graph.status_colors[n.status])}
              onClick={() => selectNode(n.id)}
              aria-label={`Open details for ${n.title}`}
            >
              {n.title}
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

  // Seed from buildFlow each time the canonical graph payload changes — that
  // covers both initial load and reload-after-save. Node positions are then
  // mutated locally as the user drags (via `onNodesChange`).
  const seeded = useMemo<{ nodes: Node[]; edges: Edge[] }>(
    () => buildFlow(graph),
    [graph],
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

  /** Click on a graph node → open the detail drawer for that task. React
   * Flow fires this on mouseup AFTER the (possibly zero-distance) drag, so
   * single clicks reliably reach here. The drag-end handler does its own
   * persistence work and they don't conflict — clicks that don't move past
   * the dnd threshold result in a no-op `onNodeDragStop` (the order is
   * unchanged, so the POSTed array equals the current state).
   */
  const onNodeClick = useCallback(
    (_event: ReactMouseEvent, node: Node) => {
      selectNode(node.id);
    },
    [selectNode],
  );

  return (
    <div className={`stx-todo-flow${saving ? " stx-todo-flow--saving" : ""}`}>
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
      <UncategorizedPool graph={graph} />
      <NodeDetailPanelContainer />
    </div>
  );
}
