// ── Hivemind tab ─────────────────────────────────────────────────────────────

const hivemindDashboardWs = {};  // hivemindId -> selected workstream id (or null for overview)
let _hmTabDebounce = {};    // projectId -> setTimeout timer ID

function hivemindTabHTML(p) {
  const cached = hivemindCache[p.id];
  if (!cached || !cached.loaded) {
    return `<div class="card-section"><div style="color:var(--text-faint);font-style:italic">Loading hiveminds...</div></div>`;
  }
  const hiveminds = cached.hiveminds || [];

  let listHTML = '';
  if (!hiveminds.length) {
    listHTML = `<div style="color:var(--text-faint);font-style:italic;margin-bottom:14px">
      No hiveminds yet. Create one to start coordinated multi-agent analysis.
    </div>`;
  } else {
    const sorted = [...hiveminds].sort((a, b) => {
      const order = { active: 0, paused: 1, pending: 2, completed: 3, stopped: 4 };
      return (order[a.status] ?? 5) - (order[b.status] ?? 5);
    });
    listHTML = sorted.map(hm => {
      const ws = hm._workstreams || [];
      const totalFindings = ws.reduce((s, w) => s + (w.findings_count || 0), 0);
      const completedWs = ws.filter(w => w.status === 'completed').length;
      const controls = hm.status === 'active'
        ? `<button onclick="event.stopPropagation();hivemindAction('${esc(hm.id)}','pause')" title="Pause" style="background:none;border:none;color:var(--text-faint);cursor:pointer;font-size:13px;padding:2px 4px">&#x23F8;</button>
           <button onclick="event.stopPropagation();hivemindAction('${esc(hm.id)}','stop')" title="Stop" style="background:none;border:none;color:var(--text-faint);cursor:pointer;font-size:13px;padding:2px 4px">&#x23F9;</button>`
        : hm.status === 'paused'
        ? `<button onclick="event.stopPropagation();hivemindAction('${esc(hm.id)}','start')" title="Resume" style="background:none;border:none;color:var(--text-faint);cursor:pointer;font-size:13px;padding:2px 4px">&#x25B6;</button>`
        : (hm.status === 'completed' || hm.status === 'stopped' || hm.status === 'stale')
        ? `<button onclick="event.stopPropagation();hivemindAction('${esc(hm.id)}','start')" title="Restart" style="background:none;border:none;color:var(--text-faint);cursor:pointer;font-size:13px;padding:2px 4px">&#x25B6;</button>`
        : '';
      return `<div class="hm-list-row" onclick="openHivemindDashboard('${esc(hm.id)}')" style="display:flex;align-items:center;gap:8px;padding:8px 10px;border:1px solid var(--border);border-radius:6px;margin-bottom:6px;cursor:pointer;background:var(--surface);transition:background 0.15s" onmouseover="this.style.background='var(--surface-hover)'" onmouseout="this.style.background='var(--surface)'">
        <span class="hm-status-badge ${esc(hm.status)}" style="font-size:11px;flex-shrink:0">${esc(hm.status)}</span>
        <span style="flex:1;font-size:13px;font-weight:500;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(hm.title || hm.goal || 'Untitled')}</span>
        <span style="font-size:11px;color:var(--text-faint);flex-shrink:0">${ws.length} ws &middot; ${completedWs} done &middot; ${totalFindings} findings</span>
        <span style="display:flex;gap:2px;flex-shrink:0" onclick="event.stopPropagation()">${controls}</span>
      </div>`;
    }).join('');
  }

  return `<div class="card-section">
    <div class="section-title">Hiveminds</div>
    ${listHTML}
    <button class="btn-add" onclick="startHivemindChat('${esc(p.id)}')">+ New Hivemind</button>
  </div>`;
}

function wsStatusIcon(status) {
  const icons = {
    completed: '<span style="color:#22c55e">&#x2714;</span>',
    active: '<span style="color:var(--accent)">&#x25CF;</span>',
    pending: '<span style="color:var(--text-faint)">&#x25CB;</span>',
    blocked: '<span style="color:#f59e0b">&#x23F3;</span>',
    paused: '<span style="color:#f59e0b">&#x23F8;</span>',
    failed: '<span style="color:#ef4444">&#x2716;</span>',
  };
  return icons[status] || icons.pending;
}

