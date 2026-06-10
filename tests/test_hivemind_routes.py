"""Request-level tests for the hivemind family (mc/blueprints/hivemind_routes.py).

Added with blueprint step 1.10 (MODERNIZATION_PLAN.md Phase 5): happy path,
auth-rejected path, malformed-input path across the CRUD + workstream + bus +
knowledge + escalation + runs surfaces, plus the two spawn paths (claude
direct-Popen and non-claude runtime routing) and the SSE bus stream.

Auth contract (same as 1.8/1.9): no route-private gate — protection is the
app-wide local_auth_gate (mc/blueprints/local_auth.py). Loopback is exempt;
a non-loopback peer with no passcode cookie gets 401 auth_required BEFORE
the handler runs (proved by the untouched recorders + empty data dir).

Determinism: no real child processes, no real threads from the blueprint.
`subprocess` AND `threading` are replaced ON THE BLUEPRINT MODULE (the
Phase-0 test-port rule: patch mc.blueprints.hivemind_routes.*, never
server.*) with recorder namespaces; `_agent_runtime` is a fake registry for
the non-claude routing test. HIVEMIND_DIR is repointed at tmp_path. Shared
mc.state structures the family writes (agent_sessions,
_hivemind_orchestrating, _hivemind_sse_queues) are snapshot/cleared/restored
by the fixture so nothing leaks across tests (the 1.8 cross-test-pollution
lesson). /api/processes is never touched -> 1.8's pid-reaper fixture not
needed.
"""
import json
import sys
import threading as real_threading
import types
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

LAN = {'REMOTE_ADDR': '192.168.1.50'}


# ── fakes ─────────────────────────────────────────────────────────────────────

class FakeProc:
    """Popen stand-in for the worker/orchestrator spawn paths."""
    def __init__(self):
        self.pid = 990200
        self.stdout = None
        self.killed = False

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True


class FakeThread:
    """threading.Thread stand-in: records, never runs the target."""
    started = []

    def __init__(self, target=None, args=(), kwargs=None, daemon=None, name=None):
        self.target = target
        self.args = args

    def start(self):
        FakeThread.started.append(self)


class FakeManager:
    def __init__(self):
        self.lock = real_threading.RLock()
        self.session_ids = set()
        self.guardian_calls = 0

    def ensure_guardian(self):
        self.guardian_calls += 1


class FakeRuntime:
    """agent_runtime stand-in for the non-claude routing branch."""
    def __init__(self):
        self.dispatch_calls = []

    def dispatch(self, **kw):
        self.dispatch_calls.append(kw)


