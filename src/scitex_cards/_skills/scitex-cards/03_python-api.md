---
description: |
  [TOPIC] Python API
  [DETAILS] Public API of scitex-todo — loader/saver, mermaid builder,
  renderer (mmdc/kroki), path resolution, and the STATUS_STYLE / VALID_STATUSES
  tables. The full surface is `scitex_cards.__all__`.
tags: [scitex-todo-python-api]
---

# Python API

```python
import scitex_cards as todo
```

Audit §6 narrows the top-level surface (`scitex_cards.__all__`) to the six
task-store APIs that match a Convention A MCP tool name 1:1: `add_task` /
`update_task` / `complete_task` / `list_tasks` / `summarize_tasks` /
`resolve_store`. The rendering / model / paths helpers below remain
importable from their submodules (`scitex_cards._diagram`,
`scitex_cards._diagram`, `scitex_cards._model`, `scitex_cards._paths`). Run
`scitex-todo list-python-apis -v` for live signatures of the public surface.

## Loading and saving

### `load_tasks(path) -> list[dict]`

Validating loader. Raises `TaskValidationError` on a missing `id`/`title`, a
duplicate `id`, or an invalid `status`.

```python
tasks = todo.load_tasks("tasks.yaml")
# [{"id": "design", "title": "Design", "status": "done"}, ...]
```

### `save_tasks(tasks, path) -> None`

Round-trips the YAML store with `ruamel.yaml`, preserving hand-written
comments. Validates before writing (an invalid task leaves the file
untouched).

## Rendering

### `build_mermaid(tasks) -> str`

Renders the task list to a mermaid `flowchart TB`: `depends_on` → arrows,
`blocks` → `-- blocks --x` inhibition edges, per-status fill colors.

### `render(mermaid_src, output) -> str`

Renders mermaid source to a PNG and returns the engine used (`"mmdc"` or
`"kroki"`). `render_with_mmdc` / `render_with_kroki` force a single backend;
`find_chromium()` locates a puppeteer/playwright chromium for mmdc. Failures
raise `RenderError`.

## Path resolution

### `resolve_tasks_path(explicit=None) -> Path`

Returns the first existing store in precedence order (explicit →
`$SCITEX_TODO_TASKS_YAML_SHARED` → user). There is **no bundled fallback**: as of
2026-07-19 no YAML store ships in the wheel, because a packaged demo file at the
end of the chain could be — and once was — resolved as the fleet's live board. An
unresolvable store raises `FileNotFoundError` rather than silently standing in.
`bundled_example()` remains only as a raising stub so an external caller gets a
stated reason instead of an `AttributeError`.

## Constants and exceptions

| Name               | Meaning                                                       |
|--------------------|---------------------------------------------------------------|
| `VALID_STATUSES`   | `goal`/`pending`/`in_progress`/`blocked`/`done`/`deferred`/`failed`/`cancelled` (cancelled = closed-as-not-planned, terminal) |
| `STATUS_STYLE`     | per-status fill color + edge style used by `build_mermaid`     |
| `TaskValidationError` | raised by `load_tasks`/`save_tasks` on a bad task           |
| `RenderError`      | raised by `render*` when no backend can produce the PNG       |

## Task schema

| Field        | Required | Meaning                                              |
|--------------|----------|------------------------------------------------------|
| `id`         | yes      | unique id, referenced by `depends_on` / `blocks`     |
| `title`      | yes      | short label                                          |
| `status`     | yes      | one of `VALID_STATUSES`                              |
| `repo`       | no       | owning repo / area                                   |
| `depends_on` | no       | ids this task depends on → arrow `dep --> task`      |
| `blocks`     | no       | ids this task inhibits → `blocker -- blocks --x task`|
| `note`       | no       | free-text annotation                                 |
| `priority`   | no       | integer rank (lower = higher); document order if absent |
| `parent`     | no       | id of the task this nests under (drill-down view)    |
