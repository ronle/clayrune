"""Agent dispatch family — blueprint 1.12 (MODERNIZATION_PLAN.md).

Moved VERBATIM from server.py (app-to-bp route-decorator swap; CONFIG reads
rewritten to state.CONFIG — the 1.7/1.9/1.11 precedent). 33 routes (the plan
table said 9 /api/agent + 3 /api/claude — that count predates the provider
generalization and the run-history growth, same undercount story as
1.3/1.4/1.10):

  • claude binary resolution (_resolve_claude/_claude) + pid/kill/window
    helpers (_pid_is_alive/_kill_pid/_hide_process_windows/_hide_windows_delayed)
  • the global incognito pseudo-project (INCOGNITO_PROJECT_ID +
    _ensure_incognito_project)
  • per-project MCP trim (_mcp_server_catalog/_resolve_project_mcp_config) +
    _build_claude_flags + the auto-model router (classifier pool,
    _resolve_dispatch_model/_dispatch_with_routing*/_route_dispatch_model,
    _router_stat telemetry, /api/router/stats)
  • sysprompt temp-file plumbing (_sysprompt_file_args/_sysprompt_cleanup)
  • ProjectAgentManager + get_manager(+_for_session)/all_managers + the
    per-project guardian loop, plus the Session Guardian check family
    (GUARDIAN_* consts, _guardian_check_session & co., idle eviction)
  • the process ledger writers _register_process/_unregister_process (the
    reaper + _proc_identity/_persist_pid_ledger STAY in server.py — startup
    family — and are wired in)
  • /api/agent/upload-image (+_downscale_image_if_huge; quota helpers
    cross-imported from project_routes)
  • claude auth-state tracking + /api/agent/providers + provider env store +
    the generic /api/agent/<provider>/auth-* family + legacy /api/claude/*
    shims (3)
  • the agent context builders (_clayrune_api_reference/
    _clayrune_universal_capabilities/_skills_catalog_block/
    _build_agent_context) and the TodoWrite->backlog sync
  • BOTH stream readers (_read_agent_stream/_read_agent_stream_b — now
    heartbeating as 'stream-reader:a'/'stream-reader:b' in
    /api/system/loops, Phase 2) + _auto_recover_failed_resume
  • the agent_log store (_load_agent_log/_save_agent_log) + completion/
    dispatch-pending writers + _session_usage_payload + revive machinery
  • _dispatch_via_runtime + _dispatch_agent_internal + the 11 /api/.../agent/*
    routes incl. the 492-line agent_followup (moved WHOLE — decomposition is
    explicitly out of scope per the plan) + plan-file pair + transcript/
    reconstruct + recent-runs/search-chats/conversations/plans/usage

THE LOAD-BEARING LINE (CLAUDE.md "Memory system"): ALL scribe/condense/
checkpoint/MEMORY.md-write machinery stays in server.py. Every call from
moved code into that family is WIRED (see wire(): maybe_checkpoint_fn,
write_session_memory_fn, dispatch_condense_fn, should_condense_fn,
get_condense_status_fn, scribe_call_fn, memory_search_fn,
get_memory_path_fn/get_archive_path_fn). Nothing in this module takes
_get_mem_write_lock or writes MEMORY.md.
"""

import concurrent.futures
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time as _time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from flask import Blueprint, Response, jsonify, request

from mc import obs, state
from mc import state as _mc_state  # readers write _mc_state._LAST_SYSTEM_STATUS verbatim
from mc.core import _harden_secret_perms, _log, now_iso, time_ago
from mc.state import (
    _backlog_sync_lock,
    _claude_auth_lock,
    _claude_auth_state,
    _condense_lock,
    _condensing_projects,
    _guardian_stop,
    _managers,
    _managers_lock,
    _provider_env_lock,
    agent_sessions,
    process_tracker_lock,
    terminal_sessions,
    tracked_processes,
)

import agent_runtime as _agent_runtime  # Multi-provider abstraction
import distiller as _distiller          # exploration read-floor (registered by server.py)
import skills as _skills                # _skills_catalog_block

