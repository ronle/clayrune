// ── Agent Panel ─────────────────────────────────────────────────────────────

// Tag agent-bound POST bodies with client="mobile" when the viewport is
// phone-sized. The backend then decides — based on mobile_brief_replies_enabled
// — whether to prepend a Telegram-style directive to the message that gets
// piped to claude stdin. The original message still goes into log_lines so
// the user's chat bubble shows what they typed.
function _maybeTagMobileClient(body) {
  if (window.innerWidth <= 960) body.client = 'mobile';
  return body;
}

let exitPlanModeCount = {};    // session_id → number of consecutive ExitPlanMode calls
let pendingDispatchProvider = {};  // project_id → provider chosen in the new-chat composer (per-conversation, not per-project)
let pendingDispatchCharacter = {}; // project_id → "scope:name" persona chosen in the new-chat composer (Prompt Builder Phase 2)
let characterCache = {};           // project_id → array of available characters (lazy-loaded for the composer picker)
let characterCacheLoading = {};    // project_id → true while a fetch is in flight (dedupe)

// Lazy-load a project's characters (project pool + globals) for the new-chat
// composer picker. Re-renders once when the list arrives. Best-effort: a
// failure just leaves the picker absent (no persona = today's behavior).
function _ensureCharacters(projectId) {
  if (characterCache[projectId] || characterCacheLoading[projectId]) return;
  characterCacheLoading[projectId] = true;
  fetch(API_BASE + '/api/characters?project_id=' + encodeURIComponent(projectId))
    .then(r => r.json())
    .then(list => {
      characterCache[projectId] = Array.isArray(list) ? list : [];
      characterCacheLoading[projectId] = false;
      if (characterCache[projectId].length) refreshModalById(projectId);
    })
    .catch(() => { characterCache[projectId] = []; characterCacheLoading[projectId] = false; });
}

// Character/persona dropdown for the +New composer. Returns '' (no picker)
// on resume (persona is fixed at spawn) or when the project has no
// characters. Default selection = none = today's plain-agent behavior.
function _composerCharacterPicker(p, resumeId) {
  if (!p || resumeId) return '';
  _ensureCharacters(p.id);
  const list = characterCache[p.id] || [];
  if (!list.length) return '';
  const cur = pendingDispatchCharacter[p.id] || '';
  const opts = list.map(c => {
    const val = esc((c.scope || 'global') + ':' + c.name);
    const label = esc(c.display_name || c.name) + (c.scope === 'global' ? ' (global)' : '');
    return `<option value="${val}" ${val === cur ? 'selected' : ''}>${label}</option>`;
  }).join('');
  return `<div class="composer-provider-row composer-character-row">
    <span class="composer-provider-label">Persona</span>
    <select class="composer-provider-select" onchange="setComposerCharacter('${esc(p.id)}',this.value)">
      <option value="">None</option>${opts}
    </select>
  </div>`;
}

function setComposerCharacter(projectId, character) {
  pendingDispatchCharacter[projectId] = character;
  refreshModalById(projectId);
}

// Cross-module accessors for the per-chat persona state. These vars are
// module-scoped to conversation.js; resume-preview.js (the dispatch path) must
// reach them through these window-exposed helpers — NOT by touching the vars
// directly, which throws a ReferenceError across the ES-module boundary and
// aborts every dispatch (the "new/old chats immediately STOPPED" regression).
// Mirrors how provider state is shared via _composerProvider().
function getPendingCharacter(projectId) {
  return pendingDispatchCharacter[projectId] || '';
}
function clearPendingCharacter(projectId) {
  delete pendingDispatchCharacter[projectId];
}
function resolveCharacterMeta(projectId, character) {
  if (!character) return null;
  const i = character.indexOf(':');
  const scope = character.slice(0, i), name = character.slice(i + 1);
  const rec = (characterCache[projectId] || [])
    .find(c => c.name === name && (c.scope || 'global') === scope);
  return { name, scope, display_name: (rec && (rec.display_name || rec.name)) || name };
}
window.setComposerCharacter = setComposerCharacter;
window.getPendingCharacter = getPendingCharacter;
window.clearPendingCharacter = clearPendingCharacter;
window.resolveCharacterMeta = resolveCharacterMeta;

// Provider is bound per-conversation. The new-chat composer picks it; the
// project's `provider` field (set via three-dot menu) is only the default seed.
function _composerProvider(p) {
  if (!p) return 'claude';
  return pendingDispatchProvider[p.id]
      || p.provider
      || (_globalConfig && _globalConfig.default_provider)
      || 'claude';
}

// Provider dropdown for the +New composer. Returns '' (no picker) when only
// claude is available — claude-only deployments stay pixel-identical.
function _composerProviderPicker(p) {
  const provs = (_agentProviders || []).filter(x => x.installed);
  if (provs.length <= 1) return '';
  const cur = _composerProvider(p);
  const opts = provs.map(x =>
    `<option value="${esc(x.name)}" ${x.name === cur ? 'selected' : ''}>${esc(x.display_name)}</option>`
  ).join('');
  return `<div class="composer-provider-row">
    <span class="composer-provider-label">Agent</span>
    <select class="composer-provider-select" onchange="setComposerProvider('${esc(p.id)}',this.value)">${opts}</select>
  </div>`;
}

function setComposerProvider(projectId, provider) {
  pendingDispatchProvider[projectId] = provider;
  // Caps changed (resume / image-input / etc.) — re-render so the composer
  // reflects the chosen provider's affordances.
  refreshModal();
}

function getIncognitoFor(projectId) {
  // Global incognito project is always incognito; user can't turn it off.
  if (projectId === '_incognito') return true;
  return !!incognitoToggle[projectId];
}

function toggleIncognito(projectId) {
  if (projectId === '_incognito') return;  // forced on
  incognitoToggle[projectId] = !incognitoToggle[projectId];
  refreshModalById(projectId);
}

function isHivemindWorker(h) {
  return !!(h.hivemindWsId || (agentStatusCache[h.sessionId] || {}).hivemindWsId);
}
function isHivemindOrchestrator(h) {
  return (h.hivemindRole === 'orchestrator') || ((agentStatusCache[h.sessionId] || {}).hivemindRole === 'orchestrator');
}

function getProjectSessions(projectId) {
  return agentHistory.filter(h => h.projectId === projectId && !isHivemindWorker(h));
}

// Tabs visible in the per-project agent strip. Filters out automated runs that
// have already finished — they're available in the Scheduler's Runs panel
// (and Agent Log tab), so showing them as tabs forever just buries the tabs
// the user actually cares about. Active automated runs DO stay visible so a
// "Run Now" or live schedule fire is watchable in the strip.
function getProjectTabSessions(projectId) {
  const TERMINAL = new Set(['completed', 'stopped', 'error']);
  const HIDDEN_TRIGGERS = new Set(['schedule', 'hivemind_worker']);
  return getProjectSessions(projectId).filter(h => {
    if (!HIDDEN_TRIGGERS.has(h.triggerType)) return true;  // manual + orchestrator always visible
    return !TERMINAL.has(h.status);  // automated: only while active
  });
}

// ── Hivemind worker popover ─────────────────────────────────────────────────
let hmPopoverTimer = null;
let hmPopoverSessionId = null;

function showHmWorkerPopover(event, sessionId) {
  cancelHideHmPopover();
  const cached = agentStatusCache[sessionId];
  if (!cached || !cached.hivemindId) return;
  const popover = document.getElementById('hm-worker-popover');
  const tab = event.currentTarget;
  const rect = tab.getBoundingClientRect();
  popover.style.left = Math.max(0, rect.left) + 'px';
  popover.style.top = (rect.bottom + 4) + 'px';
  popover.classList.remove('hidden');
  hmPopoverSessionId = sessionId;

  // Get workers from agentHistory matching this hivemind
  const hmId = cached.hivemindId;
  const workers = agentHistory.filter(h => {
    const c = agentStatusCache[h.sessionId] || h;
    return (c.hivemindId || h.hivemindId) === hmId && (c.hivemindWsId || h.hivemindWsId);
  });

  // Enrich with workstream titles from hivemindCache
  let wsTitleMap = {};
  for (const pid of Object.keys(hivemindCache)) {
    const hms = (hivemindCache[pid] || {}).hiveminds || [];
    const hm = hms.find(h => h.id === hmId);
    if (hm && hm._workstreams) {
      hm._workstreams.forEach(w => { wsTitleMap[w.id] = w.title; });
      break;
    }
  }

  if (workers.length === 0) {
    popover.innerHTML = `<div class="hm-pop-header">&#x2B21; Hivemind Workers</div><div class="hm-pop-empty">No workers spawned yet</div>`;
  } else {
    const rows = workers.map(w => {
      const wsId = w.hivemindWsId || (agentStatusCache[w.sessionId] || {}).hivemindWsId || '';
      const wsTitle = wsTitleMap[wsId] || wsId;
      const wProv = w.provider || (agentStatusCache[w.sessionId] || {}).provider || 'claude';
      const wModel = w.model || (agentStatusCache[w.sessionId] || {}).model || '';
      return `<div class="hm-pop-worker" onclick="if(typeof openHivemindDashboard==='function'){openHivemindDashboard('${esc(hmId)}','${esc(wsId)}');}">
        <span class="agent-status-dot ${w.status}"></span>
        <span class="hm-pop-ws-title">${esc(wsTitle)}</span>
        ${_providerBadge(wProv)}
        ${wModel ? `<span class="hm-pop-ws-meta">${esc(wModel)}</span>` : ''}
        <span class="hm-pop-ws-status">${esc(w.status)}</span>
      </div>`;
    }).join('');
    popover.innerHTML = `<div class="hm-pop-header">&#x2B21; Hivemind Workers (${workers.length})</div>${rows}`;
  }
}
function scheduleHideHmPopover() {
  hmPopoverTimer = setTimeout(() => {
    const el = document.getElementById('hm-worker-popover');
    if (el) el.classList.add('hidden');
    hmPopoverSessionId = null;
  }, 300);
}
function cancelHideHmPopover() {
  if (hmPopoverTimer) { clearTimeout(hmPopoverTimer); hmPopoverTimer = null; }
}


