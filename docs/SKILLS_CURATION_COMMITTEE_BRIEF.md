# Skills Curation — Committee Review Brief

> Status: **OPEN for review**. Authored 2026-05-19 (post-Phase A close).
> The design under review is `docs/SKILLS_CURATION_DESIGN.md` (476 lines).
> This brief defines the four review seats, focus areas, evidence
> requirements, and required output format. Pattern mirrors the
> Memory System (`MEMORY_SYSTEM_SPEC.md` §3.A.MID — post-committee
> v2) and Leg C (`CONDENSE_STRUCTURED_DESIGN.md` §Committee review
> 2026-05-18 — RATIFY-WITH-CONDITIONS).

---

## 1. Background — why a committee, why now

**Phase A complete** as of 2026-05-19:

- **Step 1 shipped:** `mc-distill` skill (158 lines) at
  `data/skills/builtin/mc-distill/SKILL.md`. Auto-installs globally via
  `_install_builtin_skills()` on MC startup.
- **Visibility bug resolved (design-doc open item #9):** root cause was
  description-length overflow (920 → 519 chars). Validated across ~30+
  post-restart sessions in `mission-control` project — mc-distill
  present in every `skill_listing` attachment. Other projects with
  curated loadouts (DayTrading-engulfing, FL3-V2) correctly exclude
  it by project config; that is not a regression.
- **End-to-end validated:** mc-distill produced a real, high-quality
  proposal on 2026-05-19 (`frontend-render-hang-diagnostic` — 70-line
  SKILL.md with paste-safe DOM probe, 4-way diagnostic table, loopback-
  curl-≠-real-network discriminator). Proposal saved correctly to
  `data/skills/_proposed/2026-05-19T20-50/SKILL.md`. The "MC owns,
  agent proposes, human promotes" rule worked end-to-end.

**Why review now:** Phase A close is the right cut to harden the design
*before* any backend code (Distiller, telemetry, `_proposed/` CRUD,
audit extension, dispatch hint) lands. Same discipline that produced
the Memory System v2 spec and Leg C ratification. **No backend code
lands until conditions are closed.**

**Document under review:** `docs/SKILLS_CURATION_DESIGN.md` — read it
end to end. Particularly the 9 open items (one resolved, one Phase-A-
closed, seven open) and the load-bearing-rules section.

---

## 2. The four seats

Mirrors the Memory System / Leg C committee structure. Each seat reviews
the design from one angle. Seats are independent; no cross-talk during
review. Each produces a written assessment using the format in §4.

### Seat 1 — Pattern integrity

**Scope:**
- Open item #1 — Pattern fingerprint stability.
- Open item #2 — UPDATE.md schema rigor.
- Open item #8 — PATCH.md schema variant for external-agent patches.
- Schema invariants across SKILL.md / UPDATE.md / PATCH.md.

**Core questions:**
- Is the proposed fingerprint approach (cheap-model canonical phrase →
  hash) stable enough to support a recurrence counter? What's the
  collision rate likely to look like? Failure mode if a pattern
  fingerprint drifts across sessions?
- Is UPDATE.md's "target_skill name + prose + unified diff" expressive
  enough? Edge cases: renaming a skill, multiple skills referenced,
  cross-file changes.
- PATCH.md's `rationale` field says it MUST cite report dates + query
  results. Is that enforceable? What stops a future agent from writing
  vague "based on observed patterns" rationale and slipping through?
- `backtest_plan` field — is the `unable_to_backtest: <reason>` escape
  hatch a liability? Would it become the default?

**Block authority:** If schema is too loose, schema breaks the
provenance/audit story, or fingerprint collides at unacceptable rates,
this seat can BLOCK.

### Seat 2 — Agent behavior & proposal quality

**Scope:**
- Conversational push annoyance bar (open item #7 focus area).
- Proactive trigger calibration (`mc-distill` SKILL.md hard rules).
- Auto-mode skill-quality decay (open item #7 focus area).
- The "human promotes" rule + the new "agent executes promotion on
  explicit user instruction" clarification (load-bearing-rules section).
- Promotion-on-instruction safeguards in `mc-distill`'s new section.

**Core questions:**
- The proactive bar (recurrence ≥ 2 + natural breakpoint + specificity)
  produced ONE proposal across ~30+ post-restart sessions, and it was a
  good one. Is that a calibrated bar or an under-firing one?
- Auto-mode rollback: if the agent self-promotes a bad skill in
  `auto` mode, what's the fastest path to revert? Is the monthly audit
  surfaced fast enough, or does this need an in-week trigger?
- The new "agent executes promotion on explicit instruction" clarification
  — is the list of YES/NO triggers (`yes promote`, `ship it` etc.)
  complete? Adversarial phrasing the agent might misinterpret?
- `Later` and `No` semantics — are they reversible across sessions, or
  is a "No" today immutably suppressing the same pattern in next month's
  session? (Open item #4 + suppression-marker contract.)

**Block authority:** If the bar will produce noise at scale, the
promotion-on-instruction rule has a clear adversarial-ambiguity path,
or auto-mode rollback isn't fast enough, this seat can BLOCK.

### Seat 3 — Concurrency & lifecycle

**Scope:**
- Race between conversational-push `Later` and the silent Distiller at
  session end (open item #7 focus area).
- Suppression marker contract in `_skill_stats.json` (load-bearing
  rules).
- Per-project leaf lock reuse (`_get_mem_write_lock` analog).
- Bounded semaphore reuse (per-project cap=2 from Step 6).
- Best-effort posture (Distiller is not load-bearing).
- Hard-kill / mid-flight handling (Distiller drops cleanly vs. Scribe's
  Fix-B reconciler).

**Core questions:**
- Push says `Later`, session ends in 10s, Distiller fires on same
  transcript — does Distiller see the suppression marker the push
  wrote? Order of operations matters: marker write must precede
  Distiller dispatch.
- What if push fires, user says `Yes`, agent writes proposal to
  `_proposed/`, session crashes before reporting back? Is the
  proposal orphaned or auditable?
- Two parallel sessions in the same project both fire proactively at
  similar times — leaf lock serializes them, but does the second one
  observe the first one's suppression marker and stay silent, or does
  it fire-and-be-suppressed? Defensible either way; needs explicit
  choice.
- Distiller runs parallel to Scribe at session end. Are both writes
  (Scribe → MEMORY.md, Distiller → `_proposed/` + `_skill_stats.json`)
  isolated such that one failing doesn't poison the other?

**Block authority:** If concurrent execution can lose a proposal,
corrupt `_skill_stats.json`, or leak suppression state across
sessions, this seat can BLOCK.

### Seat 4 — Config, ops, rollback, cost

**Scope:**
- Cost cap calibration (open item #3).
- Three-mode config (`off` / `proposed` / `auto`) + kill switches
  (`distiller_enabled_global`, `skill_hint_enabled`).
- DATA_DIR exclusion rule for `_skill_stats.json` (load-bearing).
- Rollback story per phase (mode flip back, kill-switch global, undo
  promotions).
- Telemetry surface (`/distiller-stats`).
- Audit-extension checklist scope (which signals make it monthly).

**Core questions:**
- 5,000 tokens/project/day cap — is that right? For a Mode-B project
  with 100+ sessions/day (Scribe-heavy mode), is the per-call budget
  too tight? Too loose for a quiet project?
- `distiller_enabled_global=false` kill switch — is there a single
  place to verify it ACTUALLY stops every Distiller call path? Same
  rigor as Scribe's `scribe_enabled` switch.
- `auto`-mode promotion writes directly to `<project>/.claude/skills/`.
  How does the user undo? Is "delete the SKILL.md file" sufficient, or
  is there state in `_skill_stats.json` that needs cleanup too?
- The audit extension's "skills with zero invocation in 90 days" rule —
  what's the action? Auto-archive? Surface for review? Should the
  90-day threshold be per-skill-type (manual vs. distilled vs.
  auto-authored)?
- DATA_DIR exclusion — `_skill_stats.json` MUST be suffix-excluded in
  `load_projects()`. Is there a regression test for this once the file
  lands? (Pattern: `_agent_log.json`, `_scribe_stats.json` precedents.)

**Block authority:** If the cost cap will be hit weekly under realistic
load, kill switches don't actually stop everything, rollback is
ambiguous, or DATA_DIR rule isn't enforced by test, this seat can
BLOCK.

---

## 3. Review rules

- **Each seat reads `docs/SKILLS_CURATION_DESIGN.md` end to end.**
  Don't review based on this brief alone. The brief sets focus; the
  design doc is the artifact under review.
- **Also read `data/skills/builtin/mc-distill/SKILL.md`** for Phase A
  empirical context (esp. the new "Promotion on explicit user
  instruction" section).
- **Also read the live proposal** at
  `data/skills/_proposed/2026-05-19T20-50/SKILL.md` for concrete
  evidence of what the system produces.
- **No cross-seat communication during review.** Each seat works
  independently. Synthesis happens after all four assessments arrive.
- **Evidence over assertion.** Claims should reference line numbers in
  the design doc, prior `MEMORY_SYSTEM_SPEC.md` or
  `CONDENSE_STRUCTURED_DESIGN.md` precedents, or the empirical
  Phase A data (the validated proposal).
- **Specific failure paths over abstract concerns.** "This might be
  brittle" is not actionable. "Under condition X, line Y of the
  design produces failure Z" is.

---

## 4. Required output (per seat)

Each seat writes a single markdown section in this shape:

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
- **Gate phase:** which build-order step (1-7) this gates

### Ratifications
- <Items the design got right, worth preserving across future changes.>
  Helps prevent regression in future revisions.

### Out-of-scope but flagged
- <Concerns adjacent to but outside this seat's focus, for the
  synthesizer to route.>
```

---

## 5. Synthesis (post-review, before backend)

After all four seats report:

1. Collect all four assessments verbatim into a new section appended to
   `docs/SKILLS_CURATION_DESIGN.md`:
   `## Committee review (2026-05-19) — <overall decision>`
2. Synthesize:
   - **Overall decision** is the *strictest* of the four (one BLOCK =
     overall BLOCK).
   - **Blockers must be fixed in the design** before any backend code
     lands.
   - **Conditions split into:** must-fix-in-design (block design v2),
     must-fix-in-implementation (block specific build-order phases),
     soak-gate (block default-flip but not the commit).
3. If overall = RATIFY or RATIFY-WITH-CONDITIONS:
   - Address blockers (if any) by editing the design doc.
   - Re-mark the doc status to `DRAFT v2 (post-committee-review)`.
   - The design is now stable. Build-order phase 2 (telemetry) can
     start.
4. If overall = BLOCK:
   - Address the blockers, revise the design, re-launch the committee.

---

## 6. Dispatch — how to actually run this

**Recommended:** four parallel CR agent sessions, one per seat, each
given this brief + instructed to focus on their seat only. Output goes
to four files:

- `docs/_committee/SKILLS_CURATION_seat1_pattern.md`
- `docs/_committee/SKILLS_CURATION_seat2_agent.md`
- `docs/_committee/SKILLS_CURATION_seat3_concurrency.md`
- `docs/_committee/SKILLS_CURATION_seat4_ops.md`

(Note the `_committee` directory uses the `_` prefix per DATA_DIR
convention — these are scratch artifacts that get folded into the
design doc on synthesis, not committed standalone.)

**Alternative:** spawn a HIVEMIND with this brief as the orchestrator's
task and the four seats as worker briefs. Same output structure;
orchestrator handles synthesis.

**Single-session alternative:** one CR agent walks all four seats
sequentially with this brief, producing one combined document. Less
ideal — cross-seat priming compromises independence — but acceptable if
HIVEMIND or parallel dispatch is not available.

After synthesis, append the result to `docs/SKILLS_CURATION_DESIGN.md`
under a new `## Committee review (2026-05-19)` heading and commit the
whole bundle.
