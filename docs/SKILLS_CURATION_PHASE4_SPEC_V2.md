# Skills Curation — Phase 4 v2 Spec (Learning Layer)

Status: **DRAFT v2 — RATIFIED WITH CONDITIONS (committee review
2026-05-27)** · Author: redesign session 2026-05-27 (post
locked-learning-definition). Companion to
`docs/SKILLS_CURATION_DESIGN.md` (v2 post-committee 2026-05-19) and
**supersedes** `docs/SKILLS_CURATION_PHASE4_SPEC.md` (v1.1, now
reference-only). The parent design's load-bearing rules and Conditions
1–11 remain authoritative. Mechanism details from v1.1 (Scribe trigger,
lock pattern, kill-switch shape, atomic-write discipline,
`distiller.py`-outside-server.py) carry forward verbatim where this
spec does not override them.

> **v2.1 revision required** before backend code lands: 14 must-fix-in-
> design conditions to close + 5 must-fix-in-implementation conditions
> to track against the backend commit + 2 soak-gate conditions to track
> against default-flips. Committee synthesis at end of doc. No blockers;
> no data-loss path; no inversion of a load-bearing rule.

> **Why a v2 (not a v1.2 revision):** v1.1 was scoped against the
> narrower "cross-session pattern detection on prompt-based skills,
> per-project default" framing. After v1.1's four-seat committee
> returned RATIFY-WITH-CONDITIONS (no blockers, 14 must-fix-in-design),
> Ron locked an operational definition of "learning"
> (`memory/decision_learning_definition.md`, 2026-05-27) that is
> structurally broader. v1.1's surface no longer matches the target.
> The mechanism plumbing v1.1 specified is still right; the scope,
> structure, and artifact-type layer needs a redesign. v2 is that
> redesign.

---

## 0. The locked definition (verbatim, for reference)

> **Learning is when the agent's effective behavior changes over time,
> driven by experience, without the human having to type the change.**

Experience includes: own past sessions; **proactive external exploration**
(web, docs, code search); user behavior and preferences observed across
interactions; the agent's own decision patterns and failure modes.

Targets: codebase, work, **user**, and the agent itself.

Scope: **default cross-project / operator-level**; narrows to
project-specific only when intrinsically tied to one codebase.
**Dual-checkpoint scope tagging** — extraction-time best-guess +
promotion-time human confirmation.

Four tests: experience-driven, persistent, has-a-feedback-signal
(RELAXED to human review at promotion), broadly scoped by default.

---

## 1. What v2 changes vs v1.1

Five shifts, each driven by a clause in the locked definition.

### 1.1 Scope default flips from per-project to cross-project

**v1.1:** per-project default for proposal generation, with a separate
"cross-project candidates surface" that notified but never auto-wrote.
Rationale was blast-radius safety.

**v2:** cross-project IS the default scope tag at extraction time. A
pattern's scope is determined by the extraction model from the
observations themselves, not by a path convention. Project-specific is
the narrow case — used only when the pattern names a project-local
artifact (a file path under that project, a config key unique to that
project, a database/service that exists only in that project).

**Blast-radius safety is preserved at the *promotion* gate, not the
*generation* gate.** All proposals still land in `_proposed/`. A
cross-project proposal lands at `data/skills/_proposed/global/<...>`;
human reviews and explicitly promotes to `~/.claude/skills/`. A
project-specific proposal lands at `data/skills/_proposed/<project_id>/<...>`;
human promotes to `<project>/.claude/skills/`. The parent design's
load-bearing rule "auto-authored skills are project-local only" still
holds for Phase 5 (`auto` mode, deferred) — `auto` mode writes
cross-project proposals to a global staging area, never directly to
`~/.claude/skills/`. Global skills always require a human in the loop.

This eliminates v1.1's split-surface design (per-project proposals +
separate cross-project notify). One surface, one workflow, scope tag
on each proposal, human promotes to the scope the tag indicates.

### 1.2 External-exploration retention is a first-class artifact

**v1.1:** out of scope. Buried in Scribe prose if at all.

**v2:** the Distiller extracts an `exploration` signal class. Trigger
patterns observable in the transcript: WebSearch tool calls,
WebFetch tool calls, sequences of grep/read across unfamiliar
subsystems, candidate-comparison reasoning ("considered A vs B vs
C…"), explicit alternatives discussion. Each meaningful exploration
becomes an `EXPLORATION.md` artifact (a new proposal type, §4.3)
capturing: question, paths tried, what worked, what didn't, why.

**Why first-class:** the locked definition says proactive external
exploration is in-scope as experience. A research path that took 40
minutes and ended with "Y was the answer because Z" is a learning
artifact even if it never recurs — because the next agent facing
similar question can skip the 40 minutes. Recurrence gating is wrong
for explorations: each exploration is worth retaining once, not after
3 repetitions.

This is the inverse of Phase 4's recurrence-gated SKILL.md flow.
EXPLORATION.md is single-shot retention; SKILL.md is recurrence-gated
synthesis. Both live in the same `_proposed/` directory with different
artifact types (frontmatter `kind: skill` vs `kind: exploration`).

### 1.3 Proactive exploration disposition (behavioral, not structural)

**v1.1:** silent on the agent's disposition; assumed default reactive
behavior.

**v2:** the locked definition explicitly encourages the agent to seek
solutions externally rather than only react. This is a behavioral
shift, set in the agent's operating posture (CLAUDE.md / system
prompt / a disposition skill), not in Phase 4 backend code.

**What Phase 4 does about it:** detects the disposition's effect.
When the agent explores proactively (per §1.2 signals), the Distiller
catches and retains the exploration. When the agent fails to explore
in a session that would have benefited (e.g., a debug session that
ended in "I don't know," with no WebSearch/WebFetch calls), the
Distiller's user-pref extraction (§1.4) may surface that as a
preference signal: "user expected exploration here, agent didn't."
Repeated → proposal: "skill suggesting external exploration for
class-of-problem X."

The disposition itself ships out-of-band — a CLAUDE.md addendum or a
new built-in skill that primes the agent. Phase 4's role is closing
the loop by observing whether the disposition is producing the
retention the user expected.

### 1.4 User-facing learning is in scope

**v1.1:** explicitly out of scope per parent design "authored skills
only; no learned behaviors via curated-region MEMORY.md drift."

**v2:** user behavior and preferences observed across interactions
are an experience class per the locked definition. The Distiller
extracts a `preference` signal class. Trigger patterns: user
corrections ("no, don't do X"), confirmations of non-obvious
recommendations ("yes exactly, keep doing that"), expressed
constraints ("never deploy on Friday"), recurring asks about how
the agent should behave.

These aggregate across sessions like other signals. At threshold
recurrence, the Distiller emits a `PREFERENCE.md` artifact (a new
proposal type, §4.4) containing: the observed preference, evidence
quotes from N sessions, suggested promotion target (a feedback
memory file in the user's memory dir, OR a global SKILL.md, OR
both).

**This does NOT violate the parent design's "no MEMORY.md drift"
rule.** The agent never writes to MEMORY.md from a Phase 4 trigger.
The PREFERENCE.md sits in `_proposed/` until a human reviews it. At
promotion, the human picks the destination: typically a new
`feedback_*.md` file in the user's memory dir, indexed via the
existing MEMORY.md curated-region update pattern. The curated-region
boundary is preserved — the only change is that the Distiller now
*suggests* what should go in the curated region, where before it
only ever suggested what should go in `_proposed/` skills.

The promotion pathway for PREFERENCE.md → feedback memory is
described in §4.4. It uses the same atomic-write + human-gate
discipline as SKILL.md promotion.

### 1.5 Dual-checkpoint scope tagging

**v1.1:** scope determined by which directory the proposal landed
in. Implicit, set by extraction-time routing logic, no human
override path until manual file-move.

**v2:** every signal carries an explicit `scope_tag` field set by
the extraction cheap-model. Values: `cross-project` (default),
`project-specific`, `ambiguous`. Each proposal artifact inherits the
scope tag from its dominant evidence. At promotion, the human UI
displays the extraction-time tag with a one-click override (the
"promotion checkpoint"). The promoted artifact's frontmatter records
both: `extraction_scope: cross-project`, `promoted_scope:
cross-project | project-specific`. Divergence between extraction tag
and promotion tag becomes a calibration signal — surfaced in the
monthly audit.

This makes scope a first-class observable property with two human-
auditable checkpoints, rather than an implicit consequence of where
the file lives.

---

## 2. Principles (inherits parent + locked-definition implications)

All parent-design load-bearing rules (DESIGN §256–344) apply unchanged.
The following are restated because v2 touches them, or are new
implications of the locked definition.

1. **MC observes; agent does not self-report.** Same control-plane /
   data-plane split. Phase 4 reads the same `.jsonl` Scribe reads.

2. **Distiller is best-effort, never load-bearing.** Failure to distill
   never breaks the session, Scribe, completion logging, or memory
   write. Implementation MUST be a daemon thread (Seat 3 v1.1 Cond 9),
   not a sequential call.

3. **MC owns, agent proposes, human promotes.** v2 ships `proposed`
   mode only across all artifact types. `auto` mode (Phase 5) defers
   until v2 proposals reach reviewable quality.

4. **No new lock domains, no new write disciplines.** All v2 artifact
   writers route through `_atomic_write_text`; all per-project signal-
   store reads AND writes route through `_get_skill_stats_lock(project_id)`
   (parent Cond 6 v2; Seat 3 v1.1 Cond 8 wording fix carried forward).

