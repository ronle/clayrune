"""MCP server URL installer — "paste URL, install" pipeline for Clayrune.

Goal: a non-technical user pastes a GitHub URL / npm package / raw JSON URL
and we (a) classify it, (b) clone or stage it, (c) extract a CC-compatible
mcpServers config from the repo, (d) collect security signals before any
install command runs, (e) on confirm, run the package manager and write the
config to ~/.claude.json (or a project .mcp.json) via the existing `mcp`
module.

Threat model: the user is already running arbitrary Claude Code in the same
shell — they've accepted that risk class. Our job is to make a different
risk class (running a random GitHub repo's install scripts) at least as
transparent as `npm install` would be in a terminal: show the SHA, show the
commands, surface dependency advisories, summarize what the code actually
does. Not a sandbox.

Staged installs live in:
    ~/.clayrune/mcp_installs/<owner>-<repo>/

Each install dir carries a `.meta.json` with `{url, sha, installed_at}` so
later re-installs / upgrades don't silently shift to a backdoored push.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


# ── Paths ────────────────────────────────────────────────────────────────────

def _home() -> Path:
    return Path(os.environ.get('USERPROFILE') or os.environ.get('HOME') or str(Path.home()))


INSTALLS_ROOT = _home() / '.clayrune' / 'mcp_installs'

# Cache for the security_scan output — keyed by install_dir.sha so a re-preview
# of the same commit doesn't re-spend Claude tokens.
_scan_cache: dict[str, dict[str, Any]] = {}
_scan_cache_lock = threading.Lock()


# ── Popen flags shared with server.py (no console windows on Windows) ────────

if sys.platform == 'win32':
    _POPEN_FLAGS = subprocess.CREATE_NO_WINDOW
    _STARTUPINFO = subprocess.STARTUPINFO()
    _STARTUPINFO.dwFlags |= subprocess.STARTF_USESHOWWINDOW
else:
    _POPEN_FLAGS = 0
    _STARTUPINFO = None


def _resolve_npm() -> str | None:
    """Find `npm` even when the server's PATH doesn't include the Node bin dir
    (common when MC is launched from Tauri / a Start-menu shortcut on Windows
    that doesn't inherit user shell PATH). Returns None if not found."""
    for candidate in ('npm', 'npm.cmd'):
        found = shutil.which(candidate)
        if found:
            return found
    if sys.platform == 'win32':
        # First, try resolving via `node` — npm ships next to it.
        node = shutil.which('node') or shutil.which('node.exe')
        if node:
            node_dir = Path(node).resolve().parent
            for n in ('npm.cmd', 'npm.exe'):
                p = node_dir / n
                if p.exists():
                    return str(p)
        candidates = [
            Path(r'C:\Program Files\nodejs\npm.cmd'),
            Path(r'C:\Program Files (x86)\nodejs\npm.cmd'),
            Path(os.environ.get('APPDATA', '')) / 'npm' / 'npm.cmd',
            Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs' / 'nodejs' / 'npm.cmd',
        ]
    else:
        home = _home()
        candidates = [
            Path('/usr/local/bin/npm'),
            Path('/opt/homebrew/bin/npm'),
            home / '.npm-global' / 'bin' / 'npm',
            home / '.local' / 'bin' / 'npm',
            home / '.nvm' / 'current' / 'bin' / 'npm',
        ]
    for c in candidates:
        try:
            if c.exists():
                return str(c)
        except Exception:
            pass
    return None


def _run(cmd: list[str], cwd: str | None = None, timeout: int = 60,
         env: dict[str, str] | None = None) -> tuple[int, str, str]:
    """Sync subprocess.run wrapper that captures text output without popping a
    console window on Windows. Returns (rc, stdout, stderr)."""
    full_env = {**os.environ, **(env or {})}
    try:
        proc = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
            env=full_env, creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
        )
    except FileNotFoundError as e:
        return 127, '', f'{cmd[0]} not on PATH: {e}'
    except subprocess.TimeoutExpired as e:
        return 124, '', f'timeout after {timeout}s: {e}'
    return proc.returncode, proc.stdout or '', proc.stderr or ''


