#!/usr/bin/env python3
import hashlib
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
from flask import Flask, jsonify, send_from_directory, request, send_file, abort, Response, redirect
import secrets


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
_POPEN_FLAGS = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if sys.platform == 'win32' else 0
_STARTUPINFO = None
if sys.platform == 'win32':
    _STARTUPINFO = subprocess.STARTUPINFO()
    _STARTUPINFO.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    _STARTUPINFO.wShowWindow = 0  # SW_HIDE


def _pid_is_alive(pid):
    """Check if a PID is alive. Works reliably on both Windows and Unix."""
    if sys.platform == 'win32':
        import ctypes
        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    else:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def _kill_pid(pid, tree=False):
    """Kill a process by PID. Works reliably on both Windows and Unix.
    If tree=True, also kills all child processes (Windows: taskkill /T)."""
    if sys.platform == 'win32':
        try:
            cmd = ['taskkill', '/F']
            if tree:
                cmd.append('/T')
            cmd.extend(['/PID', str(pid)])
            subprocess.run(cmd, capture_output=True, timeout=10,
                           creationflags=_POPEN_FLAGS)
            return True
        except Exception:
            return False
    else:
        if tree:
            # Kill process group if possible
            try:
                os.killpg(os.getpgid(pid), 9)
                return True
            except OSError:
                pass
        try:
            os.kill(pid, 9)
            return True
        except OSError:
            return False


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

# ── Remote-access provider discovery ────────────────────────────────────────
# Open-source contract (`mc_remote_iface`) is always imported. The proprietary
# provider (`mc_remote`) auto-registers at import time IF installed alongside.
# This lets MC core run cleanly with or without remote-access bundled.
# See `docs/remote-access/07-licensing.md` §4.
try:
    import mc_remote_iface  # noqa: F401  (import for side-effect: surface available)
except Exception as _e:
    mc_remote_iface = None  # type: ignore[assignment]
    print(f"[remote-access] mc_remote_iface not available: {_e}", flush=True)

if mc_remote_iface is not None:
    # Dev stub takes precedence when its env var is set — useful for UI work
    # without standing up the full proprietary provider. Real builds for end
    # users never have this set.
    _dev_stub_active = bool(os.environ.get("MC_DEV_REMOTE_STUB"))
    if _dev_stub_active:
        try:
            from mc_remote_iface.dev_stub import maybe_register_dev_stub
            if maybe_register_dev_stub():
                print(f"[remote-access] dev stub registered "
                      f"(MC_DEV_REMOTE_STUB={os.environ.get('MC_DEV_REMOTE_STUB')})", flush=True)
        except Exception as _e:
            print(f"[remote-access] dev stub unavailable: {_e}", flush=True)
    else:
        try:
            import mc_remote  # noqa: F401  (provider self-registers via __init__)
        except Exception as _e:
            # Absence is normal in an open-source build with no proprietary
            # provider installed. Log at info volume only.
            print(f"[remote-access] no provider installed: {_e}", flush=True)

# ── Configuration ────────────────────────────────────────────────────────────

CONFIG_PATH = _DATA_ROOT / 'config.json'