function hivemindCardHTML(projectId, hm, expanded) {
  const ws = hm._workstreams || [];
  const wsListHTML = ws.map(w => `
    <div class="hm-ws-item" onclick="openHivemindDashboard('${esc(hm.id)}','${esc(w.id)}')">
      <span class="hm-ws-status-icon">${wsStatusIcon(w.status)}</span>
      <span class="hm-ws-title">${esc(w.title || w.id)}</span>
      <span class="hm-ws-meta">
        <span>${w.findings_count || 0} findings</span>
        <span>${esc(w.status)}</span>
        ${w.provider ? _providerBadge(w.provider) : ''}
        ${w.model ? `<span>${esc(w.model)}</span>` : ''}
      </span>
    </div>
  `).join('');

  const recentMsgs = (hm._recent_messages || []).slice(-10);
  const activityHTML = recentMsgs.map(m => {
    const ts = (m.timestamp || '').substring(11, 16);
    const isEscalation = m.type === 'escalation';
    return `<div class="hm-activity-item">
      <span class="hm-activity-ts">${esc(ts)}</span>
      <span class="hm-activity-msg ${isEscalation ? 'hm-activity-escalation' : ''}">
        ${esc(m.from || '')} &rarr; ${esc(m.to || '')}: ${esc((m.content || m.title || '').substring(0, 200))}
      </span>
    </div>`;
  }).join('');

  const totalFindings = ws.reduce((s, w) => s + (w.findings_count || 0), 0);
  const completedWs = ws.filter(w => w.status === 'completed').length;
  const activeWs = ws.filter(w => w.status === 'active').length;

  const controlsHTML = hm.status === 'active'
    ? `<button onclick="event.stopPropagation();hivemindAction('${esc(hm.id)}','pause')" title="Pause">&#x23F8;</button>
       <button onclick="event.stopPropagation();hivemindAction('${esc(hm.id)}','stop')" title="Stop">&#x23F9;</button>`
    : hm.status === 'paused'
    ? `<button onclick="event.stopPropagation();hivemindAction('${esc(hm.id)}','start')" title="Resume">&#x25B6;</button>
       <button onclick="event.stopPropagation();hivemindAction('${esc(hm.id)}','stop')" title="Stop">&#x23F9;</button>`
    : (hm.status === 'completed' || hm.status === 'stopped' || hm.status === 'stale')
    ? `<button onclick="event.stopPropagation();hivemindAction('${esc(hm.id)}','start')" title="Restart">&#x25B6;</button>`
    : '';

  return `<div style="margin-bottom:12px">
    <div class="hm-header">
      <h3 style="cursor:pointer" onclick="openHivemindDashboard('${esc(hm.id)}')">${esc(hm.title || 'Untitled')}</h3>
      <span class="hm-status-badge ${esc(hm.status)}">${esc(hm.status)}</span>
      <div class="hm-controls">${controlsHTML}</div>
    </div>
    <div class="hm-goal">${esc(hm.goal || '').substring(0, 300)}</div>
    <div class="hm-stats">
      <span>${ws.length} workstreams</span>
      <span>${completedWs} done</span>
      <span>${activeWs} active</span>
      <span>${totalFindings} findings</span>
      <span>${hm.updated_relative || ''}</span>
    </div>
    ${expanded ? `
      <div class="hm-ws-list">${wsListHTML || '<div style="color:var(--text-faint);font-size:12px;font-style:italic">Orchestrator is decomposing the goal into workstreams...</div>'}</div>
      ${recentMsgs.length ? `<div style="font-size:12px;color:var(--text-faint);margin-bottom:4px">Recent Activity</div>
        <div class="hm-activity">${activityHTML}</div>` : ''}
      <div class="hm-actions" style="margin-top:10px">
        <button onclick="openHivemindDashboard('${esc(hm.id)}')">Full Dashboard</button>
        <button class="secondary" onclick="viewSynthesis('${esc(hm.id)}')">View Synthesis</button>
      </div>
    ` : ''}
  </div>`;
}

async function loadHiveminds(projectId) {
  try {
    const res = await fetch(API_BASE + `/api/hivemind/list?project_id=${encodeURIComponent(projectId)}`);
    const hiveminds = await res.json();
    // Load workstreams and recent messages for each
    for (const hm of hiveminds) {
      try {
        const detail = await fetch(API_BASE + `/api/hivemind/${hm.id}`);
        const data = await detail.json();
        hm._workstreams = data.workstreams || [];
        hm._recent_messages = data.recent_messages || [];
        hm._decisions = data.decisions || [];
      } catch(e) { hm._workstreams = []; hm._recent_messages = []; }
    }
    hivemindCache[projectId] = { hiveminds, loaded: true };
    refreshModal();
  } catch(e) {
    hivemindCache[projectId] = { hiveminds: [], loaded: true };
    refreshModal();
  }
}

