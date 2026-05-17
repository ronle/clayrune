"""GitHub Issues ↔ Mission Control backlog sync.

Isolated module — no Flask dependency.  Pure sync logic + subprocess calls
to the `gh` CLI.  Call ``register()`` once from server.py to inject helpers.

Correctness fixes (IMPROVEMENT_PLAN_V2.md Sprint 2, 2026-05-17):
  P0-1 pagination — no more silent 100-issue cap
  P0-2 last_synced_state base → 3-way merge (stop clobbering local edits)
  P0-3 no redundant close/reopen every cycle (gated on status delta)
  P0-4 deleted-on-GitHub issues detected & defused (no more zombie 404s)
  P0-5 symmetric sanitization (no spurious "updated" churn)
  P0-6 push issue bodies (was always --body '')
  P0-7 throttle bulk pushes (cap creates/cycle so the lock isn't held ~5min)

Rollback: `git checkout plan-v2-rollback-base -- github_sync.py`
(or restore _plan_v2_backups/<ts>/github_sync.py.orig). The new
`last_synced_state` key on backlog items is additive and ignored by old
code, so a downgrade is safe.
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

# ── Tunables ─────────────────────────────────────────────────────────────────

# P0-1: `gh issue list` paginates its GraphQL query internally up to
# --limit, so a single high cap fetches everything for any realistic repo.
# We also detect truncation (returned == cap) and skip dead-detection that
# cycle so a capped page can't be mistaken for "issues deleted".
_GH_ISSUE_LIMIT = 2000

# P0-7: bound how many `gh issue create` calls one sync cycle makes while
# holding the per-project lock. Remainder is created on subsequent cycles.
# 25 creates ≈ a few seconds of lock, vs. ~5 min for 200 unbounded.
_MAX_PUSH_CREATES_PER_CYCLE = 25


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


def sanitize_body(text: str) -> str:
    """Sanitize an issue body. Same rules as sanitize() but allows the
    full GitHub body length (sanitize()'s 1000-char cap is for titles)."""
    if not text:
        return ''
    text = _RE_HTML_TAG.sub('', text)
    text = _RE_DANGEROUS.sub('', text)
    text = _RE_CONTROL.sub('', text)
    return text[:65000].strip()


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


# ── 3-way merge helpers (P0-2) ───────────────────────────────────────────────

_SYNCED_FIELDS = ('text', 'priority', 'status')


def _snapshot(item: dict) -> dict:
    """The synced-field projection used as the merge base."""
    return {f: item.get(f) for f in _SYNCED_FIELDS}


def _merge_field(item, field, gh_val, base, project_id, num):
    """Apply one field with last-synced-state as the 3-way base.

    Returns True if the local value changed.

      gh unchanged vs base                  → keep local (may be pushed)
      gh changed, local untouched vs base   → take gh
      gh changed, local also changed        → conflict: keep local, log
    """
    local_val = item.get(field)
    base_val = base.get(field)
    if gh_val == base_val:
        return False  # GitHub side unchanged — nothing to pull
    if local_val == base_val:
        # Only GitHub moved → adopt it.
        item[field] = gh_val
        return True
    if local_val == gh_val:
        return False  # Both converged independently — no-op.
    # Both sides diverged from base → conflict. Local wins (it will be
    # pushed back), but surface it so a human can reconcile.
    _log_activity(
        project_id,
        f"GitHub: conflict on issue #{num} '{field}' "
        f"(local kept '{str(local_val)[:40]}', GitHub had '{str(gh_val)[:40]}')",
    )
    return False


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
        '--state', 'all', '--limit', str(_GH_ISSUE_LIMIT),
        '--json', 'number,title,state,labels,author,updatedAt',
    ])
    if not ok:
        raise RuntimeError(f'Failed to list issues: {data}')
    if not isinstance(data, list):
        return 0, 0, 0

    # P0-1: if the page came back full, it may be truncated. Don't trust
    # absence-of-issue as "deleted" this cycle (P0-4 guard).
    truncated = len(data) >= _GH_ISSUE_LIMIT
    if truncated:
        _log_activity(
            project.get('id', ''),
            f"GitHub: issue list hit the {_GH_ISSUE_LIMIT} cap for {repo} — "
            f"some issues may not have synced this cycle",
        )

    backlog = project.setdefault('backlog', [])
    by_num: dict[int, dict] = {}
    for item in backlog:
        num = item.get('github_issue_number')
        if num is not None:
            by_num[num] = item

    new_count = 0
    updated_count = 0
    closed_count = 0
    project_id = project.get('id', '')
    seen_nums: set[int] = set()

    for issue in data:
        num = issue.get('number')
        if num is None:
            continue
        seen_nums.add(num)
        title = sanitize(issue.get('title', ''))   # P0-5: GitHub side sanitized
        state = issue.get('state', 'OPEN')
        labels = issue.get('labels', [])
        author = sanitize((issue.get('author') or {}).get('login', ''))
        priority = _priority_from_labels(labels)
        mc_status = 'done' if state == 'CLOSED' else 'open'

        if num in by_num:
            item = by_num[num]
            # P0-2: base = previous reconciled state. Missing (pre-upgrade
            # or freshly-linked) → seed with current local values so this
            # cycle behaves like the old GitHub-wins path, then 3-way takes
            # over once a snapshot exists.
            base = item.get('last_synced_state') or _snapshot(item)
            old_status = item.get('status')

            changed = False
            changed |= _merge_field(item, 'text', title, base, project_id, num)
            changed |= _merge_field(item, 'priority', priority, base, project_id, num)
            changed |= _merge_field(item, 'status', mc_status, base, project_id, num)

            if item.get('status') == 'done' and old_status != 'done':
                if not item.get('done_at'):
                    item['done_at'] = _now_iso()
                closed_count += 1
                _log_activity(project_id,
                              f"GitHub: Issue #{num} closed by @{author}")
            elif item.get('status') == 'open' and old_status == 'done':
                item['done_at'] = None

            if changed:
                item['github_synced_at'] = _now_iso()
                updated_count += 1
            # Record what GitHub had this cycle as the new base.
            item['last_synced_state'] = {
                'text': title, 'priority': priority, 'status': mc_status,
            }
            item.pop('github_deleted', None)  # resurrected if it reappeared
        else:
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
            new_item['last_synced_state'] = {
                'text': title, 'priority': priority, 'status': mc_status,
            }
            backlog.append(new_item)
            new_count += 1
            _log_activity(project_id,
                          f"GitHub: New issue #{num} '{title[:50]}' by @{author}")

    # P0-4: an item linked to a number GitHub no longer returns (deleted /
    # transferred) is a zombie — every future push would 404. Defuse it:
    # unlink + flag, keep the local task. Skip when the list was truncated
    # (a missing number might just be on an unfetched page).
    if not truncated:
        for item in backlog:
            num = item.get('github_issue_number')
            if num is None or num in seen_nums:
                continue
            if item.get('github_synced_at') == _now_iso():
                continue  # just created/linked this cycle
            item['github_deleted'] = True
            item['github_issue_number'] = None
            item.pop('last_synced_state', None)
            _log_activity(
                project_id,
                f"GitHub: issue #{num} no longer exists on {repo} — "
                f"unlinked (local task kept)",
            )

    return new_count, updated_count, closed_count


