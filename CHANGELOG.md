# Clayrune — Changelog

> Renamed from "Mission Control" 2026-05-01. Backend identifiers (`mc_remote`,
> `MC_*` env vars, repo name, Cloud Run service, keystore namespace) intentionally
> remain "mission-control" to avoid breaking existing installs.

## [2026-05-15] — Activity feed redesign, focus-theft fix, AskUserQuestion status wiring, mobile missing-prompt reconciliation

A single-session bundle: one feature reshape + three correctness fixes.
All changes are `static/index.html` only — no `server.py` changes, so a
hard refresh picks everything up (no restart).

**Activity feed redesign — bucketed + time-aware (`static/index.html`)**
The feed was a flat, equal-weight reverse-chron list — nothing rose to
the top, so it carried no signal. `renderFeed()` now splits into two
buckets. **"Needs you"** is derived live from project state (not the
lagging `activity_log`): `friendlyStatus(p)` of `asking` or `stuck`,
with the reason resolved from plan-approval / question-pending / blocked
/ error. It renders with an accent rail and pins to the top, and the
collapsed feed tab carries an attention-count badge so urgency survives
a hidden feed. **"Recent"** collapses to one rolling line per project
(newest event + `+N earlier`) and is time-bucketed by the age of that
newest event: `Fresh · last hour` / `Today` / `This week`, with
progressive CSS opacity fade and a 7-day cutoff (the feed is a
"what's alive" surface; Agent Log remains the archive). New helpers:
`classifyFeedEvent` (msg-text → icon/kind), `_buildAttentionList`,
`_feedAgeBucket`, `_updateFeedAttentionBadge`.

**Focus-theft regression — fixed (`static/index.html`)**
Regression introduced in `e473323` (the Android-IME chat-input
preservation). `refreshModalById` detaches the focused
`agent-followup-${sid}` textarea and reattaches it across the
`innerHTML` wipe; removing a node from the DOM blurs it, and the
focus-restore block deliberately *skipped* the preserved input
(re-assigning `.value`/selection would desync the IME). Net effect:
any cross-modal `refreshModal()` — e.g. an SSE `turn_start` from a
different agent — silently blurred whatever textarea the user was
typing in. Fix: the restore block now re-focuses the preserved input
when it isn't already `document.activeElement`, **without** touching
`.selectionStart/.selectionEnd` (the reattached node still carries the
correct selection + IME compose buffer).

**AskUserQuestion status pipeline (`static/index.html`)**
`waiting_for_question` was fully tracked server-side and exposed on
`/agent/status`, but the frontend never propagated it, so an agent
blocking on `AskUserQuestion` showed as `working`/`idle` instead of
`asking` — tiles and the new Needs-you bucket couldn't surface it.
Wired the full chain: `fetchAgentStatus` now reads
`s.waiting_for_question` into `agentStatusCache[sid].waitingForQuestion`;
`computeLiveStatus` emits a new `currentTaskClass = 'question'`;
`friendlyStatus` maps `'question'` → `'asking'`. The flag is set on
the SSE `question` event (with a `refreshModal()` so tiles repaint)
and cleared on `submitQuestionAnswer`, `turn_start`, and the terminal
`turn_complete`/`status` handlers (alongside the existing
`waitingForPlanApproval` clears).

