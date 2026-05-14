---
name: mc-skill-broker
description: Search Clayrune for skills that might help the current task — including skills authored in OTHER projects that your session doesn't normally see. TRIGGER when you suspect there's a procedure, playbook, or template that could help but you haven't been shown one in your active skill set. Also TRIGGER when the user mentions they "wrote something like this before" or asks "do we have a skill for X?".
---

# Skill broker — cross-project skill discovery

Clayrune merges global skills + the current project's skills into your active set at session start. **It does NOT show you skills authored inside OTHER projects.** This skill bridges that gap.

## When to call this

- You're about to write a procedure from scratch and suspect one already exists.
- The user says "we have a playbook for this somewhere" — but you don't see it.
- The user asks "do we have a skill for X?" — even if you have a partial match, search to be sure.
- A task in one project resembles work done in another.

Do NOT call this skill for:
- General web searches or Anthropic documentation lookups.
- Tasks fully covered by a skill already in your active set.

## How to use

### 1. Search

```bash
curl -s "http://localhost:5199/api/skills/search?q=<task+description>&limit=10"
```

Use natural-language keywords. The endpoint scores skills by keyword overlap across name, description, and body.

Response shape:
```json
[
  {"name": "release-checklist", "scope": "project", "project_id": "foo",
   "description": "...", "body_excerpt": "...", "score": 6.0}
]
```

### 2. Inspect candidates

For any high-scoring candidate, read its full body:

```bash
# Global skill
curl -s "http://localhost:5199/api/skills/global/<name>?include_body=true"

# Project-scoped skill
curl -s "http://localhost:5199/api/skills/project/<name>?project_id=<pid>&include_body=true"
```

### 3. Decide how to use it

Two patterns, depending on user context:

**Read-and-follow (default).** Read the body, mentally apply the playbook to the current task, proceed. Don't install — the skill stays in its original project, the current project stays clean.

**Install on user confirmation.** If the user wants this skill available to *future* sessions in the current project, ask:

> "I found a `release-checklist` skill in project Y. Want me to copy it into THIS project so it's always available here?"

On yes:

```bash
# Fetch the full content
SKILL=$(curl -s "http://localhost:5199/api/skills/project/<name>?project_id=<source-pid>&include_body=true")

# Re-create it in the current project's scope
curl -s -X POST http://localhost:5199/api/skills \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"<name>\",\"description\":\"...\",\"body\":\"...\",\"scope\":\"project\",\"project_id\":\"<current-pid>\"}"
```

## Tone

When reporting search results back to the user:
- Lead with the most relevant match.
- Quote 1-2 lines from the description so they recognize the skill.
- Suggest read-and-follow vs install — don't auto-install.
- If nothing relevant found, say so plainly. Don't pad.
