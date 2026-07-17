// ── Multi-modal window manager ──────────────────────────────────────────────


// ── Persistent modal prefs (per-project size+zoom) and open-list snapshot ───
// mc_modal_prefs: { [projectId]: { width, height, zoom } } — survives reboot,
//   applied every time a project modal is opened.
// mc_open_modals: [ { projectId, left, top, minimized } ] — snapshot of which
//   project modals are open and where, restored once on page load.
function _loadModalPrefs() {
  try { return JSON.parse(localStorage.getItem('mc_modal_prefs') || '{}'); }
  catch { return {}; }
}
function _saveModalPrefs(prefs) {
  try { localStorage.setItem('mc_modal_prefs', JSON.stringify(prefs)); } catch {}
}
function _getModalPref(projectId) {
  const all = _loadModalPrefs();
  return all[projectId] || null;
}
// Text zoom is persisted SEPARATELY from geometry so it survives on mobile too.
// Geometry prefs (_get/_setModalPref) are desktop-only — the modal is
// full-screen on mobile — but a pinch-to-zoom text size should stick across
// reopen/reload on every device. Keyed by projectId in its own localStorage map.
function _getModalZoom(projectId) {
  try { return (JSON.parse(localStorage.getItem('mc_modal_zoom') || '{}') || {})[projectId] || 0; }
  catch { return 0; }
}
function _setModalZoom(projectId, size) {
  if (!projectId || projectId.startsWith('__') || !size) return;
  try {
    const all = JSON.parse(localStorage.getItem('mc_modal_zoom') || '{}') || {};
    all[projectId] = size;
    localStorage.setItem('mc_modal_zoom', JSON.stringify(all));
  } catch {}
}
let _modalPrefSaveTimer = null;
let _modalPrefPending = null; // latest in-memory prefs object awaiting flush
function _flushModalPrefs() {
  if (_modalPrefSaveTimer) { clearTimeout(_modalPrefSaveTimer); _modalPrefSaveTimer = null; }
  if (_modalPrefPending) { _saveModalPrefs(_modalPrefPending); _modalPrefPending = null; }
}
function _setModalPref(projectId, patch) {
  if (!projectId || projectId.startsWith('__')) return;
  if (_isMobileDevice || window.innerWidth <= 960) return; // mobile is full-screen, skip
  if (!_modalPrefPending) _modalPrefPending = _loadModalPrefs();
  _modalPrefPending[projectId] = Object.assign({}, _modalPrefPending[projectId] || {}, patch);
  // Debounce writes during continuous resize/scroll-zoom
  clearTimeout(_modalPrefSaveTimer);
  _modalPrefSaveTimer = setTimeout(_flushModalPrefs, 250);
}

function _saveOpenModalsSnapshot() {
  if (_isMobileDevice || window.innerWidth <= 960) return; // mobile: don't persist
  const list = [];
  for (const [modalId, entry] of openModals) {
    // Only persist project modals (synthetic IDs like __terminal_*, __hivemind_* are transient)
    if (!entry.projectId || modalId.startsWith('__')) continue;
    const r = entry.element.getBoundingClientRect();
    list.push({
      projectId: entry.projectId,
      left: Math.round(r.left),
      top: Math.round(r.top),
      minimized: !!entry.minimized,
      snap: entry.snap || null,
      preSnap: entry.preSnap || null,
      unpinned: !!entry.unpinned,
    });
  }
  try { localStorage.setItem('mc_open_modals', JSON.stringify(list)); } catch {}
}
function _loadOpenModalsSnapshot() {
  try { return JSON.parse(localStorage.getItem('mc_open_modals') || '[]'); }
  catch { return []; }
}

function getStatusColor(status) {
  const map = { active: 'var(--green)', blocked: 'var(--red)', waiting: 'var(--amber)', parked: 'var(--text-faint)' };
  return map[status] || 'var(--text-faint)';
}

function centerModalElement(win) {
  if (_isMobileDevice || window.innerWidth <= 960) {
    win.style.left = '0px';
    win.style.top = '0px';
    return;
  }
  const w = win.offsetWidth, h = win.offsetHeight;
  const offset = Math.max(0, openModals.size - 1) * 30;
  win.style.left = Math.max(0, (window.innerWidth - w) / 2 + offset) + 'px';
  win.style.top = Math.max(0, (window.innerHeight - h) / 2 + offset) + 'px';
}

// Anchor the Settings panel beside the sidebar "Settings" item it launched
// from (rather than dead-center). Uses the item's live rect, so it works
// whether the sidebar is hover-expanded (click) or collapsed (command palette).
// Clamped to stay fully on-screen. Mobile keeps the full-screen 0,0 layout.
function _positionSettingsModal(win) {
  if (_isMobileDevice || window.innerWidth <= 960) {
    win.style.left = '0px';
    win.style.top = '0px';
    return;
  }
  const item = document.querySelector('.sidebar-item[data-nav="settings"]');
  if (!item) { centerModalElement(win); return; }   // fallback: shouldn't happen on desktop
  const rect = item.getBoundingClientRect();
  const margin = 12;
  const w = win.offsetWidth || 480;
  const h = win.offsetHeight || Math.round(window.innerHeight * 0.6);
  // Just to the right of the item, aligned with its top…
  let left = rect.right + margin;
  let top = rect.top;
  // …then clamped so the whole panel stays on-screen (against its current
  // height — re-run after the async render when the real height is known).
  left = Math.max(margin, Math.min(left, window.innerWidth - w - margin));
  top = Math.max(margin, Math.min(top, window.innerHeight - h - margin));
  win.style.left = left + 'px';
  win.style.top = top + 'px';
}

