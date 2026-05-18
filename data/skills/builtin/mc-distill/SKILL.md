---
name: mc-distill
description: Propose a reusable SKILL.md from the current session — when a pattern, workflow, gotcha, or rule worth bottling has emerged. TRIGGER on EXPLICIT user request ("/distill", "propose a skill", "do we have a pattern here", "is this worth a skill"). ALSO TRIGGER PROACTIVELY when YOU (the agent) notice a clear repeatable pattern in this session with ≥2 observed recurrences AND a natural breakpoint has been reached (end of task, after commit, wrap-up) — propose inline to the user with a specific name, recurrence justification, and [Yes / Later / No] choice. NEVER trigger proactively mid-task, mid-debug, or for vague generic patterns. Once per session maximum for proactive triggers. Writes proposals to data/skills/_proposed/<session_id>/SKILL.md (or UPDATE.md for patches to existing skills) for manual review. NEVER auto-installs. NEVER writes to ~/.claude/skills/ or any project's .claude/skills/.
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
- **Later** → don't write now. The future automated Distiller (when built) will pick this up at session end via the silent path; for now, just acknowledge and move on.
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

**Do not auto-install. Do not write to `~/.claude/skills/` or any `<project>/.claude/skills/` directory. Proposals live in `data/skills/_proposed/<sid>/` until the human promotes them.**

## Tone

- **Be discriminating.** Most sessions don't warrant a proposal. Saying "nothing here" is good output.
- **Be specific.** "Always test your code" is a bad skill. "When editing `server.py`: run `pytest -q` before committing because import-time side-effects mean syntax-clean isn't behavior-clean" is a good one.
- **Be honest about scope.** If a pattern only applies to one project, name that project in the description so future sessions in unrelated projects don't pull it in.
- **No skill bodies longer than ~120 lines.** If you're writing more than that, you're capturing context, not a pattern.
- **For proactive triggers specifically: err toward asking less.** Annoyance is a failure mode. If you're unsure whether a pattern is worth proposing, don't propose. The user can always invoke `/distill` explicitly if they think you missed something.
