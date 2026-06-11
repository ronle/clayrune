// ── Process Manager ──────────────────────────────────────────────────────────

async function openProcessManager() {
  const modalId = '__processes';
  if (openModals.has(modalId)) {
    const entry = openModals.get(modalId);
    if (entry.minimized) restoreModal(modalId);
    focusModal(modalId);
    refreshProcessList();
    return;
  }

  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content';
  _clampModalSize(content, 800);
  content.innerHTML = `
    <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:16px 24px 12px 28px">
      <span style="font-size:16px;font-weight:700;color:var(--text)">Process Manager</span>
      <div class="modal-window-controls" style="position:static;display:flex;gap:4px">
        <button class="modal-minimize" onclick="minimizeModal('${modalId}')" title="Minimize">&#x2015;</button>
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
    </div>
    <div style="padding:12px 24px 20px 28px">
      <div class="process-toolbar">
        <span id="process-count" class="memory-hint" style="margin:0">Loading...</span>
        <div style="display:flex;gap:6px">
          <button class="btn-header-action" style="padding:5px 12px;font-size:11px" onclick="refreshProcessList()">Refresh</button>
          <button class="btn-cleanup" onclick="cleanupOrphanedProcesses()">Cleanup Orphaned</button>
        </div>
      </div>
      <div id="process-list"></div>
    </div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);

  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);

  await refreshProcessList();
}

function _formatDuration(startedAt) {
  const ms = Date.now() - new Date(startedAt).getTime();
  const secs = Math.floor(ms / 1000);
  if (secs < 60) return secs + 's';
  const mins = Math.floor(secs / 60);
  if (mins < 60) return mins + 'm ' + (secs % 60) + 's';
  const hrs = Math.floor(mins / 60);
  return hrs + 'h ' + (mins % 60) + 'm';
}

async function refreshProcessList() {
  const container = document.getElementById('process-list');
  const countEl = document.getElementById('process-count');
  if (!container) return;
  try {
    const res = await fetch(API_BASE + '/api/processes');
    const data = await res.json();
    const processes = Array.isArray(data) ? data : (data.processes || []);
    const alive = processes.filter(p => p.alive).length;
    if (countEl) countEl.textContent = `${alive} running / ${processes.length} tracked`;
    if (!processes.length) {
      container.innerHTML = '<div class="process-empty">No tracked processes.</div>';
      return;
    }
    container.innerHTML = `<table class="process-table">
      <thead><tr>
        <th style="width:24px"></th>
        <th>PID</th>
        <th>Name</th>
        <th>Project</th>
        <th>Status</th>
        <th>Task / Command</th>
        <th>Duration</th>
        <th style="width:50px"></th>
      </tr></thead>
      <tbody>${processes.map(p => {
        const dotClass = p.alive ? 'alive' : (p.exit_code === 0 ? 'exited' : 'dead');
        const dur = _formatDuration(p.started_at);
        const statusLabel = p.agent_status
          ? p.agent_status
          : (p.alive ? 'running' : (p.exit_code === 0 ? 'exited' : 'dead'));
        return `<tr>
          <td><span class="process-status-dot ${dotClass}" title="${p.alive ? 'Running' : 'Exited (' + p.exit_code + ')'}"></span></td>
          <td style="font-family:var(--mono);font-size:11px">${p.pid}</td>
          <td>${esc(p.name)}</td>
          <td>${esc(p.project_name || p.project_id || '\u2014')}</td>
          <td><span class="process-status-pill st-${esc(statusLabel)}">${esc(statusLabel)}</span></td>
          <td title="${esc(p.command_preview || '')}" style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(p.command_preview || '\u2014')}</td>
          <td>${dur}</td>
          <td>${p.alive ? `<button class="btn-kill-process" onclick="killTrackedProcess(${p.pid})">Kill</button>` : ''}</td>
        </tr>`;
      }).join('')}</tbody>
    </table>`;
  } catch(e) {
    container.innerHTML = '<div class="process-empty">Failed to load processes.</div>';
  }
}

async function killTrackedProcess(pid) {
  if (!confirm('Kill process ' + pid + '?')) return;
  try {
    const res = await fetch(API_BASE + '/api/processes/' + pid + '/kill', { method: 'POST' });
    const data = await res.json();
    if (data.error) showToast(data.error, 4000);
    else showToast('Process ' + pid + ' killed');
  } catch(e) {
    showToast('Failed to kill process: ' + e.message, 4000);
  }
  refreshProcessList();
}

async function cleanupOrphanedProcesses() {
  try {
    const res = await fetch(API_BASE + '/api/processes/cleanup', { method: 'POST' });
    const data = await res.json();
    showToast('Cleaned up ' + (data.killed || 0) + ' orphaned process(es)');
  } catch(e) {
    showToast('Cleanup failed: ' + e.message, 4000);
  }
  refreshProcessList();
}


// ── Interop: re-expose for inline / generated-on*= callers. All runtime-only.
//    `openProcessManager` ← sidebarNav('processes') + command-palette action.
//    refreshProcessList / killTrackedProcess / cleanupOrphanedProcesses ←
//    the modal's generated on*= buttons. `_formatDuration` is module-private
//    (used only by refreshProcessList). 2-segment move: openProcessManager
//    sat under the Process Manager header; the helpers were mis-filed ~480
//    lines down under the Cross-project Hivemind header — both moved here. ──
window.openProcessManager = openProcessManager;
window.refreshProcessList = refreshProcessList;
window.killTrackedProcess = killTrackedProcess;
window.cleanupOrphanedProcesses = cleanupOrphanedProcesses;
