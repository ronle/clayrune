# Hivemind — Persistent Multi-Agent Collaborative Intelligence

## Feature Specification v1.0
**Project:** Mission Control
**Author:** Ron + Claude
**Date:** 2026-03-23
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

---

## 2. Concepts & Terminology

| Term | Definition |
|------|-----------|
| **Hivemind** | A persistent collaborative effort with a shared goal, containing multiple workstreams |
| **Workstream** | A focused area of investigation/work within the hivemind, owned by one agent at a time |
| **Orchestrator** | A special agent that decomposes goals, coordinates workstreams, synthesizes findings, and escalates to the user |
| **Worker** | An agent assigned to a specific workstream — disposable, replaceable, but inherits all prior knowledge |
| **Knowledge Base** | The persistent store of findings, decisions, messages, and synthesis — the hivemind's long-term memory |
| **Message Bus** | The communication channel between agents — all messages are persisted |
| **Synthesis** | A periodically-updated human-readable summary of everything the hivemind has learned |
| **Escalation** | When an agent surfaces a decision or blocker to the user for input |

---

## 3. Data Model

### 3.1 Directory Structure

```
data/hiveminds/{hivemind_id}/
  manifest.json                    # Core metadata and configuration
  workstreams/
    {ws_id}.json                   # Workstream definition and status
    {ws_id}_findings.jsonl         # Append-only findings log
    {ws_id}_context.md             # Accumulated context injected on resume
  knowledge/
    synthesis.md                   # Running synthesis (auto-updated by orchestrator)
    decisions.jsonl                # Decisions made + rationale
    open_questions.jsonl           # Unresolved questions for future sessions
  bus/
    messages.jsonl                 # Complete inter-agent message history
  sessions/
    {session_timestamp}.json       # Per-session snapshot (who ran, what changed)
```

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
    "model": "opus",
    "worker_model": "sonnet"
  }
}
```

### 3.3 Workstream (`workstreams/{ws_id}.json`)

```json
{
  "id": "ws_002",
  "title": "False Positive Classification",
  "description": "Identify and categorize false positive engulfing patterns. Build a taxonomy of unreliable signals and determine filtering criteria.",
  "status": "completed",
  "dependencies": ["ws_001"],
  "created_at": "2026-03-23T14:05:00Z",
  "completed_at": "2026-03-24T16:20:00Z",
  "findings_count": 14,
  "sessions_used": 3,
  "current_agent_session_id": null,
  "last_agent_session_id": "abc-123-def"
}
```

Status values: `pending` | `active` | `blocked` | `completed` | `paused` | `failed`

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
  "rationale": "Wick-to-wick includes noise from liquidity grabs and produces inconsistent measurements across timeframes. Close-to-close aligns with actual body engulfment which is the core signal.",
  "decided_by": "orchestrator",
  "user_approved": true,
  "impacts": ["ws_003", "ws_005"]
}
```

---

## 4. Agent Architecture

### 4.1 Orchestrator Agent

The orchestrator is a Claude agent with a specialized system prompt. It is responsible for:

1. **Decomposition** — Breaking the goal into workstreams with dependencies
2. **Coordination** — Managing workstream lifecycle, spawning workers
3. **Synthesis** — Periodically updating `knowledge/synthesis.md` with cross-workstream insights
4. **Routing** — Forwarding relevant findings between workstreams
5. **Escalation** — Surfacing decisions, blockers, and key findings to the user
6. **Adaptation** — Adding/modifying workstreams based on emergent findings

The orchestrator communicates with MC via the message bus API. It does NOT directly spawn worker agents — it requests them from the server, which manages process lifecycle.

**Orchestrator system prompt structure:**
```
You are the Orchestrator of a Hivemind analysis.

GOAL: {manifest.goal}

CURRENT STATE:
{workstream statuses, loaded from workstream JSONs}

KNOWLEDGE BASE SUMMARY:
{loaded from knowledge/synthesis.md}

RECENT DECISIONS:
{loaded from knowledge/decisions.jsonl, last N}

OPEN QUESTIONS:
{loaded from knowledge/open_questions.jsonl}

YOUR CAPABILITIES:
- Post findings/decisions/messages: curl POST /api/hivemind/{id}/bus/post
- Request new workstream: curl POST /api/hivemind/{id}/workstreams/create
- Update workstream status: curl POST /api/hivemind/{id}/workstreams/{ws_id}/status
- Request worker spawn: curl POST /api/hivemind/{id}/workstreams/{ws_id}/spawn
- Escalate to user: curl POST /api/hivemind/{id}/escalate
- Update synthesis: curl PUT /api/hivemind/{id}/knowledge/synthesis

YOUR RESPONSIBILITIES:
1. Review any new findings from workers since last session
2. Update synthesis if new information warrants it
3. Identify which workstreams to advance next
4. Request worker spawns for active workstreams
5. Route relevant findings to dependent workstreams
6. Escalate blockers or important decisions to the user
```

