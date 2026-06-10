// ── Ask Claydo (in-app guide assistant) ───────────────────────────────────
// Floating button → modal chat → POST /api/guide/ask → render answer +
// strip + dispatch [clayrune:...] UI control markers emitted by Claydo.

// Per-modal-session conversation history. Reset each time the modal is
// opened fresh (i.e. after being closed). Minimize+restore preserves it.
// Cap at 6 messages (3 exchanges) — anything older falls off the front.
let _claydoHistory = [];

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
  // Fresh open → fresh conversation. (Minimize keeps history; close+reopen
  // resets, since closeModalById removes the modal from the DOM.)
  _claydoHistory = [];

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
          <div style="font-size:11px;color:var(--text-faint)">Your in-app guide to Clayrune</div>
        </div>
      </div>
      <div class="modal-window-controls" style="position:static;display:flex;gap:4px">
        <button class="modal-minimize" onclick="minimizeModal('${modalId}')" title="Minimize">&#x2015;</button>
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
    </div>
    <div class="claydo-history" id="claydo-history">
      <div class="claydo-msg bot">Hi — I'm Claydo. Ask me anything about Clayrune. I can highlight UI elements while I explain. Try: <em>"how do I start a hivemind?"</em> or <em>"where do I see scheduled runs?"</em></div>
    </div>
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
  setTimeout(() => document.getElementById('claydo-input')?.focus(), 50);
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

  // Snapshot prior history (last 6 messages = ~3 exchanges).
  const historyPayload = _claydoHistory.slice(-6);
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
      body: JSON.stringify({question, history: historyPayload}),
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
          if (_claydoHistory.length > 12) {
            _claydoHistory = _claydoHistory.slice(-12);
          }
          _claydoDispatchActions(actions);
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
// reply. Returns the cleaned text + an array of action objects to dispatch.
function _claydoParseMarkers(raw) {
  const actions = [];
  const re = /\[clayrune:(goto|open-modal|highlight)\s+([^\]]+)\]/g;
  const cleanText = raw.replace(re, (_match, kind, attrs) => {
    const out = {kind};
    // Parse key="value" pairs (also accept key=value for unquoted nums).
    const attrRe = /(\w+)=(?:"([^"]*)"|(\d+))/g;
    let m;
    while ((m = attrRe.exec(attrs)) !== null) {
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

// Light markdown-ish formatting for Claydo's text. Bold, inline code, and
// preserve newlines. Strip any HTML the agent might emit (defense in depth).
function _claydoFormatText(s) {
  return esc(s)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\n/g, '<br>');
}

// interop: called from inline script/onclick
window.submitClaydo = submitClaydo;
