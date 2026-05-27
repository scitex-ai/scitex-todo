/** React Flow rendering of the task dependency graph.
 *
 * Interaction model:
 *   - Graph nodes are DRAGGABLE. On drag-end, all current node positions
 *     are sorted top→bottom (then left→right within a y-band) to derive a
 *     priority order, which is POSTed to `/priority` via the board store
 *     (`reorderPriority`). The backend writes the YAML via `save_tasks`,
 *     and the store reloads the graph from the canonical source of truth.
 *   - The Uncategorized pool is a DOCKED, collapsible left sidebar (not a
 *     floating overlay) so it never covers the canvas.
 *   - The toolbar filter (search + status chips) DIMS non-matching graph
 *     nodes and HIDES non-matching pool items.
 *   - Click routing depends on whether the clicked node HAS CHILDREN:
 *       * has-children → DRILL IN; leaf → open the markdown detail drawer.
 *   - `fitView` re-runs whenever the drill scope or filter changes, so the
 *     visible nodes are always centered (no dead band).
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type MouseEvent as ReactMouseEvent,
} from "react";
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  applyNodeChanges,
  useReactFlow,
  type Connection,
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
import {
  STATUSES,
  taskMatchesFilter,
  useBoardStore,
} from "./store/useBoardStore";
import { useTheme } from "./useTheme";
import type { GraphNode, GraphPayload } from "./types/board";

/* Canvas chrome (Background dots + MiniMap) per theme. React Flow's own
 * `colorMode` handles node/edge/control base theming; these are the bits it
 * doesn't infer, so we flip them alongside the shell's data-theme. */
const FLOW_CHROME = {
  dark: {
    miniMapBg: "#1b1b29",
    miniMapMask: "rgba(20, 20, 32, 0.78)",
    miniMapNode: "#3a3a52",
    miniMapNodeStroke: "#9b7fd6",
    bgDots: "#33334a",
  },
  light: {
    miniMapBg: "#f3f2f0",
    miniMapMask: "rgba(225, 223, 219, 0.78)",
    miniMapNode: "#c9c4d8",
    miniMapNodeStroke: "#6a4fb0",
    bgDots: "#c8c6c2",
  },
} as const;

const EDGE_TYPES: EdgeTypes = {
  [INHIBITION_EDGE_TYPE]: InhibitionEdge,
};

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

/** Re-fit the viewport whenever `dep` changes (drill scope / filter / reload),
 * so the currently-visible nodes are always centered with no dead band.
 * When `focusIds` is non-empty (an active search/filter), zoom to JUST those
 * matching nodes — a "search-jump" — instead of fitting the whole scope.
 * Lives inside <ReactFlow> so `useReactFlow()` has a provider. */
