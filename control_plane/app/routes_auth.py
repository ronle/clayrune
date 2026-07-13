"""Session auth: JWKS, sign-in, silent refresh, sign-out.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

This is the control-plane half of the contract with the edge Worker
(`clayrune-cloud/edge-worker/src/index.js`), which replaced Cloudflare Access.

  GET  /v1/jwks             public ES256 keys. The Worker fetches + caches 10 min.
  GET  /v1/signin           the page the Worker 302s an unauthenticated visitor to.
  POST /v1/session/start    Firebase ID token → `cr_session` + `cr_refresh` cookies.
  POST /v1/session/refresh  silent renewal. **The live-entitlement chokepoint.**
  POST /v1/session/logout   revoke this session, clear the cookies.

## Why refresh is the chokepoint and not a formality

The access JWT is verified at the edge with no origin call — which is the entire
point, and also means the edge cannot know anything the token doesn't say. So the
token's 30-minute TTL *is* our revocation lag, and `/v1/session/refresh` is the
one moment we get to re-read the world. It re-reads the LIVE user row every time:
subscription state, suspension, username. If you ever cache the user row here, or
mint from the old JWT's claims instead of from Firestore, the chokepoint quietly
stops being one and cancellations stop taking effect.

## ⚠️ The Worker's route matches this host too

`wrangler.toml` routes `*.clayrune.io/*` — which includes `api.clayrune.io`, where
this control plane lives. As written, the Worker would put itself in front of its
own sign-in page and its own JWKS, and an unauthenticated visitor would bounce
between the two forever. The Worker needs a reserved-subdomain bypass before this
ships. See `docs/remote-access/03-control-plane-api.md` §3.15.4 for the patch.
"""
from __future__ import annotations

import json as _json
import logging
import os
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, Body, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from . import auth, entitlement, firestore as fs, jwt_es256, sessions

router = APIRouter()
log = logging.getLogger(__name__)


def _zone() -> str:
    return os.environ.get("CLAYRUNE_PRIMARY_ZONE", "clayrune.io")


def _secure_cookies() -> bool:
    # A `Secure` cookie is dropped by the browser over plain http, so local dev
    # (http://localhost:8080) could never hold a session. Production is https-only
    # behind Cloud Run, and the dev shim is refused there (routes_account.py).
    return os.environ.get("MC_CP_DEV_AUTH") != "1"


def _paywall_url() -> str:
    return os.environ.get("CLAYRUNE_PAYWALL_URL", f"https://{_zone()}/upgrade")


# ─── GET /v1/jwks ────────────────────────────────────────────────────────────


@router.get("/jwks", tags=["auth"])
async def jwks() -> Response:
    """The Worker's only view of our signing keys. Public, cacheable, no auth.

    `max-age=600` matches the Worker's own 10-minute JWKS cache — a key rotation
    is visible at the edge within one window.
    """
    return JSONResponse(
        content=jwt_es256.jwks(),
        headers={"Cache-Control": "public, max-age=600"},
    )


# ─── Cookies ─────────────────────────────────────────────────────────────────


def _set_session_cookies(resp: Response, *, access_jwt: str, ttl: int,
                         refresh_token: Optional[str] = None) -> None:
    resp.set_cookie(
        sessions.COOKIE_NAME, access_jwt,
        max_age=ttl, domain=sessions.cookie_domain(), path="/",
        secure=_secure_cookies(), httponly=True, samesite="lax",
    )
    if refresh_token is not None:
        # Path-scoped to the refresh endpoints. It is a long-lived credential and
        # has no business being attached to every dashboard request — even though
        # the Worker strips `Cookie` before proxying to the user's tunnel.
        resp.set_cookie(
            sessions.REFRESH_COOKIE_NAME, refresh_token,
            max_age=sessions.BROWSER_REFRESH_DAYS * 86400,
            domain=sessions.cookie_domain(), path="/v1/session",
            secure=_secure_cookies(), httponly=True, samesite="lax",
        )


def _clear_session_cookies(resp: Response, *, refresh: bool = True) -> None:
    resp.delete_cookie(sessions.COOKIE_NAME, domain=sessions.cookie_domain(), path="/")
    if refresh:
        resp.delete_cookie(sessions.REFRESH_COOKIE_NAME,
                           domain=sessions.cookie_domain(), path="/v1/session")


# ─── POST /v1/session/start ──────────────────────────────────────────────────


