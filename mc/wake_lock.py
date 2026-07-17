"""Keep the machine awake while an agent is working.

Clayrune runs agents on the user's own machine. If that machine goes to sleep,
the agent stops — and over a tunnel the user just sees it stall. That caveat is
honest and unavoidable, but it is also *fixable* for the case that matters most:
while an agent is actually running, hold a wake lock so the box does not sleep
out from under it.

This turns the product's biggest caveat from an apology into a feature line:
**"Clayrune keeps your machine awake while an agent is working."** (LAUNCH_PLAN §2
calls this the single highest-value product ask; backlog `48fc83c5`.)

## Design — a reconciler, not lifecycle hooks

The wake lock is driven by a periodic reconcile against "how many sessions are
running", NOT by hooking every status transition. Sessions end many ways — clean
completion, error, a hard kill, a crash — and a reconciler catches all of them
for free. Worst case after a missed release is one extra poll interval of the
machine staying awake, which is the safe direction to fail.

## Posture

- **Off by default** (`keep_awake_enabled`). Opt-in per install.
- **Best-effort.** Every platform call is guarded; a failure logs once and never
  touches an agent. Same rule as Scribe.
- **Fails toward asleep.** On shutdown or any doubt, the lock is released, so we
  can never wedge a machine permanently awake.

## Platform mechanisms

- **Windows:** `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`.
  The state is per-THREAD (not process-wide) and persists until that same
  thread clears it or exits. That works here because the reconciler thread is
  the sole engage/release caller during runtime; on process exit Windows
  clears it automatically, so a crash can never wedge the machine awake.
- **macOS:** a `caffeinate -s -w <our pid>` subprocess — `-w` ties it to our
  process, so it exits on its own when we die (any death mode, SIGKILL
  included). Killed early to release.
- **Linux:** a `systemd-inhibit --what=sleep ... cat` subprocess holding OUR
  stdin pipe — when we die the pipe closes, `cat` sees EOF and exits, and the
  inhibitor releases. Same any-death-mode guarantee as macOS.

Both POSIX inhibitors are therefore parent-death-bound: `atexit` (registered
in `start()`) is the graceful path, the pipe/`-w` binding is the crash path.
"""
from __future__ import annotations

import atexit
import ctypes
import os
import subprocess
import sys
import threading
import time
from typing import Callable, Optional

from mc.core import _log

# ── Windows constants ────────────────────────────────────────────────────────
_ES_CONTINUOUS = 0x80000000
_ES_SYSTEM_REQUIRED = 0x00000001

_lock = threading.Lock()
_engaged = False
_proc: Optional[subprocess.Popen] = None   # mac/linux inhibitor process


def _engage_windows() -> bool:
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(
            _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED)
        return True
    except Exception as e:
        _log(f"[wake-lock] windows engage failed: {e}", flush=True)
        return False


def _release_windows() -> bool:
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
        return True
    except Exception as e:
        _log(f"[wake-lock] windows release failed: {e}", flush=True)
        return False


def _engage_subprocess(cmd: list[str], bind_stdin: bool = False) -> bool:
    """Spawn an inhibitor subprocess. With `bind_stdin`, the child holds OUR
    stdin pipe — parent death (any mode) closes the pipe and the child exits,
    so a hard-killed server can never orphan a sleep inhibitor."""
    global _proc
    try:
        _proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=(subprocess.PIPE if bind_stdin else subprocess.DEVNULL))
        return True
    except FileNotFoundError:
        _log(f"[wake-lock] {cmd[0]} not found — cannot inhibit sleep on this system",
             flush=True)
        return False
    except Exception as e:
        _log(f"[wake-lock] engage failed: {e}", flush=True)
        return False


def _release_subprocess() -> bool:
    global _proc
    if _proc is None:
        return True
    try:
        if _proc.stdin is not None:
            try:
                _proc.stdin.close()   # EOF — the pipe-bound child exits itself
            except Exception:
                pass
        _proc.terminate()
        try:
            _proc.wait(timeout=5)
        except Exception:
            _proc.kill()
    except Exception as e:
        _log(f"[wake-lock] release failed: {e}", flush=True)
        return False
    finally:
        _proc = None
    return True


def _inhibitor_dead() -> bool:
    """POSIX only: True if we believe we hold the lock but the inhibitor
    process has died out from under us (crashed, OOM-killed, user `kill`).
    Without this check the reconciler would keep believing the machine is
    protected while it is in fact free to sleep mid-run."""
    if sys.platform == "win32":
        return False
    return _proc is None or _proc.poll() is not None


def _engage() -> bool:
    if sys.platform == "win32":
        return _engage_windows()
    if sys.platform == "darwin":
        # -w: caffeinate exits by itself when OUR pid does (crash-safe).
        return _engage_subprocess(
            ["caffeinate", "-s", "-w", str(os.getpid())])
    # linux / other posix — `cat` on our pipe dies on our EOF (crash-safe).
    return _engage_subprocess([
        "systemd-inhibit", "--what=sleep", "--who=Clayrune",
        "--why=an agent is running", "--mode=block", "cat"],
        bind_stdin=True)


def _release() -> bool:
    if sys.platform == "win32":
        return _release_windows()
    return _release_subprocess()


def set_active(active: bool) -> None:
    """Engage the wake lock iff `active`; release it otherwise. Idempotent."""
    global _engaged
    with _lock:
        # Child-liveness: a held POSIX lock whose inhibitor died is a silent
        # lie — clear the flag so the branch below re-engages this tick.
        if active and _engaged and _inhibitor_dead():
            _log("[wake-lock] inhibitor process died — re-engaging", flush=True)
            _engaged = False
        if active and not _engaged:
            if _engage():
                _engaged = True
                _log("[wake-lock] engaged — machine will stay awake while an "
                     "agent is running", flush=True)
        elif not active and _engaged:
            if _release():
                _engaged = False
                _log("[wake-lock] released — no agents running", flush=True)


def release_now() -> None:
    """Unconditional release, for shutdown. Fails toward asleep.

    Registered via atexit in start(). On Windows this call is best-effort
    from whatever thread atexit runs on (the execution state is per-thread),
    but process exit clears the state regardless; on POSIX it reaps the
    inhibitor subprocess, whose pipe/-w binding also covers non-atexit
    deaths."""
    global _engaged
    with _lock:
        if _engaged:
            _release()
            _engaged = False


def start(count_running: Callable[[], int], is_enabled: Callable[[], bool],
          interval_s: int = 20) -> None:
    """Background reconciler.

    `count_running()` returns the number of sessions currently working;
    `is_enabled()` reads the live config flag (so the toggle takes effect without
    a restart). When the feature is switched off mid-run, any held lock is
    released on the next tick.
    """
    def _loop():
        while True:
            try:
                active = is_enabled() and count_running() > 0
                set_active(active)
            except Exception as e:
                _log(f"[wake-lock] reconcile error: {e}", flush=True)
            time.sleep(max(5, interval_s))

    # Graceful-shutdown release (night-review blocker 2026-07-15): without
    # this, a clean server exit on POSIX left the inhibitor subprocess
    # holding the machine awake forever. The pipe/-w parent binding covers
    # hard kills; atexit covers the polite path.
    atexit.register(release_now)
    threading.Thread(target=_loop, name="wake-lock", daemon=True).start()
    _log("[wake-lock] reconciler started", flush=True)
