// ── System status (CC /status equivalent) ───────────────────────────────────
// Surfaces the same info Claude Code's `/status` slash command shows: model,
// CLI version, auth source, rate-limit window state, connected MCP servers.
// Backend: see `_capture_system_init` + `/api/system/status[/refresh]` in
// server.py. Cache is populated for free by every agent session's init
// message; the Refresh button triggers an active one-shot claude spawn.
//
// Popover layout mirrors the CLI `/status` tabs:
//   Status  — live health: rate limit, model, CLI version, auth
//   Config  — permission mode, output style, fast mode, cwd, memory, counts
//   MCP     — per-server connection list
//   Usage   — authoritative subscription usage % (5h / weekly / per-model)
//             from the OAuth endpoint (/api/system/usage → usage_limits, the
//             same numbers the CLI `/usage` shows), plus local token
//             aggregates (today / week / top models) from
//             ~/.claude/stats-cache.json. Falls back to the header-derived
//             rate-limit window when the OAuth fetch is unavailable; still
//             links out to claude.ai as a cross-check.
let systemStatusCache = null;
let systemUsageCache = null;
let _sysStatusPopoverOpen = false;
let _sysStatusRefreshing = false;
let _sysStatusActiveTab = 'status';  // status | config | mcp | usage
let _sysUsageFetching = false;

async function fetchSystemStatus() {
  try {
    const res = await fetchFailFast(API_BASE + '/api/system/status');
    if (!res.ok) return;
    systemStatusCache = await res.json();
    renderSysStatusPill();
    _rerenderSysStatusSurfaces();
  } catch { /* network blip — keep last cache */ }
}

function _ssRateLimitHealth(rl) {
  // Map rate_limit_info into {color, label, message}. Color drives the pill
  // dot. Label is the compact pill text. Message is the popover detail line.
  if (!rl || !rl.status) return { color: 'idle', label: '—', message: 'No data yet' };
  const isUsingOverage = !!rl.isUsingOverage;
  const status = (rl.status || '').toLowerCase();
  const overage = (rl.overageStatus || '').toLowerCase();
  if (status === 'blocked' || status === 'denied') {
    return { color: 'bad', label: 'BLK', message: 'Rate limited' };
  }
  if (isUsingOverage || overage === 'using_overage') {
    return { color: 'warn', label: 'OVR', message: 'Using overage allowance' };
  }
  // Within 20% of the window reset → amber as a soft warning. Cheap proxy
  // for "approaching limit" since we don't have a usage-percentage signal.
  const resets = Number(rl.resetsAt || 0);
  if (resets > 0) {
    const remainingMs = (resets * 1000) - Date.now();
    const remainingMin = Math.max(0, Math.floor(remainingMs / 60000));
    // 5-hour window = 300 min. Inside 60 min of reset = amber.
    const win = (rl.rateLimitType || '').includes('five') ? 300 : 60;
    const compact = remainingMin >= 60
      ? `${Math.floor(remainingMin / 60)}h${remainingMin % 60 ? (remainingMin % 60) + 'm' : ''}`
      : remainingMin + 'm';
    if (remainingMin < win * 0.2) {
      return { color: 'warn', label: compact, message: 'Approaching reset' };
    }
    return { color: 'ok', label: compact, message: 'OK · ' + compact + ' to reset' };
  }
  return { color: 'ok', label: 'OK', message: 'OK' };
}

function _ssRelTime(iso) {
  if (!iso) return 'never';
  try {
    const dt = new Date(iso);
    const sec = Math.max(0, Math.floor((Date.now() - dt.getTime()) / 1000));
    if (sec < 60) return sec + 's ago';
    if (sec < 3600) return Math.floor(sec / 60) + 'm ago';
    if (sec < 86400) return Math.floor(sec / 3600) + 'h ago';
    return Math.floor(sec / 86400) + 'd ago';
  } catch { return ''; }
}

