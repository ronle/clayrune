"""Kill-switch enumeration regression — Seat 3 v1.1 Cond 1 + v2.1 §4.6.

Parent design Cond 10 v2: "EVERY Distiller entry point ... MUST route
through one function `_distiller_should_proceed(project_id, entry_point)
-> bool`. No entry point may inline its own check."

v2.1 §4.6 enumerates 6 entry points; this test asserts the enumeration
matches the ENTRY_POINTS constant in distiller.py. A future contributor
who adds a 7th entry point without registering it fails this test loudly.

Without this test, kill-switch coverage rot is silent.
"""
from __future__ import annotations

import distiller


def test_entry_points_constant_exists():
    assert hasattr(distiller, 'ENTRY_POINTS')
    assert isinstance(distiller.ENTRY_POINTS, frozenset)


def test_entry_points_enumeration_complete():
    """v2.1 §4.6 enumerates 6 entry points by name."""
    expected = {
        'session_end_extract',
        'proposal_generate',
        'cross_project_aggregate',
        'record_push',
        'auto_promote',
        'dispatch_hint',
    }
    assert distiller.ENTRY_POINTS == expected, (
        f"v2.1 §4.6 entry-point enumeration drift: "
        f"expected={expected}, actual={set(distiller.ENTRY_POINTS)}"
    )


def test_unknown_entry_point_gates_off_loudly():
    """A future contributor passing an unregistered entry_point should
    have their call gated OFF (returns False), not silently allowed."""
    # No register() call → all calls return False. The unknown-entry-point
    # path returns False even with full registration (the kill-switch
    # short-circuits before checking config).
    result = distiller._distiller_should_proceed(
        'fake_project', 'totally_unknown_entry_point_xyz'
    )
    assert result is False, (
        "unknown entry_point must be gated OFF — preventing silent rot "
        "where a new code path adds a Distiller-touching call without "
        "registration"
    )


def test_master_kill_switch_disables_all_entry_points(monkeypatch):
    """When distiller_enabled_global=False, every enumerated entry point
    must return False from _distiller_should_proceed."""
    # Stub config_get + load_project so the gate can run
    distiller._config_get = lambda k, d=None: (
        False if k == 'distiller_enabled_global' else d
    )
    distiller._load_project = lambda pid: {'id': pid, 'distiller_mode': 'proposed'}
    try:
        for entry_point in distiller.ENTRY_POINTS:
            result = distiller._distiller_should_proceed(
                'fake_project', entry_point
            )
            assert result is False, (
                f"entry_point {entry_point!r} did not gate off when "
                f"master kill switch is False"
            )
    finally:
        distiller._config_get = None
        distiller._load_project = None


def test_per_project_off_mode_disables_all_entry_points():
    """When project's distiller_mode='off', every entry point returns False."""
    distiller._config_get = lambda k, d=None: True
    distiller._load_project = lambda pid: {'id': pid, 'distiller_mode': 'off'}
    try:
        for entry_point in distiller.ENTRY_POINTS:
            result = distiller._distiller_should_proceed(
                'fake_project', entry_point
            )
            assert result is False, (
                f"entry_point {entry_point!r} did not gate off when "
                f"project mode is 'off'"
            )
    finally:
        distiller._config_get = None
        distiller._load_project = None


def test_cross_project_kill_is_independent():
    """v2.1 §4.6 + Seat 4 v1.1 Cond 5: distiller_cross_project_enabled
    gates ONLY the cross_project_aggregate entry point. Other entry
    points continue when only the cross-project kill is flipped."""
    def cfg(k, d=None):
        if k == 'distiller_enabled_global':
            return True
        if k == 'distiller_cross_project_enabled':
            return False
        return d
    distiller._config_get = cfg
    distiller._load_project = lambda pid: {'id': pid, 'distiller_mode': 'proposed'}
    try:
        # cross_project_aggregate gated off
        assert distiller._distiller_should_proceed(
            'p', 'cross_project_aggregate') is False
        # other entry points still allowed
        assert distiller._distiller_should_proceed(
            'p', 'session_end_extract') is True
        assert distiller._distiller_should_proceed(
            'p', 'proposal_generate') is True
        assert distiller._distiller_should_proceed(
            'p', 'record_push') is True
    finally:
        distiller._config_get = None
        distiller._load_project = None


def test_incognito_session_disables_session_end_extract():
    """Inherits Scribe's incognito/housekeeping gate verbatim."""
    distiller._config_get = lambda k, d=None: True
    distiller._load_project = lambda pid: {'id': pid, 'distiller_mode': 'proposed'}
    try:
        assert distiller._distiller_should_proceed(
            'p', 'session_end_extract',
            session={'incognito': True}
        ) is False
        assert distiller._distiller_should_proceed(
            'p', 'session_end_extract',
            session={'housekeeping': True}
        ) is False
    finally:
        distiller._config_get = None
        distiller._load_project = None
