# Multi-Provider Coupling Audit

**Workstream:** Coupling Auditor  
**Date:** 2026-05-21  
**Scope:** Complete inventory of every place MC depends on claude-code specifically  
**Method:** Full codebase walk of `server.py` (12,648 lines), `mcp.py`, `skills.py`, `mcp_installer.py`

---

## Summary

Every agent-facing subsystem in MC is tightly coupled to claude-code. There is no provider abstraction layer. The coupling runs through approximately **17 distinct coupling categories** covering binary resolution, CLI flags, output protocol, session continuity, transcript storage, memory layout, skills/MCP config conventions, auth flow, and model-call helpers. Existing claude users are unaffected by adding a provider layer **provided** all existing call sites go through a new abstraction rather than being altered.

---

## Category 1 — Binary Resolution

### `_resolve_claude` (server.py:60–109)
The function `_resolve_claude()` hardcodes every known install path for the `claude` CLI binary:
- `APPDATA/npm/claude.cmd` (Windows npm)
- `USERPROFILE/.claude/bin/claude.cmd` / `.exe` (Windows native installer)
- `~/.claude/bin/claude` (Linux/macOS native)
- `~/.local/bin/claude`, `~/.npm-global/bin/claude`
- `/usr/local/bin/claude`, `/opt/homebrew/bin/claude`

The Windows-specific logic (server.py:65–82) also detects and prefers `claude.cmd` over a stale `claude.exe` orphan. Returns the string `'claude'` as last resort.

The helper `_claude(*args)` at server.py:112–114 wraps it into a command list.

### `_resolve_claude_bin` (mcp_installer.py:385–410)
A near-duplicate of `_resolve_claude` in `mcp_installer.py`. The docstring at mcp_installer.py:386 explicitly says it mirrors server.py:_resolve_claude. Does not include the `.exe` orphan detection logic.

**Impact:** Every agent Popen, every Scribe call, every condense call, every Claydo call, every hivemind worker call goes through one of these two functions. Replace both with a `provider.resolve_binary()` method.

---

## Category 2 — CLI Flags (`_build_claude_flags`)

### `_build_claude_flags` (server.py:910–934)
Outputs a list of entirely claude-specific flags:

| Flag | Purpose | Claude-specific? |
|---|---|---|
| `--print` | Run non-interactively | Yes |
| `--verbose` | Enable verbose output | Yes |
| `--output-format stream-json` | JSONL streaming output | Yes |
| `--dangerously-skip-permissions` | Skip permission prompts | Yes |
| `--input-format stream-json` | JSONL streaming input (Mode B) | Yes |
| `--model <name>` | Model selection | Yes (name format) |
| `--max-turns <n>` | Turn limit | Yes |
| `--permission-mode <mode>` | Permission mode | Yes |
| `--channels <channels>` | Plugin channels | Yes |
| `--remote-control` | Remote control mode | Yes |

### Additional ad-hoc flags used at call sites

| Flag | Location | Note |
|---|---|---|
| `--append-system-prompt <context>` | server.py:5771, 5834, 4132, 4192, 3681, 3717, 6279–6280, 6429–6430, 6620–6621, 6664, 8131, 8468 | System prompt injection — claude-only |
| `-r <csid>` | server.py:5767, 5830, 4116, 4727, 6260, 6422, 6613 | Resume by session ID — claude-only |
| `--continue` | server.py:4729, 6424 | Continue most recent session — claude-only |
| `-p <task>` | Nearly every Popen site | Prompt flag — equivalent in other CLIs differs |
| `--output-format json` | server.py:1887, mcp_installer.py:439, 765 | JSON (non-streaming) output — claude-only |
| `--strict-mcp-config` | server.py:1437 (_CLAYDO_NO_TOOLS_FLAGS) | Restrict MCP — claude-only |
| `--mcp-config <json>` | server.py:1438 (_CLAYDO_NO_TOOLS_FLAGS) | Inline MCP config — claude-only |
| `--tools ""` | server.py:1436 (_CLAYDO_NO_TOOLS_FLAGS) | Disable tools — claude-only |

---

## Category 3 — `_dispatch_agent_internal` and All Subprocess.Popen Agent Spawn Sites

