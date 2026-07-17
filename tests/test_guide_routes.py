"""Request-level tests for the guide/walkthrough/scribe-read family
(mc/blueprints/guide_routes.py).

Added with blueprint step 1.9 (MODERNIZATION_PLAN.md Phase 5): happy path,
auth-rejected path, malformed-input path for each of the 5 routes.

Auth contract (same as 1.8): no route-private gate — protection is the
app-wide local_auth_gate (mc/blueprints/local_auth.py). Loopback is exempt;
a non-loopback peer with no passcode cookie gets 401 auth_required BEFORE
the handler runs (proved by the empty subprocess recorder).

Determinism: no real child processes, no real claude. `subprocess` is
replaced ON THE BLUEPRINT MODULE (the Phase-0 test-port rule: patch
mc.blueprints.guide_routes.*, never server.*) with a recorder namespace;
guide_stream gets a FakeStreamProc whose stdout is the claude stream-json
JSONL the verbatim-moved SSE generator parses. _SERVER_DIR / DATA_DIR are
repointed at tmp_path so the Claydo CLAUDE.md materialization and the
walkthrough seed files land in the sandbox.
"""
import io
import json
import subprocess as real_subprocess
import sys
import types
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

LAN = {'REMOTE_ADDR': '192.168.1.50'}


# ── fakes ─────────────────────────────────────────────────────────────────────

class _RecordingPipe:
    """stdin stand-in: records writes so tests can assert the stream-json msg."""
    def __init__(self):
        self.data = ''
        self.closed = False

    def write(self, s):
        self.data += s

    def flush(self):
        pass

    def close(self):
        self.closed = True


class FakeStreamProc:
    """Popen stand-in for guide_stream: text-mode stdout/stderr, recorded stdin."""
    def __init__(self, stdout_text='', rc=0, stderr_text=''):
        self.stdin = _RecordingPipe()
        self.stdout = io.StringIO(stdout_text)
        self.stderr = io.StringIO(stderr_text)
        self.returncode = rc
        self.pid = 990100
        self.killed = False

    def wait(self, timeout=None):
        return self.returncode

    def poll(self):
        return self.returncode

    def kill(self):
        self.killed = True


def _stream_json(*texts):
    """claude --output-format stream-json stdout: init + assistant turns + result."""
    lines = [json.dumps({'type': 'system', 'subtype': 'init'})]
    for t in texts:
        lines.append(json.dumps({
            'type': 'assistant',
            'message': {'role': 'assistant',
                        'content': [{'type': 'text', 'text': t}]},
        }))
    lines.append(json.dumps({'type': 'result', 'result': ''.join(texts)}))
    return '\n'.join(lines) + '\n'