@router.post("/session/start", tags=["auth"])
async def session_start(request: Request, body: dict = Body(...)) -> Response:
    """Firebase ID token → a Clayrune session.

    Body: `{"id_token": "<firebase>"}`

    Requires an **enrolled** user: no `users/{uid}.username` means no subdomain,
    which means no `u` claim, which means the Worker's authorization check has
    nothing to compare against. We refuse rather than mint a token that is either
    useless or dangerous.
    """
    from .routes_account import _verify_firebase_token

    id_token = (body.get("id_token") or "").strip()
    if not id_token:
        raise HTTPException(status_code=400, detail={
            "code": "bad_envelope", "message": "id_token is required.", "request_id": "x",
        })

    try:
        fb = _verify_firebase_token(id_token)
    except Exception as e:
        log.info("session_start: firebase verify failed: %s", e)
        raise HTTPException(status_code=401, detail={
            "code": "unauthorized", "message": f"Sign-in token invalid: {e}", "request_id": "x",
        })
    if not fb.get("email_verified", False):
        raise HTTPException(status_code=403, detail={
            "code": "email_unverified",
            "message": "Verify your email with the provider, then try again.",
            "request_id": "x",
        })

    user_row = fs.user_get(fb["user_id"])
    if not user_row or not user_row.get("username"):
        raise HTTPException(status_code=409, detail={
            "code": "not_enrolled",
            "message": "This account has no Clayrune subdomain yet. "
                       "Enable Remote Access from Clayrune on your machine first.",
            "request_id": "x",
        })

    username = user_row["username"]
    ip = auth.ip_hash(request)
    session_id, refresh_token, _ = sessions.create(
        user_id=user_row["user_id"], username=username,
        kind=sessions.KIND_BROWSER, label=_client_label(request), ip_hash=ip,
    )
    access_jwt, ttl = sessions.mint_access_jwt(user_row, session_id=session_id)
    entitled = entitlement.is_entitled(user_row)

    resp = JSONResponse({
        "ok": True,
        "username": username,
        "hostname": f"{username}.{_zone()}",
        "entitled": entitled,
        "expires_in": ttl,
        "paywall_url": None if entitled else _paywall_url(),
    })
    _set_session_cookies(resp, access_jwt=access_jwt, ttl=ttl, refresh_token=refresh_token)
    return resp


# ─── POST /v1/session/refresh ────────────────────────────────────────────────


@router.post("/session/refresh", tags=["auth"])
async def session_refresh(request: Request) -> Response:
    """Renew the access JWT against LIVE subscription state.

    Credential: the `cr_refresh` cookie (browser) or `{"refresh_token": "..."}`
    in the body (the phone, which has no cookie jar we control).

    Outcomes:
      200  renewed
      401  session unknown / revoked / expired  → cookies cleared, sign in again
      402  session valid, entitlement is not    → cookies cleared, go to the paywall

    The 401/402 split matters: 401 means *we don't know who you are*, 402 means
    *we know exactly who you are and you haven't paid*. Collapsing them would
    bounce a lapsed customer through sign-in forever instead of to a checkout page.
    """
    token = request.cookies.get(sessions.REFRESH_COOKIE_NAME, "")
    if not token:
        try:
            body = await request.json()
            if isinstance(body, dict):
                token = (body.get("refresh_token") or "").strip()
        except Exception:
            token = ""

    row = sessions.resolve_refresh(token)
    if row is None:
        resp = JSONResponse(status_code=401, content={
            "code": "session_invalid",
            "message": "Session expired or revoked. Sign in again.",
            "request_id": "x",
        })
        _clear_session_cookies(resp)
        return resp

    # LIVE read. Never mint from the previous token's claims.
    user_row = fs.user_get(row["user_id"])
    if not user_row or not user_row.get("username"):
        sessions.revoke(row["_id"], user_id=row["user_id"])
        resp = JSONResponse(status_code=401, content={
            "code": "session_invalid",
            "message": "Account no longer has a Clayrune subdomain.",
            "request_id": "x",
        })
        _clear_session_cookies(resp)
        return resp

    if not entitlement.is_entitled(user_row):
        # Do NOT revoke the session — the subscription may come back (a card
        # retry succeeds, a suspension is lifted) and the user should not have to
        # re-authenticate to discover that. Just refuse to mint.
        resp = JSONResponse(status_code=402, content={
            "code": "not_entitled",
            "message": "This subscription is not active.",
            "paywall_url": _paywall_url(),
            "request_id": "x",
        })
        _clear_session_cookies(resp, refresh=False)
        return resp

    # The username is re-read here too, so a username change propagates into the
    # `u` claim on the next refresh instead of stranding the session on the old
    # subdomain (the Worker would 403 it: claims.u !== want).
    access_jwt, ttl = sessions.mint_access_jwt(user_row, session_id=row["_id"])
    sessions.touch(row["_id"], ip_hash=auth.ip_hash(request))

    resp = JSONResponse({
        "ok": True,
        "username": user_row["username"],
        "entitled": True,
        "expires_in": ttl,
    })
    _set_session_cookies(resp, access_jwt=access_jwt, ttl=ttl)
    return resp


