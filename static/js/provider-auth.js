// ── Auth banner — multi-provider ───────────────────────────────────────────
// Always checks claude (via the legacy /api/claude/auth-status alias). When
// multi_provider_enabled is on and the default provider differs from claude,
// also checks that provider and surfaces provider-specific messaging.
let _authBannerDismissed = false;
let _authBannerLastReason = null;

async function refreshAuthStatus() {
  try {
    // Always check claude (the original + most common provider).
    const res = await fetchFailFast(API_BASE + '/api/claude/auth-status');
    if (!res.ok) return;
    const state = await res.json();
    // Attach provider name so _renderAuthBanner can label it correctly.
    state._provider = 'claude';
    _renderAuthBanner(state);
  } catch (e) {
    // Network blip — leave whatever banner state we have.
  }
}

async function refreshProviderAuthStatus(providerName) {
  if (!providerName || providerName === 'claude') { refreshAuthStatus(); return; }
  try {
    const res = await fetch(API_BASE + `/api/agent/provider/${providerName}/auth`);
    if (!res.ok) return;
    const data = await res.json();
    // Normalize to the same shape as /api/claude/auth-status
    const auth = data.auth_state || {};
    const state = {
      ok: auth.status === 'ok',
      reason: auth.status !== 'ok' ? auth.status : null,
      _provider: providerName,
    };
    _renderAuthBanner(state);
  } catch (e) { /* ignore blips */ }
}

function _renderAuthBanner(state) {
  const banner = document.getElementById('auth-banner');
  if (!banner) return;
  const ok = !state || state.ok !== false;
  if (ok) {
    banner.classList.add('hidden');
    _authBannerLastReason = null;
    return;
  }
  // If we already dismissed this exact reason, stay hidden until reason changes.
  if (_authBannerDismissed && state.reason === _authBannerLastReason) {
    banner.classList.add('hidden');
    return;
  }
  _authBannerLastReason = state.reason;
  _authBannerDismissed = false;
  const textEl = document.getElementById('auth-banner-text');
  if (textEl) textEl.textContent = _authBannerMessage(state);
  const signin = document.getElementById('auth-banner-signin');
  if (signin) {
    const prov = (state && state._provider) || 'claude';
    const provLabel = prov === 'claude' ? 'Claude'
      : ((_agentProviders || []).find(p => p.name === prov) || {}).display_name || prov;
    signin.textContent = `Authenticate ${provLabel}`;
    if (prov === 'claude') {
      signin.onclick = () => claudeAuthenticate();
    } else {
      signin.onclick = () => settingsProviderTerminalLogin(prov, signin);
    }
  }
  banner.classList.remove('hidden');
}

function _authBannerMessage(state) {
  const prov = (state && state._provider) ? state._provider : 'claude';
  const provLabel = prov === 'claude' ? 'Claude'
    : ((_agentProviders || []).find(p => p.name === prov) || {}).display_name || prov;
  switch (state && state.reason) {
    case 'not_logged_in':
      return `${provLabel} isn't signed in. Agents will fail until you authenticate.`;
    case 'invalid_api_key':
      return `${provLabel} credentials are invalid — sign in again to refresh them.`;
    case 'cli_not_found':
      return `The \`${prov}\` CLI isn't on this machine's PATH.`;
    default:
      return `${provLabel} authentication is failing. Sign in to retry.`;
  }
}

function dismissAuthBanner() {
  _authBannerDismissed = true;
  const banner = document.getElementById('auth-banner');
  if (banner) banner.classList.add('hidden');
}

