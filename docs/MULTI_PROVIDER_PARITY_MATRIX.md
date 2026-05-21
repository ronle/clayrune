# Multi-Provider Parity Matrix

> **Purpose:** For every Mission Control feature, score whether it *works*,
> *works-with-shim*, *doesn't-work*, or *unclear* on each candidate provider —
> and specify the shim design or fallback UX for every non-trivial cell.
>
> **Scope:** Feature parity only. Does NOT cover binary resolution, output-format
> parsing, dispatch-flag construction, or session-resume mechanics — those are
> the *substrate* every feature is built on, and are inventoried in
> `MULTI_PROVIDER_COUPLING_AUDIT.md` Categories 1–6. This doc assumes the
> substrate has a provider abstraction (`ProviderAdapter`); we score what
> happens *above* it.
>
> **Companions:**
> - `MULTI_PROVIDER_CAPABILITY_MATRIX.md` — what each provider's CLI can do
> - `MULTI_PROVIDER_COUPLING_AUDIT.md` — what MC currently couples to in `claude-code`
>
> **Workstream:** ws_parity · **Date:** 2026-05-21

---

## 1. Methodology

For each feature × provider cell we score one of four states:

| Score | Meaning |
|---|---|
| ✅ **Works** | Native equivalent exists; adapter passes through with no semantic loss. |
| 🟡 **Shim** | Works with a translation layer (schema rewrite, file-format conversion, prompt-prepend, etc.). Adapter design specified in §3. |
| ⛔ **Doesn't work** | No equivalent; feature must be disabled or replaced. UX specified in §4. |
| ❓ **Unclear** | Public docs insufficient; needs prototype probe before commitment. |

