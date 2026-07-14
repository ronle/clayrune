// ── Settings categories + drill-down (WhatsApp-style) ────────────────────────
// The settings modal is a master list of categories; tapping one drills into a
// detail screen (back arrow in the header returns). A search box filters every
// setting across all categories. _settingsActiveCat / _settingsView persist at
// module scope so re-renders (setTone/setAccent/…) keep your place.
let _settingsActiveCat = 'providers';
let _settingsActiveSub = 0;
let _settingsView = 'list'; // 'list' (categories) | 'subs' (sub-category list) | 'detail' | 'search'
const _settingsIcon = (p) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${p}</svg>`;
const SETTINGS_CATS = [
  { key:'providers',  group:1, label:'Providers',    sub:'Claude, Gemini, Codex — sign-in & API keys', icon:_settingsIcon('<circle cx="7.5" cy="15.5" r="4.5"/><path d="m10.7 12.3 9.3-9.3"/><path d="M17 5l2.5 2.5"/>') },
  { key:'agent',      group:1, label:'Agent',        sub:'Identity, model, behavior & integration',    icon:_settingsIcon('<rect x="4" y="9" width="16" height="11" rx="2"/><path d="M12 9V5.5"/><circle cx="12" cy="3.5" r="1.5"/><path d="M9 14h.01M15 14h.01"/>') },
  { key:'memory',     group:1, label:'Memory',       sub:'Auto-condense & tuning',                     icon:_settingsIcon('<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.66 3.58 3 8 3s8-1.34 8-3V5"/><path d="M4 12c0 1.66 3.58 3 8 3s8-1.34 8-3"/>') },
  { key:'appearance', group:2, label:'Appearance',   sub:'Theme, accent, density & writing style',     icon:_settingsIcon('<path d="M12 2.7l5.7 5.7a8 8 0 1 1-11.4 0z"/>') },
  { key:'connect',    group:2, label:'Connectivity', sub:'Remote access, push & mobile pairing',       icon:_settingsIcon('<path d="M5 12.5a10 10 0 0 1 14 0"/><path d="M8.5 16a5 5 0 0 1 7 0"/><path d="M2 9a15 15 0 0 1 20 0"/><path d="M12 19.5h.01"/>') },
  { key:'system',     group:2, label:'System',       sub:'Paths, advanced features, server & help',    icon:_settingsIcon('<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="2" y1="14" x2="6" y2="14"/><line x1="10" y1="8" x2="14" y2="8"/><line x1="18" y1="16" x2="22" y2="16"/>') },
];

// One master-list row: icon + title + descriptive subtitle + chevron.
const _settingsRow = (c) =>
  `<button type="button" class="settings-list-row" onclick="drillSettings('${c.key}')">`
  + `<span class="settings-list-ico">${c.icon}</span>`
  + `<span class="settings-list-txt"><span class="settings-list-title">${esc(c.label)}</span>`
  + `<span class="settings-list-sub">${esc(c.sub)}</span></span>`
  + `<span class="settings-list-chev">&#8250;</span></button>`;

function _settingsCatLabel(key) {
  const c = SETTINGS_CATS.find(x => x.key === key);
  return c ? c.label : 'Settings';
}

// Direct .settings-section children of a category's detail pane — these are the
// category's sub-items (one per layer-2 row, each its own layer-3 settings screen).
function _settingsSectionEls(cat) {
  const pane = document.querySelector('#settings-detail .settings-detail-pane[data-cat="' + cat + '"]');
  if (!pane) return [];
  return Array.from(pane.children).filter(el => el.classList && el.classList.contains('settings-section'));
}

// Build the layer-2 sub-category list for a category (one row per section title).
function _renderSettingsSubList(cat) {
  const rows = _settingsSectionEls(cat).map((s, i) => {
    const titleEl = s.querySelector('.settings-section-title');
    const t = titleEl ? titleEl.textContent.trim() : ('Section ' + (i + 1));
    return `<button type="button" class="settings-sub-row" onclick="drillSettingsSub(${i})">`
         + `<span class="settings-sub-title">${esc(t)}</span>`
         + `<span class="settings-sub-chev">&#8250;</span></button>`;
  }).join('');
  return `<div class="settings-group">${rows}</div>`;
}

// Layer 3: show only the active section in the active pane. Extracted so the async
// Remote/Push/Pair hydration can re-assert it after replacing a section via
// outerHTML (which would otherwise drop its settings-hidden class and leak in).
function _applySettingsSectionVisibility() {
  if (_settingsView !== 'detail') return;
  _settingsSectionEls(_settingsActiveCat).forEach((s, i) =>
    s.classList.toggle('settings-hidden', i !== _settingsActiveSub));
}

