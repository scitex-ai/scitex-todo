/** Flat, sortable table view of the task store — a fast triage surface for a
 * large store (the graph is great for structure, less so for scanning 200
 * rows). Honors the toolbar search + status filter. Rows open the same detail
 * drawer on click and the same right-click context menu as the graph; a row
 * with children drills into its subgraph instead.
 *
 * Unlike the graph (which is scoped by the drill path), the table shows ALL
 * matching tasks flat — that's the point of the view.
 */

import { useMemo, useState } from "react";
import { nodeHasChildren } from "./layout";
import { taskMatchesFilter, useBoardStore } from "./store/useBoardStore";
import type { GraphNode, GraphPayload } from "./types/board";

type SortKey = "title" | "status" | "priority" | "repo" | "deps" | "comments";
type SortDir = "asc" | "desc";

interface Row {
  node: GraphNode;
  deps: number;
  comments: number;
  hasChildren: boolean;
}

const COLUMNS: { key: SortKey; label: string; className?: string }[] = [
  { key: "title", label: "Title" },
  { key: "status", label: "Status" },
  { key: "priority", label: "Prio" },
  { key: "repo", label: "Repo" },
  { key: "deps", label: "Deps" },
  { key: "comments", label: "💬" },
];

export function TableView({ graph }: { graph: GraphPayload }) {
  const query = useBoardStore((s) => s.query);
  const activeStatuses = useBoardStore((s) => s.activeStatuses);
  const selectNode = useBoardStore((s) => s.selectNode);
  const drillInto = useBoardStore((s) => s.drillInto);
  const openMenu = useBoardStore((s) => s.openMenu);
  const [sortKey, setSortKey] = useState<SortKey>("priority");
  const [sortDir, setSortDir] = useState<SortDir>("asc");

  // Count edges touching each node once, so the Deps column is O(E) not O(N·E).
  const degree = useMemo(() => {
    const d = new Map<string, number>();
    for (const e of graph.edges) {
      d.set(e.source, (d.get(e.source) ?? 0) + 1);
      d.set(e.target, (d.get(e.target) ?? 0) + 1);
    }
    return d;
  }, [graph.edges]);

  const rows = useMemo<Row[]>(() => {
    const filtered = graph.nodes.filter((n) =>
      taskMatchesFilter(n, query, activeStatuses),
    );
    const mapped: Row[] = filtered.map((n) => ({
      node: n,
      deps: degree.get(n.id) ?? 0,
      comments: n.comments?.length ?? 0,
      hasChildren: nodeHasChildren(graph, n.id),
    }));
    const dir = sortDir === "asc" ? 1 : -1;
    const val = (r: Row): string | number => {
      switch (sortKey) {
        case "title":
          return r.node.title.toLowerCase();
        case "status":
          return r.node.status;
        case "priority":
          // Nulls sort last regardless of direction.
          return r.node.priority ?? Number.POSITIVE_INFINITY;
        case "repo":
          return (r.node.repo ?? "").toLowerCase();
        case "deps":
          return r.deps;
        case "comments":
          return r.comments;
      }
    };
    return mapped.sort((a, b) => {
      const va = val(a);
      const vb = val(b);
      if (va < vb) return -1 * dir;
      if (va > vb) return 1 * dir;
      return a.node.title.localeCompare(b.node.title);
    });
  }, [graph, degree, query, activeStatuses, sortKey, sortDir]);

  const onSort = (key: SortKey) => {
    if (key === sortKey) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
  };

  return (
    <div className="stx-todo-table-wrap">
      <table className="stx-todo-table">
        <thead>
          <tr>
            {COLUMNS.map((col) => (
              <th
                key={col.key}
                className={`stx-todo-table__th${
                  sortKey === col.key ? " stx-todo-table__th--sorted" : ""
                }`}
                onClick={() => onSort(col.key)}
                title={`Sort by ${col.label}`}
              >
                {col.label}
                {sortKey === col.key && (
                  <span className="stx-todo-table__caret" aria-hidden="true">
                    {sortDir === "asc" ? " ▲" : " ▼"}
                  </span>
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(({ node, deps, comments, hasChildren }) => {
            const c = graph.status_colors[node.status];
            return (
              <tr
                key={node.id}
                className="stx-todo-table__row"
                onClick={() =>
                  hasChildren ? drillInto(node.id) : selectNode(node.id)
                }
                onContextMenu={(e) => {
                  e.preventDefault();
                  openMenu(e.clientX, e.clientY, node.id);
                }}
                title={hasChildren ? "Drill in (right-click to edit)" : "Details (right-click to edit)"}
              >
                <td className="stx-todo-table__title">
                  {hasChildren ? "▸ " : ""}
                  {node.title}
                </td>
                <td>
                  <span
                    className="stx-todo-table__status"
                    style={{
                      background: c?.fill ?? "#888",
                      borderColor: c?.stroke ?? "#888",
                    }}
                  >
                    {node.status}
                  </span>
                </td>
                <td className="stx-todo-table__num">
                  {node.priority ?? ""}
                </td>
                <td className="stx-todo-table__repo">{node.repo ?? ""}</td>
                <td className="stx-todo-table__num">{deps || ""}</td>
                <td className="stx-todo-table__num">{comments || ""}</td>
              </tr>
            );
          })}
          {rows.length === 0 && (
            <tr>
              <td className="stx-todo-table__empty" colSpan={COLUMNS.length}>
                No matching tasks.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
