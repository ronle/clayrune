// ── Skills (global + per-project Anthropic-format skill manager) ─────────────
//
// Skills live at ~/.claude/skills/<name>/SKILL.md (global) and
// <project_path>/.claude/skills/<name>/SKILL.md (project-local).  MC's role
// is the management surface — list, create, edit, archive, search, stats.
// CC reads them natively at session start; no preamble injection needed.

let _allSkillsCache = { items: [], loaded: false, loading: false };
let _allSkillsFilter = { scope: 'all', project: 'all', search: '', includeArchived: false };
let _skillUsageCache = { stats: null, loaded: false };

function openAllSkillsForProject(projectId) {
  _allSkillsFilter.project = projectId || 'all';
  _allSkillsFilter.scope = 'all';
  openAllSkills();
}

async function openAllSkills() {
  const modalId = '__all_skills';
  if (openModals.has(modalId)) {
    const entry = openModals.get(modalId);
    if (entry.minimized) restoreModal(modalId);
    focusModal(modalId);
    renderAllSkills();
    loadAllSkills();
    loadDistillerQueue();
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
      <span style="font-size:16px;font-weight:700;color:var(--text)">&#x1F9E9; Skills</span>
      <div class="modal-window-controls" style="position:static;display:flex;gap:4px">
        <button class="modal-minimize" onclick="minimizeModal('${modalId}')" title="Minimize">&#x2015;</button>
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
    </div>
    <div style="padding:4px 24px 20px 28px">
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:12px;flex-wrap:wrap">
        <input type="text" id="as-search" placeholder="Search name / description..." value="${esc(_allSkillsFilter.search)}"
          style="flex:1;min-width:180px;padding:6px 10px;font-size:12px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text)"
          oninput="_allSkillsFilter.search=this.value;renderAllSkills()">
        <select id="as-scope" style="padding:6px 8px;font-size:12px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text)"
          onchange="_allSkillsFilter.scope=this.value;renderAllSkills()">
          <option value="all"${_allSkillsFilter.scope==='all'?' selected':''}>All scopes</option>
          <option value="global"${_allSkillsFilter.scope==='global'?' selected':''}>Global only</option>
          <option value="project"${_allSkillsFilter.scope==='project'?' selected':''}>Project only</option>
          <option value="archive"${_allSkillsFilter.scope==='archive'?' selected':''}>Archived</option>
        </select>
        <select id="as-project" style="padding:6px 8px;font-size:12px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text);max-width:200px"
          onchange="_allSkillsFilter.project=this.value;loadAllSkills()">
          <option value="all">All projects</option>
        </select>
        <label style="display:flex;align-items:center;gap:4px;font-size:11px;color:var(--text-faint);cursor:pointer">
          <input type="checkbox" id="as-archived" ${_allSkillsFilter.includeArchived?'checked':''}
            onchange="_allSkillsFilter.includeArchived=this.checked;loadAllSkills()">
          Include archived
        </label>
        <span id="as-count" style="font-size:11px;color:var(--text-faint)"></span>
        <div style="margin-left:auto;display:flex;gap:6px;position:relative" id="skills-import-wrapper">
          <button class="btn-secondary" onclick="_toggleSkillsImportMenu(event)">Import &#x25BE;</button>
          <button class="btn-add" onclick="openSkillEditor('global', '', null, true)">+ New Skill</button>
          <div id="skills-import-menu" style="display:none;position:absolute;top:100%;left:0;margin-top:4px;background:var(--surface);border:1px solid var(--border);border-radius:6px;padding:4px 0;z-index:10;min-width:220px;box-shadow:0 8px 24px rgba(0,0,0,.3)">
            <button class="modal-menu-item" onclick="openSkillImportPaste()" style="width:100%;text-align:left">
              <span class="menu-icon">&#x1F4CB;</span> Paste SKILL.md
            </button>
            <button class="modal-menu-item" onclick="openSkillImportFolder()" style="width:100%;text-align:left">
              <span class="menu-icon">&#x1F4C1;</span> From a folder
            </button>
            <button class="modal-menu-item" onclick="openSkillImportGit()" style="width:100%;text-align:left">
              <span class="menu-icon">&#x1F310;</span> From a Git URL
            </button>
            <button class="modal-menu-item" onclick="openSkillImportBrowse()" style="width:100%;text-align:left">
              <span class="menu-icon">&#x1F50D;</span> Browse other projects
            </button>
          </div>
        </div>
      </div>
      <div id="as-queue"></div>
      <div id="as-list" style="max-height:65vh;overflow-y:auto"></div>
    </div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);

  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);

  renderAllSkills();
  loadAllSkills();
  loadSkillUsage();
  loadDistillerQueue();
}

async function loadAllSkills() {
  if (_allSkillsCache.loading) return;
  _allSkillsCache.loading = true;
  try {
    // If a specific project is selected, we pass project_id so project-local skills + shadow flags appear
    const pid = _allSkillsFilter.project !== 'all' ? _allSkillsFilter.project : '';
    const params = new URLSearchParams();
    if (pid) params.set('project_id', pid);
    if (_allSkillsFilter.includeArchived || _allSkillsFilter.scope === 'archive') {
      params.set('include_archived', 'true');
    }
    const url = API_BASE + '/api/skills' + (params.toString() ? '?' + params.toString() : '');
    const res = await fetch(url);
    const items = await res.json();

    // If the user has projects, eagerly fetch project-local skills for each so the
    // "All projects" view actually shows them. Cheap (one call per project) and only
    // runs when scope=all/project and no specific project filter.
    let extra = [];
    if ((_allSkillsFilter.project === 'all') && (_allSkillsFilter.scope === 'all' || _allSkillsFilter.scope === 'project')) {
      const realProjects = (allProjects || []).filter(p => !isIncognitoProject(p) && p.project_path);
      await Promise.all(realProjects.map(async p => {
        try {
          const r = await fetch(API_BASE + '/api/skills?project_id=' + encodeURIComponent(p.id));
          const arr = await r.json();
          // Filter to scope==='project' only (we already have globals from the first call)
          for (const s of arr) if (s.scope === 'project') extra.push(s);
        } catch(e) {}
      }));
    }

    _allSkillsCache = { items: items.concat(extra), loaded: true, loading: false };
    renderAllSkills();
  } catch(e) {
    _allSkillsCache = { items: [], loaded: true, loading: false };
    renderAllSkills();
  }
}

async function loadSkillUsage() {
  if (_skillUsageCache.loaded) return;
  try {
    const res = await fetch(API_BASE + '/api/skills/usage?days=30');
    _skillUsageCache = { stats: await res.json(), loaded: true };
    renderAllSkills();
  } catch(e) {
    _skillUsageCache = { stats: {}, loaded: true };
  }
}

// ── Learning queue (Distiller _proposed/ artifacts + loop-health) ────────────
// The human-promotes leg: surface the cross-session Distiller's proposals so
// the operator can promote a good one into a real skill or reject it. Also
// renders the loop-health self-detection alerts so a degraded leg is visible.
let _distillerQueueOpen = true;

async function loadDistillerQueue() {
  const el = document.getElementById('as-queue');
  if (!el) return;
  try {
    const [qRes, hRes] = await Promise.all([
      fetch(API_BASE + '/api/distiller/_proposed'),
      fetch(API_BASE + '/api/distiller/loop-health')
    ]);
    const items = await qRes.json().catch(() => []);
    const health = await hRes.json().catch(() => ({}));
    renderDistillerQueue(el, Array.isArray(items) ? items : [], health || {});
  } catch (e) {
    el.innerHTML = '';
  }
}

function _distillerKindBadge(kind) {
  const map = {
    skill:       ['&#x1F9E9;', 'var(--accent)'],
    exploration: ['&#x1F50D;', '#5b8def'],
    preference:  ['&#x2699;',  '#b07cd0'],
    update:      ['&#x270E;',  '#d0a24c'],
  };
  const [icon, color] = map[kind] || ['&#x2022;', 'var(--text-faint)'];
  return `<span style="font-size:10px;font-weight:700;color:${color};border:1px solid ${color};border-radius:3px;padding:1px 5px;white-space:nowrap">${icon} ${esc((kind||'?').toUpperCase())}</span>`;
}

let _distillerDiagOpen = false;
// Explorations are reference notes the readback already uses silently; they
// have no Promote action (only Reject), so they're collapsed by default — the
// queue stays a list of real promote/reject DECISIONS, not a wall of notes.
let _distillerExplorationsOpen = false;
// Cache the proposals so click handlers reference them BY INDEX. Embedding the
// artifact directory (a Windows path with backslashes) into an onclick JS
// string literal eats the backslashes (\U -> U), corrupting the path — so we
// never serialize it into HTML; we look it up from this array at click time.
let _distillerQueueItems = [];

