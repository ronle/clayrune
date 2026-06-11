// ── MCP servers (global + per-project Model Context Protocol manager) ───────
//
// MCP servers live in ~/.claude.json (global mcpServers key) and
// <project_path>/.mcp.json (project-committed). Three transports: stdio,
// http, sse. MC manages the files; Claude Code reads them natively at session
// start.

let _allMCPCache = { items: [], loaded: false, loading: false };
let _allMCPFilter = { scope: 'all', project: 'all', search: '' };

function openAllMCPForProject(projectId) {
  _allMCPFilter.project = projectId || 'all';
  _allMCPFilter.scope = 'all';
  openAllMCP();
}

async function openAllMCP() {
  const modalId = '__all_mcp';
  if (openModals.has(modalId)) {
    const entry = openModals.get(modalId);
    if (entry.minimized) restoreModal(modalId);
    focusModal(modalId);
    renderAllMCP();
    loadAllMCP();
    return;
  }

  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content';
  _clampModalSize(content, 1000);
  content.innerHTML = `
    <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:16px 24px 12px 28px">
      <span style="font-size:16px;font-weight:700;color:var(--text)">&#x1F50C; MCP servers</span>
      <div class="modal-window-controls" style="position:static;display:flex;gap:4px">
        <button class="modal-minimize" onclick="minimizeModal('${modalId}')" title="Minimize">&#x2015;</button>
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
    </div>
    <div style="padding:4px 24px 20px 28px">
      <div style="font-size:11px;color:var(--text-faint);margin-bottom:10px;line-height:1.5">
        Model Context Protocol servers extend Claude Code with extra tools (filesystems, databases, hosted APIs).
        Global servers live in <code>~/.claude.json</code>; project servers live in <code>.mcp.json</code> at the project root.
        Claude Code picks them up on next session start &mdash; no restart needed here.
      </div>
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:12px;flex-wrap:wrap">
        <input type="text" id="am-search" placeholder="Search name / command / URL..." value="${esc(_allMCPFilter.search)}"
          style="flex:1;min-width:180px;padding:6px 10px;font-size:12px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text)"
          oninput="_allMCPFilter.search=this.value;renderAllMCP()">
        <select id="am-scope" style="padding:6px 8px;font-size:12px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text)"
          onchange="_allMCPFilter.scope=this.value;renderAllMCP()">
          <option value="all"${_allMCPFilter.scope==='all'?' selected':''}>All scopes</option>
          <option value="global"${_allMCPFilter.scope==='global'?' selected':''}>Global only</option>
          <option value="project"${_allMCPFilter.scope==='project'?' selected':''}>Project only</option>
        </select>
        <select id="am-project" style="padding:6px 8px;font-size:12px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text);max-width:200px"
          onchange="_allMCPFilter.project=this.value;loadAllMCP()">
          <option value="all">All projects</option>
        </select>
        <span id="am-count" style="font-size:11px;color:var(--text-faint)"></span>
        <div style="margin-left:auto;display:flex;gap:6px">
          <button class="btn-add" onclick="openMCPEditor('global', '', null, true)">+ New MCP server</button>
        </div>
      </div>
      <div id="am-list" style="max-height:65vh;overflow-y:auto"></div>
    </div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);

  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);

  renderAllMCP();
  loadAllMCP();
}

async function loadAllMCP() {
  if (_allMCPCache.loading) return;
  _allMCPCache.loading = true;
  try {
    const pid = _allMCPFilter.project !== 'all' ? _allMCPFilter.project : '';
    const params = new URLSearchParams();
    if (pid) params.set('project_id', pid);
    const url = API_BASE + '/api/mcp' + (params.toString() ? '?' + params.toString() : '');
    const res = await fetch(url);
    const items = await res.json();

    // For "all projects" view, also fetch each project's local servers so they appear.
    let extra = [];
    if ((_allMCPFilter.project === 'all') && (_allMCPFilter.scope === 'all' || _allMCPFilter.scope === 'project')) {
      const realProjects = (allProjects || []).filter(p => !isIncognitoProject(p) && p.project_path);
      await Promise.all(realProjects.map(async p => {
        try {
          const r = await fetch(API_BASE + '/api/mcp?project_id=' + encodeURIComponent(p.id));
          const arr = await r.json();
          for (const s of arr) if (s.scope === 'project') extra.push(s);
        } catch(e) {}
      }));
    }

    _allMCPCache = { items: items.concat(extra), loaded: true, loading: false };
    renderAllMCP();
  } catch(e) {
    _allMCPCache = { items: [], loaded: true, loading: false };
    renderAllMCP();
  }
}

function renderAllMCP() {
  const container = document.getElementById('am-list');
  const countEl = document.getElementById('am-count');
  const projectSel = document.getElementById('am-project');
  if (!container) return;

  if (projectSel) {
    const realProjects = (allProjects || []).filter(p => !isIncognitoProject(p));
    const cur = _allMCPFilter.project;
    const opts = ['<option value="all">All projects</option>',
      ...realProjects.map(p =>
        `<option value="${esc(p.id)}"${cur===p.id?' selected':''}>${esc(p.name||p.id)}</option>`
      )];
    if (projectSel.innerHTML !== opts.join('')) {
      projectSel.innerHTML = opts.join('');
      projectSel.value = cur;
    }
  }

  if (!_allMCPCache.loaded) {
    container.innerHTML = '<div style="padding:40px 12px;text-align:center;color:var(--text-faint);font-size:12px">Loading MCP servers...</div>';
    if (countEl) countEl.textContent = '';
    return;
  }

  const f = _allMCPFilter;
  const q = (f.search || '').trim().toLowerCase();
  let rows = _allMCPCache.items.filter(s => {
    if (f.scope !== 'all' && s.scope !== f.scope) return false;
    if (f.project !== 'all') {
      if (s.scope === 'project' && s.project_id !== f.project) return false;
    }
    if (q) {
      const hay = ((s.name || '') + ' ' + (s.preview || '') + ' ' + (s.transport || '')).toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });

  // De-dup
  const seen = new Set();
  rows = rows.filter(s => {
    const key = s.scope + ':' + s.name + ':' + (s.project_id || '');
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  // Sort: scope (project first, then global), name asc
  const scopeOrder = { project: 0, global: 1 };
  rows.sort((a, b) => {
    const oa = scopeOrder[a.scope] ?? 9;
    const ob = scopeOrder[b.scope] ?? 9;
    if (oa !== ob) return oa - ob;
    return (a.name || '').localeCompare(b.name || '');
  });

  if (countEl) countEl.textContent = `${rows.length} server${rows.length===1?'':'s'}`;

  // Per-project loadout banner — only meaningful when one project is selected.
  // active/loadout_custom are annotated by /api/mcp?project_id=… (see server.py).
  let banner = '';
  if (f.project !== 'all' && _allMCPCache.loaded && _allMCPCache.items.length) {
    const its = _allMCPCache.items;
    const custom = its.some(s => s.loadout_custom);
    const names = [...new Set(its.map(s => s.name))];
    const activeNames = [...new Set(its.filter(s => s.active).map(s => s.name))];
    if (custom) {
      banner = `<div style="padding:8px 12px;border-bottom:1px solid var(--border);background:var(--surface2);font-size:11px;color:var(--text-mute);display:flex;align-items:center;gap:8px">
        <span style="color:var(--accent)">●</span> Custom loadout — <b>${activeNames.length}/${names.length}</b> servers load for this project's agents
        <button class="btn-tiny" onclick="_mcpResetLoadout()" style="margin-left:auto">Reset to all</button></div>`;
    } else {
      banner = `<div style="padding:8px 12px;border-bottom:1px solid var(--border);background:var(--surface2);font-size:11px;color:var(--text-faint)">
        All <b>${names.length}</b> servers load for this project (default) — toggle any <b>off</b> to trim what its agents load.</div>`;
    }
  }

  if (!rows.length) {
    container.innerHTML = banner + `<div style="padding:40px 12px;text-align:center;color:var(--text-faint);font-size:12px">No MCP servers match the current filters.<br><span style="font-size:11px">Click "+ New MCP server" to add one.</span></div>`;
    return;
  }

  container.innerHTML = banner + rows.map(s => _renderMCPRow(s)).join('');
}

function _renderMCPRow(s) {
  const scopeBadge = (() => {
    if (s.scope === 'global') return '<span style="font-size:10px;background:var(--surface2);color:var(--text-faint);padding:1px 6px;border-radius:3px">global</span>';
    if (s.scope === 'project') {
      const pname = (allProjects || []).find(p => p.id === s.project_id);
      const label = pname ? (pname.name || pname.id) : (s.project_id || 'project');
      return `<span style="font-size:10px;background:var(--accent-dim);color:var(--accent);padding:1px 6px;border-radius:3px" title="Project-scoped">project: ${esc(label)}</span>`;
    }
    return '';
  })();
  const transportBadge = s.transport
    ? `<span style="font-size:10px;background:var(--surface2);color:var(--text-faint);padding:1px 6px;border-radius:3px;font-family:var(--mono)">${esc(s.transport)}</span>`
    : '';
  const shadowBadge = s.shadowed_by_project
    ? '<span style="font-size:10px;background:transparent;color:var(--amber);padding:1px 6px;border:1px solid var(--amber);border-radius:3px;margin-left:4px" title="A project entry of the same name shadows this global">shadowed</span>'
    : '';
  const editArgs = `'${esc(s.scope)}', '${esc(s.name)}', ${s.project_id ? `'${esc(s.project_id)}'` : 'null'}, false`;
  const delArgs = `'${esc(s.scope)}', '${esc(s.name)}', ${s.project_id ? `'${esc(s.project_id)}'` : 'null'}`;
  const mtime = s.mtime_iso ? new Date(s.mtime_iso).toLocaleString() : '';
  // Per-project "active" toggle — only present when a single project is selected
  // (server annotates s.active). always_on servers (engram/memory) are locked on.
  const activeToggle = (s.active === undefined) ? '' : (
    s.always_on
      ? `<span class="btn-tiny" style="opacity:.6;cursor:default;border-color:var(--accent);color:var(--accent)" title="Always loaded — provides cross-session memory; can't be disabled">✓ memory</span>`
      : `<button class="btn-tiny" onclick="_mcpToggleActive('${esc(s.name)}', ${s.active ? 'false' : 'true'})" style="${s.active ? 'color:var(--accent);border-color:var(--accent)' : 'opacity:.65'}" title="${s.active ? 'Loaded for this project — click to disable' : 'Not loaded for this project — click to enable'}">${s.active ? '✓ active' : '○ off'}</button>`
  );

  return `<div class="mcp-row" style="padding:10px 12px;border-bottom:1px solid var(--border);display:flex;flex-direction:column;gap:4px">
    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
      <span style="font-weight:600;color:var(--text);font-size:13px">${esc(s.name)}</span>
      ${scopeBadge}
      ${transportBadge}
      ${shadowBadge}
      <div style="margin-left:auto;display:flex;gap:6px;align-items:center">
        ${activeToggle}
        <button class="btn-tiny" onclick="openMCPEditor(${editArgs})">Edit</button>
        <button class="btn-tiny danger" onclick="deleteMCPAction(${delArgs})">Delete</button>
      </div>
    </div>
    <div style="font-size:12px;color:var(--text-mute);line-height:1.4;font-family:var(--mono);word-break:break-all">${esc(s.preview || '(no command/url)')}</div>
    <div style="font-size:10px;color:var(--text-faint);font-family:var(--mono)">${esc(s.path || '')} &middot; ${esc(mtime)}</div>
  </div>`;
}

// Per-project MCP loadout writes → /api/project/<id>/mcp-enabled (server.py).
// Toggling materializes enabled_mcp_servers from the project's currently-active
// set; Reset clears the opt-in (back to the full fleet). engram is force-kept
// server-side regardless. After a write we refetch so active flags re-sync.
async function _mcpToggleActive(name, newActive) {
  const pid = _allMCPFilter.project;
  if (!pid || pid === 'all') return;
  const activeSet = new Set(_allMCPCache.items.filter(s => s.active).map(s => s.name));
  if (newActive) activeSet.add(name); else activeSet.delete(name);
  try {
    await fetch(API_BASE + '/api/project/' + encodeURIComponent(pid) + '/mcp-enabled', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: [...activeSet] })
    });
  } catch (e) {}
  _allMCPCache = { items: [], loaded: false, loading: false };
  loadAllMCP();
}

async function _mcpResetLoadout() {
  const pid = _allMCPFilter.project;
  if (!pid || pid === 'all') return;
  try {
    await fetch(API_BASE + '/api/project/' + encodeURIComponent(pid) + '/mcp-enabled', {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: null })
    });
  } catch (e) {}
  _allMCPCache = { items: [], loaded: false, loading: false };
  loadAllMCP();
}

async function openMCPEditor(scope, name, projectId, isNew) {
  const modalId = isNew ? '__mcp_new' : `__mcp_${scope}_${name}`;
  if (openModals.has(modalId)) { focusModal(modalId); return; }

  // Pre-fetch existing record if editing
  let existing = null;
  if (!isNew) {
    try {
      const params = new URLSearchParams();
      if (projectId) params.set('project_id', projectId);
      const res = await fetch(API_BASE + `/api/mcp/${encodeURIComponent(scope)}/${encodeURIComponent(name)}` + (params.toString() ? '?' + params.toString() : ''));
      if (res.ok) existing = await res.json();
    } catch(e) {}
  }

  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content';
  _clampModalSize(content, 760);

  const title = isNew ? 'New MCP server' : `Edit: ${name}`;
  const initTransport = (existing && existing.transport) || 'stdio';
  const initCfg = (existing && existing.config) || {};

  // Project picker — only for new servers with project scope
  const realProjects = (allProjects || []).filter(p => !isIncognitoProject(p) && p.project_path);
  const initialProjectId = projectId || (_allMCPFilter.project !== 'all' ? _allMCPFilter.project : (realProjects[0] && realProjects[0].id) || '');
  const projOptions = realProjects.map(p =>
    `<option value="${esc(p.id)}"${p.id===initialProjectId?' selected':''}>${esc(p.name||p.id)}</option>`
  ).join('');

  // Mode toggle is only meaningful when adding new servers; editing always
  // uses the manual form so we don't lose the user's existing config.
  const modeToggle = isNew ? `
    <div style="display:flex;gap:0;margin-bottom:8px;border:1px solid var(--border);border-radius:6px;overflow:hidden;width:fit-content">
      <button id="me-mode-manual" class="me-mode-btn active" onclick="_mcpEditorSetMode('${modalId}','manual')"
        style="padding:6px 14px;font-size:12px;background:var(--accent);color:var(--bg);border:none;cursor:pointer;font-weight:600">
        Manual
      </button>
      <button id="me-mode-url" class="me-mode-btn" onclick="_mcpEditorSetMode('${modalId}','url')"
        style="padding:6px 14px;font-size:12px;background:var(--surface2);color:var(--text);border:none;border-left:1px solid var(--border);cursor:pointer">
        From URL
      </button>
    </div>` : '';

  content.innerHTML = `
    <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:16px 24px 12px 28px">
      <span style="font-size:16px;font-weight:700;color:var(--text)">&#x1F50C; ${esc(title)}</span>
      <div class="modal-window-controls" style="position:static;display:flex;gap:4px">
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
    </div>
    <div style="padding:4px 24px 20px 28px;display:flex;flex-direction:column;gap:12px">
      ${modeToggle}
      <div id="me-manual-mode" style="display:flex;flex-direction:column;gap:12px">
        <div>
          <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">Name</label>
          <input type="text" id="me-name" value="${esc(name || '')}" ${isNew?'':'readonly'}
            placeholder="filesystem"
            style="width:100%;padding:6px 10px;font-size:13px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono);${isNew?'':'opacity:0.6'}">
          <div style="font-size:10px;color:var(--text-faint);margin-top:3px">Letters, digits, dots, dashes, underscores. Must be unique within its scope.</div>
        </div>
        <div>
          <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">Scope</label>
          <div style="display:flex;gap:12px;align-items:center;flex-wrap:wrap">
            <label style="display:flex;align-items:center;gap:6px;font-size:12px;cursor:${isNew?'pointer':'not-allowed'}">
              <input type="radio" name="me-scope" value="global" ${scope==='global'?'checked':''} ${isNew?'':'disabled'}>
              Global (all projects)
            </label>
            <label style="display:flex;align-items:center;gap:6px;font-size:12px;cursor:${isNew?'pointer':'not-allowed'}">
              <input type="radio" name="me-scope" value="project" ${scope==='project'?'checked':''} ${isNew?'':'disabled'} ${realProjects.length?'':'disabled'}>
              Project (committed to .mcp.json)
            </label>
            <select id="me-project" style="padding:4px 8px;font-size:12px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text);max-width:220px;${scope==='project'?'':'display:none'}">
              ${projOptions || '<option value="">No projects with a path</option>'}
            </select>
          </div>
        </div>
        <div>
          <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">Transport</label>
          <select id="me-transport" onchange="_mcpEditorRenderTransport()"
            style="padding:6px 10px;font-size:13px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text);min-width:240px">
            <option value="stdio"${initTransport==='stdio'?' selected':''}>stdio (local subprocess)</option>
            <option value="http"${initTransport==='http'?' selected':''}>http (streamable HTTP)</option>
            <option value="sse"${initTransport==='sse'?' selected':''}>sse (legacy HTTP+SSE)</option>
          </select>
        </div>
        <div id="me-fields"></div>
        <div id="me-status" style="font-size:11px;color:var(--text-faint);min-height:14px"></div>
        <div style="display:flex;gap:8px;justify-content:flex-end">
          <button class="btn-secondary" onclick="closeModalById('${modalId}')">Cancel</button>
          <button class="btn-add" id="me-save" onclick="saveMCPServer('${modalId}', ${isNew})">${isNew?'Create':'Save changes'}</button>
        </div>
      </div>
      <div id="me-url-mode" style="display:none;flex-direction:column;gap:12px"></div>
    </div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);

  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: projectId || null, element: win, minimized: false, zIndex: z, _mcpEditing: { scope, name, isNew, initCfg, initTransport } });
  centerModalElement(win);
  focusModal(modalId);

  // Wire scope radio → project picker visibility
  win.querySelectorAll('input[name="me-scope"]').forEach(r => {
    r.addEventListener('change', () => {
      const proj = win.querySelector('#me-project');
      if (proj) proj.style.display = r.checked && r.value === 'project' ? '' : 'none';
    });
  });

  _mcpEditorRenderTransport();
}