def _load_config():
    """Load config.json, creating with defaults if it doesn't exist."""
    defaults = {
        'port': 5199,
        'shared_rules_path': str(_DATA_ROOT / 'data' / 'SHARED_RULES.md'),
        'projects_base': str(Path.home()),
        'auto_workspace_base': str(Path.home() / 'MissionControl'),
        'agent_model': '',
        'agent_max_turns': 0,
        'agent_permission_mode': '',
        'desktop_mode': False,
        'user_name': '',
        'agent_name': '',
        'use_streaming_agent': False,
        'condense_threshold_kb': 30,
        'condense_model': '',
        'condense_enabled': True,
        'agent_channels': '',
        'agent_remote_control': False,
        'agent_revive_from_log': True,
        'agent_log_backfill_enabled': True,
        'agent_log_backfill_max_per_project': 200,
        'agent_log_backfill_max_age_days': 60,
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

@app.after_request
def add_cors_headers(response):
    # Localhost-only dev app: echo back whatever Origin the caller sends so
    # the Tauri webview (which may use http://tauri.localhost, tauri://localhost,
    # https://tauri.localhost, or other custom schemes depending on platform)
    # can always reach the API. Not a security risk — server binds localhost.
    origin = request.headers.get('Origin', '*')
    response.headers['Access-Control-Allow-Origin'] = origin
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    response.headers['Vary'] = 'Origin'
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

# Global incognito pseudo-project. Lives at data/projects/_incognito.json with
# `_is_incognito_project: True`. All sessions dispatched into it are forced
# incognito. Auto-created on first use.
INCOGNITO_PROJECT_ID = '_incognito'


def _ensure_incognito_project():
    """Lazily create the global incognito project record + workspace folder.

    Returns the project dict (loaded fresh from disk on each call so callers
    see any updates the user has made, e.g. renamed it).
    """
    fp = DATA_DIR / f'{INCOGNITO_PROJECT_ID}.json'
    if fp.exists():
        try:
            return json.loads(fp.read_text(encoding='utf-8'))
        except Exception:
            pass
    base = Path(CONFIG.get('auto_workspace_base') or str(Path.home() / 'MissionControl'))
    workspace = base / '_incognito'
    try:
        workspace.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    rec = {
        'id': INCOGNITO_PROJECT_ID,
        'name': 'Incognito',
        'emoji': '\U0001F576️',  # detective/sunglasses face
        'description': 'Ephemeral scratch space. Sessions here skip MEMORY.md, '
                       'AGENT_RULES.md, and the agent log. Useful for one-off '
                       'questions you do not want polluting a project.',
        'project_path': str(workspace),
        'status': 'active',
        'domain': 'general',
        'activity_log': [],
        'backlog': [],
        'current_task': '',
        'next_action': '',
        '_is_incognito_project': True,
        'last_updated': now_iso() if 'now_iso' in globals() else datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
    }
    try:
        fp.write_text(json.dumps(rec, indent=2, ensure_ascii=False), encoding='utf-8')
    except Exception:
        pass
    return rec


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


def _extract_user_text(msg_field):
    """Extract plain user text from a jsonl message field, skipping tool_result blocks."""
    if not isinstance(msg_field, dict) or msg_field.get('role') != 'user':
        return ''
    content = msg_field.get('content', '')
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        texts = []
        for block in content:
            if isinstance(block, dict) and block.get('type') == 'text':
                texts.append(str(block.get('text', '')))
        return ' '.join(t.strip() for t in texts if t).strip()
    return ''


def _recent_claude_transcripts(project_path, limit=5):
    """Scan ~/.claude/projects/<encoded>/*.jsonl for a project.

    Returns [{session_id, mtime, first_user, last_user, turns, size}] sorted by mtime desc.
    Covers both `_`→`-` encodings, dedups by filename.
    """
    if not project_path:
        return []
    try:
        resolved = str(Path(project_path).resolve())
    except Exception:
        return []
    encoded = resolved.replace(':', '-').replace('\\', '-').replace('/', '-')
    candidates = [CLAUDE_HOME / encoded]
    encoded_alt = encoded.replace('_', '-')
    if encoded_alt != encoded:
        candidates.append(CLAUDE_HOME / encoded_alt)

    seen = set()
    files = []
    for d in candidates:
        if not d.exists():
            continue
        try:
            for f in d.glob('*.jsonl'):
                if f.name in seen:
                    continue
                seen.add(f.name)
                try:
                    files.append((f, f.stat().st_mtime))
                except OSError:
                    continue
        except OSError:
            continue
    files.sort(key=lambda x: x[1], reverse=True)
    files = files[:limit]

    results = []
    for f, mtime in files:
        first_user = ''
        last_user = ''
        turns = 0
        try:
            with open(f, 'r', encoding='utf-8', errors='replace') as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    if obj.get('type') != 'user':
                        continue
                    text = _extract_user_text(obj.get('message', {}))
                    if not text:
                        continue
                    turns += 1
                    if not first_user:
                        first_user = text
                    last_user = text
        except Exception:
            pass
        try:
            size = f.stat().st_size
        except OSError:
            size = 0
        results.append({
            'session_id': f.stem,
            'mtime': mtime,
            'first_user': first_user[:300],
            'last_user': last_user[:300],
            'turns': turns,
            'size': size,
        })
    return results


def _find_transcript_file(project_path, claude_session_id):
    """Locate the Claude Code transcript JSONL for a given csid, or None."""
    if not project_path or not claude_session_id:
        return None
    try:
        resolved = str(Path(project_path).resolve())
    except Exception:
        return None
    encoded = resolved.replace(':', '-').replace('\\', '-').replace('/', '-')
    candidates = [CLAUDE_HOME / encoded]
    encoded_alt = encoded.replace('_', '-')
    if encoded_alt != encoded:
        candidates.append(CLAUDE_HOME / encoded_alt)
    for d in candidates:
        f = d / f'{claude_session_id}.jsonl'
        if f.exists():
            return f
    return None


def _parse_transcript_messages(f, max_messages=300):
    """Parse a Claude Code JSONL transcript into [{role, text, tool, timestamp}] for read-only display.

    role: 'user' | 'assistant' | 'tool_call'
    Returns at most max_messages entries; longer transcripts are truncated.
    """
    messages = []
    try:
        with open(f, 'r', encoding='utf-8', errors='replace') as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                t = obj.get('type', '')
                ts = obj.get('timestamp', '')
                if t == 'user':
                    text = _extract_user_text(obj.get('message', {}))
                    if text:
                        messages.append({'role': 'user', 'text': text[:5000], 'timestamp': ts})
                elif t == 'assistant':
                    content = obj.get('message', {}).get('content', [])
                    if isinstance(content, list):
                        for block in content:
                            if not isinstance(block, dict):
                                continue
                            btype = block.get('type', '')
                            if btype == 'text':
                                txt = str(block.get('text', '')).strip()
                                if txt:
                                    messages.append({'role': 'assistant', 'text': txt[:5000], 'timestamp': ts})
                            elif btype == 'tool_use':
                                messages.append({'role': 'tool_call',
                                                 'tool': block.get('name', ''),
                                                 'timestamp': ts})
                if len(messages) >= max_messages:
                    break
    except Exception as e:
        return [{'role': 'error', 'text': f'Failed to parse transcript: {e}'}]
    return messages


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
    # Claude Code may also replace underscores with dashes — check both
    # and prefer whichever was modified most recently
    encoded_alt = encoded.replace('_', '-')
    if encoded_alt != encoded:
        alt_path = CLAUDE_HOME / encoded_alt / 'memory' / 'MEMORY.md'
        if alt_path.exists() and mem_path.exists():
            if alt_path.stat().st_mtime > mem_path.stat().st_mtime:
                return alt_path
        elif alt_path.exists():
            return alt_path
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


# ── Per-project agent isolation ──────────────────────────────────────────────
# Each project gets its own ProjectAgentManager with its own lock and guardian.
# A hung kill or slow operation in one project cannot block any other project,
# because no lock is ever shared across project_ids.
class ProjectAgentManager:
    def __init__(self, project_id):
        self.project_id = project_id
        self.lock = threading.RLock()
        self.session_ids = set()  # session_ids belonging to this project
        self._guardian_thread = None
        self._guardian_stop = threading.Event()

    def add_session(self, session_id):
        with self.lock:
            self.session_ids.add(session_id)

    def remove_session(self, session_id):
        with self.lock:
            self.session_ids.discard(session_id)

    def iter_sessions(self):
        """Snapshot of (sid, session) tuples for this project. Briefly takes self.lock."""
        with self.lock:
            ids = list(self.session_ids)
        out = []
        for sid in ids:
            s = agent_sessions.get(sid)
            if s is not None:
                out.append((sid, s))
        return out

    def ensure_guardian(self):
        """Lazy-start this project's guardian thread on first use."""
        with self.lock:
            if self._guardian_thread is not None and self._guardian_thread.is_alive():
                return
            t = threading.Thread(
                target=_project_guardian_loop,
                args=(self,),
                daemon=True,
                name=f'guardian-{self.project_id[:12]}',
            )
            self._guardian_thread = t
            t.start()

    def shutdown(self):
        self._guardian_stop.set()


_managers = {}                       # project_id -> ProjectAgentManager
_managers_lock = threading.Lock()    # ONLY for _managers dict mutation; never held during work


def get_manager(project_id):
    """Get or create the ProjectAgentManager for a project. Cheap to call."""
    with _managers_lock:
        m = _managers.get(project_id)
        if m is None:
            m = ProjectAgentManager(project_id)
            _managers[project_id] = m
    return m


def get_manager_for_session(session_id):
    """Find the manager that owns a given session. Returns None if not tracked."""
    s = agent_sessions.get(session_id)
    if not s:
        return None
    pid = s.get('project_id')
    if not pid:
        return None
    return get_manager(pid)


def all_managers():
    """Snapshot of all current managers. The dict lock is held only for the copy."""
    with _managers_lock:
        return list(_managers.values())


def _project_guardian_loop(manager):
    """Per-project guardian loop. One thread per ProjectAgentManager.

    Iterates only this project's sessions. A hung kill or slow check in this
    project cannot affect any other project — there is no shared lock.
    """
    while not manager._guardian_stop.is_set() and not _guardian_stop.is_set():
        if manager._guardian_stop.wait(GUARDIAN_CHECK_INTERVAL):
            break
        if _guardian_stop.is_set():
            break
        now = _time.time()
        # Snapshot under this project's lock only — never global.
        snapshots = []
        with manager.lock:
            for sid in list(manager.session_ids):
                session = agent_sessions.get(sid)
                if session is None:
                    continue
                if session['status'] in ('completed', 'stopped'):
                    continue
                if session.get('housekeeping'):
                    continue
                snapshots.append((sid, session))
        for sid, session in snapshots:
            try:
                _guardian_check_session(sid, session, now)
            except Exception as e:
                print(f"[guardian:{manager.project_id[:8]}] Error checking {sid[:8]}: {e}")

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


def _should_condense(project, include_claude_md=False):
    """Check whether memory condensation should be triggered for this project.

    If include_claude_md is True, also count the project's CLAUDE.md in the size check.
    This is used by the pre-dispatch context budget check.
    """
    if not CONFIG.get('condense_enabled', True):
        return False
    pid = project['id']
    with _condense_lock:
        if pid in _condensing_projects:
            return False
    # Skip running-agent check when called from pre-dispatch (agent hasn't started yet)
    if not include_claude_md and _has_running_agent(pid):
        return False
    mem_path = _get_memory_path(project)
    archive_path = _get_archive_path(project)
    combined = 0
    if mem_path.exists():
        combined += mem_path.stat().st_size
    if archive_path.exists():
        combined += archive_path.stat().st_size
    if include_claude_md:
        pp = project.get('project_path', '')
        if pp:
            claude_md = Path(pp) / 'CLAUDE.md'
            if claude_md.exists():
                try:
                    combined += claude_md.stat().st_size
                except OSError:
                    pass
    threshold = CONFIG.get('condense_threshold_kb', 30) * 1024
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


# ── Asset serving (mascot icon, etc.) ────────────────────────────────────────

@app.route('/assets/<path:filename>')
def serve_asset(filename):
    """Serve files from the repo's assets/ dir (mascot icon, etc.)."""
    assets_dir = Path(__file__).parent / 'assets'
    return send_from_directory(str(assets_dir), filename)


# ── "Ask Playdo" guide assistant ────────────────────────────────────────────

@app.route('/api/guide/ask', methods=['POST'])
def guide_ask():
    """Single-shot ask of the in-app Playdo guide assistant.

    Spawns a claude session with `docs/USER_GUIDE.md` as system prompt, runs
    the user's question (optionally with prior-turn context), returns the
    answer. No project context, no memory writes, no agent_log entry. Each
    call is fully independent — `history` is just prepended to the prompt.

    Request body: {question: str, history?: [{role: 'user'|'assistant', text: str}]}.
    The answer may contain inline `[clayrune:...]` markers — the frontend
    parses + strips them and triggers UI actions (highlight, goto, open-modal).
    """
    data = request.get_json() or {}
    question = (data.get('question') or '').strip()
    if not question:
        return jsonify({'error': 'question required'}), 400
    # Cap length to avoid a runaway prompt eating tokens.
    if len(question) > 2000:
        return jsonify({'error': 'question too long (max 2000 chars)'}), 400

    # Validate + cap conversation history (last 6 messages = ~3 exchanges).
    history = data.get('history', [])
    if not isinstance(history, list):
        history = []
    history = history[-6:]

    guide_path = Path(__file__).parent / 'docs' / 'USER_GUIDE.md'
    if not guide_path.exists():
        return jsonify({'error': 'guide not available — docs/USER_GUIDE.md missing'}), 500
    try:
        system_prompt = guide_path.read_text(encoding='utf-8')
    except Exception as e:
        return jsonify({'error': f'guide read failed: {e}'}), 500

    # Build the user prompt: prior turns (if any) + current question.
    if history:
        lines = ['Previous exchange in this conversation:']
        for m in history:
            role = 'User' if (m.get('role') or '') == 'user' else 'You'
            text = (m.get('text') or '').strip()[:1000]
            if text:
                lines.append(f'{role}: {text}')
        lines.append('')
        lines.append(f'Current question: {question}')
        full_question = '\n'.join(lines)
    else:
        full_question = question
    # Hard cap on the assembled prompt to keep us tame.
    if len(full_question) > 8000:
        full_question = full_question[-8000:]

    cmd = ['claude', '-p', full_question,
           '--append-system-prompt', system_prompt,
           '--max-turns', '1']
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=60, encoding='utf-8', errors='replace',
            creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
        )
    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Playdo timed out (>60s)'}), 504
    except FileNotFoundError:
        return jsonify({'error': 'Claude CLI not found on this server'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

    if result.returncode != 0:
        err = (result.stderr or 'claude failed').strip()[:500]
        return jsonify({'error': err}), 500
    return jsonify({'answer': result.stdout.strip()})


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
    is_new = not filepath.exists()
    existing = json.loads(filepath.read_text(encoding='utf-8')) if not is_new else {'id': project_id}
    existing.setdefault('backlog', [])

    # ── Auto-create a dedicated workspace folder when creating a project with no path.
    if is_new:
        provided_path = (data.get('project_path') or '').strip()
        if not provided_path:
            base = Path(CONFIG.get('auto_workspace_base') or str(Path.home() / 'MissionControl'))
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


@app.route('/api/project/<project_id>/generate_summary', methods=['POST'])
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

    model = CONFIG.get('condense_model', '') or 'haiku'
    cmd = ['claude', '-p', prompt, '--model', model, '--output-format', 'json',
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


@app.route('/api/project/<project_id>/backlog/<item_id>/note', methods=['POST'])
def add_backlog_note(project_id, item_id):
    data = request.get_json() or {}
    text = (data.get('text') or '').strip()
    if not text:
        return jsonify({'error': 'text required'}), 400
    agent_code = (data.get('agent_code') or 'user').strip() or 'user'
    if _append_note_to_backlog_item(project_id, item_id, text, agent_code):
        return jsonify({'ok': True})
    return jsonify({'error': 'item not found'}), 404


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
        'current_task': 'Learn how to use Clayrune',
        'next_action': 'Try adding tasks to the backlog',
        'last_updated': ts,
        'backlog': [
            {'id': 'sample01', 'text': 'Explore the project tabs', 'status': 'open', 'priority': 'normal', 'created_at': ts},
            {'id': 'sample02', 'text': 'Try dispatching an AI agent', 'status': 'open', 'priority': 'high', 'created_at': ts},
            {'id': 'sample03', 'text': 'Connect a GitHub repo for issue sync', 'status': 'open', 'priority': 'low', 'created_at': ts},
        ],
        'activity_log': [
            {'ts': ts, 'msg': 'Project created during Clayrune walkthrough'}
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

def _clayrune_universal_capabilities(port: int | None = None) -> list[str]:
    """Universal Clayrune-aware behaviors that apply to EVERY agent —
    regular project agents, hivemind workers, future agent types.

    THIS IS THE CANONICAL PLACE for "things every agent should know about
    how Clayrune works". Both `_build_agent_context()` and
    `_hm_build_worker_context()` (and any future builders) splice the
    output of this function into their system prompts.

    Add a new universal capability HERE, not in the per-context builders.
    Project-specific items (backlog API with project_id, memory paths,
    workstream bus endpoints) belong in the per-context builders.

    Each entry becomes one section of the agent's appended system prompt.
    """
    if port is None:
        port = PORT
    return [
        # Plan mode hangs in headless Claude Code regardless of agent type.
        "IMPORTANT — Plan Mode: Do NOT use EnterPlanMode or ExitPlanMode. "
        "You are running headless without an interactive terminal, so plan "
        "mode approval will hang indefinitely. Just describe your plan in a "
        "text message and proceed directly with implementation.",

        # Clayrune intercepts AskUserQuestion and renders it as an interactive form.
        "Questions: When you need to ask the user, use the AskUserQuestion "
        "tool. Clayrune intercepts it and presents an interactive form; "
        "answers come back as a follow-up message.",

        # Mermaid blocks render inline in the chat panel.
        "Diagrams: Clayrune renders ```mermaid fenced blocks INLINE in your "
        "chat response — the user sees a rendered diagram (hand-drawn style, "
        "click to enlarge), NOT raw text. PREFER putting Mermaid diagrams "
        "directly in your assistant response over writing them to a separate "
        "file, unless the user explicitly asks for a file. Supported types: "
        "flowchart, sequence, state, class, ER, gantt, journey, pie. The "
        "Clayrune theme (cream nodes, orange borders, clay-brown text) is "
        "applied automatically — do not override it.",

        # Two schedulers exist — pick the right one for the job.
        f"Scheduler — TWO options, pick by lifespan:\n"
        f"  • Clayrune LOCAL scheduler — for LONG-TERM, REPEATABLE jobs scoped "
        f"to a project that must outlive any single session and re-run an agent "
        f"inside THIS Mission Control environment (daily standups, weekly "
        f"reports, recurring cleanups, one-shots scheduled hours/days out). "
        f"List: GET http://localhost:{port}/api/schedules  "
        f"Create: POST http://localhost:{port}/api/schedules with "
        f"{{\"project_id\":\"...\",\"task\":\"...\",\"schedule_type\":\"daily|weekly|interval|once|cron\","
        f"\"time\":\"09:00\",\"days\":[],\"interval_minutes\":60,\"run_at\":\"ISO8601\",\"cron_expr\":\"...\"}}  "
        f"Update: PUT /api/schedules/<id>  Delete: DELETE /api/schedules/<id>.\n"
        f"  • Anthropic /schedule skill — for SHORT-INTERVAL polling/follow-ups "
        f"that live inside the CURRENT session lifespan (e.g. \"check the build "
        f"every 5 min\", \"poll this PR until merged\"). Cloud-side; cannot reach "
        f"local Mission Control state, but perfect for in-session tick work.\n"
        f"Rule of thumb: if it should still fire after this conversation ends, "
        f"use the Clayrune local scheduler; if it's a tight loop tied to the "
        f"work you're doing right now, use /schedule.",

        # API discovery hint — when an unfamiliar Clayrune feature is needed,
        # don't guess endpoint names; list them.
        f"API discovery: When you need a Clayrune feature you haven't used "
        f"before, do NOT guess endpoint names (e.g. /api/cron, /api/jobs). "
        f"Grep server.py for `@app.route` to enumerate the real endpoints, "
        f"or curl http://localhost:{port}/ and inspect the served HTML.",
    ]


def _build_agent_context(project, incognito=False):
    """Build system prompt context for the agent.

    incognito=True keeps the full project context (rules, memory pointer,
    recent activity, recent conversations, current task) so the agent knows
    what's been done and can answer side questions. It only changes the
    output side: Mission Control will not log the session to the agent log
    and will not append a summary to project memory on completion. The
    notice block tells the agent so it doesn't write to MEMORY/rules itself.

    The global incognito pseudo-project (`_is_incognito_project`) doesn't
    have meaningful "what's been done" context anyway, so this still works
    naturally — the lack of activity/recent-conversations is just the truth.
    """
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

    if incognito:
        parts.append(
            "--- INCOGNITO MODE ---\n"
            "This is an incognito session. You can read everything about the project "
            "(rules, memory, recent activity, files) so you have full context to answer. "
            "However, Mission Control will NOT log this session to the agent log and will "
            "NOT append a summary to MEMORY.md on completion. Treat this as an off-the-record "
            "side conversation: do not modify MEMORY.md, AGENT_RULES.md, or SHARED_RULES.md "
            "and do not push commits unless the user explicitly asks. "
            "Note: Claude still writes a transcript to ~/.claude/projects/, so incognito "
            "hides this session from Mission Control surfaces, not from disk."
        )

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
        f"MANDATORY — Process Registration: Every time you spawn a background process, server, bot, "
        f"or any long-running command, you MUST register it with the Process Manager IMMEDIATELY after spawning. "
        f"This is NOT optional. Unregistered processes cannot be monitored or stopped by the user. "
        f"Steps: 1) Spawn the process. 2) Capture the PID (Bash: `cmd & echo $!` — Python: `p = subprocess.Popen(...); p.pid`). "
        f"3) Register: curl -s -X POST http://localhost:{port}/api/processes/register "
        f'-H "Content-Type: application/json" '
        f"-d '{{\"pid\":PID_NUMBER,\"name\":\"Short description\",\"project_id\":\"{pid}\","
        f"\"command\":\"the command that was run\"}}' "
        f"— PID must be an integer. Do NOT skip this step.",
        # Universal Clayrune awareness — see _clayrune_universal_capabilities().
        # Add new universal entries THERE, not here.
        *_clayrune_universal_capabilities(port=port),
        f"Backlog: This project has a Mission Control backlog (prioritized task list with notes, "
        f"attachments, and status). When the user says \"backlog\", \"backlog items\", \"the list\", "
        f"or similar, they mean THIS list — do NOT grep the filesystem. "
        f"Read it: curl -s http://localhost:{port}/api/project/{pid}/backlog "
        f"Update an item: curl -s -X PATCH http://localhost:{port}/api/project/{pid}/backlog/<item_id> "
        f'-H "Content-Type: application/json" -d \'{{"status":"done"}}\' '
        f"(status values: open, in_progress, blocked, done). "
        f"Add a note: POST /api/project/{pid}/backlog/<item_id>/note with {{\"text\":\"...\"}}.",
        f"Hivemind: You can launch multi-agent coordinated analysis on this project. "
        f"To create a hivemind, call: curl -s -X POST http://localhost:{port}/api/hivemind/create "
        f'-H "Content-Type: application/json" '
        f"-d '{{\"project_id\":\"{pid}\",\"goal\":\"GOAL_TEXT\",\"max_concurrent_workers\":3,"
        f"\"orchestrator_model\":\"sonnet\",\"worker_model\":\"sonnet\"}}' "
        f"— The orchestrator will decompose the goal into workstreams and spawn workers automatically. "
        f"Before creating, ask the user clarifying questions about scope, priorities, and constraints.",
    ])
    parts.append("--- SYSTEM ---\n" + "\n".join(awareness))

    # Recent activity
    log = project.get('activity_log', [])[:3]
    if log:
        lines = [f"  - {e.get('ts','')}: {e.get('msg','')}" for e in log]
        parts.append("Recent activity:\n" + "\n".join(lines))

    # Recent conversations — read directly from .jsonl transcripts so interrupted
    # sessions (never reached completion log) are still discoverable. Display the
    # LAST user message, not the first, since the first is usually a meta prompt
    # (context condensation, boot text) that the user won't recognize.
    project_path = project.get('project_path', '')
    convos = _recent_claude_transcripts(project_path, limit=5) if project_path else []
    if convos:
        live_by_csid = {}
        try:
            for s in agent_sessions.values():
                if s.get('project_id') != project['id']:
                    continue
                csid = s.get('claude_session_id', '')
                if csid:
                    live_by_csid[csid] = s.get('status', 'unknown')
        except Exception:
            pass
        log_by_csid = {}
        try:
            for e in _load_agent_log(project['id']):
                csid = e.get('claude_session_id', '')
                if csid and csid not in log_by_csid:
                    log_by_csid[csid] = e.get('status', '')
        except Exception:
            pass
        sess_lines = []
        for c in convos:
            sid = c['session_id']
            st = live_by_csid.get(sid) or log_by_csid.get(sid) or (
                'interrupted' if c['turns'] > 0 else 'empty'
            )
            label = c['last_user'] or c['first_user'] or '(empty)'
            label = ' '.join(label.split())[:80]
            sess_lines.append(f"  - [{st}] {label} | claude -r {sid}")
        parts.append(
            "Recent conversations (use 'claude -r <id>' to resume any of these — "
            "label is the user's LAST message):\n" + "\n".join(sess_lines)
        )
    else:
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


# ── Agent → backlog sync (TodoWrite interception) ───────────────────────────
# When an agent calls the TodoWrite tool, we upsert its todo list into the
# project's backlog so that in-flight tasks survive agent crashes / reboots.
# Items are keyed by (session, content-hash) so repeated TodoWrite calls in the
# same session update the same rows rather than duplicating.

_backlog_sync_lock = threading.Lock()


def _agent_todo_ref(session_key, content):
    """Stable dedup key for a TodoWrite item within a session."""
    norm = (content or '').strip().lower()
    h = hashlib.md5(f"{session_key}|{norm}".encode('utf-8')).hexdigest()[:12]
    return f"agent:{h}"


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


def _auto_snapshot_notes_on_turn(session):
    """At a turn boundary, append the last substantive assistant text as a note
    on every in_progress agent-sourced backlog item owned by this session."""
    try:
        sk = (session.get('claude_session_id')
              or session.get('id')
              or session.get('session_id'))
        pid = session.get('project_id')
        if not sk or not pid:
            return
        lines = session.get('log_lines', []) or []
        start = session.get('_last_result_log_index', 0)
        session['_last_result_log_index'] = len(lines)
        if start >= len(lines):
            return
        fragments = []
        for ln in lines[start:]:
            s = (ln or '').strip()
            if not s or s.startswith('['):
                continue
            fragments.append(s)
        if not fragments:
            return
        summary = ' '.join(fragments)[:300].strip()
        if len(summary) < 20:
            return
        with _backlog_sync_lock:
            try:
                p = load_project(pid)
            except Exception:
                return
            if p is None:
                return
            agent_code = sk[:8] if isinstance(sk, str) else 'agent'
            updated = False
            now = now_iso()
            for it in p.get('backlog', []) or []:
                if (it.get('agent_session_id') == sk
                        and it.get('agent_status') == 'in_progress'):
                    notes = it.setdefault('notes', [])
                    if notes and notes[-1].get('text') == summary:
                        continue
                    notes.append({'ts': now, 'agent_code': agent_code, 'text': summary})
                    if len(notes) > 50:
                        it['notes'] = notes[-50:]
                    updated = True
            if updated:
                p['last_updated'] = now
                try:
                    save_project(pid, p)
                except Exception:
                    return
    except Exception:
        pass


def _sync_todowrite_to_backlog(project_id, session_key, todos):
    """Upsert a TodoWrite list into the project's backlog.

    TodoWrite is called with the agent's full current task list each time,
    so we upsert every item and leave items no longer present untouched
    (the user can clean them up; we don't auto-delete agent context).

    session_key: stable identifier (claude_session_id preferred) so the same
                 logical session updates the same rows across TodoWrite calls.
    todos: list of {content, status, activeForm} dicts from tool_input.
    """
    if not project_id or not session_key or not todos or not isinstance(todos, list):
        return 0
    with _backlog_sync_lock:
        try:
            p = load_project(project_id)
        except Exception:
            return 0
        if p is None:
            return 0
        backlog = p.setdefault('backlog', [])
        existing_by_ref = {i.get('agent_ref'): i for i in backlog if i.get('agent_ref')}
        now = now_iso()
        touched = 0

        for td in todos:
            if not isinstance(td, dict):
                continue
            content = (td.get('content') or '').strip()
            if not content:
                continue
            agent_status = td.get('status', 'pending')  # pending | in_progress | completed
            active_form = (td.get('activeForm') or '').strip()
            ref = _agent_todo_ref(session_key, content)
            backlog_status = 'done' if agent_status == 'completed' else 'open'

            if ref in existing_by_ref:
                item = existing_by_ref[ref]
                item['text'] = content
                item['status'] = backlog_status
                item['agent_status'] = agent_status
                item['agent_activity'] = active_form if agent_status == 'in_progress' else ''
                item['updated_at'] = now
                if backlog_status == 'done' and not item.get('done_at'):
                    item['done_at'] = now
                elif backlog_status == 'open':
                    item['done_at'] = None
            else:
                backlog.insert(0, {
                    'id': str(uuid.uuid4())[:8],
                    'text': content,
                    'priority': 'normal',
                    'status': backlog_status,
                    'created_at': now,
                    'updated_at': now,
                    'done_at': now if backlog_status == 'done' else None,
                    'source': 'agent:todowrite',
                    'agent_ref': ref,
                    'agent_session_id': session_key,
                    'agent_status': agent_status,
                    'agent_activity': active_form if agent_status == 'in_progress' else '',
                    'attachments': [],
                })
            touched += 1

        if touched:
            p['last_updated'] = now
            try:
                save_project(project_id, p)
            except Exception:
                return 0
        return touched


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
    elif name == 'TodoWrite':
        todos = inp.get('todos', []) or []
        total = len(todos)
        done = sum(1 for t in todos if isinstance(t, dict) and t.get('status') == 'completed')
        in_prog = next((t.get('content', '') for t in todos
                        if isinstance(t, dict) and t.get('status') == 'in_progress'), '')
        summary = f'{done}/{total}'
        if in_prog:
            summary += f' — now: {in_prog[:60]}'
        return f'[tool: TodoWrite] {summary}'
    else:
        return f'[tool: {name}]'


# ── Single-emit gate ─────────────────────────────────────────────────────────
# Phase 1 of the 2026-04-27 race-condition consolidation: every place that
# wanted to write session['status'] / 'process_alive' / emit a status event
# from a stream-reader thread now goes through this one check. Returns True
# iff `my_proc` is still the authoritative process for this session AND the
# session isn't mid-interrupt (kill in flight, new proc not yet registered).
#
# Rationale: the old `session.get('proc') is my_proc` check was correct as
# far as it went, but `agent_interrupt` kills the old proc BEFORE the new
# one is spawned and registered, so the old reader's finally block could
# still pass that check during the kill→respawn gap and emit a stale
# terminal status (`error`/`completed`) that flipped the UI to "stopped".
# The `_interrupting` flag closes that gap: it is set under the lock at the
# top of `agent_interrupt`, cleared under the lock when the new proc is
# assigned to `session['proc']`. While set, the old reader's writes are
# discarded, the new reader's writes are still legitimate (it always passes
# `proc is session['proc']`).
def _session_owned_by(session, my_proc):
    """True iff `my_proc` is still the authoritative proc for this session."""
    if session.get('_interrupting'):
        return False
    return session.get('proc') is my_proc


def _read_agent_stream(proc, session):
    """Reader thread: captures stdout lines into session log_lines."""
    # Snapshot the proc we were launched with so we can detect if a follow-up
    # replaced us with a newer process while we were still draining stdout.
    my_proc = proc
    try:
        for raw_line in proc.stdout:
            # If session proc changed (or interrupt in flight), a follow-up
            # superseded us — stop writing.
            if not _session_owned_by(session, my_proc):
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
                            session['last_output_time'] = _time.time()
                        elif block.get('type') == 'tool_use':
                            tool_name = block.get('name', '')
                            tool_input = block.get('input', {})
                            activity = _format_tool_activity(tool_name, tool_input)
                            session['log_lines'].append(activity)
                            session['last_output_time'] = _time.time()
                            # Track .md file edits for plan file detection
                            if tool_name in ('Write', 'Edit'):
                                fp = tool_input.get('file_path', '')
                                if fp.lower().endswith('.md'):
                                    session['_last_md_file'] = fp
                            elif tool_name == 'ExitPlanMode':
                                if session.get('_last_md_file'):
                                    session['plan_file'] = session['_last_md_file']
                                session['waiting_for_plan_approval'] = True
                                session['log_lines'].append('[Plan mode exit detected — waiting for user approval]')
                            elif tool_name == 'TodoWrite':
                                try:
                                    sk = (session.get('claude_session_id')
                                          or session.get('id')
                                          or session.get('session_id'))
                                    n = _sync_todowrite_to_backlog(
                                        session.get('project_id'), sk,
                                        tool_input.get('todos', []))
                                    if n:
                                        session['log_lines'].append(
                                            f'[backlog: synced {n} item(s) from TodoWrite]')
                                except Exception as e:
                                    session['log_lines'].append(f'[backlog-sync error: {e}]')
                            elif tool_name == 'AskUserQuestion':
                                session.setdefault('pending_questions', []).append(tool_input)
                                session['waiting_for_question'] = True
                                # Transition to 'idle' BEFORE killing so the guardian
                                # doesn't race in and mark us 'error' when it sees a
                                # dead process with status still 'running'.
                                session['status'] = 'idle'
                                session['last_status_change_time'] = _time.time()
                                # Kill process — the auto-resolved turn is wasted.
                                # User's answer will resume the session via follow-up.
                                try:
                                    proc.kill()
                                except OSError:
                                    pass
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
                    _auto_snapshot_notes_on_turn(session)
            except json.JSONDecodeError:
                session['log_lines'].append(line)
                session['last_output_time'] = _time.time()
    except Exception as e:
        # Only log stream errors if we're still the active reader
        # and the process wasn't intentionally killed (question/stop)
        if _session_owned_by(session, my_proc):
            if not session.get('waiting_for_question') and session.get('status') not in ('stopped',):
                session['log_lines'].append(f"[stream error: {e}]")
    finally:
        rc = proc.wait()
        _unregister_process(proc.pid)
        # Acquire per-project lock to prevent race with agent_stop setting 'stopped'
        with get_manager(session['project_id']).lock:
            # Single-emit gate: only update session status if we still own it.
            # Covers normal replacement (new proc assigned) AND in-flight interrupt
            # (kill issued, new proc not yet spawned — `_interrupting` flag set).
            if _session_owned_by(session, my_proc):
                # Never overwrite 'stopped' — that's a user-initiated terminal state
                if session['status'] == 'running':
                    if session.get('waiting_for_question'):
                        # Process was intentionally killed after AskUserQuestion —
                        # not an error, just waiting for user's answer
                        session['status'] = 'idle'
                        session['last_status_change_time'] = _time.time()
                    else:
                        session['status'] = 'completed' if rc == 0 else 'error'
                        session['last_status_change_time'] = _time.time()
                        if rc != 0:
                            session['log_lines'].append(f"[exited with code {rc}]")
                        if rc == 0:
                            session['recovery_attempts'] = 0
                            session['guardian_state'] = None
                            session['pending_recovery_message'] = None
                            session['circuit_breaker_tripped'] = False
                elif session['status'] == 'stopped':
                    pass  # User stopped — don't change status regardless of rc
                _log_agent_completion(session)

                # Auto-dispatch pending follow-ups
                pending = session.get('pending_followups', [])
                if pending:
                    session['_dispatching_followup'] = True
                    followup_msg = pending.pop(0)
                    _auto_dispatch_followup(session, followup_msg)
                    session.pop('_dispatching_followup', None)

        # Auto-recover failed resume (Mode A)
        if (session.get('_resume_id')
                and session.get('status') == 'error'
                and not session.get('_resume_recovery_attempted')
                and _time.time() - session.get('_dispatch_time', 0) < 60
                and not session.get('num_turns')):
            _auto_recover_failed_resume(session)


def _read_agent_stream_b(proc, session):
    """Reader thread for Mode B: persistent process with stream-json I/O.

    Unlike Mode A, the process does NOT exit after each turn.
    A 'result' message signals the end of a turn, not the end of the process.
    """
    my_proc = proc
    try:
        for raw_line in proc.stdout:
            if not _session_owned_by(session, my_proc):
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
                            session['last_output_time'] = _time.time()
                        elif block.get('type') == 'tool_use':
                            tool_name = block.get('name', '')
                            tool_input = block.get('input', {})
                            activity = _format_tool_activity(tool_name, tool_input)
                            session['log_lines'].append(activity)
                            session['last_output_time'] = _time.time()
                            if tool_name in ('Write', 'Edit'):
                                fp = tool_input.get('file_path', '')
                                if fp.lower().endswith('.md'):
                                    session['_last_md_file'] = fp
                            elif tool_name == 'ExitPlanMode':
                                if session.get('_last_md_file'):
                                    session['plan_file'] = session['_last_md_file']
                                session['waiting_for_plan_approval'] = True
                                session['log_lines'].append('[Plan mode exit detected — waiting for user approval]')
                            elif tool_name == 'TodoWrite':
                                try:
                                    sk = (session.get('claude_session_id')
                                          or session.get('id')
                                          or session.get('session_id'))
                                    n = _sync_todowrite_to_backlog(
                                        session.get('project_id'), sk,
                                        tool_input.get('todos', []))
                                    if n:
                                        session['log_lines'].append(
                                            f'[backlog: synced {n} item(s) from TodoWrite]')
                                except Exception as e:
                                    session['log_lines'].append(f'[backlog-sync error: {e}]')
                            elif tool_name == 'AskUserQuestion':
                                session.setdefault('pending_questions', []).append(tool_input)
                                session['waiting_for_question'] = True
                                # Transition to 'idle' BEFORE killing so the guardian
                                # doesn't race in and mark us 'error' when it sees a
                                # dead process with status still 'running'.
                                session['status'] = 'idle'
                                session['last_status_change_time'] = _time.time()
                                # Kill process — the auto-resolved turn is wasted.
                                # User's answer will resume via follow-up (respawns process).
                                try:
                                    proc.kill()
                                except OSError:
                                    pass
                elif msg_type == 'result':
                    if 'session_id' in msg:
                        session['claude_session_id'] = msg['session_id']
                    if 'usage' in msg:
                        session['usage'] = msg['usage']
                    if 'cost_usd' in msg:
                        session['cost_usd'] = msg['cost_usd']
                    if 'num_turns' in msg:
                        session['num_turns'] = msg['num_turns']
                    _auto_snapshot_notes_on_turn(session)
                    # Turn boundary — process stays alive
                    session['status'] = 'idle'
                    session['last_status_change_time'] = _time.time()
            except json.JSONDecodeError:
                session['log_lines'].append(line)
                session['last_output_time'] = _time.time()
            # Cap log_lines to prevent unbounded memory growth
            if len(session['log_lines']) > 2000:
                session['log_lines'] = session['log_lines'][-1500:]
    except Exception as e:
        if _session_owned_by(session, my_proc):
            if not session.get('waiting_for_question') and session.get('status') not in ('stopped',):
                session['log_lines'].append(f"[stream error: {e}]")
    finally:
        rc = proc.wait()
        _unregister_process(proc.pid)
        # Acquire per-project lock to prevent race with agent_stop setting 'stopped'
        with get_manager(session['project_id']).lock:
            # Single-emit gate (see _session_owned_by). Skip when interrupt
            # is in flight — the new reader will set process_alive=True/status
            # legitimately and there's no point flipping it False between.
            if _session_owned_by(session, my_proc):
                session['process_alive'] = False
                # Never overwrite 'stopped' — that's a user-initiated terminal state
                if session['status'] in ('running', 'idle'):
                    if session.get('waiting_for_question'):
                        # Process was intentionally killed after AskUserQuestion —
                        # not an error, just waiting for user's answer
                        session['status'] = 'idle'
                        session['last_status_change_time'] = _time.time()
                    else:
                        session['status'] = 'completed' if rc == 0 else 'error'
                        session['last_status_change_time'] = _time.time()
                        if rc != 0:
                            session['log_lines'].append(f"[exited with code {rc}]")
                        if rc == 0:
                            session['recovery_attempts'] = 0
                            session['guardian_state'] = None
                            session['pending_recovery_message'] = None
                            session['circuit_breaker_tripped'] = False
                elif session['status'] == 'stopped':
                    pass  # User stopped — don't change status regardless of rc
                _log_agent_completion(session)

        # Auto-recover failed resume: if we tried to resume a prior session and
        # it died quickly without producing meaningful output, restart fresh.
        if (session.get('_resume_id')
                and session.get('status') == 'error'
                and not session.get('_resume_recovery_attempted')
                and _time.time() - session.get('_dispatch_time', 0) < 60
                and not session.get('num_turns')):
            _auto_recover_failed_resume(session)


def _auto_recover_failed_resume(session):
    """When a resumed session dies immediately, silently restart fresh.

    Reuses the same session object so the frontend sees seamless recovery.
    """
    session['_resume_recovery_attempted'] = True
    project_id = session['project_id']
    task = session.get('task', '')
    mode = session.get('mode', 'A')
    resume_id = session.get('_resume_id', '')

    p = load_project(project_id)
    if not p:
        return
    pp = p.get('project_path', '')
    if not pp or not Path(pp).is_dir():
        return

    session['log_lines'].append(
        f'[Resume of session {resume_id[:12]} failed — restarting fresh]')
    print(f"[dispatch] Resume {resume_id[:12]} failed for {project_id}, retrying fresh")
    _log_agent_activity(project_id, f"Resume failed, restarting fresh: {task[:80]}")

    context = _build_agent_context(p)
    fresh_task = (f"[Continuing from a previous conversation (session {resume_id}) that could not "
                  f"be resumed. Start fresh but continue the user's request below.]\n\n{task}")

    try:
        if mode == 'B':
            cmd = ['claude', *_build_claude_flags(p, streaming=True),
                   '--append-system-prompt', context]
            proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, cwd=pp,
                text=True, encoding='utf-8', errors='replace',
                creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
            )
            initial_msg = json.dumps({
                "type": "user",
                "message": {"role": "user", "content": fresh_task}
            }) + '\n'
            proc.stdin.write(initial_msg)
            proc.stdin.flush()

            threading.Thread(target=_hide_windows_delayed, args=(proc.pid,), daemon=True).start()
            _register_process(proc, 'Agent (Mode B, fresh retry)', 'agent',
                              session['session_id'], project_id, task[:80])

            mgr = get_manager(project_id)
            with mgr.lock:
                session['proc'] = proc
                session['status'] = 'running'
                session['process_alive'] = True
                session['stdin_lock'] = threading.Lock()
                session['last_output_time'] = _time.time()
                session['last_status_change_time'] = _time.time()
                session['_resume_id'] = None  # no longer a resume
                session['guardian_state'] = None
                session['recovery_attempts'] = 0
                session['circuit_breaker_tripped'] = False

            threading.Thread(target=_read_agent_stream_b, args=(proc, session), daemon=True).start()

        else:
            # Mode A
            cmd = ['claude', '-p', fresh_task, *_build_claude_flags(p),
                   '--append-system-prompt', context]
            proc = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, cwd=pp,
                text=True, encoding='utf-8', errors='replace',
                creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
            )
            threading.Thread(target=_hide_windows_delayed, args=(proc.pid,), daemon=True).start()
            _register_process(proc, 'Agent (Mode A, fresh retry)', 'agent',
                              session['session_id'], project_id, task[:80])

            mgr = get_manager(project_id)
            with mgr.lock:
                session['proc'] = proc
                session['status'] = 'running'
                session['last_output_time'] = _time.time()
                session['last_status_change_time'] = _time.time()
                session['_resume_id'] = None
                session['guardian_state'] = None
                session['recovery_attempts'] = 0
                session['circuit_breaker_tripped'] = False

            threading.Thread(target=_read_agent_stream, args=(proc, session), daemon=True).start()

    except Exception as e:
        session['log_lines'].append(f'[Fresh restart also failed: {e}]')
        session['status'] = 'error'
        print(f"[dispatch] Fresh retry failed for {project_id}: {e}")


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
    """Persist the agent log, trimming to the most recent N entries.

    Entries are inserted at index 0 (newest first), so list[:N] keeps the newest.
    Cap is `agent_log_max_entries` in config.json (default 500). Set to 0 to
    disable trimming (keep everything — file grows unbounded).
    """
    filepath = DATA_DIR / f'{project_id}_agent_log.json'
    cap = int(CONFIG.get('agent_log_max_entries', 500) or 0)
    if cap > 0 and len(log) > cap:
        log = log[:cap]
    filepath.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding='utf-8')


