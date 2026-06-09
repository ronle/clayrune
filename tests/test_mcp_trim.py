"""Regression tests for per-project MCP trimming (server.py + agent_runtime.py).

Covers the resource-efficiency change (2026-06-03, step 1 of the MCP-fleet work):
a project may declare `enabled_mcp_servers` so its sessions load ONLY those MCP
servers (via `--strict-mcp-config` + a generated `--mcp-config`), instead of
inheriting the full global+project+plugin fleet.

Key invariants under test:
  - DEFAULT-OFF: a project without a list-valued `enabled_mcp_servers` resolves
    to None → no flags → byte-identical to pre-trim behavior;
  - engram (a *plugin*, dropped by --strict-mcp-config) is always re-declared in
    the catalog so a trimmed project can keep memory;
  - unknown server names are skipped, not fatal; an empty list loads nothing;
  - resolution is fail-open (any error → None → full fleet, never breaks dispatch);
  - build_command emits the two flags iff a non-empty config JSON is supplied.
"""
from __future__ import annotations

import importlib
import json

import pytest


@pytest.fixture
def srv(tmp_data_dir):
    import server
    importlib.reload(server)
    return server


# ── _resolve_project_mcp_config: the default-off invariant ────────────────────

def test_unset_returns_none(srv):
    """No `enabled_mcp_servers` key → not opted in → None (full fleet)."""
    assert srv._resolve_project_mcp_config({'id': 'p'}) is None


def test_none_project_returns_none(srv):
    assert srv._resolve_project_mcp_config(None) is None


def test_non_list_returns_none(srv):
    """A stray non-list value must NOT silently trim — treat as not-opted-in."""
    assert srv._resolve_project_mcp_config({'enabled_mcp_servers': 'filesystem'}) is None
    assert srv._resolve_project_mcp_config({'enabled_mcp_servers': {'a': 1}}) is None


# ── selection logic (catalog stubbed for determinism) ─────────────────────────

def _stub_catalog(srv, monkeypatch, catalog):
    monkeypatch.setattr(srv, '_mcp_server_catalog', lambda project: dict(catalog))


def test_list_selects_named_servers(srv, monkeypatch):
    _stub_catalog(srv, monkeypatch, {
        'tradingview': {'command': 'node'},
        'filesystem': {'command': 'npx'},
        'engram': {'command': 'engram', 'args': ['mcp', '--tools=agent']},
    })
    out = srv._resolve_project_mcp_config(
        {'id': 'p', 'enabled_mcp_servers': ['filesystem', 'engram']})
    cfg = json.loads(out)
    assert set(cfg['mcpServers']) == {'filesystem', 'engram'}
    assert 'tradingview' not in cfg['mcpServers']
    assert cfg['mcpServers']['engram'] == {'command': 'engram', 'args': ['mcp', '--tools=agent']}


def test_unknown_name_skipped(srv, monkeypatch):
    _stub_catalog(srv, monkeypatch, {'filesystem': {'command': 'npx'}})
    out = srv._resolve_project_mcp_config(
        {'id': 'p', 'enabled_mcp_servers': ['filesystem', 'does-not-exist']})
    cfg = json.loads(out)
    assert set(cfg['mcpServers']) == {'filesystem'}


def test_empty_list_loads_nothing(srv, monkeypatch):
    """[] is a valid maximal trim → {"mcpServers": {}} (not None)."""
    _stub_catalog(srv, monkeypatch, {'filesystem': {'command': 'npx'}})
    out = srv._resolve_project_mcp_config({'id': 'p', 'enabled_mcp_servers': []})
    assert json.loads(out) == {'mcpServers': {}}


def test_resolve_fails_open(srv, monkeypatch):
    """Any error in catalog build → None (fail-open to full fleet)."""
    def _boom(project):
        raise RuntimeError('disk gone')
    monkeypatch.setattr(srv, '_mcp_server_catalog', _boom)
    assert srv._resolve_project_mcp_config(
        {'id': 'p', 'enabled_mcp_servers': ['filesystem']}) is None


