// ── Create-form attachment handling ──────────────────────────────────────────


function handleCreatePaste(e, projectId) {
  const items = Array.from(e.clipboardData?.items || []);
  const imageItems = items.filter(i => i.type.startsWith('image/'));
  if (imageItems.length === 0) return;
  e.preventDefault();
  const files = pendingFiles.get(projectId) || [];
  for (const item of imageItems) {
    const blob = item.getAsFile();
    if (blob) files.push(blob);
  }
  pendingFiles.set(projectId, files);
  renderCreatePreviews(projectId);
}

function createDragOver(e, projectId) {
  e.preventDefault();
  e.stopPropagation();
  document.getElementById(`backlog-input-${projectId}`)?.classList.add('drag-over');
}

function createDragLeave(projectId) {
  document.getElementById(`backlog-input-${projectId}`)?.classList.remove('drag-over');
}

function createDrop(e, projectId) {
  e.preventDefault();
  e.stopPropagation();
  createDragLeave(projectId);
  const droppedFiles = Array.from(e.dataTransfer.files);
  if (droppedFiles.length === 0) return;
  const files = pendingFiles.get(projectId) || [];
  files.push(...droppedFiles);
  pendingFiles.set(projectId, files);
  renderCreatePreviews(projectId);
}

function renderCreatePreviews(projectId) {
  const container = document.getElementById(`create-previews-${projectId}`);
  if (!container) return;
  const files = pendingFiles.get(projectId) || [];
  container.innerHTML = files.map((file, i) => {
    const isImage = file.type.startsWith('image/');
    const thumbContent = isImage
      ? `<img src="${URL.createObjectURL(file)}" alt="${esc(file.name)}">`
      : `<div class="file-icon">📎</div>`;
    return `
      <div class="create-preview-item" title="${esc(file.name)}">
        ${thumbContent}
        <div class="file-name">${esc(file.name)}</div>
        <button class="create-preview-remove" onclick="removeCreateFile('${esc(projectId)}',${i})">✕</button>
      </div>`;
  }).join('');
}

function removeCreateFile(projectId, index) {
  const files = pendingFiles.get(projectId) || [];
  files.splice(index, 1);
  if (files.length === 0) pendingFiles.delete(projectId);
  else pendingFiles.set(projectId, files);
  renderCreatePreviews(projectId);
}

// ── Proactive Clayrune-update notification ──────────────────────────────────
// Polls the server's CACHED update status (cheap, no git fetch on the
// dashboard side -- the server's background loop refreshes every 6h).
// When an update is available:
//   - Adds .has-update on the Settings sidebar entry (always visible)
//   - Shows a one-time toast IF this commit's notification hasn't been
//     dismissed yet (localStorage 'mc_update_dismissed_for' tracks the
//     remote_commit the user already saw)
//   - "Remind me later" defers re-showing the toast for 24h regardless of
//     commit (localStorage 'mc_update_remind_after_ts')
//
// User clicks "Update now" → opens Settings; the existing Update Clayrune
// row handles the actual git pull + restart.
async function checkClayruneUpdateAvailable() {
  let data;
  try {
    const res = await fetch(API_BASE + '/api/system/update/cached');
    if (!res.ok) return;
    data = await res.json();
  } catch (e) { return; }

  // Reflect on sidebar regardless of toast state — passive, always honest.
  const settingsItem = document.querySelector('.sidebar-item[data-nav="settings"]');
  if (settingsItem) {
    settingsItem.classList.toggle('has-update', !!data.update_available);
  }

  if (!data.update_available || !data.remote_commit) return;

  // Dismissal honor checks
  const dismissedFor = localStorage.getItem('mc_update_dismissed_for');
  if (dismissedFor === data.remote_commit) return; // already saw THIS commit

  const remindAfter = parseInt(localStorage.getItem('mc_update_remind_after_ts') || '0', 10);
  if (remindAfter && Date.now() < remindAfter) return; // snoozed window

  // Build a brief message: "<N> behind · <ver> → <newver>"
  const n = data.behind || 0;
  const cur = data.version || (data.commit || '').slice(0, 7);
  const remote = data.remote_version || (data.remote_commit || '').slice(0, 7);
  const msg = `<strong>Clayrune update available</strong><br>` +
              `<span style="color:var(--text-faint);font-size:12px">` +
              `${n} commit${n === 1 ? '' : 's'} behind &middot; ` +
              `${esc(cur)} → ${esc(remote)}</span>`;

  showActionToast(msg, [
    {
      label: 'Later',
      onclick: () => {
        // Snooze 24h
        localStorage.setItem('mc_update_remind_after_ts', String(Date.now() + 24 * 3600 * 1000));
      },
    },
    {
      label: 'Dismiss',
      onclick: () => {
        // Don't bug the user again about THIS specific commit. They'll get
        // a fresh toast when a new commit lands.
        localStorage.setItem('mc_update_dismissed_for', data.remote_commit);
      },
    },
    {
      label: 'Update',
      primary: true,
      onclick: () => {
        // Open Settings; the existing Update Clayrune row handles the
        // actual pull + restart prompt.
        try { sidebarNav('settings'); } catch (e) {}
        // Clear any prior dismissal for this commit so it doesn't reappear
        // as "dismissed" if they bail out of the Settings flow.
        localStorage.removeItem('mc_update_dismissed_for');
      },
    },
  ], { dismissOnAction: true });
}