function renderSysStatusPill() {
  const pill = document.getElementById('sys-status-pill');
  const label = document.getElementById('sys-status-pill-label');
  if (!pill || !label) return;
  pill.classList.remove('ok', 'warn', 'bad', 'stale');
  const s = systemStatusCache;
  if (!s || !s.captured_at) {
    label.textContent = '—';
    return;
  }
  const h = _ssRateLimitHealth(s.rate_limit_info);
  if (h.color === 'ok') pill.classList.add('ok');
  else if (h.color === 'warn') pill.classList.add('warn');
  else if (h.color === 'bad') pill.classList.add('bad');
  label.textContent = h.label;
  // Cache older than 30 min → dim the pill so users know it's not live.
  if ((s.cache_age_seconds || 0) > 1800) pill.classList.add('stale');
}

function _ssFormatTokens(n) {
  n = Number(n) || 0;
  if (n >= 1e9) return (n / 1e9).toFixed(2) + 'B';
  if (n >= 1e6) return (n / 1e6).toFixed(2) + 'M';
  if (n >= 1e3) return (n / 1e3).toFixed(1) + 'k';
  return String(n);
}

function _ssShortenModel(m) {
  if (!m) return '—';
  return String(m).replace(/^claude-/, '').replace(/-\d{8}$/, '');
}

// Per-provider install/auth health — makes every registered agent provider
// visible in the Status popup, not just claude. Returns '' for claude-only
// deployments (nothing extra to show).
function _renderProviderHealthRows() {
  const provs = _agentProviders || [];
  if (provs.length <= 1) return '';
  const rows = provs.map(pr => {
    const installed = !!pr.installed;
    const authOk = pr.auth_status === 'ok';
    const color = !installed ? 'var(--text-faint)'
                : authOk     ? 'var(--green)'
                :              'var(--amber)';
    const state = !installed ? 'not installed'
                : authOk     ? 'ready'
                : (pr.auth_status === 'not_logged_in' ? 'not signed in' : 'installed');
    const ver = pr.version ? ' · v' + esc(pr.version) : '';
    return `<div class="ssp-row"><span class="ssp-k">${esc(pr.display_name)}</span><span class="ssp-v" style="color:${color}">${state}${ver}</span></div>`;
  }).join('');
  return `<div class="ssp-section-head" style="margin-top:10px">Agent providers</div>${rows}`;
}

function _renderStatusTab(s, rl, h) {
  const colorClass = h.color === 'ok' ? 'green' : (h.color === 'warn' ? 'amber' : (h.color === 'bad' ? 'red' : ''));
  const resetWhen = rl.resetsAt ? new Date(rl.resetsAt * 1000).toLocaleTimeString() : '';
  const empty = !s.captured_at;
  const authDisplay = (s.apiKeySource && s.apiKeySource !== 'none')
    ? s.apiKeySource
    : 'OAuth / keychain';
  // Rate-limit / overage / model telemetry below is Claude-Code-specific —
  // the cache is fed only by claude stream readers. Other providers don't
  // expose equivalent account telemetry, so the section is headed honestly
  // and every provider's install/auth health is listed separately below.
  const multiProvider = (_agentProviders || []).length > 1;
  return `
    ${multiProvider ? '<div class="ssp-section-head">Claude Code</div>' : ''}
    <div class="ssp-row"><span class="ssp-k">Rate limit</span><span class="ssp-v ${colorClass}">${empty ? '—' : (h.message + (resetWhen ? ' · resets ' + resetWhen : ''))}</span></div>
    <div class="ssp-row"><span class="ssp-k">Window</span><span class="ssp-v">${esc((rl.rateLimitType || '').replace('_', '-') || '—')}</span></div>
    <div class="ssp-row"><span class="ssp-k">Overage</span><span class="ssp-v">${rl.isUsingOverage ? 'using overage' : esc(rl.overageStatus || '—')}</span></div>
    <div class="ssp-row"><span class="ssp-k">Model</span><span class="ssp-v">${esc(s.model || '—')}</span></div>
    <div class="ssp-row"><span class="ssp-k">Agent version</span><span class="ssp-v">${esc(s.claude_code_version || '—')}</span></div>
    <div class="ssp-row"><span class="ssp-k">Auth</span><span class="ssp-v">${esc(authDisplay)}</span></div>
    ${_renderProviderHealthRows()}
  `;
}

