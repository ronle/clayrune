let acExpanded = false;
// ── Agent Console (persistent bottom panel) ─────────────────────────────────

function updateHistoryStatus(sessionId, status) {
  const entry = agentHistory.find(h => h.sessionId === sessionId);
  if (entry) entry.status = status;
}

function toggleAgentConsole() {
  const el = document.getElementById('agent-console');
  acExpanded = !acExpanded;
  el.classList.toggle('collapsed', !acExpanded);
  el.classList.toggle('expanded', acExpanded);
  document.body.classList.toggle('console-open', acExpanded);
  document.body.classList.toggle('console-visible', !acExpanded && !el.classList.contains('hidden'));
}

function toggleConsoleSession(sessionId) {
  if (acOpenSessions.has(sessionId)) acOpenSessions.delete(sessionId);
  else acOpenSessions.add(sessionId);
  renderAgentConsole();
}

function renderAgentConsole() {
  const el = document.getElementById('agent-console');
  const body = document.getElementById('ac-body');
  const countEl = document.getElementById('ac-count');

  if (agentHistory.length === 0) {
    el.classList.add('hidden');
    document.body.classList.remove('console-open', 'console-visible');
    return;
  }

  el.classList.remove('hidden');
  if (!acExpanded) document.body.classList.add('console-visible');

  const runningCount = agentHistory.filter(h => h.status === 'running' || h.status === 'idle').length;
  const visibleHistory = agentHistory.filter(h => !isHivemindWorker(h));
  countEl.textContent = runningCount > 0 ? `${runningCount} running` : visibleHistory.length;
  countEl.classList.toggle('has-running', runningCount > 0);

  body.innerHTML = visibleHistory.map(h => {
    const isOpen = acOpenSessions.has(h.sessionId);
    const rawBuf = agentOutputBuffers[h.sessionId] || [];
    // Only split lines when panel is open (expensive for large buffers)
    const allLines = isOpen ? rawBuf.flatMap(l => l.trimStart().startsWith('> ') ? [l] : l.split('\n')) : [];
    const lines = allLines.length > 200 ? allLines.slice(-200) : allLines;
    const outputHTML = (() => {
      if (!isOpen) return '';
      let result = '';
      let tableLines = [];
      function flushTable() {
        if (tableLines.length === 0) return;
        if (isPipeTable(tableLines)) {
          result += `<div class="hl-table">${buildPipeTable(tableLines)}</div>`;
        } else {
          result += `<div class="hl-table-pre">${tableLines.map(l => formatTableLine(esc(l))).join('\n')}</div>`;
        }
        tableLines = [];
      }
      for (const l of lines) {
        if (isTableLine(l)) {
          tableLines.push(l);
        } else if (tableLines.length > 0 && l.trim() === '') {
          tableLines.push(l);
        } else {
          flushTable();
          const cls = agentLineCls(l);
          // Same prompt-vs-special split as agentPanelHTML — prompts get image
          // thumbnails inlined; tool/error/etc. stay on plain esc.
          const html = cls.includes('agent-line-prompt')
            ? escPromptWithImages(l)
            : (cls.includes('agent-line-tool') || cls.includes('agent-line-error') || cls.includes('agent-line-followup') || cls.includes('agent-line-queued')
                ? esc(l) : formatAgentText(l));
          result += `<div class="${cls}">${html}</div>`;
        }
      }
      flushTable();
      return result;
    })();

    const stopBtn = (h.status === 'running' || h.status === 'idle')
      ? `<button class="ac-stop-btn" onclick="event.stopPropagation();stopAgent('${esc(h.projectId)}','${esc(h.sessionId)}')" title="Stop agent">Stop</button>`
      : '';

    // Show last tool activity in console header for running sessions
    let lastTool = '';
    if (h.status === 'running') {
      for (let i = rawBuf.length - 1; i >= 0; i--) {
        if (rawBuf[i].startsWith('[tool:')) { lastTool = rawBuf[i]; break; }
      }
    }

    return `<div class="ac-session">
      <div class="ac-session-header" onclick="toggleConsoleSession('${esc(h.sessionId)}')">
        <span class="agent-status-dot ${h.status}"></span>
        <span class="ac-session-project">${esc(h.projectName)}</span>
        <span class="ac-session-task" title="${esc(h.task)}">${esc(h.task)}</span>
        ${lastTool ? `<span class="agent-activity" id="ac-activity-${esc(h.sessionId)}">${esc(lastTool)}</span>` : ''}
        ${stopBtn}
        <span class="ac-session-time">${h.startedAt ? timeAgoShort(h.startedAt) : ''}</span>
        <span class="ac-session-chevron ${isOpen ? 'open' : ''}">&#9654;</span>
      </div>
      <div class="ac-session-output ${isOpen ? 'open' : ''}" id="ac-output-${esc(h.sessionId)}">${outputHTML}</div>
    </div>`;
  }).join('');
}

