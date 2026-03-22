# Mission Control — Changelog

## [2026-03-22b] — .NET fallback, Process Manager

### .NET runtime fallback
- Desktop app now gracefully handles missing .NET Desktop Runtime on target machines
- Shows a Windows MessageBox explaining the issue instead of crashing with a raw traceback
- Falls back to opening Mission Control in the default browser so the app is still usable
- Provides download link for .NET Desktop Runtime

## [2026-03-22] — Process Manager (PID Tracker)

### Process Manager
- Centralized PID tracker for all subprocess spawns (agents, terminals, housekeeping)
- Each process registered with human-readable name, type, project, session ID, and task preview
- Header "Processes" button opens 800px modal with live process table
- Table shows: status dot (green=alive, red=dead, gray=exited), PID, name, project, task/command, duration, kill button
- Toolbar displays running/total count with Refresh and "Cleanup Orphaned" buttons
- Kill button terminates individual processes and updates corresponding agent/terminal session status
- "Cleanup Orphaned" kills all processes that are alive but whose sessions are gone or completed
- Scheduler liveness sweep auto-removes dead processes every 30 seconds
- API endpoints: `GET /api/processes`, `POST /api/processes/<pid>/kill`, `POST /api/processes/cleanup`
- All 7 Popen call sites instrumented: Mode A/B agents, followups, respawns, housekeeping, terminals
- Process unregistered at all kill/cleanup/completion points (stream reader finally blocks, stop, delete, atexit)

## [2026-03-22a] — Claude Code channels, remote control, cron schedules, token display

### Claude Code Channels support
- New `agent_channels` config option (global or per-project)
- Appends `--channels <value>` to agent spawn command
- Supports Telegram, Discord, and custom MCP channel plugins

### Remote Control flag
- New `agent_remote_control` config option (global or per-project)
- When enabled, appends `--remote-control` to agent spawn
- Allows controlling MC-managed agent sessions from claude.ai or mobile app

### Cron expression support for scheduler
- New "Cron" schedule type alongside Daily/Interval/Once
- Standard 5-field cron expressions: minute hour day-of-month month day-of-week
- Supports wildcards, ranges, steps, comma-separated lists
- Vixie-cron semantics for day matching

### Scheduler modal now draggable
- Added `.modal-header` to scheduler window for grab-and-drag

### Enhanced token/context usage display
- Status bar shows token breakdown with cache read info during and after runs
- Turn count shown in status bar and agent log entries
- Metrics update live every second during running sessions

## [2026-03-21a] — Mobile touch fix, auto-fresh sessions, TTY shim, toast notifications

### Mobile tile drag fix
- Tile reordering now requires a 300ms long-press before drag starts
- Scroll, swipe, and pinch-to-zoom gestures pass through to browser normally
- Multi-finger touches (pinch) are ignored by the drag handler entirely
- Visual scale feedback on long-press activation
- Separate tile order for mobile vs desktop (mobile is local-only, desktop is source of truth)
- Insert-and-shift tile reorder: dragging a tile between others pushes them right instead of swapping

### Auto-fresh large sessions
- Sessions with transcripts > 5 MB are auto-started fresh instead of resumed
- Prevents slow startup from loading massive conversation history
- Context note injected so agent knows it's continuing from a prior session
- Covers all resume paths: main dispatch, Mode A followup, Mode B respawn
- Activity log entry notifies user of auto-fresh with size info
- Toast notification shown in UI when auto-fresh triggers

### Toast notification system
- Lightweight toast notifications slide in from top-right corner
- Auto-dismiss after 5 seconds with fade-out animation
- Used for auto-fresh session alerts; available for future notifications via `showToast()`

### TTY shim improvements (`mc_tty_shim/sitecustomize.py`)
- Added `_FakeBuffer` wrapper — preserves `isatty()=True` through `TextIOWrapper` re-wrapping
- Auto-flush on buffer write — fixes Rich `Live` display buffering with `line_buffering=True`
- Patched `os.get_terminal_size()` and `shutil.get_terminal_size()` to read `COLUMNS`/`LINES` env vars when pipe fd fails
- Root cause: dashboard's `sys.stdout = io.TextIOWrapper(sys.stdout.buffer)` was overwriting the TTY shim

### Agent tab ordering
- New agent tabs now appear on the right side of existing tabs (chronological order)
- Sessions sorted by `startedAt` ascending in the tab bar

### GitHub Issues sync (Phase 1) — `github_sync.py`
- Bidirectional sync between MC backlog items and GitHub Issues via `gh` CLI
- Security: `sanitize()` strips HTML, dangerous protocols, control chars from all GitHub text
- 4 new API endpoints: setup, disconnect, sync, status
- Auto-sync every 5 minutes via scheduler
- Sync badge in backlog header, `#N` issue links on items, three-dot menu integration
- Activity Stream integration for all sync events

## [2026-03-20a] — Fix ExitPlanMode infinite loop in agents

- Agents spawned by Mission Control could get stuck calling ExitPlanMode in an infinite loop
  (known Claude CLI bug: `--dangerously-skip-permissions` does not auto-approve ExitPlanMode)
- System prompt now instructs agents to NEVER use EnterPlanMode or ExitPlanMode
- Mode A: if ExitPlanMode is detected in tool_use output, a follow-up message is queued
  telling the agent to proceed directly with implementation
- Mode B: `_auto_approve_plan_b()` sends an approval message via stdin immediately when
  ExitPlanMode is detected, breaking the loop

## [2026-03-19e] — TTY shim for Rich color support in terminal pop-outs

- `mc_tty_shim/sitecustomize.py` auto-injected via `PYTHONPATH` into terminal processes
- Child Python processes see `isatty()=True` via monkey-patched stdout/stderr
- Rich's legacy Windows detection patched — emits ANSI escape codes instead of Console API calls
- Full Rich table colors (truecolor), Live display, and styled output now render in xterm.js
- Terminal launch sets `MC_FORCE_TTY=1`, `TERM=xterm-256color`, `COLUMNS=120`, `LINES=30`
- Centralized `_kill_terminal_session()` helper for cleanup

## [2026-03-19d] — Two-tier memory with auto-condensation

