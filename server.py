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


# ── Claude binary resolution + pid/kill/window helpers ── moved to
# mc/blueprints/agent_routes.py (1.12). Inbound shims for the stayer call
# sites (reaper, condense, scheduler, runtime hooks) bind at the agent
# stanza below.

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
        # Live activity states (2026-07-11). ON → claude is spawned with
        # --include-partial-messages, the readers derive a transient
        # thinking/writing/tool flag from the content_block deltas, and the chat
        # shows a spinner vs. dots instead of one undifferentiated bubble.
        # OFF (default) → no flag, no stream_event, no `activity` SSE event, and
        # the UI falls back to the plain typing dots. Experimental; flip off to
        # revert completely (no persisted state, no transcript impact).
        'activity_states_enabled': False,
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
        except Exception as e:
            # Don't silently run on defaults — a malformed config.json (e.g. a
            # half-written file after a crash) would otherwise revert every
            # operator-set flag with zero indication. `_log` isn't imported yet
            # at module-load time, so write straight to stderr.
            print(f"[config] failed to read {CONFIG_PATH}; running on defaults: {e}",
                  file=sys.stderr, flush=True)
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

# ── Global incognito pseudo-project ── moved to mc/blueprints/agent_routes.py
# (1.12); INCOGNITO_PROJECT_ID + _ensure_incognito_project resolve through the
# inbound shims at the agent stanza below (startup + backfill/reconcile readers).


# ── Memory / Scribe / Condense engine ── extracted VERBATIM to mc/memory.py
# (mop-up, no behavior change). server.py keeps inbound shims (memory.X) for
# the names its startup backfills / runtime hooks / blueprint wire() sites and
# the tests still reference; the agent_routes/project_routes/guide_routes wire()
# stanzas now source the memory values from memory.*. The load-bearing
# leaf-lock+atomic MEMORY.md write discipline lives wholly in mc/memory.py now.


# ── DEFAULT_DOMAINS + settings.json store (_load_settings / _save_settings) ──
# extracted to mc/blueprints/settings_routes.py (1.14) with the 10
# settings/config/browse routes. SETTINGS_PATH (above) stays home, wired in.


# _load_schedules / _save_schedules (schedules.json store) -- moved to
# mc/blueprints/scheduler_routes.py (1.13).


# ── MCP catalog + per-project trim resolution ── moved to
# mc/blueprints/agent_routes.py (1.12); the mcp_routes wire() below re-homes
# its mcp_server_catalog_fn slot onto _bp_agent.


# ── ProjectAgentManager + get_manager + per-project guardian loop ── moved to
# mc/blueprints/agent_routes.py (1.12).

# ── Memory condensation state ────────────────────────────────────────────────
# _condensing_projects / _condense_lock / _condense_triggered_at /
# _condense_status moved to mc/state.py (Phase 0).


# ── Terminal session tracking + process tracker ──────────────────────────────
# terminal_sessions / terminal_lock / tracked_processes /
# process_tracker_lock moved to mc/state.py (Phase 0).


# _register_process / _unregister_process ── moved to
# mc/blueprints/agent_routes.py (1.12).


# ── MC-spawned child PID ledger + startup orphan reaper ── extracted VERBATIM
# to mc/process_ledger.py (mop-up, the mc/memory.py non-blueprint pattern).
# _PID_LEDGER_PATH (data/ const) stays home + wired in (1.7 placeholder); the
# reaper's _pid_is_alive/_kill_pid (agent_routes 1.12) wire in too. wire() +
# import live at the dispatch stanza below; __main__ calls
# process_ledger._reap_prior_instance_strays().
_PID_LEDGER_PATH = _DATA_ROOT / 'data' / 'mc_child_pids.json'


# ── Project-record store + project/backlog/github/code-sync/attachment/
# rules/memory-editor/order endpoints ── extracted to
# mc/blueprints/project_routes.py (1.11): the CRUD core (load_project /
# save_project / load_projects with the LOAD-BEARING EXCLUDED_SIDECAR_SUFFIXES
# exclusion / update_project / delete_project), _project_live_agent,
# _log_agent_activity (project-record activity_log writer), backlog CRUD +
# _append_note_to_backlog_item, github + code-sync glue (the blueprint imports
# github_sync/project_sync directly; their register() wiring stays below,
# unchanged), attachments + serve-image + the upload-quota helpers, import,
# rules, the memory editor-CRUD trio (the locked managed-region writers
# _commit_managed_entry/_condense_apply live in mc/memory.py — mop-up), and
# projects/order + grid-layout. wire() late-binds the path constants (DATA_DIR
# & co. stay here — other families still read them) and the cross-family fns
# (_get_memory_path → memory.* via the mop-up shim; _resolve_claude,
# get_manager, _unregister_process, Popen consts → dispatch, 1.12). This stanza
# sits ABOVE the other blueprints' wire() sites so they can re-home their
# projects-family slots to _bp_projects.*.
from mc.blueprints import project_routes as _bp_projects  # noqa: E402

