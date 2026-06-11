"""Smoke tests for all non-claude AgentRuntime subclasses.

Tests verify:
1. build_command() output matches each CLI's documented invocation
2. parse_event() correctly normalizes each provider's JSONL/text output
3. capabilities() returns the correct flags for each provider
4. health_check() works without a live binary (not-installed path)
5. Registry: all 7 providers are registered at import time

These tests are standalone — no server.py, Flask, or live binary required.
Providers not installed on this machine are tested via the not-installed path.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent_runtime
from agent_runtime import (
    EventType,
    CodexRuntime,
    OpenCodeRuntime,
    GooseRuntime,
    AiderRuntime,
    KiroRuntime,
    GeminiRuntime,
)


# ─────────────────────────────────────────────────────────────────────────────
# Registry: all 7 providers registered
# ─────────────────────────────────────────────────────────────────────────────


def test_all_providers_registered():
    names = {r.name for r in agent_runtime.available_runtimes()}
    expected = {'claude', 'gemini', 'codex', 'opencode', 'goose', 'aider', 'kiro'}
    assert expected.issubset(names), f"Missing: {expected - names}"


def test_get_runtime_all_providers():
    for name in ('claude', 'gemini', 'codex', 'opencode', 'goose', 'aider', 'kiro'):
        rt = agent_runtime.get_runtime(name)
        assert rt.name == name


# ─────────────────────────────────────────────────────────────────────────────
# GeminiRuntime — verify session resume capability fix
# ─────────────────────────────────────────────────────────────────────────────


class TestGeminiRuntime:
    def setup_method(self):
        self.rt = GeminiRuntime()
        # Reset bin cache so resolve_binary actually searches
        self.rt._bin_cache = None

    def test_capabilities_session_resume(self):
        # gemini CLI supports --resume, so this should be True
        caps = self.rt.capabilities()
        assert caps.supports_session_resume is True, (
            "GeminiRuntime.capabilities() must set supports_session_resume=True "
            "— gemini CLI has --resume <id|latest> flag (confirmed v0.20.0)"
        )

    def test_build_command_base(self):
        # When binary not found, should still build a valid command shape
        cmd = self.rt.build_command()
        assert 'gemini' in cmd[0]
        assert '--output-format' in cmd
        assert 'stream-json' in cmd

    def test_parse_event_empty(self):
        assert self.rt.parse_event('') is None
        assert self.rt.parse_event('\n') is None

    def test_parse_event_plain_text(self):
        ev = self.rt.parse_event('Hello from Gemini')
        assert ev is not None
        assert ev.type == EventType.ASSISTANT_TEXT
        assert ev.payload['text'] == 'Hello from Gemini'

    def test_parse_event_stream_json_content(self):
        line = json.dumps({'type': 'content', 'text': 'Hello!'})
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.ASSISTANT_TEXT
        assert ev.payload['text'] == 'Hello!'

    def test_parse_event_tool_use(self):
        line = json.dumps({'type': 'tool_use', 'name': 'read_file', 'input': {'path': '/x'}})
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.TOOL_USE
        blocks = ev.payload['blocks']
        assert blocks[0]['name'] == 'read_file'

    def test_parse_event_result(self):
        line = json.dumps({'type': 'result', 'usage': {'tokens': 100}})
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.TURN_END

    def test_capabilities_mcp(self):
        assert self.rt.capabilities().supports_mcp is True


# ─────────────────────────────────────────────────────────────────────────────
# CodexRuntime
# ─────────────────────────────────────────────────────────────────────────────


class TestCodexRuntime:
    def setup_method(self):
        self.rt = CodexRuntime()
        self.rt._bin_cache = None
        self.rt._npx_fallback = False

    def test_build_command_basic(self):
        """codex exec --json --dangerously-bypass-approvals-and-sandbox"""
        self.rt._bin_cache = 'codex'
        self.rt._npx_fallback = False
        cmd = self.rt.build_command()
        assert 'exec' in cmd
        assert '--json' in cmd
        assert '--dangerously-bypass-approvals-and-sandbox' in cmd

    def test_build_command_with_model(self):
        self.rt._bin_cache = 'codex'
        cmd = self.rt.build_command(model='o4-mini')
        assert '-m' in cmd
        idx = cmd.index('-m')
        assert cmd[idx + 1] == 'o4-mini'

    def test_build_command_resume_last(self):
        """codex exec resume --last --json"""
        self.rt._bin_cache = 'codex'
        cmd = self.rt.build_command(resume_id='last')
        assert 'exec' in cmd
        assert 'resume' in cmd
        assert '--last' in cmd
        assert '--json' in cmd

    def test_build_command_resume_specific_id(self):
        """codex exec resume <SESSION_ID> --json"""
        self.rt._bin_cache = 'codex'
        session_id = '019e4bff-aa7d-77f1-bf2c-7e7367deb2c4'
        cmd = self.rt.build_command(resume_id=session_id)
        assert session_id in cmd
        assert 'resume' in cmd

    def test_build_command_npx_fallback(self):
        """When binary not found, uses npx @openai/codex prefix"""
        self.rt._bin_cache = '__npx__'
        self.rt._npx_fallback = True
        cmd = self.rt.build_command()
        assert cmd[0] == 'npx'
        assert '@openai/codex' in cmd
        assert '--json' in cmd

    def test_parse_event_thread_started(self):
        """thread.started → INIT with thread_id"""
        line = json.dumps({'type': 'thread.started',
                           'thread_id': '019e4bff-aa7d-77f1-bf2c-7e7367deb2c4'})
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.INIT
        assert ev.payload['thread_id'] == '019e4bff-aa7d-77f1-bf2c-7e7367deb2c4'

    def test_parse_event_turn_started_suppressed(self):
        """turn.started → None (suppressed internal event)"""
        line = json.dumps({'type': 'turn.started'})
        ev = self.rt.parse_event(line)
        assert ev is None

    def test_parse_event_error(self):
        """error → EventType.ERROR"""
        line = json.dumps({'type': 'error',
                           'message': 'model not supported'})
        ev = self.rt.parse_event(line, mc_session_id='abc')
        assert ev is not None
        assert ev.type == EventType.ERROR
        assert 'model not supported' in ev.payload['text']

    def test_parse_event_turn_failed(self):
        """turn.failed → EventType.ERROR"""
        line = json.dumps({'type': 'turn.failed',
                           'error': {'message': 'API error 400'}})
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.ERROR
        assert 'API error 400' in ev.payload['text']

    def test_parse_event_turn_completed(self):
        """turn.completed → TURN_END"""
        line = json.dumps({'type': 'turn.completed',
                           'usage': {'input_tokens': 10, 'output_tokens': 5}})
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.TURN_END
        assert ev.payload['usage'] == {'input_tokens': 10, 'output_tokens': 5}

    def test_parse_event_item_completed_message(self):
        """item.completed with message → ASSISTANT_TEXT"""
        line = json.dumps({
            'type': 'item.completed',
            'item': {
                'type': 'message',
                'content': [{'type': 'output_text', 'text': 'Hello from Codex!'}],
            },
        })
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.ASSISTANT_TEXT
        assert 'Hello from Codex!' in ev.payload['text']

    def test_parse_event_plain_text_fallback(self):
        """Non-JSON lines → ASSISTANT_TEXT"""
        ev = self.rt.parse_event('Reading prompt from stdin...')
        assert ev is not None
        assert ev.type == EventType.ASSISTANT_TEXT

    def test_parse_event_empty(self):
        assert self.rt.parse_event('') is None

    def test_capabilities(self):
        caps = self.rt.capabilities()
        assert caps.name == 'codex'
        assert caps.supports_session_resume is True
        assert caps.supports_mcp is True
        assert caps.supports_plan_mode is True
        assert caps.emits_cost is True
        assert caps.context_injection == 'file'
        assert caps.context_file_name == 'AGENTS.md'

    def test_health_check_not_installed(self, monkeypatch):
        """When neither binary nor npx is found, installed=False."""
        import shutil
        monkeypatch.setattr(shutil, 'which', lambda _: None)
        self.rt._bin_cache = None
        self.rt._npx_fallback = False
        hs = self.rt.health_check()
        assert hs.installed is False
        assert hs.auth_state.status == 'not_installed'
        assert 'npm install' in hs.install_hint

    def test_transcript_path_missing_session(self):
        assert self.rt.transcript_path('/some/path', '') is None

    def test_live_probe_events(self):
        """Live probe: codex exec --json emits thread.started as first event.

        This test runs only when npx is available and is marked as slow.
        It verifies the actual JSONL format from the running binary.
        """
        import shutil
        if not shutil.which('npx'):
            pytest.skip('npx not available on this machine')

        # We just verify the first line (thread.started) without needing auth
        import subprocess
        rt = CodexRuntime()
        rt._bin_cache = None
        cmd = rt._cmd_prefix() + ['exec', '--json',
                                   '--dangerously-bypass-approvals-and-sandbox']
        try:
            proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True,
                encoding='utf-8', errors='replace',
            )
            proc.stdin.write('echo ok\n')
            proc.stdin.close()
            first_line = proc.stdout.readline()
            proc.kill()
            proc.wait()
        except Exception as e:
            pytest.skip(f'codex exec failed: {e}')

        if not first_line.strip():
            pytest.skip('no output from codex exec')

        try:
            msg = json.loads(first_line.strip())
        except json.JSONDecodeError:
            pytest.fail(f'First line not JSON: {first_line!r}')

        assert msg.get('type') == 'thread.started', f'Expected thread.started, got: {msg}'
        assert 'thread_id' in msg, f'Expected thread_id in: {msg}'


# ─────────────────────────────────────────────────────────────────────────────
# OpenCodeRuntime
# ─────────────────────────────────────────────────────────────────────────────


class TestOpenCodeRuntime:
    def setup_method(self):
        self.rt = OpenCodeRuntime()
        self.rt._bin_cache = None

    def test_build_command_basic(self):
        """opencode run --format json"""
        self.rt._bin_cache = 'opencode'
        cmd = self.rt.build_command()
        assert 'opencode' in cmd[0]
        assert 'run' in cmd
        assert '--format' in cmd
        assert 'json' in cmd

    def test_build_command_resume_last(self):
        """opencode run --format json --continue"""
        self.rt._bin_cache = 'opencode'
        cmd = self.rt.build_command(resume_id='last')
        assert '--continue' in cmd

    def test_build_command_resume_specific(self):
        """opencode run --format json --session <ID>"""
        self.rt._bin_cache = 'opencode'
        cmd = self.rt.build_command(resume_id='abc123')
        assert '--session' in cmd
        assert 'abc123' in cmd

    def test_parse_event_session(self):
        line = json.dumps({'type': 'session', 'properties': {'id': 'sess-abc', 'model': 'claude-3'}})
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.INIT
        assert ev.session_id == 'sess-abc'

    def test_parse_event_assistant_message(self):
        line = json.dumps({
            'type': 'message',
            'role': 'assistant',
            'content': [{'type': 'text', 'text': 'Hello from OpenCode!'}],
        })
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.ASSISTANT_TEXT
        assert 'Hello from OpenCode!' in ev.payload['text']

    def test_parse_event_user_message_ignored(self):
        line = json.dumps({'type': 'message', 'role': 'user', 'content': 'hi'})
        ev = self.rt.parse_event(line)
        assert ev is None

    def test_parse_event_done(self):
        line = json.dumps({'type': 'done', 'info': {'cost': 0.002, 'usage': {}}})
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.TURN_END
        assert ev.payload['cost_usd'] == 0.002

    def test_parse_event_error(self):
        line = json.dumps({'type': 'error', 'error': {'message': 'rate limit'}})
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.ERROR

    def test_capabilities(self):
        caps = self.rt.capabilities()
        assert caps.name == 'opencode'
        assert caps.supports_session_resume is True
        assert caps.supports_mcp is True
        assert caps.emits_cost is True

    def test_health_check_not_installed(self, monkeypatch):
        import shutil
        monkeypatch.setattr(shutil, 'which', lambda _: None)
        self.rt._bin_cache = None
        hs = self.rt.health_check()
        assert hs.installed is False


# ─────────────────────────────────────────────────────────────────────────────
# GooseRuntime
# ─────────────────────────────────────────────────────────────────────────────


class TestGooseRuntime:
    def setup_method(self):
        self.rt = GooseRuntime()
        self.rt._bin_cache = None

    def test_build_command_basic(self):
        """goose run --no-session --output-format stream-json"""
        self.rt._bin_cache = 'goose'
        cmd = self.rt.build_command()
        assert 'goose' in cmd[0]
        assert 'run' in cmd
        assert '--no-session' in cmd
        assert '--output-format' in cmd
        assert 'stream-json' in cmd

    def test_build_command_with_system_prompt(self):
        """goose run --system TEXT --no-session --output-format stream-json"""
        self.rt._bin_cache = 'goose'
        cmd = self.rt.build_command(system_prompt='You are a coding assistant.')
        assert '--system' in cmd
        idx = cmd.index('--system')
        assert 'coding assistant' in cmd[idx + 1]

    def test_build_command_with_model(self):
        self.rt._bin_cache = 'goose'
        cmd = self.rt.build_command(model='openai/gpt-4o')
        assert '--model' in cmd
        idx = cmd.index('--model')
        assert cmd[idx + 1] == 'openai/gpt-4o'

    def test_parse_event_plain_text(self):
        ev = self.rt.parse_event('Analyzing your code...')
        assert ev is not None
        assert ev.type == EventType.ASSISTANT_TEXT

    def test_parse_event_init(self):
        line = json.dumps({'type': 'init', 'session_id': 'goose-sess-1', 'model': 'gpt-4o'})
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.INIT
        assert ev.payload['session_id'] == 'goose-sess-1'

    def test_parse_event_assistant_message(self):
        line = json.dumps({
            'type': 'message',
            'role': 'assistant',
            'content': [{'type': 'text', 'text': 'Hello from Goose!'}],
        })
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.ASSISTANT_TEXT

    def test_parse_event_tool_use(self):
        line = json.dumps({'type': 'tool_use', 'name': 'bash', 'input': {'cmd': 'ls'}})
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.TOOL_USE
        assert ev.payload['blocks'][0]['name'] == 'bash'

    def test_parse_event_result(self):
        line = json.dumps({'type': 'result', 'usage': {}})
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.TURN_END

    def test_parse_event_error(self):
        line = json.dumps({'type': 'error', 'message': 'provider not configured'})
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.ERROR

    def test_capabilities(self):
        caps = self.rt.capabilities()
        assert caps.name == 'goose'
        assert caps.supports_mcp is True
        assert caps.supports_session_resume is True
        assert caps.context_injection == 'flag'
        assert caps.emits_cost is False  # goose doesn't emit cost

    def test_health_check_not_installed(self, monkeypatch):
        import shutil
        monkeypatch.setattr(shutil, 'which', lambda _: None)
        self.rt._bin_cache = None
        hs = self.rt.health_check()
        assert hs.installed is False
        assert 'goose' in hs.install_hint.lower()


# ─────────────────────────────────────────────────────────────────────────────
# AiderRuntime
# ─────────────────────────────────────────────────────────────────────────────


class TestAiderRuntime:
    def setup_method(self):
        self.rt = AiderRuntime()
        self.rt._bin_cache = None

    def test_build_command_basic(self):
        """aider --no-stream --yes --no-auto-commits"""
        self.rt._bin_cache = 'aider'
        cmd = self.rt.build_command()
        assert 'aider' in cmd[0]
        assert '--no-stream' in cmd
        assert '--yes' in cmd
        assert '--no-auto-commits' in cmd

    def test_build_command_with_model(self):
        self.rt._bin_cache = 'aider'
        cmd = self.rt.build_command(model='claude-3-5-sonnet-20241022')
        assert '--model' in cmd
        idx = cmd.index('--model')
        assert cmd[idx + 1] == 'claude-3-5-sonnet-20241022'

    def test_build_command_dry_run(self):
        self.rt._bin_cache = 'aider'
        cmd = self.rt.build_command(dry_run=True)
        assert '--dry-run' in cmd

    def test_parse_event_plain_text(self):
        """Aider plain text → ASSISTANT_TEXT for every non-empty line"""
        ev = self.rt.parse_event('Applying changes to auth.py...')
        assert ev is not None
        assert ev.type == EventType.ASSISTANT_TEXT
        assert ev.payload['text'] == 'Applying changes to auth.py...'

    def test_parse_event_empty(self):
        assert self.rt.parse_event('') is None

    def test_parse_event_tokens_line(self):
        """Aider token/cost lines are surfaced as ASSISTANT_TEXT"""
        ev = self.rt.parse_event('Tokens: 1234 sent, 567 received. Cost: $0.01')
        assert ev is not None
        assert ev.type == EventType.ASSISTANT_TEXT

    def test_capabilities(self):
        caps = self.rt.capabilities()
        assert caps.name == 'aider'
        assert caps.supports_session_resume is False
        assert caps.supports_mcp is False
        assert caps.supports_plan_mode is True  # via --dry-run
        assert caps.emits_usage is False
        assert caps.context_injection == 'file'
        assert caps.context_file_name == '.aider.conf.yml'

    def test_transcript_path_missing_file(self):
        """Returns None when .aider.chat.history.md doesn't exist"""
        result = self.rt.transcript_path('/nonexistent/path', 'any')
        assert result is None

    def test_transcript_path_existing_file(self, tmp_path):
        """Returns path when .aider.chat.history.md exists"""
        hist = tmp_path / '.aider.chat.history.md'
        hist.write_text('# Aider history\n')
        result = self.rt.transcript_path(str(tmp_path), 'any')
        assert result == hist

    def test_health_check_not_installed(self, monkeypatch):
        import shutil
        monkeypatch.setattr(shutil, 'which', lambda _: None)
        self.rt._bin_cache = None
        hs = self.rt.health_check()
        assert hs.installed is False
        assert 'pip install' in hs.install_hint


