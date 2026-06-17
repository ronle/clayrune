"""Beacon aggregator — compose the cross-project digest.

Reads every persisted heartbeat (the narrative), overlays LIVE state from
agent_sessions (running/resting + plan/question blockers) at read time, computes
`stale` from absence of signal, and sorts by attention need.

Core principle: suppression, not display. Ink proportional to attention-need —
a healthy project is one line; a blocked one earns a briefing.
"""
from datetime import datetime, timezone

from ._config import CFG, now_iso, _log
from . import store

# A heartbeat older than cadence × this, while not live, is `stale`.
STALE_TOLERANCE = 1.5


def _parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace('Z', '+00:00'))
    except Exception:
        return None


def _age_hours(ts) -> float:
    dt = _parse_ts(ts)
    if dt is None:
        return float('inf')
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0


def _is_working(live) -> bool:
    return bool(live) and live.get('state') == 'working'


def _compute_blocker(project, hb, live):
    """Live blockers (plan/question) come from the in-memory session — fresh,
    and they persist in Mode B while the process waits. A persisted
    failed_resume survives after the session has left agent_sessions. An
    actively-working session is never blocked."""
    if live:
        reason = live.get('reason')
        if reason == 'plan':
            return {'type': 'plan_pending', 'since': (project.get('last_updated') or now_iso()),
                    'summary': 'Plan ready — awaiting approval'}
        if reason == 'question':
            return {'type': 'question_pending', 'since': (project.get('last_updated') or now_iso()),
                    'summary': 'Question pending — awaiting answer'}
    if hb and isinstance(hb.get('blocker'), dict) and not _is_working(live):
        b = hb['blocker']
        if b.get('type') == 'failed_resume':
            return b
    return None


def _is_stale(project, hb, live) -> bool:
    """Stale fires from ABSENCE of signal. Opt-in per project: a cadence of 0
    (the default) means 'no cadence expectation' → never stale, so the digest
    isn't flooded by legitimately-dormant projects. Set beacon_cadence_hours>0
    on projects you expect regular activity from (e.g. scheduled scanners)."""
    if live:
        return False
    try:
        cadence = float(project.get('beacon_cadence_hours', 0) or 0)
    except Exception:
        cadence = 0.0
    if cadence <= 0:
        return False
    if project.get('status') in ('parked', 'completed'):
        return False
    if not hb:
        return False
    return _age_hours(hb.get('updated_at')) > cadence * STALE_TOLERANCE


def _fallback_headline(project, live):
    if _is_working(live) and live.get('task'):
        return str(live['task']).strip()
    al = project.get('activity_log') or []
    if al:
        return (al[0].get('msg') or '').strip()
    return (project.get('summary') or '').strip() or '—'


def _row(project, hb, live):
    blocker = _compute_blocker(project, hb, live)
    stale = _is_stale(project, hb, live)
    if stale and not blocker:
        blocker = {'type': 'stale',
                   'since': (hb or {}).get('updated_at') or project.get('last_updated') or now_iso(),
                   'summary': 'No signal past expected cadence — possible silent failure'}
    if blocker:
        status = 'blocked'
    elif _is_working(live):
        status = 'running'
    else:
        status = 'resting'
    headline = ((hb or {}).get('headline') or '').strip() or _fallback_headline(project, live)
    return {
        'id': project.get('id'),
        'name': project.get('name') or project.get('id'),
        'domain': project.get('domain'),
        'status': status,                       # bucket: blocked|running|resting
        'live': 'running' if _is_working(live) else 'resting',
        'live_state': (live or {}).get('state'),   # working|idle|asking|None (detail)
        'headline': headline,
        'blocker': blocker,
        'brief': (hb or {}).get('brief'),
        'has_brief': bool(hb),
        'updated_at': (hb or {}).get('updated_at'),     # when the brief was generated
        'last_touched': project.get('last_updated'),    # last project activity
    }


def build_digest() -> dict:
    """The snapshot the view loads. Always returns a well-formed object even if
    beacon isn't configured (degrade, never 500)."""
    if not CFG.configured or not CFG.load_projects or not CFG.live_agent:
        return {'generated_at': now_iso(), 'counts': {'blocked': 0, 'running': 0, 'resting': 0},
                'projects': [], 'configured': False}
    try:
        projects = CFG.load_projects() or []
    except Exception as e:
        _log(f"[beacon] load_projects failed: {e}")
        projects = []
    hbs = store.read_all_heartbeats()

    rows = []
    for p in projects:
        pid = p.get('id')
        if not pid:
            continue
        try:
            live = CFG.live_agent(pid)
        except Exception as e:
            _log(f"[beacon] live_agent({pid}) failed: {e}")
            live = None
        rows.append(_row(p, hbs.get(pid), live))

    blocked = [r for r in rows if r['status'] == 'blocked']
    running = [r for r in rows if r['status'] == 'running']
    resting = [r for r in rows if r['status'] == 'resting']

    # Blocked: oldest blocker first (most-neglected on top). stale sorts among them.
    blocked.sort(key=lambda r: (r.get('blocker') or {}).get('since') or '')
    # Running / resting: most-recently-touched first.
    running.sort(key=lambda r: r.get('last_touched') or '', reverse=True)
    resting.sort(key=lambda r: r.get('last_touched') or '', reverse=True)

    ordered = blocked + running + resting
    return {
        'generated_at': now_iso(),
        'counts': {'blocked': len(blocked), 'running': len(running), 'resting': len(resting)},
        'projects': ordered,
        'configured': True,
    }
