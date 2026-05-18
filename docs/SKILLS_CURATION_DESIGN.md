# Clayrune Skills Curation — Design

> Status: design draft. Step 1 (`mc-distill` skill — manual + conversational-
> push triggers) **SHIPPED 2026-05-18**. Backend Distiller and subsequent
> steps not yet built, no committee review yet. Parallel architecture to
> [`MEMORY_SYSTEM.md`](MEMORY_SYSTEM.md); reuses the Scribe infrastructure
> wherever possible. Principles locked in MEMORY_SYSTEM.md open item #5.
> Authored in chat 2026-05-18; awaiting committee review before any
> backend code lands.

**Goal:** turn recurring patterns the agent demonstrates into reusable
`SKILL.md` artifacts *without* humans writing them by hand, while keeping
MC firmly in control of what enters the project's skill loadout. Hermes-
equivalent capability, but explicitly: **MC owns, agent proposes, human
promotes** — except in opt-in `auto` mode where the agent self-promotes
within a project-local sandbox.

**Scope (firm):** authored skills only. Explicit, named SKILL.md artifacts
in `~/.claude/skills/` or `<project>/.claude/skills/`. NOT in scope:
"learned behaviors" via curated-region MEMORY.md drift — that blurs the
curated/managed boundary and is hard to roll back. Skills are the unit of
self-improvement here.


## The big picture — three paths

```
                     ┌─────────────────────────────────────────────────┐
                     │            Skill artifacts (per project)         │
   reads (CC native) │  ┌──────────────────────────────────────────┐    │
  ┌──────────────────┼─▶│ ~/.claude/skills/        (global)         │    │
  │                  │  │ <proj>/.claude/skills/   (project-local)  │    │
  │  ┌───────────────┼─▶│  - manually authored                      │    │
  │  │               │  │  - promoted from _proposed/               │    │
  │  │               │  │  - auto_authored: true   (auto-mode only) │    │
  │  │               │  └──────────────────────────────────────────┘    │
  │  │               │                  ▲                                │
  │  │               │                  │ promote (proposed mode)        │
  │  │               │                  │ direct write (auto mode)       │
  │  │               │  ┌──────────────────────────────────────────┐    │
  │  │  WRITE        │  │ data/skills/_proposed/<sid>/             │    │
  │  │ (Distiller)───┼─▶│  - SKILL.md  (new-skill proposal)         │    │
  │  │               │  │  - UPDATE.md (patch to existing skill)    │    │
  │  │               │  └──────────────────────────────────────────┘    │
  │  │               └─────────────────────────────────────────────────┘
  │  │
[Agent session]
  │  │
  └──┴── READ at dispatch: top-K relevant skills surfaced into context
         (v1: keyword via /api/skills/search; v2: bge-m3 once Step 7 ships)
         + on-demand: existing mc-skill-broker, mc-memory-search skills
```

- **WRITE** — Distiller runs at session end (parallels Scribe), reads the
  same `.jsonl`, asks "is there a reusable pattern here?", writes a
  proposal (or, in `auto` mode, the skill itself).
- **READ** — top-K skill hints injected at dispatch via the same read-floor
  mechanism as memory. Keyword-scored today, semantic when Step 7 lands.
- **EVOLVE** — monthly maintenance audit surfaces stale / unused / un-
  promoted / un-reviewed skills as backlog items. No new evolution loop.

## Components

| Component | Plain English | Status | Depends on |
|---|---|---|---|
| **Distiller** | Cheap-model "is this worth a SKILL.md?" call, parallel to Scribe at session end. | not built | Scribe trigger (Leg A — shipped) |
| **Conversational push** | Agent surfaces a candidate proposal inline mid/end-session via a `[Yes / Later / No]` choice. Complements silent Distiller — catches in-session patterns while context is sharp. User-gated. | **shipped** via `mc-distill` skill (2026-05-18) | Existing chat surface; no new infrastructure |
| **Manual `/distill` invocation** | User explicitly asks the agent to propose a skill. | **shipped** via `mc-distill` skill (2026-05-18) | Existing chat surface |
| **Proposal writer** | Atomic write to `data/skills/_proposed/<sid>/SKILL.md` (or `UPDATE.md`). | not built | Reuses `_atomic_write_text` + per-project leaf-lock pattern |
| **Promote / reject API + UI** | `POST /api/skills/_proposed/<sid>/promote` moves to skill dir; reject deletes proposal. | not built | Existing skills CRUD endpoints |
| **Skill-use telemetry** | Same-pass `.jsonl` scan as Scribe identifies which skills were referenced this session; writes `_skill_stats.json`. | not built | Scribe path |
| **Skill-relevance hint at dispatch** | Top-K skills injected alongside memory read-floor. | not built | v1: existing `/api/skills/search` (keyword); v2: Step 7 bge-m3 |
| **Auto-mode self-promote** | When `mode=auto`, skips `_proposed/` and writes directly to `<project>/.claude/skills/` with `auto_authored: true`. | not built | Distiller |
| **Audit extension** | New checklist items in `MAINTENANCE_AUDIT_PROMPT.md`: stale proposals, unused skills, auto-authored skills awaiting review. | not built | Audit infra (shipped) + telemetry |

