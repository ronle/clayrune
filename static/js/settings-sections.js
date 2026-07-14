// ── Local network access (LAN passcode) ─────────────────────────────────────
// The dashboard binds 0.0.0.0, so any device on the same network can reach it.
// Loopback (this PC) and tunneled (CF Access) traffic is always exempt; this
// section lets the host set / change the passcode that gates direct LAN access.
// Locked by default: until a passcode is set, LAN devices see a setup page.
// Backend: /api/local-auth/{status,set,login}.
window._localAuthState = window._localAuthState || null;

async function fetchLocalAuthStatus() {
  try {
    const r = await fetch(API_BASE + '/api/local-auth/status');
    if (!r.ok) return null;
    return await r.json();
  } catch (_) { return null; }
}

async function refreshLocalAccessSection() {
  window._localAuthState = await fetchLocalAuthStatus();
  const el = document.getElementById('local-access-section');
  if (el) el.outerHTML = localAccessSettingsHTML();
  // outerHTML replacement drops the settings-hidden class; re-assert it so a
  // single-sub-item detail screen stays correct.
  try { _applySettingsSectionVisibility(); } catch (_) {}
}

function localAccessSettingsHTML() {
  const st = window._localAuthState;
  const configured = !!(st && st.configured);
  const pill = configured
    ? `<span style="font-size:11px;font-weight:600;padding:3px 8px;border-radius:8px;background:#10b98122;color:#059669;border:1px solid #10b98155;letter-spacing:.3px;margin-left:8px">On</span>`
    : `<span style="font-size:11px;font-weight:600;padding:3px 8px;border-radius:8px;background:#f59e0b22;color:#d97706;border:1px solid #f59e0b55;letter-spacing:.3px;margin-left:8px">Set a passcode</span>`;
  let body;
  if (!configured) {
    body = `
      <div class="settings-hint" style="margin-bottom:10px;line-height:1.45">
        No passcode is set yet, so devices on your local network are locked out
        (they can't create one — only you can, here). Set one to let them sign
        in. This computer is always exempt, and remote access through the secure
        tunnel keeps its own sign-in.
      </div>
      <div id="local-auth-form-row">
        <button class="btn-dispatch" onclick="showLocalAuthForm('set')">Set a passcode</button>
      </div>`;
  } else {
    body = `
      <div class="settings-hint" style="margin-bottom:10px;line-height:1.45">
        Devices on your local network must enter the passcode to open this
        dashboard. This computer is always exempt.
      </div>
      <div id="local-auth-form-row">
        <button class="btn-dispatch" onclick="showLocalAuthForm('change')">Change passcode</button>
      </div>`;
  }
  return `<div class="settings-section" id="local-access-section">
    <div class="settings-section-title">Network access${pill}</div>
    ${body}
  </div>`;
}

function showLocalAuthForm(mode) {
  const row = document.getElementById('local-auth-form-row');
  if (!row) return;
  const st = window._localAuthState || {};
  // The host (loopback) and tunneled callers are exempt and don't need the
  // current passcode to change it; an authed LAN device does.
  const needCurrent = (mode === 'change') && st.configured && !st.exempt;
  row.innerHTML = `
    ${needCurrent ? `<input class="settings-input" id="la-cur" type="password" placeholder="Current passcode" autocomplete="current-password" style="margin-bottom:8px">` : ''}
    <input class="settings-input" id="la-p1" type="password" placeholder="New passcode (at least 4 characters)" autocomplete="new-password" style="margin-bottom:8px">
    <input class="settings-input" id="la-p2" type="password" placeholder="Confirm passcode" autocomplete="new-password" style="margin-bottom:8px">
    <div style="display:flex;gap:8px">
      <button class="btn-dispatch" onclick="submitLocalAuth('${mode}')">Save passcode</button>
      <button class="btn-dispatch" style="background:var(--surface3);border-color:var(--border2);color:var(--text)" onclick="refreshLocalAccessSection()">Cancel</button>
    </div>
    <div class="settings-hint" id="la-err" style="color:var(--red,#c0392b);min-height:16px;margin-top:6px"></div>`;
  const f = document.getElementById('la-p1'); if (f) f.focus();
}

async function submitLocalAuth(mode) {
  const p1 = (document.getElementById('la-p1') || {}).value || '';
  const p2 = (document.getElementById('la-p2') || {}).value || '';
  const curEl = document.getElementById('la-cur');
  const err = document.getElementById('la-err');
  const setErr = (m) => { if (err) err.textContent = m || ''; };
  if (p1.length < 4) { setErr('Passcode must be at least 4 characters.'); return; }
  if (p1 !== p2) { setErr('Passcodes do not match.'); return; }
  const payload = { passcode: p1 };
  if (curEl) payload.current = curEl.value || '';
  try {
    const r = await fetch(API_BASE + '/api/local-auth/set', {
      method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(payload)
    });
    const j = await r.json().catch(() => ({}));
    if (r.ok) {
      showToast(mode === 'change' ? 'Passcode changed' : 'Passcode set — local network access is now protected');
      refreshLocalAccessSection();
    } else {
      setErr(j.error === 'bad_current_passcode' ? 'Current passcode is incorrect.'
           : j.error === 'passcode_too_short' ? 'Passcode must be at least 4 characters.'
           : j.error === 'setup_requires_host' ? 'The first passcode must be set on the host computer.'
           : 'Could not save passcode.');
    }
  } catch (_) { setErr('Network error — try again.'); }
}

// ── Remote Access ────────────────────────────────────────────────────────────
// Three states, served by /api/remote/status:
//   1. No provider installed     → "COMING SOON" CTA
//   2. Provider installed, not enrolled → "Enable Remote Access" button (live)
//   3. Provider installed, enrolled     → hostname + status pill + manage controls
//
// Status is fetched on each settings render and cached at window._remoteState.
// See docs/remote-access/07-licensing.md §4 for the open-core split.

window._remoteState = window._remoteState || null;

// Robust copy-to-clipboard with fallback for Tauri/WebView2.
// `navigator.clipboard.writeText()` returns a Promise that can silently
// reject in WebView2 (no user-gesture detected, sandboxed contexts, etc.).
// We try it first, fall back to a hidden-textarea + execCommand copy on
// failure or if navigator.clipboard isn't available.
function copyToClipboardSafe(text, toastOk = 'Copied', toastFail = null) {
  function _legacy() {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;top:0;left:0;width:1px;height:1px;opacity:0;pointer-events:none';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    let ok = false;
    try { ok = document.execCommand('copy'); } catch(_) { ok = false; }
    document.body.removeChild(ta);
    if (ok) {
      if (toastOk) showToast(toastOk);
    } else {
      showToast(toastFail || ('Could not copy. URL: ' + text), 6000);
    }
  }
  if (window.isSecureContext === false || !navigator.clipboard || !navigator.clipboard.writeText) {
    _legacy();
    return;
  }
  navigator.clipboard.writeText(text)
    .then(() => { if (toastOk) showToast(toastOk); })
    .catch(_legacy);
}

async function fetchRemoteStatus() {
  try {
    const r = await fetch(API_BASE + '/api/remote/status');
    if (!r.ok) return null;
    return await r.json();
  } catch (_) { return null; }
}