function renderDistillerQueue(el, items, health) {
  const alerts = (health && Array.isArray(health.alerts)) ? health.alerts : [];
  if (!items.length && !alerts.length) { el.innerHTML = ''; return; }
  _distillerQueueItems = items;

  // Diagnostics (the loop-health alerts) are developer-facing — tuck them
  // behind a disclosure so they don't crowd out the actual proposals.
  const diagHtml = alerts.length ? `
    <div style="margin:8px 0 4px">
      <div style="font-size:11px;color:var(--text-faint);cursor:pointer" onclick="_distillerDiagOpen=!_distillerDiagOpen;loadDistillerQueue()">
        ${_distillerDiagOpen ? '&#x25BE;' : '&#x25B8;'} Health diagnostics (${alerts.length})
      </div>
      ${_distillerDiagOpen ? `<div style="margin-top:6px;display:flex;flex-direction:column;gap:5px">
        ${alerts.map(a => `<div style="font-size:11px;color:#b8893a;background:#d0a24c14;border:1px solid #d0a24c33;border-radius:4px;padding:6px 9px">${esc(a)}</div>`).join('')}
      </div>` : ''}
    </div>` : '';

  const rowData = items.map((it, i) => {
    const dir = it.directory || '';
    const scope = it.scope || 'uncategorized';
    const isCross = scope === 'cross-project';
    const recur = it.recurrence_count_exact || it.recurrence_count_coarse || '';
    const where = isCross ? 'seen across projects' : ('in ' + esc(scope));
    const title = esc(it.title || it.name || '(unnamed)');
    const snippet = it.snippet ? esc(it.snippet) : '';
    // Kind-aware actions: explorations are reference notes the readback surfaces
    // on demand (their question-shaped triggers were noise as always-loaded
    // skills), so the queue only curates them — Reject noise, keep the rest in
    // _proposed/ where readback uses them. Only SKILL / PREFERENCE — the kinds
    // meant to be always-loaded — can Promote. Promote: project-scoped go to
    // their project or global; cross-project go global only (project promote
    // needs a project_id). Handlers read the (un-serialized) path from cache.
    const isExploration = it.kind === 'exploration';
    const promoteBtns = isExploration
      ? `<span style="font-size:10px;color:var(--text-faint);align-self:center" title="Surfaced on demand by the readback when a task matches — no install needed">&#x1F50D; auto-surfaced on demand</span>`
      : (isCross
        ? `<button class="btn-secondary" style="font-size:11px;padding:3px 8px" title="Install as a skill available in every project" onclick="promoteProposedByIdx(${i},'global')">Promote &#x2192; Global</button>`
        : `<button class="btn-secondary" style="font-size:11px;padding:3px 8px" title="Install as a skill in this project only" onclick="promoteProposedByIdx(${i},'project')">Promote &#x2192; Project</button>
           <button class="btn-secondary" style="font-size:11px;padding:3px 8px" title="Install as a skill available in every project" onclick="promoteProposedByIdx(${i},'global')">Global</button>`);
    const html = `
      <div style="padding:10px 4px;border-bottom:1px solid var(--border)">
        <div style="display:flex;align-items:flex-start;gap:10px">
          <div style="flex-shrink:0;padding-top:1px">${_distillerKindBadge(it.kind)}</div>
          <div style="flex:1;min-width:0">
            <div style="font-size:12px;font-weight:600;color:var(--text);word-break:break-word">${title}</div>
            ${snippet ? `<div style="font-size:11px;color:var(--text-muted, var(--text-faint));margin-top:3px;line-height:1.4">${snippet}</div>` : ''}
            <div style="font-size:10px;color:var(--text-faint);margin-top:4px">${where}${recur ? ' &#x2022; seen &#xD7;' + esc(String(recur)) : ''}
              &#x2022; <span style="cursor:pointer;text-decoration:underline" onclick="toggleProposedReadByIdx(${i}, 'pa-${i}')">read full</span></div>
            <div id="pa-${i}" style="display:none;margin-top:8px;padding:10px;background:var(--surface);border:1px solid var(--border);border-radius:6px;font-size:11px;color:var(--text);white-space:pre-wrap;max-height:300px;overflow:auto;line-height:1.5"></div>
          </div>
        </div>
        <div style="display:flex;gap:5px;flex-wrap:wrap;justify-content:flex-end;margin-top:7px">
          ${promoteBtns}
          <button class="btn-secondary" style="font-size:11px;padding:3px 8px;color:#c0556b;border-color:#c0556b55" title="Discard and stop suggesting this" onclick="rejectProposedByIdx(${i})">Reject</button>
        </div>
      </div>`;
    return { isExploration, html };
  });

  // Split: promotable proposals (PREFERENCE/SKILL/UPDATE) are real decisions and
  // lead the queue; explorations are reference notes the readback already uses
  // silently, so they collapse into a prune-only drawer (the user's call —
  // 2026-06-10: "why present them if all I can do is reject them?").
  const promotableRows = rowData.filter(r => !r.isExploration).map(r => r.html).join('');
  const explorationRows = rowData.filter(r => r.isExploration).map(r => r.html).join('');
  const explCount = rowData.filter(r => r.isExploration).length;
  const promotableCount = rowData.length - explCount;
  const hitRate = (health && health.readback && typeof health.readback.hit_rate === 'number')
    ? Math.round(health.readback.hit_rate * 100) : null;
  const explDrawer = explCount ? `
    <div style="margin-top:10px;border-top:1px dashed var(--border);padding-top:8px">
      <div style="font-size:11px;color:var(--text-faint);cursor:pointer" onclick="_distillerExplorationsOpen=!_distillerExplorationsOpen;loadDistillerQueue()">
        ${_distillerExplorationsOpen ? '&#x25BE;' : '&#x25B8;'} &#x1F50D; ${explCount} reference note${explCount>1?'s':''} &#x2014; auto-used by agents${hitRate!=null?', '+hitRate+'% hit rate':''} &#x2022; expand to prune noise
      </div>
      ${_distillerExplorationsOpen ? `<div style="margin-top:6px">${explorationRows}</div>` : ''}
    </div>` : '';

  const bodyHtml = _distillerQueueOpen ? `
    <div style="font-size:11px;color:var(--text-faint);margin:6px 0 2px;line-height:1.45">
      What agents worked out across sessions. <b>&#x1F50D; Explorations</b> are reference notes surfaced on demand when a task matches &#x2014; just <b>Reject</b> the noise. <b>Skills &amp; preferences</b> can be <b>Promoted</b> into artifacts the agent always loads.
    </div>
    ${diagHtml}
    <div style="max-height:300px;overflow-y:auto;margin-top:6px">${promotableRows || '<div style="font-size:11px;color:var(--text-faint);padding:8px 4px">No proposals to review.</div>'}</div>
    ${explDrawer}
  ` : '';

  el.innerHTML = `
    <div style="border:1px solid var(--border);border-radius:8px;padding:12px 14px;margin-bottom:14px;background:var(--surface2)">
      <div style="display:flex;align-items:center;gap:8px;cursor:pointer" onclick="_distillerQueueOpen=!_distillerQueueOpen;loadDistillerQueue()">
        <span style="font-size:13px;font-weight:700;color:var(--text)">&#x1F9E0; Learning queue</span>
        <span style="font-size:11px;color:var(--text-faint)">${promotableCount} pending${alerts.length ? ' &#x2022; ' + alerts.length + ' alert' + (alerts.length>1?'s':'') : ''}</span>
        <span style="margin-left:auto;font-size:11px;color:var(--text-faint)">${_distillerQueueOpen ? '&#x25BE;' : '&#x25B8;'}</span>
      </div>
      ${bodyHtml}
    </div>`;
}

async function toggleProposedRead(directory, rowId) {
  const box = document.getElementById(rowId);
  if (!box) return;
  if (box.style.display !== 'none') { box.style.display = 'none'; return; }
  box.style.display = 'block';
  box.textContent = 'Loading…';
  try {
    const res = await fetch(API_BASE + '/api/distiller/proposed-artifact?directory=' + encodeURIComponent(directory));
    const art = await res.json();
    if (!res.ok || !art || art.error) { box.textContent = (art && art.error) || 'Could not load.'; return; }
    box.textContent = art.body || '(empty)';
  } catch (e) {
    box.textContent = 'Could not load.';
  }
}

async function _distillerPost(url, payload) {
  try {
    const res = await fetch(API_BASE + url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.ok === false) {
      showToast((data && data.error) ? data.error : 'Action failed');
      return false;
    }
    return true;
  } catch (e) {
    showToast('Network error');
    return false;
  }
}

// Index-based handlers — read the artifact path from the cached item so the
// (backslash-laden, Windows) directory never passes through an HTML/JS string.
function promoteProposedByIdx(i, scope) {
  const it = _distillerQueueItems[i];
  if (!it) return;
  promoteProposed(it.directory, scope, scope === 'project' ? it.scope : null);
}
function rejectProposedByIdx(i) {
  const it = _distillerQueueItems[i];
  if (it) rejectProposed(it.directory);
}
function toggleProposedReadByIdx(i, rowId) {
  const it = _distillerQueueItems[i];
  if (it) toggleProposedRead(it.directory, rowId);
}

