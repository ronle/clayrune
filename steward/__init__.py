"""Autonomous Steward — fire-and-forget self-directing project agent.

Framework-agnostic package (born outside server.py, beacon/ precedent). Holds
the reversibility FENCE (the load-bearing safety backstop), per-project steward
config, charter helpers, the cycle-task builder, and the notify seam. Wired into
the server via mc/blueprints/steward_routes.py.

Scope + rationale: docs/AUTONOMOUS_STEWARD_SCOPE.md. The directive the steward
runs on is data/skills/builtin/mc-steward/SKILL.md.
"""
from .fence import classify_action, classify_bash, FenceDecision  # noqa: F401
from ._config import configure, CFG  # noqa: F401
from .core import (  # noqa: F401
    steward_enabled, get_objective, get_cadence_minutes, find_charter,
    ensure_charter, build_cycle_task, steward_notify, ensure_fence_settings,
    fence_settings_path, fence_script_path, loop_health, CHARTER_PREFIX,
)
