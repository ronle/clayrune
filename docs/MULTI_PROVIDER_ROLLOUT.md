# Multi-Provider Rollout Guide

**Branch:** `feat/multi-provider-agents`  
**Date:** 2026-05-21  
**Workstream:** ws_rollout  
**Companion docs:**
- `docs/MULTI_PROVIDER_DESIGN.md` — full architecture
- `docs/MULTI_PROVIDER_PARITY_MATRIX.md` — per-feature × per-provider capability table
- `docs/MULTI_PROVIDER_CAPABILITY_MATRIX.md` — raw provider capability data

---

## 1. Backwards Compatibility

**Existing claude users are fully safe — no config change needed.**

The dispatch path in `server.py` (line ~5881) reads:

```python
provider_name = (p.get('provider') or CONFIG.get('default_provider') or 'claude').lower()
if provider_name != 'claude':
    return _dispatch_via_runtime(...)
# else: falls through to the existing 100% unchanged claude code path
```

A project with no `provider` field set — every project created before this branch — takes the `else` branch and runs exactly the same code as today. `ClaudeRuntime` is a thin delegator that only fires when the project's `provider` is explicitly set to `'claude'`; the legacy path is the default.

**What does NOT change for claude users:**
- All CLI flags, session lifecycle, Scribe, Condense, Hivemind, MCP, Skills, Plan-mode — unchanged
- `claude_session_id`, `agent_log` structure, transcript scanning — unchanged  
- Auth flow (`/api/claude/auth-*`, Settings sign-in button) — unchanged

---

## 2. Installing Providers

### 2.1 Claude Code (default — already installed)

Claude Code ships with Clayrune's installer. If you need to reinstall:

```
npm install -g @anthropic-ai/claude-code
```

**Auth:** Run `claude login` in a terminal or use Settings → Agent Provider → Claude Code → "Sign in". A browser window opens for Anthropic OAuth.

**API key (alternative):** Set `ANTHROPIC_API_KEY` in your environment before launching Clayrune.

### 2.2 Gemini CLI

```
npm install -g @google/gemini-cli
```

**Auth — option A (recommended for unattended use):**  
Set `GEMINI_API_KEY` in your environment before launching Clayrune:

```bash
# Linux / macOS — add to ~/.bashrc or ~/.zshrc
export GEMINI_API_KEY="your-key-here"

# Windows — PowerShell (persist to user environment)
[Environment]::SetEnvironmentVariable("GEMINI_API_KEY", "your-key-here", "User")
```