# ─────────────────────────────────────────────────────────────────────────────
# KiroRuntime
# ─────────────────────────────────────────────────────────────────────────────


class TestKiroRuntime:
    def setup_method(self):
        self.rt = KiroRuntime()
        self.rt._bin_cache = None

    def test_build_command_basic(self):
        """kiro-cli --no-interactive --trust-all-tools"""
        self.rt._bin_cache = 'kiro-cli'
        cmd = self.rt.build_command()
        assert 'kiro-cli' in cmd[0]
        assert '--no-interactive' in cmd
        assert '--trust-all-tools' in cmd

    def test_parse_event_plain_text(self):
        ev = self.rt.parse_event('Analyzing repository structure...')
        assert ev is not None
        assert ev.type == EventType.ASSISTANT_TEXT

    def test_parse_event_json_rpc_metadata(self):
        """JSON-RPC notification _kiro.dev/metadata → ASSISTANT_TEXT"""
        line = json.dumps({
            'jsonrpc': '2.0',
            'method': '_kiro.dev/metadata',
            'params': {'text': 'Processing your request...'},
        })
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.ASSISTANT_TEXT
        assert 'Processing' in ev.payload['text']

    def test_parse_event_json_rpc_error(self):
        """JSON-RPC error → ERROR"""
        line = json.dumps({
            'jsonrpc': '2.0',
            'id': 1,
            'error': {'code': -32600, 'message': 'Invalid request'},
        })
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.ERROR
        assert 'Invalid request' in ev.payload['text']

    def test_parse_event_session_new(self):
        """session/new response → INIT"""
        line = json.dumps({
            'jsonrpc': '2.0',
            'id': 1,
            'method': 'session/new',
            'result': {'session_id': 'kiro-sess-abc'},
        })
        ev = self.rt.parse_event(line)
        assert ev is not None
        assert ev.type == EventType.INIT

    def test_parse_event_empty(self):
        assert self.rt.parse_event('') is None

    def test_capabilities(self):
        caps = self.rt.capabilities()
        assert caps.name == 'kiro'
        assert caps.supports_mcp is True
        assert caps.supports_plan_mode is False
        assert caps.supports_session_resume is False
        assert caps.emits_cost is False

    def test_health_check_not_installed(self, monkeypatch):
        import shutil
        monkeypatch.setattr(shutil, 'which', lambda _: None)
        self.rt._bin_cache = None
        hs = self.rt.health_check()
        assert hs.installed is False
        assert 'kiro' in hs.install_hint.lower()


