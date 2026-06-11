// ── Project Path Editor ──────────────────────────────────────────────────────

async function saveProjectName(projectId, inputEl) {
  const val = inputEl.value.trim();
  if (!val) { inputEl.value = allProjects.find(x => x.id === projectId)?.name || projectId; return; }
  const p = allProjects.find(x => x.id === projectId);
  if (p && p.name === val) return;
  try {
    await fetch(API_BASE + `/api/project/${projectId}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ name: val })
    });
    await refreshSilent();
  } catch(e) {}
}

async function saveDomainFromMenu(e, projectId, domain) {
  e.stopPropagation();
  document.querySelectorAll('.modal-menu-dropdown.open').forEach(d => d.classList.remove('open'));
  const p = allProjects.find(x => x.id === projectId);
  if (p && p.domain === domain) return;
  try {
    await fetch(API_BASE + `/api/project/${projectId}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ domain })
    });
    await refreshSilent();
  } catch(e) {}
}

async function addDomainFromMenu(e, projectId, value) {
  e.stopPropagation();
  const name = value.trim();
  if (!name) return;
  const id = name.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
  if (!id) return;
  try {
    const res = await fetch(API_BASE + '/api/settings/domains/add', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ id, label: name })
    });
    const data = await res.json();
    if (!data.ok && data.error !== 'domain already exists') return;
    await fetchDomains();
    renderDomainFilters();
    await saveDomainFromMenu(e, projectId, id);
  } catch(err) {}
}

async function setDomainColorFromMenu(e, projectId, domainId, color, bg) {
  e.stopPropagation();
  const domain = domainsList.find(d => d.id === domainId);
  if (!domain) return;
  domain.color = color;
  domain.bg = bg;
  try {
    await fetch(API_BASE + `/api/settings/domains/${domainId}`, {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ color, bg })
    });
    document.querySelectorAll('.modal-menu-dropdown.open').forEach(d => d.classList.remove('open'));
    renderDomainFilters();
    await refreshSilent();
  } catch(err) {}
}

async function saveProjectPath(projectId, inputEl) {
  const val = inputEl.value.trim();
  const p = allProjects.find(x => x.id === projectId);
  if (p && p.project_path === val) return; // no change
  try {
    await fetch(API_BASE + `/api/project/${projectId}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ project_path: val })
    });
    await refreshSilent();
  } catch(e) {}
}

// ── Folder picker (project_path) ────────────────────────────────────────────
let _fpState = { projectId: null, currentPath: '', parent: null, home: '', workspaceBase: '' };

function openFolderPicker(projectId) {
  _fpState.projectId = projectId;
  const p = allProjects.find(x => x.id === projectId);
  const startPath = (p && p.project_path) || '';

  let overlay = document.getElementById('fp-overlay');
  if (!overlay) {
    overlay = document.createElement('div');
    overlay.id = 'fp-overlay';
    overlay.className = 'fp-overlay';
    overlay.onclick = (e) => { if (e.target === overlay) closeFolderPicker(); };
    overlay.innerHTML = `
      <div class="fp-dialog">
        <div class="fp-header">
          <div class="fp-title">Choose project folder</div>
          <button class="fp-close" onclick="closeFolderPicker()">&times;</button>
        </div>
        <div class="fp-path-bar">
          <button class="fp-btn" onclick="fpGoUp()" title="Parent folder">&#x2191;</button>
          <input id="fp-path-input" type="text" placeholder="Type or paste a path..."
            onkeydown="if(event.key==='Enter'){fpLoad(this.value)}">
          <button class="fp-btn" onclick="fpLoad(document.getElementById('fp-path-input').value)">Go</button>
        </div>
        <div class="fp-shortcuts" id="fp-shortcuts"></div>
        <div class="fp-list" id="fp-list"></div>
        <div class="fp-footer">
          <div class="fp-new-folder">
            <input id="fp-new-name" type="text" placeholder="New folder name..."
              onkeydown="if(event.key==='Enter'){fpCreateFolder()}">
            <button class="fp-btn" onclick="fpCreateFolder()">+ Create</button>
          </div>
          <div class="fp-actions">
            <button class="fp-btn" onclick="closeFolderPicker()">Cancel</button>
            <button class="fp-btn primary" id="fp-select" onclick="fpSelectCurrent()">Use this folder</button>
          </div>
        </div>
      </div>`;
    document.body.appendChild(overlay);
  } else {
    overlay.style.display = 'flex';
  }
  fpLoad(startPath);
}

function closeFolderPicker() {
  const overlay = document.getElementById('fp-overlay');
  if (overlay) overlay.remove();
  _fpState.projectId = null;
}

async function fpLoad(path) {
  const list = document.getElementById('fp-list');
  if (!list) return;
  list.innerHTML = '<div class="fp-empty">Loading...</div>';
  try {
    const url = API_BASE + '/api/browse/folders' + (path ? '?path=' + encodeURIComponent(path) : '');
    const r = await fetch(url);
    const data = await r.json();
    if (!r.ok) {
      list.innerHTML = `<div class="fp-error">${esc(data.error || 'Failed to load')}</div>`;
      return;
    }
    _fpState.currentPath = data.path;
    _fpState.parent = data.parent;
    _fpState.home = data.home || '';
    _fpState.workspaceBase = data.workspace_base || '';
    const pathInput = document.getElementById('fp-path-input');
    if (pathInput) pathInput.value = data.path;
    fpRenderShortcuts();

    if (!data.folders || !data.folders.length) {
      list.innerHTML = '<div class="fp-empty">No subfolders here. Create one below, or use this folder.</div>';
      return;
    }
    list.innerHTML = data.folders.map(f =>
      `<div class="fp-row" onclick="fpLoad('${esc(f.path).replace(/'/g, "\\'")}')">` +
      `<span class="fp-icon">&#x1F4C1;</span><span>${esc(f.name)}</span></div>`
    ).join('');
  } catch (e) {
    list.innerHTML = `<div class="fp-error">${esc(String(e))}</div>`;
  }
}

