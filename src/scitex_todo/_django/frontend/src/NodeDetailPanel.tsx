/** Right-side detail drawer for a single task.
 *
 * Three modes, driven by the board store:
 *   - read   — renders the task's `note` as markdown + metadata (default).
 *   - edit   — an editable form for an existing task (store.editMode).
 *   - create — the same form with a blank draft (store.creating).
 *
 * Opened when a graph or pool node is clicked (read), or via the right-click
 * menu's "Edit…" / "New task" (edit / create). Close behaviour: × button,
 * click on the dimmed backdrop, or Escape.
 *
 * The drawer is a sibling of the React Flow canvas (rendered by `GraphView`)
 * and is absolutely positioned so it overlays the board without affecting
 * layout. Pool clicks reuse the same drawer.
 */

import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import type { TaskInput } from "./api/client";
import { STATUSES, useBoardStore } from "./store/useBoardStore";
import type { GraphNode, GraphPayload, StatusColor } from "./types/board";

function StatusBadge({
  status,
  color,
}: {
  status: string;
  color: StatusColor | undefined;
}) {
  const c = color ?? { fill: "#eceff1", stroke: "#90a4ae", dashed: false };
  return (
    <span
      className="stx-todo-detail__badge"
      style={{
        background: c.fill,
        border: `2px ${c.dashed ? "dashed" : "solid"} ${c.stroke}`,
        color: "#222",
      }}
    >
      {status}
    </span>
  );
}

/** Editable form shared by edit + create modes. */
function DetailEditor({
  node,
  graph,
  creating,
  onCancel,
}: {
  node: GraphNode | null;
  graph: GraphPayload;
  creating: boolean;
  onCancel: () => void;
}) {
  const createTask = useBoardStore((s) => s.createTask);
  const updateTask = useBoardStore((s) => s.updateTask);
  const mutating = useBoardStore((s) => s.mutating);

  const [title, setTitle] = useState(node?.title ?? "");
  const [status, setStatus] = useState(node?.status ?? "pending");
  const [priority, setPriority] = useState(
    node?.priority != null ? String(node.priority) : "",
  );
  const [repo, setRepo] = useState(node?.repo ?? "");
  const [parent, setParent] = useState(node?.parent ?? "");
  const [note, setNote] = useState(
    node && node.note && node.note !== "uncategorized" ? node.note : "",
  );

  const parentOptions = graph.nodes.filter((n) => n.id !== node?.id);

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!title.trim()) return;
    const prioNum = priority.trim() === "" ? null : Number(priority);
    const input: TaskInput = {
      title: title.trim(),
      status,
      priority: Number.isFinite(prioNum as number) ? prioNum : null,
      repo: repo.trim(),
      parent: parent || null,
      note,
    };
    if (creating) void createTask(input);
    else if (node) void updateTask(node.id, input);
  };

  return (
    <form className="stx-todo-detail__form" onSubmit={onSubmit}>
      <label className="stx-todo-field">
        <span>Title</span>
        <input
          className="stx-todo-input"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          autoFocus
          required
        />
      </label>

      <div className="stx-todo-field-row">
        <label className="stx-todo-field">
          <span>Status</span>
          <select
            className="stx-todo-input"
            value={status}
            onChange={(e) => setStatus(e.target.value)}
          >
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <label className="stx-todo-field stx-todo-field--narrow">
          <span>Priority</span>
          <input
            className="stx-todo-input"
            type="number"
            value={priority}
            placeholder="—"
            onChange={(e) => setPriority(e.target.value)}
          />
        </label>
      </div>

      <label className="stx-todo-field">
        <span>Repo</span>
        <input
          className="stx-todo-input"
          value={repo}
          placeholder="optional"
          onChange={(e) => setRepo(e.target.value)}
        />
      </label>

      <label className="stx-todo-field">
        <span>Parent</span>
        <select
          className="stx-todo-input"
          value={parent}
          onChange={(e) => setParent(e.target.value)}
        >
          <option value="">— none (top level) —</option>
          {parentOptions.map((n) => (
            <option key={n.id} value={n.id}>
              {n.title}
            </option>
          ))}
        </select>
      </label>

      <label className="stx-todo-field stx-todo-field--grow">
        <span>Note (markdown)</span>
        <textarea
          className="stx-todo-input stx-todo-textarea"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          rows={8}
        />
      </label>

      <div className="stx-todo-detail__actions">
        <button
          type="button"
          className="stx-todo-btn"
          onClick={onCancel}
          disabled={mutating}
        >
          Cancel
        </button>
        <button
          type="submit"
          className="stx-todo-btn stx-todo-btn--primary"
          disabled={mutating || !title.trim()}
        >
          {mutating ? "Saving…" : creating ? "Create" : "Save"}
        </button>
      </div>
    </form>
  );
}

/** localStorage key remembering the last author typed, so a commenter
 * doesn't re-enter their name each time. */
const AUTHOR_KEY = "stx-todo-comment-author";

