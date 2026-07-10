"""Steward core — config accessors, charter, cycle-task, notify seam, fence
settings, loop-health. Pure-ish: reads/writes project state through injected
CFG helpers, no Flask/server import.
"""
import json
import sys
from pathlib import Path

from ._config import CFG, now_iso, _log

# ── Config (per-project, stored on the project dict) ──────────────────────────
CHARTER_PREFIX = 'STEWARD CHARTER: '
DEFAULT_CADENCE_MINUTES = 180
MIN_CADENCE_MINUTES = 30      # floor — bounds runaway wake frequency
MAX_CADENCE_MINUTES = 1440    # ceiling — 1/day

_NOTIFY_KINDS = ('fyi', 'done', 'blocked', 'decision-needed')
_PUSH_KINDS = ('blocked', 'decision-needed', 'done')  # never push routine FYIs


def steward_enabled(project: dict) -> bool:
    return bool(project) and str(project.get('steward_mode', 'off')).lower() == 'on'


def get_objective(project: dict) -> str:
    return (project.get('steward_objective') or '').strip()


def get_cadence_minutes(project: dict) -> int:
    try:
        v = int(project.get('steward_cadence_minutes') or DEFAULT_CADENCE_MINUTES)
    except (TypeError, ValueError):
        v = DEFAULT_CADENCE_MINUTES
    return max(MIN_CADENCE_MINUTES, min(MAX_CADENCE_MINUTES, v))


# ── Charter (a pinned backlog item = the field of responsibility) ─────────────
def find_charter(project: dict):
    """Return the charter backlog item, or None. Identified by source tag first,
    text prefix as fallback (survives a source rewrite)."""
    for it in (project.get('backlog') or []):
        if it.get('source') == 'steward-charter':
            return it
    for it in (project.get('backlog') or []):
        if str(it.get('text', '')).startswith(CHARTER_PREFIX):
            return it
    return None


def ensure_charter(project_id: str, objective: str):
    """Find-or-create the charter item for a project. Returns the item dict or
    None on failure. Idempotent."""
    if not CFG.configured or not CFG.load_project or not CFG.save_project:
        return None
    try:
        p = CFG.load_project(project_id)
    except Exception as e:
        _log(f"[steward] ensure_charter load {project_id} failed: {e}")
        return None
    if p is None:
        return None
    existing = find_charter(p)
    if existing:
        return existing
    import uuid
    item = {
        'id': str(uuid.uuid4())[:8],
        'text': CHARTER_PREFIX + (objective or '(objective unset)').strip(),
        'priority': 'high',
        'status': 'open',
        'created_at': now_iso(),
        'done_at': None,
        'source': 'steward-charter',
        'attachments': [],
        'notes': [],
    }
    p.setdefault('backlog', []).insert(0, item)
    p['last_updated'] = now_iso()
    try:
        CFG.save_project(project_id, p)
    except Exception as e:
        _log(f"[steward] ensure_charter save {project_id} failed: {e}")
        return None
    return item


# ── The cycle task (what the scheduler dispatches each fire) ───────────────────
def build_cycle_task(project: dict, charter_item: dict) -> str:
    """The `[Steward cycle]` prompt. The marker triggers the mc-steward skill;
    the scheduler prepends its own local-time header on continued runs."""
    objective = get_objective(project) or (
        str(charter_item.get('text', '')).replace(CHARTER_PREFIX, '') if charter_item else '')
    cid = (charter_item or {}).get('id', '')
    return (
        "[Steward cycle] You are the autonomous STEWARD of this project — run ONE "
        "cycle now, following the mc-steward skill exactly.\n\n"
        f"Charter (your field of responsibility): {objective}\n"
        f"Charter backlog item id: {cid} (append your progress note here via "
        f"POST /api/project/<pid>/backlog/{cid}/note).\n\n"
        "Reminder of the cycle: orient (charter + backlog + your past notes) → "
        "pick the SINGLE highest-value next step → if reversible, do it; if "
        "irreversible/mutating, post a DECISION NEEDED note with the exact "
        "command and STOP that step → log what you did → message the human only "
        "if they must KNOW or DECIDE → ensure your next wake is scheduled. Never "
        "block the loop waiting; if stuck, post BLOCKED and end the cycle."
    )


