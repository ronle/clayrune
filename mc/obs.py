"""Observability layer (MODERNIZATION_PLAN.md Phase 2).

Two primitives, deliberately tiny:

- log(subsystem, msg, level)  — single-line structured output through the
  existing mc.core._log chokepoint: `[<subsystem>] msg`.
- heartbeat(subsystem)        — records "this background loop completed an
  iteration" in mc.state.last_ok. GET /api/system/loops (system_routes
  blueprint) exposes {subsystem: {last_ok, age_seconds}} so a silently-dead
  loop becomes visible instead of failing dark.

Loops get instrumented as their blueprints extract (stream readers at 1.12,
scheduler at 1.13); the update-check daemon starts at 1.6.
"""

import time as _time

from mc import state
from mc.core import _log


def log(subsystem: str, msg: str, level: str = 'info') -> None:
    _log(f"[{subsystem}] {msg}", level=level)


def heartbeat(subsystem: str) -> None:
    """Mark a successful loop iteration. Cheap; safe from any thread."""
    with state._last_ok_lock:
        state.last_ok[subsystem] = _time.time()


def snapshot() -> dict:
    """{subsystem: {last_ok, age_seconds}} for /api/system/loops."""
    now = _time.time()
    with state._last_ok_lock:
        return {
            name: {'last_ok': ts, 'age_seconds': round(now - ts, 1)}
            for name, ts in sorted(state.last_ok.items())
        }