### 4.2 Worker Agents

Each worker is a standard Claude agent session (using MC's existing Mode A or Mode B infrastructure). Workers receive a workstream-specific system prompt:

```
You are a specialist agent in a Hivemind analysis.

YOUR WORKSTREAM: {ws.title}
YOUR BRIEF: {ws.description}

ACCUMULATED KNOWLEDGE (from previous sessions on this workstream):
{loaded from workstreams/{ws_id}_context.md}

KEY FINDINGS SO FAR:
{loaded from workstreams/{ws_id}_findings.jsonl}

RELEVANT FINDINGS FROM OTHER WORKSTREAMS:
{filtered from bus/messages.jsonl — only findings tagged as relevant to this ws}

DECISIONS THAT AFFECT YOUR WORK:
{filtered from knowledge/decisions.jsonl by ws.impacts}

YOUR CAPABILITIES:
- Report a finding: curl POST /api/hivemind/{id}/bus/post -d '{"from":"{ws_id}","type":"finding_report",...}'
- Ask a question: curl POST /api/hivemind/{id}/bus/post -d '{"from":"{ws_id}","type":"question","to":"ws_xxx",...}'
- Report blocker: curl POST /api/hivemind/{id}/bus/post -d '{"from":"{ws_id}","type":"escalation",...}'
- Mark complete: curl POST /api/hivemind/{id}/workstreams/{ws_id}/status -d '{"status":"completed"}'

RULES:
1. Build on accumulated knowledge — do NOT repeat analysis already done
2. Report findings as you discover them (don't batch)
3. Reference evidence and data for all findings
4. If you need information from another workstream, ask via the bus
5. If you hit a decision that affects other workstreams, escalate
```

### 4.3 Context Injection on Resume

When a worker is spawned for a workstream that has prior history, the server builds the context injection by:

1. Loading `{ws_id}_context.md` (human-curated or orchestrator-maintained summary)
2. Loading last N findings from `{ws_id}_findings.jsonl`
3. Loading relevant cross-workstream findings from `bus/messages.jsonl`
4. Loading applicable decisions from `knowledge/decisions.jsonl`
5. Assembling into the system prompt (with size budgeting to stay within limits)

If accumulated context exceeds a threshold, the orchestrator is asked to **condense** the context file — summarizing older findings while preserving key insights (similar to MC's existing two-tier memory condensation).

---

## 5. Server API

### 5.1 Hivemind Management

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/hivemind/create` | Create a new hivemind from goal + project |
| `GET` | `/api/hivemind/{id}` | Get full hivemind state |
| `GET` | `/api/hivemind/list` | List all hiveminds (optionally by project) |
| `PUT` | `/api/hivemind/{id}` | Update hivemind config |
| `POST` | `/api/hivemind/{id}/start` | Start/resume the hivemind (spawn orchestrator) |
| `POST` | `/api/hivemind/{id}/pause` | Pause all agents, preserve state |
| `POST` | `/api/hivemind/{id}/stop` | Stop all agents, mark inactive |
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
| `PUT` | `/api/hivemind/{id}/knowledge/synthesis` | Update synthesis (orchestrator) |
| `GET` | `/api/hivemind/{id}/knowledge/decisions` | All decisions |
| `GET` | `/api/hivemind/{id}/knowledge/findings` | All findings across workstreams |
| `POST` | `/api/hivemind/{id}/escalate` | Escalate to user |

### 5.5 User Intervention

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/hivemind/{id}/intervene` | User sends directive to orchestrator or specific workstream |
| `POST` | `/api/hivemind/{id}/findings/{f_id}/review` | User approves/rejects a finding |
| `POST` | `/api/hivemind/{id}/decisions/{d_id}/approve` | User approves/rejects a decision |

---

## 6. Frontend Design

### 6.1 Entry Points

- **Project tile badge** — Shows active hivemind indicator (e.g., honeycomb icon with agent count)
- **Project modal** — New "Hivemind" tab alongside Backlog, Agent, Agent Log, Activity
- **Standalone hivemind modal** — Full-width view for detailed monitoring (similar to plan viewer)

### 6.2 Hivemind Tab (in project modal)

```
┌──────────────────────────────────────────────────────────────┐
│  Hivemind: Engulfing Pattern Deep Analysis          [⏸] [⏹] │
│  Session 5 · 14 findings · 3 decisions · 2hr 34min total    │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  Goal: Comprehensive analysis of engulfing pattern data...   │
│                                                              │
│  Workstreams:                                                │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ ✅ ws_001  Historical Frequency Analysis    [3 findings]│  │
│  │ ✅ ws_002  False Positive Classification   [14 findings]│  │
│  │ 🔄 ws_003  Multi-Timeframe Correlation      [5 findings]│  │
│  │ ⏳ ws_004  Optimal Entry/Exit Parameters    [blocked]   │  │
│  │ ⏳ ws_005  Combined Scoring Model           [blocked]   │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  Latest Activity:                                            │
│  [14:32] ws_003 finding: H4+D1 alignment shows 67% WR       │
│  [14:28] orchestrator → ws_003: Check correlation with...    │
│  [14:15] ws_003 finding: Sample size sufficient (n=128)      │
│  [14:01] ⚠ ESCALATION: Should we include crypto-specific... │
│          [Respond]                                           │
│                                                              │
│  [View Full Dashboard]  [View Synthesis]  [Export Report]    │
└──────────────────────────────────────────────────────────────┘
```

### 6.3 Full Dashboard (standalone modal)

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Hivemind Dashboard: Engulfing Pattern Deep Analysis       [⏸] [⏹] [×]│
├───────────────┬─────────────────────────────────────────────────────────┤
│               │                                                         │
│  Workstreams  │  ws_003: Multi-Timeframe Correlation            [⏸][↗] │
│               │  ──────────────────────────────────────────────────────  │
│  ✅ Frequency │  Status: active · Session 2 · 5 findings               │
│  ✅ False Pos │                                                         │
│  🔄 Multi-TF  │  Findings:                                              │
│  ⏳ Entry/Exit│  • H4 engulfing + D1 bullish trend = 67% WR (n=128)    │
│  ⏳ Scoring   │  • H1 engulfing alone = 51% WR (no edge)               │
│               │  • Volume confirmation adds +8% to filtered WR          │
│  ──────────── │  • Asian session: insufficient sample (n=23)            │
│  [+ Add]      │  • Crypto pairs show higher correlation than forex      │
│               │                                                         │
│               │  Agent Output (live):                                    │
│               │  ┌─────────────────────────────────────────────────────┐ │
│               │  │ > Analyzing EUR/USD 4H data for comparison...      │ │
│               │  │ > Found 67 engulfing patterns in 2-year range      │ │
│               │  │ > Cross-referencing with D1 trend direction...     │ │
│               │  └─────────────────────────────────────────────────────┘ │
│               │                                                         │
│               │  [Send directive to this workstream...]                 │
├───────────────┴─────────────────────────────────────────────────────────┤
│  Message Bus                                            [Filter ▼]      │
│  ─────────────────────────────────────────────────────────────────────── │
│  14:32  ws_003 → orchestrator  Finding: H4+D1 alignment 67% WR         │
│  14:30  orchestrator → ws_003  Check if pattern holds for EUR/USD       │
│  14:28  ws_002 → ws_003        FYI: exclude wick-ratio < 0.4 patterns  │
│  14:15  ⚠ orchestrator → user  ESCALATION: Include crypto-only data?   │
│         [Respond to escalation...]                                      │
└─────────────────────────────────────────────────────────────────────────┘
```

### 6.4 Synthesis Viewer

Accessible via "View Synthesis" button. Renders `knowledge/synthesis.md` in a modal with:
- Markdown rendering
- Last-updated timestamp
- Option to manually trigger re-synthesis
- Export as standalone document

---

## 7. Lifecycle & Flows

### 7.1 Create Hivemind

```
User clicks "New Hivemind" in project modal
  → Enters goal description
  → POST /api/hivemind/create
  → Server creates directory structure + manifest
  → Server spawns orchestrator agent
  → Orchestrator decomposes goal into workstreams
  → Orchestrator posts workstream definitions via bus
  → Server creates workstream JSON files
  → Frontend renders workstream list
  → Orchestrator requests first batch of worker spawns
  → Server spawns workers (up to max_concurrent)
  → Workers begin analysis, post findings to bus
  → Frontend updates in real-time via SSE
```

### 7.2 Resume Hivemind

```
User clicks "Resume" on paused hivemind
  → POST /api/hivemind/{id}/start
  → Server loads manifest, checks last state
  → Server spawns orchestrator with full context injection:
      - manifest.goal
      - All workstream statuses
      - knowledge/synthesis.md
      - Recent decisions and open questions
  → Orchestrator reviews state, determines next actions
  → Orchestrator requests worker spawns for active workstreams
  → Workers spawn with workstream-specific context injection:
      - Their findings history
      - Relevant cross-workstream findings
      - Applicable decisions
  → Work continues from where it left off
```

### 7.3 User Intervention

```
User types message in workstream directive input
  → POST /api/hivemind/{id}/intervene
  → Message posted to bus with type "directive"
  → If directed at specific workstream: delivered to worker as follow-up
  → If directed at orchestrator: orchestrator processes and may redirect workers
  → Bus message persisted for future context
```

### 7.4 Escalation Flow

```
Worker encounters decision point
  → Posts escalation to bus
  → Server creates notification
  → SSE pushes notification to frontend
  → Frontend shows alert with "Respond" button
  → User types response
  → Response posted to bus, delivered to requesting agent
  → Decision recorded in knowledge/decisions.jsonl
```

### 7.5 Synthesis Cycle

```
Every N turns (configurable) OR on orchestrator request:
  → Orchestrator reads all workstream findings
  → Orchestrator reads current synthesis.md
  → Orchestrator produces updated synthesis
  → PUT /api/hivemind/{id}/knowledge/synthesis
  → Server writes synthesis.md
  → Frontend notified of update via SSE
```

### 7.6 Context Condensation

```
When workstream context exceeds size threshold:
  → Server detects during context injection build
  → Server spawns condensation agent (similar to existing MC memory condense)
  → Condensation agent reads full findings + context
  → Produces condensed {ws_id}_context.md
  → Older findings remain in JSONL (append-only) but active context is trimmed
  → Next worker spawn uses condensed context
```

---

## 8. Implementation Phases

### Phase 1: Foundation (Data + API)
- Directory structure and data model
- Manifest CRUD
- Workstream CRUD
- Message bus (post + poll + history)
- Knowledge base endpoints (synthesis, decisions, findings)
- SSE stream for hivemind events

### Phase 2: Agent Integration
- Orchestrator system prompt builder
- Worker system prompt builder with context injection
- Orchestrator spawn + lifecycle management
- Worker spawn + lifecycle management
- Follow-up/directive routing to agents
- Integration with existing MC agent infrastructure (Mode A/B)

### Phase 3: Frontend — Basic
- Hivemind tab in project modal
- Workstream list with status indicators
- Activity feed (message bus viewer)
- Escalation alerts with response UI
- Create hivemind dialog
- Resume/pause/stop controls

### Phase 4: Frontend — Full Dashboard
- Standalone hivemind dashboard modal
- Workstream detail view with findings
- Live agent output per workstream
- Synthesis viewer
- Message bus with filtering
- Directive input per workstream

### Phase 5: Intelligence & Polish
- Context condensation for long-running hiveminds
- Auto-synthesis cycle
- Dependency graph visualization
- Cost tracking per hivemind (aggregate token usage)
- Export synthesis as standalone report
- Hivemind templates (pre-built decomposition patterns)
- Fork/branch a hivemind for alternative hypotheses

---

## 9. Integration with Existing MC Systems

| MC System | Integration |
|-----------|------------|
| **Agent sessions** | Workers are standard MC agent sessions; reuse Mode A/B infrastructure |
| **SSE streaming** | Hivemind events flow through existing SSE; new event types added |
| **Memory system** | Hivemind knowledge base is separate from project memory; orchestrator can write key findings to project memory too |
| **Terminal pop-out** | Workers can launch terminals as usual for data processing |
| **GitHub sync** | Hivemind findings could optionally sync as GitHub issues/discussions |
| **Scheduler** | Scheduled hivemind sessions (e.g., "run analysis nightly with new data") |
| **Process manager** | All hivemind agent processes registered and visible |

---

## 10. Open Questions

1. **Orchestrator as agent vs. server logic?** Current spec uses a real Claude agent as orchestrator. Alternative: server-side orchestration with rule-based coordination. Agent approach is more flexible but costs more tokens.

2. **Worker model selection** — Should different workstreams be able to use different models? (e.g., Opus for complex analysis, Sonnet for data processing)

3. **Concurrent hiveminds** — Should a project support multiple active hiveminds? (e.g., one for pattern analysis, one for portfolio optimization)

4. **Cross-project hiveminds** — Should a hivemind be able to span multiple MC projects?

5. **Human-in-the-loop granularity** — Should every finding require user approval, or only decisions/escalations? Configurable per-hivemind?

6. **Real-time inter-agent communication** — Should workers be able to message each other directly, or should everything route through the orchestrator? Direct messaging is faster but harder to track.

7. **External data integration** — Should workers be able to ingest external data (files, APIs) and store processed results in the knowledge base?

8. **Versioning** — Should synthesis/findings support versioning so you can see how understanding evolved over time?

---

## 11. Example: Engulfing Pattern Analysis Hivemind

**Goal:** Deep analysis of all aspects of engulfing pattern data — detection methods, classification, statistical edge, multi-timeframe correlation, optimal trade parameters, and combined scoring model.

**Orchestrator decomposes into:**

| # | Workstream | Dependencies | Description |
|---|-----------|-------------|-------------|
| 1 | Historical Frequency Analysis | — | Scan dataset, identify all engulfing patterns, establish baseline statistics |
| 2 | False Positive Classification | 1 | Categorize unreliable patterns, build filter taxonomy |
| 3 | Multi-Timeframe Correlation | 1 | Test pattern reliability across timeframe combinations |
| 4 | Volume & Momentum Confirmation | 1, 2 | Analyze volume, RSI, and momentum at pattern formation |
| 5 | Session & Timing Analysis | 1, 2 | Compare pattern reliability across trading sessions |
| 6 | Support/Resistance Proximity | 1, 2 | Measure pattern reliability near key price levels |
| 7 | Optimal Entry/Exit Parameters | 2, 3, 4 | Determine best stop-loss, take-profit, and R:R ratios |
| 8 | Combined Scoring Model | ALL | Build composite score from all factors, backtest |

**After 3 sessions, synthesis.md might contain:**

```markdown
# Engulfing Pattern Analysis — Synthesis
Last updated: 2026-03-25T09:30:00Z | Session 3 of ongoing

## Executive Summary
Engulfing patterns show statistically significant predictive power ONLY
when filtered by multiple confirmation factors. Raw engulfing signals have
no edge (52% win rate). Filtered signals show 67-72% win rate depending
on filter combination.

## Key Findings (14 total across 3 workstreams)
### Pattern Frequency
- 847 engulfing patterns identified in 2-year BTC/USDT 4H dataset
- 412 bullish, 435 bearish (roughly balanced)
- Average: 1.6 patterns per day

### False Positive Taxonomy (14 categories identified)
- Strongest filter: wick-to-body ratio threshold of 0.4 (reduces FP by 38%)
- Volume below 0.7x average correlates with 73% false positive rate
- Asian session patterns have 2.1x higher FP rate

### Multi-Timeframe Correlation (in progress)
- H4 engulfing + D1 trend alignment = 67% win rate (n=128)
- H1 engulfing alone = 51% win rate (no edge, n=342)
- Preliminary: volume confirmation adds +8% to filtered WR

## Decisions Made
1. Using close-to-close measurement for pattern size (not wick-to-wick)
2. Minimum body engulfment ratio set at 60%
3. Excluding patterns with body < 0.3% of price (noise threshold)

## Open Questions
- Does the edge persist in forex pairs or is it crypto-specific?
- Should we incorporate order flow data where available?
- How does the pattern interact with market regime (trending vs ranging)?

## Next Steps
- Complete multi-timeframe correlation analysis (ws_003)
- Begin volume/momentum confirmation study (ws_004)
- Start session timing analysis (ws_005)
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
| Cross-session synthesis | No | No | **Auto-synthesis** |
| Context condensation | No | No | **Yes (leverages MC memory system)** |
| Cost tracking | Per-session | No | **Per-hivemind aggregate** |
| Dependency management | Task-level | No | **Workstream-level with blocking** |
