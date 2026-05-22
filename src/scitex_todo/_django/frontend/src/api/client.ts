/** API client for the Django board backend (figrecipe parity).
 *
 * `graph` / `ping` are read-only; `setPriorityOrder` is the first write path
 * — it POSTs a list of task ids in priority order (top-priority first) to
 * `/priority`, which assigns sequential `priority = 1..N` and saves the
 * YAML store via `save_tasks` (see `handlers/priority.py`).
 */

import type { GraphPayload } from "../types/board";

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

export const api = {
  graph: () => request<GraphPayload>("graph"),
  ping: () => request<{ status: string }>("ping"),
  /** Persist a new priority order. ids are ranked 1..N in array order. */
  setPriorityOrder: (order: string[]) =>
    request<PriorityResponse>("priority", {
      method: "POST",
      body: JSON.stringify({ order }),
    }),
};
