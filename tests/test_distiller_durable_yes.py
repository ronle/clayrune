"""Durable-"yes" — an INSTALLED artifact's pattern must not re-enter the queue.

Mirror of the durable-"no" rail (test_distiller_safety.py §3). Two real holes
found in the 2026-07-16 queue triage, both live in _proposed/ while the same
artifact sat installed in ~/.claude/skills/:

  1. KIND DRIFT — 94084ae5 was promoted as kind=skill; the Distiller later
     re-proposed the same fingerprint as kind=preference. Suppression keys on
     (fingerprint, kind) — the ratified committee keying, correct for
     rejections — so the promote record couldn't see the re-proposal.
  2. MISSING HISTORY — b4e4b1bf was promoted before the 2026-07-11
     global-suppression fix; no suppression record exists anywhere.

Fix under test: `_installed_exact_fingerprints` + the generation-time skip.
The install itself is the record (dynamic, any kind); uninstalling makes the
pattern proposable again — the semantics a promotion revert needs.
Explorations are exempt (never installed as themselves).
"""
from pathlib import Path

import distiller


FP = 'aabb112233445566'


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(distiller, '_data_root', tmp_path / 'projects')
    monkeypatch.setattr(distiller, '_skills_root', tmp_path / 'skills')
    monkeypatch.setattr(distiller, '_installed_skills_roots',
                        [tmp_path / 'installed'])
    monkeypatch.setattr(distiller, '_atomic_write_text',
                        lambda p, t: Path(p).write_text(t, encoding='utf-8'))
    monkeypatch.setattr(distiller, '_now_iso', lambda: '2026-07-16T00:00:00Z')
    (tmp_path / 'projects').mkdir(parents=True, exist_ok=True)


def _install(tmp_path, name='preference-aabb', fp=FP):
    d = tmp_path / 'installed' / name
    d.mkdir(parents=True, exist_ok=True)
    (d / 'SKILL.md').write_text(
        f"---\nname: {name}\ndescription: x\nprovenance: distilled-promoted\n"
        f"promoted_from: skill\nextraction_fingerprint_exact: {fp}\n---\n\nbody\n",
        encoding='utf-8')
    return d


def _candidate(kind='preference', fp=FP):
    return {'kind': kind, 'exact': fp, 'coarse': 'c' * 16,
            'scope_tag': 'project-specific', 'phrase': 'test-pattern',
            'evidence_signals': [], 'unattended': False}


def _drive_generation(monkeypatch, project_id, candidate):
    """Run _generate_and_write_artifact with the model call stubbed to a
    valid body, returning whether an artifact landed in _proposed/."""
    monkeypatch.setattr(distiller, '_distiller_should_proceed',
                        lambda pid, ep: True)
    monkeypatch.setattr(
        distiller, '_render_preference',
        lambda pid, proj, cand: (
            "---\nkind: preference\nname: test-pattern\n---\n\n# T\n\nSafe.",
            distiller._proposal_target(pid, cand['scope_tag'],
                                       cand['kind'], cand['exact'],
                                       'test-pattern')))
    monkeypatch.setattr(
        distiller, '_render_exploration',
        lambda pid, proj, cand: (
            "---\nkind: exploration\nname: test-pattern\n---\n\n# T\n\nSafe.",
            distiller._proposal_target(pid, cand['scope_tag'],
                                       cand['kind'], cand['exact'],
                                       'test-pattern')))
    distiller._generate_and_write_artifact(project_id, {'id': project_id},
                                           candidate)
    root = distiller._skills_root / '_proposed' / project_id
    return root.is_dir() and any(root.iterdir())


def test_installed_fingerprint_blocks_same_kind(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _install(tmp_path)
    assert not _drive_generation(monkeypatch, 'proj_a',
                                 _candidate('preference'))


def test_installed_fingerprint_blocks_across_kinds(tmp_path, monkeypatch):
    """The 94084ae5 incident: promoted as SKILL, re-proposed as PREFERENCE."""
    _setup(tmp_path, monkeypatch)
    _install(tmp_path, name='skill-aabb')   # installed under kind=skill
    assert not _drive_generation(monkeypatch, 'proj_a',
                                 _candidate('preference'))


def test_no_suppression_record_needed(tmp_path, monkeypatch):
    """The b4e4b1bf incident: installed pre-07-11, zero suppression records.
    The install alone must block re-proposal."""
    _setup(tmp_path, monkeypatch)
    _install(tmp_path)
    stats = distiller._read_skill_stats('proj_a')
    assert not stats.get('suppressions'), 'precondition: no records'
    assert not _drive_generation(monkeypatch, 'proj_a',
                                 _candidate('preference'))


def test_uninstall_makes_pattern_proposable_again(tmp_path, monkeypatch):
    """Dynamic semantics: removing the installed artifact (a promotion
    revert) restores proposability — no permanent mark."""
    _setup(tmp_path, monkeypatch)
    d = _install(tmp_path)
    assert not _drive_generation(monkeypatch, 'proj_a',
                                 _candidate('preference'))
    (d / 'SKILL.md').unlink()
    d.rmdir()
    assert _drive_generation(monkeypatch, 'proj_a', _candidate('preference'))


def test_exploration_exempt_from_installed_check(tmp_path, monkeypatch):
    """Explorations are never installed as themselves — a reframe-promoted
    exploration installs under the reframed skill's identity — so the
    installed-fingerprint check must not starve exploration retention."""
    _setup(tmp_path, monkeypatch)
    _install(tmp_path)
    assert _drive_generation(monkeypatch, 'proj_a', _candidate('exploration'))


def test_fresh_fingerprint_unaffected(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _install(tmp_path, fp='9999888877776666')
    assert _drive_generation(monkeypatch, 'proj_a', _candidate('preference'))


def test_counter_records_the_skip(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    _install(tmp_path)
    _drive_generation(monkeypatch, 'proj_a', _candidate('preference'))
    stats = distiller._read_skill_stats('proj_a')
    assert stats['counters'].get('skipped_installed:preference') == 1
