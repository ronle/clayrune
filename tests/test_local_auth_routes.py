"""Request-level tests for the LAN passcode gate (mc/blueprints/local_auth.py).

Added with blueprint step 1.1 (MODERNIZATION_PLAN.md Phase 5): happy path,
auth-rejected path, malformed-input path. The Flask test client's default
REMOTE_ADDR is 127.0.0.1 (loopback-exempt = "the host"); LAN devices are
simulated via environ_base REMOTE_ADDR overrides.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

LAN = {'REMOTE_ADDR': '192.168.1.50'}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Flask test client with local-auth storage isolated to tmp_path.

    Patches the BLUEPRINT module's globals (mc.blueprints.local_auth), not
    server.py's — after 1.1 the module is the single source of truth
    (docs/_tracks/backend_progress.md Phase-1 landmine note).
    """
    import server
    from mc.blueprints import local_auth as la
    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')
    monkeypatch.setattr(la, '_LOCAL_AUTH_FAILS', {})
    server.app.config['TESTING'] = True
    return server.app.test_client()


class TestExemptHost:
    def test_status_shape_and_exemption(self, client):
        r = client.get('/api/local-auth/status')
        assert r.status_code == 200
        j = r.get_json()
        assert j == {'configured': False, 'exempt': True, 'authed': True}

    def test_host_never_gated_even_unconfigured(self, client):
        # Unknown API path: the gate lets loopback through → Flask 404,
        # NOT the gate's 401.
        r = client.get('/api/_gate_probe_does_not_exist')
        assert r.status_code == 404


class TestLanLockedNoPasscode:
    def test_api_rejected_401_locked(self, client):
        r = client.get('/api/_gate_probe_does_not_exist', environ_base=LAN)
        assert r.status_code == 401
        assert r.get_json() == {'error': 'auth_required', 'auth_state': 'locked'}

    def test_page_redirects_to_locked(self, client):
        r = client.get('/', environ_base=LAN)
        assert r.status_code == 302
        assert r.headers['Location'].endswith('/_mc/local-locked')

    def test_locked_page_reachable_while_locked(self, client):
        r = client.get('/_mc/local-locked', environ_base=LAN)
        assert r.status_code == 200
        assert b'Clayrune' in r.data

    def test_lan_cannot_bootstrap_passcode(self, client):
        r = client.post('/api/local-auth/set', json={'passcode': 'hunter22'},
                        environ_base=LAN)
        assert r.status_code == 403
        assert r.get_json()['error'] == 'setup_requires_host'


class TestPasscodeLifecycle:
    def test_set_login_and_gate_pass(self, client):
        # Malformed: too short → 400 (host context).
        r = client.post('/api/local-auth/set', json={'passcode': 'abc'})
        assert r.status_code == 400
        assert r.get_json()['error'] == 'passcode_too_short'

        # Host sets the first passcode → ok + cookie.
        r = client.post('/api/local-auth/set', json={'passcode': 'hunter22'})
        assert r.status_code == 200
        assert r.get_json() == {'ok': True, 'configured': True}

        # The set response logged the HOST in (cookie in the shared test-client
        # jar). A real LAN device has no such cookie — drop it before
        # impersonating one.
        client.delete_cookie('mc_local_auth')

        # LAN now sees 'login' state on API, and the login page on /.
        r = client.get('/api/_gate_probe_does_not_exist', environ_base=LAN)
        assert r.status_code == 401
        assert r.get_json()['auth_state'] == 'login'
        r = client.get('/', environ_base=LAN)
        assert r.status_code == 302
        assert r.headers['Location'].endswith('/_mc/local-login')

        # Wrong passcode → 403; right passcode → ok + auth cookie unlocks the gate.
        r = client.post('/api/local-auth/login', json={'passcode': 'nope-nope'},
                        environ_base=LAN)
        assert r.status_code == 403
        assert r.get_json()['error'] == 'bad_passcode'
        r = client.post('/api/local-auth/login', json={'passcode': 'hunter22'},
                        environ_base=LAN)
        assert r.status_code == 200
        assert 'mc_local_auth' in r.headers.get('Set-Cookie', '')
        # Cookie jar now carries the auth cookie → unknown API path = 404 (past gate).
        r = client.get('/api/_gate_probe_does_not_exist', environ_base=LAN)
        assert r.status_code == 404

    def test_login_unconfigured_400(self, client):
        r = client.post('/api/local-auth/login', json={'passcode': 'whatever'},
                        environ_base=LAN)
        assert r.status_code == 400
        assert r.get_json()['error'] == 'not_configured'

    def test_throttle_429_after_cap(self, client):
        client.post('/api/local-auth/set', json={'passcode': 'hunter22'})
        from mc.blueprints import local_auth as la
        for _ in range(la._LOCAL_AUTH_FAIL_CAP):
            r = client.post('/api/local-auth/login', json={'passcode': 'bad'},
                            environ_base=LAN)
            assert r.status_code == 403
        r = client.post('/api/local-auth/login', json={'passcode': 'hunter22'},
                        environ_base=LAN)
        assert r.status_code == 429
        assert r.get_json()['error'] == 'too_many_attempts'
