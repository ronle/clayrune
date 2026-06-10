"""Local (LAN) passcode gate — blueprint 1.1 (MODERNIZATION_PLAN.md Phase 1).

Moved VERBATIM from server.py. The dashboard binds 0.0.0.0:PORT, so any device
on the same network can reach it directly at http://<host-ip>:PORT. Remote
access through the Cloudflare tunnel sits behind CF Access (email OTP), but
direct LAN hits had NO auth at all — anyone on the Wi-Fi got full control.
This gate closes that gap:

  • Loopback (this machine) and CF-tunneled requests are ALWAYS exempt. The
    tunnel terminates at cloudflared on localhost, so tunneled traffic both
    arrives as 127.0.0.1 AND carries Cf-Access-* headers — and it has already
    passed CF Access OTP. We never double-gate it.
  • Every other origin (a real LAN IP) must pass a shared passcode. Until a
    passcode is set the dashboard is LOCKED to LAN devices, which instead see
    a one-time "set a passcode" page; once set, they see a login page.

remote_addr is the real TCP peer (we deliberately do NOT trust X-Forwarded-For
here — a LAN attacker could forge XFF: 127.0.0.1, but cannot forge the TCP
source and still complete the handshake). Storage lives in data/ (NOT
data/projects/), so load_projects() never sees it.

The before_request gate body lives here as local_auth_gate(); the handler
itself stays registered on `app` in server.py (a thin wrapper), per the plan.
"""

import json
import time as _time
from pathlib import Path
from typing import Callable

from flask import Blueprint, jsonify, redirect, request

from mc.core import _harden_secret_perms, _is_loopback_request, _log

bp = Blueprint('local_auth', __name__)

# ── wired by server.py (see wire()) ──────────────────────────────────────────
# LOCAL_AUTH_PATH derives from _DATA_ROOT, which still lives in server.py;
# _is_cf_tunneled_request is remote-family (CF JWT machinery) and migrates to
# mc/blueprints/remote.py at step 1.7. Until those extractions land, server.py
# injects both via wire() before registering the blueprint. Annotated with
# their wired types; the None defaults are import-time-only (wire() runs
# before the first request can touch them).
LOCAL_AUTH_PATH: Path = None  # type: ignore[assignment]
_is_cf_tunneled_request: Callable[[], bool] = None  # type: ignore[assignment]

_LOCAL_AUTH_COOKIE = 'mc_local_auth'
_LOCAL_AUTH_MAX_AGE = 30 * 86400  # cookie + signature validity (30 days)
_LOCAL_AUTH_MIN_LEN = 4

# Light in-memory brute-force throttle (per source IP). Best-effort; resets on
# restart. Not a substitute for a strong passcode, just a speed bump.
_LOCAL_AUTH_FAILS = {}            # ip -> [count, window_start_ts]
_LOCAL_AUTH_FAIL_CAP = 10
_LOCAL_AUTH_FAIL_WINDOW = 300     # seconds


def wire(*, local_auth_path, is_cf_tunneled_request):
    """Late-bind the two cross-family deps. Called once from server.py at
    import, BEFORE app.register_blueprint(bp)."""
    global LOCAL_AUTH_PATH, _is_cf_tunneled_request
    LOCAL_AUTH_PATH = local_auth_path
    _is_cf_tunneled_request = is_cf_tunneled_request


def _load_local_auth() -> dict:
    try:
        if LOCAL_AUTH_PATH.exists():
            return json.loads(LOCAL_AUTH_PATH.read_text(encoding='utf-8')) or {}
    except Exception:
        pass
    return {}


def _save_local_auth(d: dict) -> None:
    try:
        LOCAL_AUTH_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = LOCAL_AUTH_PATH.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(d, indent=2), encoding='utf-8')
        tmp.replace(LOCAL_AUTH_PATH)
        _harden_secret_perms(LOCAL_AUTH_PATH)
    except Exception as e:
        _log(f"[local-auth] save failed: {e}", flush=True)


def _local_auth_is_configured() -> bool:
    d = _load_local_auth()
    return bool(d.get('pw_hash') and d.get('pw_salt'))


def _local_auth_hash(passcode: str, salt: bytes) -> str:
    import hashlib
    return hashlib.pbkdf2_hmac('sha256', passcode.encode('utf-8'), salt, 200_000).hex()


