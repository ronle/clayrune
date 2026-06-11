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
  try { openProjectModal(pid); } catch (e) { console.warn('openProjectModal', e); }
  if (sid) {
    // openProjectModal kicks off fetchAgentStatus async (and skips it
    // entirely on a warm tap where the modal is already open). The old
    // fixed-80ms guess lost that race on mobile cold-start, so the chat
    // rendered before the agent's reply was loaded from server log_lines —
    // user saw only their own message. Await an authoritative load, then
    // select the tab. If the URL's session id is stale (server restarted →
    // session revived under a new id) fall back to the project's real
    // active session so the conversation still shows.
    try { await fetchAgentStatus(pid); } catch (_) {}
    // If the deep-linked session isn't live (server bounced and it hasn't
    // been revived — no follow-up sent yet), it won't be in the status
    // response at all. Reconstruct a read-only buffer from its transcript so
    // the tap shows the full conversation (incl. the agent reply the push
    // was about) instead of an empty tab. Falls back silently if the session
    // can't be reconstructed.
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
  if (pref && pref.zoom) {
    modalZoomLevels[projectId] = pref.zoom;
    applyModalZoom(win, pref.zoom);
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
  Promise.all([fetchAgentStatus(projectId), fetchTerminalStatus(projectId)])
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
  // Closed via UI (X / Esc / Home), not via hardware back: unwind every MC
  // sentinel we pushed (L1 + L2 if drilled in) so a later back press isn't
  // swallowed by now-dead entries. On the hardware-back path the relevant
  // flag is already false (popstate cleared it), so n=0 and this is a no-op
  // — no double-close.
  if (openModals.size === 0) {
    const n = (_mcConvHistoryActive ? 1 : 0) + (_mcModalHistoryActive ? 1 : 0)
            + _mcSettingsNavDepth + (_mcSettingsHistoryActive ? 1 : 0);
    _mcConvHistoryActive = false;
    _mcModalHistoryActive = false;
    _mcSettingsNavDepth = 0;
    _mcSettingsHistoryActive = false;
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

async function toggleProjectRemoteControl(projectId, enabled) {
  document.querySelectorAll('.modal-menu-dropdown.open').forEach(d => d.classList.remove('open'));
  try {
    await fetch(API_BASE + `/api/project/${projectId}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ agent_remote_control: enabled })
    });
    showToast(enabled ? 'Remote control enabled' : 'Remote control disabled');
    await refreshSilent();
  } catch(e) {}
}

async function toggleProjectStreaming(projectId, enabled) {
  document.querySelectorAll('.modal-menu-dropdown.open').forEach(d => d.classList.remove('open'));
  try {
    await fetch(API_BASE + `/api/project/${projectId}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ use_streaming_agent: enabled })
    });
    showToast(enabled ? 'Mode B (streaming) enabled — new sessions will use persistent process' : 'Mode B disabled — new sessions will use Mode A (spawn per turn)');
    await refreshSilent();
  } catch(e) {}
}

function editProjectDescription(projectId) {
  document.querySelectorAll('.modal-menu-dropdown.open').forEach(d => d.classList.remove('open'));
  const p = allProjects.find(x => x.id === projectId);
  if (!p) return;
  const current = p.description || '';
  const val = prompt('Project description:', current);
  if (val === null) return; // cancelled
  (async () => {
    try {
      await fetch(API_BASE + `/api/project/${projectId}`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ description: val })
      });
      await refreshSilent();
    } catch(e) {}
  })();
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
window._setModalPref = _setModalPref;
window._saveOpenModalsSnapshot = _saveOpenModalsSnapshot;
window._loadOpenModalsSnapshot = _loadOpenModalsSnapshot;
window.centerModalElement = centerModalElement;
window._positionSettingsModal = _positionSettingsModal;
window.focusModal = focusModal;
window._handleDeepLinkFromUrl = _handleDeepLinkFromUrl;
window.openProjectModal = openProjectModal;
window.closeModalById = closeModalById;
window.toggleModalMenu = toggleModalMenu;
window.toggleModalMenuSub = toggleModalMenuSub;
window.setProjectStatus = setProjectStatus;
window.setProjectColor = setProjectColor;
window.setProjectModel = setProjectModel;
window.setProjectEffort = setProjectEffort;
window.setProjectProvider = setProjectProvider;
window.toggleProjectRemoteControl = toggleProjectRemoteControl;
window.toggleProjectStreaming = toggleProjectStreaming;
window.editProjectDescription = editProjectDescription;
window.editProjectEmoji = editProjectEmoji;
window.closeEmojiPicker = closeEmojiPicker;
window.generateProjectProfile = generateProjectProfile;
window.deleteProject = deleteProject;
window.minimizeModal = minimizeModal;
window.showDesktop = showDesktop;
window.toggleCommandPalette = toggleCommandPalette;
window.renderCommandResults = renderCommandResults;
window.restoreModal = restoreModal;
window.pickEmoji = pickEmoji;
