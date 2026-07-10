"""Steward dependency-injection config (beacon/_config.py precedent).

The steward package is framework-agnostic — it does NOT import Flask, server.py,
or the blueprints. server.py wires the few shared functions it needs through
`configure()` at startup (called from mc/blueprints/steward_routes.wire()).
Submodules read `CFG`; they never reach back into the blueprint layer.
"""
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


class _Cfg:
    """Injected dependencies. All None until configure() runs."""
    data_root: Optional[Path] = None
    load_project: Optional[Callable[[str], Any]] = None
    save_project: Optional[Callable[[str, dict], Any]] = None
    load_projects: Optional[Callable[[], list]] = None
    append_note: Optional[Callable[..., bool]] = None      # _append_note_to_backlog_item
    notify_push: Optional[Callable[..., Any]] = None        # optional agent->human push
    log: Optional[Callable[[str], None]] = None
    configured: bool = False


CFG = _Cfg()


def configure(*, data_root, load_project_fn, save_project_fn, load_projects_fn,
              append_note_fn, notify_push_fn=None, log_fn=None) -> None:
    CFG.data_root = Path(data_root)
    CFG.load_project = load_project_fn
    CFG.save_project = save_project_fn
    CFG.load_projects = load_projects_fn
    CFG.append_note = append_note_fn
    CFG.notify_push = notify_push_fn
    CFG.log = log_fn
    CFG.configured = True


def _log(msg: str) -> None:
    """Best-effort logging — never raises. Steward failures must never break a
    session, a schedule tick, or completion (Scribe/Distiller/beacon posture)."""
    try:
        if CFG.log:
            CFG.log(msg)
    except Exception:
        pass
