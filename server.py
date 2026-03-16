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
from datetime import datetime, timezone
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

MEMORY_DIR = _DATA_ROOT / 'data' / 'memory'
MEMORY_DIR.mkdir(parents=True, exist_ok=True)

SKILLS_DIR = _DATA_ROOT / 'data' / 'skills'
SKILLS_GLOBAL_DIR = SKILLS_DIR / 'global'
SKILLS_PROJECT_DIR = SKILLS_DIR / 'project'
SKILLS_ATTACH_PATH = SKILLS_DIR / 'attachments.json'
SKILLS_GLOBAL_DIR.mkdir(parents=True, exist_ok=True)
SKILLS_PROJECT_DIR.mkdir(parents=True, exist_ok=True)

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


def _build_claude_flags(project=None):
    """Build common Claude CLI flags from config, with optional per-project overrides."""
    flags = ['--print', '--verbose', '--output-format', 'stream-json',
             '--dangerously-skip-permissions']
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
    return flags


# ── Agent session tracking ───────────────────────────────────────────────────
# session_id → {proc, status, task, log_lines, started_at, session_id, project_id}
agent_sessions = {}
agent_lock = threading.Lock()  # single global lock for session creation


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
            agent_sessions.pop(sid, None)

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


# ── Skills helpers ───────────────────────────────────────────────────────────

