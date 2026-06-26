// ── Tile HTML (compact grid card) ───────────────────────────────────────────

function computeLiveStatus(projectId) {
  let currentTask = 'Idle', currentTaskClass = 'idle';
  let nextAction = '\u2014', nextActionClass = '';

  // --- Current Task ---
  // Check hivemind first
  const hmData = hivemindCache[projectId];
  if (hmData && hmData.hiveminds) {
    const activeHm = hmData.hiveminds.find(h => h.status === 'running' || h.status === 'active');
    if (activeHm) {
      const ws = activeHm._workstreams || [];
      const done = ws.filter(w => w.status === 'completed').length;
      const active = ws.filter(w => w.status === 'active').length;
      const goal = activeHm.goal || activeHm.title || '';
      const goalSnip = goal.length > 40 ? goal.slice(0, 37) + '...' : goal;
      currentTask = `Hivemind: ${done}/${ws.length} done, ${active} active \u2014 ${goalSnip}`;
      currentTaskClass = 'running';
    }
  }

  // Check running/idle agents (don't override hivemind).
  //
  // CONSOLIDATED SOURCE: p.live_agent is server-authoritative — derived
  // from the in-memory agent_sessions map every /api/projects poll, fresh
  // for ALL projects. agentHistory is only refreshed by fetchAgentStatus()
  // for projects whose modal THIS client has open, so for a closed project
  // it's frozen at cold-start (a stale errored/idle session) and used to
  // mislabel an actively-running project as "Error/stuck". So when the
  // server reports a live agent it WINS here, at the single resolver — not
  // patched per-consumer downstream. agentHistory is only a supplementary
  // detail source (the richer client-side task string / plan-vs-question
  // sub-state) and the sole source for completed/error history, which the
  // server's live map (running/idle only) does not retain.
  const sessions = agentHistory.filter(h => h.projectId === projectId && !isHivemindWorker(h));
  const runningSess = sessions.find(h => h.status === 'running' || h.status === 'idle');
  const _p = allProjects.find(x => x.id === projectId);
  const la = _p && _p.live_agent;  // {state, reason, task} | null | undefined
  if (la) {
    // SERVER TRUTH — authoritative regardless of whether this client has
    // refreshed agentHistory for the project.
    const cache = runningSess ? (agentStatusCache[runningSess.sessionId] || {}) : {};
    const isPlan = la.reason === 'plan' || cache.waitingForPlanApproval;
    const isQuestion = la.reason === 'question' || cache.waitingForQuestion;
    if (isPlan) {
      currentTask = 'Awaiting plan approval';
      currentTaskClass = 'plan-approval';
    } else if (isQuestion) {
      currentTask = 'Awaiting your answer';
      currentTaskClass = 'question';
    } else if (currentTaskClass !== 'running') {
      // Prefer the client's own (fresher when modal-open) task string,
      // fall back to the server-provided task so closed projects still
      // get a meaningful line instead of a generic placeholder.
      const task = (runningSess && runningSess.task) || la.task || 'Working...';
      currentTask = task.length > 60 ? task.slice(0, 57) + '...' : task;
      // la.state is the /api/projects poll (coarse, lags a live turn). The
      // client SSE is the freshest per-moment signal: turn_start sets the
      // session cache/history status to 'running', turn_complete to 'idle'
      // (see the SSE handler). When that fresher signal says the agent is
      // actively mid-turn, it WINS over a lagging server 'idle' — otherwise
      // the tile reads "Awaiting input" while the console (which reads the
      // SSE status directly) correctly reads "In progress": the exact
      // tile-stuck-while-running gap the user hit. We only ever promote
      // idle→running on the fresher signal, never demote, so a closed
      // project with no SSE still falls back to the server's authority.
      const clientRunning = cache.status === 'running'
        || (runningSess && runningSess.status === 'running');
      currentTaskClass = (la.state === 'idle' && !clientRunning) ? 'idle-agent' : 'running';
    }
  } else if (runningSess) {
    // No server signal (old backend, or genuinely no live agent) but this
    // client's history — fresh because its modal is open — shows one.
    const cache = agentStatusCache[runningSess.sessionId] || {};
    if (cache.waitingForPlanApproval) {
      currentTask = 'Awaiting plan approval';
      currentTaskClass = 'plan-approval';
    } else if (cache.waitingForQuestion) {
      currentTask = 'Awaiting your answer';
      currentTaskClass = 'question';
    } else if (currentTaskClass !== 'running') {
      const task = runningSess.task || 'Working...';
      currentTask = task.length > 60 ? task.slice(0, 57) + '...' : task;
      currentTaskClass = runningSess.status === 'idle' ? 'idle-agent' : 'running';
    }
  } else if (currentTaskClass === 'idle') {
    // No live agent on the server AND none in (fresh) client history — a
    // stale errored session can no longer mask a running project because
    // we only reach here when la is falsy. Surface error/completed history.
    const errored = sessions.find(h => h.status === 'error');
    if (errored) {
      const task = errored.task || 'Unknown task';
      currentTask = task.length > 55 ? task.slice(0, 52) + '...' : task;
      currentTaskClass = 'error';
    } else {
      // Check last completed
      const completed = sessions.find(h => h.status === 'completed');
      if (completed) {
        const task = completed.task || 'Task';
        currentTask = 'Done: ' + (task.length > 55 ? task.slice(0, 52) + '...' : task);
        currentTaskClass = 'completed';
      }
    }
  }

  // --- Next Action ---
  // Hivemind pending workstreams
  if (hmData && hmData.hiveminds) {
    const activeHm = hmData.hiveminds.find(h => h.status === 'running' || h.status === 'active');
    if (activeHm) {
      const ws = activeHm._workstreams || [];
      const pending = ws.find(w => w.status === 'pending');
      if (pending) {
        const title = pending.title || pending.id || 'workstream';
        nextAction = 'Next: ' + (title.length > 50 ? title.slice(0, 47) + '...' : title);
        nextActionClass = 'next-text';
      }
    }
  }
  // Backlog fallback
  if (!nextActionClass) {
    const p = allProjects.find(x => x.id === projectId);
    const open = ((p && p.backlog) || []).filter(i => i.status === 'open');
    if (open.length) {
      const text = open[0].text || '';
      nextAction = text.length > 55 ? text.slice(0, 52) + '...' : text;
      nextActionClass = 'next-text';
    }
  }

  return { currentTask, currentTaskClass, nextAction, nextActionClass };
}

