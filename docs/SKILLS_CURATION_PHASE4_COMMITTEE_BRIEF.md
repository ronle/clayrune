# Skills Curation Phase 4 (v1.1) — Committee Review Brief

> Status: **OPEN for review**. Authored 2026-05-27. The design under
> review is `docs/SKILLS_CURATION_PHASE4_SPEC.md` (DRAFT v1.1, 2026-05-27).
> This brief defines the four review seats, focus areas, evidence
> requirements, and required output format. Pattern mirrors the parent
> brief `docs/SKILLS_CURATION_COMMITTEE_BRIEF.md` (which produced the
> 2026-05-19 RATIFY-WITH-CONDITIONS verdict, parent design DRAFT v2).

---

## 1. Background — what's new in v1.1, why a fresh review

The parent design (`SKILLS_CURATION_DESIGN.md` DRAFT v2) was ratified
2026-05-19 with 11 must-fix-in-design conditions closed. The
load-bearing rules, the three-mode taxonomy, the schema reuse strategy,
and the lock/atomic-write discipline are **already ratified** — those are
not under review.

What v1.1 adds is **diagnostic-driven** and only touches **four new
surfaces**. Re-litigation of the parent design is out of scope.

### 1.1 What v1.1 changes vs the parent design

A 2026-05-27 diagnostic against `~/.claude/projects/**/*.jsonl` over the
9-day post-Phase-1-ship window:

| Signal | Count |
|---|---|
| Total sessions | 1,199 |
| Sessions where `mc-distill` was loaded | ~99% |
| **Proactive proposals surfaced** | **0** |
| Manual `/distill` invocations | **1** (the original 2026-05-19 Phase A test) |

The committee's parent soak-gate Condition #14 collected its calibration
evidence: zero. The in-session-recurrence ≥ 2 bar is structurally
incompatible with single-task sessions. v1.1's four changes:

1. **Build-order flip.** Phase 4 (silent Distiller) promoted ahead of
   Phase 2 (telemetry). Phase 2's substrate (`_skill_stats.json`,
   locks, fingerprint normalization, kill-switch gate) folds into
   Phase 4 implementation. PHASE4_SPEC §1, §3.
2. **Extraction-not-judgment prompt.** Phase 4 cheap-model call asks
   "what topics did this session touch?" — a narrow objective
   extraction. Cross-session aggregator does the judging. This is the
   *inverse* of Phase 1's "is this worth bottling?" framing. PHASE4_SPEC §3.1, §3.2.
3. **Cross-project candidates surface.** Proposal generation is
   per-project (blast-radius safety). A separate surface *notifies*
   when a fingerprint recurs across multiple projects but never
   auto-writes. Operator-level patterns findable without leaking. PHASE4_SPEC §3.6.
4. **Phase 1 softening (parallel SKILL.md edit).** `mc-distill` SKILL.md
   softened in place: dropped within-session-recurrence and
   once-per-session-cap; reversed "err toward asking less"
   disposition; kept natural-breakpoint, specificity, no-duplicates.
   Shipped ahead of Phase 4 backend code as a controlled experiment.
   PHASE4_SPEC §3.8.

### 1.2 What's NOT under review

- Load-bearing rules from parent design (auto-authored project-local
  only, MC owns / agent proposes / human promotes, etc.) — ratified.
- Three-mode taxonomy (`off` / `proposed` / `auto`) — ratified.
- Lock/semaphore reuse pattern — ratified.
- Conditions 1–11 from parent committee — already closed in parent v2.
- The `mc-distill` skill's *existence*, *promotion-on-instruction*
  procedure, and *reversal* procedure — ratified Phase A close.

### 1.3 Why review now

Same discipline as parent: no backend code lands until v1.1 is
ratified. The Distiller (`distiller.py`), endpoint surface, and
`_skill_stats.json` writers all sit behind this gate.

The Phase 1 softening already shipped to disk (commit `95b5aa8`) but
hasn't propagated (MC restart pending). That edit IS under review — if
the committee flags it, it can be reverted or further tuned before the
softened rules reach `~/.claude/skills/`.

