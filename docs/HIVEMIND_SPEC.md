# Hivemind — Persistent Multi-Agent Collaborative Intelligence

## Feature Specification v1.1
**Project:** Mission Control
**Author:** Ron + Claude
**Date:** 2026-03-23
**Revised:** 2026-03-23 (v1.1 — architecture corrections and gap-fills)
**Status:** Draft — open for review

---

## 1. Vision

Hivemind enables **coordinated groups of AI agents** that work on a shared goal, accumulate domain expertise over time, and persist their collective knowledge across sessions, restarts, and days/weeks of ongoing work.

Unlike ephemeral multi-agent systems (including Claude Code's built-in Agent Teams), Hivemind treats agents as **disposable workers** while making the **knowledge permanent**. Each session builds on everything learned before. Over time, the hivemind develops deep expertise in its domain — agents that resume a workstream inherit all accumulated findings and effectively become instant experts.

### Core Principles

1. **Knowledge outlives agents** — Findings, decisions, and reasoning persist independently of agent sessions
2. **Incremental expertise** — Each session advances the frontier; no work is repeated
3. **User sovereignty** — The user sees everything, controls everything, can intervene at any point
4. **Cross-pollination** — Agents share discoveries; insights from one workstream inform all others
5. **Resumability** — Any hivemind can be paused and continued later with full context preservation
6. **CLI-native** — All Claude intelligence is invoked through the same `claude` CLI subprocess infrastructure used everywhere in Mission Control; no direct API calls

---

## 2. Concepts & Terminology

| Term | Definition |
|------|-----------|
| **Hivemind** | A persistent collaborative effort with a shared goal, containing multiple workstreams |
| **Workstream** | A focused area of investigation/work within the hivemind, owned by one agent at a time |
| **Server orchestrator** | A Python state machine in `server.py` that tracks workstream statuses, resolves dependencies, schedules worker spawns, and routes findings — deterministic logic, no Claude involved |
| **Orchestrator CLI session** | A short-lived `claude -p` subprocess (same pattern as memory condensation) invoked at intelligence moments: goal decomposition, synthesis, adaptive re-planning |
| **Worker** | A standard MC agent session (Mode A or B) assigned to a specific workstream — disposable, replaceable, but inherits all prior knowledge on spawn |
| **Knowledge Base** | The persistent store of findings, decisions, messages, and synthesis — the hivemind's long-term memory |
| **Message Bus** | The communication channel between agents — all messages are persisted |
| **Synthesis** | A periodically-updated human-readable summary of everything the hivemind has learned, produced by an orchestrator CLI session |
| **Escalation** | When an agent surfaces a decision or blocker to the user for input |

---

## 3. Data Model

### 3.1 Directory Structure

```
data/hiveminds/{hivemind_id}/
  manifest.json                    # Core metadata and configuration
  workstreams/
    {ws_id}.json                   # Workstream definition and status
    {ws_id}_findings.jsonl         # Append-only findings log (source of truth)
    {ws_id}_context.md             # Derived working summary — updated by orchestrator CLI sessions
  knowledge/
    synthesis.md                   # Running synthesis (produced by orchestrator CLI sessions)
    decisions.jsonl                # Decisions made + rationale
    open_questions.jsonl           # Unresolved questions for future sessions
  bus/
    messages.jsonl                 # Complete inter-agent message history
  sessions/
    {session_timestamp}.json       # Per-session snapshot (who ran, what changed)
```

**Key distinction — findings vs. context:**
- `{ws_id}_findings.jsonl` is the **source of truth** — append-only, never edited, full record of every finding
- `{ws_id}_context.md` is the **derived working summary** — mutable, updated by orchestrator CLI sessions or condensation; this is what gets injected into workers at spawn time
- On worker spawn, only `_context.md` + the last N findings (default: 20) are injected — the full JSONL is never read at spawn time (could be thousands of lines)


### 3.2 Manifest (`manifest.json`)

```json
{
  "id": "hm_engulfing_analysis",
  "project_id": "trading_research",
  "title": "Engulfing Pattern Deep Analysis",
  "goal": "Comprehensive analysis of engulfing pattern data: detection, classification, statistical edge, multi-timeframe correlation, and optimal trade parameters",
  "status": "active",
  "created_at": "2026-03-23T14:00:00Z",
  "updated_at": "2026-03-25T09:30:00Z",
  "session_count": 5,
  "config": {
    "max_concurrent_workers": 3,
    "auto_synthesize": true,
    "synthesize_interval_turns": 10,
    "require_user_approval_for_decisions": false,
    "orchestrator_model": "sonnet",
    "worker_model": "sonnet",
    "max_retries_per_workstream": 2
  }
}
```

Status values: `pending` | `active` | `paused` | `stopped` | `completed`

### 3.3 Workstream (`workstreams/{ws_id}.json`)

```json
{
  "id": "ws_002",
  "title": "False Positive Classification",
  "description": "Identify and categorize false positive engulfing patterns. Build a taxonomy of unreliable signals and determine filtering criteria.",
  "status": "completed",
  "dependencies": ["ws_001"],
  "priority": 1,
  "model": "sonnet",
  "created_at": "2026-03-23T14:05:00Z",
  "completed_at": "2026-03-24T16:20:00Z",
  "findings_count": 14,
  "sessions_used": 3,
  "retry_count": 0,
  "current_agent_session_id": null,
  "last_agent_session_id": "abc-123-def"
}
```

Status values: `pending` | `active` | `blocked` | `completed` | `paused` | `failed`

**New fields vs. v1.0:**
- `priority` — integer, lower = higher priority. Used by the server orchestrator when more workstreams are ready than there are worker slots. Default: 5.
- `model` — overrides `manifest.config.worker_model` for this specific workstream. Allows high-complexity workstreams to use Opus while data-processing ones use Haiku.
- `retry_count` — tracks how many times this workstream has been retried after failure.

### 3.4 Findings (`workstreams/{ws_id}_findings.jsonl`)

Append-only. Each line:

```json
{
  "id": "f_002_007",
  "timestamp": "2026-03-24T10:15:00Z",
  "session_id": "abc-123-def",
  "type": "finding",
  "title": "Asian session false positive rate",
  "content": "Engulfing patterns forming during Asian session (00:00-08:00 UTC) have a 2.1x higher false positive rate compared to London/NY sessions. Sample: n=203 Asian vs n=412 London/NY.",
  "confidence": "high",
  "evidence": "Statistical analysis of 2-year BTC/USDT 4H data",
  "tags": ["session-timing", "false-positive", "statistical"],
  "user_reviewed": false
}
```

### 3.5 Messages (`bus/messages.jsonl`)

```json
{
  "id": "msg_047",
  "timestamp": "2026-03-24T11:30:00Z",
  "from": "ws_002",
  "to": "orchestrator",
  "type": "finding_report",
  "content": "Completed false positive taxonomy. 14 categories identified. Strongest filter: wick-to-body ratio < 0.4 reduces FP by 38%.",
  "references": ["f_002_007", "f_002_012"]
}
```

Message types: `finding_report` | `question` | `answer` | `status_update` | `escalation` | `directive` | `synthesis_update`

### 3.6 Decisions (`knowledge/decisions.jsonl`)

```json
{
  "id": "d_003",
  "timestamp": "2026-03-24T12:00:00Z",
  "workstream": "ws_002",
  "decision": "Use close-to-close measurement for pattern size, not wick-to-wick",
  "rationale": "Wick-to-wick includes noise from liquidity grabs and produces inconsistent measurements across timeframes.",
  "decided_by": "orchestrator",
  "user_approved": true,
  "impacts": ["ws_003", "ws_005"]
}
```


---

## 4. Agent Architecture

### 4.1 Server Orchestrator (Python state machine)

The server orchestrator is **pure Python logic in `server.py`** — no Claude session. It runs as part of the existing Flask server and is responsible for all deterministic coordination:

1. **Dependency resolution** — determines which workstreams are ready to run based on their `dependencies` and current statuses
2. **Worker scheduling** — selects which ready workstreams to spawn next, respecting `max_concurrent_workers` and `priority` ordering
3. **Finding routing** — when a finding is posted to the bus, identifies dependent workstreams and queues the finding for injection into their next worker spawn
4. **Escalation delivery** — when an escalation message arrives on the bus, triggers a toast notification, updates the project activity log, and sets a persistent badge on the hivemind
5. **Liveness monitoring** — detects stalled or failed workers (similar to the scheduler's liveness sweep); triggers retry or escalation as appropriate

The server orchestrator does NOT decompose goals, write synthesis, or make adaptive decisions — those require intelligence and are delegated to orchestrator CLI sessions.

**Precedent:** The existing scheduler background thread in `server.py` is a direct model for the server orchestrator — a background thread that fires on a timer, checks state, and spawns subprocesses.

### 4.2 Orchestrator CLI Sessions

Orchestrator CLI sessions are **short-lived `claude -p` subprocesses** invoked by the server orchestrator at specific intelligence moments. They follow exactly the same pattern as the existing memory condensation housekeeping agent:

- Spawned via `subprocess.Popen` with `claude -p <prompt> --max-turns 5 --model <orchestrator_model>`
- Marked with `housekeeping: True` in the agent log so their completion does not trigger memory appends or further condensation
- Registered in the Process Manager like all other subprocesses
- Visible in the agent log but clearly labelled as orchestrator sessions

**When orchestrator CLI sessions are invoked:**

| Trigger | Task | Output |
|---------|------|--------|
| Hivemind creation | Decompose goal into initial workstreams | Creates `workstreams/{ws_id}.json` files via bus API |
| Every N worker turns (configurable) | Synthesize all findings into `knowledge/synthesis.md` | Writes via `PUT /api/hivemind/{id}/knowledge/synthesis` |
| Worker escalation received | Assess escalation, decide response or surface to user | Posts directive or escalation to bus |
| Workstream stall detected | Re-plan: adjust workstream scope, add/merge workstreams | Updates workstream definitions via API |
| User explicit request | On-demand synthesis or re-planning | Same as periodic paths |

**Orchestrator CLI session system prompt structure:**

```
You are the orchestrator of a Hivemind analysis. You will be given a specific task.
Complete only that task and exit.

GOAL: {manifest.goal}

CURRENT WORKSTREAM STATE:
{workstream statuses and finding counts, loaded from workstream JSONs}

KNOWLEDGE BASE SUMMARY:
{loaded from knowledge/synthesis.md}

RECENT DECISIONS:
{loaded from knowledge/decisions.jsonl, last 10}

OPEN QUESTIONS:
{loaded from knowledge/open_questions.jsonl}

YOUR TASK: {specific task — decompose / synthesize / re-plan / respond to escalation}

YOUR CAPABILITIES (use curl to call these):
- Post to message bus: curl -X POST http://localhost:5199/api/hivemind/{id}/bus/post -d '{...}'
- Create workstream: curl -X POST http://localhost:5199/api/hivemind/{id}/workstreams/create -d '{...}'
- Update workstream: curl -X PUT http://localhost:5199/api/hivemind/{id}/workstreams/{ws_id} -d '{...}'
- Update synthesis: curl -X PUT http://localhost:5199/api/hivemind/{id}/knowledge/synthesis -d '{...}'
- Escalate to user: curl -X POST http://localhost:5199/api/hivemind/{id}/escalate -d '{...}'

Complete your task, call the appropriate API endpoints, then stop. Do not start new tasks.
```

The `_hivemind_orchestrating` set (analogous to `_condensing_projects`) prevents double-dispatch of orchestrator sessions for the same hivemind.


### 4.3 Worker Agents

Each worker is a standard MC agent session (Mode A or Mode B), spawned by the server orchestrator. Workers receive a workstream-specific system prompt injected via `--append-system-prompt`:

```
You are a specialist agent in a Hivemind analysis.

YOUR WORKSTREAM: {ws.title}
YOUR BRIEF: {ws.description}

ACCUMULATED CONTEXT (from previous sessions on this workstream):
{loaded from workstreams/{ws_id}_context.md}

RECENT FINDINGS FROM THIS WORKSTREAM (last 20):
{loaded from workstreams/{ws_id}_findings.jsonl, last 20 entries}

RELEVANT FINDINGS FROM OTHER WORKSTREAMS:
{filtered from bus/messages.jsonl — only findings tagged as relevant to this workstream}

DECISIONS THAT AFFECT YOUR WORK:
{filtered from knowledge/decisions.jsonl by ws.impacts}

YOUR CAPABILITIES (use curl to call these):
- Report a finding: curl -X POST http://localhost:5199/api/hivemind/{id}/bus/post \
    -d '{"from":"{ws_id}","type":"finding_report","title":"...","content":"..."}'
- Ask a question: curl -X POST http://localhost:5199/api/hivemind/{id}/bus/post \
    -d '{"from":"{ws_id}","type":"question","to":"ws_xxx","content":"..."}'
- Report a blocker: curl -X POST http://localhost:5199/api/hivemind/{id}/bus/post \
    -d '{"from":"{ws_id}","type":"escalation","content":"..."}'
- Mark complete: curl -X POST http://localhost:5199/api/hivemind/{id}/workstreams/{ws_id}/status \
    -d '{"status":"completed"}'

RULES:
1. Build on accumulated context — do NOT repeat analysis already completed
2. Report findings as you discover them (do not batch at the end)
3. Reference evidence and data for all findings
4. If you need information from another workstream, ask via the bus
5. If you encounter a decision point that affects other workstreams, escalate
6. Do NOT write to the project MEMORY.md — your findings go to the bus only
```

**Memory policy for workers:** Workers must not write to the project `MEMORY.md`. Their knowledge goes exclusively to the hivemind knowledge base via the bus API. The server orchestrator may optionally write a top-level summary to project `MEMORY.md` at synthesis time (one-way bridge: knowledge base → project memory). Project `MEMORY.md` content is also excluded from worker context injection to prevent cross-contamination.

### 4.4 Context Injection Budget

When building the worker system prompt, the server applies the same context budget logic as the main agent dispatch:

1. Load `{ws_id}_context.md`
2. Load last 20 findings from `{ws_id}_findings.jsonl`
3. Load relevant bus messages (cross-workstream findings tagged for this workstream)
4. Load applicable decisions from `knowledge/decisions.jsonl`
5. Check combined size — if over threshold (default 20KB), trigger context condensation before spawning

Context condensation for hivemind workstreams follows the same pattern as the existing `_auto_condense_memory()` flow: a short-lived `claude -p` housekeeping session reads the full findings and context, produces a condensed `{ws_id}_context.md`, and then the worker is spawned with the condensed context.

### 4.5 Error Recovery

Worker failures are expected in long-running hiveminds (context overflow, timeout, bad data). The server orchestrator handles these explicitly:

- **Transient failures** (context overflow, timeout, unexpected exit): auto-retry up to `manifest.config.max_retries_per_workstream`. Each retry spawns a fresh worker that inherits all findings posted before the failure. `workstream.retry_count` is incremented on each attempt.
- **Unrecoverable failures** (repeated failures after max retries, or agent posts an escalation of type `blocker`): workstream status set to `failed`; server orchestrator escalates to user with a summary of what was attempted and what failed.
- **Stall detection**: if a worker session has been running for > N minutes with no new findings posted to the bus, the server orchestrator flags it as potentially stalled and escalates to user.

The `_condensing_projects` guard pattern is reused: a `_retrying_workstreams` set prevents double-spawn on retry.


---

## 5. Server API

### 5.1 Hivemind Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/hivemind/create` | Create hivemind from goal + project; triggers orchestrator CLI session for goal decomposition |
| `GET` | `/api/hivemind/{id}` | Get full hivemind state |
| `GET` | `/api/hivemind/list` | List all hiveminds (optionally by project) |
| `PUT` | `/api/hivemind/{id}` | Update hivemind config |
| `POST` | `/api/hivemind/{id}/start` | Start/resume the hivemind — server orchestrator re-evaluates state and spawns ready workers |
| `POST` | `/api/hivemind/{id}/pause` | Graceful pause: send checkpoint directive to all active workers, wait for acknowledgment, then set status to `paused`. Use `?force=true` for hard stop (kills all worker processes immediately via Process Manager kill API). |
| `POST` | `/api/hivemind/{id}/stop` | Hard stop all agents, mark inactive |
| `DELETE` | `/api/hivemind/{id}` | Archive a hivemind |

### 5.2 Workstream Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/hivemind/{id}/workstreams/create` | Add a workstream |
| `GET` | `/api/hivemind/{id}/workstreams` | List all workstreams with status |
| `PUT` | `/api/hivemind/{id}/workstreams/{ws_id}` | Update workstream definition |
| `POST` | `/api/hivemind/{id}/workstreams/{ws_id}/status` | Update status |
| `POST` | `/api/hivemind/{id}/workstreams/{ws_id}/spawn` | Spawn a worker agent for this workstream |

### 5.3 Message Bus

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/hivemind/{id}/bus/post` | Post a message to the bus |
| `GET` | `/api/hivemind/{id}/bus/poll/{ws_id}` | Poll messages directed at a workstream |
| `GET` | `/api/hivemind/{id}/bus/stream` | SSE stream of all bus activity |
| `GET` | `/api/hivemind/{id}/bus/history` | Full message history (paginated) |

### 5.4 Knowledge Base

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/hivemind/{id}/knowledge/synthesis` | Current synthesis document |
| `PUT` | `/api/hivemind/{id}/knowledge/synthesis` | Update synthesis (called by orchestrator CLI session) |
| `GET` | `/api/hivemind/{id}/knowledge/decisions` | All decisions |
| `GET` | `/api/hivemind/{id}/knowledge/findings` | All findings across workstreams |
| `POST` | `/api/hivemind/{id}/escalate` | Post an escalation (called by workers or orchestrator CLI sessions) |

### 5.5 User Intervention

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/hivemind/{id}/intervene` | User sends directive to orchestrator or specific workstream |
| `POST` | `/api/hivemind/{id}/findings/{f_id}/review` | User approves/rejects a finding |
| `POST` | `/api/hivemind/{id}/decisions/{d_id}/approve` | User approves/rejects a decision |

### 5.6 SSE Event Types

Hivemind events flow through the existing SSE infrastructure. New event types (prefixed `hivemind_`) are added alongside existing agent event types:

| Event | Payload | Description |
|-------|---------|-------------|
| `hivemind_finding` | `{hivemind_id, ws_id, finding}` | New finding posted to bus |
| `hivemind_workstream` | `{hivemind_id, ws_id, status}` | Workstream status changed |
| `hivemind_escalation` | `{hivemind_id, ws_id, message, escalation_id}` | User action required |
| `hivemind_synthesis` | `{hivemind_id, updated_at}` | `synthesis.md` updated |
| `hivemind_worker_spawned` | `{hivemind_id, ws_id, session_id}` | Worker agent started |
| `hivemind_worker_done` | `{hivemind_id, ws_id, session_id, status}` | Worker completed or failed |
| `hivemind_message` | `{hivemind_id, message}` | General bus message |

These events are pushed to the project's SSE stream and consumed by the frontend's existing `connectAgentStream()` handler, extended to route `hivemind_*` events to the Hivemind tab.


---

## 6. Frontend Design

### 6.1 Entry Points

- **Project tile badge** — Shows active hivemind indicator (honeycomb icon with worker count). Red badge overlay when there is an unresolved escalation.
- **Project modal** — New "Hivemind" tab alongside Backlog, Agent, Agent Log, Activity
- **Standalone hivemind modal** — Full-width view for detailed monitoring (same draggable/resizable pattern as Plan Viewer)

### 6.2 Hivemind Tab (in project modal)

```
┌──────────────────────────────────────────────────────────────┐
│  Hivemind: Engulfing Pattern Deep Analysis          [⏸] [⏹] │
│  Session 5 · 14 findings · 3 decisions · 2hr 34min total    │
├──────────────────────────────────────────────────────────────┤
│  Goal: Comprehensive analysis of engulfing pattern data...   │
│                                                              │
│  Workstreams:                                                │
│  ✅ ws_001  Historical Frequency Analysis    [3 findings]    │
│  ✅ ws_002  False Positive Classification   [14 findings]    │
│  🔄 ws_003  Multi-Timeframe Correlation      [5 findings]    │
│  ⏳ ws_004  Optimal Entry/Exit Parameters    [blocked]       │
│  ⏳ ws_005  Combined Scoring Model           [blocked]       │
│                                                              │
│  Latest Activity:                                            │
│  [14:32] ws_003 finding: H4+D1 alignment shows 67% WR       │
│  [14:28] orchestrator → ws_003: Check correlation with...   │
│  [14:01] ⚠ ESCALATION: Should we include crypto-specific... │
│          [Respond]                                           │
│                                                              │
│  [View Full Dashboard]  [View Synthesis]  [Export Report]   │
└──────────────────────────────────────────────────────────────┘
```

**Escalation notification path:**
1. `hivemind_escalation` SSE event received → `showToast()` with escalation summary
2. Persistent badge (red dot) added to project tile — not dismissed until user responds
3. Escalation entry added to project Activity log
4. "Respond" button inline in Hivemind tab activity feed
5. Unresolved escalations listed at top of Hivemind tab with prominent styling

### 6.3 Full Dashboard (standalone modal)

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Hivemind Dashboard: Engulfing Pattern Deep Analysis       [⏸] [⏹] [×]│
├───────────────┬─────────────────────────────────────────────────────────┤
│  Workstreams  │  ws_003: Multi-Timeframe Correlation            [⏸][↗] │
│               │  Status: active · Session 2 · 5 findings               │
│  ✅ Frequency │                                                         │
│  ✅ False Pos │  Findings:                                              │
│  🔄 Multi-TF  │  • H4 engulfing + D1 bullish trend = 67% WR (n=128)   │
│  ⏳ Entry/Exit│  • H1 engulfing alone = 51% WR (no edge)               │
│  ⏳ Scoring   │  • Volume confirmation adds +8% to filtered WR          │
│               │                                                         │
│  [+ Add]      │  Agent Output (live):                                   │
│               │  > Analyzing EUR/USD 4H data for comparison...         │
│               │  > Found 67 engulfing patterns in 2-year range         │
│               │                                                         │
│               │  [Send directive to this workstream...]                 │
├───────────────┴─────────────────────────────────────────────────────────┤
│  Message Bus                                            [Filter ▼]      │
│  14:32  ws_003 → orchestrator  Finding: H4+D1 alignment 67% WR         │
│  14:30  orchestrator → ws_003  Check if pattern holds for EUR/USD       │
│  ⚠ 14:01  orchestrator → user  ESCALATION: Include crypto-only data?   │
│           [Respond to escalation...]                                    │
└─────────────────────────────────────────────────────────────────────────┘
```

### 6.4 Synthesis Viewer

Accessible via "View Synthesis" button. Renders `knowledge/synthesis.md` in a modal with markdown rendering, last-updated timestamp, manual re-synthesis trigger, and export as standalone document.


---

## 7. Lifecycle & Flows

### 7.1 Create Hivemind

```
User clicks "New Hivemind" in project modal → enters goal description
  → POST /api/hivemind/create
  → Server creates directory structure + manifest.json
  → Server spawns orchestrator CLI session (claude -p, task: goal decomposition)
  → Orchestrator CLI session posts workstream definitions via bus API
  → Server creates workstream JSON files from bus messages
  → Frontend renders workstream list
  → Orchestrator CLI session exits
  → Server orchestrator evaluates dependency graph, identifies ready workstreams
  → Server orchestrator spawns workers (up to max_concurrent_workers, by priority)
  → Workers begin analysis, post findings to bus
  → Frontend updates in real-time via SSE (hivemind_* events)
```

### 7.2 Resume Hivemind

```
User clicks "Resume" on paused hivemind
  → POST /api/hivemind/{id}/start
  → Server orchestrator loads manifest, re-evaluates workstream statuses
  → Server orchestrator spawns orchestrator CLI session (task: review state, identify next actions)
  → Orchestrator CLI session reads synthesis.md + recent decisions, may update open_questions.jsonl
  → Orchestrator CLI session exits
  → Server orchestrator identifies ready workstreams (pending/paused with deps met)
  → Workers spawn with context injection:
      - {ws_id}_context.md (condensed prior knowledge)
      - Last 20 findings from {ws_id}_findings.jsonl
      - Relevant cross-workstream bus messages
      - Applicable decisions from decisions.jsonl
      - NOTE: project MEMORY.md is NOT included
  → Work continues from where it left off
```

### 7.3 Pause Hivemind

**Graceful pause (default):**
```
User clicks Pause (or POST /api/hivemind/{id}/pause)
  → Server sends follow-up directive to all active workers:
    "Please stop at a clean checkpoint. Post any in-progress findings to the bus,
     mark yourself complete or paused, then stop. Do not start new work."
  → Server waits up to 60 seconds for workers to acknowledge
  → After timeout or acknowledgment, sets all active workstream statuses to "paused"
  → Sets hivemind status to "paused"
```

**Hard stop (`?force=true`):**
```
  → Server calls Process Manager kill API for all registered worker PIDs
  → All active workstream statuses set to "paused" immediately
  → Findings posted before kill are preserved in JSONL
  → In-flight work since last finding post is lost
  → Hivemind status set to "paused"
```

### 7.4 Worker Error Recovery

```
Worker session ends with error (or no new findings for > stall_threshold minutes)
  → Server orchestrator detects via process liveness check
  → If workstream.retry_count < max_retries_per_workstream:
      → Increment retry_count
      → Re-spawn worker with same context injection (findings before failure are preserved)
      → Log retry event to activity feed
  → Else (retries exhausted):
      → Set workstream status to "failed"
      → Spawn orchestrator CLI session (task: assess failure, decide to re-scope or escalate)
      → Orchestrator CLI session either updates workstream scope and resets retry_count,
        or posts escalation to user
```

### 7.5 User Intervention

```
User types message in workstream directive input
  → POST /api/hivemind/{id}/intervene
  → Message posted to bus with type "directive"
  → If directed at specific workstream: delivered as follow-up to active worker session
  → If directed at orchestrator: spawns orchestrator CLI session (task: process directive)
  → Bus message persisted for future context
```

### 7.6 Escalation Flow

```
Worker or orchestrator CLI session calls POST /api/hivemind/{id}/escalate
  → Server persists escalation in bus/messages.jsonl
  → SSE pushes hivemind_escalation event to frontend
  → Frontend: showToast(), adds persistent red badge to project tile,
    adds entry to activity log, shows "Respond" button in Hivemind tab
  → User types response via Respond UI
  → Response posted to bus as "directive", delivered to originating workstream
  → Decision recorded in knowledge/decisions.jsonl
  → Badge cleared after user responds
```

### 7.7 Synthesis Cycle

```
Every N worker turns (configurable) OR on user request:
  → Server orchestrator spawns orchestrator CLI session (task: synthesize)
  → CLI session reads all workstream findings and current synthesis.md
  → CLI session produces updated synthesis via PUT /api/hivemind/{id}/knowledge/synthesis
  → SSE pushes hivemind_synthesis event
  → Frontend notifies user of synthesis update
  → CLI session exits
```

### 7.8 Context Condensation

```
When workstream context exceeds size threshold (during spawn context build):
  → Server detects context + findings exceed budget
  → Spawns condensation CLI session (claude -p, same pattern as memory condensation)
  → Condensation session reads full findings + context
  → Produces condensed {ws_id}_context.md
  → Older findings remain in JSONL (append-only) — source of truth preserved
  → Worker then spawned with condensed context
```


---

## 8. Implementation Phases

The phases are deliberately ordered so that real agent behavior validates the API design early, before the full API surface is built.

### Phase 1 — Minimal Foundation (unblocks Phase 2 immediately)
- Directory structure and data model
- Manifest CRUD
- Workstream CRUD (including `priority` and `model` fields)
- Findings post + last-N read
- Minimal SSE (hivemind_finding, hivemind_workstream, hivemind_worker_spawned, hivemind_worker_done)
- Basic server orchestrator (dependency resolver + worker scheduler background thread)

### Phase 2 — Agent Integration (starts before Phase 1 is complete)
- Orchestrator CLI session infrastructure (goal decomposition on create)
- Worker system prompt builder with context injection
- Worker spawn + lifecycle management
- Follow-up / directive routing to active workers
- Error recovery (retry logic, stall detection)
- Integration with Process Manager for all spawned processes

*Phase 1 remainder runs in parallel:* full message bus (poll + history), full knowledge base endpoints, complete SSE event set, escalation delivery

### Phase 3 — Frontend (Basic)
- Hivemind tab in project modal
- Workstream list with status indicators (pending/active/blocked/completed/failed)
- Activity feed (recent bus messages)
- Escalation alerts: toast + badge + Respond UI
- Create hivemind dialog (goal input)
- Resume / pause / stop controls

### Phase 4 — Frontend (Full Dashboard)
- Standalone hivemind dashboard modal
- Per-workstream detail view with findings list
- Live agent output per workstream
- Synthesis viewer
- Message bus with filtering
- Directive input per workstream
- Per-workstream model + priority editing

### Phase 5 — Intelligence & Polish
- Auto-synthesis cycle (every N turns)
- Adaptive re-planning on stall/escalation (orchestrator CLI session)
- Context condensation for long-running workstreams
- Cost tracking per hivemind (aggregate token usage across all worker + orchestrator sessions)
- Export synthesis as standalone report
- Hivemind templates (pre-built decomposition patterns for common analysis types)
- Optional one-way bridge: orchestrator writes top-level summary to project MEMORY.md at synthesis time

---

## 9. Integration with Existing MC Systems

| MC System | Integration |
|-----------|------------|
| **Agent sessions (Mode A/B)** | Workers are standard MC agent sessions; full reuse of spawn, SSE streaming, follow-up, stop infrastructure |
| **Orchestrator CLI sessions** | Same pattern as existing memory condensation housekeeping agent (`claude -p`, `housekeeping: True`, `--max-turns 5`) |
| **Process Manager** | All hivemind worker and orchestrator processes registered at spawn, visible in Process Manager modal |
| **SSE streaming** | Hivemind events use new `hivemind_*` event types flowing through existing SSE infrastructure |
| **Memory system** | Hivemind knowledge base is separate from project MEMORY.md. Workers do NOT write to MEMORY.md. Orchestrator may optionally write a synthesis summary to MEMORY.md (Phase 5). |
| **Scheduler** | Scheduled hivemind sessions — e.g. "run analysis nightly with new data" |
| **Terminal pop-out** | Workers can launch terminal windows as usual for data processing tasks |
| **Token tracking** | All worker + orchestrator sessions contribute to per-project and global token counters |
| **GitHub sync** | Hivemind findings could optionally sync as GitHub Issues/Discussions (Phase 5) |
| **Context budget system** | Same 20KB pre-dispatch warning and auto-condensation logic applied to worker context builds |

---

## 10. Open Questions (resolved and remaining)

**Resolved in v1.1:**
- ~~Should the orchestrator be a Claude agent or server logic?~~ → **Hybrid: server state machine for deterministic coordination + short-lived orchestrator CLI sessions for intelligence moments** (same pattern as memory condensation)
- ~~Should different workstreams use different models?~~ → **Yes** — `model` field on workstream JSON, overrides manifest `worker_model`
- ~~How to handle concurrent hivemind + limited worker slots?~~ → `priority` field on workstream, server orchestrator schedules by priority when `max_concurrent_workers` is reached

**Still open:**
1. **Concurrent hiveminds** — Should a project support multiple active hiveminds simultaneously?
2. **Cross-project hiveminds** — Should a hivemind span multiple MC projects?
3. **Human-in-the-loop granularity** — Should every finding require user approval, or only decisions/escalations? Configurable per-hivemind?
4. **Real-time inter-agent communication** — Should workers message each other directly, or route everything through the bus? Direct is faster but harder to audit.
5. **External data integration** — Workers ingest files/APIs and store processed results in the knowledge base?
6. **Versioning** — Should synthesis/findings support versioning to track how understanding evolved?


---

## 11. Example: Engulfing Pattern Analysis Hivemind

**Goal:** Deep analysis of all aspects of engulfing pattern data — detection methods, classification, statistical edge, multi-timeframe correlation, optimal trade parameters, and combined scoring model.

**Orchestrator CLI session decomposes into:**

| # | Workstream | Priority | Model | Dependencies | Description |
|---|-----------|----------|-------|-------------|-------------|
| 1 | Historical Frequency Analysis | 1 | Sonnet | — | Scan dataset, identify all engulfing patterns, establish baseline statistics |
| 2 | False Positive Classification | 1 | Sonnet | 1 | Categorize unreliable patterns, build filter taxonomy |
| 3 | Multi-Timeframe Correlation | 2 | Sonnet | 1 | Test pattern reliability across timeframe combinations |
| 4 | Volume & Momentum Confirmation | 2 | Haiku | 1, 2 | Analyze volume, RSI, and momentum at pattern formation |
| 5 | Session & Timing Analysis | 2 | Haiku | 1, 2 | Compare pattern reliability across trading sessions |
| 6 | Support/Resistance Proximity | 3 | Haiku | 1, 2 | Measure pattern reliability near key price levels |
| 7 | Optimal Entry/Exit Parameters | 3 | Sonnet | 2, 3, 4 | Determine best stop-loss, take-profit, and R:R ratios |
| 8 | Combined Scoring Model | 4 | Opus | ALL | Build composite score from all factors, backtest |

Note the model assignments: data-processing workstreams (4, 5, 6) use Haiku for cost efficiency; analysis workstreams use Sonnet; the final synthesis workstream uses Opus for the highest-quality combined output.

**After 3 sessions, `knowledge/synthesis.md` might contain:**

```markdown
# Engulfing Pattern Analysis — Synthesis
Last updated: 2026-03-25T09:30:00Z | Session 3 of ongoing

## Executive Summary
Engulfing patterns show statistically significant predictive power ONLY when filtered
by multiple confirmation factors. Raw signals have no edge (52% win rate). Filtered
signals show 67-72% win rate depending on filter combination.

## Key Findings (14 total across 3 workstreams)
### Pattern Frequency
- 847 engulfing patterns in 2-year BTC/USDT 4H dataset (1.6 per day average)

### False Positive Taxonomy
- Strongest filter: wick-to-body ratio < 0.4 reduces FP by 38%
- Volume below 0.7x average → 73% false positive rate
- Asian session patterns: 2.1x higher FP rate

### Multi-Timeframe Correlation (in progress)
- H4 + D1 trend alignment = 67% WR (n=128)
- H1 alone = 51% WR (no edge, n=342)
- Volume confirmation adds +8% to filtered WR

## Decisions Made
1. Using close-to-close measurement for pattern size (not wick-to-wick)
2. Minimum body engulfment ratio set at 60%
3. Excluding patterns with body < 0.3% of price (noise threshold)

## Open Questions
- Does the edge persist in forex pairs or is it crypto-specific?
- How does the pattern interact with market regime (trending vs ranging)?

## Next Steps
- Complete multi-timeframe correlation (ws_003)
- Begin volume/momentum confirmation (ws_004)
- Begin session timing analysis (ws_005)
```

---

## 12. Comparison with Alternatives

| Feature | Claude Agent Teams | ruflo / claude-squad | MC Hivemind |
|---------|-------------------|---------------------|-------------|
| Multi-agent coordination | Yes | Yes | Yes |
| Persistent knowledge | No | Limited | **Full persistence** |
| Resumable across restarts | No | No | **Yes** |
| Cumulative expertise | No | No | **Yes** |
| Visual dashboard | Terminal only | Terminal only | **Full web UI** |
| User intervention | Direct terminal | Limited | **Inline escalations + directives** |
| Cross-session synthesis | No | No | **Auto-synthesis (CLI-native)** |
| Context condensation | No | No | **Yes (leverages MC memory system)** |
| Cost tracking | Per-session | No | **Per-hivemind aggregate** |
| Dependency management | Task-level | No | **Workstream-level with blocking** |
| Per-workstream model | No | No | **Yes (Haiku/Sonnet/Opus per workstream)** |
| Error recovery | None | None | **Retry policy + stall detection** |
| All Claude via CLI | Yes | Yes | **Yes — no direct API calls** |
