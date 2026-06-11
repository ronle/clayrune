"""Request-level tests for the settings family
(mc/blueprints/settings_routes.py).

Added with the mop-up step 1.14 (MODERNIZATION_PLAN.md Phase 5) — the FINAL
app-level API blueprint extraction. A pure move: the route handlers + the
settings.json store helpers are byte-verbatim from server.py, modulo the
documented `CONFIG` -> `state.CONFIG` live-alias rewrite (read in get_config /
browse_folders; read + in-place-mutate + persist in update_config). The path
seams (CONFIG_PATH / PROJECTS_BASE stay in server.py; SETTINGS_PATH wired
placeholder) are late-bound via wire().

These tests guard the MOVE: registration parity (the seam's worst silent
failure), config GET + PUT round-trips (incl. the live Mode-B respawn-flag path
with agent_sessions seeded), the 4 domains CRUD routes + malformed 400s, the
folder-browse + list-directory happy paths + the path-escape guard on
create-folder, and the app-wide local_auth gate (401 before the handler for a
non-loopback peer).

Determinism: patches mc.blueprints.settings_routes.* ONLY (the Phase-0
test-port rule — never server.*). SETTINGS_PATH / CONFIG_PATH / PROJECTS_BASE
are pointed at tmp; state.CONFIG + mc.state.agent_sessions are snapshotted and
restored so the respawn-flag test can't leak into other suites. No subprocess,
no real config write to ./data.
"""
import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

LAN = {'REMOTE_ADDR': '192.168.1.50'}

# The exact route surface 1.14 owns. A change here is intentional API churn.
EXPECTED_ROUTES = {
    '/api/config',
    '/api/browse/folders',
    '/api/browse/create_folder',
    '/api/settings/domains',
    '/api/settings/domains/add',
    '/api/settings/domains/<domain_id>',
    '/api/list-directory',
    '/api/create-folder',
}


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    """Flask test client + handles to the patched settings module.

    Patches the blueprint's wired globals ON THE MODULE (test-port rule):
    SETTINGS_PATH / CONFIG_PATH -> tmp files; PROJECTS_BASE -> a tmp dir.
    state.CONFIG + mc.state.agent_sessions are snapshotted and restored.
    """
    import server  # noqa: F401  (registers the blueprint + runs wire() on import)
    from mc import state
    from mc.blueprints import local_auth as la
    from mc.blueprints import settings_routes as sr

    # Deterministic gate: no LAN passcode this run (loopback exempt, LAN 401).
    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')

    # Settings + config stores -> isolated tmp files; FS picker base -> tmp dir.
    settings_path = tmp_path / 'settings.json'
    config_path = tmp_path / 'config.json'
    projects_base = tmp_path / 'projects_base'
    projects_base.mkdir()
    monkeypatch.setattr(sr, 'SETTINGS_PATH', settings_path)
    monkeypatch.setattr(sr, 'CONFIG_PATH', config_path)
    monkeypatch.setattr(sr, 'PROJECTS_BASE', projects_base)

    # Snapshot + restore the live CONFIG dict and agent_sessions so the
    # respawn-flag test (which mutates both) can't bleed into the rest of the
    # suite. We restore CONTENTS in place (the modules hold the same object).
    cfg_snapshot = dict(state.CONFIG)
    sess_snapshot = dict(state.agent_sessions)

    server.app.config['TESTING'] = True

    class Ctx:
        pass
    c = Ctx()
    c.client = server.app.test_client()
    c.sr = sr
    c.state = state
    c.settings_path = settings_path
    c.config_path = config_path
    c.projects_base = projects_base
    yield c

    state.CONFIG.clear()
    state.CONFIG.update(cfg_snapshot)
    state.agent_sessions.clear()
    state.agent_sessions.update(sess_snapshot)


# ── registration parity — the move's load-bearing guard ───────────────────────

def test_blueprint_registered(ctx):
    import server
    assert 'settings_routes' in server.app.blueprints


