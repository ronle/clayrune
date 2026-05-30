---
name: mc-distill
description: Propose a reusable SKILL.md from the current session when a hard-won novel insight has emerged. TRIGGER on explicit user request ("/distill", "propose a skill", "do we have a pattern here") OR proactively at a natural breakpoint (end of task, after commit, wrap-up) when a single in-the-moment insight is worth bottling NOW. Cross-session recurrence is the silent Distiller's job (Phase 4). Writes only to data/skills/_proposed/ for human review; never auto-installs.
---

# Distill a SKILL.md proposal from the current session

This skill complements Phase 4 (the cross-session silent Distiller). Phase 4 catches patterns that recur across many sessions; this skill catches the novel in-the-moment insight from THIS session that's worth bottling immediately — because waiting weeks for cross-session recurrence to catch a fresh hard-won discovery is silly.

MC owns the skill registry — you propose, the human reviews and promotes by hand.

## Two ways this skill triggers

### 1. Explicit user invocation

The user says: "/distill", "propose a skill", "do we have a pattern here", "is this worth bottling".

Run through the full Procedure below.

### 2. Proactive (agent-initiated)

YOU notice an insight worth bottling and surface it without being asked.

**Rules for proactive triggering — ALL must hold:**

- **Natural breakpoint:** end of a task, just after a commit, or in a clear wrap-up moment. NEVER trigger mid-task, mid-debug, mid-investigation.
- **Strengthened specificity (v2 — pattern-bound vs session-bound):** you can name the insight in one sentence AND a future agent in a *different* session — possibly months later — would recognize the trigger from incoming context. The test: rewrite the proposal's TRIGGER phrasing without naming the specific session's symptom. Does anything reusable remain?
  - **Pattern-bound** (good): "when CF Access tokens lag in 'last used' display, check session_duration first — the dashboard timestamp is stale by design."
  - **Session-bound** (bad — war story, not skill): "when the Gemini SSE pill stuck on COMPLETED today, check turn_complete — 40min debug."
- **Not already covered:** quick search first (step 1 of Procedure).
- **Once per session, max** (v2 restoration): if you've already surfaced one proactive proposal this session — regardless of user response (Yes / Later / No) — do NOT surface another. Multiple genuinely distinct insights in one session is rare; if it happens, the user can invoke `/distill` explicitly for the second.

**Disposition:** if you noticed something worth bottling at a natural breakpoint AND it passes the strengthened specificity test, **say so**. Phase 4 catches what you missed across sessions; you catch what you noticed in-the-moment. The previous "err toward asking less" tone (now removed) over-corrected to zero proactive proposals across ~1199 sessions; the strengthened specificity + once-per-session cap is the v2 calibration.

**Proactive proposal format (inline message to the user):**

> Noticed an insight worth bottling: **\<one-line description\>**. Concrete observation: \<verbatim quote from session\>. Proposed skill name: `\<kebab-case-name\>`.
>
> Bottle this? **[Yes / Later / No]**

**User responses:**

- **Yes** → run through Procedure steps 1–5 and save the proposal as normal.
- **Later** → call `POST /api/project/<id>/distiller/record-push` with `{"phrase": "<kebab-case-name>", "kind": "skill", "decision": "later"}`. If the response is `{"accepted": true, ...}`, the silent Distiller will pick this up later once cross-session recurrence increments past the current count. If the response is `{"accepted": false, "reason": "distiller_disabled"}`, tell the user honestly that the marker wasn't written (Distiller is off for this project / globally) and `Later` won't auto-resurface.
- **No** → call `POST /api/project/<id>/distiller/record-push` with `{"phrase": "<kebab-case-name>", "kind": "skill", "decision": "no"}`. Suppresses this pattern for the silent Distiller too. If the endpoint returns `accepted: false`, the suppression isn't persistent — say so.

## Do NOT call this skill (either trigger) for

