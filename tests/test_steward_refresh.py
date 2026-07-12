"""Steward cycle refresh — the steward can learn between cycles (2026-07-11).

Before this, a long-running steward was blind to everything learned after it was
switched on. Two independent causes, both fixed here:

  1. Its prompt was FROZEN into the schedule row at enable time and
     re-dispatched verbatim forever.
  2. The refresh rides in the TASK TEXT so it reaches even the cheap
     stdin-append continue path — a LIVE process whose system prompt cannot
     change mid-flight. (Historical note: this predates the 2026-07-11 fix
     that re-appends context on `-r` respawns; the task-text channel stays
     because the no-respawn stdin path still has no other channel.)
  3. The CLI reads the skills dir at PROCESS SPAWN, but the cheap continue path
     appends to a LIVE process's stdin — so a skill promoted between cycles
     never loaded. A new skill now forces a cold `-r` respawn.

The safety property is preserved: the steward is fed only interactive-origin
artifacts (_UNATTENDED_LOOP_RULE). Its own output comes back to it the long way
round — a human promotes it, it becomes a skill on disk, and (3) delivers it.
"""
import time
from datetime import datetime, timezone

import pytest

import steward.core as core


def _iso_now():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


# ── build_cycle_task ─────────────────────────────────────────────────────────

def test_cycle_task_without_refresh_is_unchanged():
    """No new learning → byte-identical to the pre-refresh prompt."""
    task = core.build_cycle_task({'steward_objective': 'keep it green'},
                                 {'id': 'c1'})
    assert task.startswith('[Steward cycle]')
    assert 'keep it green' in task
    assert 'NEW SKILLS' not in task


def test_cycle_task_appends_refresh_after_the_marker():
    """The [Steward cycle] marker must stay FIRST — steward/fence.py self-gates
    on it (it reads the first user message), and mc-steward triggers on it. A
    refresh block that displaced the marker would silently un-fence the steward.
    """
    task = core.build_cycle_task({'steward_objective': 'obj'}, {'id': 'c1'},
                                 refresh='--- NEW SKILLS ---\n  • foo')
    assert task.lstrip().startswith('[Steward cycle]')
    assert task.rstrip().endswith('• foo')


def test_cycle_task_tolerates_a_missing_charter():
    assert core.build_cycle_task({'steward_objective': 'obj'}, None)


# ── new_skills_since ─────────────────────────────────────────────────────────

def test_new_skills_since_detects_a_skill_installed_after_the_last_cycle(
        tmp_path, monkeypatch):
    monkeypatch.setattr(core.Path, 'home', staticmethod(lambda: tmp_path))
    gskills = tmp_path / '.claude' / 'skills'
    (gskills / 'old-skill').mkdir(parents=True)
    (gskills / 'old-skill' / 'SKILL.md').write_text(
        '---\nname: old-skill\ndescription: was already here\n---\n',
        encoding='utf-8')

    time.sleep(1.1)          # fs mtime granularity
    since = _iso_now()
    time.sleep(1.1)

    (gskills / 'new-skill').mkdir()
    (gskills / 'new-skill' / 'SKILL.md').write_text(
        '---\nname: new-skill\ndescription: promoted between cycles\n---\n',
        encoding='utf-8')

    found = core.new_skills_since({}, since)
    names = [s['name'] for s in found]
    assert 'new-skill' in names          # the whole point
    assert 'old-skill' not in names      # don't re-announce every cycle
    assert found[0]['description'] == 'promoted between cycles'
    assert found[0]['scope'] == 'global'


def test_new_skills_since_finds_project_local_skills(tmp_path, monkeypatch):
    monkeypatch.setattr(core.Path, 'home', staticmethod(lambda: tmp_path / 'h'))
    proj = tmp_path / 'proj'
    d = proj / '.claude' / 'skills' / 'proj-skill'
    d.mkdir(parents=True)
    (d / 'SKILL.md').write_text(
        '---\nname: proj-skill\ndescription: local one\n---\n', encoding='utf-8')

    found = core.new_skills_since({'project_path': str(proj)},
                                  '2020-01-01T00:00:00Z')
    assert [s['scope'] for s in found] == ['project']


def test_new_skills_since_is_empty_without_a_baseline():
    """First cycle after enable has no `since` — don't dump the entire skills
    dir into the prompt."""
    assert core.new_skills_since({}, '') == []


def test_new_skills_since_never_raises_on_a_missing_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(core.Path, 'home', staticmethod(lambda: tmp_path / 'nope'))
    assert core.new_skills_since({'project_path': '/does/not/exist'},
                                 '2020-01-01T00:00:00Z') == []


# ── The safety property, at the integration seam ─────────────────────────────

def test_steward_refresh_requests_only_interactive_explorations(monkeypatch):
    """_UNATTENDED_LOOP_RULE: the steward's own artifacts must not flow back to
    it unattended. The refresh builder MUST query the read-floor with
    consumer_unattended=True — if someone drops that kwarg, the steward starts
    training on its own output again and no other test would catch it."""
    from mc.blueprints import scheduler_routes as s

    seen = {}

    def fake_read_floor(project_id, task, topk, consumer_unattended=False):
        seen['consumer_unattended'] = consumer_unattended
        return []

    monkeypatch.setattr(s, 'load_project', lambda pid: {
        'id': pid, 'steward_objective': 'keep it green', 'backlog': [],
        'steward_last_cycle_at': '2026-07-01T00:00:00Z',
    })
    monkeypatch.setattr(s, 'save_project', lambda pid, p: None)
    monkeypatch.setattr(s._mem, '_memory_search', lambda *a, **k: [])
    monkeypatch.setattr(s._distiller, 'exploration_read_floor', fake_read_floor)

    task, respawn = s._steward_cycle_task('p1')
    assert seen['consumer_unattended'] is True
    assert task.lstrip().startswith('[Steward cycle]')
