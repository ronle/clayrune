# Clayrune Memory System — Design Spec

Status: **DRAFT v2 (post-committee-review)** · Author: design session 2026-05-15
· Supersedes the ad-hoc Session-Log append + recency-trim mechanism.

> v2 changelog: closes committee blockers — added §3.0 file-format contract
> (B1), raw-transcript input + map-reduce (B2), deterministic read floor shipped
> with the skill (B3), normative v1 write target (B4), dispatch-time
> incognito/housekeeping gate (B5), dedicated scribe lock (B6), re-grounded Leg C
> rationale (B7), mandated 2295 rewrite (B8), anchor corrections, narrowed
> pre-impl spike (`.jsonl` readability empirically de-risked 2026-05-15 — only
> tool_result-truncation [v1] and live-flush [Phase 2] remain), scribe
> telemetry. Mid-session checkpointing is **in v1 scope,
> same build** — gated behind `scribe_checkpoint_kb` (default disabled), flipped
> on once session-end telemetry validates extraction quality; NOT a separate
> release. Its one hard prerequisite is the crash-durable watermark (§3.A.MID).
>
> Decisions resolved: **D2** `condense_threshold_kb`=20 · **D3** no hard-delete,
> no periodic archive compaction, unbounded archive + Leg B search · **D4**
> mid-session in v1, flag-gated, not deferred.

---

## 1. Problem

