// ── Autonomous Stewards ────────────────────────────────────────────────────
// Fire-and-forget agents that set their own next steps. Lives in the Automation
// (scheduler) modal, above the raw schedule list. Backend: steward/ package +
// mc/blueprints/steward_routes.py. Module (type=module) → all cross-module
// entry points attach to window; shared globals (API_BASE, allProjects, esc,
// showToast, openProjectModal) resolve against window at call time.

let stewardFormOpen = false;

async function renderStewards() {
  const list = document.getElementById('steward-list');
  if (!list) return;
  list.innerHTML = '<div class="schedule-empty">Loading stewards…</div>';
  let health;
  try {
    const res = await fetch(API_BASE + '/api/steward/loop-health');
    health = await res.json();
  } catch (e) {
    list.innerHTML = '<div class="schedule-empty">Failed to load stewards.</div>';
    return;
  }
  const stewards = health.enabled || [];
  if (!stewards.length) {
    list.innerHTML = `<div class="schedule-empty">No stewards yet. A steward runs unattended over a
      project — it picks its own next step each cycle, does reversible work, and asks before
      anything irreversible. Click “+ New Steward”.</div>`;
    return;
  }
  list.innerHTML = stewards.map(s => {
    const pid = esc(s.project_id);
    const pend = s.decisions_pending || 0;
    const blk = s.blocked || 0;
    const cadence = s.cadence_minutes >= 60 && s.cadence_minutes % 60 === 0
      ? `every ${s.cadence_minutes / 60}h` : `every ${s.cadence_minutes}m`;
    const pendBadge = pend
      ? `<span class="steward-badge decide" title="Decisions awaiting your approval">${pend} to approve</span>` : '';
    const blkBadge = blk
      ? `<span class="steward-badge blocked" title="Blocked cycles">${blk} blocked</span>` : '';
    const fenceBadge = `<span class="steward-badge fence" title="Irreversible actions are hard-blocked; the steward must ask first">🛡 fenced</span>`;
    const scopeBadge = s.standalone
      ? `<span class="steward-badge" title="Operator-level — not tied to a codebase">🧭 standalone</span>` : '';
    const last = s.last_note_ts ? `last activity ${_stewardAgo(s.last_note_ts)}` : 'no activity yet';
    return `<div class="schedule-card-wrap"><div class="schedule-card${pend ? ' steward-attn' : ''}">
      <div class="schedule-card-body">
        <div class="schedule-card-project">${esc(s.project)}</div>
        <div class="schedule-card-task" title="${esc(s.objective)}">${esc(s.objective || '(no objective)')}</div>
        <div class="schedule-card-meta">
          <span>${cadence}</span>
          <span>${last}</span>
          ${fenceBadge}${scopeBadge}${pendBadge}${blkBadge}
        </div>
      </div>
      <div class="schedule-card-actions">
        <button class="btn-header-action" style="padding:3px 8px;font-size:11px;color:var(--accent);border-color:var(--accent)"
          onclick="stewardOpenChat('${pid}','${esc(s.claude_session_id || '')}')" title="Open the steward's conversation to read its work and reply">Open chat</button>
        <button class="btn-header-action" style="padding:3px 8px;font-size:11px"
          onclick="stewardOpenCharter('${pid}')" title="Open the project backlog to review the charter + decisions">Charter</button>
        <button class="btn-header-action" style="padding:3px 8px;font-size:11px"
          onclick="editSteward('${pid}')" title="Edit the objective (charter) and cadence">Edit</button>
        <button class="btn-header-action" style="padding:3px 8px;font-size:11px;color:var(--red-text);border-color:var(--red)"
          onclick="stewardDisable('${pid}')" title="Stop this steward (kill switch)">Stop</button>
      </div>
    </div></div>`;
  }).join('');
}

function _stewardAgo(iso) {
  try {
    const diff = (Date.now() - new Date(iso).getTime()) / 1000;
    if (diff < 60) return 'just now';
    if (diff < 3600) return `${Math.round(diff / 60)}m ago`;
    if (diff < 86400) return `${Math.round(diff / 3600)}h ago`;
    return `${Math.round(diff / 86400)}d ago`;
  } catch { return '—'; }
}

function stewardOpenCharter(pid) {
  if (window.openProjectModal) window.openProjectModal(pid);
}