function _renderConfigTab(s) {
  const memPaths = Array.isArray(s.memory_paths) ? s.memory_paths : [];
  const memDisplay = memPaths.length === 0
    ? '—'
    : (memPaths.length === 1 ? memPaths[0] : memPaths.length + ' paths');
  const fastDisplay = (s.fast_mode_state && s.fast_mode_state !== 'off') ? s.fast_mode_state : 'off';
  const sspCaps = _getProviderCaps(s.provider || 'claude');
  return `
    <div class="ssp-row"><span class="ssp-k">Permission mode</span><span class="ssp-v">${esc(s.permissionMode || '—')}</span></div>
    <div class="ssp-row"><span class="ssp-k">Output style</span><span class="ssp-v">${esc(s.output_style || '—')}</span></div>
    <div class="ssp-row"><span class="ssp-k">Fast mode</span><span class="ssp-v">${esc(fastDisplay)}</span></div>
    <div class="ssp-row"><span class="ssp-k">Analytics</span><span class="ssp-v">${s.analytics_disabled ? 'disabled' : 'enabled'}</span></div>
    <div class="ssp-row"><span class="ssp-k">Working dir</span><span class="ssp-v" title="${esc(s.cwd || '')}">${esc(s.cwd || '—')}</span></div>
    <div class="ssp-row"><span class="ssp-k">Memory</span><span class="ssp-v" title="${esc(memPaths.join('\n'))}">${esc(memDisplay)}</span></div>
    <div class="ssp-row"><span class="ssp-k">Tools</span><span class="ssp-v">${s.tools_count || 0}</span></div>
    ${sspCaps.supports_skills ? `<div class="ssp-row"><span class="ssp-k">Skills</span><span class="ssp-v">${s.skills_count || 0}</span></div>` : ''}
    <div class="ssp-row"><span class="ssp-k">Agents</span><span class="ssp-v">${s.agents_count || 0}</span></div>
    <div class="ssp-row"><span class="ssp-k">Plugins</span><span class="ssp-v">${s.plugins_count || 0}</span></div>
    <div class="ssp-row"><span class="ssp-k">Slash commands</span><span class="ssp-v">${s.slash_commands_count || 0}</span></div>
  `;
}

function _renderMcpTab(s) {
  const mcp = Array.isArray(s.mcp_servers) ? s.mcp_servers : [];
  const mcpConnected = mcp.filter(m => (m.status || '').toLowerCase() === 'connected').length;
  const mcpTotal = mcp.length;
  if (mcpTotal === 0) {
    return '<div class="ssp-empty">No MCP servers configured for the active agent session.</div>';
  }
  const summary = `${mcpConnected}/${mcpTotal} connected`;
  return `
    <div class="ssp-section-head">MCP servers · ${esc(summary)}</div>
    <div class="ssp-mcp-list">${mcp.map(m => {
      const st = (m.status || 'unknown').toLowerCase();
      return `<div class="ssp-mcp-row"><span class="ssp-mcp-name">${esc(m.name || '—')}</span><span class="ssp-mcp-status ${esc(st)}">${esc(st)}</span></div>`;
    }).join('')}</div>
  `;
}

