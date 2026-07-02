"""FIX 2a/2b regression — exploration→skill reframe + extraction tuning.

FIX 2a (reframe_exploration_to_skill): the sanctioned exploration→skill bridge.
A human-selected EXPLORATION is INVERTED by an LLM into a TRIGGER+procedure
skill, or REFUSEd when there's no reusable procedure. This is NOT the
promote-the-body-as-is path rejected 2026-06-06 — these tests pin that the
reframe only fires on explorations, refuses cleanly, strips frontmatter/fences,
and falls back sanely when the model omits frontmatter.

FIX 2b (extraction prompt): topics must carry recognition-bound problem/
resolution. Pins the worked example + procedure-preference guidance in the
extraction prompt so a future edit can't silently drop them.
"""
from __future__ import annotations

from pathlib import Path

import distiller


def _make_exploration(skills_root: Path, scope_dir: str, slug: str,
                      exact='deadbeefdeadbeef', body='# Why did X fail?\n\n'
                      '## Paths tried\n- checked config: wrong\n\n'
                      '## What worked\nrestart the worker\n'):
    d = skills_root / '_proposed' / scope_dir / f"2026-06-05T00-00-00-aaaa-{slug}"
    d.mkdir(parents=True, exist_ok=True)
    (d / 'EXPLORATION.md').write_text(
        "---\n"
        "kind: exploration\n"
        f"name: {slug}\n"
        f"extraction_fingerprint_exact: {exact}\n"
        "extraction_scope: project-specific\n"
        "created_at: 2026-06-05T00:00:00Z\n"
        "---\n\n" + body,
        encoding='utf-8')
    return d


def _make_skill(skills_root: Path, scope_dir: str, slug: str):
    d = skills_root / '_proposed' / scope_dir / f"2026-06-05T00-00-00-bbbb-{slug}"
    d.mkdir(parents=True, exist_ok=True)
    (d / 'SKILL.md').write_text(
        "---\nkind: skill\nname: " + slug + "\n---\n\n# A skill\n\nbody\n",
        encoding='utf-8')
    return d


def _setup(tmp_path):
    distiller._skills_root = tmp_path / 'skills'
    distiller._data_root = tmp_path / 'projects'
    (tmp_path / 'projects').mkdir(parents=True, exist_ok=True)


# ── FIX 2a: reframe ──────────────────────────────────────────────────────────

def test_reframe_only_applies_to_explorations(tmp_path, monkeypatch):
    _setup(tmp_path)
    monkeypatch.setattr(distiller, '_scribe_call',
                        lambda *a, **k: '# should not be called')
    d = _make_skill(tmp_path / 'skills', 'myproj', 'already-a-skill')
    assert distiller.reframe_exploration_to_skill(str(d)) is None


def test_reframe_refusal_returns_none(tmp_path, monkeypatch):
    _setup(tmp_path)
    monkeypatch.setattr(distiller, '_scribe_call', lambda *a, **k: 'REFUSE')
    d = _make_exploration(tmp_path / 'skills', 'myproj', 'trivial-lookup')
    assert distiller.reframe_exploration_to_skill(str(d)) is None


def test_reframe_refusal_with_rationale_returns_none(tmp_path, monkeypatch):
    _setup(tmp_path)
    monkeypatch.setattr(
        distiller, '_scribe_call',
        lambda *a, **k: '`REFUSE`\n\nOne-off lookup, no reusable procedure.')
    d = _make_exploration(tmp_path / 'skills', 'myproj', 'one-off')
    assert distiller.reframe_exploration_to_skill(str(d)) is None


