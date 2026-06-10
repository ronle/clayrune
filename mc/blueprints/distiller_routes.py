"""Distiller (self-learning) endpoints — blueprint 1.5 (MODERNIZATION_PLAN.md).

Moved VERBATIM from server.py. Thin glue: distiller.py owns the logic.
7 routes (plan table said 5 — loop-health + proposed-artifact landed after):
5 /api/distiller/* + 2 /api/project/<id>/distiller* (feature cohesion, same
call as 1.2/1.4). /api/router/stats and /api/project/<id>/memory/search sat
inside the same source region but are dispatch/memory family — left behind.
"""

import json
import os
import time as _time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from flask import Blueprint, jsonify, request

from mc import state
from mc.core import _log

import distiller as _distiller
import skills as _skills

from mc.blueprints.skills_routes import _resolve_project_path_or_400

bp = Blueprint('distiller_routes', __name__)

# ── wired by server.py (see wire()) ──────────────────────────────────────────
load_project: Callable[[str], Any] = None  # type: ignore[assignment]
DATA_DIR: Path = None  # type: ignore[assignment]


def wire(*, load_project_fn, data_dir):
    """Late-bind projects-family deps (1.11 re-homes them)."""
    global load_project, DATA_DIR
    load_project = load_project_fn
    DATA_DIR = data_dir


# ── Phase 4 Distiller endpoints (v2.1 §7) ────────────────────────────────────

@bp.route('/api/project/<project_id>/distiller-stats', methods=['GET'])
def get_distiller_stats(project_id):
    """Distiller telemetry — mirrors /scribe-stats shape. Includes recurrence
    `fingerprints_near_threshold` so operator can see whether the threshold
    is plausibly reachable (Seat 1 v1.1 Cond 3 inherited)."""
    try:
        return jsonify(_distiller.get_distiller_stats(project_id))
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/project/<project_id>/distiller/record-push',
           methods=['POST'])
def post_distiller_record_push(project_id):
    """In-session mc-distill calls this on No / Later. Body:
      {phrase, kind, decision}. Server re-normalizes the phrase
    through closed-vocab fingerprint (single source of truth — C-G
    closure)."""
    body = request.get_json(silent=True) or {}
    try:
        result, status = _distiller.record_push(project_id, body)
        return jsonify(result), status
    except Exception as e:
        return jsonify({'accepted': False, 'reason': str(e)}), 500


@bp.route('/api/distiller/_proposed', methods=['GET'])
def get_distiller_proposed():
    """Unified _proposed/ queue lister. Walks global/ + <project_id>/
    subdirs AND tolerates legacy flat _proposed/<sid>/ entries (§3.0
    D13 closure). Newest first."""
    try:
        return jsonify(_distiller.list_proposed())
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@bp.route('/api/distiller/loop-health', methods=['GET'])
def get_distiller_loop_health():
    """Learning-loop health snapshot — the self-detection layer (step 2 of the
    2026-06-05 plan). Aggregates per-project counters + the _proposed/ queue
    into generation/refuse/readback/queue signals with an `alerts` list, so a
    degraded leg surfaces on its own. Enriches queue timestamps with day-age.
    Read-only; never mutates state."""
    try:
        snap = _distiller.loop_health()
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    # Enrich queue staleness with day-age (datetime lives server-side; the
    # distiller deliberately stays datetime-free, using only ISO strings).
    try:
        now = datetime.now(timezone.utc)
        for key in ('oldest_created_at', 'newest_created_at'):
            ca = snap.get('queue', {}).get(key)
            if ca:
                try:
                    dt = datetime.fromisoformat(ca.replace('Z', '+00:00'))
                    snap['queue'][key.replace('_created_at', '_age_days')] = \
                        round((now - dt).total_seconds() / 86400, 1)
                except Exception:
                    pass
    except Exception:
        pass
    return jsonify(snap)


@bp.route('/api/distiller/promote', methods=['POST'])
def post_distiller_promote():
    """Promote a _proposed/ artifact into a real SKILL.md (the human-promotes
    leg — step 3). Body: {directory, scope: 'project'|'global', project_id?}.
    Installs via skills.write_skill (overwrite), then distiller.mark_promoted
    suppresses re-proposal + moves the proposal to _promoted/. SKILL artifacts
    carry their own TRIGGER description; EXPLORATION/PREFERENCE get a synthesized
    one the user can edit afterward (this is also the step-4 bridge — a great
    exploration becomes a skill by a deliberate human click)."""
    body = request.get_json(silent=True) or {}
    directory = body.get('directory', '')
    scope = (body.get('scope') or 'project').strip()
    project_id = body.get('project_id') or None
    if scope not in ('project', 'global'):
        return jsonify({'ok': False, 'error': 'scope must be project or global'}), 400
    try:
        art = _distiller.read_proposed_artifact(directory)
        if art is None:
            return jsonify({'ok': False,
                            'error': 'artifact not found or outside _proposed/'}), 404
        project_path = None
        if scope == 'project':
            project_id = project_id or art.get('project_id')
            if not project_id:
                return jsonify({'ok': False,
                                'error': 'project_id required for project-scope promote '
                                         '(cross-project artifact — choose global or pass project_id)'}), 400
            project_path, err = _resolve_project_path_or_400(scope, project_id)
            if err:
                return err
        rec = _skills.write_skill(
            name=art['name'],
            description=art['description'],
            body=art['body'],
            scope=scope,
            project_path=project_path,
            project_id=project_id,
            extra_meta={
                'provenance': 'distilled-promoted',
                'promoted_from': art['kind'],
                'source_session': art.get('source_session', ''),
                'extraction_fingerprint_exact': art.get('exact', ''),
            },
            overwrite=True,
        )
        mark = _distiller.mark_promoted(directory)
        return jsonify({'ok': True, 'installed': rec, 'mark': mark})
    except ValueError as e:
        return jsonify({'ok': False, 'error': str(e)}), 400
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@bp.route('/api/distiller/reject', methods=['POST'])
def post_distiller_reject():
    """Reject a _proposed/ artifact: write a suppression marker (Distiller
    won't re-propose) + move it to _rejected/. Body: {directory}."""
    body = request.get_json(silent=True) or {}
    directory = body.get('directory', '')
    try:
        result = _distiller.reject_proposed(directory)
        if not result.get('ok') and result.get('reason') == 'not_found':
            return jsonify({'ok': False,
                            'error': 'artifact not found or outside _proposed/'}), 404
        return jsonify(result)
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@bp.route('/api/distiller/proposed-artifact', methods=['GET'])
def get_distiller_proposed_artifact():
    """Full content of one _proposed/ artifact (kind/title/description/body),
    for the Learning-queue expand-to-read action. Path-guarded in the
    distiller. Query: ?directory=<path>."""
    directory = request.args.get('directory', '')
    try:
        art = _distiller.read_proposed_artifact(directory)
        if art is None:
            return jsonify({'error': 'artifact not found or outside _proposed/'}), 404
        return jsonify(art)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
