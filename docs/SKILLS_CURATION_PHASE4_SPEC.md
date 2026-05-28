# Skills Curation — Phase 4 (Silent Distiller) Spec

Status: **DRAFT v1.1 — RATIFIED WITH CONDITIONS (committee review
2026-05-27)** · Author: diagnostic session 2026-05-27 · Companion
to `docs/SKILLS_CURATION_DESIGN.md` (v2 post-committee-review 2026-05-19).
Committee synthesis at end of doc. **v1.2 revision required** before
Phase 4 backend code lands: 14 must-fix-in-design conditions to close.

> **v1.1 changes (2026-05-27, operator confirmations):**
> - Recurrence threshold default LOCKED at `3` (§3.2, §6 Q1 closed).
> - Cross-project scoping RESOLVED to per-project default + a follow-on
>   "cross-project candidates" surface (§3.7, §6 Q4 closed).
> - Phase 1 (`mc-distill`) hard-rule softening APPROVED — parallel
>   SKILL.md edit ships ahead of Phase 4 backend (§6 Q5 closed; see
>   "Phase 1 softening" subsection at end of §3).

> **Posture change vs. the parent design.** The parent design's
> Recommended build order put Phase 4 (silent Distiller) at step 4 of 7,
> behind Phase 2 (skill-use telemetry) and Phase 3 (audit checklist
> extension). This spec proposes promoting Phase 4 to **the next thing
> built**, absorbing Phase 2's telemetry-substrate work into it. The
> rationale is empirical (see §1).

---

## 1. Problem — the diagnostic that motivated this spec

Phase 1 (`mc-distill` skill, shipped 2026-05-18) has two trigger paths:

- **Explicit user invocation** (`/distill`, "propose a skill", ...).
- **Proactive agent-initiated proposal** with hard rules: recurrence ≥2
  *within the same session*, natural breakpoint, specificity bar, one-
  push-per-session-max.

Diagnostic run 2026-05-27 against `~/.claude/projects/**/*.jsonl` over
9 days post-ship, **1,199 sessions** in the top working projects:

| Signal | Count |
|---|---|
| Sessions where `mc-distill` was loaded | ~99% (47/47 MC, 51/52 soccer-intel, 20/20 APEX-Live, …) |
| **Proactive proposals surfaced by the agent** | **0** |
| Manual `/distill` invocations | **1** (the original Phase A validation on 2026-05-19) |
| Actual `_proposed/` writes | 2 (the May 19 proposal + this diagnostic) |

The committee's soak-gate condition #14 ("calibrate the proactive bar
against organic evidence; do not flip defaults on assertion") just
collected its evidence. The empirical fire rate is **zero**.

### 1.1 Why the in-session bar produces zero proposals

The recurrence-≥2-*within-session* gate is structurally incompatible
with how work actually happens. Single-task sessions ("fix this bug,
commit, done") have no within-session repetition to detect. The patterns
that *are* worth bottling — "every time I touch the mobile UI I forget
X," "every time I work on memory code I rediscover Y" — recur
**across** sessions, which the in-session agent cannot see.

This is not a calibration problem (loosening `≥2` to `≥1` would produce
noise, not signal). It is a **layer** problem: the cross-session
observer (MC, holding the full transcript history per project) is the
only place where these patterns are visible.

### 1.2 Why Phase 2-telemetry-first no longer makes sense

Phase 2 was scoped as "count how often Phase 1 fires and how the user
responds." With a zero fire rate, the telemetry is silent — it tells us
nothing actionable. The recurrence-counting substrate Phase 2 was going
to build is still needed, but it is needed **as an input to Phase 4**,
not as a standalone measurement of Phase 1.

This spec therefore folds Phase 2's substrate (`_skill_stats.json`,
fingerprint normalization, locks, kill-switch gate) into Phase 4's
implementation surface.

---

## 2. Principles

Inherits all parent-design load-bearing rules. Re-stating the ones
Phase 4 specifically touches:

1. **MC observes; agent does not self-report.** Same control-plane /
   data-plane split as Memory System. The Distiller reads the same
   `.jsonl` Scribe reads and writes proposals server-side.
2. **Distiller is best-effort, never load-bearing.** Failure to distill
   never breaks the session, Scribe, or completion logging.