We score against the *behaviour Mission Control needs*, not against the
provider's full capability surface. A provider may have features we don't use,
and may *lack* features that are tangential (we don't penalise that here).

**Providers covered** (per `MULTI_PROVIDER_CAPABILITY_MATRIX.md`):

| Code | Provider | Tier |
|---|---|---|
| CC | Claude Code (Anthropic) | Baseline (reference) |
| CX | Codex CLI (OpenAI) | Tier 1 |
| GM | Gemini CLI (Google) | Tier 1 |
| OC | OpenCode (sst/anomalyco) | Tier 1 |
| GS | Goose (Block/AAIF) | Tier 1 |
| AD | Aider | Tier 2 |
| KR | Kiro CLI (AWS) | Tier 2 |

---

## 2. Master Matrix

| MC feature | CC | CX | GM | OC | GS | AD | KR |
|---|---|---|---|---|---|---|---|
| **Skills (Anthropic SKILL.md)** | ✅ | 🟡 prompt-graft | 🟡 GEMINI.md graft | 🟡 prompt-graft | 🟡 system-flag graft | ⛔ | ❓ |
| **MCP servers** | ✅ | ✅ (`config.toml`) | ✅ (`mcpServers`) | ✅ (`mcp` block) | ✅ (extensions) | ⛔ (or community `mcpm-aider`) | ✅ (`--require-mcp-startup`) |
| **Memory: MEMORY.md path + read-floor** | ✅ | 🟡 path remap → AGENTS.md or prompt-prepend | 🟡 path remap → GEMINI.md or prompt-prepend | 🟡 prompt-prepend (no auto-load file documented) | 🟡 `--system` flag carries content | 🟡 `--read MEMORY.md` flag | 🟡 prompt-prepend (config-file path TBD) |
| **Scribe (transcript → MEMORY.md)** | ✅ | 🟡 model-API summariser | 🟡 model-API summariser | 🟡 model-API summariser | 🟡 model-API summariser | 🟡 model-API summariser | 🟡 model-API summariser |
| **Condense (agent mode)** | ✅ | 🟡 provider-native agentic call | 🟡 same | 🟡 same | 🟡 same | ⛔ (no streaming JSONL) | ❓ |
| **Condense (structured mode)** | ✅ | 🟡 model-API + server-side apply | 🟡 same | 🟡 same | 🟡 same | 🟡 same (Aider can be the planner) | 🟡 same |
| **Hivemind orchestration** | ✅ | ✅ | ✅ | ✅ | ✅ | 🟡 degraded (no MCP, no resume) | ✅ |
| **Plan-mode approval** | ✅ (`ExitPlanMode` tool) | 🟡 inline-approval bridge | ⛔ | 🟡 `OPENCODE_EXPERIMENTAL_PLAN_MODE` (probe) | 🟡 `/plan` slash command | 🟡 `--dry-run` (diff preview only) | ❓ |
| **Transcript scanning** | ✅ (`~/.claude/projects/<enc>/*.jsonl`) | 🟡 wrap `codex exec resume` output | ⛔ no on-disk transcript dir | 🟡 sessions API export | 🟡 SQLite session DB | ⛔ git-log substitute only | 🟡 ACP session/load |
| **agent_log backfill** | ✅ (csid-keyed) | 🟡 thread_id-keyed | ⛔ (no resumable id) | 🟡 session_id-keyed | 🟡 session_id-keyed | ⛔ (stateless) | 🟡 ACP session id |
| **Image attachments** | ✅ (Read tool reads PNG/JPG) | 🟡 depends on provider Read tool vision support | 🟡 same | 🟡 same | 🟡 same | ⛔ (Aider can't read images) | ❓ |
| **File attachments** | ✅ (Read tool) | ✅ | ✅ | ✅ | ✅ | ✅ (`--file`) | ✅ |
| **AskUserQuestion** | ✅ (built-in tool) | ⛔ no tool emitted | ⛔ no tool emitted | ⛔ no tool emitted | ⛔ no tool emitted | ⛔ | ⛔ |

**Reading the matrix:** every row except *Skills* and *AskUserQuestion* has at
least one workable shim path on Tier 1 providers. The *only* features that
*must* be disabled outright on non-Claude providers are **Skills** (because
they require the provider to natively read `~/.claude/skills/`) and
**AskUserQuestion** (because it's a Claude-built-in tool with no equivalent).
Everything else degrades through some combination of prompt-prepend, path
remap, or model-API substitute.

---

## 3. Shim/Adapter Designs

For every 🟡 cell, this section specifies *what* the adapter does. The
abstraction surface is the `ProviderAdapter` interface ws_architect is
designing; this section names the methods we expect on it.

### 3.1 Skills (Anthropic-format)

**Claude baseline.** Skills are folders at `~/.claude/skills/<name>/SKILL.md`
with YAML frontmatter (`name`, `description`, `trigger`). Claude Code reads
them natively at session start, surfaces matching skills in the system prompt
based on the *description* field, and the user can invoke via the `Skill` tool.

**Why no provider has a native equivalent.** Skills are an Anthropic-format
convention; no other CLI parses SKILL.md frontmatter or surfaces skills as
named in-prompt capabilities.

#### 3.1.1 Shim — Codex CLI, OpenCode, Goose, Kiro (`prompt-graft`)

The adapter reads `~/.claude/skills/<name>/SKILL.md` files at dispatch time,
filters by Mission Control's existing keyword-search (the path already used by
`mc-skill-broker`), and grafts the top-K matching skills into the provider's
system-prompt channel:

- **Codex** → prepend to `AGENTS.md` content (or to the per-turn user prompt)
- **OpenCode** → inject via session config (probe needed for system-prompt path)
- **Goose** → pass via `goose run --system "<text>"`
- **Kiro** → write to the agent-config file before dispatch

```text
ProviderAdapter.inject_skills(skills: list[SkillDoc], context: PromptContext)
  → PromptContext
```

The SkillDoc's full body is included only if the description matched the task
keyword set (top-K=3, same as `/api/skills/search`). Skills below the
keyword-relevance threshold are listed *by name + description only* (one line)
so the model can request them via a tool turn — this is graceful degradation
of the Anthropic "skill triggered by description" mechanic.

**Limitations vs. Claude:** the provider doesn't *natively* match skill
descriptions to task intent — we approximate it with the existing
keyword-search index. Step 7 (bge-m3 semantic skill ranking, deferred) would
improve this for non-Claude providers as much as for Claude.

#### 3.1.2 Shim — Gemini CLI (`GEMINI.md graft`)

Gemini CLI auto-loads `GEMINI.md` from CWD + ancestors. The adapter writes a
materialised `GEMINI.md` into the project workspace at dispatch time
containing: (a) skill manifest (name + description, one line each), (b) full
body of top-K matched skills. Same writer that Claydo already uses to write
materialised CLAUDE.md (server.py:1522–1526) — just rename target.

```text
ProviderAdapter.write_context_file(workspace: Path, context: PromptContext)
```

#### 3.1.3 Doesn't work — Aider

Aider has no system-prompt graft beyond `--read`. The `--read` flag is for
read-only context files, not skill discovery. We could pass the manifest as a
read file, but Aider has no concept of "invoke skill X" — and Aider tasks are
single-shot edits, not the multi-turn flow skills are designed for. **UX:** hide
the Skills sidebar entry when the project's selected provider is Aider; show
a "Skills are not supported on Aider" notice on the Skills modal.

#### 3.1.4 Unclear — Kiro

Kiro's agent-configuration file format isn't documented publicly. Probe
before committing to a shim design. If it accepts arbitrary text, the
prompt-graft pattern works.

---

### 3.2 MCP Servers

**Claude baseline.** Two config locations: `~/.claude.json` (top-level
`mcpServers` key, global) and `<project>/.mcp.json` (project shadow). MC owns
these via `mcp.py`. Claude Code reads them natively at session start.

#### 3.2.1 Works — Codex CLI

Codex reads `~/.codex/config.toml` with an `mcpServers` section. Format is
TOML, not JSON, but field semantics are identical (`command`, `args`, `env`,
optional `url` for HTTP/SSE).

```text
ProviderAdapter.write_mcp_config(global_path, project_path, servers: list[McpServer])
```

The Codex adapter implements this by serialising the same `McpServer` dataclass
into TOML (`tomli_w` or manual templating). Project-shadow logic stays
identical — Codex supports project-level `config.toml` overrides.

#### 3.2.2 Works — Gemini CLI

Gemini reads `~/.gemini/settings.json` with a `mcpServers` JSON object.
Identical shape to `~/.claude.json` — the adapter can write the same JSON dict
to a different path. Per-server `includeTools` / `excludeTools` allowlists are
honoured.

#### 3.2.3 Works — OpenCode

OpenCode reads its config file with an `mcp` block. The shape is slightly
different (`type: 'local' | 'remote'`, `command` is an array). Adapter
translates server dict → OpenCode shape, but the data model is 1:1.

#### 3.2.4 Works — Goose

Goose calls them "extensions" but uses MCP under the hood. Config via
`goose configure` interactive flow OR by writing the YAML config directly.
Adapter writes YAML.

#### 3.2.5 Works — Kiro

Kiro accepts MCP server configs at install time and surfaces
`--require-mcp-startup` for fail-fast behavior. The config file format mirrors
Claude's JSON shape. Probe path before committing.

#### 3.2.6 Doesn't work (native) — Aider

Aider has no native MCP. Community wrapper `mcpm-aider` exists but is brittle
and not part of upstream Aider. **UX:** When the active provider is Aider, hide
the MCP sidebar entry and show a one-line notice: *"MCP servers are not
supported on Aider — switch provider to use MCP tools."* Built-in MCP install
list still installs to `~/.claude.json` (a no-op for Aider users, but harmless
since the file is untouched by Aider).

---

### 3.3 Memory / MEMORY.md read-floor

**Claude baseline.** MC reads `~/.claude/projects/<encoded>/memory/MEMORY.md`
and injects it via `--append-system-prompt`. The path encoding is Claude's
project-dir scheme.

#### 3.3.1 Shim — All non-Claude providers (`path-remap + prompt-prepend`)

The MEMORY.md *location* stays at `~/.claude/projects/<encoded>/memory/MEMORY.md`
(MC writes it; we don't move it). What changes is the **injection path**:

| Provider | Injection mechanism |
|---|---|
| Codex | Concatenate MEMORY.md content into the materialised `AGENTS.md` MC writes into the project workspace (same pattern as the existing `CLAUDE.md` Claydo write at server.py:1522). |
| Gemini | Concatenate into materialised `GEMINI.md`. |
| OpenCode | Prepend to the per-turn user message (no documented system-prompt-injection flag — probe before assuming a config-file path exists). |
| Goose | Pass via `goose run --system "<MEMORY.md content>"`. ⚠️ The 32 KB CreateProcess limit on Windows applies — fall back to file-graft if MEMORY.md > 28 KB. |
| Aider | `aider --read MEMORY.md` (the actual file at the Claude location, passed by absolute path). |
| Kiro | Inject via agent-config file (mechanism undocumented; probe needed). |

```text
ProviderAdapter.inject_memory(memory_text: str, context: PromptContext)
  → PromptContext  # may mutate context.system_prompt OR materialised files
```

**Architectural note:** the memory *store* (where Scribe writes) stays at the
Claude path. Only the *load mechanism* is provider-specific. This means a
single MEMORY.md is shared across providers, which is what we want — the user
can switch providers mid-project without losing memory.

---

### 3.4 Scribe (session → MEMORY.md summarisation)

**Claude baseline.** `_scribe_call` spawns `claude -p` with the transcript
piped via stdin and writes the model's one-line summary to MEMORY.md.

#### 3.4.1 Shim — All non-Claude providers (`model-API summariser`)

Scribe is a *cheap model call*, not an agentic session. The shim swaps
`claude -p` for the provider's equivalent thin-CLI invocation:

| Provider | Cheap-model invocation |
|---|---|
| Codex | `codex exec --json --model gpt-4o-mini "<scribe prompt>" < transcript` (with `--output-schema` to constrain to `{summary: str}`). |
| Gemini | `gemini --output-format json --prompt "<scribe prompt>" < transcript`. |
| OpenCode | `opencode run --format json --model <cheap-model> "<scribe prompt>" < transcript`. |
| Goose | `goose run --quiet --output-format json --system "<scribe prompt>"` with transcript piped. |
| Aider | Not viable as scribe — Aider modifies files rather than answering. Use Anthropic/OpenAI/Gemini API directly (the user's `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` env var is already in scope). |
| Kiro | `kiro-cli --no-interactive --trust-tools=read "<scribe prompt>"` with transcript via stdin. |

```text
ProviderAdapter.model_call(prompt: str, stdin_text: str, model_hint: str = 'cheap')
  → str  # model output
```

**Source-of-truth question.** Claude's Scribe reads the on-disk JSONL transcript
because MC's in-memory `log_lines` drops tool results and thinking. Non-Claude
providers may not write a JSONL transcript at all (see §3.7). For those
providers, Scribe input becomes whatever the provider's structured output
stream emitted during the session — captured by the same stream-reader
abstraction the substrate provides. This is a fidelity downgrade (no
tool-result content for thin streams) but is the only available signal.

#### 3.4.2 Unclear — Aider as scribe input

Aider produces plain text and a diff; no structured transcript. We could
ingest stdout + diff as the Scribe input, but quality will be lower. **Open
question for ws_architect:** is it acceptable to make Scribe a *no-op* on
Aider sessions, or do we degrade by piping the (low-fidelity) stdout?

---

### 3.5 Condense

**Claude baseline.** Two modes:
- *Agent mode* (`condense_mode='agent'`) — spawns a full agentic Claude
  session with Write/Edit tool access to MEMORY.md + CLAUDE.md.
- *Structured mode* (`condense_mode='structured'`) — plan via cheap-model
  call, apply server-side with locks (`_condense_apply`).

#### 3.5.1 Agent mode shim — Codex / Gemini / OpenCode / Goose / Kiro

Same dispatch path as a normal agentic session — just with a constrained
prompt and `--max-turns 14`. The adapter is the same `ProviderAdapter.dispatch`
the substrate already exposes; condense just calls into it with a fixed system
prompt and a 14-turn cap.

```text
condense_agent_mode(provider: ProviderAdapter, project, model='cheap'):
    provider.dispatch(
        task=CONDENSE_PROMPT,
        max_turns=14,
        model=model,
        tools=['Read', 'Write', 'Edit'],   # provider maps to its own tool names
        cwd=project.path,
    )
```

The condense prompt currently references CLAUDE.md by name. **Adapter
responsibility:** rewrite the prompt to reference the provider's auto-loaded
context file name (`AGENTS.md` for Codex, `GEMINI.md` for Gemini, etc.). MC
stores the canonical content in one place and renders the right filename per
provider.

#### 3.5.2 Agent mode doesn't work — Aider

Aider can edit files but produces no structured tool-call stream. The 14-turn
mechanic doesn't apply (Aider is single-shot per `--message`). **UX:** disable
agent-mode condense for Aider projects; default to structured mode.

#### 3.5.3 Structured mode — all providers

Structured mode is mostly server-side: plan via `model_call` (covered in §3.4),
apply via `_condense_apply` which uses Python `_atomic_write_text` + the
shared `_get_mem_write_lock`. No provider-side dispatch needed. The only
shim is the `model_call` substitute used by `_condense_plan` — same as
Scribe's cheap-model swap.

#### 3.5.4 Unclear — Kiro condense

Need to confirm `kiro-cli acp` supports a 14-turn budget without
session-abort. Probe.

---

### 3.6 Hivemind Orchestration

**Claude baseline.** Three spawn paths (orchestrator, workers via API, workers
via auto-spawn) all use `_resolve_claude` + claude flags + stream-json.

#### 3.6.1 Works — Codex / Gemini / OpenCode / Goose / Kiro

Hivemind is structurally a *wrapper around N agent dispatches with a shared
message bus*. The bus is server-side and provider-agnostic. The wrapper just
needs:

1. `provider.dispatch(task, system_prompt, cwd)` — already needed for solo
   agent runs. Same call, different role-injected system prompt.
2. `provider.parse_output_line(line) → OutputEvent` — already needed.
3. `provider.session_id_from_output(stream) → str` — provider's notion of a
   session UUID, for the bus to address workers.

All five providers have these. The Hivemind code at server.py:8100–8526
swaps `_resolve_claude()` → `provider.binary()` and `_build_claude_flags(...)`
→ `provider.build_dispatch_cmd(...)`. The bus (server.py POST/GET
`/api/hivemind/<hm>/bus/...`) is unchanged.

**Worker brief note:** the worker-context system prompt currently warns
against `EnterPlanMode`/`ExitPlanMode` (server.py:2848). For non-Claude
providers that string is a no-op (the model won't recognise the tool names)
but should be replaced with the provider's plan-mode warning for cleanliness
(e.g. `/plan` for Goose).

#### 3.6.2 Degraded — Aider

Aider workers can run, but:
- No session resume → worker can't be revived if MC restarts mid-hivemind.
- No MCP → bus access via subprocess curl in the worker prompt is the only
  channel (already the pattern). This works.
- No structured tool-call events → `parse_output_line` returns at-best
  plain-text chunks. The bus *post* commands the worker needs to call
  (curl POST to `/api/hivemind/.../bus/post`) still work because the worker
  has Bash. So Aider Hivemind *runs* but you lose plan-approval and richer
  worker UI.

**UX:** Aider workers in hivemind are flagged with a 🟡 badge in the
workstreams panel: "Aider: degraded (no resume, no plan mode)."

---

### 3.7 Plan-Mode Approval

**Claude baseline.** Both stream readers watch for `tool_name == 'ExitPlanMode'`
and set `session['waiting_for_plan_approval'] = True`. The dashboard surfaces
the plan markdown file and a one-click approve button.

#### 3.7.1 Shim — Codex CLI (`inline-approval bridge`)

Codex emits an *inline approval* prompt before executing changes. The output
event surfaces a `turn.completed` with a `pending_approval` field. The
adapter maps this to the same `session['waiting_for_plan_approval'] = True`
state. Approval round-trip is the existing `/api/project/<id>/agent/followup`
endpoint with `approve_plan: true` — provider adapter rewrites that to Codex's
`/approve` slash-command.

```text
ProviderAdapter.handle_approval(session, approve: bool) → stdin_chunk
```

#### 3.7.2 Shim — Goose (`/plan` slash command)

Goose's `/plan` is interactive (not a structured event). Two options:
- *Polling shim:* the adapter parses stdout for the literal "Approve plan?"
  string Goose prints. Brittle.
- *Native shim:* drive Goose via its session API rather than CLI. Out of scope
  for the "subprocess + stdin/stdout JSON" constraint.

**Recommendation:** ship the polling shim with a known-fragility caveat; track
upstream issue for structured plan-mode events.

#### 3.7.3 Shim — OpenCode (`probe required`)

`OPENCODE_EXPERIMENTAL_PLAN_MODE=1` exists but isn't documented well. Probe
the actual event shape before committing.

#### 3.7.4 Shim — Aider (`--dry-run` substitute)

Aider's `--dry-run` shows the diff without applying. The adapter could
*always* run Aider in dry-run first, surface the diff as a "plan", and only
re-run without `--dry-run` on approval. This doubles the model call cost
and roughly doubles latency, but it's the only way to give Aider users a
preview gate.

**UX:** mark this as a *per-project* opt-in toggle (`aider_dry_run_first:
true`), default off. Users who want speed leave it off; users who want a
review gate turn it on.

#### 3.7.5 Doesn't work — Gemini

Gemini has no dry-run, no plan-mode, no inline approval. **UX:** when Gemini
is the active provider, hide the plan-approval banner permanently; the
"Approve plan" button is unreachable because the trigger never fires. Mark
this in the provider-select UI so users know.

#### 3.7.6 Unclear — Kiro

Plan-mode not documented for Kiro. Probe.

---

### 3.8 Transcript Scanning

**Claude baseline.** `_recent_claude_transcripts`, `_find_transcript_file`,
`_parse_transcript_messages` read `~/.claude/projects/<encoded>/*.jsonl`.
Powers: history view, Scribe input, backfill, revive, transcript download.

#### 3.8.1 Shim — Codex CLI (`codex exec resume` wrap)

Codex stores session state but doesn't expose a JSONL transcript dir we can
scan. The shim: `codex exec resume <SESSION_ID> --json --replay-only` — needs
to be confirmed as a Codex feature. If not present, the adapter caches the
JSONL stream MC already captures during the session into a parallel directory
(`~/.mc-transcripts/codex/<project_enc>/<session_id>.jsonl`). All MC transcript
consumers then read from this MC-owned cache instead of the (non-existent)
provider-owned one.

```text
ProviderAdapter.transcript_path(project_path: Path, session_id: str) → Path
ProviderAdapter.list_recent_transcripts(project_path: Path) → list[TranscriptRef]
ProviderAdapter.parse_transcript(path: Path) → list[TranscriptMessage]
```

The Claude adapter delegates to the existing `_find_transcript_file` /
`_parse_transcript_messages`. The Codex adapter parses its own JSONL event
shape. Other providers similar.

#### 3.8.2 Shim — OpenCode (`session export`)

OpenCode supports session data export/import as JSON. The adapter shells out
to `opencode session export <id>` (or equivalent — confirm command) on demand
when MC needs to read a past session.

#### 3.8.3 Shim — Goose (`SQLite query`)

Goose v1.10.0+ stores sessions in SQLite. The adapter opens the DB read-only
and queries by session_id. This is the cleanest non-Claude transcript story
because the source-of-truth is queryable.

#### 3.8.4 Doesn't work — Gemini / Aider

Gemini CLI has no on-disk transcript directory we can scan; checkpointing
exists but the on-disk format is undocumented. Aider has no session state at
all — every invocation is stateless.

**Fallback for both:** *MC owns the transcript.* During a live session MC
captures every stream-json line into `~/.mc-transcripts/<provider>/<enc>/<msid>.jsonl`
(where `msid` is an MC-issued session UUID, since provider session IDs may not
exist). After session end this file is the only transcript record. All MC
transcript consumers read from here regardless of provider. The Claude adapter
falls through to `~/.claude/projects/` as a *secondary* source so existing
Claude history pre-dating the multi-provider refactor stays visible.

#### 3.8.5 Unclear — Kiro

`session/load` ACP message can return prior session state, but the wire
format isn't documented. Probe.

---

### 3.9 agent_log Backfill

**Claude baseline.** `_log_agent_completion` writes an `agent_log` row with
`claude_session_id` (the UUID from claude's stream-json `result` message).
Backfill at server.py:3790 cross-references unhandled transcripts with the
agent_log on startup and stamps missing csid values.

#### 3.9.1 Shim — Codex / OpenCode / Goose / Kiro (`session_id field rename`)

The `claude_session_id` field gets a sibling `provider_session_id`. The
backfill logic at server.py:3820–3841 (which currently matches by `<csid>.jsonl`
filename) gets a provider-aware variant that knows where the provider stores
its session IDs (or, for the MC-owned transcript cache from §3.8, the
`<msid>.jsonl` filename pattern).

```python
# agent_log row, post-abstraction:
{
    'provider': 'codex' | 'gemini' | 'opencode' | 'goose' | 'aider' | 'kiro' | 'claude',
    'provider_session_id': '<provider-issued or mc-issued uuid>',
    'claude_session_id': '<legacy field, populated only for provider=claude>',
    ...
}
```

The `/api/project/<id>/transcript/<id>` endpoint accepts either field as the
URL parameter (it's already a UUID-shaped string; the route handler dispatches
to the right provider adapter based on the agent_log row).

#### 3.9.2 Doesn't work — Gemini / Aider (`no resumable id`)

Gemini and Aider lack session-resume mechanics, so backfill that's
*specifically* about reviving stuck sessions has nothing to back-fill *into*.
**UX:** these rows still get logged (so history view works) but with
`provider_session_id: null` and `resumable: false`. The "Revive" button is
hidden for those rows. Mark them in the history list with a small badge:
"📋 read-only (no resume)".

---

### 3.10 Image Attachments

**Claude baseline.** MC saves an uploaded image to
`data/uploads/agent_<hash>.png`, appends `[Screenshot: <abs-path>]` to the
prompt, and the agent uses its built-in `Read` tool which has native vision
support for PNG/JPG/GIF/WebP.

#### 3.10.1 Pass-through — Codex / Gemini / OpenCode / Goose / Kiro

All five providers support image-capable Read tools when the underlying model
supports vision (GPT-4o, Gemini 2.5+, Claude 3.5+, etc.). The MC code injecting
`[Screenshot: <abs-path>]` is provider-agnostic — the agent's own `Read` tool
loads the bytes.

**Adapter responsibility:** none — provided the provider's Read tool /
file-attachment mechanism handles image MIME types. The adapter *can*
optionally rewrite the marker syntax if the provider has a preferred
attachment encoding (some providers accept `@path/to/img.png`-style citations
in prompts).

```text
ProviderAdapter.attachment_marker(path: Path, kind: 'image' | 'file') → str
# default: f'[Screenshot: {path}]' for image, f'[Attachment: {path}]' for file
```

#### 3.10.2 Doesn't work — Aider

Aider's file-read mechanism is text-only — it can't extract semantic content
from PNG/JPG. We can still *pass* the path, but the model won't see the image
content. **UX:** the mobile/desktop attach UI hides the image button when the
active provider is Aider; show a "Image attachments not supported on Aider"
tooltip on the paperclip menu.

#### 3.10.3 Unclear — Kiro

Read tool category includes `read` but image MIME support isn't documented.
Probe.

---

### 3.11 File Attachments

**Claude baseline.** Same path/prompt injection as images, but with text files.

#### 3.11.1 Works — All providers (including Aider)

Every provider's built-in Read/file tool handles text files. Aider in fact has
a *better* file-attachment model (`--file` flag mounts the file as editable
context). The adapter chooses the best surface per provider:

| Provider | File attachment surface |
|---|---|
| Claude | `[Attachment: <path>]` marker + Read tool |
| Codex | `[Attachment: <path>]` marker + Read tool |
| Gemini | `@<path>` citation syntax in GEMINI.md (auto-imported) |
| OpenCode | `[Attachment: <path>]` marker + Read tool |
| Goose | `[Attachment: <path>]` marker + Read tool |
| Aider | `--file <path>` CLI flag (mounts as editable) OR `--read <path>` (read-only) |
| Kiro | `[Attachment: <path>]` marker + Read tool |

```text
ProviderAdapter.attach_files(files: list[Path], context: PromptContext)
  → PromptContext  # may mutate context.cli_flags AND/OR context.prompt
```

The Aider adapter is the interesting one: it mutates `context.cli_flags` to
add `--file`/`--read` rather than just prepending the marker to the prompt.

---

### 3.12 AskUserQuestion

**Claude baseline.** Claude's built-in tool `AskUserQuestion` emits a
`tool_use` event with structured questions/options. MC intercepts the tool name
in both stream readers (server.py:3403, 3558), pauses the agent, and surfaces
the question in the dashboard.

#### 3.12.1 Doesn't work — every non-Claude provider

No other CLI has an `AskUserQuestion` tool. The model could be *asked* to
emit a question by prompting it to wrap its question in a sentinel string,
but:

1. The model has no instruction-fine-tuning to do this reliably.
2. The dashboard UI would have to parse free-text questions, which is much
   lower fidelity than the structured tool format.
3. The provider's tool-call channel can't be hijacked safely without an
   MC-side custom MCP tool.

**Shim (optional, low priority):** ship an `mc-ask` MCP tool MC hosts itself
(via mcp.py) that any provider with MCP support can call. The MC-hosted tool
returns a "pending — see dashboard" signal, MC surfaces the question, the
follow-up answer is fed back as the tool result on the next turn.

```text
# MC-hosted MCP tool spec:
{
  "name": "mc_ask",
  "description": "Ask the user a question. Pauses for response.",
  "input_schema": {
    "type": "object",
    "properties": {
      "question": {"type": "string"},
      "options": {"type": "array", "items": {"type": "object",
        "properties": {"label": {"type": "string"}, "header": {"type": "string"}}}}
    }
  }
}
```

The tool call blocks until the dashboard receives the user's answer. This
*does* work but is opt-in per provider, and requires the provider to support
MCP. For Aider (no MCP), AskUserQuestion has no replacement at all.

**UX recommendation:** ship the `mc-ask` MCP shim as a Tier-2 follow-up.
Initial multi-provider release: AskUserQuestion is a Claude-only feature, with
a notice on the dashboard ("Interactive questions only available on Claude")
when the active provider isn't Claude.

---

## 4. UX Recommendations for "Doesn't Work" Cells

| Feature × Provider | Recommended UX |
|---|---|
| Skills × Aider | Hide Skills sidebar entry; show "Skills require Claude/Codex/Gemini/OpenCode/Goose" on Skills modal if reached via deep link. |
| MCP × Aider | Hide MCP sidebar entry; show one-line notice on settings page. |
| Plan-mode × Gemini | Hide plan-approval banner UI when Gemini is active; show "Plan mode not supported on Gemini" in provider-select tooltip. |
| Transcript × Gemini | History list shows last-N completed runs (from `agent_log`) but transcript-open is disabled; greyed out with "Transcript not retained" tooltip. |
| Transcript × Aider | Same as Gemini; rely on git log for change history (link the git log in the project page footer when provider=aider). |
| Backfill × Gemini, Aider | Read-only badge on history rows; Revive button hidden. |
| Image attachments × Aider | Paperclip menu hides the image option; show tooltip on hover. |
| AskUserQuestion × all-non-Claude | Provider-select badge "no interactive Q" on non-Claude options; runtime: agent that asks questions in plain text falls through as normal output (no special handling). |

All "Doesn't work" UXes follow one principle: **the feature is silently
unavailable, not visibly broken.** A user who selects a provider that lacks a
feature sees the affected UI element disappear or grey out — never sees a
button that hangs or an error mid-flow.

---

## 5. Build-Order Priority

Suggested implementation order matched to ws_research's Tier-1 list:

1. **Codex CLI** — highest Claude-feature parity (AGENTS.md ≈ CLAUDE.md, session
   resume, MCP). Validates the abstraction by being a close-but-not-identical
   provider.
2. **Gemini CLI** — second priority; free tier helps adoption. Plan-mode and
   transcript gaps are the cleanest "graceful degrade" cases.
3. **OpenCode** — Tier 1; 75+ provider support is a force multiplier.
4. **Goose** — Tier 1; `--system` flag makes shimming MEMORY.md trivial.
5. **Aider** — Tier 2; ship after all Tier 1s validate the degraded-mode UX
   pattern.
6. **Kiro** — Tier 2; gated on paid-subscription users.

For each provider, the implementation step that *most clears the path* for the
next one is the **ProviderAdapter abstraction surface**. The shim designs
above all assume that surface exists; without it every shim becomes an
inline if-claude-then-this branch. ws_architect is owning that abstraction.

---

## 6. Open Questions for ws_architect

Coordination points where this matrix has dependencies on the architect's
design:

1. **MEMORY.md location**: confirm whether MEMORY.md stays at the Claude path
   for all providers (recommended: yes — single store, multi-provider load)
   or moves to a provider-neutral location like `<project>/.mc/MEMORY.md`.
   If it moves, the Claude adapter writes a symlink or path-translates.
2. **agent_log schema additions**: confirm the `provider` + `provider_session_id`
   sibling-field approach in §3.9.1 vs. a fully-typed schema migration.
3. **Transcript cache directory**: confirm `~/.mc-transcripts/<provider>/<enc>/`
   is the right convention or whether transcripts go into a project-local
   path. Affects backfill (§3.9), Scribe input (§3.4), history (§3.8).
4. **`mc-ask` MCP tool**: green-light or defer? Decision affects whether
   AskUserQuestion is Claude-only or all-MCP-providers (§3.12).
5. **Plan-mode polling for Goose** (§3.7.2): accept brittle stdout parsing
   for v1 or defer Goose plan-mode entirely?
6. **CLAUDE.md content materialisation**: the Claydo write at server.py:1522
   already materialises USER_GUIDE + CHANGELOG as CLAUDE.md. For non-Claude
   providers, this becomes AGENTS.md / GEMINI.md / etc. Confirm the write
   helper gets renamed (e.g. `_materialise_context_file(provider)`) and the
   filename is provider-derived.

---

## 7. Summary

**Feature parity is achievable for every Tier-1 provider** on 9 of 11 features
(Skills and AskUserQuestion are the exceptions and even those have viable
shims for some providers via prompt-graft and `mc-ask`-MCP-tool respectively).

**Aider is the realistic floor** of the parity story: it lacks MCP, session
resume, structured output, vision, and tool calls beyond file-edit. Five of
the eleven features are no-ops on Aider. The UX for Aider projects is
"degraded but functional" — file edits + Scribe via API + structured condense
+ Hivemind workers run, but plan mode is `--dry-run`-only, history is
git-only, and there's no Skills/MCP/AskUserQuestion/image-attachment.

**Kiro has the cleanest *protocol*** (JSON-RPC 2.0 ACP) of any non-Claude
provider, but several feature cells are ❓ because Kiro's public docs are
thin. A 1-day probe sprint resolves these.

**The biggest open architectural question** is whether MC owns the
transcript store (`~/.mc-transcripts/`) or relies on each provider's native
store. The matrix above assumes MC owns it as a *fallback* — i.e. always
write our own JSONL during live sessions, and prefer it over provider stores
that may or may not exist. This decouples Scribe / backfill / history from
provider-specific filesystem layouts and is the recommended path for
ws_architect.
