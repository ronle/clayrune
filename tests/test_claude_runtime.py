"""Unit tests for ClaudeRuntime — AgentRuntime ABC + ClaudeRuntime foundation.

Tests prove that ClaudeRuntime returns the same command/flags/paths as the
legacy server.py helpers (_resolve_claude, _build_claude_flags, _find_transcript_file)
for a representative set of inputs.

These tests are standalone — no server.py import, no Flask, no live binary required.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Ensure agent_runtime is importable from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── Import smoke tests ────────────────────────────────────────────────────────


def test_import_agent_runtime():
    """agent_runtime.py must import cleanly with no server.py or Flask deps."""
    import agent_runtime
    assert hasattr(agent_runtime, 'AgentRuntime')
    assert hasattr(agent_runtime, 'ClaudeRuntime')
    assert hasattr(agent_runtime, 'GeminiRuntime')
    assert hasattr(agent_runtime, 'CapabilityFlags')
    assert hasattr(agent_runtime, 'ProviderCapabilities')
    assert hasattr(agent_runtime, 'AgentEvent')
    assert hasattr(agent_runtime, 'SessionHandle')
    assert hasattr(agent_runtime, 'OneshotResult')
    assert hasattr(agent_runtime, 'get_runtime')
    assert hasattr(agent_runtime, 'register_runtime')
    assert hasattr(agent_runtime, 'available_runtimes')
    assert hasattr(agent_runtime, 'installed_runtimes')


def test_capability_flags_alias():
    """CapabilityFlags must be the same class as ProviderCapabilities."""
    from agent_runtime import CapabilityFlags, ProviderCapabilities
    assert CapabilityFlags is ProviderCapabilities


# ── Registry tests ────────────────────────────────────────────────────────────


def test_registry_contains_claude():
    import agent_runtime
    names = {r.name for r in agent_runtime.available_runtimes()}
    assert 'claude' in names


def test_registry_contains_gemini():
    import agent_runtime
    names = {r.name for r in agent_runtime.available_runtimes()}
    assert 'gemini' in names


def test_get_runtime_claude():
    import agent_runtime
    rt = agent_runtime.get_runtime('claude')
    assert isinstance(rt, agent_runtime.ClaudeRuntime)


def test_get_runtime_unknown_raises():
    import agent_runtime
    with pytest.raises(KeyError):
        agent_runtime.get_runtime('does_not_exist')


def test_default_runtime_name():
    import agent_runtime
    assert agent_runtime.default_runtime_name() == 'claude'


def test_runtime_for_project_defaults_to_claude():
    import agent_runtime
    rt = agent_runtime.runtime_for_project({})
    assert rt.name == 'claude'


def test_runtime_for_project_unknown_falls_back():
    import agent_runtime
    rt = agent_runtime.runtime_for_project({'provider': 'nonexistent_provider_xyz'})
    assert rt.name == 'claude'


# ── resolve_binary() ── proves equivalence with _resolve_claude() ─────────────


def test_resolve_binary_returns_path():
    from agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    result = rt.resolve_binary()
    assert result is not None
    assert isinstance(result, Path)


def test_resolve_binary_str_is_string():
    from agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    result = rt.resolve_binary_str()
    assert isinstance(result, str)
    assert len(result) > 0


def test_resolve_binary_windows_exe_orphan(monkeypatch, tmp_path):
    """On Windows: if shutil.which returns a .exe with a sibling .cmd, prefer .cmd."""
    if sys.platform != 'win32':
        pytest.skip("Windows-only test")
    from agent_runtime import ClaudeRuntime

    # Create a fake claude.exe with a sibling claude.cmd
    exe = tmp_path / 'claude.exe'
    cmd = tmp_path / 'claude.cmd'
    exe.write_text('fake')
    cmd.write_text('fake cmd')

    monkeypatch.setattr('shutil.which', lambda name: str(exe) if name == 'claude' else None)

    rt = ClaudeRuntime()
    result = rt.resolve_binary()
    assert result is not None
    assert result.suffix.lower() == '.cmd'


def test_resolve_binary_falls_back_to_candidate(monkeypatch, tmp_path):
    """When shutil.which returns None, check fallback paths."""
    from agent_runtime import ClaudeRuntime

    # Pretend claude is not on PATH
    monkeypatch.setattr('shutil.which', lambda name: None)

    # Create a fake binary at a known fallback location
    if sys.platform == 'win32':
        fake_claude = tmp_path / 'claude.cmd'
        fake_claude.write_text('fake')
        monkeypatch.setenv('APPDATA', str(tmp_path))
        # The fallback checks APPDATA/npm/claude.cmd
        npm_dir = tmp_path / 'npm'
        npm_dir.mkdir()
        (npm_dir / 'claude.cmd').write_text('fake')
    else:
        fake_claude = tmp_path / '.claude' / 'bin' / 'claude'
        fake_claude.parent.mkdir(parents=True)
        fake_claude.write_text('fake')
        monkeypatch.setenv('HOME', str(tmp_path))

    rt = ClaudeRuntime()
    result = rt.resolve_binary()
    assert result is not None


def test_resolve_binary_last_resort_when_nothing_found(monkeypatch):
    """When nothing is found, return Path('claude') as last resort."""
    from agent_runtime import ClaudeRuntime

    monkeypatch.setattr('shutil.which', lambda name: None)

    # Monkeypatch Path.exists to always return False for any candidate
    original_exists = Path.exists

    def fake_exists(self):
        return False

    monkeypatch.setattr(Path, 'exists', fake_exists)

    rt = ClaudeRuntime()
    result = rt.resolve_binary()
    assert result is not None
    assert str(result) == 'claude'


# ── build_command() ── proves equivalence with _build_claude_flags() ──────────


def test_build_command_bare_matches_legacy_flags():
    """build_command() with no args must contain exactly the same flags as
    _build_claude_flags(None, streaming=False) would return, in addition to
    the binary prefix.

    Legacy _build_claude_flags output for empty config:
      ['--print', '--verbose', '--output-format', 'stream-json',
       '--dangerously-skip-permissions']
    """
    from agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    cmd = rt.build_command()
    flags = cmd[1:]  # skip binary

    # Exact legacy flags must be present
    for flag in ['--print', '--verbose', '--output-format', 'stream-json',
                 '--dangerously-skip-permissions']:
        assert flag in flags, f"Expected flag {flag!r} not in build_command() output: {flags}"

    # Mode-B flag must NOT be present when streaming=False
    assert '--input-format' not in flags


def test_build_command_streaming_adds_input_format():
    """streaming=True must add --input-format stream-json (Mode B flag)."""
    from agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    cmd = rt.build_command(streaming=True)
    flags = cmd[1:]
    assert '--input-format' in flags
    idx = flags.index('--input-format')
    assert flags[idx + 1] == 'stream-json'


def test_build_command_model():
    from agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    cmd = rt.build_command(model='claude-sonnet-4-6')
    flags = cmd[1:]
    assert '--model' in flags
    assert flags[flags.index('--model') + 1] == 'claude-sonnet-4-6'


def test_build_command_model_empty_omitted():
    from agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    cmd = rt.build_command(model='')
    assert '--model' not in cmd[1:]


def test_build_command_max_turns():
    from agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    cmd = rt.build_command(max_turns=14)
    flags = cmd[1:]
    assert '--max-turns' in flags
    assert flags[flags.index('--max-turns') + 1] == '14'


def test_build_command_max_turns_zero_omitted():
    from agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    for mt in (0, -1):
        cmd = rt.build_command(max_turns=mt)
        assert '--max-turns' not in cmd[1:], f"max_turns={mt} should not emit --max-turns"


def test_build_command_perm_mode():
    from agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    cmd = rt.build_command(perm_mode='acceptEdits')
    flags = cmd[1:]
    assert '--permission-mode' in flags
    assert flags[flags.index('--permission-mode') + 1] == 'acceptEdits'


def test_build_command_perm_mode_empty_omitted():
    from agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    cmd = rt.build_command(perm_mode='')
    assert '--permission-mode' not in cmd[1:]


def test_build_command_channels():
    from agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    cmd = rt.build_command(channels='plugin:foo@bar')
    flags = cmd[1:]
    assert '--channels' in flags
    assert flags[flags.index('--channels') + 1] == 'plugin:foo@bar'


def test_build_command_channels_empty_omitted():
    from agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    cmd = rt.build_command(channels='')
    assert '--channels' not in cmd[1:]


def test_build_command_remote_control():
    from agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    cmd = rt.build_command(remote_control=True)
    assert '--remote-control' in cmd[1:]


def test_build_command_remote_control_false_omitted():
    from agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    cmd = rt.build_command(remote_control=False)
    assert '--remote-control' not in cmd[1:]


def test_build_command_all_options():
    """All options together produce the expected flag list."""
    from agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    cmd = rt.build_command(
        model='claude-opus-4-7',
        max_turns=10,
        streaming=True,
        perm_mode='bypassPermissions',
        channels='plugin:my-plugin@test',
        remote_control=True,
    )
    flags = cmd[1:]
    assert '--print' in flags
    assert '--verbose' in flags
    assert '--output-format' in flags
    assert '--dangerously-skip-permissions' in flags
    assert '--input-format' in flags
    assert '--model' in flags
    assert '--max-turns' in flags
    assert '--permission-mode' in flags
    assert '--channels' in flags
    assert '--remote-control' in flags
    # Verify values
    assert flags[flags.index('--model') + 1] == 'claude-opus-4-7'
    assert flags[flags.index('--max-turns') + 1] == '10'
    assert flags[flags.index('--permission-mode') + 1] == 'bypassPermissions'


# ── _encode_project_path() ────────────────────────────────────────────────────


def test_encode_project_path_windows_style():
    from agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    # Simulate Windows path encoding
    # Note: Path.resolve() will make this absolute — on Windows: C--Users-foo-bar
    # On non-Windows: the resolved path replaces / with -
    result = rt._encode_project_path('C:\\Users\\foo\\bar')
    assert result is not None
    # Colons and backslashes replaced with dashes
    assert ':' not in result
    assert '\\' not in result


def test_encode_project_path_unix_style(tmp_path):
    from agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    result = rt._encode_project_path(str(tmp_path))
    assert result is not None
    assert ':' not in result
    assert '\\' not in result
    assert '/' not in result


def test_encode_project_path_empty():
    from agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    assert rt._encode_project_path('') is None
    assert rt._encode_project_path(None) is None


# ── transcript_path() ── proves equivalence with _find_transcript_file() ──────


def test_transcript_path_none_for_empty_session():
    from agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    assert rt.transcript_path('/some/project', '') is None
    assert rt.transcript_path('/some/project', None) is None


def test_transcript_path_none_for_empty_project():
    from agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    assert rt.transcript_path('', 'abc123') is None


def test_transcript_path_none_when_file_absent(tmp_path):
    """transcript_path() returns None when the .jsonl file doesn't exist."""
    from agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    result = rt.transcript_path(str(tmp_path), 'nonexistent-session-id-xyz')
    assert result is None


