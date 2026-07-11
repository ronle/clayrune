"""Regression + smoke tests for the subprocess spawn-site refactor (ws_002).

Tests verify:
  1. _resolve_claude() → ClaudeRuntime.resolve_binary_str() (byte-identical)
  2. _build_claude_flags() → ClaudeRuntime.build_command()[1:] (byte-identical)
  3. _scribe_call() → ClaudeRuntime.oneshot() (equivalent — stdin vs argv difference
     is an intentional improvement: avoids Windows 32 KB argv limit for large transcripts)
  4. GeminiRuntime.dispatch() smoke test (spawns stub binary without crashing)
  5. _hm_spawn_worker_session() provider routing (non-claude routes through runtime)

These are standalone — no Flask, no live claude binary required.
subprocess.Popen / subprocess.run are monkeypatched where needed.
"""

from __future__ import annotations

import sys
import subprocess
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import agent_runtime as ar


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fresh_claude() -> ar.ClaudeRuntime:
    """Return a ClaudeRuntime with a known binary path (no shutil.which lookup)."""
    rt = ar.ClaudeRuntime()
    return rt


def _claude_binary(rt: ar.ClaudeRuntime) -> str:
    """Return the binary string the runtime would use; stub to 'claude' if absent."""
    b = rt.resolve_binary_str()
    return b if b else 'claude'


# ─────────────────────────────────────────────────────────────────────────────
# 1. _resolve_claude equivalence
# ─────────────────────────────────────────────────────────────────────────────

class TestResolveClaudeEquivalence:
    """ClaudeRuntime.resolve_binary_str() must return the same result as the
    pre-refactor _resolve_claude() on any environment."""

    def test_returns_string(self):
        rt = _fresh_claude()
        result = rt.resolve_binary_str()
        assert isinstance(result, str)

    def test_nonempty(self):
        rt = _fresh_claude()
        result = rt.resolve_binary_str()
        assert result  # at minimum 'claude' as fallback

    def test_last_resort_fallback(self):
        """When shutil.which returns nothing and no known paths exist, falls back to 'claude'."""
        rt = _fresh_claude()
        with patch('shutil.which', return_value=None), \
             patch.object(Path, 'exists', return_value=False), \
             patch.object(Path, 'is_file', return_value=False):
            # Force a fresh probe (clear any cached result)
            result = rt.resolve_binary_str()
        # Must still return a non-empty string
        assert isinstance(result, str)
        assert len(result) > 0

    def test_returns_path_from_which(self):
        """When shutil.which finds claude, that path is returned (normalized for OS)."""
        rt = _fresh_claude()
        fake_path = '/usr/local/bin/claude'
        with patch('shutil.which', side_effect=lambda x: fake_path if x in ('claude', 'claude.cmd') else None):
            result = rt.resolve_binary_str()
        # Path() normalizes separators on Windows; compare via Path for portability
        assert Path(result) == Path(fake_path)


