"""MCP server management endpoints — blueprint 1.4 (MODERNIZATION_PLAN.md).

Moved VERBATIM from server.py. Thin glue: mcp.py / mcp_installer.py own the
logic. 10 routes (plan table said 6 — the URL-install flow + the per-project
loadout trim grew after the table): 8 /api/mcp* + the 2
/api/project/<id>/mcp-enabled loadout routes, which are MCP-feature routes
that happen to live under /api/project/ (feature cohesion wins; same call as
/api/presence in 1.2).
"""

import json
import os
import threading
import time as _time
from pathlib import Path
from typing import Any, Callable

from flask import Blueprint, Response, jsonify, request

from mc import state
from mc.core import _log, now_iso

import mcp as _mcp
import mcp_installer as _mcpinst

# Shared request helper — lives with the skills blueprint until a better home.
from mc.blueprints.skills_routes import _resolve_project_path_or_400

bp = Blueprint('mcp_routes', __name__)

# ── wired by server.py (see wire()) ──────────────────────────────────────────
load_project: Callable[[str], Any] = None  # type: ignore[assignment]
save_project: Callable[..., Any] = None  # type: ignore[assignment]
DATA_DIR: Path = None  # type: ignore[assignment]
_mcp_server_catalog: Callable[[Any], Any] = None  # type: ignore[assignment]


def wire(*, load_project_fn, save_project_fn, data_dir, mcp_server_catalog_fn):
    """Late-bind projects-family deps (1.11) + the shared catalog helper
    (dispatch machinery keeps it until 1.12)."""
    global load_project, save_project, DATA_DIR, _mcp_server_catalog
    load_project = load_project_fn
    save_project = save_project_fn
    DATA_DIR = data_dir
    _mcp_server_catalog = mcp_server_catalog_fn


# ── MCP server endpoints ────────────────────────────────────────────────────
#
# MCP (Model Context Protocol) servers extend Claude Code with extra tool
# providers. Two native config locations:
#
#   ~/.claude.json                       ← global; `mcpServers` top-level key
#   <project_path>/.mcp.json             ← project-committed (team-shared)
#
# MC manages the files; CC reads them natively at session start. Three
# transports supported: stdio (local subprocess), http (streamable HTTP),
# sse (legacy HTTP+SSE). See mcp.py for the schema details.

# Servers force-kept in any custom per-project loadout — dropping them would
# silently break a load-bearing capability. `engram` = cross-session memory;
# losing it kills the whole memory system for that project (see CLAUDE.md).
_MCP_ALWAYS_KEEP = ('engram',)


@bp.route('/api/mcp')
def list_mcp_route():
    """List MCP servers across global pool + (optionally) one project's pool.

    When `project_id` is given, each item is annotated with `active` (is it in
    the project's enabled_mcp_servers loadout?), `loadout_custom` (has the
    project opted into trimming at all?), and `always_on` (force-kept server).
    No opt-in → every server is active (full fleet, unchanged default).
    """
    project_id = request.args.get('project_id')

    project = None
    project_path = None
    if project_id:
        p = load_project(project_id)
        if p:
            project = p
            project_path = p.get('project_path') or None

    items = _mcp.list_servers(project_path=project_path, project_id=project_id)

    if project is not None:
        sel = project.get('enabled_mcp_servers')
        opted_in = isinstance(sel, list)
        sel_set = set(sel) if opted_in else None
        for it in items:
            name = it.get('name')
            it['always_on'] = name in _MCP_ALWAYS_KEEP
            it['loadout_custom'] = opted_in
            it['active'] = (sel_set is None) or (name in sel_set) or it['always_on']

    return jsonify(items)


@bp.route('/api/mcp/<scope>/<name>')
def read_mcp_route(scope, name):
    if scope not in ('global', 'project'):
        return jsonify({'error': 'scope must be global or project'}), 400
    project_id = request.args.get('project_id')

    project_path, err = _resolve_project_path_or_400(scope, project_id)
    if err:
        return err

    try:
        rec = _mcp.read_server(scope, name, project_path=project_path, project_id=project_id)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    if not rec:
        return jsonify({'error': 'MCP server not found'}), 404
    return jsonify(rec)


