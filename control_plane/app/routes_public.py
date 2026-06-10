"""Public, unauthenticated endpoints.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

Routes:
  GET  /v1/health
  GET  /v1/connect       (HTML signin page; Firebase Auth in browser)
  POST /v1/signin/start  (registers enrollment_intent before signin)
  POST /v1/signin/complete (verifies Firebase ID token + drives enrollment)
  POST /v1/webhooks/cloudflare

See `docs/remote-access/03-control-plane-api.md` §3.1–3.4 + §5.4.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import logging
import os
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from . import build_info, firestore as fs

router = APIRouter()
log = logging.getLogger(__name__)


# Firebase web config — public values, OK to embed in HTML. Set via env on
# Cloud Run; documented in SETUP_CHECKLIST §3. The auth domain is normally
# `<project>.firebaseapp.com` but Cloud-Run-hosted Auth is also fine.
FB_API_KEY     = os.environ.get("FB_API_KEY", "")
FB_AUTH_DOMAIN = os.environ.get("FB_AUTH_DOMAIN", "clayrune.firebaseapp.com")
FB_PROJECT_ID  = os.environ.get("FB_PROJECT_ID", "clayrune")

# Allowed callback hosts: only loopback is permitted as the redirect target so
# we can't be turned into an open redirect that exfiltrates enrollment tokens.
_ALLOWED_CALLBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]"}

# Username policy mirror (kept in sync with routes_account.py:_USERNAME_RE).
_USERNAME_RE_STR = r"^[a-z0-9](-?[a-z0-9])*$"


def _is_safe_callback(url: str) -> bool:
    """True iff `url` is `http://(127.0.0.1|localhost):<port>/api/mc-callback`."""
    if not url:
        return False
    try:
        u = urlparse(url)
    except Exception:
        return False
    if u.scheme != "http":
        return False
    if (u.hostname or "").lower() not in _ALLOWED_CALLBACK_HOSTS:
        return False
    if u.path != "/api/mc-callback":
        return False
    return True


@router.get("/health", tags=["public"])
async def health() -> dict:
    """Cloud Run / load balancer probe. See doc §3.1."""
    return {
        "status": "ok",
        "build": build_info.VERSION,
        "time": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }


@router.get("/connect", response_class=HTMLResponse, tags=["public"])
async def connect_page(
    pub: str = Query(..., description="Device public key (base64)"),
    nonce: str = Query(..., description="CSRF nonce minted by MC client"),
    callback: str = Query(..., description="MC's loopback callback URL"),
):
    """Self-contained HTML signin page.

    Three query params (passed verbatim by MC client):
      - `pub`: device public key
      - `nonce`: CSRF nonce (binds this signin to the MC instance that opened
        the browser)
      - `callback`: MC's loopback callback URL

    The page:
      1. POSTs `/v1/signin/start` to register the (nonce, pub, callback) tuple
         in Firestore — protects against open-redirect via callback substitution.
      2. Loads Firebase Auth web SDK.
      3. User clicks "Sign in with Google".
      4. JS gets the Firebase ID token and POSTs `/v1/signin/complete` with
         {id_token, nonce, pub, username}.
      5. On success, JS redirects to `<callback>?nonce=…&enrollment_token=…&...`.
    """
    if not _is_safe_callback(callback):
        raise HTTPException(status_code=400, detail={
            "code": "bad_callback",
            "message": "Callback URL must be http://(127.0.0.1|localhost):<port>/api/mc-callback",
            "request_id": "x",
        })

    # Embed values via JSON to keep escaping sane; values are public (Firebase
    # web config + the user-supplied query params).
    import json as _json
    cfg = {
        "apiKey": FB_API_KEY,
        "authDomain": FB_AUTH_DOMAIN,
        "projectId": FB_PROJECT_ID,
    }
    html = _CONNECT_HTML.replace("__FB_CFG__", _json.dumps(cfg)) \
                        .replace("__PUB__",      _json.dumps(pub)) \
                        .replace("__NONCE__",    _json.dumps(nonce)) \
                        .replace("__CALLBACK__", _json.dumps(callback)) \
                        .replace("__USERNAME_RE__", _json.dumps(_USERNAME_RE_STR))
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})


@router.post("/signin/start", tags=["public"])
async def signin_start(
    request: Request,
    body: dict = Body(...),
):
    """Persist `{nonce, pub, callback}` as an enrollment_intent before signin.

    This is a defense-in-depth check: by the time `/signin/complete` runs, we
    can verify the (nonce, pub) the client claims came from the same MC
    instance that opened the browser, and that the callback URL hasn't been
    tampered with.
    """
    nonce = (body.get("nonce") or "").strip()
    pub = (body.get("pub") or "").strip()
    callback = (body.get("callback") or "").strip()
    if not nonce or not pub or not callback:
        raise HTTPException(status_code=400, detail={
            "code": "bad_envelope",
            "message": "nonce, pub, and callback are required.",
            "request_id": "x",
        })
    if not _is_safe_callback(callback):
        raise HTTPException(status_code=400, detail={
            "code": "bad_callback",
            "message": "Callback URL must be http://(127.0.0.1|localhost):<port>/api/mc-callback",
            "request_id": "x",
        })

    nonce_hash = hashlib.sha256(nonce.encode("utf-8")).hexdigest()
    pub_hash = hashlib.sha256(pub.encode("utf-8")).hexdigest()
    now = _dt.datetime.now(_dt.timezone.utc)
    expires = now + _dt.timedelta(minutes=15)

    db = fs.db()
    db.collection(fs.COL_ENROLL_INTENTS).document(nonce_hash).set({
        "csrf_nonce_hash": nonce_hash,
        "device_pub_hash": pub_hash,
        "callback": callback,
        "created_at": now,
        "expires_at": expires,
    })
    return {"ok": True, "expires_at": expires.isoformat(timespec="seconds").replace("+00:00", "Z")}


@router.post("/signin/complete", tags=["public"])
async def signin_complete(
    request: Request,
    body: dict = Body(...),
):
    """Verify Firebase ID token + drive the enrollment, return redirect params.

    Body: `{id_token, nonce, pub, username, device_name?, os?, mc_version?}`

    Returns: `{redirect_url}` — a URL the browser-side JS should navigate to
    (the MC loopback callback) with the enrollment fields appended as query
    params. Errors return `{ok: false, code, message}` with HTTP 4xx/5xx.
    """
    from .routes_account import (
        _verify_firebase_token, _is_username_valid, _USERNAME_RESERVED,
    )
    id_token  = (body.get("id_token") or "").strip()
    nonce     = (body.get("nonce") or "").strip()
    pub       = (body.get("pub") or "").strip()
    username  = (body.get("username") or "").strip().lower()
    if not id_token or not nonce or not pub or not username:
        raise HTTPException(status_code=400, detail={
            "code": "bad_envelope",
            "message": "id_token, nonce, pub, and username are required.",
            "request_id": "x",
        })

    # 1. Verify Firebase token
    try:
        user = _verify_firebase_token(id_token)
    except Exception as e:
        log.info("signin_complete: firebase verify failed: %s", e)
        raise HTTPException(status_code=401, detail={
            "code": "unauthorized",
            "message": f"Sign-in token invalid: {e}",
            "request_id": "x",
        })
    if not user.get("email_verified", False):
        raise HTTPException(status_code=403, detail={
            "code": "email_unverified",
            "message": "Verify your email with the provider, then try again.",
            "request_id": "x",
        })

    # 2. Validate username early
    ok, reason = _is_username_valid(username)
    if not ok:
        raise HTTPException(
            status_code=409 if username in _USERNAME_RESERVED else 400,
            detail={
                "code": "username_reserved" if username in _USERNAME_RESERVED else "username_invalid",
                "message": reason,
                "request_id": "x",
            },
        )

    # 3. Verify enrollment_intent matches (nonce + pub + still valid)
    nonce_hash = hashlib.sha256(nonce.encode("utf-8")).hexdigest()
    pub_hash = hashlib.sha256(pub.encode("utf-8")).hexdigest()
    db = fs.db()
    doc = db.collection(fs.COL_ENROLL_INTENTS).document(nonce_hash).get()
    if not doc.exists:
        raise HTTPException(status_code=400, detail={
            "code": "enrollment_intent_invalid",
            "message": "Sign-in expired. Click 'Enable Remote Access' again from MC.",
            "request_id": "x",
        })
    intent = doc.to_dict() or {}
    if intent.get("device_pub_hash") != pub_hash:
        raise HTTPException(status_code=400, detail={
            "code": "enrollment_intent_mismatch",
            "message": "Sign-in token doesn't match this MC instance.",
            "request_id": "x",
        })
    callback = intent.get("callback") or ""
    if not _is_safe_callback(callback):
        raise HTTPException(status_code=400, detail={
            "code": "bad_callback",
            "message": "Stored callback URL is invalid.",
            "request_id": "x",
        })

    # 4. Drive the same provisioning that POST /v1/enroll does. Reuse its
    #    machinery by synthesizing the request body and calling its handler
    #    function directly. Cleaner than duplicating the CF logic.
    import base64 as _b64
    try:
        # Validate pub is base64 32 bytes — same defense /v1/enroll does.
        if len(_b64.b64decode(pub, validate=True)) != 32:
            raise ValueError("pub is not 32 bytes")
    except Exception as e:
        raise HTTPException(status_code=400, detail={
            "code": "bad_envelope",
            "message": f"Invalid device public key: {e}",
            "request_id": "x",
        })

    # Call the enrollment helper (we need a synthetic request-like object).
    # Easier: refactor /v1/enroll to expose a `_do_enroll` callable. For
    # now, replicate the post-auth path inline — small enough to be clear.
    from .routes_account import _do_enroll_after_auth
    try:
        result = await _do_enroll_after_auth(
            user=user,
            device_pub_b64=pub,
            csrf_nonce=nonce,
            username=username,
            device_name=body.get("device_name", "Browser-enrolled device"),
            os_str=body.get("os", "browser"),
            mc_version=body.get("mc_version", "1.0.0"),
        )
    except HTTPException:
        # Burn the intent so the user can't replay it
        try:
            db.collection(fs.COL_ENROLL_INTENTS).document(nonce_hash).delete()
        except Exception:
            pass
        raise

    # 5. Build the redirect URL with the callback values that mc_remote.enrollment.complete expects
    params = {
        "nonce": nonce,
        "enrollment_token": result["enrollment_token"],
        "device_id": result["device_id"],
        "hostname": result["hostname"],
        "username": result["username"],
    }
    redirect_url = f"{callback}?{urlencode(params)}"

    # Burn the intent (also done inside _do_enroll_after_auth via _consume_enrollment_intent,
    # but be defensive in case that code path changes).
    try:
        db.collection(fs.COL_ENROLL_INTENTS).document(nonce_hash).delete()
    except Exception:
        pass

    return {"ok": True, "redirect_url": redirect_url}


# ─── HTML signin page ────────────────────────────────────────────────────────
# Self-contained: Firebase web SDK from CDN + a minimal layout matching the
# Mission Control look. Google sign-in only for v1; Email/password later.

_CONNECT_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Connect to Clayrune</title>
<style>
  :root { --accent:#e8824a; --bg:#fdfaf6; --fg:#1a1a1a; --muted:#6b6b6b; --border:#e0d8cc; }
  * { box-sizing: border-box; }
  html, body { margin:0; padding:0; height:100%; background:var(--bg); color:var(--fg);
              font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif; }
  .wrap { max-width: 460px; margin: 0 auto; padding: 36px 22px; }
  h1 { font-size: 22px; margin: 0 0 8px; font-weight: 700; }
  p.lead { color: var(--muted); font-size: 14px; line-height: 1.5; margin: 0 0 18px; }
  .card { background:#fff; border:2px solid var(--border); border-radius:14px; padding:18px 18px 22px; }
  label { display:block; font-size:12px; font-weight:600; color:var(--muted);
          text-transform:uppercase; letter-spacing:.04em; margin: 14px 0 6px; }
  input { width:100%; padding:12px 14px; font-size:16px; border:2px solid var(--border);
          border-radius:10px; background:#fff; color:var(--fg); }
  input:focus { outline:none; border-color:var(--accent); }
  button.primary { width:100%; margin-top:16px; padding:14px; font-size:16px; font-weight:600;
                   background:var(--accent); color:#fff; border:none; border-radius:10px; cursor:pointer; }
  button.primary:disabled { opacity:.5; cursor:not-allowed; }
  button.primary:hover:not(:disabled) { filter: brightness(1.05); }
  button.google { display:flex; align-items:center; justify-content:center; gap:10px;
                  width:100%; padding:12px 14px; font-size:15px; font-weight:600;
                  background:#fff; color:#1f2937; border:2px solid var(--border);
                  border-radius:10px; cursor:pointer; }
  button.google:hover { background:#f6f1ea; }
  .err { color:#c0392b; font-size:13px; margin-top:10px; min-height:1em; }
  .ok  { color:#0b8a3a; font-size:13px; margin-top:10px; min-height:1em; }
  .footer { text-align:center; font-size:11px; color:var(--muted); margin-top:18px; }
  .signed-in { font-size:13px; color:var(--muted); padding:10px 12px; background:#f6f1ea;
               border-radius:8px; margin-bottom:14px; }
  .signed-in b { color:var(--fg); }
  .hidden { display:none !important; }
</style>
</head>
<body>
  <div class="wrap">
    <h1>Connect this Clayrune</h1>
    <p class="lead">Sign in to claim a public URL like <code>yourname.clayrune.io</code>. Only you (signed in with this email) will be able to access it.</p>

    <div class="card">
      <div id="step-signin">
        <button class="google" id="btn-google">
          <svg width="18" height="18" viewBox="0 0 48 48"><path fill="#FFC107" d="M43.6 20.5H42V20.4H24v7.2h11.3c-1.6 4.6-5.9 7.9-11.3 7.9-6.6 0-12-5.4-12-12s5.4-12 12-12c3 0 5.7 1.1 7.8 3l5.1-5.1C33.2 6.7 28.9 5 24 5 13.5 5 5 13.5 5 24s8.5 19 19 19 19-8.5 19-19c0-1.2-.1-2.4-.4-3.5z"/><path fill="#FF3D00" d="M6.3 14.7l5.9 4.3C13.9 16 18.5 13 24 13c3 0 5.7 1.1 7.8 3l5.1-5.1C33.2 6.7 28.9 5 24 5c-7.4 0-13.7 4-17.7 9.7z"/><path fill="#4CAF50" d="M24 43c4.7 0 9-1.6 12.3-4.4l-5.7-4.8c-2 1.4-4.4 2.2-7.6 2.2-5.4 0-9.7-3.3-11.3-7.9l-5.9 4.5C9.7 38.7 16.3 43 24 43z"/><path fill="#1976D2" d="M43.6 20.5H42V20.4H24v7.2h11.3c-.7 2.1-2 4-3.7 5.4l5.7 4.8c-.4.4 6.7-4.9 6.7-13.8 0-1.2-.1-2.4-.4-3.5z"/></svg>
          Sign in with Google
        </button>
        <div class="err" id="signin-err"></div>
      </div>

      <div id="step-username" class="hidden">
        <div class="signed-in">Signed in as <b id="signed-email">…</b> · <a href="#" id="signout">sign out</a></div>
        <label for="uname">Pick a username</label>
        <input id="uname" placeholder="e.g. ron" autofocus maxlength="24" />
        <p class="lead" style="font-size:12px;margin:8px 0 0">3–24 chars, lowercase letters / numbers / dashes. Will become <code>&lt;name&gt;.clayrune.io</code>.</p>
        <button class="primary" id="btn-enroll" disabled>Connect</button>
        <div class="err" id="enroll-err"></div>
        <div class="ok"  id="enroll-ok"></div>
      </div>
    </div>
    <div class="footer">Clayrune</div>
  </div>

<script type="module">
import { initializeApp } from "https://www.gstatic.com/firebasejs/10.13.0/firebase-app.js";
import { getAuth, GoogleAuthProvider, signInWithPopup, signOut, onAuthStateChanged }
  from "https://www.gstatic.com/firebasejs/10.13.0/firebase-auth.js";

const FB_CFG   = __FB_CFG__;
const PUB      = __PUB__;
const NONCE    = __NONCE__;
const CALLBACK = __CALLBACK__;
const UNAME_RE = new RegExp(__USERNAME_RE__);

if (!FB_CFG.apiKey) {
  document.getElementById("signin-err").textContent =
    "Server misconfigured: Firebase apiKey not set. See SETUP_CHECKLIST §3.";
}

const app  = initializeApp(FB_CFG);
const auth = getAuth(app);

// Step 1: register the enrollment_intent before showing the signin button
let intentReady = false;
fetch("/v1/signin/start", {
  method: "POST", headers: {"Content-Type": "application/json"},
  body: JSON.stringify({nonce: NONCE, pub: PUB, callback: CALLBACK}),
}).then(r => r.json()).then(j => {
  if (j.ok) { intentReady = true; }
  else { document.getElementById("signin-err").textContent = j.message || "Failed to register enrollment intent"; }
}).catch(e => {
  document.getElementById("signin-err").textContent = "Network error: " + e;
});

// Google signin
document.getElementById("btn-google").addEventListener("click", async () => {
  if (!intentReady) {
    document.getElementById("signin-err").textContent = "Still registering... try again in a sec.";
    return;
  }
  document.getElementById("signin-err").textContent = "";
  try {
    await signInWithPopup(auth, new GoogleAuthProvider());
  } catch (e) {
    document.getElementById("signin-err").textContent = "Sign-in cancelled or failed: " + (e.message || e);
  }
});

// State changes drive UI
onAuthStateChanged(auth, (u) => {
  const stepSignin   = document.getElementById("step-signin");
  const stepUsername = document.getElementById("step-username");
  if (u) {
    stepSignin.classList.add("hidden");
    stepUsername.classList.remove("hidden");
    document.getElementById("signed-email").textContent = u.email || "(no email)";
  } else {
    stepUsername.classList.add("hidden");
    stepSignin.classList.remove("hidden");
  }
});

document.getElementById("signout").addEventListener("click", async (e) => {
  e.preventDefault();
  await signOut(auth);
});

// Username validation
const uInput   = document.getElementById("uname");
const enrollBtn= document.getElementById("btn-enroll");
function validUname() {
  const v = (uInput.value || "").trim().toLowerCase();
  return v.length >= 3 && v.length <= 24 && UNAME_RE.test(v);
}
uInput.addEventListener("input", () => { enrollBtn.disabled = !validUname(); });
uInput.addEventListener("keydown", (e) => { if (e.key === "Enter" && !enrollBtn.disabled) doEnroll(); });
enrollBtn.addEventListener("click", doEnroll);

async function doEnroll() {
  const errEl = document.getElementById("enroll-err");
  const okEl  = document.getElementById("enroll-ok");
  errEl.textContent = ""; okEl.textContent = "";
  enrollBtn.disabled = true;

  const u = auth.currentUser;
  if (!u) { errEl.textContent = "Sign in first."; enrollBtn.disabled = false; return; }
  let id_token;
  try { id_token = await u.getIdToken(true); }
  catch (e) { errEl.textContent = "Couldn't get sign-in token: " + e; enrollBtn.disabled = false; return; }

  const username = (uInput.value || "").trim().toLowerCase();
  try {
    const r = await fetch("/v1/signin/complete", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({id_token, nonce: NONCE, pub: PUB, username}),
    });
    const j = await r.json();
    if (r.ok && j.ok) {
      okEl.textContent = "Connected. Returning to Clayrune...";
      setTimeout(() => { window.location.href = j.redirect_url; }, 800);
    } else {
      errEl.textContent = j.message || ("Enrollment failed (HTTP " + r.status + ")");
      enrollBtn.disabled = false;
    }
  } catch (e) {
    errEl.textContent = "Network error: " + e;
    enrollBtn.disabled = false;
  }
}
</script>
</body>
</html>
"""
