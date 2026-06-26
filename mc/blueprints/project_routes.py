"""Project CRUD endpoints — blueprint 1.11 (MODERNIZATION_PLAN.md).

Moved VERBATIM from server.py (app-to-bp route-decorator swap; CONFIG reads
rewritten to state.CONFIG — the 1.7/1.9 precedent). 32 routes (plan table
said 48 — that count included /api/project-prefixed routes that belong to
OTHER families feature-wise and either moved with them already (mcp-enabled
1.4, distiller 1.5, terminal/status 1.8, scribe-stats + memory/search 1.9)
or stay for 1.12 dispatch (the 11 agent/* routes, transcript, reconstruct,
search-chats, conversations, plans)):

  • the project-record CRUD core: /api/projects, POST+DELETE
    /api/project/<id>, generate_summary, import
  • backlog CRUD ×5 (+ _append_note_to_backlog_item, its only caller)
  • github ×4 + code-sync ×5 glue (project-record mutations over the
    github_sync / project_sync modules — imported directly, top-level
    modules, the 1.3 precedent; their register() wiring stays in server.py)
  • attachments upload/delete + /api/attachments/<name> + /api/serve-image
    (+ the upload-quota helpers, shimmed back for agent_upload_image)
  • rules ×4 (project AGENT_RULES.md + shared SHARED_RULES.md editor CRUD)
  • memory editor-CRUD trio (pure file read/write over _get_memory_path —
    per the 1.9 scoping call; it does NOT touch the locked managed-region
    writers, which stay in server.py untouched)
  • /api/projects/order + /api/grid-layout (projects-grid layout)
  • _project_live_agent (single caller is /api/projects) and
    _log_agent_activity (pure project-record activity_log writer; the ~25
    dispatch/github call sites resolve the server.py inbound shim)

load_projects() carries the LOAD-BEARING sidecar suffix-exclusion
(EXCLUDED_SIDECAR_SUFFIXES) — see CLAUDE.md "LOAD-BEARING RULE — DATA_DIR
pollution" and tests/test_load_projects_sidecar_exclusions.py (which reads
both names through the server.py shims).
"""

import json
import os
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any, Callable

from flask import Blueprint, abort, jsonify, request, send_file

from mc import state
from mc.core import _log, file_type, now_iso, time_ago
from mc.state import (
    _backlog_sync_lock,
    agent_sessions,
    terminal_lock,
    terminal_sessions,
)

import github_sync as _gh_sync
import project_sync as _proj_sync

# Cross-blueprint import (the 1.4/1.5 _resolve_project_path_or_400 precedent):
# delete_project kills this project's terminal sessions via the terminal
# family's killer. Called at request time only — terminal_routes is wired by
# server.py long before the first request.
from mc.blueprints.terminal_routes import _kill_terminal_session

bp = Blueprint('project_routes', __name__)

# ── wired by server.py (see wire()) ──────────────────────────────────────────
DATA_DIR: Path = None  # type: ignore[assignment]
_DATA_ROOT: Path = None  # type: ignore[assignment]
UPLOADS_DIR: Path = None  # type: ignore[assignment]
PROJECTS_BASE: Path = None  # type: ignore[assignment]
SHARED_RULES_PATH: Path = None  # type: ignore[assignment]
_get_memory_path: Callable[[dict], Path] = None  # type: ignore[assignment]
_resolve_claude: Callable[[], str] = None  # type: ignore[assignment]
get_manager: Callable[[str], Any] = None  # type: ignore[assignment]
_unregister_process: Callable[[int], None] = None  # type: ignore[assignment]
_POPEN_FLAGS: int = 0
_STARTUPINFO: Any = None


def wire(*, data_dir, data_root, uploads_dir, projects_base,
         shared_rules_path, get_memory_path_fn, resolve_claude_fn,
         get_manager_fn, unregister_process_fn, popen_flags, startupinfo):
    """Late-bind the path constants (they stay in server.py — many families
    still read them there) and the cross-family fns: _get_memory_path is
    shared with the Scribe/condense machinery, _resolve_claude + the Popen
    consts + get_manager + _unregister_process are dispatch family (1.12)."""
    global DATA_DIR, _DATA_ROOT, UPLOADS_DIR, PROJECTS_BASE, SHARED_RULES_PATH
    global _get_memory_path, _resolve_claude, get_manager, _unregister_process
    global _POPEN_FLAGS, _STARTUPINFO
    DATA_DIR = data_dir
    _DATA_ROOT = data_root
    UPLOADS_DIR = uploads_dir
    PROJECTS_BASE = projects_base
    SHARED_RULES_PATH = shared_rules_path
    _get_memory_path = get_memory_path_fn
    _resolve_claude = resolve_claude_fn
    get_manager = get_manager_fn
    _unregister_process = unregister_process_fn
    _POPEN_FLAGS = popen_flags
    _STARTUPINFO = startupinfo


# ── Project-record store (load/save/list + attachment decoration) ────────────

_ATTACHMENT_RUNTIME_FIELDS = ('_present',)


def _decorate_attachments(project):
    """Decorate backlog-item attachments with runtime presence flags.

    Each attachment gets `_present: bool` based on whether its stored file
    still exists on disk. Lets the SPA skip <img> requests for orphaned
    records instead of generating console-error noise on 404. The flag is
    stripped before save (see save_project) so it never pollutes the JSON.
    """
    if not isinstance(project, dict):
        return project
    for item in project.get('backlog', []) or []:
        for att in item.get('attachments', []) or []:
            try:
                att['_present'] = (UPLOADS_DIR / att.get('stored_name', '')).is_file()
            except Exception:
                att['_present'] = False
    return project


def load_project(project_id):
    filepath = DATA_DIR / f'{project_id}.json'
    if not filepath.exists():
        return None
    return _decorate_attachments(json.loads(filepath.read_text(encoding='utf-8')))


