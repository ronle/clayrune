// ── Ask Claydo (in-app guide assistant) ───────────────────────────────────
// Floating button → modal chat → POST /api/guide/stream → render answer +
// strip + dispatch [clayrune:...] UI control markers emitted by Claydo.
// Three modes (docs/PROMPT_BUILDER_DESIGN.md): 'ask' (help desk, default),
// 'prompt' (prompt workshop) and 'character' (character workshop) — the
// builder modes hand their artifact back via prompt-ready/character-ready
// markers + the last fenced block of the reply.

// Per-modal-session conversation history. Reset each time the modal is
// opened fresh (i.e. after being closed) and on every mode switch (each
// mode is a different conversation against a different brief).
// Cap at 6 messages (3 exchanges) — anything older falls off the front.
let _claydoHistory = [];
let _claydoMode = 'ask';

// One-time localStorage migration from the previous "Playdo" name. Read the
// old key, write to the new key if not already set, then delete the old.
// Idempotent — safe to run on every page load.
(function _migrateClaydoKeys() {
  for (const [oldK, newK] of [['playdo_opened', 'claydo_opened'],
                              ['playdo_fab_pos', 'claydo_fab_pos']]) {
    try {
      const v = localStorage.getItem(oldK);
      if (v != null) {
        if (localStorage.getItem(newK) == null) localStorage.setItem(newK, v);
        localStorage.removeItem(oldK);
      }
    } catch (e) {}
  }
})();

// Pulse the floating button until the user opens the modal once.
(function _initClaydoPulse() {
  if (localStorage.getItem('claydo_opened')) return;
  // Apply on next tick so the button has been added to DOM.
  setTimeout(() => {
    const fab = document.getElementById('claydo-fab');
    if (fab) fab.classList.add('pulse');
  }, 300);
})();

// Make the FAB draggable. Tap (no movement >5px) = open the modal; drag =
// reposition + persist the new spot in localStorage. Touch + mouse both
// supported. Position re-clamped on viewport resize so the button can't get
// trapped off-screen.
const _claydoDrag = {active: false, startX: 0, startY: 0, startLeft: 0, startTop: 0, moved: 0};
const CLAYDO_DRAG_THRESHOLD = 5;  // px before a press becomes a drag

function _claydoApplyFabPosition(left, top) {
  const fab = document.getElementById('claydo-fab');
  if (!fab) return;
  const size = fab.offsetWidth || 56;
  // Clamp within viewport with an 8px margin so it doesn't kiss the edge.
  left = Math.max(8, Math.min(left, window.innerWidth - size - 8));
  top  = Math.max(8, Math.min(top,  window.innerHeight - size - 8));
  // Switch from default right/bottom anchoring to explicit left/top.
  fab.style.right = 'auto';
  fab.style.bottom = 'auto';
  fab.style.left = left + 'px';
  fab.style.top = top + 'px';
}

function _claydoLoadFabPosition() {
  try {
    const s = JSON.parse(localStorage.getItem('claydo_fab_pos') || 'null');
    if (s && typeof s.left === 'number' && typeof s.top === 'number') {
      _claydoApplyFabPosition(s.left, s.top);
    }
  } catch {}
}

