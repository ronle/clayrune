// ── Agent Log Panel ──────────────────────────────────────────────────────────

let continueInputOpen = {};  // entryId → true (tracks which continue input is expanded)

function agentLogPanelHTML(p) {
  if (!p.project_path) return '';
  const isOpen = agentLogOpen[p.id] || false;
  const entries = (agentLogCache[p.id] || []).filter(e => !e.hivemind_ws_id);

  const entriesHTML = entries.length === 0
    ? '<div class="agent-log-empty">No completed sessions yet</div>'
    : entries.map(e => {
      const csid = e.claude_session_id || '';
      const eid = e.session_id || csid;
      const eprov = e.provider || p.provider || 'claude';
      const ecaps = _getProviderCaps(eprov);
      // Usage stats: gate by provider capability
      const usageStr = (() => {
        if (!ecaps.emits_usage) return '';
        const u = e.usage || {};
        const t = (u.input_tokens||0)+(u.output_tokens||0);
        const cr = u.cache_read_input_tokens||0;
        let s = t ? ` &middot; ${formatTokens(t)} tokens` : '';
        if (cr) s += ` (${formatTokens(cr)} cached)`;
        if (ecaps.emits_num_turns && e.num_turns) s += ` &middot; ${e.num_turns} turn${e.num_turns!==1?'s':''}`;
        return s;
      })();
      const costStr = ecaps.emits_cost && e.cost_usd ? ` &middot; <span class="alu-cost">${formatCost(e.cost_usd)}</span>` : '';
      // Session ID display: provider-neutral label
      const sessionLabel = eprov === 'claude' ? 'claude -r' : `${eprov} session`;
      // Continue button: only show for providers that support session resume
      const continueBtn = (csid && ecaps.supports_session_resume)
        ? `<button class="agent-log-continue-btn" onclick="toggleContinueInput('${esc(p.id)}','${esc(eid)}','${esc(csid)}')">Continue</button>`
        : '';
      return `
      <div class="agent-log-entry status-${e.status || 'completed'}">
        <div class="agent-log-task">
          <span class="agent-status-dot ${e.status || 'completed'}"></span>
          ${_providerBadge(eprov)}
          ${esc((e.task || '').substring(0, 100))}
          ${continueBtn}
        </div>
        <div class="agent-log-summary">${esc(e.summary || '')}</div>
        <div class="agent-log-ts">${esc(e.ts_relative || e.ts || '')} &middot; started ${esc(e.started_relative || e.started_at || '')}${usageStr}${costStr}</div>
        ${csid ? `<div class="agent-log-session-id">${sessionLabel} <code title="Click to copy">${esc(csid)}</code><span class="copy-sid" onclick="navigator.clipboard.writeText('${esc(csid)}');this.textContent='copied!';setTimeout(()=>this.textContent='copy',1200)" title="Copy session ID">copy</span></div>` : ''}
        ${(csid && ecaps.supports_session_resume) ? `<div class="agent-log-continue-input${continueInputOpen[eid] ? ' open' : ''}" id="continue-input-${esc(eid)}">
          <textarea id="continue-msg-${esc(eid)}" rows="2" placeholder="What should the agent continue with?" onkeydown="handleInputEnter(event,()=>dispatchContinue('${esc(p.id)}','${esc(eid)}','${esc(csid)}'),'${esc(p.id)}')"></textarea>
          <button class="btn-send" onclick="dispatchContinue('${esc(p.id)}','${esc(eid)}','${esc(csid)}')">Send</button>
        </div>` : ''}
      </div>`;
    }).join('');

  return `<div class="card-section">
    <div class="section-title">Completed Sessions</div>
    ${entriesHTML}
  </div>`;
}

async function toggleAgentLog(projectId) {
  const body = document.getElementById(`agent-log-body-${projectId}`);
  const chevron = document.getElementById(`agent-log-chevron-${projectId}`);
  if (!body) return;

  const isOpen = body.classList.contains('open');
  agentLogOpen[projectId] = !isOpen;
  body.classList.toggle('open');
  chevron?.classList.toggle('open');

  if (!isOpen) {
    await loadAgentLog(projectId);  // always re-fetch on open for fresh data
  }
}

