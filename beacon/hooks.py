"""Beacon write path — piggyback existing rituals, never add a new discipline.

The expensive field is the brief, so it regenerates only on session-close (the
existing Scribe trigger) or explicit refresh — never on dashboard load. Live
state (running/asking) is read-time-overlaid by the aggregator from
agent_sessions, so the ONLY thing a heartbeat must persist is the narrative
(headline+brief) plus a `failed_resume` blocker that outlives the process.

All entry points are best-effort: failure here never blocks Scribe, MEMORY.md,
or completion (same posture as the Distiller dispatch).
"""
import threading

from ._config import CFG, now_iso, _log
from . import briefer, store


def regenerate_brief(project: dict, status=None) -> bool:
    """Regenerate one project's heartbeat brief and persist it. `status` is the
    session-end status: anything other than 'completed' records a failed_resume
    blocker (work may be incomplete). Returns True on write. Never raises."""
    if not CFG.configured or not isinstance(project, dict):
        return False
    pid = project.get('id')
    if not pid:
        return False
    try:
        b = briefer.generate_brief(project)
    except Exception as e:
        _log(f"[beacon] generate_brief failed for {pid}: {e}")
        return False

    blocker = None
    if status and status not in ('completed', 'stopped', 'interrupted'):
        blocker = {
            'type': 'failed_resume',
            'since': now_iso(),
            'summary': f'Session ended with status={status}; work may be incomplete',
        }

    hb = {
        'project': project.get('name') or pid,
        'project_id': pid,
        'updated_at': now_iso(),
        'headline': b.get('headline', ''),
        'live': 'resting',
        'brief': {
            'done': b.get('done', 'unavailable'),
            'standing': b.get('standing', 'unavailable'),
            'next': b.get('next', 'unavailable'),
        },
        'blocker': blocker,
    }
    try:
        store.write_heartbeat(pid, hb)
    except Exception as e:
        _log(f"[beacon] write_heartbeat failed for {pid}: {e}")
        return False
    _log(f"[beacon] heartbeat regenerated for {pid} (status={status})")
    return True


def regenerate_brief_async(project_id: str, status=None) -> None:
    """Threaded best-effort regen — the shape the session-close hook calls so it
    never blocks teardown (mirrors the Distiller daemon-thread dispatch)."""
    lp = CFG.load_project
    if not CFG.configured or not lp:
        return

    def _run():
        try:
            p = lp(project_id)
            if p:
                regenerate_brief(p, status)
        except Exception as e:
            _log(f"[beacon] async regen failed for {project_id}: {e}")

    try:
        threading.Thread(target=_run, daemon=True, name=f"beacon-{project_id}").start()
    except Exception as e:
        _log(f"[beacon] could not start regen thread for {project_id}: {e}")


def refresh(project_id: str) -> bool:
    """Synchronous regen for the per-card refresh affordance
    (POST /api/beacon/refresh/<id>)."""
    lp = CFG.load_project
    if not CFG.configured or not lp:
        return False
    p = lp(project_id)
    return regenerate_brief(p, status=None) if p else False
