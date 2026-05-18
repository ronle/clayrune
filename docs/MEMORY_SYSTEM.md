# Clayrune Memory System — Layout (canonical map)

> Status: v1 (Legs 0/A/B/C + Fix A/B) committed `24a3af8` (2026-05-17).
> Step 6 mid-session note-taker committed `9683996`, **offline- AND
> live-validated end-to-end (2026-05-18), currently ENABLED**
> (`scribe_checkpoint_enabled=true`, `scribe_checkpoint_kb=8`; ships
> default-off in code, one Settings toggle to revert). Both on `origin/master`.
> This is the at-a-glance map; **design rationale + committee reviews live in
> [`MEMORY_SYSTEM_SPEC.md`](MEMORY_SYSTEM_SPEC.md)** — that is authoritative
> for *why*; this doc is authoritative for *what's where*.

**Goal:** headless project agents build on each other's work without a human
relaying anything. MC (server-side) owns memory; the agent is never trusted
to self-document.

## The big picture — three paths

```
                          ┌─────────────────────────────────────────┐
                          │            MEMORY.md (per project)        │
   reads (CLI auto-load)  │  ┌────────────────────────────────────┐  │
  ┌──────────────────────▶│  │ CURATED region (human/condense)    │  │
  │                        │  │  - pointer index, byte-preserved   │  │
  │                        │  ├──── <!-- clayrune:managed:begin --> ┤  │
  │   ┌───────────────────▶│  │ ## Session Log (MANAGED region)    │  │
  │   │  WRITE (Scribe)     │  │  - [date] **task** — summary       │  │
  │   │                     │  │  - [date] ... _(stopped)_ — ...    │  │
  │   │                     │  │  <!-- clayrune:wm:<sid> {...} -->  │◀─┼─ Step 6
  │   │                     │  ├──── <!-- clayrune:managed:end -->   ┤  │   only
  │   │                     │  └────────────────────────────────────┘  │
  │   │                     └───────────────┬───────────────────────────┘
  │   │                                     │ overflow (oldest, lossless)
  │   │                                     ▼
  │   │                          MEMORY_ARCHIVE.md  (permanent, searchable)
  │   │
[Agent session]                  TRIM: mechanical floor (line-keyed) + Leg C condense model
  │   │
  └───┴── READ: deterministic grep read-floor injected at dispatch
          + GET /api/project/<id>/memory/search  + mc-memory-search skill
```

- **WRITE** — the Scribe summarizes a finished session from its full `.jsonl`
  transcript into the managed region.
- **READ** — relevant prior memory is force-fed at dispatch (deterministic
  floor) and pullable on demand (search / skill).
- **TRIM** — keeps the auto-loaded file under the line budget without losing
  anything (relocate, never delete).

## Components

| Leg / Fix | Plain English | Status |
|---|---|---|
| **Leg 0 — Format** | MEMORY.md = curated notes on top + a sentinel-walled managed region MC owns. Migration lazy, idempotent, never touches curated. | ✅ shipped `24a3af8` |
| **Leg A — Scribe** | On session end, read the full on-disk `.jsonl`, cheap-model-summarize one line into the managed region. Thin/refusal guards; falls back to stdout tail on any failure; never breaks completion. | ✅ shipped |
| **Fix A** | Scribe also fires for `error`/`stopped` sessions (tagged `_(error)_`/`_(stopped)_`), not just clean completion. | ✅ shipped |
| **Fix B** | Startup reconciler closes the hard-MC-kill gap; first boot baseline-stamps history `scribed:true` *without* re-scribing it. | ✅ shipped |
| **Leg B — Read** | `/memory/search` ranked grep + a deterministic top-k floor injected into fresh dispatches + `mc-memory-search` skill. | ✅ shipped |
| **Leg C — Trim** | Line-keyed lossless mechanical floor (relocate oldest→archive) + condense model tier (value-based fold/demote, never delete). Archive is permanent searchable cold storage. | ✅ shipped |
| **Step 6 — Mid-session note-taker** | Mode-B per-turn capture (not just teardown). Append-only checkpoint entries; watermark folded into MEMORY.md; leaf-locked; semaphore-bounded. | ✅ shipped `9683996`, live-validated 2026-05-18, **currently ENABLED** (default-off in code) |
| **Step 7 — bge-m3 retrieval** | Replace grep with server-side semantic search. | ⏸ deferred (telemetry-gated) |