function updateConsoleOutput(sessionId) {
  const el = document.getElementById(`ac-output-${sessionId}`);
  if (!el) return;
  const freshMount = !el.dataset.scrollInitialized;
  const wasPinned = freshMount || _isAgentOutputPinned(el);
  const lines = agentOutputBuffers[sessionId] || [];
  const lastEntry = lines[lines.length - 1];
  if (!lastEntry) return;

  // Split multi-line text blocks into individual lines
  const subLines = lastEntry.split('\n');
  for (const lastLine of subLines) {
    // Group consecutive table lines into a single styled block
    if (isTableLine(lastLine)) {
      let block = el.lastElementChild;
      if (block && (block.classList.contains('hl-table') || block.classList.contains('hl-table-pre'))) {
        block._rawLines.push(lastLine);
      } else {
        block = document.createElement('div');
        block._rawLines = [lastLine];
        el.appendChild(block);
      }
      if (isPipeTable(block._rawLines)) {
        block.className = 'hl-table';
        block.innerHTML = buildPipeTable(block._rawLines);
      } else {
        block.className = 'hl-table-pre';
        block.innerHTML = block._rawLines.map(l => formatTableLine(esc(l))).join('\n');
      }
      continue;
    }
    if (lastLine.trim() === '') {
      const last = el.lastElementChild;
      if (last && (last.classList.contains('hl-table') || last.classList.contains('hl-table-pre'))) {
        last._rawLines.push(lastLine);
        continue;
      }
    }

    const div = document.createElement('div');
    const cls = agentLineCls(lastLine);
    div.className = cls;
    if (cls.includes('agent-line-tool') || cls.includes('agent-line-error') || cls.includes('agent-line-followup') || cls.includes('agent-line-queued') || cls.includes('agent-line-prompt')) {
      div.textContent = lastLine;
    } else {
      div.innerHTML = formatAgentText(lastLine);
    }
    el.appendChild(div);
  }
  if (wasPinned) {
    el.scrollTop = el.scrollHeight;
    if (freshMount && el.scrollHeight > 0) el.dataset.scrollInitialized = '1';
  }
}

// ── Memory tab ──────────────────────────────────────────────────────────────

const memoryCache = {};  // projectId -> { content, loaded }

function memoryTabHTML(p) {
  const cached = memoryCache[p.id];
  if (!cached) {
    return `<div class="memory-section"><div class="memory-hint">Loading...</div></div>`;
  }
  const pathHint = cached.path ? `<br><span style="font-family:'JetBrains Mono',monospace;font-size:10px;opacity:0.6">${esc(cached.path)}</span>` : '';
  return `<div class="memory-section">
    <div class="memory-hint">Claude's native MEMORY.md &mdash; read/written by agents directly, injected into every session as context.${pathHint}</div>
    <textarea class="memory-textarea" id="memory-editor-${esc(p.id)}" rows="16"
      placeholder="# Project Memory\n\n## Architecture Decisions\n- ...\n\n## Gotchas\n- ..."
    >${esc(cached.content || '')}</textarea>
    <div style="display:flex;align-items:center;gap:8px;margin-top:10px;flex-shrink:0">
      <button class="btn-save-rules" onclick="saveMemory('${esc(p.id)}')">Save</button>
      <span class="rules-saved" id="memory-saved-${esc(p.id)}">Saved</span>
    </div>
  </div>`;
}

async function loadMemory(projectId) {
  try {
    const res = await fetch(API_BASE + '/api/project/' + projectId + '/memory');
    const data = await res.json();
    memoryCache[projectId] = { content: data.content || '', path: data.path || '', loaded: true };
  } catch(e) {
    memoryCache[projectId] = { content: '', path: '', loaded: true };
  }
  refreshModal();
}