def _backfill_agent_log_from_transcripts(project_id, project):
    """Synthesize agent_log entries for Claude transcripts that have no matching log row.

    Scenario this fixes: a session is dispatched via MC, runs for hours, but the server
    is restarted before the stream reader's `finally` block can call _log_agent_completion().
    The Claude transcript on disk survives but MC has no record of it — so the Agent Log
    tab is empty for that session and `_revive_from_agent_log` can't find it either.

    Walks the project's transcript directory, compares each .jsonl filename to the set of
    claude_session_ids already in <pid>_agent_log.json, and inserts a synthesized entry for
    any missing transcript newer than `agent_log_backfill_max_age_days`. Synthesized entries
    are tagged with `synthesized: True` and `status: 'interrupted'`.

    Roll back: set CONFIG['agent_log_backfill_enabled'] = False, restart MC.
    """
    if not CONFIG.get('agent_log_backfill_enabled', True):
        return 0
    pp = (project or {}).get('project_path', '')
    if not pp or not Path(pp).is_dir():
        return 0

    max_n = int(CONFIG.get('agent_log_backfill_max_per_project', 200))
    max_age_days = int(CONFIG.get('agent_log_backfill_max_age_days', 60))
    cutoff_ts = _time.time() - max_age_days * 86400

    transcripts = _recent_claude_transcripts(pp, limit=max_n)
    if not transcripts:
        return 0

    log = _load_agent_log(project_id)
    known_csids = {e.get('claude_session_id') for e in log if e.get('claude_session_id')}

    added = 0
    for t in transcripts:
        csid = t.get('session_id')  # this is the .jsonl filename / claude_session_id
        if not csid or csid in known_csids:
            continue
        if t.get('mtime', 0) < cutoff_ts:
            continue
        try:
            ts_iso = datetime.fromtimestamp(t['mtime'], tz=timezone.utc).isoformat().replace('+00:00', 'Z')
        except Exception:
            ts_iso = now_iso()
        first_user = t.get('first_user', '') or ''
        last_user = t.get('last_user', '') or ''
        log.insert(0, {
            'ts': ts_iso,
            'task': first_user[:300],
            'status': 'interrupted',
            'summary': last_user[:1000],
            'session_id': '',  # MC never owned this session — leave empty so revival creates a new sid
            'claude_session_id': csid,
            'started_at': ts_iso,
            'usage': {},
            'cost_usd': 0,
            'num_turns': t.get('turns', 0),
            'plan_file': '',
            'hivemind_id': '',
            'hivemind_ws_id': '',
            'hivemind_role': '',
            'synthesized': True,
        })
        added += 1

    if added:
        log.sort(key=lambda e: e.get('ts', ''), reverse=True)
        _save_agent_log(project_id, log)
        print(f"[backfill] {project_id}: added {added} synthesized log entr{'y' if added == 1 else 'ies'} from transcripts")
    return added


def _backfill_all_agent_logs():
    """Run agent_log backfill across every project. Called once at server startup.

    Wrapped in a thread by the caller so it doesn't block app.run().
    """
    if not CONFIG.get('agent_log_backfill_enabled', True):
        return
    try:
        projects = load_projects()
    except Exception as e:
        print(f"[backfill] load_projects failed: {e}")
        return
    total = 0
    for p in projects:
        pid = p.get('id')
        if not pid:
            continue
        # Skip the global incognito project — it intentionally has no agent log.
        if p.get('_is_incognito_project') or pid == INCOGNITO_PROJECT_ID:
            continue
        try:
            total += _backfill_agent_log_from_transcripts(pid, p)
        except Exception as e:
            print(f"[backfill] {pid}: {e}")
    if total:
        print(f"[backfill] done: {total} synthesized entr{'y' if total == 1 else 'ies'} across {len(projects)} project(s)")


def _revive_from_agent_log(project_id, session_id, message, p):
    """Revive a finalized/purged session by spawning a fresh process with -r <claude_session_id>.

    Looks up the most recent agent_log entry whose session_id matches; if it has a
    claude_session_id we can resume from, builds a new session dict that reuses the
    same session_id so the frontend's UI tab stays addressed.

    Roll back: set CONFIG['agent_revive_from_log'] = False (the only call site checks
    this flag before calling). Or delete this function and the gated block in
    agent_followup.

    Returns the new session dict on success, None if not revivable (no matching
    log entry, no claude_session_id, missing project_path, or spawn failure).
    """
    if not CONFIG.get('agent_revive_from_log', True):
        return None

    log = _load_agent_log(project_id)
    entry = next((e for e in log if e.get('session_id') == session_id), None)
    if not entry:
        return None
    claude_sid = entry.get('claude_session_id')
    if not claude_sid:
        return None

    pp = p.get('project_path', '')
    if not pp or not Path(pp).is_dir():
        return None

    use_streaming = p.get('use_streaming_agent', CONFIG.get('use_streaming_agent', False))

    too_large, size_bytes = _session_too_large(pp, claude_sid)
    resume_flags = []
    context = None
    revival_msg = message
    if too_large:
        size_mb = size_bytes / (1024 * 1024)
        context = _build_agent_context(p)
        revival_msg = (f"[Resuming a previous conversation that grew too large to "
                       f"resume directly ({size_mb:.0f} MB). Start fresh but continue "
                       f"the user's request below.]\n\n{message}")
    else:
        resume_flags = ['-r', claude_sid]

    mgr = get_manager(project_id)
    mgr.ensure_guardian()
    user_label = CONFIG.get('user_name') or 'User'
    revive_note = f'[Session revived from agent log — resuming claude_session={claude_sid[:12]}]'

    if use_streaming:
        cmd = ['claude', *resume_flags, *_build_claude_flags(p, streaming=True)]
        if not resume_flags and context:
            cmd.extend(['--append-system-prompt', context])
        try:
            proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, cwd=pp,
                text=True, encoding='utf-8', errors='replace',
                creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
            )
        except Exception as e:
            print(f"[revive] {project_id}: spawn failed: {e}")
            return None
        threading.Thread(target=_hide_windows_delayed, args=(proc.pid,), daemon=True).start()
        _register_process(proc, 'Agent revived (B)', 'agent', session_id, project_id, message[:80])

        session = {
            'proc': proc,
            'status': 'running',
            'task': entry.get('task', ''),
            'log_lines': [revive_note, f"\n> {user_label}: {message}\n"],
            'started_at': now_iso(),
            'session_id': session_id,
            'project_id': project_id,
            'mode': 'B',
            'stdin_lock': threading.Lock(),
            'process_alive': True,
            'last_output_time': _time.time(),
            'last_status_change_time': _time.time(),
            'guardian_state': None,
            'recovery_attempts': 0,
            'last_recovery_time': 0,
            'pending_recovery_message': None,
            'circuit_breaker_tripped': False,
            'claude_session_id': claude_sid,
            '_resume_id': claude_sid,
            '_dispatch_time': _time.time(),
            'usage': entry.get('usage', {}),
            'cost_usd': entry.get('cost_usd', 0),
            'num_turns': entry.get('num_turns', 0),
        }
        with mgr.lock:
            agent_sessions[session_id] = session
            mgr.session_ids.add(session_id)
        threading.Thread(target=_read_agent_stream_b, args=(proc, session), daemon=True).start()
        stdin_msg = json.dumps({"type": "user", "message": {"role": "user", "content": revival_msg}}) + '\n'
        with session['stdin_lock']:
            try:
                proc.stdin.write(stdin_msg)
                proc.stdin.flush()
            except Exception as e:
                session['log_lines'].append(f'[stdin write error on revive: {e}]')
        print(f"[revive] {project_id}: Mode B revived session {session_id} via -r {claude_sid[:12]}")
        return session

    # Mode A
    if resume_flags:
        cmd = ['claude', *resume_flags, '-p', revival_msg, *_build_claude_flags(p)]
    else:
        if not context:
            context = _build_agent_context(p)
        cmd = ['claude', '-p', revival_msg, *_build_claude_flags(p),
               '--append-system-prompt', context]
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, cwd=pp,
            text=True, encoding='utf-8', errors='replace',
            creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
        )
    except Exception as e:
        print(f"[revive] {project_id}: spawn failed: {e}")
        return None
    threading.Thread(target=_hide_windows_delayed, args=(proc.pid,), daemon=True).start()
    _register_process(proc, 'Agent revived (A)', 'agent', session_id, project_id, message[:80])

    session = {
        'proc': proc,
        'status': 'running',
        'task': entry.get('task', ''),
        'log_lines': [revive_note, f"\n> {user_label}: {message}\n"],
        'started_at': now_iso(),
        'session_id': session_id,
        'project_id': project_id,
        'mode': 'A',
        'last_output_time': _time.time(),
        'last_status_change_time': _time.time(),
        'guardian_state': None,
        'recovery_attempts': 0,
        'last_recovery_time': 0,
        'pending_recovery_message': None,
        'circuit_breaker_tripped': False,
        'claude_session_id': claude_sid,
        '_resume_id': claude_sid,
        '_dispatch_time': _time.time(),
        'usage': entry.get('usage', {}),
        'cost_usd': entry.get('cost_usd', 0),
        'num_turns': entry.get('num_turns', 0),
    }
    with mgr.lock:
        agent_sessions[session_id] = session
        mgr.session_ids.add(session_id)
    threading.Thread(target=_read_agent_stream, args=(proc, session), daemon=True).start()
    print(f"[revive] {project_id}: Mode A revived session {session_id} via -r {claude_sid[:12]}")
    return session


def _log_agent_dispatch_pending(session):
    """Write a placeholder agent_log row at dispatch time so trigger correlation
    survives a server restart that kills the session before _log_agent_completion
    can run.

    Without this, scheduled / hivemind sessions that are still running (or are
    Mode B sessions sitting idle forever) appear in the log only after either
    (a) a clean finalization (rare for long-lived idle Mode B), or (b) a startup
    transcript backfill — and the backfill cannot recover trigger_type/trigger_id,
    so the schedule's "Runs" panel filter (`trigger_type==schedule AND trigger_id==X`)
    finds nothing. By dropping a row immediately, the trigger info is durable from
    the moment we spawn the process.

    Caller: _dispatch_agent_internal, only when trigger_type != 'manual'.
    Manual dispatches don't need correlation and would just double the agent_log
    write traffic for the common case.
    """
    project_id = session.get('project_id')
    if not project_id or session.get('incognito') or session.get('housekeeping'):
        return
    sid = session.get('session_id', '')
    if not sid:
        return
    entry = {
        'ts': now_iso(),
        'task': session.get('task', ''),
        'status': 'in_progress',
        'summary': '',
        'session_id': sid,
        'claude_session_id': '',  # populated on completion (Claude assigns this after first message)
        'started_at': session.get('started_at', ''),
        'usage': {},
        'cost_usd': 0,
        'num_turns': 0,
        'plan_file': '',
        'hivemind_id': session.get('hivemind_id', ''),
        'hivemind_ws_id': session.get('hivemind_ws_id', ''),
        'hivemind_role': session.get('hivemind_role', ''),
        'trigger_type': session.get('trigger_type', 'manual'),
        'trigger_id': session.get('trigger_id', ''),
    }
    try:
        log = _load_agent_log(project_id)
        log.insert(0, entry)
        _save_agent_log(project_id, log)
    except Exception as e:
        print(f"[dispatch-log] {project_id}: pending write failed: {e}")


def _reconcile_pending_agent_log_entries():
    """At startup, flip any leftover 'in_progress' agent_log rows to 'interrupted'.

    Pending rows come from _log_agent_dispatch_pending. If the server restarts
    while a session is in flight, the pending row never gets upserted by
    _log_agent_completion. At startup nothing is live yet, so any in_progress
    row is by definition orphaned.
    """
    try:
        projects = load_projects()
    except Exception as e:
        print(f"[reconcile-pending] load_projects failed: {e}")
        return
    flipped_total = 0
    for p in projects:
        pid = p.get('id')
        if not pid:
            continue
        if p.get('_is_incognito_project') or pid == INCOGNITO_PROJECT_ID:
            continue
        try:
            log = _load_agent_log(pid)
            changed = False
            for e in log:
                if e.get('status') == 'in_progress':
                    e['status'] = 'interrupted'
                    changed = True
                    flipped_total += 1
            if changed:
                _save_agent_log(pid, log)
        except Exception as e:
            print(f"[reconcile-pending] {pid}: {e}")
    if flipped_total:
        print(f"[reconcile-pending] flipped {flipped_total} orphaned in_progress entr{'y' if flipped_total == 1 else 'ies'} to 'interrupted'")


def _log_agent_completion(session):
    """Save a summary entry when an agent session finishes."""
    project_id = session.get('project_id')
    if not project_id:
        return

    # Incognito sessions are fully ephemeral from MC's perspective: no agent_log
    # entry, no memory append, no condense trigger. The Claude transcript on
    # disk is unaffected (that's outside MC's control).
    if session.get('incognito'):
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
        'hivemind_id': session.get('hivemind_id', ''),
        'hivemind_ws_id': session.get('hivemind_ws_id', ''),
        'hivemind_role': session.get('hivemind_role', ''),
        # Trigger correlation: lets us list runs by what spawned them.
        # trigger_type: 'manual' | 'schedule' | 'hivemind_orchestrator' | 'hivemind_worker'
        # trigger_id: schedule_id, hivemind_id, or workstream_id depending on type
        'trigger_type': session.get('trigger_type', 'manual'),
        'trigger_id': session.get('trigger_id', ''),
    }
    log = _load_agent_log(project_id)
    # Upsert: if a pending entry was written at dispatch time (non-manual trigger),
    # replace it in place so trigger_type/trigger_id survive the rewrite. Otherwise
    # insert at the top as before. Move the row to position 0 on update so newest-
    # finalized stays at the top (matches the "log.insert(0, ...)" convention).
    sid = entry['session_id']
    replaced = False
    if sid:
        for i, e in enumerate(log):
            if e.get('session_id') == sid and e.get('status') == 'in_progress':
                log.pop(i)
                replaced = True
                break
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
                if len(new_content.encode('utf-8')) > 20 * 1024:
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

                # Trigger condensation if thresholds met (include CLAUDE.md in check)
                if _should_condense(p, include_claude_md=True):
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
    session['last_status_change_time'] = _time.time()
    session['last_output_time'] = _time.time()
    session['pending_recovery_message'] = None
    _register_process(proc, 'Agent followup (A)', 'agent',
                      session['session_id'], session['project_id'], message[:80])
    user_label = CONFIG.get('user_name') or 'User'
    session['log_lines'].append(f"> {user_label}: {message}")

    t = threading.Thread(target=_read_agent_stream, args=(proc, session), daemon=True)
    t.start()


def _check_context_budget(project, appended_prompt):
    """Measure context files; if total exceeds 20KB, trigger condensation and return info string."""
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
    if total > 40 * 1024:
        parts = ', '.join(f'{k}: {v/1024:.1f}k' for k, v in sizes.items())
        # Actively trigger condensation instead of just warning
        if _should_condense(project, include_claude_md=True):
            _dispatch_condense(project)
            return f'[context trim] Auto-condensing context files ({parts}) — will be smaller next session.'
        # If condensation is already running or disabled, just note it
        pid = project['id']
        with _condense_lock:
            if pid in _condensing_projects:
                return f'[context trim] Condensation in progress ({parts}).'
        return None  # Don't warn if we can't act on it
    return None


def _dispatch_condense(project):
    """Launch a housekeeping agent to condense memory + CLAUDE.md for a project."""
    pid = project['id']
    with _condense_lock:
        if pid in _condensing_projects:
            return
        _condensing_projects.add(pid)

    mem_path = _get_memory_path(project)
    archive_path = _get_archive_path(project)
    pp = project.get('project_path', '')

    # Check if CLAUDE.md exists and is large enough to warrant condensation
    claude_md_path = Path(pp) / 'CLAUDE.md' if pp else None
    claude_md_big = False
    if claude_md_path and claude_md_path.exists():
        try:
            claude_md_big = claude_md_path.stat().st_size > 15 * 1024  # > 15KB
        except OSError:
            pass

    prompt_parts = [
        "You are a memory housekeeping agent. Your ONLY job is to condense the project context files "
        "so they stay concise and effective.\n",
        f"## MEMORY.md condensation — target under 15KB\n"
        f"1. Read {mem_path}\n"
        f"2. Read {archive_path} (if it exists)\n"
        "3. First-line tactics (use these before touching curated prose):\n"
        "   - If a '## Session Log' section exists: keep only the last 5 entries; fold useful insights "
        "from the rest into the matching curated knowledge sections.\n"
        "   - Move older session log overflow into the archive file.\n"
        "4. If still over 15KB after step 3, tighten the curated sections themselves:\n"
        "   - Merge overlapping sections (e.g., two sections covering the same subsystem).\n"
        "   - Drop stale 'as of YYYY-MM-DD' notes whose content has clearly been superseded by a later section.\n"
        "   - Remove redundant examples and excessive prose; keep the fact, cut the narration.\n"
        "   - Collapse bullet lists that restate the same idea.\n"
        "5. DO NOT lose hard-won facts. Preserve verbatim: file paths, line numbers, function/class names, "
        "config keys, exact numeric thresholds, API signatures, command snippets, and any 'gotcha' warnings.\n"
        f"6. Write the condensed result back to {mem_path}. Target under 15KB; if after honest tightening "
        f"the file is still slightly over, that is acceptable — do NOT delete critical facts just to hit a number.\n"
        f"7. Delete {archive_path} when done (if it exists and its contents have been folded in).\n",
    ]

    if claude_md_big:
        prompt_parts.append(
            f"\n## CLAUDE.md condensation — target under 15KB\n"
            f"8. Read {claude_md_path}\n"
            "9. This file contains project instructions and context that Claude CLI loads natively. "
            "Condense it while preserving ALL critical information:\n"
            "   - Keep all instructions, rules, and constraints verbatim.\n"
            "   - Merge duplicate/overlapping sections.\n"
            "   - Remove redundant examples, excessive formatting, and verbose explanations.\n"
            "   - Compress session logs / historical notes into brief summaries.\n"
            "   - Preserve code snippets, API references, and config patterns exactly.\n"
            f"10. Write the condensed result back to {claude_md_path}. Target under 15KB; do NOT "
            f"strip critical rules just to hit a number.\n"
        )

    prompt_parts.append(
        "\nDo NOT create any other files. Do NOT modify any code. Only touch the files listed above."
    )
    prompt = '\n'.join(prompt_parts)

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
            mgr = get_manager(pid)
            with mgr.lock:
                agent_sessions[session_id] = session
                mgr.session_ids.add(session_id)

            # Reuse existing stream reader
            _read_agent_stream(proc, session)
        except Exception as e:
            print(f"[condense] error for {pid}: {e}")
        finally:
            with _condense_lock:
                _condensing_projects.discard(pid)

    threading.Thread(target=_run, daemon=True).start()


