"""GitHub Issues ↔ Mission Control backlog sync.

Isolated module — no Flask dependency.  Pure sync logic + subprocess calls
to the `gh` CLI.  Call ``register()`` once from server.py to inject helpers.
"""
import json
import re
import subprocess
import threading
import uuid
from datetime import datetime, timezone

# ── Injected helpers (set by register()) ─────────────────────────────────────

_POPEN_FLAGS = 0
_STARTUPINFO = None
_log_activity = None   # _log_agent_activity(project_id, msg)
_load_project = None
_save_project = None
_now_iso = None

# ── Per-project rate-limit / lock state ──────────────────────────────────────

_locks: dict[str, threading.Lock] = {}
_last_sync: dict[str, float] = {}          # project_id → epoch seconds
_RATE_LIMIT_SECS = 60


def register(popen_flags, startupinfo, log_activity, load_project, save_project, now_iso):
    """Inject server helpers — called once at startup."""
    global _POPEN_FLAGS, _STARTUPINFO, _log_activity
    global _load_project, _save_project, _now_iso
    _POPEN_FLAGS = popen_flags
    _STARTUPINFO = startupinfo
    _log_activity = log_activity
    _load_project = load_project
    _save_project = save_project
    _now_iso = now_iso


# ── Security: sanitise all text from GitHub ──────────────────────────────────

_RE_HTML_TAG = re.compile(r'<[^>]+>')
_RE_DANGEROUS = re.compile(
    r'javascript\s*:|data\s*:[^,]*;base64',
    re.IGNORECASE,
)
_RE_CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
_MAX_TEXT_LEN = 1000


def sanitize(text: str) -> str:
    """Strip dangerous content from GitHub-sourced text."""
    if not text:
        return ''
    text = _RE_HTML_TAG.sub('', text)
    text = _RE_DANGEROUS.sub('', text)
    text = _RE_CONTROL.sub('', text)
    return text[:_MAX_TEXT_LEN].strip()


# ── Repo validation ─────────────────────────────────────────────────────────

_RE_REPO = re.compile(r'^[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+$')


def validate_repo(repo: str) -> tuple[bool, str]:
    """Check format + existence via gh CLI.  Returns (ok, error_or_empty)."""
    if not _RE_REPO.match(repo):
        return False, 'Invalid format — use owner/repo'
    ok, result = gh_run(['repo', 'view', repo, '--json', 'name'])
    if not ok:
        return False, f'Cannot access repo: {result}'
    return True, ''


# ── Safe subprocess wrapper ─────────────────────────────────────────────────

def gh_run(args: list[str], timeout: int = 30) -> tuple[bool, object]:
    """Run a ``gh`` sub-command.  Returns (ok, parsed_json_or_error_str)."""
    cmd = ['gh'] + args
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=_POPEN_FLAGS,
            startupinfo=_STARTUPINFO,
        )
        if r.returncode != 0:
            return False, (r.stderr or r.stdout or 'unknown error').strip()
        out = r.stdout.strip()
        if not out:
            return True, None
        try:
            return True, json.loads(out)
        except json.JSONDecodeError:
            return True, out
    except subprocess.TimeoutExpired:
        return False, 'gh command timed out'
    except FileNotFoundError:
        return False, 'gh CLI not found — install from https://cli.github.com'
    except Exception as e:
        return False, str(e)


# ── Pull: GitHub → MC ───────────────────────────────────────────────────────

def _priority_from_labels(labels: list[dict]) -> str:
    """Extract MC priority from GitHub labels."""
    for lbl in labels:
        name = (lbl.get('name') or '').lower()
        if name.startswith('priority:'):
            p = name.split(':', 1)[1].strip()
            if p in ('high', 'medium', 'low'):
                return p
    return 'normal'