# Agent dispatch family module (1.12) — IMPORTED here (its defs feed the
# dispatch-family slots of this and the following wire() stanzas: 1.10/1.11
# passed server.py fns that now live in the blueprint), but its own wire() +
# register_blueprint sit further down at the dispatch tombstone, AFTER the
# memory/scribe/condense stayers it late-binds are defined. Import order:
# agent_routes itself cross-imports project_routes/push_mobile/system_routes
# defs at import time (request/stream-time calls only — safe before wire()).
from mc.blueprints import agent_routes as _bp_agent  # noqa: E402

# ── MC-spawned child PID ledger (mop-up: mc/process_ledger.py) ───────────────
# Imported here (its _proc_identity/_persist_pid_ledger feed the _bp_agent.wire()
# slots below); process_ledger.wire() runs after that stanza, once _bp_agent's
# _pid_is_alive/_kill_pid exist. Leaf module (mc.state/mc.core only — no cycle).
from mc import process_ledger  # noqa: E402

# ── Memory / Scribe / Condense engine (mop-up: mc/memory.py) ─────────────────
# The engine was extracted VERBATIM to mc/memory.py (no behavior change). Its
# wire() runs HERE — after _bp_projects + _bp_agent are imported (so it can pass
# their dispatch-family fns) and BEFORE the blueprint wire() stanzas below that
# source memory.* values (projects' get_memory_path_fn, guide's memory_search_fn,
# agent's write_session_memory_fn/scribe_call_fn/dispatch_condense_fn/...).
# Path/config roots stay home in server.py; the 6 dispatch-family fns live in
# agent_routes/project_routes. CONFIG is read live via state.CONFIG (not wired).
from mc import memory  # noqa: E402

memory.wire(
    data_dir=DATA_DIR,
    memory_dir=MEMORY_DIR,
    claude_home=CLAUDE_HOME,
    session_size_limit=_SESSION_SIZE_LIMIT,
    popen_flags=_POPEN_FLAGS,
    startupinfo=_STARTUPINFO,
    load_project_fn=_bp_projects.load_project,
    get_manager_fn=_bp_agent.get_manager,
    resolve_claude_fn=_bp_agent._resolve_claude,
    register_process_fn=_bp_agent._register_process,
    read_agent_stream_fn=_bp_agent._read_agent_stream,
    hide_windows_delayed_fn=_bp_agent._hide_windows_delayed,
)
# Inbound shims — startup backfills / runtime hooks / the blueprint wire() sites
# below and the tests read these off server.<name>; all call through to the
# engine in mc/memory.py (the mop-up sibling of the 1.11/1.12 shim blocks).
_encode_project_path = memory._encode_project_path
_session_transcript_path = memory._session_transcript_path
_session_too_large = memory._session_too_large
_long_session_advisory = memory._long_session_advisory
_resume_is_fragile = memory._resume_is_fragile
_extract_user_text = memory._extract_user_text
_recent_claude_transcripts = memory._recent_claude_transcripts
_find_transcript_file = memory._find_transcript_file
_parse_transcript_messages = memory._parse_transcript_messages
_native_memory_path = memory._native_memory_path
_get_memory_path = memory._get_memory_path
_get_archive_path = memory._get_archive_path
_mem_split_full = memory._mem_split_full
_mem_split = memory._mem_split
_mem_compose = memory._mem_compose
_mem_migrate = memory._mem_migrate
_wm_line = memory._wm_line
_wm_parse = memory._wm_parse
_wm_find = memory._wm_find
_wm_upsert = memory._wm_upsert
_wm_remove = memory._wm_remove
_memory_search = memory._memory_search
_condense_combined_bytes = memory._condense_combined_bytes
_set_condense_status = memory._set_condense_status
_get_condense_status = memory._get_condense_status
_has_running_agent = memory._has_running_agent
_should_condense = memory._should_condense
_append_to_archive = memory._append_to_archive
_commit_managed_entry = memory._commit_managed_entry
_write_session_memory = memory._write_session_memory
_sha8 = memory._sha8
_get_checkpoint_sema = memory._get_checkpoint_sema
_checkpoint_prev_offset = memory._checkpoint_prev_offset
_maybe_checkpoint = memory._maybe_checkpoint
_checkpoint_worker = memory._checkpoint_worker
_scribe_stat = memory._scribe_stat
_scribe_render_lines = memory._scribe_render_lines
_scribe_render_transcript = memory._scribe_render_transcript
_scribe_render_delta = memory._scribe_render_delta
_scribe_call = memory._scribe_call
_extract_transcript_telemetry = memory._extract_transcript_telemetry
_scribe_extract = memory._scribe_extract
_scribe_summarize_text = memory._scribe_summarize_text
_condense_integrity_check = memory._condense_integrity_check
_condense_parse_json = memory._condense_parse_json
_validate_condense_payload = memory._validate_condense_payload
_condense_plan = memory._condense_plan
_condense_apply = memory._condense_apply
_run_structured_condense = memory._run_structured_condense
_dispatch_condense = memory._dispatch_condense