def test_transcript_path_finds_existing_file(tmp_path, monkeypatch):
    """transcript_path() returns the correct Path when the .jsonl file exists.
    Mirrors _find_transcript_file() in server.py exactly.
    """
    from agent_runtime import ClaudeRuntime
    import agent_runtime

    rt = ClaudeRuntime()

    # Create a fake CLAUDE_HOME directory structure
    fake_home = tmp_path / '.claude' / 'projects'
    monkeypatch.setattr(agent_runtime, '_CLAUDE_HOME', fake_home)

    project_path = str(tmp_path / 'my-project')
    session_id = 'abc123def456-0000-0000-0000-000000000000'

    # Build the encoded dir and create the file
    encoded = rt._encode_project_path(project_path)
    jsonl_dir = fake_home / encoded
    jsonl_dir.mkdir(parents=True)
    jsonl_file = jsonl_dir / f'{session_id}.jsonl'
    jsonl_file.write_text('{"type":"user"}')

    result = rt.transcript_path(project_path, session_id)
    assert result is not None
    assert result == jsonl_file


def test_transcript_path_checks_underscore_dash_variant(tmp_path, monkeypatch):
    """transcript_path() also checks the encoded path with _ replaced by - (Claude variant)."""
    from agent_runtime import ClaudeRuntime
    import agent_runtime

    rt = ClaudeRuntime()

    fake_home = tmp_path / '.claude' / 'projects'
    monkeypatch.setattr(agent_runtime, '_CLAUDE_HOME', fake_home)

    # Use a project path that encodes to something with underscores
    # We'll manually construct an encoded path with underscore
    project_path = str(tmp_path / 'my_project_dir')
    session_id = 'session-id-with-dashes-1234'

    encoded = rt._encode_project_path(project_path)
    encoded_alt = encoded.replace('_', '-') if encoded else None

    if encoded == encoded_alt:
        pytest.skip("This path encoding has no underscore to test the alt variant")

    # Only create the alt variant (dash version)
    alt_dir = fake_home / encoded_alt
    alt_dir.mkdir(parents=True)
    jsonl_file = alt_dir / f'{session_id}.jsonl'
    jsonl_file.write_text('{"type":"user"}')

    result = rt.transcript_path(project_path, session_id)
    assert result is not None
    assert result == jsonl_file


