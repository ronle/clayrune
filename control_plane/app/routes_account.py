"""Account / browser-session endpoints (Firebase ID token auth in v1, dev shim available).

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

Implemented:
  POST /v1/enroll                   — provisions a CF tunnel + DNS + Access app,
                                      persists user + device, returns enrollment_token

Pending:
  GET    /v1/account
  DELETE /v1/account
  POST   /v1/account/username
  GET    /v1/devices
  POST   /v1/devices/{id}/rename
  POST   /v1/devices/{id}/revoke

Auth: in v1 dev (MC_CP_DEV_AUTH=1), X-Dev-User-Email header authorizes the
caller as that email (no signin verification). When Firebase Auth is wired
(SETUP_CHECKLIST.md §3), the dev shim is gated off and Firebase ID token
verification kicks in.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import logging
import os
import re
import secrets
import time
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Query, Request

from . import cloudflare, firestore as fs

router = APIRouter()
log = logging.getLogger(__name__)


# ─── GET /v1/devices ──────────────────────────────────────────────────────────


@router.get("/devices", tags=["account"])
async def list_devices(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_dev_user_email: Optional[str] = Header(None, alias="X-Dev-User-Email"),
    x_mc_device_auth: Optional[str] = Header(None, alias="X-MC-Device-Auth"),
    x_mc_device_id: Optional[str] = Header(None, alias="X-MC-Device-Id"),
):
    """List all non-revoked devices owned by the authenticated user.

    Auth: Firebase ID token (production) or X-Dev-User-Email (dev shim).
    `X-MC-Device-Id` is optional — if provided, the matching device row
    gets `is_this_device: true` so the UI can highlight the one the user
    is currently looking from.

    `online` is a heuristic: True iff `last_seen` is within the last 15 min
    (covers two attestation cycles + a healthy buffer).
    """
    rid = _request_id(request)

    try:
        user = _resolve_user(authorization, x_dev_user_email, device_auth=x_mc_device_auth)
    except HTTPException as e:
        d = dict(e.detail) if isinstance(e.detail, dict) else {"code": "unauthorized",
                                                                "message": str(e.detail)}
        d["request_id"] = rid
        raise HTTPException(status_code=e.status_code, detail=d)

    db = fs.db()
    now = _dt.datetime.now(_dt.timezone.utc)
    online_window_s = 15 * 60

    docs = list(db.collection(fs.COL_DEVICES)
                  .where("user_id", "==", user["user_id"]).stream())

    devices: list[dict] = []
    for d in docs:
        row = d.to_dict() or {}
        if row.get("revoked_at"):
            continue

        # Convert Firestore datetimes to ISO strings; handle None
        def _iso(v):
            if v is None:
                return None
            try:
                return v.isoformat(timespec="seconds").replace("+00:00", "Z")
            except Exception:
                return str(v)

        last_seen = row.get("last_seen")
        online = False
        if last_seen is not None:
            try:
                age_s = (now - last_seen).total_seconds()
                online = age_s < online_window_s
            except Exception:
                pass

        devices.append({
            "device_id": d.id,
            "device_name": row.get("device_name") or "Unnamed device",
            "hostname": row.get("hostname_claim") or "",
            "os": row.get("os") or "",
            "mc_version": row.get("mc_version") or "",
            "online": online,
            "last_seen": _iso(last_seen),
            "enrolled_at": _iso(row.get("enrolled_at")),
            "last_attestation_result": row.get("last_attestation_result"),
            "is_this_device": (x_mc_device_id is not None and d.id == x_mc_device_id),
        })

    # Sort: this-device first, then online, then by enrolled_at desc
    def _sort_key(d):
        return (
            not d["is_this_device"],     # this-device first
            not d["online"],              # then online
            d.get("enrolled_at") or "",   # newest enrollments first
        )
    devices.sort(key=_sort_key)

    # Pull user info for tier + cap
    user_snap = db.collection(fs.COL_USERS).document(user["user_id"]).get()
    user_data = (user_snap.to_dict() or {}) if user_snap.exists else {}

    return {
        "devices": devices,
        "tier": user_data.get("tier", "free"),
        "device_cap": int(user_data.get("device_cap", 2)),
    }


# ─── /v1/sessions (Cloudflare Access sessions for the user) ──────────────────


@router.get("/sessions", tags=["account"])
async def list_sessions(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_dev_user_email: Optional[str] = Header(None, alias="X-Dev-User-Email"),
    x_mc_device_auth: Optional[str] = Header(None, alias="X-MC-Device-Auth"),
):
    """List active Cloudflare Access sign-in sessions for the user's email.

    These are browser/phone sessions created by CF Access OTP signin. They are
    DIFFERENT from `/v1/devices` which lists enrolled MC installations.
    Anyone hitting `<username>.clayrune.io` who completes the email OTP
    creates a session row visible here.
    """
    rid = _request_id(request)
    try:
        user = _resolve_user(authorization, x_dev_user_email, device_auth=x_mc_device_auth)
    except HTTPException as e:
        d = dict(e.detail) if isinstance(e.detail, dict) else {"code": "unauthorized",
                                                                "message": str(e.detail)}
        d["request_id"] = rid
        raise HTTPException(status_code=e.status_code, detail=d)

    cf = _get_cf_client()
    acc = await cf.get_account_id()
    email = user["email"]

    # Find the CF Access user for this email
    try:
        users = await cf._call("GET", f"/accounts/{acc}/access/users", params={"email": email})
    except cloudflare.CloudflareAPIError as e:
        log.warning("rid=%s could not list CF Access users: %s", rid, e)
        return {"sessions": [], "cf_user_id": None, "error": "list_users_failed"}

    if not users:
        # User has never signed in — no sessions yet
        return {"sessions": [], "cf_user_id": None}

    cf_user = users[0]
    cf_user_id = cf_user.get("id")

    try:
        sessions_raw = await cf._call(
            "GET", f"/accounts/{acc}/access/users/{cf_user_id}/active_sessions",
        ) or []
    except cloudflare.CloudflareAPIError as e:
        log.warning("rid=%s could not list sessions for CF user %s: %s", rid, cf_user_id, e)
        return {"sessions": [], "cf_user_id": cf_user_id, "error": "list_sessions_failed"}

    # CF's active_sessions response shape (verified empirically against real
    # account 2026-04-29):
    #   {
    #     "result": [
    #       {
    #         "expiration": <unix-ts>,
    #         "name": "<account_id>_<user_id>_sessions_<nonce>",
    #         "metadata": {
    #           "apps": { "<app_uid_hash>": { "hostname":..., "name":..., "type":..., "uid":... }, ... },
    #           "expires": <unix-ts>,
    #           "iat": <unix-ts>,
    #           "nonce": "<short id>",
    #           "ttl": 86400
    #         }
    #       }
    #     ]
    #   }
    #
    # NOT in the response: user_agent, IP, country, identity provider used.
    # CF Access doesn't expose those via this API — they're surfaced only in
    # Access audit logs (different endpoint).
    def _flatten(s: dict) -> dict:
        meta = s.get("metadata") or {}
        apps_dict = meta.get("apps") or {}
        # Apps the session has been used to access — useful as a freshness
        # signal (a session that has touched many app UIDs has been around;
        # one that touched only the current app is fresher / single-use).
        apps_seen = []
        for _hash, info in apps_dict.items():
            host = info.get("hostname", "")
            if host:
                apps_seen.append(host)
        nonce = meta.get("nonce") or ""
        # Session id used for revocation: the full `name` field is what CF
        # accepts. The nonce is a friendlier display short-id.
        full_name = s.get("name") or ""
        return {
            "session_id": full_name,                  # for revoke
            "nonce": nonce,                            # for joining with MC-side labels
            "short_id": nonce[-6:] if nonce else "",  # for display
            "issued_at": meta.get("iat"),              # Unix seconds
            "expires_at": s.get("expiration") or meta.get("expires"),
            "ttl_seconds": meta.get("ttl"),
            "apps_seen_count": len(apps_seen),
            "apps_seen": list(set(apps_seen))[:5],
        }

    sessions = [_flatten(s) for s in sessions_raw]

    return {
        "sessions": sessions,
        "cf_user_id": cf_user_id,
        "email": email,
    }


@router.post("/sessions/{session_id}/revoke", tags=["account"])
async def revoke_session(
    session_id: str,
    request: Request,
    strict: bool = Query(False, description="If true, do not fall back to revoke-all"),
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_dev_user_email: Optional[str] = Header(None, alias="X-Dev-User-Email"),
    x_mc_device_auth: Optional[str] = Header(None, alias="X-MC-Device-Auth"),
):
    """Revoke one CF Access session.

    Tries multiple known CF API shapes for per-session revoke (the public API
    has been inconsistent across account configurations). If all fail and
    `strict=false` (default), falls back to revoking ALL sessions for the
    user — the frontend surfaces this via `fallback=true` so the user knows.

    With `strict=true` (used by automated cleanup loops), returns 503 on
    failure instead of nuking unrelated named sessions.
    """
    rid = _request_id(request)
    try:
        user = _resolve_user(authorization, x_dev_user_email, device_auth=x_mc_device_auth)
    except HTTPException as e:
        d = dict(e.detail) if isinstance(e.detail, dict) else {"code": "unauthorized",
                                                                "message": str(e.detail)}
        d["request_id"] = rid
        raise HTTPException(status_code=e.status_code, detail=d)

    cf = _get_cf_client()
    acc = await cf.get_account_id()
    email = user["email"]

    users = await cf._call("GET", f"/accounts/{acc}/access/users", params={"email": email})
    if not users:
        return _err_response(404, "no_sessions", "No CF Access user record for this email.", rid)
    cf_user_id = users[0].get("id")

    # Try per-session revoke. CF's API for this is poorly documented and the
    # accepted shape varies by account; try the most likely variants in order.
    # The session_id we received is the full canonical name from CF's listing
    # (`<account>_<user>_sessions_<nonce>`). Some endpoints want that whole
    # string; others want just the trailing nonce.
    nonce_only = session_id.rsplit("_sessions_", 1)[-1] if "_sessions_" in session_id else session_id
    attempts = [
        ("POST",   f"/accounts/{acc}/access/users/{cf_user_id}/active_sessions/{session_id}/revoke"),
        ("POST",   f"/accounts/{acc}/access/users/{cf_user_id}/active_sessions/{nonce_only}/revoke"),
        ("DELETE", f"/accounts/{acc}/access/users/{cf_user_id}/active_sessions/{session_id}"),
        ("DELETE", f"/accounts/{acc}/access/users/{cf_user_id}/active_sessions/{nonce_only}"),
    ]
    last_err: Optional[Exception] = None
    for method, path in attempts:
        try:
            await cf._call(method, path)
            return {"ok": True, "scope": "session", "method": method}
        except cloudflare.CloudflareAPIError as e:
            last_err = e
            continue

    log.info("rid=%s all per-session revoke variants failed (last=%s); strict=%s",
             rid, last_err, strict)
    if strict:
        return _err_response(503, "per_session_unsupported",
                             f"Per-session revoke not supported by CF for this token/account. "
                             f"Use revoke-all or expand token scope. Last error: {last_err}",
                             rid)

    # Fallback: revoke ALL sessions for the email
    try:
        await cf._call(
            "POST",
            f"/accounts/{acc}/access/organizations/revoke_user",
            json={"email": email},
        )
        return {"ok": True, "scope": "all_sessions", "fallback": True}
    except cloudflare.CloudflareAPIError as e:
        return _err_response(503, "revoke_failed",
                             f"Could not revoke session: {e}", rid)


@router.post("/sessions/revoke-all", tags=["account"])
async def revoke_all_sessions(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_dev_user_email: Optional[str] = Header(None, alias="X-Dev-User-Email"),
    x_mc_device_auth: Optional[str] = Header(None, alias="X-MC-Device-Auth"),
):
    """Revoke ALL CF Access sessions for the user's email.

    Forces every signed-in browser/phone to re-authenticate via OTP before
    accessing the dashboard. The MC supervisor's tunnel attestation is
    unaffected — this only kicks browser sessions, not the tunnel itself.
    """
    rid = _request_id(request)
    try:
        user = _resolve_user(authorization, x_dev_user_email, device_auth=x_mc_device_auth)
    except HTTPException as e:
        d = dict(e.detail) if isinstance(e.detail, dict) else {"code": "unauthorized",
                                                                "message": str(e.detail)}
        d["request_id"] = rid
        raise HTTPException(status_code=e.status_code, detail=d)

    cf = _get_cf_client()
    acc = await cf.get_account_id()
    email = user["email"]

    try:
        await cf._call(
            "POST",
            f"/accounts/{acc}/access/organizations/revoke_user",
            json={"email": email},
        )
    except cloudflare.CloudflareAPIError as e:
        return _err_response(503, "revoke_failed",
                             f"Could not revoke all sessions: {e}", rid)
    return {"ok": True, "email": email}


# ─── /v1/devices/{device_id}/revoke ───────────────────────────────────────────


@router.post("/devices/{device_id}/revoke", tags=["account"])
async def revoke_device(
    device_id: str,
    request: Request,
):
    """Device-self-revoke: delete CF resources + Firestore row + release username.

    For v1 dev: auth is via the body's `enrollment_token`, which the device
    persisted at /v1/enroll time. We compare its sha256 against the stored
    hash. Equivalent in trust to the device key (both are stored together in
    the OS keystore and only used together).

    Idempotent: if the device row is already gone, returns 200 + already_revoked.
    Best-effort on CF deletes — if any CF API call fails we still wipe the
    Firestore row + username claim so the user isn't stuck.
    """
    from fastapi import Body
    rid = _request_id(request)
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    enrollment_token = (body.get("enrollment_token") or "").strip()

    # Look up device
    device_row = fs.device_by_id(device_id)
    if device_row is None:
        return {"ok": True, "already_revoked": True, "reason": "device_not_found"}

    # Verify enrollment_token matches stored hash
    stored_hash = device_row.get("enrollment_token_hash", "")
    if not enrollment_token or not stored_hash:
        raise HTTPException(status_code=401, detail={
            "code": "bad_enrollment_token",
            "message": "enrollment_token required.",
            "request_id": rid,
        })
    provided_hash = hashlib.sha256(enrollment_token.encode("utf-8")).hexdigest()
    if not secrets.compare_digest(stored_hash, provided_hash):
        raise HTTPException(status_code=401, detail={
            "code": "bad_enrollment_token",
            "message": "enrollment_token mismatch.",
            "request_id": rid,
        })

    # Delete CF resources by stored ID (best-effort; force_cleanup is the catch-all)
    cf = _get_cf_client()
    deleted = {"access_app": False, "dns_record": False, "tunnel": False}

    if app_id := device_row.get("cf_access_app_id"):
        try:
            await cf.delete_access_app(app_id)
            deleted["access_app"] = True
        except Exception as e:
            log.warning("revoke: failed deleting access app %s: %s", app_id, e)

    if rec_id := device_row.get("cf_dns_record_id"):
        try:
            await cf.delete_dns_record(rec_id)
            deleted["dns_record"] = True
        except Exception as e:
            log.warning("revoke: failed deleting dns record %s: %s", rec_id, e)

    if tunnel_id := device_row.get("cf_tunnel_uuid"):
        try:
            await cf.delete_tunnel(tunnel_id)
            deleted["tunnel"] = True
        except Exception as e:
            log.warning("revoke: failed deleting tunnel %s: %s", tunnel_id, e)

    # Belt-and-suspenders: also run force_cleanup for any orphans missed
    hostname = device_row.get("hostname_claim", "")
    username = device_row.get("hostname_claim", "").split(".")[0]
    if hostname and username:
        try:
            await _force_cleanup_for_hostname(hostname=hostname, username=username)
        except Exception as e:
            log.warning("revoke: force_cleanup raised: %s", e)

    # Wipe Firestore device row + username claim
    try:
        fs.db().collection(fs.COL_DEVICES).document(device_id).delete()
    except Exception as e:
        log.warning("revoke: failed deleting devices/%s: %s", device_id, e)

    # Release username claim if this device's user owned it
    user_id = device_row.get("user_id", "")
    if username and user_id:
        try:
            uref = fs.db().collection("usernames").document(username)
            snap = uref.get()
            if snap.exists and (snap.to_dict() or {}).get("user_id") == user_id:
                uref.delete()
        except Exception as e:
            log.warning("revoke: failed releasing username %s: %s", username, e)

    return {"ok": True, "already_revoked": False, "deleted": deleted}


# ─── Username policy (matches `03-` §3.5) ────────────────────────────────────


_USERNAME_RE = re.compile(r"^[a-z0-9](-?[a-z0-9])*$")
_USERNAME_RESERVED = frozenset({
    # platform / infra
    "admin", "api", "app", "support", "help", "www", "ftp", "mail",
    "root", "mc", "clayrune", "dashboard", "cdn", "edge",
    # brand-impersonation defense (small starter list)
    "claude", "anthropic", "openai", "chatgpt", "google", "microsoft",
    "apple", "github", "twitter", "x",
})


def _is_username_valid(u: str) -> tuple[bool, str]:
    if not (3 <= len(u) <= 24):
        return False, "Username must be 3–24 characters."
    if not _USERNAME_RE.fullmatch(u):
        return False, "Username may contain only lowercase letters, numbers, and dashes."
    if u in _USERNAME_RESERVED:
        return False, "That username isn't available."
    return True, ""


# ─── Auth resolution (Firebase real, dev shim, or 401) ───────────────────────


_DEV_AUTH_ENABLED = os.environ.get("MC_CP_DEV_AUTH") == "1"


def _resolve_user(
    authorization: Optional[str],
    dev_email: Optional[str],
    device_auth: Optional[str] = None,
) -> dict:
    """Return {user_id, email, email_verified}. Raises HTTPException(401) on failure.

    Three paths, in priority order:
      1. Firebase ID token in `Authorization: Bearer <token>` (production user UI).
      2. Device-self auth via `X-MC-Device-Auth: <device_id>:<enrollment_token>`
         (the local MC instance authenticates as itself; resolves to its owner).
      3. Dev shim via `X-Dev-User-Email` (only when MC_CP_DEV_AUTH=1).
    """
    if authorization and authorization.startswith("Bearer "):
        try:
            return _verify_firebase_token(authorization[7:])
        except Exception as e:
            raise HTTPException(status_code=401, detail={
                "code": "unauthorized", "message": f"Invalid Firebase token: {e}",
                "request_id": "x",
            })

    if device_auth and ":" in device_auth:
        device_id, enrollment_token = device_auth.split(":", 1)
        device_id = device_id.strip()
        enrollment_token = enrollment_token.strip()
        if not device_id or not enrollment_token:
            raise HTTPException(status_code=401, detail={
                "code": "unauthorized",
                "message": "X-MC-Device-Auth must be '<device_id>:<enrollment_token>'.",
                "request_id": "x",
            })
        db = fs.db()
        device_snap = db.collection(fs.COL_DEVICES).document(device_id).get()
        if not device_snap.exists:
            raise HTTPException(status_code=401, detail={
                "code": "unknown_device",
                "message": "Device not enrolled.",
                "request_id": "x",
            })
        row = device_snap.to_dict() or {}
        if row.get("revoked_at"):
            raise HTTPException(status_code=401, detail={
                "code": "device_revoked",
                "message": "Device has been revoked.",
                "request_id": "x",
            })
        provided_hash = hashlib.sha256(enrollment_token.encode("utf-8")).hexdigest()
        if provided_hash != row.get("enrollment_token_hash", ""):
            raise HTTPException(status_code=401, detail={
                "code": "bad_enrollment_token",
                "message": "Invalid enrollment_token for this device.",
                "request_id": "x",
            })
        user_id = row.get("user_id", "")
        # Pull the user row for email — needed by sessions endpoint to query CF.
        user_snap = db.collection(fs.COL_USERS).document(user_id).get()
        user_data = (user_snap.to_dict() or {}) if user_snap.exists else {}
        return {
            "user_id": user_id,
            "email": user_data.get("email", ""),
            "email_verified": True,  # device exists → user was email-verified at enrollment
        }

    if _DEV_AUTH_ENABLED and dev_email:
        return {
            "user_id": "dev_" + hashlib.sha256(dev_email.encode("utf-8")).hexdigest()[:16],
            "email": dev_email,
            "email_verified": True,
        }

    raise HTTPException(status_code=401, detail={
        "code": "unauthorized",
        "message": "Authorization header missing or unrecognized.",
        "request_id": "x",
    })


_FIREBASE_INITIALIZED = False


def _ensure_firebase_initialized() -> None:
    """Lazy-init the Firebase Admin SDK on first use.

    Reads FB_PROJECT_ID from env so token verification matches the project
    that issued the token (the Firebase project may be named differently
    from the GCP project — e.g. our GCP `clayrune` hosts a Firebase project
    `clayrune-49e57` because the bare name was taken). Without an explicit
    projectId, firebase_admin falls back to GOOGLE_CLOUD_PROJECT which
    would reject Firebase-issued tokens whose `aud` is the Firebase project.
    """
    global _FIREBASE_INITIALIZED
    if _FIREBASE_INITIALIZED:
        return
    try:
        import firebase_admin
        if not firebase_admin._apps:
            project_id = os.environ.get("FB_PROJECT_ID", "").strip()
            if project_id:
                firebase_admin.initialize_app(options={"projectId": project_id})
            else:
                firebase_admin.initialize_app()  # falls back to ADC / GOOGLE_CLOUD_PROJECT
        _FIREBASE_INITIALIZED = True
    except Exception as e:
        log.warning("Firebase Admin SDK init failed: %s", e)
        raise


def _verify_firebase_token(id_token: str) -> dict:
    """Verify a Firebase ID token and return {user_id, email, email_verified}.

    Raises (caught by `_resolve_user`) on any verification failure.
    """
    _ensure_firebase_initialized()
    from firebase_admin import auth as _fb_auth
    decoded = _fb_auth.verify_id_token(id_token)
    return {
        "user_id": decoded.get("uid") or decoded.get("user_id") or decoded.get("sub"),
        "email": decoded.get("email") or "",
        "email_verified": bool(decoded.get("email_verified")),
    }


# ─── /v1/enroll ──────────────────────────────────────────────────────────────


@router.post("/enroll", tags=["account"])
async def enroll(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_dev_user_email: Optional[str] = Header(None, alias="X-Dev-User-Email"),
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
):
    """Bind device pubkey to user account; provision Cloudflare resources.

    Body shape (subset of `03-` §3.5):
      {
        "device_pub_b64": "<base64 32 bytes>",
        "csrf_nonce":     "<from /v1/connect>",
        "username":       "ron",
        "device_name":    "Ron's Desktop",
        "os":             "win32-11-26200",
        "mc_version":     "1.4.2"
      }
    """
    rid = _request_id(request)
    body = await request.json()
    if not isinstance(body, dict):
        return _err_response(400, "malformed_json", "Body must be a JSON object.", rid)

    # 0. Auth
    try:
        user = _resolve_user(authorization, x_dev_user_email)
    except HTTPException as e:
        # Re-emit with our request_id
        d = dict(e.detail) if isinstance(e.detail, dict) else {"code": "unauthorized",
                                                                "message": str(e.detail)}
        d["request_id"] = rid
        raise HTTPException(status_code=e.status_code, detail=d)
    if not user.get("email_verified", False):
        return _err_response(403, "email_unverified",
                             "Verify your email before enrolling.", rid)

    # 1. Idempotency: if same key + same user has been seen, return cached.
    if idempotency_key:
        cached = _idem_get(user_id=user["user_id"], key=idempotency_key)
        if cached is not None:
            return cached  # already a JSONable dict

    # 2. Validate fields
    device_pub_b64 = body.get("device_pub_b64", "")
    csrf_nonce = body.get("csrf_nonce", "")
    username = (body.get("username", "") or "").strip().lower()
    if not device_pub_b64 or not csrf_nonce or not username:
        return _err_response(400, "bad_envelope",
                             "device_pub_b64, csrf_nonce, and username are required.", rid)

    ok, reason = _is_username_valid(username)
    if not ok:
        return _err_response(409 if username in _USERNAME_RESERVED else 400,
                             "username_reserved" if username in _USERNAME_RESERVED
                             else "username_invalid", reason, rid)

    # 3. Validate CSRF nonce (consumes the enrollment_intent row)
    intent = _consume_enrollment_intent(csrf_nonce, device_pub_b64)
    if intent is None:
        return _err_response(400, "enrollment_intent_invalid",
                             "Sign-in expired or this device wasn't part of it. "
                             "Click 'Connect' again from Mission Control.", rid)

    # 4-7. Common post-auth/post-CSRF provisioning path. Shared with
    # /v1/signin/complete which has its own auth + CSRF checks.
    response = await _do_enroll_after_auth(
        user=user,
        device_pub_b64=device_pub_b64,
        csrf_nonce=csrf_nonce,
        username=username,
        device_name=(body.get("device_name") or ""),
        os_str=(body.get("os") or ""),
        mc_version=(body.get("mc_version") or ""),
    )

    # 8. Cache for idempotency
    if idempotency_key:
        _idem_set(user_id=user["user_id"], key=idempotency_key, value=response)

    return response


async def _do_enroll_after_auth(
    *,
    user: dict,
    device_pub_b64: str,
    csrf_nonce: str,
    username: str,
    device_name: str = "",
    os_str: str = "",
    mc_version: str = "",
) -> dict:
    """Username claim + CF provisioning + Firestore persist. Caller must have
    already authenticated the user and validated the CSRF nonce.

    Raises HTTPException on any failure (claims released, CF resources rolled back).
    Returns the JSON-able response dict the protocol expects.
    """
    if not _claim_username(username, user["user_id"]):
        raise HTTPException(status_code=409, detail={
            "code": "username_taken",
            "message": "That username is taken. Try another.",
            "request_id": "x",
        })

    zone_root = os.environ.get("CLAYRUNE_PRIMARY_ZONE", "clayrune.io")
    hostname = f"{username}.{zone_root}"

    cf_resources: dict[str, Any] = {}
    try:
        cf = _get_cf_client()

        tunnel = await cf.create_named_tunnel(name=f"mc-{username}-{secrets.token_urlsafe(4)}")
        cf_resources["tunnel_id"] = tunnel["id"]
        cf_resources["tunnel_token"] = tunnel["token"]

        await cf.set_tunnel_ingress(
            tunnel_id=tunnel["id"], hostname=hostname,
            service_url="http://localhost:5199",
        )

        try:
            dns_record = await cf.create_dns_cname(name=username, target_uuid=tunnel["id"])
        except cloudflare.CloudflareAPIError as e:
            if not _is_cf_error_code(e, 81053):
                raise
            log.info("DNS create collided with stale record; running force_cleanup + retry")
            await _force_cleanup_for_hostname(
                hostname=hostname, username=username,
                exclude_tunnel_id=cf_resources["tunnel_id"],
            )
            dns_record = await cf.create_dns_cname(name=username, target_uuid=tunnel["id"])
        cf_resources["dns_record_id"] = dns_record["id"]

        try:
            access_app = await cf.create_access_app(
                hostname=hostname, allowed_email=user["email"],
            )
        except cloudflare.CloudflareAPIError as e:
            if not _is_cf_error_code(e, 11010):
                raise
            log.info("Access app collided with stale app; running force_cleanup + retry")
            await _force_cleanup_for_hostname(
                hostname=hostname, username=username,
                exclude_tunnel_id=cf_resources["tunnel_id"],
                exclude_dns_record_id=cf_resources["dns_record_id"],
            )
            access_app = await cf.create_access_app(
                hostname=hostname, allowed_email=user["email"],
            )
        cf_resources["access_app_id"] = access_app["id"]

    except Exception as e:
        log.exception("CF provisioning failed mid-flight; rolling back: %s", e)
        await _rollback_cf_resources(cf_resources)
        _release_username(username, user["user_id"])
        if isinstance(e, cloudflare.CloudflareAPIError):
            raise HTTPException(status_code=503, detail={
                "code": "provisioning_failed",
                "message": f"Cloudflare provisioning failed: {e}",
                "request_id": "x",
                "retry_after_ms": 5000,
            })
        raise HTTPException(status_code=503, detail={
            "code": "internal_error",
            "message": f"Provisioning failed: {e}",
            "request_id": "x",
            "retry_after_ms": 5000,
        })

    enrollment_token = secrets.token_urlsafe(32)
    enrollment_token_hash = hashlib.sha256(enrollment_token.encode("utf-8")).hexdigest()
    device_id = "dev_" + secrets.token_urlsafe(12).replace("_", "").replace("-", "")[:16]

    now = _dt.datetime.now(_dt.timezone.utc)
    db = fs.db()

    db.collection(fs.COL_USERS).document(user["user_id"]).set({
        "user_id": user["user_id"],
        "email": user["email"],
        "email_hash": hashlib.sha256(user["email"].encode("utf-8")).hexdigest(),
        "username": username,
        "created_at": now,
        "tier": "free",
        "device_cap": 2,
        "bandwidth_quota_period_bytes": 5 * 1024 ** 3,
        "bandwidth_used_period_bytes": 0,
    }, merge=True)

    device_pub_hash = hashlib.sha256(device_pub_b64.encode("utf-8")).hexdigest()
    db.collection(fs.COL_DEVICES).document(device_id).set({
        "device_id": device_id,
        "user_id": user["user_id"],
        "device_pub_b64": device_pub_b64,
        "device_pub_hash": device_pub_hash,
        "enrollment_token_hash": enrollment_token_hash,
        "enrollment_token_renewed_at": now,
        "device_name": (device_name or "").strip()[:64] or "Unnamed device",
        "os": (os_str or "").strip()[:64],
        "mc_version": (mc_version or "").strip()[:32],
        "hostname_claim": hostname,
        "cf_tunnel_uuid": cf_resources["tunnel_id"],
        "cf_tunnel_token": cf_resources["tunnel_token"],
        "cf_dns_record_id": cf_resources["dns_record_id"],
        "cf_access_app_id": cf_resources["access_app_id"],
        "enrolled_at": now,
        "revoked_at": None,
        "last_seen": None,
        "provisioning_state": "active",
        "min_protocol": 1,
    })

    return {
        "device_id": device_id,
        "enrollment_token": enrollment_token,
        "username": username,
        "hostname": hostname,
        "control_plane_pubkey_id": "cp-2026a",
        "min_protocol": 1,
    }


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _request_id(request: Request) -> str:
    return request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex[:12]}"


def _err_response(status: int, code: str, message: str, request_id: str,
                  *, retry_after_ms: Optional[int] = None) -> Any:
    detail = {"code": code, "message": message, "request_id": request_id}
    if retry_after_ms is not None:
        detail["retry_after_ms"] = retry_after_ms
    raise HTTPException(status_code=status, detail=detail)


def _is_cf_error_code(err: cloudflare.CloudflareAPIError, code: int) -> bool:
    """True if `err` carries the given CF API error code (e.g. 81053 / 11010)."""
    return any(e.get("code") == code for e in (err.errors or []))


# ─── Cloudflare client (singleton; overrideable for tests) ──────────────────


_cf_client: Optional[cloudflare.CloudflareClient] = None


def _get_cf_client() -> cloudflare.CloudflareClient:
    global _cf_client
    if _cf_client is None:
        _cf_client = cloudflare.CloudflareClient.from_env()
    return _cf_client


def set_cf_client_for_tests(client: cloudflare.CloudflareClient) -> None:
    """Inject a mocked CF client for tests."""
    global _cf_client
    _cf_client = client


def reset_cf_client() -> None:
    global _cf_client
    _cf_client = None


# ─── CSRF nonce / enrollment_intent consume ──────────────────────────────────


def _consume_enrollment_intent(csrf_nonce: str, device_pub_b64: str) -> Optional[dict]:
    """Look up + delete an enrollment_intent matching this nonce + device pubkey.

    For v1 (no /v1/connect endpoint shipped), we accept any non-empty nonce and
    create-on-the-fly. When /v1/connect lands, this becomes a strict lookup.
    Currently tracked in `enrollment_intents/`; if no row found, we treat as OK
    (dev convenience). Production will require strict matching.
    """
    if not csrf_nonce:
        return None
    db = fs.db()
    nonce_hash = hashlib.sha256(csrf_nonce.encode("utf-8")).hexdigest()
    pub_hash = hashlib.sha256(device_pub_b64.encode("utf-8")).hexdigest()

    # Look for a matching intent
    docs = db.collection(fs.COL_ENROLL_INTENTS) \
        .where("csrf_nonce_hash", "==", nonce_hash) \
        .where("device_pub_hash", "==", pub_hash) \
        .limit(1) \
        .stream()
    for d in docs:
        # Burn it
        d.reference.delete() if hasattr(d, "reference") else \
            db.collection(fs.COL_ENROLL_INTENTS).document(d.id).delete()
        return d.to_dict() or {}

    # Dev fallthrough: when MC_CP_DEV_AUTH=1, accept the nonce without prior
    # /v1/connect call. Production behavior will be strict (return None).
    if _DEV_AUTH_ENABLED:
        return {"_dev_synthetic": True}
    return None


# ─── Username allocation (transactional) ─────────────────────────────────────


def _claim_username(username: str, user_id: str) -> bool:
    """Atomically reserve `username` for `user_id`. Returns True on success.

    Stored as `usernames/{username}` with `user_id` field. Transaction prevents
    races between simultaneous enrollments.
    """
    from google.cloud import firestore as gfs  # type: ignore
    db = fs.db()
    ref = db.collection("usernames").document(username)

    @gfs.transactional
    def _txn(txn) -> bool:
        snap = ref.get(transaction=txn)
        if snap.exists:
            existing = (snap.to_dict() or {}).get("user_id")
            if existing == user_id:
                return True  # idempotent re-claim by same user
            return False
        txn.set(ref, {"username": username, "user_id": user_id,
                      "claimed_at": _dt.datetime.now(_dt.timezone.utc)})
        return True

    return _txn(db.transaction())


def _release_username(username: str, user_id: str) -> None:
    """Best-effort release used during rollback."""
    db = fs.db()
    ref = db.collection("usernames").document(username)
    snap = ref.get()
    if snap.exists and (snap.to_dict() or {}).get("user_id") == user_id:
        try:
            ref.delete()
        except Exception:
            pass


# ─── Idempotency cache ───────────────────────────────────────────────────────


def _idem_key(user_id: str, key: str) -> str:
    return f"{user_id}:{hashlib.sha256(key.encode('utf-8')).hexdigest()[:24]}"


def _idem_get(*, user_id: str, key: str) -> Optional[dict]:
    db = fs.db()
    ref = db.collection("idempotency_cache").document(_idem_key(user_id, key))
    snap = ref.get()
    if not snap.exists:
        return None
    row = snap.to_dict() or {}
    expires = row.get("expires_at")
    if expires is not None:
        try:
            now = _dt.datetime.now(_dt.timezone.utc)
            if expires < now:
                return None
        except Exception:
            pass
    return row.get("value")


def _idem_set(*, user_id: str, key: str, value: dict, ttl_hours: int = 24) -> None:
    db = fs.db()
    expires_at = _dt.datetime.now(_dt.timezone.utc) + _dt.timedelta(hours=ttl_hours)
    db.collection("idempotency_cache").document(_idem_key(user_id, key)).set({
        "value": value,
        "expires_at": expires_at,
    })


# ─── Cloudflare rollback ────────────────────────────────────────────────────


async def _rollback_cf_resources(resources: dict) -> None:
    """Best-effort delete of CF resources we created during a failed enroll.

    Order matters somewhat: app + DNS first (user-facing), tunnel last.
    Failures here are logged but not raised — we already failed the enroll;
    a stale CF resource is far less bad than crashing the response.
    """
    cf = _get_cf_client()
    if app_id := resources.get("access_app_id"):
        try:
            await cf.delete_access_app(app_id)
        except Exception as e:
            log.warning("rollback: delete access app %s failed: %s", app_id, e)
    if record_id := resources.get("dns_record_id"):
        try:
            await cf.delete_dns_record(record_id)
        except Exception as e:
            log.warning("rollback: delete dns record %s failed: %s", record_id, e)
    if tunnel_id := resources.get("tunnel_id"):
        try:
            await cf.delete_tunnel(tunnel_id)
        except Exception as e:
            log.warning("rollback: delete tunnel %s failed: %s", tunnel_id, e)


async def _force_cleanup_for_hostname(
    *,
    hostname: str,
    username: str,
    exclude_tunnel_id: Optional[str] = None,
    exclude_dns_record_id: Optional[str] = None,
    exclude_access_app_id: Optional[str] = None,
) -> dict:
    """Delete any pre-existing CF resources + Firestore device rows for `hostname`.

    Called from /v1/devices/{id}/revoke AND as collision-recovery from
    /v1/enroll's create-DNS / create-Access-app paths. Makes re-enrollment of
    the same username idempotent — no more "application already exists" /
    "record already exists" collisions from prior orphans.

    Lists each resource type via the CF API directly (independent of Firestore),
    so it cleans up resources whose Firestore row was lost or never written.

    `exclude_*` parameters skip the matching resource — used during enrollment
    collision-recovery so we don't delete the resources we just created.

    Returns a summary dict of what was deleted, for logging.
    """
    cf = _get_cf_client()
    summary = {"access_apps": 0, "dns_records": 0, "tunnels": 0, "devices": 0}

    # 1. Access apps gating this hostname
    try:
        # CF doesn't support filter-by-domain on /access/apps, so list-then-filter
        acc = await cf.get_account_id()
        apps = await cf._call("GET", f"/accounts/{acc}/access/apps")
        for app in (apps or []):
            if app.get("domain", "").lower() != hostname.lower():
                continue
            if exclude_access_app_id and app["id"] == exclude_access_app_id:
                continue
            try:
                await cf.delete_access_app(app["id"])
                summary["access_apps"] += 1
                log.info("force-cleanup: deleted access app %s for %s", app["id"], hostname)
            except Exception as e:
                log.warning("force-cleanup: failed deleting access app %s: %s", app["id"], e)
    except Exception as e:
        log.warning("force-cleanup: listing access apps failed: %s", e)

    # 2. DNS records for this hostname (CF supports name= filter)
    try:
        zone_id = await cf.get_zone_id()
        records = await cf._call("GET", f"/zones/{zone_id}/dns_records",
                                 params={"name": hostname})
        for r in (records or []):
            if exclude_dns_record_id and r["id"] == exclude_dns_record_id:
                continue
            try:
                await cf.delete_dns_record(r["id"])
                summary["dns_records"] += 1
                log.info("force-cleanup: deleted DNS record %s for %s", r["id"], hostname)
            except Exception as e:
                log.warning("force-cleanup: failed deleting DNS record %s: %s", r["id"], e)
    except Exception as e:
        log.warning("force-cleanup: listing DNS records failed: %s", e)

    # 3. Tunnels named mc-{username}-* (CF doesn't filter by name pattern, so list-then-match)
    try:
        acc = await cf.get_account_id()
        tunnels = await cf._call("GET", f"/accounts/{acc}/cfd_tunnel")
        prefix = f"mc-{username}-"
        for t in (tunnels or []):
            if t.get("deleted_at"):
                continue
            if not (t.get("name") or "").startswith(prefix):
                continue
            if exclude_tunnel_id and t["id"] == exclude_tunnel_id:
                continue
            try:
                await cf.delete_tunnel(t["id"])
                summary["tunnels"] += 1
                log.info("force-cleanup: deleted tunnel %s (%s)", t["id"], t.get("name"))
            except Exception as e:
                log.warning("force-cleanup: failed deleting tunnel %s: %s", t["id"], e)
    except Exception as e:
        log.warning("force-cleanup: listing tunnels failed: %s", e)

    # 4. Firestore device rows for this hostname (regardless of revoked_at)
    try:
        db = fs.db()
        docs = list(db.collection(fs.COL_DEVICES)
                      .where("hostname_claim", "==", hostname).stream())
        for d in docs:
            try:
                db.collection(fs.COL_DEVICES).document(d.id).delete()
                summary["devices"] += 1
                log.info("force-cleanup: deleted devices/%s", d.id)
            except Exception as e:
                log.warning("force-cleanup: failed deleting devices/%s: %s", d.id, e)
    except Exception as e:
        log.warning("force-cleanup: listing device rows failed: %s", e)

    if any(summary.values()):
        log.info("force-cleanup for %s: %s", hostname, summary)
    return summary
