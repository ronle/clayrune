// ── Schedule Banner (upcoming + recent runs dropdown) ─────────────────────
//
// Compact trigger button + dropdown panel with two tabs:
//   • Upcoming: enabled schedules with their next_run time
//   • Recent:   agent runs across all projects in the last N hours
//                (selectable: 1h / 2h / 4h / 24h; default 2h)
//
// Recent rows click through to the project's Agent Log tab.

const _sbState = {
  open: false,
  tab: 'upcoming',
  windowHours: 2,
  upcoming: [],
  recent: [],
  recentLoading: false,
  recentLoaded: false,
};



async function refreshScheduleBanner() {
  try {
    const res = await fetchFailFast(API_BASE + '/api/schedules');
    const schedules = await res.json();
    _sbState.upcoming = schedules
      .filter(s => s.enabled && s.next_run)
      .sort((a, b) => (a.next_run || '').localeCompare(b.next_run || ''))
      .slice(0, 10);
  } catch(e) {
    _sbState.upcoming = [];
  }
  _sbRender();
  // If the dropdown is open on the Recent tab, refresh that data too so
  // the polled 60s tick keeps recency labels fresh.
  if (_sbState.open && _sbState.tab === 'recent') {
    _sbLoadRecent();
  }
}

function _sbRender() {
  const banner = document.getElementById('schedule-banner');
  if (!banner) return;
  // Hide the banner only when there's nothing to show AND the user hasn't
  // explicitly opened it. Keeping it visible while open lets the user finish
  // browsing Recent runs even if no upcoming schedules exist.
  if (!_sbState.upcoming.length && !_sbState.open) {
    banner.classList.add('hidden');
    return;
  }
  banner.classList.remove('hidden');

  const totalUpcoming = _sbState.upcoming.length;
  const next = _sbState.upcoming[0];
  const triggerNext = next
    ? `Next: <b>${esc(next.project_name || next.project_id)}</b> &middot; ${esc(formatScheduleTime(next.next_run))}`
    : `<span style="color:var(--text-faint)">No upcoming</span>`;

  const upcomingRows = totalUpcoming
    ? _sbState.upcoming.map(s => `
        <div class="sb-row" onclick="openScheduler()" title="${esc(s.task)}">
          <span class="sb-project">${esc(s.project_name || s.project_id)}</span>
          <span class="sb-task">${esc(s.task)}</span>
          <span class="sb-time">${esc(formatScheduleTime(s.next_run))}</span>
        </div>`).join('')
    : `<div class="sb-empty">No upcoming runs scheduled.</div>`;

  let recentRows;
  if (_sbState.recentLoading && !_sbState.recentLoaded) {
    recentRows = `<div class="sb-empty">Loading…</div>`;
  } else if (!_sbState.recent.length) {
    recentRows = `<div class="sb-empty">No runs in the last ${_sbState.windowHours}h.</div>`;
  } else {
    recentRows = _sbState.recent.map((e, idx) => {
      const status = e.status || 'unknown';
      const csid = e.claude_session_id || '';
      const pid = e.project_id || '';
      // No csid = transcript can't be located (e.g. crashed before claude
      // assigned one). Fall back to the project's Agent Log so the user
      // still has a route in. Index is used to look up the full entry so
      // we don't have to escape task text into an onclick string.
      const hasCsid = !!csid;
      const handler = hasCsid
        ? `_sbOpenTranscript(${idx})`
        : `_sbOpenAgentLog('${esc(pid)}')`;
      const cls = hasCsid ? 'sb-row sb-row-recent' : 'sb-row sb-row-recent sb-row-no-csid';
      const absTime = _sbFormatAbs(e.ts);
      const provBadge = _providerBadge(e.provider);
      return `
        <div class="${cls}" onclick="${handler}" title="${esc(e.task || '')}">
          <span class="sb-project">${esc(e.project_name || pid)}${provBadge ? '&nbsp;' + provBadge : ''}</span>
          <span class="sb-task">${esc(e.task || '(no task)')}</span>
          <span class="sb-status sb-status-${esc(status)}">${esc(status)}</span>
          <span class="sb-time sb-time-stack">
            <span class="sb-time-abs">${esc(absTime)}</span>
            <span class="sb-time-rel">${esc(e.ts_relative || '')}</span>
          </span>
        </div>`;
    }).join('');
  }

  const winPills = [1, 2, 4, 24].map(h =>
    `<button class="sb-win-pill ${_sbState.windowHours===h?'active':''}" type="button" onclick="_sbSetWindow(event, ${h})">${h}h</button>`
  ).join('');

  const recentCountLabel = _sbState.recentLoaded
    ? `<span class="sb-tab-count">${_sbState.recent.length}</span>`
    : '';

  banner.innerHTML = `
    <button class="sb-trigger ${_sbState.open?'open':''}" id="sb-trigger" type="button"
            onclick="toggleSchedulePanel(event)" aria-expanded="${_sbState.open?'true':'false'}">
      <span class="sb-icon">&#x23F1;</span>
      <span class="sb-label">Schedule</span>
      <span class="sb-count">${totalUpcoming}</span>
      <span class="sb-next">${triggerNext}</span>
      <span class="sb-chev">&#9662;</span>
    </button>
    <div class="sb-panel ${_sbState.open?'open':''}" id="sb-panel">
      <div class="sb-tabs">
        <button class="sb-tab ${_sbState.tab==='recent'?'active':''}" type="button" onclick="_sbSetTab(event,'recent')">
          Recent ${recentCountLabel}
        </button>
        <button class="sb-tab ${_sbState.tab==='upcoming'?'active':''}" type="button" onclick="_sbSetTab(event,'upcoming')">
          Upcoming <span class="sb-tab-count">${totalUpcoming}</span>
        </button>
        <div class="sb-tabs-spacer"></div>
        ${_sbState.tab==='recent' ? `<div class="sb-win-pills">${winPills}</div>` : ''}
      </div>
      <div class="sb-rows">
        ${_sbState.tab==='upcoming' ? upcomingRows : recentRows}
      </div>
    </div>`;
}