async function promoteProposed(directory, scope, projectId) {
  const payload = { directory, scope };
  if (scope === 'project' && projectId) payload.project_id = projectId;
  const ok = await _distillerPost('/api/distiller/promote', payload);
  if (ok) {
    showToast('Promoted to ' + (scope === 'global' ? 'global' : 'project') + ' skills');
    _allSkillsCache.loaded = false;
    loadAllSkills();
    loadDistillerQueue();
  }
}

async function rejectProposed(directory) {
  if (!confirm('Reject this proposal? It will be suppressed (not re-proposed) and moved to _rejected/.')) return;
  const ok = await _distillerPost('/api/distiller/reject', { directory });
  if (ok) { showToast('Rejected'); loadDistillerQueue(); }
}

function renderAllSkills() {
  const container = document.getElementById('as-list');
  const countEl = document.getElementById('as-count');
  const projectSel = document.getElementById('as-project');
  if (!container) return;

  // Populate project dropdown from currently loaded projects
  if (projectSel) {
    const realProjects = (allProjects || []).filter(p => !isIncognitoProject(p));
    const cur = _allSkillsFilter.project;
    const opts = ['<option value="all">All projects</option>',
      ...realProjects.map(p =>
        `<option value="${esc(p.id)}"${cur===p.id?' selected':''}>${esc(p.name||p.id)}</option>`
      )];
    if (projectSel.innerHTML !== opts.join('')) {
      projectSel.innerHTML = opts.join('');
      projectSel.value = cur;
    }
  }

  if (!_allSkillsCache.loaded) {
    container.innerHTML = '<div style="padding:40px 12px;text-align:center;color:var(--text-faint);font-size:12px">Loading skills...</div>';
    if (countEl) countEl.textContent = '';
    return;
  }

  const f = _allSkillsFilter;
  const q = (f.search || '').trim().toLowerCase();
  let rows = _allSkillsCache.items.filter(s => {
    if (f.scope !== 'all' && s.scope !== f.scope) return false;
    if (f.project !== 'all') {
      if (s.scope === 'project' && s.project_id !== f.project) return false;
      // Global skills are visible across all projects, so don't filter them out
    }
    if (q) {
      const hay = ((s.name || '') + ' ' + (s.description || '')).toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });

  // De-dup (a global+project might appear twice if shadowed)
  const seen = new Set();
  rows = rows.filter(s => {
    const key = s.scope + ':' + s.name + ':' + (s.project_id || '');
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  // Sort: scope (project first, then global, then archive), name asc
  const scopeOrder = { project: 0, global: 1, archive: 2 };
  rows.sort((a, b) => {
    const oa = scopeOrder[a.scope] ?? 9;
    const ob = scopeOrder[b.scope] ?? 9;
    if (oa !== ob) return oa - ob;
    return (a.name || '').localeCompare(b.name || '');
  });

  if (countEl) countEl.textContent = `${rows.length} skill${rows.length===1?'':'s'}`;

  if (!rows.length) {
    container.innerHTML = `<div style="padding:40px 12px;text-align:center;color:var(--text-faint);font-size:12px">No skills match the current filters.<br><span style="font-size:11px">Click "+ New Skill" to author one.</span></div>`;
    return;
  }

  container.innerHTML = rows.map(s => _renderSkillRow(s)).join('');
}

function _renderSkillRow(s) {
  const usage = (_skillUsageCache.stats && _skillUsageCache.stats[s.name]) || null;
  const usageStr = usage ? `<span title="Invocations in last 30 days" style="font-size:10px;color:var(--text-faint);margin-left:6px">${usage.invocations}× last 30d</span>` : '';
  const scopeBadge = (() => {
    if (s.scope === 'global') return '<span style="font-size:10px;background:var(--surface2);color:var(--text-faint);padding:1px 6px;border-radius:3px">global</span>';
    if (s.scope === 'project') {
      const pname = (allProjects || []).find(p => p.id === s.project_id);
      const label = pname ? (pname.name || pname.id) : (s.project_id || 'project');
      return `<span style="font-size:10px;background:var(--accent-dim);color:var(--accent);padding:1px 6px;border-radius:3px" title="Project-scoped">project: ${esc(label)}</span>`;
    }
    if (s.scope === 'archive') return '<span style="font-size:10px;background:var(--surface2);color:var(--text-faint);padding:1px 6px;border-radius:3px;opacity:0.7">archived</span>';
    return '';
  })();
  const shadowBadge = s.shadowed_by_project
    ? '<span style="font-size:10px;background:transparent;color:var(--amber);padding:1px 6px;border:1px solid var(--amber);border-radius:3px;margin-left:4px" title="A project skill of the same name shadows this global">shadowed</span>'
    : '';
  const editArgs = `'${esc(s.scope)}', '${esc(s.name)}', ${s.project_id ? `'${esc(s.project_id)}'` : 'null'}, false`;
  const archiveArgs = `'${esc(s.scope)}', '${esc(s.name)}', ${s.project_id ? `'${esc(s.project_id)}'` : 'null'}`;
  const actionsHTML = (() => {
    if (s.scope === 'archive') {
      return `<button class="btn-tiny" onclick="restoreSkillAction('${esc(s.name)}')">Restore</button>
              <button class="btn-tiny danger" onclick="deleteSkillAction('archive', '${esc(s.name)}', null, true)">Delete</button>`;
    }
    return `<button class="btn-tiny" onclick="openSkillEditor(${editArgs})">Edit</button>
            <button class="btn-tiny" onclick="archiveSkillAction(${archiveArgs})">${s.scope==='global'?'Archive':'Delete'}</button>`;
  })();

  const mtime = s.mtime_iso ? new Date(s.mtime_iso).toLocaleString() : '';

  return `<div class="skill-row" style="padding:10px 12px;border-bottom:1px solid var(--border);display:flex;flex-direction:column;gap:4px">
    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
      <span style="font-weight:600;color:var(--text);font-size:13px">${esc(s.name)}</span>
      ${scopeBadge}
      ${shadowBadge}
      ${usageStr}
      <div style="margin-left:auto;display:flex;gap:6px">${actionsHTML}</div>
    </div>
    <div style="font-size:12px;color:var(--text-mute);line-height:1.4">${esc(s.description || '(no description)')}</div>
    <div style="font-size:10px;color:var(--text-faint);font-family:var(--mono)">${esc(s.path || '')} &middot; ${esc(mtime)}</div>
  </div>`;
}

function lintSkillDescription(text) {
  const out = [];
  const t = (text || '').trim();
  if (!t) return out;
  if (t.length < 40) out.push('Description is short — aim for at least 40 characters so the model can pick the skill from the catalog.');
  if (!/\btrigger\b/i.test(t)) out.push('No TRIGGER section detected. Include explicit phrases like "TRIGGER when user says X" so the model knows when to apply this skill.');
  if (/\bwhen needed\b|\bif relevant\b|\bas appropriate\b/i.test(t)) out.push('Trigger language is vague ("when needed" / "if relevant"). Be specific about user phrasings or task shapes.');
  return out;
}

function openSkillEditor(scope, name, projectId, isNew) {
  const modalId = isNew ? '__skill_new' : `__skill_${scope}_${name}`;
  if (openModals.has(modalId)) { focusModal(modalId); return; }

  // Async fetch when editing an existing skill — open shell first, populate on load
  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content';
  _clampModalSize(content, 820);

  const title = isNew ? 'New skill' : `Edit: ${name}`;
  content.innerHTML = `
    <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:16px 24px 12px 28px">
      <span style="font-size:16px;font-weight:700;color:var(--text)">&#x1F9E9; ${esc(title)}</span>
      <div class="modal-window-controls" style="position:static;display:flex;gap:4px">
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
    </div>
    <div style="padding:4px 24px 20px 28px;display:flex;flex-direction:column;gap:12px">
      <div>
        <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">Name (kebab-case, lowercase, hyphens)</label>
        <input type="text" id="se-name" value="${esc(name || '')}" ${isNew?'':'readonly'}
          placeholder="my-skill-name"
          style="width:100%;padding:6px 10px;font-size:13px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono);${isNew?'':'opacity:0.6'}">
      </div>
      <div>
        <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">Scope</label>
        <div style="display:flex;gap:12px;align-items:center">
          <label style="font-size:12px;color:var(--text);display:flex;gap:4px;align-items:center;cursor:${isNew?'pointer':'not-allowed'}">
            <input type="radio" name="se-scope-${modalId}" value="global" ${scope==='global'?'checked':''} ${isNew?'':'disabled'} onchange="_seToggleProjectPicker('${modalId}')"> Global
          </label>
          <label style="font-size:12px;color:var(--text);display:flex;gap:4px;align-items:center;cursor:${isNew?'pointer':'not-allowed'}">
            <input type="radio" name="se-scope-${modalId}" value="project" ${scope==='project'?'checked':''} ${isNew?'':'disabled'} onchange="_seToggleProjectPicker('${modalId}')"> Project
          </label>
          <select id="se-project" style="padding:5px 8px;font-size:12px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text);${scope==='project'?'':'display:none'}" ${isNew?'':'disabled'}>
            <option value="">— pick a project —</option>
            ${(allProjects || []).filter(p => !isIncognitoProject(p) && p.project_path).map(p =>
              `<option value="${esc(p.id)}"${projectId===p.id?' selected':''}>${esc(p.name||p.id)}</option>`
            ).join('')}
          </select>
        </div>
      </div>
      <div>
        <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">Description (used by the model to decide when to invoke this skill — include TRIGGER conditions)</label>
        <textarea id="se-description" rows="3" placeholder="Use when... TRIGGER on user phrases like..."
          oninput="_seLintDescription('${modalId}')"
          style="width:100%;padding:8px 10px;font-size:12px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text);resize:vertical;font-family:inherit"></textarea>
        <div id="se-lint" style="margin-top:6px;font-size:11px;color:var(--amber);min-height:14px"></div>
      </div>
      <div>
        <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">Body (markdown — full playbook loaded into context only when the skill is invoked)</label>
        <textarea id="se-body" rows="18" placeholder="# Skill name&#10;&#10;## Steps&#10;&#10;1. ..."
          style="width:100%;padding:8px 10px;font-size:12px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text);resize:vertical;font-family:var(--mono);line-height:1.5"></textarea>
      </div>
      <div style="display:flex;gap:8px;align-items:center;justify-content:flex-end">
        <span id="se-status" style="font-size:11px;color:var(--text-faint);margin-right:auto"></span>
        <button class="btn-secondary" onclick="closeModalById('${modalId}')">Cancel</button>
        <button class="btn-add" onclick="saveSkillFromEditor('${modalId}', ${isNew?'true':'false'}, '${esc(scope)}', '${esc(name)}', ${projectId?`'${esc(projectId)}'`:'null'})">${isNew?'Create':'Save'}</button>
      </div>
    </div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);

  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: projectId || null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);

  if (!isNew) {
    // Fetch existing skill content
    const params = new URLSearchParams({ include_body: 'true' });
    if (projectId) params.set('project_id', projectId);
    fetch(API_BASE + `/api/skills/${encodeURIComponent(scope)}/${encodeURIComponent(name)}?` + params.toString())
      .then(r => r.json())
      .then(data => {
        if (data && !data.error) {
          const descEl = win.querySelector('#se-description');
          const bodyEl = win.querySelector('#se-body');
          if (descEl) descEl.value = data.description || '';
          if (bodyEl) bodyEl.value = data.body || '';
          _seLintDescription(modalId);
        }
      })
      .catch(() => {});
  }
}

function _seToggleProjectPicker(modalId) {
  const win = openModals.get(modalId)?.element;
  if (!win) return;
  const scopeRadio = win.querySelector(`input[name="se-scope-${modalId}"]:checked`);
  const sel = win.querySelector('#se-project');
  if (sel) sel.style.display = (scopeRadio?.value === 'project') ? '' : 'none';
}

function _seLintDescription(modalId) {
  const win = openModals.get(modalId)?.element;
  if (!win) return;
  const descEl = win.querySelector('#se-description');
  const lintEl = win.querySelector('#se-lint');
  if (!descEl || !lintEl) return;
  const warnings = lintSkillDescription(descEl.value);
  lintEl.innerHTML = warnings.length
    ? warnings.map(w => `&#x26A0; ${esc(w)}`).join('<br>')
    : '';
}

async function saveSkillFromEditor(modalId, isNew, origScope, origName, origProjectId) {
  const win = openModals.get(modalId)?.element;
  if (!win) return;
  const statusEl = win.querySelector('#se-status');
  const name = isNew ? (win.querySelector('#se-name')?.value || '').trim() : origName;
  const description = (win.querySelector('#se-description')?.value || '').trim();
  const body = win.querySelector('#se-body')?.value || '';
  const scopeRadio = win.querySelector(`input[name="se-scope-${modalId}"]:checked`);
  const scope = isNew ? (scopeRadio?.value || 'global') : origScope;
  const projectId = isNew
    ? (scope === 'project' ? (win.querySelector('#se-project')?.value || '') : '')
    : (origProjectId || '');

  if (!name) { statusEl.textContent = 'Name is required'; statusEl.style.color = 'var(--red)'; return; }
  if (!description) { statusEl.textContent = 'Description is required'; statusEl.style.color = 'var(--red)'; return; }
  if (scope === 'project' && !projectId) { statusEl.textContent = 'Pick a project for project-scoped skills'; statusEl.style.color = 'var(--red)'; return; }

  statusEl.textContent = 'Saving...';
  statusEl.style.color = 'var(--text-faint)';

  try {
    const url = isNew
      ? (API_BASE + '/api/skills')
      : (API_BASE + `/api/skills/${encodeURIComponent(scope)}/${encodeURIComponent(name)}`);
    const method = isNew ? 'POST' : 'PUT';
    const payload = isNew
      ? { name, description, body, scope, project_id: projectId || undefined }
      : { description, body, project_id: projectId || undefined };
    const res = await fetch(url, {
      method,
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok || data.error) {
      statusEl.textContent = data.error || ('HTTP ' + res.status);
      statusEl.style.color = 'var(--red)';
      return;
    }
    statusEl.textContent = 'Saved';
    statusEl.style.color = 'var(--green)';
    // Refresh list + close after a beat
    _allSkillsCache.loaded = false;
    loadAllSkills();
    setTimeout(() => closeModalById(modalId), 400);
  } catch(e) {
    statusEl.textContent = 'Save failed: ' + e.message;
    statusEl.style.color = 'var(--red)';
  }
}

async function archiveSkillAction(scope, name, projectId) {
  const verb = (scope === 'global') ? 'Archive' : 'Delete';
  if (!confirm(`${verb} skill "${name}"?`)) return;
  try {
    const params = new URLSearchParams();
    if (projectId) params.set('project_id', projectId);
    if (scope !== 'global') params.set('archive', 'false');
    const res = await fetch(API_BASE + `/api/skills/${encodeURIComponent(scope)}/${encodeURIComponent(name)}?` + params.toString(), { method: 'DELETE' });
    const data = await res.json();
    if (!res.ok || data.error) { showToast(data.error || 'Failed', 4000); return; }
    showToast(data.action === 'archived' ? 'Archived' : 'Deleted');
    _allSkillsCache.loaded = false;
    loadAllSkills();
  } catch(e) { showToast('Action failed: ' + e.message, 4000); }
}

async function deleteSkillAction(scope, name, projectId, hard) {
  if (!confirm(`Permanently delete archived skill "${name}"? This cannot be undone.`)) return;
  try {
    // For archive scope, we use a different delete endpoint pattern — currently
    // archived skills delete via filesystem path; expose this through DELETE on
    // archive scope. (Backend handles archive as a scope.)
    const res = await fetch(API_BASE + `/api/skills/archive/${encodeURIComponent(name)}?archive=false`, { method: 'DELETE' });
    if (!res.ok) {
      // Fallback: archive scope delete may not be exposed; just hide locally
      showToast('Could not delete archived skill (manual cleanup required)', 4000);
    } else {
      showToast('Deleted');
    }
    _allSkillsCache.loaded = false;
    loadAllSkills();
  } catch(e) { showToast('Delete failed: ' + e.message, 4000); }
}

async function restoreSkillAction(name) {
  try {
    const res = await fetch(API_BASE + `/api/skills/archive/${encodeURIComponent(name)}/restore`, { method: 'POST' });
    const data = await res.json();
    if (!res.ok || data.error) { showToast(data.error || 'Failed', 4000); return; }
    showToast('Restored to global skills');
    _allSkillsCache.loaded = false;
    loadAllSkills();
  } catch(e) { showToast('Restore failed: ' + e.message, 4000); }
}

// ── Skills import (paste / folder / Git URL / cross-project) ─────────────────

let win_gitStaging = {}; // modalId -> staging_id (set when multi-skill picker is shown)
let win_importPluginSource = {}; // modalId -> { source: 'git'|'folder', staging_id?, path? }

function _renderPluginBanner(plugin) {
  if (!plugin) return '';
  const stats = [];
  if (plugin.skill_count) stats.push(`${plugin.skill_count} skill${plugin.skill_count===1?'':'s'}`);
  if (plugin.command_count) stats.push(`${plugin.command_count} command${plugin.command_count===1?'':'s'}`);
  if (plugin.agent_count) stats.push(`${plugin.agent_count} sub-agent${plugin.agent_count===1?'':'s'}`);
  if (plugin.hook_count) stats.push(`${plugin.hook_count} hook${plugin.hook_count===1?'':'s'}`);
  const statsLine = stats.length ? stats.join(' · ') : 'no managed components';
  const readme = (plugin.readme_excerpt || '').trim();
  const readmeHTML = readme
    ? `<div style="font-size:11px;color:var(--text-mute);margin-top:6px;line-height:1.5;max-height:80px;overflow-y:auto;white-space:pre-wrap;font-family:inherit">${esc(readme.slice(0, 360))}${readme.length>360?'…':''}</div>`
    : '';
  const hookNote = plugin.has_hooks
    ? `<div style="font-size:11px;color:var(--amber);margin-top:6px">&#x26A0; Hooks won't be installed by Clayrune (they run arbitrary shell code). To enable them, install the whole plugin via CC's <code>/plugin</code> command.</div>`
    : '';
  return `<div style="padding:10px 12px;border:1px solid var(--accent-dim);background:var(--accent-dim);border-radius:6px;margin-bottom:10px">
    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
      <span style="font-size:11px;background:var(--accent);color:#fff;padding:2px 8px;border-radius:3px;font-weight:600">PLUGIN</span>
      <span style="font-size:13px;font-weight:600;color:var(--text)">${esc(plugin.name)}</span>
      <span style="font-size:11px;color:var(--text-faint)">${esc(statsLine)}</span>
    </div>
    ${readmeHTML}
    ${hookNote}
  </div>`;
}

function _renderFullPluginButton(modalId) {
  return `<button class="btn-add" style="margin-bottom:10px;width:100%" onclick="_doSkillImportFullPlugin('${modalId}')">
    Install full plugin (skills + commands + sub-agents)
  </button>`;
}

async function _doSkillImportFullPlugin(modalId) {
  const src = win_importPluginSource[modalId];
  if (!src) { _siStatus(modalId, 'no plugin source for this modal', 'var(--red)'); return; }
  _siStatus(modalId, 'Installing full plugin...');
  try {
    const body = src.source === 'git'
      ? { staging_id: src.staging_id }
      : { path: src.path };
    const res = await fetch(API_BASE + '/api/skills/import/plugin', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok || data.error) { _siStatus(modalId, data.error || 'HTTP ' + res.status, 'var(--red)'); return; }
    _siStatus(modalId, data.message || 'Installed', 'var(--green)');
    showToast(data.message || ('Installed ' + (data.plugin_name || 'plugin')), 6000);
    win_importPluginSource[modalId] = null;
    win_gitStaging[modalId] = null;
    _allSkillsCache.loaded = false;
    loadAllSkills();
    setTimeout(() => closeModalById(modalId), 800);
  } catch(e) {
    _siStatus(modalId, 'Install failed: ' + e.message, 'var(--red)');
  }
}

function _toggleSkillsImportMenu(event) {
  event.stopPropagation();
  const menu = document.getElementById('skills-import-menu');
  if (!menu) return;
  const willOpen = menu.style.display === 'none';
  menu.style.display = willOpen ? 'block' : 'none';
  if (willOpen) {
    setTimeout(() => {
      const closeOnce = (e) => {
        if (!document.getElementById('skills-import-wrapper')?.contains(e.target)) {
          menu.style.display = 'none';
          document.removeEventListener('click', closeOnce);
        }
      };
      document.addEventListener('click', closeOnce);
    }, 0);
  }
}

function _hideSkillsImportMenu() {
  const menu = document.getElementById('skills-import-menu');
  if (menu) menu.style.display = 'none';
}

function _defaultImportContext() {
  // If the user is viewing a specific project's skills, default the import there.
  const pid = (_allSkillsFilter.project && _allSkillsFilter.project !== 'all') ? _allSkillsFilter.project : '';
  return {
    scope: pid ? 'project' : 'global',
    projectId: pid || null,
  };
}

function _importContextHTML(modalId, ctx) {
  const realProjects = (allProjects || []).filter(p => !isIncognitoProject(p) && p.project_path);
  return `
    <div style="display:flex;gap:12px;align-items:center">
      <label style="font-size:12px;color:var(--text);display:flex;gap:4px;align-items:center;cursor:pointer">
        <input type="radio" name="si-scope-${modalId}" value="global" ${ctx.scope==='global'?'checked':''}
          onchange="_siToggleProject('${modalId}')"> Global
      </label>
      <label style="font-size:12px;color:var(--text);display:flex;gap:4px;align-items:center;cursor:pointer">
        <input type="radio" name="si-scope-${modalId}" value="project" ${ctx.scope==='project'?'checked':''}
          onchange="_siToggleProject('${modalId}')"> Project
      </label>
      <select id="si-project-${modalId}" style="padding:5px 8px;font-size:12px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text);${ctx.scope==='project'?'':'display:none'}">
        <option value="">— pick a project —</option>
        ${realProjects.map(p =>
          `<option value="${esc(p.id)}"${ctx.projectId===p.id?' selected':''}>${esc(p.name||p.id)}</option>`
        ).join('')}
      </select>
    </div>`;
}

function _siToggleProject(modalId) {
  const win = openModals.get(modalId)?.element;
  if (!win) return;
  const radio = win.querySelector(`input[name="si-scope-${modalId}"]:checked`);
  const sel = win.querySelector(`#si-project-${modalId}`);
  if (sel) sel.style.display = (radio?.value === 'project') ? '' : 'none';
}

function _siReadContext(modalId) {
  const win = openModals.get(modalId)?.element;
  if (!win) return null;
  const radio = win.querySelector(`input[name="si-scope-${modalId}"]:checked`);
  const scope = radio?.value || 'global';
  const projectId = scope === 'project' ? (win.querySelector(`#si-project-${modalId}`)?.value || '') : '';
  if (scope === 'project' && !projectId) return { error: 'Pick a project for project-scoped import' };
  return { scope, projectId: projectId || null };
}

function _siModalShell(modalId, title, bodyHTML, footerHTML) {
  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content';
  _clampModalSize(content, 720);
  content.innerHTML = `
    <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:16px 24px 12px 28px">
      <span style="font-size:16px;font-weight:700;color:var(--text)">&#x1F9E9; ${esc(title)}</span>
      <div class="modal-window-controls" style="position:static;display:flex;gap:4px">
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
    </div>
    <div style="padding:4px 24px 20px 28px;display:flex;flex-direction:column;gap:12px">
      ${bodyHTML}
      <div style="display:flex;gap:8px;align-items:center;justify-content:flex-end">
        <span id="si-status-${modalId}" style="font-size:11px;color:var(--text-faint);margin-right:auto"></span>
        ${footerHTML}
      </div>
    </div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);
  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);
  return win;
}

function _siStatus(modalId, msg, color) {
  const el = document.querySelector(`#si-status-${modalId}`);
  if (el) {
    el.textContent = msg || '';
    el.style.color = color || 'var(--text-faint)';
  }
}

// ── Paste SKILL.md ───────────────────────────────────────────────────────────

function openSkillImportPaste() {
  _hideSkillsImportMenu();
  const modalId = '__skill_import_paste';
  if (openModals.has(modalId)) { focusModal(modalId); return; }
  const ctx = _defaultImportContext();
  const body = `
    <div style="font-size:11px;color:var(--text-faint)">Paste the full SKILL.md content (frontmatter + body). The skill name comes from the <code>name:</code> field, or you can override it below.</div>
    ${_importContextHTML(modalId, ctx)}
    <div>
      <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">Override name (optional)</label>
      <input type="text" id="sip-name" placeholder="leave blank to use frontmatter name"
        style="width:100%;padding:6px 10px;font-size:13px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono)">
    </div>
    <div>
      <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">SKILL.md content</label>
      <textarea id="sip-content" rows="18" placeholder="---&#10;name: my-skill&#10;description: ...&#10;---&#10;&#10;# body markdown"
        style="width:100%;padding:8px 10px;font-size:12px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text);resize:vertical;font-family:var(--mono);line-height:1.5"></textarea>
    </div>`;
  const footer = `
    <button class="btn-secondary" onclick="closeModalById('${modalId}')">Cancel</button>
    <button class="btn-add" onclick="_doSkillImportPaste('${modalId}')">Import</button>`;
  _siModalShell(modalId, 'Paste SKILL.md', body, footer);
}

async function _doSkillImportPaste(modalId) {
  const ctx = _siReadContext(modalId);
  if (!ctx || ctx.error) { _siStatus(modalId, ctx?.error || 'invalid context', 'var(--red)'); return; }
  const win = openModals.get(modalId).element;
  const content = win.querySelector('#sip-content')?.value || '';
  const name = (win.querySelector('#sip-name')?.value || '').trim();
  if (!content.trim()) { _siStatus(modalId, 'paste content is empty', 'var(--red)'); return; }
  _siStatus(modalId, 'Importing...');
  try {
    const res = await fetch(API_BASE + '/api/skills/import/paste', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content, scope: ctx.scope, project_id: ctx.projectId, name: name || undefined }),
    });
    const data = await res.json();
    if (!res.ok || data.error) { _siStatus(modalId, data.error || 'HTTP ' + res.status, 'var(--red)'); return; }
    _siStatus(modalId, 'Imported', 'var(--green)');
    _allSkillsCache.loaded = false;
    loadAllSkills();
    setTimeout(() => closeModalById(modalId), 500);
  } catch(e) {
    _siStatus(modalId, 'Import failed: ' + e.message, 'var(--red)');
  }
}

// ── Import from folder ───────────────────────────────────────────────────────

function openSkillImportFolder() {
  _hideSkillsImportMenu();
  const modalId = '__skill_import_folder';
  if (openModals.has(modalId)) { focusModal(modalId); return; }
  const ctx = _defaultImportContext();
  const body = `
    <div style="font-size:11px;color:var(--text-faint)">Path to a folder containing <code>SKILL.md</code>, or a parent folder with skill subfolders. If multiple skills are found you'll pick one.</div>
    ${_importContextHTML(modalId, ctx)}
    <div>
      <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">Folder path</label>
      <input type="text" id="sif-path" placeholder="/path/to/skill or C:\\path\\to\\skill"
        style="width:100%;padding:6px 10px;font-size:13px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono)">
    </div>
    <div>
      <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">Override name (optional)</label>
      <input type="text" id="sif-name" placeholder="leave blank to use folder name / frontmatter"
        style="width:100%;padding:6px 10px;font-size:13px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono)">
    </div>
    <div id="sif-candidates" style="display:none"></div>`;
  const footer = `
    <button class="btn-secondary" onclick="closeModalById('${modalId}')">Cancel</button>
    <button class="btn-add" onclick="_doSkillImportFolder('${modalId}')">Import</button>`;
  _siModalShell(modalId, 'Import from folder', body, footer);
}

async function _doSkillImportFolder(modalId, selectedRelDir) {
  const ctx = _siReadContext(modalId);
  if (!ctx || ctx.error) { _siStatus(modalId, ctx?.error || 'invalid context', 'var(--red)'); return; }
  const win = openModals.get(modalId).element;
  const path = (win.querySelector('#sif-path')?.value || '').trim();
  const name = (win.querySelector('#sif-name')?.value || '').trim();
  if (!path) { _siStatus(modalId, 'path is required', 'var(--red)'); return; }
  _siStatus(modalId, 'Importing...');
  try {
    const res = await fetch(API_BASE + '/api/skills/import/folder', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        path, scope: ctx.scope, project_id: ctx.projectId,
        name: name || undefined, selected_rel_dir: selectedRelDir,
      }),
    });
    const data = await res.json();
    if (!res.ok || data.error) { _siStatus(modalId, data.error || 'HTTP ' + res.status, 'var(--red)'); return; }
    if (data.multiple) {
      // Show candidate picker (+ optional plugin banner)
      win_importPluginSource[modalId] = data.plugin
        ? { source: 'folder', path: path }
        : null;
      const wrap = win.querySelector('#sif-candidates');
      wrap.style.display = '';
      const pluginBanner = _renderPluginBanner(data.plugin);
      const fullPluginBtn = data.plugin ? _renderFullPluginButton(modalId) : '';
      const skillLabel = data.plugin
        ? `Or install just one of the ${data.candidates.length} skill${data.candidates.length===1?'':'s'} the plugin contains:`
        : 'Multiple skills found — pick one to install:';
      wrap.innerHTML = `
        ${pluginBanner}
        ${fullPluginBtn}
        <div style="font-size:12px;color:var(--text);margin-bottom:6px">${skillLabel}</div>
        ${(data.candidates || []).map(c => `
          <div style="display:flex;align-items:center;gap:8px;padding:6px 8px;border:1px solid var(--border);border-radius:4px;margin-bottom:4px">
            <div style="flex:1">
              <div style="font-size:13px;font-weight:600">${esc(c.name)}</div>
              <div style="font-size:11px;color:var(--text-faint);font-family:var(--mono)">${esc(c.rel_dir || '(root)')}</div>
              <div style="font-size:11px;color:var(--text-mute);margin-top:2px">${esc(c.description || '')}</div>
            </div>
            <button class="btn-tiny" onclick="_doSkillImportFolder('${modalId}', '${esc(c.rel_dir).replace(/'/g, "\\'")}')">Install this</button>
          </div>
        `).join('')}`;
      _siStatus(modalId, data.plugin ? 'Plugin detected. Choose what to install.' : 'Pick one of the candidates above', 'var(--text-faint)');
      return;
    }
    _siStatus(modalId, 'Imported', 'var(--green)');
    _allSkillsCache.loaded = false;
    loadAllSkills();
    setTimeout(() => closeModalById(modalId), 500);
  } catch(e) {
    _siStatus(modalId, 'Import failed: ' + e.message, 'var(--red)');
  }
}