(function _initClaydoDrag() {
  // Wait for the FAB to exist in the DOM.
  setTimeout(() => {
    const fab = document.getElementById('claydo-fab');
    if (!fab) return;

    _claydoLoadFabPosition();

    const onDown = (e) => {
      const t = e.touches ? e.touches[0] : e;
      _claydoDrag.active = true;
      _claydoDrag.startX = t.clientX;
      _claydoDrag.startY = t.clientY;
      const r = fab.getBoundingClientRect();
      _claydoDrag.startLeft = r.left;
      _claydoDrag.startTop  = r.top;
      _claydoDrag.moved = 0;
      // Don't preventDefault on mousedown — we want focus + browser hover state
      // to behave normally. We'll preventDefault on touchmove instead so the
      // page doesn't scroll while dragging.
    };

    const onMove = (e) => {
      if (!_claydoDrag.active) return;
      const t = e.touches ? e.touches[0] : e;
      const dx = t.clientX - _claydoDrag.startX;
      const dy = t.clientY - _claydoDrag.startY;
      const dist = Math.abs(dx) + Math.abs(dy);
      if (dist > _claydoDrag.moved) _claydoDrag.moved = dist;
      if (_claydoDrag.moved < CLAYDO_DRAG_THRESHOLD) return;
      // Past the threshold — we're dragging.
      if (e.cancelable && e.touches) e.preventDefault();
      fab.classList.add('dragging');
      _claydoApplyFabPosition(_claydoDrag.startLeft + dx, _claydoDrag.startTop + dy);
    };

    const onUp = () => {
      if (!_claydoDrag.active) return;
      _claydoDrag.active = false;
      const wasDrag = _claydoDrag.moved >= CLAYDO_DRAG_THRESHOLD;
      fab.classList.remove('dragging');
      if (wasDrag) {
        // Persist the final spot.
        const r = fab.getBoundingClientRect();
        try {
          localStorage.setItem('claydo_fab_pos', JSON.stringify({left: r.left, top: r.top}));
        } catch {}
      } else {
        // It was a tap (no movement) — open the modal.
        openClaydo();
      }
    };

    fab.addEventListener('mousedown', onDown);
    fab.addEventListener('touchstart', onDown, {passive: true});
    document.addEventListener('mousemove', onMove);
    document.addEventListener('touchmove', onMove, {passive: false});
    document.addEventListener('mouseup', onUp);
    document.addEventListener('touchend', onUp);
    document.addEventListener('touchcancel', onUp);

    // Re-clamp to viewport when the window size changes (browser resize,
    // mobile rotation) so the button can't end up off-screen.
    window.addEventListener('resize', () => {
      const r = fab.getBoundingClientRect();
      // Only re-apply if a custom position was set (i.e. the user dragged).
      if (fab.style.left || fab.style.top) {
        _claydoApplyFabPosition(r.left, r.top);
      }
    });
  }, 200);
})();