function agentPanelHTML(p) {
  const pp = p.project_path || '';
  if (!pp) {
    return `<div class="agent-panel">
      <div class="section-title">Agent</div>
      <div class="agent-disabled">Set project_path to enable agent dispatch</div>
    </div>`;
  }
  // Visible tab list: hides completed schedule/hivemind-worker runs since
  // those are surfaced in the Scheduler's Runs panel + Agent Log tab.
  const sessions = getProjectTabSessions(p.id);
  // Sort newest-first so the most recent conversation leads.
  sessions.sort((a, b) => (b.startedAt || '').localeCompare(a.startedAt || ''));

  // ── Conversation model ──────────────────────────────────────────────────
  // MOBILE (≤960px): WhatsApp-Communities drill-down — >1 convo shows a
  //   vertical list (tap to open, back bar returns), 1 → direct chat,
  //   0 → dispatch. Space-constrained, so one thing at a time.
  // DESKTOP: classic horizontal tab strip — every conversation is a tab,
  //   the whole modal is visible at once, no drill-down. There's room, so
  //   forcing a list/back dance just adds clicks (the regression reported).
  const mobileMode = isMobileChatList();
  const multi = sessions.length > 1;
  const wantNew = agentConvNew[p.id] === true;
  // Drop a stale selection (session closed/ended elsewhere) so it can't
  // blank the panel or pin us off the list.
  if (activeAgentTab[p.id] && !sessions.some(s => s.sessionId === activeAgentTab[p.id])) {
    delete activeAgentTab[p.id];
  }
  // Auto-select a tab when none is active:
  //  • desktop → classic: most-recent running/idle (else newest) so a tab
  //    is always open and the strip behaves like before the drill-down.
  //  • mobile  → only the lone session (single = direct chat); multi stays
  //    unselected so the drill-down list is the home view.
  if (!wantNew && !activeAgentTab[p.id] && sessions.length) {
    if (!mobileMode) {
      const running = sessions.find(s => s.status === 'running' || s.status === 'idle');
      activeAgentTab[p.id] = (running || sessions[0]).sessionId;
    } else if (!multi) {
      activeAgentTab[p.id] = sessions[0].sessionId;
    }
  }
  const activeSessionId = wantNew ? null : (activeAgentTab[p.id] || null);
  const activeSession = activeSessionId ? agentStatusCache[activeSessionId] : null;
  const st = activeSession ? (activeSession.status || 'idle') : 'idle';
  const isRunning = st === 'running';

  // View chrome: mobile = drill-down list + back bar; desktop = tab strip.
  let convListHTML = '', convBackBar = '', tabBar = '';
  if (mobileMode) {
    const showConvList = multi && !activeSession && !wantNew;
    convListHTML = showConvList ? conversationListHTML(p, sessions) : '';
    const showBackBar = (multi && activeSession) || (wantNew && sessions.length > 0);
    const backLabel = multi ? '&#8592; All conversations' : '&#8592; Back';
    convBackBar = showBackBar
      ? `<div class="conv-back-bar">
          <button class="conv-back-btn" onclick="mcBackFromConv('${esc(p.id)}')">${backLabel}</button>
          ${multi ? `<span class="conv-list-count">${sessions.length} conversations</span>` : ''}
        </div>`
      : '';
  } else {
    // Desktop tab strip: oldest conversation on the left, newest on the right
    // (append-on-right). `sessions` stays newest-first for auto-select + mobile
    // list semantics; we only flip the render order here. PINNED conversations
    // (chat-level, keyed on the durable claude_session_id) sort to the FRONT and
    // carry a pin marker — each chat is independently pinnable, not the project.
    const pinnedList = p.pinned_conversations || [];
    // Prefer the server-authoritative per-session `pinned` flag (set by
    // fetchAgentStatus); fall back to claude_session_id ∈ project list when the
    // agent status hasn't been fetched yet for this session.
    const _isPinnedConv = h => {
      const cache = agentStatusCache[h.sessionId] || {};
      if (typeof cache.pinned === 'boolean') return cache.pinned;
      const c = cache.claudeSessionId || '';
      return !!c && pinnedList.includes(c);
    };
    const _ordered = sessions.slice().reverse();
    const renderTab = h => {
      const isActive = h.sessionId === activeSessionId;
      const label = (h.task || '').substring(0, 30) || 'Session';
      const isOrch = isHivemindOrchestrator(h);
      const hmBadge = isOrch ? '<span class="hm-tab-badge" title="Hivemind orchestrator">&#x2B21;</span>' : '';
      const isInc = h.incognito || (agentStatusCache[h.sessionId] || {}).incognito;
      const incBadge = isInc ? '<span class="agent-tab-incognito" title="Incognito session — not saved to project memory or agent log">&#x1F576;&#xFE0F;</span>' : '';
      const isPinnedConv = _isPinnedConv(h);
      const pinMark = `<button class="agent-tab-pin-mark${isPinnedConv ? ' pinned' : ''}" onclick="event.stopPropagation();togglePinConversationSession('${esc(p.id)}','${esc(h.sessionId)}')" title="${isPinnedConv ? 'Unpin this chat' : 'Pin this chat'}" aria-label="${isPinnedConv ? 'Unpin this chat' : 'Pin this chat'}" aria-pressed="${isPinnedConv ? 'true' : 'false'}">&#x1F4CC;</button>`;
      return `<div class="agent-tab ${isActive ? 'active' : ''} ${isOrch ? 'hivemind-orch' : ''} ${isInc ? 'incognito' : ''} ${isPinnedConv ? 'pinned-conv' : ''}" onclick="switchAgentTab('${esc(p.id)}','${esc(h.sessionId)}')" title="${esc(h.task)}${isInc ? ' (incognito)' : ''}"${isOrch ? ` onmouseenter="showHmWorkerPopover(event,'${esc(h.sessionId)}')" onmouseleave="scheduleHideHmPopover()"` : ''}>
        ${hmBadge}${incBadge}<span class="agent-status-dot ${h.status}"></span>
        <span class="agent-tab-label">${esc(label)}</span>
        ${pinMark}
        <button class="agent-tab-close" onclick="event.stopPropagation();closeAgentTab('${esc(p.id)}','${esc(h.sessionId)}')" title="Close tab">&#10005;</button>
      </div>`;
    };
    const tabsHTML = [..._ordered.filter(_isPinnedConv), ..._ordered.filter(h => !_isPinnedConv(h))]
      .map(renderTab).join('');
    tabBar = sessions.length > 0
      ? `<div class="agent-tab-bar">${tabsHTML}<button class="agent-tab-new" onclick="newAgentTab('${esc(p.id)}')">+ New</button></div>`
      : '';
  }

  // Dispatch / session-picker screen.
  //  • mobile: only with 0 conversations or an explicit "New" — NOT when the
  //    drill-down list is showing (the list has its own "New" entry point).
  //  • desktop: classic — whenever no tab is active (the tab strip stays
  //    visible above it so you can switch/start from there).
  const noActiveTab = mobileMode
    ? ((sessions.length === 0) || wantNew)
    : (!activeSession || !activeSessionId);
  if (noActiveTab) {
    // Auto-load agent log and set default resume on first render
    if (!agentLogCache[p.id]) {
      loadAgentLog(p.id);
    } else if (!(p.id in pendingResumeId)) {
      pendingResumeId[p.id] = getDefaultResumeId(p.id);
    }
    // Also load .jsonl-derived conversation list (captures interrupted sessions)
    if (!conversationsCache[p.id]) {
      loadConversations(p.id);
    }
  }
  // Provider capability gate — all defaults match claude (safe for existing
  // callers). An active session is bound to the provider it began with; the
  // +New screen reflects the provider chosen in the composer dropdown.
  const _pcaps = activeSession
    ? _getProviderCaps(activeSession.provider || p.provider || 'claude')
    : _getProviderCaps(_composerProvider(p));
  const picker = (noActiveTab && _pcaps.supports_session_resume) ? sessionPickerHTML(p.id) : '';

  // Resume indicator (shown when a prior session is selected AND provider supports resume)
  const resumeId = (_pcaps.supports_session_resume && pendingResumeId[p.id]) || null;
  const resumeIndicator = (noActiveTab && resumeId) ? (() => {
    const convos = conversationsCache[p.id] || [];
    const convo = convos.find(c => c.claude_session_id === resumeId);
    let label;
    if (convo) {
      label = (convo.label || convo.last_user || convo.first_user || '').substring(0, 60);
    } else {
      const entries = agentLogCache[p.id] || [];
      const entry = entries.find(e => e.claude_session_id === resumeId);
      label = entry ? (entry.task || '').substring(0, 50) : resumeId.substring(0, 12);
    }
    return `<div class="resume-indicator">Resuming: ${esc(label)} <span class="ri-clear" onclick="selectResumeSession('${esc(p.id)}','')">clear</span></div>`;
  })() : '';

  // Dispatch row (only shown on the +New screen, not when viewing an active session)
  const dispatchPreviews = noActiveTab ? renderAgentImagePreviews(p.id) : '';
  // Search-past-chats box + the bottom pane that fills the dead space below the
  // composer: search results when a query is active, else the inline preview of
  // the selected resume conversation. Same gate as the picker.
  const showResume = noActiveTab && _pcaps.supports_session_resume;
  const chatSearch = showResume ? chatSearchHTML(p.id) : '';
  const searchPane = showResume ? searchPaneInner(p.id) : '';
  const incOn = getIncognitoFor(p.id);
  const incForced = isIncognitoProject(p);
  const incognitoChip = noActiveTab ? `<div class="incognito-toggle ${incOn ? 'on' : ''} ${incForced ? 'forced' : ''}"
      onclick="${incForced ? '' : `toggleIncognito('${esc(p.id)}')`}"
      title="${incForced ? 'This is the global Incognito agent — sessions here are always incognito.' : (incOn ? 'Incognito ON: agent has full project context, but the session is not logged and nothing is appended to MEMORY.md.' : 'Incognito OFF: standard project context applies.')}">
      <span class="inc-mark">&#x1F576;&#xFE0F;</span>
      <span class="inc-label">Incognito${incForced ? '' : (incOn ? ' on' : '')}</span>
    </div>` : '';
  const _dispatchPlaceholder = incOn ? 'Incognito — ephemeral, not saved to project memory...' : 'Describe a task for the agent... (paste or drop files here)';
  const _attachBtn = _pcaps.image_input ? `
    <input type="file" multiple id="agent-attach-input-${esc(p.id)}" class="agent-attach-input"
      onchange="handleAgentAttachPick(event,'${esc(p.id)}')">
    <button class="btn-attach" type="button" title="Attach files or take a photo"
      onclick="triggerAgentAttach('${esc(p.id)}')">&#128206;</button>` : '';
  const _dispatchMicBtn = micBtnHTML(`agent-task-${esc(p.id)}`);
  const dispatchRow = noActiveTab ? `${chatSearch}${picker}${resumeIndicator}<div class="agent-input-row agent-drop-zone"
    ondragover="handleAgentDragOver(event,this)"
    ondragenter="handleAgentDragOver(event,this)"
    ondragleave="handleAgentDragLeave(event,this)"
    ondrop="${_pcaps.image_input ? `handleAgentDrop(event,'${esc(p.id)}')` : 'event.preventDefault()'}">
    <textarea spellcheck="true" class="agent-task-input" id="agent-task-${esc(p.id)}" rows="1"
      placeholder="${_dispatchPlaceholder}"
      onkeydown="handleInputEnter(event,()=>dispatchAgent('${esc(p.id)}'),'${esc(p.id)}')"
      onpaste="${_pcaps.image_input ? `handleAgentPaste(event,'${esc(p.id)}')` : ''}"
    ></textarea>
    ${_attachBtn}
    ${_dispatchMicBtn}
    <button class="btn-dispatch" onclick="dispatchAgent('${esc(p.id)}')">${resumeId ? 'Continue' : 'Dispatch'}</button>
  </div>
  <div class="composer-controls-row">${_composerProviderPicker(p)}${_composerCharacterPicker(p, resumeId)}${incognitoChip}</div>
  ${dispatchPreviews}<div class="agent-search-pane" id="agent-search-pane-${esc(p.id)}">${searchPane}</div>` : '';

  // Active tab content
  let tabContent = '';
  if (activeSession && activeSessionId) {
    const MAX_RENDER_LINES = 500;
    const outputLines = _skipAgentOutput ? '' : (() => {
      const fullBuf = (agentOutputBuffers[activeSessionId] || []).flatMap(l => l.trimStart().startsWith('> ') ? [l] : l.split('\n'));
      const forceAll = expandedOutputSessions.has(activeSessionId);
      const truncated = !forceAll && fullBuf.length > MAX_RENDER_LINES;
      const buf = truncated ? fullBuf.slice(-MAX_RENDER_LINES) : fullBuf;
      let result = '';
      let tableLines = [];
      let planBlock = '';   // accumulates non-tool lines for plan detection
      let planRawLines = []; // raw text for plan viewer
      let mermaidLines = null;  // null when not inside a ```mermaid block; array otherwise
      function flushTable() {
        if (tableLines.length === 0) return;
        if (isPipeTable(tableLines)) {
          planBlock += `<div class="hl-table">${buildPipeTable(tableLines)}</div>`;
        } else {
          planBlock += `<div class="hl-table-pre">${tableLines.map(l => formatTableLine(esc(l))).join('\n')}</div>`;
        }
        tableLines = [];
      }
      for (const line of buf) {
        // Mermaid block detection: ```mermaid ... ``` becomes a placeholder
        // div that's later rendered by _renderAllMermaidPlaceholders.
        if (mermaidLines === null && /^\s*```\s*mermaid\b/.test(line)) {
          flushTable();
          mermaidLines = [];
          planRawLines.push(line);
          continue;
        }
        if (mermaidLines !== null) {
          if (/^\s*```\s*$/.test(line)) {
            planBlock += _mermaidPlaceholderHTML(mermaidLines.join('\n'));
            planRawLines.push(line);
            mermaidLines = null;
          } else {
            mermaidLines.push(line);
            planRawLines.push(line);
          }
          continue;
        }
        // Plan detection: when ExitPlanMode is hit, collapse prior non-tool lines
        if (line.trim() === '[tool: ExitPlanMode]') {
          flushTable();
          if (planRawLines.length >= 2) {
            planViewerContent[activeSessionId] = planRawLines;
            result += `<button class="plan-show-btn" onclick="openPlanViewer('${esc(activeSessionId)}')">&#128196; Show Plan</button>`;
            result += `<div class="plan-hidden-block">${planBlock}</div>`;
          } else {
            result += planBlock;
          }
          planBlock = ''; planRawLines = [];
          const cls = agentLineCls(line);
          result += `<div class="${cls}">${esc(line)}</div>`;
          continue;
        }
        if (isTableLine(line)) {
          tableLines.push(line);
          planRawLines.push(line);
        } else if (tableLines.length > 0 && line.trim() === '') {
          tableLines.push(line);
          planRawLines.push(line);
        } else {
          flushTable();
          const cls = agentLineCls(line);
          // Prompts get the image-aware escaper so an attached "[Screenshot:
          // C:\path\file.jpg]" renders as a thumbnail instead of literal text.
          // Tool / error / status / queued lines stay on plain esc — we don't
          // want path-like substrings in tool traces to render images.
          const html = cls.includes('agent-line-prompt')
            ? escPromptWithImages(line)
            : (cls.includes('agent-line-tool') || cls.includes('agent-line-error') || cls.includes('agent-line-followup') || cls.includes('agent-line-queued')
                ? esc(line) : formatAgentText(line));
          const div = `<div class="${cls}">${html}</div>`;
          // Tool lines and user prompts reset plan-block accumulator
          if (cls.includes('agent-line-tool') || cls.includes('agent-line-prompt')) {
            result += planBlock + div;
            planBlock = ''; planRawLines = [];
          } else {
            planBlock += div;
            planRawLines.push(line);
          }
        }
      }
      flushTable();
      result += planBlock; // flush any remaining non-plan text
      if (truncated) {
        result = `<div class="agent-line" style="text-align:center;padding:8px;opacity:0.6;cursor:pointer" onclick="expandAgentOutput('${esc(activeSessionId)}')">&#x25B2; ${fullBuf.length - MAX_RENDER_LINES} earlier lines — click to load all &#x25B2;</div>` + result;
      }
      return result;
    })();

    const stopBtn = (isRunning || st === 'idle' || st === 'error')
      ? `<button class="btn-stop" onclick="stopAgent('${esc(p.id)}','${esc(activeSessionId)}')">Stop</button>` : '';

    // §4 cold-render: derive the typing indicator declaratively from run-state
    // so it survives a refreshModal rebuild (turn_start/turn_complete skip
    // refreshModal, but switchAgentTab / modal reopen DO rebuild). Suppressed
    // while parked on a plan/question — those states aren't "generating".
    const showTyping = isRunning && !(activeSession?.waitingForPlanApproval || activeSession?.waitingForQuestion);
    const typingHTML = showTyping
      ? `<div class="agent-line typing-indicator" id="typing-${esc(activeSessionId)}"><span></span><span></span><span></span></div>` : '';

    // Same vocabulary AND same detected-wait distinction as the project
    // badge (see consoleStatusLabel) — activeSession carries the pending
    // plan/question flags that gate "Awaiting input".
    const consoleStatusLabelText = consoleStatusLabel(st, activeSession);

    const followupPreviews = renderAgentImagePreviews('fu_' + activeSessionId);
    const guardianState = activeSession?.guardianState;
    const cbTripped = activeSession?.circuitBreakerTripped;
    const guardianBanner = cbTripped
      ? `<div class="guardian-banner">
          <span>\u26A0 Auto-recovery exhausted after repeated failures.</span>
          <button class="btn-retry" onclick="guardianReset('${esc(p.id)}','${esc(activeSessionId)}','retry')">Try Again</button>
          <button class="btn-fresh" onclick="dispatchAgent('${esc(p.id)}')">Start Fresh</button>
        </div>`
      : guardianState === 'recovering'
        ? `<div class="guardian-banner"><span>\u23F3 Guardian is recovering the session...</span></div>`
        : guardianState === 'needs_attention'
          ? `<div class="guardian-banner">
              <span>\u26A0 Session needs attention.</span>
              <button class="btn-retry" onclick="guardianReset('${esc(p.id)}','${esc(activeSessionId)}','retry')">Retry</button>
              <button class="btn-fresh" onclick="guardianReset('${esc(p.id)}','${esc(activeSessionId)}','dismiss')">Dismiss</button>
            </div>`
          : '';
    const _fuAttachBtn = _pcaps.image_input ? `
            <input type="file" multiple id="agent-attach-input-fu_${esc(activeSessionId)}" class="agent-attach-input"
              onchange="handleAgentAttachPick(event,'fu_${esc(activeSessionId)}')">
            <button class="btn-attach" type="button" title="Attach files or take a photo"
              onclick="triggerAgentAttach('fu_${esc(activeSessionId)}')">&#128206;</button>` : '';
    const _fuMicBtn = micBtnHTML(`agent-followup-${esc(activeSessionId)}`);
    const chatInput = (st === 'running' || st === 'completed' || st === 'stopped' || st === 'idle' || st === 'error')
      ? `${guardianBanner}<div class="agent-chat-input">
          ${followupPreviews}
          <div class="agent-chat-input-row agent-drop-zone"
              ondragover="handleAgentDragOver(event,this)"
              ondragenter="handleAgentDragOver(event,this)"
              ondragleave="handleAgentDragLeave(event,this)"
              ondrop="${_pcaps.image_input ? `handleAgentDrop(event,'fu_${esc(activeSessionId)}')` : 'event.preventDefault()'}">
            <textarea spellcheck="true" class="agent-task-input" id="agent-followup-${esc(activeSessionId)}" rows="1"
              placeholder="${st === 'error' ? 'Type to continue from where it stopped...' : st === 'stopped' ? 'Type to resume conversation...' : st === 'running' ? 'Interrupt and redirect agent... (Enter to send)' : 'Send follow-up... (paste or drop files here)'}"
              onkeydown="handleInputEnter(event,()=>sendFollowup('${esc(p.id)}','${esc(activeSessionId)}'),'${esc(p.id)}')"
              onpaste="${_pcaps.image_input ? `handleAgentPaste(event,'fu_${esc(activeSessionId)}')` : ''}"
            ></textarea>
            ${_fuAttachBtn}
            ${_fuMicBtn}
            <button class="btn-dispatch" onclick="sendFollowup('${esc(p.id)}','${esc(activeSessionId)}')">Send</button>
          </div>
        </div>` : '';

    const planFileBtn = (_pcaps.supports_plan_mode && activeSession && activeSession.planFile)
      ? (() => {
          const fname = activeSession.planFile.split(/[/\\]/).pop();
          const label = planFileTitle[activeSessionId] || planFileLabel(activeSession.task || fname);
          // Lazy-fetch heading if not cached yet
          if (!planFileTitle[activeSessionId]) {
            fetch(API_BASE + `/api/project/${esc(p.id)}/agent/plan-file?session=${esc(activeSessionId)}`)
              .then(r => r.ok ? r.json() : null)
              .then(d => {
                if (d && d.content) {
                  const m = d.content.match(/^#\s+(.+)/m);
                  if (m) {
                    let t = m[1].trim();
                    if (t.length > 40) t = t.slice(0, 37) + '...';
                    planFileTitle[activeSessionId] = t;
                    const c = document.getElementById(`plan-file-btn-${activeSessionId}`);
                    if (c) c.querySelector('.btn-plan-file')?.childNodes?.forEach?.((n, i) => {
                      if (i > 0) n.textContent = ' ' + t;
                    });
                    if (c && c.querySelector('.btn-plan-file')) {
                      c.querySelector('.btn-plan-file').innerHTML = `&#128196; ${esc(t)}`;
                    }
                  }
                }
              }).catch(() => {});
          }
          return `<button class="btn-plan-file" onclick="openPlanFileViewer('${esc(p.id)}','${esc(activeSessionId)}')" title="${esc(fname)}">&#128196; ${esc(label)}</button>`;
        })()
      : '';
    const isActiveOrch = isHivemindOrchestrator({sessionId: activeSessionId, hivemindRole: (activeSession||{}).hivemindRole});
    const orchHmId = isActiveOrch ? ((activeSession||{}).hivemindId || '') : '';
    // Provider badge — ALWAYS show in the chat header (unlike _providerBadge
    // which hides claude to keep tile/list rows quiet). Inside a chat the
    // user needs to know which model they're talking to at a glance, even
    // when it's the default claude. The model name (e.g. "sonnet",
    // "gemini-2.5-pro") rides along when set — empty string means the
    // provider's CLI default; we render just the provider in that case.
    const _provName = ((activeSession && activeSession.provider) || p.provider || 'claude').toLowerCase();
    const _provLabel = (_agentProviders || []).find(x => x.name === _provName)?.display_name || _provName;
    // When the auto-router picked a model for this session, prefer the routed
    // model (activeSession.model) over the configured one (activeSession.agentModel).
    // model_source carries the attribution: 'manual' = user-configured;
    // 'auto' = classifier-picked; 'fallback' = classifier failed, user model used.
    const _modelSource = (activeSession && activeSession.modelSource) || 'manual';
    const _routedModel = (activeSession && activeSession.model) || '';
    const _provModel = _routedModel || (activeSession && activeSession.agentModel) || '';
    const _provText = _provModel ? `${_provName} · ${_provModel}` : _provName;
    const _routerSuffixHTML = (_modelSource === 'auto')
      ? ` <span style="opacity:.7;font-size:10px;font-weight:600;letter-spacing:.4px;margin-left:4px">AUTO</span>`
      : (_modelSource === 'fallback')
        ? ` <span style="opacity:.7;font-size:10px;font-weight:600;letter-spacing:.4px;margin-left:4px;color:var(--orange,#f59e0b)" title="Classifier failed; using your configured model">FB</span>`
        : '';
    const _provTitle = _provModel
      ? `Provider: ${_provLabel} • Model: ${_provModel}${_modelSource === 'auto' ? ' (auto-picked by router)' : (_modelSource === 'fallback' ? ' (classifier failed, fallback)' : '')}`
      : `Provider: ${_provLabel} (default model)`;
    const _provBadge = `<span class="provider-badge prov-${esc(_provName.replace(/[^a-z]/g,''))}" title="${esc(_provTitle)}">${esc(_provText)}${_routerSuffixHTML}</span>`;
    // Persona pill (Prompt Builder Phase 2) — visible for the whole chat
    // when a character was chosen at spawn; immutable for this chat's life.
    const _char = activeSession && activeSession.character;
    const _charName = _char && (_char.display_name || _char.name);
    const _charBadge = _charName
      ? `<span class="character-badge" title="Persona for this chat: ${esc(_charName)}${_char.scope === 'global' ? ' (global)' : ''} — fixed for the conversation; start a new chat to change it">&#x1F3AD; ${esc(_charName)}</span>`
      : '';
    // APK version pill — visible only inside the Capacitor APK (the native
    // injection sets window.__clayruneAPK). Lets the user confirm which
    // version is loaded and whether the native POST bridge is active. On
    // the desktop dashboard / regular browser the pill stays hidden.
    //
    // Auto-hide the GREEN (bridge-active) pill after 20s — it's
    // confirmation, not a permanent indicator. The RED (bridge-missing)
    // pill stays forever since it's a warning the user needs to act on.
    const _apk = (typeof window !== 'undefined') ? window.__clayruneAPK : null;
    if (_apk && _apk.bridge && !window._apkPillFirstShownAt) {
      window._apkPillFirstShownAt = Date.now();
      // Schedule a one-shot re-render at 20s so the pill goes away even
      // when nothing else triggers a refresh (idle session, no SSE events).
      setTimeout(() => { try { renderAgentConsole(); refreshModal(); } catch (_) {} }, 20500);
    }
    const _apkExpired = (_apk && _apk.bridge && window._apkPillFirstShownAt
        && (Date.now() - window._apkPillFirstShownAt > 20000));
    const _apkBadge = (_apk && !_apkExpired) ? `<span class="provider-badge" style="background:${_apk.bridge ? 'rgba(16,185,129,.12)' : 'rgba(239,68,68,.15)'};color:${_apk.bridge ? '#10b981' : '#ef4444'}" title="${_apk.bridge ? 'Native POST bridge active' : 'WARNING: native bridge missing — POSTs may hang after Doze'}">APK ${esc(_apk.version)}${_apk.bridge ? '' : ' ⚠'}</span>` : '';
    tabContent = `
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;flex-wrap:wrap;flex-shrink:0">
        <span class="agent-status-dot ${st}"></span>
        <span class="agent-status-label ${st}">${esc(consoleStatusLabelText)}</span>
        ${_provBadge}
        ${_charBadge}
        ${_apkBadge}
        ${isActiveOrch ? '<span class="hm-orch-label">&#x2B21; Hivemind</span>' : ''}
        ${stopBtn}
        ${_pcaps.emits_usage ? `<span class="token-badge" id="session-metrics-${esc(activeSessionId)}">${activeSession ? sessionMetricsHTML(activeSession, _pcaps) : ''}</span>` : ''}
        <span class="agent-activity" id="agent-activity-${esc(activeSessionId)}"></span>
        ${_pcaps.supports_plan_mode ? `<span id="plan-file-btn-${esc(activeSessionId)}">${planFileBtn}</span>` : ''}
        <button class="btn-popout" onclick="openPlanViewer('${esc(activeSessionId)}')" title="Open output in viewer window">Pop Out &#8599;</button>
        ${orchHmId ? `<button class="btn-hm-dash" onclick="openHivemindDashboard('${esc(orchHmId)}')">Dashboard &#8599;</button>` : ''}
        ${mobileMode ? `<button class="conv-new-btn" style="margin-left:auto" onclick="newAgentTab('${esc(p.id)}')" title="Start a new conversation">+ New</button>` : ''}
      </div>
      <div class="agent-chat">
        <div class="agent-output" id="agent-output-${esc(activeSessionId)}">${outputLines}${typingHTML}</div>
        ${chatInput ? `<div class="agent-chat-separator"></div>${chatInput}` : ''}
      </div>`;
  }

  return `<div class="agent-panel">
    <div class="agent-panel-header">
      <div class="section-title" style="margin-bottom:0">Agent</div>
    </div>
    ${tabBar}
    ${convBackBar}
    ${convListHTML}
    ${tabContent}
    ${dispatchRow}
  </div>`;
}

// WhatsApp-Communities-style conversation list: shown in the Agent panel when
// a project has >1 active conversation. Each row drills into that
// conversation's chat; a back bar (rendered by agentPanelHTML) returns here.
function conversationListHTML(p, sessions) {
  // Pinned chats (keyed on the durable claude_session_id) lead the list.
  const _pinnedList = p.pinned_conversations || [];
  const _isPin = h => {
    const cache = agentStatusCache[h.sessionId] || {};
    if (typeof cache.pinned === 'boolean') return cache.pinned;
    const c = cache.claudeSessionId || '';
    return !!c && _pinnedList.includes(c);
  };
  const _ordered = [...sessions.filter(_isPin), ...sessions.filter(h => !_isPin(h))];
  const rows = _ordered.map(h => conversationRowHTML(p, h)).join('');
  return `
    <div class="conv-list-header">
      <span class="conv-list-title">Conversations</span>
      <span class="conv-list-count">${sessions.length}</span>
      <button class="conv-new-btn" onclick="newAgentTab('${esc(p.id)}')" title="Start a new conversation">+ New</button>
    </div>
    <div class="conv-list">${rows}</div>`;
}

// One row in the conversation drill-down list. Mirrors the consolidated
// status vocabulary: ring/dot colour and sub-line come from the same
// resolver the badge/console use (running→working, detected plan/question→
// asking "Awaiting input", error→stuck, turn-done idle→done "All done").
function conversationRowHTML(p, h) {
  const cache = agentStatusCache[h.sessionId] || {};
  const sst = cache.status || h.status || 'idle';
  const waiting = cache.waitingForPlanApproval || cache.waitingForQuestion;
  const fs = sst === 'running' ? 'working'
    : waiting ? 'asking'
    : sst === 'error' ? 'stuck'
    : 'done';  // idle / completed / stopped — turn finished
  const isOrch = isHivemindOrchestrator(h);
  const isInc = h.incognito || cache.incognito;
  const glyph = isOrch ? '⬡' : isInc ? '🕶️' : '💬';
  const name = esc((h.task || '').substring(0, 70) || 'Session');
  const when = esc(timeAgoJS(h.startedAt) || '');
  const statusText = esc(consoleStatusLabel(sst, cache));
  const convProv = h.provider || cache.provider || p.provider || 'claude';
  const _convChar = h.character || cache.character;
  const _convCharName = _convChar && (_convChar.display_name || _convChar.name);
  const tags = [
    isOrch ? '<span class="conv-tag orch">Hivemind</span>' : '',
    isInc ? '<span class="conv-tag inc">Incognito</span>' : '',
    _convCharName ? `<span class="conv-tag char" title="Persona: ${esc(_convCharName)}">&#x1F3AD; ${esc(_convCharName)}</span>` : '',
    _providerBadge(convProv),
  ].join('');
  const isPinnedConv = (typeof cache.pinned === 'boolean')
    ? cache.pinned
    : (() => { const c = cache.claudeSessionId || ''; return !!c && (p.pinned_conversations || []).includes(c); })();
  const pinMark = `<button class="conv-pin-mark${isPinnedConv ? ' pinned' : ''}" onclick="event.stopPropagation();togglePinConversationSession('${esc(p.id)}','${esc(h.sessionId)}')" title="${isPinnedConv ? 'Unpin this chat' : 'Pin this chat'}" aria-label="${isPinnedConv ? 'Unpin this chat' : 'Pin this chat'}" aria-pressed="${isPinnedConv ? 'true' : 'false'}">&#x1F4CC;</button>`;
  return `
  <div class="conv-row ${isPinnedConv ? 'pinned-conv' : ''}" onclick="switchAgentTab('${esc(p.id)}','${esc(h.sessionId)}')" title="${esc(h.task || '')}">
    <div class="conv-av friendly-${fs}"><span class="conv-ring"></span>${glyph}</div>
    <div class="conv-main">
      <div class="conv-top">
        <span class="conv-name">${name}</span>
        ${pinMark}
        <span class="conv-time">${when}</span>
      </div>
      <div class="conv-bot">
        <span class="conv-sub"><span class="agent-status-dot ${sst}"></span>${statusText}</span>
        <span class="conv-badges">${tags}</span>
      </div>
    </div>
    <button class="conv-row-close" onclick="event.stopPropagation();closeAgentTab('${esc(p.id)}','${esc(h.sessionId)}')" title="Close conversation">&#10005;</button>
  </div>`;
}