# ── URL classification ───────────────────────────────────────────────────────

_GITHUB_RE = re.compile(
    r'^https?://github\.com/([A-Za-z0-9._-]+)/([A-Za-z0-9._-]+?)'
    r'(?:\.git)?(?:/(?:tree|blob)/([^/]+))?/?$'
)
_NPM_URL_RE = re.compile(
    r'^https?://(?:www\.)?npmjs\.com/package/((?:@[A-Za-z0-9._-]+/)?[A-Za-z0-9._-]+)/?'
)
_BARE_NPM_RE = re.compile(r'^(@[A-Za-z0-9._-]+/)?[A-Za-z0-9._-]+$')
_RAW_JSON_RE = re.compile(r'^https?://.+\.json(?:\?.*)?$', re.I)


def classify_url(raw: str) -> dict[str, Any]:
    """Classify the user's pasted input. Never throws; unknown is a valid kind."""
    s = (raw or '').strip()
    if not s:
        return {'kind': 'unknown', 'reason': 'empty input'}

    m = _GITHUB_RE.match(s)
    if m:
        return {
            'kind': 'git', 'url': f'https://github.com/{m.group(1)}/{m.group(2)}.git',
            'owner': m.group(1), 'repo': m.group(2), 'ref': m.group(3),
        }

    m = _NPM_URL_RE.match(s)
    if m:
        return {'kind': 'npm', 'package': m.group(1)}

    if _RAW_JSON_RE.match(s):
        return {'kind': 'json', 'url': s}

    if _BARE_NPM_RE.match(s):
        return {'kind': 'npm', 'package': s}

    if s.startswith('git@') or s.endswith('.git'):
        return {'kind': 'git', 'url': s}

    return {'kind': 'unknown', 'reason': f"can't classify {s!r}"}


# ── GitHub signals (gh CLI if available, raw HTTP fallback) ──────────────────

def fetch_github_signals(owner: str, repo: str) -> dict[str, Any]:
    """Pull repo-trust signals — stars, age, last-commit date, license,
    archived flag, open issues, default branch. Best-effort: returns
    `{available: False, reason}` if the lookup fails."""
    api_url = f'https://api.github.com/repos/{owner}/{repo}'
    headers = {'Accept': 'application/vnd.github+json',
               'User-Agent': 'clayrune-mcp-installer'}
    token = os.environ.get('GITHUB_TOKEN') or os.environ.get('GH_TOKEN')
    if token:
        headers['Authorization'] = f'Bearer {token}'

    try:
        req = urllib.request.Request(api_url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode('utf-8'))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as e:
        return {'available': False, 'reason': str(e)}
    except Exception as e:
        return {'available': False, 'reason': f'parse error: {e}'}

    created = data.get('created_at') or ''
    pushed = data.get('pushed_at') or ''
    age_days = None
    last_commit_days = None
    try:
        if created:
            t = datetime.fromisoformat(created.replace('Z', '+00:00'))
            age_days = (datetime.now(timezone.utc) - t).days
        if pushed:
            t = datetime.fromisoformat(pushed.replace('Z', '+00:00'))
            last_commit_days = (datetime.now(timezone.utc) - t).days
    except Exception:
        pass

    return {
        'available': True,
        'full_name': data.get('full_name'),
        'description': data.get('description'),
        'stars': data.get('stargazers_count') or 0,
        'forks': data.get('forks_count') or 0,
        'open_issues': data.get('open_issues_count') or 0,
        'archived': bool(data.get('archived')),
        'license': (data.get('license') or {}).get('name'),
        'default_branch': data.get('default_branch') or 'main',
        'created_at': created,
        'pushed_at': pushed,
        'age_days': age_days,
        'last_commit_days': last_commit_days,
    }


# ── Git clone + SHA pin ──────────────────────────────────────────────────────

def _slugify(owner: str, repo: str) -> str:
    return re.sub(r'[^A-Za-z0-9_-]+', '-', f'{owner}-{repo}').strip('-') or 'mcp-server'