# ── fixture ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Flask test client; hivemind blueprint deps patched on the MODULE."""
    import server  # noqa: F401  (registers the blueprint on the shared app)
    from mc import state as mc_state
    from mc.blueprints import hivemind_routes as hm
    from mc.blueprints import local_auth as la

    # Deterministic gate state: no LAN passcode configured on this run.
    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')

    # Sandbox the hivemind data root (wired placeholder in prod).
    hm_dir = tmp_path / 'hiveminds'
    hm_dir.mkdir()
    monkeypatch.setattr(hm, 'HIVEMIND_DIR', hm_dir)
    monkeypatch.setattr(hm, 'PORT', 5377)

    # Project registry stub (load_project is wired from server.py until 1.11).
    proj_path = tmp_path / 'ws'
    proj_path.mkdir()
    proj = {'id': 'thm', 'name': 'HM Test', 'project_path': str(proj_path),
            'provider': 'claude'}
    projects = {'thm': proj}
    monkeypatch.setattr(hm, 'load_project', lambda pid: projects.get(pid))

    # Dispatch-family recorders (all wired fns — the real ones stay in
    # server.py until 1.12).
    mgr = FakeManager()
    monkeypatch.setattr(hm, 'get_manager', lambda pid: mgr)
    reg_calls, activity, sp_ctx, cleanup_calls, hidden = [], [], [], [], []
    monkeypatch.setattr(hm, '_register_process',
                        lambda proc, name, t, sid, pid, prev='': reg_calls.append(
                            (proc, name, t, sid, pid, prev)))
    monkeypatch.setattr(hm, '_read_agent_stream',
                        lambda proc, session: None)
    monkeypatch.setattr(hm, '_resolve_claude', lambda: 'claude-stub')
    monkeypatch.setattr(hm, '_sysprompt_file_args',
                        lambda ctx: (sp_ctx.append(ctx), ([], None))[1])
    monkeypatch.setattr(hm, '_sysprompt_cleanup',
                        lambda path, proc: cleanup_calls.append((path, proc)))
    monkeypatch.setattr(hm, '_hide_windows_delayed',
                        lambda pid: hidden.append(pid))
    monkeypatch.setattr(hm, '_log_agent_activity',
                        lambda pid, msg, bump_updated=True: activity.append((pid, msg)))
    monkeypatch.setattr(hm, '_clayrune_universal_capabilities',
                        lambda port=None: ['UNIVERSAL-CAPS port=%s' % port])
    monkeypatch.setattr(hm, '_clayrune_api_reference', lambda: 'API-REF-BODY')

    # Run-history stubs (straggler route deps).
    agent_log = []
    monkeypatch.setattr(hm, '_load_agent_log', lambda pid: list(agent_log))
    monkeypatch.setattr(hm, '_enrich_run_entries', lambda page: page)

    # Recorder namespaces ON THE MODULE — nothing real may spawn/run.
    popen_calls = []
    holder = {'popen': lambda cmd, kw: FakeProc()}

    def _popen(cmd, **kw):
        popen_calls.append((cmd, kw))
        out = holder['popen'](cmd, kw)
        if isinstance(out, BaseException):
            raise out
        return out

    import subprocess as real_subprocess
    monkeypatch.setattr(hm, 'subprocess', types.SimpleNamespace(
        Popen=_popen, PIPE=-1, STDOUT=-2, DEVNULL=-3,
        TimeoutExpired=real_subprocess.TimeoutExpired))
    FakeThread.started = []
    monkeypatch.setattr(hm, 'threading', types.SimpleNamespace(Thread=FakeThread))

    # Fake provider registry for the non-claude branch.
    fake_rt = FakeRuntime()

    def _get_runtime(name):
        if name == 'fakeprov':
            return fake_rt
        raise KeyError(name)

    monkeypatch.setattr(hm, '_agent_runtime',
                        types.SimpleNamespace(get_runtime=_get_runtime))

    # Deterministic CONFIG reads (live data/config.json otherwise).
    monkeypatch.setitem(mc_state.CONFIG, 'agent_max_turns', 0)
    monkeypatch.setitem(mc_state.CONFIG, 'agent_model', '')
    monkeypatch.setitem(mc_state.CONFIG, 'default_provider', 'claude')

    # Snapshot/clear shared state the family mutates; restore after.
    sess_snap = dict(mc_state.agent_sessions)
    orch_snap = set(mc_state._hivemind_orchestrating)
    sse_snap = dict(mc_state._hivemind_sse_queues)
    mc_state.agent_sessions.clear()
    mc_state._hivemind_orchestrating.clear()
    mc_state._hivemind_sse_queues.clear()

    server.app.config['TESTING'] = True
    c = server.app.test_client()
    c.hm = hm                          # type: ignore[attr-defined]
    c.hm_dir = hm_dir                  # type: ignore[attr-defined]
    c.projects = projects              # type: ignore[attr-defined]
    c.proj_path = proj_path            # type: ignore[attr-defined]
    c.mgr = mgr                        # type: ignore[attr-defined]
    c.reg_calls = reg_calls            # type: ignore[attr-defined]
    c.activity = activity              # type: ignore[attr-defined]
    c.sp_ctx = sp_ctx                  # type: ignore[attr-defined]
    c.popen_calls = popen_calls        # type: ignore[attr-defined]
    c.holder = holder                  # type: ignore[attr-defined]
    c.fake_rt = fake_rt                # type: ignore[attr-defined]
    c.agent_log = agent_log            # type: ignore[attr-defined]
    c.state = mc_state                 # type: ignore[attr-defined]
    try:
        yield c
    finally:
        mc_state.agent_sessions.clear()
        mc_state.agent_sessions.update(sess_snap)
        mc_state._hivemind_orchestrating.clear()
        mc_state._hivemind_orchestrating.update(orch_snap)
        mc_state._hivemind_sse_queues.clear()
        mc_state._hivemind_sse_queues.update(sse_snap)


def _create(client, **over):
    payload = {
        'goal': 'Audit the data layer',
        'project_id': 'thm',
        'workstreams': [
            {'id': 'ws_001', 'title': 'Schema review', 'priority': 1},
            {'id': 'ws_002', 'title': 'Query audit', 'dependencies': ['ws_001']},
        ],
    }
    payload.update(over)
    r = client.post('/api/hivemind/create', json=payload)
    assert r.status_code == 200, r.get_data(as_text=True)
    return r.get_json()


# ── management CRUD ───────────────────────────────────────────────────────────