@bp.route('/api/mcp', methods=['POST'])
def create_mcp_route():
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    transport = (data.get('transport') or '').strip()
    config = data.get('config') or {}
    scope = (data.get('scope') or 'global').strip()
    project_id = data.get('project_id')

    if scope not in ('global', 'project'):
        return jsonify({'error': 'scope must be global or project'}), 400

    project_path, err = _resolve_project_path_or_400(scope, project_id)
    if err:
        return err

    try:
        rec = _mcp.write_server(
            name=name,
            transport=transport,
            config=config,
            scope=scope,
            project_path=project_path,
            project_id=project_id,
            overwrite=False,
        )
        return jsonify(rec), 201
    except FileExistsError as e:
        return jsonify({'error': str(e)}), 409
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


@bp.route('/api/mcp/<scope>/<name>', methods=['PUT'])
def update_mcp_route(scope, name):
    if scope not in ('global', 'project'):
        return jsonify({'error': 'scope must be global or project'}), 400
    data = request.get_json() or {}
    transport = (data.get('transport') or '').strip()
    config = data.get('config') or {}
    project_id = data.get('project_id') or request.args.get('project_id')

    project_path, err = _resolve_project_path_or_400(scope, project_id)
    if err:
        return err

    try:
        rec = _mcp.write_server(
            name=name,
            transport=transport,
            config=config,
            scope=scope,
            project_path=project_path,
            project_id=project_id,
            overwrite=True,
        )
        return jsonify(rec)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


@bp.route('/api/mcp/<scope>/<name>', methods=['DELETE'])
def delete_mcp_route(scope, name):
    if scope not in ('global', 'project'):
        return jsonify({'error': 'scope must be global or project'}), 400
    project_id = request.args.get('project_id')

    project_path, err = _resolve_project_path_or_400(scope, project_id)
    if err:
        return err

    try:
        result = _mcp.delete_server(scope=scope, name=name, project_path=project_path)
        return jsonify(result)
    except FileNotFoundError as e:
        return jsonify({'error': str(e)}), 404
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


# ── Per-project MCP loadout (the enabled_mcp_servers trim) ───────────────────
#
# WRITE/READ surface for project['enabled_mcp_servers'], which the dispatch
# trim (_resolve_project_mcp_config → --strict-mcp-config) consumes:
#   absent/None → full fleet (default; unchanged for projects that never opted
#                 in — this is why 0 projects were trimmed before this shipped).
#   list        → load ONLY those servers for this project's agents.
# A custom loadout always force-keeps _MCP_ALWAYS_KEEP (engram/memory).

@bp.route('/api/project/<project_id>/mcp-enabled', methods=['GET'])
def get_project_mcp_enabled(project_id):
    p = load_project(project_id)
    if not p:
        return jsonify({'error': 'project not found'}), 404
    sel = p.get('enabled_mcp_servers')
    opted_in = isinstance(sel, list)
    try:
        catalog = sorted(_mcp_server_catalog(p).keys())
    except Exception:
        catalog = []
    return jsonify({
        'opted_in': opted_in,
        'enabled': sel if opted_in else None,
        'catalog': catalog,
        'always_keep': list(_MCP_ALWAYS_KEEP),
    })


