"""Request-level tests for the scheduler family
(mc/blueprints/scheduler_routes.py).

Added with blueprint step 1.13 (MODERNIZATION_PLAN.md Phase 5) — the last
blueprint extraction. A pure move: the route handlers + the background
_scheduler_loop are byte-verbatim from server.py, with the single Phase-2
obs.heartbeat('scheduler') line added to the loop. The agent-dispatch deps
(_dispatch_agent_internal & co.) STAY in agent_routes (1.12) and the projects
store stays in project_routes (1.11); both are late-bound via wire().

These tests guard the MOVE: registration parity (the seam's worst silent
failure), the schedules-store CRUD round-trips against a tmp schedules.json,
the run-now dispatch path with _dispatch_agent_internal PATCHED to a recorder
(MUST NOT spawn a real agent), the /runs pagination reading a seeded agent_log,
and the app-wide local_auth gate (401 before handler for a non-loopback peer).

Determinism: patches mc.blueprints.scheduler_routes.* ONLY (the Phase-0
test-port rule — never server.*). SCHEDULES_PATH and the agent-log reader are
pointed at tmp / recorders so nothing real fires. The fixture rebinds the
blueprint's wired globals on the MODULE for the duration of the test, then
restores them (wire() ran at import with the live deps).
"""
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

LAN = {'REMOTE_ADDR': '192.168.1.50'}

# The exact route surface 1.13 owns. A change here is intentional API churn.
EXPECTED_ROUTES = {
    '/api/schedule/<schedule_id>/run-now',
    '/api/schedule/<schedule_id>/runs',
    '/api/schedules',
    '/api/schedules/<schedule_id>',
}


class _DispatchRecorder:
    """Stand-in for _dispatch_agent_internal: records calls, returns a fake
    session id, never spawns anything. Raise-mode lets us cover the error path."""
    def __init__(self, sid='sess-fake-001', raise_exc=None):
        self.calls = []
        self._sid = sid
        self._raise = raise_exc

    def __call__(self, project_id, task, **kwargs):
        self.calls.append({'project_id': project_id, 'task': task, **kwargs})
        if self._raise is not None:
            raise self._raise
        return self._sid


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    """Flask test client + handles to the patched scheduler module.

    Patches the blueprint's wired globals ON THE MODULE (test-port rule):
    SCHEDULES_PATH -> tmp file; load_project(s) -> simple fakes; the
    agent-dispatch + agent-log seams -> recorders. Restores everything after.
    """
    import server  # noqa: F401  (registers the blueprint + runs wire() on import)
    from mc.blueprints import local_auth as la
    from mc.blueprints import scheduler_routes as sr

    # Deterministic gate: no LAN passcode this run (loopback exempt, LAN 401).
    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')

    # Schedules store -> isolated tmp file.
    sched_path = tmp_path / 'schedules.json'
    monkeypatch.setattr(sr, 'SCHEDULES_PATH', sched_path)

    # Projects: a single known project so name-enrichment + continue paths work.
    projects = [{'id': 'p1', 'name': 'Project One', 'project_path': str(tmp_path / 'ws')}]
    monkeypatch.setattr(sr, 'load_projects', lambda: list(projects))
    monkeypatch.setattr(sr, 'load_project',
                        lambda pid: next((p for p in projects if p['id'] == pid), None))

    # Agent-dispatch + agent-log seams -> deterministic recorders. Default the
    # run-now path to NO continuation (no prior session) so it reaches dispatch.
    dispatch = _DispatchRecorder()
    monkeypatch.setattr(sr, '_dispatch_agent_internal', dispatch)
    monkeypatch.setattr(sr, '_latest_session_id_for_schedule', lambda pid, sid: '')
    monkeypatch.setattr(sr, '_latest_claude_sid_for_schedule', lambda pid, sid: '')
    monkeypatch.setattr(sr, '_newest_run_session_id_for_schedule', lambda pid, sid: '')
    monkeypatch.setattr(sr, '_enrich_run_entries', lambda entries: entries)
    monkeypatch.setattr(sr, '_log_agent_activity', lambda *a, **k: None)

    server.app.config['TESTING'] = True

    class Ctx:
        pass
    c = Ctx()
    c.client = server.app.test_client()
    c.sr = sr
    c.sched_path = sched_path
    c.dispatch = dispatch
    c.projects = projects
    return c