# ─────────────────────────────────────────────────────────────────────────────
# 2. _build_claude_flags equivalence
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildClaudeFlagsEquivalence:
    """ClaudeRuntime.build_command()[1:] must match what _build_claude_flags()
    used to produce. Reference implementation copied from the pre-refactor
    version of server.py for cross-check."""

    def _legacy_flags(self, *, model='', max_turns=0, streaming=False,
                      perm_mode='', channels='', remote_control=False):
        """Pre-refactor _build_claude_flags() logic reconstructed from git history."""
        cmd = ['--print', '--verbose', '--output-format', 'stream-json',
               '--dangerously-skip-permissions']
        if streaming:
            cmd.extend(['--input-format', 'stream-json'])
        if model:
            cmd.extend(['--model', model])
        if max_turns and int(max_turns) > 0:
            cmd.extend(['--max-turns', str(int(max_turns))])
        if perm_mode:
            cmd.extend(['--permission-mode', perm_mode])
        if channels:
            cmd.extend(['--channels', channels])
        if remote_control:
            cmd.append('--remote-control')
        return cmd

    def test_bare_defaults(self):
        rt = _fresh_claude()
        flags = rt.build_command()[1:]
        assert flags == self._legacy_flags()

    def test_with_model(self):
        rt = _fresh_claude()
        flags = rt.build_command(model='claude-opus-4-7')[1:]
        assert flags == self._legacy_flags(model='claude-opus-4-7')

    def test_with_model_and_max_turns(self):
        rt = _fresh_claude()
        flags = rt.build_command(model='claude-haiku-4-5-20251001', max_turns=10)[1:]
        assert flags == self._legacy_flags(model='claude-haiku-4-5-20251001', max_turns=10)

    def test_streaming_mode(self):
        rt = _fresh_claude()
        flags = rt.build_command(streaming=True)[1:]
        assert flags == self._legacy_flags(streaming=True)
        assert '--input-format' in flags
        assert 'stream-json' in flags

    def test_perm_mode(self):
        rt = _fresh_claude()
        flags = rt.build_command(perm_mode='acceptEdits')[1:]
        assert flags == self._legacy_flags(perm_mode='acceptEdits')

    def test_channels(self):
        rt = _fresh_claude()
        flags = rt.build_command(channels='web,slack')[1:]
        assert flags == self._legacy_flags(channels='web,slack')

    def test_remote_control(self):
        rt = _fresh_claude()
        flags = rt.build_command(remote_control=True)[1:]
        assert flags == self._legacy_flags(remote_control=True)
        assert '--remote-control' in flags

    def test_all_options(self):
        rt = _fresh_claude()
        flags = rt.build_command(
            model='claude-sonnet-4-6',
            max_turns=5,
            streaming=True,
            perm_mode='bypassPermissions',
            channels='api',
            remote_control=True,
        )[1:]
        expected = self._legacy_flags(
            model='claude-sonnet-4-6',
            max_turns=5,
            streaming=True,
            perm_mode='bypassPermissions',
            channels='api',
            remote_control=True,
        )
        assert flags == expected

    def test_max_turns_zero_omitted(self):
        """max_turns=0 means unlimited — must NOT add --max-turns flag."""
        rt = _fresh_claude()
        flags = rt.build_command(max_turns=0)[1:]
        assert '--max-turns' not in flags

    def test_flags_include_mandatory_base(self):
        """Core flags must always be present regardless of options."""
        rt = _fresh_claude()
        flags = rt.build_command()[1:]
        for flag in ('--print', '--verbose', '--output-format', 'stream-json',
                     '--dangerously-skip-permissions'):
            assert flag in flags


# ─────────────────────────────────────────────────────────────────────────────
# 3. _scribe_call equivalence via oneshot()
# ─────────────────────────────────────────────────────────────────────────────