async function openClaydo() {
  // Stop the pulse the first time the user opens it (persisted).
  localStorage.setItem('claydo_opened', '1');
  const fab = document.getElementById('claydo-fab');
  if (fab) fab.classList.remove('pulse');

  const modalId = '__claydo';
  if (openModals.has(modalId)) {
    const entry = openModals.get(modalId);
    if (entry.minimized) restoreModal(modalId);
    focusModal(modalId);
    setTimeout(() => document.getElementById('claydo-input')?.focus(), 50);
    return;
  }
  // Fresh open → fresh conversation in ask mode. (Minimize keeps history
  // + mode; close+reopen resets, since closeModalById removes the modal
  // from the DOM.)
  _claydoHistory = [];
  _claydoMode = 'ask';

  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content';
  _clampModalSize(content, 520, 600);
  content.innerHTML = `
    <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:14px 22px 12px 24px">
      <div style="display:flex;align-items:center;gap:10px;min-width:0;flex:1">
        <img id="claydo-avatar" src="/assets/claydo-idle.webp" alt="" style="width:32px;height:32px;border-radius:50%;border:1px solid var(--border);object-fit:contain;background:var(--surface2)" draggable="false">
        <div style="min-width:0">
          <div style="font-size:14px;font-weight:700;color:var(--text)">Ask Claydo</div>
          <div id="claydo-subtitle" style="font-size:11px;color:var(--text-faint)">Your in-app guide to Clayrune</div>
        </div>
      </div>
      <div class="modal-window-controls" style="position:static;display:flex;gap:4px;align-items:center">
        <button id="claydo-home-btn" class="claydo-home-btn" onclick="setClaydoMode('ask')" title="Back to Ask Claydo" style="display:none">&#8592; Home</button>
        <button class="modal-minimize" onclick="minimizeModal('${modalId}')" title="Minimize">&#x2015;</button>
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
    </div>
    <div class="claydo-history" id="claydo-history"></div>
    <div class="claydo-input-row">
      <textarea id="claydo-input" class="claydo-input" rows="1" placeholder="Ask a question..."
        onkeydown="if(event.key==='Enter' && !event.shiftKey){event.preventDefault();submitClaydo();}"></textarea>
      <button id="claydo-send" class="claydo-send" onclick="submitClaydo()">Send</button>
    </div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);

  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);
  _claydoResetConversation('ask');
  setTimeout(() => document.getElementById('claydo-input')?.focus(), 50);
}

// ── Modes: ask (help desk) / prompt / character workshops ─────────────────

const _CLAYDO_MODE_UI = {
  ask: {
    subtitle: 'Your in-app guide to Clayrune',
    placeholder: 'Ask a question...',
    greeting: 'Hi — I\'m Claydo. Ask me anything about Clayrune. I can highlight UI elements while I explain. Try: <em>"how do I start a hivemind?"</em> or <em>"where do I see scheduled runs?"</em>',
  },
  prompt: {
    subtitle: 'Prompt workshop',
    placeholder: 'Describe the task you need a prompt for...',
    greeting: 'Prompt workshop. Tell me what you want your agent to do — rough is fine. I\'ll ask a question or two if I need to, then hand you a sharpened prompt ready to send.',
  },
  character: {
    subtitle: 'Character workshop',
    placeholder: 'Describe the character — role, attitude, boundaries...',
    greeting: 'Character workshop. Describe the character you want your agent to play — a strict code reviewer, a patient teacher, a domain expert. I\'ll sculpt it into a reusable character you can save.',
  },
};

// Rebuild the history pane for a mode: greeting bubble + the chips row
// (workshop entries in ask mode, a back link in builder modes).
function _claydoResetConversation(mode) {
  const histDiv = document.getElementById('claydo-history');
  if (!histDiv) return;
  _claydoHistory = [];
  _claydoMode = mode;
  const ui = _CLAYDO_MODE_UI[mode] || _CLAYDO_MODE_UI.ask;

  const sub = document.getElementById('claydo-subtitle');
  if (sub) sub.textContent = ui.subtitle;
  const input = document.getElementById('claydo-input');
  if (input) { input.placeholder = ui.placeholder; input.value = ''; }

  // Persistent header "Home" button: visible in builder modes (the
  // greeting chips scroll out of reach once a draft is generated).
  const home = document.getElementById('claydo-home-btn');
  if (home) home.style.display = mode === 'ask' ? 'none' : '';

  const chips = mode === 'ask'
    ? `<div class="claydo-chips">
         <button class="claydo-chip" onclick="setClaydoMode('prompt')">&#x270D;&#xFE0F; Help me write a prompt</button>
         <button class="claydo-chip" onclick="setClaydoMode('character')">&#x1F3AD; Create an agent character</button>
       </div>`
    : '';
  histDiv.innerHTML = `<div class="claydo-msg bot">${ui.greeting}</div>${chips}`;
  setTimeout(() => document.getElementById('claydo-input')?.focus(), 30);
}

function setClaydoMode(mode) {
  if (!_CLAYDO_MODE_UI[mode] || mode === _claydoMode) return;
  _claydoResetConversation(mode);
}

// The project the user is "in" right now = topmost open, non-minimized
// project modal. Builder modes send it so Claydo grounds drafts in the
// project's name/rules/skills.
function _claydoFocusedProjectId() {
  let best = null, bestZ = -1;
  try {
    for (const [, info] of openModals) {
      if (info && info.projectId && !info.minimized && (info.zIndex || 0) > bestZ) {
        best = info.projectId;
        bestZ = info.zIndex || 0;
      }
    }
  } catch (e) {}
  return best;
}

// Swap the mascot's image between idle / thinking / (future: error,
// answering, celebrating). Updates BOTH the floating FAB and the avatar
// inside the chat modal so the animation is visible regardless of which
// surface the user is looking at. Source files are static stills (idle)
// + animated WebPs (thinking et al.) so a state change is just an
// img.src swap — the browser handles the loop without any JS player.
// State map for the Claydo mascot. Each entry is the image src that gets
// swapped onto the FAB and chat-modal avatar when the matching state is set.
// idle = static-ish ambient pose, thinking = while a question is in flight,
// working = brief "got it!" beat between thinking and idle (currently unused
// but reserved for a future "answer arrived" celebration), error = when the
// stream fails or API errors.
const _CLAYDO_STATE_SRC = {
  idle:     '/assets/claydo-idle.webp',
  thinking: '/assets/claydo-thinking.webp',
  working:  '/assets/claydo-working.webp',
  error:    '/assets/claydo-error.webp',
};
function _setClaydoState(state) {
  const src = _CLAYDO_STATE_SRC[state] || _CLAYDO_STATE_SRC.idle;
  const fabImg = document.querySelector('#claydo-fab img');
  if (fabImg && fabImg.src.split('/').pop() !== src.split('/').pop()) {
    fabImg.src = src;
  }
  const av = document.getElementById('claydo-avatar');
  if (av && av.src.split('/').pop() !== src.split('/').pop()) {
    av.src = src;
  }
}

async function submitClaydo() {
  const input = document.getElementById('claydo-input');
  const send = document.getElementById('claydo-send');
  const histDiv = document.getElementById('claydo-history');
  if (!input || !histDiv) return;
  const question = input.value.trim();
  if (!question) return;

  _setClaydoState('thinking');

  // User's message
  const userMsg = document.createElement('div');
  userMsg.className = 'claydo-msg user';
  userMsg.textContent = question;
  histDiv.appendChild(userMsg);

  // Bot message div — will be filled with streaming tokens.
  // Starts empty + shows the typing dots; replaced with real text as we go.
  const botMsg = document.createElement('div');
  botMsg.className = 'claydo-msg bot';
  const thinking = document.createElement('span');
  thinking.className = 'claydo-thinking';
  thinking.textContent = 'Claydo is thinking';
  botMsg.appendChild(thinking);
  histDiv.appendChild(botMsg);
  histDiv.scrollTop = histDiv.scrollHeight;

  input.value = '';
  input.disabled = true;
  send.disabled = true;

  // Snapshot prior history (ask: last 6 messages = ~3 exchanges; builder
  // modes carry a longer tail so interview answers survive to the draft —
  // the server caps at 12 either way).
  const historyPayload = _claydoHistory.slice(_claydoMode === 'ask' ? -6 : -12);
  // Push the user's message into history NOW so the next turn sees it even
  // if streaming fails midway.
  _claydoHistory.push({role: 'user', text: question});

  // Accumulate streamed text chunks here so we can render incrementally
  // AND parse markers at the end from the full assembled answer.
  let assembled = '';
  let firstChunk = true;
  let errored = false;

  try {
    const res = await fetch(API_BASE + '/api/guide/stream', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        question,
        history: historyPayload,
        mode: _claydoMode,
        project_id: _claydoMode === 'ask' ? null : _claydoFocusedProjectId(),
      }),
    });
    if (!res.ok || !res.body) {
      const data = await res.json().catch(() => ({}));
      _claydoRenderError(botMsg, data.error || `Request failed (${res.status})`, question);
      errored = true;
      return;
    }

    // Read the SSE stream chunk-by-chunk via the body's ReadableStream.
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    while (true) {
      const {value, done} = await reader.read();
      if (done) break;
      buf += decoder.decode(value, {stream: true});
      // SSE events are `data: {...}\n\n` — split on double-newline.
      let nl;
      while ((nl = buf.indexOf('\n\n')) !== -1) {
        const event = buf.slice(0, nl);
        buf = buf.slice(nl + 2);
        const dataLine = event.split('\n').find(l => l.startsWith('data: '));
        if (!dataLine) continue;
        let payload;
        try { payload = JSON.parse(dataLine.slice(6)); } catch { continue; }
        if (payload.type === 'delta') {
          if (firstChunk) {
            botMsg.innerHTML = '';
            firstChunk = false;
          }
          assembled += payload.text || '';
          // Render the assembled-so-far with markers stripped (they'll be
          // dispatched on `done`). Light formatting matches non-streaming path.
          const {cleanText} = _claydoParseMarkers(assembled);
          botMsg.innerHTML = _claydoFormatText(cleanText);
          histDiv.scrollTop = histDiv.scrollHeight;
        } else if (payload.type === 'error') {
          _claydoRenderError(botMsg, payload.message || 'Claydo errored', question);
          errored = true;
          return;
        } else if (payload.type === 'done') {
          // Final assembled answer — use it as ground truth in case any
          // delta got dropped. Then parse markers and dispatch.
          const finalText = (payload.answer || assembled).trim();
          const {cleanText, actions} = _claydoParseMarkers(finalText);
          botMsg.innerHTML = _claydoFormatText(cleanText);
          // Store the cleaned text (NO markers) so Claydo's next turn doesn't
          // re-emit the same highlights from seeing them in its own prior reply.
          _claydoHistory.push({role: 'assistant', text: cleanText});
          const histCap = _claydoMode === 'ask' ? 12 : 24;
          if (_claydoHistory.length > histCap) {
            _claydoHistory = _claydoHistory.slice(-histCap);
          }
          _claydoDispatchActions(actions);
          _claydoRenderReadyCard(botMsg, actions, cleanText);
        }
      }
    }
  } catch (e) {
    _claydoRenderError(botMsg, 'Network error: ' + (e.message || e), question);
    errored = true;
  } finally {
    input.disabled = false;
    send.disabled = false;
    input.focus();
    histDiv.scrollTop = histDiv.scrollHeight;
    // On error: hold the confused/error pose for ~3s so the user notices,
    // then revert to idle. On success: revert immediately. The errored
    // flag was set in the catch / payload-error branches above.
    if (errored) {
      _setClaydoState('error');
      setTimeout(() => _setClaydoState('idle'), 3000);
    } else {
      _setClaydoState('idle');
    }
  }
}

// Render an error in the bot message slot with a Retry button. The retry
// pops the failed user message back out of _claydoHistory + the input box
// so the user just clicks Send again (or edits the question first).
function _claydoRenderError(botMsg, message, originalQuestion) {
  botMsg.innerHTML = '';
  botMsg.classList.remove('bot'); botMsg.classList.add('error');
  const msg = document.createElement('div');
  msg.textContent = message;
  botMsg.appendChild(msg);
  const retry = document.createElement('button');
  retry.className = 'claydo-retry-btn';
  retry.textContent = 'Retry';
  retry.onclick = () => {
    // Drop the failed user message from history (we pushed it pre-fetch).
    if (_claydoHistory.length && _claydoHistory[_claydoHistory.length - 1].role === 'user') {
      _claydoHistory.pop();
    }
    // Refill the input so the user can edit if they want, then resubmit.
    const input = document.getElementById('claydo-input');
    if (input) {
      input.value = originalQuestion;
      input.focus();
    }
    // Remove the error bubble so it doesn't pile up on retry.
    botMsg.remove();
  };
  botMsg.appendChild(retry);
}

// Parse [clayrune:goto view="..."], [clayrune:open-modal project="..."],
// [clayrune:highlight selector="..." duration=N] markers out of Claydo's
// reply, plus the builder handoffs [clayrune:prompt-ready] and
// [clayrune:character-ready name="..."]. Returns the cleaned text + an
// array of action objects to dispatch. The ready markers deliberately
// carry no payload — the artifact is the reply's last fenced block.
function _claydoParseMarkers(raw) {
  const actions = [];
  const re = /\[clayrune:(goto|open-modal|highlight|prompt-ready|character-ready)(\s+[^\]]+)?\]/g;
  const cleanText = raw.replace(re, (_match, kind, attrs) => {
    const out = {kind};
    // Parse key="value" pairs (also accept key=value for unquoted nums).
    const attrRe = /(\w+)=(?:"([^"]*)"|(\d+))/g;
    let m;
    while ((m = attrRe.exec(attrs || '')) !== null) {
      out[m[1]] = m[2] !== undefined ? m[2] : m[3];
    }
    actions.push(out);
    return '';
  });
  return {cleanText: cleanText.trim(), actions};
}

function _claydoDispatchActions(actions) {
  if (!actions.length) return;
  // Stagger by 300ms so the user can follow what's happening.
  let delay = 0;
  for (const a of actions) {
    setTimeout(() => _claydoRunAction(a), delay);
    delay += 350;
  }
}

function _claydoRunAction(a) {
  try {
    if (a.kind === 'goto' && a.view) {
      if (typeof sidebarNav === 'function') sidebarNav(a.view);
    } else if (a.kind === 'open-modal' && a.project) {
      if (typeof openProjectModal === 'function') openProjectModal(a.project);
    } else if (a.kind === 'highlight' && a.selector) {
      const el = document.querySelector(a.selector);
      if (!el) return;
      const dur = parseInt(a.duration, 10) || 2500;
      el.classList.add('clayrune-highlight');
      try { el.scrollIntoView({behavior: 'smooth', block: 'center'}); } catch {}
      setTimeout(() => el.classList.remove('clayrune-highlight'), dur);
    }
  } catch (e) {
    console.warn('Claydo action failed:', a, e);
  }
}

// Light markdown-ish formatting for Claydo's text. Fenced blocks become
// <pre> (the builder modes deliver their artifact in one); the prose gets
// bold + inline code + preserved newlines. Strip any HTML the agent might
// emit (defense in depth). While a fence is still streaming (odd count),
// the open tail renders as <pre> too — settles correctly at `done`.
function _claydoFormatText(s) {
  const segs = String(s).split(/```(?:[\w-]+)?\n?/);
  let out = '';
  for (let i = 0; i < segs.length; i++) {
    if (i % 2 === 1) {
      out += '<pre class="claydo-code">' + esc(segs[i].replace(/\n$/, '')) + '</pre>';
    } else {
      out += esc(segs[i])
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
        .replace(/\n/g, '<br>');
    }
  }
  return out;
}