// Toggle open/closed. Stops propagation so the document-level
// click-outside handler doesn't immediately close us.
function toggleSchedulePanel(ev) {
  if (ev) ev.stopPropagation();
  _sbState.open = !_sbState.open;
  _sbRender();
  // First time opening on Recent, kick off a fetch.
  if (_sbState.open && _sbState.tab === 'recent' && !_sbState.recentLoaded) {
    _sbLoadRecent();
  }
}

function _sbSetTab(ev, tab) {
  if (ev) ev.stopPropagation();
  _sbState.tab = tab;
  _sbRender();
  if (tab === 'recent') {
    // Always re-fetch on tab open so "X ago" labels are current.
    _sbLoadRecent();
  }
}

function _sbSetWindow(ev, hours) {
  if (ev) ev.stopPropagation();
  _sbState.windowHours = hours;
  _sbLoadRecent();
}

async function _sbLoadRecent() {
  _sbState.recentLoading = true;
  _sbRender();
  try {
    const res = await fetch(API_BASE + `/api/recent-runs?hours=${_sbState.windowHours}&limit=50`);
    const data = await res.json();
    _sbState.recent = Array.isArray(data.runs) ? data.runs : [];
    _sbState.recentLoaded = true;
  } catch(e) {
    _sbState.recent = [];
  }
  _sbState.recentLoading = false;
  _sbRender();
}

// Format an entry's `ts` (ISO UTC) as a local clock label.
//   • same day  → "HH:MM"
//   • otherwise → "MMM D HH:MM"
// Pairs with `ts_relative` ("12m ago") in the row so batch events that
// share a single completion timestamp are obvious at a glance.
function _sbFormatAbs(ts) {
  if (!ts) return '';
  try {
    const d = new Date(ts);
    if (isNaN(d.getTime())) return '';
    const now = new Date();
    const sameDay = d.getFullYear() === now.getFullYear()
                 && d.getMonth() === now.getMonth()
                 && d.getDate() === now.getDate();
    if (sameDay) {
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
    }
    return d.toLocaleString([], {
      month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit', hour12: false,
    });
  } catch(e) {
    return '';
  }
}