function fpRenderShortcuts() {
  const sc = document.getElementById('fp-shortcuts');
  if (!sc) return;
  const items = [];
  if (_fpState.workspaceBase) items.push({ label: 'Workspace', path: _fpState.workspaceBase });
  if (_fpState.home) items.push({ label: 'Home', path: _fpState.home });
  sc.innerHTML = items.map(i =>
    `<button class="fp-shortcut" onclick="fpLoad('${esc(i.path).replace(/'/g, "\\'")}')">${esc(i.label)}</button>`
  ).join('');
}

function fpGoUp() {
  if (_fpState.parent) fpLoad(_fpState.parent);
}

async function fpCreateFolder() {
  const nameEl = document.getElementById('fp-new-name');
  const name = (nameEl ? nameEl.value : '').trim();
  if (!name) return;
  try {
    const r = await fetch(API_BASE + '/api/browse/create_folder', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ parent: _fpState.currentPath, name })
    });
    const data = await r.json();
    if (!r.ok) { alert(data.error || 'Failed to create folder'); return; }
    if (nameEl) nameEl.value = '';
    fpLoad(data.path); // jump into the freshly-created folder
  } catch (e) {
    alert(String(e));
  }
}

async function fpSelectCurrent() {
  const projectId = _fpState.projectId;
  const chosen = _fpState.currentPath;
  if (!projectId || !chosen) { closeFolderPicker(); return; }
  try {
    await fetch(API_BASE + `/api/project/${projectId}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ project_path: chosen })
    });
    await refreshSilent();
    refreshModalById(projectId);
  } catch (e) {}
  closeFolderPicker();
}

async function importFromProject(projectId) {
  try {
    const res = await fetch(API_BASE + `/api/project/${projectId}/import`, { method: 'POST' });
    const data = await res.json();
    if (!data.ok) { alert(data.error || 'Import failed'); return; }
    const imp = data.imported || {};
    const parts = [];
    if (imp.activity_log) parts.push(`${imp.activity_log} log entries`);
    if (imp.backlog) parts.push(`${imp.backlog} backlog items`);
    if (imp.description) parts.push('description');
    if (imp.current_task) parts.push('current task');
    alert(parts.length > 0 ? `Imported: ${parts.join(', ')}` : 'No new data found in CHANGELOG.md');
    await refreshSilent();
  } catch(e) { alert('Import failed: ' + e.message); }
}

// ── Shared Rules Editor (Header-level) ──────────────────────────────────────

async function openSharedRulesEditor() {
  const modalId = '__shared_rules';
  if (openModals.has(modalId)) {
    const entry = openModals.get(modalId);
    if (entry.minimized) restoreModal(modalId);
    focusModal(modalId);
    return;
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
        <button class="modal-minimize" onclick="minimizeModal('${modalId}')" title="Minimize">&#x2015;</button>
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
      <h2 style="margin:0;font-size:18px;font-weight:700;color:var(--text)">Shared Baseline Rules</h2>
      <div class="rules-hint" style="margin:4px 0 0">SHARED_RULES.md &mdash; applies to all projects</div>
    </div>
    <div class="shared-rules-editor">
      <textarea spellcheck="true" class="rules-textarea" id="shared-rules-global" rows="16"
        placeholder="Enter shared rules that apply to all projects..."></textarea>
      <div style="display:flex;align-items:center;gap:8px;margin-top:10px;flex-shrink:0">
        <button class="btn-save-rules" onclick="saveSharedRulesGlobal()">Save</button>
        <span class="rules-saved" id="shared-rules-global-saved">Saved</span>
      </div>
    </div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);

  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);

  try {
    const res = await fetch(API_BASE + '/api/rules/shared');
    const data = await res.json();
    const el = document.getElementById('shared-rules-global');
    if (el) el.value = data.shared_rules || '';
  } catch(e) {}
}

async function saveSharedRulesGlobal() {
  const el = document.getElementById('shared-rules-global');
  if (!el) return;
  try {
    await fetch(API_BASE + '/api/rules/shared', {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ shared_rules: el.value })
    });
    // Update any cached shared rules in rulesLoaded
    for (const k of Object.keys(rulesLoaded)) {
      rulesLoaded[k].shared_rules = el.value;
    }
    flashSaved('shared-rules-global-saved');
  } catch(e) { alert('Failed to save shared rules'); }
}

// ── Plan Viewer ─────────────────────────────────────────────────────────────

function openPlanViewer(sessionId) {
  const modalId = `__plan_${sessionId}`;

  // If already open, focus it
  if (openModals.has(modalId)) {
    const entry = openModals.get(modalId);
    if (entry.minimized) restoreModal(modalId);
    focusModal(modalId);
    return;
  }

  // Get content: prefer specific plan content, fall back to full output buffer
  const lines = planViewerContent[sessionId] || agentOutputBuffers[sessionId] || [];
  const usingFullBuffer = !planViewerContent[sessionId];

  // Determine title from session info
  const cached = agentStatusCache[sessionId];
  const taskTitle = cached ? (cached.task || '').substring(0, 80) : 'Agent Output';

  // Render lines with rich formatting (same logic as outputLines builder)
  const flatLines = lines.flatMap(l => l.trimStart().startsWith('> ') ? [l] : l.split('\n'));
  let bodyHTML = '';
  let tableLines = [];
  let mermaidLines = null;  // null when not in a mermaid block
  function flushTable() {
    if (tableLines.length === 0) return;
    if (isPipeTable(tableLines)) {
      bodyHTML += `<div class="hl-table">${buildPipeTable(tableLines)}</div>`;
    } else {
      bodyHTML += `<div class="hl-table-pre">${tableLines.map(l => formatTableLine(esc(l))).join('\n')}</div>`;
    }
    tableLines = [];
  }
  for (const line of flatLines) {
    // Mermaid block detection — same shape as outputLines builder
    if (mermaidLines === null && /^\s*```\s*mermaid\b/.test(line)) {
      flushTable();
      mermaidLines = [];
      continue;
    }
    if (mermaidLines !== null) {
      if (/^\s*```\s*$/.test(line)) {
        bodyHTML += _mermaidPlaceholderHTML(mermaidLines.join('\n'));
        mermaidLines = null;
      } else {
        mermaidLines.push(line);
      }
      continue;
    }
    if (isTableLine(line)) {
      tableLines.push(line);
    } else if (tableLines.length > 0 && line.trim() === '') {
      tableLines.push(line);
    } else {
      flushTable();
      const cls = agentLineCls(line);
      // Same prompt-vs-special split as the other render paths.
      const html = cls.includes('agent-line-prompt')
        ? escPromptWithImages(line)
        : (cls.includes('agent-line-tool') || cls.includes('agent-line-error') || cls.includes('agent-line-followup') || cls.includes('agent-line-queued')
            ? esc(line) : formatAgentText(line));
      bodyHTML += `<div class="${cls}">${html}</div>`;
    }
  }
  flushTable();

  const headerLabel = usingFullBuffer ? 'Agent Output' : 'Plan Viewer';

  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content plan-viewer-content';
  content.innerHTML = `
    <div class="modal-window-controls">
      <button class="modal-minimize" onclick="minimizeModal('${esc(modalId)}')" title="Minimize">&#x2015;</button>
      <button class="modal-close" onclick="closeModalById('${esc(modalId)}')" title="Close">&#10005;</button>
    </div>
    <div class="plan-viewer-header modal-header">
      <h3 style="color:#c4b5fd;margin:0;font-size:15px">${esc(headerLabel)}</h3>
      <div style="color:var(--text-faint);font-size:12px;margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(taskTitle)}</div>
    </div>
    <div class="plan-viewer-body">${bodyHTML}</div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);

  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);
  // Render mermaid placeholders inserted by the body builder above.
  _renderAllMermaidPlaceholders(content);
}

function planFileLabel(task) {
  if (!task) return 'Plan';
  const t = task.length > 40 ? task.slice(0, 37) + '...' : task;
  return t.charAt(0).toUpperCase() + t.slice(1);
}

async function openPlanFileViewer(projectId, sessionId) {
  const modalId = `__planfile_${sessionId}`;

  // If already open, focus it
  if (openModals.has(modalId)) {
    const entry = openModals.get(modalId);
    if (entry.minimized) restoreModal(modalId);
    focusModal(modalId);
    return;
  }

  // Fetch plan file content from server
  let data;
  try {
    const resp = await fetch(API_BASE + `/api/project/${projectId}/agent/plan-file?session=${sessionId}`);
    if (!resp.ok) throw new Error('not found');
    data = await resp.json();
  } catch (e) {
    return;
  }

  const rawFilename = data.filename || 'Plan';
  // Use first markdown heading or session task as the display title
  const cached = agentStatusCache[sessionId];
  const headingMatch = data.content.match(/^#\s+(.+)/m);
  const filename = headingMatch ? headingMatch[1].trim() : planFileLabel(cached?.task || rawFilename);
  if (headingMatch) planFileTitle[sessionId] = headingMatch[1].trim();
  const lines = data.content.split('\n');

  // Render markdown lines with rich formatting
  let bodyHTML = '';
  let tableLines = [];
  function flushTable() {
    if (tableLines.length === 0) return;
    if (isPipeTable(tableLines)) {
      bodyHTML += `<div class="hl-table">${buildPipeTable(tableLines)}</div>`;
    } else {
      bodyHTML += `<div class="hl-table-pre">${tableLines.map(l => formatTableLine(esc(l))).join('\n')}</div>`;
    }
    tableLines = [];
  }
  for (const line of lines) {
    if (isTableLine(line)) {
      tableLines.push(line);
    } else if (tableLines.length > 0 && line.trim() === '') {
      tableLines.push(line);
    } else {
      flushTable();
      bodyHTML += `<div class="agent-line">${formatAgentText(line)}</div>`;
    }
  }
  flushTable();

  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content plan-viewer-content';
  content.innerHTML = `
    <div class="modal-window-controls">
      <button class="modal-minimize" onclick="minimizeModal('${esc(modalId)}')" title="Minimize">&#x2015;</button>
      <button class="modal-close" onclick="closeModalById('${esc(modalId)}')" title="Close">&#10005;</button>
    </div>
    <div class="plan-viewer-header modal-header">
      <h3 style="color:#c4b5fd;margin:0;font-size:15px">&#128196; ${esc(filename)}</h3>
      <div style="color:var(--text-faint);font-size:12px;margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(data.path)}</div>
    </div>
    <div class="plan-viewer-body">${bodyHTML}</div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);

  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);
}


function timeAgoShort(isoStr) {
  if (!isoStr) return 'never';
  try {
    const d = new Date(isoStr);
    const secs = Math.round((Date.now() - d) / 1000);
    if (secs < 60) return `${secs}s ago`;
    if (secs < 3600) return `${Math.floor(secs/60)}m ago`;
    if (secs < 86400) return `${Math.floor(secs/3600)}h ago`;
    return `${Math.floor(secs/86400)}d ago`;
  } catch(e) { return isoStr; }
}


// ── New Project Creation ────────────────────────────────────────────────────

function openNewProjectForm() {
  newProjDomain = 'general';
  const modalId = '__new_project';
  if (openModals.has(modalId)) {
    const entry = openModals.get(modalId);
    if (entry.minimized) restoreModal(modalId);
    focusModal(modalId);
    return;
  }

  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content';
  content.innerHTML = `
    <div class="modal-header" style="padding:16px 24px 12px 28px;border-radius:6px 6px 0 0">
      <div class="modal-window-controls">
        <button class="modal-minimize" onclick="minimizeModal('${modalId}')" title="Minimize">&#x2015;</button>
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
      <h2 style="margin:0;font-size:18px;font-weight:700;color:var(--text)">Create New Project</h2>
    </div>
    <div class="new-project-form">
      <div class="form-group">
        <label>Name</label>
        <input type="text" id="new-proj-name" placeholder="My Project" oninput="autoSlug()">
      </div>
      <div class="form-group">
        <label>ID (slug)</label>
        <input type="text" id="new-proj-id" placeholder="my_project">
        <div class="hint">Lowercase, underscores. Auto-generated from name.</div>
      </div>
      <div class="form-group">
        <label>Domain</label>
        <div class="new-proj-domain-wrap">
          <div class="new-proj-domain-trigger" onclick="toggleNewProjDomain(event)">
            ${(() => { const cfg = getDomainConfig(newProjDomain); return `<span class="modal-menu-sub-dot" style="background:${cfg.color}"></span> <span id="new-proj-domain-label">${esc(cfg.label||newProjDomain)}</span>`; })()}
            <span style="margin-left:auto;color:var(--text-faint);font-size:11px">&#x25BE;</span>
          </div>
          <div class="new-proj-domain-dd" id="new-proj-domain-dd">
            ${domainsList.map(d => {
              const cfg = getDomainConfig(d.id);
              return `<button class="new-proj-domain-item${newProjDomain===d.id?' active':''}" onclick="selectNewProjDomain(event,'${esc(d.id)}')">
              <span class="modal-menu-sub-dot" style="background:${cfg.color}"></span> ${esc(d.label||d.id)}</button>`;
            }).join('')}
            <div style="border-top:1px solid var(--border);padding:6px 8px">
              <div style="font-size:9px;color:var(--text-faint);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px">Color</div>
              <div style="display:flex;gap:4px;flex-wrap:wrap">
                ${COLOR_PRESETS.map(c =>
                  `<button style="width:18px;height:18px;border-radius:4px;border:1px solid var(--border);background:${c.bg};cursor:pointer;padding:0;display:flex;align-items:center;justify-content:center"
                    title="${c.label}"
                    onclick="setNewProjDomainColor(event,'${c.color}','${c.bg}')"><span style="width:8px;height:8px;border-radius:2px;background:${c.color}"></span></button>`
                ).join('')}
              </div>
            </div>
            <div style="border-top:1px solid var(--border);padding:4px">
              <input type="text" placeholder="New domain..." style="width:100%;background:var(--bg);border:1px solid var(--border);color:var(--text);font-size:11px;padding:4px 8px;border-radius:4px;outline:none;box-sizing:border-box"
                onclick="event.stopPropagation()"
                onkeydown="if(event.key==='Enter'){event.stopPropagation();addNewProjDomainEntry(this.value);this.value=''}">
            </div>
          </div>
        </div>
      </div>
      <div class="form-group">
        <label>Project Path (optional)</label>
        <div style="display:flex;gap:6px;align-items:center">
          <input type="text" id="new-proj-path" placeholder="Leave blank to auto-create a folder" style="flex:1"
                 onkeydown="if(event.key==='Enter'){event.preventDefault();browseTo(this.value)}">
          <button class="btn-add" style="padding:6px 14px;font-size:12px;white-space:nowrap"
                  onclick="toggleFolderBrowser()" id="browse-toggle-btn">Browse</button>
        </div>
        <div class="hint">If blank, a dedicated folder will be created under your auto-workspace base. Each project needs its own folder.</div>
        <div id="folder-browser" style="display:none;margin-top:8px">
          <div id="folder-breadcrumb" class="folder-breadcrumb"></div>
          <div id="folder-listing" class="folder-listing"></div>
          <div style="display:flex;gap:6px;align-items:center;margin-top:6px">
            <input type="text" id="new-proj-folder" placeholder="New folder name"
                   style="flex:1;background:var(--bg);border:1px solid var(--border);color:var(--text);font-family:'Inter',sans-serif;font-size:12px;padding:6px 10px;border-radius:5px;outline:none"
                   onkeydown="if(event.key==='Enter'){event.preventDefault();createFolderInBrowser()}">
            <button class="btn-add" style="padding:6px 14px;font-size:12px;white-space:nowrap"
                    onclick="createFolderInBrowser()">New Folder</button>
          </div>
          <button class="btn-add" style="margin-top:6px;padding:6px 14px;font-size:12px;width:100%"
                  onclick="selectBrowsedFolder()">&#10003; Use This Directory</button>
        </div>
        <div class="hint">Browse to select a directory, or type/paste a path directly.</div>
      </div>
      <button class="btn-add" style="padding:10px 24px;font-size:13px" onclick="createProject()">Create Project</button>
    </div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);

  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);
  folderBrowserOpen = false;
  folderBrowserPath = '';
  setTimeout(() => document.getElementById('new-proj-name')?.focus(), 100);
}

