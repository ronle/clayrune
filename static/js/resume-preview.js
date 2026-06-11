let sseRetryCount = {};       // session_id → number of consecutive reconnect attempts
// ── Resume-conversation inline preview ───────────────────────────────────────
// Shows the selected prior conversation's chat in the dead space below the
// composer on the +New / dispatch screen. Reuses the transcript endpoint and
// the shared _transcriptCache (same data the pop-out viewer uses).
const _convPreviewLoading = new Set();   // csids with an in-flight fetch
const _convPreviewScrolled = new Set();  // `${projectId}|${csid}` already auto-scrolled to latest

function _convPreviewBodyHTML(data) {
  if (!data) return '<div class="conv-preview-empty">No preview available.</div>';
  if (data.__error) return `<div class="conv-preview-empty">Couldn't load preview: ${esc(data.__error)}</div>`;
  const all = (data.messages || []).filter(m => m.role === 'user' || m.role === 'assistant');
  if (!all.length) {
    // Non-JSONL providers return raw log_lines instead of structured messages.
    if (data.log_lines && data.log_lines.length) {
      return `<pre class="cp-raw">${esc(data.log_lines.join('\n'))}</pre>`;
    }
    return '<div class="conv-preview-empty">Empty conversation.</div>';
  }
  const CAP = 40;
  const truncated = all.length > CAP;
  const msgs = truncated ? all.slice(-CAP) : all;
  const head = truncated
    ? `<div class="cp-trunc">Showing the last ${CAP} of ${all.length} messages — Open ↗ for the full transcript</div>`
    : '';
  return head + msgs.map(m => {
    const who = m.role === 'user' ? 'You' : 'Agent';
    return `<div class="cp-msg ${m.role === 'user' ? 'user' : 'assistant'}">
      <div class="cp-msg-label">${who}</div>
      <div class="cp-msg-text">${esc(m.text || '')}</div>
    </div>`;
  }).join('');
}

// Resolve a human label for a conversation from whatever cache has it.
function _convPreviewLabel(projectId, csid) {
  const convo = (conversationsCache[projectId] || []).find(c => c.claude_session_id === csid);
  if (convo) {
    const l = (convo.label || convo.last_user || convo.first_user || '').trim();
    if (l) return l;
  }
  const entry = (agentLogCache[projectId] || []).find(e => e.claude_session_id === csid);
  return entry ? (entry.task || '').trim() : '';
}

function convPreviewHTML(projectId) {
  const csid = pendingResumeId[projectId] || null;
  if (!csid) return '';  // Fresh session selected → no preview, dead space returns
  const label = _convPreviewLabel(projectId, csid);
  const cached = _transcriptCache[csid];
  const bodyHTML = cached ? _convPreviewBodyHTML(cached) : '<div class="conv-preview-empty">Loading preview…</div>';
  // After paint: fetch if needed, then repaint + scroll to latest.
  setTimeout(() => loadConvPreview(projectId, csid), 0);
  // data-painted is set ONLY when the body already holds this csid's content,
  // so loadConvPreview / refreshModalById can tell a real paint from a "Loading…"
  // placeholder and avoid a redundant repaint that would reset the scroll.
  return `<div class="conv-preview">
    <div class="conv-preview-head">
      <span class="conv-preview-title">Preview</span>
      <span class="conv-preview-sub" title="${esc(label)}">${esc(label.substring(0, 100)) || '(empty conversation)'}</span>
      <button class="conv-preview-open" onclick="previewOpenFull('${esc(projectId)}','${esc(csid)}')" title="Open full transcript">Open &#8599;</button>
    </div>
    <div class="conv-preview-body" id="conv-preview-body-${esc(projectId)}"
      data-csid="${esc(csid)}"${cached ? ` data-painted="${esc(csid)}"` : ''}>${bodyHTML}</div>
  </div>`;
}

async function loadConvPreview(projectId, csid) {
  if ((pendingResumeId[projectId] || null) !== csid) return;  // selection moved on
  if (!_transcriptCache[csid]) {
    if (_convPreviewLoading.has(csid)) return;  // a fetch is already in flight
    _convPreviewLoading.add(csid);
    try {
      const res = await fetch(API_BASE + `/api/project/${encodeURIComponent(projectId)}/transcript/${encodeURIComponent(csid)}`);
      if (res.ok) {
        _transcriptCache[csid] = await res.json();
      } else {
        const err = await res.json().catch(() => ({}));
        _transcriptCache[csid] = { __error: err.error || res.statusText || 'not found' };
      }
    } catch (e) {
      _transcriptCache[csid] = { __error: 'load failed' };
    } finally {
      _convPreviewLoading.delete(csid);
    }
  }
  if ((pendingResumeId[projectId] || null) !== csid) return;  // re-check after await
  const body = document.getElementById(`conv-preview-body-${projectId}`);
  if (!body) return;
  // Repaint only when the body isn't already showing this csid's content
  // (placeholder → loaded, or csid changed). Skipping the no-op repaint keeps
  // the user's scroll position intact across the frequent refreshModal ticks.
  if (body.dataset.painted !== csid) {
    body.innerHTML = _convPreviewBodyHTML(_transcriptCache[csid]);
    body.dataset.painted = csid;
  }
  // Auto-scroll to the latest exchange once per selection — that's the
  // "where we left off" context you want when resuming.
  const sk = projectId + '|' + csid;
  if (!_convPreviewScrolled.has(sk)) {
    body.scrollTop = body.scrollHeight;
    _convPreviewScrolled.add(sk);
  }
}

