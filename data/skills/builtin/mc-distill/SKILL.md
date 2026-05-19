---
name: mc-distill
description: Propose a reusable SKILL.md from the current session when a pattern, workflow, gotcha, or rule worth bottling has emerged. TRIGGER on explicit user request ("/distill", "propose a skill", "do we have a pattern here") OR proactively when you notice a clear repeatable pattern (≥2 recurrences) at a natural breakpoint (end of task, after commit, wrap-up). See body for hard rules, proposal format, and once-per-session-max constraint. Writes only to data/skills/_proposed/<sid>/ for manual review; never auto-installs.
---

# Distill a SKILL.md proposal from the current session

This is the manual precursor to the future automated Distiller pipeline (see `docs/SKILLS_CURATION_DESIGN.md`). MC owns the skill registry — you propose, the human reviews and promotes by hand.

## Two ways this skill triggers

### 1. Explicit user invocation

The user says: "/distill", "propose a skill", "do we have a pattern here", "is this worth bottling".

Run through the full Procedure below.

### 2. Proactive (agent-initiated)

YOU notice a pattern worth bottling and surface it without being asked.

**Hard rules for proactive triggering — ALL must hold:**

- **Recurrence:** the pattern occurred ≥2 times observably in this session, OR the user has explicitly noted "we did this before."
- **Natural breakpoint:** you're at the end of a task, just after a commit, or in a clear wrap-up moment. NEVER trigger mid-task, mid-debug, mid-investigation.
- **Specificity:** you can name the pattern in one sentence and justify the recurrence with concrete observations from this session.
- **Not already covered:** quick search first (step 1 of Procedure).
- **Once per session, max.** If you've already proposed proactively in this session and the user accepted/deferred/declined, do not propose again. Wait for next session.

**Proactive proposal format (inline message to the user):**

> Noticed a pattern worth bottling: **\<one-line description\>**. Observed \<N\> times this session: \<concrete observations\>. Proposed skill name: `\<kebab-case-name\>`.
>
> Bottle this? **[Yes / Later / No]**

**User responses:**

- **Yes** → run through Procedure steps 1–5 and save the proposal as normal.
- **Later** → **v1 NOTE: until the silent Distiller backend ships (build-order Phase 4), `Later` is functionally equivalent to `No` — nothing is persisted for future pickup.** Be honest about this when the user picks Later: acknowledge the choice but tell them it won't auto-resurface, and offer to write a quick reminder note somewhere they'll see it (e.g., a backlog item). Do NOT pretend the silent Distiller will pick it up later.
- **No** → respect the call; do not propose this pattern again in this session.

## Do NOT call this skill (either trigger) for

- Sessions where nothing memorable happened.
- Patterns already fully covered by an existing skill (search first — step 1 of Procedure).
- Tasks fully handled by Claude Code's defaults.
- Generic advice ("test your code", "be careful with git") — too vague to be a skill.

## Procedure

### 1. Search for overlap first

```bash
curl -s "http://localhost:5199/api/skills/search?q=<keywords>&limit=5"
```

Use 2-4 keywords describing the pattern. If a strong match (score > 4) comes back and the match captures the same idea, this is an **UPDATE** proposal (step 4), not a new skill.

### 2. Decide if there's a pattern worth proposing

Bar for a new-skill proposal:

- Specific enough to be actionable (not generic advice).
- Recurs or is likely to recur in similar future sessions.
- Not already part of Claude Code's default behavior.
- A future agent reading the description would know when to apply it.

If nothing meets the bar, say so plainly and stop:

> "Nothing in this session looks worth bottling — [one-line reason]. No proposal written."

Refusing to draft a proposal when there's nothing real is the right call. Noise is the enemy.

### 3. Draft and save a new SKILL.md proposal

Standard SKILL.md format:

```
---
name: <kebab-case-name>
description: <when should this be triggered? what pattern does it address? include TRIGGER phrasing>
distilled_manual: true
source_session: <session_id or ISO date>
---

# <Skill title>

## When to call

<concrete trigger conditions>

## The pattern (or: How to use)

<the actionable content — specific steps, not advice>
```

Save:

```bash
SID="<session_id>"  # use timestamp like 2026-05-18T18-30 if unknown
mkdir -p "data/skills/_proposed/$SID"
# Write the SKILL.md you drafted to: data/skills/_proposed/$SID/SKILL.md
```

### 4. Draft and save an UPDATE.md proposal (instead of step 3, if step 1 found a match)

