# Multi-Provider Agent Runtime — Test Plan

**Branch:** `feat/multi-provider-agents`  
**Date:** 2026-05-21  
**Purpose:** Decide merge vs. iterate for the multi-provider AgentRuntime prototype.

---

## How to use this document

Work top to bottom. **All items in Sections A–C must pass before merging.** Items in Section D are tracked regressions — any failure there is a blocker even if the new feature works. Section E items are nice-to-have and can be deferred to the next PR.

Check boxes as you go. A failing item = open an issue and note it in the box.

---

## Prerequisites / Setup

- [ ] **Check out the branch:** `git checkout feat/multi-provider-agents`
- [ ] **Restart MC** (required — server.py changes are only active after restart)
- [ ] **Confirm MC is healthy:** open the dashboard, verify no 500 errors in the log
- [ ] **Install Gemini CLI** (needed for Section B):
  ```
  npm install -g @google/gemini-cli
  ```
  Then either set `GEMINI_API_KEY=<your-key>` in your environment, or run `gemini` once to complete browser OAuth.
- [ ] **Verify Gemini auth:** `gemini --prompt "say hi"` returns a response in your terminal.

---

## Section A — Automated Tests (pytest)

These tests do not require Gemini to be installed. Run from the repo root:
```
pytest tests/ -v
```

### A1 — Import and registry (no subprocess)

> File: `tests/test_agent_runtime.py` (to be created per A-setup below)
>
> **A-setup:** Before running, create `tests/test_agent_runtime.py` using the
> template in Appendix 1 at the bottom of this document.

- [ ] **A1.1** `import agent_runtime` succeeds with no side effects (no subprocess spawned, no file I/O outside tmp).
- [ ] **A1.2** `available_runtimes()` returns at least two entries (`claude` and `gemini`) after module import.
- [ ] **A1.3** `default_runtime_name()` returns `'claude'`.
- [ ] **A1.4** `get_runtime('claude')` returns a `ClaudeRuntime` instance; `get_runtime('gemini')` returns a `GeminiRuntime` instance.
- [ ] **A1.5** `get_runtime('nonexistent')` raises `KeyError`.
- [ ] **A1.6** `runtime_for_project({})` returns the `ClaudeRuntime` (empty project → default).
- [ ] **A1.7** `runtime_for_project({'provider': 'gemini'})` returns the `GeminiRuntime`.
- [ ] **A1.8** `runtime_for_project({'provider': 'unknown_future_provider'})` falls back to `ClaudeRuntime` silently (no exception).
- [ ] **A1.9** `ProviderCapabilities` for claude has `supports_mode_b=True`, `supports_mcp=True`, `supports_skills=True`, `supports_plan_mode=True`.
- [ ] **A1.10** `ProviderCapabilities` for gemini has `supports_mode_b=False`, `supports_mcp=False`, `supports_skills=False`, `supports_plan_mode=False`.

### A2 — ClaudeRuntime hook delegation

- [ ] **A2.1** Before `register_claude_hooks()` is called, `ClaudeRuntime.health_check()` still returns a `HealthStatus` (probes PATH — may or may not find claude, but must not raise).
- [ ] **A2.2** After `register_claude_hooks()` is called with mock callables, each ABC method calls the corresponding mock. Verify with a spy:
  - `dispatch()` → calls `_CLAUDE_HOOKS['dispatch']`
  - `write_followup()` → calls `_CLAUDE_HOOKS['followup']`
  - `stop()` → calls `_CLAUDE_HOOKS['stop']`
  - `interrupt()` → calls `_CLAUDE_HOOKS['interrupt']`
- [ ] **A2.3** `ClaudeRuntime.oneshot()` returns `None` if no `oneshot` hook is registered (base-class default).

### A3 — GeminiRuntime binary resolution (no real subprocess)

Using `monkeypatch` to control `shutil.which` and fake filesystem:

