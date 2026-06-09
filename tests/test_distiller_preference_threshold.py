"""Per-kind recurrence threshold — preferences generate at recurrence 1.

Diagnostic (2026-06-06) proved the recurrence-3 gate starves the knowledge-
bearing kinds: of 7 distinct preference fingerprints, ZERO ever recurred 3x, so
no preference was ever proposed. Preferences carry content (summary +
evidence_quote) and are human-gated at promotion, so they should generate on
first observation. Topics (->skill) stay gated at 3 (a separate content fix).
"""
from __future__ import annotations

import distiller


def _eval(kind, *, exact_count, coarse_count, pref_min_rec=1, min_rec=3,
          extra_signal_fields=None):
    """Drive _evaluate_candidate with synthetic recurrence indexes."""
    exact, coarse = 'e' * 16, 'c' * 16
    sids_e = {f's{i}' for i in range(exact_count)}
    sids_c = {f's{i}' for i in range(coarse_count)}
    kind_exact = {(kind, exact): sids_e}
    kind_coarse = {(kind, coarse): sids_c}
    base = {'kind': kind, 'exact': exact, 'coarse': coarse,
            'scope_tag': 'project-specific', 'sid': 's0'}
    if extra_signal_fields:
        base.update(extra_signal_fields)
    window = [dict(base, sid=s) for s in sids_e]
    return distiller._evaluate_candidate(
        kind=kind, exact=exact, coarse=coarse,
        kind_exact=kind_exact, kind_coarse=kind_coarse,
        suppressions={}, outbox={}, min_rec=min_rec, pref_min_rec=pref_min_rec,
        dedupe_days=7, window_signals=window, new_signals=window)


def test_preference_generates_at_recurrence_1():
    cand = _eval('preference', exact_count=1, coarse_count=1,
                 extra_signal_fields={'summary': 'Prefer X', 'evidence_quote': 'do X'})
    assert cand is not None
    assert cand['kind'] == 'preference'
    assert cand['recurrence_exact'] == 1


def test_topic_still_gated_at_3():
    # A topic (->skill) at recurrence 1 must NOT generate (content-starved path).
    assert _eval('topic', exact_count=1, coarse_count=1) is None
    assert _eval('topic', exact_count=2, coarse_count=2) is None


def test_topic_generates_at_3():
    cand = _eval('topic', exact_count=3, coarse_count=3)
    assert cand is not None
    assert cand['kind'] == 'skill'  # topic -> skill


def test_preference_threshold_is_configurable():
    # If an operator raises the preference threshold, recurrence-1 is gated.
    assert _eval('preference', exact_count=1, coarse_count=1, pref_min_rec=3,
                 extra_signal_fields={'summary': 'x'}) is None
