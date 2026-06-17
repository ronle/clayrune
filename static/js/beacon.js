// Beacon — cross-project situational digest (Phase 2 view layer).
//
// Hybrid placement (confirmed with Ron): a thin, always-visible summary bar
// (3 counts + a blocked badge) on the main dashboard that expands into the full
// triaged Beacon list. Data: GET /api/beacon/digest; live updates via the SSE
// stream while the panel is expanded, with a poll baseline otherwise.
//
// ES-module note: top-level names are NOT global. Functions called from inline
// onclick handlers / the inline render() loop are re-exposed via window.* at the
// bottom (the codebase convention — see appearance.js / feed.js). Degrades
// silently when /api/beacon/* isn't live yet (server not yet restarted): the
// bar simply stays hidden, no error spam.

const BEACON_POLL_MS = 15000;

let beaconDigest = null;          // last digest payload
let beaconExpanded = false;       // panel open?
let beaconRestingOpen = false;    // resting sub-grid open?
const beaconRows = {};            // projectId -> accordion open bool
const beaconRefreshing = {};      // projectId -> in-flight refresh bool
let beaconAvailable = true;       // false once the API 404s (routes not live)
let beaconPollTimer = null;
let beaconES = null;              // EventSource, only while expanded

const _api = () => (window.API_BASE || '');

function _ago(iso) {
  if (window.timeAgoShort) return window.timeAgoShort(iso);
  if (!iso) return '';
  try {
    const s = Math.round((Date.now() - new Date(iso)) / 1000);
    if (s < 60) return s + 's ago';
    if (s < 3600) return Math.floor(s / 60) + 'm ago';
    if (s < 86400) return Math.floor(s / 3600) + 'h ago';
    return Math.floor(s / 86400) + 'd ago';
  } catch (e) { return ''; }
}

function _esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

const BLOCKER_ICON = {
  plan_pending: '\u{1F4CB}',     // 📋
  question_pending: '❓',    // ❓
  failed_resume: '⚠',       // ⚠
  stale: '\u{1F551}',            // 🕐
};

// ── data ────────────────────────────────────────────────────────────────────

function fetchBeacon() {
  if (!beaconAvailable) return;
  fetch(_api() + '/api/beacon/digest')
    .then(r => { if (!r.ok) throw new Error('http ' + r.status); return r.json(); })
    .then(d => { beaconDigest = d; renderBeacon(); })
    .catch(e => {
      // 404 = routes not registered yet (server hasn't been restarted). Stop
      // trying and hide quietly. Other (network) errors are transient — keep
      // polling so it self-heals.
      if (String(e && e.message || '').indexOf('404') !== -1) {
        beaconAvailable = false;
        renderBeacon();
      }
    });
}

function _openStream() {
  if (beaconES || !beaconAvailable || typeof EventSource === 'undefined') return;
  try {
    beaconES = new EventSource(_api() + '/api/beacon/stream');
    beaconES.onmessage = (e) => {
      try {
        const d = JSON.parse(e.data);
        if (d && d.counts) { beaconDigest = d; renderBeacon(); }
      } catch (_) { /* ignore keepalive / partial */ }
    };
    beaconES.onerror = () => { _closeStream(); };  // poll baseline covers it
  } catch (e) { beaconES = null; }
}

function _closeStream() {
  if (beaconES) { try { beaconES.close(); } catch (_) {} beaconES = null; }
}

// ── render ────────────────────────────────────────────────────────────────────

function renderBeacon() {
  const bar = document.getElementById('beacon-bar');
  const panel = document.getElementById('beacon-panel');
  if (!bar) return;

  if (!beaconAvailable || !beaconDigest || !beaconDigest.configured) {
    bar.style.display = 'none';
    if (panel) panel.style.display = 'none';
    _updateBadge(0);
    return;
  }

  const c = beaconDigest.counts || { blocked: 0, running: 0, resting: 0 };
  bar.style.display = '';
  bar.innerHTML = _barHTML(c);
  _updateBadge(c.blocked || 0);

  if (panel) {
    panel.style.display = beaconExpanded ? '' : 'none';
    panel.innerHTML = beaconExpanded ? _panelHTML() : '';
  }
}