def test_all_expected_routes_present_under_blueprint(ctx):
    import server
    owned = {r.rule for r in server.app.url_map.iter_rules()
             if r.endpoint.startswith('settings_routes.')}
    missing = EXPECTED_ROUTES - owned
    assert not missing, f'routes missing from settings_routes blueprint: {sorted(missing)}'


def test_no_unexpected_settings_routes(ctx):
    import server
    owned = {r.rule for r in server.app.url_map.iter_rules()
             if r.endpoint.startswith('settings_routes.')}
    extra = owned - EXPECTED_ROUTES
    assert not extra, f'unpinned routes under settings_routes blueprint: {sorted(extra)}'


# ── GET /api/config — returns the editable-key projection ─────────────────────

def test_get_config_returns_editable_keys(ctx):
    resp = ctx.client.get('/api/config')
    assert resp.status_code == 200
    body = resp.get_json()
    # Every key returned must be an editable key; log_level is always present.
    assert set(body.keys()) == set(ctx.sr._CONFIG_EDITABLE_KEYS)
    assert 'log_level' in body


# ── PUT /api/config — happy persist + ignores non-editable keys ───────────────

def test_update_config_happy_persists(ctx):
    resp = ctx.client.put('/api/config', json={'agent_name': 'Vector',
                                               'log_level': 'warn'})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['ok'] is True
    assert set(body['updated']) == {'agent_name', 'log_level'}
    # live CONFIG updated
    assert ctx.state.CONFIG['agent_name'] == 'Vector'
    assert ctx.state.CONFIG['log_level'] == 'warn'
    # persisted to the tmp config file
    saved = json.loads(ctx.config_path.read_text(encoding='utf-8'))
    assert saved['agent_name'] == 'Vector'


def test_update_config_ignores_non_editable_key(ctx):
    resp = ctx.client.put('/api/config', json={'totally_made_up_key': 1})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['updated'] == []
    assert 'totally_made_up_key' not in ctx.state.CONFIG
    # nothing editable changed → no config write
    assert not ctx.config_path.exists()


def test_update_config_empty_body_noop(ctx):
    resp = ctx.client.put('/api/config', json={})
    assert resp.status_code == 200
    assert resp.get_json()['updated'] == []


# ── PUT /api/config — the live Mode-B respawn-flag path ───────────────────────

def test_update_config_flags_live_mode_b_session_for_respawn(ctx):
    """Flipping a Tier-1a (spawn-baked) key with sticky settings ON marks live
    Mode-B claude sessions to resume into a fresh process next turn."""
    ctx.state.CONFIG['sticky_agent_settings'] = True
    ctx.state.agent_sessions.clear()
    ctx.state.agent_sessions['live-b'] = {
        'mode': 'B', 'provider': 'claude', 'process_alive': True,
    }
    ctx.state.agent_sessions['dead-b'] = {
        'mode': 'B', 'provider': 'claude', 'process_alive': False,
    }
    ctx.state.agent_sessions['mode-a'] = {
        'mode': 'A', 'provider': 'claude', 'process_alive': True,
    }
    resp = ctx.client.put('/api/config', json={'agent_model': 'opus'})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['respawn_flagged'] == 1            # only the live Mode-B session
    assert ctx.state.agent_sessions['live-b'].get('_needs_respawn') is True
    assert '_needs_respawn' not in ctx.state.agent_sessions['dead-b']
    assert '_needs_respawn' not in ctx.state.agent_sessions['mode-a']


def test_update_config_no_respawn_when_sticky_off(ctx):
    ctx.state.CONFIG['sticky_agent_settings'] = False
    ctx.state.agent_sessions.clear()
    ctx.state.agent_sessions['live-b'] = {
        'mode': 'B', 'provider': 'claude', 'process_alive': True,
    }
    resp = ctx.client.put('/api/config', json={'agent_model': 'opus'})
    assert resp.status_code == 200
    assert resp.get_json()['respawn_flagged'] == 0
    assert '_needs_respawn' not in ctx.state.agent_sessions['live-b']