// ── Import from Git URL ──────────────────────────────────────────────────────

function openSkillImportGit() {
  _hideSkillsImportMenu();
  const modalId = '__skill_import_git';
  if (openModals.has(modalId)) { focusModal(modalId); return; }
  const ctx = _defaultImportContext();
  const body = `
    <div style="font-size:11px;color:var(--text-faint)">Public Git URL. Accepts bare repo URLs <em>and</em> GitHub web URLs pointing at a subfolder (<code>github.com/owner/repo/tree/main/path/to/skill</code>) — Clayrune extracts the repo + ref + path. HTTPS recommended; private repos require system-git auth.</div>
    ${_importContextHTML(modalId, ctx)}
    <div>
      <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">Git URL</label>
      <input type="text" id="sig-url" placeholder="https://github.com/owner/repo.git"
        style="width:100%;padding:6px 10px;font-size:13px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono)">
    </div>
    <div>
      <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">Branch / tag (optional)</label>
      <input type="text" id="sig-ref" placeholder="main"
        style="width:100%;padding:6px 10px;font-size:13px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono)">
    </div>
    <div>
      <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">Override name (optional, single-skill repos only)</label>
      <input type="text" id="sig-name" placeholder="leave blank to use frontmatter name"
        style="width:100%;padding:6px 10px;font-size:13px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono)">
    </div>
    <div id="sig-candidates" style="display:none"></div>`;
  const footer = `
    <button class="btn-secondary" id="sig-cancel-${modalId}" onclick="_doSkillImportGitCancel('${modalId}')">Cancel</button>
    <button class="btn-add" id="sig-import-${modalId}" onclick="_doSkillImportGit('${modalId}')">Clone &amp; import</button>`;
  _siModalShell(modalId, 'Import from Git URL', body, footer);
  win_gitStaging[modalId] = null;
}