function timeAgoJS(ts) {
  if (!ts) return 'never';
  try {
    const d = new Date(ts);
    const secs = Math.floor((Date.now() - d.getTime()) / 1000);
    if (secs < 60) return `${secs}s ago`;
    if (secs < 3600) return `${Math.floor(secs/60)}m ago`;
    if (secs < 86400) return `${Math.floor(secs/3600)}h ago`;
    return `${Math.floor(secs/86400)}d ago`;
  } catch { return ts; }
}

async function githubConnect(projectId) {
  const input = document.getElementById(`gh-repo-${projectId}`);
  if (!input) return;
  const repo = input.value.trim();
  if (!repo) { input.focus(); return; }
  if (!/^[a-zA-Z0-9._-]+\/[a-zA-Z0-9._-]+$/.test(repo)) {
    alert('Invalid format — use owner/repo');
    input.focus();
    return;
  }
  document.querySelectorAll('.modal-menu-dropdown.open').forEach(d => d.classList.remove('open'));
  try {
    const res = await fetch(API_BASE + `/api/project/${projectId}/github/setup`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({repo})
    });
    const data = await res.json();
    if (data.error) { alert(data.error); return; }
    await refreshSilent();
  } catch (e) { alert('Connection failed: ' + e.message); }
}

async function githubDisconnect(projectId) {
  if (!confirm('Disconnect GitHub sync? Existing items will be kept.')) return;
  document.querySelectorAll('.modal-menu-dropdown.open').forEach(d => d.classList.remove('open'));
  await fetch(API_BASE + `/api/project/${projectId}/github/disconnect`, {method: 'POST'});
  await refreshSilent();
}

async function githubSyncNow(projectId) {
  const badge = document.getElementById(`gh-badge-${projectId}`);
  if (badge) badge.classList.add('syncing');
  document.querySelectorAll('.modal-menu-dropdown.open').forEach(d => d.classList.remove('open'));
  try {
    const res = await fetch(API_BASE + `/api/project/${projectId}/github/sync`, {method: 'POST'});
    const data = await res.json();
    if (data.error) alert(data.error);
    await refreshSilent();
  } catch (e) { alert('Sync failed: ' + e.message); }
  finally {
    if (badge) badge.classList.remove('syncing');
  }
}

// ── Code sync (spike — read-only) ────────────────────────────────────────
async function codeSyncEnable(projectId) {
  document.querySelectorAll('.modal-menu-dropdown.open').forEach(d => d.classList.remove('open'));
  try {
    const res = await fetch(API_BASE + `/api/project/${projectId}/code-sync/enable`, {method: 'POST'});
    const data = await res.json();
    if (data.error) { alert('Enable failed: ' + data.error); return; }
    await refreshSilent();
  } catch (e) { alert('Enable failed: ' + e.message); }
}

async function codeSyncDisable(projectId) {
  if (!confirm('Disable code sync? The hidden worktree and remote branch are left in place.')) return;
  document.querySelectorAll('.modal-menu-dropdown.open').forEach(d => d.classList.remove('open'));
  await fetch(API_BASE + `/api/project/${projectId}/code-sync/disable`, {method: 'POST'});
  await refreshSilent();
}

