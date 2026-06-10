#!/usr/bin/env python3
# Python-version preflight — keep FIRST. server.py is a direct entry point
# (python server.py) and is also imported by app.py's Flask thread; either path
# rejects a too-old interpreter before the 3.10+ import chain loads.
import preflight  # noqa: F401

import hashlib
import json
import os
import shutil
import uuid
import mimetypes
import subprocess
import sys
import threading
import concurrent.futures
import time as _time
from pathlib import Path
from typing import Dict, Optional
from datetime import datetime, timezone, timedelta
from flask import Flask, jsonify, send_from_directory, request, send_file, abort, Response, redirect
import secrets

import skills as _skills
import mcp as _mcp
import mcp_installer as _mcpinst
import marketing_preview as _marketing_preview  # P1-1 Tier 1a (blueprint)
import agent_runtime as _agent_runtime  # Multi-provider abstraction


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


# ── Claude CLI binary resolution ────────────────────────────────────────────
# Delegates to ClaudeRuntime.resolve_binary_str() — single source of truth.
# The full resolution logic lives in agent_runtime.ClaudeRuntime.resolve_binary()
# and handles the Windows .exe-vs-.cmd orphan case + common fallback paths.
# Re-resolved on each call (cheap) so a Claude install after server startup
# is picked up without restart.
def _resolve_claude():
    """Return absolute path to the claude executable, or 'claude' as last
    resort (will then FileNotFoundError, matching prior behavior).
    Delegates to ClaudeRuntime.resolve_binary_str() — single source of truth."""
    return _agent_runtime.get_runtime('claude').resolve_binary_str()


def _claude(*args):
    """Build a subprocess command list with claude resolved to its full path."""
    return [_resolve_claude(), *args]


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

# ── Remote-access provider discovery ── moved to
# mc/blueprints/remote_routes.py (1.7) with the rest of the remote family.
# The import side-effect (mc_remote_iface + provider self-registration) now
# fires at that module's import in the remote stanza below; the registry is
# only read at request/loop time, so timing is equivalent.

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
        'agent_effort': '',
        'agent_max_turns': 0,
        'agent_permission_mode': '',
        'desktop_mode': False,
        'user_name': '',
        'agent_name': '',
        # Persistent agent process (Mode B). Default ON (2026-06-04) — streaming
        # is the standard runtime: one long-lived `claude` per chat, follow-ups
        # written to stdin. A fresh install gets Mode B. Existing config.json
        # files keep their saved value (the merge below preserves it), so this
        # flip only reaches new installs / configs that predate the key.
        'use_streaming_agent': True,
        # P2-1/P2-2 upload limits. 0 = unlimited (default → no behavior
        # change; enforcement is opt-in). upload_quota_bytes caps a
        # project's cumulative backlog-attachment storage;
        # upload_max_file_bytes caps any single uploaded file. Both can be
        # overridden per-project via the arbitrary-key update_project path.
        'upload_quota_bytes': 0,
        'upload_max_file_bytes': 0,
        'log_level': 'info',  # P2-3: debug|info|warn|error gate for _log()
        'condense_threshold_kb': 30,
        'condense_model': '',
        'condense_enabled': True,
        # Leg C executor. 'agent' = legacy free claude -p + Write tool
        # (default until the structured path is telemetry-validated).
        # 'structured' = one non-agentic JSON model call applied server-side
        # through the leaf-locked writer. See docs/CONDENSE_STRUCTURED_DESIGN.md.
        'condense_mode': 'agent',
        'index_line_budget': 160,      # SPEC §3 Leg C model-tier target (lines)
        'index_line_hard_floor': 185,  # SPEC §3 Leg C mechanical floor (lines)
        'scribe_enabled': True,        # SPEC §3 Leg A session-end scribe
        'scribe_model': '',            # '' -> 'haiku'
        'scribe_reconcile_enabled': True,  # Fix B startup reconciliation
        'scribe_reconcile_cap': 5,     # max reconciled sessions/project/boot
        'scribe_checkpoint_enabled': False,  # SPEC §3.A.MID Step 6 — default OFF
        'scribe_checkpoint_kb': 0,     # mid-session cadence (KB new transcript); 0=disabled
        'long_session_advisory_enabled': False,  # soft "restart long Mode-B session" nudge
        'long_session_advisory_turns': 25,      # num_turns threshold for that nudge
        # Idle-session eviction — reclaim a warm Mode B fleet (claude.exe + its
        # MCP servers) after long inactivity; the next message transparently
        # respawns it with `-r <csid>` (full context preserved). Default OFF;
        # enable after validation, same posture as scribe_checkpoint. [2026-06-03]
        'idle_eviction_enabled': False,
        'idle_eviction_minutes': 60,    # idle minutes before a warm session is evicted
        # Phase 4 Distiller (v2.1 §11 global keys).
        # Self-learning observer parallel to Scribe — extracts cross-session
        # patterns into _proposed/ for human review. Best-effort, never load-
        # bearing. Default ON; flip distiller_enabled_global=False to kill all
        # paths. distiller_cross_project_enabled gates only the cross-project
        # walk independently. See docs/SKILLS_CURATION_PHASE4_SPEC_V2.md.
        'distiller_enabled_global': True,
        'distiller_cross_project_enabled': True,
        'distiller_model': '',                  # '' → haiku
        'distiller_window_days': 30,
        'distiller_cost_cap_tokens_per_project_per_day': 100000,
        'distiller_proposal_dedupe_days': 7,
        # Preferences carry content + are human-gated at promotion, so they
        # generate on first observation (recurrence 1) instead of waiting for
        # the 3x topic/skill threshold that structurally never fires for
        # single-task sessions. Recurrence becomes a ranking signal, not a gate.
        'distiller_preference_min_recurrence': 1,
        'distiller_cross_project_walk_debounce_session_count': 5,
        'distiller_cross_project_walk_debounce_seconds': 600,
        'read_floor_topk': 3,          # SPEC §3 Leg B deterministic read floor
        # Exploration read-floor — surfaces the Distiller's captured
        # EXPLORATION.md proposals back into a new session's context (the
        # learning-loop closer). Ships default-ON; flip enabled=false to
        # revert to write-only _proposed/ behavior. Kept small (topk=2) so
        # the cache-warmed context stays lean.
        'exploration_readback_enabled': True,
        'exploration_read_floor_topk': 2,
        'agent_channels': '',
        'agent_remote_control': False,
        'agent_revive_from_log': True,
        'agent_log_backfill_enabled': True,
        'agent_log_backfill_max_per_project': 200,
        'agent_log_backfill_max_age_days': 60,
        # Mobile brief replies — when on, messages POSTed with client="mobile"
        # get a hidden directive prepended on the way to the claude stdin
        # stream so the agent answers in Telegram-style: short, conversational,
        # one idea per message, no headers/bullets/long code blocks. The user's
        # chat bubble still shows the original message verbatim. Off by default.
        'mobile_brief_replies_enabled': False,
        # Brief replies EVERYWHERE — same hidden-directive mechanism, but not
        # gated on client="mobile". When on, every Claude dispatch (desktop
        # included) gets a device-neutral brevity nudge so the agent answers
        # short and elaborates only when asked. Supersedes the phone-only gate
        # above. Off by default.
        'brief_replies_always_enabled': False,
        # Auto model router (experimental, default OFF). When on, every dispatch
        # runs a cheap Haiku classifier on the prompt and picks Haiku/Sonnet/Opus
        # based on task complexity. When off, the user-selected model is used
        # as-is. The classifier is fail-open: any error falls back to the
        # user-selected model. Side branch feat/auto-model-router — see backlog
        # for the v2 within-turn multi-CC-call variant.
        'auto_model_enabled': False,
        'auto_model_classifier_model': '',  # '' -> 'haiku'
        # Classifier hard timeout (seconds). Blocks the dispatch only until this
        # deadline; on expiry the router fails open to the user-selected model.
        # Without this, a Haiku rate-limit burst would hang dispatches for the
        # underlying claude oneshot's 180s timeout — diagnosed in the analysis
        # doc (docs/DISPATCH_AND_ROUTING_ANALYSIS.md §C.1 step 1).
        'auto_model_classifier_timeout_secs': 8,
        # Sticky agent settings + respawn-on-flip. Default ON (2026-06-04).
        # When on: (a) the "brief replies everywhere" directive is baked into the
        # spawn-time system prompt (cached, authoritative) instead of being
        # re-prepended to every user turn, and (b) flipping a CLI-flag Tier-1
        # setting (model/effort/…) mid-session resumes live Mode B sessions via -r
        # at the next turn boundary so the change takes effect. System-prompt
        # settings (brief directive, read-floor) apply to FRESH chats only — see
        # _RESPAWN_TRIGGER_KEYS and docs plan respawn-on-setting-flip.md.
        # NOTE: a True default also reaches existing installs whose config.json
        # predates this key (defaults merge under saved values); set it false in
        # config.json to opt out.
        'sticky_agent_settings': True,
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

# ── mc package shims (MODERNIZATION_PLAN.md Phase 0) ─────────────────────────
# Shared state + pure helpers moved verbatim to mc/state.py and mc/core.py.
# These explicit import-as shims keep every existing bare-name reference in
# this file working unchanged; each name's references migrate to
# `state.<name>` as its blueprint extracts (Phase 1). globals().update() is
# forbidden by the plan — explicit names only.
from mc import state as _mc_state

_mc_state.CONFIG = CONFIG  # live alias: mc.core._log reads log_level through this

from mc.core import (  # noqa: E402
    _LOG_LEVELS,
    _atomic_write_text,
    _harden_secret_perms,
    _is_loopback_request,
    _log,
    file_type,
    now_iso,
    time_ago,
)
from mc.state import (  # noqa: E402
    PRESENCE_FRESH_SEC,
    _ENFORCER_STATE,
    _UPDATE_CHECK_BOOT_DELAY_S,
    _UPDATE_CHECK_CACHE,
    _UPDATE_CHECK_INTERVAL_S,
    _UPDATE_CHECK_LOCK,
    _backlog_sync_lock,
    _checkpoint_guard,
    _checkpoint_inflight,
    _checkpoint_sema,
    _checkpoint_sema_guard,
    _claude_auth_lock,
    _claude_auth_state,
    _condense_lock,
    _condense_status,
    _condense_triggered_at,
    _condensing_projects,
    _enforcer_lock,
    _get_mem_write_lock,
    _guardian_stop,
    _hivemind_lock,
    _hivemind_orch_lock,
    _hivemind_orchestrating,
    _hivemind_orchestrator_stop,
    _hivemind_sessions,
    _hivemind_sse_lock,
    _hivemind_sse_queues,
    _managers,
    _managers_lock,
    _mem_write_locks,
    _mem_write_locks_guard,
    _presence_lock,
    _presence_state,
    _provider_env_lock,
    _push_state_lock,
    _scheduler_stop,
    _scribe_lock,
    _scribing_projects,
    agent_sessions,
    process_tracker_lock,
    terminal_lock,
    terminal_sessions,
    tracked_processes,
)

def _cors_origin_allowed(origin: str) -> bool:
    """True iff `origin` is one of our own app shells or a loopback origin.

    The browser sets Origin and a web page cannot forge it, so allowlisting the
    native-webview schemes (Capacitor/Ionic) plus loopback hosts is safe
    and — critically — blocks ordinary websites from driving the API cross-site.
    """
    if not origin:
        return False
    try:
        from urllib.parse import urlparse
        u = urlparse(origin)
    except Exception:
        return False
    host = (u.hostname or '').lower()
    scheme = (u.scheme or '').lower()
    # Native mobile app shells: capacitor://localhost, ionic://localhost
    if scheme in ('capacitor', 'ionic') and host in ('localhost', ''):
        return True
    # Loopback + the https://localhost webview variant
    return host in ('localhost', '127.0.0.1', '::1')


@app.after_request
def add_cors_headers(response):
    # Cross-origin access is allowed ONLY for our own app shells (Tauri /
    # Capacitor / Ionic) and loopback origins. We deliberately do NOT reflect
    # arbitrary Origins: the API binds 0.0.0.0 and the host itself is
    # loopback-exempt from the auth gate, so reflecting any Origin would let any
    # website the user visits drive the API cross-site (CSRF → e.g.
    # /api/terminal/launch RCE). Dashboard access over the CF tunnel is
    # same-origin and needs no CORS header at all.
    origin = request.headers.get('Origin', '')
    if _cors_origin_allowed(origin):
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


def _encode_project_path(project_path):
    """Encode a project path to Claude Code's ~/.claude/projects/<encoded>
    directory name.  C:\\Users\\foo\\bar  →  C--Users-foo-bar.

    Returns None when the path is empty or cannot be resolved (callers
    treat that as "no transcript dir").  Extracted from four inline
    duplicates (IMPROVEMENT_PLAN_V2.md P1-2); the underscore→dash
    fallback some callers also try stays at the call site since not all
    of them want it.
    """
    if not project_path:
        return None
    try:
        resolved = str(Path(project_path).resolve())
    except Exception:
        return None
    return resolved.replace(':', '-').replace('\\', '-').replace('/', '-')


def _session_transcript_path(project_path, claude_session_id):
    """Return the .jsonl transcript path for a Claude session (no existence check).
    Delegates to ClaudeRuntime._build_transcript_path() — path construction lives
    in the runtime so non-claude providers automatically return None.
    """
    return _agent_runtime.get_runtime('claude')._build_transcript_path(
        project_path, claude_session_id)


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


def _long_session_advisory(s):
    """Advisory (NOT enforced): a long-running Mode-B session may be
    compacting away its own early-session context. Step 6 has captured that
    learning durably to MEMORY.md, so restarting the session reloads it
    fresh (a fresh process re-loads MEMORY.md + gets the read-floor) at
    near-zero loss. Distinct from _session_too_large (that's the 5 MB
    resume-perf HARD cap); this is turn-count keyed, fires far earlier, and
    is a soft human-in-loop nudge for Mode-B sessions only.
    SPEC docs/MEMORY_SYSTEM.md Open item #6.
    """
    if not CONFIG.get('long_session_advisory_enabled', True):
        return False
    if s.get('mode') != 'B':
        return False  # Mode A spawns per-turn — no persistent-process amnesia
    if s.get('housekeeping') or s.get('incognito'):
        return False
    if s.get('status') not in ('running', 'idle'):
        return False  # only a live session can be usefully restarted
    thr = int(CONFIG.get('long_session_advisory_turns', 25) or 25)
    return int(s.get('num_turns', 0) or 0) >= thr


def _resume_is_fragile(was_resume, resume_confirmed):
    """Decide whether a dead Mode B session that was a `-r` resume must be
    abandoned (fresh restart, losing the transcript) vs. resumed again.

    Only a resume that NEVER produced output is "fragile" — re-`-r`-ing it
    would just loop, so we go fresh. A resume that produced output is healthy:
    if it dies LATER (the AskUserQuestion `proc.kill()`, idle-eviction, or a
    crash) it must be resumed with `-r` so the conversation is preserved.

    Before this guard existed, ANY session that was ever a resume reset to a
    fresh, context-less session on its next process death — which is why an
    AskUserQuestion in a resumed session lost the whole conversation. See the
    followup respawn path and tests/test_resume_revival.py.
    """
    return bool(was_resume) and not bool(resume_confirmed)


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
    """Scan the Claude transcript directory for a project.

    Returns [{session_id, mtime, first_user, last_user, turns, size}] sorted by mtime desc.
    Delegates to ClaudeRuntime.list_sessions() — scanning logic lives in the runtime.
    """
    return _agent_runtime.get_runtime('claude').list_sessions(project_path, limit=limit)


def _find_transcript_file(project_path, claude_session_id):
    """Locate the Claude Code transcript JSONL for a given csid, or None.
    Delegates to ClaudeRuntime.transcript_path() — path logic lives in the runtime.
    """
    return _agent_runtime.get_runtime('claude').transcript_path(
        project_path, claude_session_id)


def _parse_transcript_messages(f, max_messages=2000):
    """Parse a Claude Code JSONL transcript into [{role, text, tool, timestamp}] for read-only display.

    role: 'user' | 'assistant' | 'tool_call'
    Returns at most max_messages entries; on overflow, keeps the TAIL (most
    recent) — see ClaudeRuntime.parse_transcript_file() for the rationale.
    """
    return _agent_runtime.get_runtime('claude').parse_transcript_file(f, max_messages=max_messages)


def _native_memory_path(project_path):
    """Derive the Claude Code native MEMORY.md path for a project.

    Claude stores memory at ~/.claude/projects/<encoded-path>/memory/MEMORY.md
    where the path encoding replaces : and path separators with -.
    """
    encoded = _encode_project_path(project_path)
    if not encoded:
        return None
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


# ── Leg 0: MEMORY.md managed-region format contract ──────────────────────────
# See docs/MEMORY_SYSTEM_SPEC.md §3 Leg 0. MEMORY.md has two regions:
#   • curated region (top): human/condense-curated pointer index. NEVER touched
#     by the mechanical floor; only the condense model tier may rewrite it.
#   • managed region: machine-written session entries, between the sentinels.
# '## Session Log' is RESERVED as the managed-region header — curated content
# must not use that literal heading.
_MEM_BEGIN = '<!-- clayrune:managed:begin -->'
_MEM_END = '<!-- clayrune:managed:end -->'
_MEM_LOG_HEADER = '## Session Log'
# SPEC §3.A.MID fold-in contract: Step-6 watermark markers live INSIDE the
# managed region but are NOT '- [' entries. They must survive split/compose,
# the mechanical floor must never relocate them, and the Leg C condense prompt
# must preserve them verbatim. One transient line per LIVE session.
_MEM_WM_PREFIX = '<!-- clayrune:wm:'


def _mem_split_full(content):
    """Split MEMORY.md into (curated_text, [entry_lines], [wm_marker_lines]).

    Managed region = sentinel-delimited (or a legacy bare '## Session Log').
    `entries` = lines starting with '- [' (curated pointer lines, also
    '- [...]', are never collected — they're above the sentinel).
    `wm_markers` = full lines starting with the Step-6 watermark prefix.
    Pure function.
    """
    content = content or ''
    if _MEM_BEGIN in content and _MEM_END in content:
        i = content.index(_MEM_BEGIN)
        j = content.index(_MEM_END, i)
        curated = content[:i].rstrip()
        mid = content[i + len(_MEM_BEGIN):j]
    elif _MEM_LOG_HEADER in content:
        i = content.index(_MEM_LOG_HEADER)
        curated = content[:i].rstrip()
        mid = content[i + len(_MEM_LOG_HEADER):]
    else:
        return content.rstrip(), [], []
    entries, wm = [], []
    for ln in mid.splitlines():
        s = ln.strip()
        if s.startswith('- ['):
            entries.append(ln)
        elif s.startswith(_MEM_WM_PREFIX):
            wm.append(s)
    return curated, entries, wm


def _mem_split(content):
    """Back-compat 2-tuple (curated, entries) — every pre-Step-6 caller uses
    this. wm markers are dropped from the return but NOT from the file (the
    write path uses _mem_split_full + _mem_compose(..., wm) to preserve them).
    """
    c, e, _w = _mem_split_full(content)
    return c, e


def _mem_compose(curated, entries, wm_markers=None):
    """Rebuild canonical MEMORY.md from curated + entry lines (+ optional wm
    markers). Always one sentinel-delimited managed region. With wm_markers
    falsy, output is byte-identical to the pre-Step-6 form (existing callers
    unaffected). wm markers are emitted after entries, before the END sentinel.
    """
    curated = (curated or '').rstrip()
    block = f'{_MEM_BEGIN}\n{_MEM_LOG_HEADER}\n'
    body = '\n'.join(entries)
    if body:
        block += body + '\n'
    if wm_markers:
        block += '\n'.join(wm_markers) + '\n'
    block += f'{_MEM_END}\n'
    return (curated + '\n\n' + block) if curated else block


def _mem_migrate(content):
    """Idempotent, additive migration to the Leg 0 canonical format.

    Already-migrated content round-trips unchanged. Legacy bare
    '## Session Log' sections get wrapped in sentinels. Files with no managed
    content gain an empty managed region. Curated content is preserved
    verbatim (modulo trailing whitespace); curated lines are never reordered
    or dropped. wm markers (Step 6) are preserved.
    """
    return _mem_compose(*_mem_split_full(content))


# ── Step 6 watermark markers (SPEC §3.A.MID, D6 fold-in) ─────────────────────
# One single-line comment per LIVE Mode-B session, embedded in the managed
# region, carrying the durable checkpoint state (the only handle for the next
# checkpoint's reduce base, since append-only entries are non-addressable).
# Removed on clean teardown. _mem_split_full buckets these; _mem_compose
# re-emits them; the floor never relocates them; Leg C is told to preserve
# them verbatim.
_MEM_WM_SUMMARY_CAP = 600  # bound the marker's line length in the auto-loaded file


def _wm_line(rec):
    """Build the single physical marker line for a watermark record.

    rec keys: session_id, claude_session_id, transcript_path, byte_offset,
    slice_hash, running_summary. running_summary is sanitized to stay on one
    line and not prematurely close the HTML comment.
    """
    sid = str(rec.get('session_id', ''))
    safe = dict(rec)
    rs = str(safe.get('running_summary', '') or '')
    rs = rs.replace('\n', ' ').replace('\r', ' ').replace('-->', '—>')
    safe['running_summary'] = rs[:_MEM_WM_SUMMARY_CAP]
    js = json.dumps(safe, separators=(',', ':'), ensure_ascii=False)
    return f"{_MEM_WM_PREFIX}{sid} {js} -->"


def _wm_parse(line):
    """Parse a marker line back to a record dict, or None if malformed."""
    line = (line or '').strip()
    if not line.startswith(_MEM_WM_PREFIX) or not line.endswith(' -->'):
        return None
    core = line[len(_MEM_WM_PREFIX):].rsplit(' -->', 1)[0]
    sp = core.split(' ', 1)
    if len(sp) != 2:
        return None
    try:
        rec = json.loads(sp[1])
        return rec if isinstance(rec, dict) else None
    except Exception:
        return None


def _wm_find(wm_markers, session_id):
    """Return the parsed record for session_id from a wm_markers list, or None."""
    for ln in wm_markers or []:
        r = _wm_parse(ln)
        if r and str(r.get('session_id', '')) == str(session_id):
            return r
    return None


def _wm_upsert(wm_markers, rec):
    """Return a new wm_markers list with rec's session replaced (or appended)."""
    sid = str(rec.get('session_id', ''))
    kept = [ln for ln in (wm_markers or [])
            if (_wm_parse(ln) or {}).get('session_id') != sid]
    kept.append(_wm_line(rec))
    return kept


def _wm_remove(wm_markers, session_id):
    """Return a new wm_markers list without session_id's marker (teardown)."""
    sid = str(session_id)
    return [ln for ln in (wm_markers or [])
            if (_wm_parse(ln) or {}).get('session_id') != sid]


def _memory_search(project, query, topk=3):
    """Ranked-grep over the project's memory corpus (SPEC §3 Leg B).

    Corpus = the memory dir's topic *.md files + MEMORY_ARCHIVE.md entries +
    the MANAGED region of MEMORY.md. The curated MEMORY.md index is excluded
    by construction — the agent already auto-loads it. Deterministic, no
    model. Returns [{file, score, snippet}] sorted by score desc.
    """
    import re  # module has no top-level `re` import (see _re_auth pattern)
    terms = [t for t in re.findall(r'[a-z0-9_]+', (query or '').lower())
             if len(t) >= 3]
    if not terms:
        return []
    try:
        mem_path = _get_memory_path(project)
        mem_dir = mem_path.parent
    except Exception:
        return []
    if not mem_dir.is_dir():
        return []
    mem_name = mem_path.name
    arch_name = _get_archive_path(project).name
    units = []  # (label, text)
    for f in sorted(mem_dir.glob('*.md')):
        try:
            txt = f.read_text(encoding='utf-8', errors='replace')
        except Exception:
            continue
        if f.name == mem_name:
            for e in _mem_split(txt)[1]:           # managed entries only
                units.append((f'{f.name}#managed', e))
        elif f.name == arch_name:
            for ln in txt.splitlines():
                if ln.strip().startswith('- ['):
                    units.append((f.name, ln.strip()))
        else:
            units.append((f.name, txt))            # topic file (whole)
    scored = []
    for label, text in units:
        low = text.lower()
        score = sum(low.count(t) for t in terms)
        if score <= 0:
            continue
        if any(t in label.lower() for t in terms):
            score += 2                              # filename relevance bonus
        pos = min((low.find(t) for t in terms if t in low), default=0)
        start = max(0, pos - 120)
        snip = text[start:start + 400].replace('\n', ' ').strip()
        scored.append({'file': label, 'score': score, 'snippet': snip})
    scored.sort(key=lambda r: r['score'], reverse=True)
    return scored[:max(1, topk)]


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


# Built-in re-declaration for the engram memory plugin. engram is a *plugin*
# (settings.json enabledPlugins), NOT an mcpServers entry — so --strict-mcp-config
# drops it along with everything else. Re-declaring it here lets a trimmed project
# keep memory. The `engram` binary is on PATH, so this spec is stable across
# plugin-version bumps (verified 2026-06-03 against CC 2.1.158: this exact spec
# restores mcp__engram__* under --strict-mcp-config). Mirrors the plugin's own
# .mcp.json.
_ENGRAM_MCP_SPEC = {'command': 'engram', 'args': ['mcp', '--tools=agent']}


def _mcp_server_catalog(project):
    """Merge every MCP server this project *could* load into {name: spec}.

    Sources, later overriding earlier on name collision:
      1. global  ~/.claude.json  → mcpServers
      2. project <project_path>/.mcp.json → mcpServers
      3. built-in `engram` re-declaration (so trimming can keep memory)

    Best-effort: an unreadable / malformed source is skipped, never raised."""
    catalog = {}
    try:
        gp = Path.home() / '.claude.json'
        if gp.exists():
            g = json.loads(gp.read_text(encoding='utf-8'))
            for k, v in (g.get('mcpServers') or {}).items():
                if isinstance(v, dict):
                    catalog[k] = v
    except Exception:
        pass
    try:
        ppath = (project or {}).get('project_path')
        if ppath:
            mp = Path(ppath) / '.mcp.json'
            if mp.exists():
                pj = json.loads(mp.read_text(encoding='utf-8'))
                for k, v in (pj.get('mcpServers') or {}).items():
                    if isinstance(v, dict):
                        catalog[k] = v
    except Exception:
        pass
    catalog.setdefault('engram', dict(_ENGRAM_MCP_SPEC))
    return catalog


def _resolve_project_mcp_config(project):
    """Per-project MCP trimming → a `--mcp-config` JSON string, or None.

    None → the project did NOT opt in (`enabled_mcp_servers` absent or not a
           list): no flags are emitted and the session inherits the full
           global+project+plugin fleet, byte-identical to pre-trim behavior.
           This is the default-off invariant — most projects hit this path.
    str  → opt-in. A JSON `{"mcpServers": {…}}` naming exactly the selected
           servers, paired by build_command with `--strict-mcp-config`. An
           empty selection → `{"mcpServers": {}}` (loads nothing — a valid
           maximal trim). Names not in the catalog are logged and skipped.

    Best-effort: any failure returns None (fail-open to the full fleet) — MCP
    trimming must never break a dispatch."""
    try:
        sel = (project or {}).get('enabled_mcp_servers')
        if not isinstance(sel, list):
            return None  # not opted in → unchanged behavior
        catalog = _mcp_server_catalog(project)
        chosen = {}
        for name in sel:
            if name in catalog:
                chosen[name] = catalog[name]
            else:
                _log(f"[mcp-trim] {(project or {}).get('id', '?')}: unknown MCP "
                     f"server '{name}' skipped (catalog: {sorted(catalog)})",
                     level='warn')
        return json.dumps({'mcpServers': chosen})
    except Exception as e:
        _log(f"[mcp-trim] resolve failed ({e!r}); inheriting full fleet",
             level='warn')
        return None


def _build_claude_flags(project=None, streaming=False, model_override=None):
    """Build common Claude CLI flags from config, with optional per-project overrides.
    Delegates to ClaudeRuntime.build_command()[1:] — single source of truth.
    Returns flags only (no binary prefix), matching the legacy contract.

    `model_override` lets a caller force a specific model (e.g. picked by the
    auto-router via _resolve_dispatch_model). Pass-through when None — existing
    callers that don't know about routing keep their original behavior.
    """
    model = model_override or (
        (project or {}).get('agent_model', '') or CONFIG.get('agent_model', '')
    )
    effort = (project or {}).get('agent_effort', '') or CONFIG.get('agent_effort', '')
    return _agent_runtime.get_runtime('claude').build_command(
        model=model,
        max_turns=CONFIG.get('agent_max_turns', 0),
        streaming=streaming,
        perm_mode=CONFIG.get('agent_permission_mode', ''),
        channels=(project or {}).get('agent_channels', '') or CONFIG.get('agent_channels', ''),
        remote_control=bool(
            (project or {}).get('agent_remote_control', False) or
            CONFIG.get('agent_remote_control', False)
        ),
        effort=effort,
        mcp_config_json=_resolve_project_mcp_config(project) or '',
    )[1:]  # strip binary — _build_claude_flags() contract is flags-only


def _resolve_dispatch_model(project, prompt):
    """Pick the model to use for a user-facing dispatch, given the prompt.

    Returns (model_name, source) where source is one of:
      'manual'   — auto-router off; using the configured/project model verbatim
      'auto'     — classifier ran and picked this model
      'fallback' — classifier ran but errored; using the configured model

    Caller threads `model_name` into _build_claude_flags via `model_override`
    and surfaces a per-bubble pill when source != 'manual'. Side-effect free.

    Synchronous path. For parallel classification overlapping with
    `_build_agent_context`, use `_dispatch_with_routing_parallel`.
    """
    fallback = (project or {}).get('agent_model', '') or CONFIG.get('agent_model', '') or 'sonnet'
    if not prompt or not CONFIG.get('auto_model_enabled', False):
        return fallback, 'manual'
    return _route_dispatch_model(prompt, fallback)


# Background pool for parallel classifier calls during dispatch. Keeps the
# router off the dispatch thread's critical path so context build and
# classification overlap (see docs/DISPATCH_AND_ROUTING_ANALYSIS.md §B.3.b).
# Daemon threads so the pool never blocks shutdown.
_classifier_pool = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix='router-cls'
)


def _dispatch_with_routing(project, prompt, streaming=False):
    """One-shot helper: resolve model + build flags.

    Returns (model, source, flags). Caller stamps session['model'] and
    session['model_source'] for SSE emission. Used by dispatch sites that
    don't have a separate expensive context-build step to parallelize with.
    """
    model, source = _resolve_dispatch_model(project, prompt)
    flags = _build_claude_flags(project, streaming=streaming, model_override=model)
    return model, source, flags


def _dispatch_with_routing_parallel(project, prompt, context_builder, streaming=False):
    """Same as `_dispatch_with_routing` but runs `context_builder` in parallel
    with the classifier when the router is on.

    `context_builder` is a zero-arg callable returning the context string
    (typically `lambda: _build_agent_context(p, incognito=…, task=…)`). Net
    latency add when router is on: ~0–500 ms (classifier vs. context overlap)
    instead of 600–1500 ms serially.

    Returns (model, source, flags, context). Caller stamps session fields.
    When router is off or prompt is empty, runs context_builder synchronously
    and short-circuits to the manual model.
    """
    if not CONFIG.get('auto_model_enabled', False) or not prompt:
        context = context_builder() if context_builder else ''
        model, source, flags = _dispatch_with_routing(project, prompt, streaming=streaming)
        return model, source, flags, context, ''

    fallback = (project or {}).get('agent_model', '') or CONFIG.get('agent_model', '') or 'sonnet'
    fut = _classifier_pool.submit(_route_dispatch_model, prompt, fallback)
    context = context_builder() if context_builder else ''
    timeout = max(1, int(CONFIG.get('auto_model_classifier_timeout_secs', 8) or 8))
    _fallback_reason = ''
    try:
        model, source = fut.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        model, source = fallback, 'fallback'
        _fallback_reason = 'timeout'
    except Exception as _exc:
        model, source = fallback, 'fallback'
        _fallback_reason = type(_exc).__name__
    flags = _build_claude_flags(project, streaming=streaming, model_override=model)
    return model, source, flags, context, _fallback_reason


def _sysprompt_file_args(context):
    """Return (cli_args, tmp_path) for passing a system prompt via a temp file.

    On Windows, npm-installed `claude.cmd` is invoked through cmd.exe, which
    enforces an 8191-char command-line cap (vs. CreateProcess's 32 KB). A
    multi-KB context (CLAYRUNE_API_REFERENCE + rules + read-floor + recent
    activity) passed inline as `--append-system-prompt <context>` blows past
    that cap and the spawn fails with "The command line is too long" + rc=1.

    The hidden `--append-system-prompt-file <path>` flag is exactly the
    escape hatch — Claude CLI supports it (verified locally; absent from
    `claude --help` but documented in `--bare`'s help text). The temp file
    is created here; the caller MUST call _sysprompt_cleanup(path, proc)
    right after Popen so the file is deleted when the process exits.

    Returns ([], None) for empty/missing context so callers can splat
    unconditionally.
    """
    if not context:
        return [], None
    import tempfile
    fd, path = tempfile.mkstemp(prefix='clayrune-sysprompt-', suffix='.txt')
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(context)
    except Exception:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    return ['--append-system-prompt-file', path], path