def save_project(project_id, data):
    # Strip runtime-only attachment fields (e.g. `_present`) before persisting
    # so they never leak into the JSON. See _decorate_attachments.
    if isinstance(data, dict):
        for item in data.get('backlog', []) or []:
            for att in item.get('attachments', []) or []:
                for k in _ATTACHMENT_RUNTIME_FIELDS:
                    att.pop(k, None)
    filepath = DATA_DIR / f'{project_id}.json'
    filepath.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')


# LOAD-BEARING: every per-project sidecar file MUST be listed here, OR be
# moved outside DATA_DIR entirely. A stray non-project JSON here 500s
# _get_active_restart_blockers and the restart endpoints. See CLAUDE.md
# "LOAD-BEARING RULE — DATA_DIR pollution" and the parametric regression
# test at tests/test_load_projects_sidecar_exclusions.py (Seat 4 v2 Cond 6
# closure — single source of truth, parametric + next-sidecar canary).
EXCLUDED_SIDECAR_SUFFIXES = (
    '_agent_log.json',
    '_scribe_stats.json',
    '_router_stats.json',
    '_skill_stats.json',           # Phase 4 Distiller — D9 closure
    '_skill_stats_summary.json',   # Phase 4 Distiller cache — D3 closure
)


def load_projects():
    projects = []
    for f in DATA_DIR.glob('*.json'):
        if f.name.endswith(EXCLUDED_SIDECAR_SUFFIXES):
            continue
        try:
            p = json.loads(f.read_text(encoding='utf-8'))
            if not isinstance(p, dict):
                continue
            p.setdefault('status', 'unknown')
            p.setdefault('blocked', False)
            p.setdefault('activity_log', [])
            p.setdefault('current_task', '')
            p.setdefault('next_action', '')
            p.setdefault('domain', 'general')
            p.setdefault('blocked_reason', None)
            p.setdefault('backlog', [])
            p.setdefault('project_path', '')
            # Phase 4 Distiller per-project defaults (v2.1 §11 — I5 closure).
            # Mirrors the current_task / next_action precedent. Written through
            # on first session-end touch or first Settings-modal open via save_project.
            p.setdefault('distiller_mode', 'proposed')
            p.setdefault('distiller_min_recurrence', 3)
            p.setdefault('distiller_max_topics_per_session', 3)
            p.setdefault('distiller_max_preferences_per_session', 3)
            p.setdefault('distiller_max_explorations_per_session', 3)
            p.setdefault('distiller_min_turns', 5)
            p.setdefault('distiller_skip_errors', True)
            # Beacon staleness cadence (hours). 0 = no cadence expectation →
            # never flagged `stale` (avoids flooding the digest with
            # legitimately-dormant projects). Set >0 on projects you expect
            # regular activity from (e.g. scheduled scanners).
            p.setdefault('beacon_cadence_hours', 0)
            # Durable per-CONVERSATION pins: a list of Claude session ids
            # (claude_session_id) for the individual chats the user pinned —
            # chat-level, NOT a whole-project flag. Keyed on claude_session_id
            # because that's the conversation identity that survives MC's
            # internal session-id churn on revival, so a pin persists across
            # restarts; stored server-side (not per-browser localStorage) so it
            # is identical on every interface. A project is treated as "pinned"
            # in the chat list iff this list is non-empty. Distinct from the
            # modal-collapse `unpinned` pref and the status-based asking/stuck
            # sort tier — both unrelated uses of the word "pin".
            if not isinstance(p.get('pinned_conversations'), list):
                p['pinned_conversations'] = []
            _decorate_attachments(p)
            projects.append(p)
        except Exception as e:
            _log(f"Error loading {f}: {e}")
    projects.sort(key=lambda p: (p.get('display_order', 9999), p.get('last_updated', '1970-01-01T00:00:00Z')))
    # Secondary sort: within same display_order, most recently updated first
    projects.sort(key=lambda p: p.get('last_updated', '1970-01-01T00:00:00Z'), reverse=True)
    projects.sort(key=lambda p: p.get('display_order', 9999))
    return projects


def _project_live_agent(project_id):
    """Server-authoritative live-agent state for a project, from the in-memory
    agent_sessions map (the source of truth — fresh for ALL projects, every
    poll).

    The client's per-project agentHistory is only refreshed when that
    project's modal is open, so for a closed project computeLiveStatus()
    falls back to a stale errored session and mislabels an actively-running
    project as "Error/stuck" with no live presence. Surfacing this on the
    regularly-polled /api/projects lets friendlyStatus() trust server truth
    instead. Returns {'state', 'task'} or None.

    Priority: asking (needs the user) > working (a turn is running) > idle
    (process alive between turns). Housekeeping/incognito sessions are
    excluded so the public indicator respects incognito gating.

    `reason` distinguishes the asking sub-state ('plan' = awaiting plan
    approval, 'question' = awaiting an answer, else None) so the client can
    label a CLOSED project's attention item correctly without its
    lazily-refreshed agentStatusCache (which is only fresh for projects
    whose modal this client has open — the same staleness this helper exists
    to defeat).
    """
    best = None  # 0=idle, 1=working, 2=asking
    rank = {'idle': 0, 'working': 1, 'asking': 2}
    for s in agent_sessions.values():
        if s.get('project_id') != project_id:
            continue
        if s.get('housekeeping') or s.get('incognito'):
            continue
        st = s.get('status')
        if st not in ('running', 'idle'):
            continue
        reason = None
        if s.get('waiting_for_plan_approval'):
            state, reason = 'asking', 'plan'   # turn done, awaiting approval
        elif s.get('waiting_for_question'):
            state, reason = 'asking', 'question'  # awaiting an answer
        elif st == 'running':
            state = 'working'  # a turn is actively running
        else:
            state = 'idle'     # process alive, between turns, not waiting
        if best is None or rank[state] > rank[best['state']]:
            best = {'state': state, 'reason': reason,
                    'task': (s.get('task') or '').strip()[:80]}
    return best


