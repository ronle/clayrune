// ── Cross-project Hivemind view ─────────────────────────────────────────────
let _allHivemindFilter = { status: 'active', search: '', project: 'all' };
let _allHivemindsCache = { hiveminds: [], loaded: false, loading: false };

// Hivemind is treated as "stale" if it's nominally active/paused but the
// orchestrator hasn't moved in HM_STALE_HOURS — usually the server restarted
// or the run was abandoned and the manifest never got updated.
const HM_STALE_HOURS = 24;
function _hmEffectiveStatus(hm) {
  const real = hm && hm.status;
  if ((real === 'active' || real === 'paused') && hm && hm.updated_at) {
    const ageH = (Date.now() - new Date(hm.updated_at).getTime()) / 3600000;
    if (!isNaN(ageH) && ageH > HM_STALE_HOURS) return 'stale';
  }
  return real;
}
function _hmShortId(id) {
  if (!id) return '';
  return id.startsWith('hm_') ? id.slice(3, 11) : id.slice(0, 8);
}

function openAllHivemindsForProject(projectId) {
  _allHivemindFilter.project = projectId || 'all';
  _allHivemindFilter.status = 'all';  // show every hivemind for that project, not just active
  openAllHiveminds();
}

async function openAllHiveminds() {
  const modalId = '__all_hivemind';
  if (openModals.has(modalId)) {
    const entry = openModals.get(modalId);
    if (entry.minimized) restoreModal(modalId);
    focusModal(modalId);
    renderAllHiveminds();
    loadAllHiveminds();
    return;
  }

  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content';
  _clampModalSize(content, 980);
  content.innerHTML = `
    <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:16px 24px 12px 28px">
      <span style="font-size:16px;font-weight:700;color:var(--text)">&#x1F41D; Hivemind</span>
      <div class="modal-window-controls" style="position:static;display:flex;gap:4px">
        <button class="modal-minimize" onclick="minimizeModal('${modalId}')" title="Minimize">&#x2015;</button>
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
    </div>
    <div style="padding:4px 24px 20px 28px">
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:12px;flex-wrap:wrap">
        <input type="text" id="ahm-search" placeholder="Search title/goal..." value="${esc(_allHivemindFilter.search)}"
          style="flex:1;min-width:180px;padding:6px 10px;font-size:12px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text)"
          oninput="_allHivemindFilter.search=this.value;renderAllHiveminds()">
        <select id="ahm-status" style="padding:6px 8px;font-size:12px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text)"
          onchange="_allHivemindFilter.status=this.value;renderAllHiveminds()">
          <option value="active"${_allHivemindFilter.status==='active'?' selected':''}>Active</option>
          <option value="paused"${_allHivemindFilter.status==='paused'?' selected':''}>Paused</option>
          <option value="stale"${_allHivemindFilter.status==='stale'?' selected':''}>Stale</option>
          <option value="completed"${_allHivemindFilter.status==='completed'?' selected':''}>Completed</option>
          <option value="all"${_allHivemindFilter.status==='all'?' selected':''}>All</option>
        </select>
        <select id="ahm-project" style="padding:6px 8px;font-size:12px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text);max-width:200px"
          onchange="_allHivemindFilter.project=this.value;renderAllHiveminds()">
          <option value="all">All projects</option>
        </select>
        <span id="ahm-count" style="font-size:11px;color:var(--text-faint)"></span>
        <button class="btn-add" style="margin-left:auto" onclick="newHivemindFromGlobal()">+ New Hivemind</button>
      </div>
      <div id="ahm-list" style="max-height:65vh;overflow-y:auto"></div>
    </div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);

  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);

  renderAllHiveminds();
  loadAllHiveminds();
}

async function loadAllHiveminds() {
  if (_allHivemindsCache.loading) return;
  _allHivemindsCache.loading = true;
  try {
    const res = await fetch(API_BASE + '/api/hivemind/list');
    const hiveminds = await res.json();
    // Enrich each with workstream summaries
    await Promise.all(hiveminds.map(async hm => {
      try {
        const detail = await fetch(API_BASE + `/api/hivemind/${hm.id}`);
        const data = await detail.json();
        hm._workstreams = data.workstreams || [];
        hm._recent_messages = data.recent_messages || [];
      } catch(e) {
        hm._workstreams = [];
        hm._recent_messages = [];
      }
    }));
    _allHivemindsCache = { hiveminds, loaded: true, loading: false };
    renderAllHiveminds();
  } catch(e) {
    _allHivemindsCache = { hiveminds: [], loaded: true, loading: false };
    renderAllHiveminds();
  }
}

function _hmTreeMiniViz(ws) {
  if (!ws || !ws.length) {
    return `<div class="hm-mini-tree empty">Orchestrator decomposing the goal...</div>`;
  }
  const chips = ws.map(w => {
    const st = w.status || 'pending';
    const icon = ({
      completed: '&#x2714;',
      active: '&#x25CF;',
      pending: '&#x25CB;',
      blocked: '&#x23F3;',
      paused: '&#x23F8;',
      failed: '&#x2716;',
    })[st] || '&#x25CB;';
    const label = (w.title || w.id || '').substring(0, 28);
    const fc = w.findings_count || 0;
    return `<div class="hm-tree-worker st-${esc(st)}" title="${esc(w.title || w.id)} — ${esc(st)}, ${fc} findings">
      <span class="hm-tree-worker-icon">${icon}</span>
      <span class="hm-tree-worker-label">${esc(label)}</span>
    </div>`;
  }).join('');
  return `<div class="hm-mini-tree">
    <div class="hm-tree-orchestrator">
      <span class="hm-tree-orch-icon">&#x25C6;</span>
      <span>orchestrator</span>
    </div>
    <div class="hm-tree-trunk"></div>
    <div class="hm-tree-workers">${chips}</div>
  </div>`;
}

function renderAllHiveminds() {
  const container = document.getElementById('ahm-list');
  const countEl = document.getElementById('ahm-count');
  const projectSel = document.getElementById('ahm-project');
  if (!container) return;

  // Populate project filter dropdown from cache
  if (projectSel && _allHivemindsCache.loaded) {
    const projectIds = [...new Set(_allHivemindsCache.hiveminds.map(h => h.project_id).filter(Boolean))];
    const projectMap = {};
    for (const pid of projectIds) {
      const proj = allProjects.find(p => p.id === pid);
      projectMap[pid] = proj ? (proj.name || pid) : pid;
    }
    const currentVal = _allHivemindFilter.project;
    const opts = ['<option value="all">All projects</option>',
      ...projectIds.map(pid =>
        `<option value="${esc(pid)}"${currentVal===pid?' selected':''}>${esc(projectMap[pid])}</option>`
      )];
    projectSel.innerHTML = opts.join('');
    projectSel.value = currentVal;
  }

  if (!_allHivemindsCache.loaded) {
    container.innerHTML = '<div style="padding:40px 12px;text-align:center;color:var(--text-faint);font-size:12px">Loading hiveminds...</div>';
    if (countEl) countEl.textContent = '';
    return;
  }

  const f = _allHivemindFilter;
  const q = (f.search || '').trim().toLowerCase();
  const rows = _allHivemindsCache.hiveminds.filter(hm => {
    const eff = _hmEffectiveStatus(hm);
    if (f.status !== 'all' && eff !== f.status) return false;
    if (f.project !== 'all' && hm.project_id !== f.project) return false;
    if (q) {
      const hay = ((hm.title || '') + ' ' + (hm.goal || '')).toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });

  // Sort: active first, then paused, pending, stale, completed, stopped/archived; updated_at desc within
  const order = { active: 0, paused: 1, pending: 2, stale: 3, completed: 4, stopped: 5, archived: 6 };
  rows.sort((a, b) => {
    const oa = order[_hmEffectiveStatus(a)] ?? 7;
    const ob = order[_hmEffectiveStatus(b)] ?? 7;
    if (oa !== ob) return oa - ob;
    return (b.updated_at || '').localeCompare(a.updated_at || '');
  });

  if (countEl) countEl.textContent = `${rows.length} hivemind${rows.length===1?'':'s'}`;
  if (!rows.length) {
    container.innerHTML = '<div style="padding:40px 12px;text-align:center;color:var(--text-faint);font-size:12px">No matching hiveminds. Use + New Hivemind on a project to start one.</div>';
    return;
  }

  container.innerHTML = rows.map(hm => {
    const ws = hm._workstreams || [];
    const totalFindings = ws.reduce((s, w) => s + (w.findings_count || 0), 0);
    const completedWs = ws.filter(w => w.status === 'completed').length;
    const activeWs = ws.filter(w => w.status === 'active').length;
    const proj = allProjects.find(p => p.id === hm.project_id);
    const projName = proj ? (proj.name || hm.project_id) : (hm.project_id || '—');
    const updated = hm.updated_at ? _hmRelativeTime(hm.updated_at) : '';

    const controls = hm.status === 'active'
      ? `<button class="hm-card-ctrl" onclick="event.stopPropagation();hivemindAction('${esc(hm.id)}','pause');setTimeout(loadAllHiveminds,300)" title="Pause">&#x23F8;</button>
         <button class="hm-card-ctrl" onclick="event.stopPropagation();hivemindAction('${esc(hm.id)}','stop');setTimeout(loadAllHiveminds,300)" title="Stop">&#x23F9;</button>`
      : hm.status === 'paused'
      ? `<button class="hm-card-ctrl" onclick="event.stopPropagation();hivemindAction('${esc(hm.id)}','start');setTimeout(loadAllHiveminds,300)" title="Resume">&#x25B6;</button>`
      : (hm.status === 'completed' || hm.status === 'stopped' || hm.status === 'stale')
      ? `<button class="hm-card-ctrl" onclick="event.stopPropagation();hivemindAction('${esc(hm.id)}','start');setTimeout(loadAllHiveminds,300)" title="Restart">&#x25B6;</button>`
      : '';

    const eff = _hmEffectiveStatus(hm);
    const badgeTitle = eff === 'stale' ? `Marked stale because no activity for >${HM_STALE_HOURS}h. Underlying status: ${hm.status}` : '';
    return `<div class="hm-global-card" onclick="openHivemindDashboard('${esc(hm.id)}')">
      <div class="hm-global-card-head">
        <span class="hm-status-badge ${esc(eff)}"${badgeTitle ? ` title="${esc(badgeTitle)}"` : ''}>${esc(eff)}</span>
        <span class="hm-global-card-shortid" title="Hivemind ID: ${esc(hm.id)}">#${esc(_hmShortId(hm.id))}</span>
        <div class="hm-global-card-title">${esc(hm.title || 'Untitled')}</div>
        <span class="hm-global-card-project" onclick="event.stopPropagation();_allHivemindFilter.project='${esc(hm.project_id)}';document.getElementById('ahm-project').value='${esc(hm.project_id)}';renderAllHiveminds()" title="Filter by project">${esc(projName)}</span>
        <span class="hm-global-card-meta">${esc(updated)}</span>
        <span class="hm-global-card-controls">${controls}</span>
      </div>
      ${hm.goal ? `<div class="hm-global-card-goal">${esc((hm.goal || '').substring(0, 240))}${hm.goal.length > 240 ? '…' : ''}</div>` : ''}
      ${_hmTreeMiniViz(ws)}
      <div class="hm-global-card-stats">
        <span><strong>${ws.length}</strong> workstreams</span>
        <span><strong>${completedWs}</strong> done</span>
        <span><strong>${activeWs}</strong> active</span>
        <span><strong>${totalFindings}</strong> findings</span>
      </div>
    </div>`;
  }).join('');
}