# ─── POST /v1/session/logout ─────────────────────────────────────────────────


@router.post("/session/logout", tags=["auth"])
async def session_logout(request: Request) -> Response:
    """Revoke this session and clear the cookies.

    The access JWT already in the browser stays cryptographically valid until it
    expires — that is inherent to stateless edge verification. What this
    guarantees is that it is never renewed. To cut a session off *now*, suspend
    the user (KV denylist) — that is what the denylist is for.
    """
    token = request.cookies.get(sessions.REFRESH_COOKIE_NAME, "")
    row = sessions.resolve_refresh(token) if token else None
    if row is not None:
        sessions.revoke(row["_id"], user_id=row["user_id"])

    resp = JSONResponse({"ok": True})
    _clear_session_cookies(resp)
    return resp


# ─── GET /v1/signin ──────────────────────────────────────────────────────────


def _safe_return_to(url: str) -> Optional[str]:
    """Only ever bounce back to an https URL inside our own zone.

    The Worker appends `?return_to=<the url they wanted>` when it 302s here. That
    parameter is attacker-controllable, so it is an open-redirect primitive until
    proven otherwise — and this page is reached with a live session cookie in
    scope.
    """
    if not url:
        return None
    try:
        u = urlparse(url)
    except ValueError:
        return None
    if u.scheme != "https":
        return None
    host = (u.hostname or "").lower()
    zone = _zone().lower()
    if host != zone and not host.endswith("." + zone):
        return None
    return url


@router.get("/signin", response_class=HTMLResponse, tags=["auth"])
async def signin_page(
    return_to: str = Query("", description="Where to send the user after sign-in"),
):
    """The page the Worker redirects an unauthenticated visitor to.

    Point the Worker's `SIGNIN_URL` at this: `https://api.<zone>/v1/signin`.
    Serving it from the control plane's own origin is what lets `/v1/session/start`
    set a `Domain=.clayrune.io` cookie without a cross-origin dance.
    """
    dest = _safe_return_to(return_to) or ""
    html = _SIGNIN_HTML \
        .replace("__FB_CFG__", _json.dumps({
            "apiKey": os.environ.get("FB_API_KEY", ""),
            "authDomain": os.environ.get("FB_AUTH_DOMAIN", "clayrune.firebaseapp.com"),
            "projectId": os.environ.get("FB_PROJECT_ID", "clayrune"),
        })) \
        .replace("__RETURN_TO__", _json.dumps(dest)) \
        .replace("__ZONE__", _json.dumps(_zone()))
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})


def _client_label(request: Request) -> str:
    """A human-recognisable label for the sessions list. Coarse on purpose — we
    are not building a fingerprint, just something the user can point at and say
    'that one is not me'."""
    ua = (request.headers.get("user-agent") or "").lower()
    if "android" in ua:
        return "Android browser"
    if "iphone" in ua or "ipad" in ua:
        return "iOS browser"
    if "edg/" in ua:
        return "Edge"
    if "chrome" in ua:
        return "Chrome"
    if "firefox" in ua:
        return "Firefox"
    if "safari" in ua:
        return "Safari"
    return "Browser"