class TestCreate:
    def test_happy_inline_workstreams(self, client):
        body = _create(client)
        assert body['ok'] is True
        hm_id = body['hivemind']['id']
        assert hm_id.startswith('hm_')
        # Manifest + synthesis seeded on disk under the sandboxed dir.
        d = client.hm_dir / hm_id
        manifest = json.loads((d / 'manifest.json').read_text(encoding='utf-8'))
        assert manifest['goal'] == 'Audit the data layer'
        assert manifest['status'] == 'active'
        assert manifest['config']['worker_model'] == 'sonnet'
        assert 'No findings yet' in (d / 'knowledge' / 'synthesis.md').read_text(encoding='utf-8')
        # Inline workstreams materialized (the create-endpoint fix).
        ws_ids = {w['id'] for w in body['workstreams']}
        assert ws_ids == {'ws_001', 'ws_002'}
        assert (d / 'workstreams' / 'ws_001.json').exists()
        # Inline ws given -> NO orchestrator decompose dispatch.
        assert FakeThread.started == []
        assert client.popen_calls == []

    def test_no_workstreams_triggers_decompose_orchestrator(self, client):
        body = _create(client, workstreams=[])
        hm_id = body['hivemind']['id']
        # Orchestrator CLI session was prepared and its runner thread started
        # (recorded, never run — so the in-flight guard still holds the id).
        assert len(FakeThread.started) == 1
        assert hm_id in client.state._hivemind_orchestrating

    @pytest.mark.parametrize('payload,err', [
        ({}, 'goal required'),
        ({'goal': '   '}, 'goal required'),
        ({'goal': 'g'}, 'project_id required'),
    ])
    def test_malformed_400(self, client, payload, err):
        r = client.post('/api/hivemind/create', json=payload)
        assert r.status_code == 400
        assert r.get_json()['error'] == err

    def test_unknown_project_404(self, client):
        r = client.post('/api/hivemind/create',
                        json={'goal': 'g', 'project_id': 'nope'})
        assert r.status_code == 404
        assert r.get_json()['error'] == 'project not found'


class TestListGetUpdate:
    def test_list_with_project_filter_and_summary(self, client):
        hm_id = _create(client)['hivemind']['id']
        r = client.get('/api/hivemind/list')
        assert r.status_code == 200
        rows = r.get_json()
        assert [h['id'] for h in rows] == [hm_id]
        assert rows[0]['workstream_count'] == 2
        assert rows[0]['workstreams_completed'] == 0
        assert 'updated_relative' in rows[0]
        # Filter excludes non-matching project ids.
        r = client.get('/api/hivemind/list?project_id=other')
        assert r.get_json() == []

    def test_get_detail_shape(self, client):
        hm_id = _create(client)['hivemind']['id']
        r = client.get(f'/api/hivemind/{hm_id}')
        assert r.status_code == 200
        body = r.get_json()
        assert body['manifest']['id'] == hm_id
        assert {w['id'] for w in body['workstreams']} == {'ws_001', 'ws_002'}
        assert body['recent_messages'] == []
        assert body['decisions'] == []
        assert body['open_questions'] == []

    def test_get_unknown_404(self, client):
        r = client.get('/api/hivemind/hm_nope')
        assert r.status_code == 404

    def test_update_allowed_fields_and_config_merge(self, client):
        hm_id = _create(client)['hivemind']['id']
        r = client.put(f'/api/hivemind/{hm_id}', json={
            'title': 'New title', 'config': {'max_concurrent_workers': 7},
            'ignored_key': 'x'})
        assert r.status_code == 200
        m = r.get_json()['manifest']
        assert m['title'] == 'New title'
        assert m['config']['max_concurrent_workers'] == 7
        assert m['config']['worker_model'] == 'sonnet'  # merge, not replace
        assert 'ignored_key' not in m

    def test_update_unknown_404(self, client):
        assert client.put('/api/hivemind/hm_nope', json={}).status_code == 404


