# Clayrune — Resume Here

**Last updated:** 2026-05-07 (PM)
**Branch:** `master`
**Latest committed:** `4a7dd4b` — Hivemind global surface + trigger-aware run history + sizeAgentChat fix.
**In working tree, NOT yet committed:** see section 0 below — five discrete units of work from today's session, plus the Claydo design.

> Pick this up after a system restart. Skim section 0 for what's pending,
> then section 1 for state-of-the-world, then section 4 for next steps.

---

## 0. What's in flight RIGHT NOW (today, 2026-05-07)

Working tree contains **five units of code** (A, B, B′, B″, C) **and one design-only item** (D, Claydo helper). The five code units split naturally into:

- **A + B + B′ + B″** — scheduler reliability & run-history UX. Tightly related, share files (`server.py` + `static/index.html`), one commit makes sense.
- **C** — installer scaffold. Untracked `installer/` dir + `assets/clayrune.png`. Independent.

Run `git status` to see the file list; `git diff` to review individual hunks.

### A. SSE-slot fix + dispatch-pending agent_log rows (CHANGELOG `[2026-05-07]` already drafted)

**Why** (two related symptoms users hit when the scheduler ran heavily):
1. **Page becomes unresponsive** every so often — closing & reopening the tab restored it.
2. **Empty "Runs" panel** even after a schedule had clearly fired.

