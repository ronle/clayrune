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
    const res = await fetch(API_BASE + '/api/schedules');
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

// ── Interop: re-expose for inline / cross-module + region-generated on*=
//    handler callers. All runtime-only. `refreshScheduleBanner` is called
//    by the inline fetchProjects().then initial-paint AND by scheduler.js
//    (×3, after schedule mutations) — both runtime, resolve the window prop.
//    `_sbState` (const) + _sbRender / _sbLoadRecent / _sbFormatAbs are
//    module-private. ──
window.refreshScheduleBanner = refreshScheduleBanner; // fetchProjects initial paint + scheduler.js mutations
// region-generated on*= handler targets:
window.toggleSchedulePanel = toggleSchedulePanel;
window._sbSetTab = _sbSetTab;
window._sbSetWindow = _sbSetWindow;
window._sbOpenTranscript = _sbOpenTranscript;
window._sbOpenAgentLog = _sbOpenAgentLog;
