"""Admin / operator endpoints.

PROPRIETARY AND CONFIDENTIAL.
Copyright (c) 2026 Clayrune. All rights reserved.

Routes:
  GET  /v1/admin            — HTML operator dashboard (Firebase signin + tables)
  GET  /v1/admin/data       — JSON: users + devices aggregate (admin-only)

Admin allowlist: comma-separated emails in `MC_CP_ADMIN_EMAILS` env var
(default: `leviran1@gmail.com`). Anyone not on the list signing into the
page sees an "Access denied" panel; the JSON endpoint returns 403.

The operator dashboard intentionally aggregates only Firestore-resident data
(users + devices). CF Access sessions per user would require an O(N) fan-out
of CF API calls and we hit rate limits on free Zero Trust quickly. If you
need session visibility, click through to the user-facing /v1/sessions for
your own email or extend this with a paginated, cached worker job.
"""
from __future__ import annotations

import datetime as _dt
import logging
import os
from typing import Optional

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse

from . import firestore as fs

router = APIRouter()
log = logging.getLogger(__name__)


def _admin_emails() -> set[str]:
    raw = os.environ.get("MC_CP_ADMIN_EMAILS", "leviran1@gmail.com")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def _require_admin(authorization: Optional[str]) -> dict:
    """Verify the caller's Firebase token + check email allowlist.

    Raises 401 if the token is missing or invalid; 403 if valid but the email
    isn't in MC_CP_ADMIN_EMAILS.
    """
    from .routes_account import _verify_firebase_token

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail={
            "code": "unauthorized",
            "message": "Authorization: Bearer <Firebase ID token> required.",
            "request_id": "x",
        })
    try:
        user = _verify_firebase_token(authorization[7:])
    except Exception as e:
        raise HTTPException(status_code=401, detail={
            "code": "unauthorized", "message": f"Invalid Firebase token: {e}",
            "request_id": "x",
        })
    email = (user.get("email") or "").strip().lower()
    if email not in _admin_emails():
        raise HTTPException(status_code=403, detail={
            "code": "forbidden",
            "message": f"{email} is not an operator. Add to MC_CP_ADMIN_EMAILS to grant access.",
            "request_id": "x",
        })
    return user


@router.get("/data", tags=["admin"])
async def admin_data(
    request: Request,
    authorization: Optional[str] = Header(None, alias="Authorization"),
):
    """Aggregate operator view: users + devices, with online status.

    Online heuristic: device.last_seen within the last 15 min.
    """
    _require_admin(authorization)

    db = fs.db()
    now = _dt.datetime.now(_dt.timezone.utc)
    online_window_s = 15 * 60

    def _iso(v):
        if v is None:
            return None
        try:
            return v.isoformat(timespec="seconds").replace("+00:00", "Z")
        except Exception:
            return str(v)

    users: list[dict] = []
    user_devices: dict[str, list[dict]] = {}
    online_total = 0
    devices_total = 0

    # Collect devices first (single scan, group by user)
    for d in db.collection(fs.COL_DEVICES).stream():
        row = d.to_dict() or {}
        if row.get("revoked_at"):
            continue
        last_seen = row.get("last_seen")
        online = False
        if last_seen is not None:
            try:
                online = (now - last_seen).total_seconds() < online_window_s
            except Exception:
                pass
        if online:
            online_total += 1
        devices_total += 1
        user_devices.setdefault(row.get("user_id", ""), []).append({
            "device_id": d.id,
            "device_name": row.get("device_name") or "Unnamed device",
            "hostname": row.get("hostname_claim") or "",
            "os": row.get("os") or "",
            "mc_version": row.get("mc_version") or "",
            "online": online,
            "last_seen": _iso(last_seen),
            "enrolled_at": _iso(row.get("enrolled_at")),
            "last_attestation_result": row.get("last_attestation_result"),
        })

    # Then users
    for u in db.collection(fs.COL_USERS).stream():
        row = u.to_dict() or {}
        users.append({
            "user_id": u.id,
            "email": row.get("email", ""),
            "username": row.get("username", ""),
            "tier": row.get("tier", "free"),
            "device_cap": int(row.get("device_cap", 2)),
            "bandwidth_quota_period_bytes": int(row.get("bandwidth_quota_period_bytes", 0)),
            "bandwidth_used_period_bytes": int(row.get("bandwidth_used_period_bytes", 0)),
            "created_at": _iso(row.get("created_at")),
            "devices": user_devices.get(u.id, []),
        })

    # Sort users: most recent enrollment first
    def _user_recency(u):
        devs = u.get("devices") or []
        return max((d.get("enrolled_at") or "" for d in devs), default="")
    users.sort(key=_user_recency, reverse=True)

    return {
        "summary": {
            "users": len(users),
            "devices": devices_total,
            "online": online_total,
            "as_of": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        },
        "users": users,
    }