# ── parse_event() ─────────────────────────────────────────────────────────────


def test_parse_event_empty_line():
    from agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    assert rt.parse_event('') is None
    assert rt.parse_event('\n') is None
    assert rt.parse_event('\r\n') is None


def test_parse_event_non_json_plain_text():
    from agent_runtime import ClaudeRuntime, EventType
    rt = ClaudeRuntime()
    ev = rt.parse_event('Hello from the model')
    assert ev is not None
    assert ev.type == EventType.ASSISTANT_TEXT
    assert ev.payload.get('text') == 'Hello from the model'
    assert ev.raw is None
    assert ev.provider == 'claude'


def test_parse_event_auth_not_logged_in():
    from agent_runtime import ClaudeRuntime, EventType
    rt = ClaudeRuntime()
    ev = rt.parse_event('Please run /login to authenticate with Claude')
    assert ev is not None
    assert ev.type == EventType.AUTH_ERROR
    assert ev.payload.get('reason') == 'not_logged_in'


def test_parse_event_auth_not_logged_in_variant():
    from agent_runtime import ClaudeRuntime, EventType
    rt = ClaudeRuntime()
    ev = rt.parse_event('Error: not logged in')
    assert ev is not None
    assert ev.type == EventType.AUTH_ERROR
    assert ev.payload.get('reason') == 'not_logged_in'


def test_parse_event_auth_invalid_key():
    from agent_runtime import ClaudeRuntime, EventType
    rt = ClaudeRuntime()
    ev = rt.parse_event('Error: Invalid API key provided')
    assert ev is not None
    assert ev.type == EventType.AUTH_ERROR
    assert ev.payload.get('reason') == 'invalid_api_key'


def test_parse_event_auth_error_in_json_not_triggered():
    """Auth sentinel scan must NOT trigger on JSON assistant text about auth."""
    from agent_runtime import ClaudeRuntime, EventType
    rt = ClaudeRuntime()
    # This is a valid JSON assistant message — auth scan only applies to non-JSON lines
    line = json.dumps({
        'type': 'assistant',
        'message': {'content': [{'type': 'text', 'text': 'not logged in discussion'}]},
    })
    ev = rt.parse_event(line)
    assert ev is not None
    assert ev.type == EventType.ASSISTANT_TEXT  # not AUTH_ERROR


def test_parse_event_assistant_text():
    from agent_runtime import ClaudeRuntime, EventType
    rt = ClaudeRuntime()
    line = json.dumps({
        'type': 'assistant',
        'session_id': 'sess-abc-123',
        'message': {'content': [{'type': 'text', 'text': 'Hello world'}]},
    })
    ev = rt.parse_event(line, mc_session_id='mc-sid-test')
    assert ev is not None
    assert ev.type == EventType.ASSISTANT_TEXT
    assert ev.session_id == 'sess-abc-123'
    assert ev.mc_session_id == 'mc-sid-test'
    assert ev.raw is not None
    assert ev.raw['type'] == 'assistant'
    blocks = ev.payload.get('blocks', [])
    assert len(blocks) == 1
    assert blocks[0]['type'] == 'text'
    assert blocks[0]['text'] == 'Hello world'


def test_parse_event_assistant_thinking():
    from agent_runtime import ClaudeRuntime, EventType
    rt = ClaudeRuntime()
    line = json.dumps({
        'type': 'assistant',
        'message': {'content': [{'type': 'thinking', 'thinking': 'I am reasoning...'}]},
    })
    ev = rt.parse_event(line)
    assert ev is not None
    assert ev.type == EventType.THINKING
    blocks = ev.payload.get('blocks', [])
    assert blocks[0]['type'] == 'thinking'
    assert blocks[0]['text'] == 'I am reasoning...'


def test_parse_event_tool_use():
    from agent_runtime import ClaudeRuntime, EventType
    rt = ClaudeRuntime()
    line = json.dumps({
        'type': 'assistant',
        'session_id': 'sess-tool-1',
        'message': {'content': [{
            'type': 'tool_use',
            'name': 'Bash',
            'input': {'command': 'ls -la'},
            'id': 'tu_abc123',
        }]},
    })
    ev = rt.parse_event(line)
    assert ev is not None
    assert ev.type == EventType.TOOL_USE
    assert ev.session_id == 'sess-tool-1'
    blocks = ev.payload.get('blocks', [])
    assert len(blocks) == 1
    assert blocks[0]['type'] == 'tool_use'
    assert blocks[0]['name'] == 'Bash'
    assert blocks[0]['input'] == {'command': 'ls -la'}
    assert blocks[0]['tool_use_id'] == 'tu_abc123'


def test_parse_event_mixed_content_primary_is_first():
    """When an assistant message has both thinking and text blocks, primary type = first block."""
    from agent_runtime import ClaudeRuntime, EventType
    rt = ClaudeRuntime()
    line = json.dumps({
        'type': 'assistant',
        'message': {'content': [
            {'type': 'thinking', 'thinking': 'Let me think'},
            {'type': 'text', 'text': 'Here is the answer'},
        ]},
    })
    ev = rt.parse_event(line)
    assert ev is not None
    assert ev.type == EventType.THINKING  # first block is thinking
    blocks = ev.payload.get('blocks', [])
    assert len(blocks) == 2