def _seed_schedules(ctx, schedules):
    ctx.sched_path.write_text(json.dumps(schedules), encoding='utf-8')


# ── registration parity — the move's load-bearing guard ───────────────────────

def test_blueprint_registered(ctx):
    import server
    assert 'scheduler_routes' in server.app.blueprints


def test_all_expected_routes_present_under_blueprint(ctx):
    import server
    owned = {r.rule for r in server.app.url_map.iter_rules()
             if r.endpoint.startswith('scheduler_routes.')}
    missing = EXPECTED_ROUTES - owned
    assert not missing, f'routes missing from scheduler_routes blueprint: {sorted(missing)}'


def test_no_unexpected_scheduler_routes(ctx):
    import server
    owned = {r.rule for r in server.app.url_map.iter_rules()
             if r.endpoint.startswith('scheduler_routes.')}
    extra = owned - EXPECTED_ROUTES
    assert not extra, f'unpinned routes under scheduler_routes blueprint: {sorted(extra)}'


# ── GET /api/schedules — empty + populated ────────────────────────────────────

def test_get_schedules_empty(ctx):
    resp = ctx.client.get('/api/schedules')
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_get_schedules_populated_enriches_project_name(ctx):
    _seed_schedules(ctx, [
        {'id': 's1', 'project_id': 'p1', 'task': 'do x', 'enabled': True,
         'schedule_type': 'daily', 'time': '09:00'},
    ])
    resp = ctx.client.get('/api/schedules')
    assert resp.status_code == 200
    body = resp.get_json()
    assert len(body) == 1
    assert body[0]['id'] == 's1'
    # name enrichment from load_projects()
    assert body[0]['project_name'] == 'Project One'


# ── POST /api/schedules — create happy + malformed ────────────────────────────

def test_create_schedule_happy(ctx):
    resp = ctx.client.post('/api/schedules', json={
        'project_id': 'p1', 'task': 'nightly build',
        'schedule_type': 'daily', 'time': '03:00',
    })
    assert resp.status_code == 201
    body = resp.get_json()
    assert body['project_id'] == 'p1'
    assert body['task'] == 'nightly build'
    assert body['enabled'] is True
    assert 'id' in body and len(body['id']) == 8
    # persisted to the tmp store
    saved = json.loads(ctx.sched_path.read_text(encoding='utf-8'))
    assert len(saved) == 1 and saved[0]['id'] == body['id']


def test_create_schedule_missing_fields_400(ctx):
    # no task
    r1 = ctx.client.post('/api/schedules', json={'project_id': 'p1'})
    assert r1.status_code == 400
    # no project_id
    r2 = ctx.client.post('/api/schedules', json={'task': 'x'})
    assert r2.status_code == 400
    # empty body
    r3 = ctx.client.post('/api/schedules', json={})
    assert r3.status_code == 400
    # nothing was written
    assert not ctx.sched_path.exists() or json.loads(ctx.sched_path.read_text()) == []


# ── PUT /api/schedules/<id> — update + 404 ────────────────────────────────────

def test_update_schedule_merges_and_recomputes(ctx):
    _seed_schedules(ctx, [
        {'id': 's1', 'project_id': 'p1', 'task': 'old', 'enabled': True,
         'schedule_type': 'daily', 'time': '09:00', 'next_run': 'stale'},
    ])
    resp = ctx.client.put('/api/schedules/s1', json={'task': 'new', 'enabled': False})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['task'] == 'new'
    assert body['enabled'] is False
    saved = json.loads(ctx.sched_path.read_text(encoding='utf-8'))
    assert saved[0]['task'] == 'new'


def test_update_schedule_not_found_404(ctx):
    _seed_schedules(ctx, [])
    resp = ctx.client.put('/api/schedules/nope', json={'task': 'x'})
    assert resp.status_code == 404


# ── DELETE /api/schedules/<id> — delete + 404 ─────────────────────────────────

def test_delete_schedule(ctx):
    _seed_schedules(ctx, [
        {'id': 's1', 'project_id': 'p1', 'task': 't'},
        {'id': 's2', 'project_id': 'p1', 'task': 't2'},
    ])
    resp = ctx.client.delete('/api/schedules/s1')
    assert resp.status_code == 200
    assert resp.get_json() == {'ok': True}
    saved = json.loads(ctx.sched_path.read_text(encoding='utf-8'))
    assert [s['id'] for s in saved] == ['s2']