function _renderMcActivitySection() {
  // MC-scoped totals. Reads `mcUsageCache` from /api/usage (multi-provider).
  const u = mcUsageCache;
  const total = u ? (u.total || {}) : {};
  const tokens = u ? formatTokens(total.total_tokens || u.total_tokens || 0) : '—';
  const costNum = u ? (total.cost_usd !== undefined ? total.cost_usd : u.cost_usd) : null;
  const costStr = costNum != null ? formatCost(costNum) : '';
  const sessions = u ? (total.sessions || u.total_sessions || 0) : 0;
  const rangeBtns = TOKEN_MODES.map(m => `
    <button class="ssp-range-btn ${tokenCounterMode === m.id ? 'active' : ''}" onclick="setTokenMode('${esc(m.id)}')">${esc(m.label)}</button>
  `).join('');

  // Per-provider breakdown (shown when >1 provider has activity)
  const byProv = u ? (u.by_provider || {}) : {};
  const provEntries = Object.entries(byProv).filter(([, b]) => (b.sessions || 0) > 0);
  const multiProvider = provEntries.length > 1;
  const provBreakdown = multiProvider ? `
    <div class="ssp-section-head" style="margin-top:8px">By provider</div>
    ${provEntries.map(([pname, b]) => {
      const pTok = formatTokens(b.total_tokens || 0);
      const pCost = b.cost_usd != null ? formatCost(b.cost_usd) : '';
      const provBadge = _providerBadge(pname) || `<span class="provider-badge prov-${esc(pname)}">${esc(pname)}</span>`;
      return `<div class="ssp-row">${provBadge}<span class="ssp-v">${pTok} tok${pCost ? ' · ' + esc(pCost) : ''} · ${b.sessions || 0} sess</span></div>`;
    }).join('')}
  ` : '';

  return `
    <div class="ssp-section-head">Clayrune activity</div>
    <div class="ssp-range-row">${rangeBtns}</div>
    <div class="ssp-bignum">${tokens} tok<span class="ssp-bignum-cost">${costStr ? esc(costStr) : ''}</span></div>
    <div class="ssp-bignum-sub">${sessions.toLocaleString()} agent ${sessions === 1 ? 'session' : 'sessions'} in this range</div>
    ${provBreakdown}
  `;
}

function _ssUntil(iso) {
  // Future-relative "resets in 3h12m" for an ISO timestamp.
  if (!iso) return '';
  try {
    const ms = new Date(iso).getTime() - Date.now();
    if (ms <= 0) return 'resetting';
    const m = Math.floor(ms / 60000);
    if (m < 60) return 'resets in ' + m + 'm';
    const h = Math.floor(m / 60);
    if (h < 24) return 'resets in ' + h + 'h' + (m % 60 ? (m % 60) + 'm' : '');
    const d = Math.floor(h / 24);
    return 'resets in ' + d + 'd' + (h % 24 ? (h % 24) + 'h' : '');
  } catch { return ''; }
}

function _ssUsageLimitBar(label, win) {
  // One progress bar for an OAuth usage window {utilization, resets_at}.
  // Returns '' for null windows (e.g. per-model blocks when unused).
  if (!win || win.utilization == null) return '';
  const pct = Math.max(0, Math.min(100, Number(win.utilization) || 0));
  const color = pct >= 90 ? 'var(--red)' : pct >= 70 ? 'var(--amber)' : 'var(--green)';
  const until = _ssUntil(win.resets_at);
  return `
    <div style="margin:5px 0 9px">
      <div style="display:flex;justify-content:space-between;align-items:baseline;margin-bottom:3px">
        <span class="ssp-k">${esc(label)}</span>
        <span class="ssp-v">${pct.toFixed(0)}%${until ? ' · ' + esc(until) : ''}</span>
      </div>
      <div style="height:6px;border-radius:3px;background:var(--border);overflow:hidden">
        <div style="height:100%;width:${pct}%;background:${color};border-radius:3px"></div>
      </div>
    </div>`;
}

