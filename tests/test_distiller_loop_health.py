"""Loop-health aggregator regression — the self-detection layer (step 2).

loop_health() aggregates per-project _skill_stats.json counters + the
_proposed/ queue census into the four signals we agreed to watch (generation,
refuse rate, readback hit-rate, queue staleness) and emits an `alerts` list.
The whole point is that a degraded leg surfaces on its own — the REFUSE bug
sat undetected because nothing watched these numbers. These tests pin the
derivations + alert thresholds.
"""
from __future__ import annotations

import json
from pathlib import Path

import distiller


def _stats(projects_dir: Path, pid: str, counters: dict):
    projects_dir.mkdir(parents=True, exist_ok=True)
    (projects_dir / f"{pid}_skill_stats.json").write_text(
        json.dumps({'counters': counters}), encoding='utf-8')


def _expl(skills_root: Path, scope_dir: str, slug: str):
    d = skills_root / '_proposed' / scope_dir / f"2026-06-05T00-00-00-aaaa-{slug}"
    d.mkdir(parents=True, exist_ok=True)
    (d / 'EXPLORATION.md').write_text(
        "---\nkind: exploration\nname: " + slug + "\n"
        "created_at: 2026-06-05T00:00:00Z\n---\n\n# t\n\nbody\n",
        encoding='utf-8')


def _pref(skills_root: Path, scope_dir: str, slug: str):
    d = skills_root / '_proposed' / scope_dir / f"2026-06-05T00-00-00-bbbb-{slug}"
    d.mkdir(parents=True, exist_ok=True)
    (d / 'PREFERENCE.md').write_text(
        "---\nkind: preference\nname: " + slug + "\n"
        "created_at: 2026-06-05T00:00:00Z\n---\n\n# t\n\nbody\n",
        encoding='utf-8')


def _setup(tmp_path):
    distiller._data_root = tmp_path / 'projects'
    distiller._skills_root = tmp_path / 'skills'
    # Server-injected helpers — stub so the stats-write path (e.g. inside
    # _record_vocab_miss) actually executes in standalone runs.
    distiller._atomic_write_text = lambda p, t: Path(p).write_text(
        t, encoding='utf-8')
    distiller._now_iso = lambda: '2026-06-12T00:00:00Z'


def test_refuse_rate_computed(tmp_path):
    _setup(tmp_path)
    _stats(tmp_path / 'projects', 'p1',
           {'proposed:skill': 2, 'render_refuse:skill': 2})
    snap = distiller.loop_health()
    assert snap['generation']['skill']['refuse_rate'] == 0.5
    assert snap['generation']['skill']['proposed'] == 2
    assert snap['generation']['skill']['refused'] == 2


def test_high_refuse_rate_alert(tmp_path):
    _setup(tmp_path)
    _stats(tmp_path / 'projects', 'p1',
           {'proposed:skill': 1, 'render_refuse:skill': 4,
            'proposed:exploration': 5})
    _expl(tmp_path / 'skills', 'p1', 'a')
    snap = distiller.loop_health()
    assert any('high refuse rate for skill' in a for a in snap['alerts'])


def test_preference_flatline_alert(tmp_path):
    _setup(tmp_path)
    _stats(tmp_path / 'projects', 'p1', {'proposed:exploration': 5})
    _expl(tmp_path / 'skills', 'p1', 'a')
    snap = distiller.loop_health()
    assert any('preference generation flatlined' in a for a in snap['alerts'])


def test_no_flatline_when_pipeline_dead(tmp_path):
    """No explorations + empty queue → pipeline isn't alive, so flatline
    alerts must NOT fire (avoids noise on a cold/new install)."""
    _setup(tmp_path)
    _stats(tmp_path / 'projects', 'p1', {})
    snap = distiller.loop_health()
    assert not any('flatlined' in a for a in snap['alerts'])


def test_readback_hit_rate(tmp_path):
    _setup(tmp_path)
    _stats(tmp_path / 'projects', 'p1',
           {'readback_query': 10, 'readback_hit': 1, 'proposed:exploration': 1})
    _expl(tmp_path / 'skills', 'p1', 'a')
    snap = distiller.loop_health()
    assert snap['readback']['hit_rate'] == 0.1
    assert any('readback hit-rate low' in a for a in snap['alerts'])


def test_promotion_backlog_alert_excludes_explorations(tmp_path):
    """Explorations have no promote action (readback uses them silently), so a
    queue of pure explorations must NOT fire the promotion-backlog alert — the
    old behavior fired perpetually while promotion was actively draining the
    real backlog."""
    _setup(tmp_path)
    _stats(tmp_path / 'projects', 'p1', {})
    for i in range(10):
        _expl(tmp_path / 'skills', 'p1', f'expl-{i}')
    snap = distiller.loop_health()
    assert snap['queue']['total'] == 10
    assert snap['queue']['by_kind']['exploration'] == 10
    assert not any('promotion backlog' in a for a in snap['alerts'])


def test_promotion_backlog_alert_fires_on_promotables(tmp_path):
    """Promotable artifacts (preference/skill) awaiting review DO fire it."""
    _setup(tmp_path)
    _stats(tmp_path / 'projects', 'p1', {})
    for i in range(10):
        _pref(tmp_path / 'skills', 'p1', f'pref-{i}')
    snap = distiller.loop_health()
    assert any('promotion backlog' in a for a in snap['alerts'])


def test_vocab_miss_samples_surface(tmp_path):
    """Dropped phrases are sampled with their OOV reason and surfaced in
    loop-health so the closed vocab can be grown from real misses."""
    _setup(tmp_path)
    _stats(tmp_path / 'projects', 'p1', {})  # ensure projects dir + sidecar
    # OOV verb (frobnicate∉VERBS), seen twice; OOV noun once.
    distiller._record_vocab_miss('p1', 'frobnicate-condense')
    distiller._record_vocab_miss('p1', 'frobnicate-scribe')
    distiller._record_vocab_miss('p1', 'fix-quantumwidget')
    snap = distiller.loop_health()
    assert snap['extraction']['vocabulary_miss'] == 3
    assert snap['extraction']['vocab_miss_sampled'] == 3
    verbs = dict(snap['extraction']['vocab_miss_oov_verbs'])
    nouns = dict(snap['extraction']['vocab_miss_oov_nouns'])
    assert verbs.get('frobnicate') == 2
    assert nouns.get('quantumwidget') == 1
    phrases = dict(snap['extraction']['vocab_miss_top_phrases'])
    assert phrases.get('frobnicate-condense') == 1


def test_vocab_miss_ring_buffer_caps(tmp_path):
    """The per-project sample buffer is bounded (newest kept), but the lifetime
    counter keeps counting past the cap."""
    _setup(tmp_path)
    _stats(tmp_path / 'projects', 'p1', {})  # ensure projects dir + sidecar
    for i in range(distiller._VOCAB_MISS_CAP + 25):
        distiller._record_vocab_miss('p1', f'frobnicate-thing-{i}')
    stats = distiller._read_skill_stats('p1')
    assert len(stats['vocab_misses']) == distiller._VOCAB_MISS_CAP
    assert stats['counters']['vocabulary_miss'] == distiller._VOCAB_MISS_CAP + 25


def test_never_raises_on_empty(tmp_path):
    _setup(tmp_path)
    snap = distiller.loop_health()
    assert snap['queue']['total'] == 0
    assert snap['readback']['hit_rate'] is None
    assert isinstance(snap['alerts'], list)
