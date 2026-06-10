// ── Mobile pairing (Clayrune Android shell — QR-based onboarding) ────────────
// Lets the user configure CF Access service-token creds ONCE on the desktop
// dashboard and ship them to the phone via a scannable QR code, instead of
// hand-typing the Client ID / Secret in SetupActivity. The APK's
// SetupActivity scans `clayrune://pair?...` and auto-fills + verifies +
// persists. Backend lives under /api/mobile-pair/*.

window._mobilePairState = { configured: null, tunnel_url: '', client_id: '',
                            client_secret_masked: '', pair_uri: '' };

function mobilePairingSettingsHTML() {
  return `
    <div class="settings-section" id="mobile-pair-section">
      <div class="settings-section-title">Pair Mobile App</div>
      <div class="settings-hint" style="margin-bottom:10px;line-height:1.45">
        Set up the Clayrune Android app by scanning a QR code from the app's
        setup screen. No Cloudflare account or service-token paste required.
      </div>
      <div id="mobile-pair-body">
        <div class="settings-hint" style="font-size:11px;opacity:.6;padding:4px 0">Loading…</div>
      </div>
    </div>`;
}

async function refreshMobilePairingSection() {
  const body = document.getElementById('mobile-pair-body');
  if (!body) return;
  // Decide flow based on Remote Access enrollment state: enrolled → auto-pair
  // via the control plane (no CF dashboard), unenrolled → fall back to the
  // legacy manual paste form (operator path).
  let remote = null;
  try {
    const r = await fetch(API_BASE + '/api/remote/status', { cache: 'no-store' });
    if (r.ok) remote = await r.json();
  } catch (_) { /* offline / no-provider → handled below */ }

  const enrolled = !!(remote && remote.enrolled);
  if (enrolled) {
    await _mobilePairRenderAuto(body, remote);
  } else {
    await _mobilePairRenderManual(body);
  }
}

async function _mobilePairRenderAuto(body, remote) {
  // Try to list already-paired phones; tolerate 404 if CP hasn't shipped the
  // endpoint yet (the "Pair a phone" button still works as soon as it does).
  let tokens = [];
  let listError = '';
  try {
    const r = await fetch(API_BASE + '/api/mobile-pair/tokens', { cache: 'no-store' });
    const b = await r.json().catch(() => ({}));
    if (r.ok && Array.isArray(b.tokens)) tokens = b.tokens;
    else if (b && b.error) listError = b.message || b.error;
  } catch (e) { listError = e.message || String(e); }

  const hostnamePill = remote.hostname
    ? `<span style="font-size:11px;padding:2px 8px;border-radius:8px;background:var(--accent-dim);color:var(--accent);font-weight:600">${esc(remote.hostname)}</span>`
    : '';

  const pairedRows = tokens.map(t => `
    <div style="display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px dashed var(--border-soft,rgba(0,0,0,.08))">
      <div style="flex:1;min-width:0">
        <div style="font-size:13px;color:var(--text);font-weight:500">${esc(t.label || 'Mobile device')}</div>
        <div style="font-size:11px;color:var(--text-dim)">paired ${esc((t.created_at || '').slice(0, 10))}${t.last_used_at ? ' · last seen ' + esc(t.last_used_at.slice(0, 10)) : ''}</div>
      </div>
      <button class="btn-dispatch" style="font-size:11px;padding:4px 10px;background:transparent;border:1px solid var(--border);color:var(--text-dim)" onclick="_mobilePairRevokeToken('${esc(t.token_id)}', '${esc(t.label || 'this phone').replace(/'/g, '\\\'')}')">Revoke</button>
    </div>`).join('');

  body.innerHTML = `
    <div style="display:flex;flex-direction:column;gap:14px">
      <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
        <span class="settings-label" style="margin:0">Pairing to</span>
        ${hostnamePill}
      </div>

      <div id="mobile-pair-create" style="display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap">
        <div style="flex:1;min-width:180px">
          <div class="settings-label">Label for this phone</div>
          <input id="mobile-pair-label" class="settings-input" type="text" value="" placeholder="e.g. Ron's Pixel" style="width:100%" maxlength="48">
        </div>
        <button class="btn-dispatch" style="font-size:12px;padding:8px 14px" onclick="_mobilePairGenerate()">Pair a phone</button>
      </div>
      <div id="mobile-pair-error" style="display:none;font-size:11px;color:var(--red,#e35858);background:#ef444411;border-left:3px solid #ef4444;padding:8px 12px;border-radius:4px"></div>
      <div id="mobile-pair-qr-block" style="display:none"></div>

      <div>
        <div class="settings-label" style="margin-bottom:4px">Paired phones</div>
        ${tokens.length
          ? pairedRows
          : `<div class="settings-hint" style="font-size:11px;opacity:.7">${listError ? esc('Could not load list: ' + listError) : 'No phones paired yet.'}</div>`}
      </div>

      <details style="margin-top:10px">
        <summary style="font-size:11px;color:var(--text-dim);cursor:pointer;user-select:none">Advanced: configure tunnel manually</summary>
        <div id="mobile-pair-manual-wrap" style="margin-top:10px;padding-top:10px;border-top:1px dashed var(--border-soft,rgba(0,0,0,.08))">
          <div class="settings-hint" style="font-size:11px;margin-bottom:8px">For users with their own Cloudflare Zero Trust setup. The auto-pair flow above is the recommended path.</div>
          <div id="mobile-pair-manual-body">
            <div class="settings-hint" style="font-size:11px;opacity:.6">Loading…</div>
          </div>
        </div>
      </details>
    </div>`;

  // Hydrate the manual section lazily — it's hidden inside <details> but
  // the form should be ready the moment the user expands it.
  _mobilePairRenderManual(document.getElementById('mobile-pair-manual-body'));
}