async function refreshRemoteAccessSection() {
  window._remoteState = await fetchRemoteStatus();
  const el = document.getElementById('remote-access-section');
  if (el) el.outerHTML = remoteAccessSettingsHTML();
  // outerHTML replacement drops the fresh section's settings-hidden class; if a
  // settings detail screen is showing only one sub-item, re-assert that.
  try { _applySettingsSectionVisibility(); } catch (_) {}

  // If we landed in the enrolled state, populate the devices list async.
  // (refreshRemoteDevices is a no-op when the #remote-devices-list anchor
  // isn't present, so safe to call unconditionally.)
  if (window._remoteState && window._remoteState.enrolled) {
    refreshRemoteDevices();
    refreshRemoteSessions();
  }

  // Self-recovery: if we landed in a transient "connecting" state, schedule
  // a follow-up poll so the panel doesn't get stuck if the user looks at it
  // mid-transition. Stops polling once we hit a stable state. Caps at ~30s.
  const st = window._remoteState;
  if (st && st.connecting) {
    if (!window._remoteAutoPoll) window._remoteAutoPoll = Date.now();
    if (Date.now() - window._remoteAutoPoll < 30000) {
      setTimeout(() => {
        if (document.getElementById('remote-access-section')) {
          refreshRemoteAccessSection();
        }
      }, 2000);
    } else {
      // 30s elapsed without resolution; stop polling. User can refresh manually.
      window._remoteAutoPoll = null;
    }
  } else {
    // Stable state — reset the auto-poll timer for next time
    window._remoteAutoPoll = null;
  }
}

function _ra_pill(state) {
  // small status pill HTML
  const styles = {
    online:  'background:#10b98122;color:#059669;border:1px solid #10b98155',
    offline: 'background:#9ca3af22;color:#6b7280;border:1px solid #9ca3af55',
    error:   'background:#ef444422;color:#dc2626;border:1px solid #ef444455',
    pending: 'background:#f59e0b22;color:#d97706;border:1px solid #f59e0b55',
  };
  const s = styles[state] || styles.offline;
  const label = state === 'online' ? 'Online'
              : state === 'offline' ? 'Offline'
              : state === 'error' ? 'Error'
              : state === 'pending' ? 'Connecting…' : state;
  return `<span style="font-size:11px;font-weight:600;padding:3px 8px;border-radius:8px;${s};letter-spacing:.3px">${esc(label)}</span>`;
}

function _ra_section_open(extras = '') {
  return `<div class="settings-section" id="remote-access-section">
    <div class="settings-section-title">Remote Access${extras}</div>`;
}

function remoteAccessSettingsHTML() {
  const st = window._remoteState;

  // ─── State 1: no provider installed ─────────────────────────────────
  if (!st || !st.provider) {
    const badge = `<span style="font-size:10px;font-weight:600;padding:2px 6px;border-radius:6px;background:var(--accent-dim);color:var(--accent);margin-left:6px;letter-spacing:.5px">COMING SOON</span>`;
    return _ra_section_open(badge) + `
      <div class="settings-hint" style="margin-bottom:10px;line-height:1.45">
        Reach this Clayrune dashboard from outside your home network &mdash;
        from your phone, a laptop on the road, anywhere. Sign in once at
        <span style="font-family:'JetBrains Mono','Courier New',monospace">clayrune.io</span>
        and your dashboard becomes available at
        <span style="font-family:'JetBrains Mono','Courier New',monospace">&lt;your-name&gt;.clayrune.io</span>.
      </div>
      <div class="settings-row">
        <div>
          <div class="settings-label">Clayrune Remote Access</div>
          <div class="settings-hint">No remote access provider installed yet. Available in a future update.</div>
        </div>
        <button class="btn-dispatch" disabled style="opacity:.55;cursor:not-allowed" title="Not yet available">Enable Remote Access</button>
      </div>
      <div class="settings-row" style="border-top:1px dashed var(--border-soft,rgba(0,0,0,.08));padding-top:10px;margin-top:8px">
        <div>
          <div class="settings-label" style="font-size:12px;color:var(--text-soft,#888)">Want to use a different provider?</div>
          <div class="settings-hint">
            Clayrune is open source. Forks can ship their own remote-access
            provider (Tailscale, ngrok, custom) by implementing the
            <span style="font-family:'JetBrains Mono','Courier New',monospace">mc_remote_iface</span>
            interface.
          </div>
        </div>
      </div>
    </div>`;
  }

  const providerName = esc(st.provider.name || 'Remote Access');

  // ─── State 2: provider installed, not enrolled ─────────────────────
  if (!st.enrolled) {
    return _ra_section_open() + `
      <div class="settings-hint" style="margin-bottom:10px;line-height:1.45">
        Reach this Clayrune dashboard from outside your home network using
        <strong>${providerName}</strong>. Sign in once and your dashboard becomes available at
        <span style="font-family:'JetBrains Mono','Courier New',monospace">&lt;your-name&gt;.clayrune.io</span>.
      </div>
      ${st.error_code ? `
        <div class="settings-hint" style="background:#ef444411;border-left:3px solid #ef4444;padding:8px 12px;margin-bottom:10px;color:#b91c1c">
          ${esc(st.error_message || st.error_code)}
        </div>` : ''}
      <div class="settings-row">
        <div>
          <div class="settings-label">${providerName}</div>
          <div class="settings-hint">Click to sign in and connect this device.</div>
        </div>
        <button class="btn-dispatch" onclick="enableRemoteAccess()">Enable Remote Access</button>
      </div>
    </div>`;
  }

  // ─── State 3: provider installed, enrolled ─────────────────────────
  const pillState = st.online ? 'online'
                  : (st.error_code ? 'error'
                  : (st.connecting ? 'pending' : 'offline'));
  const hostname = esc(st.hostname || '');
  const username = esc(st.username || '');
  const lastSeen = st.last_seen ? `Last seen ${esc(timeAgoShort(st.last_seen))}` : '';

  let capsRow = '';
  if (st.caps && st.caps.bandwidth_quota_period_bytes) {
    const used = st.caps.bandwidth_used_period_bytes || 0;
    const cap = st.caps.bandwidth_quota_period_bytes;
    const pct = Math.min(100, Math.round((used / cap) * 100));
    const fmt = b => b > 1e9 ? `${(b/1e9).toFixed(2)} GB` : b > 1e6 ? `${(b/1e6).toFixed(0)} MB` : `${(b/1e3).toFixed(0)} KB`;
    capsRow = `
      <div class="settings-row">
        <div>
          <div class="settings-label" style="font-size:12px">Bandwidth this period</div>
          <div class="settings-hint">${fmt(used)} of ${fmt(cap)} used</div>
        </div>
        <div style="flex:1;max-width:200px;height:6px;background:var(--border-soft,rgba(0,0,0,.08));border-radius:3px;overflow:hidden;margin-left:16px">
          <div style="width:${pct}%;height:100%;background:${pct>90?'#ef4444':pct>70?'#f59e0b':'#10b981'}"></div>
        </div>
      </div>`;
  }

  return _ra_section_open() + `
    <div class="settings-row" style="align-items:flex-start">
      <div style="flex:1">
        <div class="settings-label" style="display:flex;align-items:center;gap:8px">
          ${providerName} ${_ra_pill(pillState)}
        </div>
        <div class="settings-hint" style="margin-top:4px">
          <span style="font-family:'JetBrains Mono','Courier New',monospace;font-size:13px;color:var(--text)">${hostname}</span>
          ${username ? ` &middot; signed in as <strong>${username}</strong>` : ''}
        </div>
        ${lastSeen ? `<div class="settings-hint" style="font-size:11px;margin-top:2px">${lastSeen}</div>` : ''}
        ${st.error_code ? `
          <div class="settings-hint" style="background:#ef444411;border-left:3px solid #ef4444;padding:8px 12px;margin-top:8px;color:#b91c1c">
            ${esc(st.error_message || st.error_code)}
          </div>` : ''}
        ${(!st.online && !st.error_code && !st.connecting) ? `
          <div class="settings-hint" style="background:#fef3c7;border-left:3px solid #f59e0b;padding:10px 12px;margin-top:10px;color:#92400e;line-height:1.5">
            <strong>Tunnel paused.</strong> The dashboard isn't reachable from outside your network right now. Click <strong>Resume</strong> to reconnect.
          </div>` : ''}
        ${(!st.online && !st.error_code && st.connecting) ? `
          <div class="settings-hint" style="background:#dbeafe;border-left:3px solid #2563eb;padding:10px 12px;margin-top:10px;color:#1e3a8a;line-height:1.5">
            <strong>Reconnecting…</strong> Bringing the tunnel back up. This usually takes a few seconds.
          </div>` : ''}
      </div>
      <div style="display:flex;flex-direction:column;gap:6px;margin-left:12px">
        ${(st.online && hostname)
          ? `<button class="btn-dispatch" onclick="copyToClipboardSafe('https://${hostname}','Link copied')" style="font-size:12px;padding:6px 10px">Copy link</button>`
          : (hostname ? `<button class="btn-dispatch" disabled title="The tunnel isn't connected yet — the link won't work until it is" style="font-size:12px;padding:6px 10px;opacity:.5;cursor:not-allowed">Copy link</button>` : '')}
        ${st.online
          ? `<button class="btn-dispatch" onclick="disableRemoteAccess()" style="font-size:12px;padding:6px 10px;background:transparent;border:1px solid var(--border)">Pause</button>`
          : (st.connecting
              ? `<button class="btn-dispatch" disabled style="font-size:12px;padding:6px 10px;opacity:.5;cursor:wait">Connecting…</button>`
              : `<button class="btn-dispatch" onclick="resumeRemoteAccess()" style="font-size:12px;padding:6px 10px">Resume</button>`)}
      </div>
    </div>
    ${capsRow}
    <div id="remote-devices-list" style="border-top:1px dashed var(--border-soft,rgba(0,0,0,.08));padding-top:10px;margin-top:8px">
      <!-- populated async by refreshRemoteDevices() -->
    </div>
    <div id="remote-sessions-list" style="border-top:1px dashed var(--border-soft,rgba(0,0,0,.08));padding-top:10px;margin-top:8px">
      <!-- populated async by refreshRemoteSessions() -->
    </div>
    <div class="settings-row" style="border-top:1px dashed var(--border-soft,rgba(0,0,0,.08));padding-top:10px;margin-top:8px">
      <div>
        <div class="settings-label" style="font-size:12px">Disconnect this device</div>
        <div class="settings-hint">Revokes access from the platform. You'll need to sign in again to reconnect.</div>
      </div>
      <button class="btn-dispatch" onclick="disconnectRemoteAccess()" style="font-size:12px;padding:6px 10px;background:transparent;border:1px solid #ef4444;color:#dc2626">Disconnect</button>
    </div>
  </div>`;
}

