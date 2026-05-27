"""Regression: agent dispatch must not crash on Windows when the system
prompt exceeds cmd.exe's 8191-char limit.

Symptom Amit's user reported: after a transcript-too-large auto-fresh
fallback, the new dispatch died with "The command line is too long" +
rc=1. Root cause: every dispatch site passed context inline as
`--append-system-prompt <context>`, and a multi-KB context
(CLAYRUNE_API_REFERENCE + rules + read-floor + recent activity) easily
clears 8191 chars. The 2026-05-08 Playdo fix only covered /api/guide/*.

Fix: `_sysprompt_file_args(context)` writes context to a temp file and
returns `['--append-system-prompt-file', path]`. The command line stays
tiny regardless of context size. Cross-platform safe.
"""
from __future__ import annotations

import importlib
import os


def _server(tmp_data_dir):
    server = importlib.import_module("server")
    importlib.reload(server)
    return server


def test_sysprompt_file_args_writes_context_and_returns_path(tmp_data_dir):
    server = _server(tmp_data_dir)
    # A 20 KB context — well past cmd.exe's 8191-char cap.
    big_context = "ABCDEFGH" * 2500

    args, path = server._sysprompt_file_args(big_context)
    try:
        assert args[0] == '--append-system-prompt-file'
        assert args[1] == path
        assert os.path.exists(path)
        with open(path, encoding='utf-8') as f:
            assert f.read() == big_context
    finally:
        if path and os.path.exists(path):
            os.unlink(path)


def test_sysprompt_file_args_empty_is_noop(tmp_data_dir):
    server = _server(tmp_data_dir)
    args, path = server._sysprompt_file_args('')
    assert args == []
    assert path is None
    args, path = server._sysprompt_file_args(None)
    assert args == []
    assert path is None


def test_dispatch_command_line_stays_short_for_huge_context(tmp_data_dir):
    """The dispatched argv must never put the full context inline. We
    spot-check by simulating the dispatch-site idiom directly: even a
    100 KB context produces a stable, tiny argv.
    """
    server = _server(tmp_data_dir)
    huge = "X" * 100_000  # 100 KB — far past every Windows cmd-line cap

    args, path = server._sysprompt_file_args(huge)
    try:
        cmd = ['claude.cmd', '-p', 'hello', '--print', '--verbose',
               '--output-format', 'stream-json', *args]
        # The whole argv stringified must fit comfortably under 8191.
        argv_len = sum(len(a) for a in cmd) + len(cmd)  # +len(cmd) for spaces
        assert argv_len < 1024, (
            f"argv length {argv_len} suggests context leaked inline; "
            f"args={args}"
        )
        # ...but the file on disk still holds the full payload.
        with open(path, encoding='utf-8') as f:
            assert len(f.read()) == 100_000
    finally:
        if path and os.path.exists(path):
            os.unlink(path)


def test_sysprompt_cleanup_unlinks_after_proc_exit(tmp_data_dir):
    """The watchdog thread must delete the temp file once the proc exits."""
    server = _server(tmp_data_dir)
    args, path = server._sysprompt_file_args("hello")
    assert os.path.exists(path)

    class _ExitedProc:
        def wait(self):
            return 0

    server._sysprompt_cleanup(path, _ExitedProc())
    # Cleanup runs in a daemon thread — give it a beat.
    import time
    for _ in range(50):
        if not os.path.exists(path):
            break
        time.sleep(0.02)
    assert not os.path.exists(path), (
        f"sysprompt temp file was not cleaned up: {path}"
    )