3. **MC owns, agent proposes, human promotes.** Phase 4 ships
   `proposed` mode only. `auto` mode (Phase 5) is gated on Phase 4
   producing reviewable proposals at acceptable quality.
4. **No new lock domains, no new write disciplines.** Phase 4 reuses
   `_get_mem_write_lock` / `_atomic_write_text` / the `.tmp + rename`
   atomic-proposal pattern from the parent design's load-bearing rules.

---

## 3. Mechanism

Phase 4 runs at session end, parallel to Scribe, on the same `.jsonl`.

### 3.1 Per-session extraction

For each completed session that passes the kill-switch gate (§3.5), the
Distiller emits zero or more **session signals**. A session signal is:

```
{
  "fingerprint": "<deterministic-hash>",
  "canonical_phrase": "<5-12 word description>",
  "evidence": ["<concrete observation 1>", "<concrete observation 2>", ...],
  "session_id": "<sid>",
  "ts": "<iso8601>"
}
```

The cheap-model call extracts **observations**, not proposals. The
prompt is deliberately weaker than `mc-distill`'s: "what topics or
problems did this session touch — name them as short phrases. Do not
judge whether anything is worth bottling." Single-touch topics are
fine; the cross-session aggregator (§3.2) is what filters for
recurrence.

This is the inverse of the Phase 1 design choice. Phase 1 asks the
agent "is this pattern worth proposing?" — a hard, subjective judgment
the agent declines to make. Phase 4 asks "what happened here?" — a
narrow, objective extraction the cheap model handles reliably.

### 3.2 Cross-session aggregation

Session signals are written to per-project `_skill_stats.json` under a
rolling window (default 60 days). On each write, the Distiller checks:

- **Fingerprint matches across distinct sessions ≥ `distiller_min_recurrence`** (default `3`)
- **No existing skill name match** (against `~/.claude/skills/` + `<project>/.claude/skills/`)
- **Not in the per-fingerprint suppression list** (set by `No` responses, §3.4)
- **Not in the per-(project, fingerprint) daily dedupe window** (Cond 7 v2)

Patterns crossing all gates become **proposal candidates**.

### 3.3 Fingerprint stability

Per parent design open item #1 (RESOLVED v2):

- **Stage 1** — cheap model emits a canonical phrase.
- **Stage 2** — deterministic normalization: lowercase → tokenize →
  stopword-strip → sort tokens → join with `-` → hash.

The cheap model's lexical variance ("use-edit-block-for-surgical-
changes" vs. "prefer-edit-block-over-write-file-for-small-edits") is
collapsed by Stage 2. Bag-of-tokens equivalence is the fingerprint.

Stage 2 is testable in isolation (pure function); ships with unit
tests covering lexical-variant collapse and stopword stability.

### 3.4 Proposal generation

When a candidate crosses all gates, the Distiller invokes a second
cheap-model call with the **aggregated evidence** (concrete
observations from the N matching sessions) and asks for a SKILL.md
draft. Output is atomic-written to
`data/skills/_proposed/<YYYY-MM-DD-T-HH-MM-SS-fingerprint>/SKILL.md`
via `.tmp + rename` (Cond 8 v2).

UI surfaces unreviewed proposals in the existing Skills panel (no new
surface). Human reviews + promotes via the existing manual flow (the
`mc-distill` SKILL.md "Promotion on explicit user instruction"
procedure, or future Phase-2-UI promote button).

### 3.5 Kill-switch gate

Single function `_distiller_should_proceed(project_id) -> bool` (Cond
10 v2). Returns `True` iff:

- `CONFIG.get('distiller_enabled_global', True)` — master kill
- `project.get('distiller_mode', 'proposed') != 'off'` — per-project
- `not session.get('incognito') and not session.get('housekeeping')`
  — inherits the Scribe gating-rules verbatim

Every entry point (session-end hook, future auto-mode promote, future
dispatch hint) MUST route through this function. Unit test enumerates
all call sites and asserts each passes through the gate.

### 3.6 Scoping — per-project with a cross-project surface

**Proposal generation is per-project.** Aggregation in §3.2 reads only
the current project's `_skill_stats.json`; proposals land in the
current project's `_proposed/` directory. This honors the parent-
design load-bearing rule "auto-authored skills are project-local
only" and keeps blast radius small: a bad pattern in one project does
not leak into others.