### `_dispatch_agent_internal` (server.py:5700–5887)
The core dispatch function. Entirely claude-specific:
- Calls `_resolve_claude()` at every branch
- Builds commands with `_build_claude_flags()`
- Uses `-r <resume_id>` for session continuity (server.py:5767, 5830)
- Uses `--append-system-prompt` for context injection (server.py:5771, 5834)
- Mode B sends the initial message as a claude stream-json envelope (server.py:5787–5792)
- Session dict captures `claude_session_id` from output

### Complete inventory of agent-spawning `subprocess.Popen` calls

| Location | Context | Notes |
|---|---|---|
| server.py:3682 | Mode B auto-recover fresh retry | _auto_recover_failed_resume |
| server.py:3718 | Mode A auto-recover fresh retry | _auto_recover_failed_resume |
| server.py:4134 | Revive Mode B | _revive_from_agent_log |
| server.py:4194 | Revive Mode A | _revive_from_agent_log |
| server.py:4734 | Web-push revive | _handle_revive_on_push |
| server.py:5576 | Condense agent (condense_mode=agent) | housekeeping |
| server.py:5773 | _dispatch_agent_internal Mode B | primary dispatch |
| server.py:5836 | _dispatch_agent_internal Mode A | primary dispatch |
| server.py:6359 | Followup respawn Mode B | agent_followup |
| server.py:6431 | Followup Mode A | agent_followup |
| server.py:6622 | Guardian respawn Mode B | _project_guardian_loop |
| server.py:6666 | Guardian respawn Mode A | _project_guardian_loop |
| server.py:8141 | Hivemind worker API spawn | hivemind_workstream_spawn |
| server.py:8351 | Hivemind orchestrator | _hm_dispatch_orchestrator |
| server.py:8474 | Hivemind worker auto-spawn | _hm_auto_spawn_workers |
| server.py:13678 | system/status/refresh | spawn minimal session for init message |

Additionally `subprocess.run` calls (non-streaming):
- server.py:1575 (Claydo streaming)
- server.py:1722 (Claydo non-streaming)
- server.py:1891 (project summary generation)
- server.py:4985 (_scribe_call)
- server.py:13687 (system/status/refresh)

All spawn `_resolve_claude()` with claude-specific flags.

---

## Category 4 — JSONL Transcript Parser

### Stream-JSON Output Protocol (server.py:3344–3660)

Both `_read_agent_stream` (Mode A, server.py:3344) and `_read_agent_stream_b` (Mode B, server.py:3504) parse claude's proprietary `--output-format stream-json` JSONL:

**Expected message types:**

| Type | Fields consumed | Location |
|---|---|---|
| `assistant` | `message.content[].type = 'text'|'tool_use'|'thinking'`, `message.content[].text`, `.name`, `.input` | server.py:3369–3421 |
| `result` | `session_id`, `usage`, `cost_usd`, `num_turns` | server.py:3422–3432 |
| `system` (subtype=`init`) | `model`, `claude_code_version`, `apiKeySource`, `permissionMode`, `mcp_servers`, `tools`, `skills`, `agents`, `plugins`, `slash_commands`, `fast_mode_state`, `memory_paths`, `cwd` | server.py:13514–13548 |
| `rate_limit_event` | `rate_limit_info.{status, resetsAt, rateLimitType, overageStatus, isUsingOverage}` | server.py:13549–13562 |
| `user` | `message.role`, `message.content` | server.py:3358–3360 (Mode B stdin) |

**Mode B stdin envelope** (server.py:5787–5792):
```json
{"type": "user", "message": {"role": "user", "content": "<task>"}}
```
This is a claude-specific JSON protocol for streaming stdin.

### On-disk JSONL Transcript Files (server.py:491–625)

- `_recent_claude_transcripts` (server.py:491): scans `CLAUDE_HOME/<encoded>/*.jsonl`
- `_find_transcript_file` (server.py:565): finds `<csid>.jsonl` by session UUID
- `_parse_transcript_messages` (server.py:583): parses the same JSONL format as the live stream readers — type=`user`/`assistant`/`tool_use` messages

### Scribe Transcript Renderer (server.py:4877–4942)
`_scribe_render_lines` (server.py:4877) also parses the on-disk JSONL format, rendering it to text for the Scribe model call.

---

## Category 5 — Agent Log Shape (`claude_session_id`)

The `agent_log` entries carry a `claude_session_id` field (the UUID emitted by claude in stream-json output). This ID is:

1. **Captured** from stream output by `_note_claude_sid` (server.py:4237), hooked into both stream readers
2. **Backfilled** into pending agent_log rows for scheduled/hivemind sessions (server.py:4260–4272)
3. **Used for resume** via `-r <csid>` in every respawn/revive/followup path
4. **Used for transcript lookup** via `_find_transcript_file(pp, csid)` for Scribe, revive, history
5. **Used in backfill** at server.py:3820–3841 where transcripts are matched by `.jsonl` filename = csid
6. **Surfaced** via `/api/project/<id>/transcript/<claude_session_id>` endpoint (server.py:9060)

The `transcript_path` field in Scribe checkpoint records (server.py:766) also stores a claude-layout path.

**Other claude-specific session dict fields:**
- `'mode': 'A'|'B'` — maps to spawn-per-turn vs persistent process (both are claude modes)
- `'_resume_id'` — stores the claude csid for resume
- `'housekeeping': True` — condense/hivemind sessions that skip MEMORY.md (a claude convention)

---

## Category 6 — `--append-system-prompt` / `-r <sid>` / `--continue` Flags

### `--append-system-prompt`
Used at **14 call sites** (see Category 2 table). Injects MEMORY.md + AGENT_RULES.md + project context as a system-prompt extension. This is a claude-code-exclusive flag. Equivalent for other providers:
- Gemini CLI: no equivalent flag (would require task prepending)
- Codex/GPT CLI: no equivalent
- Aider: has `--system-prompt` but different syntax and scope

### `-r <csid>` (resume)
Used at **7 call sites** (see Category 2 table). Continues a prior claude conversation by UUID. No equivalent in any other CLI.

### `--continue`
Used at **2 call sites** (server.py:4729, 6424). Continues the most recent claude session. No equivalent in other CLIs.

---

## Category 7 — Plan-Mode Detection (`ExitPlanMode` / `EnterPlanMode`)

### Stream readers check for `ExitPlanMode` tool name
- `_read_agent_stream` (server.py:3385): `elif tool_name == 'ExitPlanMode':`
- `_read_agent_stream_b` (server.py:3540): identical check

When detected: sets `session['waiting_for_plan_approval'] = True`, stores the last `.md` file written as `session['plan_file']`, appends `[Plan mode exit detected — waiting for user approval]`.

This is a claude-code-specific tool (part of the `EnterPlanMode`/`ExitPlanMode` built-in tool pair). No other CLI agent has plan-mode.

### Hivemind worker brief (server.py:2848)
The system prompt injected into hivemind worker sessions explicitly warns:
> `"IMPORTANT — Plan Mode: Do NOT use EnterPlanMode or ExitPlanMode."`

This is present in the worker context string only because workers run in headless mode (no terminal for plan approval). The reference confirms plan-mode is an expected claude feature that needs to be guarded against in automated contexts.

---

## Category 8 — MEMORY.md Hooks

### Path derivation (server.py:628–664)
`_native_memory_path(project_path)` constructs:
```
~/.claude/projects/<path-encoded>/memory/MEMORY.md
```
Uses the same path encoding as transcript files (`_encode_project_path`, server.py:412–428). The `_get_memory_path()` function (server.py:651) uses this as primary, with a MC data-dir fallback.

### MEMORY.md is read into `--append-system-prompt` context
`_build_agent_context()` (called at most dispatch sites) reads MEMORY.md + AGENT_RULES.md and passes both via `--append-system-prompt`. This means the memory system is entirely coupled to: (a) the claude-specific transcript store path, (b) the `--append-system-prompt` flag to inject it.

### Scribe writes to the same path
`_scribe_extract` (server.py:5003) and `_write_session_memory` both write to `_get_memory_path(project)`, which is the claude-layout memory path.

### Condense reads/writes the same path
Both `_dispatch_condense` (agent mode, server.py:5459) and `_condense_apply` (structured mode, server.py:5323) read/write `_get_memory_path(project)`.

---

## Category 9 — Scribe (shells out to `claude -p`)

### `_scribe_call` (server.py:4978–5000)
Spawns:
```python
[_resolve_claude(), '-p', '--model', model, '--max-turns', '1', '--dangerously-skip-permissions']
```
with transcript text piped via stdin. Returns model's text output.

### Call sites for `_scribe_call`:
- `_scribe_summarize_text` (server.py:5038): whole-transcript summarization
- `_condense_plan` (server.py:5301): structured condense planner (also uses `_scribe_call`)

