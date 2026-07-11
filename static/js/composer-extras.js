// ── Mic / voice transcription ───────────────────────────────────────────────
// Two backends:
//  • Native: the @capacitor-community/speech-recognition plugin in the Clayrune
//    Android APK (the streaming restart-cycle machinery below).
//  • Browser: the Web Speech API (webkitSpeechRecognition) in Chrome/Edge/
//    WebView2 — used on desktop, where there's no Capacitor plugin. Requires a
//    secure context (https or localhost), which the desktop app + tunnel are.
function _micNativeAvailable() {
  try {
    return !!(window.Capacitor && typeof window.Capacitor.isNativePlatform === 'function'
      && window.Capacitor.isNativePlatform()
      && window.Capacitor.Plugins && window.Capacitor.Plugins.SpeechRecognition);
  } catch (_) { return false; }
}
function _micBrowserCtor() {
  try { return window.SpeechRecognition || window.webkitSpeechRecognition || null; } catch (_) { return null; }
}
function micAvailable() { return _micNativeAvailable() || !!_micBrowserCtor(); }
function micBtnHTML(textareaId) {
  if (!micAvailable()) return '';
  return `<button class="btn-mic" type="button" id="btn-mic-${textareaId}"
    title="Voice dictation — tap to record, tap again to stop"
    onclick="toggleAgentMic('${textareaId}')">&#127908;</button>`;
}
// Per-textarea recording state. `gen` is a monotonic counter that lets stale
// listeners from a previous session (e.g. after a modal rebuild) recognize
// they no longer belong to the current run and bail out.
const _micState = {};
let _micGen = 0;
function _micUiOff(textareaId) {
  const btn = document.getElementById(`btn-mic-${textareaId}`);
  if (btn) btn.classList.remove('recording');
}
function toggleAgentMic(textareaId) {
  // Synchronous dispatcher — never awaits. Avoids click being "swallowed"
  // by a pending native call (e.g. SR.stop hanging) so the button is always
  // responsive even if a prior session is in a stuck native state. Routes to
  // the native or browser backend depending on the platform.
  const st = _micState[textareaId];
  if (_micNativeAvailable()) {
    if (st && st.active) { _stopAgentMic(textareaId); return; }
    _startAgentMic(textareaId);
  } else {
    if (st && st.active) { _stopBrowserMic(textareaId); return; }
    _startBrowserMic(textareaId);
  }
}
function _micToast(msg) {
  try { showToast('[mic] ' + msg, 3500); } catch (_) {}
}
async function _startAgentMic(textareaId) {
  // Always nuke any prior state first. If a previous session got wedged
  // (native recognizer stuck, modal rebuilt while recording, etc.) this
  // ensures the new attempt starts clean.
  _hardResetMic(textareaId);
  const SR = window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.SpeechRecognition;
  const ta = document.getElementById(textareaId);
  if (!SR) { _micToast('plugin missing'); return; }
  if (!ta) { _micToast('textarea not found: ' + textareaId); return; }
  const gen = ++_micGen;
  const base0 = ta.value || '';
  _micState[textareaId] = {
    active: true, gen, base: base0,
    sep: base0 && !/\s$/.test(base0) ? ' ' : '',
    listening: false, partial: null, state: null,
    endTimer: null, beginTimer: null, watchdog: null, forceReset: null,
    sawPartial: false, cycleStartBase: base0, emptyStreak: 0,
  };
  const btn = document.getElementById(`btn-mic-${textareaId}`);
  if (btn) btn.classList.add('recording');
  try {
    const av = await SR.available().catch((e) => { _micToast('available() threw: ' + (e && e.message || e)); return { available: false }; });
    if (!av || av.available === false) { _micToast('SR not available on device'); _hardResetMic(textareaId); return; }
    let perm = await SR.checkPermissions().catch((e) => { _micToast('checkPerm err: ' + (e && e.message || e)); return null; });
    if (!perm || perm.speechRecognition !== 'granted') {
      const req = await SR.requestPermissions().catch((e) => { _micToast('reqPerm err: ' + (e && e.message || e)); return null; });
      if (!req || req.speechRecognition !== 'granted') { _micToast('perm denied (' + (req && req.speechRecognition || '?') + ')'); _hardResetMic(textareaId); return; }
    }
    // State may have been reset by another tap while permissions resolved.
    if (!_micState[textareaId] || _micState[textareaId].gen !== gen) return;
    // Re-base on the textarea's current value (it may have changed while the
    // permission prompt was up).
    {
      const cur = _micState[textareaId];
      cur.base = ta.value || '';
      cur.sep = cur.base && !/\s$/.test(cur.base) ? ' ' : '';
      cur.cycleStartBase = cur.base;
    }
    // Attach the streaming listeners ONCE; they live for the whole recording
    // session and are reused across the silence-driven restart cycles below.
    try {
      _micState[textareaId].partial = await SR.addListener('partialResults', (data) => {
        const cur = _micState[textareaId];
        if (!cur || cur.gen !== gen || !cur.listening) return;
        const live = document.getElementById(textareaId);
        if (!live) return;
        const matches = (data && (data.matches || data.value)) || [];
        if (!matches.length) return;
        // Speech detected — keep the cycle alive and remember we got text.
        cur.sawPartial = true;
        _micResetWatchdog(textareaId, gen);
        live.value = cur.base + cur.sep + String(matches[0] || '');
        try { live.dispatchEvent(new Event('input', { bubbles: true })); } catch (_) {}
      });
      _micState[textareaId].state = await SR.addListener('listeningState', (data) => {
        const cur = _micState[textareaId];
        if (!cur || cur.gen !== gen) return;
        if (data && data.status === 'started') { _micResetWatchdog(textareaId, gen); return; }
        // 'stopped' = end of one utterance (a silence gap). Defer the cycle
        // end a beat so the trailing final transcript (delivered as one last
        // partialResults event right after onEndOfSpeech) lands before we
        // promote the text and restart.
        if (data && data.status === 'stopped') _micScheduleEnd(textareaId, gen, 600);
      });
    } catch (e) { console.warn('mic listener attach failed', e); }
    _micToast('listening…');
    _beginMicCycle(textareaId, gen);
  } catch (e) {
    _micToast('mic outer fail: ' + (e && e.message || e));
    _hardResetMic(textareaId);
  }
}
// One listen pass. Android's stock SpeechRecognizer ALWAYS finalizes on a
// silence gap — there is no true continuous mode — so we treat each finalize
// as the end of a *cycle*, not the end of recording, and transparently
// restart. Dictation then continues until the user taps the button off. This
// is the record-on / record-off model: Google's eager stop no longer ends the
// session, it just rolls into the next pass.
//
// Options rationale:
//   language 'en-US'  — device locales (he-IL / en-IL …) frequently lack an
//                       installed recognizer pack → ERROR_LANGUAGE_NOT_SUPPORTED.
//   popup: false      — inline/streaming via SpeechRecognizer; NO fullscreen
//                       Google dialog that owns (and prematurely ends) its own
//                       lifecycle. This is the core of the fix.
//   partialResults    — enables live partial events AND, via the plugin's
//                       DICTATION_MODE intent extra, makes each pass ride out
//                       short thinking pauses before finalizing.
// In partialResults mode the plugin resolves start() immediately (text arrives
// via the listeners, and the final transcript as the last partialResults
// event), so we never read the promise for text — only to catch a hard reject.
function _beginMicCycle(textareaId, gen) {
  const SR = window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.SpeechRecognition;
  const cur = _micState[textareaId];
  if (!SR || !cur || cur.gen !== gen || !cur.active) return;
  cur.listening = true;
  cur.sawPartial = false;
  cur.cycleStartBase = cur.base;
  _micResetWatchdog(textareaId, gen);
  try {
    Promise.resolve(SR.start({ language: 'en-US', maxResults: 2, partialResults: true, popup: false }))
      .catch((e) => {
        const c = _micState[textareaId];
        if (!c || c.gen !== gen) return;
        // Some devices reject (e.g. RECOGNIZER_BUSY) instead of emitting a
        // 'stopped' event — funnel it through the same cycle-end path.
        _micScheduleEnd(textareaId, gen, 250);
      });
  } catch (e) {
    _micScheduleEnd(textareaId, gen, 250);
  }
}
function _micResetWatchdog(textareaId, gen) {
  const cur = _micState[textareaId];
  if (!cur || cur.gen !== gen) return;
  try { cur.watchdog && clearTimeout(cur.watchdog); } catch (_) {}
  // Fires only when a cycle goes fully silent with no end event at all —
  // covers the case where the recognizer dies on ERROR_NO_MATCH /
  // SPEECH_TIMEOUT, which the plugin swallows (start() already resolved) and
  // reports via neither a rejection nor a 'stopped' event. Any partial or
  // 'started' event pushes this out, so it never preempts live speech.
  cur.watchdog = setTimeout(() => _endMicCycle(textareaId, gen), 6000);
}
function _micScheduleEnd(textareaId, gen, delay) {
  const cur = _micState[textareaId];
  if (!cur || cur.gen !== gen || !cur.listening) return;
  try { cur.endTimer && clearTimeout(cur.endTimer); } catch (_) {}
  cur.endTimer = setTimeout(() => _endMicCycle(textareaId, gen), delay);
}
function _endMicCycle(textareaId, gen) {
  const cur = _micState[textareaId];
  if (!cur || cur.gen !== gen || !cur.listening) return;   // once per cycle
  cur.listening = false;
  try { cur.watchdog && clearTimeout(cur.watchdog); } catch (_) {}
  try { cur.endTimer && clearTimeout(cur.endTimer); } catch (_) {}
  // Promote whatever the listeners rendered into the committed base so the
  // next cycle appends after it (guard against an external shrink of the box).
  const live = document.getElementById(textareaId);
  if (live && typeof live.value === 'string' && live.value.length >= cur.base.length) {
    cur.base = live.value;
    cur.sep = cur.base && !/\s$/.test(cur.base) ? ' ' : '';
  }
  // Detect a recognizer producing nothing (silent-death loop) and stop after a
  // run of empty cycles rather than spinning forever.
  if (!cur.sawPartial && cur.base === cur.cycleStartBase) {
    cur.emptyStreak = (cur.emptyStreak || 0) + 1;
  } else {
    cur.emptyStreak = 0;
  }
  if (cur.active && cur.emptyStreak >= 6) { _micToast('stopped — no speech'); _hardResetMic(textareaId); return; }
  if (cur.active) {
    // Brief settle before restart; an immediate start() often hits
    // ERROR_RECOGNIZER_BUSY before the old recognizer fully releases.
    cur.beginTimer = setTimeout(() => _beginMicCycle(textareaId, gen), 300);
  } else {
    _hardResetMic(textareaId);
  }
}
function _stopAgentMic(textareaId) {
  // User tapped off. Signal "do not restart" and drop the button highlight
  // SYNCHRONOUSLY so it's responsive even if native stop() hangs. Then let the
  // in-flight cycle finalize (capturing the last transcript) with a hard
  // backstop if it never does.
  const SR = window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.SpeechRecognition;
  const st = _micState[textareaId];
  if (!st) { _micUiOff(textareaId); return; }
  st.active = false;
  _micUiOff(textareaId);
  try { st.beginTimer && clearTimeout(st.beginTimer); } catch (_) {}
  if (SR) { try { Promise.resolve(SR.stop()).catch(() => {}); } catch (_) {} }
  // Between cycles (not currently listening) → nothing will finalize; reset now.
  if (!st.listening) { _hardResetMic(textareaId); return; }
  try { st.forceReset && clearTimeout(st.forceReset); } catch (_) {}
  st.forceReset = setTimeout(() => _hardResetMic(textareaId), 1500);
}
function _hardResetMic(textareaId) {
  const st = _micState[textareaId];
  if (st) {
    try { st.partial && st.partial.remove && st.partial.remove(); } catch (_) {}
    try { st.state && st.state.remove && st.state.remove(); } catch (_) {}
    try { st.endTimer && clearTimeout(st.endTimer); } catch (_) {}
    try { st.beginTimer && clearTimeout(st.beginTimer); } catch (_) {}
    try { st.watchdog && clearTimeout(st.watchdog); } catch (_) {}
    try { st.forceReset && clearTimeout(st.forceReset); } catch (_) {}
  }
  delete _micState[textareaId];
  _micUiOff(textareaId);
}