// "Open ↗" — hand off to the full pop-out transcript viewer.
function previewOpenFull(projectId, csid) {
  openTranscriptViewer(projectId, csid, _convPreviewLabel(projectId, csid));
}


function sessionPickerHTML(projectId) {
  // Prefer transcript-derived conversations (survives reboots, includes interrupted sessions).
  // Fall back to the completion log if the endpoint hasn't returned yet.
  const convos = conversationsCache[projectId] || [];
  const logEntries = agentLogCache[projectId] || [];

  // Running sessions already visible as tabs — exclude from picker
  const runningSessions = getProjectSessions(projectId);
  const runningResumeIds = new Set(
    runningSessions.filter(h => h.resumedFrom).map(h => h.resumedFrom)
  );

  // Build unified list keyed by claude_session_id. Convo entries win (richer metadata).
  const byId = new Map();
  for (const c of convos) {
    const csid = c.claude_session_id;
    if (!csid || runningResumeIds.has(csid)) continue;
    byId.set(csid, {
      csid,
      label: (c.label || c.last_user || c.first_user || '').trim() || '(empty conversation)',
      status: c.status || '',
      ts: c.ts_relative || '',
      turns: c.turns || 0,
      source: 'transcript',
    });
  }
  for (const e of logEntries) {
    const csid = e.claude_session_id;
    if (!csid || e.hivemind_ws_id || runningResumeIds.has(csid) || byId.has(csid)) continue;
    byId.set(csid, {
      csid,
      label: (e.task || '').trim() || 'Session',
      status: e.status || '',
      ts: e.ts_relative || e.ts || '',
      turns: e.num_turns || 0,
      source: 'log',
    });
  }

  const available = Array.from(byId.values());
  if (available.length === 0) return '';

  const selected = pendingResumeId[projectId] || null;

  const opts = available.slice(0, 12).map(item => {
    const csid = item.csid;
    const isSelected = selected === csid;
    const label = item.label.substring(0, 80);
    const statusDot = item.status
      ? `<span class="agent-status-dot ${esc(item.status)}" title="${esc(item.status)}"></span>`
      : '';
    const meta = [item.ts, item.turns ? `${item.turns} turn${item.turns !== 1 ? 's' : ''}` : '']
      .filter(Boolean).join(' · ');
    return `<div class="session-picker-opt ${isSelected ? 'selected' : ''}"
      onclick="selectResumeSession('${esc(projectId)}','${esc(csid)}')"
      title="${esc(item.label)}">
      <div class="sp-radio"></div>
      <div class="sp-label">
        <div class="sp-task">${statusDot}${esc(label)}</div>
        <div class="sp-meta">${esc(meta)}</div>
      </div>
    </div>`;
  }).join('');

  const freshSelected = !selected;
  return `<div class="session-picker">
    <div class="session-picker-label">Resume a prior conversation (label = user's last message)</div>
    <div class="session-picker-options">
      <div class="session-picker-opt ${freshSelected ? 'selected' : ''}"
        onclick="selectResumeSession('${esc(projectId)}','')">
        <div class="sp-radio"></div>
        <div class="sp-label"><div class="sp-task">Fresh session (no prior context)</div></div>
      </div>
      ${opts}
    </div>
  </div>`;
}

function closeAgentTab(projectId, sessionId) {
  // Close EventSource immediately
  if (agentEventSources[sessionId]) {
    agentEventSources[sessionId].close();
    delete agentEventSources[sessionId];
  }
  if (agentSSEWatchdogs[sessionId]) { clearInterval(agentSSEWatchdogs[sessionId]); delete agentSSEWatchdogs[sessionId]; }

  // Zero-gap picker update: snapshot csid + last user line BEFORE we nuke local
  // state, upsert into conversationsCache so the picker reflects the close
  // immediately. Then reload from server to reconcile (authoritative status / ts).
  const cached = agentStatusCache[sessionId] || {};
  const histEntry = agentHistory.find(h => h.sessionId === sessionId);
  const csid = cached.claudeSessionId || (histEntry && histEntry.resumedFrom) || '';
  if (csid) {
    const lastUser = _lastUserFromBuffer(sessionId) || (histEntry && histEntry.task) || '';
    upsertConversationCache(projectId, csid, lastUser, 'stopped');
  }

  // Kill process + remove session from backend
  fetch(API_BASE + `/api/project/${projectId}/agent/session`, {
    method: 'DELETE',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ session_id: sessionId })
  }).then(() => {
    // Reconcile the optimistic cache entry with the authoritative list (real
    // ts, real turn count, real status from the transcript).
    loadConversations(projectId);
  }).catch(() => {});

  // Remove from frontend state
  delete agentStatusCache[sessionId];
  delete agentOutputBuffers[sessionId];
  delete agentServerLines[sessionId];
  agentHistory = agentHistory.filter(h => h.sessionId !== sessionId);
  acOpenSessions.delete(sessionId);

  // Drill-down semantics: don't auto-jump into another conversation. Drop
  // the selection and let agentPanelHTML decide — back to the list if >1
  // remain, direct chat if exactly 1, dispatch screen if none.
  delete activeAgentTab[projectId];
  delete agentConvNew[projectId];
  refreshModal();
  renderAgentConsole();
}

