"""Clayrune project code sync — spike (read-only).

Companion to ``github_sync.py``. github_sync handles Issues↔backlog.
This module handles **code** sync: bidirectional git sync between a
project workspace and its GitHub remote via per-machine sync branches.

Design: docs/PROJECT_SYNC_DESIGN.md. Decisions locked 2026-05-25.

This file is the **spike** (§12.1 of the design doc):
  - install ID + sync-branch name derivation
  - hidden worktree (Option A) under <project>/.clayrune/sync-tree/
  - read-only fetch loop (no auto-pull, no auto-commit, no push yet)
  - status computation: ahead / behind / dirty / incoming commits per
    other-install sync branch

Out of scope for the spike (later phases):
  - auto-commit per agent turn
  - accept / reject / cherry-pick of incoming commits
  - conflict resolution UI
  - sync-branch GC
  - PR escalation on protected main

Sidecars live OUTSIDE DATA_DIR per the load-bearing rule in CLAUDE.md
(``data/projects/`` is project-records-only; everything else suffix-excluded
in load_projects() or routed elsewhere).
"""
from __future__ import annotations

import re
import socket
import subprocess
import threading
import uuid
from pathlib import Path

# ── Injected helpers (set by register()) ─────────────────────────────────────

_POPEN_FLAGS = 0
_STARTUPINFO = None
_log_activity = None       # _log_agent_activity(project_id, msg)
_load_project = None
_save_project = None
_now_iso = None
_data_root: Path | None = None  # for project_sync sidecar dir

# ── Tunables ────────────────────────────────────────────────────────────────

_RATE_LIMIT_SECS = 60
_DEFAULT_BRANCH_PREFIX = 'clayrune/sync'

_locks: dict[str, threading.Lock] = {}
_last_sync: dict[str, float] = {}


def register(popen_flags, startupinfo, log_activity,
             load_project, save_project, now_iso, data_root: Path):
    """Inject server helpers — called once at startup."""
    global _POPEN_FLAGS, _STARTUPINFO, _log_activity
    global _load_project, _save_project, _now_iso, _data_root
    _POPEN_FLAGS = popen_flags
    _STARTUPINFO = startupinfo
    _log_activity = log_activity
    _load_project = load_project
    _save_project = save_project
    _now_iso = now_iso
    _data_root = data_root


# ── Install ID (survives reinstall — design §11.3) ──────────────────────────

def _install_id_path() -> Path:
    """Stable location outside Clayrune's project data so a wipe+reinstall
    of Clayrune preserves the same sync-branch name."""
    home = Path.home()
    return home / '.clayrune' / 'install_id'


def get_install_id() -> str:
    """Return a stable per-install UUID, creating it on first call.

    Format: ``<sanitized-hostname>-<8 hex chars>``. The hostname half is
    human-readable (so Ron sees ``ron-laptop-a3f9c2d1`` not just hex);
    the hex half makes collisions across hostnames vanishingly unlikely.
    """
    p = _install_id_path()
    if p.exists():
        try:
            saved = p.read_text(encoding='utf-8').strip()
            if saved:
                return saved
        except Exception:
            pass
    host = _sanitize_host(socket.gethostname() or 'unknown')
    suffix = uuid.uuid4().hex[:8]
    install_id = f'{host}-{suffix}'
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(install_id, encoding='utf-8')
    except Exception as e:
        # Non-fatal; we just won't have stability across reinstalls.
        if _log_activity:
            _log_activity('', f"project_sync: failed to persist install_id: {e}")
    return install_id


_RE_HOST_SAFE = re.compile(r'[^a-z0-9-]+')


def _sanitize_host(host: str) -> str:
    h = host.lower().split('.')[0]  # strip domain if FQDN
    h = _RE_HOST_SAFE.sub('-', h).strip('-')
    return h or 'host'


# ── Sync branch naming ──────────────────────────────────────────────────────

def sync_branch_name(project: dict) -> str:
    """Derive this install's sync branch for a project.

    Prefix can be overridden per project via ``code_sync_branch_prefix``
    (design §11.8 — privacy / branding customization)."""
    prefix = (project.get('code_sync_branch_prefix') or _DEFAULT_BRANCH_PREFIX).strip()
    prefix = prefix.rstrip('/') or _DEFAULT_BRANCH_PREFIX
    # Never let an operator-set prefix masquerade as a git flag in argv.
    if prefix.startswith('-'):
        prefix = _DEFAULT_BRANCH_PREFIX
    return f'{prefix}/{get_install_id()}'


