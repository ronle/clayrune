"""System + processes endpoints — blueprint 1.6 (MODERNIZATION_PLAN.md).

Moved VERBATIM from server.py (two source regions): 4 /api/processes +
11 /api/system routes (plan table said 3+11 — processes/cleanup grew), plus
the system-status passive cache, restart machinery, and the update-check
daemon loop. _LAST_SYSTEM_STATUS / _LAST_RESTART_TIME (the last two rebound
globals) now live in mc/state.py with every reference rewritten to state.*.

Phase 2 addition (deliberate NEW route, invariant 209 -> 210):
GET /api/system/loops exposes mc.obs heartbeats.
"""

import json
import os
import shutil
import subprocess
import sys
import threading
import time as _time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from flask import Blueprint, jsonify, request

from mc import obs, state
from mc.core import _atomic_write_text, _log, now_iso, time_ago
from mc.state import (
    _UPDATE_CHECK_BOOT_DELAY_S,
    _hivemind_lock,
    _hivemind_sessions,
    terminal_lock,
    terminal_sessions,
    _UPDATE_CHECK_CACHE,
    _UPDATE_CHECK_INTERVAL_S,
    _UPDATE_CHECK_LOCK,
    agent_sessions,
    process_tracker_lock,
    tracked_processes,
)

bp = Blueprint('system_routes', __name__)

# ── wired by server.py (see wire()) ──────────────────────────────────────────
load_project: Callable[[str], Any] = None  # type: ignore[assignment]
load_projects: Callable[..., Any] = None  # type: ignore[assignment]
DATA_DIR: Path = None  # type: ignore[assignment]
_DATA_ROOT: Path = None  # type: ignore[assignment]
_APP_DIR: Path = None  # type: ignore[assignment]
_POPEN_FLAGS: int = 0
_STARTUPINFO: Any = None
_backfill_token_telemetry: Callable[..., Any] = None  # type: ignore[assignment]
_is_cf_tunneled_request: Callable[..., Any] = None  # type: ignore[assignment]
_kill_pid: Callable[..., Any] = None  # type: ignore[assignment]
_kill_proc_background: Callable[..., Any] = None  # type: ignore[assignment]
_pid_is_alive: Callable[..., Any] = None  # type: ignore[assignment]
_resolve_claude: Callable[..., Any] = None  # type: ignore[assignment]
_stop_session: Callable[..., Any] = None  # type: ignore[assignment]
get_manager: Callable[..., Any] = None  # type: ignore[assignment]
get_manager_for_session: Callable[..., Any] = None  # type: ignore[assignment]


def wire(*, load_project_fn, load_projects_fn, data_dir, data_root, app_dir,
         popen_flags, startupinfo, backfill_token_telemetry_fn, is_cf_tunneled_request_fn, kill_pid_fn, kill_proc_background_fn, pid_is_alive_fn, resolve_claude_fn, stop_session_fn, get_manager_fn, get_manager_for_session_fn):
    """Late-bind projects-family accessors (1.11) + path constants."""
    global load_project, load_projects, DATA_DIR, _DATA_ROOT, _APP_DIR
    load_project = load_project_fn
    load_projects = load_projects_fn
    DATA_DIR = data_dir
    _DATA_ROOT = data_root
    _APP_DIR = app_dir
    global _POPEN_FLAGS, _STARTUPINFO, RESTART_LOG_PATH, SYSTEM_STATUS_PATH
    _POPEN_FLAGS = popen_flags
    _STARTUPINFO = startupinfo
    RESTART_LOG_PATH = data_root / 'data' / 'restart_log.json'
    SYSTEM_STATUS_PATH = data_root / 'data' / 'system_status.json'
    global _backfill_token_telemetry
    _backfill_token_telemetry = backfill_token_telemetry_fn
    global _is_cf_tunneled_request
    _is_cf_tunneled_request = is_cf_tunneled_request_fn
    global _kill_pid
    _kill_pid = kill_pid_fn
    global _kill_proc_background
    _kill_proc_background = kill_proc_background_fn
    global _pid_is_alive
    _pid_is_alive = pid_is_alive_fn
    global _resolve_claude
    _resolve_claude = resolve_claude_fn
    global _stop_session
    _stop_session = stop_session_fn
    global get_manager
    get_manager = get_manager_fn
    global get_manager_for_session
    get_manager_for_session = get_manager_for_session_fn


@bp.route('/api/system/loops')
def system_loops():
    """Background-loop heartbeat ages (mc/obs.py, Phase 2). A loop missing
    from this map after boot, or with a runaway age, is silently dead."""
    return jsonify(obs.snapshot())


# ── Process Tracker endpoints ─────────────────────────────────────────────────

@bp.route('/api/processes')
def list_processes():
    """Return all tracked processes with live status."""
    result = []
    with process_tracker_lock:
        snapshot = list(tracked_processes.items())
    for pid, entry in snapshot:
        proc = entry.get('proc')
        if proc is not None:
            alive = proc.poll() is None
            exit_code = proc.poll()
        else:
            # External process — check via OS
            alive = _pid_is_alive(entry['pid'])
            exit_code = None
        # Cross-reference agent/housekeeping entries to the matching session so the UI
        # can show running/idle/error/stopped distinct from raw process liveness.
        agent_status = None
        entry_type = entry.get('type', '')
        sid = entry.get('session_id', '')
        if sid and entry_type in ('agent', 'housekeeping'):
            session = agent_sessions.get(sid)
            if session:
                agent_status = session.get('status')
        elif sid and entry_type == 'terminal':
            term = terminal_sessions.get(sid)
            if term:
                agent_status = term.get('status')
        result.append({
            'pid': entry['pid'],
            'name': entry['name'],
            'type': entry_type,
            'session_id': sid,
            'project_id': entry['project_id'],
            'project_name': entry['project_name'],
            'command_preview': entry['command_preview'],
            'started_at': entry['started_at'],
            'alive': alive,
            'exit_code': exit_code,
            'agent_status': agent_status,
        })
    result.sort(key=lambda x: (0 if x['alive'] else 1, x.get('started_at', '')))
    return jsonify(result)


@bp.route('/api/processes/<int:pid>/kill', methods=['POST'])
def kill_tracked_process(pid):
    """Kill a specific tracked process by PID."""
    with process_tracker_lock:
        entry = tracked_processes.get(pid)
        if not entry:
            return jsonify({'error': 'process not found in tracker'}), 404
        proc = entry.get('proc')
        if proc:
            if proc.poll() is not None:
                tracked_processes.pop(pid, None)
                return jsonify({'ok': True, 'already_dead': True})
            _kill_pid(pid, tree=True)
            try:
                proc.kill()
            except Exception as e:
                return jsonify({'error': f'kill failed: {e}'}), 500
        else:
            # External process — kill via OS
            if not _kill_pid(pid, tree=True):
                tracked_processes.pop(pid, None)
                return jsonify({'ok': True, 'already_dead': True})
        tracked_processes.pop(pid, None)
        session_id = entry.get('session_id', '')
        entry_type = entry.get('type', '')

    # Update corresponding session status (outside tracker lock)
    if entry_type in ('agent', 'housekeeping'):
        mgr = get_manager_for_session(session_id)
        if mgr is not None:
            with mgr.lock:
                session = agent_sessions.get(session_id)
                if session and session['status'] in ('running', 'idle'):
                    session['status'] = 'stopped'
                    session['last_status_change_time'] = _time.time()
                    session['log_lines'].append('[Process killed via Process Manager]')
                if session and session.get('mode') == 'B':
                    session['process_alive'] = False
    elif entry_type == 'terminal':
        with terminal_lock:
            session = terminal_sessions.get(session_id)
            if session and session['status'] == 'running':
                session['status'] = 'stopped'
                session['output_lines'].append('\r\n[Process killed via Process Manager]')

    return jsonify({'ok': True})