function focusModal(modalId) {
  const entry = openModals.get(modalId);
  if (!entry || entry.minimized) return;
  if (focusedModalId && openModals.has(focusedModalId)) {
    openModals.get(focusedModalId).element.classList.remove('focused');
  }
  const z = nextModalZ++;
  entry.zIndex = z;
  entry.element.style.zIndex = z;
  entry.element.classList.add('focused');
  focusedModalId = modalId;
}
// ── Deep link: ?project=X&session=Y opens the project modal + session tab ───
// Called from three places:
//   1) Boot, after fetchProjects() resolves (URL-based, PWA cold-start).
//   2) Service worker `mc-deeplink` postMessage (PWA warm-tap).
//   3) Capacitor `pushNotificationActionPerformed` (native APK tap).
// On native, the tap can fire before allProjects is populated — in that case
// we cache the URL on window._mcPendingDeepLink and replay it from
// fetchProjects().then(...) below.
// §1: open a project modal AND select a specific session, with the same
// authoritative load + reconstruct-from-transcript fallback the URL deep-link
// path uses. Shared by _handleDeepLinkFromUrl (push/URL) and the "Needs you"
// feed so neither duplicates the race-prone logic. sid may be empty (opens the
// project at its list, like a bare openProjectModal).
async function openProjectAtSession(pid, sid) {
  try { openProjectModal(pid); } catch (e) { console.warn('openProjectModal', e); }
  if (!sid) return;
  // openProjectModal kicks off fetchAgentStatus async (and skips it entirely on
  // a warm tap where the modal is already open). Await an authoritative load
  // before selecting the tab so the chat doesn't render before the agent's
  // reply loads from server log_lines. If sid is stale (server restarted →
  // session revived under a new id) fall back to the project's real active
  // session so the conversation still shows.
  try { await fetchAgentStatus(pid); } catch (_) {}
  // If the target session isn't live (server bounced, not yet revived) it won't
  // be in the status response — reconstruct a read-only buffer from its
  // transcript so the tap shows the full conversation, not an empty tab.
  if (!agentStatusCache[sid] && !activeAgentTab[pid]) {
    try {
      const rr = await fetch(API_BASE + `/api/project/${pid}/session/${encodeURIComponent(sid)}/reconstruct`);
      if (rr.ok) {
        const rd = await rr.json();
        agentStatusCache[sid] = { status: 'completed', task: rd.task || '', projectId: pid,
          startedAt: rd.started_at || '', claudeSessionId: rd.claude_session_id || '',
          _readOnlyRevived: true };
        agentOutputBuffers[sid] = rd.log_lines || [];
        agentServerLines[sid] = (rd.log_lines || []).length;
        if (!agentHistory.find(h => h.sessionId === sid)) {
          const pName = (allProjects.find(x => x.id === pid) || {}).name || pid;
          agentHistory.unshift({ projectId: pid, sessionId: sid, projectName: pName,
            task: rd.task || '', status: 'completed', startedAt: rd.started_at || '' });
        }
      }
    } catch (_) {}
  }
  const targetSid = agentStatusCache[sid] ? sid : (activeAgentTab[pid] || sid);
  try { switchAgentTab(pid, targetSid); } catch (e) { console.warn('switchAgentTab', e); }
  try { refreshModalById(pid); } catch (_) {}
}

async function _handleDeepLinkFromUrl(rawUrl) {
  let url;
  try { url = new URL(rawUrl || window.location.href, window.location.origin); }
  catch (_) { return; }
  const params = url.searchParams;
  const pid = params.get('project');
  if (!pid) return;
  const sid = params.get('session') || '';
  if (!Array.isArray(allProjects) || allProjects.length === 0) {
    // Projects haven't loaded yet — stash and let the boot path retry.
    window._mcPendingDeepLink = rawUrl || url.toString();
    return;
  }
  const known = new Set(allProjects.map(p => p.id));
  if (!known.has(pid)) return;
  await openProjectAtSession(pid, sid);
  // Clean the URL so a manual refresh doesn't keep re-firing the deep link.
  try {
    const cleaned = new URL(window.location.href);
    cleaned.searchParams.delete('project');
    cleaned.searchParams.delete('session');
    history.replaceState({}, '', cleaned.pathname + (cleaned.search || '') + (cleaned.hash || ''));
  } catch (_) {}
}
function openProjectModal(projectId, restoreState) {
  const p = allProjects.find(x => x.id === projectId);
  if (!p) return;

  // Opening a project = reading it (WhatsApp semantics): clear its unread
  // badge and drop the bold. Re-render so the change shows on return.
  markProjectSeen(projectId);
  if (isMobileChatList()) renderProjects();

  // If already open, focus/restore it
  if (openModals.has(projectId)) {
    const entry = openModals.get(projectId);
    if (entry.minimized) restoreModal(projectId);
    focusModal(projectId);
    return;
  }

  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = projectId;

  // MOBILE ONLY: land on the conversation LIST (not a previously drilled-in
  // chat) every time a multi-conversation modal is FRESHLY opened — the list
  // is the home view; drilling in is an explicit tap. Single conversation
  // still auto-opens its chat (agentPanelHTML), 0 → dispatch. Runs only on a
  // true fresh open; refreshModal() during the session does NOT reset it, so
  // a drilled-in chat stays put while you read it. On DESKTOP the classic tab
  // strip persists the active tab across opens (pre-drill-down behavior).
  if (isMobileChatList()) {
    delete activeAgentTab[projectId];
    delete agentConvNew[projectId];
  }

  const content = document.createElement('div');
  content.className = 'modal-content';
  content.innerHTML = modalContentHTML(p);
  win.appendChild(content);

  document.getElementById('modal-layer').appendChild(win);
  const nameInput = content.querySelector('.name-edit');
  if (nameInput) autoSizeNameInput(nameInput);

  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(projectId, { projectId, element: win, minimized: false, zIndex: z });
  mcPushModalHistory();

  // Apply persistent per-project prefs (size + zoom) before positioning
  const pref = (!_isMobileDevice && window.innerWidth > 960) ? _getModalPref(projectId) : null;
  if (pref && pref.width)  content.style.width  = Math.min(pref.width,  window.innerWidth)  + 'px';
  if (pref && pref.height) content.style.height = Math.min(pref.height, window.innerHeight) + 'px';
  // Zoom re-applies on EVERY device (mobile included) from its own store, with a
  // fallback to the legacy per-project geometry pref for existing desktop users.
  const savedZoom = _getModalZoom(projectId) || (pref && pref.zoom) || 0;
  if (savedZoom) {
    modalZoomLevels[projectId] = savedZoom;
    applyModalZoom(win, savedZoom);
  }
  // Restore pin state (snapshot wins over saved pref for per-instance memory)
  const unpinnedToRestore = (restoreState && typeof restoreState.unpinned === 'boolean')
    ? restoreState.unpinned
    : !!(pref && pref.unpinned);
  if (unpinnedToRestore) {
    win.classList.add('is-unpinned');
    const e = openModals.get(projectId);
    if (e) e.unpinned = true;
  }

  // Restore snap state. Snap geometry from `_zoneRect` wins over both the
  // saved width/height (used as "free" size) and any restoreState left/top.
  // The snapshot (mc_open_modals) carries the per-instance snap; the pref
  // (mc_modal_prefs) carries the cross-reload default. Snapshot wins so
  // closing-then-reopening keeps your last snap.
  const snapToRestore = (restoreState && restoreState.snap) || (pref && pref.snap) || null;
  const preSnapToRestore = (restoreState && restoreState.preSnap) || (pref && pref.preSnap) || null;
  if (snapToRestore && _snapEnabled() && _zoneRect(snapToRestore)) {
    const entry = openModals.get(projectId);
    if (entry) {
      entry.snap = snapToRestore;
      entry.preSnap = preSnapToRestore;
    }
    win.classList.add('is-snapped');
    const r = _zoneRect(snapToRestore);
    win.style.left = r.left + 'px';
    win.style.top = r.top + 'px';
    content.style.width = r.width + 'px';
    content.style.height = r.height + 'px';
  } else if (restoreState && typeof restoreState.left === 'number' && !_isMobileDevice && window.innerWidth > 960) {
    // Position: restoreState (refresh/restore-on-load) wins; else center
    const w = win.offsetWidth, h = win.offsetHeight;
    win.style.left = Math.max(0, Math.min(window.innerWidth  - 100, restoreState.left)) + 'px';
    win.style.top  = Math.max(0, Math.min(window.innerHeight - 50,  restoreState.top))  + 'px';
  } else {
    centerModalElement(win);
  }

  if (restoreState && restoreState.minimized) {
    minimizeModal(projectId);
  } else {
    focusModal(projectId);
  }
  _saveOpenModalsSnapshot();
  // §1c: immediately switch the mobile bottom bar to this project's context.
  if (typeof _syncBottomBarContext === 'function') _syncBottomBarContext();
  // /api/projects trims backlog note/attachment BODIES to counts; lazy-load this
  // project's FULL backlog so the modal's note/attachment panels populate.
  Promise.all([fetchAgentStatus(projectId), fetchTerminalStatus(projectId), refreshProjectBacklog(projectId)])
    .then(() => refreshModalById(projectId));

  // Re-size agent chat when modal is resized + persist size pref. Skip the
  // size-persist while snapped — snap-driven resizes shouldn't clobber the
  // user's saved "free" size (which we want back when they unsnap).
  new ResizeObserver(() => {
    const entry = openModals.get(projectId);
    if (!entry || entry.minimized) return;
    const sid = activeAgentTab[projectId];
    if (sid && (modalActiveTab[projectId] || 'agent') === 'agent') {
      sizeAgentChat(entry.element, sid);
    }
    if (!entry.snap) {
      _setModalPref(projectId, { width: content.offsetWidth, height: content.offsetHeight });
    }
  }).observe(content);
}