def _log_agent_activity(project_id, msg, bump_updated=True):
    """Add an entry to the project's activity_log.

    bump_updated: when True (default) also refresh `last_updated`, which drives
    the recency sort in both the desktop list and the mobile chat list. Pass
    False for background machinery (e.g. GitHub auto-sync) that should be
    *logged* without floating the project to the top of the recency sort.
    """
    p = load_project(project_id)
    if not p:
        return
    log = p.setdefault('activity_log', [])
    log.insert(0, {'ts': now_iso(), 'msg': msg})
    p['activity_log'] = log[:20]
    if bump_updated:
        p['last_updated'] = now_iso()
    save_project(project_id, p)


# ── Project endpoints ────────────────────────────────────────────────────────

@bp.route('/api/projects')
def api_projects():
    projects = load_projects()
    for p in projects:
        p['last_updated_relative'] = time_ago(p.get('last_updated'))
        p['live_agent'] = _project_live_agent(p.get('id'))
        for entry in p.get('activity_log', []):
            entry['ts_relative'] = time_ago(entry.get('ts'))
        # Backlog note/attachment BODIES dominate this payload (notes alone were
        # ~1.4 MB on a single heavy project) yet the dashboard list needs only
        # their COUNTS — bodies render solely in an open project modal, which
        # lazy-loads the full backlog via /api/project/<id>/backlog on open.
        # Trim bodies to counts so this regularly-polled list stays small.
        # (load_projects() returns fresh per-request dicts, so mutating is safe.
        # The old per-item ts_relative was also dead compute — nothing rendered it.)
        for item in p.get('backlog', []):
            item['notes_count'] = len(item.get('notes') or [])
            item['attachments_count'] = len(item.get('attachments') or [])
            item.pop('notes', None)
            item.pop('attachments', None)
    return jsonify(projects)


@bp.route('/api/project/<project_id>', methods=['POST'])
def update_project(project_id):
    data = request.get_json()
    if not data:
        return jsonify({'error': 'no data'}), 400

    filepath = DATA_DIR / f'{project_id}.json'
    is_new = not filepath.exists()
    existing = json.loads(filepath.read_text(encoding='utf-8')) if not is_new else {'id': project_id}
    existing.setdefault('backlog', [])

    # ── Auto-create a dedicated workspace folder when creating a project with no path.
    if is_new:
        provided_path = (data.get('project_path') or '').strip()
        if not provided_path:
            base = Path(state.CONFIG.get('auto_workspace_base') or str(Path.home() / 'MissionControl'))
            try:
                base.mkdir(parents=True, exist_ok=True)
                candidate = base / project_id
                n = 1
                while candidate.exists():
                    candidate = base / f'{project_id}_{n}'
                    n += 1
                candidate.mkdir(parents=True, exist_ok=True)
                data['project_path'] = str(candidate)
            except Exception as e:
                return jsonify({'error': f'could not create workspace folder: {e}'}), 500

    # ── Prevent two projects from sharing the same folder.
    candidate_path = (data.get('project_path') or '').strip()
    if candidate_path:
        try:
            resolved = str(Path(candidate_path).resolve()).lower() if os.name == 'nt' else str(Path(candidate_path).resolve())
        except Exception:
            resolved = candidate_path
        for pf in DATA_DIR.glob('*.json'):
            if pf.stem == project_id or pf.stem.endswith('_agent_log'):
                continue
            try:
                with open(pf, encoding='utf-8') as f:
                    other = json.load(f)
                op = (other.get('project_path') or '').strip()
                if not op:
                    continue
                other_resolved = str(Path(op).resolve()).lower() if os.name == 'nt' else str(Path(op).resolve())
                if other_resolved == resolved:
                    name = other.get('name') or pf.stem
                    return jsonify({'error': f'Path already used by project "{name}". Each project needs its own folder.'}), 409
            except Exception:
                continue

    for k, v in data.items():
        if k not in ('log_msg', 'backlog'):
            existing[k] = v

    existing['last_updated'] = now_iso()

    if 'log_msg' in data:
        log = existing.setdefault('activity_log', [])
        log.insert(0, {'ts': existing['last_updated'], 'msg': data['log_msg']})
        existing['activity_log'] = log[:20]

    save_project(project_id, existing)

    return jsonify({'ok': True, 'id': project_id})


@bp.route('/api/project/<project_id>/conversation-pin', methods=['POST'])
def set_conversation_pin(project_id):
    """Pin / unpin a SINGLE conversation within a project — chat-level, not the
    whole project.

    Keys on the Claude session id (`conversation_id` == claude_session_id), the
    stable conversation identity that survives MC's internal session-id churn on
    revival, so the pin persists across restarts; stored in the project JSON so
    it is identical on every interface. Deliberately does NOT touch
    `last_updated`: pinning is a view preference, not activity.

    Body: {"conversation_id": "<claude_session_id>", "pinned": true|false};
    omit "pinned" to toggle.
    """
    filepath = DATA_DIR / f'{project_id}.json'
    if not filepath.exists():
        return jsonify({'error': 'project not found'}), 404
    body = request.get_json(silent=True) or {}
    csid = (body.get('conversation_id') or '').strip()
    if not csid:
        return jsonify({'error': 'conversation_id required'}), 400
    existing = json.loads(filepath.read_text(encoding='utf-8'))
    pinned = existing.get('pinned_conversations')
    if not isinstance(pinned, list):
        pinned = []
    is_pinned = csid in pinned
    want = bool(body['pinned']) if 'pinned' in body else (not is_pinned)
    if want and not is_pinned:
        pinned.append(csid)
    elif not want and is_pinned:
        pinned = [c for c in pinned if c != csid]
    existing['pinned_conversations'] = pinned
    save_project(project_id, existing)
    return jsonify({'ok': True, 'pinned_conversations': pinned, 'pinned': csid in pinned})