async function loadAgentLog(projectId) {
  try {
    const res = await fetch(API_BASE + `/api/project/${projectId}/agent/log`);
    agentLogCache[projectId] = await res.json();
    // Populate planFile in status cache from agent log entries
    for (const entry of agentLogCache[projectId]) {
      if (entry.plan_file && entry.session_id) {
        const cached = agentStatusCache[entry.session_id];
        if (cached && !cached.planFile) {
          cached.planFile = entry.plan_file;
        }
      }
    }
    // Auto-set default resume if not explicitly chosen yet — DESKTOP ONLY.
    // On mobile this fired after the async log load and jumped the user from the
    // conversations list / 5a picker straight into a resume preview they never
    // selected. Mobile shows the list and waits for an explicit tap.
    if (!(projectId in pendingResumeId) && window.innerWidth > 960) {
      pendingResumeId[projectId] = getDefaultResumeId(projectId);
    }
    refreshModal();
  } catch(e) {}
}

async function loadConversations(projectId) {
  try {
    const res = await fetch(API_BASE + `/api/project/${projectId}/conversations?limit=20`);
    conversationsCache[projectId] = await res.json();
    refreshModal();
  } catch(e) {}
}

// Optimistically upsert a conversation entry so the picker reflects the user's
// latest message without waiting for a server round-trip. Called from close /
// followup; a background loadConversations() reconciles with authoritative data.
function upsertConversationCache(projectId, claudeSessionId, lastUser, status) {
  if (!projectId || !claudeSessionId) return;
  const list = conversationsCache[projectId] || (conversationsCache[projectId] = []);
  const nowMs = Date.now();
  const label = (lastUser || '').trim();
  const idx = list.findIndex(c => c.claude_session_id === claudeSessionId);
  if (idx >= 0) {
    const e = list[idx];
    if (label) {
      e.last_user = label;
      e.label = label;
      if (!e.first_user) e.first_user = label;
    }
    if (status) e.status = status;
    e.turns = (e.turns || 0) + (label ? 1 : 0);
    e.ts_relative = 'just now';
    e.mtime = nowMs / 1000;
    list.splice(idx, 1);
    list.unshift(e);
  } else {
    list.unshift({
      claude_session_id: claudeSessionId,
      mc_session_id: '',
      status: status || 'stopped',
      label: label || '(empty conversation)',
      last_user: label,
      first_user: label,
      turns: label ? 1 : 0,
      size: 0,
      mtime: nowMs / 1000,
      ts: '',
      ts_relative: 'just now',
      live: false,
    });
  }
}

// Scan the output buffer backward for the most recent user prompt line (local-echo
// format: "> <text>"). Used to reconstruct last_user when closing a tab.
function _lastUserFromBuffer(sessionId) {
  const buf = agentOutputBuffers[sessionId] || [];
  for (let i = buf.length - 1; i >= 0; i--) {
    const line = buf[i] || '';
    const trimmed = line.trimStart();
    if (trimmed.startsWith('> ')) return trimmed.slice(2).trim();
  }
  return '';
}

// ── Plans tab ─────────────────────────────────────────────────────────────────

let planSelections = {};  // projectId → Set of plan_file paths

async function loadProjectPlans(projectId) {
  try {
    const res = await fetch(API_BASE + `/api/project/${projectId}/plans`);
    plansCache[projectId] = await res.json();
  } catch(e) {
    plansCache[projectId] = [];
  }
  refreshModal();
  renderPlansTab(projectId);
}

