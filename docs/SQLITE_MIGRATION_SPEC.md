# SQLite State Migration — Design Spec

**Status:** DRAFT v1 (2026-06-04). Author: agent + Ron.
**Decision context:** Ron chose "migrate MC state to SQLite" over an FTS-only
index or a doc-only spike (see chat 2026-06-04). The driver is **correctness,
not raw speed** — at current volumes JSON file reads are already milliseconds.

## 1. Goal & non-goals

**Goal.** Move the *contended, hot, or list-scanned* slices of MC's on-disk
JSON state into a single embedded SQLite database (`data/mc.db`) behind a thin
data-access layer (DAL), so we get:

- **Atomic writes for free.** Many writers today are plain `write_text()` (torn
  on crash): `save_project`, `_save_agent_log`, `_save_settings`,
  `_save_schedules`, `system_status`, hivemind manifests/workstreams. SQLite
  transactions replace the hand-rolled `_atomic_write_text` + per-project lock
  machinery.
- **Kill the `DATA_DIR` pollution foot-gun.** `load_projects()` globs
  `data/projects/*.json` and treats every non-excluded file as a project; a
  stray sidecar 500s the restart endpoints. The `EXCLUDED_SIDECAR_SUFFIXES`
  allowlist (server.py:1597) disappears once records are rows.
- **Replace "load-all-then-filter-in-Python" scans with indexed SQL.** `/usage`
  and `/telemetry` glob **every** `*_agent_log.json`, parse each, and aggregate
  in Python. That becomes one indexed query.

**Non-goals (explicit — we do NOT migrate dogmatically):**

- **Hand-editable singletons stay files.** `config.json`, `settings.json` —
  being greppable/diffable/hand-editable is a *feature*, and they're written
  rarely. Keep as files.
- **Secrets stay files.** `provider_env.json`, `firebase_admin.json`,
  `push_vapid.json`, `mobile_pairing.json` — locked files already; a GCP file
  (firebase) is supplied as a file. No benefit to moving.
- **Content artifacts stay files.** `MEMORY.md` (human-curated + git-tracked),
  `data/skills/_proposed/**/*.md`, `data/uploads/**` (binaries). These are not
  "state," and git-diffability is load-bearing.
- **No full normalization in v1.** Records stay JSON blobs in a row (see §4);
  we do not shred `backlog[]`/`activity_log[]` into child tables unless a query
  need appears (YAGNI).

## 2. Why SQLite (not NoSQL), recap

Our JSON files already *are* a document store. The upgrade that buys
atomicity + queries is **relational embedded SQLite** (one file, no daemon,
WAL, JSON1 functions). It's a natural fit for MC's **single-instance
invariant** (one process, threaded) — SQLite's single-writer model is exactly
that shape.

## 3. Architecture

### 3.1 DAL preserving signatures (the key to low risk)
~50 routes + `github_sync` + `project_sync` all reach state through a handful
of functions: `load_project` / `save_project` / `load_projects` /
`_load_agent_log` / `_save_agent_log` (and a few more). We **reimplement those
internals** to hit SQLite while keeping signatures and return shapes
byte-identical (e.g. `load_project` still returns the decorated dict). Callers
do not change. `project_sync` (verified: syncs the user's *code* worktree, not
record JSON; reaches records only via injected `load_project`/`save_project`)
and `github_sync` (backlog↔Issues over the API, through the record) are
unaffected.

### 3.2 `db.py` module
- Single connection file `data/mc.db`, `PRAGMA journal_mode=WAL`,
  `PRAGMA busy_timeout=5000`, `foreign_keys=ON`.
- **Thread-local connections** (Flask is `threaded=True`); WAL gives concurrent
  readers + one writer.
- **Schema versioning** via `PRAGMA user_version` + an ordered `MIGRATIONS`
  list applied at startup inside a transaction.
- Helpers: `q()` (read), `execute()`, and `tx()` context manager issuing
  `BEGIN IMMEDIATE` for read-modify-write so multi-step updates are atomic —
  this is what replaces the per-project `threading.Lock`s.

### 3.3 Storage model — blob rows vs. normalized columns
- **Records → JSON-blob row + extracted index columns.** `projects(id PK,
  data TEXT, status, blocked, display_order, last_updated)` — `data` is the full
  record JSON; the scalar columns are extracted on write for sort/filter. Keeps
  the opaque-dict contract exactly. (SQLite JSON1 can query into `data` later if
  needed.)
- **Logs → real columns.** `agent_log(project_id, ts, claude_session_id,
  status, summary, task, usage_json, cost_usd, provider, model, scribed, …)`
  indexed by `(project_id, ts)` and `(ts)` — this is where the query win lives
  (`/usage`, `/telemetry`, run-history). Worth normalizing.