@bp.route('/api/processes/register', methods=['POST'])
def register_external_process():
    """Register an externally-spawned process (e.g. from an agent)."""
    data = request.get_json() or {}
    pid = data.get('pid')
    name = data.get('name', 'External process')
    project_id = data.get('project_id', '')
    command_preview = data.get('command', '')
    if not pid or not isinstance(pid, int):
        return jsonify({'error': 'pid (integer) required'}), 400
    # Verify PID is actually running (warn but still register — process may have exited quickly)
    alive = _pid_is_alive(pid)
    if not alive:
        _log(f"[process-register] Warning: PID {pid} not detected as alive, registering anyway")
    project_name = project_id
    try:
        p = load_project(project_id)
        if p:
            project_name = p.get('name', project_id)
    except Exception:
        pass
    with process_tracker_lock:
        tracked_processes[pid] = {
            'pid': pid,
            'name': name,
            'type': 'external',
            'session_id': '',
            'project_id': project_id,
            'project_name': project_name,
            'command_preview': (command_preview or '')[:80],
            'started_at': now_iso(),
            'proc': None,
        }
    return jsonify({'ok': True, 'pid': pid})


@bp.route('/api/processes/cleanup', methods=['POST'])
def cleanup_processes():
    """Kill all orphaned processes (alive but session gone or completed)."""
    killed = 0
    with process_tracker_lock:
        to_kill = []
        for pid, entry in tracked_processes.items():
            proc = entry.get('proc')
            if not proc or proc.poll() is not None:
                continue
            sid = entry.get('session_id', '')
            orphaned = False
            if entry['type'] in ('agent', 'housekeeping'):
                session = agent_sessions.get(sid)
                if not session or session['status'] not in ('running', 'idle'):
                    orphaned = True
            elif entry['type'] == 'terminal':
                session = terminal_sessions.get(sid)
                if not session or session['status'] != 'running':
                    orphaned = True
            if orphaned:
                to_kill.append((pid, proc))
        for pid, proc in to_kill:
            try:
                proc.kill()
                killed += 1
            except Exception:
                pass
            tracked_processes.pop(pid, None)
    return jsonify({'ok': True, 'killed': killed})




# ── Server restart (remote-triggered, graceful) ──────────────────────────────
# Lets the user restart the Mission Control Flask process from the dashboard
# (including over the clayrune.io tunnel from a phone or remote PC) so they can
# pick up new code/config without needing physical access. Two endpoints:
#   GET  /api/system/restart/status — list active sessions/hiveminds that would
#                                      be killed by a restart (UI shows a warning).
#   POST /api/system/restart        — re-check empty state server-side, then
#                                      stop everything cleanly and re-exec.
# Auth model: same as the rest of the app. Localhost is unauthenticated by
# design (your own machine); tunneled requests have already passed CF Access OTP.
RESTART_LOG_PATH: Path = None  # type: ignore[assignment]  # wired
# state._LAST_RESTART_TIME lives in mc/state.py (1.6).
_RESTART_RATE_LIMIT_SECONDS = 30
# Set once at module load. Changes every time the Python process is replaced,
# so any dashboard polling /api/system/heartbeat can detect a restart by
# comparing this against its cached value.
_SERVER_STARTED_AT = datetime.now(timezone.utc).isoformat()
_SERVER_STARTED_MONOTONIC = _time.time()

# ── System status passive cache ─────────────────────────────────────────────
# Every `claude` session emits a `system/init` message and a `rate_limit_event`
# message at startup. Both contain account-global info: model, CLI version,
# auth source, rate-limit window state, connected MCP servers, etc. — exactly
# the same info CC's own `/status` slash command surfaces. We tap the two
# main stream readers (Mode A + Mode B) so every dispatched agent session
# refreshes this cache for free. Frontend reads it via /api/system/status.
SYSTEM_STATUS_PATH: Path = None  # type: ignore[assignment]  # wired
# state._LAST_SYSTEM_STATUS lives in mc/state.py (1.6).


def _load_system_status_from_disk():
    """Populate `state._LAST_SYSTEM_STATUS` on startup so the panel shows something
    immediately even if no agent has run since the restart."""
    try:
        if SYSTEM_STATUS_PATH.exists():
            state._LAST_SYSTEM_STATUS = json.loads(SYSTEM_STATUS_PATH.read_text(encoding='utf-8'))
    except Exception:
        state._LAST_SYSTEM_STATUS = {}


def _save_system_status_to_disk():
    try:
        SYSTEM_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SYSTEM_STATUS_PATH.write_text(
            json.dumps(state._LAST_SYSTEM_STATUS, indent=2), encoding='utf-8'
        )
    except Exception:
        pass  # Non-fatal — cache stays in memory.


def _capture_system_init(msg):
    """Extract account-global fields from a claude stream-json message and
    refresh the in-memory + on-disk system-status cache.

    Hooked into both `_read_agent_stream` (Mode A) and `_read_agent_stream_b`
    (Mode B) right after `msg = json.loads(line)`. Returns silently for any
    message type we don't care about.

    Handles two message types:
      - `system/init` — model, version, auth, MCP servers, tool/skill/plugin counts.
      - `rate_limit_event` — 5-hour or 1-hour rate-limit window state.
    """
    try:
        mtype = msg.get('type', '')
        now_iso = datetime.now(timezone.utc).isoformat()
        if mtype == 'system' and msg.get('subtype') == 'init':
            mcp = msg.get('mcp_servers') or []
            # `memory_paths` is a dict (`{"auto": "..."}`) — collapse to a list
            # of paths for display so the panel doesn't need to know the shape.
            mp_raw = msg.get('memory_paths') or {}
            if isinstance(mp_raw, dict):
                mp_list = [p for p in mp_raw.values() if isinstance(p, str) and p]
            elif isinstance(mp_raw, list):
                mp_list = [p for p in mp_raw if isinstance(p, str) and p]
            else:
                mp_list = []
            init_data = {
                'model': msg.get('model') or '',
                'claude_code_version': msg.get('claude_code_version') or '',
                'apiKeySource': msg.get('apiKeySource') or '',
                'permissionMode': msg.get('permissionMode') or '',
                'mcp_servers': [
                    {'name': m.get('name', ''), 'status': m.get('status', 'unknown')}
                    for m in mcp if isinstance(m, dict)
                ],
                'tools_count': len(msg.get('tools') or []),
                'skills_count': len(msg.get('skills') or []),
                'agents_count': len(msg.get('agents') or []),
                'plugins_count': len(msg.get('plugins') or []),
                'slash_commands_count': len(msg.get('slash_commands') or []),
                'output_style': msg.get('output_style') or '',
                'fast_mode_state': msg.get('fast_mode_state') or '',
                'analytics_disabled': bool(msg.get('analytics_disabled')),
                'cwd': msg.get('cwd') or '',
                'memory_paths': mp_list,
            }
            state._LAST_SYSTEM_STATUS.update(init_data)
            state._LAST_SYSTEM_STATUS['init_captured_at'] = now_iso
            state._LAST_SYSTEM_STATUS['captured_at'] = now_iso
            _save_system_status_to_disk()
        elif mtype == 'rate_limit_event':
            info = msg.get('rate_limit_info') or {}
            if isinstance(info, dict):
                state._LAST_SYSTEM_STATUS['rate_limit_info'] = {
                    'status': info.get('status', ''),
                    'resetsAt': info.get('resetsAt'),
                    'rateLimitType': info.get('rateLimitType', ''),
                    'overageStatus': info.get('overageStatus', ''),
                    'overageResetsAt': info.get('overageResetsAt'),
                    'isUsingOverage': bool(info.get('isUsingOverage')),
                }
                state._LAST_SYSTEM_STATUS['rate_limit_captured_at'] = now_iso
                state._LAST_SYSTEM_STATUS['captured_at'] = now_iso
                _save_system_status_to_disk()
    except Exception:
        pass  # Capture is best-effort; never break the reader on a parse error.