async function _mobilePairRenderManual(bodyEl) {
  if (!bodyEl) return;
  let st;
  try {
    const r = await fetch(API_BASE + '/api/mobile-pair/config');
    st = await r.json();
  } catch (e) {
    bodyEl.innerHTML = `<div class="settings-hint" style="color:var(--red,#e35858)">Failed to load: ${esc(e.message || e)}</div>`;
    return;
  }
  window._mobilePairState = st;
  if (!st.configured) {
    bodyEl.innerHTML = _mobilePairFormHTML('', '', '');
    return;
  }
  bodyEl.innerHTML = `
    <div style="display:grid;grid-template-columns:1fr auto;gap:14px;align-items:flex-start">
      <div style="min-width:0">
        <div class="settings-row" style="border:none;padding:0">
          <div>
            <div class="settings-label">Tunnel URL</div>
            <div class="settings-hint" style="word-break:break-all">${esc(st.tunnel_url)}</div>
          </div>
        </div>
        <div class="settings-row" style="border:none;padding:0">
          <div>
            <div class="settings-label">CF Access Client ID</div>
            <div class="settings-hint" style="word-break:break-all">${esc(st.client_id)}</div>
          </div>
        </div>
        <div class="settings-row" style="border:none;padding:0">
          <div>
            <div class="settings-label">CF Access Client Secret</div>
            <div class="settings-hint">${esc(st.client_secret_masked || '••••')}</div>
          </div>
        </div>
        <div style="display:flex;gap:8px;margin-top:10px;flex-wrap:wrap">
          <button class="btn-dispatch" style="font-size:12px;padding:6px 10px;background:transparent;border:1px solid var(--border)" onclick="_mobilePairEdit()">Edit</button>
          <button class="btn-dispatch" style="font-size:12px;padding:6px 10px;background:transparent;border:1px solid var(--border)" onclick="_mobilePairCopyUri()">Copy URI</button>
          <button class="btn-dispatch" style="font-size:12px;padding:6px 10px;background:var(--red-dim,#5a2828);border-color:var(--red,#e35858);color:var(--red-text,#ffd2d2)" onclick="_mobilePairDelete()">Remove</button>
        </div>
      </div>
      <div style="display:flex;flex-direction:column;align-items:center;gap:6px">
        <div id="mobile-pair-qr" style="background:#fff;padding:10px;border-radius:8px;width:220px;height:220px;display:flex;align-items:center;justify-content:center"></div>
        <div class="settings-hint" style="font-size:10px;text-align:center;max-width:220px">Scan from the Clayrune app's setup screen</div>
      </div>
    </div>`;
  _mobilePairRenderQR(st.pair_uri);
}