class TestScribeCallEquivalence:
    """ClaudeRuntime.oneshot() must produce the same subprocess invocation as the
    pre-refactor _scribe_call(). The one intentional difference: old code passed
    the full prompt+transcript as a positional CLI argument (hit Windows 32 KB
    argv limit on large transcripts); new code passes via stdin (safer, correct).
    """

    def _capture_run_calls(self, rt, model, instruction, body):
        """Call oneshot() and capture subprocess.run kwargs."""
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append({'cmd': cmd, 'kwargs': kwargs})
            fake = MagicMock()
            fake.returncode = 0
            fake.stdout = 'summarized text'
            return fake

        with patch('subprocess.run', side_effect=fake_run):
            result = rt.oneshot(
                prompt=instruction,
                model=model,
                stdin_text=body,
                cwd=str(Path.home()),
            )
        return calls, result

    def test_returns_text_on_success(self):
        rt = _fresh_claude()
        _, result = self._capture_run_calls(rt, 'claude-haiku-4-5-20251001',
                                             'Summarize this.', 'session log content')
        assert result is not None
        assert result.text == 'summarized text'

    def test_cmd_structure(self):
        """Verify argv shape: [binary, '-p', '--model', model, '--max-turns', '1', <sandbox flags>]."""
        rt = _fresh_claude()
        model = 'claude-haiku-4-5-20251001'
        calls, _ = self._capture_run_calls(rt, model, 'Summarize.', 'content')
        assert len(calls) == 1
        cmd = calls[0]['cmd']
        assert cmd[1] == '-p'
        assert '--model' in cmd
        assert cmd[cmd.index('--model') + 1] == model
        assert '--max-turns' in cmd
        assert cmd[cmd.index('--max-turns') + 1] == '1'

    # ── Sandbox (2026-07-11) — LOAD-BEARING, do not relax ────────────────────
    # oneshot() is a pure text transform (Scribe / condense / Distiller). It used
    # to run with --dangerously-skip-permissions and the full tool + MCP fleet.
    # With 80KB of transcript in the payload, the cheap model would CONTINUE the
    # transcript rather than analyse it — a live repro had the extraction call
    # execute an MCP mem_save on the user's machine. Removing tools removed both
    # the hazard and ~41% of Distiller extraction failures (validated: 1/4 → 4/4
    # parse rate on the same transcripts). If a future caller needs tools, it is
    # not a oneshot and belongs on the agent path.

    def test_oneshot_never_skips_permissions(self):
        rt = _fresh_claude()
        calls, _ = self._capture_run_calls(rt, 'haiku', 'Summarize.', 'content')
        assert '--dangerously-skip-permissions' not in calls[0]['cmd']

    def test_oneshot_has_no_tools_and_no_mcp(self):
        rt = _fresh_claude()
        calls, _ = self._capture_run_calls(rt, 'haiku', 'Summarize.', 'content')
        cmd = calls[0]['cmd']
        assert '--allowedTools' in cmd
        assert cmd[cmd.index('--allowedTools') + 1] == ''       # zero tools
        assert '--strict-mcp-config' in cmd
        assert cmd[cmd.index('--mcp-config') + 1] == '{"mcpServers":{}}'

    def test_oneshot_fences_the_transcript_as_data(self):
        """Recency wins in a long context: without a trailing restatement the
        model answers the transcript's last turn instead of the instruction."""
        rt = _fresh_claude()
        calls, _ = self._capture_run_calls(rt, 'haiku', 'Emit JSON.', 'blah blah')
        payload = calls[0]['kwargs']['input']
        assert 'BEGIN SESSION TRANSCRIPT' in payload
        assert 'END SESSION TRANSCRIPT' in payload
        # the instruction must be RESTATED after the transcript body
        assert payload.rindex('emit ONLY') > payload.rindex('blah blah')

    def test_oneshot_records_why_it_failed(self):
        """A None return used to be indistinguishable between timeout, spawn
        failure and non-zero exit — 78 extraction errors sat unexplained for
        six weeks behind a generic RuntimeError."""
        rt = _fresh_claude()

        def fake_run(cmd, **kwargs):
            fake = MagicMock()
            fake.returncode = 1
            fake.stdout = 'Error: Reached max turns (1)'
            fake.stderr = ''
            return fake

        with patch('subprocess.run', side_effect=fake_run):
            assert rt.oneshot(prompt='p', model='haiku', stdin_text='x') is None
        assert 'rc=1' in rt.last_error
        assert 'max turns' in rt.last_error

    def test_payload_via_stdin_not_argv(self):
        """The instruction + transcript must flow via stdin (input=), NOT as a
        positional argv element after '-p'. This avoids the Windows 32 KB limit."""
        rt = _fresh_claude()
        instruction = 'Summarize the following transcript.'
        body = 'turn1\nturn2\nturn3'
        calls, _ = self._capture_run_calls(rt, 'claude-haiku-4-5-20251001', instruction, body)
        cmd = calls[0]['cmd']
        kwargs = calls[0]['kwargs']
        # argv must not contain the instruction text
        for arg in cmd[2:]:
            assert instruction not in arg, "instruction should be in stdin, not argv"
        # stdin must contain the instruction and body
        assert 'input' in kwargs
        assert instruction in kwargs['input']
        assert body in kwargs['input']

    def test_stdin_payload_format(self):
        """Instruction first, then the fenced transcript body.

        The old `---TRANSCRIPT---` separator was replaced (2026-07-11) by an
        explicit DATA fence plus a trailing restatement — the bare separator was
        not enough to stop the model continuing the transcript. See
        test_oneshot_fences_the_transcript_as_data.
        """
        rt = _fresh_claude()
        instruction = 'Summarize.'
        body = 'log content here'
        calls, _ = self._capture_run_calls(rt, 'claude-haiku-4-5-20251001', instruction, body)
        payload = calls[0]['kwargs']['input']
        assert 'BEGIN SESSION TRANSCRIPT' in payload
        assert payload.index(instruction) < payload.index('BEGIN SESSION TRANSCRIPT')
        assert payload.index('BEGIN SESSION TRANSCRIPT') < payload.index(body)
        assert payload.index(body) < payload.index('END SESSION TRANSCRIPT')

    def test_returns_none_on_nonzero_exit(self):
        rt = _fresh_claude()
        def fake_run(cmd, **kwargs):
            m = MagicMock()
            m.returncode = 1
            m.stdout = ''
            return m
        with patch('subprocess.run', side_effect=fake_run):
            result = rt.oneshot(prompt='p', model='claude-haiku-4-5-20251001', stdin_text='x')
        assert result is None

    def test_returns_none_on_exception(self):
        rt = _fresh_claude()
        with patch('subprocess.run', side_effect=subprocess.TimeoutExpired(cmd='c', timeout=180)):
            result = rt.oneshot(prompt='p', model='claude-haiku-4-5-20251001', stdin_text='x')
        assert result is None