// Collapse internal project status + live agent state into one of the 5
// user-facing states from the design handoff: working/asking/stuck/done/idle.
function friendlyStatus(p) {
  if (!p) return 'idle';
  // Single resolver: computeLiveStatus() already reconciles the
  // server-authoritative p.live_agent against the lazily-refreshed client
  // agentHistory (server wins), so a stale errored session can no longer
  // surface as c==='error' while an agent is live. No live_agent
  // special-casing here — that divergence is what full consolidation removed.
  const live = computeLiveStatus(p.id) || {};
  const c = live.currentTaskClass;
  if (c === 'running')        return 'working';
  if (c === 'plan-approval')  return 'asking';
  if (c === 'question')       return 'asking';
  if (c === 'error')          return 'stuck';
  if (p.blocked)              return 'stuck';
  switch (p.status) {
    // NOTE: 'active' is a project-LIFECYCLE state ("in play", not
    // parked/completed) — it is NOT "an agent is running right now".
    // Almost every project sits at 'active' permanently, so mapping it to
    // 'working' made every tile show a permanent, never-updated
    // "IN PROGRESS" badge. Real activity is already caught above via the
    // server-authoritative live_agent (c === 'running'/'plan'/'question').
    // So 'active' falls through to the live-evidence tail below: 'done' if
    // there's completed history, else 'idle' (resting, ready) — truthful.
    case 'waiting':   return 'asking';
    case 'blocked':   return 'stuck';
    case 'completed': return 'done';
    case 'parked':    return 'idle';
  }
  if (c === 'completed')  return 'done';
  // 'idle-agent' = a live agent SESSION alive but momentarily between turns
  // (Mode-B persistent process; server live_agent.state 'idle'), with
  // NOTHING pending. "Awaiting input" is reserved for the cases we actually
  // DETECT the agent is blocked on the user — a plan or a question — and
  // those are already caught above (c==='plan-approval'/'question', from
  // server-authoritative la.reason). Labelling plain turn-finished idle
  // "Awaiting input" was a fabricated claim: the agent is not necessarily
  // waiting for anyone, it simply completed its turn. The truthful state is
  // 'done' ("All done" / "Completed" — i.e. done with this turn, ready for
  // more), not a false "needs you". This pill is the one authoritative
  // indicator; the console label mirrors it (see consoleStatusLabel).
  if (c === 'idle-agent') return 'done';
  return 'idle';
}

const FRIENDLY_TO_VOICE = {
  working: 'status_active',
  asking:  'status_waiting',
  stuck:   'status_blocked',
  done:    'status_completed',
  idle:    'status_parked',
};

// Build a one-sentence plain-English status line for a project tile, driven
// by friendlyStatus(). Falls back to p.summary (profile-generated) for idle
// projects with no recent activity, so the tile still reads as meaningful.
function friendlySummary(p) {
  if (!p) return '';
  const fs = friendlyStatus(p);
  const live = computeLiveStatus(p.id) || {};
  const isCasual = currentVoice === 'casual';
  const clip = (s, n) => { s = String(s || '').trim(); return s.length > n ? s.slice(0, n - 1) + '…' : s; };

  const sessions = agentHistory.filter(h => h.projectId === p.id && !isHivemindWorker(h));
  const runningSess = sessions.find(h => h.status === 'running' || h.status === 'idle');
  const erroredSess = sessions.find(h => h.status === 'error');
  const completedSess = sessions.find(h => h.status === 'completed');
  const taskOf = s => clip((s && s.task) || '', 60);

  const hmData = hivemindCache[p.id];
  const activeHm = hmData && hmData.hiveminds && hmData.hiveminds.find(h => h.status === 'running' || h.status === 'active');

  if (fs === 'working') {
    if (activeHm) {
      const ws = activeHm._workstreams || [];
      const done = ws.filter(w => w.status === 'completed').length;
      const goal = clip(activeHm.goal || activeHm.title || 'the plan', 50);
      return isCasual
        ? `Making progress on ${goal} — ${done} of ${ws.length} done.`
        : `In progress on ${goal} (${done}/${ws.length} complete).`;
    }
    // Prefer the client's own session task; fall back to the server-provided
    // live_agent task so closed projects (no runningSess client-side) still
    // get a meaningful subtitle instead of the generic placeholder.
    const task = taskOf(runningSess) || clip((p.live_agent && p.live_agent.task) || '', 60) || 'the current task';
    return isCasual ? `Working on ${task}. I'll let you know when it's ready.` : `In progress: ${task}.`;
  }

  if (fs === 'asking') {
    const cache = runningSess ? (agentStatusCache[runningSess.sessionId] || {}) : {};
    if (cache.waitingForPlanApproval) {
      return isCasual
        ? `I have a plan ready — take a look and tell me to go ahead.`
        : `Awaiting plan approval. Review and approve to proceed.`;
    }
    return isCasual
      ? `I need a quick answer from you before I can keep going.`
      : `Awaiting input to proceed.`;
  }

  if (fs === 'stuck') {
    if (p.blocked) {
      const reason = clip(p.blocked_reason, 60);
      return isCasual ? `Stuck — ${reason || 'something is blocking me'}.` : `Blocked: ${reason || 'unresolved dependency'}.`;
    }
    const task = taskOf(erroredSess) || 'the last task';
    return isCasual ? `Hit a snag with ${task}. Could use your help.` : `Error on ${task}. Intervention required.`;
  }

  if (fs === 'done') {
    const task = taskOf(completedSess);
    if (task) {
      return isCasual ? `All done with ${task}. Take a look when you can.` : `Completed: ${task}.`;
    }
    return isCasual ? `All caught up for now.` : `All tasks completed.`;
  }

  // idle — prefer generated profile summary, else generic rest line
  if (p.summary) return clip(p.summary, 140);
  if (p.description) return clip(p.description, 140);
  return isCasual ? `Resting — ready whenever you are.` : `Idle. No active tasks.`;
}