function _barHTML(c) {
  const blocked = c.blocked || 0, running = c.running || 0, resting = c.resting || 0;
  const blockedChip = blocked > 0
    ? `<span class="beacon-count blocked">⚠ ${blocked} blocked</span>`
    : `<span class="beacon-count clear">✓ All clear</span>`;
  return `<div class="beacon-bar-inner" onclick="toggleBeaconPanel()">
    <span class="beacon-chevron ${beaconExpanded ? 'open' : ''}">❯</span>
    <span class="beacon-title">Beacon</span>
    ${blockedChip}
    <span class="beacon-count running">▶ ${running} running</span>
    <span class="beacon-count resting">· ${resting} resting</span>
    <span class="beacon-updated">updated ${_esc(_ago(beaconDigest.generated_at))}</span>
  </div>`;
}

function _panelHTML() {
  const ps = beaconDigest.projects || [];
  if (!ps.length) return `<div class="beacon-empty">No projects.</div>`;
  const blocked = ps.filter(p => p.status === 'blocked');
  const running = ps.filter(p => p.status === 'running');
  const resting = ps.filter(p => p.status === 'resting');
  let html = '';
  blocked.forEach(p => { html += _rowHTML(p); });
  running.forEach(p => { html += _rowHTML(p); });
  if (resting.length) html += _restingHTML(resting);
  return html || `<div class="beacon-empty">No projects.</div>`;
}

function _rowHTML(p) {
  const open = !!beaconRows[p.id];
  const glyph = p.status === 'blocked'
    ? (BLOCKER_ICON[p.blocker && p.blocker.type] || '⚠')
    : (p.status === 'running' ? '⟳' : '·');
  const age = _ago(p.last_touched);
  return `<div class="beacon-row ${p.status} ${open ? 'open' : ''}" onclick="beaconToggleRow('${_esc(p.id)}')">
    <span class="br-chevron ${open ? 'open' : ''}">❯</span>
    <span class="br-glyph">${glyph}</span>
    <span class="br-name">${_esc(p.name)}</span>
    <span class="br-headline">${_esc(p.headline || '')}</span>
    <span class="br-age">${_esc(age)}</span>
  </div>${open ? _bodyHTML(p) : ''}`;
}

function _bodyHTML(p) {
  const b = p.brief || {};
  const hasBrief = p.has_brief && (b.done || b.standing || b.next);
  const blk = p.blocker
    ? `<div class="beacon-blocker ${_esc(p.blocker.type)}">${BLOCKER_ICON[p.blocker.type] || '⚠'} ${_esc(p.blocker.summary || 'Blocked')}</div>`
    : '';
  let fields;
  if (hasBrief) {
    fields = `
      <div class="beacon-field"><span class="bf-ico">✓</span><div><div class="bf-label">Done this session</div><div class="bf-text">${_esc(b.done || '—')}</div></div></div>
      <div class="beacon-field"><span class="bf-ico">\u{1F4CD}</span><div><div class="bf-label">Where it stands</div><div class="bf-text">${_esc(b.standing || '—')}</div></div></div>
      <div class="beacon-field"><span class="bf-ico">→</span><div><div class="bf-label">Next step</div><div class="bf-text">${_esc(b.next || '—')}</div></div></div>`;
  } else {
    fields = `<div class="beacon-nobrief">No briefing yet — it generates when this project's next session ends. <a onclick="beaconRefreshRow('${_esc(p.id)}')">Generate now</a></div>`;
  }
  const reviewPlan = (p.blocker && p.blocker.type === 'plan_pending')
    ? `<button onclick="beaconOpen('${_esc(p.id)}')">Review plan</button>` : '';
  const refreshing = !!beaconRefreshing[p.id];
  return `<div class="beacon-body" onclick="event.stopPropagation()">
    ${blk}
    ${fields}
    <div class="beacon-actions">
      ${reviewPlan}
      <button onclick="beaconOpen('${_esc(p.id)}')">Open project</button>
      <button class="beacon-refresh" onclick="beaconRefreshRow('${_esc(p.id)}')" ${refreshing ? 'disabled' : ''}>${refreshing ? 'Refreshing…' : '↻ Refresh'}</button>
    </div>
  </div>`;
}

