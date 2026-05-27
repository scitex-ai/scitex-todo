/** API client for the Django board backend (figrecipe parity).
 *
 * `graph` / `ping` are read-only; `setPriorityOrder` is the first write path
 * — it POSTs a list of task ids in priority order (top-priority first) to
 * `/priority`, which assigns sequential `priority = 1..N` and saves the
 * YAML store via `save_tasks` (see `handlers/priority.py`).
 */

import type { GraphPayload, TaskComment } from "../types/board";

let _base = "";

/** Set the API base URL at runtime (used when embedded). */
export function setApiBase(base: string): void {
  _base = base.replace(/\/+$/, "");
}

/** Read the optional ?store= override from the page URL. */
function storeParam(): string {
  const params = new URLSearchParams(window.location.search);
  return params.get("store") || "";
}

function buildUrl(endpoint: string): string {
  const store = storeParam();
  let url = `${_base}/${endpoint}`;
  if (store) {
    const sep = url.includes("?") ? "&" : "?";
    url += `${sep}store=${encodeURIComponent(store)}`;
  }
  return url;
}

async function request<T>(endpoint: string, init?: RequestInit): Promise<T> {
  const res = await fetch(buildUrl(endpoint), {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || `API error: ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export interface PriorityResponse {
  updated: string[];
  store_path: string;
}

/** Editable fields for create / update. `id` is server-owned (generated on
 * create from the title, immutable on update) so it is not part of the input. */
export interface TaskInput {
  title?: string;
  status?: string;
  priority?: number | null;
  note?: string;
  repo?: string;
  parent?: string | null;
  depends_on?: string[];
  blocks?: string[];
}

export interface TaskMutateResponse {
  task: Record<string, unknown> & { id: string };
  store_path: string;
}

export const api = {
  graph: () => request<GraphPayload>("graph"),
  ping: () => request<{ status: string }>("ping"),
  /** Cheap revision fingerprint (store mtime + task count) for change polling. */
  rev: () =>
    request<{ mtime: number; count: number; store_path: string }>("rev"),
  /** Persist a new priority order. ids are ranked 1..N in array order. */
  setPriorityOrder: (order: string[]) =>
    request<PriorityResponse>("priority", {
      method: "POST",
      body: JSON.stringify({ order }),
    }),
  /** Create a task. `title` is required; the backend generates the id. */
  createTask: (input: TaskInput) =>
    request<TaskMutateResponse>("create", {
      method: "POST",
      body: JSON.stringify(input),
    }),
  /** Patch an existing task's editable fields (only provided keys change). */
  updateTask: (id: string, input: TaskInput) =>
    request<TaskMutateResponse>("update", {
      method: "POST",
      body: JSON.stringify({ id, ...input }),
    }),
  /** Delete a task; the backend also scrubs edges/parent refs to it. */
  deleteTask: (id: string) =>
    request<{ deleted: string; store_path: string }>("delete", {
      method: "POST",
      body: JSON.stringify({ id }),
    }),
  /** Append a comment to a task's thread. The backend stamps ts + author
   * (author defaults to $USER when omitted). */
  addComment: (id: string, text: string, author?: string) =>
    request<{ comment: TaskComment; count: number; store_path: string }>(
      "comment",
      {
        method: "POST",
        body: JSON.stringify({ id, text, author }),
      },
    ),
};