async function codeSyncNow(projectId) {
  document.querySelectorAll('.modal-menu-dropdown.open').forEach(d => d.classList.remove('open'));
  try {
    const res = await fetch(API_BASE + `/api/project/${projectId}/code-sync/sync`, {method: 'POST'});
    const data = await res.json();
    if (data.error) alert(data.error);
    await refreshSilent();
  } catch (e) { alert('Code sync failed: ' + e.message); }
}

async function codeSyncShowIncoming(projectId) {
  document.querySelectorAll('.modal-menu-dropdown.open').forEach(d => d.classList.remove('open'));
  let status;
  try {
    const res = await fetch(API_BASE + `/api/project/${projectId}/code-sync/status`);
    status = await res.json();
    if (status.error) { alert(status.error); return; }
  } catch (e) { alert('Failed to load incoming: ' + e.message); return; }

  const groups = status.incoming || [];
  const totalCommits = groups.reduce((n, g) => n + (g.commits || []).length, 0);

  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.style.cssText = 'position:fixed;inset:0;background:rgba(0,0,0,0.55);z-index:9000;display:flex;align-items:center;justify-content:center;padding:20px';
  overlay.onclick = (e) => { if (e.target === overlay) overlay.remove(); };

  const body = totalCommits === 0
    ? `<div style="padding:24px;text-align:center;color:var(--text-dim);font-size:13px">
         Nothing incoming. Either the other side hasn't pushed yet, or
         everything they pushed is already on <strong>${esc(status.working_branch||'main')}</strong>.
       </div>`
    : groups.map(g => `
        <div style="border:1px solid var(--border);border-radius:8px;padding:10px 12px;margin-bottom:10px">
          <div style="font-size:11px;color:var(--text-dim);margin-bottom:6px">
            ${esc(g.install_label||g.branch)} &middot;
            <span style="color:var(--text-faint);font-family:monospace">${esc(g.branch)}</span>
          </div>
          ${(g.commits||[]).map(c => `
            <div style="display:flex;gap:10px;align-items:flex-start;padding:6px 0;border-top:1px solid var(--border-soft,var(--border))">
              <code style="color:var(--accent);font-size:11px;flex-shrink:0">${esc(c.short)}</code>
              <div style="flex:1;min-width:0">
                <div style="font-size:12px">${esc(c.subject||'(no subject)')}</div>
                <div style="font-size:10px;color:var(--text-faint)">${esc(c.author_name||'')} &middot; ${esc(c.authored_at||'')}</div>
              </div>
              <button class="btn-small" style="font-size:10px;padding:3px 8px"
                onclick="codeSyncDismiss('${esc(projectId)}','${esc(c.sha)}', this)">Reject</button>
            </div>
          `).join('')}
        </div>
      `).join('');

  overlay.innerHTML = `
    <div style="background:var(--bg-card,var(--bg));border:1px solid var(--border);border-radius:10px;max-width:720px;width:100%;max-height:80vh;overflow:auto;padding:18px 20px;color:var(--text)">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
        <h3 style="margin:0;font-size:14px">Incoming code from other installs</h3>
        <button class="btn-small" onclick="this.closest('.modal-overlay').remove()">Close</button>
      </div>
      <div style="font-size:11px;color:var(--text-dim);margin-bottom:12px">
        ${totalCommits} commit${totalCommits===1?'':'s'} across ${groups.length} branch${groups.length===1?'':'es'}.
        Accept is not wired yet — spike is read-only. Reject hides a commit from this list locally.
      </div>
      ${body}
    </div>`;
  document.body.appendChild(overlay);
}

async function codeSyncDismiss(projectId, sha, btn) {
  if (btn) { btn.disabled = true; btn.textContent = '...'; }
  try {
    const res = await fetch(API_BASE + `/api/project/${projectId}/code-sync/dismiss`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({sha})
    });
    const data = await res.json();
    if (data.error) { alert(data.error); if (btn) { btn.disabled = false; btn.textContent = 'Reject'; } return; }
    // Pop this row out of the modal
    if (btn) {
      const row = btn.parentElement;
      if (row) row.remove();
    }
  } catch (e) {
    alert('Reject failed: ' + e.message);
    if (btn) { btn.disabled = false; btn.textContent = 'Reject'; }
  }
}

function toggleShowDone(e, projectId) {
  e.stopPropagation();
  showDoneMap[projectId] = !showDoneMap[projectId];
  refreshModal();
}

function pushUndo(entry) {
  undoStack.push(entry);
  if (undoStack.length > 20) undoStack.shift();
}