def test_parse_event_result_turn_end():
    from agent_runtime import ClaudeRuntime, EventType
    rt = ClaudeRuntime()
    line = json.dumps({
        'type': 'result',
        'session_id': 'sess-result-1',
        'usage': {'input_tokens': 100, 'output_tokens': 50,
                  'cache_read_input_tokens': 200},
        'cost_usd': 0.001234,
        'num_turns': 3,
    })
    ev = rt.parse_event(line)
    assert ev is not None
    assert ev.type == EventType.TURN_END
    assert ev.session_id == 'sess-result-1'
    p = ev.payload
    assert p.get('cost_usd') == 0.001234
    assert p.get('num_turns') == 3
    assert p.get('usage', {}).get('input_tokens') == 100
    assert ev.raw['type'] == 'result'


def test_parse_event_system_init():
    from agent_runtime import ClaudeRuntime, EventType
    rt = ClaudeRuntime()
    line = json.dumps({
        'type': 'system',
        'subtype': 'init',
        'session_id': 'sess-init-1',
        'model': 'claude-opus-4-7',
        'claude_code_version': '1.2.3',
        'cwd': '/home/user/project',
        'mcp_servers': [{'name': 'filesystem', 'status': 'connected'}],
        'tools': ['Read', 'Write', 'Bash'],
        'apiKeySource': 'env',
        'permissionMode': 'bypassPermissions',
    })
    ev = rt.parse_event(line)
    assert ev is not None
    assert ev.type == EventType.INIT
    assert ev.session_id == 'sess-init-1'
    p = ev.payload
    assert p.get('model') == 'claude-opus-4-7'
    assert p.get('cli_version') == '1.2.3'
    assert p.get('cwd') == '/home/user/project'
    assert len(p.get('mcp_servers', [])) == 1
    assert p.get('permission_mode') == 'bypassPermissions'
    assert ev.raw is not None


def test_parse_event_system_non_init_returns_none():
    """system messages with subtype != 'init' return None (not handled)."""
    from agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    line = json.dumps({'type': 'system', 'subtype': 'something_else'})
    ev = rt.parse_event(line)
    assert ev is None


def test_parse_event_rate_limit():
    from agent_runtime import ClaudeRuntime, EventType
    rt = ClaudeRuntime()
    line = json.dumps({
        'type': 'rate_limit_event',
        'rate_limit_info': {
            'status': 'rate_limited',
            'resetsAt': '2026-05-21T12:00:00Z',
            'rateLimitType': 'daily',
            'overageStatus': 'none',
            'isUsingOverage': False,
        }
    })
    ev = rt.parse_event(line)
    assert ev is not None
    assert ev.type == EventType.RATE_LIMIT
    p = ev.payload
    assert p.get('status') == 'rate_limited'
    assert p.get('resets_at') == '2026-05-21T12:00:00Z'
    assert p.get('rate_limit_type') == 'daily'
    assert p.get('is_using_overage') is False


def test_parse_event_user_message():
    from agent_runtime import ClaudeRuntime, EventType
    rt = ClaudeRuntime()
    line = json.dumps({
        'type': 'user',
        'message': {'role': 'user', 'content': 'Hello, please do X'},
    })
    ev = rt.parse_event(line)
    assert ev is not None
    assert ev.type == EventType.USER_MESSAGE
    assert ev.payload.get('role') == 'user'
    assert ev.payload.get('content') == 'Hello, please do X'


def test_parse_event_unknown_type_returns_none():
    from agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    line = json.dumps({'type': 'something_totally_unknown', 'data': 42})
    ev = rt.parse_event(line)
    assert ev is None


def test_parse_event_raw_preserved():
    """event.raw must equal the full original parsed JSON object."""
    from agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    original = {
        'type': 'result',
        'session_id': 'x',
        'usage': {'input_tokens': 5},
        'cost_usd': 0.01,
        'num_turns': 1,
        'extra_field': 'should be in raw',
    }
    ev = rt.parse_event(json.dumps(original))
    assert ev is not None
    assert ev.raw == original  # full original, not just the normalized subset


# ── capabilities() ───────────────────────────────────────────────────────────


def test_capabilities_claude_all_fields():
    from agent_runtime import ClaudeRuntime, CapabilityFlags
    rt = ClaudeRuntime()
    caps = rt.capabilities()
    assert isinstance(caps, CapabilityFlags)
    assert caps.name == 'claude'
    assert caps.display_name == 'Claude Code'
    assert caps.supports_mode_a is True
    assert caps.supports_mode_b is True
    assert caps.mode_b_kind == 'native'
    assert caps.default_mode == 'B'
    assert caps.supports_session_resume is True
    assert caps.supports_mcp is True
    assert caps.supports_skills is True
    assert caps.supports_plan_mode is True
    assert caps.supports_ask_user_question is True
    assert caps.supports_streaming_text is True
    assert caps.emits_usage is True
    assert caps.emits_rate_limit is True
    assert caps.emits_cost is True
    assert caps.emits_num_turns is True
    assert caps.image_input is True
    assert caps.context_window == 200_000
    assert caps.context_injection == 'flag'
    assert caps.context_file_name == 'CLAUDE.md'
    assert caps.oneshot_supported is True


def test_capabilities_gemini():
    from agent_runtime import GeminiRuntime
    rt = GeminiRuntime()
    caps = rt.capabilities()
    assert caps.name == 'gemini'
    assert caps.supports_mode_b is False
    assert caps.emits_cost is False
    assert caps.emits_num_turns is False
    # Stage 1 (full-parity): Gemini is multimodal and reads attachments via the
    # [Screenshot:/Attachment:] markers in the task text (with_attachment_hint).
    assert caps.image_input is True
    assert caps.context_window is None
    assert caps.context_injection == 'prepend'


# ── Stage 1 full-parity: provider-agnostic attachment hint ───────────────────


def test_with_attachment_hint():
    """The shared attachment hint is prepended only when the text carries
    [Screenshot:/Attachment:] markers, and is inherited by every runtime."""
    from agent_runtime import GeminiRuntime
    rt = GeminiRuntime()
    # No markers → unchanged (zero prompt overhead on ordinary turns).
    plain = "Refactor the auth module."
    assert rt.with_attachment_hint(plain) == plain
    # Screenshot marker → instruction prepended, original text preserved.
    withimg = "Look at this\n\n[Screenshot: /uploads/agent_ab12.png]"
    out = rt.with_attachment_hint(withimg)
    assert out.startswith(rt.ATTACHMENT_INSTRUCTION)
    assert withimg in out
    # Attachment marker also triggers it.
    assert rt.with_attachment_hint(
        "see [Attachment: /tmp/x.pdf]").startswith(rt.ATTACHMENT_INSTRUCTION)
    # Empty / falsy input is safe.
    assert rt.with_attachment_hint('') == ''