async function _doSkillImportGit(modalId) {
  const ctx = _siReadContext(modalId);
  if (!ctx || ctx.error) { _siStatus(modalId, ctx?.error || 'invalid context', 'var(--red)'); return; }
  const win = openModals.get(modalId).element;
  const url = (win.querySelector('#sig-url')?.value || '').trim();
  const ref = (win.querySelector('#sig-ref')?.value || '').trim();
  const name = (win.querySelector('#sig-name')?.value || '').trim();
  if (!url) { _siStatus(modalId, 'URL is required', 'var(--red)'); return; }
  _siStatus(modalId, 'Cloning...');
  try {
    const res = await fetch(API_BASE + '/api/skills/import/git', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        url, ref: ref || undefined, scope: ctx.scope, project_id: ctx.projectId,
        name: name || undefined,
      }),
    });
    const data = await res.json();
    if (!res.ok && !data.candidates) {
      _siStatus(modalId, data.error || 'HTTP ' + res.status, 'var(--red)');
      return;
    }
    if (data.installed) {
      _siStatus(modalId, 'Imported ' + (data.installed.name || ''), 'var(--green)');
      _allSkillsCache.loaded = false;
      loadAllSkills();
      setTimeout(() => closeModalById(modalId), 600);
      return;
    }
    // Multi-skill repo OR plugin detected: show picker (+ optional plugin banner)
    win_gitStaging[modalId] = data.staging_id;
    win_importPluginSource[modalId] = data.plugin
      ? { source: 'git', staging_id: data.staging_id }
      : null;
    const wrap = win.querySelector('#sig-candidates');
    wrap.style.display = '';
    const pluginBanner = _renderPluginBanner(data.plugin);
    const fullPluginBtn = data.plugin ? _renderFullPluginButton(modalId) : '';
    const skillLabel = data.plugin
      ? `Or install just one of the ${data.candidates.length} skill${data.candidates.length===1?'':'s'} the plugin contains:`
      : `${(data.candidates || []).length} skills found in the repo — pick one:`;
    wrap.innerHTML = `
      ${pluginBanner}
      ${fullPluginBtn}
      <div style="font-size:12px;color:var(--text);margin-bottom:6px">${skillLabel}</div>
      ${(data.candidates || []).map(c => `
        <div style="display:flex;align-items:center;gap:8px;padding:6px 8px;border:1px solid var(--border);border-radius:4px;margin-bottom:4px">
          <div style="flex:1">
            <div style="font-size:13px;font-weight:600">${esc(c.name)}</div>
            <div style="font-size:11px;color:var(--text-faint);font-family:var(--mono)">${esc(c.rel_dir || '(root)')}</div>
            <div style="font-size:11px;color:var(--text-mute);margin-top:2px">${esc(c.description || '')}</div>
          </div>
          <button class="btn-tiny" onclick="_doSkillImportGitInstallOne('${modalId}', '${esc(c.rel_dir).replace(/'/g, "\\'")}', '${esc(c.name).replace(/'/g, "\\'")}')">Install this</button>
        </div>
      `).join('')}`;
    _siStatus(modalId, data.plugin ? 'Plugin cloned. Choose what to install.' : 'Repo cloned. Pick a skill to install.', 'var(--text-faint)');
  } catch(e) {
    _siStatus(modalId, 'Import failed: ' + e.message, 'var(--red)');
  }
}

