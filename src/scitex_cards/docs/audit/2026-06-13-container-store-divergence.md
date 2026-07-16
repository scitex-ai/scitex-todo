# Audit: container/host `tasks.yaml` divergence

**Date**: 2026-06-13
**Charter**: Operator directive 2026-06-13 ("audit the bind mechanism +
sync direction before P3a rollout") + board card
`board-container-store-divergence`.
**Author**: investigation agent worktree `agent-abd4fa8d35ae097e2` (this
container = `proj-scitex-todo`).
**Scope**: DOC-ONLY. No code, schema, or `tasks.yaml` changes were made.
**Status**: AUDIT — proposes options for a follow-up PR.

## TL;DR

The proj-scitex-todo container's bind source is **NOT**
`~/.scitex/todo` on the host — it is
`/home/ywatanabe/.dotfiles/src/.scitex/todo/`, an entirely *separate*
host directory that the P3a-2 design spec
(`scitex-agent-container/.dev/p3a-mcp-wire/p3a-2-todo-bind.yaml`) does
NOT describe. That dotfiles store is a self-contained git repo (`main`
branch, no remote, last commit `bc7f0b8` 2026-06-13 09:43) which a small
set of host-side scripts evidently keep separate from the operator's
`~/.scitex/todo/`.

So the divergence is **structural, not transient**: different agents'
containers may bind from *different* host snapshots depending on which
dotfiles tree the runtime resolves, and there is no
single-canonical-store guarantee yet. P3a's "single shared store"
assumption is unmet on the way the binds are actually wired today.

## Findings — concrete probes

### 1. Effective bind / symlink topology in *this* container

```text
$ readlink -f /home/agent/.scitex/todo
/scitex-todo
$ readlink -f /scitex-todo
/scitex-todo
```

So `/home/agent/.scitex/todo` is a symlink → `/scitex-todo`. Same inode:

```text
$ stat -c "%i %n" /scitex-todo/tasks.yaml /home/agent/.scitex/todo/tasks.yaml
14212385 /scitex-todo/tasks.yaml
14212385 /home/agent/.scitex/todo/tasks.yaml
$ md5sum /scitex-todo/tasks.yaml /home/agent/.scitex/todo/tasks.yaml
d92dc26066610811024f0e79e1ce3a2b  /scitex-todo/tasks.yaml
d92dc26066610811024f0e79e1ce3a2b  /home/agent/.scitex/todo/tasks.yaml
```

Internally consistent. So intra-container, the store is one file.

### 2. Mount source on the host (the load-bearing finding)

From `/proc/self/mountinfo`:

```text
8:48 /home/ywatanabe/.dotfiles/src/.scitex/todo  /scitex-todo  rw,nosuid,nodev,relatime ...
8:48 /home/ywatanabe/proj/scitex-todo            /work         rw,nosuid,nodev,relatime ...
8:48 /home/ywatanabe/.dotfiles/src/.scitex/agent-container/runtime/proj-scitex-todo  /state/proj-scitex-todo  rw,nosuid,nodev,relatime ...
```

The container's `/scitex-todo` is bound from
`~/.dotfiles/src/.scitex/todo/`, **NOT** `~/.scitex/todo/`. The P3a-2
design spec (`p3a-2-todo-bind.yaml`) explicitly calls for
`"~/.scitex/todo:/home/agent/.scitex/todo:rw"`. Either:

  - the spec was landed against a *different* host path than the design
    document specifies, **or**
  - `~/.scitex/todo` on the host is itself a symlink into
    `~/.dotfiles/src/.scitex/todo` (cannot verify from inside the
    container — the host `/home/ywatanabe/.scitex/` is not visible from
    here).

Either way: from a container's point of view the bind source is the
dotfiles tree, and there is no symlink at the host level enforcing that
the operator's morning-sweep `~/.scitex/todo/tasks.yaml` is the SAME
inode as the dotfiles tree.

### 3. resolve-store inside this container

```text
$ /opt/venv-agent/bin/scitex-todo resolve-store
resolved:        /home/agent/.scitex/todo/tasks.yaml
exists:          True
explicit:        None
$SCITEX_TODO_TASKS: None
user store:      /home/agent/.scitex/todo/tasks.yaml
bundled example: /work/.worktrees/agent-nudge/src/scitex_cards/examples/tasks.yaml
```

Two side-issues:

  - The bundled-example path leaks a *stale worktree path*
    (`agent-nudge`) — package was installed from that worktree and the
    resolver hard-codes `Path(__file__).resolve().parent`. Cosmetic but
    confusing; not the divergence cause.
  - `resolve-store` only verifies file *presence*, not which host inode
    the bind is from. So an agent today cannot tell from `resolve-store`
    alone whether its store matches the operator's.

### 4. What this container actually SEES

`tasks.yaml`: 592 records, md5
`d92dc26066610811024f0e79e1ce3a2b`, size 508 667 B, mtime 2026-06-13 09:43.

```text
by project (top):                by assignee (top):
   84  scitex-dev                    504  <none>
   83  scitex-agent-container         62  proj-scitex-todo
   80  scitex-todo                     8  proj-scitex-hub
   50  <none>                          5  lead
   32  ripple-wm                       3  proj-paper-scitex-clew
   28  business                        3  proj-scitex-agent-container
   25  scitex-hub                      2  proj-scitex-dev
   20  scitex-live-paper               1  proj-scitex-dict / -str / -types / ...
   ...
aj-* prefixed IDs: 0
assignee=proj-scitex-hub rows: 8 (all visible here)
```

So **this** container sees the 25 scitex-hub project rows + 8
`assignee=proj-scitex-hub` cards. The board card claims the
proj-scitex-hub *container* sees only `aj-*` and is missing those 7
hub-* cards. That asymmetry → the two containers are reading from
*different files*.

### 5. The store has its own git history (no remote, separate from the package repo)

```text
$ git -C /scitex-todo remote -v
(no remotes)
$ git -C /scitex-todo log --format="%h %ai" -5
bc7f0b8 2026-06-13 09:43:05 +0900
62c8f52 2026-06-13 09:13:37 +0900
285ee7d 2026-06-13 09:13:19 +0900
3dfa058 2026-06-13 09:03:29 +0900
cbf55cc 2026-06-13 08:42:21 +0900
```

The dotfiles store is its own git repo on `main`, no remote, mutated
many times per day. There is no automatic sync to anywhere. So any
divergence between two containers' bind sources is *permanent* until
someone manually rsyncs / commits across them.

### 6. Sync mechanisms today are stubs

```text
$ scitex-todo sync-store --help
... Phase 2 body: `git -C ~/.scitex/todo pull --rebase --autostash && git push`
    against an operator-owned remote. The stub prints the plan and exits 0 ...
```

`sync-store` is a **Phase-1 stub** — it has no real implementation.
`sync-github` only mirrors merged PRs into board cards; it does NOT
sync host-side stores to each other.

So today there is no mechanism in scitex-todo itself that would keep two
containers' stores in lockstep. The "single shared store" promise lives
*entirely* in the bind-mount spec, which (see Finding 2) is not the
spec's target path.

### 7. Source-of-truth design vs reality

Design (`p3a-2-todo-bind.yaml`):

```yaml
apptainer:
  binds:
    - "~/.scitex/todo:/home/agent/.scitex/todo:rw"
```

Reality (this container's mountinfo): the bind source is
`~/.dotfiles/src/.scitex/todo`. If the operator's `~/.scitex/todo` is a
symlink to that path on the host, the spec is honoured *by accident*;
if not, this container and the proj-scitex-hub container are reading
two unrelated files.

## Hypothesis

The proj-scitex-hub container's "missing 7 hub-* cards + only sees
`aj-*`" symptom is most consistent with:

> **(b) host-level path divergence: the proj-scitex-hub container's
> bind source resolves to a *different* host dotfiles tree (or to a
> stale snapshot of the dotfiles tree) than this proj-scitex-todo
> container's bind source. The two containers are not seeing the same
> file. Without a host-side symlink (or a runtime injector) enforcing
> the spec's `~/.scitex/todo` target, "single shared store" is not
> currently true on disk.**

Supporting evidence: the bind source visible here is *not* the spec's
target path; there is no remote on the store git repo, so divergent
trees won't auto-converge; `sync-store` is a stub; this container's
own store includes a full set of hub-* cards yet the hub container is
reported to see none. The simplest explanation is that the hub
container binds a different host directory entirely.

Secondary contributors (ranked):

  - (e) **stale snapshot**: the dotfiles tree may be different per
    SAC account (`/tmp/sac-claude` is bound from
    `.scitex/agent-container/accounts/ywatanabe-scitex-ai`); a
    per-project `.dotfiles` overlay could pin a hub container to an
    older fork of the store.
  - (c) **project-scope override**: per `_paths.py`, precedence-3
    (`<git-root>/.scitex/todo/tasks.yaml`) is consulted *before*
    user-scope. If `/work` in the hub container is a git repo with a
    `.scitex/todo/tasks.yaml` checked in, it would shadow the
    user-scope store entirely. **Not** the case in this container
    (`/work` = scitex-todo repo, no `.scitex/todo/` directory) but
    needs to be confirmed inside the hub container.
  - (d) **stale symlink**: if `~/.scitex/todo` on the host points
    somewhere stale, every container picks up the same wrong file.
  - (a) **snapshot copy from a different host**: less likely given
    `sync-store` is a stub and there is no push/pull active.

## Proposed fix options (NO implementation in this PR)

| Rank | Option                                                                                                                                                                                                                       | Risk | Cost   |
|------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|------|--------|
| 1    | **Realign bind source with the P3a-2 spec.** Make the host's `~/.scitex/todo` a *real symlink* into `~/.dotfiles/src/.scitex/todo`, OR change the dotfiles `_base/` spec to bind the actual host target the runtime uses. Add a startup probe (`scitex-todo verify-store`) that fails loudly when the resolved store's inode disagrees with an operator-pinned fingerprint. | LOW  | small  |
| 2    | **Drop the bind, use git-backed sync** (Phase-2 of `sync-store`). Each container keeps a local clone of an operator-owned remote; `sync-store --apply` runs on a timer and rebases. Eliminates "two binds, two files" by design.                                                                                                            | MED  | medium |
| 3    | **Hub-only triage**: enter the proj-scitex-hub container, run `scitex-todo resolve-store` + `cat /proc/self/mountinfo | grep todo` + `md5sum` to confirm exactly which file it is reading. Patch *that* container's bind directly without touching the design. Tactical, not strategic — buys time for option 1 or 2.                  | LOW  | tiny   |

**Recommended pick**: **Option 1** for fleet-wide rollout safety, with
**Option 3** as a same-day mitigation so the proj-scitex-hub agent can
see the 7 hub-* cards again *before* the architectural fix lands.

### Open questions for lead/operator

1. Is `~/.scitex/todo` on the host *actually* a symlink to
   `~/.dotfiles/src/.scitex/todo`? (Trivial to verify from a host
   shell; not visible from inside any container.)
2. Does the operator want one canonical store as a *file* (single
   inode shared by symlink) or as a *git-synced clone* (Phase-2
   `sync-store`)? The two have very different operational shapes —
   file-symlink means concurrent writers race; git-sync means rebase
   conflicts. The board card asks for "ONE consistent store" but does
   not specify which mechanism.
3. Per-project `<git-root>/.scitex/todo/tasks.yaml` (precedence-3) is
   currently *higher* priority than the bind-mounted user-scope store
   (precedence-4). Is that the intended precedence under P3a, or
   should the bind-mount win once it lands fleet-wide?
4. The cosmetic `bundled example` path leaks the build-time worktree
   (`agent-nudge`) into `resolve-store` output. Worth fixing in the
   same follow-up?

## Next step

**Owner**: proj-scitex-todo lead (architecture) + operator
(host-side dotfiles).

**Exact follow-up PR(s)** suggested:

  - `chore(p3a-2): pin tasks.yaml bind source — symlink + verify-store
    probe` (Option 1). Adds a `scitex-todo verify-store` subcommand
    that prints the resolved inode + host fingerprint and exits
    non-zero on mismatch; updates `p3a-2-todo-bind.yaml` to document
    the *actual* host path; lands a `~/.scitex/todo` → dotfiles
    symlink so the design and reality match.
  - `fix(hub-container): rebind tasks.yaml so proj-scitex-hub sees
    the canonical store` (Option 3, same-day mitigation in the
    `scitex-agent-container` repo).
  - **NOT** in this PR: `feat(sync-store): phase-2 git-backed sync`
    (Option 2) — large, separate, and depends on Open Question #2.

This audit PR is intentionally docs-only and does NOT block on
those follow-ups landing.
