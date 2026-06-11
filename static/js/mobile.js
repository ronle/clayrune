// ── Mobile UI: app bar greeting + filter pills (≤960px, warm tone) ──────────
function renderMobileAppBar() {
  if (window.innerWidth > 960) return;
  const eyebrow = document.getElementById('mc-eyebrow');
  if (!eyebrow) return;
  const now = new Date();
  const day = now.toLocaleDateString(undefined, { weekday: 'long' });
  const h = now.getHours();
  const part = h < 5 ? 'night' : h < 12 ? 'morning' : h < 17 ? 'afternoon' : 'evening';
  eyebrow.textContent = `${day} ${part}`;
  // Avatar initials: from configured display name, else fall back to "C" for Clayrune
  const name = (_globalConfig && (_globalConfig.user_display_name || _globalConfig.user_name)) || '';
  const initials = name
    ? name.split(/\s+/).map(s => s[0]).filter(Boolean).slice(0, 2).join('').toUpperCase()
    : 'C';
  const av = document.getElementById('mc-avatar-btn');
  if (av) av.textContent = initials;
}

function renderMobileFilterPills() {
  const row = document.getElementById('mobile-filter-pills');
  if (!row) return;
  if (window.innerWidth > 960) { row.innerHTML = ''; return; }
  const counts = { all: allProjects.length, unread: 0, urgent: 0, working: 0, done: 0, idle: 0 };
  allProjects.forEach(p => {
    const fs = friendlyStatus(p);
    if (unreadCount(p) > 0) counts.unread++;
    if (fs === 'asking' || fs === 'stuck') counts.urgent++;
    if (fs === 'working') counts.working++;
    if (fs === 'done') counts.done++;
    if (fs === 'idle') counts.idle++;
  });
  const af = activeFilter || 'all';
  const pills = [
    { id: 'urgent',    label: 'Needs you', count: counts.urgent,  cls: 'urgent' },
    { id: 'unread',    label: 'Unread',    count: counts.unread,  cls: '' },
    { id: 'all',       label: 'All',       count: counts.all,     cls: '' },
    { id: 'active',    label: 'Working',   count: counts.working, cls: '' },
    { id: 'completed', label: 'Done',      count: counts.done,    cls: '' },
    { id: 'parked',    label: 'Resting',   count: counts.idle,    cls: '' },
  ];
  row.innerHTML = pills.map(p => {
    if (!p.count && p.id !== 'all' && af !== p.id) return '';
    const active = af === p.id;
    return `<button class="mc-pill ${p.cls} ${active ? 'active' : ''}" onclick="setFilter('${p.id}')">${esc(p.label)}${p.count ? ` <span class="count">${p.count}</span>` : ''}</button>`;
  }).join('');
}

// Re-render on resize so mobile-only blocks appear/disappear cleanly
// (incl. the grid↔chat-list swap when crossing the 960px boundary).
// ── Mobile chat list (WhatsApp-style) ───────────────────────────────────────
// On ≤960px the card grid is replaced by a contact-list of projects:
// avatar + name + live-status subtitle + time + unread badge, pinned items
// (asking/stuck) on top, then by recency. Desktop is untouched.
function isMobileChatList() { return window.innerWidth <= 960; }

// Single client-local touch-point for read state. Structured so a later
// move to server-side per-device tracking is a body swap, not a refactor.
function markProjectSeen(pid) {
  if (!pid) return;
  projectLastSeen[pid] = Date.now();
  try { localStorage.setItem('mc_proj_seen', JSON.stringify(projectLastSeen)); } catch (e) {}
}

// Count of actionable agent events the user hasn't seen for project p.
// Actionable = (a) the agent is waiting on the user to continue
// (friendlyStatus 'asking' — plan approval / question / waiting), or
// (b) an autonomous (non-manual trigger) session produced an update.
// Interactive turns the user drove are NOT counted (they were watching).
// Derived on every render from polled state — deliberately NOT SSE — because
// closed projects have no live connection (Chromium 6-slot cap closes SSE on
// turn_complete), so an SSE-incremented counter would silently miss them.
function unreadCount(p) {
  if (!p) return 0;
  const seen = projectLastSeen[p.id] || 0;
  let n = 0;
  // (a) standing "asking" — counts once, keyed to the project's last_updated
  // onset so it does not inflate every poll while the user is away.
  if (friendlyStatus(p) === 'asking') {
    if ((Date.parse(p.last_updated || '') || 0) > seen) n += 1;
  }
  // (b) autonomous-session updates newer than last-seen. Hivemind workers are
  // excluded as noise; the orchestrator finishing IS a real update.
  agentHistory.forEach(h => {
    if (h.projectId !== p.id) return;
    if (!h.triggerType || h.triggerType === 'manual') return;
    if (isHivemindWorker(h)) return;
    if (!(h.status === 'completed' || h.status === 'idle' || h.status === 'error')) return;
    if ((Date.parse(h.startedAt || '') || 0) > seen) n += 1;
  });
  return n;
}

function projectInitials(p) {
  const s = String((p && (p.name || p.id)) || '?').trim();
  const parts = s.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  return s.slice(0, 2).toUpperCase();
}

function projectRowHTML(p) {
  const fs = friendlyStatus(p);
  const unread = unreadCount(p);
  const av = p.emoji
    ? `<span class="cr-av cr-av-emoji">${esc(p.emoji)}</span>`
    : `<span class="cr-av cr-av-init">${esc(projectInitials(p))}</span>`;
  const badge = unread > 0
    ? `<span class="cr-badge">${unread > 9 ? '9+' : unread}</span>` : '';
  return `
  <div class="mc-chat-row friendly-${fs} ${unread ? 'cr-unread' : ''}" data-id="${esc(p.id)}">
    <div class="cr-avatar friendly-${fs}">${av}<span class="cr-ring"></span></div>
    <div class="cr-main">
      <div class="cr-top">
        <span class="cr-name">${esc(p.name || p.id)}</span>
        <span class="cr-time">${esc(p.last_updated_relative || '')}</span>
      </div>
      <div class="cr-bot">
        <span class="cr-sub">${esc(friendlySummary(p))}</span>
        ${badge}
      </div>
    </div>
  </div>`;
}