- [ ] **A3.1** `resolve_binary()` returns `None` when `shutil.which('gemini')` returns `None` and no candidate paths exist.
- [ ] **A3.2** `resolve_binary()` returns the path when `shutil.which('gemini')` returns a valid path.
- [ ] **A3.3** `health_check()` returns `HealthStatus(installed=False)` when `resolve_binary()` returns `None`.
- [ ] **A3.4** `health_check()` returns `HealthStatus(installed=True)` when binary exists and `--version` subprocess call is mocked to return `"gemini 1.x"`.
- [ ] **A3.5** `health_check()` returns `auth_state.method='env:GEMINI_API_KEY'` when `GEMINI_API_KEY` is set in the environment.
- [ ] **A3.6** Cache invalidation: calling `resolve_binary()` twice with the same mock returns the cached result (no second subprocess call).

### A4 — GeminiRuntime event parsing (no real subprocess)

Mock `subprocess.Popen` to inject fake JSON lines; verify `AgentEvent` normalization:

- [ ] **A4.1** A line `{"type":"message","content":"hello"}` is normalized to `AgentEvent(type=EventType.ASSISTANT_TEXT, payload={'text': 'hello'})`.
- [ ] **A4.2** A plain text line (non-JSON) is treated as `ASSISTANT_TEXT`.
- [ ] **A4.3** A `{"type":"error","message":"..."}` line is normalized to `AgentEvent(type=EventType.ERROR)`.
- [ ] **A4.4** Malformed JSON on a line (e.g. `{bad`) does not crash the reader thread; it emits an `ERROR` event or silently skips.
- [ ] **A4.5** EOF on stdout triggers a `PROCESS_EXIT` event with `payload.returncode`.

### A5 — server.py import + providers endpoint

Reuse the existing `tmp_data_dir` fixture:

- [ ] **A5.1** `import server` (with `tmp_data_dir`) does NOT raise — the `agent_runtime` import at the top of `server.py` does not break the server import.
- [ ] **A5.2** `GET /api/agent/providers` returns HTTP 200 with JSON body containing `providers` list and `default` key.
- [ ] **A5.3** The `providers` list contains at least one entry with `name='claude'` and `default=true`.
- [ ] **A5.4** Each entry in `providers` has keys: `name`, `display_name`, `installed`, `capabilities`, `default`. Missing keys = test failure.
- [ ] **A5.5** A project record with `provider='gemini'` dispatched through `_dispatch_via_runtime()` does NOT touch the legacy `_dispatch_agent_internal()` claude path (verify by checking that the claude-specific `_resolve_claude()` function is NOT called).

### A6 — Regression: existing tests still pass

- [ ] **A6.1** `pytest tests/test_smoke.py -v` — all 3 tests green.
- [ ] **A6.2** `pytest tests/test_condense_structured.py -v` — all 9 tests green.
- [ ] **A6.3** `pytest tests/test_github_sync_p0.py -v` — all 16 tests green.
- [ ] **A6.4** `pytest tests/test_p2_3_log_shim.py -v` — all tests green.
- [ ] **A6.5** Full suite: `pytest tests/ -v` exits zero.

---

## Section B — Manual Scenarios (MUST PASS TO MERGE)

### B1 — Provider endpoint and UI (no Gemini install needed)

- [ ] **B1.1** `curl http://localhost:5199/api/agent/providers` returns JSON with `providers` array and `"default": "claude"`.
- [ ] **B1.2** Open any existing project's three-dot menu. Confirm a new **"Agent Provider"** submenu item is present (only appears when ≥2 runtimes are registered).
- [ ] **B1.3** The "Agent Provider" submenu shows **Claude Code** with a green active dot.
- [ ] **B1.4** If Gemini CLI is not installed, clicking "Gemini CLI" in the submenu shows a toast with the install hint (`npm install -g @google/gemini-cli`), NOT a broken state.

### B2 — Claude unchanged (core regression, MUST PASS)

Use an existing project whose `provider` field is unset (any project created before this branch).

- [ ] **B2.1 Dispatch:** Send `What is 2 + 2?`. Verify the session goes `running` → text streams → `completed`. No errors in console.
- [ ] **B2.2 Followup:** After completion, send a followup message (`What about 3 + 3?`). Verify it resumes and answers correctly.
- [ ] **B2.3 Interrupt:** Start a long task (`Count slowly from 1 to 100, one number per line`). While running, click the interrupt button. Verify the session stops and status changes.
- [ ] **B2.4 Stop:** Start a new task. While running, click Stop. Verify status → `stopped` and the process is killed (no zombie in task manager).
- [ ] **B2.5 Agent log:** After a completed claude session, verify it appears in the project's run history (the three-dot → History view).