## Per-project mode

Skills Curation behavior is **per-project**, set in Settings (and editable
via `_CONFIG_EDITABLE_KEYS`). Three modes:

| Mode | Distiller fires? | Where output lands | Promotion |
|---|---|---|---|
| `off` | No | — | All skills manually authored; status quo |
| `proposed` (default for new projects) | Yes | `data/skills/_proposed/<sid>/` | Human reviews + promotes via UI |
| `auto` | Yes | `<project>/.claude/skills/` directly | Agent self-promotes; flagged `auto_authored: true`; surfaced in monthly audit for review |

**Suggested defaults by project type:**

- Sandbox / personal-exploration projects → `auto` (the place the Hermes-style loop is interesting)
- Active development projects (Clayrune itself, most work) → `proposed`
- Real-money / production / client-facing projects (TRADING) → `off` or `proposed`, never `auto`

Switching modes never deletes existing skills — it only controls future
Distiller output. Promoted skills stay promoted; auto-authored skills stay
present even after switching back to `proposed`.

## Key files & anchors (proposed structure)

- **`distiller.py`** (new) — `_distill_extract`, `_distill_call`,
  `_distill_render_transcript` (or shared helpers with Scribe),
  `_propose_or_promote(project, proposal)`, `_distiller_stat`. Parallels
  the Scribe helpers in `server.py` but lives in its own module from day
  one (per `MAINTENANCE_PROTOCOL.md` Rule 1: new subsystems born outside
  `server.py`).
- **`server.py`** — new endpoints `/api/skills/_proposed/list`,
  `/api/skills/_proposed/<sid>/promote`, `/api/skills/_proposed/<sid>/reject`;
  new `/api/distiller-stats`; Distiller invocation in
  `_write_session_memory`; optional read-floor extension in
  `_build_agent_context` to inject skill hints.
- **`skills.py`** — extensions for `data/skills/_proposed/` listing,
  promote-to-scope (global / project), UPDATE.md patching semantics.
- **`data/skills/_proposed/`** (new directory). Gitignored.
- **`data/projects/<id>/_skill_stats.json`** (new, suffix-excluded in
  `load_projects()` — the load-bearing DATA_DIR rule from
  MEMORY_SYSTEM.md applies, same as `_agent_log.json` / `_scribe_stats.json`).
- **`docs/MAINTENANCE_AUDIT_PROMPT.md`** — new checklist section for skills.
- **`docs/MEMORY_SYSTEM.md`** open item #5 — references this doc once
  drafted; mark as "design drafted" rather than "deferred."

## Config surface (per-project, in `_CONFIG_EDITABLE_KEYS`)

| Key | Default | Role |
|---|---|---|
| `distiller_mode` | `'proposed'` | `off`, `proposed`, or `auto` |
| `distiller_model` | `''` → haiku | Cheap-model identifier (same shape as `scribe_model`) |
| `distiller_min_turns` | `5` | Sessions with fewer turns → skip distillation |
| `distiller_min_recurrence` | `3` | New-skill proposals require pattern recurrence across ≥N sessions. UPDATE proposals not gated. |
| `distiller_skip_errors` | `true` | Skip `_(error)_` / `_(stopped)_` sessions |
| `distiller_enabled_global` | `true` | Master kill-switch (config.json — not per-project) |
| `skill_hint_topk` | `3` | Dispatch-time skill hint count (mirrors `read_floor_topk`) |
| `skill_hint_enabled` | `true` | Toggle the dispatch hint independent of Distiller |