_bp_projects.wire(
    data_dir=DATA_DIR,
    data_root=_DATA_ROOT,
    uploads_dir=UPLOADS_DIR,
    projects_base=PROJECTS_BASE,
    shared_rules_path=SHARED_RULES_PATH,
    get_memory_path_fn=memory._get_memory_path,
    resolve_claude_fn=_bp_agent._resolve_claude,
    get_manager_fn=_bp_agent.get_manager,
    unregister_process_fn=_bp_agent._unregister_process,
    popen_flags=_POPEN_FLAGS,
    startupinfo=_STARTUPINFO,
)
app.register_blueprint(_bp_projects.bp)
# Inbound shims — dispatch/scheduler/scribe/condense and the github/project
# sync register() calls below keep their bare names; tests read
# server.EXCLUDED_SIDECAR_SUFFIXES and server._upload_limit & co.
load_project = _bp_projects.load_project
save_project = _bp_projects.save_project
load_projects = _bp_projects.load_projects
EXCLUDED_SIDECAR_SUFFIXES = _bp_projects.EXCLUDED_SIDECAR_SUFFIXES
_log_agent_activity = _bp_projects._log_agent_activity
_upload_limit = _bp_projects._upload_limit
_incoming_file_size = _bp_projects._incoming_file_size
_project_attachment_usage = _bp_projects._project_attachment_usage


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
# read — the Scribe machinery lives in mc/memory.py now, mop-up), and
# /api/project/<id>/memory/search (read-only retrieval). wire() late-binds
# load_project/save_project (projects family, 1.11), _memory_search (sourced
# from memory.* — the read floor in agent_routes' _build_agent_context shares
# it via the same engine) + _resolve_claude + the Popen consts, DATA_DIR, and
# the server-dir anchor (Path(__file__).parent
# evaluated HERE — the 1.7/1.8 wired-placeholder pattern: data/claydo,
# docs/USER_GUIDE.md and CHANGELOG.md resolve from the repo root, not
# mc/blueprints/).
from mc.blueprints import guide_routes as _bp_guide  # noqa: E402

_bp_guide.wire(
    load_project_fn=_bp_projects.load_project,
    save_project_fn=_bp_projects.save_project,
    data_dir=DATA_DIR,
    memory_search_fn=memory._memory_search,
    resolve_claude_fn=_bp_agent._resolve_claude,
    popen_flags=_POPEN_FLAGS,
    startupinfo=_STARTUPINFO,
    server_dir=Path(__file__).parent,
)
app.register_blueprint(_bp_guide.bp)


# ── Agent characters (Prompt Builder Phase 1) ── /api/characters CRUD over
# standard Claude Code subagent files (.claude/agents/, global + project
# scope). Logic in mc/characters.py; design docs/PROMPT_BUILDER_DESIGN.md.
from mc.blueprints import character_routes as _bp_characters  # noqa: E402

_bp_characters.wire(load_project_fn=_bp_projects.load_project)
app.register_blueprint(_bp_characters.bp)


# ── Project endpoints ── moved to mc/blueprints/project_routes.py (1.11).


# ── Scribe telemetry (SPEC §8) ── /scribe-stats moved to mc/blueprints/guide_routes.py (1.9).


# ── Phase 4 Distiller endpoints ── extracted to
# mc/blueprints/distiller_routes.py (1.5). Projects-family accessor re-homed
# onto _bp_projects (1.11).
from mc.blueprints import distiller_routes as _bp_distiller  # noqa: E402

_bp_distiller.wire(load_project_fn=_bp_projects.load_project, data_dir=DATA_DIR)
app.register_blueprint(_bp_distiller.bp)


# ── Beacon: cross-project situational digest ── framework-agnostic beacon/
# package + this thin blueprint. Heartbeats persist at data/beacon/<id>.json —
# beside data/projects/ but OUTSIDE DATA_DIR itself (the DATA_DIR-pollution
# rule). NOTE: _DATA_ROOT is the app/repo root (parent of data/); the actual
# data dir is DATA_DIR.parent (= _DATA_ROOT/'data'). Passing _DATA_ROOT here
# would write into the beacon/ *package* dir — wire the data dir, not the root.
# Live state is overlaid at read time from agent_sessions via _project_live_agent.
# Brief: data/uploads/agent_0fd9f3689b.md.
from mc.blueprints import beacon_routes as _bp_beacon  # noqa: E402

_bp_beacon.wire(
    data_root=DATA_DIR.parent,
    load_projects_fn=_bp_projects.load_projects,
    load_project_fn=_bp_projects.load_project,
    live_agent_fn=_bp_projects._project_live_agent,
    get_memory_path_fn=memory._get_memory_path,
)
app.register_blueprint(_bp_beacon.bp)


# /api/router/stats ── moved to mc/blueprints/agent_routes.py (1.12).


# /api/project/<id>/memory/search ── moved to mc/blueprints/guide_routes.py (1.9).


# ── Backlog endpoints ── moved to mc/blueprints/project_routes.py (1.11).


# ── Walkthrough onboarding project ── moved to mc/blueprints/guide_routes.py (1.9).


# ── GitHub sync + code-sync + attachment + import endpoints ── moved to
# mc/blueprints/project_routes.py (1.11). github_sync/project_sync register()
# wiring stays below, unchanged.


# ── Agent image upload ── moved to mc/blueprints/agent_routes.py (1.12);
# the quota helpers stay in project_routes (cross-imported there).


# ── Claude auth tracking + provider discovery/env/auth routes + clayrune
# context feeders ── moved to mc/blueprints/agent_routes.py (1.12).