function _sbOpenTranscript(idx) {
  const e = _sbState.recent[idx];
  if (!e) return;
  const pid = e.project_id || '';
  const csid = e.claude_session_id || '';
  if (!pid || !csid) return;
  // Use the task text as the transcript label so the viewer's subhead
  // reads as the original prompt instead of just a UUID.
  openTranscriptViewer(pid, csid, e.task || '');
  _sbState.open = false;
  _sbRender();
}

function _sbOpenAgentLog(pid) {
  if (!pid) return;
  modalActiveTab[pid] = 'agent-log';
  openProjectModal(pid);
  _sbState.open = false;
  _sbRender();
}

// Click anywhere outside the banner closes the panel.
document.addEventListener('click', (ev) => {
  if (!_sbState.open) return;
  const banner = document.getElementById('schedule-banner');
  if (banner && banner.contains(ev.target)) return;
  _sbState.open = false;
  _sbRender();
});

// Esc closes too.
document.addEventListener('keydown', (ev) => {
  if (ev.key !== 'Escape') return;
  if (!_sbState.open) return;
  _sbState.open = false;
  _sbRender();
});



// ── Boot: relocated from index.html's inline boot tail. As a deferred
//    `type="module"` script the 60s poll starts a few hundred ms after
//    parse instead of mid-parse — immaterial for the schedule banner (it
//    renders from refreshScheduleBanner's async fetch; the inline
//    fetchProjects().then callback also calls window.refreshScheduleBanner()
//    once for the initial paint). Byte-verbatim from the original two lines. ──
// Refresh schedule banner every 60s
setInterval(refreshScheduleBanner, 60000);

// ── History — unified run log (Agent Log + Schedule Runs + Hivemind Runs),
// backed by /api/recent-runs (all agent_log entries, tagged by trigger_type).
// Filterable by source; a row opens that run's transcript. Desktop redesign
// step 5 — one surface replacing the three separate run-history views.
let _histState = { runs: [], rendered: [], loading: false, filter: 'all', hours: 168 };

function _histTrigGroup(t) {
  t = String(t || '').toLowerCase();
  if (t === 'schedule') return 'scheduled';
  if (t.indexOf('hivemind') === 0) return 'hivemind';
  return 'manual';
}
function _histBadge(g) { return g === 'scheduled' ? 'Scheduled' : g === 'hivemind' ? 'Hivemind' : 'Manual'; }
function _histDayLabel(ts) {
  try {
    const d = new Date(ts), now = new Date();
    const same = (a, b) => a.toDateString() === b.toDateString();
    const y = new Date(now); y.setDate(now.getDate() - 1);
    if (same(d, now)) return 'Today';
    if (same(d, y)) return 'Yesterday';
    return d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' });
  } catch (e) { return ''; }
}
async function _histLoad() {
  _histState.loading = true;
  _histRenderList();
  try {
    const res = await fetch(API_BASE + `/api/recent-runs?hours=${_histState.hours}&limit=200`);
    const data = await res.json();
    _histState.runs = Array.isArray(data.runs) ? data.runs : [];
  } catch (e) { _histState.runs = []; }
  _histState.loading = false;
  _histRenderList();
}
function _histRenderList() {
  const el = document.getElementById('history-list');
  if (!el) return;
  if (_histState.loading && !_histState.runs.length) { el.innerHTML = '<div class="mib-empty">Loading&hellip;</div>'; return; }
  const f = _histState.filter;
  const rows = _histState.runs.filter(r => f === 'all' || _histTrigGroup(r.trigger_type) === f);
  _histState.rendered = rows;
  if (!rows.length) { el.innerHTML = '<div class="mib-empty">No runs in this window.</div>'; return; }
  let html = '', curDay = null;
  rows.forEach((r, i) => {
    const day = _histDayLabel(r.ts);
    if (day !== curDay) { curDay = day; html += `<div class="mib-day">${esc(day)}</div>`; }
    const g = _histTrigGroup(r.trigger_type);
    const st = String(r.status || '').toLowerCase();
    html += `<div class="hist-row" onclick="_histOpenRunIdx(${i})">
      <span class="hist-badge hist-${g}">${_histBadge(g)}</span>
      <div class="hist-main">
        <div class="hist-top"><span class="hist-proj">${esc(r.project_name || r.project_id || '')}</span><span class="hist-time">${esc(r.ts_relative || '')}</span></div>
        <div class="hist-task">${esc((r.task || '(no task)').slice(0, 160))}</div>
      </div>
      <span class="hist-status s-${esc(st)}">${esc(r.status || '')}</span>
    </div>`;
  });
  el.innerHTML = html;
}
function _histOpenRunIdx(i) {
  const r = _histState.rendered[i];
  if (!r) return;
  const pid = r.project_id || '', csid = r.claude_session_id || '';
  if (pid && csid && typeof openTranscriptViewer === 'function') openTranscriptViewer(pid, csid, r.task || '');
  else if (pid && typeof openProjectModal === 'function') { modalActiveTab[pid] = 'agent-log'; openProjectModal(pid); }
}
function _histSetFilter(f) {
  _histState.filter = f;
  document.querySelectorAll('.hist-filter').forEach(b => b.classList.toggle('active', b.dataset.f === f));
  _histRenderList();
}
function _histSetWindow(h) { _histState.hours = h || 168; _histLoad(); }