async function _mobilePairGenerate() {
  const labelEl = document.getElementById('mobile-pair-label');
  const errEl = document.getElementById('mobile-pair-error');
  const btn = document.querySelector('#mobile-pair-create .btn-dispatch');
  const label = (labelEl ? labelEl.value : '').trim() || 'Mobile device';
  if (errEl) { errEl.style.display = 'none'; errEl.textContent = ''; }
  if (btn) { btn.disabled = true; btn.textContent = 'Minting…'; }
  let b;
  try {
    const r = await fetch(API_BASE + '/api/mobile-pair/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ label }),
    });
    b = await r.json().catch(() => ({}));
    if (!r.ok || !b.ok) {
      if (errEl) {
        errEl.textContent = `Could not pair: ${b.message || b.error || ('HTTP ' + r.status)}`;
        errEl.style.display = '';
      }
      if (btn) { btn.disabled = false; btn.textContent = 'Pair a phone'; }
      return;
    }
  } catch (e) {
    if (errEl) { errEl.textContent = `Failed: ${e.message || e}`; errEl.style.display = ''; }
    if (btn) { btn.disabled = false; btn.textContent = 'Pair a phone'; }
    return;
  }

  // Refresh the paired list FIRST so the new token shows up. This rebuilds
  // the auto section's DOM (including #mobile-pair-qr-block), so we have to
  // render the one-time QR AFTER the refresh — otherwise the QR gets nuked
  // by the re-render. The client_secret lives only in `b.pair_uri`; once
  // the user dismisses this block they have to revoke + re-pair to scan
  // again. We stash b.pair_uri so a re-render survives even if some other
  // refresh fires concurrently.
  window._mobilePairFreshUri = b.pair_uri;
  await refreshMobilePairingSection();
  const qrBlock = document.getElementById('mobile-pair-qr-block');
  if (qrBlock) {
    qrBlock.innerHTML = `
      <div style="background:var(--surface2);border:1px solid var(--border);border-radius:10px;padding:14px;display:grid;grid-template-columns:auto 1fr;gap:14px;align-items:flex-start">
        <div id="mobile-pair-qr-fresh" style="background:#fff;padding:10px;border-radius:8px;width:220px;height:220px;display:flex;align-items:center;justify-content:center;flex-shrink:0"></div>
        <div style="min-width:0">
          <div style="font-size:13px;font-weight:600;color:var(--text);margin-bottom:6px">Scan from the Clayrune app</div>
          <div class="settings-hint" style="font-size:11px;line-height:1.45;margin-bottom:8px">
            Open the app on your phone and tap "Scan QR" in the setup screen.
            This code contains the secret — it's shown <strong>only once</strong>;
            if you can't scan now, revoke and create a new pairing instead.
          </div>
          <div style="display:flex;gap:6px;flex-wrap:wrap">
            <button class="btn-dispatch" style="font-size:11px;padding:4px 10px;background:transparent;border:1px solid var(--border)" onclick="_mobilePairCopyFreshUri()">Copy URI</button>
            <button class="btn-dispatch" style="font-size:11px;padding:4px 10px;background:transparent;border:1px solid var(--border)" onclick="_mobilePairDismissFresh()">Done</button>
          </div>
        </div>
      </div>`;
    qrBlock.style.display = '';
    _mobilePairRenderQRInto('mobile-pair-qr-fresh', b.pair_uri);
  }
  // The label input was re-rendered by refresh — clear it on the new node.
  const newLabelEl = document.getElementById('mobile-pair-label');
  if (newLabelEl) newLabelEl.value = '';
  const newBtn = document.querySelector('#mobile-pair-create .btn-dispatch');
  if (newBtn) newBtn.textContent = 'Pair another';
}

function _mobilePairDismissFresh() {
  const qrBlock = document.getElementById('mobile-pair-qr-block');
  if (qrBlock) { qrBlock.style.display = 'none'; qrBlock.innerHTML = ''; }
  // Drop the stashed secret too — once dismissed it's gone for good.
  window._mobilePairFreshUri = null;
}