// Apply the current view (list / subs / detail / search) to the DOM. Called after
// every _renderSettings rebuild and on every drill/back/search transition.
function _applySettingsView() {
  const body = document.getElementById('settings-body');
  if (!body) return;
  body.dataset.view = _settingsView;

  const titleEl = document.getElementById('settings-title');
  const backBtn = document.getElementById('settings-back-btn');
  const catLabel = _settingsCatLabel(_settingsActiveCat);
  let title = 'Settings', showBack = false;
  if (_settingsView === 'subs') {
    title = catLabel; showBack = true;
  } else if (_settingsView === 'detail') {
    const sec = _settingsSectionEls(_settingsActiveCat)[_settingsActiveSub];
    const t = sec ? (sec.querySelector('.settings-section-title') || {}).textContent : '';
    title = (t && t.trim()) || catLabel;
    showBack = true;
  }
  if (titleEl) titleEl.textContent = title;
  if (backBtn) backBtn.classList.toggle('settings-hidden', !showBack);

  // Layer-2 sub-list: rebuilt + shown only in 'subs'.
  const subList = document.getElementById('settings-sub-list');
  if (subList) {
    if (_settingsView === 'subs') {
      subList.innerHTML = _renderSettingsSubList(_settingsActiveCat);
      subList.classList.remove('settings-hidden');
    } else {
      subList.classList.add('settings-hidden');
    }
  }

  const panes = document.querySelectorAll('#settings-detail .settings-detail-pane');
  if (_settingsView === 'detail') {
    panes.forEach(p => p.classList.toggle('settings-hidden', p.dataset.cat !== _settingsActiveCat));
    document.querySelectorAll('#settings-detail .settings-row').forEach(r => r.classList.remove('settings-hidden'));
    _applySettingsSectionVisibility();
  } else if (_settingsView !== 'search') {
    // list or subs → hide every pane; clear leftover row/section hiding.
    panes.forEach(p => p.classList.add('settings-hidden'));
    document.querySelectorAll('#settings-detail .settings-row, #settings-detail .settings-section')
      .forEach(el => el.classList.remove('settings-hidden'));
  }

  if (_settingsView !== 'search') {
    const noRes = document.getElementById('settings-no-results');
    if (noRes) noRes.classList.add('settings-hidden');
  }
  body.scrollTop = 0;
}

// Layer 1 → drill a category. Multi-section → sub-list (layer 2); single-section →
// straight to its settings (layer 3, no pointless one-item middle screen).
function drillSettings(cat) {
  _settingsActiveCat = cat;
  _settingsActiveSub = 0;
  _settingsView = (_settingsSectionEls(cat).length > 1) ? 'subs' : 'detail';
  const s = document.getElementById('settings-search'); if (s) s.value = '';
  mcPushSettingsNav();
  _applySettingsView();
}

// Layer 2 → drill a sub-category into its settings (layer 3).
function drillSettingsSub(idx) {
  _settingsActiveSub = idx;
  _settingsView = 'detail';
  mcPushSettingsNav();
  _applySettingsView();
}

// Move up one level (detail → subs/list, subs/search → list). Pure UI; the
// popstate handler calls this after it has consumed the history entry.
function _settingsUpUI() {
  if (_settingsView === 'detail') {
    _settingsView = (_settingsSectionEls(_settingsActiveCat).length > 1) ? 'subs' : 'list';
  } else {
    _settingsView = 'list';
  }
  const s = document.getElementById('settings-search'); if (s) s.value = '';
  _applySettingsView();
}

// On-screen back arrow: move up one level + keep the hardware-back stack in sync.
function settingsBack() {
  if (_mcSettingsNavDepth > 0) { _mcSettingsNavDepth--; _mcUnwindHistory(1); }
  _settingsUpUI();
}

// Live-filter every setting across all categories (flat results with category
// headers). Empty query returns to the category list.
function filterSettings(q) {
  q = (q || '').trim().toLowerCase();
  if (!q) {
    if (_settingsView === 'search' && _mcSettingsNavDepth > 0) { _mcSettingsNavDepth--; _mcUnwindHistory(1); }
    _settingsView = 'list';
    _applySettingsView();
    const s = document.getElementById('settings-search');
    if (s) s.focus();
    return;
  }

  if (_settingsView !== 'search') {
    _settingsView = 'search';
    mcPushSettingsNav();  // hardware/UI back exits search → category list
  }
  _applySettingsView();
  let anyMatch = false;
  document.querySelectorAll('#settings-detail .settings-detail-pane').forEach(pane => {
    let paneHasMatch = false;
    // Direct .settings-section children only (avoids :scope for WebView2 safety).
    Array.from(pane.children)
      .filter(el => el.classList && el.classList.contains('settings-section'))
      .forEach(sec => {
        const titleEl = sec.querySelector('.settings-section-title');
        const titleMatch = titleEl ? titleEl.textContent.toLowerCase().includes(q) : false;
        const rows = sec.querySelectorAll('.settings-row');
        if (rows.length === 0) {
          // Custom-content section (provider / remote / push / pair): match as a unit.
          const m = titleMatch || sec.textContent.toLowerCase().includes(q);
          sec.classList.toggle('settings-hidden', !m);
          if (m) paneHasMatch = true;
        } else {
          let rowMatch = false;
          rows.forEach(r => {
            const m = titleMatch || r.textContent.toLowerCase().includes(q);
            r.classList.toggle('settings-hidden', !m);
            if (m) rowMatch = true;
          });
          sec.classList.toggle('settings-hidden', !rowMatch);
          if (rowMatch) paneHasMatch = true;
        }
      });
    pane.classList.toggle('settings-hidden', !paneHasMatch);
    if (paneHasMatch) anyMatch = true;
  });
  const noRes = document.getElementById('settings-no-results');
  if (noRes) noRes.classList.toggle('settings-hidden', anyMatch);
}

