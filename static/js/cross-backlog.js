// ── Cross-project Backlog View ──────────────────────────────────────────────
let _allBacklogFilter = { status: 'open', search: '', priority: 'all' };

async function openAllBacklog() {
  const modalId = '__all_backlog';
  if (openModals.has(modalId)) {
    const entry = openModals.get(modalId);
    if (entry.minimized) restoreModal(modalId);
    focusModal(modalId);
    renderAllBacklog();
    return;
  }

  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content';
  _clampModalSize(content, 860);
  content.innerHTML = `
    <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:16px 24px 12px 28px">
      <span style="font-size:16px;font-weight:700;color:var(--text)">All Backlog Items</span>
      <div class="modal-window-controls" style="position:static;display:flex;gap:4px">
        <button class="modal-minimize" onclick="minimizeModal('${modalId}')" title="Minimize">&#x2015;</button>
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
    </div>
    <div style="padding:4px 24px 20px 28px">
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:12px;flex-wrap:wrap">
        <input type="text" id="abl-search" placeholder="Search text..." value="${esc(_allBacklogFilter.search)}"
          style="flex:1;min-width:180px;padding:6px 10px;font-size:12px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text)"
          oninput="_allBacklogFilter.search=this.value;renderAllBacklog()">
        <select id="abl-status" style="padding:6px 8px;font-size:12px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text)"
          onchange="_allBacklogFilter.status=this.value;renderAllBacklog()">
          <option value="open"${_allBacklogFilter.status==='open'?' selected':''}>Open</option>
          <option value="done"${_allBacklogFilter.status==='done'?' selected':''}>Done</option>
          <option value="all"${_allBacklogFilter.status==='all'?' selected':''}>All</option>
        </select>
        <select id="abl-priority" style="padding:6px 8px;font-size:12px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text)"
          onchange="_allBacklogFilter.priority=this.value;renderAllBacklog()">
          <option value="all"${_allBacklogFilter.priority==='all'?' selected':''}>All priorities</option>
          <option value="high"${_allBacklogFilter.priority==='high'?' selected':''}>High</option>
          <option value="normal"${_allBacklogFilter.priority==='normal'?' selected':''}>Normal</option>
          <option value="low"${_allBacklogFilter.priority==='low'?' selected':''}>Low</option>
        </select>
        <span id="abl-count" style="font-size:11px;color:var(--text-faint)"></span>
      </div>
      <div id="abl-list" style="max-height:65vh;overflow-y:auto"></div>
    </div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);

  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);

  renderAllBacklog();
}

function renderAllBacklog() {
  const container = document.getElementById('abl-list');
  const countEl = document.getElementById('abl-count');
  if (!container) return;
  const f = _allBacklogFilter;
  const q = (f.search || '').trim().toLowerCase();
  const rows = [];
  for (const p of allProjects) {
    const items = p.backlog || [];
    for (const item of items) {
      if (f.status === 'open' && item.status === 'done') continue;
      if (f.status === 'done' && item.status !== 'done') continue;
      if (f.priority !== 'all' && (item.priority || 'normal') !== f.priority) continue;
      if (q && !(item.text || '').toLowerCase().includes(q)) continue;
      rows.push({ p, item });
    }
  }
  const prioRank = { high: 0, normal: 1, low: 2 };
  rows.sort((a, b) => {
    const ad = a.item.status === 'done' ? 1 : 0;
    const bd = b.item.status === 'done' ? 1 : 0;
    if (ad !== bd) return ad - bd;
    const ap = prioRank[a.item.priority || 'normal'] ?? 1;
    const bp = prioRank[b.item.priority || 'normal'] ?? 1;
    if (ap !== bp) return ap - bp;
    return (b.item.updated_at || '').localeCompare(a.item.updated_at || '');
  });
  if (countEl) countEl.textContent = `${rows.length} item${rows.length===1?'':'s'}`;
  if (!rows.length) {
    container.innerHTML = '<div style="padding:40px 12px;text-align:center;color:var(--text-faint);font-size:12px">No matching backlog items.</div>';
    return;
  }
  container.innerHTML = rows.map(({ p, item }) => {
    const isAgent = (item.source || '').startsWith('agent:');
    const isInProgress = isAgent && item.agent_status === 'in_progress';
    const cls = [
      item.status === 'done' ? 'done' : '',
      `priority-${item.priority || 'normal'}`,
      isAgent ? 'agent-source' : '',
      isInProgress ? 'agent-in-progress' : '',
    ].filter(Boolean).join(' ');
    const notesCount = (item.notes || []).length;
    return `
      <div class="backlog-item ${cls}" style="cursor:pointer" onclick="_jumpToBacklogItem('${esc(p.id)}','${esc(item.id)}')">
        <button class="backlog-check" onclick="event.stopPropagation();toggleDone(event,'${esc(p.id)}','${esc(item.id)}','${item.status}')" title="${item.status==='done'?'Reopen':'Mark done'}">
          ${item.status==='done' ? '&#x2713;' : ''}
        </button>
        <div style="flex:1;min-width:0">
          <div class="backlog-text" style="pointer-events:none">${esc(item.text || '')}</div>
          <div style="font-size:11px;color:var(--accent);font-weight:600;margin-top:3px;letter-spacing:0.2px">${esc(p.name || p.id)}</div>
        </div>
        <div class="backlog-meta">
          ${isAgent ? `<span class="agent-badge${isInProgress?' in-progress':''}">${isInProgress?'doing':'agent'}</span>` : ''}
          <span class="priority-badge priority-${item.priority||'normal'}">${item.priority||'normal'}</span>
          ${notesCount ? `<span class="backlog-source" title="${notesCount} note${notesCount===1?'':'s'}">&#x1F4DD; ${notesCount}</span>` : ''}
        </div>
      </div>`;
  }).join('');
}

function _jumpToBacklogItem(projectId, itemId) {
  openProjectModal(projectId);
  setTimeout(() => {
    const el = document.querySelector(`[data-modal-id="${projectId}"] .backlog-item[data-item-id="${itemId}"]`);
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'center' });
      el.style.transition = 'background 0.4s';
      const orig = el.style.background;
      el.style.background = 'var(--accent-dim)';
      setTimeout(() => { el.style.background = orig; }, 900);
    }
  }, 150);
}


// ── Interop: re-expose for inline/cross-module callers. ──
//   • _allBacklogFilter — OBJECT-IDENTITY bridge: the generated oninput/onchange
//     handlers do `_allBacklogFilter.search=this.value; renderAllBacklog()` etc.
//     (bare-identifier *property* writes that resolve against window at event
//     time). The object is never wholesale-reassigned (formal scan: the only
//     write is its declaration), so exposing the same object on window routes
//     every handler property-write into the module's live object — one source
//     of truth, same pattern as mobile-pairing's _mobilePairState.
//   • openAllBacklog — sidebarNav('backlog') (runtime).
//   • renderAllBacklog — the central render() calls it, but ONLY guarded by
//     `openModals.has('__all_backlog')` (false at boot; runs only once the
//     modal is open ⇒ after this module has evaluated). Also called by
//     openAllBacklog. Runtime-only.
//   • _jumpToBacklogItem — region-generated row onclick (resolves against
//     window at click time).
window._allBacklogFilter = _allBacklogFilter;
window.openAllBacklog = openAllBacklog;
window.renderAllBacklog = renderAllBacklog;
window._jumpToBacklogItem = _jumpToBacklogItem;
