// Beacon — cross-project situational digest (Phase 2 view, v2: report modal).
//
// Shape (per Ron): a thin always-visible summary BAR with a button that opens a
// FULL-SCREEN MODAL report. The report is a TABLE — one row per project, sorted
// by most-recent activity, with dormant ("Paused") projects bundled at the
// bottom. Each row shows a Haiku "where we stand" summary (NOT the last log
// line) and expands in place for the full briefing. Summaries are CACHED;
// refresh is manual (per-row + "Refresh stale").
//
// ES-module note: functions used by inline onclick / the render() loop are
// re-exposed on window.* at the bottom. Degrades silently (bar hidden) when
// /api/beacon/* isn't live yet.

const BEACON_POLL_MS = 15000;
const DORMANT_DAYS = 14;        // resting + untouched longer than this → "Paused"
const REFRESH_STALE_HOURS = 6;  // "Refresh stale" targets briefs older than this

let beaconDigest = null;
let beaconModalOpen = false;
let beaconPausedOpen = false;
const beaconRows = {};          // projectId -> row expanded?
const beaconItemOpen = {};      // "projectId::field" -> briefing line expanded?
const beaconRefreshing = {};    // projectId -> refresh in-flight?
let beaconRefreshingAll = false;
let beaconAvailable = true;
let beaconPollTimer = null;
let beaconES = null;
let _beaconEscHandler = null;

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
function _ageHours(iso) { if (!iso) return Infinity; try { return (Date.now() - new Date(iso)) / 3600000; } catch (e) { return Infinity; } }
function _esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

const BLOCKER_ICON = { plan_pending: '\u{1F4CB}', question_pending: '❓', failed_resume: '⚠', stale: '\u{1F551}' };

// ── data ──────────────────────────────────────────────────────────────────────

function fetchBeacon() {
  if (!beaconAvailable) return;
  fetch(_api() + '/api/beacon/digest')
    .then(r => { if (!r.ok) throw new Error('http ' + r.status); return r.json(); })
    .then(d => { beaconDigest = d; renderBeacon(); })
    .catch(e => {
      if (String(e && e.message || '').indexOf('404') !== -1) { beaconAvailable = false; renderBeacon(); }
    });
}

function _openStream() {
  if (beaconES || !beaconAvailable || typeof EventSource === 'undefined') return;
  try {
    beaconES = new EventSource(_api() + '/api/beacon/stream');
    beaconES.onmessage = (e) => {
      try { const d = JSON.parse(e.data); if (d && d.counts) { beaconDigest = d; renderBeacon(); } } catch (_) {}
    };
    beaconES.onerror = () => { _closeStream(); };
  } catch (e) { beaconES = null; }
}
function _closeStream() { if (beaconES) { try { beaconES.close(); } catch (_) {} beaconES = null; } }

// ── grouping ──────────────────────────────────────────────────────────────────

function _isPaused(p) {
  return p.status === 'resting' && (_ageHours(p.last_touched) / 24) > DORMANT_DAYS;
}
function _split(rows) {
  const active = [], paused = [];
  for (const p of rows) (_isPaused(p) ? paused : active).push(p);
  return { active, paused };
}

// ── render: bar ───────────────────────────────────────────────────────────────

function renderBeacon() {
  const bar = document.getElementById('beacon-bar');
  if (bar) {
    if (!beaconAvailable || !beaconDigest || !beaconDigest.configured) {
      bar.style.display = 'none';
      _updateBadge(0);
    } else {
      bar.style.display = '';
      bar.innerHTML = _barHTML();
      _updateBadge((beaconDigest.counts || {}).blocked || 0);
    }
  }
  if (typeof openModals !== 'undefined' && openModals.has('__beacon')) _renderModal();
}

function _barHTML() {
  const rows = beaconDigest.projects || [];
  const { paused } = _split(rows);
  const blocked = (beaconDigest.counts || {}).blocked || 0;
  const pausedN = paused.length;
  const activeN = rows.length - pausedN;
  const needChip = blocked > 0
    ? `<span class="beacon-count blocked">⚠ ${blocked} need you</span>`
    : `<span class="beacon-count clear">✓ All clear</span>`;
  return `<div class="beacon-bar-inner" onclick="openBeaconReport()">
    <span class="beacon-title">Beacon</span>
    ${needChip}
    <span class="beacon-count active">${activeN} active</span>
    <span class="beacon-count resting">${pausedN} paused</span>
    <span class="beacon-open">Open report →</span>
  </div>`;
}

// ── render: full-screen modal report ───────────────────────────────────────────