### `_scribe_extract` (server.py:5003–5035)
Finds the on-disk `.jsonl` transcript, renders it, calls `_scribe_summarize_text` → `_scribe_call`.

**Full Scribe dependency chain:**
1. Session ends → `_log_agent_completion` calls `_scribe_extract`
2. `_scribe_extract` finds `~/.claude/projects/<enc>/<csid>.jsonl`
3. `_scribe_render_transcript` parses it (JSONL format)
4. `_scribe_call` spawns `claude -p` with rendered text
5. Output is written to `MEMORY.md`

Every step is claude-specific.

---

## Category 10 — Condense Path

### Condense `agent` mode (server.py:5442–5616)
`_dispatch_condense` with `condense_mode='agent'` (default) spawns:
```python
[_resolve_claude(), '-p', prompt, '--model', model, '--max-turns', '14',
 '--print', '--verbose', '--output-format', 'stream-json', '--dangerously-skip-permissions']
```
The prompt instructs the agent to use the Write/Edit/Read tools on MEMORY.md and (optionally) CLAUDE.md. The agent is a full claude agentic session.

### Condense `structured` mode (server.py:5454–5456)
`_run_structured_condense` → `_condense_plan` → `_scribe_call` (a `claude -p` call). The `_condense_apply` function then applies results server-side. The plan step still calls `_resolve_claude()`.

### CLAUDE.md condensation (server.py:5471–5538)
The condense prompt (agent mode) explicitly references `CLAUDE.md` as a file that "Claude CLI loads natively" and instructs the agent to condense it if > 15 KB. CLAUDE.md is a claude-code convention — other providers have no auto-loaded instruction file.

---

## Category 11 — Hivemind Worker Dispatch

### `hivemind_workstream_spawn` (server.py:8100–8198)
Builds and spawns:
```python
cmd = [_resolve_claude(), '-p', task, '--print', '--verbose',
       '--output-format', 'stream-json', '--dangerously-skip-permissions',
       '--append-system-prompt', worker_context]
```
Optionally adds `--model` and `--max-turns`. Uses `_read_agent_stream` as the reader thread.

### `_hm_auto_spawn_workers` (server.py:8422–8526)
Identical command construction at server.py:8467–8468. Auto-spawns workers without HTTP round-trip.

### `_hm_dispatch_orchestrator` (server.py:8264–8420)
Spawns an orchestrator session at server.py:8351:
```python
cmd = [_resolve_claude(), '-p', task, '--print', '--verbose',
       '--output-format', 'stream-json', '--dangerously-skip-permissions',
       '--append-system-prompt', orch_context]
```

**All three hivemind process types are fully claude-specific.** The worker system prompt injected at server.py:2848 also warns against `EnterPlanMode`/`ExitPlanMode` — implicitly acknowledging these sessions use claude.

---

## Category 12 — Skills and MCP Install Paths (Claude Conventions)

### Skills: `~/.claude/skills/` (skills.py:41–54)
```python
GLOBAL_SKILLS_DIR = _home() / '.claude' / 'skills'
ARCHIVE_SKILLS_DIR = _home() / '.claude' / 'skills.archive'
STAGING_SKILLS_DIR = _home() / '.claude' / 'skills.staging'
GLOBAL_COMMANDS_DIR = _home() / '.claude' / 'commands'
GLOBAL_AGENTS_DIR = _home() / '.claude' / 'agents'
```
skills.py:3: _"Skills are Anthropic-format SKILL.md folders consumed natively by Claude Code."_ MC does not teach CC about skills — CC reads them natively. Other providers have no equivalent native skill-loading. SKILL.md format (Anthropic frontmatter schema) is claude-specific.

### Project skills: `<project_path>/.claude/skills/` (skills.py:53–54)
Also inside `.claude/` — a claude project convention.

### MCP global config: `~/.claude.json` (mcp.py:42)
```python
GLOBAL_CLAUDE_JSON = _home() / '.claude.json'
```
mcp.py:7: _"`~/.claude.json` — global; `mcpServers` top-level key."_ This file is owned by CC. The `mcpServers` JSON key is a claude convention. Project-level `.mcp.json` (mcp.py:44) is also a claude project convention.

### Startup installs
- `_install_builtin_skills()` (server.py:14253) seeds `~/.claude/skills/` on every boot
- `_install_builtin_mcps()` (server.py:14256) seeds `~/.claude.json` mcpServers on every boot

