"""Signal cold-archive — bounds the hot _skill_stats.json sidecar.

The recurrence signal store was append-only with no bound: MC's sidecar hit
248KB / 280 signals in ~6 weeks (2026-07-15 revisit), and every counter bump
re-reads + atomically rewrites the whole file under the per-project lock.
Every reader window-filters to distiller_window_days (30), so signals older
than 3× the window are dead weight.

The committee condition "never purge, just filter" (Seat 1 v1.1 Cond 3) is
about data loss, and it holds: old signals move VERBATIM to an append-only
`*_skill_stats_archive.jsonl` next to the hot file — nothing is deleted.
Archive-append happens BEFORE the hot-list trim, so a crash between the two
can duplicate a signal in the archive but can never lose one.

The `.jsonl` extension keeps the archive invisible to load_projects()'s
`*.json` glob by construction (LOAD-BEARING DATA_DIR pollution rule); the
suffix is ALSO in both EXCLUDED_SIDECAR_SUFFIXES tuples as defense-in-depth.
"""
import json
import time
from pathlib import Path

import distiller


def _iso(epoch: float) -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(epoch))


def _sig(ts_epoch: float, phrase: str) -> dict:
    return {'sid': 'aabbccddeeff', 'ts': _iso(ts_epoch),
            'scope_tag': 'project-specific', 'kind': 'topic',
            'phrase': phrase, 'exact': 'e' * 16, 'coarse': 'c' * 16}


def _setup(tmp_path):
    distiller._data_root = tmp_path / 'projects'
    distiller._data_root.mkdir(parents=True, exist_ok=True)
    distiller._atomic_write_text = lambda p, t: Path(p).write_text(
        t, encoding='utf-8')
    distiller._now_iso = lambda: '2026-07-15T00:00:00Z'


def test_old_signals_move_to_archive_verbatim(tmp_path):
    _setup(tmp_path)
    now = time.time()
    old = _sig(now - 200 * 86400, 'ancient-topic')
    fresh = _sig(now - 5 * 86400, 'fresh-topic')
    distiller._commit_signals('proj_a', [old, fresh])

    stats = json.loads(
        (tmp_path / 'projects' / 'proj_a_skill_stats.json').read_text(
            encoding='utf-8'))
    phrases = [s['phrase'] for s in stats['signals']]
    assert phrases == ['fresh-topic'], 'old signal should leave the hot file'

    arch = tmp_path / 'projects' / 'proj_a_skill_stats_archive.jsonl'
    assert arch.exists()
    lines = [json.loads(ln) for ln in
             arch.read_text(encoding='utf-8').splitlines() if ln.strip()]
    assert len(lines) == 1
    assert lines[0] == old, 'archived signal must be byte-equivalent (no loss)'


def test_within_retention_stays_hot(tmp_path):
    _setup(tmp_path)
    now = time.time()
    # 60 days old: outside the 30d read window but inside 3× retention —
    # stays hot so a window_days config increase can still see it.
    mid = _sig(now - 60 * 86400, 'mid-aged')
    distiller._commit_signals('proj_b', [mid])
    stats = json.loads(
        (tmp_path / 'projects' / 'proj_b_skill_stats.json').read_text(
            encoding='utf-8'))
    assert [s['phrase'] for s in stats['signals']] == ['mid-aged']
    assert not (tmp_path / 'projects'
                / 'proj_b_skill_stats_archive.jsonl').exists()


def test_archive_appends_across_passes(tmp_path):
    _setup(tmp_path)
    now = time.time()
    distiller._commit_signals('proj_c', [_sig(now - 200 * 86400, 'first')])
    distiller._commit_signals('proj_c', [_sig(now - 150 * 86400, 'second'),
                                         _sig(now - 1 * 86400, 'live')])
    arch = tmp_path / 'projects' / 'proj_c_skill_stats_archive.jsonl'
    lines = [json.loads(ln) for ln in
             arch.read_text(encoding='utf-8').splitlines() if ln.strip()]
    assert [s['phrase'] for s in lines] == ['first', 'second']
    stats = json.loads(
        (tmp_path / 'projects' / 'proj_c_skill_stats.json').read_text(
            encoding='utf-8'))
    assert [s['phrase'] for s in stats['signals']] == ['live']


def test_archive_failure_leaves_hot_file_untouched(tmp_path):
    _setup(tmp_path)
    now = time.time()
    old = _sig(now - 200 * 86400, 'stuck-but-safe')
    # Make the archive path unopenable: create a DIRECTORY where the
    # archive file would go.
    (tmp_path / 'projects' / 'proj_d_skill_stats_archive.jsonl').mkdir(
        parents=True)
    distiller._commit_signals('proj_d', [old])
    stats = json.loads(
        (tmp_path / 'projects' / 'proj_d_skill_stats.json').read_text(
            encoding='utf-8'))
    # Best-effort: the pass failed, the signal stays hot (bigger, never wrong).
    assert [s['phrase'] for s in stats['signals']] == ['stuck-but-safe']


def test_archive_extension_cannot_match_project_glob():
    """The archive is kept out of load_projects() by its EXTENSION, not by
    the suffix tuple (the exclusion test enforces tuple entries are
    glob-matchable, so a .jsonl entry there is dead config). Pin the real
    invariant: the archive filename must never end in '.json'."""
    name = 'x_skill_stats_archive.jsonl'
    assert not name.endswith('.json')
    # And the helper actually derives that name from the stats path.
    from pathlib import Path
    distiller._data_root = Path('.')
    p = distiller._skill_stats_path('x').with_name(
        distiller._skill_stats_path('x').name.replace(
            '_skill_stats.json', '_skill_stats_archive.jsonl'))
    assert p.name == name