---

## 2. The four seats

Mirrors parent brief structure. Each seat reviews v1.1 from one angle,
scoped to v1.1 deltas. Each produces a written assessment using the
format in §4.

### Seat 1 — Pattern integrity (v1.1 scope)

**Focus:**
- PHASE4_SPEC §3.1 — the "extraction not judgment" prompt design.
- PHASE4_SPEC §3.2 — cross-session aggregation logic (rolling window,
  recurrence threshold, fingerprint comparison).
- PHASE4_SPEC §3.3 — fingerprint stability (two-stage normalization,
  Stage 2 deterministic collapse).
- PHASE4_SPEC §3.6 — cross-project fingerprint consistency. Same
  pattern in two different projects: does it normalize to the same
  fingerprint? If not, the cross-project candidates surface produces
  false negatives.

**Core questions:**
- Is "what topics did this session touch?" narrow enough that cheap-
  model variance is acceptable? Or does it produce drifting topic
  vocabularies across sessions, breaking recurrence detection?
- Does the rolling window (60 days default, PHASE4_SPEC §3.2) interact
  cleanly with the recurrence counter? What happens to a fingerprint
  observed 3 times across 75 days vs. 3 times across 10 days?
- Cross-project fingerprint comparison: Stage 2 normalization is
  deterministic (lowercase + tokenize + stopword-strip + sort + hash).
  Same operator phrasing the "same" insight in two projects — does
  Stage 2 collapse them, or do project-specific vocabularies prevent
  it (e.g., "mc-distill" in one project vs. "the distillation skill"
  in another)?
- Recurrence threshold = 3 across distinct sessions. Is that
  defensible for v1, or should the committee push for 4-5 to be
  conservative on first soak?

**Block authority:** If the extraction prompt produces unstable
fingerprints at unacceptable rates, or cross-project comparison breaks
silently, BLOCK.

### Seat 2 — Agent behavior & proposal quality (v1.1 scope)

**Focus:**
- PHASE4_SPEC §1 / §3.8 — the Phase 1 softening, treated as a live
  controlled experiment.
- PHASE4_SPEC §3.4 — proposal generation from aggregated evidence.
- The build-order flip itself: is promoting Phase 4 ahead of Phase 2
  the right call, given the diagnostic evidence?

**Core questions:**
- Phase 1 softening dropped `recurrence ≥ 2 within session` and
  `once per session, max`. The rationale is "Phase 4 catches what
  Phase 1 misses." But Phase 4 isn't built yet — the softening
  ships first. Is there a 2-3 week window where Phase 1 is soft AND
  Phase 4 doesn't exist? What's the worst case for noise during that
  window?
- The "if you noticed something worth bottling at a natural breakpoint,
  say so" disposition is a deliberate reversal. Adversarial scenario:
  agent in a debug session, frustrated, surfaces a vague "we should
  remember not to do this" proposal at the post-debug breakpoint.
  Does the specificity-bar catch that?
- §3.4 proposal generation uses the aggregated evidence (N concrete
  observations from N sessions). What stops the cheap model from
  generating bland summaries that pass the gate but produce
  low-utility skills?
- Build-order flip: Phase 4 ships before Phase 6 (dispatch hint). So
  proposals get written to `_proposed/` but skills, even if promoted,
  may not surface at the right moment until Phase 6 lands. Does that
  ordering create a trough where the loop produces output but no
  observable benefit?

**Block authority:** If softening will produce noise at scale, the
build-order flip creates an unrecoverable trough, or §3.4 proposal
quality is structurally low, BLOCK.

### Seat 3 — Concurrency & lifecycle (v1.1 scope)

**Focus:**
- PHASE4_SPEC §3.5 — `_distiller_should_proceed` kill-switch gate.
- PHASE4_SPEC §3.7 — suppression marker writes from `mc-distill` (CC
  process) and reads from silent Distiller (MC server process). The
  shared `_get_skill_stats_lock(project_id)`.
