"""Attestation endpoints: GET /v1/nonce + POST /v1/attest.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

The load-bearing surface. See:
  - `docs/remote-access/02-attestation-protocol.md` §7 (envelope, verification)
  - `docs/remote-access/03-control-plane-api.md` §3.6, §3.7
  - `app/verify.py` (the 14+1-step list)

Latency budget: p99 ≤ 300ms (`03-` §8.4). Avoid synchronous CF API calls
in the request path — tunnel-token issuance reads from a per-device pool
in Firestore (or, in v1 dev, returns a stub token).
"""
from __future__ import annotations

import datetime as _dt
import logging
import secrets
import time
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Header, Query, Request

from . import auth, firestore as fs, verify
from .schemas import AttestationRequest

router = APIRouter()
log = logging.getLogger(__name__)


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _now_iso(offset_s: float = 0.0) -> str:
    return (_dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(seconds=offset_s)) \
        .isoformat(timespec="seconds").replace("+00:00", "Z")


def _err(code: str, message: str, *, request_id: str, retry_after_ms: Optional[int] = None) -> dict:
    body = {"code": code, "message": message, "request_id": request_id}
    if retry_after_ms is not None:
        body["retry_after_ms"] = retry_after_ms
    return body


# ─── /v1/nonce ───────────────────────────────────────────────────────────────


@router.get("/nonce", tags=["attestation"])
async def issue_nonce(
    request: Request,
    device_id: str = Query(..., description="Device id from enrollment"),
    auth_device_id: Optional[str] = Depends(auth.maybe_device_auth_dep),
):
    """Issue a single-use attestation nonce. Per `03-` §3.6.

    The header's device_id (if present) MUST match the query parameter when
    both are supplied. We accept query-only to keep this a simple GET that
    works from a curl command.
    """
    if auth_device_id and auth_device_id != device_id:
        raise HTTPException(status_code=401, detail=_err(
            "unauthorized",
            "Authorization header device_id doesn't match query parameter.",
            request_id=_request_id(request),
        ))

    # Sanity: confirm device exists. Not strictly required by protocol
    # (attest's verify chain catches it later), but rejects nonsense early.
    if fs.device_by_id(device_id) is None:
        raise HTTPException(status_code=401, detail=_err(
            "unknown_device",
            "Device not enrolled.",
            request_id=_request_id(request),
        ))

    nonce_id = uuid.uuid4().hex
    nonce_value = secrets.token_urlsafe(32)
    fs.nonce_create(device_id=device_id, nonce_id=nonce_id, nonce=nonce_value, ttl_seconds=30)

    return {
        "nonce": nonce_value,
        "nonce_id": nonce_id,
        "expires_at": _now_iso(30),
    }


# ─── /v1/attest ──────────────────────────────────────────────────────────────


