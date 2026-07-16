"""Promotion / rejection regression — the human-promotes leg (step 3).

read_proposed_artifact + mark_promoted + reject_proposed move artifacts out of
_proposed/ and write suppression markers so the Distiller won't re-propose.
Pins: the path-traversal guard, project_id derivation from the scope dir,
suppression write, and relocation to sibling buckets (never under _proposed/).
"""
from __future__ import annotations

import json
from pathlib import Path

import distiller


def _make_artifact(skills_root: Path, scope_dir: str, slug: str, kind='skill',
                   exact='deadbeefdeadbeef'):
    d = skills_root / '_proposed' / scope_dir / f"2026-06-05T00-00-00-aaaa-{slug}"
    d.mkdir(parents=True, exist_ok=True)
    fname = {'skill': 'SKILL.md', 'exploration': 'EXPLORATION.md',
             'preference': 'PREFERENCE.md'}[kind]
    (d / fname).write_text(
        "---\n"
        f"kind: {kind}\n"
        f"name: {slug}\n"
        f"extraction_fingerprint_exact: {exact}\n"
        "extraction_scope: project-specific\n"
        "created_at: 2026-06-05T00:00:00Z\n"
        "---\n\n"
        "# A real title\n\nSome body content.\n",
        encoding='utf-8')
    return d


def _setup(tmp_path):
    distiller._skills_root = tmp_path / 'skills'
    distiller._data_root = tmp_path / 'projects'
    (tmp_path / 'projects').mkdir(parents=True, exist_ok=True)
    # Server-injected helpers — stub for standalone runs so the stats-write
    # path inside _suppress_artifact actually executes.
    distiller._atomic_write_text = lambda p, t: Path(p).write_text(
        t, encoding='utf-8')
    distiller._now_iso = lambda: '2026-06-05T00:00:00Z'


def test_read_artifact_fields(tmp_path):
    _setup(tmp_path)
    d = _make_artifact(tmp_path / 'skills', 'myproj', 'fix-the-thing')
    art = distiller.read_proposed_artifact(str(d))
    assert art is not None
    assert art['kind'] == 'skill'
    assert art['name'] == 'fix-the-thing'
    assert art['project_id'] == 'myproj'
    assert art['exact'] == 'deadbeefdeadbeef'
    assert 'Some body content.' in art['body']
    # frontmatter must be stripped from body
    assert 'kind: skill' not in art['body']


def test_description_synthesized_for_exploration(tmp_path):
    _setup(tmp_path)
    d = _make_artifact(tmp_path / 'skills', 'myproj', 'why-x', kind='exploration')
    art = distiller.read_proposed_artifact(str(d))
    # no frontmatter description → first heading used
    assert art['description'] == 'A real title'


def test_path_traversal_guard(tmp_path):
    _setup(tmp_path)
    _make_artifact(tmp_path / 'skills', 'myproj', 'fix-the-thing')
    # A dir outside _proposed/ must be refused.
    assert distiller.read_proposed_artifact(str(tmp_path)) is None
    assert distiller.read_proposed_artifact(str(tmp_path / 'skills')) is None
    assert distiller._is_within_proposed(str(tmp_path / 'etc' / 'passwd')) is None


def test_global_artifact_has_no_project_id(tmp_path):
    _setup(tmp_path)
    d = _make_artifact(tmp_path / 'skills', 'global', 'cross-thing')
    art = distiller.read_proposed_artifact(str(d))
    assert art['project_id'] is None


def test_reject_suppresses_and_moves(tmp_path):
    _setup(tmp_path)
    d = _make_artifact(tmp_path / 'skills', 'myproj', 'bad-idea', exact='cafef00dcafef00d')
    res = distiller.reject_proposed(str(d))
    assert res['ok'] is True
    assert res['suppressed'] is True
    # original gone from _proposed/, present in _rejected/
    assert not d.exists()
    assert (tmp_path / 'skills' / '_rejected').exists()
    # suppression written keyed {exact}:{kind}
    stats = json.loads(
        (tmp_path / 'projects' / 'myproj_skill_stats.json').read_text())
    assert 'cafef00dcafef00d:skill' in stats['suppressions']
    assert stats['suppressions']['cafef00dcafef00d:skill']['decision'] == 'no'
    assert stats['suppressions']['cafef00dcafef00d:skill']['source'] == 'ui_reject'


def test_mark_promoted_suppresses_and_moves(tmp_path):
    _setup(tmp_path)
    d = _make_artifact(tmp_path / 'skills', 'myproj', 'good-idea')
    res = distiller.mark_promoted(str(d))
    assert res['ok'] is True
    assert res['suppressed'] is True
    assert not d.exists()
    assert (tmp_path / 'skills' / '_promoted').exists()


def test_global_reject_is_durably_suppressed(tmp_path):
    """Rejecting a cross-project artifact used to record NOTHING (no owning
    project stats file), so the Distiller re-proposed it and it could still be
    promoted later — preference-1ba8d678 was live in ~/.claude/skills/ while
    sitting in _rejected/ (2026-07-11). Global rejections now persist to the
    reserved _global store and bind every project. See tests/test_distiller_
    safety.py::test_global_rejection_is_durable_across_projects."""
    _setup(tmp_path)
    d = _make_artifact(tmp_path / 'skills', 'global', 'cross-thing')
    res = distiller.reject_proposed(str(d))
    assert res['ok'] is True
    assert res['suppressed'] is True
    assert not d.exists()


def test_rejected_bucket_not_relisted(tmp_path):
    """_rejected/ and _promoted/ are siblings of _proposed/, so list_proposed()
    must not surface their contents (the underscore-name re-listing trap)."""
    _setup(tmp_path)
    d = _make_artifact(tmp_path / 'skills', 'myproj', 'bad-idea')
    distiller.reject_proposed(str(d))
    assert distiller.list_proposed() == []


def test_degenerate_frontmatter_description_falls_through(tmp_path, monkeypatch):
    """2026-07-16 incident: a proposal carrying `description: "Why"` (a
    section heading captured at render time) survived promotion and
    overwrote a good installed skill's description. A description too short
    to ever trigger must fall through to the body's first substantial line
    — which for preference bodies is the plain TITLE line, not the '## Why'
    heading _first_heading would land on."""
    import distiller
    monkeypatch.setattr(distiller, '_skills_root', tmp_path / 'skills')
    d = tmp_path / 'skills' / '_proposed' / 'proj_x' / '2026-07-09T00-00-00-abcd-preference-abcd'
    d.mkdir(parents=True)
    (d / 'PREFERENCE.md').write_text(
        "---\nkind: preference\nname: preference-abcd\ndescription: \"Why\"\n"
        "extraction_fingerprint_exact: aaaabbbbccccdddd\n---\n"
        "Use the native learning-item system for cross-project knowledge\n\n"
        "## Why\nBecause reasons.\n",
        encoding='utf-8')
    art = distiller.read_proposed_artifact(str(d))
    assert art is not None
    assert art['description'] == (
        'Use the native learning-item system for cross-project knowledge')