async function dispatchAgent(projectId) {
  const input = document.getElementById(`agent-task-${projectId}`);
  const task = input.value.trim();

  // Check if we're resuming a prior session
  const resumeId = pendingResumeId[projectId] || null;
  delete pendingResumeId[projectId];

  // Require a prompt for fresh sessions; resume can go without one
  if (!task && !resumeId) { input.focus(); return; }
  input.value = '';
  if (input.id) delete textareaValues[input.id];

  // Compute all display state synchronously before any async work so the UI
  // can switch to the chat view immediately (the dispatch POST + auto-router
  // classifier run server-side and add ~0.5s before a response arrives).
  const displayTask = task || 'Continue where we left off.';
  const pName = (allProjects.find(x => x.id === projectId) || {}).name || projectId;
  const _projForProv = allProjects.find(x => x.id === projectId);
  const _chosenProvider = _composerProvider(_projForProv);
  const incognitoFlag = !!getIncognitoFor(projectId);
  // Match the server's seeded log_lines format so the SSE/reconcile delivery
  // of the same line dedupes against this optimistic insert. The server
  // writes `> {user_label}: {task}` to log_lines on fresh dispatch (see
  // server.py:6105 / 6272); if the prefix here doesn't match, BOTH the
  // optimistic line and the server line end up in the buffer → the user
  // sees their first message echoed twice on a brand-new conversation
  // (followups don't have this — sendFollowup has no optimistic buffer
  // write; the dedup goes through the .agent-echo DOM-class path instead).
  const userLabel = (window._globalConfig && (window._globalConfig.user_name || window._globalConfig.user_display_name)) || 'User';
  const prefix = resumeId ? `> [resuming prior session] ${displayTask}` : `> ${userLabel}: ${displayTask}`;

  // Use a temp session ID to show the chat view immediately. It gets promoted
  // to the real server-assigned ID once the POST returns.
  const tempSessionId = `_pending_${Date.now()}`;
  agentOutputBuffers[tempSessionId] = [prefix];
  // Treat the optimistic line as if it had already arrived from the server,
  // so connectAgentStream's `?since=` cursor skips it and reconcile's
  // `serverLines.length > have` check doesn't re-deliver it. Without this,
  // matching the format alone isn't enough — every replay would still push
  // a second copy.
  agentServerLines[tempSessionId] = 1;
  agentStatusCache[tempSessionId] = { status: 'running', task: displayTask, projectId, startedAt: new Date().toISOString(), claudeSessionId: resumeId || '', incognito: incognitoFlag, provider: _chosenProvider };
  agentHistory.unshift({ projectId, sessionId: tempSessionId, projectName: pName, task: displayTask, status: 'running', startedAt: new Date().toISOString(), resumedFrom: resumeId || null, incognito: incognitoFlag, provider: _chosenProvider });
  activeAgentTab[projectId] = tempSessionId;
  delete agentConvNew[projectId];  // dispatched → drill into the new convo
  refreshModal();
  renderAgentConsole();

  // Upload any pasted images and build final task
  const imagePaths = await uploadAgentImages(projectId);
  const fullTask = buildTaskWithImages(displayTask, imagePaths);
  clearAgentImages(projectId);

  const body = { task: fullTask };
  if (resumeId) body.resume_conversation_id = resumeId;
  if (incognitoFlag) body.incognito = true;
  // Per-conversation provider — the composer dropdown is authoritative. The
  // project's `provider` field is only the default seed for this picker.
  body.provider = _chosenProvider;
  _maybeTagMobileClient(body);

  try {
    const res = await fetch(API_BASE + `/api/project/${projectId}/agent/dispatch`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(body)
    });
    const data = await res.json();
    if (!data.ok) {
      // Dispatch failed — clean up temp session and restore the dispatch screen
      delete agentOutputBuffers[tempSessionId];
      delete agentServerLines[tempSessionId];
      delete agentStatusCache[tempSessionId];
      const errIdx = agentHistory.findIndex(h => h.sessionId === tempSessionId);
      if (errIdx >= 0) agentHistory.splice(errIdx, 1);
      delete activeAgentTab[projectId];
      agentConvNew[projectId] = true;
      input.value = task;
      refreshModal();
      alert(data.error || 'Dispatch failed');
      return;
    }
    const sessionId = data.session_id;

    // Promote temp → real session ID
    const savedBuf = agentOutputBuffers[tempSessionId] || [prefix];
    delete agentOutputBuffers[tempSessionId];
    delete agentServerLines[tempSessionId];
    delete agentStatusCache[tempSessionId];
    const histEntry = agentHistory.find(h => h.sessionId === tempSessionId);
    if (histEntry) histEntry.sessionId = sessionId;
    if (activeAgentTab[projectId] === tempSessionId) activeAgentTab[projectId] = sessionId;

    agentOutputBuffers[sessionId] = savedBuf;
    agentServerLines[sessionId] = 1;
    agentStatusCache[sessionId] = { status: 'running', task: displayTask, projectId, startedAt: new Date().toISOString(), claudeSessionId: resumeId || '', incognito: incognitoFlag, provider: _chosenProvider };

    // Zero-gap: if resuming a known prior conversation, patch its last_user now.
    // For a fresh session, we don't know the claude_session_id yet; it will be
    // populated by the next fetchAgentStatus tick and picked up on sendFollowup.
    if (resumeId && task) {
      upsertConversationCache(projectId, resumeId, task, 'running');
    }

    refreshModal();
    renderAgentConsole();
    connectAgentStream(projectId, sessionId);
    // Resume → the server preloaded log_lines from the prior .jsonl transcript
    // so the chat shows the full history, not just the new prompt. Pull that
    // server-side buffer in now so the user sees the prior conversation
    // immediately instead of waiting for the next status poll.
    if (resumeId) {
      try { await fetchAgentStatus(projectId); } catch (_) {}
      refreshModal();
    }
  } catch(e) {
    delete agentOutputBuffers[tempSessionId];
    delete agentServerLines[tempSessionId];
    delete agentStatusCache[tempSessionId];
    const errIdx = agentHistory.findIndex(h => h.sessionId === tempSessionId);
    if (errIdx >= 0) agentHistory.splice(errIdx, 1);
    delete activeAgentTab[projectId];
    agentConvNew[projectId] = true;
    input.value = task;
    refreshModal();
    alert('Failed to dispatch: ' + e.message);
  }
}



