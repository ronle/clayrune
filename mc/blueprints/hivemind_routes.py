"""Hivemind endpoints + orchestrator — blueprint 1.10 (MODERNIZATION_PLAN.md).

Moved VERBATIM from server.py — TWO source regions, 28 routes (the biggest
cohesive family, ~1,700 lines):
  • Main region (9 `# ── Hivemind…` sections): data layer (manifest/workstream/
    findings/bus/knowledge JSONL helpers), management + workstream CRUD, worker
    context builder & spawn, orchestrator CLI dispatch, message bus + SSE
    stream, knowledge base, escalation/intervention, and the background server
    orchestrator loop (dependency resolver + worker scheduler).
  • One straggler: /api/hivemind/<id>/runs from the trigger-aware run-history
    section — hivemind-family route, moved with its family.

Scoping calls:
  • _dispatch_agent_internal is NOT used by this family (verified — the 1.9
    terrain note said x1, but that call site is schedule_run_now's). The worker/
    orchestrator spawn paths Popen directly + _read_agent_stream, both wired.
  • _clayrune_universal_capabilities / _clayrune_api_reference feed
    _build_agent_context too (dispatch family, 1.12) — wired in, not moved.
  • _load_agent_log / _enrich_run_entries are agent-log/run-history family —
    wired in, not moved.
  • atexit.register(_hivemind_orchestrator_stop.set) STAYS in server.py
    verbatim (LIFO exit-hook ordering — the 1.8 lesson); the Event lives in
    mc/state.py since Phase 0.

Single permitted edits: the app-to-bp route-decorator swap; 3× `CONFIG.get` →
`state.CONFIG.get` (Phase-0 live alias, 1.7/1.9 precedent); the HIVEMIND_DIR
module constant (`_DATA_ROOT`-derived) became a wired placeholder with its
`.mkdir` moved into wire() (1.6/1.7 SESSION_LABELS_PATH pattern); and the
orchestrator loop gains `obs.heartbeat('hivemind-orchestrator')` (Phase 2,
plan-sanctioned — 1.6/1.7 precedent).

Inbound shims kept on server.py (startup call sites under __main__):
_start_hivemind_orchestrator, _hm_reconcile_stale_on_startup.
"""

import json
import subprocess
import threading
import uuid
import time as _time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from flask import Blueprint, Response, jsonify, request

import agent_runtime as _agent_runtime  # Multi-provider abstraction

from mc import obs, state
from mc.core import _log, now_iso, time_ago
from mc.state import (
    _hivemind_orch_lock,
    _hivemind_orchestrating,
    _hivemind_orchestrator_stop,
    _hivemind_sse_lock,
    _hivemind_sse_queues,
    agent_sessions,
)

bp = Blueprint('hivemind_routes', __name__)

# ── wired by server.py (see wire()) ──────────────────────────────────────────
load_project: Callable[[str], Any] = None  # type: ignore[assignment]
get_manager: Callable[[str], Any] = None  # type: ignore[assignment]
_register_process: Callable[..., Any] = None  # type: ignore[assignment]
_read_agent_stream: Callable[..., Any] = None  # type: ignore[assignment]
_resolve_claude: Callable[[], str] = None  # type: ignore[assignment]
_sysprompt_file_args: Callable[..., Any] = None  # type: ignore[assignment]
_sysprompt_cleanup: Callable[..., Any] = None  # type: ignore[assignment]
_hide_windows_delayed: Callable[..., Any] = None  # type: ignore[assignment]
_log_agent_activity: Callable[..., Any] = None  # type: ignore[assignment]
_load_agent_log: Callable[[str], Any] = None  # type: ignore[assignment]
_enrich_run_entries: Callable[..., Any] = None  # type: ignore[assignment]
_clayrune_universal_capabilities: Callable[..., Any] = None  # type: ignore[assignment]
_clayrune_api_reference: Callable[[], str] = None  # type: ignore[assignment]
PORT: int = 0
_POPEN_FLAGS: int = 0
_STARTUPINFO: Any = None
# Hivemind data root — derives from server.py's _DATA_ROOT, so it arrives via
# wire() (the 1.7 SESSION_LABELS_PATH wired-placeholder pattern); its
# module-level .mkdir moved into wire().
HIVEMIND_DIR: Path = None  # type: ignore[assignment]


def wire(*, hivemind_dir, port, load_project_fn, get_manager_fn,
         register_process_fn, read_agent_stream_fn, resolve_claude_fn,
         sysprompt_file_args_fn, sysprompt_cleanup_fn,
         hide_windows_delayed_fn, log_agent_activity_fn, load_agent_log_fn,
         enrich_run_entries_fn, clayrune_universal_capabilities_fn,
         clayrune_api_reference_fn, popen_flags, startupinfo):
    """Late-bind cross-family deps: load_project (projects family, 1.11);
    get_manager + the process-ledger/stream-reader/spawn helpers
    (_register_process, _read_agent_stream, _resolve_claude,
    _sysprompt_file_args/_cleanup, _hide_windows_delayed — dispatch family,
    1.12); _log_agent_activity + _load_agent_log + _enrich_run_entries
    (agent-log/run-history family); the _clayrune_* context feeders (they also
    feed _build_agent_context — dispatch, 1.12); the Popen platform consts;
    PORT; and the _DATA_ROOT-derived hivemind dir. Called once from server.py
    at import, BEFORE app.register_blueprint(bp)."""
    global HIVEMIND_DIR, PORT, load_project, get_manager, _register_process
    global _read_agent_stream, _resolve_claude, _sysprompt_file_args
    global _sysprompt_cleanup, _hide_windows_delayed, _log_agent_activity
    global _load_agent_log, _enrich_run_entries
    global _clayrune_universal_capabilities, _clayrune_api_reference
    global _POPEN_FLAGS, _STARTUPINFO
    HIVEMIND_DIR = hivemind_dir
    HIVEMIND_DIR.mkdir(parents=True, exist_ok=True)
    PORT = port
    load_project = load_project_fn
    get_manager = get_manager_fn
    _register_process = register_process_fn
    _read_agent_stream = read_agent_stream_fn
    _resolve_claude = resolve_claude_fn
    _sysprompt_file_args = sysprompt_file_args_fn
    _sysprompt_cleanup = sysprompt_cleanup_fn
    _hide_windows_delayed = hide_windows_delayed_fn
    _log_agent_activity = log_agent_activity_fn
    _load_agent_log = load_agent_log_fn
    _enrich_run_entries = enrich_run_entries_fn
    _clayrune_universal_capabilities = clayrune_universal_capabilities_fn
    _clayrune_api_reference = clayrune_api_reference_fn
    _POPEN_FLAGS = popen_flags
    _STARTUPINFO = startupinfo


# ── Hivemind: Persistent Multi-Agent Collaborative Intelligence ──────────────
# Phase 1 — data model, CRUD, message bus, findings, knowledge base, SSE events,
#            server orchestrator (dependency resolver + worker scheduler)

# HIVEMIND_DIR: wired placeholder (see module top + wire()) — was a
# _DATA_ROOT-derived module constant + .mkdir here pre-1.10.

# Global state: _hivemind_sessions / _hivemind_lock / _hivemind_sse_queues /
# _hivemind_sse_lock moved to mc/state.py (Phase 0).


def _hm_dir(hivemind_id):
    """Return the directory for a hivemind, creating subdirs if needed."""
    d = HIVEMIND_DIR / hivemind_id
    return d


def _hm_ensure_dirs(hivemind_id):
    """Ensure all subdirectories exist for a hivemind."""
    d = HIVEMIND_DIR / hivemind_id
    (d / 'workstreams').mkdir(parents=True, exist_ok=True)
    (d / 'knowledge').mkdir(parents=True, exist_ok=True)
    (d / 'bus').mkdir(parents=True, exist_ok=True)
    (d / 'sessions').mkdir(parents=True, exist_ok=True)
    return d


def _hm_load_manifest(hivemind_id):
    """Load a hivemind manifest, or None if not found."""
    p = _hm_dir(hivemind_id) / 'manifest.json'
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return None


def _hm_save_manifest(hivemind_id, manifest):
    """Save a hivemind manifest."""
    p = _hm_dir(hivemind_id) / 'manifest.json'
    p.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')


def _hm_load_workstream(hivemind_id, ws_id):
    """Load a workstream definition."""
    p = _hm_dir(hivemind_id) / 'workstreams' / f'{ws_id}.json'
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return None


def _hm_save_workstream(hivemind_id, ws_id, ws):
    """Save a workstream definition."""
    p = _hm_dir(hivemind_id) / 'workstreams' / f'{ws_id}.json'
    p.write_text(json.dumps(ws, indent=2, ensure_ascii=False), encoding='utf-8')


