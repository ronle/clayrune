"""Remote Access (Mission Control Cloud) — blueprint 1.7 (MODERNIZATION_PLAN.md).

Moved VERBATIM from server.py: 16 routes — the 12 /api/remote/* routes, the
2 device-label pages (GET /_mc/name-device + POST /api/_mc/session-label),
and the 2 mc-tunnel / enrollment-browser integration points
(/api/tunnel-handshake + /api/mc-callback). The plan table said
"12 /api/remote + /_mc"; the tunnel/enrollment endpoints are the same family
(fixed integration points for the mc_remote provider — same section, same
_get_remote_provider dependency). Also moved: the mc_remote_iface
provider-discovery glue (was top-of-server.py; the registry is only read at
request/loop time, so importing it here is equivalent), the session-labels
store, the CF Access JWT machinery (_is_cf_tunneled_request, _cf_jwt_verified,
_cf_session_nonce_from_request), the unnamed-session label enforcer + its
daemon loop (thread still started by server.py startup via an inbound shim),
and the body of the _redirect_unlabeled_cf_session before_request hook (thin
wrapper stays registered on `app` in server.py at the same source position).

_ENFORCER_STATE / _enforcer_lock live in mc/state.py (Phase 0); CONFIG reads
go through state.CONFIG (the live alias). The MC_REMOTE_LOCAL_MOCK dev-only
mock control plane STAYS in server.py — it mocks the cloud CP, not this
family, and registers conditionally on an env flag. Phase 2: the enforcer
daemon loop gains obs.heartbeat('session-label-enforcer').
"""

import json
import os
import sys
import time as _time
from pathlib import Path

from flask import Blueprint, Response, jsonify, redirect, request

from mc import obs, state
from mc.core import _is_loopback_request, _log
from mc.state import _ENFORCER_STATE, _enforcer_lock

# ── Remote-access provider discovery ────────────────────────────────────────
# Open-source contract (`mc_remote_iface`) is always imported. The proprietary
# provider (`mc_remote`) auto-registers at import time IF installed alongside.
# This lets MC core run cleanly with or without remote-access bundled.
# See `docs/remote-access/07-licensing.md` §4.
try:
    import mc_remote_iface  # noqa: F401  (import for side-effect: surface available)
except Exception as _e:
    mc_remote_iface = None  # type: ignore[assignment]
    # NOTE: print(), not _log — kept from this block's original top-of-
    # server.py position (it ran before CONFIG loaded). Moves-only (1.7).
    print(f"[remote-access] mc_remote_iface not available: {_e}", flush=True)

if mc_remote_iface is not None:
    # Dev stub takes precedence when its env var is set — useful for UI work
    # without standing up the full proprietary provider. Real builds for end
    # users never have this set.
    _dev_stub_active = bool(os.environ.get("MC_DEV_REMOTE_STUB"))
    if _dev_stub_active:
        try:
            from mc_remote_iface.dev_stub import maybe_register_dev_stub
            if maybe_register_dev_stub():
                print(f"[remote-access] dev stub registered "
                      f"(MC_DEV_REMOTE_STUB={os.environ.get('MC_DEV_REMOTE_STUB')})", flush=True)
        except Exception as _e:
            print(f"[remote-access] dev stub unavailable: {_e}", flush=True)
    else:
        try:
            import mc_remote  # noqa: F401  (provider self-registers via __init__)
        except Exception as _e:
            # Absence is normal in an open-source build with no proprietary
            # provider installed. Log at info volume only.
            print(f"[remote-access] no provider installed: {_e}", flush=True)

bp = Blueprint('remote_routes', __name__)

# ── wired by server.py (see wire()) ──────────────────────────────────────────────
SESSION_LABELS_PATH: Path = None  # type: ignore[assignment]


def wire(*, session_labels_path):
    """Late-bind the _DATA_ROOT-derived path constant (1.6 lesson: module-level
    path constants become wired placeholders set by server.py)."""
    global SESSION_LABELS_PATH
    SESSION_LABELS_PATH = session_labels_path


# ─────────────────────────────────────────────────────────────────────────────
# Remote Access (Mission Control Cloud)
# ─────────────────────────────────────────────────────────────────────────────
# Thin Flask layer over whatever RemoteAccessProvider has registered itself
# via mc_remote_iface. Open-source-safe: if no provider is installed, every
# /api/remote/* endpoint returns 200 with `provider: null` (status) or 501
# (action endpoints). The frontend's Settings panel handles either.
#
# See `docs/remote-access/07-licensing.md` §4 for the open-core contract.

def _get_remote_provider():
    """Return the registered RemoteAccessProvider, or None."""
    if mc_remote_iface is None:
        return None
    try:
        return mc_remote_iface.get_provider()
    except Exception:
        return None


def _provider_status_dict(p):
    """Convert ProviderStatus dataclass → dict for JSON response."""
    s = p.status()
    caps = p.get_caps()
    return {
        'provider': {
            'name': p.name,
            'vendor_url': p.vendor_url,
        },
        'enrolled': s.enrolled,
        'online': s.online,
        'connecting': getattr(s, 'connecting', False),
        'hostname': s.hostname,
        'username': s.username,
        'last_seen': s.last_seen,
        'error_code': s.error_code,
        'error_message': s.error_message,
        'caps': None if caps is None else {
            'bandwidth_quota_period_bytes': caps.bandwidth_quota_period_bytes,
            'bandwidth_used_period_bytes': caps.bandwidth_used_period_bytes,
            'rate_limit_rps': caps.rate_limit_rps,
            'max_response_bytes': caps.max_response_bytes,
            'max_concurrent_connections': caps.max_concurrent_connections,
        },
    }