def _load_skills_attachments():
    if SKILLS_ATTACH_PATH.exists():
        try:
            return json.loads(SKILLS_ATTACH_PATH.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {}

def _save_skills_attachments(data):
    SKILLS_ATTACH_PATH.parent.mkdir(parents=True, exist_ok=True)
    SKILLS_ATTACH_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')

def _load_global_skills():
    skills = []
    for f in sorted(SKILLS_GLOBAL_DIR.glob('*.json')):
        try:
            skills.append(json.loads(f.read_text(encoding='utf-8')))
        except Exception:
            pass
    return skills

def _load_project_skills(project_id):
    d = SKILLS_PROJECT_DIR / project_id
    if not d.exists():
        return []
    skills = []
    for f in sorted(d.glob('*.json')):
        try:
            skills.append(json.loads(f.read_text(encoding='utf-8')))
        except Exception:
            pass
    return skills

def _resolve_skills_for_project(project_id):
    """Get all skills that should be injected for a project dispatch."""
    project_skills = _load_project_skills(project_id)
    global_skills = _load_global_skills()
    attachments = _load_skills_attachments()
    attached_ids = set(attachments.get(project_id, []))
    result = list(project_skills)
    for gs in global_skills:
        if gs['id'] in attached_ids:
            result.append(gs)
    return result


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

    # Project memory
    mem_path = MEMORY_DIR / f'{project["id"]}.md'
    has_memory = False
    if mem_path.exists():
        mem = mem_path.read_text(encoding='utf-8').strip()
        if mem:
            has_memory = True
            parts.append(f"--- PROJECT MEMORY ---\n{mem}")

    # Skills
    skills = _resolve_skills_for_project(project['id'])
    for skill in skills:
        parts.append(f"--- SKILL: {skill['name']} ---\n{skill['content']}")

    # System awareness
    pid = project['id']
    port = PORT
    awareness = [
        "You are managed by Mission Control, a project dashboard.",
        "The PROJECT MEMORY section above (if present) contains persistent knowledge about this project "
        "that carries across sessions — architecture decisions, gotchas, key learnings. "
        "Reference it to avoid repeating past mistakes or re-discovering known issues.",
        "",
        "You can READ and WRITE project memory via the Mission Control API:",
        f"  - Read:   curl -s http://localhost:{port}/api/project/{pid}/memory",
        f"  - Append: curl -s -X POST http://localhost:{port}/api/project/{pid}/memory/append "
        f"-H 'Content-Type: application/json' -d '{{\"content\": \"your markdown content here\"}}'",
        f"  - Replace all: curl -s -X PUT http://localhost:{port}/api/project/{pid}/memory "
        f"-H 'Content-Type: application/json' -d '{{\"content\": \"full markdown content\"}}'",
        "",
        "When you discover important information during a session (architecture decisions, tricky bugs, "
        "environment gotchas, key patterns, things that worked or failed), proactively append them to "
        "project memory using the append endpoint. Keep entries concise with markdown formatting. "
        "Session completions are also auto-logged to memory.",
    ]
    if skills:
        skill_names = ', '.join(s['name'] for s in skills)
        awareness.append(f"\nActive skills for this session: {skill_names}. Follow their instructions.")
    parts.append("--- SYSTEM ---\n" + "\n".join(awareness))

    # Recent activity
    log = project.get('activity_log', [])[:5]
    if log:
        lines = [f"  - {e.get('ts','')}: {e.get('msg','')}" for e in log]
        parts.append("Recent activity:\n" + "\n".join(lines))

    # Recent agent sessions (for continuity if prior conversation hung)
    agent_log = _load_agent_log(project['id'])[:5]
    if agent_log:
        sess_lines = []
        for e in agent_log:
            csid = e.get('claude_session_id', '')
            sid_part = f" | claude -r {csid}" if csid else ''
            sess_lines.append(f"  - [{e.get('status','')}] {e.get('task','')[:80]}{sid_part}")
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
                            elif tool_name == 'ExitPlanMode' and session.get('_last_md_file'):
                                session['plan_file'] = session['_last_md_file']
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
    }
    log = _load_agent_log(project_id)
    log.insert(0, entry)
    _save_agent_log(project_id, log)

    # Auto-append session summary to project memory
    if session.get('status') == 'completed' and summary:
        try:
            task = session.get('task', '').strip()
            ts = entry['ts'][:10]  # date only
            brief = summary[:300].replace('\n', ' ').strip()
            mem_entry = f"- [{ts}] **{task[:80]}** — {brief}"
            mem_path = MEMORY_DIR / f'{project_id}.md'
            existing = ''
            if mem_path.exists():
                existing = mem_path.read_text(encoding='utf-8').rstrip()
            header = '## Session Log'
            if header not in existing:
                existing = existing + f'\n\n{header}' if existing else header
            mem_path.write_text(existing + '\n' + mem_entry + '\n', encoding='utf-8')
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
    session['proc'] = proc
    session['status'] = 'running'
    user_label = CONFIG.get('user_name') or 'User'
    session['log_lines'].append(f"> {user_label}: {message}")

    t = threading.Thread(target=_read_agent_stream, args=(proc, session), daemon=True)
    t.start()


@app.route('/api/project/<project_id>/agent/dispatch', methods=['POST'])
def agent_dispatch(project_id):
    p = load_project(project_id)
    if not p:
        return jsonify({'error': 'project not found'}), 404

    pp = p.get('project_path', '')
    if not pp or not Path(pp).is_dir():
        return jsonify({'error': 'project_path not set or invalid'}), 400

    data = request.get_json() or {}
    task = data.get('task', '').strip()
    if not task:
        return jsonify({'error': 'task required'}), 400

    resume_id = data.get('resume_conversation_id', '').strip()

    with agent_lock:
        session_id = uuid.uuid4().hex[:12]

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

        session = {
            'proc': proc,
            'status': 'running',
            'task': task,
            'log_lines': [],
            'started_at': now_iso(),
            'session_id': session_id,
            'project_id': project_id,
        }
        agent_sessions[session_id] = session

        t = threading.Thread(target=_read_agent_stream, args=(proc, session), daemon=True)
        t.start()

    resume_label = f" (resuming {resume_id})" if resume_id else ""
    print(f"[dispatch] cmd: {' '.join(cmd)}")
    _log_agent_activity(project_id, f"Agent dispatched{resume_label}: {task[:100]}")
    return jsonify({'ok': True, 'session_id': session_id})