**A separate cross-project candidates surface** observes fingerprints
that recur across **multiple projects** (default ≥2 distinct projects,
each with recurrence ≥ `distiller_min_recurrence`). It does NOT
auto-write proposals — it surfaces a notification in the Skills UI:

> "Pattern `<canonical_phrase>` appeared in projects mc, soccer-intel,
> claydo. Looks operator-level. Promote a draft to `~/.claude/skills/`
> globally?"

Promotion remains a deliberate human action (per the parent-design
load-bearing rule). The cross-project surface is just *evidence* that
a candidate is operator-level rather than codebase-level.

**Why both:** patterns split into two kinds — codebase conventions
(project-local, e.g., "`load_projects()` needs DATA_DIR exclusion every
time we add a sidecar") and operator workflow (cross-project, e.g.,
"Ron wants ELI5 versions when explanations get jargon-heavy"). Default
per-project keeps the first kind safe; the cross-project surface
exposes the second kind without leaking it automatically.

### 3.7 Suppression / `Later` honoring

The `mc-distill` skill's `No` response writes a suppression marker
keyed on `(project_id, fingerprint)`. The Phase 4 silent Distiller
honors these markers — a suppressed fingerprint never produces a
proposal, even at recurrence ≥ N.

`Later` responses now write a `wait_until_recurrence: <N+1>` stub —
the silent Distiller picks up the pattern *only* when recurrence
exceeds the count it was at when `Later` was said. This closes the
parent design's Cond 3 v2 "Later is a false promise" honestly.

Both writes go through the shared `_get_skill_stats_lock(project_id)`
(Cond 6 v2).

### 3.8 Phase 1 softening (parallel SKILL.md edit, ships ahead of backend)

The diagnostic established that `mc-distill`'s hard rules combine to
produce zero proactive fires across 1199 sessions in 9 days. With
Phase 4 set to catch the *cross-session* patterns the in-session agent
can't see, Phase 1's residual job is the **novel, in-the-moment
insight** — a hard-won discovery from this session that's worth
bottling immediately, even at recurrence = 1, because waiting for
Phase 4 to see it 3 times across weeks is silly when the insight is
fresh.

The following changes ship as a `mc-distill/SKILL.md` edit ahead of
Phase 4 backend code:

| Rule | Old | New |
|---|---|---|
| Recurrence within-session | Required ≥2 observable repetitions | **Dropped.** A single hard-won novel insight is enough. Phase 4 catches cross-session recurrence. |
| Once-per-session cap | Maximum 1 proactive push | **Dropped.** Multiple genuinely distinct insights in one session may each warrant a proposal. |
| Natural breakpoint | Required (end of task / after commit / wrap-up) | **Kept.** Don't propose mid-debug. |
| Specificity bar | One-sentence name + concrete observations | **Kept and strengthened.** Vague is still worse than silent. |
| Not-already-covered | Required (search first) | **Kept.** No duplicates. |
| Tone "err toward asking less" | Default disposition | **Reversed:** "if you noticed something worth bottling at a natural breakpoint, say so. Phase 4 catches what you missed; you catch what you noticed." |

The softening is a controlled experiment. Two signals to watch in the
2-3 weeks after the SKILL.md edit lands:

1. Does the proactive fire rate move above zero?
2. Do humans accept the proposals (Yes/Later/No ratio)?

If both look healthy → Phase 1's softened bar is sustainable.
If fire rate is high but quality is low → tighten specificity rule.
If fire rate stays at zero → Phase 1 is structurally inert and Phase 4
is the entire mechanism. Either outcome is informative.

---

## 4. Reuse map

| Phase 4 piece | Reuse from | Site |
|---|---|---|
| Session-end trigger | `_write_session_memory` (Leg A) | server.py:4699 |
| Cheap-model call wrapper | `_scribe_call` | server.py:5244 |
| Transcript reader | `_scribe_render_transcript` | server.py:5205 |
| Extraction/render guards | `_scribe_extract` precedent | server.py:5263 |
| Per-project leaf lock | mirror `_get_mem_write_lock` | server.py:849 |
| Atomic write | `_atomic_write_text` | server.py:859 |
| Counter writer | mirror `_scribe_stat` | server.py:5126 |
| Telemetry endpoint shape | mirror `/scribe-stats` | server.py:1809 |
| `load_projects` exclusion site | append `_skill_stats.json` | server.py:1158 |

