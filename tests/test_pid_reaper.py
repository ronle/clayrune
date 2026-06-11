"""Regression tests for the MC-spawned child PID ledger + startup orphan reaper.

Covers the leak fix (2026-06-03). server.py restarts by re-exec'ing via
``os._exit()``; any agent child (claude.exe) not killed inside the bounded
graceful-stop window — plus its MCP-server tree (node/cmd/engram) — is orphaned,
and the new instance never knew its PIDs (``tracked_processes`` is in-memory).
The reaper persists live child PIDs to a ledger and, at the next startup, kills
strays from the prior instance. The kill is guarded by an image-name +
creation-time match so a reused PID can never be friendly-fired.

Deterministic + CI-safe: liveness, identity, and the kill are all stubbed, so no
real processes are spawned or signalled and there are no hardcoded PIDs.

PORTED with the mop-up (1.14 sibling): the ledger + reaper were extracted VERBATIM
from server.py to ``mc/process_ledger.py`` (a non-blueprint module, the
mc/memory.py pattern). Per the Phase-0 test-port rule, these tests now patch
``mc.process_ledger.*`` — NOT ``server.*`` — because the reaper resolves
``_pid_is_alive`` / ``_kill_pid`` / ``_proc_identity`` / ``_PID_LEDGER_PATH`` from
that module's own globals (wired at server startup), so a ``server.<name>``
rebind would no longer reach it. ``tracked_processes`` / ``process_tracker_lock``
live in mc.state and are imported (mutated in-place), so they're still reachable
off ``server.*``.
"""
from __future__ import annotations

import importlib
import json
import os

import pytest


@pytest.fixture
def srv(tmp_data_dir):
    """Import server with an isolated MC_DATA_DIR (so import-time dir creation
    and the default ledger path land in a throwaway dir, not the real ./data).

    Returns (server, process_ledger). server is kept for the state accessors
    (tracked_processes / process_tracker_lock, imported from mc.state); the
    ledger + reaper now live on process_ledger and are patched there."""
    import server
    importlib.reload(server)
    import mc.process_ledger as pl
    return server, pl


# ── _should_reap_entry: the friendly-fire guard ─────────────────────────────

def test_reap_predicate_exact_match(srv):
    _server, pl = srv
    e = {'os_image': 'claude.exe', 'create_time': 1000.0}
    assert pl._should_reap_entry(e, 'claude.exe', 1000.5) is True


def test_reap_predicate_name_mismatch_skips(srv):
    _server, pl = srv
    e = {'os_image': 'claude.exe', 'create_time': 1000.0}
    assert pl._should_reap_entry(e, 'explorer.exe', 1000.0) is False


def test_reap_predicate_createtime_drift_skips(srv):
    _server, pl = srv
    e = {'os_image': 'claude.exe', 'create_time': 1000.0}
    assert pl._should_reap_entry(e, 'claude.exe', 1200.0) is False


def test_reap_predicate_missing_identity_skips(srv):
    _server, pl = srv
    assert pl._should_reap_entry({'os_image': None}, 'claude.exe', 1.0) is False
    assert pl._should_reap_entry({'os_image': 'x.exe'}, None, 1.0) is False


def test_reap_predicate_name_only_when_no_createtime(srv):
    _server, pl = srv
    # No creation time on either side → fall back to image-name match alone.
    assert pl._should_reap_entry({'os_image': 'x.exe'}, 'x.exe', None) is True


# ── ledger round-trip ───────────────────────────────────────────────────────

def test_persist_pid_ledger_roundtrip(srv, tmp_path, monkeypatch):
    server, pl = srv
    led = tmp_path / 'ledger.json'
    monkeypatch.setattr(pl, '_PID_LEDGER_PATH', led)
    # tracked_processes / process_tracker_lock live in mc.state — reachable
    # off server.* (imported, mutated in-place) AND off the ledger module.
    with server.process_tracker_lock:
        server.tracked_processes.clear()
        server.tracked_processes[4321] = {
            'pid': 4321, 'name': 'Agent (Mode B)', 'type': 'agent',
            'os_image': 'claude.exe', 'create_time': 111.0, 'proc': object(),
        }
    pl._persist_pid_ledger()
    entry = json.loads(led.read_text())['children'][0]
    assert entry['pid'] == 4321
    assert entry['os_image'] == 'claude.exe'
    assert 'proc' not in entry            # the live Popen must never be serialized


# ── reaper: kills confirmed live strays, spares everything else ──────────────

def test_reaper_kills_only_matching_live_strays(srv, tmp_path, monkeypatch):
    _server, pl = srv
    led = tmp_path / 'ledger.json'
    monkeypatch.setattr(pl, '_PID_LEDGER_PATH', led)

    alive = {100, 200, 300}               # 400 is dead
    identities = {
        100: ('claude.exe', 1000.0),      # matches ledger          → reap
        200: ('explorer.exe', 1000.0),    # PID reused by other app  → spare
        300: ('claude.exe', 9000.0),      # same name, newer process → spare
    }
    killed = []
    monkeypatch.setattr(pl, '_pid_is_alive', lambda p: p in alive)
    monkeypatch.setattr(pl, '_proc_identity', lambda p: identities.get(p, (None, None)))
    monkeypatch.setattr(pl, '_kill_pid', lambda p, tree=False: (killed.append((p, tree)), True)[1])

    led.write_text(json.dumps({'mc_pid': 999999, 'children': [
        {'pid': 100, 'os_image': 'claude.exe', 'create_time': 1000.0},
        {'pid': 200, 'os_image': 'claude.exe', 'create_time': 1000.0},
        {'pid': 300, 'os_image': 'claude.exe', 'create_time': 1000.0},
        {'pid': 400, 'os_image': 'claude.exe', 'create_time': 1000.0},
    ]}))

    pl._reap_prior_instance_strays()

    assert killed == [(100, True)]                          # only the stray, tree-kill
    assert json.loads(led.read_text())['children'] == []    # ledger cleared


def test_reaper_spares_self_and_prior_mc(srv, tmp_path, monkeypatch):
    _server, pl = srv
    led = tmp_path / 'ledger.json'
    monkeypatch.setattr(pl, '_PID_LEDGER_PATH', led)
    me = os.getpid()
    killed = []
    monkeypatch.setattr(pl, '_pid_is_alive', lambda p: True)
    monkeypatch.setattr(pl, '_proc_identity', lambda p: ('claude.exe', 1000.0))
    monkeypatch.setattr(pl, '_kill_pid', lambda p, tree=False: (killed.append(p), True)[1])
    led.write_text(json.dumps({'mc_pid': 555, 'children': [
        {'pid': me,  'os_image': 'claude.exe', 'create_time': 1000.0},   # ourselves
        {'pid': 555, 'os_image': 'claude.exe', 'create_time': 1000.0},   # the prior MC pid
    ]}))
    pl._reap_prior_instance_strays()
    assert killed == []                    # never kill our own pid or the recorded MC pid


def test_reaper_no_ledger_is_noop(srv, tmp_path, monkeypatch):
    _server, pl = srv
    monkeypatch.setattr(pl, '_PID_LEDGER_PATH', tmp_path / 'nope.json')
    pl._reap_prior_instance_strays()      # must not raise