### B3 — Provider selection (requires Gemini install)

- [ ] **B3.1** Create a **new** test project pointing at any folder (e.g. a temp dir or the repo root).
- [ ] **B3.2** Open the project's three-dot menu → Agent Provider → **Gemini CLI**. Verify a toast confirms the switch.
- [ ] **B3.3** `GET /api/project/<id>` shows `"provider": "gemini"` in the response JSON.
- [ ] **B3.4** Switch back to Claude Code via the same menu. Verify `provider` field reverts (or is removed).

### B4 — Gemini dispatch end-to-end (requires Gemini install)

Switch the test project back to Gemini for this section.

- [ ] **B4.1 Basic dispatch:** Send `List three small files in this folder and describe them`. Verify:
  - Session status goes `running`
  - Text streams into the log (not blank, not error)
  - Session reaches `completed` or `done`
- [ ] **B4.2 Session dict shape:** After completion, the session in `agent_sessions` has `status`, `log_lines`, and `proc` keys (same shape as claude sessions). Verify via `GET /api/project/<id>/agent/status`.
- [ ] **B4.3 SSE stream:** Open the MC dashboard during dispatch. Verify the output appears in real time (not buffered at the end). Text should stream line by line.
- [ ] **B4.4 Error handling — bad prompt:** Send an empty message. Verify MC does not crash; the session either errors gracefully or returns an empty response, but `status` settles to a terminal value (`completed`, `error`, or `stopped`).

### B5 — Gemini followup (requires Gemini install)

After B4.1 completes:

- [ ] **B5.1** Send a followup: `Now what is 2 + 2?`
- [ ] **B5.2** Verify the new session starts and Gemini responds. The prior turn's context should be visible in the prompt (the `[Prior turn excerpt for context only]` header if checkpoint is not supported, or seamless continuation if it is).
- [ ] **B5.3** Verify the old process was killed (PID changed or previous `proc` is gone) and a fresh process was spawned.

### B6 — Gemini interrupt (requires Gemini install)

- [ ] **B6.1** Start a long Gemini task: `Write a very detailed 500-word essay on the history of computing.`
- [ ] **B6.2** While the session is `running`, click the interrupt button and send `Actually, just tell me what year the first computer was built.`
- [ ] **B6.3** Verify the old process is killed and a new process spawns with the new message.
- [ ] **B6.4** Verify the new session completes and answers the new question.

### B7 — Gemini stop (requires Gemini install)

- [ ] **B7.1** Start a long Gemini task: `Count slowly from 1 to 200, one number per line, with a short pause between each.`
- [ ] **B7.2** While the session is `running`, click Stop.
- [ ] **B7.3** Verify status changes to `stopped` within ~2 seconds.
- [ ] **B7.4** Verify the process is killed (no orphan `gemini` process in task manager / `tasklist`).

### B8 — Gemini binary not found (graceful degradation)

Temporarily rename or unset the gemini binary (e.g. `GEMINI_API_KEY=` and rename `gemini.cmd`):

- [ ] **B8.1** `GET /api/agent/providers` still returns HTTP 200. The gemini entry has `"installed": false` and a non-empty `install_hint`.
- [ ] **B8.2** Attempting to dispatch to a gemini project returns an error response (HTTP 4xx or an error event in the SSE stream), NOT a server 500.
- [ ] **B8.3** The claude provider is unaffected — dispatch still works.

---

## Section C — Regression Checklist (claude-only, MUST PASS)

These verify the abstraction layer did not break any existing claude behavior.

### C1 — Session lifecycle

- [ ] **C1.1 Guardian:** Start a claude task that takes more than 30 seconds. Verify the guardian heartbeat fires and session does not get stuck in `running` state after completion.
- [ ] **C1.2 Revive from agent_log:** Restart MC while a claude session is in progress (or simulate by having a completed session without a clean teardown). Verify the session history is preserved and visible in run history.
- [ ] **C1.3 Session resume (Mode B):** On a project using the default streaming mode, dispatch a task, wait for completion, then dispatch another. Verify the second dispatch reuses the persistent process (no cold-start delay on second turn).

### C2 — Memory and context

