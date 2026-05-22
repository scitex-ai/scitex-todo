/** Right-side detail drawer for a single task.
 *
 * Opens when a graph or pool node is clicked. Renders the task's `note`
 * field as markdown via `react-markdown`, plus title / status / priority /
 * repo metadata.
 *
 * Close behaviour:
 *   - Explicit close button (×).
 *   - Click on the dimmed backdrop (anywhere outside the panel).
 *   - `Escape` key.
 *
 * The drawer is a sibling of the React Flow canvas (rendered by
 * `GraphView`) and is absolutely positioned so it overlays the board
 * without affecting layout. Pool clicks reuse the same drawer.
 */

import { useEffect } from "react";
import ReactMarkdown from "react-markdown";
import { useBoardStore } from "./store/useBoardStore";
import type { GraphNode, StatusColor } from "./types/board";

interface NodeDetailPanelProps {
  node: GraphNode;
  color: StatusColor | undefined;
  onClose: () => void;
}

function StatusBadge({
  status,
  color,
}: {
  status: string;
  color: StatusColor | undefined;
}) {
  const c = color ?? { fill: "#eceff1", stroke: "#90a4ae", dashed: false };
  return (
    <span
      className="stx-todo-detail__badge"
      style={{
        background: c.fill,
        border: `2px ${c.dashed ? "dashed" : "solid"} ${c.stroke}`,
        color: "#222",
      }}
    >
      {status}
    </span>
  );
}

export function NodeDetailPanel({
  node,
  color,
  onClose,
}: NodeDetailPanelProps) {
  // Close on Escape — easy keyboard exit without reaching for the mouse.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const note = (node.note ?? "").trim();
  const hasNote = note.length > 0 && note !== "uncategorized";

  return (
    <div
      className="stx-todo-detail__backdrop"
      role="dialog"
      aria-modal="true"
      aria-label={`Task detail: ${node.title}`}
      onClick={onClose}
    >
      <aside
        className="stx-todo-detail"
        // Swallow clicks inside the panel so they don't bubble up to the
        // backdrop's onClick (which would close the drawer mid-interaction).
        onClick={(e) => e.stopPropagation()}
      >
        <header className="stx-todo-detail__header">
          <div className="stx-todo-detail__title-row">
            <h2 className="stx-todo-detail__title">{node.title}</h2>
            <button
              type="button"
              className="stx-todo-detail__close"
              onClick={onClose}
              aria-label="Close task detail"
            >
              ×
            </button>
          </div>
          <div className="stx-todo-detail__meta">
            <StatusBadge status={node.status} color={color} />
            {node.priority != null && (
              <span className="stx-todo-detail__prio">
                priority {node.priority}
              </span>
            )}
            {node.repo && (
              <span className="stx-todo-detail__repo">
                <code>{node.repo}</code>
              </span>
            )}
            <span className="stx-todo-detail__id">
              id: <code>{node.id}</code>
            </span>
          </div>
        </header>
        <div className="stx-todo-detail__body">
          {hasNote ? (
            <div className="stx-todo-detail__markdown">
              <ReactMarkdown>{note}</ReactMarkdown>
            </div>
          ) : (
            <p className="stx-todo-detail__empty">
              <em>No note yet for this task.</em>
            </p>
          )}
        </div>
      </aside>
    </div>
  );
}

/** Hook wrapper: pull `selectedNodeId` + setter from the board store and
 * resolve it against the current graph payload. Returns `null` when no node
 * is selected (so the caller can render nothing). */
export function NodeDetailPanelContainer() {
  const graph = useBoardStore((s) => s.graph);
  const selectedNodeId = useBoardStore((s) => s.selectedNodeId);
  const clearSelection = useBoardStore((s) => s.clearSelection);

  if (!graph || !selectedNodeId) return null;
  const node = graph.nodes.find((n) => n.id === selectedNodeId);
  if (!node) return null;

  return (
    <NodeDetailPanel
      node={node}
      color={graph.status_colors[node.status]}
      onClose={clearSelection}
    />
  );
}
