# scitex-todo → scitex-cards

**This package was renamed to [scitex-cards](https://pypi.org/project/scitex-cards/)
(2026-07-16).** `scitex-todo` is now a metadata-only stub that installs
scitex-cards.

Nothing breaks on upgrade:

- `import scitex_todo` keeps working (scitex-cards ships a one-window
  compatibility shim that aliases it to the same modules).
- The `scitex-todo` CLI keeps working (both console scripts resolve to the
  same entry point).
- `SCITEX_TODO_*` environment variables keep working (mirrored from/next to
  the new `SCITEX_CARDS_*` names, with a deprecation warning).

Switch at your convenience:

```bash
pip install scitex-cards
```

```python
import scitex_cards  # the canonical name
```

Repository: https://github.com/scitex-ai/scitex-cards
