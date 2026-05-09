# Clayrune — Resume Here

**Last updated:** 2026-05-07 (end-of-session)
**Branch:** `master` — clean tree (after the final retry-button commit), everything pushed.
**Latest committed:** `25651f0` — Claydo streaming + Settings update button + clayrune.io hosting prep, plus a small Claydo error-retry-button polish on top.

**This session's commits** (most recent first):
- *(uncommitted on disk: small Claydo retry-button polish)* — error UX in Claydo modal
- `25651f0` — **Claydo streaming + Settings update button + clayrune.io hosting prep**
- `8edd80d` — Claydo: draggable FAB (mistitled — same subject as 010de5a)
- `010de5a` — Claydo: marker reliability + within-session memory
- `34c93d8` — RESUME_HERE: post-Claydo session summary + verification checklist
- `4b65248` — **Ask Claydo helper + walkthrough rewrite + USER_GUIDE.md**
- `24993da` — Walkthrough: filter out `_incognito` from first-run gate
- `da932fe`, `9d2909f`, `3824658`, `18ead06`, `d498bb4`, `5607839`, `36be356` — **Installer hardening chain** (7 commits) tested end-to-end on WSL Ubuntu
- `28ced41` — Scheduler reliability + run history pagination (paginated Runs panel + Run Now)
- `4a7dd4b` — Hivemind global surface + trigger-aware run history + sizeAgentChat fix

> Pick this up after a system restart. Skim section 0 for what's pending,
> then section 1 for state-of-the-world, then section 4 for next steps.

---

## 0. What's pending after the next reboot

**Tree state**: one small uncommitted change (Claydo error retry button) — needs to be committed before pushing. Everything else from today is shipped.

**Server restart needed** to pick up: `/api/guide/stream` (new), `/api/system/update/status` (new), `/api/system/update` (new) — all from `25651f0`.

**User action queue** (you, Ron):

- [ ] **Validate the install on a vanilla Windows host** (the only thing not yet tested end-to-end this session). PowerShell command in section "Vanilla Windows install — test recipe" below.
- [ ] **Set up Cloudflare Pages for clayrune.io** following `docs/HOSTING.md`. ~10 minutes — domain DNS to Cloudflare, Pages project, output dir = `installer/`, custom domain attach. After that, `clayrune.io/install.sh` works directly (no `CLAYRUNE_PROMPT_URL` env var needed).
- [ ] **Test the new Claydo features** that landed today after restart + hard-refresh: streaming token-by-token, draggable FAB, retry button on errors, marker reliability with new few-shot prompt.
- [ ] **Test the Settings → Update Clayrune** button. Make a deliberate small change on master + push, then click Update from the dashboard — should report "1 commit behind", click Update, see the pull succeed, click Restart now.

Three things to do *immediately* after reboot:

### A. Server restart (required to pick up new endpoints)

Open Settings → Server → Restart server, OR kill and re-launch the server manually.
The new endpoints in `4b65248` (`POST /api/guide/ask`, `GET /assets/<path>`) need a restart.

### B. Hard-refresh the browser dashboard

Picks up the rewritten walkthrough + the floating Claydo button + the marker parser CSS.

### C. Manual end-to-end test of "Ask Claydo"

Walk through the verification checklist below to confirm `4b65248` is solid.

---

## Verification checklist for "Ask Claydo" (after restart + hard-refresh)

**Floating button**:
- [ ] Bottom-right of dashboard — visible? Pulsing accent-orange ring (until first click)?
- [ ] Click it → `__claydo` modal opens, pulse stops, `localStorage.claydo_opened` is set.

**Chat flow**:
- [ ] Welcome message renders with Claydo's greeting.
- [ ] Type `"how do I start a hivemind?"` → "Claydo is thinking..." indicator → answer renders within ~10s.
- [ ] Answer references the 3-dot menu and ✨ Start Hivemind item.
- [ ] **Markers fire**: a UI element should pulse (highlighted by `.clayrune-highlight`) — most likely the 3-dot button or the sidebar Hivemind entry.

**Walkthrough**:
- [ ] In an incognito browser (or after `localStorage.clear()`), reload the dashboard.
- [ ] Walkthrough fires after ~600ms on the empty dashboard (project list excluding `_incognito` is empty).
- [ ] All 16 steps render with current copy. Last two steps:
  - [ ] **ask-claydo** highlights the floating Claydo button (left-positioned card pointing at it).
  - [ ] **done** wraps up with "Get Started" button.