// Return from a drilled-in conversation (or the New screen) to the list.
// Pure UI — also called by the popstate handler (which already consumed the
// L2 history entry). Do NOT touch history here.
function backToConvList(projectId) {
  delete activeAgentTab[projectId];
  delete agentConvNew[projectId];
  refreshModal();
}

// On-screen "← All conversations" button: same UI as backToConvList, but
// also synthetically pops the L2 sentinel so the hardware-back stack stays
// in sync (next hardware back then closes the modal, not double-back).
function mcBackFromConv(projectId) {
  if (_mcConvHistoryActive) {
    _mcConvHistoryActive = false;
    _mcUnwindHistory(1);
  }
  backToConvList(projectId);
}

function switchAgentTab(projectId, sessionId) {
  // Drilling into a conversation exits "New" mode.
  delete agentConvNew[projectId];
  const wasOnList = !activeAgentTab[projectId];
  activeAgentTab[projectId] = sessionId;
  // Forward nav into a conversation when a list level exists (>1 convo):
  // push the L2 sentinel now so hardware-back returns to the list.
  if (wasOnList && getProjectTabSessions(projectId).length > 1) {
    mcPushConvHistory();
  }
  refreshModal();
  // Recover anything this conversation missed while it was inactive: its SSE may
  // have closed (turn_complete, the Chromium 6-connection cap, or Doze parking),
  // so the buffer can be stale and a plain tab switch otherwise never re-fetches
  // — the agent's later output stays silently missing until a hard refresh.
  // fetchAgentStatus refills the buffer from server truth, idempotently repaints
  // the now-visible panel (the render-gap fix inside it), and reconnects the live
  // stream for a running or active-idle session. We deliberately use it rather
  // than a direct _reconcileAgentBuffer append: the append could race a
  // concurrent background poll's repaint and render the recovered lines twice,
  // whereas the clear-and-rebuild repaint is idempotent.
  fetchAgentStatus(projectId);
}