function autoSlug() {
  const name = document.getElementById('new-proj-name').value;
  document.getElementById('new-proj-id').value = name.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
}

// ── Folder Browser ────────────────────────────────────────────────────────
let folderBrowserPath = '';
let folderBrowserOpen = false;

function toggleFolderBrowser() {
  folderBrowserOpen = !folderBrowserOpen;
  const panel = document.getElementById('folder-browser');
  const btn = document.getElementById('browse-toggle-btn');
  if (folderBrowserOpen) {
    panel.style.display = 'block';
    btn.textContent = 'Hide';
    const startPath = (document.getElementById('new-proj-path').value || '').trim();
    loadDirectory(startPath || '');
  } else {
    panel.style.display = 'none';
    btn.textContent = 'Browse';
  }
}

async function loadDirectory(path) {
  const listing = document.getElementById('folder-listing');
  listing.innerHTML = '<div class="folder-loading">Loading...</div>';
  try {
    const res = await fetch(API_BASE + '/api/list-directory', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ path: path || '' })
    });
    const data = await res.json();
    if (!res.ok) {
      listing.innerHTML = `<div class="folder-error">${esc(data.error)}</div>`;
      return;
    }
    folderBrowserPath = data.path;
    renderBreadcrumb(data.path, data.parent);
    if (data.dirs.length === 0) {
      listing.innerHTML = '<div class="folder-empty">No subdirectories</div>';
    } else {
      listing.innerHTML = data.dirs.map(name =>
        `<div class="folder-item" data-dir="${esc(name)}" title="${esc(name)}">
          <span class="folder-item-icon">&#128193;</span>
          <span class="folder-item-name">${esc(name)}</span>
        </div>`
      ).join('');
    }
    listing.onclick = function(e) {
      const item = e.target.closest('.folder-item');
      if (!item) return;
      loadDirectory(folderBrowserPath + '/' + item.dataset.dir);
    };
  } catch(e) {
    listing.innerHTML = `<div class="folder-error">Failed to load: ${esc(e.message)}</div>`;
  }
}