# ── _mcp_server_catalog: engram always present, reads project .mcp.json ───────

def test_catalog_always_includes_engram(srv, tmp_path):
    """Even with no project .mcp.json, engram is re-declared (memory preserved)."""
    cat = srv._mcp_server_catalog({'id': 'p', 'project_path': str(tmp_path)})
    assert cat['engram'] == {'command': 'engram', 'args': ['mcp', '--tools=agent']}


def test_catalog_reads_project_mcp_json(srv, tmp_path):
    (tmp_path / '.mcp.json').write_text(
        json.dumps({'mcpServers': {'foo': {'command': 'x'}}}), encoding='utf-8')
    cat = srv._mcp_server_catalog({'id': 'p', 'project_path': str(tmp_path)})
    assert cat['foo'] == {'command': 'x'}
    assert 'engram' in cat  # still re-declared alongside project servers


def test_catalog_survives_malformed_project_mcp_json(srv, tmp_path):
    (tmp_path / '.mcp.json').write_text('{ not json', encoding='utf-8')
    cat = srv._mcp_server_catalog({'id': 'p', 'project_path': str(tmp_path)})
    assert 'engram' in cat  # malformed source skipped, never raises


def test_engram_spec_matches_plugin_manifest(srv):
    """Guards against drift from the engram plugin's own .mcp.json."""
    assert srv._ENGRAM_MCP_SPEC == {'command': 'engram', 'args': ['mcp', '--tools=agent']}


# ── build_command flag injection (agent_runtime, no server reload needed) ─────

@pytest.fixture
def claude_rt(monkeypatch):
    import agent_runtime
    rt = agent_runtime.ClaudeRuntime()
    # Pin the binary so the test doesn't depend on a resolvable claude install.
    monkeypatch.setattr(rt, 'resolve_binary_str', lambda: 'claude')
    return rt


def test_build_command_injects_strict_and_config(claude_rt):
    cfg = '{"mcpServers": {}}'
    cmd = claude_rt.build_command(streaming=True, mcp_config_json=cfg)
    assert '--strict-mcp-config' in cmd
    i = cmd.index('--mcp-config')
    assert cmd[i + 1] == cfg
    # strict precedes the config payload
    assert cmd.index('--strict-mcp-config') < i


def test_build_command_omits_when_empty(claude_rt):
    """Default-off invariant at the flag layer: no mcp_config_json → no flags."""
    cmd = claude_rt.build_command(streaming=True, mcp_config_json='')
    assert '--strict-mcp-config' not in cmd
    assert '--mcp-config' not in cmd


def test_build_command_omits_when_whitespace(claude_rt):
    cmd = claude_rt.build_command(streaming=True, mcp_config_json='   ')
    assert '--strict-mcp-config' not in cmd
    assert '--mcp-config' not in cmd


def test_build_command_default_arg_is_off(claude_rt):
    """Callers that never pass mcp_config_json keep old behavior."""
    cmd = claude_rt.build_command(streaming=True)
    assert '--mcp-config' not in cmd


# ── Per-project loadout endpoints: GET/PUT /api/project/<id>/mcp-enabled ───────
#
# The WRITE surface that finally lets enabled_mcp_servers be set from the UI
# (previously the trim was read-only → 0 projects ever opted in). Invariants:
#   - default-off project → opted_in False, enabled None;
#   - PUT a list → opted_in True, persisted, engram force-kept, unknown skipped;
#   - PUT null → opt-in cleared (key removed) → back to full fleet;
#   - non-list → 400; unknown project → 404;
#   - /api/mcp?project_id annotates active/loadout_custom/always_on.

@pytest.fixture
def client(srv):
    srv.app.config['TESTING'] = True
    return srv.app.test_client()


