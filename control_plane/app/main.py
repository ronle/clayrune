"""ASGI entrypoint for the control plane.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

Routes are organized by auth scheme:
  - public       (no auth)
  - account      (Firebase ID token)
  - attestation  (device signature + client signature)
  - admin        (operator JWT, behind IAP)

Each subrouter file mirrors a section of `docs/remote-access/03-control-plane-api.md`.
"""
from __future__ import annotations

import logging
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import build_info
from .routes_public import router as public_router
from .routes_attest import router as attest_router
from .routes_account import router as account_router
# Pending implementation:
# from .routes_admin import router as admin_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("control_plane")

app = FastAPI(
    title="Mission Control Cloud — control plane",
    version=build_info.VERSION,
    docs_url="/docs" if os.environ.get("ENV") != "prod" else None,
    redoc_url=None,
    openapi_url="/openapi.json" if os.environ.get("ENV") != "prod" else None,
)

# CORS: only the platform origins should ever call us cross-origin.
# Tighten on the way to production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("ALLOWED_ORIGINS", "https://clayrune.io,https://app.clayrune.io").split(","),
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"],
    allow_headers=["*"],
    allow_credentials=True,
)

app.include_router(public_router, prefix="/v1")
app.include_router(attest_router, prefix="/v1")
app.include_router(account_router, prefix="/v1")
# app.include_router(admin_router, prefix="/v1/admin")


# Flatten HTTPException(detail={"code": ..., "message": ..., ...}) into the
# protocol's flat error envelope (see `error_codes.md`). FastAPI's default
# wraps the dict in `{"detail": <dict>}`, which doesn't match the contract.
@app.exception_handler(HTTPException)
async def _flat_error_envelope(_request: Request, exc: HTTPException):
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"code": "internal_error",
                                                              "message": str(exc.detail),
                                                              "request_id": "unknown"})


@app.on_event("startup")
async def on_startup() -> None:
    log.info("control plane starting: build=%s env=%s", build_info.VERSION, os.environ.get("ENV", "dev"))


@app.on_event("shutdown")
async def on_shutdown() -> None:
    log.info("control plane shutting down")