def _local_auth_set_passcode(passcode: str) -> None:
    import secrets
    salt = secrets.token_bytes(16)
    d = _load_local_auth()
    d['pw_salt'] = salt.hex()
    d['pw_hash'] = _local_auth_hash(passcode, salt)
    # Rotate the cookie-signing secret so every existing session is invalidated
    # when the passcode changes.
    d['cookie_secret'] = secrets.token_hex(32)
    d['updated_at'] = int(_time.time())
    _save_local_auth(d)


def _local_auth_verify_passcode(passcode: str) -> bool:
    import hmac as _hmac
    d = _load_local_auth()
    salt_hex, pw_hash = d.get('pw_salt'), d.get('pw_hash')
    if not salt_hex or not pw_hash:
        return False
    try:
        salt = bytes.fromhex(salt_hex)
    except Exception:
        return False
    return _hmac.compare_digest(_local_auth_hash(passcode, salt), pw_hash)


def _local_auth_make_cookie() -> str:
    import hmac as _hmac, hashlib
    secret = (_load_local_auth().get('cookie_secret') or '').encode('utf-8')
    iat = str(int(_time.time()))
    sig = _hmac.new(secret, iat.encode('utf-8'), hashlib.sha256).hexdigest()
    return f"{iat}.{sig}"


def _local_auth_verify_cookie(val: str) -> bool:
    import hmac as _hmac, hashlib
    if not val or '.' not in val:
        return False
    secret = (_load_local_auth().get('cookie_secret') or '')
    if not secret:
        return False
    try:
        iat_str, sig = val.split('.', 1)
        iat = int(iat_str)
    except Exception:
        return False
    expected = _hmac.new(secret.encode('utf-8'), iat_str.encode('utf-8'), hashlib.sha256).hexdigest()
    if not _hmac.compare_digest(expected, sig):
        return False
    return (_time.time() - iat) <= _LOCAL_AUTH_MAX_AGE


def _local_auth_exempt() -> bool:
    """The host machine (loopback) and CF-tunneled requests never see the gate."""
    return _is_loopback_request() or _is_cf_tunneled_request()


def _local_auth_request_ok() -> bool:
    """True iff this request may proceed past the gate (exempt, or carries a
    valid auth cookie against a configured passcode)."""
    if _local_auth_exempt():
        return True
    return _local_auth_is_configured() and _local_auth_verify_cookie(
        request.cookies.get(_LOCAL_AUTH_COOKIE, ''))


def _local_auth_throttled() -> bool:
    rec = _LOCAL_AUTH_FAILS.get(request.remote_addr or '?')
    if not rec:
        return False
    if _time.time() - rec[1] > _LOCAL_AUTH_FAIL_WINDOW:
        _LOCAL_AUTH_FAILS.pop(request.remote_addr or '?', None)
        return False
    return rec[0] >= _LOCAL_AUTH_FAIL_CAP


def _local_auth_note_fail() -> None:
    ip = request.remote_addr or '?'
    now = _time.time()
    rec = _LOCAL_AUTH_FAILS.get(ip)
    if not rec or now - rec[1] > _LOCAL_AUTH_FAIL_WINDOW:
        _LOCAL_AUTH_FAILS[ip] = [1, now]
    else:
        rec[0] += 1


def _local_auth_set_cookie(resp):
    resp.set_cookie(_LOCAL_AUTH_COOKIE, _local_auth_make_cookie(),
                    max_age=_LOCAL_AUTH_MAX_AGE, httponly=True, samesite='Lax', path='/')
    return resp


def local_auth_gate():
    """before_request body — registered on `app` by server.py's thin wrapper."""
    # OPTIONS preflight carries no cookies and must not be redirected.
    if request.method == 'OPTIONS':
        return None
    if _local_auth_request_ok():
        return None
    path = request.path or '/'
    # The auth pages + their API + favicon must stay reachable while locked.
    if (path.startswith('/api/local-auth/')
            or path == '/_mc/local-locked'
            or path == '/_mc/local-login'
            or path == '/favicon.ico'):
        return None
    # When a passcode exists → login page. When none is set, a LAN device gets
    # an informational "locked" page (NOT a setup form) — it can never bootstrap
    # a passcode; only the host (exempt) can, via Settings.
    state = 'login' if _local_auth_is_configured() else 'locked'
    if path.startswith('/api/'):
        return jsonify({'error': 'auth_required', 'auth_state': state}), 401
    return redirect('/_mc/local-login' if state == 'login' else '/_mc/local-locked', code=302)


