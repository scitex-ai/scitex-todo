# scitex-todo roadmap

The canonical artifact is the **YAML task store** (top-level `tasks:` list).
Everything else is an adapter that renders, imports, or serves that store.
This keeps the data portable and the surfaces swappable.

```
                         tasks.yaml  (canonical store, single source of truth)
                              |
        +---------------------+---------------------+
        |                     |                     |
   mermaid adapter       org adapter           Web UI adapter
   (YAML -> PNG)         (org <-> YAML)        (drag -> reprioritize -> YAML)
      [done]              [future]               [future]
```

## Done

- **Canonical store + validator** — `load_tasks` validates id/title/status;
  statuses include `goal`.
- **Mermaid adapter** — `build_mermaid` (depends_on arrows, `blocks`
  inhibition edges, per-status colors) + `render` (mmdc-first, kroki
  fallback).
- **Local-state path resolution** — explicit -> `$SCITEX_TODO_TASKS` ->
  project `.scitex/todo/` -> user `~/.scitex/todo/` -> bundled example.
- **CLI** — `scitex-todo render`, `scitex-todo list`.

## Future

Deliberately deferred to keep the MVP small. None of these are built yet.

- **org adapter** — read/write org-mode TODO trees (`:BLOCKER:` / `ORDERED` /
  org-edna) as an alternate canonical face, so deps are text-derivable from
  Emacs and round-trip back to YAML.
- **Web UI** — a browser view where dragging a task reads coordinates and
  reprioritizes, writing the new ordering/deps back to the YAML store.
- **MCP server** — expose `load`/`render`/`list` as agent-callable tools
  (`todo_*`), so agents can query and update the store. Follows the SciTeX
  five-interface convention.
- **HTTP API** — optional FastAPI surface for web clients / dashboards.
- **Read the Docs + full skills** — Sphinx site and the standard `_skills/`
  tree (`01_installation` … `20_env-vars`).

## Intended role in the ecosystem

`scitex-todo` is meant to be a **shared task backend** usable across SciTeX —
e.g. a fleet orchestrator (orochi) reading/writing the same YAML store that a
researcher edits locally, with the mermaid adapter giving everyone the same
dependency picture without prose.