// Open the steward's own conversation thread directly. The steward runs with
// trigger_type='schedule', so its chat is hidden from the conversation tab's
// user-initiated filter — but openConversation reconstructs by csid regardless,
// so this deep-links straight into the thread where you can read + reply.
async function stewardOpenChat(pid, csid) {
  if (window.openProjectModal) window.openProjectModal(pid);
  // Resolve the csid on demand if the card didn't carry one (e.g. no run yet).
  if (!csid) {
    try {
      const st = await fetch(`${API_BASE}/api/project/${encodeURIComponent(pid)}/steward`).then(r => r.json());
      if (st.schedule_id) {
        const runs = await fetch(`${API_BASE}/api/schedule/${encodeURIComponent(st.schedule_id)}/runs?limit=1`).then(r => r.json());
        csid = runs.runs && runs.runs[0] && runs.runs[0].claude_session_id;
      }
    } catch (e) { /* fall through */ }
  }
  if (!csid) {
    if (window.showToast) showToast('No steward conversation yet — it runs on its next cycle.', 4000);
    return;
  }
  // Let the project modal mount before opening the thread inside it.
  setTimeout(() => { if (window.openConversation) window.openConversation(pid, csid, '', false); }, 500);
}

let stewardScope = 'project';  // 'project' | 'standalone'

