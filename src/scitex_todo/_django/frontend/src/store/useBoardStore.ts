/** Zustand store for the board: the loaded graph payload + load lifecycle. */

import { create } from "zustand";
import { api } from "../api/client";
import type { GraphPayload } from "../types/board";

interface BoardStore {
  graph: GraphPayload | null;
  loading: boolean;
  error: string | null;
  load: () => Promise<void>;
}

export const useBoardStore = create<BoardStore>((set) => ({
  graph: null,
  loading: false,
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
}));
