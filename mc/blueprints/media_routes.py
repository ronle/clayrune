"""Media gallery endpoints — /api/project/<id>/media.

Read side of mc/media.py. The write side is a hook on the two agent stream
readers (mc/blueprints/agent_routes.py), which feed the recorder the assistant's
VISIBLE text as it arrives.
"""
from flask import Blueprint, jsonify

from mc import media as _media

bp = Blueprint('media', __name__)


@bp.route('/api/project/<project_id>/media')
def list_media_route(project_id):
    """Diagrams + images this project's agents produced, newest first."""
    items = _media.list_media(project_id)
    return jsonify({
        'items': items,
        'count': len(items),
        # The gallery is forward-only by design (no backfill of the 155MB of
        # existing transcripts) — the UI says so when the list is empty, so it
        # doesn't read as "broken".
        'forward_only': True,
    })


@bp.route('/api/project/<project_id>/media', methods=['DELETE'])
def clear_media_route(project_id):
    ok = _media.clear(project_id)
    return (jsonify({'ok': True}) if ok
            else (jsonify({'error': 'clear failed'}), 500))
