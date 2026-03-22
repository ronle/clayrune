#!/usr/bin/env python3
import json
import os
import uuid
import mimetypes
import subprocess
import sys
import threading
import time as _time
from pathlib import Path
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify, send_from_directory, request, send_file, abort, Response


def _resolve_dirs():
    """Resolve application and data directories.

    Frozen (PyInstaller): assets from sys._MEIPASS, user data in %APPDATA%/MissionControl.
    Dev mode: both point to the repo root (backward-compatible).
    """
    if getattr(sys, 'frozen', False):
        app_dir = Path(sys._MEIPASS)
        data_root = Path(os.environ.get(
            'MC_DATA_DIR',
            str(Path(os.environ.get('APPDATA', str(Path.home()))) / 'MissionControl')
        ))
    else:
        app_dir = Path(__file__).parent
        data_root = Path(os.environ['MC_DATA_DIR']) if os.environ.get('MC_DATA_DIR') else app_dir
    return app_dir, data_root

_APP_DIR, _DATA_ROOT = _resolve_dirs()
STATIC_DIR = str(_APP_DIR / 'static')
_POPEN_FLAGS = subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
_STARTUPINFO = None
if sys.platform == 'win32':
    _STARTUPINFO = subprocess.STARTUPINFO()
    _STARTUPINFO.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    _STARTUPINFO.wShowWindow = 0  # SW_HIDE


def _hide_process_windows(pid):
    """Hide any console windows created by a process (Windows only)."""
    if sys.platform != 'win32':
        return
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
        def _cb(hwnd, _):
            proc_id = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(proc_id))
            if proc_id.value == pid:
                user32.ShowWindow(hwnd, 0)  # SW_HIDE
            return True

        user32.EnumWindows(_cb, 0)
    except Exception:
        pass


def _hide_windows_delayed(pid):
    """Hide windows after a short delay to catch late-created consoles."""
    import time
    for _ in range(5):
        time.sleep(0.3)
        _hide_process_windows(pid)
    # One final check after a longer wait
    time.sleep(1)
    _hide_process_windows(pid)

app = Flask(__name__, static_folder=STATIC_DIR)
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB max upload

# ── Configuration ────────────────────────────────────────────────────────────

CONFIG_PATH = _DATA_ROOT / 'config.json'

def _load_config():
    """Load config.json, creating with defaults if it doesn't exist."""
    defaults = {
        'port': 5199,
        'shared_rules_path': str(_DATA_ROOT / 'data' / 'SHARED_RULES.md'),
        'projects_base': str(Path.home()),
        'agent_model': '',
        'agent_max_turns': 0,
        'agent_permission_mode': '',
        'desktop_mode': False,
        'user_name': '',
        'agent_name': '',
        'use_streaming_agent': False,
        'condense_threshold_kb': 15,
        'condense_model': '',
        'condense_enabled': True,
        'agent_channels': '',
        'agent_remote_control': False,
    }
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, encoding='utf-8') as f:
                saved = json.load(f)
            # Merge: saved values override defaults
            for k, v in saved.items():
                defaults[k] = v
        except Exception:
            pass
    else:
        # Create default config for the user to customize
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(defaults, f, indent=2, ensure_ascii=False)
    return defaults

CONFIG = _load_config()
PORT = int(os.environ.get('MC_PORT', CONFIG.get('port', 5199)))

ALLOWED_ORIGINS = {
    'https://tauri.localhost',
    'tauri://localhost',
    f'http://localhost:{PORT}',
}

@app.after_request
def add_cors_headers(response):
    origin = request.headers.get('Origin', '')
    if origin in ALLOWED_ORIGINS:
        response.headers['Access-Control-Allow-Origin'] = origin
        response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    if request.method == 'OPTIONS':
        response.status_code = 204
    return response

DATA_DIR = _DATA_ROOT / 'data' / 'projects'
DATA_DIR.mkdir(parents=True, exist_ok=True)

UPLOADS_DIR = _DATA_ROOT / 'data' / 'uploads'
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

SHARED_RULES_PATH = Path(CONFIG.get('shared_rules_path', ''))
PROJECTS_BASE = Path(CONFIG.get('projects_base', str(Path.home())))
SETTINGS_PATH = _DATA_ROOT / 'data' / 'settings.json'
SCHEDULES_PATH = _DATA_ROOT / 'data' / 'schedules.json'

MEMORY_DIR = _DATA_ROOT / 'data' / 'memory'  # fallback for projects without project_path
MEMORY_DIR.mkdir(parents=True, exist_ok=True)

CLAUDE_HOME = Path.home() / '.claude' / 'projects'
_SESSION_SIZE_LIMIT = 5 * 1024 * 1024  # 5 MB — resume becomes too slow above this


def _session_transcript_path(project_path, claude_session_id):
    """Return the .jsonl transcript path for a Claude session."""
    if not project_path or not claude_session_id:
        return None
    resolved = str(Path(project_path).resolve())
    encoded = resolved.replace(':', '-').replace('\\', '-').replace('/', '-')
    return CLAUDE_HOME / encoded / f'{claude_session_id}.jsonl'


def _session_too_large(project_path, claude_session_id):
    """Check if a session transcript exceeds the size limit."""
    p = _session_transcript_path(project_path, claude_session_id)
    if p and p.exists():
        try:
            size = p.stat().st_size
            return size > _SESSION_SIZE_LIMIT, size
        except OSError:
            pass
    return False, 0


def _native_memory_path(project_path):
    """Derive the Claude Code native MEMORY.md path for a project.

    Claude stores memory at ~/.claude/projects/<encoded-path>/memory/MEMORY.md
    where the path encoding replaces : and path separators with -.
    """
    if not project_path:
        return None
    resolved = str(Path(project_path).resolve())
    # Encode: C:\Users\foo\bar → C--Users-foo-bar
    encoded = resolved.replace(':', '-').replace('\\', '-').replace('/', '-')
    mem_path = CLAUDE_HOME / encoded / 'memory' / 'MEMORY.md'
    return mem_path


def _get_memory_path(project):
    """Get the memory file path for a project — native Claude path preferred, fallback to MC data dir."""
    pp = project.get('project_path', '')
    if pp:
        native = _native_memory_path(pp)
        if native:
            return native
    return MEMORY_DIR / f'{project["id"]}.md'


def _get_archive_path(project):
    """Get the MEMORY_ARCHIVE.md path — sibling to the project's MEMORY.md."""
    mem_path = _get_memory_path(project)
    return mem_path.parent / 'MEMORY_ARCHIVE.md'


DEFAULT_DOMAINS = [
    {'id': 'general', 'label': 'General', 'color': 'var(--text-dim)', 'bg': 'var(--surface3)'},
    {'id': 'trading', 'label': 'Trading', 'color': 'var(--accent)', 'bg': 'var(--accent-dim)'},
    {'id': 'infra', 'label': 'Infra', 'color': 'var(--purple-text)', 'bg': 'var(--purple-dim)'},
    {'id': 'hobby', 'label': 'Hobby', 'color': 'var(--amber-text)', 'bg': 'var(--amber-dim)'},
]

def _load_settings():
    defaults = {'domains': list(DEFAULT_DOMAINS)}
    if SETTINGS_PATH.exists():
        try:
            with open(SETTINGS_PATH, encoding='utf-8') as f:
                saved = json.load(f)
            for k, v in saved.items():
                defaults[k] = v
        except Exception:
            pass
    return defaults

def _save_settings(settings):
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding='utf-8')


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


def _build_claude_flags(project=None, streaming=False):
    """Build common Claude CLI flags from config, with optional per-project overrides."""
    flags = ['--print', '--verbose', '--output-format', 'stream-json',
             '--dangerously-skip-permissions']
    if streaming:
        flags.extend(['--input-format', 'stream-json'])
    # Per-project model takes priority over global config
    model = (project or {}).get('agent_model', '') or CONFIG.get('agent_model', '')
    if model:
        flags.extend(['--model', model])
    max_turns = CONFIG.get('agent_max_turns', 0)
    if max_turns and int(max_turns) > 0:
        flags.extend(['--max-turns', str(int(max_turns))])
    perm_mode = CONFIG.get('agent_permission_mode', '')
    if perm_mode:
        flags.extend(['--permission-mode', perm_mode])
    # Channels (e.g. "plugin:telegram@claude-plugins-official")
    channels = (project or {}).get('agent_channels', '') or CONFIG.get('agent_channels', '')
    if channels:
        flags.extend(['--channels', channels])
    # Remote control
    rc = (project or {}).get('agent_remote_control', False) or CONFIG.get('agent_remote_control', False)
    if rc:
        flags.append('--remote-control')
    return flags


# ── Agent session tracking ───────────────────────────────────────────────────
# session_id → {proc, status, task, log_lines, started_at, session_id, project_id}
agent_sessions = {}
agent_lock = threading.Lock()  # single global lock for session creation

# ── Memory condensation state ────────────────────────────────────────────────
_condensing_projects = set()
_condense_lock = threading.Lock()


def _has_running_agent(project_id):
    """Return True if any non-housekeeping agent is running or idle for this project."""
    for s in agent_sessions.values():
        if s.get('project_id') == project_id and not s.get('housekeeping'):
            if s.get('status') in ('running', 'idle'):
                return True
    return False


def _should_condense(project):
    """Check whether memory condensation should be triggered for this project."""
    if not CONFIG.get('condense_enabled', True):
        return False
    pid = project['id']
    with _condense_lock:
        if pid in _condensing_projects:
            return False
    if _has_running_agent(pid):
        return False
    mem_path = _get_memory_path(project)
    archive_path = _get_archive_path(project)
    combined = 0
    if mem_path.exists():
        combined += mem_path.stat().st_size
    if archive_path.exists():
        combined += archive_path.stat().st_size
    threshold = CONFIG.get('condense_threshold_kb', 15) * 1024
    return combined > threshold


# ── Terminal session tracking ────────────────────────────────────────────────
# session_id → {proc, status, command, output_lines, started_at, session_id, project_id, exit_code}
# TTY shim: mc_tty_shim/sitecustomize.py patches isatty() + Rich for ANSI colors
terminal_sessions = {}
terminal_lock = threading.Lock()

# ── Process tracker (PID registry) ────────────────────────────────────────────
# pid (int) → {pid, name, type, session_id, project_id, project_name,
#              command_preview, started_at, proc}
tracked_processes = {}
process_tracker_lock = threading.Lock()


def _register_process(proc, name, proc_type, session_id, project_id, command_preview=''):
    """Register a spawned process in the PID tracker."""
    project_name = project_id
    try:
        p = load_project(project_id)
        if p:
            project_name = p.get('name', project_id)
    except Exception:
        pass
    with process_tracker_lock:
        tracked_processes[proc.pid] = {
            'pid': proc.pid,
            'name': name,
            'type': proc_type,
            'session_id': session_id,
            'project_id': project_id,
            'project_name': project_name,
            'command_preview': (command_preview or '')[:80],
            'started_at': now_iso(),
            'proc': proc,
        }


