"""MC-spawned child PID ledger + startup orphan reaper — mop-up extraction
(non-blueprint, the mc/memory.py sibling pattern).

Moved VERBATIM out of server.py to drive it toward its <2,000-line target. ZERO
behavior change — a PURE MOVE. No mechanical rewrites were needed: every name
the moved bodies reference resolves identically (`process_tracker_lock` /
`tracked_processes` imported by name from mc.state; `_atomic_write_text` / `_log`
/ `now_iso` from mc.core; `os`/`sys`/`json` stdlib). The two dispatch-family
helpers the reaper calls (`_pid_is_alive`, `_kill_pid`) live in agent_routes
(1.12) and arrive via wire() — the 1.13 scheduler_routes cross-family call-seam
pattern.

WHY a module, not a blueprint: there are no routes here. The reaper is a
startup-only function (`server.py`'s `__main__` calls
`process_ledger._reap_prior_instance_strays()`); the ledger writers are called
from the register/unregister process path (already wired into agent_routes 1.12
as `proc_identity_fn` / `persist_pid_ledger_fn`).

server.py restarts by re-exec'ing via os._exit(): any child not killed inside
the bounded graceful-stop window is orphaned, and the new instance never knew
its PIDs (tracked_processes is in-memory only). Net effect: claude.exe + their
MCP-server trees (node/cmd/engram) leak across every restart/crash. We persist
the live child PIDs to a ledger and, at the next startup, reap any that are
STILL alive AND still the same process (image-name + creation-time guard
defeats PID reuse, so we can never friendly-fire an unrelated process).
Everything here is best-effort: it never raises, never blocks a spawn or
startup, and degrades to a no-op if identity can't be confirmed. [2026-06-03]

NO import cycle: this module imports leaf modules only (mc.state, mc.core,
stdlib). It NEVER imports server or any blueprint.
"""
from pathlib import Path
from typing import Any, Callable
import json
import os
import sys

from mc.core import _atomic_write_text, _log, now_iso
from mc.state import process_tracker_lock, tracked_processes

# ── wired by server.py (see wire()) ──────────────────────────────────────────
# _PID_LEDGER_PATH is a server.py module constant -> wired placeholder (the 1.7
# SESSION_LABELS_PATH pattern). _pid_is_alive / _kill_pid are dispatch-family
# call seams (agent_routes 1.12); typed Callable (the 1.13 scheduler_routes
# precedent) so the placeholder None doesn't trip pyright reportOptionalCall at
# the verbatim call sites below.
_PID_LEDGER_PATH: Path = None  # type: ignore[assignment]
_pid_is_alive: Callable[[int], bool] = None  # type: ignore[assignment]
_kill_pid: Callable[..., Any] = None  # type: ignore[assignment]


def wire(*, pid_ledger_path, pid_is_alive_fn, kill_pid_fn):
    """Late-bind the ledger path + the two dispatch-family helpers the reaper
    calls. Called once by server.py before the startup reaper fires (and before
    agent_routes' wire(), which sources proc_identity_fn/persist_pid_ledger_fn
    from this module)."""
    global _PID_LEDGER_PATH, _pid_is_alive, _kill_pid
    _PID_LEDGER_PATH = pid_ledger_path
    _pid_is_alive = pid_is_alive_fn
    _kill_pid = kill_pid_fn


def _proc_identity(pid):
    """Return (image_basename_lower, creation_epoch_float) for a live PID, or
    (None, None) if it can't be read. Dependency-free ctypes on Windows so the
    reaper works without psutil; psutil elsewhere. Used purely as a PID-reuse
    guard — a failure here just means "can't confirm", which is treated as
    "don't reap"."""
    if sys.platform == 'win32':
        try:
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.windll.kernel32
            k32.OpenProcess.restype = wintypes.HANDLE
            k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not h:
                return (None, None)
            try:
                name = None
                buf = ctypes.create_unicode_buffer(32768)
                size = wintypes.DWORD(32768)
                if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                    name = buf.value.rsplit('\\', 1)[-1].lower()
                ct = None
                creation, exit_, kern, user = (wintypes.FILETIME(), wintypes.FILETIME(),
                                               wintypes.FILETIME(), wintypes.FILETIME())
                if k32.GetProcessTimes(h, ctypes.byref(creation), ctypes.byref(exit_),
                                       ctypes.byref(kern), ctypes.byref(user)):
                    ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
                    # FILETIME = 100ns ticks since 1601-01-01 → unix epoch seconds.
                    ct = ticks / 1e7 - 11644473600.0
                return (name, ct)
            finally:
                k32.CloseHandle(h)
        except Exception:
            return (None, None)
    else:
        try:
            import psutil
            p = psutil.Process(int(pid))
            return (p.name().lower(), float(p.create_time()))
        except Exception:
            return (None, None)


def _persist_pid_ledger():
    """Snapshot the live tracked-process PIDs to disk (atomic, best-effort).
    Called after every register/unregister; read once at the next startup by
    _reap_prior_instance_strays(), then cleared. Lives in data/ (NOT
    data/projects/) so load_projects() never sees it."""
    try:
        with process_tracker_lock:
            entries = [{
                'pid': e.get('pid'),
                'name': e.get('name', ''),
                'type': e.get('type', ''),
                'os_image': e.get('os_image'),
                'create_time': e.get('create_time'),
            } for e in tracked_processes.values()]
        _atomic_write_text(_PID_LEDGER_PATH, json.dumps(
            {'mc_pid': os.getpid(), 'written_at': now_iso(), 'children': entries}))
    except Exception:
        pass  # ledger is best-effort; a write failure must never break a spawn


def _should_reap_entry(entry, live_image, live_ct):
    """Pure predicate: should the startup reaper kill this ledgered PID?

    Reap ONLY if the PID is still the same process MC spawned — guarded by an
    exact image-name match and, when both sides have it, a creation-time match
    (within 2s). A reused PID (different image, or a creation time newer than
    recorded) is skipped. Missing identity on either side → do not reap."""
    rec_img = (entry.get('os_image') or '')
    if not rec_img or not live_image:
        return False
    if rec_img.lower() != live_image.lower():
        return False
    rec_ct = entry.get('create_time')
    if rec_ct is not None and live_ct is not None:
        if abs(float(rec_ct) - float(live_ct)) > 2.0:
            return False
    return True


def _reap_prior_instance_strays():
    """Startup: kill child process trees orphaned by a prior MC instance that
    exited (restart/crash) without tearing them down. Reads the prior instance's
    PID ledger, reaps anything still alive AND still the same process, then
    clears the ledger. Best-effort; never blocks startup."""
    try:
        if not _PID_LEDGER_PATH.exists():
            return
        data = json.loads(_PID_LEDGER_PATH.read_text(encoding='utf-8'))
    except Exception:
        return
    me = os.getpid()
    prior_mc = data.get('mc_pid')
    reaped = 0
    for entry in (data.get('children') or []):
        try:
            pid = int(entry.get('pid'))
        except Exception:
            continue
        if pid == me or pid == prior_mc or not _pid_is_alive(pid):
            continue
        live_image, live_ct = _proc_identity(pid)
        if not _should_reap_entry(entry, live_image, live_ct):
            continue
        if _kill_pid(pid, tree=True):
            reaped += 1
    try:
        if reaped:
            _log(f"[reaper] killed {reaped} orphaned child tree(s) from a prior MC "
                 f"instance (was PID {prior_mc})")
        _atomic_write_text(_PID_LEDGER_PATH, json.dumps(
            {'mc_pid': me, 'written_at': now_iso(), 'children': []}))
    except Exception:
        pass