def _sysprompt_cleanup(path, proc):
    """Schedule deletion of the temp system-prompt file when `proc` exits.

    Daemon thread; survives until the process actually terminates so we
    never delete the file out from under claude.cmd mid-startup. No-op if
    `path` is None (empty context).
    """
    if not path:
        return
    def _wait_and_unlink():
        try:
            proc.wait()
        except Exception:
            pass
        try:
            os.unlink(path)
        except OSError:
            pass
    threading.Thread(target=_wait_and_unlink, daemon=True,
                     name=f'sysprompt-cleanup').start()


# ── Agent session tracking ───────────────────────────────────────────────────
# agent_sessions moved to mc/state.py (Phase 0).


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


# _managers / _managers_lock moved to mc/state.py (Phase 0).


def get_manager(project_id):
    """Get or create the ProjectAgentManager for a project. Cheap to call."""
    with _managers_lock:
        m = _managers.get(project_id)
        if m is None:
            m = ProjectAgentManager(project_id)
            _managers[project_id] = m
    return m


# _mem_write_locks(+guard) + _get_mem_write_lock moved to mc/state.py;
# _atomic_write_text moved to mc/core.py (Phase 0). SPEC §3.A.MID lock
# ordering + atomicity rationale documented at the definitions.


# _harden_secret_perms moved to mc/core.py (step 1.1).


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
                _log(f"[guardian:{manager.project_id[:8]}] Error checking {sid[:8]}: {e}")

# ── Memory condensation state ────────────────────────────────────────────────
# _condensing_projects / _condense_lock / _condense_triggered_at /
# _condense_status moved to mc/state.py (Phase 0).


def _condense_combined_bytes(project):
    """Combined size of a project's MEMORY.md + archive (0 if absent)."""
    total = 0
    for p in (_get_memory_path(project), _get_archive_path(project)):
        try:
            if p and p.exists():
                total += p.stat().st_size
        except OSError:
            pass
    return total


def _set_condense_status(pid, **kw):
    with _condense_lock:
        cur = _condense_status.get(pid, {})
        cur.update(kw)
        _condense_status[pid] = cur


def _get_condense_status(pid):
    with _condense_lock:
        st = _condense_status.get(pid)
        return dict(st) if st else {'state': 'idle'}

# _scribing_projects / _scribe_lock moved to mc/state.py (Phase 0).


def _has_running_agent(project_id):
    """Return True if any non-housekeeping agent is running or idle for this project."""
    for s in agent_sessions.values():
        if s.get('project_id') == project_id and not s.get('housekeeping'):
            if s.get('status') in ('running', 'idle'):
                return True
    return False


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
        # Cooldown: don't re-trigger within 1 hour of the last dispatch. This
        # prevents the pre-dispatch check from firing on back-to-back sessions
        # when CLAUDE.md + MEMORY.md keep the total above threshold while the
        # previous condense job is still running or just finished.
        _cooldown = int(CONFIG.get('condense_cooldown_secs', 3600) or 3600)
        if _time.time() - _condense_triggered_at.get(pid, 0) < _cooldown:
            return False
    # Skip running-agent check when called from pre-dispatch (agent hasn't started yet)
    if not include_claude_md and _has_running_agent(pid):
        return False
    # The structured executor is line-keyed and only ever acts on MEMORY.md's
    # managed region. Trigger it on the auto-loaded file's LINE count vs. the
    # model-tier budget — NOT on combined bytes. Byte-keying would let a large
    # CLAUDE.md (which structured deliberately doesn't touch) keep the trigger
    # permanently hot, firing a no-op model call every session-end. This also
    # makes the structured trigger and its target agree in units (closes
    # docs/CONDENSE_STRUCTURED_DESIGN.md Open Question #5). The legacy agent
    # path keeps its existing combined-byte trigger below, unchanged.
    if (CONFIG.get('condense_mode', 'agent') or 'agent') == 'structured':
        mem_path = _get_memory_path(project)
        if not mem_path.exists():
            return False
        try:
            n_lines = len(mem_path.read_text(encoding='utf-8').splitlines())
        except Exception:
            return False  # a trigger check must never raise
        return n_lines > int(CONFIG.get('index_line_budget', 160) or 160)
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


# ── Terminal session tracking + process tracker ──────────────────────────────
# terminal_sessions / terminal_lock / tracked_processes /
# process_tracker_lock moved to mc/state.py (Phase 0).


def _register_process(proc, name, proc_type, session_id, project_id, command_preview=''):
    """Register a spawned process in the PID tracker."""
    project_name = project_id
    try:
        p = load_project(project_id)
        if p:
            project_name = p.get('name', project_id)
    except Exception:
        pass
    _img, _ct = _proc_identity(proc.pid)
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
            'os_image': _img,
            'create_time': _ct,
            'proc': proc,
        }
    _persist_pid_ledger()


def _unregister_process(pid):
    """Remove a process from the PID tracker."""
    with process_tracker_lock:
        tracked_processes.pop(pid, None)
    _persist_pid_ledger()


# ── MC-spawned child PID ledger + startup orphan reaper ──────────────────────
# server.py restarts by re-exec'ing via os._exit(): any child not killed inside
# the bounded graceful-stop window is orphaned, and the new instance never knew
# its PIDs (tracked_processes is in-memory only). Net effect: claude.exe + their
# MCP-server trees (node/cmd/engram) leak across every restart/crash. We persist
# the live child PIDs to a ledger and, at the next startup, reap any that are
# STILL alive AND still the same process (image-name + creation-time guard
# defeats PID reuse, so we can never friendly-fire an unrelated process).
# Everything here is best-effort: it never raises, never blocks a spawn or
# startup, and degrades to a no-op if identity can't be confirmed. [2026-06-03]
_PID_LEDGER_PATH = _DATA_ROOT / 'data' / 'mc_child_pids.json'


def _proc_identity(pid):
    """Return (image_basename_lower, creation_epoch_float) for a live PID, or
    (None, None) if it can't be read. Dependency-free ctypes on Windows so the
    reaper works without psutil; psutil elsewhere. Used purely as a PID-reuse
    guard — a failure here just means "can't confirm", which is treated as
    "don't reap"."""
    if sys.platform == 'win32':
        try:
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.windll.kernel32
            k32.OpenProcess.restype = wintypes.HANDLE
            k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not h:
                return (None, None)
            try:
                name = None
                buf = ctypes.create_unicode_buffer(32768)
                size = wintypes.DWORD(32768)
                if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                    name = buf.value.rsplit('\\', 1)[-1].lower()
                ct = None
                creation, exit_, kern, user = (wintypes.FILETIME(), wintypes.FILETIME(),
                                               wintypes.FILETIME(), wintypes.FILETIME())
                if k32.GetProcessTimes(h, ctypes.byref(creation), ctypes.byref(exit_),
                                       ctypes.byref(kern), ctypes.byref(user)):
                    ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
                    # FILETIME = 100ns ticks since 1601-01-01 → unix epoch seconds.
                    ct = ticks / 1e7 - 11644473600.0
                return (name, ct)
            finally:
                k32.CloseHandle(h)
        except Exception:
            return (None, None)
    else:
        try:
            import psutil
            p = psutil.Process(int(pid))
            return (p.name().lower(), float(p.create_time()))
        except Exception:
            return (None, None)


def _persist_pid_ledger():
    """Snapshot the live tracked-process PIDs to disk (atomic, best-effort).
    Called after every register/unregister; read once at the next startup by
    _reap_prior_instance_strays(), then cleared. Lives in data/ (NOT
    data/projects/) so load_projects() never sees it."""
    try:
        with process_tracker_lock:
            entries = [{
                'pid': e.get('pid'),
                'name': e.get('name', ''),
                'type': e.get('type', ''),
                'os_image': e.get('os_image'),
                'create_time': e.get('create_time'),
            } for e in tracked_processes.values()]
        _atomic_write_text(_PID_LEDGER_PATH, json.dumps(
            {'mc_pid': os.getpid(), 'written_at': now_iso(), 'children': entries}))
    except Exception:
        pass  # ledger is best-effort; a write failure must never break a spawn


def _should_reap_entry(entry, live_image, live_ct):
    """Pure predicate: should the startup reaper kill this ledgered PID?

    Reap ONLY if the PID is still the same process MC spawned — guarded by an
    exact image-name match and, when both sides have it, a creation-time match
    (within 2s). A reused PID (different image, or a creation time newer than
    recorded) is skipped. Missing identity on either side → do not reap."""
    rec_img = (entry.get('os_image') or '')
    if not rec_img or not live_image:
        return False
    if rec_img.lower() != live_image.lower():
        return False
    rec_ct = entry.get('create_time')
    if rec_ct is not None and live_ct is not None:
        if abs(float(rec_ct) - float(live_ct)) > 2.0:
            return False
    return True


def _reap_prior_instance_strays():
    """Startup: kill child process trees orphaned by a prior MC instance that
    exited (restart/crash) without tearing them down. Reads the prior instance's
    PID ledger, reaps anything still alive AND still the same process, then
    clears the ledger. Best-effort; never blocks startup."""
    try:
        if not _PID_LEDGER_PATH.exists():
            return
        data = json.loads(_PID_LEDGER_PATH.read_text(encoding='utf-8'))
    except Exception:
        return
    me = os.getpid()
    prior_mc = data.get('mc_pid')
    reaped = 0
    for entry in (data.get('children') or []):
        try:
            pid = int(entry.get('pid'))
        except Exception:
            continue
        if pid == me or pid == prior_mc or not _pid_is_alive(pid):
            continue
        live_image, live_ct = _proc_identity(pid)
        if not _should_reap_entry(entry, live_image, live_ct):
            continue
        if _kill_pid(pid, tree=True):
            reaped += 1
    try:
        if reaped:
            _log(f"[reaper] killed {reaped} orphaned child tree(s) from a prior MC "
                 f"instance (was PID {prior_mc})")
        _atomic_write_text(_PID_LEDGER_PATH, json.dumps(
            {'mc_pid': me, 'written_at': now_iso(), 'children': []}))
    except Exception:
        pass


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
            _decorate_attachments(p)
            projects.append(p)
        except Exception as e:
            _log(f"Error loading {f}: {e}")
    projects.sort(key=lambda p: (p.get('display_order', 9999), p.get('last_updated', '1970-01-01T00:00:00Z')))
    # Secondary sort: within same display_order, most recently updated first
    projects.sort(key=lambda p: p.get('last_updated', '1970-01-01T00:00:00Z'), reverse=True)
    projects.sort(key=lambda p: p.get('display_order', 9999))
    return projects


# time_ago / now_iso / file_type moved to mc/core.py (Phase 0).


# ── Asset serving (mascot icon, etc.) ────────────────────────────────────────

@app.route('/assets/<path:filename>')
def serve_asset(filename):
    """Serve files from the assets/ dir (Claydo mascot, etc.).

    Uses _APP_DIR so it resolves both in dev (repo root) and in a frozen
    PyInstaller bundle (sys._MEIPASS), where assets/ is bundled via the
    build spec's datas. Path(__file__).parent would point into the PYZ
    archive in the frozen app and 404 → broken images in the UI."""
    assets_dir = _APP_DIR / 'assets'
    return send_from_directory(str(assets_dir), filename)


# ── Marketing-site preview (dev convenience) ─────────────────────────────────
# Extracted to marketing_preview.py (P1-1 Tier 1a). Routes /marketing/ and
# /marketing/<path> are unchanged — see that module's docstring. `app`
# exists here (created above), so the blueprint registers at import time.
_marketing_preview.register(app)


# ── "Ask Claydo" guide assistant + walkthrough + scribe/memory reads ──
# extracted to mc/blueprints/guide_routes.py (1.9): the 2 /api/guide/* routes
# with the Claydo subprocess glue, /api/walkthrough/sample-project (+ the
# README/AGENT_RULES seed helpers), /api/project/<id>/scribe-stats (telemetry
# read — Scribe machinery stays below, untouched), and
# /api/project/<id>/memory/search (read-only retrieval). wire() late-binds
# load_project/save_project (projects family, 1.11), _memory_search +
# _resolve_claude + the Popen consts (dispatch/memory family — _memory_search
# is shared with the read floor in _build_agent_context, so the fn stays here
# until 1.12), DATA_DIR, and the server-dir anchor (Path(__file__).parent
# evaluated HERE — the 1.7/1.8 wired-placeholder pattern: data/claydo,
# docs/USER_GUIDE.md and CHANGELOG.md resolve from the repo root, not
# mc/blueprints/).
from mc.blueprints import guide_routes as _bp_guide  # noqa: E402

_bp_guide.wire(
    load_project_fn=load_project,
    save_project_fn=save_project,
    data_dir=DATA_DIR,
    memory_search_fn=_memory_search,
    resolve_claude_fn=_resolve_claude,
    popen_flags=_POPEN_FLAGS,
    startupinfo=_STARTUPINFO,
    server_dir=Path(__file__).parent,
)
app.register_blueprint(_bp_guide.bp)


# ── Project endpoints ────────────────────────────────────────────────────────

@app.route('/api/projects')
def api_projects():
    projects = load_projects()
    for p in projects:
        p['last_updated_relative'] = time_ago(p.get('last_updated'))
        p['live_agent'] = _project_live_agent(p.get('id'))
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


# ── Scribe telemetry (SPEC §8) ── /scribe-stats moved to mc/blueprints/guide_routes.py (1.9).


# ── Phase 4 Distiller endpoints ── extracted to
# mc/blueprints/distiller_routes.py (1.5). wire() late-binds projects-family
# accessors until 1.11.
from mc.blueprints import distiller_routes as _bp_distiller  # noqa: E402

_bp_distiller.wire(load_project_fn=load_project, data_dir=DATA_DIR)
app.register_blueprint(_bp_distiller.bp)




@app.route('/api/router/stats', methods=['GET'])
def get_router_stats_aggregate():
    """Cross-project auto-router counters. Sums totals and by_pair across
    every project's _router_stats.json. Surfaces last_fallback as the most
    recent across projects. Read-only; never mutates state.

    Response shape:
      {
        "totals": {"manual": N, "auto": N, "fallback": N},
        "by_pair": {"opus->haiku": N, ...},
        "last_fallback": {"ts": "...Z", "reason": "...", "project_id": "..."},
        "projects": N            # how many had a stats file
      }
    """
    agg_totals = {}
    agg_by_pair = {}
    last_fb = None
    project_count = 0
    for f in DATA_DIR.glob('*_router_stats.json'):
        try:
            data = json.loads(f.read_text(encoding='utf-8') or '{}')
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        project_count += 1
        for k, v in (data.get('totals') or {}).items():
            agg_totals[k] = int(agg_totals.get(k, 0)) + int(v or 0)
        for k, v in (data.get('by_pair') or {}).items():
            agg_by_pair[k] = int(agg_by_pair.get(k, 0)) + int(v or 0)
        fb = data.get('last_fallback')
        if isinstance(fb, dict) and fb.get('ts'):
            if last_fb is None or fb['ts'] > last_fb.get('ts', ''):
                # Derive project_id from the filename suffix-strip.
                pid = f.name[:-len('_router_stats.json')]
                last_fb = {**fb, 'project_id': pid}
    return jsonify({
        'totals': agg_totals,
        'by_pair': agg_by_pair,
        'last_fallback': last_fb,
        'projects': project_count,
    })


# /api/project/<id>/memory/search ── moved to mc/blueprints/guide_routes.py (1.9).


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


# ── Walkthrough onboarding project ── moved to mc/blueprints/guide_routes.py (1.9).


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


# ── Code sync endpoints (spike — read-only) ─────────────────────────────────

@app.route('/api/project/<project_id>/code-sync/enable', methods=['POST'])
def code_sync_enable(project_id):
    """Turn on code sync for a project. Creates the hidden worktree on
    the sync branch and pushes it to the remote (best-effort)."""
    if load_project(project_id) is None:
        return jsonify({'error': 'not found'}), 404
    ok, msg = _proj_sync.enable(project_id)
    if not ok:
        return jsonify({'error': msg}), 400
    return jsonify({'ok': True, 'message': msg})


@app.route('/api/project/<project_id>/code-sync/disable', methods=['POST'])
def code_sync_disable(project_id):
    if load_project(project_id) is None:
        return jsonify({'error': 'not found'}), 404
    ok, msg = _proj_sync.disable(project_id)
    if not ok:
        return jsonify({'error': msg}), 400
    return jsonify({'ok': True, 'message': msg})


@app.route('/api/project/<project_id>/code-sync/sync', methods=['POST'])
def code_sync_sync_now(project_id):
    if load_project(project_id) is None:
        return jsonify({'error': 'not found'}), 404
    ok, summary = _proj_sync.sync_now(project_id)
    if not ok:
        return jsonify({'error': summary}), 429 if 'rate limited' in summary else 400
    return jsonify({'ok': True, 'summary': summary})


@app.route('/api/project/<project_id>/code-sync/status')
def code_sync_status(project_id):
    p = load_project(project_id)
    if p is None:
        return jsonify({'error': 'not found'}), 404
    return jsonify(_proj_sync.compute_status(p))


@app.route('/api/project/<project_id>/code-sync/dismiss', methods=['POST'])
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
        val = CONFIG.get(key, 0)
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


@app.route('/api/attachments/<stored_name>')
def serve_attachment(stored_name):
    """Serve an attachment file."""
    safe = Path(stored_name).name  # prevent path traversal
    att_path = UPLOADS_DIR / safe
    if not att_path.exists():
        abort(404)
    return send_file(str(att_path), as_attachment=False)


_IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp',
               '.svg', '.ico', '.tif', '.tiff', '.avif'}


@app.route('/api/serve-image')
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

# Anthropic's many-image request limit: each image's long edge must be ≤ 2000px,
# else the API drops it with "an image in the conversation could not be
# processed and was removed." Shrinking on save keeps the original aspect ratio
# and saves the agent from tripping the limit later in the conversation.
_UPLOAD_IMAGE_MAX_EDGE = 2000


def _downscale_image_if_huge(path: Path) -> dict:
    """Resize an on-disk image to ≤ _UPLOAD_IMAGE_MAX_EDGE on the long edge.

    Best-effort: returns {'resized': bool, 'original': (w,h), 'final': (w,h)}
    on success, or {'resized': False, 'error': str} if Pillow is missing,
    the file isn't a recognized image, or the resize fails. Never raises —
    the upload itself must not fail because of an opportunistic shrink.
    """
    try:
        from PIL import Image  # local import: Pillow is optional
    except ImportError:
        return {'resized': False, 'error': 'pillow_missing'}
    try:
        with Image.open(path) as im:
            orig = im.size
            w, h = orig
            if max(w, h) <= _UPLOAD_IMAGE_MAX_EDGE:
                return {'resized': False, 'original': orig, 'final': orig}
            im.thumbnail((_UPLOAD_IMAGE_MAX_EDGE, _UPLOAD_IMAGE_MAX_EDGE),
                         Image.LANCZOS)
            final = im.size
            # Preserve original format (PNG stays PNG, JPEG stays JPEG, …)
            save_kwargs = {}
            fmt = (im.format or '').upper()
            if fmt in ('JPEG', 'JPG'):
                save_kwargs['quality'] = 90
                save_kwargs['optimize'] = True
            elif fmt == 'PNG':
                save_kwargs['optimize'] = True
            im.save(path, **save_kwargs)
            return {'resized': True, 'original': orig, 'final': final}
    except Exception as e:
        return {'resized': False, 'error': f'{type(e).__name__}: {e}'}


@app.route('/api/agent/upload-image', methods=['POST'])
def agent_upload_image():
    """Save a pasted image and return its absolute path for agent consumption."""
    if 'file' not in request.files:
        return jsonify({'error': 'no file'}), 400
    f = request.files['file']
    if not f.filename:
        return jsonify({'error': 'empty filename'}), 400
    # P2-2: per-file cap only — this endpoint has no project context for a
    # cumulative quota. 0 = unlimited (default).
    max_file = _upload_limit(None, 'upload_max_file_bytes')
    if max_file:
        incoming = _incoming_file_size(f)
        if incoming > max_file:
            return jsonify({'error': 'file too large',
                            'limit_bytes': max_file,
                            'file_bytes': incoming}), 413
    ext = Path(f.filename).suffix.lower() or '.png'
    stored_name = f'agent_{uuid.uuid4().hex[:10]}{ext}'
    dest = UPLOADS_DIR / stored_name
    f.save(str(dest))
    resize_info = _downscale_image_if_huge(dest)
    resp = {'ok': True, 'path': str(dest.resolve())}
    if resize_info.get('resized'):
        resp['resized_from'] = list(resize_info['original'])
        resp['resized_to'] = list(resize_info['final'])
    return jsonify(resp)



# ── Claude CLI auth-status tracking ───────────────────────────────────────────
# Sentinels emitted by `claude` when the user isn't logged in or the stored
# OAuth/api credentials are stale. Detected on every agent-stream line; a hit
# flips a global flag the dashboard polls so we can surface a "sign in" banner
# instead of letting the user stare at a silent 401.
import re as _re_auth
_AUTH_ERROR_PATTERNS = [
    (_re_auth.compile(r'please\s+run\s*/login', _re_auth.I), 'not_logged_in'),
    (_re_auth.compile(r'not\s+logged\s+in', _re_auth.I), 'not_logged_in'),
    (_re_auth.compile(r'invalid\s+(?:api\s+)?key', _re_auth.I), 'invalid_api_key'),
    (_re_auth.compile(r'authentication_error', _re_auth.I), 'unknown'),
]
# _claude_auth_state / _claude_auth_lock moved to mc/state.py (Phase 0).


def _scan_for_auth_error(text):
    """Return the reason code for the first matching sentinel, or None."""
    if not text:
        return None
    for pat, reason in _AUTH_ERROR_PATTERNS:
        if pat.search(text):
            return reason
    return None


def _mark_claude_auth_error(reason, snippet):
    with _claude_auth_lock:
        _claude_auth_state['ok'] = False
        _claude_auth_state['reason'] = reason
        _claude_auth_state['last_error_text'] = (snippet or '')[:300]
        _claude_auth_state['detected_at'] = _time.time()


def _mark_claude_auth_ok():
    with _claude_auth_lock:
        _claude_auth_state['ok'] = True
        _claude_auth_state['reason'] = None
        _claude_auth_state['last_error_text'] = None
        _claude_auth_state['last_probe_at'] = _time.time()


# ── Multi-provider agent runtime — discovery endpoint ───────────────────────


@app.route('/api/agent/providers')
def agent_providers():
    """List all registered agent runtimes (claude + alternatives) with their
    install / capability state. The project-settings UI reads this to build
    the per-project provider dropdown.

    Returns: [{name, display_name, installed, version, install_hint,
               capabilities: {...}, default: bool}]
    """
    out = []
    default_name = _agent_runtime.default_runtime_name()
    for rt in _agent_runtime.available_runtimes():
        try:
            h = rt.health_check()
        except Exception as e:
            h = _agent_runtime.HealthStatus(
                installed=False, binary_path=None, version=None,
                auth_state=_agent_runtime.AuthState(status='unknown', last_checked=''),
                install_hint='', diagnostic=str(e),
            )
        try:
            caps = rt.capabilities()
            caps_dict = {
                'supports_mode_a': caps.supports_mode_a,
                'supports_mode_b': caps.supports_mode_b,
                'mode_b_kind': caps.mode_b_kind,
                'default_mode': caps.default_mode,
                'supports_session_resume': caps.supports_session_resume,
                'supports_mcp': caps.supports_mcp,
                'supports_skills': caps.supports_skills,
                'supports_plan_mode': caps.supports_plan_mode,
                'supports_ask_user_question': caps.supports_ask_user_question,
                'supports_streaming_text': caps.supports_streaming_text,
                'emits_usage': caps.emits_usage,
                'emits_cost': caps.emits_cost,
                'emits_num_turns': caps.emits_num_turns,
                'emits_rate_limit': caps.emits_rate_limit,
                'image_input': caps.image_input,
                'context_window': caps.context_window,
                'context_injection': caps.context_injection,
                'context_file_name': caps.context_file_name,
                'oneshot_supported': caps.oneshot_supported,
            }
        except Exception:
            caps_dict = {}
        out.append({
            'name': rt.name,
            'display_name': rt.display_name,
            'installed': h.installed,
            'binary_path': str(h.binary_path) if h.binary_path else None,
            'version': h.version,
            'install_hint': h.install_hint,
            'auth_status': h.auth_state.status if h.auth_state else 'unknown',
            'capabilities': caps_dict,
            'default': (rt.name == default_name),
        })
    return jsonify({'providers': out, 'default': default_name})


# ── Provider env-var storage (Gemini API key etc.) ──────────────────────────

PROVIDER_ENV_PATH = _DATA_ROOT / 'data' / 'provider_env.json'
# _provider_env_lock moved to mc/state.py (Phase 0).


def _load_provider_env_file() -> Dict[str, Dict[str, str]]:
    if not PROVIDER_ENV_PATH.exists():
        return {}
    try:
        return json.loads(PROVIDER_ENV_PATH.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _save_provider_env_file(data: Dict[str, Dict[str, str]]) -> None:
    PROVIDER_ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROVIDER_ENV_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    _harden_secret_perms(PROVIDER_ENV_PATH)


def _hydrate_provider_env_into_os() -> None:
    """Inject persisted provider env vars into os.environ so child agent
    processes inherit them. Shell-set vars win — we only fill blanks."""
    for _provider, kv in (_load_provider_env_file() or {}).items():
        if not isinstance(kv, dict):
            continue
        for k, v in kv.items():
            if k and v is not None and k not in os.environ:
                os.environ[k] = str(v)


_hydrate_provider_env_into_os()


@app.route('/api/agent/provider/<name>/auth')
def agent_provider_auth_status(name):
    """Re-probe one provider's install + auth state. Cheaper than the full
    /api/agent/providers list when the user just clicked Refresh on one row."""
    try:
        rt = _agent_runtime.get_runtime(name)
    except KeyError:
        return jsonify({'error': f'unknown provider {name}'}), 404
    try:
        h = rt.health_check()
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify({
        'name': name,
        'installed': h.installed,
        'version': h.version,
        'binary_path': str(h.binary_path) if h.binary_path else None,
        'auth_status': h.auth_state.status if h.auth_state else 'unknown',
        'auth_method': h.auth_state.method if h.auth_state else None,
        'auth_error_text': h.auth_state.error_text if h.auth_state else None,
        'install_hint': h.install_hint,
    })


@app.route('/api/agent/provider/<name>/env', methods=['POST'])
def agent_provider_set_env(name):
    """Save and inject one env var for a provider. Body: {key, value}.

    Value is persisted to data/provider_env.json AND written to os.environ
    so the next agent dispatch picks it up without an MC restart. The key
    must look like a normal env-var name (paranoid filter — no PATH writes
    via this surface)."""
    if name not in _agent_runtime._RUNTIMES:
        return jsonify({'error': f'unknown provider {name}'}), 404
    body = request.get_json(force=True, silent=True) or {}
    key = (body.get('key') or '').strip()
    value = body.get('value')
    if not key or value is None:
        return jsonify({'error': 'key and value required'}), 400
    if not key.replace('_', '').isalnum() or not key[0].isalpha():
        return jsonify({'error': 'invalid env var name'}), 400
    # Allowlist: only credentials-flavored names. Stops the surface from
    # being used to clobber PATH / HOME / USERPROFILE / etc.
    SAFE_SUFFIXES = ('_API_KEY', '_TOKEN', '_KEY', '_SECRET',
                     '_PROFILE', '_REGION', '_CREDENTIALS', '_ENDPOINT')
    if not any(key.upper().endswith(s) for s in SAFE_SUFFIXES):
        return jsonify({
            'error': 'env var name not allowed — must end in _API_KEY, '
                     '_TOKEN, _KEY, _SECRET, _PROFILE, _REGION, '
                     '_CREDENTIALS, or _ENDPOINT',
        }), 400

    val_str = str(value)
    with _provider_env_lock:
        data = _load_provider_env_file()
        data.setdefault(name, {})[key] = val_str
        _save_provider_env_file(data)
    if val_str:
        os.environ[key] = val_str
    else:
        # Empty value = clear the override (let shell env take over).
        os.environ.pop(key, None)
    return jsonify({'ok': True, 'key': key})


@app.route('/api/agent/provider/<name>/login-launch', methods=['POST'])
def agent_provider_login_launch(name):
    """Open the provider's CLI in a NEW OS terminal so the user can complete
    interactive login (OAuth flow for gemini, etc.). Same pattern as
    /api/claude/login-launch — needs a real TTY, not a piped subprocess.

    Preserved for backward compat; prefer /api/agent/<provider>/auth-login.
    """
    try:
        rt = _agent_runtime.get_runtime(name)
    except KeyError:
        return jsonify({'error': f'unknown provider {name}'}), 404
    bin_path = rt.resolve_binary()
    if not bin_path:
        return jsonify({'error': f'{name} CLI is not installed'}), 400
    err = _launch_terminal_for_binary(str(bin_path))
    if err:
        return jsonify({'error': err}), 500
    return jsonify({'ok': True})


def _run_claude_auth_probe() -> dict:
    """Run `claude -p ok` to actively probe auth and update _claude_auth_state.

    Extracted so both the legacy shim and the new generic /api/agent/claude/auth-probe
    share the same implementation. Returns a snapshot of _claude_auth_state.
    """
    try:
        cmd = [_resolve_claude(), '-p', 'ok', '--max-turns', '1']
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=20,
            creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
        )
        combined = (result.stdout or '') + (result.stderr or '')
        reason = _scan_for_auth_error(combined)
        if reason:
            _mark_claude_auth_error(reason, combined)
        elif result.returncode == 0:
            _mark_claude_auth_ok()
        else:
            with _claude_auth_lock:
                _claude_auth_state['last_probe_at'] = _time.time()
    except subprocess.TimeoutExpired:
        with _claude_auth_lock:
            _claude_auth_state['last_probe_at'] = _time.time()
    except FileNotFoundError:
        _mark_claude_auth_error('cli_not_found', 'claude CLI not on PATH')
    except Exception:
        with _claude_auth_lock:
            _claude_auth_state['last_probe_at'] = _time.time()
    with _claude_auth_lock:
        return dict(_claude_auth_state)


def _launch_terminal_for_binary(bin_str: str) -> Optional[str]:
    """Open `bin_str` in a new OS terminal for interactive auth flows.

    Returns None on success or an error string on failure. Callers return 500
    when this is non-None. A real TTY is required because provider CLIs like
    claude use /login which refuses to run inside a piped subprocess.
    """
    try:
        if sys.platform == 'win32':
            subprocess.Popen(
                f'start "" cmd /k "\"{bin_str}\""',
                shell=True,
                creationflags=getattr(subprocess, 'DETACHED_PROCESS', 0),
            )
        elif sys.platform == 'darwin':
            script = f'tell application "Terminal" to do script "{bin_str}"'
            subprocess.Popen(['osascript', '-e', script])
        else:
            for emu in ('x-terminal-emulator', 'gnome-terminal', 'konsole',
                        'xfce4-terminal', 'xterm'):
                if shutil.which(emu):
                    subprocess.Popen([emu, '-e', bin_str])
                    break
            else:
                return (f'No terminal emulator found. Run `{bin_str}` '
                        'manually in a terminal to sign in.')
        return None
    except Exception as e:
        return str(e)


# Wire claude auth hooks into AgentRuntime so generic routes share the same
# in-memory state. Must be at module level after _claude_auth_state and
# _run_claude_auth_probe are defined, before any request can be served.
_agent_runtime._CLAUDE_HOOKS['auth_status'] = lambda: dict(_claude_auth_state)
_agent_runtime._CLAUDE_HOOKS['auth_probe'] = _run_claude_auth_probe


# ── Generic /api/agent/<provider>/auth-* routes ──────────────────────────────


@app.route('/api/agent/<provider>/auth-status')
def agent_auth_status(provider):
    """Cheap cached auth state for any provider (no subprocess).

    For 'claude' the response shape is {ok, reason, last_error_text, detected_at,
    last_probe_at} — byte-identical to the legacy /api/claude/auth-status shim.
    Other providers return {ok, status, method, error_text, last_checked}.
    """
    try:
        rt = _agent_runtime.get_runtime(provider)
    except KeyError:
        return jsonify({'error': f'unknown provider: {provider}'}), 404
    return jsonify(rt.auth_status())


@app.route('/api/agent/<provider>/auth-probe', methods=['POST'])
def agent_auth_probe(provider):
    """Actively probe auth state for any provider. May spawn a subprocess.

    For 'claude' the payload is identical to the legacy /api/claude/auth-probe shim.
    """
    try:
        rt = _agent_runtime.get_runtime(provider)
    except KeyError:
        return jsonify({'error': f'unknown provider: {provider}'}), 404
    try:
        return jsonify(rt.auth_probe())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/agent/<provider>/auth-login', methods=['POST'])