function _renderUsageTab() {
  // Two layers stacked top-to-bottom:
  //   1. MC activity — tokens/cost/sessions launched THROUGH Mission Control,
  //      filterable by today/week/month/all. Source: /api/usage.
  //   2. Claude Code activity — authoritative subscription usage % (OAuth
  //      endpoint) + tokens by model from ~/.claude/stats-cache.json
  //      (covers ALL claude usage on this machine, not just MC-launched).
  const mcSection = _renderMcActivitySection();
  const u = systemUsageCache;
  if (_sysUsageFetching && !u) return mcSection + '<div class="ssp-empty">Loading agent usage stats…</div>';
  if (!u) return mcSection + '<div class="ssp-empty">Agent usage stats not loaded yet.</div>';
  if (u.available === false) {
    return mcSection + `
      <div class="ssp-empty">Usage stats not available (${esc(u.reason || 'provider may not support stats-cache')}).</div>
      <a class="ssp-link" href="https://claude.ai/settings/usage" target="_blank" rel="noopener">Open canonical usage page →</a>
    `;
  }
  const rl = u.rate_limit_info || {};
  const resetWhen = rl.resetsAt ? new Date(rl.resetsAt * 1000).toLocaleString() : '';
  const today = u.today || {};
  const week = u.week || {};
  const month = u.month || {};
  const top = Array.isArray(u.top_models) ? u.top_models : [];

  const renderModelRows = (obj) => {
    const entries = Object.entries(obj).sort((a, b) => (b[1] || 0) - (a[1] || 0));
    if (entries.length === 0) return '<div class="ssp-empty">No activity recorded.</div>';
    return entries.map(([m, t]) => `
      <div class="ssp-token-row">
        <span class="ssp-token-name">${esc(_ssShortenModel(m))}</span>
        <span class="ssp-token-val">${_ssFormatTokens(t)} tok</span>
      </div>
    `).join('');
  };

  // Single period section driven by the top-level filter — no duplicate sections.
  const periodLabel = { all: 'All time', today: 'Today', week: 'This week', month: 'This month' }[tokenCounterMode] || 'All time';
  let periodHTML;
  if (tokenCounterMode === 'all') {
    periodHTML = top.length === 0
      ? '<div class="ssp-empty">No model usage recorded.</div>'
      : top.map(t => `
          <div class="ssp-token-row">
            <span class="ssp-token-name">${esc(_ssShortenModel(t.model))}</span>
            <span class="ssp-token-val">${_ssFormatTokens(t.tokens)} tok</span>
          </div>
        `).join('');
  } else {
    const periodData = tokenCounterMode === 'today' ? today : tokenCounterMode === 'week' ? week : month;
    periodHTML = renderModelRows(periodData);
  }

  const dataDate = u.last_data_date || u.last_computed_date || '—';

  const claudeMultiNote = (_agentProviders || []).length > 1
    ? '<div class="ssp-hint-line">Covers all Claude Code usage on this machine — not just Mission Control. Other providers don\'t publish a stats cache; see Clayrune activity above for cross-provider totals.</div>'
    : '';

  // Authoritative subscription usage %, straight from the OAuth endpoint
  // (/api/system/usage → usage_limits) — the same numbers the CLI `/usage`
  // shows. Falls back to the header-derived window (type/reset/status, no %)
  // when the OAuth fetch was unavailable (missing/expired token, offline).
  const lim = u.usage_limits || null;
  let limitsHTML;
  if (lim && (lim.five_hour || lim.seven_day || lim.seven_day_opus || lim.seven_day_sonnet)) {
    const extra = lim.extra_usage || {};
    limitsHTML = `
    <div class="ssp-section-head" style="margin-top:6px">Usage limits</div>
    ${_ssUsageLimitBar('Session · 5-hour', lim.five_hour)}
    ${_ssUsageLimitBar('Weekly · all models', lim.seven_day)}
    ${_ssUsageLimitBar('Weekly · Opus', lim.seven_day_opus)}
    ${_ssUsageLimitBar('Weekly · Sonnet', lim.seven_day_sonnet)}
    ${extra.is_enabled ? `<div class="ssp-row"><span class="ssp-k">Extra usage</span><span class="ssp-v">${(Number(extra.utilization) || 0).toFixed(0)}%${extra.monthly_limit != null ? ' of $' + extra.monthly_limit : ''}</span></div>` : ''}`;
  } else {
    limitsHTML = `
    <div class="ssp-section-head" style="margin-top:6px">Current rate-limit window</div>
    <div class="ssp-row"><span class="ssp-k">Type</span><span class="ssp-v">${esc((rl.rateLimitType || '').replace('_', '-') || '—')}</span></div>
    <div class="ssp-row"><span class="ssp-k">Resets</span><span class="ssp-v">${esc(resetWhen || '—')}</span></div>
    <div class="ssp-row"><span class="ssp-k">Status</span><span class="ssp-v">${esc(rl.status || '—')}</span></div>`;
  }

  return mcSection + `
    <div class="ssp-section-head">Claude Code · machine-wide</div>
    ${claudeMultiNote}
    ${limitsHTML}

    <div class="ssp-section-head">${esc(periodLabel)} · tokens by model</div>
    ${periodHTML}

    <div class="ssp-section-head">Totals</div>
    <div class="ssp-row"><span class="ssp-k">Sessions</span><span class="ssp-v">${(u.total_sessions || 0).toLocaleString()}</span></div>
    <div class="ssp-row"><span class="ssp-k">Messages</span><span class="ssp-v">${(u.total_messages || 0).toLocaleString()}</span></div>
    <div class="ssp-row"><span class="ssp-k">Data through</span><span class="ssp-v">${esc(dataDate)}</span></div>

    <a class="ssp-link" href="https://claude.ai/settings/usage" target="_blank" rel="noopener">Open canonical usage page →</a>
  `;
}