class TestLifecycle:
    def test_pause_marks_active_workstreams_paused(self, client):
        hm_id = _create(client)['hivemind']['id']
        ws_p = client.hm_dir / hm_id / 'workstreams' / 'ws_001.json'
        ws = json.loads(ws_p.read_text(encoding='utf-8'))
        ws['status'] = 'active'
        ws_p.write_text(json.dumps(ws), encoding='utf-8')

        r = client.post(f'/api/hivemind/{hm_id}/pause')
        assert r.status_code == 200 and r.get_json()['status'] == 'paused'
        assert json.loads(ws_p.read_text(encoding='utf-8'))['status'] == 'paused'

    def test_stop_pauses_all_noncompleted(self, client):
        hm_id = _create(client)['hivemind']['id']
        r = client.post(f'/api/hivemind/{hm_id}/stop')
        assert r.status_code == 200 and r.get_json()['status'] == 'stopped'
        for wid in ('ws_001', 'ws_002'):
            ws = json.loads((client.hm_dir / hm_id / 'workstreams' / f'{wid}.json')
                            .read_text(encoding='utf-8'))
            assert ws['status'] == 'paused'

    def test_start_resumes_and_bumps_session_count(self, client):
        hm_id = _create(client)['hivemind']['id']
        client.post(f'/api/hivemind/{hm_id}/pause')
        r = client.post(f'/api/hivemind/{hm_id}/start')
        assert r.status_code == 200 and r.get_json()['status'] == 'active'
        m = json.loads((client.hm_dir / hm_id / 'manifest.json').read_text(encoding='utf-8'))
        assert m['session_count'] == 1
        # Workstreams exist -> no decompose dispatch fired.
        assert FakeThread.started == []

    @pytest.mark.parametrize('verb', ['start', 'pause', 'stop'])
    def test_lifecycle_unknown_404(self, client, verb):
        assert client.post(f'/api/hivemind/hm_nope/{verb}').status_code == 404

    def test_delete_archives_then_404(self, client):
        hm_id = _create(client)['hivemind']['id']
        r = client.delete(f'/api/hivemind/{hm_id}')
        assert r.status_code == 200
        assert not (client.hm_dir / hm_id).exists()
        assert (client.hm_dir / '_archived' / hm_id / 'manifest.json').exists()
        # Second delete: gone.
        assert client.delete(f'/api/hivemind/{hm_id}').status_code == 404


# ── workstream management ─────────────────────────────────────────────────────

class TestWorkstreams:
    def test_list(self, client):
        hm_id = _create(client)['hivemind']['id']
        r = client.get(f'/api/hivemind/{hm_id}/workstreams')
        assert r.status_code == 200
        assert {w['id'] for w in r.get_json()} == {'ws_001', 'ws_002'}

    def test_create_happy_and_malformed(self, client):
        hm_id = _create(client, workstreams=[])['hivemind']['id']
        r = client.post(f'/api/hivemind/{hm_id}/workstreams/create',
                        json={'title': 'Deep dive', 'priority': 2})
        assert r.status_code == 200
        ws = r.get_json()['workstream']
        assert ws['status'] == 'pending' and ws['priority'] == 2
        assert (client.hm_dir / hm_id / 'workstreams' / f"{ws['id']}.json").exists()

        r = client.post(f'/api/hivemind/{hm_id}/workstreams/create', json={})
        assert r.status_code == 400 and r.get_json()['error'] == 'title required'
        r = client.post('/api/hivemind/hm_nope/workstreams/create',
                        json={'title': 'x'})
        assert r.status_code == 404

    def test_update_and_completed_at_stamp(self, client):
        hm_id = _create(client)['hivemind']['id']
        r = client.put(f'/api/hivemind/{hm_id}/workstreams/ws_001',
                       json={'status': 'completed', 'title': 'Renamed'})
        assert r.status_code == 200
        ws = r.get_json()['workstream']
        assert ws['title'] == 'Renamed' and ws['completed_at']
        r = client.put(f'/api/hivemind/{hm_id}/workstreams/ws_nope', json={})
        assert r.status_code == 404

    def test_status_endpoint_validates(self, client):
        hm_id = _create(client)['hivemind']['id']
        r = client.post(f'/api/hivemind/{hm_id}/workstreams/ws_001/status',
                        json={'status': 'completed'})
        assert r.status_code == 200 and r.get_json()['status'] == 'completed'
        r = client.post(f'/api/hivemind/{hm_id}/workstreams/ws_001/status',
                        json={'status': 'bogus'})
        assert r.status_code == 400 and r.get_json()['error'] == 'invalid status'
        r = client.post(f'/api/hivemind/{hm_id}/workstreams/ws_nope/status',
                        json={'status': 'active'})
        assert r.status_code == 404


# ── worker spawn (claude direct + runtime routing) ────────────────────────────

