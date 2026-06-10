// ── Search past chats by transcript content ──────────────────────────────────
// Project-scoped full-text search over the project's .jsonl transcripts
// (GET /search-chats). Results render in the same bottom pane as the preview;
// clicking one selects it for resume so the preview shows it inline.
let chatSearchQuery = {};     // projectId -> raw input string
let chatSearchResults = {};   // projectId -> { query, results: [...] }
let chatSearchLoading = {};   // projectId -> bool
const _chatSearchTimers = {}; // projectId -> debounce timer id
const _chatSearchSeq = {};    // projectId -> request sequence (drops stale responses)

function chatSearchHTML(projectId) {
  const q = chatSearchQuery[projectId] || '';
  return `<div class="chat-search">
    <span class="chat-search-icon">&#128269;</span>
    <input type="text" class="chat-search-input" id="chat-search-${esc(projectId)}"
      placeholder="Search past chats by content…" value="${esc(q)}" spellcheck="false"
      oninput="onChatSearchInput('${esc(projectId)}', this.value)"
      onkeydown="if(event.key==='Escape'){clearChatSearch('${esc(projectId)}')}">
    <span class="chat-search-clear" onclick="clearChatSearch('${esc(projectId)}')" title="Clear search">&#10005;</span>
  </div>`;
}

// Bottom pane = search results when a query is active, else the resume preview.
function searchPaneInner(projectId) {
  const q = (chatSearchQuery[projectId] || '').trim();
  if (q.length >= 2) {
    const r = chatSearchResults[projectId];
    if (r && r.query === q) return chatResultsHTML(projectId);
    return '<div class="conv-preview-empty">Searching…</div>';
  }
  return convPreviewHTML(projectId);
}

function renderAgentSearchPane(projectId) {
  const el = document.getElementById(`agent-search-pane-${projectId}`);
  if (el) el.innerHTML = searchPaneInner(projectId);
}

function _highlightTerm(text, query) {
  const safe = esc(text || '');
  if (!query) return safe;
  const q = query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  try { return safe.replace(new RegExp('(' + q + ')', 'ig'), '<mark class="cp-hit">$1</mark>'); }
  catch (e) { return safe; }
}

function chatResultsHTML(projectId) {
  const r = chatSearchResults[projectId];
  if (!r) return '';
  const q = r.query;
  if (!r.results.length) return `<div class="conv-preview-empty">No chats mention “${esc(q)}”.</div>`;
  const rows = r.results.map(item => {
    const label = (item.label || '(no label)').substring(0, 90);
    const meta = [item.ts_relative, item.matches ? `${item.matches} hit${item.matches !== 1 ? 's' : ''}` : '']
      .filter(Boolean).join(' · ');
    return `<div class="chat-result" onclick="pickChatResult('${esc(projectId)}','${esc(item.csid)}')" title="${esc(item.label)}">
      <div class="chat-result-top">
        <span class="chat-result-label">${_highlightTerm(label, q)}</span>
        <span class="chat-result-meta">${esc(meta)}</span>
      </div>
      <div class="chat-result-snippet">${_highlightTerm(item.snippet || '', q)}</div>
    </div>`;
  }).join('');
  return `<div class="chat-results">
    <div class="chat-results-head">${r.results.length} chat${r.results.length !== 1 ? 's' : ''} mention “${esc(q)}”</div>
    ${rows}
  </div>`;
}

function onChatSearchInput(projectId, value) {
  chatSearchQuery[projectId] = value;
  const q = value.trim();
  clearTimeout(_chatSearchTimers[projectId]);
  if (q.length < 2) {
    delete chatSearchResults[projectId];
    chatSearchLoading[projectId] = false;
    renderAgentSearchPane(projectId);
    return;
  }
  // Debounce the scan; the pane shows "Searching…" until results land.
  _chatSearchTimers[projectId] = setTimeout(() => runChatSearch(projectId, q), 350);
  renderAgentSearchPane(projectId);
}

async function runChatSearch(projectId, q) {
  q = (q || '').trim();
  if (q.length < 2) return;
  const seq = (_chatSearchSeq[projectId] = (_chatSearchSeq[projectId] || 0) + 1);
  chatSearchLoading[projectId] = true;
  renderAgentSearchPane(projectId);
  let results = [];
  try {
    const res = await fetch(API_BASE + `/api/project/${encodeURIComponent(projectId)}/search-chats?q=${encodeURIComponent(q)}`);
    if (res.ok) { const data = await res.json(); results = data.results || []; }
  } catch (e) { /* network/parse error → empty results */ }
  if (seq !== _chatSearchSeq[projectId]) return;            // a newer search superseded this one
  if ((chatSearchQuery[projectId] || '').trim() !== q) return;  // input moved on
  chatSearchResults[projectId] = { query: q, results };
  chatSearchLoading[projectId] = false;
  renderAgentSearchPane(projectId);
}

function clearChatSearch(projectId) {
  clearTimeout(_chatSearchTimers[projectId]);
  chatSearchQuery[projectId] = '';
  delete chatSearchResults[projectId];
  chatSearchLoading[projectId] = false;
  const inp = document.getElementById(`chat-search-${projectId}`);
  if (inp) inp.value = '';
  renderAgentSearchPane(projectId);
  setTimeout(() => document.getElementById(`chat-search-${projectId}`)?.focus(), 0);
}

// Click a search result → select it for resume (preview shows it) and clear the
// search so the bottom pane flips from results back to the inline preview.
function pickChatResult(projectId, csid) {
  clearTimeout(_chatSearchTimers[projectId]);
  delete chatSearchResults[projectId];
  chatSearchQuery[projectId] = '';
  chatSearchLoading[projectId] = false;
  const inp = document.getElementById(`chat-search-${projectId}`);
  if (inp) inp.value = '';  // keep refreshModal's value-preservation from restoring the old query
  selectResumeSession(projectId, csid);  // sets pendingResumeId + refreshModal + focuses composer
}

// ── Interop: re-expose for inline callers + generated on*= handlers (module 10) ──
// Inline/cross-module callers (agentPanelHTML modal builder): chatSearchHTML, searchPaneInner.
// Generated on*= handler targets (resolve against window at event time): onChatSearchInput,
// clearChatSearch, pickChatResult. State globals + renderAgentSearchPane/_highlightTerm/
// chatResultsHTML/runChatSearch stay module-private (zero outside refs, formal scan).
window.chatSearchHTML = chatSearchHTML;
window.searchPaneInner = searchPaneInner;
window.onChatSearchInput = onChatSearchInput;
window.clearChatSearch = clearChatSearch;
window.pickChatResult = pickChatResult;