Headless project agents must be *knowledgeable* (have the relevant prior context
when they need it) and *consecutive* (each build on prior agents' work). The
current system fails on three independent axes, each verified against `server.py`
and the live memory dir:

### 1.1 The write path is exhaust, not knowledge
On completion, `_log_agent_completion` takes the **last non-`[`-prefixed line of
the agent's stdout**, truncates to 300 chars, and appends it as the memory
(server.py:3493). No summarization, no model in the loop. A typical row is the
agent's sign-off sentence. Retrieval over this corpus returns well-routed
garbage.

### 1.2 Reliability cannot live inside agent cooperation
We cannot force a headless agent to follow a rule. Instruction salience *decays*
as context grows and is effectively gone after a compaction — which is exactly
why the global "always document" rule is obeyed on turn 1, ignored by turn 40,
and why manual mid-session nudging works briefly then stops. **Any memory
mechanism that depends on the agent choosing to act is unreliable by
construction.** This is not an agent defect; it is the wrong layer.

### 1.3 The auto-loaded index silently truncates — and the guards measure the wrong axis
The Claude CLI auto-loads the project `MEMORY.md` natively (server.py:2282-2284;
MC deliberately does not re-inject it). The harness loads only **~200 lines** of
it. Two guards exist and **both are byte-keyed while the failure is line-keyed**:

- Session-Log overflow→archive: hard-coded `> 20 * 1024` bytes (server.py:3506),
  then "keep last 20 entries" (server.py:3512-3515).
- Condense-agent trigger: `condense_threshold_kb` default **30 KB** combined
  (server.py:784; default server.py:231; gated by `condense_enabled`
  server.py:759).

At ~60 chars/entry, ~200 lines ≈ ~12 KB — the line cliff is hit **long before**
either byte guard fires. Result: entries past line ~200 vanish with no error.
This already happened (2026-05-14: 263 lines, ~25% of pointers silently
dropped, symptom "agents seem to lose memory today"). Lifespan of the current
design ≈ `150 / completed_sessions_per_day` ≈ **~2–3 weeks** of active use.

### 1.4 Trimming by recency destroys value
The "keep last 20" rule uses recency as a proxy for worth. Twenty trivial
sessions evict a hard-won gotcha from 25 sessions ago. Mechanical trimming
cannot preserve value because it has no notion of value.

---

## 2. Principles

1. **Control plane / data plane split.** The agent is the data plane: its only
   job is to do the work. It is *never asked to document, never trusted to
   self-report*. MC is the control plane: it *observes* the transcript it
   already has and owns all memory writes, deterministically.
2. **The guarantee lives where cooperation is not required.** Every reliable
   step is server-side, triggered by MC, over data MC already holds.
3. **The index is a visibility budget, not a retention budget.** The ~200
   auto-loaded lines are scarce; the topic-file corpus and archive are cheap and
   unbounded. Nothing is ever destroyed to save the index — only relocated.
4. **The model judges value; mechanics only enforce the physical floor,
   losslessly.** Recency is never a proxy for worth.
5. **Reads are opportunistic; writes are not.** A missed read costs a
   re-derivation. A missed write is permanent loss. The write path may never
   depend on agent cooperation; the read pull-tool may.

---

## 3. Architecture — three legs

### Leg 0 — FILE FORMAT CONTRACT (foundational; everything depends on it)

**B1 closure.** The auto-loaded `MEMORY.md` has, today, **no machine-parseable
boundary** between hand-curated pointer lines and machine-managed entries. The
canonical project file is currently 100% curated index with *no* `## Session
Log` section, so the existing relocation code (server.py:3505-3534, keyed on a
`## Session Log` marker) is **dead code there** and a line-keyed floor would have
nothing safe to act on — or would shred curated pointers.

The contract, established before any other leg ships:

```
<curated pointer index — human/condense-curated, NEVER touched by the
 mechanical floor; only the Leg C model tier may compact it>

<!-- clayrune:managed:begin -->
## Session Log
- [date] **task** — <scribe entry>
...
<!-- clayrune:managed:end -->
```

- Everything **between the sentinels** is the *managed region*: machine-written,
  machine-relocatable, lossless-floor-eligible.
- Everything **outside** the sentinels is the *curated region*: only the Leg C
  model tier may rewrite it, and only by demote-not-delete.
- A one-time idempotent **migration** wraps any existing `## Session Log` (or
  appends an empty managed region) on first run. Files with no managed region
  get one created at first scribe write.
- The mechanical floor (Leg C) selects relocation candidates **only by sentinel
  boundary**, never by absolute line number. Relocating a curated pointer is
  defined as data loss (its discoverability is destroyed even if bytes survive).

**Two distinct overflow problems, do not conflate them:**
1. *Managed-region growth* (session entries accumulate) → mechanical floor
   relocates the managed tail to archive. Lossless, judgment-free.
2. *Curated-index growth past the ~200-line harness cliff* — the **actual
   2026-05-14 failure** — is a **Leg C model-tier responsibility** (intelligent
   pointer-merge), NOT the mechanical floor's. The mechanical floor must never
   touch curated lines.

### Leg A — WRITE: the Scribe (MC observes, the agent does nothing)

MC spawns agents with `--output-format stream-json` (server.py:599). The Scribe
is a cheap (Haiku) extraction call MC runs over the transcript. The agent is not
asked, not told, cannot skip it.

**Triggers — both in v1 scope, staged by config flag, not by release.**
1. **Session end** — always on. Runs over an *inert, closed* transcript: no
   concurrency, no partial-line reads, no watermark. This is the validation
   vehicle for the extraction-quality bet.
2. **Mid-session checkpoints** — same build, **gated behind
   `scribe_checkpoint_kb` (default `0` = disabled)**. Flipped on per-project (or
   globally) once §8 telemetry shows session-end extraction quality is good.
   This staging keeps an unvalidated quality bet decoupled from the hardest
   concurrency code *without* making it a separate release — directly serves the
   in-session-loss / long-session-extraction goal that session-end-only does
   not. Hard prerequisite: §3.A.MID watermark. Rationale: on a long session the
   single end-of-session map-reduce (B2) is lossier and costlier than extracting
   each segment fresh as it happens, so mid-session is a *quality* lever, not
   only resilience.

**Input — the on-disk CLI `.jsonl` is the ONLY full-fidelity source (B2
closure; empirically verified 2026-05-15).** MC retains *nothing* full-fidelity
itself: `_read_agent_stream` (Mode A server.py:2634 / Mode B 2794) parses the
stdout stream-json and keeps only `assistant.text` + a *formatted summary* of
tool_use in `session['log_lines']`. It has **no `user`-message branch**, so
**every `user`/`tool_result` is discarded** — i.e. MC's in-memory record is
blind to the entire empirical half of a session (tool outputs, file contents,
test failures, errors), plus all `thinking` blocks and raw tool inputs. A real
368-line transcript confirmed: 63 tool_use / 63 tool_result / 46 thinking / 24
text — only the 24 text + tool_use summaries survive in `log_lines`. Therefore
the scribe over `log_lines` (or stdout capture) is structurally a lossy slice =
the §1.1 defect reborn. The scribe MUST read the CLI's on-disk transcript, which
is the only place tool_result/thinking survive. Reading it is **already proven
reliable in production** (`_backfill_agent_log_from_transcripts` server.py:3078,
transcript viewer). `_parse_transcript_messages` (server.py:474) is still
forbidden as the scribe's reader — it hard-caps at 300 messages and clips blocks
to `[:5000]`. The scribe reads the raw JSONL directly:
- Resolve the file with **`_find_transcript_file`** (server.py:454). The
  single-candidate `_session_transcript_path` (server.py:339) is **forbidden**.
  Empirically verified: this project's live transcripts are under
  `C--Users-levir-Documents--claude-mission-control`, which is the **`_`→`-`
  ALT candidate**, NOT the primary encoding (`…Documents-_claude-…`). The alt
  branch is load-bearing, not a fallback — `_session_transcript_path` would
  resolve the wrong directory and the scribe would silently never find the
  transcript. Do not "simplify" the two-candidate resolver.
- If the transcript exceeds the scribe model's context window (sessions can hit
  the 5 MB `_SESSION_SIZE_LIMIT`, server.py:289): **chunked map-reduce** —
  segment by turn boundaries, extract per-segment deltas, then a final reduce
  pass merges/dedups. The scribe input window strategy is normative, not an open
  question.