def _unregister_process(pid):
    """Remove a process from the PID tracker."""
    with process_tracker_lock:
        tracked_processes.pop(pid, None)


def load_project(project_id):
    filepath = DATA_DIR / f'{project_id}.json'
    if not filepath.exists():
        return None
    return json.loads(filepath.read_text(encoding='utf-8'))


def save_project(project_id, data):
    filepath = DATA_DIR / f'{project_id}.json'
    filepath.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')


def load_projects():
    projects = []
    for f in DATA_DIR.glob('*.json'):
        if f.name.endswith('_agent_log.json'):
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
            projects.append(p)
        except Exception as e:
            print(f"Error loading {f}: {e}")
    projects.sort(key=lambda p: (p.get('display_order', 9999), p.get('last_updated', '1970-01-01T00:00:00Z')))
    # Secondary sort: within same display_order, most recently updated first
    projects.sort(key=lambda p: p.get('last_updated', '1970-01-01T00:00:00Z'), reverse=True)
    projects.sort(key=lambda p: p.get('display_order', 9999))
    return projects


def time_ago(ts_str):
    if not ts_str:
        return 'never'
    try:
        ts = datetime.fromisoformat(ts_str.replace('Z', '+00:00'))
        now = datetime.now(timezone.utc)
        secs = int((now - ts).total_seconds())
        if secs < 60:      return f'{secs}s ago'
        elif secs < 3600:  return f'{secs // 60}m ago'
        elif secs < 86400: return f'{secs // 3600}h ago'
        else:              return f'{secs // 86400}d ago'
    except:
        return ts_str


def now_iso():
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def file_type(filename):
    """Return a simple type hint for UI rendering."""
    ext = Path(filename).suffix.lower()
    images = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp'}
    if ext in images:
        return 'image'
    if ext == '.pdf':
        return 'pdf'
    return 'file'


# ── Project endpoints ────────────────────────────────────────────────────────

@app.route('/api/projects')
def api_projects():
    projects = load_projects()
    for p in projects:
        p['last_updated_relative'] = time_ago(p.get('last_updated'))
        for entry in p.get('activity_log', []):
            entry['ts_relative'] = time_ago(entry.get('ts'))
        for item in p.get('backlog', []):
            item['ts_relative'] = time_ago(item.get('created_at'))
    return jsonify(projects)


@app.route('/api/project/<project_id>', methods=['POST'])
def update_project(project_id):
    data = request.get_json()
    if not data:
        return jsonify({'error': 'no data'}), 400

    filepath = DATA_DIR / f'{project_id}.json'
    existing = json.loads(filepath.read_text(encoding='utf-8')) if filepath.exists() else {'id': project_id}
    existing.setdefault('backlog', [])

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


@app.route('/api/project/<project_id>', methods=['DELETE'])
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
    with agent_lock:
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

@app.route('/api/project/<project_id>/backlog', methods=['GET'])
def get_backlog(project_id):
    p = load_project(project_id)
    if p is None:
        return jsonify({'error': 'not found'}), 404
    return jsonify(p.get('backlog', []))


@app.route('/api/project/<project_id>/backlog', methods=['POST'])
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


@app.route('/api/project/<project_id>/backlog/<item_id>', methods=['PATCH'])
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


@app.route('/api/project/<project_id>/backlog/<item_id>', methods=['DELETE'])
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


# ── Walkthrough sample project ────────────────────────────────────────────────

@app.route('/api/walkthrough/sample-project', methods=['POST'])
def create_sample_project():
    """Create a sample project for the first-run walkthrough (idempotent)."""
    pid = 'sample-project'
    filepath = DATA_DIR / f'{pid}.json'
    if filepath.exists():
        return jsonify({'ok': True, 'id': pid, 'existed': True})

    ts = now_iso()
    project = {
        'id': pid,
        'name': 'Sample Project',
        'domain': 'general',
        'status': 'active',
        'description': 'A sample project created during the walkthrough. Feel free to explore, modify, or delete it!',
        'current_task': 'Learn how to use Mission Control',
        'next_action': 'Try adding tasks to the backlog',
        'last_updated': ts,
        'backlog': [
            {'id': 'sample01', 'text': 'Explore the project tabs', 'status': 'open', 'priority': 'normal', 'created_at': ts},
            {'id': 'sample02', 'text': 'Try dispatching an AI agent', 'status': 'open', 'priority': 'high', 'created_at': ts},
            {'id': 'sample03', 'text': 'Connect a GitHub repo for issue sync', 'status': 'open', 'priority': 'low', 'created_at': ts},
        ],
        'activity_log': [
            {'ts': ts, 'msg': 'Project created during Mission Control walkthrough'}
        ],
    }
    save_project(pid, project)
    return jsonify({'ok': True, 'id': pid, 'existed': False})


# ── GitHub sync endpoints ────────────────────────────────────────────────────

@app.route('/api/project/<project_id>/github/setup', methods=['POST'])
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


@app.route('/api/project/<project_id>/github/disconnect', methods=['POST'])
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


@app.route('/api/project/<project_id>/github/sync', methods=['POST'])
def github_sync_now(project_id):
    p = load_project(project_id)
    if p is None:
        return jsonify({'error': 'not found'}), 404
    ok, summary = _gh_sync.sync_project(project_id)
    if not ok:
        return jsonify({'error': summary}), 429 if 'Rate' in summary else 400
    return jsonify({'ok': True, 'summary': summary})


@app.route('/api/project/<project_id>/github/status')
def github_status(project_id):
    p = load_project(project_id)
    if p is None:
        return jsonify({'error': 'not found'}), 404
    return jsonify({
        'repo': p.get('github_repo', ''),
        'enabled': p.get('github_sync_enabled', False),
        'last_sync': p.get('github_last_sync'),
    })


# ── Attachment endpoints ─────────────────────────────────────────────────────

@app.route('/api/project/<project_id>/backlog/<item_id>/attachments', methods=['POST'])
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


@app.route('/api/attachments/<stored_name>')
def serve_attachment(stored_name):
    """Serve an attachment file."""
    safe = Path(stored_name).name  # prevent path traversal
    att_path = UPLOADS_DIR / safe
    if not att_path.exists():
        abort(404)
    return send_file(str(att_path), as_attachment=False)


@app.route('/api/project/<project_id>/backlog/<item_id>/attachments/<att_id>', methods=['DELETE'])
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


@app.route('/api/project/<project_id>/import', methods=['POST'])
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


# ── Agent image upload ────────────────────────────────────────────────────────

@app.route('/api/agent/upload-image', methods=['POST'])
def agent_upload_image():
    """Save a pasted image and return its absolute path for agent consumption."""
    if 'file' not in request.files:
        return jsonify({'error': 'no file'}), 400
    f = request.files['file']
    if not f.filename:
        return jsonify({'error': 'empty filename'}), 400
    ext = Path(f.filename).suffix.lower() or '.png'
    stored_name = f'agent_{uuid.uuid4().hex[:10]}{ext}'
    dest = UPLOADS_DIR / stored_name
    f.save(str(dest))
    return jsonify({'ok': True, 'path': str(dest.resolve())})



# ── Agent endpoints ──────────────────────────────────────────────────────────

def _build_agent_context(project):
    """Build system prompt context for the agent."""
    parts = []
    agent_name = CONFIG.get('agent_name', '')
    user_name = CONFIG.get('user_name', '')
    if agent_name:
        parts.append(f"Your name is {agent_name}.")
    if user_name:
        parts.append(f"The user's name is {user_name}. Address them accordingly.")
    parts.append(f"You are working on {project.get('name', project['id'])}.")
    pp = project.get('project_path', '')
    if pp:
        parts.append(f"Project root: {pp}")

    # Load rules
    if pp:
        agent_rules_path = Path(pp) / 'AGENT_RULES.md'
        if agent_rules_path.exists():
            parts.append(f"--- AGENT_RULES.md ---\n{agent_rules_path.read_text(encoding='utf-8')}")
    if SHARED_RULES_PATH.exists():
        parts.append(f"--- SHARED_RULES.md ---\n{SHARED_RULES_PATH.read_text(encoding='utf-8')}")

    # NOTE: Project memory (MEMORY.md) is NOT injected here — the Claude CLI
    # already reads ~/.claude/projects/<path>/memory/MEMORY.md natively.
    # Injecting it via --append-system-prompt would duplicate it in every API call.
    mem_path = _get_memory_path(project)

    # System awareness
    pid = project['id']
    port = PORT
    mem_file = str(mem_path) if mem_path else 'MEMORY.md'
    archive_path = _get_archive_path(project)
    archive_file = str(archive_path)
    awareness = [
        "You are managed by Mission Control.",
        f"Memory: {mem_file} (auto-loaded). Update it when you learn important project info.",
        f"Archive: {archive_file} — older session logs, read if needed.",
    ]
    if pp:
        rules_file = str(Path(pp) / 'AGENT_RULES.md')
        awareness.append(f"Rules: {rules_file} — add critical constraints here.")
    awareness.extend([
        f"Terminal: curl -s -X POST http://localhost:{port}/api/terminal/launch "
        f'-H "Content-Type: application/json" '
        f"-d '{{\"project_id\":\"{pid}\",\"command\":\"<CMD>\"}}'",
        f"Process tracking: When you spawn a background process (e.g. a server, bot, script), "
        f"register it so the user can monitor/kill it from the Process Manager: "
        f"curl -s -X POST http://localhost:{port}/api/processes/register "
        f'-H "Content-Type: application/json" '
        f"-d '{{\"pid\":PID_NUMBER,\"name\":\"Short description\",\"project_id\":\"{pid}\","
        f"\"command\":\"the command that was run\"}}'",
        "IMPORTANT — Plan Mode: Do NOT use EnterPlanMode or ExitPlanMode. "
        "You are running headless without an interactive terminal, so plan mode approval "
        "will hang indefinitely. Instead, just describe your plan in a text message and "
        "proceed directly with implementation. If the user asks you to plan, write your "
        "plan as a text response, then start coding immediately.",
    ])
    parts.append("--- SYSTEM ---\n" + "\n".join(awareness))

    # Recent activity
    log = project.get('activity_log', [])[:3]
    if log:
        lines = [f"  - {e.get('ts','')}: {e.get('msg','')}" for e in log]
        parts.append("Recent activity:\n" + "\n".join(lines))

    # Recent agent sessions (for continuity if prior conversation hung)
    agent_log = _load_agent_log(project['id'])[:3]
    if agent_log:
        sess_lines = []
        for e in agent_log:
            csid = e.get('claude_session_id', '')
            sid_part = f" | claude -r {csid}" if csid else ''
            sess_lines.append(f"  - [{e.get('status','')}] {e.get('task','')[:60]}{sid_part}")
        parts.append("Recent agent sessions (use 'claude -r <id>' to resume a prior conversation):\n" + "\n".join(sess_lines))

    ct = project.get('current_task', '')
    if ct:
        parts.append(f"Current task: {ct}")

    return "\n\n".join(parts)