- Session log overflow now archived to `MEMORY_ARCHIVE.md` instead of being deleted
- Archive is a sibling file to `MEMORY.md` — agents are told about it in system awareness
- Auto-condensation: when combined memory + archive exceeds threshold (default 15KB), a housekeeping agent runs to fold session insights into organized knowledge sections, keep last 5 session entries, and delete the archive
- Condensation uses a separate `claude -p` process with `--max-turns 5` and configurable model (default: sonnet)
- Housekeeping sessions visible in agent log but marked `housekeeping: True` — their completion does NOT trigger further memory appends or condensation (prevents circular triggers)
- New config options: `condense_threshold_kb` (default 15), `condense_model` (default sonnet), `condense_enabled` (default true)
- `_condensing_projects` set prevents double-dispatch of condensation for the same project
- Condensation skipped if any non-housekeeping agent is running/idle for the project

## [2026-03-19c] — Context budget auto-reduction

- MEMORY.md session log auto-pruned to last 20 entries when file exceeds 10KB
- Agent system awareness text compressed (~60% shorter) — removed instructional paragraphs
- Recent activity and agent session history reduced from 5 → 3 entries in appended context
- Session task truncation tightened from 80 → 60 chars in context
- Pre-dispatch context budget warning when CLAUDE.md + MEMORY.md + prompt exceeds 20KB

## [2026-03-19b] — Enhanced Plans tab with management tools

- Plans tab now shows checkboxes for multi-select, toolbar with Select All / Delete / Export
- Individual delete button (×) on each plan card
- Bulk delete with confirmation prompt — removes files from disk and scrubs agent log references
- Export selected plans as .md file downloads
- Plan cards show filename in faint text below the metadata
- New `POST /api/plans/delete` server endpoint with path security validation

## [2026-03-19a] — Embedded terminal pop-out windows