# Cross-blueprint imports (the 1.4/1.5/1.11 precedent — defs, not wire
# placeholders; called at request/stream time only, long after server.py has
# wired every blueprint):
from mc.blueprints.project_routes import (
    _incoming_file_size,
    _log_agent_activity,
    _upload_limit,
)
from mc.blueprints.push_mobile import _handle_push_signal      # re-homed 1.2 shim
from mc.blueprints.system_routes import _capture_system_init   # re-homed 1.6 shim

bp = Blueprint('agent_routes', __name__)

# ── wired by server.py (see wire()) ──────────────────────────────────────────
DATA_DIR: Path = None  # type: ignore[assignment]
UPLOADS_DIR: Path = None  # type: ignore[assignment]
_APP_DIR: Path = None  # type: ignore[assignment]
PORT: int = 0
SHARED_RULES_PATH: Path = None  # type: ignore[assignment]
PROVIDER_ENV_PATH: Path = None  # type: ignore[assignment]
CLAUDE_HOME: Path = None  # type: ignore[assignment]
_POPEN_FLAGS: int = 0
_STARTUPINFO: Any = None
load_project: Callable[[str], Optional[dict]] = None  # type: ignore[assignment]
save_project: Callable[[str, dict], Any] = None  # type: ignore[assignment]
load_projects: Callable[[], list] = None  # type: ignore[assignment]
# Memory/scribe/condense family — STAYS in server.py (CLAUDE.md lock+atomic
# discipline); these are the wired call seams:
_get_memory_path: Callable[[dict], Path] = None  # type: ignore[assignment]
_get_archive_path: Callable[[dict], Path] = None  # type: ignore[assignment]
_memory_search: Callable[..., list] = None  # type: ignore[assignment]
_maybe_checkpoint: Callable[[dict], None] = None  # type: ignore[assignment]
_write_session_memory: Callable[..., bool] = None  # type: ignore[assignment]
_dispatch_condense: Callable[[dict], None] = None  # type: ignore[assignment]
_should_condense: Callable[..., bool] = None  # type: ignore[assignment]
_get_condense_status: Callable[[str], dict] = None  # type: ignore[assignment]
_scribe_call: Callable[[str, str, str], str] = None  # type: ignore[assignment]
# Transcript/scan helpers — shared with the scribe/backfill stayers:
_find_transcript_file: Callable[..., Any] = None  # type: ignore[assignment]
_parse_transcript_messages: Callable[..., list] = None  # type: ignore[assignment]
_recent_claude_transcripts: Callable[..., list] = None  # type: ignore[assignment]
_session_too_large: Callable[..., tuple] = None  # type: ignore[assignment]
_long_session_advisory: Callable[[dict], Any] = None  # type: ignore[assignment]
_resume_is_fragile: Callable[..., bool] = None  # type: ignore[assignment]
_encode_project_path: Callable[..., Any] = None  # type: ignore[assignment]
_extract_transcript_telemetry: Callable[..., dict] = None  # type: ignore[assignment]
# PID-ledger internals — the reaper family stays in server.py:
_proc_identity: Callable[..., tuple] = None  # type: ignore[assignment]
_persist_pid_ledger: Callable[[], None] = None  # type: ignore[assignment]


