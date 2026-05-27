"""Regression: malformed stream-json must not crash `_read_agent_stream*`.

A user on Windows hit `[stream error: 'str' object has no attribute 'get']`
mid-session. Root cause: the inline readers in server.py lacked the
isinstance guards that agent_runtime.py's parse_event already has, so a
non-dict JSON envelope (or a string-valued `message`/`content`/`input`
field) crashed at the first `.get()` call and killed the reader thread.

Each case below is a single stdout line that previously tripped the crash.
The fix routes non-dict envelopes through the existing JSONDecodeError
branch and skips malformed sub-structures defensively.
"""
from __future__ import annotations

import importlib
import io


class _FakeProc:
    """Minimum surface the readers touch: stdout iter, pid, wait()."""

    def __init__(self, lines):
        self.stdout = io.StringIO('\n'.join(lines) + '\n')
        self.pid = -1  # _unregister_process is a no-op for unknown pids
        self._rc = 0

    def wait(self):
        return self._rc

    def kill(self):
        pass


def _new_session(project_id: str) -> dict:
    return {
        'project_id': project_id,
        'status': 'running',
        'log_lines': [],
        'last_output_time': 0.0,
        'last_status_change_time': 0.0,
        'provider': 'claude',
    }


def _crashed(session: dict) -> bool:
    return any(
        isinstance(ln, str) and "[stream error:" in ln
        for ln in session['log_lines']
    )


MALFORMED_LINES = [
    # 1. bare JSON string — json.loads returns a str
    '"some quoted error blob"',
    # 2. bare JSON number / null / bool / list
    '123',
    'null',
    'true',
    '[1, 2, 3]',
    # 3. assistant with string-valued `message` field
    '{"type": "assistant", "message": "oops not a dict"}',
    # 4. assistant.message.content is a string instead of a list of blocks
    '{"type": "assistant", "message": {"content": "hello"}}',
    # 5. content list with non-dict entries (just strings)
    '{"type": "assistant", "message": {"content": ["plain", "strings"]}}',
    # 6. tool_use with non-dict input
    '{"type": "assistant", "message": {"content": ['
    '{"type": "tool_use", "name": "Bash", "input": "not a dict"}]}}',
]

WELL_FORMED_LINES = [
    '{"type": "assistant", "message": {"content": ['
    '{"type": "text", "text": "hello world"}]}}',
    '{"type": "result", "session_id": "abc", "num_turns": 1}',
]


def test_mode_a_reader_survives_malformed_envelopes(tmp_data_dir):
    server = importlib.import_module("server")
    importlib.reload(server)

    session = _new_session('p-mode-a')
    proc = _FakeProc(MALFORMED_LINES + WELL_FORMED_LINES)
    session['proc'] = proc

    server._read_agent_stream(proc, session)

    assert not _crashed(session), (
        "reader emitted [stream error: ...]; log_lines: "
        + repr(session['log_lines'])
    )
    # Well-formed text block was still captured.
    assert any('hello world' in ln for ln in session['log_lines'])


def test_mode_b_reader_survives_malformed_envelopes(tmp_data_dir):
    server = importlib.import_module("server")
    importlib.reload(server)

    session = _new_session('p-mode-b')
    proc = _FakeProc(MALFORMED_LINES + WELL_FORMED_LINES)
    session['proc'] = proc

    server._read_agent_stream_b(proc, session)

    assert not _crashed(session), (
        "reader emitted [stream error: ...]; log_lines: "
        + repr(session['log_lines'])
    )
    assert any('hello world' in ln for ln in session['log_lines'])
