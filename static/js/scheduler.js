// ── Scheduler ─────────────────────────────────────────────────────────────

let schedulerEditId = null; // null = adding new, string = editing existing

async function openScheduler() {
  const modalId = '__scheduler';
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
  _clampModalSize(content, 700);
  content.innerHTML = `
    <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:16px 24px 12px 28px">
      <span style="font-size:16px;font-weight:700;color:var(--text)">Scheduled Tasks</span>
      <div class="modal-window-controls" style="position:static;display:flex;gap:4px">
        <button class="modal-minimize" onclick="minimizeModal('${modalId}')" title="Minimize">&#x2015;</button>
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
    </div>
    <div class="scheduler-section" style="padding-top:12px">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
        <span class="memory-hint" style="margin:0">Automatically dispatch agents at scheduled times.</span>
        <button class="btn-add" style="padding:6px 14px;font-size:12px;flex-shrink:0" onclick="showScheduleForm()">+ Add Schedule</button>
      </div>
      <div id="schedule-form-area"></div>
      <div id="schedule-list"><div class="schedule-empty">Loading...</div></div>
    </div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);

  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);

  await refreshScheduleList();
}

async function refreshScheduleList() {
  const container = document.getElementById('schedule-list');
  if (!container) return;
  try {
    const res = await fetch(API_BASE + '/api/schedules');
    const schedules = await res.json();
    if (!schedules.length) {
      container.innerHTML = '<div class="schedule-empty">No scheduled tasks yet. Click "+ Add Schedule" to create one.</div>';
      return;
    }
    container.innerHTML = schedules.map(s => {
      const enabledClass = s.enabled ? 'on' : '';
      const cardClass = s.enabled ? '' : ' disabled';
      const desc = scheduleDescription(s);
      const lastRun = s.last_run ? timeAgoShort(s.last_run) : 'never';
      const nextRun = s.next_run ? formatScheduleTime(s.next_run) : (s.enabled ? 'calculating...' : 'disabled');
      const descLine = s.description
        ? `<div class="schedule-card-desc" title="${esc(s.description)}" style="font-size:11px;color:var(--text-muted);margin:2px 0 4px;font-style:italic;overflow:hidden;text-overflow:ellipsis;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical">${esc(s.description)}</div>`
        : '';
      const continueBadge = (s.continue_session === false)
        ? `<span title="Each run starts a fresh session" style="color:var(--text-muted)">Fresh each run</span>`
        : `<span title="Resumes from previous run so the agent remembers prior work" style="color:var(--accent)">Continues prior session</span>`;
      return `<div class="schedule-card-wrap">
        <div class="schedule-card${cardClass}">
          <div class="schedule-card-body">
            <div class="schedule-card-project">${esc(s.project_name || s.project_id)}</div>
            ${descLine}
            <div class="schedule-card-task" title="${esc(s.task)}">${esc(s.task)}</div>
            <div class="schedule-card-meta">
              <span>${desc}</span>
              <span>Last: ${lastRun}</span>
              <span>Next: ${nextRun}</span>
              ${continueBadge}
            </div>
          </div>
          <div class="schedule-card-actions">
            <div class="schedule-toggle ${enabledClass}" onclick="toggleScheduleEnabled('${esc(s.id)}', ${!s.enabled})" title="${s.enabled ? 'Disable' : 'Enable'}"></div>
            <button class="btn-header-action" style="padding:3px 8px;font-size:11px" onclick="toggleScheduleRuns('${esc(s.id)}','${esc(s.project_id || '')}')" title="Show past runs">Runs</button>
            <button class="btn-header-action" style="padding:3px 8px;font-size:11px" onclick="editSchedule('${esc(s.id)}')">Edit</button>
            <button class="btn-header-action" style="padding:3px 8px;font-size:11px;color:var(--red-text);border-color:var(--red)" onclick="deleteSchedule('${esc(s.id)}')">Del</button>
            <button class="btn-header-action" style="padding:3px 8px;font-size:11px;color:var(--accent);border-color:var(--accent)" onclick="runScheduleNow('${esc(s.id)}')" title="Dispatch this task now">&#x25B6; Run Now</button>
          </div>
        </div>
        <div class="runs-panel" id="schedule-runs-${esc(s.id)}" style="display:none"></div>
      </div>`;
    }).join('');
  } catch(e) {
    container.innerHTML = '<div class="schedule-empty">Failed to load schedules.</div>';
  }
}

async function runScheduleNow(scheduleId) {
  try {
    const res = await fetch(API_BASE + `/api/schedule/${encodeURIComponent(scheduleId)}/run-now`, { method: 'POST' });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      showToast('Run failed: ' + (data.error || res.statusText), 6000);
      return;
    }
    showToast('Schedule dispatched', 3000);
    // Refresh list to update the "Last:" timestamp
    refreshScheduleList();
  } catch(e) {
    showToast('Run failed: ' + e.message, 6000);
  }
}

async function toggleScheduleRuns(scheduleId, projectId) {
  const panel = document.getElementById('schedule-runs-' + scheduleId);
  if (!panel) return;
  if (panel.style.display === 'block') {
    panel.style.display = 'none';
    return;
  }
  panel.style.display = 'block';
  await loadScheduleRunsPage(scheduleId, projectId, 0);
}

async function loadScheduleRunsPage(scheduleId, projectId, offset) {
  const panel = document.getElementById('schedule-runs-' + scheduleId);
  if (!panel) return;
  panel.innerHTML = '<div class="runs-empty">Loading runs...</div>';
  try {
    const limit = 50;
    const url = `${API_BASE}/api/schedule/${encodeURIComponent(scheduleId)}/runs?limit=${limit}&offset=${offset}`;
    const res = await fetch(url);
    if (!res.ok) {
      panel.innerHTML = '<div class="runs-empty">Failed to load runs.</div>';
      return;
    }
    const data = await res.json();
    const runs = data.runs || [];
    const total = data.total || 0;
    const pageFnTemplate = `loadScheduleRunsPage('${esc(scheduleId)}','${esc(projectId)}',$OFFSET)`;
    panel.innerHTML = renderRunRows(runs, projectId)
                    + renderRunsPagination(total, data.offset || 0, data.limit || limit, pageFnTemplate);
  } catch(e) {
    panel.innerHTML = '<div class="runs-empty">Failed to load runs.</div>';
  }
}

function scheduleDescription(s) {
  const type = s.schedule_type;
  // TZ abbr for time-of-day descriptions (daily / cron) — server interprets
  // these in the host's local zone.
  const tzAbbr = (() => {
    try {
      const m = new Date().toLocaleTimeString('en-US', { timeZoneName: 'short' }).match(/\b([A-Z]{2,5})$/);
      return m ? m[1] : '';
    } catch { return ''; }
  })();
  if (type === 'once') {
    return s.run_at ? 'Once at ' + formatScheduleTime(s.run_at) : 'Once (no time set)';
  } else if (type === 'daily') {
    const dayNames = ['', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    const days = (s.days || []).map(d => dayNames[d] || '?').join(', ');
    const time = s.time || '09:00';
    return `Daily at ${time}${tzAbbr ? ' ' + tzAbbr : ''}${days ? ' (' + days + ')' : ''}`;
  } else if (type === 'interval') {
    const mins = s.interval_minutes || 60;
    if (mins >= 60 && mins % 60 === 0) return `Every ${mins / 60}h`;
    return `Every ${mins}m`;
  } else if (type === 'cron') {
    return `Cron: ${s.cron_expr || '?'}${tzAbbr ? ' (' + tzAbbr + ')' : ''}`;
  }
  return type;
}

function formatScheduleTime(isoStr) {
  if (!isoStr) return '—';
  try {
    const d = new Date(isoStr);
    const now = new Date();
    const diffMs = d - now;
    const diffMin = Math.round(diffMs / 60000);
    if (diffMin >= 0 && diffMin < 60) return `in ${diffMin}m`;
    if (diffMin >= 60 && diffMin < 1440) return `in ${Math.round(diffMin / 60)}h`;
    const pad = n => String(n).padStart(2, '0');
    return `${pad(d.getMonth()+1)}/${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  } catch(e) { return isoStr.slice(0, 16); }
}
function showScheduleForm(existing) {
  schedulerEditId = existing ? existing.id : null;
  const area = document.getElementById('schedule-form-area');
  if (!area) return;
  const projects = allProjects.filter(p => p.project_path);
  const selPid = existing ? existing.project_id : (projects[0]?.id || '');
  const selType = existing ? existing.schedule_type : 'daily';
  const selTime = existing ? (existing.time || '09:00') : '09:00';
  const selDays = existing ? (existing.days || []) : [1,2,3,4,5];
  const selInterval = existing ? (existing.interval_minutes || 60) : 60;
  const selTask = existing ? existing.task : '';
  const selDescription = existing ? (existing.description || '') : '';
  const selContinue = existing ? (existing.continue_session !== false) : true;
  const selRunAt = existing ? (existing.run_at || '').slice(0, 16) : '';
  const selCron = existing ? (existing.cron_expr || '') : '';

  area.innerHTML = `<div class="schedule-form">
    <label>Project</label>
    <select id="sched-project">${projects.map(p =>
      `<option value="${esc(p.id)}"${p.id === selPid ? ' selected' : ''}>${esc(p.name)}</option>`
    ).join('')}</select>
    <label>Description <span class="memory-hint" style="margin:0;font-weight:normal">(for you &mdash; not sent to the agent)</span></label>
    <textarea id="sched-description" rows="2" placeholder="Why this schedule exists, what &ldquo;done&rdquo; looks like, etc.">${esc(selDescription)}</textarea>
    <label>Task <span class="memory-hint" style="margin:0;font-weight:normal">(the prompt sent on every run)</span></label>
    <textarea id="sched-task" rows="2" placeholder="What should the agent do?">${esc(selTask)}</textarea>
    <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-weight:normal">
      <input type="checkbox" id="sched-continue" ${selContinue ? 'checked' : ''} style="margin:0;width:auto">
      <span style="font-weight:600">Continue previous session</span>
      <span class="memory-hint" style="margin:0;font-weight:normal">(resume so the agent remembers prior runs)</span>
    </label>
    <label>Schedule Type</label>
    <div class="sched-type-row">
      <button class="sched-type-btn${selType==='daily'?' active':''}" onclick="setSchedType('daily')">Daily</button>
      <button class="sched-type-btn${selType==='interval'?' active':''}" onclick="setSchedType('interval')">Interval</button>
      <button class="sched-type-btn${selType==='once'?' active':''}" onclick="setSchedType('once')">Once</button>
      <button class="sched-type-btn${selType==='cron'?' active':''}" onclick="setSchedType('cron')">Cron</button>
    </div>
    <div id="sched-type-fields"></div>
    <div class="sched-actions">
      <button class="btn-sched-save" onclick="saveSchedule()">${existing ? 'Update' : 'Create'}</button>
      ${existing ? `<button class="btn-sched-cancel" style="border-color:var(--accent);color:var(--accent)" onclick="runScheduleNow('${esc(existing.id)}')">&#x25B6; Run Now</button>` : ''}
      <button class="btn-sched-cancel" onclick="hideScheduleForm()">Cancel</button>
    </div>
  </div>`;
  renderSchedTypeFields(selType, { time: selTime, days: selDays, interval_minutes: selInterval, run_at: selRunAt, cron_expr: selCron });
}

function hideScheduleForm() {
  const area = document.getElementById('schedule-form-area');
  if (area) area.innerHTML = '';
  schedulerEditId = null;
}

function setSchedType(type) {
  document.querySelectorAll('.sched-type-btn').forEach(b => b.classList.toggle('active', b.textContent.toLowerCase() === type));
  renderSchedTypeFields(type);
}

function renderSchedTypeFields(type, vals) {
  const container = document.getElementById('sched-type-fields');
  if (!container) return;
  const time = vals?.time || '09:00';
  const days = vals?.days || [1,2,3,4,5];
  const interval = vals?.interval_minutes || 60;
  const runAt = vals?.run_at || '';
  const cronExpr = vals?.cron_expr || '';

  // Show the host's local timezone abbreviation in form labels — schedule
  // times are always interpreted in local time (server uses datetime.now()
  // not utc).
  const tzAbbr = (() => {
    try {
      const m = new Date().toLocaleTimeString('en-US', { timeZoneName: 'short' }).match(/\b([A-Z]{2,5})$/);
      return m ? m[1] : 'local';
    } catch { return 'local'; }
  })();

  if (type === 'daily') {
    const dayLabels = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun'];
    container.innerHTML = `
      <label>Time (${tzAbbr})</label>
      <input type="time" id="sched-time" value="${time}">
      <label>Days</label>
      <div class="sched-days">
        ${dayLabels.map((label, i) => {
          const dayNum = i + 1;
          return `<button class="sched-day-btn${days.includes(dayNum)?' active':''}" data-day="${dayNum}" onclick="this.classList.toggle('active')">${label}</button>`;
        }).join('')}
      </div>`;
  } else if (type === 'interval') {
    container.innerHTML = `
      <label>Interval (minutes)</label>
      <input type="number" id="sched-interval" value="${interval}" min="1" step="1">`;
  } else if (type === 'once') {
    container.innerHTML = `
      <label>Run At (${tzAbbr})</label>
      <input type="datetime-local" id="sched-runat" value="${runAt}">`;
  } else if (type === 'cron') {
    container.innerHTML = `
      <label>Cron Expression (${tzAbbr})</label>
      <input type="text" id="sched-cron" value="${esc(cronExpr)}" placeholder="*/15 * * * *" spellcheck="false" style="font-family:var(--mono)">
      <span class="memory-hint" style="margin:4px 0 0;font-size:10px">5 fields: minute hour day-of-month month day-of-week &mdash; e.g. <code>0 9 * * 1-5</code> = weekdays 9am ${tzAbbr}</span>`;
  }
}

async function saveSchedule() {
  const pid = document.getElementById('sched-project')?.value;
  const task = document.getElementById('sched-task')?.value?.trim();
  if (!pid || !task) { alert('Project and task are required'); return; }

  const activeType = document.querySelector('.sched-type-btn.active')?.textContent?.toLowerCase() || 'daily';
  const description = (document.getElementById('sched-description')?.value || '').trim();
  const continueSession = !!document.getElementById('sched-continue')?.checked;
  const body = {
    project_id: pid,
    task,
    description,
    continue_session: continueSession,
    schedule_type: activeType,
  };

  if (activeType === 'daily') {
    body.time = document.getElementById('sched-time')?.value || '09:00';
    body.days = [...document.querySelectorAll('.sched-day-btn.active')].map(b => parseInt(b.dataset.day));
  } else if (activeType === 'interval') {
    body.interval_minutes = parseInt(document.getElementById('sched-interval')?.value) || 60;
  } else if (activeType === 'once') {
    const val = document.getElementById('sched-runat')?.value;
    if (val) body.run_at = new Date(val).toISOString();
  } else if (activeType === 'cron') {
    const expr = document.getElementById('sched-cron')?.value?.trim();
    if (!expr || expr.split(/\s+/).length !== 5) { alert('Cron expression must have exactly 5 fields'); return; }
    body.cron_expr = expr;
  }

  try {
    const url = schedulerEditId ? `${API_BASE}/api/schedules/${schedulerEditId}` : `${API_BASE}/api/schedules`;
    const method = schedulerEditId ? 'PUT' : 'POST';
    const res = await fetch(url, { method, headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) });
    if (!res.ok) { const d = await res.json(); alert(d.error || 'Failed'); return; }
    hideScheduleForm();
    await refreshScheduleList();
    refreshScheduleBanner();
  } catch(e) { alert('Failed to save: ' + e.message); }
}

async function toggleScheduleEnabled(id, enabled) {
  try {
    await fetch(`${API_BASE}/api/schedules/${id}`, {
      method: 'PUT',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ enabled })
    });
    await refreshScheduleList();
    refreshScheduleBanner();
  } catch(e) {}
}

async function editSchedule(id) {
  try {
    const res = await fetch(API_BASE + '/api/schedules');
    const schedules = await res.json();
    const sched = schedules.find(s => s.id === id);
    if (sched) showScheduleForm(sched);
  } catch(e) {}
}

async function deleteSchedule(id) {
  if (!confirm('Delete this schedule?')) return;
  try {
    await fetch(`${API_BASE}/api/schedules/${id}`, { method: 'DELETE' });
    await refreshScheduleList();
    refreshScheduleBanner();
  } catch(e) {}
}


// ── Interop: re-expose for inline/cross-module callers. All runtime-only
//    (resolve against window at event/call time); zero parse-time references.
//    `timeAgoShort` is deliberately NOT here — it STAYS INLINE (duplicate
//    top-level decl; the inline copy wins for the agent-console + schedule-
//    banner callers, and this module's own timeAgoShort(...) calls reach it
//    through the global object). `schedulerEditId` is module-private state
//    (zero outside refs). ──
window.openScheduler = openScheduler;            // sidebarNav / palette / schedule-banner row
window.formatScheduleTime = formatScheduleTime;  // schedule-banner Next-run line
window.loadScheduleRunsPage = loadScheduleRunsPage; // pagination onclick built by inline renderRunsPagination
// region-generated on*= handler targets:
window.showScheduleForm = showScheduleForm;
window.hideScheduleForm = hideScheduleForm;
window.setSchedType = setSchedType;
window.saveSchedule = saveSchedule;
window.toggleScheduleEnabled = toggleScheduleEnabled;
window.toggleScheduleRuns = toggleScheduleRuns;
window.editSchedule = editSchedule;
window.deleteSchedule = deleteSchedule;
window.runScheduleNow = runScheduleNow;