- [ ] **C2.1 MEMORY.md injection:** On a project with an established MEMORY.md, dispatch a task that references something in memory (e.g. `What is the name of the Clayrune mascot?`). Verify the agent answers "Claydo" (or whatever is in memory), proving the read-floor context injection still fires.
- [ ] **C2.2 Scribe:** After a claude session completes, check the project's MEMORY.md Session Log. Verify a new entry was added (Scribe fired at session end).
- [ ] **C2.3 Checkpoint:** During a longer claude session (several tool calls), verify that mid-session checkpoint entries appear in MEMORY.md (the `<!-- clayrune:wm:<sid> -->` watermarks).

### C3 — Features that must not be affected

- [ ] **C3.1 Plan mode:** Dispatch a task to claude using `#plan` or via the plan-mode UI. Verify the plan approval flow still appears and works.
- [ ] **C3.2 MCP tools:** On a project with MCP servers configured, dispatch a task that uses an MCP tool. Verify the tool call fires and the result is returned correctly.
- [ ] **C3.3 Skills invocation:** Dispatch a task that triggers a skill (e.g. `/mc-project-status`). Verify the skill fires and its output appears.
- [ ] **C3.4 AskUserQuestion:** Dispatch a claude task that calls `AskUserQuestion` (e.g. a task that needs user clarification). Verify the question appears in the MC UI and the answer is forwarded to the agent.
- [ ] **C3.5 Hivemind workers:** Create a small hivemind with claude as the provider. Verify at least one worker dispatches successfully, bus messages flow, and the handoff endpoint accepts the completion.
- [ ] **C3.6 Image attachments:** Attach an image to a claude prompt. Verify the thumbnail appears in the chat bubble and the agent receives the image (confirms the thumbnail UI change in index.html did not break the attach path).

### C4 — Infrastructure

- [ ] **C4.1 Server restart:** Restart MC cleanly. Verify all existing projects and their `provider` fields are preserved. Projects without `provider` still dispatch via claude.
- [ ] **C4.2 DATA_DIR integrity:** After running several sessions (claude + gemini), verify `load_projects()` does not pick up any stray files from `data/projects/` (check the project list in the UI — no phantom projects with garbage names).
- [ ] **C4.3 Providers endpoint is stable:** Call `GET /api/agent/providers` 10 times rapidly (use `for i in $(seq 10); do curl -s http://localhost:5199/api/agent/providers; done`). Verify all 10 return HTTP 200 with the same payload (no race condition on `_RUNTIMES` dict).

---

## Section D — Out-of-scope / Nice to Have (not blocking merge)

These are known gaps documented in CHANGELOG.md "What does NOT work". They are tracked for the follow-up PR, not a merge gate.

- [ ] **D1 — Scribe/condense via Gemini:** Trigger a Scribe call on a completed Gemini session. Expected: Scribe calls `_resolve_claude()` directly (bypasses GeminiRuntime.oneshot) and works. This is the known limitation; verify it doesn't crash.
- [ ] **D2 — Agent log entries for Gemini:** After a Gemini session completes, check the agent_log. Expected: no entry (known gap — deferred to PR-2). Acceptable for merge.
- [ ] **D3 — Auth UI for Gemini:** Open Settings. Expected: no Gemini auth row (known gap). Acceptable for merge.
- [ ] **D4 — Memory/context injection via GEMINI.md:** On a gemini project, verify the system prompt is being prepended to the task (inspect the actual prompt passed to `gemini`). Expected: `[system_prompt]\n\n[task]` in the process args. The GEMINI.md context-file approach is deferred to PR-2.
- [ ] **D5 — Hivemind worker on Gemini:** Create a hivemind with a workstream assigned to a gemini project. Expected: MAY not work (hivemind dispatch path has not been updated). Acceptable if it fails gracefully.
- [ ] **D6 — `claude_session_id` on Gemini sessions:** Check `GET /api/project/<id>/agent/status` for a gemini session. Expected: `claude_session_id` is absent or null. The UI should fall back to `session_id` for transcript links.
- [ ] **D7 — Gemini MCP:** On a gemini project with MCP servers configured in `.mcp.json`, dispatch a task requiring an MCP tool. Expected: tool call does NOT fire (gemini driver does not inject MCP). Acceptable for merge — this is in the parity matrix as a future feature.

---

## Section E — Decision Framework