function openHistory() {
  const modalId = '__history';
  if (openModals.has(modalId)) {
    const entry = openModals.get(modalId);
    if (entry.minimized) restoreModal(modalId);
    focusModal(modalId);
    _histLoad();
    return;
  }
  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content';
  if (typeof _clampModalSize === 'function') _clampModalSize(content, 720);
  const chip = (f, label) => `<button class="hist-filter${_histState.filter === f ? ' active' : ''}" data-f="${f}" onclick="_histSetFilter('${f}')">${label}</button>`;
  content.innerHTML = `
    <div class="modal-header" style="display:flex;align-items:center;gap:12px;padding:16px 24px 12px 28px">
      <span style="font-size:16px;font-weight:700;color:var(--text)">History</span>
      <div class="hist-filters">${chip('all', 'All')}${chip('manual', 'Manual')}${chip('scheduled', 'Scheduled')}${chip('hivemind', 'Hivemind')}</div>
      <select onchange="_histSetWindow(parseInt(this.value,10))" style="margin-left:auto;padding:6px 8px;font-size:12px;background:var(--surface2);border:1px solid var(--border);border-radius:6px;color:var(--text)">
        <option value="24">24 hours</option>
        <option value="168" selected>7 days</option>
        <option value="720">30 days</option>
      </select>
      <div class="modal-window-controls" style="position:static;display:flex;gap:4px">
        <button class="modal-minimize" onclick="minimizeModal('${modalId}')" title="Minimize">&#x2015;</button>
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
    </div>
    <div id="history-list" style="max-height:66vh;overflow-y:auto;padding:0 8px 8px"></div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);
  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  if (typeof centerModalElement === 'function') centerModalElement(win);
  focusModal(modalId);
  _histLoad();
}

// ── Interop: re-expose for inline / cross-module + region-generated on*=
//    handler callers. All runtime-only. `refreshScheduleBanner` is called
//    by the inline fetchProjects().then initial-paint AND by scheduler.js
//    (×3, after schedule mutations) — both runtime, resolve the window prop.
//    `_sbState` (const) + _sbRender / _sbLoadRecent / _sbFormatAbs are
//    module-private. ──
window.openHistory = openHistory;
window._histSetFilter = _histSetFilter;
window._histSetWindow = _histSetWindow;
window._histOpenRunIdx = _histOpenRunIdx;
window.refreshScheduleBanner = refreshScheduleBanner; // fetchProjects initial paint + scheduler.js mutations
// region-generated on*= handler targets:
window.toggleSchedulePanel = toggleSchedulePanel;
window._sbSetTab = _sbSetTab;
window._sbSetWindow = _sbSetWindow;
window._sbOpenTranscript = _sbOpenTranscript;
window._sbOpenAgentLog = _sbOpenAgentLog;
