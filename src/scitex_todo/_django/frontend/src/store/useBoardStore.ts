/** Zustand store for the board: loaded graph payload + load / reorder actions.
 *
 * `reorderPriority` POSTs an ordered list of task ids to the backend
 * `/priority` endpoint (which assigns priority = 1..N in array order and
 * writes the YAML via `save_tasks`), then reloads the graph so the UI
 * reflects the new ordering from the canonical source of truth.
 */

import { create } from "zustand";
import { api } from "../api/client";
import type { GraphPayload } from "../types/board";

interface BoardStore {
  graph: GraphPayload | null;
  loading: boolean;
  /** True while a drag-reorder POST is in flight. Separate from `loading` so
   * the initial load spinner doesn't double-fire during interaction. */
  saving: boolean;
  error: string | null;
  load: () => Promise<void>;
  reorderPriority: (order: string[]) => Promise<void>;
}

export const useBoardStore = create<BoardStore>((set, get) => ({
  graph: null,
  loading: false,
  saving: false,
  error: null,
  load: async () => {
    set({ loading: true, error: null });
    try {
      const graph = await api.graph();
      set({ graph, loading: false });
    } catch (e) {
      set({ error: (e as Error).message, loading: false });
    }
  },
  reorderPriority: async (order: string[]) => {
    set({ saving: true, error: null });
    try {
      await api.setPriorityOrder(order);
      // Re-fetch from the canonical store so any backend-side normalization
      // (skipped unknown ids, comment-preserved YAML, etc.) is reflected.
      const graph = await api.graph();
      set({ graph, saving: false });
    } catch (e) {
      set({ error: (e as Error).message, saving: false });
      // Reload so the UI snaps back to the on-disk state on failure rather
      // than displaying the optimistic (but not persisted) drag positions.
      void get().load();
    }
  },
}));