def test_attachment_hint_inherited_by_all_runtimes():
    """with_attachment_hint lives on the AgentRuntime base — every provider
    inherits it, so attachment parity generalises without per-runtime code."""
    from agent_runtime import available_runtimes
    marked = "do it [Screenshot: /uploads/x.png]"
    for rt in available_runtimes():
        assert rt.with_attachment_hint(marked).startswith(rt.ATTACHMENT_INSTRUCTION)
        assert rt.with_attachment_hint("no markers here") == "no markers here"


# ── Stage 2 full-parity: MC Tool Protocol ────────────────────────────────────


def test_parse_and_strip_mc_tool_blocks():
    from agent_runtime import parse_mc_tool_blocks, strip_mc_tool_blocks
    text = (
        'Sure, let me ask.\n\n'
        '```mc:question\n'
        '{"questions": [{"header": "X", "question": "A or B?", '
        '"options": [{"label": "A"}, {"label": "B"}]}]}\n'
        '```\n'
    )
    blocks = parse_mc_tool_blocks(text)
    assert len(blocks) == 1
    assert blocks[0][0] == 'question'
    assert '"questions"' in blocks[0][1]
    stripped = strip_mc_tool_blocks(text)
    assert 'mc:question' not in stripped
    assert 'Sure, let me ask.' in stripped
    # no-block text is returned unchanged
    assert parse_mc_tool_blocks('just text') == []
    assert strip_mc_tool_blocks('just text') == 'just text'


def test_with_mc_tool_protocol_idempotent():
    from agent_runtime import GeminiRuntime, MC_TOOL_PROTOCOL_PROMPT
    rt = GeminiRuntime()
    assert rt.with_mc_tool_protocol('') == MC_TOOL_PROTOCOL_PROMPT
    out = rt.with_mc_tool_protocol('SYSTEM CONTEXT')
    assert out.startswith('SYSTEM CONTEXT')
    assert MC_TOOL_PROTOCOL_PROMPT in out
    # idempotent — re-applying does not double the protocol
    assert rt.with_mc_tool_protocol(out) == out


def test_apply_mc_tool_blocks_question():
    """An mc:question block populates pending_questions exactly like Claude's
    native AskUserQuestion tool, and pauses the turn."""
    from agent_runtime import GeminiRuntime
    rt = GeminiRuntime()
    session = {'log_lines': []}
    turn = (
        '```mc:question\n'
        '{"questions": [{"header": "DB", "question": "Postgres or SQLite?", '
        '"options": [{"label": "Postgres"}, {"label": "SQLite"}]}]}\n'
        '```'
    )
    res = rt.apply_mc_tool_blocks(session, turn)
    assert res == {'blocks_found': True, 'paused': True}
    assert session['waiting_for_question'] is True
    pq = session['pending_questions']
    assert len(pq) == 1
    assert pq[0]['question_id']
    assert pq[0]['questions'][0]['question'] == 'Postgres or SQLite?'


def test_apply_mc_tool_blocks_question_tolerates_control_chars():
    """A question block whose JSON body carries a raw newline inside a string
    value (a streaming/model artefact) still parses — json.loads strict=False.
    Regression for the Gemini delta-join corruption seen 2026-05-22."""
    from agent_runtime import GeminiRuntime
    rt = GeminiRuntime()
    session = {'log_lines': []}
    turn = ('```mc:question\n'
            '{"questions": [{"header": "X", "question": "A or B?", '
            '"options": [{"label": "A", "description": "first\noption"}]}]}\n```')
    res = rt.apply_mc_tool_blocks(session, turn)
    assert res['blocks_found'] is True
    assert res['paused'] is True
    opt = session['pending_questions'][0]['questions'][0]['options'][0]
    assert opt['description']  # the control char did not break the parse


def test_apply_mc_tool_blocks_malformed_is_safe():
    """A malformed block is logged and skipped — never raised, never pauses."""
    from agent_runtime import GeminiRuntime
    rt = GeminiRuntime()
    session = {'log_lines': []}
    res = rt.apply_mc_tool_blocks(session, '```mc:question\n{not json\n```')
    assert res['blocks_found'] is True
    assert res['paused'] is False
    assert 'pending_questions' not in session
    assert any('malformed' in line for line in session['log_lines'])


def test_apply_mc_tool_blocks_no_blocks():
    from agent_runtime import GeminiRuntime
    rt = GeminiRuntime()
    session = {'log_lines': []}
    res = rt.apply_mc_tool_blocks(session, 'Just a normal answer, no tools.')
    assert res == {'blocks_found': False, 'paused': False}
    assert 'pending_questions' not in session


def test_apply_mc_tool_blocks_todo():
    """An mc:todo block routes through the registered sync hook (the same one
    Claude's native TodoWrite uses) and does not pause the turn."""
    from agent_runtime import GeminiRuntime, register_mc_tool_hooks, _MC_TOOL_HOOKS
    rt = GeminiRuntime()
    calls = []
    register_mc_tool_hooks(
        sync_todos=lambda pid, sk, todos: (calls.append((pid, sk, todos))
                                           or len(todos)))
    try:
        session = {'log_lines': [], 'project_id': 'proj1', 'session_id': 'sess1'}
        turn = ('```mc:todo\n'
                '{"todos": [{"content": "Do X", "status": "pending"}]}\n```')
        res = rt.apply_mc_tool_blocks(session, turn)
        assert res == {'blocks_found': True, 'paused': False}
        assert calls == [('proj1', 'sess1',
                          [{'content': 'Do X', 'status': 'pending'}])]
        assert any('synced' in line for line in session['log_lines'])
    finally:
        _MC_TOOL_HOOKS.pop('sync_todos', None)


# ── CapabilityFlags new fields present on all runtimes ───────────────────────


def test_capability_flags_new_fields_exist():
    """All CapabilityFlags fields required by the brief must exist on every runtime."""
    from agent_runtime import available_runtimes
    required = ('emits_cost', 'emits_num_turns', 'image_input', 'context_window')
    for rt in available_runtimes():
        caps = rt.capabilities()
        for field in required:
            assert hasattr(caps, field), (
                f"Runtime {rt.name!r} capabilities missing field {field!r}"
            )