function startHivemindChat(projectId) {
  // Open a fresh agent tab and immediately dispatch the hivemind setup prompt
  // so the user lands directly in an active conversation, not a populated form.
  modalActiveTab[projectId] = 'agent';
  delete activeAgentTab[projectId];
  delete pendingResumeId[projectId];
  // Force the dispatch screen (the +New row). Without this, mobile lands on
  // the existing session: with one conversation the auto-select picks it,
  // with many the drill-down list shows — either way the `agent-task-...`
  // textarea we target below doesn't exist and dispatchAgent never fires.
  // Desktop also benefits — the dispatch row is what holds the textarea we
  // pre-fill.
  agentConvNew[projectId] = true;
  refreshModal();
  setTimeout(() => {
    const input = document.getElementById('agent-task-' + projectId);
    if (!input) {
      // Belt-and-braces: if the textarea still isn't there (e.g. the modal
      // is in some other tab state), surface a hint instead of silently
      // doing nothing — the old behavior would just look "broken".
      showToast('Could not open the new-conversation form. Tap +New manually then send your hivemind prompt.', 6000);
      return;
    }
    input.value = 'I want to start a new Hivemind multi-agent analysis on this project. ' +
      'Before creating it, ask me clarifying questions about the goal, scope, priorities, ' +
      'and any constraints. Then create the hivemind when you have enough context.';
    dispatchAgent(projectId);
  }, 100);
}


async function hivemindAction(hivemindId, action) {
  try {
    const res = await fetch(API_BASE + `/api/hivemind/${hivemindId}/${action}`, { method: 'POST' });
    const data = await res.json();
    if (!data.ok && data.error) showToast('Error: ' + data.error, 8000);
    // Reload all hiveminds for the project this belongs to
    for (const [pid, cache] of Object.entries(hivemindCache)) {
      if (cache.hiveminds?.some(h => h.id === hivemindId)) {
        delete hivemindCache[pid];
        loadHiveminds(pid);
        break;
      }
    }
  } catch(e) { showToast('Action failed', 8000); }
}

// ── Hivemind Dashboard Modal ────────────────────────────────────────────────

