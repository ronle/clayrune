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

# ── Global incognito pseudo-project ── moved to mc/blueprints/agent_routes.py
# (1.12); INCOGNITO_PROJECT_ID + _ensure_incognito_project resolve through the
# inbound shims at the agent stanza below (startup + backfill/reconcile readers).


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


# ── MCP catalog + per-project trim resolution ── moved to
# mc/blueprints/agent_routes.py (1.12); the mcp_routes wire() below re-homes
# its mcp_server_catalog_fn slot onto _bp_agent.


# ── ProjectAgentManager + get_manager + per-project guardian loop ── moved to
# mc/blueprints/agent_routes.py (1.12).

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


# _project_live_agent ── moved to mc/blueprints/project_routes.py (1.11);
# its only caller is /api/projects (same module).


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


# _register_process / _unregister_process ── moved to
# mc/blueprints/agent_routes.py (1.12); _proc_identity + _persist_pid_ledger
# stay here with the reaper (startup family) and are wired in.


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


# ── Project-record store + project/backlog/github/code-sync/attachment/
# rules/memory-editor/order endpoints ── extracted to
# mc/blueprints/project_routes.py (1.11): the CRUD core (load_project /
# save_project / load_projects with the LOAD-BEARING EXCLUDED_SIDECAR_SUFFIXES
# exclusion / update_project / delete_project), _project_live_agent,
# _log_agent_activity (project-record activity_log writer), backlog CRUD +
# _append_note_to_backlog_item, github + code-sync glue (the blueprint imports
# github_sync/project_sync directly; their register() wiring stays below,
# unchanged), attachments + serve-image + the upload-quota helpers, import,
# rules, the memory editor-CRUD trio (locked managed-region writers stay
# below, untouched), and projects/order + grid-layout. wire() late-binds the
# path constants (DATA_DIR & co. stay here — other families still read them)
# and the cross-family fns (_get_memory_path → Scribe/condense; _resolve_claude,
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

_bp_projects.wire(
    data_dir=DATA_DIR,
    data_root=_DATA_ROOT,
    uploads_dir=UPLOADS_DIR,
    projects_base=PROJECTS_BASE,
    shared_rules_path=SHARED_RULES_PATH,
    get_memory_path_fn=_get_memory_path,
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
    load_project_fn=_bp_projects.load_project,
    save_project_fn=_bp_projects.save_project,
    data_dir=DATA_DIR,
    memory_search_fn=_memory_search,
    resolve_claude_fn=_bp_agent._resolve_claude,
    popen_flags=_POPEN_FLAGS,
    startupinfo=_STARTUPINFO,
    server_dir=Path(__file__).parent,
)
app.register_blueprint(_bp_guide.bp)


# ── Project endpoints ── moved to mc/blueprints/project_routes.py (1.11).


# ── Scribe telemetry (SPEC §8) ── /scribe-stats moved to mc/blueprints/guide_routes.py (1.9).


# ── Phase 4 Distiller endpoints ── extracted to
# mc/blueprints/distiller_routes.py (1.5). Projects-family accessor re-homed
# onto _bp_projects (1.11).
from mc.blueprints import distiller_routes as _bp_distiller  # noqa: E402

_bp_distiller.wire(load_project_fn=_bp_projects.load_project, data_dir=DATA_DIR)
app.register_blueprint(_bp_distiller.bp)


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


# ── _log_agent_completion + _auto_dispatch_followup + _check_context_budget ──
# moved to mc/blueprints/agent_routes.py (1.12). Their scribe/condense calls
# are wired (the machinery stays below, untouched).


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


# ── Auto-model router (classifier + telemetry) ── moved to
# mc/blueprints/agent_routes.py (1.12). _scribe_call stays above; wired in.


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


# ── Agent dispatch family ── extracted to mc/blueprints/agent_routes.py (1.12):
# claude resolution + pid/kill/window helpers, incognito pseudo-project, MCP
# trim catalog, claude flags + auto-model router, sysprompt files,
# ProjectAgentManager + guardians, process-ledger writers, upload-image,
# provider auth/env routes + legacy /api/claude shims, context builders, BOTH
# stream readers, agent_log store + completion writers, revive, dispatch
# internals, the 11 agent/* routes (followup moved WHOLE), plan-file pair,
# transcript/reconstruct, run-history + usage. wire() late-binds the
# memory/scribe/condense seams (THE LOAD-BEARING LINE — that machinery stays
# in THIS file: _scribe_*, _condense_*, _maybe_checkpoint/_checkpoint_*,
# _write_session_memory, _commit_managed_entry, _mem_*, _get_memory_path/
# _get_archive_path, _append_to_archive, _reconcile_unscribed_sessions,
# distiller glue), the transcript/scan helpers shared with those stayers, the
# reaper-family internals, and the path/Popen consts. The module was imported
# at the project_routes stanza above (its defs feed earlier re-homed seams);
# wire() runs HERE because the wired stayers (_dispatch_condense & co.) are
# defined just above.
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
    get_memory_path_fn=_get_memory_path,
    get_archive_path_fn=_get_archive_path,
    memory_search_fn=_memory_search,
    maybe_checkpoint_fn=_maybe_checkpoint,
    write_session_memory_fn=_write_session_memory,
    dispatch_condense_fn=_dispatch_condense,
    should_condense_fn=_should_condense,
    get_condense_status_fn=_get_condense_status,
    scribe_call_fn=_scribe_call,
    find_transcript_file_fn=_find_transcript_file,
    parse_transcript_messages_fn=_parse_transcript_messages,
    recent_claude_transcripts_fn=_recent_claude_transcripts,
    session_too_large_fn=_session_too_large,
    long_session_advisory_fn=_long_session_advisory,
    resume_is_fragile_fn=_resume_is_fragile,
    encode_project_path_fn=_encode_project_path,
    extract_transcript_telemetry_fn=_extract_transcript_telemetry,
    proc_identity_fn=_proc_identity,
    persist_pid_ledger_fn=_persist_pid_ledger,
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


# ── Agent endpoints (dispatch/send/stream/followup/stop/interrupt/session/
# plan-file/status/guardian-reset) ── moved to mc/blueprints/agent_routes.py
# (1.12). agent_followup moved WHOLE (492 lines, per plan).
# ── Terminal session management ── moved to mc/blueprints/terminal_routes.py (1.8).


# ── Process Tracker endpoints ── moved to mc/blueprints/system_routes.py (1.6).

# ── Hivemind ── moved to mc/blueprints/hivemind_routes.py (1.10).


# ── Agent log + transcript/reconstruct routes ── moved to
# mc/blueprints/agent_routes.py (1.12).

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


# ── Project order + grid layout ── moved to mc/blueprints/project_routes.py (1.11).


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
