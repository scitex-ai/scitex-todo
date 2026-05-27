/** Right-click context menu for the board.
 *
 * A single menu instance driven by the store's `menu` state. Opened by:
 *   - right-click on a graph node  (GraphView onNodeContextMenu)
 *   - right-click on a pool card   (UncategorizedPool item onContextMenu)
 *   - right-click on empty canvas  (GraphView onPaneContextMenu)
 *
 * For a card it offers Edit… / Set status / Delete; for the canvas it offers
 * New task. Closes on action, click-away, or Escape.
 */

import { useEffect, useRef } from "react";
import { STATUSES, useBoardStore } from "./store/useBoardStore";

export function ContextMenu() {
  const menu = useBoardStore((s) => s.menu);
  const closeMenu = useBoardStore((s) => s.closeMenu);
  const beginEdit = useBoardStore((s) => s.beginEdit);
  const beginCreate = useBoardStore((s) => s.beginCreate);
  const updateTask = useBoardStore((s) => s.updateTask);
  const deleteTask = useBoardStore((s) => s.deleteTask);
  const graph = useBoardStore((s) => s.graph);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!menu) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeMenu();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menu, closeMenu]);

  if (!menu) return null;

  const task = menu.taskId
    ? graph?.nodes.find((n) => n.id === menu.taskId)
    : null;

  // Clamp so the menu never overflows the right/bottom edge.
  const MENU_W = 200;
  const MENU_H = 320;
  const left = Math.min(menu.x, window.innerWidth - MENU_W - 8);
  const top = Math.min(menu.y, window.innerHeight - MENU_H - 8);

  return (
    <div
      className="stx-todo-menu__backdrop"
      onClick={closeMenu}
      onContextMenu={(e) => {
        e.preventDefault();
        closeMenu();
      }}
    >
      <div
        ref={ref}
        className="stx-todo-menu"
        style={{ left, top }}
        role="menu"
        onClick={(e) => e.stopPropagation()}
      >
        {menu.taskId === null ? (
          <button
            type="button"
            className="stx-todo-menu__item"
            role="menuitem"
            onClick={beginCreate}
          >
            ✚ New task
          </button>
        ) : (
          <>
            <div className="stx-todo-menu__label" title={menu.taskId}>
              {task ? task.title : menu.taskId}
            </div>
            <button
              type="button"
              className="stx-todo-menu__item"
              role="menuitem"
              onClick={() => beginEdit(menu.taskId as string)}
            >
              ✎ Edit…
            </button>
            <div className="stx-todo-menu__sep" />
            <div className="stx-todo-menu__group-label">Set status</div>
            <div className="stx-todo-menu__statuses">
              {STATUSES.map((s) => {
                const c = graph?.status_colors[s];
                const current = task?.status === s;
                return (
                  <button
                    type="button"
                    key={s}
                    className={`stx-todo-menu__status${
                      current ? " stx-todo-menu__status--current" : ""
                    }`}
                    role="menuitem"
                    onClick={() =>
                      void updateTask(menu.taskId as string, { status: s })
                    }
                  >
                    <span
                      className="stx-todo-menu__swatch"
                      style={{
                        background: c?.fill ?? "#888",
                        borderColor: c?.stroke ?? "#888",
                      }}
                      aria-hidden="true"
                    />
                    {s}
                  </button>
                );
              })}
            </div>
            <div className="stx-todo-menu__sep" />
            <button
              type="button"
              className="stx-todo-menu__item stx-todo-menu__item--danger"
              role="menuitem"
              onClick={() => {
                const label = task ? task.title : (menu.taskId as string);
                if (window.confirm(`Delete "${label}"? This cannot be undone.`)) {
                  void deleteTask(menu.taskId as string);
                } else {
                  closeMenu();
                }
              }}
            >
              🗑 Delete
            </button>
          </>
        )}
      </div>
    </div>
  );
}
