"""Public, unauthenticated endpoints.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

Routes:
  GET  /v1/health
  GET  /v1/connect       (HTML; opens Firebase signin → /v1/enroll)
  POST /v1/signin/start
  POST /v1/signin/complete
  POST /v1/webhooks/cloudflare

See `docs/remote-access/03-control-plane-api.md` §3.1–3.4 + §5.4.
"""
from __future__ import annotations

import datetime as _dt

from fastapi import APIRouter

from . import build_info

router = APIRouter()


@router.get("/health", tags=["public"])
async def health() -> dict:
    """Cloud Run / load balancer probe. See doc §3.1."""
    # TODO: deep-check Firestore + Memorystore (or Firestore-only if we drop
    # Memorystore per `06-` §13.2). For now, shallow.
    return {
        "status": "ok",
        "build": build_info.VERSION,
        "time": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }


# TODO: GET /connect (HTML signin page)
# TODO: POST /signin/start
# TODO: POST /signin/complete
# TODO: POST /webhooks/cloudflare
