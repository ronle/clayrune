"""Request-level tests for the agent-characters family
(mc/blueprints/character_routes.py + mc/characters.py) — Prompt Builder
Phase 1 (docs/PROMPT_BUILDER_DESIGN.md §5.2).

Determinism: GLOBAL_AGENTS_DIR is repointed at tmp_path (never the real
~/.claude/agents), and load_project is patched on BOTH blueprint modules —
character_routes binds its own copy via wire(), while the shared
_resolve_project_path_or_400 helper reads skills_routes' module global.
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
    import server
    from mc import characters as ch
    from mc.blueprints import character_routes as cr
    from mc.blueprints import local_auth as la
    from mc.blueprints import skills_routes as sr

    monkeypatch.setattr(la, 'LOCAL_AUTH_PATH', tmp_path / 'local_auth.json')

    global_dir = tmp_path / 'agents-global'
    monkeypatch.setattr(ch, 'GLOBAL_AGENTS_DIR', global_dir)

    proj_path = tmp_path / 'proj'
    proj_path.mkdir()
    proj = {'id': 'tchar', 'name': 'Char Test', 'project_path': str(proj_path)}
    pathless = {'id': 'nopath', 'name': 'No Path'}

    def _load(pid):
        return {'tchar': proj, 'nopath': pathless}.get(pid)

    monkeypatch.setattr(cr, 'load_project', _load)
    monkeypatch.setattr(sr, 'load_project', _load)

    server.app.config['TESTING'] = True
    c = server.app.test_client()
    c.global_dir = global_dir          # type: ignore[attr-defined]
    c.proj_agents = proj_path / '.claude' / 'agents'  # type: ignore[attr-defined]
    return c


def _payload(**over):
    base = {
        'name': 'code-reviewer',
        'description': 'Use for strict review of diffs before merge.',
        'body': 'You are a strict senior code reviewer. Be terse.',
        'scope': 'project',
        'project_id': 'tchar',
    }
    base.update(over)
    return base


class TestCreate:
    def test_project_scope_writes_standard_subagent_file(self, client):
        r = client.post('/api/characters', json=_payload())
        assert r.status_code == 201
        rec = r.get_json()
        assert rec['name'] == 'code-reviewer' and rec['scope'] == 'project'

        f = client.proj_agents / 'code-reviewer.md'
        text = f.read_text(encoding='utf-8')
        # Standard Claude Code subagent shape: frontmatter then body.
        assert text.startswith('---\nname: code-reviewer\n')
        assert 'description: ' in text
        assert text.rstrip().endswith('Be terse.')

    def test_global_scope(self, client):
        r = client.post('/api/characters', json=_payload(scope='global'))
        assert r.status_code == 201
        assert (client.global_dir / 'code-reviewer.md').is_file()

    @pytest.mark.parametrize('over,frag', [
        ({'name': 'Bad Name!'}, 'kebab-case'),
        ({'description': '  '}, 'description is required'),
        ({'body': ''}, 'body is required'),
        ({'body': 'x' * (6 * 1024 + 1)}, 'too large'),
        ({'scope': 'archive'}, 'scope must be'),
    ])
    def test_validation_400(self, client, over, frag):
        r = client.post('/api/characters', json=_payload(**over))
        assert r.status_code == 400
        assert frag in r.get_json()['error']

    def test_project_scope_requires_project(self, client):
        r = client.post('/api/characters', json=_payload(project_id=None))
        assert r.status_code == 400
        r = client.post('/api/characters', json=_payload(project_id='nopath'))
        assert r.status_code == 400
        assert 'project_path' in r.get_json()['error']

    def test_collision_409_then_overwrite(self, client):
        assert client.post('/api/characters', json=_payload()).status_code == 201
        r = client.post('/api/characters', json=_payload(body='v2'))
        assert r.status_code == 409
        r = client.post('/api/characters', json=_payload(body='v2', overwrite=True))
        assert r.status_code == 201
        text = (client.proj_agents / 'code-reviewer.md').read_text(encoding='utf-8')
        assert text.rstrip().endswith('v2')

    def test_lan_without_passcode_401(self, client):
        r = client.post('/api/characters', json=_payload(),
                        environ_overrides=LAN)
        assert r.status_code == 401


class TestListReadUpdateDelete:
    def test_roundtrip(self, client):
        client.post('/api/characters', json=_payload())

        r = client.get('/api/characters?project_id=tchar')
        names = [c['name'] for c in r.get_json()]
        assert 'code-reviewer' in names

        r = client.get('/api/characters/project/code-reviewer?project_id=tchar')
        rec = r.get_json()
        assert rec['body'].startswith('You are a strict senior code reviewer')

        r = client.put('/api/characters/project/code-reviewer',
                       json={'project_id': 'tchar', 'description': 'Updated desc.'})
        assert r.status_code == 200
        assert r.get_json()['description'] == 'Updated desc.'
        # Body untouched by a description-only PUT.
        r = client.get('/api/characters/project/code-reviewer?project_id=tchar')
        assert 'senior code reviewer' in r.get_json()['body']

        r = client.delete('/api/characters/project/code-reviewer?project_id=tchar')
        assert r.status_code == 200
        assert not (client.proj_agents / 'code-reviewer.md').exists()
        r = client.get('/api/characters/project/code-reviewer?project_id=tchar')
        assert r.status_code == 404

    def test_project_shadows_global_in_list(self, client):
        client.post('/api/characters', json=_payload(scope='global'))
        client.post('/api/characters', json=_payload())
        items = client.get('/api/characters?project_id=tchar').get_json()
        by_scope = {c['scope']: c for c in items if c['name'] == 'code-reviewer'}
        assert by_scope['global'].get('shadowed_by_project') is True
        assert 'shadowed_by_project' not in by_scope['project']

    def test_nested_community_file_found_and_deleted(self, client):
        # Imported packs may nest files in subfolders; CC scans recursively
        # and so do we (lookup by file stem).
        nested = client.global_dir / 'review' / 'security-auditor.md'
        nested.parent.mkdir(parents=True)
        nested.write_text('---\nname: security-auditor\ndescription: audits\n---\nYou audit.\n',
                          encoding='utf-8')
        r = client.get('/api/characters/global/security-auditor')
        assert r.status_code == 200
        assert r.get_json()['body'].strip() == 'You audit.'
        assert client.delete('/api/characters/global/security-auditor').status_code == 200
        assert not nested.exists()

    def test_q_filter(self, client):
        client.post('/api/characters', json=_payload(scope='global'))
        client.post('/api/characters', json=_payload(
            scope='global', name='docs-writer',
            description='Use for documentation work.'))
        items = client.get('/api/characters?q=documentation').get_json()
        assert [c['name'] for c in items] == ['docs-writer']