// ── Builder handoff: ready cards, insert/copy, save panel ─────────────────

// The artifact = the LAST fenced block in the cleaned reply (the briefs'
// output contract). Never sourced from marker attrs.
function _claydoLastFenced(text) {
  const re = /```(?:[\w-]+)?\n([\s\S]*?)```/g;
  let m, last = null;
  while ((m = re.exec(text)) !== null) last = m[1];
  return last ? last.trim() : null;
}

function _claydoRenderReadyCard(botMsg, actions, cleanText) {
  const ready = actions.find(a => a.kind === 'prompt-ready' || a.kind === 'character-ready');
  if (!ready) return;
  const artifact = _claydoLastFenced(cleanText);
  if (!artifact) return;  // marker without a fenced draft — nothing to hand off

  const card = document.createElement('div');
  card.className = 'claydo-ready-card';

  const mkBtn = (label, cls, onClick) => {
    const b = document.createElement('button');
    b.className = 'claydo-ready-btn' + (cls ? ' ' + cls : '');
    b.innerHTML = label;
    b.onclick = onClick;
    card.appendChild(b);
    return b;
  };

  if (ready.kind === 'prompt-ready') {
    const pid = _claydoFocusedProjectId();
    if (pid) {
      mkBtn('&#8594; Insert into project chat', 'accent',
            () => _claydoInsertIntoProject(pid, artifact));
    }
    mkBtn('Copy prompt', pid ? '' : 'accent', (e) => _claydoCopy(artifact, e.target));
  } else {
    mkBtn('&#x1F4BE; Save character&hellip;', 'accent',
          () => _claydoOpenSavePanel(artifact, ready.name || ''));
    mkBtn('Copy', '', (e) => _claydoCopy(artifact, e.target));
  }
  botMsg.appendChild(card);
}