function renderBreadcrumb(currentPath, parentPath) {
  const bc = document.getElementById('folder-breadcrumb');
  const isWindows = currentPath.includes('\\');
  const parts = currentPath.split(/[/\\]/).filter(Boolean);
  let html = '';
  let accumulated = isWindows ? '' : '/';
  parts.forEach((part, i) => {
    if (i > 0) html += '<span class="folder-crumb-sep">&#9656;</span>';
    accumulated += (i > 0 ? '/' : '') + part;
    const segPath = accumulated;
    if (i === parts.length - 1) {
      html += `<span class="folder-crumb-current">${esc(part)}</span>`;
    } else {
      html += `<span class="folder-crumb" onclick="loadDirectory('${esc(segPath)}')">${esc(part)}</span>`;
    }
  });
  if (parentPath) {
    html = `<span class="folder-crumb" onclick="loadDirectory('${esc(parentPath)}')" title="Go up"
             style="font-size:14px;margin-right:4px">&#11176;</span>` + html;
  }
  bc.innerHTML = html;
}

function browseTo(path) {
  if (!folderBrowserOpen) toggleFolderBrowser();
  else loadDirectory(path);
}

function selectBrowsedFolder() {
  document.getElementById('new-proj-path').value = folderBrowserPath;
  toggleFolderBrowser();
}