### 3.4 Feature flags + reversibility
- Per-domain flag: `db_agent_logs_enabled`, `db_projects_enabled`, … (default
  **off**). One domain flips at a time.
- **Dual-write during soak.** While a domain's flag is on, writes go to **both**
  SQLite and the JSON file; reads come from SQLite with a **JSON fallback** if a
  row is missing (covers hand-edits + not-yet-backfilled). Rollback = flip the
  flag off; JSON is still current, nothing lost.
- **Backfill is additive.** A one-time idempotent importer reads existing JSON →
  upserts rows. Never deletes JSON during soak. JSON is retired (stop dual-write)
  only per-domain after a clean soak.

## 4. Scope table

| State | Action | Rationale |
|---|---|---|
| Project records `*.json` | **Migrate** (blob row) | hot, glob-scanned, torn-write, the foot-gun |
| `_agent_log.json` | **Migrate** (columns) | hot append, cross-project aggregate scans |
| `system_status.json` | **Migrate** (1 row) | written per agent-init, non-atomic |
| `_scribe_stats` / `_router_stats` / `_skill_stats*` | **Later** | telemetry; skill_stats already locked+atomic |
| Hivemind manifests/workstreams/JSONL | **Later / maybe** | append logs; defer unless contended |
| `schedules.json` | **Later** | small, rare writes |
| `config.json`, `settings.json` | **Keep file** | hand-editable feature |
| `provider_env`, `firebase_admin`, push/mobile secrets | **Keep file** | secrets / supplied files |
| `MEMORY.md`, `_proposed/*.md`, `uploads/**` | **Keep file** | content artifacts, git-tracked |

## 5. Phasing

- **Phase 0 — Foundation.** `db.py` (WAL, thread-local conn, `tx()`, migration
  runner), the flag scaffold, schema for Phase 1. No behavior change (flags off).
- **Phase 1 — Pilot: `agent_log`.** Best first domain: real query win,
  append+cap maps to `INSERT` + delete-oldest, and a bug is *recoverable*
  (history/telemetry, not the live record). Reimplement `_load_agent_log` /
  `_save_agent_log` / the `/usage` + `/telemetry` scans on SQLite behind
  `db_agent_logs_enabled`; backfill importer; dual-write soak; tests.
- **Phase 2 — Project records.** The headline: kills the foot-gun + glob-scan.
  `load_project`/`save_project`/`load_projects` on `projects` table; backfill;
  dual-write; remove `EXCLUDED_SIDECAR_SUFFIXES` reliance only after soak.
- **Phase 3 — Selective tail.** `system_status`, then telemetry sidecars /
  schedules / hiveminds as appetite warrants. Stop when remaining files are
  better as files.

## 6. Concurrency

WAL + `BEGIN IMMEDIATE` transactions in `tx()` replace the per-project
`threading.Lock`s for migrated domains. Any read-modify-write (e.g.
`_log_agent_activity` reading the record, mutating `activity_log`, saving) runs
inside one `tx()` so it's atomic against concurrent turns. `busy_timeout`
handles the rare writer contention in a single-process server.

## 7. Testing

- **Dual-read keeps existing fixture tests green.** `conftest.py` redirects
  `MC_DATA_DIR` and writes JSON fixtures; with JSON-fallback reads those still
  resolve during soak. Add a DB-backed conftest fixture for native tests.
- New tests: migration runner idempotency; backfill idempotency (run twice →
  same rows); dual-write parity (JSON == DB after a write); `tx()` RMW atomicity;
  rollback (flag off → reads JSON). Keep `test_load_projects_sidecar_exclusions`
  green until Phase 2 soak completes.

## 8. Risks & mitigations

- **Data loss on the headline domain.** → dual-write + additive backfill +
  per-domain flag; JSON remains the source of truth until soak passes.
- **WAL file handling on Windows / Tauri packaging.** → verify `mc.db`,
  `mc.db-wal`, `mc.db-shm` live under `DATA_DIR` and are excluded from any
  glob/sync; confirm clean open/close across restart.
- **Hidden direct-file readers.** → the inventory (this doc's basis) is the
  allowlist of access sites; grep gate in review before flipping each flag.
- **`mc.db` must not look like a project.** → it lives in `data/`, not
  `data/projects/`; `load_projects()` never sees it. (Still true post-Phase-2.)

## 9. Open questions

1. Connection strategy: thread-local vs. a tiny pool — start thread-local.
2. Do we ever want JSON1 queries into the project blob, or is fetch-by-id
   forever enough? (Decides whether Phase 2 needs more index columns.)
3. Soak duration per domain before retiring dual-write (propose: until Ron
   confirms a domain is clean in live use).
