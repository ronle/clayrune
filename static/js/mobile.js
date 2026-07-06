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
    // §1b: the "Needs you" pill now opens the dedicated inbox surface (Decision
    // 5a) instead of filtering the list; the other pills still filter.
    const onclick = p.id === 'urgent' ? 'openInbox()' : `setFilter('${p.id}')`;
    return `<button class="mc-pill ${p.cls} ${active ? 'active' : ''}" onclick="${onclick}">${esc(p.label)}${p.count ? ` <span class="count">${p.count}</span>` : ''}</button>`;
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
        <span class="cr-name">${(p.pinned_conversations || []).length ? '<span class="cr-pin" title="Has pinned chat(s)">&#x1F4CC;</span> ' : ''}${esc(p.name || p.id)}</span>
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
    if ((p.pinned_conversations || []).length) return 0;  // has a pinned chat → always top (survives restarts/interfaces)
    const fs = friendlyStatus(p);
    if (fs === 'asking') return 1;   // needs you → next
    if (fs === 'stuck')  return 2;   // blocked → next
    return 3;                        // everything else → recency below
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

// ── §1b Mobile "Waiting on you" inbox ───────────────────────────────────────
// A dedicated attention surface (replaces the old "Needs you" filter pill,
// Decision 5a). Reuses _buildAttentionList (each item carries a sessionId from
// §1a) and deep-links each row to the exact waiting chat via openProjectAtSession.
function renderInbox() {
  const list = document.getElementById('mobile-inbox-list');
  if (!list) return;
  const items = (typeof _buildAttentionList === 'function') ? _buildAttentionList() : [];
  if (!items.length) {
    list.innerHTML = '<div class="mib-empty">Nothing needs you right now.</div>';
    return;
  }
  list.innerHTML = items.map(it => `
    <div class="mib-row" data-project-id="${esc(it.projectId)}" data-session-id="${esc(it.sessionId || '')}">
      <span class="mib-icon">${it.icon}</span>
      <div class="mib-main">
        <div class="mib-project">${esc(it.project)}</div>
        <div class="mib-msg">${esc(it.msg)}</div>
      </div>
      <span class="mib-chev">&#x203A;</span>
    </div>`).join('');
  list.querySelectorAll('.mib-row').forEach(row => {
    row.addEventListener('click', () => {
      const sid = row.dataset.sessionId;
      closeInbox();
      if (sid) openProjectAtSession(row.dataset.projectId, sid);
      else openProjectModal(row.dataset.projectId);
    });
  });
}
function openInbox() {
  const el = document.getElementById('mobile-inbox');
  if (!el) return;
  renderInbox();
  el.classList.add('open');
  el.setAttribute('aria-hidden', 'false');
  if (!_mcInboxOpen) {
    try { history.pushState({ mc: 'inbox' }, ''); _mcInboxOpen = true; } catch (e) {}
  }
}
// DOM-only close — shared by the header button, row-tap, and the popstate
// handler (which has already consumed the sentinel, so it must NOT re-unwind).
function _closeInboxUI() {
  const el = document.getElementById('mobile-inbox');
  if (!el) return;
  el.classList.remove('open');
  el.setAttribute('aria-hidden', 'true');
}
function closeInbox() {
  if (_mcInboxOpen) { _mcInboxOpen = false; _mcUnwindHistory(1); }
  _closeInboxUI();
}
window.openInbox = openInbox;
window.closeInbox = closeInbox;
window.renderInbox = renderInbox;
window._closeInboxUI = _closeInboxUI;

// ── §1c Context-adaptive bottom nav bar ─────────────────────────────────────
// "Inside a project, the bar becomes the project" (mockup Turn 4). When a
// project modal is focused on mobile, the global bar swaps to project surfaces
// (Home / Chats / +New chat / Backlog / More); back to global otherwise.
// Self-healing: driven off live state every render tick with a change-guard, so
// no lifecycle path can leave a stale/orphaned bar and the 2s poll never
// clobbers the active :active state or an open ⋮ menu.
let _globalBarHTML = null;      // snapshot of the static global markup (captured once)
let _barContextPid = undefined; // current context key, to skip needless re-renders

function _focusedProjectModalId() {
  if (!isMobileChatList()) return null;
  if (typeof openModals === 'undefined' || !openModals.size) return null;
  let top = null, topZ = -1;
  openModals.forEach((entry, id) => {
    if (!id || id.startsWith('__')) return;         // skip special modals (settings/all-backlog/…)
    if (!entry || entry.minimized) return;
    const z = parseInt((entry.element && entry.element.style.zIndex) || entry.zIndex || 0, 10) || 0;
    if (z >= topZ) { topZ = z; top = entry.projectId || id; }
  });
  return top;
}

function _syncBottomBarContext() {
  const bar = document.getElementById('bottom-tab-bar');
  if (!bar) return;
  if (_globalBarHTML === null) _globalBarHTML = bar.innerHTML;  // capture static global markup once
  // Spec §1 (2026-07-06): the bottom bar exists ONLY on the Layer-1 Dashboard.
  // Inside a project — the conversation LIST (Layer 2) or a THREAD (Layer 3) —
  // there is NO bar; navigation is the header ‹ back + ⋮ project menu (iOS
  // push/pop; "the user chose no-bar"). So hide the bar whenever a project modal
  // is focused on mobile (and let the modal fill full height); restore the
  // global dashboard bar otherwise. (Supersedes the earlier context-adaptive
  // project bar.)
  const inProject = !!_focusedProjectModalId();
  const ctx = inProject ? '__hidden__' : '__global__';
  if (ctx === _barContextPid) return;   // unchanged → skip re-render
  _barContextPid = ctx;
  bar.classList.remove('project-context');
  bar.classList.toggle('mc-bar-hidden', inProject);
  document.body.classList.toggle('mc-modal-fullh', inProject);
  if (!inProject) bar.innerHTML = _globalBarHTML;
}
window._syncBottomBarContext = _syncBottomBarContext;

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