function _stewardScopeField(scope) {
  if (scope === 'standalone') {
    return `<label>Name <span class="memory-hint" style="margin:0;font-weight:normal">(what to call this operator-level steward)</span></label>
      <input type="text" id="steward-name" placeholder="e.g. Env Health, Weekly Research, Dependency Watch">`;
  }
  // NOTE: `allProjects` is a top-level `let` in a classic index.html script — a
  // bare-name global, NOT a window property (window.allProjects is undefined).
  // Read it by bare name like scheduler.js does; window.allProjects → empty list.
  // Exclude the incognito + standalone-steward pseudo-projects from the picker.
  const projects = (typeof allProjects !== 'undefined' ? allProjects : [])
    .filter(p => p.project_path && !p._is_incognito_project && p.id !== '_incognito'
                 && !p._is_steward_workspace && !String(p.id).startsWith('_steward_'));
  return `<label>Project <span class="memory-hint" style="margin:0;font-weight:normal">(the field of responsibility)</span></label>
    <select id="steward-project">${projects.map(p =>
      `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('')}</select>`;
}

function setStewardScope(scope) {
  stewardScope = scope;
  document.querySelectorAll('#steward-scope-row .sched-type-btn')
    .forEach(b => b.classList.toggle('active', b.dataset.scope === scope));
  const f = document.getElementById('steward-scope-field');
  if (f) f.innerHTML = _stewardScopeField(scope);
}

// null = creating a new steward; a project id = editing that steward in place.
let stewardEditPid = null;

function showStewardForm() {
  if (stewardFormOpen && !stewardEditPid) { hideStewardForm(); return; }
  stewardEditPid = null;
  stewardFormOpen = true;
  stewardScope = 'project';
  const area = document.getElementById('steward-form-area');
  if (!area) return;
  area.innerHTML = `<div class="schedule-form">
    <label>Scope</label>
    <div class="sched-type-row" id="steward-scope-row">
      <button class="sched-type-btn active" data-scope="project" onclick="setStewardScope('project')">A project</button>
      <button class="sched-type-btn" data-scope="standalone" onclick="setStewardScope('standalone')">Standalone</button>
    </div>
    <div id="steward-scope-field">${_stewardScopeField('project')}</div>
    ${_stewardObjectiveCadenceHTML('', 180)}
    <div class="sched-actions">
      <button class="btn-sched-save" onclick="createSteward()">Start steward</button>
      <button class="btn-sched-cancel" onclick="hideStewardForm()">Cancel</button>
    </div>
  </div>`;
}

// Objective + cadence + fence note — shared by the create and edit forms.
function _stewardObjectiveCadenceHTML(objective, cadence) {
  return `<label>Objective <span class="memory-hint" style="margin:0;font-weight:normal">(what the steward is responsible for — its charter)</span></label>
    <textarea id="steward-objective" rows="3" placeholder="e.g. Keep the README and docs in sync with the code; open a decision when a release is warranted.">${esc(objective || '')}</textarea>
    <label>Cadence (minutes between cycles)</label>
    <input type="number" id="steward-cadence" value="${cadence || 180}" min="30" max="1440" step="30">
    <div class="memory-hint" style="margin:6px 0 0;font-size:11px">🛡 Reversible work runs unattended. Anything
      irreversible (push, deploy, delete, external send, spend) is <b>hard-blocked</b> — the steward must post a
      decision for you to approve.</div>`;
}

// Open the form pre-filled to EDIT an existing steward's objective + cadence.
// Scope/project can't change on an edit (that would be a different steward), so
// the picker is replaced by a static label. Save posts to the same idempotent
// enable endpoint, which updates config, charter text, and the schedule in place.
async function editSteward(pid) {
  const area = document.getElementById('steward-form-area');
  if (!area) return;
  let st;
  try {
    st = await fetch(`${API_BASE}/api/project/${encodeURIComponent(pid)}/steward`).then(r => r.json());
  } catch (e) { if (window.showToast) showToast('Could not load steward config.', 4000); return; }
  stewardEditPid = pid;
  stewardFormOpen = true;
  const label = (typeof allProjects !== 'undefined' ? allProjects : []).find(p => p.id === pid);
  const projName = label ? label.name : pid;
  area.innerHTML = `<div class="schedule-form">
    <label>Editing steward</label>
    <div style="font-size:13px;font-weight:600;color:var(--text);margin-bottom:6px">${esc(projName)}</div>
    ${_stewardObjectiveCadenceHTML(st.objective || '', st.cadence_minutes || 180)}
    <div class="sched-actions">
      <button class="btn-sched-save" onclick="createSteward()">Save changes</button>
      <button class="btn-sched-cancel" onclick="hideStewardForm()">Cancel</button>
    </div>
  </div>`;
  area.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
}

function hideStewardForm() {
  stewardFormOpen = false;
  stewardEditPid = null;
  const area = document.getElementById('steward-form-area');
  if (area) area.innerHTML = '';
}

async function createSteward() {
  const objective = (document.getElementById('steward-objective')?.value || '').trim();
  const cadence = parseInt(document.getElementById('steward-cadence')?.value) || 180;
  if (!objective) { alert('An objective is required — it is the steward’s field of responsibility.'); return; }

  let url, body;
  if (stewardEditPid) {
    // Edit in place — the per-project enable endpoint is idempotent and updates
    // objective, cadence, charter text, and the schedule. Works for standalone
    // stewards too (their pid is a real _steward_ project record).
    url = `${API_BASE}/api/project/${encodeURIComponent(stewardEditPid)}/steward/enable`;
    body = { objective, cadence_minutes: cadence };
  } else if (stewardScope === 'standalone') {
    const name = (document.getElementById('steward-name')?.value || '').trim();
    if (!name) { alert('Give the standalone steward a name.'); return; }
    url = `${API_BASE}/api/steward/standalone/enable`;
    body = { name, objective, cadence_minutes: cadence };
  } else {
    const pid = document.getElementById('steward-project')?.value;
    if (!pid) { alert('Pick a project'); return; }
    url = `${API_BASE}/api/project/${encodeURIComponent(pid)}/steward/enable`;
    body = { objective, cadence_minutes: cadence };
  }
  const editing = !!stewardEditPid;
  try {
    const res = await fetch(url, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) { alert(data.error || 'Failed to save steward'); return; }
    if (data.fenced === false && window.showToast)
      showToast('Saved, but the safety fence could NOT be installed — running unfenced.', 8000);
    else if (window.showToast) showToast(editing ? 'Steward updated.' : 'Steward started.', 3000);
    hideStewardForm();
    await renderStewards();
    if (window.refreshScheduleList) refreshScheduleList();
  } catch (e) { alert('Failed: ' + e.message); }
}

async function stewardDisable(pid) {
  if (!confirm('Stop this steward? It stops running and the project is un-fenced. The charter record is kept.')) return;
  try {
    await fetch(`${API_BASE}/api/project/${encodeURIComponent(pid)}/steward/disable`, { method: 'POST' });
    await renderStewards();
    if (window.refreshScheduleList) refreshScheduleList();
  } catch (e) { alert('Failed: ' + e.message); }
}

// ── Interop exports (runtime-resolved; matches scheduler.js convention) ──
window.renderStewards = renderStewards;
window.showStewardForm = showStewardForm;
window.editSteward = editSteward;
window.setStewardScope = setStewardScope;
window.hideStewardForm = hideStewardForm;
window.createSteward = createSteward;
window.stewardDisable = stewardDisable;
window.stewardOpenCharter = stewardOpenCharter;
window.stewardOpenChat = stewardOpenChat;