// Drop the prompt into the focused project modal's chat box (the
// agent-followup-<sessionId> textarea). Falls back to clipboard when the
// modal/box isn't there.
function _claydoInsertIntoProject(pid, text) {
  let modalEl = null, modalId = null;
  try {
    for (const [id, info] of openModals) {
      if (info && info.projectId === pid && !info.minimized && info.element) {
        modalEl = info.element;
        modalId = id;
        break;
      }
    }
  } catch (e) {}
  const ta = modalEl ? modalEl.querySelector('textarea[id^="agent-followup-"]') : null;
  if (!ta) {
    _claydoCopy(text, null);
    _claydoBotNote('Couldn\'t find the project chat box — the prompt is on your clipboard instead.');
    return;
  }
  ta.value = text;
  ta.dispatchEvent(new Event('input', {bubbles: true}));  // autosize + value-preserve hooks
  if (modalId && typeof focusModal === 'function') focusModal(modalId);
  ta.focus();
}

function _claydoCopy(text, btn) {
  const done = () => {
    if (btn && btn.textContent !== undefined) {
      const t = btn.textContent;
      btn.textContent = 'Copied ✓';
      setTimeout(() => { btn.textContent = t; }, 1500);
    }
  };
  const fallback = () => {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); } catch (e) {}
    ta.remove();
    done();
  };
  try {
    navigator.clipboard.writeText(text).then(done, fallback);
  } catch (e) {
    fallback();
  }
}

