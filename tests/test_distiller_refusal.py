"""REFUSE-leak regression — _is_refusal must catch the model's decline.

Bug (2026-06-05): the three renderers (_render_skill / _render_exploration /
_render_preference) called the cheap model, got "REFUSE", then ran the output
through _wrap_skill_body — which prepends YAML frontmatter. The caller's guard
`body.strip() == 'REFUSE'` then never matched (the wrapped body starts with
`---`), so the refusal was persisted as a real artifact. The clayrune_website
`distilled-969b3b91` SKILL.md shipped with a body of literally "REFUSE".

Fix: check the RAW model output via _is_refusal() inside each renderer, before
wrapping. This test pins that behavior so the leak can't silently return.
"""
from __future__ import annotations

import distiller


def test_is_refusal_exact_sentinel():
    assert distiller._is_refusal('REFUSE') is True
    assert distiller._is_refusal('  REFUSE  ') is True
    assert distiller._is_refusal('REFUSE\n') is True


def test_is_refusal_short_variants():
    # Lenient: short responses that are just the sentinel + punctuation.
    assert distiller._is_refusal('REFUSE.') is True
    assert distiller._is_refusal('refuse') is True


def test_is_refusal_empty_is_refusal():
    assert distiller._is_refusal('') is True
    assert distiller._is_refusal(None) is True


def test_is_refusal_backtick_and_rationale_forms():
    # 2026-06-08 leak: the model emits the sentinel wrapped in backticks and/or
    # followed by a rationale paragraph. The old `== 'REFUSE' or len<=12` guard
    # missed both, writing them to the queue as junk artifacts.
    assert distiller._is_refusal('`REFUSE`') is True
    assert distiller._is_refusal('`REFUSE`\n\nThis is a single observation, '
                                 'not a recurring preference.') is True
    assert distiller._is_refusal('REFUSE\n\nThe evidence quote is delegatory '
                                 'and does not express a preference.') is True
    assert distiller._is_refusal('```\nREFUSE\n```') is True
    assert distiller._is_refusal('**REFUSE**') is True


def test_is_refusal_fenced_real_artifact_is_not_refusal():
    # A real artifact the model wrapped in a ```markdown fence must NOT be
    # treated as a refusal (the first content line is the heading, not REFUSE).
    body = "```markdown\n# Avoid full IDE download when only CLI tools are needed\n\n## Why\n...\n```"
    assert distiller._is_refusal(body) is False


def test_strip_code_fences():
    assert distiller._strip_code_fences('```markdown\n# Title\n\nbody\n```') == '# Title\n\nbody'
    assert distiller._strip_code_fences('```\nplain\n```') == 'plain'
    # No fence → unchanged (trimmed)
    assert distiller._strip_code_fences('# Title\n\nbody\n') == '# Title\n\nbody'


def test_is_refusal_real_artifact_is_not_refusal():
    # A genuine artifact body must NOT be treated as a refusal, even if the
    # word "refuse" appears somewhere in the prose.
    body = (
        "# Code-signing macOS apps\n\n"
        "The notarytool service will refuse submissions without a hardened "
        "runtime flag. Use codesign with the Developer ID certificate.\n"
    )
    assert distiller._is_refusal(body) is False


def test_wrapped_refusal_no_longer_passes_caller_guard(monkeypatch):
    """The original leak path: REFUSE wrapped in frontmatter. The raw-output
    check must fire BEFORE wrapping so this composed string is never produced.
    Here we assert the wrapped form would have defeated the old guard, proving
    why the check has to be on the raw output."""
    # _now_iso is wired from server at import; stub it for standalone runs.
    monkeypatch.setattr(distiller, '_now_iso', lambda: '2026-06-05T00:00:00Z')
    candidate = {
        'evidence_signals': [{'sid': 'abc123'}],
        'scope_tag': 'project-specific',
        'exact': 'deadbeefdeadbeef',
        'coarse': 'cafef00dcafef00d',
        'recurrence_exact': 3,
        'recurrence_coarse': 3,
    }
    wrapped = distiller._wrap_skill_body(
        'REFUSE', 'proj', candidate, kind='skill', name_slug='x')
    # Old guard checked the wrapped body — it starts with '---', so it slips.
    assert wrapped.strip() != 'REFUSE'
    # New guard checks the raw output — it catches it.
    assert distiller._is_refusal('REFUSE') is True