function closeModalById(modalId) {
  const entry = openModals.get(modalId);
  if (!entry) return;
  // Clean up hivemind SSE connections
  if (modalId.startsWith('__hivemind_')) {
    const hmId = modalId.replace('__hivemind_', '');
    if (hivemindSSE[hmId]) { hivemindSSE[hmId].close(); delete hivemindSSE[hmId]; }
    clearTimeout(_hmDashDebounce[hmId]);
    delete _hmDashDebounce[hmId];
    delete _hmDashInFlight[hmId];
  }
  // Beacon report: stop its digest SSE on any close path (X / Esc / Home).
  if (modalId === '__beacon' && typeof window._beaconTeardown === 'function') {
    window._beaconTeardown();
  }
  // Clean up terminal pop-out resources
  cleanupTerminalModal(modalId);
  if (entry._statusBarObserver) { entry._statusBarObserver.disconnect(); delete entry._statusBarObserver; }
  entry.element.remove();
  const chip = document.getElementById('chip-' + CSS.escape(modalId));
  if (chip) chip.remove();
  openModals.delete(modalId);

  if (entry.projectId && !modalId.startsWith('__')) {
    const p = allProjects.find(x => x.id === entry.projectId);
    if (p && p.backlog) p.backlog.forEach(item => { openAttPanels.delete(item.id); openNotesPanels.delete(item.id); });
  }

  if (focusedModalId === modalId) {
    focusedModalId = null;
    let topId = null, topZ = 0;
    for (const [id, e] of openModals) {
      if (!e.minimized && e.zIndex > topZ) { topZ = e.zIndex; topId = id; }
    }
    if (topId) focusModal(topId);
  }
  _saveOpenModalsSnapshot();
  // §1c: revert/refresh the mobile bottom bar immediately so it doesn't linger
  // in project-context (with buttons pointing at a closed modal) until the next
  // poll tick. Guarded + idempotent (no-op on desktop / when context unchanged).
  if (typeof _syncBottomBarContext === 'function') _syncBottomBarContext();
  // A __-surface (Claydo / Skills / MCP / …) closed via its X while a PROJECT
  // modal is still open never reaches the size===0 unwind below, so its
  // sentinel would leak and swallow the NEXT back press. Unwind it here once no
  // other surface remains. On the hardware-back path popstate already cleared
  // the flag, so this is a no-op — no double-unwind.
  if (modalId.startsWith('__') && modalId !== '__settings'
      && openModals.size > 0
      && typeof _mcSurfaceOpen !== 'undefined' && _mcSurfaceOpen) {
    const anotherSurfaceOpen = Array.from(openModals.keys())
      .some(id => String(id).startsWith('__') && id !== '__settings');
    if (!anotherSurfaceOpen) {
      _mcSurfaceOpen = false;
      _mcUnwindHistory(1);
    }
  }
  // Closed via UI (X / Esc / Home), not via hardware back: unwind every MC
  // sentinel we pushed (L1 + L2 if drilled in) so a later back press isn't
  // swallowed by now-dead entries. On the hardware-back path the relevant
  // flag is already false (popstate cleared it), so n=0 and this is a no-op
  // — no double-close.
  if (openModals.size === 0) {
    // A still-true _mcModalHistoryActive means this is a UI close (X/Esc/Home);
    // on the hardware-back path popstate already cleared it. Only on a UI close
    // do we also drop a parked inbox sentinel (a chat opened FROM the inbox) —
    // the hardware-back path keeps it so popstate can reveal the inbox.
    const _uiClose = _mcModalHistoryActive;
    const _dropInbox = _uiClose && (typeof _mcConvFromInbox !== 'undefined')
      && _mcConvFromInbox && _mcInboxOpen;
    // A surface closed via its X (not hardware-back): its sentinel is still set
    // (the popstate path clears it before calling here), so unwind it too.
    const _dropSurface = (typeof _mcSurfaceOpen !== 'undefined') && _mcSurfaceOpen;
    const n = (_mcConvHistoryActive ? 1 : 0) + (_mcModalHistoryActive ? 1 : 0)
            + _mcSettingsNavDepth + (_mcSettingsHistoryActive ? 1 : 0)
            + (_dropInbox ? 1 : 0) + (_dropSurface ? 1 : 0);
    _mcConvHistoryActive = false;
    _mcModalHistoryActive = false;
    _mcSettingsNavDepth = 0;
    _mcSettingsHistoryActive = false;
    if (_dropInbox) { _mcConvFromInbox = false; _mcInboxOpen = false; }
    if (_dropSurface) _mcSurfaceOpen = false;
    _mcUnwindHistory(n);
  }
}