function _mobilePairCopyFreshUri() {
  const uri = window._mobilePairFreshUri || '';
  if (!uri) { showToast('No fresh pair URI to copy', 3000); return; }
  navigator.clipboard.writeText(uri).then(
    () => showToast('Pair URI copied — keep it secret'),
    () => showToast('Copy failed', 4000),
  );
}

async function _mobilePairRevokeToken(tokenId, label) {
  if (!tokenId) return;
  if (!confirm(`Revoke ${label}? That phone will lose access immediately.`)) return;
  try {
    const r = await fetch(API_BASE + '/api/mobile-pair/tokens/' + encodeURIComponent(tokenId), { method: 'DELETE' });
    const b = await r.json().catch(() => ({}));
    if (!r.ok || (b && b.error)) {
      showToast(`Revoke failed: ${b.message || b.error || ('HTTP ' + r.status)}`, 5000);
      return;
    }
    showToast('Phone revoked');
    refreshMobilePairingSection();
  } catch (e) {
    showToast(`Revoke failed: ${e.message || e}`, 5000);
  }
}

function _mobilePairRenderQRInto(elId, uri) {
  const el = document.getElementById(elId);
  if (!el || !uri) return;
  el.innerHTML = '';
  if (typeof QRCode === 'undefined') {
    el.innerHTML = '<div style="font-size:11px;color:#666">QR library not loaded</div>';
    return;
  }
  try {
    new QRCode(el, { text: uri, width: 200, height: 200, colorDark: '#000000', colorLight: '#ffffff', correctLevel: QRCode.CorrectLevel.M });
  } catch (e) {
    el.innerHTML = `<div style="font-size:11px;color:#666">QR render failed: ${esc(e.message || e)}</div>`;
  }
}

function _mobilePairFormHTML(url, cid, secret) {
  return `
    <div style="display:flex;flex-direction:column;gap:8px">
      <div>
        <div class="settings-label">Tunnel URL</div>
        <div class="settings-hint" style="margin-bottom:4px">e.g. <code>https://ronl.clayrune.io</code></div>
        <input id="mobile-pair-url" class="settings-input" type="text" value="${esc(url || '')}" placeholder="https://your.clayrune.io" style="width:100%">
      </div>
      <div>
        <div class="settings-label">CF Access Client ID</div>
        <div class="settings-hint" style="margin-bottom:4px">Cloudflare Zero Trust → Access → Service Auth — ends in <code>.access</code></div>
        <input id="mobile-pair-cid" class="settings-input" type="text" value="${esc(cid || '')}" placeholder="abc1234....access" style="width:100%">
      </div>
      <div>
        <div class="settings-label">CF Access Client Secret</div>
        <div class="settings-hint" style="margin-bottom:4px">Long opaque string. Shown only at creation — rotate if lost.</div>
        <input id="mobile-pair-secret" class="settings-input" type="password" value="${esc(secret || '')}" placeholder="••••••••••••" style="width:100%">
      </div>
      <div id="mobile-pair-error" style="display:none;font-size:11px;color:var(--red,#e35858);background:#ef444411;border-left:3px solid #ef4444;padding:8px 12px;border-radius:4px"></div>
      <div style="display:flex;gap:8px;margin-top:4px;flex-wrap:wrap">
        <button class="btn-dispatch" style="font-size:12px;padding:6px 10px" onclick="_mobilePairSave()">Verify & save</button>
        ${window._mobilePairState && window._mobilePairState.configured
          ? `<button class="btn-dispatch" style="font-size:12px;padding:6px 10px;background:transparent;border:1px solid var(--border)" onclick="refreshMobilePairingSection()">Cancel</button>`
          : ''}
      </div>
    </div>`;
}

function _mobilePairEdit() {
  const body = document.getElementById('mobile-pair-body');
  if (!body) return;
  const st = window._mobilePairState || {};
  body.innerHTML = _mobilePairFormHTML(st.tunnel_url || '', st.client_id || '', '');
}