def agent_auth_login(provider):
    """Open the provider CLI in a new OS terminal for interactive login.

    Requires a real TTY — MC's in-app terminal pop-out is a piped subprocess
    and most provider CLIs refuse interactive auth flows without a console.
    Equivalent to the legacy /api/claude/login-launch shim.
    """
    try:
        rt = _agent_runtime.get_runtime(provider)
    except KeyError:
        return jsonify({'error': f'unknown provider: {provider}'}), 404
    bin_path = rt.resolve_binary()
    if not bin_path:
        return jsonify({'error': f'{provider} CLI is not installed'}), 400
    err = _launch_terminal_for_binary(str(bin_path))
    if err:
        return jsonify({'error': err}), 500
    return jsonify({'ok': True})


@app.route('/api/agent/<provider>/auth-logout', methods=['POST'])
def agent_auth_logout(provider):
    """Revoke / clear stored credentials for a provider."""
    try:
        rt = _agent_runtime.get_runtime(provider)
    except KeyError:
        return jsonify({'error': f'unknown provider: {provider}'}), 404
    try:
        return jsonify(rt.auth_logout())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Legacy /api/claude/auth-* shims ──────────────────────────────────────────
# Kept indefinitely so Tauri launcher, mobile APK, and dashboards built before
# ws_007 updates the UI keep working with zero behavioral change.


@app.route('/api/claude/auth-status')
def claude_auth_status():
    """Backward-compat shim → /api/agent/claude/auth-status."""
    with _claude_auth_lock:
        return jsonify(dict(_claude_auth_state))


@app.route('/api/claude/login-launch', methods=['POST'])
def claude_login_launch():
    """Backward-compat shim → /api/agent/claude/auth-login."""
    return agent_auth_login('claude')


@app.route('/api/claude/auth-probe', methods=['POST'])
def claude_auth_probe():
    """Backward-compat shim → /api/agent/claude/auth-probe.

    Actively probes Claude CLI auth by running `claude -p ok --max-turns 1`.
    Only invoked when the user clicks 'Re-check' in the banner.
    """
    return agent_auth_probe('claude')


# ── Agent endpoints ──────────────────────────────────────────────────────────

def _clayrune_api_reference() -> str:
    """Return the pre-authored Clayrune API reference for agent system prompts.

    Sourced from `data/agent_reference/CLAYRUNE_API.md`. Injected once per
    session by `_build_agent_context()` and `_hm_build_worker_context()` so
    agents don't have to curl-probe endpoints at runtime. Anthropic's prompt
    cache covers the cost after the first turn.

    Returns an empty string if the file is missing — failure here must never
    break a session (mirrors the AGENT_RULES.md / SHARED_RULES.md posture).
    """
    try:
        path = _APP_DIR / 'data' / 'agent_reference' / 'CLAYRUNE_API.md'
        if path.exists():
            return path.read_text(encoding='utf-8')
    except Exception:
        pass
    return ''


_PLANS_DIR = Path.home() / '.claude' / 'plans'


def _is_plan_path(fp: str) -> bool:
    """True if fp is a .md file under ~/.claude/plans/.

    Headless-safe plan detection: ExitPlanMode hangs without a TTY (agents are
    told not to use it), so any markdown file written into ~/.claude/plans/ is
    registered as the session's plan instead. Feeds session['plan_file'] → the
    PLAN tab (/api/project/<id>/plans) and the in-chat plan link.
    """
    try:
        p = Path(fp)
        if p.suffix.lower() != '.md':
            return False
        p.resolve().relative_to(_PLANS_DIR.resolve())
        return True
    except Exception:
        return False


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
        # Plan mode hangs in headless Claude Code regardless of agent type, so
        # plans are captured as files instead (see _is_plan_path + the PLAN tab).
        "IMPORTANT — Plans: Do NOT use EnterPlanMode or ExitPlanMode — you run "
        "headless with no interactive terminal, so plan-mode approval hangs "
        "indefinitely. Instead, when you form a non-trivial plan: (1) describe it "
        "inline in your chat reply, AND (2) write the full plan as a markdown "
        "file into your home directory's .claude/plans/ folder (an absolute "
        "path, e.g. ~/.claude/plans/<short-descriptive-name>.md; create the "
        "folder if needed). Clayrune auto-detects plans written there and shows "
        "them in the project's PLAN tab with a link in the chat — there is no "
        "approval step, just keep working after you write the file.",

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
        f"inside THIS Clayrune environment (daily standups, weekly "
        f"reports, recurring cleanups, one-shots scheduled hours/days out). "
        f"List: GET http://localhost:{port}/api/schedules  "
        f"Create: POST http://localhost:{port}/api/schedules with "
        f"{{\"project_id\":\"...\",\"task\":\"...\",\"schedule_type\":\"daily|weekly|interval|once|cron\","
        f"\"time\":\"09:00\",\"days\":[],\"interval_minutes\":60,\"run_at\":\"ISO8601\",\"cron_expr\":\"...\"}}  "
        f"Update: PUT /api/schedules/<id>  Delete: DELETE /api/schedules/<id>.\n"
        f"  • Anthropic /schedule skill — for SHORT-INTERVAL polling/follow-ups "
        f"that live inside the CURRENT session lifespan (e.g. \"check the build "
        f"every 5 min\", \"poll this PR until merged\"). Cloud-side; cannot reach "
        f"local Clayrune state, but perfect for in-session tick work.\n"
        f"Rule of thumb: if it should still fire after this conversation ends, "
        f"use the Clayrune local scheduler; if it's a tight loop tied to the "
        f"work you're doing right now, use /schedule.",

        # API discovery hint — when an unfamiliar Clayrune feature is needed,
        # don't guess endpoint names; list them.
        f"API discovery: When you need a Clayrune feature you haven't used "
        f"before, do NOT guess endpoint names (e.g. /api/cron, /api/jobs). "
        f"Grep server.py for `@app.route` to enumerate the real endpoints, "
        f"or curl http://localhost:{port}/ and inspect the served HTML. "
        f"For the curated, current shape of the API, see the "
        f"'--- CLAYRUNE API REFERENCE ---' block in your system prompt.",

        # User-facing answers: when the user asks how to manage MCP/Skills/etc.,
        # they mean inside Clayrune, NOT via the upstream Claude CLI.
        "Management surfaces — you are running inside Clayrune, not bare Claude "
        "Code. MCP servers, Skills, Scheduler, Settings, and project Memory are "
        "all owned and managed by Clayrune via its UI (sidebar entries: MCP, "
        "Skills, Scheduler, Settings; Memory via the project modal). Underlying "
        "files: `~/.claude.json` (mcpServers), per-project `<project>/.mcp.json`, "
        "`~/.claude/skills/` and `<project>/.claude/skills/`, "
        "`~/.claude/settings.json`. When the user asks how to add, edit, or "
        "remove any of these, point them at the Clayrune UI surface — do NOT "
        "tell them to run `claude mcp add`, `claude skill ...`, or hand-edit the "
        "underlying JSON. If asked 'how do I X', assume X is a Clayrune action "
        "first; only fall back to upstream Claude CLI advice if Clayrune "
        "genuinely doesn't surface it.",

        # Leg B priming — name the skill so it's reached for at the right moment.
        f"Project memory: when you hit an unknown about this project's history, "
        f"a prior decision, or a convention, use the mc-memory-search skill "
        f"before guessing — it ranks the project's topic files, archive, and "
        f"session log. Relevant memory for the current task is also "
        f"auto-surfaced in your context under 'RELEVANT MEMORY'.",
    ]


def _skills_catalog_block(project):
    """Skill catalog for non-Claude agents (full-parity Stage 3).

    Claude Code auto-discovers skills from ~/.claude/skills/ and the project's
    .claude/skills/; other provider CLIs don't. So MC injects the catalog —
    each skill's name, description and SKILL.md path — into the system prompt.
    The agent reads the full SKILL.md with its own file tools when a task
    matches. Returns '' for Claude (native discovery) or when there are no
    skills. Best-effort: any failure yields '' — skills are never load-bearing.
    """
    if (project.get('provider') or 'claude').lower() == 'claude':
        return ''
    try:
        skills = _skills.list_skills(project.get('project_path') or None,
                                     project.get('id'))
    except Exception:
        return ''
    visible = [s for s in skills
               if s.get('scope') != 'archive'
               and not s.get('shadowed_by_project')]
    if not visible:
        return ''
    lines = []
    for s in visible:
        desc = (s.get('description') or '').strip().replace('\n', ' ')
        lines.append(f"- {s.get('name')}: {desc}\n  SKILL.md: {s.get('path')}")
    return ("--- AVAILABLE SKILLS ---\n"
            "Reusable skills are available to you. When the current task "
            "matches a skill's description, read its SKILL.md with your "
            "file tools and follow it.\n" + "\n".join(lines))


def _build_agent_context(project, incognito=False, task=''):
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
    # Non-Claude agents (Gemini etc.) get a slimmer context. Claude treats a
    # rich context dump as background; weaker models read prompt-history-shaped
    # sections (the MEMORY.md session log, recent conversations, recent
    # activity) as a TASK LIST and go off doing phantom work on a plain "Hi".
    # So those sections are Claude-only; non-Claude still gets the targeted
    # read-floor (RELEVANT MEMORY) which is small and task-scoped.
    _is_claude = (project.get('provider') or 'claude').lower() == 'claude'
    agent_name = CONFIG.get('agent_name', '')
    user_name = CONFIG.get('user_name', '')
    if agent_name:
        parts.append(f"Your name is {agent_name}.")
    if user_name:
        parts.append(f"The user's name is {user_name}. Address them accordingly.")
    # Sticky brevity: when sticky_agent_settings is on, the device-neutral brief
    # directive lives HERE (cached, once per spawn) instead of being prepended to
    # every user turn by _apply_mobile_brief. Flipping the toggle mid-session is
    # handled by the respawn-on-flip path (see update_config / agent_followup).
    if (CONFIG.get('sticky_agent_settings', False)
            and CONFIG.get('brief_replies_always_enabled', False)):
        parts.append(_BRIEF_REPLY_DIRECTIVE_SYSTEM)
    parts.append(f"You are working on {project.get('name', project['id'])}.")
    pp = project.get('project_path', '')
    if pp:
        parts.append(f"Project root: {pp}")

    if incognito:
        parts.append(
            "--- INCOGNITO MODE ---\n"
            "This is an incognito session. You can read everything about the project "
            "(rules, memory, recent activity, files) so you have full context to answer. "
            "However, Clayrune will NOT log this session to the agent log and will "
            "NOT append a summary to MEMORY.md on completion. Treat this as an off-the-record "
            "side conversation: do not modify MEMORY.md, AGENT_RULES.md, or SHARED_RULES.md "
            "and do not push commits unless the user explicitly asks. "
            "Note: Claude still writes a transcript to ~/.claude/projects/, so incognito "
            "hides this session from Clayrune surfaces, not from disk."
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
        "You are managed by Clayrune.",
        f"Memory: {mem_file} is auto-loaded and maintained for you by Clayrune. "
        f"To retrieve older context, search it; do NOT hand-edit it.",
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
        f"Backlog: This project has a Clayrune backlog (prioritized task list with notes, "
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

    # Stage 3 full-parity: non-Claude agents don't auto-discover skills —
    # inject the catalog so they can read + follow the relevant SKILL.md.
    _skills_block = _skills_catalog_block(project)
    if _skills_block:
        parts.append(_skills_block)

    # NOTE: the full MEMORY.md is NOT injected here for non-Claude agents.
    # An earlier attempt (Stage 4 "memory-in") dumped the whole index in —
    # but its Session Log is a wall of past prompts, which Gemini read as a
    # live task list. The targeted read-floor below ("RELEVANT MEMORY") is
    # the memory mechanism for every provider: small, task-scoped, safe.

    # Pre-authored Clayrune API reference — agents inside Clayrune used to
    # curl-probe endpoints every session. Injecting the curated reference
    # once eliminates that turn-cost; Anthropic's prompt cache makes it free
    # after the first turn.
    api_ref = _clayrune_api_reference()
    if api_ref:
        parts.append("--- CLAYRUNE API REFERENCE ---\n" + api_ref)

    # Leg B.3 — deterministic read floor (no model; ranked grep). The agent
    # already auto-loads the curated index; this surfaces relevant topic-file /
    # archive / session-log detail for THIS task so the read side never depends
    # solely on the probabilistic search skill. SPEC §3 Leg B.
    if task:
        try:
            hits = _memory_search(project, task,
                                  int(CONFIG.get('read_floor_topk', 3) or 3))
        except Exception:
            hits = []
        if hits:
            rl = "\n".join(f"  • [{h['file']}] {h['snippet']}" for h in hits)
            parts.append(
                "--- RELEVANT MEMORY (auto-surfaced for this task; "
                "use the mc-memory-search skill to dig deeper) ---\n" + rl)

    # Exploration read-floor — closes the learning loop by feeding the
    # Distiller's captured EXPLORATION.md proposals back into context. Without
    # this, _proposed/ explorations are write-only and never change behavior.
    # Best-effort, gated, and never load-bearing (same posture as the Distiller
    # write side). Skipped for incognito sessions (no memory leakage).
    if task and not incognito and CONFIG.get('exploration_readback_enabled', True):
        try:
            expl = _distiller.exploration_read_floor(
                project['id'], task,
                int(CONFIG.get('exploration_read_floor_topk', 2) or 2))
        except Exception:
            expl = []
        if expl:
            el = "\n".join(
                f"  • [{e['scope']}] {e['snippet']}  (full: {e['path']})"
                for e in expl)
            parts.append(
                "--- RELEVANT PAST EXPLORATIONS (a prior session already "
                "investigated something like this; read the full file before "
                "re-deriving) ---\n" + el)

    # Recent activity — Claude-only: a non-Claude agent reads these past
    # "Agent dispatched: <task>" lines as things it still has to do.
    log = project.get('activity_log', [])[:3]
    if log and _is_claude:
        lines = [f"  - {e.get('ts','')}: {e.get('msg','')}" for e in log]
        parts.append("Recent activity:\n" + "\n".join(lines))

    # Recent conversations — read directly from .jsonl transcripts so interrupted
    # sessions (never reached completion log) are still discoverable. Display the
    # LAST user message, not the first, since the first is usually a meta prompt
    # (context condensation, boot text) that the user won't recognize.
    # Claude-only: these are Claude transcripts, listed with `claude -r <id>`
    # resume hints. For a non-Claude agent they are both wrong (not its CLI)
    # and actively harmful — it reads them as "our last chat" and tries to
    # continue tasks from them.
    project_path = project.get('project_path', '')
    convos = (_recent_claude_transcripts(project_path, limit=5)
              if (project_path and _is_claude) else [])
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
    elif _is_claude:
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

# _backlog_sync_lock moved to mc/state.py (Phase 0).


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
                # Defensive: json.loads on a bare-string line returns a str
                # (e.g. a quoted error blob), which would crash msg.get()
                # below with `'str' object has no attribute 'get'` and kill
                # the reader. Treat any non-dict envelope as non-JSON noise.
                if not isinstance(msg, dict):
                    raise json.JSONDecodeError('non-dict JSON envelope', line, 0)
                msg_type = msg.get('type', '')
                # Capture Claude CLI session UUID from init or result messages
                if 'session_id' in msg:
                    _note_claude_sid(session, msg['session_id'])
                # Refresh the account-global system status cache from
                # `system/init` and `rate_limit_event` messages (every claude
                # session emits these). No-op for any other message type.
                _capture_system_init(msg)
                _mc_state._LAST_SYSTEM_STATUS['provider'] = session.get('provider', 'claude')
                if msg_type == 'assistant' and isinstance(msg.get('message'), dict):
                    # First assistant output proves a `-r` resume loaded OK (not a
                    # fragile resume that dies instantly), so a LATER process death
                    # (the Mode-B AskUserQuestion proc.kill(), idle-eviction, or a
                    # crash) re-resumes with -r instead of resetting to a context-
                    # less fresh session. Harmless for Mode A (never consulted).
                    # See _resume_is_fragile + the followup respawn path.
                    session['_resume_confirmed'] = True
                    for block in msg['message'].get('content', []) or []:
                        if not isinstance(block, dict):
                            continue
                        if block.get('type') == 'text':
                            session['log_lines'].append(block['text'])
                            session['last_output_time'] = _time.time()
                        elif block.get('type') == 'tool_use':
                            tool_name = block.get('name', '')
                            tool_input = block.get('input', {})
                            if not isinstance(tool_input, dict):
                                tool_input = {}
                            activity = _format_tool_activity(tool_name, tool_input)
                            session['log_lines'].append(activity)
                            session['last_output_time'] = _time.time()
                            # Track .md file edits for plan file detection
                            if tool_name in ('Write', 'Edit'):
                                fp = tool_input.get('file_path', '')
                                if fp.lower().endswith('.md'):
                                    session['_last_md_file'] = fp
                                    # A plan written into ~/.claude/plans/
                                    # registers immediately (headless-safe; no
                                    # ExitPlanMode). Feeds the PLAN tab + link.
                                    if _is_plan_path(fp):
                                        session['plan_file'] = fp
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
                                # Stable question_id so the SSE can re-emit the question
                                # to a late-connecting / reconnecting client and the
                                # client can dedupe by id (instead of dropping it).
                                _q = dict(tool_input)
                                _q['question_id'] = uuid.uuid4().hex
                                session.setdefault('pending_questions', []).append(_q)
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
                        _note_claude_sid(session, msg['session_id'])
                    # Accumulate token usage across turns (result fires once per turn
                    # in Mode B; overwriting would discard all prior turns).
                    if 'usage' in msg:
                        _accumulate_session_usage(session, msg['usage'])
                    if 'cost_usd' in msg:
                        session['cost_usd'] = (session.get('cost_usd') or 0.0) + (msg['cost_usd'] or 0.0)
                    if 'num_turns' in msg:
                        session['num_turns'] = msg['num_turns']
                    _auto_snapshot_notes_on_turn(session)
                # Web push hook: intercept PushNotification tool_use + turn results.
                _handle_push_signal(
                    session.get('project_id', ''),
                    session.get('session_id', ''),
                    msg,
                )
            except json.JSONDecodeError:
                # Non-JSON lines are claude's raw stderr — that's where real
                # auth-error sentinels appear ("Please run /login", "Invalid
                # API key"). Scanning stream-json lines was a false-positive
                # magnet: an agent's own assistant text discussing auth flows
                # ("the user might not be logged in") triggered the banner.
                _auth_reason = _scan_for_auth_error(line)
                if _auth_reason:
                    _mark_claude_auth_error(_auth_reason, line)
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
                # See Mode A reader: any non-dict JSON envelope crashes the
                # reader at msg.get() with `'str' object has no attribute
                # 'get'`. Treat as non-JSON noise instead.
                if not isinstance(msg, dict):
                    raise json.JSONDecodeError('non-dict JSON envelope', line, 0)
                msg_type = msg.get('type', '')
                if 'session_id' in msg:
                    _note_claude_sid(session, msg['session_id'])
                # See Mode A reader: refresh the system-status cache.
                _capture_system_init(msg)
                _mc_state._LAST_SYSTEM_STATUS['provider'] = session.get('provider', 'claude')
                if msg_type == 'assistant' and isinstance(msg.get('message'), dict):
                    # First assistant output proves a `-r` resume loaded OK (not a
                    # fragile resume that dies instantly), so a LATER process death
                    # (the Mode-B AskUserQuestion proc.kill(), idle-eviction, or a
                    # crash) re-resumes with -r instead of resetting to a context-
                    # less fresh session. Harmless for Mode A (never consulted).
                    # See _resume_is_fragile + the followup respawn path.
                    session['_resume_confirmed'] = True
                    for block in msg['message'].get('content', []) or []:
                        if not isinstance(block, dict):
                            continue
                        if block.get('type') == 'text':
                            session['log_lines'].append(block['text'])
                            session['last_output_time'] = _time.time()
                        elif block.get('type') == 'tool_use':
                            tool_name = block.get('name', '')
                            tool_input = block.get('input', {})
                            if not isinstance(tool_input, dict):
                                tool_input = {}
                            activity = _format_tool_activity(tool_name, tool_input)
                            session['log_lines'].append(activity)
                            session['last_output_time'] = _time.time()
                            if tool_name in ('Write', 'Edit'):
                                fp = tool_input.get('file_path', '')
                                if fp.lower().endswith('.md'):
                                    session['_last_md_file'] = fp
                                    # A plan written into ~/.claude/plans/
                                    # registers immediately (headless-safe; no
                                    # ExitPlanMode). Feeds the PLAN tab + link.
                                    if _is_plan_path(fp):
                                        session['plan_file'] = fp
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
                                _q = dict(tool_input)
                                _q['question_id'] = uuid.uuid4().hex
                                session.setdefault('pending_questions', []).append(_q)
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
                        _note_claude_sid(session, msg['session_id'])
                    if 'usage' in msg:
                        _accumulate_session_usage(session, msg['usage'])
                    if 'cost_usd' in msg:
                        session['cost_usd'] = (session.get('cost_usd') or 0.0) + (msg['cost_usd'] or 0.0)
                    if 'num_turns' in msg:
                        session['num_turns'] = msg['num_turns']
                    _auto_snapshot_notes_on_turn(session)
                    # Turn boundary — process stays alive
                    session['status'] = 'idle'
                    session['last_status_change_time'] = _time.time()
                    # Step 6: mid-session note-taker (default-off; fast-gated).
                    _maybe_checkpoint(session)
                # Web push hook: intercept PushNotification tool_use + turn results.
                _handle_push_signal(
                    session.get('project_id', ''),
                    session.get('session_id', ''),
                    msg,
                )
            except json.JSONDecodeError:
                # Auth-sentinel scan only on non-JSON lines (claude's raw
                # stderr). See Mode A reader for the false-positive history.
                _auth_reason = _scan_for_auth_error(line)
                if _auth_reason:
                    _mark_claude_auth_error(_auth_reason, line)
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
                        # rc!=0 while 'idle' = the turn ALREADY ended cleanly
                        # (the result event set 'idle' at the turn boundary);
                        # the nonzero exit is post-turn teardown — e.g.
                        # claude-fable-5 exits 1 after every turn under the
                        # full Mode B flag set — not a task failure. Logging
                        # it 'error' painted a red "Blocked" tile after each
                        # successful turn (2026-06-10). Mid-turn deaths still
                        # classify as error: they die with status 'running'.
                        _post_turn = session['status'] == 'idle'
                        session['status'] = 'completed' if (rc == 0 or _post_turn) else 'error'
                        session['last_status_change_time'] = _time.time()
                        if rc != 0:
                            session['log_lines'].append(f"[exited with code {rc}]")
                        if rc == 0 or _post_turn:
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
    _log(f"[dispatch] Resume {resume_id[:12]} failed for {project_id}, retrying fresh")
    _log_agent_activity(project_id, f"Resume failed, restarting fresh: {task[:80]}")

    context = _build_agent_context(p)
    fresh_task = (f"[Continuing from a previous conversation (session {resume_id}) that could not "
                  f"be resumed. Start fresh but continue the user's request below.]\n\n{task}")

    try:
        if mode == 'B':
            _sp_args, _sp_path = _sysprompt_file_args(context)
            cmd = [_resolve_claude(), *_build_claude_flags(p, streaming=True),
                   *_sp_args]
            proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, cwd=pp,
                text=True, encoding='utf-8', errors='replace',
                creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
            )
            _sysprompt_cleanup(_sp_path, proc)
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
            _sp_args, _sp_path = _sysprompt_file_args(context)
            cmd = [_resolve_claude(), '-p', fresh_task, *_build_claude_flags(p),
                   *_sp_args]
            proc = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, cwd=pp,
                text=True, encoding='utf-8', errors='replace',
                creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
            )
            _sysprompt_cleanup(_sp_path, proc)
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
        _log(f"[dispatch] Fresh retry failed for {project_id}: {e}")


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


def _log_github_sync_activity(project_id, msg):
    """Log a GitHub-sync event WITHOUT bumping `last_updated`.

    GitHub auto-sync runs every 5 min (incl. error cycles like an unreachable
    repo). Routing those through `_log_agent_activity` bumped `last_updated`
    each cycle, floating the project to the top of the mobile recency sort with
    no real conversation. Sync events still appear in the activity log; they no
    longer affect time-placement. (Ron, 2026-06-05)
    """
    _log_agent_activity(project_id, msg, bump_updated=False)


# ── GitHub sync module ───────────────────────────────────────────────────────
import github_sync as _gh_sync
_gh_sync.register(_POPEN_FLAGS, _STARTUPINFO,
                   _log_github_sync_activity, load_project, save_project, now_iso)


# ── Project (code) sync module — spike: read-only fetch + status ────────────
import project_sync as _proj_sync
_proj_sync.register(_POPEN_FLAGS, _STARTUPINFO,
                    _log_agent_activity, load_project, save_project, now_iso,
                    _DATA_ROOT)


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


def _migrate_agent_log_provider_field():
    """One-time idempotent migration: stamp provider='claude' on legacy agent_log entries.

    Entries written before the multi-provider branch existed have no 'provider' key.
    /api/usage and run-history endpoints default-read them as 'claude', but explicit
    presence makes queries unambiguous.  Safe to re-run (skips rows that already have
    the field).  Called once at startup inside _startup_memory_maintenance().
    """
    stamped = 0
    for f in DATA_DIR.glob('*_agent_log.json'):
        try:
            log = json.loads(f.read_text(encoding='utf-8'))
            dirty = False
            for entry in log:
                if 'provider' not in entry:
                    entry['provider'] = 'claude'
                    dirty = True
                    stamped += 1
            if dirty:
                f.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding='utf-8')
        except Exception:
            continue
    if stamped:
        _log(f"[provider-migrate] stamped provider='claude' on {stamped} legacy agent_log row(s)")


def _session_usage_payload(session: dict) -> dict:
    """Build the usage/cost/turns slice of an SSE payload, gated on provider capabilities.

    Providers that don't emit cost or turns (e.g. Gemini) must NOT fabricate zeros —
    the frontend reads absence of the key as "this provider doesn't support it" and
    hides the corresponding counter rather than showing 0.

    Always includes 'usage' when emits_usage is True (it is the authoritative token
    counter).  cost_usd and num_turns are only included when their respective
    capability flags are set.
    """
    provider = (session.get('provider') or 'claude').lower()
    try:
        caps = _agent_runtime.get_runtime(provider).capabilities()
    except Exception:
        # Fallback: treat as Claude (full telemetry) so we never silently drop data.
        caps = _agent_runtime.get_runtime('claude').capabilities()

    out: dict = {}
    if caps.emits_usage:
        out['usage'] = session.get('usage', {})
    if caps.emits_cost:
        out['cost_usd'] = session.get('cost_usd', 0)
    if caps.emits_num_turns:
        out['num_turns'] = session.get('num_turns', 0)
    return out


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
        _log(f"[backfill] {project_id}: added {added} synthesized log entr{'y' if added == 1 else 'ies'} from transcripts")
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
        _log(f"[backfill] load_projects failed: {e}")
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
            _log(f"[backfill] {pid}: {e}")
    if total:
        _log(f"[backfill] done: {total} synthesized entr{'y' if total == 1 else 'ies'} across {len(projects)} project(s)")


_SCRIBE_TERMINAL_STATUSES = ('completed', 'error', 'stopped', 'interrupted')


def _reconcile_unscribed_sessions():
    """Fix B — close the hard-MC-kill gap (SPEC §3 Leg A §3.A).

    `_log_agent_completion` never runs when the MC process is killed mid-
    session, so those sessions get no memory entry. This pass, run once at
    startup AFTER backfill (so orphan transcripts already have agent_log
    rows), captures them.

    First encounter per project (no entry carries the 'scribed' key — i.e. the
    log predates Fix B) → BASELINE-STAMP every entry scribed=True WITHOUT
    running the scribe. We deliberately do NOT retro-scribe history; the goal
    is to stop LOSING future hard-killed sessions, not to mine the past.

    Thereafter → for terminal entries lacking `scribed` (post-baseline orphans
    = the hard-kill victims), run the shared memory write, capped per project
    per boot to bound Haiku cost. Over-cap remainder retried next boot.
    """
    if not CONFIG.get('scribe_enabled', True):
        return
    if not CONFIG.get('scribe_reconcile_enabled', True):
        return
    cap = int(CONFIG.get('scribe_reconcile_cap', 5) or 5)
    try:
        projects = load_projects()
    except Exception as e:
        _log(f"[scribe-reconcile] load_projects failed: {e}")
        return
    baselined = scribed_n = 0
    for p in projects:
        pid = p.get('id')
        if not pid:
            continue
        if p.get('_is_incognito_project') or pid == INCOGNITO_PROJECT_ID:
            continue
        try:
            log = _load_agent_log(pid)
            if not log:
                continue
            first_encounter = not any('scribed' in e for e in log)
            if first_encounter:
                for e in log:
                    e['scribed'] = True
                _save_agent_log(pid, log)
                baselined += 1
                continue
            # Don't race a live session for this project.
            if _has_running_agent(pid):
                continue
            # SPEC §3.A.MID Fix-B coordination: snapshot leftover Step-6 wm
            # markers once. A marker present for a session ⇒ it was killed
            # mid-flight while checkpointing → finalize from its running
            # summary (no Haiku) instead of a full re-scribe.
            try:
                _mp = _get_memory_path(p)
                _wm = (_mem_split_full(_mp.read_text(encoding='utf-8'))[2]
                       if _mp.exists() else [])
            except Exception:
                _wm = []
            wrote = False
            done = 0
            for e in log:
                if done >= cap:
                    break
                if e.get('scribed'):
                    continue
                if e.get('status') not in _SCRIBE_TERMINAL_STATUSES:
                    continue
                _esid = e.get('session_id', '')
                _wmrec = _wm_find(_wm, _esid) if _esid else None
                if _wmrec and str(_wmrec.get('running_summary') or '').strip():
                    # Killed mid-flight WITH Step-6 progress: finalize from the
                    # running summary, drop the wm marker, NO model call.
                    _rs = str(_wmrec['running_summary']).replace('\n', ' ').strip()[:300]
                    _tk = (e.get('task', '') or '').strip()
                    _ts = (e.get('ts', '') or now_iso())[:10]
                    _fin = f"- [{_ts}] **{_tk[:80]}** _(reconciled)_ — {_rs}"
                    try:
                        if _commit_managed_entry(p, mem_entry=_fin,
                                                 wm_remove_sid=_esid):
                            _dispatch_condense(p)
                        e['scribed'] = True
                        wrote = True
                        scribed_n += 1
                        done += 1
                        _scribe_stat(pid, 'checkpoint_finalized')
                        continue
                    except Exception as ex:
                        _log(f"[scribe-reconcile] {pid} wm-finalize: {ex}")
                        # fall through to full re-scribe
                if not e.get('claude_session_id'):
                    continue
                sess = {
                    'project_id': pid,
                    'claude_session_id': e.get('claude_session_id', ''),
                    'task': e.get('task', ''),
                    'incognito': False,
                    'housekeeping': False,
                }
                try:
                    if _write_session_memory(p, sess, e.get('status', 'interrupted'),
                                              e.get('summary', ''),
                                              (e.get('ts', '') or now_iso())[:10]):
                        e['scribed'] = True
                        wrote = True
                        scribed_n += 1
                        done += 1
                except Exception as ex:
                    _log(f"[scribe-reconcile] {pid} entry: {ex}")
            if wrote:
                _save_agent_log(pid, log)
        except Exception as e:
            _log(f"[scribe-reconcile] {pid}: {e}")
    if baselined or scribed_n:
        _log(f"[scribe-reconcile] baselined {baselined} project(s); "
              f"reconciled {scribed_n} previously-unscribed session(s)")


def _backfill_token_telemetry():
    """Populate model_tokens on existing agent_log entries that pre-date
    the telemetry feature. Reads each entry's JSONL transcript and writes
    input_tokens / output_tokens / model / model_tokens. Safe to re-run:
    entries that already have model_tokens are skipped. Never raises.
    """
    try:
        projects = load_projects()
    except Exception:
        return 0
    updated = 0
    for p in projects:
        pid = p.get('id', '')
        pp = p.get('project_path', '')
        if not pid or not pp:
            continue
        if p.get('_is_incognito_project') or pid == INCOGNITO_PROJECT_ID:
            continue
        try:
            log = _load_agent_log(pid)
            changed = False
            for entry in log:
                if entry.get('model_tokens'):
                    continue
                csid = entry.get('claude_session_id', '')
                if not csid:
                    continue
                tf = _find_transcript_file(pp, csid)
                if not tf:
                    continue
                tel = _extract_transcript_telemetry(tf)
                if not tel:
                    continue
                entry['model'] = tel.get('model', '')
                entry['input_tokens'] = tel.get('input_tokens', 0)
                entry['output_tokens'] = tel.get('output_tokens', 0)
                entry['cache_read_tokens'] = tel.get('cache_read_tokens', 0)
                entry['model_tokens'] = tel.get('model_tokens', {})
                changed = True
                updated += 1
            if changed:
                _save_agent_log(pid, log)
        except Exception as e:
            _log(f"[telemetry-backfill] {pid}: {e}")
    if updated:
        _log(f"[telemetry-backfill] populated {updated} entr{'y' if updated == 1 else 'ies'}")
    return updated


