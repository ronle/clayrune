// ── Media gallery ── diagrams + images the agent produced, per project ───────
//
// Backed by mc/media.py: media is INDEXED as it streams past, not scanned on
// open (one project's transcripts are 155 MB — a live scan took >100s and is
// not a request-time operation).
//
// FORWARD-ONLY: there is no backfill of history, so the gallery starts empty
// and fills as you work. The empty state says so — otherwise a blank grid on a
// project with months of history reads as "broken", which is worse than the
// honest limitation.
//
// Rendering is entirely borrowed: images go to _openImageViewer (zoom/pan/save)
// and diagrams to the same mermaid pipeline the chat uses
// (_mermaidPlaceholderHTML + _renderAllMermaidPlaceholders), so a diagram in
// the gallery is pixel-identical to the one in the transcript and gets the
// diagram viewer for free.

let _mediaCache = { items: [], loaded: false, loading: false, projectId: null };
let _mediaFilter = 'all';   // all | diagram | image

function openMediaSurface(projectId) {
  const pid = projectId || _mediaCache.projectId
    || (typeof allProjects !== 'undefined' && allProjects[0] && allProjects[0].id) || null;
  if (!pid) { try { showToast('No project to show media for.'); } catch (_) {} return; }
  if (pid !== _mediaCache.projectId) {
    _mediaCache = { items: [], loaded: false, loading: false, projectId: pid };
  }
  const modalId = '__media';
  if (openModals.has(modalId)) {
    const entry = openModals.get(modalId);
    if (entry.minimized) restoreModal(modalId);
    focusModal(modalId);
    _mediaRenderBody();
    loadMedia();
    return;
  }
  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content';
  _clampModalSize(content, 1000);
  content.innerHTML = `
    <div class="modal-header" style="display:flex;align-items:center;gap:12px;padding:14px 24px 0 28px">
      <span style="font-size:16px;font-weight:700;color:var(--text)">&#x1F5BC; Media</span>
      <div class="modal-window-controls" style="position:static;display:flex;gap:4px;margin-left:auto">
        <button class="modal-minimize" onclick="minimizeModal('${modalId}')" title="Minimize">&#x2015;</button>
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
    </div>
    <div id="media-body"></div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);
  const z = nextModalZ++;
  win.style.zIndex = z;
  // projectId MUST be null. refreshModal() re-renders every open modal whose
  // entry carries a projectId AS A PROJECT MODAL — so setting it here made the
  // gallery repaint itself as a second copy of the project on the next refresh,
  // and its X then called closeModalById(<project-id>) instead of '__media', so
  // the window couldn't even be closed. The media project lives in _mediaCache,
  // not in the modal entry. (Extensions/Skills pass null for the same reason.)
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);
  _mediaRenderBody();
  loadMedia();
}

function _mediaRenderBody() {
  const body = document.getElementById('media-body');
  if (!body) return;
  const projects = (typeof allProjects !== 'undefined' ? allProjects : [])
    .filter(p => typeof isIncognitoProject !== 'function' || !isIncognitoProject(p));
  const cur = _mediaCache.projectId;
  body.innerHTML = `
    <div style="padding:4px 24px 20px 28px">
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:12px;flex-wrap:wrap">
        <select id="media-project" style="padding:6px 8px;font-size:12px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text);max-width:220px"
          onchange="setMediaProject(this.value)">
          ${projects.map(p => `<option value="${esc(p.id)}"${p.id===cur?' selected':''}>${esc(p.name||p.id)}</option>`).join('')}
        </select>
        <div class="mc-seg media-seg">
          <button class="${_mediaFilter==='all'?'active':''}" onclick="setMediaFilter('all')">All</button>
          <button class="${_mediaFilter==='diagram'?'active':''}" onclick="setMediaFilter('diagram')">Diagrams</button>
          <button class="${_mediaFilter==='image'?'active':''}" onclick="setMediaFilter('image')">Images</button>
        </div>
        <span id="media-count" style="font-size:11px;color:var(--text-faint)"></span>
      </div>
      <div id="media-grid"></div>
    </div>`;
  renderMedia();
}

function setMediaProject(pid) {
  // Deliberately does NOT write entry.projectId — see the note in
  // openMediaSurface: a projectId on the modal entry turns this window into a
  // project modal on the next refreshModal().
  _mediaCache = { items: [], loaded: false, loading: false, projectId: pid };
  _mediaRenderBody();
  loadMedia();
}

function setMediaFilter(f) {
  _mediaFilter = f;
  _mediaRenderBody();
}

async function loadMedia() {
  const pid = _mediaCache.projectId;
  if (!pid || _mediaCache.loading) return;
  _mediaCache.loading = true;
  try {
    const res = await fetch(API_BASE + `/api/project/${encodeURIComponent(pid)}/media`);
    const data = await res.json();
    _mediaCache = {
      items: Array.isArray(data.items) ? data.items : [],
      loaded: true, loading: false, projectId: pid,
    };
  } catch (e) {
    _mediaCache = { items: [], loaded: true, loading: false, projectId: pid };
  }
  renderMedia();
}

// Tiles reference items BY INDEX — a Windows path in an onclick string literal
// eats its backslashes (\U → U) and corrupts the path. Never serialize one.
let _mediaRows = [];

function renderMedia() {
  const grid = document.getElementById('media-grid');
  const countEl = document.getElementById('media-count');
  if (!grid) return;

  if (!_mediaCache.loaded) {
    grid.innerHTML = '<div style="padding:40px 12px;text-align:center;color:var(--text-faint);font-size:12px">Loading media…</div>';
    if (countEl) countEl.textContent = '';
    return;
  }

  const rows = _mediaCache.items.filter(m => _mediaFilter === 'all' || m.kind === _mediaFilter);
  _mediaRows = rows;
  if (countEl) countEl.textContent = rows.length ? `${rows.length} item${rows.length===1?'':'s'}` : '';

  if (!rows.length) {
    // Say WHY it's empty. A blank grid on a project with months of history
    // otherwise reads as a bug rather than the forward-only design.
    grid.innerHTML = `<div style="padding:36px 16px;text-align:center;color:var(--text-faint);font-size:12px;line-height:1.6">
      ${_mediaCache.items.length
        ? 'Nothing matches that filter.'
        : 'No media yet.<br>Diagrams and images the agent produces from now on will collect here.<br>'
          + '<span style="color:var(--text-faint);opacity:.75">Existing history isn\'t indexed — the gallery starts from today.</span>'}
    </div>`;
    return;
  }

  grid.innerHTML = `<div class="media-grid">` + rows.map((m, i) => {
    const when = m.ts ? new Date(m.ts * 1000).toLocaleString() : '';
    const label = esc(m.task || '');
    if (m.kind === 'image') {
      const src = API_BASE + '/api/serve-image?path=' + encodeURIComponent(m.path);
      const name = String(m.path).split(/[\\/]/).pop();
      return `<div class="media-tile" onclick="_mediaOpen(${i})" title="${esc(m.path)}">
        <div class="media-thumb"><img src="${src}" alt="" loading="eager"
          onerror="this.closest('.media-tile').classList.add('media-dead')"></div>
        <div class="media-meta"><span class="media-name">${esc(name)}</span><span class="media-when">${esc(when)}</span></div>
        ${label ? `<div class="media-task">${label}</div>` : ''}
      </div>`;
    }
    // Diagram: render it for real, through the chat's own mermaid pipeline.
    return `<div class="media-tile media-tile-diagram">
      <div class="media-thumb media-thumb-diagram">${_mermaidPlaceholderHTML(m.source || '')}</div>
      <div class="media-meta"><span class="media-name">Diagram</span><span class="media-when">${esc(when)}</span></div>
      ${label ? `<div class="media-task">${label}</div>` : ''}
    </div>`;
  }).join('') + `</div>`;

  // Same call the chat makes after inserting placeholders — gives us the
  // rendered SVG and the click-to-open diagram viewer for free.
  if (typeof _renderAllMermaidPlaceholders === 'function') _renderAllMermaidPlaceholders();
}

function _mediaOpen(i) {
  const m = _mediaRows[i];
  if (!m || m.kind !== 'image') return;
  if (typeof _openImageViewer === 'function') {
    _openImageViewer(API_BASE + '/api/serve-image?path=' + encodeURIComponent(m.path));
  }
}

// interop: reached from sidebarNav()/the project menu (inline script) and from
// generated onclick/onchange handlers — must cross the ES-module boundary.
window.openMediaSurface = openMediaSurface;
window.loadMedia = loadMedia;
window.renderMedia = renderMedia;
window.setMediaFilter = setMediaFilter;
window.setMediaProject = setMediaProject;
window._mediaOpen = _mediaOpen;