def _dispatch_agent_internal(project_id, task, resume_id='', incognito=False,
                             trigger_type='manual', trigger_id=''):
    """Core dispatch logic shared by HTTP endpoint and scheduler.

    Returns session_id on success, raises ValueError on error.

    When incognito=True (or the project itself is the global incognito project),
    MEMORY/AGENT_RULES are skipped from --append-system-prompt and the session
    is flagged so _log_agent_completion will not write to the agent log or
    append to MEMORY.md.

    trigger_type/trigger_id annotate the resulting agent_log entry so callers
    (scheduler, hivemind dispatch) can later list "all runs for this trigger".
    Defaults are 'manual'/'' for direct user dispatch.
    """
    p = load_project(project_id)
    if not p:
        if project_id == INCOGNITO_PROJECT_ID:
            p = _ensure_incognito_project()
        else:
            raise ValueError('project not found')

    # Global incognito project always forces incognito on, regardless of caller.
    if p.get('_is_incognito_project') or project_id == INCOGNITO_PROJECT_ID:
        incognito = True

    pp = p.get('project_path', '')
    if not pp or not Path(pp).is_dir():
        raise ValueError('project_path not set or invalid')

    use_streaming = p.get('use_streaming_agent', CONFIG.get('use_streaming_agent', False))

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

    mgr = get_manager(project_id)
    mgr.ensure_guardian()
    with mgr.lock:
        session_id = uuid.uuid4().hex[:12]

        if use_streaming:
            # Mode B: persistent process with stream-json stdin
            if resume_id:
                cmd = ['claude', '-r', resume_id, *_build_claude_flags(p, streaming=True)]
            else:
                context = _build_agent_context(p, incognito=incognito)
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
                'last_output_time': _time.time(),
                'last_status_change_time': _time.time(),
                'guardian_state': None,
                'recovery_attempts': 0,
                'last_recovery_time': 0,
                'pending_recovery_message': None,
                'circuit_breaker_tripped': False,
                '_resume_id': resume_id or None,
                '_dispatch_time': _time.time(),
                'incognito': bool(incognito),
                'trigger_type': trigger_type,
                'trigger_id': trigger_id,
            }
            agent_sessions[session_id] = session
            mgr.session_ids.add(session_id)

            t = threading.Thread(target=_read_agent_stream_b, args=(proc, session), daemon=True)
            t.start()
        else:
            # Mode A: spawn-per-turn (existing behavior)
            if resume_id:
                cmd = ['claude', '-r', resume_id, '-p', task, *_build_claude_flags(p)]
            else:
                context = _build_agent_context(p, incognito=incognito)
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
                'last_output_time': _time.time(),
                'last_status_change_time': _time.time(),
                'guardian_state': None,
                'recovery_attempts': 0,
                'last_recovery_time': 0,
                'pending_recovery_message': None,
                'circuit_breaker_tripped': False,
                '_resume_id': resume_id or None,
                '_dispatch_time': _time.time(),
                'incognito': bool(incognito),
                'trigger_type': trigger_type,
                'trigger_id': trigger_id,
            }
            agent_sessions[session_id] = session
            mgr.session_ids.add(session_id)

            t = threading.Thread(target=_read_agent_stream, args=(proc, session), daemon=True)
            t.start()

        # Drop a pending row in the agent log immediately for non-manual triggers
        # so the schedule/hivemind "Runs" panel can correlate even if the session
        # never gets to call _log_agent_completion (long-lived idle Mode B session
        # killed by a server restart, etc.). Manual dispatches don't need this.
        if trigger_type and trigger_type != 'manual':
            _log_agent_dispatch_pending(session)

        # Context budget check — triggers auto-condensation if context too large
        if not resume_id:
            notice = _check_context_budget(p, context)
            if notice:
                session['log_lines'].append(notice)

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
    incognito = bool(data.get('incognito'))
    try:
        session_id = _dispatch_agent_internal(project_id, task, resume_id, incognito=incognito)
    except ValueError as e:
        code = 404 if 'not found' in str(e) else 400
        return jsonify({'error': str(e)}), code
    except FileNotFoundError:
        return jsonify({'error': 'Claude CLI not found. Install it with: npm install -g @anthropic-ai/claude-code'}), 500
    except Exception as e:
        return jsonify({'error': f'dispatch failed: {e}'}), 500
    return jsonify({'ok': True, 'session_id': session_id})


@app.route('/api/project/<project_id>/agent/send', methods=['POST'])
def agent_send(project_id):
    """Single user-intent endpoint. The server reads live session state
    under the per-project lock and routes to the correct internal handler:

      - no session_id, or session missing  → revive from agent_log if possible,
                                              else dispatch fresh
      - session exists, status == 'running' → interrupt-and-resume (atomic)
      - session exists, any other status    → followup (queues for Mode A,
                                              writes stdin for Mode B,
                                              respawns purged sessions)

    Frontend never picks the route. It just sends intent. Phase 2 of the
    2026-04-27 race-condition consolidation — see CHANGELOG `[2026-04-27i]`.
    """
    p = load_project(project_id)
    if not p and project_id == INCOGNITO_PROJECT_ID:
        p = _ensure_incognito_project()
    if not p:
        return jsonify({'error': 'project not found'}), 404
    pp = p.get('project_path', '')
    if not pp or not Path(pp).is_dir():
        return jsonify({'error': 'project_path not set'}), 400

    data = request.get_json() or {}
    message = (data.get('message') or '').strip()
    session_id = (data.get('session_id') or '').strip()
    incognito = (
        bool(data.get('incognito'))
        or bool(p.get('_is_incognito_project'))
        or project_id == INCOGNITO_PROJECT_ID
    )
    if not message:
        return jsonify({'error': 'message required'}), 400

    # Decision under the lock — this is the ONLY place that picks the route.
    with get_manager(project_id).lock:
        session = agent_sessions.get(session_id) if session_id else None
        if session and session.get('project_id') != project_id:
            session = None  # session belongs to a different project — ignore
        if not session:
            decision = 'fresh_or_revive'
        elif session.get('status') == 'running':
            decision = 'interrupt'
        else:
            decision = 'followup'

    # Route to the appropriate handler. Each does its own lock acquisition
    # for the actual mutation; the decision above is just to pick the path.
    # The existing handlers read `request.get_json()` themselves; they get
    # the same body we got. We tag the response so the frontend can log the
    # route taken (useful for debugging; FE doesn't act on it).
    if decision == 'interrupt':
        resp = agent_interrupt(project_id)
    elif decision == 'followup':
        resp = agent_followup(project_id)
    else:  # fresh_or_revive
        if session_id:
            try:
                revived = _revive_from_agent_log(project_id, session_id, message, p)
            except Exception as e:
                revived = None
                _log_agent_activity(project_id, f"Revive error in /send: {e}")
            if revived:
                return jsonify({'ok': True, 'session_id': session_id,
                                'revived': True, 'route': 'revive'})
        # Otherwise dispatch a fresh session
        try:
            new_session_id = _dispatch_agent_internal(project_id, message, incognito=incognito)
        except ValueError as e:
            code = 404 if 'not found' in str(e) else 400
            return jsonify({'error': str(e)}), code
        except FileNotFoundError:
            return jsonify({'error': 'Claude CLI not found.'}), 500
        except Exception as e:
            return jsonify({'error': f'dispatch failed: {e}'}), 500
        return jsonify({'ok': True, 'session_id': new_session_id, 'route': 'dispatch'})

    # Tag the upstream response with the route we took. Flask Response objects
    # support get_json(); we rebuild and return.
    try:
        body = resp.get_json(silent=True) or {}
        if isinstance(body, dict):
            body.setdefault('route', decision)
            return jsonify(body), resp.status_code
    except Exception:
        pass
    return resp


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
        last_guardian_state = None
        # Phase 2 (2026-04-27): the FE no longer flips status optimistically,
        # so we need to tell it when a new turn starts (status: idle -> running)
        # so the UI reflects reality without closing the stream. Sent as
        # `turn_start` so the existing `status` handler (which closes on
        # terminal states) is unaffected.
        last_emitted_status = None
        while True:
            session['_last_sse_poll_time'] = _time.time()
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

            # Emit a `turn_start` event whenever status transitions INTO 'running'.
            # FE relies on this for the running-state UI flip post-Phase-2.
            if status == 'running' and last_emitted_status != 'running':
                yield f"data: {json.dumps({'type': 'turn_start', 'status': 'running'})}\n\n"
            last_emitted_status = status

            if is_mode_b:
                if status == 'idle' and not idle_sent:
                    # Turn finished but process is still alive
                    yield f"data: {json.dumps({'type': 'turn_complete', 'status': 'idle', 'usage': session.get('usage', {}), 'cost_usd': session.get('cost_usd', 0), 'num_turns': session.get('num_turns', 0)})}\n\n"
                    idle_sent = True
                elif status == 'running':
                    idle_sent = False  # reset for next turn
                elif status not in ('running', 'idle'):
                    if session.get('guardian_state') == 'recovering':
                        pass  # Wait for guardian recovery to complete
                    else:
                        yield f"data: {json.dumps({'type': 'status', 'status': status, 'usage': session.get('usage', {}), 'cost_usd': session.get('cost_usd', 0), 'num_turns': session.get('num_turns', 0)})}\n\n"
                        break
            else:
                # Mode A: close stream on terminal states immediately;
                # for non-terminal non-running, wait only if followups pending
                if status == 'stopped':
                    yield f"data: {json.dumps({'type': 'status', 'status': status, 'usage': session.get('usage', {}), 'cost_usd': session.get('cost_usd', 0), 'num_turns': session.get('num_turns', 0)})}\n\n"
                    break
                elif status != 'running':
                    if session.get('guardian_state') == 'recovering':
                        pass  # Wait for guardian recovery to complete
                    elif not session.get('pending_followups') and not session.get('_dispatching_followup'):
                        yield f"data: {json.dumps({'type': 'status', 'status': status, 'usage': session.get('usage', {}), 'cost_usd': session.get('cost_usd', 0), 'num_turns': session.get('num_turns', 0)})}\n\n"
                        break

            # Emit guardian state changes
            g_state = session.get('guardian_state')
            if g_state != last_guardian_state:
                yield f"data: {json.dumps({'type': 'guardian', 'state': g_state, 'circuit_breaker': session.get('circuit_breaker_tripped', False)})}\n\n"
                last_guardian_state = g_state

            # Heartbeat every ~15s to keep connection alive
            # Sent as data event (not comment) so browser onmessage fires
            # and frontend watchdog can detect silent connection death.
            tick += 1
            if tick % 50 == 0:
                yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"

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

    _respawn_b = None  # set if Mode B needs to respawn outside lock

    # Pre-check: if session is gone from agent_sessions (server restart, tab close,
    # 24h purge), try reviving from agent_log via -r <claude_session_id>.
    # Roll back: set CONFIG['agent_revive_from_log'] = False.
    mgr_pre = get_manager(project_id)
    with mgr_pre.lock:
        _has_session = (session_id in agent_sessions
                        and agent_sessions[session_id].get('project_id') == project_id)
    if not _has_session:
        revived = _revive_from_agent_log(project_id, session_id, message, p)
        if revived:
            _log_agent_activity(project_id, f"Agent revived from log: {message[:100]}")
            return jsonify({'ok': True, 'session_id': session_id, 'revived': True})
        # No revivable entry — fall through to original 404 below.

    with get_manager(project_id).lock:
        existing = agent_sessions.get(session_id)
        if not existing or existing['project_id'] != project_id:
            return jsonify({'error': 'session not found'}), 404

        # Clear plan approval / question flags — user has responded
        existing['waiting_for_plan_approval'] = False
        existing['waiting_for_question'] = False

        if existing.get('mode') == 'B':
            # Mode B: verify process is actually alive before trusting the flag
            if existing.get('process_alive'):
                proc = existing.get('proc')
                if proc and (proc.poll() is not None or not _pid_is_alive(proc.pid)):
                    existing['process_alive'] = False
                    existing['log_lines'].append(
                        f'[Process {proc.pid} found dead on followup — will respawn]')
            if not existing.get('process_alive'):
                # Process died (hard stop or crash) — respawn
                claude_sid = existing.get('claude_session_id')
                was_resume = bool(existing.get('_resume_id'))
                resume_flags = []
                context = None

                if not claude_sid and not was_resume:
                    # No session ID at all and wasn't a resume — can't continue
                    print(f"[followup] {project_id}: no claude_session_id, starting fresh")
                    context = _build_agent_context(p)
                    message = (f"[Previous conversation had no session ID to resume. "
                               f"Starting fresh.]\n\n{message}")
                elif not claude_sid and was_resume:
                    # Was a resume but CLI never emitted a session_id — start fresh
                    print(f"[followup] {project_id}: resume never emitted session_id, starting fresh")
                    context = _build_agent_context(p)
                    message = (f"[Resumed session did not provide a continuable session ID. "
                               f"Starting fresh.]\n\n{message}")
                elif was_resume:
                    # Session was originally a resume that succeeded but process died.
                    # Don't try to -r the same session again — it already proved fragile.
                    # Start fresh to avoid the same failure loop.
                    print(f"[followup] {project_id}: resumed session {claude_sid[:12]} died after turn, starting fresh")
                    context = _build_agent_context(p)
                    existing['log_lines'].append(
                        f'[Resumed session process exited — restarting fresh]')
                    message = (f"[Continuing from a previous conversation (session {claude_sid}) whose "
                               f"process exited. Start fresh but continue the user's request.]\n\n{message}")
                else:
                    # Normal session — try to resume with -r
                    too_large, size_bytes = _session_too_large(pp, claude_sid)
                    if too_large:
                        size_mb = size_bytes / (1024 * 1024)
                        print(f"[followup] Session {claude_sid} is {size_mb:.1f} MB — starting fresh")
                        _log_agent_activity(project_id,
                                            f"Auto-fresh: session too large ({size_mb:.0f} MB)")
                        existing['log_lines'].append(
                            f'[Session transcript too large ({size_mb:.0f} MB) — starting fresh]')
                        context = _build_agent_context(p)
                        message = (f"[Continuing from a previous conversation that grew too large "
                                   f"to resume ({size_mb:.0f} MB). Start fresh.]\n\n{message}")
                    else:
                        resume_flags = ['-r', claude_sid]
                        print(f"[followup] {project_id}: respawning Mode B with -r {claude_sid[:12]}")

                user_label = CONFIG.get('user_name') or 'User'
                existing['log_lines'].append(f"\n> {user_label}: {message}\n")
                existing['status'] = 'running'
                existing['last_status_change_time'] = _time.time()
                existing['last_output_time'] = _time.time()
                old_proc = existing.get('proc')
                if old_proc:
                    _unregister_process(old_proc.pid)
                    try:
                        old_proc.stdin.close()
                    except Exception:
                        pass
                # Build command while under lock, spawn outside to avoid blocking
                cmd = ['claude', *resume_flags,
                       *_build_claude_flags(p, streaming=True)]
                if not resume_flags and context:
                    cmd.extend(['--append-system-prompt', context])
                _respawn_b = {
                    'cmd': cmd, 'pp': pp, 'message': message,
                    'existing': existing, 'session_id': session_id,
                    'project_id': project_id,
                    'old_proc': old_proc,
                }
                # Fall through — spawn happens after lock release
            else:
                # Process alive — write message directly to stdin
                user_label = CONFIG.get('user_name') or 'User'
                existing['log_lines'].append(f"\n> {user_label}: {message}\n")
                existing['status'] = 'running'
                existing['last_status_change_time'] = _time.time()
                existing['last_output_time'] = _time.time()

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
                        existing['last_status_change_time'] = _time.time()
                        existing['process_alive'] = False
                    finally:
                        if lock:
                            lock.release()

                threading.Thread(target=_write_stdin, daemon=True).start()
                _log_agent_activity(project_id, f"Agent follow-up: {message[:100]}")
                return jsonify({'ok': True, 'session_id': session_id})

        else:
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
            existing['last_status_change_time'] = _time.time()
            existing['last_output_time'] = _time.time()
            existing['pending_recovery_message'] = message
            user_label = CONFIG.get('user_name') or 'User'
            existing['log_lines'].append(f"\n> {user_label}: {message}\n")
            claude_sid = existing.get('claude_session_id')

    # Mode B respawn — spawn outside the lock to avoid blocking stop/other ops
    if _respawn_b:
        rb = _respawn_b
        # Kill the old process if still alive (outside lock)
        if rb.get('old_proc'):
            _kill_proc_background(rb['old_proc'])
        def _do_respawn_b():
            try:
                print(f"[respawn-B] {rb['project_id']}: spawning cmd={' '.join(rb['cmd'][:5])}...")
                proc = subprocess.Popen(
                    rb['cmd'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, cwd=rb['pp'],
                    text=True, encoding='utf-8', errors='replace',
                    creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
                )
                print(f"[respawn-B] {rb['project_id']}: spawned PID {proc.pid}")
                threading.Thread(target=_hide_windows_delayed,
                                 args=(proc.pid,), daemon=True).start()
                _register_process(proc, 'Agent respawn (B)', 'agent',
                                  rb['session_id'], rb['project_id'],
                                  rb['message'][:80])
                with get_manager(rb['project_id']).lock:
                    rb['existing']['proc'] = proc
                    rb['existing']['process_alive'] = True
                    rb['existing']['stdin_lock'] = threading.Lock()
                    rb['existing']['pending_recovery_message'] = None
                    rb['existing']['_resume_id'] = None  # clear resume context for future follow-ups

                threading.Thread(target=_read_agent_stream_b,
                                 args=(proc, rb['existing']), daemon=True).start()

                # Send message to stdin
                stdin_msg = json.dumps({
                    "type": "user",
                    "message": {"role": "user", "content": rb['message']}
                }) + '\n'
                lock = rb['existing']['stdin_lock']
                with lock:
                    proc.stdin.write(stdin_msg)
                    proc.stdin.flush()
            except Exception as e:
                print(f"[respawn-B] {rb['project_id']}: FAILED — {e}")
                rb['existing']['log_lines'].append(f'[respawn error: {e}]')
                rb['existing']['status'] = 'error'
                rb['existing']['last_status_change_time'] = _time.time()
                rb['existing']['process_alive'] = False

        threading.Thread(target=_do_respawn_b, daemon=True).start()
        _log_agent_activity(project_id, f"Agent resumed: {message[:100]}")
        return jsonify({'ok': True, 'session_id': session_id, 'resumed': True})

    # Mode A: Spawn process outside the lock to avoid blocking other requests
    def _start_followup():
        try:
            followup_msg = message
            if claude_sid:
                too_large, size_bytes = _session_too_large(pp, claude_sid)
                if too_large:
                    size_mb = size_bytes / (1024 * 1024)
                    print(f"[followup-A] Session {claude_sid} is {size_mb:.1f} MB — starting fresh")
                    _log_agent_activity(project_id,
                                        f"Auto-fresh: session too large ({size_mb:.0f} MB)")
                    with get_manager(project_id).lock:
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
            existing['pending_recovery_message'] = None
            _register_process(proc, 'Agent followup (A)', 'agent',
                              session_id, project_id, followup_msg[:80])
            threading.Thread(target=_read_agent_stream, args=(proc, existing), daemon=True).start()
        except Exception as e:
            with get_manager(project_id).lock:
                existing['log_lines'].append(f'[follow-up process failed: {e}]')
                existing['status'] = 'error'
                existing['last_status_change_time'] = _time.time()

    threading.Thread(target=_start_followup, daemon=True).start()

    _log_agent_activity(project_id, f"Agent follow-up: {message[:100]}")
    return jsonify({'ok': True, 'session_id': session_id})


def _stop_session(session, session_id):
    """Internal helper: stop a session and kill its process.
    Must be called with the project's manager lock held. Returns the proc to kill outside the lock."""
    proc = session['proc']
    session['status'] = 'stopped'
    session['last_status_change_time'] = _time.time()
    session['log_lines'].append('[Agent stopped by user]')
    # Clear any pending followups — they're stale after a user-initiated stop
    session.pop('pending_followups', None)
    session.pop('_dispatching_followup', None)
    if session.get('mode') == 'B':
        try:
            proc.stdin.close()
        except Exception:
            pass
        session['process_alive'] = False
    _unregister_process(proc.pid)
    return proc


def _kill_proc_background(proc):
    """Kill a process and its tree in a background thread."""
    def _do_kill():
        _kill_pid(proc.pid, tree=True)
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=10)
        except Exception:
            pass
    threading.Thread(target=_do_kill, daemon=True).start()


@app.route('/api/project/<project_id>/agent/stop', methods=['POST'])
def agent_stop(project_id):
    data = request.get_json() or {}
    session_id = data.get('session_id', '')
    if not session_id:
        return jsonify({'error': 'session_id required'}), 400

    # Idempotent: pressing Stop is always safe — if there's nothing to stop,
    # we return 200 with `already_stopped: true` instead of an error. This lets
    # the frontend treat the button as "ensure stopped" rather than reasoning
    # about cached status. (Phase 2 of the 2026-04-27 race consolidation.)
    proc = None
    with get_manager(project_id).lock:
        session = agent_sessions.get(session_id)
        if not session or session['project_id'] != project_id:
            return jsonify({'ok': True, 'already_stopped': True, 'reason': 'no session'})
        if session['status'] not in ('running', 'idle', 'error'):
            return jsonify({'ok': True, 'already_stopped': True, 'reason': session['status']})
        proc = _stop_session(session, session_id)

    if proc is not None:
        # Kill outside the lock — taskkill can take seconds on Windows
        _kill_proc_background(proc)
        _log_agent_activity(project_id, "Agent stopped by user")

    return jsonify({'ok': True})