function tileHTML(p, slotIndex) {
  const sc = `status-${p.status||'unknown'}`;
  const fs = friendlyStatus(p);
  const domCfg = getDomainConfig(p.domain||'general');
  const backlog = p.backlog || [];
  const openItems = backlog.filter(i => i.status === 'open');
  const backlogBadge = openItems.length
    ? `<span class="backlog-badge">${openItems.length} open</span>` : '';
  // No separate "AGENT RUNNING" badge: the single status pill below is the
  // sole, authoritative indicator of the agent's immediate state (user
  // directive — two contradicting badges is exactly the bug being fixed).
  const live = computeLiveStatus(p.id);
  const summaryText = esc(friendlySummary(p));
  const techLine = p.blocked
    ? `<div class="tile-task blocked-text">${esc(p.blocked_reason||'Blocked')}</div>`
    : (live.currentTask && live.currentTask !== 'Idle'
        ? `<div class="tile-task ${live.currentTaskClass}">${esc(live.currentTask)}</div>`
        : '');

  const cardBgStyle = p.modal_color ? ` style="border-color:${p.modal_color.color}"` : '';

  return `
  <div class="card ${sc} friendly-${fs}" data-id="${esc(p.id)}" data-slot="${slotIndex !== undefined ? slotIndex : ''}"${cardBgStyle}>
    <div class="card-header">
      <div class="card-title-row">
        ${p.emoji ? `<span class="card-avatar">${esc(p.emoji)}</span>` : ''}
        <span class="project-name">${esc(p.name||p.id)}</span>
        <span class="domain-tag" style="background:${domCfg.bg};color:${domCfg.color}">${esc(domCfg.label||p.domain||'general')}</span>
      </div>
      <div style="display:flex;align-items:center;gap:6px">
        <span class="status-pill friendly-${fs}"><span class="status-dot"></span>${esc(vl(FRIENDLY_TO_VOICE[fs]))}</span>
      </div>
    </div>
    <div class="tile-body">
      ${summaryText ? `<div class="tile-summary">${summaryText}</div>` : ''}
      ${techLine}
      ${live.nextAction !== '\u2014' ? `<div class="tile-next ${live.nextActionClass}">${esc(live.nextAction)}</div>` : ''}
    </div>
    <div class="tile-footer">
      ${backlogBadge}
      ${_providerBadge(p.provider)}
      <span class="time-ago">${esc(p.last_updated_relative||'')}</span>
    </div>
  </div>`;
}

// ── Modal HTML (full detail view) ───────────────────────────────────────────

