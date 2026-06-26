"""Request-level tests for the project CRUD family
(mc/blueprints/project_routes.py).

Added with blueprint step 1.11 (MODERNIZATION_PLAN.md Phase 5): happy path,
auth-rejected path, malformed-input path across the 32-route family
(projects list, project create/update/delete, generate-summary, backlog +
notes, github + code-sync glue, attachments + serve-image, import, rules,
memory editor-CRUD, order + grid-layout).

Auth contract (same as 1.8/1.9): no route-private gate — protection is the
app-wide local_auth_gate (mc/blueprints/local_auth.py). Loopback is exempt;
a non-loopback peer with no passcode cookie gets 401 auth_required BEFORE
the handler runs.

Determinism: patches mc.blueprints.project_routes.* ONLY (the Phase-0
test-port rule — never server.*). `subprocess` is replaced on the module
with a recorder namespace so generate_summary never spawns a real claude.
github_sync / project_sync module objects are swapped for fakes on the
module. agent_sessions / terminal_sessions (mc.state objects shared with
the blueprint by import) are snapshot/cleared/restored in the fixture —
the 1.8 cross-test-pollution lesson. /api/processes is never touched, so
the 1.8 pid-reaper fixture is not needed here.
"""
import io
import json
import subprocess as real_subprocess
import sys
import threading
import types
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

LAN = {'REMOTE_ADDR': '192.168.1.50'}