async function openSettings() {
  const modalId = '__settings';
  if (openModals.has(modalId)) {
    const entry = openModals.get(modalId);
    if (entry.minimized) restoreModal(modalId);
    focusModal(modalId);
    return;
  }

  const win = document.createElement('div');
  win.className = 'modal-window mc-settings-window';
  win.dataset.modalId = modalId;
  _settingsView = 'list'; // fresh open always lands on the category list
  _settingsActiveSub = 0;
  _mcSettingsNavDepth = 0;
  const content = document.createElement('div');
  content.className = 'modal-content mc-settings-modal';
  _clampModalSize(content, 480);
  content.innerHTML = `
    <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:16px 22px 12px 22px">
      <span style="display:flex;align-items:center;gap:6px;min-width:0">
        <button id="settings-back-btn" class="settings-back-btn settings-hidden" onclick="settingsBack()" title="Back" aria-label="Back">&#8592;</button>
        <span id="settings-title" style="font-size:16px;font-weight:700;color:var(--text)">Settings</span>
      </span>
      <div class="modal-window-controls" style="position:static;display:flex;gap:4px">
        <button class="modal-minimize" onclick="minimizeModal('${modalId}')" title="Minimize">&#x2015;</button>
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
    </div>
    <div id="settings-body" data-view="list" style="padding:8px 22px 24px;overflow-y:auto;flex:1;min-height:0">
      <div class="process-empty">Loading...</div>
    </div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);

  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  // Open the panel right next to the sidebar "Settings" item it was launched
  // from, instead of floating in the middle of the screen.
  _positionSettingsModal(win);
  focusModal(modalId);

  // Mobile hardware-back support: this sentinel makes back close the settings
  // modal once you're at the master list. drillSettings pushes a second one so
  // back from a detail screen returns to the list first (Ron's reported bug).
  mcPushSettingsHistory();

  await _renderSettings();
  // Re-anchor now that the body has rendered and its true height is known, so a
  // tall panel is clamped to stay fully on-screen (the first pass measured the
  // short "Loading…" state).
  _positionSettingsModal(win);
}

async function _renderSettings() {
  const body = document.getElementById('settings-body');
  if (!body) return;
  // Ensure the provider list is loaded before render — the Agent Provider
  // section (incl. Claude sign-in) is built from it.
  try { await _ensureAgentProviders(); } catch (_) {}
  // Refresh remote-access status alongside config so the panel renders with
  // live state (no provider / not enrolled / enrolled).
  fetchRemoteStatus().then(s => { window._remoteState = s; refreshRemoteAccessSection(); });
  // Hydrate the local-network passcode section with live state.
  refreshLocalAccessSection();
  // Hydrate push notifications section (permission, VAPID key, subscriptions).
  // NOTE: these used to fire via setTimeout BEFORE body.innerHTML was set,
  // which raced — refreshPushSection's early-return-on-missing-element left
  // the section stuck on its placeholder. Now called after body fill below.
  let cfg;
  try {
    const res = await fetch(API_BASE + '/api/config');
    cfg = await res.json();
  } catch(e) {
    body.innerHTML = '<div class="process-empty">Failed to load settings.</div>';
    return;
  }

  // "Auto" folds the old separate Auto-pick toggle into the model picker — one
  // control for "which model", no duplication. saveModelChoice() handles the split.
  const _autoModel = !!cfg.auto_model_enabled;
  const modelOptions = [
    ['__auto__', 'Auto — pick per task'],
    ['', 'Default'],
    ['claude-fable-5', 'Fable 5'],
    ['claude-opus-4-8', 'Opus 4.8'],
    ['claude-sonnet-4-6', 'Sonnet 4.6'],
    ['claude-haiku-4-5-20251001', 'Haiku 4.5'],
  ];
  const modelSel = modelOptions.map(([v, l]) => {
    const sel = v === '__auto__' ? _autoModel : (!_autoModel && (cfg.agent_model || '') === v);
    return `<option value="${v}" ${sel ? 'selected' : ''}>${l}</option>`;
  }).join('');

  const effortOptions = [
    ['', 'Default (CLI default)'],
    ['low', 'Low'],
    ['medium', 'Medium'],
    ['high', 'High'],
    ['xhigh', 'Extra high'],
    ['max', 'Max'],
  ];
  const effortSel = effortOptions.map(([v, l]) =>
    `<option value="${v}" ${(cfg.agent_effort || '') === v ? 'selected' : ''}>${l}</option>`
  ).join('');

  const permOptions = [
    ['', 'Default'],
    ['default', 'Default (ask)'],
    ['plan', 'Plan mode'],
    ['auto-edit', 'Auto-edit'],
    ['full-auto', 'Full auto'],
  ];
  const permSel = permOptions.map(([v, l]) =>
    `<option value="${v}" ${cfg.agent_permission_mode === v ? 'selected' : ''}>${l}</option>`
  ).join('');

  const condenseModeOptions = [
    ['agent', 'Agent (legacy claude -p + Write)'],
    ['structured', 'Structured (server-applied JSON plan)'],
  ];
  const condenseModeSel = condenseModeOptions.map(([v, l]) =>
    `<option value="${v}" ${(cfg.condense_mode || 'agent') === v ? 'selected' : ''}>${l}</option>`
  ).join('');

  // Brief-replies scope folds two server bools into one 3-way control.
  // brief_replies_always_enabled supersedes the phone-only gate.
  const briefMode = cfg.brief_replies_always_enabled
    ? 'all'
    : (cfg.mobile_brief_replies_enabled ? 'phone' : 'off');

  function toggle(key, val) {
    return `<div class="settings-toggle ${val ? 'on' : ''}" onclick="toggleSetting(this,'${key}')"></div>`;
  }

  function textInput(key, val, opts = '') {
    return `<input class="settings-input" type="text" value="${esc(val || '')}" onchange="saveSetting('${key}',this.value)" ${opts}>`;
  }

  function numInput(key, val) {
    return `<input class="settings-input" type="number" value="${val || 0}" onchange="saveSetting('${key}',parseInt(this.value)||0)">`;
  }

  body.innerHTML = `
    <div class="settings-search-wrap">
      <svg class="settings-search-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>
      <input class="settings-search" id="settings-search" type="text" placeholder="Search settings&hellip;" autocomplete="off" spellcheck="false" oninput="filterSettings(this.value)">
    </div>
    <div id="settings-master">
      <button type="button" class="settings-profile" onclick="drillSettings('agent')">
        <span class="settings-avatar">${esc(((cfg.user_name || 'You').trim().charAt(0) || 'Y').toUpperCase())}</span>
        <span style="min-width:0">
          <span class="settings-profile-name">${esc(cfg.user_name || 'You')}</span>
          <span class="settings-profile-sub">Agent: ${esc(cfg.agent_name || '—')} &middot; tap to edit identity</span>
        </span>
      </button>
      <div class="settings-group">
        ${SETTINGS_CATS.filter(c => c.group === 1).map(_settingsRow).join('')}
      </div>
      <div class="settings-group">
        ${SETTINGS_CATS.filter(c => c.group === 2).map(_settingsRow).join('')}
      </div>
    </div>
    <div id="settings-detail">
      <div id="settings-sub-list" class="settings-hidden"></div>
      <div class="settings-empty settings-hidden" id="settings-no-results">No settings match your search.</div>

      <div class="settings-detail-pane settings-hidden" data-cat="providers">
      <div class="settings-cat-label">Providers</div>
    ${_renderProviderSettings(cfg)}
      </div>

      <div class="settings-detail-pane settings-hidden" data-cat="agent">
      <div class="settings-cat-label">Agent</div>
    <div class="settings-section">
      <div class="settings-section-title">Identity</div>
      <div class="settings-row">
        <div><div class="settings-label">User Name</div><div class="settings-hint">Shown in agent context</div></div>
        ${textInput('user_name', cfg.user_name)}
      </div>
      <div class="settings-row">
        <div><div class="settings-label">Agent Name</div><div class="settings-hint">Display name for agents</div></div>
        ${textInput('agent_name', cfg.agent_name)}
      </div>
    </div>

    <div class="settings-section">
      <div class="settings-section-title">Model</div>
      <div class="settings-row">
        <div><div class="settings-label">Model</div><div class="settings-hint">Default for new chats. Auto picks per task to save budget.</div></div>
        <select class="settings-select" onchange="saveModelChoice(this.value)">${modelSel}</select>
      </div>
      <div class="settings-row">
        <div><div class="settings-label">Effort</div><div class="settings-hint">How hard the model thinks per request.</div></div>
        <select class="settings-select" onchange="saveSetting('agent_effort',this.value)">${effortSel}</select>
      </div>
    </div>

    <div class="settings-section">
      <div class="settings-section-title">Behavior</div>
      <div class="settings-row">
        <div><div class="settings-label">Max turns</div><div class="settings-hint">0 = unlimited.</div></div>
        ${numInput('agent_max_turns', cfg.agent_max_turns)}
      </div>
      <div class="settings-row">
        <div><div class="settings-label">Permissions</div><div class="settings-hint">Agent tool-permission mode.</div></div>
        <select class="settings-select" onchange="saveSetting('agent_permission_mode',this.value)">${permSel}</select>
      </div>
      <div class="settings-row">
        <div>
          <div class="settings-label">Brief replies</div>
          <div class="settings-hint">Short, conversational answers that elaborate only when you ask. Choose where it applies — code and file edits are never shortened.</div>
        </div>
        <div class="mc-seg">
          <button class="${briefMode==='off'?'active':''}" onclick="setBriefRepliesMode('off')">Off</button>
          <button class="${briefMode==='phone'?'active':''}" onclick="setBriefRepliesMode('phone')">Phone</button>
          <button class="${briefMode==='all'?'active':''}" onclick="setBriefRepliesMode('all')">Everywhere</button>
        </div>
      </div>
      <div class="settings-row">
        <div>
          <div class="settings-label">Sticky settings</div>
          <div class="settings-hint">Bakes brevity + model/effort into each chat at spawn (cached, fewer tokens per turn) instead of re-sending every message. Changing one of those mid-chat resumes the agent so it takes effect. Experimental.</div>
        </div>
        ${toggle('sticky_agent_settings', cfg.sticky_agent_settings)}
      </div>
      <div class="settings-row">
        <div>
          <div class="settings-label">Live activity states</div>
          <div class="settings-hint">Shows what the agent is actually doing right now — a spinner while it thinks, dots while it writes the reply — instead of one generic bubble. Claude only. Experimental; off reverts to the plain dots.</div>
        </div>
        ${toggle('activity_states_enabled', cfg.activity_states_enabled)}
      </div>
    </div>

    <div class="settings-section">
      <div class="settings-section-title">Integration</div>
      <div class="settings-row">
        <div><div class="settings-label">Streaming (Mode B)</div><div class="settings-hint">One persistent process per chat.</div></div>
        ${toggle('use_streaming_agent', cfg.use_streaming_agent)}
      </div>
      <div class="settings-row">
        <div>
          <div class="settings-label">Remote control <span style="font-size:10px;font-weight:600;padding:1px 6px;border-radius:6px;background:var(--accent-dim);color:var(--accent);margin-left:6px;letter-spacing:.3px">EXPERIMENTAL</span></div>
          <div class="settings-hint">Headless sessions don't reach claude.ai yet — use Push notifications.</div>
        </div>
        ${toggle('agent_remote_control', cfg.agent_remote_control)}
      </div>
      <div class="settings-row">
        <div><div class="settings-label">Channels</div><div class="settings-hint">MCP plugin channels (e.g. plugin:telegram@…).</div></div>
        ${textInput('agent_channels', cfg.agent_channels)}
      </div>
    </div>
      </div>

      <div class="settings-detail-pane settings-hidden" data-cat="memory">
      <div class="settings-cat-label">Memory</div>
    <div class="settings-section">
      <div class="settings-section-title">Auto-condense</div>
      <div class="settings-row">
        <div><div class="settings-label">Auto-condense</div><div class="settings-hint">Condense large memory files automatically.</div></div>
        ${toggle('condense_enabled', cfg.condense_enabled)}
      </div>
      <div class="settings-row">
        <div><div class="settings-label">Threshold (KB)</div><div class="settings-hint">Condense when memory exceeds this.</div></div>
        ${numInput('condense_threshold_kb', cfg.condense_threshold_kb)}
      </div>
    </div>

    <div class="settings-section">
      <div class="settings-section-title">Tuning</div>
      <div class="settings-row">
        <div><div class="settings-label">Model</div><div class="settings-hint">Housekeeping model (blank = default).</div></div>
        ${textInput('condense_model', cfg.condense_model)}
      </div>
      <div class="settings-row">
        <div><div class="settings-label">Executor</div><div class="settings-hint">Structured = one server-side JSON call (no agent).</div></div>
        <select class="settings-select" onchange="saveSetting('condense_mode',this.value)">${condenseModeSel}</select>
      </div>
    </div>

    <div class="settings-section">
      <div class="settings-section-title">Learning</div>
      <div class="settings-row">
        <div>
          <div class="settings-label">Exploration readback</div>
          <div class="settings-hint">Feeds past investigations back into new sessions so the agent doesn't re-derive what it already worked out. This is the one learned artifact that reaches an agent WITHOUT you promoting it — turn it off to stop the learning loop feeding itself. Takes effect on the next message; no restart.</div>
        </div>
        ${toggle('exploration_readback_enabled', cfg.exploration_readback_enabled)}
      </div>
      <div class="settings-row">
        <div><div class="settings-label">Explorations per session</div><div class="settings-hint">How many past investigations to surface (default 2).</div></div>
        ${numInput('exploration_read_floor_topk', cfg.exploration_read_floor_topk)}
      </div>
    </div>
      </div>

      <div class="settings-detail-pane settings-hidden" data-cat="appearance">
      <div class="settings-cat-label">Appearance</div>
    <div class="settings-section">
      <div class="settings-section-title">Theme &amp; display</div>
      <div class="settings-row">
        <div><div class="settings-label">Theme</div><div class="settings-hint">Warm and Editorial are light themes.</div></div>
        <div class="mc-seg" id="mc-tone-seg">
          <button class="${currentTone==='dark'?'active':''}" onclick="setTone('dark')">Dark</button>
          <button class="${currentTone==='warm'?'active':''}" onclick="setTone('warm')">Warm</button>
          <button class="${currentTone==='editorial'?'active':''}" onclick="setTone('editorial')">Editorial</button>
        </div>
      </div>
      <div class="settings-row">
        <div><div class="settings-label">Accent color</div><div class="settings-hint">Used for highlights, primary buttons, and active states</div></div>
        <div class="mc-accent-row" id="mc-accent-row">
          <button class="mc-accent-pill ${!currentAccent?'active':''}" onclick="setAccent('')"><span class="mc-accent-swatch" style="background:#5b9ef5"></span>Default</button>
          <button class="mc-accent-pill ${currentAccent==='sunset'?'active':''}" onclick="setAccent('sunset')"><span class="mc-accent-swatch" style="background:#e8824a"></span>Sunset</button>
          <button class="mc-accent-pill ${currentAccent==='rose'?'active':''}" onclick="setAccent('rose')"><span class="mc-accent-swatch" style="background:#d96480"></span>Rose</button>
          <button class="mc-accent-pill ${currentAccent==='lilac'?'active':''}" onclick="setAccent('lilac')"><span class="mc-accent-swatch" style="background:#8a7ce0"></span>Lilac</button>
          <button class="mc-accent-pill ${currentAccent==='lagoon'?'active':''}" onclick="setAccent('lagoon')"><span class="mc-accent-swatch" style="background:#4fa89a"></span>Lagoon</button>
          <button class="mc-accent-pill ${currentAccent==='ink'?'active':''}" onclick="setAccent('ink')"><span class="mc-accent-swatch" style="background:#6b7286"></span>Ink</button>
        </div>
      </div>
      <div class="settings-row">
        <div><div class="settings-label">Density</div><div class="settings-hint">How much breathing room between things</div></div>
        <div class="mc-seg">
          <button class="${document.body.classList.contains('compact')?'':'active'}" onclick="setDensity('cozy')">Cozy</button>
          <button class="${document.body.classList.contains('compact')?'active':''}" onclick="setDensity('compact')">Compact</button>
        </div>
      </div>
      <div class="settings-row">
        <div><div class="settings-label">Writing style</div><div class="settings-hint">${currentVoice==='casual' ? 'Labels read like a friend helping out.' : 'Clear, neutral labels throughout the UI.'}</div></div>
        <div class="mc-seg">
          <button class="${currentVoice==='casual'?'active':''}" onclick="setVoice('casual')">Casual</button>
          <button class="${currentVoice==='pro'?'active':''}" onclick="setVoice('pro')">Professional</button>
        </div>
      </div>
      <div class="settings-row">
        <div><div class="settings-label">Open surfaces as</div><div class="settings-hint">Windows float over the dashboard; Pages fill the main area (desktop)</div></div>
        <div class="mc-seg">
          <button class="${(typeof _surfaceMode!=='undefined'&&_surfaceMode==='windows')||typeof _surfaceMode==='undefined'?'active':''}" onclick="setSurfaceMode('windows')">Windows</button>
          <button class="${typeof _surfaceMode!=='undefined'&&_surfaceMode==='pages'?'active':''}" onclick="setSurfaceMode('pages')">Pages</button>
        </div>
      </div>
      <div class="settings-row">
        <div><div class="settings-label">Agent replies</div><div class="settings-hint">Bubbles show each paragraph as its own card; Flow runs the reply together as one block. Your messages stay bubbles either way.</div></div>
        <div class="mc-seg">
          <button class="${typeof _chatStyle==='undefined'||_chatStyle!=='flow'?'active':''}" onclick="setChatStyle('bubbles')">Bubbles</button>
          <button class="${typeof _chatStyle!=='undefined'&&_chatStyle==='flow'?'active':''}" onclick="setChatStyle('flow')">Flow</button>
        </div>
      </div>
    </div>

    <div class="settings-section">
      <div class="settings-section-title">Background</div>
      <div class="settings-row">
        <div><div class="settings-label">Dashboard background</div><div class="settings-hint">Personalize the space behind your projects. Saved on this device.</div></div>
        <div class="mc-seg" id="mc-bg-seg">
          <button class="${bgMode==='default'?'active':''}" onclick="setBgMode('default')">Theme</button>
          <button class="${bgMode==='color'?'active':''}" onclick="setBgMode('color')">Color</button>
          <button class="${bgMode==='image'?'active':''}" onclick="setBgMode('image')">Image</button>
        </div>
      </div>
      ${bgMode==='color' ? `
      <div class="settings-row">
        <div><div class="settings-label">Pick a color</div><div class="settings-hint">A solid color behind the dashboard</div></div>
        <input type="color" value="${esc(bgColor)}" oninput="setBgColor(this.value)" style="width:48px;height:32px;border:1px solid var(--border);border-radius:8px;background:var(--surface);cursor:pointer;padding:2px">
      </div>` : ''}
      ${bgMode==='image' ? `
      <div class="settings-row" style="align-items:flex-start">
        <div><div class="settings-label">Gallery</div><div class="settings-hint">Built-in patterns tuned to the themes</div></div>
        <div class="mc-bg-gallery">
          ${BUILTIN_BGS.map(b => `
          <button class="mc-bg-thumb ${localStorage.getItem('mc_bg_image')===_builtinBgUrl(b.id)?'active':''}" onclick="setBuiltinBg('${b.id}')" title="${esc(b.label)}">
            <img src="${_builtinBgUrl(b.id, true)}" alt="${esc(b.label)}">
          </button>`).join('')}
        </div>
      </div>
      <div class="settings-row">
        <div><div class="settings-label">Your image</div><div class="settings-hint">${_bgIsBuiltin() ? 'Using a built-in background — choose a file to use your own.' : localStorage.getItem('mc_bg_image') ? 'An image is set for this device.' : 'No image chosen yet — pick one or use the gallery.'}</div></div>
        <div style="display:flex;gap:8px;align-items:center">
          <button class="btn-dispatch" onclick="pickBgImage()">${localStorage.getItem('mc_bg_image') && !_bgIsBuiltin() ? 'Replace…' : 'Choose…'}</button>
          ${localStorage.getItem('mc_bg_image') ? `<button class="btn-dispatch" style="background:var(--surface3);border-color:var(--border2);color:var(--text)" onclick="clearBgImage()">Remove</button>` : ''}
        </div>
      </div>
      ${localStorage.getItem('mc_bg_image') ? `
      <div class="settings-row">
        <div><div class="settings-label">Dim for readability</div><div class="settings-hint">Tints the image toward your theme so text stays legible</div></div>
        <div style="display:flex;gap:10px;align-items:center;min-width:170px">
          <input type="range" min="0" max="80" value="${bgDim}" oninput="setBgDim(this.value)" style="flex:1;accent-color:var(--accent)">
          <span id="mc-bg-dim-val" style="font-size:12px;color:var(--text-dim);min-width:34px;text-align:right">${bgDim}%</span>
        </div>
      </div>
      <div class="settings-row" style="align-items:flex-start">
        <div><div class="settings-label">Crop &amp; framing</div><div class="settings-hint">Drag the box to move, drag the corner (or scroll) to zoom. The box is what stays in view — it adapts to every screen size.</div></div>
        <div style="display:flex;flex-direction:column;gap:9px;width:236px;max-width:100%">
          <div class="mc-crop" id="mc-crop" onwheel="_bgCropWheel(event)">
            <img id="mc-crop-img" alt="" src="${esc(localStorage.getItem('mc_bg_image') || '')}" onload="_bgCropInit()">
            <div class="mc-crop-box" id="mc-crop-box" onpointerdown="_bgCropDragStart(event,'move')">
              <div class="mc-crop-handle" onpointerdown="_bgCropDragStart(event,'resize')"></div>
            </div>
          </div>
          <button class="btn-dispatch" style="background:var(--surface3);border-color:var(--border2);color:var(--text);align-self:flex-end;padding:5px 12px" onclick="resetBgFraming()">Reset framing</button>
        </div>
      </div>` : ''}` : ''}
      <input type="file" id="mc-bg-file" accept="image/*" style="display:none" onchange="onBgImageChosen(this)">
    </div>

    <div class="settings-section">
      <div class="settings-section-title">Interface</div>
      <div class="settings-row">
        <div><div class="settings-label">Enter Key Behavior</div><div class="settings-hint">How Enter works in agent input</div></div>
        <select class="settings-select" onchange="setEnterMode(this.value)">
          <option value="ctrl-enter" ${enterKeyMode==='ctrl-enter'?'selected':''}>Ctrl+Enter sends</option>
          <option value="enter" ${enterKeyMode==='enter'?'selected':''}>Enter sends</option>
        </select>
      </div>
    </div>
      </div>

      <div class="settings-detail-pane settings-hidden" data-cat="connect">
      <div class="settings-cat-label">Connectivity</div>
    ${localAccessSettingsHTML()}

    ${remoteAccessSettingsHTML()}

    ${pushNotificationsSettingsHTML()}

    ${mobilePairingSettingsHTML()}
      </div>

      <div class="settings-detail-pane settings-hidden" data-cat="system">
      <div class="settings-cat-label">System</div>
    <div class="settings-section">
      <div class="settings-section-title">Paths &amp; Server</div>
      <div class="settings-row">
        <div><div class="settings-label">Projects Base</div><div class="settings-hint">Root directory for project file browsing</div></div>
        ${textInput('projects_base', cfg.projects_base)}
      </div>
      <div class="settings-row">
        <div><div class="settings-label">Auto Workspace Base</div><div class="settings-hint">Folder where new projects without a path get their dedicated subfolder</div></div>
        ${textInput('auto_workspace_base', cfg.auto_workspace_base || '')}
      </div>
      <div class="settings-row">
        <div><div class="settings-label">Shared Rules Path</div><div class="settings-hint">Path to global SHARED_RULES.md</div></div>
        ${textInput('shared_rules_path', cfg.shared_rules_path)}
      </div>
      <div class="settings-row">
        <div><div class="settings-label">Port</div><div class="settings-hint">Requires restart to take effect</div></div>
        ${numInput('port', cfg.port)}
      </div>
    </div>

    <div class="settings-section">
      <div class="settings-section-title">Advanced features</div>
      <div class="settings-hint" style="margin-bottom:10px">Off by default for a simpler view. Enable only what you need.</div>
      ${ADV_FEATURES.map(f => `
        <div class="settings-row">
          <div><div class="settings-label">${esc(f.label)}</div><div class="settings-hint">${esc(f.hint)}</div></div>
          <label class="mc-seg" style="cursor:pointer">
            <input type="checkbox" ${advancedFlags[f.key] ? 'checked' : ''}
              onchange="setAdvancedFlag('${esc(f.key)}',this.checked)"
              style="width:16px;height:16px;cursor:pointer;accent-color:var(--accent)">
          </label>
        </div>`).join('')}
    </div>

    <div class="settings-section">
      <div class="settings-section-title">Server</div>
      <div class="settings-row">
        <div><div class="settings-label">Power</div><div class="settings-hint">Restart to pick up new code or config (open conversations are restored after reconnect), or shut down the Clayrune server completely.</div></div>
        <button class="btn-dispatch" style="background:var(--surface3);border-color:var(--border2);color:var(--text)" onclick="openPowerDialog()">Restart / shut down…</button>
      </div>
      <div class="settings-row" style="margin-top:8px">
        <div>
          <div class="settings-label">Update Clayrune</div>
          <div class="settings-hint" id="update-status-hint">Checking for updates...</div>
        </div>
        <button class="btn-dispatch" id="update-btn" onclick="performClayruneUpdate()" disabled>Update</button>
      </div>
    </div>

    <div class="settings-section">
      <div class="settings-section-title">Help</div>
      <div class="settings-row">
        <div><div class="settings-label">Interface Tour</div><div class="settings-hint">Walk through the main features</div></div>
        <button class="btn-dispatch" onclick="closeModalById('__settings');startWalkthrough()">Take Tour</button>
      </div>
    </div>
      </div>
    </div>`;

  // Now that #update-status-hint and #update-btn are in the DOM, kick off the
  // git-status check. (Earlier we fired this with a 100 ms setTimeout BEFORE
  // body.innerHTML was set, which raced and left the row stuck on the
  // "Checking for updates..." placeholder.)
  refreshUpdateStatus();

  // Same lesson — hydrate the push + mobile-pair sections AFTER the DOM has
  // their containers. Both functions early-return if their target element
  // isn't found, so the placeholder used to get stuck.
  try { refreshPushSection(); } catch (_) {}
  try { refreshMobilePairingSection(); } catch (_) {}

  // Restore the master/detail/search view (persisted across re-renders so that
  // setTone/setAccent/etc. don't bounce you back to the list mid-edit).
  _applySettingsView();
}


// ── Interop: page-facing surface ─────────────────────────────────────────────────────
// Outside callers + region-generated inline handlers resolve these against
// the global object at call time (modules 2–5 precedent).
window.openSettings = openSettings;               // interop: sidebarNav('settings') + palette action + settingsProviderRefresh fallback
window._renderSettings = _renderSettings;         // interop: setTone/setAccent/setDensity/setVoice/bg-setter/setBriefRepliesMode re-render sites (×9)
window._applySettingsSectionVisibility = _applySettingsSectionVisibility; // interop: refreshLocalAccessSection + refreshRemoteAccessSection re-assert after outerHTML hydration
window._settingsUpUI = _settingsUpUI;             // interop: popstate hardware-back handler (one level up per consumed sentinel)
window.drillSettings = drillSettings;             // interop: master-list rows + profile card (generated onclick)
window.drillSettingsSub = drillSettingsSub;       // interop: layer-2 sub-rows (generated onclick)
window.settingsBack = settingsBack;               // interop: header back arrow (generated onclick)
window.filterSettings = filterSettings;           // interop: search box (generated oninput)