function FitOnChange({ dep, focusIds }: { dep: string; focusIds: string[] }) {
  const { fitView } = useReactFlow();
  useEffect(() => {
    // rAF: let React Flow apply the new nodes before measuring.
    const id = requestAnimationFrame(() => {
      if (focusIds.length > 0) {
        fitView({
          padding: 0.3,
          duration: 300,
          maxZoom: 1.5,
          nodes: focusIds.map((nid) => ({ id: nid })),
        });
      } else {
        fitView({ padding: 0.2, duration: 200 });
      }
    });
    return () => cancelAnimationFrame(id);
    // focusIds is folded into `dep` by the caller, so depend on dep only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dep, fitView]);
  return null;
}

/** Breadcrumb bar above the canvas. */
function Breadcrumb({
  graph,
  drillPath,
  drillTo,
}: {
  graph: GraphPayload;
  drillPath: string[];
  drillTo: (depth: number) => void;
}) {
  const titles = useMemo(() => {
    const byId = new Map(graph.nodes.map((n) => [n.id, n.title]));
    return drillPath.map((id) => byId.get(id) ?? id);
  }, [graph.nodes, drillPath]);

  // Always rendered (even at the top level, where it shows just "Home") so it
  // occupies a fixed strip and the layout never jumps as you drill in/out.
  const atTop = drillPath.length === 0;

  return (
    <nav className="stx-todo-breadcrumb" aria-label="Drill-down breadcrumb">
      {atTop ? (
        <span
          className="stx-todo-breadcrumb__crumb stx-todo-breadcrumb__crumb--current"
          aria-current="page"
        >
          Home
        </span>
      ) : (
        <button
          type="button"
          className="stx-todo-breadcrumb__crumb"
          onClick={() => drillTo(0)}
        >
          Home
        </button>
      )}
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

/** Docked, collapsible sidebar of tasks not connected into the dependency
 * graph (note == "uncategorized" or no sibling edges at this scope). Honors
 * the toolbar filter: non-matching items are hidden. */
function UncategorizedPool({
  graph,
  scope,
  query,
  activeStatuses,
}: {
  graph: GraphPayload;
  scope: string | null;
  query: string;
  activeStatuses: string[];
}) {
  const poolNodes = useMemo(
    () => partitionNodes(graph, scope).poolNodes,
    [graph, scope],
  );
  const visible = useMemo(
    () => poolNodes.filter((n) => taskMatchesFilter(n, query, activeStatuses)),
    [poolNodes, query, activeStatuses],
  );
  const selectNode = useBoardStore((s) => s.selectNode);
  const drillInto = useBoardStore((s) => s.drillInto);
  const openMenu = useBoardStore((s) => s.openMenu);
  const beginCreate = useBoardStore((s) => s.beginCreate);
  // Collapsed by default so the canvas owns the full width on load; the
  // operator expands the pool via its title toggle when they want it.
  const [open, setOpen] = useState(false);

  // Group the visible pool items by status, in canonical STATUSES order, so
  // the flat inbox reads as collapsible structural sections instead of one
  // long undifferentiated list.
  const groups = useMemo(() => {
    const byStatus = new Map<string, GraphNode[]>();
    for (const n of visible) {
      const arr = byStatus.get(n.status) ?? [];
      arr.push(n);
      byStatus.set(n.status, arr);
    }
    const ordered: { status: string; items: GraphNode[] }[] = [];
    for (const s of STATUSES) {
      const items = byStatus.get(s);
      if (items && items.length) ordered.push({ status: s, items });
    }
    // Any status not in the canonical list (defensive) trails alphabetically.
    for (const [s, items] of [...byStatus.entries()].sort()) {
      if (!STATUSES.includes(s as (typeof STATUSES)[number])) {
        ordered.push({ status: s, items });
      }
    }
    return ordered;
  }, [visible]);

  if (poolNodes.length === 0) return null;

  const renderItem = (n: GraphNode) => {
    const prio = n.priority != null ? ` · p${n.priority}` : "";
    const kids = nodeChildCount(graph, n.id);
    const hasChildren = kids > 0;
    const baseStyle = nodeStyle(graph.status_colors[n.status]);
    const style = hasChildren
      ? parentNodeStyle(baseStyle, kids, graph.status_colors[n.status])
      : baseStyle;
    const onClick = () => (hasChildren ? drillInto(n.id) : selectNode(n.id));
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
        onContextMenu={(e) => {
          e.preventDefault();
          openMenu(e.clientX, e.clientY, n.id);
        }}
        title={hasChildren ? "Drill in (right-click to edit)" : "Details (right-click to edit)"}
        aria-label={
          hasChildren
            ? `Drill into ${n.title} (${kids} ${
                kids === 1 ? "child" : "children"
              })`
            : `Open details for ${n.title}`
        }
      >
        {hasChildren ? `▸ ${n.title}  ▸${kids}` : n.title}
        {n.repo ? ` · ${n.repo}` : ""}
        {prio}
        {n.comments?.length ? `  💬${n.comments.length}` : ""}
      </button>
    );
  };

  // Collapsed: a thin rail with just an expand affordance, so the canvas
  // reclaims the full width instead of reserving an empty sidebar column.
  if (!open) {
    return (
      <aside
        className="stx-todo-pool stx-todo-pool--collapsed"
        aria-label="Uncategorized tasks (collapsed)"
      >
        <button
          type="button"
          className="stx-todo-pool__expand"
          onClick={() => setOpen(true)}
          aria-expanded={false}
          title={`Show uncategorized (${visible.length})`}
          aria-label={`Show uncategorized (${visible.length} tasks)`}
        >
          <span className="stx-todo-pool__expand-glyph" aria-hidden="true">
            ▸
          </span>
          <span className="stx-todo-pool__expand-label">
            Uncategorized ({visible.length})
          </span>
        </button>
      </aside>
    );
  }

  return (
    <aside className="stx-todo-pool" aria-label="Uncategorized tasks">
      <div className="stx-todo-pool__head">
        <button
          type="button"
          className="stx-todo-pool__title"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          title="Collapse"
        >
          ▾ Uncategorized ({visible.length})
        </button>
        <button
          type="button"
          className="stx-todo-pool__new"
          onClick={beginCreate}
          title="New task"
          aria-label="New task"
        >
          ✚
        </button>
      </div>
      {open && (
        <div className="stx-todo-pool__groups">
          {groups.map(({ status, items }) => (
            <details className="stx-todo-pool__group" key={status}>
              <summary className="stx-todo-pool__group-summary">
                <span
                  className="stx-todo-pool__group-swatch"
                  style={{
                    background: graph.status_colors[status]?.fill ?? "#888",
                    borderColor: graph.status_colors[status]?.stroke ?? "#888",
                  }}
                  aria-hidden="true"
                />
                {status} ({items.length})
              </summary>
              <div className="stx-todo-pool__items">
                {items.map(renderItem)}
              </div>
            </details>
          ))}
          {visible.length === 0 && (
            <span className="stx-todo-pool__empty">no matches</span>
          )}
        </div>
      )}
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
  const query = useBoardStore((s) => s.query);
  const activeStatuses = useBoardStore((s) => s.activeStatuses);
  const openMenu = useBoardStore((s) => s.openMenu);
  const setEdge = useBoardStore((s) => s.setEdge);
  const openEdgePicker = useBoardStore((s) => s.openEdgePicker);
  const updateTask = useBoardStore((s) => s.updateTask);

  // Last pointer position, so onConnect (which has no mouse coords) can place
  // the edge-kind picker where the connection was dropped.
  const pointer = useRef({ x: 0, y: 0 });
  useEffect(() => {
    const onMove = (e: PointerEvent) => {
      pointer.current = { x: e.clientX, y: e.clientY };
    };
    window.addEventListener("pointermove", onMove);
    return () => window.removeEventListener("pointermove", onMove);
  }, []);

  const theme = useTheme();
  const chrome = FLOW_CHROME[theme];

  const scope = drillPath.length > 0 ? drillPath[drillPath.length - 1] : null;

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

  // Lookup task metadata by id so we can test the filter against the
  // draggable node state (whose `data` only carries the rendered label).
  const byId = useMemo(() => {
    const m = new Map<string, GraphNode>();
    for (const n of graph.nodes) m.set(n.id, n);
    return m;
  }, [graph.nodes]);

  const filtering = query.trim().length > 0 || activeStatuses.length > 0;

  // Dim non-matching graph nodes (keep them in place so the structure reads).
  const viewNodes = useMemo<Node[]>(() => {
    if (!filtering) return nodes;
    return nodes.map((n) => {
      const task = byId.get(n.id);
      const match = task
        ? taskMatchesFilter(task, query, activeStatuses)
        : true;
      return match
        ? { ...n, style: { ...n.style, opacity: 1 } }
        : { ...n, style: { ...n.style, opacity: 0.16 } };
    });
  }, [nodes, byId, filtering, query, activeStatuses]);

  // Ids of the in-scope nodes that match the active filter — the search-jump
  // target so the viewport zooms to the matches instead of the whole scope.
  const focusIds = useMemo<string[]>(() => {
    if (!filtering) return [];
    return nodes
      .filter((n) => {
        const task = byId.get(n.id);
        return task ? taskMatchesFilter(task, query, activeStatuses) : false;
      })
      .map((n) => n.id);
  }, [nodes, byId, filtering, query, activeStatuses]);

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    setNodes((current) => applyNodeChanges(changes, current));
  }, []);

  // Drag-to-connect / drag-to-group, by PROXIMITY to the nearest other card:
  //   heavy overlap (≥50%)         → group: dragged card nests under target
  //   close (gap within threshold) → offer an edge (arrow/blocker picker),
  //                                   dragged = source, nearby card = target
  //   neither                      → reposition (persist the priority order)
  // Proximity (not just overlap) is the point — you don't have to land on the
  // circle handle, just drag a card near another.
  const GROUP_OVERLAP = 0.5;
  const PROXIMITY_PX = 60;
  const onNodeDragStop = useCallback(
    (event: ReactMouseEvent, node: Node) => {
      setNodes((current) => {
        const rect = (n: Node) => {
          const m = (n as { measured?: { width?: number; height?: number } })
            .measured;
          return {
            x: n.position.x,
            y: n.position.y,
            w: m?.width ?? 200,
            h: m?.height ?? 60,
          };
        };
        const dr = rect(node);
        const dArea = Math.max(1, dr.w * dr.h);
        let near: Node | null = null;
        let nearGap = Infinity;
        let nearRatio = 0;
        for (const o of current) {
          if (o.id === node.id) continue;
          const r = rect(o);
          const ox = Math.max(
            0,
            Math.min(dr.x + dr.w, r.x + r.w) - Math.max(dr.x, r.x),
          );
          const oy = Math.max(
            0,
            Math.min(dr.y + dr.h, r.y + r.h) - Math.max(dr.y, r.y),
          );
          // Edge-to-edge gap (0 when the boxes overlap on that axis).
          const gx = Math.max(0, r.x - (dr.x + dr.w), dr.x - (r.x + r.w));
          const gy = Math.max(0, r.y - (dr.y + dr.h), dr.y - (r.y + r.h));
          const gap = Math.hypot(gx, gy);
          if (gap < nearGap) {
            nearGap = gap;
            nearRatio = (ox * oy) / dArea;
            near = o;
          }
        }
        if (near && nearRatio >= GROUP_OVERLAP) {
          void updateTask(node.id, { parent: near.id });
        } else if (near && nearGap <= PROXIMITY_PX) {
          openEdgePicker(node.id, near.id, event.clientX, event.clientY);
        } else {
          void reorderPriority(nodesToPriorityOrder(current));
        }
        return current;
      });
    },
    [reorderPriority, updateTask, openEdgePicker],
  );

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

  // Right-click a node -> card context menu (edit / status / delete).
  const onNodeContextMenu = useCallback(
    (event: ReactMouseEvent, node: Node) => {
      event.preventDefault();
      openMenu(event.clientX, event.clientY, node.id);
    },
    [openMenu],
  );

  // Right-click empty canvas -> pane context menu (new task).
  const onPaneContextMenu = useCallback(
    (event: MouseEvent | ReactMouseEvent) => {
      event.preventDefault();
      openMenu(
        (event as ReactMouseEvent).clientX,
        (event as ReactMouseEvent).clientY,
        null,
      );
    },
    [openMenu],
  );

  // Drag from one node's handle to another -> offer a kind choice (arrow =
  // depends_on, blocker = blocks) at the drop point, rather than assuming one.
  const onConnect = useCallback(
    (c: Connection) => {
      if (c.source && c.target && c.source !== c.target) {
        const p = pointer.current;
        openEdgePicker(c.source, c.target, p.x, p.y);
      }
    },
    [openEdgePicker],
  );

  // Right-click an edge -> confirm + delete it (kind carried in edge.data).
  const onEdgeContextMenu = useCallback(
    (event: ReactMouseEvent, edge: Edge) => {
      event.preventDefault();
      const kind =
        (edge.data?.kind as "depends_on" | "blocks" | undefined) ??
        "depends_on";
      const label = kind === "blocks" ? "blocks" : "depends-on";
      if (window.confirm(`Delete this ${label} edge?`)) {
        void setEdge("remove", kind, edge.source, edge.target);
      }
    },
    [setEdge],
  );

  // Re-fit key: scope + filter + node count drive a viewport re-fit.
  const fitKey = `${scope ?? "_top"}|${query}|${activeStatuses
    .slice()
    .sort()
    .join(",")}|${seeded.nodes.length}`;

  return (
    <div className={`stx-todo-flow${saving ? " stx-todo-flow--saving" : ""}`}>
      <Breadcrumb graph={graph} drillPath={drillPath} drillTo={drillTo} />
      <div className="stx-todo-flow__body">
        <UncategorizedPool
          graph={graph}
          scope={scope}
          query={query}
          activeStatuses={activeStatuses}
        />
        <div className="stx-todo-flow__canvas">
          <ReactFlow
            nodes={viewNodes}
            edges={edges}
            edgeTypes={EDGE_TYPES}
            onNodesChange={onNodesChange}
            onNodeDragStop={onNodeDragStop}
            onNodeClick={onNodeClick}
            onNodeContextMenu={onNodeContextMenu}
            onPaneContextMenu={onPaneContextMenu}
            onConnect={onConnect}
            onEdgeContextMenu={onEdgeContextMenu}
            fitView
            fitViewOptions={{ padding: 0.2 }}
            colorMode={theme}
            nodesDraggable={true}
            nodesConnectable={true}
            elementsSelectable={true}
            proOptions={{ hideAttribution: true }}
          >
            <FitOnChange dep={fitKey} focusIds={focusIds} />
            <Background color={chrome.bgDots} />
            <Controls showInteractive={false} />
            <MiniMap
              pannable
              zoomable
              maskColor={chrome.miniMapMask}
              nodeColor={chrome.miniMapNode}
              nodeStrokeColor={chrome.miniMapNodeStroke}
              style={{ background: chrome.miniMapBg }}
            />
          </ReactFlow>
        </div>
      </div>
    </div>
  );
}