async function _doSkillImportGitInstallOne(modalId, relDir, defaultName) {
  const ctx = _siReadContext(modalId);
  if (!ctx || ctx.error) { _siStatus(modalId, ctx?.error || 'invalid context', 'var(--red)'); return; }
  const stagingId = win_gitStaging[modalId];
  if (!stagingId) { _siStatus(modalId, 'staging session expired — re-clone', 'var(--red)'); return; }
  _siStatus(modalId, 'Installing...');
  try {
    const res = await fetch(API_BASE + '/api/skills/import/git/install', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        staging_id: stagingId,
        rel_dir: relDir,
        scope: ctx.scope, project_id: ctx.projectId,
        cleanup: true,
      }),
    });
    const data = await res.json();
    if (!res.ok || data.error) { _siStatus(modalId, data.error || 'HTTP ' + res.status, 'var(--red)'); return; }
    _siStatus(modalId, 'Imported ' + (data.name || defaultName), 'var(--green)');
    win_gitStaging[modalId] = null;
    _allSkillsCache.loaded = false;
    loadAllSkills();
    setTimeout(() => closeModalById(modalId), 500);
  } catch(e) {
    _siStatus(modalId, 'Install failed: ' + e.message, 'var(--red)');
  }
}

async function _doSkillImportGitCancel(modalId) {
  const stagingId = win_gitStaging[modalId];
  if (stagingId) {
    try {
      await fetch(API_BASE + '/api/skills/import/git/cancel', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ staging_id: stagingId }),
      });
    } catch(e) {}
    win_gitStaging[modalId] = null;
  }
  closeModalById(modalId);
}

