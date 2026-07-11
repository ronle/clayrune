"""Autonomous Steward — thin blueprint over the framework-agnostic steward/
package. Bootstraps a fire-and-forget steward on a project: sets config, seeds
the charter, installs the reversibility fence into the project's settings, and
creates a standing interval schedule that continues ONE thread each cycle.
Disable is the kill switch. Loop-health surfaces pending decisions / blocked.

Precedent: distiller_routes / beacon_routes (born-outside-server package + thin
blueprint wired from server.py). Scope: docs/AUTONOMOUS_STEWARD_SCOPE.md.
"""
import re
from pathlib import Path
from typing import Any, Callable, Optional

from flask import Blueprint, jsonify, request

from mc import state
from mc.core import _log, now_iso
import steward
from steward import core as _core
# Sibling blueprint: reuse the scheduler store + next-run math (never reimplement
# the schedule shape). scheduler_routes doesn't import steward → no cycle.
from mc.blueprints import scheduler_routes as _sched

bp = Blueprint('steward_routes', __name__)

# -- wired by server.py (see wire()) ------------------------------------------
load_project: Callable[[str], Optional[dict]] = None      # type: ignore[assignment]
save_project: Callable[[str, dict], Any] = None           # type: ignore[assignment]


def wire(*, data_root, load_project_fn, save_project_fn, load_projects_fn,
         append_note_fn, notify_push_fn=None, log_fn=None):
    """Late-bind deps + configure the steward package. Called once by server.py
    before register_blueprint."""
    global load_project, save_project
    load_project = load_project_fn
    save_project = save_project_fn
    steward.configure(
        data_root=data_root,
        load_project_fn=load_project_fn,
        save_project_fn=save_project_fn,
        load_projects_fn=load_projects_fn,
        append_note_fn=append_note_fn,
        notify_push_fn=notify_push_fn,
        log_fn=log_fn,
    )


# ── Steward schedule helpers (a schedule row tagged steward=True) ─────────────
def _find_steward_schedule(project_id, schedules):
    return next((s for s in schedules
                 if s.get('steward') and s.get('project_id') == project_id), None)


def _make_steward_schedule(project_id, task, cadence_minutes):
    sched = {
        'id': _sched.uuid.uuid4().hex[:8],
        'enabled': True,
        'steward': True,                 # marker → find/remove precisely
        'project_id': project_id,
        'task': task,
        'description': 'Autonomous steward cycle (self-directing)',
        'continue_session': True,        # same thread each fire (time series)
        'schedule_type': 'interval',
        'time': '09:00',
        'days': [],
        'interval_minutes': int(cadence_minutes),
        'run_at': '',
        'cron_expr': '',
        'delete_after_run': False,
        'last_run': None,
        'next_run': None,
        'created_at': now_iso(),
    }
    sched['next_run'] = _sched._compute_next_run(sched)
    return sched


# ── Endpoints ─────────────────────────────────────────────────────────────────
@bp.route('/api/project/<project_id>/steward', methods=['GET'])
def steward_status(project_id):
    p = load_project(project_id)
    if p is None:
        return jsonify({'error': 'not found'}), 404
    charter = _core.find_charter(p)
    schedules = _sched._load_schedules()
    sched = _find_steward_schedule(project_id, schedules)
    return jsonify({
        'enabled': _core.steward_enabled(p),
        'objective': _core.get_objective(p),
        'cadence_minutes': _core.get_cadence_minutes(p),
        'charter_item_id': (charter or {}).get('id'),
        'schedule_id': (sched or {}).get('id'),
        'next_run': (sched or {}).get('next_run'),
        'fenced': True,
    })


def _do_enable(project_id, objective, cadence):
    """Shared enable bootstrap (used by the per-project AND standalone paths).
    Assumes the project record already exists. Returns (response_dict, status)."""
    p = load_project(project_id)
    if p is None:
        return {'error': 'not found'}, 404

    # 1. Persist config.
    p['steward_mode'] = 'on'
    p['steward_objective'] = objective
    if cadence is not None:
        p['steward_cadence_minutes'] = int(cadence)
    p['last_updated'] = now_iso()
    save_project(project_id, p)
    cadence_min = _core.get_cadence_minutes(load_project(project_id))

    # 2. Seed the charter (pinned backlog item).
    charter = _core.ensure_charter(project_id, objective)
    if charter is None:
        return {'error': 'could not create charter'}, 500

    # 3. Install the reversibility fence into the project's .claude/settings.json.
    project_path = p.get('project_path', '')
    fenced = _core.install_fence_to_project(project_path) if project_path else False
    if not fenced:
        _log(f"[steward] WARNING: fence NOT installed for {project_id} "
             f"(no project_path or write failed) — running unfenced", flush=True)

    # 4. Create/refresh the standing steward schedule.
    p2 = load_project(project_id)
    task = _core.build_cycle_task(p2, charter)
    schedules = _sched._load_schedules()
    existing = _find_steward_schedule(project_id, schedules)
    if existing:
        existing.update({'enabled': True, 'task': task,
                         'interval_minutes': cadence_min})
        existing['next_run'] = _sched._compute_next_run(existing)
        sched = existing
    else:
        sched = _make_steward_schedule(project_id, task, cadence_min)
        schedules.append(sched)
    _sched._save_schedules(schedules)

    # 5. Announce.
    _core.steward_notify(project_id, 'fyi',
                         f'Steward enabled. Objective: {objective}. '
                         f'Cadence: every {cadence_min} min. '
                         f'Fence: {"on" if fenced else "OFF (unfenced!)"}.')

    return {
        'ok': True, 'enabled': True, 'project_id': project_id, 'objective': objective,
        'cadence_minutes': cadence_min, 'charter_item_id': charter.get('id'),
        'schedule_id': sched.get('id'), 'next_run': sched.get('next_run'),
        'fenced': fenced,
    }, 200