def _pull_issues(project: dict, repo: str) -> tuple[int, int, int]:
    """Fetch issues from GitHub and merge into project backlog."""
    ok, data = gh_run([
        'issue', 'list', '-R', repo,
        '--state', 'all', '--limit', '100',
        '--json', 'number,title,state,labels,author,updatedAt',
    ])
    if not ok:
        raise RuntimeError(f'Failed to list issues: {data}')
    if not isinstance(data, list):
        return 0, 0, 0

    backlog = project.setdefault('backlog', [])
    # Index existing items by github_issue_number
    by_num: dict[int, dict] = {}
    for item in backlog:
        num = item.get('github_issue_number')
        if num is not None:
            by_num[num] = item

    new_count = 0
    updated_count = 0
    closed_count = 0
    project_id = project.get('id', '')

    for issue in data:
        num = issue.get('number')
        if num is None:
            continue
        title = sanitize(issue.get('title', ''))
        state = issue.get('state', 'OPEN')
        labels = issue.get('labels', [])
        author = sanitize((issue.get('author') or {}).get('login', ''))
        priority = _priority_from_labels(labels)
        mc_status = 'done' if state == 'CLOSED' else 'open'

        if num in by_num:
            item = by_num[num]
            changed = False
            if item.get('text') != title:
                item['text'] = title
                changed = True
            if item.get('priority') != priority:
                item['priority'] = priority
                changed = True
            if item.get('status') != mc_status:
                old_status = item.get('status')
                item['status'] = mc_status
                if mc_status == 'done' and not item.get('done_at'):
                    item['done_at'] = _now_iso()
                elif mc_status == 'open':
                    item['done_at'] = None
                changed = True
                if mc_status == 'done' and old_status != 'done':
                    closed_count += 1
                    _log_activity(project_id,
                                  f"GitHub: Issue #{num} closed by @{author}")
            if changed:
                item['github_synced_at'] = _now_iso()
                updated_count += 1
        else:
            # New issue from GitHub
            new_item = {
                'id': uuid.uuid4().hex[:8],
                'text': title,
                'priority': priority,
                'status': mc_status,
                'created_at': _now_iso(),
                'source': 'github',
                'github_issue_number': num,
                'github_synced_at': _now_iso(),
            }
            if mc_status == 'done':
                new_item['done_at'] = _now_iso()
                closed_count += 1
            backlog.append(new_item)
            new_count += 1
            _log_activity(project_id,
                          f"GitHub: New issue #{num} '{title[:50]}' by @{author}")

    return new_count, updated_count, closed_count


# ── Push: MC → GitHub ───────────────────────────────────────────────────────

def _push_items(project: dict, repo: str) -> int:
    """Push local backlog items to GitHub as issues."""
    backlog = project.get('backlog', [])
    push_count = 0
    project_id = project.get('id', '')

    for item in backlog:
        if item.get('github_issue_number'):
            # Already linked — sync status
            num = item['github_issue_number']
            status = item.get('status', 'open')
            if status == 'done':
                gh_run(['issue', 'close', '-R', repo, str(num)])
            elif status == 'open':
                gh_run(['issue', 'reopen', '-R', repo, str(num)])
            continue

        # New local item → create GitHub issue
        text = (item.get('text') or '').strip()
        if not text:
            continue

        ok, result = gh_run([
            'issue', 'create', '-R', repo,
            '--title', text, '--body', '',
        ])
        if ok and isinstance(result, dict) and result.get('number'):
            item['github_issue_number'] = result['number']
            item['github_synced_at'] = _now_iso()
            push_count += 1

            # Add priority label if not normal
            priority = item.get('priority', 'normal')
            if priority != 'normal':
                gh_run([
                    'issue', 'edit', '-R', repo, str(result['number']),
                    '--add-label', f'priority:{priority}',
                ])
        elif ok and isinstance(result, str):
            # gh issue create returns URL as text, parse number
            m = re.search(r'/issues/(\d+)', result)
            if m:
                item['github_issue_number'] = int(m.group(1))
                item['github_synced_at'] = _now_iso()
                push_count += 1

    if push_count > 0:
        _log_activity(project_id,
                      f"GitHub: Pushed {push_count} item{'s' if push_count != 1 else ''} to {repo}")
    return push_count


# ── Main orchestrator ───────────────────────────────────────────────────────

def sync_project(project_id: str) -> tuple[bool, str]:
    """Run a full pull+push sync cycle for one project.

    Returns (ok, summary_string).
    Rate-limited to one sync per 60 seconds per project.
    """
    import time
    now = time.time()

    # Rate limit
    if project_id in _last_sync:
        elapsed = now - _last_sync[project_id]
        if elapsed < _RATE_LIMIT_SECS:
            return False, f'Rate limited — wait {int(_RATE_LIMIT_SECS - elapsed)}s'

    # Per-project lock (non-blocking)
    lock = _locks.setdefault(project_id, threading.Lock())
    if not lock.acquire(blocking=False):
        return False, 'Sync already in progress'

    try:
        project = _load_project(project_id)
        if not project:
            return False, 'Project not found'

        repo = project.get('github_repo', '')
        if not repo or not project.get('github_sync_enabled'):
            return False, 'GitHub sync not configured'

        _last_sync[project_id] = now

        # Pull from GitHub
        new_c, upd_c, closed_c = _pull_issues(project, repo)

        # Push to GitHub
        push_c = _push_items(project, repo)

        # Update sync timestamp
        project['github_last_sync'] = _now_iso()
        _save_project(project_id, project)

        parts = []
        if new_c:
            parts.append(f'{new_c} new')
        if upd_c:
            parts.append(f'{upd_c} updated')
        if closed_c:
            parts.append(f'{closed_c} closed')
        if push_c:
            parts.append(f'{push_c} pushed')
        summary = ', '.join(parts) if parts else 'no changes'
        return True, summary

    except Exception as e:
        _log_activity(project_id, f"GitHub sync error: {e}")
        return False, str(e)
    finally:
        lock.release()
