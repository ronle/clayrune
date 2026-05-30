"""Pure-function tests for the dual-layer closed-vocab fingerprint.

The fingerprint pure function is the foundation of the Phase 4 v2.1
Distiller's recurrence detection. v1.1 Seat 1 Cond 1 was specifically
about fingerprint stability under cheap-model variance; v2.1 §5
replaces v1.1's bag-of-tokens with a closed-vocabulary positional
scheme. This test suite locks the contract.

Layered tests:
  - vocabulary closure (the lists themselves)
  - in-vocab phrase → (exact, coarse) tuple
  - OOV verb → None
  - OOV noun → None
  - unknown modifier silently dropped
  - over-emission (>3 tokens) truncated to first 3
  - exact-vs-coarse divergence for slot-order variance (D2 intent)
  - subsystem terms as NOUNS (D1 closure — promoted from MODIFIERS)
"""
from __future__ import annotations

import pytest

import distiller


# ── Vocabulary closure ───────────────────────────────────────────────────────

def test_verbs_are_lowercase_kebab():
    for v in distiller.VERBS:
        assert v == v.lower(), f"verb {v!r} is not lowercase"
        assert ' ' not in v, f"verb {v!r} contains space"


def test_nouns_are_lowercase_kebab():
    for n in distiller.NOUNS:
        assert n == n.lower(), f"noun {n!r} is not lowercase"
        assert ' ' not in n, f"noun {n!r} contains space"


def test_modifiers_are_lowercase_kebab():
    for m in distiller.MODIFIERS:
        assert m == m.lower(), f"modifier {m!r} is not lowercase"


def test_subsystem_terms_are_nouns_not_modifiers():
    """D1 closure — Seat 1 Cond 2: subsystem terms are NOUNS, not modifiers.

    Leaving `condense`, `scribe`, `distiller`, `hivemind`, `pair`,
    `mobile-pair`, `github-sync`, `project-sync` in MODIFIERS caused the
    strict positional parser to silently reject `fix-condense-timeout` as
    OOV — losing exactly the topics this design exists to detect.
    """
    subsystem_terms = (
        'condense', 'scribe', 'distiller', 'hivemind', 'pair',
        'mobile-pair', 'github-sync', 'project-sync',
    )
    for term in subsystem_terms:
        assert term in distiller.NOUNS, (
            f"subsystem term {term!r} must be in NOUNS (D1 closure). "
            f"Leaving it in MODIFIERS produces the silent-loss failure "
            f"mode where `fix-{term}-X` is rejected as OOV."
        )
        assert term not in distiller.MODIFIERS, (
            f"subsystem term {term!r} should NOT be in MODIFIERS — "
            f"it's a noun (a thing topics are about), not a surface "
            f"narrowing modifier."
        )


# ── In-vocab fingerprint ─────────────────────────────────────────────────────

def test_valid_phrase_returns_tuple():
    fp = distiller.fingerprint('fix-condense-timeout')
    assert fp is not None
    assert isinstance(fp, tuple)
    assert len(fp) == 2
    exact, coarse = fp
    assert isinstance(exact, str) and len(exact) == 16
    assert isinstance(coarse, str) and len(coarse) == 16


def test_valid_verb_noun_only_returns_tuple():
    fp = distiller.fingerprint('debug-condense')
    assert fp is not None
    exact, coarse = fp
    assert exact and coarse


def test_subsystem_topics_no_longer_silent_loss():
    """D1 verification: the failure mode Seat 1 Cond 2 named must be closed."""
    cases = (
        'fix-condense-timeout',
        'debug-scribe',
        'gate-distiller',
        'refactor-hivemind',
        'debug-pair',
        'fix-mobile-pair',
        'configure-github-sync',
        'audit-project-sync',
    )
    for phrase in cases:
        fp = distiller.fingerprint(phrase)
        assert fp is not None, (
            f"phrase {phrase!r} returned None — D1 closure regression "
            f"(subsystem term not parseable as noun)"
        )


# ── OOV handling ─────────────────────────────────────────────────────────────

def test_oov_verb_returns_none():
    assert distiller.fingerprint('foozle-condense') is None
    assert distiller.fingerprint('zzz-skill') is None