def test_reframe_produces_skill_from_frontmatter(tmp_path, monkeypatch):
    _setup(tmp_path)
    model_out = (
        "---\n"
        "name: restart-stalled-worker\n"
        "description: TRIGGER when a worker logs zero output for hours despite "
        "a healthy status check\n"
        "---\n\n"
        "# Restart a stalled async worker\n\n"
        "1. Confirm the health check is 200 but the worker log is silent.\n"
        "2. Restart the worker process.\n\n"
        "## Anti-patterns\n- Don't just bump the config — the worker was wedged.\n"
    )
    monkeypatch.setattr(distiller, '_scribe_call', lambda *a, **k: model_out)
    d = _make_exploration(tmp_path / 'skills', 'myproj', 'why-worker-silent')
    out = distiller.reframe_exploration_to_skill(str(d))
    assert out is not None
    assert out['name'] == 'restart-stalled-worker'
    assert out['description'].startswith('TRIGGER when')
    # body carries the procedure, NOT the frontmatter
    assert '1. Confirm the health check' in out['body']
    assert 'Anti-patterns' in out['body']
    assert 'name: restart-stalled-worker' not in out['body']


def test_reframe_strips_code_fences(tmp_path, monkeypatch):
    _setup(tmp_path)
    fenced = ("```markdown\n---\nname: do-the-thing\n"
              "description: TRIGGER when the thing happens\n---\n\n"
              "# Do the thing\n\nstep one\n```")
    monkeypatch.setattr(distiller, '_scribe_call', lambda *a, **k: fenced)
    d = _make_exploration(tmp_path / 'skills', 'myproj', 'thing')
    out = distiller.reframe_exploration_to_skill(str(d))
    assert out is not None
    assert out['name'] == 'do-the-thing'
    assert '```' not in out['body']
    assert 'step one' in out['body']


def test_reframe_missing_frontmatter_falls_back(tmp_path, monkeypatch):
    _setup(tmp_path)
    # Model returns a bare body (no frontmatter). name falls back to the
    # artifact name; description to the first heading.
    monkeypatch.setattr(distiller, '_scribe_call',
                        lambda *a, **k: '# Recover the pipeline\n\ndo x then y\n')
    d = _make_exploration(tmp_path / 'skills', 'myproj', 'recover-pipeline')
    out = distiller.reframe_exploration_to_skill(str(d))
    assert out is not None
    assert out['name'] == 'recover-pipeline'
    assert out['description'] == 'Recover the pipeline'
    assert 'do x then y' in out['body']


def test_reframe_not_found_returns_none(tmp_path, monkeypatch):
    _setup(tmp_path)
    monkeypatch.setattr(distiller, '_scribe_call', lambda *a, **k: '# x')
    # A path outside _proposed/ is refused by the read guard.
    assert distiller.reframe_exploration_to_skill(str(tmp_path)) is None


def test_reframe_scribe_exception_returns_none(tmp_path, monkeypatch):
    _setup(tmp_path)
    def _boom(*a, **k):
        raise RuntimeError('model down')
    monkeypatch.setattr(distiller, '_scribe_call', _boom)
    d = _make_exploration(tmp_path / 'skills', 'myproj', 'why-x')
    # Best-effort: a model failure never raises, just declines.
    assert distiller.reframe_exploration_to_skill(str(d)) is None


def test_reframe_empty_body_returns_none(tmp_path, monkeypatch):
    _setup(tmp_path)
    # Frontmatter only, no body → no skill to install.
    monkeypatch.setattr(distiller, '_scribe_call',
                        lambda *a, **k: '---\nname: empty\n---\n\n')
    d = _make_exploration(tmp_path / 'skills', 'myproj', 'empty-one')
    assert distiller.reframe_exploration_to_skill(str(d)) is None


# ── FIX 2b: extraction prompt tuning ─────────────────────────────────────────

def test_extraction_prompt_prefers_procedure_bearing_topics():
    p = distiller._extraction_prompt('proj', {})
    assert 'PREFER' in p and 'problem was observed AND resolved' in p


def test_extraction_prompt_has_worked_example():
    p = distiller._extraction_prompt('proj', {})
    # The concrete good/bad topic example must survive future edits.
    assert 'fix-condense-timeout' in p
    assert 'WORKED EXAMPLE' in p
    # And the explicit anti-padding instruction.
    assert 'honest empty resolution is better than a fabricated one' in p