5. **Cross-project default at extraction; blast-radius safety at
   promotion.** The combination preserves the parent's "auto-authored
   project-local only" rule while honoring the locked definition's
   cross-project default scope.

6. **One artifact, one human review, one promotion.** Whatever the
   artifact type (SKILL.md, UPDATE.md, EXPLORATION.md, PREFERENCE.md),
   it lands in `_proposed/`, the human reviews, promotion is a
   deliberate action with explicit scope (the dual-checkpoint).

7. **Curated-region MEMORY.md is still write-locked from the
   Distiller.** PREFERENCE.md proposals SUGGEST feedback memory
   additions; only the human promotion action causes a write to the
   memory dir. The Distiller never writes to MEMORY.md directly.

8. **Best-effort isolation between Scribe and Distiller threads.** A
   180s Distiller hang cannot delay Scribe's MEMORY.md write. Two
   independent daemon threads at session end, neither blocks the other.

---

## 3. The four-artifact model

v2 introduces a unified `_proposed/` artifact contract. Four
artifact types, one writer, one promotion UI, one human-gate
discipline.

```
data/skills/_proposed/
  ├── global/                           (cross-project scope tag)
  │   └── <YYYY-MM-DDTHH-MM-SS>-<fingerprint>/
  │       └── {SKILL.md | UPDATE.md | EXPLORATION.md | PREFERENCE.md}
  └── <project_id>/                     (project-specific scope tag)
      └── <YYYY-MM-DDTHH-MM-SS>-<fingerprint>/
          └── {SKILL.md | UPDATE.md | EXPLORATION.md | PREFERENCE.md}
```

Frontmatter shape (all artifact types):

```yaml
---
kind: skill | update | exploration | preference
name: <kebab-case-name>            # SKILL.md / EXPLORATION.md / PREFERENCE.md
target_skill: <existing-name>      # UPDATE.md only
extraction_scope: cross-project | project-specific | ambiguous
extraction_fingerprint: <hash>     # closed-vocabulary, §5.3
evidence_session_ids: [sid1, ...]  # 1..N
evidence_window_days: 30           # for recurrence-gated kinds
recurrence_count: <N>              # 1 for exploration; ≥distiller_min_recurrence for skill/update/preference
provenance: distilled               # always for Phase 4; manual / interactive for older artifacts
source_session: <first sid>         # the session that triggered the proposal
created_at: <iso8601>
---
```

### 3.1 Artifact kinds

| Kind | Trigger | Recurrence gate | Body shape | Promotion target |
|---|---|---|---|---|
| `skill` | New pattern observed across ≥N sessions | Yes (≥`distiller_min_recurrence`) | Anthropic-native SKILL.md (TRIGGER + procedure) | `~/.claude/skills/` (cross-project) OR `<project>/.claude/skills/` (project-specific) |
| `update` | Existing skill's behavior should change | No (UPDATE.md not recurrence-gated, parent Cond 2 v2 §1.2 close) | UPDATE.md schema (parent §420–438) | In-place edit to existing skill, scope inherited from target |
| `exploration` | Substantive external research path observed | **No** — single-shot retention | EXPLORATION.md (question / paths / what worked / why) | Read-floor archive: `~/.claude/explorations/` (cross-project) OR `<project>/.claude/explorations/` (project-specific) |
| `preference` | User behavior/preference observed across ≥N sessions | Yes | PREFERENCE.md (observed-preference + evidence quotes + suggested promotion target) | Feedback memory file in user's memory dir (cross-project) OR project-local `CLAUDE.md` addendum (project-specific) |

**Why two recurrence-gated kinds (skill/update/preference) and one
not (exploration):** explorations are *experience that happened*; the
purpose of retention is to skip future redo. A single exploration of
"how do CF Service Tokens behave with WebView2 cookie jars" has
retention value the moment it's recorded. Recurrence gating an
exploration would discard the experience until it happens 3 times —
which defeats the point. Skills, updates, and preferences are
*pattern synthesis*; they need recurrence evidence to avoid bottling
session-bound idiosyncrasies (Seat 2 v1.1 Cond 4).

### 3.2 Existing skill index for read-floor

EXPLORATION.md artifacts feed the read-floor at dispatch. Same
mechanism as the memory read-floor — `_build_agent_context` injects
top-K relevant explorations alongside the relevant memory snippets.
Selection is keyword-scored against the agent's initial prompt
(reuses `/api/skills/search` shape; semantic upgrade waits on Step 7
if/when it lands).

PREFERENCE.md artifacts, once promoted to feedback memory, feed the
existing memory read-floor automatically — no new injection
mechanism needed.

---

## 4. Mechanism

Phase 4 v2 runs at session end, parallel to Scribe, on the same
`.jsonl`. The session-end trigger is the existing
`_write_session_memory` (server.py:4699), invoked as a daemon thread
per Seat 3 v1.1 Cond 9.

### 4.1 Per-session extraction (multi-signal)

For each completed session passing the kill-switch gate (§4.6), the
Distiller emits zero or more signals across four signal classes:

```python
{
  "session_id": "<sid>",
  "ts": "<iso8601>",
  "scope_tag": "cross-project" | "project-specific" | "ambiguous",
  "signals": {
    "topics": [
      # Closed-vocabulary canonical phrases (§5.3) — feed skill/update aggregation
      {"fingerprint": "<hash>", "phrase": "<verb-noun-modifier>"},
      ...
    ],
    "preferences": [
      # User-expressed preferences observed in this session
      {"fingerprint": "<hash>", "summary": "<one-line>", "evidence_quote": "<verbatim>"},
      ...
    ],
    "explorations": [
      # Substantive external research paths observed in this session
      {
        "fingerprint": "<hash>",      # exploration uses fingerprint for dedupe only, not for recurrence
        "question": "<what was being investigated>",
        "paths_tried": ["<...>", ...],
        "outcome": "<what worked / why>",
        "tools_used": ["WebSearch", "WebFetch", "Grep", ...]
      },
      ...
    ]
  }
}
```

**The extraction prompt design has hard constraints** (Seat 1 v1.1
Cond 2):

- **Granularity floor:** "Do not emit topics at the language level
  (`python-code`), file-extension level, project name level
  (`clayrune-work`), or session-symptom level (`fixed-line-1158`)."
- **Granularity ceiling:** "Emit topics at the level of a *thing a
  future skill could be about*: a subsystem invariant, a recurring
  workflow, a gotcha class, a diagnostic procedure."
- **K cap:** at most 3 topics per session. Force the model to choose
  the most salient.
- **Closed-vocabulary topics only.** Topic `phrase` must match the
  verb-noun-modifier schema in §5.3. OOV → REFUSE the signal (don't
  emit a malformed one).
- **Few-shot examples drawn from real corpora.** Implementation
  prerequisite: hand-curate 5–7 example sessions from the
  diagnostic's 1,199-session window before turning extraction on at
  scale (Seat 1 v1.1 out-of-scope flag).
- **Scope tag default cross-project; narrow to project-specific only
  when evidence references project-local paths/files/configs.**
- **REFUSE path:** if nothing salient happened (a single-task session
  with no patterns, no preferences, no explorations), emit
  `{"signals": {"topics": [], "preferences": [], "explorations": []}}`.
  Empty is a valid output; padding noise is not.

### 4.2 Cross-session aggregation

Session signals are appended to per-project `_skill_stats.json` under
the per-project leaf lock. On each write, the Distiller checks gated
artifact emission:

- **For each topic fingerprint:** count distinct sessions within the
  rolling window (default 30 days, READ-TIME filter per Seat 1 v1.1
  Cond 3 — never purge, just filter). If count ≥
  `distiller_min_recurrence` AND no existing skill name match AND not
  in suppression list AND not in per-(project, fingerprint, day)
  dedupe window → emit SKILL.md candidate.
- **For each preference fingerprint:** same recurrence check (default
  threshold same as topics) → emit PREFERENCE.md candidate.
- **For each exploration fingerprint:** check ONLY the per-
  fingerprint suppression list AND per-(project, fingerprint, day)
  dedupe. No recurrence gating. If unsuppressed and not duplicated
  today → emit EXPLORATION.md candidate.

**Cross-project aggregation runs as a second pass.** A separate
function `_distill_cross_project_aggregate()` walks
`data/projects/*/(_skill_stats.json)` (existing files only — no new
global index, no new lock domain, per Seat 3 v1.1 out-of-scope flag).
For each topic / preference fingerprint that appears in ≥1 project's
recent signals, it checks: does this fingerprint recur across ≥2
projects? If so, the artifact's `extraction_scope` defaults to
`cross-project` regardless of any single-project tag; routed to the
global staging directory.

**Window semantics:** READ-TIME filter only. `_skill_stats.json` is
append-only (no purge). The aggregator computes recurrence at decision
time by filtering signals on `ts > now -
distiller_window_days`. Expose `fingerprints_near_threshold` in
`/api/distiller-stats` so the operator can see whether the threshold
is plausibly reachable (Seat 1 v1.1 Cond 3 telemetry).

### 4.3 EXPLORATION.md generation

Distinct path from recurrence-gated artifacts. When the extraction
emits an exploration signal AND no suppression/dedupe applies, the
Distiller immediately invokes a second cheap-model call to render
the EXPLORATION.md body:

