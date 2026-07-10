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
    const last = s.last_note_ts ? `last activity ${_stewardAgo(s.last_note_ts)}` : 'no activity yet';
    return `<div class="schedule-card-wrap"><div class="schedule-card${pend ? ' steward-attn' : ''}">
      <div class="schedule-card-body">
        <div class="schedule-card-project">${esc(s.project)}</div>
        <div class="schedule-card-task" title="${esc(s.objective)}">${esc(s.objective || '(no objective)')}</div>
        <div class="schedule-card-meta">
          <span>${cadence}</span>
          <span>${last}</span>
          ${fenceBadge}${pendBadge}${blkBadge}
        </div>
      </div>
      <div class="schedule-card-actions">
        <button class="btn-header-action" style="padding:3px 8px;font-size:11px;color:var(--accent);border-color:var(--accent)"
          onclick="stewardOpenCharter('${pid}')" title="Open the project to review the charter + decisions">Review</button>
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

function showStewardForm() {
  if (stewardFormOpen) { hideStewardForm(); return; }
  stewardFormOpen = true;
  const area = document.getElementById('steward-form-area');
  if (!area) return;
  const projects = (window.allProjects || []).filter(p => p.project_path);
  area.innerHTML = `<div class="schedule-form">
    <label>Project <span class="memory-hint" style="margin:0;font-weight:normal">(the field of responsibility)</span></label>
    <select id="steward-project">${projects.map(p =>
      `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('')}</select>
    <label>Objective <span class="memory-hint" style="margin:0;font-weight:normal">(what the steward is responsible for — its charter)</span></label>
    <textarea id="steward-objective" rows="2" placeholder="e.g. Keep the README and docs in sync with the code; open a decision when a release is warranted."></textarea>
    <label>Cadence (minutes between cycles)</label>
    <input type="number" id="steward-cadence" value="180" min="30" max="1440" step="30">
    <div class="memory-hint" style="margin:6px 0 0;font-size:11px">🛡 Reversible work runs unattended. Anything
      irreversible (push, deploy, delete, external send, spend) is <b>hard-blocked</b> — the steward must post a
      decision for you to approve.</div>
    <div class="sched-actions">
      <button class="btn-sched-save" onclick="createSteward()">Start steward</button>
      <button class="btn-sched-cancel" onclick="hideStewardForm()">Cancel</button>
    </div>
  </div>`;
}

function hideStewardForm() {
  stewardFormOpen = false;
  const area = document.getElementById('steward-form-area');
  if (area) area.innerHTML = '';
}

async function createSteward() {
  const pid = document.getElementById('steward-project')?.value;
  const objective = (document.getElementById('steward-objective')?.value || '').trim();
  const cadence = parseInt(document.getElementById('steward-cadence')?.value) || 180;
  if (!pid) { alert('Pick a project'); return; }
  if (!objective) { alert('An objective is required — it is the steward’s field of responsibility.'); return; }
  try {
    const res = await fetch(`${API_BASE}/api/project/${encodeURIComponent(pid)}/steward/enable`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ objective, cadence_minutes: cadence }),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) { alert(data.error || 'Failed to start steward'); return; }
    if (data.fenced === false && window.showToast)
      showToast('Steward started, but the safety fence could NOT be installed — running unfenced.', 8000);
    else if (window.showToast) showToast('Steward started.', 3000);
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
window.hideStewardForm = hideStewardForm;
window.createSteward = createSteward;
window.stewardDisable = stewardDisable;
window.stewardOpenCharter = stewardOpenCharter;