function modalContentHTML(p) {
  // Modal header status MUST come from the single consolidated resolver
  // (same as the tile / mobile row / list row), NOT raw p.status. Otherwise
  // a lifecycle-'active' project with no live agent shows "IN PROGRESS" in
  // the open modal while the tile correctly shows "IDLE" — same project,
  // same moment, two answers. Opening a modal is a VIEW action; it must
  // never change the status badge.
  const fs = friendlyStatus(p);
  const showDone = showDoneMap[p.id] || false;
  const backlog = p.backlog || [];
  const openItems = backlog.filter(i => i.status === 'open');
  const doneItems = backlog.filter(i => i.status === 'done');

  const backlogBadge = openItems.length
    ? `<span class="backlog-badge">${openItems.length} open</span>` : '';

  const modalLive = computeLiveStatus(p.id);
  const currentTaskHTML = p.blocked
    ? `<span class="summary-value blocked-text">${esc(p.blocked_reason||'Blocked')}</span>`
    : `<span class="summary-value ${modalLive.currentTaskClass}">${esc(modalLive.currentTask)}</span>`;

  // Migrate stale tab selections (memory/rules moved to three-dot menu;
  // hivemind moved to global sidebar Hivemind view)
  const validTabs = ['agent','backlog','agent-log','plans','activity'];
  let activeTab = modalActiveTab[p.id] || 'agent';
  if (!validTabs.includes(activeTab)) { activeTab = 'agent'; modalActiveTab[p.id] = 'agent'; }

  const logHTML = (p.activity_log||[]).slice(0,20).map(e => `
    <div class="log-entry">
      <span class="log-ts">${esc(e.ts_relative||e.ts||'')}</span>
      <span class="log-msg">${esc(e.msg||'')}</span>
    </div>`).join('');

  // Sort: in_progress agent items first, then other agent-open, then user-open, then done
  const rank = it => {
    if (it.status === 'done') return 3;
    if (it.agent_status === 'in_progress') return 0;
    if ((it.source || '').startsWith('agent:')) return 1;
    return 2;
  };
  const visibleItems = [...openItems, ...(showDone ? doneItems : [])].slice().sort((a,b) => rank(a) - rank(b));
  const backlogItemsHTML = visibleItems.map(item => {
    const isAgent = (item.source || '').startsWith('agent:');
    const isInProgress = item.agent_status === 'in_progress';
    // Counts come from the trimmed list payload (notes_count/attachments_count);
    // fall back to array length once the full backlog is lazy-loaded on modal open.
    const notesN = item.notes_count ?? (item.notes || []).length;
    const attsN = item.attachments_count ?? (item.attachments || []).length;
    const extraClasses = [
      item.status==='done' ? 'done' : '',
      `priority-${item.priority||'normal'}`,
      isAgent ? 'agent-source' : '',
      isInProgress ? 'agent-in-progress' : '',
    ].filter(Boolean).join(' ');
    const agentBadgeHTML = isAgent
      ? `<span class="agent-badge${isInProgress ? ' in-progress' : ''}" title="Added by agent (${esc(item.source)})">${isInProgress ? 'doing' : 'agent'}</span>`
      : '';
    const activityLine = (isInProgress && item.agent_activity)
      ? `<div class="agent-activity-line">${esc(item.agent_activity)}</div>`
      : '';
    return `
    <div class="backlog-item ${extraClasses}" data-item-id="${esc(item.id)}">
      <button class="backlog-check" onclick="toggleDone(event,'${esc(p.id)}','${esc(item.id)}','${item.status}')" title="${item.status==='done'?'Reopen':'Mark done'}">
        ${item.status==='done' ? '✓' : ''}
      </button>
      <div style="flex:1;min-width:0">
        <span class="backlog-text" contenteditable="true" spellcheck="true"
          onblur="saveBacklogText(event,'${esc(p.id)}','${esc(item.id)}')"
          onkeydown="if(event.key==='Enter'){event.preventDefault();this.blur()}"
        >${esc(item.text)}</span>
        ${activityLine}
      </div>
      <div class="backlog-meta">
        ${agentBadgeHTML}
        <span class="priority-badge priority-${item.priority||'normal'}"
          onclick="cyclePriority(event,'${esc(p.id)}','${esc(item.id)}','${item.priority||'normal'}')"
          title="Click to change priority">${item.priority||'normal'}</span>
        ${item.github_issue_number ? `<a class="gh-issue-link" href="https://github.com/${esc(p.github_repo||'')}/issues/${item.github_issue_number}" target="_blank" rel="noopener" title="View on GitHub">#${item.github_issue_number}</a>` : ''}
        ${item.source && item.source !== 'dashboard' && !isAgent ? `<span class="backlog-source">${esc(item.source)}</span>` : ''}
        ${p.project_path && item.status !== 'done' ? `<button class="backlog-dispatch" onclick="dispatchBacklogItem(event,'${esc(p.id)}','${esc(item.id)}')" title="Send to agent">▶</button>` : ''}
        <button class="att-btn ${attsN > 0 ? 'has-atts' : ''}"
          onclick="toggleAttPanel(event,'${esc(p.id)}','${esc(item.id)}')"
          title="Attachments">📎${attsN > 0 ? ' '+attsN : ''}</button>
        <button class="notes-btn ${notesN > 0 ? 'has-notes' : ''}"
          onclick="toggleNotesPanel(event,'${esc(p.id)}','${esc(item.id)}')"
          title="Notes">📝${notesN > 0 ? ' '+notesN : ''}</button>
        <button class="backlog-del" onclick="deleteBacklogItem(event,'${esc(p.id)}','${esc(item.id)}')" title="Delete">✕</button>
      </div>
      <div class="att-panel${openAttPanels.has(item.id) ? ' open' : ''}" id="att-${esc(item.id)}">
        <div class="att-drop-zone" id="drop-${esc(item.id)}"
          ondragover="attDragOver(event,'${esc(item.id)}')"
          ondragleave="attDragLeave('${esc(item.id)}')"
          ondrop="attDrop(event,'${esc(p.id)}','${esc(item.id)}')"
          onclick="document.getElementById('file-${esc(item.id)}').click()">
          <input type="file" id="file-${esc(item.id)}" multiple
            onchange="attFileSelected(event,'${esc(p.id)}','${esc(item.id)}')">
          Drop files here or click to browse
        </div>
        <div class="att-list" id="attlist-${esc(item.id)}">
          ${(item.attachments||[]).map(a => attHTML(a, p.id, item.id)).join('')}
        </div>
      </div>
      <div class="notes-panel${openNotesPanels.has(item.id) ? ' open' : ''}" id="notes-${esc(item.id)}">
        <div class="notes-list" id="noteslist-${esc(item.id)}">
          ${(item.notes||[]).map(n => noteHTML(n)).join('')}
        </div>
        <div class="note-input-row">
          <input type="text" id="noteinput-${esc(item.id)}" placeholder="Add a note (date + user/agent code auto-prefixed)"
            onkeydown="if(event.key==='Enter'){event.preventDefault();submitNote('${esc(p.id)}','${esc(item.id)}')}">
          <button onclick="submitNote('${esc(p.id)}','${esc(item.id)}')">Add</button>
        </div>
      </div>
    </div>`}).join('');

  const undoBtn = undoStack.length && undoStack[undoStack.length-1].projectId === p.id
    ? `<button class="btn-undo" onclick="performUndo(event)" title="Undo ${undoStack[undoStack.length-1].label}">↩ Undo</button>`
    : '';
  const doneToggle = doneItems.length
    ? `<button class="toggle-done" onclick="toggleShowDone(event,'${esc(p.id)}')">${showDone ? `Hide ${doneItems.length} done` : `Show ${doneItems.length} done`}</button>`
    : '';

  const modalAccent = p.modal_color ? p.modal_color.color : '';
  const accentStyle = modalAccent ? `style="--modal-accent:${modalAccent}"` : '';

  return `
    <div class="modal-header" ${accentStyle}>
      <div class="modal-window-controls">
        <button class="modal-pin" onclick="toggleModalPin('${esc(p.id)}')" title="Collapse / expand data sheet">
          <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden="true">
            <path d="M9 1.2L12.8 5L10 7.8L9.3 7.1L6.7 9.7L4.3 7.3L6.9 4.7L6.2 4L9 1.2Z" stroke="currentColor" stroke-width="1.2" stroke-linejoin="round"/>
            <line x1="6.7" y1="9.7" x2="1.5" y2="12.5" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/>
          </svg>
        </button>
        <button class="modal-menu-btn" onclick="toggleModalMenu(event,'${esc(p.id)}')" title="Menu">&#x22EE;</button>
        <button class="modal-minimize" onclick="minimizeModal('${esc(p.id)}')" title="Minimize">&#x2015;</button>
        <button class="modal-close" onclick="closeModalById('${esc(p.id)}')" title="Close">&#10005;</button>
        <div class="modal-menu-dropdown" id="modal-menu-${esc(p.id)}">
          <div class="mc-tabs-in-menu">
            <button class="modal-menu-item${activeTab==='agent'?' active':''}" onclick="_mcMenuSwitchTab('${esc(p.id)}','agent')">
              <span class="menu-icon">&#x25A0;</span> Agent
            </button>
            <button class="modal-menu-item${activeTab==='backlog'?' active':''}" onclick="_mcMenuSwitchTab('${esc(p.id)}','backlog')">
              <span class="menu-icon">&#x2630;</span> Backlog${openItems.length ? `<span class="tab-badge" style="margin-left:auto">${openItems.length}</span>` : ''}
            </button>
            <button class="modal-menu-item${activeTab==='agent-log'?' active':''}" data-tab-name="agent-log" onclick="_mcMenuSwitchTab('${esc(p.id)}','agent-log')">
              <span class="menu-icon">&#x1F4DC;</span> Agent Log
            </button>
            <button class="modal-menu-item${activeTab==='plans'?' active':''}" onclick="_mcMenuSwitchTab('${esc(p.id)}','plans')">
              <span class="menu-icon">&#x1F4CB;</span> Plans
            </button>
            <button class="modal-menu-item${activeTab==='activity'?' active':''}" onclick="_mcMenuSwitchTab('${esc(p.id)}','activity')">
              <span class="menu-icon">&#x23F1;</span> Activity
            </button>
            <button class="modal-menu-item${activeTab==='workflows'?' active':''}" onclick="_mcMenuSwitchTab('${esc(p.id)}','workflows')">
              <span class="menu-icon">&#x26D3;</span> Workflows
            </button>
            <div class="modal-menu-sep"></div>
          </div>
          <button class="modal-menu-item" onclick="_mcMenuClose();openAllHivemindsForProject('${esc(p.id)}')">
            <span class="menu-icon">&#x1F41D;</span> Hiveminds
          </button>
          <div class="modal-menu-sep"></div>
          <button class="modal-menu-item" onclick="toggleModalMenuSub(event,'status-sub-${esc(p.id)}')">
            <span class="menu-icon">&#x25CF;</span> Change Status <span style="margin-left:auto;color:var(--text-faint);font-size:11px">&#x25B8;</span>
          </button>
          <div class="modal-menu-sub" id="status-sub-${esc(p.id)}">
            <button class="modal-menu-sub-item${(p.status||'')==='active'?' active':''}" onclick="setProjectStatus('${esc(p.id)}','active')">
              <span class="modal-menu-sub-dot" style="background:var(--green)"></span> Active</button>
            <button class="modal-menu-sub-item${(p.status||'')==='waiting'?' active':''}" onclick="setProjectStatus('${esc(p.id)}','waiting')">
              <span class="modal-menu-sub-dot" style="background:var(--amber)"></span> Waiting</button>
            <button class="modal-menu-sub-item${(p.status||'')==='blocked'?' active':''}" onclick="setProjectStatus('${esc(p.id)}','blocked')">
              <span class="modal-menu-sub-dot" style="background:var(--red)"></span> Blocked</button>
            <button class="modal-menu-sub-item${(p.status||'')==='parked'?' active':''}" onclick="setProjectStatus('${esc(p.id)}','parked')">
              <span class="modal-menu-sub-dot" style="background:var(--text-faint)"></span> Parked</button>
          </div>
          <button class="modal-menu-item" onclick="toggleModalMenuSub(event,'appearance-sub-${esc(p.id)}')">
            <span class="menu-icon">&#x1F3A8;</span> Appearance <span style="margin-left:auto;color:var(--text-faint);font-size:11px">&#x25B8;</span>
          </button>
          <div class="modal-menu-sub" id="appearance-sub-${esc(p.id)}" style="min-width:230px">
            <div style="padding:4px 8px 6px">
              <div style="font-size:9px;color:var(--text-faint);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px">Accent color</div>
              <div style="display:flex;gap:6px;flex-wrap:wrap">
                ${COLOR_PRESETS.map(c => {
                  const isCurrent = p.modal_color ? (p.modal_color.color === c.color) : (c.label === 'Blue');
                  return `<button style="width:24px;height:24px;border-radius:4px;border:${isCurrent ? '2px solid var(--text)' : '1px solid var(--border)'};background:${c.bg};cursor:pointer;padding:0;display:flex;align-items:center;justify-content:center"
                    title="${c.label}"
                    onclick="setProjectColor('${esc(p.id)}','${c.color}','${c.bg}')"><span style="width:10px;height:10px;border-radius:2px;background:${c.color}"></span></button>`;
                }).join('')}
              </div>
            </div>
            <div style="border-top:1px solid var(--border);padding-top:4px">
              <div style="font-size:9px;color:var(--text-faint);text-transform:uppercase;letter-spacing:0.5px;padding:2px 8px">Domain</div>
              ${domainsList.map(d => {
                const cfg = getDomainConfig(d.id);
                return `<button class="modal-menu-sub-item${(p.domain||'general')===d.id?' active':''}" onclick="saveDomainFromMenu(event,'${esc(p.id)}','${d.id}')">
                <span class="modal-menu-sub-dot" style="background:${cfg.color}"></span> ${esc(d.label||d.id)}</button>`;
              }).join('')}
            </div>
            <div style="border-top:1px solid var(--border);padding:6px 8px">
              <div style="font-size:9px;color:var(--text-faint);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px">Domain color</div>
              <div style="display:flex;gap:4px;flex-wrap:wrap">
                ${COLOR_PRESETS.map(c =>
                  `<button style="width:18px;height:18px;border-radius:4px;border:1px solid var(--border);background:${c.bg};cursor:pointer;padding:0;display:flex;align-items:center;justify-content:center"
                    title="${c.label}"
                    onclick="setDomainColorFromMenu(event,'${esc(p.id)}','${esc(p.domain||'general')}','${c.color}','${c.bg}')"><span style="width:8px;height:8px;border-radius:2px;background:${c.color}"></span></button>`
                ).join('')}
              </div>
            </div>
            <div style="border-top:1px solid var(--border);padding:4px">
              <input type="text" placeholder="New domain..." style="width:100%;background:var(--bg);border:1px solid var(--border);color:var(--text);font-size:11px;padding:4px 8px;border-radius:4px;outline:none;box-sizing:border-box"
                onclick="event.stopPropagation()"
                onkeydown="if(event.key==='Enter'){event.stopPropagation();addDomainFromMenu(event,'${esc(p.id)}',this.value)}">
            </div>
          </div>
          <button class="modal-menu-item" onclick="_mcMenuClose();openProjectProfileDialog('${esc(p.id)}')">
            <span class="menu-icon">${p.emoji ? esc(p.emoji) : '&#x270E;'}</span> Edit Profile&#8230;
          </button>
          <button class="modal-menu-item" onclick="_mcMenuClose();openAgentSettingsDialog('${esc(p.id)}')">
            <span class="menu-icon">&#x2699;</span> Agent Settings ${p.agent_model ? `<span style="margin-left:4px;color:var(--accent);font-size:11px">${esc(_modelShortLabel(p.agent_model))}</span>` : '<span style="margin-left:4px;color:var(--text-faint);font-size:11px">default</span>'}
          </button>
          ${(() => {
            // Providers not fetched yet — kick a load and refresh this modal
            // when it lands. The composer's provider picker renders in this
            // same pass and has no race handling of its own, so without this
            // it stays hidden after losing the first-open race (common on
            // mobile). The Agent Settings dialog awaits providers itself.
            if (_agentProviders === null) {
              _ensureAgentProviders().then(pl => { if ((pl || []).length > 1) refreshModal(); });
            }
            return '';
          })()}
          <div class="modal-menu-sep"></div>
          <button class="modal-menu-item" onclick="toggleModalMenuSub(event,'gh-sub-${esc(p.id)}')">
            <span class="menu-icon">&#x1F517;</span> GitHub Sync ${p.github_sync_enabled ? `<span style="margin-left:4px;color:var(--green);font-size:11px">&#x2713; ${esc(p.github_repo)}</span>` : '<span style="margin-left:4px;color:var(--text-faint);font-size:11px">not connected</span>'} <span style="margin-left:auto;color:var(--text-faint);font-size:11px">&#x25B8;</span>
          </button>
          <div class="modal-menu-sub" id="gh-sub-${esc(p.id)}" style="min-width:220px">
            ${p.github_sync_enabled ? `
              <div style="padding:6px 10px;font-size:11px;color:var(--text-dim)">
                Syncing with <strong style="color:var(--accent)">${esc(p.github_repo)}</strong>
                ${p.github_last_sync ? `<br><span style="font-size:10px">Last sync: ${esc(timeAgoJS(p.github_last_sync))}</span>` : ''}
              </div>
              <button class="modal-menu-sub-item" onclick="githubSyncNow('${esc(p.id)}')">
                &#x21BB; Sync Now</button>
              <button class="modal-menu-sub-item" style="color:var(--red-text)" onclick="githubDisconnect('${esc(p.id)}')">
                &#x2715; Disconnect</button>
            ` : `
              <div style="padding:6px 10px">
                <input type="text" id="gh-repo-${esc(p.id)}" placeholder="owner/repo" style="width:100%;background:var(--bg);border:1px solid var(--border);color:var(--text);font-size:11px;padding:4px 8px;border-radius:4px;outline:none;box-sizing:border-box"
                  onclick="event.stopPropagation()"
                  onkeydown="if(event.key==='Enter'){event.stopPropagation();githubConnect('${esc(p.id)}')}">
              </div>
              <button class="modal-menu-sub-item" onclick="githubConnect('${esc(p.id)}')">
                &#x2713; Connect</button>
            `}
          </div>
          <button class="modal-menu-item" onclick="toggleModalMenuSub(event,'code-sync-sub-${esc(p.id)}')">
            <span class="menu-icon">&#x1F501;</span> Code Sync ${p.code_sync_enabled ? `<span style="margin-left:4px;color:var(--green);font-size:11px">&#x2713; enabled</span>` : '<span style="margin-left:4px;color:var(--text-faint);font-size:11px">not enabled</span>'} <span style="margin-left:auto;color:var(--text-faint);font-size:11px">&#x25B8;</span>
          </button>
          <div class="modal-menu-sub" id="code-sync-sub-${esc(p.id)}" style="min-width:240px">
            ${p.code_sync_enabled ? `
              <div style="padding:6px 10px;font-size:11px;color:var(--text-dim)">
                ${p.code_sync_status ? `
                  Ahead <strong>${p.code_sync_status.ahead||0}</strong> ·
                  Behind <strong>${p.code_sync_status.behind||0}</strong> ·
                  Incoming <strong>${p.code_sync_status.incoming_count||0}</strong>
                  ${p.code_sync_status.dirty ? '<br><span style="color:var(--accent)">&#9888; working tree dirty</span>' : ''}
                ` : '<em>no status yet — run Sync Now</em>'}
                ${p.code_sync_last_fetch ? `<br><span style="font-size:10px">Last fetch: ${esc(timeAgoJS(p.code_sync_last_fetch))}</span>` : ''}
                ${p.code_sync_last_error ? `<br><span style="color:var(--red-text);font-size:10px">${esc(p.code_sync_last_error)}</span>` : ''}
              </div>
              <button class="modal-menu-sub-item" onclick="codeSyncNow('${esc(p.id)}')">
                &#x21BB; Sync Now</button>
              <button class="modal-menu-sub-item" onclick="codeSyncShowIncoming('${esc(p.id)}')">
                &#x1F4E5; View Incoming${(p.code_sync_status && p.code_sync_status.incoming_count) ? ` (${p.code_sync_status.incoming_count})` : ''}</button>
              <button class="modal-menu-sub-item" style="color:var(--red-text)" onclick="codeSyncDisable('${esc(p.id)}')">
                &#x2715; Disable</button>
            ` : `
              <div style="padding:6px 10px;font-size:11px;color:var(--text-dim)">
                Enables bidirectional code sync via a per-machine sync branch.
                Read-only spike — no auto-commit yet.
              </div>
              <button class="modal-menu-sub-item" onclick="codeSyncEnable('${esc(p.id)}')">
                &#x2713; Enable</button>
            `}
          </div>
          <div class="modal-menu-sep"></div>
          <button class="modal-menu-item mc-adv-memory" onclick="openMemoryModal('${esc(p.id)}')">
            <span class="menu-icon">&#x1F4DD;</span> Memory
          </button>
          <button class="modal-menu-item mc-adv-memory" onclick="openRulesModal('${esc(p.id)}')">
            <span class="menu-icon">&#x2699;</span> Rules
          </button>
          <button class="modal-menu-item" onclick="openAllSkillsForProject('${esc(p.id)}')">
            <span class="menu-icon">&#x1F9E9;</span> Skills
          </button>
          <button class="modal-menu-item" onclick="openAllMCPForProject('${esc(p.id)}')">
            <span class="menu-icon">&#x1F50C;</span> MCP servers
          </button>
          <div class="modal-menu-sep"></div>
          <button class="modal-menu-item danger" onclick="deleteProject('${esc(p.id)}')">
            <span class="menu-icon">&#x1F5D1;</span> Delete Project
          </button>
        </div>
      </div>
      <div class="card-title-row" style="margin-bottom:8px">
        <input class="name-edit" type="text" value="${esc(p.name||p.id)}"
          onblur="saveProjectName('${esc(p.id)}',this)"
          onkeydown="if(event.key==='Enter'){this.blur()}"
          oninput="autoSizeNameInput(this)"
        >
        ${(() => { const cfg = getDomainConfig(p.domain||'general'); return `<span class="domain-tag" style="background:${cfg.bg};color:${cfg.color}">${esc(cfg.label||p.domain||'general')}</span>`; })()}
      </div>
      <div class="modal-status-row" style="display:flex;align-items:center;gap:10px">
        <span class="status-pill friendly-${fs}"><span class="status-dot"></span>${esc(vl(FRIENDLY_TO_VOICE[fs]))}</span>
        <span class="time-ago">${esc(p.last_updated_relative||'')}</span>
      </div>
      <div class="path-row">
        <span class="path-label">Path</span>
        <input class="path-input" type="text" value="${esc(p.project_path||'')}"
          placeholder="No folder selected — click Browse, or type a path"
          onblur="saveProjectPath('${esc(p.id)}',this)"
          onkeydown="if(event.key==='Enter'){this.blur()}"
        >
        <button class="btn-browse" onclick="openFolderPicker('${esc(p.id)}')" title="Browse for folder">Browse...</button>
        ${p.project_path ? `<button class="btn-import" onclick="importFromProject('${esc(p.id)}')" title="Import from CHANGELOG.md">Import</button>` : ''}
      </div>
      ${p.summary ? `<div class="project-summary" style="margin-top:8px">${esc(p.summary)}</div>` : ''}
      ${p.description ? `<div class="description" style="margin-top:8px">${esc(p.description)}</div>` : ''}
    </div>
    <div class="card-summary">
      <div class="summary-item">
        <div class="summary-label">${p.blocked ? 'Blocked — last task' : 'Current task'}</div>
        ${currentTaskHTML}
      </div>
      <div class="summary-item">
        <div class="summary-label">Next up</div>
        <span class="summary-value ${modalLive.nextActionClass}">${esc(modalLive.nextAction)}</span>
      </div>
    </div>
    <div class="modal-tab-bar">
      <div class="modal-tab ${activeTab==='agent'?'active':''}" onclick="switchModalTab('${esc(p.id)}','agent')">Agent</div>
      <div class="modal-tab ${activeTab==='backlog'?'active':''}" onclick="switchModalTab('${esc(p.id)}','backlog')">Backlog${openItems.length ? `<span class="tab-badge">${openItems.length}</span>` : ''}</div>
      <div class="modal-tab ${activeTab==='agent-log'?'active':''}" data-tab-name="agent-log" onclick="switchModalTab('${esc(p.id)}','agent-log')">Agent Log</div>
      <div class="modal-tab ${activeTab==='plans'?'active':''}" onclick="switchModalTab('${esc(p.id)}','plans')">Plans</div>
      <div class="modal-tab ${activeTab==='activity'?'active':''}" onclick="switchModalTab('${esc(p.id)}','activity')">Activity</div>
      <div class="modal-tab ${activeTab==='workflows'?'active':''}" onclick="switchModalTab('${esc(p.id)}','workflows')">Workflows</div>
      ${activeTab !== 'agent' ? `<div class="modal-tab-search">
        <input type="text" id="tab-search-${esc(p.id)}" placeholder="Filter..."
          value="${esc(modalSearchQuery[p.id] || '')}"
          oninput="modalSearchQuery['${esc(p.id)}']=this.value;applyTabFilter('${esc(p.id)}')"
        >${modalSearchQuery[p.id] ? `<span class="search-clear" onclick="clearTabSearch('${esc(p.id)}')">&#x2715;</span>` : ''}
      </div>` : ''}
    </div>
    <div class="modal-scroll-body${activeTab==='agent'?' agent-active':''}">
      <div class="modal-tab-content ${activeTab==='backlog'?'active':''}" data-tab="backlog">
        <div class="card-section">
          <div class="section-title">
            <span>Backlog ${backlogBadge}</span>
            ${p.github_sync_enabled && p.github_repo ? `<button class="gh-sync-badge" id="gh-badge-${esc(p.id)}" onclick="githubSyncNow('${esc(p.id)}')" title="Sync with GitHub">&#x21BB; ${esc(p.github_repo)}</button>` : ''}
            ${undoBtn}${doneToggle}
          </div>
          <div class="backlog-list">${backlogItemsHTML}</div>
          <div class="backlog-add">
            <textarea spellcheck="true" class="backlog-input" id="backlog-input-${esc(p.id)}" rows="2"
              placeholder="Add action item... (paste/drop images here)"
              onkeydown="handleInputEnter(event,()=>addBacklogItem('${esc(p.id)}'),'${esc(p.id)}')"
              onpaste="handleCreatePaste(event,'${esc(p.id)}')"
              ondragover="createDragOver(event,'${esc(p.id)}')"
              ondragleave="createDragLeave('${esc(p.id)}')"
              ondrop="createDrop(event,'${esc(p.id)}')"></textarea>
            <div class="create-previews" id="create-previews-${esc(p.id)}"></div>
            <div style="display:flex;gap:6px;align-items:center">
              <select class="priority-select" id="backlog-pri-${esc(p.id)}">
                <option value="normal">Normal</option>
                <option value="high">High</option>
                <option value="low">Low</option>
              </select>
              <button class="btn-add" onclick="addBacklogItem('${esc(p.id)}')">+ Add</button>
            </div>
          </div>
        </div>
      </div>
      <div class="modal-tab-content ${activeTab==='agent'?'active':''}" data-tab="agent">
        ${agentPanelHTML(p)}
      </div>
      <div class="modal-tab-content ${activeTab==='agent-log'?'active':''}" data-tab="agent-log">
        ${agentLogPanelHTML(p)}
      </div>
      <div class="modal-tab-content ${activeTab==='plans'?'active':''}" data-tab="plans">
        <div class="card-section">
          <div class="section-title">Plans</div>
          <div id="plans-toolbar-${esc(p.id)}" style="display:none"></div>
          <div id="plans-list-${esc(p.id)}"><div style="color:var(--text-faint);font-style:italic">Loading...</div></div>
        </div>
      </div>
      <div class="modal-tab-content ${activeTab==='activity'?'active':''}" data-tab="activity">
        <div class="card-section">
          <div class="section-title">Activity Log</div>
          <div class="log-entries">${logHTML || '<div style="color:var(--text-faint);font-style:italic">No activity yet</div>'}</div>
        </div>
      </div>
      <div class="modal-tab-content ${activeTab==='workflows'?'active':''}" data-tab="workflows">
        <div class="card-section">
          <div class="section-title">Workflows</div>
          <div id="workflows-body-${esc(p.id)}"><div style="color:var(--text-faint);font-style:italic">Loading...</div></div>
        </div>
      </div>
    </div>`;
}

// ── List View Rendering ─────────────────────────────────────────────────────
function renderListView() {
  const listEl = document.getElementById('list-view');
  if (!listEl) return;
  const filtered = filterProjects();
  if (!filtered.length) { listEl.innerHTML = '<div class="loading">No projects match filter</div>'; return; }

  const header = `<div class="list-header">
    <span></span><span>Project</span><span>Status</span><span>Current Task</span><span>Next Up</span><span>Agent</span><span style="text-align:right">Updated</span>
  </div>`;

  const rows = filtered.map(p => listRowHTML(p)).join('');
  listEl.innerHTML = header + rows;

  listEl.querySelectorAll('.list-row').forEach(row => {
    row.addEventListener('click', () => openProjectModal(row.dataset.id));
  });
}

function listRowHTML(p) {
  const live = computeLiveStatus(p.id);
  const fs = friendlyStatus(p);
  const friendlyColors = { working: 'var(--green)', asking: 'var(--amber)', stuck: 'var(--red)', done: 'var(--green)', idle: 'var(--text-faint)' };
  const friendlyBgs    = { working: 'var(--green-dim)', asking: 'var(--amber-dim)', stuck: 'var(--red-dim)', done: 'var(--green-dim)', idle: 'var(--surface3)' };
  const friendlyTexts  = { working: 'var(--green-text)', asking: 'var(--amber-text)', stuck: 'var(--red-text)', done: 'var(--green-text)', idle: 'var(--text-faint)' };
  // "Running" here means actually mid-turn (live.currentTaskClass==='running'),
  // NOT merely "a process is alive": an idle-between-turns session must not
  // read as Running, or this column contradicts the consolidated status.
  const isRunning = live.currentTaskClass === 'running';
  const errSess = agentHistory.find(h => h.projectId === p.id && h.status === 'error');
  let agentLabel = '\u2014', agentClass = '';
  if (isRunning) { agentLabel = 'Running'; agentClass = 'running'; }
  else if (errSess) { agentLabel = 'Error'; agentClass = 'error'; }

  return `<div class="list-row" data-id="${esc(p.id)}">
    <div class="lr-indicator" style="background:${friendlyColors[fs]}"></div>
    <div class="lr-name">${esc(p.name || p.id)}</div>
    <div class="lr-status" style="background:${friendlyBgs[fs]};color:${friendlyTexts[fs]}">${esc(vl(FRIENDLY_TO_VOICE[fs]))}</div>
    <div class="lr-task ${live.currentTaskClass}">${esc(live.currentTask)}</div>
    <div class="lr-next">${esc(live.nextAction)}</div>
    <div class="lr-agent ${agentClass}">${agentLabel}</div>
    <div class="lr-updated">${esc(p.last_updated_relative || '')}</div>
  </div>`;
}


// ── interop: window re-exposure for inline/generated/cross-module callers ──
window.computeLiveStatus = computeLiveStatus;
window.friendlyStatus = friendlyStatus;
window.friendlySummary = friendlySummary;
window.tileHTML = tileHTML;
window.modalContentHTML = modalContentHTML;
window.renderListView = renderListView;
