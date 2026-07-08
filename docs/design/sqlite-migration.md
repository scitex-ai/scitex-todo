# DESIGN / RFC — Migrate the scitex-todo task store from YAML to SQLite

**Status:** DESIGN REVIEW — *not for merge.* No product code changes; this is
the plan only. scitex-dev feedback requested before any implementation card.

**Scope:** replace the single ~8–9 MB `tasks.yaml` (plus the `threads.yaml`
sidecar) with a canonical SQLite database `todo.db`, built to the ecosystem
local-state standard, **behind the existing load/save/CRUD API** so no caller
changes on day one.

**Motivation (one line):** the store re-parses/re-serializes the whole
document on every single-card read-modify-write. The write path already
burned through three mitigations for exactly this — the ruamel→libyaml dump
swap (~20 s → &lt;1 s), the `load_doc` single-parse, and the `_store_verify`
event-scan that replaced a ~2.3 s full `safe_load` per write
(`src/scitex_todo/_store_verify.py:18-25`). Each is a patch on an
O(whole-store) format. SQLite makes a single-card mutation and a filtered read
O(row + index), not O(store).

---

## 0. Research findings (verified on disk, cited)

### 0.1 Ecosystem skills — FOUND

- `06_dot_scitex_directory.md` — installed under
  `…/scitex_dev/_skills/general/01_ecosystem/`.
- `12_local-state-resolution.md`, `13_runtime-state-db-layout.md` — present
  only in the uv build cache (`…/.cache/uv/archive-v0/…/scitex_dev/_skills/general/01_ecosystem/`),
  newer than the installed `scitex_dev` (ships up to `11_*`).

Extractions: (skill 12 §2) **CONFIG** is project-overridable →
`local_state.path()`; **DATA/STATE** (canonical mutable record — task stores,
DBs) → `local_state.user_path()` because "there is ONE true store; a stray
project copy must never shadow it"; **RUNTIME** (logs, PIDs, caches) →
`local_state.runtime_path()` (always `…/runtime/…`, per-host, gitignored).
(skill 12 §3) The motivating incident *was scitex-todo* — the board read a
week-stale store because `_paths.py` "rolled its own precedence putting
project scope ABOVE user scope … `tasks.yaml` is DATA — it must resolve via
`user_path()`."

### 0.2 `local_state.user_path` — EXACT signature

`scitex_config/_ecosystem/_local_state.py:153` (installed source of truth;
`from scitex_config._ecosystem import local_state`):

```python
def user_path(pkg_short: str, *parts: str) -> Path:
    """Force user-scope resolution (skip project-scope walk)."""
    base = user_root() / pkg_short            # user_root() = $SCITEX_DIR or ~/.scitex
    return base.joinpath(*parts) if parts else base
```

Siblings: `path()` (project-shadowing), `runtime_path()` (always `runtime/`),
`user_root()`, `find_project_scope()`.

### 0.3 `scitex-db` helper API — FOUND (source `~/proj/scitex-db`; not installed here)

`from scitex_db import SQLite3`. Core class `SQLite3` (`_sqlite3/_SQLite3.py:31`,
mixin composite). Constructor (`:96`):
`SQLite3(db_path, use_temp=False, compress_by_default=False, autocommit=False, mode="rwc", timeout=60.0)`
(`mode ∈ {ro,rw,rwc}`; context-manager required). Methods:
`create_table(name, columns: dict[str,str], foreign_keys=None, if_not_exists=True)`
(`_TableMixin.py:29`), `execute/executemany/executescript`
(`_QueryMixin.py:29/64/91`), `get_rows/get_row_count` (`_RowMixin.py`),
`transaction()` ctx-mgr + `begin/commit/rollback/enable_foreign_keys`
(`_TransactionMixin.py:18`), `vacuum/backup/fix_corruption`
(`_MaintenanceMixin.py`).

**PRAGMAs set at connect** (`_ConnectionMixin.py:97-114`, writable opens):
`journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=300000` (5 min),
`mmap_size=30 GB`, `temp_store=MEMORY`, `wal_autocheckpoint=1000`. Read-only
skips WAL, sets `query_only=ON`. **No `user_version`/migration framework — the
caller owns versioning.**

