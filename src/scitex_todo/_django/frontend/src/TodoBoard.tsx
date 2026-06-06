/** Outer board component: load the graph, render header (progress + legend +
 * filter toolbar) and the React Flow canvas. */

import { useEffect, useMemo } from "react";
import { GraphView } from "./GraphView";
import { TableView } from "./TableView";
import { NodeDetailPanelContainer } from "./NodeDetailPanel";
import { ContextMenu } from "./ContextMenu";
import { EdgeKindMenu } from "./EdgeKindMenu";
import { AutoRefresh } from "./AutoRefresh";
import { DrillHistory } from "./DrillHistory";
import { Toast } from "./Toast";
import { KeyboardShortcuts } from "./KeyboardShortcuts";
import { EDGE_COLOR_BLOCKS, EDGE_COLOR_DEPENDS } from "./layout";
import {
  taskMatchesFilter,
  useBoardStore,
} from "./store/useBoardStore";
import {
  downloadText,
  toCsv,
  toJson,
  toMarkdown,
} from "./exportBoard";
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

/** Per-status progress summary. Narrows to the current drill scope when one
 * is active (direct children of the scope), else counts the whole store.
 *
 * The "blocked N" chip is clickable (operator UX 2026-06-06): clicking it
 * toggles a filter to status=blocked so the operator can jump from "what is
 * the SHAPE of progress" to "show me the things that need unblocking" in one
 * click. Other status chips are passive (read-only display); blocking is the
 * one most worth a shortcut because acting on it unblocks the rest. */
function Progress({ graph }: { graph: GraphPayload }) {
  const drillPath = useBoardStore((s) => s.drillPath);
  const activeStatuses = useBoardStore((s) => s.activeStatuses);
  const toggleStatus = useBoardStore((s) => s.toggleStatus);
  const scope = drillPath.length ? drillPath[drillPath.length - 1] : null;
  const scoped = useMemo(
    () =>
      scope === null
        ? graph.nodes
        : graph.nodes.filter((n) => n.parent === scope),
    [graph.nodes, scope],
  );
  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const n of scoped) c[n.status] = (c[n.status] ?? 0) + 1;
    return c;
  }, [scoped]);
  const total = scoped.length;
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
  const blockedOn = activeStatuses.includes("blocked");
  return (
    <span className="stx-todo-progress" aria-label="Progress summary">
      <strong>
        {scope ? "scope " : ""}
        {done}/{total} done ({pct}%)
      </strong>
      {order
        .filter((s) => counts[s])
        .map((s) =>
          s === "blocked" ? (
            <button
              key={s}
              type="button"
              className={`stx-todo-progress__chip stx-todo-progress__chip--blocked${
                blockedOn ? " stx-todo-progress__chip--on" : ""
              }`}
              onClick={() => toggleStatus("blocked")}
              aria-pressed={blockedOn}
              title={
                blockedOn
                  ? "Clear the blocked-only filter"
                  : "Filter the board to blocked tasks only"
              }
            >
              🚧 blocked {counts[s]}
            </button>
          ) : (
            <span key={s} className="stx-todo-progress__chip">
              {s} {counts[s]}
            </span>
          ),
        )}
    </span>
  );
}

/** Search box + status-filter chips. Filters the graph (dim non-matches) and
 * the pool (hide non-matches) via the shared store filter. */
function Toolbar({ graph }: { graph: GraphPayload }) {
  const query = useBoardStore((s) => s.query);
  const activeStatuses = useBoardStore((s) => s.activeStatuses);
  const activeRepos = useBoardStore((s) => s.activeRepos);
  const setQuery = useBoardStore((s) => s.setQuery);
  const toggleStatus = useBoardStore((s) => s.toggleStatus);
  const setRepos = useBoardStore((s) => s.setRepos);
  const resetFilters = useBoardStore((s) => s.resetFilters);
  const statuses = Object.keys(graph.status_colors);
  // Distinct, sorted repos present on the board (for the facet chips).
  const repos = useMemo(() => {
    const set = new Set<string>();
    for (const n of graph.nodes) if (n.repo) set.add(n.repo);
    return [...set].sort();
  }, [graph.nodes]);
  const filtering =
    query.trim().length > 0 ||
    activeStatuses.length > 0 ||
    activeRepos.length > 0;

  return (
    <div className="stx-todo-toolbar">
      <input
        className="stx-todo-toolbar__search"
        type="search"
        placeholder="Search (title / id / repo / note / comments)…"
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
      {repos.length > 0 && (
        <select
          className={`stx-todo-toolbar__repo${
            activeRepos.length ? " stx-todo-toolbar__repo--on" : ""
          }`}
          value={activeRepos[0] ?? ""}
          onChange={(e) => setRepos(e.target.value ? [e.target.value] : [])}
          aria-label="Filter by repo"
          title="Filter by repo"
        >
          <option value="">All repos ({repos.length})</option>
          {repos.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
      )}
      {filtering && (
        <button
          type="button"
          className="stx-todo-toolbar__reset"
          onClick={resetFilters}
        >
          clear
        </button>
      )}
      <ExportGroup graph={graph} />
    </div>
  );
}

/** Export the currently-visible (filtered) tasks to a downloaded file. */
function ExportGroup({ graph }: { graph: GraphPayload }) {
  const query = useBoardStore((s) => s.query);
  const activeStatuses = useBoardStore((s) => s.activeStatuses);
  const activeRepos = useBoardStore((s) => s.activeRepos);
  const visible = useMemo(
    () =>
      graph.nodes.filter((n) =>
        taskMatchesFilter(n, query, activeStatuses, activeRepos),
      ),
    [graph.nodes, query, activeStatuses, activeRepos],
  );
  const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, "-");
  const base = `scitex-todo-${stamp}-${visible.length}`;
  return (
    <span
      className="stx-todo-toolbar__export"
      role="group"
      aria-label={`Export ${visible.length} visible tasks`}
      title={`Export ${visible.length} visible tasks`}
    >
      <span className="stx-todo-toolbar__export-label">Export</span>
      <button
        type="button"
        className="stx-todo-chip stx-todo-chip--export"
        onClick={() =>
          downloadText(toMarkdown(graph, visible), `${base}.md`, "text/markdown")
        }
      >
        MD
      </button>
      <button
        type="button"
        className="stx-todo-chip stx-todo-chip--export"
        onClick={() => downloadText(toCsv(graph, visible), `${base}.csv`, "text/csv")}
      >
        CSV
      </button>
      <button
        type="button"
        className="stx-todo-chip stx-todo-chip--export"
        onClick={() =>
          downloadText(toJson(graph, visible), `${base}.json`, "application/json")
        }
      >
        JSON
      </button>
    </span>
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
        <EdgeKindMenu />
        <AutoRefresh />
        <DrillHistory />
        <Toast />
        <KeyboardShortcuts />
      </div>
    </div>
  );
}
