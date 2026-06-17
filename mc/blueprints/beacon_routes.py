"""Beacon endpoints — cross-project situational digest (blueprint).

Thin Flask layer over the framework-agnostic `beacon/` package. server.py wires
the package's dependencies here via wire() → beacon.configure(), then registers
this blueprint (the distiller_routes 1.5 precedent).

Routes:
  GET  /api/beacon/digest          — the triaged snapshot the view loads
  GET  /api/beacon/stream          — SSE; pushes the digest when it changes
  POST /api/beacon/refresh/<id>    — force a live brief regen for one project

Brief/spec: data/uploads/agent_0fd9f3689b.md ("Beacon: Cross-Project
Situational Digest"), Phase 1 §1.6.
"""
import json
import time

from flask import Blueprint, Response, jsonify

from mc.core import _log

import beacon

bp = Blueprint('beacon_routes', __name__)


def wire(*, data_root, load_projects_fn, load_project_fn, live_agent_fn,
         get_memory_path_fn):
    """Inject the framework-agnostic package's dependencies (called once by
    server.py before register_blueprint)."""
    beacon.configure(
        data_root=data_root,
        load_projects_fn=load_projects_fn,
        load_project_fn=load_project_fn,
        live_agent_fn=live_agent_fn,
        get_memory_path_fn=get_memory_path_fn,
        log_fn=_log,
    )


@bp.route('/api/beacon/digest')
def beacon_digest():
    return jsonify(beacon.build_digest())


@bp.route('/api/beacon/stream')
def beacon_stream():
    """Poll-based SSE: re-derive the digest and emit only when it changes. This
    matches the codebase's existing poll-then-yield SSE pattern (agent/terminal
    streams) rather than inventing a pub/sub transport. Lifetime-capped so a
    dropped client can't leak a worker thread forever; the view reconnects."""
    def gen():
        last = None
        # Emit the current snapshot immediately, then watch for ~30 min.
        for tick in range(601):
            try:
                digest = beacon.build_digest()
                payload = json.dumps(digest, sort_keys=True, ensure_ascii=False)
            except Exception as e:
                _log(f"[beacon] stream build failed: {e}")
                payload = None
            if payload and payload != last:
                yield f"data: {payload}\n\n"
                last = payload
            elif tick % 15 == 0:
                # Heartbeat comment keeps proxies/clients from idling out.
                yield ": keepalive\n\n"
            time.sleep(3)

    return Response(gen(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache',
                             'X-Accel-Buffering': 'no'})


@bp.route('/api/beacon/refresh/<project_id>', methods=['POST'])
def beacon_refresh(project_id):
    ok = beacon.refresh(project_id)
    status = 200 if ok else 502
    return jsonify({'ok': ok, 'project_id': project_id,
                    'heartbeat': beacon.read_heartbeat(project_id)}), status