class TestSpawn:
    def test_claude_path_spawns_recorder_proc(self, client):
        hm_id = _create(client)['hivemind']['id']
        r = client.post(f'/api/hivemind/{hm_id}/workstreams/ws_001/spawn')
        assert r.status_code == 200
        sid = r.get_json()['session_id']
        assert sid.startswith('hm_')

        # Exactly one (fake) Popen; argv = claude direct-spawn shape.
        assert len(client.popen_calls) == 1
        cmd, kw = client.popen_calls[0]
        assert cmd[0] == 'claude-stub' and cmd[1] == '-p'
        assert '--dangerously-skip-permissions' in cmd
        assert cmd[cmd.index('--model') + 1] == 'sonnet'
        assert '--max-turns' not in cmd          # pinned agent_max_turns=0
        assert kw['cwd'] == str(client.proj_path)

        # Worker context flowed through _sysprompt_file_args and carries the
        # wired clayrune feeders + the workstream brief.
        assert len(client.sp_ctx) == 1
        ctx = client.sp_ctx[0]
        assert 'YOUR WORKSTREAM: Schema review' in ctx
        assert 'UNIVERSAL-CAPS' in ctx and 'API-REF-BODY' in ctx
        assert f'/api/hivemind/{hm_id}/bus/post' in ctx

        # Ledger + session bookkeeping (recorders; manager is the fake).
        assert client.reg_calls[0][2] == 'hivemind_worker'
        s = client.state.agent_sessions[sid]
        assert s['hivemind_id'] == hm_id and s['hivemind_ws_id'] == 'ws_001'
        assert s['trigger_type'] == 'hivemind_worker'
        assert sid in client.mgr.session_ids and client.mgr.guardian_calls == 1

        # Workstream flipped active + persisted.
        ws = json.loads((client.hm_dir / hm_id / 'workstreams' / 'ws_001.json')
                        .read_text(encoding='utf-8'))
        assert ws['status'] == 'active'
        assert ws['current_agent_session_id'] == sid
        assert ws['sessions_used'] == 1
        assert client.activity and 'Hivemind worker spawned' in client.activity[0][1]

    def test_nonclaude_provider_routes_through_runtime(self, client):
        client.projects['thm']['provider'] = 'fakeprov'
        hm_id = _create(client)['hivemind']['id']
        r = client.post(f'/api/hivemind/{hm_id}/workstreams/ws_001/spawn')
        assert r.status_code == 200
        sid = r.get_json()['session_id']
        # No direct Popen — the runtime got the dispatch, context prepended.
        assert client.popen_calls == []
        assert len(client.fake_rt.dispatch_calls) == 1
        kw = client.fake_rt.dispatch_calls[0]
        assert kw['mc_session_id'] == sid
        assert kw['task'].startswith('You are a specialist agent') or \
            'UNIVERSAL-CAPS' in kw['task']  # worker context prepended
        assert '---' in kw['task']
        s = client.state.agent_sessions[sid]
        assert s['provider'] == 'fakeprov' and s['trigger_type'] == 'hivemind_worker'

    def test_spawn_404s_and_400(self, client):
        hm_id = _create(client)['hivemind']['id']
        assert client.post('/api/hivemind/hm_nope/workstreams/ws_001/spawn').status_code == 404
        assert client.post(f'/api/hivemind/{hm_id}/workstreams/ws_nope/spawn').status_code == 404
        client.projects['thm']['project_path'] = ''
        r = client.post(f'/api/hivemind/{hm_id}/workstreams/ws_001/spawn')
        assert r.status_code == 400
        assert r.get_json()['error'] == 'project_path not set'
        assert client.popen_calls == []


class TestHandoff:
    def test_happy_writes_md_and_open_questions(self, client):
        hm_id = _create(client)['hivemind']['id']
        r = client.post(f'/api/hivemind/{hm_id}/workstreams/ws_001/handoff', json={
            'what_was_done': 'Mapped the schema.',
            'key_findings_summary': 'Two unindexed FKs.',
            'open_questions': ['Shard or not?'],
            'next_worker_should': 'Profile the slow queries.',
            'artifact': {'tables': 12},
        })
        assert r.status_code == 200
        d = client.hm_dir / hm_id / 'workstreams'
        md = (d / 'ws_001_handoff.md').read_text(encoding='utf-8')
        assert 'Mapped the schema.' in md and 'Two unindexed FKs.' in md
        assert json.loads((d / 'ws_001_artifact.json').read_text(encoding='utf-8')) == {'tables': 12}
        qs = (client.hm_dir / hm_id / 'knowledge' / 'open_questions.jsonl').read_text(encoding='utf-8')
        assert 'Shard or not?' in qs

    def test_unknown_ws_404(self, client):
        hm_id = _create(client)['hivemind']['id']
        assert client.post(f'/api/hivemind/{hm_id}/workstreams/ws_nope/handoff',
                           json={}).status_code == 404


