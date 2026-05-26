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
import scitex_todo as todo

tasks = todo.load_tasks("tasks.yaml")    # validates id / title / status
mermaid_src = todo.build_mermaid(tasks)  # YAML -> flowchart TB
engine = todo.render(mermaid_src, "tasks.png")
print(f"rendered via {engine}")          # 'mmdc' or 'kroki'
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
# default store: project -> user -> bundled example (or $SCITEX_TODO_TASKS)
scitex-todo render-graph -o tasks.png

# inspect the generated mermaid without rendering
scitex-todo render-graph --print-mermaid

# list the resolved tasks (machine-readable with --json)
scitex-todo list-tasks --json

# read-only dependency-graph web board (needs the [web] extra)
scitex-todo board --port 8051
```