function renderMobileChatList(col) {
  const filtered = filterProjects();
  if (!filtered.length) { col.innerHTML = '<div class="loading">No projects match filter</div>'; return; }
  const rank = p => {
    const fs = friendlyStatus(p);
    if (fs === 'asking') return 0;   // needs you → top
    if (fs === 'stuck')  return 1;   // blocked → next
    return 2;                        // everything else → recency below
  };
  const sorted = filtered.slice().sort((a, b) => {
    const r = rank(a) - rank(b);
    if (r !== 0) return r;
    return (Date.parse(b.last_updated || '') || 0) - (Date.parse(a.last_updated || '') || 0);
  });
  col.innerHTML = `<div class="mc-chat-list">${sorted.map(projectRowHTML).join('')}</div>`;
  col.querySelectorAll('.mc-chat-row').forEach(row => {
    row.addEventListener('click', () => openProjectModal(row.dataset.id));
  });
}

function mcPushModalHistory() {
  if (!isMobileChatList() || _mcModalHistoryActive) return;
  try { history.pushState({ mc: 'modal' }, ''); _mcModalHistoryActive = true; } catch (e) {}
}
function mcPushConvHistory() {
  if (!isMobileChatList() || _mcConvHistoryActive) return;
  try { history.pushState({ mc: 'conv' }, ''); _mcConvHistoryActive = true; } catch (e) {}
}
function mcPushDrawerHistory() {
  if (_mcDrawerHistoryActive) return;
  try { history.pushState({ mc: 'drawer' }, ''); _mcDrawerHistoryActive = true; } catch (e) {}
}
function _settingsWantsBackNav() { return _isMobileDevice || window.innerWidth <= 960; }
function mcPushSettingsHistory() {
  if (!_settingsWantsBackNav() || _mcSettingsHistoryActive) return;
  try { history.pushState({ mc: 'settings' }, ''); _mcSettingsHistoryActive = true; } catch (e) {}
}
function mcPushSettingsNav() {
  if (!_settingsWantsBackNav()) return;
  try { history.pushState({ mc: 'settings-nav' }, ''); _mcSettingsNavDepth++; } catch (e) {}
}

// ── Mobile navigation drawer (hamburger) ────────────────────────────────────
// Pure DOM toggle so closeMobileDrawer can be reused by the popstate handler
// without re-entering history.go (the popstate path has already consumed the
// sentinel). UI-initiated close synthetically unwinds via _mcUnwindHistory(1)
// to keep the back stack in sync, matching the modal/conv discipline.
function _closeMobileDrawerUI() {
  const d  = document.getElementById('mobile-drawer');
  const bd = document.getElementById('mobile-drawer-backdrop');
  if (d)  { d.classList.remove('open'); d.setAttribute('aria-hidden', 'true'); }
  if (bd) bd.classList.remove('open');
}
function openMobileDrawer() {
  const d  = document.getElementById('mobile-drawer');
  const bd = document.getElementById('mobile-drawer-backdrop');
  if (!d || !bd) return;
  // Mirror the current top-level surface as the drawer's active row (visual
  // continuity with the sidebar). dashboard is the default when no sidebar
  // item is marked active.
  const activeNav = document.querySelector('.sidebar-item.active')?.dataset.nav || 'dashboard';
  d.querySelectorAll('.mobile-drawer-item').forEach(el => {
    el.classList.toggle('active', el.dataset.nav === activeNav);
  });
  d.classList.add('open');
  d.setAttribute('aria-hidden', 'false');
  bd.classList.add('open');
  mcPushDrawerHistory();
}
function closeMobileDrawer() {
  if (_mcDrawerHistoryActive) {
    _mcDrawerHistoryActive = false;
    _mcUnwindHistory(1);
  }
  _closeMobileDrawerUI();
}
// Tap a drawer item → close drawer, then route. Incognito has its own opener;
// everything else goes through sidebarNav (which already knows how to close
// any open project modal on 'dashboard').
function mobileDrawerNav(target) {
  closeMobileDrawer();
  if (target === 'incognito') { openIncognito(); return; }
  sidebarNav(target);
}
// Synthetically unwind `n` MC sentinels (UI-initiated close/back) so a later
// hardware back isn't swallowed by a now-dead entry.
function _mcUnwindHistory(n) {
  if (n <= 0) return;
  _mcSuppressPop = true;
  try { history.go(-n); } catch (e) { _mcSuppressPop = false; }
}

// ── interop: window re-exposure for inline/generated/cross-module callers ──
window.renderMobileAppBar = renderMobileAppBar;
window.renderMobileFilterPills = renderMobileFilterPills;
window.isMobileChatList = isMobileChatList;
window.markProjectSeen = markProjectSeen;
window.unreadCount = unreadCount;
window.renderMobileChatList = renderMobileChatList;
window.mcPushModalHistory = mcPushModalHistory;
window.mcPushConvHistory = mcPushConvHistory;
window.mcPushSettingsHistory = mcPushSettingsHistory;
window.mcPushSettingsNav = mcPushSettingsNav;
window._closeMobileDrawerUI = _closeMobileDrawerUI;
window.openMobileDrawer = openMobileDrawer;
window.closeMobileDrawer = closeMobileDrawer;
window.mobileDrawerNav = mobileDrawerNav;
window._mcUnwindHistory = _mcUnwindHistory;