# ── Agent context builder, TodoWrite→backlog sync, BOTH stream readers ──
# moved to mc/blueprints/agent_routes.py (1.12). The readers now heartbeat as
# 'stream-reader:a'/'stream-reader:b' in /api/system/loops (Phase 2).

# _log_agent_activity ── moved to mc/blueprints/project_routes.py (1.11); the
# dispatch/github call sites below resolve the inbound shim at call time.


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


# _load_agent_log / _save_agent_log ── moved to mc/blueprints/agent_routes.py
# (1.12); the startup/scheduler stayers below resolve the inbound shims.


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


# ── Revive-from-agent-log + transcript buffer renderers ── moved to
# mc/blueprints/agent_routes.py (1.12).


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


# ── Phase 4 Distiller registration ───────────────────────────────────────────
# distiller.py is the cross-session learning observer (v2.1 spec). Registered
# here, sourcing the scribe/checkpoint primitives from mc/memory.py (the engine
# now lives there). memory.wire() ran above, so memory.* is fully usable.
# Best-effort; failure to register doesn't break the rest of server startup.
import distiller as _distiller
try:
    _SKILLS_ROOT = Path(__file__).parent / 'data' / 'skills'
    _distiller.register(
        data_root=DATA_DIR,
        skills_root=_SKILLS_ROOT,
        atomic_write_text=_atomic_write_text,
        scribe_call=memory._scribe_call,
        scribe_render_transcript=memory._scribe_render_transcript,
        log=_log,
        load_project=load_project,
        save_project=save_project,
        now_iso=now_iso,
        config_get=lambda k, d=None: CONFIG.get(k, d),
        get_per_project_semaphore=memory._get_checkpoint_sema,
    )
except Exception as _distiller_reg_err:
    _log(f"[distiller] registration failed: {_distiller_reg_err!r} — "
         f"Distiller will be inert this run")


# ── Agent dispatch family ── extracted to mc/blueprints/agent_routes.py (1.12):
# claude resolution + pid/kill/window helpers, incognito pseudo-project, MCP
# trim catalog, claude flags + auto-model router, sysprompt files,
# ProjectAgentManager + guardians, process-ledger writers, upload-image,
# provider auth/env routes + legacy /api/claude shims, context builders, BOTH
# stream readers, agent_log store + completion writers, revive, dispatch
# internals, the 11 agent/* routes (followup moved WHOLE), plan-file pair,
# transcript/reconstruct, run-history + usage. wire() late-binds the
# memory/scribe/condense seams (THE LOAD-BEARING LINE — that machinery now
# lives in mc/memory.py, the mop-up sibling; the seam values below are sourced
# from memory.*: _scribe_call, _dispatch_condense, _should_condense,
# _get_condense_status, _maybe_checkpoint, _write_session_memory, _memory_search,
# _get_memory_path/_get_archive_path, _find_transcript_file/_parse_transcript_
# messages/_recent_claude_transcripts, _session_too_large, _long_session_advisory,
# _resume_is_fragile, _encode_project_path, _extract_transcript_telemetry), plus
# the path/Popen consts that stay in server.py. The reaper-family writers
# (_proc_identity/_persist_pid_ledger) now live in mc/process_ledger.py (mop-up);
# the two slots below source them from there (process_ledger.*, imported above).
_bp_agent.wire(
    data_dir=DATA_DIR,
    uploads_dir=UPLOADS_DIR,
    app_dir=_APP_DIR,
    port=PORT,
    shared_rules_path=SHARED_RULES_PATH,
    provider_env_path=_DATA_ROOT / 'data' / 'provider_env.json',
    claude_home=CLAUDE_HOME,
    popen_flags=_POPEN_FLAGS,
    startupinfo=_STARTUPINFO,
    load_project_fn=_bp_projects.load_project,
    save_project_fn=_bp_projects.save_project,
    load_projects_fn=_bp_projects.load_projects,
    get_memory_path_fn=memory._get_memory_path,
    get_archive_path_fn=memory._get_archive_path,
    memory_search_fn=memory._memory_search,
    maybe_checkpoint_fn=memory._maybe_checkpoint,
    write_session_memory_fn=memory._write_session_memory,
    dispatch_condense_fn=memory._dispatch_condense,
    should_condense_fn=memory._should_condense,
    get_condense_status_fn=memory._get_condense_status,
    scribe_call_fn=memory._scribe_call,
    find_transcript_file_fn=memory._find_transcript_file,
    parse_transcript_messages_fn=memory._parse_transcript_messages,
    recent_claude_transcripts_fn=memory._recent_claude_transcripts,
    session_too_large_fn=memory._session_too_large,
    long_session_advisory_fn=memory._long_session_advisory,
    resume_is_fragile_fn=memory._resume_is_fragile,
    encode_project_path_fn=memory._encode_project_path,
    extract_transcript_telemetry_fn=memory._extract_transcript_telemetry,
    proc_identity_fn=process_ledger._proc_identity,
    persist_pid_ledger_fn=process_ledger._persist_pid_ledger,
)
app.register_blueprint(_bp_agent.bp)
# Inbound shims — stayer call sites keep their bare names: the reaper
# (_pid_is_alive/_kill_pid), _dispatch_condense (+hooks) spawn path
# (get_manager/_register_process/_read_agent_stream/_hide_windows_delayed/
# _resolve_claude/_build_claude_flags), the scheduler family (1.13:
# _dispatch_agent_internal/_revive_from_agent_log/all_managers/_pid_is_alive/
# get_manager/_load_agent_log/_enrich_run_entries), startup
# (_ensure_incognito_project/_start_session_guardian/INCOGNITO_PROJECT_ID +
# the agent-log backfill/reconcile readers), the atexit cleanup
# (_unregister_process), and the AgentRuntime hooks
# (_sync_todowrite_to_backlog/_read_agent_stream/_kill_pid). The second block
# exists for tests that read/patch server.<name>
# (test_auto_model_router/test_mcp_trim/test_telemetry/test_idle_eviction/
# test_sysprompt_file/test_stream_reader_malformed/test_smoke — the
# _project_attachment_usage precedent).
_resolve_claude = _bp_agent._resolve_claude
_pid_is_alive = _bp_agent._pid_is_alive
_kill_pid = _bp_agent._kill_pid
_hide_windows_delayed = _bp_agent._hide_windows_delayed
get_manager = _bp_agent.get_manager
all_managers = _bp_agent.all_managers
_register_process = _bp_agent._register_process
_unregister_process = _bp_agent._unregister_process
_read_agent_stream = _bp_agent._read_agent_stream
_read_agent_stream_b = _bp_agent._read_agent_stream_b
_build_claude_flags = _bp_agent._build_claude_flags
_sync_todowrite_to_backlog = _bp_agent._sync_todowrite_to_backlog
_dispatch_agent_internal = _bp_agent._dispatch_agent_internal
_revive_from_agent_log = _bp_agent._revive_from_agent_log
_load_agent_log = _bp_agent._load_agent_log
_save_agent_log = _bp_agent._save_agent_log
_enrich_run_entries = _bp_agent._enrich_run_entries
INCOGNITO_PROJECT_ID = _bp_agent.INCOGNITO_PROJECT_ID
_ensure_incognito_project = _bp_agent._ensure_incognito_project
_start_session_guardian = _bp_agent._start_session_guardian
# test-compat shims (calls flow through; patches belong on the blueprint):
_session_usage_payload = _bp_agent._session_usage_payload
_sysprompt_file_args = _bp_agent._sysprompt_file_args
_sysprompt_cleanup = _bp_agent._sysprompt_cleanup
_mcp_server_catalog = _bp_agent._mcp_server_catalog
_resolve_project_mcp_config = _bp_agent._resolve_project_mcp_config
_ENGRAM_MCP_SPEC = _bp_agent._ENGRAM_MCP_SPEC
_skills_catalog_block = _bp_agent._skills_catalog_block
_should_evict_idle_session = _bp_agent._should_evict_idle_session
_route_dispatch_model = _bp_agent._route_dispatch_model
_resolve_dispatch_model = _bp_agent._resolve_dispatch_model
_router_stat = _bp_agent._router_stat
_AUTO_MODEL_VALID = _bp_agent._AUTO_MODEL_VALID

