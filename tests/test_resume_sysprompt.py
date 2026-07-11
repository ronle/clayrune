"""Resume must re-append the injected context (2026-07-11 fix).

CLI >= 2.1.206 rebuilds the system prompt from flags on EVERY invocation, so
a `-r` respawn that omits --append-system-prompt-file silently drops the whole
injected context (rules, read-floor, API reference, character). See memory
discovery-claude-resume-ignores-append-system-prompt (reversed 2026-07-11).

Guards the three load-bearing pieces of the fix:
  - _respawn_sysprompt_args: stash-first (safe under mgr.lock), rebuild only
    when the stash is missing, degrade to a context-less resume on failure.
  - _RESPAWN_TRIGGER_KEYS: the Tier-1a/1b split collapsed — the system-prompt
    directive key now triggers a sticky respawn.
  - sweep_orphan_tmpfiles: the startup sweep for crash-stranded atomic-write
    temps and stale sysprompt spawn files.
"""
import os
import sys
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from mc.blueprints import agent_routes  # noqa: E402
from mc.blueprints.settings_routes import _RESPAWN_TRIGGER_KEYS  # noqa: E402
from mc.core import sweep_orphan_tmpfiles  # noqa: E402


def _read_and_unlink(path):
    text = Path(path).read_text(encoding='utf-8')
    os.unlink(path)
    return text


class TestRespawnSyspromptArgs:
    def test_stash_hit_uses_session_context_without_rebuild(self, monkeypatch):
        def _boom(*a, **kw):
            raise AssertionError('stash hit must not rebuild context')
        monkeypatch.setattr(agent_routes, '_build_agent_context', _boom)

        session = {'_system_prompt': 'STASHED-CTX'}
        args, path = agent_routes._respawn_sysprompt_args(session, {'id': 'p1'})
        assert args[0] == '--append-system-prompt-file'
        assert _read_and_unlink(path) == 'STASHED-CTX'

    def test_stash_miss_rebuilds_and_remembers(self, monkeypatch):
        calls = []
        monkeypatch.setattr(
            agent_routes, '_build_agent_context',
            lambda project, incognito=False, task='', character_body='':
                calls.append((project, incognito, task)) or 'BUILT-CTX')

        session = {}
        args, path = agent_routes._respawn_sysprompt_args(
            session, {'id': 'p1'}, task='follow-up msg')
        assert calls == [({'id': 'p1'}, False, 'follow-up msg')]
        assert session['_system_prompt'] == 'BUILT-CTX'
        assert _read_and_unlink(path) == 'BUILT-CTX'

    def test_incognito_flag_carried_into_rebuild(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            agent_routes, '_build_agent_context',
            lambda project, incognito=False, task='', character_body='':
                seen.update(incognito=incognito) or 'CTX')
        _, path = agent_routes._respawn_sysprompt_args(
            {'incognito': True}, {'id': 'p1'})
        assert seen['incognito'] is True
        assert path is not None
        os.unlink(path)

    def test_rebuild_failure_degrades_to_bare_resume(self, monkeypatch):
        def _boom(*a, **kw):
            raise RuntimeError('memory search exploded')
        monkeypatch.setattr(agent_routes, '_build_agent_context', _boom)

        args, path = agent_routes._respawn_sysprompt_args({}, {'id': 'p1'})
        assert (args, path) == ([], None)

    def test_none_session_is_safe(self, monkeypatch):
        monkeypatch.setattr(
            agent_routes, '_build_agent_context', lambda *a, **kw: 'CTX')
        args, path = agent_routes._respawn_sysprompt_args(None, {'id': 'p1'})
        assert args[0] == '--append-system-prompt-file'
        assert _read_and_unlink(path) == 'CTX'


class TestTierSplitCollapsed:
    def test_system_prompt_directive_triggers_respawn(self):
        # The Tier-1b exclusion rested on the reversed 2.1.158 canary result;
        # since the fix, a sticky respawn rebuilds + re-appends the context,
        # so the brief-reply directive must be a respawn trigger.
        assert 'brief_replies_always_enabled' in _RESPAWN_TRIGGER_KEYS


class TestSweepOrphanTmpfiles:
    def test_sweeps_only_old_matching_files(self, tmp_path, monkeypatch):
        import tempfile
        fake_os_tmp = tmp_path / 'os_tmp'
        fake_os_tmp.mkdir()
        monkeypatch.setattr(tempfile, 'gettempdir', lambda: str(fake_os_tmp))

        old = time.time() - 48 * 3600
        data = tmp_path / 'data'
        (data / 'projects').mkdir(parents=True)

        stale = data / '.mc_child_pids.json.tmp49260'
        stale.write_text('{}')
        os.utime(stale, (old, old))

        stale_nested = data / 'projects' / '.state.json.tmp7'
        stale_nested.write_text('{}')
        os.utime(stale_nested, (old, old))

        fresh = data / '.live_write.json.tmp123'
        fresh.write_text('{}')  # in-flight write — must survive

        wrong_shape = data / 'notes.tmp'
        wrong_shape.write_text('keep')  # no dot-prefix/pid — must survive
        os.utime(wrong_shape, (old, old))

        stale_sp = fake_os_tmp / 'clayrune-sysprompt-abc123.txt'
        stale_sp.write_text('ctx')
        os.utime(stale_sp, (old, old))
        fresh_sp = fake_os_tmp / 'clayrune-sysprompt-live.txt'
        fresh_sp.write_text('ctx')

        removed = sweep_orphan_tmpfiles([data])
        assert removed == 3
        assert not stale.exists() and not stale_nested.exists()
        assert not stale_sp.exists()
        assert fresh.exists() and wrong_shape.exists() and fresh_sp.exists()

    def test_missing_root_is_harmless(self, tmp_path, monkeypatch):
        import tempfile
        monkeypatch.setattr(tempfile, 'gettempdir', lambda: str(tmp_path))
        assert sweep_orphan_tmpfiles([tmp_path / 'nope']) == 0