function closeModal() {
  if (focusedModalId) closeModalById(focusedModalId);
}

// ── Three-dot Modal Menu ──────────────────────────────────────────────────────

function toggleModalMenu(e, projectId) {
  e.stopPropagation();
  const dd = document.getElementById(`modal-menu-${projectId}`);
  if (!dd) return;
  const isOpen = dd.classList.contains('open');
  // Close all open modal menus first
  document.querySelectorAll('.modal-menu-dropdown.open').forEach(d => d.classList.remove('open'));
  document.querySelectorAll('.modal-menu-sub.open').forEach(d => d.classList.remove('open'));
  if (!isOpen) {
    dd.classList.add('open');
    const close = (ev) => {
      if (!dd.contains(ev.target) && !ev.target.closest('.modal-menu-btn')) {
        dd.classList.remove('open');
        dd.querySelectorAll('.modal-menu-sub.open').forEach(s => s.classList.remove('open'));
        document.removeEventListener('mousedown', close);
      }
    };
    setTimeout(() => document.addEventListener('mousedown', close), 0);
  }
}

function toggleModalMenuSub(e, subId) {
  e.stopPropagation();
  const sub = document.getElementById(subId);
  if (!sub) return;
  sub.classList.toggle('open');
}

// Collapsible "Advanced" group in the ⋮ menu — mirrors the sidebar's Advanced
// section (persisted, chevron rotates). Keeps the menu short by default.
function toggleModalMenuAdvanced(e, projectId) {
  e.stopPropagation();
  const grp = document.getElementById(`modal-menu-adv-group-${projectId}`);
  if (!grp) return;
  const open = !grp.classList.contains('expanded');
  grp.classList.toggle('expanded', open);
  const btn = e.currentTarget;
  if (btn && btn.classList) btn.classList.toggle('expanded', open);
  try { localStorage.setItem('mc_modal_menu_advanced_open', open ? '1' : '0'); } catch (_) {}
}

async function setProjectStatus(projectId, status) {
  document.querySelectorAll('.modal-menu-dropdown.open').forEach(d => d.classList.remove('open'));
  try {
    await fetch(API_BASE + `/api/project/${projectId}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ status })
    });
    await refreshSilent();
  } catch(e) {}
}

async function setProjectColor(projectId, color, bg) {
  document.querySelectorAll('.modal-menu-dropdown.open').forEach(d => d.classList.remove('open'));
  try {
    await fetch(API_BASE + `/api/project/${projectId}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ modal_color: { color, bg } })
    });
    await refreshSilent();
  } catch(e) {}
}

// Durable per-CONVERSATION pin toggle (chat-level — pins THIS chat, not the
// whole project). Resolves the tab's stable claude_session_id and hits
// /conversation-pin (which never bumps last_updated) so the pin is server-side,
// cross-interface and restart-proof — not per-browser localStorage.
// refreshSilent() re-renders the grid + open modal (refreshModalById preserves
// the live agent output DOM), flipping the tab marker and re-sorting tabs + the
// chat list. A chat with no claude_session_id yet (no first reply) can't be
// durably pinned, so we explain instead of writing a non-persisting pin.
async function togglePinConversationSession(projectId, sessionId) {
  const cache = (typeof agentStatusCache !== 'undefined') ? (agentStatusCache[sessionId] || {}) : {};
  const isPinned = !!cache.pinned;
  try {
    // Send session_id so the server resolves the authoritative claude_session_id
    // itself (the client's cached copy can be stale, esp. Mode B); conversation_id
    // is the fallback for a closed chat no longer in the live session map.
    const res = await fetch(API_BASE + `/api/project/${projectId}/conversation-pin`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ session_id: sessionId, conversation_id: cache.claudeSessionId || '', pinned: !isPinned })
    });
    if (res.status === 400) {
      if (typeof showToast === 'function') showToast('You can pin this chat once it has had its first reply.', 4000);
      return;
    }
    if (res.ok) {
      // Apply the server's authoritative result immediately so the marker +
      // re-sort flip without waiting on a fresh agent-status poll (refreshSilent
      // re-fetches /api/projects but NOT agent status).
      const data = await res.json().catch(() => ({}));
      if (agentStatusCache[sessionId]) agentStatusCache[sessionId].pinned = !!data.pinned;
      const p = (typeof allProjects !== 'undefined') ? allProjects.find(x => x.id === projectId) : null;
      if (p && Array.isArray(data.pinned_conversations)) p.pinned_conversations = data.pinned_conversations;
    }
    await refreshSilent();
  } catch(e) {}
}

