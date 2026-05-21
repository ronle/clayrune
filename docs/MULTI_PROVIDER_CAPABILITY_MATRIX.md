# Multi-Provider Agent CLI — Capability Matrix

> **Purpose:** Inform the `feat/multi-provider-agents` branch. Each row answers the
> nine integration questions that Mission Control needs to drive a CLI agent via
> subprocess + stdin/stdout JSON (the same shape as today's `claude` integration).
>
> **Research date:** 2026-05-21  
> **Coverage:** 6 viable candidates + 2 ruled out

---

## Quick Verdict

| Tool | Protocol fit | MCP | Session resume | Plan-mode | Auth for CI | Rec |
|------|-------------|-----|---------------|-----------|-------------|-----|
| **Gemini CLI** | stream-json JSONL | ✅ | partial | ✗ | API key | ✅ **Tier 1** |
| **Codex CLI** | JSONL (`--json`) | ✅ | ✅ | ✅ inline | API key | ✅ **Tier 1** |
| **Aider** | plain text only | ✗ | ✗ | `--dry-run` | env vars | ⚠️ **Tier 2** |
| **OpenCode** | nd-JSON / HTTP API | ✅ | ✅ | experimental | 75+ providers | ✅ **Tier 1** |
| **Goose** | json / stream-json | ✅ | ✅ | `/plan` cmd | env vars | ✅ **Tier 1** |
| **Kiro CLI** | JSON-RPC 2.0 ACP | ✅ | ✅ | not documented | paid only | ⚠️ **Tier 2** |
| ~~Amazon Q~~ | plain text only | ✅ | ? | ✗ | ❌ browser-only | ✗ ruled out |
| ~~Copilot CLI~~ | no headless mode | ? | ✗ | ✗ | IDE-only | ✗ ruled out |

---

## 1. Gemini CLI

**Source:** https://github.com/google-gemini/gemini-cli  
**Latest stable:** v0.32.1 (March 2026), powered by Gemini 3

### (a) stdin/stdout Protocol Shape

Headless mode is triggered by `-p`/`--prompt` flag or a non-TTY environment.

| Format | Flag | Shape |
|--------|------|-------|
| Plain text | `--output-format text` (default) | Human-readable string |
| Structured JSON | `--output-format json` | Single object: `{ "response": "...", "stats": { ... }, "error": { ... } }` |
| Streaming JSONL | `--output-format stream-json` | Newline-delimited JSON events: `init`, `message`, `tool_use`, `tool_result`, `error`, `result` |

Stdin piping is supported; the CLI accepts prompts via command-line args or stdin.

**Exit codes:** `0` success · `1` general/API error · `42` input error · `53` turn limit exceeded

### (b) Tool / Function Calling Support

Yes. Three layers:
- Built-in tools (configurable via `tools.core` allowlist)
- MCP server tools (auto-namespaced to avoid conflicts)
- Custom tool discovery via `tools.discoveryCommand`

### (c) MCP Support

**Yes.** `mcpServers` config section supports `command` (stdio), SSE URLs, and HTTP endpoints. Per-server `includeTools`/`excludeTools` allowlists. Tool names are prefixed to prevent collisions.

### (d) System Prompt Injection

**`GEMINI.md` file hierarchy** — no explicit CLI flag for arbitrary system prompts:
- Global: `~/.gemini/GEMINI.md`
- Project root and ancestor directories
- Subdirectories of CWD
- Additional files importable via `@path/to/file.md` syntax inside GEMINI.md

`/memory show` in interactive mode displays loaded context.

### (e) Session Resume

Checkpointing supported via `--checkpointing` CLI flag or `general.checkpointing.enabled` config. Enables session recovery. Full resume mechanics are not deeply documented; this is weaker than Codex/OpenCode/Goose.

### (f) Streaming

Yes — `--output-format stream-json` returns JSONL events in real-time.

### (g) Plan-Mode Equivalent

**None.** Gemini CLI has no dry-run or pre-approval step equivalent.

### (h) Install Instructions

```bash
# npm (recommended, requires Node.js 20+)
npm install -g @google/gemini-cli

# Homebrew (macOS/Linux, handles Node.js automatically)
brew install gemini-cli

# npx (one-off, no install)
npx @google/gemini-cli
```

### (i) Auth Model

| Method | Env vars / mechanism | Use case |
|--------|---------------------|----------|
| Gemini API key | `GEMINI_API_KEY` | Simplest; AI Studio key |
| Google OAuth | Browser flow (localhost redirect) | Local dev; free tier (1000 req/day) |
| Vertex AI (ADC) | `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION` | GCP service accounts |
| Vertex AI (API key) | `GOOGLE_API_KEY`, `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION` | Cloud API key |
| Vertex AI (Service Acct) | `GOOGLE_APPLICATION_CREDENTIALS`, `GOOGLE_CLOUD_PROJECT` | CI/CD recommended |

CLI exits with error in non-interactive mode if no suitable env var is found.

---

## 2. Codex CLI (OpenAI)

**Source:** https://github.com/openai/codex  
**Language:** Rust (native binary; fast startup)

### (a) stdin/stdout Protocol Shape

| Format | Flag | Shape |
|--------|------|-------|
| Plain text | (default) | Final agent message to stdout; progress to stderr |
| JSONL event stream | `--json` | Typed events per line: `thread.started`, `turn.started`, `item.completed`, etc. |
| Structured JSON | `--output-schema <path>` | Final response conforms to provided JSON Schema |
| File output | `-o <path>` | Writes final message to file (still prints to stdout) |

```bash
# Non-interactive invocation
codex exec "Refactor auth module" --json

# Resume previous session
codex exec resume --last "Continue the refactor"
codex exec resume <SESSION_ID>
```

### (b) Tool / Function Calling Support

Yes. Built-in tools + MCP server tools + web search (enabled by default for local tasks) + image generation via `$imagegen` skill. Tool usage surfaces in transcripts and JSON output.

### (c) MCP Support

**Yes.** Configuration in `~/.codex/config.toml`. Managed via `codex mcp` CLI commands (list, add, remove, authenticate). Supports STDIO and streaming HTTP servers. Servers auto-launch at session start.

### (d) System Prompt Injection

**`AGENTS.md` file hierarchy** (analogous to Claude Code's `CLAUDE.md`):
- Files are auto-loaded from `~/.codex` + each directory from repo root to CWD
- Merged in order; later directories override earlier
- Each file becomes a **user-role message**: `"# AGENTS.md instructions for <directory>"`
- Model is trained to closely adhere to these instructions

### (e) Session Resume

**Yes.** `codex exec resume --last "<task>"` continues previous run. `codex exec resume <SESSION_ID>` targets a specific session by thread ID (UUID format).

### (f) Streaming

**Yes.** `--json` flag streams JSONL events in real-time to stdout. Default mode streams progress to stderr only.

### (g) Plan-Mode Equivalent

**Yes — inline approval.** Codex explains its plan before making changes; user can approve or reject steps inline. Configurable via:
- `--ask-for-approval` / `--sandbox <level>` modes
- `--dangerously-bypass-approvals-and-sandbox` for full automation
- `/permissions` command switches approval modes mid-session

**Sandbox levels:** `auto` (default; file read/edit + commands in CWD), `read-only` (no changes), `full-access` (cross-machine)

### (h) Install Instructions

```bash
# npm (requires Node.js 18+)
npm install -g @openai/codex

# Homebrew (macOS; native binary)
brew install --cask codex

# Pre-built binary (all platforms)
# Download from: github.com/openai/codex/releases
```

### (i) Auth Model

| Method | Mechanism |
|--------|-----------|
| API key (recommended for CI) | `CODEX_API_KEY` env var |
| ChatGPT OAuth | Browser flow; credentials cached at `~/.codex/auth.json` |
| Device code flow | `codex login --device-auth` |
| Stdin API key | `codex exec --with-api-key` |
| Access token | `codex exec --with-access-token` |

Requirements: ChatGPT Plus/Pro/Team/Edu/Enterprise subscription **or** OpenAI API key with credits.

> ⚠️ **Security note:** CVE-2025-61260 (CVSS 9.8) — MCP server entries in local project config are
> auto-executed without interactive approval. Pin to a patched release.

---

## 3. Aider

**Source:** https://github.com/Aider-AI/aider  
**Language:** Python

### (a) stdin/stdout Protocol Shape

**Plain text only.** No JSON output mode. Aider is designed as a terminal pair-programmer, not a programmatic API.

```bash
# Single-shot non-interactive invocation
aider --message "Add docstrings to all public functions" src/auth.py

# With conventions file
aider --message "$prompt" --file "$file" --read CONVENTIONS.md --no-stream --yes

# Stdin workaround (not native)
echo "Add tests for auth.py" | aider --message "$(cat -)" auth.py
```

Aider does not natively read prompts from stdin. The `bash -c "... $(cat -)"` pattern is a community workaround. Process exits after each task; there is no persistent server mode.

### (b) Tool / Function Calling Support

**Built-in file editing tools only.** Aider applies code changes as unified diffs directly to files. No external tool calling, no function calling API.

### (c) MCP Support

**No native support.** Community project `mcpm-aider` exists as a wrapper but is not part of Aider itself. Do not rely on MCP with Aider.

### (d) System Prompt Injection

Via `--read <file>` flag — injects a **read-only** context file (protected from editing):
```bash
aider --read CONVENTIONS.md --read ARCHITECTURE.md --message "..."
```
Also configurable via `.aider.conf.yml` in home directory or project root.

### (e) Session Resume

**No.** Aider is stateless per invocation. Git history serves as continuity — atomic commits are written after each change, providing a natural audit trail and rollback mechanism.

### (f) Streaming

Yes — `--stream` / `--no-stream` flag (default: stream enabled).

### (g) Plan-Mode Equivalent

`--dry-run` flag — previews what changes would be made without modifying files.

### (h) Install Instructions

```bash
# pip (Python 3.9–3.12 required; 3.13 not yet supported)
pip install aider-chat

# Recommended: use uv or pipx for isolation
uv tool install --python python3.12 aider-chat

# Docker
docker run --rm -it paulgauthier/aider
```

### (i) Auth Model

Provider env vars (no unified auth command):

| Provider | Env var |
|----------|---------|
| OpenAI | `OPENAI_API_KEY` |
| Anthropic | `ANTHROPIC_API_KEY` |
| Google Gemini | `GEMINI_API_KEY` |
| Azure OpenAI | `AZURE_OPENAI_API_KEY` |
| AWS Bedrock | `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` |
| Any provider | `--api-key <provider>=<key>` CLI flag |

Also supports `.env` file with `AIDER_OPENAI_API_KEY` / `AIDER_ANTHROPIC_API_KEY` patterns.

> ⚠️ **MC integration note:** Aider is the weakest protocol fit. Plain text only, no session
> state, no JSON events, no MCP. Best suited as a fallback/simple wrapper that invokes
> `--message` and captures stdout as a plain string. MC features that rely on structured
> events (agent_log, transcript parsing, streaming turns) will need to degrade heavily.

---

## 4. OpenCode

**Source:** https://github.com/anomalyco/opencode (moved from sst/opencode)  
**Stack:** TypeScript + Go, runs on Bun; 153k+ GitHub stars

### (a) stdin/stdout Protocol Shape

Three modes:

| Mode | Invocation | Shape |
|------|-----------|-------|
| Run (non-interactive) | `opencode run --format json "prompt"` | JSON event stream |
| ACP (programmatic) | `opencode acp` | **nd-JSON (newline-delimited JSON)** over stdin/stdout |
| HTTP API | `opencode serve --port 4000` | REST API + SSE stream |

The **ACP mode** (`opencode acp`) is the primary programmatic interface: the controlling process writes JSON requests line-by-line to stdin and reads JSON responses line-by-line from stdout. This is similar to MCP's stdio transport shape.

### (b) Tool / Function Calling Support

Yes — built-in tools + MCP server tools. Tools are automatically available to the LLM.

### (c) MCP Support

**Yes.** Config file `mcp` section:

```json
{
  "mcp": {
    "my-server": {
      "type": "local",
      "command": ["npx", "-y", "my-mcp-command"],
      "enabled": true
    },
    "remote-server": {
      "type": "remote",
      "url": "https://mcp-server.example.com",
      "headers": { "Authorization": "Bearer TOKEN" },
      "oauth": {}
    }
  }
}
```

OAuth is handled automatically for remote servers.

### (d) System Prompt Injection

Not explicitly documented in the CLI reference. Investigation recommended before implementation. The `--prompt` flag exists for TUI mode but does not appear to be a system-prompt override.

### (e) Session Resume

**Yes:**
- `--continue` / `-c` — resume last session
- `--session` / `-s <SESSION_ID>` — resume specific session
- `--fork` — branch from session (works with both above)

Session data exportable/importable as JSON.

### (f) Streaming

Yes — `--format json` for the `run` command provides JSON event streaming.

### (g) Plan-Mode Equivalent

**Experimental:** `OPENCODE_EXPERIMENTAL_PLAN_MODE=1` environment variable.

### (h) Install Instructions

```bash
# curl (all platforms)
curl -fsSL https://opencode.ai/install | bash

# npm
npm install -g opencode-ai

# bun (recommended; faster)
bun add -g opencode-ai

# Homebrew
brew install anomalyco/tap/opencode
```

### (i) Auth Model

`opencode auth login` — interactive credential storage at `~/.local/share/opencode/auth.json`.

Supports **75+ providers** via Models.dev platform (Anthropic, OpenAI, Google, Ollama, OpenRouter, Azure, Bedrock, and more). Also reads standard provider env vars and `.env` files.

For server/headless mode: `OPENCODE_SERVER_PASSWORD` env var sets HTTP API auth.

---

## 5. Goose (Block / AAIF)

**Source:** https://github.com/aaif-goose/goose (transferred from block/goose to AAIF)  
**Language:** Rust

### (a) stdin/stdout Protocol Shape

| Format | Flag | Shape |
|--------|------|-------|
| Plain text | `--quiet` (`-q`) | Model response only, suppresses progress |
| Structured JSON | `--output-format json` | Results after completion |
| Streaming JSONL | `--output-format stream-json` | Events as they occur |

```bash
goose run --no-session --quiet --output-format stream-json "Explain this code"
```

### (b) Tool / Function Calling Support

Yes — built-in tools + extensions (70+ available). Extensions are the primary extensibility mechanism, built on MCP.

### (c) MCP Support

**Yes.** 70+ extensions via MCP. Configuration via `goose configure` (interactive) or YAML. Supports both HTTP and STDIO MCP server transports. Custom deeplink: `goose://` for one-click install from web.

### (d) System Prompt Injection

**Direct flag:** `goose run --system "You are an expert in Python async patterns."` Provides additional system instructions to customize agent behavior per-invocation.

### (e) Session Resume

**Yes:**
- `goose session --resume` — resume previous session
- `goose session --resume --fork` — branch with copied history
- Sessions stored in **SQLite database** (v1.10.0+; auto-migrated from legacy `.jsonl`)

### (f) Streaming

Yes — `--output-format stream-json` streams events as they occur.

### (g) Plan-Mode Equivalent

**Yes — `/plan` slash command:**
```
/plan <message_text>
```
Enters plan mode with optional message, creates a plan from current messages, and asks the user whether to act on it. Available in interactive mode.

### (h) Install Instructions

```bash
# curl (all platforms)
curl -fsSL https://github.com/aaif-goose/goose/releases/download/stable/download_cli.sh \
  | CONFIGURE=false bash

# After install, configure provider
goose configure
```

### (i) Auth Model

Provider env vars (15+ providers):

| Provider | Env var |
|----------|---------|
| OpenAI | `OPENAI_API_KEY` |
| Anthropic | `ANTHROPIC_API_KEY` |
| Google | `GOOGLE_API_KEY` |
| OpenRouter | `OPENROUTER_API_KEY` |
| Azure OpenAI | `AZURE_OPENAI_API_KEY` |
| AWS Bedrock | `AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY` |
| Ollama | No key (local) |

Also supports `goose configure` interactive flow. OAuth for headless use: `--header "X-API-Key: ..."`.

---

## 6. Kiro CLI (AWS / kirodotdev)

**Source:** https://github.com/kirodotdev/Kiro  
**Note:** AWS-developed; IDE + CLI product

### (a) stdin/stdout Protocol Shape

**JSON-RPC 2.0 over stdin/stdout** via `kiro-cli acp` — the most structured protocol of all candidates.

```python
# Subprocess integration pattern
proc = subprocess.Popen(
    ["kiro-cli", "acp"],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    bufsize=4*1024*1024  # 4MB; critical — unbuffered
)

# Initialize handshake
msg = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {
        "protocolVersion": 1,
        "clientCapabilities": {},
        "clientInfo": {"name": "mission-control", "version": "1.0"}
    }
}
proc.stdin.write(json.dumps(msg) + "\n")
proc.stdin.flush()

# Session lifecycle
# session/new → session/prompt → (response) → session/load (resume)
```

Separate daemon threads needed to monitor stdout/stderr (prevents blocking). Notifications omit `id` field; requests include `id`; responses include `result` or `error`.

Headless mode (simpler): `kiro-cli --no-interactive "Do the task"` with `--trust-all-tools` to auto-approve.

### (b) Tool / Function Calling Support

Yes — built-in tools + MCP server tools. Tool categories: `read`, `grep`, `write`. `--trust-tools=read,grep` auto-approves read-class tools.

### (c) MCP Support

**Yes.** `--require-mcp-startup` flag causes the CLI to fail immediately if any configured MCP server fails to connect (useful for CI/CD pipelines that depend on specific tools).

### (d) System Prompt Injection

Via agent configuration files (specifics not well-documented in public docs). Investigation recommended before implementation.

### (e) Session Resume

**Yes** — via ACP protocol methods `session/new` and `session/load`. Session state is preserved across `kiro-cli acp` invocations.

### (f) Streaming

**Yes** — metadata streaming via `_kiro.dev/metadata` ACP notifications.

### (g) Plan-Mode Equivalent

Not documented in current public docs.

### (h) Install Instructions

```bash
# curl (all platforms)
curl -fsSL https://cli.kiro.dev/install | bash
```

### (i) Auth Model

`KIRO_API_KEY` environment variable.

> ⚠️ **Critical limitation:** API key authentication **requires a paid subscription** (Kiro Pro,
> Pro+, or Power). Free-tier users must authenticate interactively via browser — not
> viable for MC's headless/server use case unless the operator has a paid account.

---

## 7. Ruled-Out Candidates

### Amazon Q Developer CLI

**MCP:** Yes (local + remote).  
**JSON output:** No — `q chat` outputs plain text/Markdown. Feature request open (issue #2713).  
**Headless auth:** Blocked — requires interactive browser authentication (issue #3343). Cannot be used in server/headless context.  
**Verdict:** Not viable for MC today. Revisit if issues #2713 + #3343 ship.

### GitHub Copilot CLI

**Scope:** Primarily an IDE extension (`gh copilot suggest` / `gh copilot explain`).  
**Protocol:** No headless/programmatic mode suitable for MC integration.  
**Verdict:** Not an agent CLI in the MC sense; ruled out.

---

## Capability Matrix Summary

| Capability | Gemini CLI | Codex CLI | Aider | OpenCode | Goose | Kiro CLI |
|------------|-----------|-----------|-------|----------|-------|----------|
| **Protocol** | JSONL stream-json | JSONL (`--json`) | Plain text | nd-JSON / HTTP | JSON / stream-json | JSON-RPC 2.0 |
| **Tool calling** | ✅ built-in + MCP | ✅ built-in + MCP | ✅ file ops only | ✅ built-in + MCP | ✅ built-in + MCP | ✅ built-in + MCP |
| **MCP support** | ✅ | ✅ | ✗ (community only) | ✅ | ✅ | ✅ |
| **System prompt** | GEMINI.md hierarchy | AGENTS.md hierarchy | `--read` flag | Undocumented | `--system TEXT` flag | Config file |
| **Session resume** | Partial (checkpoint) | ✅ by session ID | ✗ | ✅ --continue/--session | ✅ SQLite | ✅ session/load |
| **Streaming** | ✅ stream-json | ✅ JSONL to stdout | ✅ --stream | ✅ --format json | ✅ stream-json | ✅ metadata notifs |
| **Plan-mode equiv** | ✗ | ✅ inline approval | `--dry-run` | experimental | `/plan` command | ✗ |
| **Install** | npm / brew | npm / brew / binary | pip | curl / npm / bun | curl | curl |
| **Auth for CI** | `GEMINI_API_KEY` | `CODEX_API_KEY` | Provider env vars | Provider env vars | Provider env vars | `KIRO_API_KEY` (paid) |
| **MC tier** | **Tier 1** | **Tier 1** | **Tier 2** | **Tier 1** | **Tier 1** | **Tier 2** |

---

## Integration Priority Recommendations

### Tier 1 — Implement First

1. **Codex CLI** — Most Claude-like in architecture (AGENTS.md = CLAUDE.md, session resume, JSONL stream). Highest feature parity with current MC/Claude integration.
2. **Gemini CLI** — Google-backed, free tier (1k req/day), strong MCP, stream-json. Second most Claude-like.
3. **OpenCode** — Highest GitHub stars (153k+), 75+ providers, nd-JSON ACP + HTTP API. Best choice for users who want provider-agnostic flexibility.
4. **Goose** — Block/AAIF backed, `--system TEXT` flag makes system prompt injection trivial, clean json/stream-json output, `/plan` mode.

### Tier 2 — Implement Later / Degraded Mode

5. **Kiro CLI** — Best-structured protocol (JSON-RPC 2.0) but paid subscription required for headless auth. Implement when there's demand from paid users.
6. **Aider** — Plain text only; no MCP; no session state. Implement as a minimal wrapper: `--message` in, stdout out. All MC structured-output features degrade to no-op.

---

## Key Cross-Cutting Observations

### Protocol convergence around nd-JSON/JSONL
Four of six viable candidates support some form of newline-delimited JSON output (Gemini, Codex, OpenCode, Goose). MC's current Claude integration (stream-json JSONL) is a direct pattern match.

### MCP is nearly universal
All Tier 1 candidates support MCP. Aider is the only exception (no native MCP). MC's MCP surface should work as-is with Gemini CLI, Codex CLI, OpenCode, and Goose.

### Context files vs. CLI flags for system prompts
Three tools use **file-based** system prompt injection (GEMINI.md, AGENTS.md, CLAUDE.md all share the same hierarchical-file pattern). Two use **CLI flags** (Goose `--system`, Kiro config). Aider uses `--read` for read-only context. MC's dispatch layer needs to support both patterns.

### Session resume is a differentiator
Claude Code has strong session resume (CLAUDE session IDs). Codex and OpenCode match this. Gemini CLI is weaker here; Aider has none. MC should treat session resume as optional/graceful-degrade rather than required.

### ACP (Agent Communication Protocol) — emerging standard
Both OpenCode (`opencode acp`) and Kiro CLI (`kiro-cli acp`) expose an ACP interface. Kiro's is JSON-RPC 2.0 (well-specified). OpenCode's is nd-JSON (lighter-weight). These are NOT the same protocol despite sharing the name — OpenCode's ACP is a simpler nd-JSON wrapper, not the full JSON-RPC 2.0 spec. Watch this space; ACP may converge into an AAIF standard given the Linux Foundation donation of MCP.

---

*Research sources: google-gemini/gemini-cli docs, developers.openai.com/codex, aider.chat/docs, opencode.ai/docs, goose-docs.ai, kiro.dev/docs, GitHub issues for each project.*