# ── Subprocess git wrapper ──────────────────────────────────────────────────

class GitError(RuntimeError):
    pass


def git_run(cwd: str, args: list[str], timeout: int = 30,
            check: bool = False) -> tuple[bool, str]:
    """Run a git subcommand in ``cwd``. Returns (ok, stdout_or_stderr).

    ``check=True`` raises GitError on non-zero exit instead of returning
    ``(False, ...)`` — useful inside higher-level helpers that want to
    propagate failures."""
    try:
        r = subprocess.run(
            ['git'] + args,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=_POPEN_FLAGS,
            startupinfo=_STARTUPINFO,
        )
        if r.returncode != 0:
            err = (r.stderr or r.stdout or 'unknown git error').strip()
            if check:
                raise GitError(err)
            return False, err
        return True, (r.stdout or '').strip()
    except subprocess.TimeoutExpired:
        msg = f'git timed out after {timeout}s'
        if check:
            raise GitError(msg)
        return False, msg
    except FileNotFoundError:
        msg = 'git not found on PATH'
        if check:
            raise GitError(msg)
        return False, msg


def is_git_repo(path: str) -> bool:
    if not path or not Path(path).is_dir():
        return False
    ok, _ = git_run(path, ['rev-parse', '--is-inside-work-tree'], timeout=5)
    return ok


# ── Hidden worktree (Option A — design §11.1) ───────────────────────────────

def worktree_path(project: dict) -> Path | None:
    """The hidden second checkout that owns the sync branch."""
    base = project.get('project_path') or ''
    if not base:
        return None
    return Path(base) / '.clayrune' / 'sync-tree'


def ensure_worktree(project: dict) -> tuple[bool, str]:
    """Create the hidden worktree on the sync branch if it doesn't exist.

    Idempotent. Returns (ok, message). On first call: creates the local
    sync branch (off current HEAD), adds a `git worktree` rooted at it,
    so the user's primary checkout is never disturbed by sync-branch
    operations.
    """
    base = project.get('project_path') or ''
    if not is_git_repo(base):
        return False, 'workspace is not a git repository'

    wt = worktree_path(project)
    if wt is None:
        return False, 'no project_path configured'

    branch = sync_branch_name(project)

    # Already exists?
    if wt.exists() and (wt / '.git').exists():
        return True, f'worktree already at {wt}'
    # Path exists but isn't a worktree — refuse rather than overwrite.
    if wt.exists():
        return False, f'{wt} exists but is not a git worktree'

    # Does the branch exist locally?
    ok, refs = git_run(base, ['branch', '--list', branch])
    have_branch = ok and refs.strip() != ''

    wt.parent.mkdir(parents=True, exist_ok=True)
    if have_branch:
        ok, msg = git_run(base, ['worktree', 'add', str(wt), branch], timeout=60)
    else:
        ok, msg = git_run(base, ['worktree', 'add', '-b', branch, str(wt)],
                          timeout=60)
    if not ok:
        return False, f'git worktree add failed: {msg}'

    # Add .clayrune/ to .gitignore in primary checkout if not already there
    # (so the hidden checkout doesn't show up as untracked noise).
    _ensure_gitignore_entry(base, '.clayrune/')

    return True, f'created worktree at {wt} on {branch}'


def _ensure_gitignore_entry(repo_root: str, entry: str) -> None:
    gi = Path(repo_root) / '.gitignore'
    try:
        existing = gi.read_text(encoding='utf-8') if gi.exists() else ''
        lines = [ln.strip() for ln in existing.splitlines()]
        if entry.strip() in lines:
            return
        with gi.open('a', encoding='utf-8') as f:
            if existing and not existing.endswith('\n'):
                f.write('\n')
            f.write(f'{entry}\n')
    except Exception:
        pass  # best-effort; gitignore polish only


# ── Status computation ──────────────────────────────────────────────────────

def _working_branch(project: dict) -> str:
    b = (project.get('code_sync_branch') or 'main').strip() or 'main'
    # Never let an operator-set value masquerade as a git flag in argv.
    return 'main' if b.startswith('-') else b