function renderPlansTab(projectId) {
  const container = document.getElementById(`plans-list-${projectId}`);
  const toolbar = document.getElementById(`plans-toolbar-${projectId}`);
  if (!container) return;
  const plans = plansCache[projectId] || [];
  if (!planSelections[projectId]) planSelections[projectId] = new Set();
  const sel = planSelections[projectId];

  if (!plans.length) {
    if (toolbar) toolbar.style.display = 'none';
    container.innerHTML = '<div style="color:var(--text-faint);font-style:italic">No plans yet. Plans are created when an agent uses EnterPlanMode / ExitPlanMode.</div>';
    return;
  }

  // Toolbar
  if (toolbar) {
    const selCount = sel.size;
    const allChecked = selCount === plans.length && plans.length > 0;
    toolbar.style.display = 'flex';
    toolbar.className = 'plans-toolbar';
    toolbar.innerHTML = `
      <label><input type="checkbox" ${allChecked ? 'checked' : ''} onchange="toggleAllPlans('${esc(projectId)}', this.checked)"> Select All</label>
      ${selCount > 0 ? `<button class="btn-plans-action btn-plans-delete" onclick="deleteSelectedPlans('${esc(projectId)}')">Delete Selected (${selCount})</button>` : ''}
      ${selCount > 0 ? `<button class="btn-plans-action" onclick="exportSelectedPlans('${esc(projectId)}')">Export (${selCount})</button>` : ''}
    `;
  }

  container.innerHTML = plans.map(p => {
    const pathEsc = esc(p.plan_file.replace(/\\/g,'\\\\'));
    const checked = sel.has(p.plan_file) ? 'checked' : '';
    return `
    <div class="plan-history-card">
      <input type="checkbox" class="plan-cb" ${checked} onchange="togglePlanSelection('${esc(projectId)}','${pathEsc}',this.checked)" onclick="event.stopPropagation()">
      <div class="plan-card-body" onclick="openPlanFromHistory('${pathEsc}','${esc(p.title)}')">
        <div class="plan-history-title">${esc(p.title)}</div>
        <div class="plan-history-meta">
          <span>${esc(p.ts_relative || '')}</span>
          <span class="plan-history-task">${esc((p.task || '').length > 80 ? p.task.slice(0,77) + '...' : (p.task || ''))}</span>
        </div>
        <div class="plan-history-filename">${esc(p.filename || '')}</div>
      </div>
      <button class="plan-card-delete" onclick="event.stopPropagation();deleteSinglePlan('${esc(projectId)}','${pathEsc}','${esc(p.title)}')" title="Delete plan">&times;</button>
    </div>`;
  }).join('');
}

function togglePlanSelection(projectId, planPath, checked) {
  if (!planSelections[projectId]) planSelections[projectId] = new Set();
  const decoded = planPath.replace(/\\\\/g, '\\');
  if (checked) planSelections[projectId].add(decoded);
  else planSelections[projectId].delete(decoded);
  renderPlansTab(projectId);
}

function toggleAllPlans(projectId, checked) {
  const plans = plansCache[projectId] || [];
  if (!planSelections[projectId]) planSelections[projectId] = new Set();
  if (checked) plans.forEach(p => planSelections[projectId].add(p.plan_file));
  else planSelections[projectId].clear();
  renderPlansTab(projectId);
}

async function deleteSinglePlan(projectId, planPath, title) {
  const decoded = planPath.replace(/\\\\/g, '\\');
  if (!confirm(`Delete plan "${title}"? This cannot be undone.`)) return;
  try {
    await fetch(API_BASE + '/api/plans/delete', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({paths: [decoded]})
    });
  } catch(e) {}
  if (planSelections[projectId]) planSelections[projectId].delete(decoded);
  loadProjectPlans(projectId);
}

async function deleteSelectedPlans(projectId) {
  const sel = planSelections[projectId];
  if (!sel || !sel.size) return;
  if (!confirm(`Delete ${sel.size} plan(s)? This cannot be undone.`)) return;
  try {
    await fetch(API_BASE + '/api/plans/delete', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({paths: Array.from(sel)})
    });
  } catch(e) {}
  planSelections[projectId] = new Set();
  loadProjectPlans(projectId);
}