# ── Notify seam (server-side lifecycle messages; the AGENT uses curl+Push) ─────
def steward_notify(project_id: str, kind: str, body: str, action: str = '') -> bool:
    """Server-side agent->human message: append a prefixed note to the charter
    item and (for blocked/decision-needed/done) fire a push. The steward AGENT
    reports through the same surfaces directly (per the SKILL); this seam is for
    lifecycle events (enabled, disabled, health). When the unified inbox lands,
    re-point this one function. Best-effort — never raises."""
    kind = (kind or 'fyi').lower()
    if kind not in _NOTIFY_KINDS:
        kind = 'fyi'
    if not CFG.configured or not CFG.load_project:
        return False
    try:
        p = CFG.load_project(project_id)
        if p is None:
            return False
        charter = find_charter(p)
        if charter is None:
            charter = ensure_charter(project_id, get_objective(p))
        if charter is None:
            return False
        prefix = {'fyi': 'FYI', 'done': 'DONE', 'blocked': 'BLOCKED',
                  'decision-needed': 'DECISION NEEDED'}[kind]
        text = f"{prefix}: {body}".strip()
        if action:
            text += f"\nAction (approve to run): {action}"
        ok = False
        if CFG.append_note:
            ok = bool(CFG.append_note(project_id, charter.get('id'), text, 'steward'))
        if kind in _PUSH_KINDS and CFG.notify_push:
            try:
                CFG.notify_push(project_id, 'agent',
                                f"Steward · {prefix}", body[:200])
            except Exception as e:
                _log(f"[steward] push failed {project_id}: {e}")
        return ok
    except Exception as e:
        _log(f"[steward] steward_notify {project_id} failed: {e}")
        return False


# ── Fence settings file (the PreToolUse hook, passed via --settings) ──────────
def fence_script_path() -> Path:
    return Path(__file__).resolve().with_name('fence.py')


def fence_settings_path() -> Path:
    root = CFG.data_root or Path('data')
    d = root / 'steward'
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        _log(f"[steward] mkdir {d} failed: {e}")
    return d / 'fence-settings.json'


def _fence_settings_content() -> dict:
    # Quote both interpreter and script so paths with spaces survive the shell.
    cmd = f'"{sys.executable}" "{fence_script_path().as_posix()}"'
    return {
        "hooks": {
            "PreToolUse": [{
                "matcher": "Bash|Write|Edit|MultiEdit|NotebookEdit",
                "hooks": [{"type": "command", "command": cmd, "timeout": 10000}],
            }],
        },
    }


def ensure_fence_settings() -> Path:
    """Write (idempotently) the standalone steward fence settings file and return
    its path. Reserved for the future per-session `--settings <this>` path (fences
    ONLY the steward session). The MVP instead merges the hook into the project's
    own .claude/settings.json via install_fence_to_project() — see below."""
    path = fence_settings_path()
    content = _fence_settings_content()
    try:
        if path.exists():
            if json.loads(path.read_text(encoding='utf-8')) == content:
                return path
        path.write_text(json.dumps(content, indent=2), encoding='utf-8')
    except Exception as e:
        _log(f"[steward] ensure_fence_settings failed: {e}")
    return path


# The PreToolUse hook is installed into the project's own .claude/settings.json.
# MC runs `claude` with cwd = project_path, so the project settings (and their
# hooks) are read automatically and MERGE with user/global hooks. Identify OUR
# hook entry by this substring in its command so removal is precise and never
# touches a user-authored hook.
STEWARD_HOOK_MARKER = 'fence.py'


def _project_settings_path(project_path: str) -> Path:
    return Path(project_path) / '.claude' / 'settings.json'


def _is_steward_hook_entry(entry: dict) -> bool:
    for h in (entry.get('hooks') or []):
        if STEWARD_HOOK_MARKER in str(h.get('command', '')):
            return True
    return False