// ── Browser (Web Speech API) mic — desktop path ─────────────────────────────
// webkitSpeechRecognition stops on a silence gap even with continuous=true, so
// (like the native cycle machinery) we restart it in onend until the user taps
// off. Final results accumulate into `finalText`; interim results render live.
function _startBrowserMic(textareaId) {
  _hardResetBrowserMic(textareaId);
  const Ctor = _micBrowserCtor();
  const ta = document.getElementById(textareaId);
  if (!Ctor) { _micToast('voice not supported in this browser'); return; }
  if (!ta) { _micToast('textarea not found: ' + textareaId); return; }
  let rec;
  try { rec = new Ctor(); } catch (e) { _micToast('mic init failed'); return; }
  const gen = ++_micGen;
  const base0 = ta.value || '';
  const st = {
    active: true, gen, browser: true, rec, finalText: '',
    base: base0, sep: base0 && !/\s$/.test(base0) ? ' ' : '',
  };
  _micState[textareaId] = st;
  const btn = document.getElementById(`btn-mic-${textareaId}`);
  if (btn) btn.classList.add('recording');
  rec.continuous = true;
  rec.interimResults = true;
  try { rec.lang = navigator.language || 'en-US'; } catch (_) {}
  rec.onresult = (ev) => {
    const cur = _micState[textareaId];
    if (!cur || cur.gen !== gen) return;
    const live = document.getElementById(textareaId);
    if (!live) return;
    let interim = '';
    for (let i = ev.resultIndex; i < ev.results.length; i++) {
      const res = ev.results[i];
      const txt = (res[0] && res[0].transcript || '').trim();
      if (!txt) continue;
      if (res.isFinal) cur.finalText += (cur.finalText && !/\s$/.test(cur.finalText) ? ' ' : '') + txt;
      else interim += (interim ? ' ' : '') + txt;
    }
    const dictated = (cur.finalText + (interim ? (cur.finalText ? ' ' : '') + interim : '')).trim();
    live.value = cur.base + cur.sep + dictated;
    try { live.dispatchEvent(new Event('input', { bubbles: true })); } catch (_) {}
  };
  rec.onerror = (ev) => {
    const cur = _micState[textareaId];
    if (!cur || cur.gen !== gen) return;
    const err = ev && ev.error;
    if (err === 'not-allowed' || err === 'service-not-allowed') { _micToast('mic permission denied'); cur.active = false; _hardResetBrowserMic(textareaId); }
    else if (err === 'no-speech' || err === 'aborted') { /* benign — onend will restart if still active */ }
    else _micToast('mic error: ' + err);
  };
  rec.onend = () => {
    const cur = _micState[textareaId];
    if (!cur || cur.gen !== gen) return;
    if (!cur.active) { _hardResetBrowserMic(textareaId); return; }
    // Silence gap ended the pass — restart to stay continuous until tap-off.
    try { rec.start(); }
    catch (_) { setTimeout(() => { const c = _micState[textareaId]; if (c && c.gen === gen && c.active) { try { rec.start(); } catch (__) {} } }, 300); }
  };
  try { rec.start(); _micToast('listening…'); }
  catch (e) { _micToast('mic start failed: ' + (e && e.message || e)); _hardResetBrowserMic(textareaId); }
}
function _stopBrowserMic(textareaId) {
  const st = _micState[textareaId];
  if (!st) { _micUiOff(textareaId); return; }
  st.active = false;              // tells onend not to restart
  _micUiOff(textareaId);
  try { st.rec && st.rec.stop(); } catch (_) {}
  // Backstop in case onend never fires.
  setTimeout(() => _hardResetBrowserMic(textareaId), 1200);
}
function _hardResetBrowserMic(textareaId) {
  const st = _micState[textareaId];
  if (st && st.rec) {
    try { st.rec.onresult = st.rec.onend = st.rec.onerror = null; } catch (_) {}
    try { st.rec.abort(); } catch (_) {}
  }
  delete _micState[textareaId];
  _micUiOff(textareaId);
}