function newAgentTab(projectId) {
  // Clear active tab and force the dispatch screen. agentConvNew keeps it
  // there even with sessions present (otherwise multi would fall back to
  // the list and single would auto-reselect the lone conversation).
  const wasOnList = !activeAgentTab[projectId] && agentConvNew[projectId] !== true;
  delete activeAgentTab[projectId];
  agentConvNew[projectId] = true;
  // The "New" screen is a sub-level of the list when one exists (>1 convo):
  // push the L2 sentinel so hardware-back returns to the list, not out.
  if (wasOnList && getProjectTabSessions(projectId).length > 1) {
    mcPushConvHistory();
  }
  // Set null explicitly — do NOT delete. Deleting causes agentPanelHTML to
  // auto-repopulate from the log cache on the very next refreshModal(), which
  // makes the subsequent dispatch resume the prior conversation instead of
  // starting fresh. null keeps the key present (skips auto-populate) while
  // still meaning "no resume" to dispatchAgent and sessionPickerHTML.
  pendingResumeId[projectId] = null;
  refreshModal();
  setTimeout(() => document.getElementById(`agent-task-${projectId}`)?.focus(), 50);
}

function getDefaultResumeId(projectId) {
  const entries = (agentLogCache[projectId] || []).filter(e => !e.hivemind_ws_id);
  const runningSessions = getProjectSessions(projectId);
  const runningResumeIds = new Set(
    runningSessions.filter(h => h.resumedFrom).map(h => h.resumedFrom)
  );
  for (const e of entries) {
    if (e.claude_session_id && !runningResumeIds.has(e.claude_session_id)) {
      return e.claude_session_id;
    }
  }
  return null;
}

