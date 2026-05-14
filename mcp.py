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