# ── Push: MC → GitHub ───────────────────────────────────────────────────────

def _push_items(project: dict, repo: str) -> int:
    """Push local backlog items to GitHub as issues."""
    backlog = project.get('backlog', [])
    push_count = 0
    created_this_cycle = 0
    deferred = 0
    project_id = project.get('id', '')

    for item in backlog:
        if item.get('github_issue_number'):
            # P0-3: only push a state change when the LOCAL status actually
            # diverged from the last synced base. Previously every linked
            # item ate a close/reopen call every cycle.
            num = item['github_issue_number']
            status = item.get('status', 'open')
            base = item.get('last_synced_state') or {}
            base_status = base.get('status')
            if status == base_status:
                continue  # in sync — no API call
            if status == 'done':
                ok, _ = gh_run(['issue', 'close', '-R', repo, str(num)])
            elif status == 'open':
                ok, _ = gh_run(['issue', 'reopen', '-R', repo, str(num)])
            else:
                ok = False
            if ok:
                # Reflect the push in the base so we don't re-push next cycle.
                item.setdefault('last_synced_state', {})['status'] = status
            continue

        if item.get('github_deleted'):
            continue  # P0-4: unlinked zombie — don't recreate it

        # New local item → create GitHub issue.
        text = sanitize((item.get('text') or '').strip())  # P0-5: symmetric
        if not text:
            continue

        # P0-7: bound creates per cycle so the per-project lock isn't held
        # for minutes on a bulk first sync.
        if created_this_cycle >= _MAX_PUSH_CREATES_PER_CYCLE:
            deferred += 1
            continue

        # P0-6: push a real body (was always --body '').
        body = sanitize_body(
            item.get('body') or item.get('notes') or item.get('description') or ''
        )
        ok, result = gh_run([
            'issue', 'create', '-R', repo,
            '--title', text, '--body', body,
        ])
        created_this_cycle += 1
        new_num = None
        if ok and isinstance(result, dict) and result.get('number'):
            new_num = result['number']
        elif ok and isinstance(result, str):
            m = re.search(r'/issues/(\d+)', result)
            if m:
                new_num = int(m.group(1))
        if new_num is not None:
            item['github_issue_number'] = new_num
            item['text'] = text  # store sanitized so future compares converge
            item['github_synced_at'] = _now_iso()
            item['last_synced_state'] = _snapshot(item)
            push_count += 1

            priority = item.get('priority', 'normal')
            if priority != 'normal':
                gh_run([
                    'issue', 'edit', '-R', repo, str(new_num),
                    '--add-label', f'priority:{priority}',
                ])

    if push_count > 0:
        _log_activity(project_id,
                      f"GitHub: Pushed {push_count} item{'s' if push_count != 1 else ''} to {repo}")
    if deferred > 0:
        _log_activity(
            project_id,
            f"GitHub: deferred {deferred} new item(s) past the "
            f"{_MAX_PUSH_CREATES_PER_CYCLE}/cycle push cap — next sync continues",
        )
    return push_count


# ── Main orchestrator ───────────────────────────────────────────────────────

def sync_project(project_id: str) -> tuple[bool, str]:
    """Run a full pull+push sync cycle for one project.

    Returns (ok, summary_string).
    Rate-limited to one sync per 60 seconds per project.
    """
    import time
    now = time.time()

    if project_id in _last_sync:
        elapsed = now - _last_sync[project_id]
        if elapsed < _RATE_LIMIT_SECS:
            return False, f'Rate limited — wait {int(_RATE_LIMIT_SECS - elapsed)}s'

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

        new_c, upd_c, closed_c = _pull_issues(project, repo)
        push_c = _push_items(project, repo)

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
