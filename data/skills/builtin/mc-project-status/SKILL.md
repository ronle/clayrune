---
name: mc-project-status
description: Summarize the current state of a Clayrune project — open backlog items, recent agent runs, active hiveminds, scheduled jobs, registered processes. TRIGGER when the user asks "what's the status of X", "give me a project overview", "what's open", "what's been happening here", "where are we", or any request for a project-state recap.
---

# Clayrune — Project Status Summary

When the user asks for a project overview, query Clayrune's APIs and present a structured snapshot.

## Steps

### 1. Identify the project

If you're already running inside a project session, you know your `project_id`. Otherwise, ask the user which project, or list all:

```bash
curl -s http://localhost:5199/api/projects | python -m json.tool
```

### 2. Pull each surface

Run these in parallel where possible:

```bash
PID=<project_id>

# Backlog
curl -s http://localhost:5199/api/project/$PID/backlog

# Recent agent runs (transcripts)
curl -s "http://localhost:5199/api/project/$PID/conversations?limit=10"

# Scheduled jobs
curl -s http://localhost:5199/api/schedules | jq "[.[] | select(.project_id==\"$PID\")]"

# Active hiveminds
curl -s http://localhost:5199/api/hivemind/list | jq "[.[] | select(.project_id==\"$PID\")]"

# Registered processes
curl -s http://localhost:5199/api/processes | jq "[.[] | select(.project_id==\"$PID\")]"
```

### 3. Synthesize the report

Present a structured summary, not a raw dump. Sections:

- **Open backlog** — count of open / in-progress / blocked items, highlight high-priority titles
- **Recent activity** — last 3-5 agent sessions: what they worked on (use `last_user` field, not `first_user`)
- **Active automations** — running hiveminds, upcoming scheduled jobs (next fire time)
- **Live processes** — anything registered and still alive
- **Headline** — one sentence at the top: "Mostly idle — 3 open backlog items, last agent run 2 hours ago" or "Busy — 1 active hivemind, daily report scheduled for 09:00, 12 open items"

### 4. Suggest next actions

If anything looks blocked or stale (e.g. a hivemind that hasn't progressed in 24h, scheduled job that failed last run, backlog item flagged urgent), call it out and suggest a concrete next step.

## Tone

- Headline first, details second.
- Numbers over adjectives ("3 items" not "a few items").
- Don't pad — if nothing's happening, say "Quiet — 0 open items, no active automations."
