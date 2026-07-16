# Auto-Scope Project-Local Promotion (Phase 5 `auto` mode) — Committee Review Brief

> Status: **OPEN for review**. Authored 2026-07-16.
> Design under review: **this brief §3** (the proposal is small enough to be
> self-contained; no separate spec). Parent documents:
> `docs/SKILLS_CURATION_PHASE4_SPEC_V2.md` (v2.1, ratified 2026-05-27),
> `docs/SKILLS_CURATION_DESIGN.md` (Conditions 1–11),
> `distiller.py` safety rails + `tests/test_distiller_safety.py` (2026-07-11).
> Pattern mirrors `SKILLS_CURATION_COMMITTEE_BRIEF.md` (four seats,
> RATIFY / RATIFY-WITH-CONDITIONS / REJECT per seat, synthesis before code).

---

## 1. Background — why this review, why now

The learning loop's **producing** side works and its **safety** side has held
since the 2026-07-11 rails (zero authority violations in five days of
operation). The **consuming** side does not close:

- The human promotion gate processed **0 decisions in the 4 days** after the
  rails landed; the oldest queued proposal is **44 days old**.
- Where the gate HAS been exercised, it approved **59 of 67** decisions
  (1 rejection, 7 rail-driven quarantines). It is a consent ritual, not a
  quality filter — quality is enforced upstream (authority guard, refusal
  telemetry, recurrence thresholds), and the 2026-07-11 audit already
  concluded "the queue is where rubber-stamping happens."
- Meanwhile the queue accumulates (146 artifacts, 16 promotable) and the
  system's own loop-health alert nags about it every cycle.

