# scitex-todo

A canonical YAML task store with pluggable adapters — render your task
dependency graph as a diagram instead of a wall of prose.

The YAML store is the single source of truth. Adapters render or import it;
the mermaid adapter (YAML -> dependency PNG) ships today, with org and Web-UI
adapters on the roadmap.

## Installation

```bash
uv pip install scitex-todo
# plain pip also works
pip install scitex-todo
```

Rendering to PNG additionally needs either `mmdc` (mermaid-cli, with a
puppeteer/playwright chromium) on `PATH`, or outbound access to `kroki.io`
(the automatic fallback).

## Quick Start

```python
import scitex_todo as todo

tasks = todo.load_tasks("tasks.yaml")   # validates: id/title/status
mermaid_src = todo.build_mermaid(tasks) # YAML -> flowchart TB
engine = todo.render(mermaid_src, "tasks.png")
print(f"rendered via {engine}")
```

From the shell:

```bash
# default store: project -> user -> bundled example (or $SCITEX_TODO_TASKS)
scitex-todo render -o tasks.png

# explicit store
scitex-todo render --tasks ./.scitex/todo/tasks.yaml -o /tmp/graph.png

# inspect the generated mermaid without rendering
scitex-todo render --print-mermaid

# list the resolved tasks
scitex-todo list
```

## Task store schema

A YAML document with a top-level `tasks:` list. Each task:

| Field        | Required | Meaning                                                          |
|--------------|----------|------------------------------------------------------------------|
| `id`         | yes      | unique id, referenced by `depends_on` / `blocks`                 |
| `title`      | yes      | short label                                                      |
| `status`     | yes      | `goal` \| `pending` \| `in_progress` \| `blocked` \| `done` \| `deferred` \| `failed` |
| `repo`       | no       | owning repo / area                                               |
| `depends_on` | no       | ids this task depends on -> arrow `dep --> task`                 |
| `blocks`     | no       | ids this task inhibits -> `blocker -- blocks --x target`         |
| `note`       | no       | free-text annotation, shown under the title                      |

### status -> color

| status        | fill            | edge style    |
|---------------|-----------------|---------------|
| `goal`        | gold `#ffe082`  | solid         |
| `done`        | green `#c8e6c9` | solid         |
| `in_progress` | yellow `#fff9c4`| solid         |
| `blocked`     | orange `#fff3e0`| solid         |
| `pending`     | grey `#eceff1`  | solid         |
| `deferred`    | grey `#f5f5f5`  | dashed border |
| `failed`      | red `#ffcdd2`   | solid         |

## Where your task data lives

`scitex-todo` ships only the mechanism — no task content. Your store is
resolved in this order (first existing wins):

1. an explicit `--tasks` path
2. `$SCITEX_TODO_TASKS`
3. project scope: `<git-root>/.scitex/todo/tasks.yaml`
4. user scope: `~/.scitex/todo/tasks.yaml` (relocatable via `$SCITEX_DIR`)
5. the bundled generic example (`scitex_todo/examples/tasks.yaml`)

This follows the [SciTeX local-state convention](https://github.com/ywatanabe1989/scitex-dev).

## Roadmap

The YAML store is the canonical backend; adapters layer on top.

- **mermaid adapter** — YAML -> dependency PNG. *(done)*
- **org adapter** — read/write org-mode TODO trees (`:BLOCKER:` / `ORDERED` /
  org-edna) so deps are derivable from Emacs. *(future)*
- **Web UI** — a browser view where dragging a task reprioritizes and writes
  back to the YAML store. *(future)*
- **MCP / HTTP / RTD / full skills** — agentic + service surfaces, intended to
  make the store a shared task backend across SciTeX (e.g. orochi). *(future)*

## Part of SciTeX

scitex-todo is part of [**SciTeX**](https://scitex.ai).

>Four Freedoms for Research
>
>0. The freedom to **run** your research anywhere — your machine, your terms.
>1. The freedom to **study** how every step works — from raw data to final manuscript.
>2. The freedom to **redistribute** your workflows, not just your papers.
>3. The freedom to **modify** any module and share improvements with the community.
>
>AGPL-3.0 — because we believe research infrastructure deserves the same freedoms as the software it runs on.
