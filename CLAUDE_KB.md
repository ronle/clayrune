# Clayrune — Claude Knowledge Base
*Maintained by Claude Code sessions. Updated: 2026-05-17 (was 2026-03-23 — ~2-month refresh).*

## What changed since the last KB refresh
*(2026-03-23 → 2026-05-17. Read this first if you last saw the March KB.)*

- **Rebrand:** "Mission Control" → **Clayrune** (2026-05-01). Backend identifiers
  (`mc_remote`, `MC_*` env vars, repo name, Cloud Run service, keystore namespace)
  intentionally stay `mission-control` to avoid breaking installs. Mascot is
  **Claydo** (renamed from "Playdo" 2026-05-08); in-app helper is "Ask Claydo".
- **Hivemind shipped** — it was "next major feature" in March; it is now an
  implemented, in-use global surface (sidebar entry). See `docs/HIVEMIND_SPEC.md`.
- **New subsystems shipped:** Skills surface (Anthropic-format, `skills.py`),
  MCP servers manager (`mcp.py` + `mcp_installer.py`), Web push + native FCM
  Android APK, presence/focus-suppression push gate, Ask Claydo helper,
  Claude-driven installer, modal Aero-Snap/tile/pin, incognito mode, remote
  access platform (`mc_remote*`, `mc_tunnel`, `control_plane`), scheduler
  reliability + trigger-aware run history, video frame extraction, memsearch
  cross-session memory plugin.
- **New modules:** `skills.py`, `mcp.py`, `mcp_installer.py`; dirs
  `mc_remote/`, `mc_remote_iface/`, `mc_tunnel/`, `mc_tty_shim/`,
  `control_plane/`, `installer/`, `marketing/`.
- **server.py grew to ~12.5K lines / ~516 KB** — a split into Flask blueprints
  is planned (`IMPROVEMENT_PLAN_V2.md` P1-1) but not yet done.

---

## Project Identity
- **Name:** Clayrune (formerly "Mission Control"; rebranded 2026-05-01)
- **Owner:** Ron (levir). Trading projects use ET market hours (9:30am–4:00pm Mon–Fri).
- **Repo root:** `C:\Users\levir\Documents\_claude\mission-control`
- **GitHub:** https://github.com/ronle/mission-control (repo name kept as `mission-control`)
- **Purpose:** Multi-project management dashboard for Claude Code agents.
  Centralized UI to dispatch, monitor, and manage AI coding agents across all
  projects — now also a remote-access platform (clayrune.{com,dev,io,ai}).

---

## Architecture

| Layer | Stack |
|-------|-------|
| Backend | Python Flask, port 5199 |
| Frontend | Vanilla HTML/CSS/JS, single file: `static/index.html` (no build step, pure ES modules — deliberate constraint) |
| Desktop | pywebview (WebView2) wrapper via `app.py` (active path); Tauri v2 scaffold in `src-tauri/` (minimal/parked — confirm before relying on it) |
| Data | JSON files under `data/`; no database |
| Agent | Spawns `claude` CLI as subprocess (Mode A new-process / Mode B persistent stream) |
| Remote access | `mc_remote*` + `mc_tunnel` (Rust, source-available proprietary) + `control_plane/` (Cloud Run) + Cloudflare Access |

**Key files & modules:**

| File / dir | Role |
|------------|------|
| `server.py` | Flask backend — all API endpoints, agent process mgmt (~12.5K lines; split planned) |
| `static/index.html` | Entire frontend SPA (~942 KB single file) |
| `app.py` | Desktop entry point (pywebview + Flask in daemon thread) |
| `github_sync.py` | GitHub Issues bidirectional sync (`gh` CLI) |
| `skills.py` | Anthropic-format Skills surface (global + per-project; 5 built-ins) |
| `mcp.py` | MCP servers management surface |
| `mcp_installer.py` | MCP "Add from URL" install + security pre-flight |
| `pre_build_fix.py` | Tauri/.NET DLL + runtimeconfig build fix |
| `config.json` | User config (gitignored, auto-created) |
| `data/SHARED_RULES.md` | Rules injected into all agent prompts (checked in via config.json path) |
| `data/projects/*.json` | Per-project data (backlog, activity, settings) — gitignored |
| `data/schedules.json` | Local scheduler config — gitignored |
| `data/hiveminds/` | Runtime hivemind data — gitignored |
| `docs/HIVEMIND_SPEC.md` | Hivemind spec (implemented feature) |
| `docs/MEMORY_SYSTEM_SPEC.md` | Memory-system redesign DRAFT (not yet shipped) |
| `docs/USER_GUIDE.md` | Source for Ask Claydo helper context |
| `docs/remote-access/*.md` | Remote-access platform design (12 docs) |
| `mc_remote/`, `mc_tunnel/` | Remote-access binding + tunnel (carry PROPRIETARY.md) |
| `mc_remote_iface/` | Provider-interface contract for remote access |
| `mc_tty_shim/` | TTY shim for Rich/ANSI color in terminal pop-outs |
| `control_plane/` | Cloud Run control plane (has the only existing tests: `control_plane/tests/`) |
| `installer/` | Claude-driven installer |
| `marketing/` | Marketing site groundwork |