### 0.4 Live exemplar — scitex-clew (`clew.db`)

`scitex-session` has **no** SQLite code in this checkout (just a `@session`
decorator), so **clew is the exemplar**: `VerificationDB`
(`scitex_clew/_db/_core.py:122`) opens with `row_factory = sqlite3.Row`,
`executescript` of `CREATE TABLE/INDEX IF NOT EXISTS` (`:158`), hand-rolled
idempotent migrations (`PRAGMA table_info` → `ALTER TABLE ADD COLUMN`, `:260`),
**no `user_version`**. **Caution:** clew hand-rolls `_find_project_root()`
(`:24`) — the rolled-own-resolver anti-pattern skill 12 forbids; clew is
regenerable so it lives in `runtime/`. **We must NOT copy clew's resolver.**

### 0.5 scitex-io `.db` registration — CONFIRMED

`scitex_io/_optional_providers.py:173`:
`register_loader(".db", _load_db, builtin=True)`, `_load_db` returns
`scitex_db.SQLite3(path, **kwargs)`. Only `.db` (no `.sqlite`). Hence the name
**`todo.db`** so `stx.io.load("…/todo.db")` round-trips.

### 0.6 Current store (what we replace)

- **Resolver** `resolve_tasks_path(explicit=None)` (`_paths.py:104`): explicit
  → `$SCITEX_TODO_TASKS_YAML_SHARED` → user `~/.scitex/todo/tasks.yaml` →
  bundled example. Project scope was already deleted after the 2026-07-06
  incident (`_paths.py:18-24,138-140`) — but the resolver **still re-rolls its
  own precedence** instead of calling `user_path()`.
- **Layout:** one `tasks.yaml` with three top-level sections — `tasks:` (list),
  `users:` (list), `inboxes:` (map recipient→list) — plus a separate
  `threads.yaml` sidecar (map thread-key→list). Each file has its own
  `fcntl.flock` sentinel.
- **Load:** `load_doc(path, *, validate)` (`_model.py:501`, one `safe_load`),
  `load_tasks` (`:467`, returns `doc["tasks"]`).
- **Write:** `save_tasks` (`:1064`, takes lock) → `_save_tasks_unlocked`
  (`:1102`) → `_save_doc_unlocked` (`:1133`). Crash-safe (`:1170-1223`):
  `safe_dump`→string → `.tmp` → `flush`+`fsync` → `_verify_dumped_tmp`
  (byte-length + libyaml event-scan, `_store_verify.py:78`) → `os.replace` →
  best-effort `_git_autocommit_store`.
- **CRUD** (`_store.py`, signatures preserved): `add_task`, `update_task`,
  `complete_task`, `delete_task`, `restore_task`, `comment_task`, `set_edge`,
  `set_collaborator`, `set_subscriber`, `resolve_task`, `reopen_task`,
  `reassign_task` + reads `list_tasks`, `summarize_tasks`, `get_task`,
  `resolve_store`. Same set is the MCP tool surface.

---

## 1. Path & naming

### 1.1 Canonical path — resolved, never re-rolled

```python
from scitex_config._ecosystem import local_state
DEFAULT_DB = local_state.user_path("todo", "todo.db")   # → ~/.scitex/todo/todo.db
```

`.db` (not `.sqlite`) because scitex-io only registers `.db` (§0.5).

### 1.2 Precedence — three tiers, no project scope

1. **explicit** arg (`--db` / `db=`)
2. **`$SCITEX_TODO_DB`** env override
3. **`local_state.user_path("todo", "todo.db")`**

```python
def resolve_db_path(explicit=None) -> Path:
    if explicit is not None:
        return Path(explicit).expanduser()
    env = os.environ.get("SCITEX_TODO_DB")
    return Path(env).expanduser() if env else local_state.user_path("todo", "todo.db")
```

### 1.3 What changes vs `_paths.py`, and why the footgun dies

| Aspect | Today | New |
|---|---|---|
| Resolver | rolls own precedence (`_user_root`, `_find_git_root`) | delegates to `local_state.user_path()` |
| Project scope | removed by *comment/discipline* | **structurally impossible** — `user_path()` never walks to a project |
| Env var | `$SCITEX_TODO_TASKS_YAML_SHARED` | `$SCITEX_TODO_DB` |
| Bundled example | 4th-tier fallback | dropped — DB self-creates empty (`mode="rwc"`); demo store = separate seed script |