def _hm_list_workstreams(hivemind_id):
    """List all workstreams for a hivemind."""
    ws_dir = _hm_dir(hivemind_id) / 'workstreams'
    if not ws_dir.exists():
        return []
    result = []
    for f in sorted(ws_dir.glob('*.json')):
        try:
            ws = json.loads(f.read_text(encoding='utf-8'))
            result.append(ws)
        except Exception:
            pass
    return result


def _hm_append_finding(hivemind_id, ws_id, finding):
    """Append a finding to the workstream's JSONL file."""
    p = _hm_dir(hivemind_id) / 'workstreams' / f'{ws_id}_findings.jsonl'
    with open(p, 'a', encoding='utf-8') as f:
        f.write(json.dumps(finding, ensure_ascii=False) + '\n')
    # Increment findings_count on workstream
    ws = _hm_load_workstream(hivemind_id, ws_id)
    if ws:
        ws['findings_count'] = ws.get('findings_count', 0) + 1
        _hm_save_workstream(hivemind_id, ws_id, ws)


def _hm_read_findings(hivemind_id, ws_id, last_n=20):
    """Read last N findings from a workstream's JSONL file."""
    p = _hm_dir(hivemind_id) / 'workstreams' / f'{ws_id}_findings.jsonl'
    if not p.exists():
        return []
    lines = []
    try:
        with open(p, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    lines.append(line)
    except Exception:
        return []
    # Return last N
    result = []
    for line in lines[-last_n:]:
        try:
            result.append(json.loads(line))
        except Exception:
            pass
    return result


def _hm_read_all_findings(hivemind_id):
    """Read all findings across all workstreams."""
    ws_dir = _hm_dir(hivemind_id) / 'workstreams'
    if not ws_dir.exists():
        return []
    all_findings = []
    for f in ws_dir.glob('*_findings.jsonl'):
        try:
            with open(f, encoding='utf-8') as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        all_findings.append(json.loads(line))
        except Exception:
            pass
    all_findings.sort(key=lambda x: x.get('timestamp', ''))
    return all_findings


def _hm_append_bus_message(hivemind_id, message):
    """Append a message to the bus JSONL file."""
    p = _hm_dir(hivemind_id) / 'bus' / 'messages.jsonl'
    with open(p, 'a', encoding='utf-8') as f:
        f.write(json.dumps(message, ensure_ascii=False) + '\n')


def _hm_read_bus_messages(hivemind_id, last_n=50, ws_filter=None):
    """Read bus messages, optionally filtered to a workstream."""
    p = _hm_dir(hivemind_id) / 'bus' / 'messages.jsonl'
    if not p.exists():
        return []
    lines = []
    try:
        with open(p, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    lines.append(line)
    except Exception:
        return []
    result = []
    for line in lines:
        try:
            msg = json.loads(line)
            if ws_filter:
                if msg.get('to') != ws_filter and msg.get('from') != ws_filter:
                    continue
            result.append(msg)
        except Exception:
            pass
    return result[-last_n:] if last_n else result


def _hm_append_decision(hivemind_id, decision):
    """Append a decision to the decisions JSONL file."""
    p = _hm_dir(hivemind_id) / 'knowledge' / 'decisions.jsonl'
    with open(p, 'a', encoding='utf-8') as f:
        f.write(json.dumps(decision, ensure_ascii=False) + '\n')


def _hm_read_decisions(hivemind_id, last_n=None):
    """Read decisions from the JSONL file."""
    p = _hm_dir(hivemind_id) / 'knowledge' / 'decisions.jsonl'
    if not p.exists():
        return []
    result = []
    try:
        with open(p, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    result.append(json.loads(line))
    except Exception:
        pass
    return result[-last_n:] if last_n else result


def _hm_read_open_questions(hivemind_id):
    """Read open questions from the JSONL file (excludes resolved)."""
    p = _hm_dir(hivemind_id) / 'knowledge' / 'open_questions.jsonl'
    if not p.exists():
        return []
    result = []
    try:
        with open(p, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    q = json.loads(line)
                    if not q.get('resolved'):
                        result.append(q)
    except Exception:
        pass
    return result


def _hm_append_open_question(hivemind_id, question):
    """Append an open question."""
    p = _hm_dir(hivemind_id) / 'knowledge' / 'open_questions.jsonl'
    with open(p, 'a', encoding='utf-8') as f:
        f.write(json.dumps(question, ensure_ascii=False) + '\n')


def _hm_resolve_question(hivemind_id, question_id):
    """Mark an open question as resolved by rewriting the JSONL."""
    p = _hm_dir(hivemind_id) / 'knowledge' / 'open_questions.jsonl'
    if not p.exists():
        return False
    lines = []
    found = False
    with open(p, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            q = json.loads(line)
            if q.get('id') == question_id:
                q['resolved'] = True
                found = True
            lines.append(json.dumps(q, ensure_ascii=False))
    if found:
        with open(p, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')
    return found


def _hm_read_synthesis(hivemind_id):
    """Read the synthesis markdown file."""
    p = _hm_dir(hivemind_id) / 'knowledge' / 'synthesis.md'
    if not p.exists():
        return ''
    return p.read_text(encoding='utf-8')


def _hm_write_synthesis(hivemind_id, content):
    """Write the synthesis markdown file."""
    p = _hm_dir(hivemind_id) / 'knowledge' / 'synthesis.md'
    p.write_text(content, encoding='utf-8')


def _hm_read_context(hivemind_id, ws_id):
    """Read the workstream context markdown file."""
    p = _hm_dir(hivemind_id) / 'workstreams' / f'{ws_id}_context.md'
    if not p.exists():
        return ''
    return p.read_text(encoding='utf-8')


def _hm_write_context(hivemind_id, ws_id, content):
    """Write the workstream context markdown file."""
    p = _hm_dir(hivemind_id) / 'workstreams' / f'{ws_id}_context.md'
    p.write_text(content, encoding='utf-8')


def _hm_push_sse(hivemind_id, event):
    """Push an SSE event to all listeners for this hivemind."""
    with _hivemind_sse_lock:
        queues = _hivemind_sse_queues.get(hivemind_id, [])
        for q in queues:
            try:
                q.append(event)
            except Exception:
                pass


def _hm_resolve_dependencies(workstreams):
    """Determine which workstreams are ready to run (all deps completed)."""
    completed = {ws['id'] for ws in workstreams if ws.get('status') == 'completed'}
    ready = []
    for ws in workstreams:
        if ws.get('status') != 'pending':
            continue
        deps = ws.get('dependencies', [])
        if all(d in completed for d in deps):
            ready.append(ws)
    # Sort by priority (lower = higher priority)
    ready.sort(key=lambda ws: ws.get('priority', 5))
    return ready


def _hm_list_all():
    """List all hiveminds."""
    result = []
    if not HIVEMIND_DIR.exists():
        return result
    for d in sorted(HIVEMIND_DIR.iterdir()):
        if d.is_dir():
            manifest = _hm_load_manifest(d.name)
            if manifest:
                result.append(manifest)
    return result


# Hours of inactivity after which an "active" hivemind is considered orphaned.
# Threshold matches the frontend heuristic (HM_STALE_HOURS in static/index.html).
_HM_STALE_HOURS = 24

def _hm_reconcile_stale_on_startup():
    """One-shot pass: transition long-active hiveminds with no recent activity to 'stale'.

    Server crashes / restarts orphan hiveminds whose orchestrator + worker subprocesses
    are gone, but the manifest still says status='active'. This sweep updates those
    manifests so the UI / API reflects reality. The user can still 'Restart' to resume.
    Only touches 'active' — 'paused' is intentional idle and should stay paused.
    """
    if not HIVEMIND_DIR.exists():
        return
    threshold_secs = _HM_STALE_HOURS * 3600
    now = _time.time()
    transitioned = 0
    try:
        for d in HIVEMIND_DIR.iterdir():
            if not d.is_dir() or d.name.startswith('_'):
                continue
            manifest = _hm_load_manifest(d.name)
            if not manifest:
                continue
            if manifest.get('status') != 'active':
                continue
            updated_at = manifest.get('updated_at', '')
            if not updated_at:
                continue
            try:
                ts = datetime.fromisoformat(updated_at.replace('Z', '+00:00')).timestamp()
            except Exception:
                continue
            if now - ts > threshold_secs:
                manifest['status'] = 'stale'
                _hm_save_manifest(d.name, manifest)
                transitioned += 1
    except Exception as e:
        _log(f"[hivemind-reconcile] failed: {e}")
        return
    if transitioned:
        _log(f"[hivemind-reconcile] marked {transitioned} long-active hivemind(s) as 'stale' (>{_HM_STALE_HOURS}h idle)")


# ── Hivemind API: Management ─────────────────────────────────────────────────

@bp.route('/api/hivemind/create', methods=['POST'])
def hivemind_create():
    """Create a new hivemind."""
    data = request.get_json()
    if not data or not data.get('goal', '').strip():
        return jsonify({'error': 'goal required'}), 400

    project_id = data.get('project_id', '').strip()
    if not project_id:
        return jsonify({'error': 'project_id required'}), 400

    p = load_project(project_id)
    if not p:
        return jsonify({'error': 'project not found'}), 404

    hivemind_id = 'hm_' + str(uuid.uuid4())[:8]
    _hm_ensure_dirs(hivemind_id)

    manifest = {
        'id': hivemind_id,
        'project_id': project_id,
        'title': data.get('title', data['goal'][:80]).strip(),
        'goal': data['goal'].strip(),
        'status': 'active',
        'created_at': now_iso(),
        'updated_at': now_iso(),
        'session_count': 0,
        'config': {
            'max_concurrent_workers': data.get('max_concurrent_workers', 3),
            'auto_synthesize': data.get('auto_synthesize', True),
            'synthesize_interval_turns': data.get('synthesize_interval_turns', 10),
            'require_user_approval_for_decisions': data.get('require_user_approval', False),
            'orchestrator_model': data.get('orchestrator_model', 'sonnet'),
            'worker_model': data.get('worker_model', 'sonnet'),
            'max_retries_per_workstream': data.get('max_retries', 2),
        },
    }
    _hm_save_manifest(hivemind_id, manifest)

    # Initialize empty synthesis
    _hm_write_synthesis(hivemind_id, f"# {manifest['title']} — Synthesis\n\nNo findings yet.\n")

    # If the caller supplied workstreams inline, materialize them now. The
    # endpoint previously CHECKED `data.get('workstreams')` (to gate the
    # auto-decompose) but never actually iterated and persisted them — every
    # caller had to make a second POST per workstream after create. Fixed.
    created_workstreams = []
    inline_ws = data.get('workstreams') or []
    if inline_ws:
        for ws_in in inline_ws:
            if not isinstance(ws_in, dict):
                continue
            title = (ws_in.get('title') or '').strip()
            if not title:
                continue  # skip malformed entries rather than fail the whole create
            ws_id = ws_in.get('id') or ('ws_' + str(uuid.uuid4())[:6])
            ws = {
                'id': ws_id,
                'title': title,
                'description': (ws_in.get('description') or '').strip(),
                'status': 'pending',
                'dependencies': ws_in.get('dependencies') or [],
                'priority': ws_in.get('priority', 5),
                'model': ws_in.get('model', ''),
                'created_at': now_iso(),
                'completed_at': None,
                'findings_count': 0,
                'sessions_used': 0,
                'retry_count': 0,
                'current_agent_session_id': None,
                'last_agent_session_id': None,
            }
            _hm_save_workstream(hivemind_id, ws_id, ws)
            created_workstreams.append(ws)
        if created_workstreams:
            manifest['updated_at'] = now_iso()
            _hm_save_manifest(hivemind_id, manifest)

    # Auto-dispatch orchestrator for goal decomposition only when the caller
    # did NOT provide workstreams inline. Inline = caller already decomposed.
    if not inline_ws:
        _hm_dispatch_orchestrator(hivemind_id, 'decompose')

    return jsonify({'ok': True, 'hivemind': manifest, 'workstreams': created_workstreams})


@bp.route('/api/hivemind/list')
def hivemind_list():
    """List all hiveminds, optionally filtered by project_id."""
    project_id = request.args.get('project_id', '')
    all_hm = _hm_list_all()
    if project_id:
        all_hm = [h for h in all_hm if h.get('project_id') == project_id]
    # Add workstream summary
    for h in all_hm:
        workstreams = _hm_list_workstreams(h['id'])
        h['workstream_count'] = len(workstreams)
        h['workstreams_completed'] = sum(1 for ws in workstreams if ws.get('status') == 'completed')
        h['workstreams_active'] = sum(1 for ws in workstreams if ws.get('status') == 'active')
        h['total_findings'] = sum(ws.get('findings_count', 0) for ws in workstreams)
        h['updated_relative'] = time_ago(h.get('updated_at'))
    return jsonify(all_hm)


@bp.route('/api/hivemind/<hivemind_id>')
def hivemind_get(hivemind_id):
    """Get full hivemind state including workstreams."""
    manifest = _hm_load_manifest(hivemind_id)
    if not manifest:
        return jsonify({'error': 'not found'}), 404
    workstreams = _hm_list_workstreams(hivemind_id)
    recent_messages = _hm_read_bus_messages(hivemind_id, last_n=20)
    decisions = _hm_read_decisions(hivemind_id, last_n=10)
    open_questions = _hm_read_open_questions(hivemind_id)
    return jsonify({
        'manifest': manifest,
        'workstreams': workstreams,
        'recent_messages': recent_messages,
        'decisions': decisions,
        'open_questions': open_questions,
    })


@bp.route('/api/hivemind/<hivemind_id>', methods=['PUT'])
def hivemind_update(hivemind_id):
    """Update hivemind config."""
    manifest = _hm_load_manifest(hivemind_id)
    if not manifest:
        return jsonify({'error': 'not found'}), 404
    data = request.get_json() or {}
    # Update allowed fields
    for key in ('title', 'goal', 'status'):
        if key in data:
            manifest[key] = data[key]
    if 'config' in data and isinstance(data['config'], dict):
        manifest['config'].update(data['config'])
    manifest['updated_at'] = now_iso()
    _hm_save_manifest(hivemind_id, manifest)
    return jsonify({'ok': True, 'manifest': manifest})


@bp.route('/api/hivemind/<hivemind_id>/start', methods=['POST'])
def hivemind_start(hivemind_id):
    """Start or resume a hivemind — re-evaluate state and spawn ready workers."""
    manifest = _hm_load_manifest(hivemind_id)
    if not manifest:
        return jsonify({'error': 'not found'}), 404
    manifest['status'] = 'active'
    manifest['session_count'] = manifest.get('session_count', 0) + 1
    manifest['updated_at'] = now_iso()
    _hm_save_manifest(hivemind_id, manifest)
    _hm_push_sse(hivemind_id, {'type': 'hivemind_status', 'hivemind_id': hivemind_id, 'status': 'active'})

    # If no workstreams exist, trigger goal decomposition
    workstreams = _hm_list_workstreams(hivemind_id)
    if not workstreams:
        _hm_dispatch_orchestrator(hivemind_id, 'decompose')

    return jsonify({'ok': True, 'status': 'active'})


@bp.route('/api/hivemind/<hivemind_id>/pause', methods=['POST'])
def hivemind_pause(hivemind_id):
    """Pause a hivemind."""
    manifest = _hm_load_manifest(hivemind_id)
    if not manifest:
        return jsonify({'error': 'not found'}), 404
    manifest['status'] = 'paused'
    manifest['updated_at'] = now_iso()
    _hm_save_manifest(hivemind_id, manifest)
    # Set all active workstreams to paused
    for ws in _hm_list_workstreams(hivemind_id):
        if ws.get('status') == 'active':
            ws['status'] = 'paused'
            _hm_save_workstream(hivemind_id, ws['id'], ws)
    _hm_push_sse(hivemind_id, {'type': 'hivemind_status', 'hivemind_id': hivemind_id, 'status': 'paused'})
    return jsonify({'ok': True, 'status': 'paused'})


@bp.route('/api/hivemind/<hivemind_id>/stop', methods=['POST'])
def hivemind_stop(hivemind_id):
    """Stop a hivemind — hard stop all agents."""
    manifest = _hm_load_manifest(hivemind_id)
    if not manifest:
        return jsonify({'error': 'not found'}), 404
    manifest['status'] = 'stopped'
    manifest['updated_at'] = now_iso()
    _hm_save_manifest(hivemind_id, manifest)
    # Set all non-completed workstreams to paused
    for ws in _hm_list_workstreams(hivemind_id):
        if ws.get('status') in ('active', 'pending', 'blocked'):
            ws['status'] = 'paused'
            _hm_save_workstream(hivemind_id, ws['id'], ws)
    _hm_push_sse(hivemind_id, {'type': 'hivemind_status', 'hivemind_id': hivemind_id, 'status': 'stopped'})
    return jsonify({'ok': True, 'status': 'stopped'})


@bp.route('/api/hivemind/<hivemind_id>', methods=['DELETE'])
def hivemind_delete(hivemind_id):
    """Archive/delete a hivemind."""
    d = _hm_dir(hivemind_id)
    if not d.exists():
        return jsonify({'error': 'not found'}), 404
    import shutil
    archive_dir = HIVEMIND_DIR / '_archived'
    archive_dir.mkdir(parents=True, exist_ok=True)
    shutil.move(str(d), str(archive_dir / hivemind_id))
    return jsonify({'ok': True})


# ── Hivemind API: Workstream Management ──────────────────────────────────────

@bp.route('/api/hivemind/<hivemind_id>/workstreams')
def hivemind_workstreams_list(hivemind_id):
    """List all workstreams for a hivemind."""
    manifest = _hm_load_manifest(hivemind_id)
    if not manifest:
        return jsonify({'error': 'not found'}), 404
    workstreams = _hm_list_workstreams(hivemind_id)
    return jsonify(workstreams)


@bp.route('/api/hivemind/<hivemind_id>/workstreams/create', methods=['POST'])
def hivemind_workstream_create(hivemind_id):
    """Create a new workstream."""
    manifest = _hm_load_manifest(hivemind_id)
    if not manifest:
        return jsonify({'error': 'not found'}), 404
    data = request.get_json()
    if not data or not data.get('title', '').strip():
        return jsonify({'error': 'title required'}), 400

    ws_id = data.get('id', 'ws_' + str(uuid.uuid4())[:6])
    ws = {
        'id': ws_id,
        'title': data['title'].strip(),
        'description': data.get('description', '').strip(),
        'status': 'pending',
        'dependencies': data.get('dependencies', []),
        'priority': data.get('priority', 5),
        'model': data.get('model', ''),
        'created_at': now_iso(),
        'completed_at': None,
        'findings_count': 0,
        'sessions_used': 0,
        'retry_count': 0,
        'current_agent_session_id': None,
        'last_agent_session_id': None,
    }
    _hm_save_workstream(hivemind_id, ws_id, ws)
    manifest['updated_at'] = now_iso()
    _hm_save_manifest(hivemind_id, manifest)

    _hm_push_sse(hivemind_id, {
        'type': 'hivemind_workstream',
        'hivemind_id': hivemind_id,
        'ws_id': ws_id,
        'status': 'pending',
        'workstream': ws,
    })
    return jsonify({'ok': True, 'workstream': ws})


@bp.route('/api/hivemind/<hivemind_id>/workstreams/<ws_id>', methods=['PUT'])
def hivemind_workstream_update(hivemind_id, ws_id):
    """Update a workstream definition."""
    ws = _hm_load_workstream(hivemind_id, ws_id)
    if not ws:
        return jsonify({'error': 'workstream not found'}), 404
    data = request.get_json() or {}
    for key in ('title', 'description', 'dependencies', 'priority', 'model', 'status'):
        if key in data:
            ws[key] = data[key]
    if data.get('status') == 'completed' and not ws.get('completed_at'):
        ws['completed_at'] = now_iso()
    _hm_save_workstream(hivemind_id, ws_id, ws)

    manifest = _hm_load_manifest(hivemind_id)
    if manifest:
        manifest['updated_at'] = now_iso()
        _hm_save_manifest(hivemind_id, manifest)

    _hm_push_sse(hivemind_id, {
        'type': 'hivemind_workstream',
        'hivemind_id': hivemind_id,
        'ws_id': ws_id,
        'status': ws['status'],
        'workstream': ws,
    })
    return jsonify({'ok': True, 'workstream': ws})


@bp.route('/api/hivemind/<hivemind_id>/workstreams/<ws_id>/status', methods=['POST'])
def hivemind_workstream_status(hivemind_id, ws_id):
    """Update workstream status (convenience endpoint for workers)."""
    ws = _hm_load_workstream(hivemind_id, ws_id)
    if not ws:
        return jsonify({'error': 'workstream not found'}), 404
    data = request.get_json() or {}
    new_status = data.get('status', '').strip()
    if new_status not in ('pending', 'active', 'blocked', 'completed', 'paused', 'failed'):
        return jsonify({'error': 'invalid status'}), 400
    ws['status'] = new_status
    if new_status == 'completed' and not ws.get('completed_at'):
        ws['completed_at'] = now_iso()
    _hm_save_workstream(hivemind_id, ws_id, ws)

    _hm_push_sse(hivemind_id, {
        'type': 'hivemind_workstream',
        'hivemind_id': hivemind_id,
        'ws_id': ws_id,
        'status': new_status,
    })
    return jsonify({'ok': True, 'status': new_status})


# ── Hivemind: Worker Context Builder & Spawn ─────────────────────────────────

# _hivemind_orchestrating / _hivemind_orch_lock moved to mc/state.py (Phase 0).


def _hm_read_handoff(hivemind_id, ws_id):
    """Read the latest handoff document for a workstream."""
    p = _hm_dir(hivemind_id) / 'workstreams' / f'{ws_id}_handoff.md'
    if p.exists():
        try:
            return p.read_text(encoding='utf-8')
        except Exception:
            pass
    return ''


def _hm_write_handoff(hivemind_id, ws_id, content):
    """Write a handoff document for a workstream."""
    p = _hm_dir(hivemind_id) / 'workstreams' / f'{ws_id}_handoff.md'
    p.write_text(content, encoding='utf-8')


def _hm_build_worker_context(hivemind_id, ws_id):
    """Build the system prompt context for a hivemind worker agent."""
    manifest = _hm_load_manifest(hivemind_id)
    ws = _hm_load_workstream(hivemind_id, ws_id)
    if not manifest or not ws:
        return ''

    port = PORT
    parts = []

    parts.append(
        f"You are a specialist agent in a Hivemind analysis.\n"
        f"Hivemind: {manifest.get('title', '')}\n"
        f"Overall Goal: {manifest.get('goal', '')}"
    )

    parts.append(
        f"YOUR WORKSTREAM: {ws.get('title', ws_id)}\n"
        f"YOUR BRIEF: {ws.get('description', '')}"
    )

    # Handoff from previous worker (highest priority context)
    handoff = _hm_read_handoff(hivemind_id, ws_id)
    if handoff:
        parts.append(f"HANDOFF FROM PREVIOUS WORKER:\n{handoff[:4000]}")

    # Accumulated context
    ctx = _hm_read_context(hivemind_id, ws_id)
    if ctx:
        parts.append(f"ACCUMULATED CONTEXT:\n{ctx[:4000]}")

    # Recent findings from this workstream
    findings = _hm_read_findings(hivemind_id, ws_id, last_n=20)
    if findings:
        findings_text = '\n'.join(
            f"- [{f.get('timestamp', '')[:16]}] {f.get('title', '')}: {f.get('content', '')[:200]}"
            for f in findings[-20:]
        )
        parts.append(f"RECENT FINDINGS FROM THIS WORKSTREAM:\n{findings_text}")

    # Relevant bus messages from other workstreams
    bus_msgs = _hm_read_bus_messages(hivemind_id, last_n=50, ws_filter=ws_id)
    if bus_msgs:
        bus_text = '\n'.join(
            f"- [{m.get('timestamp', '')[:16]}] {m.get('from', '')} -> {m.get('to', '')}: "
            f"{m.get('content', '')[:200]}"
            for m in bus_msgs[-15:]
        )
        parts.append(f"RELEVANT MESSAGES FROM BUS:\n{bus_text}")

    # Decisions that affect this workstream
    decisions = _hm_read_decisions(hivemind_id, last_n=20)
    relevant = [d for d in decisions if ws_id in d.get('impacts', []) or d.get('workstream') == ws_id]
    if relevant:
        dec_text = '\n'.join(
            f"- {d.get('decision', '')}: {d.get('rationale', '')[:200]}"
            for d in relevant[-10:]
        )
        parts.append(f"DECISIONS THAT AFFECT YOUR WORK:\n{dec_text}")

    # Worker capabilities (API endpoints)
    parts.append(
        f"YOUR CAPABILITIES (use curl to call these):\n"
        f'- Report a finding: curl -s -X POST http://localhost:{port}/api/hivemind/{hivemind_id}/bus/post '
        f'-H "Content-Type: application/json" '
        f"""-d '{{"from":"{ws_id}","type":"finding_report","title":"...","content":"...","confidence":"high|medium|low"}}'\n"""
        f'- Ask a question: curl -s -X POST http://localhost:{port}/api/hivemind/{hivemind_id}/bus/post '
        f'-H "Content-Type: application/json" '
        f"""-d '{{"from":"{ws_id}","type":"question","to":"ws_xxx","content":"..."}}'\n"""
        f'- Report a blocker: curl -s -X POST http://localhost:{port}/api/hivemind/{hivemind_id}/escalate '
        f'-H "Content-Type: application/json" '
        f"""-d '{{"from":"{ws_id}","content":"..."}}'\n"""
        f'- Submit handoff (REQUIRED before marking complete): curl -s -X POST '
        f'http://localhost:{port}/api/hivemind/{hivemind_id}/workstreams/{ws_id}/handoff '
        f'-H "Content-Type: application/json" '
        f"""-d '{{"what_was_done":"...","key_findings_summary":"...","next_worker_should":"..."}}'\n"""
        f'- Mark complete: curl -s -X POST http://localhost:{port}/api/hivemind/{hivemind_id}/workstreams/{ws_id}/status '
        f'-H "Content-Type: application/json" '
        f"""-d '{{"status":"completed"}}'"""
    )

    parts.append(
        "RULES:\n"
        "1. Build on accumulated context — do NOT repeat analysis already completed\n"
        "2. Report findings as you discover them (do not batch at the end)\n"
        "3. Reference evidence and data for all findings\n"
        "4. If you need information from another workstream, ask via the bus\n"
        "5. If you encounter a decision point that affects other workstreams, escalate\n"
        "6. Do NOT write to the project MEMORY.md — your findings go to the bus only\n"
        "7. TWO-PHASE PROTOCOL:\n"
        "   PHASE 1 — Do your analysis. Post findings to the bus as you discover them.\n"
        "   PHASE 2 — When done, submit a handoff document via the handoff endpoint, "
        "then mark your workstream complete. Do NOT skip Phase 2."
    )

    # Universal Clayrune awareness — same source of truth as regular agents.
    # See _clayrune_universal_capabilities().
    parts.extend(_clayrune_universal_capabilities(port=port))

    # Pre-authored Clayrune API reference (same one regular agents get).
    api_ref = _clayrune_api_reference()
    if api_ref:
        parts.append("--- CLAYRUNE API REFERENCE ---\n" + api_ref)

    return "\n\n".join(parts)


def _hm_spawn_worker_session(manifest, ws, p, hivemind_id, ws_id):
    """Spawn a hivemind worker session. Returns session_id.

    Routes through the AgentRuntime for non-claude projects; uses the claude
    direct-spawn path otherwise (byte-identical argv). Claude is the default
    provider for hivemind workers (the bus/tool protocol is claude-native).

    Worker context is injected via --append-system-prompt for claude; prepended
    to the task for other providers (context_injection='prepend').
    """
    project_id = p.get('id', '')
    pp = p.get('project_path', '')
    worker_context = _hm_build_worker_context(hivemind_id, ws_id)
    model = (ws.get('model', '') or
             manifest.get('config', {}).get('worker_model', '') or
             state.CONFIG.get('agent_model', ''))
    task = (
        f"You are a Hivemind worker for workstream: {ws.get('title', ws_id)}.\n"
        f"Brief: {ws.get('description', '')}\n\n"
        f"Begin your analysis. Follow the two-phase protocol described in your system prompt."
    )
    session_id = f'hm_{uuid.uuid4().hex[:8]}'
    provider_name = (p.get('provider') or state.CONFIG.get('default_provider') or 'claude').lower()

    if provider_name != 'claude':
        # Non-claude: route through the runtime. Worker context prepended to
        # task since non-claude runtimes use context_injection='prepend'.
        try:
            rt = _agent_runtime.get_runtime(provider_name)
        except KeyError:
            _log(f"[hm-spawn] unknown provider {provider_name!r}, falling back to claude")
            rt = None

        if rt is not None:
            task_with_ctx = f"{worker_context}\n\n---\n\n{task}"
            pre_session = {
                'status': 'running',
                'task': task,
                'log_lines': [],
                'started_at': now_iso(),
                'session_id': session_id,
                'project_id': project_id,
                'mode': 'A',
                'housekeeping': True,
                'hivemind_id': hivemind_id,
                'hivemind_ws_id': ws_id,
                'trigger_type': 'hivemind_worker',
                'trigger_id': ws_id,
                'provider': provider_name,
                'process_alive': True,
                'last_output_time': _time.time(),
                'last_status_change_time': _time.time(),
                'guardian_state': None,
                'recovery_attempts': 0,
                'last_recovery_time': 0,
                'pending_recovery_message': None,
                'circuit_breaker_tripped': False,
                '_dispatch_time': _time.time(),
            }
            mgr = get_manager(project_id)
            mgr.ensure_guardian()
            with mgr.lock:
                agent_sessions[session_id] = pre_session
                mgr.session_ids.add(session_id)
            rt.dispatch(
                project_path=pp,
                task=task_with_ctx,
                system_prompt='',
                mode='A',
                model=model,
                mc_session_id=session_id,
                session_dict=pre_session,
                project_id=project_id,
                register_process=_register_process,
            )
            return session_id

    # Claude path (byte-identical) — _resolve_claude() delegates to ClaudeRuntime.
    max_turns = (manifest.get('config', {}).get('worker_max_turns', 0) or
                 state.CONFIG.get('agent_max_turns', 0))
    _sp_args, _sp_path = _sysprompt_file_args(worker_context)
    cmd = [_resolve_claude(), '-p', task, '--print', '--verbose',
           '--output-format', 'stream-json',
           '--dangerously-skip-permissions',
           *_sp_args]
    if model:
        cmd.extend(['--model', model])
    if max_turns and int(max_turns) > 0:
        cmd.extend(['--max-turns', str(int(max_turns))])

    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=pp,
        text=True,
        encoding='utf-8',
        errors='replace',
        creationflags=_POPEN_FLAGS,
        startupinfo=_STARTUPINFO,
    )
    _sysprompt_cleanup(_sp_path, proc)
    threading.Thread(target=_hide_windows_delayed, args=(proc.pid,), daemon=True).start()
    _register_process(proc, f'Hivemind Worker ({ws.get("title", ws_id)[:30]})',
                      'hivemind_worker', session_id, project_id, task[:80])

    session = {
        'proc': proc,
        'status': 'running',
        'task': task,
        'log_lines': [],
        'started_at': now_iso(),
        'session_id': session_id,
        'project_id': project_id,
        'mode': 'A',
        'housekeeping': True,
        'hivemind_id': hivemind_id,
        'hivemind_ws_id': ws_id,
        'trigger_type': 'hivemind_worker',
        'trigger_id': ws_id,
    }
    mgr = get_manager(project_id)
    mgr.ensure_guardian()
    with mgr.lock:
        agent_sessions[session_id] = session
        mgr.session_ids.add(session_id)

    threading.Thread(target=_read_agent_stream, args=(proc, session), daemon=True).start()
    return session_id


@bp.route('/api/hivemind/<hivemind_id>/workstreams/<ws_id>/spawn', methods=['POST'])
def hivemind_workstream_spawn(hivemind_id, ws_id):
    """Spawn a worker agent for a specific workstream."""
    manifest = _hm_load_manifest(hivemind_id)
    if not manifest:
        return jsonify({'error': 'hivemind not found'}), 404
    ws = _hm_load_workstream(hivemind_id, ws_id)
    if not ws:
        return jsonify({'error': 'workstream not found'}), 404

    project_id = manifest.get('project_id', '')
    p = load_project(project_id)
    if not p:
        return jsonify({'error': 'project not found'}), 404

    pp = p.get('project_path', '')
    if not pp or not Path(pp).is_dir():
        return jsonify({'error': 'project_path not set'}), 400

    try:
        session_id = _hm_spawn_worker_session(manifest, ws, p, hivemind_id, ws_id)

        ws['status'] = 'active'
        ws['current_agent_session_id'] = session_id
        ws['sessions_used'] = ws.get('sessions_used', 0) + 1
        _hm_save_workstream(hivemind_id, ws_id, ws)

        _hm_push_sse(hivemind_id, {
            'type': 'hivemind_worker_spawned',
            'hivemind_id': hivemind_id,
            'ws_id': ws_id,
            'session_id': session_id,
        })

        _log_agent_activity(project_id, f"Hivemind worker spawned for {ws.get('title', ws_id)}")
        return jsonify({'ok': True, 'session_id': session_id})

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/hivemind/<hivemind_id>/workstreams/<ws_id>/handoff', methods=['POST'])
def hivemind_workstream_handoff(hivemind_id, ws_id):
    """Submit a worker handoff document (Phase 2 of two-phase protocol)."""
    ws = _hm_load_workstream(hivemind_id, ws_id)
    if not ws:
        return jsonify({'error': 'workstream not found'}), 404

    data = request.get_json() or {}

    # Build handoff markdown
    sections = []
    sections.append(f"# Handoff: {ws.get('title', ws_id)}")
    sections.append(f"**Date:** {now_iso()}")

    if data.get('what_was_done'):
        sections.append(f"## What Was Done\n{data['what_was_done']}")
    if data.get('key_findings_summary'):
        sections.append(f"## Key Findings\n{data['key_findings_summary']}")
    if data.get('decisions_made'):
        decisions = data['decisions_made']
        if isinstance(decisions, list):
            dec_text = '\n'.join(f"- {d}" for d in decisions)
        else:
            dec_text = str(decisions)
        sections.append(f"## Decisions Made\n{dec_text}")
    if data.get('open_questions'):
        questions = data['open_questions']
        if isinstance(questions, list):
            q_text = '\n'.join(f"- {q}" for q in questions)
            # Also append to open_questions.jsonl
            for q in questions:
                _hm_append_open_question(hivemind_id, {
                    'id': 'q_' + str(uuid.uuid4())[:8],
                    'timestamp': now_iso(),
                    'workstream': ws_id,
                    'question': str(q),
                })
        else:
            q_text = str(questions)
        sections.append(f"## Open Questions\n{q_text}")
    if data.get('next_worker_should'):
        sections.append(f"## Next Worker Should\n{data['next_worker_should']}")

    handoff_md = '\n\n'.join(sections) + '\n'
    _hm_write_handoff(hivemind_id, ws_id, handoff_md)

    # Record artifact if provided
    if data.get('artifact'):
        artifact_path = _hm_dir(hivemind_id) / 'workstreams' / f'{ws_id}_artifact.json'
        artifact_path.write_text(json.dumps(data['artifact'], indent=2, ensure_ascii=False), encoding='utf-8')

    _hm_push_sse(hivemind_id, {
        'type': 'hivemind_handoff',
        'hivemind_id': hivemind_id,
        'ws_id': ws_id,
        'summary': data.get('key_findings_summary', '')[:500],
    })

    return jsonify({'ok': True})


# ── Hivemind: Orchestrator CLI Sessions ──────────────────────────────────────

def _hm_dispatch_orchestrator(hivemind_id, task_type, extra_context=''):
    """Spawn a short-lived orchestrator CLI session for a hivemind.
    task_type: 'decompose' | 'synthesize' | 'replan'
    """
    with _hivemind_orch_lock:
        if hivemind_id in _hivemind_orchestrating:
            return None
        _hivemind_orchestrating.add(hivemind_id)

    manifest = _hm_load_manifest(hivemind_id)
    if not manifest:
        with _hivemind_orch_lock:
            _hivemind_orchestrating.discard(hivemind_id)
        return None

    project_id = manifest.get('project_id', '')
    p = load_project(project_id)
    pp = (p or {}).get('project_path', '') or str(Path.home())
    if not Path(pp).is_dir():
        pp = str(Path.home())

    port = PORT
    workstreams = _hm_list_workstreams(hivemind_id)
    ws_summary = '\n'.join(
        f"  - {ws['id']}: {ws.get('title', '')} [status={ws.get('status', 'pending')}, "
        f"findings={ws.get('findings_count', 0)}, priority={ws.get('priority', 5)}]"
        for ws in workstreams
    ) or '  (none yet)'

    synthesis = _hm_read_synthesis(hivemind_id)
    decisions = _hm_read_decisions(hivemind_id, last_n=10)
    decisions_text = '\n'.join(
        f"  - {d.get('decision', '')}" for d in decisions
    ) or '  (none)'

    # Task-specific prompt
    if task_type == 'decompose':
        task_prompt = (
            f"YOUR TASK: Decompose the goal into workstreams.\n\n"
            f"Analyze the goal and break it into 3-8 focused workstreams. For each workstream, "
            f"call the create endpoint with: id (ws_001, ws_002, ...), title, description, "
            f"dependencies (list of ws_ids that must complete first), and priority (1=highest).\n\n"
            f"Consider which workstreams can run in parallel (no dependencies) vs which need "
            f"results from earlier workstreams.\n\n"
            f"Create workstreams by calling:\n"
            f'curl -s -X POST http://localhost:{port}/api/hivemind/{hivemind_id}/workstreams/create '
            f'-H "Content-Type: application/json" '
            f"""-d '{{"id":"ws_001","title":"...","description":"...","dependencies":[],"priority":1}}'\n\n"""
            f"Create ALL workstreams, then stop. Do not start any analysis yourself."
        )
    elif task_type == 'synthesize':
        all_findings = _hm_read_all_findings(hivemind_id)
        findings_text = '\n'.join(
            f"  - [{f.get('timestamp', '')[:16]}] ({f.get('ws_id', '')}): {f.get('title', '')} — {f.get('content', '')[:300]}"
            for f in all_findings[-50:]
        ) or '  (none)'
        synth_path = str(_hm_dir(hivemind_id) / 'knowledge' / 'synthesis.md').replace('\\', '/')
        task_prompt = (
            f"YOUR TASK: Synthesize all findings into an updated synthesis document.\n\n"
            f"ALL FINDINGS:\n{findings_text}\n\n"
            f"Write your comprehensive synthesis as markdown directly to this file:\n"
            f"  {synth_path}\n\n"
            f"After writing the file, notify the server by running:\n"
            f"  curl -s -X PUT http://localhost:{port}/api/hivemind/{hivemind_id}/knowledge/synthesis "
            f'-H "Content-Type: application/json" -d \'{{"notify_only": true}}\'\n\n'
            f"IMPORTANT: Write the file FIRST using the Write tool, then call the curl notification."
        )
    elif task_type == 'replan':
        task_prompt = (
            f"YOUR TASK: Re-evaluate workstream plan and make adjustments.\n\n"
            f"{extra_context}\n\n"
            f"You can update workstreams, create new ones, or adjust priorities. "
            f"Use the API endpoints provided."
        )
    else:
        task_prompt = extra_context or "Review the current state."

    prompt = (
        f"You are the orchestrator of a Hivemind analysis. Complete ONLY the specified task and exit.\n\n"
        f"GOAL: {manifest.get('goal', '')}\n\n"
        f"CURRENT WORKSTREAMS:\n{ws_summary}\n\n"
        f"KNOWLEDGE BASE SUMMARY:\n{synthesis[:2000] if synthesis else '(empty)'}\n\n"
        f"RECENT DECISIONS:\n{decisions_text}\n\n"
        f"{task_prompt}"
    )

    model = manifest.get('config', {}).get('orchestrator_model', '') or 'sonnet'
    cmd = [_resolve_claude(), '-p', prompt, '--model', model, '--max-turns', '5',
           '--print', '--verbose', '--output-format', 'stream-json',
           '--dangerously-skip-permissions']

    session_id = f'hm_orch_{uuid.uuid4().hex[:8]}'

    def _run():
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=pp,
                text=True,
                encoding='utf-8',
                errors='replace',
                creationflags=_POPEN_FLAGS,
                startupinfo=_STARTUPINFO,
            )
            threading.Thread(target=_hide_windows_delayed, args=(proc.pid,), daemon=True).start()
            _register_process(proc, f'Hivemind Orchestrator ({task_type})', 'hivemind_orchestrator',
                              session_id, project_id, f'Hivemind orchestrator: {task_type}')

            session = {
                'proc': proc,
                'status': 'running',
                'task': f'Hivemind orchestrator: {task_type}',
                'log_lines': [],
                'started_at': now_iso(),
                'session_id': session_id,
                'project_id': project_id,
                'mode': 'A',
                'housekeeping': True,
                'hivemind_id': hivemind_id,
                'hivemind_role': 'orchestrator',
                'trigger_type': 'hivemind_orchestrator',
                'trigger_id': hivemind_id,
            }
            mgr = get_manager(project_id)
            mgr.ensure_guardian()
            with mgr.lock:
                agent_sessions[session_id] = session
                mgr.session_ids.add(session_id)

            _read_agent_stream(proc, session)

            # After orchestrator finishes, push SSE update
            _hm_push_sse(hivemind_id, {
                'type': 'hivemind_message',
                'hivemind_id': hivemind_id,
                'message': {
                    'id': 'msg_' + str(uuid.uuid4())[:8],
                    'timestamp': now_iso(),
                    'from': 'orchestrator',
                    'to': 'all',
                    'type': 'status_update',
                    'content': f'Orchestrator {task_type} completed',
                },
            })

        except Exception as e:
            _log(f"[hivemind-orchestrator-cli] error: {e}")
        finally:
            with _hivemind_orch_lock:
                _hivemind_orchestrating.discard(hivemind_id)

    threading.Thread(target=_run, daemon=True).start()
    return session_id


def _hm_auto_spawn_workers(hivemind_id):
    """Auto-spawn workers for ready workstreams (called by orchestrator loop)."""
    manifest = _hm_load_manifest(hivemind_id)
    if not manifest or manifest.get('status') != 'active':
        return

    workstreams = _hm_list_workstreams(hivemind_id)
    max_concurrent = manifest.get('config', {}).get('max_concurrent_workers', 3)

    # Count currently active workers
    active_count = sum(1 for ws in workstreams if ws.get('status') == 'active')
    if active_count >= max_concurrent:
        return

    # Find ready workstreams
    ready = _hm_resolve_dependencies(workstreams)
    slots = max_concurrent - active_count

    for ws in ready[:slots]:
        # Check the agent session is actually still alive
        current_sid = ws.get('current_agent_session_id')
        if current_sid and current_sid in agent_sessions:
            s = agent_sessions[current_sid]
            if s.get('status') == 'running':
                continue  # already has a running worker

        # Spawn via internal call (not HTTP)
        ws_id = ws['id']
        project_id = manifest.get('project_id', '')
        p = load_project(project_id)
        if not p:
            continue
        pp = p.get('project_path', '')
        if not pp or not Path(pp).is_dir():
            continue

        try:
            session_id = _hm_spawn_worker_session(manifest, ws, p, hivemind_id, ws_id)
            ws['status'] = 'active'
            ws['current_agent_session_id'] = session_id
            ws['sessions_used'] = ws.get('sessions_used', 0) + 1
            _hm_save_workstream(hivemind_id, ws_id, ws)
            _hm_push_sse(hivemind_id, {
                'type': 'hivemind_worker_spawned',
                'hivemind_id': hivemind_id,
                'ws_id': ws_id,
                'session_id': session_id,
            })
            _log_agent_activity(project_id, f"Hivemind auto-spawned worker for {ws.get('title', ws_id)}")
        except Exception as e:
            _log(f"[hivemind] Failed to auto-spawn worker for {ws_id}: {e}")


# ── Hivemind API: Message Bus ────────────────────────────────────────────────

@bp.route('/api/hivemind/<hivemind_id>/bus/post', methods=['POST'])
def hivemind_bus_post(hivemind_id):
    """Post a message to the hivemind message bus."""
    manifest = _hm_load_manifest(hivemind_id)
    if not manifest:
        return jsonify({'error': 'not found'}), 404
    data = request.get_json()
    if not data or not data.get('type', '').strip():
        return jsonify({'error': 'type required'}), 400

    msg_type = data['type'].strip()
    msg = {
        'id': 'msg_' + str(uuid.uuid4())[:8],
        'timestamp': now_iso(),
        'from': data.get('from', 'unknown'),
        'to': data.get('to', 'orchestrator'),
        'type': msg_type,
        'content': data.get('content', ''),
        'title': data.get('title', ''),
        'references': data.get('references', []),
    }
    _hm_append_bus_message(hivemind_id, msg)

    # If this is a finding_report, also append to the workstream findings
    if msg_type == 'finding_report' and data.get('from', '').startswith('ws_'):
        ws_id = data['from']
        finding = {
            'id': 'f_' + str(uuid.uuid4())[:8],
            'timestamp': msg['timestamp'],
            'session_id': data.get('session_id', ''),
            'type': 'finding',
            'title': data.get('title', ''),
            'content': data.get('content', ''),
            'confidence': data.get('confidence', 'medium'),
            'evidence': data.get('evidence', ''),
            'tags': data.get('tags', []),
            'user_reviewed': False,
        }
        _hm_append_finding(hivemind_id, ws_id, finding)
        _hm_push_sse(hivemind_id, {
            'type': 'hivemind_finding',
            'hivemind_id': hivemind_id,
            'ws_id': ws_id,
            'finding': finding,
        })

    # If this is an escalation, push escalation SSE event
    if msg_type == 'escalation':
        _hm_push_sse(hivemind_id, {
            'type': 'hivemind_escalation',
            'hivemind_id': hivemind_id,
            'ws_id': data.get('from', ''),
            'message': data.get('content', ''),
            'escalation_id': msg['id'],
        })

    # Push general message event
    _hm_push_sse(hivemind_id, {
        'type': 'hivemind_message',
        'hivemind_id': hivemind_id,
        'message': msg,
    })

    return jsonify({'ok': True, 'message': msg})


@bp.route('/api/hivemind/<hivemind_id>/bus/poll/<ws_id>')
def hivemind_bus_poll(hivemind_id, ws_id):
    """Poll messages directed at a specific workstream."""
    manifest = _hm_load_manifest(hivemind_id)
    if not manifest:
        return jsonify({'error': 'not found'}), 404
    since = request.args.get('since', '')
    messages = _hm_read_bus_messages(hivemind_id, last_n=50, ws_filter=ws_id)
    if since:
        messages = [m for m in messages if m.get('timestamp', '') > since]
    return jsonify(messages)


@bp.route('/api/hivemind/<hivemind_id>/bus/history')
def hivemind_bus_history(hivemind_id):
    """Get full message bus history (paginated)."""
    manifest = _hm_load_manifest(hivemind_id)
    if not manifest:
        return jsonify({'error': 'not found'}), 404
    limit = int(request.args.get('limit', 100))
    messages = _hm_read_bus_messages(hivemind_id, last_n=limit)
    return jsonify(messages)


@bp.route('/api/hivemind/<hivemind_id>/bus/stream')
def hivemind_bus_stream(hivemind_id):
    """SSE stream of all hivemind bus activity."""
    manifest = _hm_load_manifest(hivemind_id)
    if not manifest:
        return jsonify({'error': 'not found'}), 404

    queue = []
    with _hivemind_sse_lock:
        if hivemind_id not in _hivemind_sse_queues:
            _hivemind_sse_queues[hivemind_id] = []
        _hivemind_sse_queues[hivemind_id].append(queue)

    def generate():
        try:
            tick = 0
            while True:
                while queue:
                    event = queue.pop(0)
                    yield f"data: {json.dumps(event)}\n\n"
                tick += 1
                if tick % 50 == 0:
                    yield ": heartbeat\n\n"
                _time.sleep(0.3)
        finally:
            with _hivemind_sse_lock:
                queues = _hivemind_sse_queues.get(hivemind_id, [])
                if queue in queues:
                    queues.remove(queue)

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


# ── Hivemind API: Knowledge Base ─────────────────────────────────────────────

@bp.route('/api/hivemind/<hivemind_id>/knowledge/synthesis')
def hivemind_knowledge_synthesis_get(hivemind_id):
    """Get the current synthesis document."""
    manifest = _hm_load_manifest(hivemind_id)
    if not manifest:
        return jsonify({'error': 'not found'}), 404
    content = _hm_read_synthesis(hivemind_id)
    return jsonify({'content': content, 'updated_at': manifest.get('updated_at')})


@bp.route('/api/hivemind/<hivemind_id>/knowledge/synthesis', methods=['PUT'])
def hivemind_knowledge_synthesis_put(hivemind_id):
    """Update the synthesis document (called by orchestrator CLI sessions)."""
    manifest = _hm_load_manifest(hivemind_id)
    if not manifest:
        return jsonify({'error': 'not found'}), 404
    data = request.get_json() or {}
    # notify_only mode: orchestrator wrote the file directly, just push SSE
    if not data.get('notify_only'):
        content = data.get('content', '')
        if not content:
            content = request.get_data(as_text=True)
        if content:
            _hm_write_synthesis(hivemind_id, content)
    manifest['updated_at'] = now_iso()
    _hm_save_manifest(hivemind_id, manifest)
    _hm_push_sse(hivemind_id, {
        'type': 'hivemind_synthesis',
        'hivemind_id': hivemind_id,
        'updated_at': manifest['updated_at'],
    })
    return jsonify({'ok': True})


@bp.route('/api/hivemind/<hivemind_id>/knowledge/decisions')
def hivemind_knowledge_decisions(hivemind_id):
    """Get all decisions."""
    manifest = _hm_load_manifest(hivemind_id)
    if not manifest:
        return jsonify({'error': 'not found'}), 404
    return jsonify(_hm_read_decisions(hivemind_id))


@bp.route('/api/hivemind/<hivemind_id>/knowledge/findings')
def hivemind_knowledge_findings(hivemind_id):
    """Get all findings across all workstreams."""
    manifest = _hm_load_manifest(hivemind_id)
    if not manifest:
        return jsonify({'error': 'not found'}), 404
    ws_id = request.args.get('ws_id', '')
    if ws_id:
        last_n = int(request.args.get('limit', 50))
        return jsonify(_hm_read_findings(hivemind_id, ws_id, last_n))
    return jsonify(_hm_read_all_findings(hivemind_id))


@bp.route('/api/hivemind/<hivemind_id>/knowledge/questions/<question_id>/resolve', methods=['POST'])
def hivemind_resolve_question(hivemind_id, question_id):
    """Mark an open question as resolved."""
    manifest = _hm_load_manifest(hivemind_id)
    if not manifest:
        return jsonify({'error': 'not found'}), 404
    found = _hm_resolve_question(hivemind_id, question_id)
    if not found:
        return jsonify({'error': 'question not found'}), 404
    return jsonify({'ok': True})


# ── Hivemind API: Escalation & User Intervention ────────────────────────────

@bp.route('/api/hivemind/<hivemind_id>/escalate', methods=['POST'])
def hivemind_escalate(hivemind_id):
    """Post an escalation (called by workers or orchestrator CLI sessions)."""
    manifest = _hm_load_manifest(hivemind_id)
    if not manifest:
        return jsonify({'error': 'not found'}), 404
    data = request.get_json() or {}
    msg = {
        'id': 'esc_' + str(uuid.uuid4())[:8],
        'timestamp': now_iso(),
        'from': data.get('from', 'orchestrator'),
        'to': 'user',
        'type': 'escalation',
        'content': data.get('content', data.get('message', '')),
        'workstream_id': data.get('workstream_id', data.get('from', '')),
        'requires_response': data.get('requires_response', True),
        'resolved': False,
    }
    _hm_append_bus_message(hivemind_id, msg)
    _hm_push_sse(hivemind_id, {
        'type': 'hivemind_escalation',
        'hivemind_id': hivemind_id,
        'ws_id': msg['workstream_id'],
        'message': msg['content'],
        'escalation_id': msg['id'],
    })
    return jsonify({'ok': True, 'escalation': msg})


@bp.route('/api/hivemind/<hivemind_id>/intervene', methods=['POST'])
def hivemind_intervene(hivemind_id):
    """User sends directive to orchestrator or specific workstream."""
    manifest = _hm_load_manifest(hivemind_id)
    if not manifest:
        return jsonify({'error': 'not found'}), 404
    data = request.get_json() or {}
    message = data.get('message', '').strip()
    if not message:
        return jsonify({'error': 'message required'}), 400

    target = data.get('target', 'orchestrator')  # workstream id or 'orchestrator'
    msg = {
        'id': 'msg_' + str(uuid.uuid4())[:8],
        'timestamp': now_iso(),
        'from': 'user',
        'to': target,
        'type': 'directive',
        'content': message,
    }
    _hm_append_bus_message(hivemind_id, msg)
    _hm_push_sse(hivemind_id, {
        'type': 'hivemind_message',
        'hivemind_id': hivemind_id,
        'message': msg,
    })
    return jsonify({'ok': True, 'message': msg})


@bp.route('/api/hivemind/<hivemind_id>/findings/<finding_id>/review', methods=['POST'])
def hivemind_finding_review(hivemind_id, finding_id):
    """User approves/rejects a finding."""
    manifest = _hm_load_manifest(hivemind_id)
    if not manifest:
        return jsonify({'error': 'not found'}), 404
    data = request.get_json() or {}
    approved = data.get('approved', True)
    # Record as a decision
    decision = {
        'id': 'd_' + str(uuid.uuid4())[:8],
        'timestamp': now_iso(),
        'type': 'finding_review',
        'finding_id': finding_id,
        'approved': approved,
        'comment': data.get('comment', ''),
        'decided_by': 'user',
        'user_approved': True,
    }
    _hm_append_decision(hivemind_id, decision)
    return jsonify({'ok': True, 'decision': decision})


@bp.route('/api/hivemind/<hivemind_id>/decisions/<decision_id>/approve', methods=['POST'])
def hivemind_decision_approve(hivemind_id, decision_id):
    """User approves/rejects a decision."""
    manifest = _hm_load_manifest(hivemind_id)
    if not manifest:
        return jsonify({'error': 'not found'}), 404
    data = request.get_json() or {}
    review = {
        'id': 'd_' + str(uuid.uuid4())[:8],
        'timestamp': now_iso(),
        'type': 'decision_review',
        'original_decision_id': decision_id,
        'approved': data.get('approved', True),
        'comment': data.get('comment', ''),
        'decided_by': 'user',
        'user_approved': True,
    }
    _hm_append_decision(hivemind_id, review)
    return jsonify({'ok': True, 'review': review})


# ── Hivemind: Server Orchestrator (background thread) ────────────────────────
# _hivemind_orchestrator_stop moved to mc/state.py (Phase 0).


def _hivemind_orchestrator_loop():
    """Background daemon: evaluate hivemind states, resolve dependencies,
    and schedule worker spawns. Runs every 10 seconds."""
    while not _hivemind_orchestrator_stop.is_set():
        obs.heartbeat('hivemind-orchestrator')  # Phase 2: loop liveness -> /api/system/loops
        try:
            if not HIVEMIND_DIR.exists():
                _hivemind_orchestrator_stop.wait(10)
                continue

            for d in HIVEMIND_DIR.iterdir():
                if not d.is_dir() or d.name.startswith('_'):
                    continue
                manifest = _hm_load_manifest(d.name)
                if not manifest or manifest.get('status') != 'active':
                    continue

                hivemind_id = manifest['id']
                workstreams = _hm_list_workstreams(hivemind_id)
                if not workstreams:
                    continue

                # Detect finished workers: workstreams marked 'active' whose agent session
                # is no longer running → update to completed or failed
                for ws in workstreams:
                    if ws.get('status') != 'active':
                        continue
                    sid = ws.get('current_agent_session_id')
                    if not sid or sid not in agent_sessions:
                        continue
                    s = agent_sessions[sid]
                    if s.get('status') in ('completed', 'error'):
                        # Worker finished — if workstream wasn't explicitly marked,
                        # push a worker_done event
                        _hm_push_sse(hivemind_id, {
                            'type': 'hivemind_worker_done',
                            'hivemind_id': hivemind_id,
                            'ws_id': ws['id'],
                            'session_id': sid,
                            'status': s.get('status', 'completed'),
                        })
                        ws['last_agent_session_id'] = sid
                        ws['current_agent_session_id'] = None
                        # Auto-mark workstream completed on agent success
                        if s.get('status') == 'completed' and ws.get('status') == 'active':
                            ws['status'] = 'completed'
                            if not ws.get('completed_at'):
                                ws['completed_at'] = now_iso()
                        elif s.get('status') == 'error' and ws.get('status') == 'active':
                            retry_count = ws.get('retry_count', 0)
                            max_retries = manifest.get('config', {}).get('max_retries_per_workstream', 2)
                            if retry_count < max_retries:
                                ws['retry_count'] = retry_count + 1
                                ws['status'] = 'pending'  # will be auto-spawned next tick
                            else:
                                ws['status'] = 'failed'
                        _hm_save_workstream(hivemind_id, ws['id'], ws)

                # Re-read workstreams after potential updates
                workstreams = _hm_list_workstreams(hivemind_id)

                # Check for blocked workstreams that are now unblocked
                completed_ids = {ws['id'] for ws in workstreams if ws.get('status') == 'completed'}
                for ws in workstreams:
                    if ws.get('status') == 'blocked':
                        deps = ws.get('dependencies', [])
                        if all(dep in completed_ids for dep in deps):
                            ws['status'] = 'pending'
                            _hm_save_workstream(hivemind_id, ws['id'], ws)
                            _hm_push_sse(hivemind_id, {
                                'type': 'hivemind_workstream',
                                'hivemind_id': hivemind_id,
                                'ws_id': ws['id'],
                                'status': 'pending',
                            })

                # Auto-spawn workers for ready workstreams
                _hm_auto_spawn_workers(hivemind_id)

                # Check if all workstreams are completed
                workstreams = _hm_list_workstreams(hivemind_id)
                all_completed = all(ws.get('status') in ('completed', 'failed') for ws in workstreams)
                if all_completed and workstreams:
                    manifest['status'] = 'completed'
                    manifest['updated_at'] = now_iso()
                    _hm_save_manifest(hivemind_id, manifest)
                    _hm_push_sse(hivemind_id, {
                        'type': 'hivemind_status',
                        'hivemind_id': hivemind_id,
                        'status': 'completed',
                    })
                    # Trigger final synthesis
                    _hm_dispatch_orchestrator(hivemind_id, 'synthesize')

        except Exception as e:
            _log(f"[hivemind-orchestrator] Error: {e}")

        _hivemind_orchestrator_stop.wait(10)


def _start_hivemind_orchestrator():
    """Start the hivemind orchestrator background thread."""
    t = threading.Thread(target=_hivemind_orchestrator_loop, daemon=True)
    t.start()


# ── Hivemind: trigger-aware run history (straggler route — lived in the
#    run-history section of server.py, moved with its family at 1.10) ──────

@bp.route('/api/hivemind/<hivemind_id>/runs')
def hivemind_runs(hivemind_id):
    """Return paginated agent_log entries for this hivemind.

    Query params:
      role=orchestrator|worker  (default: both)
      ws_id=<workstream_id>     (default: any)
      limit=<n>                 page size (default 50, max 200)
      offset=<n>                rows to skip (default 0)

    Response shape: {runs, total, offset, limit}.
    """
    role = request.args.get('role', '')
    ws_id = request.args.get('ws_id', '')
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

    manifest = _hm_load_manifest(hivemind_id)
    if not manifest:
        return jsonify({'error': 'hivemind not found'}), 404
    pid = manifest.get('project_id', '')
    if not pid:
        return jsonify({'runs': [], 'total': 0, 'offset': 0, 'limit': limit})
    log = _load_agent_log(pid)
    runs = [e for e in log if e.get('hivemind_id') == hivemind_id]
    if role == 'orchestrator':
        runs = [e for e in runs if e.get('hivemind_role') == 'orchestrator']
    elif role == 'worker':
        runs = [e for e in runs if e.get('hivemind_role') != 'orchestrator']
    if ws_id:
        runs = [e for e in runs if e.get('hivemind_ws_id') == ws_id]
    total = len(runs)
    page = runs[offset:offset + limit]
    return jsonify({
        'runs': _enrich_run_entries(page),
        'total': total,
        'offset': offset,
        'limit': limit,
    })
