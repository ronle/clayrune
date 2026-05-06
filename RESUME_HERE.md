# Clayrune — Resume Here

**Last updated:** 2026-05-06 (PM)
**Branch:** `master`
**Latest in-flight work:** Hivemind global surface (shipped), trigger-aware run history (shipped), `sizeAgentChat` measurement-loop fix (shipped). See CHANGELOG `[2026-05-06]`.

> Pick this up after a system restart. Skim section 1 for state-of-the-world,
> then jump to section 4 for the next-step recommendation.

---

## 1. Where we are

- **Hivemind elevated to a first-class surface (commit forthcoming).** Sidebar gets a 🐝 Hivemind entry that opens a cross-project list (`__all_hivemind`) with status / project / search filters, status pills, short ID hashes, planner/worker tree mini-viz per card, pause/stop/resume controls. Mobile bottom-tab bar swapped Settings → Hivemind (Settings via avatar). Per-project Hivemind tab REMOVED — replaced by 🐝 Hiveminds + ✨ Start Hivemind in the project's 3-dot menu. Start Hivemind auto-dispatches the setup prompt instead of leaving the user staring at a populated form. Stale heuristic: `active`/`paused` + no activity > 24h = rendered as "stale" with grey badge + Restart control; server-side `_hm_reconcile_stale_on_startup` rewrites the manifest at boot so the disk reflects reality.
- **Trigger-aware run history (commit forthcoming).** Every `agent_log` entry now carries `trigger_type` (`manual` / `schedule` / `hivemind_orchestrator` / `hivemind_worker`) and `trigger_id`. Three new endpoints: `GET /api/schedule/<id>/runs`, `GET /api/hivemind/<id>/runs?role=&ws_id=`, `GET /api/project/<pid>/transcript/<csid>` (read-only parsed transcript). UI: Runs button on every schedule card (inline expanding panel), Runs button on each Hivemind workstream + Orchestrator Runs in overview. Each row click opens a shared transcript viewer modal. Plus a **▶ Run Now** button on the far right of every schedule card (and in the edit form) that fires the task immediately, stamps trigger metadata, and updates `last_run` without touching `next_run`.
- **`sizeAgentChat` measurement-loop fix (commit forthcoming).** Fixed Send-button bottom-border clipping caused by `chatInputEl.offsetHeight` returning the squashed value from the previous over-allocation, feeding back into a smaller `desiredOutH` each refresh. Now resets output's explicit sizing before measuring AND computes `inputH = max(offsetHeight, scrollHeight, rowH + paddingV, 80)` — three independent signals plus an 80px safety floor.
- **Remote server restart shipped (commit `5ce48eb`).** Settings → Server → Restart server lets the user restart the Python process from anywhere, including mobile via the `clayrune.io` tunnel. Active-flow warning before confirmation, server-side recheck, audit trail in `data/restart_log.json` (gitignored), heartbeat-based cross-dashboard detection so observers don't get stuck on stale "Blocked" state. Major Windows-specific gotcha worked around: `os.execv` inherits open FDs from child agent processes — switched to `subprocess.Popen(close_fds=True)`. POSIX adapter gaps (netstat equivalent + log redirection) flagged as TODOs in `_check_port_conflict` and `_perform_server_restart_async`.
- **Modal persistence** (commit `5ce48eb`). Open conversation modals + their canvas positions survive page refresh (`mc_open_modals` snapshot). Per-project window size and zoom level survive app/system reboot (`mc_modal_prefs`). Both flushed before any in-app restart so the snapshot bridges the reload.
- **Conversation input drag** (commit `5ce48eb`). Dragging the agent chat separator now resizes the output area in lock-step with the textarea — the deferred-flex-layout snap that used to fire seconds later is gone. `sizeAgentChat` drives `agent-output` height explicitly with `!important` and is called live during the drag.
- **Scheduler / API-discovery system-prompt awareness** (commit `5ce48eb`). Every agent now sees Clayrune's local `/api/schedules` in its preamble (vs. the Anthropic `/schedule` skill, which is short-interval/in-session only) and a hint to grep `server.py` for `@app.route` instead of guessing endpoint names.
- Diagram-rendering polish wave (Mermaid → Excalidraw bridge) **complete**: clean strokes, Helvetica labels, orphan "Syntax error" SVG sweeper. Mobile rendering is a known caveat — desktop-first.