The 2026-07-06 week-stale bug was a rolled-own resolver ranking project above
user. `user_path()` cannot express a project scope at all, so that bug class
dies by construction, not by a comment.

### 1.4 Tension for scitex-dev — `todo/` vs `runtime/` (Q1)

Skill 13 places package DBs under `…/runtime/<pkg>.db`; skill 12 places a
*canonical DATA store* top-level via `user_path()`. They differ by **kind of
DB**: skill 13's session.db/clew.db are **regenerable caches**; the todo store
is the **canonical, non-regenerable system of record for the fleet's
direction**. Per the authoritative standard, todo is DATA → **top-level
`~/.scitex/todo/todo.db` via `user_path()`, not `runtime/`.** (Sign-off = Q1.)
The `runtime/` subtree stays for todo's genuinely regenerable state
(pidfiles, delivery ledger, reminder sidecars — `_paths.runtime_dir`).

---

## 2. Schema

`PRAGMA user_version = 1`; WAL set by `scitex_db.SQLite3` on open. Rule:
fields any read path **filters/sorts on** → typed column + index; rare / nested
/ opaque payloads → JSON `TEXT`.

### 2.1 `tasks` — one row per card

The 25 scalar `Task` fields (`_model.py:177-374`) → columns; list/nested
fields → child tables (§2.2–2.3); `deadlines[]` and `_log_meta` → JSON.

```sql
CREATE TABLE tasks (
    id            TEXT PRIMARY KEY,            -- required, unique
    title         TEXT NOT NULL,               -- required
    status        TEXT NOT NULL DEFAULT 'pending',  -- VALID_STATUSES
    kind          TEXT,                        -- VALID_KINDS; NULL ≡ 'task'
    blocker       TEXT,                        -- VALID_BLOCKERS; only when status='blocked'
    task          TEXT, note TEXT, goal TEXT,  -- free-text bodies
    project TEXT, host TEXT,
    agent         TEXT,                        -- owning agent
    assignee      TEXT,                        -- legacy, lock-step with agent
    scope         TEXT,                        -- e.g. 'agent:<name>'
    grp           TEXT,                        -- dataclass `group` ('group' is SQL-reserved)
    priority      INTEGER,
    parent        TEXT,                        -- task id (soft ref, not FK)
    pr_url TEXT, issue_url TEXT,
    deadline TEXT, scheduled TEXT,             -- ISO-8601 (+ optional repeater)
    created_at TEXT, last_activity TEXT, started_at TEXT, finished_at TEXT,
    created_by TEXT, job_id TEXT, command TEXT,
    repo          TEXT,                        -- see note
    deadlines_json TEXT,                       -- JSON list[str] (mut. excl. with `deadline`)
    log_meta_json  TEXT,                       -- JSON opaque `_log_meta`
    row_order     INTEGER                      -- preserves YAML document order
);
CREATE INDEX idx_tasks_status   ON tasks(status);
CREATE INDEX idx_tasks_agent    ON tasks(agent);
CREATE INDEX idx_tasks_assignee ON tasks(assignee);
CREATE INDEX idx_tasks_scope    ON tasks(scope);
CREATE INDEX idx_tasks_kind     ON tasks(kind);
CREATE INDEX idx_tasks_blocker  ON tasks(blocker);   -- 'blocking-me' view
CREATE INDEX idx_tasks_project  ON tasks(project);
CREATE INDEX idx_tasks_deadline ON tasks(deadline);  -- overdue / sort
CREATE INDEX idx_tasks_parent   ON tasks(parent);
CREATE INDEX idx_tasks_pr_url   ON tasks(pr_url);    -- PR reconcile
```

Notes: **`repo`** is used by `add_task`/`list_tasks` and rides `**extras` but
is **absent from the `Task` dataclass** — the migration is the moment to make
it first-class (Q4). **`group`→`grp`** (SQL reserved word), remapped in the
adapter so the Python/YAML field name is unchanged. Enum validity stays in
`_model._validate_tasks` (aliases, the blocker-only-when-blocked rule) — **no
SQL `CHECK`s**, keeping the closed-set logic in one place.

