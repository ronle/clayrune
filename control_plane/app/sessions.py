"""Session store: revocable refresh token + short-lived ES256 access JWT.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

## Why this exists at all

The old `/v1/sessions*` endpoints were a thin proxy over **Cloudflare Access's**
session API. They listed CF's sessions and asked CF to revoke them. With Access
removed there is nothing on the other end of those calls, so the session concept
has to become ours. This is that store.

It is a straight upgrade on what it replaces: CF's per-session revoke was so
unreliable that the old code tried four different URL shapes and, when they all
failed, fell back to revoking *every* session the user had. Here a session is a
Firestore document and revoking one is a delete.

## The two-token shape

    refresh token   opaque, long-lived (30d browser / 365d phone), stored HASHED,
                    revocable, and **only ever sent to the control plane**.
    access JWT      ES256, 30 min, `cr_session` cookie, verified at the edge in
                    CPU by the Worker. Never touches the control plane again.

Renewal is the entitlement chokepoint: `/v1/session/refresh` re-reads the LIVE
user row, so a cancellation or suspension takes effect within one access-JWT TTL.
The TTL is not laziness, it is the deliberate revocation lag (BILLING_DESIGN §3.1).
Fraud does not wait for it — that is what the KV denylist is for (`denylist.py`).

## The `u` claim is the authorization claim

`claims.u` is what the Worker compares against the requested subdomain
(`claims.u !== want → 403`). It is the *only* thing stopping alice from reaching
`bob.clayrune.io`, which is the job Cloudflare Access's per-user email policy was
doing. It is read from the enrolled `users/{uid}.username` in Firestore and is
**never** taken from anything the client sent. If you ever find yourself writing
`u=` from a request body, stop.

## Refresh tokens are stored hashed

A Firestore read leak must not hand over live sessions. The token is
`<session_id>.<secret>`: the id makes lookup an O(1) document get, and the secret
is compared against a stored SHA-256 with `compare_digest`.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import logging
import os
import secrets
from typing import Optional

from . import entitlement, firestore as fs, jwt_es256

log = logging.getLogger(__name__)


# ─── Config (mirrors edge-worker/wrangler.toml [vars]) ───────────────────────

def _zone() -> str:
    return os.environ.get("CLAYRUNE_PRIMARY_ZONE", "clayrune.io")


def issuer() -> str:
    return os.environ.get("CLAYRUNE_JWT_ISS", f"https://api.{_zone()}")


def audience() -> str:
    return os.environ.get("CLAYRUNE_JWT_AUD", "clayrune-dashboard")


def access_ttl_seconds() -> int:
    """Access-JWT lifetime. The Worker's contract says 15–60 min; clamp to it.

    Below 15 min the browser hammers the refresh endpoint; above 60 min the
    revocation lag stops being defensible to a customer who just cancelled.
    """
    raw = int(os.environ.get("CLAYRUNE_SESSION_TTL_S", "1800"))
    return max(15 * 60, min(60 * 60, raw))


COOKIE_NAME = "cr_session"          # the access JWT — read by the Worker
REFRESH_COOKIE_NAME = "cr_refresh"  # the opaque refresh token — control plane only

BROWSER_REFRESH_DAYS = 30
MOBILE_REFRESH_DAYS = 365

KIND_BROWSER = "browser"
KIND_MOBILE = "mobile"


def cookie_domain() -> str:
    """`.clayrune.io` — the cookie must be readable on every user subdomain."""
    return os.environ.get("CLAYRUNE_COOKIE_DOMAIN", f".{_zone()}")


# ─── Refresh tokens ──────────────────────────────────────────────────────────


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _split(token: str) -> tuple[str, str]:
    session_id, _, secret = (token or "").partition(".")
    return session_id.strip(), secret.strip()


def _iso(v) -> Optional[str]:
    if v is None:
        return None
    try:
        return v.isoformat(timespec="seconds").replace("+00:00", "Z")
    except Exception:
        return str(v)


# ─── Create / resolve / revoke ───────────────────────────────────────────────


def create(*, user_id: str, username: str, kind: str = KIND_BROWSER,
           label: str = "", ip_hash: Optional[str] = None) -> tuple[str, str, _dt.datetime]:
    """Open a session. Returns `(session_id, refresh_token, expires_at)`.

    The refresh token is returned exactly once and never stored in the clear.
    """
    session_id = "sess_" + secrets.token_urlsafe(12).replace("-", "").replace("_", "")[:16]
    secret = secrets.token_urlsafe(32)
    now = _dt.datetime.now(_dt.timezone.utc)
    days = MOBILE_REFRESH_DAYS if kind == KIND_MOBILE else BROWSER_REFRESH_DAYS
    expires_at = now + _dt.timedelta(days=days)

    fs.db().collection(fs.COL_SESSIONS).document(session_id).set({
        "session_id": session_id,
        "user_id": user_id,
        "username": username,
        "kind": kind,
        "label": (label or "").strip()[:48],
        "refresh_hash": _hash(secret),
        "created_at": now,
        "expires_at": expires_at,
        "last_refresh_at": now,
        "last_ip_hash": ip_hash,
        "revoked_at": None,
    })
    return session_id, f"{session_id}.{secret}", expires_at


def resolve_refresh(token: str) -> Optional[dict]:
    """Return the session row for a refresh token, or None if it is not usable.

    None covers: malformed, unknown, revoked, expired, and secret-mismatch. The
    caller gets one undifferentiated failure on purpose — a token probe should
    not learn *why* it failed.
    """
    session_id, secret = _split(token)
    if not session_id or not secret:
        return None

    snap = fs.db().collection(fs.COL_SESSIONS).document(session_id).get()
    if not snap.exists:
        return None
    row = snap.to_dict() or {}

    if not secrets.compare_digest(row.get("refresh_hash", ""), _hash(secret)):
        return None
    if row.get("revoked_at"):
        return None

    expires_at = row.get("expires_at")
    if expires_at is not None:
        try:
            if expires_at < _dt.datetime.now(_dt.timezone.utc):
                return None
        except TypeError:
            pass  # unparseable expiry — treat as non-expiring rather than lock the user out

    row["_id"] = session_id
    return row


def touch(session_id: str, *, ip_hash: Optional[str] = None) -> None:
    """Record a successful refresh. Best-effort — never fail a refresh over it."""
    try:
        fs.db().collection(fs.COL_SESSIONS).document(session_id).set({
            "last_refresh_at": _dt.datetime.now(_dt.timezone.utc),
            "last_ip_hash": ip_hash,
        }, merge=True)
    except Exception as e:
        log.warning("[sessions] touch %s failed: %s", session_id, e)


def list_for_user(user_id: str, *, kind: Optional[str] = None) -> list[dict]:
    """Active (non-revoked, non-expired) sessions, newest first. No secrets."""
    now = _dt.datetime.now(_dt.timezone.utc)
    out: list[dict] = []
    for snap in fs.db().collection(fs.COL_SESSIONS).where("user_id", "==", user_id).stream():
        row = snap.to_dict() or {}
        if row.get("revoked_at"):
            continue
        if kind is not None and row.get("kind") != kind:
            continue
        expires_at = row.get("expires_at")
        if expires_at is not None:
            try:
                if expires_at < now:
                    continue
            except TypeError:
                pass
        out.append({
            "session_id": snap.id,
            "kind": row.get("kind", KIND_BROWSER),
            "label": row.get("label", ""),
            "created_at": _iso(row.get("created_at")),
            "last_refresh_at": _iso(row.get("last_refresh_at")),
            "expires_at": _iso(expires_at),
        })
    out.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return out


def revoke(session_id: str, *, user_id: str) -> bool:
    """Kill one session. False if it does not exist or belongs to someone else.

    Ownership is checked here rather than trusted from the caller — this is the
    function that stops one user revoking another's session by guessing an id.
    """
    ref = fs.db().collection(fs.COL_SESSIONS).document(session_id)
    snap = ref.get()
    if not snap.exists:
        return False
    row = snap.to_dict() or {}
    if row.get("user_id") != user_id:
        return False
    ref.set({"revoked_at": _dt.datetime.now(_dt.timezone.utc)}, merge=True)
    return True


def revoke_all(user_id: str, *, kind: Optional[str] = None) -> int:
    """Kill every session for a user. Returns the count. Used by sign-out-everywhere
    and by suspension (where it runs alongside the KV denylist write)."""
    now = _dt.datetime.now(_dt.timezone.utc)
    n = 0
    db = fs.db()
    for snap in db.collection(fs.COL_SESSIONS).where("user_id", "==", user_id).stream():
        row = snap.to_dict() or {}
        if row.get("revoked_at"):
            continue
        if kind is not None and row.get("kind") != kind:
            continue
        db.collection(fs.COL_SESSIONS).document(snap.id).set({"revoked_at": now}, merge=True)
        n += 1
    return n


# ─── The access JWT ──────────────────────────────────────────────────────────


def mint_access_jwt(user_row: dict, *, session_id: str) -> tuple[str, int]:
    """Sign the cookie the Worker reads. Returns `(jwt, ttl_seconds)`.

    Claims are exactly the Worker's contract:
      iss, aud  — pinned; the Worker rejects a mismatch
      sub       — user_id; the KV denylist is keyed `u:{sub}`
      u         — ENROLLED USERNAME. The authorization claim. See module docstring.
      plan      — informational (surfaced in the paywall UI)
      entitled  — the live entitlement predicate at mint time
      sid       — session id, so a token can be traced back to a revocable row
    """
    username = (user_row or {}).get("username") or ""
    if not username:
        # No username → no subdomain → no `u` claim → the Worker's authorization
        # check has nothing to compare. Fail closed rather than mint a token that
        # would be either useless or (worse) permissive.
        raise ValueError("cannot mint a session JWT for a user with no enrolled username")

    ttl = access_ttl_seconds()
    token = jwt_es256.sign({
        "iss": issuer(),
        "aud": audience(),
        "sub": user_row.get("user_id") or "",
        "u": username,
        "plan": entitlement.plan_of(user_row),
        "entitled": entitlement.is_entitled(user_row),
        "sid": session_id,
    }, ttl_seconds=ttl)
    return token, ttl