// Opens as a managed .modal-window (modal-manager.js) so it inherits
// drag / resize / snap / minimize for free — the same chrome as All Backlog,
// instead of the old bespoke full-screen overlay. SSE teardown is hooked into
// closeModalById('__beacon') via _beaconTeardown so Esc / Home / close-all all
// stop the stream (no leaked EventSource).
function openBeaconReport() {
  const modalId = '__beacon';
  if (openModals.has(modalId)) {
    const entry = openModals.get(modalId);
    if (entry.minimized) restoreModal(modalId);
    focusModal(modalId);
    _renderModal();
    return;
  }
  beaconModalOpen = true;

  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content beacon-modal-content';
  if (typeof _clampModalSize === 'function') _clampModalSize(content, 1040);
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);

  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);

  _openStream();
  fetchBeacon();
  _renderModal();
}

// Single teardown — invoked by closeModalById('__beacon') (the manager's one
// close choke point: X button, Esc, Home, close-all) AND defensively below.
function _beaconTeardown() {
  beaconModalOpen = false;
  _closeStream();
}

function closeBeaconReport() {
  // The manager removes the element + fires _beaconTeardown via the hook.
  if (typeof closeModalById === 'function') closeModalById('__beacon');
  else _beaconTeardown();
}

function _renderModal() {
  const entry = (typeof openModals !== 'undefined') ? openModals.get('__beacon') : null;
  if (!entry || !entry.element) return;
  const content = entry.element.querySelector('.modal-content');
  if (!content) return;
  // Preserve scroll across re-renders (background poll / SSE shouldn't jump it).
  const scroller = content.querySelector('.beacon-modal-scroll');
  const prevScroll = scroller ? scroller.scrollTop : 0;

  const d = beaconDigest || {};
  const rows = d.projects || [];
  const { active, paused } = _split(rows);
  const c = d.counts || { blocked: 0, running: 0, resting: 0 };
  const staleN = rows.filter(r => !r.has_brief || _ageHours(r.updated_at) > REFRESH_STALE_HOURS).length;

  const refreshLabel = beaconRefreshingAll ? 'Refreshing…' : (staleN ? `↻ Refresh stale (${staleN})` : '↻ Refresh stale');

  // .modal-header is the manager's drag handle; .modal-content gets resize:both
  // from CSS. Buttons inside the header are excluded from drag (modalDragStart).
  content.innerHTML = `
    <div class="modal-header beacon-modal-head">
      <div class="beacon-modal-title">Where we stand
        <span class="bmt-sub">${rows.length} projects${c.blocked ? ` · <span class="bmt-need">${c.blocked} need you</span>` : ''} · updated ${_esc(_ago(d.generated_at))}</span>
      </div>
      <div class="beacon-modal-actions">
        <button class="bm-btn" onclick="beaconRefreshAll()" ${beaconRefreshingAll || !staleN ? 'disabled' : ''}>${refreshLabel}</button>
        <div class="modal-window-controls" style="position:static;display:flex;gap:4px">
          <button class="modal-minimize" onclick="minimizeModal('__beacon')" title="Minimize">&#x2015;</button>
          <button class="modal-close" onclick="closeBeaconReport()" title="Close (Esc)">&#10005;</button>
        </div>
      </div>
    </div>
    <div class="beacon-modal-scroll">
      ${(!d.configured)
        ? `<div class="beacon-empty">Beacon isn't live yet.</div>`
        : (!rows.length ? `<div class="beacon-empty">No projects.</div>` : `
        <table class="beacon-table">
          <thead><tr>
            <th class="bt-status">Status</th><th class="bt-proj">Project</th>
            <th class="bt-stand">Where we stand</th><th class="bt-age">Last active</th><th class="bt-act"></th>
          </tr></thead>
          <tbody>
            ${active.map(_rowHTML).join('')}
            ${paused.length ? _pausedGroupHTML(paused) : ''}
          </tbody>
        </table>`)}
    </div>`;

  const ns = content.querySelector('.beacon-modal-scroll');
  if (ns) ns.scrollTop = prevScroll;
}

function _statusPill(p) {
  if (p.status === 'blocked') {
    const t = (p.blocker || {}).type;
    const lbl = t === 'plan_pending' ? 'Needs approval' : t === 'question_pending' ? 'Needs answer' : t === 'stale' ? 'Stale' : 'Needs attention';
    return `<span class="bt-pill need">${BLOCKER_ICON[t] || '⚠'} ${lbl}</span>`;
  }
  if (p.status === 'running') return `<span class="bt-pill working">● Working</span>`;
  return `<span class="bt-pill idle">Idle</span>`;
}

function _rowHTML(p) {
  const open = !!beaconRows[p.id];
  const stand = (p.headline || '').trim();
  const standCell = stand
    ? `<span class="bt-standtext">${_esc(stand)}</span>`
    : `<span class="bt-nostand">No summary yet</span>`;
  const refreshing = !!beaconRefreshing[p.id];
  const main = `<tr class="bt-row ${p.status} ${open ? 'open' : ''}" onclick="beaconToggleRow('${_esc(p.id)}')">
    <td class="bt-status">${_statusPill(p)}</td>
    <td class="bt-proj"><span class="bt-chev ${open ? 'open' : ''}">❯</span>${_esc(p.name)}</td>
    <td class="bt-stand">${standCell}</td>
    <td class="bt-age">${_esc(_ago(p.last_touched))}</td>
    <td class="bt-act" onclick="event.stopPropagation()">
      <button class="bt-iconbtn" title="Open project" onclick="beaconOpen('${_esc(p.id)}')">↗</button>
      <button class="bt-iconbtn" title="Refresh summary" onclick="beaconRefreshRow('${_esc(p.id)}')" ${refreshing ? 'disabled' : ''}>${refreshing ? '…' : '↻'}</button>
    </td>
  </tr>`;
  return main + (open ? _detailHTML(p) : '');
}

// Keep the displayed line a true one-liner. Real one-liners come from Haiku
// (nested {line} field); for an older string-format brief we hard-clamp here so
// it stays short, and the full text drops into `detail` (revealed on click) —
// so even pre-nested cached briefs render as short + expandable, not a long
// ellipsized wall.
const BRIEF_LINE_MAX = 70;

// Normalize one briefing field to {line, detail}, tolerating both the new
// nested shape and an older plain-string brief. Returns null if empty.
function _field(p, key) {
  const f = (p.brief || {})[key];
  if (f == null) return null;
  let line, detail;
  if (typeof f === 'string') { line = detail = f.trim(); }
  else { line = (f.line || '').trim(); detail = (f.detail || f.full || f.line || '').trim(); }
  if (!line || line === 'unavailable') return null;
  let display = line;
  if (display.length > BRIEF_LINE_MAX) {
    display = display.slice(0, BRIEF_LINE_MAX - 1).trim() + '…';
    if (!detail || detail.length <= line.length) detail = line;  // full text lives in detail
  }
  return { line: display, detail: detail || line };
}

function _fieldRowHTML(p, key, label) {
  const f = _field(p, key);
  if (!f) return `<div class="bt-item"><div class="bt-itemhead"><span class="bt-ilabel">${label}</span><span class="bt-iline muted">—</span></div></div>`;
  const k = p.id + '::' + key;
  const open = !!beaconItemOpen[k];
  const expandable = f.detail && f.detail !== f.line;
  return `<div class="bt-item ${open ? 'open' : ''}">
    <div class="bt-itemhead ${expandable ? 'clickable' : ''}" onclick="event.stopPropagation();${expandable ? ` beaconItemToggle('${_esc(p.id)}','${key}')` : ''}">
      <span class="bt-ilabel">${label}</span>
      <span class="bt-iline">${_esc(f.line)}</span>
      ${expandable ? `<span class="bt-ichev ${open ? 'open' : ''}">❯</span>` : ''}
    </div>
    ${open && expandable ? `<div class="bt-idetail">${_esc(f.detail)}</div>` : ''}
  </div>`;
}

function _detailHTML(p) {
  const hasBrief = p.has_brief && (_field(p, 'done') || _field(p, 'standing') || _field(p, 'next'));
  const blk = p.blocker
    ? `<div class="bt-blocker ${_esc(p.blocker.type)}">${BLOCKER_ICON[p.blocker.type] || '⚠'} ${_esc(p.blocker.summary || 'Blocked')}</div>` : '';
  let body;
  if (hasBrief) {
    body = `<div class="bt-items">
      ${_fieldRowHTML(p, 'done', 'Done')}
      ${_fieldRowHTML(p, 'standing', 'Stands')}
      ${_fieldRowHTML(p, 'next', 'Next')}
    </div>`;
  } else {
    body = `<div class="bt-nobrief">No cached summary yet — <a onclick="event.stopPropagation(); beaconRefreshRow('${_esc(p.id)}')">generate one</a> (Haiku reads this project's recent context).</div>`;
  }
  const reviewPlan = (p.blocker && p.blocker.type === 'plan_pending')
    ? `<button class="bm-btn" onclick="beaconOpen('${_esc(p.id)}')">Review plan</button>` : '';
  const refreshing = !!beaconRefreshing[p.id];
  return `<tr class="bt-detailrow"><td colspan="5"><div class="bt-detail" onclick="event.stopPropagation()">
    ${blk}${body}
    <div class="bt-actions">
      ${reviewPlan}
      <button class="bm-btn" onclick="beaconOpen('${_esc(p.id)}')">Open project</button>
      <button class="bm-btn ghost" onclick="beaconRefreshRow('${_esc(p.id)}')" ${refreshing ? 'disabled' : ''}>${refreshing ? 'Refreshing…' : '↻ Refresh summary'}</button>
    </div>
  </div></td></tr>`;
}