function _mcpEditorRenderTransport() {
  const sel = document.getElementById('me-transport');
  const target = document.getElementById('me-fields');
  if (!sel || !target) return;
  const transport = sel.value;

  // Resolve current editor state (so we can pre-fill on first render)
  let initCfg = {};
  for (const [id, entry] of openModals.entries()) {
    if (entry && entry._mcpEditing && (id === '__mcp_new' || id.startsWith('__mcp_'))) {
      if (entry.element && entry.element.contains(sel)) {
        initCfg = entry._mcpEditing.initCfg || {};
        // Only pre-fill once; subsequent transport changes start blank for the new transport's fields.
        if (entry._mcpEditing._consumedInitCfg) initCfg = {};
        else entry._mcpEditing._consumedInitCfg = true;
        break;
      }
    }
  }

  if (transport === 'stdio') {
    const args = Array.isArray(initCfg.args) ? initCfg.args.join(' ') : '';
    const envPairs = Object.entries(initCfg.env || {}).map(([k,v]) => `${k}=${v}`).join('\n');
    target.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:10px">
        <div>
          <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">Command</label>
          <input type="text" id="me-command" value="${esc(initCfg.command || '')}" placeholder="npx"
            style="width:100%;padding:6px 10px;font-size:13px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono)">
        </div>
        <div>
          <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">Arguments (space-separated)</label>
          <input type="text" id="me-args" value="${esc(args)}" placeholder="-y @modelcontextprotocol/server-filesystem /path"
            style="width:100%;padding:6px 10px;font-size:13px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono)">
          <div style="font-size:10px;color:var(--text-faint);margin-top:3px">For arguments that need spaces inside one token, edit <code>.mcp.json</code> directly.</div>
        </div>
        <div>
          <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">Environment variables (one per line, KEY=value)</label>
          <textarea id="me-env" placeholder="API_KEY=sk-..."
            style="width:100%;min-height:60px;padding:6px 10px;font-size:12px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono);resize:vertical">${esc(envPairs)}</textarea>
        </div>
      </div>`;
  } else {
    const headerPairs = Object.entries(initCfg.headers || {}).map(([k,v]) => `${k}: ${v}`).join('\n');
    target.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:10px">
        <div>
          <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">URL</label>
          <input type="text" id="me-url" value="${esc(initCfg.url || '')}" placeholder="https://mcp.example.com/v1"
            style="width:100%;padding:6px 10px;font-size:13px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono)">
        </div>
        <div>
          <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">Headers (one per line, Name: value)</label>
          <textarea id="me-headers" placeholder="Authorization: Bearer ..."
            style="width:100%;min-height:60px;padding:6px 10px;font-size:12px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono);resize:vertical">${esc(headerPairs)}</textarea>
        </div>
      </div>`;
  }
}