def wire(*, data_dir, uploads_dir, app_dir, port, shared_rules_path,
         provider_env_path, claude_home, popen_flags, startupinfo,
         load_project_fn, save_project_fn, load_projects_fn,
         get_memory_path_fn, get_archive_path_fn, memory_search_fn,
         maybe_checkpoint_fn, write_session_memory_fn, dispatch_condense_fn,
         should_condense_fn, get_condense_status_fn, scribe_call_fn,
         find_transcript_file_fn, parse_transcript_messages_fn,
         recent_claude_transcripts_fn, session_too_large_fn,
         long_session_advisory_fn, resume_is_fragile_fn,
         encode_project_path_fn, extract_transcript_telemetry_fn,
         proc_identity_fn, persist_pid_ledger_fn):
    """Late-bind cross-family deps. Called once by server.py after the
    memory/scribe/condense machinery (which stays there) is defined."""
    global DATA_DIR, UPLOADS_DIR, _APP_DIR, PORT, SHARED_RULES_PATH
    global PROVIDER_ENV_PATH, CLAUDE_HOME, _POPEN_FLAGS, _STARTUPINFO
    global load_project, save_project, load_projects
    global _get_memory_path, _get_archive_path, _memory_search
    global _maybe_checkpoint, _write_session_memory, _dispatch_condense
    global _should_condense, _get_condense_status, _scribe_call
    global _find_transcript_file, _parse_transcript_messages
    global _recent_claude_transcripts, _session_too_large
    global _long_session_advisory, _resume_is_fragile, _encode_project_path
    global _extract_transcript_telemetry, _proc_identity, _persist_pid_ledger
    DATA_DIR = data_dir
    UPLOADS_DIR = uploads_dir
    _APP_DIR = app_dir
    PORT = port
    SHARED_RULES_PATH = shared_rules_path
    PROVIDER_ENV_PATH = provider_env_path
    CLAUDE_HOME = claude_home
    _POPEN_FLAGS = popen_flags
    _STARTUPINFO = startupinfo
    load_project = load_project_fn
    save_project = save_project_fn
    load_projects = load_projects_fn
    _get_memory_path = get_memory_path_fn
    _get_archive_path = get_archive_path_fn
    _memory_search = memory_search_fn
    _maybe_checkpoint = maybe_checkpoint_fn
    _write_session_memory = write_session_memory_fn
    _dispatch_condense = dispatch_condense_fn
    _should_condense = should_condense_fn
    _get_condense_status = get_condense_status_fn
    _scribe_call = scribe_call_fn
    _find_transcript_file = find_transcript_file_fn
    _parse_transcript_messages = parse_transcript_messages_fn
    _recent_claude_transcripts = recent_claude_transcripts_fn
    _session_too_large = session_too_large_fn
    _long_session_advisory = long_session_advisory_fn
    _resume_is_fragile = resume_is_fragile_fn
    _encode_project_path = encode_project_path_fn
    _extract_transcript_telemetry = extract_transcript_telemetry_fn
    _proc_identity = proc_identity_fn
    _persist_pid_ledger = persist_pid_ledger_fn
    # Moved module-level side effect (see the tombstone in the provider-env
    # section below): hydrate persisted provider env vars into os.environ now
    # that PROVIDER_ENV_PATH is bound. Runs during server.py module exec,
    # before app.run() and before any agent spawn — timing-equivalent.
    _hydrate_provider_env_into_os()

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
    return _agent_runtime.get_runtime('claude').resolve_binary_str()  # pyright: ignore[reportAttributeAccessIssue]  # moved-verbatim typing debt (1.12)


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
    base = Path(state.CONFIG.get('auto_workspace_base') or str(Path.home() / 'MissionControl'))
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
        (project or {}).get('agent_model', '') or state.CONFIG.get('agent_model', '')
    )
    effort = (project or {}).get('agent_effort', '') or state.CONFIG.get('agent_effort', '')
    return _agent_runtime.get_runtime('claude').build_command(
        model=model,
        max_turns=state.CONFIG.get('agent_max_turns', 0),
        streaming=streaming,
        perm_mode=state.CONFIG.get('agent_permission_mode', ''),
        channels=(project or {}).get('agent_channels', '') or state.CONFIG.get('agent_channels', ''),
        remote_control=bool(
            (project or {}).get('agent_remote_control', False) or
            state.CONFIG.get('agent_remote_control', False)
        ),
        effort=effort,  # pyright: ignore[reportCallIssue]  # moved-verbatim typing debt (1.12)
        mcp_config_json=_resolve_project_mcp_config(project) or '',  # pyright: ignore[reportCallIssue]  # moved-verbatim typing debt (1.12)
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
    fallback = (project or {}).get('agent_model', '') or state.CONFIG.get('agent_model', '') or 'sonnet'
    if not prompt or not state.CONFIG.get('auto_model_enabled', False):
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
    if not state.CONFIG.get('auto_model_enabled', False) or not prompt:
        context = context_builder() if context_builder else ''
        model, source, flags = _dispatch_with_routing(project, prompt, streaming=streaming)
        return model, source, flags, context, ''

    fallback = (project or {}).get('agent_model', '') or state.CONFIG.get('agent_model', '') or 'sonnet'
    fut = _classifier_pool.submit(_route_dispatch_model, prompt, fallback)
    context = context_builder() if context_builder else ''
    timeout = max(1, int(state.CONFIG.get('auto_model_classifier_timeout_secs', 8) or 8))
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
                     name='sysprompt-cleanup').start()

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

