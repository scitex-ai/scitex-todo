# ADR-0015 — One hub, not per-host sync; SQLite now, Postgres later

**Status:** ACCEPTED (operator-directed 2026-07-20)
**Owner:** scitex-cards
**Promotes:** `docs/design/remote-hub-backend.md` (PROPOSED 2026-07-17) to a
canonical decision. That document holds the full mechanism — endpoint table,
token model, tunnel units; this ADR records the *decision* and the reasoning
the design left implicit, and does not restate the wire format.
**Depends on:** ADR-0010 (cards.db is the single source of truth). This ADR is
that ruling extended across hosts.

## Context

The store lives on one machine today (ywata-note-win). Spartan, the NAS and a
MacBook Air should share it. The operator asked three questions: how do hosts
share it; is SQLite adequate or should it be Postgres; and is a database
abstraction (as Django offers swappable backends) worth having.

The tempting answer to the first — a database on each host, kept in sync — is
the wrong one, and this session is why. Every board-destroying incident of
2026-07-16→20 had one shape: two representations of the store, and code
treating *absence* in one as *a decision* about the other. A packaged fixture
read as the board; a 5-card file replacing 2,159 rows; two spellings of one
path each re-stamping the other; a reconcile deleting cards a stale document
omitted. Per-host databases with a sync layer is that failure class made
permanent and distributed. "Sync" is the word for it.

## Decision

**1. One hub. The others are clients, not replicas.**

Exactly one host holds `cards.db`. Other hosts reach it over HTTP, through a
verb-level backend seam:

```
remote agent                         hub
 local stdio MCP (unchanged tools)    scitex-cards serve
   └ HubBackend ── HTTP over ssh ───→   └ handlers → the same locked _store verbs
     (SCITEX_CARDS_HUB_URL)   reverse       one cards.db
                              tunnel
```

There is still one store, opened by processes on one machine, so `flock` +
SQLite WAL semantics stay valid. (WAL over NFS/SMB is unsafe — which is exactly
why a shared *filesystem* is not an option and HTTP-fronting is.)

**2. No silent local fallback. Ever.**

A host with `SCITEX_CARDS_HUB_URL` set but no reachable hub fails LOUD at the
first call. It must never fall through to a local file — that would mint the
second representation this whole architecture exists to prevent. The seam's own
code enforces this; the ADR records it as a rule, not an implementation detail.

**3. SQLite now. Postgres only when the hub itself must be replicated.**

SQLite's real weakness is many concurrent writers. The hub topology removes
that pressure by construction: every host funnels through one hub process, so
writes serialize *inside* the hub and the concurrency SQLite dislikes never
arrives. At the board's scale (a few thousand cards) SQLite is not the
bottleneck and will not become one from adding client hosts.

Postgres becomes warranted at a different trigger, and only then: when the
**hub itself** needs to be more than one process — an always-on production
server, or multiple hub replicas for availability. That is not this stage, and
adopting Postgres before it would add operational weight against a problem we do
not have.

**4. scitex-db is the swap path, not today's layer.**

`scitex-db` (the operator's cited abstraction) is the right seam for the
SQLite→Postgres migration *when* it comes — its stated purpose is precisely
"switching SQLite ↔ Postgres without rewriting every call site." But adding it
now would be abstracting over a structural defect (two stores) instead of
removing it, and an abstraction added to paper over a structural problem
entrenches it. The order is: collapse to one store (done / in progress), keep
the storage engine behind the existing `_backend` seam, and slot scitex-db in
*below* that seam if and when Postgres is needed. The seam that makes the swap
cheap already exists; nothing about choosing SQLite now forecloses Postgres
later.

## Consequences

- The remote agent's MCP surface is byte-identical to local — only the storage
  verbs beneath it swap from file to HTTP. No agent learns it is remote.
- One host is a single point of failure for the board. Accepted at this stage;
  the off-site snapshot rail is the recovery path, and hub HA is the Postgres
  trigger above, not a reason to distribute the store now.
- Identity on the wire (`X-Scitex-Agent`) is host-authenticated and
  agent-declared in v1 — spoofable between mutually-trusting fleet agents.
  Acceptable now; per-agent token rows are the named v2 hardening.

## What already exists (this ADR canonizes, it does not commission)

Verified in-tree at time of writing, not planned:

- `src/scitex_cards/_backend.py` — the verb-level seam, `LocalBackend` +
  `HubBackend`, switched by `SCITEX_CARDS_HUB_URL`.
- `src/scitex_cards/_backend_http.py` — `HubBackend`, a real client (28
  functions), not a stub.
- `src/scitex_cards/_server.py` — `scitex-cards serve`, loopback-only bearer RPC.
- `docs/ops/scitex-cards-hub-tunnel.service` — the reverse-tunnel unit.
- A Spartan pilot verified end-to-end on 2026-07-18 (hub doctor four-green over
  the tunnel; write matrix confirmed both sides).

## Sequence

1. land the store-safety and append-only work (in progress / released as 0.17.3)
2. row-level writes — retire the document round-trip that produced the stale-read
   failure class (its own card)
3. make the hub the normal path; point Spartan / NAS / MacBook Air at it via
   HTTP over the tunnel
4. only if/when the hub must be replicated: scitex-db backend swap to Postgres,
   behind the seam that already exists

## What this ADR does not claim

The hub is built but not yet the *default* path — local file is still the norm
on the primary host, and the multi-host rollout (step 3) has a pilot, not a
fleet. This records the decision and the reasoning; it does not assert the
migration is done.