def _format_tool_activity(name, inp):
    """Format a tool_use block into a compact activity line."""
    if name in ('Read', 'Edit', 'Write'):
        fp = inp.get('file_path', '')
        short = Path(fp).name if fp else '?'
        return f'[tool: {name}] {short}'
    elif name == 'Bash':
        cmd = (inp.get('command', '') or inp.get('description', '') or '')[:80]
        return f'[tool: Bash] {cmd}'
    elif name in ('Grep', 'Glob'):
        pat = inp.get('pattern', '')
        return f'[tool: {name}] {pat}'
    elif name == 'Task':
        desc = (inp.get('description', '') or '')[:50]
        return f'[tool: Task] {desc}'
    elif name == 'WebSearch':
        q = (inp.get('query', '') or '')[:60]
        return f'[tool: WebSearch] {q}'
    elif name == 'AskUserQuestion':
        qs = inp.get('questions', [])
        preview = qs[0].get('question', '')[:60] if qs else ''
        return f'[tool: AskUserQuestion] {preview}'
    else:
        return f'[tool: {name}]'


def _read_agent_stream(proc, session):
    """Reader thread: captures stdout lines into session log_lines."""
    # Snapshot the proc we were launched with so we can detect if a follow-up
    # replaced us with a newer process while we were still draining stdout.
    my_proc = proc
    try:
        for raw_line in proc.stdout:
            # If session proc changed, a follow-up superseded us — stop writing.
            if session.get('proc') is not my_proc:
                break
            line = raw_line.rstrip('\n\r')
            if not line:
                continue
            # Try to parse stream-json output
            try:
                msg = json.loads(line)
                msg_type = msg.get('type', '')
                # Capture Claude CLI session UUID from init or result messages
                if 'session_id' in msg:
                    session['claude_session_id'] = msg['session_id']
                if msg_type == 'assistant' and 'message' in msg:
                    for block in msg['message'].get('content', []):
                        if block.get('type') == 'text':
                            session['log_lines'].append(block['text'])
                        elif block.get('type') == 'tool_use':
                            tool_name = block.get('name', '')
                            tool_input = block.get('input', {})
                            activity = _format_tool_activity(tool_name, tool_input)
                            session['log_lines'].append(activity)
                            # Track .md file edits for plan file detection
                            if tool_name in ('Write', 'Edit'):
                                fp = tool_input.get('file_path', '')
                                if fp.lower().endswith('.md'):
                                    session['_last_md_file'] = fp
                            elif tool_name == 'ExitPlanMode':
                                if session.get('_last_md_file'):
                                    session['plan_file'] = session['_last_md_file']
                                session['log_lines'].append('[Plan mode exit detected — Mode A cannot approve, queueing follow-up]')
                                # Queue a follow-up to resume without plan mode
                                session.setdefault('pending_followups', []).append(
                                    'Plan approved. Proceed with implementation immediately. '
                                    'Do NOT call EnterPlanMode or ExitPlanMode again.')
                            elif tool_name == 'AskUserQuestion':
                                session.setdefault('pending_questions', []).append(tool_input)
                elif msg_type == 'result':
                    # Capture session_id from result as fallback
                    if 'session_id' in msg:
                        session['claude_session_id'] = msg['session_id']
                    # Capture token usage and cost data
                    if 'usage' in msg:
                        session['usage'] = msg['usage']
                    if 'cost_usd' in msg:
                        session['cost_usd'] = msg['cost_usd']
                    if 'num_turns' in msg:
                        session['num_turns'] = msg['num_turns']
            except json.JSONDecodeError:
                session['log_lines'].append(line)
    except Exception as e:
        # Only log stream errors if we're still the active reader
        if session.get('proc') is my_proc:
            session['log_lines'].append(f"[stream error: {e}]")
    finally:
        rc = proc.wait()
        _unregister_process(proc.pid)
        # Only update session status if we're still the active reader.
        # If a follow-up replaced us, the new reader owns status updates.
        if session.get('proc') is my_proc:
            if session['status'] == 'running':
                session['status'] = 'completed' if rc == 0 else 'error'
                if rc != 0:
                    session['log_lines'].append(f"[exited with code {rc}]")
            _log_agent_completion(session)

            # Auto-dispatch pending follow-ups
            pending = session.get('pending_followups', [])
            if pending:
                session['_dispatching_followup'] = True
                followup_msg = pending.pop(0)
                _auto_dispatch_followup(session, followup_msg)
                session.pop('_dispatching_followup', None)


def _auto_approve_plan_b(session):
    """For Mode B: send a follow-up message via stdin to break out of plan mode."""
    proc = session.get('proc')
    if not proc or not session.get('process_alive'):
        return
    approval_msg = (
        'Plan approved. Proceed with implementation immediately. '
        'Do NOT call EnterPlanMode or ExitPlanMode again.'
    )
    try:
        payload = json.dumps({
            'type': 'user',
            'message': {'role': 'user', 'content': approval_msg}
        }) + '\n'
        with session.get('stdin_lock', threading.Lock()):
            proc.stdin.write(payload)
            proc.stdin.flush()
    except Exception:
        pass


def _read_agent_stream_b(proc, session):
    """Reader thread for Mode B: persistent process with stream-json I/O.

    Unlike Mode A, the process does NOT exit after each turn.
    A 'result' message signals the end of a turn, not the end of the process.
    """
    my_proc = proc
    try:
        for raw_line in proc.stdout:
            if session.get('proc') is not my_proc:
                break
            line = raw_line.rstrip('\n\r')
            if not line:
                continue
            try:
                msg = json.loads(line)
                msg_type = msg.get('type', '')
                if 'session_id' in msg:
                    session['claude_session_id'] = msg['session_id']
                if msg_type == 'assistant' and 'message' in msg:
                    for block in msg['message'].get('content', []):
                        if block.get('type') == 'text':
                            session['log_lines'].append(block['text'])
                        elif block.get('type') == 'tool_use':
                            tool_name = block.get('name', '')
                            tool_input = block.get('input', {})
                            activity = _format_tool_activity(tool_name, tool_input)
                            session['log_lines'].append(activity)
                            if tool_name in ('Write', 'Edit'):
                                fp = tool_input.get('file_path', '')
                                if fp.lower().endswith('.md'):
                                    session['_last_md_file'] = fp
                            elif tool_name == 'ExitPlanMode':
                                if session.get('_last_md_file'):
                                    session['plan_file'] = session['_last_md_file']
                                # Auto-approve: send follow-up so agent isn't stuck
                                session['log_lines'].append('[Plan mode exit detected — auto-approving]')
                                _auto_approve_plan_b(session)
                            elif tool_name == 'AskUserQuestion':
                                session.setdefault('pending_questions', []).append(tool_input)
                elif msg_type == 'result':
                    if 'session_id' in msg:
                        session['claude_session_id'] = msg['session_id']
                    if 'usage' in msg:
                        session['usage'] = msg['usage']
                    if 'cost_usd' in msg:
                        session['cost_usd'] = msg['cost_usd']
                    if 'num_turns' in msg:
                        session['num_turns'] = msg['num_turns']
                    # Turn boundary — process stays alive
                    session['status'] = 'idle'
            except json.JSONDecodeError:
                session['log_lines'].append(line)
            # Cap log_lines to prevent unbounded memory growth
            if len(session['log_lines']) > 2000:
                session['log_lines'] = session['log_lines'][-1500:]
    except Exception as e:
        if session.get('proc') is my_proc:
            session['log_lines'].append(f"[stream error: {e}]")
    finally:
        rc = proc.wait()
        _unregister_process(proc.pid)
        session['process_alive'] = False
        if session.get('proc') is my_proc:
            if session['status'] in ('running', 'idle'):
                session['status'] = 'completed' if rc == 0 else 'error'
                if rc != 0:
                    session['log_lines'].append(f"[exited with code {rc}]")
            _log_agent_completion(session)


def _log_agent_activity(project_id, msg):
    """Add an entry to the project's activity_log."""
    p = load_project(project_id)
    if not p:
        return
    log = p.setdefault('activity_log', [])
    log.insert(0, {'ts': now_iso(), 'msg': msg})
    p['activity_log'] = log[:20]
    p['last_updated'] = now_iso()
    save_project(project_id, p)


# ── GitHub sync module ───────────────────────────────────────────────────────
import github_sync as _gh_sync
_gh_sync.register(_POPEN_FLAGS, _STARTUPINFO,
                   _log_agent_activity, load_project, save_project, now_iso)


def _load_agent_log(project_id):
    """Load the agent summary log for a project."""
    filepath = DATA_DIR / f'{project_id}_agent_log.json'
    if not filepath.exists():
        return []
    try:
        return json.loads(filepath.read_text(encoding='utf-8'))
    except Exception:
        return []


def _save_agent_log(project_id, log):
    filepath = DATA_DIR / f'{project_id}_agent_log.json'
    filepath.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding='utf-8')


