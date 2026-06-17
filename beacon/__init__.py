"""Beacon — cross-project situational digest.

A push-model layer over Mission Control's many projects: each project maintains
a compact `heartbeat.json` (one-liner + 3-field briefing + blocker), and a
single aggregator reads them all, overlays live agent state, and triages by
attention-need so "does anything need me?" is answerable at a glance.

Framework-agnostic by design (the "born outside server.py" constraint): this
package imports no Flask and no server modules. server.py injects its few
dependencies via configure() (from mc/blueprints/beacon_routes.wire()).

Public API:
  configure(...)                 — wire dependencies (called once at startup)
  build_digest()                 — the triaged cross-project snapshot
  regenerate_brief(project, ...) — (re)generate+persist one heartbeat
  regenerate_brief_async(id,...) — threaded best-effort regen (session-close hook)
  refresh(id)                    — synchronous regen (per-card refresh endpoint)
  read_heartbeat / read_all_heartbeats / write_heartbeat
"""
# Named imports below pull in every submodule in dependency order (aggregator→
# store/_config; hooks→briefer→schema/store), so no separate submodule-binding
# line is needed. The redundant `X as X` form is the PEP 484 re-export marker
# (tells type checkers these are the package's intentional public API).
from ._config import configure as configure, CFG as CFG  # noqa: F401
from .aggregator import build_digest as build_digest  # noqa: F401
from .hooks import (  # noqa: F401
    regenerate_brief as regenerate_brief,
    regenerate_brief_async as regenerate_brief_async,
    refresh as refresh,
)
from .store import (  # noqa: F401
    read_heartbeat as read_heartbeat,
    read_all_heartbeats as read_all_heartbeats,
    write_heartbeat as write_heartbeat,
)

__all__ = [
    'configure', 'CFG',
    'build_digest',
    'regenerate_brief', 'regenerate_brief_async', 'refresh',
    'read_heartbeat', 'read_all_heartbeats', 'write_heartbeat',
]