**Mobile missing-prompt — latent bug + silent reconciliation (`static/index.html`)**
Symptom: a follow-up sent from the mobile shell never appeared in the
chat even though the server received it and the agent replied
(confirmed against a live session's `log_lines`). Two fixes. (1)
Latent bug: `fetchAgentStatus` populated `agentOutputBuffers[sid]`
from server `log_lines` but never set `agentServerLines[sid]`, so a
later `connectAgentStream` used `since=0` and replayed every line on
top of the populated buffer — silent double-render. It now anchors
`agentServerLines[sid] = log_lines.length`. (2) New
`_reconcileAgentBuffer(projectId, sessionId)`: fetches `/agent/status`,
diffs server `log_lines.length` against `agentServerLines[sid]`, and
silently appends any missed tail entries through the normal
echo-dedup + `appendAgentLine` path. Per-session `_reconcileBusy`
lock + an in-loop race guard prevent double-apply if SSE catches up
mid-iteration. Triggered at the three moments a hole is most likely
to have just opened: `sendFollowup`'s POST resolution (+1.5 s), the
SSE watchdog reconnect (+1.5 s), and `visibilitychange → visible`
(fan-out over every visible modal's active session — covers mobile
backgrounding the tab and killing the EventSource without a close
event). No console/toast/flash — the recovered line appears as if
SSE delivered it.

**Rollback**: revert this commit. No persisted state or schema
changed; the feed/status changes are pure render-path, and the
reconciliation is additive (best-effort, silent on error).

## [2026-05-14b] — Modal snap layouts, tile-all button, pin/unpin, AskUserQuestion + mobile SSE fixes, Clayrune onboarding project

A single-session bundle of three usability issues + two larger features.

**AskUserQuestion render reliability (`server.py`, `static/index.html`)**
The question form was getting dropped on first ask if the SSE wasn't open, the
DOM wasn't ready, or the modal hadn't been built yet. Server now stamps each
`AskUserQuestion` with a `question_id` (uuid) and **keeps `pending_questions`
populated until the user actually answers** (cleared in `/agent/followup` +
`/agent/interrupt`). The SSE generator dedupes per-stream by `question_id` so
the 0.3 s poll doesn't spam. `/agent/status` now exposes `waiting_for_question`.
Client tracks rendered question_ids in `_renderedQuestionIds[sessionId]` and
skips re-rendering an already-shown form. Cleared on submit. `fetchAgentStatus`
also reconnects SSE for idle sessions that are either waiting on a question
or are the active tab in an open modal (still skips background idle sessions
to preserve Chromium's 6-slot per-origin cap).

**Mobile modal status stuck on IDLE (`static/index.html`)**
After a send on mobile, the modal would sit on IDLE because: (a) SSE wasn't
auto-reconnected for idle sessions on cold modal open, and (b) the post-POST
reconnect could miss `turn_start` if the server flipped through `running` →
`idle` before the new SSE opened. Fix: `sendFollowup` now eagerly opens SSE
**before** the POST, plus a `_sendInFlight[sessionId]` gate so the eager-open
SSE's stale `turn_complete` (reflecting the *prior* idle state) doesn't close
the connection on the client. Gate clears on `turn_start` or after 8 s timeout.
The `status` handler honors the gate too (except for user-initiated `stopped`).

**Android IME backspace requiring many taps (`static/index.html`)**
`refreshModalById`'s `innerHTML` wipe was destroying the focused chat
textarea's IME compose buffer, causing the next backspace to need several
presses (the IME thought the word was still in compose; the rebuilt DOM had
no such buffer). `refreshModalById` now detaches the focused
`agent-followup-${sid}` textarea before the wipe and reattaches it after —
same pattern already used for `agent-output`. The value/focus restoration
loop skips the preserved input so we don't overwrite its `.value` (which
would reset cursor + re-trigger the desync).

**Aero-Snap for modal windows (`static/index.html`)**
Drag a `.modal-window` toward a viewport edge → translucent accent preview
shows the target zone → release commits the snap. Dragging a snapped modal
more than 24 px tears it off back to its pre-snap geometry. Zones: full
(top edge), left-half, right-half, four corner quarters. Detection uses
viewport edges (cursor must reach the screen edge, not the workspace edge —
the natural crossing into the sidebar / header strip would otherwise kill
detection). Snap target uses the workspace rect with the sidebar pinned to
its collapsed width so hover-expand doesn't shift zones mid-drag. State
persists in `mc_modal_prefs.snap` + `mc_modal_prefs.preSnap` and per-instance
in the `mc_open_modals` snapshot, so reload restores the layout. Window
resize re-applies all current snaps debounced 100 ms. Mobile is full-screen
by CSS; the snap engine no-ops there.

**Header "Tile open modals" button (`static/index.html`)**
Small grid icon in the header (next to the system-status pill) opens a
popover with layout templates filtered to the current visible-modal count:
1 → maximize · 2 → side-by-side or top/bottom · 3 → three columns or
large-left+stack or stack+large-right · 4 → 2×2 quadrants · 5+ → "no
layout available." Thumbnails are numbered cells (1, 2, 3…) showing which
slot each modal will take — assignment is by zIndex descending (focused
modal → cell 1). Each cell calls the existing `applySnap`, so persistence,
the `is-snapped` class, and the resize-grip lockout all carry over. New
zone types added to `_zoneRect`: `top-half`, `bottom-half`,
`left-third`, `center-third`, `right-third`.

**Per-modal pin / unpin (`static/index.html`)**
New pin button in `.modal-window-controls` (between menu and minimize).
Unpinned collapses the middle data-sheet section: status pill row, path
row, summary, description, and the Current task / Next up grid. The
project name row at top, the window controls, **the tab bar, and the
active tab's content** all stay — handy when tiled modals only need a
title bar + the conversation visible. State persists in
`mc_modal_prefs.unpinned` + the `mc_open_modals` snapshot.

**Clayrune onboarding project replaces "Sample Project" (`server.py`)**
First-run walkthrough now seeds a real `clayrune` project at
`~/MissionControl/clayrune/`. Endpoint URL stays
`/api/walkthrough/sample-project` for compatibility; project ID is now
`clayrune`. Seeded files (only if absent — won't trample edits): a friendly
`README.md` and an `AGENT_RULES.md` that primes the dispatched agent as the
in-app help desk, with absolute paths to *this install's*
`docs/USER_GUIDE.md`, `CHANGELOG.md`, and source root (resolved via
`Path(__file__).parent`). `_build_agent_context` already reads
`AGENT_RULES.md`, so every session dispatched from Clayrune behaves as a
platform expert with no schema or dispatch-flow change. 11 backlog items
cover drag-snap, tile button, pin button, scheduler, hivemind, skills,
MCP, GitHub sync, compact mode, first-real-project, and the
tour-the-agent prompt.

**Deferred**: stale "running" status detection — the server's guardian
only fires after 600 s of stdout silence *and* CPU idle, so a wedged agent
shows "running" for up to 10 minutes. Will be its own session.

**Rollback**: revert this commit. The pre-existing `sample-project.json`
in older installs is untouched (the new endpoint creates `clayrune.json`
alongside if the user re-runs the walkthrough).

## [2026-05-14] — Native FCM push for the Clayrune Android APK shell

Web push hit a wall on Android Chrome: every notification carried a
"possible spam from clayrune.io" warning and click-through landed on the
generic dashboard, not the specific agent. With the native APK shell now
shipping (CF service-token bypass landed yesterday), the right path is
Firebase Cloud Messaging through Capacitor's `push-notifications` plugin —
no spam toast, proper deep-link routing, and the server can deliver to a
killed app via the OS push channel.

**Server (`server.py`):**
- `_push_send_fcm(sub, payload)` — lazy-inits `firebase_admin` from
  `data/firebase_admin.json` (gitignored), sends via
  `messaging.send(messaging.Message(token=…, notification=…, data=…))`.
  Hybrid `notification`+`data` payload: Android auto-renders in the tray
  when the app is backgrounded; the `data` block carries `project_id` /
  `session_id` / `url` so taps route deep. `AndroidConfig` adds
  `priority=high` + `ttl=300` + a per-project notification `tag` so a
  chatty agent doesn't carpet-bomb the tray.
- `_notify_push()` now dispatches per-subscription. `sub.type == 'fcm'`
  → FCM path (handles `NotFoundError` / `InvalidArgumentError` as
  "drop this token"); everything else stays on the existing pywebpush
  path. Lock + persistence + the auto-removal-of-stale-subs accounting
  is shared, so a mixed fleet of browser PWAs and native APKs all
  funnel through one delivery loop.
- New endpoint **`POST /api/push/register-fcm`** — accepts
  `{token, label?, project_filter?, notify_agent_push?,
  notify_turn_complete?}`. Storage key prefers the CF Access nonce; if
  absent, falls back to `fcm:<sha1(token)[:16]>` so the row survives a
  CF re-OTP. Dedups by token across keys (same logic as web
  endpoint-based dedup).
- **`POST /api/push/unsubscribe`** extended with a `token` field — same
  pattern as the existing `endpoint` field but matches FCM rows.
- **`GET /api/push/subscriptions`** now surfaces `type` (`'web'` or
  `'fcm'`) per row so the Settings UI can label them distinctly.
- `requirements.txt` adds `firebase-admin>=6.5.0`. Import is lazy
  inside `_fcm_initialize()` — if the SDK isn't installed or the
  service-account JSON is missing, FCM delivery silently no-ops and
  the web push path still works.

**Mobile (`E:\clayrune-mobile`, separate repo):**
- `android/app/google-services.json` (gitignored) drops in to wire
  the existing `apply plugin: com.google.gms.google-services` line
  that Capacitor's template already had.
- `AndroidManifest.xml` adds `POST_NOTIFICATIONS` (Android 13+
  runtime grant — Capacitor's plugin prompts on first `register()`)
  and `WAKE_LOCK` (brief wake to render incoming pushes when screen
  is off).
- No Java changes needed — Capacitor's `@capacitor/push-notifications`
  plugin ships its own `FirebaseMessagingService` subclass; FCM
  payloads route through that and into the JS bridge.

**Dashboard JS (`static/index.html`):**
- New top-level `_initNativePush()` block right after the service
  worker registration. Runs only when `Capacitor.isNativePlatform()`
  reports true — web/PWA browsers never see this code path.
- Requests `POST_NOTIFICATIONS` via the plugin's `requestPermissions()`,
  registers, listens for `registration` → POSTs the FCM token to
  `/api/push/register-fcm`.
- Wires the plugin's three events:
  - `pushNotificationReceived` (foreground delivery — FCM suppresses
    the system tray when the app is open) → `showToast(title: body)`
    so the user notices without a duplicate-looking system bubble.
  - `pushNotificationActionPerformed` (tap from tray) → reads
    `notification.data.url` (or rebuilds it from
    `project_id`/`session_id`) and hands off to the existing
    `_handleDeepLinkFromUrl()` helper — same one the service-worker
    `mc-deeplink` postMessage already calls, so behavior is
    identical to web push tap-through.
  - `registrationError` → logs to `_pushState.native.error` for
    Settings-panel diagnostics.

**Verified end-to-end 2026-05-14:**
1. APK installed → CF pre-auth Toast → Android 13 permission prompt → grant.
2. JS posts token → `data/push_subscriptions.json` gets a row with
   `type:'fcm'`, label `'Android'`.
3. `POST /api/push/test` → `{sent:1, failed:0}`.
4. App foreground: in-app toast renders via `pushNotificationReceived`.
5. App backgrounded: system tray notification renders; tap opens the app.
6. (Deep-link routing with real `project_id`/`session_id` deferred to
   the first agent-emitted `PushNotification` tool call in the wild.)

**Rollback recipe:**
- Server: revert the four hunks in `server.py` (`_push_send_fcm`,
  `_notify_push` dispatch, `/api/push/register-fcm`, `/api/push/unsubscribe`
  token branch, `/api/push/subscriptions` type field). `requirements.txt`
  can keep `firebase-admin` (harmless if unused) or drop it.
- Mobile: revert AndroidManifest permissions + delete
  `android/app/google-services.json` to short-circuit the
  `com.google.gms.google-services` plugin apply (it's wrapped in a try
  block that no-ops on missing JSON).
- Dashboard: delete the `_initNativePush()` IIFE from `static/index.html`.
- `data/firebase_admin.json` stays gitignored; can be deleted from disk
  without breaking anything (web push keeps working).

**Open follow-ups:**
- Settings UI: add a "Send test" row that targets a specific subscription
  by nonce (currently `/api/push/test` fans out to everyone matching).
- Optional `@capacitor/device` install to get a real model label
  ("Galaxy Z Fold7") instead of the `'Android'` fallback.
- iOS APK shell (Capacitor supports it; needs Apple developer cert).
- Cleanup of the now-redundant `pywebpush` path on Android once the APK
  is everyone's primary surface — keep it for desktop browsers and iOS PWA.

## [2026-05-13b] — MCP servers management surface

Users asked to add MCP (Model Context Protocol) servers from the dashboard
instead of hand-editing `~/.claude.json` / `.mcp.json`. Built on the same
pattern as the Skills surface — MC manages the files, Claude Code reads them
natively at next session start (no preamble injection, no restart of CC
required for newly-added servers).

- **`mcp.py`** new module. `list_servers` / `read_server` / `write_server` /
  `delete_server`. Three transport types validated:
  - `stdio` → `{command, args?, env?}` (defaults; no `type` key, since stdio
    is CC's default)
  - `http`  → `{type: "http", url, headers?}` (streamable HTTP — most
    modern hosted MCP servers)
  - `sse`   → `{type: "sse",  url, headers?}` (legacy HTTP+SSE — still
    common in the wild)
- **Atomic writes** via `tempfile.mkstemp` + `os.replace`. `~/.claude.json`
  is owned by Claude Code and holds lots of unrelated state — we
  read-modify-write under a single `_global_write_lock` and never truncate
  other top-level keys. Project `.mcp.json` files use the same lock for
  simplicity.
- **Server endpoints** in `server.py` between the Skills block and the
  Global config block (`# ── MCP server endpoints`):
  - `GET    /api/mcp?project_id=…`       — list (with `shadowed_by_project`
    flag if a project entry overrides a global of the same name)
  - `GET    /api/mcp/<scope>/<name>`     — read one
  - `POST   /api/mcp`                    — create (409 on duplicate)
  - `PUT    /api/mcp/<scope>/<name>`     — update (always overwrite)
  - `DELETE /api/mcp/<scope>/<name>`     — remove
- **Frontend** (`static/index.html`, section comment
  `// ── MCP servers (global + per-project Model Context Protocol manager)`):
  - Sidebar entry "MCP" (🔌) directly below Skills, wired through
    `sidebarNav('mcp') → openAllMCP()`.
  - Per-project menu entry "MCP servers" in the three-dot dropdown, calls
    `openAllMCPForProject(pid)`.
  - List modal mirrors the Skills shell — scope filter, project filter,
    free-text search, scope/transport badges, shadow badge.
  - Editor modal with a transport `<select>` that swaps the field set
    between stdio (command/args/env) and http/sse (url/headers). Env vars
    and headers entered as one-per-line key=value / Key: value text.
- **Name rule**: `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$` — looser than Skills
  (which is strict kebab) because real-world MCP names use dots and
  underscores (e.g. `mcp.local.dev`, `github_actions`).
- **What v1 does NOT do**: connection test (spawn the stdio server / hit
  the URL to verify), OAuth helper flow, marketplace browser, mass-import
  from a paste. All deferred until users hit real friction.
- **Restart needed**: changes only take effect after restarting the Flask
  process (new module import + new routes).
- **Rollback**: delete `mcp.py`; remove `import mcp as _mcp` from
  `server.py` line 18; revert the `# ── MCP server endpoints` block in
  `server.py`; in `static/index.html` remove the sidebar `data-nav="mcp"`
  entry, the `else if (target === 'mcp')` line in `sidebarNav`, the
  `openAllMCPForProject` menu item in the project three-dot menu, and the
  whole `// ── MCP servers …` JS block.

## [2026-05-13b] — MCP "Add from URL" with security pre-flight

New install path for non-technical users: paste a GitHub repo URL (or npm
package name, or raw JSON config URL), MC does the rest. Bolted onto the
existing MCP editor as a mode toggle, no new modal.

- **Backend module** `mcp_installer.py` (new, ~520 lines):
  - `classify_url()` — accepts `github.com/x/y[/tree/<ref>]`, `npmjs.com/...`,
    bare `@scope/name`, raw `.json` URLs. Unknown is a valid kind.
  - `fetch_github_signals()` — stars, age, last-commit recency, license,
    archived flag, default branch. Uses `GITHUB_TOKEN` / `GH_TOKEN` if present.
  - `stage_clone()` — shallow `git clone --depth 1` into
    `~/.clayrune/mcp_installs/<owner>-<repo>/`, pins to the resolved SHA,
    drops a `.meta.json` with `{url, sha, staged_at}`.
  - `extract_config()` — three-tier fallback: (1) committed example files
    (`claude_desktop_config.json`, `mcp.json`, `examples/*.json`), (2) regex
    the README for the first ```json fence containing `mcpServers`, (3) one
    Claude call with the README as input + structured extraction prompt.
    Tier 3 only fires when 1 and 2 miss.
  - `_absolutize_paths()` — replaces `/path/to/<repo>`-style placeholders in
    the extracted config's `args` / `command` with the real install dir so
    the resulting `~/.claude.json` entry Just Works.
  - `detect_secrets()` — placeholder regex (`your-api-key`, `paste-here`,
    etc.) + heuristic on env var names ending in `api_key` / `token` /
    `secret` / `password` to surface required user input.
  - `dependency_audit()` — `npm audit --json` (generates a lockfile via
    `--package-lock-only --ignore-scripts` if missing) or `pip-audit -f
    json`, returns critical/high/moderate/low counts + top 20 findings.
  - `security_scan()` — gathers up to 20 KB of source from the repo
    (excludes `node_modules`, `.git`, `dist`, build dirs), sends to Claude
    with a structured prompt asking for a 4-row table:
    Network / Filesystem / Shell / Secrets + a free-form `flags` list of
    anything that doesn't match the README's claimed purpose. Cached on
    `<install_dir>@<sha>` so re-previewing the same commit is free.
  - `install_commands()` / `stream_install()` — runs `npm install
    --no-audit --no-fund` or `uv sync` / `pip install -e .` /
    `pip install -r requirements.txt`, streamed via a callback.
- **Backend endpoints** (`server.py`, between the existing MCP DELETE and
  Global config sections):
  - `POST /api/mcp/url/preview` — runs the whole preview pipeline (classify
    → clone → extract → audit → scan), returns one JSON blob the frontend
    renders. Does NOT install.
  - `POST /api/mcp/url/install` — SSE stream that runs the install commands
    line-by-line then writes the final config via the existing
    `mcp.write_server()`.
  - `DELETE /api/mcp/url/staged` — cleans up the staged clone if the user
    cancels after preview. Defense in depth: rejects paths outside
    `~/.clayrune/mcp_installs/`.
- **Frontend** (`static/index.html`):
  - `openMCPEditor` gets a **Manual / From URL** mode toggle at the top of
    the modal (only shown for new servers; editing always uses manual).
  - URL mode state machine: **input** (URL field + Preview button) →
    **preview** (GitHub trust row + audit banner + security-scan table +
    secrets form + commands to run + final config preview + name/scope/
    project pickers) → **installing** (live SSE log streamed into a `<pre>`)
    → **done** (success card + collapsible install log).
  - Required-secret check before Install button fires.
  - Back button cleans up the staged clone server-side.

**Smoke test:** paste `https://github.com/tradesdontlie/tradingview-mcp` →
Preview → MC clones (~3s) + extracts the README JSON block (tier 2) +
runs `npm audit` + Claude scans the source (~5s) + shows you what's about
to run. Click Install → live `npm install` output → "Done." card.

**Restart needed:** the new module + endpoints only load after a Flask
restart.

**Rollback:** delete `mcp_installer.py`, remove the `import mcp_installer
as _mcpinst` line and the three `/api/mcp/url/*` route handlers, delete
the `_mcpEditorSetMode` / `_mcpUrl*` block in `index.html`, revert the
`openMCPEditor` mode-toggle change.

## [2026-05-13] — In-dashboard Claude auth surface

Ron hit a 401 from the dashboard this morning — the `claude` CLI had no
valid credentials and there was no UI hint, just silent failure. Added
detect-and-launch auth recovery:

- **Server-side sentinel scan** (`server.py`, just above the agent-endpoints
  section): every line read by `_read_agent_stream` (Mode A) and
  `_read_agent_stream_b` (Mode B) is run through `_scan_for_auth_error()`,
  which matches "Please run /login", "not logged in", "Invalid (api) key",
  and `authentication_error`. A hit calls `_mark_claude_auth_error(reason,
  line)` which flips a global `_claude_auth_state` dict (lock-guarded).
  (The "credit balance is too low" sentinel was removed — Clayrune users sign
  in via subscription, not API billing, so that warning was always a false
  positive coming from stray API-style errors.)
- **`GET /api/claude/auth-status`** — returns the dict. Cheap, no subprocess.
- **`POST /api/claude/auth-probe`** — actively runs
  `claude -p ok --max-turns 1` (20s timeout) and updates the dict from the
  combined stdout+stderr. Costs a few tokens when authed; only invoked when
  the user clicks "Re-check".
- **`POST /api/claude/login-launch`** — opens `claude` in a NEW OS-level
  terminal window. Why not the existing in-app terminal pop-out? That pop-out
  uses `subprocess.Popen` with `stdin=PIPE` (not a PTY), and claude's `/login`
  slash command refuses without a real TTY ("/login isn't available in this
  environment"). Windows: `start "" cmd /k claude`. macOS: AppleScript to
  Terminal.app. Linux: tries `x-terminal-emulator` / `gnome-terminal` /
  `konsole` / `xfce4-terminal` / `xterm` in that order.
- **Frontend banner** (`static/index.html`, sibling of `schedule-banner`):
  warm-orange bar above the project grid. Two buttons:
  - **Authenticate Claude** → `POST /api/claude/login-launch`, then a toast
    instructing the user to type `/login` in the new window and click Re-check
    when done.
  - **Re-check** → `POST /api/claude/auth-probe`, banner hides on success.
  - **No credit** variant swaps the primary button to "Open Billing"
    (`console.anthropic.com/settings/billing`).
- **Settings → Claude Code Integration → "Sign in to Claude"**: explicit
  Sign in + Check status buttons. Always visible regardless of whether the
  auto-banner detection fires — this is the dependable escape hatch when the
  agent-stream sentinel scan misses the actual error format claude printed.
- Polls `/api/claude/auth-status` on dashboard load, every 90s, and after
  every agent SSE `error` event so a fresh 401 surfaces within seconds.

Restart needed: changes only take effect after restarting the Flask process.

Rollback recipe: revert the `# ── Claude CLI auth-status tracking` block in
`server.py`, the two new routes, the two `_scan_for_auth_error` calls in the
stream readers; drop the `auth-banner` HTML + CSS + JS block in
`index.html`; remove the `refreshAuthStatus()` calls (initial-load chain,
`setInterval`, and SSE-error branch).

## [2026-05-11c] — Single-instance guard for browser tabs

Follow-up to the launch_handler fix in `[2026-05-11b]`. On a fresh
Windows install where the user pinned `localhost:5199` (or
`clayrune.io`) to the Start menu **as a browser shortcut** — i.e. they
hadn't actually installed the PWA — each click of the icon spawned a
new browser tab. `launch_handler` in `manifest.json` doesn't help here
because the PWA isn't installed; the click is just Chrome opening a
URL bookmark.

Added a `BroadcastChannel`-based single-instance guard at the top of
`static/index.html <head>`:

- New tab announces itself with a timestamped instance ID.
- The existing primary tab acks and calls `window.focus()` to pull
  itself forward.
- The newcomer tab, on receiving the ack, replaces its UI with a
  "Clayrune is already open" panel + "Close this tab" button, then
  attempts `window.close()` after 1.5s (works for fresh tabs that
  have no history entry).
- Tiebreaker by ID timestamp handles the case where the user double-
  clicks the Start menu icon and two tabs race to claim primary.
- Skipped when `display-mode: standalone` matches — installed PWAs use
  `launch_handler` instead, and we don't want to interfere with their
  deep-link navigation.

`index.html` is already served with `Cache-Control: no-cache` + mtime
ETag (`server.py:8331`), so a normal browser refresh on the other
install will revalidate and pick up the new script — no hard refresh
needed.

Known gap: if a user *wants* multiple Clayrune tabs open intentionally
(e.g. side-by-side comparison), this guard fights them. Acceptable for
now since the dashboard isn't designed for split-view workflows; if it
ever becomes painful, expose a `localStorage.clayrune_allow_multitab`
flag.

Rollback: remove the `<script>` block in `static/index.html` between
"Single-instance guard" and the closing `</script>` just below the
apple-mobile-web-app-title meta.

## [2026-05-11b] — PWA shell + deep linking + push-sub dedup

Follow-up to `[2026-05-11]` after live testing on Android Chrome. Three
specific problems came up:

1. Notifications were stamped **"Possible spam from clayrune.io"** by
   Chrome — its heuristic for low-traffic web-push origins.
2. Tapping a notification landed the user at the dashboard root, not at
   the project + session that fired the push.
3. CF Access re-OTP (new nonce every ~24h) would orphan the push
   subscription, gradually accumulating duplicates.

All three addressed in this change. The mechanical web-push pipeline
itself was already correct (see `[2026-05-11]`).

### PWA shell (kills the "Possible spam" warning)

- New `static/manifest.json` — name/short_name "Clayrune", `display:
  standalone`, theme/background colors, 192 + 512 PNG icons (also one
  `maskable` variant). `start_url: /`.
- New PNG icons rendered via Pillow: `static/icon-192.png`,
  `icon-512.png`, `icon-badge-72.png`. Orange rounded square + white "C"
  (matches existing inline-SVG favicon). Generated once with the script
  in CHANGELOG `[2026-05-11]`; living source kept in
  `static/icon.svg` if the brand evolves.
- `index.html <head>` now links the manifest + Apple touch icon meta +
  apple-mobile-web-app-* meta for iOS A2HS parity.
- Service worker (`static/sw.js`) — uses the new PNGs for `icon` and
  `badge` instead of the previous 404s. Notifications shown from inside
  an installed PWA are credited to **Clayrune** (the app), not to the
  website, so Chrome's spam classifier doesn't trigger.
- Settings → Push Notifications: new install-state row.
  - Installed → green checkmark + explanation.
  - `beforeinstallprompt` captured → "Install" button that triggers
    Chrome's native install flow. Listener also re-renders the section
    on `appinstalled` so the row flips to "installed" state.
  - Neither yet → hint pointing the user at Chrome's menu → Install app.

### Deep linking from notification clicks

- `_handleDeepLinkFromUrl(url)` parses `?project=X&session=Y`, calls
  `openProjectModal(X)`, waits one paint, then `switchAgentTab(X, Y)`.
  Cleans the URL with `history.replaceState` so manual refresh doesn't
  re-fire. Called once at boot from `fetchProjects().then(...)` after
  agent-session restore, and on every `mc-deeplink` postMessage from the
  service worker (notification click while the PWA is already open).
- `sw.js notificationclick`: instead of `client.navigate()` (unreliable
  for standalone PWA windows, can stomp on in-flight UI), now focuses
  the existing client and `postMessage({type:'mc-deeplink', url})` to
  the SPA. Cold start still uses `clients.openWindow(targetUrl)`.

### Push-sub dedup-by-endpoint (survives re-OTP)

- `POST /api/push/subscribe`: before writing the new record, scans
  existing subs for a matching `endpoint` under a *different* nonce. If
  found, the old nonce-keyed record is dropped and its prefs
  (`label`, `notify_*`, `created_at`) carry over. Logged with
  `[push] migrated subscription X… → Y…`. Browsers reuse the same
  PushSubscription.endpoint across CF re-OTPs even though MC's nonce
  changes, so this keeps the device count honest.

### localStorage device-name auto-submit (silences re-OTP UX)

- `/_mc/name-device` page now writes `localStorage.mc_device_name` on
  successful submit. On reload (e.g. after re-OTP gives the device a
  fresh nonce), if that key is set, the page auto-submits without UI —
  the user sees a brief *"Recognized this device as <name>.
  Reconnecting…"* card instead of being asked to name again. The
  CF-Access OTP step itself is untouched (it's CF's auth boundary, not
  ours).

### Files touched

- `static/manifest.json` (new)
- `static/icon-192.png`, `icon-512.png`, `icon-badge-72.png` (new)
- `static/sw.js` — PNG icons, postMessage on click
- `static/index.html` — manifest link, deep-link handler, SW message
  listener, install button + state, push section render
- `server.py` — `push_subscribe` dedup-by-endpoint, name-device page
  auto-submit JS

### Windows PWA: single-instance launch (`launch_handler`)

Observed on Windows after installing the PWA: clicking the Start menu /
taskbar icon while Clayrune was already open spawned a **second
standalone window** instead of focusing the existing one. Chrome's
default for `display: standalone` is `navigate-new` — a new window per
launch.

Fix: added `launch_handler.client_mode: "focus-existing"` to
`static/manifest.json`. Now a second click on the Start menu icon
focuses the open window without navigating or reloading, so session
state in the SPA is preserved. (Service-worker-driven deep links from
notification clicks are unaffected — they go through the existing
`postMessage` path, not the launch URL.)

Rollback: drop the `launch_handler` block from `manifest.json` and
re-install the PWA. Note that PWAs cache the manifest aggressively;
uninstall + reinstall is the reliable way to pick up manifest changes.

### Open follow-ups

- iOS PWA install path (requires Safari "Add to Home Screen", different
  install affordance — no `beforeinstallprompt` on iOS).
- CF Access "Session Duration" — left at user's CF policy default. Ron
  can bump it to 7d/30d in the Cloudflare dashboard if re-OTPs become
  noisy.

## [2026-05-11] — Web push notifications (Android-first)

Wires Claude's `PushNotification` tool to actual phone-side delivery via
VAPID web push. Solves the "I have no idea when the agent is done" problem
without building a Telegram bot — taps on the push land in the existing
clayrune.io chat where `/agent/send` already handles follow-ups.

### Why

Claude's built-in Remote Control (claude.ai "Code" surface) only registers
*interactive* (TTY) sessions, so MC-managed sessions never show up there
even though `--remote-control` is accepted by the CLI. We confirmed this in
testing: a real TTY `claude --remote-control "tty-test"` registered fine,
but a `claude --print --remote-control ...` from MC did not. The
`agent_remote_control` toggle is now marked **EXPERIMENTAL** in Settings;
web push is the supported notification path.

Claude's `PushNotification` tool (deferred tool, see the verbatim
description in `docs/web-push-handoff.md`) is model-aware: the model knows
when to call it (long task done, build ready, decision needed) and when
NOT to (routine progress, just-answered questions). MC intercepts the
`tool_use` event in stream-json and delivers the push itself, since the
"push to phone" half of the tool relies on Remote Control discovery that
MC sessions don't get.

### Backend (`server.py`)

- New module: `# ── Web push notifications` block (just above the per-CF
  session-labels block). Self-contained: VAPID keypair generation,
  subscription storage, dispatch helper, stream-reader hook, endpoints.
- VAPID keypair lazily generated via `py_vapid` on first call to
  `_load_vapid_keys()`. Public key serialized as base64url-encoded 65-byte
  uncompressed P-256 point (what `PushManager.subscribe` expects in
  `applicationServerKey`). Private key persisted as PEM PKCS8 (what
  `pywebpush.webpush(vapid_private_key=...)` accepts). File:
  `data/push_vapid.json`. Survives restarts; only generated once.
- Subscriptions persisted at `data/push_subscriptions.json`, keyed by CF
  Access session nonce (same key the session-label system uses, so
  subscriptions get cleaned up alongside revoked CF sessions). Non-CF
  callers fall back to `local:<sha1(endpoint)[:16]>`.
- `_notify_push(title, body, *, url, project_id, session_id, kind)`
  encrypts + signs via `pywebpush.webpush()`, fires to every subscription
  that opted in for `kind` (`'agent'` or `'turn_complete'`), removes 404/410
  subscriptions automatically (browser unsubscribed or push service
  evicted), records `last_used_at` on success.
- `_handle_push_signal(project_id, session_id, msg)` is called once per
  parsed stream-json message in **both** stream readers
  (`_read_agent_stream` Mode A, `_read_agent_stream_b` Mode B):
  - `type=assistant` with a `tool_use` block where `name=='PushNotification'`
    → fire `kind='agent'` push with `input.message` as body.
  - `type=result` → fire `kind='turn_complete'` push iff the project has
    `notify_turn_complete=True` and `notify_push_enabled` (default `True`).
- Endpoints (mirror the `# ── Remote access` block style):
  - `GET  /api/push/vapid-public-key` — returns base64url public key.
  - `POST /api/push/subscribe`        — body `{endpoint, keys{p256dh,auth}, label?}`.
  - `POST /api/push/unsubscribe`      — body `{nonce}` or `{endpoint}`.
  - `GET  /api/push/subscriptions`    — list (no endpoint exposed).
  - `PATCH /api/push/subscription/<nonce>` — toggle `notify_agent_push` /
    `notify_turn_complete` / `project_filter` / rename.
  - `POST /api/push/test`             — fire a test push to every subscriber.

### Service worker (`static/sw.js`)

- Served at `/sw.js` (not `/static/sw.js`) via a new `service_worker()` route
  in `server.py`, with `Service-Worker-Allowed: /` header so the worker
  scope covers the whole origin (`/?project=...&session=...` deep links
  need root scope).
- `push` event handler reads JSON payload `{title, body, url, project_id,
  session_id, kind, ts}` and calls `showNotification()`. Tag is
  `mc-<session_id>` so re-pushes for the same session collapse instead of
  stacking.
- `notificationclick` handler tries to focus + navigate an existing tab on
  this origin to the `url` (typically `/?project=X&session=Y`), falls back
  to `clients.openWindow()`. (Deep-link routing on the SPA side is not yet
  wired — clicking lands you on `/` for now; routing into a specific
  project + session tab is a follow-up.)

### Frontend (`static/index.html`)

- New `pushNotificationsSettingsHTML()` section rendered right under
  Remote Access in Settings. Detects browser support; shows the right CTA
  for `Notification.permission` (default / granted / denied). The
  "Enable on this device" flow runs:
  - `Notification.requestPermission()`
  - `navigator.serviceWorker.register('/sw.js', {scope: '/'})`
  - `pushManager.subscribe({userVisibleOnly: true, applicationServerKey: ...})`
  - `POST /api/push/subscribe` with the resulting endpoint + keys + a
    guessed device label (e.g. "Chrome · Android").
- "Subscribed devices" list shows label, UA, last-used / created times, a
  Remove button (calls `/api/push/unsubscribe` AND `subscription.unsubscribe()`
  if it's this device), and per-device toggles for "Agent push" and
  "Turn complete" (PATCH `/api/push/subscription/<nonce>`).
- "Send test" button calls `/api/push/test`.
- Existing "Remote Control" toggle in Claude Code Integration is now
  badged `EXPERIMENTAL` with a hint explaining the non-TTY caveat.
- `_renderSettings()` now also calls `refreshPushSection()` after the
  settings panel renders.

### Storage shapes

```jsonc
// data/push_vapid.json
{ "public": "BO…(87 chars b64url)", "private": "-----BEGIN PRIVATE KEY-----\n…", "created_at": 1715432400 }

// data/push_subscriptions.json — keyed by CF Access nonce (or local:<hash>)
{
  "<nonce>": {
    "label": "Chrome · Android",
    "ua": "Mozilla/5.0 …",
    "endpoint": "https://fcm.googleapis.com/fcm/send/xyz",
    "keys": { "p256dh": "…", "auth": "…" },
    "project_filter": null,
    "notify_agent_push": true,
    "notify_turn_complete": false,
    "created_at": 1715432400,
    "last_used_at": 0
  }
}
```

### Per-project flags (optional, default behavior is correct)

- `notify_push_enabled` (default `True`) — project-level kill-switch.
- `notify_turn_complete` (default `False`) — opt-in for end-of-turn pushes
  (spammy by default).

Not yet exposed in the per-project menu — defer until users ask. The
server reads them straight from the project JSON via `load_project(...).
get(key, default)`.

### Dependencies

- `pywebpush>=2.0.0` added to `requirements.txt` (pulls `py-vapid`,
  `http-ece` transitively). Tested with `pywebpush==2.3.0`.
- `cryptography>=43.0` was already in `requirements.txt` for mc_remote;
  pywebpush uses it for VAPID + ECE encryption.

### Rollback

- Revert this commit, remove `data/push_vapid.json` and
  `data/push_subscriptions.json`. The `pywebpush` import is lazy inside
  `_notify_push` / `_load_vapid_keys`, so leaving the package installed
  while the code is reverted is harmless.

### Follow-ups (not blocking)

- SPA deep-link routing for `/?project=X&session=Y` from notification clicks.
- Per-project notify toggles in the three-dot menu (server already
  supports them).
- iOS PWA install path (requires "Add to Home Screen" first; spec'd in
  `docs/web-push-handoff.md`).
- Test on Android Chrome end-to-end (Ron, this needs a server restart and
  a phone). After restart: open Settings → Push Notifications → Enable on
  this device on the phone via clayrune.io → tap "Send test" from the
  desktop dashboard → notification should ring on the phone.

## [2026-05-10c] — Skills import: GitHub tree-URL parsing + Anthropic plugin detection

Two related improvements to skills import. First, **GitHub web URLs that point
at a subfolder of a repo now work** — earlier the importer rejected them with
the raw `git clone` error (`repository '...not found'`). Second, **Anthropic
plugins are detected as a distinct shape**: the importer now offers "Install
full plugin" alongside "Install this skill" when a `.claude-plugin/` folder
is present.

### URL parsing (`skills.py`)

- `_GH_TREE_RE` matches `github.com/<owner>/<repo>/(tree|blob)/<ref>/<subpath>`.
- `normalize_git_url(url)` returns `{clone_url, ref, subpath}` — tree/blob URLs
  get split into bare clone URL + branch + subdirectory; bare repo URLs pass
  through.
- `git_clone_to_staging` now uses the normalized parts: clones the bare URL,
  applies the parsed branch via `--branch`, and after clone trims the staging
  tree to just the requested subpath so the rest of the pipeline (scan,
  candidate selection, install) stays unchanged.
- Error messages updated: when no SKILL.md is found under a subpath, the
  message says so plainly instead of leaving the user to guess.

### Plugin detection (`skills.py`)

- `detect_plugin_at(root)` returns `{name, manifest, readme_excerpt,
  skill_dirs, command_files, agent_files, hook_files, has_hooks, root_path}`
  when `.claude-plugin/` exists; `None` otherwise.
- `install_full_plugin(plugin_root)` copies `skills/`, `commands/`, and
  `agents/` to their respective `~/.claude/` directories. **Hooks are not
  installed**: registration requires modifying `~/.claude/settings.json`
  with author-supplied event bindings, which is arbitrary shell-code
  execution and a stronger trust statement than copying data files. The
  result includes a `skipped.hooks` list and the summary message points
  the user at CC's `/plugin` command for hook installation.
- Both `git_clone_to_staging` and `import_from_folder` now attach a
  `plugin: {...}` field to their response when a plugin is detected. The
  git endpoint also skips auto-install of single-skill clones when a
  plugin is present, so the user always sees the picker and can choose
  between "skill-only" and "full plugin" modes.
- New error path: when a plugin is detected but contains no SKILL.md, the
  message is now: *"This is the Anthropic plugin "<name>" but contains no
  skills (only N command(s), M sub-agent(s)). Clayrune manages skills;
  for the rest, install via CC's /plugin command instead."*

### Endpoint (`server.py`)

- `POST /api/skills/import/plugin` — body `{staging_id?, path?, overwrite?}`.
  Either `staging_id` (from a prior `/api/skills/import/git` call) or `path`
  (a local folder) is accepted. Full-plugin install goes to GLOBAL scope
  only; project-scope full-plugin install is not supported in v1.
- The existing `/api/skills/import/git` endpoint no longer auto-installs a
  single skill when a plugin is detected — the response includes the
  plugin info so the frontend can prompt the user.

### Frontend (`static/index.html`)

- `_renderPluginBanner(plugin)` — small accent-bordered banner with a
  PLUGIN badge, plugin name, component counts (skills · commands ·
  sub-agents · hooks), an optional README excerpt (first 360 chars), and
  an amber warning line when hooks are present.
- `_renderFullPluginButton(modalId)` — full-width "Install full plugin
  (skills + commands + sub-agents)" button rendered above the per-skill
  candidate rows.
- `_doSkillImportFullPlugin(modalId)` — POSTs to `/api/skills/import/plugin`
  using `win_importPluginSource[modalId]` (set by the multi-skill picker
  when `plugin` is in the response). Shows the summary message in the
  status line and as a toast.
- Both the Git import picker and the Folder import picker now show the
  banner + full-plugin button when applicable. The per-skill candidates
  remain below.

### Trust model

The deliberate carve-out for hooks isn't about JSON merge fragility — it's
about the trust statement. Skills, commands, and sub-agents are data the
model reads; their execution path is mediated by the model + user
permission system. Hooks are shell scripts that run automatically on
lifecycle events, with no model and no permission prompt between author
intent and execution. We auto-install the first three; we defer hooks to
CC's `/plugin` command, which (presumably) has its own confirmation step
for that stronger trust statement.

### Rollback

Remove the plugin detection block in `skills.py` (`# ── Anthropic-plugin
detection + full-plugin install ──`), the `import_full_plugin_route` and
plugin-info attachment in `server.py`, the `_renderPluginBanner` /
`_renderFullPluginButton` / `_doSkillImportFullPlugin` helpers in
`static/index.html`, and the banner-render code inside the two candidate
pickers. URL parsing changes are independent and can stay or be removed
separately.

## [2026-05-10b] — Skills import (paste / folder / Git URL / cross-project)

Follow-up to the morning's Skills surface. Adds 4 import paths so users can
bring in skills from outside Clayrune instead of authoring everything from
scratch. All four ship together because they cover non-overlapping sources
and share the same destination-scope picker.

### Backend (`skills.py`)

- `import_from_paste(content, scope, ...)` — parses pasted SKILL.md,
  validates frontmatter, calls `write_skill`. Name comes from frontmatter
  or an explicit override.
- `import_from_folder(src_path, scope, ..., selected_rel_dir?)` — scans
  the folder (depth-capped at 3) for SKILL.md files. Single hit installs
  immediately; multi-hit returns `{multiple: True, candidates: [...]}`
  so the caller can re-invoke with `selected_rel_dir`.
- `git_clone_to_staging(url, ref?, timeout=60)` — shallow `git clone` into
  `~/.claude/skills.staging/<uuid>/`, strips `.git`, scans for SKILL.md
  files. Returns `{staging_id, candidates}`.
- `install_from_staging(staging_id, rel_dir, scope, ...)` — copies the
  chosen candidate from a previously-staged clone. Path-traversal-checked
  (rel_dir must stay inside staging_path).
- `cleanup_stale_staging(max_age_hours=24)` — sweeps abandoned staging
  dirs at startup so they don't accumulate.
- `_install_skill_dir` (private) — shared helper that copies a skill
  folder + normalizes the destination SKILL.md's frontmatter `name` to
  match the install name (so the install name and the frontmatter never
  diverge).
- `_scan_for_skills(root, max_depth)` — finds all SKILL.md files,
  returns `{name, rel_dir, abs_dir, description, has_subassets}` for
  each. Used by both folder and git flows.

### Backend (`server.py`)

- `POST /api/skills/import/paste` — body `{content, scope, project_id?, name?}`
- `POST /api/skills/import/folder` — body `{path, scope, project_id?, name?, selected_rel_dir?}`;
  returns `{multiple: true, candidates: [...]}` when ambiguous.
- `POST /api/skills/import/git` — body `{url, ref?, scope, project_id?, name?, auto_install?}`.
  Auto-installs when exactly one SKILL.md found; otherwise returns
  `{staging_id, candidates}` for the picker.
- `POST /api/skills/import/git/install` — body `{staging_id, rel_dir, scope, ...}`.
  Path-checked so a malicious `rel_dir` can't escape the staging dir.
- `POST /api/skills/import/git/cancel` — discards a staging dir without
  installing.
- Startup hook: `_skills.cleanup_stale_staging(max_age_hours=24)` runs
  from `__main__` alongside `_install_builtin_skills()`.

### Frontend (`static/index.html`)

- New **Import ▾** dropdown beside "+ New Skill" in the Skills modal
  header. 4 menu entries: Paste SKILL.md / From a folder / From a Git URL
  / Browse other projects.
- `_importContextHTML` shared component renders the scope radio + project
  picker — used by all 4 import modals so destination-scope UX is uniform.
- Defaults: when the Skills modal is filtered to a specific project, the
  import context defaults to that project; else global.
- **Paste modal**: large monospace textarea, optional name override,
  scope picker. Single click installs.
- **Folder modal**: path text input (Windows + POSIX accepted), optional
  name override. If backend reports `multiple`, inline candidate picker
  shows below the input with one-click install per candidate.
- **Git modal**: URL input, optional branch/tag, optional name override
  (single-skill repos only). Single-skill repos auto-install. Multi-skill
  repos show inline candidate picker. Cancel cleans up the staging dir.
- **Browse modal**: fan-out search across global pool + every loaded
  project's pool, dedup + sort by score. Each result has "Read body"
  (toggles inline body preview) and "Install here" (copies into the
  chosen destination scope).

### Notes / design decisions

- Cross-project copy reuses existing `POST /api/skills` — no new endpoint
  needed. Frontend fetches the source skill with `include_body=true`,
  POSTs the same name/description/body to the destination.
- Git clone is shallow + 60s timeout. `.git` is stripped after clone so
  the skill folder looks like any other on-disk skill.
- Multi-skill repo case routes through a staging dir to avoid double-clone
  on candidate selection. Stale staging dirs are swept at startup.
- Private repos: deliberately not supported in v1. Users can clone
  manually with system git (which has their credentials) and import via
  the folder path, or wait for a follow-up.
- Marketplace / Anthropic registry: deliberately skipped. No registry to
  point at yet; placeholder UI would be a liability.

### Rollback

Remove the `# ── Skills import (paste / folder / Git URL ...)` block in
`server.py`, the `# ── Import (paste / folder / git URL)` block in
`skills.py`, and the `// ── Skills import (paste / folder ...)` section in
`static/index.html`. Also remove the staging cleanup call from `__main__`
and the Import dropdown HTML inside `openAllSkills`. Existing skills are
unaffected — only the import paths disappear.

## [2026-05-10] — Skills surface (Anthropic-format skill management)

Adds a first-class Skills surface to Clayrune so users can author, organize,
and (eventually) share Anthropic-format skills the way they already manage
backlog, scheduler routines, and hiveminds. Skills are the lazy-loadable
instruction packs Claude Code reads from `~/.claude/skills/<name>/SKILL.md`
(global) and `<project_path>/.claude/skills/<name>/SKILL.md` (project-local).
Clayrune does NOT teach CC about skills — CC already loads them natively. The
new surface is purely management (CRUD + archive + search + usage stats).

**Why now.** Anthropic's skill ecosystem matured around `/loop`, `/schedule`,
`/review`, `/security-review`, etc. Going full-live without a way to view /
author / manage skills would leave a visible product gap; pre-launch is the
right window to add it.

**Distinct from the March 2026-03-17i removal.** That removal deleted MC's
own homegrown "Skills" feature (markdown-blob injection — Memory replaced it).
The new feature is a wrapper around CC's native skill system, not a re-do of
the old one.

### Backend — new module `skills.py`

- `parse_skill_md` / `dump_skill_md` — tiny YAML-frontmatter parser/dumper
  (no PyYAML dep). Handles `key: value`, block scalars (`|`, `>`), folded
  multi-line continuations.
- `list_skills(project_path, include_archived, include_body)` — merges
  global pool + a named project's pool (+ optionally archived), annotates
  `shadowed_by_project=True` when a global is overridden by a project skill
  of the same name (CC's own resolution rule).
- `read_skill` / `write_skill` / `delete_skill` / `restore_skill` —
  filesystem CRUD. `delete_skill` archives globals by default (moves to
  `~/.claude/skills.archive/`); project skills hard-delete (archiving them
  globally would move files out of the user's project tree).
- `search_skills(query, project_path, limit)` — keyword search over
  name (×3) + description (×2) + body (×1). Deterministic, cheap. Used by
  the `mc-skill-broker` skill for cross-project discovery.
- `install_builtins(builtin_root)` — checksum-aware install of bundled
  skills. For each `<name>/` in `data/skills/builtin/`: if not installed,
  copy + write `.mc-builtin-hash` marker. On subsequent boots, if the
  marker matches the installed SKILL.md hash AND the source has changed,
  update it; if the user has modified the file (hash drift from marker),
  leave alone and log `preserved=[...]`. Users always win.
- `skill_usage_stats(days)` — greps `~/.claude/projects/*/*.jsonl` for
  `Skill` tool-use blocks; returns `{name -> {invocations, last_invoked_at,
  project_count}}`. Same transcript-parsing path the Agent Log tab already
  uses (CHANGELOG `[2026-04-28]`). Surfaces dead skills.

### Backend — endpoints (`server.py`)

- `GET /api/skills?project_id=&include_archived=&q=` — list (no body)
- `GET /api/skills/<scope>/<name>?project_id=&include_body=` — read one;
  scope ∈ {`global`, `project`, `archive`}
- `POST /api/skills` — create; body `{name, description, body, scope, project_id?}`
- `PUT /api/skills/<scope>/<name>` — update
- `DELETE /api/skills/<scope>/<name>?project_id=&archive=true|false` —
  archive (global default) or hard-delete
- `POST /api/skills/archive/<name>/restore` — move back to global pool
- `GET /api/skills/search?q=&project_id=&limit=` — ranked keyword search
- `GET /api/skills/usage?days=30` — invocation stats from transcripts

All endpoints validate name format (kebab-case via `_NAME_RE`), require a
non-empty description, and refuse project scope when the named project has
no `project_path` set.

### Backend — built-in install hook

`_install_builtin_skills()` runs from `__main__` on startup. Source-of-truth
under `data/skills/builtin/`; safe to run on every boot. Logs `installed=`,
`updated=`, `preserved=` to stdout.

### Built-in skill set (`data/skills/builtin/`)

Five skills ship with Clayrune:

1. **`mc-clayrune-apis`** — teaches agents the localhost:5199 API surface
   (process registration, backlog, scheduler, hivemind, terminal). This is
   the wedge that — once skills prove reliable in production — will let us
   trim the `_build_agent_context()` preamble from ~40 lines to a pointer.
2. **`document-commit-deploy`** — concrete playbook for the
   "update docs, commit, push" workflow that SHARED_RULES requires but
   that today's agents only inconsistently follow.
3. **`mc-project-status`** — pulls backlog + recent activity + active
   hiveminds + scheduled jobs + registered processes into a structured
   project-state summary.
4. **`mc-changelog-update`** — guided CHANGELOG.md entry that matches the
   existing project's date-stamp / section style / voice.
5. **`mc-skill-broker`** — cross-project skill discovery. Calls
   `/api/skills/search` so a project-A agent can find a useful skill
   authored in project-B without polluting every session's catalog. The
   scaling story past ~80 skills.

### Frontend (`static/index.html`)

- New sidebar entry "Skills" with puzzle-piece icon, positioned **above
  Backlog** (per user preference). `data-nav="skills"` → `sidebarNav('skills')`
  → `openAllSkills()`.
- New project modal three-dot menu entry "Skills" (next to Memory & Rules)
  → `openAllSkillsForProject(projectId)` which pre-filters the global view
  to that project's scope.
- **Global Skills modal** (`__all_skills`): search box, scope filter (all
  / global / project / archive), project dropdown, "Include archived"
  checkbox, "+ New Skill" button, scrollable list.
- **Skill row UI** (`_renderSkillRow`): name, scope badge (global / project:
  X / archived), shadowed badge when global is overridden, 30-day
  invocation count from `_skillUsageCache`, full path + last-edited
  timestamp, Edit / Archive (or Delete for non-global) buttons.
- **Skill editor modal** (`openSkillEditor`): name (kebab-case, locked when
  editing), scope radio + project picker (only on create), description
  textarea with **live linter** (`lintSkillDescription` — warns on
  short descriptions, missing TRIGGER, vague trigger language), body
  textarea, Save / Cancel.
- Saves call `POST/PUT /api/skills` and refresh the list on success.
- Archive / restore / delete confirmations via standard `confirm()` +
  `showToast` flash.

### State (frontend)

- `_allSkillsCache = {items, loaded, loading}`
- `_allSkillsFilter = {scope, project, search, includeArchived}`
- `_skillUsageCache = {stats, loaded}`

### Decisions captured during scoping (memory: `project_skills_for_launch.md`)

- Sidebar position: above Backlog
- Built-ins ship globally (one install in `~/.claude/skills/`, every project
  sees them) rather than copying into each project's tree
- Project skills shadow globals of the same name; surface "shadowed" badge
- Skills broker is the answer to the scaling concern — keyword search over
  the full pool, so the broker becomes *more* valuable past ~80 skills
- Per-project enable/disable of globals is **NOT** in this release; deferred
  until usage stats prove globals are bloating sessions
- Built-in update propagation: only when user hasn't edited the file. Hash
  marker `.mc-builtin-hash` decides.

### Rollback

- Remove sidebar entry (line ~3507 in `static/index.html`), `sidebarNav`
  dispatch (line ~4995), three-dot menu item (line ~4413).
- Delete the Skills section in `static/index.html` (search comment
  `// ── Skills (global + per-project`).
- Delete the Skills endpoints in `server.py` (search comment
  `# ── Skills endpoints`).
- Remove `_install_builtin_skills()` call from `if __name__ == '__main__':`
  block and the `import skills as _skills` line at the top.
- Existing `~/.claude/skills/mc-*/` folders can be archived or deleted
  manually; CC will simply stop seeing them.

## [2026-05-09] — Proactive update notification + marketing site mockups

**Proactive Clayrune update notification** (`server.py`, `static/index.html`).
The Update Clayrune button only ever fired if the user happened to click
Settings → Update — so most updates went unseen. Now the dashboard signals
updates passively + actively without needing a click.

- New background daemon `_update_check_loop()` in `server.py` runs `git fetch`
  + computes the behind count every 6 hours, stores result in
  `_UPDATE_CHECK_CACHE` under `_UPDATE_CHECK_LOCK`. First check fires 60s
  after server boot.
- New `/api/system/update/cached` endpoint reads the cache (no git
  operations on the request path). Existing `/api/system/update/status`
  unchanged — still does a fresh fetch when the user actively clicks
  "Check now" in Settings.
- Frontend: new `checkClayruneUpdateAvailable()` runs once after
  `fetchProjects()` resolves on dashboard load. If `update_available`:
    1. `.has-update` class on `.sidebar-item[data-nav="settings"]` → small
       accent dot with a 2.4s pulse, always visible until the user updates
    2. One-time `showActionToast()` toast with three actions:
       **Update** (opens Settings → Update flow), **Later** (snoozes 24h
       via `mc_update_remind_after_ts`), **Dismiss** (silences this
       specific commit via `mc_update_dismissed_for`; new commits land a
       fresh toast)
- New `showActionToast(message, actions, opts)` utility — richer toast
  variant with primary/secondary buttons, auto-dismiss, optional
  click-to-close. Used by the update toast; reserved for future similar
  prompts.
- After `performClayruneUpdate()` succeeds, sidebar dot is cleared and
  both localStorage markers are reset so the next update lands cleanly.

**Marketing-site URL routing fix** (`server.py`).
Flask's `<path:filename>` matched `/marketing/v2/` as `filename='v2/'` and
404'd because `send_from_directory` expects a file. `serve_marketing` now
detects directory-style requests and rewrites to `<dir>/index.html`. Same
trick applies to any future subdir under `marketing/`.

## [2026-05-08g] — Marketing site groundwork (warm template + operator-console v2)

Two-track design exploration so the public website can be A/B'd.

- `marketing/index.html` (+ about / docs / download / styles.css) — imported
  unmodified from the Claude-design "Mission Control Design System" Apr 23
  bundle (`14 KB`, distinct from the in-app UI redesign already at
  `docs/design_system_extracted/`). Warm-cream tone (`#f6f0e4` bg + `#e8824a`
  accent), Nunito display + Inter body, hand-drawn brutalism. Source zip
  stays in `~/Downloads/` as the canonical reference; this is the working
  copy. Branding pass (Mission Control → Clayrune) and feature-list
  swap-in deferred — clean baseline first.
- `marketing/v2/index.html` — single-page from-scratch alternative pitched
  in conversation. Operator-console aesthetic: dark base (`#0c0e12`) with
  the same terracotta accent, Inter + JetBrains Mono. Hero is a specific
  scenario ("Tuesday, 3pm. 14 agents running. 3 waiting on you.") + a
  CSS-rendered mockup of the actual dashboard with 6 project tiles in
  mixed states. Differentiator hierarchy from `RESUME_HERE.md` §3:
  3 hero blocks (multi-project / persistence / plan-approval-gate) +
  3 secondary blocks (mobile remote / memory / backlog) + the vs.
  matrix from §5 (Claude CLI / Cursor / Devin / Aider) + a for/not-for
  callout + clean install section.
- `server.py:serve_marketing` — `/marketing/<path:filename>` route plus
  the implicit `/marketing/` handler so users can hit
  `http://localhost:5199/marketing/` (and `/marketing/v2/`) in a browser
  without spinning up a separate http server. Also reachable through
  the Cloudflare tunnel for mobile review. Pure dev convenience; the
  real public site will be served by Cloudflare Pages off `marketing/`
  directly.

## [2026-05-08f] — Mascot rename: Playdo → Claydo

Codebase rename of the in-app helper. Product name "Clayrune" unchanged —
only the mascot character. ~215 occurrences touched across user-facing
strings, code identifiers, CSS classes, HTML IDs, and helper paths.

- `static/index.html` (~120) — modal title, FAB id, CSS classes, JS
  identifiers (`_claydoHistory`, `openClaydo`, `_claydoFormatText`, etc.),
  walkthrough step, localStorage keys (`claydo_opened`, `claydo_fab_pos`).
- `server.py` (~20) — `_claydo_cwd`, `_looks_like_claydo_entry` helpers,
  `/api/guide/{stream,ask}` internal references.
- `docs/USER_GUIDE.md` (10), `installer/index.html` (1), `RESUME_HERE.md` (44).

Migration logic so existing installs upgrade cleanly (no manual steps):

- localStorage one-shot migration in `static/index.html`: reads old
  `playdo_*` keys, writes to `claydo_*` if not already set, deletes the
  old. Idempotent.
- `data/claydo/` (Claude transcript sandbox for the Ask Claydo helper):
  `_claydo_cwd()` renames `data/playdo/ → data/claydo/` if the old dir
  exists, preserving Claude's stored conversation continuity (transcripts
  are keyed off cwd path).

Intentionally untouched:
- `assets/clayrune.png` / `clayrune.ico` — same image is product mark
  AND mascot likeness; one file, two roles.
- `[clayrune:...]` marker prefix — product-namespaced, kept as-is.
- `CHANGELOG.md` history — past entries describe pre-rename work
  accurately for that point in time.

Memory file `naming_playdo_clayrune.md` orphaned (delete-permission
issue); replacement `naming_claydo_clayrune.md` created and indexed in
`MEMORY.md`.

## [2026-05-08e2] — Windows taskbar icon (clayrune.ico + console icon helper)

User report: *"the clayrune icon on taskbar appears as bat file icon."*
Two compounding issues:

1. `assets/clayrune.ico` did not exist. Only `clayrune.png` was checked
   in. `install.ps1` was setting `IconLocation = ...\clayrune.ico` on the
   `.lnk` shortcut, but the file was missing — Windows fell back to the
   `.bat`'s default cmd.exe icon. Generated a multi-resolution
   `assets/clayrune.ico` from the source PNG (16/24/32/48/64/128/256)
   covering all of Windows' icon contexts.
2. Even with the `.lnk` fixed, the *running* cmd window's taskbar entry
   uses cmd.exe's icon, separately from the `.lnk`. New
   `installer/set-console-icon.ps1` sends `WM_SETICON` to the console
   window via Win32 to replace it in-place (both `ICON_SMALL` and
   `ICON_BIG`). The icon is owned by the window so it persists after
   the helper exits. `start.bat` invokes the helper at the top.

Also added `title Clayrune` so the cmd window's title bar (and taskbar
hover) reads "Clayrune" instead of the path to `start.bat`.

## [2026-05-08e] — Working-tree cleanliness so Update Clayrune doesn't get stuck

Two compounding bugs blocked **Update Clayrune** showing "Blocked" right
after a fresh install on every test VM.

1. **`data/claydo/` not gitignored.** Server materializes USER_GUIDE.md as
   `CLAUDE.md` inside `data/claydo/` (formerly `data/playdo/`) every time
   anyone asks Claydo, so Claude auto-loads it as project context. The dir
   wasn't in `.gitignore`, so `git status --porcelain` reported it
   untracked → update endpoint refused to pull → button "Blocked".
   `.gitignore` now lists `data/claydo/`, `data/playdo/` (pre-rename
   compat), and `install-launch.log` / `install.log`.
2. **Installer shell scripts had mode 100644 in the index.** `install.sh`
   STEP 3 ran `chmod +x installer/start.sh` (and the others) on Linux so
   the `.desktop` launcher could execute them. Working tree went 100755,
   git compared against 100644 in the index, reported "modified" — same
   "Blocked" UX. `installer/install.sh`, `installer/start.sh`, and
   `installer/start.command` now stored as 100755. Future `chmod +x` is
   a no-op.

For users with the dirty state at upgrade time: `git pull --ff-only`
applies both fixes to the working tree (mode change is metadata-only).
Documented `git checkout -- installer/start.sh` recovery path for VMs
that hit the modified-content blocker before fix-pull.

## [2026-05-08d] — Vanilla-VM installer validation (Windows 11 Home + Ubuntu 22.04)

End-to-end install testing on freshly-snapshotted VMs caught a long tail of
real-world OS quirks. Two new VMs are kept clean for re-testing per
`CLAUDE.md`. Big arc; subsections by failure surface.

**Deterministic install (no Claude handoff)** —
`installer/install.{ps1,sh}`. Original design piped `install-prompt.md` (24 KB
markdown) into `claude --dangerously-skip-permissions -p`. Newer Claude
models flag that as a prompt-injection attack pattern (*"I won't follow
those instructions because…"*) and refuse, then exit 0 — letting the
wrapper falsely declare success. Every step in the prompt was deterministic
shell anyway (git clone, venv, pip, shortcut, server launch). Both
installers now do the install directly:
- `[STEP 1/5]` git clone; auto-installs git via apt/dnf/pacman/winget on
  Linux/Windows if missing.
- `[STEP 2/5]` Python 3.11+ + venv + pip install. Handles Ubuntu's
  separate `python3-venv` package, Windows App Execution Alias stubs at
  `%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe`, the Python launcher
  fast path (`py -3.11`).
- `[STEP 3/5]` Launcher: `~/.local/share/applications/clayrune.desktop`
  (Linux), `~/Applications/Clayrune.command` (macOS), Desktop +
  Start Menu `.lnk` shortcuts (Windows).
- `[STEP 4/5]` Launch the server. Linux uses `setsid` to fork into a new
  session so the daemon survives `curl | sh` parent-shell exit (`nohup`
  alone catches only SIGHUP, not the SIGTERM/SIGPIPE from session
  termination). Windows uses `Start-Process -WindowStyle Minimized`.
  30s poll on localhost:5199. Captures stdout/stderr to
  `install-launch.log` (Linux) so server-startup failures leave a
  forensic trail.
- `[STEP 5/5]` Open browser via `xdg-open` / `open` / `Start-Process`.

**Linux: import-time keyring → D-Bus deadlock** (`mc_remote/__init__.py`).
On a fresh Ubuntu desktop pre-first-login (and headless server VMs, WSL
without DBUS_SESSION_BUS_ADDRESS), `import mc_remote` triggered
`tunnel_supervisor.maybe_start()` → `device_keys.load_identity()` →
`keyring.get_password()` → secretstorage trying to talk to
`org.freedesktop.secrets` over D-Bus → blocks indefinitely waiting for
a reply that never comes. server.py never reached `app.run()`. Now the
auto-start runs on a daemon thread so the keyring call can hang forever
without blocking server startup; remote-access stays "not yet started"
until the user clicks Enable.

**Windows: ASCII-only `.ps1` files + UTF-8 BOM unaware reader**
(`installer/install.ps1`, `installer/Clayrune-Nuke.ps1`). Two compounding
bugs caused `iex : Variable reference is not valid. ':' was not followed
by a valid variable name character`:
1. `${lnk}` braces missing on `Write-Host "  WARN could not create $lnk: $_"`
   — `$lnk:` parsed as a drive-qualified variable.
2. Files were UTF-8 sans BOM. PowerShell on Windows reads BOM-less
   scripts as Windows-1252; em-dashes (`—`) and box-drawing (`─`) were
   mangled into byte sequences that sometimes happened to look like
   brace/quote characters to the parser → spurious `Missing closing }`
   errors at unrelated lines. All non-ASCII replaced with ASCII
   equivalents; `Parser.ParseInput` now reports zero errors.

**Windows: `App Execution Alias` Python stubs** (`install.ps1`).
`%LOCALAPPDATA%\Microsoft\WindowsApps\python.exe` and `python3.exe` are
Microsoft Store redirect stubs, not real Pythons. `Get-Command` finds
them, version-check runs them, the stub prints
`"Python was not found; run without arguments to install from the Microsoft
Store..."` to stderr, and PowerShell's `$ErrorActionPreference = 'Stop'`
turns that into a terminating error → script halts before reaching the
winget Python install fallback. `Find-Python311` now skips paths matching
`\WindowsApps\` and adds a `py -3.12 / -3.11` fast path. ErrorActionPref
relaxed to `Continue` for the install phase since each step does its own
exit-code + Test-Path checks.

**LF/CRLF line endings + cmd.exe parse fragility**
(`.gitattributes` new, `installer/Clayrune-Setup.bat`). Bat files were
checked in with LF (no `.gitattributes` so `text=auto` normalized to LF in
the blob; GitHub raw served LF; Chrome saved LF). cmd.exe silently
misparses LF-only `.bat` files, particularly multi-line `^` continuation
in the powershell.exe call — the cmd window flashed and died before any
`pause` could hold it open. `.gitattributes` now stores `*.bat / *.cmd /
*.ps1` as `-text` (no normalization) with CRLF bytes in the index, and
`*.sh / *.command` as `text eol=lf`. The PowerShell call's `^` continuation
is also collapsed to a single line as belt + suspenders.

**Cache-busting GitHub raw** (`installer/Clayrune-Setup.bat`).
GitHub raw's CDN holds files for several minutes post-push. We were
shipping hotfixes faster than the cache expired, so users running
`Clayrune-Setup.bat` would get stale `install.ps1`. Bat now appends
`?t=$(Get-Date)` to the URL — origin ignores the param but the CDN keys
on the full URL.

**`claude /login` flow when CLI install succeeds but auth missing**
(`installer/Clayrune-Setup.bat`). `[L]` option spawns a separate window
running `claude /login`. Old version used `cmd /c "claude /login"`, but
the spawned cmd inherited the parent .bat's pre-install PATH (which
didn't yet have `%APPDATA%\npm` from the just-completed npm install).
Now spawns via PowerShell which rebuilds `$env:Path` from the registry
on each call, so the freshly-installed `claude.cmd` is visible. Also
adds a final `Read-Host` to keep the window open even on error or
"command not found", so the user always sees what happened.

**Install verification** (`install.ps1`). After Claude's old prompt-based
handoff exits, don't trust the exit code — Claude could have refused or
crashed and exited 0 anyway. Post-Claude check now verifies
`server.py` + `installer/start.bat` exist on disk; if missing, prints a
red FAIL block and exits 2 (which the .bat treats as failure → routes to
the [L]/[R]/[Q] recovery menu instead of showing fake success). Now mostly
moot since the deterministic install replaced the Claude handoff, but
kept as belt + suspenders.

## [2026-05-08a] — Walkthrough + Sample Project + Update button reliability + Windows Claude CLI shim resolution + Playdo command-line-too-long

Multi-thread polish + bug-fix arc surfaced by the same fresh-VM testing.

**Walkthrough fixes** (`static/index.html`).
- Step 10 (Three-Dot Menu) body was a runaway sentence. Now a bulleted
  list of menu entries.
- `<strong>` tags in step bodies were `esc()`-d into literal text. Stop
  escaping — bodies are author-controlled hardcoded HTML.
- Step 13 (Agent Console) was pointing to top-left of viewport because
  `#agent-console` is `.hidden` by default. `onEnter` force-shows it,
  `onLeave` restores; skipped on mobile (covered by bottom tab bar).
- Step 15 (Command Palette) toggled the wrong class — `open` instead of
  the CSS-gated `visible`. Highlighted empty space because the palette
  stayed hidden. Fixed + pre-renders results so the palette has visible
  content.
- cmd-overlay z-index 9999 vs wt-card 2001 caused "two step 14s, second
  blank square" — when the user clicked Next on the wt-card, the click
  was intercepted by the transparent cmd-overlay backdrop, which fired
  its `toggleCommandPalette()` handler and closed the palette. The
  walkthrough didn't know; kept the highlight glowing around an empty
  space. Fix: `pointer-events:none` on the overlay during the
  walkthrough step (with `pointer-events:auto` on the palette itself
  so it stays visible).
- Skip-aware step numbering: `visiblePos / visibleTotal` computed from
  `WT_STEPS.filter(s => !(s.skip && s.skip()))`. On desktop the
  mobile-only bottom-tabs step no longer creates a 13 → 15 gap.

**Sample Project** (`server.py` + `static/index.html`).
Auto-assigns `project_path` to `<auto_workspace_base>/sample-project` so
agent dispatch works from the walkthrough's first interaction. Without
this the user opened the sample, typed a prompt, and got "Set
project_path to enable agent dispatch". New `/api/browse/folders` +
`/api/browse/create_folder` endpoints + a "Browse..." button beside the
Path field opens a folder-picker modal with parent nav,
Workspace/Home shortcuts, and inline "+ Create" for new folders.

**Windows: subprocess can't find `claude.cmd`** (`server.py`).
Root cause: `subprocess.Popen(['claude', ...])` only resolves `.exe` by
default — npm-installed Claude CLI is `claude.cmd` (a batch shim).
`shutil.which` respects PATHEXT and returns the full `.cmd` path, which
subprocess CAN execute. New `_resolve_claude()` helper used at all 22
cmd-list construction sites. Re-resolves per call so a Claude install
AFTER server startup is picked up without restart. Falls back to common
Windows install paths (`%APPDATA%\npm`, `~/.claude/bin`) before giving
up. Fixes both agent dispatch and Ask Playdo on fresh Windows installs.

**Update Clayrune endpoint hangs + races** (`server.py`,
`static/index.html`).
- `git fetch` hung on Windows for 30s waiting for Git Credential Manager
  (GCM) to pop a hidden auth dialog (which never appears because we run
  git with `STARTF_USESHOWWINDOW=SW_HIDE`). `_git()` now sets
  `GIT_TERMINAL_PROMPT=0` + `GCM_INTERACTIVE=Never` in subprocess env;
  fetch timeout dropped 30s → 12s.
- Settings UI hint stuck on "Checking for updates…" because
  `setTimeout(refreshUpdateStatus, 100)` fired BEFORE
  `body.innerHTML = ...` was assigned → `getElementById` returned null
  → helper bailed silently. Moved the call to the end of
  `_renderSettings()`.

**Playdo "command line is too long" on Windows** (`server.py`).
24 KB USER_GUIDE.md piped via `--append-system-prompt` blew past
**cmd.exe's 8191-char limit** (not CreateProcess's 32 KB; cmd.exe wraps
`claude.cmd` calls and has its own smaller cap). Fix: send the question
through stdin via `--input-format stream-json` + a JSONL user message.
Command line drops to ~150 chars regardless of question length. Both
`/api/guide/stream` and `/api/guide/ask` updated.

**Streaming installer progress** (`installer/install.{ps1,sh}`).
`claude -p` only prints the FINAL response — the user saw nothing for
3-5 minutes during the Claude handoff (mostly obsolete now that the
install is deterministic, but the streaming path was kept for the
Claude-CLI-install step). Both installers parse `--output-format
stream-json` and surface assistant text + tool-call indicators in real
time.

## [2026-05-08c] — Claydo state animations (thinking) + sheet-slicing pipeline

First state-driven Claydo animation lands: when the user submits a
question, the FAB and chat-avatar swap from the static idle PNG to
an animated WebP that loops through 4 thinking poses (chin-on-hand
->  eyes-closed -> chart in the code window -> COMPLETED checkmark).
Reverts to idle when the answer is done or errors. Adds two new
tools to make this repeatable for future states.

`tools/sheet-to-frames.sh` (new): slice a 2x2 (or NxM) character
sheet from Gemini / DALL-E / etc. into separate PNG frames.
ffmpeg-based, preserves alpha, autodetects gutter widths so panel
boundaries don't bleed into each other. Output: <name>_frames/frame_N.png.

`tools/frames-to-animation.sh` (new): stitch a sequence of stills
into a looping animated WebP (or GIF / APNG via -f). Takes any
number of frame files, holds each for the configured duration
(default 250ms), loops forever. Hardened for Git Bash on Windows
where ffmpeg.exe needs Windows-format paths even though the shell
uses POSIX paths -- two-step realpath + cygpath -w.

Pipeline: Gemini sheet -> sheet-to-frames -> N PNGs ->
frames-to-animation -> assets/claydo-<state>.webp -> drop into
_CLAYDO_STATE_SRC map.

`static/index.html` wiring:
- `_CLAYDO_STATE_SRC` map: { idle: clayrune.png, thinking: claydo-thinking.webp }
- `_setClaydoState(state)` helper: swaps both the FAB img AND the
  chat-modal avatar (newly given id="claydo-avatar"). Skips DOM
  writes when the basename hasn't changed so animated WebPs don't
  reset their loop on incidental re-renders.
- submitClaydo flips to 'thinking' on entry and back to 'idle' in
  the finally block (covers success, error, and disconnect paths).

Followups (subsequently shipped — see [2026-05-08c2] below):
- White-background fix landed via Python chroma-key (white → transparent
  with soft-edge alpha ramp).
- 4-state set (idle / thinking / working / error) shipped, sourced from
  a Gemini-generated state-variants video instead of the original
  still sheet, giving each state real frame-by-frame motion.

## [2026-05-08c2] — Claydo 4-state animation set (sourced from Gemini video)

Replaced the still-sheet-derived `claydo-thinking.webp` with a 4-state
animation set (`idle`, `thinking`, `working`, `error`) sourced from a
Gemini-generated animated video where each cell of the state-variants
sheet bounces / blinks / changes expression in place. Pipeline:
extract video frames → auto-detect cell layout (4 mascot columns in
the top row) → crop the same cell out of every frame → chroma-key
white to transparent → stitch each cell's 16 frames into its own
animated WebP. Each state file is 126–140 KB, 4-second loop at 250ms
per frame.

Wiring (`static/index.html`):
- `_CLAYDO_STATE_SRC` populated with all 4 states.
- FAB and chat-modal avatar default to `claydo-idle.webp` (instead of
  the still `clayrune.png`) so the mascot feels alive from page load.
- `submitClaydo()` finally block: on `errored=true`, holds
  `claydo-error` for 3s before reverting to idle, so the user notices.

`clayrune.png` preserved for installer / favicon / brand mark; only
the in-app personality moved to the animated WebPs.

## [2026-05-08b] — Video frame extraction for Claude Code sessions

`tools/extract-frames.sh` (new) + `CLAUDE.md` (new at project root).

Claude (this model) doesn't read videos natively — only images, PDFs,
notebooks. When a user attaches an `.mp4` (typically as
`data/uploads/agent_*.mp4` from Mission Control's upload pipeline) we'd
just say "I can't see this." Now there's a one-command path that gets
the model useful frames:

- **`tools/extract-frames.sh <video> [fps] [max_frames]`** — wraps ffmpeg.
  Defaults to 2 fps capped at 24 frames; writes
  `<basename>_frames/frame_001.png ... frame_NNN.png` next to the source.
  When the naive fps would exceed `max_frames`, switches to even
  sampling across the full duration so we get coverage rather than just
  the opening clip. Prints the output paths so the caller can grep.
- Tells the user how to install ffmpeg per OS if it's not on PATH.
- **`CLAUDE.md`** at the repo root: a one-paragraph instruction that
  any Claude Code session running in this repo automatically picks up
  ("when given a video file, run the extractor first"). No more
  "I can't see videos" friction during dev work.

Why this design over alternatives:
- Not server-side: keeping it as a dev-time utility means it doesn't
  depend on MC running, doesn't slow down upload, and works for any
  video the user wants Claude to look at, not just MC uploads.
- Not auto-extracting in the upload handler: most videos uploaded to
  MC are user reference material the agent doesn't need to see; we'd
  burn disk on every reference clip.
- Not video-native models (Gemini / GPT-4o): the frame-extract approach
  preserves the same Claude session, no provider switch, no separate
  context. Costs an ffmpeg invocation per video, which is free.

## [2026-05-07c] — Ask Playdo helper + walkthrough rewrite + USER_GUIDE.md

Three pieces shipped together to close the "new user has no idea what's possible" gap:

### `docs/USER_GUIDE.md` (new)

Comprehensive user-facing reference for every Clayrune surface. ~310 lines, sections:

```
What is Clayrune
Your first 5 minutes
Surfaces overview (Dashboard / Sidebar / Header / Mobile)
Project modal (tabs + 3-dot menu inventory)
Agent dispatch (sessions, plan approval, stop/continue, pop-out)
Hivemind (sidebar surface, workings, stale heuristic, Start from project)
Scheduler (recurring + Run Now + Runs panel + paginated history)
Backlog (per-project + cross-project + GitHub sync)
Memory & Rules (per-project + shared)
Plans / Activity / Run history & transcripts
Mobile remote access (clayrune.io tunnel)
Settings
Keyboard shortcuts
Common tasks (10 recipes — each ends with the [clayrune:...] marker recipe Playdo emits)
Glossary (12 terms)
Troubleshooting (4 known issues with version pointers)
Marker syntax for the assistant (Playdo-only — explains the inline UI control markers)
```

The doc plays double duty: a human reference AND Playdo's system prompt. The Common-tasks section is the load-bearing piece — each recipe ends with the exact `[clayrune:...]` marker, so Playdo highlights the right UI element while it explains.

### Walkthrough rewrite (`WT_STEPS` in `static/index.html`)

Old walkthrough was 19 steps with stale content (Tabs step still listed "Hivemind" as a tab — no longer true; menu steps didn't include Hiveminds + Start Hivemind; no Hivemind sidebar / Scheduler / Run Now / Runs panel coverage). Rewritten to 16 hand-curated steps reflecting current UI:

```
1.  welcome              — opening screen
2.  advanced-picker      — pick power-user features (kept)
3.  sidebar              — Dashboard / Backlog / 🐝 Hivemind / Scheduler / Settings / Shared Rules / Processes
4.  header               — Ctrl+K + agent count + live badge + ? button
5.  toolbar              — Grid/List toggle + filter + density + + New Project
6.  sample-tile          — virtual demo tile (sample project auto-created)
7.  open-modal           — virtual modal demo
8.  tabs                 — Agent / Backlog / Agent Log / Plans / Activity (NO Hivemind here)
9.  agent                — dispatch input + plan approval mention
10. menu                 — three-dot menu: Hiveminds + Start Hivemind + Memory & Rules + Status/Color/Domain/Model + GitHub Sync (mobile: tabs in menu)
11. hivemind-sidebar     — global cross-project Hivemind view (desktop only)
12. scheduler            — Run Now + Runs panel + transcript viewer (desktop only)
13. console              — bottom agent console
14. bottom-tabs          — mobile bottom tab bar (mobile only)
15. cmd-palette          — Ctrl+K
16. ask-playdo           — points at the floating button (NEW)
17. done                 — Settings/cmd-palette/? to re-run; mascot pulse continues until first open
```

(Counts to 17 with the new step — net change vs old: removed 4 granular menu sub-steps + redundant backlog/agent demo steps, added hivemind-sidebar / scheduler / ask-playdo.)

### Ask Playdo — in-app guide assistant (new)

Floating circular button bottom-right of every viewport, always visible. Pulses on first visit until the user opens it once (persisted in `localStorage.playdo_opened`). Mobile sits 70 px above the bottom tab bar.

**Surface** (`static/index.html`):
- Floating FAB: 56 px desktop / 50 px mobile, Playdo mascot icon, accent border.
- `__playdo` modal: chat history + input pinned bottom. Each open is a fresh conversation (no per-session memory in v1, by design — keeps it simple).
- `submitPlaydo()` POSTs to the new endpoint and renders the response.
- `_playdoParseMarkers()` strips `[clayrune:goto/open-modal/highlight]` from the answer + queues the actions.
- `_playdoDispatchActions()` runs them with 350 ms stagger so the user can follow what's happening.
- `_playdoFormatText()` light markdown (bold, inline code, newlines).

**Backend** (`server.py`):
- `POST /api/guide/ask` — single-shot call. Reads `docs/USER_GUIDE.md` as system prompt, runs `claude -p <question> --append-system-prompt <guide> --max-turns 1`, returns `{answer}`. 60 s timeout, 2000-char question cap. No project context, no memory writes, no agent_log entry.
- `GET /assets/<filename>` — new static-file route to serve the mascot icon (and any other repo assets the FE needs).

**Marker protocol** (Playdo emits these inline; FE parses + dispatches):
```
[clayrune:goto view="hivemind"]
[clayrune:open-modal project="abc123"]
[clayrune:highlight selector="#sidebar-item-hivemind" duration=2500]
```
All read-only — no destructive actions in v1. Highlight uses a CSS pulse class (`.clayrune-highlight`) and `scrollIntoView` so the user sees what Playdo means.

**Naming convention** (saved as memory `naming_playdo_clayrune.md`): Playdo = mascot character, Clayrune = product. The marker prefix stays `clayrune:` (product-namespaced); only the user-facing helper is "Ask Playdo."

### Walkthrough trigger fix (was broken since the incognito project was added)

The trigger checked `allProjects.length === 0`, but the auto-created `_incognito` pseudo-project always counts as 1 — so the first-run walkthrough never fired on a fresh install. Fix: filter via `isIncognitoProject` before counting. Surfaced during installer end-to-end testing on a clean WSL Ubuntu where the dashboard rendered empty but no walkthrough kicked in.

### Server restart

Required for the new `/api/guide/ask` and `/assets/...` endpoints. Frontend changes apply on next page load.

### Rollback

- USER_GUIDE.md: just delete `docs/USER_GUIDE.md`. Playdo will return `guide not available` errors but nothing else breaks.
- Walkthrough rewrite: revert the `WT_STEPS` block.
- Ask Playdo: revert `<button id="playdo-fab">` HTML, the `.playdo-*` CSS block, the `// ── Ask Playdo` JS block, the `/api/guide/ask` and `/assets/<path:filename>` server routes.

---

## [2026-05-07b] — Installer scaffold (Claude-driven, browser-only v1)

A new install path designed around Clayrune's own pitch: the user runs one terminal command, Claude CLI does the install. No installer pipeline to build, sign, or maintain across three OSes; cross-platform "for free" because Claude detects the OS, package manager, and Python/Node install paths.

### Architecture

```
user runs:                             ┌─────────────────────────────┐
  curl -sSL clayrune.io/install.sh \   │ install.sh / install.ps1    │
       | sh                            │ (~110 lines each)           │
                                       │  1. verify/install Claude   │
                                       │     CLI if missing          │
                                       │  2. fetch install-prompt.md │
                                       │  3. show 5s abort window    │
                                       │  4. claude --dangerously-   │
                                       │     skip-permissions -p ... │
                                       └────────────┬────────────────┘
                                                    │
                                                    ▼
                                       ┌─────────────────────────────┐
                                       │ Claude executes 6 STEPs:    │
                                       │  1. detect env              │
                                       │  2. clone/pull repo         │
                                       │  3. python venv + deps      │
                                       │  4. node.js (safety net)    │
                                       │  5. create OS launcher      │
                                       │  6. start server + browser  │
                                       └────────────┬────────────────┘
                                                    │
                                                    ▼
                                       Clayrune at localhost:5199
                                       Desktop / Start Menu / Apps
                                       has a clickable shortcut.
```

### New files

`installer/`:
- `install-prompt.md` — the prescriptive Claude prompt, ~200 lines, 6 STEPs. Conservative: does git, pip, package-manager calls, and launches the app. Does NOT modify dotfiles, change system PATH, write outside the install dir, or `sudo` without explanation.
- `install.sh` — macOS/Linux bootstrap.
- `install.ps1` — Windows PowerShell bootstrap.
- `start.sh` — Linux launcher (activates `.venv`, runs `python server.py`, opens browser via `xdg-open`).
- `start.command` — macOS launcher (same role; opens via `open`).
- `start.bat` — Windows launcher (same role; opens via `start http://...`).
- `README.md` — architecture diagram + hosting plan + testing checklist.

`assets/`:
- `clayrune.png` — 1024×1024 RGBA. The Playdo mascot character; doubles as the product / install-shortcut icon. Source for all per-platform icon variants (`.ico`, `.icns`, scaled PNGs); the install prompt generates these on-the-fly with ImageMagick / `sips`.

### Hosting plan

| URL | Source |
|---|---|
| `clayrune.io/install.sh` | `installer/install.sh` |
| `clayrune.io/install.ps1` | `installer/install.ps1` |
| `clayrune.io/install-prompt.md` | `installer/install-prompt.md` |

Domain not yet up. Pre-domain testing uses `raw.githubusercontent.com/.../installer/<file>` with `CLAYRUNE_PROMPT_URL` env var pointing the bootstrap at the right URL.

### Disclosure model

The bootstrap prints the exact `claude --dangerously-skip-permissions -p "<prompt>"` line it's about to execute, with a 5-second Ctrl-C abort window. The install prompt is publicly hosted at `clayrune.io/install-prompt.md` so anyone can audit before authorizing.

### What's not in v1

- **Tauri desktop wrapper** — browser-only for now. The Tauri build path adds a Rust toolchain dependency to step 6 that's not worth the fragility for v1; deferred to a Settings → "install desktop wrapper" follow-up.
- **`.ico` / `.icns` pre-baked** — the install prompt generates these from `clayrune.png` on-the-fly when ImageMagick / `sips` is available. If neither is, the OS launcher uses the default icon (still works). Pre-baking is a polish add.
- **Auto-updater** — not yet. Updates use the same model (`claude "update Clayrune in ~/Clayrune"`); a formal `clayrune.io/update.sh` is a future enhancement.

### Rollback

Delete `installer/` and `assets/clayrune.png`. The existing zip + `install.bat`/`install.sh` source-setup paths in the README continue to work. The Claude-driven install is purely additive.

### Testing checklist

A new install on a clean VM (Windows 11, macOS 14+, Ubuntu 22.04) should:

- [ ] Complete in under 5 minutes with no manual intervention beyond the initial `curl … | sh`
- [ ] End with the browser open at `http://localhost:5199`
- [ ] Place a clickable launcher on Desktop and in the OS app menu
- [ ] Survive a re-run (idempotent — clone becomes pull, deps re-install cleanly)
- [ ] Leave nothing in `/etc`, `/usr`, or system-wide locations
- [ ] Not modify `.bashrc`, `.zshrc`, or system PATH

---

## [2026-05-07] — Scheduled-task UI hang + empty Runs panel

Two related symptoms users hit when the scheduler ran heavily over hours:

1. **Page becomes unresponsive** every so often. Closing & reopening the tab restored it.
2. **No actual run registered in a schedule's "Runs" panel** even after the schedule had clearly fired.

### Symptom 1 — root cause: SSE slot exhaustion via the 15s fallback poll

The 2026-04-27 SSE-slot fix closes the EventSource on `turn_complete` so idle Mode B sessions don't burn one of Chromium's 6 per-origin connection slots. `fetchAgentStatus` was updated to only auto-reconnect for `running`. But the 15s "fallback for missed completions" loop at `static/index.html` (the one that piggybacks `_checkServerRestart`) was still reconnecting for both `running` AND `idle`:

```js
} else if ((ss.status === 'running' || ss.status === 'idle') && !agentEventSources[rh.sessionId]) {
  connectAgentStream(h.projectId, rh.sessionId);
}
```

Server-side, the 30-min stale-session sweep (`server.py:_scheduler_loop` purge block) explicitly skips `running` and `idle` — so idle Mode B sessions accumulate forever (until restart). Each scheduler fire that completes a turn leaves another idle session in `agentHistory`. Within hours, 6+ idle sessions had a live SSE re-opened by the 15s poll → all 6 Chromium slots saturated → `/api/processes`, `/api/config`, `/api/project/<id>/agent_log` etc. queued forever → page hung. Rebuilding `agentHistory` from a fresh page load cleared the slots and the page worked again until the next accumulation.

**Fix** (`static/index.html`): drop the `=== 'idle'` branch from the 15s-poll reconnection block. Mirrors the `fetchAgentStatus` fix. `sendFollowup()` already reopens the stream when the user sends a message.

### Symptom 2 — root cause: trigger info doesn't survive long-lived idle sessions

A scheduler-dispatched session in Mode B finishes its turn → goes idle → process stays alive forever. The stream reader's `finally` block (where `_log_agent_completion` lives) only runs on process exit, so the agent_log entry — the one carrying `trigger_type='schedule'` and `trigger_id=<schedule_id>` — is never written. When the server eventually restarts, the next-startup `_backfill_agent_log_from_transcripts` recreates a row from the Claude transcript on disk, but that helper has no way to recover the trigger info — it's not in the transcript. The `/api/schedule/<id>/runs` filter (`trigger_type==schedule AND trigger_id==X`) then finds nothing, even though the schedule clearly fired.

Verified on `data/projects/day_trading_engulfing_scanner_agent_log.json`: the `3d9ba6f0` schedule had ~10 dispatches in a single day, **0** of which carried `trigger_type='schedule'` in the agent_log; all were `synthesized: True` with empty trigger fields.

**Fix** (`server.py`):

- New `_log_agent_dispatch_pending(session)` helper: at dispatch time, drops a placeholder row into the project's agent_log with `status='in_progress'` and full trigger info (session_id, trigger_type, trigger_id, hivemind ids if present, etc.). `claude_session_id` is empty until completion — Claude assigns it after the first message.
- `_dispatch_agent_internal` calls the helper for non-manual triggers only (manual dispatches don't need correlation and would just double the agent_log write traffic).
- `_log_agent_completion` upserts: looks for an existing row with the same `session_id` and `status=='in_progress'`, removes it, and inserts the finalized entry at the top. Preserves trigger info even though the in-flight row gets replaced.
- New `_reconcile_pending_agent_log_entries()` runs at server startup: any leftover `in_progress` entry is by definition orphaned (no live sessions exist yet), so it gets flipped to `interrupted`. Hooked in `__main__` before the existing transcript backfill so the two helpers don't race.
- Frontend (`static/index.html`): `_runStatusIcon` shows the live accent dot for `in_progress` (matches `running`/`idle`).

**Effect**: a scheduled run shows up in the Runs panel the moment the dispatch happens. Marked `in_progress` while live (accent dot), `completed`/`stopped`/`error` once the session finalizes, or `interrupted` if the server was killed mid-run. Hivemind-orchestrator and hivemind-worker triggers benefit from the same path.

**Rollback**: revert this commit. The existing `manual`-default path in `_log_agent_completion` is unchanged for manual dispatches, so reverting only loses the new pending-row behavior — agent_log shape stays compatible.

**Restart**: server restart required for the backend pieces (helpers + dispatch hook + startup reconcile). Frontend changes apply on next page load.

### Tab strip filter — completed/stopped automated tabs hidden

**Why**: opening a project that had a schedule firing repeatedly showed 8+ near-identical agent tabs ("Run python scripts/he..."). Unusable on mobile, noisy on desktop. Now that scheduled runs surface in the Scheduler's Runs panel + Agent Log, completed automated tabs in the strip are pure noise.

**Files**:
- `server.py` — `agent_status` endpoint now also returns `trigger_type` + `trigger_id` per session.
- `static/index.html` — `fetchAgentStatus` captures the new fields into `agentHistory[].triggerType` + `agentStatusCache[sid].triggerType`. New `getProjectTabSessions(projectId)` filters out `trigger_type ∈ {'schedule', 'hivemind_worker'}` whose status ∈ `{'completed', 'stopped', 'error'}`. `agentPanelHTML` uses this filtered list for the tab strip.

**Behavior**: scheduled / hivemind-worker runs only show as tabs while running. Manual + hivemind-orchestrator tabs unaffected. Completed automated tabs are still reachable via the Scheduler's Runs panel and the Agent Log.

### Runs panel timestamp fix — `started_at` over `ts`

**Why**: after a restart, the Runs panel showed every shutdown-finalized session as "12m ago" because `renderRunRows` was reading `ts` (= finalize time, which becomes uniform for all sessions stopped during shutdown) instead of `started_at` (= dispatch time, which preserves real chronology).

**Fix** (`static/index.html`): `renderRunRows` now picks `r.started_relative || r.started_at || r.ts_relative || r.ts`. Comment explains the pitfall.

### agent_log retention cap (500 entries) + Runs pagination (50 per page)

**Why**: agent_log files grew unbounded — for a schedule firing every 30 min that's ~17k entries/year. Plus the Runs panel was a single scrollable list of up to 200 rows; too much to scan.

**Disk retention** (`server.py`):
- New config `agent_log_max_entries`, default **500**. Set to `0` to disable.
- `_save_agent_log` slices to the most recent N before persisting (newest at index 0). Existing oversized files don't get retroactively trimmed; they shrink the next time anything writes.

**Endpoint pagination** (`server.py`):
- `/api/schedule/<id>/runs` and `/api/hivemind/<id>/runs` now accept `?limit=` (default 50, max 200) and `?offset=` (default 0).
- Response shape changed from a flat array to `{runs, total, offset, limit}` — `total` is the across-all-pages count so the FE can render pagination controls.

**Pagination UI** (`static/index.html`):
- New `renderRunsPagination(total, offset, limit, pageFnTemplate)` helper renders `« ‹ Prev   Page X of Y · N total   Next › »` below the rows. Buttons disabled at bounds.
- `toggleScheduleRuns` delegates to `loadScheduleRunsPage(scheduleId, projectId, offset)`.
- `openHmRunsModal` delegates to `loadHmRunsPage(hivemindId, projectId, role, wsId, offset)`.
- Each panel resets to page 1 on (re-)open.
- New CSS class `.runs-pagination`.

**Restart**: server restart required (response shape change). Frontend on next page load.

---

## [2026-05-06] — Hivemind global surface, trigger-aware run history, sizeAgentChat fix

Three threads of work in one session.

### Hivemind: global cross-project surface (replaces per-project tab)

**Why**: Hivemind was tucked into a per-project modal tab. The cross-project comms / orchestration story is the differentiator that justifies a first-class surface, parallel to Backlog and Scheduler in the sidebar — not a tab inside a single project.

**`static/index.html`**:

- **Sidebar entry "Hivemind"** between Backlog and Scheduler (🐝 icon). `sidebarNav('hivemind')` → `openAllHiveminds()` → synthetic modal `__all_hivemind`.
- **Cross-project list view** (`renderAllHiveminds`): status filter (Active / Paused / **Stale** / Completed / All), project filter (auto-populated from data), search box, count, **+ New Hivemind** action.
- **Card per hivemind**: status pill, short ID hash (`#abc12345` so visually-identical titles in the same project are distinguishable), title, project badge (clickable → filter), updated-relative, pause/stop/resume controls. Below: a **planner/worker tree mini-viz** — orchestrator badge → trunk → row of workstream chips colored by status (✓ done, ● active, ⏳ blocked, ✖ failed, ○ pending). Stats row: workstreams / done / active / findings.
- Click a card → existing `openHivemindDashboard()` detail modal (left untouched in this pass).
- **Mobile bottom-tab bar**: Settings slot replaced with Hivemind. Settings remains reachable via the avatar tap on the mobile app bar (`mc-avatar-btn` already routed there).
- **Per-project Hivemind tab REMOVED** from the modal tab strip (`validTabs` no longer includes `'hivemind'`; stale `modalActiveTab` values auto-migrate to `'agent'`). Replaced with two entries in the project's 3-dot menu, separated by a divider:
  - **🐝 Hiveminds** → opens global view filtered to this project (status: All).
  - **✨ Start Hivemind** → switches to Agent tab, opens a fresh session, **auto-dispatches** the setup prompt so the user lands directly in an active conversation (not a populated form). Earlier draft just filled the textarea; users mistook it for a misdirected new-session screen.

**Stale heuristic**:

- Frontend `_hmEffectiveStatus(hm)` in `static/index.html`: if `status === 'active' || 'paused'` and `updated_at > 24h` ago, render as **stale** (grey badge, separate filter option, **▶ Restart** control, tooltip explains: "Marked stale because no activity for >24h"). Keeps underlying status intact in the data — only display + filter behavior changes.
- Server-side reconciliation (`server.py:_hm_reconcile_stale_on_startup`): one-shot pass at startup that transitions any `active` hivemind whose `updated_at > _HM_STALE_HOURS (24)` to `status='stale'` in the manifest on disk. Only touches `active` — `paused` is intentional idle. Prints `[hivemind-reconcile] marked N long-active hivemind(s) as 'stale' (>24h idle)` if any transitions happen.

### Trigger-aware run history (scheduler + hivemind)

**Why**: scheduled / hivemind-spawned runs were invisible after restart — the live conversation context disappeared, the agent log entries weren't tagged with what triggered them, and there was no surface that said "show me the last 10 runs of *this* schedule" or "what did each worker actually do?". Server log persisted but wasn't navigable.

**`server.py`**:

- **Two new fields on every `agent_log` entry** (`_log_agent_completion`): `trigger_type` (`manual` | `schedule` | `hivemind_orchestrator` | `hivemind_worker`) and `trigger_id` (schedule_id, hivemind_id, or workstream_id depending on type). Default `'manual'`/`''` for direct user dispatch. Old entries continue to work (defaults applied at read time).
- **`_dispatch_agent_internal` extended** with `trigger_type='manual'`, `trigger_id=''` kwargs that flow into the session dict; both Mode A and Mode B are stamped.
- **Scheduler (`_scheduler_loop`)** now passes `trigger_type='schedule'`, `trigger_id=sched['id']` on every fire.
- **Hivemind orchestrator + worker spawn paths** stamp `trigger_type='hivemind_orchestrator'`/`'hivemind_worker'` directly on the inline session dicts (those paths construct sessions inline, not via `_dispatch_agent_internal`).
- **New endpoints**:
  - `GET /api/schedule/<id>/runs?limit=` — agent_log entries where `trigger_type='schedule'` and `trigger_id=<id>`. Resolves project via the schedule record.
  - `GET /api/hivemind/<id>/runs?role=&ws_id=&limit=` — falls back to existing `hivemind_id`/`hivemind_ws_id`/`hivemind_role` fields, so historical entries (predating this session) work too. `role=orchestrator` / `role=worker` filter by role; `ws_id` scopes to a specific workstream.
  - `GET /api/project/<pid>/transcript/<csid>` — read-only parsed transcript (user msgs + assistant text + `[tool: X]` markers) for the read-only viewer. Uses new helper `_parse_transcript_messages` + `_find_transcript_file` (resolves Claude Code's `~/.claude/projects/<encoded-cwd>/<csid>.jsonl` with both `_`→`-` encoding variants).
- **`POST /api/schedule/<id>/run-now`** — manually fire a schedule's task without disturbing its cadence. Updates `last_run` for visual feedback, leaves `next_run`/`enabled` alone (it's an *extra* dispatch on top of the normal cycle). Stamps `trigger_type='schedule'` so the resulting run shows up in the schedule's Runs panel.

**`static/index.html`**:

- **Shared transcript viewer modal** (`openTranscriptViewer`, `__transcript_<csid>` synthetic id): renders user/assistant blocks with role labels and inline `[tool: X]` markers. Cached per csid in `_transcriptCache`.
- **Shared row renderer** (`renderRunRows`): timestamp · status icon · summary, click → transcript viewer.
- **Scheduler card "Runs" button** + inline expanding panel (`toggleScheduleRuns`). Panel sits below the card with surface2 background.
- **Scheduler "▶ Run Now" button** at the far right of the action row (kept apart from "Runs" by Edit + Del to avoid label collision). Also available in the Edit form (only when editing existing).
- **Hivemind detail dashboard**:
  - Workstream detail view: **Runs** button next to the workstream title → opens `__hm_runs_<hivemind>_worker_<ws>` modal listing runs for that workstream.
  - Overview view: **Orchestrator Runs** button in the actions row → opens orchestrator-only runs modal.
- New CSS for `.run-row`, `.transcript-msg`, `.transcript-tool`, `.runs-panel`, `.runs-empty`.

### Fix: sizeAgentChat over-allocation cut Send button bottom border

**Symptom**: Send button's bottom green border was clipped by ~6 px after some refresh cycles. Diagnostic showed `agent-output: h=521` when it should have been 500 — over-allocated by exactly 21 px, matching agent-chat's `scrollH − clientH` overflow.

**Cause**: `sizeAgentChat` set `agent-output` to `flex: 0 0 <X>px !important` based on `desiredOutH = chatHeight − sepH − inputH`. `inputH` came from `chatInputEl.offsetHeight`, which returned the **squashed** value left over from the previous over-allocation (47 instead of natural 68). Each refresh fed back the smaller value → desiredOutH grew by 21 px → chat-input got squashed *more* → Send button's bottom border drifted past `agent-chat`'s `overflow: hidden` boundary. Classic measurement feedback loop.

**Fix** (`static/index.html:sizeAgentChat`):

1. Before measuring, `removeProperty` on output's `height` / `max-height` / `flex` / `min-height` so the natural-flex layout is what gets measured.
2. Compute `inputH` as `Math.max(offsetHeight, scrollHeight, rowOffsetHeight + computedPadding, 80)`. Three independent signals plus an 80 px safety floor (well above natural ~68 px). Pathological measurement can no longer over-allocate the output area.

### Rollback

- **Hivemind elevation**: revert the `static/index.html` block search-anchored at `// ── Cross-project Hivemind view ──` plus the sidebar HTML entry, the `sidebarNav('hivemind')` branch, and the `_hm_reconcile_stale_on_startup` call in `server.py`'s `__main__`. Re-add the modal-tab `<div>Hivemind</div>` line and the `<div data-tab="hivemind">` panel in `modalContentHTML`.
- **Run history**: drop `trigger_type`/`trigger_id` from `_log_agent_completion`, the kwargs from `_dispatch_agent_internal`, the scheduler/hivemind dispatch sites' stamping, and the four new endpoints (`schedule_runs`, `hivemind_runs`, `get_project_transcript`, `schedule_run_now`). Frontend: revert the Runs/Run Now buttons in `refreshScheduleList`, the buttons in `buildWsDetailHTML`/`buildHmOverviewHTML`, and the shared `openTranscriptViewer`/`renderRunRows`/`openHmRunsModal`/`runScheduleNow`/`toggleScheduleRuns` block.
- **sizeAgentChat fix**: revert the `removeProperty` block + replace the multi-signal `inputH` calc with the original `chatInputEl.offsetHeight`. Note: doing this revives the Send-button-clipping feedback loop.

---

## [2026-05-05] — Sticky modals, conversation drag fix, and remote server restart

Three threads of work shipped together (commit `5ce48eb`):

### Modal persistence (`static/index.html`)

- **`mc_open_modals` snapshot in `localStorage`**: stores `[{projectId, left, top, minimized}]` for every open project modal. Saved on open / close / minimize / restore / drag-end / `beforeunload`. Restored on page load right after `fetchProjects()` resolves. Skipped on mobile (full-screen modals + bottom-tab nav assume a clean slate). Filters out transient synthetic modals (`__terminal_*`, `__hivemind_*`, `__settings`, etc.).
- **`mc_modal_prefs` in `localStorage`**: per-project `{width, height, zoom}`, applied every time the modal opens. Captured by the existing `ResizeObserver` on `.modal-content` (catches corner-drag + pinch-resize), the `Ctrl+wheel` zoom handler, and pinch-zoom. Debounced 250 ms; flushed on `beforeunload` and before any in-app restart so the snapshot survives.
- Open-project modal helper extended with optional `restoreState` arg so startup restore (per-instance position) and Settings-sidebar reopen (centered, prefs only) can share the same code path.

### Conversation input drag (`static/index.html`)

- Dragging the agent chat input separator now resizes the output area in lock-step with the textarea instead of leaving it frozen and snapping a few seconds later (the snap was the deferred flex-layout finally catching up on the next periodic refresh).
- `sizeAgentChat` now drives `agent-output` height **explicitly** via `style.setProperty('height', …, 'important')` + matching `flex: 0 0 <h>px`. CSS `flex: 1` alone wasn't reliably reflowing when the textarea's inline `style.height` changed; `!important` beats whatever cached layout the browser still had from when the textarea was its smaller size.
- `separatorDragMove` now (a) updates `textareaHeights[id]` in lock-step with the live drag so any refresh that fires mid-drag restores the in-progress height instead of the default `rows="1"`, and (b) calls `sizeAgentChat` on every step so the layout follows the drag instead of waiting for the next periodic refresh.
- The "scroll position jumps up" bug: tightened the resize re-pin tolerance to ≤8 px (vs. the lazy 80 px window `_isAgentOutputPinned` uses for new-line auto-scroll, which is left untouched). Without this, a user reading 30–70 px above the bottom got snapped to the absolute bottom every refresh, which they perceived as the text jumping up by the gap they had scrolled.

### Remote server restart (`server.py`, `static/index.html`)

The user can now restart the Mission Control Python process from any open dashboard, including mobile via the `clayrune.io` tunnel. Designed for the "I just deployed a fix and I'm on my phone — let me restart" workflow.

**Endpoints** (`server.py`, just before `if __name__ == '__main__'`):

- `GET /api/system/restart/status` — returns `{active_sessions: [...], active_hiveminds: [...]}` with project names and task previews. Powers the warning modal so the user sees what would be killed before confirming.
- `POST /api/system/restart` body `{confirmed: true, force?: bool}`:
  - 400 if not confirmed.
  - 429 if a restart was triggered in the last 30 s (rate limit).
  - **409 with the live blocker list** if anything is still active and `force` isn't set — closes the GET → POST race window where a cron or hivemind could spawn a fresh session between the user seeing the modal and clicking confirm.
  - 202 + audit log + async restart thread otherwise.
- `GET /api/system/heartbeat` — `{started_at, pid, uptime_seconds}`. Cheap probe (no disk/DB). Dashboards compare `started_at` against their first-seen value to detect a restart.

**Restart thread** (`_perform_server_restart_async`):

1. Sleeps 400 ms so the 202 actually reaches the client.
2. Calls `_stop_all_sessions_for_restart` → graceful `_stop_session` (Mode B closes stdin, Mode A flips status), then `_kill_proc_background` (existing tree-kill helper).
3. Waits up to 3 s for children to die.
4. Appends to `data/restart_log.json` (capped at 200 entries; gitignored).
5. **`subprocess.Popen([sys.executable] + sys.argv, close_fds=True, …)`** then `os._exit(0)`.

**Why Popen instead of `os.execv`** — *the non-obvious lesson of this session.* On Windows, `os.execv` is implemented as spawn-new-then-exit-old AND the new process inherits open file handles. Worse, every child process we spawned (Mode B agents, terminal sessions) **also** held the listening socket FD via inheritance — so port 5199 stayed bound until every descendant died, well past the 15 s the new instance was willing to wait. Symptom: the new process bailed in `_check_port_conflict` saying `Held by PID(s): X (claude.exe)`. `subprocess.Popen([…], close_fds=True)` starts the new instance with a clean handle table, sidestepping the whole inheritance chain. POSIX uses `start_new_session=True`; Windows uses `CREATE_NEW_PROCESS_GROUP | CREATE_NEW_CONSOLE` so the new server gets a visible terminal window the user expects.

**Restart-aware port-conflict bypass** (`_check_port_conflict`):

- Before exec/spawn the parent sets `MC_RESTART_FROM_PID = <our_pid>` in env.
- The new instance recognizes the marker and polls the port every 300 ms for up to 15 s, waiting for the dying parent to release it. Only after that timeout does it fall through to the hard-abort path.
- Marker is cleared on successful bind so a normal subsequent launch (no in-progress restart) behaves like before.
- Conflict-message diagnostics enriched on Windows: now lists image name next to PID (`Held by PID(s): 42836 (claude.exe)`). POSIX equivalents (`ss -lntp`, `lsof -i`) noted as TODOs in code.

**Cross-dashboard restart detection** (`static/index.html`):

- Every dashboard probes `/api/system/heartbeat` (`_checkServerRestart`) on SSE drop AND in the existing 15 s fallback poll. If `started_at` changed since first-seen, calls `_handleServerRestart` which reuses the same `showRestartingOverlay` flow as the device that triggered the restart.
- Without this, dashboards that didn't trigger the restart would see SSE drop, retry 3×, mark sessions `'error'`, and the project tile turns "Blocked" (via `friendlyStatus` mapping `c==='error'` → `'stuck'`) until manual refresh.
- The SSE error handler now probes the heartbeat **before** incrementing the retry counter. If a restart is detected, skips the retry/error cascade entirely and reloads instead of marking the session `'error'`.

**UI** (`static/index.html`):

- Settings → new **"Server"** section with a red **"Restart server"** button.
- `openRestartConfirmation` fetches the live blocker list, builds a modal showing each active project + task preview + hivemind worker counts. Two-button confirm: "Cancel" / "Stop all and restart" (or just "Restart" if nothing's active).
- `performRestart(force)` POSTs and handles 202 / 409 / 429. On 409 (race) the modal auto-reopens with refreshed state.
- `showRestartingOverlay` flushes `mc_open_modals` + `mc_modal_prefs` synchronously before the page reload, draws a backdrop spinner overlay, polls `/api/projects` every 1 s starting at +1.2 s, reloads when it 200s. The modal-restore code then brings back open conversations and their positions/zoom from `localStorage`.

**Auth model**: same as the rest of the app — localhost is unauthenticated by design (your machine), tunneled requests have already passed CF Access OTP. No new auth surface introduced.

### System-prompt awareness (`server.py:_clayrune_universal_capabilities`)

- New **Scheduler** entry: every agent now sees Clayrune's local `/api/schedules` endpoints in its preamble, framed as the long-term option next to the Anthropic `/schedule` skill (short, in-session). Picker rule: "if it should still fire after this conversation ends, use Clayrune's local scheduler; if it's a tight loop tied to current work, use `/schedule`."
- New **API discovery** hint: tells agents to grep `server.py` for `@app.route` instead of guessing endpoint names like `/api/cron` or `/api/jobs`. Triggered by an observed failure mode where an agent probed five wrong paths before finding the real one.

### Rollback

The four pieces are independent enough to revert separately:

- **Modal persistence**: clear `mc_open_modals` + `mc_modal_prefs` from `localStorage`; remove the helper block in `static/index.html` (search for `_loadModalPrefs`).
- **Drag fix**: revert the `sizeAgentChat` block (search for `setProperty('height'`) and the changes inside `separatorDragMove` (live `sizeAgentChat` call + cache write).
- **Remote restart**: remove the four endpoints (`/api/system/restart{,/status}`, `/api/system/heartbeat`) and helpers from `server.py`, plus the Settings "Server" section + restart-related JS in `static/index.html`. Optionally also remove the `_check_port_conflict` `MC_RESTART_FROM_PID` branch.
- **System-prompt awareness**: remove the two new entries from `_clayrune_universal_capabilities`.

---

## [2026-05-04] — Diagram polish: Excalidraw bridge restored, de-sketched, orphan-error sweep

Iterative tightening of the Mermaid → Excalidraw rendering pipeline introduced
on 2026-05-03. Commit `44772f2` had brought in the Excalidraw bridge for a
polished aesthetic; commit `3a088cc` reverted it after sequence-diagram
rendering bugs (strikethrough lifelines, literal `<br/>` text, mono-color
output). This session restores the bridge but pivots away from the hand-drawn
look that made the diagrams read as childish.

### Diagram rendering (`static/index.html`)

- **Restore Excalidraw bridge** (commit `b63ec46`, revert of `3a088cc`). Keeps
  the Excalidraw layout + element model — but with the changes below, no
  longer with the Roughjs sketch effect.
- **Mermaid `look: 'handDrawn'` → `'classic'`** (line ~25). Fallback path
  (sequence/state diagrams that Excalidraw can't parse) now renders with
  clean strokes instead of wobbly Roughjs lines.
- **Excalidraw element post-processor** in `_renderViaExcalidraw` (line ~6967):
  after `convertToExcalidrawElements(skeleton)`, every element is mutated
  before the SVG export:
  - `roughness = 0` — straight strokes, no sketch wobble
  - `fillStyle = 'solid'` — kills hachure / cross-hatch fills
  - `strokeStyle = 'solid'` (preserving any explicit `dashed` / `dotted`
    intent the source author set)
  - `fontFamily = 2` (Helvetica) — replaces Excalidraw's default Virgil
    "hand-drawn" font on text + arrow labels. Without this, diagrams still
    read as whiteboard scribbles even with clean strokes.
- **Orphan "Syntax error" sweep** — Mermaid v11 (and `parseMermaidToExcalidraw`
  which uses Mermaid's parser internally) injects an error SVG into `<body>`
  when its parser fails, and never cleans it up. They accumulate on the page
  over the lifetime of the tab as visible toast-like cards.
  - New helper `_sweepOrphanMermaidNodes()` (line ~6961). Selector matches
    both `<svg>` and `<div>` direct children of `<body>` whose id starts with
    `mermaid-` / `dmermaid-`, gated by a textContent regex
    `/Syntax error|mermaid version/i`. The textContent gate is critical:
    Mermaid v11 keeps its own *working* sandbox div on body with the same
    id prefix and reuses it across renders. Removing that crashes the next
    render with `Cannot read properties of null (reading 'firstChild')`.
    The sandbox is empty between renders, so the error-text gate never
    matches it.
  - Called before + after every render attempt (Excalidraw and Mermaid
    paths), plus a one-shot sweep at lib-load to clear pre-existing orphans.

### User-facing color guidance

- `classDef` color rules in flowcharts are often stripped by the Excalidraw
  bridge — the polished look comes at the cost of some Mermaid styling
  expressiveness. Inline `style <node> fill:#...,stroke:#...,color:#...` per
  node is the reliable path; classDef should be treated as best-effort.
- Color-by-role convention used in this session's example diagrams:
  - Cream + brand-orange = your own services / compute
  - Tan + burnt-orange = caching layer
  - Sage green = persistent storage / data layer
  - Purple = async / messaging
  - Blue = observability
  - Red = secrets / crypto
  - Pale slate = external systems

### Limitations (intentional, not in scope)

- AWS architecture icons via Mermaid's `architecture-beta` diagram type are
  not enabled. Would require `mermaid.registerIconPacks([...])` wiring + an
  Iconify pack import + a bypass of the Excalidraw bridge for that diagram
  type (Excalidraw can't parse `architecture-beta`). Deferred.
- Mobile rendering of the Excalidraw bridge has known visual issues; the
  fix above is desktop-first. Tracked separately.

## [2026-05-01] — Rebrand to Clayrune + operator dashboards + scheduler timezone fix

Multi-day milestone session. Public-alpha gate is unblocked, ops surfaces are
in place, branding is unified.

### Major

- **Rebrand: Mission Control → Clayrune** (Model B, full product rename). All
  user-visible surfaces flip:
  - Window/page `<title>`, sidebar logo (orange tile + serif "C" + "CLAYRUNE"
    wordmark — both hardcoded `#e8824a` so they're independent of the user's
    selectable theme accent)
  - Settings → Remote Access labels, walkthrough copy, sample-project text
  - `/v1/connect` page header + footer + redirect toast
  - `/v1/admin` operator-dashboard title + footer
  - `/api/mc-callback` page (success + error templates)
  - `/_mc/name-device` page footer
  - OpenAPI title (`Clayrune — control plane`)
  - Tauri window title
  - CF Access app name template (`Clayrune - <hostname>` — visible in CF
    dashboard + OTP emails)
  - Attestation error messages (`Clayrune version not registered`, etc.)
  - Cloud Monitoring dashboard JSON `displayName`
  - Favicon SVG (white "C" on orange tile)
  - Mobile app-bar avatar fallback ("C")

  Backend identifiers explicitly kept as Mission Control:
  - Python packages `mc_remote`, `mc_remote_iface`, `mc_tunnel`
  - Env-var prefix `MC_*`
  - Windows Credential Manager namespace `mission-control-remote`
  - Tauri `productName` + bundle identifier `com.missioncontrol.desktop`
  - GitHub repo, Cloud Run service name, GCP project name
  - Agent system prompts mentioning "managed by Mission Control"
    (`server.py:1417-1498`)

- **Operator dashboard** at `https://api.clayrune.io/v1/admin` (`routes_admin.py`):
  - Self-contained HTML page; Firebase Google signin gated by email allowlist
    in `MC_CP_ADMIN_EMAILS` env (default `leviran1@gmail.com`)
  - Aggregates Firestore `users/` + `devices/` in a single scan
  - Summary cards (users / devices / online now / as-of) + per-user expandable
    section with device tables, online/offline pills, tier + bandwidth use
  - Endpoint `GET /v1/admin/data` returns JSON; HTML page consumes it
  - Wired into `main.py` (was commented-out skeleton)

- **Cloud Monitoring dashboard** for the control plane:
  - 8-tile mosaic: request rate (stacked by 2xx/4xx/5xx), error rate
    (4xx + 5xx), latency p50/p95/p99, active container instances, CPU
    utilization, memory utilization, Firestore reads, Firestore writes
  - Reproducible JSON config at `control_plane/monitoring/control_plane_dashboard.json`;
    re-create via `gcloud monitoring dashboards create --config-from-file=...`
  - Live at https://console.cloud.google.com/monitoring/dashboards/builder/76f6aa3d-607a-4646-a043-192faf6bb527?project=clayrune

### Bug fixes

- **Scheduler timezone fix** (`server.py:_compute_next_run`):
  - Previously, daily-schedule "time" field and cron expressions were
    interpreted as UTC time-of-day. User entered "09:00" intending wall-clock
    PT, schedule fired at 02:00 PT.
  - Now uses `datetime.now().astimezone()` (local-aware) as the time-of-day
    reference for `daily` + `cron`. `interval` and `once` paths are
    tz-agnostic so unchanged. Returned `next_run` is still UTC ISO+Z so the
    scheduler loop and frontend `new Date(...)` continue working.
  - Frontend form labels show host TZ abbreviation (e.g. "Time (PDT)") via
    `Intl.DateTimeFormat`-derived short name; schedule list descriptions
    append the same.
  - Migration: pre-fix daily/cron schedules will fire at the literal time the
    user originally typed (their original intent). Re-saving recomputes
    `next_run` correctly.

- **Device-token auth on `/api/remote/{devices,sessions,…}`** (commit `492309a`):
  - After Firebase Auth shipped, `/api/remote/devices` and `/sessions` still
    required `MC_REMOTE_DEV_EMAIL` env var to authenticate to the CP. Settings
    panel showed "Couldn't load devices: MC_REMOTE_DEV_EMAIL not set" after a
    successful Firebase enrollment.
  - CP `_resolve_user()` now accepts a third auth path: `X-MC-Device-Auth:
    <device_id>:<enrollment_token>`. Header verifies the device row exists,
    isn't revoked, and the enrollment_token hash matches; resolves to the
    owner's user_id from the device row.
  - MC client `_auth_headers()` picks device-token from keystore; falls back
    to email if keystore is empty. New helper `_cp_auth_kwargs()` in
    `server.py` encapsulates the fallback chain. All four `/api/remote/*` call
    sites + the auto-cleanup loop now use it.

## [2026-04-30] — Firebase Auth + custom domain + CI/CD

### Major

- **Browser-mediated enrollment via Firebase Auth** — replaces the
  `MC_CP_DEV_AUTH=1 + X-Dev-User-Email` shim with a real Google-signin flow.
  - New CP endpoints: `GET /v1/connect` (HTML signin page with Firebase Web
    SDK), `POST /v1/signin/start` (registers enrollment_intent), `POST /v1/signin/complete`
    (verifies Firebase ID token + drives provisioning).
  - `_verify_firebase_token()` uses `firebase_admin.auth.verify_id_token()`;
    lazy SDK init reads `FB_PROJECT_ID` env so token verification matches the
    Firebase project (`clayrune-49e57`) which is distinct from the GCP project
    (`clayrune`).
  - Extracted `_do_enroll_after_auth()` from `/v1/enroll` so the new flow
    reuses the same CF + Firestore choreography.
  - MC client: `connect_url()` builds `<cp>/v1/connect?pub=...&nonce=...&callback=...`;
    callback flow unchanged.
  - End-to-end verified: Disconnect → click Enable → Google signin → username
    pick → redirect → green Online.

- **Custom domain `api.clayrune.io`** — Cloud Run domain mapping with
  Google-managed cert. CF Origin Rules path was abandoned (Host-header
  override is paid-plan-only on CF); DNS-only CNAME → `ghs.googlehosted.com`
  with no CF proxy works on free tiers both sides.

- **CI/CD via GitHub Actions** — push-to-main on `control_plane/**` triggers
  Cloud Build + Cloud Run deploy via Workload Identity Federation (no JSON
  keys committed). After hitting Cloud Build's source-upload bucket legacy-IAM
  wall, the workflow uses `docker build` directly on the runner instead.
  Service account `ci-control-plane@clayrune.iam.gserviceaccount.com` with
  least-privilege roles. WIF pool restricted to `ronle/*` repos via attribute
  condition.

### Polish

- Added CP-warmup ping at MC startup (`_warmup_control_plane` daemon) to mask
  Cloud Run cold-start on first user interaction.
- New admin CLI `python -m control_plane.force_cleanup --username X` for
  emergency state wipes (CF + Firestore for a given username, `--dry-run`,
  `--keep-username`).
- `_force_cleanup_for_hostname()` confirmed collision-only (was already, but
  doc was stale).

## [2026-04-29] — Device naming + auto-cleanup loop

### Major

- **Per-device naming flow** — when a phone/browser hits `<user>.clayrune.io`
  after CF Access OTP, MC's `before_request` hook detects the CF tunnel
  headers, extracts the session nonce from the JWT, and if unlabeled redirects
  to `/_mc/name-device`. Self-contained HTML form with UA-derived suggestion
  chips ("My iPhone", "My Phone", "Work Laptop"…). Labels stored at
  `data/session_labels.json` keyed by CF Access nonce.
- **Retroactive renaming** — clickable "Name this session…" link on each
  unnamed row + small "rename" link on labeled rows.
- **Auto-cleanup loop** (`_session_label_enforcer_loop`, 60 s interval) tries
  strict per-session revoke for unnamed sessions older than 10 min. Aborts
  pass on first `per_session_unsupported` so named sessions are never nuked.
  Verified: CF doesn't expose per-session revoke for our token (4 API shapes
  return 405); loop fails safe and surfaces a "per-session revoke unsupported
  by CF" hint in the UI. "Sign out everywhere" remains the working tear-down.
- **CP `/v1/sessions/{id}/revoke?strict=1`** mode — returns 503 instead of
  falling back to revoke-all when per-session is unsupported. Tries 4 known
  CF API shapes (POST/DELETE × full-name/nonce-only) before giving up.

## [2026-04-28d] — Revert [2026-04-28c]: restore live auto-pin during agent streaming

User tried `[2026-04-28c]`'s "stay put while the agent streams" behavior and didn't like it. Reverted `appendAgentLine` to its prior policy: when the user is pinned (within 80 px of bottom), every new agent line snaps `scrollTop` to `scrollHeight`. The "scroll up to read older content" guard from `[2026-04-27c]` is still in place — only the `wasPinned` branch is unchanged. No code from `[2026-04-28b]` (the chat-drift fix in `sizeAgentChat`) was touched.

## [2026-04-28b] — Stop the agent chat from drifting up a few lines every poll

### Symptom
Every few seconds the conversation window jumped a few lines above where it had been. Worse when the user had dragged the chat-input separator to make the textarea taller.

### Root cause
The agent-panel header (`<div style="display:flex;...flex-wrap:wrap">` containing the status dot + label + Stop + token badge + activity ticker + plan-file btn + popout) is wrap-enabled. The `token-badge` text changes every second as elapsed time updates ("1m 30s" → "1m 31s") — when its rendered width crosses the wrap threshold by even a pixel, the row flips between 1-line and 2-line layout, changing the header height by ~24 px.

`sizeAgentChat` runs on every `refreshModalById` call (status polling tick, focus, etc.) and computes `used = Σ panel.children.offsetHeight (excluding chat) + paddings`. When the header flipped layout, `used` shifted by 24 px, `chat.style.height = available - used - 8` shifted, the `.agent-output`'s `clientHeight` (`flex: 1` inside chat) shifted, and the auto-scroll branch — `if (wasPinned) out.scrollTop = out.scrollHeight` — re-snapped to bottom on a smaller/larger viewport. Result: the visible content appeared to drift up or down by a few lines on every poll. With the textarea dragged taller, output was smaller, so the same 24 px shift was a bigger fraction of view → more obvious.

### Fix (`static/index.html`, `sizeAgentChat`)
- Guard the height write: only set `chat.style.height` if the new value differs from the existing one by more than 4 px. Steady-state polls become no-ops; legitimate resizes still apply.
- Auto-scroll only fires when the chat height actually changed (or on fresh mount). The `requestAnimationFrame` follow-up is also gated on fresh mount, since the post-frame re-snap was masking the same drift.

### What this does NOT fix
- The header itself can still wrap. If you want to *prevent* the wrap entirely, set `flex-wrap: nowrap` on the status bar or hide the activity ticker on narrow modals. Out of scope here — the goal was just to stop the wrap from cascading into the chat scroll.
- `appendAgentLine` is unchanged. New SSE output still pins the user to bottom (when they're already there). Only the polling-driven re-pin is gone.

### Rollback
Revert `sizeAgentChat`'s tail block back to:
```js
chat.style.height = chatHeight + 'px';
if (out && wasPinned) {
  out.scrollTop = out.scrollHeight;
  requestAnimationFrame(() => {
    out.scrollTop = out.scrollHeight;
    if (freshMount && out.scrollHeight > 0) out.dataset.scrollInitialized = '1';
  });
}
```

## [2026-04-28] — Backfill agent_log from Claude transcripts on startup

### Symptom
Sessions that ran for hours via the MC interface but were still mid-flight when the server was restarted disappeared from the Agent Log tab. The Claude transcript on disk was intact, but Mission Control had no record of the session because `_log_agent_completion()` only runs from the stream reader's `finally` block — and that block never fires when the Python process is killed before the agent ends. The user observed this after talking to MC overnight on mobile, then restarting the desktop app the next morning: the session was gone from Agent Log even though the conversation transcript still existed.

### Why it happened
MC's `<pid>_agent_log.json` is the only data source for the Agent Log tab. It is written exclusively by `_log_agent_completion()`, called from the Mode A and Mode B stream readers when their `proc.wait()` returns. A killed server process kills the reader threads before they reach that call. The Claude transcript in `~/.claude/projects/<encoded-cwd>/<csid>.jsonl` survives because Claude Code writes line-by-line, but MC's "I dispatched this" record was strictly in-memory until finalization. This also blocks `_revive_from_agent_log` (added in `[2026-04-27e]`) from finding the session: with no log entry, there's nothing to revive from.

### Fix (`server.py`)
- **New `_backfill_agent_log_from_transcripts(project_id, project)`** (placed right above `_revive_from_agent_log`): scans `~/.claude/projects/<encoded-cwd>/*.jsonl` for the project, compares each transcript's `claude_session_id` (the .jsonl filename) against the set of `claude_session_id`s already in `<pid>_agent_log.json`, and inserts a synthesized entry for any missing transcript newer than the configured age cutoff. Entries are tagged `synthesized: True` and `status: 'interrupted'`. `session_id` is left empty (MC never owned them); the "Continue" button in the Agent Log tab keys off `claude_session_id` so it still works.
- **New `_backfill_all_agent_logs()`** iterates every project and runs the per-project backfill. Called once at server startup in a daemon thread so `app.run()` isn't blocked.
- **Three new config flags**:
  - `agent_log_backfill_enabled` (default `True`) — gates the whole feature.
  - `agent_log_backfill_max_per_project` (default `200`) — caps how many transcripts to scan per project.
  - `agent_log_backfill_max_age_days` (default `60`) — only synthesize entries for transcripts modified within this window. Older transcripts stay invisible to keep the Agent Log focused on recent work.

### Verification
Dry-run against `mission_control_agent_log.json` (291 existing entries, 41 known `claude_session_id`s) found 25 missing transcripts within the 60-day window, including `03ffec41-b384-4bcd-88a5-c2c066e9a308` — the overnight conversation that prompted this fix. After server restart, those 25 will appear in the Mission Control project's Agent Log tab with their first user message as the task label, last user message as the summary, real turn counts, and `[interrupted]` status.

### Edge cases worth watching
- **Synthesized entries are NOT revivable via `_revive_from_agent_log`**: that helper looks up by MC `session_id`, and synthesized entries leave `session_id` empty (since MC never owned the session). The "Continue" button in the Agent Log tab is the supported path and works because it keys off `claude_session_id`. If you want synthesized entries to be revivable too, give them a fresh `session_id = 'synth-' + csid[:8]` and the existing revive lookup will find them — left out of this commit because synthesized sessions in flight could still be running in another MC process and we don't want to fight over them.
- **Duplicate entries on later finalization**: if a synthesized entry's session is still alive in another MC process and that process eventually finalizes it, `_log_agent_completion` will insert a *new* entry with the same `claude_session_id`. They coexist; the latest entry sorts to the top, the synthesized entry stays as historical record. Acceptable for now.
- **System-reminder noise in last_user labels**: `_extract_user_text` returns the raw user text including `<system-reminder>` blocks attached by the harness. Some synthesized entries' summaries will start with `<system-reminder>...`. Pre-existing issue (the Resume Picker shows the same data) — punted to a future polish pass.
- **Performance**: 200-transcript cap × O(turns) per scan. On a project with 35 transcripts the dry-run completed in well under a second. Scales to a few hundred projects fine.

### Rollback
Three options, increasing in cost:
1. **Toggle off**: edit `data/config.json`, add `"agent_log_backfill_enabled": false`. Restart MC. The synthesized entries from prior boots stay in the log files (you can identify them by `synthesized: true` and remove them by hand if desired); no new synthesis happens.
2. **Remove the call site**: delete the `threading.Thread(target=_backfill_all_agent_logs, ...)` line in `if __name__ == '__main__'`. Helpers stay but are unused.
3. **Full revert**: also delete `_backfill_agent_log_from_transcripts` and `_backfill_all_agent_logs` (the two functions added right above `_revive_from_agent_log`).

## [2026-04-27i] — Race-condition consolidation, Phase 2: server-decides + idempotent Stop

Phase 2 of the structural rewrite. Phase 1 (`[2026-04-27h]`) gated stale state emissions at the source. Phase 2 removes the frontend's role as a state-decision-maker entirely.

### Pattern being killed
The frontend used to read its own (potentially stale) `agentStatusCache[sessionId].status` to choose between `/agent/followup` and `/agent/interrupt`. When the cache disagreed with the server (which is exactly what races produce), the wrong endpoint got called and the server had to compensate. Same idea for the Stop button: cache-aware visibility, error response when "agent not running", optimistic cache writes that conflicted with reality.

### Server changes (`server.py`)
- **New endpoint `POST /api/project/<pid>/agent/send`** is the only intent endpoint the frontend calls now. Inside `get_manager(pid).lock`, it reads live `agent_sessions[session_id].status` and routes:
  - missing session (or no session_id) → revive from `agent_log` if possible, else dispatch fresh
  - `status == 'running'` → `agent_interrupt` (atomic stop+resume, Phase 1's `_interrupting` gate already in place)
  - any other status → `agent_followup` (queues for Mode A, stdin-write for Mode B, respawns purged sessions via `_revive_from_agent_log`)
  Response is the upstream handler's response with a `route` field appended (`'interrupt'` / `'followup'` / `'revive'` / `'dispatch'`) for debugging.
- **`/agent/stop` is now idempotent.** Pressing Stop on a session that's already stopped, missing, or in any non-running state returns `200 {ok: true, already_stopped: true, reason: <state>}` instead of 400/404. The frontend can call it without first checking cached status.
- **New SSE event `turn_start`** emitted by `/agent/stream` whenever `session['status']` transitions into `'running'`. Without it, the FE (which no longer flips status optimistically) would have no way to learn a new turn began until `turn_complete` fired at the end. `turn_start` is non-terminal — the SSE handler updates UI but does NOT close the stream.
- The existing `/agent/dispatch`, `/agent/followup`, `/agent/interrupt` endpoints are kept as internal helpers (still used by cron, scheduler, hivemind, and called by `/agent/send` itself). Frontend no longer calls them directly for the input box / interrupt flow.

### Frontend changes (`static/index.html`)
- **`sendFollowup` simplified.** Removed: the `currentStatus` read, the `useInterrupt` branch, the endpoint selection, the optimistic `agentStatusCache[sessionId].status = 'running'` write, the `updateHistoryStatus`/`updateAgentStatusUI` to `'running'`. Kept: prompt history, image upload, echo line, guardian guards, followup timeout. New behavior: always POST `/agent/send`, let the server pick the route, let SSE deliver the status flip via the new `turn_start` event.
- **`stopAgent` simplified.** Removed: optimistic `agentStatusCache[sessionId].status = 'stopped'`, optimistic `updateHistoryStatus(sessionId, 'stopped')`, the immediate `refreshModal`/`renderAgentConsole`. Kept: SSE close (so reconnect picks up post-stop state cleanly), timeout cancel, `_recentlyStoppedSessions` marker. Server's idempotent `/agent/stop` makes the button safe to spam.
- **New SSE handler `turn_start`** updates `agentStatusCache[sessionId].status = 'running'` and refreshes UI without closing the stream.

### Net effect
- Frontend has zero state-decision logic for the agent-send flow. All routing happens server-side under the lock.
- Cache-vs-server desync (the root of #6, #13, #16) becomes architecturally impossible for these flows: the FE doesn't hold a state opinion that can desync. Cache is reactive-only.
- Adding a new state (e.g. "queued", "recovering", "interrupting") becomes a single branch in `agent_send` — no new endpoint, no FE change.

### What this does NOT remove
- Other optimistic UI updates outside the agent-send path (e.g. backlog edits, project status changes) are unaffected; those have their own desync risks but are out of scope.
- The Phase 1 single-emit gate (`_session_owned_by`, `_interrupting` flag) is still required — Phase 2 routes work fine, but the SSE stream still needs Phase 1 to suppress dying-thread emissions. The two phases are complementary.

### Server restart required
Both Phase 1 and Phase 2 changes are server-side. The running Flask process (started 2026-04-24) won't pick them up until restart.

### Rollback
1. **Cheapest** (revert behaviour, keep code): in `static/index.html` `sendFollowup`, change `'/agent/send'` back to `'/agent/followup'`. Stop button reverts to working as before because the server's idempotent change is backward-compatible (a `200 {already_stopped: true}` response still triggers the FE's existing "ok" path).
2. **Clean**: also delete the `/api/project/<project_id>/agent/send` route in `server.py`, the `turn_start` emit block in `/agent/stream`, and the `turn_start` handler in `static/index.html`. Restore the `currentStatus`/`useInterrupt` logic and the optimistic cache writes in `sendFollowup`/`stopAgent`. Restore `/agent/stop`'s 404/400 responses.

## [2026-04-27h] — Race-condition consolidation, Phase 1: single-emit gate

After 16 distinct race-condition fixes accumulated in this codebase, the user asked for a structural fix instead of another point patch. The pattern across most of them is: **a thread (usually a stream reader's `finally` block) emits authoritative session state (`status`, `process_alive`, terminal events) for a session it no longer owns**, because either (a) a follow-up replaced the proc, or (b) an interrupt is mid-flight (kill issued, new proc not yet spawned).

Phase 1 consolidates the identity check into one helper and closes the kill-→-respawn gap that #16 was abusing. Fixes #1, #2, and #16 from the inventory in MEMORY.md ("Mode B reader's stale process_alive flag", "AskUserQuestion guardian race", "Interrupt-resume stale-status emit"). Phase 2 (server-as-only-source-of-truth on the frontend) is *not* in this commit — it's the larger refactor and deserves its own pass.

### What changed (`server.py`)
- **`_session_owned_by(session, my_proc)`** helper added next to `_read_agent_stream`. Returns True iff `my_proc` is still the live proc for this session AND the session is not mid-interrupt. All places that previously did `session.get('proc') is my_proc` (or its negation) in the agent stream readers now go through this helper.
- **`agent_interrupt`** now sets `session['_interrupting'] = True` *under the lock, before* killing the old proc. The respawn thread clears it (`session.pop('_interrupting', None)`) under the lock immediately after `session['proc'] = new_proc`. The exception path also clears the flag, so a respawn failure doesn't leave the session permanently gated.
- **Stream readers** (Mode A `_read_agent_stream` + Mode B `_read_agent_stream_b`):
  - Loop-break check (`if session.get('proc') is not my_proc: break`) → `if not _session_owned_by(session, my_proc): break`.
  - Exception block's "should I log?" gate → `_session_owned_by(...)`.
  - `finally` block's "should I emit terminal status?" gate → `_session_owned_by(...)`. This is the gate that fixes #16: between the old proc dying and the new one being assigned, `_interrupting=True`, so the dying reader's `finally` skips the `status='error'`/`status='completed'` write that was flipping the UI to "stopped".
- **Terminal session reader** (`_read_terminal_stream`) was *not* changed — it operates on a different `session` dict (`terminal_sessions`), has no interrupt path, and the existing `proc is my_proc` check is correct there.

### Why this is structural, not another point fix
The previous 15 race fixes were each "spot the bug, add a check at one site". This one consolidates the check itself. Any future code path that wants to emit session state from a thread can call `_session_owned_by(session, my_proc)` and get correct behavior, including during interrupt-resume, without reasoning about which proc is current. New emit sites added later are forced to confront ownership at the type-system level (you can't emit without a `my_proc` in scope, and you can't be sure of ownership without the helper).

### Phase 2 (deferred): frontend trust-server-only
Currently `sendFollowup` does optimistic `agentStatusCache[sessionId].status = 'running'` writes before the server confirms. When the server's truth conflicts (e.g., the interrupt-resume gap, or a 404 from a purged session), the cache stays wrong. Phase 2 will drop optimistic writes — UI status flips only when an SSE `status` event arrives. The local "echo" line for the user's typed message stays, since that's a UI affordance, not a state claim. Deferred because it touches roughly a dozen sites in `static/index.html` and benefits from Phase 1 having stabilized the server side first.

### Server restart required
The new code is in `server.py`; the running Flask process (started 2026-04-24) won't pick it up until restart. Old in-flight sessions survive restart via `_revive_from_agent_log` from `[2026-04-27e]`.

### Rollback
1. **Cheapest** (revert behaviour, keep code): in `_session_owned_by`, change the body to `return session.get('proc') is my_proc` — drop the `_interrupting` check. The flag still gets set/cleared but is no longer consulted; behaviour reverts to pre-`[h]`.
2. **Clean**: replace each call site of `_session_owned_by(session, my_proc)` with the original `session.get('proc') is my_proc` (or its negation), delete the helper, delete the three `_interrupting` set/pop sites in `agent_interrupt`.

## [2026-04-27g] — Mobile UI iteration: tabs into 3-dot menu, compact bottom bar, modal trim

Follow-up tightening of the mobile UI from `[2026-04-27f]`, driven by Galaxy Z Fold 7 cover-screen testing (~410 px CSS width).

### What changed (`static/index.html`)
- **Modal tab bar moved into the three-dot menu on mobile**. The 6 tabs (Agent / Backlog / Agent Log / Plans / Activity / Hivemind) are injected at the top of `.modal-menu-dropdown` inside a `<div class="mc-tabs-in-menu">` block. Each menu item calls `_mcMenuSwitchTab(projectId, tab)` — a thin wrapper that closes the open dropdown and delegates to `switchModalTab`. The active tab is highlighted with `--accent-dim` background. The original `.modal-tab-bar` at the top of the modal is `display: none` on mobile. Desktop unchanged.
- **Three-dot menu readability** (mobile only): items 13 → 15 px, padding 10/16 → 12/18 px, icons 16 px, sub-items 14 px. `min-width: 240px`. `max-height: calc(100dvh - 120px)` with `overflow-y: auto` + thin scrollbar so the menu can scroll when tabs + Status + Color + Memory + Rules + Pop-out + Delete overflow the viewport.
- **Modal header trim** (mobile only): hides the domain tag, the status-pill + relative-time row (now classed `.modal-status-row` on the inline div), the project summary, and the standalone `.card-summary` grid below the header. Added `.modal-status-row` class to the inline `<div>` in `modalContentHTML`. Padding tightened to `6px 14px 4px 16px`. What remains: project name input + 3-dot / minimize / close.
- **Per-session sub-tabs row + "+ New" stay inline**: `.agent-tab-bar` is now `flex-wrap: nowrap; overflow-x: auto` on mobile (was wrapping when two long session names + the New button overflowed), each `.agent-tab` capped at `max-width: 110px`, both tabs and `.agent-tab-new` get `flex-shrink: 0` and small horizontal padding.
- **Hide noisy session metrics on mobile**: `.token-badge` (elapsed · tokens · cached · turns), `.agent-activity` (live activity ticker), `.btn-popout`, `.btn-hm-dash` all `display: none` at ≤960 px. The status row then collapses to just `agent-status-dot` + label + `Stop` button.
- **Bottom tab bar shrunk** from ~60 → ~52 px tall: padding `8/12/14` → `4/8/6`, icons 22 → 18 px, label gap 3 → 1, FAB 44 → 36 px with `margin-top: -12px` (was `-16`) and `box-shadow: 0 2px 0` (was `0 3px 0`). Looks balanced on the Z Fold cover screen and similar narrow phones.
- **Modal/console offsets re-aligned to 52 px**: `.modal-content`, `.modal-window`, `.agent-console`, and the `@media (hover:none),(pointer:coarse)` modal sizing all use `calc(100dvh - 52px)` / `bottom: 52px`. This eliminates the phantom `===` line that was visible below the modal when the modal extended further than the tab bar's actual height.
- **Modal corner-resize grip + chat-resize handle hidden on mobile**: `.modal-content::after { display: none }`, `.modal-content { resize: none }`, `.agent-chat-separator { display: none }`. None of them are usable on a touch screen.
- **Home tab actually goes home now**: `sidebarNav('dashboard')` had no handler — only updated active-state. On mobile (`innerWidth <= 960`) it now closes every entry in `openModals` so tapping Home from inside a project modal returns to the project grid. Desktop behaviour unchanged.

### Galaxy Z Fold 7 / "Desktop site" gotcha
The cover screen is ~410 px CSS wide, but **Chrome and Samsung Internet often default to "Desktop site" mode on foldables**, which fakes a ~980 px viewport — causing `@media (max-width: 960px)` to never fire. Toggle off "Desktop site" in the browser menu to see the mobile UI. Documented in MEMORY.md.

### Files
- `static/index.html`: ~80 net new CSS lines inside the existing `MOBILE FRIENDLY UI` block + `@media (hover: none),(pointer: coarse)` updates; `_mcMenuSwitchTab` helper added beside `switchModalTab`; tab-list `<div class="mc-tabs-in-menu">` injected into `modalContentHTML`'s menu dropdown; `modal-status-row` class added to the inline header div.

### Rollback
The cheapest and clean rollback paths from `[2026-04-27f]` still work — they delete the entire `MOBILE FRIENDLY UI` CSS block, which now contains all of these tightening rules too. The `_mcMenuSwitchTab` helper and the `mc-tabs-in-menu` block in `modalContentHTML` are inert on desktop (the section is `display: none` at >960 px), so leaving them in place after a partial rollback is harmless.

## [2026-04-27f] — Mobile UI: friendly app bar, filter pills, rounded cards, FAB tab bar

Adapted the mobile design system handoff (`Mission Control Design System (1).zip`, `ui_kits/mobile/`) into the dashboard at ≤960 px widths. All changes are additive, scoped to a single CSS block and a couple of HTML/JS hooks — desktop is untouched.

### What changed (`static/index.html`)
- **App bar** (`#mobile-app-bar`): new `<div class="mc-app-bar">` above the project grid with an eyebrow line ("Monday afternoon"), display heading ("Hi 👋"), and circular avatar button (initials, taps to Settings). The slim desktop `.header` is hidden on mobile (it had no useful content there once the metric pill / search were already hidden at ≤600 px).
- **Filter pills row** (`#mobile-filter-pills`): horizontal-scroll row of pills — `Needs you`, `All`, `Working`, `Done`, `Resting` — each with a count derived from `friendlyStatus(p)`. `Needs you` is amber-bordered to flag attention. Clicks call `setFilter(...)`. `filterProjects()` was extended to handle the new `urgent` value (waiting + blocked + asking + stuck) and the existing `completed` status.
- **Project tile restyle**: tiles get 18 px corners, 1.5 px text-colored border, a 4 px solid drop-offset shadow (warm/editorial) or soft shadow (dark), 40 px rounded-square emoji avatar, and a chip-style status pill (rounded, colored bg, dot). Asking → amber border + amber drop shadow; Stuck → red border + red drop shadow. The desktop `::before` accent strip is suppressed (the shadow carries the cue). Domain tag and per-tile "agent running" badge are hidden on mobile (the chip already conveys it).
- **Bottom tab bar redesign**: 5 slots (Home / Backlog / **+ FAB** / Activity / Settings) instead of the old 4. Center FAB is a circular accent-colored button with a 3 px solid drop-offset shadow that floats above the bar (`margin-top: -16px`). Tapping the FAB calls `openNewProjectForm()`. `sidebarNav()`'s active-class loop now uses each tab's `data-nav` attribute instead of its index, so reorders are safe.
- **Agent console / modal sizing** bumped from `48px` to `64px` to fit the taller FAB tab bar (later re-tightened to 52 px in `[2026-04-27g]` after shrinking the bar).
- New JS: `renderMobileAppBar()` (eyebrow + greeting + avatar initials) and `renderMobileFilterPills()` (count + active state). Both bail when `window.innerWidth > 960`. Wired into `render()` and re-run on `window.resize`.

### What was deliberately *not* taken from the handoff
- Lockscreen-notifications screen: no native push surface in MC.
- Chat composer / Orchestrator chat screen: superseded by the existing per-project agent panel.
- New-project wizard suggestion grid: MC has a real `openNewProjectForm()` flow.

### Tone behaviour
The block applies in all tones; the warm/editorial palettes match the design 1:1, dark inherits the same layout with palette-appropriate shadows. The accent color (FAB / active pill / avatar) follows the user's chosen `data-accent` — pick `sunset` in Settings → Appearance to see the orange-on-cream look from the handoff exactly.

### Rollback
1. **Cheapest** (hide everything): in `static/index.html`, change `@media (max-width: 960px)` on the `MOBILE FRIENDLY UI` block (search "MOBILE FRIENDLY UI") to `@media (max-width: 0)`. Tiles/tab bar revert to pre-change desktop styling instantly.
2. **Clean**: delete the `MOBILE FRIENDLY UI` CSS block (the one starting at the comment "MOBILE FRIENDLY UI (≤960px, all tones)") + the closing `@media (min-width: 961px) { .mc-app-bar, .mc-pill-row { display: none !important; } }` rule directly after it.
3. **Full revert**: also delete the `<div class="mc-app-bar">` and `<div class="mc-pill-row">` HTML inside `.content-main`, restore the old 4-tab `<div class="bottom-tab-bar">` HTML (`Dashboard / Scheduler / Settings / Processes`), revert `sidebarNav()`'s tab-bar loop to the index-based version, drop `renderMobileAppBar` / `renderMobileFilterPills` and their `render()` / resize hooks, and remove the `urgent` / `completed` branches from `filterProjects()`.

## [2026-04-27e] — Revive finalized agent sessions from agent_log on follow-up

### Symptom
Press Stop on a Mode B agent, type a follow-up, hit send → "session not found" → frontend flips to `error` → permanent dead end. Same trap whenever an `agent_sessions` entry was gone but the conversation transcript still existed (server restart, 24 h scheduler purge, manual tab close, etc.).

### Why it happened
`/api/project/<id>/agent/followup` only looked in the in-memory `agent_sessions` dict. If the entry was missing, it returned 404 — even though `data/<id>_agent_log.json` typically still held the same `session_id` mapped to a resumable `claude_session_id`. The follow-up's `-r` resume path (already wired for `process_alive=False`) never got a chance to fire because the session vanished before the lookup.

### Fix (`server.py`)
- New `_revive_from_agent_log(project_id, session_id, message, p)` (placed right after `_save_agent_log`): looks up the most recent matching log entry, grabs its `claude_session_id`, spawns a fresh process with `-r <claude_sid>` (or `--append-system-prompt` fallback if the transcript is too large), and reuses the same `session_id` so the frontend's open UI tab stays addressed.
- `agent_followup` now does a pre-check: if the session_id is missing from `agent_sessions`, it tries `_revive_from_agent_log` *before* returning 404. On success it returns `{ok:true, revived:true}`; the frontend's existing `connectAgentStream` reconnect handles the SSE resume.
- Both Mode A and Mode B handled. Stream reader threads (`_read_agent_stream` / `_read_agent_stream_b`) are reused as-is.
- New config flag `agent_revive_from_log` (default `True`) gates the behavior.

### Rollback
Three options, increasing in cost:
1. **Toggle off**: edit `data/config.json` and add `"agent_revive_from_log": false`. Restart MC. Behavior reverts to "session not found" → frontend `error`. No code changes needed.
2. **Remove the call site**: delete the pre-check block in `agent_followup` (the `_has_session` block right above the existing `with get_manager(project_id).lock:` line). The helper function stays but is unused.
3. **Full revert**: also delete `_revive_from_agent_log` (the function added after `_save_agent_log`).

### Edge cases worth watching
- A revival creates a *new* `agent_log` entry when the new process eventually finalizes — the same `session_id` will appear multiple times in `agent_log`, newest first. The lookup picks the newest, so chained revivals work.
- If the original session was Mode A and the project's `use_streaming_agent` has since been flipped to True (or vice-versa), the revived session uses the *current* setting. The Claude transcript itself doesn't care which mode reads it.
- A revived session with `claude_session_id` whose `.jsonl` is now > 5 MB will start fresh and prepend a context note (same auto-fresh path used elsewhere).
- Tab-close (`closeAgentTab` → DELETE `/agent/session`) intentionally finalizes; subsequent follow-ups to that session will *also* now revive it. If that's undesirable, add an "intentionally closed" marker to the log entry and skip those in the helper.

## [2026-04-27d] — Pin chat to bottom on first open

Follow-up to 2026-04-27c: the new "respect user scroll" guard was *too* respectful — newly-opened agent chats started at the top because their initial `scrollTop` was 0, which `_isAgentOutputPinned` treats as "user scrolled up". Added a `dataset.scrollInitialized` flag on each agent-output element. Until that flag is set, the next scroll-to-bottom is forced (treating the mount as fresh); after the first successful pin, normal "respect user scroll" behavior takes over. Applied in `sizeAgentChat`, `appendAgentLine`, and `updateConsoleOutput`.

## [2026-04-27c] — Stop yanking the agent chat back to the bottom while user is scrolled up

### Symptom
Scrolling up in an agent's chat output to read earlier text would snap back to the bottom every couple seconds, even when the agent wasn't producing new output. Modal refreshes (status polling tick, focus events) re-ran `sizeAgentChat`, which unconditionally wrote `out.scrollTop = out.scrollHeight`.

### Fix (`static/index.html`)
- New `_isAgentOutputPinned(el)` helper: true when the user is within 80 px of the bottom.
- All agent-output auto-scrolls now capture the pinned state *before* mutating the DOM and only scroll when the user was already pinned. Touched: `appendAgentLine` (3 sites), `sizeAgentChat`, plan-approve / stuck-plan banners, `renderAgentQuestion`, and `updateConsoleOutput` (the bottom console strip).
- User-initiated echoes (`approvePlan` confirmation, `sendFollowup`) intentionally still snap to the bottom — the user just took an action and wants to see the result.

## [2026-04-27b] — Process Manager: agent status column

`/api/processes` now joins each tracked process to its `agent_sessions` (or `terminal_sessions`) entry and returns an `agent_status` field. The Process Manager UI renders a colored pill (`running` / `idle` / `error` / `stopped` / `completed`) next to each row, so it's clear which "alive" agent process is actively working vs sitting idle waiting for a follow-up.

- Server (`server.py:list_processes`): snapshot tracked_processes under the lock, then look up `agent_sessions[sid].status` outside the lock; falls back to alive/exited for non-agent rows.
- Frontend (`refreshProcessList`): new `Status` column, `.process-status-pill` styled green/orange/red/gray.

## [2026-04-27] — Free idle SSE slots so Settings / Process Manager / Agent Log stop hanging

### Symptom
Settings menu, Process Manager, and the Agent Log tab would occasionally get stuck on "Loading..." forever. New agent dispatches under projects that already had agents would silently appear to do nothing. The pattern correlated with how many projects had agents running or idle in the background.

### Root cause
Chromium / WebView2 caps HTTP/1.1 connections at **6 per origin**. Mission Control opened one long-lived `EventSource` per session whose status was `running` *or* `idle`, and Mode B turn completion didn't close that stream — only a terminal `status` event did. Once 4–6 idle agents accumulated their SSE sockets, ordinary fetches like `/api/processes`, `/api/config`, and `/api/project/<id>/agent_log` queued behind those streams indefinitely.

### Fix (`static/index.html`)
- **`turn_complete` handler (~line 6033)**: now closes the `EventSource`, deletes it from `agentEventSources`, clears `sseRetryCount`, and stops the watchdog. The agent process stays alive — only the browser-side socket is released.
- **`fetchAgentStatus` auto-reconnect (~line 6770)**: only reconnects SSE for sessions whose status is `running`. Idle sessions wait for a follow-up to reopen the stream.
- **`sendFollowup`**: already calls `connectAgentStream` after the POST resolves (line 6664-6667), so the reconnect path was already correct — idle sessions stream output normally on the next message, after a sub-second reconnect.

### Tradeoff
First output line on a follow-up arrives ~200-500 ms later than before (one SSE handshake), in exchange for never running out of browser connection slots regardless of how many idle agents are open.

## [2026-04-24] — Transcript-derived Conversations + Zero-gap Resume Picker

### Why
- The "Recent agent sessions" list (both in system prompts for new agents and in the Resume picker) was sourced from the completion log `<pid>_agent_log.json`. That log only records sessions that end cleanly, so interrupted / hung / crashed / in-flight sessions never appeared on restart — exactly the conversations the user most needs to recover after a reboot.
- Labels were the *first* user message (`task`), almost always a boot / condensation prompt the user doesn't recognize. The user's *last* message is the meaningful memory anchor.

### Source of truth: Claude Code's `.jsonl` transcripts
Claude Code already writes every conversation to disk as `~/.claude/projects/<encoded-cwd>/<uuid>.jsonl`. Those files survive server reboots. Mission Control now reads them directly.

### Backend (`server.py`)
- **`_extract_user_text(msg_field)`** — returns plain user text from a transcript line, skipping tool_result blocks. Handles both string and list content forms.
- **`_recent_claude_transcripts(project_path, limit=5)`** — scans `~/.claude/projects/<encoded>/*.jsonl`, covers both `_`→`-` encoding variants, dedups by filename, extracts `first_user` / `last_user` / `turns` per file, sorted by mtime desc.
- **`build_claude_context`** "Recent agent sessions" block replaced with a transcript-derived "Recent conversations" block. Label now shows the user's *last* message; status enriched from live `agent_sessions` → `_agent_log` → `'interrupted'` fallback. Old log-only block kept as fallback when `project_path` is blank.
- **New endpoint `GET /api/project/<pid>/conversations?limit=20`** — returns `[{claude_session_id, mc_session_id, status, label, last_user, first_user, turns, size, mtime, ts, ts_relative, live}]`.

### Frontend (`static/index.html`)
- **`conversationsCache[projectId]`** + **`loadConversations(projectId)`** — fetched on agent-panel render; invalidated alongside `agentLogCache` on SSE `result`/`error` + on tab close.
- **`sessionPickerHTML`** rewritten to merge transcript list with the completion log (transcript wins). Shows status dot, last-user label, turn count. Now surfaces interrupted / mid-flight sessions.
- **Resume indicator** reads the label from the transcript cache first.
- **`agentStatusCache[sid].claudeSessionId`** — populated from `/api/project/<pid>/agent/status` so the frontend knows each live session's Claude session id.

### Zero-gap picker updates
The picker reflects the user's latest message *without* waiting for a server round-trip:
- **`upsertConversationCache(projectId, claudeSessionId, lastUser, status)`** — in-place patch of `conversationsCache`: updates `last_user` / `label` / `status`, bumps `ts_relative='just now'`, moves entry to top, increments `turns`.
- **`_lastUserFromBuffer(sessionId)`** — reconstructs the last user prompt by scanning `agentOutputBuffers` backward for the local-echo `"> …"` line.
- **`closeAgentTab`** — before nuking local state, snapshots `claudeSessionId` + last user line and upserts (`status='stopped'`). Then the backend `DELETE` chains `loadConversations(projectId)` to reconcile with authoritative data (~200 ms later).
- **`sendFollowup`** — after local echo, upserts with `status='running'`. When the session later stops, the picker already has the real last line.
- **`dispatchAgent`** with resume — seeds `claudeSessionId = resumeId` in the status cache and upserts immediately so the resumption's prompt shows up in the picker even before the first SSE event.

### Known limitation
Fresh sessions don't have a `claude_session_id` locally until the next status-fetch tick (~≤2 s after first SSE event). Closing a brand-new tab before that tick skips the optimistic upsert and relies on `loadConversations` only — still fast, just not zero-gap.

## [2026-04-23b] — Auto-create project folder on new project

### Auto workspace folder
- **New projects with no path get their own folder.** On `POST /api/project/<id>`, if this is the project's first write and `project_path` is blank, server creates `<auto_workspace_base>/<project_id>/` and assigns it. Collisions get `_1`, `_2`, etc. suffixes.
- **Each project needs its own folder.** On any write that sets `project_path`, server scans other project JSONs and rejects with **409** if the resolved path already belongs to another project. Windows paths compared case-insensitively.
- **`auto_workspace_base` config key** (default: `~/MissionControl`). Exposed in Settings → Paths & Server as "Auto Workspace Base".

### New-Project form copy
- Path placeholder changed from `C:\Users\...\MyProject` to `Leave blank to auto-create a folder`.
- Inline hint under the field: *"If blank, a dedicated folder will be created under your auto-workspace base. Each project needs its own folder."*
- `createProject()` now surfaces server errors correctly by checking `res.ok` in addition to `data.ok` (so the 409 path-collision message reaches the user).

## [2026-04-23] — Tile Redesign, Mode-C/Audio Split, Favicon, Cross-Project Backlog

### Tile redesign (design-handoff aligned)
- **Flat surface background.** Removed the per-project `modal_color.bg` tint + color-mix/backdrop-filter transparency that made tiles look blobby. Every tile now uses `var(--surface)` like the rest of the app.
- **Project Color → border color.** The color selected in a project's three-dot menu ("Color") now paints the tile's border via inline `style="border-color:..."`. Border width normalized to **2 px** in all three tones (Dark, Warm, Editorial); Warm and Editorial use the stronger `--border2` line token for better definition on light backgrounds.
- **Status borders still win.** `.card.friendly-stuck` / `.card.friendly-asking` border rules now carry `!important` so red/amber status indication overrides the inline project color.
- **Flexible tile height.** Dropped fixed `aspect-ratio: 5/4`. Grid now uses `grid-auto-rows: 1fr` + `align-items: stretch` so every row sizes to the tallest tile — long titles (e.g. "DayTrading — Engulfing Dashboard") no longer clip the summary or backlog badges. `min-height: 200px` floor (140 px in compact mode).
- **Scoped status-pill rules.** `.status-active / .status-blocked / .status-waiting / .status-parked / .status-unknown` rules were bare selectors and were bleeding green/amber/red backgrounds onto the `.card` element (the card also has these classes). Rescoped to `.status-pill.status-*` so only the pill chip is tinted.

### Favicon
- Inline SVG data-URI favicon: rounded square in brand accent `#e8824a` with a bold white **M** (Nunito/Inter). Matches the design handoff's `.fc-brand-mark`. Added `<meta name="theme-color" content="#e8824a">` so mobile browser chrome matches.

### Cross-project Backlog view
- New sidebar nav item "Backlog" (`sidebarNav('backlog')` → `openAllBacklog()`).
- Modal `__all_backlog` aggregates backlog items across every project with filters: text search, status (Open / Done / All), priority (High / Normal / Low / All).
- Each row shows the owning project name in accent color; clicking a row jumps to that project's modal and scrolls the item into view.
- Preserves existing badges: `agent` / `doing` source tags, priority pill, notes count.

### Advanced-features toggles (off by default)
- Settings → new "Advanced features" section. Hides under-development or power-user surface by default:
  - Token usage counter (header pill)
  - Tool call lines (`[tool: Read]` / `[tool: ExitPlanMode]` markers in agent output)
  - GitHub sync badges (issue links, `#N` badges)
  - Agent Log tab (per-project history)
  - Memory & Rules menu entries (inside three-dot)
- Stored in `localStorage` as `mc_advanced_flags`; applied via `body.adv-hide-*` classes and `!important` display:none rules.
- Rationale: keeps the first-run surface simple; matches design handoff's philosophy of a friendly, minimal dashboard.

### Metrics row removed
- Deleted the 4-card metrics strip (Active Agents / Cost / Tasks Completed / Errors). The header's agent-count pill already conveys "active" count; cost/errors can be surfaced on demand rather than permanently eating vertical space.
- Removed associated CSS (`.metrics-row`, `.metric-card`, `.mc-value`, etc.), mobile overrides, `renderStats()` metrics code path, and `VOICE_LABELS.metric_*` entries.

### Mode-C & audio work moved to a side branch
- **`mode-c-audio` branch** now owns all voice-conversation work (STT via faster-whisper, TTS via Web Speech API, voice-selection UI, voice-behavior prompt, per-turn dedup) plus the Mode-C duplication/ERROR-status fixes. Pushed to origin.
- **`master`** reverted to remove Mode C entirely — `interactive_agent.py` deleted, `/api/interactive*` endpoints stripped, Chat button and all `interactiveSessions` / `voiceMode` frontend code removed (auto-merge handled everything except the file delete, which was resolved by accepting the removal).
- Rationale: Mode C and voice are still flaky. Keeping them off master unblocks shipping polish work without waiting on their stabilization. They can be re-merged from `mode-c-audio` once they're solid.
- Today's tile redesign was committed on master first, then the Mode-C revert layered on top. Final master state: fast-forward of `origin/master` — no force-push.

## [2026-04-16] — Tauri Launcher, CORS, AskUserQuestion Race Fix & Resume Recovery

### Tauri Launcher: Silent Server Death Fix
- **Root cause**: `lib.rs` spawned Flask with `Stdio::piped()` but never read from the pipes. After hours of printing, the ~64 KB OS pipe buffer filled, `print()` blocked, Flask deadlocked, and the Python process eventually exited on `BrokenPipeError`. This was invisible — no traceback, no error, just a dead server.
- **Fix**: `Stdio::piped()` → `Stdio::inherit()` in `lib.rs:40-43`. Flask stdout/stderr now flows directly into the Tauri parent terminal. No buffer, no drainage needed, and crash tracebacks are visible.
- **Related**: removed `devUrl` from `tauri.conf.json` so `npx tauri dev` doesn't block waiting for an external HTTP server before running Cargo. The Rust app spawns Flask itself via the `setup()` hook; the webview loads `static/index.html` from `frontendDist` (disk) instead of HTTP.
- **Launch workflow changed**: user runs `npx tauri dev` only — no separate `python server.py` in another terminal. The old dual-terminal setup caused port conflicts (two Flask instances on 5199, requests routing unpredictably, `port_conflict.log` accumulating entries).

### CORS: Tauri Webview Origin Fix
- **Symptom**: after removing `devUrl`, the webview loaded from Tauri's internal scheme (`http://tauri.localhost` or similar) instead of `http://localhost:5199`. API fetches returned 200 at the Flask layer but were blocked by the browser's CORS policy because the Origin didn't match the `ALLOWED_ORIGINS` set.
- **Fix**: replaced the origin allowlist with an echo-back pattern — `Access-Control-Allow-Origin` is set to whatever Origin the caller sends. Safe because Mission Control binds localhost only and has no auth layer. Added `Vary: Origin` header for proper cache behavior.

### Guardian Race Fix: AskUserQuestion
- **Symptom**: when Claude called `AskUserQuestion`, the agent went into error state instead of showing the question UI. The user saw the question text flash briefly, then `[Guardian: process found dead]` followed by repeated `[Guardian: question may have been missed]` messages.
- **Root cause**: the stream reader set `waiting_for_question=True` and called `proc.kill()` while `status` was still `'running'`. The guardian's 10s tick landed in the gap before the reader's `finally` block could reacquire the lock and set `status='idle'`. Guardian State 1 saw "dead process + running status" and marked the session `'error'`. When the reader finally got the lock, its `if status in ('running', 'idle')` check failed, so the graceful question-handling branch never ran.
- **Fix (two layers)**:
  1. Both Mode A and Mode B stream readers now set `status='idle'` and update `last_status_change_time` **before** calling `proc.kill()` for `AskUserQuestion`. This closes the race window — the guardian sees a fresh idle session, not a stale running one.
  2. Guardian State 1 now checks `waiting_for_question` and `waiting_for_plan_approval` flags as a safety net. If either is set, the dead-process → error transition is skipped entirely.

### Auto-Recovery for Failed Session Resumes
- **Problem**: dispatching with `claude -r <session_id>` across server restarts is fragile. The CLI's internal state (turn counter, context budget) was set during the original session and may not survive a fresh process reading the transcript file. Two failure modes:
  1. **Immediate death**: process exits within seconds, before producing any output.
  2. **Post-turn death**: process completes one turn successfully, then exits. Follow-up respawn tries `-r` on the same session → same failure → silent error loop.
- **Fix for immediate death** (`_auto_recover_failed_resume`):
  - Each session now tracks `_resume_id` and `_dispatch_time` at dispatch.
  - Both Mode A and Mode B readers: if a resumed session dies within 60s with `status='error'` and `num_turns=0`, `_auto_recover_failed_resume()` fires automatically.
  - Reuses the same session object (seamless to frontend), spawns fresh `claude -p` with context note: `[Continuing from a previous conversation (session X) that could not be resumed. Start fresh.]`
  - One-shot: `_resume_recovery_attempted` flag prevents infinite loops.
- **Fix for post-turn death** (Mode B followup respawn):
  - When a Mode B process dies after a turn that came from a `-r` resume, the follow-up respawn now **starts fresh** instead of trying `-r` on the same fragile session.
  - Log message: `[Resumed session process exited — restarting fresh]`
  - If `claude_session_id` was never emitted by the CLI, falls through to fresh start instead of returning 400 error.
- **Verbose respawn logging**: every decision point in the Mode B follow-up respawn path prints to stdout (`[followup]`, `[respawn-B]` prefixed) so failures are visible in the Tauri terminal.

## [2026-04-15] — Per-Project Agent Isolation & Guardian Overhaul

### Per-Project Agent Manager (eliminates cross-project blocking)
- **Root cause**: every agent operation (dispatch, follow-up, stop, guardian state mutation) routed through a single global `agent_lock`. A slow process-tree kill in Project X blocked stdin writes, status reads, and SSE events for every other project — Mode A and Mode B "isolation" was illusory because both modes ultimately serialized on the same mutex.
- **`ProjectAgentManager`** (server.py:329) — new class, one instance per `project_id`, owns its own `RLock`, `session_ids` set, and lazily-spawned guardian thread.
- **`get_manager(project_id)`** + **`get_manager_for_session(session_id)`** + **`all_managers()`** — registry helpers. `_managers_lock` is held only for microseconds to mutate the registry dict; never held across any subprocess, kill, or stdin write.
- **All 30 `with agent_lock:` call sites** replaced with `with get_manager(<project_id>).lock:` — covers `_dispatch_agent_internal`, follow-up endpoints, stop / interrupt-resume, hivemind dispatches, terminal broadcast, scheduler purge, Process Manager kill, and every guardian state mutation.
- **`agent_lock` deleted entirely.** No shared mutex remains anywhere on the agent execution path.

### Per-Project Guardian Threads
- **`_project_guardian_loop(manager)`** (server.py:5494) — one guardian thread per `ProjectAgentManager`, lazily spawned via `manager.ensure_guardian()` on first dispatch.
- Each loop iterates only its own project's `session_ids` — has zero visibility into other projects, by construction.
- Legacy global `_session_guardian_loop` is now a no-op stub kept for compatibility with startup callers.
- A hung kill, slow check, or recovery sequence in one project cannot affect any other project.

### Guardian Hung-Process Detection: CPU-Aware
- **`GUARDIAN_HUNG_TIMEOUT`: 180s → 600s.** The old 3-minute threshold was killing healthy thinking turns mid-stream.
- **New `_proc_is_cpu_idle(session, proc, now)`** (server.py:5448) — uses psutil to compare cumulative CPU times of the process *tree* (parent + children) across guardian ticks. Kill only fires if the tree burned <0.05 CPU-seconds per wall-second since the previous sample.
- **State 2 (hung process) now requires both stdout silence AND CPU idleness.** Long WebFetch / Bash / Read tool calls survive — they burn syscall/network time that psutil sees.
- **psutil missing → kill never fires.** Without psutil, `_proc_is_cpu_idle` returns `False` and the guardian falls back to dead-process detection only. No false positives possible.
- Lock is never held across `_kill_proc_background` — flag flips happen under the lock, the kill runs after release.

### Critical Bug: Stale `last_output_time` on Resume
- **Symptom**: prompting any idle Mode B session triggered an instant guardian kill ("no output for 609s — killing hung process") even though the agent had no chance to produce a single chunk.
- **Cause**: `last_output_time` was set at session creation and only advanced when the stream reader saw stdout. When a turn completed and the session sat idle, the timestamp froze. The five resume paths flipped `status` back to `'running'` without resetting the timestamp, so the guardian's next tick computed `now - last_output_time = (entire idle gap)` and killed.
- **Fix**: every site that sets `status='running'` on a resume now also sets `last_output_time = _time.time()`:
  - Mode A initial follow-up (server.py:1606)
  - Mode A interrupt-resume (server.py:2367)
  - Mode B respawn after auto-fresh (server.py:2082)
  - Mode B stdin write to alive process (server.py:2108)
  - Mode B follow-up via `_start_followup` (server.py:2149)

### Frontend: Honest Mode Display
- **Bug**: project context menu showed `Mode B (Streaming) OFF` when a project had no `use_streaming_agent` key, but dispatch fell back to global config (which is `True`) and ran Mode B regardless. UI lied about which mode would run.
- **`_globalConfig` cache** in `index.html` — populated by `refreshSilent()` from `/api/config`, used to compute the *effective* mode (per-project override if set, else global default).
- **Menu redesign** (index.html:3034): `⚡ Agent: Mode A (global)    switch → B`. Always shows the mode that will actually run, with a `(global)` badge when the project is inheriting and a `switch → A/B` hint for the next click. One click writes an explicit per-project override.

### Migration Notes
- No data migration required; sessions remain in the global `agent_sessions` dict (GIL-safe for reads). Only locking and guardian iteration moved per-project.
- `agent_lock` is removed; any external code importing it will break (none in-tree).
- Server restart required to pick up the timestamp resets and CPU-aware guardian.

## [2026-04-14] — Session Guardian, Plan Visibility & Tab Fixes

### Session Guardian (replaces Health Monitor)
- **`_session_guardian_loop()`** — new 10-second tick background thread replaces the old `_health_monitor_loop()`
- Detects 7 stuck states across both Mode A and Mode B sessions:
  1. Dead process with stale running/idle status (was Mode B only, now covers Mode A too)
  2. Hung process — alive but no output for 3+ minutes → kills process, marks needs_attention
  3. Stuck `waiting_for_plan_approval` / `waiting_for_question` flags (>2 min, no SSE client)
  4. Stuck `pending_followups` queue (>30s, not running, not dispatching)
  5. Stuck `_dispatching_followup` flag (>30s)
  6. Rapid error loop — circuit breaker trips after 3 failures within 60s
  7. Popen failure — session stuck in `running` with dead/missing process (>15s grace)
- **Auto-recovery**: preserves user's message, kills zombie process tree, retries `claude -r` with exponential backoff (5s→10s→20s)
- **Circuit breaker**: after 3 rapid failures, stops retrying, sets `guardian_state='needs_attention'`
- Recovery is scoped to individual sessions — parallel agents in other projects are never affected
- Per-session tracking: `last_output_time`, `last_status_change_time`, `guardian_state`, `recovery_attempts`, `pending_recovery_message`, `circuit_breaker_tripped`

### Critical Bug Fix: `_start_followup` Error Handling
- Wrapped `_start_followup()` body in try/except — previously, if `subprocess.Popen` failed (wrong PATH, disk full, etc.), the session would get permanently stuck in `running` with no process, no reader thread, and no way to recover
- On failure: sets `status='error'`, logs the error, guardian can then auto-recover

### Pending Message Capture
- `agent_followup` endpoint now saves the user's message as `pending_recovery_message` before spawning
- If the spawn fails, the guardian has the message to retry automatically
- Cleared on successful session completion (rc=0)

### SSE & API Integration
- New `guardian` SSE event type with `state` and `circuit_breaker` fields
- `_last_sse_poll_time` tracked in SSE loop for stuck gate flag detection
- Guardian state included in `/api/project/<id>/agent/status` response
- New endpoint: `POST /api/project/<id>/agent/guardian-reset` — `action: "retry"` resets circuit breaker, `"dismiss"` clears notification

### Frontend: Guardian UI
- New status dot states: `.recovering` (yellow pulsing), `.needs-attention` (orange pulsing)
- Guardian banner above chat input when circuit breaker trips:
  - "Try Again" button — resets circuit breaker, allows retry
  - "Start Fresh" button — dispatches new session
  - "Recovering..." banner during active recovery
  - "Needs attention" banner with retry/dismiss options
- `sendFollowup` guards: blocks input during recovery or when circuit breaker is tripped
- `updateAgentStatusUI` reflects guardian state on dots and labels
- `agentStatusCache` populated with `guardianState` and `circuitBreakerTripped` from status API

### Fix: Plan Content Hidden Before User Can Read
- `collapseIntoPlanButton()` no longer auto-collapses plan text on first `ExitPlanMode`
- Plan text stays visible in agent output so user can read it before deciding to approve
- "Approve Plan" + "Collapse Plan" buttons shown at bottom of visible plan
- On second ExitPlanMode (stuck loop), plan auto-collapses as before with warning

### Fix: Plans Tab Rendering
- Removed `setTimeout(() => renderPlansTab(...), 50)` — now called synchronously after `refreshModal()`
- `refreshModalById` re-renders plans tab content after DOM rebuild when cache exists
- Prevents race where SSE-triggered `refreshModal` could overwrite plans tab content

## [2026-04-12] — Stale Session Cleanup After Server Restart

### Frontend Session Reconciliation
- **`fetchAgentStatus()`** now compares server-returned sessions against locally cached sessions
- Sessions in `agentHistory` / `agentStatusCache` / `agentOutputBuffers` that the server doesn't know about (e.g., after server restart) are cleaned up automatically
- `activeAgentTab[projectId]` is cleared if it points to a stale session, so the dispatch input (not the follow-up input) is shown
- Associated SSE streams and watchdogs for stale sessions are closed
- `refreshModal()` + `renderAgentConsole()` triggered after stale cleanup so UI updates immediately

### Root Cause
- After server restart, in-memory `agent_sessions` is empty, but the frontend still held references to old sessions
- `activeAgentTab` pointed to a dead session ID → UI showed follow-up input instead of dispatch row
- Follow-ups sent to the dead session ID → server returned 404 → silently failed
- User saw a working chat UI but couldn't start new conversations or get responses

## [2026-04-04] — Agent Stability: Health Monitor & Error Recovery

### Process Health Watchdog
- **`_health_monitor_loop()`** — new background thread runs every 12 seconds
- Checks all Mode B sessions where `process_alive=True`, verifies PID is actually alive via `proc.poll()` + `_pid_is_alive()`
- If process is dead but flag says alive: sets `process_alive=False`, `status='error'`, logs `[Health check: process {pid} found dead]`
- Registered with `atexit` for clean shutdown via `_health_monitor_stop` Event

### Race Condition Fixes (process_alive flag)
- **`_read_agent_stream_b` finally block**: moved `session['process_alive'] = False` inside the `if session.get('proc') is my_proc:` guard — old reader threads from replaced processes can no longer falsely mark new processes as dead
- **`sendFollowup` endpoint selection**: `currentStatus` now captured BEFORE the optimistic UI update to 'running', fixing a bug where `useInterrupt` was always `true` (idle Mode B sessions were being killed and respawned instead of writing to stdin)

### Robust Followup Path
- **PID verification before stdin write**: `agent_followup` now checks `proc.poll()` / `_pid_is_alive()` before trusting `process_alive=True` flag — if process is dead, redirects to respawn path instead of silently failing
- **Old process cleanup on respawn**: Mode B respawn in followup now closes old proc's stdin and kills old process in background (prevents zombie processes)

### Frontend Unresponsive Agent Detection
- **`followupTimeouts`**: 20-second timer starts after every follow-up send; if no SSE output arrives, shows toast: "Agent appears unresponsive"
- Timer cancelled on: output received, turn_complete, status change, error event, or user stop
- Non-blocking warning — user can ignore if agent is just slow (e.g., large context resume)

### Static File Cache Busting
- `index.html` now served with `ETag` header based on file mtime+size
- Switched from `no-store` to `no-cache` — allows conditional GET (304) so Tauri WebView2 always revalidates
- Fixes stale frontend code being served after server-side changes

## [2026-03-25] — Active Context Auto-Trimming

### Context Budget → Active Condensation
- **`_check_context_budget()`** now triggers auto-condensation instead of just logging a passive warning
- Pre-dispatch check: when total context (CLAUDE.md + MEMORY.md + prompt) > 20KB, condensation fires immediately
- Post-completion check: also includes CLAUDE.md in size calculation (was MEMORY-only)
- Message changed from `[context warning]` to `[context trim]` with actionable status

### CLAUDE.md Condensation
- **`_dispatch_condense()`** now handles CLAUDE.md alongside MEMORY.md
- Only condenses CLAUDE.md when > 8KB (preserves small project configs)
- Housekeeping agent instructions: preserve rules/constraints verbatim, merge duplicates, compress verbose explanations, keep code snippets exact
- Target: under 8KB per file

### `_should_condense()` Expanded
- New `include_claude_md` parameter — includes project CLAUDE.md in size threshold check
- Used by both pre-dispatch (context budget) and post-completion triggers
- Skips running-agent guard when called from pre-dispatch (agent hasn't started yet)

## [2026-03-24] — Major UI Redesign

### Layout Overhaul
- **Collapsible sidebar** (52px → 220px on hover): Logo, nav items (Dashboard, Scheduler, Settings, Shared Rules, Processes), project shortcuts with status-colored dots
- **Slim header** (48px): Breadcrumb, Ctrl+K search trigger, token counter, agent count metric pill, Live badge
- **Metrics row** replaces stats bar: Active Agents, Cost Today, Tasks Completed, Errors — with live data
- **Toolbar** replaces filter row: Grid/List view toggle, filter dropdown with active pills, density toggle, + New Project button
- **Content area** with proper flex scroll (replaces body scroll)

### New Features
- **List view**: 7-column table (indicator, project, status, current task, next up, agent, updated) — toggle with Grid view
- **Command palette** (Ctrl+K): Search projects, actions (Scheduler, Settings, etc.), and view toggles with keyboard navigation (arrow keys + Enter)
- **Collapsible feed**: Click toggle to hide/show Activity Feed column (state persisted in localStorage)
- **Clickable feed entries**: Click any activity entry to open that project's modal
- **Mobile responsive design**: Bottom tab bar at ≤960px, single-column tiles at ≤600px, metrics row wraps at ≤768px
- **View persistence**: Grid/List mode, feed collapsed state, and density all saved to localStorage

### Mobile Fixes
- **Modal height**: Account for bottom tab bar — `calc(100vh - 48px)` at ≤960px viewport
- **Modal positioning**: Full-width, top-aligned on mobile (no center offset)
- **Agent chat input**: Fixed text entry box hidden below screen — `sizeAgentChat()` now constrains tab content and agent panel heights
- **Hide tile details on mobile**: Current Task and Next Up hidden at ≤960px

### Visual Refinements
- Refined color palette: darker backgrounds (#0c0e14), less saturated borders (#252a38), softer text (#e8ecf4)
- Tile aspect ratio: 1:1 → 5:4 (more information-dense)
- Tighter tile padding: header 14px, body 16px, footer 10px
- Feed column: 380px → 320px, clickable entries with hover accent border
- Left indicator on tiles: 4px → 3px

## [2026-03-24] — Fix process registration & plan approval reliability

### Process Registration — Windows-safe PID operations
- **New `_pid_is_alive()`**: uses `ctypes.windll.kernel32.OpenProcess()` on Windows instead of unreliable `os.kill(pid, 0)`
- **New `_kill_pid()`**: uses `taskkill /F /PID` on Windows instead of broken `os.kill(pid, 9)`
- Registration endpoint now warns-but-registers when PID not detected alive (handles race where process exits quickly)
- System prompt now includes explicit PID capture instructions for agents (Bash `$!` and Python `p.pid`)
- Process listing and kill operations use new cross-platform helpers

### Plan Approval — Server-side flag clearing
- **Root cause fix**: server now clears `waiting_for_plan_approval = False` when any followup is received
- Previously the flag was set on ExitPlanMode but never cleared — subsequent status polls re-set frontend state to "waiting"
- Frontend SSE handlers (`turn_complete`, `status`) now also clear `waitingForPlanApproval` locally
- `approvePlan()` rewritten: always sends directly via fetch API (no dependency on input element existing in DOM)
- Added double-click guard — button removed immediately before any async work

## [2026-03-24] — Live status on tiles & modals, UX fixes

### Live Auto-Populated Status
- **Current Task** and **Next Action** fields are now fully auto-computed from live state
- `computeLiveStatus(projectId)` inspects running agents, hiveminds, errors, completions, and backlog
- Priority: Hivemind > Running agent > Error > Last completed > Idle
- Next action: Hivemind pending workstreams > Top backlog item > —
- Color-coded: green (running), accent (idle agent), red (error), dim (idle/completed)
- Applies to both project tiles and modal summary section
- Replaces stale manual `current_task` / `next_action` fields

## [2026-03-24] — Plan approval gate, error recovery, UX fixes

### Plan Approval — No More Auto-Approve
- **Removed auto-approve**: `ExitPlanMode` no longer auto-approves plans — both Mode A and Mode B now set `waiting_for_plan_approval` flag and wait for user to click "Approve Plan"
- Removed `_auto_approve_plan_b()` function entirely
- Mode A no longer queues approval in `pending_followups`
- Mode B no longer sends approval via stdin automatically
- User retains full control over plan review before implementation starts

### Error Recovery — Continue from Errored Sessions
- Agent follow-up input bar now visible on errored sessions (was hidden before)
- Placeholder text: "Type to continue from where it stopped..."
- Sends follow-up via existing resume mechanism (`-r` for Mode A, stdin respawn for Mode B)

### Flexible Modal Textareas
- Memory, Rules, and Shared Rules modals use flex layouts — textareas grow/shrink with modal resize
- New `.memory-editor` and `.rules-editor` CSS classes (same pattern as `.shared-rules-editor`)
- Modals start at `60vh` height, resizable via drag corner

### Universal Ctrl+Scroll Zoom
- Ctrl+Scroll now zooms all modal content (was agent output only)
- `applyModalZoom()` helper sets `font-size` on `.modal-content` for full cascade
- Zoom levels persist per modal across refreshes

### Memory Path Resolution Fix
- `_native_memory_path()` now checks both underscore and dash encodings
- Prefers most recently modified file when both exist (fixes stale memory on projects with `_` in path)

### Agent Chat Overflow Fix
- Keep `.modal-scroll-body` overflow hidden while agent tab is active
- Prevents follow-up input bar from being pushed below the modal

### Hivemind Improvements (from prior session)
- Agent context now includes hivemind API instructions for chat-first creation
- `startHivemindChat()` — switches to Agent tab with prefilled setup prompt
- Open questions: "Respond" button prefills directive, resolves question after sending
- New endpoint: `POST /api/hivemind/{id}/knowledge/questions/{qid}/resolve`
- `_hm_read_open_questions()` now filters out resolved questions
- Findings displayed in dashboard overview and workstream detail views

## [2026-03-23] — Hivemind Phase 2+3: Agent Integration & Frontend

### Backend — Agent Integration (Phase 2)
- **Worker spawn**: `POST /api/hivemind/{id}/workstreams/{ws_id}/spawn` dispatches a standard MC agent session as a hivemind worker, with full workstream-specific context injection (handoff, findings, bus messages, decisions)
- **Handoff endpoint**: `POST /api/hivemind/{id}/workstreams/{ws_id}/handoff` — workers submit structured handoff documents (what was done, key findings, next worker instructions); written to `{ws_id}_handoff.md`
- **Orchestrator CLI sessions**: Short-lived `claude -p` subprocesses for goal decomposition (on create), synthesis, and re-planning — same pattern as memory condensation housekeeping agents
- **Auto-decomposition**: Creating a hivemind auto-dispatches an orchestrator CLI session to break the goal into workstreams
- **Auto-spawn**: Orchestrator background loop automatically spawns workers for ready workstreams (dependencies met, under max_concurrent_workers)
- **Worker lifecycle**: Detects finished/crashed workers, auto-retries up to max_retries_per_workstream, sets failed status when exhausted
- **Auto-completion**: When all workstreams complete, hivemind status set to completed and final synthesis triggered
- **Worker context builder**: `_hm_build_worker_context()` injects handoff, accumulated context, recent findings, bus messages, decisions, and API capabilities into the worker system prompt

### Frontend — Hivemind Tab & Dashboard (Phase 3+4)
- **Hivemind tab** in project modal — shows all hiveminds for a project with status, workstream list, activity feed
- **Create dialog** — goal input, title, max workers, model selection; orchestrator auto-decomposes
- **Workstream list** with status icons (completed/active/pending/blocked/paused/failed)
- **Activity feed** — recent bus messages with timestamps
- **Pause/Stop/Resume controls** on hivemind cards
- **Full dashboard modal** — standalone 900x600 modal with sidebar (workstream selector), overview, and per-workstream detail views
- **Per-workstream detail** — description, findings count, session count, messages, manual worker spawn button, directive input
- **Synthesis viewer** — modal showing the current knowledge synthesis markdown
- **Directive inputs** — send messages to orchestrator or specific workstreams via the bus
- **SSE live updates** — hivemind dashboard auto-refreshes on bus events; escalation toasts
- **Proper cleanup** — SSE connections closed when dashboard modal is closed

## [2026-03-23] — Fix drag-and-drop in Tauri window
- Disable Tauri's native drag-drop interception (`dragDropEnabled: false`) so JS drop events fire
- Add document-level `dragover`/`drop` preventers to stop browser file-open on missed drops

## [2026-03-23] — Drag-and-drop file attachments in agent chat

### Drag-and-drop files into agent chat
- Drag files (images, documents, any file type) onto the dispatch or follow-up textarea
- Visual highlight on drag-over (accent border + dim background)
- Images show thumbnail previews; documents show filename with file icon
- Files uploaded via existing upload pipeline, referenced as `[Attachment: path]` (or `[Screenshot: path]` for images)
- Works alongside existing paste-to-attach functionality

## [2026-03-23b] — Fix native window in bundled app (root cause .NET fix)

### Build fixes (pre_build_fix.py)
- **Bug 1 fixed**: Replaced `net462` WinForms DLL with `netcoreapp3.0` variant from NuGet
  - The bundled `Microsoft.Web.WebView2.WinForms.dll` was targeting classic .NET Framework
  - pythonnet loads a .NET Core CLR, so the Framework DLL caused the crash
- **Bug 2 fixed**: Added `Python.Runtime.runtimeconfig.json` with `LatestMajor` roll-forward
  - Without this file, hostfxr refuses to roll forward across .NET major versions
  - Now works on .NET 6, 7, 8, 9, or any future version
- New `pre_build_fix.py` script automates both fixes before PyInstaller runs

### Graceful browser fallback
- `import clr` runs early to fail fast if .NET CLR can't load
- If native window fails for any reason, falls back to browser mode (no crash)
- .NET Desktop Runtime pre-detection with guided install dialog (auto-install or manual)

### .NET Desktop Runtime pre-detection
- Checks for .NET Desktop Runtime BEFORE attempting to load pywebview
- Detection via `dotnet --list-runtimes` (checks for `Microsoft.WindowsDesktop.App`)
- Fallback: Windows registry check at `HKLM\SOFTWARE\dotnet\Setup\InstalledVersions`

### Guided setup dialog when .NET is missing (dev mode)
- Three-button MessageBox: **Yes** (auto-install), **No** (open download page), **Cancel** (use browser)
- Auto-install via `winget install Microsoft.DotNet.DesktopRuntime.8`
- Manual install option opens the .NET 8.0 download page in browser
- Browser fallback always available — app fully functional without native window

## [2026-03-22c] — Global Settings UI, Agent Process Registration

### Global Settings modal
- New "Settings" button in header opens a 600px modal with all configuration options
- Organized into 5 sections: Identity, Agent Defaults, Claude Code Integration, Memory & Condensation, Paths & Server
- Toggle switches for boolean settings (streaming agent, remote control, auto-condense)
- Dropdowns for model and permission mode selection
- Settings save on change with toast notification
- API: `GET /api/config` and `PUT /api/config` endpoints for reading/writing config.json

### Per-project Remote Control toggle
- New "Remote Control" toggle in project three-dot menu (after Agent Model)
- Shows ON/OFF status; per-project override for the global setting
- When enabled, agents get `--remote-control` flag for claude.ai control

### Agent-reported process registration
- Agents can register spawned processes via `POST /api/processes/register`
- System prompt teaches agents to call the API when spawning background processes
- External processes visible in Process Manager with kill support

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