function fmtTs(ts: string): string {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Append-only comment thread for a task: existing comments (oldest first)
 * plus an add box. The author defaults to the remembered value; leaving it
 * blank lets the backend stamp $USER. */
function CommentsSection({ node }: { node: GraphNode }) {
  const addComment = useBoardStore((s) => s.addComment);
  const mutating = useBoardStore((s) => s.mutating);
  const [text, setText] = useState("");
  const [author, setAuthor] = useState(
    () => localStorage.getItem(AUTHOR_KEY) ?? "",
  );
  const comments = node.comments ?? [];

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const body = text.trim();
    if (!body) return;
    if (author.trim()) localStorage.setItem(AUTHOR_KEY, author.trim());
    void addComment(node.id, body, author.trim() || undefined);
    setText("");
  };

  return (
    <section className="stx-todo-comments">
      <h3 className="stx-todo-comments__title">
        Comments {comments.length > 0 && `(${comments.length})`}
      </h3>
      {comments.length === 0 ? (
        <p className="stx-todo-comments__empty">
          <em>No comments yet.</em>
        </p>
      ) : (
        <ul className="stx-todo-comments__list">
          {comments.map((c, i) => (
            <li className="stx-todo-comment" key={`${c.ts}-${i}`}>
              <div className="stx-todo-comment__meta">
                <span className="stx-todo-comment__author">{c.author}</span>
                <span className="stx-todo-comment__ts">{fmtTs(c.ts)}</span>
              </div>
              <div className="stx-todo-comment__text">{c.text}</div>
            </li>
          ))}
        </ul>
      )}
      <form className="stx-todo-comments__form" onSubmit={submit}>
        <input
          className="stx-todo-input stx-todo-comments__author"
          value={author}
          placeholder="your name (optional)"
          onChange={(e) => setAuthor(e.target.value)}
          aria-label="Comment author"
        />
        <textarea
          className="stx-todo-input stx-todo-comments__text"
          value={text}
          placeholder="Add a comment…"
          rows={2}
          onChange={(e) => setText(e.target.value)}
          aria-label="Comment text"
        />
        <div className="stx-todo-comments__actions">
          <button
            type="submit"
            className="stx-todo-btn stx-todo-btn--primary"
            disabled={mutating || !text.trim()}
          >
            {mutating ? "…" : "Comment"}
          </button>
        </div>
      </form>
    </section>
  );
}

function DetailReader({ node }: { node: GraphNode }) {
  const note = (node.note ?? "").trim();
  const hasNote = note.length > 0 && note !== "uncategorized";
  return (
    <div className="stx-todo-detail__body">
      {hasNote ? (
        <div className="stx-todo-detail__markdown">
          <ReactMarkdown>{note}</ReactMarkdown>
        </div>
      ) : (
        <p className="stx-todo-detail__empty">
          <em>No note yet for this task.</em>
        </p>
      )}
      <CommentsSection node={node} />
    </div>
  );
}

export function NodeDetailPanel({
  node,
  color,
  editMode,
  creating,
  onClose,
  onEdit,
  graph,
}: {
  node: GraphNode | null;
  color: StatusColor | undefined;
  editMode: boolean;
  creating: boolean;
  onClose: () => void;
  onEdit: () => void;
  graph: GraphPayload;
}) {
  const endEdit = useBoardStore((s) => s.endEdit);

  // Close on Escape — but only in read mode; in edit/create Escape should
  // back out of the form (handled by the editor's own surface), not nuke
  // unsaved input. Here we close the whole drawer only when reading.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !editMode) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, editMode]);

  const title = creating ? "New task" : (node?.title ?? "");

  return (
    <div
      className="stx-todo-detail__backdrop"
      role="dialog"
      aria-modal="true"
      aria-label={creating ? "Create task" : `Task detail: ${title}`}
      onClick={onClose}
    >
      <aside
        className="stx-todo-detail"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="stx-todo-detail__header">
          <div className="stx-todo-detail__title-row">
            <h2 className="stx-todo-detail__title">{title || "Untitled"}</h2>
            <div className="stx-todo-detail__title-actions">
              {!editMode && node && (
                <button
                  type="button"
                  className="stx-todo-detail__edit"
                  onClick={onEdit}
                  aria-label="Edit task"
                  title="Edit"
                >
                  ✎
                </button>
              )}
              <button
                type="button"
                className="stx-todo-detail__close"
                onClick={onClose}
                aria-label="Close"
              >
                ×
              </button>
            </div>
          </div>
          {!editMode && node && (
            <div className="stx-todo-detail__meta">
              <StatusBadge status={node.status} color={color} />
              {node.priority != null && (
                <span className="stx-todo-detail__prio">
                  priority {node.priority}
                </span>
              )}
              {node.repo && (
                <span className="stx-todo-detail__repo">
                  <code>{node.repo}</code>
                </span>
              )}
              <span className="stx-todo-detail__id">
                id: <code>{node.id}</code>
              </span>
            </div>
          )}
        </header>
        {editMode ? (
          <DetailEditor
            node={node}
            graph={graph}
            creating={creating}
            onCancel={endEdit}
          />
        ) : node ? (
          <DetailReader node={node} />
        ) : null}
      </aside>
    </div>
  );
}

/** Hook wrapper: resolve drawer state from the board store. Renders nothing
 * unless a node is selected (read/edit) or we're composing a new task. */
export function NodeDetailPanelContainer() {
  const graph = useBoardStore((s) => s.graph);
  const selectedNodeId = useBoardStore((s) => s.selectedNodeId);
  const editMode = useBoardStore((s) => s.editMode);
  const creating = useBoardStore((s) => s.creating);
  const clearSelection = useBoardStore((s) => s.clearSelection);
  const endEdit = useBoardStore((s) => s.endEdit);
  const beginEdit = useBoardStore((s) => s.beginEdit);

  if (!graph) return null;
  const open = creating || selectedNodeId != null;
  if (!open) return null;

  const node = selectedNodeId
    ? (graph.nodes.find((n) => n.id === selectedNodeId) ?? null)
    : null;
  // A selected id that no longer resolves (deleted out from under us) and not
  // creating -> nothing to show.
  if (!creating && !node) return null;

  const onClose = () => {
    if (editMode) endEdit();
    clearSelection();
  };

  return (
    <NodeDetailPanel
      node={node}
      color={node ? graph.status_colors[node.status] : undefined}
      editMode={editMode}
      creating={creating}
      graph={graph}
      onClose={onClose}
      onEdit={() => node && beginEdit(node.id)}
    />
  );
}