```yaml
---
kind: exploration
name: <kebab-case-question-slug>
extraction_scope: cross-project | project-specific
extraction_fingerprint: <hash>
evidence_session_ids: [<sid>]
recurrence_count: 1
provenance: distilled
source_session: <sid>
created_at: <iso8601>
tools_used: [WebSearch, WebFetch, ...]
---

# <Question being investigated>

## Paths tried
- <path 1>: <result>
- <path 2>: <result>
...

## What worked
<the answer + why>

## What didn't work
<dead-ends — explicitly named so future agents skip them>

## When this applies
<one-line recognizing-the-trigger-condition>
```

**Why the explicit "what didn't work" section:** the locked
definition's "tried Y, Z worked" formulation calls out dead-end
retention as part of the experience. A future agent should not redo
a path the current agent already eliminated.

### 4.4 PREFERENCE.md generation

When a preference fingerprint crosses the recurrence gate, the
Distiller generates:

```yaml
---
kind: preference
name: <kebab-case-preference-slug>
extraction_scope: cross-project | project-specific
extraction_fingerprint: <hash>
evidence_session_ids: [<sid1>, <sid2>, ...]
evidence_window_days: 30
recurrence_count: <N>
provenance: distilled
source_session: <earliest sid>
created_at: <iso8601>
suggested_target: feedback_memory | project_claude_md | global_skill
---

# <The preference, as a one-line rule>

## Why (the underlying reason, if observable)
<extracted from session context>

## How to apply
<when this preference kicks in>

## Evidence
- Session <sid1> (<ts>): "<verbatim user quote>"
- Session <sid2> (<ts>): "<verbatim user quote>"
- Session <sidN> (<ts>): "<verbatim user quote>"

## Suggested promotion
- **Default:** new feedback memory at
  `~/.claude/projects/<...>/memory/feedback_<slug>.md` (cross-project)
  OR
  `<project>/CLAUDE.md` addendum (project-specific)
- Operator may also promote as a global SKILL.md if the preference
  contains an operating procedure rather than just a constraint.
```

**Body structure mirrors the existing feedback memory shape**
(rule → Why → How to apply) so promotion is a copy-edit, not a
rewrite. The body is generated to be promotable verbatim.

**Promotion of PREFERENCE.md is human-gated, like all other artifact
types.** A new `mc-distill` SKILL.md procedure step (§7.2) handles
the case where the user says "yes, promote that preference" — the
agent writes the suggested target file and updates MEMORY.md's
curated region (via the same `mc-changelog-update`-style append
discipline, NOT by rewriting prose). Cross-project preferences
promoted to a new feedback file get a one-line entry in MEMORY.md's
"Working with me — feedback" section.

### 4.5 SKILL.md / UPDATE.md generation

Same as v1.1 §3.4, with the proposal-generation prompt fully
specified per Seat 2 v1.1 Cond 1:

**Required prompt elements:**

1. **TRIGGER phrasing in description.** "Use when…" / "TRIGGER
   when…" — established built-in skill convention.
2. **Operating procedure, not summary.** "Do this, then this, then
   this" framing — extracts steps from the evidence, doesn't
   paraphrase what happened.
3. **Body budget ≤120 lines.** Mirrors `mc-distill/SKILL.md` line
   220 tone rule.
4. **At least one verbatim observation quote** from the aggregated
   evidence — prevents context drift.
5. **Explicit REFUSE path:** if the N observations are too
   heterogeneous to form a single coherent skill, output `REFUSE`
   and no proposal is written. Mirrors `_scribe_extract` thin/refusal
   guards.

UPDATE.md generation reuses parent design Cond 2 v2 schema
(`target_skill`, `target_files`, `target_action`, `target_rename`,
`diff`, `rationale`, `provenance`); v2 ships `target_action: edit`
only, same as parent.

### 4.6 Kill-switch gate (unified, fourth entry point enumerated)

Single function `_distiller_should_proceed(project_id, entry_point)
-> bool` (Seat 3 v1.1 Cond 1 enumeration fix). Returns `True` iff:

- `CONFIG.get('distiller_enabled_global', True)` — master kill
- `project.get('distiller_mode', 'proposed') != 'off'` — per-project
- `not session.get('incognito') and not session.get('housekeeping')` —
  inherits Scribe gating verbatim
- For `entry_point == 'cross_project_aggregate'`:
  `CONFIG.get('distiller_cross_project_enabled', True)` — independent
  cross-project kill (Seat 4 v1.1 Cond 5)
- For `entry_point == 'record_push'`: same per-project gate; gated-off
  return surfaces to caller (not silent — see below).

**Enumerated entry points** (unit test asserts each routes through
the gate, failing if a new one is added without registration):

1. `session_end_extract` — the parallel-Scribe extract pass at session end.
2. `proposal_generate` — the per-artifact-kind generation call.
3. `cross_project_aggregate` — the second-pass aggregation walking all
   projects.
4. `record_push` — `POST /api/project/<id>/distiller/record-push`
   from the in-session `mc-distill` skill (Seat 3 v1.1 Cond 1).
5. `auto_promote` — Phase 5 self-promotion path. Stubbed in v2; gate
   still enforced.
6. `dispatch_hint` — Phase 6 read-floor injection. Stubbed in v2;
   gate still enforced.

**`record_push` gated-off behavior:** returns `{accepted: false,
reason: "distiller_disabled"}` (NOT 404 — caller must be able to
detect the configuration state). The `mc-distill` skill surfaces this
to the user: "Suppression marker not written because the silent
Distiller is disabled globally / for this project."

### 4.7 Suppression and `Later` honoring

Inherits v1.1 §3.7 with the wording fix (Seat 3 v1.1 Cond 2):

> Both reads AND writes to `_skill_stats.json` go through the shared
> `_get_skill_stats_lock(project_id)`. The silent Distiller MUST hold
> this lock for the full read-recurrence-state → decide-to-propose →
> write-incremented-signal RMW. A partial lock domain (writes only)
> preserves the race Cond 6 v2 was added to prevent.

**Cross-artifact suppression:** suppression markers are keyed on
`(fingerprint)` alone (not on artifact kind). A user who says `No` to
a SKILL.md proposal for fingerprint F also suppresses an
EXPLORATION.md or PREFERENCE.md proposal for that same fingerprint
— "no" means "this pattern is not worth bottling in any form," not
"this specific artifact type." This matches user mental model and
avoids the variant where saying `No` to a SKILL.md gets re-surfaced
the next day as an EXPLORATION.md.

**`Later` semantics:** `wait_until_recurrence: <current_count + 1>`
stub (v1.1 §3.7 inherited). Honest about what the system can promise.

**Phase 1 ↔ Phase 4 fingerprint mismatch at the suppression seam
(Seat 1 v1.1 OOS flag, Cond 4):** the in-session `mc-distill` and
the silent Distiller now share the same closed-vocabulary
fingerprint schema (§5.3). Both extract using the same prompt
template (the Distiller's extraction prompt is bundled with the
`mc-distill` SKILL.md as a reference appendix; the in-session agent
follows it when generating its proposal fingerprint). Mismatch
risk is reduced to model-output variance within a constrained
vocabulary — much smaller than v1.1's two-independent-fingerprints
gap.

### 4.8 Concurrency model (explicit)

**Daemon thread, not sequential** (Seat 3 v1.1 Cond 9):

```python
# in _write_session_memory (server.py:4699), after Scribe dispatch
if _distiller_should_proceed(project_id, "session_end_extract"):
    threading.Thread(
        target=_distill_extract_and_aggregate,
        args=(project_id, sid, jsonl_path),
        daemon=True,
        name=f"distiller-{project_id}-{sid}"
    ).start()
```

Matches Step 6's `_checkpoint_worker` pattern at server.py:4808.
Distiller hang/timeout/crash cannot delay Scribe's MEMORY.md write.

**Hard-kill recovery ordering** (Seat 3 v1.1 Cond 10, Option A):

Session signals commit to `_skill_stats.json` (under the lock)
BEFORE the proposal-generate cheap-model call begins. Idempotent at
the signal layer: each session contributes exactly one set of signals
per fingerprint regardless of partial proposal-generate state. On
hard-kill mid-proposal-generate, next-session aggregation re-evaluates
the fingerprint and retries proposal generation if recurrence still
meets the threshold. No startup reconciler required.

### 4.9 Atomic writes (substrate)

All `_skill_stats.json` writes route through `_atomic_write_text`
(Seat 3 v1.1 Cond 4). `_scribe_stat` cited only for counter
key/increment SHAPE, not for I/O pattern. Torn-write recovery
posture: if `_skill_stats.json` fails JSON parse on read, emit a
structured warning log line (mirror `distiller_cost_cap_hit` shape).
Never silently re-initialize.

All `_proposed/<...>/<artifact>.md` writes use `.tmp + rename`
(parent Cond 8 v2). Phase 3 audit GC removes any `.tmp` older than
24h.

---

## 5. Closed-vocabulary fingerprint scheme

Replaces v1.1 §3.3's two-stage bag-of-tokens approach (Seat 1 v1.1
Cond 1: the worked example failed its own algorithm). v2 closes the
synonym-and-omission variance class that bag-of-tokens cannot.

### 5.1 The vocabulary

**Verbs (closed list):** `add, archive, audit, backfill, build,
cleanup, configure, debug, delete, deploy, design, diagnose, document,
edit, enable, expose, extract, fix, gate, generate, ignore, index,
inject, install, lint, migrate, monitor, normalize, package, parse,
pin, propagate, propose, query, refactor, register, remove, rename,
research, restore, retry, revert, route, run, schedule, scope, search,
seed, send, ship, sign, simplify, skip, sort, split, sync, test,
trace, unify, update, validate, write`

