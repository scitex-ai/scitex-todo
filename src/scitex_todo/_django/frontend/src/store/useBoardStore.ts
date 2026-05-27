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
import { api, type TaskInput } from "../api/client";
import type { GraphPayload } from "../types/board";

/** Selectable task statuses, mirroring the backend VALID_STATUSES. Drives the
 * status dropdown in the editor and the "Set status ▸" context submenu. */
export const STATUSES = [
  "goal",
  "pending",
  "in_progress",
  "blocked",
  "done",
  "deferred",
  "failed",
] as const;

/** Right-click context-menu anchor. `taskId === null` = the canvas/pane menu
 * (offers "New task"); a non-null id = a card menu (edit / status / delete). */
export interface MenuState {
  x: number;
  y: number;
  taskId: string | null;
}

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
  /** Free-text filter (matches task title / id / repo, case-insensitive).
   * Empty string = no text filter. */
  query: string;
  /** Statuses to KEEP visible. Empty set = show all statuses. */
  activeStatuses: string[];
  load: () => Promise<void>;
  reorderPriority: (order: string[]) => Promise<void>;
  selectNode: (id: string) => void;
  clearSelection: () => void;
  /** Descend into `parentId`'s child subgraph. Idempotent if already there. */
  drillInto: (parentId: string) => void;
  /** Pop back to the breadcrumb crumb at `depth` (0 = Home / top-level). */
  drillTo: (depth: number) => void;
  /** Set the free-text filter. */
  setQuery: (q: string) => void;
  /** Toggle a status in/out of the keep-visible set. */
  toggleStatus: (status: string) => void;
  /** Clear both the text query and the status filter. */
  resetFilters: () => void;

  // ── CRUD ───────────────────────────────────────────────────────────────
  /** True while a create/update/delete POST is in flight (reuses `saving`'s
   * dim cue is enough; this is just for disabling the editor's Save button). */
  mutating: boolean;
  /** When true the detail drawer renders its editable form rather than the
   * read view. */
  editMode: boolean;
  /** When true the drawer is composing a brand-new task (no selected id). */
  creating: boolean;
  /** Open the drawer in edit mode for an existing task. */
  beginEdit: (id: string) => void;
  /** Open the drawer in create mode (blank draft). */
  beginCreate: () => void;
  /** Leave edit/create mode (back to read view, or close if was creating). */
  endEdit: () => void;
  /** Create a task, reload the graph, and open it in the drawer. */
  createTask: (input: TaskInput) => Promise<void>;
  /** Patch a task's fields and reload the graph. */
  updateTask: (id: string, input: TaskInput) => Promise<void>;
  /** Delete a task and reload the graph (closing the drawer if it was open). */
  deleteTask: (id: string) => Promise<void>;

  // ── Right-click context menu ─────────────────────────────────────────────
  /** Active context menu, or null when closed. */
  menu: MenuState | null;
  /** Open the context menu at viewport (x, y) for a card (id) or canvas (null). */
  openMenu: (x: number, y: number, taskId: string | null) => void;
  /** Close the context menu. */
  closeMenu: () => void;
}

export const useBoardStore = create<BoardStore>((set, get) => ({
  graph: null,
  loading: false,
  saving: false,
  error: null,
  selectedNodeId: null,
  drillPath: [],
  query: "",
  activeStatuses: [],
  mutating: false,
  editMode: false,
  creating: false,
  menu: null,
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
  setQuery: (q: string) => set({ query: q }),
  toggleStatus: (status: string) =>
    set((s) => ({
      activeStatuses: s.activeStatuses.includes(status)
        ? s.activeStatuses.filter((x) => x !== status)
        : [...s.activeStatuses, status],
    })),
  resetFilters: () => set({ query: "", activeStatuses: [] }),

  // ── CRUD ───────────────────────────────────────────────────────────────
  beginEdit: (id: string) =>
    set({ selectedNodeId: id, editMode: true, creating: false, menu: null }),
  beginCreate: () =>
    set({ selectedNodeId: null, editMode: true, creating: true, menu: null }),
  endEdit: () =>
    set((s) =>
      s.creating
        ? { editMode: false, creating: false, selectedNodeId: null }
        : { editMode: false },
    ),
  createTask: async (input: TaskInput) => {
    set({ mutating: true, error: null });
    try {
      const { task } = await api.createTask(input);
      const graph = await api.graph();
      // Open the freshly-created task in the read drawer so the user sees it.
      set({
        graph,
        mutating: false,
        editMode: false,
        creating: false,
        selectedNodeId: task.id,
      });
    } catch (e) {
      set({ error: (e as Error).message, mutating: false });
    }
  },
  updateTask: async (id: string, input: TaskInput) => {
    set({ mutating: true, error: null });
    try {
      await api.updateTask(id, input);
      const graph = await api.graph();
      set({ graph, mutating: false, editMode: false });
    } catch (e) {
      set({ error: (e as Error).message, mutating: false });
    }
  },
  deleteTask: async (id: string) => {
    set({ mutating: true, error: null });
    try {
      await api.deleteTask(id);
      const graph = await api.graph();
      set((s) => ({
        graph,
        mutating: false,
        menu: null,
        // Close the drawer if it was showing the now-deleted task.
        selectedNodeId: s.selectedNodeId === id ? null : s.selectedNodeId,
        editMode: s.selectedNodeId === id ? false : s.editMode,
      }));
    } catch (e) {
      set({ error: (e as Error).message, mutating: false });
    }
  },

  // ── Right-click context menu ─────────────────────────────────────────────
  openMenu: (x: number, y: number, taskId: string | null) =>
    set({ menu: { x, y, taskId } }),
  closeMenu: () => set({ menu: null }),
}));

/** True iff a task matches the current text query + status filter.
 *
 * Empty query matches everything; empty status set matches every status.
 * Text match is case-insensitive across title / id / repo. Shared by the
 * graph (dim non-matches) and the pool (hide non-matches) so the toolbar
 * filters both views consistently.
 */
export function taskMatchesFilter(
  task: { id: string; title: string; status: string; repo?: string | null },
  query: string,
  activeStatuses: string[],
): boolean {
  if (activeStatuses.length > 0 && !activeStatuses.includes(task.status)) {
    return false;
  }
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const hay = `${task.title} ${task.id} ${task.repo ?? ""}`.toLowerCase();
  return hay.includes(q);
}
