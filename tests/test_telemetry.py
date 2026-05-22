"""Tests for ws_006: Telemetry + /api/usage per-provider refactor.

Covers:
  - agent_log migration: missing provider field stamped as 'claude'
  - /api/usage contract: claude-only totals match legacy; mixed-provider breakdown correct
  - _session_usage_payload: gemini session omits cost_usd/num_turns; claude keeps them
  - SSE payload shape: verifies gating helpers return the right keys

These tests are standalone — no running server required.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_log_entry(provider=None, input_tokens=100, output_tokens=50,
                    cost_usd=0.005, num_turns=3, ts='2026-05-21T10:00:00Z'):
    entry = {
        'ts': ts,
        'status': 'completed',
        'usage': {'input_tokens': input_tokens, 'output_tokens': output_tokens},
        'cost_usd': cost_usd,
        'num_turns': num_turns,
    }
    if provider is not None:
        entry['provider'] = provider
    return entry


# ── Migration tests ────────────────────────────────────────────────────────────


class TestAgentLogMigration:
    """_migrate_agent_log_provider_field() stamps missing provider as 'claude'."""

    def _import_server_funcs(self, tmp_data_dir, monkeypatch):
        """Import the migration function with DATA_DIR pointed at tmp_data_dir."""
        monkeypatch.setenv('MC_DATA_DIR', str(tmp_data_dir))
        monkeypatch.setenv('MC_PORT', '0')
        # Import fresh copy each test to pick up env var
        if 'server' in sys.modules:
            del sys.modules['server']
        import server
        return server

    def test_stamps_missing_provider(self, tmp_path, monkeypatch):
        data_dir = tmp_path / 'data'
        data_dir.mkdir()
        log_file = data_dir / 'proj1_agent_log.json'
        entries = [
            _make_log_entry(provider=None, ts='2026-01-01T00:00:00Z'),
            _make_log_entry(provider=None, ts='2026-01-02T00:00:00Z'),
        ]
        log_file.write_text(json.dumps(entries), encoding='utf-8')

        monkeypatch.setenv('MC_DATA_DIR', str(data_dir))
        monkeypatch.setenv('MC_PORT', '0')
        if 'server' in sys.modules:
            del sys.modules['server']
        import server
        server.DATA_DIR = data_dir

        server._migrate_agent_log_provider_field()

        result = json.loads(log_file.read_text(encoding='utf-8'))
        assert all(e['provider'] == 'claude' for e in result), \
            "All migrated entries must have provider='claude'"

    def test_skips_already_stamped(self, tmp_path, monkeypatch):
        data_dir = tmp_path / 'data'
        data_dir.mkdir()
        log_file = data_dir / 'proj1_agent_log.json'
        entries = [
            _make_log_entry(provider='claude'),
            _make_log_entry(provider='gemini'),
        ]
        original_text = json.dumps(entries)
        log_file.write_text(original_text, encoding='utf-8')

        monkeypatch.setenv('MC_DATA_DIR', str(data_dir))
        monkeypatch.setenv('MC_PORT', '0')
        if 'server' in sys.modules:
            del sys.modules['server']
        import server
        server.DATA_DIR = data_dir

        server._migrate_agent_log_provider_field()

        result = json.loads(log_file.read_text(encoding='utf-8'))
        # Values must not have changed
        assert result[0]['provider'] == 'claude'
        assert result[1]['provider'] == 'gemini'

    def test_idempotent(self, tmp_path, monkeypatch):
        data_dir = tmp_path / 'data'
        data_dir.mkdir()
        log_file = data_dir / 'proj1_agent_log.json'
        entries = [_make_log_entry(provider=None)]
        log_file.write_text(json.dumps(entries), encoding='utf-8')

        monkeypatch.setenv('MC_DATA_DIR', str(data_dir))
        monkeypatch.setenv('MC_PORT', '0')
        if 'server' in sys.modules:
            del sys.modules['server']
        import server
        server.DATA_DIR = data_dir

        server._migrate_agent_log_provider_field()
        server._migrate_agent_log_provider_field()  # second call must be safe

        result = json.loads(log_file.read_text(encoding='utf-8'))
        assert result[0]['provider'] == 'claude'


# ── /api/usage contract tests ─────────────────────────────────────────────────


def _build_mock_runtime(name, emits_usage=True, emits_cost=True, emits_num_turns=True):
    """Build a minimal mock AgentRuntime for the given provider name."""
    import agent_runtime
    caps = agent_runtime.ProviderCapabilities(
        name=name,
        display_name=name.capitalize(),
        emits_usage=emits_usage,
        emits_cost=emits_cost,
        emits_num_turns=emits_num_turns,
    )
    rt = MagicMock()
    rt.capabilities.return_value = caps
    rt.name = name
    return rt


class TestApiUsageContract:
    """The /api/usage endpoint must return the right shape."""

    def _setup(self, tmp_path, monkeypatch):
        data_dir = tmp_path / 'data'
        data_dir.mkdir()
        monkeypatch.setenv('MC_DATA_DIR', str(data_dir))
        monkeypatch.setenv('MC_PORT', '0')
        if 'server' in sys.modules:
            del sys.modules['server']
        import server
        server.DATA_DIR = data_dir
        return server, data_dir

    def _write_log(self, data_dir, project_id, entries):
        f = data_dir / f'{project_id}_agent_log.json'
        f.write_text(json.dumps(entries), encoding='utf-8')

    def test_claude_only_totals_match_legacy(self, tmp_path, monkeypatch):
        """Claude-only deployment: legacy flat fields must be identical to totals."""
        server, data_dir = self._setup(tmp_path, monkeypatch)
        entries = [
            _make_log_entry(provider='claude', input_tokens=100, output_tokens=50, cost_usd=0.01),
            _make_log_entry(provider='claude', input_tokens=200, output_tokens=80, cost_usd=0.02),
        ]
        self._write_log(data_dir, 'proj1', entries)

        with server.app.test_client() as client:
            resp = client.get('/api/usage')
        assert resp.status_code == 200
        data = resp.get_json()

        # Legacy flat fields
        assert data['input_tokens'] == 300
        assert data['output_tokens'] == 130
        assert data['total_tokens'] == 430
        assert abs(data['cost_usd'] - 0.03) < 0.001
        assert data['total_sessions'] == 2

        # New shape must also be present
        assert 'by_provider' in data
        assert 'total' in data

        # total must match flat fields
        assert data['total']['input_tokens'] == data['input_tokens']
        assert data['total']['output_tokens'] == data['output_tokens']
        assert data['total']['total_tokens'] == data['total_tokens']
        assert data['total']['cost_usd'] == data['cost_usd']

        # claude bucket must carry all the data
        claude = data['by_provider']['claude']
        assert claude['input_tokens'] == 300
        assert claude['output_tokens'] == 130
        assert abs(claude['cost_usd'] - 0.03) < 0.001

    def test_mixed_provider_breakdown(self, tmp_path, monkeypatch):
        """Mixed claude+gemini deployment: each provider gets its own bucket."""
        server, data_dir = self._setup(tmp_path, monkeypatch)
        claude_entries = [
            _make_log_entry(provider='claude', input_tokens=100, output_tokens=50, cost_usd=0.01),
        ]
        gemini_entries = [
            _make_log_entry(provider='gemini', input_tokens=200, output_tokens=90, cost_usd=None),
        ]
        self._write_log(data_dir, 'proj1', claude_entries)
        self._write_log(data_dir, 'proj2', gemini_entries)

        # Mock gemini runtime to report emits_cost=False
        import agent_runtime as ar
        orig_get_runtime = ar.get_runtime

        def _mock_get_runtime(name):
            if name == 'gemini':
                return _build_mock_runtime('gemini', emits_usage=False, emits_cost=False,
                                           emits_num_turns=False)
            return orig_get_runtime(name)

        monkeypatch.setattr(ar, 'get_runtime', _mock_get_runtime)
        # Also patch in server module's reference
        monkeypatch.setattr(server._agent_runtime, 'get_runtime', _mock_get_runtime)

        with server.app.test_client() as client:
            resp = client.get('/api/usage')
        assert resp.status_code == 200
        data = resp.get_json()

        assert 'claude' in data['by_provider']
        assert 'gemini' in data['by_provider']

        claude = data['by_provider']['claude']
        assert claude['input_tokens'] == 100
        assert claude['cost_usd'] is not None

        gemini = data['by_provider']['gemini']
        assert gemini['input_tokens'] == 200
        # gemini does not emit cost — cost_usd should be null (None in Python)
        assert gemini['cost_usd'] is None

        # Grand total still includes claude cost
        assert abs(data['total']['cost_usd'] - 0.01) < 0.001
        # total tokens = both providers
        assert data['total']['total_tokens'] == 440


# ── _session_usage_payload tests ──────────────────────────────────────────────


class TestSessionUsagePayload:
    """_session_usage_payload must gate fields on runtime capabilities."""

    def _setup(self, monkeypatch):
        monkeypatch.setenv('MC_DATA_DIR', '/tmp/test_telemetry_data')
        monkeypatch.setenv('MC_PORT', '0')
        if 'server' in sys.modules:
            del sys.modules['server']
        import server
        return server

    def test_claude_session_includes_all_fields(self, tmp_path, monkeypatch):
        monkeypatch.setenv('MC_DATA_DIR', str(tmp_path))
        monkeypatch.setenv('MC_PORT', '0')
        if 'server' in sys.modules:
            del sys.modules['server']
        import server

        session = {
            'provider': 'claude',
            'usage': {'input_tokens': 100, 'output_tokens': 50},
            'cost_usd': 0.005,
            'num_turns': 3,
        }
        payload = server._session_usage_payload(session)
        assert 'usage' in payload
        assert 'cost_usd' in payload
        assert 'num_turns' in payload
        assert payload['cost_usd'] == 0.005
        assert payload['num_turns'] == 3

    def test_gemini_session_omits_cost_and_turns(self, tmp_path, monkeypatch):
        monkeypatch.setenv('MC_DATA_DIR', str(tmp_path))
        monkeypatch.setenv('MC_PORT', '0')
        if 'server' in sys.modules:
            del sys.modules['server']
        import server
        import agent_runtime as ar

        orig = ar.get_runtime

        def _mock(name):
            if name == 'gemini':
                return _build_mock_runtime('gemini', emits_usage=False,
                                           emits_cost=False, emits_num_turns=False)
            return orig(name)

        monkeypatch.setattr(ar, 'get_runtime', _mock)
        monkeypatch.setattr(server._agent_runtime, 'get_runtime', _mock)

        session = {
            'provider': 'gemini',
            'usage': {'input_tokens': 100},
            'cost_usd': 0.0,   # server may have 0 from init; must NOT be emitted
            'num_turns': 5,    # same
        }
        payload = server._session_usage_payload(session)
        # Gemini emits_usage=False, emits_cost=False, emits_num_turns=False
        assert 'cost_usd' not in payload, "gemini must not emit cost_usd"
        assert 'num_turns' not in payload, "gemini must not emit num_turns"
        assert 'usage' not in payload, "gemini must not emit usage (emits_usage=False)"

    def test_unknown_provider_falls_back_to_claude_caps(self, tmp_path, monkeypatch):
        """If runtime lookup fails for unknown provider, fall back to claude caps."""
        monkeypatch.setenv('MC_DATA_DIR', str(tmp_path))
        monkeypatch.setenv('MC_PORT', '0')
        if 'server' in sys.modules:
            del sys.modules['server']
        import server
        import agent_runtime as ar

        orig = ar.get_runtime

        def _mock(name):
            if name == 'unknown_provider_xyz':
                raise KeyError(name)
            return orig(name)

        monkeypatch.setattr(ar, 'get_runtime', _mock)
        monkeypatch.setattr(server._agent_runtime, 'get_runtime', _mock)

        session = {
            'provider': 'unknown_provider_xyz',
            'usage': {'input_tokens': 100},
            'cost_usd': 0.002,
            'num_turns': 1,
        }
        # Should not raise; falls back to claude capabilities
        payload = server._session_usage_payload(session)
        # Claude emits all three
        assert 'usage' in payload
        assert 'cost_usd' in payload
        assert 'num_turns' in payload

    def test_missing_provider_defaults_to_claude(self, tmp_path, monkeypatch):
        """Session with no provider key is treated as claude."""
        monkeypatch.setenv('MC_DATA_DIR', str(tmp_path))
        monkeypatch.setenv('MC_PORT', '0')
        if 'server' in sys.modules:
            del sys.modules['server']
        import server

        session = {
            # no 'provider' key — legacy session
            'usage': {'input_tokens': 50},
            'cost_usd': 0.001,
            'num_turns': 2,
        }
        payload = server._session_usage_payload(session)
        assert 'usage' in payload
        assert 'cost_usd' in payload
        assert 'num_turns' in payload


# ── SSE JSON payload structure test ──────────────────────────────────────────


class TestSsePayloadStructure:
    """Verify that the SSE dict construction produces clean output."""

    def test_gemini_sse_payload_excludes_cost(self, tmp_path, monkeypatch):
        """Simulates what the SSE generator would emit for a gemini session."""
        monkeypatch.setenv('MC_DATA_DIR', str(tmp_path))
        monkeypatch.setenv('MC_PORT', '0')
        if 'server' in sys.modules:
            del sys.modules['server']
        import server
        import agent_runtime as ar

        orig = ar.get_runtime

        def _mock(name):
            if name == 'gemini':
                return _build_mock_runtime('gemini', emits_usage=False,
                                           emits_cost=False, emits_num_turns=False)
            return orig(name)

        monkeypatch.setattr(ar, 'get_runtime', _mock)
        monkeypatch.setattr(server._agent_runtime, 'get_runtime', _mock)

        session = {
            'provider': 'gemini',
            'usage': {},
            'cost_usd': 0.0,
            'num_turns': 0,
        }

        # This mirrors the actual SSE generator expression:
        event = {'type': 'turn_complete', 'status': 'idle', **server._session_usage_payload(session)}
        serialized = json.dumps(event)
        parsed = json.loads(serialized)

        assert 'cost_usd' not in parsed, "SSE turn_complete for gemini must not contain cost_usd"
        assert 'num_turns' not in parsed, "SSE turn_complete for gemini must not contain num_turns"
        assert 'type' in parsed
        assert 'status' in parsed

    def test_claude_sse_payload_includes_all(self, tmp_path, monkeypatch):
        """Claude SSE payload must include usage, cost_usd, num_turns."""
        monkeypatch.setenv('MC_DATA_DIR', str(tmp_path))
        monkeypatch.setenv('MC_PORT', '0')
        if 'server' in sys.modules:
            del sys.modules['server']
        import server

        session = {
            'provider': 'claude',
            'usage': {'input_tokens': 10, 'output_tokens': 5},
            'cost_usd': 0.001,
            'num_turns': 1,
        }

        event = {'type': 'status', 'status': 'idle', **server._session_usage_payload(session)}
        parsed = json.loads(json.dumps(event))

        assert parsed['usage'] == {'input_tokens': 10, 'output_tokens': 5}
        assert parsed['cost_usd'] == 0.001
        assert parsed['num_turns'] == 1