def _make_project(srv, pid='p', **fields):
    rec = {'id': pid, 'name': pid, 'project_path': '', **fields}
    (srv.DATA_DIR / f'{pid}.json').write_text(json.dumps(rec), encoding='utf-8')
    return rec


def _stub_endpoint_catalog(srv, monkeypatch, names):
    cat = {n: {'command': 'x'} for n in names}
    cat.setdefault('engram', {'command': 'engram', 'args': ['mcp', '--tools=agent']})
    monkeypatch.setattr(srv, '_mcp_server_catalog', lambda project: dict(cat))


def test_get_mcp_enabled_default_off(srv, client, monkeypatch):
    _make_project(srv, 'p')
    _stub_endpoint_catalog(srv, monkeypatch, ['filesystem'])
    body = client.get('/api/project/p/mcp-enabled').get_json()
    assert body['opted_in'] is False
    assert body['enabled'] is None
    assert 'engram' in body['catalog'] and 'filesystem' in body['catalog']


def test_put_mcp_enabled_sets_list_and_force_keeps_engram(srv, client, monkeypatch):
    _make_project(srv, 'p')
    _stub_endpoint_catalog(srv, monkeypatch, ['filesystem', 'github'])
    body = client.put('/api/project/p/mcp-enabled', json={'enabled': ['filesystem']}).get_json()
    assert body['opted_in'] is True
    assert 'filesystem' in body['enabled']
    assert 'engram' in body['enabled']  # force-kept even though not requested
    saved = json.loads((srv.DATA_DIR / 'p.json').read_text(encoding='utf-8'))
    assert sorted(saved['enabled_mcp_servers']) == sorted(body['enabled'])


def test_put_mcp_enabled_filters_unknown_names(srv, client, monkeypatch):
    _make_project(srv, 'p')
    _stub_endpoint_catalog(srv, monkeypatch, ['filesystem'])
    body = client.put('/api/project/p/mcp-enabled', json={'enabled': ['filesystem', 'ghost']}).get_json()
    assert 'ghost' not in body['enabled']
    assert 'filesystem' in body['enabled']


def test_put_mcp_enabled_null_clears_optin(srv, client, monkeypatch):
    _make_project(srv, 'p', enabled_mcp_servers=['filesystem', 'engram'])
    _stub_endpoint_catalog(srv, monkeypatch, ['filesystem'])
    body = client.put('/api/project/p/mcp-enabled', json={'enabled': None}).get_json()
    assert body['opted_in'] is False
    saved = json.loads((srv.DATA_DIR / 'p.json').read_text(encoding='utf-8'))
    assert 'enabled_mcp_servers' not in saved


def test_put_mcp_enabled_rejects_non_list(srv, client, monkeypatch):
    _make_project(srv, 'p')
    _stub_endpoint_catalog(srv, monkeypatch, ['filesystem'])
    assert client.put('/api/project/p/mcp-enabled', json={'enabled': 'filesystem'}).status_code == 400


def test_put_mcp_enabled_unknown_project_404(srv, client):
    assert client.put('/api/project/nope/mcp-enabled', json={'enabled': []}).status_code == 404


def test_list_mcp_annotates_active_when_project(srv, client, monkeypatch):
    _make_project(srv, 'p', enabled_mcp_servers=['filesystem', 'engram'])
    monkeypatch.setattr(srv._mcp, 'list_servers',
                        lambda project_path=None, project_id=None: [
                            {'name': 'filesystem', 'scope': 'global'},
                            {'name': 'github', 'scope': 'global'},
                            {'name': 'engram', 'scope': 'global'},
                        ])
    items = {it['name']: it for it in client.get('/api/mcp?project_id=p').get_json()}
    assert items['filesystem']['active'] is True
    assert items['github']['active'] is False      # opted-in list excludes it
    assert items['engram']['active'] is True        # always_on overrides
    assert items['engram']['always_on'] is True
    assert items['filesystem']['loadout_custom'] is True