async function openHivemindDashboard(hivemindId, selectedWsId) {
  const modalId = '__hivemind_' + hivemindId;
  if (openModals.has(modalId)) {
    focusModal(modalId);
    if (selectedWsId) {
      hivemindDashboardWs[hivemindId] = selectedWsId;
      refreshHivemindDashboard(hivemindId);
    }
    return;
  }

  // Fetch full state
  let data;
  try {
    const res = await fetch(API_BASE + `/api/hivemind/${hivemindId}`);
    data = await res.json();
  } catch(e) { showToast('Failed to load hivemind', 8000); return; }

  const manifest = data.manifest;
  const workstreams = data.workstreams || [];
  hivemindDashboardWs[hivemindId] = selectedWsId || null;

  // Create modal
  const layer = document.getElementById('modal-layer');
  const win = document.createElement('div');
  win.className = 'modal-window focused';
  win.dataset.modalId = modalId;
  _clampModalSize(win, 900, 600);
  win.style.position = 'fixed';
  const z = nextModalZ++;
  win.style.zIndex = z;

  win.innerHTML = buildHivemindDashboardHTML(hivemindId, manifest, workstreams, data);

  layer.appendChild(win);
  openModals.set(modalId, { projectId: manifest.project_id, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);

  // Connect SSE for live updates
  connectHivemindSSE(hivemindId);
}

function buildHivemindDashboardHTML(hivemindId, manifest, workstreams, fullData) {
  const selectedWs = hivemindDashboardWs[hivemindId];
  const ws = selectedWs ? workstreams.find(w => w.id === selectedWs) : null;

  const sidebarItems = workstreams.map(w => `
    <div class="hm-dash-sidebar-item ${w.id === selectedWs ? 'active' : ''}"
         onclick="selectDashboardWs('${esc(hivemindId)}','${esc(w.id)}')">
      ${wsStatusIcon(w.status)} ${esc((w.title || w.id).substring(0, 25))}
    </div>
  `).join('');

  let mainContent;
  if (ws) {
    mainContent = buildWsDetailHTML(hivemindId, ws, fullData);
  } else {
    mainContent = buildHmOverviewHTML(hivemindId, manifest, workstreams, fullData);
  }

  const controlsHTML = manifest.status === 'active'
    ? `<button onclick="hivemindAction('${esc(hivemindId)}','pause')" title="Pause" style="background:transparent;border:1px solid var(--border);color:var(--text-faint);padding:4px 10px;border-radius:4px;cursor:pointer;font-size:12px">&#x23F8; Pause</button>
       <button onclick="hivemindAction('${esc(hivemindId)}','stop')" title="Stop" style="background:transparent;border:1px solid #ef444480;color:#ef4444;padding:4px 10px;border-radius:4px;cursor:pointer;font-size:12px">&#x23F9; Stop</button>`
    : `<button onclick="hivemindAction('${esc(hivemindId)}','start')" style="background:transparent;border:1px solid var(--border);color:var(--text-faint);padding:4px 10px;border-radius:4px;cursor:pointer;font-size:12px">&#x25B6; Start</button>`;

  return `<div class="modal-content" style="width:100%;height:100%;display:flex;flex-direction:column">
    <div class="modal-header" style="cursor:move;display:flex;align-items:center;gap:10px;padding:10px 16px;border-bottom:1px solid var(--border);background:var(--surface);flex-shrink:0">
      <h3 style="margin:0;font-size:14px;flex:1">${esc(manifest.title || 'Hivemind Dashboard')}</h3>
      <span class="hm-status-badge ${esc(manifest.status)}">${esc(manifest.status)}</span>
      ${controlsHTML}
      <button onclick="closeModalById('__hivemind_${esc(hivemindId)}')" style="background:none;border:none;color:var(--text-faint);cursor:pointer;font-size:16px;padding:4px 8px">&#x2715;</button>
    </div>
    <div class="hm-dashboard" id="hm-dash-body-${esc(hivemindId)}" style="flex:1;min-height:0">
      <div class="hm-dash-sidebar">
        <div class="hm-dash-sidebar-item ${!selectedWs ? 'active' : ''}"
             onclick="selectDashboardWs('${esc(hivemindId)}',null)">
          Overview
        </div>
        ${sidebarItems}
      </div>
      <div class="hm-dash-main" id="hm-dash-main-${esc(hivemindId)}">
        ${mainContent}
      </div>
    </div>
  </div>`;
}

function buildHmOverviewHTML(hivemindId, manifest, workstreams, fullData) {
  const totalFindings = workstreams.reduce((s, w) => s + (w.findings_count || 0), 0);
  const completed = workstreams.filter(w => w.status === 'completed').length;
  const active = workstreams.filter(w => w.status === 'active').length;
  const decisions = fullData.decisions || [];
  const openQ = fullData.open_questions || [];
  const allFindings = fullData._findings || [];

  const recentMsgs = (fullData.recent_messages || []).slice(-20);
  const busHTML = recentMsgs.map(m => {
    const ts = (m.timestamp || '').substring(11, 16);
    const isEsc = m.type === 'escalation';
    return `<div class="hm-activity-item">
      <span class="hm-activity-ts">${esc(ts)}</span>
      <span class="hm-activity-msg ${isEsc ? 'hm-activity-escalation' : ''}">
        <strong>${esc(m.from || '')}</strong> &rarr; ${esc(m.to || '')}: ${esc((m.content || m.title || '').substring(0, 300))}
      </span>
    </div>`;
  }).join('');

  return `
    <div class="hm-goal" style="margin-bottom:16px"><strong>Goal:</strong> ${esc(manifest.goal || '')}</div>
    <div class="hm-stats" style="margin-bottom:16px">
      <span>${workstreams.length} workstreams</span>
      <span>${completed} done</span>
      <span>${active} active</span>
      <span>${totalFindings} findings</span>
      <span>${decisions.length} decisions</span>
    </div>

    <div style="font-size:13px;font-weight:500;margin-bottom:8px">Workstreams</div>
    <div class="hm-ws-list">
      ${workstreams.map(w => `
        <div class="hm-ws-item" onclick="selectDashboardWs('${esc(hivemindId)}','${esc(w.id)}')">
          <span class="hm-ws-status-icon">${wsStatusIcon(w.status)}</span>
          <span class="hm-ws-title">${esc(w.title || w.id)}</span>
          <span class="hm-ws-meta">
            <span>P${w.priority || 5}</span>
            <span>${w.findings_count || 0} findings</span>
            <span>${esc(w.status)}</span>
            ${w.provider ? _providerBadge(w.provider) : ''}
            ${w.model ? `<span>${esc(w.model)}</span>` : ''}
          </span>
        </div>
      `).join('')}
    </div>

    ${allFindings.length ? `
      <div style="font-size:13px;font-weight:500;margin-top:16px;margin-bottom:8px">Recent Findings <span style="font-weight:400;color:var(--text-faint)">(${allFindings.length} total)</span></div>
      <div style="max-height:300px;overflow-y:auto">
        ${allFindings.slice(-20).reverse().map(f => `<div style="font-size:12px;padding:6px 8px;margin-bottom:4px;border:1px solid var(--border);border-radius:4px;background:var(--surface)">
          <div style="display:flex;align-items:center;gap:6px;margin-bottom:2px">
            <strong style="flex:1">${esc(f.title || '(untitled)')}</strong>
            <span style="font-size:10px;color:var(--text-faint)">${esc(f.ws_id || '')}</span>
            <span style="font-size:10px;color:var(--text-faint)">${esc((f.timestamp || '').substring(11, 16))}</span>
          </div>
          <div style="color:var(--text-faint);line-height:1.4">${esc((f.content || '').substring(0, 300))}${(f.content || '').length > 300 ? '...' : ''}</div>
        </div>`).join('')}
      </div>
    ` : ''}

    ${decisions.length ? `
      <div style="font-size:13px;font-weight:500;margin-top:16px;margin-bottom:8px">Decisions</div>
      ${decisions.map(d => `<div style="font-size:12px;padding:4px 0;border-bottom:1px solid var(--border-faint)">
        <strong>${esc(d.decision || '')}</strong>
        <span style="color:var(--text-faint)"> — ${esc(d.rationale || '').substring(0, 200)}</span>
      </div>`).join('')}
    ` : ''}

    ${openQ.length ? `
      <div style="font-size:13px;font-weight:500;margin-top:16px;margin-bottom:8px">Open Questions</div>
      ${openQ.map(q => `<div style="font-size:12px;padding:6px 8px;margin-bottom:4px;border:1px solid var(--border);border-radius:4px;display:flex;align-items:flex-start;gap:8px">
        <span style="flex:1;color:var(--text-faint);line-height:1.4">${esc(q.question || '')}</span>
        <button onclick="respondToQuestion('${esc(hivemindId)}',this)" data-question="${esc(q.question || '')}" data-question-id="${esc(q.id || '')}"
          style="background:var(--accent);border:none;color:#fff;padding:3px 8px;border-radius:3px;cursor:pointer;font-size:11px;white-space:nowrap;flex-shrink:0">Respond</button>
      </div>`).join('')}
    ` : ''}

    <div class="hm-bus-section">
      <div style="font-size:13px;font-weight:500;margin-bottom:8px">Message Bus</div>
      <div class="hm-activity" style="max-height:300px">${busHTML || '<div style="color:var(--text-faint);font-size:12px">No messages yet</div>'}</div>
    </div>

    <div class="hm-directive-row" style="margin-top:12px">
      <input class="hm-directive-input" id="hm-directive-${esc(hivemindId)}" placeholder="Send directive to orchestrator..."
        onkeydown="if(event.key==='Enter'){sendHivemindDirective('${esc(hivemindId)}');event.preventDefault()}">
      <button style="background:var(--accent);border:none;color:#fff;padding:6px 12px;border-radius:4px;cursor:pointer;font-size:12px"
        onclick="sendHivemindDirective('${esc(hivemindId)}')">Send</button>
    </div>

    <div class="hm-actions" style="margin-top:14px">
      <button onclick="viewSynthesis('${esc(hivemindId)}')">View Synthesis</button>
      <button class="secondary" onclick="triggerSynthesis('${esc(hivemindId)}')">Re-synthesize</button>
      <button class="secondary" onclick="openHmRunsModal('${esc(hivemindId)}','${esc(manifest.project_id || '')}','orchestrator','','Orchestrator')">Orchestrator Runs</button>
    </div>`;
}

function buildWsDetailHTML(hivemindId, ws, fullData) {
  const wsFindings = (fullData._findings || []).filter(f => f.ws_id === ws.id);
  const allMsgs = fullData.recent_messages || [];
  const wsMessages = allMsgs.filter(m => m.from === ws.id || m.to === ws.id);

  // Check if worker is running
  const sid = ws.current_agent_session_id;
  const isRunning = sid && typeof agent_sessions !== 'undefined';
  const projectId = (fullData.manifest && fullData.manifest.project_id) || '';

  return `
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px">
      <span class="hm-ws-status-icon" style="font-size:18px">${wsStatusIcon(ws.status)}</span>
      <h3 style="margin:0;font-size:15px">${esc(ws.title || ws.id)}</h3>
      <span class="hm-status-badge ${esc(ws.status)}">${esc(ws.status)}</span>
      ${ws.model ? `<span style="font-size:11px;color:var(--text-faint)">${esc(ws.model)}</span>` : ''}
      <button style="margin-left:auto;background:transparent;border:1px solid var(--border);color:var(--text-faint);padding:3px 10px;border-radius:4px;cursor:pointer;font-size:11px"
        onclick="openHmRunsModal('${esc(hivemindId)}','${esc(projectId)}','worker','${esc(ws.id)}','${esc(ws.title || ws.id)}')">Runs</button>
    </div>
    <div style="font-size:13px;color:var(--text-faint);margin-bottom:12px;line-height:1.4">${esc(ws.description || '')}</div>

    <div class="hm-stats" style="margin-bottom:12px">
      <span>Priority: ${ws.priority || 5}</span>
      <span>${ws.findings_count || 0} findings</span>
      <span>${ws.sessions_used || 0} sessions</span>
      ${ws.retry_count ? `<span>${ws.retry_count} retries</span>` : ''}
      ${ws.dependencies?.length ? `<span>Depends: ${ws.dependencies.join(', ')}</span>` : ''}
    </div>

    ${ws.status === 'pending' || ws.status === 'paused' ? `
      <div class="hm-actions" style="margin-bottom:12px">
        <button onclick="spawnWorker('${esc(hivemindId)}','${esc(ws.id)}')">Spawn Worker</button>
      </div>
    ` : ''}

    ${sid ? `<div style="font-size:12px;color:var(--text-faint);margin-bottom:10px">
      Worker session: <code>${esc(sid)}</code>
    </div>` : ''}

    ${wsFindings.length ? `
      <div style="font-size:13px;font-weight:500;margin-bottom:8px">Findings <span style="font-weight:400;color:var(--text-faint)">(${wsFindings.length})</span></div>
      <div style="max-height:250px;overflow-y:auto;margin-bottom:12px">
        ${wsFindings.slice(-15).reverse().map(f => `<div style="font-size:12px;padding:6px 8px;margin-bottom:4px;border:1px solid var(--border);border-radius:4px;background:var(--surface)">
          <div style="display:flex;align-items:center;gap:6px;margin-bottom:2px">
            <strong style="flex:1">${esc(f.title || '(untitled)')}</strong>
            <span style="font-size:10px;color:var(--text-faint)">${esc((f.timestamp || '').substring(11, 16))}</span>
          </div>
          <div style="color:var(--text-faint);line-height:1.4">${esc((f.content || '').substring(0, 300))}${(f.content || '').length > 300 ? '...' : ''}</div>
        </div>`).join('')}
      </div>
    ` : ''}

    <div style="font-size:13px;font-weight:500;margin-bottom:8px">Messages</div>
    <div class="hm-activity" style="max-height:250px">
      ${wsMessages.length ? wsMessages.map(m => {
        const ts = (m.timestamp || '').substring(11, 16);
        return `<div class="hm-activity-item">
          <span class="hm-activity-ts">${esc(ts)}</span>
          <span class="hm-activity-msg">${esc(m.from || '')} &rarr; ${esc(m.to || '')}: ${esc((m.content || m.title || '').substring(0, 300))}</span>
        </div>`;
      }).join('') : '<div style="color:var(--text-faint);font-size:12px">No messages yet</div>'}
    </div>

    <div class="hm-directive-row" style="margin-top:12px">
      <input class="hm-directive-input" id="hm-ws-directive-${esc(ws.id)}" placeholder="Send directive to this workstream..."
        onkeydown="if(event.key==='Enter'){sendWsDirective('${esc(hivemindId)}','${esc(ws.id)}');event.preventDefault()}">
      <button style="background:var(--accent);border:none;color:#fff;padding:6px 12px;border-radius:4px;cursor:pointer;font-size:12px"
        onclick="sendWsDirective('${esc(hivemindId)}','${esc(ws.id)}')">Send</button>
    </div>`;
}

async function openHmRunsModal(hivemindId, projectId, role, wsId, label) {
  const modalId = `__hm_runs_${hivemindId}_${role}_${wsId || ''}`;
  if (openModals.has(modalId)) {
    focusModal(modalId);
    return;
  }
  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content';
  _clampModalSize(content, 700);
  const titleLabel = role === 'orchestrator' ? `${esc(label)} runs` : `${esc(label)} — runs`;
  content.innerHTML = `
    <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:16px 24px 12px 28px">
      <span style="font-size:14px;font-weight:700;color:var(--text)">${titleLabel}</span>
      <div class="modal-window-controls" style="position:static;display:flex;gap:4px">
        <button class="modal-minimize" onclick="minimizeModal('${modalId}')" title="Minimize">&#x2015;</button>
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
    </div>
    <div id="hm-runs-body-${hivemindId}-${role}-${wsId || 'all'}" style="padding:6px 24px 22px 28px;max-height:60vh;overflow-y:auto">
      <div class="runs-empty">Loading runs...</div>
    </div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);
  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);

  await loadHmRunsPage(hivemindId, projectId, role, wsId, 0);
}

async function loadHmRunsPage(hivemindId, projectId, role, wsId, offset) {
  const bodyId = `hm-runs-body-${hivemindId}-${role}-${wsId || 'all'}`;
  const body = document.getElementById(bodyId);
  if (!body) return;
  body.innerHTML = '<div class="runs-empty">Loading runs...</div>';
  try {
    const limit = 50;
    const params = new URLSearchParams({ role });
    if (wsId) params.set('ws_id', wsId);
    params.set('limit', String(limit));
    params.set('offset', String(offset));
    const res = await fetch(API_BASE + `/api/hivemind/${encodeURIComponent(hivemindId)}/runs?${params}`);
    if (!res.ok) {
      body.innerHTML = '<div class="runs-empty">Failed to load runs.</div>';
      return;
    }
    const data = await res.json();
    const runs = data.runs || [];
    const total = data.total || 0;
    const pageFnTemplate = `loadHmRunsPage('${esc(hivemindId)}','${esc(projectId)}','${esc(role)}','${esc(wsId || '')}',$OFFSET)`;
    body.innerHTML = renderRunRows(runs, projectId)
                   + renderRunsPagination(total, data.offset || 0, data.limit || limit, pageFnTemplate);
  } catch(e) {
    const body = document.getElementById(bodyId);
    if (body) body.innerHTML = '<div class="runs-empty">Failed to load runs.</div>';
  }
}

function selectDashboardWs(hivemindId, wsId) {
  hivemindDashboardWs[hivemindId] = wsId;
  refreshHivemindDashboard(hivemindId);
}

async function refreshHivemindDashboard(hivemindId) {
  const modalId = '__hivemind_' + hivemindId;
  if (!openModals.has(modalId)) return;
  if (_hmDashInFlight[hivemindId]) return;
  _hmDashInFlight[hivemindId] = true;
  try {
    const [res, findingsRes] = await Promise.all([
      fetch(API_BASE + `/api/hivemind/${hivemindId}`),
      fetch(API_BASE + `/api/hivemind/${hivemindId}/knowledge/findings`),
    ]);
    const data = await res.json();
    const allFindings = await findingsRes.json().catch(() => []);
    data._findings = Array.isArray(allFindings) ? allFindings : [];

    const mainEl = document.getElementById('hm-dash-main-' + hivemindId);
    if (!mainEl) { _hmDashInFlight[hivemindId] = false; return; }

    const selectedWs = hivemindDashboardWs[hivemindId];
    const ws = selectedWs ? (data.workstreams || []).find(w => w.id === selectedWs) : null;

    if (ws) {
      mainEl.innerHTML = buildWsDetailHTML(hivemindId, ws, data);
    } else {
      mainEl.innerHTML = buildHmOverviewHTML(hivemindId, data.manifest, data.workstreams || [], data);
    }

    // Update sidebar active state
    const sidebar = openModals.get(modalId)?.element?.querySelector('.hm-dash-sidebar');
    if (sidebar) {
      sidebar.querySelectorAll('.hm-dash-sidebar-item').forEach(el => el.classList.remove('active'));
      const items = sidebar.querySelectorAll('.hm-dash-sidebar-item');
      items.forEach(el => {
        const text = el.textContent.trim();
        if (!selectedWs && text === 'Overview') el.classList.add('active');
      });
    }
  } catch(e) {}
  _hmDashInFlight[hivemindId] = false;
}

async function spawnWorker(hivemindId, wsId) {
  try {
    const res = await fetch(API_BASE + `/api/hivemind/${hivemindId}/workstreams/${wsId}/spawn`, { method: 'POST' });
    const data = await res.json();
    if (data.ok) {
      showToast(`Worker spawned for ${wsId}`);
      refreshHivemindDashboard(hivemindId);
    } else {
      showToast('Error: ' + (data.error || 'unknown'), 8000);
    }
  } catch(e) { showToast('Failed to spawn worker', 8000); }
}

async function sendHivemindDirective(hivemindId) {
  const el = document.getElementById('hm-directive-' + hivemindId);
  if (!el) return;
  const msg = el.value.trim();
  if (!msg) return;
  const pendingQId = el.dataset.pendingQuestionId || '';
  el.value = '';
  delete el.dataset.pendingQuestionId;
  try {
    await fetch(API_BASE + `/api/hivemind/${hivemindId}/intervene`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: msg, target: 'orchestrator' }),
    });
    if (pendingQId) {
      await fetch(API_BASE + `/api/hivemind/${hivemindId}/knowledge/questions/${pendingQId}/resolve`, { method: 'POST' });
    }
    refreshHivemindDashboard(hivemindId);
  } catch(e) {}
}

function respondToQuestion(hivemindId, btn) {
  const question = btn.dataset.question || '';
  const questionId = btn.dataset.questionId || '';
  const directiveEl = document.getElementById('hm-directive-' + hivemindId);
  if (directiveEl) {
    directiveEl.value = `RE: "${question.substring(0, 100)}${question.length > 100 ? '...' : ''}" — `;
    directiveEl.focus();
    directiveEl.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    // Store question ID so sendHivemindDirective can resolve it
    directiveEl.dataset.pendingQuestionId = questionId;
    directiveEl.dataset.pendingHivemindId = hivemindId;
  }
}

async function sendWsDirective(hivemindId, wsId) {
  const el = document.getElementById('hm-ws-directive-' + wsId);
  if (!el) return;
  const msg = el.value.trim();
  if (!msg) return;
  el.value = '';
  try {
    await fetch(API_BASE + `/api/hivemind/${hivemindId}/intervene`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: msg, target: wsId }),
    });
    refreshHivemindDashboard(hivemindId);
  } catch(e) {}
}

async function viewSynthesis(hivemindId) {
  try {
    const res = await fetch(API_BASE + `/api/hivemind/${hivemindId}/knowledge/synthesis`);
    const data = await res.json();
    const content = (typeof data.content === 'string') ? data.content : JSON.stringify(data.content || '');
    const modalId = '__hm_synth_' + hivemindId;
    if (openModals.has(modalId)) { focusModal(modalId); return; }

    // Render markdown line-by-line (same approach as plan file viewer)
    const lines = content.split('\n');
    let bodyHTML = '';
    let tableLines = [];
    function flushTable() {
      if (!tableLines.length) return;
      if (isPipeTable(tableLines)) {
        bodyHTML += `<div class="hl-table">${buildPipeTable(tableLines)}</div>`;
      } else {
        bodyHTML += `<div class="hl-table-pre">${tableLines.map(l => formatTableLine(esc(l))).join('\n')}</div>`;
      }
      tableLines = [];
    }
    for (const line of lines) {
      if (isTableLine(line)) { tableLines.push(line); }
      else if (tableLines.length > 0 && line.trim() === '') { tableLines.push(line); }
      else { flushTable(); bodyHTML += `<div class="agent-line">${formatAgentText(line)}</div>`; }
    }
    flushTable();

    const layer = document.getElementById('modal-layer');
    const win = document.createElement('div');
    win.className = 'modal-window focused';
    win.dataset.modalId = modalId;
    _clampModalSize(win, 800, 600);
    const z = nextModalZ++;
    win.style.zIndex = z;
    win.innerHTML = `
      <div class="modal-content plan-viewer-content" style="height:100%;display:flex;flex-direction:column">
        <div class="modal-window-controls">
          <button class="modal-minimize" onclick="minimizeModal('${esc(modalId)}')" title="Minimize">&#x2015;</button>
          <button class="modal-close" onclick="closeModalById('${esc(modalId)}')" title="Close">&#10005;</button>
        </div>
        <div class="plan-viewer-header modal-header">
          <h3 style="color:#c4b5fd;margin:0;font-size:15px">Synthesis</h3>
        </div>
        <div class="plan-viewer-body" style="flex:1;overflow-y:auto">${bodyHTML}</div>
      </div>`;
    layer.appendChild(win);
    openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
    centerModalElement(win);
    focusModal(modalId);
  } catch(e) { showToast('Failed to load synthesis', 8000); }
}

async function triggerSynthesis(hivemindId) {
  try {
    // Get project_id from cached data
    let projectId = '';
    for (const [pid, cache] of Object.entries(hivemindCache)) {
      if (cache.hiveminds?.some(h => h.id === hivemindId)) { projectId = pid; break; }
    }
    // Just call intervene with a synthesis request — server will dispatch orchestrator
    await fetch(API_BASE + `/api/hivemind/${hivemindId}/intervene`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: 'Please synthesize all findings now.', to: 'orchestrator' }),
    });
    showToast('Synthesis requested');
  } catch(e) {}
}

function connectHivemindSSE(hivemindId) {
  if (hivemindSSE[hivemindId]) return;
  const es = new EventSource(API_BASE + `/api/hivemind/${hivemindId}/bus/stream`);
  hivemindSSE[hivemindId] = es;
  es.onmessage = (evt) => {
    try {
      const data = JSON.parse(evt.data);
      // Show toast for escalations immediately (no debounce)
      if (data.type === 'hivemind_escalation') {
        showToast('Hivemind escalation: ' + (data.message || '').substring(0, 100), 8000);
      }
      // Debounced dashboard refresh (500ms trailing edge)
      clearTimeout(_hmDashDebounce[hivemindId]);
      _hmDashDebounce[hivemindId] = setTimeout(() => {
        refreshHivemindDashboard(hivemindId);
      }, 500);
      // Debounced hivemind tab reload (2s trailing edge — expensive N+1 fetch)
      for (const [pid, cache] of Object.entries(hivemindCache)) {
        if (cache.hiveminds?.some(h => h.id === hivemindId)) {
          if (modalActiveTab[pid] === 'hivemind') {
            clearTimeout(_hmTabDebounce[pid]);
            _hmTabDebounce[pid] = setTimeout(() => {
              loadHiveminds(pid);
            }, 2000);
          } else {
            cache.loaded = false;
          }
          break;
        }
      }
    } catch(e) {}
  };
  es.onerror = () => {
    es.close();
    delete hivemindSSE[hivemindId];
    // Reconnect after delay
    setTimeout(() => {
      if (openModals.has('__hivemind_' + hivemindId)) {
        connectHivemindSSE(hivemindId);
      }
    }, 5000);
  };
}


// ── interop: window re-exposure for inline/generated/cross-module callers ──
window.startHivemindChat = startHivemindChat;
window.hivemindAction = hivemindAction;
window.openHivemindDashboard = openHivemindDashboard;
window.openHmRunsModal = openHmRunsModal;
window.respondToQuestion = respondToQuestion;
window.selectDashboardWs = selectDashboardWs;
window.sendHivemindDirective = sendHivemindDirective;
window.sendWsDirective = sendWsDirective;
window.spawnWorker = spawnWorker;
window.triggerSynthesis = triggerSynthesis;
window.viewSynthesis = viewSynthesis;