@bp.route('/api/router/stats', methods=['GET'])
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
                         Image.LANCZOS)  # pyright: ignore[reportAttributeAccessIssue]  # moved-verbatim typing debt (1.12)
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


@bp.route('/api/agent/upload-image', methods=['POST'])
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


@bp.route('/api/agent/providers')
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

# PROVIDER_ENV_PATH is wired by server.py (see wire()) — the 1.7
# SESSION_LABELS_PATH wired-placeholder pattern (declared above).
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


# _hydrate_provider_env_into_os() is called from wire() — the module-level
# call moved there because PROVIDER_ENV_PATH is wire-time state (1.6/1.7
# pattern: module-level side effects move into wire(); still runs before
# app.run() / any spawn, so child-env inheritance timing is equivalent).


@bp.route('/api/agent/provider/<name>/auth')
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


@bp.route('/api/agent/provider/<name>/env', methods=['POST'])
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


@bp.route('/api/agent/provider/<name>/login-launch', methods=['POST'])
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


@bp.route('/api/agent/<provider>/auth-status')
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


@bp.route('/api/agent/<provider>/auth-probe', methods=['POST'])
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


@bp.route('/api/agent/<provider>/auth-login', methods=['POST'])
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


@bp.route('/api/agent/<provider>/auth-logout', methods=['POST'])
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


@bp.route('/api/claude/auth-status')
def claude_auth_status():
    """Backward-compat shim → /api/agent/claude/auth-status."""
    with _claude_auth_lock:
        return jsonify(dict(_claude_auth_state))


@bp.route('/api/claude/login-launch', methods=['POST'])
def claude_login_launch():
    """Backward-compat shim → /api/agent/claude/auth-login."""
    return agent_auth_login('claude')


@bp.route('/api/claude/auth-probe', methods=['POST'])
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

        # The chat renderer inlines absolute image paths as thumbnails via
        # /api/serve-image (allowlist: project root, the Clayrune repo,
        # Clayrune's data/uploads). Markdown image syntax is NOT rendered.
        "Images: To SHOW the user an image, output its ABSOLUTE file path on "
        "its own line in your chat reply — Clayrune renders it as an inline "
        "thumbnail (click to enlarge). This is a platform fact, not something "
        "to test or hedge about. Constraints: (1) the file must live under "
        "the project root or Clayrune's data/uploads — paths outside those "
        "(e.g. the user's Pictures folder) are refused by the server and "
        "degrade to a plain link; copy such files into the project first, "
        "then output the new path. (2) Do NOT use markdown image syntax "
        "![alt](path) — it does not render as markdown here. (3) Screenshots "
        "or images the user attaches arrive as data/uploads paths you can "
        "open with your file tools.",

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
        "Project memory: when you hit an unknown about this project's history, "
        "a prior decision, or a convention, use the mc-memory-search skill "
        "before guessing — it ranks the project's topic files, archive, and "
        "session log. Relevant memory for the current task is also "
        "auto-surfaced in your context under 'RELEVANT MEMORY'.",
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

