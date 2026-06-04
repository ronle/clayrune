"""Regression tests for guardian idle-session eviction (server.py).

Covers the resource-efficiency change (2026-06-03): a warm Mode B session
(claude.exe + its MCP-server fleet) is torn down after `idle_eviction_minutes`
of inactivity and transparently respawned (`claude -r <csid>`) on the next
message. The decision lives in the pure predicate `_should_evict_idle_session`,
tested here without spawning real processes.

Key invariants under test:
  - only `idle` Mode B sessions with a LIVE process qualify (never `running`);
  - the feature is gated off by default (`enabled=False` / `minutes<=0`);
  - an already-`evicted` session is not re-evicted;
  - the threshold is strict (exactly N minutes does NOT evict).
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def srv(tmp_data_dir):
    import server
    importlib.reload(server)
    return server


class _FakeProc:
    """Minimal Popen stand-in: poll() is None while 'alive', else an exit code."""
    def __init__(self, alive=True):
        self._alive = alive

    def poll(self):
        return None if self._alive else 0


NOW = 100_000.0


def _sess(**over):
    s = {'status': 'idle', 'mode': 'B', 'evicted': False,
         'proc': _FakeProc(True), 'last_output_time': 0.0}
    s.update(over)
    return s


def test_evicts_idle_mode_b_past_threshold(srv):
    s = _sess(last_output_time=NOW - 31 * 60)
    assert srv._should_evict_idle_session(s, NOW, True, 30) is True


def test_disabled_never_evicts(srv):
    s = _sess(last_output_time=NOW - 99 * 60)
    assert srv._should_evict_idle_session(s, NOW, False, 30) is False


def test_zero_or_negative_minutes_never_evicts(srv):
    s = _sess(last_output_time=NOW - 99 * 60)
    assert srv._should_evict_idle_session(s, NOW, True, 0) is False
    assert srv._should_evict_idle_session(s, NOW, True, -5) is False


def test_running_session_never_evicted(srv):
    s = _sess(status='running', last_output_time=NOW - 99 * 60)
    assert srv._should_evict_idle_session(s, NOW, True, 30) is False


def test_mode_a_never_evicted(srv):
    s = _sess(mode='A', last_output_time=NOW - 99 * 60)
    assert srv._should_evict_idle_session(s, NOW, True, 30) is False


def test_already_evicted_not_re_evicted(srv):
    s = _sess(evicted=True, last_output_time=NOW - 99 * 60)
    assert srv._should_evict_idle_session(s, NOW, True, 30) is False


def test_no_process_not_evicted(srv):
    s = _sess(proc=None, last_output_time=NOW - 99 * 60)
    assert srv._should_evict_idle_session(s, NOW, True, 30) is False


def test_dead_process_not_evicted(srv):
    # A dead proc is State 1's job, not eviction's.
    s = _sess(proc=_FakeProc(False), last_output_time=NOW - 99 * 60)
    assert srv._should_evict_idle_session(s, NOW, True, 30) is False


def test_idle_below_threshold_not_evicted(srv):
    s = _sess(last_output_time=NOW - 29 * 60)
    assert srv._should_evict_idle_session(s, NOW, True, 30) is False


def test_threshold_is_strict(srv):
    # Exactly 30 min idle must NOT evict (strictly greater-than).
    s = _sess(last_output_time=NOW - 30 * 60)
    assert srv._should_evict_idle_session(s, NOW, True, 30) is False


def test_pending_work_blocks_eviction(srv):
    # Queued followups / in-flight dispatch carry state the next turn needs.
    for over in ({'pending_followups': ['msg']}, {'_dispatching_followup': True}):
        s = _sess(last_output_time=NOW - 99 * 60, **over)
        assert srv._should_evict_idle_session(s, NOW, True, 30) is False


def test_waiting_on_user_blocks_eviction(srv):
    for over in ({'waiting_for_question': True}, {'waiting_for_plan_approval': True}):
        s = _sess(last_output_time=NOW - 99 * 60, **over)
        assert srv._should_evict_idle_session(s, NOW, True, 30) is False