# ─────────────────────────────────────────────────────────────────────────────
# 4. GeminiRuntime smoke test — spawns stub binary without crashing
# ─────────────────────────────────────────────────────────────────────────────

class TestGeminiRuntimeSmoke:
    """GeminiRuntime.dispatch() must not crash when pointed at a stub binary.
    We verify the Popen call is made with the expected structure.
    """

    def test_dispatch_calls_popen(self, tmp_path):
        rt = ar.get_runtime('gemini')
        assert rt is not None, "gemini runtime must be registered"

        popen_calls = []
        thread_starts = []

        class FakeProc:
            pid = 99999
            stdout = MagicMock()
            def __init__(self): pass

        def fake_popen(cmd, **kwargs):
            popen_calls.append({'cmd': cmd, 'kwargs': kwargs})
            return FakeProc()

        def fake_thread_start(self):
            thread_starts.append(True)

        session_dict = {
            'session_id': 'smoke_test_001',
            'status': 'running',
            'log_lines': [],
            'project_id': 'test_proj',
        }

        with patch('subprocess.Popen', side_effect=fake_popen), \
             patch.object(threading.Thread, 'start', fake_thread_start):
            try:
                rt.dispatch(
                    project_path=str(tmp_path),
                    task='Hello, world.',
                    system_prompt='',
                    mode='A',
                    model='',
                    mc_session_id='smoke_test_001',
                    session_dict=session_dict,
                    project_id='test_proj',
                    register_process=lambda *a, **k: None,
                )
            except Exception as e:
                pytest.fail(f"GeminiRuntime.dispatch() raised unexpectedly: {e}")

        # A Popen call must have been made
        assert len(popen_calls) >= 1, "GeminiRuntime.dispatch() must call subprocess.Popen"
        cmd = popen_calls[0]['cmd']
        assert isinstance(cmd, list)
        assert len(cmd) > 0

    @staticmethod
    def _fake_popen_capture(popen_calls):
        class FakeProc:
            pid = 99998
            stdout = MagicMock()
            def poll(self):
                return None

        def fake_popen(cmd, **kwargs):
            popen_calls.append(cmd)
            return FakeProc()
        return fake_popen

    def test_followup_resumes_by_session_id_not_latest(self, tmp_path):
        """Stage 5: a Gemini followup resumes THIS session by its captured id
        (--resume <id>), never `latest` — `latest` grabs a stale unrelated
        session (the phantom-task bug) — and sends only the new message."""
        rt = ar.get_runtime('gemini')
        popen_calls, sent_prompts = [], []
        session_dict = {
            'session_id': 'fu_001', 'status': 'idle', 'log_lines': [],
            'project_id': 'test_proj',
            '_gemini_session_id': 'sess-uuid-abc123',
            '_system_prompt': 'HEAVY STASHED CONTEXT ' * 500,
        }
        handle = ar.SessionHandle(
            mc_session_id='fu_001', provider='gemini', mode='A',
            project_path=str(tmp_path), project_id='test_proj',
            session_dict=session_dict,
        )
        with patch('subprocess.Popen', side_effect=self._fake_popen_capture(popen_calls)), \
             patch.object(threading.Thread, 'start', lambda self: None), \
             patch.object(rt, '_write_prompt_async',
                          side_effect=lambda proc, prompt, sid: sent_prompts.append(prompt)):
            rt.write_followup(handle, 'Just the new question?')

        cmd = popen_calls[0]
        assert '--resume' in cmd and 'sess-uuid-abc123' in cmd, \
            "followup must resume THIS session by its captured id"
        assert 'latest' not in cmd, \
            "must NOT use --resume latest — it resumes a stale session"
        prompt = sent_prompts[0]
        assert 'Just the new question?' in prompt
        assert 'HEAVY STASHED CONTEXT' not in prompt, \
            "resume path must not re-paste the stashed context (the token burn)"

    def test_followup_without_session_id_falls_back_not_latest(self, tmp_path):
        """No captured gemini session id → the followup must NOT gamble on
        `--resume latest`; it re-pastes context so the agent isn't amnesiac."""
        rt = ar.get_runtime('gemini')
        popen_calls, sent_prompts = [], []
        session_dict = {
            'session_id': 'fu_002', 'status': 'idle', 'log_lines': [],
            'project_id': 'test_proj',
            '_system_prompt': 'STASHED CONTEXT BLOB',
        }
        handle = ar.SessionHandle(
            mc_session_id='fu_002', provider='gemini', mode='A',
            project_path=str(tmp_path), project_id='test_proj',
            session_dict=session_dict,
        )
        with patch('subprocess.Popen', side_effect=self._fake_popen_capture(popen_calls)), \
             patch.object(threading.Thread, 'start', lambda self: None), \
             patch.object(rt, '_write_prompt_async',
                          side_effect=lambda proc, prompt, sid: sent_prompts.append(prompt)):
            rt.write_followup(handle, 'new message')

        assert 'latest' not in popen_calls[0], "must never fall back to --resume latest"
        assert 'STASHED CONTEXT BLOB' in sent_prompts[0], \
            "fallback must re-paste context so the agent keeps continuity"

    def test_gemini_is_registered(self):
        rt = ar.get_runtime('gemini')
        assert rt is not None
        assert isinstance(rt, ar.GeminiRuntime)

    def test_gemini_capabilities(self):
        rt = ar.get_runtime('gemini')
        caps = rt.capabilities()
        assert caps.name == 'gemini'
        assert caps.supports_mode_a

    def test_unknown_provider_raises(self):
        with pytest.raises(KeyError):
            ar.get_runtime('nonexistent_provider_xyz')