def install_fence_to_project(project_path: str) -> bool:
    """Merge the steward PreToolUse fence hook into <project>/.claude/settings.json,
    preserving every other setting and any user-authored hooks. Idempotent — a
    second call replaces our stale entry rather than duplicating. Returns True on
    write. Best-effort. Fences ALL sessions in this project (defense-in-depth for
    a dedicated steward project)."""
    if not project_path:
        return False
    path = _project_settings_path(project_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        settings = {}
        if path.exists():
            try:
                settings = json.loads(path.read_text(encoding='utf-8')) or {}
            except Exception as e:
                _log(f"[steward] project settings unparseable, refusing to clobber: {e}")
                return False
        hooks = settings.setdefault('hooks', {})
        pre = hooks.setdefault('PreToolUse', [])
        # Drop any prior steward entry, then append the current one (self-heal on
        # path/interpreter change).
        pre = [e for e in pre if not _is_steward_hook_entry(e)]
        pre.append(_fence_settings_content()['hooks']['PreToolUse'][0])
        hooks['PreToolUse'] = pre
        path.write_text(json.dumps(settings, indent=2), encoding='utf-8')
        return True
    except Exception as e:
        _log(f"[steward] install_fence_to_project {project_path} failed: {e}")
        return False


def remove_fence_from_project(project_path: str) -> bool:
    """Remove ONLY the steward fence hook from <project>/.claude/settings.json,
    restoring normal (unfenced) behavior. Preserves all other settings/hooks.
    Best-effort; a missing file is a no-op success."""
    if not project_path:
        return False
    path = _project_settings_path(project_path)
    if not path.exists():
        return True
    try:
        settings = json.loads(path.read_text(encoding='utf-8')) or {}
        pre = (settings.get('hooks') or {}).get('PreToolUse')
        if not isinstance(pre, list):
            return True
        kept = [e for e in pre if not _is_steward_hook_entry(e)]
        if kept:
            settings['hooks']['PreToolUse'] = kept
        else:
            settings['hooks'].pop('PreToolUse', None)
            if not settings['hooks']:
                settings.pop('hooks', None)
        path.write_text(json.dumps(settings, indent=2), encoding='utf-8')
        return True
    except Exception as e:
        _log(f"[steward] remove_fence_from_project {project_path} failed: {e}")
        return False


# ── Loop-health (observability — cycles / decisions pending / blocked) ────────
def loop_health() -> dict:
    """Aggregate steward state across projects: who's enabled, pending decisions,
    blocked count, staleness. Best-effort; derived from charter notes."""
    out = {'enabled': [], 'decisions_pending': 0, 'blocked': 0,
           'projects_enabled': 0, 'alerts': []}
    if not CFG.configured or not CFG.load_projects:
        return out
    try:
        projects = CFG.load_projects() or []
    except Exception as e:
        _log(f"[steward] loop_health load_projects failed: {e}")
        return out
    for p in projects:
        if not isinstance(p, dict) or not steward_enabled(p):
            continue
        out['projects_enabled'] += 1
        charter = find_charter(p)
        notes = (charter.get('notes') if charter else []) or []
        pend = sum(1 for n in notes if str(n.get('text', '')).startswith('DECISION NEEDED'))
        blk = sum(1 for n in notes if str(n.get('text', '')).startswith('BLOCKED'))
        last_ts = notes[-1].get('ts') if notes else None
        out['decisions_pending'] += pend
        out['blocked'] += blk
        out['enabled'].append({
            'project_id': p.get('id'),
            'project': p.get('name') or p.get('id'),
            'objective': get_objective(p),
            'cadence_minutes': get_cadence_minutes(p),
            'has_charter': charter is not None,
            'decisions_pending': pend,
            'blocked': blk,
            'last_note_ts': last_ts,
            'standalone': bool(p.get('_is_steward_workspace')),
        })
        if pend:
            out['alerts'].append(f"{p.get('name') or p.get('id')}: {pend} decision(s) awaiting you")
    return out