// ── Web push notifications ───────────────────────────────────────────────────
// Subscribes the current browser via the service worker + VAPID public key
// from the server, then lists all known subscriptions (this device + others
// signed in via clayrune.io). Per-device toggles for "agent push"
// (PushNotification tool calls) and "turn complete" (end-of-turn signal).

window._pushState = window._pushState || {
  supported: null,        // null until detect runs
  permission: '',         // Notification.permission snapshot
  publicKey: '',          // VAPID public key (b64url)
  thisDeviceEndpoint: '', // PushSubscription.endpoint for this browser
  subs: [],               // /api/push/subscriptions response
  swState: 'unknown',     // service worker state for /sw.js
  swError: '',            // last register error message
  swScript: '',           // active worker scriptURL
};

// ── PWA install: capture Chrome's beforeinstallprompt event for a manual CTA.
window._deferredInstallPrompt = null;
window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  window._deferredInstallPrompt = e;
  // If the settings panel is open, re-render so the Install row updates.
  if (document.getElementById('push-section')) {
    try { refreshPushSection(); } catch (_) {}
  }
});
window.addEventListener('appinstalled', () => {
  window._deferredInstallPrompt = null;
  showToast('Clayrune installed. Launch from your home screen.');
  if (document.getElementById('push-section')) {
    try { refreshPushSection(); } catch (_) {}
  }
});
function _isPwaInstalled() {
  // Best-effort: 'standalone' display-mode is set inside an installed PWA window.
  // iOS Safari exposes navigator.standalone for the legacy A2HS PWA.
  try {
    if (window.matchMedia && window.matchMedia('(display-mode: standalone)').matches) return true;
  } catch (_) {}
  if (typeof navigator !== 'undefined' && navigator.standalone === true) return true;
  return false;
}
async function installPwaApp() {
  const p = window._deferredInstallPrompt;
  if (!p) {
    showToast('Install prompt not available yet — Chrome triggers it after a brief visit. Try again in a few seconds.', 6000);
    return;
  }
  try {
    p.prompt();
    const choice = await p.userChoice;
    if (choice && choice.outcome === 'accepted') {
      // appinstalled listener will fire and show its own toast
    } else {
      showToast('Install dismissed');
    }
  } catch (e) {
    showToast(`Install failed: ${e.message || e}`, 5000);
  } finally {
    window._deferredInstallPrompt = null;
    refreshPushSection();
  }
}

function _pushSupported() {
  return ('serviceWorker' in navigator)
      && ('PushManager' in window)
      && ('Notification' in window);
}