@bp.route('/api/local-auth/status', methods=['GET'])
def local_auth_status():
    """Lets the host Settings panel and the lock pages read current state."""
    configured = _local_auth_is_configured()
    return jsonify({
        'configured': configured,
        'exempt': _local_auth_exempt(),
        'authed': _local_auth_request_ok(),
    })


@bp.route('/api/local-auth/set', methods=['POST'])
def local_auth_set():
    """Set or change the LAN passcode.

    The FIRST passcode can be set ONLY from an exempt context — the host
    (loopback) or a CF-tunneled session — via Settings → Network access. A LAN
    device can never bootstrap a passcode on an unprotected dashboard (otherwise
    the first stranger to reach it could claim it). A LAN device may *change* an
    existing passcode only by proving the current one. On success the caller is
    logged in (cookie set)."""
    body = request.get_json(silent=True) or {}
    new_pass = (body.get('passcode') or '').strip()
    if len(new_pass) < _LOCAL_AUTH_MIN_LEN:
        return jsonify({'error': 'passcode_too_short', 'min': _LOCAL_AUTH_MIN_LEN}), 400
    if not _local_auth_exempt():
        if not _local_auth_is_configured():
            # No LAN bootstrapping — the owner sets the first passcode on the host.
            return jsonify({'error': 'setup_requires_host'}), 403
        if not _local_auth_verify_passcode((body.get('current') or '').strip()):
            return jsonify({'error': 'bad_current_passcode'}), 403
    _local_auth_set_passcode(new_pass)
    _log(f"[local-auth] passcode set/changed from {request.remote_addr}", flush=True)
    return _local_auth_set_cookie(jsonify({'ok': True, 'configured': True}))


@bp.route('/api/local-auth/login', methods=['POST'])
def local_auth_login():
    if not _local_auth_is_configured():
        return jsonify({'error': 'not_configured'}), 400
    if _local_auth_throttled():
        return jsonify({'error': 'too_many_attempts'}), 429
    passcode = ((request.get_json(silent=True) or {}).get('passcode') or '').strip()
    if not _local_auth_verify_passcode(passcode):
        _local_auth_note_fail()
        return jsonify({'error': 'bad_passcode'}), 403
    _LOCAL_AUTH_FAILS.pop(request.remote_addr or '?', None)
    return _local_auth_set_cookie(jsonify({'ok': True}))


@bp.route('/_mc/local-locked')
def mc_local_locked_page():
    # If already past the gate, no reason to show the lock page.
    if _local_auth_request_ok():
        return redirect('/', code=302)
    # A passcode exists → the login page is the right place.
    if _local_auth_is_configured():
        return redirect('/_mc/local-login', code=302)
    return _render_local_auth_page('locked')


@bp.route('/_mc/local-login')
def mc_local_login_page():
    if _local_auth_request_ok():
        return redirect('/', code=302)
    # No passcode yet → there's nothing to log in to; show the locked page.
    if not _local_auth_is_configured():
        return redirect('/_mc/local-locked', code=302)
    return _render_local_auth_page('login')


def _render_local_auth_page(mode: str) -> str:
    safe_mode = 'login' if mode == 'login' else 'locked'
    html = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Clayrune — Locked</title>