async function createFolderInBrowser() {
  const folderInput = document.getElementById('new-proj-folder');
  const folderName = (folderInput.value || '').trim();
  if (!folderName) { alert('Enter a folder name'); return; }
  try {
    const res = await fetch(API_BASE + '/api/create-folder', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ name: folderName, parent: folderBrowserPath })
    });
    const data = await res.json();
    if (!res.ok) { alert(data.error || 'Failed to create folder'); return; }
    folderInput.value = '';
    loadDirectory(data.path);
  } catch(e) { alert('Failed to create folder: ' + e.message); }
}

// ── New project domain picker ────────────────────────────────────────────────

function toggleNewProjDomain(e) {
  e.stopPropagation();
  const dd = document.getElementById('new-proj-domain-dd');
  if (!dd) return;
  const isOpen = dd.classList.contains('open');
  document.querySelectorAll('.new-proj-domain-dd.open').forEach(d => d.classList.remove('open'));
  if (!isOpen) {
    dd.classList.add('open');
    const close = (ev) => {
      if (!dd.contains(ev.target)) { dd.classList.remove('open'); document.removeEventListener('click', close); }
    };
    setTimeout(() => document.addEventListener('click', close), 0);
  }
}

function selectNewProjDomain(e, domainId) {
  e.stopPropagation();
  newProjDomain = domainId;
  document.querySelectorAll('.new-proj-domain-dd.open').forEach(d => d.classList.remove('open'));
  refreshNewProjDomainTrigger();
}