function _b64urlToUint8(b64url) {
  const padding = '='.repeat((4 - b64url.length % 4) % 4);
  const b64 = (b64url + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(b64);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out;
}

function pushNotificationsSettingsHTML() {
  const supported = _pushSupported();
  const st = window._pushState;
  if (!supported) {
    return `
    <div class="settings-section" id="push-section">
      <div class="settings-section-title">Push Notifications</div>
      <div class="settings-hint" style="margin-bottom:10px;line-height:1.45">
        Get notified on your phone or desktop when an agent finishes a long
        task or needs your attention. Powered by Web Push.
      </div>
      <div class="settings-row">
        <div>
          <div class="settings-label">Not supported in this browser</div>
          <div class="settings-hint">This browser doesn't support Web Push (Service Worker + PushManager). Try Chrome, Edge, or Firefox.</div>
        </div>
      </div>
    </div>`;
  }
  const perm = (typeof Notification !== 'undefined') ? Notification.permission : 'default';
  let stateBlock = '';
  if (perm === 'denied') {
    stateBlock = `
      <div class="settings-hint" style="background:#ef444411;border-left:3px solid #ef4444;padding:8px 12px;margin-bottom:10px;color:#b91c1c">
        Notifications are blocked for this site. Open the site permissions
        (lock icon in the URL bar) and allow notifications, then click
        <strong>Enable on this device</strong>.
      </div>`;
  }
  const enableBtn = (perm === 'granted' && st.thisDeviceEndpoint)
    ? `<button class="btn-dispatch" onclick="testPushNotification()" style="font-size:12px;padding:6px 10px">Send test</button>
       <button class="btn-dispatch" onclick="unsubscribeThisDevice()" style="font-size:12px;padding:6px 10px;background:transparent;border:1px solid var(--border)">Disable here</button>`
    : `<button class="btn-dispatch" onclick="enablePushOnThisDevice()" ${perm==='denied'?'disabled':''} style="font-size:12px;padding:6px 10px;${perm==='denied'?'opacity:.5;cursor:not-allowed':''}">Enable on this device</button>`;

  // PWA install row: shows current state (installed / available / not yet
  // eligible). Chrome's `beforeinstallprompt` only fires once criteria are
  // met (manifest + SW + HTTPS + ~30s engagement); the deferred prompt is
  // captured in window._deferredInstallPrompt.
  const installed = _isPwaInstalled();
  const installable = !!window._deferredInstallPrompt;
  let installRow = '';
  if (installed) {
    installRow = `
      <div class="settings-row" style="align-items:flex-start;border-top:1px dashed var(--border-soft,rgba(0,0,0,.08));padding-top:10px;margin-top:8px">
        <div style="flex:1">
          <div class="settings-label">Installed as app <span style="margin-left:6px;font-size:10px;font-weight:600;padding:1px 6px;border-radius:6px;background:#10b98122;color:#059669;border:1px solid #10b98155">&#x2713;</span></div>
          <div class="settings-hint">Notifications are delivered with Clayrune's identity (not flagged as web spam).</div>
        </div>
      </div>`;
  } else if (installable) {
    installRow = `
      <div class="settings-row" style="align-items:flex-start;border-top:1px dashed var(--border-soft,rgba(0,0,0,.08));padding-top:10px;margin-top:8px">
        <div style="flex:1">
          <div class="settings-label">Install Clayrune to home screen</div>
          <div class="settings-hint">Removes the "possible spam" warning on notifications, adds a home-screen icon, and runs in a standalone window. Recommended on mobile.</div>
        </div>
        <button class="btn-dispatch" onclick="installPwaApp()" style="font-size:12px;padding:6px 10px">Install</button>
      </div>`;
  } else {
    installRow = `
      <div class="settings-row" style="align-items:flex-start;border-top:1px dashed var(--border-soft,rgba(0,0,0,.08));padding-top:10px;margin-top:8px">
        <div style="flex:1">
          <div class="settings-label">Install Clayrune to home screen</div>
          <div class="settings-hint">Chrome surfaces the install prompt after a short engagement period. Use Chrome menu &rarr; "Install app" / "Add to Home screen" if it doesn't appear here.</div>
        </div>
      </div>`;
  }

  // Diagnostic row: service-worker status. Lets us see install-readiness
  // from the phone without remote-debug. Green = activated (PWA installable).
  const swState = (window._pushState && window._pushState.swState) || 'unknown';
  const swError = (window._pushState && window._pushState.swError) || '';
  const swGood = (swState === 'activated' || swState === 'activating');
  const swColor = swGood ? '#10b981' : (swState === 'failed' ? '#ef4444' : '#f59e0b');
  const swBg    = swGood ? '#10b98122' : (swState === 'failed' ? '#ef444422' : '#f59e0b22');
  const swDiagRow = `
    <div id="sw-status-line" class="settings-row" style="align-items:flex-start;font-size:11px">
      <div style="flex:1">
        <div class="settings-label" style="font-size:11px">Service worker</div>
        <div class="settings-hint" style="font-size:11px">
          ${swGood
            ? 'Active and controlling this page.'
            : swState === 'failed'
              ? `Failed to register: <code>${esc(swError || 'unknown')}</code>`
              : swState === 'registering' || swState === 'installing'
                ? 'Registering…'
                : 'Not registered yet — Install button won\'t appear until this is green.'}
        </div>
      </div>
      <span style="font-size:10px;font-weight:700;padding:2px 8px;border-radius:8px;background:${swBg};color:${swColor};letter-spacing:.3px;text-transform:uppercase">${esc(swState)}</span>
    </div>`;

  return `
    <div class="settings-section" id="push-section">
      <div class="settings-section-title">Push Notifications</div>
      <div class="settings-hint" style="margin-bottom:10px;line-height:1.45">
        Get notified when an agent calls Claude's <code>PushNotification</code>
        tool, or when a turn completes (opt-in per device). Tap the
        notification to open the dashboard at the originating session.
      </div>
      ${swDiagRow}
      ${stateBlock}
      <div class="settings-row" style="align-items:flex-start">
        <div style="flex:1">
          <div class="settings-label">This browser</div>
          <div class="settings-hint">
            ${(perm === 'granted' && st.thisDeviceEndpoint)
              ? 'Subscribed. You\'ll receive notifications here.'
              : (perm === 'denied'
                  ? 'Permission denied — see banner above.'
                  : 'Click <strong>Enable on this device</strong> to subscribe.')}
          </div>
        </div>
        <div style="display:flex;flex-direction:column;gap:6px;margin-left:12px">${enableBtn}</div>
      </div>
      ${installRow}
      <div id="push-subscriptions-list" style="border-top:1px dashed var(--border-soft,rgba(0,0,0,.08));padding-top:10px;margin-top:8px">
        <div class="settings-hint" style="font-size:11px;opacity:.6;padding:4px 0">Loading subscribed devices…</div>
      </div>
    </div>`;
}

async function refreshPushSection() {
  // Hydrate _pushState then re-render the section.
  if (!_pushSupported()) {
    const el = document.getElementById('push-section');
    if (el) el.outerHTML = pushNotificationsSettingsHTML();
    return;
  }
  window._pushState.permission = (typeof Notification !== 'undefined') ? Notification.permission : 'default';
  try {
    const r = await fetch(API_BASE + '/api/push/vapid-public-key');
    const b = await r.json();
    window._pushState.publicKey = b.public_key || '';
  } catch (_) {}
  try {
    const reg = await navigator.serviceWorker.getRegistration('/');
    if (reg) {
      const sub = await reg.pushManager.getSubscription();
      window._pushState.thisDeviceEndpoint = sub ? sub.endpoint : '';
    } else {
      window._pushState.thisDeviceEndpoint = '';
    }
  } catch (_) {
    window._pushState.thisDeviceEndpoint = '';
  }
  const sectionEl = document.getElementById('push-section');
  if (sectionEl) sectionEl.outerHTML = pushNotificationsSettingsHTML();
  refreshPushDeviceList();
}

async function refreshPushDeviceList() {
  const el = document.getElementById('push-subscriptions-list');
  if (!el) return;
  let body = null;
  try {
    const r = await fetch(API_BASE + '/api/push/subscriptions');
    body = await r.json();
  } catch (_) {
    el.innerHTML = `<div class="settings-hint" style="font-size:11px;color:#dc2626">Couldn't load devices.</div>`;
    return;
  }
  const subs = (body && body.subscriptions) || [];
  window._pushState.subs = subs;
  if (subs.length === 0) {
    el.innerHTML = `<div class="settings-hint" style="font-size:11px">No devices subscribed yet.</div>`;
    return;
  }
  const thisEnd = window._pushState.thisDeviceEndpoint;
  const rows = subs.map(s => {
    const isThis = s.nonce && thisEnd && (window._pushState._subEndpoints && window._pushState._subEndpoints[s.nonce] === thisEnd);
    // Server doesn't expose endpoint in list; match by "this device" only when we
    // happen to know it. Add a fallback: label appended with "(this device)"
    // when refreshing right after a subscribe.
    const thisBadge = (s._isThis || isThis) ? `<span style="font-size:10px;font-weight:600;padding:1px 6px;border-radius:6px;background:var(--accent-dim);color:var(--accent);margin-left:6px">This device</span>` : '';
    const lastSeen = s.last_used_at ? `Last push ${esc(timeAgoShort(s.last_used_at))}` : 'No pushes yet';
    const created = s.created_at ? esc(timeAgoShort(s.created_at)) : '';
    return `
      <div style="padding:8px 0;border-bottom:1px dashed var(--border-soft,rgba(0,0,0,.05))">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:8px">
          <div style="flex:1;min-width:0">
            <div style="font-size:12px;font-weight:600;display:flex;align-items:center;gap:6px">
              ${esc(s.label || 'Device')}${thisBadge}
            </div>
            <div class="settings-hint" style="font-size:11px;margin-top:2px;color:var(--text-faint)">
              ${esc((s.ua || '').slice(0, 80))}
            </div>
            <div class="settings-hint" style="font-size:11px;opacity:.7">${lastSeen}${created ? ` · added ${created}` : ''}</div>
          </div>
          <button class="btn-dispatch" onclick="removePushSubscription('${esc(s.nonce)}')" style="font-size:11px;padding:4px 8px;background:transparent;border:1px solid var(--border)">Remove</button>
        </div>
        <div style="margin-top:6px;display:flex;flex-wrap:wrap;gap:14px;font-size:11px">
          <label style="display:flex;align-items:center;gap:4px;cursor:pointer">
            <input type="checkbox" ${s.notify_agent_push ? 'checked' : ''}
              onchange="updatePushSubscription('${esc(s.nonce)}', 'notify_agent_push', this.checked)"
              style="accent-color:var(--accent)">
            Agent push (PushNotification tool)
          </label>
          <label style="display:flex;align-items:center;gap:4px;cursor:pointer">
            <input type="checkbox" ${s.notify_turn_complete ? 'checked' : ''}
              onchange="updatePushSubscription('${esc(s.nonce)}', 'notify_turn_complete', this.checked)"
              style="accent-color:var(--accent)">
            Turn complete
          </label>
        </div>
      </div>`;
  }).join('');
  el.innerHTML = `
    <div class="settings-label" style="font-size:12px">Subscribed devices</div>
    <div style="margin-top:6px">${rows}</div>`;
}

async function enablePushOnThisDevice() {
  if (!_pushSupported()) {
    showToast('This browser does not support web push', 4000);
    return;
  }
  try {
    let perm = Notification.permission;
    if (perm === 'default') {
      perm = await Notification.requestPermission();
    }
    if (perm !== 'granted') {
      showToast('Notification permission denied', 4000);
      await refreshPushSection();
      return;
    }
    // Make sure we have the VAPID key
    if (!window._pushState.publicKey) {
      const r = await fetch(API_BASE + '/api/push/vapid-public-key');
      const b = await r.json();
      window._pushState.publicKey = b.public_key || '';
    }
    if (!window._pushState.publicKey) {
      showToast('Server did not return a VAPID public key', 4000);
      return;
    }
    const reg = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
    await navigator.serviceWorker.ready;
    let sub = await reg.pushManager.getSubscription();
    if (!sub) {
      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: _b64urlToUint8(window._pushState.publicKey),
      });
    }
    // Try to guess a reasonable device label
    let label = 'Browser';
    try {
      const ua = navigator.userAgent || '';
      const isAndroid = /Android/i.test(ua);
      const isIOS = /iPhone|iPad/i.test(ua);
      const browser = /Edg\//.test(ua) ? 'Edge'
                    : /Chrome\//.test(ua) ? 'Chrome'
                    : /Firefox\//.test(ua) ? 'Firefox'
                    : /Safari\//.test(ua) ? 'Safari' : 'Browser';
      label = `${browser} · ${isAndroid ? 'Android' : isIOS ? 'iOS' : (navigator.platform || 'Desktop')}`;
    } catch (_) {}
    const subJson = sub.toJSON();
    const resp = await fetch(API_BASE + '/api/push/subscribe', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        endpoint: subJson.endpoint,
        keys: subJson.keys,
        label: label,
      }),
    });
    const body = await resp.json();
    if (!body.ok) {
      showToast(`Subscribe failed: ${body.error || resp.status}`, 5000);
      return;
    }
    showToast('Subscribed for push notifications');
    window._pushState.thisDeviceEndpoint = subJson.endpoint;
    await refreshPushSection();
  } catch (e) {
    console.error('[push] enable failed', e);
    showToast(`Push subscribe failed: ${e.message || e}`, 5000);
  }
}