def _log_agent_completion(session):
    """Save a summary entry when an agent session finishes."""
    project_id = session.get('project_id')
    if not project_id:
        return

    # Skip memory append and condense for housekeeping sessions (prevents circular triggers)
    is_housekeeping = session.get('housekeeping', False)

    # Take the last non-empty text block as the summary
    lines = session.get('log_lines', [])
    # Find the last substantial text (skip tool/status markers)
    summary = ''
    for line in reversed(lines):
        if line and not line.startswith('[') and not line.startswith('\n---'):
            summary = line
            break
    if not summary and lines:
        summary = lines[-1]

    entry = {
        'ts': now_iso(),
        'task': session.get('task', ''),
        'status': session.get('status', 'unknown'),
        'summary': summary[:2000],
        'session_id': session.get('session_id', ''),
        'claude_session_id': session.get('claude_session_id', ''),
        'started_at': session.get('started_at', ''),
        'usage': session.get('usage', {}),
        'cost_usd': session.get('cost_usd', 0),
        'num_turns': session.get('num_turns', 0),
        'plan_file': session.get('plan_file', ''),
    }
    log = _load_agent_log(project_id)
    log.insert(0, entry)
    _save_agent_log(project_id, log)

    if is_housekeeping:
        return

    # Auto-append session summary to project memory (native Claude MEMORY.md)
    if session.get('status') == 'completed' and summary:
        try:
            p = load_project(project_id)
            if p:
                task = session.get('task', '').strip()
                ts = entry['ts'][:10]  # date only
                brief = summary[:300].replace('\n', ' ').strip()
                mem_entry = f"- [{ts}] **{task[:80]}** — {brief}"
                mem_path = _get_memory_path(p)
                mem_path.parent.mkdir(parents=True, exist_ok=True)
                existing = ''
                if mem_path.exists():
                    existing = mem_path.read_text(encoding='utf-8').rstrip()
                header = '## Session Log'
                if header not in existing:
                    existing = existing + f'\n\n{header}' if existing else header
                new_content = existing + '\n' + mem_entry + '\n'
                mem_path.write_text(new_content, encoding='utf-8')

                # Archive overflow: if file exceeds 10KB, keep last 20 entries, archive the rest
                if len(new_content.encode('utf-8')) > 10 * 1024:
                    marker = '## Session Log'
                    idx = new_content.find(marker)
                    if idx >= 0:
                        before = new_content[:idx]
                        log_section = new_content[idx + len(marker):]
                        entries = [l for l in log_section.strip().splitlines() if l.startswith('- [')]
                        if len(entries) > 20:
                            overflow = entries[:-20]
                            kept = entries[-20:]
                            # Append overflow to archive
                            archive_path = _get_archive_path(p)
                            archive_path.parent.mkdir(parents=True, exist_ok=True)
                            archive_existing = ''
                            if archive_path.exists():
                                archive_existing = archive_path.read_text(encoding='utf-8').rstrip()
                            archive_header = '## Archived Session Log'
                            if archive_header not in archive_existing:
                                archive_existing = (archive_existing + f'\n\n{archive_header}'
                                                    if archive_existing else archive_header)
                            archive_path.write_text(
                                archive_existing + '\n' + '\n'.join(overflow) + '\n',
                                encoding='utf-8',
                            )
                            # Rewrite MEMORY.md with only kept entries
                            mem_path.write_text(
                                before.rstrip() + '\n\n' + marker + '\n' + '\n'.join(kept) + '\n',
                                encoding='utf-8',
                            )

                # Trigger condensation if thresholds met
                if _should_condense(p):
                    _dispatch_condense(p)
        except Exception:
            pass  # never fail the completion flow for memory


def _auto_dispatch_followup(session, message):
    """Auto-dispatch a queued follow-up after the current task completes."""
    project_id = session.get('project_id')
    p = load_project(project_id)
    if not p:
        session['log_lines'].append('[follow-up skipped: project not found]')
        return
    pp = p.get('project_path', '')
    if not pp or not Path(pp).is_dir():
        session['log_lines'].append('[follow-up skipped: project path invalid]')
        return

    claude_sid = session.get('claude_session_id')
    if claude_sid:
        resume_flags = ['-r', claude_sid]
    else:
        resume_flags = ['--continue']

    cmd = ['claude', *resume_flags, '-p', message, *_build_claude_flags(p)]

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
    except Exception as e:
        session['log_lines'].append(f'[follow-up failed: {e}]')
        return

    threading.Thread(target=_hide_windows_delayed, args=(proc.pid,), daemon=True).start()
    old_proc = session.get('proc')
    if old_proc:
        _unregister_process(old_proc.pid)
    session['proc'] = proc
    session['status'] = 'running'
    _register_process(proc, 'Agent followup (A)', 'agent',
                      session['session_id'], session['project_id'], message[:80])
    user_label = CONFIG.get('user_name') or 'User'
    session['log_lines'].append(f"> {user_label}: {message}")

    t = threading.Thread(target=_read_agent_stream, args=(proc, session), daemon=True)
    t.start()


def _check_context_budget(project, appended_prompt):
    """Measure context files and return a warning string if total exceeds 20KB."""
    sizes = {}
    pp = project.get('project_path', '')
    # CLAUDE.md in project root
    if pp:
        claude_md = Path(pp) / 'CLAUDE.md'
        if claude_md.exists():
            try:
                sizes['CLAUDE.md'] = claude_md.stat().st_size
            except OSError:
                pass
    # MEMORY.md (native path)
    mem_path = _get_memory_path(project)
    if mem_path and mem_path.exists():
        try:
            sizes['MEMORY.md'] = mem_path.stat().st_size
        except OSError:
            pass
    sizes['prompt'] = len(appended_prompt.encode('utf-8'))
    total = sum(sizes.values())
    if total > 20 * 1024:
        parts = ', '.join(f'{k}: {v/1024:.1f}k' for k, v in sizes.items())
        tokens_est = total / 4  # rough char-to-token ratio
        return f'[context warning] Total context: ~{tokens_est/1000:.1f}k tokens ({parts}). Consider trimming large files.'
    return None


def _dispatch_condense(project):
    """Launch a housekeeping agent to condense memory for a project."""
    pid = project['id']
    with _condense_lock:
        if pid in _condensing_projects:
            return
        _condensing_projects.add(pid)

    mem_path = _get_memory_path(project)
    archive_path = _get_archive_path(project)
    pp = project.get('project_path', '')

    prompt = (
        "You are a memory housekeeping agent. Your ONLY job is to condense the project memory files.\n\n"
        f"1. Read {mem_path}\n"
        f"2. Read {archive_path} (if it exists)\n"
        "3. Preserve ALL curated/manually-written sections (anything NOT under '## Session Log') verbatim.\n"
        "4. Extract useful insights from session log entries and fold them into organized knowledge sections "
        "(e.g., ## Architecture, ## Patterns, ## Gotchas). Merge with existing sections if present.\n"
        "5. Keep only the last 5 session log entries in the Session Log section.\n"
        f"6. Write the condensed result back to {mem_path}. Target: under 8KB total.\n"
        f"7. Delete {archive_path} when done (if it exists).\n"
        "8. Do NOT create any other files. Do NOT modify any code. Only touch the two memory files above."
    )

    model = CONFIG.get('condense_model', '') or 'sonnet'
    cmd = ['claude', '-p', prompt, '--model', model, '--max-turns', '5',
           '--print', '--verbose', '--output-format', 'stream-json',
           '--dangerously-skip-permissions']

    cwd = pp if pp and Path(pp).is_dir() else str(Path.home())

    def _run():
        session_id = f'condense_{uuid.uuid4().hex[:8]}'
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=cwd,
                text=True,
                encoding='utf-8',
                errors='replace',
                creationflags=_POPEN_FLAGS,
                startupinfo=_STARTUPINFO,
            )
            threading.Thread(target=_hide_windows_delayed, args=(proc.pid,), daemon=True).start()
            _register_process(proc, 'Housekeeping (condense)', 'housekeeping',
                              session_id, pid, 'Memory condensation')

            session = {
                'proc': proc,
                'status': 'running',
                'task': 'Memory condensation',
                'log_lines': [],
                'started_at': now_iso(),
                'session_id': session_id,
                'project_id': pid,
                'mode': 'A',
                'housekeeping': True,
            }
            with agent_lock:
                agent_sessions[session_id] = session

            # Reuse existing stream reader
            _read_agent_stream(proc, session)
        except Exception as e:
            print(f"[condense] error for {pid}: {e}")
        finally:
            with _condense_lock:
                _condensing_projects.discard(pid)

    threading.Thread(target=_run, daemon=True).start()


def _dispatch_agent_internal(project_id, task, resume_id=''):
    """Core dispatch logic shared by HTTP endpoint and scheduler.

    Returns session_id on success, raises ValueError on error.
    """
    p = load_project(project_id)
    if not p:
        raise ValueError('project not found')

    pp = p.get('project_path', '')
    if not pp or not Path(pp).is_dir():
        raise ValueError('project_path not set or invalid')

    use_streaming = CONFIG.get('use_streaming_agent', False)

    # Check session transcript size — auto-start fresh if too large
    original_resume = resume_id
    if resume_id:
        too_large, size_bytes = _session_too_large(pp, resume_id)
        if too_large:
            size_mb = size_bytes / (1024 * 1024)
            print(f"[dispatch] Session {resume_id} transcript is {size_mb:.1f} MB — starting fresh")
            _log_agent_activity(project_id,
                                f"Auto-fresh: previous session too large ({size_mb:.0f} MB)")
            # Prepend context about the previous session
            task = (f"[Continuing from a previous conversation (session {resume_id}) that grew too large "
                    f"to resume ({size_mb:.0f} MB). Start fresh but continue the user's request below.]\n\n{task}")
            resume_id = ''

    with agent_lock:
        session_id = uuid.uuid4().hex[:12]

        if use_streaming:
            # Mode B: persistent process with stream-json stdin
            if resume_id:
                cmd = ['claude', '-r', resume_id, *_build_claude_flags(p, streaming=True)]
            else:
                context = _build_agent_context(p)
                cmd = ['claude', *_build_claude_flags(p, streaming=True),
                       '--append-system-prompt', context]

            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=pp,
                text=True,
                encoding='utf-8',
                errors='replace',
                creationflags=_POPEN_FLAGS,
                startupinfo=_STARTUPINFO,
            )

            # Send initial message via stdin JSON
            initial_msg = json.dumps({
                "type": "user",
                "message": {"role": "user", "content": task}
            }) + '\n'
            proc.stdin.write(initial_msg)
            proc.stdin.flush()

            threading.Thread(target=_hide_windows_delayed, args=(proc.pid,), daemon=True).start()
            _register_process(proc, 'Agent (Mode B)', 'agent',
                              session_id, project_id, task[:80])

            session = {
                'proc': proc,
                'status': 'running',
                'task': task,
                'log_lines': [],
                'started_at': now_iso(),
                'session_id': session_id,
                'project_id': project_id,
                'mode': 'B',
                'stdin_lock': threading.Lock(),
                'process_alive': True,
            }
            agent_sessions[session_id] = session

            t = threading.Thread(target=_read_agent_stream_b, args=(proc, session), daemon=True)
            t.start()
        else:
            # Mode A: spawn-per-turn (existing behavior)
            if resume_id:
                cmd = ['claude', '-r', resume_id, '-p', task, *_build_claude_flags(p)]
            else:
                context = _build_agent_context(p)
                cmd = ['claude', '-p', task, *_build_claude_flags(p),
                       '--append-system-prompt', context]

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
            _register_process(proc, 'Agent (Mode A)', 'agent',
                              session_id, project_id, task[:80])

            session = {
                'proc': proc,
                'status': 'running',
                'task': task,
                'log_lines': [],
                'started_at': now_iso(),
                'session_id': session_id,
                'project_id': project_id,
                'mode': 'A',
            }
            agent_sessions[session_id] = session

            t = threading.Thread(target=_read_agent_stream, args=(proc, session), daemon=True)
            t.start()

        # Context budget warning (only for new sessions, not resumes)
        if not resume_id:
            warning = _check_context_budget(p, context)
            if warning:
                session['log_lines'].append(warning)

        # Notify user if session was auto-started fresh due to transcript size
        if original_resume and not resume_id:
            session['log_lines'].append(
                f'[Session transcript too large ({size_mb:.0f} MB) — starting fresh]')

    resume_label = f" (resuming {resume_id})" if resume_id else ""
    try:
        print(f"[dispatch] cmd: {' '.join(cmd)}")
    except (UnicodeEncodeError, UnicodeDecodeError):
        print(f"[dispatch] cmd: {' '.join(cmd).encode('ascii', 'replace').decode()}")
    _log_agent_activity(project_id, f"Agent dispatched{resume_label}: {task[:100]}")
    return session_id


