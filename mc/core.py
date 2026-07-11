"""Cross-cutting pure helpers (MODERNIZATION_PLAN.md Phase 0).

Moved VERBATIM from server.py; server.py keeps `from mc.core import ...`
shims so every existing call site is unchanged. The single permitted edit:
_log reads the log level via `state.CONFIG` (the live alias server.py binds
at boot) instead of the bare CONFIG global.

This module must never import server.py.
"""

import builtins as _builtins
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from flask import request

from mc import state

# ── Logging shim (IMPROVEMENT_PLAN_V2.md P2-3) ───────────────────────────────
# Single chokepoint for the ~100 diagnostic _log()s. Deliberately
# _log()-signature-compatible: *args + **kw pass straight through, so the
# `_log(` → `_log(` sweep is purely mechanical and behavior-IDENTICAL at
# the default level ('info' shows everything info+). Set `log_level` to
# 'warn'/'error' to quiet the chatter, or 'debug' for more. Levels are
# advisory — a bare `_log("...")` is 'info'; pass level='warn'/'error' at
# noteworthy call sites over time (opportunistic, not a sweep).
_LOG_LEVELS = {'debug': 10, 'info': 20, 'warn': 30, 'error': 40}


def _log(*args, level='info', **kw):
    """_log()-compatible, level-gated. Default level keeps current output
    exactly (info threshold ≤ info). flush defaults True (most existing
    call sites already pass flush=True; making it the default is harmless
    and keeps subprocess-interleaved logs ordered)."""
    threshold = _LOG_LEVELS.get(str(state.CONFIG.get('log_level', 'info')).lower(), 20)
    if _LOG_LEVELS.get(level, 20) < threshold:
        return
    kw.setdefault('flush', True)
    _builtins.print(*args, **kw)


def _atomic_write_text(path, text, encoding='utf-8'):
    """Write via temp-file + os.replace so a crash mid-write can't leave a
    torn MEMORY.md/archive (SPEC §3.A.MID atomicity). Same-dir temp so
    os.replace is atomic on the same filesystem."""
    path = Path(path)
    tmp = path.with_name(f'.{path.name}.tmp{os.getpid()}')
    tmp.write_text(text, encoding=encoding)
    os.replace(tmp, path)


def sweep_orphan_tmpfiles(roots, max_age_hours=24):
    """Delete orphaned temp files left behind by crashed writers.

    Two families: same-dir atomic-write temps (`.{name}.tmp{pid}`, see
    _atomic_write_text — a crash between write and os.replace strands one,
    e.g. data/.mc_child_pids.json.tmp49260 found 2026-07-11) under each
    root, and stale `clayrune-sysprompt-*.txt` spawn-context files in the
    OS temp dir (their normal cleanup rides on proc.wait(), which a hard
    MC kill skips). Age-gated so a live in-flight write is never swept.
    Best-effort; returns the number of files removed.
    """
    import re
    import tempfile
    import time as _t
    cutoff = _t.time() - max_age_hours * 3600
    removed = 0
    pat = re.compile(r'^\..+\.tmp\d+$')
    candidates = []
    for root in roots:
        try:
            root = Path(root)
            if root.is_dir():
                candidates.extend(
                    p for p in root.rglob('.*.tmp*') if pat.match(p.name))
        except Exception as e:
            _log(f"[tmp-sweep] scan of {root} failed: {e}")
    try:
        candidates.extend(
            Path(tempfile.gettempdir()).glob('clayrune-sysprompt-*.txt'))
    except Exception as e:
        _log(f"[tmp-sweep] temp-dir scan failed: {e}")
    for f in candidates:
        try:
            if f.is_file() and f.stat().st_mtime < cutoff:
                f.unlink()
                removed += 1
        except OSError:
            continue
    if removed:
        _log(f"[tmp-sweep] removed {removed} orphaned temp file(s)")
    return removed


def _harden_secret_perms(path) -> None:
    """Best-effort: restrict a secret file (provider API keys, VAPID/Firebase
    keys, LAN passcode hash, mobile-pairing token) to the owning user only.
    POSIX → chmod 0600; Windows → strip ACL inheritance and grant only the
    current user. Never raises — a perms failure must not break the write."""
    p = str(path)
    try:
        if os.name == 'nt':
            import getpass
            user = os.environ.get('USERNAME') or getpass.getuser()
            subprocess.run(
                ['icacls', p, '/inheritance:r', '/grant:r', f'{user}:F'],
                capture_output=True,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        else:
            os.chmod(p, 0o600)
    except Exception:
        pass


def time_ago(ts_str):
    if not ts_str:
        return 'never'
    try:
        ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)
        secs = int((now - ts).total_seconds())
        if secs < 60:      return f'{secs}s ago'
        if secs < 3600:  return f'{secs // 60}m ago'
        if secs < 86400: return f'{secs // 3600}h ago'
        return f'{secs // 86400}d ago'
    except (ValueError, TypeError, AttributeError):
        return ts_str


def now_iso():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def file_type(filename):
    """Return a simple type hint for UI rendering."""
    ext = Path(filename).suffix.lower()
    images = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp'}
    if ext in images:
        return 'image'
    if ext == '.pdf':
        return 'pdf'
    return 'file'


def _is_loopback_request() -> bool:
    ra = (request.remote_addr or '').strip().lower()
    if ra in ('127.0.0.1', '::1', 'localhost'):
        return True
    # IPv4-mapped IPv6 loopback (e.g. ::ffff:127.0.0.1)
    return ra.startswith('::ffff:127.')