function refreshNewProjDomainTrigger() {
  const wrap = document.querySelector('.new-proj-domain-wrap');
  if (!wrap) return;
  const cfg = getDomainConfig(newProjDomain);
  const trigger = wrap.querySelector('.new-proj-domain-trigger');
  if (trigger) {
    trigger.innerHTML = `<span class="modal-menu-sub-dot" style="background:${cfg.color}"></span> <span id="new-proj-domain-label">${esc(cfg.label||newProjDomain)}</span><span style="margin-left:auto;color:var(--text-faint);font-size:11px">&#x25BE;</span>`;
  }
  // Rebuild dropdown items
  const dd = document.getElementById('new-proj-domain-dd');
  if (!dd) return;
  const itemsHTML = domainsList.map(d => {
    const c = getDomainConfig(d.id);
    return `<button class="new-proj-domain-item${newProjDomain===d.id?' active':''}" onclick="selectNewProjDomain(event,'${esc(d.id)}')">
    <span class="modal-menu-sub-dot" style="background:${c.color}"></span> ${esc(d.label||d.id)}</button>`;
  }).join('');
  const colorHTML = `<div style="border-top:1px solid var(--border);padding:6px 8px">
    <div style="font-size:9px;color:var(--text-faint);text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px">Color</div>
    <div style="display:flex;gap:4px;flex-wrap:wrap">
      ${COLOR_PRESETS.map(c =>
        `<button style="width:18px;height:18px;border-radius:4px;border:1px solid var(--border);background:${c.bg};cursor:pointer;padding:0;display:flex;align-items:center;justify-content:center"
          title="${c.label}"
          onclick="setNewProjDomainColor(event,'${c.color}','${c.bg}')"><span style="width:8px;height:8px;border-radius:2px;background:${c.color}"></span></button>`
      ).join('')}
    </div>
  </div>`;
  const inputHTML = `<div style="border-top:1px solid var(--border);padding:4px">
    <input type="text" placeholder="New domain..." style="width:100%;background:var(--bg);border:1px solid var(--border);color:var(--text);font-size:11px;padding:4px 8px;border-radius:4px;outline:none;box-sizing:border-box"
      onclick="event.stopPropagation()"
      onkeydown="if(event.key==='Enter'){event.stopPropagation();addNewProjDomainEntry(this.value);this.value=''}">
  </div>`;
  dd.innerHTML = itemsHTML + colorHTML + inputHTML;
}