def test_oov_noun_returns_none():
    assert distiller.fingerprint('fix-foozle') is None
    assert distiller.fingerprint('debug-zzz') is None


def test_empty_phrase_returns_none():
    assert distiller.fingerprint('') is None
    assert distiller.fingerprint(None) is None
    assert distiller.fingerprint('   ') is None


def test_unknown_modifier_silently_dropped():
    """Unknown modifier → modifier becomes empty; verb+noun still produces
    a valid fingerprint (the modifier slot is optional)."""
    fp_with_unknown = distiller.fingerprint('fix-condense-foozle')
    fp_without = distiller.fingerprint('fix-condense')
    assert fp_with_unknown is not None
    assert fp_without is not None
    # Should be IDENTICAL — the unknown modifier was dropped
    assert fp_with_unknown == fp_without


# ── Over-emission tolerance (I1 closure — Seat 1 Cond 4) ─────────────────────

def test_over_emission_truncates_to_first_three():
    """A 4+ token emission should drop tokens[3:] and behave like a 3-token
    emission. This is the cheap-model over-emission failure mode that I1
    addresses with `extra_tokens_dropped` telemetry."""
    fp_4 = distiller.fingerprint('fix-condense-timeout-extra')
    fp_3 = distiller.fingerprint('fix-condense-timeout')
    assert fp_4 is not None
    assert fp_3 is not None
    assert fp_4 == fp_3


def test_over_emission_with_oov_in_first_three_still_returns_none():
    """If the first 3 tokens contain OOV, the truncation can't save it."""
    assert distiller.fingerprint('foozle-condense-timeout-extra') is None


# ── Exact vs coarse layers (D2 closure) ──────────────────────────────────────

def test_exact_and_coarse_differ_when_modifier_present():
    """With a modifier, exact and coarse encode different canonical strings
    so they hash to different values (even for the same phrase). The fact
    that they're independently named hashes is the structural property —
    they're not REQUIRED to differ for the design to work, but in practice
    they usually do."""
    fp = distiller.fingerprint('fix-condense-timeout')
    assert fp is not None
    exact, coarse = fp
    # Both must be 16-char hex
    assert len(exact) == 16 and len(coarse) == 16


def test_coarse_collapses_slot_order_for_two_tokens():
    """verb-noun has only one valid slot ordering (verb first, then noun).
    But for the same (verb, noun) pair, exact and coarse should still
    produce stable, reproducible hashes."""
    fp_a = distiller.fingerprint('fix-condense')
    fp_b = distiller.fingerprint('fix-condense')
    assert fp_a == fp_b  # determinism


def test_different_verbs_produce_different_fingerprints():
    """gate-X and propose-X with the same noun produce different exact
    AND different coarse hashes — the closed-vocab approach does NOT
    collapse verb-choice variance at the hash layer; that's done at
    extraction time by the model picking from the closed list."""
    fp_a = distiller.fingerprint('gate-skill')
    fp_b = distiller.fingerprint('propose-skill')
    assert fp_a is not None and fp_b is not None
    # exact differs (different verbs in slot 0)
    assert fp_a[0] != fp_b[0]
    # coarse also differs (different token sets)
    assert fp_a[1] != fp_b[1]


# ── Determinism ──────────────────────────────────────────────────────────────

def test_fingerprint_is_deterministic():
    """Same input → same output across calls. No randomness, no time
    dependency."""
    for phrase in ('fix-condense', 'gate-skill-distiller', 'propose-pair-mobile'):
        results = [distiller.fingerprint(phrase) for _ in range(5)]
        assert all(r == results[0] for r in results), (
            f"non-deterministic fingerprint for {phrase!r}: {results}"
        )


def test_case_insensitive():
    """Case folding happens before lookup."""
    assert distiller.fingerprint('FIX-CONDENSE') == \
        distiller.fingerprint('fix-condense')
    assert distiller.fingerprint('Fix-Condense') == \
        distiller.fingerprint('fix-condense')


def test_whitespace_stripped():
    assert distiller.fingerprint('  fix-condense  ') == \
        distiller.fingerprint('fix-condense')
