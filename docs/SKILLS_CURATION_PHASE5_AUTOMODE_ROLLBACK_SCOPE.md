# Phase 5 — `auto` mode + rollback-discovery surface (SCOPE)

**Status: SCOPE / paper-only (2026-07-02). No code. Pre-committee.**
Companion to `SKILLS_CURATION_PHASE4_SPEC_V2.md` (parent). Closes parent
**Condition 11**: `auto` mode cannot ship without a real-time rollback
discovery surface.

This is the last data-plane rung before self-operation over a "field of
responsibility": the agent self-installs project-local skills without a human
in the loop. The whole rung is gated on being able to *see and undo* what it
did. This doc scopes that surface so the build is mechanical when the gate
opens.

---

## 0. The gate (do NOT build yet)

`auto` mode is gated on **FIX 2 proving proposal quality**. Rationale: auto
mode removes the human review step, so the artifacts it self-installs must
already be good *before* review is removed. The signal is empirical, read off
the existing `/api/distiller/loop-health`:

- **SKILL generation is non-zero and rising** (FIX 2a reframes + FIX 2b
  extraction). Today: 1 SKILL vs 114 EXPLORATIONs — not ready.
- **Refuse rate on skill render is bounded** (`render_refuse:skill` counter
  not dominating `proposed:skill`).
- **Promotion acceptance rate is high** — of skills a human *did* promote,
  few were later rejected/uninstalled. This is the closest proxy for "would
  auto-install have been right."

Until those trend green for a project, that project stays `proposed`. Flipping
`auto` early just automates junk.

---

## 1. What `auto` mode does (the thing being gated)

Per parent design l.115 + the load-bearing guardrails, `distiller_mode='auto'`
changes exactly one thing in the existing pipeline: at
`_generate_and_write_artifact`, instead of writing to
`data/skills/_proposed/<scope>/…`, a **skill** artifact (only skill — never
exploration/preference/update) is:

1. Installed directly to `<project_path>/.claude/skills/<name>/SKILL.md` via
   the existing `skills.write_skill(scope='project', …)`.
2. Stamped `provenance: auto-authored`, `auto_authored_at: <iso>`,
   `source_session`, `extraction_fingerprint_exact`.
3. Recorded in a per-project `_auto_authored` ledger inside
   `<project>_skill_stats.json` (the existing sidecar — DATA_DIR-excluded
   already).

**Hard invariants (enforced in code, not prompt):**
- **Project-local only.** `auto` NEVER writes `~/.claude/skills/` (global).
  Cross-project (`scope_tag == cross-project`) artifacts in `auto` mode fall
  back to `_proposed/global/` for human promotion — auto never widens blast
  radius beyond the project it learned in.
- **Skill kind only.** Explorations stay readback-only; preferences and
  updates still route to `_proposed/` (they change always-loaded behavior /
  feedback memory — higher blast radius, keep the human gate).
- **Recurrence-gated as today.** `auto` does not lower thresholds; it only
  removes the *human click* on an artifact that already cleared the bar.
- **Kill switch.** Setting `distiller_mode` back to `proposed`/`off` stops
  further auto-authoring immediately (already the gate at `distiller.py`
  `_distiller_should_proceed`). Existing auto skills stay until reverted.

---

## 2. The rollback-discovery surface (Condition 11)

Three parts. All reuse existing infrastructure; net-new code is small.

### 2a. Discovery — "what did auto mode install?"
New read endpoint `GET /api/distiller/auto-authored[?project_id=]` walking each
project's `.claude/skills/` for `provenance: auto-authored` frontmatter (plus
the `_auto_authored` ledger for ones since uninstalled). Per row: name,
project, `auto_authored_at` + age-days, source session, recurrence, the TRIGGER
description, and a **fired?** flag (did any session actually load/use it —
join against the existing skill-use / readback telemetry if available; else
"unknown"). Newest first.

### 2b. Review digest — "what's new since I last looked?"
A `since` cursor (per-operator `last_reviewed_at` in settings) so the surface
leads with **auto skills installed since last review** — the operator is never
blindsided by silent accumulation. This is the real-time half of Condition 11.
Surfaced as an **"Auto-installed (N new)"** section at the top of the existing
Learning-queue panel (`skills-panel.js`), above the pending `_proposed/` rows.

### 2c. One-click revert
New endpoint `POST /api/distiller/auto-revert {directory|name, project_id}`:
1. `skills.delete_skill(...)` (uninstall the SKILL.md).
2. Write a suppression marker keyed `{exact}:skill` `decision:no`,
   `source:auto_revert` — so the Distiller does not re-author it (reuses
   `_suppress_artifact`).
3. Move the uninstalled copy to `data/skills/_auto_reverted/` (audit trail —
   never silently deleted, mirrors `_rejected/`).
4. Bump an `auto_reverted` counter for loop-health.

Revert ≈ the existing `reject_proposed` flow + an uninstall. ~80% of the
mechanism already exists (`_relocate_proposed`, `_suppress_artifact`,
`mark_promoted`).

**Bulk revert / panic button:** one action that reverts *all* auto-authored
skills for a project and flips it back to `proposed`. This is the escape hatch
for the 2026-06-06 failure mode (a flood of junk) — recovery must be one click,
not 24.

---

## 3. Build order (when the gate opens)

1. Auto-write branch in `_generate_and_write_artifact` (skill-kind + project-
   local + fallback-to-proposed-for-cross-project) + `_auto_authored` ledger.
2. Discovery endpoint (2a) + Learning-queue "Auto-installed" section.
3. Revert endpoint (2c) + per-row Revert button + bulk/panic revert.
4. Review digest cursor (2b).
5. **Soak: one low-risk project only.** Flip `auto` for a single project whose
   FIX-2 metrics are green. Watch `auto_reverted / auto_authored` ratio for a
   window. Only widen if revert rate stays low.

Steps 1–4 are ~250–400 LOC + tests. Step 5 is operational, not code.

---

## 4. Open decisions (committee — same 4 seats as parent)

- **D-A1 (pattern):** Is "skill kind only, project-local only" the right auto
  surface, or should high-confidence PREFERENCES (recurrence ≥ N) also auto-
  install into project CLAUDE.md? (Leaning: no — preferences change behavior
  globally-in-project, keep human-gated.)
- **D-A2 (agent-behavior):** "fired?" join — do we have reliable skill-use
  telemetry per session, or does the digest show "unknown" and lean on the
  operator? Governs whether 2a's most useful column exists.
- **D-A3 (concurrency):** auto-write happens in the session-end daemon thread
  (same as proposal write) — does installing into a live `.claude/skills/`
  dir race a concurrent session reading it? Needs the same atomic-write + leaf
  lock discipline; confirm `skills.write_skill` already honors it.
- **D-A4 (config-ops):** default cursor / review cadence — is "since last
  opened the panel" enough, or does auto mode also need a scheduled digest
  (email/push) so an unattended project doesn't drift unreviewed for weeks?
- **D-A5 (scope):** cross-project auto — permanently fall back to `_proposed/
  global`, or is there ever a case for auto-installing cross-project? (Leaning:
  permanent fallback — global blast radius always keeps the human gate.)

---

## 5. Relationship to rung 6 (self-repair of load-bearing code)

Explicitly out of scope. `auto` mode self-authors **additive, reversible
skill files**. It never edits `distiller.py`, `server.py`, or any load-bearing
code. Self-modifying code stays human-gated until a regression harness exists
(Ron, 2026-06-05). This surface is deliberately the *safe* rung: everything it
does is a file you can `rm` and a suppression marker you can clear.
