// ── Provider Settings section ─────────────────────────────────────────────
// Renders the "Agent Provider" block inside _renderSettings(). One health
// card per registered provider — claude included, so it no longer needs the
// separate standalone "Claude Sign-in" section.
function _renderProviderSettings(cfg) {
  // Fall back to a synthetic claude entry if the providers endpoint hasn't
  // resolved — the Claude sign-in card must never silently disappear.
  let provs = _agentProviders || [];
  if (provs.length === 0) {
    provs = [{ name: 'claude', display_name: 'Claude Code', installed: true,
               auth_status: 'unknown' }];
  }

  const defProv = cfg.default_provider || 'claude';
  const provOpts = provs.map(p =>
    `<option value="${esc(p.name)}" ${defProv === p.name ? 'selected' : ''}>${esc(p.display_name)}</option>`
  ).join('');

  const provRows = provs.map(p => {
    const isClaude  = p.name === 'claude';
    const installed = !!p.installed;
    const authOk    = p.auth_status === 'ok';
    const authNone  = p.auth_status === 'not_logged_in';
    const pillColor = !installed ? 'var(--text-faint)' : authOk ? 'var(--green)' : 'var(--amber)';
    const pillText  = !installed ? 'not installed'
                    : authOk    ? 'signed in'
                    : authNone  ? 'not signed in'
                    : 'status unknown';
    const version   = p.version ? ` · v${esc(p.version)}` : '';

    // Install help: show install command for uninstalled providers
    const installHint = !installed && p.install_hint
      ? `<div class="settings-hint" style="margin-top:4px;font-family:monospace;font-size:11px;color:var(--accent)">${esc(p.install_hint)}</div>`
      : '';

    // Action area differs by provider:
    //  • claude  → OAuth via the `/login` slash command in a terminal
    //  • others  → API-key env var + optional terminal login
    let actionBtns = '';
    let authControls = '';
    if (isClaude) {
      actionBtns = `<div style="display:flex;gap:6px;flex-shrink:0">
        <button class="btn-add" onclick="settingsClaudeLogin()">Sign in</button>
        <button class="btn-add" style="background:var(--surface3);color:var(--text)" onclick="settingsClaudeAuthCheck()">Check</button>
      </div>`;
      authControls = `<div class="settings-hint" style="margin-top:6px">
        Opens a terminal; type <code>/login</code> to finish sign-in.
        <span id="claude-auth-status-line" style="display:inline-block;margin-left:6px"></span>
      </div>`;
    } else if (installed) {
      const envKey = PROVIDER_AUTH_KEYS[p.name] || '';
      const keyInput = envKey ? `
        <div style="display:flex;gap:6px;align-items:center;margin-top:8px;flex-wrap:wrap">
          <label style="font-size:11px;color:var(--text-faint);min-width:130px">${esc(envKey)}</label>
          <input id="settings-prov-key-${esc(p.name)}" type="password"
                 class="settings-input" style="flex:1;min-width:160px"
                 placeholder="${authOk ? '(saved — paste to replace)' : 'paste API key'}"
                 autocomplete="off">
          <button class="btn-add" onclick="settingsProviderSetEnv('${esc(p.name)}','${esc(envKey)}',this)">Save</button>
        </div>` : '';
      const loginBtn = `<button class="btn-add" style="background:var(--surface3);color:var(--text);margin-top:8px;margin-right:6px"
                               onclick="settingsProviderTerminalLogin('${esc(p.name)}',this)">Launch terminal login</button>`;
      const refreshBtn = `<button class="btn-add" style="background:var(--surface3);color:var(--text);margin-top:8px"
                                  onclick="settingsProviderRefresh('${esc(p.name)}')">Refresh</button>`;
      authControls = keyInput + `<div>${loginBtn}${refreshBtn}</div>`;
    }

    return `
      <div class="settings-row" style="align-items:flex-start;flex-direction:column">
        <div style="display:flex;width:100%;align-items:flex-start;gap:10px">
          <div style="flex:1;min-width:0">
            <div class="settings-label" style="display:flex;align-items:center;gap:8px">
              ${esc(p.display_name)}
              <span style="font-size:10px;font-weight:600;padding:1px 7px;border-radius:10px;background:var(--surface3);color:${pillColor}">
                ${pillText}${version}
              </span>
            </div>
            ${installHint}
          </div>
          ${actionBtns}
        </div>
        ${authControls}
      </div>`;
  }).join('');

  // The global-default picker is noise when claude is the only provider.
  const defaultRow = provs.length > 1 ? `
      <div class="settings-row">
        <div>
          <div class="settings-label">Default provider</div>
          <div class="settings-hint">Used for new chats; change it per chat in the composer.</div>
        </div>
        <select class="settings-select" onchange="saveSetting('default_provider',this.value)">${provOpts}</select>
      </div>` : '';

  return `
    <div class="settings-section" id="settings-providers-section">
      ${defaultRow}
      ${provRows}
    </div>`;
}



// ── Interop: re-expose for the cross-module caller. `_renderProviderSettings`
//    is interpolated into _renderSettings() by settings-drill.js (module 6)
//    at render time (runtime) — resolves the window prop. Its provider-action
//    deps (settingsClaudeLogin / settingsClaudeAuthCheck / PROVIDER_AUTH_KEYS /
//    settingsProvider*) are window props from module 17 (provider-auth.js);
//    `_agentProviders` + `esc` are inline globals resolved at call time. ──
window._renderProviderSettings = _renderProviderSettings;