function _pausedGroupHTML(paused) {
  const head = `<tr class="bt-paused-head" onclick="beaconTogglePaused()">
    <td colspan="5"><span class="bt-chev ${beaconPausedOpen ? 'open' : ''}">❯</span> Paused — ${paused.length} dormant project${paused.length === 1 ? '' : 's'} (no activity in ${DORMANT_DAYS}+ days)</td>
  </tr>`;
  return head + (beaconPausedOpen ? paused.map(_rowHTML).join('') : '');
}

// ── badge (mobile) ──────────────────────────────────────────────────────────────

function _updateBadge(n) {
  const btn = document.getElementById('beacon-badge-btn');
  const span = document.getElementById('beacon-badge');
  if (span) span.textContent = n;
  if (btn) btn.style.display = n > 0 ? 'inline-flex' : 'none';
}

// ── interactions ──────────────────────────────────────────────────────────────

function beaconToggleRow(id) { beaconRows[id] = !beaconRows[id]; _renderModal(); }
function beaconTogglePaused() { beaconPausedOpen = !beaconPausedOpen; _renderModal(); }
function beaconItemToggle(id, key) { const k = id + '::' + key; beaconItemOpen[k] = !beaconItemOpen[k]; _renderModal(); }

function beaconOpen(id) {
  closeBeaconReport();
  if (window.openProjectModal) window.openProjectModal(id);
}