## Memory layers & audiences (read this before "consolidating")

There are distinct memory layers serving **different audiences**. They are
not redundant and they do not compete — confusing them leads to wrong
"cleanup" (e.g. pointing headless agents at a plugin, or removing the layer
that corrects the others).

| Layer | Audience | Store | Status |
|---|---|---|---|
| **Scribe system** (Legs 0/A/B/C + Fix A/B + Step 6) | **CR / headless project agents MC dispatches** | project `MEMORY.md` (curated + managed region), CLI auto-loads it | the only memory CR has; built *because* headless agents cannot load plugins |
| **engram** | **direct operator↔assistant sessions** (a human in Claude Code on this repo) | engram SQLite store + curated topic files in `~/.claude/projects/<enc>/memory/` | active, healthy (`mem_doctor`); conflict-aware; the long-term operator-collaboration layer |
| **memsearch** | (was meant for CR) | — | **RETIRED 2026-05-18** — verified inert; impossible for CR (plugins don't run headless), redundant with engram for the only role it could serve |

**Founding constraint:** headless MC-dispatched agents **cannot use Claude
Code plugins** (engram/memsearch). That is the entire reason the Scribe
system exists. Never try to wire a plugin to CR; never remove engram (it is
the conflict-aware layer that *corrects* Scribe mistakes).

**The Scribe captures conclusions, not truth.** It has no fact-check;
`condense` only compresses, it doesn't verify. A confidently-wrong agent
conclusion becomes durable, cross-session, self-reinforcing memory (this
happened — a stale-doc misread poisoned MEMORY.md until cleaned 2026-05-18).
Mitigation is behavioral, not structural: **verify volatile/operational
state against the live source before asserting it; never let the Scribe or
engram enshrine an unverified operational claim.** See the
`feedback-verify-volatile-state` memory.

## Key files & anchors

- **`server.py`** — Leg-0 helpers `_mem_split`/`_mem_compose`/`_mem_migrate`;
  Scribe `_scribe_extract`/`_scribe_call`/`_scribe_render_transcript`/
  `_scribe_stat`; shared writer `_write_session_memory`;
  `_reconcile_unscribed_sessions` + `_startup_memory_maintenance`;
  `_memory_search` + read-floor in `_build_agent_context`; Leg C prompt in
  `_dispatch_condense`; `_scribe_lock`; the `load_projects` /
  `_get_active_restart_blockers` DATA_DIR-exclusion fix.
- **`data/skills/builtin/mc-memory-search/SKILL.md`** — on-demand pull skill
  (auto-installs on startup).
- **`docs/MEMORY_SYSTEM_SPEC.md`** — full design + committee reviews;
  §3.A.MID is the committee-hardened Step 6 design.
- **`CHANGELOG.md`** `[2026-05-17]`, **`CLAUDE.md`** "Memory system" section
  (incl. the load-bearing DATA_DIR rule), **`docs/USER_GUIDE.md`**
  "Memory & Rules".
- **Telemetry:** `GET /api/project/<id>/scribe-stats`
  (`scribe_extracted` vs `scribe_fell_back:<reason>`).

## Config surface (all in `_CONFIG_EDITABLE_KEYS`, editable in Settings)

| Key | Default | Role |
|---|---|---|
| `scribe_enabled` | `true` | master switch (off → legacy stdout-tail write) |
| `scribe_model` | `''`→haiku | scribe model |
| `scribe_reconcile_enabled` | `true` | Fix B startup reconcile |
| `scribe_reconcile_cap` | `5` | max reconciled sessions / project / boot |
| `read_floor_topk` | `3` | deterministic read-floor snippet count |
| `index_line_budget` | `160` | Leg C model-tier target (lines) |
| `index_line_hard_floor` | `185` | mechanical floor trigger (lines) |
| `scribe_checkpoint_enabled` | `false` | Step 6 kill-switch (default-off) |
| `scribe_checkpoint_kb` | `0` | Step 6 cadence dial (≈8 once enabled) |

## Lifecycle of one session

```
dispatch ─▶ _build_agent_context injects "--- RELEVANT MEMORY ---" (read-floor)
         ─▶ agent works; may pull mc-memory-search skill on demand
         ─▶ [Step 6, LIVE] on each Mode-B turn boundary: render delta from
            watermark, reduce into running_summary, append a checkpoint entry,
            update embedded wm: marker — one atomic write, leaf-locked, bounded
         ─▶ session ends (completed / error / stopped)
            └▶ _log_agent_completion → _write_session_memory:
                 _scribe_extract over full .jsonl → one line
                 → _mem_migrate/_split → append to managed region
                 → mechanical floor (oldest → MEMORY_ARCHIVE.md)
                 → maybe _dispatch_condense (Leg C model tier)
                 → mark agent_log scribed:true
MC restart ─▶ _startup_memory_maintenance: backfill → _reconcile_unscribed_sessions
            (baseline-stamp history; capture any hard-killed session it missed)
```

## Load-bearing rules (don't violate)

- **Anything written into `DATA_DIR` (`data/projects/`) MUST be
  suffix-excluded in `load_projects()`** — else it parses as a phantom
  project and 500s the restart path. (`_agent_log.json`, `_scribe_stats.json`
  already excluded.)
- The **curated region of MEMORY.md is byte-preserved** — only the Leg C
  condense model may rewrite it; mechanical machinery touches the managed
  region only.
- The **archive is permanent** — relocate/demote, never delete or truncate;
  Leg B search depends on it.
- **Mode B note:** with `use_streaming_agent` (the global default) the
  session-end Scribe fires at *teardown* — Step 6 (LIVE) adds the per-turn
  capture on top of it. If Step 6 is ever disabled, Mode-B reverts to
  teardown-only memory.
- **Step 6 fold-in contract (implemented — don't break):** the
  `<!-- clayrune:wm:<sid> … -->` watermark marker is not a `- [` entry.
  `_mem_split_full` buckets it (back-compat `_mem_split` is the 2-tuple
  wrapper), `_mem_compose` re-emits it, the floor never relocates it, and
  the Leg C condense prompt preserves it verbatim. Any change to the Leg-0
  format/floor/condense must keep these.

## Open items

1. **Step 6** — shipped, live-validated, currently enabled. Soak-watch the
   `checkpoint_*` counters in `/scribe-stats` under real parallel/long-session
   load (cost + semaphore/coalescing behavior); revert via
   `scribe_checkpoint_enabled=false` if anything looks off.
2. **Step 7** — bge-m3 semantic retrieval, deferred until archive-size
   telemetry shows grep degrading.
3. **Spec header true-up** — `MEMORY_SYSTEM_SPEC.md` top-of-file blurb
   predates v1 shipping separately; §3.A.MID / §7 are authoritative.
4. **Not pushed** — memory commits are local on `master`; `git push` unrun.
5. **Skills Curation (deferred, depends on 6/7).** Hermes-equivalent
   self-evolving skills layer is planned but **not designed yet** —
   pending Steps 6/7. Principles locked: *MC owns, agent proposes,
   human promotes*; Distiller runs parallel to Scribe at session end
   (same trigger, different output); proposals land in
   `data/skills/_proposed/<session>/` and never auto-install; evolution
   via maintenance-audit extension, not a new loop. Step 7's bge-m3
   retrieval is the prerequisite for "skill-relevance hint" at dispatch
   (semantic similarity, not grep). Surface as a candidate sprint in the
   monthly audit once Steps 6/7 ship.