function _hmRelativeTime(iso) {
  if (!iso) return '';
  const ms = Date.now() - new Date(iso).getTime();
  if (isNaN(ms)) return '';
  const secs = Math.floor(ms / 1000);
  if (secs < 60) return 'just now';
  const mins = Math.floor(secs / 60);
  if (mins < 60) return mins + 'm ago';
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return hrs + 'h ago';
  const days = Math.floor(hrs / 24);
  if (days < 30) return days + 'd ago';
  return new Date(iso).toLocaleDateString();
}

function newHivemindFromGlobal() {
  // Pick a project, then route to that project's startHivemindChat
  const projects = allProjects.filter(p => !isIncognitoProject(p));
  if (!projects.length) {
    showToast('No projects available. Create a project first.', 4000);
    return;
  }
  // Use the currently filtered project if set, otherwise prompt
  const filterPid = _allHivemindFilter.project !== 'all' ? _allHivemindFilter.project : null;
  if (filterPid) {
    closeModalById('__all_hivemind');
    openProjectModal(filterPid);
    setTimeout(() => startHivemindChat(filterPid), 200);
    return;
  }
  // Simple list picker
  const choice = prompt('Start a hivemind in which project?\n\n' +
    projects.map((p, i) => `${i+1}. ${p.name || p.id}`).join('\n') +
    '\n\nEnter number:');
  const idx = parseInt(choice, 10) - 1;
  if (isNaN(idx) || idx < 0 || idx >= projects.length) return;
  const pid = projects[idx].id;
  closeModalById('__all_hivemind');
  openProjectModal(pid);
  setTimeout(() => startHivemindChat(pid), 200);
}



// ── Interop: re-expose for inline / cross-module + region-generated on*=
//    handler callers. All runtime-only. `renderAllHiveminds` ← the central
//    render() but GUARDED by `if (openModals.has('__all_hivemind'))` (fires
//    only once the modal is open = after this deferred module evaluates).
//    `_allHivemindFilter` is an OBJECT-IDENTITY bridge (generated handlers
//    property-write `.search`/`.status`/`.project`; never wholesale-reassigned)
//    so window prop + module binding are the same live object.
//    `_allHivemindsCache` + the _hm* helpers + HM_STALE_HOURS are module-private. ──
window.openAllHiveminds = openAllHiveminds;                   // sidebarNav('hivemind')
window.openAllHivemindsForProject = openAllHivemindsForProject; // project three-dot menu onclick
window.renderAllHiveminds = renderAllHiveminds;              // guarded central render()
window.newHivemindFromGlobal = newHivemindFromGlobal;        // region-generated card onclick
// object-identity bridge (handler property-writes route into the live object):
window._allHivemindFilter = _allHivemindFilter;
