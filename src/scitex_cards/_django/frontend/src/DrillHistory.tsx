/** Wire the drill-down stack into browser history so the mouse back/forward
 * buttons (and the browser's own back/forward, and keyboard) move between
 * drilled "pages" instead of navigating away from the board.
 *
 * Each `drillInto` / breadcrumb jump pushes a history entry carrying the full
 * scope stack; a `popstate` (mouse back/forward) restores that stack. The
 * filter/view persistence (persist.ts) uses replaceState and preserves this
 * state object, so the two coexist.
 *
 * Renders nothing.
 */

import { useEffect, useRef } from "react";
import { useBoardStore } from "./store/useBoardStore";

interface DrillState {
  stxDrill?: string[];
}

export function DrillHistory() {
  const drillPath = useBoardStore((s) => s.drillPath);
  const setDrillPath = useBoardStore((s) => s.setDrillPath);
  const key = drillPath.join(""); // unit separator — safe vs ids
  // True while applying a path that CAME FROM popstate, so the push-effect
  // below doesn't echo it back into a new history entry.
  const fromPop = useRef(false);
  const mounted = useRef(false);

  // Restore the scope stack on mouse/browser back-forward.
  useEffect(() => {
    const onPop = (e: PopStateEvent) => {
      const path = (e.state as DrillState | null)?.stxDrill ?? [];
      fromPop.current = true;
      setDrillPath(path);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [setDrillPath]);

  // Record each user-driven drill change as a history entry.
  useEffect(() => {
    if (fromPop.current) {
      fromPop.current = false; // this change came from back/forward — don't re-push
      return;
    }
    const state: DrillState = { stxDrill: drillPath };
    if (!mounted.current) {
      // Seed the initial entry (which may be a restored non-empty path) in
      // place rather than pushing a spurious extra entry on first paint.
      mounted.current = true;
      window.history.replaceState(
        { ...(window.history.state as object | null), ...state },
        "",
      );
      return;
    }
    window.history.pushState(
      { ...(window.history.state as object | null), ...state },
      "",
    );
    // key is the serialized drillPath — the value we actually react to.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return null;
}