function _mcpEditorStatus(modalId, msg, color) {
  const entry = openModals.get(modalId);
  if (!entry || !entry.element) return;
  const el = entry.element.querySelector('#me-status');
  if (el) {
    el.textContent = msg || '';
    el.style.color = color || 'var(--text-faint)';
  }
}

function _parseKVLines(text, sep) {
  const out = {};
  for (const line of (text || '').split(/\r?\n/)) {
    const t = line.trim();
    if (!t) continue;
    const idx = t.indexOf(sep);
    if (idx < 0) continue;
    const k = t.slice(0, idx).trim();
    const v = t.slice(idx + sep.length).trim();
    if (k) out[k] = v;
  }
  return out;
}

// ── MCP "From URL" mode — state machine inside openMCPEditor ────────────────
// Lives alongside the manual editor. Toggling mode swaps which child div is
// visible; the URL flow has its own sub-states (input → preview → installing
// → done) handled here.

function _mcpEditorSetMode(modalId, mode) {
  const entry = openModals.get(modalId);
  if (!entry || !entry.element) return;
  const root = entry.element;
  const manual = root.querySelector('#me-manual-mode');
  const url = root.querySelector('#me-url-mode');
  const btnManual = root.querySelector('#me-mode-manual');
  const btnUrl = root.querySelector('#me-mode-url');
  if (!manual || !url) return;
  manual.style.display = mode === 'manual' ? '' : 'none';
  url.style.display = mode === 'url' ? 'flex' : 'none';
  if (btnManual && btnUrl) {
    btnManual.classList.toggle('active', mode === 'manual');
    btnManual.style.background = mode === 'manual' ? 'var(--accent)' : 'var(--surface2)';
    btnManual.style.color = mode === 'manual' ? 'var(--bg)' : 'var(--text)';
    btnManual.style.fontWeight = mode === 'manual' ? '600' : '400';
    btnUrl.classList.toggle('active', mode === 'url');
    btnUrl.style.background = mode === 'url' ? 'var(--accent)' : 'var(--surface2)';
    btnUrl.style.color = mode === 'url' ? 'var(--bg)' : 'var(--text)';
    btnUrl.style.fontWeight = mode === 'url' ? '600' : '400';
  }
  entry._mcpUrlState = entry._mcpUrlState || { stage: 'input' };
  if (mode === 'url') _mcpUrlRender(modalId);
}

