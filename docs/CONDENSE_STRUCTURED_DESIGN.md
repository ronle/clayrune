# Clayrune Memory Condense — Structured-Output Redesign

> Status: **v1 IMPLEMENTED 2026-05-18, default-OFF** (`condense_mode='agent'`;
> flip to `structured` in Settings → "Condense Executor" to adopt, flip back to
> revert — Step 7 discipline). Companion to [`MEMORY_SYSTEM.md`](MEMORY_SYSTEM.md)
> (Leg C). Authored in chat 2026-05-18 after a root-cause review of the live
> `_dispatch_condense` path. Code: `server.py` `_condense_plan` /
> `_validate_condense_payload` / `_condense_apply` / `_run_structured_condense`
> + the `condense_mode` branch in `_dispatch_condense`. Tests:
> `tests/test_condense_structured.py` (8, green; full suite 40 green). The
> structured trigger is line-keyed (Open Question #5 resolved for this path —
> no remaining functional caveat vs. the agent path other than v2-scoped
> curated-rewrite). The interim safety net (max-turns 14 +
> `_condense_integrity_check`) remains the floor for the still-default `agent`
> path; this design is what makes that stopgap deletable once `structured` is
> telemetry-validated. Not yet committed; server restart needed for the new
> path to be selectable server-side (frontend picks up the selector on browser
> reload).

**Goal:** replace Leg C's executor — a free-roaming `claude -p` agent holding
the Write tool that rewrites a load-bearing file in place — with a single
**constrained, non-agentic model call returning structured output**, applied
to MEMORY.md by the server through the existing leaf-locked atomic writer. The
*trigger* (`_should_condense`), the *goal* (keep the auto-loaded file under the
line budget without losing facts), and the *format* (Leg 0 curated/managed +
archive) are unchanged. Only the executor changes.

## Why — the root cause (verified against `server.py`)

`_dispatch_condense` (server.py:4902) shells out to
`claude -p <11-step prompt> --dangerously-skip-permissions` and lets that agent
Read + Write MEMORY.md, MEMORY_ARCHIVE.md, and optionally CLAUDE.md itself.
Every pain point downstream is a *compensation* for that one over-powered
primitive:

| Symptom | Real source |
|---|---|
| Original ERROR bug (`--max-turns 5` exhausted by reads, CLI exits 1 before the write) | agentic tool-loop has an unbounded read phase before the write |
| 11-step prose prompt + "be turn-efficient, read in your first turn" coaching | prompting a free agent to behave like a pure function |
| `_condense_integrity_check` ok/heal/restore + pre-image snapshot + `wm_repaired`/`turn_cap` telemetry | the agent *can* corrupt the file or drop a `clayrune:wm:` watermark, so we forensically check after the fact |
| Spurious `condense_*` ERROR sessions in agent history | it's a real subprocess that exits non-zero |

The genuinely necessary judgment is **small and structured**: for each managed
`- [ … ]` entry decide `fold | demote | keep`, plus occasional curated-pointer
merge. That needs a model's judgment — it does **not** need an autonomous
file-surgery agent. A constrained call that *returns a decision list the server
applies deterministically* deletes the turn-budget problem, the corruption
surface, the heal/restore code, and the ERROR noise in one move.

## Big picture — executor swap

```
BEFORE (live today)                      AFTER (this design)
───────────────────                      ───────────────────
_should_condense ─┐                      _should_condense ─┐
                  ▼                                        ▼
   claude -p (agent + Write tool)          _condense_plan(project):
   ├─ Reads MEMORY.md / archive             ├─ read curated + entries + wm + archive-tail
   ├─ folds/demotes (own judgment)          ├─ ONE model call, no tools, JSON out  ← slow, NO lock
   ├─ Writes MEMORY.md  ◀── corruptible     ├─ validate payload (schema + invariants)
   └─ exits (rc may be ≠0)                  └─ _condense_apply(): re-read under leaf
                  │                              lock, REBASE decisions onto current
   _condense_integrity_check                     state, apply, _atomic_write_text
   ├─ ok / heal / restore  ◀── all gone          (reuses _mem_compose + archive append)
   └─ pre-image forensics
```

