"""MCP (Model Context Protocol) server config layer for Mission Control.

MCP servers are extra tool providers Claude Code connects to. CC reads their
config from two native locations — MC's job is purely management (list, add,
edit, delete). No preamble injection.

  ~/.claude.json                       ← global; `mcpServers` top-level key
  <project_path>/.mcp.json             ← project-committed (team-shared)

A project entry with the same name as a global entry shadows the global (the
project's `.mcp.json` wins at session start). The list endpoint surfaces a
`shadowed_by_project` flag so the UI can badge it.

Three transport types supported in v1:

  stdio → {"command": "...", "args": [...], "env": {...}}
  http  → {"type": "http", "url": "...", "headers": {...}}
  sse   → {"type": "sse",  "url": "...", "headers": {...}}

Atomic writes throughout — `~/.claude.json` carries Claude Code's own state,
so we read-modify-write under a lock and never truncate other keys.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ── Paths ────────────────────────────────────────────────────────────────────

def _home() -> Path:
    return Path(os.environ.get('USERPROFILE') or os.environ.get('HOME') or str(Path.home()))


GLOBAL_CLAUDE_JSON = _home() / '.claude.json'

PROJECT_MCP_FILENAME = '.mcp.json'


def project_mcp_path(project_path: str | os.PathLike) -> Path:
    return Path(project_path) / PROJECT_MCP_FILENAME


# Serialize all writes to ~/.claude.json — it's a fat shared file owned by CC.
_global_write_lock = threading.Lock()


# ── Name validation ──────────────────────────────────────────────────────────

# MCP server names in the wild use letters, digits, dashes, underscores, and
# dots (e.g. `filesystem`, `github-mcp`, `mcp.local.dev`). Reject whitespace,
# slashes, and anything that would break a JSON key on display.
_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')


def validate_name(name: str) -> str | None:
    if not name or not name.strip():
        return 'Name is required'
    if not _NAME_RE.match(name):
        return 'Name must start with a letter/digit and contain only letters, digits, dots, dashes, underscores (max 64 chars)'
    return None


# ── Config validation ────────────────────────────────────────────────────────

VALID_TRANSPORTS = ('stdio', 'http', 'sse')


def _infer_transport(cfg: dict[str, Any]) -> str:
    """Best-effort transport inference — explicit `type` wins, else heuristic."""
    t = (cfg.get('type') or '').strip().lower()
    if t in VALID_TRANSPORTS:
        return t
    if cfg.get('command'):
        return 'stdio'
    if cfg.get('url'):
        # CC defaults to HTTP when only `url` is present and no `type`.
        return 'http'
    return 'stdio'


def normalize_config(transport: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """Return a CC-compatible config dict for the given transport.

    Strips unknown keys, drops empty optional fields, and ensures the right
    `type` marker (omitted for stdio, since stdio is CC's default).
    """
    transport = (transport or '').strip().lower()
    if transport not in VALID_TRANSPORTS:
        raise ValueError(f'transport must be one of {VALID_TRANSPORTS}')

    out: dict[str, Any] = {}

    if transport == 'stdio':
        command = (cfg.get('command') or '').strip()
        if not command:
            raise ValueError('stdio transport requires a command')
        out['command'] = command
        args = cfg.get('args') or []
        if isinstance(args, str):
            # Accept a single string and split on whitespace — convenience for the UI.
            args = [a for a in args.split() if a]
        if not isinstance(args, list):
            raise ValueError('args must be a list of strings')
        args = [str(a) for a in args if str(a).strip()]
        if args:
            out['args'] = args
        env = cfg.get('env') or {}
        if not isinstance(env, dict):
            raise ValueError('env must be an object of string→string')
        env = {str(k): str(v) for k, v in env.items() if str(k).strip()}
        if env:
            out['env'] = env
        return out

    # http / sse share the same shape.
    out['type'] = transport
    url = (cfg.get('url') or '').strip()
    if not url:
        raise ValueError(f'{transport} transport requires a url')
    if not (url.startswith('http://') or url.startswith('https://')):
        raise ValueError('url must start with http:// or https://')
    out['url'] = url
    headers = cfg.get('headers') or {}
    if not isinstance(headers, dict):
        raise ValueError('headers must be an object of string→string')
    headers = {str(k): str(v) for k, v in headers.items() if str(k).strip()}
    if headers:
        out['headers'] = headers
    return out


# ── Atomic JSON IO ───────────────────────────────────────────────────────────

def _atomic_write_json(path: Path, data: Any) -> None:
    """Write JSON atomically next to `path` (tmp file → rename).

    Keeps indentation/encoding consistent with CC's own writes. Creates the
    parent dir if missing.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, ensure_ascii=False) + '\n'
    # NamedTemporaryFile on Windows holds an exclusive handle; use delete=False
    # + manual rename to dodge that.
    fd, tmp_name = tempfile.mkstemp(prefix='.mcp-', suffix='.json.tmp', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(text)
        os.replace(tmp_name, str(path))
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _read_json_or_empty(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding='utf-8')
    except Exception:
        return {}
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


# ── Server CRUD — global scope ───────────────────────────────────────────────

def _read_global_servers() -> dict[str, Any]:
    data = _read_json_or_empty(GLOBAL_CLAUDE_JSON)
    servers = data.get('mcpServers')
    return servers if isinstance(servers, dict) else {}


def _write_global_servers(mutate) -> None:
    """Read-modify-write `~/.claude.json` under the global lock.

    `mutate` receives the current mcpServers dict and mutates it in place. We
    only touch the `mcpServers` key — every other key is preserved verbatim.
    """
    with _global_write_lock:
        data = _read_json_or_empty(GLOBAL_CLAUDE_JSON)
        servers = data.get('mcpServers')
        if not isinstance(servers, dict):
            servers = {}
        mutate(servers)
        data['mcpServers'] = servers
        _atomic_write_json(GLOBAL_CLAUDE_JSON, data)


# ── Server CRUD — project scope ──────────────────────────────────────────────

def _read_project_servers(project_path: str) -> dict[str, Any]:
    data = _read_json_or_empty(project_mcp_path(project_path))
    servers = data.get('mcpServers')
    return servers if isinstance(servers, dict) else {}


def _write_project_servers(project_path: str, mutate) -> None:
    path = project_mcp_path(project_path)
    # Per-project file — no contention with other projects, so the global lock
    # is sufficient (and keeps us simple).
    with _global_write_lock:
        data = _read_json_or_empty(path)
        servers = data.get('mcpServers')
        if not isinstance(servers, dict):
            servers = {}
        mutate(servers)
        data['mcpServers'] = servers
        _atomic_write_json(path, data)


# ── Public API ───────────────────────────────────────────────────────────────

def _to_record(name: str, cfg: dict[str, Any], scope: str, project_id: str | None, source_path: Path) -> dict[str, Any]:
    transport = _infer_transport(cfg)
    rec: dict[str, Any] = {
        'name': name,
        'scope': scope,
        'project_id': project_id,
        'transport': transport,
        'config': cfg,
        'path': str(source_path),
    }
    try:
        st = source_path.stat()
        rec['mtime'] = st.st_mtime
        rec['mtime_iso'] = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        rec['mtime'] = None
        rec['mtime_iso'] = None
    # Convenience fields for the UI — flat preview of the most relevant field.
    if transport == 'stdio':
        cmd = cfg.get('command') or ''
        args = cfg.get('args') or []
        rec['preview'] = (cmd + ' ' + ' '.join(args)).strip()
    else:
        rec['preview'] = cfg.get('url') or ''
    return rec


def list_servers(
    project_path: str | None = None,
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    """List MCP servers across global + (optionally) one project's `.mcp.json`.

    Annotates shadowing: a global server with the same name as a project
    server gets `shadowed_by_project=True`.
    """
    out: list[dict[str, Any]] = []

    globals_ = _read_global_servers()
    for name, cfg in sorted(globals_.items()):
        if not isinstance(cfg, dict):
            continue
        out.append(_to_record(name, cfg, 'global', None, GLOBAL_CLAUDE_JSON))

    proj_names: set[str] = set()
    if project_path:
        proj = _read_project_servers(project_path)
        for name, cfg in sorted(proj.items()):
            if not isinstance(cfg, dict):
                continue
            out.append(_to_record(name, cfg, 'project', project_id, project_mcp_path(project_path)))
            proj_names.add(name)

    for r in out:
        if r['scope'] == 'global' and r['name'] in proj_names:
            r['shadowed_by_project'] = True

    return out


def read_server(
    scope: str,
    name: str,
    project_path: str | None = None,
    project_id: str | None = None,
) -> dict[str, Any] | None:
    if scope == 'global':
        cfg = _read_global_servers().get(name)
        if not isinstance(cfg, dict):
            return None
        return _to_record(name, cfg, 'global', None, GLOBAL_CLAUDE_JSON)
    if scope == 'project':
        if not project_path:
            raise ValueError('project scope requires project_path')
        cfg = _read_project_servers(project_path).get(name)
        if not isinstance(cfg, dict):
            return None
        return _to_record(name, cfg, 'project', project_id, project_mcp_path(project_path))
    raise ValueError(f'unknown scope: {scope}')


def write_server(
    name: str,
    transport: str,
    config: dict[str, Any],
    scope: str,
    project_path: str | None = None,
    project_id: str | None = None,
    overwrite: bool = True,
) -> dict[str, Any]:
    err = validate_name(name)
    if err:
        raise ValueError(err)

    normalized = normalize_config(transport, config or {})

    if scope == 'global':
        existing = _read_global_servers()
        if name in existing and not overwrite:
            raise FileExistsError(f'MCP server "{name}" already exists in global scope')

        def _mutate(servers: dict[str, Any]) -> None:
            servers[name] = normalized

        _write_global_servers(_mutate)
        return _to_record(name, normalized, 'global', None, GLOBAL_CLAUDE_JSON)

    if scope == 'project':
        if not project_path:
            raise ValueError('project scope requires project_path')
        existing = _read_project_servers(project_path)
        if name in existing and not overwrite:
            raise FileExistsError(f'MCP server "{name}" already exists in this project')

        def _mutate(servers: dict[str, Any]) -> None:
            servers[name] = normalized

        _write_project_servers(project_path, _mutate)
        return _to_record(name, normalized, 'project', project_id, project_mcp_path(project_path))

    raise ValueError(f'unknown scope: {scope}')


def delete_server(
    scope: str,
    name: str,
    project_path: str | None = None,
) -> dict[str, Any]:
    if scope == 'global':
        existing = _read_global_servers()
        if name not in existing:
            raise FileNotFoundError(f'MCP server "{name}" not found in global scope')

        def _mutate(servers: dict[str, Any]) -> None:
            servers.pop(name, None)

        _write_global_servers(_mutate)
        return {'ok': True, 'scope': 'global', 'name': name}

    if scope == 'project':
        if not project_path:
            raise ValueError('project scope requires project_path')
        existing = _read_project_servers(project_path)
        if name not in existing:
            raise FileNotFoundError(f'MCP server "{name}" not found in this project')

        def _mutate(servers: dict[str, Any]) -> None:
            servers.pop(name, None)

        _write_project_servers(project_path, _mutate)
        return {'ok': True, 'scope': 'project', 'name': name}

    raise ValueError(f'unknown scope: {scope}')


# ── Built-in MCPs (analogous to skills.install_builtins) ─────────────────────
#
# Sources live under `data/mcp/builtin/<name>.json` with shape:
#   {
#     "name": "filesystem",
#     "scope": "global" | "project",
#     "transport": "stdio" | "http" | "sse",
#     "description": "human-readable, not propagated to CC",
#     "config": { ... CC-compatible config; may include ${PROJECT_PATH} ... }
#   }
#
# Install/update semantics mirror skills.install_builtins (checksum-preserved
# user edits via a sidecar marker). Two install entry points:
#
#   install_global_builtins(builtin_root)
#       — seeds scope=global builtins into ~/.claude.json. Marker:
#         data/mc_builtin_mcps_global.json  (a sibling of project records, NOT
#         inside data/projects/ — see CLAUDE.md "DATA_DIR pollution" rule).
#
#   install_project_builtins(builtin_root, project_path, data_root=None)
#       — seeds scope=project builtins into <project_path>/.mcp.json with
#         ${PROJECT_PATH} substituted. Marker:
#         <project_path>/.clayrune_builtin_mcps.json (lives alongside .mcp.json
#         in the user's working dir; NOT in MC's data dir).
#
# Both functions are best-effort: any IO or JSON failure on one builtin must
# not block the rest. Mirrors the install_builtins() posture for skills.

import hashlib as _hashlib


_GLOBAL_BUILTIN_MARKER_NAME = 'mc_builtin_mcps_global.json'
_PROJECT_BUILTIN_MARKER_NAME = '.clayrune_builtin_mcps.json'


def _config_hash(cfg: dict[str, Any]) -> str:
    """Canonical hash for a server config — used by the builtin install layer.

    Stable across Python runs: sorted keys, no whitespace variation.
    """
    blob = json.dumps(cfg, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
    return _hashlib.sha256(blob.encode('utf-8')).hexdigest()


def _substitute_placeholders(obj: Any, mapping: dict[str, str]) -> Any:
    """Recursively replace ${KEY} placeholders in strings via `mapping`."""
    if isinstance(obj, str):
        out = obj
        for k, v in mapping.items():
            out = out.replace('${' + k + '}', v)
        return out
    if isinstance(obj, list):
        return [_substitute_placeholders(x, mapping) for x in obj]
    if isinstance(obj, dict):
        return {k: _substitute_placeholders(v, mapping) for k, v in obj.items()}
    return obj


def _read_builtin_sources(builtin_root: Path) -> list[dict[str, Any]]:
    """Load all builtin source JSONs. Skips malformed files silently."""
    if not builtin_root.exists():
        return []
    out: list[dict[str, Any]] = []
    for f in sorted(builtin_root.iterdir()):
        if not f.is_file() or f.suffix.lower() != '.json':
            continue
        try:
            data = json.loads(f.read_text(encoding='utf-8'))
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        name = data.get('name')
        scope = data.get('scope')
        transport = data.get('transport')
        cfg = data.get('config')
        if not (isinstance(name, str) and isinstance(scope, str)
                and isinstance(transport, str) and isinstance(cfg, dict)):
            continue
        if scope not in ('global', 'project'):
            continue
        if validate_name(name):
            continue
        out.append({
            'name': name,
            'scope': scope,
            'transport': transport,
            'config': cfg,
        })
    return out


def _load_marker(marker_path: Path) -> dict[str, str]:
    if not marker_path.exists():
        return {}
    try:
        data = json.loads(marker_path.read_text(encoding='utf-8'))
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items() if isinstance(v, str)}
    except Exception:
        pass
    return {}


def _save_marker(marker_path: Path, marker: dict[str, str]) -> None:
    _atomic_write_json(marker_path, marker)


def _apply_one_builtin(
    *,
    name: str,
    transport: str,
    rendered_cfg: dict[str, Any],
    src_hash: str,
    current_cfg: dict[str, Any] | None,
    marker: dict[str, str],
    write_fn,
) -> str:
    """Apply one builtin against a target scope. Returns status string:
    'installed' | 'updated' | 'preserved' | 'skipped' | 'error'.

    `write_fn(name, normalized_config)` performs the actual write into the
    target (global or project) and is invoked only when installing/updating.
    """
    try:
        normalized = normalize_config(transport, rendered_cfg)
    except Exception:
        return 'error'

    if current_cfg is None:
        # Not installed yet — install.
        write_fn(name, normalized)
        marker[name] = src_hash
        return 'installed'

    marker_hash = marker.get(name)
    if not marker_hash:
        # User-owned (or pre-marker install) — never touch.
        return 'skipped'

    current_hash = _config_hash(current_cfg)
    if current_hash != marker_hash:
        # User modified after MC installed — preserve.
        return 'preserved'

    if marker_hash == src_hash:
        # In sync with source already.
        return 'skipped'

    # Safe to update — current matches our last install, source has changed.
    write_fn(name, normalized)
    marker[name] = src_hash
    return 'updated'


def install_global_builtins(
    builtin_root: Path,
    marker_dir: Path,
) -> dict[str, list[str]]:
    """Install/update scope=global builtins into ~/.claude.json.

    `marker_dir` is where the sidecar `mc_builtin_mcps_global.json` lives —
    pass the MC data root (NOT data/projects/) to keep it out of project loading.
    """
    result = {'installed': [], 'updated': [], 'preserved': [], 'skipped': [], 'error': []}
    sources = [s for s in _read_builtin_sources(builtin_root) if s['scope'] == 'global']
    if not sources:
        return result

    marker_path = marker_dir / _GLOBAL_BUILTIN_MARKER_NAME
    marker = _load_marker(marker_path)
    current_servers = _read_global_servers()

    def _write(name: str, cfg: dict[str, Any]) -> None:
        def _mutate(servers: dict[str, Any]) -> None:
            servers[name] = cfg
        _write_global_servers(_mutate)

    changed = False
    for src in sources:
        name = src['name']
        rendered = _substitute_placeholders(src['config'], {})
        try:
            src_hash = _config_hash(normalize_config(src['transport'], rendered))
        except Exception:
            result['error'].append(name)
            continue

        status = _apply_one_builtin(
            name=name,
            transport=src['transport'],
            rendered_cfg=rendered,
            src_hash=src_hash,
            current_cfg=current_servers.get(name) if isinstance(current_servers.get(name), dict) else None,
            marker=marker,
            write_fn=_write,
        )
        if status in ('installed', 'updated'):
            changed = True
        result[status].append(name)

    if changed:
        _save_marker(marker_path, marker)
    return result


def install_project_builtins(
    builtin_root: Path,
    project_path: str,
) -> dict[str, list[str]]:
    """Install/update scope=project builtins into <project_path>/.mcp.json.

    Substitutes ${PROJECT_PATH} → the absolute project_path. Marker sidecar
    lives in the project dir as `.clayrune_builtin_mcps.json`.
    """
    result = {'installed': [], 'updated': [], 'preserved': [], 'skipped': [], 'error': []}
    if not project_path:
        return result
    proj_root = Path(project_path)
    if not proj_root.exists():
        return result

    sources = [s for s in _read_builtin_sources(builtin_root) if s['scope'] == 'project']
    if not sources:
        return result

    marker_path = proj_root / _PROJECT_BUILTIN_MARKER_NAME
    marker = _load_marker(marker_path)
    current_servers = _read_project_servers(project_path)

    def _write(name: str, cfg: dict[str, Any]) -> None:
        def _mutate(servers: dict[str, Any]) -> None:
            servers[name] = cfg
        _write_project_servers(project_path, _mutate)

    substitution = {'PROJECT_PATH': str(proj_root.resolve()).replace('\\', '/')}
    changed = False
    for src in sources:
        name = src['name']
        rendered = _substitute_placeholders(src['config'], substitution)
        try:
            src_hash = _config_hash(normalize_config(src['transport'], rendered))
        except Exception:
            result['error'].append(name)
            continue

        status = _apply_one_builtin(
            name=name,
            transport=src['transport'],
            rendered_cfg=rendered,
            src_hash=src_hash,
            current_cfg=current_servers.get(name) if isinstance(current_servers.get(name), dict) else None,
            marker=marker,
            write_fn=_write,
        )
        if status in ('installed', 'updated'):
            changed = True
        result[status].append(name)

    if changed:
        _save_marker(marker_path, marker)
    return result


# ── Gemini settings sync ─────────────────────────────────────────────────────
#
# Mirror MC-managed MCP servers into `~/.gemini/settings.json mcpServers` so
# gemini-cli sees the same MCP toolchain claude-code does. Safety rules:
#
#   1. Atomic write, lock-serialized — same `_atomic_write_json` used for
#      claude.json.
#   2. Only touches the `mcpServers` key and the `__mc_managed_mcp_servers`
#      sidecar marker. Every other key in settings.json (selectedAuthType,
#      theme, user-defined preferences, ...) is preserved verbatim.
#   3. User-owned entries are NEVER overwritten. If the user has independently
#      configured a server named `filesystem` in gemini, and MC also has one,
#      the user's wins and ours is skipped — recorded in the return value so
#      the caller can show a hint.
#   4. Stale MC-managed entries (in the sidecar list but no longer present in
#      MC's MCP config) are removed on the next sync.

GLOBAL_GEMINI_JSON = _home() / '.gemini' / 'settings.json'
_gemini_write_lock = threading.Lock()
_MC_MANAGED_MARKER_KEY = '__mc_managed_mcp_servers'


def _to_gemini_config(transport: str, cfg: dict[str, Any]) -> dict[str, Any]:
    """Translate a CC-flavored MCP config dict to gemini's settings.json schema.

    stdio is identical (command/args/env). HTTP and SSE use different keys:
    gemini wants `httpUrl` / `sseUrl` at the top level rather than CC's
    {type: http|sse, url: ...} shape.
    """
    transport = (transport or '').strip().lower()
    if transport == 'http':
        out: dict[str, Any] = {'httpUrl': cfg.get('url') or ''}
        if cfg.get('headers'):
            out['headers'] = dict(cfg['headers'])
        return out
    if transport == 'sse':
        out = {'sseUrl': cfg.get('url') or ''}
        if cfg.get('headers'):
            out['headers'] = dict(cfg['headers'])
        return out
    # stdio (gemini default)
    out = {'command': cfg.get('command') or ''}
    if cfg.get('args'):
        out['args'] = list(cfg['args'])
    if cfg.get('env'):
        out['env'] = dict(cfg['env'])
    if cfg.get('cwd'):
        out['cwd'] = cfg['cwd']
    return out


def collect_effective_servers_for_project(
    project_path: str | None,
) -> dict[str, dict[str, Any]]:
    """Return {name: gemini-shaped config} for all MC-managed MCP servers
    visible to this project. Project entries shadow global entries by name
    (project wins) — same precedence as CC."""
    out: dict[str, dict[str, Any]] = {}
    for name, cfg in _read_global_servers().items():
        if isinstance(cfg, dict):
            out[name] = _to_gemini_config(_infer_transport(cfg), cfg)
    if project_path:
        for name, cfg in _read_project_servers(project_path).items():
            if isinstance(cfg, dict):
                out[name] = _to_gemini_config(_infer_transport(cfg), cfg)
    return out


def sync_to_gemini(project_path: str | None) -> dict[str, Any]:
    """Merge MC-managed MCP servers into ~/.gemini/settings.json.

    Returns: {added: [names...], skipped: [names...], removed: [names...]}
      added   — names MC just wrote (refresh or first-time install)
      skipped — MC-managed names that collided with user-owned entries
                (user's stays, ours dropped)
      removed — names MC previously managed but are no longer in MC config
                (cleaned up on this sync)
    """
    mc_servers = collect_effective_servers_for_project(project_path)
    added: list[str] = []
    skipped: list[str] = []
    removed: list[str] = []

    with _gemini_write_lock:
        data = _read_json_or_empty(GLOBAL_GEMINI_JSON)
        servers_raw = data.get('mcpServers')
        servers = dict(servers_raw) if isinstance(servers_raw, dict) else {}
        prev_managed_raw = data.get(_MC_MANAGED_MARKER_KEY)
        prev_managed = list(prev_managed_raw) if isinstance(prev_managed_raw, list) else []

        # 1. Drop previously-MC-managed entries (clears stale).
        for name in prev_managed:
            if name in servers:
                servers.pop(name, None)
                if name not in mc_servers:
                    removed.append(name)

        # 2. Add fresh — but step around user-owned entries with same name.
        for name, cfg in mc_servers.items():
            if name in servers and name not in prev_managed:
                # User-owned (configured directly in gemini). Don't touch.
                skipped.append(name)
                continue
            servers[name] = cfg
            added.append(name)

        # 3. Persist. Only mcpServers + sidecar marker change.
        data['mcpServers'] = servers
        data[_MC_MANAGED_MARKER_KEY] = added
        _atomic_write_json(GLOBAL_GEMINI_JSON, data)

    return {'added': added, 'skipped': skipped, 'removed': removed}