def _startup_memory_maintenance():
    """Background startup chain: backfill agent_log from transcripts, THEN
    reconcile unscribed sessions (order matters — reconcile needs the
    synthesized orphan rows backfill creates). Off the app.run() path."""
    try:
        _migrate_agent_log_provider_field()
    except Exception as e:
        _log(f"[provider-migrate] failed: {e}")
    try:
        _backfill_all_agent_logs()
    except Exception as e:
        _log(f"[backfill] failed: {e}")
    try:
        _reconcile_unscribed_sessions()
    except Exception as e:
        _log(f"[scribe-reconcile] bootstrap failed: {e}")
    try:
        _backfill_token_telemetry()
    except Exception as e:
        _log(f"[telemetry-backfill] failed: {e}")


def _revive_history_lines(project_path, claude_sid, user_label, max_messages=40):
    """Reconstruct prior-conversation log_lines from the on-disk Claude transcript.

    On server restart a session is revived with `-r <claude_sid>` so the model
    keeps context, but the visible chat buffer would otherwise start empty —
    the user sees only their own new message, not the agent's earlier reply
    (this is what makes a tapped push land on a one-sided chat). Re-render the
    last `max_messages` turns from the transcript in the same buffer format the
    live stream uses ('> Label: ...' for user turns, raw text for assistant).

    Returns [] on any failure (no transcript, parse error) — revival still
    proceeds, just without restored history.
    """
    lines = _transcript_buffer_lines(project_path, claude_sid, user_label,
                                     max_messages=max_messages)
    if lines:
        lines.append('[— restored from transcript; conversation continues below —]')
    return lines