The recurrence gate is enforced via the per-project `_skill_stats.json`
"pattern fingerprints" rolling window (see Lifecycle below).

## Lifecycle of one proposal (proposed mode)

```
session ends ─▶ _write_session_memory fires Scribe (Leg A, shipped)
            ─▶ in parallel, _distill_extract called on the same .jsonl:
                 - reads same transcript bytes (shared helper)
                 - cheap-model emits 0-N "candidate patterns"
                 - each pattern: fingerprint + check recurrence in
                   per-project _skill_stats.json
                 - patterns under min_recurrence: just bump the stats
                   counter, don't propose
                 - patterns at/above threshold AND no existing skill match:
                   emit SKILL.md to data/skills/_proposed/<sid>/
                 - patterns that match an EXISTING skill: emit UPDATE.md
                   instead (proposed patch)
                 - thin / refusal / error: skip cleanly (same guards as Scribe)
            ─▶ _propose_or_promote:
                 mode == 'proposed' → atomic write to _proposed/<sid>/
                 mode == 'auto'     → atomic write to <project>/.claude/skills/
                                       with frontmatter auto_authored: true
                 mode == 'off'      → unreachable (Distiller didn't fire)

UI: project view shows _proposed/<sid>/ count badge in the corner
    → click → review pane shows the proposed SKILL.md / UPDATE.md
    → promote (moves file to chosen scope) or reject (deletes proposal)
    → either action bumps a counter in _skill_stats.json

Monthly maintenance audit:
   - Lists proposals older than 30 days, unreviewed
   - Lists auto-authored skills older than 30 days, never invoked
   - Lists skills with zero invocation in 90 days (candidate archival)
   - Lists skills with high invocation AND no UPDATE proposals (stable, working)
```

## Lifecycle of an auto-authored skill (auto mode)

Same flow as above, but `_propose_or_promote` writes directly to
`<project>/.claude/skills/<name>/SKILL.md` with `auto_authored: true` in
frontmatter. Next session sees it natively via Claude Code's skill loading.
Subsequent sessions can produce UPDATE proposals — in auto mode these
updates also apply directly (with `auto_authored: true` preserved on the
edited file).

**Kill switch:** flipping mode back to `proposed` or `off` stops new
authoring immediately. Existing auto-authored skills stay until pruned
manually or via audit-driven decision.

**Visibility:** the monthly audit ALWAYS lists auto-authored skills as
review candidates so they're never invisible. The UI shows an `auto`
badge in the skills list.

## Lifecycle of a conversational-push proposal

Distinct path from the silent Distiller — runs in the chat surface, not at
session end. The agent notices a pattern mid- or end-session and surfaces
it inline. Implemented today via the `mc-distill` skill.

```
agent observes pattern (≥2 recurrences, at natural breakpoint)
    ─▶ inline message to user:
       "Noticed a pattern: <X>. Observed <N> times. Bottle?"
       [Yes / Later / No]
    ─▶ user response:
         Yes   → agent drafts SKILL.md (or UPDATE.md) inline
                 mode='proposed': atomic write to _proposed/<sid>/
                                  (UI shows badge → one-click promote in UI)
                 mode='auto':     atomic write to <project>/.claude/skills/
                                  with auto_authored: true + provenance: interactive
         Later → no write; silent Distiller picks this up at session end
                 if the pattern still qualifies under min_recurrence
         No    → suppression marker added to _skill_stats.json so the silent
                 Distiller does not re-propose this pattern in this session
```

**Hard constraints (enforced by the `mc-distill` skill):**

- One proactive push per session, max. After Yes/Later/No, do not propose again.
- Only at natural breakpoints (end of task, after commit, wrap-up).
- Never mid-task or mid-debug.
- Specificity bar (name the pattern, cite recurrence) — no fishing prompts.

**Why this exists alongside the silent Distiller:**

The conversational push catches obvious patterns *in-the-moment*, while
context is sharp and the user can react quickly. The silent Distiller
catches *cross-session* recurrence the in-session agent can't see — it
only has its own session's transcript, whereas the Distiller has access
to `_skill_stats.json` history across many sessions. They're
complementary, not competing.

## Load-bearing rules (don't violate)

- **Distiller is best-effort, never load-bearing.** Failure to distill
  never breaks a session, never breaks Scribe, never blocks completion
  logging. Same posture as Scribe's thin/refusal guards.
- **Auto-authored skills are project-local only.** Never written to
  `~/.claude/skills/`. A bad auto-skill in one project does not pollute
  any other project's sessions.