// ── Browse skills from other projects (cross-project copy) ───────────────────

function openSkillImportBrowse() {
  _hideSkillsImportMenu();
  const modalId = '__skill_import_browse';
  if (openModals.has(modalId)) { focusModal(modalId); return; }
  const ctx = _defaultImportContext();
  const realProjects = (allProjects || []).filter(p => !isIncognitoProject(p) && p.project_path);
  // If the active skills filter is on a specific project, default the SOURCE to that project too.
  const defaultSource = (_allSkillsFilter.project && _allSkillsFilter.project !== 'all')
    ? `project:${_allSkillsFilter.project}` : 'all';
  const body = `
    <div style="font-size:11px;color:var(--text-faint)">Pick a source to browse, pick a destination to install into, then click <strong>Install →</strong> on any row.</div>

    <div style="display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end;padding:10px;background:var(--surface2);border:1px solid var(--border);border-radius:4px">
      <div style="flex:1;min-width:200px">
        <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px;font-weight:600">1. Browse from (source)</label>
        <select id="sib-source" onchange="_doSkillImportBrowseSearch('${modalId}')"
          style="width:100%;padding:5px 8px;font-size:12px;background:var(--surface1);border:1px solid var(--border);border-radius:4px;color:var(--text)">
          <option value="all"${defaultSource==='all'?' selected':''}>All sources (search required)</option>
          <option value="global"${defaultSource==='global'?' selected':''}>Global only</option>
          ${realProjects.map(p => {
            const v = `project:${esc(p.id)}`;
            return `<option value="${v}"${defaultSource===v?' selected':''}>${esc(p.name||p.id)} (project)</option>`;
          }).join('')}
        </select>
      </div>
      <div style="flex:2;min-width:240px">
        <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px;font-weight:600">2. Filter / search</label>
        <input type="text" id="sib-query" placeholder="leave empty to list all skills in source"
          oninput="clearTimeout(window._sibTimer); window._sibTimer = setTimeout(() => _doSkillImportBrowseSearch('${modalId}'), 250);"
          style="width:100%;padding:6px 10px;font-size:13px;background:var(--surface1);border:1px solid var(--border);border-radius:4px;color:var(--text)">
      </div>
    </div>

    <div style="padding:10px;background:var(--surface2);border:1px solid var(--border);border-radius:4px">
      <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:6px;font-weight:600">3. Install into (destination)</label>
      ${_importContextHTML(modalId, ctx)}
    </div>

    <div style="font-size:11px;color:var(--text);font-weight:600">4. Click <span style="color:var(--accent)">Install →</span> on the skill you want</div>
    <div id="sib-results" style="max-height:46vh;overflow-y:auto"></div>`;
  const footer = `
    <button class="btn-secondary" onclick="closeModalById('${modalId}')">Close</button>`;
  _siModalShell(modalId, 'Browse skills', body, footer);
  // Auto-load if a specific source is preselected (e.g. user was viewing a project filter).
  if (defaultSource !== 'all') _doSkillImportBrowseSearch(modalId);
}

async function _doSkillImportBrowseSearch(modalId) {
  const win = openModals.get(modalId)?.element;
  if (!win) return;
  const q = (win.querySelector('#sib-query')?.value || '').trim();
  const source = win.querySelector('#sib-source')?.value || 'all';
  const resultsEl = win.querySelector('#sib-results');

  // "All sources" still requires a query (otherwise too noisy); specific sources can browse-all.
  if (source === 'all' && !q) {
    resultsEl.innerHTML = '<div style="padding:24px;text-align:center;color:var(--text-faint);font-size:12px">Type to search, or pick a specific source above to browse it.</div>';
    return;
  }
  resultsEl.innerHTML = '<div style="padding:24px;text-align:center;color:var(--text-faint);font-size:12px">Loading...</div>';

  const qLower = q.toLowerCase();
  let all = [];
  try {
    if (source === 'global') {
      const r = await fetch(API_BASE + '/api/skills');
      const arr = await r.json();
      all = arr.filter(s => s.scope === 'global');
      if (q) all = all.filter(s => ((s.name||'') + ' ' + (s.description||'')).toLowerCase().includes(qLower));
    } else if (source.startsWith('project:')) {
      const pid = source.slice('project:'.length);
      const r = await fetch(API_BASE + '/api/skills?project_id=' + encodeURIComponent(pid));
      const arr = await r.json();
      // Show this project's own skills first; globals appear underneath so the user knows what's also available here.
      all = arr.filter(s => s.scope === 'project' || s.scope === 'global');
      if (q) all = all.filter(s => ((s.name||'') + ' ' + (s.description||'')).toLowerCase().includes(qLower));
      all.sort((a, b) => (a.scope === 'project' ? 0 : 1) - (b.scope === 'project' ? 0 : 1)
        || (a.name||'').localeCompare(b.name||''));
    } else {
      // "All sources" search: keyword-rank across global + every project pool
      const realProjects = (allProjects || []).filter(p => !isIncognitoProject(p) && p.project_path);
      const seen = new Set();
      const r0 = await fetch(API_BASE + '/api/skills/search?q=' + encodeURIComponent(q) + '&limit=20');
      const arr0 = await r0.json();
      for (const s of arr0) {
        const k = s.scope + ':' + s.name + ':' + (s.project_id || '');
        if (!seen.has(k)) { seen.add(k); all.push(s); }
      }
      await Promise.all(realProjects.map(async p => {
        try {
          const r = await fetch(API_BASE + '/api/skills/search?q=' + encodeURIComponent(q) + '&project_id=' + encodeURIComponent(p.id) + '&limit=10');
          const arr = await r.json();
          for (const s of arr) {
            const k = s.scope + ':' + s.name + ':' + (s.project_id || '');
            if (!seen.has(k)) { seen.add(k); all.push(s); }
          }
        } catch(e) {}
      }));
      all.sort((a, b) => (b.score || 0) - (a.score || 0));
    }
  } catch(e) {
    resultsEl.innerHTML = `<div style="padding:24px;text-align:center;color:var(--red);font-size:12px">Search failed: ${esc(e.message)}</div>`;
    return;
  }

  if (!all.length) {
    resultsEl.innerHTML = '<div style="padding:24px;text-align:center;color:var(--text-faint);font-size:12px">No skills found.</div>';
    return;
  }

  resultsEl.innerHTML = all.map((s, i) => {
    const projLabel = s.scope === 'project'
      ? ((allProjects || []).find(p => p.id === s.project_id)?.name || s.project_id || 'project')
      : (s.scope === 'archive' ? 'archive' : 'global');
    const pidArg = s.project_id ? `'${esc(s.project_id)}'` : 'null';
    const idAttr = `sib-row-${i}`;
    const scoreLabel = (typeof s.score === 'number' && s.score > 0) ? `score ${s.score}` : '';
    return `<div id="${idAttr}" style="padding:8px 10px;border:1px solid var(--border);border-radius:4px;margin-bottom:6px">
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
        <span style="font-size:13px;font-weight:600">${esc(s.name)}</span>
        <span style="font-size:10px;background:var(--surface2);color:var(--text-faint);padding:1px 6px;border-radius:3px">${esc(projLabel)}</span>
        ${scoreLabel ? `<span style="font-size:10px;color:var(--text-faint);margin-left:auto">${esc(scoreLabel)}</span>` : ''}
      </div>
      <div style="font-size:12px;color:var(--text-mute);margin-top:4px;line-height:1.4">${esc(s.description || '')}</div>
      <div style="display:flex;gap:6px;margin-top:6px;align-items:center">
        <button class="btn-tiny" onclick="_sibReadBody('${esc(s.scope)}', '${esc(s.name)}', ${pidArg}, '${idAttr}')">Read body</button>
        <button class="btn-add" style="padding:4px 10px;font-size:11px" onclick="_sibInstallHere('${modalId}', '${esc(s.scope)}', '${esc(s.name)}', ${pidArg})">Install &rarr;</button>
      </div>
      <div id="${idAttr}-body" style="display:none;margin-top:8px;padding:8px;background:var(--surface2);border-radius:4px;font-family:var(--mono);font-size:11px;white-space:pre-wrap;max-height:240px;overflow-y:auto"></div>
    </div>`;
  }).join('');
}