function _mcpUrlRender(modalId) {
  const entry = openModals.get(modalId);
  if (!entry || !entry.element) return;
  const host = entry.element.querySelector('#me-url-mode');
  if (!host) return;
  const st = entry._mcpUrlState || { stage: 'input' };
  if (st.stage === 'input')      host.innerHTML = _mcpUrlRenderInput(modalId, st);
  else if (st.stage === 'preview')   host.innerHTML = _mcpUrlRenderPreview(modalId, st);
  else if (st.stage === 'installing')host.innerHTML = _mcpUrlRenderInstalling(modalId, st);
  else if (st.stage === 'done')      host.innerHTML = _mcpUrlRenderDone(modalId, st);
}

function _mcpUrlRenderInput(modalId, st) {
  return `
    <div>
      <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">Paste a GitHub URL, npm package name, or raw JSON config URL</label>
      <input type="text" id="me-url-input" value="${esc(st.url || '')}"
        placeholder="https://github.com/owner/repo  or  @scope/mcp-server  or  https://example.com/mcp.json"
        style="width:100%;padding:8px 10px;font-size:13px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono)">
      <div style="font-size:10px;color:var(--text-faint);margin-top:4px;line-height:1.5">
        For GitHub repos: MC will clone, audit dependencies, scan the source with Claude, and show you what's about to run before any install commands fire.
        ${st.error ? `<div style="color:#ef4444;margin-top:6px">${esc(st.error)}</div>` : ''}
      </div>
    </div>
    <div style="display:flex;gap:8px;justify-content:flex-end">
      <button class="btn-secondary" onclick="closeModalById('${modalId}')">Cancel</button>
      <button class="btn-add" id="me-url-preview-btn" onclick="_mcpUrlPreview('${modalId}')">Preview &rarr;</button>
    </div>`;
}