### Merge criteria

**Merge `feat/multi-provider-agents` → master when ALL of these are true:**

| Check | Status |
|---|---|
| A1–A6 (all automated tests) pass | ☐ |
| B1–B8 (all manual scenarios) pass | ☐ |
| C1–C4 (all regression checks) pass | ☐ |
| No Section D item causes a crash or data corruption (graceful degradation is acceptable) | ☐ |
| `git diff master server.py` confirms the legacy claude path has no logic changes inside its branch (only the new early-exit branch at the top of `_dispatch_agent_internal`) | ☐ |

### Iterate criteria

**Do NOT merge if:**

- Any A-series automated test fails (the abstraction is structurally broken)
- B2.1–B2.5 (claude unchanged) fails (the abstraction broke the default path)
- C3.1–C3.5 (plan mode / MCP / skills / AskUserQuestion / hivemind) fails
- B4.1 (Gemini basic dispatch) fails in a way that corrupts the session dict or causes MC to error-loop
- B8.1–B8.3 (graceful degradation when Gemini not installed) fails — this must never break claude users

### After merging

Recommended follow-up PRs (from the design doc, not blocking this merge):

1. **PR-2:** Migrate `_resolve_claude()` call sites in Scribe / condense / hivemind / mcp_installer to use `runtime.oneshot()` / `runtime_for_project()`.
2. **PR-3:** Auth UI endpoints `/api/agent/<provider>/auth-*`.
3. **PR-4:** Agent log entries for non-claude sessions.
4. **PR-5:** Third provider implementation (codex or opencode — Tier 1 targets per capability matrix).

---

## Appendix 1 — Pytest template for `tests/test_agent_runtime.py`

Create this file before running Section A. It covers A1–A5 in a single module.

