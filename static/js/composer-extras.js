// ── Mic / voice transcription (Capacitor native shell only) ────────────────
// The @capacitor-community/speech-recognition plugin is only present in the
// Clayrune Android APK build. Browsers (desktop / mobile Chrome) don't have
// it, so micAvailable() returns false there and the button never renders.
function micAvailable() {
  try {
    return !!(window.Capacitor && typeof window.Capacitor.isNativePlatform === 'function'
      && window.Capacitor.isNativePlatform()
      && window.Capacitor.Plugins && window.Capacitor.Plugins.SpeechRecognition);
  } catch (_) { return false; }
}
function micBtnHTML(textareaId) {
  if (!micAvailable()) return '';
  return `<button class="btn-mic" type="button" id="btn-mic-${textareaId}"
    title="Voice input (tap to start, tap again to stop)"
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
  // responsive even if a prior session is in a stuck native state.
  const st = _micState[textareaId];
  if (st && st.active) { _stopAgentMic(textareaId); return; }
  _startAgentMic(textareaId);
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
  _micState[textareaId] = { active: true, gen, base: '', sep: '', partial: null, state: null };
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
    _micState[textareaId].base = ta.value || '';
    _micState[textareaId].sep = _micState[textareaId].base && !/\s$/.test(_micState[textareaId].base) ? ' ' : '';
    try {
      _micState[textareaId].partial = await SR.addListener('partialResults', (data) => {
        const cur = _micState[textareaId];
        if (!cur || cur.gen !== gen) return;
        const live = document.getElementById(textareaId);
        if (!live) return;
        const matches = (data && (data.matches || data.value)) || [];
        if (!matches.length) return;
        live.value = cur.base + cur.sep + String(matches[0] || '');
        try { live.dispatchEvent(new Event('input', { bubbles: true })); } catch (_) {}
      });
      _micState[textareaId].state = await SR.addListener('listeningState', (data) => {
        const cur = _micState[textareaId];
        if (!cur || cur.gen !== gen) return;
        if (data && data.status === 'stopped') _hardResetMic(textareaId);
      });
    } catch (e) { console.warn('mic listener attach failed', e); }
    // Hard-coded en-US: device-locale (he-IL, en-IL etc.) often isn't an
    // installed recognizer language pack, throws ERROR_LANGUAGE_NOT_SUPPORTED
    // immediately. en-US is universally available on Google's recognizer.
    _micToast('opening mic…');
    // popup: true uses Android's system RecognizerIntent fullscreen dialog.
    // Pros: bulletproof — Google handles the entire lifecycle (mic prompt,
    // partials, silence detection, retry), no state stickiness between
    // sessions, no silent hangs. The plugin returns the result via the
    // activity callback. Cons: fullscreen dialog instead of inline
    // streaming. Tradeoff worth it until the upstream plugin is patched.
    //
    // partialResults: true is NOT for partial events (none are delivered in
    // popup mode — results still arrive via the activity-result promise
    // below). The plugin forwards the flag as the undocumented
    // "android.speech.extra.DICTATION_MODE" intent extra, which switches the
    // system dialog to long-form dictation: it rides out thinking pauses
    // instead of finalizing at the first ~1s gap. The documented
    // EXTRA_SPEECH_INPUT_*SILENCE* knobs are ignored by Google's recognizer,
    // so this is the only silence-patience lever that works without forking
    // the plugin. Exact patience varies with the device's Google app version.
    SR.start({ language: 'en-US', maxResults: 2, partialResults: true, popup: true })
      .then((result) => {
        const cur = _micState[textareaId];
        const live = document.getElementById(textareaId);
        if (cur && cur.gen === gen && live) {
          const matches = (result && (result.matches || result.value)) || [];
          if (matches.length) {
            live.value = cur.base + cur.sep + String(matches[0] || '');
            try { live.dispatchEvent(new Event('input', { bubbles: true })); } catch (_) {}
            _micToast('got: ' + String(matches[0] || '').slice(0, 40));
          } else {
            _micToast('no transcription');
          }
        }
        _hardResetMic(textareaId);
      })
      .catch((e) => { _micToast('error: ' + (e && e.message || e)); _hardResetMic(textareaId); });
  } catch (e) {
    _micToast('mic outer fail: ' + (e && e.message || e));
    _hardResetMic(textareaId);
  }
}
function _stopAgentMic(textareaId) {
  // Tear down JS state + UI SYNCHRONOUSLY so the button is unstuck even if
  // the native stop() hangs or throws. Then fire the native stop in the
  // background; we don't await it.
  const SR = window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.SpeechRecognition;
  _hardResetMic(textareaId);
  if (SR) { try { Promise.resolve(SR.stop()).catch(() => {}); } catch (_) {} }
}
function _hardResetMic(textareaId) {
  const st = _micState[textareaId];
  if (st) {
    try { st.partial && st.partial.remove && st.partial.remove(); } catch (_) {}
    try { st.state && st.state.remove && st.state.remove(); } catch (_) {}
    try { st.softStop && clearTimeout(st.softStop); } catch (_) {}
    try { st.forceReset && clearTimeout(st.forceReset); } catch (_) {}
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
