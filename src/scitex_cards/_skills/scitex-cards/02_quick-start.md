---
description: |
  [TOPIC] Quick Start
  [DETAILS] 30-second tour — load a tasks.yaml, build mermaid source, render
  to PNG (Python), plus the equivalent CLI one-liners.
tags: [scitex-todo-quick-start]
---

# Quick Start

## Python

```python
# Audit §6 narrows the top-level surface to the six task-store APIs that
# match MCP tool names 1:1 (add_task/update_task/complete_task/list_tasks/
# summarize_tasks/resolve_store). The mermaid + render + model helpers
# remain available via submodule imports.
from scitex_cards._model   import load_tasks
from scitex_cards._diagram import build_mermaid
from scitex_cards._diagram  import render

tasks = load_tasks("tasks.yaml")           # validates id / title / status
mermaid_src = build_mermaid(tasks)         # YAML -> flowchart TB
engine = render(mermaid_src, "tasks.png")
print(f"rendered via {engine}")            # 'mmdc' or 'kroki'
```

A minimal `tasks.yaml`:

```yaml
tasks:
  - {id: design, title: Design, status: done}
  - {id: build, title: Build, status: in_progress, depends_on: [design]}
  - {id: ship, title: Ship, status: goal, depends_on: [build]}
```

## CLI

```bash
# default store: project -> user -> bundled example (or $SCITEX_TODO_TASKS_YAML_SHARED)
scitex-todo render-graph -o tasks.png

# inspect the generated mermaid without rendering
scitex-todo render-graph --print-mermaid

# list the resolved tasks (machine-readable with --json)
scitex-todo list-tasks --json

# read-only dependency-graph web board (needs the [web] extra)
scitex-todo board --port 8051
```