@bp.route('/api/project/<project_id>/generate_summary', methods=['POST'])
def generate_project_summary(project_id):
    """Use Claude to pick an emoji and write a one-line summary for the project."""
    p = load_project(project_id)
    if not p:
        return jsonify({'error': 'project not found'}), 404

    body = request.get_json(silent=True) or {}
    overwrite_emoji = bool(body.get('overwrite_emoji'))

    name = p.get('name') or project_id
    description = (p.get('description') or '').strip()
    domain = p.get('domain') or 'general'
    activity = p.get('activity_log', [])[:5]
    activity_str = '\n'.join(f"- {a.get('msg', '')}" for a in activity if a.get('msg'))

    prompt = (
        "You are generating a project profile for a dashboard. "
        "Return ONLY a JSON object (no markdown, no code fences, no other text) "
        "with exactly two fields:\n"
        '- "emoji": a single emoji character that matches the project theme\n'
        '- "summary": one short sentence (12-20 words) describing what the project is about\n\n'
        f"Project name: {name}\n"
        f"Description: {description or '(none)'}\n"
        f"Domain: {domain}\n"
        f"Recent activity:\n{activity_str or '(no activity yet)'}\n\n"
        'Example: {"emoji":"\u26bd","summary":"Tracks soccer match results and ranks teams across league tables."}'
    )

    model = state.CONFIG.get('condense_model', '') or 'haiku'
    cmd = [_resolve_claude(), '-p', prompt, '--model', model, '--output-format', 'json',
           '--dangerously-skip-permissions']

    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=30,
            creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
        )
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'generation timed out after 30s'}), 504
    except FileNotFoundError:
        return jsonify({'error': 'claude CLI not found'}), 500

    if result.returncode != 0:
        return jsonify({'error': f'claude exited {result.returncode}: {(result.stderr or result.stdout)[:200]}'}), 500

    # Parse Claude CLI's JSON envelope -> model's JSON content
    try:
        envelope = json.loads(result.stdout)
        content = (envelope.get('result') or '').strip()
        # Strip optional ```json fences if the model added them despite instructions
        if content.startswith('```'):
            lines = content.splitlines()
            if lines and lines[0].startswith('```'):
                lines = lines[1:]
            if lines and lines[-1].strip() == '```':
                lines = lines[:-1]
            content = '\n'.join(lines).strip()
        data = json.loads(content)
    except (json.JSONDecodeError, KeyError, AttributeError) as e:
        return jsonify({
            'error': f'could not parse model output: {e}',
            'raw': (result.stdout or '')[:500],
        }), 500

    emoji = (data.get('emoji') or '').strip()
    summary = (data.get('summary') or '').strip()

    if emoji and (overwrite_emoji or not p.get('emoji')):
        p['emoji'] = emoji
    if summary:
        p['summary'] = summary
    p['last_updated'] = now_iso()
    save_project(project_id, p)

    return jsonify({
        'ok': True,
        'emoji': p.get('emoji', ''),
        'summary': p.get('summary', ''),
    })


@bp.route('/api/project/<project_id>', methods=['DELETE'])
def delete_project(project_id):
    filepath = DATA_DIR / f'{project_id}.json'
    if not filepath.exists():
        return jsonify({'error': 'not found'}), 404

    # Clean up attachment files
    p = load_project(project_id)
    if p:
        for item in p.get('backlog', []):
            for att in item.get('attachments', []):
                att_path = UPLOADS_DIR / att['stored_name']
                if att_path.exists():
                    att_path.unlink()

    # Remove agent log file if exists
    agent_log = DATA_DIR / f'{project_id}_agent_log.json'
    if agent_log.exists():
        agent_log.unlink()

    # Kill any running agent sessions for this project
    with get_manager(project_id).lock:
        to_remove = [sid for sid, s in agent_sessions.items() if s['project_id'] == project_id]
        for sid in to_remove:
            session = agent_sessions[sid]
            if session['status'] == 'running' and session.get('proc'):
                try:
                    session['proc'].kill()
                except Exception:
                    pass
                _unregister_process(session['proc'].pid)
            agent_sessions.pop(sid, None)

    # Kill any running terminal sessions for this project
    with terminal_lock:
        to_remove = [sid for sid, s in terminal_sessions.items() if s['project_id'] == project_id]
        for sid in to_remove:
            session = terminal_sessions[sid]
            if session['status'] == 'running':
                _kill_terminal_session(session)
            terminal_sessions.pop(sid, None)

    # Delete project file
    filepath.unlink()
    return jsonify({'ok': True})


# ── Backlog endpoints ────────────────────────────────────────────────────────

@bp.route('/api/project/<project_id>/backlog', methods=['GET'])
def get_backlog(project_id):
    p = load_project(project_id)
    if p is None:
        return jsonify({'error': 'not found'}), 404
    return jsonify(p.get('backlog', []))


@bp.route('/api/project/<project_id>/backlog', methods=['POST'])
def add_backlog_item(project_id):
    data = request.get_json()
    if not data or not data.get('text', '').strip():
        return jsonify({'error': 'text required'}), 400

    p = load_project(project_id)
    if p is None:
        return jsonify({'error': 'not found'}), 404

    item = {
        'id': str(uuid.uuid4())[:8],
        'text': data['text'].strip(),
        'priority': data.get('priority', 'normal'),
        'status': 'open',
        'created_at': now_iso(),
        'done_at': None,
        'source': data.get('source', 'dashboard'),
        'attachments': [],
    }

    backlog = p.setdefault('backlog', [])
    backlog.insert(0, item)
    p['last_updated'] = now_iso()
    save_project(project_id, p)
    return jsonify({'ok': True, 'item': item})