- **`auto_authored: true` is preserved across in-place edits.** Promote-
  to-global (a deliberate manual user action) clears the flag.
- **`data/projects/<id>/_skill_stats.json` MUST be suffix-excluded in
  `load_projects()`** — the load-bearing DATA_DIR rule from
  MEMORY_SYSTEM.md applies.
- **No race between Distiller and Scribe.** Both read the same `.jsonl`
  but neither writes to MEMORY.md from the Distiller path — Distiller
  output goes only to skill files and `_skill_stats.json`. Memory and
  skills are fully separate write surfaces.
- **UPDATE.md never auto-applies in `proposed` mode.** Patches to existing
  skills go through the same human review as new skills.
- **Distiller never writes globally (`~/.claude/skills/`).** Even in
  `auto` mode. Global promotion is always a deliberate user action.
- **Conversational push is always user-gated.** The agent never writes to
  any skill location based on a proactive suggestion without an explicit
  "Yes" from the user in the same turn. Silence is not consent. `Later`
  is not consent — only `Yes`.
- **`No` from a conversational push suppresses silent re-proposal in the
  same session.** A suppression marker in `_skill_stats.json` is honored
  by the silent Distiller until the session closes.
- **One proactive push per session, max.** Enforced by the `mc-distill`
  skill's own rules. After any Yes/Later/No, no further proactive
  proposals in that session.

## Leveraging existing Scribe / memory infrastructure

The whole point of this design is to *not* rebuild what Scribe already
solved. Explicit reuse map:

| What we reuse | From |
|---|---|
| Session-end trigger | `_write_session_memory` (Leg A) |
| Hard-kill / mid-flight handling | Best-effort drop on crash (Distiller is not load-bearing; no Fix-B-equivalent needed) |
| Transcript reader | `_scribe_render_transcript` (full transcript, since Distiller works at session end) |
| Thin / refusal / error guards | `_scribe_extract` precedent |
| Cheap-model call wrapper | `_scribe_call` precedent (same model, different prompt) |
| Per-project leaf lock + atomic write | `_get_mem_write_lock` + `_atomic_write_text` (rename per-domain, same pattern) |
| Bounded semaphore | Step 6 per-project `BoundedSemaphore` (cap=2) — applies the same way to Distiller fan-out |
| Telemetry surface | `/api/project/<id>/scribe-stats` shape → mirror as `/api/project/<id>/distiller-stats` |
| Read-floor injection at dispatch | `_build_agent_context` "--- RELEVANT MEMORY ---" → add "--- RELEVANT SKILLS ---" alongside |
| On-demand pull (existing) | `mc-memory-search`, `mc-skill-broker` — no changes needed |
| Cross-project skill discovery | Existing `/api/skills/search` keyword endpoint (becomes semantic when Step 7 ships — same plumbing, swap backend) |
| Inline user gate (`Yes / Later / No`) | Existing chat surface — no new mechanism. AskUserQuestion-style prompt rendered inline by the `mc-distill` skill. |
| Audit extension surface | `MAINTENANCE_AUDIT_PROMPT.md` (read-only, monthly) |

**What's genuinely new code:**

- `distiller.py` extract + render + propose pipeline
- `_skill_stats.json` schema + pattern fingerprinting
- `_proposed/` directory CRUD (lists, promote, reject, UPDATE patching)
- Settings UI mode selector
- Audit checklist additions

Rough estimate: **~600-900 lines of genuinely new code**, vs. ~2000+ if we
hadn't built on top of Scribe. Most of the heavy lifting (transcript
parsing, atomic writes, model calling with guards, telemetry shape,
semaphore bounding) is already shipped and battle-tested.

## Step 7 dependency, revisited

Earlier framing said "dispatch hint waits on Step 7." That was wrong on
reread. The existing `/api/skills/search` endpoint (used by
`mc-skill-broker`) already does keyword scoring across name + description
+ body. The dispatch hint can ship as a **v1 keyword** version today —
inject top-K keyword matches against the agent's initial prompt into the
read-floor.

**Step 7 (bge-m3)** upgrades this in place: same plumbing, swap the
scoring backend from keyword to semantic similarity. So the dispatch hint
is buildable now, not blocked.

What IS legitimately Step-7-dependent is the *quality* of the hint —
keyword search will miss conceptually-similar skills that share no words
with the prompt. That's a v2 polish issue, not a v1 blocker.

