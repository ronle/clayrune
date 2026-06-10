// ── Backlog actions ─────────────────────────────────────────────────────────

async function addBacklogItem(projectId) {
  const input = document.getElementById(`backlog-input-${projectId}`);
  const priSel = document.getElementById(`backlog-pri-${projectId}`);
  const text = input.value.trim();
  const files = pendingFiles.get(projectId) || [];
  if (!text && files.length === 0) return;
  if (!text) { input.focus(); return; }
  input.value = '';
  if (input.id) delete textareaValues[input.id];
  input.disabled = true;
  try {
    const res = await fetch(API_BASE + `/api/project/${projectId}/backlog`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({text, priority: priSel.value, source: 'dashboard'})
    });
    const data = await res.json();
    if (data.item && data.item.id && files.length > 0) {
      for (const file of files) {
        await uploadFile(projectId, data.item.id, file);
      }
    }
    pendingFiles.delete(projectId);
    const previewEl = document.getElementById(`create-previews-${projectId}`);
    if (previewEl) previewEl.innerHTML = '';
    await refreshSilent();
  } finally { input.disabled = false; input.focus(); }
}

async function toggleDone(e, projectId, itemId, currentStatus) {
  e.stopPropagation();
  pushUndo({type: 'status', projectId, itemId, data: {status: currentStatus}, label: currentStatus === 'done' ? 'mark open' : 'mark done'});
  await patchItem(projectId, itemId, {status: currentStatus === 'done' ? 'open' : 'done'});
}

async function cyclePriority(e, projectId, itemId, current) {
  e.stopPropagation();
  const cycle = {normal:'high', high:'low', low:'normal'};
  pushUndo({type: 'priority', projectId, itemId, data: {priority: current}, label: 'priority change'});
  await patchItem(projectId, itemId, {priority: cycle[current]||'normal'});
}

async function saveBacklogText(e, projectId, itemId) {
  const text = e.target.innerText.trim();
  if (!text) return;
  const p = allProjects.find(x => x.id === projectId);
  const oldItem = p && (p.backlog || []).find(i => i.id === itemId);
  if (oldItem && oldItem.text !== text) {
    pushUndo({type: 'text', projectId, itemId, data: {text: oldItem.text}, label: 'text edit'});
  }
  await patchItem(projectId, itemId, {text});
}

async function deleteBacklogItem(e, projectId, itemId) {
  e.stopPropagation();
  if (!confirm('Delete this item?')) return;
  const p = allProjects.find(x => x.id === projectId);
  const item = p && (p.backlog || []).find(i => i.id === itemId);
  if (item) {
    pushUndo({type: 'delete', projectId, itemId, data: {...item}, label: 'delete'});
  }
  await fetch(API_BASE + `/api/project/${projectId}/backlog/${itemId}`, {method: 'DELETE'});
  await refreshSilent();
}

async function dispatchBacklogItem(e, projectId, itemId) {
  e.stopPropagation();
  const p = allProjects.find(x => x.id === projectId);
  if (!p) return;
  const item = (p.backlog || []).find(i => i.id === itemId);
  if (!item) return;

  // Load image attachments into agentPendingImages so they transfer with dispatch
  const imageAtts = (item.attachments || []).filter(a => a.type === 'image');
  if (imageAtts.length > 0) {
    const sid = activeAgentTab[projectId];
    const fetched = [];
    for (const a of imageAtts) {
      try {
        const res = await fetch(API_BASE + `/api/attachments/${a.stored_name}`);
        const blob = await res.blob();
        const file = new File([blob], a.original_name, { type: blob.type });
        fetched.push({ file, objectUrl: URL.createObjectURL(blob), serverPath: null });
      } catch(err) {}
    }
    // Populate both keys so images survive switching between session and +New
    agentPendingImages[projectId] = [...(agentPendingImages[projectId] || []), ...fetched];
    if (sid) {
      const copies = fetched.map(f => ({ file: f.file, objectUrl: f.objectUrl, serverPath: null }));
      agentPendingImages[`fu_${sid}`] = [...(agentPendingImages[`fu_${sid}`] || []), ...copies];
    }
  }

  // Switch to agent tab, keep current session tab, pre-fill the text input
  // Also store in textareaValues for both input IDs so text survives switching to +New
  textareaValues[`agent-task-${projectId}`] = item.text;
  modalActiveTab[projectId] = 'agent';
  refreshModal();
  setTimeout(() => {
    const sid = activeAgentTab[projectId];
    const input = sid
      ? document.getElementById(`agent-followup-${sid}`)
      : document.getElementById(`agent-task-${projectId}`);
    if (input) {
      input.value = item.text;
      input.focus();
    }
  }, 50);
}

async function patchItem(projectId, itemId, data, skipUndo) {
  await fetch(API_BASE + `/api/project/${projectId}/backlog/${itemId}`, {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(data)
  });
  await refreshSilent();
}


// ── Interop: re-expose for inline/cross-module callers (generated on*= handlers
//    in tile + modal HTML, the cross-project backlog list, and the Code-sync
//    retry path). All callers are runtime-only (resolve against window at event
//    time); no parse-time references, no shared mutable state in this region. ──
window.addBacklogItem = addBacklogItem;
window.toggleDone = toggleDone;
window.cyclePriority = cyclePriority;
window.saveBacklogText = saveBacklogText;
window.deleteBacklogItem = deleteBacklogItem;
window.dispatchBacklogItem = dispatchBacklogItem;
window.patchItem = patchItem;