async function claudeAuthenticate() {
  // Launches `claude` in a NEW OS-level terminal window (not MC's piped
  // pop-out — claude's OAuth flow needs a real TTY). User completes browser
  // sign-in there, then clicks "Re-check" here.
  try {
    const res = await fetch(API_BASE + '/api/claude/login-launch', { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) {
      alert('Failed to launch claude: ' + (data.error || res.status));
      return;
    }
    showToast('A terminal window opened. Type /login in it to sign in, then click Re-check here.', 12000);
  } catch (e) {
    alert('Failed to launch claude: ' + e);
  }
}

async function claudeAuthRecheck() {
  const btn = document.getElementById('auth-banner-recheck');
  if (btn) { btn.disabled = true; btn.textContent = 'Checking...'; }
  try {
    const res = await fetch(API_BASE + '/api/claude/auth-probe', { method: 'POST' });
    const state = await res.json();
    _renderAuthBanner(state);
    _renderClaudeAuthStatusLine(state);
  } catch (e) {
    // ignore
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Re-check'; }
  }
}

// Settings panel: explicit Sign-in + Check-status buttons (banner is best-effort,
// these are the user's escape hatch when the banner doesn't surface).
async function settingsClaudeLogin() {
  try {
    const res = await fetch(API_BASE + '/api/claude/login-launch', { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) {
      alert('Failed to launch claude: ' + (data.error || res.status));
      return;
    }
    showToast('A terminal window opened. Type /login in it to sign in, then click Check status.', 12000);
  } catch (e) {
    alert('Failed to launch claude: ' + e);
  }
}

async function settingsClaudeAuthCheck() {
  const line = document.getElementById('claude-auth-status-line');
  if (line) line.innerHTML = '<span style="color:var(--text-faint)">Checking...</span>';
  try {
    const res = await fetch(API_BASE + '/api/claude/auth-probe', { method: 'POST' });
    const state = await res.json();
    _renderAuthBanner(state);
    _renderClaudeAuthStatusLine(state);
  } catch (e) {
    if (line) line.innerHTML = '<span style="color:#ef4444">Check failed</span>';
  }
}

// ── Provider Auth helpers (Gemini, Codex, Aider, ...) ─────────────────────
// One env var per provider. For OAuth providers (gemini), users can also
// click "Launch terminal login" to complete the browser flow.
const PROVIDER_AUTH_KEYS = {
  gemini:   'GEMINI_API_KEY',
  codex:    'OPENAI_API_KEY',
  aider:    'OPENAI_API_KEY',
  opencode: 'OPENCODE_API_KEY',
  goose:    'OPENAI_API_KEY',
  kiro:     'AWS_PROFILE',
};

async function settingsProviderSetEnv(provider, key, btnEl) {
  const inp = document.getElementById(`settings-prov-key-${provider}`);
  if (!inp) return;
  const value = inp.value || '';
  if (btnEl) { btnEl.disabled = true; btnEl.textContent = 'Saving...'; }
  try {
    const res = await fetch(API_BASE + `/api/agent/provider/${provider}/env`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ key, value }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) {
      alert('Failed to save: ' + (data.error || res.status));
      return;
    }
    showToast(value ? `Saved ${key}. New ${provider} sessions will use it.`
                    : `Cleared ${key}.`, 6000);
    // Mask the input now that it's saved
    if (value) inp.value = '••••••••';
    settingsProviderRefresh(provider);
  } catch (e) {
    alert('Save failed: ' + e);
  } finally {
    if (btnEl) { btnEl.disabled = false; btnEl.textContent = 'Save'; }
  }
}

async function settingsProviderTerminalLogin(provider, btnEl) {
  if (btnEl) { btnEl.disabled = true; btnEl.textContent = 'Launching...'; }
  try {
    const res = await fetch(API_BASE + `/api/agent/provider/${provider}/login-launch`,
                            { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) {
      alert('Failed to launch terminal: ' + (data.error || res.status));
      return;
    }
    showToast(`A terminal opened with ${provider}. Complete sign-in there, then click Refresh.`, 12000);
  } catch (e) {
    alert('Launch failed: ' + e);
  } finally {
    if (btnEl) { btnEl.disabled = false; btnEl.textContent = 'Launch terminal login'; }
  }
}

async function settingsProviderRefresh(provider) {
  // Force a fresh /api/agent/providers fetch (reset the cached list) and
  // re-render the Settings panel so the new state shows up.
  _agentProviders = null;
  try {
    await fetch(API_BASE + `/api/agent/provider/${provider}/auth`);
  } catch (e) { /* fire-and-forget; the providers re-fetch is what counts */ }
  if (typeof refreshSettings === 'function') {
    refreshSettings();
  } else if (openModals && openModals.has('__settings')) {
    // Fallback: re-open the Settings modal so the new auth-state pills render.
    closeModalById('__settings');
    setTimeout(() => openSettings(), 50);
  }
}

function _renderClaudeAuthStatusLine(state) {
  const line = document.getElementById('claude-auth-status-line');
  if (!line) return;
  if (!state || state.ok === undefined) { line.innerHTML = ''; return; }
  if (state.ok) {
    line.innerHTML = '<span style="color:#22c55e">&#x2713; Signed in</span>';
  } else {
    const reason = state.reason === 'not_logged_in' ? 'Not signed in'
                 : state.reason === 'invalid_api_key' ? 'Invalid credentials'
                 : state.reason === 'cli_not_found' ? 'claude CLI not found'
                 : 'Auth issue';
    line.innerHTML = `<span style="color:#ef4444">&#x2717; ${esc(reason)}</span>`;
  }
}



// ── Interop: re-expose for inline / static-HTML / cross-region callers.
//    All runtime-EXCEPT `refreshAuthStatus`, which the inline `startRefresh`
//    references at parse time via `setInterval(()=>window.refreshAuthStatus(),
//    90000)` — that inline shim (a 1-line deferral edit, NOT part of this
//    moved region) resolves the window prop at each 90s tick, after this
//    deferred module has evaluated. `PROVIDER_AUTH_KEYS` is a read-only const
//    read by the inline Provider Settings section (`_renderProviderSettings`)
//    at render time — window-exposed so the bare read resolves. State
//    (_authBannerDismissed / _authBannerLastReason) + _renderAuthBanner /
//    _authBannerMessage / _renderClaudeAuthStatusLine / refreshProviderAuthStatus
//    are module-private. ──
window.refreshAuthStatus = refreshAuthStatus;     // startRefresh 90s poll (shim) + SSE-error + fetchProjects callback
window.dismissAuthBanner = dismissAuthBanner;     // auth-banner static onclick
window.claudeAuthenticate = claudeAuthenticate;   // auth-banner static onclick
window.claudeAuthRecheck = claudeAuthRecheck;     // auth-banner static onclick
window.settingsClaudeLogin = settingsClaudeLogin; // Provider Settings section onclick
window.settingsClaudeAuthCheck = settingsClaudeAuthCheck; // Provider Settings section onclick
window.PROVIDER_AUTH_KEYS = PROVIDER_AUTH_KEYS;   // read by inline _renderProviderSettings
window.settingsProviderSetEnv = settingsProviderSetEnv;           // Provider Settings section onclick
window.settingsProviderTerminalLogin = settingsProviderTerminalLogin; // Provider Settings section onclick
window.settingsProviderRefresh = settingsProviderRefresh;         // Provider Settings section onclick