**Graceful degradation note:** Skills and MCP configs are written to the filesystem before any agent runs. A non-claude provider that does not read `~/.claude/` would simply not see them — the install would be a no-op. This is a graceful-degradation candidate rather than a hard block.

---

## Category 13 — Auth Endpoints

### `/api/claude/auth-status` (server.py:2720–2724)
Returns `_claude_auth_state`. Route name explicitly says `claude`.

### `/api/claude/login-launch` (server.py:2727–2764)
Opens a new OS terminal running `_resolve_claude()` so the user can run `/login`. This is the claude OAuth flow.

### `/api/claude/auth-probe` (server.py:2767–2798)
Runs `[_resolve_claude(), '-p', 'ok', '--max-turns', '1']` to probe auth state. Scans output for claude-specific error sentinels.

### Auth sentinel patterns (server.py:2678–2701)
```python
_AUTH_ERROR_PATTERNS = [
    (r'please\s+run\s*/login', 'not_logged_in'),
    (r'not\s+logged\s+in', 'not_logged_in'),
    (r'invalid\s+(?:api\s+)?key', 'invalid_api_key'),
    (r'authentication_error', 'unknown'),
]
```
These are claude-specific stderr sentinels. Scanned in both stream readers (server.py:3446, 3598).

### Error message (server.py:5925)
```python
'Claude CLI not found. Install it with: npm install -g @anthropic-ai/claude-code'
```
Hardcoded install instruction for claude CLI.

---

## Category 14 — System Status (`system/init` + `rate_limit_event`)

### `_capture_system_init` (server.py:13498–13564)
Hooked into both stream readers (server.py:3368, 3524). Parses:

**`system/init` message fields:** `model`, `claude_code_version`, `apiKeySource`, `permissionMode`, `mcp_servers[].{name, status}`, `tools[]`, `skills[]`, `agents[]`, `plugins[]`, `slash_commands[]`, `fast_mode_state`, `analytics_disabled`, `cwd`, `memory_paths`

**`rate_limit_event` fields:** `rate_limit_info.{status, resetsAt, rateLimitType, overageStatus, overageResetsAt, isUsingOverage}`

These are entirely claude-specific message types and fields. No other CLI emits `system/init` or `rate_limit_event` in this format.

### `system/status/refresh` endpoint (server.py:13699–13738)
Spawns a minimal claude session (server.py:13715) just to populate the status cache from the `system/init` message.

### `~/.claude/stats-cache.json` (server.py:13621–13696)
`/api/system/usage` reads `~/.claude/stats-cache.json` — a file Claude Code maintains with `dailyModelTokens`, `modelUsage`, `totalSessions`, `totalMessages`. Fields (`inputTokens`, `outputTokens`, `cacheReadInputTokens`) map to Anthropic API billing. No equivalent for other providers.

---

## Category 15 — mcp_installer Claude Calls

### `_resolve_claude_bin` (mcp_installer.py:385–410)
Duplicate of `_resolve_claude`. Two independent call sites:

### `_extract_via_claude` (mcp_installer.py:413–459)
"Tier 3" config extraction: spawns `[_resolve_claude_bin(), '-p', prompt, '--max-turns', '1', '--output-format', 'json']` to ask claude to parse a README and return mcpServers JSON. Called from `extract_config` (mcp_installer.py:485) when tier 1/2 heuristics fail.

### `security_scan` (mcp_installer.py:755–784)
Security analysis: spawns `[_resolve_claude_bin(), '-p', prompt, '--max-turns', '1', '--output-format', 'json']` with the MCP repo's source code. Caches by `(install_dir, sha)`. Error message hardcodes `claude rc=<n>` (mcp_installer.py:770).

---

## Category 16 — Claydo Feature

### `_CLAYDO_NO_TOOLS_FLAGS` (server.py:1435–1439)
```python
_CLAYDO_NO_TOOLS_FLAGS = [
    '--tools', '',
    '--strict-mcp-config',
    '--mcp-config', '{"mcpServers":{}}',
]
```
These three flags disable all tools and MCP servers for the Claydo assistant. All are claude-code-specific flags. Used at server.py:1562 (`/api/guide/stream`) and server.py:1716 (`/api/guide/ask`).

### Context materialized as CLAUDE.md (server.py:1522–1526)
Claydo's working directory has USER_GUIDE.md + CHANGELOG materialized as `CLAUDE.md` because the Claude CLI auto-loads it. The comment notes this avoids the 32 KB CreateProcess limit for `--append-system-prompt`.