async function _mcpUrlPreview(modalId) {
  const entry = openModals.get(modalId);
  if (!entry || !entry.element) return;
  const input = entry.element.querySelector('#me-url-input');
  const url = (input && input.value || '').trim();
  if (!url) {
    entry._mcpUrlState = { stage: 'input', url, error: 'URL required' };
    _mcpUrlRender(modalId);
    return;
  }
  const btn = entry.element.querySelector('#me-url-preview-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Cloning & scanning... (15-30s)'; }
  try {
    const res = await fetch(API_BASE + '/api/mcp/url/preview', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url }),
    });
    const data = await res.json();
    if (!res.ok) {
      entry._mcpUrlState = { stage: 'input', url, error: data.error || `preview failed (${res.status})` };
    } else {
      // Pick the first server config from the response — most MCP repos
      // expose a single entry. If there are multiple we still install the
      // first; user can manually add others later.
      const serverNames = Object.keys(data.servers || {});
      const firstName = serverNames[0];
      const firstCfg = firstName ? data.servers[firstName] : null;
      entry._mcpUrlState = {
        stage: 'preview', url, preview: data,
        name: data.name_hint || firstName || '',
        scope: 'global', projectId: null,
        config: firstCfg,
        secretValues: {},  // populated as user types
      };
    }
  } catch (e) {
    entry._mcpUrlState = { stage: 'input', url, error: String(e) };
  } finally {
    _mcpUrlRender(modalId);
  }
}