@bp.route('/api/project/<project_id>/backlog/<item_id>', methods=['PATCH'])
def update_backlog_item(project_id, item_id):
    data = request.get_json()
    if not data:
        return jsonify({'error': 'no data'}), 400

    p = load_project(project_id)
    if p is None:
        return jsonify({'error': 'project not found'}), 404

    backlog = p.get('backlog', [])
    item = next((i for i in backlog if i['id'] == item_id), None)
    if item is None:
        return jsonify({'error': 'item not found'}), 404

    if 'text' in data:
        item['text'] = data['text'].strip()
    if 'priority' in data:
        item['priority'] = data['priority']
    if 'status' in data:
        item['status'] = data['status']
        if data['status'] == 'done' and not item.get('done_at'):
            item['done_at'] = now_iso()
        elif data['status'] == 'open':
            item['done_at'] = None

    p['last_updated'] = now_iso()
    save_project(project_id, p)
    return jsonify({'ok': True, 'item': item})


def _append_note_to_backlog_item(project_id, item_id, text, agent_code='user'):
    """Append a dated, signed note to a backlog item. Returns True on success."""
    text = (text or '').strip()
    if not text or not project_id or not item_id:
        return False
    with _backlog_sync_lock:
        try:
            p = load_project(project_id)
        except Exception:
            return False
        if p is None:
            return False
        for it in p.get('backlog', []) or []:
            if it.get('id') == item_id:
                notes = it.setdefault('notes', [])
                notes.append({
                    'ts': now_iso(),
                    'agent_code': (agent_code or 'user')[:32],
                    'text': text[:2000],
                })
                if len(notes) > 50:
                    it['notes'] = notes[-50:]
                it['updated_at'] = now_iso()
                p['last_updated'] = now_iso()
                try:
                    save_project(project_id, p)
                except Exception:
                    return False
                return True
        return False


@bp.route('/api/project/<project_id>/backlog/<item_id>/note', methods=['POST'])
def add_backlog_note(project_id, item_id):
    data = request.get_json() or {}
    text = (data.get('text') or '').strip()
    if not text:
        return jsonify({'error': 'text required'}), 400
    agent_code = (data.get('agent_code') or 'user').strip() or 'user'
    if _append_note_to_backlog_item(project_id, item_id, text, agent_code):
        return jsonify({'ok': True})
    return jsonify({'error': 'item not found'}), 404


@bp.route('/api/project/<project_id>/backlog/<item_id>', methods=['DELETE'])
def delete_backlog_item(project_id, item_id):
    p = load_project(project_id)
    if p is None:
        return jsonify({'error': 'not found'}), 404

    # Also delete any attachments for this item
    item = next((i for i in p.get('backlog', []) if i['id'] == item_id), None)
    if item:
        for att in item.get('attachments', []):
            att_path = UPLOADS_DIR / att['stored_name']
            if att_path.exists():
                att_path.unlink()

    before = len(p.get('backlog', []))
    p['backlog'] = [i for i in p.get('backlog', []) if i['id'] != item_id]
    if len(p['backlog']) == before:
        return jsonify({'error': 'item not found'}), 404

    p['last_updated'] = now_iso()
    save_project(project_id, p)
    return jsonify({'ok': True})


# ── GitHub sync endpoints ────────────────────────────────────────────────────

@bp.route('/api/project/<project_id>/github/setup', methods=['POST'])
def github_setup(project_id):
    """Validate repo, save config, trigger initial sync."""
    p = load_project(project_id)
    if p is None:
        return jsonify({'error': 'not found'}), 404
    data = request.get_json() or {}
    repo = (data.get('repo') or '').strip()
    if not repo:
        return jsonify({'error': 'repo required'}), 400

    ok, err = _gh_sync.validate_repo(repo)
    if not ok:
        return jsonify({'error': err}), 400

    p['github_repo'] = repo
    p['github_sync_enabled'] = True
    p['last_updated'] = now_iso()
    save_project(project_id, p)
    _log_agent_activity(project_id, f"GitHub: Connected to {repo}")

    # Trigger initial sync in background
    def _initial():
        _gh_sync.sync_project(project_id)
    threading.Thread(target=_initial, daemon=True).start()

    return jsonify({'ok': True, 'repo': repo})


@bp.route('/api/project/<project_id>/github/disconnect', methods=['POST'])
def github_disconnect(project_id):
    p = load_project(project_id)
    if p is None:
        return jsonify({'error': 'not found'}), 404
    repo = p.get('github_repo', '')
    p['github_sync_enabled'] = False
    p['github_repo'] = ''
    p['github_last_sync'] = None
    p['last_updated'] = now_iso()
    save_project(project_id, p)
    if repo:
        _log_agent_activity(project_id, f"GitHub: Disconnected from {repo}")
    return jsonify({'ok': True})


@bp.route('/api/project/<project_id>/github/sync', methods=['POST'])
def github_sync_now(project_id):
    p = load_project(project_id)
    if p is None:
        return jsonify({'error': 'not found'}), 404
    ok, summary = _gh_sync.sync_project(project_id)
    if not ok:
        return jsonify({'error': summary}), 429 if 'Rate' in summary else 400
    return jsonify({'ok': True, 'summary': summary})


@bp.route('/api/project/<project_id>/github/status')
def github_status(project_id):
    p = load_project(project_id)
    if p is None:
        return jsonify({'error': 'not found'}), 404
    return jsonify({
        'repo': p.get('github_repo', ''),
        'enabled': p.get('github_sync_enabled', False),
        'last_sync': p.get('github_last_sync'),
    })


