"""Agent character endpoints — Prompt Builder Phase 1
(docs/PROMPT_BUILDER_DESIGN.md §5.2).

Thin glue over mc/characters.py, same shape as skills_routes: the logic
module owns IO/validation, routes wrap it. Characters are standard Claude
Code subagent files under `.claude/agents/` (global or project scope);
the UI word for them is "Characters" — never "agents", which is taken by
MC's dispatched session.
"""

from typing import Any, Callable

from flask import Blueprint, jsonify, request

from mc import characters as _chars
from mc.blueprints.skills_routes import _resolve_project_path_or_400

bp = Blueprint('characters', __name__)

# ── wired by server.py (see wire()) ──────────────────────────────────────────
load_project: Callable[[str], Any] = None  # type: ignore[assignment]


def wire(*, load_project_fn):
    """Late-bind the projects-family accessor (same pattern as 1.3/1.9)."""
    global load_project
    load_project = load_project_fn


def _project_path_for_list(project_id: str | None) -> str | None:
    if not project_id:
        return None
    p = load_project(project_id)
    if not p:
        return None
    return p.get('project_path') or None


@bp.route('/api/characters')
def list_characters_route():
    """Global pool + (optionally) one project's pool.

    Query params:
      project_id: include this project's local characters (shadow-flags
                  same-named globals)
      q: substring filter on name+description
    """
    project_id = request.args.get('project_id')
    q = (request.args.get('q') or '').strip().lower()
    project_path = _project_path_for_list(project_id)
    items = _chars.list_characters(project_path=project_path,
                                   project_id=project_id)
    if q:
        items = [c for c in items
                 if q in (c.get('name', '') + ' ' + c.get('description', '')).lower()]
    return jsonify(items)


@bp.route('/api/characters', methods=['POST'])
def create_character_route():
    data = request.get_json() or {}
    name = (data.get('name') or '').strip()
    description = (data.get('description') or '').strip()
    body = data.get('body') or ''
    scope = (data.get('scope') or 'project').strip()
    project_id = data.get('project_id')
    overwrite = bool(data.get('overwrite'))

    if scope not in ('global', 'project'):
        return jsonify({'error': 'scope must be global|project'}), 400
    project_path, err = _resolve_project_path_or_400(scope, project_id)
    if err:
        return err

    try:
        rec = _chars.write_character(scope, name, description, body,
                                     project_path=project_path,
                                     overwrite=overwrite)
    except FileExistsError as e:
        return jsonify({'error': str(e)}), 409
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except OSError as e:
        return jsonify({'error': f'write failed: {e}'}), 500
    return jsonify(rec), 201


@bp.route('/api/characters/<scope>/<name>')
def read_character_route(scope, name):
    if scope not in ('global', 'project'):
        return jsonify({'error': 'scope must be global|project'}), 400
    project_id = request.args.get('project_id')
    project_path, err = _resolve_project_path_or_400(scope, project_id)
    if err:
        return err
    rec = _chars.read_character(scope, name, project_path=project_path,
                                project_id=project_id)
    if not rec:
        return jsonify({'error': 'character not found'}), 404
    return jsonify(rec)


@bp.route('/api/characters/<scope>/<name>', methods=['PUT'])
def update_character_route(scope, name):
    if scope not in ('global', 'project'):
        return jsonify({'error': 'scope must be global|project'}), 400
    data = request.get_json() or {}
    project_id = data.get('project_id')
    project_path, err = _resolve_project_path_or_400(scope, project_id)
    if err:
        return err

    existing = _chars.read_character(scope, name, project_path=project_path)
    if not existing:
        return jsonify({'error': 'character not found'}), 404
    description = (data.get('description') or existing.get('description') or '').strip()
    body = data.get('body')
    if body is None:
        body = existing.get('body') or ''

    try:
        rec = _chars.write_character(scope, name, description, body,
                                     project_path=project_path,
                                     overwrite=True)
    except ValueError as e:
        return jsonify({'error': str(e)}), 400
    except OSError as e:
        return jsonify({'error': f'write failed: {e}'}), 500
    return jsonify(rec)


@bp.route('/api/characters/<scope>/<name>', methods=['DELETE'])
def delete_character_route(scope, name):
    if scope not in ('global', 'project'):
        return jsonify({'error': 'scope must be global|project'}), 400
    project_id = request.args.get('project_id')
    project_path, err = _resolve_project_path_or_400(scope, project_id)
    if err:
        return err
    try:
        ok = _chars.delete_character(scope, name, project_path=project_path)
    except OSError as e:
        return jsonify({'error': f'delete failed: {e}'}), 500
    if not ok:
        return jsonify({'error': 'character not found'}), 404
    return jsonify({'ok': True})