async function setProjectModel(projectId, model) {
  document.querySelectorAll('.modal-menu-dropdown.open').forEach(d => d.classList.remove('open'));
  try {
    await fetch(API_BASE + `/api/project/${projectId}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ agent_model: model })
    });
    await refreshSilent();
  } catch(e) {}
}

async function setProjectEffort(projectId, effort) {
  document.querySelectorAll('.modal-menu-dropdown.open').forEach(d => d.classList.remove('open'));
  document.querySelectorAll('.modal-menu-sub.open').forEach(d => d.classList.remove('open'));
  try {
    await fetch(API_BASE + `/api/project/${projectId}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ agent_effort: effort })
    });
    await refreshSilent();
  } catch(e) {}
}

async function setProjectProvider(projectId, provider) {
  document.querySelectorAll('.modal-menu-dropdown.open').forEach(d => d.classList.remove('open'));
  document.querySelectorAll('.modal-menu-sub.open').forEach(d => d.classList.remove('open'));
  try {
    const r = await fetch(API_BASE + `/api/project/${projectId}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ provider: provider })
    });
    if (r.ok) {
      showToast('Provider set to ' + provider);
    } else {
      showToast('Failed to set provider');
    }
    await refreshSilent();
  } catch(e) { showToast('Error: ' + e); }
}

async function setProjectStreamingMode(projectId, mode) {
  // '' clears the override (inherit global); 'a'/'b' pin an explicit mode.
  const value = mode === '' ? null : (mode === 'b');
  try {
    await fetch(API_BASE + `/api/project/${projectId}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ use_streaming_agent: value })
    });
    if (value === null) showToast('Process mode follows the global default');
    else showToast(value ? 'Mode B (persistent process) for new sessions' : 'Mode A (spawn per turn) for new sessions');
    await refreshSilent();
  } catch(e) {}
}

const EMOJI_CHOICES = [
  '\u26bd', '\ud83c\udfc0', '\ud83c\udfc8', '\ud83c\udfbe', '\ud83c\udfaf', '\ud83c\udfae', '\ud83c\udfa8',
  '\ud83d\udcca', '\ud83d\udcc8', '\ud83d\udcb0', '\ud83d\udcb3', '\ud83c\udfe6', '\ud83d\udcbc', '\ud83d\udcc5',
  '\ud83d\ude80', '\u2728', '\u26a1', '\ud83d\udd25', '\ud83c\udf1f', '\ud83d\udca1', '\ud83c\udfaf',
  '\ud83e\udd16', '\ud83e\uddd1\u200d\ud83d\udcbb', '\ud83d\udee0\ufe0f', '\ud83d\udd27', '\u2699\ufe0f', '\ud83d\udcbb', '\ud83d\udce1',
  '\ud83d\udcdd', '\ud83d\udcda', '\ud83d\udd0d', '\ud83d\udce8', '\ud83d\udcac', '\ud83d\udce3', '\ud83d\udccc',
  '\ud83c\udf31', '\ud83c\udf3f', '\ud83c\udf3c', '\ud83c\udf34', '\ud83c\udf0d', '\ud83c\udf0a', '\ud83c\udf1e',
  '\ud83e\uddd1\u200d\ud83c\udf73', '\ud83c\udf55', '\u2615', '\ud83c\udf70', '\ud83c\udf4e', '\ud83c\udf47', '\ud83c\udf53',
  '\ud83d\udc36', '\ud83d\udc31', '\ud83e\udd81', '\ud83d\udc3c', '\ud83e\udd89', '\ud83d\udc19', '\ud83e\udd84',
];
let _emojiPickerProjectId = null;

function editProjectEmoji(projectId) {
  document.querySelectorAll('.modal-menu-dropdown.open').forEach(d => d.classList.remove('open'));
  const p = allProjects.find(x => x.id === projectId);
  if (!p) return;
  _emojiPickerProjectId = projectId;
  const grid = document.getElementById('epk-grid');
  grid.innerHTML = EMOJI_CHOICES.map(e => `<button class="epk-cell" onclick="pickEmoji('${e}')">${e}</button>`).join('');
  document.getElementById('epk-clear-btn').onclick = () => pickEmoji('');
  document.getElementById('emoji-picker-overlay').classList.add('visible');
}

function closeEmojiPicker() {
  document.getElementById('emoji-picker-overlay').classList.remove('visible');
  _emojiPickerProjectId = null;
}