- Sessions where nothing memorable happened.
- Patterns already fully covered by an existing skill (search first — step 1 of Procedure).
- Tasks fully handled by Claude Code's defaults.
- Generic advice ("test your code", "be careful with git") — too vague to be a skill.
- Session-bound war stories that fail the pattern-bound-vs-session-bound test above.

## Procedure

### 1. Search for overlap first

```bash
curl -s "http://localhost:5199/api/skills/search?q=<keywords>&limit=5"
```

Use 2-4 keywords describing the pattern. If a strong match (score > 4) comes back and the match captures the same idea, this is an **UPDATE** proposal (step 4), not a new skill.

### 2. Decide if there's a novel insight worth proposing

Bar for a new-skill proposal:

- **Specific enough to be actionable** (not generic advice).
- **Pattern-bound** — a future agent in a different session would recognize the trigger condition from incoming context.
- **Not already part of Claude Code's default behavior.**
- **A future agent reading the description would know when to apply it** — the TRIGGER phrasing describes what the agent SEES (an error, a symptom, a file shape), not "when debugging X."

If nothing meets the bar, say so plainly and stop:

> "Nothing in this session looks worth bottling — [one-line reason]. No proposal written."

Refusing to draft a proposal when there's nothing real is the right call. Noise is the enemy.

### 3. Draft and save a new SKILL.md proposal

Standard SKILL.md format:

```
---
name: <kebab-case-name>
description: TRIGGER when <observable symptom user reports / observable file or error shape / observable screenshot characteristic> AND <observable>. <one-line action>.
distilled_manual: true
source_session: <session_id or ISO date>
---

# <Skill title>

## When to call

<concrete trigger conditions — observable, not "when debugging X">

## The pattern (or: How to use)

<the actionable content — specific steps, not advice>

## Anti-patterns (if applicable)

<what NOT to do — gold-standard skills include these>
```

Save:

```bash
SID="<session_id>"  # use timestamp like 2026-05-29T18-30 if unknown
mkdir -p "data/skills/_proposed/<project_id-or-global>/$SID"
# Write the SKILL.md you drafted to: data/skills/_proposed/<scope>/$SID/SKILL.md
```

Use `<project_id>` for project-specific patterns; use `global` for cross-project / operator-level patterns.

### 4. Draft and save an UPDATE.md proposal (instead of step 3, if step 1 found a match)

```
---
target_skill: <existing-skill-name>
target_action: edit
distilled_manual: true
source_session: <session_id>
---

# Proposed update to <existing-skill-name>

## What the existing skill says

<quote or summarize the relevant section>

## Proposed change

<prose description of the patch>

```diff
- old line
+ new line
```

## Why

<what happened this session that motivated the update>
```

Save to `data/skills/_proposed/<scope>/<sid>/UPDATE.md`.

### 5. Report back to the user

Tell them:

- Full path to the proposal file.
- 1-2 sentence summary of what it captures.
- How to promote (manual copy):

> "Drafted a proposal at `data/skills/_proposed/<scope>/<sid>/SKILL.md`. It captures [one line]. To promote:
> - Global: copy to `~/.claude/skills/<name>/SKILL.md`
> - Project-local: copy to `<project_path>/.claude/skills/<name>/SKILL.md`
> - Reject: delete `data/skills/_proposed/<scope>/<sid>/`"

**Do not AUTONOMOUSLY install. Proposals live in `data/skills/_proposed/` until the human approves promotion.** If, in the same turn or a subsequent turn within the same session, the user gives explicit promote instruction, see "Promotion on explicit user instruction" below.

## Promotion on explicit user instruction