function selectResumeSession(projectId, claudeSessionId) {
  pendingResumeId[projectId] = claudeSessionId || null;
  refreshModal();
  setTimeout(() => document.getElementById(`agent-task-${projectId}`)?.focus(), 50);
}

// ── Typing indicator (§4, 2026-07-05) ──────────────────────────────────────
// A left bubble with three pulsing dots shown while the agent generates. It is
// an EPHEMERAL DOM node (id=typing-<sid>); it is never written to
// agentOutputBuffers, so a rebuild (refreshModal) drops it and re-derives it
// declaratively from run-state in agentPanelHTML. Both are exported on window
// so resume-preview.js (a separate module) can call them by bare name.
function hideTypingIndicator(sessionId) {
  document.getElementById(`typing-${sessionId}`)?.remove();
}
function showTypingIndicator(sessionId) {
  const el = document.getElementById(`agent-output-${sessionId}`);
  if (!el) return;
  if (document.getElementById(`typing-${sessionId}`)) return; // already shown
  const wasPinned = _isAgentOutputPinned(el, sessionId);
  const div = document.createElement('div');
  div.className = 'agent-line typing-indicator';
  div.id = `typing-${sessionId}`;
  div.innerHTML = '<span></span><span></span><span></span>';
  el.appendChild(div);
  if (wasPinned) _scheduleAgentPinScroll(sessionId, el, false);
}

function appendAgentLine(sessionId, text) {
  const el = document.getElementById(`agent-output-${sessionId}`);
  if (!el) return;
  // The instant any real line streams in, the typing indicator is stale — drop
  // it before appending (runs on every recursive multiline call; idempotent).
  hideTypingIndicator(sessionId);
  const freshMount = !el.dataset.scrollInitialized;
  const wasPinned = freshMount || _isAgentOutputPinned(el, sessionId);
  // NOTE: do NOT set `el.style.display = 'block'` here. That inline style
  // wins over CSS and clobbers the mobile `@media (max-width: 960px)` rule
  // that sets `.agent-output { display: flex }` for chat-bubble layout —
  // with display:block, `align-self: flex-end` on `.agent-line-prompt` is a
  // no-op and user prompts render left-aligned with agent narration. The
  // `.agent-output:empty { display: none }` rule (CSS) is what we'd be
  // overriding, but appendChild below makes the element non-empty in the
  // same task, so the :empty rule stops matching before paint.

  // Split multi-line text blocks into individual lines
  // But keep user prompts (> ) as a single block so styling covers all lines
  if (text.includes('\n') && !text.trimStart().startsWith('> ')) {
    for (const line of text.split('\n')) {
      appendAgentLine(sessionId, line);
    }
    return;
  }

  // Mermaid diagram interception. Must come before all other line handling
  // so ```mermaid fences are caught even if they look like other syntaxes.
  if (_handleMermaidLine(sessionId, text, el)) {
    if (wasPinned) _scheduleAgentPinScroll(sessionId, el, freshMount);
    return;
  }

  // Group consecutive table lines into a single styled block
  if (isTableLine(text)) {
    let block = el.lastElementChild;
    if (block && (block.classList.contains('hl-table') || block.classList.contains('hl-table-pre'))) {
      block._rawLines.push(text);
    } else {
      block = document.createElement('div');
      block._rawLines = [text];
      el.appendChild(block);
    }
    // Rebuild: pipe tables get <table>, box-drawing stays pre-formatted
    if (isPipeTable(block._rawLines)) {
      block.className = 'hl-table';
      block.innerHTML = buildPipeTable(block._rawLines);
    } else {
      block.className = 'hl-table-pre';
      block.innerHTML = block._rawLines.map(l => formatTableLine(esc(l))).join('\n');
    }
    if (wasPinned) _scheduleAgentPinScroll(sessionId, el, freshMount);
    return;
  }
  // Blank lines inside a table: keep them in the table block
  if (text.trim() === '') {
    const last = el.lastElementChild;
    if (last && (last.classList.contains('hl-table') || last.classList.contains('hl-table-pre'))) {
      last._rawLines.push(text);
      if (wasPinned) _scheduleAgentPinScroll(sessionId, el, freshMount);
      return;
    }
  }

  const div = document.createElement('div');
  const cls = agentLineCls(text);
  div.className = cls;
  // Reset stuck-plan counter only when a non-plan-related tool appears
  if (cls.includes('agent-line-tool') && !text.includes('ExitPlanMode') && !text.includes('EnterPlanMode')) {
    exitPlanModeCount[sessionId] = 0;
  }
  // Use rich formatting for regular text lines. Prompts get the image-aware
  // escaper so an attached image path renders as a thumbnail. Other special
  // lines (tool / error / status / queued) stay on plain textContent.
  if (cls.includes('agent-line-prompt')) {
    div.innerHTML = escPromptWithImages(text);
  } else if (cls.includes('agent-line-tool') || cls.includes('agent-line-error') || cls.includes('agent-line-followup') || cls.includes('agent-line-queued')) {
    div.textContent = text;
  } else {
    div.innerHTML = formatAgentText(text);
  }
  el.appendChild(div);
  if (wasPinned) _scheduleAgentPinScroll(sessionId, el, freshMount);

  // Plan detection: [tool: ExitPlanMode] signals end of plan (claude + supports_plan_mode only)
  const _pdCached = agentStatusCache[sessionId];
  const _pdProj = _pdCached ? allProjects.find(p => p.id === _pdCached.projectId) : null;
  const _pdCaps = _pdProj ? _capsForProject(_pdProj) : _CLAUDE_DEFAULT_CAPS;
  if (_pdCaps.supports_plan_mode && text.trim() === '[tool: ExitPlanMode]') {
    exitPlanModeCount[sessionId] = (exitPlanModeCount[sessionId] || 0) + 1;
    // Store plan content for viewer but keep text visible for first occurrence
    planViewerContent[sessionId] = planViewerContent[sessionId] || [];
    const children = Array.from(el.children);
    for (let i = children.length - 1; i >= 0; i--) {
      const child = children[i];
      const txt = (child.textContent || '').trim();
      if (child.classList.contains('agent-line-tool') || child.classList.contains('agent-line-prompt') || child.classList.contains('plan-show-btn')) break;
      if (txt === '[tool: ExitPlanMode]') continue;
      planViewerContent[sessionId].unshift(child.textContent || child.innerText || '');
    }

    // Mark as waiting for plan approval in caches
    if (agentStatusCache[sessionId]) agentStatusCache[sessionId].waitingForPlanApproval = true;
    const histEntry = agentHistory.find(h => h.sessionId === sessionId);
    if (histEntry) histEntry.waitingForPlanApproval = true;
    refreshModal();

    // Show "Approve Plan" + "Collapse" buttons on first ExitPlanMode (plan stays visible)
    if (exitPlanModeCount[sessionId] === 1) {
      const cached = agentStatusCache[sessionId];
      const pid = cached ? cached.projectId : null;
      if (pid) {
        const approveRow = document.createElement('div');
        approveRow.className = 'plan-approve-row';
        approveRow.id = `plan-approve-${sessionId}`;
        approveRow.style.cssText = 'display:flex;align-items:center;gap:10px;padding:8px 0;margin:6px 0;flex-wrap:wrap';
        approveRow.innerHTML = `<span style="color:var(--text-dim);font-size:12px">Agent is waiting for plan approval.</span>
          <button style="background:var(--green);color:#fff;border:none;padding:6px 16px;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer" onclick="approvePlan('${esc(pid)}','${esc(sessionId)}')">Approve Plan</button>
          <button style="background:var(--bg2);color:var(--text);border:1px solid var(--border2);padding:6px 12px;border-radius:6px;font-size:12px;cursor:pointer" onclick="collapseIntoPlanButton('${esc(sessionId)}',this.closest('.agent-output'))">Collapse Plan</button>`;
        el.appendChild(approveRow);
        if (wasPinned) _scheduleAgentPinScroll(sessionId, el, freshMount);
      }
    }

    // Detect stuck ExitPlanMode loop (agent can't exit plan mode via non-interactive dispatch)
    if (exitPlanModeCount[sessionId] >= 2) {
      collapseIntoPlanButton(sessionId, el);
      const oldApprove = document.getElementById(`plan-approve-${sessionId}`);
      if (oldApprove) oldApprove.remove();
      const cached = agentStatusCache[sessionId];
      const pid = cached ? cached.projectId : null;
      const warn = document.createElement('div');
      warn.className = 'agent-plan-stuck-warning';
      warn.style.cssText = 'background:var(--amber-dim);border:1px solid var(--amber);color:var(--amber-text);padding:8px 12px;border-radius:6px;margin:6px 0;font-size:12px;line-height:1.5;display:flex;align-items:center;gap:10px;flex-wrap:wrap';
      warn.innerHTML = `&#x26A0; Agent is stuck trying to exit plan mode.${pid ? ` <button style="background:var(--green);color:#fff;border:none;padding:6px 16px;border-radius:6px;font-size:12px;font-weight:600;cursor:pointer" onclick="approvePlan('${esc(pid)}','${esc(sessionId)}')">Approve Plan</button>` : ''}`;
      el.appendChild(warn);
      if (wasPinned) _scheduleAgentPinScroll(sessionId, el, freshMount);
      exitPlanModeCount[sessionId] = 0; // Reset so it doesn't spam
    }

    // Check for plan file and inject button
    const cached = agentStatusCache[sessionId];
    if (cached && cached.projectId) {
      fetch(API_BASE + `/api/project/${cached.projectId}/agent/status`)
        .then(r => r.json())
        .then(data => {
          const s = (data.sessions || []).find(x => x.session_id === sessionId);
          if (s && s.plan_file) {
            cached.planFile = s.plan_file;
            // Fetch plan content to extract heading for button label
            fetch(API_BASE + `/api/project/${cached.projectId}/agent/plan-file?session=${sessionId}`)
              .then(r => r.ok ? r.json() : null)
              .then(planData => {
                const fname = s.plan_file.split(/[/\\]/).pop();
                let label;
                if (planData && planData.content) {
                  const headingMatch = planData.content.match(/^#\s+(.+)/m);
                  if (headingMatch) {
                    label = headingMatch[1].trim();
                    if (label.length > 40) label = label.slice(0, 37) + '...';
                    planFileTitle[sessionId] = label;
                  }
                }
                if (!label) label = planFileLabel(cached.task || fname);
                const container = document.getElementById(`plan-file-btn-${sessionId}`);
                if (container && !container.innerHTML.trim()) {
                  container.innerHTML = `<button class="btn-plan-file" onclick="openPlanFileViewer('${esc(cached.projectId)}','${esc(sessionId)}')" title="${esc(fname)}">&#128196; ${esc(label)}</button>`;
                }
              }).catch(() => {
                const fname = s.plan_file.split(/[/\\]/).pop();
                const label = planFileLabel(cached.task || fname);
                const container = document.getElementById(`plan-file-btn-${sessionId}`);
                if (container && !container.innerHTML.trim()) {
                  container.innerHTML = `<button class="btn-plan-file" onclick="openPlanFileViewer('${esc(cached.projectId)}','${esc(sessionId)}')" title="${esc(fname)}">&#128196; ${esc(label)}</button>`;
                }
              });
          }
        }).catch(() => {});
    }
  }
}

function approvePlan(projectId, sessionId) {
  // Guard against double-click
  const row = document.getElementById(`plan-approve-${sessionId}`);
  if (row) row.remove();
  // Clear plan approval state
  if (agentStatusCache[sessionId]) agentStatusCache[sessionId].waitingForPlanApproval = false;
  const hEntry = agentHistory.find(h => h.sessionId === sessionId);
  if (hEntry) hEntry.waitingForPlanApproval = false;
  // Show local echo
  const echoEl = document.getElementById(`agent-output-${sessionId}`);
  if (echoEl) {
    const div = document.createElement('div');
    div.className = 'agent-line agent-line-prompt agent-echo';
    div.textContent = '> Plan approved, please continue.';
    div.style.whiteSpace = 'pre-wrap';
    echoEl.appendChild(div);
    echoEl.scrollTop = echoEl.scrollHeight;
  }
  // Always send directly via API (more reliable than going through input element)
  fetch(API_BASE + `/api/project/${projectId}/agent/followup`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(_maybeTagMobileClient({ message: 'Plan approved, please continue.', session_id: sessionId }))
  }).catch(e => console.error('Plan approval failed:', e));
}