Get a key at [aistudio.google.com](https://aistudio.google.com/apikey) → Create API key.

**Auth — option B (interactive OAuth):**

```
gemini auth login
```

This opens a browser for Google sign-in. The CLI writes a token to `~/.gemini/`. Clayrune picks it up automatically on the next dispatch.

**Verify install:**

```
gemini --version
```

### 2.3 Codex CLI

> Prototype status — adapter not yet implemented. Documented for planning purposes.

```
npm install -g @openai/codex
```

**Auth:** Set `OPENAI_API_KEY` in your environment.

```bash
export OPENAI_API_KEY="sk-..."
```

### 2.4 Aider

> Prototype status — adapter not yet implemented.

```
pip install aider-chat
```

**Auth:** Set `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` depending on which model you want Aider to use.

### 2.5 Where to put API keys

Clayrune inherits environment variables from the shell that launched it. The safest approach:

1. Add keys to your shell profile (`~/.bashrc`, `~/.zshrc`, or Windows user environment variables)
2. Restart Clayrune so it picks up the updated environment
3. Verify in Settings → Agent Provider — the auth status badge updates on the next health check

Do **not** put API keys inside Clayrune's `config.json` — that file is human-editable but not encrypted.

---

## 3. Feature Parity per Provider

Summary table (full detail in `docs/MULTI_PROVIDER_PARITY_MATRIX.md`):

| Feature | Claude Code | Gemini CLI | Codex CLI | Aider |
|---|---|---|---|---|
| **Mode B (persistent stream)** | ✅ native | ⛔ | ⛔ | ⛔ |
| **Session resume** | ✅ | ⛔ re-spawns | ⛔ | ⛔ |
| **MCP servers** | ✅ | 🟡 config translation | ✅ config.toml | ⛔ |
| **Skills (SKILL.md)** | ✅ native | 🟡 prompt-graft to GEMINI.md | 🟡 prompt-graft | ⛔ |
| **Memory / MEMORY.md** | ✅ | 🟡 prepend to task | 🟡 AGENTS.md remap | 🟡 `--read` flag |
| **Scribe (transcript → memory)** | ✅ | 🟡 model-API fallback | 🟡 model-API fallback | 🟡 model-API fallback |
| **Hivemind orchestration** | ✅ | ✅ | ✅ | 🟡 degraded |
| **Plan-mode approval** | ✅ | ⛔ | 🟡 inline bridge | 🟡 diff-only |
| **AskUserQuestion** | ✅ | ⛔ | ⛔ | ⛔ |
| **Image attachments** | ✅ | 🟡 vision-dependent | 🟡 | ⛔ |
| **agent_log + history** | ✅ | ✅ (prototype) | 🟡 | ⛔ |
| **Cost / usage telemetry** | ✅ | ⛔ | ⛔ | ⛔ |

Legend: ✅ works · 🟡 works with shim/degraded · ⛔ not supported

**Key limitations for Gemini CLI (current prototype):**

- Mode A only (one subprocess per turn; followup re-spawns with transcript prefix for continuity)
- No native session resume — switching away and back loses Gemini's internal context
- Plan-mode approval UI is hidden (feature requires `ExitPlanMode` tool which Gemini doesn't emit)
- AskUserQuestion is hidden — Gemini doesn't emit the structured tool call MC listens for
- Usage / cost data not captured (Gemini CLI doesn't emit token counts in JSON)
- MCP: Gemini CLI has its own MCP config format (`~/.gemini/settings.json` `mcpServers`); MC does not yet auto-translate its project MCP config into that format — a manual step for now

---

## 4. Settings UI

### 4.1 Global Default Provider

Settings → Agent Provider → **Global Default** — select the provider that all new projects will use unless overridden.

- Stored in `config.json` as `default_provider`
- Defaults to `claude` if not set
- Fallback chain at dispatch: `project.provider → config.default_provider → 'claude'`

### 4.2 Per-Project Override

Three-dot menu (⋮) on any project card → **Agent Provider** → pick from the submenu. This overrides the global default for that project only. The submenu shows an install-status dot for each option; clicking a provider that isn't installed shows a toast with the install command.

### 4.3 Per-Provider Health Cards (Settings)

Settings → Agent Provider shows a health card for every registered provider:

- **Green pill** — installed and authenticated
- **Amber pill** — installed but auth status unknown or not signed in
- **Grey pill** — not installed; shows install command

Clicking a card expands install and auth instructions specific to that provider.

---

## 5. Mid-Session Provider Switching

**You cannot change the provider of a running session.** Provider is bound at dispatch time and stored in the session object. Followup messages and interrupts are always routed through the same provider that started the session.

**What happens when you switch a project's provider while a session is running:**

1. The running session continues with its original provider until it completes normally.
2. The next dispatch (new task or followup after completion) uses the new provider.
3. The project card shows a small indicator of the active session's provider vs the configured one while both differ.

**If you want to switch immediately:** interrupt the running session (⏹ Stop), change the provider, then re-dispatch.

**No cross-provider session migration.** Starting on Claude and continuing on Gemini is not supported — the `claude_session_id` that enables Claude resume has no equivalent in other providers. If you switch provider on a project mid-history, future sessions start fresh; history entries from the old provider remain in agent_log and are readable, but will show a different `provider` field.

---

## 6. Telemetry — Identifying Provider in agent_log

Every `agent_log` entry now includes a `provider` field:

```json
{
  "ts": "2026-05-21T09:00:00Z",
  "task": "Refactor the auth module",
  "status": "completed",
  "provider": "gemini",
  "session_id": "abc123def456",
  ...
}
```

**How to use it for debugging:**

```python
# Which provider ran a given session?
log = load_agent_log(project_id)
entry = next(e for e in log if e['session_id'] == session_id)
print(entry['provider'])   # 'claude' | 'gemini' | ...

# Filter to Gemini runs only
gemini_runs = [e for e in log if e.get('provider') == 'gemini']
```

**Existing entries** (written before this branch) have no `provider` key. Code that reads agent_log should treat a missing `provider` field as `'claude'` — consistent with the historical reality that all prior runs were claude.

**The `/api/project/<id>/agent/log` endpoint** already returns the full entry dict, so `provider` is immediately visible in the UI's run history without any frontend changes.

**For Hivemind:** the `trigger_type`/`trigger_id` correlation fields coexist with `provider`. A Hivemind worker run on Gemini will have both `trigger_type: 'hivemind_worker'` and `provider: 'gemini'` — making cross-provider Hivemind debugging tractable.

---

## 7. Migration Runbook

### For users upgrading from a pre-multi-provider build

No action required. All existing projects default to `provider='claude'` and run the legacy claude code path unchanged.

### For users who want to try Gemini CLI

1. `npm install -g @google/gemini-cli`
2. Set `GEMINI_API_KEY` in your environment
3. Restart Clayrune (to pick up the env var)
4. Open Settings → Agent Provider — verify Gemini shows a green "installed" badge
5. On any project's three-dot menu → Agent Provider → Gemini CLI
6. Dispatch a task — output streams in the same UI; history is logged

### For admins deploying a multi-user instance

- `default_provider` in `config.json` controls the global default for all projects on that instance
- Per-project overrides always win
- Providers that aren't installed are shown greyed out but remain selectable (the next dispatch will fail fast with an install-hint error rather than silently falling back)

### Rolling back

To revert a project to Claude Code: three-dot menu → Agent Provider → Claude Code. There is no state to unwind — the project's `provider` field is a single string; overwriting it is the entire migration.

To revert globally: delete or blank `default_provider` from `config.json`. All projects without an explicit provider revert to claude.

---

## 8. Test Plan

The following manual tests verify the rollout is ready for merge.

### Backwards compat

| # | Test | Expected |
|---|------|---------|
| BC-1 | Dispatch a task on a project with no `provider` field set | Runs claude; output, Scribe, MCP all work |
| BC-2 | Dispatch a task on a project with `provider: 'claude'` set explicitly | Identical to BC-1 |
| BC-3 | Settings → Agent Provider → set global default to `claude` → dispatch | Same as BC-1 |

### Gemini provider

| # | Test | Expected |
|---|------|---------|
| GM-1 | Install Gemini CLI, set `GEMINI_API_KEY`, switch a project to Gemini, dispatch | Task runs; output appears; agent_log entry created with `provider: 'gemini'` |
| GM-2 | Send a followup while Gemini session is running | Accepted; Gemini re-spawns with transcript prefix |
| GM-3 | Press Stop during a Gemini run | Process killed; status → stopped |
| GM-4 | Switch project to Gemini while a claude session is running | Running session unaffected; next dispatch uses Gemini |
| GM-5 | Switch to Gemini without `GEMINI_API_KEY` set | Dispatch fails fast with an auth error; no hang |
| GM-6 | Open Settings → Agent Provider with Gemini not installed | Grey "not installed" badge; install command visible |

### Settings UI

| # | Test | Expected |
|---|------|---------|
| UI-1 | Open Settings with only claude installed | Agent Provider section shows only claude row; no global-default picker (or picker with single option) |
| UI-2 | Open Settings with Gemini also installed | Both rows appear; global-default picker shows both options |
| UI-3 | Change global default to Gemini → close → reopen Settings | Persisted; select shows Gemini selected |
| UI-4 | Click project three-dot menu with Gemini installed | Agent Provider submenu shows both; Gemini shows green dot |
| UI-5 | Click Gemini in submenu when not installed | Toast shows install command |

### Telemetry

| # | Test | Expected |
|---|------|---------|
| TM-1 | Run a Gemini task → check `/api/project/<id>/agent/log` | Entry has `"provider": "gemini"` |
| TM-2 | Run a claude task → check log | Entry has `"provider": "claude"` |
| TM-3 | Inspect a pre-existing log entry (from before this branch) | No `provider` key (tolerated as claude by convention) |