@app.route('/api/project/<project_id>/agent/interrupt', methods=['POST'])
def agent_interrupt(project_id):
    """Atomic stop + immediate resume with a new prompt.
    Kills the current process and respawns with -r <session_id> in one operation.
    This avoids the broken intermediate 'stopped' state."""
    p = load_project(project_id)
    if not p:
        return jsonify({'error': 'project not found'}), 404
    pp = p.get('project_path', '')
    if not pp or not Path(pp).is_dir():
        return jsonify({'error': 'project_path not set'}), 400

    data = request.get_json() or {}
    session_id = data.get('session_id', '')
    message = data.get('message', '').strip()
    if not session_id:
        return jsonify({'error': 'session_id required'}), 400
    if not message:
        return jsonify({'error': 'message required'}), 400

    with get_manager(project_id).lock:
        session = agent_sessions.get(session_id)
        if not session or session['project_id'] != project_id:
            return jsonify({'error': 'session not found'}), 404
        if session['status'] not in ('running', 'idle', 'error'):
            return jsonify({'error': 'agent not active'}), 400

        old_proc = session['proc']
        claude_sid = session.get('claude_session_id')

        # Mark as interrupting BEFORE killing the old proc. The old reader's
        # finally block will see this flag (via _session_owned_by) and skip
        # all status / process_alive writes, eliminating the stale-status
        # flash that flipped the UI to "stopped" between kill and respawn.
        # Cleared by the respawn thread once the new proc replaces session['proc'].
        session['_interrupting'] = True

        # Stop the current process
        session['log_lines'].append('[Agent interrupted by user]')
        session.pop('pending_followups', None)
        session.pop('_dispatching_followup', None)
        session['waiting_for_plan_approval'] = False
        session['waiting_for_question'] = False
        if session.get('mode') == 'B':
            try:
                old_proc.stdin.close()
            except Exception:
                pass
        _unregister_process(old_proc.pid)

        # Immediately set status to running for the new prompt
        user_label = CONFIG.get('user_name') or 'User'
        session['log_lines'].append(f"\n> {user_label}: {message}\n")
        session['status'] = 'running'
        session['last_status_change_time'] = _time.time()
        session['last_output_time'] = _time.time()
        session['process_alive'] = True

    # Kill old process in background
    _kill_proc_background(old_proc)

    # Respawn with the new message
    is_mode_b = session.get('mode') == 'B'

    def _do_respawn():
        try:
            # Check transcript size
            resume_flags = []
            context = None
            respawn_msg = message
            if claude_sid:
                too_large, size_bytes = _session_too_large(pp, claude_sid)
                if too_large:
                    size_mb = size_bytes / (1024 * 1024)
                    session['log_lines'].append(
                        f'[Session transcript too large ({size_mb:.0f} MB) — starting fresh]')
                    context = _build_agent_context(p)
                    respawn_msg = (f"[Continuing from a previous conversation that grew too large "
                                   f"to resume ({size_mb:.0f} MB). Start fresh.]\n\n{message}")
                else:
                    resume_flags = ['-r', claude_sid]
            else:
                context = _build_agent_context(p)

            if is_mode_b:
                cmd = ['claude', *resume_flags,
                       *_build_claude_flags(p, streaming=True)]
                if not resume_flags and context:
                    cmd.extend(['--append-system-prompt', context])
                proc = subprocess.Popen(
                    cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, cwd=pp,
                    text=True, encoding='utf-8', errors='replace',
                    creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
                )
                threading.Thread(target=_hide_windows_delayed,
                                 args=(proc.pid,), daemon=True).start()
                _register_process(proc, 'Agent interrupt-resume (B)', 'agent',
                                  session_id, project_id, message[:80])
                with get_manager(project_id).lock:
                    session['proc'] = proc
                    session['process_alive'] = True
                    session['stdin_lock'] = threading.Lock()
                    # New proc is now the authoritative one — clear the
                    # interrupt gate so its reader's writes are accepted.
                    session.pop('_interrupting', None)

                threading.Thread(target=_read_agent_stream_b,
                                 args=(proc, session), daemon=True).start()

                # Send the new message
                stdin_msg = json.dumps({
                    "type": "user",
                    "message": {"role": "user", "content": respawn_msg}
                }) + '\n'
                with session['stdin_lock']:
                    proc.stdin.write(stdin_msg)
                    proc.stdin.flush()
            else:
                # Mode A
                if resume_flags:
                    cmd = ['claude', *resume_flags, '-p', respawn_msg,
                           *_build_claude_flags(p)]
                else:
                    if not context:
                        context = _build_agent_context(p)
                    cmd = ['claude', '-p', respawn_msg, *_build_claude_flags(p),
                           '--append-system-prompt', context]

                proc = subprocess.Popen(
                    cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, cwd=pp,
                    text=True, encoding='utf-8', errors='replace',
                    creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
                )
                threading.Thread(target=_hide_windows_delayed,
                                 args=(proc.pid,), daemon=True).start()
                _register_process(proc, 'Agent interrupt-resume (A)', 'agent',
                                  session_id, project_id, message[:80])
                with get_manager(project_id).lock:
                    session['proc'] = proc
                    # New proc is now authoritative — clear the interrupt gate.
                    session.pop('_interrupting', None)

                threading.Thread(target=_read_agent_stream,
                                 args=(proc, session), daemon=True).start()

        except Exception as e:
            session['log_lines'].append(f'[interrupt-resume error: {e}]')
            session['status'] = 'error'
            session['last_status_change_time'] = _time.time()
            session['process_alive'] = False
            # Clear the interrupt gate on failure too — otherwise the session
            # stays permanently gated and no future reader can update status.
            session.pop('_interrupting', None)

    threading.Thread(target=_do_respawn, daemon=True).start()

    _log_agent_activity(project_id, f"Agent interrupted: {message[:100]}")
    return jsonify({'ok': True, 'session_id': session_id})


@app.route('/api/project/<project_id>/agent/session', methods=['DELETE', 'POST'])
def agent_session_delete(project_id):
    """Kill process (if running), wait for exit, and remove session entirely.
    Accepts POST in addition to DELETE for navigator.sendBeacon compatibility."""
    data = request.get_json(force=True, silent=True) or {}
    session_id = data.get('session_id', '')
    if not session_id:
        return jsonify({'error': 'session_id required'}), 400

    proc = None
    with get_manager(project_id).lock:
        session = agent_sessions.get(session_id)
        if not session or session['project_id'] != project_id:
            return jsonify({'ok': True})  # Already gone — idempotent
        if session['status'] in ('running', 'idle'):
            proc = session['proc']
            session['status'] = 'stopped'
            session['last_status_change_time'] = _time.time()
            session['log_lines'].append('[Agent stopped — tab closed]')
            if session.get('mode') == 'B':
                try:
                    proc.stdin.close()
                except Exception:
                    pass
                session['process_alive'] = False
            _kill_pid(proc.pid, tree=True)
            try:
                proc.kill()
            except Exception:
                pass
            _unregister_process(proc.pid)

    # Wait outside lock for process to fully exit
    if proc:
        try:
            proc.wait(timeout=5)
        except Exception:
            pass

    # Remove session from tracking.
    # The stream reader thread has already called _log_agent_completion()
    # in its finally block after proc.wait(), so usage data is persisted.
    mgr = get_manager(project_id)
    with mgr.lock:
        agent_sessions.pop(session_id, None)
        mgr.session_ids.discard(session_id)

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
                'process_alive': s.get('process_alive', False) if s.get('mode') == 'B' else (s['status'] in ('running',)),
                'hivemind_id': s.get('hivemind_id', ''),
                'hivemind_ws_id': s.get('hivemind_ws_id', ''),
                'hivemind_role': s.get('hivemind_role', ''),
                'trigger_type': s.get('trigger_type', 'manual'),
                'trigger_id': s.get('trigger_id', ''),
                'waiting_for_plan_approval': s.get('waiting_for_plan_approval', False),
                'guardian_state': s.get('guardian_state'),
                'circuit_breaker_tripped': s.get('circuit_breaker_tripped', False),
                'incognito': s.get('incognito', False),
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


@app.route('/api/project/<project_id>/agent/guardian-reset', methods=['POST'])
def agent_guardian_reset(project_id):
    data = request.get_json() or {}
    session_id = data.get('session_id', '')
    action = data.get('action', 'retry')
    if not session_id:
        return jsonify({'error': 'session_id required'}), 400
    retry_message = None
    with get_manager(project_id).lock:
        session = agent_sessions.get(session_id)
        if not session or session['project_id'] != project_id:
            return jsonify({'error': 'session not found'}), 404
        if action == 'retry':
            session['circuit_breaker_tripped'] = False
            session['recovery_attempts'] = 0
            session['guardian_state'] = 'recovering'
            session['log_lines'].append('[Guardian: retry requested by user]')
            retry_message = session.get('pending_recovery_message')
            if not retry_message:
                retry_message = 'Continue where you left off.'
                session['pending_recovery_message'] = retry_message
        elif action == 'dismiss':
            session['guardian_state'] = None
            session['pending_recovery_message'] = None

    if retry_message:
        threading.Thread(
            target=_guardian_attempt_recovery,
            args=(session,), daemon=True).start()

    return jsonify({'ok': True})


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

    # Notify any active agent SSE streams for this project (only this project's sessions)
    mgr = get_manager(project_id)
    with mgr.lock:
        for sid in list(mgr.session_ids):
            asess = agent_sessions.get(sid)
            if asess and asess['status'] in ('running', 'idle'):
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
    # Verify PID is actually running (warn but still register — process may have exited quickly)
    alive = _pid_is_alive(pid)
    if not alive:
        print(f"[process-register] Warning: PID {pid} not detected as alive, registering anyway")
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


# ── Hivemind: Persistent Multi-Agent Collaborative Intelligence ──────────────
# Phase 1 — data model, CRUD, message bus, findings, knowledge base, SSE events,
#            server orchestrator (dependency resolver + worker scheduler)

HIVEMIND_DIR = _DATA_ROOT / 'data' / 'hiveminds'
HIVEMIND_DIR.mkdir(parents=True, exist_ok=True)

# Global state
_hivemind_sessions = {}           # hivemind_id → {status, worker_sessions, ...}
_hivemind_lock = threading.Lock()
_hivemind_sse_queues = {}         # hivemind_id → [queue, queue, ...] for SSE fan-out
_hivemind_sse_lock = threading.Lock()


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
        print(f"[hivemind-reconcile] failed: {e}")
        return
    if transitioned:
        print(f"[hivemind-reconcile] marked {transitioned} long-active hivemind(s) as 'stale' (>{_HM_STALE_HOURS}h idle)")


# ── Hivemind API: Management ─────────────────────────────────────────────────

@app.route('/api/hivemind/create', methods=['POST'])
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

    # Auto-dispatch orchestrator for goal decomposition (if workstreams not provided)
    if not data.get('workstreams'):
        _hm_dispatch_orchestrator(hivemind_id, 'decompose')

    return jsonify({'ok': True, 'hivemind': manifest})


@app.route('/api/hivemind/list')
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


@app.route('/api/hivemind/<hivemind_id>')
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


@app.route('/api/hivemind/<hivemind_id>', methods=['PUT'])
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


@app.route('/api/hivemind/<hivemind_id>/start', methods=['POST'])
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


@app.route('/api/hivemind/<hivemind_id>/pause', methods=['POST'])
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


@app.route('/api/hivemind/<hivemind_id>/stop', methods=['POST'])
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


@app.route('/api/hivemind/<hivemind_id>', methods=['DELETE'])
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

@app.route('/api/hivemind/<hivemind_id>/workstreams')
def hivemind_workstreams_list(hivemind_id):
    """List all workstreams for a hivemind."""
    manifest = _hm_load_manifest(hivemind_id)
    if not manifest:
        return jsonify({'error': 'not found'}), 404
    workstreams = _hm_list_workstreams(hivemind_id)
    return jsonify(workstreams)


@app.route('/api/hivemind/<hivemind_id>/workstreams/create', methods=['POST'])
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


@app.route('/api/hivemind/<hivemind_id>/workstreams/<ws_id>', methods=['PUT'])
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


@app.route('/api/hivemind/<hivemind_id>/workstreams/<ws_id>/status', methods=['POST'])
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

_hivemind_orchestrating = set()  # hivemind_ids currently running orchestrator CLI sessions
_hivemind_orch_lock = threading.Lock()


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

    return "\n\n".join(parts)


