"""Allowlist unattended detection (committee M4 / Seat 1 Cond 4).

The old rule enumerated known unattended shapes and defaulted everything
else to interactive — failing OPEN for transcript-backfilled sessions
(trigger_type unrecoverable) and rephrased unattended prompts. Inverted:
interactive ONLY when trigger_type == 'manual' AND no unattended text
marker; anything missing or unknown stamps unattended.
"""
import distiller


def test_manual_trigger_plain_task_is_interactive():
    assert distiller.is_unattended_session('fix the login bug', 'manual') is False


def test_steward_marker_always_unattended():
    assert distiller.is_unattended_session(
        '[Steward cycle] You are the autonomous STEWARD...', 'manual') is True


def test_night_shift_text_is_unattended_even_with_manual_trigger():
    assert distiller.is_unattended_session(
        'You are the night agent. You run unattended, late, while...',
        'manual') is True


def test_schedule_trigger_is_unattended():
    assert distiller.is_unattended_session('watchdog sweep', 'schedule') is True


def test_hivemind_triggers_are_unattended():
    assert distiller.is_unattended_session('x', 'hivemind_orchestrator') is True
    assert distiller.is_unattended_session('x', 'hivemind_worker') is True


def test_missing_trigger_fails_closed():
    """Backfilled sessions lose trigger_type (agent_routes backfill cannot
    recover it) — they must stamp unattended, not interactive."""
    assert distiller.is_unattended_session('fix the login bug', None) is True
    assert distiller.is_unattended_session('fix the login bug', '') is True


def test_unknown_future_trigger_fails_closed():
    assert distiller.is_unattended_session('x', 'remote_trigger') is True


def test_text_helper_widened_for_night_shift():
    assert distiller.is_unattended_task(
        'You are the maintenance agent. You run unattended, late.') is True
    assert distiller.is_unattended_task('ordinary user ask') is False