let _aqCounter = 0;
const _EMPTY_SET = new Set();  // shared read-only fallback for the answered-id lookup

// Cache a pending question on the session so a later panel rebuild can restore
// the form without waiting for the /agent/status poll. Deduped by question_id.
function _cachePendingQuestion(sessionId, questions, questionId) {
  const c = agentStatusCache[sessionId];
  if (!c) return;
  const list = c.pendingQuestions || (c.pendingQuestions = []);
  if (questionId && list.some(p => p.question_id === questionId)) return;
  list.push({ questions: questions || [], question_id: questionId || '' });
}

// Re-render any unanswered question forms for a session after its panel was
// rebuilt. Idempotent: renderAgentQuestion dedupes against the DOM, so this is
// a no-op when the form's still on screen and restores it when it isn't.
function _rerenderPendingQuestions(projectId, sessionId) {
  const c = agentStatusCache[sessionId];
  if (!c || !c.waitingForQuestion) return;
  for (const pq of (c.pendingQuestions || [])) {
    try { renderAgentQuestion(sessionId, projectId, pq.questions || [], pq.question_id || ''); }
    catch (_) {}
  }
}

// Render an AskUserQuestion form into the session's chat. The server re-emits
// the question on every SSE reconnect AND mirrors it on /agent/status, so this
// is called repeatedly for the same question — dedupe against the DOM, never a
// persistent set. A set that outlived the element WAS the "question silently
// never comes back" bug: once the card got wiped (modal reopen, tab switch,
// conversation-list nav — anything that rebuilds agent-output without the
// preserved node), the set still said "already rendered" and suppressed every
// re-delivery, so the agent sat waiting with no form on screen.
function renderAgentQuestion(sessionId, projectId, questions, questionId) {
  const el = document.getElementById(`agent-output-${sessionId}`);
  if (!el || !questions || !questions.length) return;
  // Agent is parked waiting for an answer → definitively not generating.
  // Covers all 3 call sites (SSE question, fetchAgentStatus, rerender) and the
  // turn_complete-suppressed window where status never flips to idle. (§4)
  hideTypingIndicator(sessionId);
  if (questionId) {
    // Don't resurrect a form the user already answered this turn…
    if ((_answeredQuestionIds[sessionId] || _EMPTY_SET).has(questionId)) return;
    // …and skip only if THIS card is actually on screen right now. (question_id
    // is uuid hex → safe to interpolate into the attribute selector.)
    if (el.querySelector(`.agent-question[data-qid="${questionId}"]`)) return;
  }
  const wasPinned = _isAgentOutputPinned(el);
  const formId = `aq-${sessionId}-${_aqCounter++}`;

  const container = document.createElement('div');
  container.className = 'agent-question';
  container.id = formId;
  if (questionId) container.dataset.qid = questionId;

  let html = '';
  questions.forEach((q, qi) => {
    const inputType = q.multiSelect ? 'checkbox' : 'radio';
    const groupName = `${formId}-q${qi}`;
    html += `<div class="agent-question-header">${esc(q.header || 'Question')}</div>`;
    html += `<div class="agent-question-text">${esc(q.question)}</div>`;
    html += `<div class="agent-question-options" data-qidx="${qi}">`;
    (q.options || []).forEach((opt, oi) => {
      html += `<div class="agent-question-option">
        <input type="${inputType}" name="${groupName}" id="${groupName}-o${oi}" value="${esc(opt.label)}" data-desc="${esc(opt.description || '')}">
        <label for="${groupName}-o${oi}">${esc(opt.label)}${opt.description ? `<span class="aq-desc">${esc(opt.description)}</span>` : ''}</label>
      </div>`;
    });
    // "Other" option
    html += `<div class="agent-question-option">
      <input type="${inputType}" name="${groupName}" id="${groupName}-other" value="__other__" onchange="document.getElementById('${groupName}-other-text').classList.toggle('visible', this.checked || this.selected)">
      <label for="${groupName}-other">Other</label>
    </div>`;
    html += `<input type="text" class="agent-question-other" id="${groupName}-other-text" placeholder="Type your answer...">`;
    html += `</div>`;
  });
  html += `<div class="agent-question-actions">
    <button onclick="submitQuestionAnswer('${esc(projectId)}','${esc(sessionId)}','${formId}',${questions.length})">Submit Answer</button>
  </div>`;

  container.innerHTML = html;
  el.appendChild(container);
  if (wasPinned) el.scrollTop = el.scrollHeight;

  // Show/hide "Other" text field on radio change
  container.querySelectorAll('input[type="radio"]').forEach(radio => {
    radio.addEventListener('change', () => {
      const group = radio.name;
      const otherText = document.getElementById(group + '-other-text');
      const otherRadio = document.getElementById(group + '-other');
      if (otherText) otherText.classList.toggle('visible', otherRadio && otherRadio.checked);
    });
  });
  container.querySelectorAll('input[type="checkbox"]').forEach(cb => {
    cb.addEventListener('change', () => {
      if (cb.value === '__other__') {
        const otherText = document.getElementById(cb.name + '-other-text');
        if (otherText) otherText.classList.toggle('visible', cb.checked);
      }
    });
  });
}

function submitQuestionAnswer(projectId, sessionId, formId, questionCount) {
  const container = document.getElementById(formId);
  if (!container) return;

  const answers = [];
  for (let qi = 0; qi < questionCount; qi++) {
    const groupName = `${formId}-q${qi}`;
    const checked = container.querySelectorAll(`input[name="${groupName}"]:checked`);
    const parts = [];
    checked.forEach(input => {
      if (input.value === '__other__') {
        const otherText = document.getElementById(groupName + '-other-text');
        if (otherText && otherText.value.trim()) parts.push(otherText.value.trim());
      } else {
        parts.push(input.value);
      }
    });
    if (parts.length === 0) continue;
    // Find the question text from the header
    const qTextEl = container.querySelectorAll('.agent-question-text')[qi];
    const qText = qTextEl ? qTextEl.textContent : '';
    answers.push({ question: qText, answer: parts.join(', ') });
  }

  if (answers.length === 0) return;

  // Format answer as a clear response to the agent's AskUserQuestion.
  // Phrasing is directive because the previous turn was killed mid-tool_use:
  // claude resumes with an unresolved AskUserQuestion in its transcript and,
  // without explicit framing, can interpret the user's reply as a fresh
  // unrelated turn and re-ask the same questions in plain text. We tell it
  // up front: this IS the answer, do not re-ask.
  let message = (
    'I answered your AskUserQuestion through the Clayrune UI. The values '
    + 'below are my chosen responses — proceed with the task using them. '
    + 'Do NOT re-ask these questions.\n\n'
  ) + answers.map(a =>
    a.question ? `Q: ${a.question}\nA: ${a.answer}` : a.answer
  ).join('\n\n');

  // Mark the form as answered (both in the DOM and in the per-turn answered-set
  // so a rebuild-after-answer can't re-render it from cache before turn_start).
  container.classList.add('answered');
  if (container.dataset.qid) {
    (_answeredQuestionIds[sessionId] || (_answeredQuestionIds[sessionId] = new Set())).add(container.dataset.qid);
  }
  const actionsEl = container.querySelector('.agent-question-actions');
  if (actionsEl) actionsEl.innerHTML = `<div class="agent-question-answer">Answered: ${esc(answers.map(a => a.answer).join(', '))}</div>`;
  // Disable inputs
  container.querySelectorAll('input').forEach(i => i.disabled = true);
  // Clear waiting_for_question locally — server will confirm on turn_start,
  // but we want the tile + feed to drop out of "asking" immediately.
  if (agentStatusCache[sessionId]) agentStatusCache[sessionId].waitingForQuestion = false;
  const _hAns = agentHistory.find(h => h.sessionId === sessionId);
  if (_hAns) _hAns.waitingForQuestion = false;
  // Drop the cached pending question(s) so a post-rebuild re-render can't
  // resurrect this now-answered form. (Dedup is DOM-based and the answered card
  // keeps its data-qid, so a stray re-delivery is a no-op regardless.)
  if (agentStatusCache[sessionId]) agentStatusCache[sessionId].pendingQuestions = [];

  // Send as follow-up
  const input = document.getElementById(`agent-followup-${sessionId}`);
  if (input) {
    input.value = message;
    sendFollowup(projectId, sessionId);
  } else {
    // Fallback: direct API call
    fetch(API_BASE + `/api/project/${projectId}/agent/followup`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(_maybeTagMobileClient({ message, session_id: sessionId }))
    }).catch(() => {});
  }
}