async function addNewProjDomainEntry(value) {
  const name = value.trim();
  if (!name) return;
  const id = name.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
  if (!id) return;
  try {
    const res = await fetch(API_BASE + '/api/settings/domains/add', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ id, label: name })
    });
    const data = await res.json();
    if (!data.ok && data.error !== 'domain already exists') return;
    await fetchDomains();
    renderDomainFilters();
    newProjDomain = id;
    refreshNewProjDomainTrigger();
  } catch(err) {}
}

async function setNewProjDomainColor(e, color, bg) {
  e.stopPropagation();
  const domainId = newProjDomain;
  const domain = domainsList.find(d => d.id === domainId);
  if (!domain) return;
  domain.color = color;
  domain.bg = bg;
  try {
    await fetch(API_BASE + `/api/settings/domains/${domainId}`, {
      method: 'PATCH',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ color, bg })
    });
    renderDomainFilters();
    refreshNewProjDomainTrigger();
    await refreshSilent();
  } catch(err) {}
}

async function createProject() {
  const id = document.getElementById('new-proj-id').value.trim();
  const name = document.getElementById('new-proj-name').value.trim();
  const domain = newProjDomain;
  const path = document.getElementById('new-proj-path').value.trim();
  if (!id || !name) { alert('Name and ID are required'); return; }
  if (allProjects.find(p => p.id === id)) { alert('A project with this ID already exists'); return; }
  try {
    const res = await fetch(API_BASE + `/api/project/${encodeURIComponent(id)}`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ name, domain, status: 'active', project_path: path })
    });
    const data = await res.json();
    if (!res.ok || !data.ok) { alert(data.error || 'Failed to create project'); return; }
    newProjDomain = 'general';
    closeModalById('__new_project');
    await refreshSilent();
    openProjectModal(id);
  } catch(e) { alert('Failed to create project: ' + e.message); }
}


