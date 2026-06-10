"""Tests for the auto model router (feat/auto-model-router).

Covers the v1 surface called out in docs/DISPATCH_AND_ROUTING_ANALYSIS.md §C.3:

  - _route_dispatch_model: H/S/O parsing, exception fail-open, garbage fail-open
  - _resolve_dispatch_model: toggle gating, manual short-circuit, fallback path
  - _router_stat: file shape (totals, by_pair, last_fallback), tier collapsing
  - load_projects(): regression test — _router_stats.json sidecar MUST be ignored
    so the restart endpoints don't 500 (load-bearing per CLAUDE.md)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from mc.blueprints import agent_routes as _bp_agent

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))


def _fresh_server(tmp_data_dir, monkeypatch):
    """Re-import server with DATA_DIR pointed at a clean tmp path.

    Mirrors the pattern in test_telemetry.py — each test gets a virgin
    module so DATA_DIR + CONFIG are reset.
    """
    monkeypatch.setenv('MC_DATA_DIR', str(tmp_data_dir))
    monkeypatch.setenv('MC_PORT', '0')
    if 'server' in sys.modules:
        del sys.modules['server']
    import server
    return server


# ── _route_dispatch_model ────────────────────────────────────────────────────

class TestRouteDispatchModel:
    def test_haiku_pick(self, tmp_path, monkeypatch):
        s = _fresh_server(tmp_path, monkeypatch)
        s.CONFIG['auto_model_enabled'] = True
        with patch.object(_bp_agent, '_scribe_call', return_value='H'):
            model, source = s._route_dispatch_model('say hi', 'opus')
        assert model == s._AUTO_MODEL_VALID['H']
        assert source == 'auto'

    def test_sonnet_pick(self, tmp_path, monkeypatch):
        s = _fresh_server(tmp_path, monkeypatch)
        s.CONFIG['auto_model_enabled'] = True
        with patch.object(_bp_agent, '_scribe_call', return_value='S'):
            model, source = s._route_dispatch_model('refactor this function', 'opus')
        assert model == s._AUTO_MODEL_VALID['S']
        assert source == 'auto'

    def test_opus_pick(self, tmp_path, monkeypatch):
        s = _fresh_server(tmp_path, monkeypatch)
        s.CONFIG['auto_model_enabled'] = True
        with patch.object(_bp_agent, '_scribe_call', return_value='O'):
            model, source = s._route_dispatch_model('redesign the auth layer', 'sonnet')
        assert model == s._AUTO_MODEL_VALID['O']
        assert source == 'auto'

    def test_classifier_exception_fails_open(self, tmp_path, monkeypatch):
        s = _fresh_server(tmp_path, monkeypatch)
        s.CONFIG['auto_model_enabled'] = True
        with patch.object(_bp_agent, '_scribe_call', side_effect=RuntimeError('boom')):
            model, source = s._route_dispatch_model('anything', 'opus')
        assert model == 'opus'
        assert source == 'fallback'

    def test_garbage_output_fails_open(self, tmp_path, monkeypatch):
        s = _fresh_server(tmp_path, monkeypatch)
        s.CONFIG['auto_model_enabled'] = True
        # Anything that isn't H/S/O after upper+strip+[:1] is treated as garbage.
        with patch.object(_bp_agent, '_scribe_call', return_value='I refuse to classify.'):
            model, source = s._route_dispatch_model('anything', 'opus')
        assert model == 'opus'
        assert source == 'fallback'

    def test_lowercase_token_accepted(self, tmp_path, monkeypatch):
        s = _fresh_server(tmp_path, monkeypatch)
        s.CONFIG['auto_model_enabled'] = True
        with patch.object(_bp_agent, '_scribe_call', return_value='h'):
            model, source = s._route_dispatch_model('say hi', 'opus')
        assert model == s._AUTO_MODEL_VALID['H']
        assert source == 'auto'

    def test_empty_prompt_short_circuits(self, tmp_path, monkeypatch):
        s = _fresh_server(tmp_path, monkeypatch)
        s.CONFIG['auto_model_enabled'] = True
        # Empty prompt must not invoke the classifier at all.
        with patch.object(_bp_agent, '_scribe_call') as mock_call:
            model, source = s._route_dispatch_model('', 'opus')
            assert not mock_call.called
        assert model == 'opus'
        assert source == 'fallback'


# ── _resolve_dispatch_model ──────────────────────────────────────────────────

class TestResolveDispatchModel:
    def test_toggle_off_returns_manual(self, tmp_path, monkeypatch):
        s = _fresh_server(tmp_path, monkeypatch)
        s.CONFIG['auto_model_enabled'] = False
        with patch.object(_bp_agent, '_scribe_call') as mock_call:
            model, source = s._resolve_dispatch_model({'agent_model': 'opus'}, 'anything')
            assert not mock_call.called, 'classifier must NOT run when toggle is off'
        assert model == 'opus'
        assert source == 'manual'

    def test_toggle_on_classifies(self, tmp_path, monkeypatch):
        s = _fresh_server(tmp_path, monkeypatch)
        s.CONFIG['auto_model_enabled'] = True
        with patch.object(_bp_agent, '_scribe_call', return_value='H'):
            model, source = s._resolve_dispatch_model({'agent_model': 'opus'}, 'say hi')
        assert model == s._AUTO_MODEL_VALID['H']
        assert source == 'auto'

    def test_empty_prompt_short_circuits_to_manual(self, tmp_path, monkeypatch):
        s = _fresh_server(tmp_path, monkeypatch)
        s.CONFIG['auto_model_enabled'] = True
        with patch.object(_bp_agent, '_scribe_call') as mock_call:
            model, source = s._resolve_dispatch_model({'agent_model': 'opus'}, '')
            assert not mock_call.called
        assert model == 'opus'
        assert source == 'manual'

    def test_no_project_model_falls_back_to_config(self, tmp_path, monkeypatch):
        s = _fresh_server(tmp_path, monkeypatch)
        s.CONFIG['auto_model_enabled'] = False
        s.CONFIG['agent_model'] = 'sonnet'
        model, source = s._resolve_dispatch_model({}, 'anything')
        assert model == 'sonnet'
        assert source == 'manual'

    def test_no_model_at_all_defaults_to_sonnet(self, tmp_path, monkeypatch):
        s = _fresh_server(tmp_path, monkeypatch)
        s.CONFIG['auto_model_enabled'] = False
        s.CONFIG['agent_model'] = ''
        model, source = s._resolve_dispatch_model({}, 'anything')
        assert model == 'sonnet'
        assert source == 'manual'


# ── _router_stat ─────────────────────────────────────────────────────────────

class TestRouterStat:
    def _stats_file(self, server, project_id):
        return server.DATA_DIR / f'{project_id}_router_stats.json'

    def test_manual_increments_totals(self, tmp_path, monkeypatch):
        s = _fresh_server(tmp_path, monkeypatch)
        s._router_stat('proj1', 'opus', 'opus', 'manual')
        data = json.loads(self._stats_file(s, 'proj1').read_text())
        assert data['totals']['manual'] == 1
        assert data['by_pair']['opus->opus'] == 1
        assert 'last_fallback' not in data

    def test_auto_records_pair(self, tmp_path, monkeypatch):
        s = _fresh_server(tmp_path, monkeypatch)
        s._router_stat('proj2', 'opus', 'haiku', 'auto')
        data = json.loads(self._stats_file(s, 'proj2').read_text())
        assert data['totals']['auto'] == 1
        assert data['by_pair']['opus->haiku'] == 1

    def test_fallback_records_last_fallback(self, tmp_path, monkeypatch):
        s = _fresh_server(tmp_path, monkeypatch)
        s._router_stat('proj3', 'opus', 'opus', 'fallback', reason='TimeoutError')
        data = json.loads(self._stats_file(s, 'proj3').read_text())
        assert data['totals']['fallback'] == 1
        assert data['by_pair']['fallback:opus'] == 1
        assert data['last_fallback']['reason'] == 'TimeoutError'
        assert data['last_fallback']['ts'].endswith('Z')

    def test_increments_accumulate(self, tmp_path, monkeypatch):
        s = _fresh_server(tmp_path, monkeypatch)
        for _ in range(3):
            s._router_stat('proj4', 'opus', 'haiku', 'auto')
        data = json.loads(self._stats_file(s, 'proj4').read_text())
        assert data['totals']['auto'] == 3
        assert data['by_pair']['opus->haiku'] == 3

    def test_dated_model_id_collapses_to_tier(self, tmp_path, monkeypatch):
        s = _fresh_server(tmp_path, monkeypatch)
        # Anthropic-style snapshot IDs should map to the tier keyword so
        # by_pair stays small (3x3 + fallback bucket).
        s._router_stat('proj5', 'claude-opus-4-7',
                       'claude-haiku-4-5-20251001', 'auto')
        data = json.loads(self._stats_file(s, 'proj5').read_text())
        assert data['by_pair']['opus->haiku'] == 1

    def test_telemetry_failure_never_raises(self, tmp_path, monkeypatch):
        s = _fresh_server(tmp_path, monkeypatch)
        # Point DATA_DIR at a non-writable location by replacing it with a
        # file path — open() will fail. The call must NOT raise.
        bad = tmp_path / 'not_a_dir'
        bad.write_text('blocker')
        s.DATA_DIR = bad
        s._router_stat('proj6', 'opus', 'haiku', 'auto')  # should not raise


# ── load_projects() exclusion — LOAD-BEARING regression test ──────────────────

class TestLoadProjectsExclusion:
    """Per CLAUDE.md DATA_DIR pollution rule: every new sidecar JSON in
    DATA_DIR must be suffix-excluded in load_projects(). A stray file there
    becomes a malformed 'project' and 500s _get_active_restart_blockers,
    blackholing the restart endpoints. This test enforces the exclusion."""

    def test_router_stats_excluded(self, tmp_path, monkeypatch):
        s = _fresh_server(tmp_path, monkeypatch)
        # Plant a router-stats sidecar with a non-project shape.
        (s.DATA_DIR / 'proj7_router_stats.json').write_text(
            json.dumps({'totals': {'manual': 1}}))
        projects = s.load_projects()
        ids = [p.get('id') for p in projects]
        assert 'proj7_router_stats' not in ids
        # And the existing excluded sidecars stay excluded.
        (s.DATA_DIR / 'proj7_agent_log.json').write_text('[]')
        (s.DATA_DIR / 'proj7_scribe_stats.json').write_text('{}')
        projects = s.load_projects()
        ids = [p.get('id') for p in projects]
        assert 'proj7_agent_log' not in ids
        assert 'proj7_scribe_stats' not in ids