def _rmtree_force(path: Path) -> None:
    """Robust rmtree that handles the Windows read-only quirk in `.git/objects`.

    git marks pack index files read-only; Python's default `shutil.rmtree`
    raises `PermissionError` on those. The onexc/onerror callback clears
    the read-only bit and retries — handles `.git/objects/pack/*.idx` and
    everything else of the same shape."""
    import stat

    def _onerror(func, target, exc_info):
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except Exception:
            pass

    # `onexc` is the modern (3.12+) kwarg; `onerror` is the legacy alias. Try
    # the new one first so we don't trip a DeprecationWarning.
    try:
        shutil.rmtree(path, onexc=_onerror)
    except TypeError:
        shutil.rmtree(path, onerror=_onerror)


def stage_clone(url: str, owner: str, repo: str, ref: str | None = None) -> dict[str, Any]:
    """Shallow-clone the repo and pin to a fixed SHA. Returns
    `{install_dir, sha, default_branch}` or raises on failure."""
    INSTALLS_ROOT.mkdir(parents=True, exist_ok=True)
    install_dir = INSTALLS_ROOT / _slugify(owner, repo)

    if install_dir.exists():
        # Stale staging from a previous preview — wipe and re-clone so we know
        # the SHA we hand back is fresh.
        try:
            _rmtree_force(install_dir)
        except Exception as e:
            raise RuntimeError(f'failed to clean stale install dir: {e}')

    branch_args = ['--branch', ref] if ref else []
    # `--` terminates option parsing so a hostile URL can't smuggle a git flag
    # (e.g. --upload-pack=…) into the positional slots.
    rc, out, err = _run(
        ['git', 'clone', '--depth', '1', *branch_args, '--', url, str(install_dir)],
        timeout=120,
    )
    if rc != 0:
        raise RuntimeError(f'git clone failed: {err.strip() or out.strip() or rc}')

    rc, sha, _ = _run(['git', 'rev-parse', 'HEAD'], cwd=str(install_dir), timeout=10)
    sha = sha.strip()
    if rc != 0 or not sha:
        raise RuntimeError('git rev-parse HEAD failed in fresh clone')

    rc, default_branch, _ = _run(
        ['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
        cwd=str(install_dir), timeout=10,
    )
    default_branch = default_branch.strip() or (ref or 'main')

    meta = {
        'url': url, 'sha': sha, 'staged_at': _now_iso(),
        'ref': ref, 'default_branch': default_branch,
    }
    (install_dir / '.meta.json').write_text(
        json.dumps(meta, indent=2), encoding='utf-8',
    )

    return {'install_dir': str(install_dir), 'sha': sha,
            'default_branch': default_branch}


def cleanup_staged(install_dir: str) -> bool:
    """Wipe a staged clone the user decided not to install. Safe to call on
    already-deleted dirs. Returns True if something was deleted."""
    p = Path(install_dir).resolve()
    # Defense in depth: only allow deletes under the installs root.
    try:
        p.relative_to(INSTALLS_ROOT.resolve())
    except ValueError:
        raise ValueError(f'refusing to clean dir outside {INSTALLS_ROOT}')
    if not p.exists():
        return False
    _rmtree_force(p)
    return True


# ── Config extraction (tiers 1–3) ────────────────────────────────────────────

_EXAMPLE_FILENAMES = (
    'claude_desktop_config.json', 'mcp.json', '.mcp.json',
    'config/claude_desktop_config.json',
    'examples/claude_desktop_config.json', 'examples/mcp.json',
    'docs/claude_desktop_config.json',
)


def _find_mcp_servers_in_obj(obj: Any) -> dict[str, Any] | None:
    """Walk a JSON-decoded value looking for the first `mcpServers` object."""
    if isinstance(obj, dict):
        ms = obj.get('mcpServers')
        if isinstance(ms, dict) and ms:
            return ms
        for v in obj.values():
            found = _find_mcp_servers_in_obj(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_mcp_servers_in_obj(v)
            if found:
                return found
    return None


def _extract_from_example_files(install_dir: Path) -> dict[str, Any] | None:
    for rel in _EXAMPLE_FILENAMES:
        p = install_dir / rel
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text(encoding='utf-8'))
        except Exception:
            continue
        servers = _find_mcp_servers_in_obj(data)
        if servers:
            return servers
    return None


_README_NAMES = ('README.md', 'README.MD', 'Readme.md', 'README.rst', 'README')
_JSON_FENCE_RE = re.compile(
    r'```(?:json|jsonc|JSON)?\s*\n(.*?)\n```', re.DOTALL,
)


def _extract_from_readme(install_dir: Path) -> dict[str, Any] | None:
    for name in _README_NAMES:
        p = install_dir / name
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding='utf-8', errors='replace')
        except Exception:
            continue
        for m in _JSON_FENCE_RE.finditer(text):
            block = m.group(1).strip()
            if '"mcpServers"' not in block and 'mcpServers' not in block:
                continue
            # Strip line comments (jsonc style) — vendors love sprinkling them.
            stripped = re.sub(r'(?m)^\s*//.*$', '', block)
            try:
                data = json.loads(stripped)
            except Exception:
                continue
            servers = _find_mcp_servers_in_obj(data)
            if servers:
                return servers
    return None


