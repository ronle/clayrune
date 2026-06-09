"""Stranded-preference rescue — in-window preferences generate even when the
current session emitted none.

Context (2026-06-08): the recurrence-1 preference fix (e42c358) only let
preferences generate, but _aggregate_per_project evaluated ONLY the current
session's fingerprints (new_signals). Preferences captured under the old
recurrence-3 gate were content-rich one-offs that never recur, so they sat in
_skill_stats.json forever and never reached the review queue — the fix produced
zero output in the wild. The rescue: evaluate ALL in-window preference
fingerprints, not just this session's. Scoped to preferences only (topics would
flood the skill renderer with REFUSEs; explorations are single-shot).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import distiller


def _recent_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def _setup(tmp_path):
    distiller._skills_root = tmp_path / 'skills'
    distiller._data_root = tmp_path / 'projects'
    distiller._data_root.mkdir(parents=True, exist_ok=True)


def _write_stats(project_id, signals, *, outbox=None, suppressions=None):
    stats = distiller._empty_stats()
    stats['signals'] = signals
    if outbox:
        stats['outbox'] = outbox
    if suppressions:
        stats['suppressions'] = suppressions
    # Write the sidecar directly — distiller._write_skill_stats goes through the
    # registered _atomic_write_text callback (None outside the running server).
    # _aggregate_per_project only READS stats, so a plain write is faithful.
    distiller._skill_stats_path(project_id).write_text(
        json.dumps(stats, indent=2), encoding='utf-8')


def _pref_signal(exact, coarse, *, sid='old_sid'):
    return {
        'sid': sid, 'ts': _recent_iso(), 'scope_tag': 'project-specific',
        'kind': 'preference', 'phrase': 'prefer-config',
        'exact': exact, 'coarse': coarse,
        'summary': 'Default sticky settings ON for new installs',
        'evidence_quote': 'sticky should default on',
    }


def test_stranded_preference_is_backfilled(tmp_path):
    _setup(tmp_path)
    pid = 'myproj'
    # A preference sits in stored stats; the CURRENT session emitted nothing.
    _write_stats(pid, [_pref_signal('a' * 16, 'b' * 16)])
    candidates = distiller._aggregate_per_project(pid, {}, new_signals=[])
    prefs = [c for c in candidates if c['kind'] == 'preference']
    assert len(prefs) == 1, f"expected the stranded preference to backfill, got {candidates}"
    assert prefs[0]['exact'] == 'a' * 16


def test_stranded_topic_is_NOT_backfilled(tmp_path):
    _setup(tmp_path)
    pid = 'myproj'
    # A topic recurring 3x in stored stats but absent from this session must NOT
    # backfill — topic->skill is content-starved; backfilling = REFUSE flood.
    topic = lambda sid: {
        'sid': sid, 'ts': _recent_iso(), 'scope_tag': 'project-specific',
        'kind': 'topic', 'phrase': 'diagnose-alert',
        'exact': 'c' * 16, 'coarse': 'd' * 16,
    }
    _write_stats(pid, [topic('s1'), topic('s2'), topic('s3')])
    candidates = distiller._aggregate_per_project(pid, {}, new_signals=[])
    assert not candidates, f"topics must not backfill, got {candidates}"


def test_backfill_respects_outbox_dedupe(tmp_path):
    _setup(tmp_path)
    pid = 'myproj'
    # An already-proposed preference (recent outbox stamp) must not re-propose.
    _write_stats(
        pid,
        [_pref_signal('a' * 16, 'b' * 16)],
        outbox={f"{'a' * 16}:preference": {'last_proposed_at': _recent_iso()}},
    )
    candidates = distiller._aggregate_per_project(pid, {}, new_signals=[])
    assert not candidates, f"outbox dedupe should suppress re-proposal, got {candidates}"


def test_backfill_respects_suppression(tmp_path):
    _setup(tmp_path)
    pid = 'myproj'
    # A user-rejected preference (suppression decision 'no') must stay suppressed.
    _write_stats(
        pid,
        [_pref_signal('a' * 16, 'b' * 16)],
        suppressions={f"{'a' * 16}:preference": {'decision': 'no'}},
    )
    candidates = distiller._aggregate_per_project(pid, {}, new_signals=[])
    assert not candidates, f"suppression should block backfill, got {candidates}"