def _sse_events(body: bytes):
    out = []
    for chunk in body.decode('utf-8').split('\n\n'):
        chunk = chunk.strip()
        if chunk.startswith('data: '):
            out.append(json.loads(chunk[len('data: '):]))
    return out


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Flask test client; guide blueprint deps patched on the MODULE."""
    import server
    from mc import state as mc_state
    from mc.blueprints import guide_routes as gr
    from mc.blueprints import local_auth as la

    # Deterministic gate state: no LAN passcode configured on this run.
    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')

    # Sandbox the wired paths: USER_GUIDE/CHANGELOG + data/claydo under tmp.
    (tmp_path / 'docs').mkdir()
    (tmp_path / 'docs' / 'USER_GUIDE.md').write_text(
        '# User guide\nHow to drive Clayrune.\n', encoding='utf-8')
    (tmp_path / 'CHANGELOG.md').write_text(
        '# Changelog\n\n## [2026-06-10] latest\n- shipped a thing\n',
        encoding='utf-8')
    monkeypatch.setattr(gr, '_SERVER_DIR', tmp_path)
    data_dir = tmp_path / 'projects'
    data_dir.mkdir()
    monkeypatch.setattr(gr, 'DATA_DIR', data_dir)
    monkeypatch.setattr(gr, '_resolve_claude', lambda: 'claude-stub')

    # Project registry + memory-search stubs (both wired fns; the real ones
    # stay in server.py until 1.11/1.12).
    proj = {'id': 'tguide', 'name': 'Guide Test', 'project_path': str(tmp_path)}
    monkeypatch.setattr(gr, 'load_project',
                        lambda pid: proj if pid == 'tguide' else None)
    mem_calls = []

    def _memory_search(p, q, k):
        mem_calls.append((p, q, k))
        return [{'file': 'topic.md', 'score': 3, 'snippet': 'hit for ' + q}]

    monkeypatch.setattr(gr, '_memory_search', _memory_search)

    saved = []
    monkeypatch.setattr(gr, 'save_project',
                        lambda pid, data: saved.append((pid, data)))
    monkeypatch.setitem(mc_state.CONFIG, 'auto_workspace_base',
                        str(tmp_path / 'ws'))

    # Recorder subprocess namespace — nothing real may spawn. Tests reshape
    # behavior through the `holder` hooks.
    run_calls, popen_calls = [], []
    holder = {
        'run': lambda cmd, kw: types.SimpleNamespace(
            returncode=0, stdout=_stream_json('Hello from Claydo'), stderr=''),
        'popen': lambda cmd, kw: FakeStreamProc(_stream_json('Hello from Claydo')),
    }

    def _run(cmd, **kw):
        run_calls.append((cmd, kw))
        out = holder['run'](cmd, kw)
        if isinstance(out, BaseException):
            raise out
        return out

    popen_procs = []

    def _popen(cmd, **kw):
        popen_calls.append((cmd, kw))
        out = holder['popen'](cmd, kw)
        if isinstance(out, BaseException):
            raise out
        popen_procs.append(out)
        return out

    monkeypatch.setattr(gr, 'subprocess', types.SimpleNamespace(
        run=_run, Popen=_popen, PIPE=-1,
        TimeoutExpired=real_subprocess.TimeoutExpired))

    server.app.config['TESTING'] = True
    c = server.app.test_client()
    c.gr = gr                      # type: ignore[attr-defined]
    c.data_dir = data_dir          # type: ignore[attr-defined]
    c.tmp = tmp_path               # type: ignore[attr-defined]
    c.holder = holder              # type: ignore[attr-defined]
    c.run_calls = run_calls        # type: ignore[attr-defined]
    c.popen_calls = popen_calls    # type: ignore[attr-defined]
    c.popen_procs = popen_procs    # type: ignore[attr-defined]
    c.mem_calls = mem_calls        # type: ignore[attr-defined]
    c.saved = saved                # type: ignore[attr-defined]
    return c


# ── /api/guide/ask ───────────────────────────────────────────────────────────

class TestGuideAsk:
    def test_happy_path_assembles_answer_and_materializes_context(self, client):
        r = client.post('/api/guide/ask', json={'question': 'what is this app?'})
        assert r.status_code == 200
        assert r.get_json() == {'answer': 'Hello from Claydo'}

        (cmd, kw) = client.run_calls[0]
        assert cmd[0] == 'claude-stub'
        # No-tools flags + stream-json plumbing moved intact.
        assert '--strict-mcp-config' in cmd and '--tools' in cmd
        assert cmd[cmd.index('--input-format') + 1] == 'stream-json'
        # Question travels via stdin (Windows cmd-line-limit fix), not -p.
        sent = json.loads(kw['input'])
        assert sent['message']['content'] == 'what is this app?'
        # Claydo sandbox cwd + CLAUDE.md = guide + recent-changelog tail.
        claydo = client.tmp / 'data' / 'claydo'
        assert kw['cwd'] == str(claydo)
        ctx = (claydo / 'CLAUDE.md').read_text(encoding='utf-8')
        assert 'How to drive Clayrune.' in ctx
        assert 'Recent changes (from CHANGELOG)' in ctx

    def test_history_prepended(self, client):
        r = client.post('/api/guide/ask', json={
            'question': 'and how do I stop it?',
            'history': [{'role': 'user', 'text': 'how do I start it?'},
                        {'role': 'assistant', 'text': 'Click run.'}],
        })
        assert r.status_code == 200
        sent = json.loads(client.run_calls[0][1]['input'])
        prompt = sent['message']['content']
        assert prompt.startswith('Previous exchange in this conversation:')
        assert 'User: how do I start it?' in prompt
        assert 'You: Click run.' in prompt
        assert prompt.rstrip().endswith('Current question: and how do I stop it?')

    @pytest.mark.parametrize('payload,err', [
        ({}, 'question required'),
        ({'question': '   '}, 'question required'),
        ({'question': 'x' * 2001}, 'question too long (max 2000 chars)'),
    ])
    def test_malformed_400(self, client, payload, err):
        r = client.post('/api/guide/ask', json=payload)
        assert r.status_code == 400
        assert r.get_json()['error'] == err
        assert client.run_calls == []

    def test_guide_missing_500(self, client):
        (client.tmp / 'docs' / 'USER_GUIDE.md').unlink()
        r = client.post('/api/guide/ask', json={'question': 'hi'})
        assert r.status_code == 500
        assert 'USER_GUIDE.md missing' in r.get_json()['error']
        assert client.run_calls == []

    def test_claude_failure_paths(self, client):
        client.holder['run'] = lambda cmd, kw: types.SimpleNamespace(
            returncode=1, stdout='', stderr='boom')
        r = client.post('/api/guide/ask', json={'question': 'hi'})
        assert r.status_code == 500 and r.get_json()['error'] == 'boom'

        client.holder['run'] = lambda cmd, kw: real_subprocess.TimeoutExpired(
            cmd='claude-stub', timeout=60)
        r = client.post('/api/guide/ask', json={'question': 'hi'})
        assert r.status_code == 504
        assert 'timed out' in r.get_json()['error']

        client.holder['run'] = lambda cmd, kw: FileNotFoundError('no claude')
        r = client.post('/api/guide/ask', json={'question': 'hi'})
        assert r.status_code == 500
        assert r.get_json()['error'] == 'Claude CLI not found on this server'


class TestGuideAskAuthReject:
    def test_non_loopback_rejected_before_handler(self, client):
        r = client.post('/api/guide/ask', json={'question': 'hi'},
                        environ_base=LAN)
        assert r.status_code == 401
        assert r.get_json() == {'error': 'auth_required', 'auth_state': 'locked'}
        assert client.run_calls == []

    def test_loopback_is_exempt_same_payload(self, client):
        r = client.post('/api/guide/ask', json={'question': 'hi'})
        assert r.status_code == 200  # gate, not handler, caused the 401 above


# ── /api/guide/stream ────────────────────────────────────────────────────────

class TestGuideStream:
    def test_happy_path_sse_deltas_then_done(self, client):
        client.holder['popen'] = lambda cmd, kw: FakeStreamProc(
            _stream_json('Hello ', 'world'))
        r = client.post('/api/guide/stream', json={'question': 'stream me'})
        assert r.status_code == 200
        assert r.mimetype == 'text/event-stream'
        events = _sse_events(r.data)
        assert events == [
            {'type': 'delta', 'text': 'Hello '},
            {'type': 'delta', 'text': 'world'},
            {'type': 'done', 'answer': 'Hello world'},
        ]
        # stdin carried the stream-json user message, then closed.
        (cmd, kw) = client.popen_calls[0]
        assert cmd[0] == 'claude-stub' and '--strict-mcp-config' in cmd

    def test_malformed_400_is_json_not_sse(self, client):
        r = client.post('/api/guide/stream', json={})
        assert r.status_code == 400
        assert r.get_json()['error'] == 'question required'
        assert client.popen_calls == []

    def test_spawn_failure_yields_sse_error(self, client):
        client.holder['popen'] = lambda cmd, kw: FileNotFoundError('no claude')
        r = client.post('/api/guide/stream', json={'question': 'hi'})
        events = _sse_events(r.data)
        assert events == [{'type': 'error',
                           'message': 'Claude CLI not found on this server'}]

    def test_nonzero_exit_yields_sse_error_with_stderr(self, client):
        client.holder['popen'] = lambda cmd, kw: FakeStreamProc(
            '', rc=2, stderr_text='kaboom')
        r = client.post('/api/guide/stream', json={'question': 'hi'})
        events = _sse_events(r.data)
        assert events == [{'type': 'error', 'message': 'kaboom'}]

    def test_non_loopback_rejected(self, client):
        r = client.post('/api/guide/stream', json={'question': 'hi'},
                        environ_base=LAN)
        assert r.status_code == 401
        assert client.popen_calls == []


# ── /api/project/<id>/scribe-stats ───────────────────────────────────────────

class TestScribeStats:
    def test_no_file_returns_empty_object(self, client):
        r = client.get('/api/project/tguide/scribe-stats')
        assert r.status_code == 200 and r.get_json() == {}

    def test_seeded_counters_roundtrip(self, client):
        (client.data_dir / 'tguide_scribe_stats.json').write_text(
            json.dumps({'scribe_extracted': 4, 'scribe_fell_back:thin': 1}),
            encoding='utf-8')
        r = client.get('/api/project/tguide/scribe-stats')
        assert r.status_code == 200
        assert r.get_json() == {'scribe_extracted': 4, 'scribe_fell_back:thin': 1}

    def test_corrupt_file_500(self, client):
        (client.data_dir / 'tguide_scribe_stats.json').write_text(
            '{not json', encoding='utf-8')
        r = client.get('/api/project/tguide/scribe-stats')
        assert r.status_code == 500
        assert 'error' in r.get_json()


# ── /api/project/<id>/memory/search ──────────────────────────────────────────

class TestMemorySearch:
    def test_happy_path_passes_through_wired_fn(self, client):
        r = client.get('/api/project/tguide/memory/search?q=condense&k=5')
        assert r.status_code == 200
        assert r.get_json() == [{'file': 'topic.md', 'score': 3,
                                 'snippet': 'hit for condense'}]
        (p, q, k) = client.mem_calls[0]
        assert p['id'] == 'tguide' and q == 'condense' and k == 5

    def test_k_defaults_to_3_on_garbage(self, client):
        r = client.get('/api/project/tguide/memory/search?q=x&k=abc')
        assert r.status_code == 200
        assert client.mem_calls[0][2] == 3

    def test_unknown_project_404(self, client):
        r = client.get('/api/project/nope/memory/search?q=x')
        assert r.status_code == 404
        assert client.mem_calls == []

    def test_missing_q_400(self, client):
        r = client.get('/api/project/tguide/memory/search')
        assert r.status_code == 400
        assert r.get_json()['error'] == 'missing q'
        assert client.mem_calls == []


# ── /api/walkthrough/sample-project ──────────────────────────────────────────

class TestWalkthrough:
    def test_happy_path_creates_clayrune_project_and_seeds(self, client):
        r = client.post('/api/walkthrough/sample-project')
        assert r.status_code == 200
        assert r.get_json() == {'ok': True, 'id': 'clayrune', 'existed': False}

        (pid, project) = client.saved[0]
        assert pid == 'clayrune' and project['name'] == 'Clayrune'
        assert len(project['backlog']) == 11
        ws = client.tmp / 'ws' / 'clayrune'
        assert project['project_path'] == str(ws)
        # Seed files written into the auto workspace; AGENT_RULES points at
        # THIS install's docs — proves the _SERVER_DIR wiring, not the
        # blueprint module's own __file__.
        assert 'onboarding project' in (ws / 'README.md').read_text(encoding='utf-8')
        rules = (ws / 'AGENT_RULES.md').read_text(encoding='utf-8')
        assert str(client.tmp / 'docs' / 'USER_GUIDE.md') in rules

    def test_idempotent_when_project_exists(self, client):
        (client.data_dir / 'clayrune.json').write_text('{"id": "clayrune"}',
                                                       encoding='utf-8')
        r = client.post('/api/walkthrough/sample-project')
        assert r.status_code == 200
        assert r.get_json() == {'ok': True, 'id': 'clayrune', 'existed': True}
        assert client.saved == []

    def test_existing_seed_files_not_trampled(self, client):
        ws = client.tmp / 'ws' / 'clayrune'
        ws.mkdir(parents=True)
        (ws / 'README.md').write_text('user-edited', encoding='utf-8')
        r = client.post('/api/walkthrough/sample-project')
        assert r.status_code == 200
        assert (ws / 'README.md').read_text(encoding='utf-8') == 'user-edited'

    def test_non_loopback_rejected(self, client):
        r = client.post('/api/walkthrough/sample-project', environ_base=LAN)
        assert r.status_code == 401
        assert client.saved == []


# ── seed_onboarding_on_startup (first-boot seeder) ───────────────────────────

class TestOnboardingSeed:
    def test_incognito_present_still_seeds_on_fresh_install(self, client):
        # REGRESSION: _ensure_incognito_project() writes _incognito.json into
        # DATA_DIR moments BEFORE the seeder runs. A naive any(*.json) check
        # counted it and skipped seeding, leaving a fresh install (tour skipped)
        # with zero real projects.
        (client.data_dir / '_incognito.json').write_text(
            '{"id": "_incognito", "_is_incognito_project": true}', encoding='utf-8')
        client.gr.seed_onboarding_on_startup()
        assert any(pid == 'clayrune' for (pid, _) in client.saved)
        assert (client.tmp / 'onboarding_seeded.flag').exists()

    def test_sidecar_only_still_seeds(self, client):
        # Telemetry sidecars aren't real projects either.
        (client.data_dir / 'x_agent_log.json').write_text('[]', encoding='utf-8')
        client.gr.seed_onboarding_on_startup()
        assert any(pid == 'clayrune' for (pid, _) in client.saved)

    def test_real_project_present_skips_seed(self, client):
        (client.data_dir / 'myproj.json').write_text(
            '{"id": "myproj", "name": "Mine"}', encoding='utf-8')
        client.gr.seed_onboarding_on_startup()
        assert client.saved == []
        assert (client.tmp / 'onboarding_seeded.flag').exists()

    def test_marker_present_heals_broken_install(self, client):
        # Damaged install: seed marker stamped by the buggy version, but only
        # the incognito pseudo-project exists and no clayrune.json was created.
        (client.tmp / 'onboarding_seeded.flag').write_text('x', encoding='utf-8')
        (client.data_dir / '_incognito.json').write_text(
            '{"id": "_incognito", "_is_incognito_project": true}', encoding='utf-8')
        client.gr.seed_onboarding_on_startup()
        # One-time heal seeds the missing project + stamps its own marker.
        assert any(pid == 'clayrune' for (pid, _) in client.saved)
        assert (client.tmp / 'onboarding_heal_v1.flag').exists()

    def test_heal_is_one_shot(self, client):
        (client.tmp / 'onboarding_seeded.flag').write_text('x', encoding='utf-8')
        (client.tmp / 'onboarding_heal_v1.flag').write_text('x', encoding='utf-8')
        (client.data_dir / '_incognito.json').write_text(
            '{"id": "_incognito", "_is_incognito_project": true}', encoding='utf-8')
        client.gr.seed_onboarding_on_startup()
        assert client.saved == []

    def test_heal_skips_when_real_project_exists(self, client):
        (client.tmp / 'onboarding_seeded.flag').write_text('x', encoding='utf-8')
        (client.data_dir / 'myproj.json').write_text(
            '{"id": "myproj", "name": "Mine"}', encoding='utf-8')
        client.gr.seed_onboarding_on_startup()
        assert client.saved == []
        # Heal marker still stamped so the check never repeats.
        assert (client.tmp / 'onboarding_heal_v1.flag').exists()


# ── Builder modes (Prompt Builder Phase 1) ───────────────────────────────────

def _seed_briefs(tmp):
    d = tmp / 'docs' / 'claydo'
    d.mkdir(parents=True, exist_ok=True)
    (d / 'PROMPT_BUILDER_BRIEF.md').write_text(
        '# Prompt workshop brief\n', encoding='utf-8')
    (d / 'CHARACTER_BUILDER_BRIEF.md').write_text(
        '# Character workshop brief\n', encoding='utf-8')


class TestGuideStreamModes:
    def test_prompt_mode_uses_builder_sandbox_and_brief(self, client):
        _seed_briefs(client.tmp)
        r = client.post('/api/guide/stream',
                        json={'question': 'I need a prompt', 'mode': 'prompt'})
        assert r.status_code == 200
        events = _sse_events(r.data)
        assert events[-1] == {'type': 'done', 'answer': 'Hello from Claydo'}

        (cmd, kw) = client.popen_calls[0]
        builder = client.tmp / 'data' / 'claydo' / 'builder-prompt'
        assert kw['cwd'] == str(builder)
        assert (builder / 'CLAUDE.md').read_text(encoding='utf-8') == \
            '# Prompt workshop brief\n'
        # The ask-mode guide context is NOT what builders see.
        assert '--strict-mcp-config' in cmd  # no-tools posture carries over

    def test_character_mode_injects_project_context(self, client, monkeypatch):
        _seed_briefs(client.tmp)
        # The fixture's project_path IS client.tmp — give it rules + skills.
        (client.tmp / 'AGENT_RULES.md').write_text(
            '# my rules head\n', encoding='utf-8')
        monkeypatch.setattr(
            client.gr, '_skills',
            types.SimpleNamespace(list_skills=lambda **kw: [
                {'name': 'mc-distill'}, {'name': 'code-review'}]))

        r = client.post('/api/guide/stream', json={
            'question': 'make me a reviewer character',
            'mode': 'character', 'project_id': 'tguide',
        })
        assert r.status_code == 200
        # The prompt travels via the recorded stdin pipe.
        sent = json.loads(client.popen_procs[0].stdin.data)
        prompt = sent['message']['content']
        assert prompt.startswith("Context about the user's current project:")
        assert '- Project: Guide Test' in prompt
        assert '# my rules head' in prompt
        assert 'mc-distill, code-review' in prompt
        assert prompt.rstrip().endswith(
            'Current question: make me a reviewer character')
        # Character mode runs in its own sandbox, not prompt mode's.
        assert client.popen_calls[0][1]['cwd'] == str(
            client.tmp / 'data' / 'claydo' / 'builder-character')

    def test_ask_mode_ignores_project_id(self, client):
        r = client.post('/api/guide/stream', json={
            'question': 'how do I tile modals?', 'project_id': 'tguide'})
        assert r.status_code == 200
        sent = json.loads(client.popen_procs[0].stdin.data)
        assert sent['message']['content'] == 'how do I tile modals?'
        assert client.popen_calls[0][1]['cwd'] == str(
            client.tmp / 'data' / 'claydo')

    def test_unknown_mode_400(self, client):
        r = client.post('/api/guide/stream',
                        json={'question': 'x', 'mode': 'bogus'})
        assert r.status_code == 400
        assert 'mode must be' in r.get_json()['error']
        assert client.popen_calls == []

    def test_missing_brief_500(self, client):
        r = client.post('/api/guide/stream',
                        json={'question': 'x', 'mode': 'character'})
        assert r.status_code == 500
        assert 'builder brief missing' in r.get_json()['error']
        assert client.popen_calls == []