**If anything fails**:
- Check `claude --version` works on the host the server runs on (Claydo subprocesses `claude` to answer).
- Check `docs/USER_GUIDE.md` exists in the install dir (Claydo loads it as system prompt).
- Check `assets/clayrune.png` is fetchable at `/assets/clayrune.png` (the FAB icon).

---

## What landed this session — short version

The whole session was about closing the **first-time-user gap**: getting a clean install and a guided UX. Three threads, all shipped:

### Thread 1 — Installer (Claude-driven, browser-only v1)

End-to-end install path: user runs one terminal command, Claude CLI installs everything. Cross-platform "for free" because Claude detects OS / package manager / Node / Python / etc.

- `installer/install.sh`, `install.ps1` — bootstraps that verify/install Claude CLI then hand off the install prompt to it.
- `installer/install-prompt.md` — the prescriptive 6-STEP prompt Claude executes (clone repo → Python venv → Node check → desktop launcher → start server → open browser).
- `installer/start.sh / start.command / start.bat` — per-OS launchers wired to a Desktop / Start-Menu / Applications shortcut.
- `assets/clayrune.png` — 1024×1024 RGBA mascot icon.

Tested end-to-end on **WSL Ubuntu** with multiple iterations exposing bugs and progressively hardening the bootstrap:
- Node 18+ preflight (auto-installs via nvm if missing/old) — sidesteps the io.js-2.5.0-as-default trap.
- Multi-method install (Anthropic curl-installer first, npm fallback) with **post-install validation** (`claude --version` actually runs, not just `command -v claude`).
- Auth preflight — catches "Not logged in" before handoff with a clear 3-step `claude /login` recipe.
- `bash` (not `sh`) for Anthropic's installer — their bootstrap uses bash-only syntax.
- nvm sourced into `~/.bashrc` so subsequent shells find Node + the npm-installed `claude`.
- "Run `exec bash -l` and remember to type `exit` after `/login`" hint added to the auth-failure recipe.

Final test produced: ✅ install dir at `~/Clayrune` / Python venv / running server on `:5199` / `.desktop` launcher in `~/.local/share/applications/` / browser pointer printed (xdg-open absent on minimal WSL).

**Hosting plan**: `clayrune.io/install.{sh,ps1}` and `clayrune.io/install-prompt.md`. Domain not yet up — testing currently uses `raw.githubusercontent.com/ronle/mission-control/master/installer/...` via `CLAYRUNE_PROMPT_URL` env var.

### Thread 2 — Walkthrough rewrite

Old 19-step walkthrough referenced removed surfaces (Hivemind tab) and missed everything new (Hivemind sidebar / Scheduler Run-Now / Runs panel / new 3-dot menu structure). Rewritten to **16 steps** in `WT_STEPS` (`static/index.html`):

```
welcome → advanced-picker → sidebar → header → toolbar → sample-tile →
open-modal → tabs → agent → menu → hivemind-sidebar → scheduler →
console → bottom-tabs → cmd-palette → ask-claydo → done
```

Each step's copy reflects the current UI: 3-dot menu lists Hiveminds + Start Hivemind + Memory & Rules + Status/Color/Domain/Model + GitHub Sync; Tabs no longer mentions Hivemind; new dedicated steps for Hivemind sidebar, Scheduler, and the new floating Claydo button.

**Walkthrough trigger fix** (also shipped): the first-run gate was `allProjects.length === 0`, but the auto-created `_incognito` project always counts as 1, so first-run never fired. Fix: filter via `isIncognitoProject()` before counting.

### Thread 3 — "Ask Claydo" in-app helper (NEW)

A floating circular button bottom-right of every viewport, opens a chat modal where users ask plain-English questions about Clayrune. Claydo answers + emits inline UI control markers that highlight the relevant element while explaining.

- **Surface** (`static/index.html`):
  - `<button id="claydo-fab">` — 56 px FAB with the mascot icon, accent border. Pulses on first visit until the user opens it once (persisted in `localStorage.claydo_opened`). Mobile sits 70 px above the bottom tab bar.
  - `__claydo` modal — chat history + input pinned bottom. Each open is a fresh conversation (no per-session memory in v1).
  - `submitClaydo()` POSTs the question, renders the answer.
  - `_claydoParseMarkers()` strips `[clayrune:...]` markers + queues actions.
  - `_claydoDispatchActions()` runs them with 350 ms stagger.
  - `_claydoFormatText()` light markdown (bold + inline code + newlines).