- The parallel-Scribe execution at `_write_session_memory`
  (server.py:4699). Distiller fires in parallel; both touch the same
  `.jsonl` but write to different domains. Isolation guarantees?

**Core questions:**
- §3.5 enumerates the kill-switch entry points (session-end, future
  auto-mode promote, future dispatch hint). The unit test asserts each
  passes through `_distiller_should_proceed`. Is the enumeration
  complete? Specifically: does the future `record-push` HTTP endpoint
  also gate on the switch? It's not a Distiller fire path per se, but
  it writes to `_skill_stats.json`.
- §3.7 says the silent Distiller honors `Later` markers and the
  `wait_until_recurrence: <N+1>` stub. Race: user says `Later` at
  t=0; the same session ends at t+10s; Distiller fires on the same
  transcript at t+10s. Does Distiller see the `Later` marker the push
  wrote? The parent design's Cond 6 v2 specified shared-lock-domain;
  v1.1 inherits this — does the v1.1 implementation actually use it?
- Parallel-Scribe: Scribe writes MEMORY.md under
  `_get_mem_write_lock`. Distiller writes `_skill_stats.json` and
  `_proposed/<sid>/SKILL.md` under `_get_skill_stats_lock`. Two
  different lock domains. If Scribe's cheap-model call fails, does
  Distiller still fire? If Distiller's cheap-model call fails, does
  Scribe's memory write still complete? Inversion of best-effort
  posture would be a problem.
- Hard-kill mid-distillation: parent design says Distiller is
  best-effort, never load-bearing. v1.1 inherits this. But what if MC
  is killed between session-signal extraction and proposal
  generation? `_skill_stats.json` would have new signals but no
  proposal. Is the rolling-window aggregation idempotent enough that
  the next session catches up?

**Block authority:** If concurrent execution can lose a suppression
marker, corrupt `_skill_stats.json`, or invert the best-effort posture
(Distiller failure breaks Scribe), BLOCK.

### Seat 4 — Config, ops, rollback, cost (v1.1 scope)

**Focus:**
- PHASE4_SPEC §3.6 — the cross-project candidates surface (notify, do
  not auto-write).
- PHASE4_SPEC §5 — minimum viable cut + deliberately deferred items.
  Especially the deferred cost-cap structured log + endpoint.
- DATA_DIR exclusion regression test (Cond 13 v2, inherited).
- Rollback story for the Phase 1 softening, the Phase 4 backend, and
  the cross-project surface.

**Core questions:**
- §3.6 cross-project surface is *visibility*, not auto-write. Good.
  But what's the UI affordance? PHASE4_SPEC doesn't specify. A
  notification badge in the Skills panel? A row in the monthly audit?
  Both? If unspecified, the surface could ship and never be visible.
- §5 defers the cost-cap structured log + endpoint to Phase 5.
  Parent design Cond 9 v2 set the default at 100k tokens/project/day
  and required cap-hit observability with structured log. v1.1 says
  "stub the counter now, ship the cap-hit endpoint with Phase 5."
  Is that safe? If a runaway loop hits the cap in v1, will it be
  detectable without the structured log + endpoint?
- DATA_DIR regression test (Cond 13, must-fix-in-implementation): v1.1
  inherits the requirement. Where in `tests/` does it land? The
  current `tests/` layout — does it have a `test_load_projects_*`
  module already, or does this introduce a new file?
- Rollback for Phase 1 softening: source file is at
  `data/skills/builtin/mc-distill/SKILL.md`. Reverting the softening
  is a `git revert` of commit `95b5aa8`. But after MC restart
  propagates the softened version to `~/.claude/skills/`, does
  reverting the source file actually unwind the propagated copy, or
  does the user need to manually delete it?
- Rollback for cross-project surface: if it produces too many false
  positives ("operator-level pattern: promote?" 50 times), is there a
  kill switch? The parent design's `distiller_enabled_global` covers
  the Distiller but does it cover the surface?

