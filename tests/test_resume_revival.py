"""Regression tests for resume-death amnesia (server.py).

Fix (2026-06-03): a Mode B session that was a `-r` resume used to reset to a
fresh, context-less session on its NEXT process death — so an AskUserQuestion
(which deliberately `proc.kill()`s the Mode B process), an idle-eviction, or any
crash of a resumed session lost the whole conversation. The agent then had to
retrace everything.

The fix distinguishes a genuinely *fragile* resume (died before producing any
output → fresh restart is correct, avoids an -r death loop) from a *healthy*
resumed session that produced output and only died later (→ must resume with -r).
A `_resume_confirmed` flag, set on first assistant output, is the discriminator.

Covered:
  - `_resume_is_fragile` truth table (the pure decision);
  - the Mode B reader sets `_resume_confirmed` on assistant output;
  - a resume that produced no output stays unconfirmed → fragile.
"""
from __future__ import annotations

import importlib
import json
import time

import pytest


@pytest.fixture
def srv(tmp_data_dir):
    import server
    importlib.reload(server)
    return server


# ── _resume_is_fragile: the pure decision ─────────────────────────────────────

def test_fresh_dispatch_never_fragile(srv):
    # Not a resume at all → never "fragile", regardless of output.
    assert srv._resume_is_fragile(False, False) is False
    assert srv._resume_is_fragile(False, True) is False


def test_unconfirmed_resume_is_fragile(srv):
    # Was a resume, produced no output → fragile → start fresh (avoid -r loop).
    assert srv._resume_is_fragile(True, False) is True
    assert srv._resume_is_fragile(True, None) is True   # default/absent flag


def test_confirmed_resume_not_fragile(srv):
    # Was a resume, produced output, died later → healthy → resume with -r.
    assert srv._resume_is_fragile(True, True) is False


# ── the reader flips _resume_confirmed on real output ─────────────────────────

class _FakeProc:
    """Minimal Popen stand-in: stdout yields preset lines, then ends."""
    def __init__(self, lines):
        self.stdout = iter(lines)
        self.pid = 4242

    def wait(self):
        return 0

    def poll(self):
        return 0

    def kill(self):
        pass


def _stub_reader_teardown(srv, monkeypatch):
    """No-op the heavy collaborators so the test isolates the flag behavior."""
    for name in ('_handle_push_signal', '_log_agent_completion',
                 '_unregister_process', '_capture_system_init'):
        monkeypatch.setattr(srv, name, lambda *a, **k: None, raising=False)


def _session(proc):
    return {
        'proc': proc, 'log_lines': [], 'provider': 'claude', 'mode': 'B',
        'session_id': 'test-sess', 'project_id': 'testproj', 'status': 'running',
        'last_status_change_time': 0.0, '_resume_id': 'old-sid',
        '_resume_confirmed': False, '_dispatch_time': time.time(), 'num_turns': 0,
    }


def test_assistant_output_confirms_resume(srv, monkeypatch):
    _stub_reader_teardown(srv, monkeypatch)
    line = json.dumps({
        "type": "assistant",
        "message": {"role": "assistant",
                    "content": [{"type": "text", "text": "on it"}]},
    }) + "\n"
    proc = _FakeProc([line])
    session = _session(proc)
    srv._read_agent_stream_b(proc, session)
    assert session['_resume_confirmed'] is True
    # ...so on a later death it would NOT be treated as fragile:
    assert srv._resume_is_fragile(True, session['_resume_confirmed']) is False


def test_no_output_leaves_resume_fragile(srv, monkeypatch):
    _stub_reader_teardown(srv, monkeypatch)
    # A resume that dies before any assistant output (only a system line).
    line = json.dumps({"type": "system", "subtype": "init",
                       "session_id": "x"}) + "\n"
    proc = _FakeProc([line])
    session = _session(proc)
    srv._read_agent_stream_b(proc, session)
    assert not session.get('_resume_confirmed')
    # ...so it IS still fragile → fresh restart (correct, avoids -r loop):
    assert srv._resume_is_fragile(True, session.get('_resume_confirmed')) is True