function renderSysStatusPanel() {
  // Tabbed view mirroring the CLI `/status` layout. Data sources:
  //   systemStatusCache  — populated by `_capture_system_init` on every
  //                        agent run; Refresh shells out to claude.
  //   systemUsageCache   — read from ~/.claude/stats-cache.json via
  //                        /api/system/usage; lazy-fetched on Usage-tab.
  const s = systemStatusCache || {};
  const rl = s.rate_limit_info || {};
  const h = _ssRateLimitHealth(rl);
  const tab = _sysStatusActiveTab;
  const sspProvCaps = _getProviderCaps(s.provider || 'claude');
  let body = '';
  if (tab === 'config') body = _renderConfigTab(s);
  else if (tab === 'mcp') body = _renderMcpTab(s);
  else if (tab === 'usage') body = _renderUsageTab();
  else body = _renderStatusTab(s, rl, h);

  const tabBtn = (id, label, show = true) => show
    ? `<button class="ssp-tab ${tab === id ? 'active' : ''}" onclick="_sysStatusSwitchTab('${id}', event)">${label}</button>`
    : '';

  return `
    <div class="ssp-tabs">
      ${tabBtn('status', 'Status')}
      ${tabBtn('config', 'Config')}
      ${tabBtn('mcp', 'MCP', sspProvCaps.supports_mcp)}
      ${tabBtn('usage', 'Usage')}
    </div>
    <div class="ssp-tab-panel">${body}</div>
    <div class="ssp-foot">
      <span>Captured ${_ssRelTime(s.captured_at)}</span>
      <button class="ssp-refresh" id="ssp-refresh-btn" onclick="refreshSystemStatus(event)" ${_sysStatusRefreshing ? 'disabled' : ''}>${_sysStatusRefreshing ? 'Refreshing…' : 'Refresh'}</button>
    </div>
  `;
}

function _sysStatusSwitchTab(tab, ev) {
  if (ev) { ev.stopPropagation(); ev.preventDefault(); }
  _sysStatusActiveTab = tab;
  if (tab === 'usage' && !systemUsageCache && !_sysUsageFetching) {
    fetchSystemUsage();
  }
  _rerenderSysStatusSurfaces();
}

async function fetchSystemUsage() {
  if (_sysUsageFetching) return;
  _sysUsageFetching = true;
  try {
    const res = await fetchFailFast(API_BASE + '/api/system/usage');
    if (res.ok) systemUsageCache = await res.json();
  } catch { /* leave cache as-is */ }
  _sysUsageFetching = false;
  _rerenderSysStatusSurfaces();
}

function renderSysStatusPopover() {
  const pop = document.getElementById('sys-status-popover');
  if (!pop) return;
  pop.innerHTML = renderSysStatusPanel();
}

// Pixel-snap the popover. The pill is sized by fractional-width mono text
// (label is 10.5px), so its right edge lands on a sub-pixel x; a fixed panel
// at a fractional coordinate renders blurry. Snapping must be to the *device*
// pixel grid, not CSS pixels: on Windows at 125%/150% scaling an integer CSS
// px (Math.round) still maps to a fractional device px. snap() rounds in
// device space (× dpr → round → ÷ dpr) so the composited panel rasterizes
// crisp at any display scale.
function _positionSysStatusPopover() {
  const pill = document.getElementById('sys-status-pill');
  const pop = document.getElementById('sys-status-popover');
  if (!pill || !pop) return;
  const r = pill.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const snap = (v) => Math.round(v * dpr) / dpr;
  pop.style.position = 'fixed';
  pop.style.top = snap(r.bottom + 8) + 'px';
  pop.style.right = snap(window.innerWidth - r.right) + 'px';
  pop.style.left = 'auto';
  pop.style.marginTop = '0';
}