function handleAgentAttachPick(e, key) {
  const files = Array.from(e.target.files || []);
  if (files.length === 0) return;
  const list = agentPendingImages[key] || [];
  for (const file of files) {
    const isImage = file.type.startsWith('image/');
    list.push({
      file,
      objectUrl: isImage ? URL.createObjectURL(file) : null,
      serverPath: null,
      isDocument: !isImage,
      fileName: file.name,
    });
  }
  agentPendingImages[key] = list;
  // Clear the input value so picking the same file twice still fires `change`.
  try { e.target.value = ''; } catch (_) {}
  refreshModal();
}

function renderAgentImagePreviews(key) {
  const list = agentPendingImages[key] || [];
  if (list.length === 0) return '';
  return `<div class="agent-image-previews">${list.map((img, i) => `
    <div class="agent-image-preview">
      ${img.isDocument
        ? `<div class="agent-file-preview" title="${esc(img.fileName || 'file')}">&#128196; ${esc((img.fileName || 'file').length > 18 ? (img.fileName || 'file').slice(0,15) + '...' : (img.fileName || 'file'))}</div>`
        : `<img src="${img.objectUrl}" alt="paste">`}
      <button class="create-preview-remove" onclick="removeAgentImage('${esc(key)}',${i})">&#10005;</button>
    </div>`).join('')}</div>`;
}