# ─────────────────────────────────────────────────────────────────────────────
# Cross-provider: all runtimes pass base contract checks
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize('provider_name', [
    'gemini', 'codex', 'opencode', 'goose', 'aider', 'kiro',
])
def test_capabilities_name_matches_runtime_name(provider_name):
    rt = agent_runtime.get_runtime(provider_name)
    caps = rt.capabilities()
    assert caps.name == provider_name, (
        f"{rt.__class__.__name__}.capabilities().name must be {provider_name!r}"
    )


@pytest.mark.parametrize('provider_name', [
    'gemini', 'codex', 'opencode', 'goose', 'aider', 'kiro',
])
def test_parse_event_empty_returns_none(provider_name):
    rt = agent_runtime.get_runtime(provider_name)
    assert rt.parse_event('') is None
    assert rt.parse_event('\n') is None


@pytest.mark.parametrize('provider_name', [
    'gemini', 'codex', 'opencode', 'goose', 'aider', 'kiro',
])
def test_parse_event_plain_text_returns_assistant_event(provider_name):
    rt = agent_runtime.get_runtime(provider_name)
    ev = rt.parse_event('Some plain text from the agent')
    assert ev is not None
    assert ev.type == EventType.ASSISTANT_TEXT
    assert ev.provider == provider_name