---

## Configuration (`config.json`)

```json
{
  "port": 5199,
  "shared_rules_path": "C:\\Users\\levir\\Documents\\_claude\\mission-control\\data\\SHARED_RULES.md",
  "projects_base": "C:\\Users\\levir",
  "user_name": "Ron",
  "use_streaming_agent": true
}
```

Other config: `agent_model`, `agent_max_turns`, `agent_name`,
`condense_threshold_kb` (30), `condense_model` (sonnet), `condense_enabled`,
`agent_channels`, `agent_remote_control`. (A per-project `use_streaming_agent`
override is proposed in `IMPROVEMENT_PLAN_V2.md` P1-3.)

---

## Agent Modes

- **Mode A:** New `claude` process per turn. Follow-ups queue and auto-dispatch.
- **Mode B** (`use_streaming_agent: true`, **global default**): Persistent
  process with `--input-format stream-json`. Follow-ups write to stdin.
  Faster. Respawns on resume if process dead.
- **Mode C / audio** split exists on a separate branch (`mode-c-audio`),
  sidelined — voice is not in the mainline.

---

## Feature Summary

### Core
- Tile-based project dashboard (snap-to-grid, drag tiles, status colors, domain tags)
- Per-project: status (Active/Waiting/Blocked/Parked), color, domain, description, path
- Multi-window system: drag, resize, minimize, tray, cascade, ESC closes focused
- Modal Aero-Snap zones + tile-all button + pin/unpin (`mc_modal_prefs.snap`)
- Sidebar surfaces: Backlog, Skills, MCP, Hivemind, Feed

### Agent Management
- Dispatch Claude Code agents; real-time SSE streaming output
- Session tabs per project; resume via `claude -r <uuid>`; revival from agent_log
- Follow-up messages, stop/resume, send files/screenshots (paste / drag-drop)
- AskUserQuestion: interactive forms in chat (full status pipeline → tiles)
- ExitPlanMode: auto-approval button; Plan History tab
- Terminal pop-out windows (xterm.js, ANSI via TTY shim, stdin, SSE)
- Token usage + cost tracking; live elapsed timer
- Incognito mode (global pseudo-project + per-project toggle)
- Video frame extraction for attached clips (`tools/extract-frames.sh`)

### Memory System
- Native MEMORY.md at `~/.claude/projects/<encoded-path>/memory/MEMORY.md`
- Auto-appends session summaries; MEMORY_ARCHIVE.md overflow; auto-condense >30KB
- memsearch plugin (Zilliz, local ONNX bge-m3) — cross-session semantic recall
- Redesign in flight: `docs/MEMORY_SYSTEM_SPEC.md` (DRAFT, not shipped)

### Backlog
- Per-project tasks, priority, drag-drop reorder, file attachments, dispatch to agent
- GitHub Issues bidirectional sync (`gh` CLI, ~5 min) — **known correctness
  gaps tracked in `IMPROVEMENT_PLAN_V2.md` P0-1..P0-7**

### Scheduler (local, per-project)
- Once / Daily / Interval / Cron; upcoming-jobs banner; trigger-aware run history
- 30-second background check loop. NOT the Anthropic cloud `/schedule` skill.

### Skills / MCP
- Skills: Anthropic-format, global + per-project, 5 checksum-preserved built-ins
- MCP: server manager, stdio/http/sse transports, Add-from-URL security pre-flight