function toggleSysStatusPopover(ev) {
  if (ev) { ev.stopPropagation(); ev.preventDefault(); }
  const pop = document.getElementById('sys-status-popover');
  if (!pop) return;
  _sysStatusPopoverOpen = !pop.classList.contains('open');
  if (_sysStatusPopoverOpen) {
    renderSysStatusPopover();
    pop.classList.add('open');
    _positionSysStatusPopover();
    // Refresh cache when popover opens so the user sees current data.
    fetchSystemStatus();
    // Lazy-fetch usage if the Usage tab is the persisted active one.
    if (_sysStatusActiveTab === 'usage' && !systemUsageCache && !_sysUsageFetching) {
      fetchSystemUsage();
    }
  } else {
    pop.classList.remove('open');
  }
}

async function refreshSystemStatus(ev) {
  if (ev) { ev.stopPropagation(); ev.preventDefault(); }
  if (_sysStatusRefreshing) return;
  _sysStatusRefreshing = true;
  _rerenderSysStatusSurfaces();
  try {
    const res = await fetch(API_BASE + '/api/system/status/refresh', { method: 'POST' });
    const data = await res.json().catch(() => null);
    if (data && data.captured_at !== undefined) {
      systemStatusCache = data;
    } else if (data && data.status) {
      systemStatusCache = data.status;
    }
  } catch { /* surface as no-update */ }
  _sysStatusRefreshing = false;
  renderSysStatusPill();
  // Drop the usage cache too so the next render re-fetches stats-cache.json.
  systemUsageCache = null;
  if (_sysStatusActiveTab === 'usage') fetchSystemUsage();
  _rerenderSysStatusSurfaces();
}

function _rerenderSysStatusSurfaces() {
  // Header popover is now the sole surface for system status.
  if (_sysStatusPopoverOpen) {
    renderSysStatusPopover();
    _positionSysStatusPopover();
  }
}

// Keep the popover pixel-snapped if the window is resized while it's open.
window.addEventListener('resize', () => {
  if (_sysStatusPopoverOpen) _positionSysStatusPopover();
});

// Close the popover on any outside click. Bound once at load.
document.addEventListener('click', (e) => {
  if (!_sysStatusPopoverOpen) return;
  const pill = document.getElementById('sys-status-pill');
  if (pill && pill.contains(e.target)) return;
  const pop = document.getElementById('sys-status-popover');
  if (pop) pop.classList.remove('open');
  _sysStatusPopoverOpen = false;
});



// ── Boot: relocated from index.html's inline boot tail. As a deferred
//    `type="module"` script this runs just after document parse instead of
//    mid-parse — the one-shot fetch + 60s poll start a few hundred ms later,
//    which is immaterial for a status pill (it renders idle until the async
//    fetch resolves anyway). Byte-verbatim from the original two lines. ──
// System status pill: initial fetch + periodic re-fetch (60s cadence matches
// the schedule banner). Cache is also auto-refreshed server-side by any
// agent activity, so the pill stays current without active polling.
fetchSystemStatus();
setInterval(fetchSystemStatus, 60000);

// ── Interop: re-expose for inline / cross-module + region-generated on*=
//    handler callers. All runtime-only (resolve against window — incl. the
//    bare `typeof _rerenderSysStatusSurfaces` guard at the token-usage
//    refresher, which sees the window prop via the global scope chain).
//    All 6 state vars + the remaining helpers are module-private (zero
//    outside refs). ──
window.toggleSysStatusPopover = toggleSysStatusPopover;   // pill static onclick
window._rerenderSysStatusSurfaces = _rerenderSysStatusSurfaces; // typeof-guarded token-usage caller
// region-generated on*= handler targets:
window._sysStatusSwitchTab = _sysStatusSwitchTab;
window.refreshSystemStatus = refreshSystemStatus;