async function performUndo(e) {
  e.stopPropagation();
  const entry = undoStack.pop();
  if (!entry) return;
  if (entry.type === 'status' || entry.type === 'priority' || entry.type === 'text') {
    await patchItem(entry.projectId, entry.itemId, entry.data, true);
  } else if (entry.type === 'delete') {
    // Re-create the deleted item via POST, then PATCH to restore its full state
    const res = await fetch(API_BASE + `/api/project/${entry.projectId}/backlog`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({text: entry.data.text, priority: entry.data.priority || 'normal', source: entry.data.source || 'dashboard'})
    });
    if (res.ok && entry.data.status === 'done') {
      const result = await res.json();
      await fetch(API_BASE + `/api/project/${entry.projectId}/backlog/${result.item.id}`, {
        method: 'PATCH',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({status: 'done'})
      });
    }
    await refreshSilent();
  }
}

// ── Attachment helpers ──────────────────────────────────────────────────────

function attHTML(a, projectId, itemId) {
  const url = API_BASE + `/api/attachments/${a.stored_name}`;
  // `_present` is decorated server-side by _decorate_attachments — false when
  // the stored file no longer exists on disk. Skip the <img> entirely in that
  // case so we don't generate console-error 404s for orphaned records; show
  // the generic 📎 icon (same as non-image attachments) so the row stays
  // visible and deletable.
  const isImage = a.type === 'image' && a._present !== false;
  const missing = a._present === false;
  const icon = missing ? '⚠️' : (a.type === 'pdf' ? '📄' : '📎');
  const kb = a.size ? (a.size < 1024*1024 ? (a.size/1024).toFixed(0)+'KB' : (a.size/1024/1024).toFixed(1)+'MB') : '';
  const thumb = isImage
    ? `<img class="att-thumb" src="${url}" alt="${esc(a.original_name)}" onerror="this.style.display='none'">`
    : `<div class="att-icon" title="${missing ? 'File missing on disk' : ''}">${icon}</div>`;
  return `
  <div class="att-item" data-att-id="${esc(a.id)}">
    ${thumb}
    <div class="att-info">
      <div class="att-name" title="${esc(a.original_name)}">${esc(a.original_name)}</div>
      <div class="att-size">${kb}</div>
    </div>
    <a href="${url}" target="_blank" rel="noopener"><button class="att-open">Open</button></a>
    <button class="att-del" onclick="deleteAttachment(event,'${projectId}','${itemId}','${esc(a.id)}')" title="Remove">✕</button>
  </div>`;
}

function toggleAttPanel(e, projectId, itemId) {
  e.stopPropagation();
  const panel = document.getElementById('att-'+itemId);
  if (panel) {
    panel.classList.toggle('open');
    if (panel.classList.contains('open')) openAttPanels.add(itemId);
    else openAttPanels.delete(itemId);
  }
}

function noteHTML(n) {
  const ts = (n && n.ts) ? String(n.ts).slice(0, 10) : '';
  const code = esc(n && n.agent_code || 'user');
  const text = esc(n && n.text || '');
  return `<div class="backlog-note"><span class="note-meta">${esc(ts)} · ${code} ·</span>${text}</div>`;
}

function toggleNotesPanel(e, projectId, itemId) {
  e.stopPropagation();
  const panel = document.getElementById('notes-'+itemId);
  if (panel) {
    panel.classList.toggle('open');
    if (panel.classList.contains('open')) {
      openNotesPanels.add(itemId);
      const input = document.getElementById('noteinput-'+itemId);
      if (input) setTimeout(() => input.focus(), 50);
    } else {
      openNotesPanels.delete(itemId);
    }
  }
}

async function submitNote(projectId, itemId) {
  const input = document.getElementById('noteinput-'+itemId);
  if (!input) return;
  const text = input.value.trim();
  if (!text) return;
  input.disabled = true;
  try {
    const res = await fetch(API_BASE + `/api/project/${projectId}/backlog/${itemId}/note`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ text, agent_code: 'user' }),
    });
    if (res.ok) {
      input.value = '';
      openNotesPanels.add(itemId);
      await refreshProjectBacklog(projectId);
    }
  } catch(e) {
  } finally {
    input.disabled = false;
    const refreshedInput = document.getElementById('noteinput-'+itemId);
    if (refreshedInput) refreshedInput.focus();
  }
}

