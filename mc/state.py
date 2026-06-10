"""Shared mutable process state + locks (MODERNIZATION_PLAN.md Phase 0).

Every name here was moved VERBATIM from server.py. server.py keeps
`from mc.state import <name>` shims so all existing bare-name references
keep working unchanged. Rules:

- Objects here are mutated IN PLACE only, never rebound (verified: the only
  `global`-rebound names in server.py — _fcm_app, _fcm_init_error,
  _LAST_SYSTEM_STATUS, _LAST_RESTART_TIME — deliberately stayed behind and
  migrate with their blueprints).
- This module imports stdlib only. It must never import server.py or flask.
- As Phase 1 blueprints extract, their functions switch from bare names to
  `from mc import state` + `state.<name>` access.
"""

import threading

# ── Live config alias ────────────────────────────────────────────────────────
# server.py rebinds this to the real loaded CONFIG dict at boot
# (`state.CONFIG = CONFIG`, right after _load_config()). CONFIG is never
# rebound afterwards (in-place mutation only), so the alias stays live.
# mc.core._log reads the log level through here.
CONFIG: dict = {}

# ── Agent session tracking ───────────────────────────────────────────────────
# session_id → {proc, status, task, log_lines, started_at, session_id, project_id}
agent_sessions = {}

# ── Per-project agent isolation ──────────────────────────────────────────────
_managers = {}                       # project_id -> ProjectAgentManager
_managers_lock = threading.Lock()    # ONLY for _managers dict mutation; never held during work

# SPEC §3.A.MID committee blocker #3: a dedicated per-project LEAF lock that
# wraps ONLY the MEMORY.md read-modify-write — never the (≤180s) scribe model
# call, never nested under get_manager(pid).lock. Ordering is strictly
# outer(manager RLock at the teardown finally) → inner(this leaf); the
# checkpoint path never holds the manager lock, so it's single-direction and
# cannot deadlock. Also fixes a latent issue in already-shipped code where two
# parallel same-project teardowns serialized on the manager RLock across the
# scribe call.
_mem_write_locks = {}
_mem_write_locks_guard = threading.Lock()


def _get_mem_write_lock(project_id):
    """Get/create the per-project MEMORY.md write leaf-lock."""
    with _mem_write_locks_guard:
        lk = _mem_write_locks.get(project_id)
        if lk is None:
            lk = threading.Lock()
            _mem_write_locks[project_id] = lk
    return lk


# ── Memory condensation state ────────────────────────────────────────────────
_condensing_projects = set()
_condense_lock = threading.Lock()
# pid → unix timestamp of last _dispatch_condense call. Prevents the pre-
# dispatch trigger from re-firing on every back-to-back conversation when
# CLAUDE.md + MEMORY.md keep the total above threshold (condense is async
# and can't shrink the files before the next dispatch check runs).
_condense_triggered_at: dict = {}

# P2-1 (IMPROVEMENT_PLAN_V2.md): per-project memory-condensation visibility.
# Condensation is a background `claude -p` housekeeping agent the user never
# sees. Track its state so /agent/status can surface it. Guarded by
# _condense_lock (same lock that gates _condensing_projects, so state and
# membership never disagree). Shape per pid:
#   {state: idle|running|done|error, started_at, finished_at,
#    bytes_before, bytes_after, error}
_condense_status: dict = {}

# Dedicated scribe lock — distinct from condense so they never cannibalize each
# other (SPEC §3 Leg A B6). One in-flight scribe per project.
_scribing_projects = set()
_scribe_lock = threading.Lock()

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

# ── Claude auth-failure detection state ──────────────────────────────────────
_claude_auth_state = {
    'ok': True,
    'reason': None,
    'last_error_text': None,
    'detected_at': None,
    'last_probe_at': None,
}
_claude_auth_lock = threading.Lock()

# ── Provider env-var storage (Gemini API key etc.) ───────────────────────────
_provider_env_lock = threading.Lock()