# ── Code sync endpoints (spike — read-only) ─────────────────────────────────

@bp.route('/api/project/<project_id>/code-sync/enable', methods=['POST'])
def code_sync_enable(project_id):
    """Turn on code sync for a project. Creates the hidden worktree on
    the sync branch and pushes it to the remote (best-effort)."""
    if load_project(project_id) is None:
        return jsonify({'error': 'not found'}), 404
    ok, msg = _proj_sync.enable(project_id)
    if not ok:
        return jsonify({'error': msg}), 400
    return jsonify({'ok': True, 'message': msg})


@bp.route('/api/project/<project_id>/code-sync/disable', methods=['POST'])
def code_sync_disable(project_id):
    if load_project(project_id) is None:
        return jsonify({'error': 'not found'}), 404
    ok, msg = _proj_sync.disable(project_id)
    if not ok:
        return jsonify({'error': msg}), 400
    return jsonify({'ok': True, 'message': msg})


@bp.route('/api/project/<project_id>/code-sync/sync', methods=['POST'])
def code_sync_sync_now(project_id):
    if load_project(project_id) is None:
        return jsonify({'error': 'not found'}), 404
    ok, summary = _proj_sync.sync_now(project_id)
    if not ok:
        return jsonify({'error': summary}), 429 if 'rate limited' in summary else 400
    return jsonify({'ok': True, 'summary': summary})


@bp.route('/api/project/<project_id>/code-sync/status')
def code_sync_status(project_id):
    p = load_project(project_id)
    if p is None:
        return jsonify({'error': 'not found'}), 404
    return jsonify(_proj_sync.compute_status(p))


@bp.route('/api/project/<project_id>/code-sync/dismiss', methods=['POST'])
def code_sync_dismiss(project_id):
    """Reject a remote commit so it stops appearing in incoming. Spike
    has no Accept yet — Reject is the only review action wired so far."""
    if load_project(project_id) is None:
        return jsonify({'error': 'not found'}), 404
    data = request.get_json() or {}
    sha = (data.get('sha') or '').strip()
    ok, msg = _proj_sync.dismiss_commit(project_id, sha)
    if not ok:
        return jsonify({'error': msg}), 400
    return jsonify({'ok': True, 'message': msg})


# ── Attachment endpoints ─────────────────────────────────────────────────────

# P2-2 (IMPROVEMENT_PLAN_V2.md): per-project upload quota.

def _upload_limit(project, key):
    """Resolve an upload limit: per-project override → global config → 0.
    0 (or missing/invalid) means unlimited."""
    val = None
    if project is not None:
        val = project.get(key)
    if val is None:
        val = state.CONFIG.get(key, 0)
    try:
        val = int(val)
    except (TypeError, ValueError):
        return 0
    return val if val > 0 else 0


def _incoming_file_size(f):
    """Size of a werkzeug FileStorage without consuming it."""
    try:
        pos = f.stream.tell()
        f.stream.seek(0, os.SEEK_END)
        size = f.stream.tell()
        f.stream.seek(pos)
        return size
    except (OSError, AttributeError):
        return 0


def _project_attachment_usage(project):
    """Sum of recorded attachment sizes across all backlog items."""
    total = 0
    for item in project.get('backlog', []):
        for a in item.get('attachments', []):
            try:
                total += int(a.get('size', 0))
            except (TypeError, ValueError):
                pass
    return total


@bp.route('/api/project/<project_id>/backlog/<item_id>/attachments', methods=['POST'])
def upload_attachment(project_id, item_id):
    """Upload a file and attach it to a backlog item."""
    p = load_project(project_id)
    if p is None:
        return jsonify({'error': 'project not found'}), 404

    item = next((i for i in p.get('backlog', []) if i['id'] == item_id), None)
    if item is None:
        return jsonify({'error': 'item not found'}), 404

    if 'file' not in request.files:
        return jsonify({'error': 'no file'}), 400

    f = request.files['file']
    if not f.filename:
        return jsonify({'error': 'empty filename'}), 400

    # P2-2: enforce per-file + per-project cumulative upload limits before
    # touching disk. Limits default to 0 (unlimited) so this is a no-op
    # unless Ron sets upload_max_file_bytes / upload_quota_bytes globally
    # or per-project.
    incoming = _incoming_file_size(f)
    max_file = _upload_limit(p, 'upload_max_file_bytes')
    if max_file and incoming > max_file:
        _log_agent_activity(
            project_id,
            f"Upload rejected: '{f.filename}' is {incoming} B, over the "
            f"{max_file} B per-file limit")
        return jsonify({'error': 'file too large',
                        'limit_bytes': max_file,
                        'file_bytes': incoming}), 413
    quota = _upload_limit(p, 'upload_quota_bytes')
    if quota:
        used = _project_attachment_usage(p)
        if used + incoming > quota:
            _log_agent_activity(
                project_id,
                f"Upload rejected: project attachment quota exceeded "
                f"({used}+{incoming} B > {quota} B)")
            return jsonify({'error': 'project upload quota exceeded',
                            'quota_bytes': quota, 'used_bytes': used,
                            'file_bytes': incoming}), 413

    original_name = f.filename
    ext = Path(original_name).suffix.lower()
    stored_name = f'{project_id}_{item_id}_{uuid.uuid4().hex[:8]}{ext}'
    dest = UPLOADS_DIR / stored_name
    f.save(str(dest))

    att = {
        'id': str(uuid.uuid4())[:8],
        'original_name': original_name,
        'stored_name': stored_name,
        'size': dest.stat().st_size,
        'type': file_type(original_name),
        'uploaded_at': now_iso(),
    }

    item.setdefault('attachments', []).append(att)
    p['last_updated'] = now_iso()
    save_project(project_id, p)
    return jsonify({'ok': True, 'attachment': att})