@pytest.mark.parametrize('provider_name', [
    'gemini', 'codex', 'opencode', 'goose', 'aider', 'kiro',
])
def test_claude_regression_default_runtime(provider_name):
    """Claude runtime is still accessible after registering all other providers."""
    claude_rt = agent_runtime.get_runtime('claude')
    assert claude_rt.name == 'claude'
    # Claude's capabilities are unchanged
    caps = claude_rt.capabilities()
    assert caps.supports_mode_b is True
    assert caps.mode_b_kind == 'native'
    assert caps.supports_plan_mode is True
    assert caps.supports_session_resume is True


# ─────────────────────────────────────────────────────────────────────────────
# Followup amnesia fix — _compose_respawn_prompt re-injects system context
# ─────────────────────────────────────────────────────────────────────────────


def test_compose_respawn_prompt_reinjects_system_context():
    """Regression: Mode-A followups must re-prepend the dispatch-time system
    prompt, not just a log tail. Without this, every provider except claude
    loses MEMORY / AGENT_RULES / CLAYRUNE_API after turn 1."""
    session = {
        '_system_prompt': 'SYSTEM-CONTEXT-MARKER: rules and memory here',
        'log_lines': ['prior assistant output line'],
    }
    out = agent_runtime._compose_respawn_prompt(session, 'the new user message')
    assert 'SYSTEM-CONTEXT-MARKER' in out, 'system context dropped on followup'
    assert 'prior assistant output line' in out, 'prior-turn tail dropped'
    assert out.rstrip().endswith('the new user message')
    # System context comes first, user message last.
    assert out.index('SYSTEM-CONTEXT-MARKER') < out.index('the new user message')


