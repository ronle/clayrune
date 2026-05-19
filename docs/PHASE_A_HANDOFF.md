# Phase A handoff — Stabilize Skills Curation Step 1

> Generated in chat 2026-05-18. Captures Phase A status, the root-caused
> visibility bug, and the action checklist for completing Phase A.
> Untracked. Apply, don't re-derive.

**Reference:** `docs/SKILLS_CURATION_DESIGN.md` build order, open items #8
(PATCH.md schema) and #9 (visibility bug — root cause hypothesis recorded).

---

## 1. Phase A goal

Stabilize Step 1 (the `mc-distill` skill, shipped 2026-05-18) before any
backend work begins. Two parts:

- **(a)** Fix the intermittent skill-listing visibility bug.
- **(b)** Live-validate the proactive trigger across 5–10 real sessions
  and confirm the bar is calibrated correctly.

Phase A passing is the gate to Phase B (committee review).

---

## 2. Visibility bug — diagnostic record + root-cause hypothesis

**Symptom.** `mc-distill` is intermittently absent from the CC
`skill_listing` attachment at session start. On-disk presence is
continuous; the listing is what's flaky.

**Reproduction data (mission-control project, 2026-05-18):**

| Time | Session ID | mc-distill in listing? | Skill count |
|---|---|---|---|
| 12:02 | `b9954e9d` | ✅ Yes | 19 |
| 12:27 | `612cc97b` | ❌ No  | 18 |
| 12:33 | `3e0b6342` | ✅ Yes | 19 |

**Where the bug is NOT:** Clayrune's `skills.py`. Grep confirms no
`skill_listing` string anywhere in `skills.py` or `server.py`. MC's
`list_skills()` and `install_builtins()` are pure filesystem walkers with
no cache or filter. The `skill_listing` attachment is built by **Claude
Code itself**, which reads `~/.claude/skills/` natively at session start.

**Strong root-cause hypothesis (data-backed):** Description length.

| Skill | Description length |
|---|---|
| `mc-distill` | **920 chars** ← outlier |
| `mc-memory-search` | 526 |
| `document-commit-deploy` | 418 |
| `mc-skill-broker` | 414 |
| `mc-clayrune-apis` | 352 |
| `mc-project-status` | 347 |
| `mc-changelog-update` | 312 |

`mc-distill` is the only built-in with a >600 char description, and
it's the only skill that intermittently disappears. The 12:27 session
that dropped it had ~25 minutes of accumulated agent context (mobile-UI
CHANGELOG work); the 12:02 and 12:33 sessions were fresher. Pattern is
consistent with CC's `skill_listing` having a per-attachment budget that
mc-distill's bloated description blows when other context competes for
space.

---

## 3. Proposed fix — description rewrite (NOT YET APPLIED)

Current description (920 chars) duplicates much of the body. Specifically
the rules already documented under "Proactive trigger (agent-initiated)"
in the body get repeated verbatim in the description:

- "NEVER trigger proactively mid-task, mid-debug, or for vague generic
  patterns."
- "Once per session maximum for proactive triggers."
- Specifics of `[Yes / Later / No]` mechanics.
- "NEVER auto-installs. NEVER writes to..."

These belong in the **body**, where they already exist. The description
should be just the TRIGGER language CC's loader uses to decide invocation.

**Proposed shortened description (~390 chars):**

```yaml
description: Propose a reusable SKILL.md from the current session when a pattern, workflow, gotcha, or rule worth bottling has emerged. TRIGGER on explicit user request ("/distill", "propose a skill", "do we have a pattern here") OR proactively when you notice a clear repeatable pattern (≥2 recurrences) at a natural breakpoint. See body for hard rules and proposal format. Writes only to data/skills/_proposed/<sid>/ for manual review.
```

**Application path (source-only — let install_builtins propagate):**

1. Edit `data/skills/builtin/mc-distill/SKILL.md` — replace the
   description line. Do NOT touch the body. Do NOT touch the installed
   copy at `~/.claude/skills/mc-distill/SKILL.md` (touching it would
   cause `install_builtins()` to mark it user-modified via hash mismatch
   and PRESERVE it — blocking future builtin updates).
2. Restart MC. `_install_builtin_skills()` runs at startup, sees source
   hash differs from marker, hash of installed copy still matches marker
   → safely updates installed copy to new source.