# ── message bus ───────────────────────────────────────────────────────────────

class TestBus:
    def test_post_message_and_finding_report(self, client):
        hm_id = _create(client)['hivemind']['id']
        r = client.post(f'/api/hivemind/{hm_id}/bus/post', json={
            'from': 'ws_001', 'type': 'finding_report',
            'title': 'FK gap', 'content': 'orders.user_id unindexed',
            'confidence': 'high'})
        assert r.status_code == 200
        msg = r.get_json()['message']
        assert msg['type'] == 'finding_report' and msg['from'] == 'ws_001'
        # Finding JSONL appended + findings_count incremented on the ws.
        f = (client.hm_dir / hm_id / 'workstreams' / 'ws_001_findings.jsonl')
        assert 'FK gap' in f.read_text(encoding='utf-8')
        ws = json.loads((client.hm_dir / hm_id / 'workstreams' / 'ws_001.json')
                        .read_text(encoding='utf-8'))
        assert ws['findings_count'] == 1

    def test_post_malformed_and_unknown(self, client):
        hm_id = _create(client)['hivemind']['id']
        r = client.post(f'/api/hivemind/{hm_id}/bus/post', json={})
        assert r.status_code == 400 and r.get_json()['error'] == 'type required'
        assert client.post('/api/hivemind/hm_nope/bus/post',
                           json={'type': 'question'}).status_code == 404

    def test_poll_filters_by_workstream_and_since(self, client):
        hm_id = _create(client)['hivemind']['id']
        client.post(f'/api/hivemind/{hm_id}/bus/post', json={
            'from': 'ws_001', 'to': 'ws_002', 'type': 'question', 'content': 'q1'})
        client.post(f'/api/hivemind/{hm_id}/bus/post', json={
            'from': 'orchestrator', 'to': 'all', 'type': 'status_update', 'content': 'x'})
        r = client.get(f'/api/hivemind/{hm_id}/bus/poll/ws_002')
        assert r.status_code == 200
        msgs = r.get_json()
        assert len(msgs) == 1 and msgs[0]['content'] == 'q1'
        r = client.get(f'/api/hivemind/{hm_id}/bus/poll/ws_002?since=2999-01-01')
        assert r.get_json() == []

    def test_history_and_limit(self, client):
        hm_id = _create(client)['hivemind']['id']
        for i in range(3):
            client.post(f'/api/hivemind/{hm_id}/bus/post',
                        json={'type': 'status_update', 'content': f'm{i}'})
        r = client.get(f'/api/hivemind/{hm_id}/bus/history?limit=2')
        assert [m['content'] for m in r.get_json()] == ['m1', 'm2']

    def test_sse_stream_delivers_pushed_event_and_unregisters(self, client, monkeypatch):
        hm_id = _create(client)['hivemind']['id']
        # The generator sleeps 0.3s per empty tick and werkzeug's test client
        # pulls the FIRST chunk during .get() (start_response trigger) — that
        # pull would block ~15s until the tick-50 heartbeat. No-op the sleep
        # (module attr — test-port rule) so empty ticks spin instantly.
        import time as real_time
        monkeypatch.setattr(client.hm, '_time', types.SimpleNamespace(
            sleep=lambda s: None, time=real_time.time))
        r = client.get(f'/api/hivemind/{hm_id}/bus/stream')
        assert r.status_code == 200
        assert r.mimetype == 'text/event-stream'
        # The handler registered a queue at connect; push an event into it.
        assert len(client.state._hivemind_sse_queues[hm_id]) == 1
        client.hm._hm_push_sse(hm_id, {'type': 'hivemind_message', 'n': 1})
        # Scan past comment/heartbeat chunks (incl. the pre-push buffered one)
        # to the first data event.
        event = None
        for _ in range(5):
            chunk = next(r.response)
            if isinstance(chunk, bytes):
                chunk = chunk.decode('utf-8')
            if chunk.startswith('data: '):
                event = json.loads(chunk[len('data: '):].strip())
                break
        assert event == {'type': 'hivemind_message', 'n': 1}
        r.response.close()  # generator finally -> queue unregistered
        assert client.state._hivemind_sse_queues[hm_id] == []

    def test_sse_stream_unknown_404(self, client):
        assert client.get('/api/hivemind/hm_nope/bus/stream').status_code == 404


# ── knowledge base ────────────────────────────────────────────────────────────

