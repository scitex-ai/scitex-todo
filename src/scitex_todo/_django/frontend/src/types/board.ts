/** Shared types for the scitex-todo board. Mirror the backend graph payload. */

/** One entry in a task's append-only comment thread. `ts` is an ISO-8601 UTC
 * timestamp and `author` the commenter, both stamped server-side. */
export interface TaskComment {
  ts: string;
  author: string;
  text: string;
}

export interface GraphNode {
  id: string;
  title: string;
  status: string;
  priority: number | null;
  note: string | null;
  repo: string | null;
  /** Id of the node this task nests under. `null` (or absent) = top-level.
   *  Drives the nested-graph drill-down: clicking a node WITH children (any
   *  task whose `parent` equals this node's id) re-renders the canvas to that
   *  child subgraph; a breadcrumb navigates back. */
  parent: string | null;
  /** Append-only comment thread (oldest first). Always present (may be []). */
  comments: TaskComment[];
}

export type EdgeKind = "depends_on" | "blocks";

export interface GraphEdge {
  source: string;
  target: string;
  kind: EdgeKind;
}

export interface StatusColor {
  fill: string;
  stroke: string;
  dashed: boolean;
}

export interface GraphPayload {
  nodes: GraphNode[];
  edges: GraphEdge[];
  status_colors: Record<string, StatusColor>;
  mermaid: string;
  store_path: string;
  task_count: number;
}