async function pickEmoji(emoji) {
  const projectId = _emojiPickerProjectId;
  closeEmojiPicker();
  if (!projectId) return;
  try {
    await fetch(API_BASE + `/api/project/${projectId}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ emoji })
    });
    await refreshSilent();
    // The picker may have been opened from the profile dialog — sync its emoji button.
    if (_profileDialogProjectId === projectId) _renderProfileDialog();
  } catch(e) {}
}

async function generateProjectProfile(projectId, opts) {
  document.querySelectorAll('.modal-menu-dropdown.open').forEach(d => d.classList.remove('open'));
  const p = allProjects.find(x => x.id === projectId);
  if (!p) return;
  const overwrite = !!(opts && opts.overwriteEmoji);
  showToast('Generating project profile...', 3000);
  try {
    const r = await fetch(API_BASE + `/api/project/${projectId}/generate_summary`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ overwrite_emoji: overwrite })
    });
    const data = await r.json();
    if (!r.ok || !data.ok) {
      showToast('Profile generation failed: ' + (data.error || r.status), 6000);
      return;
    }
    showToast(`Profile updated ${data.emoji || ''}`, 3000);
    await refreshSilent();
  } catch(e) {
    showToast('Profile generation failed: ' + e.message, 6000);
  }
}

// ── Edit Profile dialog (emoji + description + auto-generate in one place) ──
let _profileDialogProjectId = null;

function openProjectProfileDialog(projectId) {
  const p = allProjects.find(x => x.id === projectId);
  if (!p) return;
  _profileDialogProjectId = projectId;
  _renderProfileDialog();
  document.getElementById('profile-dialog-overlay').classList.add('visible');
}

function closeProfileDialog() {
  document.getElementById('profile-dialog-overlay').classList.remove('visible');
  _profileDialogProjectId = null;
}

function _renderProfileDialog() {
  const p = allProjects.find(x => x.id === _profileDialogProjectId);
  const box = document.getElementById('profile-dialog-box');
  if (!p || !box) return;
  box.innerHTML = `
    <div class="mc-dialog-header">
      <span>Project profile</span>
      <button class="mc-dialog-close" onclick="closeProfileDialog()" title="Close">&#10005;</button>
    </div>
    <div class="settings-row">
      <div><div class="settings-label">Emoji</div><div class="settings-hint">Shown on the tile and modal</div></div>
      <button class="btn-dispatch" style="background:var(--surface3);border-color:var(--border2);color:var(--text);font-size:16px;min-width:48px" onclick="editProjectEmoji('${esc(p.id)}')">${p.emoji ? esc(p.emoji) : 'Pick…'}</button>
    </div>
    <div class="settings-row" style="align-items:flex-start;flex-direction:column;gap:6px">
      <div><div class="settings-label">Folder</div><div class="settings-hint">Working directory the agent runs in — enables dispatch, skills, MCP</div></div>
      <div style="display:flex;width:100%;gap:6px;align-items:center">
        <input id="pfd-path" class="path-input" type="text" value="${esc(p.project_path || '')}"
          placeholder="No folder selected — click Browse, or type a path"
          onblur="saveProjectPath('${esc(p.id)}',this)"
          onkeydown="if(event.key==='Enter'){this.blur()}"
          style="flex:1;box-sizing:border-box">
        <button class="btn-browse" onclick="openFolderPicker('${esc(p.id)}')" title="Browse for folder">Browse…</button>
      </div>
    </div>
    <div class="settings-row" style="align-items:flex-start;flex-direction:column;gap:6px">
      <div><div class="settings-label">Description</div><div class="settings-hint">What this project is — agents see this as context</div></div>
      <textarea id="pfd-desc" rows="4" style="width:100%;box-sizing:border-box;background:var(--bg);border:1px solid var(--border);color:var(--text);border-radius:6px;padding:8px;font-size:12px;font-family:inherit;resize:vertical">${esc(p.description || '')}</textarea>
    </div>
    <div class="settings-row">
      <div><div class="settings-label">Auto-generate</div><div class="settings-hint">Agent writes the summary + emoji from the project files</div></div>
      <button class="btn-dispatch" style="background:var(--surface3);border-color:var(--border2);color:var(--text)" onclick="closeProfileDialog();generateProjectProfile('${esc(p.id)}')">&#x2728; ${p.summary ? 'Regenerate' : 'Generate'}</button>
    </div>
    <div class="mc-dialog-footer">
      <button class="btn-dispatch" style="background:var(--surface3);border-color:var(--border2);color:var(--text)" onclick="closeProfileDialog()">Cancel</button>
      <button class="btn-add" onclick="saveProfileDialog()">Save</button>
    </div>`;
}

async function saveProfileDialog() {
  const projectId = _profileDialogProjectId;
  const ta = document.getElementById('pfd-desc');
  const description = ta ? ta.value : null;
  closeProfileDialog();
  if (!projectId || description === null) return;
  try {
    await fetch(API_BASE + `/api/project/${projectId}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ description })
    });
    await refreshSilent();
  } catch(e) {}
}

// ── Agent Settings dialog (per-project overrides of the global agent config) ──
const MC_MODEL_CHOICES = [
  ['', 'Default (global)'],
  ['claude-fable-5', 'Fable 5'],
  ['claude-sonnet-4-6', 'Sonnet 4.6'],
  ['claude-opus-4-8', 'Opus 4.8'],
  ['claude-haiku-4-5-20251001', 'Haiku 4.5'],
];
const MC_EFFORT_CHOICES = [
  ['', 'Default (global)'], ['low', 'Low'], ['medium', 'Medium'],
  ['high', 'High'], ['xhigh', 'Extra high'], ['max', 'Max'],
];

function _modelShortLabel(id) {
  const m = MC_MODEL_CHOICES.find(c => c[0] === id);
  return m ? m[1] : id;
}
// Cross-module: the composer's Model picker (conversation.js) reuses this list —
// single source of truth for the model ids/labels.
window.MC_MODEL_CHOICES = MC_MODEL_CHOICES;

let _agentSettingsProjectId = null;

async function openAgentSettingsDialog(projectId) {
  _agentSettingsProjectId = projectId;
  // Provider row needs the runtime list; cached after the first fetch, and
  // awaiting here closes the old menu's lost-race-on-mobile gap for good.
  await _ensureAgentProviders();
  _renderAgentSettingsDialog();
  document.getElementById('agent-settings-overlay').classList.add('visible');
}

function closeAgentSettingsDialog() {
  document.getElementById('agent-settings-overlay').classList.remove('visible');
  _agentSettingsProjectId = null;
}