**Root causes** (full detail in CHANGELOG.md `[2026-05-07]`):
- The 15s fallback-poll loop in `static/index.html` was still reconnecting EventSources for both `running` AND `idle` sessions. Idle Mode B sessions accumulate forever (server's stale-session sweep skips them), so within hours 6+ live SSEs saturated Chromium's 6 per-origin slot cap → `/api/processes`, `/api/config`, etc. queued forever → page hung. Mirrors the earlier `fetchAgentStatus` fix; this loop was the missed sibling.
- Mode B scheduler-dispatched sessions go idle without exiting, so `_log_agent_completion`'s finally block never runs → the `trigger_type='schedule'` row never reaches `agent_log.json`. The `/api/schedule/<id>/runs` filter then finds nothing.

**Files touched**:
- `server.py` — new `_log_agent_dispatch_pending(session)` (writes a placeholder row at dispatch time with full trigger info, status `'in_progress'`); `_log_agent_completion` upserts that row; new `_reconcile_pending_agent_log_entries()` runs at startup to flip orphaned `'in_progress'` rows to `'interrupted'`; `_dispatch_agent_internal` calls the helper when `trigger_type != 'manual'`.
- `static/index.html` — drop the `=== 'idle'` reconnect branch from the 15s-poll block; `_runStatusIcon` shows the live accent dot for `'in_progress'`.

**Test after restart**: trigger a schedule fire (Run Now or wait); the Runs panel should show the run *immediately* with an `in_progress` indicator, then transition to `completed` when the turn finishes. Idle sessions accumulating no longer freezes the page over hours.

### B. Tab strip filter — completed/stopped automated tabs hidden (NEW, not yet in CHANGELOG)

**Why**: opening a project that had a schedule firing repeatedly showed 8+ near-identical agent tabs ("Run python scripts/he..."). Unusable on mobile, noisy on desktop.

**Files touched** (additive on top of A):
- `server.py` — `agent_status` endpoint also returns `trigger_type` + `trigger_id` per session (already added; consumed below).
- `static/index.html` — `fetchAgentStatus` captures the new fields into `agentHistory[].triggerType` + `agentStatusCache[sid].triggerType`. New `getProjectTabSessions(projectId)` filters out `trigger_type ∈ {'schedule', 'hivemind_worker'}` whose status ∈ `{'completed', 'stopped', 'error'}`. `agentPanelHTML` uses this filtered list for the tab strip.

**Behavior after restart**: scheduled runs only show as tabs while running. Completed runs stay in the Scheduler's "Runs" panel (and Agent Log). Manual + hivemind-orchestrator tabs unaffected.

**Test**: trigger a Run Now → tab appears while in_progress/running → disappears once `completed`. View it via Scheduler → Runs.

### B′. Runs panel timestamp fix (small, frontend-only — added this session)

**Why**: after the restart, the Runs panel showed every shutdown-finalized session as "12m ago" because `renderRunRows` was reading `ts` (= finalize time, which becomes uniform for all sessions stopped during shutdown) instead of `started_at` (= dispatch time, which preserves real chronology).

**File touched**: `static/index.html` only — `renderRunRows` now picks `r.started_relative || r.started_at || r.ts_relative || r.ts`. Comment in the code explains the pitfall.

**Test**: hard-refresh; reopen any Runs panel — timestamps should now span the actual schedule fire times (yesterday/today), not the shutdown moment.

### B″. agent_log retention + Runs pagination (server + frontend, added this session)

**Why**: agent_log files grow unbounded. For a schedule firing every 30 min that's ~17k entries/year. Plus the Runs panel was a single scrollable list of up to 200 rows — too much to scan.

**Disk retention** (`server.py`):
- New config `agent_log_max_entries`, default **500**. Set to `0` to disable.
- `_save_agent_log` slices to the most recent N before persisting (newest are at index 0). Existing oversized files don't get retroactively trimmed; they shrink the next time anything writes to them.

**Endpoint pagination** (`server.py`):
- `/api/schedule/<id>/runs` and `/api/hivemind/<id>/runs` now accept `?limit=` (default 50, max 200) and `?offset=` (default 0).
- Response shape changed to `{runs, total, offset, limit}` — total is the across-all-pages count so the frontend can render pagination controls.

**Pagination footer** (`static/index.html`):
- New `renderRunsPagination(total, offset, limit, pageFnTemplate)` helper renders `«   ‹ Prev   Page X of Y · N total   Next ›   »` below the rows. Buttons disabled at bounds. CSS class `.runs-pagination`.
- `toggleScheduleRuns` now delegates to `loadScheduleRunsPage(scheduleId, projectId, offset)`. Pagination buttons re-call this with new offset.
- `openHmRunsModal` similarly delegates to `loadHmRunsPage(hivemindId, projectId, role, wsId, offset)`.
- Each panel resets to page 1 on (re-)open.

**Test after restart**: open Runs on any schedule that has > 50 entries → first 50 rows + footer with Next/Last; click Next → next page loads; first/last buttons jump to bounds.

### Commit suggestion for A + B + B′ + B″

All four are about scheduler/run visibility & quality. Cleanest:

- **One commit**: `"Scheduler reliability + run history pagination"` — pulls in the CHANGELOG `[2026-05-07]` entry, appends sub-sections for the tab strip filter, started_at fix, retention cap, and pagination. Code-wise these touch the same files (server.py + static/index.html) and are mutually consistent.

If you'd rather split, the natural break is A+B (the bug fixes) vs. B′+B″ (the run-history UX).

### C. Installer scaffold (untracked — `installer/` and `assets/clayrune.png`)

**Why**: today's design conversation about a "Claude-driven installer" — bootstrap shell script verifies/installs Claude CLI, fetches a prescriptive prompt from `clayrune.io/install-prompt.md`, pipes it into `claude --dangerously-skip-permissions` which performs the actual install. No installer pipeline to build. Cross-platform "for free."

**New files** (all in `installer/`):
- `install-prompt.md` — the prescriptive Claude prompt, ~200 lines, 6 STEPs. Already existed from a prior partial session; verified solid.
- `install.sh` — macOS/Linux bootstrap (~110 lines).
- `install.ps1` — Windows PowerShell bootstrap (~110 lines).
- `start.sh` — Linux launcher (activates venv, runs `python server.py`, opens browser via `xdg-open`).
- `start.command` — macOS launcher (same role; opens via `open`).
- `start.bat` — Windows launcher (same role; opens via `start http://...`).
- `README.md` — architecture diagram + hosting plan + testing checklist.

**Plus**: `assets/clayrune.png` — 1024×1024 RGBA Claydo character icon, source for all per-platform icon variants (`.ico`, `.icns`, scaled PNGs). Generated by Ron from the design system; saved from `data/uploads/agent_2b72e64f18.png`.

**Hosting plan**: `clayrune.io/install.sh`, `clayrune.io/install.ps1`, `clayrune.io/install-prompt.md`. Domain not yet up — for first round of testing point bootstraps at `raw.githubusercontent.com/ronle/mission-control/master/installer/<file>` via the `CLAYRUNE_PROMPT_URL` env var.

**Testing checklist** (from `installer/README.md`): clean Windows 11, macOS 14+, Ubuntu 22.04 VMs. Each install should complete in <5 min, end with the browser open at localhost:5199, place a clickable launcher on Desktop + OS app menu, and survive a re-run (idempotent).

**Suggested commit message**: "Installer scaffold: Claude-driven bootstrap + install prompt + per-OS launchers"

### D. "Ask Playdo" helper — design locked, NOT YET STARTED

> **Naming convention (2026-05-07):**
> - **Playdo** = the mascot character (the cute clay figure at `assets/clayrune.png`). Originally proposed as "Claydo" but that was unavailable; "Playdo" is the final character name.
> - **Clayrune** = the product/brand.
> - **The helper** is "Ask Playdo" — Playdo is the in-app guide. Keeps the mascot's voice distinct from the product.

The design conversation locked the v1 plan. **No code written.** Three open questions resolved, ready to start whenever Ron says go:

**The plan**:
- **Surface**: floating circular button bottom-right, always visible (desktop + mobile, mobile sits 70px above bottom-tab bar). Icon: the Playdo mascot character. Tooltip: "Ask Playdo." Pulse animation until first open (persisted in `localStorage`).
- **Naming**: "Ask Playdo." Modal title: "Ask Playdo."
- **No sidebar entry** — floating button is the universal pattern (Intercom / Copilot Chat / Slack).
- **Behind the scenes**: an incognito Claude session spawned via existing agent infra, system prompt loaded from `docs/USER_GUIDE.md`. Streaming response via SSE.
- **UI control markers** that the assistant emits inline; frontend strips them and acts. Marker prefix is `clayrune:` (the product, not the mascot — keeps the namespace tied to the app):
  - `[clayrune:goto view="hivemind"]` → `sidebarNav('hivemind')`
  - `[clayrune:open-modal project="abc123"]` → `openProjectModal('abc123')`
  - `[clayrune:highlight selector="#sidebar-item-hivemind" duration=2500]` → CSS pulse animation
  Markers are read-only — no destructive actions in v1.
- **Knowledge source**: new `docs/USER_GUIDE.md` (sibling to existing developer-focused `CLAUDE_KB.md`). Sections: Quick start / Features overview / Common tasks (with marker recipes baked in) / Keyboard shortcuts / Glossary / Troubleshooting. Maintained in repo so updates ship with releases.
- **First-time tour integration**: at the end of `startWalkthrough()`, auto-open the "Ask Playdo" modal pre-focused on the input with a welcome message.

**Three open decisions Ron has now resolved** (going into v1 build):
1. Streaming response token-by-token via SSE — yes, friendlier, reuses existing infra.
2. Walkthrough's last step opens the Claydo modal directly — yes, eliminates one click on the most important first impression.
3. Pulse animation cadence: pulse the floating button on every page load until the user opens it once, then stop forever (persisted in `localStorage`).

**Build order** (when starting):
1. Write `docs/USER_GUIDE.md` — the foundation. Without this the assistant has nothing to say.
2. Floating "Ask Claydo" button — fixed-position circular button bottom-right with pulse animation.
3. `__clayrune_guide` modal — chat-style interface, opens on button click. Reuse existing modal infrastructure.
4. Backend endpoint `POST /api/guide/ask` — spawns a Claude session with `USER_GUIDE.md` as system prompt + the user's question, streams back via SSE. Treats it like an incognito agent session under the hood (no project memory writes).
5. Marker parser — frontend regex strips `[clayrune:...]` markers from the assistant's text and dispatches the corresponding actions.
6. Highlight CSS — `.clayrune-highlight` class with orange-pulse animation, auto-removes after the marker's duration.
7. Walkthrough integration — auto-open modal on tour completion.

---

## 1. Where we are (committed work)

- **Hivemind elevated to a first-class surface (committed in `4a7dd4b`).** Sidebar gets a 🐝 Hivemind entry that opens a cross-project list (`__all_hivemind`) with status / project / search filters, status pills, short ID hashes, planner/worker tree mini-viz per card, pause/stop/resume controls. Mobile bottom-tab bar swapped Settings → Hivemind (Settings via avatar). Per-project Hivemind tab REMOVED — replaced by 🐝 Hiveminds + ✨ Start Hivemind in the project's 3-dot menu. Start Hivemind auto-dispatches the setup prompt instead of leaving the user staring at a populated form. Stale heuristic: `active`/`paused` + no activity > 24h = rendered as "stale" with grey badge + Restart control; server-side `_hm_reconcile_stale_on_startup` rewrites the manifest at boot so the disk reflects reality.
- **Trigger-aware run history (committed in `4a7dd4b`).** Every `agent_log` entry now carries `trigger_type` (`manual` / `schedule` / `hivemind_orchestrator` / `hivemind_worker`) and `trigger_id`. Three new endpoints: `GET /api/schedule/<id>/runs`, `GET /api/hivemind/<id>/runs?role=&ws_id=`, `GET /api/project/<pid>/transcript/<csid>` (read-only parsed transcript). UI: Runs button on every schedule card (inline expanding panel), Runs button on each Hivemind workstream + Orchestrator Runs in overview. Each row click opens a shared transcript viewer modal. Plus a **▶ Run Now** button on the far right of every schedule card (and in the edit form) that fires the task immediately, stamps trigger metadata, and updates `last_run` without touching `next_run`.
- **`sizeAgentChat` measurement-loop fix (committed in `4a7dd4b`).** Fixed Send-button bottom-border clipping caused by `chatInputEl.offsetHeight` returning the squashed value from the previous over-allocation, feeding back into a smaller `desiredOutH` each refresh. Now resets output's explicit sizing before measuring AND computes `inputH = max(offsetHeight, scrollHeight, rowH + paddingV, 80)` — three independent signals plus an 80px safety floor.
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