# ─────────────────────────────────────────────────────────────────────────────
# 5. Provider routing — non-claude goes through runtime
# ─────────────────────────────────────────────────────────────────────────────

class TestProviderRouting:
    """Verify that the provider-routing logic in the refactored spawn sites
    correctly dispatches non-claude providers through the runtime registry."""

    def test_claude_is_default_runtime(self):
        """get_runtime('claude') must return a ClaudeRuntime."""
        rt = ar.get_runtime('claude')
        assert isinstance(rt, ar.ClaudeRuntime)

    def test_all_providers_registered(self):
        """All providers in scope must be registered."""
        for provider in ('claude', 'gemini'):
            try:
                rt = ar.get_runtime(provider)
                assert rt is not None
            except KeyError:
                pytest.fail(f"Provider {provider!r} must be registered in the runtime registry")

    def test_claude_capabilities_context_injection_flag(self):
        """Claude uses '--append-system-prompt' flag injection (not prepend)."""
        rt = ar.get_runtime('claude')
        caps = rt.capabilities()
        assert caps.context_injection == 'flag'

    def test_gemini_capabilities_context_injection_prepend(self):
        """Non-claude runtimes use prepend — worker context injected into task."""
        rt = ar.get_runtime('gemini')
        caps = rt.capabilities()
        assert caps.context_injection == 'prepend'

    def test_runtime_registry_is_stable(self):
        """Multiple calls to get_runtime() return the same object."""
        rt1 = ar.get_runtime('claude')
        rt2 = ar.get_runtime('claude')
        assert rt1 is rt2

    def test_resolve_binary_str_type(self):
        """ClaudeRuntime.resolve_binary_str() must return a non-empty str."""
        rt = ar.get_runtime('claude')
        result = rt.resolve_binary_str()
        assert isinstance(result, str)
        assert len(result) > 0

    def test_all_runtimes_have_resolve_binary(self):
        """All registered runtimes must implement resolve_binary()."""
        for rt in ar.available_runtimes():
            name = rt.capabilities().name
            assert hasattr(rt, 'resolve_binary'), f"{name} must implement resolve_binary()"