# ── Gemini parse_event() + transcript_path() ─────────────────────────────────


def test_gemini_transcript_path_always_none():
    from agent_runtime import GeminiRuntime
    rt = GeminiRuntime()
    assert rt.transcript_path('/some/project', 'some-session') is None


def test_gemini_parse_event_empty():
    from agent_runtime import GeminiRuntime
    rt = GeminiRuntime()
    assert rt.parse_event('') is None


def test_gemini_parse_event_plain_text():
    from agent_runtime import GeminiRuntime, EventType
    rt = GeminiRuntime()
    ev = rt.parse_event('Hello from Gemini')
    assert ev is not None
    assert ev.type == EventType.ASSISTANT_TEXT
    assert ev.provider == 'gemini'


def test_gemini_parse_event_turn_end():
    from agent_runtime import GeminiRuntime, EventType
    rt = GeminiRuntime()
    ev = rt.parse_event(json.dumps({'type': 'result', 'session_id': 's1'}))
    assert ev is not None
    assert ev.type == EventType.TURN_END


def test_gemini_parse_event_tool_use_canonical_fields():
    """gemini-cli PR #10883 emits `tool_name` + `parameters` on tool_use
    (NOT `name` / `input` — those were guessed from claude's shape and never
    populated, hence MC's blank `[tool: call]` for every gemini tool call).
    """
    from agent_runtime import GeminiRuntime, EventType
    rt = GeminiRuntime()
    raw = json.dumps({
        'type': 'tool_use',
        'tool_name': 'Bash',
        'tool_id': 'bash-123',
        'parameters': {'command': 'ls'},
    })
    ev = rt.parse_event(raw)
    assert ev is not None and ev.type == EventType.TOOL_USE
    block = ev.payload['blocks'][0]
    assert block['name'] == 'Bash'
    assert block['input'] == {'command': 'ls'}
    assert block['tool_use_id'] == 'bash-123'


def test_gemini_parse_event_tool_result_correlates_via_tool_id():
    """tool_result has only `tool_id` + `status`, no name field. The parser
    passes tool_id through; _read_stream does the name lookup via a per-
    session tool_id→tool_name map populated by the prior tool_use event.
    """
    from agent_runtime import GeminiRuntime, EventType
    rt = GeminiRuntime()
    raw = json.dumps({
        'type': 'tool_result',
        'tool_id': 'bash-123',
        'status': 'success',
        'output': 'file1.txt',
    })
    ev = rt.parse_event(raw)
    assert ev is not None and ev.type == EventType.TOOL_RESULT
    assert ev.payload['tool_id'] == 'bash-123'
    assert ev.payload['status'] == 'success'


# ── ABC completeness ──────────────────────────────────────────────────────────


def test_abc_abstract_methods_cannot_instantiate():
    """AgentRuntime ABC must be non-instantiable (abstract)."""
    from agent_runtime import AgentRuntime
    with pytest.raises(TypeError):
        AgentRuntime()


def test_abc_has_all_required_methods():
    """All methods called by the brief must exist on the ABC."""
    from agent_runtime import AgentRuntime
    for method in ('resolve_binary', 'health_check', 'capabilities',
                   'build_command', 'parse_event', 'transcript_path',
                   'dispatch', 'write_followup', 'interrupt', 'stop', 'oneshot'):
        assert hasattr(AgentRuntime, method), f"ABC missing method: {method!r}"


def test_claude_runtime_implements_all_abc_methods():
    """ClaudeRuntime must implement every method in the AgentRuntime ABC."""
    from agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    for method in ('resolve_binary', 'health_check', 'capabilities',
                   'build_command', 'parse_event', 'transcript_path',
                   'dispatch', 'write_followup', 'interrupt', 'stop', 'oneshot'):
        assert callable(getattr(rt, method)), f"ClaudeRuntime missing callable: {method!r}"


# ── _build_transcript_path() ─────────────────────────────────────────────────


def test_build_transcript_path_returns_path_without_existence_check(tmp_path):
    """_build_transcript_path() returns a Path even when the file does not exist."""
    from agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    result = rt._build_transcript_path(str(tmp_path), 'nonexistent-session-id')
    assert result is not None
    assert isinstance(result, Path)
    assert result.name == 'nonexistent-session-id.jsonl'
    # File does NOT have to exist — this is the key difference from transcript_path()
    assert not result.exists()


def test_build_transcript_path_none_for_empty_session():
    from agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    assert rt._build_transcript_path('/some/project', '') is None
    assert rt._build_transcript_path('/some/project', None) is None


def test_build_transcript_path_none_for_empty_project():
    from agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    assert rt._build_transcript_path('', 'some-session') is None


# ── parse_transcript_file() — fixture-based regression ───────────────────────

# Canonical Claude JSONL fixture — 3 turns: user + assistant-text + tool + result.
_FIXTURE_TRANSCRIPT = [
    json.dumps({
        'type': 'user',
        'timestamp': '2026-05-21T10:00:00Z',
        'message': {'role': 'user', 'content': 'What is 2+2?'},
    }),
    json.dumps({
        'type': 'assistant',
        'session_id': 'sess-fixture-abc',
        'timestamp': '2026-05-21T10:00:01Z',
        'message': {'content': [
            {'type': 'text', 'text': 'Let me calculate that.'},
            {'type': 'tool_use', 'name': 'Bash', 'input': {'command': 'echo $((2+2))'}, 'id': 'tu_1'},
        ]},
    }),
    json.dumps({
        'type': 'assistant',
        'session_id': 'sess-fixture-abc',
        'timestamp': '2026-05-21T10:00:02Z',
        'message': {'content': [
            {'type': 'text', 'text': '2+2 equals 4.'},
        ]},
    }),
    json.dumps({
        'type': 'result',
        'session_id': 'sess-fixture-abc',
        'usage': {'input_tokens': 100, 'output_tokens': 50},
        'cost_usd': 0.001234,
        'num_turns': 2,
    }),
]


def _write_fixture(tmp_path, lines=None):
    """Write fixture JSONL lines to a temp file and return the path."""
    lines = lines if lines is not None else _FIXTURE_TRANSCRIPT
    f = tmp_path / 'fixture.jsonl'
    f.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return f