function _mcpUrlRenderPreview(modalId, st) {
  const p = st.preview || {};
  const gh = p.github || {};
  const scan = p.scan || {};
  const audit = p.audit || {};
  const secrets = p.secrets || [];
  const cmds = p.install_commands || [];
  const tierLabel = p.source_tier === 1 ? 'config file in repo'
                  : p.source_tier === 2 ? 'README JSON block'
                  : p.source_tier === 3 ? 'Claude-extracted from README'
                  : 'unknown source';

  // GitHub trust row
  let ghHtml = '';
  if (gh.available) {
    const ageDays = gh.age_days;
    const lastCommit = gh.last_commit_days;
    const archivedFlag = gh.archived ? `<span style="color:#ef4444;font-weight:600">ARCHIVED</span>` : '';
    const freshFlag = ageDays !== null && ageDays < 30 ? `<span style="color:#eab308">new (${ageDays}d)</span>` : '';
    const staleFlag = lastCommit !== null && lastCommit > 365 ? `<span style="color:#eab308">stale (last commit ${lastCommit}d ago)</span>` : '';
    ghHtml = `
      <div style="display:flex;gap:14px;flex-wrap:wrap;align-items:center;font-size:11px;padding:8px 12px;background:var(--surface2);border-radius:4px">
        <span><b>${esc(gh.full_name || '')}</b></span>
        <span>&#x2B50; ${gh.stars}</span>
        <span>License: ${esc(gh.license || 'unknown')}</span>
        <span>Default: <code>${esc(gh.default_branch || '')}</code></span>
        ${archivedFlag} ${freshFlag} ${staleFlag}
      </div>`;
  }

  // Security scan table
  let scanHtml = '';
  if (scan.available) {
    const row = (label, items, isFlag) => {
      const txt = (items || []).length ? esc((items || []).join(', ')) : '<span style="color:var(--text-faint)">none</span>';
      return `<tr><td style="padding:4px 8px;color:var(--text-faint);width:90px;vertical-align:top">${label}</td><td style="padding:4px 8px;${isFlag && items && items.length ? 'color:#ef4444;font-weight:600':''}">${txt}</td></tr>`;
    };
    scanHtml = `
      <div>
        <div style="font-size:11px;color:var(--text-faint);margin-bottom:4px">Source scan <span style="color:var(--text-faint)">(Claude reviewed the code)</span></div>
        <div style="font-size:12px;padding:8px 4px;background:var(--surface2);border-radius:4px">
          <div style="padding:0 8px 6px;font-style:italic;color:var(--text)">${esc(scan.summary || '')}</div>
          <table style="width:100%;font-size:11px;border-collapse:collapse">
            ${row('Network', scan.network)}
            ${row('Filesystem', scan.filesystem)}
            ${row('Shell', scan.shell)}
            ${row('Secrets', scan.secrets)}
            ${row('Flags', scan.flags, true)}
          </table>
        </div>
      </div>`;
  } else if (scan.reason) {
    scanHtml = `<div style="font-size:11px;color:var(--text-faint)">Source scan unavailable: ${esc(scan.reason)}</div>`;
  }

  // Audit banner + collapsible findings list
  let auditHtml = '';
  if (audit.available) {
    const findings = audit.findings || [];
    const critHigh = (audit.critical || 0) + (audit.high || 0);
    const summaryLine = critHigh > 0
      ? `<b style="color:#ef4444">${audit.critical || 0} serious, ${audit.high || 0} important</b> &middot; <span style="color:var(--text-faint)">${audit.moderate || 0} minor, ${audit.low || 0} cosmetic</span>`
      : audit.total > 0
        ? `<span style="color:var(--text-faint)">${audit.total} minor/cosmetic issues, none serious</span>`
        : `<span style="color:#22c55e">clean</span>`;

    // One-line recommendation in plain English, derived from the worst-case
    // finding and whether any fix exists. The user shouldn't need to know
    // what "npm audit fix" means to make a decision.
    const hasFix = findings.some(f => f.fix && !f.fix.startsWith('no fix'));
    const hasUnfixable = findings.some(f => f.fix && f.fix.startsWith('no fix'));
    let recommendation;
    if (critHigh === 0 && audit.total === 0) {
      recommendation = "&#x2713; No known vulnerabilities in this server's dependencies. Safe to install.";
    } else if (critHigh === 0) {
      recommendation = "Issues found are minor / cosmetic. Most users can install safely. If you handle sensitive data with this server, review the findings below.";
    } else if (hasFix && !hasUnfixable) {
      recommendation = "&#x26A0; Serious issues exist but the affected packages have patched versions available. The repo author would need to publish an update. You can install anyway if the server's purpose doesn't expose the vulnerable code paths.";
    } else if (hasUnfixable) {
      recommendation = "&#x26A0; Serious issues with no patched version available yet. Recommended: skip this server for now, or proceed only if you understand the specific risk and trust the maintainer to fix it.";
    } else {
      recommendation = "&#x26A0; Review the findings below before installing.";
    }

    const bgStyle = critHigh > 0
      ? 'background:rgba(239,68,68,0.12);border:1px solid rgba(239,68,68,0.4)'
      : 'background:var(--surface2);border:1px solid var(--border)';
    const sevColor = s => s === 'critical' ? '#ef4444'
                       : s === 'high' ? '#f97316'
                       : s === 'moderate' ? '#eab308'
                       : '#9ca3af';
    const sevLabel = s => s === 'critical' ? 'serious'
                       : s === 'high' ? 'important'
                       : s === 'moderate' ? 'minor'
                       : 'cosmetic';

    // Per-finding plain English: what action (if any) is needed.
    const findingAction = f => {
      const fix = (f.fix || '').toLowerCase();
      if (!f.fix) return '';
      if (fix.startsWith('no fix')) {
        return `<div style="color:#ef4444;font-size:11px;margin-top:4px">
          What to do: <b>No patch exists yet.</b> Either wait for the maintainer to release a fix, or install only if you understand the specific risk.
        </div>`;
      }
      const breaking = f.fix_is_breaking
        ? ' <span style="color:#eab308">Note: this is a breaking change, so an upgrade may require code changes in the repo.</span>'
        : '';
      return `<div style="color:var(--text);font-size:11px;margin-top:4px">
        What to do: A patched version exists (<code>${esc(f.fix)}</code>). The repo maintainer would need to publish an update with that version; you can't change it yourself without forking the repo.${breaking}
      </div>`;
    };

    const findingsHtml = findings.length ? findings.map(f => `
      <div style="padding:10px 12px;border-top:1px solid var(--border);font-size:11px">
        <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:4px">
          <code style="font-size:12px;color:var(--text);font-weight:600">${esc(f.package || '?')}</code>
          <span style="font-size:10px;font-weight:700;padding:1px 6px;border-radius:3px;background:${sevColor(f.severity)};color:#fff;text-transform:uppercase">${esc(sevLabel(f.severity))}</span>
          <span style="font-size:10px;color:var(--text-faint)" title="${f.is_direct ? 'This package is listed directly in the server\'s dependencies' : 'This package was pulled in by another package the server depends on'}">${f.is_direct ? 'used directly' : 'used indirectly'}</span>
        </div>
        <div style="color:var(--text);margin-bottom:2px">${esc(f.why || '')}</div>
        ${f.advisories && f.advisories.length ? `
          <div style="color:var(--text-faint);font-size:10px;line-height:1.5">
            More info: ${f.advisories.map(a => `
              ${a.url ? `<a href="${esc(a.url)}" target="_blank" style="color:var(--accent);text-decoration:none">${esc(a.title || a.source || 'advisory')} &#x2197;</a>` : esc(a.title || a.source || 'advisory')}
              ${a.cve && a.cve.length ? ` &middot; <code>${esc(Array.isArray(a.cve) ? a.cve.join(', ') : a.cve)}</code>` : ''}
            `).join(' &nbsp; ')}
          </div>` : ''}
        ${f.upstream && f.upstream.length ? `
          <div style="color:var(--text-faint);font-size:10px;margin-top:3px">
            Pulled in via: ${f.upstream.map(esc).join(' &rarr; ')}
          </div>` : ''}
        ${findingAction(f)}
      </div>`).join('') : '';

    auditHtml = `
      <div style="padding:10px 12px;${bgStyle};border-radius:4px;font-size:12px">
        <div style="margin-bottom:4px">Dependency check: ${summaryLine}</div>
        <div style="color:var(--text);line-height:1.5">${recommendation}</div>
        ${findings.length ? `
          <details style="margin-top:8px">
            <summary style="cursor:pointer;color:var(--text-faint);font-size:11px">Show ${findings.length} finding${findings.length===1?'':'s'} in detail</summary>
            <div style="margin-top:6px;margin-left:-12px;margin-right:-12px">
              ${findingsHtml}
            </div>
          </details>` : ''}
      </div>`;
  } else if (audit.reason) {
    auditHtml = `<div style="font-size:11px;color:var(--text-faint)">Audit skipped: ${esc(audit.reason)}</div>`;
  }

  // Secrets form
  let secretsHtml = '';
  if (secrets.length) {
    secretsHtml = `
      <div>
        <div style="font-size:11px;color:var(--text-faint);margin-bottom:4px">This server needs credentials — fill them in before installing.</div>
        ${secrets.map(s => `
          <div style="display:flex;gap:8px;align-items:center;margin-bottom:6px">
            <code style="font-size:12px;min-width:160px">${esc(s.key)}</code>
            <input type="password" data-secret-key="${esc(s.key)}" placeholder="${esc(s.hint)}"
              value="${esc(st.secretValues[s.key] || '')}"
              oninput="_mcpUrlSecretChange('${modalId}','${esc(s.key)}',this.value)"
              style="flex:1;padding:5px 8px;font-size:12px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono)">
          </div>`).join('')}
      </div>`;
  }

  // Commands preview
  let cmdsHtml = '';
  if (cmds.length) {
    cmdsHtml = `
      <div>
        <div style="font-size:11px;color:var(--text-faint);margin-bottom:4px">Will run (in the cloned repo):</div>
        <pre style="margin:0;padding:8px 10px;background:var(--surface2);border-radius:4px;font-size:11px;color:var(--text);overflow-x:auto">${cmds.map(c => '$ ' + c.map(esc).join(' ')).join('\n')}</pre>
      </div>`;
  }

  // Resulting config preview
  const cfgPreview = JSON.stringify(st.config || {}, null, 2);

  // Project realProjects re-read so the picker works
  const realProjects = (allProjects || []).filter(p => !isIncognitoProject(p) && p.project_path);
  const projOptions = realProjects.map(p =>
    `<option value="${esc(p.id)}"${p.id===st.projectId?' selected':''}>${esc(p.name||p.id)}</option>`
  ).join('');

  return `
    <div style="display:flex;align-items:center;gap:8px;font-size:11px;color:var(--text-faint)">
      <span>Source: <b style="color:var(--text)">${esc(p.kind || '')}</b></span>
      <span>&middot;</span>
      <span>Config detected via: <b style="color:var(--text)">${esc(tierLabel)}</b></span>
      ${p.sha ? `<span>&middot;</span><span>SHA: <code style="font-size:10px">${esc(p.sha.slice(0,7))}</code></span>` : ''}
    </div>
    ${ghHtml}
    ${auditHtml}
    ${scanHtml}
    ${secretsHtml}
    ${cmdsHtml}
    <div style="display:flex;gap:8px;align-items:end;flex-wrap:wrap">
      <div style="flex:1;min-width:160px">
        <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">Server name</label>
        <input type="text" id="me-url-name" value="${esc(st.name || '')}"
          oninput="_mcpUrlFieldChange('${modalId}','name',this.value)"
          style="width:100%;padding:6px 10px;font-size:13px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono)">
      </div>
      <div>
        <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">Scope</label>
        <select id="me-url-scope" onchange="_mcpUrlFieldChange('${modalId}','scope',this.value)"
          style="padding:6px 10px;font-size:12px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text)">
          <option value="global" ${st.scope==='global'?'selected':''}>Global</option>
          <option value="project" ${st.scope==='project'?'selected':''} ${realProjects.length?'':'disabled'}>Project</option>
        </select>
      </div>
      <div id="me-url-project-wrap" style="display:${st.scope==='project'?'':'none'}">
        <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">Project</label>
        <select id="me-url-project" onchange="_mcpUrlFieldChange('${modalId}','projectId',this.value)"
          style="padding:6px 10px;font-size:12px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text);max-width:220px">
          ${projOptions || '<option value="">No projects with a path</option>'}
        </select>
      </div>
    </div>
    <details style="font-size:11px">
      <summary style="cursor:pointer;color:var(--text-faint)">Final config to be written</summary>
      <pre style="margin:6px 0 0;padding:8px 10px;background:var(--surface2);border-radius:4px;font-size:11px;color:var(--text);overflow-x:auto">${esc(cfgPreview)}</pre>
    </details>
    <div style="display:flex;gap:8px;justify-content:space-between;align-items:center">
      <button class="btn-secondary" onclick="_mcpUrlBack('${modalId}')">&larr; Back</button>
      <div style="display:flex;gap:8px">
        <button class="btn-secondary" onclick="closeModalById('${modalId}')">Cancel</button>
        <button class="btn-add" onclick="_mcpUrlInstall('${modalId}')">Install &rarr;</button>
      </div>
    </div>`;
}

function _mcpUrlFieldChange(modalId, key, value) {
  const entry = openModals.get(modalId);
  if (!entry || !entry._mcpUrlState) return;
  entry._mcpUrlState[key] = value;
  if (key === 'scope') {
    const wrap = entry.element.querySelector('#me-url-project-wrap');
    if (wrap) wrap.style.display = value === 'project' ? '' : 'none';
  }
}

function _mcpUrlSecretChange(modalId, key, value) {
  const entry = openModals.get(modalId);
  if (!entry || !entry._mcpUrlState) return;
  entry._mcpUrlState.secretValues = entry._mcpUrlState.secretValues || {};
  entry._mcpUrlState.secretValues[key] = value;
}

async function _mcpUrlBack(modalId) {
  const entry = openModals.get(modalId);
  if (!entry || !entry._mcpUrlState) return;
  // Clean up the staged clone server-side so we don't leak install dirs.
  const dir = (entry._mcpUrlState.preview || {}).install_dir;
  if (dir) {
    try {
      await fetch(API_BASE + '/api/mcp/url/staged', {
        method: 'DELETE', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ install_dir: dir }),
      });
    } catch (e) {}
  }
  entry._mcpUrlState = { stage: 'input', url: entry._mcpUrlState.url };
  _mcpUrlRender(modalId);
}

async function _mcpUrlInstall(modalId) {
  const entry = openModals.get(modalId);
  if (!entry || !entry._mcpUrlState) return;
  const st = entry._mcpUrlState;
  // Required-secret check.
  const missing = (st.preview.secrets || []).filter(s => !((st.secretValues || {})[s.key] || '').trim());
  if (missing.length) {
    alert('Fill in: ' + missing.map(m => m.key).join(', '));
    return;
  }
  if (!st.name || !st.name.trim()) {
    alert('Server name required');
    return;
  }
  if (st.scope === 'project' && !st.projectId) {
    alert('Pick a project');
    return;
  }
  st.stage = 'installing';
  st.log = '';
  _mcpUrlRender(modalId);

  try {
    const res = await fetch(API_BASE + '/api/mcp/url/install', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        install_dir: st.preview.install_dir,
        name: st.name.trim(),
        scope: st.scope,
        project_id: st.projectId,
        config: st.config,
        secrets: st.secretValues || {},
      }),
    });
    if (!res.ok || !res.body) {
      const err = await res.text();
      st.log = `[install request failed: ${res.status} ${err}]`;
      st.stage = 'preview';
      _mcpUrlRender(modalId);
      return;
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const events = buf.split('\n\n');
      buf = events.pop() || '';
      for (const ev of events) {
        const line = ev.split('\n').find(l => l.startsWith('data: '));
        if (!line) continue;
        let payload;
        try { payload = JSON.parse(line.slice(6)); } catch (e) { continue; }
        if (payload.type === 'log') {
          st.log = (st.log || '') + payload.text;
          _mcpUrlAppendLog(modalId, payload.text);
        } else if (payload.type === 'done') {
          st.stage = 'done';
          st.installedRecord = payload.record;
          _mcpUrlRender(modalId);
          // Invalidate cached MCP list so the dashboard reflects it.
          if (typeof loadAllMCP === 'function') {
            _allMCPCache.loaded = false;
            loadAllMCP();
          }
        } else if (payload.type === 'error') {
          st.log = (st.log || '') + '\n[error] ' + (payload.message || '');
          st.stage = 'preview';
          alert('Install failed: ' + payload.message);
          _mcpUrlRender(modalId);
        }
      }
    }
  } catch (e) {
    st.stage = 'preview';
    alert('Install failed: ' + e);
    _mcpUrlRender(modalId);
  }
}

function _mcpUrlAppendLog(modalId, text) {
  const entry = openModals.get(modalId);
  if (!entry || !entry.element) return;
  const pre = entry.element.querySelector('#me-url-log');
  if (!pre) return;
  pre.textContent = (pre.textContent || '') + text;
  pre.scrollTop = pre.scrollHeight;
}

function _mcpUrlRenderInstalling(modalId, st) {
  return `
    <div style="font-size:12px;color:var(--text-faint)">Installing — this may take a minute...</div>
    <pre id="me-url-log" style="margin:0;padding:10px;background:#0a0c10;color:#cfd8dc;border-radius:4px;font-size:11px;height:260px;overflow:auto;white-space:pre-wrap">${esc(st.log || '')}</pre>`;
}

function _mcpUrlRenderDone(modalId, st) {
  const rec = st.installedRecord || {};
  return `
    <div style="padding:14px;background:rgba(34,197,94,0.12);border:1px solid rgba(34,197,94,0.4);border-radius:6px">
      <div style="font-size:14px;font-weight:700;color:#22c55e;margin-bottom:6px">&#x2713; Installed</div>
      <div style="font-size:12px;color:var(--text)">MCP server <code>${esc(rec.name || st.name)}</code> is now configured in <b>${esc(rec.scope || st.scope)}</b> scope. Claude Code will pick it up on the next agent dispatch.</div>
    </div>
    <details style="font-size:11px">
      <summary style="cursor:pointer;color:var(--text-faint)">Install log</summary>
      <pre style="margin:6px 0 0;padding:8px 10px;background:#0a0c10;color:#cfd8dc;border-radius:4px;font-size:11px;max-height:200px;overflow:auto;white-space:pre-wrap">${esc(st.log || '')}</pre>
    </details>
    <div style="display:flex;gap:8px;justify-content:flex-end">
      <button class="btn-add" onclick="closeModalById('${modalId}')">Close</button>
    </div>`;
}


async function saveMCPServer(modalId, isNew) {
  const entry = openModals.get(modalId);
  if (!entry || !entry.element) return;
  const root = entry.element;

  const name = (root.querySelector('#me-name').value || '').trim();
  const transport = root.querySelector('#me-transport').value;
  const scopeRadio = root.querySelector('input[name="me-scope"]:checked');
  const scope = scopeRadio ? scopeRadio.value : 'global';
  const projectSel = root.querySelector('#me-project');
  const projectId = (scope === 'project' && projectSel) ? projectSel.value : null;

  if (!name) { _mcpEditorStatus(modalId, 'Name is required', 'var(--red)'); return; }
  if (scope === 'project' && !projectId) {
    _mcpEditorStatus(modalId, 'Pick a project (must have a project_path configured)', 'var(--red)');
    return;
  }

  let config = {};
  if (transport === 'stdio') {
    const command = (root.querySelector('#me-command').value || '').trim();
    const argsText = (root.querySelector('#me-args').value || '').trim();
    const envText = (root.querySelector('#me-env').value || '').trim();
    if (!command) { _mcpEditorStatus(modalId, 'Command is required for stdio', 'var(--red)'); return; }
    config = { command, args: argsText ? argsText.split(/\s+/) : [], env: _parseKVLines(envText, '=') };
  } else {
    const url = (root.querySelector('#me-url').value || '').trim();
    const headersText = (root.querySelector('#me-headers').value || '').trim();
    if (!url) { _mcpEditorStatus(modalId, 'URL is required', 'var(--red)'); return; }
    config = { url, headers: _parseKVLines(headersText, ':') };
  }

  _mcpEditorStatus(modalId, 'Saving...');

  try {
    let res;
    if (isNew) {
      res = await fetch(API_BASE + '/api/mcp', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, scope, project_id: projectId, transport, config })
      });
    } else {
      const params = new URLSearchParams();
      if (projectId) params.set('project_id', projectId);
      const url = API_BASE + `/api/mcp/${encodeURIComponent(scope)}/${encodeURIComponent(name)}` + (params.toString() ? '?' + params.toString() : '');
      res = await fetch(url, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ transport, config, project_id: projectId })
      });
    }
    if (!res.ok) {
      const errBody = await res.json().catch(() => ({}));
      _mcpEditorStatus(modalId, errBody.error || `Save failed (HTTP ${res.status})`, 'var(--red)');
      return;
    }
    closeModalById(modalId);
    _allMCPCache.loaded = false;
    loadAllMCP();
  } catch(e) {
    _mcpEditorStatus(modalId, 'Save failed: ' + e.message, 'var(--red)');
  }
}

async function deleteMCPAction(scope, name, projectId) {
  if (!confirm(`Delete MCP server "${name}"?\n\nThis removes it from ${scope === 'global' ? '~/.claude.json' : 'the project .mcp.json'}.`)) return;
  try {
    const params = new URLSearchParams();
    if (projectId) params.set('project_id', projectId);
    const url = API_BASE + `/api/mcp/${encodeURIComponent(scope)}/${encodeURIComponent(name)}` + (params.toString() ? '?' + params.toString() : '');
    const res = await fetch(url, { method: 'DELETE' });
    if (!res.ok) {
      const errBody = await res.json().catch(() => ({}));
      alert(errBody.error || `Delete failed (HTTP ${res.status})`);
      return;
    }
    _allMCPCache.loaded = false;
    loadAllMCP();
  } catch(e) {
    alert('Delete failed: ' + e.message);
  }
}



// ── Interop: re-expose for inline / cross-module + region-generated
//    on*= handler callers. All runtime-only (resolve against window at
//    event/call time); zero parse-time references. State: `_allMCPFilter`
//    is an OBJECT-IDENTITY bridge (generated handlers property-write
//    `.search`/`.scope`/`.project`; never wholesale-reassigned), so the
//    window prop and the module binding are the same live object.
//    `_allMCPCache` is module-private (zero outside refs; reassigned only
//    inside this module). ──
window.openAllMCP = openAllMCP;                     // sidebarNav('mcp')
window.openAllMCPForProject = openAllMCPForProject; // project three-dot menu onclick
// region-generated on*= handler targets:
window.loadAllMCP = loadAllMCP;
window.renderAllMCP = renderAllMCP;
window.openMCPEditor = openMCPEditor;
window._mcpToggleActive = _mcpToggleActive;
window._mcpResetLoadout = _mcpResetLoadout;
window._mcpEditorRenderTransport = _mcpEditorRenderTransport;
window._mcpEditorSetMode = _mcpEditorSetMode;
window._mcpUrlPreview = _mcpUrlPreview;
window._mcpUrlBack = _mcpUrlBack;
window._mcpUrlFieldChange = _mcpUrlFieldChange;
window._mcpUrlSecretChange = _mcpUrlSecretChange;
window._mcpUrlInstall = _mcpUrlInstall;
window.saveMCPServer = saveMCPServer;
window.deleteMCPAction = deleteMCPAction;
// object-identity bridge (handler property-writes route into the live object):
window._allMCPFilter = _allMCPFilter;
