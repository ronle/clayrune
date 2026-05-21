# Multi-Provider AgentRuntime — Design

**Workstream:** Runtime Architect
**Branch:** `feat/multi-provider-agents`
**Date:** 2026-05-21
**Status:** Draft for committee review — interface contract; no implementation yet
**Companion docs:**
- `docs/MULTI_PROVIDER_COUPLING_AUDIT.md` (ws_audit — what's coupled and where)
- `docs/MULTI_PROVIDER_CAPABILITY_MATRIX.md` (ws_research — what each provider offers)

---

## 1. Goals & Non-Goals

### Goals

1. **Abstract MC away from `claude-code`** so the same MC binary can drive any supported CLI agent (claude, codex, gemini, opencode, goose, aider, kiro).
2. **Keep every existing claude user working unchanged.** A user with `provider='claude'` (default) sees byte-identical behavior. No flag, file, or wire change visible to them.
3. **Keep MC features working where the provider supports them.** Skills, MCP, Memory/Scribe, Hivemind, plan-approval, transcripts, agent_log — all degrade gracefully per the table in §6, never silently drop.
4. **Use direct CLI** (subprocess + stdin/stdout JSON) — same shape as today's claude integration. **NOT provider APIs.** Auth, billing, OS install all stay the user's responsibility — MC just drives the binary they already have.
5. **JSONL transcript-style consumers stay compatible.** The on-disk `.jsonl` transcript files MC reads today (Scribe, revive, history) are preserved for claude; other providers store transcripts in their own native location, and the abstraction surfaces a `provider.transcript_path(...)` so MC subsystems that need them keep working without hardcoded paths.

### Non-Goals

- **No "ChatGPT-style" cloud API integration.** Direct CLI only.
- **No cross-provider session migration** (start on claude, continue on codex). Provider is bound at session creation.
- **No re-implementation of MCP/Skills/CLAUDE.md inside MC** to fill provider gaps. If a provider doesn't natively load them, MC adapts where there's a clean translation (write `GEMINI.md` instead of `CLAUDE.md`), and warns where there isn't.
- **No feature freeze on claude.** Claude-specific features (plan-mode UI, `claude_session_id`, stats-cache) keep shipping — they just live behind `runtime.capabilities()` flags.

---

## 2. Architectural Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    server.py / hivemind / scheduler          │
│  (calls runtime.dispatch / write_followup / interrupt / stop)│
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
              ┌────────────────────────┐
              │   agent_runtime.py     │
              │                        │
              │   get_runtime(name)    │──► AgentRuntime (ABC)
              │   register_runtime()   │
              └──────────┬─────────────┘
                         │
   ┌─────────┬───────────┼───────────┬──────────┬───────────┐
   ▼         ▼           ▼           ▼          ▼           ▼
ClaudeRT  CodexRT    GeminiRT    OpenCodeRT  GooseRT    AiderRT  KiroRT
   │         │           │           │          │           │       │
   ▼         ▼           ▼           ▼          ▼           ▼       ▼
claude    codex       gemini      opencode    goose      aider   kiro-cli
CLI       CLI         CLI         CLI         CLI        CLI     CLI
```

The runtime is a **passive driver around the provider CLI**. It owns:

- Binary resolution (replaces `_resolve_claude` / `_resolve_claude_bin`)
- Command assembly (replaces `_build_claude_flags` + 15+ Popen sites)
- Subprocess lifecycle (spawn / wait / kill / writes-to-stdin)
- Output parsing (one reader thread per session, normalized to `AgentEvent`)
- Mode A/B abstraction (synthetic B for providers without native persistent stream)
- Auth probing (replaces `/api/claude/auth-*` route family — becomes `/api/agent/<provider>/auth-*`)
- One-shot model calls (replaces `_scribe_call` for Scribe + condense planner)

It does **not** own:
- Session state (lives in `agent_sessions` in server.py)
- Project records (server.py)
- Memory/Scribe/Condense logic (server.py — but they call `runtime.oneshot()` instead of `_scribe_call()`)
- HTTP routing (server.py)

---

## 3. The `AgentRuntime` Interface

The full signature/docstring contract is in **`agent_runtime.py`**. Summary:

```python
class AgentRuntime(ABC):
    name: str                                     # 'claude', 'codex', 'gemini', ...
    display_name: str                             # 'Claude Code', 'Codex CLI', ...

    # Discovery / health
    def resolve_binary(self) -> Path: ...
    def health_check(self) -> HealthStatus: ...
    def capabilities(self) -> ProviderCapabilities: ...

    # Session lifecycle
    def dispatch(self, *, project_path, task, system_prompt,
                 resume_id, mode, model, max_turns,
                 incognito, env_extra, callbacks) -> SessionHandle: ...
    def write_followup(self, handle: SessionHandle, message: str,
                       attachments: list = None) -> None: ...
    def interrupt(self, handle: SessionHandle) -> None: ...   # hard kill
    def stop(self, handle: SessionHandle) -> None: ...        # graceful
    def read_stream(self, handle: SessionHandle) -> Iterator[AgentEvent]: ...

    # One-shot model call (Scribe / condense planner / summary / mcp_installer)
    def oneshot(self, *, prompt: str, system_prompt: str = '',
                model: str = '', max_turns: int = 1,
                stdin_text: str = None) -> OneshotResult | None: ...

    # Transcript / memory location
    def transcript_path(self, project_path: str, session_id: str) -> Path | None: ...
    def memory_path(self, project_path: str) -> Path | None: ...
    def context_file_path(self, project_path: str) -> Path | None: ...

    # Auth
    def auth_status(self) -> AuthState: ...
    def auth_login_command(self) -> list[str] | None: ...
```

### 3.1 `dispatch(...)` semantics

- **Returns synchronously** with a `SessionHandle` holding the session id, provider id (e.g. claude UUID), proc handle, and a thread-safe event queue.
- **Spawns a reader thread internally.** The reader emits normalized `AgentEvent`s into the handle's queue. Caller does not see the subprocess directly.
- **`mode='A'`** = spawn-per-turn semantics. For claude, this is `-p <task>` with `--print`. For codex/gemini/aider, same idea via their flags.
- **`mode='B'`** = persistent stream semantics. Native for claude (`--input-format stream-json`), opencode (`opencode acp`), kiro (`kiro-cli acp`). Synthetic for codex/gemini/goose — see §5.
- **`resume_id`** is a provider-native resume token (claude csid UUID, codex thread UUID, opencode session id, goose session id). Opaque to MC.
- **`system_prompt`** carries the MEMORY.md + AGENT_RULES.md + project context blob today injected via `--append-system-prompt`. The runtime decides per provider whether to:
  - Use a flag (claude `--append-system-prompt`, goose `--system`)
  - Write to a context file the provider auto-loads (gemini `GEMINI.md`, codex `AGENTS.md`)
  - Prepend to the task itself (opencode, kiro, aider via `--read` workaround)
- **`callbacks`** is an optional dict of named hooks (`on_session_id`, `on_tool_use`, `on_assistant_text`, `on_turn_end`, etc.) so MC can keep its existing per-session bookkeeping (claude_session_id capture, TodoWrite→backlog sync, plan-mode detection) without polling the event stream. Equivalent to today's `_read_agent_stream` inline handlers.

### 3.2 `write_followup(...)` semantics

- **Mode B (native B):** writes JSON to the live process's stdin (per claude's `{"type":"user","message":{"role":"user","content":...}}` envelope, or the provider's equivalent).
- **Mode B (synthetic B):** runtime treats this as "the previous turn ended; spawn a fresh process with the resume token and the new message". The handle's old proc is killed; a new proc inherits the same `SessionHandle` identity.
- **Mode A:** runtime spawns a new process with the provider's resume flag (claude `-r <csid>`, codex `exec resume <id>`, goose `session --resume`) and the new message. Same handle, new proc.

The caller does not branch on mode. The runtime owns the respawn dance.

### 3.3 `interrupt` vs `stop`

- **`interrupt(handle)`** — hard cancel. SIGKILL the process. Emit `AgentEvent(type='interrupted')`. Used by the MC interrupt button (today: `agent_interrupt` at server.py:6528).
- **`stop(handle)`** — graceful. Mode B: close stdin and let the process drain. Mode A: kill (current `agent_stop` behavior — Mode A processes are short-lived enough that "graceful" is a no-op).

### 3.4 `read_stream(handle)`

Generator that yields `AgentEvent` until the session reaches a terminal state (`turn_end`, `error`, `interrupted`, `auth_error`). Backed by the handle's thread-safe queue. Multiple concurrent consumers allowed (SSE + agent_log + scribe checkpoint all read the same stream). For multi-consumer use the runtime exposes `handle.subscribe()` to mint a per-consumer queue — see §4.5.

---

## 4. `AgentEvent` — The Normalized Wire Shape

Goal: claude's existing JSONL stream-json consumers (transcript scanner, log_lines writer, system_init capture, agent_log claude_session_id, TodoWrite→backlog sync, plan-mode detection) **all keep working unchanged** by reading `event.raw`. Non-claude providers populate the normalized fields and may leave `event.raw` empty or carry their own raw payload.

### 4.1 Shape

```python
@dataclass
class AgentEvent:
    type: EventType           # see §4.2
    provider: str             # 'claude', 'codex', ...
    session_id: str | None    # provider-native id (claude csid UUID, codex thread UUID)
    mc_session_id: str        # MC's own 12-char hex session id
    timestamp: str            # ISO-8601 UTC
    payload: dict             # type-specific (see §4.3)
    raw: dict | None          # full original message from the CLI, or None for synthesized events
    sequence: int             # monotonic per-session counter
```

### 4.2 `EventType` taxonomy

| Type             | Meaning                                              | Claude → MC source                                          |
|------------------|------------------------------------------------------|-------------------------------------------------------------|
| `init`           | Session metadata at session start                    | `{"type":"system","subtype":"init",...}`                     |
| `assistant_text` | Plain text from the model                            | `{"type":"assistant","message":{"content":[{"type":"text"}]}}`|
| `thinking`       | Reasoning trace (claude `thinking` block)            | `{"type":"assistant","message":{"content":[{"type":"thinking"}]}}`|
| `tool_use`       | Tool invocation (Read/Write/Bash/Custom MCP)         | `{"type":"assistant","message":{"content":[{"type":"tool_use"}]}}`|
| `tool_result`    | Tool output (where provider exposes it)              | (not surfaced by claude today; opencode/codex do)            |
| `user_message`   | User text echoed in stream (Mode B stdin)            | `{"type":"user","message":{...}}`                           |
| `turn_end`       | End of one assistant turn                            | `{"type":"result", session_id, usage, cost_usd, num_turns}` |
| `usage`          | Token/cost summary (often piggybacks on turn_end)    | `{"type":"result","usage":{...}}`                           |
| `rate_limit`     | Rate-limit info                                       | `{"type":"rate_limit_event", rate_limit_info:{...}}`        |
| `auth_error`     | Provider stderr matched an auth sentinel             | non-JSON line matching `_AUTH_ERROR_PATTERNS`                |
| `plan_request`   | Plan-mode pause / approval prompt                    | `tool_use(ExitPlanMode)` for claude; codex/goose equivalents |
| `question`       | `AskUserQuestion` invoked                            | `tool_use(AskUserQuestion)` for claude                       |
| `interrupted`    | Process was killed via `interrupt()`                 | synthesized                                                  |
| `process_exit`   | Underlying process exited (turn done in Mode A; or session crashed in Mode B) | rc + last-line surfacing            |
| `warn`           | Degradation notice (e.g. "Skills disabled on aider") | synthesized                                                  |
| `error`          | Stream/parse error or non-JSON garbage from CLI      | exception in reader / `[stream error: ...]`                  |

### 4.3 `payload` shape per `type`

| Type             | Payload fields                                                                              |
|------------------|---------------------------------------------------------------------------------------------|
| `init`           | `model`, `cli_version`, `permission_mode`, `mcp_servers[]`, `tools[]`, `skills[]`, `agents[]`, `plugins[]`, `slash_commands[]`, `cwd`, `memory_paths`, `fast_mode_state` (any subset; non-claude may only fill `model` + `cwd`) |
| `assistant_text` | `text: str`                                                                                  |
| `thinking`       | `text: str`                                                                                  |
| `tool_use`       | `name: str`, `input: dict`, `tool_use_id: str | None`                                       |
| `tool_result`    | `tool_use_id: str | None`, `output: str | dict`, `is_error: bool`                           |
| `user_message`   | `role: str`, `content: str`                                                                  |
| `turn_end`       | `usage: dict | None`, `cost_usd: float | None`, `num_turns: int | None`, `rc: int | None`   |
| `usage`          | `input_tokens`, `output_tokens`, `cache_read_input_tokens` (any subset)                     |
| `rate_limit`     | `status: str`, `resets_at: str`, `rate_limit_type: str`, `overage_status: str`, `is_using_overage: bool` |
| `auth_error`     | `reason: str` (`not_logged_in` | `invalid_api_key` | `unknown`), `raw_line: str`             |
| `plan_request`   | `plan_text: str | None`, `plan_file: str | None`                                            |
| `question`       | `question: str`, `options: list`, `question_id: str` (synthesized by runtime if absent)     |
| `process_exit`   | `rc: int`, `last_line: str | None`                                                          |
| `warn`           | `message: str`, `feature: str` (`skills`, `mcp`, `plan_mode`, ...)                          |
| `error`          | `message: str`, `recoverable: bool`                                                          |
| `interrupted`    | `reason: str`                                                                                |

### 4.4 Back-compat for stream-json consumers

For provider `claude`, every reader-thread side effect in `_read_agent_stream` / `_read_agent_stream_b` is preserved by:

1. **`event.raw` = the original parsed JSON object** — so `_capture_system_init(event.raw)`, `_handle_push_signal(..., event.raw)`, and any future claude-specific deep inspector keeps working unchanged.
2. **Hooks via `callbacks`** — `on_session_id`, `on_tool_use`, `on_assistant_text`, `on_turn_end` keep the inline mutations of `session['log_lines']`, `session['claude_session_id']`, `session['plan_file']`, `session['pending_questions']` exactly as they happen today.
3. **`_format_tool_activity`** stays — it's a UI rendering concern, not a parser concern.

For non-claude providers, MC's UI reads the normalized fields directly. Claude-only payload fields (e.g. `rate_limit`, `plugins`) simply never get emitted; the UI is already defensive about missing keys.

### 4.5 Multi-consumer streams

A `SessionHandle` exposes:

```python
handle.subscribe() -> Iterator[AgentEvent]   # per-consumer queue
handle.events                                  # primary iterator (alias for subscribe)
```

The runtime fans events out to all subscribers. This matches MC's current model where multiple subsystems (SSE generator, agent_log writer, Scribe checkpoint, plan-file detector) all observe the same stream. Subscribers that fall behind are not blocked — events are dropped from their queue after a high-water mark, with a synthesized `warn` event ("event queue overflow"); the primary log_lines never drops.

---

## 5. Mode A / Mode B Mapping per Provider

| Provider | Mode A (spawn-per-turn)               | Mode B (persistent stream)               | Followup mechanism                                  |
|----------|---------------------------------------|------------------------------------------|-----------------------------------------------------|
| **claude**   | Native: `claude -p <task> --print --verbose --output-format stream-json` | Native: `--input-format stream-json` + stdin JSON | Mode B: stdin JSON; Mode A: respawn with `-r <csid>` |
| **codex**    | Native: `codex exec <task> --json`     | **Synthetic B** (re-spawn with `codex exec resume <id>` each turn) | Same — runtime maintains the thread UUID and respawns. |
| **gemini**   | Native: `gemini -p <task> --output-format stream-json` | **Synthetic B** (re-spawn fresh, no resume; checkpoint only) | Each followup spawns a fresh process and the runtime prepends prior turn summaries as system context. |
| **opencode** | Native: `opencode run --format json "<task>"` | Native: `opencode acp` (nd-JSON over stdin/stdout) | Mode B: nd-JSON request line; Mode A: `--continue` or `--session <id>` respawn |
| **goose**    | Native: `goose run --no-session --output-format stream-json "<task>"` | **Synthetic B** (re-spawn with `goose session --resume`) | Same — runtime maintains the goose session id. |
| **aider**    | Native: `aider --message "<task>" --no-stream --yes` (plain text only) | **No B at all** — emit `warn` and fall back to Mode A. Each followup is a fresh `aider --message`. | git history is the only continuity. |
| **kiro**     | Native: `kiro-cli --no-interactive "<task>" --trust-all-tools` | Native: `kiro-cli acp` (JSON-RPC 2.0 handshake) | Mode B: JSON-RPC `session/prompt`; Mode A: respawn with `session/load` |

### 5.1 Synthetic Mode B contract

When the runtime advertises Mode B but the underlying provider has no persistent process:

1. `dispatch(mode='B')` spawns a single Mode-A-shaped process and presents it to MC as Mode B.
2. When the process exits (turn done), the runtime emits `turn_end`, then **keeps the SessionHandle alive** (status = `idle_between_turns`).
3. `write_followup(...)` spawns a fresh process using the provider's resume token, with the new message. The handle's `proc` is replaced. The event sequence continues.
4. `read_stream()` does not see a gap — it just sees `turn_end` then the next `init` for the new turn.

This means MC's existing Mode-B-aware code (the followup branch in `agent_followup` at server.py:6209–6291) **does not need to know** whether the provider is native-B or synthetic-B. The runtime handles it.

### 5.2 Mode selection rules

- `dispatch(mode='B')` is honored if `runtime.capabilities().supports_mode_b == True` (native or synthetic).
- `dispatch(mode='B')` on aider auto-downgrades to Mode A and emits a `warn` event once per session.
- The MC project setting `use_streaming_agent` (today's claude-only Mode-B toggle) becomes a per-provider boolean: `runtime.capabilities().default_mode` determines what `use_streaming_agent=True` means for that provider.

---

## 6. Graceful-Degradation Policy

### 6.1 Decision tree

For every claude-specific feature MC depends on, choose:

| Tier              | When to use                                  | User-visible effect                                                                 |
|-------------------|----------------------------------------------|-------------------------------------------------------------------------------------|
| **TRANSLATE**     | The target provider has a semantic equivalent | Feature works the same. Runtime maps claude → provider transparently.               |
| **DISABLE_QUIET** | No equivalent + feature is non-essential      | UI hides the panel/badge or shows "N/A". No warning.                                |
| **DISABLE_WARN**  | No equivalent + user might expect it          | One-time `warn` AgentEvent at session start, visible in agent log + status badge.  |
| **HARD_BLOCK**    | Genuinely unsafe combination                  | Dispatch refused with an explanatory error. **Not used in v1.**                     |

### 6.2 Per-feature × per-provider matrix

| Feature                  | claude    | codex        | gemini      | opencode   | goose       | aider          | kiro         |
|--------------------------|-----------|--------------|-------------|------------|-------------|----------------|--------------|
| **Stream JSON output**   | native    | TRANSLATE    | TRANSLATE   | TRANSLATE  | TRANSLATE   | DISABLE_WARN (line-buffered text only) | TRANSLATE (JSON-RPC) |
| **Session resume**       | native    | TRANSLATE    | DISABLE_WARN (checkpoint only) | TRANSLATE | TRANSLATE | DISABLE_WARN (no resume; new dispatch each followup) | TRANSLATE |
| **MEMORY.md context**    | flag      | TRANSLATE (write `AGENTS.md` at session start) | TRANSLATE (write `GEMINI.md`) | TRANSLATE (task prepend) | TRANSLATE (`--system <blob>`) | TRANSLATE (`--read <tmpfile>`) | TRANSLATE (config file or prepend) |
| **`--append-system-prompt`** | flag  | same as MEMORY.md | same | same | flag (`--system`) | `--read` | prepend |
| **MCP servers**          | native    | TRANSLATE (`~/.codex/config.toml`) | TRANSLATE (`mcpServers` config) | TRANSLATE (config `mcp` section) | TRANSLATE (extensions config) | **DISABLE_WARN** (no native MCP) | TRANSLATE (`--require-mcp-startup`) |
| **Skills (`.claude/skills/`)** | native | DISABLE_WARN (no equivalent — agent won't see the skill catalog) | DISABLE_WARN | DISABLE_WARN | DISABLE_WARN | DISABLE_WARN | DISABLE_WARN |
| **Plan-mode (`ExitPlanMode`)** | native | TRANSLATE (inline approval) | DISABLE_QUIET | DISABLE_WARN (experimental flag, off by default) | TRANSLATE (`/plan`) | TRANSLATE (`--dry-run`) | DISABLE_QUIET |
| **AskUserQuestion**      | native    | TRANSLATE (codex approval/permissions hook) | DISABLE_WARN (queued — UI surfaces "this provider can't ask back") | DISABLE_WARN | TRANSLATE (extension-side) | DISABLE_WARN | DISABLE_WARN |
| **system_init / model / cli_version** | native | TRANSLATE (parse JSON envelope) | TRANSLATE | TRANSLATE | TRANSLATE | partial (text scrape) | TRANSLATE |
| **rate_limit_event**     | native    | DISABLE_QUIET | DISABLE_QUIET | DISABLE_QUIET | DISABLE_QUIET | DISABLE_QUIET | DISABLE_QUIET |
| **stats-cache.json**     | native    | DISABLE_QUIET | DISABLE_QUIET | DISABLE_QUIET | DISABLE_QUIET | DISABLE_QUIET | DISABLE_QUIET |
| **Scribe (`oneshot` call)** | native (`claude -p`) | TRANSLATE (`codex exec --json`) | TRANSLATE (`gemini -p --output-format json`) | TRANSLATE (`opencode run`) | TRANSLATE (`goose run --quiet`) | TRANSLATE (`aider --message --no-stream --yes`) | TRANSLATE (`kiro-cli --no-interactive`) |
| **Condense (`agent` mode)** | native | TRANSLATE   | TRANSLATE   | TRANSLATE  | TRANSLATE   | DISABLE_WARN (no agentic file-edit mode at this granularity) | TRANSLATE |
| **TodoWrite → backlog**  | native    | DISABLE_QUIET (codex has no TodoWrite-equivalent tool name; user can still manage backlog manually) | DISABLE_QUIET | DISABLE_QUIET | DISABLE_QUIET | DISABLE_QUIET | DISABLE_QUIET |
| **PushNotification (web push)** | native | DISABLE_QUIET | DISABLE_QUIET | DISABLE_QUIET | DISABLE_QUIET | DISABLE_QUIET | DISABLE_QUIET |
| **CLAUDE.md auto-load**  | native    | TRANSLATE (`AGENTS.md`) | TRANSLATE (`GEMINI.md`) | not standard | not standard | not standard | not standard |

### 6.3 Brief's example clarified

> The brief says "ChatGPT has no MCP — does MC translate, disable, or warn?"

Per `MULTI_PROVIDER_CAPABILITY_MATRIX.md` §2, **Codex CLI (the OpenAI CLI agent) DOES support MCP** via `~/.codex/config.toml`. So that specific example doesn't apply — codex gets TRANSLATE for MCP. **The only Tier-1/2 provider without MCP is Aider**, where MCP gets `DISABLE_WARN`: at session start the runtime emits a `warn` event ("Aider does not support MCP — configured MCP servers will not be available this session") visible once per session in the agent log.

### 6.4 Where degradation lives

- **Warnings** are emitted as `AgentEvent(type='warn')` at session init by the runtime — not as HTTP 4xx errors. MC's existing log_lines machinery surfaces them in the chat history.
- **Translations** are owned by the runtime subclass. Example: `GeminiRuntime.dispatch()` will write the `system_prompt` to `<project>/GEMINI.md` (and optionally to `~/.gemini/GEMINI.md` for global context) before spawning. The MC-side caller doesn't know.
- **DISABLE_QUIET hides UI elements** via `runtime.capabilities()` flags read by `static/index.html` (single new API: `GET /api/agent/<provider>/capabilities`). No new code paths in JS — existing badges already conditionally render.

---

## 7. Provider Registry & Selection

### 7.1 Per-project provider

Add `provider: str` field to project records (default `claude`). On project create the UI offers a dropdown of installed providers (filtered by `runtime.health_check().installed == True`).

### 7.2 Runtime registry

```python
# agent_runtime.py
_RUNTIMES: dict[str, AgentRuntime] = {}

def register_runtime(runtime: AgentRuntime) -> None: ...
def get_runtime(name: str) -> AgentRuntime: ...
def available_runtimes() -> list[AgentRuntime]: ...
def installed_runtimes() -> list[AgentRuntime]: ...
```

Initial registrations happen at import time in `agent_runtime.py` — `ClaudeRuntime` always registers (default). Others register only if their feature flag is enabled OR their binary is found on PATH. `CONFIG['multi_provider_enabled']` global feature flag (default False) gates whether non-claude entries show up in the UI dropdown at all. Roll back by setting `multi_provider_enabled=false` — all projects fall back to claude, and the per-project `provider` field is ignored.

### 7.3 Backwards compatibility

- A project record without a `provider` field is treated as `provider='claude'`. No migration needed.
- `CONFIG['multi_provider_enabled'] == False` (default) hides every non-claude code path: the dropdown only shows claude, `get_runtime('claude')` is the only callable, and the existing claude paths run unchanged.
- The `/api/claude/auth-*` route family stays for v1 as a back-compat alias for `/api/agent/claude/auth-*` (the new namespaced route). UI updates in a later PR.

---

## 8. Auth, One-Shot, and Capabilities Sub-Interfaces

### 8.1 `HealthStatus`

```python
@dataclass
class HealthStatus:
    installed: bool
    binary_path: Path | None
    version: str | None
    auth_state: AuthState           # see 8.3
    install_hint: str               # human-readable for the UI ("npm install -g @openai/codex")
    diagnostic: str                 # last error if anything failed
```

### 8.2 `ProviderCapabilities`

```python
@dataclass
class ProviderCapabilities:
    name: str
    supports_mode_a: bool            # always True (Mode A is the universal baseline)
    supports_mode_b: bool             # True for claude, opencode, kiro (native) + codex, gemini, goose (synthetic)
    mode_b_kind: Literal['native','synthetic','none']
    supports_session_resume: bool
    supports_mcp: bool
    supports_skills: bool             # only claude today
    supports_plan_mode: bool          # plan_request events fire
    supports_ask_user_question: bool
    supports_streaming_text: bool     # vs. only end-of-turn flush (aider)
    emits_usage: bool                 # turn_end.payload.usage populated
    emits_rate_limit: bool
    context_injection: Literal['flag','file','prepend','read-file']
    context_file_name: str | None     # 'CLAUDE.md', 'AGENTS.md', 'GEMINI.md', None
    oneshot_supported: bool           # can Scribe/condense use this provider?
    default_mode: Literal['A','B']
```

### 8.3 `AuthState`

```python
@dataclass
class AuthState:
    status: Literal['ok','not_logged_in','invalid_api_key','unknown','not_installed']
    method: str | None               # 'env:CODEX_API_KEY', 'oauth:~/.codex/auth.json', etc.
    last_checked: str                # ISO
    error_text: str | None
```

### 8.4 `oneshot(...)`

```python
@dataclass
class OneshotResult:
    text: str                        # final assistant text
    raw: dict | None                 # full envelope if provider returns JSON
    usage: dict | None
    cost_usd: float | None
```

Used by Scribe, condense planner, project summary generation, and mcp_installer's `_extract_via_claude` + `security_scan`. Returns `None` if the provider can't do a non-streaming call (aider can fake it; gemini, codex, claude all support `--output-format json` directly).

---

## 9. Migration Plan (server.py call-site translation)

Each row maps a current Popen call site to its replacement. Listed in the recommended migration order — the bottom half can stay claude-only in the first PR.

| Current site (server.py) | New form                                                                  |
|--------------------------|---------------------------------------------------------------------------|
| `_resolve_claude()` calls | `runtime.resolve_binary()`                                                |
| `_build_claude_flags(p, streaming=...)` | folded into `runtime.dispatch()`                              |
| `subprocess.Popen([_resolve_claude(), -p, task, *_build_claude_flags(p), --append-system-prompt, ctx])` (Mode A primary, 5836) | `runtime.dispatch(project_path=pp, task=task, system_prompt=ctx, resume_id='', mode='A', model=..., max_turns=..., callbacks={...})` |
| Mode B Popen at 5773 + stdin JSON envelope at 5787 | `runtime.dispatch(..., mode='B')` — initial message handled internally |
| `_read_agent_stream` / `_read_agent_stream_b` | replaced by the runtime's reader thread + `callbacks` dict passed at dispatch |
| Followup respawn at 6359 / 6431 / stdin write at 6304 | `runtime.write_followup(handle, message)` |
| Guardian respawn at 6622 / 6666 | `runtime.write_followup(handle, recovery_msg)` |
| Revive at 4134 / 4194 / 4734 | `runtime.dispatch(..., resume_id=<csid>)` (runtime returns a fresh handle adopting the prior session id) |
| Auto-recover at 3682 / 3718 | same as revive |
| Hivemind worker spawn at 8141 / 8474 | `runtime.dispatch(..., mode='A')` with `runtime` resolved from the hivemind worker's provider (initially: same as parent project) |
| Hivemind orchestrator at 8351 | same |
| Condense agent at 5576 | `runtime.dispatch(..., mode='A', housekeeping=True)` OR a future `runtime.condense_agent(...)` helper |
| `_scribe_call` at 4978 | `runtime.oneshot(prompt=..., stdin_text=transcript_text)` |
| `_condense_plan` (calls `_scribe_call`) | same |
| `mcp_installer.py:_extract_via_claude` (413) | `runtime.oneshot(...)` — runtime sourced from MC-global default provider for housekeeping tasks |
| `mcp_installer.py:security_scan` (755) | same |
| `/api/project/<id>/generate_summary` (1887) | `runtime.oneshot(prompt=..., model=...)` |
| `/api/claude/auth-status` (2720), `/api/claude/login-launch` (2727), `/api/claude/auth-probe` (2767) | new family: `/api/agent/<provider>/auth-status`, `/api/agent/<provider>/login-launch`, `/api/agent/<provider>/auth-probe`. Old paths kept as claude aliases for v1. |
| `_native_memory_path` (628) | `runtime.memory_path(pp)` (claude returns the current path; others return `None` or a provider-native path) |
| `_find_transcript_file` (565), `_recent_claude_transcripts` (491), `_parse_transcript_messages` (583) | runtime-namespaced. `runtime.transcript_path(pp, sid)` + `runtime.parse_transcript(path)`. Non-claude providers may return `None` (no transcript store) — Scribe + revive gate on `runtime.capabilities().supports_session_resume`. |

### 9.1 First-PR scope (recommended)

To keep the first PR reviewable:

1. Land `agent_runtime.py` with `ClaudeRuntime` as the only registered provider — it wraps the existing `_resolve_claude` / `_build_claude_flags` / `_read_agent_stream*` exactly as they are today.
2. Migrate `_dispatch_agent_internal` (5700–5887), `agent_followup` (6163), `_read_agent_stream*` (3344, 3504) to go through `ClaudeRuntime`.
3. Leave everything else (Scribe, condense, hivemind, mcp_installer, auth routes) calling `_resolve_claude()` directly. They're trivially migrateable in PR 2.
4. Add `CONFIG['multi_provider_enabled']` flag (default False). Add a second runtime (Codex, since it's most claude-like per §2 of the capability matrix) gated behind the flag, **with a smoke test**, in PR 3.

This sequencing means:
- PR 1: pure refactor — no behavior change for any user.
- PR 2: refactor housekeeping call sites.
- PR 3: first real multi-provider provider lands, behind a flag.

---

## 10. Open Questions for Committee Review

1. **Project-level vs session-level provider:** is `provider` a property of the project, or can a single project have sessions on different providers? **Recommendation:** project-level. Same project, same memory, same code, same provider. Cross-provider experimentation happens via new projects.
2. **Auto-converting MEMORY.md to GEMINI.md / AGENTS.md:** do we write these files into the user's project directory? They'll show up in `git status`. **Recommendation:** write them, but gitignore-friendly path (`<project>/.clayrune/AGENTS.md` and a symlink-or-copy to the conventional location). Open question because this affects the user's working tree.
3. **Hivemind workers across providers:** the hivemind worker brief at server.py:2848 warns against `ExitPlanMode`. If a worker runs on codex (which has plan-mode), do we keep that warning, swap to codex's equivalent, or drop it? **Recommendation:** runtime-specific worker briefs — the runtime owns its own "worker addendum" string that gets prepended/appended to the brief.
4. **mcp_installer's security_scan provider choice:** today it always uses claude. Should it follow the project's provider, the global default, or always the most-capable available provider? **Recommendation:** always the global "housekeeping provider" config field (default claude). Security scan quality matters more than provider parity.
5. **Synthetic-B turn boundaries:** how does MC know a synthetic-B "turn" ended? The runtime emits `turn_end` on process exit, but a crashed process is hard to distinguish from a clean exit. **Recommendation:** use the same rc + last-line heuristic as today's Mode A; mark synthetic-B `turn_end` with `payload.synthetic=True` so the consumer can be more lenient.
6. **`/api/claude/*` route deprecation timeline:** old route family stays as alias for v1. **Recommendation:** deprecate in v2 (one minor release after multi-provider lands), remove in v3.

---

## 11. Test Plan (for ws_testplan / the user to validate this design)

The user-runnable test plan that proves the abstraction holds:

1. **Claude unchanged smoke test:** with `multi_provider_enabled=False`, every existing claude user flow (dispatch, follow-up, interrupt, stop, revive from log, web-push revive, hivemind, Scribe, condense, plan approval, AskUserQuestion) works byte-identically. Verified by `git diff` on `_read_agent_stream*` and `_dispatch_agent_internal` behavior plus a regression run of the test suite.
2. **Codex Mode A:** dispatch a simple "list files in cwd" task on a codex-installed project. Verify `init`, `assistant_text`, `tool_use`, `turn_end` events fire. Verify agent_log row gets `provider='codex'` and a codex thread UUID in the `provider_session_id` field.
3. **Codex follow-up via synthetic B:** dispatch, wait for `turn_end`, send a follow-up. Verify the runtime re-spawns with `codex exec resume <id>` and the new turn continues the prior context (model knows what was discussed).
4. **Gemini Mode A:** same as codex but for gemini. Verify `GEMINI.md` was written into the project dir and the model sees the MEMORY.md content.
5. **Aider degradation:** dispatch on an aider project. Verify a `warn` event fires for MCP and Skills. Verify a follow-up creates a fresh git commit (no session resume).
6. **Mode B follow-up on claude:** unchanged from today.
7. **Stop / interrupt on every provider:** verify both terminate the process cleanly and emit `process_exit` (stop) or `interrupted` (interrupt).
8. **`oneshot`:** trigger a Scribe extract on each provider's project — verify MEMORY.md gets a one-line entry written through that provider.

---

## 12. Summary

The AgentRuntime interface is a single Python ABC with 9 methods, normalizing 17 distinct claude-couplings into one well-defined seam. Modes A and B are presented uniformly to MC; the runtime handles native vs synthetic implementations. The graceful-degradation policy uses a four-tier decision tree (TRANSLATE / DISABLE_QUIET / DISABLE_WARN / HARD_BLOCK) with a per-feature × per-provider matrix. The first PR is a pure refactor (no user-visible change); the first non-claude provider lands behind a feature flag in a later PR. The full type contract is in `agent_runtime.py`.