function removeAgentImage(key, index) {
  const list = agentPendingImages[key] || [];
  if (list[index]) URL.revokeObjectURL(list[index].objectUrl);
  list.splice(index, 1);
  if (list.length === 0) delete agentPendingImages[key];
  refreshModal();
}

async function uploadAgentImages(key) {
  const list = agentPendingImages[key] || [];
  const paths = [];
  for (const img of list) {
    if (img.serverPath) { paths.push(img.serverPath); continue; }
    const fd = new FormData();
    const fname = img.fileName || img.file.name || 'screenshot.png';
    fd.append('file', img.file, fname);
    try {
      const data = await _xhrUploadForm(API_BASE + '/api/agent/upload-image', fd);
      if (data && data.ok) { img.serverPath = data.path; paths.push(data.path); }
      else { showToast('Image upload failed' + (data && data.error ? ': ' + data.error : ''), 5000); }
    } catch(e) {
      showToast('Image upload failed — ' + (e.message || 'network error'), 5000);
    }
  }
  return paths;
}

function buildTaskWithImages(task, imagePaths) {
  if (imagePaths.length === 0) return task;
  const imageExts = ['.png','.jpg','.jpeg','.gif','.webp','.bmp','.svg'];
  const refs = imagePaths.map(p => {
    const ext = (p.match(/\.[^.]+$/) || [''])[0].toLowerCase();
    const label = imageExts.includes(ext) ? 'Screenshot' : 'Attachment';
    return `[${label}: ${p}]`;
  }).join('\n');
  return `${task}\n\n${refs}`;
}