# ── PID-ledger reaper wiring (mop-up: mc/process_ledger.py) ──────────────────
# wire() runs HERE — after _bp_agent's _pid_is_alive/_kill_pid are bound above
# (the reaper calls them). No inbound shims: every caller uses process_ledger.*
# directly (the wire slots + the __main__ reaper call); the test patches
# mc.process_ledger.* (the test-port rule).
process_ledger.wire(
    pid_ledger_path=_PID_LEDGER_PATH,
    pid_is_alive_fn=_bp_agent._pid_is_alive,
    kill_pid_fn=_bp_agent._kill_pid,
)


# ── Agent endpoints (dispatch/send/stream/followup/stop/interrupt/session/
# plan-file/status/guardian-reset) ── moved to mc/blueprints/agent_routes.py
# (1.12). agent_followup moved WHOLE (492 lines, per plan).
# ── Terminal session management ── moved to mc/blueprints/terminal_routes.py (1.8).


# ── Process Tracker endpoints ── moved to mc/blueprints/system_routes.py (1.6).

# ── Hivemind ── moved to mc/blueprints/hivemind_routes.py (1.10).


# ── Agent log + transcript/reconstruct routes ── moved to
# mc/blueprints/agent_routes.py (1.12).

# ## -- Scheduled-run history (run-now + runs) -- moved to
# mc/blueprints/scheduler_routes.py (1.13).


# ── Hivemind: persistent multi-agent collaboration ── extracted to
# mc/blueprints/hivemind_routes.py (1.10): all 28 /api/hivemind* routes (27
# from the main region above + /api/hivemind/<id>/runs from this run-history
# section — moved with its family), the _hm_* data layer, worker context
# builder + spawn, orchestrator CLI dispatch, message bus/SSE, knowledge
# base, and the background orchestrator loop (now heartbeating as
# 'hivemind-orchestrator' in /api/system/loops — Phase 2). wire() late-binds
# load_project (projects family, 1.11), the dispatch-family helpers
# (get_manager, _register_process, _read_agent_stream, _resolve_claude,
# _sysprompt_file_args/_cleanup, _hide_windows_delayed — re-homed onto
# _bp_agent at 1.12), the agent-log/run-history readers (_load_agent_log,
# _enrich_run_entries — also _bp_agent now), the _clayrune_* context feeders
# (shared with _build_agent_context — _bp_agent),
# PORT, the Popen platform consts, and the _DATA_ROOT-derived hivemind dir
# (its module-level .mkdir moved into wire()). The stanza sits HERE, not at
# the main region tombstone (a pre-1.12 placement the agent extraction kept —
# the 1.2 import-order lesson).
from mc.blueprints import hivemind_routes as _bp_hivemind  # noqa: E402

