# Skills Curation — Phase 4 v2 Committee Review Brief

> Status: **OPEN for review**. Authored 2026-05-27. The design under
> review is `docs/SKILLS_CURATION_PHASE4_SPEC_V2.md` (1163 lines).
> Pattern mirrors the v1.1 committee brief
> (`docs/SKILLS_CURATION_COMMITTEE_BRIEF.md`, 2026-05-19) — same four
> seats, same output format, same synthesis discipline.

---

## 1. Background — why a v2, why review now

**v1.1 was scoped against the narrower "cross-session pattern
detection on prompt-based skills, per-project default" framing.** After
v1.1's committee returned RATIFY-WITH-CONDITIONS (no blockers, 14
must-fix-in-design + 4 must-fix-in-implementation + 2 soak-gate
conditions), Ron pushed back: "we're not defining the right things."

A working definition of "learning" was then locked in conversation
(see `~/.claude/projects/.../memory/decision_learning_definition.md`):

> **Learning is when the agent's effective behavior changes over time,
> driven by experience, without the human having to type the change.**

This definition is structurally broader than v1.1's framing. The
mechanism plumbing v1.1 specified is still right; the scope, structure,
and artifact-type layer needs a redesign. **v2 is that redesign.**

**Why review now:** v2 introduces meaningful new surface (four artifact
kinds, cross-project default, closed-vocabulary fingerprinting, user-
preference learning, external-exploration retention). v1.1's
ratification covered the narrower framing only. No backend code lands
until v2 clears committee, same discipline as parent design v2
(`docs/SKILLS_CURATION_DESIGN.md`) and Memory System SPEC §3.A.MID.