async function sendFollowup(projectId, sessionId) {
  const input = document.getElementById(`agent-followup-${sessionId}`);
  const message = input.value.trim();
  if (!message) { input.focus(); return; }

  // Guardian guards — prevent piling on during recovery
  const guardCached = agentStatusCache[sessionId];
  if (guardCached?.guardianState === 'recovering') {
    showToast('Session is being recovered \u2014 please wait', 3000);
    return;
  }
  if (guardCached?.circuitBreakerTripped) {
    showToast('Session recovery exhausted \u2014 use "Try Again" or "Start Fresh"', 5000);
    return;
  }

  input.value = '';
  if (input.id) delete textareaValues[input.id];

  // Immediate local echo — show the user's message in DOM only (not buffer)
  // The server will send the real version via SSE which gets added to the buffer
  const echoEl = document.getElementById(`agent-output-${sessionId}`);
  if (echoEl) {
    const div = document.createElement('div');
    div.className = 'agent-line agent-line-prompt agent-echo';
    div.textContent = `> ${message}`;
    div.style.whiteSpace = 'pre-wrap';
    echoEl.appendChild(div);
    echoEl.scrollTop = echoEl.scrollHeight;
  }

  // Zero-gap picker update: reflect the newest user message in conversationsCache
  // so when this session later becomes non-running (stops / ends), the picker
  // already shows the real last line without waiting for a reload.
  {
    const cachedForConvo = agentStatusCache[sessionId] || {};
    const csid = cachedForConvo.claudeSessionId || '';
    if (csid) upsertConversationCache(projectId, csid, message, 'running');
  }

  // Upload any pasted images and build final message
  const imageKey = 'fu_' + sessionId;
  const imagePaths = await uploadAgentImages(imageKey);
  const fullMessage = buildTaskWithImages(message, imagePaths);
  clearAgentImages(imageKey);
  // Remove preview thumbnails from DOM
  const previewContainer = input.closest('.agent-chat-input')?.querySelector('.agent-image-previews');
  if (previewContainer) previewContainer.remove();

  // Phase 2 of 2026-04-27 race consolidation: frontend never reads cached
  // status to pick the route. We always POST /agent/send and let the server
  // read live session state under the lock to decide between interrupt /
  // followup / revive / dispatch. UI status flips only when SSE delivers a
  // status event — no optimistic cache writes here.

  // Mark this send as in-flight before opening SSE. Until `turn_start` arrives
  // (or 8s passes) we ignore turn_complete/status events from the eager-opened
  // SSE — those reflect the PRIOR idle state, not the new turn we're about to
  // dispatch. See _sendInFlight comment for the full race.
  _sendInFlight[sessionId] = Date.now();
  _turnStartAcked[sessionId] = false;
  setTimeout(() => {
    // Safety net: if turn_start never arrives, lift the gate so terminal
    // status events can flow again.
    if (_sendInFlight[sessionId] && (Date.now() - _sendInFlight[sessionId]) >= 8000) {
      delete _sendInFlight[sessionId];
    }
  }, 8500);

  // Eagerly (re)open SSE BEFORE the POST so we don't miss `turn_start` on a
  // slow mobile request. If we waited until the fetch resolved, the server
  // could already have transitioned running → idle (fast turn) and our new
  // SSE would never see status='running' — leaving the UI stuck on IDLE.
  // The server's stream reader is session-scoped (not process-scoped), so a
  // pre-opened SSE survives the respawn that interrupt-resume performs.
  if (agentEventSources[sessionId]) {
    agentEventSources[sessionId].close();
    delete agentEventSources[sessionId];
    if (agentSSEWatchdogs[sessionId]) { clearInterval(agentSSEWatchdogs[sessionId]); delete agentSSEWatchdogs[sessionId]; }
  }
  connectAgentStream(projectId, sessionId);

  // Fire the API call. Server decides the route. SSE delivers the result.
  // For follow-ups: the existing session already carries its incognito flag,
  // so we only forward the request-level flag if the project is currently set
  // to incognito (matters when the server falls back to dispatching fresh —
  // e.g. revive of a purged session that has no log entry).
  const sendBody = { message: fullMessage, session_id: sessionId };
  if (getIncognitoFor(projectId)) sendBody.incognito = true;
  _maybeTagMobileClient(sendBody);
  // Fail-fast timeout: on Android after a Doze cycle the WebView fetch can
  // hang forever on dead sockets — the user sees their prompt cleared, the
  // echo briefly appear and then vanish (reconcile sees no recovered line),
  // and no error toast. Surface the failure within 12s so the user knows to
  // retry instead of staring at a frozen "Completed" pill. The local echo
  // line carries `agent-echo-failed` so it stays visible (as a retry cue)
  // instead of being silently wiped by the next reconcile pass.
  const _sendAC = new AbortController();
  const _sendTID = setTimeout(() => _sendAC.abort(), 12000);
  fetch(API_BASE + `/api/project/${projectId}/agent/send`, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(sendBody),
    signal: _sendAC.signal
  }).then(r => { clearTimeout(_sendTID); return r.json(); }).then(data => {
    if (!data.ok && !data.queued) {
      console.error('Send failed:', data.error);
    }
    // A successful send makes this session server-live again (followup /
    // revive / dispatch alike) — drop the read-only marker so the freshness
    // reconciler resumes healing this sid (it skips _readOnlyRevived, which
    // is set by the push-tap reconstruct path and was never cleared).
    if (data.ok && agentStatusCache[sessionId]) {
      delete agentStatusCache[sessionId]._readOnlyRevived;
    }
    // If queued (Mode A follow-up while previous turn still running),
    // mark the echo so the user knows it isn't going out yet.
    if (data.queued) {
      const el = document.getElementById(`agent-output-${sessionId}`);
      if (el) {
        const echo = el.querySelector('.agent-echo');
        if (echo) {
          echo.classList.add('agent-echo-queued');
          echo.textContent = `> [queued] ${message}`;
        }
      }
    }
    // If the server dispatched a fresh session under a new id (no live
    // session_id was provided, or the prior one was purged), point the UI
    // at the new session so SSE connects there.
    const targetSessionId = data.session_id || sessionId;
    if (targetSessionId !== sessionId) {
      // A fresh session was spawned — redirect the active tab so the user
      // sees the new session running rather than the old one stuck at its
      // previous terminal status.
      const oldHist = agentHistory.find(h => h.sessionId === sessionId && h.projectId === projectId);
      if (oldHist && !agentHistory.find(h => h.sessionId === targetSessionId)) {
        agentHistory.unshift({ ...oldHist, sessionId: targetSessionId, status: 'running', startedAt: new Date().toISOString() });
      }
      agentStatusCache[targetSessionId] = { ...(agentStatusCache[sessionId] || {}), status: 'running', startedAt: new Date().toISOString(), claudeSessionId: '' };
      activeAgentTab[projectId] = targetSessionId;
      _sendInFlight[targetSessionId] = _sendInFlight[sessionId] || Date.now();
      delete _sendInFlight[sessionId];
      refreshModal();
      renderAgentConsole();
      if (agentEventSources[sessionId]) {
        agentEventSources[sessionId].close();
        delete agentEventSources[sessionId];
      }
      if (!agentEventSources[targetSessionId]) {
        connectAgentStream(projectId, targetSessionId);
      }
    } else if (!agentEventSources[targetSessionId]) {
      // Eager connect above may have failed (e.g. session momentarily
      // missing); retry now that we know the server route resolved.
      connectAgentStream(projectId, targetSessionId);
    }
    // Reconcile after a short window: gives SSE a chance to deliver the new
    // `> Ron:` line normally, and if it didn't (mobile drop, stale cursor),
    // we silently fill in the gap so the user always sees their prompt.
    setTimeout(() => _reconcileAgentBuffer(projectId, targetSessionId), 1500);
    // Status-recovery watchdog. On mobile WebView + CF Tunnel the eager-
    // opened SSE often silently buffers — turn_start never reaches the
    // client and the pill stays frozen on "idle" / "Completed" while the
    // agent works or waits on an AskUserQuestion. Symptom: user has to
    // background+foreground the app for any update. If turn_start hasn't
    // fired by ~4s after POST resolved, tear down the (possibly dead) SSE,
    // reconnect, and reconcile status + pending question from server.
    setTimeout(() => {
      if (!(_sendInFlight[sessionId] || _sendInFlight[targetSessionId])) return;
      if (agentEventSources[targetSessionId]) {
        agentEventSources[targetSessionId].close();
        delete agentEventSources[targetSessionId];
      }
      if (agentSSEWatchdogs[targetSessionId]) {
        clearInterval(agentSSEWatchdogs[targetSessionId]);
        delete agentSSEWatchdogs[targetSessionId];
      }
      connectAgentStream(projectId, targetSessionId);
      _reconcileAgentBuffer(projectId, targetSessionId);
    }, 4000);
  }).catch(e => {
    clearTimeout(_sendTID);
    console.error('Send error:', e);
    delete _sendInFlight[sessionId];
    const aborted = (e && (e.name === 'AbortError' || /aborted/i.test(String(e))));
    // Mark the local echo as failed so it stays visible (otherwise the next
    // reconcile pass wipes any `.agent-echo` the moment it sees an unrelated
    // "> " line from prior turns, and the user is left with no trace of what
    // they typed). Also restore the input so they can retry without retyping.
    const el = document.getElementById(`agent-output-${sessionId}`);
    if (el) {
      const echo = el.querySelector('.agent-echo');
      if (echo) {
        echo.classList.remove('agent-echo');
        echo.classList.add('agent-echo-failed');
        echo.style.opacity = '0.6';
        echo.textContent = `> [failed to send] ${message}`;
      }
    }
    const inp = document.getElementById(`agent-followup-${sessionId}`);
    // Only restore the textarea if the server never acknowledged the message
    // (turn_start never arrived). If turn_start DID arrive, the agent received
    // the message and is/was running — restoring would show stale text that the
    // user already sent, which is the "text jumps back" bug.
    if (inp && !inp.value && !_turnStartAcked[sessionId]) inp.value = message;
    showToast(aborted
      ? 'Send timed out — network may be paused. Try again.'
      : `Send failed: ${e && e.message ? e.message : 'network error'}`, 4500);
  });

  // (Previously: a 20s "agent appears unresponsive" toast fired here. Removed
  // 2026-05-28 — the new fail-fast send timeout above + visible echo retention
  // already cover the actual-failure case; this toast just nagged on every
  // turn that legitimately took >20s, which is most non-trivial work.)
}

const _recentlyStoppedSessions = {};  // sessionId → timestamp when stopped

async function stopAgent(projectId, sessionId) {
  // Phase 2: no optimistic cache writes. Server is idempotent — pressing
  // Stop on an already-stopped session returns 200 with already_stopped:true.
  // Status flip happens when SSE delivers the terminal status event.
  if (followupTimeouts[sessionId]) { clearTimeout(followupTimeouts[sessionId].timerId); delete followupTimeouts[sessionId]; }
  _recentlyStoppedSessions[sessionId] = Date.now();
  // Close SSE so the next reconnect picks up the post-stop state cleanly.
  if (agentEventSources[sessionId]) {
    agentEventSources[sessionId].close();
    delete agentEventSources[sessionId];
  }
  if (agentSSEWatchdogs[sessionId]) { clearInterval(agentSSEWatchdogs[sessionId]); delete agentSSEWatchdogs[sessionId]; }
  // REQUIRED: stopAgent closes its own EventSource synchronously above, so no
  // terminal 'status' SSE arrives to remove the dots — without this they'd
  // animate forever after a manual Stop. (§4)
  hideTypingIndicator(sessionId);

  // Fire the stop request with a timeout — server kill can take seconds
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 8000);
  try {
    const res = await fetch(API_BASE + `/api/project/${projectId}/agent/stop`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ session_id: sessionId }),
      signal: controller.signal,
    });
    clearTimeout(timeoutId);
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      console.warn('Stop response:', data.error || res.status);
    }
  } catch(e) {
    clearTimeout(timeoutId);
    if (e.name === 'AbortError') {
      console.warn('Stop request timed out — process may still be terminating');
      showToast('Stop sent — process is terminating...', 3000);
    } else {
      console.error('Stop request error:', e);
    }
  }
  refreshSilent();
}

// Refresh a single project's backlog in-place (used when agent TodoWrite syncs mid-stream)
async function refreshProjectBacklog(projectId) {
  try {
    const res = await fetch(API_BASE + `/api/project/${projectId}/backlog`);
    if (!res.ok) return;
    const backlog = await res.json();
    const p = allProjects.find(x => x.id === projectId);
    if (p) {
      p.backlog = backlog;
      p._backlogFull = true;  // full bodies loaded — preserve across list re-fetches
      refreshModalById(projectId);
    }
  } catch(e) {}
}

