"""Scheduler family — blueprint 1.13 (MODERNIZATION_PLAN.md).

Moved VERBATIM from server.py (app-to-bp route-decorator swap is the only
edit applied to the moved text, plus the single Phase-2 obs.heartbeat line in
_scheduler_loop). 6 routes:

  * POST /api/schedule/<id>/run-now  (schedule_run_now)
  * GET  /api/schedule/<id>/runs     (schedule_runs)
  * GET  /api/schedules              (get_schedules)
  * POST /api/schedules              (create_schedule)
  * PUT  /api/schedules/<id>         (update_schedule)
  * DEL  /api/schedules/<id>         (delete_schedule)

Plus the `## -- Scheduled Tasks --` section: cron parser
(_parse_cron_field/_next_cron_match), _compute_next_run, the background
_scheduler_loop (which ALSO drives GitHub auto-sync, code-sync auto-fetch,
stale-session purge, and the process-tracker liveness sweep — all moved
verbatim with it), _start_scheduler (thread-start-once, daemon thread named
'scheduler'), the schedule continuation helpers
(_latest_claude_sid_for_schedule, _latest_session_id_for_schedule,
_newest_run_session_id_for_schedule, _scheduled_run_marker,
_scheduled_continue), and the schedules store (_load_schedules/_save_schedules).

_scheduler_stop lives in mc/state.py since Phase 0 — imported, not moved.

Phase 2: _scheduler_loop heartbeats as 'scheduler' in /api/system/loops (the
only intentional behavior addition; everything else is byte-verbatim).

THE LOAD-BEARING NOTE: _dispatch_agent_internal (the run-now + cron dispatch
path) is wired in from agent_routes (1.12); the scheduler never imports the
memory/scribe/condense machinery. github_sync/project_sync are imported
directly (top-level modules, the 1.3/1.11 precedent) — their register() wiring
stays in server.py.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional
from pathlib import Path
import json
import threading
import time as _time
import uuid

from flask import Blueprint, jsonify, request

from mc import obs
from mc import state
from mc.core import _log, now_iso
from mc.state import (
    _scheduler_stop,
    agent_sessions,
    process_tracker_lock,
    terminal_lock,
    terminal_sessions,
    tracked_processes,
)

# Top-level sync modules (no Flask dep, no import side effects — verified);
# their register() wiring stays in server.py. Same module object via sys.modules.
import github_sync as _gh_sync
import project_sync as _proj_sync

bp = Blueprint('scheduler_routes', __name__)

# -- wired by server.py (see wire()) ------------------------------------------
# SCHEDULES_PATH is a server.py module constant -> wired placeholder (the 1.7
# SESSION_LABELS_PATH pattern). The rest are cross-family call seams.
SCHEDULES_PATH: Path = None  # type: ignore[assignment]
load_project: Callable[[str], Optional[dict]] = None  # type: ignore[assignment]
load_projects: Callable[[], list] = None  # type: ignore[assignment]
_log_agent_activity: Callable[[str, str], Any] = None  # type: ignore[assignment]
# agent-dispatch family (re-homed onto _bp_agent at 1.12):
_dispatch_agent_internal: Callable[..., str] = None  # type: ignore[assignment]
_load_agent_log: Callable[[str], list] = None  # type: ignore[assignment]
_enrich_run_entries: Callable[[list], list] = None  # type: ignore[assignment]
get_manager: Callable[[str], Any] = None  # type: ignore[assignment]
all_managers: Callable[[], list] = None  # type: ignore[assignment]
_pid_is_alive: Callable[[int], bool] = None  # type: ignore[assignment]
_revive_from_agent_log: Callable[..., bool] = None  # type: ignore[assignment]


def wire(*, schedules_path, load_project_fn, load_projects_fn,
         log_agent_activity_fn, dispatch_agent_internal_fn, load_agent_log_fn,
         enrich_run_entries_fn, get_manager_fn, all_managers_fn,
         pid_is_alive_fn, revive_from_agent_log_fn):
    """Late-bind cross-family deps. Called once by server.py before
    register_blueprint + _start_scheduler()."""
    global SCHEDULES_PATH, load_project, load_projects, _log_agent_activity
    global _dispatch_agent_internal, _load_agent_log, _enrich_run_entries
    global get_manager, all_managers, _pid_is_alive, _revive_from_agent_log
    SCHEDULES_PATH = schedules_path
    load_project = load_project_fn
    load_projects = load_projects_fn
    _log_agent_activity = log_agent_activity_fn
    _dispatch_agent_internal = dispatch_agent_internal_fn
    _load_agent_log = load_agent_log_fn
    _enrich_run_entries = enrich_run_entries_fn
    get_manager = get_manager_fn
    all_managers = all_managers_fn
    _pid_is_alive = pid_is_alive_fn
    _revive_from_agent_log = revive_from_agent_log_fn


def _load_schedules():
    if SCHEDULES_PATH.exists():
        try:
            return json.loads(SCHEDULES_PATH.read_text(encoding='utf-8'))
        except Exception:
            pass
    return []

def _save_schedules(schedules):
    SCHEDULES_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEDULES_PATH.write_text(json.dumps(schedules, indent=2, ensure_ascii=False), encoding='utf-8')


# ── Scheduled Tasks ──────────────────────────────────────────────────────────


def _parse_cron_field(field, min_val, max_val):
    """Parse a single cron field into a set of valid integers."""
    values = set()
    for part in field.split(','):
        part = part.strip()
        if '/' in part:
            base, step = part.split('/', 1)
            step = int(step)
            if base == '*':
                start, end = min_val, max_val
            elif '-' in base:
                start, end = (int(x) for x in base.split('-', 1))
            else:
                start, end = int(base), max_val
            for v in range(start, end + 1, step):
                if min_val <= v <= max_val:
                    values.add(v)
        elif part == '*':
            values.update(range(min_val, max_val + 1))
        elif '-' in part:
            lo, hi = (int(x) for x in part.split('-', 1))
            values.update(range(lo, hi + 1))
        else:
            v = int(part)
            if min_val <= v <= max_val:
                values.add(v)
    return values


def _next_cron_match(cron_expr, after_dt):
    """Find the next datetime matching a 5-field cron expression after after_dt.
    Fields: minute hour day-of-month month day-of-week (0/7=Sun)."""
    fields = cron_expr.strip().split()
    if len(fields) != 5:
        return None
    try:
        minutes = _parse_cron_field(fields[0], 0, 59)
        hours = _parse_cron_field(fields[1], 0, 23)
        doms = _parse_cron_field(fields[2], 1, 31)
        months = _parse_cron_field(fields[3], 1, 12)
        dows_raw = _parse_cron_field(fields[4], 0, 7)
        dows = {d % 7 for d in dows_raw}  # Normalize 7 -> 0 (both = Sunday)
    except Exception:
        return None
    dom_any = fields[2] == '*'
    dow_any = fields[4] == '*'
    candidate = after_dt.replace(second=0, microsecond=0) + timedelta(minutes=1)
    end = after_dt + timedelta(days=366)
    while candidate <= end:
        if candidate.month not in months:
            if candidate.month == 12:
                candidate = candidate.replace(year=candidate.year + 1, month=1, day=1, hour=0, minute=0)
            else:
                candidate = candidate.replace(month=candidate.month + 1, day=1, hour=0, minute=0)
            continue
        # cron dow: 0=Sun,1=Mon..6=Sat; Python weekday(): 0=Mon..6=Sun
        py_dow = (candidate.weekday() + 1) % 7
        if dom_any and dow_any:
            day_ok = True
        elif dom_any:
            day_ok = py_dow in dows
        elif dow_any:
            day_ok = candidate.day in doms
        else:
            day_ok = candidate.day in doms or py_dow in dows
        if not day_ok:
            candidate = candidate.replace(hour=0, minute=0) + timedelta(days=1)
            continue
        if candidate.hour not in hours:
            candidate += timedelta(hours=1)
            candidate = candidate.replace(minute=0)
            continue
        if candidate.minute not in minutes:
            candidate += timedelta(minutes=1)
            continue
        return candidate
    return None


def _compute_next_run(schedule):
    """Compute the next run time for a schedule. Returns UTC ISO string or None.

    Time-of-day fields ("daily" `time` and "cron" expressions) are interpreted
    in the host's LOCAL timezone — the user enters "09:00" meaning their wall
    clock, not UTC. The returned ISO string is normalized to UTC (with `Z`
    suffix) so the scheduler loop and storage stay tz-agnostic.

    Storage choice: ISO+Z is what the loop's `now > next_run` comparison and
    the frontend's `new Date(...)` call both expect. The frontend already
    displays `next_run` via `d.getHours()` / `d.getMinutes()` which auto-
    converts to local — so the user sees their wall clock end-to-end.
    """
    stype = schedule.get('schedule_type', 'once')
    # Local-aware "now" — datetime.now() with no arg gives naive local time;
    # .astimezone() attaches the system tz. Used for daily/cron computations.
    now_local = datetime.now().astimezone()
    now_utc = datetime.now(timezone.utc)

    def _to_utc_z(dt):
        """Normalize a tz-aware datetime to a UTC ISO 8601 string with Z."""
        return dt.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')

    if stype == 'once':
        run_at = schedule.get('run_at', '')
        if not run_at:
            return None
        try:
            dt = datetime.fromisoformat(run_at.replace('Z', '+00:00'))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return _to_utc_z(dt) if dt > now_utc else None
        except Exception:
            return None

    elif stype == 'daily':
        time_str = schedule.get('time', '09:00')
        days = schedule.get('days', [])  # 1=Mon..7=Sun, empty=every day
        try:
            h, m = int(time_str.split(':')[0]), int(time_str.split(':')[1])
        except Exception:
            h, m = 9, 0
        # Build candidates in LOCAL time (matches the user's input intent).
        for offset in range(8):
            candidate = now_local.replace(hour=h, minute=m, second=0, microsecond=0) \
                                 + timedelta(days=offset)
            if candidate <= now_local:
                continue
            if days and candidate.isoweekday() not in days:
                continue
            return _to_utc_z(candidate)
        return None

    elif stype == 'interval':
        interval_min = schedule.get('interval_minutes', 60)
        if interval_min <= 0:
            return None
        last_run = schedule.get('last_run', '')
        if last_run:
            try:
                last_dt = datetime.fromisoformat(last_run.replace('Z', '+00:00'))
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                nxt = last_dt + timedelta(minutes=interval_min)
                if nxt <= now_utc:
                    nxt = now_utc + timedelta(seconds=5)
                return _to_utc_z(nxt)
            except Exception:
                pass
        return _to_utc_z(now_utc + timedelta(seconds=5))

    elif stype == 'cron':
        expr = schedule.get('cron_expr', '')
        if not expr:
            return None
        # Cron fields are also local-time-of-day per user intent.
        nxt = _next_cron_match(expr, now_local)
        if nxt:
            if nxt.tzinfo is None:
                # _next_cron_match returns naive — assume local.
                nxt = nxt.replace(tzinfo=now_local.tzinfo)
            return _to_utc_z(nxt)
        return None

    return None


# _scheduler_stop moved to mc/state.py (Phase 0).


def _scheduler_loop():
    """Background daemon: check schedules every 30s and dispatch due tasks."""
    while not _scheduler_stop.is_set():
        obs.heartbeat('scheduler')
        try:
            schedules = _load_schedules()
            now = datetime.now(timezone.utc)
            changed = False
            for sched in schedules:
                if not sched.get('enabled', True):
                    continue
                next_run = sched.get('next_run', '')
                if not next_run:
                    # Compute and save next_run
                    nr = _compute_next_run(sched)
                    if nr:
                        sched['next_run'] = nr
                        changed = True
                    continue
                try:
                    nr_dt = datetime.fromisoformat(next_run.replace('Z', '+00:00'))
                    if nr_dt.tzinfo is None:
                        nr_dt = nr_dt.replace(tzinfo=timezone.utc)
                except Exception:
                    continue
                if now >= nr_dt:
                    # Time to dispatch
                    pid = sched.get('project_id', '')
                    task = sched.get('task', '')
                    if pid and task:
                        sched_id = sched.get('id', '')
                        cont = sched.get('continue_session', True)
                        try:
                            outcome = None
                            if cont:
                                prev_sid = _latest_session_id_for_schedule(pid, sched_id)
                                if prev_sid:
                                    pp_ = load_project(pid)
                                    if pp_:
                                        # Continued thread: stamp a local-time
                                        # header so the long single transcript
                                        # reads as a time series.
                                        outcome = _scheduled_continue(
                                            pp_, pid, prev_sid,
                                            _scheduled_run_marker() + task)
                            if outcome == 'busy':
                                _log(f"[scheduler] Skipped for {pid}: prior run of "
                                     f"{sched_id} still active -> session {prev_sid}")
                            elif outcome in ('appended', 'revived'):
                                _log(f"[scheduler] Continued ({outcome}) for {pid}: "
                                     f"{task[:60]} -> session {prev_sid}")
                            else:
                                # First run, or nothing continuable — fresh dispatch.
                                resume_id = ''
                                if cont:
                                    resume_id = _latest_claude_sid_for_schedule(pid, sched_id)
                                # Resuming the same Claude convo by cold respawn:
                                # reuse the prior run's MC row + mark the turn,
                                # so continued fires stay one thread / one tab /
                                # one resolvable transcript instead of orphaning
                                # a csid-less row per cadence tick.
                                reuse_sid = ''
                                dispatch_task = task
                                if resume_id:
                                    reuse_sid = _newest_run_session_id_for_schedule(pid, sched_id)
                                    dispatch_task = _scheduled_run_marker() + task
                                sid = _dispatch_agent_internal(pid, dispatch_task,
                                                              resume_id=resume_id,
                                                              trigger_type='schedule',
                                                              trigger_id=sched_id,
                                                              reuse_session_id=reuse_sid)
                                tag = ' (resumed)' if resume_id else ''
                                _log(f"[scheduler] Dispatched{tag} for {pid}: {task[:60]} -> session {sid}")
                        except Exception as e:
                            _log(f"[scheduler] Failed to dispatch for {pid}: {e}")
                    sched['last_run'] = now_iso()
                    if sched.get('schedule_type') == 'once':
                        sched['enabled'] = False
                        sched['next_run'] = None
                    else:
                        sched['next_run'] = _compute_next_run(sched)
                    changed = True
            if changed:
                _save_schedules(schedules)
        except Exception as e:
            _log(f"[scheduler] Error: {e}")

        # ── GitHub auto-sync (every 5 minutes) ──
        try:
            for proj in load_projects():
                if proj.get('github_sync_enabled') and proj.get('github_repo'):
                    last = proj.get('github_last_sync', '')
                    if last:
                        try:
                            last_dt = datetime.fromisoformat(last.replace('Z', '+00:00'))
                            if last_dt.tzinfo is None:
                                last_dt = last_dt.replace(tzinfo=timezone.utc)
                            if (now - last_dt).total_seconds() < 300:  # pyright: ignore[reportOperatorIssue]  # moved-verbatim typing debt (1.13)
                                continue
                        except Exception:
                            pass
                    try:
                        _gh_sync.sync_project(proj['id'])
                    except Exception as e:
                        _log(f"[scheduler] GitHub sync error for {proj['id']}: {e}")
        except Exception as e:
            _log(f"[scheduler] GitHub sync loop error: {e}")

        # ── Code sync auto-fetch (every 5 minutes) ──
        try:
            for proj in load_projects():
                if not proj.get('code_sync_enabled'):
                    continue
                last = proj.get('code_sync_last_fetch', '')
                if last:
                    try:
                        last_dt = datetime.fromisoformat(last.replace('Z', '+00:00'))
                        if last_dt.tzinfo is None:
                            last_dt = last_dt.replace(tzinfo=timezone.utc)
                        if (now - last_dt).total_seconds() < 300:  # pyright: ignore[reportOperatorIssue]  # moved-verbatim typing debt (1.13)
                            continue
                    except Exception:
                        pass
                try:
                    _proj_sync.sync_now(proj['id'])
                except Exception as e:
                    _log(f"[scheduler] code sync error for {proj['id']}: {e}")
        except Exception as e:
            _log(f"[scheduler] code sync loop error: {e}")

        # ── Purge stale sessions from memory ──────────────────────────────
        try:
            cutoff = now - timedelta(minutes=60)
            total_stale = 0
            for mgr in all_managers():
                with mgr.lock:
                    stale = []
                    for sid in list(mgr.session_ids):
                        s = agent_sessions.get(sid)
                        if s is None:
                            stale.append(sid)
                            continue
                        if s['status'] not in ('running', 'idle'):
                            try:
                                ts = datetime.fromisoformat(s['started_at'].replace('Z', '+00:00'))
                                if ts.tzinfo is None:
                                    ts = ts.replace(tzinfo=timezone.utc)
                                if ts < cutoff:  # pyright: ignore[reportOperatorIssue]  # moved-verbatim typing debt (1.13)
                                    stale.append(sid)
                            except Exception:
                                stale.append(sid)
                    for sid in stale:
                        agent_sessions.pop(sid, None)
                        mgr.session_ids.discard(sid)
                    total_stale += len(stale)
            if total_stale:
                _log(f"[scheduler] Purged {total_stale} stale agent session(s)")
            with terminal_lock:
                stale_t = []
                for sid, s in terminal_sessions.items():
                    if s['status'] != 'running':
                        stale_t.append(sid)
                for sid in stale_t:
                    terminal_sessions.pop(sid, None)
        except Exception as e:
            _log(f"[scheduler] Session purge error: {e}")

        # ── Process tracker: liveness sweep ───────────────────────────────
        try:
            with process_tracker_lock:
                dead_pids = [pid for pid, entry in tracked_processes.items()
                             if entry.get('proc') and entry['proc'].poll() is not None]
                for pid in dead_pids:
                    tracked_processes.pop(pid, None)
                if dead_pids:
                    _log(f"[scheduler] Cleaned {len(dead_pids)} dead process(es) from tracker")
        except Exception as e:
            _log(f"[scheduler] Process tracker sweep error: {e}")

        _scheduler_stop.wait(30)


def _start_scheduler():
    t = threading.Thread(target=_scheduler_loop, daemon=True, name='scheduler')
    t.start()
    return t


def _latest_claude_sid_for_schedule(project_id, schedule_id):
    """Return the most recent claude_session_id from a previous run of this schedule,
    or '' if none. Agent log is stored newest-first."""
    if not project_id or not schedule_id:
        return ''
    log = _load_agent_log(project_id)
    for e in log:
        if (e.get('trigger_type') == 'schedule'
                and e.get('trigger_id') == schedule_id
                and e.get('claude_session_id')):
            return e.get('claude_session_id', '')
    return ''


def _latest_session_id_for_schedule(project_id, schedule_id):
    """Return the MC session_id of this schedule's most recent run so the next
    fire can CONTINUE it (same thread, same UI tab) instead of minting a fresh
    session_id (Defect A — every _dispatch_agent_internal call does
    `uuid.uuid4().hex[:12]`, which the frontend tab strip keys on, so a new id
    is by construction a new tab).

    Prefers a still-live in-memory session (the common case: a persistent Mode-B
    session sitting idle between fires — exactly the "endless idle tabs"
    screenshot). Falls back to the newest agent_log row for this schedule that
    carries a claude_session_id (revivable after a restart, thanks to the
    _note_claude_sid backfill). Returns '' when there is nothing to continue
    (first run, or no revivable history)."""
    if not project_id or not schedule_id:
        return ''
    # Live session wins — pick the most recently dispatched one for this trigger.
    best_sid, best_t = '', -1.0
    for s in list(agent_sessions.values()):
        if (s.get('project_id') == project_id
                and s.get('trigger_type') == 'schedule'
                and s.get('trigger_id') == schedule_id
                and not s.get('incognito')):
            t = s.get('_dispatch_time') or 0
            if t >= best_t:
                best_sid, best_t = s.get('session_id', ''), t
    if best_sid:
        return best_sid
    # Otherwise the newest revivable logged run (csid present → -r resumable).
    log = _load_agent_log(project_id)
    for e in log:
        if (e.get('trigger_type') == 'schedule'
                and e.get('trigger_id') == schedule_id
                and e.get('claude_session_id')
                and e.get('session_id')):
            return e.get('session_id', '')
    return ''


def _newest_run_session_id_for_schedule(project_id, schedule_id):
    """Return the MC session_id of this schedule's newest agent_log row REGARDLESS
    of status or csid presence ('' if none).

    Differs from _latest_session_id_for_schedule (which only returns a row that is
    live or carries a csid). Used by the scheduler's fresh-resume fallback to
    REUSE the prior run's row instead of orphaning a brand-new one every fire —
    the orphan-row bug that left scheduled threads with no resolvable transcript
    (continued runs share one Claude session, so they belong on one MC row)."""
    if not project_id or not schedule_id:
        return ''
    for e in _load_agent_log(project_id):  # newest-first
        if (e.get('trigger_type') == 'schedule'
                and e.get('trigger_id') == schedule_id
                and e.get('session_id')):
            return e.get('session_id', '')
    return ''


def _scheduled_run_marker():
    """A local-time header prepended to the task of a CONTINUED scheduled run so a
    single long thread reads as a time series ('when did each fire happen')."""
    try:
        ts = datetime.now().astimezone().strftime('%Y-%m-%d %H:%M %Z')
    except Exception:
        ts = now_iso()
    return f"[Scheduled run · {ts}]\n\n"


def _scheduled_continue(p, project_id, session_id, task):
    """Continue an existing scheduled run with `task` as the next turn, keeping
    the SAME session_id (→ same UI tab, same Claude conversation). Mirrors the
    proven agent_followup decision tree but for the scheduler:

      - live persistent Mode-B process, idle  → append task to its stdin
      - live session currently running        → 'busy' (skip this fire; don't
                                                 pile overlapping turns — the
                                                 prior run continues, the next
                                                 cadence tick will catch up)
      - session gone / dead / Mode A          → _revive_from_agent_log (spawns
                                                 fresh `-r <csid>`, REUSES the
                                                 same session_id by design)

    Returns 'appended' | 'busy' | 'revived', or None to tell the caller to fall
    back to a fresh dispatch (nothing continuable)."""
    pp = p.get('project_path', '')
    mgr = get_manager(project_id)
    mgr.ensure_guardian()
    with mgr.lock:
        existing = agent_sessions.get(session_id)
        if existing and existing.get('project_id') == project_id:
            status = existing.get('status')
            if status == 'running':
                return 'busy'
            proc = existing.get('proc')
            alive = (existing.get('mode') == 'B'
                     and existing.get('process_alive')
                     and proc is not None
                     and proc.poll() is None
                     and _pid_is_alive(proc.pid))
            if alive:
                existing['status'] = 'running'
                existing['last_status_change_time'] = _time.time()
                existing['last_output_time'] = _time.time()
                existing['log_lines'].append(f"\n> [scheduled run]: {task}\n")
                stdin_msg = json.dumps({
                    "type": "user",
                    "message": {"role": "user", "content": task},
                }) + '\n'

                def _write_stdin():
                    lock = existing.get('stdin_lock')
                    if lock:
                        lock.acquire()
                    try:
                        existing['proc'].stdin.write(stdin_msg)
                        existing['proc'].stdin.flush()
                    except Exception as e:
                        existing['log_lines'].append(f'[stdin write error: {e}]')
                        existing['status'] = 'error'
                        existing['last_status_change_time'] = _time.time()
                        existing['process_alive'] = False
                    finally:
                        if lock:
                            lock.release()

                threading.Thread(target=_write_stdin, daemon=True).start()
                _log_agent_activity(project_id, f"Scheduled run (appended): {task[:100]}")
                return 'appended'
    # Not live (purged / dead / Mode A) — revive from log; this reuses the same
    # session_id so the UI tab stays addressed (see _revive_from_agent_log).
    if not pp or not Path(pp).is_dir():
        return None
    try:
        if _revive_from_agent_log(project_id, session_id, task, p):
            _log_agent_activity(project_id, f"Scheduled run (revived): {task[:100]}")
            return 'revived'
    except Exception as e:
        _log(f"[scheduled-continue] {project_id}: revive failed: {e}")
    return None


@bp.route('/api/schedules')
def get_schedules():
    schedules = _load_schedules()
    # Enrich with project names
    projects_map = {p['id']: p.get('name', p['id']) for p in load_projects()}
    for s in schedules:
        s['project_name'] = projects_map.get(s.get('project_id', ''), s.get('project_id', ''))
    return jsonify(schedules)


@bp.route('/api/schedules', methods=['POST'])
def create_schedule():
    data = request.get_json() or {}
    pid = (data.get('project_id') or '').strip()
    task = (data.get('task') or '').strip()
    stype = data.get('schedule_type', 'daily')
    if not pid or not task:
        return jsonify({'error': 'project_id and task required'}), 400

    sched = {
        'id': uuid.uuid4().hex[:8],
        'enabled': True,
        'project_id': pid,
        'task': task,
        'description': (data.get('description') or '').strip(),
        'continue_session': bool(data.get('continue_session', True)),
        'schedule_type': stype,
        'time': data.get('time', '09:00'),
        'days': data.get('days', []),
        'interval_minutes': data.get('interval_minutes', 60),
        'run_at': data.get('run_at', ''),
        'cron_expr': data.get('cron_expr', ''),
        'last_run': None,
        'next_run': None,
        'created_at': now_iso(),
    }
    sched['next_run'] = _compute_next_run(sched)

    schedules = _load_schedules()
    schedules.append(sched)
    _save_schedules(schedules)
    return jsonify(sched), 201


@bp.route('/api/schedules/<schedule_id>', methods=['PUT'])
def update_schedule(schedule_id):
    data = request.get_json() or {}
    schedules = _load_schedules()
    sched = next((s for s in schedules if s['id'] == schedule_id), None)
    if not sched:
        return jsonify({'error': 'not found'}), 404

    for key in ('project_id', 'task', 'description', 'continue_session',
                'schedule_type', 'time', 'days',
                'interval_minutes', 'enabled', 'run_at', 'cron_expr'):
        if key in data:
            sched[key] = data[key]

    # Recompute next_run
    sched['next_run'] = _compute_next_run(sched)
    _save_schedules(schedules)
    return jsonify(sched)


@bp.route('/api/schedules/<schedule_id>', methods=['DELETE'])
def delete_schedule(schedule_id):
    schedules = _load_schedules()
    before = len(schedules)
    schedules = [s for s in schedules if s['id'] != schedule_id]
    if len(schedules) == before:
        return jsonify({'error': 'not found'}), 404
    _save_schedules(schedules)
    return jsonify({'ok': True})


@bp.route('/api/schedule/<schedule_id>/run-now', methods=['POST'])
def schedule_run_now(schedule_id):
    """Manually fire a schedule's task right now without disturbing its cadence.

    Updates last_run for visual feedback but leaves next_run/enabled untouched —
    the schedule still fires at its normal cadence; this is an extra dispatch.
    """
    schedules = _load_schedules()
    sched = next((s for s in schedules if s.get('id') == schedule_id), None)
    if not sched:
        return jsonify({'error': 'schedule not found'}), 404
    pid = sched.get('project_id', '')
    task = sched.get('task', '')
    if not pid or not task:
        return jsonify({'error': 'schedule missing project or task'}), 400
    cont = sched.get('continue_session', True)
    # Continue the schedule's existing thread/tab when possible (same as the
    # cron path) instead of always minting a new session_id → new tab.
    if cont:
        prev_sid = _latest_session_id_for_schedule(pid, schedule_id)
        if prev_sid:
            pcur = load_project(pid)
            if pcur:
                outcome = _scheduled_continue(pcur, pid, prev_sid,
                                              _scheduled_run_marker() + task)
                if outcome == 'busy':
                    return jsonify({'ok': False, 'busy': True,
                                    'session_id': prev_sid,
                                    'error': 'previous run still active'}), 409
                if outcome in ('appended', 'revived'):
                    sched['last_run'] = now_iso()
                    _save_schedules(schedules)
                    return jsonify({'ok': True, 'session_id': prev_sid,
                                    'continued': outcome})
    resume_id = ''
    if cont:
        resume_id = _latest_claude_sid_for_schedule(pid, schedule_id)
    reuse_sid = ''
    dispatch_task = task
    if resume_id:
        reuse_sid = _newest_run_session_id_for_schedule(pid, schedule_id)
        dispatch_task = _scheduled_run_marker() + task
    try:
        sid = _dispatch_agent_internal(pid, dispatch_task, resume_id=resume_id,
                                       trigger_type='schedule',
                                       trigger_id=schedule_id,
                                       reuse_session_id=reuse_sid)
    except ValueError as e:
        code = 404 if 'not found' in str(e) else 400
        return jsonify({'error': str(e)}), code
    except FileNotFoundError:
        return jsonify({'error': 'Claude CLI not found'}), 500
    except Exception as e:
        return jsonify({'error': f'dispatch failed: {e}'}), 500
    sched['last_run'] = now_iso()
    _save_schedules(schedules)
    return jsonify({'ok': True, 'session_id': sid, 'resumed': bool(resume_id)})


@bp.route('/api/schedule/<schedule_id>/runs')
def schedule_runs(schedule_id):
    """Return paginated agent_log entries dispatched by this schedule.

    Query params:
      limit  page size (default 50)
      offset rows to skip (default 0)

    Response shape: {runs, total, offset, limit}.
    `total` is the total matching across all pages (lets the FE render
    pagination controls). `runs` is the requested slice.
    """
    try:
        limit = int(request.args.get('limit', 50))
    except Exception:
        limit = 50
    try:
        offset = int(request.args.get('offset', 0))
    except Exception:
        offset = 0
    if limit < 1: limit = 50
    if limit > 200: limit = 200
    if offset < 0: offset = 0

    schedules = _load_schedules()
    sched = next((s for s in schedules if s.get('id') == schedule_id), None)
    if not sched:
        return jsonify({'error': 'schedule not found'}), 404
    pid = sched.get('project_id', '')
    if not pid:
        return jsonify({'runs': [], 'total': 0, 'offset': 0, 'limit': limit})
    log = _load_agent_log(pid)
    runs = [e for e in log
            if e.get('trigger_type') == 'schedule' and e.get('trigger_id') == schedule_id]
    total = len(runs)
    page = runs[offset:offset + limit]
    return jsonify({
        'runs': _enrich_run_entries(page),
        'total': total,
        'offset': offset,
        'limit': limit,
    })