The model never touches the filesystem. The server is the only writer, via the
same `_mem_split_full` / `_mem_compose` / `_atomic_write_text` primitives that
`_commit_managed_entry` already uses.

## The structured payload (model output schema)

Input given to the model (all read-only, server-assembled, bounded):

- `curated` — the curated region text (above `clayrune:managed:begin`).
- `entries[]` — managed `- [ … ]` lines, each with a server-assigned
  `id = _sha8(line)` and its index.
- `archive_tail` — last N KB of MEMORY_ARCHIVE.md (dedupe context only; the
  full archive is never sent — it is permanent cold storage).
- `line_budget`, `hard_floor` (from CONFIG).
- **wm markers are NOT sent.** The model never sees, names, or decides on
  `clayrune:wm:` watermarks — the server owns them end to end.

Required model output (strict JSON, no prose, no tool use):

```jsonc
{
  "entry_decisions": [
    { "id": "<sha8>", "action": "keep" },
    { "id": "<sha8>", "action": "demote" },               // → archive verbatim, drop from managed
    { "id": "<sha8>", "action": "fold",
      "fold_into": "<exact curated section heading>",
      "pointer_line": "- [Title](file.md) — one-line hook" } // appended/merged into curated;
                                                              // raw entry ALSO demoted to archive
  ],
  "curated_rewrite": null            // v1: always null (see Scope). v2: full new
                                     // curated text, validated then applied.
}
```

`fold` is the only action that mutates the curated region in v1, and only
**additively** (append/merge a single pointer line under a *named existing
section*). Wholesale curated re-authoring is `curated_rewrite`, deferred to v2.

## Server-side apply — `_condense_apply` (rebased, under the leaf lock)

Mirrors the proven `_write_session_memory` discipline (slow model call OUTSIDE
the lock; mutation INSIDE):

1. Model call completes (slow, lock-free).
2. Validate payload (next section). On any failure → abort, leave file
   untouched, record telemetry. **No restore needed — nothing was written.**
3. Acquire `_get_mem_write_lock(pid)`. **Re-read** MEMORY.md and re-split
   (`_mem_split_full(_mem_migrate(...))`).
4. **Rebase** each decision onto the *current* entries by `id` (= `_sha8` of
   the entry line):
   - entry whose `id` is no longer present (Step-6 folded/teardown removed it,
     or floor relocated it) → silently skip that decision.
   - entry present but with no decision (appended after the snapshot) → `keep`.
   This makes the plan safe against concurrent Step-6 checkpoint appends.