// Fetch all agent sessions for a project on modal open / page boot
async function fetchAgentStatus(projectId) {
  try {
    const res = await fetchFailFast(API_BASE + `/api/project/${projectId}/agent/status`);
    const data = await res.json();
    const sessions = data.sessions || [];
    const pName = (allProjects.find(x => x.id === projectId) || {}).name || projectId;

    for (const s of sessions) {
      const sid = s.session_id;
      // Don't let server overwrite a recently-stopped session with 'error'
      // (protects against race where reader thread sets error before stop lands)
      const stoppedAt = _recentlyStoppedSessions[sid];
      if (stoppedAt && (Date.now() - stoppedAt < 15000) && s.status === 'error') {
        s.status = 'stopped';
      }
      if (stoppedAt && (Date.now() - stoppedAt >= 15000)) {
        delete _recentlyStoppedSessions[sid];
      }
      // Long Mode-B session advisory toast — REMOVED 2026-05-22. It was
      // well-intentioned (nudging a restart so a long session reloads its
      // Step-6-captured memory fresh) but in practice just an annoying 14s
      // nag. The server still computes `s.long_session_advisory`; nothing
      // consumes it now. To bring the nudge back, render it somewhere
      // non-intrusive (e.g. an inline session-panel hint) rather than a toast.
      agentStatusCache[sid] = { status: s.status, task: s.task, projectId, startedAt: s.started_at, planFile: s.plan_file || '', usage: s.usage || {}, cost_usd: s.cost_usd || 0, num_turns: s.num_turns || 0, hivemindId: s.hivemind_id || '', hivemindWsId: s.hivemind_ws_id || '', hivemindRole: s.hivemind_role || '', triggerType: s.trigger_type || 'manual', triggerId: s.trigger_id || '', waitingForPlanApproval: s.waiting_for_plan_approval || false, waitingForQuestion: s.waiting_for_question || false, guardianState: s.guardian_state || null, circuitBreakerTripped: s.circuit_breaker_tripped || false, claudeSessionId: s.claude_session_id || '', incognito: !!s.incognito, provider: s.provider || 'claude', agentModel: s.agent_model || '', model: s.model || '', modelSource: s.model_source || 'manual', character: s.character || null, pinned: !!s.pinned };
      // Question-form reconciliation (parity with _reconcileAgentBuffer). The
      // wholesale cache rebuild above drops pendingQuestions on every poll. If we
      // don't re-derive it here, a form that only ever lived in the cache — the
      // question arrived while this session's panel wasn't in the DOM (a
      // background agent tab, an unfocused or closed modal) — is lost on the next
      // poll and never comes back: the SSE is already connected so it won't
      // re-emit, and _rerenderPendingQuestions finds an empty cache when the tab
      // is finally focused. Re-derive from server truth so the form restores on
      // focus / tab-switch. renderAgentQuestion is DOM-dedup-safe and no-ops when
      // the panel is absent or the qid was already answered this turn.
      if (s.waiting_for_question) {
        agentStatusCache[sid].pendingQuestions = (s.pending_questions || []).map(pq => ({
          questions: pq.questions || [], question_id: pq.question_id || ''
        }));
        for (const pq of (s.pending_questions || [])) {
          try { renderAgentQuestion(sid, projectId, pq.questions || [], pq.question_id || ''); }
          catch (_) {}
        }
      }
      if (s.log_lines && s.log_lines.length > 0) {
        const _prevBufLen = (agentOutputBuffers[sid] || []).length;
        // Defensive: older / runtime-dispatched sessions may not have seeded
        // the user prompt into server-side log_lines, so a wholesale replace
        // would wipe the locally-echoed `> {task}` prefix and the user's
        // first message would disappear from the chat. Prepend it back when
        // missing so the chat survives a refresh.
        const _hasPrompt = s.log_lines.some(l => (l || '').trimStart().startsWith('> '));
        if (!_hasPrompt && s.task) {
          agentOutputBuffers[sid] = [`> ${s.task}`, ...s.log_lines];
        } else {
          agentOutputBuffers[sid] = s.log_lines;
        }
        // Anchor the SSE since= cursor to the server's authoritative count so
        // a subsequent connectAgentStream() resumes at the right index instead
        // of replaying all log_lines from 0 (which would double the buffer
        // and double-render every line in the DOM).
        agentServerLines[sid] = s.log_lines.length;
        // Render-gap recovery: if the buffer just GREW here (lines arrived while
        // this panel was inactive/backgrounded and its SSE was parked), the
        // refreshModal() at the end of this function PRESERVES the existing
        // agent-output node (scroll/perf) and won't repaint it from the grown
        // buffer — so the recovered lines stay invisible. Repaint from the buffer
        // now. Gated on (a) growth and (b) no live SSE, so the actively-streaming
        // chat (kept in sync incrementally by appendAgentLine) never flickers.
        if ((agentOutputBuffers[sid] || []).length > _prevBufLen
            && !agentEventSources[sid]
            && document.getElementById(`agent-output-${sid}`)) {
          _repaintAgentOutput(sid);
        }
      }
      const existingHist = agentHistory.find(h => h.sessionId === sid);
      if (!existingHist) {
        agentHistory.unshift({ projectId, sessionId: sid, projectName: pName, task: s.task || '', status: s.status, startedAt: s.started_at || '', hivemindId: s.hivemind_id || '', hivemindWsId: s.hivemind_ws_id || '', hivemindRole: s.hivemind_role || '', triggerType: s.trigger_type || 'manual', triggerId: s.trigger_id || '', incognito: !!s.incognito, provider: s.provider || 'claude', agentModel: s.agent_model || '', model: s.model || '', modelSource: s.model_source || 'manual', character: s.character || null });
      } else {
        if (s.incognito && !existingHist.incognito) existingHist.incognito = true;
        if (s.trigger_type && !existingHist.triggerType) existingHist.triggerType = s.trigger_type;
        if (s.trigger_id && !existingHist.triggerId) existingHist.triggerId = s.trigger_id;
        if (s.provider && !existingHist.provider) existingHist.provider = s.provider;
        if (s.agent_model && !existingHist.agentModel) existingHist.agentModel = s.agent_model;
      }
      // Auto-connect SSE for:
      //  - actively-running sessions (need live output)
      //  - sessions waiting on AskUserQuestion (need re-delivery of the form
      //    after mobile cold-reopen / modal-rebuild)
      //  - the active tab of an open modal that's idle (so the user sees
      //    status flip when they send a follow-up; otherwise sendFollowup
      //    has to open the stream, which can miss a fast turn_start → idle)
      // We still skip idle background sessions to preserve Chromium's 6-slot
      // per-origin cap; only the currently-visible idle one is subscribed.
      const isActiveTab = activeAgentTab[projectId] === sid && openModals.has(projectId);
      const wantsLiveStream = (
        s.status === 'running' ||
        (s.status === 'idle' && (s.waiting_for_question || isActiveTab))
      );
      if (wantsLiveStream && !agentEventSources[sid]) {
        connectAgentStream(projectId, sid);
      }
    }
    // Sessions the server no longer lists — after a restart that cleared the
    // in-memory agent_sessions, or the 30-min stale-purge in server.py. The
    // conversation itself is NOT gone: its transcript is on disk + in the
    // agent_log, and a follow-up transparently resumes it (`claude -r`). So we
    // DETACH the tab instead of deleting it — keep the entry, its rendered
    // buffer, and its status cache so the window stays put ("don't make the
    // user feel the chat is gone"); only tear down the live stream and mark it
    // 'stopped'. 'stopped' shows the "Type to resume conversation..." input,
    // draws no Stop button, and isn't in the wantsLiveStream set, so it won't
    // trigger a doomed SSE reconnect to a session the server forgot. The user
    // can still ✕-close it deliberately (closeAgentTab). [keep-window-open]
    const serverSids = new Set(sessions.map(s => s.session_id));
    // Read-only reconstructed sessions (deep-link tap on a dead session) are
    // intentionally client-injected and will never be in serverSids — don't
    // touch them or a background poll would disturb the tab mid-read.
    // Temp sessions (_pending_*) are optimistic pre-POST entries — the server
    // doesn't know about them yet; exclude them so a concurrent fetchAgentStatus
    // doesn't race-detach the temp session and cause a spurious BLOCKED flash.
    // Locally-running sessions are also protected: a fetchAgentStatus response
    // initiated BEFORE dispatch may arrive after the temp→real promotion and
    // carry an empty session list, which would race-detach the newly promoted
    // real session ID (not _pending_ any more) and cause a BLOCKED flash.
    const _isInjectedRO = sid => !!(agentStatusCache[sid] || {})._readOnlyRevived;
    const _isPending = sid => (sid || '').startsWith('_pending_');
    const _isLocallyRunning = sid => (agentStatusCache[sid] || {}).status === 'running';
    const _isDetached = h => h.projectId === projectId && !serverSids.has(h.sessionId) && !_isInjectedRO(h.sessionId) && !_isPending(h.sessionId) && !_isLocallyRunning(h.sessionId);
    const detachedEntries = agentHistory.filter(_isDetached);
    for (const det of detachedEntries) {
      // Tear down only the live resources; KEEP the entry, its output buffer,
      // and its status cache (the latter holds claudeSessionId, needed to
      // resume, and is what agentPanelHTML reads to render the active session).
      if (agentEventSources[det.sessionId]) {
        agentEventSources[det.sessionId].close();
        delete agentEventSources[det.sessionId];
      }
      if (agentSSEWatchdogs[det.sessionId]) {
        clearInterval(agentSSEWatchdogs[det.sessionId]);
        delete agentSSEWatchdogs[det.sessionId];
      }
      det.status = 'stopped';
      if (agentStatusCache[det.sessionId]) agentStatusCache[det.sessionId].status = 'stopped';
    }
    // activeAgentTab is intentionally NOT cleared for a detached session — the
    // user stays on the conversation they were reading; it just becomes
    // resumable in place. (agentPanelHTML still drops a selection that points to
    // a session no longer in the tab list, e.g. one the user ✕-closed.)

    // Auto-select a tab when none is active. DESKTOP: always (classic tab
    // strip — a tab should be open). MOBILE: only a single conversation
    // (→ direct chat); with >1 the drill-down list is the home view and
    // auto-selecting on every background sync would override
    // openProjectModal's list-first reset and yank the user into a chat the
    // instant the list appears (the "flashes then jumps" bug).
    if (sessions.length > 0 && !activeAgentTab[projectId]) {
      const visible = sessions.filter(s => !s.hivemind_ws_id);
      if (!isMobileChatList() || visible.length <= 1) {
        const running = visible.find(s => s.status === 'running' || s.status === 'idle');
        activeAgentTab[projectId] = running ? running.session_id : (visible[0] || sessions[0]).session_id;
      }
    }
    if (sessions.length > 0 || detachedEntries.length > 0) {
      refreshModal();
      renderAgentConsole();
    }
  } catch(e) {}
}


// ── interop: window re-exposure for inline/generated/cross-module callers ──
window._maybeTagMobileClient = _maybeTagMobileClient;
window._composerProvider = _composerProvider;
window.getIncognitoFor = getIncognitoFor;
window.isHivemindWorker = isHivemindWorker;
window.getProjectSessions = getProjectSessions;
window.getProjectTabSessions = getProjectTabSessions;
window.scheduleHideHmPopover = scheduleHideHmPopover;
window.cancelHideHmPopover = cancelHideHmPopover;
window.agentPanelHTML = agentPanelHTML;
window.backToConvList = backToConvList;
window.switchAgentTab = switchAgentTab;
window.getDefaultResumeId = getDefaultResumeId;
window.selectResumeSession = selectResumeSession;
window.appendAgentLine = appendAgentLine;
window.showTypingIndicator = showTypingIndicator;
window.hideTypingIndicator = hideTypingIndicator;
window._cachePendingQuestion = _cachePendingQuestion;
window._rerenderPendingQuestions = _rerenderPendingQuestions;
window.renderAgentQuestion = renderAgentQuestion;
window.sendFollowup = sendFollowup;
window.stopAgent = stopAgent;
window.refreshProjectBacklog = refreshProjectBacklog;
window.fetchAgentStatus = fetchAgentStatus;
window.approvePlan = approvePlan;
window.mcBackFromConv = mcBackFromConv;
window.newAgentTab = newAgentTab;
window.setComposerProvider = setComposerProvider;
window.showHmWorkerPopover = showHmWorkerPopover;
window.submitQuestionAnswer = submitQuestionAnswer;
window.toggleIncognito = toggleIncognito;