function beaconRefreshRow(id) {
  if (beaconRefreshing[id]) return;
  beaconRefreshing[id] = true;
  beaconRows[id] = true;
  renderBeacon();
  fetch(_api() + '/api/beacon/refresh/' + encodeURIComponent(id), { method: 'POST' })
    .then(r => r.json())
    .then(() => { beaconRefreshing[id] = false; fetchBeacon(); })
    .catch(() => { beaconRefreshing[id] = false; renderBeacon(); });
}

async function beaconRefreshAll() {
  if (beaconRefreshingAll) return;
  const rows = (beaconDigest && beaconDigest.projects) || [];
  const stale = rows.filter(r => !r.has_brief || _ageHours(r.updated_at) > REFRESH_STALE_HOURS).map(r => r.id);
  if (!stale.length) return;
  beaconRefreshingAll = true;
  stale.forEach(id => { beaconRefreshing[id] = true; });
  renderBeacon();
  let i = 0;
  const worker = async () => {
    while (i < stale.length) {
      const id = stale[i++];
      try { await fetch(_api() + '/api/beacon/refresh/' + encodeURIComponent(id), { method: 'POST' }); } catch (_) {}
      beaconRefreshing[id] = false;
      renderBeacon();
    }
  };
  await Promise.all([worker(), worker(), worker()]);  // concurrency 3
  beaconRefreshingAll = false;
  fetchBeacon();
}

function initBeacon() {
  fetchBeacon();
  if (!beaconPollTimer) {
    beaconPollTimer = setInterval(() => { if (!beaconES) fetchBeacon(); }, BEACON_POLL_MS);
  }
}

// ── window interop ────────────────────────────────────────────────────────────
window.renderBeacon = renderBeacon;
window.initBeacon = initBeacon;
window.openBeaconReport = openBeaconReport;
window.closeBeaconReport = closeBeaconReport;
window._beaconTeardown = _beaconTeardown;   // called by closeModalById('__beacon')
window.beaconToggleRow = beaconToggleRow;
window.beaconTogglePaused = beaconTogglePaused;
window.beaconItemToggle = beaconItemToggle;
window.beaconOpen = beaconOpen;
window.beaconRefreshRow = beaconRefreshRow;
window.beaconRefreshAll = beaconRefreshAll;
window.toggleBeaconPanel = openBeaconReport;  // back-comat alias (mobile badge)

try { initBeacon(); } catch (e) { /* never break boot */ }