async function exportSelectedPlans(projectId) {
  const sel = planSelections[projectId];
  if (!sel || !sel.size) return;
  for (const planPath of sel) {
    try {
      const res = await fetch(API_BASE + `/api/plan-file?path=${encodeURIComponent(planPath)}`);
      if (!res.ok) continue;
      const data = await res.json();
      const blob = new Blob([data.content || ''], {type: 'text/markdown'});
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url; a.download = data.filename || 'plan.md';
      document.body.appendChild(a); a.click(); document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch(e) {}
  }
}

async function openPlanFromHistory(planPath, title) {
  const modalId = '__planhistory_' + planPath.replace(/[^a-zA-Z0-9]/g, '_').slice(-30);
  if (openModals.has(modalId)) { focusModal(modalId); return; }

  let content;
  try {
    const res = await fetch(API_BASE + `/api/plan-file?path=${encodeURIComponent(planPath)}`);
    if (!res.ok) throw new Error('Failed to load');
    const data = await res.json();
    content = data.content || '';
  } catch(e) {
    content = 'Failed to load plan file.';
  }

  // Render with rich formatting (same as openPlanFileViewer)
  const lines = content.split('\n');
  let bodyHTML = '';
  let tableLines = [];
  function flushTable() {
    if (tableLines.length === 0) return;
    if (isPipeTable(tableLines)) {
      bodyHTML += `<div class="hl-table">${buildPipeTable(tableLines)}</div>`;
    } else {
      bodyHTML += `<div class="hl-table-pre">${tableLines.map(l => formatTableLine(esc(l))).join('\n')}</div>`;
    }
    tableLines = [];
  }
  for (const line of lines) {
    if (isTableLine(line)) {
      tableLines.push(line);
    } else if (tableLines.length > 0 && line.trim() === '') {
      tableLines.push(line);
    } else {
      flushTable();
      bodyHTML += `<div class="agent-line">${formatAgentText(line)}</div>`;
    }
  }
  flushTable();

  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const contentEl = document.createElement('div');
  contentEl.className = 'modal-content plan-viewer-content';
  contentEl.innerHTML = `
    <div class="modal-window-controls">
      <button class="modal-minimize" onclick="minimizeModal('${esc(modalId)}')" title="Minimize">&#x2015;</button>
      <button class="modal-close" onclick="closeModalById('${esc(modalId)}')" title="Close">&#10005;</button>
    </div>
    <div class="plan-viewer-header modal-header">
      <h3 style="color:#c4b5fd;margin:0;font-size:15px">&#128196; ${esc(title)}</h3>
      <div style="color:var(--text-faint);font-size:12px;margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(planPath.split(/[/\\]/).pop())}</div>
    </div>
    <div class="plan-viewer-body">${bodyHTML}</div>`;
  win.appendChild(contentEl);
  document.getElementById('modal-layer').appendChild(win);
  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);
}

// ── Agent Log continue ───────────────────────────────────────────────────────

function toggleContinueInput(projectId, entryId, claudeSessionId) {
  const el = document.getElementById(`continue-input-${entryId}`);
  if (!el) return;
  const isOpen = el.classList.contains('open');
  document.querySelectorAll('.agent-log-continue-input.open').forEach(x => x.classList.remove('open'));
  continueInputOpen = {};
  if (!isOpen) {
    el.classList.add('open');
    continueInputOpen[entryId] = true;
    setTimeout(() => document.getElementById(`continue-msg-${entryId}`)?.focus(), 50);
  }
}

async function dispatchContinue(projectId, entryId, claudeSessionId) {
  const input = document.getElementById(`continue-msg-${entryId}`);
  const task = input.value.trim();
  if (!task) { input.focus(); return; }
  input.value = '';
  if (input.id) delete textareaValues[input.id];
  continueInputOpen = {};

  try {
    const res = await fetch(API_BASE + `/api/project/${projectId}/agent/dispatch`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(_maybeTagMobileClient({ task, resume_conversation_id: claudeSessionId }))
    });
    const data = await res.json();
    if (!data.ok) { alert(data.error || 'Continue failed'); return; }
    const sessionId = data.session_id;
    const pName = (allProjects.find(x => x.id === projectId) || {}).name || projectId;

    agentOutputBuffers[sessionId] = [`> [continuing conversation] ${task}`];
    agentStatusCache[sessionId] = { status: 'running', task, projectId, startedAt: new Date().toISOString() };
    agentHistory.unshift({ projectId, sessionId, projectName: pName, task, status: 'running', startedAt: new Date().toISOString() });
    activeAgentTab[projectId] = sessionId;
    delete agentConvNew[projectId];  // continued → drill into this conversation

    modalActiveTab[projectId] = 'agent';
    refreshModal();
    renderAgentConsole();
    connectAgentStream(projectId, sessionId);
  } catch(e) {
    alert('Failed to continue: ' + e.message);
  }
}