**Nouns (closed list, project-agnostic):** `agent, alert, artifact,
audit-log, auth, backlog, binding, build, cache, callback, certificate,
config, context, dashboard, dependency, deploy, diff, dispatch, doc,
endpoint, env-var, error, event, exception, feature-flag, fingerprint,
form, frontmatter, handler, hash, header, hivemind, hook, identity,
incident, indicator, integration, lock, log, marker, memory, message,
metadata, middleware, migration, mode, model, module, notification,
output, package, pair, parser, path, payload, permission, pipeline,
plan, prompt, provider, push, queue, race, read-floor, record, refresh,
regex, render, report, request, resource, response, route, schema,
schedule, scope, screenshot, script, secret, session, settings, signal,
sidecar, signal, skill, sort-order, source, spec, state, status,
stream, suppression, table, target, telemetry, template, terminal,
test, threshold, throttle, timeout, token, tool, transcript, transport,
trigger, ui, update, upload, user, validation, view, watermark, web,
window, worker, workflow, write`

**Modifiers (optional, closed list, scope-narrowing):** `mobile,
desktop, ios, android, web, cli, server, client, frontend, backend,
agent-side, mc-side, condense, scribe, distiller, hivemind, schedule,
pair, mobile-pair, github-sync, project-sync`

**Project-name modifiers:** rejected. A topic that requires a
project-name modifier to be disambiguated is project-specific by
construction; its scope tag is `project-specific` and the project_id
itself disambiguates without polluting the fingerprint vocabulary.

### 5.2 Stage 1 extraction with vocabulary constraint

The extraction prompt includes the full vocabulary in the system
context. The model MUST emit topic phrases in the form
`<verb>-<noun>[-<modifier>]`. OOV verbs/nouns are rejected (the
signal is discarded, with telemetry — `vocabulary_miss` counter).

**Few-shot:** 5–7 example sessions from the diagnostic's 1,199-
session corpus, hand-curated at implementation time. Examples must
span all four signal classes (topics, preferences, explorations,
mixed) and demonstrate scope-tag selection.

**Maintenance:** vocabulary is rev-locked at v2 ship. Drift signal:
the `vocabulary_miss` counter on `/distiller-stats`. If a verb or
noun is missed >5% of the time over a 2-week window, the audit
checklist surfaces a "vocabulary tune" backlog item. Vocabulary
revisions are a deliberate human action, propagated via the same
file-hash-marker pattern `mc-distill/SKILL.md` uses.

### 5.3 Stage 2 normalization

Deterministic, testable, pure function:

```python
def fingerprint(phrase: str) -> str:
    parts = phrase.lower().strip().split('-')
    verb = parts[0]
    noun = parts[1] if len(parts) > 1 else ''
    modifier = parts[2] if len(parts) > 2 else ''
    if verb not in VERBS or noun not in NOUNS:
        return None  # OOV → caller increments vocabulary_miss
    if modifier and modifier not in MODIFIERS:
        modifier = ''  # ignore unknown modifier; don't fail
    canonical = f"{verb}-{noun}" + (f"-{modifier}" if modifier else "")
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]
```

**Why this collapses the synonym variance that bag-of-tokens missed:**
the vocabulary IS the synonym normalization layer. `use` and `prefer`
both must collapse to `use` (or `select`, or whichever is in the
vocab) at extraction time, because the model is constrained to emit
only vocab terms. The collapse happens UPSTREAM of the hash, where
it has the model's semantic understanding behind it, rather than
downstream where the hash sees only token literals.

**Belt-and-suspenders telemetry** (Seat 1 v1.1 Cond 1, option c):
emit a `fingerprint_near_miss` log for every new fingerprint, listing
the top-3 closest existing fingerprints by Levenshtein distance on
the canonical string. If near-misses dominate, the vocabulary needs
tuning.

### 5.4 Cross-project fingerprint stability (Seat 1 v1.1 Cond 4)

With closed-vocabulary, the same operator pattern phrased in two
projects produces the same fingerprint because both extractions are
constrained to the same vocab. The "project mc says `mc-distill`
natural-breakpoint" vs "project soccer-intel says distillation-skill
session-end" variant from Seat 1 Cond 4 dissolves: both projects'
extractions would converge on something like `propose-skill-distiller`
or `gate-skill-natural-breakpoint`, hashable identically.

This is the v2 fix for the cross-project surface that v1.1 had to
ship behind a flag.

---

## 6. mc-distill v2 (Phase 1 reset)

Single SKILL.md edit, internally consistent, ships ahead of Phase 4
backend.

### 6.1 The Procedure / Tone / Disposition reconciliation

v1.1's softening dropped three constraints but left the SKILL.md
internally inconsistent (Seat 4 v1.1 OOS flag): the §Procedure step
2 still references "Recurs or is likely to recur"; the §Tone still
says "err toward asking less"; the §Disposition paragraph reverses
the tone. v2 ships a clean, end-to-end consistent revision.

**Decisions (closing v1.1 open question (a)):**

| Rule | v1.1 (current on disk) | v2 (this spec) |
|---|---|---|
| Within-session recurrence ≥2 | **Dropped.** Phase 4 catches cross-session. | **Keep dropped.** Phase 4 v2 catches it. |
| Once-per-session cap | **Dropped.** | **RESTORED.** One proactive push max per session. |
| Natural breakpoint | Kept. | Kept. |
| Specificity bar | Kept and strengthened. | Kept; further strengthened per Seat 2 Cond 4 to discriminate session-bound from pattern-bound. |
| Not-already-covered | Kept. | Kept. |
| Tone | "err toward asking less" (inconsistent with reversed disposition) | **Removed.** Replaced with the disposition language directly: "if you noticed something worth bottling at a natural breakpoint, say so." No tonal duplication. |
| Disposition | Reversed in §Disposition; old tone left in §Tone | **Single source of truth.** Disposition lives in one place; no contradicting paragraph elsewhere. |
| Procedure step 2 ("Decide if there's a pattern") | "Recurs or is likely to recur" (contradicts §1.2 softening) | **Rewritten.** Bar: "novel insight, specific enough to be a future skill, not already covered." No recurrence language — Phase 4 owns recurrence. |

**Why restore the once-per-session cap (v1.1 open question (a)):**

1. **Seat 2 v1.1 Cond 2 was right:** v1.1 conflated three changes.
   The diagnostic motivating the softening pinpointed within-session-
   recurrence as the structural inhibitor, not the cap. Keeping the
   cap isolates the experiment.
2. **Phase 4 v2 catches more.** With external explorations, user
   preferences, and cross-project default scope, Phase 4 v2 is far
   more aggressive than v1.1. Phase 1's residual role shrinks to
   "single, hard-won, in-the-moment novel insight per session." The
   cap fits the residual role.
3. **Adversarial post-debug specificity (Seat 2 v1.1 Cond 4)** is
   the highest-risk failure mode for a cap-less Phase 1. Restoring
   the cap limits the worst case to one bad proposal per session
   rather than five.
4. **Multiple distinct insights per session is rare.** v1.1 §3.8
   noted it as "rare but possible." Rare-but-possible doesn't justify
   the volume risk; if it happens, the user can invoke `/distill`
   explicitly for the second insight.

The cap is therefore restored. The natural-breakpoint + strengthened-
specificity + no-duplicates + cap-of-1 combination is the v2 Phase 1
posture.

### 6.2 Strengthened specificity bar (Seat 2 v1.1 Cond 4)

Add to the §Proactive subsection:

> **Specificity test (must pass before surfacing):** rewrite the
> proposal's TRIGGER phrasing without naming the specific session's
> symptom. Does anything reusable remain? If the only recognizable
> trigger is "I am currently debugging the exact thing you debugged,"
> the proposal is session-bound, not pattern-bound, and should not
> be surfaced. Pattern-bound: "when CF Access tokens lag in 'last
> used' display, check session_duration first (logs are stale by
> design)." Session-bound: "when the Gemini SSE pill stuck on
> COMPLETED today, check turn_complete — 40min debug." The former is
> a skill; the latter is a war story.

### 6.3 Rollback paths (Seat 4 v1.1 Cond 4)

New §Rollback procedure subsection:

**Path 1 — Hot revert (hours-old, no user edits expected):**
`git revert <v2-softening-commit>` → restart MC. `install_builtins()`
auto-propagates via hash-marker. Verified clean.

**Path 2 — Cold revert (days/weeks-old, possible user edits):**
`git revert <commit>` → restart MC. Some users on hash-divergent
installed copies will keep the v2 text (skills.py "preserved"
branch). Audit checklist (Phase 3) grows a "check installed
mc-distill matches source" entry. Operator decision per-user whether
to force-update.

**Path 3 — Hard revert (force-update all):** `install_builtins()`
gains a `--force` mode (one-line code change, deferred to Phase 5
audit work) OR document manual file deletion as the fallback. v2
spec ships with the manual-deletion fallback documented; `--force`
mode is a separate backlog item.

### 6.4 The new `record-push` call

The v1.1 SKILL.md was honest that "`Later` ≡ `No` until backend
ships." With Phase 4 backend shipping, the SKILL.md updates to call
`POST /api/project/<id>/distiller/record-push` on No and Later. The
in-session agent surfaces the response:

- `{accepted: true, ...}` → "Won't propose this pattern again in this
  session."
- `{accepted: false, reason: "distiller_disabled"}` → "Suppression
  marker not written — the silent Distiller is disabled. `No` will
  not stick."