@router.post("/attest", tags=["attestation"])
async def attest(
    request: Request,
    auth_device_id: str = Depends(auth.device_auth_dep),
):
    """Verify the dual-signed envelope; issue a tunnel token on success.

    Implements the full 14+1 verification chain via `verify.verify()`. On
    success, returns the issuance payload from `02-` §7.5.

    Reads the raw request body so the canonical-JSON hash is computed from
    the exact bytes the client signed (Pydantic round-trip would change
    them by adding nulls for optional missing fields).

    v1 simplification: tunnel_token in the response is currently a placeholder
    string. When the real CF tunnel provisioning lands (per `03-` §5.1),
    this is replaced with a real CF-issued tunnel token.
    """
    started = time.monotonic()
    rid = _request_id(request)

    # Read raw body and parse manually for the inner envelope dict.
    body_bytes = await request.body()
    try:
        import json as _json
        raw_body = _json.loads(body_bytes)
    except Exception as e:
        raise HTTPException(status_code=400, detail=_err(
            "malformed_json", f"Could not parse request body: {e}", request_id=rid,
        ))

    raw_envelope = raw_body.get("envelope") if isinstance(raw_body, dict) else None
    if not isinstance(raw_envelope, dict):
        raise HTTPException(status_code=400, detail=_err(
            "bad_envelope", "Envelope must be a JSON object.", request_id=rid,
        ))

    # Validate via Pydantic (structural check). On failure, raise a clean
    # 400 — Pydantic's default 422 is uglier and exposes too much detail.
    try:
        req = AttestationRequest.model_validate(raw_body)
    except Exception as e:
        raise HTTPException(status_code=400, detail=_err(
            "bad_envelope", f"Schema validation failed: {e}", request_id=rid,
        ))

    # Header device_id must match envelope device_id (defense against header/body mismatch).
    if auth_device_id != req.envelope.device_id:
        raise HTTPException(status_code=401, detail=_err(
            "unauthorized",
            "Authorization header device_id doesn't match envelope.",
            request_id=rid,
        ))

    try:
        result = await verify.verify(req, raw_envelope=raw_envelope)
    except verify.AttestationFailure as e:
        # Log the failure for forensics (not the envelope contents)
        try:
            fs.attestation_log_append(
                device_id=auth_device_id,
                user_id=req.envelope.device_id,  # best effort if device row missing
                result=e.code,
                mc_version=req.envelope.mc_version,
                client_secret_key_id=req.envelope.client_secret_key_id,
                os_str=req.envelope.os,
                ip_hash=auth.ip_hash(request),
                nonce_id=None,
                latency_ms=int((time.monotonic() - started) * 1000),
            )
        except Exception:
            pass
        raise HTTPException(status_code=e.http_status, detail=_err(e.code, e.message, request_id=rid))
    except Exception as e:
        log.exception("attestation crashed: %s", e)
        raise HTTPException(status_code=503, detail=_err(
            "internal_error", "Internal error; retry.", request_id=rid, retry_after_ms=2000,
        ))

    # Success: log + issue token.
    try:
        fs.attestation_log_append(
            device_id=result.device["device_id"],
            user_id=result.device.get("user_id", ""),
            result="ok",
            mc_version=req.envelope.mc_version,
            client_secret_key_id=result.client_key_id,
            os_str=req.envelope.os,
            ip_hash=auth.ip_hash(request),
            nonce_id=result.nonce_id,
            latency_ms=int((time.monotonic() - started) * 1000),
        )
    except Exception as e:
        log.warning("attestation_log_append failed: %s", e)

    # Update the device row's last_seen + version metadata so /v1/devices
    # can show meaningful "Last seen X minutes ago" + current MC version.
    try:
        import datetime as _dt
        fs.db().collection(fs.COL_DEVICES).document(result.device["device_id"]).set({
            "last_seen": _dt.datetime.now(_dt.timezone.utc),
            "mc_version": req.envelope.mc_version,
            "os": req.envelope.os,
            "client_secret_key_id": req.envelope.client_secret_key_id,
            "last_attestation_result": "ok",
        }, merge=True)
    except Exception as e:
        log.warning("device row last_seen update failed: %s", e)

    # Token issuance.
    # v1 dev: placeholder token. Real impl pulls a per-user token issued
    # at /v1/enroll time (CF tunnel + token + DNS already provisioned).
    tunnel_token = result.device.get("cf_tunnel_token") or f"PLACEHOLDER_TOKEN_{secrets.token_urlsafe(24)}"

    return {
        "envelope_type": "attestation_response",
        "result": "ok",
        "tunnel_token": tunnel_token,
        "tunnel_token_id": f"tt_{uuid.uuid4().hex[:16]}",
        "tunnel_token_expires_at": _now_iso(15 * 60),
        "next_attestation_after": _now_iso(10 * 60),
        "caps": {
            "bandwidth_bytes_remaining_period": 5 * 1024 ** 3,
            "bandwidth_used_period_bytes": 0,
            "rate_limit_rps": 60,
            "max_response_bytes": 10 * 1024 ** 2,
            "max_concurrent_connections": 20,
        },
        "directives": [],
    }


def _request_id(request: Request) -> str:
    rid = request.headers.get("x-request-id")
    if rid:
        return rid
    return f"req_{uuid.uuid4().hex[:12]}"