// ── Agent image paste ────────────────────────────────────────────────────────

function handleAgentPaste(e, key) {
  const items = Array.from(e.clipboardData?.items || []);
  const files = Array.from(e.clipboardData?.files || []);
  // Try items first (screenshots, image blobs)
  const imageItems = items.filter(i => i.type.startsWith('image/'));
  // Also accept files pasted from Explorer (Ctrl+C a file, then Ctrl+V)
  const allFiles = imageItems.length > 0
    ? imageItems.map(i => i.getAsFile()).filter(Boolean)
    : files.length > 0 ? files : [];
  if (allFiles.length === 0) return;
  e.preventDefault();
  const list = agentPendingImages[key] || [];
  for (const file of allFiles) {
    const isImage = file.type.startsWith('image/');
    list.push({
      file,
      objectUrl: isImage ? URL.createObjectURL(file) : null,
      serverPath: null,
      isDocument: !isImage,
      fileName: file.name
    });
  }
  agentPendingImages[key] = list;
  refreshModal();
}

function handleAgentDragOver(e, el) {
  e.preventDefault();
  e.stopPropagation();
  if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
  el.classList.add('drag-over');
}

function handleAgentDragLeave(e, el) {
  // Only remove highlight if we actually left the container (not entering a child)
  if (el.contains(e.relatedTarget)) return;
  e.preventDefault();
  e.stopPropagation();
  el.classList.remove('drag-over');
}

function handleAgentDrop(e, key) {
  e.preventDefault();
  e.stopPropagation();
  // Find the drop-zone container and remove highlight
  const zone = e.target.closest('.agent-drop-zone');
  if (zone) zone.classList.remove('drag-over');
  const files = Array.from(e.dataTransfer.files);
  if (files.length === 0) return;
  const list = agentPendingImages[key] || [];
  for (const file of files) {
    const isImage = file.type.startsWith('image/');
    list.push({
      file,
      objectUrl: isImage ? URL.createObjectURL(file) : null,
      serverPath: null,
      isDocument: !isImage,
      fileName: file.name
    });
  }
  agentPendingImages[key] = list;
  refreshModal();
}

// Mobile attachment picker — paperclip button next to Send. Drag-and-drop
// isn't a thing on touch keyboards, so on mobile the user needs an explicit
// affordance to attach a file or take a photo. We funnel the picked files
// through the SAME `agentPendingImages[key]` array that handleAgentDrop /
// handleAgentPaste use, so the existing dispatch + send pipelines pick them
// up unchanged. No `accept` / no `capture` attribute on the input → on
// Android/iOS the native picker shows Camera + Photos + Files as separate
// sources, which is what Ron asked for.
function triggerAgentAttach(key) {
  const input = document.getElementById(`agent-attach-input-${key}`);
  if (input) input.click();
}


// ── interop: window re-exposure for inline/generated/cross-module callers ──
window.agentLogPanelHTML = agentLogPanelHTML;
window.loadAgentLog = loadAgentLog;
window.loadConversations = loadConversations;
window.upsertConversationCache = upsertConversationCache;
window._lastUserFromBuffer = _lastUserFromBuffer;
window.loadProjectPlans = loadProjectPlans;
window.renderPlansTab = renderPlansTab;
window.handleAgentPaste = handleAgentPaste;
window.handleAgentDragOver = handleAgentDragOver;
window.handleAgentDragLeave = handleAgentDragLeave;
window.handleAgentDrop = handleAgentDrop;
window.triggerAgentAttach = triggerAgentAttach;
window.deleteSelectedPlans = deleteSelectedPlans;
window.deleteSinglePlan = deleteSinglePlan;
window.dispatchContinue = dispatchContinue;
window.exportSelectedPlans = exportSelectedPlans;
window.openPlanFromHistory = openPlanFromHistory;
window.toggleAllPlans = toggleAllPlans;
window.toggleContinueInput = toggleContinueInput;
window.togglePlanSelection = togglePlanSelection;
