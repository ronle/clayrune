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
import re
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


# A session id, exactly as `create()` mints it: `sess_` + url-safe alphanumerics.
# resolve_refresh() feeds this straight to Firestore's `.document()`, and the
# client library splits the string on "/" to build a document path — so a token
# like "a/b.secret" yields an odd-length path and raises ValueError, i.e. an
# HTTP 500. resolve_refresh() is documented to return ONE undifferentiated
# failure so a token probe cannot learn *why* it failed; a 500 among 401s is
# precisely that signal. Validate the shape before it ever reaches Firestore.
_SESSION_ID_RE = re.compile(r"^sess_[A-Za-z0-9]{1,32}$")

# How long a just-superseded refresh secret is still tolerated after rotation.
#
# Rotation races are real and benign: two tabs refresh at once, or the response
# carrying the new token is lost in flight and the client retries with the old
# one. Revoking the session on those would log people out at random and train us
# to distrust the alarm. Outside this window, presenting a superseded secret has
# no innocent explanation left — that is the theft signal, and we act on it.
REUSE_GRACE_SECONDS = 60


def _split(token: str) -> tuple[str, str]:
    session_id, _, secret = (token or "").partition(".")
    return session_id.strip(), secret.strip()


def rotates(kind: str) -> bool:
    """Does this session kind rotate its refresh secret on every use?

    **Browser: yes. Phone: not yet — and turning it on would brick every paired
    phone in the field.**

    The shipped APK stores the `pair_token` it was given at QR-pairing time and
    replays that *same* token at every renewal, keeping only the `cr_session`
    cookie it gets back (see routes_account.py, "Mobile pairing" flow, step 4).
    A rotating server would hand it a new secret it does not persist; on the next
    renewal it would present the superseded one and reuse-detection would revoke
    the session as theft. Every phone, dead, and the failure would look exactly
    like the attack we built the detector for.

    Mobile rotation lands WITH the mobile-pairing rework (backlog `ee94a17e`),
    which is what teaches the APK to persist the rotated token. Flip this then —
    not before.
    """
    return kind != KIND_MOBILE


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


def _expired(expires_at, now: _dt.datetime) -> bool:
    """Is this session past its expiry?

    An unparseable `expires_at` counts as EXPIRED, not as "no expiry". It is the
    only thing bounding a long-lived bearer credential, so treating a corrupt
    value as non-expiring mints an immortal refresh token that no cleanup will
    ever reap. The cost of failing closed is one sign-in; the cost of failing
    open is unbounded. Easy trade.
    """
    if expires_at is None:
        log.error("[sessions] session row has no expires_at — treating as expired")
        return True
    try:
        return expires_at < now
    except TypeError:
        log.error("[sessions] unparseable expires_at %r — treating as EXPIRED "
                  "(a corrupt expiry must never yield an immortal token)", expires_at)
        return True


def resolve_refresh(token: str) -> Optional[dict]:
    """Return the session row for a refresh token, or None if it is not usable.

    None covers: malformed, unknown, revoked, expired, secret-mismatch, and
    detected reuse. The caller gets one undifferentiated failure on purpose — a
    token probe should not learn *why* it failed.

    **Rotation (browser sessions).** A used secret is retired and replaced. The
    caller must hand the new token in `row["_new_refresh_token"]` back to the
    client. Presenting a superseded secret after `REUSE_GRACE_SECONDS` is proof
    that two parties hold the same token — the session is revoked outright and
    None is returned. See `rotates()` for why phones are excluded (for now).
    """
    session_id, secret = _split(token)
    if not session_id or not secret:
        return None
    if not _SESSION_ID_RE.match(session_id):
        # Never let an unvalidated id reach Firestore's .document() — see
        # _SESSION_ID_RE. A 500 here would be a side channel.
        return None

    ref = fs.db().collection(fs.COL_SESSIONS).document(session_id)
    snap = ref.get()
    if not snap.exists:
        return None
    row = snap.to_dict() or {}

    if row.get("revoked_at"):
        return None

    now = _dt.datetime.now(_dt.timezone.utc)
    if _expired(row.get("expires_at"), now):
        return None

    presented = _hash(secret)
    current_ok = secrets.compare_digest(row.get("refresh_hash", ""), presented)

    if not current_ok:
        prev = row.get("prev_refresh_hash") or ""
        prev_ok = bool(prev) and secrets.compare_digest(prev, presented)
        if not prev_ok:
            return None

        # A superseded secret. Inside the grace window this is a race or a retry
        # (see REUSE_GRACE_SECONDS) — let it through and rotate again. Outside
        # it, the only explanation left is that someone else has this token.
        rotated_at = row.get("rotated_at")
        within_grace = False
        try:
            within_grace = (rotated_at is not None and
                            (now - rotated_at).total_seconds() <= REUSE_GRACE_SECONDS)
        except TypeError:
            within_grace = False  # unparseable → treat as OUTSIDE grace (fail closed)

        if not within_grace:
            log.error(
                "[sessions] REFRESH TOKEN REUSE on %s (user=%s): a superseded secret "
                "was presented %ss after rotation. Two parties hold this token. "
                "Revoking the session.",
                session_id, row.get("user_id"),
                int((now - rotated_at).total_seconds()) if rotated_at else "?",
            )
            ref.set({"revoked_at": now, "revoked_reason": "refresh_reuse"}, merge=True)
            return None

        log.info("[sessions] %s: superseded secret replayed inside the %ss grace "
                 "window — treating as a race/retry, not theft.",
                 session_id, REUSE_GRACE_SECONDS)

    row["_id"] = session_id

    if rotates(row.get("kind") or KIND_BROWSER):
        new_secret = secrets.token_urlsafe(32)
        ref.set({
            "refresh_hash": _hash(new_secret),
            "prev_refresh_hash": row.get("refresh_hash", ""),
            "rotated_at": now,
        }, merge=True)
        row["_new_refresh_token"] = f"{session_id}.{new_secret}"

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
        if _expired(expires_at, now):
            continue
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
    and by suspension (where it runs alongside the KV denylist write).

    ONE atomic batch, not a write-per-session loop. This runs on the abuse path:
    a partial failure mid-loop would leave some of a suspended abuser's sessions
    alive while returning a count that claims they are all gone — the worst kind
    of wrong, because it reads as success.
    """
    now = _dt.datetime.now(_dt.timezone.utc)
    db = fs.db()
    batch = db.batch()
    n = 0
    for snap in db.collection(fs.COL_SESSIONS).where("user_id", "==", user_id).stream():
        row = snap.to_dict() or {}
        if row.get("revoked_at"):
            continue
        if kind is not None and row.get("kind") != kind:
            continue
        batch.set(db.collection(fs.COL_SESSIONS).document(snap.id),
                  {"revoked_at": now}, merge=True)
        n += 1
    if n:
        batch.commit()  # all-or-nothing: the count we return is now the truth
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
