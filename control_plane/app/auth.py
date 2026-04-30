"""Auth helpers for FastAPI route dependencies.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

Three schemes (`docs/remote-access/03-control-plane-api.md` §2):

  - parse_device_auth: extract device_id from `Authorization: MC-Device ...`
                       header. Body signature verification happens later in
                       verify.py — this just gets us the device_id string.

  - firebase_user:    Firebase ID token → decoded claims dict.
                      (Stub for now; routes that need it call this directly.)

  - operator_user:    operator JWT issued by Google IAP.
                      (Stub for now; admin endpoints not in v1 scope.)

The parsers raise HTTPException so they integrate cleanly with FastAPI
`Depends()`.
"""
from __future__ import annotations

import re
from typing import Optional

from fastapi import HTTPException, Header, Request


# ─── MC-Device header (attestation flow) ─────────────────────────────────────


# Authorization header shape (per `03-` §2.2):
#   MC-Device device_id="<id>",sig_alg="ed25519"
_MC_DEVICE_RE = re.compile(
    r'^\s*MC-Device\s+device_id="(?P<device_id>[A-Za-z0-9_\-]+)"(?:\s*,\s*sig_alg="(?P<sig_alg>[A-Za-z0-9]+)")?\s*$'
)


def parse_device_auth(authorization: Optional[str]) -> str:
    """Parse `Authorization: MC-Device ...` and return the device_id.

    Raises HTTPException(401) on malformed / missing header.
    """
    if not authorization:
        raise HTTPException(status_code=401, detail={
            "code": "unauthorized",
            "message": "Authorization header missing.",
        })
    m = _MC_DEVICE_RE.match(authorization)
    if not m:
        raise HTTPException(status_code=401, detail={
            "code": "unauthorized",
            "message": "Authorization header is not a valid MC-Device credential.",
        })
    sig_alg = m.group("sig_alg")
    if sig_alg and sig_alg.lower() != "ed25519":
        raise HTTPException(status_code=401, detail={
            "code": "unauthorized",
            "message": f"Unsupported sig_alg {sig_alg!r}; only ed25519 is accepted.",
        })
    return m.group("device_id")


def device_auth_dep(authorization: str = Header(..., alias="Authorization")) -> str:
    """FastAPI dependency wrapping parse_device_auth()."""
    return parse_device_auth(authorization)


def maybe_device_auth_dep(authorization: Optional[str] = Header(None, alias="Authorization")) -> Optional[str]:
    """Variant for endpoints (like GET /v1/nonce) where the device_id is also
    in the query string — the header is informational and we don't 401 on
    missing/bad."""
    if not authorization:
        return None
    try:
        return parse_device_auth(authorization)
    except HTTPException:
        return None


# ─── Firebase ID token (browser flows) ───────────────────────────────────────


async def firebase_user(authorization: str = Header(..., alias="Authorization")) -> dict:
    """Verify Firebase ID token and return decoded claims dict.

    Stub. Real impl uses firebase-admin SDK with project public keys cached.
    Raises HTTPException(401) on invalid/missing/expired.
    """
    raise NotImplementedError("firebase-admin SDK integration pending — wire up with §3 of SETUP_CHECKLIST.md")


# ─── Operator JWT (admin endpoints) ──────────────────────────────────────────


async def operator_user(authorization: str = Header(..., alias="Authorization")) -> dict:
    """Verify operator JWT issued by Google IAP. Stub for v1."""
    raise NotImplementedError("Operator JWT (IAP) integration pending — admin endpoints out of v1 scope")


# ─── IP hashing for attestation_log ──────────────────────────────────────────


def ip_hash(request: Request) -> Optional[str]:
    """Compute a daily-salted sha256 of the requesting IP for attestation_log.

    Per `04-abuse-prevention.md` §7 / `03-` §4.4 — coarse-grained, rotates daily.
    Returns None if the IP can't be determined.
    """
    import datetime as _dt
    import hashlib

    # Trust X-Forwarded-For only when running behind Cloud Run / a known proxy.
    # For local dev, request.client.host is the value.
    ip = request.client.host if request.client else None
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        # Take the leftmost (original client) value
        ip = fwd.split(",")[0].strip() or ip
    if not ip:
        return None
    daily_salt = _dt.date.today().isoformat()
    return hashlib.sha256(f"{daily_salt}:{ip}".encode("utf-8")).hexdigest()