function attDragOver(e, itemId) {
  e.preventDefault();
  e.stopPropagation();
  document.getElementById('drop-'+itemId)?.classList.add('drag-over');
}
function attDragLeave(itemId) {
  document.getElementById('drop-'+itemId)?.classList.remove('drag-over');
}
async function attDrop(e, projectId, itemId) {
  e.preventDefault();
  e.stopPropagation();
  attDragLeave(itemId);
  const files = Array.from(e.dataTransfer.files);
  for (const file of files) await uploadFile(projectId, itemId, file);
}
async function attFileSelected(e, projectId, itemId) {
  const files = Array.from(e.target.files);
  for (const file of files) await uploadFile(projectId, itemId, file);
  e.target.value = '';
}

// Upload a FormData via XMLHttpRequest, NOT fetch.
//
// On the Clayrune Android APK the injected fetch override (MainActivity
// injectFetchOverride) reroutes every body-bearing fetch through a native
// HTTP bridge that serializes the body with String(body) — turning a
// FormData into the literal "[object FormData]" and silently dropping the
// file (server sees no 'file' part → 400, the UI swallows it). XHR is only
// header-decorated by that same override (CF-Access-* added in XP.send), so
// the multipart body — boundary and all — reaches the server intact. On
// desktop, where no override is installed, this is a plain XHR upload.
function _xhrUploadForm(url, formData, timeoutMs = 60000) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', url);
    xhr.timeout = timeoutMs;
    xhr.onload = () => {
      try { resolve(JSON.parse(xhr.responseText || '{}')); }
      catch (e) { reject(new Error('bad upload response')); }
    };
    xhr.onerror = () => reject(new Error('network error'));
    xhr.ontimeout = () => reject(new Error('timed out'));
    xhr.send(formData);
  });
}

async function uploadFile(projectId, itemId, file) {
  const listEl = document.getElementById('attlist-'+itemId);
  const uploading = document.createElement('div');
  uploading.className = 'att-uploading';
  uploading.textContent = `Uploading ${file.name}...`;
  listEl?.appendChild(uploading);
  try {
    const fd = new FormData();
    fd.append('file', file);
    const data = await _xhrUploadForm(API_BASE + `/api/project/${projectId}/backlog/${itemId}/attachments`, fd);
    if (data && data.ok) {
      uploading.remove();
      openAttPanels.add(itemId);
      await refreshSilent();
    } else {
      uploading.textContent = 'Upload failed' + (data && data.error ? ': ' + data.error : '');
      setTimeout(() => uploading.remove(), 3000);
    }
  } catch(err) {
    uploading.textContent = 'Upload failed — ' + (err.message || 'error');
    setTimeout(() => uploading.remove(), 3000);
  }
}

async function deleteAttachment(e, projectId, itemId, attId) {
  e.stopPropagation();
  if (!confirm('Remove this attachment?')) return;
  openAttPanels.add(itemId);
  await fetch(API_BASE + `/api/project/${projectId}/backlog/${itemId}/attachments/${attId}`, {method:'DELETE'});
  await refreshSilent();
}


// ── interop: window re-exposure for inline/generated/cross-module callers ──
window.handleCreatePaste = handleCreatePaste;
window.createDragOver = createDragOver;
window.createDragLeave = createDragLeave;
window.createDrop = createDrop;
window.checkClayruneUpdateAvailable = checkClayruneUpdateAvailable;
window.timeAgoJS = timeAgoJS;
window.githubConnect = githubConnect;
window.githubDisconnect = githubDisconnect;
window.githubSyncNow = githubSyncNow;
window.codeSyncEnable = codeSyncEnable;
window.codeSyncDisable = codeSyncDisable;
window.codeSyncNow = codeSyncNow;
window.codeSyncShowIncoming = codeSyncShowIncoming;
window.toggleShowDone = toggleShowDone;
window.pushUndo = pushUndo;
window.performUndo = performUndo;
window.attHTML = attHTML;
window.toggleAttPanel = toggleAttPanel;
window.noteHTML = noteHTML;
window.toggleNotesPanel = toggleNotesPanel;
window.submitNote = submitNote;
window.attDragOver = attDragOver;
window.attDragLeave = attDragLeave;
window.attDrop = attDrop;
window.attFileSelected = attFileSelected;
window._xhrUploadForm = _xhrUploadForm;
window.uploadFile = uploadFile;
window.codeSyncDismiss = codeSyncDismiss;
window.deleteAttachment = deleteAttachment;
window.removeCreateFile = removeCreateFile;