- **Remote server restart shipped (commit `5ce48eb`).** Settings → Server → Restart server lets the user restart the Python process from anywhere, including mobile via the `clayrune.io` tunnel. Active-flow warning before confirmation, server-side recheck, audit trail in `data/restart_log.json` (gitignored), heartbeat-based cross-dashboard detection so observers don't get stuck on stale "Blocked" state. Major Windows-specific gotcha worked around: `os.execv` inherits open FDs from child agent processes — switched to `subprocess.Popen(close_fds=True)`. POSIX adapter gaps (netstat equivalent + log redirection) flagged as TODOs in `_check_port_conflict` and `_perform_server_restart_async`.
- **Modal persistence** (same commit). Open conversation modals + their canvas positions survive page refresh (`mc_open_modals` snapshot). Per-project window size and zoom level survive app/system reboot (`mc_modal_prefs`). Both flushed before any in-app restart so the snapshot bridges the reload.
- **Conversation input drag** (same commit). Dragging the agent chat separator now resizes the output area in lock-step with the textarea — the deferred-flex-layout snap that used to fire seconds later is gone. `sizeAgentChat` drives `agent-output` height explicitly with `!important` and is called live during the drag.
- **Scheduler / API-discovery system-prompt awareness** (same commit). Every agent now sees Clayrune's local `/api/schedules` in its preamble (vs. the Anthropic `/schedule` skill, which is short-interval/in-session only) and a hint to grep `server.py` for `@app.route` instead of guessing endpoint names.
- Diagram-rendering polish wave (Mermaid → Excalidraw bridge) **complete**: clean strokes, Helvetica labels, orphan "Syntax error" SVG sweeper. Mobile rendering is a known caveat — desktop-first.
- Mobile UI bottom tab bar reshuffled: **Home | Backlog | + FAB | Scheduler | Settings** (Activity dropped — Processes view isn't usable on a phone; Scheduler was previously unreachable).
- **Backlog cleanup pass complete.** Mission Control project: 105 open → 11 real open + 9 wontdo. **89 items closed this session** across four batches:
  - Batch 1 (Group A+B): 16 — recently shipped diagram + rebrand items
  - Batch 2 (Group C): 15 — user-retest / smoke-test breadcrumbs, all confirmed
  - Batch 3: 22 + 23 — stale `agent:todowrite` entries from the rebrand/CI/remote-access push
  - Batch 4: 11 (2 done + 9 wontdo with reasons)
- All 33 `agent_status: in_progress` items are closed; no active in-progress work tracked in the backlog.

### Real open items (11) — the actual roadmap

| ID | Source | Title |
|---|---|---|
| ce8e1927, 3bc90f3a | agent:todowrite | Phase 2: animated Claydo logos (dups) |
| 2483e34b | agent:todowrite | Cleanup: remove `MC_REMOTE_DEV_EMAIL` as required env var |
| e287ae52 | design-plan | Onboarding rewrite: `startWalkthrough()` copy |
| ce1ecf38 | design-plan | Density tokens refactor: replace `body.compact` with CSS vars |
| 75718665 | design-plan | 3rd view mode: grouped-by-status list |
| b049c18f | design-plan | Progress bar on tiles (`backlog.done / backlog.total`) |
| 580ff7a1 | dashboard | Modal agents communicate cross-modal (Hivemind cross-project) |
| 26c6a449 | dashboard | Drag modal to screen edge → snap layout |
| 124dbb47 | dashboard | Syntax-highlight code in chat |
| feb3f16f | dashboard | Resize tiles from any border, not just corner |

---

## 2. Feature inventory

### Core orchestration
- Multi-project dashboard: grid + list views, status pills, modal colors, domain tags, friendly-status mapping
- Multi-modal windows: many project modals open simultaneously, drag/resize, z-order, minimized tray
- Multiple agent sessions per project (tab strip)
- Mode A (`claude -p` per turn) and Mode B (persistent `--input-format stream-json`)
- Per-project session isolation: `ProjectAgentManager` with own lock + guardian thread
- Session Guardian: hung-process detection (stdout silence + CPU-idle), auto-recovery, circuit breaker
- Session revival: from `agent_log` + Claude JSONL transcripts; failed-resume auto-fallback to fresh dispatch
- 24-hour stale-session purge with auto-resume on follow-up

### Agent UX
- Live SSE streaming with `turn_start` / `turn_complete` / terminal `status`; idempotent Stop button
- Plan Approval: `ExitPlanMode` collapses into Approve/Collapse pair — nothing auto-runs
- Agent Log tab + "Continue" on any past session
- Inline Mermaid diagrams (Excalidraw bridge, classic-Mermaid fallback) with fullscreen zoom viewer
- Image upload via paste/drop
- Terminal pop-out (xterm.js + TTY shim) for visual long-running commands
- Agent Console (bottom tray) listing all sessions across projects
- Token counter (global) and per-session

### Knowledge / state
- Two-tier memory (CLAUDE.md + MEMORY.md per project) with auto-condense via housekeeping agent
- MEMORY_ARCHIVE.md overflow
- Per-project Memory & Rules editor + cross-project Shared Rules
- Backlog as first-class: per-item agent linkage, priorities, status (open/done/wontdo), source tagging
- Cross-project Backlog view (`openAllBacklog`)
- Activity Log per project; Activity Feed cross-project sidebar

### Automation
- Local Scheduler (per-project): `/api/schedules` daily/cron/interval/once recurring agent dispatches
- Hivemind: cross-agent communication within a project (planner/worker pattern)
- Walkthrough/onboarding flow

### Remote / ops
- Remote access via clayrune.io: Cloudflare Tunnel + Access OTP, named devices, auto-cleanup
- **Remote server restart** from any dashboard (mobile included): warning modal lists active sessions/hiveminds, server-side recheck closes GET→POST race, audit log, 30s rate limit, cross-dashboard detection via `/api/system/heartbeat` so observers reload too instead of getting stuck on stale "error" state
- Operator dashboard at `/v1/admin` (Firebase email allowlist)
- Cloud Monitoring dashboard for control plane
- Mobile UI: bottom tab bar, greeting bar, filter pills, modal-tabs-in-3-dot-menu
- Tauri desktop wrapper

### Misc
- Command palette (Ctrl+K), density toggle, advanced-feature flags
- Auto workspace folder per new project
- Sidebar quick-jump to active projects
- **Sticky modal layout** — open conversation modals + their canvas positions survive page refresh (`mc_open_modals` snapshot); per-project window size and zoom level survive app/system reboot (`mc_modal_prefs`)

---

## 3. Website / README differentiator items

### Hero (max 3 — the elevator pitch)

1. **"Mission control for many Claude agents at once"**
   Multi-project dashboard, multi-modal windows, run 5–20 long-lived agents in parallel without losing track. **No direct competitor** at this positioning.
2. **Sessions that actually survive**
   Auto-revival from `agent_log` + Claude's own JSONL transcripts. Crash MC, reboot the laptop, lose the tab — your conversations come back. 24-hour stale-session window + "Continue" buttons on every past run.
3. **Plan Approval gate**
   `ExitPlanMode` collapses into explicit Approve / Collapse. Nothing dangerous runs without you. **Counter-positioning vs. autonomous agents** like Devin.

### Second tier — "and also..."

4. Inline Mermaid diagrams via Excalidraw bridge — agents draw architecture *while* explaining it. Visually distinctive in screenshots/demo videos.
5. Mobile remote access via clayrune.io — Cloudflare tunnel + named devices. Manage agents from your phone, including **restarting the server itself after deploying a fix** without going back to the desktop.
6. Scheduler + Hivemind — recurring runs, cross-agent coordination.
7. Two-tier memory with auto-condense — curated automatically, archived when oversized.
8. Backlog as first-class — items linked to agent sessions, priorities, status, cross-project view.
9. Terminal pop-out — agents run visual commands you can watch.

### Don't lead with (mention but bury)

- Mode A / Mode B distinction — internal architecture, confusing
- Session Guardian / race-condition consolidation — invisible reliability work
- Operator dashboard / Cloud Monitoring — only relevant for hosted users
- Tauri wrapper — packaging detail
- Command palette, density toggle, advanced flags — table stakes

### Suggested README hierarchy

```
Clayrune — operator console for long-running Claude agents
├─ Why (one paragraph: the gap between Claude CLI and Devin)
├─ Screenshots (multi-modal dashboard, Mermaid diagram, mobile)
├─ Features
│   ├─ Run many agents in parallel
│   ├─ Sessions survive everything
│   ├─ Plan approval
│   ├─ Mobile + remote access
│   ├─ Memory that curates itself
│   └─ Backlog + scheduler + hivemind
├─ Install
├─ Architecture (one diagram — multi-modal + per-project manager)
└─ Roadmap
```

---

## 4. Next-step recommendations

### Top 3 to actively invest in

1. **Hivemind** — *highest leverage feature, weakest current state.*
   The cross-agent comms idea is unique (no competitor has this), but it's tucked into a modal tab and the planner/worker pattern isn't surfaced. If "many agents working together" is the differentiator, this is what you double down on.
   - **Concrete next steps:**
     - Dedicated Hivemind dashboard view (not a tab)
     - Agent-to-agent message inspector
     - "Spawn worker" action from a planner agent
     - Visualization of the planner/worker tree
     - Persistent transcript of cross-agent messages

2. **Mobile UI maturity** — *the remote-access story falls apart if mobile is rough.*
   The Excalidraw bridge breaks on mobile (known caveat in CHANGELOG `[2026-05-04]`). The Scheduler tab swap was discovery work. Mobile UI sits at "good enough to test", not "daily driver."
   - **Concrete next steps:**
     - Mermaid/Excalidraw mobile rendering audit
     - Modal interaction polish on small screens
     - Gesture hints (swipe between sessions?)
     - Performance on older Android devices
   - **Owned story:** *"Clayrune is the first Claude tool you can actually run from a phone."*

3. **Plan Approval flow** — *already differentiating, ripe for polish.*
   Currently a single Approve/Collapse pair. Room to make this a defining safety story.
   - **Concrete next steps:**
     - Per-step approval (approve only steps 1–3)
     - Plan diff between runs
     - Plan templates / saved approvals
     - Step-execution preview before approve
   - **Marketing tagline:** *"the AI tool that asks first."*

### Mid-tier (smaller wins, real returns)

4. **Backlog ↔ agent linkage visualization** — backlog items already track `agent_status`/`agent_session_id`. Realize this on tiles via a real progress bar (matches open backlog item `b049c18f`). Tiny code change, big visual signal.
5. **Session-revival UX surfacing** — capability is shipped, users don't see it. Surface "Resumed from transcript" badges on revived sessions. Make the magic visible.
6. **Diagram colors guarantee** — Excalidraw bridge eats `classDef`. Either detect color-tagged sources and route through plain Mermaid, or post-process Excalidraw output to honor styles.

### Don't invest here yet

- Animated logos (`ce8e1927`, `3bc90f3a`) — pure polish, no leverage
- Density tokens refactor (`ce1ecf38`) — internal cleanup, invisible
- Voice mode (`mode-c-audio` branch) — Claude CLI catching up on voice makes this wasted effort; re-evaluate in a quarter
- Drag-modal-to-edge snap (`26c6a449`) — neat but niche; multi-modal already works fine

### Single-bet recommendation

If you only do **one thing next**: invest in **Hivemind as a first-class surface**, not a modal tab.

It's the feature **no one else has**, the one the README most needs to show off, and the one that turns "Clayrune is a nice multi-project dashboard" into **"Clayrune is the way to run a small fleet of cooperating agents."** Everything else (mobile polish, plan approval depth) is improvement; Hivemind is identity.

---

## 5. Competitive frame (one-line)

**Clayrune is the operator console for long-running Claude agents** — the niche between *single-pane CLI* (Claude CLI) and *autonomous SaaS* (Devin).

| vs. | Wins on | Loses on |
|---|---|---|
| Claude CLI | Multi-project, persistence, mobile, plan approval, backlog | Freshness, portability, official support |
| Cursor / Windsurf | Project-agnostic, multi-project, scheduler, hivemind | No editor-depth; no inline diff/autocomplete |
| Devin | Local infra, plan-approval gate, multi-project oversight | No sandboxed compute / built-in browser |
| Claude Desktop / ChatGPT Desktop | Multi-project, persistent sessions, scheduler, backlog | Less polished, no native OS integrations |
| Aider / Cline / Continue.dev | Multi-project orchestration, mobile remote, scheduler | Smaller ecosystem, no git-commit native flow |

**Right user:** someone running 5–20 long-lived AI work-streams on their own machine and needing a dashboard, not someone doing one heads-down coding session.

---

## 6. Quick reference

- **Memory index:** `~/.claude/projects/C--Users-levir-Documents--claude-mission-control/memory/MEMORY.md`
- **Topic memory files:** `remote_access_device_naming.md`, `clayrune_scheduler.md` (sibling files in same dir)
- **CHANGELOG:** `CHANGELOG.md` — most recent entry is `[2026-05-04]` Diagrams polish
- **Remote-access deep-dive resume file:** `docs/remote-access/RESUME_HERE.md` (separate scope)
- **Top-level docs:** `BUILD_INSTRUCTIONS.md`, `CLAUDE_KB.md`, `README.md`
