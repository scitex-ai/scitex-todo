---
description: |
  [TOPIC] Installation
  [DETAILS] pip install scitex-todo; the [web] extra adds the Django board;
  PNG rendering needs mmdc or outbound kroki.io. Verify with
  `python -c "import scitex_todo"`.
tags: [scitex-todo-installation]
---

# Installation

```bash
# Recommended — uv resolver
uv pip install scitex-todo[all]

# Plain pip also works
pip install scitex-todo
```

System requirements: Python ≥ 3.10.

## Extras

| Extra   | Adds                                                        |
|---------|-------------------------------------------------------------|
| `web`   | Django + scitex-app/scitex-ui for the `scitex-todo board`   |
| `docs`  | Sphinx + RTD theme for building the documentation           |
| `dev`   | pytest, pytest-cov, scitex-dev (test + audit toolchain)     |
| `all`   | everything above                                            |

## Rendering backends (for `render-graph`)

PNG output needs **one** of:

- `mmdc` (mermaid-cli) on `PATH`, with a puppeteer/playwright chromium, or
- outbound access to `kroki.io` (the automatic fallback).

Printing the mermaid source (`scitex-todo render-graph --print-mermaid`)
needs neither.

## Verify

```bash
python -c "import scitex_todo; print(scitex_todo.__version__)"
scitex-todo --help
```