_load_system_status_from_disk()


@bp.route('/api/system/heartbeat')
def system_heartbeat():
    """Tiny endpoint dashboards poll to detect that the server has restarted.

    Cheap to call (no DB / disk read). The frontend caches `started_at` from
    its first response and reloads the page if a later response shows a
    different value — that means the Python process has been replaced (e.g.
    by /api/system/restart) and any in-memory session state the dashboard
    was tracking is stale.
    """
    return jsonify({
        'started_at': _SERVER_STARTED_AT,
        'pid': os.getpid(),
        'uptime_seconds': int(_time.time() - _SERVER_STARTED_MONOTONIC),
    })


def _build_system_status_payload():
    """Shape the cached system-status dict for /api/system/status responses.

    Returns the cache as-is plus a `cache_age_seconds` field computed from
    `captured_at`, so the frontend can render "stale" without re-parsing the
    timestamp. Returns an empty `{captured_at: null}` shape if the cache is
    still empty (no agent has run since first install / cache file deletion).
    """
    payload = dict(state._LAST_SYSTEM_STATUS)
    cap = payload.get('captured_at')
    age = None
    if cap:
        try:
            dt = datetime.fromisoformat(cap.replace('Z', '+00:00'))
            age = int((datetime.now(timezone.utc) - dt).total_seconds())
        except Exception:
            age = None
    payload['cache_age_seconds'] = age
    return payload


@bp.route('/api/system/status', methods=['GET'])
def system_status_get():
    """Return the cached system status (model, version, rate limit, MCP, etc.).

    Read-only and cheap — just serializes the in-memory dict. Cache is
    populated by both stream readers as agents run; falls back to disk after
    a restart via `_load_system_status_from_disk()` at module load.
    """
    return jsonify(_build_system_status_payload())


def _mc_usage_from_agent_logs():
    """Aggregate token usage from MC's own agent_log files.

    Returns {'today': {model: tokens}, 'week': {...}, 'month': {...},
             'all_time': {model: tokens}, 'last_data_date': str}
    Reads all *_agent_log.json in DATA_DIR. Entries without model_tokens are
    skipped (pre-telemetry entries). Never raises.

    Deduplicates by claude_session_id: Scribe checkpoints write multiple entries
    for the same session (each with the cumulative token total from session start).
    We keep only the latest entry per csid to avoid counting the same tokens N times.
    Sessions without a csid are counted individually (legacy / non-CC providers).
    """
    today_str = datetime.now().strftime('%Y-%m-%d')
    try:
        week_cutoff  = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
        month_cutoff = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')
    except Exception:
        week_cutoff = month_cutoff = today_str

    today_t, week_t, month_t, all_t = {}, {}, {}, {}
    last_data_date = ''

    try:
        # First pass: collect all entries across all log files, deduplicated by csid.
        # For each csid, keep only the latest entry (highest ts = most complete snapshot).
        # Entries without a csid are kept as-is (keyed by a unique fallback).
        best_by_csid: dict = {}  # csid -> entry dict
        _no_csid_counter = 0
        for log_path in DATA_DIR.glob('*_agent_log.json'):
            try:
                entries = json.loads(log_path.read_text(encoding='utf-8',
                                                        errors='replace'))
            except Exception:
                continue
            if not isinstance(entries, list):
                continue
            for e in entries:
                if not isinstance(e, dict):
                    continue
                mt = e.get('model_tokens')
                if not mt or not isinstance(mt, dict):
                    continue
                ts = (e.get('ts') or '')[:10]
                if not ts:
                    continue
                csid = e.get('claude_session_id') or ''
                if csid:
                    prev = best_by_csid.get(csid)
                    if prev is None or ts >= (prev.get('ts') or '')[:10]:
                        best_by_csid[csid] = e
                else:
                    # No csid — count individually (non-CC provider or legacy entry)
                    _no_csid_counter += 1
                    best_by_csid[f'__no_csid_{_no_csid_counter}'] = e

        for e in best_by_csid.values():
            mt = e.get('model_tokens') or {}
            ts = (e.get('ts') or '')[:10]
            if ts > last_data_date:
                last_data_date = ts
            for model, tok in mt.items():
                tok = int(tok or 0)
                if not tok:
                    continue
                all_t[model] = int(all_t.get(model, 0)) + tok
                if ts >= month_cutoff:
                    month_t[model] = int(month_t.get(model, 0)) + tok
                if ts >= week_cutoff:
                    week_t[model] = int(week_t.get(model, 0)) + tok
                if ts == today_str:
                    today_t[model] = int(today_t.get(model, 0)) + tok
    except Exception:
        pass

    return {
        'today': today_t,
        'week': week_t,
        'month': month_t,
        'all_time': all_t,
        'last_data_date': last_data_date,
    }


@bp.route('/api/system/usage/backfill', methods=['POST'])
def system_usage_backfill():
    """Trigger a one-shot telemetry backfill in the background.
    Populates model_tokens on existing agent_log entries from JSONL transcripts.
    """
    def _run():
        try:
            _backfill_token_telemetry()
        except Exception as e:
            _log(f"[telemetry-backfill] endpoint trigger failed: {e}")
    threading.Thread(target=_run, daemon=True).start()
    return jsonify({'ok': True, 'msg': 'backfill started in background'})


# Authoritative subscription usage windows (5h / 7d / per-model %) come from
# Claude Code's own OAuth token hitting Anthropic's undocumented usage endpoint
# — the same call the CLI `/usage` command makes. No client-readable file or
# `--print` flag exposes these percentages, so this is the only programmatic
# source. Best-effort: any failure (missing/expired token, network, 401)
# returns None and the UI falls back to the header-derived rate-limit window.
# Cached briefly to avoid hammering the endpoint; the User-Agent MUST start with
# `claude-code/` or Anthropic routes the request to an aggressively throttled
# bucket (persistent 429s).
_OAUTH_USAGE_TTL = 60.0  # seconds
_oauth_usage_cache: dict = {'ts': 0.0, 'data': None}