@app.route('/api/project/<project_id>/agent/stream')
def agent_stream(project_id):
    """SSE endpoint streaming agent output for a specific session."""
    session_id = request.args.get('session', '')

    def generate():
        session = agent_sessions.get(session_id)
        if not session or session['project_id'] != project_id:
            yield f"data: {json.dumps({'type': 'error', 'msg': 'no active session'})}\n\n"
            return

        sent = 0
        tick = 0
        while True:
            lines = session['log_lines']
            if sent < len(lines):
                for line in lines[sent:]:
                    yield f"data: {json.dumps({'type': 'output', 'text': line})}\n\n"
                sent = len(lines)

            if session['status'] != 'running':
                # Don't close if follow-ups are pending or being dispatched
                if not session.get('pending_followups') and not session.get('_dispatching_followup'):
                    yield f"data: {json.dumps({'type': 'status', 'status': session['status'], 'usage': session.get('usage', {}), 'cost_usd': session.get('cost_usd', 0), 'num_turns': session.get('num_turns', 0)})}\n\n"
                    break
                # Else: wait for follow-up auto-dispatch to restart the session

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

        # If agent is still running, queue the follow-up instead of killing
        if existing['status'] == 'running':
            pending = existing.setdefault('pending_followups', [])
            pending.append(message)
            user_label = CONFIG.get('user_name') or 'User'
            existing['log_lines'].append(f"> [queued] {user_label}: {message}")
            _log_agent_activity(project_id, f"Agent follow-up queued: {message[:100]}")
            return jsonify({'ok': True, 'queued': True, 'session_id': session_id})

        # Agent is not running — send follow-up immediately via resume
        claude_sid = existing.get('claude_session_id')
        if claude_sid:
            resume_flags = ['-r', claude_sid]
        else:
            resume_flags = ['--continue']

        cmd = ['claude', *resume_flags, '-p', message, *_build_claude_flags(p)]

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

        existing['proc'] = proc
        existing['status'] = 'running'
        user_label = CONFIG.get('user_name') or 'User'
        existing['log_lines'].append(f"\n> {user_label}: {message}\n")

        t = threading.Thread(target=_read_agent_stream, args=(proc, existing), daemon=True)
        t.start()

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
        if session['status'] != 'running':
            return jsonify({'error': 'agent not running'}), 400
        proc = session['proc']
        try:
            proc.kill()
        except Exception:
            pass
        session['status'] = 'stopped'
        session['log_lines'].append('[Agent stopped by user]')

    # Wait outside lock so reader thread can update session
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
        if session['status'] == 'running':
            proc = session['proc']
            try:
                proc.kill()
            except Exception:
                pass
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
                'log_lines': s['log_lines'],
                'started_at': s['started_at'],
                'plan_file': s.get('plan_file', ''),
                'usage': s.get('usage', {}),
                'cost_usd': s.get('cost_usd', 0),
                'num_turns': s.get('num_turns', 0),
            })
    # Sort: running first, then newest first (ISO timestamps sort lexically)
    sessions.sort(key=lambda s: (
        0 if s['status'] == 'running' else 1,
        '~' if not s.get('started_at') else s['started_at']
    ), reverse=False)
    # Within each group, newest first
    sessions.sort(key=lambda s: s.get('started_at', ''), reverse=True)
    sessions.sort(key=lambda s: 0 if s['status'] == 'running' else 1)
    return jsonify({'sessions': sessions})


# ── Agent log endpoint ────────────────────────────────────────────────────────

@app.route('/api/project/<project_id>/agent/log')
def get_agent_log(project_id):
    log = _load_agent_log(project_id)
    for entry in log:
        entry['ts_relative'] = time_ago(entry.get('ts'))
        entry['started_relative'] = time_ago(entry.get('started_at'))
    return jsonify(log)


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
    mem_path = MEMORY_DIR / f'{project_id}.md'
    content = ''
    if mem_path.exists():
        content = mem_path.read_text(encoding='utf-8')
    return jsonify({'content': content})

