"""Wake-lock reconciler logic.

The OS calls (SetThreadExecutionState / caffeinate / systemd-inhibit) are stubbed;
what matters here is the state machine: engage exactly when an agent is running
AND the feature is on, release otherwise, idempotently, and never wedge the
machine awake.
"""
from __future__ import annotations

import sys
import types

import pytest

# Import wake_lock with a stub _log so it doesn't need the full app.
_core = types.ModuleType("mc.core")
_core._log = lambda *a, **k: None
sys.modules.setdefault("mc.core", _core)

from mc import wake_lock  # noqa: E402


@pytest.fixture(autouse=True)
def _stub_os(monkeypatch):
    """Replace the real OS calls with counters, and reset module state."""
    calls = {"engage": 0, "release": 0}
    monkeypatch.setattr(wake_lock, "_engage", lambda: calls.__setitem__("engage", calls["engage"] + 1) or True)
    monkeypatch.setattr(wake_lock, "_release", lambda: calls.__setitem__("release", calls["release"] + 1) or True)
    wake_lock._engaged = False
    wake_lock._proc = None
    yield calls
    wake_lock._engaged = False


def test_engages_when_an_agent_starts(_stub_os):
    wake_lock.set_active(True)
    assert wake_lock._engaged is True
    assert _stub_os["engage"] == 1


def test_releases_when_the_last_agent_stops(_stub_os):
    wake_lock.set_active(True)
    wake_lock.set_active(False)
    assert wake_lock._engaged is False
    assert _stub_os["release"] == 1


def test_is_idempotent_while_agents_keep_running(_stub_os):
    """The reconciler ticks every ~20s. A running agent must not re-engage the
    lock on every tick — engage once, hold, release once."""
    for _ in range(5):
        wake_lock.set_active(True)
    assert _stub_os["engage"] == 1

    for _ in range(5):
        wake_lock.set_active(False)
    assert _stub_os["release"] == 1


def test_a_failed_engage_does_not_mark_it_engaged(monkeypatch, _stub_os):
    """If the OS refuses the lock, we must retry next tick — not believe we hold
    a lock we don't."""
    monkeypatch.setattr(wake_lock, "_engage", lambda: False)
    wake_lock.set_active(True)
    assert wake_lock._engaged is False


def test_release_now_is_unconditional(_stub_os):
    wake_lock.set_active(True)
    wake_lock.release_now()
    assert wake_lock._engaged is False
    assert _stub_os["release"] == 1


def test_release_now_is_safe_when_nothing_is_held(_stub_os):
    wake_lock.release_now()          # must not call _release or raise
    assert _stub_os["release"] == 0


def test_reconciler_gates_on_both_enabled_and_running(monkeypatch, _stub_os):
    """The loop's `active` is `is_enabled() AND count_running() > 0`. Prove all
    four corners resolve correctly through set_active."""
    def reconcile(enabled, running):
        wake_lock.set_active(enabled and running > 0)
        return wake_lock._engaged

    assert reconcile(True, 2) is True     # on + working  -> awake
    assert reconcile(True, 0) is False    # on + idle     -> asleep
    assert reconcile(False, 2) is False   # off + working -> asleep (respects the switch)
    assert reconcile(False, 0) is False


def test_disabling_mid_run_releases_the_lock(monkeypatch, _stub_os):
    """Flip the setting off while an agent is running: the next tick must release,
    since the reconciler reads the flag live (no restart)."""
    wake_lock.set_active(True and 1 > 0)          # enabled, running
    assert wake_lock._engaged is True
    wake_lock.set_active(False and 1 > 0)         # user flips it off
    assert wake_lock._engaged is False
    assert _stub_os["release"] == 1


# ── Night-review blockers (2026-07-15/16) — the three verified bugs ──────────

def test_start_registers_atexit_release(monkeypatch, _stub_os):
    """POSIX orphan bug: a clean server exit never released the inhibitor
    subprocess — caffeinate/systemd-inhibit kept the box awake forever."""
    registered = []
    monkeypatch.setattr(wake_lock.atexit, "register",
                        lambda fn: registered.append(fn))
    wake_lock.start(count_running=lambda: 0, is_enabled=lambda: False,
                    interval_s=9999)
    assert wake_lock.release_now in registered


def test_dead_inhibitor_triggers_reengage(monkeypatch, _stub_os):
    """Child-liveness: if the inhibitor process dies out from under us, the
    reconciler must notice and re-engage instead of trusting a stale flag."""
    calls = _stub_os
    wake_lock.set_active(True)
    assert calls["engage"] == 1
    # Simulate the inhibitor dying while we believe the lock is held.
    monkeypatch.setattr(wake_lock, "_inhibitor_dead", lambda: True)
    wake_lock.set_active(True)
    assert calls["engage"] == 2, "must re-engage after inhibitor death"
    # Once alive again, no churn.
    monkeypatch.setattr(wake_lock, "_inhibitor_dead", lambda: False)
    wake_lock.set_active(True)
    assert calls["engage"] == 2


# Captured at import time — the autouse _stub_os fixture replaces
# wake_lock._engage in every test, so the parent-death test calls the real
# implementation through this reference (its globals still see monkeypatches).
_REAL_ENGAGE = wake_lock._engage


def test_posix_inhibitors_are_parent_death_bound(monkeypatch):
    """Crash path: the inhibitor must die WITH us even on SIGKILL — macOS via
    `caffeinate -w <pid>`, linux via holding our stdin pipe (`cat` + EOF)."""
    spawned = {}

    class _FakeProc:
        stdin = None
        def poll(self): return None

    def fake_popen(cmd, **kw):
        spawned["cmd"] = cmd
        spawned["stdin"] = kw.get("stdin")
        return _FakeProc()

    monkeypatch.setattr(wake_lock.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(wake_lock.sys, "platform", "darwin")
    assert _REAL_ENGAGE() is True
    assert "-w" in spawned["cmd"]
    assert str(wake_lock.os.getpid()) in spawned["cmd"]

    monkeypatch.setattr(wake_lock.sys, "platform", "linux")
    assert _REAL_ENGAGE() is True
    assert spawned["cmd"][-1] == "cat"
    assert spawned["stdin"] == wake_lock.subprocess.PIPE
    wake_lock._proc = None  # don't leak the fake into other tests