def _fetch_oauth_usage_limits():
    """Return the parsed OAuth usage windows dict, or None on any failure.

    Shape: {five_hour, seven_day, seven_day_opus, seven_day_sonnet, extra_usage}
    where each window is {utilization: 0-100, resets_at: ISO8601} (per-model
    blocks are null when unused).
    """
    now = _time.time()
    cached = _oauth_usage_cache.get('data')
    if cached is not None and (now - _oauth_usage_cache.get('ts', 0.0)) < _OAUTH_USAGE_TTL:
        return cached
    try:
        cred_path = Path.home() / '.claude' / '.credentials.json'
        creds = json.loads(cred_path.read_text(encoding='utf-8'))
        oauth = creds.get('claudeAiOauth') or {}
        token = oauth.get('accessToken')
        if not token:
            return None
        ver = state._LAST_SYSTEM_STATUS.get('claude_code_version') or '2.0.0'
        req = urllib.request.Request(
            'https://api.anthropic.com/api/oauth/usage',
            headers={
                'Authorization': f'Bearer {token}',
                'anthropic-beta': 'oauth-2025-04-20',
                'User-Agent': f'claude-code/{ver}',
                'Content-Type': 'application/json',
            },
            method='GET',
        )
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        if isinstance(data, dict):
            _oauth_usage_cache['ts'] = now
            _oauth_usage_cache['data'] = data
            return data
        return None
    except Exception as e:
        _log(f"[system_usage] oauth usage fetch failed: {e}", flush=True)
        return None


@bp.route('/api/system/usage', methods=['GET'])
def system_usage_get():
    """Return local token-usage aggregates derived from ~/.claude/stats-cache.json.

    This is the file Claude Code maintains itself: a per-day breakdown of
    tokens by model + cumulative per-model totals. The CLI's interactive
    `/status` Usage tab shows server-side rate-limit *percentages* (5h
    window, weekly all-model, weekly Sonnet-only) that are NOT exposed via
    any client-readable file or `--print` invocation — those come from
    Anthropic's billing service. We surface what we CAN see locally:

      - today's tokens by model
      - last 7-day tokens by model
      - all-time top models
      - totalSessions / totalMessages
      - lastComputedDate (so the user knows when the cache last ticked)

    Plus the rate-limit reset time from the existing system-status cache.
    Frontend ties this off with a "see canonical usage" link to
    https://claude.ai/settings/usage.
    """
    # MC's own agent_log telemetry — primary source for period buckets.
    mc = _mc_usage_from_agent_logs()

    # CC stats-cache — used for all-time top_models and totalSessions/Messages
    # fallback. May be stale (only updates during interactive CC use).
    cc_data = {}
    cc_available = False
    try:
        cc_path = Path.home() / '.claude' / 'stats-cache.json'
        if cc_path.exists():
            cc_data = json.loads(cc_path.read_text(encoding='utf-8'))
            cc_available = True
    except Exception:
        pass

    # All-time top models: prefer MC aggregated if it has data, fall back to CC.
    mc_all = mc.get('all_time', {})
    if mc_all:
        ranked = sorted(mc_all.items(), key=lambda x: x[1], reverse=True)[:5]
        top_models = [{'model': m, 'tokens': t, 'cache_read': 0}
                      for m, t in ranked]
    else:
        model_usage = cc_data.get('modelUsage') or {}
        top_models = []
        if isinstance(model_usage, dict):
            ranked = []
            for m, mu in model_usage.items():
                if not isinstance(mu, dict):
                    continue
                total = int(mu.get('inputTokens') or 0) + int(mu.get('outputTokens') or 0)
                ranked.append((m, total, int(mu.get('cacheReadInputTokens') or 0)))
            ranked.sort(key=lambda x: x[1], reverse=True)
            for m, total, cache in ranked[:5]:
                top_models.append({'model': m, 'tokens': total, 'cache_read': cache})

    last_data_date = mc.get('last_data_date', '') or cc_data.get('lastComputedDate', '')

    return jsonify({
        'available': True,
        'today': mc.get('today', {}),
        'week': mc.get('week', {}),
        'month': mc.get('month', {}),
        'top_models': top_models,
        'total_sessions': int(cc_data.get('totalSessions') or 0),
        'total_messages': int(cc_data.get('totalMessages') or 0),
        'last_computed_date': cc_data.get('lastComputedDate') or '',
        'last_data_date': last_data_date,
        'rate_limit_info': state._LAST_SYSTEM_STATUS.get('rate_limit_info') or {},
        # Authoritative subscription usage windows (% + resets) from the OAuth
        # endpoint; None when unavailable (UI falls back to rate_limit_info).
        'usage_limits': _fetch_oauth_usage_limits(),
    })


@bp.route('/api/system/status/refresh', methods=['POST'])
def system_status_refresh():
    """Active refresh: spawn a minimal claude session purely to read its init
    message + rate-limit event, then return the freshly-updated cache.

    Costs roughly $0.001 (one tiny prompt, one tiny reply). Use sparingly:
    the cache auto-refreshes from any real agent activity, so this is only
    needed when the user wants live data after a long idle period.
    """
    try:
        # `--max-turns 1` with a one-word prompt is the cheapest valid call
        # that still emits the system/init + rate_limit_event we care about.
        # `--tools "" --strict-mcp-config --mcp-config {"mcpServers":{}}` is
        # NOT applied here — we WANT the full tool/MCP roster in the init so
        # the panel reflects the user's real environment, not a sandboxed
        # subset (which is why we don't reuse Claydo's flags).
        cmd = [_resolve_claude(),
               '--max-turns', '1',
               '--print', '--verbose',
               '--input-format', 'stream-json',
               '--output-format', 'stream-json']
        stdin_msg = json.dumps({
            'type': 'user',
            'message': {'role': 'user', 'content': 'ok'},
        }) + '\n'
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            input=stdin_msg,
            timeout=30, encoding='utf-8', errors='replace',
            creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
        )
        for line in (proc.stdout or '').splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            _capture_system_init(obj)
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'refresh timed out (>30s)',
                        'status': _build_system_status_payload()}), 504
    except FileNotFoundError:
        return jsonify({'error': 'Claude CLI not found on this server',
                        'status': _build_system_status_payload()}), 500
    except Exception as e:
        return jsonify({'error': str(e),
                        'status': _build_system_status_payload()}), 500
    return jsonify(_build_system_status_payload())


