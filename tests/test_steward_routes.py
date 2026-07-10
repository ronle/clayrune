"""End-to-end test for the steward blueprint: enable → status → disable through
the real Flask app, isolated via monkeypatched data paths. Validates the whole
bootstrap: config persisted, charter seeded, schedule created, fence installed
into the project's .claude/settings.json — and that disable reverses all of it.
"""
import json

import pytest


@pytest.fixture()
def sclient(tmp_path, monkeypatch):
    import server  # noqa: F401 — registers blueprints + runs wire() on import
    from mc.blueprints import project_routes as pr
    from mc.blueprints import scheduler_routes as sr
    from mc.blueprints import local_auth as la
    from mc import state
    import steward

    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    data_dir = tmp_path / 'projects'
    data_dir.mkdir()
    monkeypatch.setattr(pr, 'DATA_DIR', data_dir)
    monkeypatch.setattr(sr, 'SCHEDULES_PATH', tmp_path / 'schedules.json')
    monkeypatch.setattr(steward.CFG, 'data_root', tmp_path)
    monkeypatch.setattr(steward.CFG, 'notify_push', lambda *a, **k: None)
    monkeypatch.setitem(state.CONFIG, 'auto_workspace_base', str(tmp_path / 'ws'))

    proj_path = tmp_path / 'proj'
    proj_path.mkdir()
    pr.save_project('p1', {'id': 'p1', 'name': 'P1',
                           'project_path': str(proj_path), 'backlog': []})

    server.app.config['TESTING'] = True
    return server.app.test_client(), proj_path, pr, sr


def _settings(proj_path):
    f = proj_path / '.claude' / 'settings.json'
    return json.loads(f.read_text(encoding='utf-8')) if f.exists() else {}


def test_enable_requires_objective(sclient):
    client, *_ = sclient
    r = client.post('/api/project/p1/steward/enable', json={})
    assert r.status_code == 400


def test_enable_bootstraps_everything(sclient):
    client, proj_path, pr, sr = sclient
    r = client.post('/api/project/p1/steward/enable',
                    json={'objective': 'Keep docs synced with code',
                          'cadence_minutes': 60})
    assert r.status_code == 200
    body = r.get_json()
    assert body['enabled'] and body['fenced']
    assert body['cadence_minutes'] == 60
    assert body['charter_item_id']

    # config persisted
    p = pr.load_project('p1')
    assert p['steward_mode'] == 'on'
    assert p['steward_objective'] == 'Keep docs synced with code'

    # charter seeded
    charter = next(it for it in p['backlog'] if it['source'] == 'steward-charter')
    assert charter['text'].startswith('STEWARD CHARTER:')

    # schedule created, continues one thread, interval cadence, steward-tagged
    scheds = json.loads((sr.SCHEDULES_PATH).read_text(encoding='utf-8'))
    st = next(s for s in scheds if s.get('steward'))
    assert st['project_id'] == 'p1'
    assert st['continue_session'] is True
    assert st['schedule_type'] == 'interval'
    assert st['interval_minutes'] == 60
    assert '[Steward cycle]' in st['task']

    # fence installed into the project's own settings
    hook = _settings(proj_path)['hooks']['PreToolUse'][0]
    assert 'Bash' in hook['matcher']
    assert 'fence.py' in hook['hooks'][0]['command']


def test_enable_is_idempotent(sclient):
    client, proj_path, pr, sr = sclient
    for _ in range(2):
        client.post('/api/project/p1/steward/enable',
                    json={'objective': 'x', 'cadence_minutes': 90})
    p = pr.load_project('p1')
    charters = [it for it in p['backlog'] if it['source'] == 'steward-charter']
    assert len(charters) == 1                          # no duplicate charter
    scheds = json.loads((sr.SCHEDULES_PATH).read_text(encoding='utf-8'))
    assert sum(1 for s in scheds if s.get('steward')) == 1   # no duplicate schedule
    pre = _settings(proj_path)['hooks']['PreToolUse']
    assert sum(1 for e in pre for h in e['hooks']
               if 'fence.py' in h['command']) == 1     # no duplicate hook


def test_status_reflects_state(sclient):
    client, *_ = sclient
    assert client.get('/api/project/p1/steward').get_json()['enabled'] is False
    client.post('/api/project/p1/steward/enable', json={'objective': 'o'})
    s = client.get('/api/project/p1/steward').get_json()
    assert s['enabled'] and s['objective'] == 'o' and s['schedule_id']


