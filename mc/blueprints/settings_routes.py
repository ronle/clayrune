"""Settings / config / folder-browse family — blueprint 1.14 (the final
app-level API extraction; MODERNIZATION_PLAN.md mop-up).

Moved VERBATIM from server.py (app-to-bp route-decorator swap is the only edit
applied to the moved text, plus the documented `CONFIG` -> `state.CONFIG`
live-alias rewrite — the 1.7/1.10/1.11/memory precedent). 10 routes:

  * GET   /api/config                       (get_config)
  * PUT   /api/config                       (update_config)
  * GET   /api/browse/folders               (browse_folders)
  * POST  /api/browse/create_folder         (browse_create_folder)
  * GET   /api/settings/domains             (get_domains)
  * POST  /api/settings/domains/add         (add_domain)
  * PATCH /api/settings/domains/<domain_id> (update_domain)
  * DEL   /api/settings/domains/<domain_id> (delete_domain)
  * POST  /api/list-directory               (list_directory)
  * POST  /api/create-folder                (create_folder)

Plus the supporting constants/helpers used ONLY by these routes (re-derived by
AST free-name pass — the 1.13/memory method): DEFAULT_DOMAINS, _load_settings /
_save_settings (the settings.json store), _CONFIG_EDITABLE_KEYS and
_RESPAWN_TRIGGER_KEYS.

DELIBERATELY LEFT in server.py (entry-point / app-shell static, NOT API):
the `/` index, `/sw.js`, `/manifest.json`, `/assets/<filename>` serve routes,
and the env-gated MC_REMOTE_LOCAL_MOCK dev mock-CP routes (/v1/nonce,
/v1/attest, /api/_mock/connect) — those register on `app`.

WIRING (re-derived via AST + proven by ruff F821 + import + pytest): three
path/const slots arrive via wire() — CONFIG_PATH (update_config's persist
target; STAYS in server.py because _load_config reads it at module init),
PROJECTS_BASE (list-directory/create-folder default base; STAYS because
project_routes.wire() also passes it), and SETTINGS_PATH (the settings.json
store path; the 1.7 SESSION_LABELS_PATH wired-placeholder pattern — the
_DATA_ROOT const stays home). CONFIG is NOT wired — it is read (get_config /
browse_folders) AND mutated in-place + persisted (update_config) live via
state.CONFIG, which is the SAME dict object server.py binds at startup
(`_mc_state.CONFIG = CONFIG`). agent_sessions is imported from mc.state and
mutated in-place only (update_config's respawn-flag path: it sets
`_sess['_needs_respawn'] = True` on live Mode-B sessions — a dict-value write,
never a rebind). The respawn mechanism is therefore fully self-contained: no
extra fn slot is needed (the 1.6 _LAST_SYSTEM_STATUS-style "rebound global"
problem does not apply — nothing here rebinds CONFIG or agent_sessions, only
mutates them).

NO import cycle: imports leaf modules only (mc.state, mc.core, flask, stdlib).
"""
from pathlib import Path
import json

from flask import Blueprint, jsonify, request

from mc import state
from mc.core import _log
from mc.state import agent_sessions

bp = Blueprint('settings_routes', __name__)

# ── wired by server.py (see wire()) ──────────────────────────────────────────
# Path/const seams. CONFIG_PATH + PROJECTS_BASE STAY in server.py (other init /
# families read them); SETTINGS_PATH is the wired-placeholder (1.7 pattern).
CONFIG_PATH: Path = None  # type: ignore[assignment]
PROJECTS_BASE: Path = None  # type: ignore[assignment]
SETTINGS_PATH: Path = None  # type: ignore[assignment]


def wire(*, config_path, projects_base, settings_path):
    """Late-bind the three path/const seams. Called once by server.py before
    register_blueprint."""
    global CONFIG_PATH, PROJECTS_BASE, SETTINGS_PATH
    CONFIG_PATH = config_path
    PROJECTS_BASE = projects_base
    SETTINGS_PATH = settings_path


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
    'reply_summarize_enabled', 'reply_summarize_threshold_chars',
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

@bp.route('/api/config')
def get_config():
    """Return all editable config keys."""
    return jsonify({k: state.CONFIG.get(k) for k in _CONFIG_EDITABLE_KEYS})

@bp.route('/api/config', methods=['PUT'])
def update_config():
    """Update config keys and persist to config.json."""
    data = request.get_json() or {}
    updated = {}
    for k, v in data.items():
        if k in _CONFIG_EDITABLE_KEYS:
            state.CONFIG[k] = v
            updated[k] = v
    if updated:
        try:
            with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(state.CONFIG, f, indent=2, ensure_ascii=False)
        except Exception as e:
            return jsonify({'error': f'failed to save config: {e}'}), 500
    # Sticky settings: if a spawn-baked (Tier-1) key changed, flag live Mode B
    # claude sessions to resume into a fresh process at their next turn boundary
    # so the change actually takes effect (a running CLI can't see spawn-baked
    # changes). Best-effort; agent_followup reads `_needs_respawn` under lock.
    respawn_flagged = 0
    if state.CONFIG.get('sticky_agent_settings', False):
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

@bp.route('/api/browse/folders')
def browse_folders():
    """List immediate subdirectories of the requested path. Used by the
    project_path picker UI so users can choose a folder without typing.
    Hidden / dot-prefixed dirs are filtered out."""
    raw = (request.args.get('path') or '').strip()
    if not raw:
        # Default landing: the auto-workspace base (creates if missing).
        base = Path(state.CONFIG.get('auto_workspace_base') or str(Path.home() / 'MissionControl'))
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
    base = str(Path(state.CONFIG.get('auto_workspace_base') or str(Path.home() / 'MissionControl')))
    return jsonify({
        'path': str(target),
        'parent': parent,
        'folders': folders,
        'home': home,
        'workspace_base': base,
    })


@bp.route('/api/browse/create_folder', methods=['POST'])
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

@bp.route('/api/settings/domains')
def get_domains():
    settings = _load_settings()
    return jsonify(settings.get('domains', []))

@bp.route('/api/settings/domains/add', methods=['POST'])
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

@bp.route('/api/settings/domains/<domain_id>', methods=['PATCH'])
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

@bp.route('/api/settings/domains/<domain_id>', methods=['DELETE'])
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


@bp.route('/api/list-directory', methods=['POST'])
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


@bp.route('/api/create-folder', methods=['POST'])
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
