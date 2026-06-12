"""Unit tests for Prompt Builder Phase 2 persona wiring in
mc/blueprints/agent_routes.py: _resolve_character (new-chat pick →
meta+body) and _build_agent_context character injection at spawn.

Determinism: mc.characters.GLOBAL_AGENTS_DIR is repointed at tmp_path; no
real ~/.claude is touched. Importing server wires the blueprint's
global-scope deps (SHARED_RULES_PATH, PORT, memory helpers) so
_build_agent_context runs.
"""
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    import server  # noqa: F401 — wires the blueprint deps
    from mc import characters as ch
    from mc.blueprints import agent_routes as ar

    monkeypatch.setattr(ch, 'GLOBAL_AGENTS_DIR', tmp_path / 'agents-global')

    proj_path = tmp_path / 'proj'
    (proj_path / '.claude' / 'agents').mkdir(parents=True)
    # A project-scope character and a global one.
    ch.write_character('project', 'code-reviewer',
                       'Use for strict review.', 'You are a terse reviewer.',
                       project_path=str(proj_path))
    ch.write_character('global', 'docs-writer',
                       'Use for docs.', 'You write clear docs.')
    return {'ar': ar, 'proj_path': str(proj_path), 'tmp': tmp_path}


class TestResolveCharacter:
    def test_project_scope_resolves_meta_and_body(self, env):
        meta, body = env['ar']._resolve_character(env['proj_path'], 'project:code-reviewer')
        assert meta == {'name': 'code-reviewer', 'scope': 'project',
                        'display_name': 'code-reviewer'}
        assert body.strip() == 'You are a terse reviewer.'

    def test_global_scope(self, env):
        meta, body = env['ar']._resolve_character(env['proj_path'], 'global:docs-writer')
        assert meta['scope'] == 'global' and meta['name'] == 'docs-writer'
        assert 'clear docs' in body

    @pytest.mark.parametrize('val', ['', None, 'bogus', 'archive:x', 'global:', ':name', 'project:does-not-exist'])
    def test_invalid_or_missing_yields_none(self, env, val):
        meta, body = env['ar']._resolve_character(env['proj_path'], val)
        assert meta is None and body == ''

    def test_project_name_not_found_in_global_scope(self, env):
        # code-reviewer exists only in project scope; asking global misses.
        meta, body = env['ar']._resolve_character(env['proj_path'], 'global:code-reviewer')
        assert meta is None and body == ''


class TestContextInjection:
    def _project(self, env):
        return {'id': 'tc', 'name': 'TC', 'project_path': env['proj_path'],
                'provider': 'claude'}

    def test_character_block_injected_after_rules(self, env):
        ctx = env['ar']._build_agent_context(
            self._project(env), character_body='You are a terse reviewer.')
        assert '--- CHARACTER (active persona for this chat) ---' in ctx
        assert 'You are a terse reviewer.' in ctx

    def test_no_block_without_character(self, env):
        ctx = env['ar']._build_agent_context(self._project(env))
        assert 'CHARACTER (active persona' not in ctx