_bp_hivemind.wire(
    hivemind_dir=_DATA_ROOT / 'data' / 'hiveminds',
    port=PORT,
    load_project_fn=_bp_projects.load_project,
    get_manager_fn=_bp_agent.get_manager,
    register_process_fn=_bp_agent._register_process,
    read_agent_stream_fn=_bp_agent._read_agent_stream,
    resolve_claude_fn=_bp_agent._resolve_claude,
    sysprompt_file_args_fn=_bp_agent._sysprompt_file_args,
    sysprompt_cleanup_fn=_bp_agent._sysprompt_cleanup,
    hide_windows_delayed_fn=_bp_agent._hide_windows_delayed,
    log_agent_activity_fn=_bp_projects._log_agent_activity,
    load_agent_log_fn=_bp_agent._load_agent_log,
    enrich_run_entries_fn=_bp_agent._enrich_run_entries,
    clayrune_universal_capabilities_fn=_bp_agent._clayrune_universal_capabilities,
    clayrune_api_reference_fn=_bp_agent._clayrune_api_reference,
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


# ── Run history (recent-runs/search-chats/conversations/plans/usage) ──
# moved to mc/blueprints/agent_routes.py (1.12).


# ── Rules + memory editor-CRUD endpoints ── moved to mc/blueprints/project_routes.py (1.11).


# ── Skills endpoints ── extracted to mc/blueprints/skills_routes.py (1.3).
# (Module named skills_routes, not skills — the top-level skills.py owns the
# logic and the name.) Projects-family accessors re-homed onto _bp_projects (1.11).
from mc.blueprints import skills_routes as _bp_skills  # noqa: E402

_bp_skills.wire(load_project_fn=_bp_projects.load_project,
                load_projects_fn=_bp_projects.load_projects,
                app_dir=_APP_DIR)
app.register_blueprint(_bp_skills.bp)
# Inbound shims (startup installers + the shared request helper used by the
# MCP/distiller sections until they extract):
_install_builtin_skills = _bp_skills._install_builtin_skills
_install_builtin_mcps = _bp_skills._install_builtin_mcps
_resolve_project_path_or_400 = _bp_skills._resolve_project_path_or_400

# ── MCP endpoints (server mgmt + per-project loadout + URL installer) ──
# extracted to mc/blueprints/mcp_routes.py (1.4). Projects-family accessors
# re-homed onto _bp_projects (1.11).
from mc.blueprints import mcp_routes as _bp_mcp  # noqa: E402

_bp_mcp.wire(load_project_fn=_bp_projects.load_project,
             save_project_fn=_bp_projects.save_project,
             data_dir=DATA_DIR,
             mcp_server_catalog_fn=_bp_agent._mcp_server_catalog)
app.register_blueprint(_bp_mcp.bp)

# ── Global config + folder-browse + domain settings endpoints (10 routes:
# /api/config GET+PUT, /api/browse/folders + create_folder, the 4
# /api/settings/domains, and below the project-order tombstone
# /api/list-directory + /api/create-folder) ── extracted to
# mc/blueprints/settings_routes.py (1.14, the final app-level API blueprint).
# wire() late-binds CONFIG_PATH + PROJECTS_BASE (both STAY home — _load_config +
# project_routes.wire() read them) + SETTINGS_PATH (placeholder). CONFIG is
# read+mutated live via state.CONFIG (same dict).
from mc.blueprints import settings_routes as _bp_settings  # noqa: E402

_bp_settings.wire(
    config_path=CONFIG_PATH,
    projects_base=PROJECTS_BASE,
    settings_path=SETTINGS_PATH,
)
app.register_blueprint(_bp_settings.bp)
# Inbound shim: test_p2_3_log_shim reads server._CONFIG_EDITABLE_KEYS off the
# module (the _project_attachment_usage precedent; handlers run on the blueprint).
_CONFIG_EDITABLE_KEYS = _bp_settings._CONFIG_EDITABLE_KEYS


# ── Project order + grid layout ── moved to mc/blueprints/project_routes.py (1.11).


# /api/list-directory + /api/create-folder ── moved to
# mc/blueprints/settings_routes.py (1.14) with the rest of the settings family.


# ── Scheduled Tasks ── moved to mc/blueprints/scheduler_routes.py (1.13):
# the cron parser + _compute_next_run + the background _scheduler_loop
# (which also drives GitHub auto-sync, code-sync auto-fetch, stale-session
# purge, and the process-tracker liveness sweep -- all moved verbatim) +
# _start_scheduler (daemon thread 'scheduler', start-once) + the 6 schedule
# routes. Phase 2: the loop now heartbeats as 'scheduler' in
# /api/system/loops. _scheduler_stop stays in mc/state.py (Phase 0); its
# atexit.register stays in server.py (LIFO exit-hook ordering).
from mc.blueprints import scheduler_routes as _bp_sched  # noqa: E402
_bp_sched.wire(
    schedules_path=SCHEDULES_PATH,
    load_project_fn=_bp_projects.load_project,
    load_projects_fn=_bp_projects.load_projects,
    log_agent_activity_fn=_bp_projects._log_agent_activity,
    dispatch_agent_internal_fn=_bp_agent._dispatch_agent_internal,
    load_agent_log_fn=_bp_agent._load_agent_log,
    enrich_run_entries_fn=_bp_agent._enrich_run_entries,
    get_manager_fn=_bp_agent.get_manager,
    all_managers_fn=_bp_agent.all_managers,
    pid_is_alive_fn=_bp_agent._pid_is_alive,
    revive_from_agent_log_fn=_bp_agent._revive_from_agent_log,
)
app.register_blueprint(_bp_sched.bp)


# Scheduler continuation helpers (_latest_*_for_schedule,
# _scheduled_run_marker, _scheduled_continue) + the /api/schedules CRUD
# routes -- moved to mc/blueprints/scheduler_routes.py (1.13).


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


def _asset_version():
    """Cache-bust token = newest mtime across the served static css/js + the
    index itself. Any change to those files changes the token, so (a) index.html
    revalidates via its ETag and (b) its rewritten asset URLs get a fresh ?v=,
    forcing WebViews / the service worker to re-fetch the updated CSS/JS instead
    of serving a stale cached copy (app.css/JS are otherwise linked without a
    cache-bust)."""
    latest = 0.0
    for sub in ('css', 'js'):
        d = Path(STATIC_DIR) / sub
        if not d.exists():
            continue
        for f in d.rglob('*'):
            try:
                m = f.stat().st_mtime
                if m > latest:
                    latest = m
            except OSError:
                continue
    try:
        m = (Path(STATIC_DIR) / 'index.html').stat().st_mtime
        if m > latest:
            latest = m
    except OSError:
        pass
    return str(int(latest))


@app.route('/')
def index():
    index_path = Path(STATIC_DIR) / 'index.html'
    ver = _asset_version()
    etag = f'"{ver}"'
    # Conditional GET — let WebView2 cache but always revalidate. The ETag folds
    # in the asset version so a CSS/JS-only change still invalidates index.html.
    if request.headers.get('If-None-Match') == etag:
        return Response(status=304, headers={'ETag': etag, 'Cache-Control': 'no-cache'})
    try:
        html = index_path.read_text(encoding='utf-8')
    except OSError:
        return send_from_directory(STATIC_DIR, 'index.html')
    # Append ?v=<ver> to every /static/*.css|js reference so each deploy busts
    # the client cache automatically (no manual version bumps, no per-tag edits).
    import re
    html = re.sub(r'(src|href)="(/static/[^"?]+\.(?:js|css))"',
                  rf'\1="\2?v={ver}"', html)
    resp = Response(html, mimetype='text/html')
    # no-STORE (not just no-cache): some Android WebViews keep serving a cached
    # index.html from their disk cache without revalidating even under no-cache,
    # which pins the whole SPA to a stale ?v= (a deploy never reaches the app —
    # a force-stop kills the process but not the disk cache). no-store forbids
    # caching index.html at all, so every load fetches fresh HTML → fresh ?v= →
    # fresh JS/CSS. The JS/CSS themselves stay no-cache (ETag-revalidated, 304
    # when unchanged) so only the tiny HTML pays the always-fetch cost.
    resp.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    resp.headers['Pragma'] = 'no-cache'
    resp.headers['ETag'] = etag
    return resp


@app.route('/api/version')
def api_version():
    """Deploy token (newest static-asset mtime) — same value used for the index
    ETag + the ?v= cache-bust. The frontend version-watcher polls this and
    prompts a reload when it changes, so a deploy reaches an already-open
    WebView/tab that would otherwise keep running stale JS."""
    return jsonify({'version': _asset_version()})


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

# ── Session Guardian check family ── moved to mc/blueprints/agent_routes.py
# (1.12). atexit.register(_guardian_stop.set) stays below (1.8 LIFO lesson).


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
        "    1. Stop the other MC first, or",
        "    2. Use the already-running instance directly, or",
        "    3. Set MC_ALLOW_PORT_CONFLICT=1 if you really need both",
        "       (rare; only meaningful for protocol-level testing).",
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
        _log("[port-conflict] MC_ALLOW_PORT_CONFLICT=1 set — proceeding ANYWAY. "
              "You will likely see traffic split between instances.", flush=True)
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
# load_project (re-homed onto _bp_projects at 1.11) + the remote-family fns
# (re-homed onto _bp_remote at 1.7). The _handle_push_signal inbound shim
# re-homed at 1.12: the stream readers moved to agent_routes, which
# cross-imports it directly; no server.py caller remains.
from mc.blueprints import push_mobile as _bp_push_mobile  # noqa: E402

_bp_push_mobile.wire(
    data_root=_DATA_ROOT,
    load_project_fn=_bp_projects.load_project,
    cf_session_nonce_fn=_bp_remote._cf_session_nonce_from_request,
    get_remote_provider_fn=_bp_remote._get_remote_provider,
)
app.register_blueprint(_bp_push_mobile.bp)


# ── Autonomous Steward ── framework-agnostic steward/ package + thin blueprint.
# Wired AFTER push_mobile (needs _notify_push) and projects (needs the backlog
# note writer). State (fence-settings) persists under data/steward/ — OUTSIDE
# DATA_DIR (the pollution rule), so pass DATA_DIR.parent. Scope:
# docs/AUTONOMOUS_STEWARD_SCOPE.md. Reversibility fence: steward/fence.py.
from mc.blueprints import steward_routes as _bp_steward  # noqa: E402

_bp_steward.wire(
    data_root=DATA_DIR.parent,
    load_project_fn=_bp_projects.load_project,
    save_project_fn=_bp_projects.save_project,
    load_projects_fn=_bp_projects.load_projects,
    append_note_fn=_bp_projects._append_note_to_backlog_item,
    notify_push_fn=(lambda pid, kind, title, body:
                    _bp_push_mobile._notify_push(title, body, project_id=pid,
                                                 kind='agent')),
    log_fn=lambda m: _log(m, flush=True),
)
app.register_blueprint(_bp_steward.bp)


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

_bp_system.wire(load_project_fn=_bp_projects.load_project,
                load_projects_fn=_bp_projects.load_projects,
                data_dir=DATA_DIR, data_root=_DATA_ROOT, app_dir=_APP_DIR,
                popen_flags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
                backfill_token_telemetry_fn=_backfill_token_telemetry,
                is_cf_tunneled_request_fn=_bp_remote._is_cf_tunneled_request,
                kill_pid_fn=_bp_agent._kill_pid,
                kill_proc_background_fn=_bp_agent._kill_proc_background,
                pid_is_alive_fn=_bp_agent._pid_is_alive,
                resolve_claude_fn=_bp_agent._resolve_claude,
                stop_session_fn=_bp_agent._stop_session,
                get_manager_fn=_bp_agent.get_manager,
                get_manager_for_session_fn=_bp_agent.get_manager_for_session,
)
app.register_blueprint(_bp_system.bp)
# Inbound shim: startup starts the update-check daemon (re-homes at 1.13).
# The _capture_system_init shim re-homed at 1.12: the stream readers moved to
# agent_routes, which cross-imports it directly; no server.py caller remains.
_update_check_loop = _bp_system._update_check_loop

# ── Terminal session endpoints ── extracted to
# mc/blueprints/terminal_routes.py (1.8): the 5 /api/terminal/* routes +
# /api/project/<id>/terminal/status, the reader/kill helpers, and the
# TTY-shim env wiring. wire() late-binds load_project (re-homed onto
# _bp_projects at 1.11), get_manager + _register_process/_unregister_process
# (dispatch family — re-homed onto _bp_agent at 1.12), the Popen platform
# consts, and the _APP_DIR-derived shim dir (the 1.7 wired-placeholder
# pattern).
from mc.blueprints import terminal_routes as _bp_terminal  # noqa: E402

_bp_terminal.wire(
    load_project_fn=_bp_projects.load_project,
    get_manager_fn=_bp_agent.get_manager,
    register_process_fn=_bp_agent._register_process,
    unregister_process_fn=_bp_agent._unregister_process,
    popen_flags=_POPEN_FLAGS,
    startupinfo=_STARTUPINFO,
    tty_shim_dir=str(_APP_DIR / 'mc_tty_shim'),
)
app.register_blueprint(_bp_terminal.bp)
# Inbound shim: the atexit _cleanup_terminals hook kills terminal sessions —
# the call site stays; the global resolves at call time, after this binding
# exists. (delete_project moved to project_routes at 1.11 and imports
# _kill_terminal_session cross-blueprint instead.)
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
    # Derive the pill status from the real keys. The old code read state['state']
    # — a key that never exists in _claude_auth_state (ok/reason/last_error_text/
    # detected_at/last_probe_at), so the pill was permanently "status unknown".
    # Honest mapping: a failure shows the reason; "ok" only after a verified
    # signal (a passing probe or a clean turn stamps last_probe_at) so a fresh,
    # never-checked boot reads "unknown" instead of falsely claiming signed-in.
    ok = state.get('ok')
    reason = state.get('reason')
    if ok is False:
        status = reason or 'invalid_api_key'
    elif ok is True and state.get('last_probe_at'):
        status = 'ok'
    else:
        status = 'unknown'
    return HealthStatus(
        installed=installed,
        binary_path=None,
        version=None,
        auth_state=AuthState(
            status=status,
            method=None,
            last_checked=str(state.get('last_probe_at') or _t.time()),
            error_text=state.get('last_error_text') or reason,
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
        process_ledger._reap_prior_instance_strays()
    except Exception as e:
        _log(f"[reaper] startup reap failed: {e}")
    _bp_sched._start_scheduler()
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
    # First-boot onboarding project (marker-gated): skipping the tour used to
    # mean a fresh install had zero projects. Swallows internally; never blocks
    # startup.
    _bp_guide.seed_onboarding_on_startup()
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