def _resolve_claude_bin() -> str:
    """Mirror server.py:_resolve_claude — kept local so this module stays
    importable on machines that don't have server.py on the path."""
    found = shutil.which('claude')
    if found:
        return found
    if sys.platform == 'win32':
        candidates = [
            Path(os.environ.get('APPDATA', '')) / 'npm' / 'claude.cmd',
            _home() / '.claude' / 'bin' / 'claude.cmd',
            _home() / '.claude' / 'bin' / 'claude.exe',
        ]
    else:
        candidates = [
            _home() / '.claude' / 'bin' / 'claude',
            _home() / '.local' / 'bin' / 'claude',
            Path('/usr/local/bin/claude'),
            Path('/opt/homebrew/bin/claude'),
        ]
    for c in candidates:
        try:
            if c.exists():
                return str(c)
        except Exception:
            pass
    return 'claude'


def _extract_via_claude(install_dir: Path) -> dict[str, Any] | None:
    """Tier 3: ask Claude to read the README and return the mcpServers
    object. Costs a few thousand tokens; only invoked when tiers 1 and 2
    miss."""
    readme = None
    for name in _README_NAMES:
        p = install_dir / name
        if p.is_file():
            try:
                readme = p.read_text(encoding='utf-8', errors='replace')[:30000]
                break
            except Exception:
                pass
    if not readme:
        return None

    prompt = (
        "Extract the MCP server configuration from the README below. Return "
        "ONLY a JSON object of the form "
        "{\"mcpServers\": {\"<name>\": {\"command\": \"...\", \"args\": [...], "
        "\"env\": {...}}}} — no prose, no markdown fences. If the server is "
        "http/sse instead of stdio, use {\"type\":\"http\"|\"sse\",\"url\":...,"
        "\"headers\":{...}}. If the README doesn't contain a usable config, "
        "return {\"mcpServers\": {}}.\n\nREADME:\n\n" + readme
    )
    rc, out, err = _run(
        [_resolve_claude_bin(), '-p', prompt, '--max-turns', '1',
         '--output-format', 'json'],
        timeout=60,
    )
    if rc != 0 or not out.strip():
        return None
    try:
        envelope = json.loads(out)
        # Output-format json wraps the model's reply under `result` (or a
        # similar key depending on CC version) — try a few.
        text = (envelope.get('result') or envelope.get('response')
                or envelope.get('text') or '').strip()
        if not text:
            return None
        # Strip code fences if the model added them despite instructions.
        text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip(), flags=re.M)
        data = json.loads(text)
    except Exception:
        return None
    servers = _find_mcp_servers_in_obj(data)
    return servers or None