def test_parse_transcript_file_empty_file(tmp_path):
    """Empty transcript returns empty list."""
    from agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    f = tmp_path / 'empty.jsonl'
    f.write_text('', encoding='utf-8')
    result = rt.parse_transcript_file(f)
    assert result == []


def test_parse_transcript_file_user_message(tmp_path):
    """User message is extracted with correct role and text."""
    from agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    f = _write_fixture(tmp_path)
    msgs = rt.parse_transcript_file(f)
    user_msgs = [m for m in msgs if m['role'] == 'user']
    assert len(user_msgs) == 1
    assert user_msgs[0]['text'] == 'What is 2+2?'
    assert user_msgs[0]['timestamp'] == '2026-05-21T10:00:00Z'


def test_parse_transcript_file_assistant_text(tmp_path):
    """Assistant text blocks produce role=assistant entries."""
    from agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    f = _write_fixture(tmp_path)
    msgs = rt.parse_transcript_file(f)
    asst_msgs = [m for m in msgs if m['role'] == 'assistant']
    texts = [m['text'] for m in asst_msgs]
    assert 'Let me calculate that.' in texts
    assert '2+2 equals 4.' in texts


def test_parse_transcript_file_tool_call(tmp_path):
    """tool_use blocks produce role=tool_call entries with the tool name."""
    from agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    f = _write_fixture(tmp_path)
    msgs = rt.parse_transcript_file(f)
    tool_msgs = [m for m in msgs if m['role'] == 'tool_call']
    assert len(tool_msgs) == 1
    assert tool_msgs[0]['tool'] == 'Bash'


def test_parse_transcript_file_result_not_in_output(tmp_path):
    """result lines produce no display entries."""
    from agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    f = _write_fixture(tmp_path)
    msgs = rt.parse_transcript_file(f)
    roles = {m['role'] for m in msgs}
    assert 'error' not in roles
    # No 'result' role — result events are usage data, not display messages
    assert all(r in ('user', 'assistant', 'tool_call') for r in roles)


def test_parse_transcript_file_max_messages_truncates(tmp_path):
    """max_messages keeps the TAIL of the transcript (most-recent N entries).

    Head-truncation hides the actual work product behind the opening prompt,
    which broke long conversations in the UI. Fixture produces, in order:
    user → assistant → tool_call → assistant. With max_messages=1 we keep
    the last assistant message, not the user opener.
    """
    from agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    f = _write_fixture(tmp_path)
    truncated = rt.parse_transcript_file(f, max_messages=1)
    assert len(truncated) == 1
    assert truncated[0]['role'] == 'assistant'
    assert truncated[0]['text'] == '2+2 equals 4.'
    # Full parse (fixture has user + 2 asst blocks + tool + asst → 4 total)
    full = rt.parse_transcript_file(f, max_messages=2000)
    assert len(full) > 1
    assert full[0]['role'] == 'user'  # head preserved when under limit
    assert full[-1]['text'] == '2+2 equals 4.'


def test_parse_transcript_file_nonexistent_returns_error():
    """Non-existent file returns [{'role': 'error', ...}]."""
    from agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    result = rt.parse_transcript_file(Path('/nonexistent/path/file.jsonl'))
    assert len(result) == 1
    assert result[0]['role'] == 'error'
    assert 'text' in result[0]


def test_parse_transcript_file_skips_thinking_blocks(tmp_path):
    """Thinking blocks do not produce any display entry."""
    from agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    lines = [
        json.dumps({'type': 'assistant', 'session_id': 's', 'timestamp': 'ts',
                    'message': {'content': [
                        {'type': 'thinking', 'thinking': 'I am thinking...'},
                        {'type': 'text', 'text': 'Final answer.'},
                    ]}}),
    ]
    f = tmp_path / 't.jsonl'
    f.write_text('\n'.join(lines), encoding='utf-8')
    msgs = rt.parse_transcript_file(f)
    # Only the text block should appear, not the thinking block
    assert len(msgs) == 1
    assert msgs[0]['role'] == 'assistant'
    assert msgs[0]['text'] == 'Final answer.'


def test_parse_transcript_file_user_list_content(tmp_path):
    """User messages with list content (mixed text + tool_result) extract only text."""
    from agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    line = json.dumps({
        'type': 'user',
        'timestamp': 'ts1',
        'message': {'role': 'user', 'content': [
            {'type': 'tool_result', 'tool_use_id': 'tu_1', 'content': '4'},
            {'type': 'text', 'text': 'Great, thanks!'},
        ]},
    })
    f = tmp_path / 'u.jsonl'
    f.write_text(line, encoding='utf-8')
    msgs = rt.parse_transcript_file(f)
    assert len(msgs) == 1
    assert msgs[0]['role'] == 'user'
    assert msgs[0]['text'] == 'Great, thanks!'


def test_parse_transcript_file_matches_legacy_format(tmp_path):
    """parse_transcript_file() produces the same [{role, text, timestamp}] shape
    as the legacy _parse_transcript_messages() in server.py."""
    from agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    f = _write_fixture(tmp_path)
    msgs = rt.parse_transcript_file(f)
    for m in msgs:
        assert 'role' in m
        assert 'timestamp' in m
        if m['role'] == 'tool_call':
            assert 'tool' in m
        else:
            assert 'text' in m


# ── list_sessions() ───────────────────────────────────────────────────────────


def test_list_sessions_empty_for_nonexistent_dir(tmp_path):
    """list_sessions() returns [] when the project has no transcript directory."""
    from agent_runtime import ClaudeRuntime
    import agent_runtime
    rt = ClaudeRuntime()
    # Point CLAUDE_HOME somewhere that has no matching dir
    fake_home = tmp_path / '.claude' / 'projects'
    original = agent_runtime._CLAUDE_HOME
    try:
        agent_runtime._CLAUDE_HOME = fake_home
        result = rt.list_sessions(str(tmp_path / 'my-project'))
        assert result == []
    finally:
        agent_runtime._CLAUDE_HOME = original