// Per-session lock so concurrent reconcile triggers don't double-apply.
let _reconcileBusy = {};

// Compare server's authoritative log_lines against our SSE-received count and
// silently apply any missed lines. Mobile-network drops, page-suspension SSE
// kills, and stale `since=` cursors can all leave a hole where one or more
// `output` events never reached onmessage — most visibly, a follow-up prompt
// that never appears in the chat even though the server received and replied
// to it. This helper gives us a deterministic catch-up path that runs at the
// moments a hole is most likely to have just opened.
async function _reconcileAgentBuffer(projectId, sessionId) {
  if (!projectId || !sessionId) return;
  if (_reconcileBusy[sessionId]) return;
  _reconcileBusy[sessionId] = true;
  try {
    const r = await fetch(API_BASE + `/api/project/${projectId}/agent/status`);
    if (!r.ok) return;
    const data = await r.json();
    const sess = (data.sessions || []).find(s => s.session_id === sessionId);
    if (!sess) return;
    // Status reconciliation. SSE can silently buffer on mobile WebView / CF
    // Tunnel — turn_start / status events never reach the client and the
    // pill stays frozen on the prior (often "Completed") state. Sync from
    // the authoritative /agent/status here so the UI catches up regardless.
    const serverStatus = sess.status;
    const cached = agentStatusCache[sessionId];
    if (serverStatus && cached && cached.status !== serverStatus) {
      cached.status = serverStatus;
      if (sess.usage) cached.usage = sess.usage;
      if (sess.cost_usd !== undefined) cached.cost_usd = sess.cost_usd;
      if (sess.num_turns !== undefined) cached.num_turns = sess.num_turns;
      updateHistoryStatus(sessionId, serverStatus);
      updateAgentStatusUI(sessionId, serverStatus);
    }
    // Question-form reconciliation. A `type: question` SSE event can be
    // silently dropped the same way — agent ends up waiting with no form
    // visible while the chat shows "Completed". Server now mirrors
    // pending_questions on /agent/status; replay them through
    // renderAgentQuestion which dedupes by question_id, so re-delivery is
    // safe even if the SSE eventually arrives.
    if (sess.waiting_for_question && cached) {
      cached.waitingForQuestion = true;
      const _hQ = agentHistory.find(h => h.sessionId === sessionId);
      if (_hQ) _hQ.waitingForQuestion = true;
      // Cache the question payloads so a panel rebuild (tab switch / reopen) can
      // re-render the form immediately instead of waiting for the next poll.
      cached.pendingQuestions = (sess.pending_questions || []).map(pq => ({
        questions: pq.questions || [], question_id: pq.question_id || ''
      }));
      for (const pq of (sess.pending_questions || [])) {
        try { renderAgentQuestion(sessionId, projectId, pq.questions || [], pq.question_id || ''); }
        catch (_) {}
      }
    }
    const serverLines = sess.log_lines || [];
    const have = agentServerLines[sessionId] || 0;
    if (serverLines.length <= have) return;  // buffer in sync — nothing more to recover
    const missing = serverLines.slice(have);
    for (const text of missing) {
      // Race guard: SSE may have delivered the same line between fetch start
      // and now. Re-check our cursor against the index this iteration targets.
      if ((agentServerLines[sessionId] || 0) >= have + missing.indexOf(text) + 1) continue;
      if (!agentOutputBuffers[sessionId]) agentOutputBuffers[sessionId] = [];
      agentOutputBuffers[sessionId].push(text);
      agentServerLines[sessionId] = (agentServerLines[sessionId] || 0) + 1;
      // Mirror the SSE onmessage echo-dedup so a recovered `> Ron:` line
      // wipes the local echo that sendFollowup left in the DOM.
      if (text && text.trimStart().startsWith('> ')) {
        const el = document.getElementById(`agent-output-${sessionId}`);
        if (el) {
          const echo = el.querySelector('.agent-echo');
          if (echo) echo.remove();
        }
      }
      appendAgentLine(sessionId, text);
    }
  } catch (_) {
    // silent — reconciliation is best-effort
  } finally {
    _reconcileBusy[sessionId] = false;
  }
}