@app.route('/api/project/<project_id>/memory', methods=['PUT'])
def save_memory(project_id):
    p = load_project(project_id)
    if not p:
        return jsonify({'error': 'not found'}), 404
    data = request.get_json() or {}
    content = data.get('content')
    if content is None:
        return jsonify({'error': 'content required'}), 400
    mem_path = MEMORY_DIR / f'{project_id}.md'
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
    mem_path = MEMORY_DIR / f'{project_id}.md'
    existing = ''
    if mem_path.exists():
        existing = mem_path.read_text(encoding='utf-8').rstrip()
    if existing:
        combined = existing + '\n\n' + content
    else:
        combined = content
    mem_path.write_text(combined, encoding='utf-8')
    return jsonify({'ok': True})


# ── Skills endpoints ───────────────────────────────────────────────────────

@app.route('/api/skills/global')
def get_global_skills():
    return jsonify(_load_global_skills())

@app.route('/api/skills/global', methods=['POST'])
def create_global_skill():
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name required'}), 400
    skill_id = name.lower().replace(' ', '_')
    skill_id = ''.join(c for c in skill_id if c.isalnum() or c == '_')
    if not skill_id:
        skill_id = uuid.uuid4().hex[:8]
    # Ensure unique
    existing = SKILLS_GLOBAL_DIR / f'{skill_id}.json'
    if existing.exists():
        skill_id = f'{skill_id}_{uuid.uuid4().hex[:4]}'
    now = datetime.now(timezone.utc).isoformat()
    skill = {
        'id': skill_id,
        'name': name,
        'description': (data.get('description') or '').strip(),
        'content': (data.get('content') or '').strip(),
        'created_at': now,
        'updated_at': now,
    }
    (SKILLS_GLOBAL_DIR / f'{skill_id}.json').write_text(
        json.dumps(skill, indent=2, ensure_ascii=False), encoding='utf-8')
    return jsonify(skill), 201

@app.route('/api/skills/global/<skill_id>', methods=['PUT'])
def update_global_skill(skill_id):
    path = SKILLS_GLOBAL_DIR / f'{skill_id}.json'
    if not path.exists():
        return jsonify({'error': 'not found'}), 404
    skill = json.loads(path.read_text(encoding='utf-8'))
    data = request.get_json() or {}
    if 'name' in data:
        skill['name'] = data['name'].strip()
    if 'description' in data:
        skill['description'] = data['description'].strip()
    if 'content' in data:
        skill['content'] = data['content'].strip()
    skill['updated_at'] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(skill, indent=2, ensure_ascii=False), encoding='utf-8')
    return jsonify(skill)

@app.route('/api/skills/global/<skill_id>', methods=['DELETE'])
def delete_global_skill(skill_id):
    path = SKILLS_GLOBAL_DIR / f'{skill_id}.json'
    if not path.exists():
        return jsonify({'error': 'not found'}), 404
    path.unlink()
    # Remove from all project attachments
    att = _load_skills_attachments()
    changed = False
    for pid in list(att.keys()):
        if skill_id in att[pid]:
            att[pid].remove(skill_id)
            changed = True
    if changed:
        _save_skills_attachments(att)
    return jsonify({'ok': True})

@app.route('/api/project/<project_id>/skills')
def get_project_skills(project_id):
    p = load_project(project_id)
    if not p:
        return jsonify({'error': 'not found'}), 404
    project_skills = _load_project_skills(project_id)
    global_skills = _load_global_skills()
    att = _load_skills_attachments()
    attached_ids = set(att.get(project_id, []))
    attached = [gs for gs in global_skills if gs['id'] in attached_ids]
    available = [gs for gs in global_skills if gs['id'] not in attached_ids]
    return jsonify({
        'project': project_skills,
        'attached': attached,
        'available': available,
    })

