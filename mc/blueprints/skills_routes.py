"""Skills management endpoints — blueprint 1.3 (MODERNIZATION_PLAN.md).

Moved VERBATIM from server.py. Thin glue: skills.py owns the logic; these
routes wrap it. 14 routes (plan table said 12 — the git-import flow grew
install/cancel since the table was written).
"""

import json
import os
import shutil
import threading
import time as _time
from pathlib import Path
from typing import Any, Callable

from flask import Blueprint, jsonify, request

from mc import state
from mc.core import _log

import mcp as _mcp
import skills as _skills

bp = Blueprint('skills', __name__)

# ── wired by server.py (see wire()) ──────────────────────────────────────────
load_project: Callable[[str], Any] = None  # type: ignore[assignment]
load_projects: Callable[..., Any] = None  # type: ignore[assignment]
_APP_DIR: Path = None  # type: ignore[assignment]


def wire(*, load_project_fn, load_projects_fn, app_dir):
    """Late-bind projects-family deps (1.11) + the app dir constant."""
    global load_project, load_projects, _APP_DIR
    load_project = load_project_fn
    load_projects = load_projects_fn
    _APP_DIR = app_dir


# ── Skills endpoints ────────────────────────────────────────────────────────
#
# Anthropic-format skills live at ~/.claude/skills/<name>/SKILL.md (global)
# and <project_path>/.claude/skills/<name>/SKILL.md (project-local).  CC reads
# them natively — Mission Control just provides the management surface (list,
# read, create, update, archive, search, usage stats).
#
# Built-ins ship under data/skills/builtin/ and install once at startup via
# `_install_builtin_skills()` with checksum-based update preservation.

def _resolve_project_path_or_400(scope: str, project_id: str | None):
    """Helper: validate that project scope has a usable project_path.

    Returns (project_path: str|None, error_response|None). On error the caller
    short-circuits with the (jsonify, status) tuple.
    """
    if scope != 'project':
        return None, None
    if not project_id:
        return None, (jsonify({'error': 'project_id required for project scope'}), 400)
    p = load_project(project_id)
    if not p:
        return None, (jsonify({'error': 'project not found'}), 404)
    project_path = p.get('project_path') or None
    if not project_path:
        return None, (jsonify({'error': 'project has no project_path; configure it first'}), 400)
    return project_path, None


@bp.route('/api/skills')
def list_skills_route():
    """List skills across global pool + (optionally) one project's pool.

    Query params:
      project_id: include this project's local skills and shadow-flag globals
      include_archived: 'true' to also include archived globals
      q: substring filter on name+description
    """
    project_id = request.args.get('project_id')
    include_archived = (request.args.get('include_archived', '') or '').lower() in ('1', 'true', 'yes')
    q = (request.args.get('q') or '').strip().lower()

    project_path = None
    if project_id:
        p = load_project(project_id)
        if p:
            project_path = p.get('project_path') or None

    items = _skills.list_skills(
        project_path=project_path,
        project_id=project_id,
        include_archived=include_archived,
        include_body=False,
    )
    if q:
        items = [s for s in items if q in (s.get('name', '') + ' ' + s.get('description', '')).lower()]
    return jsonify(items)


@bp.route('/api/skills/<scope>/<name>')
def read_skill_route(scope, name):
    if scope not in ('global', 'project', 'archive'):
        return jsonify({'error': 'scope must be global|project|archive'}), 400
    project_id = request.args.get('project_id')
    include_body = (request.args.get('include_body', 'true') or 'true').lower() in ('1', 'true', 'yes')

    project_path, err = _resolve_project_path_or_400(scope, project_id)
    if err:
        return err

    try:
        rec = _skills.read_skill(
            scope, name,
            project_path=project_path,
            project_id=project_id,
            include_body=include_body,
        )
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    if not rec:
        return jsonify({'error': 'skill not found'}), 404
    return jsonify(rec)