def test_compose_respawn_prompt_no_system_prompt():
    """When no system prompt was stashed, the prompt is still well-formed."""
    session = {'log_lines': ['some output']}
    out = agent_runtime._compose_respawn_prompt(session, 'hello')
    assert out.rstrip().endswith('hello')
    assert 'some output' in out


def test_compose_respawn_prompt_empty_session():
    """Empty session dict — followup degrades to just the message."""
    out = agent_runtime._compose_respawn_prompt({}, 'just the message')
    assert out == 'just the message'


# ─────────────────────────────────────────────────────────────────────────────
# Gemini auth detection — OAuth credentials, not just GEMINI_API_KEY
# ─────────────────────────────────────────────────────────────────────────────


def test_gemini_auth_state_env_key(monkeypatch):
    """GEMINI_API_KEY present → ok via env."""
    monkeypatch.setenv('GEMINI_API_KEY', 'test-key')
    status, method, err = agent_runtime.get_runtime('gemini')._gemini_auth_state()
    assert status == 'ok'
    assert method == 'env:GEMINI_API_KEY'
    assert err is None


def test_gemini_auth_state_oauth_creds(monkeypatch, tmp_path):
    """Regression: cached OAuth credentials count as signed in — the card
    must not show 'status unknown' when ~/.gemini/oauth_creds.json exists."""
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    gdir = tmp_path / '.gemini'
    gdir.mkdir()
    (gdir / 'oauth_creds.json').write_text(
        json.dumps({'access_token': 'a', 'refresh_token': 'r'}), encoding='utf-8')
    (gdir / 'google_accounts.json').write_text(
        json.dumps({'active': 'user@example.com'}), encoding='utf-8')
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    monkeypatch.setenv('HOME', str(tmp_path))
    status, method, err = agent_runtime.get_runtime('gemini')._gemini_auth_state()
    assert status == 'ok'
    assert method == 'oauth (user@example.com)'
    assert err is None


def test_gemini_auth_state_not_logged_in(monkeypatch, tmp_path):
    """No env key and no OAuth creds → not_logged_in with a helpful hint."""
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    monkeypatch.setenv('USERPROFILE', str(tmp_path))
    monkeypatch.setenv('HOME', str(tmp_path))
    status, method, err = agent_runtime.get_runtime('gemini')._gemini_auth_state()
    assert status == 'not_logged_in'
    assert method is None
    assert err and 'GEMINI_API_KEY' in err