def _absolutize_paths(servers: dict[str, Any], install_dir: Path) -> dict[str, Any]:
    """Replace `/path/to/<repo>` and similar placeholders in args with the
    real install_dir so the config Just Works when written to ~/.claude.json."""
    install_str = str(install_dir).replace('\\', '/')
    placeholder_re = re.compile(
        r'(?:/path/to/[A-Za-z0-9._-]+|<path[ _-]?to[ _-]?[A-Za-z0-9._-]+>|'
        r'\$REPO_DIR|\$\{REPO_DIR\}|\$\{INSTALL_DIR\}|\{install_dir\})',
        re.I,
    )
    out: dict[str, Any] = {}
    for name, cfg in servers.items():
        cfg2 = dict(cfg)
        args = cfg2.get('args')
        if isinstance(args, list):
            cfg2['args'] = [placeholder_re.sub(install_str, str(a)) for a in args]
        # Sometimes vendors write `command: /path/to/.../bin/foo`.
        cmd = cfg2.get('command')
        if isinstance(cmd, str):
            cfg2['command'] = placeholder_re.sub(install_str, cmd)
        out[name] = cfg2
    return out


def extract_config(install_dir: str, allow_claude_fallback: bool = True) -> dict[str, Any]:
    """Try three tiers in order. Returns
    `{servers: {name: cfg}, source_tier: 1|2|3, name_hint: str|None}` or
    `{servers: {}, source_tier: 0}` if nothing was found."""
    p = Path(install_dir)
    servers = _extract_from_example_files(p)
    tier = 1
    if not servers:
        servers = _extract_from_readme(p)
        tier = 2
    if not servers and allow_claude_fallback:
        servers = _extract_via_claude(p)
        tier = 3
    if not servers:
        return {'servers': {}, 'source_tier': 0, 'name_hint': p.name}

    servers = _absolutize_paths(servers, p)
    # Pick the first server name as the hint for the form.
    name_hint = next(iter(servers.keys()), p.name)
    return {'servers': servers, 'source_tier': tier, 'name_hint': name_hint}


# ── Secret detection ─────────────────────────────────────────────────────────

_SECRET_PLACEHOLDER_RE = re.compile(
    r'(?:paste[_-]?(?:here|me)|your[_-]?(?:api[_-]?key|token|secret|password)|'
    r'replace[_-]?me|<your[_-]?[a-z]+>|example[_-]?(?:key|token)|'
    r'sk-xxx+|insert[_-]?here|change[_-]?me)',
    re.I,
)
_SECRETISH_KEY_RE = re.compile(r'(?:api[_-]?key|token|secret|password|passphrase|access[_-]?key)$', re.I)