# ── Per-CF-session "name this device" labels ────────────────────────────────
# When a browser/phone signs in via CF Access OTP, the first request through
# the tunnel is intercepted (see `_redirect_unlabeled_cf_session` below) and
# routed to `/_mc/name-device`. The user picks a friendly name; we store
# `{nonce → {label, ua, created_at}}` keyed by the CF Access session nonce.
# `/api/remote/sessions` then enriches CP sessions with the label for that
# nonce. CF Access doesn't expose user_agent or the device name itself, so
# this is the only way to give sessions human-meaningful identifiers.

# SESSION_LABELS_PATH is wired by server.py (_DATA_ROOT-derived — see wire()).


def _load_session_labels() -> dict:
    try:
        with open(SESSION_LABELS_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_session_labels(d: dict) -> None:
    SESSION_LABELS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = SESSION_LABELS_PATH.with_suffix('.json.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f, indent=2, ensure_ascii=False)
    os.replace(tmp, SESSION_LABELS_PATH)


def _set_session_label(nonce: str, label: str, ua: str) -> None:
    if not nonce:
        return
    d = _load_session_labels()
    existing = d.get(nonce, {}) if isinstance(d.get(nonce), dict) else {}
    d[nonce] = {
        'label': label[:80],
        'ua': (ua or '')[:300],
        'created_at': existing.get('created_at') or int(_time.time()),
        'updated_at': int(_time.time()),
    }
    _save_session_labels(d)


def _cf_session_nonce_from_request() -> str:
    """Best-effort extraction of the CF Access session nonce.

    Reads the `Cf-Access-Jwt-Assertion` header (preferred) or the
    `CF_Authorization` cookie. We base64-decode the JWT payload without
    verifying the signature — the tunnel itself is the auth boundary in our
    threat model (anyone reaching this MC instance has already passed CF
    Access OTP). Returns '' if absent or unparseable.
    """
    jwt_str = request.headers.get('Cf-Access-Jwt-Assertion', '') or request.cookies.get('CF_Authorization', '')
    if not jwt_str or jwt_str.count('.') < 2:
        return ''
    try:
        import base64
        payload_b64 = jwt_str.split('.')[1]
        # base64url, may need padding
        padding = '=' * ((4 - len(payload_b64) % 4) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64 + padding))
        return str(payload.get('nonce') or payload.get('identity_nonce') or '')
    except Exception:
        return ''


_CF_JWKS_CACHE: dict = {'ts': 0, 'keys': {}}  # mixed ts/keys values — annotated for pyright (1.7)


def _cf_jwks_key(team: str, kid):
    """Return the JWK dict for `kid` from the CF Access team certs, cached 1h."""
    import time as _t
    import urllib.request
    now = int(_t.time())
    if kid and kid in _CF_JWKS_CACHE['keys'] and now - _CF_JWKS_CACHE['ts'] < 3600:
        return _CF_JWKS_CACHE['keys'][kid]
    with urllib.request.urlopen(f"https://{team}/cdn-cgi/access/certs", timeout=5) as r:
        data = json.loads(r.read().decode('utf-8'))
    _CF_JWKS_CACHE['keys'] = {k.get('kid'): k for k in data.get('keys', []) if k.get('kid')}
    _CF_JWKS_CACHE['ts'] = now
    return _CF_JWKS_CACHE['keys'].get(kid)


def _cf_jwt_verified(jwt_str: str):
    """Verify a Cf-Access-Jwt-Assertion (RS256 sig + aud + exp) against the CF
    Access team certs. Returns True/False ONLY when verification is configured
    (CF_ACCESS_TEAM_DOMAIN + CF_ACCESS_AUD env) and a token is present; returns
    None when unconfigured or unverifiable (network/parse error) so the caller
    falls back to the loopback-gated trust — a JWKS hiccup must never lock out
    remote access. A bad signature/claim returns False (reject)."""
    team = (os.environ.get('CF_ACCESS_TEAM_DOMAIN') or '').strip().rstrip('/')
    aud = (os.environ.get('CF_ACCESS_AUD') or '').strip()
    if not team or not aud or not jwt_str or jwt_str.count('.') != 2:
        return None
    try:
        import base64, time as _t
        from cryptography.hazmat.primitives.asymmetric import padding, rsa

        def _b64u(s):
            return base64.urlsafe_b64decode(s + '=' * ((4 - len(s) % 4) % 4))

        header_b64, payload_b64, sig_b64 = jwt_str.split('.')
        header = json.loads(_b64u(header_b64))
        payload = json.loads(_b64u(payload_b64))
        if header.get('alg') != 'RS256':
            return False
        token_aud = payload.get('aud')
        if aud not in (token_aud if isinstance(token_aud, list) else [token_aud]):
            return False
        if int(payload.get('exp', 0)) < int(_t.time()):
            return False
        jwk = _cf_jwks_key(team, header.get('kid'))
        if not jwk:
            return None  # couldn't fetch keys → fail-open, don't lock out
        pub = rsa.RSAPublicNumbers(
            int.from_bytes(_b64u(jwk['e']), 'big'),
            int.from_bytes(_b64u(jwk['n']), 'big'),
        ).public_key()
        signing_input = (header_b64 + '.' + payload_b64).encode('ascii')
    except Exception:
        return None  # setup/parse/network error → fail-open to the loopback gate
    # Signature check kept separate so an INVALID signature → False (reject),
    # not None (which would fall back to trusting the header).
    try:
        from cryptography.hazmat.primitives import hashes as _hashes
        pub.verify(_b64u(sig_b64), signing_input, padding.PKCS1v15(), _hashes.SHA256())
        return True
    except Exception:
        return False


def _is_cf_tunneled_request() -> bool:
    """True iff this request arrived through CF Access (i.e. via the tunnel).

    cloudflared runs on THIS host and forwards to the origin over loopback, so a
    genuine tunneled request both (a) carries Cf-Access-* headers AND (b) has a
    loopback TCP peer. We require BOTH. The headers alone are attacker-forgeable:
    the origin binds 0.0.0.0, so any LAN device can send Cf-Access-* directly —
    but a forged header from a LAN IP fails the loopback-peer check and is
    correctly treated as un-exempt (it must then pass the LAN passcode). If you
    run cloudflared on a SEPARATE host, set a LAN passcode: tunnel traffic then
    arrives from a non-loopback peer and is intentionally no longer auto-exempt.

    When CF_ACCESS_TEAM_DOMAIN + CF_ACCESS_AUD are set, the assertion JWT is also
    signature-verified (defense-in-depth); a provably-forged token is rejected.
    """
    if not _is_loopback_request():
        return False
    jwt_str = request.headers.get('Cf-Access-Jwt-Assertion', '')
    if jwt_str and _cf_jwt_verified(jwt_str) is False:
        return False
    return bool(request.headers.get('Cf-Access-Authenticated-User-Email')
                or request.headers.get('Cf-Access-Jwt-Assertion'))


# Body of the _redirect_unlabeled_cf_session before_request hook — the thin
# wrapper stays registered on `app` in server.py at the same source position
# (after _local_auth_gate; hook order unchanged).
def redirect_unlabeled_cf_session():
    """If a tunneled request lacks a stored label for its CF nonce, send the
    user to the name-device page. Skips API/static/the page itself.
    """
    if not _is_cf_tunneled_request():
        return None
    path = request.path or '/'
    # Don't redirect API, static, or the name-device page itself (and its POST endpoint).
    # `/sw.js` is the PWA service worker — must always be fetchable without
    # 302-redirect; otherwise SW registration silently fails and the page
    # never qualifies as installable.
    if (path.startswith('/api/')
            or path.startswith('/static/')
            or path.startswith('/_mc/')
            or path == '/favicon.ico'
            or path == '/sw.js'
            or path == '/manifest.json'):
        return None
    nonce = _cf_session_nonce_from_request()
    if not nonce:
        return None  # nothing to key on; let the request through
    labels = _load_session_labels()
    if nonce in labels and (labels[nonce] or {}).get('label'):
        return None  # already named
    return redirect('/_mc/name-device', code=302)


@bp.route('/_mc/name-device')
def mc_name_device_page():
    """Serve the 'name this device' form. Pre-fills detected platform/browser
    from the User-Agent so the user sees what we detected.
    """
    ua = request.headers.get('User-Agent', '')
    nonce = _cf_session_nonce_from_request()
    email = request.headers.get('Cf-Access-Authenticated-User-Email', '')
    # Render a tiny standalone HTML page (no dependency on the SPA bundle).
    html = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Name this device</title>
  <style>
    :root { --accent: #e8824a; --bg: #fdfaf6; --fg: #1a1a1a; --muted: #6b6b6b; --border: #e0d8cc; }
    * { box-sizing: border-box; }
    html, body { margin: 0; padding: 0; height: 100%; background: var(--bg); color: var(--fg); font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif; }
    .wrap { max-width: 440px; margin: 0 auto; padding: 36px 22px; }
    h1 { font-size: 22px; margin: 0 0 8px; font-weight: 700; }
    p.lead { color: var(--muted); font-size: 14px; line-height: 1.5; margin: 0 0 18px; }
    .card { background: white; border: 2px solid var(--border); border-radius: 14px; padding: 18px; }
    label { display: block; font-size: 12px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: .04em; margin-bottom: 6px; }
    input { width: 100%; padding: 12px 14px; font-size: 16px; border: 2px solid var(--border); border-radius: 10px; background: white; color: var(--fg); }
    input:focus { outline: none; border-color: var(--accent); }
    .detected { font-size: 12px; color: var(--muted); margin: 14px 0 0; padding: 10px 12px; background: #f6f1ea; border-radius: 8px; word-break: break-word; }
    .detected b { color: var(--fg); }
    button { width: 100%; margin-top: 16px; padding: 14px; font-size: 16px; font-weight: 600; background: var(--accent); color: white; border: none; border-radius: 10px; cursor: pointer; }
    button:disabled { opacity: .5; cursor: not-allowed; }
    button:hover:not(:disabled) { filter: brightness(1.05); }
    .err { color: #c0392b; font-size: 13px; margin-top: 10px; min-height: 1em; }
    .footer { text-align: center; font-size: 11px; color: var(--muted); margin-top: 18px; }
    .suggest-row { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
    .suggest { font-size: 12px; padding: 5px 10px; background: #f6f1ea; border: 1px solid var(--border); border-radius: 999px; cursor: pointer; }
    .suggest:hover { background: #efe5d6; }
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Name this device</h1>
    <p class="lead">So you can tell your sessions apart later. Sign-in expires in 24 hours.</p>
    <div class="card">
      <label for="nm">Device name</label>
      <input id="nm" autofocus placeholder="e.g. My iPhone" maxlength="80" />
      <div class="suggest-row" id="suggest"></div>
      <div class="detected">Detected: <b id="det"></b><br><span id="email" style="font-size:11px;opacity:.75"></span></div>
      <button id="go" disabled>Continue</button>
      <div class="err" id="err"></div>
    </div>
    <div class="footer">Clayrune · Cloudflare Access</div>
  </div>
<script>
const NONCE = __NONCE__;
const UA    = __UA__;
const EMAIL = __EMAIL__;

function brief(ua) {
  let b='Browser', os='';
  if (/Edg\\//.test(ua)) b='Edge';
  else if (/CriOS/.test(ua)) b='Chrome';
  else if (/FxiOS/.test(ua)) b='Firefox';
  else if (/Chrome\\//.test(ua)) b='Chrome';
  else if (/Firefox\\//.test(ua)) b='Firefox';
  else if (/Safari\\//.test(ua)) b='Safari';
  if (/iPhone/.test(ua)) os='iPhone';
  else if (/iPad/.test(ua)) os='iPad';
  else if (/Android/.test(ua)) os='Android';
  else if (/Windows/.test(ua)) os='Windows';
  else if (/Mac OS X|Macintosh/.test(ua)) os='Mac';
  else if (/Linux/.test(ua)) os='Linux';
  return os ? b+' on '+os : b;
}

const detEl = document.getElementById('det');
const emailEl = document.getElementById('email');
detEl.textContent = brief(UA || navigator.userAgent);
emailEl.textContent = EMAIL;

// Suggestion chips
const ua = (UA || navigator.userAgent);
const sugs = [];
if (/iPhone/.test(ua))    sugs.push('My iPhone');
if (/iPad/.test(ua))      sugs.push('My iPad');
if (/Android/.test(ua))   { sugs.push('My Phone'); sugs.push('My Android'); }
if (/Windows/.test(ua))   sugs.push('Windows PC');
if (/Mac OS X|Macintosh/.test(ua)) sugs.push('My Mac');
sugs.push('Work Laptop'); sugs.push('Home PC');
const sugRow = document.getElementById('suggest');
sugs.slice(0,4).forEach(s => {
  const b = document.createElement('button');
  b.type = 'button'; b.className = 'suggest'; b.textContent = s;
  b.onclick = () => { document.getElementById('nm').value = s; checkBtn(); };
  sugRow.appendChild(b);
});

const inp = document.getElementById('nm');
const btn = document.getElementById('go');
const err = document.getElementById('err');
function checkBtn() { btn.disabled = !inp.value.trim(); }
inp.addEventListener('input', checkBtn);
inp.addEventListener('keydown', e => { if (e.key === 'Enter' && !btn.disabled) submit(); });
btn.addEventListener('click', submit);

async function submit() {
  const label = inp.value.trim();
  if (!label) return;
  btn.disabled = true; err.textContent = '';
  try {
    const r = await fetch('/api/_mc/session-label', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ nonce: NONCE, label }),
    });
    const j = await r.json();
    if (r.ok && j.ok) {
      // Remember the chosen name so re-OTPs (new nonce, same device) can
      // auto-submit without showing this page again.
      try { localStorage.setItem('mc_device_name', label); } catch (_) {}
      window.location.href = '/';
    } else {
      err.textContent = j.message || ('Could not save (' + r.status + ')');
      btn.disabled = false;
    }
  } catch (e) {
    err.textContent = 'Network error: ' + e;
    btn.disabled = false;
  }
}

// Auto-submit on re-OTP: if this device has labeled itself before in a
// previous CF session (different nonce, same browser+device → same
// localStorage), silently re-label the new nonce and continue.
(function autoSubmitIfRemembered() {
  try {
    const remembered = localStorage.getItem('mc_device_name');
    if (!remembered || !remembered.trim()) return;
    if (!NONCE) return;
    inp.value = remembered;
    // Hide the form so the user doesn't see a flash; show a tiny "Reconnecting…"
    document.querySelector('.card').innerHTML =
      '<div style="font-size:14px;color:#6b6b6b;padding:24px;text-align:center">'
      + 'Recognized this device as <b>' + remembered.replace(/[<>&]/g,'') + '</b>.<br>Reconnecting…</div>';
    submit();
  } catch (_) {}
})();
</script>
</body>
</html>
"""
    html = (html
            .replace('__NONCE__', json.dumps(nonce))
            .replace('__UA__',    json.dumps(ua))
            .replace('__EMAIL__', json.dumps(email)))
    resp = Response(html, mimetype='text/html; charset=utf-8')
    resp.headers['Cache-Control'] = 'no-store'
    return resp


@bp.route('/api/_mc/session-label', methods=['POST'])
def mc_set_session_label():
    """Record `{nonce → label}`. Only accepts requests that came through CF Access."""
    if not _is_cf_tunneled_request():
        return jsonify({'ok': False, 'message': 'Not a tunneled request'}), 403
    body = request.get_json(silent=True) or {}
    nonce = (body.get('nonce') or '').strip() or _cf_session_nonce_from_request()
    label = (body.get('label') or '').strip()
    if not nonce:
        return jsonify({'ok': False, 'message': 'No CF session nonce'}), 400
    if not label:
        return jsonify({'ok': False, 'message': 'Label required'}), 400
    ua = request.headers.get('User-Agent', '')
    _set_session_label(nonce, label, ua)
    return jsonify({'ok': True, 'nonce': nonce, 'label': label})


# ── Auto-revoke unnamed sessions ────────────────────────────────────────────
# Background loop: every interval, lists CF Access sessions; for any session
# whose nonce isn't in `session_labels.json` AND is older than the threshold,
# calls per-session revoke (strict mode — no fallback to revoke-all). Keeps
# the sessions UI tidy: sessions that didn't go through the name-device flow
# get cleaned up automatically. Named sessions are never touched.

# _ENFORCER_STATE / _enforcer_lock moved to mc/state.py (Phase 0).


def _enforce_session_labels_once(force: bool = False) -> dict:
    """One pass of the label enforcer. Returns a small status dict.

    Called by the daemon loop on a timer + by a manual `/api/remote/sessions/enforce`
    endpoint. Idempotent.
    """
    cfg = state.CONFIG  # already loaded (live alias, mc/state.py)
    enabled = bool(cfg.get('auto_revoke_unnamed_sessions', True))
    threshold = int(cfg.get('auto_revoke_unnamed_after_seconds', 600))
    if not enabled and not force:
        return {'ok': True, 'skipped': 'disabled'}

    p = _get_remote_provider()
    if p is None:
        return {'ok': True, 'skipped': 'no_provider'}

    try:
        from mc_remote import enrollment as _mc_enrollment, config as _mc_config
    except Exception as e:
        return {'ok': False, 'error': f'import_error: {e}'}

    auth_kwargs, err = _cp_auth_kwargs(empty_resp_field='sessions')
    if err is not None:
        return {'ok': True, 'skipped': err.get('error', 'no_auth')}

    cp_url = _mc_config.control_plane_base_url()

    try:
        body = _mc_enrollment.list_sessions_via_cp(cp_base_url=cp_url, **auth_kwargs)
    except Exception as e:
        return {'ok': False, 'error': f'list_failed: {e}'}

    if not isinstance(body, dict) or not isinstance(body.get('sessions'), list):
        return {'ok': True, 'skipped': 'no_sessions_response'}
    if body.get('error'):
        return {'ok': True, 'skipped': f'cp_error:{body.get("error")}'}

    labels = _load_session_labels()
    now = int(_time.time())
    revoked = []
    skipped_unsupported = []
    for s in body['sessions']:
        nonce = s.get('nonce') or ''
        sid = s.get('session_id') or ''
        issued = s.get('issued_at') or 0
        if not sid or not nonce:
            continue
        is_labeled = nonce in labels and (labels[nonce] or {}).get('label')
        if is_labeled:
            continue
        age = now - int(issued) if issued else 0
        if age < threshold and not force:
            continue
        # Strict revoke — no fallback to revoke-all. If CF doesn't support
        # per-session revoke for this account, we abort rather than nuking
        # the user's labeled sessions.
        try:
            r = _mc_enrollment.revoke_session_via_cp(
                cp_base_url=cp_url, session_id=sid, strict=True, **auth_kwargs,
            )
            if r.get('ok') and r.get('scope') == 'session':
                revoked.append({'nonce': nonce, 'short_id': s.get('short_id', '')})
                _ENFORCER_STATE['last_per_session_supported'] = True
            elif r.get('error') == 'per_session_unsupported' or r.get('status') == 503:
                # CF doesn't support per-session for this token. Stop trying.
                _ENFORCER_STATE['last_per_session_supported'] = False
                skipped_unsupported.append(nonce)
                break
            else:
                skipped_unsupported.append(nonce)
        except Exception as e:
            _ENFORCER_STATE['last_error'] = f'revoke_failed: {e}'

    _ENFORCER_STATE['last_run'] = now
    _ENFORCER_STATE['last_revoked_count'] = len(revoked)
    _ENFORCER_STATE['last_skipped_count'] = len(skipped_unsupported)
    if revoked:
        _log(f"[remote-access] auto-revoked {len(revoked)} unnamed session(s): "
              f"{[r['short_id'] for r in revoked]}", flush=True)
    return {
        'ok': True,
        'revoked': revoked,
        'skipped_unsupported': skipped_unsupported,
        'per_session_supported': _ENFORCER_STATE['last_per_session_supported'],
    }


def _warmup_control_plane():
    """Fire one GET /v1/health at the configured CP base URL.

    Cloud Run with min-instances=0 cold-starts in 2-5s; without warmup, the
    user's first click pays that latency. Hitting /health on MC startup means
    the CP is already warm by the time anyone clicks anything.
    """
    try:
        from mc_remote import config as _mc_config
    except Exception:
        return  # provider not installed — nothing to warm
    try:
        base = _mc_config.control_plane_base_url()
    except Exception:
        return
    if not base:
        return
    url = f"{base.rstrip('/')}/health"
    try:
        import requests
        t0 = _time.monotonic()
        r = requests.get(url, timeout=15)
        dt_ms = int((_time.monotonic() - t0) * 1000)
        _log(f"[remote-access] CP warmup {url} -> {r.status_code} in {dt_ms}ms", flush=True)
    except Exception as e:
        _log(f"[remote-access] CP warmup failed (will not retry): {e}", flush=True)


def _session_label_enforcer_loop():
    """Daemon thread: run the enforcer every N seconds."""
    interval = max(30, int(state.CONFIG.get('auto_revoke_check_interval_seconds', 60)))
    while True:
        obs.heartbeat('session-label-enforcer')  # Phase 2: loop liveness -> /api/system/loops
        try:
            with _enforcer_lock:
                _enforce_session_labels_once()
        except Exception as e:
            _log(f"[remote-access] enforcer crashed: {e}", flush=True)
            _ENFORCER_STATE['last_error'] = str(e)
        _time.sleep(interval)


@bp.route('/api/remote/sessions/enforce', methods=['POST'])
def remote_sessions_enforce():
    """Manually trigger the unnamed-session cleanup. Returns what was revoked."""
    with _enforcer_lock:
        body = _enforce_session_labels_once(force=True)
    body['state'] = dict(_ENFORCER_STATE)
    return jsonify(body)


@bp.route('/api/remote/sessions/enforcer-state')
def remote_sessions_enforcer_state():
    """Read-only view of the last enforcer run for the Settings panel."""
    return jsonify(dict(_ENFORCER_STATE))


@bp.route('/api/remote/status')
def remote_status():
    """Status of the registered remote-access provider, or `provider: null`.

    Polled by the Settings panel. Cheap; safe to hit every few seconds.
    """
    p = _get_remote_provider()
    if p is None:
        return jsonify({'provider': None})
    try:
        return jsonify(_provider_status_dict(p))
    except Exception as e:
        return jsonify({
            'provider': {'name': getattr(p, 'name', 'Unknown'),
                         'vendor_url': getattr(p, 'vendor_url', '')},
            'enrolled': False,
            'online': False,
            'error_code': 'internal_error',
            'error_message': f'Provider status() failed: {e}',
        }), 200


@bp.route('/api/remote/enable', methods=['POST'])
def remote_enable():
    """Begin enrollment. Launches the OS browser server-side and also returns
    the URL so the frontend can fall back to a manual-copy display.

    Server-side launch (via Python's webbrowser module) is required because
    Tauri / WebView2 silently blocks `window.open()` calls that aren't
    direct user-gesture navigations.
    """
    p = _get_remote_provider()
    if p is None:
        return jsonify({'error': 'no_provider'}), 501
    try:
        url = p.begin_enrollment()
    except NotImplementedError as e:
        return jsonify({'error': 'not_implemented', 'message': str(e)}), 501
    except Exception as e:
        return jsonify({'error': 'internal_error', 'message': str(e)}), 500

    # Some providers (notably the dev stub) signal "no real browser needed —
    # we're done already" by returning a `data:` URL or a URL with the
    # `mc-no-browser` query flag. Skip the launch in those cases.
    skip_browser = (
        url.startswith('data:')
        or url.startswith('mc://')
        or 'mc-no-browser=1' in url
    )

    launched = False if skip_browser else _launch_browser_for_user(url)

    return jsonify({
        'ok': True,
        'enrollment_url': url,
        'launched': launched,
        'skip_browser': skip_browser,
    })


def _launch_browser_for_user(url: str) -> bool:
    """Open `url` in the user's default browser. Returns True on success.

    Windows: os.startfile(url) → ShellExecuteW(open). Most reliable across
    elevation contexts, Tauri-spawned subprocesses, and headless services.

    macOS / Linux: subprocess.Popen of `open` / `xdg-open` respectively.
    """
    try:
        if sys.platform.startswith("win"):
            os.startfile(url)  # type: ignore[attr-defined]
            return True
        if sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", url], close_fds=True)
            return True
        # Linux / BSD
        import subprocess
        subprocess.Popen(["xdg-open", url], close_fds=True)
        return True
    except Exception as e:
        _log(f"[remote-access] _launch_browser_for_user failed: {e}", flush=True)
        return False


@bp.route('/api/remote/disable', methods=['POST'])
def remote_disable():
    """Stop the tunnel. Keeps credentials so re-enable is fast."""
    p = _get_remote_provider()
    if p is None:
        return jsonify({'error': 'no_provider'}), 501
    try:
        p.disable()
    except NotImplementedError as e:
        return jsonify({'error': 'not_implemented', 'message': str(e)}), 501
    except Exception as e:
        return jsonify({'error': 'internal_error', 'message': str(e)}), 500
    return jsonify({'ok': True})


@bp.route('/api/remote/resume', methods=['POST'])
def remote_resume():
    """Reverse of /api/remote/disable: restart the tunnel for an already-enrolled
    device. No re-enrollment, no new keypair, no new CF resources.
    """
    p = _get_remote_provider()
    if p is None:
        return jsonify({'error': 'no_provider'}), 501
    try:
        p.resume()
    except NotImplementedError as e:
        return jsonify({'error': 'not_implemented', 'message': str(e)}), 501
    except RuntimeError as e:
        # e.g. "Cannot resume: no enrolled device."
        return jsonify({'error': 'not_enrolled', 'message': str(e)}), 409
    except Exception as e:
        return jsonify({'error': 'internal_error', 'message': str(e)}), 500
    return jsonify({'ok': True})


def _cp_auth_kwargs(empty_resp_field: str = "devices") -> tuple[dict, dict | None]:
    """Build the auth kwargs for `*_via_cp` calls.

    Prefers device-token auth from the local keystore (post-Firebase
    enrollment). Falls back to MC_REMOTE_DEV_EMAIL env (dev-shim only).
    Returns (kwargs, error_response). When error_response is not None, the
    caller should jsonify+return it directly (covers no-provider / no-auth).
    """
    try:
        from mc_remote import device_keys
    except Exception as e:
        return {}, {'error': 'import_error', 'message': str(e), empty_resp_field: []}
    kwargs: dict = {}
    try:
        identity = device_keys.load_identity()
    except Exception:
        identity = None
    if identity:
        kwargs['device_id'] = identity.device_id
        kwargs['enrollment_token'] = identity.enrollment_token
        return kwargs, None
    # Fall back to dev shim
    email = os.environ.get('MC_REMOTE_DEV_EMAIL', '').strip()
    if email:
        kwargs['email'] = email
        return kwargs, None
    return {}, {'error': 'not_enrolled',
                'message': 'No device keystore + no MC_REMOTE_DEV_EMAIL fallback. Click Enable Remote Access first.',
                empty_resp_field: []}


@bp.route('/api/remote/devices')
def remote_devices():
    """Proxy GET /v1/devices on the configured CP for the authenticated user.

    Auth: device-token from keystore (post-Firebase) preferred; falls back to
    MC_REMOTE_DEV_EMAIL (dev shim) if no keystore identity.
    """
    p = _get_remote_provider()
    if p is None:
        return jsonify({'error': 'no_provider', 'devices': []}), 501
    try:
        from mc_remote import enrollment as _mc_enrollment, device_keys, config
    except Exception as e:
        return jsonify({'error': 'import_error', 'message': str(e), 'devices': []}), 500

    auth_kwargs, err = _cp_auth_kwargs(empty_resp_field='devices')
    if err is not None:
        return jsonify(err), 503

    try:
        identity = device_keys.load_identity()
    except Exception:
        identity = None
    this_device_id = identity.device_id if identity else None

    body = _mc_enrollment.list_devices_via_cp(
        cp_base_url=config.control_plane_base_url(),
        this_device_id=this_device_id,
        **auth_kwargs,
    )
    return jsonify(body)


@bp.route('/api/remote/sessions')
def remote_sessions():
    """Proxy GET /v1/sessions on the configured CP for the authenticated user."""
    p = _get_remote_provider()
    if p is None:
        return jsonify({'error': 'no_provider', 'sessions': []}), 501
    try:
        from mc_remote import enrollment as _mc_enrollment, config
    except Exception as e:
        return jsonify({'error': 'import_error', 'message': str(e), 'sessions': []}), 500
    auth_kwargs, err = _cp_auth_kwargs(empty_resp_field='sessions')
    if err is not None:
        return jsonify(err), 503
    body = _mc_enrollment.list_sessions_via_cp(
        cp_base_url=config.control_plane_base_url(),
        **auth_kwargs,
    )
    # Enrich each session with its locally-stored device label (if any).
    # Match by full nonce; fall back to short_id if CP is on an older version.
    try:
        if isinstance(body, dict) and isinstance(body.get('sessions'), list):
            labels = _load_session_labels()
            short_index = {n[-6:]: lab for n, lab in labels.items() if isinstance(lab, dict) and n}
            for s in body['sessions']:
                nonce = s.get('nonce') or ''
                lab = labels.get(nonce) if nonce else None
                if not lab:
                    lab = short_index.get(s.get('short_id') or '')
                if isinstance(lab, dict) and lab.get('label'):
                    s['label'] = lab.get('label')
                    s['ua'] = lab.get('ua') or ''
    except Exception as _e:
        _log(f"[remote-access] session label enrichment failed: {_e}", flush=True)
    return jsonify(body)


@bp.route('/api/remote/sessions/<session_id>/label', methods=['POST'])
def remote_session_label(session_id):
    """Retroactively label any CF Access session by full session_id.

    Local-only endpoint (called by the desktop dashboard); does NOT require
    a CF Access tunneled request the way `/api/_mc/session-label` does.
    Extracts the nonce from the trailing `_sessions_<nonce>` suffix of the
    session_id (CF's canonical name format).
    """
    body = request.get_json(silent=True) or {}
    label = (body.get('label') or '').strip()
    if not label:
        return jsonify({'ok': False, 'message': 'Label required'}), 400
    # session_id format: <account>_<user>_sessions_<nonce>
    marker = '_sessions_'
    idx = session_id.rfind(marker)
    if idx < 0:
        return jsonify({'ok': False, 'message': 'Could not parse nonce from session_id'}), 400
    nonce = session_id[idx + len(marker):]
    if not nonce:
        return jsonify({'ok': False, 'message': 'Empty nonce'}), 400
    _set_session_label(nonce, label, '')  # no UA available retroactively
    return jsonify({'ok': True, 'nonce': nonce, 'label': label})


@bp.route('/api/remote/sessions/<session_id>/revoke', methods=['POST'])
def remote_session_revoke(session_id):
    p = _get_remote_provider()
    if p is None:
        return jsonify({'error': 'no_provider'}), 501
    try:
        from mc_remote import enrollment as _mc_enrollment, config
    except Exception as e:
        return jsonify({'error': 'import_error', 'message': str(e)}), 500
    auth_kwargs, err = _cp_auth_kwargs(empty_resp_field='sessions')
    if err is not None:
        return jsonify(err), 503
    body = _mc_enrollment.revoke_session_via_cp(
        cp_base_url=config.control_plane_base_url(),
        session_id=session_id,
        **auth_kwargs,
    )
    return jsonify(body)


@bp.route('/api/remote/sessions/revoke-all', methods=['POST'])
def remote_sessions_revoke_all():
    p = _get_remote_provider()
    if p is None:
        return jsonify({'error': 'no_provider'}), 501
    try:
        from mc_remote import enrollment as _mc_enrollment, config
    except Exception as e:
        return jsonify({'error': 'import_error', 'message': str(e)}), 500
    auth_kwargs, err = _cp_auth_kwargs(empty_resp_field='sessions')
    if err is not None:
        return jsonify(err), 503
    body = _mc_enrollment.revoke_all_sessions_via_cp(
        cp_base_url=config.control_plane_base_url(),
        **auth_kwargs,
    )
    return jsonify(body)


@bp.route('/api/remote/disconnect', methods=['POST'])
def remote_disconnect():
    """Revoke this device on the platform; clear local credentials."""
    p = _get_remote_provider()
    if p is None:
        return jsonify({'error': 'no_provider'}), 501
    try:
        p.disconnect_this_device()
    except NotImplementedError as e:
        return jsonify({'error': 'not_implemented', 'message': str(e)}), 501
    except Exception as e:
        return jsonify({'error': 'internal_error', 'message': str(e)}), 500
    return jsonify({'ok': True})


# ── Endpoints called by mc-tunnel and the enrollment browser flow ────────────
# These exist so the proprietary provider has fixed integration points it can
# rely on. Until a real provider is wired in, both return placeholder responses.

@bp.route('/api/tunnel-handshake')
def tunnel_handshake():
    """Localhost handshake from `mc-tunnel`. See attestation protocol §5.2.

    The proprietary provider, when wired up, replaces this handler with one
    that verifies the shared secret and returns the device challenge JSON.
    Without a provider, returns 503 so `mc-tunnel` exits cleanly.
    """
    p = _get_remote_provider()
    if p is None:
        return jsonify({'error': 'no_provider', 'remote_access_enabled': False}), 503
    # Provider hasn't installed a custom handler yet — placeholder until wired.
    return jsonify({'error': 'not_implemented'}), 501


def _mc_callback_html(title: str, body: str, *, status: int = 200, accent: str = "#10b981") -> Response:
    """Render the friendly post-enrollment page shown to the user's browser."""
    safe_title = title.replace("<", "&lt;").replace(">", "&gt;")
    return Response(
        f"""<!doctype html>
<html><head><meta charset='utf-8'><title>Clayrune</title>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
          background: #fafaf7; color: #1f2937; margin: 0; min-height: 100vh;
          display: flex; align-items: center; justify-content: center; padding: 24px; }}
  .card {{ background: #fff; border-radius: 16px; box-shadow: 0 1px 3px rgba(0,0,0,.06), 0 8px 24px rgba(0,0,0,.04);
           padding: 40px 32px; max-width: 480px; width: 100%; text-align: center; }}
  .badge {{ width: 56px; height: 56px; border-radius: 14px; background: {accent}22;
            color: {accent}; display: inline-flex; align-items: center; justify-content: center;
            font-size: 28px; margin-bottom: 20px; border: 2px solid {accent}55; }}
  h1 {{ font-size: 22px; margin: 0 0 8px; font-weight: 700; }}
  p {{ font-size: 15px; line-height: 1.55; color: #4b5563; margin: 0 0 14px; }}
  .hint {{ font-size: 13px; color: #6b7280; margin-top: 20px; padding-top: 16px;
           border-top: 1px solid #f0eee8; }}
</style></head>
<body><div class='card'>
  <div class='badge'>{'✓' if status == 200 else '!'}</div>
  <h1>{safe_title}</h1>
  {body}
  <p class='hint'>You can close this window and return to Clayrune.</p>
</div></body></html>""",
        status=status,
        mimetype='text/html; charset=utf-8',
    )


@bp.route('/api/mc-callback')
def mc_callback():
    """Browser redirect target at the end of enrollment.

    Calls the registered provider's enrollment.complete() with the query
    params from the control plane. Renders a friendly success/failure page.
    See `02-attestation-protocol.md` §6.1 step 7.
    """
    p = _get_remote_provider()
    if p is None:
        return _mc_callback_html(
            "Remote access isn't available",
            "<p>Clayrune Remote Access isn't installed in this build.</p>",
            status=404, accent="#9ca3af",
        )

    # The proprietary provider's enrollment module owns this validation.
    # We ask the provider for it via a dunder-ish hook so MC core stays
    # provider-agnostic. If the provider doesn't expose one, fall back
    # to the canonical mc_remote.enrollment.complete().
    try:
        from mc_remote import enrollment as _mc_enrollment  # type: ignore
    except Exception as e:
        return _mc_callback_html(
            "Remote access isn't fully wired yet",
            f"<p>Couldn't reach the enrollment module ({e}).</p>",
            status=500, accent="#ef4444",
        )

    result = _mc_enrollment.complete(request.args.to_dict(flat=True))  # pyright: ignore[reportArgumentType]  # moved-verbatim typing debt (1.7): werkzeug stubs lack the flat=True overload

    if result.get("ok"):
        identity = result["identity"]
        host = identity.hostname
        return _mc_callback_html(
            "You're connected!",
            f"<p>Your Clayrune dashboard is reachable from anywhere at:</p>"
            f"<p style='font-family:JetBrains Mono,Consolas,monospace;font-size:14px;color:#1f2937;"
            f"background:#f3f4f6;padding:10px 14px;border-radius:8px;display:inline-block'>"
            f"https://{host}</p>",
        )

    return _mc_callback_html(
        "Sign-in didn't complete",
        f"<p>{result.get('message', 'Unknown error')}</p>"
        f"<p style='font-size:12px;color:#9ca3af'>Code: {result.get('error', 'unknown')}</p>",
        status=400, accent="#ef4444",
    )
