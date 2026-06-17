"""Beacon dependency-injection config.

Beacon is framework-agnostic (the "born outside server.py" constraint). It does
NOT import Flask, server.py, or the route blueprints. Instead server.py wires
the few shared functions it needs through `configure()` at startup (called from
mc/blueprints/beacon_routes.wire()). Submodules read `CFG` — they never reach
back into the blueprint layer, so there is no layering inversion.
"""
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional


def now_iso() -> str:
    """UTC ISO timestamp matching the codebase's mc.core.now_iso() format
    (kept local so beacon has no import-time coupling to mc.core)."""
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


class _Cfg:
    """Injected dependencies. All None until configure() runs."""
    data_root: Optional[Path] = None
    load_projects: Optional[Callable[[], list]] = None
    load_project: Optional[Callable[[str], Any]] = None
    live_agent: Optional[Callable[[str], Any]] = None
    get_memory_path: Optional[Callable[[dict], Any]] = None
    log: Optional[Callable[[str], None]] = None
    configured: bool = False


CFG = _Cfg()


def configure(*, data_root, load_projects_fn, load_project_fn, live_agent_fn,
              get_memory_path_fn, log_fn=None) -> None:
    CFG.data_root = Path(data_root)
    CFG.load_projects = load_projects_fn
    CFG.load_project = load_project_fn
    CFG.live_agent = live_agent_fn
    CFG.get_memory_path = get_memory_path_fn
    CFG.log = log_fn
    CFG.configured = True


def _log(msg: str) -> None:
    """Best-effort logging — never raises. Beacon failures must never break a
    session, a digest read, or completion (same posture as Scribe/Distiller)."""
    try:
        if CFG.log:
            CFG.log(msg)
    except Exception:
        pass