@bp.route('/api/project/<project_id>/mcp-enabled', methods=['PUT'])
def set_project_mcp_enabled(project_id):
    """Set (or clear) a project's MCP loadout.

    Body: {"enabled": [names]}  → trim to these (engram force-kept).
          {"enabled": null}     → clear the opt-in (back to full fleet).
    """
    filepath = DATA_DIR / f'{project_id}.json'
    if not filepath.exists():
        return jsonify({'error': 'project not found'}), 404
    data = request.get_json(silent=True) or {}
    enabled = data.get('enabled', None)
    existing = json.loads(filepath.read_text(encoding='utf-8'))

    if enabled is None:
        existing.pop('enabled_mcp_servers', None)
        existing['last_updated'] = now_iso()
        save_project(project_id, existing)
        return jsonify({'ok': True, 'opted_in': False, 'enabled': None})

    if not isinstance(enabled, list) or not all(isinstance(x, str) for x in enabled):
        return jsonify({'error': 'enabled must be a list of server names or null'}), 400

    try:
        catalog = set(_mcp_server_catalog(existing).keys())
    except Exception:
        catalog = set(enabled)
    chosen = [n for n in dict.fromkeys(enabled) if n in catalog]
    for keep in _MCP_ALWAYS_KEEP:
        if keep in catalog and keep not in chosen:
            chosen.append(keep)

    existing['enabled_mcp_servers'] = chosen
    existing['last_updated'] = now_iso()
    save_project(project_id, existing)
    return jsonify({'ok': True, 'opted_in': True, 'enabled': chosen})


# ── MCP "Add from URL" — preview / install / cleanup ────────────────────────
#
# Frontend hits these in sequence:
#   1. POST /api/mcp/url/preview   → clone + extract + audit + scan (no install)
#   2. POST /api/mcp/url/install   → run package-manager + write config (SSE)
#   3. DELETE /api/mcp/url/staged  → cleanup if user cancels after preview
#
# Why split: the preview is heavy (clone + Claude scan ~5-10s + token cost)
# but lets the user see what's about to happen before committing. The install
# stream is SSE so the UI can show live `npm install` output.

@bp.route('/api/mcp/url/preview', methods=['POST'])
def mcp_url_preview():
    data = request.get_json() or {}
    raw_url = (data.get('url') or '').strip()
    ref = (data.get('ref') or '').strip() or None
    if not raw_url:
        return jsonify({'error': 'url required'}), 400

    classified = _mcpinst.classify_url(raw_url)
    kind = classified.get('kind')

    # NPM packages don't need cloning — the install command is npx, the
    # config is templated, the security signal set is much thinner.
    if kind == 'npm':
        pkg = classified.get('package')
        servers = {pkg: {'command': 'npx', 'args': ['-y', pkg]}}
        return jsonify({
            'kind': 'npm', 'classified': classified,
            'servers': servers, 'name_hint': pkg, 'source_tier': 0,
            'secrets': [],
            'install_commands': [['npx', '-y', pkg, '--help']],
            'github': {'available': False},
            'audit': {'available': False, 'reason': 'npm package — runs via npx'},
            'scan': {'available': False, 'reason': 'no source to scan (npx runs the published package)'},
            'install_dir': None, 'sha': None,
        })

    # Raw JSON URL → fetch, parse, return as if the user pasted it manually.
    if kind == 'json':
        try:
            import urllib.request as _ur
            with _ur.urlopen(classified['url'], timeout=15) as resp:
                blob = json.loads(resp.read().decode('utf-8'))
        except Exception as e:
            return jsonify({'error': f'fetch failed: {e}'}), 400
        servers = _mcpinst._find_mcp_servers_in_obj(blob)
        if not servers:
            return jsonify({'error': 'no mcpServers object found in JSON'}), 400
        return jsonify({
            'kind': 'json', 'classified': classified,
            'servers': servers, 'name_hint': next(iter(servers.keys()), None),
            'source_tier': 1, 'secrets': _mcpinst.detect_secrets(servers),
            'install_commands': [],
            'github': {'available': False},
            'audit': {'available': False, 'reason': 'pure-config import — nothing to install'},
            'scan': {'available': False, 'reason': 'no source to scan'},
            'install_dir': None, 'sha': None,
        })

    if kind != 'git':
        return jsonify({'error': classified.get('reason') or f'unsupported url kind: {kind}'}), 400

    owner = classified.get('owner') or ''
    repo = classified.get('repo') or ''
    url = classified['url']
    git_ref = ref or classified.get('ref')

    github = _mcpinst.fetch_github_signals(owner, repo) if owner and repo else {'available': False}

    try:
        clone = _mcpinst.stage_clone(url, owner=owner, repo=repo, ref=git_ref)
    except Exception as e:
        return jsonify({'error': str(e), 'github': github}), 500

    install_dir = clone['install_dir']
    sha = clone['sha']

    extracted = _mcpinst.extract_config(install_dir, allow_claude_fallback=True)
    secrets = _mcpinst.detect_secrets(extracted.get('servers') or {})
    audit = _mcpinst.dependency_audit(install_dir)
    scan = _mcpinst.security_scan(install_dir, sha)
    install_cmds = _mcpinst.install_commands(install_dir)

    return jsonify({
        'kind': 'git', 'classified': classified,
        'install_dir': install_dir, 'sha': sha,
        'default_branch': clone.get('default_branch'),
        'servers': extracted.get('servers') or {},
        'name_hint': extracted.get('name_hint'),
        'source_tier': extracted.get('source_tier'),
        'secrets': secrets,
        'install_commands': install_cmds,
        'github': github,
        'audit': audit,
        'scan': scan,
    })