function _renderAgentSettingsDialog() {
  const p = allProjects.find(x => x.id === _agentSettingsProjectId);
  const box = document.getElementById('agent-settings-box');
  if (!p || !box) return;
  const modelSel = MC_MODEL_CHOICES.map(([v, l]) =>
    `<option value="${v}"${(p.agent_model || '') === v ? ' selected' : ''}>${l}</option>`).join('');
  const effortSel = MC_EFFORT_CHOICES.map(([v, l]) =>
    `<option value="${v}"${(p.agent_effort || '') === v ? ' selected' : ''}>${l}</option>`).join('');

  // Provider row only when more than one runtime is registered — with just
  // claude available it'd be noise (same rule as the composer picker).
  const provs = _agentProviders || [];
  let providerRow = '';
  if (provs.length > 1) {
    const current = (p.provider || 'claude').toLowerCase();
    const opts = provs.map(rt =>
      `<option value="${esc(rt.name)}"${current === rt.name ? ' selected' : ''}${(!rt.installed && rt.name !== 'claude') ? ' disabled' : ''}>${esc(rt.display_name)}${rt.installed ? '' : ' (not installed)'}</option>`).join('');
    providerRow = `
    <div class="settings-row">
      <div><div class="settings-label">Default provider</div><div class="settings-hint">Seeds new chats — each chat keeps its provider; switch per-chat in the composer</div></div>
      <select class="settings-select" onchange="_agentSettingsSet('provider', this.value)">${opts}</select>
    </div>`;
  }

  const globalMode = _globalConfig.use_streaming_agent ? 'Mode B' : 'Mode A';
  const modeVal = (p.use_streaming_agent === true) ? 'b' : (p.use_streaming_agent === false) ? 'a' : '';
  box.innerHTML = `
    <div class="mc-dialog-header">
      <span>Agent settings — ${esc(p.name || p.id)}</span>
      <button class="mc-dialog-close" onclick="closeAgentSettingsDialog()" title="Close">&#10005;</button>
    </div>
    <div class="settings-hint" style="margin-bottom:10px">Overrides for this project. Global defaults live in Settings &rarr; Agent.</div>
    <div class="settings-row">
      <div><div class="settings-label">Model</div><div class="settings-hint">Default for new chats in this project</div></div>
      <select class="settings-select" onchange="_agentSettingsSet('agent_model', this.value)">${modelSel}</select>
    </div>
    <div class="settings-row">
      <div><div class="settings-label">Effort</div><div class="settings-hint">How hard the model thinks per request</div></div>
      <select class="settings-select" onchange="_agentSettingsSet('agent_effort', this.value)">${effortSel}</select>
    </div>
    ${providerRow}
    <div class="settings-row">
      <div><div class="settings-label">Process mode</div><div class="settings-hint">Mode B keeps one persistent process per chat; Mode A spawns per turn</div></div>
      <select class="settings-select" onchange="_agentSettingsSet('use_streaming_agent', this.value)">
        <option value=""${modeVal === '' ? ' selected' : ''}>Default (global — ${globalMode})</option>
        <option value="b"${modeVal === 'b' ? ' selected' : ''}>Mode B — persistent</option>
        <option value="a"${modeVal === 'a' ? ' selected' : ''}>Mode A — spawn per turn</option>
      </select>
    </div>
    <div class="mc-dialog-footer">
      <button class="btn-add" onclick="closeAgentSettingsDialog()">Done</button>
    </div>`;
}

// Selects apply immediately (same as the old menu pickers did); the re-render
// keeps the dialog in sync with the refreshed project record.
async function _agentSettingsSet(field, value) {
  const projectId = _agentSettingsProjectId;
  if (!projectId) return;
  if (field === 'agent_model') await setProjectModel(projectId, value);
  else if (field === 'agent_effort') await setProjectEffort(projectId, value);
  else if (field === 'provider') await setProjectProvider(projectId, value);
  else if (field === 'use_streaming_agent') await setProjectStreamingMode(projectId, value);
  _renderAgentSettingsDialog();
}

async function deleteProject(projectId) {
  document.querySelectorAll('.modal-menu-dropdown.open').forEach(d => d.classList.remove('open'));
  const p = allProjects.find(x => x.id === projectId);
  const name = p ? (p.name || p.id) : projectId;
  if (!confirm(`Delete project "${name}"?\n\nThis will permanently remove the project and all its data.`)) return;
  try {
    const r = await fetch(API_BASE + `/api/project/${projectId}`, { method: 'DELETE' });
    if (r.ok) {
      closeModalById(projectId);
      await refreshSilent();
    } else {
      alert('Failed to delete project.');
    }
  } catch(e) { alert('Failed to delete project.'); }
}

function minimizeModal(modalId) {
  const entry = openModals.get(modalId);
  if (!entry) return;
  entry.minimized = true;
  entry.savedScrollTop = entry.element.querySelector('.modal-scroll-body')?.scrollTop || 0;
  entry.element.classList.add('minimized');

  const tray = document.getElementById('minimized-tray');
  const p = entry.projectId ? allProjects.find(x => x.id === entry.projectId) : null;
  const name = p ? (p.name || p.id) : modalId.replace(/^__/, '').replace(/_/g, ' ');
  const statusColor = p ? getStatusColor(p.status) : 'var(--text-faint)';

  const chipEl = document.createElement('div');
  chipEl.className = 'minimized-chip';
  chipEl.id = 'chip-' + modalId;
  chipEl.innerHTML = `
    <span class="chip-status" style="background:${statusColor}"></span>
    <span>${esc(name)}</span>
    <span class="chip-close" onclick="event.stopPropagation(); closeModalById('${esc(modalId)}')" title="Close">&#10005;</span>`;
  chipEl.addEventListener('click', (e) => {
    if (!e.target.closest('.chip-close')) restoreModal(modalId);
  });
  tray.appendChild(chipEl);

  if (focusedModalId === modalId) {
    focusedModalId = null;
    let topId = null, topZ = 0;
    for (const [id, e] of openModals) {
      if (!e.minimized && e.zIndex > topZ) { topZ = e.zIndex; topId = id; }
    }
    if (topId) focusModal(topId);
  }
  _saveOpenModalsSnapshot();
}

function showDesktop() {
  for (const [modalId, entry] of openModals) {
    if (!entry.minimized) minimizeModal(modalId);
  }
}
function toggleCommandPalette() {
  cmdPaletteOpen = !cmdPaletteOpen;
  const overlay = document.getElementById('cmd-overlay');
  if (!overlay) return;
  overlay.classList.toggle('visible', cmdPaletteOpen);
  if (cmdPaletteOpen) {
    const inp = document.getElementById('cmd-input');
    if (inp) { inp.value = ''; setTimeout(() => inp.focus(), 100); }
    cmdSelectedIndex = 0;
    renderCommandResults('');
  }
}