@bp.route('/api/attachments/<stored_name>')
def serve_attachment(stored_name):
    """Serve an attachment file."""
    safe = Path(stored_name).name  # prevent path traversal
    att_path = UPLOADS_DIR / safe
    if not att_path.exists():
        abort(404)
    return send_file(str(att_path), as_attachment=False)


_IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp',
               '.svg', '.ico', '.tif', '.tiff', '.avif'}


@bp.route('/api/serve-image')
def serve_image():
    """Serve an image file referenced in agent output.

    Security model (this is a localhost dashboard, but still): the
    realpath-resolved target MUST be an image extension AND must live
    under a known project working dir, the uploads dir, or the data
    root. realpath() collapses any `..` so the prefix check can't be
    escaped. Anything else 403/404/415s.
    """
    raw = (request.args.get('path') or '').strip()
    if not raw:
        abort(400)
    try:
        real = os.path.realpath(raw)
    except Exception:
        abort(400)
    if os.path.splitext(real)[1].lower() not in _IMAGE_EXTS:
        abort(415)
    if not os.path.isfile(real):
        abort(404)
    allowed = [str(UPLOADS_DIR), str(_DATA_ROOT)]
    try:
        for p in load_projects():
            pp = (p.get('project_path') or '').strip()
            if pp:
                # Don't let a project rooted at a filesystem/drive root (C:\, /,
                # C:\Users, /home) turn serve-image into a whole-disk image read.
                try:
                    if len(Path(os.path.realpath(pp)).parts) < 3:
                        continue
                except Exception:
                    continue
                allowed.append(pp)
    except Exception:
        pass
    rn = os.path.normcase(real)
    ok = False
    for a in allowed:
        try:
            ar = os.path.normcase(os.path.realpath(a))
        except Exception:
            continue
        if rn == ar or rn.startswith(ar + os.sep):
            ok = True
            break
    if not ok:
        abort(403)
    return send_file(real, as_attachment=False, max_age=3600)


@bp.route('/api/project/<project_id>/backlog/<item_id>/attachments/<att_id>', methods=['DELETE'])
def delete_attachment(project_id, item_id, att_id):
    p = load_project(project_id)
    if p is None:
        return jsonify({'error': 'project not found'}), 404

    item = next((i for i in p.get('backlog', []) if i['id'] == item_id), None)
    if item is None:
        return jsonify({'error': 'item not found'}), 404

    atts = item.get('attachments', [])
    att = next((a for a in atts if a['id'] == att_id), None)
    if att is None:
        return jsonify({'error': 'attachment not found'}), 404

    att_path = UPLOADS_DIR / att['stored_name']
    if att_path.exists():
        att_path.unlink()

    item['attachments'] = [a for a in atts if a['id'] != att_id]
    p['last_updated'] = now_iso()
    save_project(project_id, p)
    return jsonify({'ok': True})


# ── Project import ────────────────────────────────────────────────────────────

def _parse_changelog(text):
    """Parse the most recent CHANGELOG.md entry into structured sections."""
    lines = text.split('\n')
    # Find first ## heading (most recent entry)
    start = None
    for i, line in enumerate(lines):
        if line.startswith('## '):
            if start is None:
                start = i
            else:
                # Hit the next entry, stop
                lines = lines[start:i]
                break
    else:
        if start is not None:
            lines = lines[start:]
        else:
            return {}

    title = lines[0].lstrip('# ').strip() if lines else ''
    sections = {}
    current_section = None
    current_lines = []

    for line in lines[1:]:
        if line.startswith('### '):
            if current_section:
                sections[current_section] = current_lines
            current_section = line.lstrip('# ').strip().lower()
            current_lines = []
        elif current_section:
            stripped = line.strip()
            if stripped and stripped != '---':
                # Remove leading "- " or "* "
                if stripped.startswith('- ') or stripped.startswith('* '):
                    stripped = stripped[2:]
                if stripped:
                    current_lines.append(stripped)

    if current_section:
        sections[current_section] = current_lines

    return {'title': title, 'sections': sections}


@bp.route('/api/project/<project_id>/import', methods=['POST'])
def import_from_project(project_id):
    p = load_project(project_id)
    if not p:
        return jsonify({'error': 'project not found'}), 404

    pp = p.get('project_path', '')
    if not pp or not Path(pp).is_dir():
        return jsonify({'error': 'project_path not set or invalid'}), 400

    imported = {}

    # Parse CHANGELOG.md
    changelog_path = Path(pp) / 'CHANGELOG.md'
    if changelog_path.exists():
        parsed = _parse_changelog(changelog_path.read_text(encoding='utf-8'))
        sections = parsed.get('sections', {})
        title = parsed.get('title', '')

        # Done → activity log entries
        done_items = sections.get('done', [])
        if done_items:
            log = p.setdefault('activity_log', [])
            ts = now_iso()
            for item in done_items:
                if not any(e.get('msg') == item for e in log):
                    log.insert(0, {'ts': ts, 'msg': item})
            p['activity_log'] = log[:50]
            imported['activity_log'] = len(done_items)

        # State → description
        state_items = sections.get('state', [])
        if state_items:
            p['description'] = '\n'.join(state_items)
            imported['description'] = True

        # Next → backlog + next_action
        next_items = sections.get('next', [])
        if next_items:
            p['next_action'] = next_items[0]
            backlog = p.setdefault('backlog', [])
            existing_texts = {i['text'] for i in backlog}
            added = 0
            for item in next_items:
                if item not in existing_texts:
                    backlog.insert(0, {
                        'id': str(uuid.uuid4())[:8],
                        'text': item,
                        'priority': 'normal',
                        'status': 'open',
                        'created_at': now_iso(),
                        'done_at': None,
                        'source': 'changelog',
                        'attachments': [],
                    })
                    added += 1
            imported['backlog'] = added

        # Title → current_task if present
        if title and not p.get('current_task'):
            p['current_task'] = title
            imported['current_task'] = True

    p['last_updated'] = now_iso()
    save_project(project_id, p)
    return jsonify({'ok': True, 'imported': imported})


