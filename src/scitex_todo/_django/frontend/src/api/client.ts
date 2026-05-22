/** API client for the Django board backend (figrecipe parity, read-only). */

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

async function request<T>(endpoint: string): Promise<T> {
  const res = await fetch(buildUrl(endpoint), {
    headers: { "Content-Type": "application/json" },
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ error: res.statusText }));
    throw new Error(err.error || `API error: ${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  graph: () => request<GraphPayload>("graph"),
  ping: () => request<{ status: string }>("ping"),
};