async function unsubscribeThisDevice() {
  try {
    const reg = await navigator.serviceWorker.getRegistration('/');
    if (reg) {
      const sub = await reg.pushManager.getSubscription();
      if (sub) {
        await fetch(API_BASE + '/api/push/unsubscribe', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ endpoint: sub.endpoint }),
        });
        try { await sub.unsubscribe(); } catch (_) {}
      }
    }
    window._pushState.thisDeviceEndpoint = '';
    showToast('Unsubscribed');
    await refreshPushSection();
  } catch (e) {
    showToast(`Unsubscribe failed: ${e.message || e}`, 4000);
  }
}

async function removePushSubscription(nonce) {
  if (!nonce) return;
  // If this is the current device, also tear down the browser subscription.
  try {
    const reg = await navigator.serviceWorker.getRegistration('/');
    if (reg) {
      const sub = await reg.pushManager.getSubscription();
      if (sub && sub.endpoint === window._pushState.thisDeviceEndpoint) {
        try { await sub.unsubscribe(); } catch (_) {}
        window._pushState.thisDeviceEndpoint = '';
      }
    }
  } catch (_) {}
  await fetch(API_BASE + '/api/push/unsubscribe', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ nonce: nonce }),
  });
  showToast('Device removed');
  await refreshPushSection();
}

