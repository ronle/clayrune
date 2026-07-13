"""Account / browser-session endpoints (Firebase ID token auth in v1, dev shim available).

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

Implemented:
  POST /v1/enroll                   — provisions a CF tunnel + DNS, persists user +
                                      device, returns enrollment_token
  GET/POST /v1/sessions*            — our sessions (app/sessions.py), NOT CF Access's
  */devices/{id}/mobile-tokens      — phone pairing, now a long-lived mobile session

**No Cloudflare Access anywhere in this file's provisioning paths.** Access was
per-seat priced ($7/user/mo above 50 users, 500-app account cap) against a $6.99
product. It is replaced by the edge Worker + our session JWT. The Access-deleting
calls that remain exist only to tear down what we already created for existing
users — see `control_plane/teardown_access.py`.

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
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Request

from . import cloudflare, firestore as fs, sessions as _sessions

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


# ─── /v1/sessions (OUR sessions — see app/sessions.py) ───────────────────────
#
# These used to be a proxy over Cloudflare Access's session API: they listed CF's
# sessions and asked CF to revoke them. With Access gone there is nothing on the
# other end of those calls, so the session concept is now ours.
#
# The replacement is strictly better. CF's per-session revoke was unreliable
# enough that the old code tried four different URL shapes and, when they all
# failed, fell back to revoking EVERY session the user had — and told the UI it
# had done so via `fallback: true`. Here a session is a Firestore document;
# revoking one revokes one.


@router.get("/sessions", tags=["account"])
async def list_sessions(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_dev_user_email: Optional[str] = Header(None, alias="X-Dev-User-Email"),
    x_mc_device_auth: Optional[str] = Header(None, alias="X-MC-Device-Auth"),
):
    """List the user's active sign-in sessions (browsers + paired phones).

    DIFFERENT from `/v1/devices`, which lists enrolled MC installations. A
    session is "someone is signed in and can open the dashboard"; a device is
    "a machine is running MC behind a tunnel".
    """
    rid = _request_id(request)
    try:
        user = _resolve_user(authorization, x_dev_user_email, device_auth=x_mc_device_auth)
    except HTTPException as e:
        d = dict(e.detail) if isinstance(e.detail, dict) else {"code": "unauthorized",
                                                                "message": str(e.detail)}
        d["request_id"] = rid
        raise HTTPException(status_code=e.status_code, detail=d)

    return {
        "sessions": _sessions.list_for_user(user["user_id"]),
        "email": user.get("email", ""),
    }


@router.post("/sessions/{session_id}/revoke", tags=["account"])
async def revoke_session(
    session_id: str,
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_dev_user_email: Optional[str] = Header(None, alias="X-Dev-User-Email"),
    x_mc_device_auth: Optional[str] = Header(None, alias="X-MC-Device-Auth"),
):
    """Revoke one session. Ownership is enforced in the store, not here.

    The session's current access JWT stays cryptographically valid until it
    expires (≤ 30 min) — that is inherent to verifying at the edge with no origin
    call. What is guaranteed is that it is never renewed. To cut someone off
    *now*, suspend them: that writes the KV denylist the Worker reads on every
    request.
    """
    rid = _request_id(request)
    try:
        user = _resolve_user(authorization, x_dev_user_email, device_auth=x_mc_device_auth)
    except HTTPException as e:
        d = dict(e.detail) if isinstance(e.detail, dict) else {"code": "unauthorized",
                                                                "message": str(e.detail)}
        d["request_id"] = rid
        raise HTTPException(status_code=e.status_code, detail=d)

    if not _sessions.revoke(session_id, user_id=user["user_id"]):
        return _err_response(404, "unknown_session",
                             "No such session for this account.", rid)
    return {"ok": True, "scope": "session",
            "max_lag_seconds": _sessions.access_ttl_seconds()}


@router.post("/sessions/revoke-all", tags=["account"])
async def revoke_all_sessions(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_dev_user_email: Optional[str] = Header(None, alias="X-Dev-User-Email"),
    x_mc_device_auth: Optional[str] = Header(None, alias="X-MC-Device-Auth"),
):
    """Sign out everywhere: revoke every session, browsers and paired phones.

    Does not touch the tunnel — an enrolled device keeps attesting. This kicks
    people out of the dashboard, it does not unenroll a machine.
    """
    rid = _request_id(request)
    try:
        user = _resolve_user(authorization, x_dev_user_email, device_auth=x_mc_device_auth)
    except HTTPException as e:
        d = dict(e.detail) if isinstance(e.detail, dict) else {"code": "unauthorized",
                                                                "message": str(e.detail)}
        d["request_id"] = rid
        raise HTTPException(status_code=e.status_code, detail=d)

    n = _sessions.revoke_all(user["user_id"])
    return {"ok": True, "revoked": n, "email": user.get("email", ""),
            "max_lag_seconds": _sessions.access_ttl_seconds()}


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


# ─── Mobile pairing tokens ───────────────────────────────────────────────────
#
# ⚠️ REBASED OFF CLOUDFLARE ACCESS (2026-07-13). Pairing used to mint a CF Access
# **service token** and attach a Service Auth **policy to the user's Access app**.
# Removing Access therefore removed the thing pairing was built on — this was not
# a cost cleanup, it deleted the phone's entire credential.
#
# A paired phone is now just a SESSION of kind "mobile" (`app/sessions.py`): a
# long-lived (1y) opaque refresh token which the phone exchanges at
# /v1/session/refresh for the same 30-minute `cr_session` cookie the browser
# uses. Same credential, same edge check, same entitlement chokepoint — a phone
# whose owner cancels stops working within one TTL, which the CF service token
# (duration 8760h, no entitlement check anywhere) never did.
#
# Flow:
#   1. Dashboard on the host MC instance calls
#      POST /v1/devices/{this_device_id}/mobile-tokens with {label}
#   2. CP opens a mobile session and returns `pair_token` ONCE (it is stored
#      hashed; we cannot show it again)
#   3. Host MC turns it into a clayrune://pair?... URI for the QR code
#   4. APK POSTs {refresh_token: <pair_token>} to /v1/session/refresh, stores the
#      returned `cr_session` cookie, and renews it before expiry
#
# ⚠️ THE SHIPPED APK STILL SENDS CF-Access-Client-Id / CF-Access-Client-Secret
# HEADERS. Those headers now authorize nothing. The Android shell
# (E:\clayrune-mobile, MainActivity) must be updated to the refresh-token flow
# before phones work again. Backlog item filed; see §3.15.6 of
# `docs/remote-access/03-control-plane-api.md`.
#
# Auth: device-self or the owning Firebase user. The caller MUST be authorized
# as the device's owner; cross-user pairing is rejected.


_MOBILE_TOKEN_NAME_RE = re.compile(r"^[A-Za-z0-9 _.\-]{1,48}$")


def _mobile_tokens_col(device_id: str):
    """Subcollection holding per-device mobile-token rows.

    Kept as a per-device subcollection (rather than folding straight into
    `sessions/`) because pairing is scoped to a *host machine*: the QR code is
    shown by one MC instance, and "which phones are paired to this box" is the
    question the dashboard asks. The row is a pointer to the session that holds
    the actual credential.
    """
    return fs.db().collection(fs.COL_DEVICES).document(device_id) \
        .collection("mobile_tokens")


def _authorized_for_device(user: dict, device_row: dict, *, rid: str) -> None:
    """403 unless `user` owns `device_row`. Raises HTTPException on mismatch."""
    if device_row.get("user_id") != user.get("user_id"):
        raise HTTPException(status_code=403, detail={
            "code": "forbidden",
            "message": "Device belongs to a different user.",
            "request_id": rid,
        })


@router.post("/devices/{device_id}/mobile-tokens", tags=["account"])
async def create_mobile_token(
    device_id: str,
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_dev_user_email: Optional[str] = Header(None, alias="X-Dev-User-Email"),
    x_mc_device_auth: Optional[str] = Header(None, alias="X-MC-Device-Auth"),
):
    """Pair a phone: open a long-lived mobile session and hand back its token.

    Body: {"label": "Ron's Pixel"} — free-form, shown in the dashboard list.

    Response (ONE TIME — the token is stored hashed and cannot be shown again):
      {
        "ok": true,
        "token_id": "<firestore doc id>",
        "session_id": "sess_...",
        "pair_token": "<opaque refresh token>",
        "refresh_url": "https://api.<zone>/v1/session/refresh",
        "hostname": "<username>.clayrune.io",
        "label": "Ron's Pixel",
        "created_at": "<iso8601>"
      }

    The phone POSTs `{"refresh_token": pair_token}` to `refresh_url`, gets a
    30-minute `cr_session` cookie, and renews it before expiry. That renewal is
    the entitlement chokepoint — unlike the CF service token this replaces, which
    was valid for a year and checked nobody's subscription, ever.
    """
    rid = _request_id(request)
    user = _resolve_user(authorization, x_dev_user_email, x_mc_device_auth)

    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    label = (body.get("label") or "").strip()[:48]
    if not label:
        label = "Mobile device"
    if not _MOBILE_TOKEN_NAME_RE.fullmatch(label):
        raise HTTPException(status_code=400, detail={
            "code": "bad_label",
            "message": "label must be 1–48 chars of letters, digits, space, dot, underscore, dash.",
            "request_id": rid,
        })

    device_row = fs.device_by_id(device_id)
    if device_row is None:
        raise HTTPException(status_code=404, detail={
            "code": "unknown_device", "message": "Device not enrolled.", "request_id": rid,
        })
    _authorized_for_device(user, device_row, rid=rid)

    hostname = device_row.get("hostname_claim", "")
    if not hostname:
        raise HTTPException(status_code=409, detail={
            "code": "device_unprovisioned",
            "message": "Device has no hostname — re-enroll the host before pairing phones.",
            "request_id": rid,
        })
    username = hostname.split(".")[0]

    session_id, pair_token, expires_at = _sessions.create(
        user_id=user["user_id"], username=username,
        kind=_sessions.KIND_MOBILE, label=label,
    )

    now = _dt.datetime.now(_dt.timezone.utc).isoformat()
    doc_ref = _mobile_tokens_col(device_id).document()
    doc_ref.set({
        "label": label,
        "session_id": session_id,
        # The token itself is NOT persisted here — `sessions/{id}.refresh_hash`
        # is the only copy, and it is a hash. If the user loses it they re-pair.
        "created_at": now,
        "expires_at": expires_at.isoformat(),
        "last_used_at": None,
        "revoked_at": None,
    })

    return {
        "ok": True,
        "token_id": doc_ref.id,
        "session_id": session_id,
        "pair_token": pair_token,
        "refresh_url": f"https://api.{os.environ.get('CLAYRUNE_PRIMARY_ZONE', 'clayrune.io')}"
                       f"/v1/session/refresh",
        "hostname": hostname,
        "label": label,
        "created_at": now,
    }


@router.get("/devices/{device_id}/mobile-tokens", tags=["account"])
async def list_mobile_tokens(
    device_id: str,
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_dev_user_email: Optional[str] = Header(None, alias="X-Dev-User-Email"),
    x_mc_device_auth: Optional[str] = Header(None, alias="X-MC-Device-Auth"),
):
    """List paired mobile devices for this host. Does NOT return secrets."""
    rid = _request_id(request)
    user = _resolve_user(authorization, x_dev_user_email, x_mc_device_auth)
    device_row = fs.device_by_id(device_id)
    if device_row is None:
        raise HTTPException(status_code=404, detail={
            "code": "unknown_device", "message": "Device not enrolled.", "request_id": rid,
        })
    _authorized_for_device(user, device_row, rid=rid)

    out = []
    for snap in _mobile_tokens_col(device_id).stream():
        row = snap.to_dict() or {}
        if row.get("revoked_at"):
            continue
        out.append({
            "token_id": snap.id,
            "label": row.get("label", ""),
            "session_id": row.get("session_id", ""),
            "created_at": row.get("created_at"),
            "expires_at": row.get("expires_at"),
            "last_used_at": row.get("last_used_at"),
            # Legacy CF Access pairs (pre-2026-07-13) still carry a client_id and
            # no session_id. They no longer authorize anything — surface the flag
            # so the dashboard can tell the user to re-pair rather than silently
            # listing a dead phone as live.
            "legacy_cf_access": bool(row.get("client_id")) and not row.get("session_id"),
        })
    out.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return {"tokens": out}


@router.delete("/devices/{device_id}/mobile-tokens/{token_id}", tags=["account"])
async def delete_mobile_token(
    device_id: str,
    token_id: str,
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_dev_user_email: Optional[str] = Header(None, alias="X-Dev-User-Email"),
    x_mc_device_auth: Optional[str] = Header(None, alias="X-MC-Device-Auth"),
):
    """Unpair a phone: revoke its session (and tear down any legacy CF Access
    service token / policy, for pairs created before 2026-07-13).

    Idempotent. Best-effort on CF — the Firestore row is deleted even if the CF
    API fails, so the dashboard list stays accurate.

    The phone keeps working until its current 30-minute `cr_session` cookie
    expires; the next refresh fails. If you lost the phone and 30 minutes is too
    long, suspend the account — the KV denylist cuts the edge off immediately.
    """
    rid = _request_id(request)
    user = _resolve_user(authorization, x_dev_user_email, x_mc_device_auth)
    device_row = fs.device_by_id(device_id)
    if device_row is None:
        return {"ok": True, "already_revoked": True, "reason": "device_not_found"}
    _authorized_for_device(user, device_row, rid=rid)

    doc_ref = _mobile_tokens_col(device_id).document(token_id)
    snap = doc_ref.get()
    if not snap.exists:
        return {"ok": True, "already_revoked": True, "reason": "token_not_found"}
    row = snap.to_dict() or {}

    deleted = {"session": False, "legacy_cf_policy": False, "legacy_cf_token": False}

    if session_id := row.get("session_id"):
        deleted["session"] = _sessions.revoke(session_id, user_id=user["user_id"])

    # Legacy teardown: pairs created while CF Access was still in the path.
    app_id = device_row.get("cf_access_app_id", "")
    cf_token_id = row.get("cf_token_id", "")
    cf_policy_id = row.get("cf_policy_id", "")
    if cf_token_id or cf_policy_id:
        cf = _get_cf_client()
        if app_id and cf_policy_id:
            try:
                await cf.delete_access_policy(app_id=app_id, policy_id=cf_policy_id)
                deleted["legacy_cf_policy"] = True
            except Exception as e:
                log.warning("[mobile-pair] delete legacy policy %s failed: %s", cf_policy_id, e)
        if cf_token_id:
            try:
                await cf.delete_service_token(cf_token_id)
                deleted["legacy_cf_token"] = True
            except Exception as e:
                log.warning("[mobile-pair] delete legacy token %s failed: %s", cf_token_id, e)

    try:
        doc_ref.delete()
    except Exception as e:
        log.warning("[mobile-pair] firestore delete failed: %s", e)

    return {"ok": True, "deleted": deleted,
            "max_lag_seconds": _sessions.access_ttl_seconds()}


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

# Fail-closed: the dev auth shim (X-Dev-User-Email impersonation, no signin
# verification) must NEVER be on in production. Cloud Run always sets K_SERVICE,
# so refuse to import there with dev auth enabled rather than silently let anyone
# authenticate as any user.
if _DEV_AUTH_ENABLED and os.environ.get("K_SERVICE"):
    raise RuntimeError(
        "MC_CP_DEV_AUTH=1 (full X-Dev-User-Email impersonation bypass) is set in a "
        "Cloud Run environment (K_SERVICE present). Refusing to start — unset "
        "MC_CP_DEV_AUTH in production."
    )


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
                             "Click 'Connect' again from Clayrune.", rid)

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

        # NO CLOUDFLARE ACCESS APP. This used to create a self-hosted Access app
        # gating `hostname` with an email policy — $7/user/mo above 50 seats, and
        # a hard 500-application ceiling on the account, against a $6.99 price.
        #
        # Access was doing two jobs and the money was the boring one. The other
        # was AUTHORIZATION: its email policy is what stopped alice reaching
        # bob.clayrune.io. That job now belongs to the edge Worker's
        # `claims.u !== subdomain → 403` check, fed by the `u` claim we mint in
        # `app/sessions.py`. Tunnel + DNS below/above are untouched — those are
        # free and unmetered; only Access was the trap.

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
        # `cf_access_app_id` is deliberately NOT written any more. Rows enrolled
        # before 2026-07-13 still carry one, and the teardown/revoke paths still
        # honour it so those apps get cleaned up.
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