The operator (2026-07-16) adopted the recommendation to pursue auto-scope
promotion **subject to this committee review**, because the proposal moves a
boundary set by the 07-11 safety rails — specifically the shape of the
"human promotes" leg. Per the standing rule in CLAUDE.md ("do not weaken any
of them without a committee review"), no code lands until this brief is
ratified and conditions are closed.

**What changed since Phase 5 was deferred.** The original design said `auto`
mode ships only "after `proposed` is real and proposal quality has been
observed in practice." Both conditions are now met: 80+ human decisions
observed, generation-time guards proven in production (2 authority refusals
recorded by telemetry, 6 escaped artifacts caught and quarantined
retroactively, 0 since), suppression durable across projects, and origin
provenance stamped on every artifact (backfilled 2026-07-16 across all 147
pre-existing artifacts using session records, with a STRICTER rule than the
live `is_unattended_task`).

---

## 2. The rail question, stated honestly

Rail #2 says: **a human must be on at least one side of every learning
loop.** Today the promotion gate puts the human on the *consuming* side of
SKILL/PREFERENCE installs. This proposal removes that promotion-time human
for a narrow class of artifacts — so it must put the human on the
*producing* side instead, structurally, not aspirationally:

> **Only artifacts with `origin: interactive` are eligible for
> auto-installation.** An interactive-origin artifact is distilled
> exclusively from sessions where a human was present and driving. The
> human is therefore on the producing side of the loop by construction —
> the same logic `exploration_read_floor` already uses for readback, now
> applied to installation.

The rail is not weakened; its enforcement point moves from promotion time to
origin provenance. Whether the committee accepts that equivalence is the
central question of this review.

---

## 3. The proposal

### 3.1 Eligibility (ALL must hold)

An artifact in `data/skills/_proposed/<project_id>/...` is auto-installed to
`<project>/.claude/skills/` iff:

1. `kind` ∈ {`skill`, `preference`} (explorations stay readback-only).
2. `origin: interactive` — stamped at generation, conservative-OR over
   evidence sessions; `unattended`, missing, or backfill-unresolvable makes
   the artifact **ineligible forever** (fail closed).
3. `recurrence_count_exact ≥ 3` (the ratified v2.1 threshold, exact layer
   only — coarse recurrence does NOT qualify; it exists for near-miss
   telemetry, and D2 gave it threshold N+1 for *proposal*, not install).
4. Scope is **project-specific**. Cross-project / global artifacts are
   NEVER auto-installed — global promotion stays human-only, permanently.
5. Not suppressed: `_is_suppressed(fingerprint, kind)` clean, including the
   global suppression file (durable-"no" rail untouched).
6. Passes `_authority_violation()` **again at install time** (defense in
   depth — the generation-time refusal already exists; re-checking at the
   new trust boundary costs one function call).

### 3.2 Install semantics

- Installed with `auto_authored: true` + `auto_installed_at` frontmatter —
  same marker the original design specified for `auto` mode.
- One-click revert: uninstall moves the artifact to `_rejected/` and writes
  a suppression record (same path as a human reject — "no" stays durable).
- Surfaced in the Skills panel under a distinct "auto-installed" badge, and
  listed in the monthly audit (`MAINTENANCE_AUDIT_PROMPT.md` extension —
  design build-order step 3, which ships WITH this, not after).
- A per-project AND global kill switch (`distiller_auto_scope_enabled`,
  default **false**; per-project `distiller_mode: auto` opt-in). Ships dark;
  the operator enables per project.

### 3.3 What this does NOT change

- The authority guard (rail #1) — untouched, still generation-time + now
  re-checked at install.
- Durable rejection (rail #3) — untouched; auto-install respects existing
  suppressions and its revert path writes new ones.
- Global/`~/.claude/skills/` — no machine write path exists or is added;
  the steward fence continues to block `.claude/` writes by agents.
- EXPLORATION readback — unchanged (already governed by origin filtering).
- `distiller_mode: proposed` remains the default for every project.

### 3.4 Known adjacent gap (flagged for the same review)

`is_unattended_task()` currently recognizes ONLY the `[Steward cycle]`
marker. Schedule-triggered sessions (watchdogs, night-shift maintenance:
185 `trigger_type: schedule` records) stamp `origin: interactive` today
despite having no human present. **Proposed tightening** (a pure
restriction): unattended = steward marker OR `trigger_type` ∈ {schedule,
hivemind_orchestrator, hivemind_worker} OR unattended-prompt text. The
2026-07-16 origin backfill already applied this stricter rule to the 147
historical artifacts; this condition would make generation match it.
Auto-scope should not ship without this tightening — otherwise a scheduled
watchdog's output could qualify as "human on the producing side."

---

## 4. The four seats

Same structure as the parent brief. Independent review, no cross-talk,
output format per `SKILLS_CURATION_COMMITTEE_BRIEF.md` §4.

### Seat 1 — Rail integrity (was: pattern integrity)
Is origin-provenance-at-producing-side a true equivalent of
promotion-time-human for rail #2? Attack the equivalence: laundering paths
(interactive session quoting steward output?), origin-stamp spoofing, the
conservative-OR's blind spots, backfill trustworthiness.

### Seat 2 — Agent behavior & artifact quality
Does recurrence ≥3 + interactive-origin actually select good artifacts?
Sample the 16 currently-promotable artifacts against these criteria: which
would have auto-installed, and would any have been wrong? (Evidence
requirement: name them.)

### Seat 3 — Concurrency & lifecycle
Install/revert races vs. the Distiller's lock-free cross-project walk;
double-install on re-aggregation; suppression write ordering; what happens
when a human rejects an artifact that auto-installed an hour earlier.

### Seat 4 — Config, ops, rollback, cost
Kill-switch layering (global flag vs. per-project mode), dark-ship
verification, audit-surface adequacy, blast radius of a bad auto-install
(one project's loadout), and whether revert truly restores the pre-install
state.

---

## 5. Review rules

Per the parent brief: each seat reads this brief + the three parent
documents + `distiller.py`'s rails section end-to-end before writing.
Verdicts: RATIFY / RATIFY-WITH-CONDITIONS / REJECT. Conditions must be
closable in design or implementation (say which). Synthesis appended to
this file before any code lands.