def test_delete_schedule_not_found_404(ctx):
    _seed_schedules(ctx, [{'id': 's1', 'project_id': 'p1', 'task': 't'}])
    resp = ctx.client.delete('/api/schedules/nope')
    assert resp.status_code == 404


# ── POST /api/schedule/<id>/run-now — dispatch recorder, NO real spawn ────────

def test_run_now_dispatches_via_recorder(ctx):
    _seed_schedules(ctx, [
        {'id': 's1', 'project_id': 'p1', 'task': 'fire me', 'continue_session': False},
    ])
    resp = ctx.client.post('/api/schedule/s1/run-now')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['ok'] is True
    assert body['session_id'] == 'sess-fake-001'
    # the recorder was invoked exactly once with the schedule's trigger metadata
    assert len(ctx.dispatch.calls) == 1
    call = ctx.dispatch.calls[0]
    assert call['project_id'] == 'p1'
    assert call['task'] == 'fire me'
    assert call['trigger_type'] == 'schedule'
    assert call['trigger_id'] == 's1'
    # last_run stamped for visual feedback
    saved = json.loads(ctx.sched_path.read_text(encoding='utf-8'))
    assert saved[0]['last_run']


def test_run_now_schedule_not_found_404(ctx):
    _seed_schedules(ctx, [])
    resp = ctx.client.post('/api/schedule/nope/run-now')
    assert resp.status_code == 404
    assert len(ctx.dispatch.calls) == 0


def test_run_now_missing_project_or_task_400(ctx):
    _seed_schedules(ctx, [{'id': 's1', 'project_id': '', 'task': ''}])
    resp = ctx.client.post('/api/schedule/s1/run-now')
    assert resp.status_code == 400
    assert len(ctx.dispatch.calls) == 0


def test_run_now_dispatch_failure_500(ctx):
    # recorder raises a generic Exception → 500 dispatch failed
    ctx.sr._dispatch_agent_internal = _DispatchRecorder(raise_exc=RuntimeError('boom'))
    _seed_schedules(ctx, [
        {'id': 's1', 'project_id': 'p1', 'task': 't', 'continue_session': False},
    ])
    resp = ctx.client.post('/api/schedule/s1/run-now')
    assert resp.status_code == 500


# ── GET /api/schedule/<id>/runs — pagination over a seeded agent_log ──────────

def test_runs_pagination_filters_by_trigger(ctx):
    _seed_schedules(ctx, [{'id': 's1', 'project_id': 'p1', 'task': 't'}])
    # 5 matching rows + 2 noise rows (different trigger / manual)
    rows = [
        {'session_id': f'r{i}', 'trigger_type': 'schedule', 'trigger_id': 's1'}
        for i in range(5)
    ] + [
        {'session_id': 'other', 'trigger_type': 'schedule', 'trigger_id': 's2'},
        {'session_id': 'manual', 'trigger_type': 'manual'},
    ]
    ctx.sr._load_agent_log = lambda pid: list(rows)

    # page 1: limit 2
    resp = ctx.client.get('/api/schedule/s1/runs?limit=2&offset=0')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['total'] == 5            # only s1-triggered rows count
    assert body['limit'] == 2
    assert body['offset'] == 0
    assert len(body['runs']) == 2
    assert [r['session_id'] for r in body['runs']] == ['r0', 'r1']

    # page 3 (offset 4): remainder
    resp2 = ctx.client.get('/api/schedule/s1/runs?limit=2&offset=4')
    b2 = resp2.get_json()
    assert [r['session_id'] for r in b2['runs']] == ['r4']


def test_runs_schedule_not_found_404(ctx):
    _seed_schedules(ctx, [])
    resp = ctx.client.get('/api/schedule/nope/runs')
    assert resp.status_code == 404


def test_runs_bad_params_default(ctx):
    _seed_schedules(ctx, [{'id': 's1', 'project_id': 'p1', 'task': 't'}])
    ctx.sr._load_agent_log = lambda pid: []
    resp = ctx.client.get('/api/schedule/s1/runs?limit=abc&offset=-9')
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['limit'] == 50  # malformed → default
    assert body['offset'] == 0  # negative → clamped


# ── auth contract — app-wide gate still covers the moved routes ───────────────

def test_moved_route_behind_lan_gate(ctx):
    """A non-loopback peer with no passcode is 401'd BEFORE the handler runs."""
    resp = ctx.client.get('/api/schedules', environ_overrides=LAN)
    assert resp.status_code == 401