# ── Rules endpoints ─────────────────────────────────────────────────────────

def _validate_project_path(pp):
    """Ensure path is under PROJECTS_BASE to prevent traversal."""
    try:
        resolved = Path(pp).resolve()
        return resolved.is_relative_to(PROJECTS_BASE.resolve())
    except Exception:
        return False


@bp.route('/api/project/<project_id>/rules')
def get_rules(project_id):
    p = load_project(project_id)
    if not p:
        return jsonify({'error': 'project not found'}), 404

    agent_rules = ''
    pp = p.get('project_path', '')
    if pp and _validate_project_path(pp):
        agent_path = Path(pp) / 'AGENT_RULES.md'
        if agent_path.exists():
            agent_rules = agent_path.read_text(encoding='utf-8')

    shared_rules = ''
    if SHARED_RULES_PATH.exists():
        shared_rules = SHARED_RULES_PATH.read_text(encoding='utf-8')

    return jsonify({'agent_rules': agent_rules, 'shared_rules': shared_rules})


@bp.route('/api/project/<project_id>/rules', methods=['PUT'])
def save_rules(project_id):
    p = load_project(project_id)
    if not p:
        return jsonify({'error': 'project not found'}), 404

    pp = p.get('project_path', '')
    if not pp or not _validate_project_path(pp):
        return jsonify({'error': 'project_path not set or invalid'}), 400

    data = request.get_json() or {}
    agent_rules = data.get('agent_rules')
    if agent_rules is None:
        return jsonify({'error': 'agent_rules required'}), 400

    agent_path = Path(pp) / 'AGENT_RULES.md'
    agent_path.write_text(agent_rules, encoding='utf-8')
    return jsonify({'ok': True})


@bp.route('/api/rules/shared')
def get_shared_rules():
    content = ''
    if SHARED_RULES_PATH.exists():
        content = SHARED_RULES_PATH.read_text(encoding='utf-8')
    return jsonify({'shared_rules': content})


@bp.route('/api/rules/shared', methods=['PUT'])
def save_shared_rules():
    data = request.get_json() or {}
    content = data.get('shared_rules')
    if content is None:
        return jsonify({'error': 'shared_rules required'}), 400

    SHARED_RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
    SHARED_RULES_PATH.write_text(content, encoding='utf-8')
    return jsonify({'ok': True})


# ── Memory endpoints ────────────────────────────────────────────────────────
# Editor-CRUD only (the 1.9 scoping call): raw read/replace/append over the
# file at _get_memory_path. The locked managed-region writers
# (_commit_managed_entry / _condense_apply / _get_mem_write_lock) are NOT
# touched and stay in server.py — see CLAUDE.md memory-system rules.

@bp.route('/api/project/<project_id>/memory')
def get_memory(project_id):
    p = load_project(project_id)
    if not p:
        return jsonify({'error': 'not found'}), 404
    mem_path = _get_memory_path(p)
    content = ''
    if mem_path.exists():
        content = mem_path.read_text(encoding='utf-8')
    return jsonify({'content': content, 'path': str(mem_path)})

@bp.route('/api/project/<project_id>/memory', methods=['PUT'])
def save_memory(project_id):
    p = load_project(project_id)
    if not p:
        return jsonify({'error': 'not found'}), 404
    data = request.get_json() or {}
    content = data.get('content')
    if content is None:
        return jsonify({'error': 'content required'}), 400
    mem_path = _get_memory_path(p)
    mem_path.parent.mkdir(parents=True, exist_ok=True)
    mem_path.write_text(content, encoding='utf-8')
    return jsonify({'ok': True})

@bp.route('/api/project/<project_id>/memory/append', methods=['POST'])
def append_memory(project_id):
    p = load_project(project_id)
    if not p:
        return jsonify({'error': 'not found'}), 404
    data = request.get_json() or {}
    content = (data.get('content') or '').strip()
    if not content:
        return jsonify({'error': 'content required'}), 400
    mem_path = _get_memory_path(p)
    mem_path.parent.mkdir(parents=True, exist_ok=True)
    existing = ''
    if mem_path.exists():
        existing = mem_path.read_text(encoding='utf-8').rstrip()
    if existing:
        combined = existing + '\n\n' + content
    else:
        combined = content
    mem_path.write_text(combined, encoding='utf-8')
    return jsonify({'ok': True})


# ── Project order ────────────────────────────────────────────────────────────

@bp.route('/api/projects/order', methods=['POST', 'OPTIONS'])
def save_project_order():
    if request.method == 'OPTIONS':
        return '', 204
    data = request.get_json()
    if not data or 'order' not in data:
        return jsonify({'error': 'order array required'}), 400
    order = data['order']
    # Save full grid layout (with nulls for spacers)
    layout_path = DATA_DIR.parent / 'grid_layout.json'
    layout_path.write_text(json.dumps({'order': order}, indent=2, ensure_ascii=False), encoding='utf-8')
    # Update display_order on each project
    for i, project_id in enumerate(order):
        if project_id is None:
            continue
        p = load_project(project_id)
        if p:
            p['display_order'] = i
            save_project(project_id, p)
    return jsonify({'ok': True})

@bp.route('/api/grid-layout')
def get_grid_layout():
    layout_path = DATA_DIR.parent / 'grid_layout.json'
    if layout_path.exists():
        try:
            return jsonify(json.loads(layout_path.read_text(encoding='utf-8')))
        except Exception:
            pass
    return jsonify({'order': []})