@app.route('/api/project/<project_id>/agent/dispatch', methods=['POST'])
def agent_dispatch(project_id):
    data = request.get_json() or {}
    task = data.get('task', '').strip()
    if not task:
        return jsonify({'error': 'task required'}), 400
    resume_id = data.get('resume_conversation_id', '').strip()
    try:
        session_id = _dispatch_agent_internal(project_id, task, resume_id)
    except ValueError as e:
        code = 404 if 'not found' in str(e) else 400
        return jsonify({'error': str(e)}), code
    except FileNotFoundError:
        return jsonify({'error': 'Claude CLI not found. Install it with: npm install -g @anthropic-ai/claude-code'}), 500
    except Exception as e:
        return jsonify({'error': f'dispatch failed: {e}'}), 500
    return jsonify({'ok': True, 'session_id': session_id})


@app.route('/api/project/<project_id>/agent/stream')
def agent_stream(project_id):
    """SSE endpoint streaming agent output for a specific session."""
    session_id = request.args.get('session', '')
    since = request.args.get('since', '0')

    def generate():
        session = agent_sessions.get(session_id)
        if not session or session['project_id'] != project_id:
            yield f"data: {json.dumps({'type': 'error', 'msg': 'no active session'})}\n\n"
            return

        is_mode_b = session.get('mode') == 'B'
        sent = int(since) if since.isdigit() else 0
        tick = 0
        idle_sent = False  # track whether we've sent turn_complete for current idle
        while True:
            lines = session['log_lines']
            if sent < len(lines):
                for line in lines[sent:]:
                    yield f"data: {json.dumps({'type': 'output', 'text': line})}\n\n"
                sent = len(lines)

            # Send pending AskUserQuestion data
            pqs = session.get('pending_questions')
            if pqs:
                for pq in pqs:
                    yield f"data: {json.dumps({'type': 'question', 'questions': pq.get('questions', [])})}\n\n"
                session['pending_questions'] = []

            status = session['status']

            if is_mode_b:
                if status == 'idle' and not idle_sent:
                    # Turn finished but process is still alive
                    yield f"data: {json.dumps({'type': 'turn_complete', 'status': 'idle', 'usage': session.get('usage', {}), 'cost_usd': session.get('cost_usd', 0), 'num_turns': session.get('num_turns', 0)})}\n\n"
                    idle_sent = True
                elif status == 'running':
                    idle_sent = False  # reset for next turn
                elif status not in ('running', 'idle'):
                    # Process actually exited — terminal status
                    yield f"data: {json.dumps({'type': 'status', 'status': status, 'usage': session.get('usage', {}), 'cost_usd': session.get('cost_usd', 0), 'num_turns': session.get('num_turns', 0)})}\n\n"
                    break
            else:
                # Mode A: existing behavior
                if status != 'running':
                    if not session.get('pending_followups') and not session.get('_dispatching_followup'):
                        yield f"data: {json.dumps({'type': 'status', 'status': status, 'usage': session.get('usage', {}), 'cost_usd': session.get('cost_usd', 0), 'num_turns': session.get('num_turns', 0)})}\n\n"
                        break

            # Heartbeat every ~15s to keep connection alive
            tick += 1
            if tick % 50 == 0:
                yield ": heartbeat\n\n"

            _time.sleep(0.3)

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/api/project/<project_id>/agent/followup', methods=['POST'])
def agent_followup(project_id):
    p = load_project(project_id)
    if not p:
        return jsonify({'error': 'project not found'}), 404

    pp = p.get('project_path', '')
    if not pp or not Path(pp).is_dir():
        return jsonify({'error': 'project_path not set'}), 400

    data = request.get_json() or {}
    message = data.get('message', '').strip()
    session_id = data.get('session_id', '')
    if not message:
        return jsonify({'error': 'message required'}), 400
    if not session_id:
        return jsonify({'error': 'session_id required'}), 400

    with agent_lock:
        existing = agent_sessions.get(session_id)
        if not existing or existing['project_id'] != project_id:
            return jsonify({'error': 'session not found'}), 404

        if existing.get('mode') == 'B':
            # Mode B: write directly to persistent process stdin
            if not existing.get('process_alive'):
                # Process died (hard stop or crash) — respawn with claude -r
                claude_sid = existing.get('claude_session_id')
                if not claude_sid:
                    return jsonify({'error': 'no session to resume'}), 400

                # Check transcript size before resuming
                too_large, size_bytes = _session_too_large(pp, claude_sid)
                resume_flags = ['-r', claude_sid]
                if too_large:
                    size_mb = size_bytes / (1024 * 1024)
                    print(f"[followup] Session {claude_sid} is {size_mb:.1f} MB — starting fresh")
                    _log_agent_activity(project_id,
                                        f"Auto-fresh: session too large ({size_mb:.0f} MB)")
                    existing['log_lines'].append(
                        f'[Session transcript too large ({size_mb:.0f} MB) — starting fresh]')
                    resume_flags = []
                    context = _build_agent_context(p)
                    message = (f"[Continuing from a previous conversation that grew too large "
                               f"to resume ({size_mb:.0f} MB). Start fresh.]\n\n{message}")

                user_label = CONFIG.get('user_name') or 'User'
                existing['log_lines'].append(f"\n> {user_label}: {message}\n")
                existing['status'] = 'running'

                cmd = ['claude', *resume_flags,
                       *_build_claude_flags(p, streaming=True)]
                if not resume_flags:
                    cmd.extend(['--append-system-prompt', context])
                proc = subprocess.Popen(
                    cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, cwd=pp,
                    text=True, encoding='utf-8', errors='replace',
                    creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
                )
                threading.Thread(target=_hide_windows_delayed,
                                 args=(proc.pid,), daemon=True).start()
                old_proc = existing.get('proc')
                if old_proc:
                    _unregister_process(old_proc.pid)
                _register_process(proc, 'Agent respawn (B)', 'agent',
                                  session_id, project_id, message[:80])

                existing['proc'] = proc
                existing['process_alive'] = True
                existing['stdin_lock'] = threading.Lock()

                threading.Thread(target=_read_agent_stream_b,
                                 args=(proc, existing), daemon=True).start()

                # Send message to stdin
                stdin_msg = json.dumps({
                    "type": "user",
                    "message": {"role": "user", "content": message}
                }) + '\n'
                def _write_initial():
                    lock = existing['stdin_lock']
                    with lock:
                        try:
                            proc.stdin.write(stdin_msg)
                            proc.stdin.flush()
                        except Exception as e:
                            existing['log_lines'].append(f'[stdin write error: {e}]')
                            existing['status'] = 'error'
                            existing['process_alive'] = False
                threading.Thread(target=_write_initial, daemon=True).start()

                _log_agent_activity(project_id, f"Agent resumed: {message[:100]}")
                return jsonify({'ok': True, 'session_id': session_id, 'resumed': True})

            user_label = CONFIG.get('user_name') or 'User'
            existing['log_lines'].append(f"\n> {user_label}: {message}\n")
            existing['status'] = 'running'

            stdin_msg = json.dumps({
                "type": "user",
                "message": {"role": "user", "content": message}
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
                    existing['process_alive'] = False
                finally:
                    if lock:
                        lock.release()

            threading.Thread(target=_write_stdin, daemon=True).start()
            _log_agent_activity(project_id, f"Agent follow-up: {message[:100]}")
            return jsonify({'ok': True, 'session_id': session_id})

        # Mode A: existing behavior
        # If agent is still running, queue the follow-up instead of killing
        if existing['status'] == 'running':
            pending = existing.setdefault('pending_followups', [])
            pending.append(message)
            user_label = CONFIG.get('user_name') or 'User'
            existing['log_lines'].append(f"> [queued] {user_label}: {message}")
            _log_agent_activity(project_id, f"Agent follow-up queued: {message[:100]}")
            return jsonify({'ok': True, 'queued': True, 'session_id': session_id})

        # Mark as running and return quickly — spawn process in background
        existing['status'] = 'running'
        user_label = CONFIG.get('user_name') or 'User'
        existing['log_lines'].append(f"\n> {user_label}: {message}\n")
        claude_sid = existing.get('claude_session_id')

    # Spawn process outside the lock to avoid blocking other requests
    def _start_followup():
        followup_msg = message
        if claude_sid:
            too_large, size_bytes = _session_too_large(pp, claude_sid)
            if too_large:
                size_mb = size_bytes / (1024 * 1024)
                print(f"[followup-A] Session {claude_sid} is {size_mb:.1f} MB — starting fresh")
                _log_agent_activity(project_id,
                                    f"Auto-fresh: session too large ({size_mb:.0f} MB)")
                with agent_lock:
                    existing['log_lines'].append(
                        f'[Session transcript too large ({size_mb:.0f} MB) — starting fresh]')
                context = _build_agent_context(p)
                followup_msg = (f"[Continuing from a previous conversation that grew too large "
                                f"to resume ({size_mb:.0f} MB). Start fresh.]\n\n{message}")
                resume_flags = []
            else:
                resume_flags = ['-r', claude_sid]
        else:
            resume_flags = ['--continue']
        cmd = ['claude', *resume_flags, '-p', followup_msg, *_build_claude_flags(p)]
        if not resume_flags:
            cmd.extend(['--append-system-prompt', context])
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
        old_proc = existing.get('proc')
        if old_proc:
            _unregister_process(old_proc.pid)
        existing['proc'] = proc
        _register_process(proc, 'Agent followup (A)', 'agent',
                          session_id, project_id, followup_msg[:80])
        threading.Thread(target=_read_agent_stream, args=(proc, existing), daemon=True).start()

    threading.Thread(target=_start_followup, daemon=True).start()

    _log_agent_activity(project_id, f"Agent follow-up: {message[:100]}")
    return jsonify({'ok': True, 'session_id': session_id})


@app.route('/api/project/<project_id>/agent/stop', methods=['POST'])
def agent_stop(project_id):
    data = request.get_json() or {}
    session_id = data.get('session_id', '')
    if not session_id:
        return jsonify({'error': 'session_id required'}), 400

    with agent_lock:
        session = agent_sessions.get(session_id)
        if not session or session['project_id'] != project_id:
            return jsonify({'error': 'session not found'}), 404
        if session['status'] not in ('running', 'idle'):
            return jsonify({'error': 'agent not running'}), 400
        proc = session['proc']
        # Kill process for both modes
        if session.get('mode') == 'B':
            try:
                proc.stdin.close()
            except Exception:
                pass
            session['process_alive'] = False
        try:
            proc.kill()
        except Exception:
            pass
        _unregister_process(proc.pid)
        session['status'] = 'stopped'
        session['log_lines'].append('[Agent stopped by user]')

    # Wait for process to exit
    try:
        proc.wait(timeout=5)
    except Exception:
        pass

    _log_agent_activity(project_id, "Agent stopped by user")
    return jsonify({'ok': True})


@app.route('/api/project/<project_id>/agent/session', methods=['DELETE', 'POST'])
def agent_session_delete(project_id):
    """Kill process (if running), wait for exit, and remove session entirely.
    Accepts POST in addition to DELETE for navigator.sendBeacon compatibility."""
    data = request.get_json(force=True, silent=True) or {}
    session_id = data.get('session_id', '')
    if not session_id:
        return jsonify({'error': 'session_id required'}), 400

    proc = None
    with agent_lock:
        session = agent_sessions.get(session_id)
        if not session or session['project_id'] != project_id:
            return jsonify({'ok': True})  # Already gone — idempotent
        if session['status'] in ('running', 'idle'):
            proc = session['proc']
            if session.get('mode') == 'B':
                try:
                    proc.stdin.close()
                except Exception:
                    pass
                session['process_alive'] = False
            try:
                proc.kill()
            except Exception:
                pass
            _unregister_process(proc.pid)
            session['status'] = 'stopped'
            session['log_lines'].append('[Agent stopped — tab closed]')

    # Wait outside lock for process to fully exit
    if proc:
        try:
            proc.wait(timeout=5)
        except Exception:
            pass

    # Remove session from tracking.
    # The stream reader thread has already called _log_agent_completion()
    # in its finally block after proc.wait(), so usage data is persisted.
    with agent_lock:
        agent_sessions.pop(session_id, None)

    return jsonify({'ok': True})


@app.route('/api/project/<project_id>/agent/plan-file')
def agent_plan_file(project_id):
    """Read and return the plan .md file content for a session."""
    session_id = request.args.get('session', '')
    session = agent_sessions.get(session_id)
    if not session or session['project_id'] != project_id:
        return jsonify({'error': 'session not found'}), 404
    plan_path = session.get('plan_file', '')
    if not plan_path:
        return jsonify({'error': 'no plan file'}), 404
    p = Path(plan_path)
    if not p.is_file():
        return jsonify({'error': 'file not found'}), 404
    try:
        content = p.read_text(encoding='utf-8')
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify({'path': str(p), 'filename': p.name, 'content': content})


@app.route('/api/plan-file')
def read_plan_file():
    """Read a plan file by path (for plan history viewer)."""
    plan_path = request.args.get('path', '')
    if not plan_path:
        return jsonify({'error': 'path required'}), 400
    p = Path(plan_path)
    # Security: only allow reading from ~/.claude/plans/
    plans_dir = Path.home() / '.claude' / 'plans'
    try:
        p.resolve().relative_to(plans_dir.resolve())
    except ValueError:
        return jsonify({'error': 'access denied'}), 403
    if not p.is_file():
        return jsonify({'error': 'file not found'}), 404
    try:
        content = p.read_text(encoding='utf-8')
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify({'path': str(p), 'filename': p.name, 'content': content})


@app.route('/api/plans/delete', methods=['POST'])
def delete_plans():
    """Delete plan files from disk and scrub references from agent logs."""
    data = request.get_json(force=True) or {}
    paths = data.get('paths', [])
    if not paths or not isinstance(paths, list):
        return jsonify({'error': 'paths array required'}), 400
    plans_dir = Path.home() / '.claude' / 'plans'
    resolved_plans_dir = plans_dir.resolve()
    deleted = 0
    deleted_paths = set()
    for plan_path in paths:
        p = Path(plan_path)
        try:
            if not p.resolve().is_relative_to(resolved_plans_dir):
                continue
        except Exception:
            continue
        if p.is_file():
            try:
                p.unlink()
                deleted += 1
            except Exception:
                pass
        deleted_paths.add(str(p))
    # Scrub plan_file references from all agent logs
    if deleted_paths:
        for log_file in DATA_DIR.glob('*_agent_log.json'):
            try:
                log = json.loads(log_file.read_text(encoding='utf-8'))
                changed = False
                for entry in log:
                    if entry.get('plan_file', '') in deleted_paths:
                        entry['plan_file'] = ''
                        changed = True
                if changed:
                    log_file.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding='utf-8')
            except Exception:
                pass
    return jsonify({'ok': True, 'deleted': deleted})


@app.route('/api/project/<project_id>/agent/status')
def agent_status(project_id):
    sessions = []
    for sid, s in agent_sessions.items():
        if s['project_id'] == project_id:
            sessions.append({
                'session_id': s['session_id'],
                'claude_session_id': s.get('claude_session_id', ''),
                'status': s['status'],
                'task': s['task'],
                'log_lines': [l for l in s['log_lines']
                              if not l.startswith('[terminal:')
                              or l.split(':')[1] in terminal_sessions],
                'started_at': s['started_at'],
                'plan_file': s.get('plan_file', ''),
                'usage': s.get('usage', {}),
                'cost_usd': s.get('cost_usd', 0),
                'num_turns': s.get('num_turns', 0),
                'mode': s.get('mode', 'A'),
            })
    # Sort: running first, then newest first (ISO timestamps sort lexically)
    sessions.sort(key=lambda s: (
        0 if s['status'] == 'running' else 1,
        '~' if not s.get('started_at') else s['started_at']
    ), reverse=False)
    # Within each group, newest first
    sessions.sort(key=lambda s: s.get('started_at', ''), reverse=True)
    sessions.sort(key=lambda s: 0 if s['status'] in ('running', 'idle') else 1)
    return jsonify({'sessions': sessions})


# ── Terminal session management ───────────────────────────────────────────────

def _read_terminal_stream(proc, session):
    """Reader thread: captures stdout chunks into terminal session output_lines.

    Uses raw chunk reads (not line-by-line) to preserve ANSI escape sequences
    like cursor movement, screen clearing, and Rich Live display updates.
    """
    my_proc = proc
    fd = proc.stdout.fileno()
    try:
        while True:
            if session.get('proc') is not my_proc:
                break
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            text = chunk.decode('utf-8', errors='replace')
            session['output_lines'].append(text)
            # Cap to prevent unbounded memory growth
            if len(session['output_lines']) > 5000:
                session['output_lines'] = session['output_lines'][-3000:]
    except Exception as e:
        if session.get('proc') is my_proc:
            session['output_lines'].append(f'[stream error: {e}]')
    finally:
        rc = proc.wait()
        _unregister_process(proc.pid)
        if session.get('proc') is my_proc:
            session['exit_code'] = rc
            if session['status'] == 'running':
                session['status'] = 'completed' if rc == 0 else 'error'
                session['output_lines'].append(f'\r\n[Process exited with code {rc}]')


def _kill_terminal_session(session):
    """Kill a terminal session's subprocess."""
    proc = session.get('proc')
    if not proc:
        return
    try:
        proc.stdin.close()
    except Exception:
        pass
    try:
        proc.kill()
    except Exception:
        pass
    _unregister_process(proc.pid)
    try:
        proc.wait(timeout=5)
    except Exception:
        pass


# Resolve path to mc_tty_shim directory (contains sitecustomize.py)
_TTY_SHIM_DIR = str(_APP_DIR / 'mc_tty_shim')


@app.route('/api/terminal/launch', methods=['POST'])
def terminal_launch():
    """Launch a command in a terminal session.  Called by agents via curl."""
    data = request.get_json() or {}
    project_id = data.get('project_id', '').strip()
    command = data.get('command', '').strip()
    if not project_id or not command:
        return jsonify({'error': 'project_id and command required'}), 400

    p = load_project(project_id)
    if not p:
        return jsonify({'error': 'project not found'}), 404

    pp = p.get('project_path', '')
    cwd = pp if pp and Path(pp).is_dir() else None

    session_id = uuid.uuid4().hex[:12]
    # TTY shim: inject sitecustomize.py via PYTHONPATH so child Python
    # processes see isatty()=True and Rich emits ANSI color codes
    existing_pypath = os.environ.get('PYTHONPATH', '')
    shim_pypath = _TTY_SHIM_DIR + os.pathsep + existing_pypath if existing_pypath else _TTY_SHIM_DIR
    env = {
        **os.environ,
        'PYTHONIOENCODING': 'utf-8',
        'PYTHONUNBUFFERED': '1',
        'MC_FORCE_TTY': '1',
        'PYTHONPATH': shim_pypath,
        'TERM': 'xterm-256color',
        'COLUMNS': '120',
        'LINES': '30',
    }

    try:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=cwd,
            shell=True,
            creationflags=_POPEN_FLAGS,
            startupinfo=_STARTUPINFO,
            env=env,
        )
    except Exception as e:
        return jsonify({'error': f'Failed to launch: {e}'}), 500

    session = {
        'proc': proc,
        'status': 'running',
        'command': command,
        'output_lines': [],
        'started_at': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'session_id': session_id,
        'project_id': project_id,
        'exit_code': None,
    }

    _register_process(proc, 'Terminal', 'terminal',
                      session_id, project_id, command[:80])

    with terminal_lock:
        terminal_sessions[session_id] = session

    threading.Thread(target=_read_terminal_stream, args=(proc, session), daemon=True).start()

    # Notify any active agent SSE streams for this project
    with agent_lock:
        for sid, asess in agent_sessions.items():
            if asess['project_id'] == project_id and asess['status'] in ('running', 'idle'):
                cmd_label = command.replace('\n', ' ').replace('\r', '')[:60]
                asess['log_lines'].append(f'[terminal:{session_id}:{cmd_label}]')

    return jsonify({'ok': True, 'session_id': session_id})