5. Apply: `keep` stays; `demote`/`fold` → append raw line to archive
   (append-only, never dedupe-delete), drop from managed; `fold` also
   appends/merges `pointer_line` into the named curated section (reject at
   validation if the heading doesn't exist verbatim).
6. wm markers carried through **untouched** (`_mem_split_full` bucketed them;
   `_mem_compose(curated, entries, wm)` re-emits them — same as the floor).
7. Run the existing mechanical line-floor as the backstop (oldest `- [` →
   archive until under `hard_floor`).
8. `_atomic_write_text` MEMORY.md + archive. Release lock.

Crash-safety (precise contract — committee Seat 4 corrected the earlier
overstatement): this is **two** atomic writes under one lock — archive first
(`_append_to_archive`), then MEMORY.md (`_atomic_write_text`) — the *identical*
ordering `_commit_managed_entry` already uses. A crash *between* the two
writes leaves the relocated lines in the append-only archive while still
present in the managed region: **transient duplication, never loss** (an entry
is removed from the managed region only by the successful MEMORY.md write that
follows its archival). Retry converges — the next plan re-archives the same
lines (append-only, so a duplicate archive line; archive dedupe is Open
Question #2) and then removes them from managed. So: not a single atomic
write, but a fact is never lost and the steady state is correct. Decisions
are rebased by `_sha8` against live state, so re-running an old plan skips
vanished ids and is otherwise a no-op.

## Validation — what replaces `_condense_integrity_check`

The check does not vanish; it **moves earlier and becomes deterministic**.
Instead of forensic comparison *after* an untrusted process wrote the file, we
validate a *structured payload before* the server writes anything. Strictly
better: reject-before-write, no pre-image, no restore, no heuristic.

Reject the whole payload (→ leave file untouched, telemetry `rejected:<reason>`)
if any of:

- not valid JSON / schema mismatch / unknown `action`.
- any `id` not in the input set; any duplicate `id`.
- `fold` without `fold_into` matching an existing curated heading **verbatim**,
  or without a non-empty `pointer_line`.
- `pointer_line` contains a newline, a `clayrune:` sentinel, or a
  `clayrune:wm:` prefix (model must never synthesize machinery).
- net result would empty/▽75%-shrink the curated region (the *only* surviving
  heuristic — and here it blocks a bad *plan* pre-write rather than reverting a
  bad *file* post-write; far less likely to misfire because additive `fold`
  can't shrink curated at all in v1).

Note the asymmetry vs. the stopgap's `restore`: a legitimately aggressive
agent compaction could trip the post-hoc ">75% curated lost" guard and get
*reverted* (a good run masked as failure). With v1's additive-only curated
mutation, that class of false-positive is structurally impossible.

## Scope

**v1 (the necessary core):**
- `entry_decisions` (`keep`/`demote`/`fold`) + additive curated pointer merge.
- `curated_rewrite` always `null` (model may not re-author curated wholesale).
- CLAUDE.md condensation **removed from this path entirely** — it is the
  user's project instructions, not the managed region; conflating them is
  itself part of the over-engineering. Re-home or de-scope separately (it
  fires rarely, only >15 KB).

**v2 (deferred, telemetry-gated like Step 7):**
- `curated_rewrite`: model returns full proposed curated text; server validates
  (sentinels intact, every fact-bearing token from a configurable preserve-set
  still present, size sane) then applies. This is the only place "model
  produces file content" survives — but bounded, validated pre-write, and rare.
- Optional CLAUDE.md structured condense as its own sibling, never shared.

## Config / flag / rollback

New `condense_mode` in CONFIG + `_CONFIG_EDITABLE_KEYS`:

| Value | Behavior |
|---|---|
| `agent` | **default until validated.** Existing `_dispatch_condense` path (with the committed stopgap as its floor). |
| `structured` | New `_condense_plan`/`_condense_apply` path. |

Same default-off, flip-to-adopt, flip-back-to-revert discipline as Steps 6/7
and the long-session advisory. No data migration: both paths read/write the
identical Leg-0 format; switching is purely which executor runs. Rollback =
set `condense_mode=agent` (Settings toggle, no restart once both paths are
resident).

## Telemetry

Replace `turn_cap`/`wm_repaired` (artifacts of the agent path) on the
`structured` path with:
`condense_structured_ok`, `condense_rejected:<reason>`,
`condense_entries_{kept,demoted,folded}`, `condense_model_ms`,
`condense_decisions_skipped_rebased` (proves the rebase is doing real work
under concurrent Step-6 load). Keep `bytes_before/after` and the
`/api/project/<id>/scribe-stats` + condense-status surface as-is.

## Coexistence with the uncommitted stopgap

Independent and complementary. The stopgap hardens the `agent` path so it is a
safe *default* during the transition; this design is what eventually lets the
`agent` path **and** `_condense_integrity_check` be deleted. Recommended order
(pending Ron's call — he chose "design doc first"): land this design → review →
build behind `condense_mode=structured` default-off → soak on this deployment →
flip default → remove agent path + integrity guard in a later cleanup commit.

## Open questions for committee review

1. **`fold` granularity.** Is "append/merge one `pointer_line` under a named
   existing heading" expressive enough, or does fold need to *replace* an
   existing pointer line (supersede) — and if so, how is the target line
   addressed safely (by `_sha8` of the curated line, same as entries)?
2. **Archive dedupe.** v1 demotes raw lines append-only with no dedupe. Is
   unbounded archive growth acceptable (Step 7 / search owns retrieval), or do
   we need an exact-duplicate guard at append time (cheap, deterministic, no
   model)?
3. **Curated `>75%` heuristic.** With v1 additive-only, can curated even shrink?
   If not, drop the heuristic entirely in v1 and reintroduce it only as a v2
   `curated_rewrite` validator.
4. **Model + determinism.** `condense_model` (default sonnet) with
   `temperature=0` and JSON-mode/stop-sequence enforcement — acceptable, or
   force a specific model for schema reliability?
5. **Trigger mismatch — RESOLVED for the structured path (2026-05-18).**
   `_should_condense` legacy behavior fires on **bytes**
   (`condense_threshold_kb` 30 KB) while the mechanical floor fires on
   **lines** (`index_line_hard_floor` 185). Originally flagged as out of
   scope, but it produced a concrete defect *specific to structured mode*: a
   large `CLAUDE.md` (which structured v1 deliberately does not touch) keeps
   the combined-byte trigger permanently hot, firing a no-op condense every
   session-end. Fixed by branching `_should_condense`: under
   `condense_mode='structured'` the trigger is the auto-loaded MEMORY.md
   **line count vs. `index_line_budget`** (the thing structured actually
   controls) — CLAUDE.md/archive bytes are irrelevant, and trigger + target
   now agree in units. The legacy `agent` path keeps its combined-byte
   trigger unchanged. Tests: `test_structured_trigger_ignores_huge_claude_md`,
   `test_agent_trigger_unchanged_byte_keyed`. The broader pre-existing
   byte-vs-line mismatch on the *agent* path remains a separate ticket.

## Committee review (2026-05-18) — RATIFY-WITH-CONDITIONS

Four-seat review (memory-invariants / agent-knowledge / config-ops-rollback /
concurrency-lifecycle) against all aspects of CR agent behavior & knowledge.
**Unanimous: RATIFY-WITH-CONDITIONS. No blockers, no data-loss path.** Core
ratified: curated is additive-only & byte-preserved for non-fold lines;
sentinels structurally regenerated; watermarks byte-equivalent to the floor;
archive refactor behavior-identical & strictly append-only; second writer
correctly under the same leaf lock + atomic primitive; rules/incognito
injection untouched; demote/fold keep facts in the read-floor + mc-memory-search
corpus (read-side neutral-to-better); rollback bidirectional & format-stable;
DATA_DIR rule respected; no auto-restart; re-entrancy guarded.

**Conditions fixed in this commit:** bounded telemetry key (no raw exc in
`condense_rejected:`); fence-aware heading collection + unique-match-or-demote
(never misplace a pointer / never lose the fact); duplicate-id intentional-
collapse documented in code; curated-growth gauge (`curated_lines`) for soak;
trigger read-guard widened; CLAUDE.md "one writer" rule + `_commit_managed_entry`
docstring + this doc's atomicity claim trued up.

**Conditions DEFERRED (gate the default-flip, not the commit; ship default-off
exactly so soak surfaces them):**

6. **No trigger hysteresis.** Structured fires at `lines > index_line_budget`
   (160) but the model only "trends toward" budget and the mechanical floor is
   185 — a file parked at 161–185 lines can dispatch a model call on nearly
   every session-end (self-limited to one in-flight by `_condensing_projects`).
   Watch `condense_structured_ok` frequency during soak; if it re-fires hot,
   add a post-condense cooldown or a `budget * margin` band before flipping
   `condense_mode` default to `structured`.
7. **Curated-index monotonic drift.** Additive-only fold with no semantic/shape
   guard and no mechanical eviction (v2 `curated_rewrite` is the eviction path,
   deferred). A liberally-folding model can accrete low-value pointer lines
   into the file every CR agent auto-loads, and since the trigger counts
   curated lines this is mildly self-perpetuating. The new `curated_lines`
   status gauge is the soak signal; sustained climb gates the default-flip and
   pulls v2 (or a light `- [..](..) — ..` shape guard) forward.

## Test plan (when built)

- Pure-function unit tests: payload validation (every reject branch),
  `_condense_apply` rebase (decision for a since-removed id is skipped; entry
  added after snapshot is kept; wm markers byte-preserved through a full
  apply), archive append-only invariant, sentinel/format invariants.
- Concurrency: a Step-6 checkpoint append racing `_condense_apply` under the
  shared `_get_mem_write_lock` loses nothing and corrupts nothing.
- Golden: a realistic over-budget MEMORY.md → plan → applied file is
  under-budget, every fact still present in curated-or-archive, byte-identical
  curated for `keep`-only entries.
- Equivalence: same input through `agent` vs `structured` produces
  functionally equivalent (not byte-identical) results; no fact lost in either.