function clearAgentImages(key) {
  const list = agentPendingImages[key] || [];
  for (const img of list) URL.revokeObjectURL(img.objectUrl);
  delete agentPendingImages[key];
}

// ── Rules Panel ─────────────────────────────────────────────────────────────


function rulesTabHTML(p) {
  const pp = p.project_path || '';
  if (!pp) return '<div class="memory-section"><div class="memory-hint">Set a project path to enable rules.</div></div>';

  const loaded = rulesLoaded[p.id];
  if (!loaded) return '<div class="memory-section"><div class="memory-hint">Loading...</div></div>';

  return `<div class="memory-section">
    <div class="memory-hint">Project-specific and shared rules injected into every agent session.</div>
    <div class="rules-label">Agent Rules (AGENT_RULES.md)</div>
    <textarea spellcheck="true" class="rules-textarea" id="rules-agent-${esc(p.id)}" rows="8"
      placeholder="Project-specific rules for the agent...">${esc(loaded.agent_rules || '')}</textarea>
    <div style="display:flex;align-items:center;gap:8px;margin-top:6px;flex-shrink:0">
      <button class="btn-save-rules" onclick="saveProjectRules('${esc(p.id)}')">Save Agent Rules</button>
      <span class="rules-saved" id="rules-saved-agent-${esc(p.id)}">Saved</span>
    </div>
    <div class="rules-label" style="margin-top:14px">Shared Baseline (SHARED_RULES.md)</div>
    <textarea spellcheck="true" class="rules-textarea" id="rules-shared-${esc(p.id)}" rows="8"
      placeholder="Shared rules applied to all projects...">${esc(loaded.shared_rules || '')}</textarea>
    <div style="display:flex;align-items:center;gap:8px;margin-top:6px;flex-shrink:0">
      <button class="btn-save-rules" onclick="saveSharedRules('${esc(p.id)}')">Save Shared Rules</button>
      <span class="rules-saved" id="rules-saved-shared-${esc(p.id)}">Saved</span>
    </div>
  </div>`;
}