### 2.2 `task_comments`

```sql
CREATE TABLE task_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,               -- position within the card thread
    author TEXT, ts TEXT, kind TEXT, text TEXT NOT NULL
);
CREATE INDEX idx_comments_task ON task_comments(task_id, seq);
```

### 2.3 `task_edges` + `task_roles`

`blocks` is the inverse of `depends_on`; store each edge once with a direction.

```sql
CREATE TABLE task_edges (
    src_task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    dst_task_id TEXT NOT NULL,          -- soft ref (target may not exist yet)
    edge_type TEXT NOT NULL,            -- 'depends_on' | 'blocks'
    PRIMARY KEY (src_task_id, dst_task_id, edge_type)
);
CREATE INDEX idx_edges_dst ON task_edges(dst_task_id);   -- reverse/unblock

CREATE TABLE task_roles (
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    who TEXT NOT NULL,
    role TEXT NOT NULL,                 -- 'collaborator' | 'subscriber'
    PRIMARY KEY (task_id, who, role)
);
CREATE INDEX idx_roles_who ON task_roles(who);           -- "cards I subscribe to"
```

### 2.4 `users` + `user_names`

`u_*` id + globally-unique alias `names[]` → parent/child; `notify` opaque → JSON.

```sql
CREATE TABLE users (
    id TEXT PRIMARY KEY,                -- 'u_' + 12 hex
    kind TEXT NOT NULL,                 -- 'human' | 'agent'
    host_at_name TEXT,                  -- canonical 'host@name' join key
    notify_json TEXT, turn_url TEXT, a2a_port INTEGER,
    created_at TEXT, last_seen TEXT     -- last_seen = touch_user liveness
);
CREATE TABLE user_names (
    name TEXT PRIMARY KEY,              -- alias; globally unique (matches validator)
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX idx_user_names_uid ON user_names(user_id);
```

`resolve_user()` (alias → `host_at_name` → canonicalised retry) becomes two
indexed lookups + the existing Python canonicalisation fallback.

### 2.5 `notifications` (the `inboxes:` map)

```sql
CREATE TABLE notifications (
    id TEXT PRIMARY KEY,               -- 'n_' + 12 hex
    recipient_id TEXT NOT NULL,        -- u_* id or raw-name fallback
    event_type TEXT NOT NULL,          -- 'reassigned'|'completed'|'dm'|…
    card_id TEXT, body TEXT, actor TEXT, ts TEXT NOT NULL,
    seen INTEGER NOT NULL DEFAULT 0    -- per-record cursor flag
);
CREATE INDEX idx_notif_recipient_seen ON notifications(recipient_id, seen);
```

The `(recipient_id, seen)` index is the hot path — `poll_inbox`'s "unseen for
me" is today a full re-parse. Dedup (`event_type,card_id,ts,actor`) and
`supersede` become a `SELECT`+`DELETE` in one transaction.

### 2.6 `messages` (folds the `threads.yaml` sidecar into the same DB)

A thread is just its key; `list_threads` becomes `GROUP BY thread_key`.

```sql
CREATE TABLE messages (
    id TEXT PRIMARY KEY,               -- 'm_' + 12 hex
    thread_key TEXT NOT NULL,          -- 'dm:<lo>::<hi>'
    sender TEXT NOT NULL,              -- YAML `from`
    recipient TEXT NOT NULL,           -- YAML `to`
    body TEXT NOT NULL, ts TEXT NOT NULL,
    read INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_messages_thread ON messages(thread_key, ts);
```

### 2.7 `schema_meta` + `PRAGMA user_version`

```sql
CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT);
-- seed ('schema_version','1'), ('created_at', …), ('source','yaml-import'|'fresh')
PRAGMA user_version = 1;              -- machine-readable duplicate for fast gating
```

Migrations follow clew's idempotent pattern (`PRAGMA table_info` +
`ALTER TABLE ADD COLUMN`, keyed off `user_version`), never a destructive rebuild.

---

## 3. Staged migration (independently shippable, reversible)