- **csid-missing fallback:** if the CLI never emitted a `session_id` (early
  crash, no transcript path resolvable), fall back to the *current* stdout-tail
  behavior (`summary[:300]`) — this is the one case the old path covered that a
  transcript-only scribe cannot, and it must be preserved as the floor of the
  floor.

**Output target — NORMATIVE for v1 (B4 closure).** Flat dated entries
`- [date] **task** — <scribe text>` appended **inside the Leg 0 managed region**
(`<!-- clayrune:managed:begin -->`). No dependency on Leg C: the scribe ships
*with* a well-defined home. The richer `{what_changed, what_learned,
what_to_watch, files, topic_key, supersedes?}` structured-delta form and
model-routed placement is **Phase 2**, gated on Leg C model tier existing. v1
buys "a model wrote a real summary instead of stdout-tail," in a parseable
location, with zero forward dependency.

**Fallback:** on any scribe failure, fall back to today's `summary[:300]`
(line **3492**) so completion never breaks (preserve the server.py:3539-3540
"never fail the completion flow" invariant). Every outcome is counted (§4
telemetry) so silent 100%-fallback cannot hide.

**Gating (B5 closure).** The scribe dispatch performs its **own** incognito +
housekeeping check at dispatch time — it does NOT rely solely on sitting inside
the `status=='completed'` block (server.py:3486). For v1 (session-end only) the
completion-block placement is sufficient, but the incognito/housekeeping
predicate is asserted explicitly at `_dispatch_scribe()` entry so Phase 2
mid-session triggers (which fire *before* the completion block) inherit it for
free. Honors the housekeeping early-return (server.py:3482-3483).

**Concurrency (B6 closure).** The scribe uses a **dedicated per-project lock
`_scribe_lock` / `_scribing_projects`**, distinct from `_condensing_projects`
(server.py:3637-3640). Reusing the condense singleton would make scribe and
condense silently no-op each other. Ordering rule: a completion scribe must
never be starved by a (Phase 2) checkpoint scribe — completion takes the lock
with priority; checkpoint scribes debounce and yield.

Config: `scribe_enabled` (default True), `scribe_model`
(`CONFIG.get('scribe_model','') or 'haiku'` — empty-string-safe idiom per
server.py:3697, NOT `.get(k,'haiku')`).

#### §3.A.MID — Mid-session checkpoints (REVISED post-committee 2026-05-17)

Status: design only, **not built**, ships **default-off**. v1-sketch had a
fatal flaw (rolling-entry-in-place) the skeptic+pragmatist committee converged
on independently; this is the corrected, simpler design. The append-only
correction also **dissolves the (i)/(ii) goal fork** — append-only checkpoint
entries are immediately visible to search and the read-floor, so live
cross-agent visibility (ii) is delivered for free with no rolling-entry
machinery.

**Trigger — Mode B `result`→idle ONLY.** The earlier prose pointing at
`_read_agent_stream` ~2659-2729 was a **Mode-A artifact and is wrong for
Mode B**. The sole correct hook is the Mode B reader's `result` handler
(`_read_agent_stream_b`, ~server.py:3170-3182, right after `status='idle'`),
cloning the existing `_auto_snapshot_notes_on_turn(session)` precedent at the
same point. Debounced by `scribe_checkpoint_kb` (≥N KB new transcript since
last checkpoint). **Exclude** the AskUserQuestion / plan-approval idle
transitions and any `process_alive=False` — those are not real turn
boundaries. Snapshot `(session_id, claude_session_id, num_turns, project_id,
transcript_path)` into locals **before** spawning the side thread; the worker
must never touch the live `session` dict (the reader mutates it lock-free on
the hot path). Inherits the dispatch-time incognito/housekeeping skip.