def _remote_name(project: dict) -> str:
    r = (project.get('code_sync_remote') or 'origin').strip() or 'origin'
    # Never let an operator-set value masquerade as a git flag in argv.
    return 'origin' if r.startswith('-') else r


def fetch(project: dict) -> tuple[bool, str]:
    """git fetch <remote> — pulls refs but does NOT modify the working tree."""
    base = project.get('project_path') or ''
    if not is_git_repo(base):
        return False, 'workspace is not a git repository'
    remote = _remote_name(project)
    return git_run(base, ['fetch', '--prune', remote], timeout=120)


def _ahead_behind(repo: str, local: str, upstream: str) -> tuple[int, int]:
    """Returns (ahead, behind) of ``local`` relative to ``upstream``."""
    ok, out = git_run(repo, ['rev-list', '--left-right', '--count',
                              f'{local}...{upstream}'])
    if not ok or not out:
        return 0, 0
    parts = out.split()
    if len(parts) != 2:
        return 0, 0
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return 0, 0


def _dirty(repo: str) -> bool:
    ok, out = git_run(repo, ['status', '--porcelain'])
    return ok and bool(out.strip())


def _list_sync_branches(repo: str, prefix: str, remote: str) -> list[str]:
    """All remote sync-branch refs, full short-name like ``origin/clayrune/sync/keegan-x``."""
    ok, out = git_run(repo, [
        'for-each-ref', '--format=%(refname:short)',
        f'refs/remotes/{remote}/{prefix}/',
    ])
    if not ok or not out:
        return []
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def _commits_between(repo: str, base_ref: str, head_ref: str,
                     limit: int = 50) -> list[dict]:
    """List commits reachable from head_ref but not from base_ref."""
    fmt = '%H%x1f%h%x1f%an%x1f%ae%x1f%aI%x1f%s'
    ok, out = git_run(repo, [
        'log', f'--max-count={limit}', f'--format={fmt}',
        f'{base_ref}..{head_ref}',
    ])
    if not ok or not out:
        return []
    commits = []
    for line in out.splitlines():
        parts = line.split('\x1f')
        if len(parts) != 6:
            continue
        commits.append({
            'sha': parts[0],
            'short': parts[1],
            'author_name': parts[2],
            'author_email': parts[3],
            'authored_at': parts[4],
            'subject': parts[5],
        })
    return commits


def compute_status(project: dict) -> dict:
    """Snapshot of code-sync state. Used by /api/.../code-sync/status."""
    base = project.get('project_path') or ''
    branch_prefix = (project.get('code_sync_branch_prefix')
                     or _DEFAULT_BRANCH_PREFIX).rstrip('/')
    if branch_prefix.startswith('-'):
        branch_prefix = _DEFAULT_BRANCH_PREFIX
    working = _working_branch(project)
    remote = _remote_name(project)
    my_branch = sync_branch_name(project)
    dismissed = set(project.get('code_sync_dismissed_commits') or [])

    status: dict = {
        'enabled': bool(project.get('code_sync_enabled')),
        'working_branch': working,
        'remote': remote,
        'my_sync_branch': my_branch,
        'install_id': get_install_id(),
        'is_git_repo': False,
        'ahead': 0,
        'behind': 0,
        'dirty': False,
        'incoming': [],
        'incoming_truncated_at': None,
        'last_error': project.get('code_sync_last_error'),
    }

    if not is_git_repo(base):
        status['last_error'] = 'workspace is not a git repository'
        return status
    status['is_git_repo'] = True

    upstream = f'{remote}/{working}'
    ok, _ = git_run(base, ['rev-parse', '--verify', upstream])
    if ok:
        ahead, behind = _ahead_behind(base, working, upstream)
        status['ahead'] = ahead
        status['behind'] = behind
    status['dirty'] = _dirty(base)

    sync_refs = _list_sync_branches(base, branch_prefix, remote)
    my_remote_ref = f'{remote}/{my_branch}'

    incoming: list[dict] = []
    for ref in sync_refs:
        if ref == my_remote_ref:
            continue  # don't show our own work as incoming
        # Compare against the working branch — commits on the other side's
        # sync branch but not yet on our main are "pending review".
        commits = _commits_between(base, working, ref, limit=50)
        # Filter rejected commits.
        commits = [c for c in commits if c['sha'] not in dismissed]
        if not commits:
            continue
        incoming.append({
            'branch': ref,
            'install_label': ref.split('/')[-1],  # last segment
            'commits': commits,
        })

    status['incoming'] = incoming
    return status