@app.route('/api/terminal/stream')
def terminal_stream():
    """SSE endpoint streaming terminal output for a specific session."""
    session_id = request.args.get('session', '')
    since = request.args.get('since', '0')

    def generate():
        session = terminal_sessions.get(session_id)
        if not session:
            yield f"data: {json.dumps({'type': 'error', 'msg': 'no active session'})}\n\n"
            return

        sent = int(since) if since.isdigit() else 0
        tick = 0
        while True:
            lines = session['output_lines']
            if sent < len(lines):
                for line in lines[sent:]:
                    yield f"data: {json.dumps({'type': 'output', 'text': line})}\n\n"
                sent = len(lines)

            status = session['status']
            if status != 'running':
                yield f"data: {json.dumps({'type': 'status', 'status': status, 'exit_code': session.get('exit_code')})}\n\n"
                break

            tick += 1
            if tick % 50 == 0:
                yield ": heartbeat\n\n"

            _time.sleep(0.3)

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/api/terminal/stdin', methods=['POST'])
def terminal_stdin():
    """Write text to a terminal session's stdin."""
    data = request.get_json() or {}
    session_id = data.get('session_id', '').strip()
    text = data.get('text', '')
    if not session_id:
        return jsonify({'error': 'session_id required'}), 400

    session = terminal_sessions.get(session_id)
    if not session or session['status'] != 'running':
        return jsonify({'error': 'session not running'}), 400

    try:
        session['proc'].stdin.write(text.encode('utf-8'))
        session['proc'].stdin.flush()
    except (BrokenPipeError, OSError):
        pass

    return jsonify({'ok': True})