async function updatePushSubscription(nonce, field, value) {
  try {
    const body = {};
    body[field] = value;
    const r = await fetch(API_BASE + `/api/push/subscription/${encodeURIComponent(nonce)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const b = await r.json();
    if (!b.ok) {
      showToast(`Update failed: ${b.error || r.status}`, 4000);
    }
  } catch (e) {
    showToast(`Update failed: ${e.message || e}`, 4000);
  }
}

async function testPushNotification() {
  try {
    const r = await fetch(API_BASE + '/api/push/test', { method: 'POST' });
    const b = await r.json();
    if (!b.ok) {
      showToast(`Test failed: ${b.error || 'unknown'}`, 6000);
      return;
    }
    if (b.sent > 0) {
      let msg = `Sent test push to ${b.sent} device(s)`;
      if (b.failed > 0) msg += `, ${b.failed} failed`;
      if (b.removed > 0) msg += `, ${b.removed} pruned`;
      showToast(msg, 5000);
    } else if (b.failed > 0) {
      showToast(`Delivery failed (${b.failed}): ${b.last_error || 'unknown error'}`, 8000);
    } else if (b.removed > 0) {
      showToast(`No active subscribers (cleaned ${b.removed} expired)`);
    } else {
      showToast(`No active subscribers`);
    }
  } catch (e) {
    showToast(`Test failed: ${e.message || e}`, 6000);
  }
}

// ── Devices list (calls /api/remote/devices on CP via MC's proxy) ────────────

async function refreshRemoteDevices() {
  const el = document.getElementById('remote-devices-list');
  if (!el) return;
  el.innerHTML = `<div class="settings-hint" style="font-size:11px;opacity:.6;padding:4px 0">Loading devices…</div>`;
  let body = null;
  try {
    const r = await fetch(API_BASE + '/api/remote/devices');
    body = await r.json();
  } catch (e) {
    el.innerHTML = `<div class="settings-hint" style="font-size:11px;color:#dc2626">Couldn't load device list.</div>`;
    return;
  }

  if (body.error) {
    el.innerHTML = `<div class="settings-hint" style="font-size:11px;color:#dc2626">Couldn't load devices: ${esc(body.message || body.error)}</div>`;
    return;
  }

  const devices = body.devices || [];
  const cap = body.device_cap || 2;
  const tier = body.tier || 'free';

  if (devices.length === 0) {
    el.innerHTML = `<div class="settings-hint" style="font-size:11px">No devices enrolled.</div>`;
    return;
  }

  const rows = devices.map(d => {
    const pillCol = d.online ? '#10b981' : '#9ca3af';
    const pillBg  = d.online ? '#10b98122' : '#9ca3af22';
    const pillTxt = d.online ? 'Online' : 'Offline';
    const thisBadge = d.is_this_device ? `<span style="font-size:10px;font-weight:600;padding:1px 6px;border-radius:6px;background:var(--accent-dim);color:var(--accent);margin-left:6px">This device</span>` : '';
    const lastSeen = d.last_seen ? `Last seen ${esc(timeAgoShort(d.last_seen))}` : 'Never seen';
    return `
      <div style="display:flex;align-items:center;justify-content:space-between;padding:6px 0;border-bottom:1px dashed var(--border-soft,rgba(0,0,0,.05))">
        <div style="flex:1;min-width:0">
          <div style="font-size:12px;font-weight:600;display:flex;align-items:center;gap:6px">
            ${esc(d.device_name)}${thisBadge}
            <span style="font-size:10px;font-weight:600;padding:1px 6px;border-radius:6px;background:${pillBg};color:${pillCol};border:1px solid ${pillCol}55">${pillTxt}</span>
          </div>
          <div class="settings-hint" style="font-size:11px;margin-top:2px">
            <span style="font-family:'JetBrains Mono','Courier New',monospace;color:var(--text)">${esc(d.hostname)}</span>
            ${d.os ? ` · ${esc(d.os)}` : ''}
            ${d.mc_version ? ` · v${esc(d.mc_version)}` : ''}
          </div>
          <div class="settings-hint" style="font-size:11px;opacity:.65">${lastSeen}</div>
        </div>
      </div>`;
  }).join('');

  const capLine = `${devices.length} of ${cap} on ${tier} plan`;

  el.innerHTML = `
    <div class="settings-label" style="font-size:12px;display:flex;justify-content:space-between;align-items:center">
      <span>Your devices</span>
      <span class="settings-hint" style="font-size:10px">${esc(capLine)}</span>
    </div>
    <div style="margin-top:6px">${rows}</div>
  `;
}

// ── Active sign-in sessions (browsers, paired phones) ───────────────────────
// Different from devices — a session is "someone is signed in and can open the
// dashboard"; a device is "a machine is running MC behind a tunnel".
//
// These were Cloudflare Access sessions until 2026-07-13. They are now our own
// (control_plane/app/sessions.py) — Access was per-seat priced and had to go.
// Consequences visible here: revoke is genuinely per-session now (CF's was so
// unreliable it fell back to nuking all of them), and the fields changed —
// `created_at` replaces `issued_at`, `kind` replaces `apps_seen`, and there is
// no `short_id`/`ua` (CF never actually returned a user-agent either).

async function refreshRemoteSessions() {
  const el = document.getElementById('remote-sessions-list');
  if (!el) return;
  el.innerHTML = `<div class="settings-hint" style="font-size:11px;opacity:.6;padding:4px 0">Loading sessions…</div>`;
  let body = null;
  try {
    const r = await fetch(API_BASE + '/api/remote/sessions');
    body = await r.json();
  } catch (e) {
    el.innerHTML = '';   // silently hide on network failure
    return;
  }

  if (body.error) {
    el.innerHTML = `<div class="settings-hint" style="font-size:11px;color:#dc2626">Couldn't load sessions: ${esc(body.message || body.error)}</div>`;
    return;
  }

  const sessions = body.sessions || [];

  const headerHtml = `
    <div class="settings-label" style="font-size:12px;display:flex;justify-content:space-between;align-items:center">
      <span>Active sign-in sessions</span>
      <span class="settings-hint" style="font-size:10px">${sessions.length} active</span>
    </div>
    <div class="settings-hint" style="font-size:11px;margin-bottom:6px">
      Browsers and paired phones currently signed in. Different from devices above.
      Signing one out stops it renewing; it loses access within 30 minutes.
    </div>`;

  let listHtml;
  if (sessions.length === 0) {
    listHtml = `<div class="settings-hint" style="font-size:11px;font-style:italic">No active sessions.</div>`;
  } else {
    const toMs = (v) => {
      if (v == null) return null;
      if (typeof v === 'number') return v < 1e12 ? v * 1000 : v;
      const d = new Date(v);
      return isNaN(d.getTime()) ? null : d.getTime();
    };
    const fmtAgo = (ms) => {
      if (ms == null) return '';
      const secs = Math.max(0, Math.floor((Date.now() - ms) / 1000));
      if (secs < 60) return `${secs}s ago`;
      if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
      if (secs < 86400) {
        const h = Math.floor(secs / 3600);
        const m = Math.floor((secs % 3600) / 60);
        return m ? `${h}h ${m}m ago` : `${h}h ago`;
      }
      return `${Math.floor(secs / 86400)}d ago`;
    };
    const fmtIn = (ms) => {
      if (ms == null) return '';
      const secs = Math.max(0, Math.floor((ms - Date.now()) / 1000));
      if (secs < 60) return `in ${secs}s`;
      if (secs < 3600) return `in ${Math.floor(secs / 60)}m`;
      if (secs < 86400) {
        const h = Math.floor(secs / 3600);
        const m = Math.floor((secs % 3600) / 60);
        return m ? `in ${h}h ${m}m` : `in ${h}h`;
      }
      return `in ${Math.floor(secs / 86400)}d`;
    };
    const fmtAbs = (ms) => ms == null ? '' : new Date(ms).toLocaleString();

    const briefUA = (ua) => {
      if (!ua) return '';
      let b = '', os = '';
      if (/Edg\//.test(ua))                  b = 'Edge';
      else if (/CriOS/.test(ua))             b = 'Chrome';
      else if (/FxiOS/.test(ua))             b = 'Firefox';
      else if (/Chrome\//.test(ua))          b = 'Chrome';
      else if (/Firefox\//.test(ua))         b = 'Firefox';
      else if (/Safari\//.test(ua))          b = 'Safari';
      if (/iPhone/.test(ua))                 os = 'iPhone';
      else if (/iPad/.test(ua))              os = 'iPad';
      else if (/Android/.test(ua))           os = 'Android';
      else if (/Windows/.test(ua))           os = 'Windows';
      else if (/Mac OS X|Macintosh/.test(ua)) os = 'Mac';
      else if (/Linux/.test(ua))             os = 'Linux';
      if (b && os) return `${b} on ${os}`;
      return b || os || '';
    };

    listHtml = sessions.map((s, idx) => {
      const issuedMs = toMs(s.created_at);
      const expiresMs = toMs(s.expires_at);
      const ago = issuedMs ? fmtAgo(issuedMs) : '';
      const expIn = expiresMs ? fmtIn(expiresMs) : '';
      const lastMs = toMs(s.last_refresh_at);
      const appsLine = s.kind === 'mobile'
        ? `Paired phone${lastMs ? ` · last active ${fmtAgo(lastMs)}` : ''}`
        : (lastMs ? `Last active ${fmtAgo(lastMs)}` : '');
      const ua = briefUA(s.ua || '');
      const sidEsc = esc(s.session_id);
      // Headline priority: user-typed label > clickable "Name this session…"
      const labelHtml = s.label
        ? `<span>${esc(s.label)}</span> <a href="#" onclick="renameRemoteSession('${sidEsc}', ${JSON.stringify(s.label)}); return false;" style="font-size:10px;font-weight:400;opacity:.5;margin-left:6px;text-decoration:underline">rename</a>`
        : `<a href="#" onclick="renameRemoteSession('${sidEsc}', ''); return false;" style="color:var(--accent);text-decoration:underline">Name this session…</a>`;
      const subParts = [];
      if (ua) subParts.push(esc(ua));
      if (ago) subParts.push(esc(ago));
      if (!s.label && sessions.length > 1) subParts.push(`session #${idx + 1}`);
      const subLine = subParts.join(' · ');
      return `
        <div style="display:flex;align-items:flex-start;justify-content:space-between;padding:6px 0;border-bottom:1px dashed var(--border-soft,rgba(0,0,0,.05));gap:8px">
          <div style="flex:1;min-width:0">
            <div style="font-size:13px;font-weight:600">${labelHtml}</div>
            ${subLine ? `<div class="settings-hint" style="font-size:11px;opacity:.75">${subLine}</div>` : ''}
            ${appsLine ? `<div class="settings-hint" style="font-size:11px;opacity:.6">${esc(appsLine)}</div>` : ''}
            ${issuedMs ? `<div class="settings-hint" style="font-size:11px;opacity:.5">Signed in ${esc(fmtAbs(issuedMs))}</div>` : ''}
            ${expiresMs ? `<div class="settings-hint" style="font-size:11px;opacity:.45">Expires ${esc(expIn)}</div>` : ''}
            ${s.short_id ? `<div class="settings-hint" style="font-size:10px;opacity:.35;font-family:var(--mono,monospace);margin-top:2px">id: ${esc(s.short_id)}</div>` : ''}
          </div>
          <button class="btn-dispatch"
                  onclick="signOutSession('${sidEsc}')"
                  style="font-size:11px;padding:4px 8px;background:transparent;border:1px solid var(--border)">Sign out</button>
        </div>`;
    }).join('');
  }

  const revokeAllRow = sessions.length > 0 ? `
    <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:8px">
      <button class="btn-dispatch"
              onclick="enforceSessionCleanup()"
              style="font-size:11px;padding:4px 10px;background:transparent;border:1px solid var(--border)">Clean up unnamed</button>
      <button class="btn-dispatch"
              onclick="signOutAllSessions()"
              style="font-size:11px;padding:4px 10px;background:transparent;border:1px solid #ef4444;color:#dc2626">Sign out everywhere</button>
    </div>
    <div id="remote-sessions-enforcer" class="settings-hint" style="font-size:10px;opacity:.55;margin-top:4px;text-align:right"></div>
    ` : '';

  el.innerHTML = headerHtml + listHtml + revokeAllRow;
  // Render last-run state below the buttons.
  refreshEnforcerState();
}

async function refreshEnforcerState() {
  const el = document.getElementById('remote-sessions-enforcer');
  if (!el) return;
  try {
    const r = await fetch(API_BASE + '/api/remote/sessions/enforcer-state');
    const j = await r.json();
    if (!j.last_run) { el.textContent = 'Auto-cleanup: not yet run'; return; }
    const ago = Math.max(0, Math.floor(Date.now() / 1000 - j.last_run));
    const agoStr = ago < 60 ? `${ago}s` : ago < 3600 ? `${Math.floor(ago / 60)}m` : `${Math.floor(ago / 3600)}h`;
    let msg = `Auto-cleanup ran ${agoStr} ago`;
    if (j.last_revoked_count) msg += ` · revoked ${j.last_revoked_count}`;
    if (j.last_per_session_supported === false) msg += ' · per-session revoke unsupported by CF';
    el.textContent = msg;
  } catch (_) { /* swallow */ }
}

async function enforceSessionCleanup() {
  try {
    const r = await fetch(API_BASE + '/api/remote/sessions/enforce', { method: 'POST' });
    const j = await r.json();
    if (j.ok) {
      const n = (j.revoked || []).length;
      if (n > 0) {
        showToast(`Cleaned up ${n} unnamed session${n === 1 ? '' : 's'}.`);
      } else if (j.per_session_supported === false) {
        showToast('CF doesn’t support per-session revoke for this token. Use “Sign out everywhere” instead.', 6000);
      } else {
        showToast('No unnamed sessions to clean up.');
      }
      refreshRemoteSessions();
    } else {
      showToast(j.error || 'Cleanup failed', 4000);
    }
  } catch (_) {
    showToast('Network error', 4000);
  }
}

async function signOutSession(sessionId) {
  try {
    const r = await fetch(API_BASE + '/api/remote/sessions/' + encodeURIComponent(sessionId) + '/revoke', { method: 'POST' });
    const j = await r.json();
    if (r.ok && j.ok) {
      if (j.fallback) {
        showToast('Signed out. (CF revoked all your sessions; per-session revoke not available.)', 5000);
      } else {
        showToast('Signed out.');
      }
      refreshRemoteSessions();
    } else {
      showToast(j.message || 'Could not sign out session', 4000);
    }
  } catch (_) {
    showToast('Network error', 4000);
  }
}

async function renameRemoteSession(sessionId, currentLabel) {
  const next = window.prompt(
    currentLabel
      ? 'Rename this session:'
      : "What is this session?\n\nTip: if you can't tell, click Sign out — the device that gets logged out is this one.",
    currentLabel || ''
  );
  if (next == null) return;
  const label = next.trim();
  if (!label) return;
  try {
    const r = await fetch(API_BASE + '/api/remote/sessions/' + encodeURIComponent(sessionId) + '/label', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ label }),
    });
    const j = await r.json();
    if (r.ok && j.ok) {
      showToast('Session named.');
      refreshRemoteSessions();
    } else {
      showToast(j.message || 'Could not save name', 4000);
    }
  } catch (_) {
    showToast('Network error', 4000);
  }
}

async function signOutAllSessions() {
  if (!confirm('Sign out everywhere?\n\nEvery browser and phone currently signed into your dashboard will need to re-enter the email OTP next time. The tunnel itself stays up.')) return;
  try {
    const r = await fetch(API_BASE + '/api/remote/sessions/revoke-all', { method: 'POST' });
    const j = await r.json();
    if (r.ok && j.ok) {
      showToast('All sessions signed out.');
      refreshRemoteSessions();
    } else {
      showToast(j.message || 'Could not sign out all sessions', 4000);
    }
  } catch (_) {
    showToast('Network error', 4000);
  }
}

async function enableRemoteAccess() {
  try {
    const r = await fetch(API_BASE + '/api/remote/enable', { method: 'POST' });
    const j = await r.json();
    if (!r.ok) {
      if (r.status === 501) showToast(j.message || 'Remote access is not yet available', 4000);
      else showToast('Could not start enrollment', 4000);
      return;
    }
    if (j.skip_browser) {
      // Provider signaled it completed in-process (direct-API enrollment).
      // No browser needed. After enrollment the supervisor takes a few seconds
      // to spawn cloudflared + complete first attestation — poll a few times
      // so the panel doesn't get stuck on "Connecting…" indefinitely.
      showToast('Enrolled. Bringing tunnel online…', 2500);
      refreshRemoteAccessSection();
      setTimeout(refreshRemoteAccessSection, 1500);
      setTimeout(refreshRemoteAccessSection, 3500);
      setTimeout(refreshRemoteAccessSection, 6000);
      setTimeout(refreshRemoteAccessSection, 10000);
    } else if (j.launched) {
      showToast('Sign-in opened in your browser. Complete it there, then return here.', 4000);
      setTimeout(refreshRemoteAccessSection, 2000);
      setTimeout(refreshRemoteAccessSection, 6000);
      setTimeout(refreshRemoteAccessSection, 12000);
    } else {
      // OS browser launch failed — surface the URL so user can copy it.
      _showEnrollmentFallback(j.enrollment_url);
    }
  } catch (e) {
    showToast('Network error starting enrollment', 4000);
  }
}

function _showEnrollmentFallback(url) {
  // Inject a one-off element under the Settings section with the URL +
  // copy button. Self-removes when the section refreshes.
  const sect = document.getElementById('remote-access-section');
  if (!sect) {
    showToast('Could not open browser. URL: ' + url, 8000);
    return;
  }
  const div = document.createElement('div');
  div.className = 'settings-row';
  div.style.cssText = 'background:#fef3c7;border-left:3px solid #f59e0b;padding:10px 12px;margin-top:10px;border-radius:6px';
  div.innerHTML = `
    <div style="flex:1">
      <div class="settings-label" style="font-size:13px">Couldn't open browser automatically</div>
      <div class="settings-hint" style="margin-bottom:6px">Copy this link and open it in your browser to finish signing in:</div>
      <div style="font-family:'JetBrains Mono','Courier New',monospace;font-size:11px;color:#1f2937;background:#fff;padding:6px 10px;border-radius:4px;word-break:break-all;border:1px solid #f3e9c8">${esc(url)}</div>
    </div>
    <button class="btn-dispatch" onclick="copyToClipboardSafe('${esc(url).replace(/'/g, "\\'")}','Link copied — paste it into your browser')" style="font-size:12px;padding:6px 10px;margin-left:12px;align-self:flex-start">Copy link</button>
  `;
  sect.appendChild(div);
}

async function disableRemoteAccess() {
  if (!confirm('Pause remote access? The dashboard will be unreachable from outside your network until you resume.')) return;
  try {
    const r = await fetch(API_BASE + '/api/remote/disable', { method: 'POST' });
    if (r.ok) { showToast('Remote access paused'); refreshRemoteAccessSection(); }
    else if (r.status === 501) { const j = await r.json(); showToast(j.message || 'Not implemented yet', 3500); }
    else { showToast('Could not pause remote access', 4000); }
  } catch (_) { showToast('Network error', 4000); }
}

async function resumeRemoteAccess() {
  // Optimistic: flip to "connecting" state immediately so the user doesn't
  // see the yellow "paused" notice for the 1–5s while the supervisor restarts.
  if (window._remoteState) {
    window._remoteState = { ...window._remoteState, connecting: true, online: false, error_code: null, error_message: null };
    refreshRemoteAccessSection();
  }
  try {
    const r = await fetch(API_BASE + '/api/remote/resume', { method: 'POST' });
    if (r.ok) {
      showToast('Reconnecting…');
      // Poll a few times to catch the actual online state quickly.
      setTimeout(refreshRemoteAccessSection, 800);
      setTimeout(refreshRemoteAccessSection, 2500);
      setTimeout(refreshRemoteAccessSection, 5000);
      setTimeout(refreshRemoteAccessSection, 10000);
    } else if (r.status === 501) {
      const j = await r.json();
      showToast(j.message || 'Not implemented yet', 3500);
      refreshRemoteAccessSection();
    } else {
      const j = await r.json().catch(() => ({}));
      showToast(j.message || 'Could not resume remote access', 4000);
      refreshRemoteAccessSection();
    }
  } catch (_) {
    showToast('Network error', 4000);
    refreshRemoteAccessSection();
  }
}

async function disconnectRemoteAccess() {
  if (!confirm("Disconnect this PC from remote access?\n\nYou will need to sign in again to reconnect, and any link you've shared (https://" + (window._remoteState?.hostname || 'your-name.clayrune.io') + ") will stop working immediately.")) return;
  try {
    const r = await fetch(API_BASE + '/api/remote/disconnect', { method: 'POST' });
    if (r.ok) { showToast('This PC has been disconnected'); refreshRemoteAccessSection(); }
    else if (r.status === 501) { const j = await r.json(); showToast(j.message || 'Not implemented yet', 3500); }
    else { showToast('Could not disconnect', 4000); }
  } catch (_) { showToast('Network error', 4000); }
}


// ── ES-module interop ──────────────────────────────────────────────────
// Re-expose page-called functions on window. Inbound callers (settings-
// drill.js templates/hydration + the inline SW statechange listener) and
// this module's own generated on*= attributes resolve against the global
// object at call time. State (_localAuthState/_remoteState/_remoteAutoPoll/
// _pushState/_deferredInstallPrompt) is already window-qualified at every
// read/write by construction; no accessor bridges needed (formal scan empty).
window.localAccessSettingsHTML = localAccessSettingsHTML;
window.remoteAccessSettingsHTML = remoteAccessSettingsHTML;
window.pushNotificationsSettingsHTML = pushNotificationsSettingsHTML;
window.refreshLocalAccessSection = refreshLocalAccessSection;
window.refreshRemoteAccessSection = refreshRemoteAccessSection;
window.refreshPushSection = refreshPushSection;
window.fetchRemoteStatus = fetchRemoteStatus;
window.showLocalAuthForm = showLocalAuthForm;
window.submitLocalAuth = submitLocalAuth;
window.enableRemoteAccess = enableRemoteAccess;
window.copyToClipboardSafe = copyToClipboardSafe;
window.disableRemoteAccess = disableRemoteAccess;
window.resumeRemoteAccess = resumeRemoteAccess;
window.disconnectRemoteAccess = disconnectRemoteAccess;
window.testPushNotification = testPushNotification;
window.unsubscribeThisDevice = unsubscribeThisDevice;
window.enablePushOnThisDevice = enablePushOnThisDevice;
window.installPwaApp = installPwaApp;
window.removePushSubscription = removePushSubscription;
window.updatePushSubscription = updatePushSubscription;
window.renameRemoteSession = renameRemoteSession;
window.signOutSession = signOutSession;
window.enforceSessionCleanup = enforceSessionCleanup;
window.signOutAllSessions = signOutAllSessions;