This closes the "Later is a false promise" issue (parent Cond 3 v2,
v1.1 §3.7) and the kill-switch enumeration issue (Seat 3 v1.1 Cond 1)
in one edit.

---

## 7. Reuse map

| Phase 4 v2 piece | Reuse from | Site |
|---|---|---|
| Session-end trigger | `_write_session_memory` (Leg A) | server.py:4699 |
| Daemon-thread dispatch pattern | `_checkpoint_worker` (Step 6) | server.py:4808 |
| Cheap-model call wrapper | `_scribe_call` | server.py:5244 |
| Transcript reader | `_scribe_render_transcript` | server.py:5205 |
| Extraction/render guards (REFUSE path) | `_scribe_extract` precedent | server.py:5263 |
| Per-project leaf lock | mirror `_get_mem_write_lock` | server.py:849 |
| Atomic write | `_atomic_write_text` | server.py:859 |
| Counter writer SHAPE (not I/O) | mirror `_scribe_stat` | server.py:5126 |
| Telemetry endpoint shape | mirror `/scribe-stats` | server.py:1809 |
| `load_projects` exclusion site | append `_skill_stats.json` | server.py:1158 |
| Skill search (read-floor injection) | `/api/skills/search` | (existing) |

**Genuinely new code:**

1. `distiller.py` — module with the multi-signal extraction
   (`_distill_extract`), aggregation (`_distill_aggregate`),
   cross-project aggregation (`_distill_cross_project_aggregate`),
   per-kind proposal generation (`_distill_render_skill`,
   `_distill_render_exploration`, `_distill_render_preference`,
   `_distill_render_update`), kill-switch gate
   (`_distiller_should_proceed`), closed-vocabulary fingerprint
   (`_fingerprint`), vocabulary constants.
2. `_skill_stats.json` schema + writers + lock helper +
   atomic-write integration.
3. Server endpoints:
   - `POST /api/project/<id>/distiller/record-push` (gated through
     kill-switch; returns `{accepted, reason}`).
   - `GET /api/project/<id>/distiller-stats` (mirrors `/scribe-stats`;
     exposes `cost`, `cap_hits`, `vocabulary_miss`,
     `fingerprints_near_threshold`, per-kind proposal counts).
   - `GET /api/distiller/_proposed` (lists across all projects + global
     staging; powers the unified Skills panel review surface).
4. Hook into `_write_session_memory` (one daemon-thread dispatch
   alongside the Scribe dispatch; gated).
5. Hook into `_build_agent_context` for EXPLORATION.md read-floor
   injection (top-K explorations alongside the memory read-floor).
6. `mc-distill` SKILL.md v2 edit (consistency + record-push + rollback
   subsection).
7. Tests:
   - `tests/test_load_projects_sidecar_exclusions.py` (new file, Seat
     4 v1.1 Cond 3 placement).
   - `tests/test_distiller_kill_switch_enumeration.py` (asserts all 6
     entry points route through `_distiller_should_proceed`).
   - `tests/test_distiller_fingerprint.py` (pure-function tests for
     vocabulary constraint + closed-vocab collapse).
   - `tests/test_distiller_lock_contract.py` (asserts read-side lock
     acquisition).