@bp.route('/api/skills', methods=['POST'])
def create_skill_route():
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    description = (data.get('description') or '').strip()
    body = data.get('body') or ''
    scope = (data.get('scope') or 'global').strip()
    project_id = data.get('project_id')

    if scope not in ('global', 'project'):
        return jsonify({'error': 'scope must be global or project'}), 400

    project_path, err = _resolve_project_path_or_400(scope, project_id)
    if err:
        return err

    try:
        rec = _skills.write_skill(
            name=name,
            description=description,
            body=body,
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


@bp.route('/api/skills/<scope>/<name>', methods=['PUT'])
def update_skill_route(scope, name):
    if scope not in ('global', 'project'):
        return jsonify({'error': 'can only update global or project scope'}), 400
    data = request.get_json() or {}
    description = (data.get('description') or '').strip()
    body = data.get('body') or ''
    project_id = data.get('project_id') or request.args.get('project_id')

    project_path, err = _resolve_project_path_or_400(scope, project_id)
    if err:
        return err

    try:
        rec = _skills.write_skill(
            name=name,
            description=description,
            body=body,
            scope=scope,
            project_path=project_path,
            project_id=project_id,
            overwrite=True,
        )
        return jsonify(rec)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


@bp.route('/api/skills/<scope>/<name>', methods=['DELETE'])
def delete_skill_route(scope, name):
    if scope not in ('global', 'project', 'archive'):
        return jsonify({'error': 'scope must be global, project, or archive'}), 400
    project_id = request.args.get('project_id')
    # archive=true → soft archive (only meaningful for global scope).
    # archive=false → hard delete. For scope=archive this is the only valid mode.
    archive = (request.args.get('archive', 'true') or 'true').lower() in ('1', 'true', 'yes')
    if scope == 'archive':
        archive = False  # archived skills can only be hard-deleted

    project_path, err = _resolve_project_path_or_400(scope, project_id)
    if err:
        return err

    try:
        result = _skills.delete_skill(
            scope=scope, name=name,
            project_path=project_path,
            archive=archive,
        )
        return jsonify(result)
    except FileNotFoundError as e:
        return jsonify({'error': str(e)}), 404


@bp.route('/api/skills/archive/<name>/restore', methods=['POST'])
def restore_skill_route(name):
    try:
        result = _skills.restore_skill(name)
        return jsonify(result)
    except FileNotFoundError as e:
        return jsonify({'error': str(e)}), 404
    except FileExistsError as e:
        return jsonify({'error': str(e)}), 409


@bp.route('/api/skills/search')
def search_skills_route():
    """Keyword search across global + named-project pools.

    Used by the mc-skill-broker built-in skill for cross-project discovery.
    """
    q = (request.args.get('q') or '').strip()
    try:
        limit = int(request.args.get('limit', '10'))
    except ValueError:
        limit = 10
    project_id = request.args.get('project_id')

    project_path = None
    if project_id:
        p = load_project(project_id)
        if p:
            project_path = p.get('project_path') or None

    results = _skills.search_skills(
        query=q,
        project_path=project_path,
        project_id=project_id,
        limit=max(1, min(limit, 50)),
    )
    return jsonify(results)


@bp.route('/api/skills/usage')
def skill_usage_route():
    """Skill invocation stats parsed from Claude Code transcripts."""
    try:
        days = int(request.args.get('days', '30'))
    except ValueError:
        days = 30
    return jsonify(_skills.skill_usage_stats(days=max(1, min(days, 365))))


@bp.route('/api/skills/import/paste', methods=['POST'])
def import_skill_paste_route():
    """Import a skill from a pasted SKILL.md string.

    Body: {content, scope, project_id?, name?, overwrite?}
    """
    data = request.get_json() or {}
    content = data.get('content') or ''
    scope = (data.get('scope') or 'global').strip()
    project_id = data.get('project_id')
    name_override = (data.get('name') or '').strip() or None
    overwrite = bool(data.get('overwrite'))

    if scope not in ('global', 'project'):
        return jsonify({'error': 'scope must be global or project'}), 400

    project_path, err = _resolve_project_path_or_400(scope, project_id)
    if err:
        return err

    try:
        rec = _skills.import_from_paste(
            content=content,
            scope=scope,
            project_path=project_path,
            project_id=project_id,
            name_override=name_override,
            overwrite=overwrite,
        )
        return jsonify(rec), 201
    except FileExistsError as e:
        return jsonify({'error': str(e)}), 409
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


@bp.route('/api/skills/import/folder', methods=['POST'])
def import_skill_folder_route():
    """Import a skill from a local folder containing SKILL.md.

    Body: {path, scope, project_id?, name?, selected_rel_dir?, overwrite?}
    If multiple SKILL.md found, returns {multiple: true, candidates: [...]} —
    caller re-invokes with selected_rel_dir.
    """
    data = request.get_json() or {}
    path = (data.get('path') or '').strip()
    scope = (data.get('scope') or 'global').strip()
    project_id = data.get('project_id')
    name_override = (data.get('name') or '').strip() or None
    selected_rel_dir = data.get('selected_rel_dir')
    overwrite = bool(data.get('overwrite'))

    if not path:
        return jsonify({'error': 'path is required'}), 400
    if scope not in ('global', 'project'):
        return jsonify({'error': 'scope must be global or project'}), 400

    project_path, err = _resolve_project_path_or_400(scope, project_id)
    if err:
        return err

    try:
        result = _skills.import_from_folder(
            src_path=path,
            scope=scope,
            project_path=project_path,
            project_id=project_id,
            name_override=name_override,
            selected_rel_dir=selected_rel_dir,
            overwrite=overwrite,
        )
        # Multi-skill case: re-prompt the user
        if isinstance(result, dict) and result.get('multiple'):
            return jsonify(result), 200
        return jsonify(result), 201
    except FileNotFoundError as e:
        return jsonify({'error': str(e)}), 404
    except FileExistsError as e:
        return jsonify({'error': str(e)}), 409
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


@bp.route('/api/skills/import/git', methods=['POST'])
def import_skill_git_route():
    """Clone a Git repo into staging and return SKILL.md candidates.

    Body: {url, ref?, scope, project_id?, name?, overwrite?, auto_install?}
    auto_install (default true): if exactly one SKILL.md found, install it
    immediately and clean up staging. If multiple, return {staging_id, candidates}
    and require a follow-up call to /api/skills/import/git/install.
    """
    data = request.get_json() or {}
    url = (data.get('url') or '').strip()
    ref = (data.get('ref') or '').strip() or None
    scope = (data.get('scope') or 'global').strip()
    project_id = data.get('project_id')
    name_override = (data.get('name') or '').strip() or None
    overwrite = bool(data.get('overwrite'))
    auto_install = data.get('auto_install', True)

    if scope not in ('global', 'project'):
        return jsonify({'error': 'scope must be global or project'}), 400

    project_path, err = _resolve_project_path_or_400(scope, project_id)
    if err:
        return err

    try:
        clone = _skills.git_clone_to_staging(url=url, ref=ref)
    except (ValueError, RuntimeError) as e:
        return jsonify({'error': str(e)}), 400

    candidates = clone['candidates']
    staging_id = clone['staging_id']
    plugin_info = clone.get('plugin')

    # When a plugin is detected, skip auto-install — let the user choose
    # between "Install skill(s) only" and "Install full plugin" in the UI.
    if auto_install and len(candidates) == 1 and not plugin_info:
        try:
            rec = _skills.install_from_staging(
                staging_id=staging_id,
                rel_dir=candidates[0]['rel_dir'],
                scope=scope,
                project_path=project_path,
                project_id=project_id,
                name_override=name_override or candidates[0]['name'],
                overwrite=overwrite,
                cleanup=True,
            )
            return jsonify({'installed': rec, 'candidates': candidates}), 201
        except FileExistsError as e:
            return jsonify({'error': str(e), 'staging_id': staging_id, 'candidates': candidates}), 409
        except ValueError as e:
            return jsonify({'error': str(e)}), 400

    # Multi-skill, plugin, or auto_install=false: return list for picker
    response = {'staging_id': staging_id, 'candidates': candidates}
    if plugin_info:
        response['plugin'] = plugin_info
    return jsonify(response), 200


@bp.route('/api/skills/import/plugin', methods=['POST'])
def import_full_plugin_route():
    """Install all skill + command + agent components of a plugin.

    Body: {staging_id?, path?, overwrite?}

    Either staging_id (from a prior /api/skills/import/git call) or path (a
    local folder) is required. Hooks are deliberately not installed — see
    skills.install_full_plugin for the trust-model rationale.

    All components install to GLOBAL scope. Project-scoped full-plugin
    install is not supported in v1.
    """
    data = request.get_json() or {}
    staging_id = (data.get('staging_id') or '').strip()
    path = (data.get('path') or '').strip()
    overwrite = bool(data.get('overwrite'))

    if not staging_id and not path:
        return jsonify({'error': 'staging_id or path required'}), 400

    if staging_id:
        plugin_root = _skills.STAGING_SKILLS_DIR / staging_id
        if not plugin_root.exists():
            return jsonify({'error': 'staging dir not found'}), 404
    else:
        plugin_root = Path(path).expanduser()
        if not plugin_root.exists():
            return jsonify({'error': 'path does not exist'}), 404

    try:
        result = _skills.install_full_plugin(plugin_root, overwrite=overwrite)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400

    # Clean up staging if we came from a git import
    if staging_id:
        try:
            shutil.rmtree(_skills.STAGING_SKILLS_DIR / staging_id, ignore_errors=True)
        except Exception:
            pass

    return jsonify(result), 201


@bp.route('/api/skills/import/git/install', methods=['POST'])
def import_skill_git_install_route():
    """Install one specific skill from a previously-staged Git clone.

    Body: {staging_id, rel_dir, scope, project_id?, name?, overwrite?, cleanup?}
    """
    data = request.get_json() or {}
    staging_id = (data.get('staging_id') or '').strip()
    rel_dir = data.get('rel_dir', '')
    scope = (data.get('scope') or 'global').strip()
    project_id = data.get('project_id')
    name_override = (data.get('name') or '').strip() or None
    overwrite = bool(data.get('overwrite'))
    cleanup = bool(data.get('cleanup', True))

    if not staging_id:
        return jsonify({'error': 'staging_id required'}), 400
    if scope not in ('global', 'project'):
        return jsonify({'error': 'scope must be global or project'}), 400

    project_path, err = _resolve_project_path_or_400(scope, project_id)
    if err:
        return err

    try:
        rec = _skills.install_from_staging(
            staging_id=staging_id,
            rel_dir=rel_dir,
            scope=scope,
            project_path=project_path,
            project_id=project_id,
            name_override=name_override,
            overwrite=overwrite,
            cleanup=cleanup,
        )
        return jsonify(rec), 201
    except FileNotFoundError as e:
        return jsonify({'error': str(e)}), 404
    except FileExistsError as e:
        return jsonify({'error': str(e)}), 409
    except ValueError as e:
        return jsonify({'error': str(e)}), 400


@bp.route('/api/skills/import/git/cancel', methods=['POST'])
def import_skill_git_cancel_route():
    """Discard a staging dir without installing anything."""
    data = request.get_json() or {}
    staging_id = (data.get('staging_id') or '').strip()
    if not staging_id:
        return jsonify({'error': 'staging_id required'}), 400
    target = _skills.STAGING_SKILLS_DIR / staging_id
    try:
        if target.exists():
            import shutil as _sh
            _sh.rmtree(target, ignore_errors=True)
    except Exception:
        pass
    return jsonify({'ok': True})


def _install_builtin_skills():
    """Install/update built-in skills bundled with MC.

    Called from __main__ on startup. Safe to run on every boot: install_builtins
    is checksum-aware and preserves user modifications.
    """
    try:
        builtin_root = _APP_DIR / 'data' / 'skills' / 'builtin'
        if not builtin_root.exists():
            return
        result = _skills.install_builtins(builtin_root)
        installed = result.get('installed') or []
        updated = result.get('updated') or []
        preserved = result.get('preserved') or []
        if installed or updated:
            _log(f"[skills] installed={installed} updated={updated}")
        if preserved:
            _log(f"[skills] preserved user-modified builtins: {preserved}")
    except Exception as e:
        _log(f"[skills] builtin install failed: {e}")


def _install_builtin_mcps():
    """Install/update built-in MCP servers bundled with MC.

    Mirrors `_install_builtin_skills`. Two passes:

    1. Global builtins → seeded into ~/.claude.json once. Marker sidecar
       lives in `data/mc_builtin_mcps_global.json` (NOT under data/projects/
       so `load_projects()` ignores it — see CLAUDE.md DATA_DIR pollution rule).
    2. Project builtins → seeded into each existing project's
       `<project_path>/.mcp.json` (filesystem MCP bound to project_path).
       Acts as the backfill for projects that pre-date this feature; new
       projects also get it via `update_project()` is_new=True (see hook).

    Safe to run on every boot: checksum-aware and preserves user modifications.
    """
    try:
        builtin_root = _APP_DIR / 'data' / 'mcp' / 'builtin'
        if not builtin_root.exists():
            return
        # Global pass.
        marker_dir = _APP_DIR / 'data'
        gres = _mcp.install_global_builtins(builtin_root, marker_dir)
        installed = gres.get('installed') or []
        updated = gres.get('updated') or []
        preserved = gres.get('preserved') or []
        if installed or updated:
            _log(f"[mcp] global installed={installed} updated={updated}")
        if preserved:
            _log(f"[mcp] global preserved user-modified: {preserved}")

        # Per-project backfill.
        try:
            projects = load_projects()
        except Exception:
            projects = []
        for p in projects:
            pp = (p.get('project_path') or '').strip()
            if not pp:
                continue
            try:
                pres = _mcp.install_project_builtins(builtin_root, pp)
                pinst = pres.get('installed') or []
                pupd = pres.get('updated') or []
                pprev = pres.get('preserved') or []
                if pinst or pupd:
                    _log(f"[mcp] project {p.get('id')!r} installed={pinst} updated={pupd}")
                if pprev:
                    _log(f"[mcp] project {p.get('id')!r} preserved user-modified: {pprev}")
            except Exception as e:
                _log(f"[mcp] project {p.get('id')!r} builtin install failed: {e}")
    except Exception as e:
        _log(f"[mcp] builtin install failed: {e}")