def _get_active_restart_blockers():
    """Snapshot of sessions/hiveminds that would be killed if we restarted now.

    "Active" = a live agent turn (status='running') or an active hivemind
    orchestrator. Idle/completed/error/stopped sessions are NOT blockers — their
    process is either dead or just waiting on stdin and is safe to drop.
    """
    # Defensive: never let a stray/malformed file in DATA_DIR (no 'id') crash
    # the restart path — it shares this helper with the GET status endpoint.
    project_names = {p['id']: p.get('name', p['id'])
                     for p in load_projects() if isinstance(p, dict) and p.get('id')}
    active_sessions = []
    for sid, sess in list(agent_sessions.items()):
        if sess.get('status') != 'running':
            continue
        pid = sess.get('project_id', '')
        task = (sess.get('task') or '').strip()
        active_sessions.append({
            'session_id': sid,
            'project_id': pid,
            'project_name': project_names.get(pid, pid),
            'status': sess.get('status'),
            'task_preview': (task[:80] + '…') if len(task) > 80 else task,
            'started_at': sess.get('started_at'),
        })
    active_hiveminds = []
    with _hivemind_lock:
        for hm_id, hm in list(_hivemind_sessions.items()):
            if hm.get('status') != 'active':
                continue
            workers = hm.get('worker_sessions', []) or []
            active_hiveminds.append({
                'hivemind_id': hm_id,
                'project_id': hm.get('project_id', ''),
                'project_name': project_names.get(hm.get('project_id', ''), hm.get('project_id', '')),
                'title': hm.get('title') or hm.get('goal', '')[:80],
                'workers_running': len(workers),
            })
    return {'active_sessions': active_sessions, 'active_hiveminds': active_hiveminds}


def _append_restart_log(entry):
    try:
        log = []
        if RESTART_LOG_PATH.exists():
            try:
                log = json.loads(RESTART_LOG_PATH.read_text(encoding='utf-8'))
            except Exception:
                log = []
        log.append(entry)
        # Keep last 200 entries to bound the file
        if len(log) > 200:
            log = log[-200:]
        RESTART_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        RESTART_LOG_PATH.write_text(json.dumps(log, indent=2), encoding='utf-8')
    except Exception as e:
        _log(f"[restart] failed to append log: {e}")


def _stop_all_sessions_for_restart(grace_seconds=3.0):
    """Best-effort graceful stop of every tracked session before re-exec.

    Iterates agent_sessions, sends graceful stop (Mode B closes stdin; both modes
    schedule a background kill of the proc tree). Then waits up to grace_seconds
    for processes to exit before letting the re-exec orphan/kill the rest.
    """
    procs = []
    for sid, sess in list(agent_sessions.items()):
        try:
            mgr = get_manager_for_session(sid)
            if mgr is None:
                # Fall back to a per-project lookup; if still not found, just touch the dict directly.
                pid = sess.get('project_id', '')
                mgr = get_manager(pid) if pid else None
            if mgr is not None:
                with mgr.lock:
                    if sess.get('status') in ('running', 'idle', 'error'):
                        proc = _stop_session(sess, sid)
                        if proc is not None:
                            procs.append(proc)
            else:
                # No manager — direct stop without lock as a last resort.
                if sess.get('status') in ('running', 'idle', 'error'):
                    proc = _stop_session(sess, sid)
                    if proc is not None:
                        procs.append(proc)
        except Exception as e:
            _log(f"[restart] graceful stop failed for {sid}: {e}")

    # Schedule background kills (existing helper handles tree-kill + wait).
    for proc in procs:
        _kill_proc_background(proc)

    # Stop the Cloudflare tunnel too. It's spawned outside the agent-session
    # tracker, so without this every restart/shutdown orphans cloudflared.exe
    # (observed: 29 leaked connectors accumulated across prior restarts).
    # Best-effort + bounded; a missing/disabled remote-access build just no-ops.
    # [leak fix 2026-06-03]
    try:
        from mc_remote import tunnel_supervisor as _tunnel_sup
        _tunnel_sup.get().stop(timeout=3.0)
    except Exception as e:
        try: _log(f"[restart] tunnel stop skipped: {e}")
        except Exception: pass

    # Brief wait so the children get a chance to die before exec replaces us.
    deadline = _time.time() + grace_seconds
    while _time.time() < deadline:
        alive = [p for p in procs if p.poll() is None]
        if not alive:
            break
        _time.sleep(0.1)


def _has_visible_console() -> bool:
    """Windows: True only if this process owns a VISIBLE console window.

    A windowless (start-hidden.vbs) launch owns a HIDDEN console — GetConsoleWindow
    returns a handle but IsWindowVisible is false — so this returns False and the
    restart path keeps the new instance windowless instead of popping a console.
    Fail-safe returns True (preserve the prior new-console behaviour) on any error
    or on non-Windows."""
    if sys.platform != 'win32':
        return False
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if not hwnd:
            return False
        return bool(ctypes.windll.user32.IsWindowVisible(hwnd))
    except Exception:
        return True


def _perform_server_restart_async(audit_entry):
    """Run after the HTTP response flushes: stop everything, then re-exec.

    Re-exec replaces the current Python process in place. Same PID, fresh
    interpreter — picks up code changes on disk. Open SSE streams drop, the
    frontend's polling overlay reconnects when /api/projects starts answering
    again, and the localStorage open-modals snapshot restores the conversation
    layout.

    Hardening (2026-05-27): if `_stop_all_sessions_for_restart` deadlocks (e.g.
    on an SSE-held mgr.lock held by the very session that triggered the
    restart), the original implementation hung forever and never re-exec'd.
    The UI's "any 200 = back" poll then declared false success against the
    old process. Three guards: (a) spawn the new process FIRST so progress is
    made before any potentially-blocking work, (b) bound the graceful stop in
    its own thread with a hard timeout, (c) start a hard watchdog that forces
    os._exit(2) past an absolute deadline no matter what.
    """
    def _do_restart():
        # Watchdog: under any circumstance, terminate within 10s of being
        # asked to restart. Daemon thread won't be joined; os._exit is a hard
        # SIGKILL-equivalent that bypasses atexit hooks but that's the point.
        def _watchdog():
            _time.sleep(10.0)
            try: _log("[restart] watchdog tripped — forcing termination")
            except Exception: pass
            os._exit(2)
        threading.Thread(target=_watchdog, daemon=True).start()

        _time.sleep(0.4)  # let the HTTP 202 actually reach the client

        # (1) Spawn the new instance FIRST. Even if everything below hangs,
        # the user already has a fresh server starting up. The new instance's
        # port-conflict bypass will wait for the old socket to free.
        spawned = False
        new_env = os.environ.copy()
        new_env['MC_RESTART_FROM_PID'] = str(os.getpid())
        try:
            popen_kwargs = {
                'env': new_env,
                'cwd': os.getcwd(),
                'close_fds': True,
            }
            if sys.platform == 'win32':
                # CREATE_NEW_PROCESS_GROUP so Ctrl-C in the old terminal doesn't
                # propagate to the fresh instance. For the console flag we branch:
                #   - Windowless launch (end users, via start-hidden.vbs): the
                #     process owns a HIDDEN console, so CREATE_NEW_CONSOLE would
                #     pop a visible python.exe log window on EVERY restart / self-
                #     update. Stay windowless (CREATE_NO_WINDOW) and redirect the
                #     new instance's output to the same log start.bat uses.
                #   - Dev launch from a real terminal: keep CREATE_NEW_CONSOLE so
                #     the restarted server stays visible (matches expectation).
                _windowless = (os.environ.get('CLAYRUNE_HIDDEN') == '1'
                               or not _has_visible_console())
                popen_kwargs['creationflags'] = (
                    subprocess.CREATE_NEW_PROCESS_GROUP
                    | (subprocess.CREATE_NO_WINDOW if _windowless
                       else subprocess.CREATE_NEW_CONSOLE)
                )
                if _windowless:
                    # No console to print into — persist logs like the VBS path.
                    try:
                        log_path = os.path.join(os.getcwd(), 'data', 'logs', 'clayrune.log')
                        os.makedirs(os.path.dirname(log_path), exist_ok=True)
                        _restart_log = open(log_path, 'ab')  # leaked intentionally; we os._exit shortly
                        popen_kwargs['stdout'] = _restart_log
                        popen_kwargs['stderr'] = subprocess.STDOUT
                    except Exception as e:
                        _log(f"[restart] windowless log redirect failed: {e}")
            else:
                popen_kwargs['start_new_session'] = True
            subprocess.Popen([sys.executable] + sys.argv, **popen_kwargs)
            spawned = True
            _log("[restart] spawned new server process")
        except Exception as e:
            _log(f"[restart] failed to spawn new instance: {e}")

        # (2) Best-effort graceful stop, bounded by a wall-clock timeout.
        # Run in its own thread so a deadlock cannot prevent the os._exit
        # below. Whether or not it finishes, we proceed.
        stop_done = threading.Event()
        def _bounded_stop():
            try: _stop_all_sessions_for_restart()
            except Exception as e:
                try: _log(f"[restart] stop-all failed: {e}")
                except Exception: pass
            finally:
                stop_done.set()
        threading.Thread(target=_bounded_stop, daemon=True).start()
        stop_done.wait(timeout=4.0)
        if not stop_done.is_set():
            try: _log("[restart] stop-all exceeded 4s — proceeding to exit anyway")
            except Exception: pass

        # (3) Audit log + exit. Log write is best-effort.
        try: _append_restart_log(audit_entry)
        except Exception: pass

        # Brief settle so the new process can claim the port if the OS is
        # quick about it; the new instance is allowed to wait longer.
        _time.sleep(0.25)
        try: _log(f"[restart] exiting old process (spawned={spawned})")
        except Exception: pass
        os._exit(0 if spawned else 1)

    threading.Thread(target=_do_restart, daemon=True).start()


