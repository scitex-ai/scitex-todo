/** Zustand store for the board: loaded graph payload + load / reorder /
 * select / drill actions.
 *
 * `reorderPriority` POSTs an ordered list of task ids to the backend
 * `/priority` endpoint (which assigns priority = 1..N in array order and
 * writes the YAML via `save_tasks`), then reloads the graph so the UI
 * reflects the new ordering from the canonical source of truth.
 *
 * `selectedNodeId` drives the right-side detail drawer (NodeDetailPanel) —
 * clicking a graph or pool LEAF card sets it; click-away / Escape / × clears
 * it. Held in the global store (not local component state) so a graph reload
 * after a successful drag-reorder preserves the open drawer.
 *
 * `drillPath` drives the nested-graph drill-down. Each entry is the id of a
 * parent node the user has descended INTO. Empty array = top-level view
 * (nodes whose `parent` is null/absent). Clicking a node with children
 * pushes its id; the breadcrumb sets the path to a prefix to navigate back.
 * Held in the store (not local component state) so the canvas keeps its
 * place across graph reloads (e.g. after a drag-reorder save).
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
  /** Task id currently displayed in the detail drawer, or null if closed. */
  selectedNodeId: string | null;
  /** Stack of parent ids the user has drilled INTO (deepest at the end).
   * Empty = top-level scope. The currently-visible scope is the last entry
   * (`null` when empty). */
  drillPath: string[];
  load: () => Promise<void>;
  reorderPriority: (order: string[]) => Promise<void>;
  selectNode: (id: string) => void;
  clearSelection: () => void;
  /** Descend into `parentId`'s child subgraph. Idempotent if already there. */
  drillInto: (parentId: string) => void;
  /** Pop back to the breadcrumb crumb at `depth` (0 = Home / top-level). */
  drillTo: (depth: number) => void;
}

export const useBoardStore = create<BoardStore>((set, get) => ({
  graph: null,
  loading: false,
  saving: false,
  error: null,
  selectedNodeId: null,
  drillPath: [],
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
  selectNode: (id: string) => set({ selectedNodeId: id }),
  clearSelection: () => set({ selectedNodeId: null }),
  drillInto: (parentId: string) =>
    set((s) => {
      // Idempotent: clicking the same already-current scope is a no-op so
      // double-clicks don't push a redundant crumb.
      if (s.drillPath[s.drillPath.length - 1] === parentId) return s;
      // Also clear any open detail drawer when changing scope so a stale
      // selection from the previous level doesn't linger.
      return {
        drillPath: [...s.drillPath, parentId],
        selectedNodeId: null,
      };
    }),
  drillTo: (depth: number) =>
    set((s) => {
      const clamped = Math.max(0, Math.min(depth, s.drillPath.length));
      if (clamped === s.drillPath.length) return s;
      return {
        drillPath: s.drillPath.slice(0, clamped),
        selectedNodeId: null,
      };
    }),
}));