# ── Agent → backlog sync (TodoWrite interception) ────────────────────────────
_backlog_sync_lock = threading.Lock()

# ── Step 6: mid-session checkpoint note-taker (SPEC §3.A.MID) ────────────────
_checkpoint_inflight = set()           # session_ids with a worker running
_checkpoint_guard = threading.Lock()
_checkpoint_sema = {}                  # pid -> BoundedSemaphore (fan-out cap)
_checkpoint_sema_guard = threading.Lock()

# ── Hivemind global state ────────────────────────────────────────────────────
_hivemind_sessions = {}           # hivemind_id → {status, worker_sessions, ...}
_hivemind_lock = threading.Lock()
_hivemind_sse_queues = {}         # hivemind_id → [queue, queue, ...] for SSE fan-out
_hivemind_sse_lock = threading.Lock()

_hivemind_orchestrating = set()  # hivemind_ids currently running orchestrator CLI sessions
_hivemind_orch_lock = threading.Lock()

_hivemind_orchestrator_stop = threading.Event()

# ── Background loop stop events ──────────────────────────────────────────────
_scheduler_stop = threading.Event()
_guardian_stop = threading.Event()

# ── Web push state ───────────────────────────────────────────────────────────
_push_state_lock = threading.Lock()

# FCM lazy-init handles (REBOUND globals — the Phase-0 sweep deferred them;
# blueprint 1.2 moved them here with every reference rewritten to state.*,
# so there is exactly one live binding).
_fcm_app = None  # lazy-init firebase_admin.App
_fcm_init_error = None

# ── Dashboard presence (push focus-suppression gate) ─────────────────────────
# A browser/PWA that has a session's chat OPEN and the tab/window VISIBLE +
# FOCUSED pings /api/presence every ~15s. While a fresh ping exists for
# (project_id, session_id), push for that session is suppressed — the user is
# already watching it, so a buzz would be pure noise. Presence is global (any
# device watching → suppress all devices): if Ron is at a screen looking at
# the chat, his phone shouldn't buzz either.
_presence_state: dict = {}
_presence_lock = threading.Lock()
PRESENCE_FRESH_SEC = 25  # ping cadence ~15s; tolerate one missed beat + latency

# ── Remote-session label enforcer state ──────────────────────────────────────
_ENFORCER_STATE = {
    'last_run': 0,
    'last_revoked_count': 0,
    'last_skipped_count': 0,
    'last_error': '',
    'last_per_session_supported': None,  # None=unknown, True/False after a try
}
_enforcer_lock = threading.Lock()

# ── System status cache + restart bookkeeping (rebound globals — moved at 1.6
# with every reference rewritten to state.*, same treatment as _fcm_*) ────────
_LAST_SYSTEM_STATUS = {}
_LAST_RESTART_TIME = 0.0

# ── Observability: background-loop heartbeats (mc/obs.py, Phase 2) ───────────
# subsystem name → unix ts of the last successful iteration. Written by
# mc.obs.heartbeat(), read by GET /api/system/loops.
last_ok: dict = {}
_last_ok_lock = threading.Lock()

# ── Passive update check cache ───────────────────────────────────────────────
_UPDATE_CHECK_LOCK = threading.Lock()
_UPDATE_CHECK_CACHE = {
    'last_check_ts': 0,           # 0 = never checked yet
    'is_git_repo': True,
    'branch': '',
    'commit': '',                  # local HEAD short SHA
    'version': '',                 # synthetic build, e.g. "v1.5.1 build 180"
    'remote_version': '',          # same for origin/<branch> at last fetch
    'remote_commit': '',           # origin/<branch> short SHA at last fetch
    'behind': 0,
    'ahead': 0,
    'has_local_changes': False,
    'update_available': False,
    'recent_log': '',              # `git log HEAD..origin -5 --oneline`
}
_UPDATE_CHECK_INTERVAL_S = 6 * 3600   # 6 hours
_UPDATE_CHECK_BOOT_DELAY_S = 60       # wait 1 min after server start