class FakeProc:
    def __init__(self, pid=990201):
        self.pid = pid
        self.killed = False

    def kill(self):
        self.killed = True


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Flask test client; projects blueprint deps patched on the MODULE."""
    import server  # noqa: F401  (registers the blueprint on first import)
    from mc import state as mc_state
    from mc.blueprints import local_auth as la
    from mc.blueprints import project_routes as pr

    # Deterministic gate state: no LAN passcode configured on this run.
    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')

    data_dir = tmp_path / 'projects'
    data_dir.mkdir()
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    mem_dir = tmp_path / 'memory'
    mem_dir.mkdir()
    monkeypatch.setattr(pr, 'DATA_DIR', data_dir)
    monkeypatch.setattr(pr, '_DATA_ROOT', tmp_path)
    monkeypatch.setattr(pr, 'UPLOADS_DIR', uploads)
    monkeypatch.setattr(pr, 'PROJECTS_BASE', tmp_path)
    monkeypatch.setattr(pr, 'SHARED_RULES_PATH', tmp_path / 'SHARED_RULES.md')
    monkeypatch.setattr(pr, '_get_memory_path',
                        lambda p: mem_dir / f"{p['id']}.md")
    monkeypatch.setattr(pr, '_resolve_claude', lambda: 'claude-stub')
    monkeypatch.setattr(pr, 'get_manager',
                        lambda pid: types.SimpleNamespace(lock=threading.Lock()))
    unregistered = []
    monkeypatch.setattr(pr, '_unregister_process', unregistered.append)
    killed_terms = []
    monkeypatch.setattr(pr, '_kill_terminal_session', killed_terms.append)
    monkeypatch.setitem(mc_state.CONFIG, 'auto_workspace_base',
                        str(tmp_path / 'ws'))

    # mc.state session maps are shared objects (blueprint imports them) —
    # snapshot, clear, restore IN PLACE; never rebind (split-brain).
    sess_snapshot = dict(mc_state.agent_sessions)
    term_snapshot = dict(mc_state.terminal_sessions)
    mc_state.agent_sessions.clear()
    mc_state.terminal_sessions.clear()

    # Recorder subprocess namespace — generate_summary must never spawn.
    run_calls = []
    holder = {
        'run': lambda cmd, kw: types.SimpleNamespace(
            returncode=0,
            stdout=json.dumps({'result': json.dumps(
                {'emoji': '⚙', 'summary': 'A test summary.'})}),
            stderr=''),
    }

    def _run(cmd, **kw):
        run_calls.append((cmd, kw))
        out = holder['run'](cmd, kw)
        if isinstance(out, BaseException):
            raise out
        return out

    monkeypatch.setattr(pr, 'subprocess', types.SimpleNamespace(
        run=_run, TimeoutExpired=real_subprocess.TimeoutExpired))

    # Fake sync modules (the blueprint imports the module objects).
    gh_calls = []

    class _FakeGh:
        validate_ok = (True, '')
        sync_result = (True, 'synced 2 items')

        def validate_repo(self, repo):
            gh_calls.append(('validate', repo))
            return self.validate_ok

        def sync_project(self, pid):
            gh_calls.append(('sync', pid))
            return self.sync_result

    fake_gh = _FakeGh()
    monkeypatch.setattr(pr, '_gh_sync', fake_gh)
    # github_setup fires the initial sync on a background thread — record it
    # instead of running it so assertions are deterministic.
    started_threads = []

    class _FakeThread:
        def __init__(self, *a, **kw):
            started_threads.append(kw.get('target'))

        def start(self):
            pass

    monkeypatch.setattr(pr, 'threading',
                        types.SimpleNamespace(Thread=_FakeThread))

    class _FakeProjSync:
        enable_result = (True, 'enabled')
        sync_result = (True, 'fetched')

        def enable(self, pid):
            return self.enable_result

        def disable(self, pid):
            return (True, 'disabled')

        def sync_now(self, pid):
            return self.sync_result

        def compute_status(self, p):
            return {'state': 'in-sync', 'project': p.get('id')}

        def dismiss_commit(self, pid, sha):
            if not sha:
                return (False, 'sha required')
            return (True, f'dismissed {sha}')

    fake_ps = _FakeProjSync()
    monkeypatch.setattr(pr, '_proj_sync', fake_ps)

    import server as srv
    srv.app.config['TESTING'] = True
    c = srv.app.test_client()
    c.pr = pr                          # type: ignore[attr-defined]
    c.state = mc_state                 # type: ignore[attr-defined]
    c.data_dir = data_dir              # type: ignore[attr-defined]
    c.uploads = uploads                # type: ignore[attr-defined]
    c.mem_dir = mem_dir                # type: ignore[attr-defined]
    c.tmp = tmp_path                   # type: ignore[attr-defined]
    c.holder = holder                  # type: ignore[attr-defined]
    c.run_calls = run_calls            # type: ignore[attr-defined]
    c.gh_calls = gh_calls              # type: ignore[attr-defined]
    c.fake_gh = fake_gh                # type: ignore[attr-defined]
    c.fake_ps = fake_ps                # type: ignore[attr-defined]
    c.unregistered = unregistered      # type: ignore[attr-defined]
    c.killed_terms = killed_terms      # type: ignore[attr-defined]
    c.started_threads = started_threads  # type: ignore[attr-defined]
    yield c

    mc_state.agent_sessions.clear()
    mc_state.agent_sessions.update(sess_snapshot)
    mc_state.terminal_sessions.clear()
    mc_state.terminal_sessions.update(term_snapshot)


def _seed(client, pid='tproj', **extra):
    rec = {'id': pid, 'name': 'Test Project', 'status': 'active',
           'backlog': [], **extra}
    (client.data_dir / f'{pid}.json').write_text(
        json.dumps(rec), encoding='utf-8')
    return rec


# ── /api/projects ────────────────────────────────────────────────────────────

def test_projects_list_happy(client):
    _seed(client)
    r = client.get('/api/projects')
    assert r.status_code == 200
    projects = r.get_json()
    assert len(projects) == 1
    p = projects[0]
    assert p['id'] == 'tproj'
    assert 'live_agent' in p            # server-authoritative status field
    assert p['live_agent'] is None      # no live sessions seeded
    assert 'last_updated_relative' in p


def test_projects_list_excludes_sidecars(client):
    _seed(client)
    for sfx in client.pr.EXCLUDED_SIDECAR_SUFFIXES:
        (client.data_dir / f'tproj{sfx}').write_text('{}', encoding='utf-8')
    r = client.get('/api/projects')
    assert [p['id'] for p in r.get_json()] == ['tproj']


def test_projects_list_live_agent_priority(client):
    _seed(client)
    client.state.agent_sessions['s1'] = {
        'project_id': 'tproj', 'status': 'running', 'task': 'do x'}
    client.state.agent_sessions['s2'] = {
        'project_id': 'tproj', 'status': 'idle',
        'waiting_for_question': True, 'task': 'ask y'}
    r = client.get('/api/projects')
    la = r.get_json()[0]['live_agent']
    assert la['state'] == 'asking'      # asking outranks working
    assert la['reason'] == 'question'


# ── POST /api/project/<id> ───────────────────────────────────────────────────

def test_create_project_auto_workspace(client):
    r = client.post('/api/project/newproj', json={'name': 'New'})
    assert r.status_code == 200
    rec = json.loads((client.data_dir / 'newproj.json').read_text(encoding='utf-8'))
    assert rec['name'] == 'New'
    ws = Path(rec['project_path'])
    assert ws.is_dir() and ws.name == 'newproj'


def test_update_project_log_msg_and_no_data(client):
    _seed(client)
    r = client.post('/api/project/tproj', json={'log_msg': 'did a thing'})
    assert r.status_code == 200
    rec = json.loads((client.data_dir / 'tproj.json').read_text(encoding='utf-8'))
    assert rec['activity_log'][0]['msg'] == 'did a thing'
    r = client.post('/api/project/tproj', json={})
    assert r.status_code == 400


def test_projects_list_defaults_pinned_false(client):
    _seed(client)                       # seeded record has no pin field
    p = client.get('/api/projects').get_json()[0]
    assert p['pinned_conversation'] is False   # load_projects() backfills it


def test_pin_endpoint_sets_and_toggles_without_bumping_recency(client):
    _seed(client, last_updated='2020-01-01T00:00:00Z')
    # Explicit set true
    r = client.post('/api/project/tproj/pin', json={'pinned': True})
    assert r.status_code == 200 and r.get_json()['pinned_conversation'] is True
    rec = json.loads((client.data_dir / 'tproj.json').read_text(encoding='utf-8'))
    assert rec['pinned_conversation'] is True
    # LOAD-BEARING: pinning must NOT fake activity — recency is untouched.
    assert rec['last_updated'] == '2020-01-01T00:00:00Z'
    # No body → toggle back off
    r = client.post('/api/project/tproj/pin', json={})
    assert r.get_json()['pinned_conversation'] is False
    # Explicit set false stays false
    r = client.post('/api/project/tproj/pin', json={'pinned': False})
    assert r.get_json()['pinned_conversation'] is False


def test_pin_endpoint_missing_project_404(client):
    assert client.post('/api/project/nope/pin', json={'pinned': True}).status_code == 404


def test_create_project_duplicate_path_409(client):
    ws = client.tmp / 'shared_ws'
    ws.mkdir()
    _seed(client, pid='first', project_path=str(ws))
    r = client.post('/api/project/second',
                    json={'name': 'Second', 'project_path': str(ws)})
    assert r.status_code == 409
    assert 'already used' in r.get_json()['error']


# ── generate_summary ─────────────────────────────────────────────────────────

def test_generate_summary_happy(client):
    _seed(client)
    r = client.post('/api/project/tproj/generate_summary', json={})
    assert r.status_code == 200
    body = r.get_json()
    assert body['emoji'] == '⚙'
    assert body['summary'] == 'A test summary.'
    rec = json.loads((client.data_dir / 'tproj.json').read_text(encoding='utf-8'))
    assert rec['summary'] == 'A test summary.'
    cmd, kw = client.run_calls[0]
    assert cmd[0] == 'claude-stub' and '--output-format' in cmd


def test_generate_summary_claude_missing_and_timeout(client):
    _seed(client)
    client.holder['run'] = lambda cmd, kw: FileNotFoundError('no claude')
    assert client.post('/api/project/tproj/generate_summary',
                       json={}).status_code == 500
    client.holder['run'] = lambda cmd, kw: real_subprocess.TimeoutExpired(
        cmd='claude-stub', timeout=30)
    assert client.post('/api/project/tproj/generate_summary',
                       json={}).status_code == 504


def test_generate_summary_unknown_project_404(client):
    assert client.post('/api/project/nope/generate_summary',
                       json={}).status_code == 404


# ── DELETE /api/project/<id> ─────────────────────────────────────────────────

def test_delete_project_full_cleanup(client):
    att = client.uploads / 'tproj_it1_aa.png'
    att.write_bytes(b'png')
    _seed(client, backlog=[{'id': 'it1', 'text': 't', 'attachments': [
        {'id': 'aa', 'stored_name': 'tproj_it1_aa.png'}]}])
    (client.data_dir / 'tproj_agent_log.json').write_text('[]', encoding='utf-8')
    proc = FakeProc()
    client.state.agent_sessions['s1'] = {
        'project_id': 'tproj', 'status': 'running', 'proc': proc}
    client.state.terminal_sessions['t1'] = {
        'project_id': 'tproj', 'status': 'running'}

    r = client.delete('/api/project/tproj')
    assert r.status_code == 200
    assert not (client.data_dir / 'tproj.json').exists()
    assert not (client.data_dir / 'tproj_agent_log.json').exists()
    assert not att.exists()
    assert proc.killed and client.unregistered == [proc.pid]
    assert 's1' not in client.state.agent_sessions
    assert 't1' not in client.state.terminal_sessions
    assert client.killed_terms == [{'project_id': 'tproj', 'status': 'running'}]


def test_delete_project_404(client):
    assert client.delete('/api/project/nope').status_code == 404


# ── backlog CRUD ─────────────────────────────────────────────────────────────

def test_backlog_get_post_patch_delete_roundtrip(client):
    _seed(client)
    assert client.get('/api/project/tproj/backlog').get_json() == []

    r = client.post('/api/project/tproj/backlog', json={'text': 'task A'})
    assert r.status_code == 200
    item = r.get_json()['item']
    assert item['status'] == 'open' and item['source'] == 'dashboard'

    r = client.patch(f"/api/project/tproj/backlog/{item['id']}",
                     json={'status': 'done'})
    assert r.status_code == 200
    assert r.get_json()['item']['done_at']

    r = client.delete(f"/api/project/tproj/backlog/{item['id']}")
    assert r.status_code == 200
    assert client.get('/api/project/tproj/backlog').get_json() == []


def test_backlog_malformed_and_404(client):
    _seed(client)
    assert client.post('/api/project/tproj/backlog',
                       json={'text': '  '}).status_code == 400
    assert client.post('/api/project/nope/backlog',
                       json={'text': 'x'}).status_code == 404
    assert client.patch('/api/project/tproj/backlog/zzz',
                        json={'text': 'x'}).status_code == 404
    assert client.delete('/api/project/tproj/backlog/zzz').status_code == 404
    assert client.get('/api/project/nope/backlog').status_code == 404


def test_backlog_note_append(client):
    _seed(client, backlog=[{'id': 'it1', 'text': 't'}])
    r = client.post('/api/project/tproj/backlog/it1/note',
                    json={'text': 'note 1', 'agent_code': 'ag1'})
    assert r.status_code == 200
    rec = json.loads((client.data_dir / 'tproj.json').read_text(encoding='utf-8'))
    note = rec['backlog'][0]['notes'][0]
    assert note['text'] == 'note 1' and note['agent_code'] == 'ag1'
    assert client.post('/api/project/tproj/backlog/it1/note',
                       json={'text': ''}).status_code == 400
    assert client.post('/api/project/tproj/backlog/zzz/note',
                       json={'text': 'x'}).status_code == 404


# ── github sync glue ─────────────────────────────────────────────────────────

def test_github_setup_happy_and_status(client):
    _seed(client)
    r = client.post('/api/project/tproj/github/setup',
                    json={'repo': 'owner/repo'})
    assert r.status_code == 200
    assert ('validate', 'owner/repo') in client.gh_calls
    assert len(client.started_threads) == 1   # initial sync queued
    rec = json.loads((client.data_dir / 'tproj.json').read_text(encoding='utf-8'))
    assert rec['github_repo'] == 'owner/repo' and rec['github_sync_enabled']
    assert rec['activity_log'][0]['msg'].startswith('GitHub: Connected')

    s = client.get('/api/project/tproj/github/status').get_json()
    assert s == {'repo': 'owner/repo', 'enabled': True, 'last_sync': None}


def test_github_setup_malformed_and_invalid(client):
    _seed(client)
    assert client.post('/api/project/tproj/github/setup',
                       json={}).status_code == 400
    client.fake_gh.validate_ok = (False, 'no such repo')
    r = client.post('/api/project/tproj/github/setup',
                    json={'repo': 'bad/repo'})
    assert r.status_code == 400 and r.get_json()['error'] == 'no such repo'


def test_github_sync_now_rate_limited_429(client):
    _seed(client)
    client.fake_gh.sync_result = (False, 'Rate limited, retry later')
    assert client.post('/api/project/tproj/github/sync').status_code == 429
    client.fake_gh.sync_result = (False, 'boom')
    assert client.post('/api/project/tproj/github/sync').status_code == 400


def test_github_disconnect(client):
    _seed(client, github_repo='o/r', github_sync_enabled=True)
    r = client.post('/api/project/tproj/github/disconnect')
    assert r.status_code == 200
    rec = json.loads((client.data_dir / 'tproj.json').read_text(encoding='utf-8'))
    assert rec['github_repo'] == '' and not rec['github_sync_enabled']


# ── code-sync glue ───────────────────────────────────────────────────────────

def test_code_sync_lifecycle(client):
    _seed(client)
    assert client.post('/api/project/tproj/code-sync/enable').status_code == 200
    assert client.get('/api/project/tproj/code-sync/status').get_json() == {
        'state': 'in-sync', 'project': 'tproj'}
    assert client.post('/api/project/tproj/code-sync/sync').status_code == 200
    r = client.post('/api/project/tproj/code-sync/dismiss', json={'sha': 'abc'})
    assert r.status_code == 200 and 'abc' in r.get_json()['message']
    assert client.post('/api/project/tproj/code-sync/dismiss',
                       json={}).status_code == 400
    assert client.post('/api/project/tproj/code-sync/disable').status_code == 200
    assert client.post('/api/project/nope/code-sync/enable').status_code == 404


def test_code_sync_rate_limited_429(client):
    _seed(client)
    client.fake_ps.sync_result = (False, 'rate limited (60s)')
    assert client.post('/api/project/tproj/code-sync/sync').status_code == 429


# ── attachments + serve-image ────────────────────────────────────────────────

def test_attachment_upload_serve_delete_roundtrip(client):
    _seed(client, backlog=[{'id': 'it1', 'text': 't'}])
    r = client.post('/api/project/tproj/backlog/it1/attachments',
                    data={'file': (io.BytesIO(b'hello'), 'doc.txt')},
                    content_type='multipart/form-data')
    assert r.status_code == 200
    att = r.get_json()['attachment']
    assert att['original_name'] == 'doc.txt' and att['size'] == 5
    stored = client.uploads / att['stored_name']
    assert stored.read_bytes() == b'hello'

    r = client.get(f"/api/attachments/{att['stored_name']}")
    assert r.status_code == 200 and r.data == b'hello'
    r.close()   # Windows: send_file holds the handle until the response closes
    assert client.get('/api/attachments/zzz.txt').status_code == 404

    r = client.delete(
        f"/api/project/tproj/backlog/it1/attachments/{att['id']}")
    assert r.status_code == 200
    assert not stored.exists()


def test_attachment_upload_malformed(client):
    _seed(client, backlog=[{'id': 'it1', 'text': 't'}])
    assert client.post('/api/project/tproj/backlog/it1/attachments',
                       data={}, content_type='multipart/form-data'
                       ).status_code == 400
    assert client.post('/api/project/tproj/backlog/zzz/attachments',
                       data={'file': (io.BytesIO(b'x'), 'a.txt')},
                       content_type='multipart/form-data').status_code == 404
    assert client.delete(
        '/api/project/tproj/backlog/it1/attachments/zzz').status_code == 404


def test_attachment_per_file_cap_413(client, monkeypatch):
    _seed(client, backlog=[{'id': 'it1', 'text': 't'}])
    monkeypatch.setitem(client.state.CONFIG, 'upload_max_file_bytes', 3)
    r = client.post('/api/project/tproj/backlog/it1/attachments',
                    data={'file': (io.BytesIO(b'too big'), 'big.txt')},
                    content_type='multipart/form-data')
    assert r.status_code == 413
    assert r.get_json()['limit_bytes'] == 3
    monkeypatch.setitem(client.state.CONFIG, 'upload_max_file_bytes', 0)


def test_serve_image_allowlist(client):
    _seed(client)
    img = client.uploads / 'pic.png'
    img.write_bytes(b'\x89PNG')
    assert client.get('/api/serve-image',
                      query_string={'path': str(img)}).status_code == 200
    assert client.get('/api/serve-image').status_code == 400
    # non-image extension → 415
    txt = client.uploads / 'note.txt'
    txt.write_text('x', encoding='utf-8')
    assert client.get('/api/serve-image',
                      query_string={'path': str(txt)}).status_code == 415
    # outside every allowed root → 403 (image in the pytest basetemp parent)
    outside = client.tmp.parent / 'outside_pic_1_11.png'
    outside.write_bytes(b'\x89PNG')
    try:
        assert client.get('/api/serve-image',
                          query_string={'path': str(outside)}).status_code == 403
    finally:
        outside.unlink()


# ── import ───────────────────────────────────────────────────────────────────

def test_import_from_changelog(client):
    ws = client.tmp / 'impws'
    ws.mkdir()
    (ws / 'CHANGELOG.md').write_text(
        '## [2026-06-10] Sprint title\n'
        '### Done\n- shipped X\n'
        '### State\n- all green\n'
        '### Next\n- do Y\n- do Z\n',
        encoding='utf-8')
    _seed(client, project_path=str(ws))
    r = client.post('/api/project/tproj/import')
    assert r.status_code == 200
    imported = r.get_json()['imported']
    assert imported['activity_log'] == 1
    assert imported['backlog'] == 2
    rec = json.loads((client.data_dir / 'tproj.json').read_text(encoding='utf-8'))
    assert rec['description'] == 'all green'
    assert rec['next_action'] == 'do Y'
    assert rec['current_task'] == '[2026-06-10] Sprint title'


def test_import_malformed(client):
    _seed(client, project_path=str(client.tmp / 'no_such_dir'))
    assert client.post('/api/project/tproj/import').status_code == 400
    assert client.post('/api/project/nope/import').status_code == 404


# ── rules ────────────────────────────────────────────────────────────────────

def test_rules_get_put_roundtrip(client):
    ws = client.tmp / 'rulesws'
    ws.mkdir()
    _seed(client, project_path=str(ws))
    (client.tmp / 'SHARED_RULES.md').write_text('shared!', encoding='utf-8')

    r = client.get('/api/project/tproj/rules')
    assert r.status_code == 200
    assert r.get_json() == {'agent_rules': '', 'shared_rules': 'shared!'}

    r = client.put('/api/project/tproj/rules',
                   json={'agent_rules': '# my rules'})
    assert r.status_code == 200
    assert (ws / 'AGENT_RULES.md').read_text(encoding='utf-8') == '# my rules'
    assert client.get(
        '/api/project/tproj/rules').get_json()['agent_rules'] == '# my rules'


def test_rules_put_invalid_path_and_malformed(client):
    _seed(client, project_path='')   # no path → PUT rejected
    assert client.put('/api/project/tproj/rules',
                      json={'agent_rules': 'x'}).status_code == 400
    ws = client.tmp / 'rules2'
    ws.mkdir()
    _seed(client, pid='t2', project_path=str(ws))
    assert client.put('/api/project/t2/rules', json={}).status_code == 400
    assert client.get('/api/project/nope/rules').status_code == 404


def test_shared_rules_get_put(client):
    r = client.put('/api/rules/shared', json={'shared_rules': 'be kind'})
    assert r.status_code == 200
    assert client.get(
        '/api/rules/shared').get_json()['shared_rules'] == 'be kind'
    assert client.put('/api/rules/shared', json={}).status_code == 400


# ── memory editor-CRUD trio ──────────────────────────────────────────────────

def test_memory_get_put_append(client):
    _seed(client)
    r = client.get('/api/project/tproj/memory')
    assert r.status_code == 200
    assert r.get_json()['content'] == ''

    assert client.put('/api/project/tproj/memory',
                      json={'content': 'line 1'}).status_code == 200
    assert (client.mem_dir / 'tproj.md').read_text(encoding='utf-8') == 'line 1'

    assert client.post('/api/project/tproj/memory/append',
                       json={'content': 'line 2'}).status_code == 200
    assert (client.mem_dir / 'tproj.md').read_text(
        encoding='utf-8') == 'line 1\n\nline 2'
    assert client.get(
        '/api/project/tproj/memory').get_json()['content'] == 'line 1\n\nline 2'


def test_memory_malformed_and_404(client):
    _seed(client)
    assert client.put('/api/project/tproj/memory', json={}).status_code == 400
    assert client.post('/api/project/tproj/memory/append',
                       json={'content': '  '}).status_code == 400
    assert client.get('/api/project/nope/memory').status_code == 404
    assert client.put('/api/project/nope/memory',
                      json={'content': 'x'}).status_code == 404
    assert client.post('/api/project/nope/memory/append',
                       json={'content': 'x'}).status_code == 404


# ── order + grid layout ──────────────────────────────────────────────────────

def test_project_order_and_grid_layout(client):
    _seed(client, pid='p1')
    _seed(client, pid='p2')
    r = client.post('/api/projects/order', json={'order': ['p2', None, 'p1']})
    assert r.status_code == 200
    rec1 = json.loads((client.data_dir / 'p1.json').read_text(encoding='utf-8'))
    rec2 = json.loads((client.data_dir / 'p2.json').read_text(encoding='utf-8'))
    assert rec2['display_order'] == 0 and rec1['display_order'] == 2
    assert client.get('/api/grid-layout').get_json() == {
        'order': ['p2', None, 'p1']}


def test_project_order_malformed_and_options(client):
    assert client.post('/api/projects/order', json={}).status_code == 400
    assert client.options('/api/projects/order').status_code == 204


# ── auth gate (app-wide local_auth_gate; same contract as 1.8/1.9) ──────────

def test_lan_peer_rejected_401(client):
    _seed(client)
    r = client.get('/api/projects', environ_base=LAN)
    assert r.status_code == 401
    assert r.get_json()['error'] == 'auth_required'
    r = client.post('/api/project/tproj', json={'name': 'X'},
                    environ_base=LAN)
    assert r.status_code == 401
    # handler never ran — record unchanged
    rec = json.loads((client.data_dir / 'tproj.json').read_text(encoding='utf-8'))
    assert rec['name'] == 'Test Project'


def test_loopback_exempt_twin(client):
    _seed(client)
    r = client.get('/api/projects',
                   environ_base={'REMOTE_ADDR': '127.0.0.1'})
    assert r.status_code == 200
