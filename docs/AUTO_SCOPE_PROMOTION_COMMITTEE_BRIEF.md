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

---

## Committee review (2026-07-16) — SYNTHESIS

> Four seats dispatched 2026-07-16, independent, no cross-talk. Full
> assessments: `docs/_committee/AUTO_SCOPE_seat{1,2,3,4}_*.md`.

**Verdict: 4× RATIFY-WITH-CONDITIONS — unanimous, no REJECT, no blockers as
filed.** One condition is blocker-grade (Seat 1 flips to REJECT if unclosed).
30 seat-conditions consolidate to the 12 below after dedup; the convergences
were strong (three conditions were independently named by 3+ seats).

**The two findings that reshape the proposal:**

1. **The laundering circuit is real (Seat 1, blocker-grade).** Steward
   exploration (`origin: unattended`) → read-floor injection into attended
   sessions → those sessions re-emit the fingerprint as `interactive`
   (extraction checks only the session's own task) → 3 injections manufacture
   recurrence ≥3 → auto-install → installed skill loads into steward cycles
   (no origin filter on loadouts). Autonomous output becomes autonomous
   standing instruction with no human reading any artifact. Must close via
   fingerprint-level taint (M1).

2. **The preference leg is an empirically null set (Seat 2's audit of all 93
   artifact instances).** Preferences generate at recurrence 1 BY DESIGN
   ("the human promotion step is the quality gate"); 50/50 observed
   preference fingerprints never recurred, so `preference + rec≥3` admits
   nothing — and the first fp ever to reach 3 is disproportionately likely a
   closed-vocab collision blending unrelated remarks into one standing
   instruction (fp `a0faa2e13c42` demonstrably rendered two unrelated
   bodies). Seat 4 independently flagged the preference leg as contradicting
   the parent scope doc. **Resolution: the preference leg is DROPPED — auto
   scope is skills-only.** Preferences keep the human queue permanently.

**Honest yield statement (Seat 2, evidence requirement met):** as the disk
stands, 0 artifacts would have auto-installed (fail-closed on origin); under
the most generous counterfactual, 11 — 0 harmful, 1 redundant pair, 3 good
skills starved by fingerprint collision. With §3.4 tightening (which
reclassifies the heaviest rec≥3 producers as unattended), expected yield is
**~0–3 installs**. The queue-drain motivation is moot anyway — the queue was
manually drained to zero promotables on 2026-07-16 and durable-yes now
prevents re-accumulation of installed content. Auto-scope is
**future-proofing the interactive path**, not backlog relief. The brief's §1
motivation is superseded by this statement.

### Consolidated must-close conditions

M1 (design+impl, BLOCKER-GRADE) — Fingerprint taint closes the laundering
    circuit: a fingerprint with any unattended-origin sibling (exact OR
    coarse) anywhere in _proposed/_promoted/_rejected is ineligible. [S1-C1]
M2 (impl) — Eligibility (origin, recurrence, suppression) recomputed from
    _skill_stats.json at install time; frontmatter is display-only.
    [S1-C2 + S2-C5 + S3-C6 — strongest convergence]
M3 (impl) — Steward fence extended to data/skills/** and *_skill_stats.json
    writes, with regression tests beside test_distiller_safety.py. [S1-C3]
M4 (design+impl) — Unattended detection inverted to an ALLOWLIST:
    interactive ⇔ trigger_type=='manual' present AND no unattended markers;
    missing/backfilled/unknown → unattended. Machine-dispatched child
    sessions (steward → local API) carry initiator provenance so they can't
    shed unattendedness in one hop. [S1-C4, S1-C5; supersedes §3.4]
M5 (design) — Skills-only: preference leg dropped per the null-set + collision
    evidence. [S2-C1, S4-flag]
M6 (design+impl) — Lifecycle: proposal-dir rename is the atomic claim token
    (NOT the stats lock — self-deadlock class); post-claim suppression
    re-check so a human "no" always wins; startup reconciler for crash
    windows keyed on suppression source; explicit artifact state machine;
    auto-install writes NO suppression, revert does. [S3-C1,C2,C3,C8]
M7 (impl) — Durable-"no" on EVERY removal path for auto_authored artifacts
    (Skills-panel delete included), or revert becomes a reinstall loop.
    [S1-C6, S3-C3]
M8 (impl) — Atomic install writes + collision guard: never overwrite a
    non-auto-authored skill (the 2026-07-16 b4e4b1bf overwrite is live
    evidence of the clobber class); install-time structural re-validation
    (name/description present, non-REFUSE body, length floor) + re-run
    _authority_violation. [S3-C4,C5; S4-C6; S2-C3]
M9 (design) — Loop containment: per-project per-day auto-install rate cap +
    coarse-family telemetry for derivative-fingerprint amplification.
    [S1-C7, S3-C7, S4-C10 — 3-seat convergence]
M10 (impl) — Observability ships in the SAME commit: structured per-install
    log (fingerprint, evidence, origin), auto_installed:<kind>/auto_reverted
    counters, real-time Inbox event per auto-install, discovery surface
    (ledger + panel filter + one-click and bulk revert), monthly-audit
    extension. [S1-C7, S4-C4,C5,C9]
M11 (impl) — Kill-switch wiring: two-flag gate joins ENTRY_POINTS with an
    independence test; flag-off semantics = installed artifacts STAY,
    surfaced, nothing silently removed; dark-ship proven by fresh-config +
    defaults-table tests; no new DATA_DIR sidecar. [S4-C1,C2,C3,C8]
M12 (design) — Collision-starvation fix: a candidate whose fp is already
    auto-installed routes to the HUMAN queue instead of silent skip (the
    exact-fp durable-yes block demonstrably starves distinct skills on
    collided fingerprints); revert completeness defined testably; 30-day
    retrospective yield measured and published BEFORE the flag is enabled
    anywhere. [S2-C2,C4; S4-C7]

### Build sequencing (synthesis recommendation)

M3, M4, M8's atomic-write/collision guard, and M10's counters are
freestanding hardening — worth shipping regardless of whether the installer
is ever built. Ship those first. Then M12's retrospective yield measurement
runs against real data; the installer itself (M1, M2, M5–M7, M9, M11) is
built only if the measured yield justifies it. This sequencing is effectively
mandated by S2-C4 and matches the committee's posture: the feature is safe
as conditioned, but its value case must be demonstrated, not assumed.

**No code lands until the operator accepts this synthesis.** Conditions
close in the order above; M1 and M4 gate everything downstream.