- **Backend** (`server.py`):
  - `POST /api/guide/ask` — single-shot. Reads `docs/USER_GUIDE.md` as system prompt, runs `claude -p <question> --append-system-prompt <guide> --max-turns 1`. 60 s timeout, 2000-char question cap.
  - `GET /assets/<filename>` — static-file route serving the mascot icon and any other repo assets the FE needs.
- **Marker protocol**:
  - `[clayrune:goto view="hivemind"]` → `sidebarNav('hivemind')`
  - `[clayrune:open-modal project="abc123"]` → `openProjectModal('abc123')`
  - `[clayrune:highlight selector="#sidebar-item-hivemind" duration=2500]` → `.clayrune-highlight` CSS pulse + `scrollIntoView`
  - All read-only — no destructive actions in v1.
- **Knowledge source**: new `docs/USER_GUIDE.md` (~310 lines). Comprehensive user-facing reference. Sections cover every surface, common-tasks recipes (with marker syntax baked in), keyboard shortcuts, glossary, troubleshooting. Plays double duty as Claydo's system prompt AND a human reference.
- **Naming convention** (saved as memory `naming_claydo_clayrune.md`): Claydo = mascot character, Clayrune = product. Marker prefix stays `clayrune:` (product-namespaced); only the user-facing helper is "Ask Claydo."

**Voice**: deliberately not playful or childish (per Ron). Friendly + tight register. The system prompt at the bottom of `USER_GUIDE.md` instructs Claydo on tone.

### Thread 4 — Claydo polish iterations (`010de5a`, `8edd80d`, `25651f0`)

After the initial Claydo landed, several refinements followed in the same session:

- **Marker reliability** (`010de5a` part 1): the original system prompt described markers as a soft guideline; the model treated emission as optional. Live testing surfaced "Claydo answered correctly but didn't open/highlight the menu." Strengthened the system prompt with **hard rules** (marker emission is MANDATORY for how/where questions), **7 concrete few-shot Q→A pairs**, a **CSS selector cheatsheet** (17 entries pulled from live UI), and a hard ban on `[clayrune:open-modal project="<id>"]` placeholder fabrications. Common-tasks recipes rewritten to use only working selectors.
- **Within-session memory** (`010de5a` part 2): `_claydoHistory` array tracks last 12 messages (~6 exchanges). Each request to `/api/guide/ask` now sends the last 6 messages as `history`; backend prepends them as "Previous exchange in this conversation: …". Cleaned text only (markers stripped before storing) so Claydo doesn't re-emit prior highlights. Reset on close+reopen, preserved across minimize+restore.
- **Draggable FAB** (`8edd80d`): tap-vs-drag detection via 5px movement threshold. Tap → opens modal as before. Drag → button follows cursor; on release, position persists in `localStorage.claydo_fab_pos`. Touch + mouse both supported. Re-clamps to viewport on resize so it can't get trapped off-screen.
- **Streaming responses** (`25651f0` part 1): replaces the 5–15s blocking wait with token-by-token streaming via SSE. New `/api/guide/stream` endpoint spawns claude with `--output-format stream-json`, parses assistant text blocks, emits `delta` / `done` / `error` events. Subprocess killed on client disconnect (GeneratorExit handler) so abandoned conversations don't burn tokens. Frontend `submitClaydo` rewritten to use `fetch + ReadableStream`; bot message div fills incrementally; markers parsed from final assembled answer at done event (mid-stream marker firing is v2 polish).
- **Error retry button** (uncommitted on disk): error bubbles in the chat now have a **Retry** button. Removes the failed user message from `_claydoHistory`, refills the input box, lets the user re-submit (or edit + re-submit). Surfaced after a transient `claude exit 1` that didn't recover gracefully.

### Thread 5 — Settings → Update Clayrune (`25651f0` part 2)

Closes the "users without git CLI experience can't update" gap.

- New `GET /api/system/update/status` — runs `git fetch --quiet`, returns `{is_git_repo, branch, commit, behind, ahead, has_local_changes, update_available}`.
- New `POST /api/system/update` — runs `git pull --ff-only`. Refuses with 409 if working tree is dirty. Returns `recent_log` + `new_commit` on success.
- Settings → Server section gets an "Update Clayrune" row below the existing "Restart server" row. Auto-checks status when Settings opens. Button reads `Update (N)` when N commits behind, `Up to date` when in sync. Successful pull flips the button to `Restart now` which jumps to the existing restart confirm flow.
- Helper `_git(args, cwd)` wraps `subprocess.run` with the project's Windows-flag boilerplate.