@app.route('/api/terminal/stop', methods=['POST'])
def terminal_stop():
    """Stop (kill) a running terminal session."""
    data = request.get_json() or {}
    session_id = data.get('session_id', '').strip()
    if not session_id:
        return jsonify({'error': 'session_id required'}), 400

    with terminal_lock:
        session = terminal_sessions.get(session_id)
        if not session:
            return jsonify({'error': 'session not found'}), 404
        if session['status'] != 'running':
            return jsonify({'error': 'not running'}), 400
        _kill_terminal_session(session)
        session['status'] = 'stopped'
        session['output_lines'].append('\r\n[Process stopped by user]')

    return jsonify({'ok': True})


@app.route('/api/project/<project_id>/terminal/status')
def terminal_status(project_id):
    """Return running terminal sessions for a project (for reconnection after refresh)."""
    sessions = []
    for sid, s in list(terminal_sessions.items()):
        if s['project_id'] != project_id:
            continue
        # Only return running sessions — completed/stopped are disposable
        if s['status'] == 'running':
            sessions.append({
                'session_id': s['session_id'],
                'status': s['status'],
                'command': s['command'],
                'output_lines': s['output_lines'],
                'started_at': s['started_at'],
                'exit_code': s.get('exit_code'),
            })
        else:
            # Purge non-running sessions from memory
            terminal_sessions.pop(sid, None)
    return jsonify({'sessions': sessions})


@app.route('/api/terminal/delete', methods=['POST'])
def terminal_delete():
    """Kill process (if running) and remove session from memory entirely."""
    data = request.get_json() or {}
    session_id = data.get('session_id', '').strip()
    if not session_id:
        return jsonify({'error': 'session_id required'}), 400

    with terminal_lock:
        session = terminal_sessions.pop(session_id, None)
        if not session:
            return jsonify({'ok': True})  # already gone
        if session['status'] == 'running':
            _kill_terminal_session(session)

    return jsonify({'ok': True})


# ── Process Tracker endpoints ─────────────────────────────────────────────────

@app.route('/api/processes')
def list_processes():
    """Return all tracked processes with live status."""
    result = []
    with process_tracker_lock:
        for pid, entry in tracked_processes.items():
            proc = entry.get('proc')
            if proc is not None:
                alive = proc.poll() is None
                exit_code = proc.poll()
            else:
                # External process — check via OS
                try:
                    os.kill(entry['pid'], 0)
                    alive = True
                except OSError:
                    alive = False
                exit_code = None
            result.append({
                'pid': entry['pid'],
                'name': entry['name'],
                'type': entry['type'],
                'session_id': entry['session_id'],
                'project_id': entry['project_id'],
                'project_name': entry['project_name'],
                'command_preview': entry['command_preview'],
                'started_at': entry['started_at'],
                'alive': alive,
                'exit_code': exit_code,
            })
    result.sort(key=lambda x: (0 if x['alive'] else 1, x.get('started_at', '')))
    return jsonify(result)


@app.route('/api/processes/<int:pid>/kill', methods=['POST'])
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
            try:
                proc.kill()
            except Exception as e:
                return jsonify({'error': f'kill failed: {e}'}), 500
        else:
            # External process — kill via OS
            try:
                os.kill(pid, 9)  # SIGKILL
            except OSError as e:
                tracked_processes.pop(pid, None)
                return jsonify({'ok': True, 'already_dead': True})
        tracked_processes.pop(pid, None)
        session_id = entry.get('session_id', '')
        entry_type = entry.get('type', '')

    # Update corresponding session status (outside tracker lock)
    if entry_type in ('agent', 'housekeeping'):
        with agent_lock:
            session = agent_sessions.get(session_id)
            if session and session['status'] in ('running', 'idle'):
                session['status'] = 'stopped'
                session['log_lines'].append('[Process killed via Process Manager]')
                if session.get('mode') == 'B':
                    session['process_alive'] = False
    elif entry_type == 'terminal':
        with terminal_lock:
            session = terminal_sessions.get(session_id)
            if session and session['status'] == 'running':
                session['status'] = 'stopped'
                session['output_lines'].append('\r\n[Process killed via Process Manager]')

    return jsonify({'ok': True})


@app.route('/api/processes/register', methods=['POST'])
def register_external_process():
    """Register an externally-spawned process (e.g. from an agent)."""
    data = request.get_json() or {}
    pid = data.get('pid')
    name = data.get('name', 'External process')
    project_id = data.get('project_id', '')
    command_preview = data.get('command', '')
    if not pid or not isinstance(pid, int):
        return jsonify({'error': 'pid (integer) required'}), 400
    # Verify PID is actually running
    try:
        os.kill(pid, 0)
    except OSError:
        return jsonify({'error': f'PID {pid} is not running'}), 400
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


@app.route('/api/processes/cleanup', methods=['POST'])
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


# ── Agent log endpoint ────────────────────────────────────────────────────────

@app.route('/api/project/<project_id>/agent/log')
def get_agent_log(project_id):
    log = _load_agent_log(project_id)
    for entry in log:
        entry['ts_relative'] = time_ago(entry.get('ts'))
        entry['started_relative'] = time_ago(entry.get('started_at'))
    return jsonify(log)


@app.route('/api/project/<project_id>/plans')
def get_project_plans(project_id):
    """Return all plan files associated with this project from agent log + live sessions."""
    import re
    log = _load_agent_log(project_id)
    plans = []
    seen = set()

    # Include plans from currently running/idle sessions (not yet in log)
    for sid, s in agent_sessions.items():
        if s['project_id'] != project_id:
            continue
        pf = s.get('plan_file', '')
        if not pf or pf in seen:
            continue
        p = Path(pf)
        if not p.is_file():
            continue
        seen.add(pf)
        try:
            content = p.read_text(encoding='utf-8')
            m = re.match(r'^#\s+(.+)', content, re.MULTILINE)
            title = m.group(1).strip() if m else p.stem
        except Exception:
            title = p.stem
        plans.append({
            'plan_file': pf,
            'filename': p.name,
            'title': title,
            'task': s.get('task', ''),
            'ts': s.get('started_at', ''),
            'ts_relative': time_ago(s.get('started_at')),
            'session_id': s.get('session_id', ''),
        })

    for entry in log:
        pf = entry.get('plan_file', '')
        if not pf or pf in seen:
            continue
        p = Path(pf)
        if not p.is_file():
            continue
        seen.add(pf)
        # Extract first heading from file
        try:
            content = p.read_text(encoding='utf-8')
            m = re.match(r'^#\s+(.+)', content, re.MULTILINE)
            title = m.group(1).strip() if m else p.stem
        except Exception:
            title = p.stem
        plans.append({
            'plan_file': pf,
            'filename': p.name,
            'title': title,
            'task': entry.get('task', ''),
            'ts': entry.get('ts', ''),
            'ts_relative': time_ago(entry.get('ts')),
            'session_id': entry.get('session_id', ''),
        })
    return jsonify(plans)


# ── Usage / token tracking ──────────────────────────────────────────────────

@app.route('/api/usage')
def api_usage():
    """Aggregate token usage across all agent log files and running sessions.

    Optional query param ?since=<ISO timestamp> to filter entries after a cutoff.
    """
    since = request.args.get('since', '')
    total_input = 0
    total_output = 0
    total_cost = 0.0
    total_sessions = 0
    for f in DATA_DIR.glob('*_agent_log.json'):
        try:
            log = json.loads(f.read_text(encoding='utf-8'))
            for entry in log:
                if since and entry.get('ts', '') < since:
                    continue
                usage = entry.get('usage', {})
                total_input += usage.get('input_tokens', 0)
                total_output += usage.get('output_tokens', 0)
                total_cost += entry.get('cost_usd', 0) or 0
                total_sessions += 1
        except Exception:
            continue
    # Include running sessions that haven't been logged yet
    for s in agent_sessions.values():
        if since and s.get('started_at', '') < since:
            continue
        usage = s.get('usage', {})
        total_input += usage.get('input_tokens', 0)
        total_output += usage.get('output_tokens', 0)
        total_cost += s.get('cost_usd', 0) or 0
    return jsonify({
        'input_tokens': total_input,
        'output_tokens': total_output,
        'total_tokens': total_input + total_output,
        'cost_usd': round(total_cost, 4),
        'total_sessions': total_sessions,
    })


# ── Rules endpoints ─────────────────────────────────────────────────────────

def _validate_project_path(pp):
    """Ensure path is under PROJECTS_BASE to prevent traversal."""
    try:
        resolved = Path(pp).resolve()
        return resolved.is_relative_to(PROJECTS_BASE.resolve())
    except Exception:
        return False


@app.route('/api/project/<project_id>/rules')
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


@app.route('/api/project/<project_id>/rules', methods=['PUT'])
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


@app.route('/api/rules/shared')
def get_shared_rules():
    content = ''
    if SHARED_RULES_PATH.exists():
        content = SHARED_RULES_PATH.read_text(encoding='utf-8')
    return jsonify({'shared_rules': content})


@app.route('/api/rules/shared', methods=['PUT'])
def save_shared_rules():
    data = request.get_json() or {}
    content = data.get('shared_rules')
    if content is None:
        return jsonify({'error': 'shared_rules required'}), 400

    SHARED_RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
    SHARED_RULES_PATH.write_text(content, encoding='utf-8')
    return jsonify({'ok': True})


# ── Memory endpoints ────────────────────────────────────────────────────────

@app.route('/api/project/<project_id>/memory')
def get_memory(project_id):
    p = load_project(project_id)
    if not p:
        return jsonify({'error': 'not found'}), 404
    mem_path = _get_memory_path(p)
    content = ''
    if mem_path.exists():
        content = mem_path.read_text(encoding='utf-8')
    return jsonify({'content': content, 'path': str(mem_path)})

@app.route('/api/project/<project_id>/memory', methods=['PUT'])
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

@app.route('/api/project/<project_id>/memory/append', methods=['POST'])
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



# ── Domain settings ─────────────────────────────────────────────────────────

@app.route('/api/settings/domains')
def get_domains():
    settings = _load_settings()
    return jsonify(settings.get('domains', []))