class TestKnowledge:
    def test_synthesis_get_put_roundtrip(self, client):
        hm_id = _create(client)['hivemind']['id']
        r = client.get(f'/api/hivemind/{hm_id}/knowledge/synthesis')
        assert 'No findings yet' in r.get_json()['content']
        r = client.put(f'/api/hivemind/{hm_id}/knowledge/synthesis',
                       json={'content': '# Updated synthesis'})
        assert r.status_code == 200
        r = client.get(f'/api/hivemind/{hm_id}/knowledge/synthesis')
        assert r.get_json()['content'] == '# Updated synthesis'

    def test_synthesis_notify_only_keeps_file(self, client):
        hm_id = _create(client)['hivemind']['id']
        before = (client.hm_dir / hm_id / 'knowledge' / 'synthesis.md').read_text(encoding='utf-8')
        r = client.put(f'/api/hivemind/{hm_id}/knowledge/synthesis',
                       json={'notify_only': True})
        assert r.status_code == 200
        after = (client.hm_dir / hm_id / 'knowledge' / 'synthesis.md').read_text(encoding='utf-8')
        assert after == before

    def test_decisions_findings_and_question_resolve(self, client):
        hm_id = _create(client)['hivemind']['id']
        assert client.get(f'/api/hivemind/{hm_id}/knowledge/decisions').get_json() == []
        # Seed a finding through the bus, then read it back both ways.
        client.post(f'/api/hivemind/{hm_id}/bus/post', json={
            'from': 'ws_001', 'type': 'finding_report', 'title': 'T', 'content': 'C'})
        all_f = client.get(f'/api/hivemind/{hm_id}/knowledge/findings').get_json()
        ws_f = client.get(f'/api/hivemind/{hm_id}/knowledge/findings?ws_id=ws_001').get_json()
        assert len(all_f) == 1 and len(ws_f) == 1 and ws_f[0]['title'] == 'T'
        # Question resolve: unknown id -> 404; real id -> resolved.
        r = client.post(f'/api/hivemind/{hm_id}/knowledge/questions/q_nope/resolve')
        assert r.status_code == 404
        client.post(f'/api/hivemind/{hm_id}/workstreams/ws_001/handoff',
                    json={'open_questions': ['Why?']})
        qid = json.loads((client.hm_dir / hm_id / 'knowledge' / 'open_questions.jsonl')
                         .read_text(encoding='utf-8').strip())['id']
        r = client.post(f'/api/hivemind/{hm_id}/knowledge/questions/{qid}/resolve')
        assert r.status_code == 200
        assert client.get(f'/api/hivemind/{hm_id}').get_json()['open_questions'] == []

    def test_knowledge_unknown_404(self, client):
        for path in ('knowledge/synthesis', 'knowledge/decisions', 'knowledge/findings'):
            assert client.get(f'/api/hivemind/hm_nope/{path}').status_code == 404


# ── escalation & user intervention ────────────────────────────────────────────

class TestEscalationIntervention:
    def test_escalate_happy(self, client):
        hm_id = _create(client)['hivemind']['id']
        r = client.post(f'/api/hivemind/{hm_id}/escalate',
                        json={'from': 'ws_001', 'content': 'Blocked on creds'})
        assert r.status_code == 200
        esc = r.get_json()['escalation']
        assert esc['type'] == 'escalation' and esc['to'] == 'user'
        assert esc['workstream_id'] == 'ws_001'

    def test_intervene_happy_and_malformed(self, client):
        hm_id = _create(client)['hivemind']['id']
        r = client.post(f'/api/hivemind/{hm_id}/intervene',
                        json={'message': 'Focus on ws_002', 'target': 'ws_002'})
        assert r.status_code == 200
        msg = r.get_json()['message']
        assert msg['from'] == 'user' and msg['to'] == 'ws_002' and msg['type'] == 'directive'
        r = client.post(f'/api/hivemind/{hm_id}/intervene', json={})
        assert r.status_code == 400 and r.get_json()['error'] == 'message required'

    def test_finding_review_and_decision_approve(self, client):
        hm_id = _create(client)['hivemind']['id']
        r = client.post(f'/api/hivemind/{hm_id}/findings/f_001/review',
                        json={'approved': False, 'comment': 'weak evidence'})
        assert r.status_code == 200
        dec = r.get_json()['decision']
        assert dec['approved'] is False and dec['finding_id'] == 'f_001'
        r = client.post(f'/api/hivemind/{hm_id}/decisions/{dec["id"]}/approve',
                        json={'approved': True})
        assert r.status_code == 200
        assert r.get_json()['review']['original_decision_id'] == dec['id']
        # Both recorded in decisions.jsonl.
        decisions = client.get(f'/api/hivemind/{hm_id}/knowledge/decisions').get_json()
        assert len(decisions) == 2

    def test_unknown_404s(self, client):
        for verb, path in [('post', 'escalate'), ('post', 'intervene'),
                           ('post', 'findings/f/review'),
                           ('post', 'decisions/d/approve')]:
            r = getattr(client, verb)(f'/api/hivemind/hm_nope/{path}', json={'message': 'm'})
            assert r.status_code == 404, path