_SIGNIN_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in · Clayrune</title>
<style>
  :root { --accent:#e8824a; --bg:#fdfaf6; --fg:#1a1a1a; --muted:#6b6b6b; --border:#e0d8cc; }
  * { box-sizing: border-box; }
  html, body { margin:0; padding:0; height:100%; background:var(--bg); color:var(--fg);
              font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif; }
  .wrap { max-width: 420px; margin: 0 auto; padding: 48px 22px; }
  h1 { font-size: 22px; margin: 0 0 8px; font-weight: 700; }
  p.lead { color: var(--muted); font-size: 14px; line-height: 1.5; margin: 0 0 18px; }
  .card { background:#fff; border:2px solid var(--border); border-radius:14px; padding:20px; }
  button.google { display:flex; align-items:center; justify-content:center; gap:10px;
                  width:100%; padding:12px 14px; font-size:15px; font-weight:600;
                  background:#fff; color:#1f2937; border:2px solid var(--border);
                  border-radius:10px; cursor:pointer; }
  button.google:hover { background:#f6f1ea; }
  .err { color:#c0392b; font-size:13px; margin-top:12px; min-height:1em; }
  .ok  { color:#0b8a3a; font-size:13px; margin-top:12px; min-height:1em; }
  .footer { text-align:center; font-size:11px; color:var(--muted); margin-top:18px; }
</style>
</head>
<body>
  <div class="wrap">
    <h1>Sign in to Clayrune</h1>
    <p class="lead">Sign in with the account you enrolled with to reach your dashboard.</p>
    <div class="card">
      <button class="google" id="btn-google">
        <svg width="18" height="18" viewBox="0 0 48 48"><path fill="#FFC107" d="M43.6 20.5H42V20.4H24v7.2h11.3c-1.6 4.6-5.9 7.9-11.3 7.9-6.6 0-12-5.4-12-12s5.4-12 12-12c3 0 5.7 1.1 7.8 3l5.1-5.1C33.2 6.7 28.9 5 24 5 13.5 5 5 13.5 5 24s8.5 19 19 19 19-8.5 19-19c0-1.2-.1-2.4-.4-3.5z"/><path fill="#FF3D00" d="M6.3 14.7l5.9 4.3C13.9 16 18.5 13 24 13c3 0 5.7 1.1 7.8 3l5.1-5.1C33.2 6.7 28.9 5 24 5c-7.4 0-13.7 4-17.7 9.7z"/><path fill="#4CAF50" d="M24 43c4.7 0 9-1.6 12.3-4.4l-5.7-4.8c-2 1.4-4.4 2.2-7.6 2.2-5.4 0-9.7-3.3-11.3-7.9l-5.9 4.5C9.7 38.7 16.3 43 24 43z"/><path fill="#1976D2" d="M43.6 20.5H42V20.4H24v7.2h11.3c-.7 2.1-2 4-3.7 5.4l5.7 4.8c-.4.4 6.7-4.9 6.7-13.8 0-1.2-.1-2.4-.4-3.5z"/></svg>
        Sign in with Google
      </button>
      <div class="err" id="err"></div>
      <div class="ok"  id="ok"></div>
    </div>
    <div class="footer">Clayrune</div>
  </div>
<script type="module">
import { initializeApp } from "https://www.gstatic.com/firebasejs/10.13.0/firebase-app.js";
import { getAuth, GoogleAuthProvider, signInWithPopup }
  from "https://www.gstatic.com/firebasejs/10.13.0/firebase-auth.js";

const FB_CFG    = __FB_CFG__;
const RETURN_TO = __RETURN_TO__;
const ZONE      = __ZONE__;
const errEl = document.getElementById("err");
const okEl  = document.getElementById("ok");

if (!FB_CFG.apiKey) errEl.textContent = "Server misconfigured: Firebase apiKey not set.";

const auth = getAuth(initializeApp(FB_CFG));

document.getElementById("btn-google").addEventListener("click", async () => {
  errEl.textContent = ""; okEl.textContent = "";
  let idToken;
  try {
    const cred = await signInWithPopup(auth, new GoogleAuthProvider());
    idToken = await cred.user.getIdToken(true);
  } catch (e) {
    errEl.textContent = "Sign-in cancelled or failed: " + (e.message || e);
    return;
  }
  try {
    const r = await fetch("/v1/session/start", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      credentials: "include",
      body: JSON.stringify({id_token: idToken}),
    });
    const j = await r.json();
    if (!r.ok) { errEl.textContent = j.message || ("Sign-in failed (HTTP " + r.status + ")"); return; }
    if (!j.entitled && j.paywall_url) { window.location.href = j.paywall_url; return; }
    okEl.textContent = "Signed in. Taking you to your dashboard...";
    const dest = RETURN_TO || ("https://" + j.username + "." + ZONE + "/");
    setTimeout(() => { window.location.href = dest; }, 600);
  } catch (e) {
    errEl.textContent = "Network error: " + e;
  }
});
</script>
</body>
</html>
"""