// Repaint a session's agent-output element from its full buffer. Fixes a render
// gap: when lines are recovered into the buffer wholesale (a /agent/status sync)
// while refreshModal has PRESERVED the existing agent-output node for scroll/perf,
// the node is never rebuilt from the grown buffer and the recovered lines stay
// invisible ("agent text didn't appear even though there was progress"). A
// clear-and-reappend brings the DOM back in sync with the buffer. Call only on
// buffer growth for a non-streaming session — never on the hot incremental path.
function _repaintAgentOutput(sessionId) {
  const el = document.getElementById(`agent-output-${sessionId}`);
  if (!el) return false;
  const buf = agentOutputBuffers[sessionId] || [];
  el.innerHTML = '';
  delete el.dataset.scrollInitialized;
  for (const line of buf) appendAgentLine(sessionId, line);
  return true;
}

function connectAgentStream(projectId, sessionId) {
  if (agentEventSources[sessionId]) {
    agentEventSources[sessionId].close();
  }
  if (agentSSEWatchdogs[sessionId]) {
    clearInterval(agentSSEWatchdogs[sessionId]);
    delete agentSSEWatchdogs[sessionId];
  }

  // Tell server how many lines we already have, so it doesn't replay them.
  // Use agentServerLines (actual server-received count) not agentOutputBuffers.length
  // which includes local echo lines the server doesn't know about.
  const since = agentServerLines[sessionId] || 0;
  const es = new EventSource(API_BASE + `/api/project/${projectId}/agent/stream?session=${sessionId}&since=${since}`);
  agentEventSources[sessionId] = es;

  // Heartbeat watchdog: if no SSE event (data or heartbeat) arrives within 30s,
  // the connection has silently died. Force reconnect.
  let lastEventTime = Date.now();
  agentLastEventAt[sessionId] = lastEventTime;  // mirror for the freshness reconciler
  agentSSEWatchdogs[sessionId] = setInterval(() => {
    if (Date.now() - lastEventTime > 30000) {
      console.warn(`SSE watchdog: no event for 30s on session ${sessionId.slice(0,8)}, reconnecting`);
      clearInterval(agentSSEWatchdogs[sessionId]);
      delete agentSSEWatchdogs[sessionId];
      es.close();
      delete agentEventSources[sessionId];
      const cached = agentStatusCache[sessionId];
      if (cached && (cached.status === 'running' || cached.status === 'idle')) {
        connectAgentStream(projectId, sessionId);
        // A silent SSE death often means we missed lines while the connection
        // was zombie. Reconcile after the new stream has had a moment to flow.
        setTimeout(() => _reconcileAgentBuffer(projectId, sessionId), 1500);
      }
    }
  }, 10000);

  es.onmessage = (event) => {
    lastEventTime = Date.now();
    agentLastEventAt[sessionId] = lastEventTime;  // mirror for the freshness reconciler
    try {
      const msg = JSON.parse(event.data);
      if (msg.type === 'output') {
        sseRetryCount[sessionId] = 0;  // successful data — reset retry counter
        if (followupTimeouts[sessionId]) { clearTimeout(followupTimeouts[sessionId].timerId); delete followupTimeouts[sessionId]; }
        if (!agentOutputBuffers[sessionId]) agentOutputBuffers[sessionId] = [];
        agentOutputBuffers[sessionId].push(msg.text);
        agentServerLines[sessionId] = (agentServerLines[sessionId] || 0) + 1;
        // Cap buffer to prevent unbounded memory growth
        if (agentOutputBuffers[sessionId].length > 2000) {
          agentOutputBuffers[sessionId] = agentOutputBuffers[sessionId].slice(-1500);
        }
        // Detect terminal launch marker from agent
        const termMatch = msg.text && msg.text.match(/^\[terminal:([^:]+):(.+)\]$/);
        if (termMatch) {
          const [, termSessionId, termCommand] = termMatch;
          if (!terminalDismissed.has(termSessionId) && !openModals.has(`__terminal_${termSessionId}`)) {
            // Verify session still exists on server before opening pop-out
            fetch(API_BASE + `/api/project/${projectId}/terminal/status`)
              .then(r => r.json())
              .then(data => {
                const exists = (data.sessions || []).some(s => s.session_id === termSessionId);
                if (exists) openTerminalPopout(projectId, termSessionId, termCommand);
                else terminalDismissed.add(termSessionId);
              }).catch(() => terminalDismissed.add(termSessionId));
          }
          return; // don't render this line in agent chat
        }
        // Show toast for auto-fresh session notification
        if (msg.text && msg.text.startsWith('[Session transcript too large')) {
          const pName = (allProjects.find(p => p.id === projectId) || {}).name || projectId;
          showToast(`Session restarted fresh for "${pName}" — previous transcript was too large`);
        }
        // Remove local echo if server sends the real user prompt
        if (msg.text && msg.text.trimStart().startsWith('> ')) {
          const el = document.getElementById(`agent-output-${sessionId}`);
          if (el) {
            const echo = el.querySelector('.agent-echo');
            if (echo) echo.remove();
          }
        }
        appendAgentLine(sessionId, msg.text);
        updateConsoleOutput(sessionId);
        // Update live activity ticker for tool lines
        if (msg.text && msg.text.startsWith('[tool:')) {
          for (const id of [`agent-activity-${sessionId}`, `ac-activity-${sessionId}`]) {
            const el = document.getElementById(id);
            if (el) el.textContent = msg.text;
          }
        } else if (msg.text && !msg.text.startsWith('[') && !msg.text.startsWith('\n---')) {
          // Text output — clear activity (agent is now responding)
          for (const id of [`agent-activity-${sessionId}`, `ac-activity-${sessionId}`]) {
            const el = document.getElementById(id);
            if (el) el.textContent = '';
          }
        }
        // Agent wrote a TodoWrite → server synced into backlog → refresh that project's backlog live
        if (msg.text && msg.text.startsWith('[backlog: synced')) {
          refreshProjectBacklog(projectId);
        }
      } else if (msg.type === 'question' && msg.questions) {
        if (agentStatusCache[sessionId]) agentStatusCache[sessionId].waitingForQuestion = true;
        const _hQ = agentHistory.find(h => h.sessionId === sessionId);
        if (_hQ) _hQ.waitingForQuestion = true;
        // A `question` event only ever fires when an agent actually emitted a
        // structured AskUserQuestion. Providers that lack the tool (Gemini etc.)
        // have the instruction stripped from their prompt and never reach here,
        // so the event itself proves the question is renderable. Gating on the
        // *project's* configured provider was wrong — a Claude session running
        // in a Gemini-configured project would wrongly fall back to plain text.
        // The form's answer round-trips as a normal follow-up message, which is
        // provider-agnostic. Render it directly; renderAgentQuestion dedupes by
        // question_id so SSE reconnects don't stack duplicate cards.
        renderAgentQuestion(sessionId, projectId, msg.questions, msg.question_id || '');
        _cachePendingQuestion(sessionId, msg.questions, msg.question_id || '');
        refreshModal();
      } else if (msg.type === 'turn_start') {
        // Phase 2 (2026-04-27): server tells us a new turn started so we can
        // flip the status UI without optimistic cache writes in sendFollowup.
        // turn_start is what we're waiting for in _sendInFlight — clear it
        // here so subsequent terminal events can flow normally.
        delete _sendInFlight[sessionId];
        _turnStartAcked[sessionId] = true;
        if (agentStatusCache[sessionId]) {
          agentStatusCache[sessionId].status = 'running';
          agentStatusCache[sessionId].waitingForPlanApproval = false;
          agentStatusCache[sessionId].waitingForQuestion = false;
          agentStatusCache[sessionId].pendingQuestions = [];
        }
        delete _answeredQuestionIds[sessionId];  // new turn → answered-set is moot
        updateHistoryStatus(sessionId, 'running');
        updateAgentStatusUI(sessionId, 'running');
        // NOTE: deliberately NO refreshModal() here. It does
        // content.innerHTML = modalContentHTML(p), which detaches/recreates
        // the chat textarea on EVERY turn — measured root cause of the mobile
        // IME death (dead autocorrect/predictive, multi-press backspace),
        // text-size reset, and ~205ms/keystroke paint. updateAgentStatusUI +
        // updateHistoryStatus already update the status dot/label/tab in
        // place. The only thing lost is the input placeholder text flipping
        // ("Interrupt…" vs "Send follow-up…") — cosmetic, worth it.
        renderAgentConsole();
      } else if (msg.type === 'turn_complete') {
        if (_sendInFlight[sessionId]) {
          // Stale turn_complete from the prior idle state — the new turn has
          // not yet started server-side. Ignore so we don't close SSE early
          // (which would prevent us from seeing the imminent turn_start).
          return;
        }
        // Blocked-on-user guard: if this session is waiting on an
        // AskUserQuestion / plan approval, the turn is NOT complete — the agent
        // is parked waiting for input. Ignore turn_complete so we keep the SSE
        // open (the `question` event is delivered/re-delivered on it) and don't
        // clear the asking-state. The server now suppresses turn_complete in
        // this state too; this guards event-ordering and old-server races that
        // would otherwise drop the question form (chat shows "Completed" with
        // no form until a resync). See server SSE loop `waiting_on_user`.
        {
          const _qc = agentStatusCache[sessionId];
          if (_qc && (_qc.waitingForQuestion || _qc.waitingForPlanApproval)) return;
        }
        if (followupTimeouts[sessionId]) { clearTimeout(followupTimeouts[sessionId].timerId); delete followupTimeouts[sessionId]; }
        // Mode B: turn finished, process still alive. We close the SSE here to free
        // a browser per-origin connection slot (Chromium caps at 6 over HTTP/1.1).
        // sendFollowup() reconnects on the next message; agent process is unaffected.
        if (agentStatusCache[sessionId]) {
          agentStatusCache[sessionId].status = 'idle';
          agentStatusCache[sessionId].waitingForPlanApproval = false;
          agentStatusCache[sessionId].waitingForQuestion = false;
          if (msg.usage) agentStatusCache[sessionId].usage = msg.usage;
          if (msg.cost_usd !== undefined) agentStatusCache[sessionId].cost_usd = msg.cost_usd;
          if (msg.num_turns !== undefined) agentStatusCache[sessionId].num_turns = msg.num_turns;
        }
        updateHistoryStatus(sessionId, 'idle');
        updateAgentStatusUI(sessionId, 'idle');
        es.close();
        delete agentEventSources[sessionId];
        delete sseRetryCount[sessionId];
        if (agentSSEWatchdogs[sessionId]) { clearInterval(agentSSEWatchdogs[sessionId]); delete agentSSEWatchdogs[sessionId]; }
        // No refreshModal() — same reason as turn_start. Session is idle but
        // alive; the input must stay put so the IME survives. Status dot
        // already updated in place above.
        renderAgentConsole();
      } else if (msg.type === 'status') {
        // 'stopped' is user-initiated and always authoritative even mid-send.
        // For other statuses, suppress while a send is in flight (same
        // staleness reasoning as turn_complete above).
        if (_sendInFlight[sessionId] && msg.status !== 'stopped') {
          return;
        }
        // A non-terminal 'idle' must not tear down a session that's blocked on
        // a question / plan approval (would close SSE + clear asking-state and
        // drop the form). Ignore it; the waiting state is resolved by
        // turn_start (answer sent) or a real terminal status.
        if (msg.status === 'idle') {
          const _qc = agentStatusCache[sessionId];
          if (_qc && (_qc.waitingForQuestion || _qc.waitingForPlanApproval)) return;
        }
        if (followupTimeouts[sessionId]) { clearTimeout(followupTimeouts[sessionId].timerId); delete followupTimeouts[sessionId]; }
        // Terminal status — close SSE
        if (agentStatusCache[sessionId]) {
          agentStatusCache[sessionId].status = msg.status;
          agentStatusCache[sessionId].waitingForPlanApproval = false;
          agentStatusCache[sessionId].waitingForQuestion = false;
          if (msg.usage) agentStatusCache[sessionId].usage = msg.usage;
          if (msg.cost_usd !== undefined) agentStatusCache[sessionId].cost_usd = msg.cost_usd;
          if (msg.num_turns !== undefined) agentStatusCache[sessionId].num_turns = msg.num_turns;
        }
        updateHistoryStatus(sessionId, msg.status);
        // Surface a fresh auth banner if the session ended in error (claude's
        // not-logged-in path exits with rc!=0, which the server emits here as
        // status='error' — NOT as msg.type='error').
        if (msg.status === 'error') {
          refreshAuthStatus();
        }
        es.close();
        delete agentEventSources[sessionId];
        delete sseRetryCount[sessionId];
        if (agentSSEWatchdogs[sessionId]) { clearInterval(agentSSEWatchdogs[sessionId]); delete agentSSEWatchdogs[sessionId]; }
        delete agentLogCache[projectId];  // invalidate so fresh data (with claude_session_id) loads
        delete conversationsCache[projectId];  // refresh transcript-based list too
        if (agentLogOpen[projectId]) loadAgentLog(projectId);  // re-fetch if panel is open
        loadConversations(projectId);
        refreshModal();
        renderAgentConsole();
        refreshSilent();
      } else if (msg.type === 'error') {
        if (_sendInFlight[sessionId]) {
          // SSE opened before POST resolved or session is being revived —
          // close this stale stream silently so the POST-completion reconnect
          // can attach to the live session. Never set status = 'error' here:
          // that would show a spurious BLOCKED pill before the send finishes.
          es.close();
          delete agentEventSources[sessionId];
          if (agentSSEWatchdogs[sessionId]) { clearInterval(agentSSEWatchdogs[sessionId]); delete agentSSEWatchdogs[sessionId]; }
          return;
        }
        if (followupTimeouts[sessionId]) { clearTimeout(followupTimeouts[sessionId].timerId); delete followupTimeouts[sessionId]; }
        if (agentStatusCache[sessionId]) agentStatusCache[sessionId].status = 'error';
        updateHistoryStatus(sessionId, 'error');
        es.close();
        delete agentEventSources[sessionId];
        delete sseRetryCount[sessionId];
        if (agentSSEWatchdogs[sessionId]) { clearInterval(agentSSEWatchdogs[sessionId]); delete agentSSEWatchdogs[sessionId]; }
        delete agentLogCache[projectId];  // invalidate so fresh data loads
        delete conversationsCache[projectId];
        if (agentLogOpen[projectId]) loadAgentLog(projectId);  // re-fetch if panel is open
        loadConversations(projectId);
        refreshModal();
        renderAgentConsole();
        // Surface a fresh auth banner if the failure was caused by a 401.
        refreshAuthStatus();
      } else if (msg.type === 'guardian') {
        const cached = agentStatusCache[sessionId];
        if (cached) {
          cached.guardianState = msg.state;
          cached.circuitBreakerTripped = msg.circuit_breaker || false;
        }
        if (msg.state === 'recovering') {
          showToast('Session Guardian is recovering the agent...', 5000);
        } else if (msg.state === 'needs_attention') {
          showToast('Agent session needs attention \u2014 auto-recovery exhausted', 10000);
        }
        refreshModal();
        renderAgentConsole();
      }
    } catch(e) {}
  };

  es.onerror = () => {
    es.close();
    delete agentEventSources[sessionId];
    if (agentSSEWatchdogs[sessionId]) { clearInterval(agentSSEWatchdogs[sessionId]); delete agentSSEWatchdogs[sessionId]; }
    const cached = agentStatusCache[sessionId];
    if (cached && (cached.status === 'running' || cached.status === 'idle')) {
      // Before the retry/error cascade, check whether the server has been
      // restarted. If so, this dashboard's in-memory state is stale and the
      // right move is to reload — not to mark the session as errored. This
      // catches the case where the user restarted MC from another device
      // (mobile) and this device was just observing.
      _checkServerRestart().then(restarted => {
        if (restarted) return; // _handleServerRestart already triggered the reload overlay
        const retries = (sseRetryCount[sessionId] || 0) + 1;
        sseRetryCount[sessionId] = retries;
        if (retries <= 3) {
          // Retry with increasing delay
          setTimeout(() => connectAgentStream(projectId, sessionId), retries * 2000);
        } else {
          // Too many retries — the server lost the live session (process died
          // or was purged). That's not an error, it's a dropped/resumable
          // session: mark it 'stopped' (resumable, no reconnect) to match the
          // detach path, instead of 'error' which renders as a "Blocked" pill on
          // the way to STOPPED. If the session is actually still alive
          // server-side, the next status poll restores its real running/idle.
          cached.status = 'stopped';
          const hist = agentHistory.find(h => h.sessionId === sessionId);
          if (hist) hist.status = 'stopped';
          delete sseRetryCount[sessionId];
          refreshModal();
          renderAgentConsole();
          refreshSilent();
        }
      });
    }
  };
}