@app.route('/api/settings/domains/add', methods=['POST'])
def add_domain():
    data = request.get_json() or {}
    domain_id = (data.get('id') or '').strip().lower().replace(' ', '_')
    domain_id = ''.join(c for c in domain_id if c.isalnum() or c == '_')
    if not domain_id:
        return jsonify({'error': 'id required'}), 400
    label = data.get('label', domain_id.capitalize())
    color = data.get('color', 'var(--text-dim)')
    bg = data.get('bg', 'var(--surface3)')
    settings = _load_settings()
    domains = settings.get('domains', [])
    if any(d['id'] == domain_id for d in domains):
        return jsonify({'error': 'domain already exists'}), 409
    domains.append({'id': domain_id, 'label': label, 'color': color, 'bg': bg})
    settings['domains'] = domains
    _save_settings(settings)
    return jsonify({'ok': True, 'domain': domains[-1]})

@app.route('/api/settings/domains/<domain_id>', methods=['PATCH'])
def update_domain(domain_id):
    data = request.get_json() or {}
    settings = _load_settings()
    domains = settings.get('domains', [])
    domain = next((d for d in domains if d['id'] == domain_id), None)
    if not domain:
        return jsonify({'error': 'not found'}), 404
    if 'color' in data:
        domain['color'] = data['color']
    if 'bg' in data:
        domain['bg'] = data['bg']
    if 'label' in data:
        domain['label'] = data['label']
    settings['domains'] = domains
    _save_settings(settings)
    return jsonify({'ok': True})

@app.route('/api/settings/domains/<domain_id>', methods=['DELETE'])
def delete_domain(domain_id):
    if domain_id == 'general':
        return jsonify({'error': 'cannot delete general domain'}), 400
    settings = _load_settings()
    domains = settings.get('domains', [])
    before = len(domains)
    domains = [d for d in domains if d['id'] != domain_id]
    if len(domains) == before:
        return jsonify({'error': 'not found'}), 404
    settings['domains'] = domains
    _save_settings(settings)
    return jsonify({'ok': True})


# ── Project order ────────────────────────────────────────────────────────────

@app.route('/api/projects/order', methods=['POST', 'OPTIONS'])
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

@app.route('/api/grid-layout')
def get_grid_layout():
    layout_path = DATA_DIR.parent / 'grid_layout.json'
    if layout_path.exists():
        try:
            return jsonify(json.loads(layout_path.read_text(encoding='utf-8')))
        except Exception:
            pass
    return jsonify({'order': []})


@app.route('/api/list-directory', methods=['POST'])
def list_directory():
    data = request.get_json() or {}
    path = (data.get('path') or '').strip()
    target = Path(path) if path else PROJECTS_BASE
    try:
        target = target.resolve()
    except Exception as e:
        return jsonify({'error': f'Invalid path: {e}'}), 400
    if not target.is_dir():
        return jsonify({'error': f'Not a directory: {target}'}), 400
    try:
        dirs = sorted(
            item.name for item in target.iterdir()
            if item.is_dir() and not item.name.startswith('.')
        )
        return jsonify({
            'path': str(target),
            'parent': str(target.parent) if target.parent != target else None,
            'dirs': dirs,
            'projects_base': str(PROJECTS_BASE),
        })
    except PermissionError:
        return jsonify({'error': f'Permission denied: {target}'}), 403
    except Exception as e:
        return jsonify({'error': f'Failed to list directory: {e}'}), 500


@app.route('/api/create-folder', methods=['POST'])
def create_folder():
    data = request.get_json()
    folder_name = (data or {}).get('name', '').strip()
    parent = (data or {}).get('parent', '').strip()
    if not folder_name:
        return jsonify({'error': 'Folder name is required'}), 400
    # Prevent path traversal in folder name
    if '..' in folder_name or folder_name.startswith(('/', '\\')):
        return jsonify({'error': 'Invalid folder name'}), 400
    base = Path(parent) if parent else PROJECTS_BASE
    if not base.is_dir():
        return jsonify({'error': f'Parent directory does not exist: {base}'}), 400
    target = base / folder_name
    if target.exists():
        return jsonify({'error': 'Folder already exists', 'path': str(target)}), 409
    try:
        target.mkdir(parents=True, exist_ok=False)
    except Exception as e:
        return jsonify({'error': f'Failed to create folder: {e}'}), 500
    return jsonify({'ok': True, 'path': str(target)})


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
    """Compute the next run time for a schedule. Returns ISO string or None."""
    stype = schedule.get('schedule_type', 'once')
    now = datetime.now(timezone.utc)

    if stype == 'once':
        run_at = schedule.get('run_at', '')
        if not run_at:
            return None
        try:
            dt = datetime.fromisoformat(run_at.replace('Z', '+00:00'))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.isoformat().replace('+00:00', 'Z') if dt > now else None
        except Exception:
            return None

    elif stype == 'daily':
        time_str = schedule.get('time', '09:00')
        days = schedule.get('days', [])  # 1=Mon..7=Sun, empty=every day
        try:
            h, m = int(time_str.split(':')[0]), int(time_str.split(':')[1])
        except Exception:
            h, m = 9, 0
        # Try today and next 7 days
        for offset in range(8):
            candidate = now.replace(hour=h, minute=m, second=0, microsecond=0) + timedelta(days=offset)
            if candidate <= now:
                continue
            if days:
                # Python isoweekday: 1=Mon..7=Sun — matches our format
                if candidate.isoweekday() not in days:
                    continue
            return candidate.isoformat().replace('+00:00', 'Z')
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
                if nxt <= now:
                    # Missed window — run now-ish (next tick)
                    nxt = now + timedelta(seconds=5)
                return nxt.isoformat().replace('+00:00', 'Z')
            except Exception:
                pass
        # No last_run — run immediately
        nxt = now + timedelta(seconds=5)
        return nxt.isoformat().replace('+00:00', 'Z')

    elif stype == 'cron':
        expr = schedule.get('cron_expr', '')
        if not expr:
            return None
        nxt = _next_cron_match(expr, now)
        if nxt:
            if nxt.tzinfo is None:
                nxt = nxt.replace(tzinfo=timezone.utc)
            return nxt.isoformat().replace('+00:00', 'Z')
        return None

    return None


_scheduler_stop = threading.Event()


def _scheduler_loop():
    """Background daemon: check schedules every 30s and dispatch due tasks."""
    while not _scheduler_stop.is_set():
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
                        try:
                            sid = _dispatch_agent_internal(pid, task)
                            print(f"[scheduler] Dispatched for {pid}: {task[:60]} -> session {sid}")
                        except Exception as e:
                            print(f"[scheduler] Failed to dispatch for {pid}: {e}")
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
            print(f"[scheduler] Error: {e}")

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
                            if (now - last_dt).total_seconds() < 300:
                                continue
                        except Exception:
                            pass
                    try:
                        _gh_sync.sync_project(proj['id'])
                    except Exception as e:
                        print(f"[scheduler] GitHub sync error for {proj['id']}: {e}")
        except Exception as e:
            print(f"[scheduler] GitHub sync loop error: {e}")

        # ── Purge stale sessions from memory ──────────────────────────────
        try:
            cutoff = now - timedelta(minutes=30)
            with agent_lock:
                stale = []
                for sid, s in agent_sessions.items():
                    if s['status'] not in ('running', 'idle'):
                        try:
                            ts = datetime.fromisoformat(s['started_at'].replace('Z', '+00:00'))
                            if ts.tzinfo is None:
                                ts = ts.replace(tzinfo=timezone.utc)
                            if ts < cutoff:
                                stale.append(sid)
                        except Exception:
                            stale.append(sid)
                for sid in stale:
                    agent_sessions.pop(sid, None)
                if stale:
                    print(f"[scheduler] Purged {len(stale)} stale agent session(s)")
            with terminal_lock:
                stale_t = []
                for sid, s in terminal_sessions.items():
                    if s['status'] != 'running':
                        stale_t.append(sid)
                for sid in stale_t:
                    terminal_sessions.pop(sid, None)
        except Exception as e:
            print(f"[scheduler] Session purge error: {e}")

        # ── Process tracker: liveness sweep ───────────────────────────────
        try:
            with process_tracker_lock:
                dead_pids = [pid for pid, entry in tracked_processes.items()
                             if entry.get('proc') and entry['proc'].poll() is not None]
                for pid in dead_pids:
                    tracked_processes.pop(pid, None)
                if dead_pids:
                    print(f"[scheduler] Cleaned {len(dead_pids)} dead process(es) from tracker")
        except Exception as e:
            print(f"[scheduler] Process tracker sweep error: {e}")

        _scheduler_stop.wait(30)


def _start_scheduler():
    t = threading.Thread(target=_scheduler_loop, daemon=True, name='scheduler')
    t.start()
    return t


@app.route('/api/schedules')
def get_schedules():
    schedules = _load_schedules()
    # Enrich with project names
    projects_map = {p['id']: p.get('name', p['id']) for p in load_projects()}
    for s in schedules:
        s['project_name'] = projects_map.get(s.get('project_id', ''), s.get('project_id', ''))
    return jsonify(schedules)


@app.route('/api/schedules', methods=['POST'])
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


@app.route('/api/schedules/<schedule_id>', methods=['PUT'])
def update_schedule(schedule_id):
    data = request.get_json() or {}
    schedules = _load_schedules()
    sched = next((s for s in schedules if s['id'] == schedule_id), None)
    if not sched:
        return jsonify({'error': 'not found'}), 404

    for key in ('project_id', 'task', 'schedule_type', 'time', 'days',
                'interval_minutes', 'enabled', 'run_at', 'cron_expr'):
        if key in data:
            sched[key] = data[key]

    # Recompute next_run
    sched['next_run'] = _compute_next_run(sched)
    _save_schedules(schedules)
    return jsonify(sched)


@app.route('/api/schedules/<schedule_id>', methods=['DELETE'])
def delete_schedule(schedule_id):
    schedules = _load_schedules()
    before = len(schedules)
    schedules = [s for s in schedules if s['id'] != schedule_id]
    if len(schedules) == before:
        return jsonify({'error': 'not found'}), 404
    _save_schedules(schedules)
    return jsonify({'ok': True})


# ── Static ───────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory(STATIC_DIR, 'index.html')


import atexit

def _cleanup_persistent_agents():
    """Clean up any Mode B persistent processes on server shutdown."""
    for sid, session in list(agent_sessions.items()):
        if session.get('mode') == 'B' and session.get('process_alive'):
            try:
                session['proc'].stdin.close()
            except Exception:
                pass
            try:
                session['proc'].kill()
            except Exception:
                pass
            _unregister_process(session['proc'].pid)

def _cleanup_terminals():
    for sid, session in list(terminal_sessions.items()):
        if session['status'] == 'running':
            _kill_terminal_session(session)

atexit.register(_cleanup_persistent_agents)
atexit.register(_cleanup_terminals)
atexit.register(_scheduler_stop.set)


if __name__ == '__main__':
    _start_scheduler()
    print(f"Mission Control running at http://localhost:{PORT}")
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