def _perform_server_shutdown_async(audit_entry):
    """Run after the HTTP response flushes: stop everything, then exit for good.

    The power-off analog of _perform_server_restart_async — same bounded
    graceful-stop + hard watchdog, but it does NOT spawn a replacement
    process. The dashboard shows a terminal "powered off" overlay; the user
    relaunches via the Clayrune shortcut.
    """
    def _do_shutdown():
        # Hard watchdog: terminate within 10s no matter what (mirrors restart).
        def _watchdog():
            _time.sleep(10.0)
            try: _log("[shutdown] watchdog tripped — forcing termination")
            except Exception: pass
            os._exit(0)
        threading.Thread(target=_watchdog, daemon=True).start()

        _time.sleep(0.4)  # let the HTTP 202 actually reach the client

        # Best-effort graceful stop, bounded by a wall-clock timeout and run in
        # its own thread so a deadlock cannot prevent the os._exit below.
        stop_done = threading.Event()
        def _bounded_stop():
            try: _stop_all_sessions_for_restart()
            except Exception as e:
                try: _log(f"[shutdown] stop-all failed: {e}")
                except Exception: pass
            finally:
                stop_done.set()
        threading.Thread(target=_bounded_stop, daemon=True).start()
        stop_done.wait(timeout=4.0)
        if not stop_done.is_set():
            try: _log("[shutdown] stop-all exceeded 4s — exiting anyway")
            except Exception: pass

        try: _append_restart_log(audit_entry)
        except Exception: pass

        try: _log("[shutdown] exiting — powered off by user request")
        except Exception: pass
        os._exit(0)

    threading.Thread(target=_do_shutdown, daemon=True).start()


@bp.route('/api/system/restart/status')
def system_restart_status():
    """Return what's currently active so the UI can warn before restarting."""
    return jsonify(_get_active_restart_blockers())


# ── Update Clayrune (git pull from inside the dashboard) ───────────────────

# "Is the working tree dirty enough that updating would destroy the user's
# work?" — asked before every update, and used for the UI's update-available
# badge.
#
# `-uno` (don't list untracked files) is LOAD-BEARING. Clayrune's own user data
# lives INSIDE the checkout (data/projects/, config.json, data/logs/, .venv/),
# and so does anything the user happens to drop in the install dir. Plain
# `--porcelain` reports all of that as "local changes", so a single stray
# untracked file made this endpoint answer 409 forever and set
# update_available=False — the install could never update again, with no
# indication why. Untracked files are user data, not edits to our source; the
# question here is only about MODIFIED TRACKED files.
#
# Narrow accepted trade-off: if the user parks a file at a path a future commit
# also adds, the update overwrites it. That beats never updating at all.
_DIRTY_TREE_ARGS = ['status', '--porcelain', '-uno']


def _git(args, cwd, timeout=30):
    """Run git with the given args in cwd. Returns (returncode, stdout+stderr).

    Hardened against the most common hang on Windows: Git Credential Manager
    (GCM) popping a hidden auth dialog (we use STARTF_USESHOWWINDOW=SW_HIDE,
    so the dialog never appears, but git waits for it forever until our
    timeout). GIT_TERMINAL_PROMPT=0 + GCM_INTERACTIVE=Never make git fail
    fast instead of prompting — for a public repo no auth is needed anyway.
    """
    env = os.environ.copy()
    env['GIT_TERMINAL_PROMPT'] = '0'
    env['GCM_INTERACTIVE'] = 'Never'
    try:
        r = subprocess.run(
            ['git', *args],
            cwd=str(cwd),
            capture_output=True, text=True,
            encoding='utf-8', errors='replace',
            timeout=timeout,
            creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
            env=env,
        )
        out = (r.stdout or '') + (r.stderr or '')
        return r.returncode, out.strip()
    except FileNotFoundError:
        return -1, 'git not found on PATH'
    except subprocess.TimeoutExpired:
        return -2, f'git {args[0]} timed out'
    except Exception as e:
        return -3, str(e)


def _git_version(repo_root, committish):
    """Synthetic build number from the nearest `v*` semver tag.

    `git describe --tags --match v*` yields one of:
      - "v1.5.1"                 → exactly on a release tag
      - "v1.5.1-180-gc6d2fae"    → 180 commits past v1.5.1
      - "<sha>" (--always)       → no v* tag reachable (fresh clone / shallow)

    Returns {'display', 'base', 'build', 'sha'}. `display` is the
    human string the UI shows; the rest are structured for callers that
    want to compare without re-parsing.
    """
    import re
    rc, out = _git(
        ['describe', '--tags', '--match', 'v*', '--always', '--abbrev=7', committish],
        repo_root,
    )
    if rc != 0 or not out:
        return {'display': 'unknown', 'base': '', 'build': 0, 'sha': ''}
    m = re.match(r'^(v[0-9][0-9.]*)-(\d+)-g([0-9a-f]+)$', out)
    if m:
        base, build, sha = m.group(1), int(m.group(2)), m.group(3)
        return {'display': f'{base} build {build}', 'base': base,
                'build': build, 'sha': sha}
    if re.match(r'^v[0-9][0-9.]*$', out):
        return {'display': out, 'base': out, 'build': 0, 'sha': ''}
    # --always fallback: no reachable v* tag, `out` is a bare short SHA.
    return {'display': f'untagged ({out})', 'base': '', 'build': 0, 'sha': out}