def detect_secrets(servers: dict[str, Any]) -> list[dict[str, str]]:
    """Return one entry per env var the user probably needs to fill in.

    Triggers on (a) placeholder values that look obviously fake, (b)
    empty-string env vars on keys whose names suggest a credential."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for srv_name, cfg in servers.items():
        env = cfg.get('env') or {}
        if not isinstance(env, dict):
            continue
        for k, v in env.items():
            if k in seen:
                continue
            value = str(v or '')
            looks_secretish = bool(_SECRETISH_KEY_RE.search(k))
            placeholder = bool(_SECRET_PLACEHOLDER_RE.search(value)) if value else False
            empty_and_credential = (not value) and looks_secretish
            if placeholder or empty_and_credential:
                out.append({
                    'server': srv_name, 'key': k,
                    'current': value, 'hint': k.replace('_', ' ').lower(),
                })
                seen.add(k)
    return out


# ── Dependency audit (npm / pip) ─────────────────────────────────────────────

def dependency_audit(install_dir: str) -> dict[str, Any]:
    """Run `npm audit --json` or `pip-audit -f json` if the corresponding
    lock/manifest exists. Returns a flat summary suitable for the UI."""
    p = Path(install_dir)
    if (p / 'package.json').is_file():
        return _npm_audit(p)
    if (p / 'pyproject.toml').is_file() or (p / 'requirements.txt').is_file():
        return _pip_audit(p)
    return {'kind': 'none', 'available': False,
            'reason': 'no package.json / pyproject.toml / requirements.txt'}


def _npm_audit(p: Path) -> dict[str, Any]:
    npm = _resolve_npm()
    if not npm:
        return {'kind': 'npm', 'available': False, 'reason': 'npm not found (install Node.js)'}
    # npm audit needs node_modules OR a lockfile. With --package-lock-only it
    # can audit without installing — but only if a lockfile exists. Generate
    # one on demand so we audit before the user commits to install.
    if not (p / 'package-lock.json').is_file() and not (p / 'npm-shrinkwrap.json').is_file():
        rc, _, _ = _run(
            [npm, 'install', '--package-lock-only', '--ignore-scripts',
             '--no-audit', '--no-fund'],
            cwd=str(p), timeout=120,
        )
        if rc != 0:
            return {'kind': 'npm', 'available': False,
                    'reason': 'failed to generate package-lock for audit'}
    rc, out, err = _run([npm, 'audit', '--json'], cwd=str(p), timeout=90)
    # npm audit exits non-zero when vulns are found — that's expected.
    if not out.strip():
        return {'kind': 'npm', 'available': False, 'reason': err.strip()[:200] or 'no output'}
    try:
        data = json.loads(out)
    except Exception as e:
        return {'kind': 'npm', 'available': False, 'reason': f'parse error: {e}'}
    meta = data.get('metadata') or {}
    counts = (meta.get('vulnerabilities') or {})
    findings: list[dict[str, Any]] = []
    for pkg, info in (data.get('vulnerabilities') or {}).items():
        sev = (info.get('severity') or '').lower()
        if sev not in ('high', 'critical', 'moderate', 'low'):
            continue
        # `via` is either a list of strings (transitive — names of upstream
        # vulnerable packages) or a list of advisory objects on the direct
        # offender. Pick up structured advisory data when we have it.
        advisories: list[dict[str, Any]] = []
        upstream_chain: list[str] = []
        for v in (info.get('via') or []):
            if isinstance(v, dict):
                advisories.append({
                    'title': v.get('title') or '',
                    'url': v.get('url') or '',
                    'cve': v.get('cve') or v.get('cves') or [],
                    'source': v.get('source'),
                    'range': v.get('range') or '',
                    'severity': (v.get('severity') or '').lower(),
                })
            elif isinstance(v, str):
                upstream_chain.append(v)
        # Is this a top-level dep or pulled in transitively? `isDirect: true`
        # tells us; otherwise infer from `effects` (what depends on it).
        is_direct = bool(info.get('isDirect'))
        effects = info.get('effects') or []
        fix = info.get('fixAvailable')
        if isinstance(fix, dict):
            fix_summary = (
                f"upgrade {fix.get('name','?')} to {fix.get('version','?')}"
            )
            fix_is_breaking = bool(fix.get('isSemVerMajor'))
        elif fix is True:
            fix_summary = 'npm audit fix'
            fix_is_breaking = False
        elif fix is False:
            fix_summary = 'no fix available yet'
            fix_is_breaking = False
        else:
            fix_summary = ''
            fix_is_breaking = False
        # Plain-English rationale for the user.
        if advisories:
            primary = advisories[0]
            why = primary.get('title') or 'flagged by npm advisory database'
        elif upstream_chain:
            why = f"transitive vulnerability via {' → '.join(upstream_chain[:3])}"
        else:
            why = 'flagged by npm advisory database'
        findings.append({
            'package': pkg,
            'severity': sev,
            'why': why,
            'is_direct': is_direct,
            'effects': effects[:5],
            'range': info.get('range') or '',
            'fix': fix_summary,
            'fix_is_breaking': fix_is_breaking,
            'advisories': advisories[:3],
            'upstream': upstream_chain[:5],
        })
    # Sort high/critical first so they render at the top.
    sev_order = {'critical': 0, 'high': 1, 'moderate': 2, 'low': 3}
    findings.sort(key=lambda f: sev_order.get(f['severity'], 9))
    return {
        'kind': 'npm', 'available': True,
        'total': sum(counts.values()) if isinstance(counts, dict) else 0,
        'critical': counts.get('critical', 0),
        'high': counts.get('high', 0),
        'moderate': counts.get('moderate', 0),
        'low': counts.get('low', 0),
        'findings': findings[:30],
    }


def _pip_audit(p: Path) -> dict[str, Any]:
    if not shutil.which('pip-audit'):
        return {'kind': 'pip', 'available': False, 'reason': 'pip-audit not on PATH (pip install pip-audit)'}
    rc, out, _ = _run(['pip-audit', '-f', 'json'], cwd=str(p), timeout=90)
    if rc not in (0, 1) or not out.strip():
        return {'kind': 'pip', 'available': False, 'reason': 'pip-audit failed'}
    try:
        data = json.loads(out)
    except Exception:
        return {'kind': 'pip', 'available': False, 'reason': 'parse error'}
    findings: list[dict[str, Any]] = []
    for entry in (data.get('dependencies') or []):
        pkg_name = entry.get('name')
        for v in entry.get('vulns') or []:
            vid = v.get('id') or ''
            desc = (v.get('description') or '').strip()
            fixed_in = v.get('fix_versions') or []
            findings.append({
                'package': pkg_name,
                'severity': 'high',  # pip-audit doesn't always provide severity
                'why': desc[:200] if desc else f'advisory {vid}',
                'is_direct': True,  # pip-audit doesn't expose dep depth
                'effects': [],
                'range': entry.get('version') or '',
                'fix': f"upgrade to {fixed_in[0]}" if fixed_in else 'no fix available yet',
                'fix_is_breaking': False,
                'advisories': [{
                    'title': desc[:200], 'url': '', 'cve': [vid] if vid.startswith('CVE') else [],
                    'source': vid, 'range': '', 'severity': 'high',
                }] if vid or desc else [],
                'upstream': [],
            })
    return {
        'kind': 'pip', 'available': True,
        'total': len(findings),
        'critical': 0, 'high': len(findings),
        'moderate': 0, 'low': 0,
        'findings': findings[:30],
    }


# ── Static security scan (Claude-powered) ────────────────────────────────────

_SCAN_PROMPT = (
    "You're auditing an MCP server's source for a non-technical user about to "
    "install it. Read the files below and return JSON of this shape:\n\n"
    "{\n"
    '  "network": [list of outbound destinations (hostnames or URL patterns)],\n'
    '  "filesystem": [list of paths/patterns the code reads or writes OUTSIDE '
    "its own install dir],\n"
    '  "shell": [list of external commands invoked via child_process/subprocess],\n'
    '  "secrets": [list of env vars read],\n'
    '  "flags": [list of short strings, one per genuinely suspicious behavior '
    "that doesn't match the README's stated purpose — obfuscated strings, "
    "exfil to unrelated hosts, eval of remote content, etc.],\n"
    '  "summary": "one-sentence plain-English description of what this server does"\n'
    "}\n\n"
    "Be concrete (names + values), don't speculate. If a category is empty, "
    "return an empty list. Return ONLY JSON, no prose."
)


def _gather_source_snippets(install_dir: Path, max_bytes: int = 20000) -> str:
    """Concatenate a representative slice of source for the scan. Caps total
    bytes so the prompt stays cheap."""
    keep_ext = {'.js', '.mjs', '.ts', '.py', '.go', '.rs'}
    files: list[Path] = []
    for root, dirs, names in os.walk(install_dir):
        # Skip vendor / build dirs.
        dirs[:] = [d for d in dirs if d not in (
            'node_modules', '.git', 'dist', 'build', '.venv', 'venv',
            '__pycache__', '.next', '.nuxt', 'out', 'target',
        )]
        for n in names:
            p = Path(root) / n
            if p.suffix.lower() in keep_ext:
                files.append(p)
    files.sort(key=lambda p: (len(p.relative_to(install_dir).parts), p.name))

    chunks: list[str] = []
    used = 0
    for f in files:
        try:
            text = f.read_text(encoding='utf-8', errors='replace')
        except Exception:
            continue
        rel = f.relative_to(install_dir).as_posix()
        snippet = f'\n\n=== {rel} ===\n{text[:8000]}'
        if used + len(snippet) > max_bytes:
            break
        chunks.append(snippet)
        used += len(snippet)
    return ''.join(chunks) or '(no source files found)'


def security_scan(install_dir: str, sha: str) -> dict[str, Any]:
    """One Claude call summarizing what the server does. Cached on (dir, sha)."""
    key = f'{install_dir}@{sha}'
    with _scan_cache_lock:
        if key in _scan_cache:
            return _scan_cache[key]

    src = _gather_source_snippets(Path(install_dir))
    prompt = _SCAN_PROMPT + "\n\nSOURCE:\n" + src
    rc, out, _ = _run(
        [_resolve_claude_bin(), '-p', prompt, '--max-turns', '1',
         '--output-format', 'json'],
        timeout=90,
    )
    if rc != 0 or not out.strip():
        result = {'available': False, 'reason': f'claude rc={rc}'}
    else:
        try:
            envelope = json.loads(out)
            text = (envelope.get('result') or envelope.get('response')
                    or envelope.get('text') or '').strip()
            text = re.sub(r'^```(?:json)?\s*|\s*```$', '', text, flags=re.M)
            parsed = json.loads(text)
            result = {'available': True, **parsed}
        except Exception as e:
            result = {'available': False, 'reason': f'parse: {e}'}

    with _scan_cache_lock:
        _scan_cache[key] = result
    return result


# ── Install runner (streamed) ────────────────────────────────────────────────

def detect_install_kind(install_dir: str) -> str:
    p = Path(install_dir)
    if (p / 'package.json').is_file():
        return 'npm'
    if (p / 'pyproject.toml').is_file():
        return 'pyproject'
    if (p / 'requirements.txt').is_file():
        return 'requirements'
    return 'none'


def install_commands(install_dir: str) -> list[list[str]]:
    """Return the actual command list(s) we'll run. Surface to the UI so the
    user can read them before clicking Install."""
    kind = detect_install_kind(install_dir)
    if kind == 'npm':
        npm = _resolve_npm() or 'npm'
        return [[npm, 'install', '--no-audit', '--no-fund']]
    if kind == 'pyproject':
        if shutil.which('uv'):
            return [['uv', 'sync']]
        return [[sys.executable, '-m', 'pip', 'install', '-e', '.']]
    if kind == 'requirements':
        return [[sys.executable, '-m', 'pip', 'install', '-r', 'requirements.txt']]
    return []


def stream_install(install_dir: str, emit: Callable[[str], None]) -> int:
    """Run the install commands, streaming combined stdout/stderr to `emit`.
    Returns the last non-zero rc, or 0 if all succeeded."""
    cmds = install_commands(install_dir)
    if not cmds:
        emit('[no install needed — no package.json / pyproject.toml / requirements.txt]\n')
        return 0
    for cmd in cmds:
        emit(f'\n$ {" ".join(cmd)}\n')
        try:
            proc = subprocess.Popen(
                cmd, cwd=install_dir, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, bufsize=1,
                creationflags=_POPEN_FLAGS, startupinfo=_STARTUPINFO,
            )
        except FileNotFoundError:
            emit(f'[command not found: {cmd[0]}]\n')
            return 127
        assert proc.stdout is not None
        for line in proc.stdout:
            emit(line)
        rc = proc.wait()
        if rc != 0:
            emit(f'[exited with code {rc}]\n')
            return rc
    return 0


# ── Misc ─────────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def apply_secrets_to_config(servers: dict[str, Any], secrets: dict[str, str]) -> dict[str, Any]:
    """Replace env values for keys the user supplied in the preview form."""
    out: dict[str, Any] = {}
    for name, cfg in servers.items():
        cfg2 = dict(cfg)
        env = dict(cfg2.get('env') or {})
        for k, v in (secrets or {}).items():
            if k in env:
                env[k] = v
        if env:
            cfg2['env'] = env
        out[name] = cfg2
    return out
