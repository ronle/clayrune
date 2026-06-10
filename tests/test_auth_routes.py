"""Test suite for auth route generalization (ws_004).

Route test matrix:
  - Old shim /api/claude/auth-status returns identical payload to /api/agent/claude/auth-status
  - Old shim /api/claude/auth-probe returns identical payload to /api/agent/claude/auth-probe
  - /api/agent/gemini/auth-status returns a well-formed response
  - /api/agent/gemini/auth-probe returns a well-formed response
  - Unknown provider returns 404
  - /api/agent/<provider>/auth-logout returns a well-formed response
"""
import json
import sys
import threading
import time as _time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import agent_runtime as _ar


# ── Helpers ──────────────────────────────────────────────────────────────────

def _get_flask_client():
    """Import server and return a Flask test client.

    Importing server.py registers all routes and hooks; we do it lazily inside
    each test function (or fixture) so the module-level state is fresh.
    """
    import server
    server.app.config['TESTING'] = True
    return server.app.test_client(), server


@pytest.fixture()
def client():
    """Flask test client with a fresh import of server."""
    c, srv = _get_flask_client()
    return c, srv


# ── AgentRuntime unit tests ───────────────────────────────────────────────────


class TestAgentRuntimeABC:
    def test_claude_auth_status_fallback(self):
        """ClaudeRuntime.auth_status() returns a well-formed dict without hooks."""
        rt = _ar.ClaudeRuntime()
        old_hook = _ar._CLAUDE_HOOKS.pop('auth_status', None)
        try:
            result = rt.auth_status()
            assert isinstance(result, dict)
            assert 'ok' in result
            assert result['ok'] is True
        finally:
            if old_hook is not None:
                _ar._CLAUDE_HOOKS['auth_status'] = old_hook

    def test_claude_auth_status_uses_hook(self):
        """ClaudeRuntime.auth_status() delegates to the registered hook."""
        rt = _ar.ClaudeRuntime()
        fake = {'ok': False, 'reason': 'not_logged_in', 'last_error_text': 'Please run /login',
                'detected_at': 1000.0, 'last_probe_at': None}
        _ar._CLAUDE_HOOKS['auth_status'] = lambda: dict(fake)
        try:
            result = rt.auth_status()
            assert result == fake
        finally:
            _ar._CLAUDE_HOOKS.pop('auth_status', None)

    def test_claude_auth_probe_uses_hook(self):
        """ClaudeRuntime.auth_probe() delegates to the registered hook."""
        rt = _ar.ClaudeRuntime()
        fake = {'ok': True, 'reason': None, 'last_error_text': None,
                'detected_at': None, 'last_probe_at': _time.time()}
        _ar._CLAUDE_HOOKS['auth_probe'] = lambda: dict(fake)
        try:
            result = rt.auth_probe()
            assert result == fake
        finally:
            _ar._CLAUDE_HOOKS.pop('auth_probe', None)

    def test_claude_auth_logout_not_supported(self):
        """ClaudeRuntime.auth_logout() returns ok=False (no programmatic logout)."""
        rt = _ar.ClaudeRuntime()
        result = rt.auth_logout()
        assert result['ok'] is False
        assert 'error' in result

    def test_gemini_auth_status_default(self):
        """GeminiRuntime.auth_status() returns cached state (unknown initially)."""
        rt = _ar.GeminiRuntime()
        result = rt.auth_status()
        assert isinstance(result, dict)
        assert 'ok' in result
        assert 'status' in result

    def test_gemini_auth_probe_no_key(self):
        """GeminiRuntime.auth_probe() returns not_logged_in when GEMINI_API_KEY unset."""
        rt = _ar.GeminiRuntime()
        with patch.dict('os.environ', {}, clear=True):
            # Also patch resolve_binary to return a fake path so we don't skip to not_installed
            with patch.object(rt, 'resolve_binary', return_value=Path('/fake/gemini')):
                result = rt.auth_probe()
        assert result['ok'] is False
        assert result['status'] == 'not_logged_in'
        assert 'GEMINI_API_KEY' in result['error_text']

    def test_gemini_auth_probe_with_key(self):
        """GeminiRuntime.auth_probe() returns ok when GEMINI_API_KEY is set."""
        rt = _ar.GeminiRuntime()
        with patch.dict('os.environ', {'GEMINI_API_KEY': 'fake-key-123'}):
            with patch.object(rt, 'resolve_binary', return_value=Path('/fake/gemini')):
                result = rt.auth_probe()
        assert result['ok'] is True
        assert result['status'] == 'ok'
        assert result['method'] == 'env:GEMINI_API_KEY'

    def test_gemini_auth_probe_not_installed(self):
        """GeminiRuntime.auth_probe() returns not_installed when binary missing."""
        rt = _ar.GeminiRuntime()
        with patch.object(rt, 'resolve_binary', return_value=None):
            result = rt.auth_probe()
        assert result['ok'] is False
        assert result['status'] == 'not_installed'

    def test_gemini_auth_probe_updates_cache(self):
        """auth_probe() updates the internal cache so auth_status() reflects it."""
        rt = _ar.GeminiRuntime()
        with patch.dict('os.environ', {'GEMINI_API_KEY': 'k'}):
            with patch.object(rt, 'resolve_binary', return_value=Path('/fake/gemini')):
                rt.auth_probe()
        assert rt._auth_cache['status'] == 'ok'
        assert rt.auth_status()['status'] == 'ok'

    def test_abc_auth_logout_default(self):
        """ABC default auth_logout returns ok=False for providers that override nothing."""
        # GeminiRuntime doesn't override auth_logout — uses ABC default
        rt = _ar.GeminiRuntime()
        result = rt.auth_logout()
        assert result['ok'] is False
        assert 'error' in result