@bp.route('/api/system/update/status')
def system_update_status():
    """Report whether the install dir is a git repo, current commit + branch,
    and how far behind origin master we are. The Settings UI uses this to
    show a "X commits behind" badge.
    """
    repo_root = _APP_DIR  # repo root in dev, app dir frozen; __file__ here is mc/blueprints/ — not the checkout
    if not (repo_root / '.git').exists():
        return jsonify({
            'is_git_repo': False,
            'message': 'Install directory is not a git checkout — automatic updates not available.',
        })

    rc, sha = _git(['rev-parse', '--short', 'HEAD'], repo_root)
    current_commit = sha if rc == 0 else 'unknown'
    rc, branch = _git(['rev-parse', '--abbrev-ref', 'HEAD'], repo_root)
    current_branch = branch if rc == 0 else 'unknown'

    # Fetch silently to learn what's on the remote. Tighter timeout (12s)
    # so the Settings UI doesn't sit on "Checking for updates..." for half a
    # minute when the network is slow or git's credential helper is
    # misbehaving. If fetch fails, we still report local-tip behind=0 below
    # rather than blocking the whole status response.
    _git(['fetch', '--quiet', 'origin'], repo_root, timeout=12)
    rc, ahead_behind = _git(
        ['rev-list', '--left-right', '--count', f'origin/{current_branch}...HEAD'],
        repo_root,
    )
    behind = 0
    ahead = 0
    if rc == 0 and ahead_behind:
        try:
            behind, ahead = (int(x) for x in ahead_behind.split())
        except Exception:
            pass

    # Detect dirty working tree (uncommitted changes that would block pull).
    rc, status_out = _git(_DIRTY_TREE_ARGS, repo_root)
    has_local_changes = bool(status_out)

    # Remote tip SHA + commit dates, so the UI can show "installed X (date) →
    # latest Y (date)" instead of just an opaque behind-count.
    rc, remote_sha = _git(['rev-parse', '--short', f'origin/{current_branch}'], repo_root)
    remote_commit = remote_sha if rc == 0 else ''
    rc, ld = _git(['log', '-1', '--format=%cs', 'HEAD'], repo_root)
    local_commit_date = ld if rc == 0 else ''
    rc, rd = _git(['log', '-1', '--format=%cs', f'origin/{current_branch}'], repo_root)
    remote_commit_date = rd if rc == 0 else ''

    local_ver = _git_version(repo_root, 'HEAD')
    remote_ver = _git_version(repo_root, f'origin/{current_branch}')

    return jsonify({
        'is_git_repo': True,
        'install_dir': str(repo_root),
        'branch': current_branch,
        'commit': current_commit,
        'commit_date': local_commit_date,
        'version': local_ver['display'],
        'remote_commit': remote_commit,
        'remote_commit_date': remote_commit_date,
        'remote_version': remote_ver['display'],
        'behind': behind,
        'ahead': ahead,
        'has_local_changes': has_local_changes,
        'update_available': behind > 0 and not has_local_changes and ahead == 0,
    })


# ── Background update-check daemon ──────────────────────────────────────────
# Runs `git fetch` every 6h and caches the answer. Lets the dashboard show a
# passive "update available" badge without doing a 12-second git operation on
# every page load. Settings -> Update Clayrune still does a fresh fetch via
# /api/system/update/status when the user actively asks.

# _UPDATE_CHECK_LOCK / _UPDATE_CHECK_CACHE / _UPDATE_CHECK_INTERVAL_S /
# _UPDATE_CHECK_BOOT_DELAY_S moved to mc/state.py (Phase 0).


def _refresh_update_cache():
    """Run git fetch + recompute the update status, store in
    _UPDATE_CHECK_CACHE. Idempotent; safe to call from any thread."""
    repo_root = _APP_DIR  # repo root in dev, app dir frozen; __file__ here is mc/blueprints/ — not the checkout
    if not (repo_root / '.git').exists():
        with _UPDATE_CHECK_LOCK:
            _UPDATE_CHECK_CACHE.update({
                'last_check_ts': _time.time(),
                'is_git_repo': False,
            })
        return

    rc, sha = _git(['rev-parse', '--short', 'HEAD'], repo_root)
    current_commit = sha if rc == 0 else 'unknown'
    rc, branch = _git(['rev-parse', '--abbrev-ref', 'HEAD'], repo_root)
    current_branch = branch if rc == 0 else 'unknown'

    _git(['fetch', '--quiet', 'origin'], repo_root, timeout=12)
    rc, ahead_behind = _git(
        ['rev-list', '--left-right', '--count', f'origin/{current_branch}...HEAD'],
        repo_root,
    )
    behind = ahead = 0
    if rc == 0 and ahead_behind:
        try:
            behind, ahead = (int(x) for x in ahead_behind.split())
        except Exception:
            pass

    rc, status_out = _git(_DIRTY_TREE_ARGS, repo_root)
    has_local_changes = bool(status_out)

    rc, remote_sha = _git(['rev-parse', '--short', f'origin/{current_branch}'], repo_root)
    remote_commit = remote_sha if rc == 0 else ''

    rc, log_out = _git(
        ['log', f'HEAD..origin/{current_branch}', '-5', '--pretty=format:%h %s'],
        repo_root,
    )
    recent_log = log_out if rc == 0 else ''

    local_ver = _git_version(repo_root, 'HEAD')
    remote_ver = _git_version(repo_root, f'origin/{current_branch}')

    with _UPDATE_CHECK_LOCK:
        _UPDATE_CHECK_CACHE.update({
            'last_check_ts': _time.time(),
            'is_git_repo': True,
            'branch': current_branch,
            'commit': current_commit,
            'version': local_ver['display'],
            'remote_version': remote_ver['display'],
            'remote_commit': remote_commit,
            'behind': behind,
            'ahead': ahead,
            'has_local_changes': has_local_changes,
            'update_available': behind > 0 and not has_local_changes and ahead == 0,
            'recent_log': recent_log,
        })


def _update_check_loop():
    """Daemon thread: refresh the update cache every _UPDATE_CHECK_INTERVAL_S
    seconds. First check fires after _UPDATE_CHECK_BOOT_DELAY_S so we don't
    fight server startup."""
    _time.sleep(_UPDATE_CHECK_BOOT_DELAY_S)
    while True:
        obs.heartbeat('update-check')  # Phase 2: loop liveness -> /api/system/loops
        try:
            _refresh_update_cache()
        except Exception as e:
            _log(f"[update-check] loop error: {e}", flush=True)
        _time.sleep(_UPDATE_CHECK_INTERVAL_S)


@bp.route('/api/system/update/cached')
def system_update_cached():
    """Cheap snapshot of the update cache. No git operations -- just reads
    memory. Frontend polls this on dashboard load to decide whether to show
    the "update available" badge / toast.

    For a fresh fetch (manual "Check now" path), use /api/system/update/status.
    """
    with _UPDATE_CHECK_LOCK:
        snap = dict(_UPDATE_CHECK_CACHE)
    snap['stale_seconds'] = int(_time.time() - snap['last_check_ts']) if snap['last_check_ts'] else None
    return jsonify(snap)