**Append-only — NO rolling entry, NO locator (committee blocker #1).** The
mechanical floor (`pop(0)` oldest→archive) and the Leg C condense model
rewrite/relocate the managed region *between* checkpoints, so any sidecar
locator (offset/line-index) goes stale → duplicate entries, eviction of a
live session's entry, or overwrite of an *unrelated* session's line
(cross-session corruption) — and it triggers in normal multi-session steady
state, silently. Therefore: **each checkpoint appends a normal, self-contained
managed entry**, reusing `_write_session_memory`'s existing append+floor+compose
tail **verbatim, zero format change**. Self-containment comes from the
cumulative `running_summary` (below), not from rewriting a prior line. A
session's multiple checkpoint lines are deduped/merged by the **Leg C model
tier** (it already merges "a newer entry wholly containing an older one") —
that is where judgment belongs. `mem_entry_locator` is deleted from the
watermark.

**Watermark — keyed by the stable MC `session_id` (committee blocker #2);
stored embedded in MEMORY.md (D6 RESOLVED — fold-in).** Per-live-session
durable record `{session_id, claude_session_id, transcript_path, byte_offset,
slice_hash, running_summary}`, persisted as a **single non-entry marker line
inside the managed region**, e.g. `<!-- clayrune:wm:<session_id> {…json…} -->`
— NOT a `- [` entry (so the mechanical floor's `pop(0)` and `_mem_split`'s
entry extraction ignore it), removed on clean teardown. It must carry the
`running_summary` because append-only entries are deliberately
non-addressable (blocker #1) — the marker is the *only* durable handle for
the next checkpoint's reduce base. One line per *live* session; bounded by
concurrent sessions (hivemind is housekeeping-excluded), transient, counts
toward the line budget like any line. `claude -r` resume/revival mints a **new
`session_id`/`.jsonl`** while MC reuses the internal session id; the v1
"resolve the predecessor's offset" was unimplementable (the only join key is
cleared before the new csid is known). Corrected contract: **if the resolved
transcript path differs from the watermarked `transcript_path` → reset
`byte_offset=0` and treat the new file as delta, but carry `running_summary`
forward unchanged as the reduce base** (no context lost). Detect the csid
change in the reader at the `session_id` message (~3117/3171) where both old
and new are in hand — not later from `_resume_id` (cleared on the recovery
path). **Delta render:** new `_scribe_render_delta(path, byte_offset)` —
`seek(byte_offset)`; if offset≠0 discard bytes up to and including the first
newline (**leading**-partial rule); read to the last complete newline
(**trailing**-partial rule); return `(text, offset_of_last_consumed_newline)`.
Offset only ever advances to a consumed `\n`. (`_scribe_render_transcript`
today reads the whole file and has no offset param — this is a required new
function, not a reuse.)

**Cumulative synthesis.** `new_running_summary = reduce(prev_running_summary,
scribe(delta))` — map-reduce, reusing `_scribe_call` verbatim. Skip the reduce
call when the delta extracts to "no material change" (advance the offset, don't
spend a model call).

**Concurrency — dedicated leaf lock (committee blocker #3).** A per-project
`_mem_write_lock[pid]` (accessor cloned from `get_manager`) wraps **only the
MEMORY.md read-modify-write inside `_write_session_memory`** — never the model
call, never nested under `get_manager(pid).lock`. Strict ordering:
outer(manager RLock at the teardown finally) → inner(mem leaf); the checkpoint
path never holds the manager lock at all, so the ordering is single-direction
and cannot deadlock. Move `_dispatch_condense` outside the locked region
(capture a "should-condense" boolean inside, dispatch after release) so the
mutex stays sub-ms. **Reject the serialized-writer-queue alternative** — its
teardown-flush against the manager lock is itself the deadlock generator.
*Side-benefit:* this also fixes a latent issue in already-committed code —
today the manager RLock is held across the ≤180s scribe call, so a parallel
session's teardown can stall on another's Haiku; the leaf-lock split removes
that. **Backpressure (replaces the removed per-project `'busy'` gate):** model
calls run fully parallel per-session, but a per-project **semaphore** (cap ~2)
+ a coalescing budget bound fan-out — over-budget checkpoints are *skipped*
(the next covers the larger cumulative delta — safe, deltas are cumulative),
never dropped. Parallel: yes. Unbounded fan-out: no.

**Atomicity (simplified by D6 fold-in).** Because the watermark lives *inside*
MEMORY.md, a checkpoint is **one atomic write** (temp + `os.replace`) carrying
both the new appended entry and the updated `wm:` marker — there is no
cross-file gap, so the duplicate/loss-on-crash class is *eliminated*, not just
ordered around. `_write_session_memory` must move to atomic temp+replace.

**Fix B reconcile coordination (simplified by D6 fold-in).** Clean teardown
**removes this session's `wm:` marker** as part of its final atomic MEMORY.md
write. A `wm:` marker still present at startup ⇒ the session was killed
mid-flight: `_reconcile_unscribed_sessions`, before its `_write_session_memory`
call, scans the managed region for a `wm:` marker for the entry's session — if
present with a non-empty `running_summary`, write **one finalizing entry from
that `running_summary` (no Haiku call)** instead of re-scribing the whole
transcript, set `scribed=True`, and drop the marker (one atomic write).
Absent → existing full-re-scribe behavior. The `_has_running_agent(pid)` skip
and the first-boot baseline / `scribed`-marker invariants are unchanged
(the `wm:` markers are orthogonal to the baseline; the `scribed` field stays
the source of truth for "captured?").

**D6 RESOLVED — watermark folded into MEMORY.md** (principal took the
recommendation). Rationale: a single atomic write deletes *two whole failure
classes the committee found* — the watermark/MEMORY.md atomicity gap and the
entire DATA_DIR-pollution-500 class — at the price of one machine-metadata
comment line per live session in the agent-visible file. **Accepted cost — a
load-bearing Leg-0 contract:** the `<!-- clayrune:wm:… -->` marker lives inside
the managed sentinels but is *not* a `- [` entry, so:
- `_mem_split` must return it as a third bucket (curated, entries, **wm
  markers**), and `_mem_compose` must re-emit the markers (today it
  reconstructs only from the entry list — it would silently drop them).
- The mechanical floor relocates only `- [` entries — it must never move/
  archive a `wm:` marker.
- The **Leg C condense prompt must be told to preserve `<!-- clayrune:wm:… -->`
  lines verbatim** (it rewrites the managed/curated regions; an un-preserved
  marker = a lost reduce base = a re-scribe-from-zero on the next checkpoint).
This Leg-0 preservation requirement is the real implementation cost of
fold-in and must be built *with* §3.A.MID, not after.

**Enablement criterion (unchanged).** `scribe_checkpoint_enabled=false` and
`scribe_checkpoint_kb=0` until §8 telemetry (`scribe_extracted` vs
`scribe_fell_back`, spot-checked quality) confirms session-end extraction is
sound; then flip the boolean and raise the KB dial conservatively (~8 KB),
watching the semaphore/budget counters and cost.

### Leg B — READ: server-side search + bounded index + pull skill

Four sub-parts. **Parts 2, 3, and 4 ship together** — the deterministic floor is
not deferred (B3 closure).

1. **The auto-loaded index stays a pure, bounded pointer map.** Scribe output
   goes in the Leg 0 managed region, never into the curated pointer region. The
   CLI keeps auto-loading it (server.py:2282-2284) — unchanged.
2. **`GET /api/project/<id>/memory/search?q=` — server-side search over the
   topic-file corpus + archive** (not the curated index; the agent already has
   that). v1 = ranked grep over the memory dir; later v2 = server-side bge-m3
   embeddings (MC server *can* run ONNX; the headless constraint only blocks the
   agent, not MC). Returns `{topic_file, why, snippet}` rows. The corpus has no
   auto-load ceiling, so it has no "lifespan."
3. **Deterministic read floor (ships WITH the skill, NOT deferred).** On dispatch,
   `_build_agent_context` (server.py:2235) runs the *same* server-side ranked
   search from part 2 against the **task text** and injects the top-k (≤3)
   snippets as a `--- RELEVANT MEMORY ---` part, after the awareness block
   (~server.py:2333). This is grep-ranked, deterministic, requires zero agent
   cooperation, and shares the part-2 code path (build once). Rationale: the spec
   forbids agent-cooperation-dependence for writes (Principle 2); the read side
   must not exempt itself. A probabilistic skill alone re-creates the exact
   instruction-decay pathology on the read side. The floor is intentionally
   *task-text-based* (the one place cold-prompt retrieval is acceptable, because
   it is a backstop, not the primary path) and is token-budgeted to avoid
   context bloat. NOTE: this is **not** the rejected session-start *router* — no
   second model, no relevance LLM; it is a deterministic grep injection.
4. **`mc-memory-search` built-in skill** wrapping the curl, installed under
   `data/skills/builtin/mc-memory-search/SKILL.md` (auto-installed on startup
   like existing built-ins; verified path: `_skills.install_builtins` →
   `~/.claude/skills/<name>/`, checksum-aware). Skills work headless; MCP/plugins
   do not. Mechanism rationale: headless Mode-A/B agents are spawned with
   **`stdin=subprocess.DEVNULL`** (server.py:3709 / 3566) — there is no stdin
   pipe at all, so a marker-intercept would force a full `claude -r` resume
   round-trip per lookup; a skill+curl runs sub-second *in the agent's current
   turn*. (The earlier "stdin closed at server.py:1118" citation was wrong —
   1118 is the unrelated Ask-Claydo SSE path; conclusion unchanged.) Add **one
   ≤2-sentence** entry to the list returned by `_clayrune_universal_capabilities`
   (server.py:2168) — that list is paid on *every* agent and hivemind-worker API
   call, so no curl/examples in the prompt (the skill carries them). Skill
   invocation is probabilistic — acceptable *because part 3 is the deterministic
   floor beneath it*; the skill is the deepening, not the guarantee.

**B8 closure (mandatory):** server.py:2295 currently tells every agent
*"Memory: {mem_file} (auto-loaded). Update it when you learn important project
info."* — the exact self-document instruction §1.2 declares unreliable, and once
the scribe owns writes it invites agent-vs-scribe write races on the same file.
This line MUST be rewritten to: *"Memory is auto-loaded and maintained for you
by Clayrune; to retrieve older context use the mc-memory-search skill. Do not
hand-edit MEMORY.md."* Shipping the scribe without this fix is a known
data-corruption path.

### Leg C — TRIM: model-curated, budget-bounded, lossless mechanical floor

Two tiers. The model owns all judgment; the mechanical step owns only the
physical ceiling and is forbidden from judging or destroying.

- **Model tier (the real curation).** The condense agent — already a Sonnet
  housekeeping agent (`_dispatch_condense` server.py:3634) — is given an
  explicit budget ("curated pointer index must stay < ~180 lines; archive is
  unbounded; topic corpus is searchable") and decides per entry: keep a pointer
  line / fold detail into an existing topic file / promote to a new topic file /
  demote to archive. It never trims by recency. It **never hard-deletes** except
  for strict supersession or exact duplicates (a newer entry wholly containing
  the old). "Not worth an index slot" means *demote to searchable cold storage*,
  never *delete*. **The model tier is the ONLY thing permitted to compact the
  curated pointer region** — including the §1.3 / 2026-05-14 failure case
  (curated index itself exceeding the ~200-line cliff via accumulated topic
  pointers). Intelligent pointer-merge of the curated region is exclusively its
  job; the mechanical floor must never touch curated lines.
- **Mechanical floor (judgment-free, lossless, MANAGED REGION ONLY).** The
  replacement for server.py:3506-3534. It operates **strictly within the Leg 0
  `<!-- clayrune:managed -->` sentinels** — it relocates the *managed-region
  tail* by sentinel boundary, never by absolute line number, never a curated
  pointer (Leg 0 defines that as data loss). Trigger re-keyed from bytes to
  **lines**: when total auto-loaded file line count exceeds the hard floor,
  move the oldest managed-region entries to `MEMORY_ARCHIVE.md` (no line limit;
  not auto-loaded) until back under budget, and flag the project for priority
  condense. If the *curated* region alone already exceeds budget (no managed
  slack to relocate), the floor cannot help — it flags priority condense and
  the model tier handles it. It never picks winners; it loses nothing.

**Why the floor is needed (B7 — corrected rationale).** Not because "condense is
skipped while an agent runs" — that was a misread: the completion-path call
`_should_condense(p, include_claude_md=True)` (server.py:3537) *bypasses* the
running-agent gate at server.py:766 (`if not include_claude_md and
_has_running_agent`). The real reason: condense is gated by a **30 KB combined
size threshold (D2: 20 KB) AND a singleton lock** (`_condensing_projects`,
server.py:3637-3640), while the ~200-line harness cliff is hit *far below* 20 KB
(~12 KB) regardless of whether condense ever runs. The synchronous lossless
floor guarantees the physical ceiling in the gap between cliff and any condense
firing. This is the §1.3 argument and stands on its own.

---

## 4. Config surface

| Key | Default | Controls |
|---|---|---|
| `scribe_enabled` | `true` | Leg A on/off |
| `scribe_model` | `haiku` | Scribe model (`CONFIG.get(...,'') or 'haiku'` idiom) |
| `index_line_budget` | `160` | Model-tier target ceiling (Leg C model) |
| `index_line_hard_floor` | `185` | Mechanical relocation trigger (Leg C floor) |
| `read_floor_topk` | `3` | Deterministic read-floor snippet count (Leg B.3) |
| `condense_enabled` | `true` | Existing — model tier on/off |
| `condense_threshold_kb` | `20` | **Lowered from 30** — the scribe writes far more than `summary[:300]`, so the model tier must run sooner to keep the curated region under the line budget. Still KB-keyed (operates on archive too); the *line* guarantee is the mechanical floor's job, this just paces the model tier. |
| `scribe_checkpoint_enabled` | `false` | Mid-session note-taker kill-switch (§3.A.MID). Default-off; flip on only after §8 telemetry validates session-end quality. |
| `scribe_checkpoint_kb` | `0` | Mid-session cadence dial (KB of new transcript before a checkpoint). Recommended ≈ 8 once enabled. Both this **and** `scribe_checkpoint_enabled` must be set for checkpoints to fire (§3.A.MID). |

Defaults block opens at `_load_config()` server.py:**219** (`condense_threshold_kb`
at 231); allowlist `_CONFIG_EDITABLE_KEYS` is the set at server.py:**8296-8301**.

**Budget band (S9 — hysteresis).** Harness cliff ≈ **200 lines** (empirical:
2026-05-14 degraded at 263). Band: model target **160** → mechanical floor
**185** → cliff **200**. The floor is evaluated **synchronously on every scribe
write and every completion** (not on a timer), so a single write cannot vault
185→200 between evaluations — at most one entry (~1 line) is added per
evaluation point. The 15-line floor→cliff margin absorbs the curated-region
case where the floor can't act and must wait for the model tier.

---

## 5. Failure modes & mitigations

| # | Failure | Mitigation |
|---|---|---|
| F1 | Scribe extracts wrong/confident-wrong memory | Append-only into dated sections; transcript stays ground truth; model tier can demote/supersede; never silently rewrites human-curated prose |
| F2 | Scribe call fails / slow / rate-limited | Falls back to `summary[:300]`; never blocks completion (server.py:3539-3540 invariant) |
| F3 | Mid-session checkpoint cost unbounded | Triggers are delta/event-based, not per-turn; `scribe_checkpoint_kb` gate; config-disable |
| F4 | Agent never invokes the read skill | **Leg B.3 deterministic grep floor injected at dispatch — ships WITH the skill, not deferred.** Skill is the deepening; the floor is the guarantee. (B3) |
| F5 | Curated region over cliff before model tier runs | Mechanical floor relocates managed tail (lossless); if curated region itself is over budget the floor flags priority condense — 15-line floor→cliff margin buys the gap (B7, S9) |
| F6 | Model hard-deletes something that mattered | Hard-delete restricted to strict supersession/dupes; everything else demote-only; archive fully searchable via Leg B |
| F7 | Incognito/housekeeping leakage | Dispatch-time incognito+housekeeping predicate at `_dispatch_scribe()` entry, independent of the completion block (B5) — covers mid-session triggers too |
| F8 | Scribe process self-triggers scribe/condense | `'housekeeping': True` on the scribe's synthetic session → `_log_agent_completion` early-returns at server.py:3482-3483 |
| F9 | Search returns the curated index itself | Search scoped to topic files + archive + managed region; curated pointer index excluded by construction |
| F10 | Scribe silently 100%-falls-back (bad path resolver / unflushed `.jsonl`) and hides behind the "never fail completion" `except: pass` | **Mandatory scribe-outcome telemetry (§8)** — counters for extracted / fell-back / no-transcript; staged-rollout gates read these (S1, S10) |
| F11 | Transcript exceeds scribe model context window | Chunked map-reduce by turn boundary (Leg A, normative) — never silently truncated input |
| F12 | csid never emitted (early crash) → no transcript path | Fall back to current stdout-tail `summary[:300]` — the one case transcript-only cannot cover (Leg A) |

---

## 6. Ship sequence (revised post-review)

**Step 0 — pre-impl spike (NARROWED — mostly de-risked by evidence
2026-05-15).** The original S1 assumption ("can MC reliably resolve+read the
CLI `.jsonl`?") is **empirically answered YES** for the v1 case: transcripts are
present, complete (1–2.8 MB for finished sessions), `_find_transcript_file`
resolves them via the verified `_`→`-` alt candidate, and production backfill
(server.py:3078) already reads them. Session-end v1 reads a *closed, complete*
file — no flush race. Only two **narrow** questions remain to confirm before
Leg A:
- **(spike-1, v1-relevant)** Does the CLI truncate large `tool_result` payloads
  in the on-disk transcript? (Only a tiny one was sampled.) Affects scribe
  *quality*, not viability — if truncated, note the ceiling; it's still
  strictly more than `log_lines` (which has zero tool_result).
- **(spike-2, Phase-2-only)** Mid-session flush *completeness* of a *live*
  `.jsonl`: is the last complete JSON line reliably readable while the agent is
  still writing? Gates the §3.A.MID flag only — NOT v1 session-end.

`claude_session_id` population is already handled (the csid-missing fallback in
Leg A covers the early-crash case). The spike no longer blocks the whole
sequence — only spike-1 gates Leg A quality expectations; spike-2 gates Phase 2.

1. **Leg 0 file-format contract + migration.** Sentinel-delimited managed
   region; idempotent migration of existing files. *Nothing else can ship
   correctly without this* — the mechanical floor and the scribe both depend on
   a parseable boundary that does not exist today.
2. **Mechanical floor re-key**, scoped to the managed region only (server.py:
   3505-3534 replacement). Kills the silent-loss class for managed growth.
   Standalone after Step 1; lowest risk.
3. **Leg A scribe (session-end only) WITH its v1 home** — flat dated entries
   into the managed region (B4 resolved normatively, no Leg C dependency).
   Replace `summary[:300]` at server.py:**3492**. Plus the B8 server.py:2295
   rewrite (same change-set — shipping the scribe without it is a corruption
   path). Scribe-outcome telemetry (§8) ships here, not later.
4. **Leg B parts 2+3+4 together** — search endpoint + **deterministic grep
   floor in `_build_agent_context`** + skill + priming line. The floor is not
   deferred; the failure-compensation pair (deterministic floor + probabilistic
   skill) ships intact (B3).
5. **Leg C model tier** — extend the condense prompt with the budget + the
   keep/fold/promote/demote decision; remove "keep last 20"; lower
   `condense_threshold_kb` to 20. The model tier also owns curated-region
   pointer-merge (the §1.3 case).
6. **Mid-session checkpoints (§3.A.MID) — built in this same delivery**, code
   landed but `scribe_checkpoint_kb=0` (disabled). Its watermark + partial-line
   contracts are designed and tested here; the flag is flipped only after Step 3
   telemetry validates session-end extraction quality. Not a separate release.
7. **Later, telemetry-gated:** Leg B v2 (server-side bge-m3 replacing grep in
   both the floor and the endpoint) when **archive-size** telemetry — not skill
   telemetry — shows grep precision degrading (S8 correction).

Rationale for the order: scribe-first (committee's original instinct) would
leave the line-cliff bug bleeding; floor-first was kept. But Leg 0 must precede
the floor (the floor has nothing safe to act on without the format contract —
B1). Leg A precedes the read skill so the skill debuts over an improving corpus,
*but* the deterministic read floor ships with the skill so the read side never
has a no-floor window. Mid-session is built last in the sequence but in the
*same* delivery — staged by config, not by release (D4).

**Explicitly not built:** the original session-start Haiku *router* (a second
cold-prompt relevance model — strictly dominated). NB: Leg B.3's deterministic
grep injection is *not* the router — no model, no relevance LLM.

---

## 7. Decisions

### Mid-session §3.A.MID — committee-revised 2026-05-17 (Legs 0/A/B/C + Fix A/B already shipped, committed `24a3af8`)

- **D5 — DISSOLVED:** the (i) crash-durability vs (ii) live-cross-agent-visibility
  fork no longer exists. The append-only correction (committee blocker #1)
  makes every checkpoint entry immediately visible to search/read-floor, so
  (ii) is delivered for free. No choice needed.
- **D6 — RESOLVED (principal took recommendation): fold the watermark into
  MEMORY.md.** A single atomic write eliminates the atomicity gap *and* the
  DATA_DIR-pollution-500 class. Accepted cost: a load-bearing Leg-0 contract —
  `_mem_split`/`_mem_compose` must preserve `<!-- clayrune:wm:… -->` markers,
  the floor must not relocate them, and the Leg C condense prompt must
  preserve them verbatim. Built *with* §3.A.MID. See §3.A.MID.
- **D7 — RESOLVED (principal took recommendation):** ship
  `scribe_checkpoint_enabled=false` (kill-switch) + `scribe_checkpoint_kb=0`;
  recommended cadence ≈ **8 KB** once enabled, after §8 telemetry validates
  session-end quality.

### Original D1–D4 — ALL RESOLVED (2026-05-15)

- **D1 — RESOLVED:** scribe writes flat dated entries into the managed region
  for v1; structured-delta + model-routed placement is a later iteration.
  Normative, no forward dependency.
- **D2 — RESOLVED:** `condense_threshold_kb` = **20**. Confirmed by principal.
- **D3 — RESOLVED:** no hard-delete except strict supersession/dupes; **no
  periodic archive-compaction pass**; archive grows unbounded by design and
  relies on Leg B search to stay useful. Confirmed by principal.
- **D4 — RESOLVED:** mid-session checkpointing is **in v1 scope, same build**,
  staged by config flag (`scribe_checkpoint_kb=0` until session-end telemetry
  validates extraction quality), NOT a separate release. The one hard
  prerequisite is the §3.A.MID crash-durable watermark + partial-line contract,
  designed and tested before the flag is enabled. Rationale: session-end-only
  does not serve the in-session-loss / long-session-extraction goal that
  motivated the redesign; deferring it to a "v2" would defer the half of the
  value the principal cares most about.

v1 (Legs 0/A/B/C + Fix A/B) shipped & committed `24a3af8`. **No open
decisions remain** — D5 dissolved, D6/D7 resolved. §3.A.MID is design-
complete and committee-hardened; the one remaining pre-build check is spike-2
(live-`.jsonl` flush completeness). It is **build-ready, default-off** — no
implementation until the principal asks.

---

## 8. Telemetry (mandatory, ships with Leg A — F10/S10)

Per-project counters, exposed via an MC status endpoint:
`scribe_extracted`, `scribe_fell_back` (with reason: `no_csid` /
`no_transcript` / `model_error` / `parse_empty`), `read_floor_hit`,
`skill_invoked`, `floor_relocations`, `curated_over_budget_events`. Without
this, a scribe silently falling back 100% (S1 path-resolver miss) is
indistinguishable from "working," and every staged-rollout gate in §6 is
ungated. The "never fail the completion flow" `except: pass` (server.py:
3539-3540) must increment a counter before swallowing.