<style>
  :root { --accent:#e8824a; --bg:#fdfaf6; --fg:#1a1a1a; --muted:#6b6b6b; --border:#e0d8cc; --err:#c0392b; }
  * { box-sizing:border-box; }
  html,body { margin:0; padding:0; min-height:100%; background:var(--bg); color:var(--fg);
              font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',system-ui,sans-serif; }
  .wrap { max-width:440px; margin:0 auto; padding:48px 22px; }
  .logo { font-size:13px; font-weight:700; letter-spacing:.12em; text-transform:uppercase; color:var(--accent); margin-bottom:18px; }
  h1 { font-size:22px; margin:0 0 8px; font-weight:700; }
  p.lead { color:var(--muted); font-size:14px; line-height:1.55; margin:0 0 18px; }
  .card { background:#fff; border:2px solid var(--border); border-radius:14px; padding:18px; }
  label { display:block; font-size:12px; font-weight:600; color:var(--muted); text-transform:uppercase; letter-spacing:.04em; margin:0 0 6px; }
  input { width:100%; padding:12px 14px; font-size:16px; border:2px solid var(--border); border-radius:10px; background:#fff; color:var(--fg); margin-bottom:12px; }
  input:focus { outline:none; border-color:var(--accent); }
  button { width:100%; margin-top:4px; padding:14px; font-size:16px; font-weight:600; background:var(--accent); color:#fff; border:none; border-radius:10px; cursor:pointer; }
  button:disabled { opacity:.5; cursor:not-allowed; }
  .err { color:var(--err); font-size:13px; min-height:18px; margin:8px 0 0; }
  .hint { font-size:12px; color:var(--muted); margin-top:14px; padding:10px 12px; background:#f6f1ea; border-radius:8px; line-height:1.5; }
</style>
</head>
<body>
<div class="wrap">
  <div class="logo">Clayrune</div>
  <div id="root"></div>
</div>
<script>var MODE = "__MODE__";</script>
<script>
(function(){
  var root = document.getElementById('root');
  function setErr(m){ var e=document.getElementById('err'); if(e) e.textContent = m||''; }
  function msgFor(j){
    if(!j) return 'Something went wrong.';
    switch(j.error){
      case 'bad_passcode': return 'Incorrect passcode.';
      case 'passcode_too_short': return 'Passcode must be at least 4 characters.';
      case 'too_many_attempts': return 'Too many attempts — wait a minute and try again.';
      case 'bad_current_passcode': return 'Current passcode is incorrect.';
      case 'not_configured': return 'No passcode is set yet.';
      default: return 'Something went wrong.';
    }
  }
  function bindEnter(){
    Array.prototype.forEach.call(document.querySelectorAll('input'), function(i){
      i.addEventListener('keydown', function(e){ if(e.key==='Enter'){ var b=document.getElementById('go'); if(b) b.click(); }});
    });
  }
  function doLogin(){
    var p1=(document.getElementById('p1').value||'');
    if(!p1){ setErr('Enter your passcode.'); return; }
    var b=document.getElementById('go'); b.disabled=true; setErr('');
    fetch('/api/local-auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({passcode:p1})})
      .then(function(r){ return r.json().then(function(j){ return {ok:r.ok,j:j}; }); })
      .then(function(res){
        if(res.ok){ location.replace('/'); return; }
        b.disabled=false;
        // A passcode was removed on the host since this page loaded → back to locked.
        if(res.j && res.j.error==='not_configured'){ render('locked'); return; }
        setErr(msgFor(res.j));
      })
      .catch(function(){ b.disabled=false; setErr('Network error — try again.'); });
  }
  function render(mode){
    if(mode==='login'){
      root.innerHTML =
        '<h1>Enter passcode</h1>'
      + '<p class="lead">This Clayrune dashboard is protected. Enter the passcode to continue.</p>'
      + '<div class="card">'
      + '<label for="p1">Passcode</label>'
      + '<input id="p1" type="password" autocomplete="current-password" placeholder="Passcode" autofocus>'
      + '<button id="go">Unlock</button>'
      + '<p class="err" id="err"></p>'
      + '</div>';
      document.getElementById('go').onclick = doLogin;
      bindEnter();
    } else {
      // No passcode is set. A network device CANNOT create one here — that would
      // let the first stranger to reach the dashboard claim it. Point them to
      // the host, where the owner sets it in Settings.
      root.innerHTML =
        '<h1>Dashboard locked</h1>'
      + '<p class="lead">This Clayrune dashboard is not open to your network yet. The owner needs to set a passcode on the host computer &mdash; open Clayrune there and go to <b>Settings &rarr; Connectivity &rarr; Network access</b>. Once a passcode is set, you can sign in here.</p>'
      + '<div class="card">'
      + '<button id="go">Try again</button>'
      + '</div>';
      document.getElementById('go').onclick = function(){ location.reload(); };
    }
  }
  render(MODE);
})();
</script>
</body>
</html>"""
    return html.replace('__MODE__', safe_mode)
