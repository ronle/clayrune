"""Tests for steward/core.py — config accessors, charter, cycle-task, notify
seam, fence settings, loop-health. Uses an in-memory project store injected via
configure() so nothing touches disk except the fence-settings file (tmp_path).
"""
import json

import pytest

import steward
from steward import core
from steward._config import configure


@pytest.fixture
def store(tmp_path):
    """In-memory project store wired through configure()."""
    projects = {}

    def load_project(pid):
        return json.loads(json.dumps(projects[pid])) if pid in projects else None

    def save_project(pid, p):
        projects[pid] = json.loads(json.dumps(p))

    def load_projects():
        return [json.loads(json.dumps(v)) for v in projects.values()]

    def append_note(pid, item_id, text, agent_code='user'):
        p = projects.get(pid)
        if not p:
            return False
        for it in p.get('backlog', []):
            if it.get('id') == item_id:
                it.setdefault('notes', []).append(
                    {'ts': 'now', 'agent_code': agent_code, 'text': text})
                return True
        return False

    pushes = []
    configure(
        data_root=tmp_path,
        load_project_fn=load_project,
        save_project_fn=save_project,
        load_projects_fn=load_projects,
        append_note_fn=append_note,
        notify_push_fn=lambda *a, **k: pushes.append((a, k)),
        log_fn=None,
    )
    projects['p1'] = {
        'id': 'p1', 'name': 'Proj One',
        'steward_mode': 'on',
        'steward_objective': 'Keep the docs in sync with the code',
        'steward_cadence_minutes': 120,
        'backlog': [],
    }
    return {'projects': projects, 'pushes': pushes}


def test_enabled_and_accessors(store):
    p = store['projects']['p1']
    assert core.steward_enabled(p)
    assert core.get_objective(p) == 'Keep the docs in sync with the code'
    assert core.get_cadence_minutes(p) == 120


def test_cadence_clamped(store):
    assert core.get_cadence_minutes({'steward_cadence_minutes': 5}) == core.MIN_CADENCE_MINUTES
    assert core.get_cadence_minutes({'steward_cadence_minutes': 99999}) == core.MAX_CADENCE_MINUTES
    assert core.get_cadence_minutes({}) == core.DEFAULT_CADENCE_MINUTES
    assert core.get_cadence_minutes({'steward_cadence_minutes': 'garbage'}) == core.DEFAULT_CADENCE_MINUTES


def test_disabled_default():
    assert not core.steward_enabled({'id': 'x'})
    assert not core.steward_enabled({'steward_mode': 'off'})


def test_ensure_charter_creates_once(store):
    c1 = core.ensure_charter('p1', 'Keep the docs in sync')
    assert c1 is not None
    assert c1['text'].startswith(core.CHARTER_PREFIX)
    assert c1['source'] == 'steward-charter'
    # idempotent
    c2 = core.ensure_charter('p1', 'a different objective')
    assert c2['id'] == c1['id']
    assert len(store['projects']['p1']['backlog']) == 1


def test_find_charter_by_prefix_fallback(store):
    store['projects']['p1']['backlog'] = [
        {'id': 'aa', 'text': core.CHARTER_PREFIX + 'legacy', 'source': 'dashboard'}]
    assert core.find_charter(store['projects']['p1'])['id'] == 'aa'


def test_build_cycle_task_has_marker(store):
    c = core.ensure_charter('p1', 'Keep docs synced')
    task = core.build_cycle_task(store['projects']['p1'], c)
    assert task.startswith('[Steward cycle]')
    assert c['id'] in task
    assert 'DECISION NEEDED' in task


def test_notify_appends_note_and_pushes(store):
    core.ensure_charter('p1', 'obj')
    assert core.steward_notify('p1', 'decision-needed', 'push to prod?', action='git push')
    charter = core.find_charter(store['projects']['p1'])
    last = charter['notes'][-1]['text']
    assert last.startswith('DECISION NEEDED: push to prod?')
    assert 'Action (approve to run): git push' in last
    assert len(store['pushes']) == 1  # decision-needed pushes


def test_notify_fyi_does_not_push(store):
    core.ensure_charter('p1', 'obj')
    assert core.steward_notify('p1', 'fyi', 'routine progress')
    assert len(store['pushes']) == 0  # routine FYI never pushes


def test_notify_unknown_kind_coerced_to_fyi(store):
    core.ensure_charter('p1', 'obj')
    core.steward_notify('p1', 'wat', 'body')
    charter = core.find_charter(store['projects']['p1'])
    assert charter['notes'][-1]['text'].startswith('FYI:')


def test_ensure_fence_settings_writes_hook(store):
    path = core.ensure_fence_settings()
    assert path.exists()
    content = json.loads(path.read_text(encoding='utf-8'))
    hook = content['hooks']['PreToolUse'][0]
    assert 'Bash' in hook['matcher']
    assert 'fence.py' in hook['hooks'][0]['command']
    # idempotent — second call doesn't rewrite/raise
    assert core.ensure_fence_settings() == path


def test_loop_health(store):
    core.ensure_charter('p1', 'obj')
    core.steward_notify('p1', 'decision-needed', 'approve X', action='deploy')
    core.steward_notify('p1', 'blocked', 'waiting on Y')
    h = core.loop_health()
    assert h['projects_enabled'] == 1
    assert h['decisions_pending'] == 1
    assert h['blocked'] == 1
    assert h['enabled'][0]['project_id'] == 'p1'
    assert any('decision' in a for a in h['alerts'])


def test_public_api_exports():
    for name in ('steward_enabled', 'ensure_charter', 'build_cycle_task',
                 'steward_notify', 'ensure_fence_settings', 'loop_health',
                 'classify_bash', 'classify_action'):
        assert hasattr(steward, name)
