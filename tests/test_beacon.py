"""Beacon Phase-1 logic tests — aggregator triage + schema caps.

Pure-logic: configures the framework-agnostic beacon package with stub
load_projects / live_agent and real on-disk heartbeats under a tmp data_root, so
nothing here needs the Flask server or a model call. Exercises the read path
(store.read_all_heartbeats), the blocker taxonomy, stale gating, and the
attention-need sort order.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import beacon
from beacon import aggregator, schema


# ── fixtures ──────────────────────────────────────────────────────────────────

OLD_TS = '2020-01-01T00:00:00Z'          # far enough past to be stale
RECENT_TS = '2099-01-01T00:00:00Z'       # far future → "freshest"


def _write_hb(data_root: Path, pid: str, hb: dict) -> None:
    d = data_root / 'beacon'
    d.mkdir(parents=True, exist_ok=True)
    (d / f'{pid}.json').write_text(json.dumps(hb), encoding='utf-8')


@pytest.fixture
def configured(tmp_path):
    """Configure beacon against a tmp data_root. Returns a helper to set the
    project list + live-agent map per test."""
    state = {'projects': [], 'live': {}}

    def load_projects():
        return state['projects']

    def load_project(pid):
        return next((p for p in state['projects'] if p.get('id') == pid), None)

    def live_agent(pid):
        return state['live'].get(pid)

    beacon.configure(
        data_root=tmp_path,
        load_projects_fn=load_projects,
        load_project_fn=load_project,
        live_agent_fn=live_agent,
        get_memory_path_fn=lambda p: None,
        log_fn=None,
    )
    return state, tmp_path


# ── schema ────────────────────────────────────────────────────────────────────

def test_clamp_collapses_whitespace_and_truncates():
    assert schema.clamp('  a   b\n c  ', 80) == 'a b c'
    out = schema.clamp('x' * 200, 70)
    assert len(out) == 70 and out.endswith('…')


def test_normalize_brief_fills_missing_with_unavailable():
    b = schema.normalize_brief({'headline': 'Did the thing'})
    assert b['headline'] == 'Did the thing'
    assert b['done'] == 'unavailable'
    assert b['standing'] == 'unavailable'
    assert b['next'] == 'unavailable'


def test_normalize_brief_caps_headline():
    b = schema.normalize_brief({'headline': 'v ' * 100})
    assert len(b['headline']) <= schema.HEADLINE_MAX


# ── live-state overlay → status bucket ────────────────────────────────────────

def test_working_session_is_running(configured):
    state, _ = configured
    state['projects'] = [{'id': 'p1', 'name': 'P1'}]
    state['live'] = {'p1': {'state': 'working', 'reason': None, 'task': 'building'}}
    d = beacon.build_digest()
    row = d['projects'][0]
    assert row['status'] == 'running'
    assert row['live'] == 'running'
    assert d['counts']['running'] == 1


def test_plan_and_question_are_blocked(configured):
    state, _ = configured
    state['projects'] = [{'id': 'p1', 'name': 'P1'}, {'id': 'p2', 'name': 'P2'}]
    state['live'] = {
        'p1': {'state': 'asking', 'reason': 'plan', 'task': ''},
        'p2': {'state': 'asking', 'reason': 'question', 'task': ''},
    }
    d = beacon.build_digest()
    by_id = {r['id']: r for r in d['projects']}
    assert by_id['p1']['status'] == 'blocked'
    assert by_id['p1']['blocker']['type'] == 'plan_pending'
    assert by_id['p2']['blocker']['type'] == 'question_pending'
    assert d['counts']['blocked'] == 2


def test_no_live_no_hb_is_resting(configured):
    state, _ = configured
    state['projects'] = [{'id': 'p1', 'name': 'P1'}]
    d = beacon.build_digest()
    assert d['projects'][0]['status'] == 'resting'
    assert d['projects'][0]['has_brief'] is False


# ── persisted blockers + brief overlay ────────────────────────────────────────

def test_persisted_failed_resume_blocks_when_not_live(configured):
    state, root = configured
    state['projects'] = [{'id': 'p1', 'name': 'P1'}]
    _write_hb(root, 'p1', {
        'project': 'P1', 'updated_at': RECENT_TS, 'headline': 'crashed',
        'brief': {'done': 'd', 'standing': 's', 'next': 'n'},
        'blocker': {'type': 'failed_resume', 'since': OLD_TS, 'summary': 'died'},
    })
    d = beacon.build_digest()
    row = d['projects'][0]
    assert row['status'] == 'blocked'
    assert row['blocker']['type'] == 'failed_resume'
    assert row['has_brief'] is True
    assert row['brief']['standing'] == 's'
    assert row['headline'] == 'crashed'


def test_working_overrides_persisted_failed_resume(configured):
    """An actively-working session is never shown blocked, even if a stale
    failed_resume sits in its heartbeat."""
    state, root = configured
    state['projects'] = [{'id': 'p1', 'name': 'P1'}]
    state['live'] = {'p1': {'state': 'working', 'reason': None, 'task': 't'}}
    _write_hb(root, 'p1', {
        'project': 'P1', 'updated_at': RECENT_TS, 'headline': 'h',
        'brief': {'done': 'd', 'standing': 's', 'next': 'n'},
        'blocker': {'type': 'failed_resume', 'since': OLD_TS, 'summary': 'died'},
    })
    d = beacon.build_digest()
    assert d['projects'][0]['status'] == 'running'


# ── stale gating ──────────────────────────────────────────────────────────────

def test_stale_off_by_default(configured):
    """cadence 0 (default) → never stale, even with an ancient heartbeat."""
    state, root = configured
    state['projects'] = [{'id': 'p1', 'name': 'P1', 'beacon_cadence_hours': 0}]
    _write_hb(root, 'p1', {'project': 'P1', 'updated_at': OLD_TS,
                           'headline': 'old', 'brief': {}, 'blocker': None})
    d = beacon.build_digest()
    assert d['projects'][0]['status'] == 'resting'


def test_stale_fires_when_cadence_set_and_overdue(configured):
    state, root = configured
    state['projects'] = [{'id': 'p1', 'name': 'P1', 'beacon_cadence_hours': 1}]
    _write_hb(root, 'p1', {'project': 'P1', 'updated_at': OLD_TS,
                           'headline': 'old', 'brief': {}, 'blocker': None})
    d = beacon.build_digest()
    row = d['projects'][0]
    assert row['status'] == 'blocked'
    assert row['blocker']['type'] == 'stale'


def test_parked_project_never_stale(configured):
    state, root = configured
    state['projects'] = [{'id': 'p1', 'name': 'P1', 'status': 'parked',
                          'beacon_cadence_hours': 1}]
    _write_hb(root, 'p1', {'project': 'P1', 'updated_at': OLD_TS,
                           'headline': 'old', 'brief': {}, 'blocker': None})
    d = beacon.build_digest()
    assert d['projects'][0]['status'] == 'resting'


# ── sort order: blocked (oldest blocker first) → running → resting (recent) ────

def test_attention_sort_order(configured):
    state, root = configured
    state['projects'] = [
        {'id': 'rest_old', 'name': 'rest_old', 'last_updated': OLD_TS},
        {'id': 'rest_new', 'name': 'rest_new', 'last_updated': RECENT_TS},
        {'id': 'run1', 'name': 'run1', 'last_updated': OLD_TS},
        {'id': 'blk_recent', 'name': 'blk_recent', 'last_updated': RECENT_TS},
        {'id': 'blk_old', 'name': 'blk_old', 'last_updated': RECENT_TS},
    ]
    state['live'] = {'run1': {'state': 'working', 'reason': None, 'task': 't'}}
    # Two blocked via persisted failed_resume with different blocker ages.
    _write_hb(root, 'blk_recent', {'project': 'blk_recent', 'updated_at': RECENT_TS,
              'headline': 'h', 'brief': {},
              'blocker': {'type': 'failed_resume', 'since': RECENT_TS, 'summary': 'x'}})
    _write_hb(root, 'blk_old', {'project': 'blk_old', 'updated_at': RECENT_TS,
              'headline': 'h', 'brief': {},
              'blocker': {'type': 'failed_resume', 'since': OLD_TS, 'summary': 'x'}})

    d = beacon.build_digest()
    order = [r['id'] for r in d['projects']]
    # blocked group first, oldest blocker (most-neglected) at the very top
    assert order[0] == 'blk_old'
    assert order[1] == 'blk_recent'
    # then running
    assert order[2] == 'run1'
    # then resting, most-recently-touched first
    assert order[3] == 'rest_new'
    assert order[4] == 'rest_old'
    assert d['counts'] == {'blocked': 2, 'running': 1, 'resting': 2}


def test_unconfigured_digest_is_well_formed(monkeypatch):
    """build_digest never 500s even if beacon was never configured."""
    monkeypatch.setattr(aggregator.CFG, 'configured', False)
    d = aggregator.build_digest()
    assert d['configured'] is False
    assert d['counts'] == {'blocked': 0, 'running': 0, 'resting': 0}
    assert d['projects'] == []