### Thread 6 — clayrune.io hosting prep (`25651f0` part 3)

Files in place; still needs Ron to set up Cloudflare Pages + DNS.

- `installer/_headers` — Cloudflare Pages config. Sets `Content-Type: text/plain; charset=utf-8` on `.sh`/`.ps1`/`.md` files so curling them works AND viewing in a browser doesn't trigger a download.
- `installer/index.html` — landing page for `clayrune.io`. Dark theme, brand orange accent, hero + 6-feature grid + per-OS install commands + audit link to `/install-prompt.md` + GitHub source link.
- `installer/clayrune.png` — copy of the mascot icon for the landing page (referenced as `/clayrune.png`).
- `docs/HOSTING.md` — step-by-step Cloudflare Pages setup. ~10 minutes for the operator. After setup, every push to `master` auto-redeploys; bootstrap fetches from `clayrune.io/install-prompt.md` directly (no env-var override needed).

---

## Things still TBD (after testing + the next session)

### User-action queue

- [ ] **Validate vanilla Windows install** (the only platform not tested end-to-end this session — `install.ps1` path).
- [ ] **Set up Cloudflare Pages for clayrune.io** following `docs/HOSTING.md`. ~10 min.
- [ ] **Test the new Claydo features** end-to-end after restart (streaming, draggable FAB, retry button, marker reliability with new prompt).
- [ ] **Commit the uncommitted retry-button polish** if it's still on disk in the next session.

### Engineering items still TBD

- **Mid-stream marker firing** — Claydo currently parses markers only at the `done` event. Mid-stream firing (so highlights appear as Claydo "speaks them" rather than after) is the next polish layer.
- **Suggested follow-up chips** — after each answer, Claydo could offer 3 clickable next-questions ("Want to know how to schedule one?"). A new `[clayrune:suggest "Q1" "Q2" "Q3"]` marker shape.
- **Claydo as actor** — v1 is read-only. v2 could add safe destructive markers (`create-schedule`, etc.) gated by explicit user confirmations.
- **Telemetry on installer success/failure** — anonymous, opt-in. Count successful installs and where they fail. Helps drive the install prompt iteration.
- **CI check** that any user-visible UI/server change also touches `docs/USER_GUIDE.md` so Claydo's knowledge stays current. Soft warning, not a block.
- **Versioned install prompts** at clayrune.io (e.g. `install-prompt-v1.md`) so updates can ship without breaking in-flight installs.
- **POSIX adapter parity for the existing remote-restart code** (CHANGELOG `[2026-05-05]` flagged this as a TODO — netstat equivalent + log redirection for non-Windows).
- **Mobile clay icon** — the FAB on mobile is 50 px which feels right; verify it doesn't clash with the bottom-tab bar in landscape orientation.
- **First-launch UX**: walkthrough → Claydo. After the rewritten walkthrough completes, the Claydo button is pulsing for the user. The "ask-claydo" step calls it out. Could optionally auto-open the modal at the end of the tour — currently doesn't, by design (user clicks if they want).

---

## Older context (older commits this session, full detail in CHANGELOG)

For brevity, the per-thread post-mortem detail of the earlier commits this session has been moved out of this section. The CHANGELOG entries `[2026-05-07]`, `[2026-05-07b]`, `[2026-05-07c]` cover them. Key commits:

- **`28ced41`** Scheduler reliability + run history pagination (SSE-slot fix, dispatch-pending agent_log rows, tab strip filter, started_at-not-ts, retention 500-cap, Runs panel pagination).
- **`36be356`** Installer scaffold: Claude-driven bootstrap + per-OS launchers (the initial drop before today's hardening).
- **`5607839`, `d498bb4`, `18ead06`, `3824658`, `9d2909f`, `da932fe`** — six iterations of installer hardening surfaced by WSL Ubuntu testing today.
- **`24993da`** Walkthrough trigger fix (`_incognito` was suppressing the first-run gate).

### Still committed earlier this session (full detail follows in section 1):

- Hivemind elevated to a first-class surface (`4a7dd4b`)
- Trigger-aware run history with paginated Runs panel + Run Now (`4a7dd4b` + `28ced41`)
- `sizeAgentChat` measurement-loop fix (`4a7dd4b`)

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
