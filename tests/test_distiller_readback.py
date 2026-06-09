"""Exploration read-floor regression — the learning-loop closer.

exploration_read_floor() surfaces the Distiller's captured EXPLORATION.md
proposals back into a new session's context. Without it, _proposed/
explorations are write-only and never change agent behavior (the system
journals but does not learn). These tests pin the ranking + scoping contract.
"""
from __future__ import annotations

from pathlib import Path

import distiller


def _write_expl(root: Path, scope_dir: str, slug: str, title: str, body: str):
    d = root / '_proposed' / scope_dir / f"2026-06-05T00-00-00-aaaaaaaaaaaa-{slug}"
    d.mkdir(parents=True, exist_ok=True)
    (d / 'EXPLORATION.md').write_text(
        "---\n"
        "kind: exploration\n"
        f"name: {slug}\n"
        f"extraction_scope: {'cross-project' if scope_dir == 'global' else 'project-specific'}\n"
        "created_at: 2026-06-05T00:00:00Z\n"
        "---\n\n"
        f"# {title}\n\n{body}\n",
        encoding='utf-8')


def _setup(tmp_path):
    distiller._skills_root = tmp_path
    _write_expl(tmp_path, 'myproj', 'why-do-limit-orders-not-fill',
                'Why do limit orders not fill', 'Checked entry prices.')
    _write_expl(tmp_path, 'global', 'how-to-notarize-macos-apps',
                'Notarizing macOS apps', 'Use codesign and notarytool.')
    _write_expl(tmp_path, 'otherproj', 'unrelated-project-thing',
                'Unrelated', 'Nothing to see.')


def test_matches_project_scoped_exploration(tmp_path):
    _setup(tmp_path)
    hits = distiller.exploration_read_floor(
        'myproj', 'why are limit orders not filling', 2)
    names = [h['name'] for h in hits]
    assert 'why-do-limit-orders-not-fill' in names


def test_includes_cross_project_global(tmp_path):
    _setup(tmp_path)
    hits = distiller.exploration_read_floor(
        'myproj', 'how do I notarize the macos app', 2)
    assert any(h['name'] == 'how-to-notarize-macos-apps' for h in hits)
    assert hits[0]['scope'] == 'cross-project'


def test_excludes_other_projects(tmp_path):
    """An exploration scoped to a DIFFERENT project must never surface."""
    _setup(tmp_path)
    hits = distiller.exploration_read_floor(
        'myproj', 'unrelated project thing', 2)
    assert all(h['name'] != 'unrelated-project-thing' for h in hits)


def test_no_match_returns_empty(tmp_path):
    _setup(tmp_path)
    hits = distiller.exploration_read_floor(
        'myproj', 'quantum basket weaving aardvark', 2)
    assert hits == []


def test_empty_task_returns_empty(tmp_path):
    _setup(tmp_path)
    assert distiller.exploration_read_floor('myproj', '', 2) == []


def test_snippet_strips_frontmatter(tmp_path):
    _setup(tmp_path)
    hits = distiller.exploration_read_floor(
        'myproj', 'limit orders fill', 1)
    assert hits
    snip = hits[0]['snippet']
    assert 'kind: exploration' not in snip
    assert '---' not in snip
    assert 'Why do limit orders not fill' in snip
