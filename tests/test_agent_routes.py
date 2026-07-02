"""Request-level tests for the agent-dispatch family
(mc/blueprints/agent_routes.py).

Added with blueprint step 1.12 (MODERNIZATION_PLAN.md Phase 5). 1.12 is the
largest extraction (the agent dispatch/stream/followup/transcript/usage/
provider-auth surface, 33 routes). It is a pure move: the route handlers are
byte-verbatim, the memory/scribe/condense machinery STAYS in server.py and is
late-bound into the blueprint via wire(). These tests therefore guard the move
itself rather than re-deriving behavior already covered by
test_auth_routes.py (provider auth), test_auto_model_router.py (the router),
and test_telemetry.py (/api/usage shape):

  - REGISTRATION PARITY: every route the family is supposed to own is present
    on app.url_map under the agent_routes blueprint — the single guard that
    catches a broken register_blueprint()/wire() seam (a move's worst silent
    failure).
  - read-only endpoint smokes on loopback (providers / usage / router-stats /
    recent-runs) prove wire() actually bound the global-scope deps.
  - the app-wide local_auth gate still covers the moved routes (same auth
    contract as 1.8/1.9/1.11): a non-loopback peer with no passcode → 401.

Determinism: patches mc.blueprints.agent_routes.* ONLY (the Phase-0 test-port
rule — never server.*). DATA_DIR is pointed at an empty tmp dir so the usage /
router-stats / recent-runs globs see a clean slate. agent_sessions (a mc.state
object shared with the blueprint by import) is snapshot/cleared/restored in
place — the 1.8 cross-test-pollution lesson.
"""
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

LAN = {'REMOTE_ADDR': '192.168.1.50'}