- Agents can launch CLI processes in visual pop-out terminal windows inside Mission Control
- Full ANSI color support via xterm.js (loaded from CDN) — dashboards, colored output, box-drawing all render correctly
- Agent uses `curl` to POST `/api/terminal/launch` — system prompt teaches this automatically
- Terminal appears as a draggable pop-out window (same pattern as Plan Viewer)
- Stdin input bar below terminal for sending input to running processes
- Stop button to kill processes, status dot shows running/completed/error/stopped
- Terminal sessions survive page refresh — only running sessions reconnect
- SSE streaming for real-time output (same 0.3s poll pattern as agent output)
- Server-side cleanup: atexit kills all terminal processes, delete_project cleans up sessions
- `[terminal:sessionId:command]` marker injected into agent SSE stream triggers auto-open on frontend
- Closing pop-out with X deletes session from server (won't reappear on refresh)
- Minimize/close controls positioned on right side of header bar
- Fixed: newlines in commands no longer break the terminal marker detection

## [2026-03-18c] — AskUserQuestion tool support

- Agent questions now appear as interactive forms in the chat (radio buttons, checkboxes, "Other" text input)
- Server extracts question data from `AskUserQuestion` tool_use blocks in both Mode A and Mode B stream readers
- New `question` SSE event type delivers structured question data to the frontend
- `renderAgentQuestion()` builds interactive form with options matching the tool's schema
- `submitQuestionAnswer()` formats selected answers and sends as follow-up message
- Single-select (radio) and multi-select (checkbox) modes supported
- Form greys out after submission with answer summary
- `_format_tool_activity()` now shows question preview text instead of bare `[tool: AskUserQuestion]`

## [2026-03-18b] — Walkthrough tour improvements

- Header highlight split into two focused steps (logo area + action buttons) instead of one broad highlight
- Enhanced demoTarget sub-element highlighting with accent outline on tab bar and menu button
- Added 4 new menu feature steps: Change Status, Color & Domain, Agent Model, GitHub Sync
- New `wtDemoMenuHTML()` renders virtual modal with menu dropdown open for the menu feature steps
- Tour now has 18 steps (was 13)

## [2026-03-18a] — Snap-to-grid tile arrangement

- Project tiles can be dragged to any grid cell position (Android home screen style)
- Dropping a tile onto another tile swaps their positions
- Empty grid cells (spacers) are invisible but occupy space — creating gaps between tiles
- Ghost preview follows cursor during drag with drop-target highlight
- Double-click an empty cell to remove the gap
- "Compact" button in filter row removes all gaps at once
- Grid layout persisted to server (`/api/grid-layout`) and localStorage
- Touch drag support for mobile devices
- Backlog dispatch triangle now fills current session's input (or +New via textareaValues)

## [2026-03-17i] — Remove Skills system

- Removed Skills tab from project modals (unused — Memory serves the same purpose)
- Removed global Skills manager (header button + modal)
- Removed all Skills API endpoints (global CRUD, project CRUD, attach/detach)
- Removed Skills helper functions and agent context injection from server.py
- Removed Skills CSS styles and JS functions from index.html

## [2026-03-17h] — First-run walkthrough tour

- Spotlight-style walkthrough highlights UI areas one at a time with dimmed backdrop
- 13 steps: welcome, header, new button, stats, project tile, modal, tabs, backlog, agent, menu, feed, console, done
- Sample project created automatically during tour via `POST /api/walkthrough/sample-project` (idempotent)
- Clip-path cutout on backdrop with pulsing accent-glow highlight ring around target elements
- Smart card positioning (top/bottom/left/right) with viewport clamping
- "Don't show again" checkbox on skip — lets users dismiss without completing
- Auto-triggers on first run (zero projects + no localStorage flag)
- Re-triggerable anytime via "Tour" button in header
- Escape key and window resize handling
- Mobile responsive card layout
- Virtual demo tile and modal shown during tour steps (not reliant on real DOM elements)

### Bug fixes
- Plans tab now shows plans from live running sessions, not just completed ones
- Stuck ExitPlanMode loop detection: after 3 consecutive calls, shows warning banner with recovery instructions
- `/api/project/<id>/plans` endpoint checks live `agent_sessions` in addition to on-disk agent log

## [2026-03-17g] — GitHub Issues sync (Phase 1)

### New module: `github_sync.py`
- Bidirectional sync between MC backlog items and GitHub Issues via `gh` CLI
- `sanitize()` strips HTML tags, `javascript:` URIs, null bytes, control chars from all GitHub text
- `validate_repo()` checks format + existence via `gh repo view`
- `gh_run()` safe subprocess wrapper (no shell=True, 30s timeout)
- `_pull_issues()` fetches GitHub issues, maps labels to priority, creates/updates backlog items
- `_push_items()` creates GitHub issues for unlinked MC items, syncs open/closed status
- `sync_project()` orchestrator with 60s rate limit and per-project threading locks

### Backend (`server.py`)
- 4 new endpoints: `/github/setup`, `/github/disconnect`, `/github/sync`, `/github/status`
- Scheduler auto-syncs every 5 minutes for projects with GitHub sync enabled
- All sync events logged to Activity Stream via `_log_agent_activity()`

### Frontend (`static/index.html`)
- GitHub Sync submenu in three-dot menu: connect (owner/repo input), sync now, disconnect
- Sync badge in Backlog section header (clickable to trigger sync)
- `#N` issue link badges on backlog items linked to GitHub issues
- `githubConnect()`, `githubDisconnect()`, `githubSyncNow()` JS functions

### Security
- All GitHub text sanitized before storage (HTML strip, dangerous protocol removal, char limit)
- Repo name validated with strict regex before any subprocess calls
- Subprocess uses argument list (never shell=True)

## [2026-03-17f] — Plan button persistence from agent log

- Plan file button in agent status row now populated from agent log entries
- After agent log loads, any session with a `plan_file` gets it set in status cache
- Ensures plan button shows for sessions that generated plans (even if loaded after initial fetch)

## [2026-03-17e] — Textarea preservation + charmap fix

- Textarea content now preserved across tab switches via global `textareaValues` cache
- Delegated `input` event listener on modal-layer captures values as user types
- Cache cleared on submit (dispatch, followup, backlog add, continue)
- Fixed Windows charmap codec error (`\u2192` arrow) crashing agent dispatch
- Replaced Unicode arrow in scheduler print with ASCII `->` equivalent

## [2026-03-17d] — Resume conversation after stop

- Stop kills the process (both modes), but conversation can be resumed via follow-up
- Mode B followup handler respawns process with `claude -r` when process is dead
- Reverted `CREATE_NEW_PROCESS_GROUP` flag that was breaking Mode B on Windows
- Input placeholder shows "Type to resume conversation..." for stopped sessions

## [2026-03-17c] — Plans History tab + UI polish

### Plans History tab
- New "Plans" tab in project modal shows all historical plan files generated under the project
- Backend persists `plan_file` path in agent log entries on session completion
- `GET /api/project/<id>/plans` endpoint scans agent log for entries with plan files
- `GET /api/plan-file?path=` endpoint reads plan file content (restricted to `~/.claude/plans/`)
- Plan cards show title (extracted from `# heading`), task, and relative timestamp
- Clicking a plan card opens the plan viewer modal with full formatted content
- Empty state shown when no plans exist for a project

### UI polish
- Agent chat follow-up input: added bottom padding to avoid clipping at modal edge
- Default modal tab changed from Backlog to Agent
- Modal resize corner grip made larger (14px desktop, 18px touch) with border-based indicator
- Scheduler modal: restructured header layout so "+ Add Schedule" button doesn't overlap window controls
- Tile dim colors made more vivid/saturated (amber, green, red, purple, accent)
- Plan button title now lazy-fetches the actual plan file `# heading` instead of showing session task text

## [2026-03-17b] — Scheduled Tasks

### Scheduler
- New Scheduled Tasks system: automate agent dispatch at configured times
- Three schedule types: Once (specific datetime), Daily (time + day-of-week), Interval (every N minutes)
- Background scheduler thread checks every 30 seconds and dispatches due tasks
- Extracted `_dispatch_agent_internal()` helper from endpoint for shared use by HTTP and scheduler
- CRUD API: GET/POST/PUT/DELETE `/api/schedules` with `data/schedules.json` storage
- `_compute_next_run()` calculates next execution time for each schedule type
- Scheduler auto-starts on server boot, auto-stops on shutdown via atexit

### Frontend
- "Scheduler" button in header opens modal with schedule list and add/edit form
- Schedule cards show project name, task, schedule description, last/next run times
- Enable/disable toggle per schedule, edit and delete actions
- Add/edit form with project dropdown, task textarea, type selector, day checkboxes (daily), interval input
- **Upcoming jobs banner**: top-of-page bar showing next 5 scheduled tasks with relative countdown times
- Banner auto-refreshes every 60 seconds, hidden when no upcoming schedules

## [2026-03-17a] — Persistent agent process (Mode B) + mobile touch support

### Persistent agent process (Mode B)
- New `use_streaming_agent` config toggle (default: false) enables Mode B alongside existing Mode A
- Mode B uses `--input-format stream-json` to keep a single Claude CLI process alive across turns
- Follow-ups write directly to stdin — no queuing, no process respawn, faster responses
- New `_read_agent_stream_b()` reader treats `result` messages as turn boundaries, not process exit
- New `idle` status: process alive and waiting for input (accent-colored dot with glow)
- SSE sends `turn_complete` events on idle, keeps stream open between turns
- `atexit` handler cleans up persistent processes on server shutdown
- Mode A (spawn-per-turn) unchanged — toggle off to use original behavior

### Mobile touch support
- Modal drag-to-move now works on touch devices (touchstart/touchmove/touchend)
- Separator drag (resize input area) works on touch devices
- Bottom-right corner touch resize for modals (40px hit zone with visual indicator)
- Pinch-to-resize: two-finger gesture scales modal width and height proportionally
- CSS `resize: both` disabled on touch devices (replaced by touch handlers)

### UI fixes
- Send button stays fixed size when expanding textarea (flex align-items: flex-end)
- Image previews now clear from DOM after sending follow-up
- Textarea resize handle removed (resize: none) — separator bar is the only resize control
- Agent output gets `flex: 1; min-height: 0` for proper flex sizing
- Queued follow-up echo shows yellow border + hint text (Mode A only)

## [2026-03-16d] — Full-height agent chat + performance overhaul

### Full-height agent chat
- Agent chat now fills the entire modal window height instead of fixed 450px
- `sizeAgentChat()` calculates available height dynamically and sets explicit pixel height
- ResizeObserver on modal content triggers re-sizing on window/modal resize
- Chat opens scrolled to the bottom showing latest messages
- 8px buffer between input area and modal bottom edge

### Draggable separator
- Replaced counter-intuitive bottom-corner resize handle with a draggable separator bar
- Separator sits between output and input areas — drag up/down to resize input
- Visual indicator (thin bar) with hover highlight

### Follow-up performance — non-blocking sends
- `sendFollowup()` is now fire-and-forget — no `await`, no `refreshModal()` call
- Local echo: user message appears instantly in DOM (`.agent-echo` class) before API responds
- Echo removed when server's version arrives via SSE (deduplication)
- Lightweight `updateAgentStatusUI()` replaces full modal rebuild for status changes

### Server-side performance
- Flask runs with `threaded=True` — SSE streams no longer block other requests
- Follow-up subprocess spawned in background thread — endpoint returns immediately
- SSE `since` parameter prevents replay of all historical lines on reconnect

### Long-running session optimizations
- DOM preservation in `refreshModalById()` — agent output element detached before `innerHTML` wipe, reattached after rebuild
- `_skipAgentOutput` flag skips expensive output line processing during preserved rebuilds
- Agent output DOM limited to 500 lines in modal, 200 in console tile, with "click to load all" button
- `agentOutputBuffers` capped at 2000 entries (trimmed to 1500 when exceeded)
- `renderAgentConsole()` optimized: skips line processing when panel is closed, efficient reverse-loop for lastTool

## [2026-03-16c] — Use Claude's native MEMORY.md for project memory

### Native memory integration
- Memory tab now reads/writes Claude Code's native `~/.claude/projects/<encoded-path>/memory/MEMORY.md`
- Path derived from project's `project_path` — same file the agent writes to with its Edit tool
- Fallback to `data/memory/<project_id>.md` for projects without a project_path
- Memory tab shows the resolved file path for transparency
- Auto-memory on session completion writes to the native location
- Agent system prompt simplified: tells agent the memory file path, no more curl API instructions
- Single source of truth — agents and dashboard share the same memory file

## [2026-03-16b] — Robust memory: append endpoint + auto-memory

### Memory append endpoint
- New `POST /api/project/<pid>/memory/append` — safely appends content without overwriting
- Agents can append to memory in one call instead of read-then-write
- Agent system prompt updated with all three memory API commands (read, append, replace)

### Auto-memory on session completion
- `_log_agent_completion()` now auto-appends a `## Session Log` entry to project memory
- Each entry: date, task name, brief summary (first 300 chars)
- Fails silently — never blocks the completion flow
- Memory builds passively even if the agent doesn't explicitly write to it

## [2026-03-16a] — Skills + Memory system

### Memory system
- New **Memory tab** in project modals — persistent per-project markdown memory
- Memory content injected into agent context as `--- PROJECT MEMORY ---`
- Backend: `GET/PUT /api/project/<pid>/memory` endpoints
- Storage: `data/memory/<project_id>.md` (one markdown file per project)
- Lazy-loaded on first tab visit, textarea with save button

### Skills system
- New **Skills tab** in project modals — manage project-scoped and attached global skills
- **Global Skills Manager** — header-level "Skills" button opens dedicated modal for managing global skills
- Skills are reusable prompt templates with name, description, and markdown content
- Skills injected into agent context as `--- SKILL: <name> ---` sections
- Two scopes: **project skills** (specific to one project) and **global skills** (shared, attachable to any project)
- Attach/detach global skills per project from the Skills tab
- Inline create/edit forms for both project and global skills
- Filter support in Skills tab via existing search bar
- Backend: Full CRUD for global skills (`/api/skills/global`), project skills (`/api/project/<pid>/skills`), and attach/detach endpoints
- Storage: `data/skills/global/*.json`, `data/skills/project/<pid>/*.json`, `data/skills/attachments.json`

### Context injection
- `_build_agent_context()` now includes project memory and resolved skills in agent system prompt
- Skills resolved per-project: all project-scoped skills + explicitly attached global skills

## [2026-03-15d] — Package as standalone Windows .exe

### Desktop mode (app.py)
- New `app.py` entry point: starts Flask in daemon thread, opens native pywebview window
- First-run creates `%APPDATA%\MissionControl\data\{projects,uploads}\` and `config.json`
- Auto-installs Claude CLI if missing (via npm, or winget→Node.js→npm fallback)
- Shows non-blocking alert in webview if CLI install fails (app still usable)
- Web interface remains accessible at `http://localhost:5199` while native window is open

### Dual-directory system (server.py)
- Replaced `BASE_DIR` with `_APP_DIR` (bundled assets) and `_DATA_ROOT` (user data)
- Frozen mode: `_APP_DIR = sys._MEIPASS`, `_DATA_ROOT = %APPDATA%\MissionControl`
- Dev mode: both point to repo root — fully backward-compatible
- `MC_DATA_DIR` env var overrides data root for custom deployments

### Build & packaging
- `build.spec` — PyInstaller `--onedir` spec (bundles server.py + static/index.html, console=False)
- `installer.iss` — Inno Setup script (per-user install, Start Menu + Desktop shortcuts, post-install launch)
- `build.bat` — Automated build: pip install deps → pyinstaller → prints Inno Setup instructions
- `requirements.txt` — Added `pywebview>=5.0`

## [2026-03-15c] — User-configurable modal header color

### Modal accent color
- Modal header left accent bar is now user-configurable per project (decoupled from status)
- "Change Color" submenu added to three-dot menu between "Change Status" and "Change Domain"
- Shows 6 color swatches (Blue, Purple, Green, Amber, Red, Gray) using existing `COLOR_PRESETS`
- Current color highlighted with thicker border
- Color saved as `modal_color: {color, bg}` on project JSON
- Default: Blue (`var(--accent)`) for projects without a chosen color
- CSS: Replaced 4 `.modal-header.status-*::before` rules with single `var(--modal-accent)` custom property
- Tile cards in grid also use chosen color via `--card-accent` inline override (falls back to status color)
- Status pill text in modal unchanged — still shows status with correct styling
- Function: `setProjectColor(projectId, color, bg)`

## [2026-03-15b] — Token tracking, live timer, enter key mode, UX refinements

### Three-dot modal menu
- Added three-dot menu button (vertical ellipsis) to project modal header controls
- Menu items: Change Status (Active/Waiting/Blocked/Parked submenu), Edit/Add Description, Delete Project
- Status submenu shows colored dots and highlights current status
- Delete Project is danger-styled with confirmation dialog
- Functions: `toggleModalMenu()`, `toggleModalMenuSub()`, `setProjectStatus()`, `editProjectDescription()`, `deleteProject()`
- CSS: `.modal-menu-btn`, `.modal-menu-dropdown`, `.modal-menu-item`, `.modal-menu-sep`, `.modal-menu-sub`, `.modal-menu-sub-item`, `.modal-menu-sub-dot`

### Token usage tracking
- Captures `usage`, `cost_usd`, `num_turns` from Claude CLI `result` message in `_read_agent_stream()`
- Persists usage data in agent log entries via `_log_agent_completion()`
- Exposes usage in `agent_status()` API and SSE completion messages
- Global token counter in header bar (lightning bolt badge) with total tokens + cost
- Per-session token/cost badge in Agent tab status row (appears on session completion)
- Token/cost inline in Agent Log entries (after timestamp)
- Helper functions: `formatTokens()` (1.2k/1.2M), `formatCost()`, `tokenBadgeHTML()`, `sessionMetricsHTML()`
- CSS: `.token-counter-global`, `.tc-icon`, `.tc-cost`, `.tc-mode`, `.token-badge`, `.agent-log-usage`

### Token counter time range selector
- Click the global token counter to switch between: All Time, Today, This Week, This Month
- Context menu with checkmark on active mode
- Mode persisted in `localStorage` (`tc_mode` key)
- Server: `/api/usage` endpoint accepts `?since=<ISO timestamp>` for time-filtered aggregation
- Functions: `getTokenSince()`, `fetchGlobalUsage()`, `openTokenContextMenu()`, `setTokenMode()`
- `TOKEN_MODES` constant; `tokenCounterMode` state variable

### Live elapsed timer for running sessions
- Running agent sessions show `⏱ 0s` → `⏱ 1m 23s` → `⏱ 1h 5m` ticking every second
- Transitions to token count + cost when session completes
- Functions: `formatElapsed()`, `sessionMetricsHTML()`
- 1-second `setInterval` updates all running session timer elements

### Enter key mode toggle
- Configurable send behavior: "Ctrl+Enter sends" (default) or "Enter sends" (Shift+Enter for newline)
- Accessible from three-dot modal menu → "Enter Key" submenu (shows current mode inline)
- Global setting persisted in `localStorage` (`enter_mode` key)
- Applied to all 4 textareas: agent dispatch, follow-up, agent log continue, backlog input
- Functions: `handleInputEnter()`, `setEnterMode()`
- Removed standalone right-click context menu — native right-click restored on textareas

### Project delete endpoint
- Server: `DELETE /api/project/<project_id>` — cleans up attachment files, agent log JSON, kills running agent sessions, deletes project file
- Frontend: `deleteProject()` calls API, closes modal, refreshes dashboard

### Bug fixes
- Fixed stale token count showing on follow-up dispatch (usage/cost cleared from cache when session resumes)
- Fixed `agent_session_delete` — stream reader thread handles completion logging, delete handler just removes from tracking

### Files Changed
- server.py: `_read_agent_stream()` usage capture, `_log_agent_completion()` usage persistence, `agent_status()` usage fields, SSE status message includes usage, `DELETE /api/project/<id>` endpoint, `GET /api/usage` endpoint with `?since=` filter, `agent_session_delete` logging fix
- static/index.html: Three-dot menu system, token counter with click-to-switch time range, live elapsed timer, enter key mode toggle, session metrics badge, context menu CSS/JS, all textarea onkeydown handlers unified

---

## [2026-03-15] — Domain management moved to three-dot menu and new project form

### Done
- Moved domain selection from clickable pill to three-dot menu "Change Domain" submenu
- Domain submenu shows all domains with colored dots, color picker swatches, and "New domain..." input
- Domain pill in modal header is now display-only (no longer clickable)
- Replaced `<select>` in new project form with rich domain picker matching the menu style
- New project domain picker includes domain list, color swatches, and new domain creation
- Removed old `toggleDomainDropdown()`, `saveDomain()`, `addDomainFromDropdown()`, `setDomainColor()` functions
- Added `saveDomainFromMenu()`, `addDomainFromMenu()`, `setDomainColorFromMenu()` for modal menu
- Added `toggleNewProjDomain()`, `selectNewProjDomain()`, `addNewProjDomainEntry()`, `setNewProjDomainColor()`, `refreshNewProjDomainTrigger()` for new project form
- `newProjDomain` state variable tracks selection; reset to `'general'` on form open and after creation
- Removed old CSS: `.domain-select-wrap`, `.domain-tag.editable`, `.domain-dropdown`, `.domain-dropdown-item`
- Added new CSS: `.new-proj-domain-wrap`, `.new-proj-domain-trigger`, `.new-proj-domain-dd`, `.new-proj-domain-item`

### Files Changed
- static/index.html: Domain submenu in three-dot menu, display-only pill, rich domain picker in new project form, replaced old domain CSS with new `.new-proj-domain-*` classes

---

## [2026-03-14] — Three-dot menu, token tracking, session resume, enter key mode, dynamic domains

### Three-dot modal menu (new)
- Built the three-dot menu system for project modals (button, dropdown, submenus)
- Menu items: Change Status (submenu), Change Domain (submenu), Agent Model (submenu), Edit/Add Description, Delete Project
- CSS: `.modal-menu-btn`, `.modal-menu-dropdown`, `.modal-menu-item`, `.modal-menu-sep`, `.modal-menu-sub`, `.modal-menu-sub-item`, `.modal-menu-sub-dot`
- Functions: `toggleModalMenu()`, `toggleModalMenuSub()`, `setProjectStatus()`, `editProjectDescription()`

### Token usage tracking (new)
- Global token counter in header showing input/output tokens and USD cost
- Right-click context menu to switch time range: All, Today, This Week, This Month
- Per-session token badge in agent status row (tokens + cost after completion)
- Token/cost display in agent log entries
- `tokenCounterMode` persisted in localStorage; `TOKEN_MODES` constant
- Functions: `formatTokens()`, `formatCost()`, `tokenBadgeHTML()`, `getTokenSince()`, `fetchGlobalUsage()`, `openTokenContextMenu()`, `setTokenMode()`, `formatElapsed()`, `sessionMetricsHTML()`
- CSS: `.token-counter-global`, `.tc-icon`, `.tc-cost`, `.tc-mode`, `.tc-context-menu`, `.token-badge`, `.agent-log-usage`
- Server: new `GET /api/usage` endpoint aggregates tokens/cost across all agent logs and running sessions (supports `?since=` filter)
- Server: `_read_agent_stream()` captures `usage`, `cost_usd`, `num_turns` from Claude result messages
- Server: `_log_agent_completion()` persists usage data; SSE status messages include usage; `agent_status()` exposes usage

### Session resume picker
- Session picker UI when opening Agent tab or clicking "+ New": radio buttons for prior sessions to resume
- Most recent session pre-selected by default; "Fresh session" available as explicit choice
- Deduplicated entries (follow-ups no longer show as separate entries)
- Dispatch button label changes to "Continue" when resuming; default task text becomes "Continue where we left off."
- `pendingResumeId` state; `getDefaultResumeId()`, `selectResumeSession()`, `sessionPickerHTML()` functions
- `agentHistory` entries store `resumedFrom` field; `dispatchAgent()` sends `resume_conversation_id`
- CSS: `.session-picker`, `.session-picker-opt`, `.resume-indicator`

### Per-project agent model
- Agent Model submenu in three-dot menu (Sonnet 4.5, Opus 4.6, Haiku 4.5, or global default)
- Per-project `agent_model` overrides global config for all dispatch/follow-up paths
- Server: `_build_claude_flags(project)` accepts per-project override; all 4 Popen call sites pass project

### Enter key mode toggle (new)
- Configurable send behavior: "Enter sends" vs "Ctrl+Enter sends" (default)
- Right-click context menu on all agent/backlog textareas to switch mode
- `enterKeyMode` persisted in localStorage; `handleInputEnter()`, `openInputContextMenu()`, `setEnterMode()` functions
- Applied to backlog input, agent task input, agent follow-up, agent log continue textareas

### Dynamic domain system (new)
- Domains fetched from server settings instead of hardcoded CSS classes
- Domain filter buttons dynamically rendered via `renderDomainFilters()`
- `domainsList` state; `fetchDomains()`, `getDomainConfig()`, `renderDomainFilters()` functions
- `COLOR_PRESETS` constant (Blue, Purple, Green, Amber, Red, Gray)
- Domain tags in tiles and modals use inline styles from `getDomainConfig()` instead of CSS classes
- Server: `SETTINGS_PATH` (`data/settings.json`), `DEFAULT_DOMAINS`, `_load_settings()`, `_save_settings()`
- Server endpoints: `GET /api/settings/domains`, `POST /api/settings/domains/add`, `PATCH /api/settings/domains/<id>`, `DELETE /api/settings/domains/<id>`

### Project delete
- Delete Project option in three-dot menu (danger-styled, with confirmation dialog)
- `deleteProject()` function calls `DELETE /api/project/{id}`, closes modal, refreshes
- Server: `DELETE /api/project/<id>` cleans up attachment files, agent log, kills running sessions, deletes project JSON

### Plan file label
- `planFileLabel()` generates a display label from task description (truncated, capitalized)
- `openPlanFileViewer()` extracts first markdown heading from plan content as viewer title

### Windows process window hiding
- `_POPEN_FLAGS` uses `CREATE_NO_WINDOW` (not `DETACHED_PROCESS`); `_STARTUPINFO` with `SW_HIDE`
- `_hide_process_windows()` uses ctypes to enumerate and hide windows by PID
- `_hide_windows_delayed()` runs in background thread, calling hide 6 times over ~2.5 seconds
- Background thread spawned after every Popen call (4 sites: dispatch, followup, auto-followup, and agent_followup)
- `stdin=subprocess.DEVNULL` added to all Popen calls

### Misc fixes
- Fixed agent image preview remove button not appearing on hover (CSS selector mismatch)
- Agent dispatch activity log now includes resume label
- 1-second interval timer updates elapsed time displays for running sessions

### Files Changed
- server.py: Three-dot menu backend (delete project, domain CRUD, usage endpoint), `_build_claude_flags(project)` per-project model, token/usage capture in stream reader and completion logger, `_POPEN_FLAGS`/`_STARTUPINFO`/`_hide_process_windows()`/`_hide_windows_delayed()`, `stdin=DEVNULL` on all Popen calls
- static/index.html: Three-dot menu system, token counter UI + context menu, session resume picker, enter key mode toggle, dynamic domain system, plan file labels, CSS for all new components

---

## [2026-03-13] — User and agent name settings

### Done
- Added `user_name` and `agent_name` to config.json defaults
- User name replaces hardcoded "Ron" in agent log lines (falls back to "User")
- Agent name and user name injected into agent system prompt context
- Added settings 7 (Your name) and 8 (Agent name) to both installer scripts
- Settings shown in post-install summary

### Files Changed
- server.py: New config defaults, replaced hardcoded "Ron" with `user_name`, inject names into `_build_agent_context()`
- install.bat: Added prompts 7-8, updated config.json writer and summary
- install.sh: Added prompts 7-8, updated config.json writer and summary

---

## [2026-03-13] — Open-source release preparation

### Done
- Replaced hardcoded user paths (`C:\Users\levir\...`) with `config.json` configuration system
- `config.json` auto-created on first run with sensible defaults (gitignored)
- Server port configurable via `config.json` or `MC_PORT` environment variable (default 5199)
- Set Flask `debug=False` for production
- Removed test injection function (`injectTestPlan`)
- Deleted personal/temporary files (helper scripts, session context, zip artifacts)
- Created `.gitignore`, `requirements.txt`, `LICENSE` (MIT), comprehensive `README.md`
- Created installer scripts: `install.bat` (Windows) and `install.sh` (macOS/Linux)
- Created launcher scripts: `start.bat` (Windows) and `start.sh` (macOS/Linux)
- Installers check prerequisites (Python, pip, Claude CLI), install dependencies, create data dirs
- Added `.gitkeep` files for `data/projects/` and `data/uploads/` directories

### Files Changed
- server.py: Replaced hardcoded `SHARED_RULES_PATH` and `PROJECTS_BASE` with config.json loader; port from config/env; `debug=False`
- static/index.html: Removed `injectTestPlan()` test function

### Files Added
- `.gitignore`, `requirements.txt`, `LICENSE`, `README.md`
- `install.bat`, `install.sh`, `start.bat`, `start.sh`
- `data/projects/.gitkeep`, `data/uploads/.gitkeep`

### Files Removed
- `fix_feed.py`, `patch_attachments.py`, `files.zip`, `frve.json`
- `patch_err.txt`, `patch_out.txt`, `.claude_session_context.md`, `SHARED_RULES_SNIPPET.md`

---

## [2026-03-13 16:30 ET] — Tab search/filter field

### Done
- Search input in the tab bar (right-aligned) for Backlog, Agent Log, and Activity tabs
- Live filtering on keystroke — hides non-matching items via DOM (no re-render)
- Searches backlog item text, agent log task+summary, activity log messages
- Per-project state persists across tab switches and auto-refreshes
- Clear (X) button appears when query is active
- Hidden on Agent tab (agent output is better served by different UX)

### How it works
- `modalSearchQuery[projectId]` stores the filter string per project
- `applyTabFilter()` reads query + active tab, shows/hides matching DOM elements
- Filter reapplied at end of `refreshModalById()` so it survives periodic re-renders
- Input focus and value preserved via extended textarea save/restore in refresh cycle

### Files Changed
- static/index.html: CSS `.modal-tab-search`, search input in tab bar template, `applyTabFilter()` / `clearTabSearch()` / `findModalIdForProject()` functions, `refreshModalById()` filter reapplication + input preservation

---

## [2026-03-13 16:15 ET] — Fix agent session hang on server restart

### Problem
When `server.py` was edited (triggering Flask's debug auto-reloader), the server process restarted and wiped all in-memory `agent_sessions`. Running agent sessions in the browser UI would freeze in a permanent "running" state because:
1. SSE connection broke → frontend retried indefinitely with no cap
2. Polling fallback silently skipped sessions not found on the server (`if (!ss) continue`)
3. No code path transitioned "running" → error when the server lost the session

### Fixes
- **Polling fallback** — when a session the frontend thinks is "running" is missing from the server entirely, mark it as `error` and refresh the UI (instead of silently skipping)
- **SSE reconnect retry cap** — max 3 retries with increasing delay (2s, 4s, 6s); after that, mark the session as errored and stop retrying
- **Retry counter cleanup** — `sseRetryCount[sessionId]` resets on successful data, and is deleted on normal completion or error

### Files Changed
- static/index.html: polling fallback (setInterval block), `connectAgentStream()` es.onerror/onmessage handlers, new `sseRetryCount` state variable

---

## [2026-03-13 16:00 ET] — Continue session from Agent Log

### Done
- "Continue" button on each Agent Log entry (when claude_session_id exists)
- Clicking expands an inline textarea to type a follow-up message
- Dispatches a new agent session that resumes the old conversation via `claude -r <id>`
- Automatically switches to Agent tab to show the running session
- Ctrl+Enter shortcut to send from the textarea

### How it works
- Backend `agent_dispatch()` accepts optional `resume_conversation_id` in POST body
- When present, builds `claude -r <id> -p <message>` instead of `claude -p <task>` (skips `--append-system-prompt` since resumed conversation already has context)
- Frontend `dispatchContinue()` mirrors `dispatchAgent()` but passes `resume_conversation_id` and switches tab

### Files Changed
- server.py: `agent_dispatch()` — read `resume_conversation_id`, conditional cmd build
- static/index.html: CSS for `.agent-log-continue-btn` and `.agent-log-continue-input`, updated `agentLogPanelHTML()` entries, new `toggleContinueInput()` and `dispatchContinue()` functions

---

## [2026-03-13 14:30 ET] — Plan file viewer button

### Done
- When an agent edits a `.md` file and then calls `ExitPlanMode`, a purple button with the filename appears in the agent status row
- Clicking the button opens the actual plan file content in a dedicated viewer modal (reads the `.md` file from disk)
- Separate from the "Pop Out" button which still shows the full conversation
- Button persists across page refreshes (plan_file stored in session status)

### How it works
- Server tracks the last `.md` file touched by Write/Edit tool calls during agent stream
- When `ExitPlanMode` is called, the tracked file path is stored as `plan_file` on the session
- New endpoint `GET /api/project/{pid}/agent/plan-file?session={sid}` reads and returns the file content
- Frontend detects the plan file both on live SSE (fetches status after ExitPlanMode) and on re-render (from cached status)

### Files Changed
- server.py: Track `.md` edits in `_read_agent_stream()`, new `/agent/plan-file` endpoint, `plan_file` in status response
- static/index.html: `openPlanFileViewer()` function, `.btn-plan-file` CSS, plan file button in status row, live detection on ExitPlanMode

---

## [2026-03-13 10:39 ET] — Ctrl+Scroll zoom on agent output

### Done
- Ctrl+Scroll over agent chat output areas zooms text in/out (8px–24px range, default 12px)
- Applies to both `.agent-output` and `.ac-session-output` elements
- Zoom level is per-modal — each window maintains its own independent zoom
- Zoom persists through content refreshes (SSE updates, tab switches, etc.)

### Files Changed
- static/index.html: Added `modalZoomLevels` state (per-modal), `wheel` event listener on `#modal-layer` with Ctrl detection, zoom reapply in `refreshModalById()`

---

## [2026-03-12 15:00 ET] — Plan Viewer window

### Done
- Agent plan output is now hidden from the chat window — replaced by a purple **"Show Plan"** button
- Clicking the button opens a dedicated **Plan Viewer** modal (1000px wide, 85vh tall) for easier reading
- Detection: when `[tool: ExitPlanMode]` appears in the stream, all preceding non-tool text lines are identified as the plan and collapsed
- Plan viewer renders with full rich formatting: markdown headers, tables, code blocks, lists
- **"Pop Out"** button always visible in the agent panel status row — opens any session's output in the wider viewer
- Works on page refresh: static HTML builder also detects and collapses plan content
- Plan viewer is draggable, minimizable, resizable — follows the same modal system as project windows

### Files Changed
- static/index.html: Added `.plan-viewer-content`, `.plan-show-btn`, `.plan-hidden-block`, `.btn-popout` CSS; added `planViewerContent` state; modified `appendAgentLine()` to detect `[tool: ExitPlanMode]`; new `collapseIntoPlanButton()` function; modified static output builder in `agentPanelHTML()` for refresh-safe plan detection; new `openPlanViewer()` function; added Pop Out button to agent status row

---

## [2026-03-12 14:00 ET] — Tabbed modal layout + auto-size name input

### Done
- Modal sections now organized into 4 tabs: **Backlog**, **Agent**, **Agent Log**, **Activity**
- Tab bar sits between the header/summary and scrollable content area
- Header (name, status, domain, path, description) and summary (current task, next action) stay always visible above tabs
- Each tab gets full scroll area — no more scrolling past unrelated sections
- Agent Log tab lazy-loads completed sessions on first click
- Rules panel stays inside Agent tab (collapsible)
- Activity log expanded from 6 to 20 entries
- Project name input auto-sizes to fit text content (removed `flex: 1`)
- More drag area in header since name input no longer stretches full-width
- Backlog count badge shown in tab bar
- Modal structure changed from single scroll to flex column (fixed header + tab bar, scrollable body)

### Files Changed
- static/index.html: Added `modalActiveTab` state, `switchModalTab()`, `autoSizeNameInput()` functions; new CSS for `.modal-tab-bar`, `.modal-tab`, `.modal-tab-content`, `.modal-scroll-body`, `.name-measure`; restructured `modalContentHTML()` return template; `.modal-content` now flex column with `overflow: hidden`; `.modal-header` no longer sticky (not needed — it's in non-scrolling region); simplified `agentLogPanelHTML()` (removed collapsible wrapper); updated `refreshModalById()`, `minimizeModal()`, `restoreModal()` for new scroll container

---

## [2026-03-12 13:15 ET] — Proper HTML table rendering for pipe-delimited tables

### Done
- Pipe-delimited markdown tables (`| col | col |`) now render as actual HTML `<table>` elements with proper column alignment
- Header rows detected via separator lines (`|---|---|`) and styled with blue text + bold weight
- Box-drawing tables (Unicode `┌─┬─┐`) still render as pre-formatted blocks with colored borders
- Sticky modal header: project name, status, domain, path all stay pinned at top when scrolling modal content
- Modal header has distinct background (`#1e2230`) to visually separate from content
- Minimize/close buttons moved inside the sticky header
- User prompts with `\n` wrapping (follow-ups) now correctly match prompt styling via `trim()`
- Queued follow-up detection fixed (check order was shadowed by general `> ` match)
- Page refresh no longer kills running agent processes (removed `sendBeacon` kill in `beforeunload`)

### Files Changed
- static/index.html: Replaced `formatTableLine` pre-rendering with `buildPipeTable()` HTML table parser; added `isPipeTable()`, `isSeparatorLine()` helpers; updated all 4 render paths; new `.hl-table table/th/td` CSS; `.hl-table-pre` for box-drawing fallback; sticky `.modal-header`; controls moved inside header; `agentLineCls` uses `trim()` and reordered checks; removed `sendBeacon` kill from `beforeunload`

---

## [2026-03-12 12:35 ET] — Fix agent chat resize direction

### Done
- Moved resize handle from top edge to bottom edge of agent chat box
- Flipped drag direction so dragging down = expand, dragging up = shrink (matches visual result)

### Files Changed
- static/index.html: Changed `.agent-chat-resize` from `top: -4px` to `bottom: -4px`; flipped `dy` calculation in mousemove handler

---

## [2026-03-12 12:30 ET] — ASCII table rendering in agent chat

### Done
- ASCII tables (pipe-delimited and Unicode box-drawing) now render in a styled block with preserved alignment
- Consecutive table lines are grouped into a single `<div class="hl-table">` with `white-space: pre` and `overflow-x: auto`
- Blank lines between table rows stay inside the table block instead of breaking it apart
- Pipes colored blue, border characters in slate gray for visual clarity
- Table lines skip `formatAgentText()` regex to prevent corruption (e.g., `-` as bullet, `*` as bold)
- Applied to all 4 render paths: modal live stream, console live stream, modal batch, console batch
- Added `overflow-x: hidden` and `min-width: 0` on `.agent-output` so wide tables scroll within their own `.hl-table` block instead of clipping
- Added `max-width: 100%` on `.hl-table` to constrain to parent and show horizontal scrollbar

### Files Changed
- static/index.html: Added `.hl-table` CSS for both `.agent-output` and `.ac-session-output`; added `isTableLine()` and `formatTableLine()` functions; updated `appendAgentLine()`, `updateConsoleOutput()`, and both batch renderers to group table lines; added overflow containment on `.agent-output`

---

## [2026-03-12 11:15 ET] — Resizable agent chat panel

### Done
- Agent chat area (`.agent-chat`) now has a draggable resize handle at its bottom edge
- Drag downward to expand, upward to shrink (min 120px, max 80vh)
- Handle shows a subtle bar indicator that highlights blue on hover

### Files Changed
- static/index.html: Changed `.agent-chat` from `max-height: 450px` to `height: 450px` with `min-height`/`max-height`; added `.agent-chat-resize` handle element + CSS; added `chatResize` mousedown/mousemove/mouseup logic

---

## [2026-03-12 11:00 ET] — Multi-modal windows with minimize

### Done
- Converted single-overlay modal to floating window manager: multiple project modals can be open simultaneously
- Each modal top bar now has minimize (horizontal bar) + close (X) buttons
- Minimize collapses modal to a chip in a bottom tray; click chip to restore, chip X to close
- Focus management: clicking a modal brings it to front (accent border), ESC closes only the focused modal
- Modals cascade-offset (+30px) when opened so they don't stack directly on top of each other
- Grid remains visible and scrollable underneath open modals (no blocking overlay)
- Drag-to-move and resize preserved per-modal
- Shared Rules editor and New Project form also participate in the multi-modal system
- All existing features preserved: agent panels, editable fields, textarea value preservation across refresh

### Files Changed
- static/index.html: Replaced `.modal-overlay` with `.modal-layer` + `.modal-window` system; added `.minimized-tray` and `.minimized-chip` CSS; new state (`openModals` Map, `focusedModalId`, `nextModalZ`); new functions (`openProjectModal`, `closeModalById`, `minimizeModal`, `restoreModal`, `focusModal`, `refreshModalById`, `centerModalElement`); updated drag handler for multi-modal delegation; converted `openSharedRulesEditor` and `openNewProjectForm`

---

## [2026-03-11 20:30 ET] — Agent log: Claude session ID tracking

### Done
- Capture real Claude CLI session UUID from stream-json `init`/`result` messages
- Persist `claude_session_id` in agent log entries and agent status API
- Display session ID in agent log UI with `claude -r <uuid>` hint and copy button
- Feed last 5 agent sessions (with resume IDs) into agent context prompt for continuity

### Files Changed
- server.py: `_read_agent_stream` (capture UUID), `_log_agent_completion` (persist), `agent_status` (expose), `_build_agent_context` (include in prompt)
- static/index.html: CSS for `.agent-log-session-id`, agent log entry template updated

---

## [2026-03-11 20:20 ET] — Project changelog created

### Done
- Created CHANGELOG.md for Mission Control project

### State
- Mission Control is a Tauri v2 desktop app with a Flask (Python) backend on port 5199
- Single-page dashboard (static/index.html) with dark theme, Inter/JetBrains Mono fonts
- Backend features: project CRUD, backlog management, file attachments, agent dispatch via Claude CLI, SSE streaming, follow-up/stop, agent log, project import from CHANGELOG.md, rules editor (AGENT_RULES.md + SHARED_RULES.md), project reordering
- Data stored as JSON files in data/projects/, uploads in data/uploads/

### Next
- Multi-session agent tabs, agent log, image paste, project import (current task per system context)

### Files Changed
- CHANGELOG.md (created)
