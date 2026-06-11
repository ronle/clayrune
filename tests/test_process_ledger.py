"""Unit tests for the MC-spawned child PID ledger module
(mc/process_ledger.py).

Added with the mop-up (1.14 sibling): the ledger writers + the startup orphan
reaper were extracted VERBATIM from server.py into this non-blueprint module
(the mc/memory.py pattern). These tests pin the module in isolation —
NO `import server` needed for the pure pieces — and assert the load-bearing
no-import-cycle invariant (the module must import leaf modules only, never
server or any blueprint).

The end-to-end reaper behavior (kill-only-matching-strays, spares-self/prior-mc)
is covered by tests/test_pid_reaper.py, which drives it through a reloaded
server (so wire() has bound the real _pid_is_alive/_kill_pid before patching).
Here we cover what's self-contained: import smoke, the cycle guard, the
_persist_pid_ledger round-trip (ledger path wired to a tmp file), and the
_should_reap_entry truth table (a pure predicate — no wiring required).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ── import smoke ──────────────────────────────────────────────────────────────

def test_import_smoke():
    import mc.process_ledger as pl
    for name in ('_proc_identity', '_persist_pid_ledger', '_should_reap_entry',
                 '_reap_prior_instance_strays', 'wire'):
        assert hasattr(pl, name), f'missing {name}'


# ── no-import-cycle invariant (LOAD-BEARING) ─────────────────────────────────

def test_no_import_cycle():
    """The module must import leaf modules only — never server or a blueprint.

    A static scan of the source AST: assert no `import server` /
    `from server import ...` and no `mc.blueprints` import anywhere. (Importing
    the module already proves it loads without pulling server, but the static
    assert documents the contract and catches a future accidental edit.)
    """
    import ast
    import mc.process_ledger as pl

    src = Path(pl.__file__).read_text(encoding='utf-8')
    tree = ast.parse(src)
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == 'server' or a.name.startswith('mc.blueprints'):
                    bad.append(a.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ''
            if mod == 'server' or mod.startswith('mc.blueprints'):
                bad.append(mod)
    assert not bad, f'process_ledger imports forbidden module(s): {bad}'


def test_module_not_in_server_dependency_when_imported_alone():
    """Importing process_ledger alone must NOT drag in server.py."""
    # Fresh-ish check: if server is already imported by another test, we can't
    # un-import it cleanly, so only assert when it isn't present yet.
    if 'server' in sys.modules:
        pytest.skip('server already imported by another test in this session')
    import mc.process_ledger  # noqa: F401
    assert 'server' not in sys.modules


# ── _persist_pid_ledger round-trip ───────────────────────────────────────────

def test_persist_pid_ledger_roundtrip(tmp_path, monkeypatch):
    """Write a ledger under a tmp path, read it back. The live Popen object in a
    tracked entry must never be serialized; mc_pid + children land on disk."""
    import mc.process_ledger as pl
    from mc import state

    led = tmp_path / 'ledger.json'
    monkeypatch.setattr(pl, '_PID_LEDGER_PATH', led)

    # tracked_processes / process_tracker_lock live in mc.state; the module
    # imports them by name (mutated in-place).
    with state.process_tracker_lock:
        state.tracked_processes.clear()
        state.tracked_processes[7777] = {
            'pid': 7777, 'name': 'Agent (Mode B)', 'type': 'agent',
            'os_image': 'claude.exe', 'create_time': 222.0, 'proc': object(),
        }
    try:
        pl._persist_pid_ledger()
        data = json.loads(led.read_text(encoding='utf-8'))
    finally:
        with state.process_tracker_lock:
            state.tracked_processes.clear()

    assert 'mc_pid' in data and 'written_at' in data
    assert len(data['children']) == 1
    entry = data['children'][0]
    assert entry['pid'] == 7777
    assert entry['os_image'] == 'claude.exe'
    assert entry['create_time'] == 222.0
    assert 'proc' not in entry            # the live Popen must never serialize


def test_persist_pid_ledger_empty_tracker(tmp_path, monkeypatch):
    import mc.process_ledger as pl
    from mc import state

    led = tmp_path / 'ledger.json'
    monkeypatch.setattr(pl, '_PID_LEDGER_PATH', led)
    with state.process_tracker_lock:
        state.tracked_processes.clear()
    pl._persist_pid_ledger()
    data = json.loads(led.read_text(encoding='utf-8'))
    assert data['children'] == []


def test_persist_pid_ledger_never_raises_on_bad_path(monkeypatch):
    """Best-effort posture: an unwritable ledger path must not raise."""
    import mc.process_ledger as pl
    # A path whose parent dir does not exist → _atomic_write_text fails inside;
    # _persist_pid_ledger swallows it (best-effort).
    monkeypatch.setattr(pl, '_PID_LEDGER_PATH',
                        Path('/this/dir/does/not/exist/ledger.json'))
    pl._persist_pid_ledger()   # must not raise


# ── _should_reap_entry truth table ───────────────────────────────────────────

@pytest.mark.parametrize('entry,live_image,live_ct,expected', [
    # exact image + creation-time within 2s → reap
    ({'os_image': 'claude.exe', 'create_time': 1000.0}, 'claude.exe', 1000.5, True),
    ({'os_image': 'claude.exe', 'create_time': 1000.0}, 'claude.exe', 1001.9, True),
    # boundary: drift exactly > 2s → spare
    ({'os_image': 'claude.exe', 'create_time': 1000.0}, 'claude.exe', 1002.1, False),
    # image-name mismatch (PID reused by another app) → spare
    ({'os_image': 'claude.exe', 'create_time': 1000.0}, 'explorer.exe', 1000.0, False),
    # same name, newer creation time → spare (PID reuse, fresh process)
    ({'os_image': 'claude.exe', 'create_time': 1000.0}, 'claude.exe', 9000.0, False),
    # case-insensitive image match → reap
    ({'os_image': 'Claude.EXE', 'create_time': 1000.0}, 'claude.exe', 1000.0, True),
    # missing recorded image → can't confirm → spare
    ({'os_image': None, 'create_time': 1000.0}, 'claude.exe', 1000.0, False),
    ({'create_time': 1000.0}, 'claude.exe', 1000.0, False),
    # missing live image → can't confirm → spare
    ({'os_image': 'claude.exe', 'create_time': 1000.0}, None, 1000.0, False),
    ({'os_image': 'claude.exe', 'create_time': 1000.0}, '', 1000.0, False),
    # no creation time on either side → image-name match alone is sufficient
    ({'os_image': 'node.exe'}, 'node.exe', None, True),
    # recorded ct present, live ct missing → name match alone (ct guard skipped)
    ({'os_image': 'node.exe', 'create_time': 1000.0}, 'node.exe', None, True),
])
def test_should_reap_entry_truth_table(entry, live_image, live_ct, expected):
    import mc.process_ledger as pl
    assert pl._should_reap_entry(entry, live_image, live_ct) is expected