async function _mobilePairSave() {
  const urlEl = document.getElementById('mobile-pair-url');
  const cidEl = document.getElementById('mobile-pair-cid');
  const secEl = document.getElementById('mobile-pair-secret');
  const errEl = document.getElementById('mobile-pair-error');
  if (!urlEl || !cidEl || !secEl) return;
  const tunnel_url = urlEl.value.trim();
  const client_id = cidEl.value.trim();
  const client_secret = secEl.value.trim();
  if (errEl) { errEl.style.display = 'none'; errEl.textContent = ''; }
  if (!tunnel_url || !client_id || !client_secret) {
    if (errEl) { errEl.textContent = 'All three fields are required.'; errEl.style.display = ''; }
    return;
  }
  const saveBtn = document.querySelector('#mobile-pair-body button.btn-dispatch');
  if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = 'Verifying…'; }
  try {
    const r = await fetch(API_BASE + '/api/mobile-pair/config', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tunnel_url, client_id, client_secret }),
    });
    const b = await r.json();
    if (!r.ok || !b.ok) {
      if (errEl) { errEl.textContent = `Verification failed: ${b.error || ('HTTP ' + r.status)}`; errEl.style.display = ''; }
      if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = 'Verify & save'; }
      return;
    }
    showToast('Mobile pairing saved');
    await refreshMobilePairingSection();
  } catch (e) {
    if (errEl) { errEl.textContent = `Failed: ${e.message || e}`; errEl.style.display = ''; }
    if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = 'Verify & save'; }
  }
}

async function _mobilePairDelete() {
  if (!confirm('Remove mobile-pairing credentials? The QR code will stop working until you reconfigure.')) return;
  try {
    await fetch(API_BASE + '/api/mobile-pair/config', { method: 'DELETE' });
    showToast('Mobile pairing removed');
    await refreshMobilePairingSection();
  } catch (e) {
    showToast(`Delete failed: ${e.message || e}`, 5000);
  }
}

function _mobilePairCopyUri() {
  const uri = (window._mobilePairState && window._mobilePairState.pair_uri) || '';
  if (!uri) return;
  navigator.clipboard.writeText(uri).then(
    () => showToast('Pair URI copied to clipboard'),
    () => showToast('Copy failed — select the QR and right-click instead', 5000),
  );
}

function _mobilePairRenderQR(uri) {
  const el = document.getElementById('mobile-pair-qr');
  if (!el || !uri) return;
  el.innerHTML = '';
  if (typeof QRCode === 'undefined') {
    el.innerHTML = '<div style="font-size:11px;color:#666">QR library not loaded</div>';
    return;
  }
  try {
    new QRCode(el, {
      text: uri,
      width: 200,
      height: 200,
      colorDark: '#000000',
      colorLight: '#ffffff',
      correctLevel: QRCode.CorrectLevel.M,
    });
  } catch (e) {
    el.innerHTML = `<div style="font-size:11px;color:#666">QR render failed: ${esc(e.message || e)}</div>`;
  }
}

// interop: called from the Settings render in index.html (_renderSettings)
// and from this module's own generated onclick attributes, which resolve
// against the global object at click time.
window.mobilePairingSettingsHTML = mobilePairingSettingsHTML;     // interop: settings template (_renderSettings)
window.refreshMobilePairingSection = refreshMobilePairingSection; // interop: settings hydration (_renderSettings) + generated onclick (Cancel)
window._mobilePairGenerate = _mobilePairGenerate;                 // interop: generated onclick (Pair a phone)
window._mobilePairRevokeToken = _mobilePairRevokeToken;           // interop: generated onclick (Revoke)
window._mobilePairCopyFreshUri = _mobilePairCopyFreshUri;         // interop: generated onclick (fresh-QR Copy URI)
window._mobilePairDismissFresh = _mobilePairDismissFresh;         // interop: generated onclick (fresh-QR Done)
window._mobilePairEdit = _mobilePairEdit;                         // interop: generated onclick (Edit)
window._mobilePairCopyUri = _mobilePairCopyUri;                   // interop: generated onclick (Copy URI)
window._mobilePairDelete = _mobilePairDelete;                     // interop: generated onclick (Remove)
window._mobilePairSave = _mobilePairSave;                         // interop: generated onclick (Verify & save)