### Push / Remote
- Web push (Android-first) + native FCM for the Clayrune Android APK shell
- Presence/focus-suppression gate (don't buzz when the chat is open & focused)
- Remote access platform: Cloudflare Access + `mc_tunnel` + `control_plane`
- Remote server restart (`/api/system/restart`) — heartbeat cross-dashboard detect
- Single-instance invariant: exactly one MC per port

### Other
- Process Manager (PID tracker, kill, cleanup orphaned)
- Global Settings modal; Ask Claydo in-app helper; Claude-driven installer
- Walkthrough tour; Ctrl+scroll zoom on agent output

---

## Active Backlog

> The March backlog table (Hivemind / window-snap / syntax-highlight / etc.)
> is **stale** — Hivemind, modal-snap, and most of it shipped. Live backlog is
> per-project JSON under `data/projects/` (gitignored; not visible from the
> repo). The current cross-cutting work queue is **`IMPROVEMENT_PLAN_V2.md`**:
> P0 github_sync correctness, P1 server split + test scaffolding + this KB
> refresh, P2 UX robustness, P3 cleanup. Also active: memory-system redesign
> (`docs/MEMORY_SYSTEM_SPEC.md` DRAFT) and the clayrune.io remote-access rollout.

---

## Hivemind (implemented feature — see `docs/HIVEMIND_SPEC.md`)

Shipped; in active use as a global sidebar surface (replaced the per-project
tab). Summary of the implemented design:

- **Hiveminds** = persistent collaborative multi-agent efforts with shared goals
- **Workstreams** = focused task areas, one owning agent at a time
- **Orchestrator** = decomposes goals, spawns workers, synthesizes, escalates
- **Workers** = standard MC agent sessions; disposable; inherit accumulated knowledge
- **Knowledge Base** = append-only findings JSONL, decisions log, open questions, synthesis
- **Message Bus** = persisted inter-agent channel
- Principle: *Agents are disposable; knowledge is permanent*
- Data at `data/hiveminds/{hivemind_id}/` (gitignored). `_hmEffectiveStatus`
  heuristic + server-side `_hm_reconcile_stale_on_startup`.

---

## Agent Session Rules (MANDATORY)

**Every session startup:**
1. Get current date/time in ET. If project tag is TRADING, state market status. Otherwise skip.
2. Read last 20 lines of CHANGELOG.md. State what was last done.
3. Confirm task with Ron before starting work.

**Every session shutdown** (when Ron says "done", "stop", "close", "end", "wrap up"):
1. Stop work immediately.
2. Append to CHANGELOG.md: `## [DATE TIME] — <summary>` with Done / State / Next / Files Changed sections.
3. Update `## Current Status` in CLAUDE.md.
4. Say: *"Session documented. Safe to close."*

**Every 15 tool calls:** Re-read AGENT_RULES.md (or this file if no project rules exist).

**SHARED_RULES:** After each major update to any project artifact, update relevant docs, commit, and push to git.

**Safety:** Temp/scratch files → `temp/` only. Ask before irreversible changes. Read first, ask second, act third.
Builds: Use local Docker Desktop only. No remote CI/CD.

---

## Recent Changelog Highlights (reverse chronological)

| Date | Change |
|------|--------|
| 2026-05-16 | Push policy: "waiting for me" + focus-suppression presence gate |
| 2026-05-15 | Activity feed redesign (bucketed/time-aware) + focus-theft + AskUserQuestion status + mobile reconcile |
| 2026-05-14b | Modal snap layouts, tile-all, pin/unpin, mobile SSE fixes, Clayrune onboarding project |
| 2026-05-14 | Native FCM push for the Clayrune Android APK shell |
| 2026-05-13b | MCP servers management surface + "Add from URL" security pre-flight |
| 2026-05-13 | In-dashboard Claude auth surface |
| 2026-05-11 | Web push notifications (Android-first) + PWA shell + deep linking + single-instance guard |
| 2026-05-10 | Skills surface (Anthropic-format) + import (paste/folder/Git URL/cross-project) |
| 2026-05-09 | Proactive update notification + marketing site mockups |
| 2026-05-08 | Claydo mascot rename + installer scaffold + Ask Claydo helper + video frame extraction + VM installer validation |
| 2026-05-07 | Installer scaffold (Claude-driven) + scheduled-task UI hang fix |
| 2026-05-06 | Hivemind global surface + trigger-aware run history + sizeAgentChat fix |
| 2026-05-05 | Sticky modals + remote server restart |
| 2026-05-01 | Rebrand to Clayrune + operator dashboards + scheduler timezone fix |
| 2026-04-30 | Firebase Auth + custom domain + CI/CD |
| 2026-04-29 | Device naming + auto-cleanup loop |
| 2026-04-28 | Backfill agent_log from Claude transcripts on startup |
| 2026-04-27 | Race-condition consolidation (Phase 1+2) + mobile UI iteration + SSE slot freeing |
| 2026-04-24 | Transcript-derived conversations + zero-gap resume picker |
| 2026-04-23 | Tile redesign + Mode-C/audio split + cross-project backlog |
| 2026-04-16 | Tauri launcher + CORS + AskUserQuestion race fix |
| 2026-04-15 | Per-project agent isolation + guardian overhaul |
| 2026-04-04 | Agent stability: health monitor & error recovery |
| 2026-03-23 | Hivemind Phase 2+3 + drag-drop file attachments (last March KB point) |

*(Full history in `CHANGELOG.md` — ~80 dated entries; this is the skim layer.)*

---

## Other Managed Projects (brief)

Ron manages these through Clayrune (not exhaustive):
- **apex_trader** — Trading/Apex platform project
- **daytrading** — Day trading system
- **day_trading_engulfing_scanner** — Engulfing pattern scanner
- **discord_reader** — Discord integration
- **options_trader** — Options trading
- **polymarket** — Polymarket integration
- **market_replay** — Market replay tool