**Genuinely new code:**

1. `distiller.py` — module containing `_distill_session_signals`,
   `_distill_aggregate`, `_distill_render_proposal`,
   `_distiller_should_proceed`, fingerprint normalizer.
2. `_skill_stats.json` schema + writers + lock helper.
3. Two server endpoints:
   - `POST /api/project/<id>/distiller/record-push` (called by
     `mc-distill` on `No`/`Later`).
   - `GET /api/project/<id>/distiller-stats` (mirrors `/scribe-stats`).
4. Hook into `_write_session_memory` (one parallel call alongside the
   Scribe call; guarded by the kill-switch).
5. Regression test for `load_projects` exclusion (Cond 13).
6. `mc-distill` SKILL.md update: replace the "`Later` ≡ `No`"
   disclosure with a real `record-push` call.

Estimate: **~400–600 lines of new code** (vs. ~800 if Phase 2 were
built standalone first; the substrate is the same code, just folded
into one PR).

---

## 5. Minimum viable cut (what Phase 4 v1 ships)

In scope:

- Session-signal extraction (§3.1)
- Cross-session aggregation with recurrence gate (§3.2)
- Two-stage fingerprint normalization (§3.3)
- Proposal generation + atomic `_proposed/` write (§3.4)
- Kill-switch gate + unit test (§3.5)
- Suppression/`Later` honoring (§3.6)
- `_skill_stats.json` schema + writers + lock
- Two server endpoints
- `mc-distill` SKILL.md update
- DATA_DIR exclusion regression test
- Per-(project, fingerprint, day) dedupe (Cond 7 v2)

Out of scope (deliberately deferred):

- **`auto` mode (Phase 5).** Gates on Phase 4 producing reviewable
  proposals at acceptable rate + quality. Default `proposed` only.
- **Dispatch-time skill hint (Phase 6).** Independent track; can ship
  whenever.
- **Audit checklist extension (Phase 3).** Folds in once Phase 4 has
  enough stats to surface "stale proposal," "auto-authored awaiting
  review," etc.
- **UPDATE.md/PATCH.md proposal types.** Phase 4 v1 emits new-skill
  SKILL.md only. UPDATE.md gates on a stable matched-skill detector
  (parent design open item #2).
- **Cost cap structured log + endpoint (Cond 9 v2).** Stub the counter
  now (writes a `cost: { date: tokens }` field); ship the structured
  log + cap-hit endpoint with Phase 5 when token volume warrants it.
  Phase 4 v1 is conservative on volume: one extract call + zero or one
  proposal-generation call per session.

---

## 6. Open questions for committee

1. ~~**Recurrence threshold default.**~~ **CLOSED v1.1:** `3`. Bump to
   `5` only if first 2 weeks of v1 soak produce garbage.
2. **Extraction prompt quality.** §3.1's "name the topics" framing is
   intentionally narrow. Is it narrow enough to keep the cheap model
   reliable? Should we ship a corpus of golden expected outputs and
   test against them, or accept the empirical-only validation path?
3. **Rolling window size.** 60 days is a guess. Memory System's
   archive is unbounded; should `_skill_stats.json` mirror that, or
   stay bounded to keep the recurrence counter responsive to recent
   patterns vs. ancient ones?
4. ~~**Cross-project recurrence.**~~ **CLOSED v1.1:** per-project
   default for proposal generation (blast-radius safety) + a separate
   cross-project candidates surface that *notifies* but does not
   auto-write (operator-level patterns are findable without leaking).
   See §3.6.
5. ~~**Phase 1 trigger guidance.**~~ **CLOSED v1.1:** soften via
   parallel `mc-distill/SKILL.md` edit ahead of Phase 4 backend. Drop
   within-session recurrence and once-per-session cap; keep natural
   breakpoint, specificity, no-duplicates. See §3.8.

---

## 7. Implementation anchors

| Anchor | Existing site | Phase 4 change |
|---|---|---|
| `_write_session_memory` | server.py:4699 | Add one parallel call to `_distill_extract_and_aggregate` (guarded by `_distiller_should_proceed`). |
| `load_projects` exclusion | server.py:1158 | Append `_skill_stats.json` to the tuple. |
| Lock pattern | `_get_mem_write_lock` server.py:849 | Mirror as `_get_skill_stats_lock`. |
| Atomic writer | `_atomic_write_text` server.py:859 | Reuse verbatim. |
| Cheap-model wrapper | `_scribe_call` server.py:5244 | Reuse verbatim (same `claude -p` path). |
| Telemetry endpoint | `/scribe-stats` server.py:1809 | Mirror as `/distiller-stats`. |
| Config keys | `_CONFIG_EDITABLE_KEYS` server.py:11109 | Add `distiller_enabled_global`, `distiller_mode`, `distiller_min_recurrence`, `distiller_model`. |

New module: `distiller.py` (mirrors the `project_sync.py` /
`github_sync.py` pattern — born outside `server.py` per
`MAINTENANCE_PROTOCOL.md` Rule 1).

---

## 8. Committee posture

Per the parent design's discipline (mirroring Memory System
SPEC §3.A.MID post-committee + Leg C 2026-05-18 ratification): **no
backend code lands until this spec passes committee review.** The
parent design's v2 ratification covers Phase 4's surface (it ratified
*the existence* of Phase 4 with conditions), but the specific shape
proposed here — promoting Phase 4 ahead of Phase 2, the extraction-not-
judgment prompt design, the rolling-window aggregation — is new and
warrants a fresh four-seat pass.

