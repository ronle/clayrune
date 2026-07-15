"""Deep-link file serving — /api/serve-file.

Verifies the allowlist (project roots + uploads/media, NOT the data root), the
secrets denylist, and the traversal guard. Ron's scope, 2026-07-15: "project
files only + a secrets denylist".
"""
import importlib

import pytest


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import server
    from mc.blueprints import project_routes as pr
    from mc.blueprints import local_auth as la

    # Loopback is auth-exempt; point the passcode file at nothing so LAN is the
    # only gated path (test_client requests come from 127.0.0.1 anyway).
    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')

    proj = tmp_path / 'proj'
    (proj / 'sub').mkdir(parents=True)
    (proj / 'report.log').write_text('hello log', encoding='utf-8')
    (proj / 'sub' / 'nested.txt').write_text('nested', encoding='utf-8')
    (proj / '.env').write_text('SECRET=1', encoding='utf-8')
    (proj / 'server.pem').write_text('KEY', encoding='utf-8')
    (proj / 'aws_credentials.json').write_text('{}', encoding='utf-8')

    data_root = tmp_path / 'root'
    uploads = data_root / 'data' / 'uploads'
    media = data_root / 'data' / 'media'
    uploads.mkdir(parents=True); media.mkdir(parents=True)
    (uploads / 'shot.png').write_bytes(b'\x89PNG')
    (data_root / 'data' / 'projects').mkdir(parents=True)
    (data_root / 'data' / 'projects' / 'secret.json').write_text('{"k":1}', encoding='utf-8')

    outside = tmp_path / 'outside'
    outside.mkdir()
    (outside / 'other.txt').write_text('nope', encoding='utf-8')

    monkeypatch.setattr(pr, 'UPLOADS_DIR', uploads)
    monkeypatch.setattr(pr, '_DATA_ROOT', data_root)
    monkeypatch.setattr(pr, 'load_projects',
                        lambda: [{'id': 'p', 'project_path': str(proj)}])

    server.app.config['TESTING'] = True
    return server.app.test_client(), {
        'proj': proj, 'uploads': uploads, 'media': media,
        'data_root': data_root, 'outside': outside,
    }


def _get(c, path, **qs):
    from urllib.parse import urlencode
    q = urlencode({'path': str(path), **qs})
    return c.get(f'/api/serve-file?{q}')


def test_serves_a_project_file(client):
    c, d = client
    r = _get(c, d['proj'] / 'report.log')
    assert r.status_code == 200
    assert r.data == b'hello log'
    # Download by default.
    assert 'attachment' in r.headers.get('Content-Disposition', '')
    assert 'report.log' in r.headers.get('Content-Disposition', '')


def test_inline_flag(client):
    c, d = client
    r = _get(c, d['proj'] / 'report.log', inline='1')
    assert r.status_code == 200
    assert 'attachment' not in r.headers.get('Content-Disposition', '')


def test_serves_nested_project_file(client):
    c, d = client
    assert _get(c, d['proj'] / 'sub' / 'nested.txt').status_code == 200


def test_serves_uploads_and_media(client):
    c, d = client
    assert _get(c, d['uploads'] / 'shot.png').status_code == 200
    # media dir is allowed even though empty of this exact file → 404 (allowed, absent)
    assert _get(c, d['media'] / 'ghost.png').status_code == 404


@pytest.mark.parametrize('name', ['.env', 'server.pem', 'aws_credentials.json'])
def test_secrets_are_denied_even_inside_a_project(client, name):
    c, d = client
    assert _get(c, d['proj'] / name).status_code == 403


def test_data_root_records_are_not_reachable(client):
    """The whole data root is NOT allowlisted — only uploads/media under it."""
    c, d = client
    r = _get(c, d['data_root'] / 'data' / 'projects' / 'secret.json')
    assert r.status_code == 403


def test_outside_allowlist_denied(client):
    c, d = client
    assert _get(c, d['outside'] / 'other.txt').status_code == 403


def test_traversal_cannot_escape(client):
    c, d = client
    sneak = str(d['proj'] / '..' / 'outside' / 'other.txt')
    assert _get(c, sneak).status_code == 403


def test_missing_file_404(client):
    c, d = client
    assert _get(c, d['proj'] / 'nope.xyz').status_code == 404


def test_no_path_400(client):
    c, d = client
    assert c.get('/api/serve-file').status_code == 400