@router.get("", response_class=HTMLResponse, tags=["admin"])
@router.get("/", response_class=HTMLResponse, tags=["admin"])
async def admin_page():
    """Operator dashboard HTML. Self-contained: Firebase Auth + table render."""
    import json as _json
    cfg = {
        "apiKey": os.environ.get("FB_API_KEY", ""),
        "authDomain": os.environ.get("FB_AUTH_DOMAIN", "clayrune-49e57.firebaseapp.com"),
        "projectId": os.environ.get("FB_PROJECT_ID", "clayrune-49e57"),
    }
    html = _ADMIN_HTML.replace("__FB_CFG__", _json.dumps(cfg))
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})


_ADMIN_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Mission Control · Operator Dashboard</title>
<style>
  :root { --accent:#e8824a; --bg:#fdfaf6; --fg:#1a1a1a; --muted:#6b6b6b;
          --border:#e0d8cc; --ok:#0b8a3a; --warn:#c0392b; --pill:#f6f1ea; }
  * { box-sizing: border-box; }
  html, body { margin:0; padding:0; min-height:100%; background:var(--bg); color:var(--fg);
              font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif; }
  .wrap { max-width: 1200px; margin: 0 auto; padding: 24px 22px; }
  header { display:flex; justify-content:space-between; align-items:center; margin-bottom:20px; }
  h1 { font-size:22px; margin:0; font-weight:700; }
  .who { font-size:13px; color:var(--muted); }
  .who a { color:var(--accent); margin-left:8px; }
  .summary { display:grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
             gap:12px; margin-bottom:24px; }
  .card { background:#fff; border:2px solid var(--border); border-radius:14px; padding:14px 16px; }
  .card .label { font-size:11px; font-weight:600; color:var(--muted); text-transform:uppercase;
                  letter-spacing:.04em; margin-bottom:4px; }
  .card .value { font-size:24px; font-weight:700; }
  .users { display:flex; flex-direction:column; gap:14px; }
  .user { background:#fff; border:2px solid var(--border); border-radius:14px; padding:14px 18px; }
  .user-head { display:flex; justify-content:space-between; align-items:center; margin-bottom:8px; }
  .user-email { font-weight:600; font-size:15px; }
  .user-meta { font-size:12px; color:var(--muted); }
  .pill { display:inline-block; font-size:11px; padding:2px 8px; border-radius:99px;
          background:var(--pill); color:var(--fg); margin-left:6px; }
  .pill.online { background:#e0f4e8; color:var(--ok); }
  .pill.offline { background:#f3f0ea; color:var(--muted); }
  table { width:100%; border-collapse:collapse; margin-top:8px; font-size:13px; }
  th, td { text-align:left; padding:6px 10px; border-bottom:1px dashed #f0eee8; }
  th { font-weight:600; color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.04em; }
  td.mono { font-family: var(--mono, Consolas, monospace); font-size:12px; color:var(--muted); }
  .empty { color:var(--muted); font-style:italic; padding:14px; text-align:center; }
  .err { color:var(--warn); font-size:13px; padding:14px; }
  .signin { background:#fff; border:2px solid var(--border); border-radius:14px;
            padding:24px; text-align:center; max-width:380px; margin:60px auto; }
  button.primary { padding:12px 18px; font-size:15px; font-weight:600;
                   background:var(--accent); color:#fff; border:none; border-radius:10px; cursor:pointer; }
  button.primary:hover { filter:brightness(1.05); }
  button.signout { font-size:12px; padding:6px 10px; background:transparent;
                   border:1px solid var(--border); border-radius:8px; cursor:pointer; color:var(--muted); }
  .footer { text-align:center; font-size:11px; color:var(--muted); margin-top:32px; }
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Operator Dashboard</h1>
    <div id="who" class="who"></div>
  </header>

  <div id="signin" class="signin" style="display:none">
    <p style="margin:0 0 14px;color:var(--muted);font-size:14px">Sign in to view enrolled devices.</p>
    <button class="primary" id="btn-google">Sign in with Google</button>
    <div class="err" id="signin-err"></div>
  </div>

  <div id="dash" style="display:none">
    <div class="summary" id="summary"></div>
    <div class="users" id="users"></div>
  </div>

  <div class="footer">Mission Control · Clayrune</div>
</div>

<script type="module">
import { initializeApp } from "https://www.gstatic.com/firebasejs/10.13.0/firebase-app.js";
import { getAuth, GoogleAuthProvider, signInWithPopup, signOut, onAuthStateChanged }
  from "https://www.gstatic.com/firebasejs/10.13.0/firebase-auth.js";

const FB_CFG = __FB_CFG__;
if (!FB_CFG.apiKey) {
  document.getElementById("signin-err").textContent =
    "Server misconfigured: Firebase apiKey not set.";
}

const app  = initializeApp(FB_CFG);
const auth = getAuth(app);

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s||"").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const fmt = (iso) => iso ? new Date(iso).toLocaleString() : "—";

document.getElementById("btn-google").addEventListener("click", async () => {
  $("signin-err").textContent = "";
  try { await signInWithPopup(auth, new GoogleAuthProvider()); }
  catch (e) { $("signin-err").textContent = "Sign-in failed: " + (e.message || e); }
});

onAuthStateChanged(auth, async (u) => {
  const signinEl = $("signin"), dashEl = $("dash"), whoEl = $("who");
  if (!u) {
    dashEl.style.display = "none";
    signinEl.style.display = "block";
    whoEl.innerHTML = "";
    return;
  }
  whoEl.innerHTML = `Signed in as <b>${esc(u.email)}</b> <a href="#" id="so">sign out</a>`;
  $("so").addEventListener("click", (e) => { e.preventDefault(); signOut(auth); });
  signinEl.style.display = "none";
  dashEl.style.display = "block";
  await loadData(u);
});

async function loadData(u) {
  $("summary").innerHTML = "";
  $("users").innerHTML = `<div class="empty">Loading...</div>`;
  let id_token;
  try { id_token = await u.getIdToken(true); } catch (e) {
    $("users").innerHTML = `<div class="err">Couldn't get sign-in token: ${esc(e)}</div>`; return;
  }
  let r, j;
  try {
    r = await fetch("/v1/admin/data", { headers: {"Authorization": "Bearer " + id_token}});
    j = await r.json();
  } catch (e) {
    $("users").innerHTML = `<div class="err">Network error: ${esc(e)}</div>`; return;
  }
  if (!r.ok) {
    const msg = (j && j.message) || ("HTTP " + r.status);
    $("users").innerHTML = `<div class="err">${esc(msg)}</div>`; return;
  }
  renderSummary(j.summary);
  renderUsers(j.users);
}

function renderSummary(s) {
  $("summary").innerHTML = `
    <div class="card"><div class="label">Users</div><div class="value">${s.users}</div></div>
    <div class="card"><div class="label">Devices</div><div class="value">${s.devices}</div></div>
    <div class="card"><div class="label">Online now</div><div class="value">${s.online}</div></div>
    <div class="card"><div class="label">As of</div><div class="value" style="font-size:13px;font-weight:400">${fmt(s.as_of)}</div></div>
  `;
}

function renderUsers(users) {
  if (!users.length) { $("users").innerHTML = `<div class="empty">No users enrolled yet.</div>`; return; }
  $("users").innerHTML = users.map(u => {
    const onlineCount = (u.devices||[]).filter(d => d.online).length;
    const totalCount = (u.devices||[]).length;
    const bw = `${(u.bandwidth_used_period_bytes/1024/1024).toFixed(1)} / ${(u.bandwidth_quota_period_bytes/1024/1024/1024).toFixed(2)} GB`;
    const rows = (u.devices||[]).length
      ? `<table>
          <thead><tr><th>Device</th><th>Hostname</th><th>OS</th><th>Status</th><th>Last seen</th></tr></thead>
          <tbody>${u.devices.map(d => `
            <tr>
              <td>${esc(d.device_name)}</td>
              <td class="mono">${esc(d.hostname)}</td>
              <td class="mono">${esc(d.os)}</td>
              <td><span class="pill ${d.online ? "online" : "offline"}">${d.online ? "online" : "offline"}</span></td>
              <td>${fmt(d.last_seen)}</td>
            </tr>`).join("")}</tbody>
        </table>`
      : `<div class="empty">No active devices.</div>`;
    return `<div class="user">
      <div class="user-head">
        <div class="user-email">${esc(u.email || "(no email)")} <span class="pill">@${esc(u.username || "—")}</span></div>
        <div class="user-meta">${onlineCount}/${totalCount} online · ${esc(u.tier)} · ${esc(bw)}</div>
      </div>
      ${rows}
    </div>`;
  }).join("");
}
</script>
</body>
</html>
"""
