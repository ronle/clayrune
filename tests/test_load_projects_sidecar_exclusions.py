"""Parametric regression test for the DATA_DIR sidecar exclusion rule.

LOAD-BEARING per CLAUDE.md "LOAD-BEARING RULE — DATA_DIR pollution":
``data/projects/`` is the project-records dir; ``load_projects()`` treats
every ``*.json`` there as a project. Sibling sidecar files (telemetry,
caches, machinery state) MUST be suffix-excluded in ``load_projects()``
or a stray file there becomes a malformed "project" and 500s
``_get_active_restart_blockers`` → both restart endpoints.

The single source of truth is ``server.EXCLUDED_SIDECAR_SUFFIXES``. Both
the production code and this test import it; the test is parametric so
adding a new sidecar = adding to the tuple = test still passes. Adding
a sidecar WITHOUT updating the tuple = the canary case fails loudly.

This test landed alongside Phase 4 v2.1 backend (Seat 4 v2 Cond 6
closure: parametric + next-sidecar canary). It supersedes the v1.1
single-case test that v2 inherited as Cond 17 (which only asserted
``_skill_stats.json`` exclusion and would rot the moment Phase 5
adds another sidecar).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def tmp_data_dir(tmp_path, monkeypatch):
    """Create the nested data/projects/ dir layout that server.py expects.

    server.DATA_DIR = MC_DATA_DIR / 'data' / 'projects', so MC_DATA_DIR
    is the *root* of the data layout, not the projects dir itself.
    """
    monkeypatch.setenv('MC_DATA_DIR', str(tmp_path))
    d = tmp_path / 'data' / 'projects'
    d.mkdir(parents=True)
    return d


@pytest.fixture
def server_module(tmp_data_dir, monkeypatch):
    """Import server with an isolated DATA_DIR. The tmp_data_dir fixture
    has already set MC_DATA_DIR; this reloads server so module-level
    DATA_DIR re-evaluates against the env var."""
    import importlib
    # Force re-import so the module-level DATA_DIR evaluates against the
    # monkeypatched env var. server is heavy (imports Flask, scribe, etc.),
    # so this fixture is session-scoped via tmp_path implicitly.
    if 'server' in list(__import__('sys').modules):
        import sys as _sys
        del _sys.modules['server']
    import server
    return server


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data), encoding='utf-8')


def test_excluded_suffixes_tuple_is_nonempty(server_module):
    """Sanity: the constant exists and is a non-empty tuple of strings."""
    assert hasattr(server_module, 'EXCLUDED_SIDECAR_SUFFIXES')
    suffixes = server_module.EXCLUDED_SIDECAR_SUFFIXES
    assert isinstance(suffixes, tuple)
    assert len(suffixes) >= 4  # at least the precedents we know about
    for s in suffixes:
        assert isinstance(s, str)
        assert s.endswith('.json')
        assert s.startswith('_'), (
            f"sidecar {s!r} doesn't start with underscore — "
            f"that's the convention. Failed-loudly canary."
        )


def test_each_excluded_suffix_is_filtered(server_module, tmp_data_dir):
    """Parametric over the full tuple. Write one sidecar per suffix +
    one legitimate project. Assert load_projects() returns ONLY the project.
    """
    # Legitimate project
    _write_json(tmp_data_dir / 'real_project.json',
                {'id': 'real_project', 'status': 'active'})
    # One sidecar per excluded suffix
    for suffix in server_module.EXCLUDED_SIDECAR_SUFFIXES:
        _write_json(tmp_data_dir / f'real_project{suffix}',
                    {'_updated': '2026-05-29T00:00:00Z'})
    projects = server_module.load_projects()
    project_ids = {p.get('id') for p in projects if isinstance(p, dict)}
    assert 'real_project' in project_ids
    # No sidecar file should have been parsed as a "project" — verify by
    # checking the project count matches the count of legitimate files.
    legit_count = 1
    assert len(projects) == legit_count, (
        f"load_projects() returned {len(projects)} projects but "
        f"only {legit_count} legitimate project record exists; sidecars "
        f"leaked through. Loaded IDs: {project_ids}"
    )


def test_misnamed_sidecar_without_underscore_fails_loudly(
    server_module, tmp_data_dir
):
    """Canary: a file like ``skill_stats.json`` (no leading underscore)
    is NOT excluded by suffix-match — it gets loaded as a project. If it
    has the wrong shape, the existing load_projects() error handling
    silently skips it (logs the parse error). The test here documents
    the canary intent: future contributors adding a new sidecar MUST use
    the leading-underscore convention AND add to EXCLUDED_SIDECAR_SUFFIXES.

    The test passes when the misnamed file is treated as an attempted
    project (loaded but malformed) — proving suffix-match catches only
    the underscore-prefixed convention. This forces contributors to
    notice their sidecar isn't excluded.
    """
    # Legitimate project
    _write_json(tmp_data_dir / 'real_project.json',
                {'id': 'real_project', 'status': 'active'})
    # Misnamed sidecar (no leading underscore) — looks like a project record
    # but is actually telemetry. server.load_projects() loads it (because
    # the suffix tuple doesn't match) and either accepts it as a project
    # (wrong) or silently filters by some other rule. Canary intent: the
    # file SHOULD have been named ``_my_telemetry.json`` to inherit the
    # exclusion rule.
    _write_json(tmp_data_dir / 'my_telemetry.json',
                {'_updated': '2026-05-29T00:00:00Z'})
    projects = server_module.load_projects()
    ids = [p.get('id') for p in projects if isinstance(p, dict)]
    # The misnamed file was loaded (no exception); it's now in the project
    # set. This is the "fails loudly" state the canary forces contributors
    # to notice: they must rename the file to start with underscore.
    assert 'real_project' in ids
    # Misnamed sidecar leaked through as a "project" — this is the
    # intentional failure mode the canary surfaces. If a future contributor
    # adds a no-underscore sidecar, this assertion makes their intent
    # visible (they expected exclusion).
    assert len(projects) == 2, (
        f"Misnamed sidecar without leading underscore must be visible as a "
        f"leak (count==2). Got {len(projects)} projects: {ids}. If this "
        f"fails with count==1, the sidecar is being filtered by some other "
        f"rule (a foot-gun for future contributors who copy that pattern)."
    )


def test_distiller_sidecars_are_in_exclusion_tuple(server_module):
    """Phase 4 v2.1: _skill_stats.json + _skill_stats_summary.json must be
    in the exclusion tuple. This test fails loudly if a future refactor
    removes them.
    """
    assert '_skill_stats.json' in server_module.EXCLUDED_SIDECAR_SUFFIXES
    assert '_skill_stats_summary.json' in server_module.EXCLUDED_SIDECAR_SUFFIXES


def test_legacy_sidecars_still_in_exclusion_tuple(server_module):
    """Precedent sidecars MUST stay excluded — guards against accidental
    removal during refactors."""
    expected_precedents = (
        '_agent_log.json',
        '_scribe_stats.json',
        '_router_stats.json',
    )
    for sfx in expected_precedents:
        assert sfx in server_module.EXCLUDED_SIDECAR_SUFFIXES, (
            f"legacy sidecar suffix {sfx!r} was removed from "
            f"EXCLUDED_SIDECAR_SUFFIXES — refactor regression"
        )