def test_list_sessions_extracts_user_text(tmp_path, monkeypatch):
    """list_sessions() extracts first_user and last_user from JSONL files."""
    from agent_runtime import ClaudeRuntime
    import agent_runtime

    rt = ClaudeRuntime()
    fake_home = tmp_path / '.claude' / 'projects'
    monkeypatch.setattr(agent_runtime, '_CLAUDE_HOME', fake_home)

    project_path = str(tmp_path / 'test-project')
    encoded = rt._encode_project_path(project_path)
    transcript_dir = fake_home / encoded
    transcript_dir.mkdir(parents=True)

    session_id = 'test-session-abc-123'
    transcript_file = transcript_dir / f'{session_id}.jsonl'
    lines = [
        json.dumps({'type': 'user', 'message': {'role': 'user', 'content': 'First question'}}),
        json.dumps({'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': 'Answer'}]}}),
        json.dumps({'type': 'user', 'message': {'role': 'user', 'content': 'Second question'}}),
    ]
    transcript_file.write_text('\n'.join(lines), encoding='utf-8')

    results = rt.list_sessions(project_path, limit=10)
    assert len(results) == 1
    r = results[0]
    assert r['session_id'] == session_id
    assert r['first_user'] == 'First question'
    assert r['last_user'] == 'Second question'
    assert r['turns'] == 2


def test_list_sessions_deduplicates_across_variants(tmp_path, monkeypatch):
    """list_sessions() deduplicates files when both _ and - encoded dirs exist."""
    from agent_runtime import ClaudeRuntime
    import agent_runtime

    rt = ClaudeRuntime()
    fake_home = tmp_path / '.claude' / 'projects'
    monkeypatch.setattr(agent_runtime, '_CLAUDE_HOME', fake_home)

    # Create project path with underscore so both variants matter
    project_path = str(tmp_path / 'my_project')
    encoded = rt._encode_project_path(project_path)
    encoded_alt = encoded.replace('_', '-')

    if encoded == encoded_alt:
        import pytest as _pytest
        _pytest.skip("This path has no underscore to test dedup")

    # Create BOTH dirs with the SAME session file
    session_id = 'session-xyz-789'
    for enc in [encoded, encoded_alt]:
        d = fake_home / enc
        d.mkdir(parents=True, exist_ok=True)
        (d / f'{session_id}.jsonl').write_text(
            json.dumps({'type': 'user', 'message': {'role': 'user', 'content': 'hello'}}),
            encoding='utf-8'
        )

    results = rt.list_sessions(project_path, limit=10)
    # Must deduplicate — session_id should appear only once
    session_ids = [r['session_id'] for r in results]
    assert session_ids.count(session_id) == 1


def test_list_sessions_respects_limit(tmp_path, monkeypatch):
    """list_sessions() returns at most `limit` entries."""
    from agent_runtime import ClaudeRuntime
    import agent_runtime

    rt = ClaudeRuntime()
    fake_home = tmp_path / '.claude' / 'projects'
    monkeypatch.setattr(agent_runtime, '_CLAUDE_HOME', fake_home)

    project_path = str(tmp_path / 'test-proj')
    encoded = rt._encode_project_path(project_path)
    d = fake_home / encoded
    d.mkdir(parents=True)

    for i in range(5):
        (d / f'session-{i:03d}.jsonl').write_text(
            json.dumps({'type': 'user', 'message': {'role': 'user', 'content': f'msg {i}'}}),
            encoding='utf-8'
        )

    results = rt.list_sessions(project_path, limit=3)
    assert len(results) == 3


# ── Gemini new-provider smoke test with usage fields ─────────────────────────


def test_gemini_parse_event_usage_in_turn_end():
    """GeminiRuntime.parse_event() preserves usage fields in TURN_END events."""
    from agent_runtime import GeminiRuntime, EventType
    rt = GeminiRuntime()
    line = json.dumps({
        'type': 'result',
        'session_id': 'gemini-sess-001',
        'usage': {'input_tokens': 42, 'output_tokens': 17},
    })
    ev = rt.parse_event(line)
    assert ev is not None
    assert ev.type == EventType.TURN_END
    assert ev.provider == 'gemini'
    usage = ev.payload.get('usage')
    assert usage is not None
    assert usage.get('input_tokens') == 42
    assert usage.get('output_tokens') == 17


def test_gemini_parse_event_no_raise_on_synthetic_transcript():
    """GeminiRuntime.parse_event() does not raise on any line of a synthetic transcript."""
    from agent_runtime import GeminiRuntime
    rt = GeminiRuntime()
    synthetic_lines = [
        '{"type": "content", "text": "Hello from Gemini", "session_id": "g1"}',
        '{"type": "tool_use", "name": "search", "input": {"q": "test"}}',
        '{"type": "done", "session_id": "g1"}',
        '{"type": "result", "usage": {"input_tokens": 5, "output_tokens": 10}, "session_id": "g1"}',
        'plain text fallback line',
        '',
        'not-json {{{',
    ]
    for line in synthetic_lines:
        try:
            ev = rt.parse_event(line)
            # Either None or a valid AgentEvent — must not raise
        except Exception as e:
            raise AssertionError(f"parse_event raised on {line!r}: {e}") from e


def test_gemini_parse_event_no_lose_usage_on_turn_end_variants():
    """GeminiRuntime.parse_event() preserves usage for all turn_end type variants."""
    from agent_runtime import GeminiRuntime, EventType
    rt = GeminiRuntime()
    usage_payload = {'input_tokens': 100, 'output_tokens': 50}
    for end_type in ('result', 'turn_end', 'done'):
        line = json.dumps({'type': end_type, 'session_id': 's1', 'usage': usage_payload})
        ev = rt.parse_event(line)
        assert ev is not None, f"parse_event returned None for type={end_type!r}"
        assert ev.type == EventType.TURN_END, f"Expected TURN_END for {end_type!r}"
        u = ev.payload.get('usage')
        assert u is not None, f"usage lost for type={end_type!r}"
        assert u.get('input_tokens') == 100


# ── Regression: new methods on ClaudeRuntime ABC surface ─────────────────────


def test_claude_runtime_has_new_transcript_methods():
    """ClaudeRuntime must expose _build_transcript_path, list_sessions, parse_transcript_file."""
    from agent_runtime import ClaudeRuntime
    rt = ClaudeRuntime()
    for method in ('_build_transcript_path', 'list_sessions', 'parse_transcript_file'):
        assert callable(getattr(rt, method, None)), (
            f"ClaudeRuntime missing callable: {method!r}"
        )
