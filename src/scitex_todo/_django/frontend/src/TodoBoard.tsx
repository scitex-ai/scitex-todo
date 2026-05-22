/** Outer board component: load the graph, render status legend + React Flow. */

import { useEffect } from "react";
import { GraphView } from "./GraphView";
import { useBoardStore } from "./store/useBoardStore";

function Legend({
  colors,
}: {
  colors: Record<string, { fill: string; stroke: string; dashed: boolean }>;
}) {
  return (
    <div className="stx-todo-legend">
      {Object.entries(colors).map(([status, c]) => (
        <span key={status} className="stx-todo-legend__item">
          <span
            className="stx-todo-legend__swatch"
            style={{
              background: c.fill,
              border: `2px ${c.dashed ? "dashed" : "solid"} ${c.stroke}`,
            }}
          />
          {status}
        </span>
      ))}
    </div>
  );
}

export function TodoBoard() {
  const { graph, loading, error, load } = useBoardStore();

  useEffect(() => {
    void load();
  }, [load]);

  if (loading && !graph) {
    return <div className="stx-todo-status">Loading task graph…</div>;
  }
  if (error) {
    return (
      <div className="stx-todo-status stx-todo-status--err">Error: {error}</div>
    );
  }
  if (!graph) {
    return <div className="stx-todo-status">No graph.</div>;
  }

  return (
    <div className="stx-todo-board">
      <header className="stx-todo-board__header">
        <span className="stx-todo-board__title">
          SciTeX Todo — dependency graph
        </span>
        <span className="stx-todo-board__meta">
          {graph.task_count} tasks · <code>{graph.store_path}</code>
        </span>
        <Legend colors={graph.status_colors} />
      </header>
      <div className="stx-todo-board__canvas">
        <GraphView graph={graph} />
      </div>
    </div>
  );
}