function renderCommandResults(query) {
  const results = [];
  const q = query.toLowerCase();

  // Projects group
  const matchingProjects = allProjects.filter(p => !isIncognitoProject(p) && (!q || (p.name||p.id).toLowerCase().includes(q)));
  if (matchingProjects.length) {
    results.push({ type: 'group', label: 'Projects' });
    matchingProjects.forEach(p => results.push({
      type: 'item', icon: '&#x25CF;', text: `Open ${p.name||p.id}`,
      hint: p.status, action: () => { toggleCommandPalette(); openProjectModal(p.id); }
    }));
  }

  // Actions group
  const actions = [
    { text: 'New Project', icon: '&#x2B;', action: () => { toggleCommandPalette(); openNewProjectForm(); } },
    { text: 'Open Incognito', icon: '&#x1F576;', action: () => { toggleCommandPalette(); openIncognito(); } },
    { text: 'Open Scheduler', icon: '&#x23F1;', action: () => { toggleCommandPalette(); openScheduler(); } },
    { text: 'Settings', icon: '&#x2699;', action: () => { toggleCommandPalette(); openSettings(); } },
    { text: 'Shared Rules', icon: '&#x1F4DC;', action: () => { toggleCommandPalette(); openSharedRulesEditor(); } },
    { text: 'Processes', icon: '&#x2630;', action: () => { toggleCommandPalette(); openProcessManager(); } },
    { text: 'Minimize All', icon: '&#x1F5D4;', action: () => { toggleCommandPalette(); showDesktop(); } },
    { text: 'Take Tour', icon: '?', action: () => { toggleCommandPalette(); startWalkthrough(); } },
  ];
  const matchingActions = actions.filter(a => !q || a.text.toLowerCase().includes(q));
  if (matchingActions.length) {
    results.push({ type: 'group', label: 'Actions' });
    matchingActions.forEach(a => results.push({ type: 'item', ...a }));
  }

  // View group
  const views = [
    { text: 'Grid View', icon: '&#x25A6;', action: () => { toggleCommandPalette(); setView('grid'); } },
    { text: 'List View', icon: '&#x2630;', action: () => { toggleCommandPalette(); setView('list'); } },
    { text: 'Toggle Compact', icon: '&#x25A6;', action: () => { toggleCommandPalette(); toggleDensity(); } },
    { text: 'Toggle Feed', icon: '&#x276E;', action: () => { toggleCommandPalette(); toggleFeed(); } },
  ];
  const matchingViews = views.filter(v => !q || v.text.toLowerCase().includes(q));
  if (matchingViews.length) {
    results.push({ type: 'group', label: 'View' });
    matchingViews.forEach(v => results.push({ type: 'item', ...v }));
  }

  // Render
  const container = document.getElementById('cmd-results');
  if (!container) return;
  const items = results.filter(r => r.type === 'item');
  if (cmdSelectedIndex >= items.length) cmdSelectedIndex = Math.max(0, items.length - 1);
  let itemIdx = 0;
  container.innerHTML = results.map(r => {
    if (r.type === 'group') return `<div class="cmd-group-label">${r.label}</div>`;
    const selected = itemIdx === cmdSelectedIndex ? ' selected' : '';
    const html = `<div class="cmd-item${selected}" data-idx="${itemIdx}">
      <span class="ci-icon">${r.icon}</span>
      <span class="ci-text">${esc(r.text)}</span>
      ${r.hint ? `<span class="ci-hint">${esc(r.hint)}</span>` : ''}
    </div>`;
    itemIdx++;
    return html;
  }).join('');

  // Store actions for keyboard execution
  window._cmdActions = items.map(r => r.action);

  // Click handlers
  container.querySelectorAll('.cmd-item').forEach(el => {
    el.addEventListener('click', () => {
      const idx = parseInt(el.dataset.idx);
      const action = window._cmdActions[idx];
      if (action) action();
    });
  });
}

function restoreModal(modalId) {
  const entry = openModals.get(modalId);
  if (!entry) return;
  entry.minimized = false;
  entry.element.classList.remove('minimized');
  const chip = document.getElementById('chip-' + CSS.escape(modalId));
  if (chip) chip.remove();
  if (entry.projectId && !modalId.startsWith('__hivemind_')) refreshModalById(modalId);
  const scrollBody = entry.element.querySelector('.modal-scroll-body');
  if (scrollBody && entry.savedScrollTop) scrollBody.scrollTop = entry.savedScrollTop;
  focusModal(modalId);
  _saveOpenModalsSnapshot();
}


// ── interop: window re-exposure for inline/generated/cross-module callers ──
window._flushModalPrefs = _flushModalPrefs;
window._getModalZoom = _getModalZoom;
window._setModalZoom = _setModalZoom;
window._setModalPref = _setModalPref;
window._saveOpenModalsSnapshot = _saveOpenModalsSnapshot;
window._loadOpenModalsSnapshot = _loadOpenModalsSnapshot;
window.centerModalElement = centerModalElement;
window._positionSettingsModal = _positionSettingsModal;
window.focusModal = focusModal;
window._handleDeepLinkFromUrl = _handleDeepLinkFromUrl;
window.openProjectModal = openProjectModal;
window.openProjectAtSession = openProjectAtSession;
window.closeModalById = closeModalById;
window.toggleModalMenu = toggleModalMenu;
window.toggleModalMenuSub = toggleModalMenuSub;
window.toggleModalMenuAdvanced = toggleModalMenuAdvanced;
window.setProjectStatus = setProjectStatus;
window.setProjectColor = setProjectColor;
window.togglePinConversationSession = togglePinConversationSession;
window.setProjectModel = setProjectModel;
window.setProjectEffort = setProjectEffort;
window.setProjectProvider = setProjectProvider;
window.setProjectStreamingMode = setProjectStreamingMode;
window.editProjectEmoji = editProjectEmoji;
window.closeEmojiPicker = closeEmojiPicker;
window.generateProjectProfile = generateProjectProfile;
window.openProjectProfileDialog = openProjectProfileDialog;
window.closeProfileDialog = closeProfileDialog;
window.saveProfileDialog = saveProfileDialog;
window.openAgentSettingsDialog = openAgentSettingsDialog;
window.closeAgentSettingsDialog = closeAgentSettingsDialog;
window._agentSettingsSet = _agentSettingsSet;
window._modelShortLabel = _modelShortLabel;
window.deleteProject = deleteProject;
window.minimizeModal = minimizeModal;
window.showDesktop = showDesktop;
window.toggleCommandPalette = toggleCommandPalette;
window.renderCommandResults = renderCommandResults;
window.restoreModal = restoreModal;
window.pickEmoji = pickEmoji;
