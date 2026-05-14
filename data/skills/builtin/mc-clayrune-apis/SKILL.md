---
name: mc-clayrune-apis
description: Use whenever you need to interact with Clayrune local APIs — backlog management, scheduler routines, hivemind orchestration, process registration, terminal pop-out, or any localhost:5199 endpoint. TRIGGER when the user mentions "backlog", "schedule", "hivemind", "register this process", "open a terminal", or refers to Clayrune by name.
---

# Clayrune API surface

Clayrune runs locally at **http://localhost:5199**. Every Clayrune-aware operation is a curl call to this base URL.

## Process Registration — MANDATORY for any spawned process

If you spawn a background process, server, bot, or any long-running command, you MUST register it. Unregistered processes cannot be monitored or stopped by the user.

```bash
# 1) Spawn and capture PID
mycommand &
PID=$!

# 2) Register
curl -s -X POST http://localhost:5199/api/processes/register \
  -H "Content-Type: application/json" \
  -d "{\"pid\":$PID,\"name\":\"Short description\",\"project_id\":\"<pid>\",\"command\":\"the command run\"}"
```

PID must be an integer. Do not skip registration.

## Backlog (per-project task list)

When the user says "backlog", "backlog items", "the list" — they mean THIS, not files on disk.

- Read: `GET /api/project/<pid>/backlog`
- Update: `PATCH /api/project/<pid>/backlog/<item_id>` with `{"status":"done"}` — status values: `open`, `in_progress`, `blocked`, `done`
- Add note: `POST /api/project/<pid>/backlog/<item_id>/note` with `{"text":"..."}`
- Add item: `POST /api/project/<pid>/backlog` with `{"title":"...","priority":"high|medium|low","note":"..."}`

## Scheduler (Clayrune LOCAL — for jobs that outlive a session)

For long-term, repeatable jobs that should re-run an agent inside Clayrune after the current conversation ends.

- List: `GET /api/schedules`
- Create: `POST /api/schedules` with:
  ```json
  {"project_id":"...","task":"...","schedule_type":"daily|weekly|interval|once|cron",
   "time":"09:00","days":[],"interval_minutes":60,"run_at":"ISO8601","cron_expr":"..."}
  ```
- Update: `PUT /api/schedules/<id>`
- Delete: `DELETE /api/schedules/<id>`

**Picker rule:** if the job must still fire after this conversation ends → Clayrune scheduler. If it's a tight in-session poll loop (e.g. "check the build every 5 min") → Anthropic `/schedule` skill instead.

## Hivemind (multi-agent orchestration)

To create a hivemind (orchestrator decomposes a goal and spawns workers):

```bash
curl -s -X POST http://localhost:5199/api/hivemind/create \
  -H "Content-Type: application/json" \
  -d '{"project_id":"<pid>","goal":"GOAL TEXT","max_concurrent_workers":3,
       "orchestrator_model":"sonnet","worker_model":"sonnet"}'
```

Before creating, ask the user clarifying questions about scope, priorities, and constraints.

## Terminal pop-out

For visual commands, dashboards, or long-running processes the user should see:

```bash
curl -s -X POST http://localhost:5199/api/terminal/launch \
  -H "Content-Type: application/json" \
  -d '{"project_id":"<pid>","command":"<CMD>"}'
```

Output appears in Clayrune's terminal pop-out with full ANSI color.

## API discovery

When you need a Clayrune feature you haven't used before, do NOT guess endpoint names. Either:
- Grep `server.py` for `@app.route` to enumerate real endpoints, or
- `curl http://localhost:5199/` and inspect the served HTML.