@app.route('/api/hivemind/<hivemind_id>/workstreams/<ws_id>/spawn', methods=['POST'])
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

    # Build worker context
    worker_context = _hm_build_worker_context(hivemind_id, ws_id)

    # Determine model: workstream override > manifest config > global config
    model = ws.get('model', '') or manifest.get('config', {}).get('worker_model', '') or CONFIG.get('agent_model', '')

    task = (
        f"You are a Hivemind worker for workstream: {ws.get('title', ws_id)}.\n"
        f"Brief: {ws.get('description', '')}\n\n"
        f"Begin your analysis. Follow the two-phase protocol described in your system prompt."
    )

    # Build command — Mode A (spawn-per-turn) for workers
    cmd = ['claude', '-p', task, '--print', '--verbose', '--output-format', 'stream-json',
           '--dangerously-skip-permissions', '--append-system-prompt', worker_context]
    if model:
        cmd.extend(['--model', model])
    max_turns = manifest.get('config', {}).get('worker_max_turns', 0) or CONFIG.get('agent_max_turns', 0)
    if max_turns and int(max_turns) > 0:
        cmd.extend(['--max-turns', str(int(max_turns))])

    session_id = f'hm_{uuid.uuid4().hex[:8]}'

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
        _register_process(proc, f'Hivemind Worker ({ws.get("title", ws_id)[:30]})', 'hivemind_worker',
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
            'housekeeping': True,  # prevent MEMORY.md writes — hivemind workers use bus only
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

        t = threading.Thread(target=_read_agent_stream, args=(proc, session), daemon=True)
        t.start()

        # Update workstream status
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


@app.route('/api/hivemind/<hivemind_id>/workstreams/<ws_id>/handoff', methods=['POST'])
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
    cmd = ['claude', '-p', prompt, '--model', model, '--max-turns', '5',
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
            print(f"[hivemind-orchestrator-cli] error: {e}")
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

        worker_context = _hm_build_worker_context(hivemind_id, ws_id)
        model = ws.get('model', '') or manifest.get('config', {}).get('worker_model', '') or CONFIG.get('agent_model', '')

        task = (
            f"You are a Hivemind worker for workstream: {ws.get('title', ws_id)}.\n"
            f"Brief: {ws.get('description', '')}\n\n"
            f"Begin your analysis. Follow the two-phase protocol described in your system prompt."
        )

        cmd = ['claude', '-p', task, '--print', '--verbose', '--output-format', 'stream-json',
               '--dangerously-skip-permissions', '--append-system-prompt', worker_context]
        if model:
            cmd.extend(['--model', model])

        session_id = f'hm_{uuid.uuid4().hex[:8]}'
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
            _register_process(proc, f'Hivemind Worker ({ws.get("title", ws_id)[:30]})', 'hivemind_worker',
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
                'housekeeping': True,
                'hivemind_id': hivemind_id,
                'hivemind_ws_id': ws_id,
            }
            mgr = get_manager(project_id)
            mgr.ensure_guardian()
            with mgr.lock:
                agent_sessions[session_id] = session
                mgr.session_ids.add(session_id)

            t = threading.Thread(target=_read_agent_stream, args=(proc, session), daemon=True)
            t.start()

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
            print(f"[hivemind] Failed to spawn worker for {ws_id}: {e}")


# ── Hivemind API: Message Bus ────────────────────────────────────────────────

@app.route('/api/hivemind/<hivemind_id>/bus/post', methods=['POST'])
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


@app.route('/api/hivemind/<hivemind_id>/bus/poll/<ws_id>')
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


@app.route('/api/hivemind/<hivemind_id>/bus/history')
def hivemind_bus_history(hivemind_id):
    """Get full message bus history (paginated)."""
    manifest = _hm_load_manifest(hivemind_id)
    if not manifest:
        return jsonify({'error': 'not found'}), 404
    limit = int(request.args.get('limit', 100))
    messages = _hm_read_bus_messages(hivemind_id, last_n=limit)
    return jsonify(messages)


@app.route('/api/hivemind/<hivemind_id>/bus/stream')
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

@app.route('/api/hivemind/<hivemind_id>/knowledge/synthesis')
def hivemind_knowledge_synthesis_get(hivemind_id):
    """Get the current synthesis document."""
    manifest = _hm_load_manifest(hivemind_id)
    if not manifest:
        return jsonify({'error': 'not found'}), 404
    content = _hm_read_synthesis(hivemind_id)
    return jsonify({'content': content, 'updated_at': manifest.get('updated_at')})


@app.route('/api/hivemind/<hivemind_id>/knowledge/synthesis', methods=['PUT'])
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


@app.route('/api/hivemind/<hivemind_id>/knowledge/decisions')
def hivemind_knowledge_decisions(hivemind_id):
    """Get all decisions."""
    manifest = _hm_load_manifest(hivemind_id)
    if not manifest:
        return jsonify({'error': 'not found'}), 404
    return jsonify(_hm_read_decisions(hivemind_id))


@app.route('/api/hivemind/<hivemind_id>/knowledge/findings')
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


@app.route('/api/hivemind/<hivemind_id>/knowledge/questions/<question_id>/resolve', methods=['POST'])
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

@app.route('/api/hivemind/<hivemind_id>/escalate', methods=['POST'])
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


@app.route('/api/hivemind/<hivemind_id>/intervene', methods=['POST'])
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


@app.route('/api/hivemind/<hivemind_id>/findings/<finding_id>/review', methods=['POST'])
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


@app.route('/api/hivemind/<hivemind_id>/decisions/<decision_id>/approve', methods=['POST'])
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

_hivemind_orchestrator_stop = threading.Event()


def _hivemind_orchestrator_loop():
    """Background daemon: evaluate hivemind states, resolve dependencies,
    and schedule worker spawns. Runs every 10 seconds."""
    while not _hivemind_orchestrator_stop.is_set():
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
            print(f"[hivemind-orchestrator] Error: {e}")

        _hivemind_orchestrator_stop.wait(10)


def _start_hivemind_orchestrator():
    """Start the hivemind orchestrator background thread."""
    t = threading.Thread(target=_hivemind_orchestrator_loop, daemon=True)
    t.start()


# ── Agent log endpoint ────────────────────────────────────────────────────────

@app.route('/api/project/<project_id>/agent/log')
def get_agent_log(project_id):
    log = _load_agent_log(project_id)
    for entry in log:
        entry['ts_relative'] = time_ago(entry.get('ts'))
        entry['started_relative'] = time_ago(entry.get('started_at'))
    return jsonify(log)


def _enrich_run_entries(entries):
    """Add ts_relative + started_relative for FE display, in place."""
    for e in entries:
        e['ts_relative'] = time_ago(e.get('ts'))
        e['started_relative'] = time_ago(e.get('started_at'))
    return entries


@app.route('/api/project/<project_id>/transcript/<claude_session_id>')
def get_project_transcript(project_id, claude_session_id):
    """Return parsed transcript for read-only display in the Runs panel viewer."""
    p = load_project(project_id)
    if not p:
        return jsonify({'error': 'project not found'}), 404
    f = _find_transcript_file(p.get('project_path', ''), claude_session_id)
    if not f:
        return jsonify({'error': 'transcript not found'}), 404
    try:
        size = f.stat().st_size
    except OSError:
        size = 0
    messages = _parse_transcript_messages(f)
    return jsonify({
        'csid': claude_session_id,
        'size': size,
        'message_count': len(messages),
        'messages': messages,
    })


@app.route('/api/schedule/<schedule_id>/run-now', methods=['POST'])
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
    try:
        sid = _dispatch_agent_internal(pid, task,
                                       trigger_type='schedule',
                                       trigger_id=schedule_id)
    except ValueError as e:
        code = 404 if 'not found' in str(e) else 400
        return jsonify({'error': str(e)}), code
    except FileNotFoundError:
        return jsonify({'error': 'Claude CLI not found'}), 500
    except Exception as e:
        return jsonify({'error': f'dispatch failed: {e}'}), 500
    sched['last_run'] = now_iso()
    _save_schedules(schedules)
    return jsonify({'ok': True, 'session_id': sid})


@app.route('/api/schedule/<schedule_id>/runs')
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


@app.route('/api/hivemind/<hivemind_id>/runs')
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


@app.route('/api/project/<project_id>/conversations')
def get_project_conversations(project_id):
    """Return recent Claude Code conversations for a project, read from .jsonl transcripts.

    Survives server reboots, captures interrupted / mid-flight sessions that never
    landed in the agent completion log. Enriched with live status + completion-log
    status, and label defaults to the user's LAST message.
    """
    try:
        limit = int(request.args.get('limit', 10))
    except Exception:
        limit = 10
    limit = max(1, min(limit, 50))

    p = load_project(project_id)
    if not p:
        return jsonify([])
    project_path = p.get('project_path', '')
    convos = _recent_claude_transcripts(project_path, limit=limit)

    live_by_csid = {}
    for s in agent_sessions.values():
        if s.get('project_id') != project_id:
            continue
        csid = s.get('claude_session_id', '')
        if csid:
            live_by_csid[csid] = {
                'status': s.get('status', 'unknown'),
                'session_id': s.get('session_id', ''),
                'task': s.get('task', ''),
            }

    log_by_csid = {}
    for e in _load_agent_log(project_id):
        csid = e.get('claude_session_id', '')
        if csid and csid not in log_by_csid:
            log_by_csid[csid] = e

    from datetime import datetime, timezone
    out = []
    for c in convos:
        sid = c['session_id']
        live = live_by_csid.get(sid)
        log_entry = log_by_csid.get(sid, {})
        if live:
            status = live['status']
            mc_session_id = live.get('session_id', '')
        elif log_entry:
            status = log_entry.get('status', 'completed')
            mc_session_id = log_entry.get('session_id', '')
        else:
            status = 'interrupted' if c['turns'] > 0 else 'empty'
            mc_session_id = ''

        label = c['last_user'] or c['first_user'] or '(empty)'
        label = ' '.join(label.split())

        try:
            ts_iso = datetime.fromtimestamp(c['mtime'], tz=timezone.utc).isoformat()
        except Exception:
            ts_iso = ''
        out.append({
            'claude_session_id': sid,
            'mc_session_id': mc_session_id,
            'status': status,
            'label': label,
            'first_user': c['first_user'],
            'last_user': c['last_user'],
            'turns': c['turns'],
            'size': c['size'],
            'mtime': c['mtime'],
            'ts': ts_iso,
            'ts_relative': time_ago(ts_iso) if ts_iso else '',
            'live': bool(live),
        })
    return jsonify(out)


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



# ── Global config endpoints ────────────────────────────────────────────────

_CONFIG_EDITABLE_KEYS = {
    'user_name', 'agent_name', 'agent_model', 'agent_max_turns',
    'agent_permission_mode', 'agent_channels', 'agent_remote_control',
    'use_streaming_agent', 'condense_enabled', 'condense_threshold_kb',
    'condense_model', 'projects_base', 'shared_rules_path', 'port',
}

@app.route('/api/config')
def get_config():
    """Return all editable config keys."""
    return jsonify({k: CONFIG.get(k) for k in _CONFIG_EDITABLE_KEYS})

@app.route('/api/config', methods=['PUT'])
def update_config():
    """Update config keys and persist to config.json."""
    data = request.get_json() or {}
    updated = {}
    for k, v in data.items():
        if k in _CONFIG_EDITABLE_KEYS:
            CONFIG[k] = v
            updated[k] = v
    if updated:
        try:
            with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(CONFIG, f, indent=2, ensure_ascii=False)
        except Exception as e:
            return jsonify({'error': f'failed to save config: {e}'}), 500
    return jsonify({'ok': True, 'updated': list(updated.keys())})


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
                            sid = _dispatch_agent_internal(pid, task,
                                                          trigger_type='schedule',
                                                          trigger_id=sched.get('id', ''))
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
                                if ts < cutoff:
                                    stale.append(sid)
                            except Exception:
                                stale.append(sid)
                    for sid in stale:
                        agent_sessions.pop(sid, None)
                        mgr.session_ids.discard(sid)
                    total_stale += len(stale)
            if total_stale:
                print(f"[scheduler] Purged {total_stale} stale agent session(s)")
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
    index_path = Path(STATIC_DIR) / 'index.html'
    etag = None
    if index_path.exists():
        stat = index_path.stat()
        etag = f'"{int(stat.st_mtime)}-{stat.st_size}"'
    # Conditional GET — let WebView2 cache but always revalidate
    if etag and request.headers.get('If-None-Match') == etag:
        return Response(status=304, headers={'ETag': etag, 'Cache-Control': 'no-cache'})
    resp = send_from_directory(STATIC_DIR, 'index.html')
    resp.headers['Cache-Control'] = 'no-cache'  # cache OK, but must revalidate
    resp.headers['Pragma'] = 'no-cache'
    if etag:
        resp.headers['ETag'] = etag
    return resp


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
atexit.register(_hivemind_orchestrator_stop.set)


# ── Session Guardian ─────────────────────────────────────────────────────────
# Replaces the old health monitor. Detects stuck sessions and auto-recovers
# them with exponential backoff, without discarding session context.

_guardian_stop = threading.Event()
GUARDIAN_CHECK_INTERVAL = 10
# Hung threshold: 10 minutes of *both* no stdout and no CPU progress.
# Claude can legitimately go silent for several minutes during long thinking,
# context loads, or tool calls — so we require CPU idleness as confirmation.
GUARDIAN_HUNG_TIMEOUT = 600
GUARDIAN_STUCK_FLAG_TIMEOUT = 120
GUARDIAN_MAX_RECOVERIES = 3
GUARDIAN_BACKOFF_BASE = 5


def _proc_is_cpu_idle(session, proc, now):
    """Return True if the process appears CPU-idle (i.e. truly hung, not thinking).

    Compares cpu_times() across calls. If cpu time hasn't advanced by at least
    0.5s since the previous sample, treat as idle. First sample always returns
    False (not enough data — give the process the benefit of the doubt).
    """
    try:
        import psutil
    except ImportError:
        # Without psutil we cannot distinguish "thinking" from "hung". The safe
        # default is to NEVER auto-kill on silence — return False so the State 2
        # guardian skips the kill. The user can install psutil to enable it, or
        # manually stop a truly hung agent. Dead-process detection (State 1)
        # still works without psutil.
        return False
    try:
        p = psutil.Process(proc.pid)
        cpu = p.cpu_times()
        cur_total = cpu.user + cpu.system
        # Walk children too — Claude CLI spawns subprocesses
        for child in p.children(recursive=True):
            try:
                cc = child.cpu_times()
                cur_total += cc.user + cc.system
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return True  # process is gone — let the dead-process check handle it
    prev_total = session.get('_guardian_prev_cpu_total')
    prev_time = session.get('_guardian_prev_cpu_time', now)
    session['_guardian_prev_cpu_total'] = cur_total
    session['_guardian_prev_cpu_time'] = now
    if prev_total is None:
        return False  # first sample — wait for next tick before declaring idle
    delta_cpu = cur_total - prev_total
    delta_wall = max(0.001, now - prev_time)
    # If the process burned less than 0.5s of CPU per ~10s of wall time, call it idle
    return (delta_cpu / delta_wall) < 0.05


def _guardian_should_recover(session):
    if session.get('circuit_breaker_tripped'):
        return False
    attempts = session.get('recovery_attempts', 0)
    if attempts >= GUARDIAN_MAX_RECOVERIES:
        session['circuit_breaker_tripped'] = True
        session['guardian_state'] = 'needs_attention'
        session['log_lines'].append(
            f'[Guardian: recovery exhausted after {attempts} attempts. '
            f'Use "Try Again" or "Start Fresh".]')
        return False
    last = session.get('last_recovery_time', 0)
    backoff = GUARDIAN_BACKOFF_BASE * (2 ** attempts)
    if _time.time() - last < backoff:
        return False
    return True


def _guardian_attempt_recovery(session):
    if not _guardian_should_recover(session):
        if session.get('guardian_state') == 'recovering':
            session['guardian_state'] = 'needs_attention'
        return
    message = session.get('pending_recovery_message')
    if not message:
        if session.get('guardian_state') == 'recovering':
            session['guardian_state'] = None
        return

    session['recovery_attempts'] = session.get('recovery_attempts', 0) + 1
    session['last_recovery_time'] = _time.time()
    session['guardian_state'] = 'recovering'
    session['log_lines'].append(
        f'[Guardian: recovery attempt {session["recovery_attempts"]}/{GUARDIAN_MAX_RECOVERIES}]')

    proc = session.get('proc')
    if proc:
        _kill_proc_background(proc)
        _time.sleep(2)

    _auto_dispatch_followup(session, message)

    with get_manager(session['project_id']).lock:
        if session['status'] == 'running':
            session['guardian_state'] = None
            session['pending_recovery_message'] = None
        else:
            if session.get('recovery_attempts', 0) >= GUARDIAN_MAX_RECOVERIES:
                session['circuit_breaker_tripped'] = True
                session['guardian_state'] = 'needs_attention'
            else:
                session['guardian_state'] = None


def _session_guardian_loop():
    """Removed — per-project guardians (ProjectAgentManager.ensure_guardian) now
    own session checking. This stub is kept only so _start_session_guardian()
    doesn't break older callers."""
    return


def _guardian_check_session(sid, session, now):
    status = session['status']
    proc = session.get('proc')
    mode = session.get('mode', 'A')
    last_output = session.get('last_output_time', now)
    last_change = session.get('last_status_change_time', now)

    if session.get('guardian_state') == 'recovering':
        return

    # State 7: stuck 'running' with no/dead process (Popen failure)
    if status == 'running' and now - last_change > 15:
        proc_dead = proc is None or proc.poll() is not None
        if proc_dead:
            print(f"[guardian] Session {sid[:8]}: stuck running, process dead/missing")
            with get_manager(session['project_id']).lock:
                session['status'] = 'error'
                session['last_status_change_time'] = now
                if mode == 'B':
                    session['process_alive'] = False
                session['log_lines'].append(
                    '[Guardian: process dead but status was running — recovered]')
            if session.get('pending_recovery_message'):
                _guardian_attempt_recovery(session)
            return

    # State 1: dead process, stale status (running/idle)
    if status in ('running', 'idle') and proc and now - last_change > 2:
        if proc.poll() is not None or not _pid_is_alive(proc.pid):
            # Safety net: if the session is waiting for user input (question /
            # plan approval), the process was killed intentionally by the reader
            # thread as part of that flow. Don't mark it 'error' — the follow-up
            # (user's answer) will respawn it.
            if session.get('waiting_for_question') or session.get('waiting_for_plan_approval'):
                return
            old_status = status
            print(f"[guardian] Session {sid[:8]}: PID {proc.pid} dead, was {old_status}")
            with get_manager(session['project_id']).lock:
                if mode == 'B':
                    session['process_alive'] = False
                if session['status'] in ('running', 'idle'):
                    session['status'] = 'error'
                    session['last_status_change_time'] = now
                    session['log_lines'].append(
                        f'[Guardian: process {proc.pid} found dead]')
            if session.get('pending_recovery_message'):
                _guardian_attempt_recovery(session)
            return

    # State 2: hung process (alive, no output for GUARDIAN_HUNG_TIMEOUT seconds AND no CPU progress)
    if status == 'running' and proc and proc.poll() is None:
        silent_secs = now - last_output
        if silent_secs > GUARDIAN_HUNG_TIMEOUT and _proc_is_cpu_idle(session, proc, now):
            print(f"[guardian] Session {sid[:8]}: no output for {silent_secs:.0f}s, killing")
            with get_manager(session['project_id']).lock:
                session['log_lines'].append(
                    f'[Guardian: no output for {silent_secs:.0f}s — killing hung process]')
                session['guardian_state'] = 'needs_attention'
            # Snapshot pid; release lock before kill (process-tree walk can be slow on Windows)
            _kill_proc_background(proc)
            return

    proj_lock = get_manager(session['project_id']).lock

    # State 3: stuck gate flags (approval/question)
    if session.get('waiting_for_plan_approval') and now - last_change > GUARDIAN_STUCK_FLAG_TIMEOUT:
        last_sse = session.get('_last_sse_poll_time', 0)
        if now - last_sse > 60:
            with proj_lock:
                session['log_lines'].append(
                    '[Guardian: plan approval may have been missed — re-check session]')
                session['guardian_state'] = 'needs_attention'

    if session.get('waiting_for_question') and now - last_change > GUARDIAN_STUCK_FLAG_TIMEOUT:
        last_sse = session.get('_last_sse_poll_time', 0)
        if now - last_sse > 60:
            with proj_lock:
                session['log_lines'].append(
                    '[Guardian: question may have been missed — re-check session]')
                session['guardian_state'] = 'needs_attention'

    # State 5: stuck _dispatching_followup flag
    if session.get('_dispatching_followup') and status != 'running':
        if now - last_change > 30:
            with proj_lock:
                session.pop('_dispatching_followup', None)
                session['log_lines'].append(
                    '[Guardian: cleared stuck dispatching flag]')

    # State 4: stuck pending_followups queue
    pending = session.get('pending_followups', [])
    if pending and status != 'running' and not session.get('_dispatching_followup'):
        if now - last_change > 30:
            with proj_lock:
                msg = pending.pop(0)
                session['log_lines'].append(
                    f'[Guardian: dispatching stuck follow-up]')
            _auto_dispatch_followup(session, msg)

    # State 6: error session with pending recovery message — retry or trip breaker
    if status == 'error' and session.get('pending_recovery_message'):
        attempts = session.get('recovery_attempts', 0)
        last_recovery = session.get('last_recovery_time', 0)
        if attempts >= 2 and now - last_recovery < 60:
            if not session.get('circuit_breaker_tripped'):
                with proj_lock:
                    session['circuit_breaker_tripped'] = True
                    session['guardian_state'] = 'needs_attention'
                    session['log_lines'].append(
                        f'[Guardian: {attempts} rapid failures detected — '
                        f'auto-recovery disabled]')
        elif now - last_change > 10:
            _guardian_attempt_recovery(session)


def _start_session_guardian():
    """No-op: per-project guardians spawn lazily on first dispatch via
    ProjectAgentManager.ensure_guardian(). Kept for callers in startup code."""
    return None


atexit.register(_guardian_stop.set)


def _check_port_conflict():
    """Refuse to start if another MC is already on our port.

    This used to be a non-fatal warning. It's now fatal because two MCs
    sharing a port (which Windows allows in some socket configurations)
    leads to traffic splitting between two `agent_sessions` dicts —
    requests look like they "migrate" between instances and killing one
    instance kills agents the other doesn't know about.

    Bypass: set MC_ALLOW_PORT_CONFLICT=1 if you genuinely need two MCs
    competing for the port (rare; almost always a misconfiguration).

    Restart-aware bypass: if MC_RESTART_FROM_PID is set, this is the new
    instance from a `/api/system/restart` re-exec. On Windows, os.execv
    actually spawns a new process and exits the old one, so the old
    process briefly still holds the port. Wait up to 15s for it to release
    before declaring a true conflict.
    """
    import socket
    def _try_bind():
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(('0.0.0.0', PORT))
            s.close()
            return True
        except OSError:
            try: s.close()
            except Exception: pass
            return False

    if _try_bind():
        return  # Clean — port is free.

    # Restart re-exec window: the parent we just replaced may still be releasing
    # the socket. Poll briefly before treating this as a real conflict.
    restart_parent = os.environ.get('MC_RESTART_FROM_PID', '')
    if restart_parent:
        deadline = _time.time() + 15.0
        while _time.time() < deadline:
            _time.sleep(0.3)
            if _try_bind():
                # Clean — clear the marker so a subsequent restart starts fresh
                # and doesn't inherit a stale value.
                os.environ.pop('MC_RESTART_FROM_PID', None)
                print(f"[port-conflict] dying parent (PID {restart_parent}) released port {PORT}; continuing.", flush=True)
                return
        print(f"[port-conflict] waited 15s for parent PID {restart_parent} to release port {PORT}; falling through to conflict check.", flush=True)

    other_pids: list[str] = []
    pid_details: dict[str, str] = {}
    # TODO(linux/macos): when MC runs on POSIX, add equivalent diagnostic
    # branches so the conflict message names what's holding the port:
    #   Linux  → `ss -lntp 'sport = :<PORT>'`  (parses users:(("name",pid=N,...)))
    #   macOS  → `lsof -i :<PORT> -P -n -sTCP:LISTEN`  (image name in column 1, PID in column 2)
    # The restart flow itself already works on POSIX (close_fds + start_new_session),
    # so this is purely UX — without it the abort message just says "port in use"
    # with no PID list. Not urgent; only matters when the wait-15s bypass fails.
    if sys.platform == 'win32':
        try:
            result = subprocess.run(
                ['netstat', '-ano'], capture_output=True, text=True, timeout=5)
            pids = set()
            for line in result.stdout.splitlines():
                if f':{PORT}' in line and 'LISTENING' in line:
                    parts = line.split()
                    if parts:
                        pids.add(parts[-1])
            my_pid = str(os.getpid())
            other_pids = sorted(pids - {my_pid})
            # Identify each holder by image name + parent PID. Helps tell
            # whether we're fighting an orphan child process (e.g. claude.exe
            # that inherited our socket FD) vs an unrelated MC instance.
            for pid in other_pids:
                try:
                    out = subprocess.run(
                        ['tasklist', '/FI', f'PID eq {pid}', '/FO', 'CSV', '/NH'],
                        capture_output=True, text=True, timeout=5)
                    line = out.stdout.strip().splitlines()[0] if out.stdout.strip() else ''
                    if line and ',' in line:
                        # CSV: "image","pid","sessionname","session#","memusage"
                        image = line.split(',')[0].strip().strip('"')
                        pid_details[pid] = image
                except Exception:
                    pass
        except Exception:
            pass

    msg_lines = [
        "",
        "=" * 72,
        f"  Clayrune cannot start: port {PORT} is already in use.",
        "=" * 72,
    ]
    if other_pids:
        if pid_details:
            described = [f"{p} ({pid_details.get(p, '?')})" for p in other_pids]
            msg_lines.append(f"  Held by PID(s): {', '.join(described)}")
        else:
            msg_lines.append(f"  Held by PID(s): {', '.join(other_pids)}")
    msg_lines += [
        "",
        "  Another MC is likely already running (e.g. via Tauri).",
        "  Running two MCs at once causes traffic to split between them,",
        "  duplicates agent sessions, and produces 'unrecoverable error'",
        "  conditions when one instance shuts down.",
        "",
        "  To fix:",
        f"    1. Stop the other MC first, or",
        f"    2. Use the already-running instance directly, or",
        f"    3. Set MC_ALLOW_PORT_CONFLICT=1 if you really need both",
        f"       (rare; only meaningful for protocol-level testing).",
        "=" * 72,
        "",
    ]
    print('\n'.join(msg_lines), flush=True)

    # Forensic log
    try:
        from datetime import datetime
        log_path = Path(_DATA_ROOT) / 'port_conflict.log'
        with open(log_path, 'a', encoding='utf-8') as f:
            f.write(f"{datetime.utcnow().isoformat()}Z  PID {os.getpid()} aborting, "
                    f"port {PORT} held by PID(s) {','.join(other_pids) or 'unknown'}  "
                    f"cmdline: {' '.join(sys.argv)}\n")
    except Exception:
        pass

    if os.environ.get('MC_ALLOW_PORT_CONFLICT') == '1':
        print(f"[port-conflict] MC_ALLOW_PORT_CONFLICT=1 set — proceeding ANYWAY. "
              f"You will likely see traffic split between instances.", flush=True)
        return

    sys.exit(2)


# ─────────────────────────────────────────────────────────────────────────────
# Local mock control plane (DEV ONLY)
# ─────────────────────────────────────────────────────────────────────────────
# When MC_REMOTE_LOCAL_MOCK=1 is set, MC routes /api/_mock/connect as if it
# were the real PLATFORM_DOMAIN/connect endpoint: pretends Firebase signin
# succeeded, synthesizes plausible enrollment_token / device_id / hostname,
# and bounces back to /api/mc-callback. Lets the entire Enable -> browser ->
# callback -> enrolled flow be exercised before the real GCP control plane
# exists.
#
# To use:
#   1. Set env: MC_REMOTE_LOCAL_MOCK=1
#   2. Set env: MC_REMOTE_PLATFORM_DOMAIN=127.0.0.1:5199 (so connect URL points local)
#      (Note: connect_url() builds https://; for the local mock we deliberately
#       generate a plain http URL via the dedicated mock helper below.)
#
# This block only registers when the flag is set. Production builds with the
# flag unset have no mock endpoints.

if os.environ.get('MC_REMOTE_LOCAL_MOCK') == '1':
    # In-memory state for the mock CP
    _mock_nonces: dict = {}        # nonce_id -> { nonce, expires_at, device_id }
    _mock_devices: dict = {}       # device_id -> { device_pub_b64, hostname, username }
    _mock_lock = threading.Lock()

    def _mock_now_iso(offset_s: float = 0.0) -> str:
        from datetime import datetime, timezone, timedelta
        return (datetime.now(timezone.utc) + timedelta(seconds=offset_s)) \
            .isoformat(timespec='seconds').replace('+00:00', 'Z')

    @app.route('/v1/nonce')
    def _mock_v1_nonce():
        """Mock CP nonce endpoint (matches `03-` §3.6)."""
        device_id = request.args.get('device_id', '').strip()
        if not device_id:
            return jsonify({'code': 'bad_envelope', 'message': 'device_id required',
                            'request_id': 'mock'}), 400
        nonce_id = secrets.token_urlsafe(16)
        nonce = secrets.token_urlsafe(32)
        with _mock_lock:
            _mock_nonces[nonce_id] = {
                'nonce': nonce,
                'expires_at': _time.time() + 30,
                'device_id': device_id,
                'used': False,
            }
        return jsonify({
            'nonce': nonce,
            'nonce_id': nonce_id,
            'expires_at': _mock_now_iso(30),
        })

    @app.route('/v1/attest', methods=['POST'])
    def _mock_v1_attest():
        """Mock CP attest endpoint. Verifies BOTH signatures before issuing
        a (fake) tunnel token. Implements a subset of the 14+1 verification
        steps from `02-` §7.4 — enough to exercise the client end-to-end."""
        import base64 as _b64
        import hashlib as _hashlib
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            from cryptography.exceptions import InvalidSignature
            import rfc8785
        except Exception as e:
            return jsonify({'code': 'internal_error', 'message': f'mock missing dep: {e}',
                            'request_id': 'mock'}), 500

        body = request.get_json(silent=True) or {}
        env = body.get('envelope') or {}
        canon_hash_hex = body.get('envelope_canonical_sha256', '')
        sig_b64 = body.get('signature_b64', '')
        client_sig_b64 = body.get('client_signature_b64', '')

        if not env or not canon_hash_hex or not sig_b64 or not client_sig_b64:
            return _mock_attest_err('bad_envelope', 400, "Missing envelope fields")

        # Step 2: recompute canonical-JSON sha256
        try:
            recomputed = _hashlib.sha256(rfc8785.dumps(env)).hexdigest()
        except Exception as e:
            return _mock_attest_err('bad_canonicalization', 400, f"JCS dump failed: {e}")
        if recomputed != canon_hash_hex:
            return _mock_attest_err('bad_canonicalization', 400,
                                    f"Hash mismatch: client={canon_hash_hex} server={recomputed}")

        envelope_hash_bytes = bytes.fromhex(canon_hash_hex)

        # Step 4: device signature verifies
        try:
            device_pub_raw = _b64.b64decode(env.get('device_pub_b64', ''))
            Ed25519PublicKey.from_public_bytes(device_pub_raw).verify(
                _b64.b64decode(sig_b64), envelope_hash_bytes,
            )
        except (InvalidSignature, ValueError) as e:
            return _mock_attest_err('bad_signature', 401, f"Device sig invalid: {e}")

        # Step 4.5: client signature verifies under the registered key
        try:
            from mc_remote import attestation as _att
            expected_key_id = _att.dev_client_secret_key_id()
            expected_pub_b64 = _att.dev_client_pubkey_b64()
        except Exception as e:
            return _mock_attest_err('internal_error', 500, f"Mock can't import dev client pub: {e}")

        if env.get('client_secret_key_id') != expected_key_id:
            return _mock_attest_err('unknown_client_key', 401,
                                    f"key_id {env.get('client_secret_key_id')!r} not in active set")
        try:
            client_pub_raw = _b64.b64decode(expected_pub_b64)
            Ed25519PublicKey.from_public_bytes(client_pub_raw).verify(
                _b64.b64decode(client_sig_b64), envelope_hash_bytes,
            )
        except (InvalidSignature, ValueError) as e:
            return _mock_attest_err('bad_client_signature', 401, f"Client sig invalid: {e}")

        # Issue a "tunnel token". For the mock, it's just a random string —
        # we don't run cloudflared. Supervisor treats successful issuance
        # as proof the tunnel would be up.
        return jsonify({
            'envelope_type': 'attestation_response',
            'result': 'ok',
            'tunnel_token': f"MOCK_TUNNEL_TOKEN_{secrets.token_urlsafe(24)}",
            'tunnel_token_id': f"tt_{secrets.token_urlsafe(12)}",
            'tunnel_token_expires_at': _mock_now_iso(15 * 60),
            'next_attestation_after': _mock_now_iso(10 * 60),
            'caps': {
                'bandwidth_bytes_remaining_period': 5 * 1024 ** 3,
                'bandwidth_used_period_bytes': 0,
                'rate_limit_rps': 60,
                'max_response_bytes': 10 * 1024 ** 2,
                'max_concurrent_connections': 20,
            },
            'directives': [],
        })

    def _mock_attest_err(code: str, status: int, message: str):
        return jsonify({'code': code, 'message': message, 'request_id': 'mock'}), status

    @app.route('/api/_mock/connect')
    def _mock_clayrune_connect():
        """Dev-only: pretends to be PLATFORM_DOMAIN/connect.

        Skips Firebase signin / username pick / Cloudflare provisioning;
        immediately redirects to /api/mc-callback with synthesized values.
        Username defaults to 'devuser' but can be overridden via ?username_hint=.
        """
        from urllib.parse import urlencode
        nonce = request.args.get('nonce', '')
        username = request.args.get('username_hint', '').strip() or 'devuser'
        device_pub = request.args.get('device_pub', '')

        # Synthesize what the real CP would return
        callback_params = {
            'nonce': nonce,
            'enrollment_token': f'MOCK_TOKEN_{secrets.token_urlsafe(16)}',
            'username': username,
            'device_id': f'dev_mock_{secrets.token_urlsafe(8)}',
            # Use whatever PLATFORM_DOMAIN the proprietary mc_remote module
            # was configured with — keeps validator happy (it checks
            # hostname == <username>.<PLATFORM_DOMAIN>).
            'hostname': f'{username}.{_mock_platform_domain()}',
        }
        return redirect('/api/mc-callback?' + urlencode(callback_params))

    def _mock_platform_domain() -> str:
        try:
            from mc_remote import config as _mc_cfg
            return _mc_cfg.PLATFORM_DOMAIN
        except Exception:
            return 'clayrune.io'

    print('[remote-access] LOCAL MOCK control plane enabled at /api/_mock/connect '
          '(dev only; do not enable in production)', flush=True)


# ─────────────────────────────────────────────────────────────────────────────
# Remote Access (Mission Control Cloud)
# ─────────────────────────────────────────────────────────────────────────────
# Thin Flask layer over whatever RemoteAccessProvider has registered itself
# via mc_remote_iface. Open-source-safe: if no provider is installed, every
# /api/remote/* endpoint returns 200 with `provider: null` (status) or 501
# (action endpoints). The frontend's Settings panel handles either.
#
# See `docs/remote-access/07-licensing.md` §4 for the open-core contract.

def _get_remote_provider():
    """Return the registered RemoteAccessProvider, or None."""
    if mc_remote_iface is None:
        return None
    try:
        return mc_remote_iface.get_provider()
    except Exception:
        return None


def _provider_status_dict(p):
    """Convert ProviderStatus dataclass → dict for JSON response."""
    s = p.status()
    caps = p.get_caps()
    return {
        'provider': {
            'name': p.name,
            'vendor_url': p.vendor_url,
        },
        'enrolled': s.enrolled,
        'online': s.online,
        'connecting': getattr(s, 'connecting', False),
        'hostname': s.hostname,
        'username': s.username,
        'last_seen': s.last_seen,
        'error_code': s.error_code,
        'error_message': s.error_message,
        'caps': None if caps is None else {
            'bandwidth_quota_period_bytes': caps.bandwidth_quota_period_bytes,
            'bandwidth_used_period_bytes': caps.bandwidth_used_period_bytes,
            'rate_limit_rps': caps.rate_limit_rps,
            'max_response_bytes': caps.max_response_bytes,
            'max_concurrent_connections': caps.max_concurrent_connections,
        },
    }


# ── Per-CF-session "name this device" labels ────────────────────────────────
# When a browser/phone signs in via CF Access OTP, the first request through
# the tunnel is intercepted (see `_redirect_unlabeled_cf_session` below) and
# routed to `/_mc/name-device`. The user picks a friendly name; we store
# `{nonce → {label, ua, created_at}}` keyed by the CF Access session nonce.
# `/api/remote/sessions` then enriches CP sessions with the label for that
# nonce. CF Access doesn't expose user_agent or the device name itself, so
# this is the only way to give sessions human-meaningful identifiers.

SESSION_LABELS_PATH = _DATA_ROOT / 'data' / 'session_labels.json'


def _load_session_labels() -> dict:
    try:
        with open(SESSION_LABELS_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_session_labels(d: dict) -> None:
    SESSION_LABELS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SESSION_LABELS_PATH.with_suffix('.json.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f, indent=2, ensure_ascii=False)
    os.replace(tmp, SESSION_LABELS_PATH)


def _set_session_label(nonce: str, label: str, ua: str) -> None:
    if not nonce:
        return
    d = _load_session_labels()
    existing = d.get(nonce, {}) if isinstance(d.get(nonce), dict) else {}
    d[nonce] = {
        'label': label[:80],
        'ua': (ua or '')[:300],
        'created_at': existing.get('created_at') or int(_time.time()),
        'updated_at': int(_time.time()),
    }
    _save_session_labels(d)


def _cf_session_nonce_from_request() -> str:
    """Best-effort extraction of the CF Access session nonce.

    Reads the `Cf-Access-Jwt-Assertion` header (preferred) or the
    `CF_Authorization` cookie. We base64-decode the JWT payload without
    verifying the signature — the tunnel itself is the auth boundary in our
    threat model (anyone reaching this MC instance has already passed CF
    Access OTP). Returns '' if absent or unparseable.
    """
    jwt_str = request.headers.get('Cf-Access-Jwt-Assertion', '') or request.cookies.get('CF_Authorization', '')
    if not jwt_str or jwt_str.count('.') < 2:
        return ''
    try:
        import base64
        payload_b64 = jwt_str.split('.')[1]
        # base64url, may need padding
        padding = '=' * ((4 - len(payload_b64) % 4) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
        return str(payload.get('nonce') or payload.get('identity_nonce') or '')
    except Exception:
        return ''


def _is_cf_tunneled_request() -> bool:
    """True iff this request arrived through CF Access (i.e. via the tunnel).

    Localhost dashboard hits don't have these headers — only requests routed
    through cloudflared from the public hostname do.
    """
    return bool(request.headers.get('Cf-Access-Authenticated-User-Email')
                or request.headers.get('Cf-Access-Jwt-Assertion'))


@app.before_request
def _redirect_unlabeled_cf_session():
    """If a tunneled request lacks a stored label for its CF nonce, send the
    user to the name-device page. Skips API/static/the page itself.
    """
    if not _is_cf_tunneled_request():
        return None
    path = request.path or '/'
    # Don't redirect API, static, or the name-device page itself (and its POST endpoint).
    if (path.startswith('/api/')
            or path.startswith('/static/')
            or path.startswith('/_mc/')
            or path == '/favicon.ico'):
        return None
    nonce = _cf_session_nonce_from_request()
    if not nonce:
        return None  # nothing to key on; let the request through
    labels = _load_session_labels()
    if nonce in labels and (labels[nonce] or {}).get('label'):
        return None  # already named
    return redirect('/_mc/name-device', code=302)


@app.route('/_mc/name-device')
def mc_name_device_page():
    """Serve the 'name this device' form. Pre-fills detected platform/browser
    from the User-Agent so the user sees what we detected.
    """
    ua = request.headers.get('User-Agent', '')
    nonce = _cf_session_nonce_from_request()
    email = request.headers.get('Cf-Access-Authenticated-User-Email', '')
    # Render a tiny standalone HTML page (no dependency on the SPA bundle).
    html = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Name this device</title>
  <style>
    :root { --accent: #e8824a; --bg: #fdfaf6; --fg: #1a1a1a; --muted: #6b6b6b; --border: #e0d8cc; }
    * { box-sizing: border-box; }
    html, body { margin: 0; padding: 0; height: 100%; background: var(--bg); color: var(--fg); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif; }
    .wrap { max-width: 440px; margin: 0 auto; padding: 36px 22px; }
    h1 { font-size: 22px; margin: 0 0 8px; font-weight: 700; }
    p.lead { color: var(--muted); font-size: 14px; line-height: 1.5; margin: 0 0 18px; }
    .card { background: white; border: 2px solid var(--border); border-radius: 14px; padding: 18px; }
    label { display: block; font-size: 12px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; margin-bottom: 6px; }
    input { width: 100%; padding: 12px 14px; font-size: 16px; border: 2px solid var(--border); border-radius: 10px; background: white; color: var(--fg); }
    input:focus { outline: none; border-color: var(--accent); }
    .detected { font-size: 12px; color: var(--muted); margin: 14px 0 0; padding: 10px 12px; background: #f6f1ea; border-radius: 8px; word-break: break-word; }
    .detected b { color: var(--fg); }
    button { width: 100%; margin-top: 16px; padding: 14px; font-size: 16px; font-weight: 600; background: var(--accent); color: white; border: none; border-radius: 10px; cursor: pointer; }
    button:disabled { opacity: .5; cursor: not-allowed; }
    button:hover:not(:disabled) { filter: brightness(1.05); }
    .err { color: #c0392b; font-size: 13px; margin-top: 10px; min-height: 1em; }
    .footer { text-align: center; font-size: 11px; color: var(--muted); margin-top: 18px; }
    .suggest-row { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
    .suggest { font-size: 12px; padding: 5px 10px; background: #f6f1ea; border: 1px solid var(--border); border-radius: 999px; cursor: pointer; }
    .suggest:hover { background: #efe5d6; }
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Name this device</h1>
    <p class="lead">So you can tell your sessions apart later. Sign-in expires in 24 hours.</p>
    <div class="card">
      <label for="nm">Device name</label>
      <input id="nm" autofocus placeholder="e.g. My iPhone" maxlength="80" />
      <div class="suggest-row" id="suggest"></div>
      <div class="detected">Detected: <b id="det"></b><br><span id="email" style="font-size:11px;opacity:.75"></span></div>
      <button id="go" disabled>Continue</button>
      <div class="err" id="err"></div>
    </div>
    <div class="footer">Clayrune · Cloudflare Access</div>
  </div>
<script>
const NONCE = __NONCE__;
const UA    = __UA__;
const EMAIL = __EMAIL__;

function brief(ua) {
  let b='Browser', os='';
  if (/Edg\\//.test(ua)) b='Edge';
  else if (/CriOS/.test(ua)) b='Chrome';
  else if (/FxiOS/.test(ua)) b='Firefox';
  else if (/Chrome\\//.test(ua)) b='Chrome';
  else if (/Firefox\\//.test(ua)) b='Firefox';
  else if (/Safari\\//.test(ua)) b='Safari';
  if (/iPhone/.test(ua)) os='iPhone';
  else if (/iPad/.test(ua)) os='iPad';
  else if (/Android/.test(ua)) os='Android';
  else if (/Windows/.test(ua)) os='Windows';
  else if (/Mac OS X|Macintosh/.test(ua)) os='Mac';
  else if (/Linux/.test(ua)) os='Linux';
  return os ? b+' on '+os : b;
}

const detEl = document.getElementById('det');
const emailEl = document.getElementById('email');
detEl.textContent = brief(UA || navigator.userAgent);
emailEl.textContent = EMAIL;

// Suggestion chips
const ua = (UA || navigator.userAgent);
const sugs = [];
if (/iPhone/.test(ua))    sugs.push('My iPhone');
if (/iPad/.test(ua))      sugs.push('My iPad');
if (/Android/.test(ua))   { sugs.push('My Phone'); sugs.push('My Android'); }
if (/Windows/.test(ua))   sugs.push('Windows PC');
if (/Mac OS X|Macintosh/.test(ua)) sugs.push('My Mac');
sugs.push('Work Laptop'); sugs.push('Home PC');
const sugRow = document.getElementById('suggest');
sugs.slice(0,4).forEach(s => {
  const b = document.createElement('button');
  b.type = 'button'; b.className = 'suggest'; b.textContent = s;
  b.onclick = () => { document.getElementById('nm').value = s; checkBtn(); };
  sugRow.appendChild(b);
});

const inp = document.getElementById('nm');
const btn = document.getElementById('go');
const err = document.getElementById('err');
function checkBtn() { btn.disabled = !inp.value.trim(); }
inp.addEventListener('input', checkBtn);
inp.addEventListener('keydown', e => { if (e.key === 'Enter' && !btn.disabled) submit(); });
btn.addEventListener('click', submit);

async function submit() {
  const label = inp.value.trim();
  if (!label) return;
  btn.disabled = true; err.textContent = '';
  try {
    const r = await fetch('/api/_mc/session-label', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ nonce: NONCE, label }),
    });
    const j = await r.json();
    if (r.ok && j.ok) {
      window.location.href = '/';
    } else {
      err.textContent = j.message || ('Could not save (' + r.status + ')');
      btn.disabled = false;
    }
  } catch (e) {
    err.textContent = 'Network error: ' + e;
    btn.disabled = false;
  }
}
</script>
</body>
</html>
"""
    html = (html
            .replace('__NONCE__', json.dumps(nonce))
            .replace('__UA__',    json.dumps(ua))
            .replace('__EMAIL__', json.dumps(email)))
    resp = Response(html, mimetype='text/html; charset=utf-8')
    resp.headers['Cache-Control'] = 'no-store'
    return resp


@app.route('/api/_mc/session-label', methods=['POST'])
def mc_set_session_label():
    """Record `{nonce → label}`. Only accepts requests that came through CF Access."""
    if not _is_cf_tunneled_request():
        return jsonify({'ok': False, 'message': 'Not a tunneled request'}), 403
    body = request.get_json(silent=True) or {}
    nonce = (body.get('nonce') or '').strip() or _cf_session_nonce_from_request()
    label = (body.get('label') or '').strip()
    if not nonce:
        return jsonify({'ok': False, 'message': 'No CF session nonce'}), 400
    if not label:
        return jsonify({'ok': False, 'message': 'Label required'}), 400
    ua = request.headers.get('User-Agent', '')
    _set_session_label(nonce, label, ua)
    return jsonify({'ok': True, 'nonce': nonce, 'label': label})


# ── Auto-revoke unnamed sessions ────────────────────────────────────────────
# Background loop: every interval, lists CF Access sessions; for any session
# whose nonce isn't in `session_labels.json` AND is older than the threshold,
# calls per-session revoke (strict mode — no fallback to revoke-all). Keeps
# the sessions UI tidy: sessions that didn't go through the name-device flow
# get cleaned up automatically. Named sessions are never touched.

_ENFORCER_STATE = {
    'last_run': 0,
    'last_revoked_count': 0,
    'last_skipped_count': 0,
    'last_error': '',
    'last_per_session_supported': None,  # None=unknown, True/False after a try
}
_enforcer_lock = threading.Lock()


def _enforce_session_labels_once(force: bool = False) -> dict:
    """One pass of the label enforcer. Returns a small status dict.

    Called by the daemon loop on a timer + by a manual `/api/remote/sessions/enforce`
    endpoint. Idempotent.
    """
    cfg = CONFIG  # already loaded
    enabled = bool(cfg.get('auto_revoke_unnamed_sessions', True))
    threshold = int(cfg.get('auto_revoke_unnamed_after_seconds', 600))
    if not enabled and not force:
        return {'ok': True, 'skipped': 'disabled'}

    p = _get_remote_provider()
    if p is None:
        return {'ok': True, 'skipped': 'no_provider'}

    try:
        from mc_remote import enrollment as _mc_enrollment, config as _mc_config
    except Exception as e:
        return {'ok': False, 'error': f'import_error: {e}'}

    auth_kwargs, err = _cp_auth_kwargs(empty_resp_field='sessions')
    if err is not None:
        return {'ok': True, 'skipped': err.get('error', 'no_auth')}

    cp_url = _mc_config.control_plane_base_url()

    try:
        body = _mc_enrollment.list_sessions_via_cp(cp_base_url=cp_url, **auth_kwargs)
    except Exception as e:
        return {'ok': False, 'error': f'list_failed: {e}'}

    if not isinstance(body, dict) or not isinstance(body.get('sessions'), list):
        return {'ok': True, 'skipped': 'no_sessions_response'}
    if body.get('error'):
        return {'ok': True, 'skipped': f'cp_error:{body.get("error")}'}

    labels = _load_session_labels()
    now = int(_time.time())
    revoked = []
    skipped_unsupported = []
    for s in body['sessions']:
        nonce = s.get('nonce') or ''
        sid = s.get('session_id') or ''
        issued = s.get('issued_at') or 0
        if not sid or not nonce:
            continue
        is_labeled = nonce in labels and (labels[nonce] or {}).get('label')
        if is_labeled:
            continue
        age = now - int(issued) if issued else 0
        if age < threshold and not force:
            continue
        # Strict revoke — no fallback to revoke-all. If CF doesn't support
        # per-session revoke for this account, we abort rather than nuking
        # the user's labeled sessions.
        try:
            r = _mc_enrollment.revoke_session_via_cp(
                cp_base_url=cp_url, session_id=sid, strict=True, **auth_kwargs,
            )
            if r.get('ok') and r.get('scope') == 'session':
                revoked.append({'nonce': nonce, 'short_id': s.get('short_id', '')})
                _ENFORCER_STATE['last_per_session_supported'] = True
            elif r.get('error') == 'per_session_unsupported' or r.get('status') == 503:
                # CF doesn't support per-session for this token. Stop trying.
                _ENFORCER_STATE['last_per_session_supported'] = False
                skipped_unsupported.append(nonce)
                break
            else:
                skipped_unsupported.append(nonce)
        except Exception as e:
            _ENFORCER_STATE['last_error'] = f'revoke_failed: {e}'

    _ENFORCER_STATE['last_run'] = now
    _ENFORCER_STATE['last_revoked_count'] = len(revoked)
    _ENFORCER_STATE['last_skipped_count'] = len(skipped_unsupported)
    if revoked:
        print(f"[remote-access] auto-revoked {len(revoked)} unnamed session(s): "
              f"{[r['short_id'] for r in revoked]}", flush=True)
    return {
        'ok': True,
        'revoked': revoked,
        'skipped_unsupported': skipped_unsupported,
        'per_session_supported': _ENFORCER_STATE['last_per_session_supported'],
    }


def _warmup_control_plane():
    """Fire one GET /v1/health at the configured CP base URL.

    Cloud Run with min-instances=0 cold-starts in 2-5s; without warmup, the
    user's first click pays that latency. Hitting /health on MC startup means
    the CP is already warm by the time anyone clicks anything.
    """
    try:
        from mc_remote import config as _mc_config
    except Exception:
        return  # provider not installed — nothing to warm
    try:
        base = _mc_config.control_plane_base_url()
    except Exception:
        return
    if not base:
        return
    url = f"{base.rstrip('/')}/health"
    try:
        import requests
        t0 = _time.monotonic()
        r = requests.get(url, timeout=15)
        dt_ms = int((_time.monotonic() - t0) * 1000)
        print(f"[remote-access] CP warmup {url} -> {r.status_code} in {dt_ms}ms", flush=True)
    except Exception as e:
        print(f"[remote-access] CP warmup failed (will not retry): {e}", flush=True)


def _session_label_enforcer_loop():
    """Daemon thread: run the enforcer every N seconds."""
    interval = max(30, int(CONFIG.get('auto_revoke_check_interval_seconds', 60)))
    while True:
        try:
            with _enforcer_lock:
                _enforce_session_labels_once()
        except Exception as e:
            print(f"[remote-access] enforcer crashed: {e}", flush=True)
            _ENFORCER_STATE['last_error'] = str(e)
        _time.sleep(interval)


@app.route('/api/remote/sessions/enforce', methods=['POST'])
def remote_sessions_enforce():
    """Manually trigger the unnamed-session cleanup. Returns what was revoked."""
    with _enforcer_lock:
        body = _enforce_session_labels_once(force=True)
    body['state'] = dict(_ENFORCER_STATE)
    return jsonify(body)


@app.route('/api/remote/sessions/enforcer-state')
def remote_sessions_enforcer_state():
    """Read-only view of the last enforcer run for the Settings panel."""
    return jsonify(dict(_ENFORCER_STATE))


@app.route('/api/remote/status')
def remote_status():
    """Status of the registered remote-access provider, or `provider: null`.

    Polled by the Settings panel. Cheap; safe to hit every few seconds.
    """
    p = _get_remote_provider()
    if p is None:
        return jsonify({'provider': None})
    try:
        return jsonify(_provider_status_dict(p))
    except Exception as e:
        return jsonify({
            'provider': {'name': getattr(p, 'name', 'Unknown'),
                         'vendor_url': getattr(p, 'vendor_url', '')},
            'enrolled': False,
            'online': False,
            'error_code': 'internal_error',
            'error_message': f'Provider status() failed: {e}',
        }), 200


@app.route('/api/remote/enable', methods=['POST'])
def remote_enable():
    """Begin enrollment. Launches the OS browser server-side and also returns
    the URL so the frontend can fall back to a manual-copy display.

    Server-side launch (via Python's webbrowser module) is required because
    Tauri / WebView2 silently blocks `window.open()` calls that aren't
    direct user-gesture navigations.
    """
    p = _get_remote_provider()
    if p is None:
        return jsonify({'error': 'no_provider'}), 501
    try:
        url = p.begin_enrollment()
    except NotImplementedError as e:
        return jsonify({'error': 'not_implemented', 'message': str(e)}), 501
    except Exception as e:
        return jsonify({'error': 'internal_error', 'message': str(e)}), 500

    # Some providers (notably the dev stub) signal "no real browser needed —
    # we're done already" by returning a `data:` URL or a URL with the
    # `mc-no-browser` query flag. Skip the launch in those cases.
    skip_browser = (
        url.startswith('data:')
        or url.startswith('mc://')
        or 'mc-no-browser=1' in url
    )

    launched = False if skip_browser else _launch_browser_for_user(url)

    return jsonify({
        'ok': True,
        'enrollment_url': url,
        'launched': launched,
        'skip_browser': skip_browser,
    })


def _launch_browser_for_user(url: str) -> bool:
    """Open `url` in the user's default browser. Returns True on success.

    Windows: os.startfile(url) → ShellExecuteW(open). Most reliable across
    elevation contexts, Tauri-spawned subprocesses, and headless services.

    macOS / Linux: subprocess.Popen of `open` / `xdg-open` respectively.
    """
    try:
        if sys.platform.startswith("win"):
            os.startfile(url)  # type: ignore[attr-defined]
            return True
        if sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", url], close_fds=True)
            return True
        # Linux / BSD
        import subprocess
        subprocess.Popen(["xdg-open", url], close_fds=True)
        return True
    except Exception as e:
        print(f"[remote-access] _launch_browser_for_user failed: {e}", flush=True)
        return False


@app.route('/api/remote/disable', methods=['POST'])
def remote_disable():
    """Stop the tunnel. Keeps credentials so re-enable is fast."""
    p = _get_remote_provider()
    if p is None:
        return jsonify({'error': 'no_provider'}), 501
    try:
        p.disable()
    except NotImplementedError as e:
        return jsonify({'error': 'not_implemented', 'message': str(e)}), 501
    except Exception as e:
        return jsonify({'error': 'internal_error', 'message': str(e)}), 500
    return jsonify({'ok': True})


@app.route('/api/remote/resume', methods=['POST'])
def remote_resume():
    """Reverse of /api/remote/disable: restart the tunnel for an already-enrolled
    device. No re-enrollment, no new keypair, no new CF resources.
    """
    p = _get_remote_provider()
    if p is None:
        return jsonify({'error': 'no_provider'}), 501
    try:
        p.resume()
    except NotImplementedError as e:
        return jsonify({'error': 'not_implemented', 'message': str(e)}), 501
    except RuntimeError as e:
        # e.g. "Cannot resume: no enrolled device."
        return jsonify({'error': 'not_enrolled', 'message': str(e)}), 409
    except Exception as e:
        return jsonify({'error': 'internal_error', 'message': str(e)}), 500
    return jsonify({'ok': True})


def _cp_auth_kwargs(empty_resp_field: str = "devices") -> tuple[dict, dict | None]:
    """Build the auth kwargs for `*_via_cp` calls.

    Prefers device-token auth from the local keystore (post-Firebase
    enrollment). Falls back to MC_REMOTE_DEV_EMAIL env (dev-shim only).
    Returns (kwargs, error_response). When error_response is not None, the
    caller should jsonify+return it directly (covers no-provider / no-auth).
    """
    try:
        from mc_remote import device_keys
    except Exception as e:
        return {}, {'error': 'import_error', 'message': str(e), empty_resp_field: []}
    kwargs: dict = {}
    try:
        identity = device_keys.load_identity()
    except Exception:
        identity = None
    if identity:
        kwargs['device_id'] = identity.device_id
        kwargs['enrollment_token'] = identity.enrollment_token
        return kwargs, None
    # Fall back to dev shim
    email = os.environ.get('MC_REMOTE_DEV_EMAIL', '').strip()
    if email:
        kwargs['email'] = email
        return kwargs, None
    return {}, {'error': 'not_enrolled',
                'message': 'No device keystore + no MC_REMOTE_DEV_EMAIL fallback. Click Enable Remote Access first.',
                empty_resp_field: []}


@app.route('/api/remote/devices')
def remote_devices():
    """Proxy GET /v1/devices on the configured CP for the authenticated user.

    Auth: device-token from keystore (post-Firebase) preferred; falls back to
    MC_REMOTE_DEV_EMAIL (dev shim) if no keystore identity.
    """
    p = _get_remote_provider()
    if p is None:
        return jsonify({'error': 'no_provider', 'devices': []}), 501
    try:
        from mc_remote import enrollment as _mc_enrollment, device_keys, config
    except Exception as e:
        return jsonify({'error': 'import_error', 'message': str(e), 'devices': []}), 500

    auth_kwargs, err = _cp_auth_kwargs(empty_resp_field='devices')
    if err is not None:
        return jsonify(err), 503

    try:
        identity = device_keys.load_identity()
    except Exception:
        identity = None
    this_device_id = identity.device_id if identity else None

    body = _mc_enrollment.list_devices_via_cp(
        cp_base_url=config.control_plane_base_url(),
        this_device_id=this_device_id,
        **auth_kwargs,
    )
    return jsonify(body)


@app.route('/api/remote/sessions')
def remote_sessions():
    """Proxy GET /v1/sessions on the configured CP for the authenticated user."""
    p = _get_remote_provider()
    if p is None:
        return jsonify({'error': 'no_provider', 'sessions': []}), 501
    try:
        from mc_remote import enrollment as _mc_enrollment, config
    except Exception as e:
        return jsonify({'error': 'import_error', 'message': str(e), 'sessions': []}), 500
    auth_kwargs, err = _cp_auth_kwargs(empty_resp_field='sessions')
    if err is not None:
        return jsonify(err), 503
    body = _mc_enrollment.list_sessions_via_cp(
        cp_base_url=config.control_plane_base_url(),
        **auth_kwargs,
    )
    # Enrich each session with its locally-stored device label (if any).
    # Match by full nonce; fall back to short_id if CP is on an older version.
    try:
        if isinstance(body, dict) and isinstance(body.get('sessions'), list):
            labels = _load_session_labels()
            short_index = {n[-6:]: lab for n, lab in labels.items() if isinstance(lab, dict) and n}
            for s in body['sessions']:
                nonce = s.get('nonce') or ''
                lab = labels.get(nonce) if nonce else None
                if not lab:
                    lab = short_index.get(s.get('short_id') or '')
                if isinstance(lab, dict) and lab.get('label'):
                    s['label'] = lab.get('label')
                    s['ua'] = lab.get('ua') or ''
    except Exception as _e:
        print(f"[remote-access] session label enrichment failed: {_e}", flush=True)
    return jsonify(body)


@app.route('/api/remote/sessions/<session_id>/label', methods=['POST'])
def remote_session_label(session_id):
    """Retroactively label any CF Access session by full session_id.

    Local-only endpoint (called by the desktop dashboard); does NOT require
    a CF Access tunneled request the way `/api/_mc/session-label` does.
    Extracts the nonce from the trailing `_sessions_<nonce>` suffix of the
    session_id (CF's canonical name format).
    """
    body = request.get_json(silent=True) or {}
    label = (body.get('label') or '').strip()
    if not label:
        return jsonify({'ok': False, 'message': 'Label required'}), 400
    # session_id format: <account>_<user>_sessions_<nonce>
    marker = '_sessions_'
    idx = session_id.rfind(marker)
    if idx < 0:
        return jsonify({'ok': False, 'message': 'Could not parse nonce from session_id'}), 400
    nonce = session_id[idx + len(marker):]
    if not nonce:
        return jsonify({'ok': False, 'message': 'Empty nonce'}), 400
    _set_session_label(nonce, label, '')  # no UA available retroactively
    return jsonify({'ok': True, 'nonce': nonce, 'label': label})


@app.route('/api/remote/sessions/<session_id>/revoke', methods=['POST'])
def remote_session_revoke(session_id):
    p = _get_remote_provider()
    if p is None:
        return jsonify({'error': 'no_provider'}), 501
    try:
        from mc_remote import enrollment as _mc_enrollment, config
    except Exception as e:
        return jsonify({'error': 'import_error', 'message': str(e)}), 500
    auth_kwargs, err = _cp_auth_kwargs(empty_resp_field='sessions')
    if err is not None:
        return jsonify(err), 503
    body = _mc_enrollment.revoke_session_via_cp(
        cp_base_url=config.control_plane_base_url(),
        session_id=session_id,
        **auth_kwargs,
    )
    return jsonify(body)


@app.route('/api/remote/sessions/revoke-all', methods=['POST'])
def remote_sessions_revoke_all():
    p = _get_remote_provider()
    if p is None:
        return jsonify({'error': 'no_provider'}), 501
    try:
        from mc_remote import enrollment as _mc_enrollment, config
    except Exception as e:
        return jsonify({'error': 'import_error', 'message': str(e)}), 500
    auth_kwargs, err = _cp_auth_kwargs(empty_resp_field='sessions')
    if err is not None:
        return jsonify(err), 503
    body = _mc_enrollment.revoke_all_sessions_via_cp(
        cp_base_url=config.control_plane_base_url(),
        **auth_kwargs,
    )
    return jsonify(body)


@app.route('/api/remote/disconnect', methods=['POST'])
def remote_disconnect():
    """Revoke this device on the platform; clear local credentials."""
    p = _get_remote_provider()
    if p is None:
        return jsonify({'error': 'no_provider'}), 501
    try:
        p.disconnect_this_device()
    except NotImplementedError as e:
        return jsonify({'error': 'not_implemented', 'message': str(e)}), 501
    except Exception as e:
        return jsonify({'error': 'internal_error', 'message': str(e)}), 500
    return jsonify({'ok': True})


# ── Endpoints called by mc-tunnel and the enrollment browser flow ────────────
# These exist so the proprietary provider has fixed integration points it can
# rely on. Until a real provider is wired in, both return placeholder responses.

@app.route('/api/tunnel-handshake')
def tunnel_handshake():
    """Localhost handshake from `mc-tunnel`. See attestation protocol §5.2.

    The proprietary provider, when wired up, replaces this handler with one
    that verifies the shared secret and returns the device challenge JSON.
    Without a provider, returns 503 so `mc-tunnel` exits cleanly.
    """
    p = _get_remote_provider()
    if p is None:
        return jsonify({'error': 'no_provider', 'remote_access_enabled': False}), 503
    # Provider hasn't installed a custom handler yet — placeholder until wired.
    return jsonify({'error': 'not_implemented'}), 501


def _mc_callback_html(title: str, body: str, *, status: int = 200, accent: str = "#10b981") -> Response:
    """Render the friendly post-enrollment page shown to the user's browser."""
    safe_title = title.replace("<", "&lt;").replace(">", "&gt;")
    return Response(
        f"""<!doctype html>
<html><head><meta charset='utf-8'><title>Clayrune</title>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
          background: #fafaf7; color: #1f2937; margin: 0; min-height: 100vh;
          display: flex; align-items: center; justify-content: center; padding: 24px; }}
  .card {{ background: #fff; border-radius: 16px; box-shadow: 0 1px 3px rgba(0,0,0,.06), 0 8px 24px rgba(0,0,0,.04);
           padding: 40px 32px; max-width: 480px; width: 100%; text-align: center; }}
  .badge {{ width: 56px; height: 56px; border-radius: 14px; background: {accent}22;
            color: {accent}; display: inline-flex; align-items: center; justify-content: center;
            font-size: 28px; margin-bottom: 20px; border: 2px solid {accent}55; }}
  h1 {{ font-size: 22px; margin: 0 0 8px; font-weight: 700; }}
  p {{ font-size: 15px; line-height: 1.55; color: #4b5563; margin: 0 0 14px; }}
  .hint {{ font-size: 13px; color: #6b7280; margin-top: 20px; padding-top: 16px;
           border-top: 1px solid #f0eee8; }}
</style></head>
<body><div class='card'>
  <div class='badge'>{'✓' if status == 200 else '!'}</div>
  <h1>{safe_title}</h1>
  {body}
  <p class='hint'>You can close this window and return to Clayrune.</p>
</div></body></html>""",
        status=status,
        mimetype='text/html; charset=utf-8',
    )


@app.route('/api/mc-callback')
def mc_callback():
    """Browser redirect target at the end of enrollment.

    Calls the registered provider's enrollment.complete() with the query
    params from the control plane. Renders a friendly success/failure page.
    See `02-attestation-protocol.md` §6.1 step 7.
    """
    p = _get_remote_provider()
    if p is None:
        return _mc_callback_html(
            "Remote access isn't available",
            "<p>Clayrune Remote Access isn't installed in this build.</p>",
            status=404, accent="#9ca3af",
        )

    # The proprietary provider's enrollment module owns this validation.
    # We ask the provider for it via a dunder-ish hook so MC core stays
    # provider-agnostic. If the provider doesn't expose one, fall back
    # to the canonical mc_remote.enrollment.complete().
    try:
        from mc_remote import enrollment as _mc_enrollment  # type: ignore
    except Exception as e:
        return _mc_callback_html(
            "Remote access isn't fully wired yet",
            f"<p>Couldn't reach the enrollment module ({e}).</p>",
            status=500, accent="#ef4444",
        )

    result = _mc_enrollment.complete(request.args.to_dict(flat=True))

    if result.get("ok"):
        identity = result["identity"]
        host = identity.hostname
        return _mc_callback_html(
            "You're connected!",
            f"<p>Your Clayrune dashboard is reachable from anywhere at:</p>"
            f"<p style='font-family:JetBrains Mono,Consolas,monospace;font-size:14px;color:#1f2937;"
            f"background:#f3f4f6;padding:10px 14px;border-radius:8px;display:inline-block'>"
            f"https://{host}</p>",
        )

    return _mc_callback_html(
        "Sign-in didn't complete",
        f"<p>{result.get('message', 'Unknown error')}</p>"
        f"<p style='font-size:12px;color:#9ca3af'>Code: {result.get('error', 'unknown')}</p>",
        status=400, accent="#ef4444",
    )


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
RESTART_LOG_PATH = _DATA_ROOT / 'data' / 'restart_log.json'
_LAST_RESTART_TIME = 0.0
_RESTART_RATE_LIMIT_SECONDS = 30
# Set once at module load. Changes every time the Python process is replaced,
# so any dashboard polling /api/system/heartbeat can detect a restart by
# comparing this against its cached value.
_SERVER_STARTED_AT = datetime.now(timezone.utc).isoformat()
_SERVER_STARTED_MONOTONIC = _time.time()


@app.route('/api/system/heartbeat')
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


def _get_active_restart_blockers():
    """Snapshot of sessions/hiveminds that would be killed if we restarted now.

    "Active" = a live agent turn (status='running') or an active hivemind
    orchestrator. Idle/completed/error/stopped sessions are NOT blockers — their
    process is either dead or just waiting on stdin and is safe to drop.
    """
    project_names = {p['id']: p.get('name', p['id']) for p in load_projects()}
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
        print(f"[restart] failed to append log: {e}")


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
            print(f"[restart] graceful stop failed for {sid}: {e}")

    # Schedule background kills (existing helper handles tree-kill + wait).
    for proc in procs:
        _kill_proc_background(proc)

    # Brief wait so the children get a chance to die before exec replaces us.
    deadline = _time.time() + grace_seconds
    while _time.time() < deadline:
        alive = [p for p in procs if p.poll() is None]
        if not alive:
            break
        _time.sleep(0.1)


def _perform_server_restart_async(audit_entry):
    """Run after the HTTP response flushes: stop everything, then re-exec.

    Re-exec replaces the current Python process in place. Same PID, fresh
    interpreter — picks up code changes on disk. Open SSE streams drop, the
    frontend's polling overlay reconnects when /api/projects starts answering
    again, and the localStorage open-modals snapshot restores the conversation
    layout.
    """
    def _do_restart():
        _time.sleep(0.4)  # let the HTTP 202 actually reach the client
        try:
            _stop_all_sessions_for_restart()
        except Exception as e:
            print(f"[restart] stop-all failed (continuing anyway): {e}")
        try:
            _append_restart_log(audit_entry)
        except Exception:
            pass
        print("[restart] spawning fresh server process and exiting old one")
        # Use subprocess.Popen instead of os.execv. On Windows execv is
        # implemented as spawn-new-then-exit-old AND the new process inherits
        # open handles (including the listening socket). Worse, any child
        # processes we spawned (Mode B agents, terminal sessions) ALSO hold
        # that socket FD — so the port stays bound until every descendant
        # dies, which can be longer than the new instance is willing to wait.
        # A fresh subprocess.Popen with close_fds=True starts clean.
        new_env = os.environ.copy()
        new_env['MC_RESTART_FROM_PID'] = str(os.getpid())
        try:
            popen_kwargs = {
                'env': new_env,
                'cwd': os.getcwd(),
                'close_fds': True,
            }
            if sys.platform == 'win32':
                # DETACHED_PROCESS so it survives our exit; CREATE_NEW_PROCESS_GROUP
                # so Ctrl-C in the old terminal doesn't propagate. CREATE_NEW_CONSOLE
                # gives it a visible window if launched from one (matches user expectation).
                popen_kwargs['creationflags'] = (
                    subprocess.CREATE_NEW_PROCESS_GROUP
                    | subprocess.CREATE_NEW_CONSOLE
                )
            else:
                popen_kwargs['start_new_session'] = True
                # TODO(linux/macos): once the parent exits, the child's stdout/stderr
                # are wired to the now-closed terminal so log output disappears.
                # Redirect to a rotating file (e.g. data/server.log) here:
                #   logf = open(_DATA_ROOT / 'data' / 'server.log', 'ab')
                #   popen_kwargs['stdout'] = logf
                #   popen_kwargs['stderr'] = subprocess.STDOUT
                # Skipped today because MC is Windows-only in practice and the
                # CREATE_NEW_CONSOLE branch above gives Windows users a visible window.
            subprocess.Popen([sys.executable] + sys.argv, **popen_kwargs)
        except Exception as e:
            print(f"[restart] failed to spawn new instance: {e}")
            os._exit(1)
        # Give the spawn ~250ms to get past its own startup before we exit and
        # release the listening socket. (The new instance's port-conflict
        # bypass will keep waiting beyond that if needed.)
        _time.sleep(0.25)
        os._exit(0)
    threading.Thread(target=_do_restart, daemon=True).start()


@app.route('/api/system/restart/status')
def system_restart_status():
    """Return what's currently active so the UI can warn before restarting."""
    return jsonify(_get_active_restart_blockers())


@app.route('/api/system/restart', methods=['POST'])
def system_restart():
    """Restart the Mission Control server process.

    Body: {"confirmed": true, "force": bool}. We always re-check active state
    on the server to close the GET → POST race window (a cron or hivemind
    could have spawned a fresh session in between). If active and force is
    falsy, return 409 with the live blocker list so the UI can re-prompt.
    """
    global _LAST_RESTART_TIME
    data = request.get_json(silent=True) or {}
    if not data.get('confirmed'):
        return jsonify({'error': 'confirmation required (set "confirmed": true)'}), 400

    now = _time.time()
    if now - _LAST_RESTART_TIME < _RESTART_RATE_LIMIT_SECONDS:
        wait = int(_RESTART_RATE_LIMIT_SECONDS - (now - _LAST_RESTART_TIME))
        return jsonify({'error': f'restart was triggered recently; try again in {wait}s'}), 429

    blockers = _get_active_restart_blockers()
    if (blockers['active_sessions'] or blockers['active_hiveminds']) and not data.get('force'):
        return jsonify({
            'error': 'active flows present; stop them or pass "force": true',
            **blockers,
        }), 409

    _LAST_RESTART_TIME = now
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


if __name__ == '__main__':
    _check_port_conflict()
    _start_scheduler()
    _start_hivemind_orchestrator()
    _start_session_guardian()
    # Ensure the global incognito pseudo-project exists so it shows up in
    # /api/projects without the FE needing a first-touch bootstrap.
    try:
        _ensure_incognito_project()
    except Exception as e:
        print(f"[incognito] bootstrap failed: {e}")
    # Reconcile pending agent_log rows: any 'in_progress' entry leftover from a
    # session that was killed by the previous shutdown is by definition orphaned
    # (no live sessions exist yet at startup). Flip those to 'interrupted' so
    # they don't show as forever-running in the Agent Log / Runs panels.
    # Cheap, synchronous; runs before backfill so the two helpers don't race.
    try:
        _reconcile_pending_agent_log_entries()
    except Exception as e:
        print(f"[reconcile-pending] bootstrap failed: {e}")
    # Backfill agent_log from Claude transcripts: makes mid-flight sessions that
    # never finalized (server killed before stream reader's finally) visible in
    # the Agent Log tab. Runs once, in the background, so app.run() isn't blocked.
    # Roll back: set agent_log_backfill_enabled = false in data/config.json.
    threading.Thread(target=_backfill_all_agent_logs, daemon=True).start()
    # One-shot: transition orphaned 'active' hiveminds to 'stale'. Cheap, runs
    # synchronously before app.run().
    try:
        _hm_reconcile_stale_on_startup()
    except Exception as e:
        print(f"[hivemind-reconcile] bootstrap failed: {e}")
    # Auto-cleanup unnamed CF Access sessions (per-session revoke, strict mode).
    # Roll back: set auto_revoke_unnamed_sessions=false in data/config.json.
    threading.Thread(target=_session_label_enforcer_loop, daemon=True).start()
    # Cloud Run cold-start mitigation: hit /v1/health on startup so the user's
    # first interaction (Enable / Resume / Disconnect) hits a warm CP instance.
    # Cheap; idempotent; safe even if remote-access provider is absent.
    threading.Thread(target=_warmup_control_plane, daemon=True).start()
    print(f"Clayrune running at http://localhost:{PORT}")
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