---

## Category 17 — Project Summary Generation

### `/api/project/<id>/generate_summary` (server.py:1887–1926)
Spawns:
```python
[_resolve_claude(), '-p', prompt, '--model', model, '--output-format', 'json', '--dangerously-skip-permissions']
```
Then parses the JSON envelope `result` field (server.py:1906–1917). The `--output-format json` wrapper and `result` key are claude-specific.

---

## Coupling Severity Matrix

| Category | Description | Severity | Graceful Degradation |
|---|---|---|---|
| Binary resolution | `_resolve_claude` / `_resolve_claude_bin` | **BLOCKING** | Replace with provider.binary() |
| CLI flags | `_build_claude_flags()` | **BLOCKING** | Replace with provider.build_flags() |
| Stream-json protocol | Reader threads, Mode B stdin | **BLOCKING** | Needs per-provider reader |
| Session resume `-r`/`--continue` | All respawn/revive/followup paths | **BLOCKING** | Other providers skip resume |
| Transcript store | `~/.claude/projects/` JSONL | **BLOCKING** | Other providers have no transcript |
| Scribe | `_scribe_call` → `claude -p` | **BLOCKING** | Disable or use provider model API |
| Condense | Agent + structured modes | **BLOCKING** | Disable or use provider model API |
| Hivemind workers | All three spawn paths | **BLOCKING** | Use same provider dispatch abstraction |
| Plan-mode detection | `ExitPlanMode` tool name | **PARTIAL** | Other providers: never fires (graceful) |
| `--append-system-prompt` | Context injection | **BLOCKING** | Must prepend to task/prompt instead |
| Auth endpoints | `/api/claude/*` | **BLOCKING** | Per-provider auth flow needed |
| `system/init` parsing | Status + rate-limit | **DEGRADED** | Other providers: status panel shows N/A |
| Skills `~/.claude/skills/` | Native load path | **DEGRADED** | Non-claude providers won't load skills |
| MCP `~/.claude.json` | mcpServers config | **DEGRADED** | Non-claude providers don't use this file |
| Stats cache | `~/.claude/stats-cache.json` | **DEGRADED** | Other providers: usage panel empty |
| mcp_installer AI calls | `_extract_via_claude`, `security_scan` | **DEGRADED** | Disable or use provider model API |
| Claydo | `--strict-mcp-config` etc. | **DEGRADED** | Can use provider's equivalent |
| CLAUDE.md convention | Auto-loaded project context | **DEGRADED** | Other providers: inject context differently |
| Project summary gen | `--output-format json` | **DEGRADED** | Use provider model API directly |

---

## Key Abstraction Points

Based on the audit, a minimal multi-provider abstraction needs these seams:

1. **`provider.resolve_binary()`** — replaces `_resolve_claude()` and `_resolve_claude_bin()`
2. **`provider.build_dispatch_cmd(task, flags)`** — replaces `_build_claude_flags()` + all Popen call site command construction
3. **`provider.parse_output_line(line) → OutputEvent`** — replaces the `stream-json` parsing in both reader threads
4. **`provider.stdin_message(task) → str`** — replaces Mode B's JSON envelope
5. **`provider.resume_flags(csid) → list`** — replaces `-r <csid>` / `--continue` (returns `[]` for providers with no resume)
6. **`provider.inject_context(task, context) → str`** — replaces `--append-system-prompt` (prepend to task for other providers)
7. **`provider.model_call(prompt) → str`** — replaces `_scribe_call` for Scribe and condense planner
8. **`provider.transcript_reader(project_path, session_id)`** — replaces `_find_transcript_file` + `_parse_transcript_messages` (returns empty for non-claude)
9. **`provider.auth_endpoints`** — per-provider `/api/<provider>/auth-*` routes

Items 1–6 are BLOCKING for any basic multi-provider dispatch. Items 7–9 can degrade gracefully (Scribe/condense disabled; transcript empty; auth panel hidden).

---

## Files Audited

| File | Lines | Claude-coupling |
|---|---|---|
| `server.py` | 12,648 | Pervasive (all 17 categories) |
| `mcp.py` | ~600 | `~/.claude.json` path, `mcpServers` key |
| `skills.py` | ~1,000 | `~/.claude/skills/` paths, SKILL.md format |
| `mcp_installer.py` | ~800 | `_resolve_claude_bin`, `claude -p` calls |