async function saveMemory(projectId) {
  const el = document.getElementById('memory-editor-' + projectId);
  if (!el) return;
  try {
    await fetch(API_BASE + '/api/project/' + projectId + '/memory', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ content: el.value })
    });
    memoryCache[projectId] = { content: el.value, loaded: true };
    flashSaved('memory-saved-' + projectId);
  } catch(e) { alert('Failed to save memory'); }
}

async function openMemoryModal(projectId) {
  const modalId = '__memory_' + projectId;
  if (openModals.has(modalId)) {
    const entry = openModals.get(modalId);
    if (entry.minimized) restoreModal(modalId);
    focusModal(modalId);
    return;
  }

  const p = allProjects.find(x => x.id === projectId);
  if (!p) return;

  if (!memoryCache[projectId]) {
    try {
      const res = await fetch(API_BASE + '/api/project/' + projectId + '/memory');
      const data = await res.json();
      memoryCache[projectId] = { content: data.content || '', path: data.path || '', loaded: true };
    } catch(e) {
      memoryCache[projectId] = { content: '', path: '', loaded: true };
    }
  }

  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content';
  content.style.height = '60vh';
  content.innerHTML = `
    <div class="modal-header" style="padding:16px 24px 12px 28px">
      <div class="modal-window-controls">
        <button class="modal-minimize" onclick="minimizeModal('${esc(modalId)}')" title="Minimize">&#x2015;</button>
        <button class="modal-close" onclick="closeModalById('${esc(modalId)}')" title="Close">&#10005;</button>
      </div>
      <h2 style="margin:0;font-size:18px;font-weight:700;color:var(--text)">Memory &mdash; ${esc(p.name || p.id)}</h2>
    </div>
    <div class="memory-editor">
      ${memoryTabHTML(p)}
    </div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);

  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);
}

// ── Tab switch ──────────────────────────────────────────────────────────────

// Mobile: invoked from the modal three-dot menu to switch tabs and close the menu
function _mcMenuClose() {
  document.querySelectorAll('.modal-menu-dropdown.open').forEach(d => d.classList.remove('open'));
  document.querySelectorAll('.modal-menu-sub.open').forEach(d => d.classList.remove('open'));
}
function _mcMenuSwitchTab(projectId, tab) {
  _mcMenuClose();
  switchModalTab(projectId, tab);
}

function switchModalTab(projectId, tab) {
  modalActiveTab[projectId] = tab;
  if (tab === 'agent-log') {
    loadAgentLog(projectId);
    return;
  }
  if (tab === 'plans') {
    loadProjectPlans(projectId);
    return;
  }
  if (tab === 'workflows') {
    refreshModal();          // make the panel (+ Loading placeholder) visible
    loadWorkflows(projectId); // then populate it from the journal scan
    return;
  }
  refreshModal();
}

// ── Workflow progress (ASCII tree reconstructed from on-disk CC journals) ─────
// CC's Workflow tool never streams per-agent progress to MC's agent channel
// (only the launch tool_use + final task-notification arrive), so the live tree
// is read from ~/.claude/projects/<enc>/<csid>/subagents/workflows/<id>/journal.jsonl
// server-side and rendered here as a monospace <pre>. Best-effort, read-only.
const _wfPollTimers = {};
function _wfStopPolling(projectId) {
  if (_wfPollTimers[projectId]) {
    clearInterval(_wfPollTimers[projectId]);
    delete _wfPollTimers[projectId];
  }
}
function _wfStartPolling(projectId) {
  if (_wfPollTimers[projectId]) return;  // already polling this project
  _wfPollTimers[projectId] = setInterval(() => {
    // Self-cancel once the tab is no longer active or the panel is gone.
    if ((modalActiveTab[projectId] || 'agent') !== 'workflows'
        || !document.getElementById('workflows-body-' + projectId)) {
      _wfStopPolling(projectId);
      return;
    }
    loadWorkflows(projectId);
  }, 3000);
}
async function loadWorkflows(projectId) {
  const el = document.getElementById('workflows-body-' + projectId);
  if (!el) return;
  try {
    const res = await fetch(API_BASE + `/api/project/${encodeURIComponent(projectId)}/workflows`);
    if (!res.ok) {
      el.innerHTML = '<div style="color:var(--text-faint);font-style:italic">Could not load workflows.</div>';
      _wfStopPolling(projectId);
      return;
    }
    const data = await res.json();
    const wfs = data.workflows || [];
    if (!wfs.length) {
      el.innerHTML = '<div style="color:var(--text-faint);font-style:italic">No workflows in the last 24h. Launch one with "use a workflow to …" in the Agent tab.</div>';
      _wfStopPolling(projectId);
      return;
    }
    el.innerHTML = wfs.map(w => {
      const badge = w.running
        ? '<span style="color:var(--amber,#d98a00);font-weight:600">● running</span>'
        : '<span style="color:var(--green,#2e7d32);font-weight:600">✓ complete</span>';
      return `<div class="card-section" style="margin-bottom:10px">
        <div class="section-title" style="display:flex;justify-content:space-between;align-items:center;gap:8px">
          <span style="font-family:var(--mono,monospace);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(w.wf_id)}</span>${badge}
        </div>
        <pre style="white-space:pre;overflow-x:auto;font-size:12px;line-height:1.45;font-family:var(--mono,monospace);margin:6px 0 0">${esc(w.ascii || '')}</pre>
      </div>`;
    }).join('');
    if (wfs.some(w => w.running)) _wfStartPolling(projectId);
    else _wfStopPolling(projectId);
  } catch (e) {
    el.innerHTML = '<div style="color:var(--text-faint);font-style:italic">Failed to load workflows.</div>';
    _wfStopPolling(projectId);
  }
}
window.loadWorkflows = loadWorkflows;

// ── Tab search / filter ─────────────────────────────────────────────────────

function findModalIdForProject(projectId) {
  for (const [modalId, entry] of openModals) {
    if (entry.projectId === projectId && !modalId.startsWith('__')) return modalId;
  }
  return null;
}

function applyTabFilter(projectId) {
  const query = (modalSearchQuery[projectId] || '').toLowerCase().trim();
  const modalId = findModalIdForProject(projectId);
  if (!modalId) return;
  const el = openModals.get(modalId).element;
  const activeTab = modalActiveTab[projectId] || 'agent';

  if (activeTab === 'backlog') {
    el.querySelectorAll('.backlog-item').forEach(item => {
      const text = (item.querySelector('.backlog-text')?.textContent || '').toLowerCase();
      item.style.display = (!query || text.includes(query)) ? '' : 'none';
    });
  } else if (activeTab === 'agent-log') {
    el.querySelectorAll('.agent-log-entry').forEach(item => {
      const task = (item.querySelector('.agent-log-task')?.textContent || '').toLowerCase();
      const summary = (item.querySelector('.agent-log-summary')?.textContent || '').toLowerCase();
      item.style.display = (!query || task.includes(query) || summary.includes(query)) ? '' : 'none';
    });
  } else if (activeTab === 'activity') {
    el.querySelectorAll('.log-entry').forEach(item => {
      const text = item.textContent.toLowerCase();
      item.style.display = (!query || text.includes(query)) ? '' : 'none';
    });
  }

  // Toggle clear button without full re-render
  const searchDiv = el.querySelector('.modal-tab-search');
  if (searchDiv) {
    let clearBtn = searchDiv.querySelector('.search-clear');
    if (query && !clearBtn) {
      clearBtn = document.createElement('span');
      clearBtn.className = 'search-clear';
      clearBtn.innerHTML = '&#x2715;';
      clearBtn.onclick = () => clearTabSearch(projectId);
      searchDiv.appendChild(clearBtn);
    } else if (!query && clearBtn) {
      clearBtn.remove();
    }
  }
}

function clearTabSearch(projectId) {
  modalSearchQuery[projectId] = '';
  const modalId = findModalIdForProject(projectId);
  if (!modalId) return;
  const el = openModals.get(modalId).element;
  const input = el.querySelector('.modal-tab-search input');
  if (input) { input.value = ''; input.focus(); }
  applyTabFilter(projectId);
}


// ── interop: window re-exposure for inline/generated/cross-module callers ──
window.updateHistoryStatus = updateHistoryStatus;
window.toggleAgentConsole = toggleAgentConsole;
window.renderAgentConsole = renderAgentConsole;
window.updateConsoleOutput = updateConsoleOutput;
window.openMemoryModal = openMemoryModal;
window._mcMenuClose = _mcMenuClose;
window._mcMenuSwitchTab = _mcMenuSwitchTab;
window.switchModalTab = switchModalTab;
window.applyTabFilter = applyTabFilter;
window.clearTabSearch = clearTabSearch;
window.saveMemory = saveMemory;
window.toggleConsoleSession = toggleConsoleSession;