// Escape user-prompt text but inline any absolute image paths as the SAME
// thumbnail+lightbox markup that formatAgentText produces for agent output.
// Used for `agent-line-prompt` bubbles so a "[Screenshot: C:\path\file.jpg]"
// prompt renders the image instead of just the path string. We intentionally
// skip the rest of formatAgentText (markdown headers, bullets, code, etc.)
// for prompts — those would re-render the user's own markup in surprising
// ways. Image rendering only.
function escPromptWithImages(raw) {
  // Strip the `[Screenshot: <path>]` / `[Attachment: <path>]` wrapper that
  // `buildTaskWithImages` adds when the user attaches an image — once we
  // render a thumbnail the wrapper text is redundant clutter. Non-image
  // attachments (PDFs, .txt, etc.) keep the wrapper since they don't render.
  const stripped = raw.replace(
    /\[(?:Screenshot|Attachment):\s+((?:[A-Za-z]:[\\\/]|\/)[^\s"'`<>|\[\]]+?\.(?:png|jpe?g|gif|webp|bmp|svg|ico|tiff?|avif))\s*\]/gi,
    '$1');
  let t = esc(stripped);
  const _imgTokens = [];
  // Same path detection regex as formatAgentText — keep the two in sync.
  t = t.replace(
    /(?<![\w:/%])((?:[A-Za-z]:(?!\/\/)[\\/]|\/)[^\s"'`<>|]+?\.(?:png|jpe?g|gif|webp|bmp|svg|ico|tiff?|avif))(?![A-Za-z0-9])/gi,
    (m, p) => {
      const rawPath = p.replace(/&amp;/g, '&').replace(/&lt;/g, '<')
        .replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'");
      const src = API_BASE + '/api/serve-image?path=' + encodeURIComponent(rawPath);
      const tok = '@@CLImg' + _imgTokens.length + '@@';
      _imgTokens.push(
        `<span class="agent-img-wrap">` +
        `<a class="agent-img-path" href="${src}" target="_blank" rel="noopener">${p}</a>` +
        `<img class="agent-img" src="${src}" alt="" ` +
        `onload="this.classList.add('agent-img-ok')" ` +
        `onerror="this.closest('.agent-img-wrap').classList.add('agent-img-failed');this.remove()" ` +
        `onclick="_openImageViewer(this.src)"></span>`);
      return tok;
    });
  // Swap tokens back in.
  for (let i = 0; i < _imgTokens.length; i++) {
    t = t.replace('@@CLImg' + i + '@@', _imgTokens[i]);
  }
  return t;
}


// ── interop: window re-exposure for inline/generated/cross-module callers ──
window.convPreviewHTML = convPreviewHTML;
window.loadConvPreview = loadConvPreview;
window.sessionPickerHTML = sessionPickerHTML;
window.closeAgentTab = closeAgentTab;
window.dispatchAgent = dispatchAgent;
window._reconcileAgentBuffer = _reconcileAgentBuffer;
window._repaintAgentOutput = _repaintAgentOutput;
window.connectAgentStream = connectAgentStream;
window.escPromptWithImages = escPromptWithImages;
window.previewOpenFull = previewOpenFull;