@bp.route('/api/project/<project_id>/steward/enable', methods=['POST'])
def steward_enable(project_id):
    """Turn a project into an autonomous steward. Idempotent."""
    data = request.get_json() or {}
    p = load_project(project_id)
    if p is None:
        return jsonify({'error': 'not found'}), 404
    objective = (data.get('objective') or _core.get_objective(p) or '').strip()
    if not objective:
        return jsonify({'error': 'objective required (the field of responsibility)'}), 400
    resp, status = _do_enable(project_id, objective,
                              data.get('cadence_minutes', p.get('steward_cadence_minutes')))
    return jsonify(resp), status


# ── Standalone (operator-level) steward — not tied to an existing project ─────
STEWARD_WORKSPACE_PREFIX = '_steward_'


def _slugify(name: str) -> str:
    s = re.sub(r'[^a-z0-9]+', '_', (name or '').lower()).strip('_')[:40]
    return s or 'steward'


def _ensure_steward_workspace(slug: str, name: str) -> Optional[dict]:
    """Create-or-return a standalone steward's pseudo-project (mirrors the
    _incognito pattern): a project record at data/projects/_steward_<slug>.json
    with its own scratch workspace folder, hidden from the dashboard grid.
    Returns the project dict, or None on failure."""
    pid = STEWARD_WORKSPACE_PREFIX + slug
    existing = load_project(pid)
    if existing is not None:
        return existing
    base = Path(state.CONFIG.get('auto_workspace_base') or str(Path.home() / 'MissionControl'))
    workspace = base / pid
    try:
        workspace.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        _log(f"[steward] could not create workspace {workspace}: {e}", flush=True)
        return None
    rec = {
        'id': pid,
        'name': name.strip() or 'Standalone Steward',
        'emoji': '\U0001F9ED',  # compass — self-directing
        'description': 'Standalone (operator-level) steward workspace. Not tied '
                       'to a specific codebase; runs a cross-cutting mandate.',
        'project_path': str(workspace),
        'status': 'active',
        'domain': 'general',
        'activity_log': [],
        'backlog': [],
        'current_task': '',
        'next_action': '',
        '_is_steward_workspace': True,
        'last_updated': now_iso(),
    }
    try:
        save_project(pid, rec)
    except Exception as e:
        _log(f"[steward] could not save workspace record {pid}: {e}", flush=True)
        return None
    return rec


@bp.route('/api/steward/standalone/enable', methods=['POST'])
def steward_standalone_enable(project_id=None):
    """Start a standalone steward: provision a dedicated workspace pseudo-project,
    then run the same enable bootstrap on it. Idempotent per name (slug)."""
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    objective = (data.get('objective') or '').strip()
    if not name:
        return jsonify({'error': 'name required (what to call this steward)'}), 400
    if not objective:
        return jsonify({'error': 'objective required (the field of responsibility)'}), 400
    slug = _slugify(name)
    ws = _ensure_steward_workspace(slug, name)
    if ws is None:
        return jsonify({'error': 'could not provision steward workspace'}), 500
    resp, status = _do_enable(ws['id'], objective, data.get('cadence_minutes'))
    if isinstance(resp, dict):
        resp['standalone'] = True
    return jsonify(resp), status


@bp.route('/api/project/<project_id>/steward/disable', methods=['POST'])
def steward_disable(project_id):
    """Kill switch. Stops future cycles, removes the fence (restores normal
    behavior). Leaves the charter + its notes intact as a record. Idempotent."""
    p = load_project(project_id)
    if p is None:
        return jsonify({'error': 'not found'}), 404

    p['steward_mode'] = 'off'
    p['last_updated'] = now_iso()
    save_project(project_id, p)

    # Remove the standing schedule (no more wakes).
    schedules = _sched._load_schedules()
    kept = [s for s in schedules
            if not (s.get('steward') and s.get('project_id') == project_id)]
    removed = len(schedules) - len(kept)
    if removed:
        _sched._save_schedules(kept)

    # Un-fence the project (restore normal unattended-off behavior).
    unfenced = _core.remove_fence_from_project(p.get('project_path', ''))

    _core.steward_notify(project_id, 'fyi', 'Steward disabled (kill switch).')
    return jsonify({'ok': True, 'enabled': False,
                    'schedule_removed': bool(removed), 'unfenced': unfenced})


@bp.route('/api/steward/loop-health', methods=['GET'])
def steward_loop_health():
    health = _core.loop_health()
    # Enrich each steward with its latest conversation session id + schedule id so
    # the Automation card can deep-link straight into the steward's chat thread
    # (which the conversation tab's user-initiated filter would otherwise hide).
    try:
        schedules = _sched._load_schedules()
        for entry in health.get('enabled', []):
            pid = entry.get('project_id')
            sched = _find_steward_schedule(pid, schedules)
            if not sched:
                continue
            entry['schedule_id'] = sched.get('id')
            try:
                entry['claude_session_id'] = _sched._latest_claude_sid_for_schedule(
                    pid, sched.get('id')) or ''
            except Exception:
                entry['claude_session_id'] = ''
    except Exception as e:
        _log(f"[steward] loop-health enrichment failed: {e}", flush=True)
    return jsonify(health)