Each stage = its own PR behind `$SCITEX_TODO_STORE=yaml|dual|db` (default
advances per stage) with an explicit no-loss rollback. ~930 cards import in
well under a second.

- **S0 — adapter + resolver, YAML still canonical.** Add `scitex_db` (via the
  `scitex-io[db]` extra) as an *optional* dep; add `resolve_db_path` (§1.2) and
  a `_db.py` adapter that opens `SQLite3(path, mode="rwc")`, applies the schema
  if empty, and exposes `load_doc_from_db`/`save_doc_to_db` mirroring the YAML
  primitives. Add a one-shot `scitex-todo import --from-yaml` (read current
  `tasks.yaml`+`threads.yaml` via existing loaders, INSERT all entities in one
  transaction, stamp `source='yaml-import'`). Nothing reads the DB yet.
  **Rollback:** delete `todo.db`; zero risk.
- **S1 — dual-write (write YAML+DB, read DB), `STORE=dual`.** Each mutating
  verb writes both stores in one critical section (YAML first — proven path —
  then DB; a DB-write failure logs loud, does not fail the call). Reads come
  from the DB. Run the A/B equivalence check (R4) after every write; any
  divergence alerts loud and auto-falls back to YAML reads. Re-run
  `import --from-yaml` (idempotent, `INSERT OR REPLACE` by id) at cutover.
  **Rollback:** flip `STORE=yaml`; YAML is current to the last write.
- **S2 — DB canonical, YAML only via export, `STORE=db`.** Reads/writes hit
  the DB only; `_save_doc_unlocked` no longer touches `tasks.yaml`. YAML
  becomes a derived view: `scitex-todo export --yaml` dumps DB→YAML on demand
  (git history, operator inspection, disaster export), never read back as
  truth. The per-save git audit trail moves to committing the *exported* YAML
  (R6). **Rollback:** `export --yaml` → flip `STORE=yaml`.
- **S3 — retire YAML-as-source.** Remove the dual-write branch and the YAML
  *read* path; `_store_verify`'s write-verify role goes away (kept only if the
  exporter reuses `safe_dump`). YAML lives on only as `export --yaml` output.
  **One-way door** — gate on ≥1 stable week at S2 with zero A/B divergences and
  a verified round-trip.

---

## 4. API-shape preservation

Callers (12 CRUD verbs, MCP tools, board handlers, `_users`/`_inbox`/
`_threads`) keep exact signatures — the DB lives behind the same four
functions:

| Current | Under SQLite |
|---|---|
| `load_doc(path, *, validate)` → mapping | assembles the same `{tasks, users, inboxes}` dict from `SELECT`s (child rows re-nested) |
| `load_tasks(path)` → `list[dict]` | `SELECT … ORDER BY row_order` + child joins → same list-of-dict |
| `_save_doc_unlocked(doc, path, *, tasks)` | diff doc vs DB → `INSERT/UPDATE/DELETE` in one `transaction()`; no whole-store rewrite |
| `_store_lock(path)` | thin wrapper / no-op — concurrency is WAL + `BEGIN IMMEDIATE`; kept so `with _store_lock(path):` sites are untouched |

Because every verb is written as `_read_write_doc` → mutate `doc["tasks"]` →
`_save_doc_unlocked` (`_store.py:301`), swapping those four primitives leaves
every verb and MCP tool **byte-for-byte unchanged**. That is the S1/S2 seam.

### Crash-safety: what replaces dump→tmp→fsync→verify→replace

The YAML dance (`_model.py:1170-1223`) bought atomicity+integrity that a text
file lacks natively. SQLite provides the same guarantees:

| YAML guarantee (source) | SQLite equivalent |
|---|---|
| Atomic write (`os.replace` of `.tmp`) | `transaction()` — commit or rollback; a crash mid-write rolls back via the WAL |
| No truncated file (`_verify_dumped_tmp`) | WAL + `synchronous=NORMAL`; a torn write is discarded on next open |
| Writers serialize (`fcntl.flock`, whole RMW) | WAL = N readers ∥ 1 writer; `BEGIN IMMEDIATE` takes the write lock up front; `busy_timeout=300000` blocks not errors |
| Post-write integrity (full-reparse→event-scan) | `PRAGMA quick_check` on open; `integrity_check` in the health command |

