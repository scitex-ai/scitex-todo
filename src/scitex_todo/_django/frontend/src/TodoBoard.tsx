/** Outer board component: load the graph, render header (progress + legend +
 * filter toolbar) and the React Flow canvas. */

import { useEffect, useMemo } from "react";
import { GraphView } from "./GraphView";
import { TableView } from "./TableView";
import { NodeDetailPanelContainer } from "./NodeDetailPanel";
import { ContextMenu } from "./ContextMenu";
import { AutoRefresh } from "./AutoRefresh";
import { DrillHistory } from "./DrillHistory";
import { EDGE_COLOR_BLOCKS, EDGE_COLOR_DEPENDS } from "./layout";
import { useBoardStore } from "./store/useBoardStore";
import type { GraphPayload, StatusColor } from "./types/board";

/** Segmented toggle between the graph and the flat table view. */
function ViewToggle() {
  const view = useBoardStore((s) => s.view);
  const setView = useBoardStore((s) => s.setView);
  return (
    <div className="stx-todo-viewtoggle" role="group" aria-label="View mode">
      <button
        type="button"
        className={`stx-todo-viewtoggle__btn${
          view === "graph" ? " stx-todo-viewtoggle__btn--on" : ""
        }`}
        onClick={() => setView("graph")}
        aria-pressed={view === "graph"}
      >
        Graph
      </button>
      <button
        type="button"
        className={`stx-todo-viewtoggle__btn${
          view === "table" ? " stx-todo-viewtoggle__btn--on" : ""
        }`}
        onClick={() => setView("table")}
        aria-pressed={view === "table"}
      >
        Table
      </button>
    </div>
  );
}

function Legend({ colors }: { colors: Record<string, StatusColor> }) {
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
      {/* Edge semantics — depends_on (arrow) vs blocks (inhibition tee). */}
      <span className="stx-todo-legend__item" title="A depends on B">
        <span
          className="stx-todo-legend__edge"
          style={{ background: EDGE_COLOR_DEPENDS }}
        />
        depends&nbsp;on&nbsp;→
      </span>
      <span className="stx-todo-legend__item" title="A blocks B">
        <span
          className="stx-todo-legend__edge"
          style={{ background: EDGE_COLOR_BLOCKS }}
        />
        blocks&nbsp;⊣
      </span>
    </div>
  );
}

/** Per-status progress summary computed from the full task set. */
function Progress({ graph }: { graph: GraphPayload }) {
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const n of graph.nodes) c[n.status] = (c[n.status] ?? 0) + 1;
    return c;
  }, [graph.nodes]);
  const total = graph.nodes.length;
  const done = counts["done"] ?? 0;
  const pct = total ? Math.round((done / total) * 100) : 0;
  // Order the chips so the "alive" states read left→right.
  const order = [
    "goal",
    "in_progress",
    "blocked",
    "pending",
    "deferred",
    "failed",
    "done",
  ];
  return (
    <span className="stx-todo-progress" aria-label="Progress summary">
      <strong>
        {done}/{total} done ({pct}%)
      </strong>
      {order
        .filter((s) => counts[s])
        .map((s) => (
          <span key={s} className="stx-todo-progress__chip">
            {s} {counts[s]}
          </span>
        ))}
    </span>
  );
}

/** Search box + status-filter chips. Filters the graph (dim non-matches) and
 * the pool (hide non-matches) via the shared store filter. */
function Toolbar({ graph }: { graph: GraphPayload }) {
  const query = useBoardStore((s) => s.query);
  const activeStatuses = useBoardStore((s) => s.activeStatuses);
  const setQuery = useBoardStore((s) => s.setQuery);
  const toggleStatus = useBoardStore((s) => s.toggleStatus);
  const resetFilters = useBoardStore((s) => s.resetFilters);
  const statuses = Object.keys(graph.status_colors);
  const filtering = query.trim().length > 0 || activeStatuses.length > 0;

  return (
    <div className="stx-todo-toolbar">
      <input
        className="stx-todo-toolbar__search"
        type="search"
        placeholder="Search tasks (title / id / repo)…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        aria-label="Search tasks"
      />
      <div className="stx-todo-toolbar__chips">
        {statuses.map((s) => {
          const on = activeStatuses.includes(s);
          const c = graph.status_colors[s];
          return (
            <button
              key={s}
              type="button"
              className={`stx-todo-chip${on ? " stx-todo-chip--on" : ""}`}
              style={
                on
                  ? { background: c.fill, borderColor: c.stroke, color: "#222" }
                  : { borderColor: c.stroke }
              }
              onClick={() => toggleStatus(s)}
              aria-pressed={on}
            >
              {s}
            </button>
          );
        })}
      </div>
      {filtering && (
        <button
          type="button"
          className="stx-todo-toolbar__reset"
          onClick={resetFilters}
        >
          clear
        </button>
      )}
    </div>
  );
}

export function TodoBoard() {
  const { graph, loading, error, load } = useBoardStore();
  const view = useBoardStore((s) => s.view);

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
          <code>{graph.store_path}</code>
        </span>
        <Progress graph={graph} />
        <ViewToggle />
        <Legend colors={graph.status_colors} />
      </header>
      <Toolbar graph={graph} />
      <div className="stx-todo-board__canvas">
        {view === "graph" ? (
          <GraphView graph={graph} />
        ) : (
          <TableView graph={graph} />
        )}
        {/* Drawer, context menu, and live-refresh poller are shared by both
            views, so they live here rather than inside GraphView. */}
        <NodeDetailPanelContainer />
        <ContextMenu />
        <AutoRefresh />
        <DrillHistory />
      </div>
    </div>
  );
}