def test_update_config_no_respawn_for_non_tier1_key(ctx):
    """A non-respawn-trigger key (e.g. log_level) never flags sessions even with
    sticky settings ON."""
    ctx.state.CONFIG['sticky_agent_settings'] = True
    ctx.state.agent_sessions.clear()
    ctx.state.agent_sessions['live-b'] = {
        'mode': 'B', 'provider': 'claude', 'process_alive': True,
    }
    resp = ctx.client.put('/api/config', json={'log_level': 'debug'})
    assert resp.status_code == 200
    assert resp.get_json()['respawn_flagged'] == 0


# ── /api/settings/domains — CRUD round-trips ──────────────────────────────────

def test_get_domains_defaults(ctx):
    resp = ctx.client.get('/api/settings/domains')
    assert resp.status_code == 200
    domains = resp.get_json()
    ids = {d['id'] for d in domains}
    assert {'general', 'trading', 'infra', 'hobby'} <= ids


def test_add_domain_happy_and_persists(ctx):
    resp = ctx.client.post('/api/settings/domains/add',
                           json={'id': 'research', 'label': 'Research'})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body['ok'] is True
    assert body['domain']['id'] == 'research'
    # persisted
    saved = json.loads(ctx.settings_path.read_text(encoding='utf-8'))
    assert any(d['id'] == 'research' for d in saved['domains'])


def test_add_domain_missing_id_400(ctx):
    resp = ctx.client.post('/api/settings/domains/add', json={'label': 'x'})
    assert resp.status_code == 400


def test_add_domain_duplicate_409(ctx):
    ctx.client.post('/api/settings/domains/add', json={'id': 'dup'})
    resp = ctx.client.post('/api/settings/domains/add', json={'id': 'dup'})
    assert resp.status_code == 409


def test_update_domain_patches_fields(ctx):
    ctx.client.post('/api/settings/domains/add', json={'id': 'edit_me'})
    resp = ctx.client.patch('/api/settings/domains/edit_me',
                            json={'label': 'Edited', 'color': 'red'})
    assert resp.status_code == 200
    saved = json.loads(ctx.settings_path.read_text(encoding='utf-8'))
    dom = next(d for d in saved['domains'] if d['id'] == 'edit_me')
    assert dom['label'] == 'Edited'
    assert dom['color'] == 'red'


def test_update_domain_not_found_404(ctx):
    resp = ctx.client.patch('/api/settings/domains/nope', json={'label': 'x'})
    assert resp.status_code == 404


def test_delete_domain_happy(ctx):
    ctx.client.post('/api/settings/domains/add', json={'id': 'temp'})
    resp = ctx.client.delete('/api/settings/domains/temp')
    assert resp.status_code == 200
    saved = json.loads(ctx.settings_path.read_text(encoding='utf-8'))
    assert not any(d['id'] == 'temp' for d in saved['domains'])


def test_delete_domain_general_protected_400(ctx):
    resp = ctx.client.delete('/api/settings/domains/general')
    assert resp.status_code == 400


def test_delete_domain_not_found_404(ctx):
    resp = ctx.client.delete('/api/settings/domains/never_existed')
    assert resp.status_code == 404


# ── /api/browse/folders — happy + not-a-dir ───────────────────────────────────

def test_browse_folders_lists_subdirs(ctx, tmp_path):
    base = tmp_path / 'browse_root'
    base.mkdir()
    (base / 'alpha').mkdir()
    (base / 'beta').mkdir()
    (base / '.hidden').mkdir()         # dot-dir filtered out
    (base / 'a_file.txt').write_text('x', encoding='utf-8')
    resp = ctx.client.get('/api/browse/folders', query_string={'path': str(base)})
    assert resp.status_code == 200
    body = resp.get_json()
    names = {f['name'] for f in body['folders']}
    assert names == {'alpha', 'beta'}   # no .hidden, no file
    assert body['path'] == str(base.resolve())