Recommended seats (same structure as parent committee):

- **Seat 1 — Pattern integrity.** Reviews §3.1 extraction prompt,
  §3.2 aggregation logic, §3.3 fingerprint stability against
  cheap-model variance.
- **Seat 2 — Agent behavior & proposal quality.** Reviews §3.4
  proposal generation, the "extraction not judgment" choice, and
  whether the soak-gate evidence (zero proactive fires in 9 days)
  justifies the build-order change.
- **Seat 3 — Concurrency & lifecycle.** Reviews §3.5 kill-switch,
  §3.6 suppression markers, the shared-lock-with-`mc-distill`
  contract, parallel-Scribe execution at `_write_session_memory`.
- **Seat 4 — Config, ops, rollback, cost.** Reviews §5 scope
  boundaries (especially the deferred cost-cap structured log),
  rollback path (mode flip + file deletion), DATA_DIR exclusion
  test, telemetry surface.

After ratification + condition closure (if any), build can start.

---

## Committee review (2026-05-27) — RATIFY-WITH-CONDITIONS

Four-seat review dispatched against this spec + the parent design + the
post-softening `mc-distill/SKILL.md` (commit `95b5aa8`). Full per-seat
assessments preserved at `docs/_committee/SKILLS_CURATION_PHASE4_seat<N>_*.md`
(scratch, not committed).

**Unanimous: RATIFY-WITH-CONDITIONS. No blockers, no data-loss path.**
Core ratified: extraction-not-judgment inversion (§3.1), build-order
flip rationale grounded in the §1.1 diagnostic, per-project default +
cross-project notify surface (§3.6) preserving the load-bearing
"auto-authored project-local only" rule, reuse-map discipline (no new
lock domains, no new write disciplines), `distiller.py` born outside
`server.py`, `auto` mode deferred to Phase 5, atomic `.tmp + rename`
inherited from parent Cond 8 v2.

**14 must-fix-in-design conditions** to close before v1.2 issuance:

1. **Stage 2 normalization is structurally too narrow (Seat 1).** Spec's
   own §3.3 worked example fails its own algorithm: `use-edit-block-for-
   surgical-changes` and `prefer-edit-block-over-write-file-for-small-
   edits` normalize to **different fingerprints**. Stage 2 collapses
   stopword + ordering variance, NOT synonym + omission variance, which
   is what cheap models actually produce. Re-opens parent Cond 1 v2's
   silent-failure mode. Fix: pick one of (a) closed-vocabulary Stage 1
   schema, (b) embedding-based Stage 2 now (don't defer to v3), or (c)
   ship Stage 2 as-is with fingerprint-drift telemetry + near-miss log.
2. **§3.1 extraction prompt has no granularity bound (Seat 1).**
   "What topics did this session touch?" can be answered at any
   granularity (`fixed-load-projects-bug` vs `edited-python-code` vs
   `clayrune-work`). All four valid; none collide. Upstream cause of
   Stage 2's failure in Condition 1. Fix: add granularity floor + ceiling
   to the prompt, OR cap signals per session at K=3 with few-shot
   examples drawn from the §1.1 1,199-session corpus.
3. **§3.2 rolling-window semantics unspecified (Seat 1).** Write-time
   purge vs read-time filter not stated. 3-in-75-days vs 3-in-10-days
   are not the same signal but spec treats them identically. Fix:
   specify read-time filtering, append-only `_skill_stats.json`, expose
   `fingerprints_near_threshold` telemetry.
4. **Cross-project surface (§3.6) inherits Conditions 1–2 failures
   silently (Seat 1).** Same operator pattern phrased differently across
   projects produces non-colliding fingerprints; surface looks empty
   even when real cross-project patterns exist. Fix: close C1/C2 first
   OR ship §3.6 with `distiller_cross_project_surface_enabled=false`
   default until empirical evidence of cross-project collisions.
5. **§3.4 proposal-generation prompt unspecified (Seat 2).** Same gap
   as the extraction prompt — only one sentence. Cheap model handed N
   observations produces a median summary that passes review but yields
   low-utility skills. Fix: spec required elements (TRIGGER phrasing,
   operating-procedure framing, ≤120 line body, verbatim observation
   quote, REFUSE path mirroring `_scribe_extract` thin/refusal guards).
6. **§3.8 softening conflates three changes; rollback unordered (Seat 2).**
   Dropped within-session-recurrence + once-per-session-cap + tone-
   reversal simultaneously. Can't disentangle which moved the needle.
   Fix: either (preferred) restore the once-per-session-cap in SKILL.md
   and soak the smaller change first, OR spec an ordered rollback in §3.8.
7. **Kill-switch enumeration omits `record-push` endpoint (Seat 3).**
   §3.5 lists three entry points; the future `POST /distiller/record-
   push` is a fourth that mutates `_skill_stats.json` from the CC
   process. Inverts parent Cond 10 v2 intent. Fix: list `record-push`
   as a fourth gated entry point with explicit `accepted: false` return
   when gated off; unit test enumerates it.
8. **§3.7 shared-lock language covers writers only (Seat 3).** Parent
   Cond 6 v2 requires "readers acquire the lock before reading." v1.1
   §3.7 wording lets an implementer read it as "only writes need the
   lock." Concrete race: push writes `Later` marker; session ends; Distiller
   reads recurrence state pre-marker; proposes anyway under its own
   lock for the write. Fix: reword §3.7 to "Both reads AND writes go
   through the shared lock," with explicit RMW-under-lock spec.
9. **"Parallel-Scribe" not specified — threaded vs sequential (Seat 3).**
   §7 says "one parallel call" — ambiguous. Sequential implementation
   means a 180s Distiller hang delays Scribe's MEMORY.md write 180s
   (user sees `terminating` for full timeout). Inverts best-effort
   posture. Fix: spec `threading.Thread(daemon=True)` dispatch matching
   Step 6's `_checkpoint_worker` pattern at server.py:4808.
10. **Hard-kill recovery order undefined (Seat 3).** Spec doesn't
    specify whether signal-write or proposal-generate commits first.
    Option B (defer signal write) silently undercounts; Option A
    (write first) is idempotent and self-healing. Fix: §3.4 specify
    Option A — signal commits BEFORE proposal-generate begins.
11. **Cross-project surface UI affordance undefined (Seat 4).** §3.6
    specifies behavior + notification copy but never names where the
    notification renders. Surface ships invisible. Fix: pick — Skills
    panel "Cross-project candidates" section, monthly audit entry, or
    both (preferred).
12. **Phase 1 softening rollback path undocumented (Seat 4).** Source
    at commit `95b5aa8` propagates via `_install_builtin_skills()`
    hash-marker scheme. Three rollback paths (hot/cold/hard) have
    different recovery semantics; cold revert silently leaves
    user-edited copies on softened version. Fix: add §3.8 "Rollback
    procedure" subsection naming all three paths + add `--force` mode
    to `install_builtins()` for hard revert OR document manual file
    deletion fallback.
13. **Cross-project surface kill switch missing (Seat 4).**
    `_distiller_should_proceed(project_id)` is per-project; cross-
    project aggregator has no equivalent toggle. Operator wanting to
    silence false-positive cross-project notifications must flip every
    project to `off`. Fix: add `distiller_cross_project_enabled` global
    config key, gated independently.
14. **Config-key scope (per-project vs global) and Settings-UI
    editability ambiguous (Seat 4).** §7 lumps all four keys into
    `_CONFIG_EDITABLE_KEYS` which is the global config table. Parent
    DESIGN §150 explicitly splits these; v1.1 elides the split. Fix:
    rewrite §7 table with explicit per-project (`distiller_mode`,
    `distiller_min_recurrence`) vs global (`distiller_enabled_global`,
    `distiller_model`) classification + Settings-UI exposure flags.

**4 must-fix-in-implementation conditions** to land in the Phase 4
backend commit:

15. **Cost-cap stub must emit structured log + counter on cap-hit (Seat 4).**
    v1.1 §5 defers structured log + endpoint to Phase 5 — directly
    inverts parent Cond 9 v2 ("No silent disables"). Fix: ship
    `distiller_cost_cap_hit:<project_id>:<date>:<tokens_used>` log line
    + `cap_hits` counter in `_skill_stats.json` exposed via the v1
    `/api/distiller-stats` endpoint (which §4 already commits to
    shipping).
16. **`_skill_stats.json` writes must use `_atomic_write_text` (Seat 3).**
    §4 reuse map cites `_scribe_stat` (non-atomic via
    `fp.write_text(...)`). Tolerable for telemetry; not tolerable for
    decision substrate. Fix: reuse `_scribe_stat` for counter SHAPE
    only; route I/O through `_atomic_write_text` per parent Cond 6 v2.
17. **DATA_DIR regression test placement specified (Seat 4).** No
    existing `test_load_projects_*` module. Fix: new file
    `tests/test_load_projects_sidecar_exclusions.py` with anchor-
    comments at server.py:1158, covering all sidecar exclusions +
    asserting that misnamed `skill_stats.json` (no underscore) fails
    loudly.
18. **Observability of §3.8 softening experiment is missing during
    the trough (Seat 2).** Telemetry substrate (`_skill_stats.json` +
    `record-push` + `/distiller-stats`) ships with Phase 4 backend, not
    with the softening. During the 2–3 week window between softening
    propagation and Phase 4 ship, the "two signals to watch" cannot be
    observed without manual grep-the-jsonl. Fix: either (option A) gate
    the next MC restart that propagates the softening behind Phase 4
    substrate shipping first, OR (option B) ship a scripted interim
    diagnostic that reports fire rate + proposals-written weekly.
    **NOTE:** softening ALREADY propagated (MC restarted earlier this
    session per chat record). Option A is past-tense; option B applies.

**2 soak-gate conditions** to track against feature default-flips:

19. **Recurrence threshold default = 3 (Seat 1).** Defensible IF
    Conditions 1–2 close. Keep default = 3 in v1.2; add
    `fingerprints_near_threshold` telemetry; revisit at 4-week soak
    mark with empirical evidence. Do not pre-tighten to 4–5 on
    assertion (mirrors parent Cond 14 discipline).
20. **Adversarial post-debug spurious-specificity (Seat 2).** Current
    SKILL.md specificity bar catches generic vagueness but not session-
    bound concrete proposals ("when the Gemini Mode-A SSE pill stuck,
    check turn_complete first — observed once in 40min debug"). Soak-
    gate: strengthen the bar to require pattern-bound vs session-bound
    discrimination; revisit at 4-week mark with empirical specimens.

**Cross-cutting ratifications (preserve across future revisions):**

- **Extraction-not-judgment inversion (§3.1, §3.2).** Diagnostic evidence
  (0 fires across 1,199 sessions) is a clean refutation of in-session
  "is this worth?" framing. Inverse framing is structurally correct.
- **Per-project default + cross-project notify surface (§3.6).** Right
  blast-radius posture. Cross-project auto-write would invert parent
  load-bearing rule. Surface as evidence, not as automation.
- **Build-order flip rationale (§1.2).** Phase 2 telemetry on top of a
  silent Phase 1 is silent itself. Folding substrate into Phase 4
  unifies the lock/atomic-write surface.
- **Best-effort posture preserved (§2 principle 2).** Distiller failure
  never breaks Scribe, completion logging, or session lifecycle.
- **Reuse-map discipline (§4).** Eight reuses against six pieces of
  genuinely new code. No new lock domains, no new write disciplines.
- **`distiller.py` born outside `server.py`** per MAINTENANCE_PROTOCOL
  Rule 1.
- **`auto` mode deferred to Phase 5.** Bounds v1 blast radius.
- **The single-fingerprint suppression contract (§3.7) inherits Cond 6
  v2 and Cond 3 v2 correctly.** `Later → wait_until_recurrence: <N+1>`
  is honest about what the system can promise.

**Out-of-scope flags routed by synthesizer:**

- **mc-distill SKILL.md is internally inconsistent post-softening
  (multiple seats).** §"Proactive" subsection dropped the old rules but
  §"Procedure" Step 2 still says "Recurs or is likely to recur in
  similar future sessions" and the proactive proposal format still says
  "Observed \<N\> times this session." §"Tone" still says "err toward
  asking less" — directly contradicting the §"Disposition (reversed)"
  paragraph. **Already-propagated SKILL.md is live with the
  inconsistency.** Fix in v1.2 SKILL.md edit alongside Condition 6.
- **Phase 1 vs Phase 4 fingerprint mismatch at the suppression seam
  (Seat 1 → Seat 3).** In-session agent's fingerprint and silent
  Distiller's fingerprint are independently generated. If they don't
  collide for the "same insight," suppression marker is keyed on Phase
  1's fingerprint and Phase 4 proposes anyway under its own. Bears on
  Conditions 1, 4, 8. Address in v1.2 §3.7.
- **`Later` honoring + dropped once-per-session-cap during trough
  (Seat 2 → Seat 3).** No suppression substrate yet AND no cap. Agent
  CAN re-propose same fingerprint in same session after `Later`.
  Acceptable if volume stays low; flag for v1.2 trough-window note.
- **`distilled_manual: true` vs `provenance: distilled` divergence
  (Seat 2 → Seat 4).** Trough-era manual proposals and post-Phase-4
  silent-distilled proposals will have different provenance flags.
  Audit + per-provenance no-invocation thresholds (parent Cond 15)
  must handle both shapes.
- **Cross-project surface frequency/dedupe semantics (Seat 4 → Seat 1).**
  Parent Cond 7 v2 gave per-project per-(project, fingerprint, day)
  dedupe. Cross-project surface inherits no such window. Address with
  Condition 11 UI affordance design.
- **Cross-project threshold semantics (Seat 4 self-flagged).**
  `distiller_min_recurrence × 2 projects = 6 total sessions` at
  default. Intentional, or should cross-project have own threshold
  (`distiller_cross_project_min_projects`)? Address with Condition 13.
- **Few-shot examples drawn from real corpora (Seat 1).** Hand-crafted
  few-shots will calibrate cheap model to author's voice, not
  operator's. Implementation discipline: sample 20 sessions from the
  diagnostic's 1,199-session window before turning Stage 1 on at scale.
- **Telemetry schema `(fingerprint, [(canonical_phrase, sid, ts), ...])`
  (Seat 1 → Seat 4).** For Condition 1(c) debug ergonomics. Trivial
  v1 extension.
- **Cross-project aggregation data location (Seat 3).** Walk all
  `data/projects/*_skill_stats.json` at request time (safe, no new
  lock domain) vs cached global index (new shared write surface).
  Address with Condition 11.

**Path forward:**

1. Address the 14 must-fix-in-design conditions by revising the spec
   to v1.2. Includes the mc-distill SKILL.md consistency fix (out-of-
   scope flag) and Condition 6's decision on whether to restore the
   once-per-session-cap.
2. Mark doc status: `DRAFT v1.2 (post-committee-review 2026-05-27)`.
3. 4 must-fix-in-implementation conditions get tracked against the
   Phase 4 backend commit.
4. 2 soak-gate conditions get tracked in the audit checklist when
   Phase 3 lands.
5. Phase 4 backend build can start once spec is at v1.2.

**No backend code lands until spec is at v1.2.** Same discipline as
parent design v2 (mirroring Memory System §3.A.MID post-committee +
Leg C 2026-05-18 ratification).