# The exact route surface 1.12 owns. A change here is intentional API churn and
# must be made deliberately — that is the point of pinning it.
EXPECTED_ROUTES = {
    '/api/agent/providers',
    '/api/agent/upload-image',
    '/api/agent/<provider>/auth-login',
    '/api/agent/<provider>/auth-logout',
    '/api/agent/<provider>/auth-probe',
    '/api/agent/<provider>/auth-status',
    '/api/agent/provider/<name>/auth',
    '/api/agent/provider/<name>/env',
    '/api/agent/provider/<name>/login-launch',
    '/api/claude/auth-probe',
    '/api/claude/auth-status',
    '/api/claude/login-launch',
    '/api/plan-file',
    '/api/plans/delete',
    '/api/recent-runs',
    '/api/router/stats',
    '/api/usage',
    '/api/project/<project_id>/agent/dispatch',
    '/api/project/<project_id>/agent/followup',
    '/api/project/<project_id>/agent/guardian-reset',
    '/api/project/<project_id>/agent/interrupt',
    '/api/project/<project_id>/agent/log',
    '/api/project/<project_id>/agent/plan-file',
    '/api/project/<project_id>/agent/send',
    '/api/project/<project_id>/agent/session',
    '/api/project/<project_id>/agent/status',
    '/api/project/<project_id>/agent/stop',
    '/api/project/<project_id>/agent/stream',
    '/api/project/<project_id>/conversations',
    '/api/project/<project_id>/plans',
    '/api/project/<project_id>/search-chats',
    '/api/project/<project_id>/session/<session_id>/reconstruct',
    '/api/project/<project_id>/transcript/<claude_session_id>',
    '/api/project/<project_id>/workflows',
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Flask test client; agent_routes global-scope deps patched on the MODULE."""
    import server  # noqa: F401  (registers the blueprint + runs wire() on import)
    from mc import state as mc_state
    from mc.blueprints import agent_routes as ar
    from mc.blueprints import local_auth as la

    # Deterministic gate: no LAN passcode configured this run (loopback exempt,
    # LAN rejected). Path points at a non-existent file.
    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')

    # Empty data dir so the usage/router-stats/recent-runs globs are clean.
    data_dir = tmp_path / 'projects'
    data_dir.mkdir()
    monkeypatch.setattr(ar, 'DATA_DIR', data_dir)

    # mc.state.agent_sessions is a shared object (blueprint imports it) —
    # snapshot, clear, restore IN PLACE; never rebind (split-brain).
    sess_snapshot = dict(mc_state.agent_sessions)
    mc_state.agent_sessions.clear()

    server.app.config['TESTING'] = True
    try:
        yield server.app.test_client()
    finally:
        mc_state.agent_sessions.clear()
        mc_state.agent_sessions.update(sess_snapshot)


# ── registration parity — the move's load-bearing guard ───────────────────────

def test_blueprint_registered(client):
    import server
    assert 'agent_routes' in server.app.blueprints


def test_all_expected_routes_present_under_blueprint(client):
    """Every 1.12 route exists AND is owned by the agent_routes blueprint."""
    import server
    owned = {
        rule.rule for rule in server.app.url_map.iter_rules()
        if rule.endpoint.startswith('agent_routes.')
    }
    missing = EXPECTED_ROUTES - owned
    assert not missing, f'routes missing from agent_routes blueprint: {sorted(missing)}'


def test_no_unexpected_agent_routes(client):
    """Pin the surface: a NEW route under the blueprint must be added to
    EXPECTED_ROUTES deliberately (guards accidental scope creep on re-merge)."""
    import server
    owned = {
        rule.rule for rule in server.app.url_map.iter_rules()
        if rule.endpoint.startswith('agent_routes.')
    }
    extra = owned - EXPECTED_ROUTES
    assert not extra, f'unpinned routes under agent_routes blueprint: {sorted(extra)}'


# ── read-only loopback smokes — prove wire() bound the global deps ────────────

def test_providers_endpoint_ok(client):
    resp = client.get('/api/agent/providers')
    assert resp.status_code == 200
    assert isinstance(resp.get_json(), (list, dict))


def test_usage_endpoint_ok_empty(client):
    """Clean data dir → usage responds 200 with the documented shape."""
    resp = client.get('/api/usage')
    assert resp.status_code == 200
    body = resp.get_json()
    assert 'total' in body and 'by_provider' in body
    assert body['total']['input_tokens'] == 0


def test_router_stats_endpoint_ok(client):
    resp = client.get('/api/router/stats')
    assert resp.status_code == 200
    assert isinstance(resp.get_json(), dict)


def test_recent_runs_endpoint_ok(client):
    resp = client.get('/api/recent-runs')
    assert resp.status_code == 200


# ── auth contract — the app-wide gate still covers the moved routes ───────────

def test_moved_route_behind_lan_gate(client):
    """A non-loopback peer with no passcode is 401'd BEFORE the handler runs —
    same contract the routes had while they lived in server.py."""
    resp = client.get('/api/usage', environ_overrides=LAN)
    assert resp.status_code == 401


# ── SSE cursor-overshoot guard (2026-06-11) ───────────────────────────────────
# log_lines can be rebuilt SHORTER under the same session_id (revive after a
# restart/purge reseeds from the transcript; the 2000→1500 cap slams the
# array). A client cursor beyond the array used to starve the stream forever —
# heartbeats only, chat frozen even in focus. The guard must emit a `reset`
# and replay from zero. These pull only the events yielded before the
# generator's first sleep, so they're fast and deterministic.

def _sse_events(resp, n):
    gen = resp.response
    out = []
    for _ in range(n):
        chunk = next(gen)
        if isinstance(chunk, bytes):
            chunk = chunk.decode('utf-8')
        out.append(json.loads(chunk[len('data: '):].strip()))
    return out


def _seed_stream_session(sid, lines):
    from mc import state as mc_state
    mc_state.agent_sessions[sid] = {
        'project_id': 'sse-test-proj', 'mode': 'B', 'status': 'running',
        'log_lines': list(lines),
    }


def test_stream_cursor_overshoot_resets_and_replays(client):
    """since > len(log_lines) → first event is `reset`, then the full replay."""
    _seed_stream_session('sse-overshoot', ['a', 'b', 'c'])
    resp = client.get(
        '/api/project/sse-test-proj/agent/stream?session=sse-overshoot&since=9999',
        buffered=False)
    try:
        evs = _sse_events(resp, 5)
    finally:
        resp.close()
    assert evs[0] == {'type': 'reset'}
    assert [e.get('text') for e in evs[1:4]] == ['a', 'b', 'c']
    assert all(e.get('type') == 'output' for e in evs[1:4])
    assert evs[4].get('type') == 'turn_start'


def test_stream_normal_cursor_no_reset(client):
    """since within bounds → no reset, delivery starts at the cursor."""
    _seed_stream_session('sse-normal', ['a', 'b', 'c'])
    resp = client.get(
        '/api/project/sse-test-proj/agent/stream?session=sse-normal&since=1',
        buffered=False)
    try:
        evs = _sse_events(resp, 3)
    finally:
        resp.close()
    assert [e.get('text') for e in evs[:2]] == ['b', 'c']
    assert evs[2].get('type') == 'turn_start'


def test_stream_exact_cursor_no_reset(client):
    """since == len(log_lines) (in sync) → no reset, no replay."""
    _seed_stream_session('sse-exact', ['a', 'b', 'c'])
    resp = client.get(
        '/api/project/sse-test-proj/agent/stream?session=sse-exact&since=3',
        buffered=False)
    try:
        evs = _sse_events(resp, 1)
    finally:
        resp.close()
    assert evs[0].get('type') == 'turn_start'