# ── Enable / disable ────────────────────────────────────────────────────────

def enable(project_id: str) -> tuple[bool, str]:
    """Turn on code sync for a project. Creates the hidden worktree and
    pushes the new sync branch to the remote so the other side can see
    it. Idempotent."""
    project = _load_project(project_id)
    if not project:
        return False, 'project not found'
    base = project.get('project_path') or ''
    if not is_git_repo(base):
        return False, 'workspace is not a git repository'

    ok, msg = ensure_worktree(project)
    if not ok:
        return False, msg

    project['code_sync_enabled'] = True
    project.setdefault('code_sync_branch', 'main')
    project.setdefault('code_sync_remote', 'origin')
    project.setdefault('code_sync_auto_pull', 'ff-only')
    project.setdefault('code_sync_auto_commit', False)  # off in spike
    project.setdefault('code_sync_dismissed_commits', [])
    project['code_sync_last_error'] = None
    _save_project(project_id, project)

    # Best-effort: push the new sync branch so the other side sees it.
    # Failures don't break enable — they just leave the remote ref empty
    # until the next fetch loop tries again.
    branch = sync_branch_name(project)
    remote = _remote_name(project)
    push_ok, push_msg = git_run(base, ['push', '-u', remote, branch], timeout=60)
    if push_ok:
        _log_activity(project_id,
                      f'code sync enabled — pushed initial sync branch {branch}')
    else:
        _log_activity(project_id,
                      f'code sync enabled — sync branch push deferred: {push_msg}')

    return True, msg


def disable(project_id: str) -> tuple[bool, str]:
    """Turn off code sync. Leaves the worktree + remote branch in place
    (cheap to re-enable later; manual cleanup if the user really wants
    it gone)."""
    project = _load_project(project_id)
    if not project:
        return False, 'project not found'
    project['code_sync_enabled'] = False
    _save_project(project_id, project)
    _log_activity(project_id, 'code sync disabled')
    return True, 'disabled'


# ── Top-level fetch+status (called by scheduler and /api/.../sync) ──────────

def sync_now(project_id: str) -> tuple[bool, str]:
    """Fetch and recompute status. Rate-limited per project."""
    import time

    project = _load_project(project_id)
    if not project:
        return False, 'project not found'
    if not project.get('code_sync_enabled'):
        return False, 'code sync not enabled'

    now = time.time()
    last = _last_sync.get(project_id, 0)
    if now - last < _RATE_LIMIT_SECS:
        return False, f'rate limited — wait {int(_RATE_LIMIT_SECS - (now - last))}s'

    lock = _locks.setdefault(project_id, threading.Lock())
    if not lock.acquire(blocking=False):
        return False, 'sync already in progress'

    try:
        _last_sync[project_id] = now
        ok, msg = fetch(project)
        if not ok:
            project['code_sync_last_error'] = f'fetch failed: {msg}'
            _save_project(project_id, project)
            _log_activity(project_id, f'code sync fetch failed: {msg}')
            return False, msg

        status = compute_status(project)
        project['code_sync_last_fetch'] = _now_iso()
        project['code_sync_status'] = {
            'ahead': status['ahead'],
            'behind': status['behind'],
            'dirty': status['dirty'],
            'incoming_count': sum(len(g['commits']) for g in status['incoming']),
        }
        project['code_sync_last_error'] = None
        _save_project(project_id, project)

        return True, (
            f"fetched — ahead {status['ahead']}, behind {status['behind']}, "
            f"incoming {project['code_sync_status']['incoming_count']}"
        )
    finally:
        lock.release()


# ── Reject (dismiss a remote commit from incoming list) ─────────────────────

def dismiss_commit(project_id: str, sha: str) -> tuple[bool, str]:
    """Reject a remote commit so it stops appearing in the incoming list.
    The commit stays on the other side's sync branch — this is local-only
    state."""
    project = _load_project(project_id)
    if not project:
        return False, 'project not found'
    sha = (sha or '').strip()
    if not re.fullmatch(r'[0-9a-f]{7,64}', sha):
        return False, 'invalid sha'
    dismissed = project.setdefault('code_sync_dismissed_commits', [])
    if sha not in dismissed:
        dismissed.append(sha)
    _save_project(project_id, project)
    return True, 'dismissed'