// Append a small bot-side note bubble (save confirmations, fallbacks).
function _claydoBotNote(html) {
  const histDiv = document.getElementById('claydo-history');
  if (!histDiv) return;
  const d = document.createElement('div');
  d.className = 'claydo-msg bot';
  d.innerHTML = html;
  histDiv.appendChild(d);
  histDiv.scrollTop = histDiv.scrollHeight;
}

// ── Save-character panel — overlay inside the Claydo modal ────────────────
// Inputs numbered top-down, single accent action button (the multi-input
// modal convention).

function _claydoOpenSavePanel(artifact, suggestedName) {
  // Prefill from the artifact's frontmatter; the body is what's below it.
  let name = suggestedName || '', description = '', body = artifact;
  const fm = artifact.match(/^---\r?\n([\s\S]*?)\r?\n---\r?\n?/);
  if (fm) {
    body = artifact.slice(fm[0].length).trim();
    const nm = fm[1].match(/^name:\s*(.+)$/m);
    const dm = fm[1].match(/^description:\s*([\s\S]*?)(?=\r?\n[a-zA-Z_-]+\s*:|$)/);
    if (nm) name = nm[1].trim().replace(/^["']|["']$/g, '');
    if (dm) description = dm[1].replace(/\s*\r?\n\s+/g, ' ').trim().replace(/^["']|["']$/g, '');
  }
  if (!name) name = 'my-character';

  const content = document.querySelector(`[data-modal-id="__claydo"] .modal-content`)
    || document.getElementById('claydo-history')?.parentElement;
  if (!content) return;
  // Anchor the inset:0 overlay to the Claydo modal only (modal-content is
  // position:static globally — don't change the shared class).
  if (getComputedStyle(content).position === 'static') content.style.position = 'relative';
  content.querySelector('.claydo-save-panel')?.remove();

  const pid = _claydoFocusedProjectId();
  const panel = document.createElement('div');
  panel.className = 'claydo-save-panel';
  panel.innerHTML = `
    <div class="claydo-save-inner">
      <div class="claydo-save-title">Save character</div>
      <label>1. Name</label>
      <input id="claydo-save-name" type="text" spellcheck="false" value="${esc(name)}">
      <label>2. Description <span class="claydo-save-hint">(when should the agent use it?)</span></label>
      <input id="claydo-save-desc" type="text" value="${esc(description)}">
      <label>3. Where</label>
      <select id="claydo-save-scope">
        <option value="global">All my projects (global)</option>
      </select>
      <div class="claydo-save-err" id="claydo-save-err" style="display:none"></div>
      <div class="claydo-save-actions">
        <button class="claydo-ready-btn" id="claydo-save-cancel">Cancel</button>
        <button class="claydo-ready-btn accent" id="claydo-save-go">Save character</button>
      </div>
    </div>`;
  content.appendChild(panel);

  const errEl = panel.querySelector('#claydo-save-err');
  const showErr = (msg) => { errEl.textContent = msg; errEl.style.display = 'block'; };
  panel.querySelector('#claydo-save-cancel').onclick = () => panel.remove();
  panel.addEventListener('mousedown', (e) => { if (e.target === panel) panel.remove(); });

  // Populate the "Where" dropdown with every project (value = project id),
  // not just the focused one. Default to the focused project if there is
  // one, else global. Project scope writes to <that project>/.claude/agents/.
  const scopeSel = panel.querySelector('#claydo-save-scope');
  (async () => {
    try {
      const res = await fetch(API_BASE + '/api/projects');
      const list = await res.json();
      if (Array.isArray(list)) {
        for (const p of list) {
          if (!p || !p.id) continue;
          const opt = document.createElement('option');
          opt.value = p.id;
          opt.textContent = p.name || p.id;
          scopeSel.appendChild(opt);
        }
        if (pid) scopeSel.value = pid;
      }
    } catch (e) {}
  })();

  const goBtn = panel.querySelector('#claydo-save-go');
  let overwrite = false;
  goBtn.onclick = async () => {
    const nameVal = panel.querySelector('#claydo-save-name').value.trim().toLowerCase();
    const descVal = panel.querySelector('#claydo-save-desc').value.trim();
    // 'global' → global scope; any other value is a project id.
    const whereVal = panel.querySelector('#claydo-save-scope').value;
    const isGlobal = whereVal === 'global';
    if (!/^[a-z0-9][a-z0-9-]{0,63}$/.test(nameVal)) {
      return showErr('Name must be kebab-case: letters, digits, hyphens.');
    }
    if (!descVal) return showErr('Description is required — it tells the agent when to use this character.');
    goBtn.disabled = true;
    try {
      const res = await fetch(API_BASE + '/api/characters', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          name: nameVal, description: descVal, body,
          scope: isGlobal ? 'global' : 'project',
          project_id: isGlobal ? null : whereVal,
          overwrite,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.status === 409 && !overwrite) {
        overwrite = true;
        goBtn.disabled = false;
        goBtn.textContent = 'Overwrite existing';
        return showErr(`"${nameVal}" already exists in that scope — save again to overwrite.`);
      }
      if (!res.ok) {
        goBtn.disabled = false;
        return showErr(data.error || `Save failed (${res.status})`);
      }
      panel.remove();
      const whereLabel = isGlobal ? 'all projects'
        : (scopeSel.options[scopeSel.selectedIndex]?.textContent || 'project');
      _claydoBotNote(`Saved <strong>${esc(nameVal)}</strong> ✓ (${esc(whereLabel)}). Your agent can use it now — mention <code>@${esc(nameVal)}</code> in chat.`);
    } catch (e) {
      goBtn.disabled = false;
      showErr('Network error: ' + (e.message || e));
    }
  };
  setTimeout(() => panel.querySelector('#claydo-save-name')?.focus(), 30);
}

// interop: called from inline script/onclick
window.submitClaydo = submitClaydo;
window.setClaydoMode = setClaydoMode;
