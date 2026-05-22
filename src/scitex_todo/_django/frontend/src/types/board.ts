/** Shared types for the scitex-todo board. Mirror the backend graph payload. */

export interface GraphNode {
  id: string;
  title: string;
  status: string;
  priority: number | null;
  note: string | null;
  repo: string | null;
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