async function _sibReadBody(scope, name, projectId, rowId) {
  const bodyEl = document.getElementById(rowId + '-body');
  if (!bodyEl) return;
  if (bodyEl.style.display !== 'none') { bodyEl.style.display = 'none'; return; }
  bodyEl.style.display = '';
  bodyEl.textContent = 'Loading...';
  try {
    const params = new URLSearchParams({ include_body: 'true' });
    if (projectId) params.set('project_id', projectId);
    const r = await fetch(API_BASE + `/api/skills/${encodeURIComponent(scope)}/${encodeURIComponent(name)}?` + params.toString());
    const data = await r.json();
    if (!r.ok || data.error) { bodyEl.textContent = data.error || 'Failed'; return; }
    bodyEl.textContent = data.body || '(empty body)';
  } catch(e) { bodyEl.textContent = 'Load failed: ' + e.message; }
}

async function _sibInstallHere(modalId, srcScope, srcName, srcProjectId) {
  const ctx = _siReadContext(modalId);
  if (!ctx || ctx.error) { _siStatus(modalId, ctx?.error || 'invalid context', 'var(--red)'); return; }
  // Read full source skill
  _siStatus(modalId, 'Fetching source...');
  try {
    const params = new URLSearchParams({ include_body: 'true' });
    if (srcProjectId) params.set('project_id', srcProjectId);
    const rGet = await fetch(API_BASE + `/api/skills/${encodeURIComponent(srcScope)}/${encodeURIComponent(srcName)}?` + params.toString());
    const src = await rGet.json();
    if (!rGet.ok || src.error) { _siStatus(modalId, src.error || 'fetch failed', 'var(--red)'); return; }

    // POST as new skill in destination
    _siStatus(modalId, 'Installing...');
    const rPost = await fetch(API_BASE + '/api/skills', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: src.name,
        description: src.description,
        body: src.body || '',
        scope: ctx.scope,
        project_id: ctx.projectId,
      }),
    });
    const data = await rPost.json();
    if (!rPost.ok || data.error) { _siStatus(modalId, data.error || 'install failed', 'var(--red)'); return; }
    _siStatus(modalId, 'Installed', 'var(--green)');
    _allSkillsCache.loaded = false;
    loadAllSkills();
  } catch(e) {
    _siStatus(modalId, 'Install failed: ' + e.message, 'var(--red)');
  }
}


// ── interop: page-called surface ─────────────────────────────────────────────
// Everything below is invoked from OUTSIDE this module — the inline script
// (sidebarNav) and static/generated inline event attributes, all of which
// resolve against the global object, never module scope.
window.openAllSkills = openAllSkills;                       // interop: sidebarNav('skills') in the inline script
window.openAllSkillsForProject = openAllSkillsForProject;   // interop: project-modal three-dot menu (generated onclick)
window.loadAllSkills = loadAllSkills;                       // interop: generated onchange (project filter select, include-archived checkbox)
window.renderAllSkills = renderAllSkills;                   // interop: generated oninput/onchange (search box, scope select)
window.loadDistillerQueue = loadDistillerQueue;             // interop: generated onclick (Learning-queue + Health-diagnostics disclosure toggles)
window.promoteProposedByIdx = promoteProposedByIdx;         // interop: queue-row generated onclick (Promote → Project / Global)
window.rejectProposedByIdx = rejectProposedByIdx;           // interop: queue-row generated onclick (Reject)
window.toggleProposedReadByIdx = toggleProposedReadByIdx;   // interop: queue-row generated onclick (read full)
window.openSkillEditor = openSkillEditor;                   // interop: generated onclick (+ New Skill, per-row Edit)
window._seToggleProjectPicker = _seToggleProjectPicker;     // interop: editor scope radios (generated onchange)
window._seLintDescription = _seLintDescription;             // interop: editor description textarea (generated oninput)
window.saveSkillFromEditor = saveSkillFromEditor;           // interop: editor Create/Save (generated onclick)
window.archiveSkillAction = archiveSkillAction;             // interop: row Archive/Delete (generated onclick)
window.deleteSkillAction = deleteSkillAction;               // interop: archived-row Delete (generated onclick)
window.restoreSkillAction = restoreSkillAction;             // interop: archived-row Restore (generated onclick)
window._toggleSkillsImportMenu = _toggleSkillsImportMenu;   // interop: Import ▾ button (generated onclick)
window.openSkillImportPaste = openSkillImportPaste;         // interop: import menu item (generated onclick)
window.openSkillImportFolder = openSkillImportFolder;       // interop: import menu item (generated onclick)
window.openSkillImportGit = openSkillImportGit;             // interop: import menu item (generated onclick)
window.openSkillImportBrowse = openSkillImportBrowse;       // interop: import menu item (generated onclick)
window._doSkillImportPaste = _doSkillImportPaste;           // interop: paste-modal Import (generated onclick)
window._doSkillImportFolder = _doSkillImportFolder;         // interop: folder-modal Import + candidate "Install this" (generated onclick)
window._doSkillImportGit = _doSkillImportGit;               // interop: git-modal "Clone & import" (generated onclick)
window._doSkillImportGitInstallOne = _doSkillImportGitInstallOne; // interop: git candidate "Install this" (generated onclick)
window._doSkillImportGitCancel = _doSkillImportGitCancel;   // interop: git-modal Cancel (generated onclick)
window._doSkillImportFullPlugin = _doSkillImportFullPlugin; // interop: "Install full plugin" (generated onclick)
window._siToggleProject = _siToggleProject;                 // interop: import-context scope radios (generated onchange)
window._doSkillImportBrowseSearch = _doSkillImportBrowseSearch; // interop: browse source select (onchange) + debounced query (oninput)
window._sibReadBody = _sibReadBody;                         // interop: browse-row "Read body" (generated onclick)
window._sibInstallHere = _sibInstallHere;                   // interop: browse-row "Install →" (generated onclick)
// interop: the Learning-queue and Health-diagnostics disclosure headers write
// `_distillerQueueOpen=!_distillerQueueOpen` / `_distillerDiagOpen=!_distillerDiagOpen`
// from generated onclick attributes. Inline handlers resolve against the global
// object and can't see module-scoped `let` bindings — a plain tail assignment
// would copy the value once and silently diverge. The accessors route
// window-property reads/writes to the module bindings (one source of truth;
// the moved code itself stays byte-verbatim).
Object.defineProperty(window, '_distillerQueueOpen', {
  get() { return _distillerQueueOpen; },
  set(v) { _distillerQueueOpen = v; },
});
Object.defineProperty(window, '_distillerDiagOpen', {
  get() { return _distillerDiagOpen; },
  set(v) { _distillerDiagOpen = v; },
});
// interop: the filter bar's generated oninput/onchange handlers property-write
// through the binding (`_allSkillsFilter.search=this.value` etc.), so the
// identifier must resolve on the global object. Accessor (not a copy) so the
// window property always reflects the live module binding.
Object.defineProperty(window, '_allSkillsFilter', {
  get() { return _allSkillsFilter; },
  set(v) { _allSkillsFilter = v; },
});