def test_disable_reverses_bootstrap(sclient):
    client, proj_path, pr, sr = sclient
    client.post('/api/project/p1/steward/enable',
                json={'objective': 'o', 'cadence_minutes': 60})
    r = client.post('/api/project/p1/steward/disable')
    assert r.status_code == 200 and r.get_json()['enabled'] is False

    assert pr.load_project('p1')['steward_mode'] == 'off'
    scheds = json.loads((sr.SCHEDULES_PATH).read_text(encoding='utf-8'))
    assert not any(s.get('steward') for s in scheds)        # schedule gone
    pre = _settings(proj_path).get('hooks', {}).get('PreToolUse', [])
    assert not any('fence.py' in h['command'] for e in pre for h in e['hooks'])  # unfenced
    # charter is intentionally preserved as a record
    assert any(it['source'] == 'steward-charter' for it in pr.load_project('p1')['backlog'])


def test_loop_health_endpoint(sclient):
    client, *_ = sclient
    client.post('/api/project/p1/steward/enable', json={'objective': 'o'})
    h = client.get('/api/steward/loop-health').get_json()
    assert h['projects_enabled'] == 1
    assert h['enabled'][0]['project_id'] == 'p1'


def test_standalone_requires_name_and_objective(sclient):
    client, *_ = sclient
    assert client.post('/api/steward/standalone/enable', json={'objective': 'o'}).status_code == 400
    assert client.post('/api/steward/standalone/enable', json={'name': 'X'}).status_code == 400


def test_standalone_bootstraps_workspace(sclient):
    client, proj_path, pr, sr = sclient
    r = client.post('/api/steward/standalone/enable',
                    json={'name': 'Env Health', 'objective': 'Keep my environment healthy',
                          'cadence_minutes': 120})
    assert r.status_code == 200
    body = r.get_json()
    assert body['ok'] and body['enabled'] and body['standalone']
    pid = body['project_id']
    assert pid.startswith('_steward_')

    # workspace pseudo-project provisioned, flagged, hidden-eligible
    ws = pr.load_project(pid)
    assert ws is not None and ws['_is_steward_workspace'] is True
    assert ws['project_path']
    # charter + schedule + fence all bootstrapped on the workspace
    assert any(it['source'] == 'steward-charter' for it in ws['backlog'])
    scheds = json.loads((sr.SCHEDULES_PATH).read_text(encoding='utf-8'))
    assert any(s.get('steward') and s['project_id'] == pid for s in scheds)
    fence = _settings(__import__('pathlib').Path(ws['project_path']))
    assert 'fence.py' in fence['hooks']['PreToolUse'][0]['hooks'][0]['command']


def test_standalone_idempotent_by_name(sclient):
    client, proj_path, pr, sr = sclient
    for _ in range(2):
        client.post('/api/steward/standalone/enable',
                    json={'name': 'Weekly Research', 'objective': 'o'})
    ws_projects = [p for p in pr.load_projects() if p.get('_is_steward_workspace')]
    assert len(ws_projects) == 1  # same slug reused, not duplicated


def test_standalone_shows_in_loop_health(sclient):
    client, *_ = sclient
    client.post('/api/steward/standalone/enable',
                json={'name': 'Dep Watch', 'objective': 'Watch dependencies'})
    h = client.get('/api/steward/loop-health').get_json()
    entry = next(e for e in h['enabled'] if e['standalone'])
    assert entry['objective'] == 'Watch dependencies'


def test_disable_preserves_user_hooks(sclient):
    """The fence removal must not eat a user-authored PreToolUse hook."""
    client, proj_path, pr, sr = sclient
    settings_dir = proj_path / '.claude'
    settings_dir.mkdir(parents=True, exist_ok=True)
    (settings_dir / 'settings.json').write_text(json.dumps({
        'hooks': {'PreToolUse': [{'matcher': 'Bash', 'hooks': [
            {'type': 'command', 'command': 'echo user-hook'}]}]}}), encoding='utf-8')
    client.post('/api/project/p1/steward/enable', json={'objective': 'o'})
    client.post('/api/project/p1/steward/disable')
    pre = _settings(proj_path)['hooks']['PreToolUse']
    cmds = [h['command'] for e in pre for h in e['hooks']]
    assert 'echo user-hook' in cmds                    # user hook survived
    assert not any('fence.py' in c for c in cmds)       # steward hook gone