## Open items

1. **Pattern fingerprint design.** How to hash a "pattern" stably so the
   recurrence counter is meaningful. Candidate: cheap-model emits a short
   canonical phrase per pattern (e.g. `"prefer-edit_block-for-surgical-edits"`),
   hash that string. Cheap, fuzzy, good enough — but worth committee
   review before locking.
2. **UPDATE.md schema.** Format for proposing a patch to an existing
   skill. Simplest: target-skill name + a prose description of the
   proposed change + a unified diff (informational, not auto-applied).
   Humans read the prose and decide.
3. **Cost cap.** Per-project per-day Distiller token cap. Same shape as
   Scribe's existing accounting, separate counter. Default: 5,000 tokens
   per project per day. (Distiller calls are cheap — haiku on a fingerprint
   reduction — but cap defends against runaway loops.)
4. **Auto-mode trust gradient.** Initially: any project that flips to
   `auto` is opt-in. Later possibility: "graduate from proposed to auto
   after N successful promotions" automation? Probably not in v1 — keep
   mode purely manual.
5. **TRADING / production project handling — RESOLVED 2026-05-18.** No
   special treatment. No `production` flag, no name-pattern matching, no
   validation rules about which modes are allowed where. Users choose
   `distiller_mode` per project according to their own judgment. The
   mode selector UI should make `auto` mode's implications explicit on
   selection (a clear warning that the agent will self-author skills in
   that project), but the system imposes no rules — the user is treated
   as competent to make their own safety calls.
6. **First experiment — SHIPPED 2026-05-18.** The `mc-distill` Claude Code
   skill is now in `data/skills/builtin/mc-distill/SKILL.md` (158 lines).
   It supports **both** triggers: explicit user invocation
   (`/distill`, "propose a skill") AND proactive agent-initiated proposals
   with hard rules (recurrence ≥2, natural breakpoint, specificity,
   one-proactive-push-per-session-max). Auto-installs on next MC startup.
   This is the cheapest possible experiment to validate proposal quality
   before any Distiller backend code lands.
7. **Committee review — required before any backend code lands.** Memory
   system went through committee review before build (`MEMORY_SYSTEM_SPEC.md`
   §3.A.MID is the hardened result). This design has NOT been committee-
   reviewed, and the conversational-push addition expanded the surface
   area beyond the original draft. Committee focus areas:
   - Pattern fingerprint stability (item #1).
   - UPDATE.md schema rigor (item #2).
   - Conversational-push annoyance bar — when does the agent ask, what
     constitutes a "natural breakpoint," how to prevent prompt fatigue.
   - Auto-mode skill-quality decay — bad skills compound; rollback story.
   - Race / coordination between conversational push `Later` and the
     silent Distiller at session end (the suppression-marker contract).
   - Cost cap calibration (item #3).

## Recommended build order

Once Step 6 is live-validated and Step 7 ships (or is decided to stay
deferred), suggested sequence:

1. **`mc-distill` skill — SHIPPED 2026-05-18.** Manual + conversational-push
   triggers both live in the same SKILL.md. Validates proposal quality
   before any backend code lands.
2. **Skill-use telemetry** (`_skill_stats.json`). Cheap; builds the recurrence-
   tracking substrate that Distiller needs anyway. Also stores the per-session
   suppression markers used by the conversational-push `No` path.
3. **Audit checklist extension.** Surface the data once telemetry exists,
   before any auto-write code lands.
4. **Distiller (`proposed` mode only).** New module, hooks into Scribe
   trigger, writes to `_proposed/`. UI for review. The conversational-
   push UX is already in the `mc-distill` skill; Distiller adds the
   *silent cross-session* path.
5. **`auto` mode.** Add after `proposed` is real and proposal quality has
   been observed in practice.
6. **Dispatch skill hint (v1 keyword).** Independent of the Distiller;
   can parallel-track.
7. **Dispatch skill hint v2 (bge-m3).** When Step 7 lands.

Step 1 is done. Steps 2-3 are days-of-work each; step 4 is the main lift
(~600-900 lines new code per the reuse-table estimate); steps 5-7 are
incremental.

Per `MAINTENANCE_PROTOCOL.md`: this is not a sprint plan — it's a build
*order* to follow opportunistically as adjacent feature work happens, or
in a dedicated sprint if you decide to take it on as a single push.