**Build-order context:** v2 is gate #3 (committee review). Gate #1
(fix condense — 58 timeouts + 48 errors per 2026-05-23 diagnostic) is
parallel-independent of this review. **No backend code lands until
BOTH gates close** AND the v2 build (gate #4) lands.

**Document under review:** `docs/SKILLS_CURATION_PHASE4_SPEC_V2.md` —
read it end to end. Particularly:

- §1 The five shifts vs v1.1
- §3 The four-artifact model
- §4 Mechanism (extraction, aggregation, kill-switch, lock, concurrency)
- §5 Closed-vocabulary fingerprint scheme (including the actual verb/
  noun/modifier lists)
- §6 mc-distill v2 reset (once-per-session-cap RESTORATION + tone fix)
- §9 The three settled open questions
- §10 Inheritance map from v1.1 committee (14 design + 4 implementation
  + 2 soak-gate conditions — v2's status on each)
- §11 Per-project vs global config classification
- §12 New open questions for v2 committee

**Also read** (companion context, do not re-review):

- `~/.claude/projects/.../memory/decision_learning_definition.md` — the
  locked definition v2 is designed against
- `docs/SKILLS_CURATION_PHASE4_SPEC.md` — v1.1, reference-only;
  understand what changed and why
- `docs/SKILLS_CURATION_DESIGN.md` — parent design + Conditions 1–11
  (still authoritative for load-bearing rules)
- `data/skills/builtin/mc-distill/SKILL.md` — current v1.1-softened
  state; v2 §6 specifies a clean revision
- `docs/_committee/SKILLS_CURATION_PHASE4_seat<N>_*.md` — v1.1 committee
  assessments. v2 §10 explicitly maps each v1.1 condition to its v2
  status; verify v2's claimed closures actually close them

---

## 2. The four seats

Same four seats as v1.1 committee. Below: scope and v2-specific
focus areas. **No cross-seat communication during review.**

### Seat 1 — Pattern integrity & vocabulary

**Scope:**
- §5 closed-vocabulary fingerprint scheme (verbs/nouns/modifiers
  lists, normalization function, near-miss telemetry).
- §4.1 extraction prompt with granularity bounds + K cap +
  closed-vocab constraint.
- §4.2 aggregation logic (READ-TIME window filtering,
  `fingerprints_near_threshold` telemetry).
- §5.4 cross-project fingerprint stability under closed vocab.
- v2 §10 closure of v1.1 Cond 1 (Stage 2 too narrow) and Cond 4
  (cross-project surface inheriting variance).

**Core questions:**
- Does the closed vocabulary in §5.1 actually cover the work this
  project does? Verb list ~70 items, noun list ~100 items, modifier
  list ~20 items. Will `vocabulary_miss` rate be <5% on the diagnostic
  corpus, or will the vocab need pre-shipping enrichment?
- The closed-vocab approach claims to collapse the synonym variance
  v1.1's bag-of-tokens missed (`use` vs `prefer` etc.). Does
  upstream constraint actually achieve this, or does it just push the
  variance into "which vocab term does the cheap model pick today"?
- The §5.4 claim that cross-project fingerprints converge under
  closed vocab — pressure-test it. Mock two extractions for the same
  operator pattern phrased in two projects.
- §4.2 says read-time filtering + append-only. Storage growth bound?
- The `fingerprint_near_miss` telemetry (§5.3): would top-3-closest
  by Levenshtein actually catch the failures it's meant to catch?

**Block authority:** If closed vocab is structurally too narrow (or
too wide), fingerprint stability remains broken under realistic
cheap-model variance, or v1.1 Cond 1's failure mode is not actually
closed.

### Seat 2 — Agent behavior & proposal quality across four artifact kinds

**Scope:**
- §4.3 EXPLORATION.md generation (single-shot retention, no
  recurrence gate, "what worked / what didn't" structure).
- §4.4 PREFERENCE.md generation (recurrence-gated user-preference
  observation; promotion target ambiguity).
- §4.5 SKILL.md / UPDATE.md generation (5 required prompt elements,
  REFUSE path).
- §4.7 cross-artifact suppression keying (`No` to SKILL.md also
  suppresses EXPLORATION.md and PREFERENCE.md for same fingerprint).
- §6 mc-distill v2 reset: once-per-session-cap RESTORATION,
  strengthened specificity bar, §Tone removal.
- v2 §10 closure of v1.1 Cond 5 (proposal-generation prompt
  unspecified) and Cond 6 (softening conflated three changes).

**Core questions:**
- v2 restores the once-per-session cap that v1.1 dropped. Is this the
  right call given the new disposition language? Specifically: does
  the cap + the strengthened specificity bar + the natural-breakpoint
  rule jointly produce a sustainable proactive surface, or does the
  cap re-collapse fire rate to zero?
- §4.4 PREFERENCE.md proposes feedback memory or project CLAUDE.md
  promotion targets. Will the generated body be promotable verbatim,
  or will every promotion require a human rewrite (defeating the
  point)?
- Cross-artifact suppression keyed on fingerprint alone: "No to skill
  also means no to exploration/preference." Right user mental model,
  or does it conflate distinct artifact decisions?
- EXPLORATION.md has no recurrence gate. Risk class: a chatty session
  that "explored" 10 sub-questions emits 10 EXPLORATION.md proposals.
  Is the K=3 cap on topics enough, or does exploration need its own
  cap?
- The strengthened specificity bar (§6.2 pattern-bound-vs-session-
  bound test): does the test actually filter the adversarial post-debug
  failure mode v1.1 Seat 2 flagged?

**Block authority:** If a proposal-generation prompt produces
unreviewable content at scale, cross-artifact suppression conflates
genuinely distinct user decisions, or §6's mc-distill revision still
isn't internally consistent.

### Seat 3 — Concurrency, lifecycle, atomicity

**Scope:**
- §4.6 unified kill-switch with 6 enumerated entry points
  (including the new `cross_project_aggregate` and the persisting
  `record_push`).
- §4.7 shared-lock contract: reads AND writes go through
  `_get_skill_stats_lock`. RMW span specified.
- §4.8 daemon-thread dispatch; signal-commits-before-proposal-generate
  ordering; hard-kill recovery via idempotent re-aggregation.
- §4.9 atomic writes for `_skill_stats.json` (closes v1.1 Cond 16).
- Cross-project aggregation walking `data/projects/*/_skill_stats.json`
  — what's the lock discipline when walking N projects' files?
- v2 §10 closure of v1.1 Cond 7 (kill-switch enumeration), Cond 8
  (lock language writers-only), Cond 9 (threaded vs sequential),
  Cond 10 (hard-kill recovery order).

**Core questions:**
- Cross-project aggregation reads all projects' `_skill_stats.json`.
  Spec says "existing files only — no new global index, no new lock
  domain." Concrete: does the walker acquire per-project locks
  serially (slow but correct), or read lock-free and accept
  occasional stale reads (fast but introduces a race)? Spec doesn't
  say. What's the right call?
- The 6-entry-point kill-switch enumeration unit test (§4.6): is the
  enumeration discoverable enough that a future contributor adding a
  7th entry point will know they need to register it? Or will it rot?
- §4.8 says daemon thread for Distiller. Two daemon threads at
  session end (Scribe + Distiller). Both touch the per-project
  semaphore (parent design "BoundedSemaphore cap=2"). Is the cap
  still safe with one slot for Scribe + one slot for Distiller, or
  does v2 need cap=3?
- The hard-kill recovery story (§4.8 Option A): signal commits
  before proposal-generate. What if proposal-generate completes
  successfully but the next-session re-aggregation also fires? Do
  we get two proposals for the same fingerprint?
- Atomic-write of `_skill_stats.json` (§4.9): the file grows
  append-only. At some volume does the full-file rewrite cost
  matter? When does it want bounded compaction?

**Block authority:** If concurrent execution can lose a proposal,
corrupt `_skill_stats.json`, leak suppression state, or block Scribe.

### Seat 4 — Config, ops, rollback, cost, scope-tag UX

**Scope:**
- §11 per-project vs global config-key classification
  (closes v1.1 Cond 14).
- §8 cost-cap structured log + counter shipped in v1 (closes v1.1
  Cond 15 — overrides v1.1's defer-to-Phase-5).
- §6.3 mc-distill rollback paths (Hot / Cold / Hard).
- §1.5 dual-checkpoint scope tagging — the promotion UI affordance.
- §3 unified `_proposed/` directory structure
  (`global/` vs `<project_id>/` staging).
- `GET /api/distiller/_proposed` (lists across all projects + global
  staging).
- v2 §10 closure of v1.1 Cond 11 (cross-project UI affordance),
  Cond 12 (Phase 1 softening rollback), Cond 13 (cross-project
  kill switch), Cond 14 (config-key scope).

**Core questions:**
- The dual-checkpoint scope tagging (extraction-tag + promotion-tag).
  Where does the promotion UI live? Spec says "monthly audit + Skills
  panel." Is that concrete enough, or does v2 need to specify the
  exact UI surface before backend lands?
- v2 §11 splits the 4 config keys cleanly into global vs per-project.
  Does the split match operator intent? Specifically:
  `distiller_min_recurrence` is per-project — but a cross-project
  pattern (§4.2) uses the per-project threshold for each project's
  contribution. Could that produce surprising behavior if two
  projects use different thresholds?
- §6.3 Path 3 (Hard revert, `--force`) is deferred. The brief calls
  it a backlog item. Acceptable, or does it need to ship with v2?
- Cost-cap structured log: spec says ship in v1. Is the log shape
  sufficient (`distiller_cost_cap_hit:<project_id>:<date>:<tokens>`)
  for operator action, or does it need to include the cap value?
- DATA_DIR regression test placement (`tests/test_load_projects_sidecar_exclusions.py`):
  is that the right file name? Will future contributors find it
  when adding the next sidecar?
- §3 unified `_proposed/` layout has both `global/` and `<project_id>/`
  subdirs. Does this collide with existing `_proposed/` content
  (`data/skills/_proposed/2026-05-19T20-50/` — the Phase A
  validation)? Migration path?

**Block authority:** If config classification is wrong, rollback
discipline isn't sustainable, scope-tag UX is unbuildable, or DATA_DIR
exclusion isn't tested.

---

## 3. Review rules

- **Each seat reads `docs/SKILLS_CURATION_PHASE4_SPEC_V2.md` end to end.**
  Don't review based on this brief alone.
- **Each seat reads the locked learning definition**
  (`<project memory dir>/decision_learning_definition.md`).
  v2's design is judged against this definition.
- **Each seat skims their corresponding v1.1 seat assessment**
  (`docs/_committee/SKILLS_CURATION_PHASE4_seat<N>_*.md`) to verify v2
  §10's closure claims actually close their seat's previous conditions.
- **Also read parent design** (`docs/SKILLS_CURATION_DESIGN.md`) for
  load-bearing rules and parent Conditions 1–11 (still authoritative).
- **No cross-seat communication during review.**
- **Evidence over assertion.** Reference line numbers in the v2 spec,
  precedents in prior specs, or the locked definition.
- **Specific failure paths over abstract concerns.**

---

## 4. Required output (per seat)

Write a single markdown section in this shape (same as v1.1):

```markdown
## Seat <N> — <name> — <DECISION>

**Decision:** RATIFY / RATIFY-WITH-CONDITIONS / BLOCK

**Summary (1-2 sentences):** <high-level assessment>

### Blockers (if any)
1. <Specific issue with line reference. What fails, under what conditions.>
   - Fix required: <concrete change to design before backend code lands>

### Conditions (RATIFY-WITH-CONDITIONS only)
Numbered, each with:
- **Condition N (must-fix-in-design / must-fix-in-implementation / soak-gate):**
- **Why:** <failure mode this prevents>
- **Proposed fix:** <specific change>
- **Gate phase:** which v2 build step this gates

### Ratifications
- <Items v2 got right, worth preserving across future changes.>

### Out-of-scope but flagged
- <Concerns adjacent to but outside this seat's focus, for the
  synthesizer to route.>
```

Write the assessment to:
`docs/_committee/SKILLS_CURATION_PHASE4_V2_seat<N>_<name>.md`

(`<name>` = `pattern` / `agent` / `concurrency` / `ops` matching the
seat number per v1.1 convention.)

---

## 5. Synthesis (post-review, before backend)

After all four seats report:

1. Collect all four assessments verbatim into a new section appended
   to `docs/SKILLS_CURATION_PHASE4_SPEC_V2.md`:
   `## Committee review (2026-05-27) — <overall decision>`
2. Overall decision = strictest of the four (one BLOCK = overall BLOCK).
3. Conditions split into: must-fix-in-design (block v2 → v2.1 revision),
   must-fix-in-implementation (block specific build-order phases),
   soak-gate (block default-flip but not the commit).
4. If overall = RATIFY or RATIFY-WITH-CONDITIONS:
   - Address blockers by editing the v2 spec → v2.1.
   - Re-mark doc status to `DRAFT v2.1 (post-committee-review)`.
   - The design is stable. Build (gate #4) can start once condense
     (gate #1) is also closed.
5. If overall = BLOCK:
   - Address the blockers, revise the design, re-launch the committee.

---

## 6. Dispatch — how to actually run this

**Recommended:** four parallel CR agent sessions, one per seat, each
given this brief + instructed to focus on their seat only. Output goes
to four files under `docs/_committee/`:

- `SKILLS_CURATION_PHASE4_V2_seat1_pattern.md`
- `SKILLS_CURATION_PHASE4_V2_seat2_agent.md`
- `SKILLS_CURATION_PHASE4_V2_seat3_concurrency.md`
- `SKILLS_CURATION_PHASE4_V2_seat4_ops.md`

After all four seats report, synthesize and append to v2 spec under
`## Committee review (2026-05-27)`.