3. Verify: `~/.claude/skills/mc-distill/SKILL.md` now contains the new
   shorter description.

**Why source-only is the right path:** the install marker contract
(`skills.py` line ~414) explicitly treats user-modified installed copies
as preserved. Editing both source and dest would make the dest look
user-modified and break future propagation.

---

## 4. Validation — what to test after the fix

Run 5+ consecutive sessions across these conditions and check that
`mc-distill` is in every `skill_listing`:

| # | Condition | Expected |
|---|---|---|
| 1 | Fresh session, mission-control project, ≤5 turns | mc-distill in listing |
| 2 | Long session (30+ min, heavy context) in mission-control | mc-distill in listing |
| 3 | Session in a different project (e.g., engulfing-* or trading) | mc-distill in listing |
| 4 | Scribe session (queue-operation type) | mc-distill in listing |
| 5 | Session immediately after another long session | mc-distill in listing |

To check the listing, read line 5–8 of the session's `.jsonl` under
`~/.claude/projects/<sanitized-path>/<sid>.jsonl` and look for the
`"type":"skill_listing"` attachment.

If mc-distill is present in all 5: bug is fixed, proceed to part (b)
proactive-trigger validation.

If still flaky: the description-length hypothesis is wrong. Next
candidate hypotheses: special characters in the description (slashes,
brackets, unicode arrows), unusual frontmatter fields, or a CC bug we
need to escalate.

---

## 5. After fix is verified — proactive-trigger validation

Run sessions in these shapes and observe whether the proactive trigger
fires (or correctly doesn't):

| # | Scenario | Expected behavior |
|---|---|---|
| 1 | Quick (≤5-turn) session, fix one thing, end | Skill stays silent (no recurrence) |
| 2 | 15-turn debug session with no repeating pattern | Skill stays silent (no recurrence) |
| 3 | Same kind of fix done 3 times in one session, end clean | Skill fires `[Yes / Later / No]` at wrap-up |
| 4 | Same as #3 but session ends abruptly mid-task | Skill stays silent (no natural breakpoint) |
| 5 | User says "/distill" mid-session | Skill fires immediately, searches existing skills, proposes-or-declines |

Check `data/skills/_proposed/` for created proposals after #3 and #5.

If #3 doesn't fire when it should, the proactive bar is too high — tune
the description trigger language. If #1 or #2 fire when they shouldn't,
the bar is too low — tighten the recurrence rule wording.

---

## 6. Action checklist

- [ ] Read this doc top-to-bottom (you may already have via Ron).
- [ ] Apply the description fix to `data/skills/builtin/mc-distill/SKILL.md` ONLY.
- [ ] Verify the hash propagation will work: read `data/skills/builtin/mc-distill/SKILL.md` hash, compare to `~/.claude/skills/mc-distill/.mc_install_marker` (or whatever the marker file is called per `skills.py` `_INSTALL_MARKER`).
- [ ] Restart MC. Verify install_builtins logged `updated: ['mc-distill']`.
- [ ] Verify installed copy at `~/.claude/skills/mc-distill/SKILL.md` shows new shorter description.
- [ ] Run validation scenarios from §4 (visibility) — record results.
- [ ] If §4 passes: run validation scenarios from §5 (proactive trigger) — record results.
- [ ] If both pass: mark Phase A complete in `SKILLS_CURATION_DESIGN.md` open item #9; schedule Phase B (committee review).
- [ ] Commit everything (source SKILL.md fix, design doc changes, this handoff doc) with message like: `Phase A: fix mc-distill description bloat, design-doc open items #8 (PATCH.md schema) + #9 (visibility bug resolved)`.

---

## 7. Files touched this session (all untracked at handoff)

- `data/skills/builtin/mc-distill/SKILL.md` — Step 1 skill, 158 lines (description fix is the only change proposed).
- `docs/SKILLS_CURATION_DESIGN.md` — design doc, now 467 lines; open items #8 (PATCH.md schema) and #9 (visibility bug + root cause) added; item #7 committee focus list updated to reference both.
- `docs/MEMORY_SYSTEM.md` — open item #5 rewritten (drafted + Step 1 shipped).
- `CLAUDE.md` — new "Skills Curation" architectural section appended.
- `CHANGELOG.md` — `[2026-05-18f]` entry at top capturing the design + Step 1.
- `docs/PHASE_A_HANDOFF.md` — this doc.