Net: `_store_verify`'s reason to exist (prove text bytes reparse) **goes
away** — the engine guarantees it; we keep a `quick_check` on open and expose
`integrity_check` + `scitex_db.fix_corruption` through `_health.py`.
Concurrency: one `BEGIN IMMEDIATE … COMMIT` per verb performs the read and
write inside one write transaction, so the last-writer-wins clobber the flock
defended against cannot occur (R2).

---

## 5. Open questions for scitex-dev

- **Q1 — `todo/` vs `runtime/`.** We place `todo.db` top-level (`user_path`)
  because it is canonical DATA, not a regenerable cache — contra skill 13's
  `runtime/` DB layout. Confirm top-level is right for a system-of-record DB,
  and whether skill 13 should carve out the "canonical vs cache" distinction.
- **Q2 — dependency posture.** Hard dep on `scitex-io[db]`/`scitex_db`
  acceptable, or must the store stay light like clew (stdlib `sqlite3`,
  PRAGMAs mirrored by hand)? Decides whether the adapter imports
  `scitex_db.SQLite3` or re-implements over stdlib.
- **Q3 — versioning.** We propose `user_version` *and* `schema_meta`. Is there
  an ecosystem-standard migration runner to adopt, or is clew-style hand-rolled
  `ALTER TABLE` the house style?
- **Q4 — `repo` field.** Promote to a real column/dataclass field in this
  migration, or keep opaque? (Affects whether it is indexed.)
- **Q5 — git audit trail.** Should S2 commit the exported YAML (diffable text)
  on a cadence, or is the DB's WAL history + periodic `backup()` the accepted
  substitute for `git show <sha>:` time-travel?
- **Q6 — multi-host sync.** `tasks.yaml` is text-mergeable across hosts; a
  binary `.db` is not. Is file-level sync still needed, or is the board/API the
  sync boundary now?

## 6. Risks

- **R1 — Corruption safety of the canonical fleet store.** This DB *is* the
  fleet's direction system; corruption is worse than for a cache. Mitigations:
  WAL + `synchronous=NORMAL`, `quick_check` on open, scheduled
  `integrity_check`, periodic `scitex_db.backup()` snapshots to `runtime/`,
  and — until S3 — the dual-written YAML is a full recoverable mirror.
- **R2 — WAL concurrent-write correctness.** WAL is 1-writer/N-reader; fleet
  writers (CLI, board POSTs, notifyd, reminders) contend. `busy_timeout` makes
  them *wait* not error; `BEGIN IMMEDIATE` per verb prevents RMW interleave.
  Load-test the write-burst convoy that today queues on the flock.
- **R3 — `u_*` identity resolution.** `resolve_user`'s alias / `host_at_name`
  / canonicalisation must map exactly onto `user_names` + `users.host_at_name`;
  a mismatch silently misroutes inbox notifications. Port the resolver's tests
  to run against the DB before S1.
- **R4 — DB↔YAML equivalence before cutover.** S1 runs a continuous A/B: DB
  projection must equal the YAML doc after every write (id-set, per-field,
  child-collection equality). Divergence blocks S2 — this is the gate.
- **R5 — Ordering/compactness.** YAML `to_dict` omits default-valued fields and
  preserves insertion order. The DB must reproduce document order (`row_order`)
  and omit defaults on `export --yaml` so exported YAML round-trips identically
  (feeds R4).
- **R6 — Git-tracking a binary `.db`.** No diff/text-merge. The per-save git
  audit trail moves to the exported YAML (Q5); the raw `.db` is `.gitignore`d.
  Losing free text-merge cross-host sync is a real regression to weigh (Q6).
- **R7 — Optional-dependency drift.** If `scitex_db` is missing,
  `stx.io.load("todo.db")` and the adapter fail on import. The store must
  degrade with a clear "install scitex-io[db]" message and — pre S3 — fall
  back to the still-present YAML path, not hard-crash.

---

*End of design. No source under `src/` modified; no `pyproject`/CHANGELOG
touched. Requesting scitex-dev review of §1.4/Q1 (path), §2 (schema), and §5.*
