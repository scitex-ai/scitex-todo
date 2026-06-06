/** Shared types for the scitex-todo board. Mirror the backend graph payload. */

/** One entry in a task's append-only comment thread. `ts` is an ISO-8601 UTC
 * timestamp and `author` the commenter, both stamped server-side. */
export interface TaskComment {
  ts: string;
  author: string;
  text: string;
}

/** Task kind — discriminator between an ordinary task row and a compute-job
 * row. Closed validated set (matches `VALID_KINDS` in `_model.py`); absence
 * over the wire (null) is equivalent to `"task"` (the default). Extensible
 * to `"ci"` etc. when task #15 wires GH-Actions rows; add the variant here
 * and in the Python validator together. */
export type TaskKind = "task" | "compute";

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

  /** Task kind. `null` (absent over the wire) is equivalent to `"task"`.
   *  `"compute"` marks a row representing an external compute job whose
   *  status is updated by an automated writer (north-star pillar #1; full
   *  design in `tasks/proj-scitex-todo-compute-state-deps/description.md`).
   */
  kind: TaskKind | null;
  /** Opaque compute-job identifier (slurm id, GH Actions run id, k8s job, …).
   *  Only meaningful when `kind === "compute"`. */
  job_id: string | null;
  /** Where the compute job runs (`spartan`, `mba`, `github`, `nas`, …).
   *  Only meaningful when `kind === "compute"`. */
  host: string | null;
  /** Shell invocation / pipeline. Long; the board truncates to ~100 chars
   *  on the node tooltip and shows the full string on click + hover in the
   *  NodeDetailPanel KV table. Only meaningful when `kind === "compute"`. */
  command: string | null;
  /** ISO-8601 timestamp when the writer observed the job started. Only
   *  meaningful when `kind === "compute"`. */
  started_at: string | null;
  /** ISO-8601 timestamp when the writer observed completion (success OR
   *  failure). Only meaningful when `kind === "compute"`. */
  finished_at: string | null;
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