```
---
target_skill: <existing-skill-name>
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

Save to `data/skills/_proposed/<sid>/UPDATE.md`.

### 5. Report back to the user

Tell them:

- Full path to the proposal file.
- 1-2 sentence summary of what it captures.
- How to promote (manual copy):

> "Drafted a proposal at `data/skills/_proposed/<sid>/SKILL.md`. It captures [one line]. To promote:
> - Global: copy to `~/.claude/skills/<name>/SKILL.md`
> - Project-local: copy to `<project_path>/.claude/skills/<name>/SKILL.md`
> - Reject: delete `data/skills/_proposed/<sid>/`"

**Do not AUTONOMOUSLY install. Proposals live in `data/skills/_proposed/<sid>/` until the human approves promotion.** If, in the same turn or a subsequent turn within the same session, the user gives explicit promote instruction, see "Promotion on explicit user instruction" below.

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
- "later" / "remind me" / "I'll review" → leave proposal in `_proposed/`, do nothing
- "no" / "reject" / "delete it" → delete `_proposed/<sid>/`, confirm
- silence / topic change → leave proposal, do nothing
- "save this" / "good idea" / "interesting" → ambiguous, ASK before executing
- "promote" with no scope specified → ASK which scope, do not assume

### Procedure when promote is explicit

1. **Confirm scope** if not stated. Ask: "Global (`~/.claude/skills/`) or project-local (`<project>/.claude/skills/`)?"
2. **Resolve destination path** based on scope and the skill name from the proposal's frontmatter:
   - Global: `~/.claude/skills/<skill-name>/SKILL.md`
   - Project-local: `<project_path>/.claude/skills/<skill-name>/SKILL.md`
3. **Check for collision (per-provenance rules — v2).** If a skill with that name already exists at the target scope, the action depends on what kind of existing skill it is. Read the existing SKILL.md's frontmatter:
   - **Existing skill is auto-authored (`auto_authored: true`):** lower-risk overwrite. Tell the user "An auto-authored skill with that name exists. Overwrite?" — proceed on yes.
   - **Existing skill is manually-authored or distilled-promoted (no `auto_authored` flag, or `provenance: manual`/`interactive`/`distilled`):** high-risk overwrite. Show the user the first ~10 lines of the existing skill's frontmatter and body BEFORE asking — they need to see what they're about to destroy. Then ask "Overwrite, rename the new one, or abort?".
   - **Existing skill is a Clayrune built-in (lives under `data/skills/builtin/` AND has an `_mc_install_marker` file in its installed location):** REJECT promotion. Built-ins are managed via source-file edit + `_install_builtin_skills()` propagation; never overwrite them through promotion. Tell the user: "That name conflicts with a built-in skill. Built-ins are managed via `data/skills/builtin/<name>/SKILL.md` — propose your changes there as a SKILL.md edit, not as a promotion."
   - **In `auto` mode (Phase 5+, not yet shipped):** name collisions abort the auto-promotion. The proposal stays in `_proposed/<sid>/` with a collision marker, and the monthly audit surfaces it. NEVER auto-rename — that creates name drift.
4. **Create destination directory** (`mkdir -p` equivalent), then **copy** the proposal file (and any other files in `_proposed/<sid>/` — references, scripts).
5. **Delete the source `_proposed/<sid>/` directory.** The proposal lifecycle ends with promotion or rejection — no orphaned proposals.
6. **Report back** with the destination path. Example: `"Promoted to ~/.claude/skills/frontend-render-hang-diagnostic/SKILL.md. CC will load it in new sessions."`

### Hard rules

- **One scope per promotion.** Never promote to both global and project-local in the same action.
- **Same-session approval only.** Promote instructions from a separate previous session are not valid — the user reviewing in the current session is the gate.
- **Never promote a proposal the user hasn't seen.** If they say "promote it" before reviewing the proposal content, offer to summarize or paste it first.
- **UPDATE.md proposals follow the same rule.** Applying a patch to an existing skill on explicit user instruction is valid; doing it autonomously is not.

### Reversing a promotion (v2)

If the user explicitly asks to reverse a promotion that happened EARLIER IN THE SAME SESSION ("undo that", "revert", "actually no, reject it instead"), you MAY:

1. **Confirm** the user means the promotion you just did (read back the destination path).
2. **Delete the promoted SKILL.md** at the destination.
3. **Restore the `_proposed/<sid>/` directory** — the proposal is once again awaiting review.
4. **Report:** `"Reversed. Promoted skill deleted from <path>; proposal restored to _proposed/<sid>/."`

Cannot reverse a promotion from a previous session — the user must do it manually or via the future Skills UI. The same-session restriction prevents the agent from being asked weeks later to "undo that thing from before" with no context.

## Tone

- **Be discriminating.** Most sessions don't warrant a proposal. Saying "nothing here" is good output.
- **Be specific.** "Always test your code" is a bad skill. "When editing `server.py`: run `pytest -q` before committing because import-time side-effects mean syntax-clean isn't behavior-clean" is a good one.
- **Be honest about scope.** If a pattern only applies to one project, name that project in the description so future sessions in unrelated projects don't pull it in.
- **No skill bodies longer than ~120 lines.** If you're writing more than that, you're capturing context, not a pattern.
- **For proactive triggers specifically: err toward asking less.** Annoyance is a failure mode. If you're unsure whether a pattern is worth proposing, don't propose. The user can always invoke `/distill` explicitly if they think you missed something.