# ── runs (the straggler route from the run-history section) ───────────────────

class TestRuns:
    def _seed_log(self, client, hm_id):
        client.agent_log.extend([
            {'session_id': 's1', 'hivemind_id': hm_id,
             'hivemind_role': 'orchestrator', 'hivemind_ws_id': ''},
            {'session_id': 's2', 'hivemind_id': hm_id,
             'hivemind_role': '', 'hivemind_ws_id': 'ws_001'},
            {'session_id': 's3', 'hivemind_id': 'hm_other',
             'hivemind_role': '', 'hivemind_ws_id': 'ws_009'},
        ])

    def test_filters_and_pagination(self, client):
        hm_id = _create(client)['hivemind']['id']
        self._seed_log(client, hm_id)
        r = client.get(f'/api/hivemind/{hm_id}/runs')
        body = r.get_json()
        assert r.status_code == 200
        assert body['total'] == 2
        assert {e['session_id'] for e in body['runs']} == {'s1', 's2'}
        r = client.get(f'/api/hivemind/{hm_id}/runs?role=orchestrator')
        assert [e['session_id'] for e in r.get_json()['runs']] == ['s1']
        r = client.get(f'/api/hivemind/{hm_id}/runs?role=worker')
        assert [e['session_id'] for e in r.get_json()['runs']] == ['s2']
        r = client.get(f'/api/hivemind/{hm_id}/runs?ws_id=ws_001')
        assert [e['session_id'] for e in r.get_json()['runs']] == ['s2']
        r = client.get(f'/api/hivemind/{hm_id}/runs?limit=1&offset=1')
        body = r.get_json()
        assert body['total'] == 2 and len(body['runs']) == 1
        # Malformed paging params fall back to defaults, not 500.
        r = client.get(f'/api/hivemind/{hm_id}/runs?limit=zap&offset=-9')
        assert r.status_code == 200 and r.get_json()['limit'] == 50

    def test_unknown_404(self, client):
        assert client.get('/api/hivemind/hm_nope/runs').status_code == 404


# ── startup reconcile helper (inbound shim target) ────────────────────────────

class TestReconcileStale:
    def test_long_idle_active_flips_to_stale(self, client):
        hm_id = _create(client)['hivemind']['id']
        man_p = client.hm_dir / hm_id / 'manifest.json'
        m = json.loads(man_p.read_text(encoding='utf-8'))
        m['updated_at'] = '2020-01-01T00:00:00+00:00'   # way past _HM_STALE_HOURS
        man_p.write_text(json.dumps(m), encoding='utf-8')

        client.hm._hm_reconcile_stale_on_startup()
        assert json.loads(man_p.read_text(encoding='utf-8'))['status'] == 'stale'

    def test_paused_untouched(self, client):
        hm_id = _create(client)['hivemind']['id']
        client.post(f'/api/hivemind/{hm_id}/pause')
        man_p = client.hm_dir / hm_id / 'manifest.json'
        m = json.loads(man_p.read_text(encoding='utf-8'))
        m['updated_at'] = '2020-01-01T00:00:00+00:00'
        man_p.write_text(json.dumps(m), encoding='utf-8')

        client.hm._hm_reconcile_stale_on_startup()
        assert json.loads(man_p.read_text(encoding='utf-8'))['status'] == 'paused'


# ── auth gate (app-wide local_auth_gate, not route-private) ───────────────────

class TestAuthReject:
    def test_non_loopback_rejected_before_handler(self, client):
        r = client.post('/api/hivemind/create',
                        json={'goal': 'g', 'project_id': 'thm'},
                        environ_base=LAN)
        assert r.status_code == 401
        assert r.get_json() == {'error': 'auth_required', 'auth_state': 'locked'}
        # Handler never ran: nothing on disk, nothing spawned.
        assert list(client.hm_dir.iterdir()) == []
        assert client.popen_calls == []

    def test_loopback_is_exempt_same_payload(self, client):
        r = client.post('/api/hivemind/create',
                        json={'goal': 'g', 'project_id': 'thm',
                              'workstreams': [{'title': 'w'}]})
        assert r.status_code == 200