The "MC owns, agent proposes, human promotes" rule means the human *approves* — not that the human has to type the copy command. If the user gives an explicit promote instruction, you EXECUTE the promotion (don't just hand them a command and stop).

### What counts as explicit promote instruction

YES — execute:
- "promote it globally"
- "yes, install it project-local"
- "ship it"
- "approve and promote"
- "yes, promote"
- "go ahead, install"

NO — do not promote, but acknowledge:
- "later" / "remind me" / "I'll review" → call record-push with `decision: later`, leave proposal in `_proposed/`
- "no" / "reject" / "delete it" → call record-push with `decision: no`, delete `_proposed/<scope>/<sid>/`
- silence / topic change → leave proposal, do nothing
- "save this" / "good idea" / "interesting" → ambiguous, ASK before executing
- "promote" with no scope specified → ASK which scope, do not assume

### Procedure when promote is explicit

1. **Confirm scope** if not stated. Ask: "Global (`~/.claude/skills/`) or project-local (`<project>/.claude/skills/`)?"
2. **Resolve destination path** based on scope and the skill name from the proposal's frontmatter:
   - Global: `~/.claude/skills/<skill-name>/SKILL.md`
   - Project-local: `<project_path>/.claude/skills/<skill-name>/SKILL.md`
3. **Check for collision (per-provenance rules — v2).** Read the existing SKILL.md's frontmatter:
   - **Existing skill is auto-authored (`auto_authored: true`):** lower-risk overwrite. Tell the user "An auto-authored skill with that name exists. Overwrite?" — proceed on yes.
   - **Existing skill is manually-authored or distilled-promoted:** high-risk overwrite. Show the user the first ~10 lines of the existing skill BEFORE asking — they need to see what they're about to destroy. Then ask "Overwrite, rename the new one, or abort?".
   - **Existing skill is a Clayrune built-in:** REJECT promotion. Built-ins are managed via source-file edit + `_install_builtin_skills()` propagation.
4. **Create destination directory**, then **copy** the proposal file (and any other files in the `_proposed/<scope>/<sid>/` directory).
5. **Delete the source `_proposed/<scope>/<sid>/` directory.**
6. **Report back** with the destination path.

### Hard rules

- **One scope per promotion.** Never promote to both global and project-local in the same action.
- **Same-session approval only.** Promote instructions from a separate previous session are not valid.
- **Never promote a proposal the user hasn't seen.**
- **UPDATE.md proposals follow the same rule.**

### Reversing a promotion (v2)

If the user explicitly asks to reverse a promotion that happened EARLIER IN THE SAME SESSION ("undo that", "revert", "actually no, reject it instead"):

1. **Confirm** the user means the promotion you just did (read back the destination path).
2. **Delete the promoted SKILL.md** at the destination.
3. **Restore the `_proposed/<scope>/<sid>/` directory.**
4. **Report:** `"Reversed. Promoted skill deleted from <path>; proposal restored to _proposed/<scope>/<sid>/."`

Cannot reverse a promotion from a previous session.

## Rollback (v2.1 — propagation paths)

The mc-distill skill propagates via `_install_builtin_skills()` from `data/skills/builtin/mc-distill/SKILL.md` to `~/.claude/skills/mc-distill/SKILL.md` on MC startup, when the installed copy's hash matches the previous source's marker (hash-marker scheme).

If the v2 mc-distill rules need to be rolled back:

1. **Hot revert** (within hours, no user edits yet): `git revert <commit>` + restart MC. `install_builtins()` re-propagates cleanly via hash-marker auto-update.
2. **Cold revert** (days/weeks, possible user edits): `git revert <commit>` + restart MC. Some users on hash-divergent installed copies will keep the v2 text (skills.py "preserved" branch). Audit checklist surfaces divergence; operator decides per-user whether to force-update.
3. **Hard revert** (force-update all): not supported by `install_builtins()` today (intentionally preserve-on-divergence). Manual file deletion of `~/.claude/skills/mc-distill/SKILL.md` followed by restart re-installs the source.

## Style

- **Be discriminating.** Most sessions don't warrant a proposal. Saying "nothing here" is good output.
- **Be specific.** "Always test your code" is a bad skill. "When editing `server.py`: run `pytest -q` before committing because import-time side-effects mean syntax-clean isn't behavior-clean" is a good one.
- **Be honest about scope.** If a pattern only applies to one project, name that project in the description so future sessions in unrelated projects don't pull it in.
- **No skill bodies longer than ~120 lines.** If you're writing more than that, you're capturing context, not a pattern.
