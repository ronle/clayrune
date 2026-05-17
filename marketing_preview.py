"""Marketing-site preview (dev convenience).

Extracted verbatim from server.py — IMPROVEMENT_PLAN_V2.md P1-1 /
docs/SERVER_SPLIT_PLAN.md Tier 1 (step a). Behavior unchanged: same
routes, same logic, same `Path(__file__).parent` resolution (this
module lives in the repo root next to server.py, and is co-bundled at
the same PyInstaller _MEIPASS root when frozen, so the marketing/ dir
resolves identically).

Lets you iterate on marketing/index.html etc. by hitting
http://localhost:5199/marketing/ in a browser instead of spinning up a
separate http server. Also reachable through the Cloudflare tunnel
(clayrune.io/marketing/) once remote access is enabled, which is how
this preview is useful from a phone before the real website goes live.
Not a production hosting path — when the site ships it'll be served by
Cloudflare Pages directly off the marketing/ folder, not by Flask.

Rollback: revert the extraction commit (re-inlines the route in
server.py). No state/schema involved.
"""
from pathlib import Path

from flask import Blueprint, send_from_directory

bp = Blueprint('marketing_preview', __name__)


@bp.route('/marketing/')
@bp.route('/marketing/<path:filename>')
def serve_marketing(filename='index.html'):
    marketing_dir = Path(__file__).parent / 'marketing'
    # Directory-style URLs (e.g. /marketing/v2/) — Flask hands us the path
    # as 'v2/' but send_from_directory expects a file. Map to index.html.
    target = (marketing_dir / filename)
    if target.is_dir():
        filename = filename.rstrip('/') + '/index.html'
    return send_from_directory(str(marketing_dir), filename)


def register(app):
    """Attach the marketing-preview blueprint. Called once from server.py."""
    app.register_blueprint(bp)