@app.route('/api/project/<project_id>/skills', methods=['POST'])
def create_project_skill(project_id):
    p = load_project(project_id)
    if not p:
        return jsonify({'error': 'not found'}), 404
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'error': 'name required'}), 400
    skill_id = name.lower().replace(' ', '_')
    skill_id = ''.join(c for c in skill_id if c.isalnum() or c == '_')
    if not skill_id:
        skill_id = uuid.uuid4().hex[:8]
    skill_dir = SKILLS_PROJECT_DIR / project_id
    skill_dir.mkdir(parents=True, exist_ok=True)
    existing = skill_dir / f'{skill_id}.json'
    if existing.exists():
        skill_id = f'{skill_id}_{uuid.uuid4().hex[:4]}'
    now = datetime.now(timezone.utc).isoformat()
    skill = {
        'id': skill_id,
        'name': name,
        'description': (data.get('description') or '').strip(),
        'content': (data.get('content') or '').strip(),
        'created_at': now,
        'updated_at': now,
    }
    (skill_dir / f'{skill_id}.json').write_text(
        json.dumps(skill, indent=2, ensure_ascii=False), encoding='utf-8')
    return jsonify(skill), 201

@app.route('/api/project/<project_id>/skills/<skill_id>', methods=['PUT'])
def update_project_skill(project_id, skill_id):
    path = SKILLS_PROJECT_DIR / project_id / f'{skill_id}.json'
    if not path.exists():
        return jsonify({'error': 'not found'}), 404
    skill = json.loads(path.read_text(encoding='utf-8'))
    data = request.get_json() or {}
    if 'name' in data:
        skill['name'] = data['name'].strip()
    if 'description' in data:
        skill['description'] = data['description'].strip()
    if 'content' in data:
        skill['content'] = data['content'].strip()
    skill['updated_at'] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(skill, indent=2, ensure_ascii=False), encoding='utf-8')
    return jsonify(skill)

@app.route('/api/project/<project_id>/skills/<skill_id>', methods=['DELETE'])
def delete_project_skill(project_id, skill_id):
    path = SKILLS_PROJECT_DIR / project_id / f'{skill_id}.json'
    if not path.exists():
        return jsonify({'error': 'not found'}), 404
    path.unlink()
    return jsonify({'ok': True})

@app.route('/api/project/<project_id>/skills/attach', methods=['POST'])
def attach_skill(project_id):
    p = load_project(project_id)
    if not p:
        return jsonify({'error': 'not found'}), 404
    data = request.get_json() or {}
    skill_id = (data.get('skill_id') or '').strip()
    if not skill_id:
        return jsonify({'error': 'skill_id required'}), 400
    # Verify global skill exists
    if not (SKILLS_GLOBAL_DIR / f'{skill_id}.json').exists():
        return jsonify({'error': 'skill not found'}), 404
    att = _load_skills_attachments()
    project_att = att.setdefault(project_id, [])
    if skill_id not in project_att:
        project_att.append(skill_id)
        _save_skills_attachments(att)
    return jsonify({'ok': True})

@app.route('/api/project/<project_id>/skills/detach', methods=['POST'])
def detach_skill(project_id):
    data = request.get_json() or {}
    skill_id = (data.get('skill_id') or '').strip()
    if not skill_id:
        return jsonify({'error': 'skill_id required'}), 400
    att = _load_skills_attachments()
    project_att = att.get(project_id, [])
    if skill_id in project_att:
        project_att.remove(skill_id)
        att[project_id] = project_att
        _save_skills_attachments(att)
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
    for i, project_id in enumerate(order):
        p = load_project(project_id)
        if p:
            p['display_order'] = i
            save_project(project_id, p)
    return jsonify({'ok': True})


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


# ── Static ───────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return send_from_directory(STATIC_DIR, 'index.html')


if __name__ == '__main__':
    print(f"Mission Control running at http://localhost:{PORT}")
    app.run(host='0.0.0.0', port=PORT, debug=False)