Estimate: **~700–900 lines of new code** (vs. v1.1's ~400–600). The
increase comes from the four-artifact model (each kind needs its
own renderer + frontmatter handling) and the closed-vocabulary
infrastructure. The exploration/preference paths reuse the same
lock/atomic-write/telemetry surfaces, so the per-kind overhead is
incremental, not multiplicative.

---

## 8. Minimum viable cut (v2 ships)

**In scope:**

- Multi-signal extraction (topics + preferences + explorations) per
  §4.1, with closed-vocabulary constraint per §5.
- Cross-session aggregation with READ-TIME window per §4.2.
- Cross-project second-pass aggregation per §4.2 (gated independently).
- All four artifact kinds (SKILL/UPDATE/EXPLORATION/PREFERENCE) per §3.
- Scope-tagged routing (global vs per-project staging) per §1.1.
- Dual-checkpoint scope tagging (extraction tag in frontmatter +
  promotion UI override) per §1.5.
- Unified kill-switch with 6 enumerated entry points per §4.6.
- Suppression / Later honoring with cross-artifact keying per §4.7.
- Daemon-thread concurrency per §4.8.
- Atomic writes for `_skill_stats.json` and `_proposed/*` per §4.9.
- Closed-vocabulary fingerprint + near-miss telemetry per §5.
- mc-distill v2 SKILL.md edit (consistency + cap restoration +
  record-push + rollback subsection) per §6.
- DATA_DIR regression test (Seat 4 v1.1 Cond 3 placement).
- Cost-cap structured log + counter (Seat 4 v1.1 Cond 1).
- EXPLORATION.md read-floor injection at dispatch (`_build_agent_context`).

**Out of scope (deliberately deferred):**

- **`auto` mode (Phase 5).** Gates on v2 producing reviewable
  proposals at acceptable rate + quality across all four kinds.
- **Dispatch-time skill hint v2 (Phase 6, bge-m3).** Independent track.
  v1 keyword scoring ships with v2 for EXPLORATION.md read-floor; SKILL
  hint deferred.
- **Audit checklist extension (Phase 3).** Folds in once v2 has 2–4
  weeks of organic stats.
- **`auto`-mode rollback discovery surface (parent Cond 11 v2).**
  Gates Phase 5.
- **Vocabulary auto-tuning.** Vocabulary revisions are manual in v2.
- **PATCH.md schema for external-agent patches (parent open item #8).**
  Independent track.
- **HIVEMIND interaction.** Inherited deferral from parent.

---

## 9. Closed open questions

The three open questions from the v2 redesign brief, settled in
this spec:

### (a) Restore mc-distill's once-per-session cap?

**SETTLED: RESTORE.** Rationale in §6.1. The cap is consistent with
Phase 1's residual role (single hard-won novel insight per session)
under Phase 4 v2's expanded coverage, and isolates Seat 2 v1.1
Cond 2's conflated-experiment concern.

### (b) Stage 2 fingerprint approach — closed vocabulary / embeddings / ship-with-telemetry?

**SETTLED: closed-vocabulary Stage 1 + near-miss telemetry as
belt-and-suspenders.** Rationale in §5. Embeddings (Step 7) remain
on the back burner per
`memory/decision_step7_semantic_search_deferral.md`. Closed
vocabulary is deterministic, testable, immediately implementable,
and addresses Seat 1 v1.1 Cond 1's structural failure directly. The
near-miss telemetry catches drift if the vocabulary needs revision.

### (c) mc-distill internal-consistency cleanup?

**SETTLED:** §6 specifies a single end-to-end consistent revision.
§Procedure step 2 rewritten (no recurrence language). §Tone removed
(disposition lives in one place). §Specificity strengthened with the
pattern-bound-vs-session-bound test. §Rollback subsection added.
§record-push call added. Ships as one SKILL.md edit alongside the v2
backend, gated behind the same MC restart so the SKILL.md and the
endpoint go live together (Seat 2 v1.1 Cond 3 option A — controlled
experiment).

---

## 10. Inherited conditions from v1.1 committee

The v1.1 committee returned 14 must-fix-in-design + 4
must-fix-in-implementation + 2 soak-gate conditions. v2 explicitly
addresses each:

**v1.1 must-fix-in-design (status in v2):**

| # | v1.1 condition | v2 status |
|---|---|---|
| 1 | Stage 2 too narrow | RESOLVED in §5 (closed vocabulary) |
| 2 | Extraction prompt no granularity bound | RESOLVED in §4.1 (granularity floor/ceiling + K cap + closed vocab) |
| 3 | Rolling-window semantics unspecified | RESOLVED in §4.2 (read-time filter, append-only, near-threshold telemetry) |
| 4 | Cross-project surface inherits C1/C2 silently | RESOLVED in §5.4 (closed-vocab collapses cross-project variance); cross-project is now the *default*, not a separate surface |
| 5 | §3.4 proposal-generation prompt unspecified | RESOLVED in §4.5 (5 required prompt elements) |
| 6 | Softening conflates three changes | RESOLVED in §6.1 (cap restored; experiment isolated) |
| 7 | record-push omitted from kill-switch enumeration | RESOLVED in §4.6 (6 enumerated entry points including record-push) |
| 8 | Shared-lock language covers writers only | RESOLVED in §4.7 (reads AND writes go through lock; explicit RMW span) |
| 9 | Parallel-Scribe threaded vs sequential | RESOLVED in §4.8 (daemon thread; explicit) |
| 10 | Hard-kill recovery order undefined | RESOLVED in §4.8 (Option A: signal commits before proposal-generate) |
| 11 | Cross-project surface UI affordance undefined | RESOLVED in §1.1 (cross-project IS the default flow; ships in main Skills panel via `/api/distiller/_proposed`) |
| 12 | Phase 1 softening rollback path undocumented | RESOLVED in §6.3 (three paths documented; --force as backlog) |
| 13 | Cross-project surface kill switch missing | RESOLVED in §4.6 (`distiller_cross_project_enabled` global gate) |
| 14 | Config-key scope per-project vs global ambiguous | RESOLVED in §11 (explicit per-project/global classification) |

**v1.1 must-fix-in-implementation (carry forward to v2 backend commit):**

| # | v1.1 condition | v2 status |
|---|---|---|
| 15 | Cost-cap structured log + counter on cap-hit | INHERITED — v2 §8 in-scope (overrides v1.1 §5 defer) |
| 16 | `_skill_stats.json` writes use `_atomic_write_text` | INHERITED — v2 §4.9 explicit |
| 17 | DATA_DIR regression test placement | INHERITED — v2 §7 names `tests/test_load_projects_sidecar_exclusions.py` |
| 18 | Observability of softening experiment during trough | RESOLVED by v2 design choice — softening propagates ONLY when v2 backend ships (Seat 2 v1.1 Cond 3 option A) |

**v1.1 soak-gate (carry forward to v2 audit checklist when Phase 3 lands):**

| # | v1.1 condition | v2 status |
|---|---|---|
| 19 | Recurrence threshold default = 3 | INHERITED. `fingerprints_near_threshold` telemetry shipped. Revisit at 4-week soak. |
| 20 | Adversarial post-debug spurious-specificity | RESOLVED at design level in §6.2 (strengthened specificity bar). Still soak-gate the empirical specimens at 4-week mark. |

---

## 11. Implementation anchors and config-key classification

Per-project vs global, addressing Seat 4 v1.1 Cond 6:

**Global (in `_CONFIG_EDITABLE_KEYS`, Settings-UI-exposed):**

- `distiller_enabled_global` (default `true`) — master kill.
- `distiller_cross_project_enabled` (default `true`) — cross-project
  aggregation kill (Seat 4 v1.1 Cond 5).
- `distiller_model` (default `''` → haiku) — cheap-model identifier.
- `distiller_window_days` (default `30`) — recurrence rolling window.
- `distiller_cost_cap_tokens_per_project_per_day` (default `100000`,
  parent Cond 9 v2).

**Per-project (stored on project record, project Settings modal):**

- `distiller_mode` (default `'proposed'`) — `off | proposed | auto`
  (auto reserved for Phase 5).
- `distiller_min_recurrence` (default `3`) — for skill/update/preference
  artifact kinds (exploration not gated).
- `distiller_min_turns` (default `5`) — minimum session length to
  trigger.
- `distiller_skip_errors` (default `true`) — skip `_(error)_` and
  `_(stopped)_` sessions.

**Implementation anchors:**

| Anchor | Site | v2 change |
|---|---|---|
| `_write_session_memory` | server.py:4699 | Add daemon-thread dispatch to `_distill_extract_and_aggregate`, gated. |
| `load_projects` exclusion | server.py:1158 | Append `_skill_stats.json` to the tuple. |
| Lock pattern | `_get_mem_write_lock` server.py:849 | Mirror as `_get_skill_stats_lock`. |
| Atomic writer | `_atomic_write_text` server.py:859 | Reuse verbatim. |
| Cheap-model wrapper | `_scribe_call` server.py:5244 | Reuse verbatim. |
| Telemetry endpoint | `/scribe-stats` server.py:1809 | Mirror as `/distiller-stats`. |
| Read-floor injection | `_build_agent_context` | Add EXPLORATION.md top-K alongside memory read-floor. |
| Global config | `_CONFIG_EDITABLE_KEYS` server.py:11115 | Add the 5 global keys above. |
| Project record | project JSON schema | Add the 4 per-project keys above. |

New module: `distiller.py` (mirrors `project_sync.py` / `github_sync.py`
pattern — born outside `server.py` per `MAINTENANCE_PROTOCOL.md` Rule 1).

---

## 12. Committee posture and open questions for v2 review

Per parent design discipline (mirroring Memory System SPEC §3.A.MID
post-committee + Leg C 2026-05-18 ratification): **no backend code
lands until this spec passes committee review.** v1.1's ratification
covered the narrower framing; v2's scope shifts (cross-project default,
four-artifact model, user-facing learning, exploration retention)
warrant a fresh four-seat pass.

**Recommended seats (same structure as v1.1 committee):**

- **Seat 1 — Pattern integrity & vocabulary.** Reviews §4.1
  extraction prompt, §4.2 aggregation, §5 closed-vocabulary
  scheme (including the vocab lists themselves — are they the right
  coverage for the observed corpora?), cross-project fingerprint
  stability (§5.4), `fingerprint_near_miss` telemetry sufficiency.
- **Seat 2 — Agent behavior & proposal quality across four
  artifact kinds.** Reviews per-kind proposal generation prompts
  (§4.3, §4.4, §4.5), the cross-artifact suppression decision
  (§4.7), the once-per-session cap restoration (§6.1), the
  strengthened specificity bar (§6.2). Specific worry: does
  PREFERENCE.md generation produce promotable feedback-memory
  bodies, or does it produce content that always needs a human
  rewrite?
- **Seat 3 — Concurrency, lifecycle, atomicity.** Reviews §4.6
  unified kill-switch (six entry points), §4.7 reads-take-the-lock
  contract, §4.8 daemon-thread + ordering, §4.9 atomic substrate.
  Specific worry: cross-project aggregation walks all projects'
  `_skill_stats.json` files — what's the lock discipline when
  walking? Does it need per-project locks serially, or is the
  walk read-only and lock-free?
- **Seat 4 — Config, ops, rollback, cost, scope-tag UX.** Reviews
  §11 per-project/global classification, §6.3 rollback paths
  (especially Path 3's deferred `--force`), §8 in-scope cost-cap
  observability, the dual-checkpoint promotion UI affordance (§1.5).
  Specific worry: with four artifact kinds and a scope tag with
  optional override, the promotion UI is meaningfully more complex
  than v1.1's single-kind-single-scope. Where does the complexity
  live — in the Skills panel, in a new review surface, in CLI?

**Open questions for v2 committee:**

1. **Vocabulary coverage.** §5.1 lists verbs/nouns drawn from
   adjacency to the observed work. Is it the right shape, or does
   it miss a category that will produce >5% `vocabulary_miss`
   right out of the gate? Spec proposes empirical tuning via
   telemetry; committee should sanity-check the v1 lists against
   their own anticipated patterns.
2. **PREFERENCE.md promotion target ambiguity.** §3 lists three
   suggested targets (feedback memory / project CLAUDE.md / global
   SKILL.md). Should the extraction model pick one, or should it
   always default to feedback memory and let the human override at
   promotion? Spec currently lets extraction suggest; might be
   over-engineering.
3. **EXPLORATION.md read-floor budget.** §3.2 adds top-K
   explorations to the read-floor at dispatch. Combined with the
   existing memory read-floor, total context-window cost grows.
   Should there be a separate `exploration_topk` config, default
   smaller than `read_floor_topk`?
4. **Cross-project aggregation cost.** The second-pass aggregation
   walks all projects' `_skill_stats.json` files at every session
   end. With ~30 projects, this is 30 file-reads per session-end.
   Acceptable? Or does it want a cache?
5. **Dual-checkpoint divergence as audit signal.** §1.5 says
   extraction-tag-vs-promotion-tag divergence becomes a calibration
   signal in the monthly audit. Spec doesn't say what action the
   audit takes when divergence exceeds a threshold. Is silent
   observation sufficient, or does it want an actionable surface?

---

## 13. Build sequence reminder

Per `memory/decision_learning_definition.md` "Build order locked
(2026-05-27)":

1. **Fix condense first.** 58 timeouts + 48 errors per 2026-05-23
   diagnostic. The memory-refinement half of the existing learning
   loop is degrading and must be working before adding a second
   learning layer on top.
2. **This spec (v2).** Drafted now.
3. **Committee review of v2.** Four-seat pass against this doc.
4. **Build.** Single bundled PR per v1.1 §post-spec recommendation;
   ~700–900 LOC per §7.

**No backend code lands until condense is fixed AND v2 has cleared
committee.** Same discipline as parent design v2 and Memory System
§3.A.MID.

---

## Committee review (2026-05-27) — RATIFY-WITH-CONDITIONS

Four-seat review dispatched against this spec + the locked-learning
definition + each seat's v1.1 predecessor assessment. Full per-seat
assessments preserved at
`docs/_committee/SKILLS_CURATION_PHASE4_V2_seat<N>_*.md` (scratch
intended for synthesis into this doc; the per-seat files remain on
disk for traceability).

**Unanimous: RATIFY-WITH-CONDITIONS. No blockers. No data-loss path.**
Each seat returned independently:

| Seat | Decision | Conditions |
|---|---|---|
| 1 — Pattern integrity & vocabulary | RATIFY-WITH-CONDITIONS | 4 design + 2 implementation |
| 2 — Agent behavior & proposal quality | RATIFY-WITH-CONDITIONS | 3 design + 1 implementation (+ 1 ratify note on cap restoration) |
| 3 — Concurrency, lifecycle, atomicity | RATIFY-WITH-CONDITIONS | 7 design |
| 4 — Config, ops, rollback, cost | RATIFY-WITH-CONDITIONS | 6 design + 2 implementation |

After deduplication on convergent issues, the synthesized list below
collapses several seat-conditions where two or three seats independently
flagged the same failure path.

### Cross-seat convergences (issues flagged by multiple seats independently)

These are the highest-confidence findings — multiple independent
reviewers reached the same conclusion from different angles.

**C-A: EXPLORATION.md needs an explicit per-session cap** (Seat 2
Cond 1, Seat 3 Cond 6). EXPLORATION.md bypasses recurrence gating
per §4.2; the §4.1 K=3 cap explicitly applies to *topics* only; a
research-heavy session emits 8+ explorations under the per-project
lock (~80s of serialized Distiller-thread runtime), and each lands
in the human review queue. Fix: `distiller_max_explorations_per_session`
(default 3, per-project) added to §11; §4.1 K-cap rewritten to bind
both topics AND explorations; §4.3 documents drop discipline beyond
the cap.

**C-B: Cross-artifact suppression must be keyed on `(fingerprint, kind)`,
not `(fingerprint)` alone** (Seat 2 Cond 3, Seat 3 Cond 5). Seat 2
isolated the *user-judgment* problem (a "no to SKILL" is a quality
call; a "no to PREFERENCE" is a privacy/accuracy call — collapsing
them produces both false positives and false negatives). Seat 3
isolated the *concurrency* version (TOCTOU between suppression-marker-
write and a mid-flight artifact's generate→write cycle). Both point
to the same fix: re-key §4.7 suppression on `(fingerprint, kind)` for
`{skill, exploration, preference}`; UPDATE inherits from SKILL.
Seat 3 also requires the lock domain to extend through the artifact-
generation check-then-write cycle, not just the recurrence-state RMW.

**C-C: Cross-project recurrence composition is operator-unpredictable**
(Seat 2 OOS, Seat 3 OOS, Seat 4 Cond 5). §4.2's cross-project aggregator
uses "≥2 projects" but doesn't say whether each project's signals must
clear THAT project's `distiller_min_recurrence` to contribute. With per-
project thresholds varying (3, 5, 2), composition is ambiguous.
Recommended fix (Seat 4): each project's signals must clear their own
threshold to contribute to cross-project count. Spec-edit one paragraph
in §4.2.

**C-D: Cross-project aggregation lock discipline + cost shape unresolved**
(Seat 1 Cond 5, Seat 3 Cond 1, Seat 3 Cond 2, Seat 4 OOS). §4.2 punts
both the lock discipline (serial per-project locks vs lock-free with
retry) and the thread topology (inline vs separate daemon vs scheduled)
and the cost shape (re-walks all projects per session-end = O(N×file_size)).
Fix combines: (i) Seat 3 Cond 1 picks serial-per-project-lock OR
lock-free-with-retry — spec must say which; (ii) Seat 3 Cond 2 picks
inline-after-per-project (recommended, preserves cap=2 semaphore); (iii)
Seat 1 Cond 5 adds `_skill_stats_summary.json` cache + bounded walk
frequency (debounced ~10min or every-5-session-ends). All three are
one spec section.

**C-E: Closed-vocabulary lists are under-fitted to this codebase**
(Seat 1 Cond 1, Seat 1 Cond 2, with Seat 4 OOS flagging the
vocabulary-revision rollback path). Seat 1 sampled ~200 commits and
found ~17 durably-missing verbs (`revoke, mint, harden, preflight,
enrich, materialize, paginate, wire, instrument, daemonize, swap,
replace, redact, rebrand, bundle, polish, interpret`). Worse, the
most-discussed subsystems (`condense, scribe, distiller, hivemind,
pair, mobile-pair, github-sync, project-sync`) appear ONLY in the
modifier list — but §5.3's strict positional parser returns `None`
when they appear as the grammatical noun. Silent loss on exactly the
topics this design exists to detect. Fix: pre-ship vocabulary
enrichment from the diagnostic corpus + promote subsystem terms from
MODIFIERS to NOUNS.

**C-F: DATA_DIR regression test must be parametric** (Seat 1 Cond 6,
Seat 4 Cond 6). Both seats independently arrived at the same fix: the
test parameterizes over the full sidecar exclusion tuple AND includes
a "next sidecar" canary (a synthetic `_future_telemetry.json` that
forces the next contributor adding a sidecar to update the tuple).
Single source of truth: an `EXCLUDED_SIDECAR_SUFFIXES` constant in
`server.py` that both `load_projects()` and the test import.

**C-G: In-session push fingerprint must be server-side re-normalized**
(Seat 1 OOS, Seat 3 OOS). Both flag that v2 §4.7's "Distiller's
extraction prompt is bundled with mc-distill SKILL.md as a reference
appendix; the in-session agent follows it when generating its proposal
fingerprint" relies on the in-session agent to apply the closed-vocab
constraint correctly without a parser in the loop. Fix: the
`POST /api/project/<id>/distiller/record-push` endpoint re-runs the
agent's submitted `phrase` through the server-side `_fingerprint()`
function, keying the suppression marker on the server-derived hash.
Then both Phase 1 and Phase 4 share a single fingerprint source of
truth.

---

### Must-fix-in-design (14 conditions — block v2 → v2.1 revision before backend commit)

These edits go into v2.1 before any backend code lands. Cross-seat
convergences above absorb several individual conditions; the list
below is the synthesized must-fix set.

**D1. Vocabulary enrichment + positional-parser fix** (Seat 1 C1+C2 →
C-E). Pre-ship verb-list enrichment (~16 additions from diagnostic
corpus); promote `condense, scribe, distiller, hivemind, pair,
mobile-pair, github-sync, project-sync` from MODIFIERS to NOUNS.
v2 §5.1 line 651–674 re-shuffled.

**D2. Add coarse `_fingerprint` layer alongside exact** (Seat 1 C3).
v2 §5.3 defines `coarse_fingerprint = sha256(sorted({verb, noun,
modifier}))`. v2 §4.2 dual-layer recurrence check: exact-fingerprint
≥ N OR coarse-fingerprint ≥ N+1. Catches the §5.4 cross-project
convergence variance the exact-string hash misses. Open: Ron picks
N+1 vs explicit `distiller_coarse_min_recurrence` per-project key.

**D3. Cross-project aggregation lock discipline, thread topology,
and cost shape** (C-D synthesis; Seat 1 C5 + Seat 3 C1 + Seat 3 C2).
v2 §4.2 explicitly says: (i) inline at end of per-project Distiller
thread (preserves cap=2 semaphore); (ii) per-project locks acquired
SERIALLY during the walk OR lock-free with 3-retry parse (Ron picks);
(iii) `_skill_stats_summary.json` cache written after per-project
aggregation + bounded walk frequency (default once per 5 session-ends
OR 10min, whichever fires first).

**D4. EXPLORATION.md per-session cap** (C-A synthesis; Seat 2 C1 +
Seat 3 C6). v2 §11 adds `distiller_max_explorations_per_session`
(default 3, per-project). v2 §4.1 K-cap rewritten to bind topics AND
explorations.

**D5. §4.5 SKILL.md prompt elements extended** (Seat 2 C2). Add
required element 6 (anti-patterns section, mirrors EXPLORATION.md
"what didn't work") and element 7 (recognition-test phrasing: TRIGGER
must describe what the agent SEES, not just "when to call"). Closes
v1.1 Seat 2 Cond 4 at the Phase 4 layer in addition to the Phase 1
layer §6.2 already closes.

**D6. Cross-artifact suppression key extended** (C-B synthesis; Seat 2
C3 + Seat 3 C5). v2 §4.7 re-keyed on `(fingerprint, kind)` for the
three kinds; UPDATE inherits from SKILL. Lock domain extended through
the artifact-generation check-then-write cycle (§4.7 last paragraph).
`distiller_suppressed_after_generate` counter added to §7 telemetry.

**D7. Hard-kill outbox marker** (Seat 3 C4). v2 §4.8 adds a
`(fingerprint, last_proposed_at, last_proposed_path)` outbox marker
written under the same lock as the per-session signal commit, AFTER
proposal-generate completes. Aggregation's dedupe extends from
per-(project, fingerprint, day) to `last_proposed_at > now -
distiller_proposal_dedupe_days` (default 7). Closes the across-the-
24h-boundary duplicate-proposal failure path.

**D8. Semaphore policy explicit** (Seat 3 C3). v2 §4.8 states:
Distiller's semaphore acquisition is non-blocking with a 2s timeout;
on miss, skip with `distiller_semaphore_skip` counter. Cap=2 retained
because Distiller is best-effort (per §2 principle 2/8); backpressure
sits on the best-effort path.

**D9. `_skill_stats.json` location flat-sidecar** (Seat 3 C7). v2 §4.2
and §11 align: location is `data/projects/<pid>_skill_stats.json`
(flat sidecar matching the `_agent_log.json` / `_scribe_stats.json`
precedent), NOT `data/projects/<pid>/_skill_stats.json`. v2 §4.2
walker becomes `DATA_DIR.glob('*_skill_stats.json')`.

**D10. Implementation-anchor line numbers refreshed** (Seat 4 C1).
Every line number in v2 §11 anchor table is stale (49–95 lines off,
worst case server.py:1158 cited where actual is 1214 mid-`_register_process`).
Refresh against current `server.py` + add disclaimer "line numbers
verified against commit <sha>; expect drift, grep for symbol name
before patching."

**D11. Parent Cond 11 v2 inheritance binding** (Seat 4 C2). Auto-mode
discovery surface (parent design §329–344) is currently homeless. Add
explicit row to v2 §10 inheritance table: "INHERITED — gates Phase 5;
surface must ship in same commit as `auto` mode default-on." Forward-
protection; not a v2-backend gate.

**D12. Dual-checkpoint promotion UI §1.5.1** (Seat 4 C3). Add concrete
commitments before backend: (a) `_proposed/` queue listing in Skills
panel (kind icon, scope-tag badge, evidence-session count, age); (b)
scope-tag override widget (click badge → dropdown → confirm preview);
(c) PREFERENCE.md target-picker on promotion-confirm modal. Resolve
v2 §12 open question 5 (divergence as audit signal or actionable
surface) — answer determines whether `_skill_stats.json` needs a
`scope_divergence` counter.

**D13. `_proposed/` migration and naming** (Seat 4 C4). Add §3.0:
lister tolerates legacy `_proposed/<sid>/` entries under "uncategorized";
project IDs writing to `_proposed/<project_id>/` validated against
`^[a-z0-9_-]+$` at write time; string `global` reserved.

**D14. Cross-project recurrence composition rule** (C-C synthesis;
Seat 4 C5). v2 §4.2 adds explicit composition rule: each project's
signal contribution must clear THAT project's `distiller_min_recurrence`;
projects below threshold contribute zero to cross-project count;
cross-project promotion requires ≥2 projects each clearing their own
threshold.

### Must-fix-in-implementation (5 conditions — block v2 backend commit)

**I1. Extraction prompt retry + over-emission telemetry** (Seat 1 C4).
`vocabulary_miss` counter ships; add `extra_tokens_dropped` counter
(incremented when `len(parts) > 3`); on OOV detected in slot 0/1 of
otherwise well-formed emission, retry once. Few-shot block must
include a negative example.

**I2. Parametric DATA_DIR regression test** (C-F synthesis; Seat 1 C6
+ Seat 4 C6). Single `EXCLUDED_SIDECAR_SUFFIXES` constant; test
parametrizes over the full tuple + "next sidecar" canary that forces
contributors to update the tuple. File: `tests/test_load_projects_sidecar_exclusions.py`.

**I3. PREFERENCE.md `suggested_target` removed; default to feedback_memory**
(Seat 2 C4). Closes v2 §12 open question 2. Promotion UI offers all
three destinations as one-click choices; extraction-time judgment
dropped.

**I4. Cost-cap log includes the cap value** (Seat 4 C7). Edit §8 log
shape to `distiller_cost_cap_hit:<project_id>:<date>:<tokens_used>:<cap_value>`.
Add `cap` field to `/distiller-stats` response alongside `cap_hits`
and `cost.<date>`.

**I5. Project JSON migration** (Seat 4 C8). Extend `load_projects()`
`setdefault` block with the 4 new per-project keys; write-through on
first Settings-modal open or first session-end. Mirrors the
`current_task` / `next_action` precedent.

### Soak-gate (2 conditions — block default-flips, not the commit)

**S1. Vocabulary drift telemetry calibration** (Seat 1, derived from
C1+C2). After 2 weeks of v2 operation, `vocabulary_miss` rate is the
calibration signal. If <5% across all projects, vocabulary is well-
fitted. If >5% sustained, audit checklist surfaces a vocabulary-tune
backlog item. The pre-ship enrichment (D1) is the starting point; drift
telemetry is the maintenance loop.

**S2. Cross-artifact suppression accuracy soak** (derived from D6). The
`(fingerprint, kind)` keying is a hypothesis: users actually want
per-kind judgment. Track at 4-week mark: do users send mixed signals
across kinds for the same fingerprint? If yes, the keying is right
and stays. If users always send same answer across kinds (no, the
collapsing IS the user mental model), v2.2 reverts to single-key.
Telemetry: `distiller_per_kind_suppression_divergence` counter.

### Cross-cutting ratifications (preserve across future revisions)

- **Closed-vocabulary direction is the right architectural answer to
  v1.1 Cond 1.** Upstream constraint actually collapses the synonym
  variance bag-of-tokens cannot. v2's vocab miss is a calibration
  issue (D1, S1), not a design issue.
- **READ-TIME window filtering with append-only `_skill_stats.json`
  storage** cleanly closes v1.1 Cond 3.
- **Scope tag as first-class observable** with extraction-tag +
  promotion-tag dual checkpoints replaces v1.1's split surface
  cleanly and honors the locked definition's cross-project default.
- **Four-artifact taxonomy** (SKILL/UPDATE/EXPLORATION/PREFERENCE) is
  the minimal decomposition of the locked-definition's four targets
  (codebase, work, user, agent itself). No artifact class missing at
  the design level.
- **§6 mc-distill v2 revision is genuinely internally consistent.**
  The Seat 4 v1.1 OOS-flag failure mode (§Procedure step 2 + §Tone
  contradicting §Disposition) is closed by §6.1's matrix format. The
  consistency invariant is visible to future editors.
- **§6.2 pattern-bound-vs-session-bound specificity test** closes
  v1.1 Seat 2 Cond 4 at the Phase 1 layer. D5 extends the same closure
  to the Phase 4 layer.
- **Once-per-session cap restoration** (§6.1 row 2) is the right
  call. The within-session-recurrence drop (§6.1 row 1) was the actual
  structural inhibitor v1.1 §3.8 removed. The cap is a volume control;
  restoring it doesn't re-collapse fire rate.
- **v1.1 Seat 3 wins all carry forward cleanly:** Cond 7 (kill-switch
  enumeration including record-push), Cond 8 (reads take the lock +
  explicit RMW span), Cond 9 (daemon-thread dispatch matching Step 6),
  Cond 10 (signal-before-generate ordering), Cond 16 (atomic
  `_skill_stats.json` via `_atomic_write_text`). All five close at
  v2 §4.6–4.9 with the exact wording v1.1 asked for.
- **`distiller_cross_project_enabled`** as an independent kill closes
  v1.1 Cond 5 and Cond 13 in one config key.
- **Rollback paths** (§6.3 Hot/Cold/Hard) satisfy v1.1 Cond 12 cleanly.
  Hard revert's `--force` deferral is operationally acceptable.
- **`distiller.py` born outside `server.py`** preserved per
  MAINTENANCE_PROTOCOL.md Rule 1.
- **Per-project vs global config split** (§11) cleanly closes v1.1
  Cond 14.
- **Cost-cap structured log promoted to v1** (§8) cleanly reverses v1.1's
  defer-to-Phase-5. I4 adds the cap-value field.

### Out-of-scope flags routed by synthesizer (not addressed in v2.1)

- **PREFERENCE.md fingerprint scheme** (Seat 1 OOS, Seat 2 territory).
  v2 specifies the topic-fingerprint scheme; preference fingerprint is
  undefined. Two independent extractions of the same preference may
  hash differently, recurrence detection falls into the same v1.1
  Cond 1 trap. Backlog: v2.x adds a preference-fingerprint scheme
  (likely: top-3 nouns from summary, sorted, hashed — or its own vocab
  subset).
- **EXPLORATION.md cross-session dedupe semantics** (Seat 1 OOS, Seat 2
  territory). Same exploration recurring 3 sessions later: 3 separate
  EXPLORATION.md proposals on disk, or dedupe across rolling window?
  Spec implies the former. Backlog: confirm intent in v2.x with a
  one-paragraph §4.3 addition.
- **EXPLORATION.md read-floor budget** (v2 §12 Q3). `exploration_topk`
  config absent from §11. Backlog: add if memory + exploration read-floor
  combined cost grows context-window perceptibly.
- **Agent-itself learning target broader than UPDATE.md** (Seat 2 OOS).
  Locked definition's "agent's own decision patterns and failure modes"
  target includes meta-patterns like "agent keeps mis-classifying X as
  Y across sessions" that UPDATE.md does not cover. Backlog: v2.x adds
  a meta-pattern artifact kind if/when telemetry catches a real instance.
- **Cost-cap interaction with four signal classes** (Seat 2 OOS, Seat 4
  OOS). v2 ships a higher per-session-end cost ceiling than v1.1
  budgeted (M EXPLORATION.md render calls + N proposal-generations +
  the extraction call + Cond I1 retries). 100k tokens/project/day cap
  may be tight. Soak-gate: monitor `cap_hits` in §7 telemetry; raise
  cap if hit rate exceeds 1/project/week.
- **In-session vs Distiller fingerprint seam re-normalization** (C-G).
  Recommended fix (server-side `_fingerprint()` on `record-push`)
  closes the gap but is an implementation detail of D6 / I3. Confirm
  it lands with the v2 backend commit.
- **Step 6 mid-session checkpoint × Distiller interaction** (Seat 3
  OOS). Distiller fires ONCE per session at session-end; mid-session
  checkpoints do not trigger Distiller. One-line clarification needed
  in v2.1 §4.8.
- **Vocabulary revision inherits SKILL.md rollback risk** (Seat 4 OOS).
  Vocab updates propagate via `_install_builtins()`-equivalent
  mechanism with the same Cold-revert-leaks-on-user-edits risk §6.3
  documents. Cross-reference §6.3 from §5.2 in v2.1.

---

### Path forward

1. **v2.1 spec revision** addresses the 14 must-fix-in-design conditions
   above. Open architectural picks Ron needs to weigh in on before
   v2.1:
   - **D2 alternative**: `coarse_fingerprint` threshold = exact threshold +1
     OR a separate `distiller_coarse_min_recurrence` per-project key?
   - **D3(ii) alternative**: serial per-project locks during cross-project
     walk OR lock-free with 3-retry parse on JSON failure?
2. **Mark doc status:** `DRAFT v2.1 (post-committee-review 2026-05-27)`.
3. **5 must-fix-in-implementation conditions** get tracked against the
   v2 backend commit.
4. **2 soak-gate conditions** get tracked in the audit checklist when
   Phase 3 lands.
5. **v2 backend build** can start once v2.1 is published AND condense
   (the parallel gate #1) is fixed. Single bundled PR per §7 estimate
   (~700–900 LOC, now higher with the convergent-condition edits —
   estimate ~900–1200 LOC for v2.1 scope).

**No backend code lands until v2.1 is published.** Same discipline as
parent design v2 (post-committee 2026-05-19) and Memory System SPEC
§3.A.MID.