@bp.route('/api/system/update', methods=['POST'])
def system_update():
    """Update the install dir to the remote tip. The Settings UI calls this
    after the user confirms. Returns the git output so the user sees what
    changed. Does NOT auto-restart — the UI prompts the user separately.

    LOAD-BEARING: `git pull --ff-only` is tried first, but it is NOT sufficient
    on its own. When the release branch is force-pushed upstream, ff-only
    aborts ("fatal: Not possible to fast-forward, aborting") and this endpoint —
    the ONLY update channel most users have — fails forever, silently. So we
    fall back to `fetch` + `reset --hard origin/<branch>`.

    Safe because: (a) we already refused above if the working tree is dirty, and
    (b) `reset --hard` rewrites TRACKED files only. All user data lives in
    untracked/gitignored paths (data/projects/, data/settings.json, config.json,
    data/logs/, .venv/) and is untouched. NEVER add `git clean` here — that
    WOULD delete it.
    """
    repo_root = _APP_DIR  # repo root in dev, app dir frozen; __file__ here is mc/blueprints/ — not the checkout
    if not (repo_root / '.git').exists():
        return jsonify({'error': 'install dir is not a git checkout'}), 400

    rc, status_out = _git(_DIRTY_TREE_ARGS, repo_root)
    if rc != 0:
        return jsonify({'error': f'git status failed: {status_out}'}), 500
    if status_out:
        return jsonify({
            'error': 'Working tree has local changes — pull would conflict.',
            'detail': status_out[:500],
            'hint': 'Stash or commit local changes, then re-try.',
        }), 409

    rc_old, old_sha = _git(['rev-parse', '--short', 'HEAD'], repo_root)
    previous_commit = old_sha if rc_old == 0 else ''

    resynced = False
    rc, pull_out = _git(['pull', '--ff-only', '--quiet'], repo_root, timeout=60)
    if rc != 0:
        _log(f"[update] ff-only pull failed (rc={rc}); falling back to hard "
             f"re-sync. git said: {pull_out[:300]}", flush=True)

        rc_f, fetch_out = _git(['fetch', '--prune', 'origin'], repo_root, timeout=60)
        if rc_f != 0:
            return jsonify({
                'error': f'git fetch failed (rc={rc_f})',
                'detail': fetch_out[:1000],
                'hint': 'Check network connectivity to github.com.',
            }), 500

        rc_b, branch = _git(['rev-parse', '--abbrev-ref', 'HEAD'], repo_root)
        branch = (branch or '').strip() if rc_b == 0 else ''
        if not branch or branch == 'HEAD':
            branch = 'master'  # detached HEAD → land on the release channel
        rc_v, _ = _git(['rev-parse', '--verify', '--quiet',
                        f'refs/remotes/origin/{branch}'], repo_root)
        if rc_v != 0:
            branch = 'master'

        rc_r, reset_out = _git(['reset', '--hard', f'origin/{branch}'],
                               repo_root, timeout=60)
        if rc_r != 0:
            return jsonify({
                'error': f'git pull failed (rc={rc}) and re-sync to '
                         f'origin/{branch} failed (rc={rc_r})',
                'detail': (pull_out + '\n---\n' + reset_out)[:1000],
                'hint': 'The checkout may be damaged. Re-run the Clayrune '
                        'installer, or re-clone and copy your data/ dir over.',
            }), 500
        resynced = True

    rc, new_sha = _git(['rev-parse', '--short', 'HEAD'], repo_root)
    rc2, log_out = _git(['log', '-5', '--pretty=format:%h %s'], repo_root)
    return jsonify({
        'ok': True,
        'new_commit': new_sha if rc == 0 else 'unknown',
        'previous_commit': previous_commit,
        # True when ff-only was impossible (upstream force-push) and we had to
        # reset --hard. Surfaced so the UI can say so, and so previous_commit is
        # meaningful for recovery: git reset --hard <previous_commit>.
        'resynced': resynced,
        'recent_log': log_out if rc2 == 0 else '',
        'restart_recommended': True,  # FE should prompt for restart after pull
    })


@bp.route('/api/system/restart', methods=['POST'])
def system_restart():
    """Restart the Mission Control server process.

    Body: {"confirmed": true, "force": bool}. We always re-check active state
    on the server to close the GET → POST race window (a cron or hivemind
    could have spawned a fresh session in between). If active and force is
    falsy, return 409 with the live blocker list so the UI can re-prompt.
    """
    data = request.get_json(silent=True) or {}
    if not data.get('confirmed'):
        return jsonify({'error': 'confirmation required (set "confirmed": true)'}), 400

    now = _time.time()
    if now - state._LAST_RESTART_TIME < _RESTART_RATE_LIMIT_SECONDS:
        wait = int(_RESTART_RATE_LIMIT_SECONDS - (now - state._LAST_RESTART_TIME))
        return jsonify({'error': f'restart was triggered recently; try again in {wait}s'}), 429

    blockers = _get_active_restart_blockers()
    if (blockers['active_sessions'] or blockers['active_hiveminds']) and not data.get('force'):
        return jsonify({
            'error': 'active flows present; stop them or pass "force": true',
            **blockers,
        }), 409

    state._LAST_RESTART_TIME = now
    audit_entry = {
        'ts': datetime.now(timezone.utc).isoformat(),
        'source_ip': request.remote_addr or '',
        'user_agent': request.headers.get('User-Agent', ''),
        'tunneled': _is_cf_tunneled_request(),
        'blockers_at_request': blockers,
        'forced': bool(data.get('force')),
    }
    _perform_server_restart_async(audit_entry)
    return jsonify({'ok': True, 'restarting': True}), 202


@bp.route('/api/system/shutdown', methods=['POST'])
def system_shutdown():
    """Shut down (power off) the Mission Control server process.

    Same confirmation + active-flow blocker semantics as /api/system/restart,
    but the process exits WITHOUT spawning a replacement. Body:
    {"confirmed": true, "force": bool}. Not rate-limited — it's a one-way,
    terminal action, so a double-submit is harmless (the process is already
    on its way out).
    """
    data = request.get_json(silent=True) or {}
    if not data.get('confirmed'):
        return jsonify({'error': 'confirmation required (set "confirmed": true)'}), 400

    blockers = _get_active_restart_blockers()
    if (blockers['active_sessions'] or blockers['active_hiveminds']) and not data.get('force'):
        return jsonify({
            'error': 'active flows present; stop them or pass "force": true',
            **blockers,
        }), 409

    audit_entry = {
        'ts': datetime.now(timezone.utc).isoformat(),
        'source_ip': request.remote_addr or '',
        'user_agent': request.headers.get('User-Agent', ''),
        'tunneled': _is_cf_tunneled_request(),
        'blockers_at_request': blockers,
        'forced': bool(data.get('force')),
        'action': 'shutdown',
    }
    _perform_server_shutdown_async(audit_entry)
    return jsonify({'ok': True, 'shutting_down': True}), 202