**Block authority:** If the cross-project surface lacks a defined UI
affordance, the deferred cost-cap creates a silent runaway risk,
DATA_DIR regression test is unenforced, or Phase 1 softening rollback
is ambiguous, BLOCK.

---

## 3. Review rules

- **Each seat reads `docs/SKILLS_CURATION_PHASE4_SPEC.md` end to
  end.** This brief sets focus; the spec is the artifact under review.
- **Each seat reads `docs/SKILLS_CURATION_DESIGN.md`** (parent design)
  to ground v1.1 changes against the ratified load-bearing rules and
  Conditions 1–11.
- **Each seat reads `data/skills/builtin/mc-distill/SKILL.md`** in its
  current post-softening state (commit `95b5aa8`).
- **Each seat reads `docs/SKILLS_CURATION_COMMITTEE_BRIEF.md`** for the
  required output format (§4 below repeats it for convenience).
- **No cross-seat communication during review.** Each seat works
  independently. Synthesis happens after all four assessments arrive.
- **Evidence over assertion.** Claims must reference line numbers in
  PHASE4_SPEC, the parent design, or the diagnostic findings (§1.1
  here). "This might be brittle" is not actionable. "Under condition
  X, line Y of the spec produces failure Z" is.
- **Scope discipline.** Do not re-litigate the parent design.
  Conditions 1–11 are closed. If a v1.1 detail interacts with a parent
  condition, cite it and move on.
- **Specific failure paths over abstract concerns.** Required.

---

## 4. Required output (per seat)

Each seat writes a single markdown file to
`docs/_committee/SKILLS_CURATION_PHASE4_seat<N>_<topic>.md`:

```markdown
## Seat <N> — <name> — <DECISION>

**Decision:** RATIFY / RATIFY-WITH-CONDITIONS / BLOCK

**Summary (1-2 sentences):** <high-level assessment>

### Blockers (if any)
1. <Specific issue with line reference in PHASE4_SPEC. What fails, under what conditions.>
   - Fix required: <concrete change to the spec before backend code lands>

### Conditions (RATIFY-WITH-CONDITIONS only)
Numbered, each with:
- **Condition N (must-fix-in-design / must-fix-in-implementation / soak-gate):**
- **Why:** <failure mode this prevents>
- **Proposed fix:** <specific change>
- **Gate phase:** which deliverable this gates (v1.1 spec ratification / Phase 4 backend / cross-project surface / Phase 5)

### Ratifications
- <Items v1.1 got right, worth preserving across future revisions.>

### Out-of-scope but flagged
- <Concerns adjacent to your seat's focus, for the synthesizer to route.>
```

---

## 5. Synthesis (post-review, before backend)

After all four seats report:

1. Collect all four assessments verbatim into a new section appended to
   `docs/SKILLS_CURATION_PHASE4_SPEC.md`:
   `## Committee review (2026-05-27) — <overall decision>`
2. Synthesize:
   - **Overall decision** is the *strictest* of the four (one BLOCK =
     overall BLOCK).
   - **Blockers** must be fixed in the spec before backend code lands.
   - **Conditions split:** must-fix-in-design (block spec v1.2 issuance),
     must-fix-in-implementation (block specific deliverables),
     soak-gate (block default-flip on the relevant feature).
3. If overall = RATIFY or RATIFY-WITH-CONDITIONS:
   - Address blockers (if any) by editing the spec to v1.2.
   - Mark spec status: `DRAFT v1.2 (post-committee-review 2026-05-27)`.
   - Phase 4 backend build is now unblocked.
4. If overall = BLOCK:
   - Address blockers, revise to v1.2, re-launch the committee.

---

## 6. Dispatch — how to run this

**Selected: four parallel Agent calls, one per seat.** Each agent
focuses on their seat only, reads the spec + parent design + softened
mc-distill SKILL.md + this brief's §4 format, and writes its
assessment to its dedicated file in `docs/_committee/`.

`docs/_committee/` is scratch — assessments fold into the spec on
synthesis and the directory can be cleaned up afterward. Per parent
brief convention, the `_` prefix signals "do not commit standalone."