@bp.route('/api/mcp/url/staged', methods=['DELETE'])
def mcp_url_staged_cleanup():
    data = request.get_json() or {}
    install_dir = (data.get('install_dir') or '').strip()
    if not install_dir:
        return jsonify({'error': 'install_dir required'}), 400
    try:
        removed = _mcpinst.cleanup_staged(install_dir)
        return jsonify({'ok': True, 'removed': removed})
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/mcp/url/install', methods=['POST'])
def mcp_url_install():
    """SSE stream: runs the install commands, writes the MCP config on success."""
    data = request.get_json(silent=True) or {}
    install_dir = (data.get('install_dir') or '').strip()
    name = (data.get('name') or '').strip()
    scope = (data.get('scope') or 'global').strip()
    project_id = (data.get('project_id') or '').strip() or None
    config = data.get('config') or {}
    secrets = data.get('secrets') or {}
    # For npm/json kinds with no install_dir, we skip the install stream and
    # just write the config — but we still emit a stream so the UI flow is
    # uniform.
    skip_install = not install_dir

    name_err = _mcp.validate_name(name)
    if name_err:
        return jsonify({'error': name_err}), 400

    project_path = None
    if scope == 'project':
        if not project_id:
            return jsonify({'error': 'project_id required for project scope'}), 400
        p = load_project(project_id)
        if not p:
            return jsonify({'error': 'project not found'}), 404
        project_path = p.get('project_path') or None
        if not project_path:
            return jsonify({'error': 'project has no project_path'}), 400
    elif scope != 'global':
        return jsonify({'error': 'scope must be global or project'}), 400

    # Apply secrets to the env block before writing.
    servers_with_secrets = _mcpinst.apply_secrets_to_config(
        {name: config}, secrets,
    )
    final_cfg = servers_with_secrets.get(name) or config
    transport = _mcp._infer_transport(final_cfg)

    def _stream():
        yield 'data: ' + json.dumps({'type': 'start'}) + '\n\n'

        if not skip_install:
            buf: list[str] = []

            def emit(text: str):
                buf.append(text)

            rc = _mcpinst.stream_install(install_dir, emit)
            # Flush buffered text to SSE in chunks (the stream_install
            # callback is sync; we batch-emit here to avoid one SSE frame per
            # character).
            chunk = ''.join(buf)
            if chunk:
                yield 'data: ' + json.dumps({'type': 'log', 'text': chunk}) + '\n\n'
            if rc != 0:
                yield 'data: ' + json.dumps({
                    'type': 'error', 'message': f'install exited with code {rc}',
                }) + '\n\n'
                return

        try:
            record = _mcp.write_server(
                name=name, transport=transport, config=final_cfg,
                scope=scope, project_path=project_path,
                project_id=project_id, overwrite=True,
            )
        except Exception as e:
            yield 'data: ' + json.dumps({
                'type': 'error', 'message': f'write_server failed: {e}',
            }) + '\n\n'
            return

        yield 'data: ' + json.dumps({
            'type': 'done', 'record': record,
        }) + '\n\n'

    return Response(_stream(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