function rulesPanelHTML(p) {
  const pp = p.project_path || '';
  if (!pp) return '';

  const loaded = rulesLoaded[p.id];
  const hasContent = loaded && (loaded.agent_rules || loaded.shared_rules);

  return `<div class="rules-panel">
    <div class="rules-toggle" onclick="toggleRulesPanel('${esc(p.id)}')">
      <div class="section-title" style="margin-bottom:0">Rules</div>
      <span class="rules-chevron" id="rules-chevron-${esc(p.id)}">&#9654;</span>
    </div>
    <div class="rules-body" id="rules-body-${esc(p.id)}">
      <div class="rules-label">Agent Rules (AGENT_RULES.md)</div>
      <textarea spellcheck="true" class="rules-textarea" id="rules-agent-${esc(p.id)}" rows="6"
        placeholder="Loading...">${esc(loaded ? loaded.agent_rules : '')}</textarea>
      <div style="display:flex;align-items:center;gap:8px;margin-top:6px">
        <button class="btn-save-rules" onclick="saveProjectRules('${esc(p.id)}')">Save Agent Rules</button>
        <span class="rules-saved" id="rules-saved-agent-${esc(p.id)}">Saved</span>
      </div>
      <div class="rules-label" style="margin-top:14px">Shared Baseline (SHARED_RULES.md)</div>
      <textarea spellcheck="true" class="rules-textarea" id="rules-shared-${esc(p.id)}" rows="6"
        placeholder="Loading...">${esc(loaded ? loaded.shared_rules : '')}</textarea>
      <div style="display:flex;align-items:center;gap:8px;margin-top:6px">
        <button class="btn-save-rules" onclick="saveSharedRules('${esc(p.id)}')">Save Shared Rules</button>
        <span class="rules-saved" id="rules-saved-shared-${esc(p.id)}">Saved</span>
      </div>
    </div>
  </div>`;
}