def _build_agent_context(project, incognito=False, task='', character_body=''):
    """Build system prompt context for the agent.

    character_body, when set, is the markdown body of a per-chat "character"
    (a Claude Code subagent persona the user picked at new-chat time). It is
    injected ONCE here at spawn beside AGENT_RULES — never on resume (claude
    -r restores the original system prompt), which is exactly why the
    character is immutable for the chat's lifetime (Prompt Builder Phase 2).

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
    agent_name = state.CONFIG.get('agent_name', '')
    user_name = state.CONFIG.get('user_name', '')
    if agent_name:
        parts.append(f"Your name is {agent_name}.")
    if user_name:
        parts.append(f"The user's name is {user_name}. Address them accordingly.")
    # Sticky brevity: when sticky_agent_settings is on, the device-neutral brief
    # directive lives HERE (cached, once per spawn) instead of being prepended to
    # every user turn by _apply_mobile_brief. Flipping the toggle mid-session is
    # handled by the respawn-on-flip path (see update_config / agent_followup).
    if (state.CONFIG.get('sticky_agent_settings', False)
            and state.CONFIG.get('brief_replies_always_enabled', False)):
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

    # Per-chat character/persona (Prompt Builder Phase 2). After the rules so
    # project/shared rules retain primacy over a chosen persona's voice.
    if character_body:
        parts.append(f"--- CHARACTER (active persona for this chat) ---\n{character_body.strip()}")

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
                                  int(state.CONFIG.get('read_floor_topk', 3) or 3))
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
    if task and not incognito and state.CONFIG.get('exploration_readback_enabled', True):
        try:
            expl = _distiller.exploration_read_floor(
                project['id'], task,
                int(state.CONFIG.get('exploration_read_floor_topk', 2) or 2))
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


# _append_note_to_backlog_item ── moved to mc/blueprints/project_routes.py (1.11)
# with its only caller (the backlog note route).


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
    if name == 'Bash':
        cmd = (inp.get('command', '') or inp.get('description', '') or '')[:80]
        return f'[tool: Bash] {cmd}'
    if name in ('Grep', 'Glob'):
        pat = inp.get('pattern', '')
        return f'[tool: {name}] {pat}'
    if name == 'Task':
        desc = (inp.get('description', '') or '')[:50]
        return f'[tool: Task] {desc}'
    if name == 'WebSearch':
        q = (inp.get('query', '') or '')[:60]
        return f'[tool: WebSearch] {q}'
    if name == 'AskUserQuestion':
        qs = inp.get('questions', [])
        preview = qs[0].get('question', '')[:60] if qs else ''
        return f'[tool: AskUserQuestion] {preview}'
    if name == 'TodoWrite':
        todos = inp.get('todos', []) or []
        total = len(todos)
        done = sum(1 for t in todos if isinstance(t, dict) and t.get('status') == 'completed')
        in_prog = next((t.get('content', '') for t in todos
                        if isinstance(t, dict) and t.get('status') == 'in_progress'), '')
        summary = f'{done}/{total}'
        if in_prog:
            summary += f' — now: {in_prog[:60]}'
        return f'[tool: TodoWrite] {summary}'
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
            obs.heartbeat('stream-reader:a')  # Phase 2 loop observability (1.12)
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
            obs.heartbeat('stream-reader:b')  # Phase 2 loop observability (1.12)
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
            proc.stdin.write(initial_msg)  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.12)
            proc.stdin.flush()  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.12)

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
    cap = int(state.CONFIG.get('agent_log_max_entries', 500) or 0)
    if cap > 0 and len(log) > cap:
        log = log[:cap]
    filepath.write_text(json.dumps(log, indent=2, ensure_ascii=False), encoding='utf-8')

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
    if not state.CONFIG.get('agent_revive_from_log', True):
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

    use_streaming = p.get('use_streaming_agent', state.CONFIG.get('use_streaming_agent', False))

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
    user_label = state.CONFIG.get('user_name') or 'User'
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
                proc.stdin.write(stdin_msg)  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.12)
                proc.stdin.flush()  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.12)
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
        'character': session.get('character'),
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
        # Per-chat persona {name,scope,display_name} or None — survives restart
        # so the header pill + conversation marker render on reload.
        'character': session.get('character'),
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
    user_label = state.CONFIG.get('user_name') or 'User'
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
    if not state.CONFIG.get('auto_model_enabled', False):
        return fallback_model, 'manual'
    if not prompt or not prompt.strip():
        return fallback_model, 'fallback'
    classifier_model = state.CONFIG.get('auto_model_classifier_model', '') or 'haiku'
    try:
        raw = _scribe_call(classifier_model, _AUTO_MODEL_CLASSIFIER_PROMPT, prompt.strip())
    except Exception:
        return fallback_model, 'fallback'
    token = (raw or '').strip().upper()[:1]
    chosen = _AUTO_MODEL_VALID.get(token)
    if not chosen:
        return fallback_model, 'fallback'
    return chosen, 'auto'

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
    if state.CONFIG.get('brief_replies_always_enabled'):
        # When sticky_agent_settings is on, this directive is baked into the
        # spawn-time system prompt (_build_agent_context) — don't also prepend
        # it per turn, or it doubles up.
        if state.CONFIG.get('sticky_agent_settings', False):
            return message
        return f"{_BRIEF_REPLY_DIRECTIVE_ALWAYS}\n\n{message}"
    if not state.CONFIG.get('mobile_brief_replies_enabled'):
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
                          display_task=None, character_meta=None,
                          character_body=''):
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
        user_label = state.CONFIG.get('user_name') or 'User'
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
            'agent_model': p.get('agent_model', '') or state.CONFIG.get('agent_model', ''),
            'character': character_meta,
        }
        agent_sessions[session_id] = session
        mgr.session_ids.add(session_id)

    # Build system_prompt blob (MEMORY/AGENT_RULES). Skip when incognito.
    system_prompt = ''
    try:
        if not incognito:
            system_prompt = _build_agent_context(p, incognito=False, task=task,
                                                 character_body=character_body)
    except Exception as e:
        _log(f"[runtime-dispatch] context build failed: {e}")

    try:
        handle = runtime.dispatch(
            project_path=pp,
            task=task,
            system_prompt=system_prompt,
            resume_id='',
            mode='A',
            model=p.get('agent_model', '') or state.CONFIG.get('agent_model', ''),
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


def _resolve_character(pp, character):
    """Resolve a new-chat character selection to (meta, body).

    `character` is a "scope:name" string from the new-chat picker
    (e.g. "project:code-reviewer", "global:docs-writer"); empty/None = none.
    Best-effort: an unknown/invalid value yields (None, '') so a stale pick
    never blocks dispatch. meta = {'name','scope','display_name'} for the
    session record + header pill.
    """
    if not character or not isinstance(character, str):
        return None, ''
    scope, _, name = character.partition(':')
    scope = (scope or '').strip().lower()
    name = (name or '').strip()
    if scope not in ('project', 'global') or not name:
        return None, ''
    try:
        from mc import characters as _characters
        rec = _characters.read_character(
            scope, name, project_path=(pp if scope == 'project' else None),
            include_body=True)
    except Exception as e:
        _log(f"[dispatch] character resolve failed for {character!r}: {e}")
        return None, ''
    if not rec:
        return None, ''
    meta = {'name': rec.get('name') or name, 'scope': scope,
            'display_name': rec.get('display_name') or rec.get('name') or name}
    return meta, (rec.get('body') or '')


def _dispatch_agent_internal(project_id, task, resume_id='', incognito=False,
                             trigger_type='manual', trigger_id='',
                             reuse_session_id='', provider_override='',
                             display_task=None, character=''):
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

    # Resolve the per-chat character (persona) now, at spawn — the only point
    # a system prompt can be set (claude -r restores the original). Immutable
    # for this chat's lifetime; switching personas = a new chat.
    character_meta, character_body = _resolve_character(pp, character)

    # ── Multi-provider routing ──────────────────────────────────────────────
    # If the conversation selects a non-claude provider, dispatch through the
    # AgentRuntime abstraction instead of the legacy claude path. Provider is
    # bound per-conversation: `provider_override` (chosen in the new-chat
    # composer) wins, then the project's default seed, then the global default.
    # Default behavior (all unset OR claude) is unchanged.
    provider_name = (provider_override or p.get('provider')
                     or state.CONFIG.get('default_provider') or 'claude').lower()
    if provider_name != 'claude':
        try:
            return _dispatch_via_runtime(p, task, provider_name=provider_name,
                                         incognito=incognito,
                                         trigger_type=trigger_type,
                                         trigger_id=trigger_id,
                                         reuse_session_id=reuse_session_id,
                                         display_task=display_task,
                                         character_meta=character_meta,
                                         character_body=character_body)
        except Exception as e:
            _log(f"[dispatch] runtime '{provider_name}' failed, no fallback: {e}")
            raise

    use_streaming = p.get('use_streaming_agent', state.CONFIG.get('use_streaming_agent', False))

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
    user_label = state.CONFIG.get('user_name') or 'User'
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
                    p, incognito=incognito, task=task,
                    character_body=character_body),
                streaming=use_streaming))
        _sp_args, _sp_path = _sysprompt_file_args(context)
    # Per-dispatch telemetry — best-effort; never raises. requested = the
    # model the user configured; chosen = what actually went to --model
    # (post-router). See docs/DISPATCH_AND_ROUTING_ANALYSIS.md §B.5.
    _router_stat(
        project_id,
        requested_model=(p.get('agent_model', '') or state.CONFIG.get('agent_model', '') or 'sonnet'),
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
                'agent_model': p.get('agent_model', '') or state.CONFIG.get('agent_model', ''),
                # Auto-router attribution — `model` is what actually got
                # passed via --model (after override); `model_source` is
                # 'manual' / 'auto' / 'fallback'. Frontend pill reads these.
                'model': routed_model,
                'model_source': routed_source,
                # Per-chat persona (Prompt Builder Phase 2): {name,scope,
                # display_name} or None. Immutable; drives the header pill.
                'character': character_meta,
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
                    _proc.stdin.write(_msg)  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.12)
                    _proc.stdin.flush()  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.12)
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
                'agent_model': p.get('agent_model', '') or state.CONFIG.get('agent_model', ''),
                'model': routed_model,
                'model_source': routed_source,
                # Per-chat persona (Prompt Builder Phase 2): {name,scope,
                # display_name} or None. Immutable; drives the header pill.
                'character': character_meta,
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

@bp.route('/api/project/<project_id>/agent/dispatch', methods=['POST'])
def agent_dispatch(project_id):
    data = request.get_json() or {}
    task = data.get('task', '').strip()
    if not task:
        return jsonify({'error': 'task required'}), 400
    resume_id = data.get('resume_conversation_id', '').strip()
    incognito = bool(data.get('incognito'))
    provider_override = (data.get('provider') or '').strip().lower()
    # Per-chat character/persona ("scope:name", e.g. "project:code-reviewer").
    # Only meaningful on a FRESH chat — a resume keeps the original spawn's
    # persona (claude -r can't change the system prompt), so ignore it there.
    character = (data.get('character') or '').strip() if not resume_id else ''
    # Mobile brief replies: augmented version goes to the agent. The frontend's
    # local echo already shows the original task as the user's chat bubble.
    claude_task = _apply_mobile_brief(task, data)
    try:
        session_id = _dispatch_agent_internal(project_id, claude_task, resume_id,
                                              incognito=incognito,
                                              provider_override=provider_override,
                                              display_task=task, character=character)
    except ValueError as e:
        code = 404 if 'not found' in str(e) else 400
        return jsonify({'error': str(e)}), code
    except FileNotFoundError:
        return jsonify({'error': 'Claude CLI not found. Install it with: npm install -g @anthropic-ai/claude-code'}), 500
    except Exception as e:
        return jsonify({'error': f'dispatch failed: {e}'}), 500
    return jsonify({'ok': True, 'session_id': session_id})


@bp.route('/api/project/<project_id>/agent/send', methods=['POST'])
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
            user_label = state.CONFIG.get('user_name') or 'User'
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
        if resp.status_code == 400:  # pyright: ignore[reportAttributeAccessIssue]  # moved-verbatim typing debt (1.12)
            try:
                body = resp.get_json(silent=True) or {}  # pyright: ignore[reportAttributeAccessIssue]  # moved-verbatim typing debt (1.12)
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
        body = resp.get_json(silent=True) or {}  # pyright: ignore[reportAttributeAccessIssue]  # moved-verbatim typing debt (1.12)
        if isinstance(body, dict):
            body.setdefault('route', decision)
            return jsonify(body), resp.status_code  # pyright: ignore[reportAttributeAccessIssue]  # moved-verbatim typing debt (1.12)
    except Exception:
        pass
    return resp


@bp.route('/api/project/<project_id>/agent/stream')
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
            # Cursor-overshoot guard: `sent` can exceed len(lines) whenever
            # log_lines was REBUILT shorter under the same session_id —
            # revive-from-agent-log reseeds from the transcript (40 msgs, no
            # tool lines) after a restart/purge, and the 2000→1500 cap slams
            # the array. Without this, `sent < len` never fires again: the
            # stream serves heartbeats forever while the chat looks frozen
            # even in focus (a healthy-looking stream disarms every client
            # recovery path, and the reconcile poll no-ops on the same
            # comparison). Tell the client to drop its buffer, then replay.
            if sent > len(lines):
                yield f"data: {json.dumps({'type': 'reset'})}\n\n"
                sent = 0
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


@bp.route('/api/project/<project_id>/agent/followup', methods=['POST'])
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
            user_label = state.CONFIG.get('user_name') or 'User'
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
                        '[Resume produced no output before exiting — restarting fresh]')
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

                user_label = state.CONFIG.get('user_name') or 'User'
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
                user_label = state.CONFIG.get('user_name') or 'User'
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
                if (state.CONFIG.get('sticky_agent_settings', False)
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
                elif state.CONFIG.get('auto_model_enabled', False) and message:
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
                user_label = state.CONFIG.get('user_name') or 'User'
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
            user_label = state.CONFIG.get('user_name') or 'User'
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
                    proc.stdin.write(stdin_msg)  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.12)
                    proc.stdin.flush()  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.12)
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


@bp.route('/api/project/<project_id>/agent/stop', methods=['POST'])
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


@bp.route('/api/project/<project_id>/agent/interrupt', methods=['POST'])
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
            user_label = state.CONFIG.get('user_name') or 'User'
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
        user_label = state.CONFIG.get('user_name') or 'User'
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
                    proc.stdin.write(stdin_msg)  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.12)
                    proc.stdin.flush()  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.12)
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


@bp.route('/api/project/<project_id>/agent/session', methods=['DELETE', 'POST'])
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


@bp.route('/api/project/<project_id>/agent/plan-file')
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


@bp.route('/api/plan-file')
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


@bp.route('/api/plans/delete', methods=['POST'])
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


@bp.route('/api/project/<project_id>/agent/status')
def agent_status(project_id):
    sessions = []
    # Hoist project lookup + per-project default model out of the loop. Used
    # as a fallback for legacy sessions that were dispatched before
    # session['agent_model'] was captured per-dispatch.
    _proj_default_model = ((load_project(project_id) or {}).get('agent_model')
                           or state.CONFIG.get('agent_model') or '')
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
                # Per-chat persona {name,scope,display_name} or None → header pill.
                'character': s.get('character'),
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


@bp.route('/api/project/<project_id>/agent/guardian-reset', methods=['POST'])
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


@bp.route('/api/project/<project_id>/agent/log')
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

@bp.route('/api/project/<project_id>/transcript/<claude_session_id>')
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


@bp.route('/api/project/<project_id>/session/<session_id>/reconstruct')
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
    user_label = state.CONFIG.get('user_name') or 'User'
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


@bp.route('/api/recent-runs')
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


@bp.route('/api/project/<project_id>/search-chats')
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


@bp.route('/api/project/<project_id>/conversations')
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


@bp.route('/api/project/<project_id>/plans')
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

@bp.route('/api/usage')
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
                                  state.CONFIG.get('idle_eviction_enabled', False),
                                  state.CONFIG.get('idle_eviction_minutes', 60)):
        proc_to_kill = None
        with get_manager(session['project_id']).lock:
            # Re-check under lock — status/proc may have changed since the snapshot.
            if _should_evict_idle_session(session, now,
                                          state.CONFIG.get('idle_eviction_enabled', False),
                                          state.CONFIG.get('idle_eviction_minutes', 60)):
                idle_min = (now - session.get('last_output_time', now)) / 60
                session['evicted'] = True
                session['process_alive'] = False
                session['last_status_change_time'] = now
                session['log_lines'].append(
                    f'[Guardian: idle {idle_min:.0f} min — process evicted to free '
                    f'resources; next message resumes with full context]')
                _unregister_process(proc.pid)  # pyright: ignore[reportOptionalMemberAccess]  # moved-verbatim typing debt (1.12)
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
                    '[Guardian: dispatching stuck follow-up]')
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

