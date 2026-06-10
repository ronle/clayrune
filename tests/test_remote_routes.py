"""Request-level tests for the remote family (mc/blueprints/remote_routes.py).

Added with blueprint step 1.7 (MODERNIZATION_PLAN.md Phase 5): happy path,
auth-rejected path, malformed-input path. Deterministic by construction:

- Only read-only / local-store endpoints are exercised. The CP-proxy and
  tunnel-mutating endpoints (enable/disable/resume/devices/sessions/revoke/
  enforce POST/mc-callback) are deliberately NOT hit — on an enrolled dev
  machine they reach the real control plane via the OS-keystore identity
  (keyring is user-level, NOT MC_DATA_DIR-scoped), and /api/remote/enable
  launches a browser.
- CF_ACCESS_TEAM_DOMAIN/AUD are cleared so _cf_jwt_verified short-circuits
  to None (no JWKS network fetch) and the loopback-gated trust applies.
- SESSION_LABELS_PATH is patched on the BLUEPRINT module (the single source
  of truth post-1.7 — Phase-0 test-port landmine), isolated to tmp_path.

A CF-"tunneled" request = loopback peer (test-client default REMOTE_ADDR
127.0.0.1) + a Cf-Access-Jwt-Assertion header; the JWT payload only needs a
decodable `nonce` claim (signature is not checked when verification is
unconfigured — the tunnel is the auth boundary).
"""
import base64
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _fake_cf_jwt(nonce: str) -> str:
    """JWT-shaped token whose payload carries a nonce claim (unsigned)."""
    def seg(obj) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b'=').decode()
    return f"{seg({'alg': 'none', 'kid': 'test'})}.{seg({'nonce': nonce})}.sig"


def _cf_headers(nonce: str) -> dict:
    return {'Cf-Access-Jwt-Assertion': _fake_cf_jwt(nonce)}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Flask test client with the session-labels store isolated to tmp_path."""
    import server
    from mc.blueprints import remote_routes as rr
    monkeypatch.setattr(rr, 'SESSION_LABELS_PATH', tmp_path / 'session_labels.json')
    # No JWT signature verification in tests → no JWKS network fetch.
    monkeypatch.delenv('CF_ACCESS_TEAM_DOMAIN', raising=False)
    monkeypatch.delenv('CF_ACCESS_AUD', raising=False)
    server.app.config['TESTING'] = True
    return server.app.test_client()


class TestRemoteStatus:
    def test_status_always_200_with_provider_key(self, client):
        # provider: null (open-source build) or a provider dict (mc_remote
        # bundled in this repo) — both are contract-valid; never 5xx.
        r = client.get('/api/remote/status')
        assert r.status_code == 200
        assert 'provider' in r.get_json()


class TestEnforcerState:
    def test_state_shape(self, client):
        r = client.get('/api/remote/sessions/enforcer-state')
        assert r.status_code == 200
        j = r.get_json()
        assert {'last_run', 'last_revoked_count', 'last_skipped_count',
                'last_error', 'last_per_session_supported'} <= set(j.keys())


class TestNameDevicePage:
    def test_page_renders_standalone_html(self, client):
        r = client.get('/_mc/name-device')
        assert r.status_code == 200
        assert b'Name this device' in r.data
        assert r.headers.get('Cache-Control') == 'no-store'


class TestSessionLabelEndpoint:
    def test_untunneled_post_rejected_403(self, client):
        r = client.post('/api/_mc/session-label',
                        json={'nonce': 'n1', 'label': 'My Phone'})
        assert r.status_code == 403
        assert r.get_json()['ok'] is False

    def test_tunneled_missing_label_400(self, client):
        r = client.post('/api/_mc/session-label', json={'nonce': 'n1'},
                        headers=_cf_headers('n1'))
        assert r.status_code == 400
        assert r.get_json()['message'] == 'Label required'

    def test_tunneled_post_persists_label(self, client):
        from mc.blueprints import remote_routes as rr
        r = client.post('/api/_mc/session-label',
                        json={'nonce': 'nonce-abc', 'label': 'My Phone'},
                        headers=_cf_headers('nonce-abc'))
        assert r.status_code == 200
        assert r.get_json() == {'ok': True, 'nonce': 'nonce-abc', 'label': 'My Phone'}
        assert rr._load_session_labels()['nonce-abc']['label'] == 'My Phone'

    def test_tunneled_post_falls_back_to_jwt_nonce(self, client):
        from mc.blueprints import remote_routes as rr
        r = client.post('/api/_mc/session-label', json={'label': 'Tablet'},
                        headers=_cf_headers('jwt-nonce-7'))
        assert r.status_code == 200
        assert r.get_json()['nonce'] == 'jwt-nonce-7'
        assert rr._load_session_labels()['jwt-nonce-7']['label'] == 'Tablet'


class TestRetroactiveSessionLabel:
    """Local-only /api/remote/sessions/<sid>/label (no CF requirement)."""

    def test_unparseable_session_id_400(self, client):
        r = client.post('/api/remote/sessions/not-a-cf-id/label',
                        json={'label': 'Desk'})
        assert r.status_code == 400
        assert 'nonce' in r.get_json()['message']

    def test_missing_label_400(self, client):
        r = client.post('/api/remote/sessions/acct_user_sessions_xyz/label',
                        json={})
        assert r.status_code == 400

    def test_happy_path_extracts_nonce_and_persists(self, client):
        from mc.blueprints import remote_routes as rr
        r = client.post('/api/remote/sessions/acct_user_sessions_xyz9/label',
                        json={'label': 'Work Laptop'})
        assert r.status_code == 200
        assert r.get_json() == {'ok': True, 'nonce': 'xyz9', 'label': 'Work Laptop'}
        assert rr._load_session_labels()['xyz9']['label'] == 'Work Laptop'


class TestRedirectUnlabeledHook:
    """The _redirect_unlabeled_cf_session before_request hook (body in the
    blueprint, thin wrapper on `app`)."""

    def test_untunneled_request_not_redirected(self, client):
        r = client.get('/')
        assert r.status_code == 200

    def test_tunneled_unlabeled_redirects_to_name_device(self, client):
        r = client.get('/', headers=_cf_headers('fresh-nonce'))
        assert r.status_code == 302
        assert r.headers['Location'].endswith('/_mc/name-device')

    def test_api_paths_exempt_from_redirect(self, client):
        # Unknown API path + tunneled: the hook must not 302 — Flask 404s.
        r = client.get('/api/_hook_probe_does_not_exist',
                       headers=_cf_headers('fresh-nonce'))
        assert r.status_code == 404

    def test_labeled_session_passes_through(self, client):
        h = _cf_headers('known-nonce')
        r = client.post('/api/_mc/session-label', json={'label': 'My Mac'},
                        headers=h)
        assert r.status_code == 200
        r = client.get('/', headers=h)
        assert r.status_code == 200