# ── Flask route tests ─────────────────────────────────────────────────────────


class TestAuthRoutes:
    """Route-level tests via Flask test client."""

    def _patch_claude_state(self, srv, state):
        """Patch _claude_auth_state in the server module."""
        srv._claude_auth_state.clear()
        srv._claude_auth_state.update(state)
        # Re-register hook so the generic route picks up the patched state
        _ar._CLAUDE_HOOKS['auth_status'] = lambda: dict(srv._claude_auth_state)

    def test_shim_vs_generic_auth_status_identical(self):
        """Old /api/claude/auth-status and new /api/agent/claude/auth-status return same payload."""
        c, srv = _get_flask_client()
        test_state = {
            'ok': False,
            'reason': 'not_logged_in',
            'last_error_text': 'Please run /login',
            'detected_at': 12345.0,
            'last_probe_at': None,
        }
        self._patch_claude_state(srv, test_state)

        resp_shim = c.get('/api/claude/auth-status')
        resp_new = c.get('/api/agent/claude/auth-status')

        assert resp_shim.status_code == 200
        assert resp_new.status_code == 200

        payload_shim = json.loads(resp_shim.data)
        payload_new = json.loads(resp_new.data)

        assert payload_shim == payload_new, (
            f"Shim and generic route payloads differ:\n"
            f"  shim: {payload_shim}\n"
            f"  new:  {payload_new}"
        )

    def test_shim_vs_generic_auth_probe_identical(self):
        """Old /api/claude/auth-probe and new /api/agent/claude/auth-probe share same logic."""
        c, srv = _get_flask_client()

        # Mock _run_claude_auth_probe to avoid actual subprocess
        probe_result = {
            'ok': True,
            'reason': None,
            'last_error_text': None,
            'detected_at': None,
            'last_probe_at': _time.time(),
        }
        _ar._CLAUDE_HOOKS['auth_probe'] = lambda: dict(probe_result)

        resp_shim = c.post('/api/claude/auth-probe')
        resp_new = c.post('/api/agent/claude/auth-probe')

        assert resp_shim.status_code == 200
        assert resp_new.status_code == 200

        payload_shim = json.loads(resp_shim.data)
        payload_new = json.loads(resp_new.data)

        assert payload_shim == payload_new

    def test_unknown_provider_returns_404(self):
        """Unknown provider names return 404 on all generic auth routes."""
        c, _ = _get_flask_client()
        for path in [
            '/api/agent/nonexistent/auth-status',
            '/api/agent/nonexistent/auth-probe',
            '/api/agent/nonexistent/auth-logout',
        ]:
            method = 'GET' if path.endswith('auth-status') else 'POST'
            resp = c.open(path, method=method)
            assert resp.status_code == 404, f"{path} should return 404, got {resp.status_code}"
            body = json.loads(resp.data)
            assert 'error' in body

    def test_unknown_provider_auth_login_returns_404(self):
        """auth-login with unknown provider returns 404."""
        c, _ = _get_flask_client()
        resp = c.post('/api/agent/nonexistent/auth-login')
        assert resp.status_code == 404
        assert 'error' in json.loads(resp.data)

    def test_gemini_auth_status_well_formed(self):
        """GET /api/agent/gemini/auth-status returns a well-formed JSON response."""
        c, _ = _get_flask_client()
        resp = c.get('/api/agent/gemini/auth-status')
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert 'ok' in body
        assert isinstance(body['ok'], bool)

    def test_gemini_auth_probe_well_formed(self):
        """POST /api/agent/gemini/auth-probe returns a well-formed JSON response."""
        c, _ = _get_flask_client()
        with patch.object(_ar.GeminiRuntime, 'resolve_binary', return_value=None):
            resp = c.post('/api/agent/gemini/auth-probe')
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert 'ok' in body
        assert body['ok'] is False
        assert 'status' in body
        assert body['status'] == 'not_installed'

    def test_gemini_auth_probe_with_key_well_formed(self):
        """Gemini auth-probe with GEMINI_API_KEY set returns ok=True."""
        c, _ = _get_flask_client()
        with patch.dict('os.environ', {'GEMINI_API_KEY': 'test-key-abc'}):
            with patch.object(_ar.GeminiRuntime, 'resolve_binary',
                               return_value=Path('/fake/gemini')):
                resp = c.post('/api/agent/gemini/auth-probe')
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert body['ok'] is True
        assert body['status'] == 'ok'
        assert body['method'] == 'env:GEMINI_API_KEY'

    def test_claude_auth_logout_not_supported(self):
        """POST /api/agent/claude/auth-logout returns ok=False (no programmatic logout)."""
        c, _ = _get_flask_client()
        resp = c.post('/api/agent/claude/auth-logout')
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert body['ok'] is False
        assert 'error' in body

    def test_gemini_auth_logout_not_supported(self):
        """POST /api/agent/gemini/auth-logout returns ok=False (ABC default)."""
        c, _ = _get_flask_client()
        resp = c.post('/api/agent/gemini/auth-logout')
        assert resp.status_code == 200
        body = json.loads(resp.data)
        assert body['ok'] is False
        assert 'error' in body

    def test_claude_login_launch_shim_calls_generic(self):
        """POST /api/claude/login-launch delegates to agent_auth_login('claude')."""
        c, _ = _get_flask_client()
        # Patch _launch_terminal_for_binary to avoid OS interaction.
        # It moved to the agent_routes blueprint (1.12); the login-launch route
        # calls ITS module-local copy, so patch there (not server).
        from mc.blueprints import agent_routes as _bp_agent
        with patch.object(_bp_agent, '_launch_terminal_for_binary', return_value=None):
            with patch.object(_ar.ClaudeRuntime, 'resolve_binary',
                               return_value=Path('/fake/claude')):
                resp = c.post('/api/claude/login-launch')
        assert resp.status_code == 200
        assert json.loads(resp.data)['ok'] is True

    def test_agent_auth_login_binary_missing_returns_400(self):
        """auth-login returns 400 when provider binary is not installed."""
        c, _ = _get_flask_client()
        with patch.object(_ar.GeminiRuntime, 'resolve_binary', return_value=None):
            resp = c.post('/api/agent/gemini/auth-login')
        assert resp.status_code == 400
        assert 'error' in json.loads(resp.data)