// ── interop: window re-exposure for inline/generated/cross-module callers ──
window.saveProjectName = saveProjectName;
window.saveDomainFromMenu = saveDomainFromMenu;
window.addDomainFromMenu = addDomainFromMenu;
window.setDomainColorFromMenu = setDomainColorFromMenu;
window.saveProjectPath = saveProjectPath;
window.openFolderPicker = openFolderPicker;
window.importFromProject = importFromProject;
window.openSharedRulesEditor = openSharedRulesEditor;
window.openPlanViewer = openPlanViewer;
window.planFileLabel = planFileLabel;
window.openPlanFileViewer = openPlanFileViewer;
window.timeAgoShort = timeAgoShort;
window.openNewProjectForm = openNewProjectForm;
window.addNewProjDomainEntry = addNewProjDomainEntry;
window.autoSlug = autoSlug;
window.browseTo = browseTo;
window.closeFolderPicker = closeFolderPicker;
window.createFolderInBrowser = createFolderInBrowser;
window.createProject = createProject;
window.fpCreateFolder = fpCreateFolder;
window.fpGoUp = fpGoUp;
window.fpLoad = fpLoad;
window.fpSelectCurrent = fpSelectCurrent;
window.loadDirectory = loadDirectory;
window.saveSharedRulesGlobal = saveSharedRulesGlobal;
window.selectBrowsedFolder = selectBrowsedFolder;
window.selectNewProjDomain = selectNewProjDomain;
window.setNewProjDomainColor = setNewProjDomainColor;
window.toggleFolderBrowser = toggleFolderBrowser;
window.toggleNewProjDomain = toggleNewProjDomain;