function _restingHTML(resting) {
  const ages = resting.map(p => p.last_touched).filter(Boolean).sort();
  const span = ages.length
    ? `last touched ${_esc(_ago(ages[ages.length - 1]))} – ${_esc(_ago(ages[0]))}`
    : '';
  const grid = beaconRestingOpen
    ? `<div class="beacon-resting-grid">${resting.map(p =>
        `<div class="brg-item" onclick="beaconOpen('${_esc(p.id)}')"><span class="brg-item-name">${_esc(p.name)}</span><span class="brg-age">${_esc(_ago(p.last_touched))}</span></div>`
      ).join('')}</div>`
    : '';
  return `<div class="beacon-resting-head" onclick="beaconToggleResting()">
    <span class="br-chevron ${beaconRestingOpen ? 'open' : ''}">❯</span>
    <span class="br-glyph">·</span>
    <span class="br-name">${resting.length} resting</span>
    <span class="br-headline">${span}</span>
  </div>${grid}`;
}

function _updateBadge(n) {
  const btn = document.getElementById('beacon-badge-btn');
  const span = document.getElementById('beacon-badge');
  if (span) span.textContent = n;
  if (btn) btn.style.display = n > 0 ? 'inline-flex' : 'none';
}

// ── interactions ──────────────────────────────────────────────────────────────

function toggleBeaconPanel() {
  beaconExpanded = !beaconExpanded;
  if (beaconExpanded) { _openStream(); fetchBeacon(); } else { _closeStream(); }
  renderBeacon();
}

function beaconToggleRow(id) {
  beaconRows[id] = !beaconRows[id];
  renderBeacon();
}

function beaconToggleResting() {
  beaconRestingOpen = !beaconRestingOpen;
  renderBeacon();
}

function beaconOpen(id) {
  if (window.openProjectModal) window.openProjectModal(id);
}

function beaconRefreshRow(id) {
  if (beaconRefreshing[id]) return;
  beaconRefreshing[id] = true;
  beaconRows[id] = true;  // keep the row open so the user sees the result
  renderBeacon();
  fetch(_api() + '/api/beacon/refresh/' + encodeURIComponent(id), { method: 'POST' })
    .then(r => r.json())
    .then(() => { beaconRefreshing[id] = false; fetchBeacon(); })
    .catch(() => { beaconRefreshing[id] = false; renderBeacon(); });
}

function initBeacon() {
  fetchBeacon();
  if (!beaconPollTimer) {
    // Poll only when the SSE stream isn't carrying updates (panel collapsed),
    // so the summary counts stay fresh without holding a connection open.
    beaconPollTimer = setInterval(() => { if (!beaconES) fetchBeacon(); }, BEACON_POLL_MS);
  }
}

// ── window interop ────────────────────────────────────────────────────────────
window.renderBeacon = renderBeacon;
window.initBeacon = initBeacon;
window.toggleBeaconPanel = toggleBeaconPanel;
window.beaconToggleRow = beaconToggleRow;
window.beaconToggleResting = beaconToggleResting;
window.beaconOpen = beaconOpen;
window.beaconRefreshRow = beaconRefreshRow;

// Module loads at end of <body>, so the DOM is ready. Kick off best-effort.
try { initBeacon(); } catch (e) { /* never break boot */ }