async function toggleRulesPanel(projectId) {
  const body = document.getElementById(`rules-body-${projectId}`);
  const chevron = document.getElementById(`rules-chevron-${projectId}`);
  if (!body) return;

  const isOpen = body.classList.contains('open');
  body.classList.toggle('open');
  chevron?.classList.toggle('open');

  if (!isOpen && !rulesLoaded[projectId]) {
    await loadRules(projectId);
  }
}

async function loadRules(projectId) {
  try {
    const res = await fetch(API_BASE + `/api/project/${projectId}/rules`);
    const data = await res.json();
    rulesLoaded[projectId] = data;
    const agentEl = document.getElementById(`rules-agent-${projectId}`);
    const sharedEl = document.getElementById(`rules-shared-${projectId}`);
    if (agentEl) agentEl.value = data.agent_rules || '';
    if (sharedEl) sharedEl.value = data.shared_rules || '';
  } catch(e) {}
}

async function saveProjectRules(projectId) {
  const el = document.getElementById(`rules-agent-${projectId}`);
  if (!el) return;
  try {
    await fetch(API_BASE + `/api/project/${projectId}/rules`, {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ agent_rules: el.value })
    });
    if (rulesLoaded[projectId]) rulesLoaded[projectId].agent_rules = el.value;
    flashSaved(`rules-saved-agent-${projectId}`);
  } catch(e) { alert('Failed to save rules'); }
}

async function saveSharedRules(projectId) {
  const el = document.getElementById(`rules-shared-${projectId}`);
  if (!el) return;
  try {
    await fetch(API_BASE + '/api/rules/shared', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ shared_rules: el.value })
    });
    if (rulesLoaded[projectId]) rulesLoaded[projectId].shared_rules = el.value;
    flashSaved(`rules-saved-shared-${projectId}`);
  } catch(e) { alert('Failed to save shared rules'); }
}

function flashSaved(elId) {
  const el = document.getElementById(elId);
  if (!el) return;
  el.classList.add('show');
  setTimeout(() => el.classList.remove('show'), 2000);
}

// ── Auto-size name input ─────────────────────────────────────────────────────
// ── Modal tab switching ─────────────────────────────────────────────────────

async function openRulesModal(projectId) {
  const modalId = '__rules_' + projectId;
  if (openModals.has(modalId)) {
    const entry = openModals.get(modalId);
    if (entry.minimized) restoreModal(modalId);
    focusModal(modalId);
    return;
  }

  const p = allProjects.find(x => x.id === projectId);
  if (!p) return;

  if (!rulesLoaded[projectId]) {
    try {
      const res = await fetch(API_BASE + `/api/project/${projectId}/rules`);
      rulesLoaded[projectId] = await res.json();
    } catch(e) {
      rulesLoaded[projectId] = { agent_rules: '', shared_rules: '' };
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
      <h2 style="margin:0;font-size:18px;font-weight:700;color:var(--text)">Rules &mdash; ${esc(p.name || p.id)}</h2>
    </div>
    <div class="rules-editor">
      ${rulesTabHTML(p)}
    </div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);

  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);
}


// ── interop: window re-exposure for inline/generated/cross-module callers ──
window.micBtnHTML = micBtnHTML;
window.handleAgentAttachPick = handleAgentAttachPick;
window.renderAgentImagePreviews = renderAgentImagePreviews;
window.uploadAgentImages = uploadAgentImages;
window.buildTaskWithImages = buildTaskWithImages;
window.clearAgentImages = clearAgentImages;
window.flashSaved = flashSaved;
window.openRulesModal = openRulesModal;
window.removeAgentImage = removeAgentImage;
window.saveProjectRules = saveProjectRules;
window.saveSharedRules = saveSharedRules;
window.toggleAgentMic = toggleAgentMic;
window.toggleRulesPanel = toggleRulesPanel;