```python
"""Unit tests for agent_runtime.py (no subprocess, no real Gemini/Claude)."""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def _fresh_module():
    """Reload agent_runtime to get a clean _RUNTIMES registry per test."""
    if 'agent_runtime' in sys.modules:
        del sys.modules['agent_runtime']
    yield
    if 'agent_runtime' in sys.modules:
        del sys.modules['agent_runtime']


def _import():
    import agent_runtime as ar
    return ar


# ─── A1 Registry ────────────────────────────────────────────────────────────

def test_import_no_side_effects():
    ar = _import()
    assert ar is not None


def test_available_runtimes_has_claude_and_gemini():
    ar = _import()
    names = {r.name for r in ar.available_runtimes()}
    assert 'claude' in names
    assert 'gemini' in names


def test_default_runtime_name():
    ar = _import()
    assert ar.default_runtime_name() == 'claude'


def test_get_runtime_known():
    ar = _import()
    assert ar.get_runtime('claude').name == 'claude'
    assert ar.get_runtime('gemini').name == 'gemini'


def test_get_runtime_unknown_raises():
    ar = _import()
    with pytest.raises(KeyError):
        ar.get_runtime('nonexistent')


def test_runtime_for_project_empty():
    ar = _import()
    r = ar.runtime_for_project({})
    assert r.name == 'claude'


def test_runtime_for_project_gemini():
    ar = _import()
    r = ar.runtime_for_project({'provider': 'gemini'})
    assert r.name == 'gemini'


def test_runtime_for_project_unknown_falls_back():
    ar = _import()
    r = ar.runtime_for_project({'provider': 'future_ai_provider'})
    assert r.name == 'claude'


def test_claude_capabilities():
    ar = _import()
    caps = ar.get_runtime('claude').capabilities()
    assert caps.supports_mode_b is True
    assert caps.supports_mcp is True
    assert caps.supports_skills is True
    assert caps.supports_plan_mode is True


def test_gemini_capabilities():
    ar = _import()
    caps = ar.get_runtime('gemini').capabilities()
    assert caps.supports_mode_b is False
    assert caps.supports_mcp is False
    assert caps.supports_plan_mode is False


# ─── A2 ClaudeRuntime hook delegation ───────────────────────────────────────

def test_claude_health_check_without_hooks_does_not_raise():
    ar = _import()
    runtime = ar.get_runtime('claude')
    result = runtime.health_check()
    assert isinstance(result, ar.HealthStatus)


def test_claude_hooks_delegation():
    ar = _import()
    dispatch_mock = MagicMock(return_value='session-123')
    followup_mock = MagicMock()
    stop_mock = MagicMock()
    interrupt_mock = MagicMock()
    health_mock = MagicMock(return_value=ar.HealthStatus(
        installed=True, binary_path=Path('/fake/claude'),
        version='1.0', auth_state=ar.AuthState(status='ok'),
        install_hint=''))

    ar.register_claude_hooks(
        resolve_binary=MagicMock(return_value=Path('/fake/claude')),
        health_check=health_mock,
        dispatch=dispatch_mock,
        followup=followup_mock,
        stop=stop_mock,
        interrupt=interrupt_mock,
    )
    runtime = ar.get_runtime('claude')
    runtime.health_check()
    health_mock.assert_called_once()


# ─── A3 GeminiRuntime binary resolution ─────────────────────────────────────

def test_gemini_resolve_binary_not_found(monkeypatch):
    ar = _import()
    monkeypatch.setattr('shutil.which', lambda _: None)
    runtime = ar.get_runtime('gemini')
    runtime._bin_cache = None  # clear cache
    with patch.object(Path, 'exists', return_value=False):
        result = runtime.resolve_binary()
    assert result is None


def test_gemini_health_check_not_installed(monkeypatch):
    ar = _import()
    runtime = ar.get_runtime('gemini')
    runtime._bin_cache = None
    monkeypatch.setattr('shutil.which', lambda _: None)
    with patch.object(Path, 'exists', return_value=False):
        status = runtime.health_check()
    assert status.installed is False
    assert status.install_hint != ''


def test_gemini_health_check_installed(monkeypatch):
    ar = _import()
    runtime = ar.get_runtime('gemini')
    runtime._bin_cache = '/fake/gemini'
    monkeypatch.setenv('GEMINI_API_KEY', 'fake-key')
    fake_result = MagicMock()
    fake_result.stdout = 'gemini 1.5.0'
    fake_result.stderr = ''
    with patch('subprocess.run', return_value=fake_result):
        status = runtime.health_check()
    assert status.installed is True
    assert status.auth_state.method == 'env:GEMINI_API_KEY'


# ─── A5 server.py import (uses tmp_data_dir from conftest) ──────────────────

def test_server_import_with_agent_runtime(tmp_data_dir):
    """server.py must import cleanly with agent_runtime wired in."""
    import importlib
    if 'server' in sys.modules:
        del sys.modules['server']
    server = importlib.import_module('server')
    assert server.app is not None


def test_providers_endpoint(tmp_data_dir):
    """GET /api/agent/providers returns valid JSON."""
    import importlib
    if 'server' in sys.modules:
        del sys.modules['server']
    server = importlib.import_module('server')
    client = server.app.test_client()
    resp = client.get('/api/agent/providers')
    assert resp.status_code == 200
    data = resp.get_json()
    assert 'providers' in data
    assert 'default' in data
    names = {p['name'] for p in data['providers']}
    assert 'claude' in names
    claude_entry = next(p for p in data['providers'] if p['name'] == 'claude')
    assert claude_entry.get('default') is True
    for p in data['providers']:
        for key in ('name', 'display_name', 'installed', 'capabilities', 'default'):
            assert key in p, f"missing key '{key}' in provider {p.get('name')}"
```

---

## Appendix 2 — Quick smoke curl commands

Copy-paste these to spot-check the API without the full test suite:

```bash
# Provider list
curl -s http://localhost:5199/api/agent/providers | python -m json.tool

# Switch a project to gemini (replace <project_id> with a real ID)
curl -s -X POST http://localhost:5199/api/project/<project_id> \
  -H "Content-Type: application/json" \
  -d '{"provider": "gemini"}' | python -m json.tool

# Verify the switch
curl -s http://localhost:5199/api/project/<project_id> | python -m json.tool | grep provider

# Dispatch to gemini
curl -s -X POST http://localhost:5199/api/project/<project_id>/agent/dispatch \
  -H "Content-Type: application/json" \
  -d '{"task": "What is 2 + 2?"}' | python -m json.tool

# Check session status
curl -s http://localhost:5199/api/project/<project_id>/agent/status | python -m json.tool
```

---

*Generated 2026-05-21 by ws_testplan (Hivemind hm_48bf4587)*
