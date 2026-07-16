# ADR-0010 — `~/.scitex/cards/cards.db` becomes the single source of truth

**Status:** ACCEPTED (operator-ruled, 2026-07-16)
**Supersedes / amends:** `docs/design/sqlite-migration.md` (the RFC on branch
`design/sqlite-migration`) — adopted as the base design; this ADR records the
deltas the operator's rulings introduced and the sequencing they force.

## Context

The RFC designed the yaml→sqlite migration when the package was still
scitex-todo, target path `~/.scitex/todo/todo.db`, with open questions Q5 (git
audit trail) and Q6 (multi-host). Since then (all 2026-07-16):

1. The package was renamed **scitex-cards** (S1 shipped in v0.14.0: import
   shim, env dual-read, both CLIs; repo renamed to `scitex-ai/scitex-cards`).
2. The operator ruled the **DATABASE is the single source of truth** — not
   yaml-with-a-db-mirror: "データベースを唯一の真実にしないと、スピードの面でも
   どこかで簡単に行き詰まる".
3. The operator declared the SSOT path: **`~/.scitex/cards/cards.db`**.
   An optional per-card file view (`~/.scitex/cards/cards/<card-id>/…`) is
   allowed but strictly DERIVED — the db always wins.
4. The operator answered RFC-Q5: the git audit/backup rail is a **periodic
   YAML snapshot EXPORTED FROM the db**, itself git-snapshotted. Git tracks an
   export, never live data — which also retires the dotfiles bind-mount hazard
   (the dotfiles working tree IS today's live store; a `git merge` there writes
   live data).
5. The operator approved migration path **(b)**: backup → stand up the db
   infrastructure → verified bulk import; a short dual window only at cutover.

## Decision

1. **Path.** `resolve_db_path`: explicit arg → `$SCITEX_CARDS_DB` →
   `$SCITEX_TODO_DB` (deprecated, loud warning, one transition window) →
   `local_state.user_path("cards", "cards.db")`. The final tier stays
   DELEGATED to the ecosystem resolver — a project scope remains structurally
   inexpressible (the 2026-07-06 stale-store class stays dead).
2. **The pre-rename shadow db is dead weight.** `~/.scitex/todo/todo.db`
   (stale since 2026-07-13, dual-write off fleet-wide) is never moved, read,
   or trusted; `cards.db` is REBUILT from the live yaml by the idempotent
   importer at cutover, then verified (RFC-R4 A/B equivalence, counts printed
   and spot-checked).
3. **Backup rail is a deliverable, not an option.** `export --yaml` (db →
   canonical yaml text) plus a git-snapshot cadence ships with S4. dotfiles
   keeps tracking the live store — today's ONLY off-site backup — until this
   rail exists and is verified, then their tracking flips to the export.
4. **Canonicality flips only at fleet cutover (S6).** The fleet's SIF bakes
   pre-rename code and every agent writes the yaml through its own MCP server
   today. Flipping the hub to db-canonical while ANY old writer remains forks
   the store. Order: publish scitex-cards (S3) → SIF floor bump → sac env flip
   → dotfiles store-path pin flip (their card
   `dotfiles-env-store-path-flip-to-scitex-cards-20260716`, pinged by us after
   verification) → db becomes canonical → `~/.scitex/todo` becomes a symlink
   to `~/.scitex/cards` (operator-ruled end shape). Exactly one canonical
   store at any moment.
5. **Multi-host (RFC-Q6).** Spartan's island store and its project-shadow fork
   stay on yaml untouched until the hub cutover is verified; merge-vs-federate
   is decided afterwards (card `scitex-cards-spartan-store-fork-reconcile-20260716`).

## Consequences

- The store's speed ceiling moves from O(whole-yaml-per-write) to O(row);
  the Cards Django board reads through the indexed db.
- yaml demotes to (a) the rollback state until cutover is verified, then
  (b) the export/backup format only. `_store_verify`'s reason to exist goes
  away with it (engine-guaranteed integrity; `quick_check` on open stays).
- Every identity/path flip in this migration carries the fleet checklist
  learned from the S1 skill-symlink regression: sweep the FLEET for old-path
  references before landing, regenerate links in the same change, and a boot
  canary (`sac agents start` must succeed) gates "done".