def _transcript_buffer_lines(project_path, claude_sid, user_label, max_messages=40):
    """Render a Claude .jsonl transcript into chat-buffer lines (user+assistant).

    Same line format the live stream/log_lines uses: '> Label: ...' for user
    turns (single buffer entry, leading/trailing newline like the live seed),
    raw text for assistant turns. tool_call/error rows are skipped — the chat
    renders user/assistant bubbles and tool noise isn't wanted here. Returns
    [] on any failure so callers can degrade gracefully.
    """
    try:
        f = _find_transcript_file(project_path, claude_sid)
        if not f:
            return []
        msgs = _parse_transcript_messages(f, max_messages=max_messages)
        lines = []
        for m in msgs:
            role = m.get('role')
            if role == 'user':
                txt = (m.get('text') or '').strip()
                if txt:
                    lines.append(f"\n> {user_label}: {txt}\n")
            elif role == 'assistant':
                txt = (m.get('text') or '').strip()
                if txt:
                    lines.append(txt)
        return lines
    except Exception as e:
        _log(f"[transcript-render] failed: {e}")
        return []


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
    # Restore the prior conversation into the visible buffer so a tapped push
    # (about the agent's pre-restart reply) doesn't land on a one-sided chat.
    # Skipped when the transcript was too large to resume directly (we started
    # fresh, so there's no coherent -r history to show anyway).
    history_lines = [] if too_large else _revive_history_lines(pp, claude_sid, user_label)
    seed_lines = history_lines + [revive_note, f"\n> {user_label}: {message}\n"]

    if use_streaming:
        cmd = [_resolve_claude(), *resume_flags, *_build_claude_flags(p, streaming=True)]
        _sp_path = None
        if not resume_flags and context:
            _sp_args, _sp_path = _sysprompt_file_args(context)
            cmd.extend(_sp_args)
        try:
            proc = subprocess.Popen(
                cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, cwd=pp,
                text=True, encoding='utf-8', errors='replace',
                creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
            )
        except Exception as e:
            _log(f"[revive] {project_id}: spawn failed: {e}")
            if _sp_path:
                try:
                    os.unlink(_sp_path)
                except OSError:
                    pass
            return None
        _sysprompt_cleanup(_sp_path, proc)
        threading.Thread(target=_hide_windows_delayed, args=(proc.pid,), daemon=True).start()
        _register_process(proc, 'Agent revived (B)', 'agent', session_id, project_id, message[:80])

        session = {
            'proc': proc,
            'status': 'running',
            'task': entry.get('task', ''),
            'log_lines': list(seed_lines),
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
            '_resume_confirmed': False,   # a just-spawned resume hasn't proven itself yet
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
        _log(f"[revive] {project_id}: Mode B revived session {session_id} via -r {claude_sid[:12]}")
        return session

    # Mode A
    _sp_path = None
    if resume_flags:
        cmd = [_resolve_claude(), *resume_flags, '-p', revival_msg, *_build_claude_flags(p)]
    else:
        if not context:
            context = _build_agent_context(p)
        _sp_args, _sp_path = _sysprompt_file_args(context)
        cmd = [_resolve_claude(), '-p', revival_msg, *_build_claude_flags(p),
               *_sp_args]
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, cwd=pp,
            text=True, encoding='utf-8', errors='replace',
            creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
        )
    except Exception as e:
        _log(f"[revive] {project_id}: spawn failed: {e}")
        if _sp_path:
            try:
                os.unlink(_sp_path)
            except OSError:
                pass
        return None
    _sysprompt_cleanup(_sp_path, proc)
    threading.Thread(target=_hide_windows_delayed, args=(proc.pid,), daemon=True).start()
    _register_process(proc, 'Agent revived (A)', 'agent', session_id, project_id, message[:80])

    session = {
        'proc': proc,
        'status': 'running',
        'task': entry.get('task', ''),
        'log_lines': list(seed_lines),
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
    _log(f"[revive] {project_id}: Mode A revived session {session_id} via -r {claude_sid[:12]}")
    return session


def _accumulate_session_usage(session, turn_usage):
    """Merge a single turn's usage dict into the running session total.

    CC emits one `result` event per turn in Mode B (persistent). Each event
    carries only THAT turn's token counts, not a cumulative total. Overwriting
    session['usage'] discards all prior turns; instead we sum the numeric
    fields so the final value reflects the whole session.
    """
    _INT_FIELDS = ('input_tokens', 'output_tokens',
                   'cache_read_input_tokens', 'cache_creation_input_tokens')
    prev = session.get('usage') or {}
    merged = dict(prev)
    for k in _INT_FIELDS:
        merged[k] = int(prev.get(k) or 0) + int(turn_usage.get(k) or 0)
    # Carry non-numeric metadata from the latest turn (service_tier, etc.)
    for k, v in turn_usage.items():
        if k not in _INT_FIELDS:
            merged[k] = v
    session['usage'] = merged


def _note_claude_sid(session, sid):
    """Record the Claude CLI session UUID on the live session and, the first time
    it becomes known for a non-manual (scheduled/hivemind) trigger, backfill it
    into the still-'in_progress' agent_log row.

    Chain fix (Defect B): _log_agent_completion is the only OTHER writer of
    claude_session_id into the log, and for a persistent Mode-B session it does
    not run until the process tears down (CLAUDE.md "Mode B caveat"). Without
    this backfill, _latest_claude_sid_for_schedule / _revive_from_agent_log find
    an empty csid on the pending row and every scheduled fire cold-starts a new
    conversation. Called from both stream readers on every message carrying a
    session_id; the prev==sid early-out makes it a cheap no-op after the first
    capture (no repeated agent_log IO on the hot path)."""
    if not sid:
        return
    prev = session.get('claude_session_id')
    session['claude_session_id'] = sid
    if prev == sid:
        return
    tt = session.get('trigger_type')
    if not tt or tt == 'manual':
        return
    if session.get('incognito') or session.get('housekeeping'):
        return
    pid = session.get('project_id')
    msid = session.get('session_id', '')
    if not pid or not msid:
        return
    try:
        log = _load_agent_log(pid)
        for e in log:
            if e.get('session_id') == msid and e.get('status') == 'in_progress':
                if e.get('claude_session_id') != sid:
                    e['claude_session_id'] = sid
                    _save_agent_log(pid, log)
                break
    except Exception as ex:
        _log(f"[csid-backfill] {pid}: {ex}")


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
        # Upsert: a continued scheduled run reuses the prior run's session_id
        # (see _dispatch_agent_internal reuse_session_id). Refresh that row in
        # place — reset it to in_progress for this fire, preserve any csid
        # already captured — instead of orphaning a fresh row every cadence
        # tick. New session_ids fall through to insert(0) as before.
        existing_i = next((i for i, e in enumerate(log)
                           if e.get('session_id') == sid), None)
        if existing_i is not None:
            prev = log[existing_i]
            entry['claude_session_id'] = prev.get('claude_session_id', '')
            entry['started_at'] = prev.get('started_at', '') or entry['started_at']
            log.pop(existing_i)
            log.insert(0, entry)
        else:
            log.insert(0, entry)
        _save_agent_log(project_id, log)
    except Exception as e:
        _log(f"[dispatch-log] {project_id}: pending write failed: {e}")


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
        _log(f"[reconcile-pending] load_projects failed: {e}")
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
            _log(f"[reconcile-pending] {pid}: {e}")
    if flipped_total:
        _log(f"[reconcile-pending] flipped {flipped_total} orphaned in_progress entr{'y' if flipped_total == 1 else 'ies'} to 'interrupted'")


_MEM_ARCHIVE_HEADER = '## Archived Session Log'


def _append_to_archive(project, lines):
    """Append raw '- [' lines to the project's permanent archive, creating the
    file + header on first write. Read-modify-write under the caller's leaf
    lock; the archive is append-only cold storage — never truncated (SPEC D3).
    Shared by _commit_managed_entry (mechanical floor) and _condense_apply."""
    if not lines:
        return
    ap = _get_archive_path(project)
    ap.parent.mkdir(parents=True, exist_ok=True)
    prev = ap.read_text(encoding='utf-8').rstrip() if ap.exists() else ''
    if _MEM_ARCHIVE_HEADER not in prev:
        prev = (prev + f'\n\n{_MEM_ARCHIVE_HEADER}'
                if prev else _MEM_ARCHIVE_HEADER)
    _atomic_write_text(ap, prev + '\n' + '\n'.join(lines) + '\n')


def _commit_managed_entry(p, mem_entry=None, wm_upsert=None, wm_remove_sid=None):
    """Leaf-locked atomic MEMORY.md commit — the write path shared by the
    completion scribe, the Step-6 checkpoint worker, and teardown (the
    structured Leg C `_condense_apply` is a co-equal writer under the SAME
    leaf lock + atomic primitive; both route archive overflow through
    `_append_to_archive`). In a single
    per-project mem-write-locked, atomic (temp+replace) operation:
      • optionally append `mem_entry` ('- [' line) to the managed region,
      • optionally `_wm_upsert`/`_wm_remove` this session's watermark marker,
      • run the lossless line-keyed floor (relocates only '- [' entries;
        wm markers never popped but DO count toward the budget),
      • write MEMORY.md (+archive overflow) atomically.
    No scribe call and no condense dispatch inside the lock (the slow/process
    parts stay out). Returns whether condense should fire; caller dispatches it
    OUTSIDE the lock. Never raises. SPEC §3.A.MID committee blocker #3.
    """
    project_id = p.get('id', '')
    mem_path = _get_memory_path(p)
    mem_path.parent.mkdir(parents=True, exist_ok=True)
    hard_floor = int(CONFIG.get('index_line_hard_floor', 185) or 185)
    with _get_mem_write_lock(project_id):
        existing = (mem_path.read_text(encoding='utf-8')
                    if mem_path.exists() else '')
        # Leg 0: idempotent, additive migration; curated region untouched.
        curated, mem_entries, wm_markers = _mem_split_full(_mem_migrate(existing))
        if mem_entry:
            mem_entries.append(mem_entry)
        if wm_remove_sid is not None:
            wm_markers = _wm_remove(wm_markers, wm_remove_sid)
        if wm_upsert is not None:
            wm_markers = _wm_upsert(wm_markers, wm_upsert)
        overflow = []
        while mem_entries and len(
                _mem_compose(curated, mem_entries, wm_markers).splitlines()) > hard_floor:
            overflow.append(mem_entries.pop(0))  # oldest → archive
        _append_to_archive(p, overflow)
        _atomic_write_text(mem_path,
                           _mem_compose(curated, mem_entries, wm_markers))
        return _should_condense(p, include_claude_md=True)


def _write_session_memory(p, session, status, summary_fallback, ts_date):
    """Shared Leg A/0/C memory write — completion path & startup reconciler.
    Scribe over the full .jsonl → brief (fallback to summary, then a
    guaranteed breadcrumb) → _commit_managed_entry (which also drops this
    session's Step-6 wm marker = clean teardown) → condense trigger. Returns
    True iff a memory entry was written. Never raises.
    SPEC docs/MEMORY_SYSTEM_SPEC.md §3 Leg A/0/C.
    """
    project_id = p.get('id', '')
    task = (session.get('task', '') or '').strip()
    # Scribe model call is the slow (≤180s) part — OUTSIDE the leaf lock.
    scribed, _why = _scribe_extract(p, session)
    _scribe_stat(project_id, 'scribe_extracted' if scribed
                 else f'scribe_fell_back:{_why}')
    fb = (summary_fallback or '')[:300].replace('\n', ' ').strip()
    brief = (scribed or fb
             or f"ended with status={status}, no captured output"
             ).replace('\n', ' ').strip()
    tag = '' if status == 'completed' else f' _({status})_'
    mem_entry = f"- [{ts_date}] **{task[:80]}**{tag} — {brief}"
    # Terminal write also removes this session's live wm marker (clean
    # teardown — SPEC §3.A.MID Fix-B coordination), in the same atomic write.
    do_condense = _commit_managed_entry(
        p, mem_entry=mem_entry,
        wm_remove_sid=session.get('session_id') or session.get('id'))
    if do_condense:
        _dispatch_condense(p)
    # Phase 4 Distiller — daemon-thread dispatch parallel to Scribe (v2.1 §4.8).
    # Best-effort: failure NEVER blocks Scribe / MEMORY.md / completion. The
    # entry point gates itself via _distiller_should_proceed at session_end_extract.
    try:
        csid = session.get('claude_session_id', '')
        sid = session.get('session_id') or session.get('id') or ''
        if not csid:
            _log(f"[distiller] dispatch SKIP project_id={project_id} sid={sid}: "
                 f"no claude_session_id on session object")
        else:
            tf = _find_transcript_file(p.get('project_path', ''), csid)
            jsonl_path = str(tf) if tf else None
            _log(f"[distiller] dispatch FIRE project_id={project_id} sid={sid[:12]} "
                 f"csid={csid[:8]} jsonl_path={'yes' if jsonl_path else 'no'}")
            threading.Thread(
                target=_distiller._distill_extract_and_aggregate,
                args=(project_id, sid, jsonl_path),
                daemon=True,
                name=f"distiller-{project_id}",
            ).start()
    except Exception as _dist_disp_err:
        # Was bare `except: pass` — silently swallowed any error in the dispatch
        # path including AttributeError if _distiller wasn't registered. Log it
        # so we can see if dispatch fails.
        _log(f"[distiller] dispatch EXCEPTION project_id={project_id}: "
             f"{type(_dist_disp_err).__name__}: {_dist_disp_err!r}")
    return True


# ── Step 6: mid-session checkpoint note-taker (SPEC §3.A.MID) — default-off ──
# _checkpoint_inflight / _checkpoint_guard / _checkpoint_sema /
# _checkpoint_sema_guard moved to mc/state.py (Phase 0).


def _sha8(s):
    import hashlib
    return hashlib.sha1((s or '').encode('utf-8', 'replace')).hexdigest()[:8]


def _get_checkpoint_sema(pid):
    with _checkpoint_sema_guard:
        s = _checkpoint_sema.get(pid)
        if s is None:
            s = threading.BoundedSemaphore(2)  # ≤2 concurrent checkpoints/project
            _checkpoint_sema[pid] = s
    return s


def _checkpoint_prev_offset(p, sid):
    """Cheap read of this session's last watermark byte_offset (0 if none)."""
    try:
        mp = _get_memory_path(p)
        if not mp.exists():
            return 0
        _c, _e, wm = _mem_split_full(mp.read_text(encoding='utf-8'))
        r = _wm_find(wm, sid)
        return int(r.get('byte_offset', 0)) if r else 0
    except Exception:
        return 0


def _maybe_checkpoint(session):
    """Mode-B turn-boundary hook (clones the _auto_snapshot_notes_on_turn
    precedent). FAST gate only — no model call here: config flags,
    incognito/housekeeping, real-boundary, KB-delta debounce, one-in-flight
    per session. Spawns the worker on a daemon thread. Never raises (must not
    break the reader)."""
    try:
        if not CONFIG.get('scribe_checkpoint_enabled', False):
            return
        kb = int(CONFIG.get('scribe_checkpoint_kb', 0) or 0)
        if kb <= 0 or not CONFIG.get('scribe_enabled', True):
            return
        if session.get('incognito') or session.get('housekeeping'):
            return
        if (session.get('waiting_for_question')
                or session.get('waiting_for_plan_approval')):
            return  # not a real work boundary
        if not session.get('process_alive', True):
            return
        pid = session.get('project_id', '')
        sid = session.get('session_id') or session.get('id')
        csid = session.get('claude_session_id', '')
        if not (pid and sid and csid):
            return
        p = load_project(pid)
        if not p:
            return
        pp = p.get('project_path', '')
        tf = _find_transcript_file(pp, csid)
        if not tf:
            return
        try:
            size = os.path.getsize(tf)
        except OSError:
            return
        if size - _checkpoint_prev_offset(p, sid) < kb * 1024:
            return  # not enough new transcript yet (debounce)
        with _checkpoint_guard:
            if sid in _checkpoint_inflight:
                _scribe_stat(pid, 'checkpoint_coalesced')
                return  # previous worker still running; next boundary covers more
            _checkpoint_inflight.add(sid)
        snap = {'pid': pid, 'sid': sid, 'csid': csid,
                'task': (session.get('task', '') or '').strip(),
                'tf': str(tf)}
        threading.Thread(target=_checkpoint_worker, args=(snap,),
                         daemon=True).start()
    except Exception:
        pass


def _checkpoint_worker(snap):
    """Render the delta since the last watermark, fold it into the running
    summary, append a self-contained `_(live)_` entry + upsert the wm marker
    in one leaf-locked atomic write. SPEC §3.A.MID. Never raises."""
    pid, sid, csid, task, tf = (snap['pid'], snap['sid'], snap['csid'],
                                snap['task'], snap['tf'])
    sema = _get_checkpoint_sema(pid)
    if not sema.acquire(blocking=False):
        _scribe_stat(pid, 'checkpoint_coalesced')  # project at fan-out cap
        with _checkpoint_guard:
            _checkpoint_inflight.discard(sid)
        return
    try:
        p = load_project(pid)
        if not p:
            return
        prev_off, prev_summary = 0, ''
        try:
            mp = _get_memory_path(p)
            if mp.exists():
                _c, _e, wm = _mem_split_full(mp.read_text(encoding='utf-8'))
                r = _wm_find(wm, sid)
                if r:
                    prev_summary = r.get('running_summary', '') or ''
                    if r.get('transcript_path') == tf:
                        prev_off = int(r.get('byte_offset', 0))
                    else:
                        # resume opened a new .jsonl → restart offset, KEEP
                        # the running summary as the reduce base (no loss).
                        _scribe_stat(pid, 'checkpoint_offset_reset')
        except Exception:
            prev_off, prev_summary = 0, ''
        delta, new_off = _scribe_render_delta(tf, prev_off)
        if not delta.strip() or new_off == prev_off:
            return  # nothing new complete; retry next boundary (offset kept)
        model = CONFIG.get('scribe_model', '') or 'haiku'
        dsum, reason = _scribe_summarize_text(delta, model)
        rec = {'session_id': sid, 'claude_session_id': csid,
               'transcript_path': tf, 'byte_offset': new_off,
               'slice_hash': _sha8(delta)}
        if reason != 'extracted':
            # Thin/refused/error delta — advance the offset (that span had
            # nothing material) but write NO entry and keep prev summary.
            rec['running_summary'] = prev_summary
            if _commit_managed_entry(p, wm_upsert=rec):
                _dispatch_condense(p)
            _scribe_stat(pid, f'checkpoint_skipped:{reason}')
            return
        if prev_summary:
            try:
                merged = _scribe_call(
                    model, _SCRIBE_CHECKPOINT_REDUCE,
                    f"PREVIOUS:\n{prev_summary}\n\nNEW:\n{dsum}")
                merged = (merged or '').strip().replace('\n', ' ').strip() or dsum
            except Exception:
                merged = dsum
        else:
            merged = dsum
        merged = merged[:300]
        rec['running_summary'] = merged
        entry = f"- [{now_iso()[:10]}] **{task[:80]}** _(live)_ — {merged}"
        if _commit_managed_entry(p, mem_entry=entry, wm_upsert=rec):
            _dispatch_condense(p)
        _scribe_stat(pid, 'checkpoint_extracted')
    except Exception:
        pass
    finally:
        sema.release()
        with _checkpoint_guard:
            _checkpoint_inflight.discard(sid)


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

    # Extract token telemetry from the transcript before building the entry.
    # Best-effort: failures silently produce empty telemetry.
    _telemetry = {}
    if not is_housekeeping and not session.get('incognito'):
        try:
            _tp = load_project(project_id)
            _pp = (_tp or {}).get('project_path', '')
            _csid = session.get('claude_session_id', '')
            if _pp and _csid:
                _tf = _find_transcript_file(_pp, _csid)
                _telemetry = _extract_transcript_telemetry(_tf)
        except Exception:
            pass

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
        # Provider that ran this session ('claude', 'gemini', ...). Absent on
        # pre-multi-provider entries; treat missing as 'claude' when reading.
        'provider': session.get('provider', 'claude'),
        # Fix B marker: whether this session's memory was captured. Presence of
        # this key on ANY entry means the log was written by Fix-B-aware code
        # (used by the reconciler to distinguish first-boot baseline).
        'scribed': False,
        # Token telemetry from transcript (indicative; populated going forward).
        'model': _telemetry.get('model', ''),
        'input_tokens': _telemetry.get('input_tokens', 0),
        'output_tokens': _telemetry.get('output_tokens', 0),
        'cache_read_tokens': _telemetry.get('cache_read_tokens', 0),
        'model_tokens': _telemetry.get('model_tokens', {}),
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

    # Auto-append session summary to project memory (native Claude MEMORY.md).
    # NOT gated to 'completed' only: error/stopped sessions are exactly where
    # "what was tried / why it broke" is most valuable, and the scribe reads
    # the .jsonl (not stdout) so it doesn't need a clean summary. The hard
    # MC-kill case (this function never runs) is closed by the startup
    # scribe-reconciliation pass, not here. SPEC §3 Leg A.
    status = session.get('status')
    if status in ('completed', 'error', 'stopped'):
        try:
            p = load_project(project_id)
            if p and _write_session_memory(p, session, status, summary,
                                           entry['ts'][:10]):
                # Persist the Fix B marker so the startup reconciler never
                # re-scribes a session the completion path already captured.
                entry['scribed'] = True
                _save_agent_log(project_id, log)
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

    cmd = [_resolve_claude(), *resume_flags, '-p', message, *_build_claude_flags(p)]

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


# ── Leg A: session-end Scribe ────────────────────────────────────────────────
# SPEC docs/MEMORY_SYSTEM_SPEC.md §3 Leg A. MC retains nothing full-fidelity
# (see [[discovery: MC retains zero full-fidelity transcript]]), so the scribe
# reads the CLI's on-disk .jsonl — the only full-fidelity source — and asks a
# cheap model to extract one tight memory line. Any failure falls back to the
# legacy stdout-tail summary so completion never breaks.

_SCRIBE_PROMPT = (
    "You are a project-memory scribe. Below is a full agent session transcript "
    "(actions, tool results, reasoning). Write ONE dense line (max 280 chars, no "
    "newlines) for a project memory log: what was done, what was decided/learned, "
    "and any gotcha or follow-up. Be concrete (files, names, decisions). Output "
    "ONLY that line — no preamble, no markdown, no quotes."
)
_SCRIBE_MAP_PROMPT = (
    "This is ONE CHUNK of a longer agent session transcript. In 1-2 tight "
    "sentences, note what was done/decided/learned/broken in THIS chunk only. "
    "Output only those sentences."
)
_SCRIBE_REDUCE_PROMPT = (
    "Below are ordered partial notes from consecutive chunks of one agent "
    "session. Synthesize them into ONE dense line (max 280 chars, no newlines) "
    "for a project memory log: what was done, decided/learned, and any gotcha. "
    "Output ONLY that line."
)
_SCRIBE_CHECKPOINT_REDUCE = (
    "PREVIOUS is the running summary of an IN-PROGRESS agent session so far; "
    "NEW is what happened since. Produce ONE updated dense line (max 280 "
    "chars, no newlines) that SUPERSEDES PREVIOUS by folding in NEW: what's "
    "been done, decided/learned, and open gotchas. Output ONLY that line — "
    "no preamble, no markdown, no quotes."
)
# Single-call ceiling (~chars). Above this -> chunked map-reduce.
_SCRIBE_SINGLE_LIMIT = 350_000
_SCRIBE_RESULT_CAP = 2000  # per tool_result bulk cap in the rendered transcript
# A transcript is "thin" (skip the model, fall back to stdout-tail) only when
# it shows NO activity (no tool ACTION/RESULT, no THINKING) AND its text is
# trivially short. Keying on activity — not raw length — avoids rejecting a
# genuinely substantive but compact session (one tool call + a one-line
# answer renders well under any fixed char threshold). A bare "ASSISTANT: OK"
# has no activity and ~13 chars → thin; "ACTION Bash… RESULT… ASSISTANT…" is
# substantive at any length.
_SCRIBE_THIN_TEXT_CHARS = 120
_SCRIBE_ACTIVITY_PREFIXES = ('ACTION ', 'RESULT:', 'THINKING:')
# If the model's reply looks like a refusal / request-for-input rather than a
# summary, never write it as memory — fall back. Lowercased substring match.
_SCRIBE_REFUSAL_MARKERS = (
    "i don't see a transcript", "i do not see a transcript",
    "no transcript", "please paste", "paste the session",
    "paste the transcript", "share the transcript",
    "provide the transcript", "don't have access to",
    "didn't receive", "did not receive", "cannot see any transcript",
    "no session transcript", "there is no transcript",
)


def _scribe_stat(project_id, key, n=1):
    """Add n to a scribe-outcome counter (SPEC §8 telemetry). Best-effort;
    n<=0 is a no-op (no file touch)."""
    if n <= 0:
        return
    try:
        fp = DATA_DIR / f'{project_id}_scribe_stats.json'
        stats = {}
        if fp.exists():
            stats = json.loads(fp.read_text(encoding='utf-8') or '{}')
        stats[key] = int(stats.get(key, 0)) + n
        stats['_updated'] = now_iso()
        fp.write_text(json.dumps(stats, indent=2), encoding='utf-8')
    except Exception:
        pass  # telemetry must never break completion


# Coarse model-tier extraction for telemetry. Even though the router emits
# full model IDs like 'claude-haiku-4-5-20251001', we bucket by tier in
# /api/router/stats so by_pair stays small (3x3 + fallback) instead of
# exploding per model snapshot.
_ROUTER_TIER_KEYWORDS = ('haiku', 'sonnet', 'opus')


def _router_model_tier(model_name):
    s = (model_name or '').lower()
    for k in _ROUTER_TIER_KEYWORDS:
        if k in s:
            return k
    return s or 'unknown'


def _router_stat(project_id, requested_model, chosen_model, source, reason=''):
    """Auto-router telemetry — bumps a per-project counter on every dispatch.

    Shape (per docs/DISPATCH_AND_ROUTING_ANALYSIS.md §B.5):
      {
        "totals": {"manual": N, "auto": N, "fallback": N},
        "by_pair": {"opus->haiku": N, "opus->sonnet": N, ...,
                    "fallback:opus": N},
        "last_fallback": {"ts": "...Z", "reason": "..."}
      }

    Best-effort; never raises. Telemetry must never break dispatch.
    """
    try:
        fp = DATA_DIR / f'{project_id}_router_stats.json'
        stats = {}
        if fp.exists():
            try:
                stats = json.loads(fp.read_text(encoding='utf-8') or '{}')
            except Exception:
                stats = {}
        if not isinstance(stats, dict):
            stats = {}
        totals = stats.setdefault('totals', {})
        totals[source] = int(totals.get(source, 0)) + 1
        by_pair = stats.setdefault('by_pair', {})
        req_tier = _router_model_tier(requested_model)
        chosen_tier = _router_model_tier(chosen_model)
        if source == 'fallback':
            key = f'fallback:{req_tier}'
        else:
            key = f'{req_tier}->{chosen_tier}'
        by_pair[key] = int(by_pair.get(key, 0)) + 1
        if source == 'fallback':
            stats['last_fallback'] = {
                'ts': now_iso(),
                'reason': reason or 'unknown',
            }
        stats['_updated'] = now_iso()
        fp.write_text(json.dumps(stats, indent=2), encoding='utf-8')
    except Exception:
        pass  # telemetry must never break dispatch


def _scribe_render_lines(lines):
    """Render an iterable of raw .jsonl text lines into the compact view.

    Shared core of _scribe_render_transcript (whole file) and
    _scribe_render_delta (Step 6, from a byte offset). Strips base64/image
    blocks, bulk-caps oversized tool_results, skips unparseable lines (so a
    stray leading fragment from a non-boundary offset is harmlessly ignored —
    the leading-partial safety net, SPEC §3.A.MID).
    """
    out = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            m = json.loads(line)
        except Exception:
            continue
        msg = m.get('message') if isinstance(m.get('message'), dict) else None
        if not msg or not isinstance(msg.get('content'), list):
            continue
        mtype = m.get('type', '')
        for b in msg['content']:
            if not isinstance(b, dict):
                continue
            bt = b.get('type', '')
            if bt == 'text' and mtype == 'assistant':
                t = (b.get('text') or '').strip()
                if t:
                    out.append(f"ASSISTANT: {t}")
            elif bt == 'thinking':
                t = (b.get('thinking') or b.get('text') or '').strip()
                if t:
                    out.append(f"THINKING: {t[:2000]}")
            elif bt == 'tool_use':
                inp = b.get('input', {})
                try:
                    s = json.dumps(inp, ensure_ascii=False)
                except Exception:
                    s = str(inp)
                out.append(f"ACTION {b.get('name','?')}: {s[:400]}")
            elif bt == 'tool_result':
                c = b.get('content')
                if isinstance(c, list):
                    parts = []
                    for cb in c:
                        if isinstance(cb, dict) and cb.get('type') == 'text':
                            parts.append(cb.get('text', ''))
                        # image/base64 blocks intentionally dropped
                    c = '\n'.join(parts)
                elif not isinstance(c, str):
                    c = json.dumps(c, ensure_ascii=False) if c else ''
                c = (c or '').strip()
                if not c:
                    continue
                if len(c) > _SCRIBE_RESULT_CAP:
                    half = _SCRIBE_RESULT_CAP // 2
                    c = f"{c[:half]}\n…[{len(c)-_SCRIBE_RESULT_CAP} chars elided]…\n{c[-half:]}"
                out.append(f"RESULT: {c}")
    return '\n'.join(out)


def _scribe_render_transcript(path):
    """Render the whole raw CLI .jsonl into the compact, full-sequence view."""
    with open(path, encoding='utf-8', errors='replace') as fh:
        return _scribe_render_lines(fh)


def _scribe_render_delta(path, byte_offset):
    """Step 6: render ONLY the transcript bytes after `byte_offset`.

    Returns (rendered_text, new_byte_offset). new_byte_offset is the position
    immediately past the last complete newline consumed — it ONLY ever
    advances to a line boundary, so the next call's start is a clean line
    start (no leading-partial drop needed; an anomalous fragment would just
    fail json parse and be skipped by _scribe_render_lines). Trailing-partial
    rule: never consume past the last '\\n' (the agent may be mid-write). If
    `byte_offset` exceeds the file (rotation/truncation, SPEC S3-1) it resets
    to 0. If no complete new line is available, returns ('', byte_offset)
    unchanged (caller skips this checkpoint, retries next turn).
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return '', byte_offset
    if byte_offset > size:
        byte_offset = 0  # transcript rotated/truncated
    try:
        with open(path, 'rb') as fh:
            fh.seek(byte_offset)
            blob = fh.read()
    except OSError:
        return '', byte_offset
    last_nl = blob.rfind(b'\n')
    if last_nl < 0:
        return '', byte_offset  # no complete line yet
    consumed = blob[:last_nl].decode('utf-8', errors='replace')
    new_offset = byte_offset + last_nl + 1
    return _scribe_render_lines(consumed.split('\n')), new_offset


def _scribe_call(model, instruction, body):
    """One blocking `claude -p` call (prompt via stdin to dodge arg limits).

    Returns the model's text output, or raises on failure/timeout.
    Delegates to ClaudeRuntime.oneshot() — single source of truth.
    Callers that catch subprocess.TimeoutExpired should also catch RuntimeError
    since oneshot() normalises all failures to a None return which we raise here.
    """
    result = _agent_runtime.get_runtime('claude').oneshot(
        prompt=instruction,
        model=model,
        stdin_text=body,
        cwd=str(Path.home()),
    )
    if result is None:
        raise RuntimeError("scribe claude call failed (non-zero exit or timeout)")
    return result.text


# ── Auto model router (classifier) ──────────────────────────────────────────
# Cheap Haiku oneshot that picks one of Haiku / Sonnet / Opus for a given user
# prompt. Used to right-size dispatches and stop burning Opus budget on trivial
# Q&A. Fail-open: any error returns the caller's fallback so a flaky classifier
# never breaks the dispatch path.
#
# Single token output keeps the call small (~5 tokens total). The prompt biases
# conservative — when in doubt, pick the larger model. The classifier is opt-in
# via CONFIG['auto_model_enabled']; when off, _route_dispatch_model is a no-op
# pass-through.

_AUTO_MODEL_CLASSIFIER_PROMPT = (
    "You classify a coding-assistant request into one of three Claude models: "
    "Haiku (H), Sonnet (S), or Opus (O).\n\n"
    "Output EXACTLY one character — H, S, or O. No other text, no punctuation.\n\n"
    "Pick H ONLY when the request is clearly trivial: a single fact question, "
    "a one-line lookup, casual chat, a yes/no, or a tiny one-file edit with "
    "obvious intent.\n\n"
    "Pick O ONLY when the request is clearly complex: multi-step refactor, "
    "architecture or design decision, cross-file debugging, deep code generation, "
    "long planning, or anything that explicitly asks for careful reasoning.\n\n"
    "Pick S for everything else — most normal coding tasks land here.\n\n"
    "Bias CONSERVATIVE: prefer S over H when unsure; prefer S over O when unsure."
)

_AUTO_MODEL_VALID = {'H': 'claude-haiku-4-5-20251001', 'S': 'claude-sonnet-4-6', 'O': 'claude-opus-4-8'}


def _route_dispatch_model(prompt, fallback_model):
    """Return (model_name, source) where model_name is a full Claude model ID.

    Returns one of _AUTO_MODEL_VALID's values (explicit full IDs, not aliases)
    from the classifier result. source is 'auto' when the classifier ran,
    'manual' when auto is off, 'fallback' when the classifier errored.
    fallback_model is used verbatim when auto is off and as the safety net
    when the classifier fails.
    """
    if not CONFIG.get('auto_model_enabled', False):
        return fallback_model, 'manual'
    if not prompt or not prompt.strip():
        return fallback_model, 'fallback'
    classifier_model = CONFIG.get('auto_model_classifier_model', '') or 'haiku'
    try:
        raw = _scribe_call(classifier_model, _AUTO_MODEL_CLASSIFIER_PROMPT, prompt.strip())
    except Exception:
        return fallback_model, 'fallback'
    token = (raw or '').strip().upper()[:1]
    chosen = _AUTO_MODEL_VALID.get(token)
    if not chosen:
        return fallback_model, 'fallback'
    return chosen, 'auto'


def _extract_transcript_telemetry(path):
    """Read a JSONL transcript and extract cumulative token usage by model.

    Returns {'model': str, 'input_tokens': int, 'output_tokens': int,
             'cache_read_tokens': int, 'model_tokens': {model: total_tokens}}
    or {} on any failure. Never raises. Indicative, not billing-accurate.
    """
    if not path:
        return {}
    try:
        model_tokens = {}  # model -> {input, output}
        with open(path, encoding='utf-8', errors='replace') as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    m = json.loads(line)
                except Exception:
                    continue
                msg = m.get('message') if isinstance(m.get('message'), dict) else None
                if not msg:
                    continue
                model = msg.get('model', '')
                usage = msg.get('usage')
                if not model or not isinstance(usage, dict):
                    continue
                if model not in model_tokens:
                    model_tokens[model] = {'input': 0, 'output': 0, 'cache_read': 0}
                model_tokens[model]['input'] += int(usage.get('input_tokens') or 0)
                model_tokens[model]['output'] += int(usage.get('output_tokens') or 0)
                model_tokens[model]['cache_read'] += int(
                    usage.get('cache_read_input_tokens') or 0)
        if not model_tokens:
            return {}
        dominant = max(model_tokens.items(),
                       key=lambda x: x[1]['input'] + x[1]['output'])[0]
        return {
            'model': dominant,
            'input_tokens': sum(v['input'] for v in model_tokens.values()),
            'output_tokens': sum(v['output'] for v in model_tokens.values()),
            'cache_read_tokens': sum(v['cache_read'] for v in model_tokens.values()),
            'model_tokens': {m: v['input'] + v['output']
                             for m, v in model_tokens.items()},
        }
    except Exception:
        return {}


# ── Phase 4 Distiller registration ───────────────────────────────────────────
# distiller.py is the cross-session learning observer (v2.1 spec). Registered
# here AFTER _scribe_call and _scribe_render_transcript are defined so the
# module can call them directly. Best-effort; failure to register doesn't
# break the rest of server startup.
import distiller as _distiller
try:
    _SKILLS_ROOT = Path(__file__).parent / 'data' / 'skills'
    _distiller.register(
        data_root=DATA_DIR,
        skills_root=_SKILLS_ROOT,
        atomic_write_text=_atomic_write_text,
        scribe_call=_scribe_call,
        scribe_render_transcript=_scribe_render_transcript,
        log=_log,
        load_project=load_project,
        save_project=save_project,
        now_iso=now_iso,
        config_get=lambda k, d=None: CONFIG.get(k, d),
        get_per_project_semaphore=_get_checkpoint_sema,
    )
except Exception as _distiller_reg_err:
    _log(f"[distiller] registration failed: {_distiller_reg_err!r} — "
         f"Distiller will be inert this run")


def _scribe_extract(project, session):
    """Leg A scribe. Returns (entry_text, outcome_reason).

    entry_text is None when the caller must fall back to the legacy
    stdout-tail summary. Never raises. Dispatch-time incognito/housekeeping
    gate is asserted here too so Phase-2 mid-session triggers inherit it.
    """
    if not CONFIG.get('scribe_enabled', True):
        return None, 'disabled'
    if session.get('incognito') or session.get('housekeeping'):
        return None, 'gated'
    pid = project.get('id', '')
    pp = project.get('project_path', '')
    csid = session.get('claude_session_id', '')
    if not csid:
        return None, 'no_csid'
    tf = _find_transcript_file(pp, csid)
    if not tf:
        return None, 'no_transcript'
    with _scribe_lock:
        if pid in _scribing_projects:
            return None, 'busy'
        _scribing_projects.add(pid)
    try:
        try:
            transcript = _scribe_render_transcript(tf)
        except Exception:
            return None, 'parse_empty'
        model = CONFIG.get('scribe_model', '') or 'haiku'
        return _scribe_summarize_text(transcript, model)
    finally:
        with _scribe_lock:
            _scribing_projects.discard(pid)


def _scribe_summarize_text(text, model):
    """Core: rendered-transcript text → (one_line_summary, 'extracted') or
    (None, reason). Thin-transcript guard + single/map-reduce + refusal guard.
    No I/O, no locks — shared by _scribe_extract (whole transcript, completion
    path) and the Step-6 checkpoint worker (delta). Never raises.
    """
    _stripped = (text or '').strip()
    _has_activity = any(
        ln.startswith(_SCRIBE_ACTIVITY_PREFIXES)
        for ln in _stripped.splitlines())
    if not _has_activity and len(_stripped) < _SCRIBE_THIN_TEXT_CHARS:
        # No tool/think activity and only a trivial blip (aborted/no-op).
        # Caller falls back rather than persist a hallucinated reply.
        return None, 'parse_empty'
    try:
        if len(_stripped) <= _SCRIBE_SINGLE_LIMIT:
            out = _scribe_call(model, _SCRIBE_PROMPT, _stripped)
        else:
            chunks, cur, n = [], [], 0
            for ln in _stripped.split('\n'):
                cur.append(ln)
                n += len(ln) + 1
                if n >= _SCRIBE_SINGLE_LIMIT:
                    chunks.append('\n'.join(cur))
                    cur, n = [], 0
            if cur:
                chunks.append('\n'.join(cur))
            partials = []
            for ch in chunks:
                try:
                    partials.append(_scribe_call(model, _SCRIBE_MAP_PROMPT, ch))
                except Exception:
                    pass
            if not partials:
                return None, 'model_error'
            out = _scribe_call(
                model, _SCRIBE_REDUCE_PROMPT,
                '\n'.join(f"- {p}" for p in partials if p))
    except subprocess.TimeoutExpired:
        return None, 'model_error'
    except Exception:
        return None, 'model_error'
    out = (out or '').strip().replace('\n', ' ').strip()
    if not out:
        return None, 'model_error'
    if any(mk in out.lower() for mk in _SCRIBE_REFUSAL_MARKERS):
        return None, 'model_refused'
    return out[:300], 'extracted'


def _condense_integrity_check(mem_path, pre_mem, pre_wm, rc):
    """Post-condense safety net for MEMORY.md.

    A condense run is an external `claude` subprocess that rewrites MEMORY.md
    with the Write tool. If it is truncated mid-task (e.g. it hits --max-turns
    before the write step, the failure that motivated this guard) it can leave
    the file empty, drop the managed-region sentinels, nuke the curated index,
    or — worst — delete a `clayrune:wm:` watermark and lose a live session's
    progress. Compare the post-run file against the pre-run snapshot and decide:

      ('ok', ...)      file intact (or no pre-image to protect)
      ('heal', ...)    structure fine but live watermark(s) dropped — caller
                       re-injects them, preserving the agent's curation work
      ('restore', ...) hard corruption — caller rewrites `pre_mem` verbatim

    Returns (action, reason, status_kw). status_kw is merged into the per-
    project condense status so chronic turn-cap failures stay visible in
    telemetry instead of silently self-healing on the next trigger.
    """
    if pre_mem is None:
        # No pre-image captured — can only trust the exit code.
        if rc not in (0, None):
            return 'ok', f'agent exited {rc}', {
                'state': 'error', 'turn_cap': True,
                'error': f'condense agent exited {rc} (likely --max-turns); '
                         'no pre-image captured to verify integrity'}
        return 'ok', '', {}
    try:
        post = mem_path.read_text(encoding='utf-8') if mem_path.exists() else ''
    except Exception as e:
        return 'restore', f'post-read failed ({e})', {
            'state': 'error',
            'error': f'MEMORY.md unreadable after condense ({e}); restored pre-image'}
    if not post.strip():
        return 'restore', 'empty after condense', {
            'state': 'error',
            'error': 'MEMORY.md empty after condense; restored pre-image'}

    if (_MEM_BEGIN in pre_mem and _MEM_END in pre_mem
            and not (_MEM_BEGIN in post and _MEM_END in post)):
        return 'restore', 'managed-region sentinels missing', {
            'state': 'error',
            'error': 'condense dropped the managed-region sentinels; restored pre-image'}

    pre_cur = _mem_split_full(pre_mem)[0]
    post_cur = _mem_split_full(post)[0]
    if len(pre_cur) > 200 and len(post_cur) < 0.25 * len(pre_cur):
        return 'restore', 'curated index lost >75%', {
            'state': 'error',
            'error': 'condense truncated the curated index (>75% lost); '
                     'restored pre-image'}

    post_wm = set(_mem_split_full(post)[2])
    missing_wm = [w for w in (pre_wm or []) if w not in post_wm]
    if missing_wm:
        if rc not in (0, None):
            kw = {'state': 'error', 'turn_cap': True,
                  'wm_repaired': len(missing_wm),
                  'error': f'condense agent exited {rc} (likely --max-turns) and '
                           f'dropped {len(missing_wm)} live-session watermark(s); '
                           're-injected, no progress lost'}
        else:
            kw = {'state': 'done', 'wm_repaired': len(missing_wm)}
        return 'heal', f'{len(missing_wm)} watermark(s) dropped', kw

    if rc not in (0, None):
        return 'ok', f'agent exited {rc}', {
            'state': 'error', 'turn_cap': True,
            'error': f'condense agent exited {rc} (likely --max-turns); '
                     'MEMORY.md integrity OK — no facts or watermarks lost'}
    return 'ok', '', {}


# ── Leg C structured condense (docs/CONDENSE_STRUCTURED_DESIGN.md) ────────────
# Replaces the free `claude -p` + Write agent with ONE non-agentic JSON model
# call (reusing _scribe_call: --max-turns 1, no tools, stdin) whose decision
# list the server applies deterministically through the same leaf-locked
# atomic writer the completion scribe + Step-6 use. The model never touches the
# filesystem and never sees `clayrune:wm:` watermarks. Gated by
# CONFIG['condense_mode'] == 'structured' (default 'agent').
_CONDENSE_ACTIONS = ('keep', 'demote', 'fold')   # the only valid per-entry verbs
_CONDENSE_ARCHIVE_TAIL_KB = 4   # dedupe-context slice of the archive sent in
_CONDENSE_PLAN_PROMPT = (
    "You are the memory-condense decider (SPEC Leg C). You are NOT an agent: "
    "you have no tools, you do not write files. You receive a JSON object and "
    "you return ONLY a JSON object — no prose, no markdown fences.\n\n"
    "INPUT shape:\n"
    "  curated_headings: exact heading lines of the hand-curated pointer index\n"
    "  entries: [{id, text}] — raw machine-written `- [date] ...` session-log lines\n"
    "  archive_tail: recent already-archived lines (dedupe context only)\n"
    "  line_budget: target max lines for the whole auto-loaded file\n\n"
    "For EACH entry decide, by VALUE not recency:\n"
    "  • keep   — recent, not yet foldable; stays in the session log\n"
    "  • demote — no lasting value as a pointer; the raw line is moved to the\n"
    "             permanent archive (still searchable). NOTHING is erased.\n"
    "  • fold   — its durable insight belongs in the curated index. Provide\n"
    "             `fold_into` (an EXACT string from curated_headings) and\n"
    "             `pointer_line` (one new `- [...]` index line, single line,\n"
    "             no newline, must NOT contain the substring 'clayrune:'). The\n"
    "             raw entry is ALSO archived (fact preserved verbatim).\n\n"
    "Rules: never invent a heading; `fold_into` must match curated_headings\n"
    "verbatim. Prefer fold/demote enough that the file trends under\n"
    "line_budget, but never sacrifice a hard-won fact (paths, line numbers,\n"
    "symbol names, config keys, thresholds, gotchas) — those go to fold or\n"
    "demote, never 'keep-and-hope'. Entries you don't mention default to keep.\n\n"
    "OUTPUT exactly: {\"entry_decisions\":[{\"id\":\"..\",\"action\":\"keep|demote|fold\","
    "\"fold_into\":\"..\",\"pointer_line\":\"..\"}],\"curated_rewrite\":null}\n"
    "(`fold_into`/`pointer_line` only on fold entries; `curated_rewrite` must "
    "be null — wholesale curated re-authoring is not permitted in this mode.)"
)


def _condense_parse_json(raw):
    """Extract the JSON object from a model reply (tolerates ``` fences /
    leading prose). Returns dict or None."""
    s = (raw or '').strip()
    if s.startswith('```'):
        s = s.split('```', 2)[1] if s.count('```') >= 2 else s.strip('`')
        if s.lstrip().lower().startswith('json'):
            s = s.lstrip()[4:]
    i, j = s.find('{'), s.rfind('}')
    if i < 0 or j <= i:
        return None
    try:
        v = json.loads(s[i:j + 1])
        return v if isinstance(v, dict) else None
    except Exception:
        return None


def _validate_condense_payload(payload, valid_ids, valid_headings):
    """Schema + invariant gate, applied BEFORE the server writes anything.
    Returns (True, '') or (False, reason). Strictly pre-write: a reject leaves
    MEMORY.md untouched (no pre-image / restore needed)."""
    if not isinstance(payload, dict):
        return False, 'not_object'
    if payload.get('curated_rewrite') is not None:
        return False, 'curated_rewrite_forbidden_v1'
    decs = payload.get('entry_decisions')
    if not isinstance(decs, list):
        return False, 'entry_decisions_not_list'
    seen = set()
    for d in decs:
        if not isinstance(d, dict):
            return False, 'decision_not_object'
        did = d.get('id')
        if did not in valid_ids:
            return False, 'unknown_id'
        if did in seen:
            return False, 'duplicate_id'
        seen.add(did)
        act = d.get('action')
        if act not in _CONDENSE_ACTIONS:
            return False, 'bad_action'
        if act == 'fold':
            fi = d.get('fold_into')
            pl = d.get('pointer_line')
            if fi not in valid_headings:
                return False, 'fold_into_not_a_heading'
            if not isinstance(pl, str) or not pl.strip():
                return False, 'empty_pointer_line'
            if '\n' in pl or '\r' in pl:
                return False, 'multiline_pointer_line'
            if 'clayrune:' in pl:
                return False, 'pointer_line_synthesizes_machinery'
    return True, ''


def _condense_plan(project):
    """Assemble bounded read-only input, make ONE non-agentic model call, parse
    + validate. Returns (payload|None, reason, model_ms). Never raises."""
    t0 = _time.time()
    try:
        mem_path = _get_memory_path(project)
        if not mem_path.exists():
            return None, 'no_memory_file', 0
        curated, entries, _wm = _mem_split_full(
            _mem_migrate(mem_path.read_text(encoding='utf-8')))
        if not entries:
            return None, 'noop', 0
        # Collect curated headings as fold targets, but skip any '#' line
        # inside a fenced code block (a shell comment / ATX-looking line in a
        # ``` fence is not a real section) — otherwise a pointer could be
        # folded into code. _condense_apply additionally requires the heading
        # to resolve UNIQUELY at apply time, else it downgrades to demote.
        valid_headings, _in_fence = [], False
        for ln in curated.splitlines():
            if ln.lstrip().startswith('```'):
                _in_fence = not _in_fence
                continue
            if not _in_fence and ln.lstrip().startswith('#'):
                valid_headings.append(ln.strip())
        in_entries, valid_ids = [], set()
        for e in entries:
            eid = _sha8(e)
            valid_ids.add(eid)
            in_entries.append({'id': eid, 'text': e})
        archive_tail = ''
        ap = _get_archive_path(project)
        if ap.exists():
            try:
                blob = ap.read_text(encoding='utf-8')
                archive_tail = blob[-_CONDENSE_ARCHIVE_TAIL_KB * 1024:]
            except Exception:
                pass
        body = json.dumps({
            'curated_headings': valid_headings,
            'entries': in_entries,
            'archive_tail': archive_tail,
            'line_budget': int(CONFIG.get('index_line_budget', 160) or 160),
        }, ensure_ascii=False)
        # Default to haiku, NOT sonnet. The structured condense is a one-shot
        # JSON call with no tools and a schema-validated reply — same shape as
        # Scribe, which already defaults to haiku. Sonnet's reasoning depth is
        # wasted here and routinely times out on 30KB+ stdin payloads (live:
        # 91 model_errors + 58 timeouts vs 5 successes before this default
        # was corrected). Users who want sonnet can still set condense_model
        # explicitly in Settings.
        model = CONFIG.get('condense_model', '') or 'haiku'
        try:
            raw = _scribe_call(model, _CONDENSE_PLAN_PROMPT, body)
        except subprocess.TimeoutExpired:
            return None, 'model_timeout', int((_time.time() - t0) * 1000)
        except Exception:
            return None, 'model_error', int((_time.time() - t0) * 1000)
        ms = int((_time.time() - t0) * 1000)
        payload = _condense_parse_json(raw)
        if payload is None:
            return None, 'parse_error', ms
        ok, why = _validate_condense_payload(
            payload, valid_ids, set(valid_headings))
        if not ok:
            return None, why, ms
        return payload, 'ok', ms
    except Exception as e:
        # Static reason — keeps the colon-suffixed telemetry key bounded
        # (raw exception text must never become a _scribe_stats.json key).
        # Detail goes to the log + the bounded last-write-wins status field.
        _log(f"[condense] {project.get('id','')}: plan exception — {e}")
        return None, 'plan_exc', int((_time.time() - t0) * 1000)


def _condense_apply(project, payload):
    """Rebased, transactional apply under the SAME leaf lock the completion
    scribe + Step-6 use. Decisions are keyed by _sha8(entry); any decision
    whose entry vanished meanwhile (Step-6 fold / teardown / floor) is silently
    skipped. wm markers pass through untouched. Returns a stats dict."""
    pid = project.get('id', '')
    mem_path = _get_memory_path(project)
    hard_floor = int(CONFIG.get('index_line_hard_floor', 185) or 185)
    decs = {d['id']: d for d in payload.get('entry_decisions', [])}
    st = {'kept': 0, 'demoted': 0, 'folded': 0,
          'skipped_rebased': 0, 'fold_downgraded': 0, 'curated_lines': 0}
    with _get_mem_write_lock(pid):
        existing = (mem_path.read_text(encoding='utf-8')
                    if mem_path.exists() else '')
        curated, entries, wm = _mem_split_full(_mem_migrate(existing))
        cur_lines = curated.splitlines()
        cur_norm = {ln.strip() for ln in cur_lines}
        present_ids = set()
        new_entries, overflow = [], []
        for e in entries:
            eid = _sha8(e)
            present_ids.add(eid)
            # Duplicate byte-identical entry lines hash to the same id, so one
            # decision intentionally applies to ALL of them. This is safe and
            # desirable: demote/fold route every copy verbatim to the
            # append-only archive (no fact lost) and collapse the noise; keep
            # is a per-copy no-op. _validate_condense_payload already rejects
            # duplicate ids in the decision LIST, so the model can't disagree
            # with itself across copies.
            d = decs.get(eid)
            act = d.get('action') if d else 'keep'
            if act == 'demote':
                overflow.append(e)
                st['demoted'] += 1
            elif act == 'fold':
                heading = d.get('fold_into')
                pl = d.get('pointer_line', '').strip()
                hits = [k for k, ln in enumerate(cur_lines)
                        if ln.strip() == heading]
                if len(hits) != 1:
                    # Heading vanished, or is ambiguous (0 or >1 matches since
                    # plan time) — never misplace a pointer or lose the fact:
                    # demote the raw entry, skip the curated insert.
                    overflow.append(e)
                    st['fold_downgraded'] += 1
                    continue
                if pl and pl not in cur_norm:
                    cur_lines.insert(hits[0] + 1, pl)
                    cur_norm.add(pl)
                overflow.append(e)        # fact preserved verbatim in archive
                st['folded'] += 1
            else:
                new_entries.append(e)
                st['kept'] += 1
        # Decisions whose target entry is gone (concurrent Step-6 / teardown).
        st['skipped_rebased'] = sum(
            1 for did in decs if did not in present_ids)
        curated2 = '\n'.join(cur_lines)
        # Mechanical line floor backstop (same rule as _commit_managed_entry).
        while new_entries and len(_mem_compose(
                curated2, new_entries, wm).splitlines()) > hard_floor:
            overflow.append(new_entries.pop(0))
        # Post-apply curated size — a gauge (not additive) so soak can watch
        # the model-authored curated index for monotonic low-value drift
        # (additive-only fold has no mechanical eviction path until v2).
        st['curated_lines'] = len(cur_lines)
        _append_to_archive(project, overflow)
        _atomic_write_text(mem_path, _mem_compose(curated2, new_entries, wm))
    return st


def _run_structured_condense(project):
    """Orchestrator for condense_mode='structured'. Mirrors the agent path's
    status/lock discipline; the slow model call is OUTSIDE the leaf lock.
    Caller (_dispatch_condense) already holds the _condensing_projects guard
    and this MUST discard it. Never raises."""
    pid = project['id']
    _set_condense_status(pid, state='running', started_at=now_iso(),
                         finished_at=None, error=None,
                         turn_cap=False, wm_repaired=0,
                         bytes_before=_condense_combined_bytes(project),
                         bytes_after=None)
    try:
        payload, reason, ms = _condense_plan(project)
        if payload is None:
            if reason in ('noop', 'no_memory_file'):
                _scribe_stat(pid, f'condense_{reason}')
                _set_condense_status(pid, state='done', model_ms=ms)
            else:
                _scribe_stat(pid, f'condense_rejected:{reason}')
                _set_condense_status(
                    pid, state='error', model_ms=ms,
                    error=f'structured condense not applied ({reason}); '
                          'MEMORY.md left untouched')
            return
        st = _condense_apply(project, payload)
        _scribe_stat(pid, 'condense_structured_ok')
        for k in ('kept', 'demoted', 'folded'):
            _scribe_stat(pid, f'condense_entries_{k}', st.get(k, 0))
        _scribe_stat(pid, 'condense_decisions_skipped_rebased',
                     st.get('skipped_rebased', 0))
        _scribe_stat(pid, 'condense_fold_downgraded',
                     st.get('fold_downgraded', 0))
        _set_condense_status(pid, state='done', model_ms=ms, **st)
        _log(f"[condense] {pid}: structured ok — "
             f"kept={st['kept']} demoted={st['demoted']} "
             f"folded={st['folded']} skipped_rebased={st['skipped_rebased']}")
    except Exception as e:
        _log(f"[condense] {pid}: structured error — {e}")
        _set_condense_status(pid, state='error', error=str(e))
    finally:
        _set_condense_status(pid, finished_at=now_iso(),
                             bytes_after=_condense_combined_bytes(project))
        with _condense_lock:
            if _condense_status.get(pid, {}).get('state') == 'running':
                _condense_status[pid]['state'] = 'done'
            _condensing_projects.discard(pid)


def _dispatch_condense(project):
    """Launch a housekeeping agent to condense memory + CLAUDE.md for a project."""
    pid = project['id']
    with _condense_lock:
        if pid in _condensing_projects:
            return
        _condensing_projects.add(pid)
        _condense_triggered_at[pid] = _time.time()

    # Leg C executor selection. 'structured' (docs/CONDENSE_STRUCTURED_DESIGN.md)
    # replaces the free claude -p + Write agent below with one non-agentic JSON
    # call applied server-side. The structured runner owns the
    # _condensing_projects discard in its finally, same as the agent _run.
    if (CONFIG.get('condense_mode', 'agent') or 'agent') == 'structured':
        threading.Thread(target=_run_structured_condense,
                         args=(project,), daemon=True).start()
        return

    mem_path = _get_memory_path(project)
    archive_path = _get_archive_path(project)
    pp = project.get('project_path', '')

    # P2-1: mark condensation in-flight (bytes_before = pre-condense size).
    _set_condense_status(pid, state='running', started_at=now_iso(),
                         finished_at=None, error=None,
                         turn_cap=False, wm_repaired=0,
                         bytes_before=_condense_combined_bytes(project),
                         bytes_after=None)

    # Check if CLAUDE.md exists and is large enough to warrant condensation
    claude_md_path = Path(pp) / 'CLAUDE.md' if pp else None
    claude_md_big = False
    if claude_md_path and claude_md_path.exists():
        try:
            claude_md_big = claude_md_path.stat().st_size > 15 * 1024  # > 15KB
        except OSError:
            pass

    budget = int(CONFIG.get('index_line_budget', 160) or 160)
    prompt_parts = [
        "You are a memory housekeeping agent (SPEC Leg C model tier). Your ONLY "
        "job is to curate the project context files so they stay concise and "
        "effective. You decide by VALUE, never by recency.\n",
        f"## MEMORY.md curation — target: the WHOLE file under {budget} LINES\n"
        f"(The harness only auto-loads ~200 lines; staying under {budget} keeps "
        f"headroom. This is a LINE budget, not a KB target.)\n"
        f"1. Read {mem_path}\n"
        f"2. Read {archive_path} (if it exists)\n"
        "3. MEMORY.md has two regions, treat them differently:\n"
        "   - CURATED region (everything ABOVE the "
        "`<!-- clayrune:managed:begin -->` sentinel): the hand-curated pointer "
        "index. You ARE permitted to compact THIS region (you are the only "
        "agent allowed to): merge overlapping pointers/sections covering the "
        "same subsystem, drop stale 'as of YYYY-MM-DD' notes clearly superseded "
        "by a later section, cut narration but keep the fact.\n"
        "   - MANAGED region (between `<!-- clayrune:managed:begin -->` and "
        "`<!-- clayrune:managed:end -->`, under `## Session Log`): raw "
        "machine-written session entries. For EACH entry decide, by value: "
        "(a) fold its durable insight into the matching curated pointer/topic "
        "then remove the raw entry; (b) if it has no lasting value, DEMOTE it "
        "(move it) to the archive; (c) keep it in the managed region only if "
        "it's recent and not yet foldable. Never keep/drop by recency alone.\n"
        "4. KEEP THE FORMAT: the rewritten file must still have the "
        "`<!-- clayrune:managed:begin -->` / `## Session Log` / "
        "`<!-- clayrune:managed:end -->` structure intact. The managed region "
        "may legitimately end up EMPTY after folding — that is fine; keep the "
        "sentinels and header. CRITICAL: any line beginning "
        "`<!-- clayrune:wm:` is a live-session watermark — PRESERVE IT "
        "VERBATIM, do not fold/move/delete/reformat it (deleting one loses a "
        "running session's progress and forces a re-scribe from zero).\n"
        "5. NEVER hard-delete a fact. The only permitted deletions are exact "
        "duplicates or an entry STRICTLY superseded by a newer one that wholly "
        "contains it. 'Not worth a curated slot' means DEMOTE to the archive "
        "(still searchable cold storage), never erase.\n"
        "6. DO NOT lose hard-won facts. Preserve verbatim: file paths, line "
        "numbers, function/class names, config keys, exact numeric thresholds, "
        "API signatures, command snippets, and any 'gotcha' warnings.\n"
        f"7. Append demoted/overflow entries to {archive_path} (create it if "
        f"needed). NEVER delete or truncate the archive — it is permanent "
        f"searchable cold storage (SPEC D3).\n"
        f"8. Write the curated result back to {mem_path}. Target under {budget} "
        f"lines; if after honest folding it is still slightly over, that is "
        f"acceptable — do NOT delete critical facts just to hit a number.\n",
    ]

    if claude_md_big:
        prompt_parts.append(
            f"\n## CLAUDE.md condensation — target under 15KB\n"
            f"9. Read {claude_md_path}\n"
            "10. This file contains project instructions and context that Claude CLI loads natively. "
            "Condense it while preserving ALL critical information:\n"
            "   - Keep all instructions, rules, and constraints verbatim.\n"
            "   - Merge duplicate/overlapping sections.\n"
            "   - Remove redundant examples, excessive formatting, and verbose explanations.\n"
            "   - Compress session logs / historical notes into brief summaries.\n"
            "   - Preserve code snippets, API references, and config patterns exactly.\n"
            f"11. Write the condensed result back to {claude_md_path}. Target under 15KB; do NOT "
            f"strip critical rules just to hit a number.\n"
        )

    prompt_parts.append(
        "\nBE TURN-EFFICIENT (you have a limited turn budget): read EVERY "
        "input file you need in your FIRST turn using parallel tool calls, "
        "do all the folding/demotion reasoning, then write each output file "
        "EXACTLY ONCE. Do not re-read a file you have already read. The write "
        "step is what matters — do not spend the whole budget exploring.\n"
        "\nDo NOT create any other files. Do NOT modify any code. Only touch the files listed above."
    )
    prompt = '\n'.join(prompt_parts)

    model = CONFIG.get('condense_model', '') or 'sonnet'
    # --max-turns 14 (was 5): the workload is read MEMORY.md + read archive
    # (+ optionally read CLAUDE.md) + fold/demote N entries + append archive
    # + rewrite MEMORY.md. 5 turns were routinely exhausted on the reads
    # alone, so the CLI exited 1 *before the write step* and the run was
    # flagged ERROR (it only "self-healed" because the next trigger retried).
    # The post-run integrity guard below makes a truncated run safe; this
    # gives it enough room to actually finish.
    cmd = [_resolve_claude(), '-p', prompt, '--model', model, '--max-turns', '14',
           '--print', '--verbose', '--output-format', 'stream-json',
           '--dangerously-skip-permissions']

    cwd = pp if pp and Path(pp).is_dir() else str(Path.home())

    def _run():
        session_id = f'condense_{uuid.uuid4().hex[:8]}'
        # Pre-image snapshot for the post-run integrity guard. Captured here
        # (just before launch) so a truncated/botched run can never corrupt
        # MEMORY.md or lose a live-session watermark.
        try:
            pre_mem = mem_path.read_text(encoding='utf-8') if mem_path.exists() else None
        except Exception:
            pre_mem = None
        pre_wm = _mem_split_full(pre_mem)[2] if pre_mem else []
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

            # Reuse existing stream reader (blocks until proc exits)
            _read_agent_stream(proc, session)

            # Post-run safety net: a truncated condense (e.g. --max-turns hit
            # before the write step) must never leave MEMORY.md corrupted or
            # drop a live-session watermark.
            rc = proc.returncode
            action, reason, kw = _condense_integrity_check(
                mem_path, pre_mem, pre_wm, rc)
            if action == 'restore':
                try:
                    mem_path.write_text(pre_mem, encoding='utf-8')
                    _log(f"[condense] {pid}: integrity FAIL ({reason}) — "
                         f"restored pre-image")
                except Exception as e:
                    _log(f"[condense] {pid}: RESTORE FAILED ({e}) — {reason}")
            elif action == 'heal':
                try:
                    cur, ent, wm = _mem_split_full(
                        mem_path.read_text(encoding='utf-8'))
                    have = set(wm)
                    for w in pre_wm:
                        if w not in have:
                            wm.append(w)
                            have.add(w)
                    mem_path.write_text(_mem_compose(cur, ent, wm),
                                        encoding='utf-8')
                    _log(f"[condense] {pid}: healed ({reason}) — re-injected "
                         f"dropped watermark(s), kept agent curation")
                except Exception as e:
                    # Heal failed — fall back to full restore to protect the
                    # load-bearing watermark over the agent's curation.
                    try:
                        mem_path.write_text(pre_mem, encoding='utf-8')
                    except Exception:
                        pass
                    _log(f"[condense] {pid}: heal FAILED ({e}) — restored "
                         f"pre-image")
                    kw = {'state': 'error',
                          'error': f'watermark heal failed ({e}); '
                                   'restored pre-image'}
            if kw:
                _set_condense_status(pid, **kw)
        except Exception as e:
            _log(f"[condense] error for {pid}: {e}")
            _set_condense_status(pid, state='error', error=str(e),
                                 finished_at=now_iso())
        finally:
            # P2-1: record outcome. bytes_after = post-condense size; a
            # still-'running' state means the body finished without raising.
            _set_condense_status(pid, finished_at=now_iso(),
                                 bytes_after=_condense_combined_bytes(project))
            with _condense_lock:
                if _condense_status.get(pid, {}).get('state') == 'running':
                    _condense_status[pid]['state'] = 'done'
                _condensing_projects.discard(pid)

    threading.Thread(target=_run, daemon=True).start()


# Mobile brief replies — the directive is silently prepended to messages from
# clients that POST `client="mobile"` when the global toggle is on. Only the
# augmented message reaches claude; the user's chat bubble (log_lines + the
# frontend's local echo) shows the original verbatim. This is the entire
# "Telegram-mode" mechanism: no dedicated UI, no new endpoints, no per-turn
# state — the agent simply gets a one-line nudge per user message.
_BRIEF_REPLY_DIRECTIVE = (
    "[the user is messaging you from a phone — reply in Telegram style: "
    "short, conversational, one idea per message; avoid headers, bullets, "
    "and long code blocks. They can switch to PC and ask follow-ups if they "
    "want more detail. This instruction is hidden from the user.]"
)

# Device-neutral variant for `brief_replies_always_enabled` (applies on desktop
# too, so it can't say "from a phone" / "switch to PC"). Per-turn prepend used
# when sticky_agent_settings is OFF; mirrors the hard framing of the
# system-baked variant. Brevity targets PROSE only — necessary code, file
# edits, and tool work are never truncated.
_BRIEF_REPLY_DIRECTIVE_ALWAYS = (
    "[BINDING for this reply — brevity is a hard rule, not a preference. Lead "
    "with the answer; hard ceiling ~4 sentences of prose (more only if the user "
    "asked for detail); no preamble, no restating the question, no closing "
    "offers. Bullets only to enumerate 3+ discrete items. Before sending, cut "
    "every non-load-bearing sentence. This caps PROSE ONLY — never shorten "
    "necessary code, file edits, tool work, or findings; completeness means "
    "substance, not length. This instruction is hidden from the user.]"
)

# System-prompt variant of the device-neutral brevity nudge, used when
# `sticky_agent_settings` is on: baked once into _build_agent_context (cached,
# system-level authority) instead of re-prepended to every user turn. Framed as
# a BINDING rule (imperative, with a pre-send self-check and an explicit
# carve-out so it can't be rationalized away against the "be complete" rules).
# Governs PROSE only.
_BRIEF_REPLY_DIRECTIVE_SYSTEM = (
    "REPLY LENGTH — BINDING RULE for this session (this is a hard constraint, "
    "not a stylistic preference): default to the SHORTEST reply that fully "
    "answers. Lead with the answer in the first sentence. Hard ceiling: ~4 "
    "sentences of prose per reply (more only if the user explicitly asks for "
    "detail). No preamble, no restating the question, no recap of what you just "
    "did, no closing offers. Use bullets ONLY to enumerate 3+ discrete items, "
    "never to pad. Before sending, re-read your draft and delete every sentence "
    "that is not load-bearing to the answer. "
    "This caps PROSE ONLY — never shorten or omit necessary code, file edits, "
    "tool calls, or actual findings. 'Complete and fully analyzed' refers to "
    "SUBSTANCE, not word count: a correct answer in two sentences outranks a "
    "thorough-sounding one in ten. When in doubt, cut."
)


def _apply_mobile_brief(message: str, request_data: dict) -> str:
    """Return `message` augmented with a hidden brief-reply directive.

    Two independent server toggles drive this:
      * `brief_replies_always_enabled` — when on, EVERY client (desktop too)
        gets the device-neutral directive. Supersedes the phone-only gate.
      * `mobile_brief_replies_enabled` — when on, only requests that declared
        `client="mobile"` get the phone-worded directive.
    If neither applies, `message` is returned unchanged.

    Callers MUST use the returned (augmented) string for whatever reaches
    claude (stdin write, spawn arg, _dispatch_agent_internal task) and keep
    the ORIGINAL `message` for anything user-visible (log_lines, telemetry).
    """
    if CONFIG.get('brief_replies_always_enabled'):
        # When sticky_agent_settings is on, this directive is baked into the
        # spawn-time system prompt (_build_agent_context) — don't also prepend
        # it per turn, or it doubles up.
        if CONFIG.get('sticky_agent_settings', False):
            return message
        return f"{_BRIEF_REPLY_DIRECTIVE_ALWAYS}\n\n{message}"
    if not CONFIG.get('mobile_brief_replies_enabled'):
        return message
    if not isinstance(request_data, dict):
        return message
    if request_data.get('client') != 'mobile':
        return message
    return f"{_BRIEF_REPLY_DIRECTIVE}\n\n{message}"


# ─────────────────────────────────────────────────────────────────────────────
# Multi-provider dispatch (non-claude providers go through this)
# ─────────────────────────────────────────────────────────────────────────────


def _dispatch_via_runtime(p, task, *, provider_name,
                          incognito=False, trigger_type='manual',
                          trigger_id='', reuse_session_id='',
                          display_task=None):
    """Dispatch a session through the AgentRuntime abstraction (non-claude).

    The runtime owns: binary resolution, subprocess.Popen, reader thread,
    output parsing. MC keeps owning the agent_sessions dict — the runtime
    writes into it (proc, log_lines, status, ...) using the same shape the
    claude path uses, so the rest of MC (status badge, SSE generator, stop
    button, agent_log) keeps working without per-provider branching.
    """
    try:
        runtime = _agent_runtime.get_runtime(provider_name)
    except KeyError:
        raise ValueError(f"unknown provider: {provider_name!r}")

    pp = p.get('project_path', '')
    project_id = p.get('id', '')

    mgr = get_manager(project_id)
    mgr.ensure_guardian()

    with mgr.lock:
        if reuse_session_id and reuse_session_id not in agent_sessions:
            session_id = reuse_session_id
        else:
            session_id = uuid.uuid4().hex[:12]

        # Seed log_lines with the user's prompt so the frontend chat shows it
        # even after /agent/status overwrites the buffer with server log_lines.
        # Same fix as the claude path — see `_dispatch_agent_internal`.
        user_label = CONFIG.get('user_name') or 'User'
        _seed_task = display_task if display_task is not None else task
        # Pre-create the session dict so SSE clients can attach instantly
        session = {
            'status': 'running',
            'task': task,
            'log_lines': [f"> {user_label}: {_seed_task}"],
            'started_at': now_iso(),
            'session_id': session_id,
            'project_id': project_id,
            'mode': 'A',
            'process_alive': True,
            'last_output_time': _time.time(),
            'last_status_change_time': _time.time(),
            'guardian_state': None,
            'recovery_attempts': 0,
            'last_recovery_time': 0,
            'pending_recovery_message': None,
            'circuit_breaker_tripped': False,
            '_dispatch_time': _time.time(),
            'incognito': bool(incognito),
            'trigger_type': trigger_type,
            'trigger_id': trigger_id,
            'provider': provider_name,
            'agent_model': p.get('agent_model', '') or CONFIG.get('agent_model', ''),
        }
        agent_sessions[session_id] = session
        mgr.session_ids.add(session_id)

    # Build system_prompt blob (MEMORY/AGENT_RULES). Skip when incognito.
    system_prompt = ''
    try:
        if not incognito:
            system_prompt = _build_agent_context(p, incognito=False, task=task)
    except Exception as e:
        _log(f"[runtime-dispatch] context build failed: {e}")

    try:
        handle = runtime.dispatch(
            project_path=pp,
            task=task,
            system_prompt=system_prompt,
            resume_id='',
            mode='A',
            model=p.get('agent_model', '') or CONFIG.get('agent_model', ''),
            incognito=incognito,
            mc_session_id=session_id,
            session_dict=session,
            project_id=project_id,
            register_process=_register_process,
        )
    except Exception as e:
        session['status'] = 'error'
        session['log_lines'].append(f"[{provider_name} dispatch failed: {e}]")
        session['process_alive'] = False
        session['last_status_change_time'] = _time.time()
        raise

    if trigger_type and trigger_type != 'manual':
        try:
            _log_agent_dispatch_pending(session)
        except Exception:
            pass

    _log_agent_activity(project_id, f"Agent dispatched (provider={provider_name}): {task[:100]}")
    return session_id


def _dispatch_agent_internal(project_id, task, resume_id='', incognito=False,
                             trigger_type='manual', trigger_id='',
                             reuse_session_id='', provider_override='',
                             display_task=None):
    """Core dispatch logic shared by HTTP endpoint and scheduler.

    Returns session_id on success, raises ValueError on error.

    When incognito=True (or the project itself is the global incognito project),
    MEMORY/AGENT_RULES are skipped from --append-system-prompt and the session
    is flagged so _log_agent_completion will not write to the agent log or
    append to MEMORY.md.

    trigger_type/trigger_id annotate the resulting agent_log entry so callers
    (scheduler, hivemind dispatch) can later list "all runs for this trigger".
    Defaults are 'manual'/'' for direct user dispatch.

    reuse_session_id: when set, the new process adopts this existing MC
    session_id instead of minting a fresh uuid. Used by the scheduler when a
    continued run cold-respawns `-r <csid>` against the SAME Claude
    conversation — reusing the prior run's session_id keeps it on one agent_log
    row / one UI tab / one resolvable transcript instead of orphaning a new
    csid-less row per fire.
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

    # ── Multi-provider routing ──────────────────────────────────────────────
    # If the conversation selects a non-claude provider, dispatch through the
    # AgentRuntime abstraction instead of the legacy claude path. Provider is
    # bound per-conversation: `provider_override` (chosen in the new-chat
    # composer) wins, then the project's default seed, then the global default.
    # Default behavior (all unset OR claude) is unchanged.
    provider_name = (provider_override or p.get('provider')
                     or CONFIG.get('default_provider') or 'claude').lower()
    if provider_name != 'claude':
        try:
            return _dispatch_via_runtime(p, task, provider_name=provider_name,
                                         incognito=incognito,
                                         trigger_type=trigger_type,
                                         trigger_id=trigger_id,
                                         reuse_session_id=reuse_session_id,
                                         display_task=display_task)
        except Exception as e:
            _log(f"[dispatch] runtime '{provider_name}' failed, no fallback: {e}")
            raise

    use_streaming = p.get('use_streaming_agent', CONFIG.get('use_streaming_agent', False))

    # Check session transcript size — auto-start fresh if too large
    original_resume = resume_id
    if resume_id:
        too_large, size_bytes = _session_too_large(pp, resume_id)
        if too_large:
            size_mb = size_bytes / (1024 * 1024)
            _log(f"[dispatch] Session {resume_id} transcript is {size_mb:.1f} MB — starting fresh")
            _log_agent_activity(project_id,
                                f"Auto-fresh: previous session too large ({size_mb:.0f} MB)")
            # Prepend context about the previous session
            task = (f"[Continuing from a previous conversation (session {resume_id}) that grew too large "
                    f"to resume ({size_mb:.0f} MB). Start fresh but continue the user's request below.]\n\n{task}")
            resume_id = ''

    # Resume → pre-load the prior conversation into log_lines so the chat
    # displays the full history when the user taps Continue, not just the new
    # prompt. Same renderer the read-only /reconstruct endpoint uses, so the
    # live and read-only views are byte-identical. Cap at 300 messages —
    # enough for any practical session, bounded so a runaway transcript can't
    # spike memory. Skipped when resume_id was reset above (transcript-too-large
    # path) since we're starting fresh and the prior history isn't relevant.
    #
    # IMPORTANT: also append the new task as a user line so the continuation
    # prompt shows up in the chat. Without this, the frontend pulls the
    # server's log_lines (transcript only) which overwrites its local
    # `[prefix]` seed — and the user's new prompt disappears from the view.
    _seed_log_lines = []
    user_label = CONFIG.get('user_name') or 'User'
    # `display_task` is the user-visible original (caller-supplied so the mobile
    # brief-reply directive doesn't leak into the chat bubble). When not given,
    # fall back to `task` — for non-mobile dispatches they're identical.
    _seed_task = display_task if display_task is not None else task
    if resume_id:
        try:
            _seed_log_lines = _transcript_buffer_lines(
                pp, resume_id, user_label, max_messages=300)
            _seed_log_lines.append(f"\n> {user_label}: {_seed_task}\n")
        except Exception as e:
            _log(f"[dispatch] transcript preload failed for {resume_id[:12]}: {e}")
    else:
        # Fresh dispatch: persist the user's prompt so the frontend chat shows
        # it even after /agent/status overwrites the buffer with server log_lines.
        # Without this, the locally-seeded `> {task}` prefix gets wiped on the
        # first poll and the user only sees the agent's reply (no question).
        _seed_log_lines.append(f"> {user_label}: {_seed_task}")

    # ── Auto-router + context build, OUTSIDE mgr.lock ───────────────────────
    # RC-2 constraint (ws_003): the classifier subprocess + context build are
    # both slow (seconds) and must NOT pin mgr.lock — that's how mobile sends /
    # interrupts wedge the project for hours when claude rate-limits. Resume
    # paths skip context build (the CC subprocess already has its sysprompt).
    _sp_args = []
    _sp_path = None
    _router_fallback_reason = ''
    if resume_id:
        routed_model, routed_source, base_flags = _dispatch_with_routing(
            p, task, streaming=use_streaming)
    else:
        routed_model, routed_source, base_flags, context, _router_fallback_reason = (
            _dispatch_with_routing_parallel(
                p, task,
                context_builder=lambda: _build_agent_context(
                    p, incognito=incognito, task=task),
                streaming=use_streaming))
        _sp_args, _sp_path = _sysprompt_file_args(context)
    # Per-dispatch telemetry — best-effort; never raises. requested = the
    # model the user configured; chosen = what actually went to --model
    # (post-router). See docs/DISPATCH_AND_ROUTING_ANALYSIS.md §B.5.
    _router_stat(
        project_id,
        requested_model=(p.get('agent_model', '') or CONFIG.get('agent_model', '') or 'sonnet'),
        chosen_model=routed_model,
        source=routed_source,
        reason=_router_fallback_reason,
    )

    mgr = get_manager(project_id)
    mgr.ensure_guardian()
    with mgr.lock:
        # Reuse the prior run's id (continued scheduled thread) unless that id is
        # somehow still a live session — never clobber a running session dict.
        if reuse_session_id and reuse_session_id not in agent_sessions:
            session_id = reuse_session_id
        else:
            session_id = uuid.uuid4().hex[:12]

        if use_streaming:
            # Mode B: persistent process with stream-json stdin
            if resume_id:
                cmd = [_resolve_claude(), '-r', resume_id, *base_flags]
            else:
                cmd = [_resolve_claude(), *base_flags, *_sp_args]

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
            _sysprompt_cleanup(_sp_path, proc)

            threading.Thread(target=_hide_windows_delayed, args=(proc.pid,), daemon=True).start()
            _register_process(proc, 'Agent (Mode B)', 'agent',
                              session_id, project_id, task[:80])

            # Build initial message but DO NOT write it under mgr.lock — see
            # below. claude can stall before reading stdin (rate-limit at
            # startup, large transcript replay, auth probe, etc.) and a
            # blocking write here would hold the project lock indefinitely,
            # wedging every subsequent /agent/* call for the project.
            # Diagnosed 2026-05-28 — scheduler dispatch stuck on a rate-
            # limited claude pinned the manager lock for 5h, blackholing all
            # mobile sends/interrupts/followups to day_trading.
            _initial_msg = json.dumps({
                "type": "user",
                "message": {"role": "user", "content": task}
            }) + '\n'

            session = {
                'proc': proc,
                'status': 'running',
                'task': task,
                'log_lines': list(_seed_log_lines),
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
                'agent_model': p.get('agent_model', '') or CONFIG.get('agent_model', ''),
                # Auto-router attribution — `model` is what actually got
                # passed via --model (after override); `model_source` is
                # 'manual' / 'auto' / 'fallback'. Frontend pill reads these.
                'model': routed_model,
                'model_source': routed_source,
            }
            agent_sessions[session_id] = session
            mgr.session_ids.add(session_id)

            t = threading.Thread(target=_read_agent_stream_b, args=(proc, session), daemon=True)
            t.start()

            # Initial stdin write — deferred to a daemon thread so a stalled
            # claude (rate-limited startup, etc.) can't pin mgr.lock and wedge
            # the whole project. Followups also serialize on stdin_lock, so
            # the initial message always lands first.
            def _write_initial(_proc=proc, _msg=_initial_msg, _sess=session):
                lk = _sess.get('stdin_lock')
                if lk:
                    lk.acquire()
                try:
                    _proc.stdin.write(_msg)
                    _proc.stdin.flush()
                except Exception as _e:
                    _sess['log_lines'].append(f'[stdin write error on dispatch: {_e}]')
                    _sess['status'] = 'error'
                    _sess['last_status_change_time'] = _time.time()
                    _sess['process_alive'] = False
                finally:
                    if lk:
                        lk.release()
            threading.Thread(target=_write_initial, daemon=True).start()
        else:
            # Mode A: spawn-per-turn (existing behavior). base_flags / _sp_args
            # were resolved above the lock (outside RC-2's danger zone).
            if resume_id:
                cmd = [_resolve_claude(), '-r', resume_id, '-p', task, *base_flags]
            else:
                cmd = [_resolve_claude(), '-p', task, *base_flags, *_sp_args]

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
            _register_process(proc, 'Agent (Mode A)', 'agent',
                              session_id, project_id, task[:80])

            session = {
                'proc': proc,
                'status': 'running',
                'task': task,
                'log_lines': list(_seed_log_lines),
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
                'agent_model': p.get('agent_model', '') or CONFIG.get('agent_model', ''),
                'model': routed_model,
                'model_source': routed_source,
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
        _log(f"[dispatch] cmd: {' '.join(cmd)}")
    except (UnicodeEncodeError, UnicodeDecodeError):
        _log(f"[dispatch] cmd: {' '.join(cmd).encode('ascii', 'replace').decode()}")
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
    provider_override = (data.get('provider') or '').strip().lower()
    # Mobile brief replies: augmented version goes to the agent. The frontend's
    # local echo already shows the original task as the user's chat bubble.
    claude_task = _apply_mobile_brief(task, data)
    try:
        session_id = _dispatch_agent_internal(project_id, claude_task, resume_id,
                                              incognito=incognito,
                                              provider_override=provider_override,
                                              display_task=task)
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

        # Pre-persist the user's prompt to log_lines INSIDE this same lock so
        # it survives even if the downstream handler races with a status
        # change and bails out (lost-prompt race: between this decision and
        # agent_interrupt/agent_followup acquiring their own lock, the agent
        # can transition to 'completed', which agent_interrupt rejects with
        # 400 — without this safety net, the prompt is silently dropped).
        # The handlers honor `_send_already_logged` and skip their own
        # append. fresh_or_revive paths log via their own mechanisms and are
        # not affected.
        if session and decision in ('interrupt', 'followup'):
            user_label = CONFIG.get('user_name') or 'User'
            session['log_lines'].append(f"\n> {user_label}: {message}\n")
            session['_send_already_logged'] = True
            # Mark followup as in-flight under the same lock so the SSE generate()
            # loop sees the signal immediately. Without this, an eagerly-opened
            # SSE can close on stale terminal status before agent_followup gets
            # a chance to flip status to 'running'. Cleared by the followup
            # handler (and by _get_active_restart_blockers stuck-flag recovery)
            # once the new turn is actually live.
            if decision == 'followup':
                session['_dispatching_followup'] = True

    # Route to the appropriate handler. Each does its own lock acquisition
    # for the actual mutation; the decision above is just to pick the path.
    # The existing handlers read `request.get_json()` themselves; they get
    # the same body we got. We tag the response so the frontend can log the
    # route taken (useful for debugging; FE doesn't act on it).
    if decision == 'interrupt':
        resp = agent_interrupt(project_id)
        # Race recovery: if the agent transitioned to a state interrupt
        # rejects (e.g., 'completed' between decision and handler entry),
        # fall back to followup so the user's message actually gets
        # processed. The prompt is already pre-persisted, so this is purely
        # about routing the live agent, not about data preservation.
        if resp.status_code == 400:
            try:
                body = resp.get_json(silent=True) or {}
                if isinstance(body, dict) and body.get('error') == 'agent not active':
                    # Re-mark as already-logged so followup doesn't double-log
                    # (the pop in agent_interrupt only ran if it reached the
                    # log_lines line; on early-bail at the status check, the
                    # flag is still set).
                    resp = agent_followup(project_id)
                    decision = 'interrupt_to_followup'
            except Exception:
                pass
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
        # Otherwise dispatch a fresh session. Augmented message goes to claude;
        # the original is preserved for the chat bubble (the frontend echo +
        # any log_lines pre-persist above — currently none on this path since
        # there is no prior session yet).
        try:
            claude_message = _apply_mobile_brief(message, data)
            new_session_id = _dispatch_agent_internal(
                project_id, claude_message, incognito=incognito,
                provider_override=(data.get('provider') or '').strip().lower())
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
        emitted_qids = set()  # per-stream: don't re-emit same question_id
        while True:
            session['_last_sse_poll_time'] = _time.time()
            lines = session['log_lines']
            if sent < len(lines):
                for line in lines[sent:]:
                    yield f"data: {json.dumps({'type': 'output', 'text': line})}\n\n"
                sent = len(lines)

            # Send pending AskUserQuestion data. We keep `pending_questions`
            # populated until the user answers (cleared in /agent/followup) so
            # a client that wasn't connected at emit time (mobile cold reopen,
            # SSE dropped mid-emit, modal not yet built) can still see the
            # question on reconnect. Within one stream we dedupe by question_id
            # so the 0.3s poll doesn't spam the same form. Client also dedupes.
            for pq in (session.get('pending_questions') or []):
                qid = pq.get('question_id', '')
                if qid and qid in emitted_qids:
                    continue
                if qid:
                    emitted_qids.add(qid)
                yield (
                    "data: " +
                    json.dumps({
                        'type': 'question',
                        'question_id': qid,
                        'questions': pq.get('questions', []),
                    }) + "\n\n"
                )

            status = session['status']

            # Emit a `turn_start` event whenever status transitions INTO 'running'.
            # FE relies on this for the running-state UI flip post-Phase-2.
            if status == 'running' and last_emitted_status != 'running':
                yield f"data: {json.dumps({'type': 'turn_start', 'status': 'running'})}\n\n"
            last_emitted_status = status

            if is_mode_b:
                # A session that is idle ONLY because it is blocked on an
                # AskUserQuestion (or plan approval) is NOT "turn complete" —
                # the agent is parked waiting on the user. Suppress turn_complete
                # in that state. The FE turn_complete handler closes the SSE and
                # clears the asking-state, which races the `question` event
                # emitted just above: there is a TOCTOU between the
                # pending_questions read (loop top) and this status read — the
                # reader thread can flip status to 'idle' in between, so an
                # iteration emits turn_complete WITHOUT the question, the FE tears
                # the stream down, and the form is lost until a resync reconnects
                # (status shows "Completed" with no form). Keeping the stream
                # open lets the question reach the client; the form is driven by
                # the `question` event, never by turn_complete.
                waiting_on_user = (session.get('waiting_for_question')
                                   or session.get('waiting_for_plan_approval'))
                if status == 'idle' and not idle_sent and not waiting_on_user:
                    # Turn finished but process is still alive
                    yield f"data: {json.dumps({'type': 'turn_complete', 'status': 'idle', **_session_usage_payload(session)})}\n\n"
                    idle_sent = True
                elif status == 'running':
                    idle_sent = False  # reset for next turn
                elif status not in ('running', 'idle'):
                    if session.get('guardian_state') == 'recovering':
                        pass  # Wait for guardian recovery to complete
                    else:
                        yield f"data: {json.dumps({'type': 'status', 'status': status, **_session_usage_payload(session)})}\n\n"
                        break
            else:
                # Mode A: close stream on terminal states immediately;
                # for non-terminal non-running, wait only if followups pending
                if status == 'stopped':
                    yield f"data: {json.dumps({'type': 'status', 'status': status, **_session_usage_payload(session)})}\n\n"
                    break
                elif status != 'running':
                    if session.get('guardian_state') == 'recovering':
                        pass  # Wait for guardian recovery to complete
                    elif (session.get('waiting_for_question')
                          or session.get('waiting_for_plan_approval')):
                        # Blocked on user input — turn isn't complete. Keep the
                        # stream open so the `question` form is (re)delivered
                        # instead of closing on the idle status (same race the
                        # Mode B branch above guards against).
                        pass
                    elif not session.get('pending_followups') and not session.get('_dispatching_followup'):
                        # Eager-followup grace window: sendFollowup() opens the
                        # SSE BEFORE POSTing /agent/send, so the very first
                        # iterations here can read a stale terminal status (the
                        # prior turn) before write_followup has had a chance to
                        # flip status back to 'running'. Closing in that gap
                        # leaves the FE's status pill stuck on "Completed" for
                        # the ~2s reconnect window — exactly the
                        # COMPLETED-while-running bug Gemini Mode A exhibits.
                        # First ~3s of the stream: hold off; if a followup is
                        # actually incoming, status will flip to 'running' and
                        # turn_start will be emitted normally. If nothing
                        # arrives, fall through and close.
                        if tick < 10:  # 10 * 0.3s ≈ 3s grace
                            pass
                        else:
                            yield f"data: {json.dumps({'type': 'status', 'status': status, **_session_usage_payload(session)})}\n\n"
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
    _model_route_state = None  # set when alive+auto_model_enabled; handled post-lock

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
        # Drop any persisted questions; the user's reply consumes them. (We
        # keep `pending_questions` populated until this point so a late /
        # reconnecting SSE client still sees the form — see the SSE generator.)
        existing.pop('pending_questions', None)

        # ── Multi-provider followup ─────────────────────────────────────────
        # Non-claude providers route through the runtime; their write_followup
        # owns process kill + respawn. We just append the user line and hand off.
        session_provider = (existing.get('provider') or 'claude').lower()
        if session_provider != 'claude':
            user_label = CONFIG.get('user_name') or 'User'
            if not existing.pop('_send_already_logged', False):
                existing['log_lines'].append(f"\n> {user_label}: {message}\n")
            existing['status'] = 'running'
            existing['last_status_change_time'] = _time.time()
            existing['last_output_time'] = _time.time()
            # Clear the in-flight marker set by agent_send: we're no longer
            # "dispatching", we're now "running". Leaving it set would prevent
            # the Mode A SSE from closing on the NEXT terminal status.
            existing.pop('_dispatching_followup', None)
            # Refresh the stashed system context so MEMORY / AGENT_RULES edits
            # made mid-conversation ride along on the next per-turn respawn.
            # The runtime re-injects existing['_system_prompt'] every followup
            # (see _compose_respawn_prompt in agent_runtime.py).
            if not existing.get('incognito'):
                try:
                    existing['_system_prompt'] = _build_agent_context(
                        p, incognito=False, task=message)
                except Exception as e:
                    _log(f"[followup] context refresh failed: {e}")
            try:
                runtime = _agent_runtime.get_runtime(session_provider)
                handle = _agent_runtime.SessionHandle(
                    mc_session_id=session_id,
                    provider=session_provider,
                    mode=existing.get('mode', 'A'),
                    project_path=pp,
                    project_id=project_id,
                    session_dict=existing,
                )
                runtime.write_followup(handle, message)
            except Exception as e:
                existing['log_lines'].append(f"[{session_provider} followup error: {e}]")
                existing['status'] = 'error'
                existing['last_status_change_time'] = _time.time()
            _log_agent_activity(project_id, f"Agent follow-up (provider={session_provider}): {message[:100]}")
            return jsonify({'ok': True, 'session_id': session_id})

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
                    _log(f"[followup] {project_id}: no claude_session_id, starting fresh")
                    context = _build_agent_context(p)
                    message = (f"[Previous conversation had no session ID to resume. "
                               f"Starting fresh.]\n\n{message}")
                elif not claude_sid and was_resume:
                    # Was a resume but CLI never emitted a session_id — start fresh
                    _log(f"[followup] {project_id}: resume never emitted session_id, starting fresh")
                    context = _build_agent_context(p)
                    message = (f"[Resumed session did not provide a continuable session ID. "
                               f"Starting fresh.]\n\n{message}")
                elif _resume_is_fragile(was_resume, existing.get('_resume_confirmed')):
                    # The resume itself proved fragile — it died BEFORE producing any
                    # output, so -r-ing it again would just loop. Start fresh.
                    # (A resume that already produced output is healthy and falls
                    # through to the -r path below, so an AskUserQuestion kill /
                    # idle-eviction / later crash keeps the full transcript.)
                    _log(f"[followup] {project_id}: resume {claude_sid[:12]} died before any output, starting fresh")
                    context = _build_agent_context(p)
                    existing['log_lines'].append(
                        f'[Resume produced no output before exiting — restarting fresh]')
                    message = (f"[Continuing from a previous conversation (session {claude_sid}) whose "
                               f"process exited. Start fresh but continue the user's request.]\n\n{message}")
                else:
                    # Normal session, OR a resume that already produced output
                    # (healthy — it just died later). Resume with -r to keep context.
                    too_large, size_bytes = _session_too_large(pp, claude_sid)
                    if too_large:
                        size_mb = size_bytes / (1024 * 1024)
                        _log(f"[followup] Session {claude_sid} is {size_mb:.1f} MB — starting fresh")
                        _log_agent_activity(project_id,
                                            f"Auto-fresh: session too large ({size_mb:.0f} MB)")
                        existing['log_lines'].append(
                            f'[Session transcript too large ({size_mb:.0f} MB) — starting fresh]')
                        context = _build_agent_context(p)
                        message = (f"[Continuing from a previous conversation that grew too large "
                                   f"to resume ({size_mb:.0f} MB). Start fresh.]\n\n{message}")
                    else:
                        resume_flags = ['-r', claude_sid]
                        _log(f"[followup] {project_id}: respawning Mode B with -r {claude_sid[:12]}")

                user_label = CONFIG.get('user_name') or 'User'
                if not existing.pop('_send_already_logged', False):
                    existing['log_lines'].append(f"\n> {user_label}: {message}\n")
                existing['status'] = 'running'
                existing['last_status_change_time'] = _time.time()
                existing['last_output_time'] = _time.time()
                existing.pop('evicted', None)  # respawned from idle-eviction → clear the State-1 skip flag
                existing.pop('_needs_respawn', None)  # this respawn already rebuilds flags+context
                # Clear the in-flight marker set by agent_send. Otherwise the
                # flag leaks past status='running' and, once this turn
                # completes (status flips back to idle/completed), Guardian
                # State 5 mops it up 30s later with a misleading
                # "[Guardian: cleared stuck dispatching flag]" line on every
                # message. Mode A clears it at the parallel spot below.
                existing.pop('_dispatching_followup', None)
                old_proc = existing.get('proc')
                if old_proc:
                    _unregister_process(old_proc.pid)
                    try:
                        old_proc.stdin.close()
                    except Exception:
                        pass
                # Build command while under lock, spawn outside to avoid blocking
                cmd = [_resolve_claude(), *resume_flags,
                       *_build_claude_flags(p, streaming=True)]
                _sp_path = None
                if not resume_flags and context:
                    _sp_args, _sp_path = _sysprompt_file_args(context)
                    cmd.extend(_sp_args)
                _respawn_b = {
                    'cmd': cmd, 'pp': pp, 'message': message,
                    'existing': existing, 'session_id': session_id,
                    'project_id': project_id,
                    'old_proc': old_proc,
                    'sysprompt_path': _sp_path,
                    # Carry the request data so the closure can decide whether
                    # to apply the mobile-brief directive at the stdin write.
                    'request_data': data,
                }
                # Fall through — spawn happens after lock release
            else:
                # Process alive — optionally re-classify model before writing to stdin
                user_label = CONFIG.get('user_name') or 'User'
                if not existing.pop('_send_already_logged', False):
                    existing['log_lines'].append(f"\n> {user_label}: {message}\n")
                existing['status'] = 'running'
                existing['last_status_change_time'] = _time.time()
                existing['last_output_time'] = _time.time()
                # Clear the in-flight marker set by agent_send — see note on
                # the parallel respawn branch above for why this matters.
                existing.pop('_dispatching_followup', None)

                # Sticky-settings respawn: a spawn-baked (Tier-1) setting was
                # flipped mid-session (see update_config). A live CLI can't see
                # CLI/system-prompt changes, so resume it into a fresh process
                # that rebuilds flags + context from current CONFIG. Mirrors the
                # auto-router's alive-process respawn just below.
                if (CONFIG.get('sticky_agent_settings', False)
                        and existing.pop('_needs_respawn', None)):
                    _sticky_sid = existing.get('claude_session_id')
                    _sticky_resume = ['-r', _sticky_sid] if _sticky_sid else []
                    _sticky_cmd = [_resolve_claude(), *_sticky_resume,
                                   *_build_claude_flags(p, streaming=True)]
                    _sticky_sp = None
                    if not _sticky_resume:
                        _sargs, _sticky_sp = _sysprompt_file_args(_build_agent_context(p))
                        _sticky_cmd.extend(_sargs)
                    existing['process_alive'] = False
                    existing['log_lines'].append('[Settings changed — applying via resume]')
                    _sticky_old = existing.get('proc')
                    if _sticky_old:
                        _unregister_process(_sticky_old.pid)
                        try:
                            _sticky_old.stdin.close()
                        except Exception:
                            pass
                    _respawn_b = {
                        'cmd': _sticky_cmd, 'pp': pp, 'message': message,
                        'existing': existing, 'session_id': session_id,
                        'project_id': project_id,
                        'old_proc': _sticky_old,
                        'sysprompt_path': _sticky_sp,
                        'request_data': data,
                    }
                    # Fall through — spawn happens after lock release.
                elif CONFIG.get('auto_model_enabled', False) and message:
                    # Defer stdin write — classify model post-lock to avoid
                    # blocking mgr.lock for the ~0.5s Haiku classifier call.
                    _model_route_state = {
                        'current_model': existing.get('model') or 'sonnet',
                        'claude_sid': existing.get('claude_session_id'),
                        'proc': existing['proc'],
                        'existing': existing,
                    }
                    # Fall through to post-lock routing section
                else:
                    # Router off — write stdin directly (original path)
                    claude_content = _apply_mobile_brief(message, data)
                    stdin_msg = json.dumps({
                        "type": "user",
                        "message": {"role": "user", "content": claude_content}
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
            # Clear the in-flight marker set by agent_send (or left by an auto-
            # dispatched queued followup): we're no longer "dispatching", we're
            # now "running". Leaving it set would prevent the Mode A SSE from
            # closing on the NEXT terminal status.
            existing.pop('_dispatching_followup', None)
            user_label = CONFIG.get('user_name') or 'User'
            if not existing.pop('_send_already_logged', False):
                existing['log_lines'].append(f"\n> {user_label}: {message}\n")
            claude_sid = existing.get('claude_session_id')

    # Mode B per-turn model routing (process alive, auto_model_enabled=True)
    # Classifier runs here — outside mgr.lock — to avoid blocking other requests.
    if _model_route_state:
        mrs = _model_route_state
        new_model, new_source = _resolve_dispatch_model(p, message)
        current_model = mrs['current_model']
        _router_stat(project_id,
                     requested_model=current_model,
                     chosen_model=new_model,
                     source=new_source)
        if _router_model_tier(new_model) != _router_model_tier(current_model):
            # Model tier changed — kill current process, respawn with -r + new model
            _log(f"[followup-B-route] {project_id}: model switch {current_model} → {new_model}")
            claude_sid = mrs['claude_sid']
            resume_flags = ['-r', claude_sid] if claude_sid else []
            _sp_path = None
            cmd = [_resolve_claude(), *resume_flags,
                   *_build_claude_flags(p, streaming=True, model_override=new_model)]
            if not resume_flags:
                _route_context = _build_agent_context(p)
                _sp_args, _sp_path = _sysprompt_file_args(_route_context)
                cmd.extend(_sp_args)
            with get_manager(project_id).lock:
                _route_existing = agent_sessions.get(session_id)
                if _route_existing:
                    _route_existing['model'] = new_model
                    _route_existing['model_source'] = new_source
                    _route_existing['process_alive'] = False
                    _route_existing['log_lines'].append(
                        f'[Auto-router: switching {current_model} → {new_model}]')
            _respawn_b = {
                'cmd': cmd, 'pp': pp, 'message': message,
                'existing': mrs['existing'],
                'session_id': session_id, 'project_id': project_id,
                'old_proc': mrs['proc'],
                'sysprompt_path': _sp_path,
                'request_data': data,
            }
        else:
            # Same tier — write stdin directly
            _rs_existing = mrs['existing']
            claude_content = _apply_mobile_brief(message, data)
            stdin_msg = json.dumps({
                "type": "user",
                "message": {"role": "user", "content": claude_content}
            }) + '\n'

            def _write_stdin_routed():
                lock = _rs_existing.get('stdin_lock')
                if lock:
                    lock.acquire()
                try:
                    _rs_existing['proc'].stdin.write(stdin_msg)
                    _rs_existing['proc'].stdin.flush()
                except Exception as e:
                    _rs_existing['log_lines'].append(f'[stdin write error: {e}]')
                    _rs_existing['status'] = 'error'
                    _rs_existing['last_status_change_time'] = _time.time()
                    _rs_existing['process_alive'] = False
                finally:
                    if lock:
                        lock.release()

            threading.Thread(target=_write_stdin_routed, daemon=True).start()
            _log_agent_activity(project_id, f"Agent follow-up: {message[:100]}")
            return jsonify({'ok': True, 'session_id': session_id})

    # Mode B respawn — spawn outside the lock to avoid blocking stop/other ops
    if _respawn_b:
        rb = _respawn_b
        # Kill the old process if still alive (outside lock)
        if rb.get('old_proc'):
            _kill_proc_background(rb['old_proc'])
        def _do_respawn_b():
            try:
                _log(f"[respawn-B] {rb['project_id']}: spawning cmd={' '.join(rb['cmd'][:5])}...")
                proc = subprocess.Popen(
                    rb['cmd'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, cwd=rb['pp'],
                    text=True, encoding='utf-8', errors='replace',
                    creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
                )
                _sysprompt_cleanup(rb.get('sysprompt_path'), proc)
                _log(f"[respawn-B] {rb['project_id']}: spawned PID {proc.pid}")
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

                # Send message to stdin. Mobile-brief directive applied here so
                # rb['message'] keeps the original for telemetry / log_lines.
                claude_content = _apply_mobile_brief(rb['message'], rb.get('request_data') or {})
                stdin_msg = json.dumps({
                    "type": "user",
                    "message": {"role": "user", "content": claude_content}
                }) + '\n'
                lock = rb['existing']['stdin_lock']
                with lock:
                    proc.stdin.write(stdin_msg)
                    proc.stdin.flush()
            except Exception as e:
                _log(f"[respawn-B] {rb['project_id']}: FAILED — {e}")
                rb['existing']['log_lines'].append(f'[respawn error: {e}]')
                rb['existing']['status'] = 'error'
                rb['existing']['last_status_change_time'] = _time.time()
                rb['existing']['process_alive'] = False
                # Pop the temp sysprompt file: spawn never reached the
                # cleanup wiring, so the watchdog thread doesn't exist.
                _sp_orphan = rb.get('sysprompt_path')
                if _sp_orphan:
                    try:
                        os.unlink(_sp_orphan)
                    except OSError:
                        pass

        threading.Thread(target=_do_respawn_b, daemon=True).start()
        _log_agent_activity(project_id, f"Agent resumed: {message[:100]}")
        return jsonify({'ok': True, 'session_id': session_id, 'resumed': True})

    # Mode A: Spawn process outside the lock to avoid blocking other requests
    def _start_followup():
        _sp_path = None  # bound here so the except below can sweep on early failure
        try:
            followup_msg = message
            if claude_sid:
                too_large, size_bytes = _session_too_large(pp, claude_sid)
                if too_large:
                    size_mb = size_bytes / (1024 * 1024)
                    _log(f"[followup-A] Session {claude_sid} is {size_mb:.1f} MB — starting fresh")
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
            # Mobile-brief applied here so log_lines + telemetry use the
            # unaugmented message; only the claude -p arg gets the directive.
            claude_followup_msg = _apply_mobile_brief(followup_msg, data)
            cmd = [_resolve_claude(), *resume_flags, '-p', claude_followup_msg, *_build_claude_flags(p)]
            if not resume_flags:
                _sp_args, _sp_path = _sysprompt_file_args(context)
                cmd.extend(_sp_args)
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
            # Popen may have raised before sysprompt cleanup was wired — sweep.
            if _sp_path:
                try:
                    os.unlink(_sp_path)
                except OSError:
                    pass

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

        # ── Multi-provider interrupt ──────────────────────────────────────
        # Non-claude providers: kill via runtime.interrupt(), then re-dispatch
        # the new message via runtime.write_followup() — the runtime owns
        # both halves of the respawn dance.
        session_provider = (session.get('provider') or 'claude').lower()
        if session_provider != 'claude':
            user_label = CONFIG.get('user_name') or 'User'
            if not session.pop('_send_already_logged', False):
                session['log_lines'].append('[Got your message]')
                session['log_lines'].append(f"\n> {user_label}: {message}\n")
            session.pop('pending_followups', None)
            # Refresh stashed context (see the followup path for rationale).
            if not session.get('incognito'):
                try:
                    session['_system_prompt'] = _build_agent_context(
                        p, incognito=False, task=message)
                except Exception as e:
                    _log(f"[interrupt] context refresh failed: {e}")
            try:
                runtime = _agent_runtime.get_runtime(session_provider)
                handle = _agent_runtime.SessionHandle(
                    mc_session_id=session_id,
                    provider=session_provider,
                    mode=session.get('mode', 'A'),
                    project_path=pp,
                    project_id=project_id,
                    session_dict=session,
                )
                runtime.write_followup(handle, message)
            except Exception as e:
                session['log_lines'].append(f"[{session_provider} interrupt error: {e}]")
                session['status'] = 'error'
                session['last_status_change_time'] = _time.time()
            _log_agent_activity(project_id, f"Agent interrupt (provider={session_provider}): {message[:80]}")
            return jsonify({'ok': True, 'session_id': session_id})

        old_proc = session['proc']
        claude_sid = session.get('claude_session_id')

        # Mark as interrupting BEFORE killing the old proc. The old reader's
        # finally block will see this flag (via _session_owned_by) and skip
        # all status / process_alive writes, eliminating the stale-status
        # flash that flipped the UI to "stopped" between kill and respawn.
        # Cleared by the respawn thread once the new proc replaces session['proc'].
        session['_interrupting'] = True

        # Stop the current process
        # Shown in the chat as a system-style line when the user interrupts a
        # running turn with a new message. Friendlier than "Agent interrupted
        # by user" — the user already knows they interrupted; this is the
        # acknowledgement bubble.
        session['log_lines'].append('[Got your message]')
        session.pop('pending_followups', None)
        session.pop('_dispatching_followup', None)
        session['waiting_for_plan_approval'] = False
        session['waiting_for_question'] = False
        session.pop('pending_questions', None)
        if session.get('mode') == 'B':
            try:
                old_proc.stdin.close()
            except Exception:
                pass
        _unregister_process(old_proc.pid)

        # Immediately set status to running for the new prompt
        user_label = CONFIG.get('user_name') or 'User'
        if not session.pop('_send_already_logged', False):
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
        _sp_path = None  # bound here so the except below can sweep on early failure
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
                cmd = [_resolve_claude(), *resume_flags,
                       *_build_claude_flags(p, streaming=True)]
                if not resume_flags and context:
                    _sp_args, _sp_path = _sysprompt_file_args(context)
                    cmd.extend(_sp_args)
                proc = subprocess.Popen(
                    cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, cwd=pp,
                    text=True, encoding='utf-8', errors='replace',
                    creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
                )
                _sysprompt_cleanup(_sp_path, proc)
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
                # Mobile-brief applied only to the claude-bound payload —
                # log_lines + telemetry above keep the unaugmented message.
                claude_content = _apply_mobile_brief(respawn_msg, data)
                stdin_msg = json.dumps({
                    "type": "user",
                    "message": {"role": "user", "content": claude_content}
                }) + '\n'
                with session['stdin_lock']:
                    proc.stdin.write(stdin_msg)
                    proc.stdin.flush()
            else:
                # Mode A
                claude_respawn_msg = _apply_mobile_brief(respawn_msg, data)
                if resume_flags:
                    cmd = [_resolve_claude(), *resume_flags, '-p', claude_respawn_msg,
                           *_build_claude_flags(p)]
                else:
                    if not context:
                        context = _build_agent_context(p)
                    _sp_args, _sp_path = _sysprompt_file_args(context)
                    cmd = [_resolve_claude(), '-p', claude_respawn_msg, *_build_claude_flags(p),
                           *_sp_args]

                proc = subprocess.Popen(
                    cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, cwd=pp,
                    text=True, encoding='utf-8', errors='replace',
                    creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
                )
                _sysprompt_cleanup(_sp_path, proc)
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
            # Popen may have raised before sysprompt cleanup was wired — sweep.
            if _sp_path:
                try:
                    os.unlink(_sp_path)
                except OSError:
                    pass

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
    # Hoist project lookup + per-project default model out of the loop. Used
    # as a fallback for legacy sessions that were dispatched before
    # session['agent_model'] was captured per-dispatch.
    _proj_default_model = ((load_project(project_id) or {}).get('agent_model')
                           or CONFIG.get('agent_model') or '')
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
                'long_session_advisory': _long_session_advisory(s),
                'process_alive': s.get('process_alive', False) if s.get('mode') == 'B' else (s['status'] in ('running',)),
                'hivemind_id': s.get('hivemind_id', ''),
                'hivemind_ws_id': s.get('hivemind_ws_id', ''),
                'hivemind_role': s.get('hivemind_role', ''),
                'trigger_type': s.get('trigger_type', 'manual'),
                'trigger_id': s.get('trigger_id', ''),
                'waiting_for_plan_approval': s.get('waiting_for_plan_approval', False),
                'waiting_for_question': s.get('waiting_for_question', False),
                # Mirror pending_questions so the FE can re-render the form on
                # reconcile even when SSE silently buffered the question event
                # (mobile WebView / CF Tunnel). Dedupe is by question_id on
                # the client, so re-delivery is safe.
                'pending_questions': s.get('pending_questions', []) if s.get('waiting_for_question') else [],
                'guardian_state': s.get('guardian_state'),
                'circuit_breaker_tripped': s.get('circuit_breaker_tripped', False),
                'incognito': s.get('incognito', False),
                'provider': s.get('provider') or 'claude',
                # Per-session snapshot, with project-level fallback so older
                # sessions (dispatched before the field was captured) still
                # surface a usable model string. Empty = provider default.
                'agent_model': s.get('agent_model') or _proj_default_model,
                # Auto-router attribution. `model` is what actually got
                # passed via --model (may differ from agent_model when the
                # router picked something else). `model_source` is one of
                # 'manual' / 'auto' / 'fallback'. Frontend pill reads
                # both to decide whether to render an auto-router badge.
                'model': s.get('model') or s.get('agent_model') or _proj_default_model,
                'model_source': s.get('model_source', 'manual'),
            })
    # Sort: running first, then newest first (ISO timestamps sort lexically)
    sessions.sort(key=lambda s: (
        0 if s['status'] == 'running' else 1,
        '~' if not s.get('started_at') else s['started_at']
    ), reverse=False)
    # Within each group, newest first
    sessions.sort(key=lambda s: s.get('started_at', ''), reverse=True)
    sessions.sort(key=lambda s: 0 if s['status'] in ('running', 'idle') else 1)
    # P2-1: surface memory-condensation state (default {'state':'idle'} so
    # the field is always present for the frontend).
    return jsonify({'sessions': sessions,
                    'condense': _get_condense_status(project_id)})


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


# ── Terminal session management ── moved to mc/blueprints/terminal_routes.py (1.8).


# ── Process Tracker endpoints ── moved to mc/blueprints/system_routes.py (1.6).

# ── Hivemind ── moved to mc/blueprints/hivemind_routes.py (1.10).


# ── Agent log endpoint ────────────────────────────────────────────────────────

def _looks_like_claydo_entry(entry):
    """Heuristic: does this agent_log entry look like a Claydo conversation
    that ended up in a project's log via transcript-backfill pollution?

    We catch the unmistakable signature: tasks that start with
    "Previous exchange in this conversation:" — that's the prefix WE
    generate when sending Claydo's history context. No real MC user task
    would ever start that way.

    First-turn Claydo entries (no history prefix) are indistinguishable
    from real questions, so we leave them. The cwd fix in `_claydo_cwd`
    prevents any new Claydo transcripts from leaking into project
    agent_logs going forward; this filter just suppresses the leftover
    follow-up entries that landed before the cwd fix.
    """
    task = (entry.get('task') or '').lstrip()
    return task.startswith('Previous exchange in this conversation:')


@app.route('/api/project/<project_id>/agent/log')
def get_agent_log(project_id):
    log = _load_agent_log(project_id)
    log = [e for e in log if not _looks_like_claydo_entry(e)]
    for entry in log:
        entry['ts_relative'] = time_ago(entry.get('ts'))
        entry['started_relative'] = time_ago(entry.get('started_at'))
    return jsonify(log)


def _enrich_run_entries(entries):
    """Add ts_relative + started_relative for FE display. Also fill in
    `claude_session_id` for in-progress rows by looking up the live
    `agent_sessions[session_id].claude_session_id` — these rows are written
    at dispatch time before claude assigns a csid, and Mode B idle sessions
    that never finalize would otherwise have an empty csid forever, leaving
    the FE with no way to open the transcript even though it exists on disk.
    """
    for e in entries:
        e['ts_relative'] = time_ago(e.get('ts'))
        e['started_relative'] = time_ago(e.get('started_at'))
        if not e.get('claude_session_id'):
            sid = e.get('session_id')
            if sid:
                live = agent_sessions.get(sid)
                if live:
                    live_csid = live.get('claude_session_id', '')
                    if live_csid:
                        e['claude_session_id'] = live_csid
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


@app.route('/api/project/<project_id>/session/<session_id>/reconstruct')
def reconstruct_dead_session(project_id, session_id):
    """Rebuild a finalized/purged MC session's chat buffer from its transcript.

    Read-only path for a tapped push whose session is dead (server bounced,
    not yet revived because the user hasn't sent a follow-up). The status
    endpoint only knows live in-memory sessions, so without this the deep-link
    lands on an empty tab and the user sees nothing — the very symptom this
    closes. Maps MC session_id → agent_log entry → claude_session_id → the
    on-disk .jsonl, rendered with the same _transcript_buffer_lines() the
    revive path uses so the read-only view is byte-identical to the live one.

    Returns 404 when the session isn't a known dead session with a resolvable
    transcript (caller then falls back to its existing behaviour).
    """
    p = load_project(project_id)
    if not p:
        return jsonify({'error': 'project not found'}), 404
    # A live session should go through /agent/status, not here.
    if session_id in agent_sessions:
        return jsonify({'error': 'session is live'}), 409
    entry = next((e for e in _load_agent_log(project_id)
                  if e.get('session_id') == session_id), None)
    if not entry:
        return jsonify({'error': 'session not in agent log'}), 404
    claude_sid = entry.get('claude_session_id')
    if not claude_sid:
        return jsonify({'error': 'no claude_session_id to resume from'}), 404
    user_label = CONFIG.get('user_name') or 'User'
    lines = _transcript_buffer_lines(p.get('project_path', ''), claude_sid,
                                     user_label, max_messages=300)
    if not lines:
        return jsonify({'error': 'transcript not found or empty'}), 404
    lines.append('[— read-only history; send a message to resume this session —]')
    return jsonify({
        'session_id': session_id,
        'claude_session_id': claude_sid,
        'task': entry.get('task', ''),
        'started_at': entry.get('timestamp', '') or entry.get('started_at', ''),
        'log_lines': lines,
        'read_only': True,
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


# ── Hivemind: persistent multi-agent collaboration ── extracted to
# mc/blueprints/hivemind_routes.py (1.10): all 28 /api/hivemind* routes (27
# from the main region above + /api/hivemind/<id>/runs from this run-history
# section — moved with its family), the _hm_* data layer, worker context
# builder + spawn, orchestrator CLI dispatch, message bus/SSE, knowledge
# base, and the background orchestrator loop (now heartbeating as
# 'hivemind-orchestrator' in /api/system/loops — Phase 2). wire() late-binds
# load_project (projects family, 1.11), the dispatch-family helpers
# (get_manager, _register_process, _read_agent_stream, _resolve_claude,
# _sysprompt_file_args/_cleanup, _hide_windows_delayed — 1.12), the
# agent-log/run-history readers (_load_agent_log, _enrich_run_entries), the
# _clayrune_* context feeders (shared with _build_agent_context — 1.12),
# PORT, the Popen platform consts, and the _DATA_ROOT-derived hivemind dir
# (its module-level .mkdir moved into wire()). The stanza sits HERE, not at
# the main region tombstone, because _enrich_run_entries is defined just
# above — wiring earlier would NameError at import (the 1.2 lesson).
from mc.blueprints import hivemind_routes as _bp_hivemind  # noqa: E402

_bp_hivemind.wire(
    hivemind_dir=_DATA_ROOT / 'data' / 'hiveminds',
    port=PORT,
    load_project_fn=load_project,
    get_manager_fn=get_manager,
    register_process_fn=_register_process,
    read_agent_stream_fn=_read_agent_stream,
    resolve_claude_fn=_resolve_claude,
    sysprompt_file_args_fn=_sysprompt_file_args,
    sysprompt_cleanup_fn=_sysprompt_cleanup,
    hide_windows_delayed_fn=_hide_windows_delayed,
    log_agent_activity_fn=_log_agent_activity,
    load_agent_log_fn=_load_agent_log,
    enrich_run_entries_fn=_enrich_run_entries,
    clayrune_universal_capabilities_fn=_clayrune_universal_capabilities,
    clayrune_api_reference_fn=_clayrune_api_reference,
    popen_flags=_POPEN_FLAGS,
    startupinfo=_STARTUPINFO,
)
app.register_blueprint(_bp_hivemind.bp)

# Inbound shims: the two startup call sites under __main__ keep their bare
# names. atexit.register(_hivemind_orchestrator_stop.set) stays verbatim
# further down (LIFO exit-hook ordering — the 1.8 lesson; the Event lives
# in mc/state.py since Phase 0).
_start_hivemind_orchestrator = _bp_hivemind._start_hivemind_orchestrator
_hm_reconcile_stale_on_startup = _bp_hivemind._hm_reconcile_stale_on_startup


@app.route('/api/recent-runs')
def api_recent_runs():
    """Aggregate agent_log entries across all projects within a time window.

    Powers the "Recent" tab in the schedule banner dropdown — answers
    "what just ran across all my projects in the last N hours?"

    Query params:
      hours  window size in hours (default 2, max 168 = 1 week)
      limit  max rows to return (default 50, max 200)
    """
    try:
        hours = float(request.args.get('hours', 2))
    except Exception:
        hours = 2
    if hours <= 0:
        hours = 2
    if hours > 168:
        hours = 168
    try:
        limit = int(request.args.get('limit', 50))
    except Exception:
        limit = 50
    if limit < 1:
        limit = 50
    if limit > 200:
        limit = 200

    cutoff_dt = datetime.now(timezone.utc) - timedelta(hours=hours)

    # Build project_id -> display name lookup once.
    proj_names = {}
    for p in load_projects():
        pid = p.get('id') or ''
        if pid:
            proj_names[pid] = p.get('name') or pid

    runs = []
    suffix = '_agent_log.json'
    for f in DATA_DIR.glob('*' + suffix):
        pid = f.name[:-len(suffix)]
        try:
            log = json.loads(f.read_text(encoding='utf-8'))
        except Exception:
            continue
        for entry in log:
            ts = entry.get('ts') or entry.get('started_at') or ''
            if not ts:
                continue
            try:
                entry_dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
            except Exception:
                continue
            if entry_dt < cutoff_dt:
                continue
            e = dict(entry)
            e['project_id'] = pid
            e['project_name'] = proj_names.get(pid, pid)
            runs.append(e)

    runs.sort(key=lambda e: e.get('ts', ''), reverse=True)
    runs = runs[:limit]
    return jsonify({
        'runs': _enrich_run_entries(runs),
        'hours': hours,
        'count': len(runs),
    })


def _extract_msg_text_from_raw(raw):
    """Pull the human-readable text from one transcript JSONL line.

    Returns the user/assistant text content ('' for tool/meta/attachment lines).
    Mirrors the extraction in ClaudeRuntime.parse_transcript_file but works on a
    single raw line so the search scanner can parse only the lines it needs.
    """
    try:
        o = json.loads(raw)
    except Exception:
        return ''
    msg = o.get('message')
    if not isinstance(msg, dict):
        return ''
    content = msg.get('content', '')
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        texts = [str(b.get('text', '')) for b in content
                 if isinstance(b, dict) and b.get('type') == 'text']
        return ' '.join(t.strip() for t in texts if t).strip()
    return ''


def _make_search_snippet(display_text, raw, query, window=90):
    """Build a context window around `query`. Prefer clean display text; fall
    back to a whitespace-collapsed window of the raw line when the hit was in
    text we don't normally surface (tool results / thinking)."""
    ql = query.lower()
    src = display_text if (display_text and ql in display_text.lower()) else None
    if src is None:
        # Fall back to the raw line, lightly de-noised.
        src = ' '.join(raw.split())
    low = src.lower()
    i = low.find(ql)
    if i < 0:
        return (display_text or src)[:window * 2].strip()
    start = max(0, i - window)
    end = min(len(src), i + len(query) + window)
    s = src[start:end].strip()
    if start > 0:
        s = '… ' + s
    if end < len(src):
        s = s + ' …'
    return s


def _search_project_transcripts(project, query, limit=50):
    """Full-text scan of a project's Claude .jsonl transcripts.

    Returns [{csid, label, snippet, matches, mtime, ts_relative}] sorted by
    recency. A fast raw-substring pass picks matching lines; only the first user
    line (for the label) and the first matching line (for the snippet) are
    JSON-parsed, so the whole project scans in ~scan-cost (benchmarked ~2s on a
    195 MB / 181-file project, sub-second elsewhere). Read-only, no locks.
    """
    pp = (project or {}).get('project_path', '')
    q = (query or '').strip()
    if not pp or len(q) < 2:
        return []
    ql = q.lower()
    encoded = _encode_project_path(pp)
    if not encoded:
        return []
    dirs = [CLAUDE_HOME / encoded]
    alt = encoded.replace('_', '-')
    if alt != encoded:
        dirs.append(CLAUDE_HOME / alt)

    seen = set()
    files = []
    for d in dirs:
        try:
            if not d.exists():
                continue
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

    from datetime import datetime, timezone
    results = []
    for f, mtime in files:
        match_count = 0
        snippet = ''
        first_user = ''
        try:
            with open(f, 'r', encoding='utf-8', errors='ignore') as fh:
                for raw in fh:
                    if not first_user and '"type":"user"' in raw:
                        first_user = _extract_msg_text_from_raw(raw)[:200]
                    if ql in raw.lower():
                        match_count += 1
                        if not snippet:
                            snippet = _make_search_snippet(
                                _extract_msg_text_from_raw(raw), raw, q)
        except Exception:
            continue
        if not match_count:
            continue
        try:
            ts_iso = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
        except Exception:
            ts_iso = ''
        results.append({
            'csid': f.stem,
            'label': ' '.join((first_user or '').split()) or '(no label)',
            'snippet': snippet,
            'matches': match_count,
            'mtime': mtime,
            'ts_relative': time_ago(ts_iso) if ts_iso else '',
        })
    results.sort(key=lambda r: r['mtime'], reverse=True)
    return results[:limit]


@app.route('/api/project/<project_id>/search-chats')
def search_project_chats(project_id):
    """Search a project's prior conversations by transcript content.

    Query: ?q=<text>&limit=<N>. Returns {query, count, results:[...]}.
    """
    q = (request.args.get('q', '') or '').strip()
    try:
        limit = int(request.args.get('limit', 50))
    except Exception:
        limit = 50
    limit = max(1, min(limit, 100))
    p = load_project(project_id)
    if not p:
        return jsonify({'error': 'project not found'}), 404
    if len(q) < 2:
        return jsonify({'query': q, 'count': 0, 'results': []})
    results = _search_project_transcripts(p, q, limit=limit)
    return jsonify({'query': q, 'count': len(results), 'results': results})


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


# ── PLAN tab load cache ───────────────────────────────────────────────────────
# /api/project/<id>/plans re-read every plan file (read + regex for the title)
# and re-parsed the whole agent log on every tab open — both are I/O that grows
# with history, which is the slow tab load. Cache both by mtime. Payload assembly
# is cheap in-memory CPU, so it's rebuilt each call → titles are always fresh (no
# whole-payload staleness) while the I/O is skipped when nothing changed.
# Best-effort: a cache error falls back to the direct log read, and
# _plan_title_for is self-guarding. CPython dict ops are atomic under the GIL, so
# these module-level caches need no lock for the threaded server.
_plan_title_cache: dict = {}      # plan_file path -> (mtime, title)
_plans_log_cache: dict = {}       # project_id  -> (log_mtime, parsed_log)


def _plan_title_for(p: Path) -> str:
    """First '# ' heading of a plan file, memoized by (path, mtime)."""
    import re
    key = str(p)
    try:
        mtime = p.stat().st_mtime
    except Exception:
        return p.stem
    cached = _plan_title_cache.get(key)
    if cached and cached[0] == mtime:
        return cached[1]
    try:
        content = p.read_text(encoding='utf-8')
        m = re.match(r'^#\s+(.+)', content, re.MULTILINE)
        title = m.group(1).strip() if m else p.stem
    except Exception:
        title = p.stem
    _plan_title_cache[key] = (mtime, title)
    return title


def _plans_cached_log(project_id):
    """_load_agent_log, but skip the JSON parse when the log file's mtime is unchanged."""
    filepath = DATA_DIR / f'{project_id}_agent_log.json'
    try:
        mtime = filepath.stat().st_mtime
    except Exception:
        _plans_log_cache.pop(project_id, None)
        return []
    cached = _plans_log_cache.get(project_id)
    if cached and cached[0] == mtime:
        return cached[1]
    log = _load_agent_log(project_id)
    _plans_log_cache[project_id] = (mtime, log)
    return log


@app.route('/api/project/<project_id>/plans')
def get_project_plans(project_id):
    """Return all plan files for this project (live sessions + agent log).

    Titles and the agent-log parse are cached by mtime (_plan_title_for /
    _plans_cached_log) so repeated tab opens don't re-read unchanged files.
    """
    def _build(log):
        plans = []
        seen = set()

        def _add(pf, task, ts, session_id):
            if not pf or pf in seen:
                return
            p = Path(pf)
            if not p.is_file():
                return
            seen.add(pf)
            plans.append({
                'plan_file': pf,
                'filename': p.name,
                'title': _plan_title_for(p),
                'task': task or '',
                'ts': ts or '',
                'ts_relative': time_ago(ts),
                'session_id': session_id or '',
            })

        # Live sessions first (may not be in the log yet)
        for sid, s in agent_sessions.items():
            if s.get('project_id') != project_id:
                continue
            _add(s.get('plan_file', ''), s.get('task', ''),
                 s.get('started_at', ''), s.get('session_id', ''))
        for entry in log:
            _add(entry.get('plan_file', ''), entry.get('task', ''),
                 entry.get('ts', ''), entry.get('session_id', ''))
        return plans

    try:
        log = _plans_cached_log(project_id)
    except Exception:
        log = _load_agent_log(project_id)
    return jsonify(_build(log))


# ── Usage / token tracking ──────────────────────────────────────────────────

@app.route('/api/usage')
def api_usage():
    """Aggregate token usage across all agent log files and running sessions.

    Optional query param ?since=<ISO timestamp> to filter entries after a cutoff.

    Response shape (multi-provider aware):
      {
        by_provider: {
          claude: {input_tokens, output_tokens, total_tokens, cost_usd, num_turns, sessions},
          gemini: {...},
          ...
        },
        total: {input_tokens, output_tokens, total_tokens, cost_usd, sessions},
        # Legacy flat fields kept for backward compat:
        input_tokens, output_tokens, total_tokens, cost_usd, total_sessions
      }

    Providers that don't emit cost (emits_cost=False) will have cost_usd=null in
    their by_provider bucket to distinguish "zero cost" from "not applicable".
    """
    since = request.args.get('since', '')

    def _empty_bucket():
        return {'input_tokens': 0, 'output_tokens': 0, 'cost_usd': 0.0, 'sessions': 0}

    by_provider: dict = {}

    def _accumulate(bucket_key, usage_dict, cost, _has_cost):
        b = by_provider.setdefault(bucket_key, _empty_bucket())
        b['input_tokens'] += usage_dict.get('input_tokens', 0)
        b['output_tokens'] += usage_dict.get('output_tokens', 0)
        if _has_cost:
            b['cost_usd'] = (b['cost_usd'] or 0.0) + (cost or 0.0)
        else:
            # Mark as None only if we've never seen a cost for this provider.
            if b.get('cost_usd') == 0.0 and cost is None:
                b['cost_usd'] = None
        b['sessions'] += 1

    def _provider_emits_cost(provider_name):
        try:
            return _agent_runtime.get_runtime(provider_name).capabilities().emits_cost
        except Exception:
            return True  # conservative: don't discard data on unknown provider

    # Deduplicate by claude_session_id: Scribe checkpoints write multiple log
    # entries for the same session (each with cumulative totals). Keep only the
    # latest entry per csid to avoid counting the same session multiple times.
    _seen_csids: dict = {}   # csid -> (ts, entry, provider)
    _no_csid: list = []      # entries without a csid (counted individually)

    for f in DATA_DIR.glob('*_agent_log.json'):
        try:
            log = json.loads(f.read_text(encoding='utf-8'))
            for entry in log:
                if since and entry.get('ts', '') < since:
                    continue
                csid = entry.get('claude_session_id') or ''
                p = (entry.get('provider') or 'claude').lower()
                ts = entry.get('ts', '')
                if csid:
                    prev = _seen_csids.get(csid)
                    if prev is None or ts >= prev[0]:
                        _seen_csids[csid] = (ts, entry, p)
                else:
                    _no_csid.append((entry, p))
        except Exception:
            continue

    def _entry_usage(entry):
        """Return a usage-shaped dict for an agent_log entry.

        Prefers top-level input_tokens/output_tokens (set by
        _extract_transcript_telemetry — full-session JSONL sum) when non-zero.
        Falls back to the nested 'usage' dict (accumulated across turns for new
        entries; last-turn only for pre-fix legacy entries).
        """
        top_in  = int(entry.get('input_tokens') or 0)
        top_out = int(entry.get('output_tokens') or 0)
        if top_in or top_out:
            return {'input_tokens': top_in, 'output_tokens': top_out}
        nested = entry.get('usage') or {}
        return {'input_tokens': int(nested.get('input_tokens') or 0),
                'output_tokens': int(nested.get('output_tokens') or 0)}

    for ts, entry, p in _seen_csids.values():
        _accumulate(p, _entry_usage(entry), entry.get('cost_usd'), _provider_emits_cost(p))
    for entry, p in _no_csid:
        _accumulate(p, _entry_usage(entry), entry.get('cost_usd'), _provider_emits_cost(p))

    # Include running sessions that haven't been logged yet
    for s in agent_sessions.values():
        if since and s.get('started_at', '') < since:
            continue
        csid = s.get('claude_session_id') or ''
        if csid and csid in _seen_csids:
            continue  # already counted from log
        p = (s.get('provider') or 'claude').lower()
        usage = s.get('usage') or {}
        cost = s.get('cost_usd')
        _accumulate(p, usage, cost, _provider_emits_cost(p))

    # Build per-provider output with total_tokens computed
    by_provider_out = {}
    for pname, b in by_provider.items():
        by_provider_out[pname] = {
            'input_tokens': b['input_tokens'],
            'output_tokens': b['output_tokens'],
            'total_tokens': b['input_tokens'] + b['output_tokens'],
            'cost_usd': round(b['cost_usd'], 4) if b['cost_usd'] is not None else None,
            'sessions': b['sessions'],
        }

    # Grand total (sum across all providers; cost_usd sums only providers that emit it)
    total_input = sum(b['input_tokens'] for b in by_provider.values())
    total_output = sum(b['output_tokens'] for b in by_provider.values())
    total_cost = sum(
        (b['cost_usd'] or 0.0) for b in by_provider.values()
        if b['cost_usd'] is not None
    )
    total_sessions = sum(b['sessions'] for b in by_provider.values())

    return jsonify({
        'by_provider': by_provider_out,
        'total': {
            'input_tokens': total_input,
            'output_tokens': total_output,
            'total_tokens': total_input + total_output,
            'cost_usd': round(total_cost, 4),
            'sessions': total_sessions,
        },
        # Legacy flat fields — identical to current response for claude-only deployments.
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



# ── Skills endpoints ── extracted to mc/blueprints/skills_routes.py (1.3).
# (Module named skills_routes, not skills — the top-level skills.py owns the
# logic and the name.) wire() late-binds load_project until 1.11.
from mc.blueprints import skills_routes as _bp_skills  # noqa: E402

_bp_skills.wire(load_project_fn=load_project, load_projects_fn=load_projects,
                app_dir=_APP_DIR)
app.register_blueprint(_bp_skills.bp)
# Inbound shims (startup installers + the shared request helper used by the
# MCP/distiller sections until they extract):
_install_builtin_skills = _bp_skills._install_builtin_skills
_install_builtin_mcps = _bp_skills._install_builtin_mcps
_resolve_project_path_or_400 = _bp_skills._resolve_project_path_or_400

# ── MCP endpoints (server mgmt + per-project loadout + URL installer) ──
# extracted to mc/blueprints/mcp_routes.py (1.4). wire() late-binds the
# projects-family accessors until 1.11.
from mc.blueprints import mcp_routes as _bp_mcp  # noqa: E402

_bp_mcp.wire(load_project_fn=load_project, save_project_fn=save_project,
             data_dir=DATA_DIR, mcp_server_catalog_fn=_mcp_server_catalog)
app.register_blueprint(_bp_mcp.bp)

# ── Global config endpoints ────────────────────────────────────────────────

_CONFIG_EDITABLE_KEYS = {
    'user_name', 'agent_name', 'agent_model', 'agent_effort', 'agent_max_turns',
    'agent_permission_mode', 'agent_channels', 'agent_remote_control',
    'use_streaming_agent', 'condense_enabled', 'condense_threshold_kb',
    'condense_model', 'condense_mode', 'index_line_budget',
    'index_line_hard_floor',
    'scribe_enabled', 'scribe_model', 'scribe_reconcile_enabled',
    'scribe_reconcile_cap', 'scribe_checkpoint_enabled',
    'scribe_checkpoint_kb', 'read_floor_topk',
    'long_session_advisory_enabled', 'long_session_advisory_turns',
    'idle_eviction_enabled', 'idle_eviction_minutes',
    'projects_base', 'shared_rules_path', 'port', 'log_level',
    'mobile_brief_replies_enabled', 'brief_replies_always_enabled',
    'auto_model_enabled', 'auto_model_classifier_model',
    'auto_model_classifier_timeout_secs',
    'sticky_agent_settings',
    # Phase 4 Distiller (v2.1 §11 global keys).
    'distiller_enabled_global', 'distiller_cross_project_enabled',
    'distiller_model', 'distiller_window_days',
    'distiller_cost_cap_tokens_per_project_per_day',
    'distiller_proposal_dedupe_days',
    'distiller_cross_project_walk_debounce_session_count',
    'distiller_cross_project_walk_debounce_seconds',
}

# Respawn-trigger ("Tier-1a") settings: passed as CLI FLAGS at process launch and
# re-applied on a `-r` respawn, so flipping one mid-session and resuming actually
# changes behavior (this is exactly how the auto-router switches --model live).
# When `sticky_agent_settings` is on, flipping any of these marks live Mode B
# sessions to resume into a fresh process at the next turn boundary.
#
# DELIBERATELY EXCLUDED — system-prompt ("Tier-1b") settings (brief-reply
# directive `brief_replies_always_enabled`, `read_floor_topk`, rules-file edits):
# these live in --append-system-prompt-file, and a canary test (2026-06-04, Haiku)
# proved `claude -r` RESTORES the session's original system prompt and IGNORES a
# resume-time append (fresh+append → applied; -r+append → ignored, 0/4 trials;
# continuity probe confirmed -r really resumed). So a respawn can't apply them to
# a resumed chat — they only take effect on a FRESH spawn. Including them would
# just burn a re-prefill for no behavior change. See discovery memory
# claude-resume-ignores-append-system-prompt.
#
# Also excluded: per-turn settings (brief phone-mode, auto-router,
# scribe-checkpoint) take effect next turn for free; agent_name/user_name change
# rarely; MCP set is per-project (not a global key here).
_RESPAWN_TRIGGER_KEYS = {
    'agent_model', 'agent_effort', 'agent_max_turns', 'agent_permission_mode',
    'agent_channels', 'agent_remote_control', 'use_streaming_agent',
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
    # Sticky settings: if a spawn-baked (Tier-1) key changed, flag live Mode B
    # claude sessions to resume into a fresh process at their next turn boundary
    # so the change actually takes effect (a running CLI can't see spawn-baked
    # changes). Best-effort; agent_followup reads `_needs_respawn` under lock.
    respawn_flagged = 0
    if CONFIG.get('sticky_agent_settings', False):
        flipped = [k for k in updated if k in _RESPAWN_TRIGGER_KEYS]
        if flipped:
            for _sess in list(agent_sessions.values()):
                if (_sess.get('mode') == 'B'
                        and (_sess.get('provider') or 'claude').lower() == 'claude'
                        and _sess.get('process_alive')):
                    _sess['_needs_respawn'] = True
                    respawn_flagged += 1
            if respawn_flagged:
                _log(f"[sticky-settings] {flipped} changed → flagged "
                     f"{respawn_flagged} live Mode B session(s) for respawn")
    return jsonify({'ok': True, 'updated': list(updated.keys()),
                    'respawn_flagged': respawn_flagged})


# ── Folder browse (for project_path picker) ─────────────────────────────────

@app.route('/api/browse/folders')
def browse_folders():
    """List immediate subdirectories of the requested path. Used by the
    project_path picker UI so users can choose a folder without typing.
    Hidden / dot-prefixed dirs are filtered out."""
    raw = (request.args.get('path') or '').strip()
    if not raw:
        # Default landing: the auto-workspace base (creates if missing).
        base = Path(CONFIG.get('auto_workspace_base') or str(Path.home() / 'MissionControl'))
        try:
            base.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        target = base
    else:
        target = Path(raw).expanduser()

    try:
        target = target.resolve()
    except Exception:
        return jsonify({'error': 'invalid path'}), 400

    if not target.exists() or not target.is_dir():
        return jsonify({'error': 'not a directory', 'path': str(target)}), 404

    folders = []
    try:
        for entry in sorted(target.iterdir(), key=lambda p: p.name.lower()):
            try:
                if not entry.is_dir():
                    continue
                if entry.name.startswith('.'):
                    continue
                folders.append({'name': entry.name, 'path': str(entry)})
            except Exception:
                continue
    except PermissionError:
        return jsonify({'error': 'permission denied', 'path': str(target)}), 403
    except Exception as e:
        return jsonify({'error': str(e), 'path': str(target)}), 500

    parent = str(target.parent) if target.parent != target else None
    home = str(Path.home())
    base = str(Path(CONFIG.get('auto_workspace_base') or str(Path.home() / 'MissionControl')))
    return jsonify({
        'path': str(target),
        'parent': parent,
        'folders': folders,
        'home': home,
        'workspace_base': base,
    })


@app.route('/api/browse/create_folder', methods=['POST'])
def browse_create_folder():
    """Create a new subdirectory inside the given parent. Used by the picker
    so users can spin up a fresh workspace folder without leaving the UI."""
    data = request.get_json() or {}
    parent = (data.get('parent') or '').strip()
    name = (data.get('name') or '').strip()
    if not parent or not name:
        return jsonify({'error': 'parent and name required'}), 400
    # Reject path-traversal / absolute names.
    if any(c in name for c in ('/', '\\', ':')) or name in ('.', '..'):
        return jsonify({'error': 'invalid folder name'}), 400
    target = Path(parent).expanduser() / name
    try:
        target.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        return jsonify({'error': 'folder already exists', 'path': str(target)}), 409
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    return jsonify({'ok': True, 'path': str(target)})


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


# _scheduler_stop moved to mc/state.py (Phase 0).


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
                            if (now - last_dt).total_seconds() < 300:
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
                        if (now - last_dt).total_seconds() < 300:
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
                                if ts < cutoff:
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


@app.route('/api/schedules/<schedule_id>', methods=['PUT'])
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

@app.route('/sw.js')
def service_worker():
    # Served at root so the SW scope covers the whole origin (`/?session=...`
    # deep links delivered via push need to be routable from this worker).
    resp = send_from_directory(STATIC_DIR, 'sw.js')
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['Service-Worker-Allowed'] = '/'
    resp.headers['Content-Type'] = 'application/javascript'
    return resp


@app.route('/manifest.json')
def web_app_manifest():
    """PWA manifest, served from root with the correct
    `application/manifest+json` Content-Type and no-cache so manifest edits
    take effect on next page load instead of being stuck behind Flask's
    default 12-hour static-file cache. Chrome's installability check is
    sensitive to manifest changes; without no-cache the install offer can
    silently stall on the old cached copy.
    """
    resp = send_from_directory(STATIC_DIR, 'manifest.json')
    resp.headers['Cache-Control'] = 'no-cache'
    resp.headers['Content-Type'] = 'application/manifest+json'
    return resp


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

# _guardian_stop moved to mc/state.py (Phase 0).
GUARDIAN_CHECK_INTERVAL = 10
# Hung threshold: 10 minutes of *both* no stdout and no CPU progress.
# Claude can legitimately go silent for several minutes during long thinking,
# context loads, or tool calls — so we require CPU idleness as confirmation.
GUARDIAN_HUNG_TIMEOUT = 600
# Per-provider override. gemini-cli has an upstream non-interactive tool-call
# hang (google-gemini/gemini-cli#16567) where --yolo doesn't bypass the
# tool-confirmation-request and the JS event loop parks on the unresolved
# promise — no heartbeat, no exit, until killed. Gemini itself rarely needs
# more than ~60s for legitimate thinking, so a 90s watchdog catches the hang
# fast without false-positiving real work.
GUARDIAN_HUNG_TIMEOUT_BY_PROVIDER = {'gemini': 90}
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


def _should_evict_idle_session(session, now, enabled, idle_minutes):
    """Pure predicate for guardian idle-eviction (kept separate so it's unit-
    testable without spawning real processes).

    True iff this is a warm Mode B session with a live process that has been
    `idle` (turn finished, waiting on the user) for longer than `idle_minutes`.
    Evicting it kills the claude.exe + MCP-server fleet to free resources; the
    next user message respawns it with `-r <csid>` (full context). Only `idle`
    sessions qualify — a `running` session is mid-work and never evicted."""
    if not enabled or not idle_minutes or idle_minutes <= 0:
        return False
    if session.get('status') != 'idle' or session.get('mode') != 'B':
        return False
    if session.get('evicted'):
        return False
    # Don't evict a session with queued work or one waiting on the user — those
    # carry pending state the next turn needs; let them resolve first.
    if (session.get('pending_followups') or session.get('_dispatching_followup')
            or session.get('waiting_for_question')
            or session.get('waiting_for_plan_approval')):
        return False
    proc = session.get('proc')
    if proc is None or proc.poll() is not None:
        return False
    return (now - session.get('last_output_time', now)) > idle_minutes * 60


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
            _log(f"[guardian] Session {sid[:8]}: stuck running, process dead/missing")
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
            if (session.get('waiting_for_question') or session.get('waiting_for_plan_approval')
                    or session.get('evicted')):
                return
            old_status = status
            _log(f"[guardian] Session {sid[:8]}: PID {proc.pid} dead, was {old_status}")
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
        provider = (session.get('provider') or 'claude').lower()
        hung_timeout = GUARDIAN_HUNG_TIMEOUT_BY_PROVIDER.get(provider, GUARDIAN_HUNG_TIMEOUT)
        if silent_secs > hung_timeout and _proc_is_cpu_idle(session, proc, now):
            _log(f"[guardian] Session {sid[:8]} ({provider}): no output for {silent_secs:.0f}s, killing")
            with get_manager(session['project_id']).lock:
                # Gemini's tool-call hang has a known upstream cause — surface
                # it instead of the generic message so the user knows retrying
                # often works and it isn't an MC fault.
                if provider == 'gemini':
                    session['log_lines'].append(
                        f'[Guardian: gemini stuck for {silent_secs:.0f}s — '
                        f'likely upstream tool-call hang '
                        f'(google-gemini/gemini-cli#16567). Retrying usually works.]')
                else:
                    session['log_lines'].append(
                        f'[Guardian: no output for {silent_secs:.0f}s — killing hung process]')
                session['guardian_state'] = 'needs_attention'
            # Snapshot pid; release lock before kill (process-tree walk can be slow on Windows)
            _kill_proc_background(proc)
            return

    # State 8: idle-session eviction. Reclaim a warm Mode B fleet (claude.exe +
    # its MCP-server tree) after long inactivity; the next user message respawns
    # it via the followup path with `-r <csid>`, so context is preserved. The
    # `evicted` flag makes State 1 skip the now-dead-proc session instead of
    # flagging it 'error'; it is cleared on respawn. Default OFF.
    if _should_evict_idle_session(session, now,
                                  CONFIG.get('idle_eviction_enabled', False),
                                  CONFIG.get('idle_eviction_minutes', 60)):
        proc_to_kill = None
        with get_manager(session['project_id']).lock:
            # Re-check under lock — status/proc may have changed since the snapshot.
            if _should_evict_idle_session(session, now,
                                          CONFIG.get('idle_eviction_enabled', False),
                                          CONFIG.get('idle_eviction_minutes', 60)):
                idle_min = (now - session.get('last_output_time', now)) / 60
                session['evicted'] = True
                session['process_alive'] = False
                session['last_status_change_time'] = now
                session['log_lines'].append(
                    f'[Guardian: idle {idle_min:.0f} min — process evicted to free '
                    f'resources; next message resumes with full context]')
                _unregister_process(proc.pid)
                proc_to_kill = proc
        if proc_to_kill is not None:
            _log(f"[guardian] Session {sid[:8]}: evicted after idle timeout")
            _kill_proc_background(proc_to_kill)
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
                _log(f"[port-conflict] dying parent (PID {restart_parent}) released port {PORT}; continuing.", flush=True)
                return
        _log(f"[port-conflict] waited 15s for parent PID {restart_parent} to release port {PORT}; falling through to conflict check.", flush=True)

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
    _log('\n'.join(msg_lines), flush=True)

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
        _log(f"[port-conflict] MC_ALLOW_PORT_CONFLICT=1 set — proceeding ANYWAY. "
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

    _log('[remote-access] LOCAL MOCK control plane enabled at /api/_mock/connect '
          '(dev only; do not enable in production)', flush=True)


# ── Remote Access (Mission Control Cloud) ── extracted to
# mc/blueprints/remote_routes.py (1.7): the 12 /api/remote/* routes, the /_mc
# device-label pages, /api/tunnel-handshake + /api/mc-callback, the
# session-labels store, the CF Access JWT machinery (_is_cf_tunneled_request +
# friends), the unnamed-session label enforcer + its daemon loop, and the
# mc_remote_iface provider-discovery glue (import side-effect now fires here;
# the registry is only read at request/loop time). wire() late-binds the
# _DATA_ROOT-derived session-labels path. This stanza sits ABOVE the
# push_mobile / local_auth / system stanzas so their wire() calls can re-home
# onto _bp_remote.* (names must exist at import time — the 1.2 lesson). The
# MC_REMOTE_LOCAL_MOCK dev-only mock control plane above STAYS here — it
# mocks the cloud CP, not this family. The _redirect_unlabeled_cf_session
# before_request handler stays registered on `app` below (same source
# position → hook order unchanged) with its body in the module.
from mc.blueprints import remote_routes as _bp_remote  # noqa: E402

_bp_remote.wire(
    session_labels_path=_DATA_ROOT / 'data' / 'session_labels.json',
)
app.register_blueprint(_bp_remote.bp)
# Inbound shims: startup (under __main__) starts the enforcer daemon and the
# one-shot CP warmup thread — call sites unchanged.
_session_label_enforcer_loop = _bp_remote._session_label_enforcer_loop
_warmup_control_plane = _bp_remote._warmup_control_plane


# ── Web push + presence + mobile pairing ── extracted to
# mc/blueprints/push_mobile.py (1.2). wire() late-binds _DATA_ROOT paths +
# load_project (projects family, 1.11) + the remote-family fns (re-homed
# onto _bp_remote at 1.7). _handle_push_signal stays importable here for the
# stream readers until 1.12 moves them.
from mc.blueprints import push_mobile as _bp_push_mobile  # noqa: E402

_bp_push_mobile.wire(
    data_root=_DATA_ROOT,
    load_project_fn=load_project,
    cf_session_nonce_fn=_bp_remote._cf_session_nonce_from_request,
    get_remote_provider_fn=_bp_remote._get_remote_provider,
)
app.register_blueprint(_bp_push_mobile.bp)
_handle_push_signal = _bp_push_mobile._handle_push_signal


# ── Local (LAN) passcode gate ── extracted to mc/blueprints/local_auth.py (1.1) ──
# Routes, helpers, and the gate body moved verbatim. wire() late-binds the
# _DATA_ROOT path + _is_cf_tunneled_request (re-homed onto _bp_remote at
# 1.7). The before_request handler stays registered on `app`
# here — same registration position as before, so hook order vs.
# _redirect_unlabeled_cf_session is unchanged — with its body in the module.
from mc.blueprints import local_auth as _bp_local_auth  # noqa: E402

_bp_local_auth.wire(
    local_auth_path=_DATA_ROOT / 'data' / 'local_auth.json',
    is_cf_tunneled_request=_bp_remote._is_cf_tunneled_request,
)
app.register_blueprint(_bp_local_auth.bp)


@app.before_request
def _local_auth_gate():
    return _bp_local_auth.local_auth_gate()


@app.before_request
def _redirect_unlabeled_cf_session():
    return _bp_remote.redirect_unlabeled_cf_session()


# ── System + restart + update endpoints ── extracted to
# mc/blueprints/system_routes.py (1.6), including the update-check daemon
# loop (started below at startup, unchanged position) and the status cache.
# _LAST_SYSTEM_STATUS/_LAST_RESTART_TIME live in mc/state.py; the two
# stream-reader touch points write state._LAST_SYSTEM_STATUS directly.
from mc.blueprints import system_routes as _bp_system  # noqa: E402

_bp_system.wire(load_project_fn=load_project, load_projects_fn=load_projects,
                data_dir=DATA_DIR, data_root=_DATA_ROOT, app_dir=_APP_DIR,
                popen_flags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
                backfill_token_telemetry_fn=_backfill_token_telemetry,
                is_cf_tunneled_request_fn=_bp_remote._is_cf_tunneled_request,
                kill_pid_fn=_kill_pid,
                kill_proc_background_fn=_kill_proc_background,
                pid_is_alive_fn=_pid_is_alive,
                resolve_claude_fn=_resolve_claude,
                stop_session_fn=_stop_session,
                get_manager_fn=get_manager,
                get_manager_for_session_fn=get_manager_for_session,
)
app.register_blueprint(_bp_system.bp)
# Inbound shims: stream readers call _capture_system_init; startup starts
# the update-check daemon. Both re-home at 1.12/1.13.
_capture_system_init = _bp_system._capture_system_init
_update_check_loop = _bp_system._update_check_loop

# ── Terminal session endpoints ── extracted to
# mc/blueprints/terminal_routes.py (1.8): the 5 /api/terminal/* routes +
# /api/project/<id>/terminal/status, the reader/kill helpers, and the
# TTY-shim env wiring. wire() late-binds load_project (projects family,
# 1.11), get_manager + _register_process/_unregister_process (dispatch
# family — the process ledger is shared by agent/hivemind/housekeeping
# spawns, so the fns stay here until 1.12), the Popen platform consts, and
# the _APP_DIR-derived shim dir (the 1.7 wired-placeholder pattern).
from mc.blueprints import terminal_routes as _bp_terminal  # noqa: E402

_bp_terminal.wire(
    load_project_fn=load_project,
    get_manager_fn=get_manager,
    register_process_fn=_register_process,
    unregister_process_fn=_unregister_process,
    popen_flags=_POPEN_FLAGS,
    startupinfo=_STARTUPINFO,
    tty_shim_dir=str(_APP_DIR / 'mc_tty_shim'),
)
app.register_blueprint(_bp_terminal.bp)
# Inbound shim: delete_project (projects family, 1.11) and the atexit
# _cleanup_terminals hook kill terminal sessions — both call sites stay; the
# global resolves at call time, after this binding exists.
_kill_terminal_session = _bp_terminal._kill_terminal_session

# ── AgentRuntime hook registration ──────────────────────────────────────────
# Wire ClaudeRuntime delegates back into server.py so external callers (future
# workstreams, tests) can use get_runtime('claude').dispatch() etc. and have
# them run the real claude path. Adapters bridge the SessionHandle API ↔
# server.py internal API (session_id + agent_sessions). Design §9.1 scope.


def _claude_health_check_hook():
    """Bridge: ClaudeRuntime.health_check() → server.py auth state."""
    from agent_runtime import HealthStatus, AuthState
    import time as _t
    with _claude_auth_lock:
        state = dict(_claude_auth_state)
    installed = bool(_resolve_claude() != 'claude' or shutil.which('claude'))
    status = state.get('state', 'unknown')
    return HealthStatus(
        installed=installed,
        binary_path=None,
        version=None,
        auth_state=AuthState(
            status=status,
            method=state.get('method'),
            last_checked=str(state.get('last_probe_at', _t.time())),
            error_text=state.get('reason'),
        ),
        install_hint='npm install -g @anthropic-ai/claude-code',
    )


def _claude_dispatch_hook(**kwargs):
    """Bridge: ClaudeRuntime.dispatch(**kwargs) → _dispatch_agent_internal().

    Accepts the kwargs signature used by _dispatch_via_runtime() so that
    get_runtime('claude').dispatch() works for both external callers and
    internal sessions that want to target claude explicitly. Returns a
    SessionHandle wrapping the existing agent_sessions entry.
    """
    project_id = kwargs.get('project_id', '')
    task = kwargs.get('task', '')
    resume_id = kwargs.get('resume_id') or ''
    incognito = bool(kwargs.get('incognito', False))
    trigger_type = kwargs.get('trigger_type') or 'manual'
    trigger_id = kwargs.get('trigger_id') or ''
    mc_session_id = kwargs.get('mc_session_id') or ''

    session_id = _dispatch_agent_internal(
        project_id, task,
        resume_id=resume_id,
        incognito=incognito,
        trigger_type=trigger_type,
        trigger_id=trigger_id,
        reuse_session_id=mc_session_id,
    )
    session = agent_sessions.get(session_id, {})
    p = load_project(project_id) or {}

    return _agent_runtime.SessionHandle(
        mc_session_id=session_id,
        provider='claude',
        mode=session.get('mode', 'A'),
        project_path=p.get('project_path', kwargs.get('project_path', '')),
        project_id=project_id,
        session_dict=session,
        started_at=session.get('started_at', ''),
        capabilities=_agent_runtime.get_runtime('claude').capabilities(),
    )


def _claude_followup_hook(handle, message, attachments=None):
    """Bridge: ClaudeRuntime.write_followup(handle, message) → followup logic.

    Looks up the existing session and writes the message via the standard
    stdin path (Mode B) or queues a new process (Mode A).
    """
    session_id = handle.mc_session_id
    project_id = handle.project_id
    existing = agent_sessions.get(session_id)
    if not existing:
        raise RuntimeError(f"_claude_followup_hook: session {session_id!r} not found")

    p = load_project(project_id) or {}
    pp = handle.project_path or p.get('project_path', '')

    if existing.get('mode') == 'B' and existing.get('process_alive'):
        proc = existing.get('proc')
        if proc and proc.poll() is None:
            stdin_msg = json.dumps({
                "type": "user",
                "message": {"role": "user", "content": message}
            }) + '\n'
            lock = existing.get('stdin_lock')
            if lock:
                with lock:
                    proc.stdin.write(stdin_msg)
                    proc.stdin.flush()
            else:
                proc.stdin.write(stdin_msg)
                proc.stdin.flush()
            return
    # Fall through: spawn new claude process (Mode A or dead Mode B)
    claude_sid = existing.get('claude_session_id')
    resume_flags = ['-r', claude_sid] if claude_sid else ['--continue']
    cmd = [_resolve_claude(), *resume_flags, '-p', message, *_build_claude_flags(p)]
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, cwd=pp,
            text=True, encoding='utf-8', errors='replace',
            creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
        )
        existing['proc'] = proc
        existing['status'] = 'running'
        existing['last_status_change_time'] = _time.time()
        threading.Thread(target=_read_agent_stream, args=(proc, existing), daemon=True).start()
    except Exception as e:
        existing['log_lines'].append(f'[hook followup failed: {e}]')
        existing['status'] = 'error'
        existing['last_status_change_time'] = _time.time()


def _claude_interrupt_hook(handle):
    """Bridge: ClaudeRuntime.interrupt(handle) → kill the claude process."""
    session = agent_sessions.get(handle.mc_session_id)
    if not session:
        return
    proc = session.get('proc')
    if proc:
        try:
            if proc.poll() is None:
                _kill_pid(proc.pid, tree=True)
                proc.kill()
        except Exception:
            pass
    session['status'] = 'stopped'
    session['last_status_change_time'] = _time.time()
    session['process_alive'] = False
    session['log_lines'].append('[interrupted via runtime hook]')


def _claude_stop_hook(handle):
    """Bridge: ClaudeRuntime.stop(handle) → graceful stop (same as interrupt for claude)."""
    _claude_interrupt_hook(handle)


def _register_claude_runtime_hooks():
    """Wire ClaudeRuntime delegates to server.py implementations. Called at startup."""
    _agent_runtime.register_claude_hooks(
        resolve_binary=_resolve_claude,
        health_check=_claude_health_check_hook,
        dispatch=_claude_dispatch_hook,
        followup=_claude_followup_hook,
        stop=_claude_stop_hook,
        interrupt=_claude_interrupt_hook,
        oneshot=lambda **kw: _agent_runtime.OneshotResult(text=_scribe_call(
            kw.get('model', 'haiku'),
            kw.get('prompt', ''),
            kw.get('stdin_text', '') or '',
        )) if kw.get('prompt') else None,
    )
    # MC Tool Protocol side effects that need server-side logic — wires
    # emulated mc:todo to the same backlog sync Claude's native TodoWrite uses.
    _agent_runtime.register_mc_tool_hooks(sync_todos=_sync_todowrite_to_backlog)


if __name__ == '__main__':
    _register_claude_runtime_hooks()
    _check_port_conflict()
    # Reap child process trees orphaned by a prior MC instance that exited
    # (restart/crash) without killing them. Reads the PID ledger the prior
    # instance persisted; identity-guarded so it can't friendly-fire. Must run
    # before any subsystem spawns its own children. [leak fix 2026-06-03]
    try:
        _reap_prior_instance_strays()
    except Exception as e:
        _log(f"[reaper] startup reap failed: {e}")
    _start_scheduler()
    _start_hivemind_orchestrator()
    _start_session_guardian()
    # Install built-in skills bundled with MC into ~/.claude/skills/.
    # Checksum-aware: user edits to managed skills are preserved.
    _install_builtin_skills()
    # Install/backfill built-in MCP servers (filesystem per-project,
    # sequential-thinking global). Same checksum-preservation pattern.
    _install_builtin_mcps()
    # Sweep stale Git-import staging dirs (>24h old) so they don't accumulate.
    try:
        n = _skills.cleanup_stale_staging(max_age_hours=24)
        if n:
            _log(f"[skills] cleaned {n} stale staging dir(s)")
    except Exception as e:
        _log(f"[skills] staging cleanup failed: {e}")
    # Ensure the global incognito pseudo-project exists so it shows up in
    # /api/projects without the FE needing a first-touch bootstrap.
    try:
        _ensure_incognito_project()
    except Exception as e:
        _log(f"[incognito] bootstrap failed: {e}")
    # Reconcile pending agent_log rows: any 'in_progress' entry leftover from a
    # session that was killed by the previous shutdown is by definition orphaned
    # (no live sessions exist yet at startup). Flip those to 'interrupted' so
    # they don't show as forever-running in the Agent Log / Runs panels.
    # Cheap, synchronous; runs before backfill so the two helpers don't race.
    try:
        _reconcile_pending_agent_log_entries()
    except Exception as e:
        _log(f"[reconcile-pending] bootstrap failed: {e}")
    # Backfill agent_log from Claude transcripts: makes mid-flight sessions that
    # never finalized (server killed before stream reader's finally) visible in
    # the Agent Log tab. Runs once, in the background, so app.run() isn't blocked.
    # Roll back: set agent_log_backfill_enabled = false in data/config.json.
    threading.Thread(target=_startup_memory_maintenance, daemon=True).start()
    # One-shot: transition orphaned 'active' hiveminds to 'stale'. Cheap, runs
    # synchronously before app.run().
    try:
        _hm_reconcile_stale_on_startup()
    except Exception as e:
        _log(f"[hivemind-reconcile] bootstrap failed: {e}")
    # Auto-cleanup unnamed CF Access sessions (per-session revoke, strict mode).
    # Roll back: set auto_revoke_unnamed_sessions=false in data/config.json.
    threading.Thread(target=_session_label_enforcer_loop, daemon=True).start()
    # Cloud Run cold-start mitigation: hit /v1/health on startup so the user's
    # first interaction (Enable / Resume / Disconnect) hits a warm CP instance.
    # Cheap; idempotent; safe even if remote-access provider is absent.
    threading.Thread(target=_warmup_control_plane, daemon=True).start()
    # Background update-check: fetches origin every 6h, caches behind-count.
    # Lets the dashboard show a passive "update available" badge without
    # firing a 12s git operation on every page load. Frontend polls
    # /api/system/update/cached.
    threading.Thread(target=_update_check_loop, daemon=True, name='update-check').start()
    _log(f"Clayrune running at http://localhost:{PORT}")
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