def test_browse_folders_not_a_directory_404(ctx, tmp_path):
    f = tmp_path / 'just_a_file.txt'
    f.write_text('x', encoding='utf-8')
    resp = ctx.client.get('/api/browse/folders', query_string={'path': str(f)})
    assert resp.status_code == 404


# ── /api/browse/create_folder — happy + path-traversal guard ──────────────────

def test_browse_create_folder_happy(ctx, tmp_path):
    parent = tmp_path / 'cf_parent'
    parent.mkdir()
    resp = ctx.client.post('/api/browse/create_folder',
                           json={'parent': str(parent), 'name': 'newdir'})
    assert resp.status_code == 200
    assert (parent / 'newdir').is_dir()


def test_browse_create_folder_rejects_traversal_name(ctx, tmp_path):
    parent = tmp_path / 'cf_parent2'
    parent.mkdir()
    for bad in ('../escape', 'a/b', 'a\\b', 'c:evil'):
        resp = ctx.client.post('/api/browse/create_folder',
                               json={'parent': str(parent), 'name': bad})
        assert resp.status_code == 400, bad


def test_browse_create_folder_missing_fields_400(ctx):
    resp = ctx.client.post('/api/browse/create_folder', json={'parent': ''})
    assert resp.status_code == 400


# ── /api/list-directory — happy + path-escape/permission shapes ───────────────

def test_list_directory_happy(ctx, tmp_path):
    root = tmp_path / 'ld_root'
    root.mkdir()
    (root / 'one').mkdir()
    (root / 'two').mkdir()
    (root / '.dot').mkdir()
    resp = ctx.client.post('/api/list-directory', json={'path': str(root)})
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body['dirs']) == {'one', 'two'}
    assert body['projects_base'] == str(ctx.projects_base)


def test_list_directory_not_a_dir_400(ctx, tmp_path):
    f = tmp_path / 'ld_file'
    f.write_text('x', encoding='utf-8')
    resp = ctx.client.post('/api/list-directory', json={'path': str(f)})
    assert resp.status_code == 400


def test_list_directory_defaults_to_projects_base(ctx):
    (ctx.projects_base / 'seeded').mkdir()
    resp = ctx.client.post('/api/list-directory', json={})
    assert resp.status_code == 200
    body = resp.get_json()
    assert 'seeded' in body['dirs']


# ── /api/create-folder — happy + traversal guard + duplicate ──────────────────

def test_create_folder_happy(ctx, tmp_path):
    parent = tmp_path / 'mk_parent'
    parent.mkdir()
    resp = ctx.client.post('/api/create-folder',
                           json={'parent': str(parent), 'name': 'made'})
    assert resp.status_code == 200
    assert (parent / 'made').is_dir()


def test_create_folder_rejects_traversal(ctx, tmp_path):
    parent = tmp_path / 'mk_parent2'
    parent.mkdir()
    resp = ctx.client.post('/api/create-folder',
                           json={'parent': str(parent), 'name': '../oops'})
    assert resp.status_code == 400


def test_create_folder_duplicate_409(ctx, tmp_path):
    parent = tmp_path / 'mk_parent3'
    parent.mkdir()
    (parent / 'exists').mkdir()
    resp = ctx.client.post('/api/create-folder',
                           json={'parent': str(parent), 'name': 'exists'})
    assert resp.status_code == 409


def test_create_folder_missing_name_400(ctx):
    resp = ctx.client.post('/api/create-folder', json={'parent': '/tmp'})
    assert resp.status_code == 400


# ── auth contract — app-wide gate still covers the moved routes ───────────────

def test_moved_route_behind_lan_gate(ctx):
    """A non-loopback peer with no passcode is 401'd BEFORE the handler runs."""
    resp = ctx.client.get('/api/config', environ_overrides=LAN)
    assert resp.status_code == 401


def test_moved_post_route_behind_lan_gate(ctx):
    resp = ctx.client.post('/api/list-directory', json={}, environ_overrides=LAN)
    assert resp.status_code == 401
