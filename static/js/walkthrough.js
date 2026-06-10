// ── Walkthrough / Tour ────────────────────────────────────────────────────────

let wtActive = false;
let wtStep = 0;
let wtDontShow = false;

const WT_STEPS = [
  {
    id: 'welcome',
    title: 'Welcome to Clayrune',
    body: 'Clayrune is your operator console for long-running Claude agents \u2014 a multi-project dashboard where you dispatch, monitor, and coordinate AI work across many parallel streams. Quick tour: about 12 steps, 2 minutes.',
    target: null, pos: 'center',
  },
  {
    id: 'advanced-picker',
    title: 'Choose your level',
    body: () => `Clayrune starts in a simple view. Turn on any power-user features you want to see \u2014 you can change these anytime in Settings.<div id="wt-adv-list" style="margin-top:14px;display:flex;flex-direction:column;gap:8px;text-align:left">` +
      ADV_FEATURES.map(f => `
        <label style="display:flex;align-items:flex-start;gap:10px;cursor:pointer;padding:6px;border-radius:4px;background:var(--surface2)">
          <input type="checkbox" ${advancedFlags[f.key] ? 'checked' : ''}
            onchange="setAdvancedFlag('${esc(f.key)}',this.checked)"
            style="margin-top:2px;width:15px;height:15px;accent-color:var(--accent)">
          <span style="flex:1"><span style="font-weight:600;color:var(--text)">${esc(f.label)}</span><br><span style="font-size:11px;color:var(--text-faint)">${esc(f.hint)}</span></span>
        </label>`).join('') + `</div>`,
    target: null, pos: 'center',
  },
  {
    id: 'sidebar',
    title: 'Sidebar Navigation',
    body: 'The sidebar is your top-level navigation: <strong>Dashboard</strong>, <strong>Backlog</strong> (cross-project task list), <strong>\ud83d\udc1d Hivemind</strong> (cross-project multi-agent runs), <strong>Scheduler</strong>, <strong>Settings</strong>, <strong>Shared Rules</strong>, and <strong>Processes</strong>. Hover to expand. Recent projects also pin here for quick jump.',
    target: '#sidebar', pos: 'right',
    skip: () => window.innerWidth <= 960, // hidden on mobile
  },
  {
    id: 'header',
    title: 'Header Bar',
    body: 'Search projects + commands with <strong>Ctrl+K</strong>, see active agents at a glance, and check the live badge that pulses while auto-refresh is on. The <strong>?</strong> button on the right re-runs this tour any time.',
    target: '.header', pos: 'bottom',
  },
  {
    id: 'toolbar',
    title: 'Toolbar',
    body: 'Switch between <strong>Grid</strong> and <strong>List</strong> views, filter projects by status or domain, toggle compact density, or create a new project.',
    target: '.toolbar', pos: 'bottom',
  },
  {
    id: 'sample-tile',
    title: 'Project Tiles',
    body: 'Each tile shows a project\u2019s status and last activity. Click one to open it as a modal window. We\u2019ve created a sample project for you to explore.',
    target: null, pos: 'right', demo: 'tile',
    onEnter: async () => {
      await fetch(API_BASE + '/api/walkthrough/sample-project', { method: 'POST' });
      await refreshSilent();
    },
  },
  {
    id: 'open-modal',
    title: 'Project Modal',
    body: 'Click a tile to open the project. Multiple modals can be open at once \u2014 drag them around, resize, minimize. Open conversations and their layouts even survive a page refresh.',
    target: null, pos: 'left', demo: 'modal',
  },
  {
    id: 'tabs',
    title: 'Tabs',
    body: '<strong>Agent</strong> (dispatch + active sessions), <strong>Backlog</strong> (tasks for this project), <strong>Agent Log</strong> (completed sessions, click any to view its transcript), <strong>Plans</strong>, <strong>Activity</strong>. On mobile these tabs move into the three-dot menu.',
    target: null, pos: 'bottom', demo: 'modal', demoTarget: '.modal-tab-bar',
  },
  {
    id: 'agent',
    title: 'Agent Dispatch',
    body: 'Type a task in the Agent tab and click Dispatch. The agent runs in the background and streams output here AND into the bottom Agent Console so you can keep watching from anywhere. Plans triggered by the agent show approve/collapse buttons \u2014 nothing dangerous runs without your click.',
    target: null, pos: 'left', demo: 'modal-agent',
  },
  {
    id: 'menu',
    title: 'Three-Dot Menu',
    body: 'The three-dot button in any project modal opens a menu with:' +
      '<ul style="margin:8px 0 0 18px;padding:0">' +
      '<li><strong>\ud83d\udc1d Hiveminds</strong> for this project</li>' +
      '<li><strong>\u2728 Start Hivemind</strong></li>' +
      '<li><strong>Memory &amp; Rules</strong> editor</li>' +
      '<li><strong>Status</strong> / <strong>Color</strong> / <strong>Domain</strong> / <strong>Model</strong> per project</li>' +
      '<li><strong>GitHub Sync</strong></li>' +
      '</ul>' +
      '<div style="margin-top:8px">On mobile, the project tabs (Agent / Backlog / etc.) move to the top of this menu.</div>',
    target: null, pos: 'bottom', demo: 'modal', demoTarget: '.modal-menu-btn',
  },
  {
    id: 'hivemind-sidebar',
    title: '\ud83d\udc1d Hivemind \u2014 multi-agent runs',
    body: 'Hivemind is Clayrune\u2019s signature feature: an orchestrator agent decomposes a goal into workstreams, then parallel worker agents tackle them in coordination. The <strong>\ud83d\udc1d Hivemind</strong> sidebar entry shows every hivemind across every project \u2014 each card has a planner-to-workers tree, status pill, and stats. Long-idle "active" hiveminds auto-mark themselves <strong>stale</strong> with a Restart control.',
    target: '[data-nav="hivemind"]', pos: 'right',
    skip: () => window.innerWidth <= 960, // mobile uses bottom-tab; covered separately
  },
  {
    id: 'scheduler',
    title: 'Scheduler \u2014 recurring agents',
    body: 'Set up tasks that fire on a daily / cron / interval schedule. Each schedule has a <strong>\u25b6 Run Now</strong> button to fire immediately and a <strong>Runs</strong> button that opens an inline panel listing the most recent runs (50 per page). Click any run row to read its transcript.',
    target: '[data-nav="scheduler"]', pos: 'right',
    skip: () => window.innerWidth <= 960, // mobile bottom-tab covers this
  },
  {
    id: 'console',
    title: 'Agent Console',
    body: 'Running agent sessions \u2014 from any project, manual or automated \u2014 appear in this bottom tray. Expand it to view output, send follow-ups, or stop a session without leaving wherever you are.',
    target: '#agent-console', pos: 'top',
    // The console is hidden when no sessions are running (default state). Force
    // it visible for this step so the highlight has something to point at,
    // restore the prior class set on leave.
    onEnter: () => {
      const el = document.getElementById('agent-console');
      if (el) {
        el.dataset.wtPrevHidden = el.classList.contains('hidden') ? '1' : '';
        el.classList.remove('hidden');
      }
    },
    onLeave: () => {
      const el = document.getElementById('agent-console');
      if (el && el.dataset.wtPrevHidden === '1') {
        el.classList.add('hidden');
        delete el.dataset.wtPrevHidden;
      }
    },
    skip: () => window.innerWidth <= 960, // bottom tab bar covers this on mobile
  },
  {
    id: 'bottom-tabs',
    title: 'Bottom Tabs',
    body: 'Mobile navigation: <strong>Home</strong>, <strong>Backlog</strong>, <strong>+ FAB</strong> (new project), <strong>Scheduler</strong>, <strong>\ud83d\udc1d Hivemind</strong>. Settings is reachable via the avatar circle in the top app bar.',
    target: '#bottom-tab-bar', pos: 'top',
    skip: () => window.innerWidth > 960, // only on mobile
  },
  {
    id: 'cmd-palette',
    title: 'Command Palette',
    body: 'Press <strong>Ctrl+K</strong> anywhere to fuzzy-search projects, jump to any view, open settings, or re-take this tour.',
    target: '.cmd-palette', pos: 'bottom',
    // The CSS class is `.visible` (not `.open` — that was a copy-paste bug).
    // Without this, the overlay stays hidden and the highlight box appears
    // to point at empty space.
    // Also pre-render results so the palette has visible content (otherwise
    // it's just an empty input box).
    //
    // Critical: cmd-overlay has z-index 9999 which is ABOVE the walkthrough
    // overlay (2000). When .visible is added the overlay also has
    // pointer-events: auto + an onclick that closes the palette on
    // backdrop clicks. Result: when the user tries to click "Next" on the
    // wt-card, the click is intercepted by cmd-overlay, which closes the
    // palette — leaving the wt-highlight glowing around an empty rectangle
    // ("the second step 14 shows blank square"). We disable click-through
    // on the overlay (pointer-events: none) and re-enable it on the palette
    // itself so the rendered palette stays visible but the wt-card's Next
    // button isn't shadowed by the overlay anymore.
    onEnter: async () => {
      const overlay = document.getElementById('cmd-overlay');
      if (!overlay) return;
      const palette = overlay.querySelector('.cmd-palette');
      overlay.classList.add('visible');
      overlay.style.background = 'transparent';
      overlay.style.backdropFilter = 'none';
      overlay.style.webkitBackdropFilter = 'none';
      overlay.style.pointerEvents = 'none';
      if (palette) {
        // Defeat the open-animation transition so we don't measure
        // mid-flight (cmd-palette uses transform translateY(-10px)
        // scale(0.98) → 0/1 over 0.15s, so getBoundingClientRect right
        // after .visible is added returns the pre-animation rect, leaving
        // the highlight pointing at the empty pre-position). Force final
        // state synchronously.
        palette.style.transition = 'none';
        palette.style.transform = 'translateY(0) scale(1)';
        palette.style.pointerEvents = 'auto';
      }
      cmdPaletteOpen = true;
      try { renderCommandResults(''); } catch (e) {}
      // Yield one frame so DOM/layout flush before wtShow measures the rect.
      await new Promise(r => requestAnimationFrame(() => r()));
    },
    onLeave: () => {
      const overlay = document.getElementById('cmd-overlay');
      if (!overlay) return;
      const palette = overlay.querySelector('.cmd-palette');
      overlay.classList.remove('visible');
      overlay.style.background = '';
      overlay.style.backdropFilter = '';
      overlay.style.webkitBackdropFilter = '';
      overlay.style.pointerEvents = '';
      if (palette) {
        palette.style.transition = '';
        palette.style.transform = '';
        palette.style.pointerEvents = '';
      }
      cmdPaletteOpen = false;
    },
  },
  {
    id: 'ask-claydo',
    title: 'Ask Claydo any time',
    body: 'Click the floating <strong>Claydo</strong> button bottom-right (it\u2019s pulsing for you) to ask questions about Clayrune in plain English \u2014 Claydo can highlight the relevant UI element while explaining. It reads the same User Guide that powers this tour, so it always knows what\u2019s available.',
    target: '#claydo-fab', pos: 'left',
  },
  {
    id: 'done',
    title: 'You\u2019re all set',
    body: 'Start by exploring the sample project or create your own with <strong>+ New Project</strong>. Re-run this tour any time from Settings, the Command Palette (Ctrl+K \u2192 "Take Tour"), or the <strong>?</strong> button in the header.',
    target: null, pos: 'center',
  },
];

// Build virtual demo elements for the walkthrough
function wtDemoTileHTML() {
  return `<div class="card status-active" style="width:260px;aspect-ratio:1;pointer-events:none">
    <div class="card-header">
      <div class="card-title-row">
        <span class="project-name">Sample Project</span>
        <span class="domain-tag" style="background:var(--surface3);color:var(--text-dim)">General</span>
      </div>
      <div style="display:flex;align-items:center;gap:6px">
        <span class="status-pill status-active">active</span>
      </div>
    </div>
    <div class="tile-body">
      <div class="tile-task">Learn how to use Clayrune</div>
    </div>
    <div class="tile-footer">
      <span class="backlog-badge">3 open</span>
      <span class="time-ago">just now</span>
    </div>
  </div>`;
}

function wtDemoModalHTML(activeTab) {
  const tabs = ['Agent','Backlog','Agent Log','Plans','Activity','Hivemind'];
  const tabBar = tabs.map(t => {
    const key = t.toLowerCase().replace(' ', '-');
    const active = (activeTab === key) ? ' active' : '';
    return `<div class="modal-tab${active}">${t}</div>`;
  }).join('');

  let bodyContent = '';
  if (activeTab === 'backlog') {
    bodyContent = `
      <div style="padding:16px">
        <div style="display:flex;gap:8px;margin-bottom:14px">
          <input type="text" placeholder="Add backlog item..." style="flex:1;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:8px 12px;border-radius:6px;font-size:13px;outline:none" disabled>
          <button style="background:var(--accent);color:var(--bg);border:none;padding:8px 16px;border-radius:6px;font-weight:600;font-size:13px" disabled>Add</button>
        </div>
        <div class="backlog-item priority-normal" style="pointer-events:none">
          <button class="backlog-check"></button>
          <span class="backlog-text">Explore the project tabs</span>
          <div class="backlog-meta"><span class="priority-badge priority-normal">normal</span></div>
        </div>
        <div class="backlog-item priority-high" style="pointer-events:none">
          <button class="backlog-check"></button>
          <span class="backlog-text">Try dispatching an AI agent</span>
          <div class="backlog-meta"><span class="priority-badge priority-high">high</span></div>
        </div>
        <div class="backlog-item priority-low" style="pointer-events:none">
          <button class="backlog-check"></button>
          <span class="backlog-text">Connect a GitHub repo for issue sync</span>
          <div class="backlog-meta"><span class="priority-badge priority-low">low</span></div>
        </div>
      </div>`;
  } else if (activeTab === 'agent') {
    bodyContent = `
      <div style="padding:16px;display:flex;flex-direction:column;gap:12px">
        <div style="flex:1;display:flex;align-items:center;justify-content:center;color:var(--text-faint);font-size:13px;padding:40px 0">
          No agent session yet. Type a prompt below to dispatch one.
        </div>
        <div style="display:flex;gap:8px;align-items:flex-end">
          <textarea style="flex:1;background:var(--bg);border:1px solid var(--border);color:var(--text);padding:10px 12px;border-radius:8px;font-size:13px;font-family:'Inter',sans-serif;resize:none;height:60px;outline:none" placeholder="Describe a task for the AI agent..." disabled></textarea>
          <button style="background:var(--accent);color:var(--bg);border:none;padding:10px 20px;border-radius:8px;font-weight:600;font-size:13px;height:42px" disabled>Dispatch</button>
        </div>
      </div>`;
  } else {
    bodyContent = `<div style="padding:40px;text-align:center;color:var(--text-faint);font-size:13px">Tab content appears here</div>`;
  }

  return `<div class="modal-window focused" style="width:700px;height:520px;pointer-events:none;position:relative;border-radius:12px;overflow:hidden">
    <div class="modal-content" style="display:flex;flex-direction:column;height:100%">
      <div class="modal-header">
        <div class="modal-window-controls">
          <button class="modal-menu-btn" style="pointer-events:none">&#x22EE;</button>
          <button class="modal-minimize" style="pointer-events:none">&#x2015;</button>
          <button class="modal-close" style="pointer-events:none">&#10005;</button>
        </div>
        <div class="card-title-row">
          <input class="name-edit" value="Sample Project" disabled style="pointer-events:none">
          <span class="domain-tag" style="background:var(--surface3);color:var(--text-dim)">General</span>
        </div>
      </div>
      <div class="card-summary" style="pointer-events:none">
        <div class="summary-item"><span class="summary-label">Current Task</span><span class="summary-value current-task">Learn how to use Clayrune</span></div>
        <div class="summary-item"><span class="summary-label">Status</span><span class="status-pill status-active">active</span></div>
      </div>
      <div class="modal-tab-bar" style="pointer-events:none">${tabBar}</div>
      <div class="modal-scroll-body" style="flex:1;overflow:hidden">${bodyContent}</div>
    </div>
  </div>`;
}

function wtDemoMenuHTML() {
  const tabs = ['Agent','Backlog','Agent Log','Plans','Activity','Hivemind'];
  const tabBar = tabs.map(t => {
    const key = t.toLowerCase().replace(' ', '-');
    return `<div class="modal-tab${key === 'agent' ? ' active' : ''}">${t}</div>`;
  }).join('');

  return `<div class="modal-window focused" style="width:700px;height:520px;pointer-events:none;position:relative;border-radius:12px;overflow:visible">
    <div class="modal-content" style="display:flex;flex-direction:column;height:100%;overflow:hidden;border-radius:12px;background:var(--surface)">
      <div class="modal-header" style="position:relative">
        <div class="modal-window-controls">
          <button class="modal-menu-btn" style="pointer-events:none;background:var(--accent-dim);color:var(--accent);border-color:var(--accent)">&#x22EE;</button>
          <button class="modal-minimize" style="pointer-events:none">&#x2015;</button>
          <button class="modal-close" style="pointer-events:none">&#10005;</button>
        </div>
          <div class="modal-menu-dropdown" style="display:block;pointer-events:none;position:absolute;top:44px;right:8px;min-width:220px;z-index:50">
            <button class="modal-menu-item wt-menu-status" style="pointer-events:none">
              <span class="menu-icon">&#x25CF;</span> Change Status <span style="margin-left:auto;color:var(--text-faint);font-size:11px">&#x25B8;</span>
            </button>
            <button class="modal-menu-item wt-menu-color" style="pointer-events:none">
              <span class="menu-icon">&#x25CF;</span> Change Color <span style="margin-left:auto;color:var(--text-faint);font-size:11px">&#x25B8;</span>
            </button>
            <button class="modal-menu-item" style="pointer-events:none">
              <span class="menu-icon">&#x25CF;</span> Change Domain <span style="margin-left:auto;color:var(--text-faint);font-size:11px">&#x25B8;</span>
            </button>
            <button class="modal-menu-item wt-menu-model" style="pointer-events:none">
              <span class="menu-icon">&#x2699;</span> Agent Model <span style="margin-left:4px;color:var(--text-faint);font-size:11px">default</span> <span style="margin-left:auto;color:var(--text-faint);font-size:11px">&#x25B8;</span>
            </button>
            <button class="modal-menu-item" style="pointer-events:none">
              <span class="menu-icon">&#x270E;</span> Add Description
            </button>
            <button class="modal-menu-item" style="pointer-events:none">
              <span class="menu-icon">&#x21B5;</span> Enter Key <span style="margin-left:4px;color:var(--text-faint);font-size:11px">new line</span> <span style="margin-left:auto;color:var(--text-faint);font-size:11px">&#x25B8;</span>
            </button>
            <button class="modal-menu-item wt-menu-github" style="pointer-events:none">
              <span class="menu-icon">&#x1F517;</span> GitHub Sync <span style="margin-left:4px;color:var(--text-faint);font-size:11px">not connected</span> <span style="margin-left:auto;color:var(--text-faint);font-size:11px">&#x25B8;</span>
            </button>
            <div class="modal-menu-sep"></div>
            <button class="modal-menu-item" style="pointer-events:none">
              <span class="menu-icon">&#x1F4DD;</span> Memory
            </button>
            <button class="modal-menu-item" style="pointer-events:none">
              <span class="menu-icon">&#x1F4DC;</span> Rules
            </button>
            <div class="modal-menu-sep"></div>
            <button class="modal-menu-item danger" style="pointer-events:none">
              <span class="menu-icon">&#x1F5D1;</span> Delete Project
            </button>
          </div>
        <div class="card-title-row">
          <input class="name-edit" value="Sample Project" disabled style="pointer-events:none">
          <span class="domain-tag" style="background:var(--surface3);color:var(--text-dim)">General</span>
        </div>
      </div>
      <div class="card-summary" style="pointer-events:none">
        <div class="summary-item"><span class="summary-label">Current Task</span><span class="summary-value current-task">Learn how to use Clayrune</span></div>
        <div class="summary-item"><span class="summary-label">Status</span><span class="status-pill status-active">active</span></div>
      </div>
      <div class="modal-tab-bar" style="pointer-events:none">${tabBar}</div>
      <div class="modal-scroll-body" style="flex:1;overflow:hidden">
        <div style="padding:40px;text-align:center;color:var(--text-faint);font-size:13px">Tab content appears here</div>
      </div>
    </div>
  </div>`;
}

function startWalkthrough() {
  wtActive = true;
  wtStep = 0;
  wtDontShow = false;
  showDesktop();
  wtShow(0);
}

async function wtShow(idx) {
  // Call onLeave for the previous step
  const prevStep = WT_STEPS[wtStep];
  if (prevStep && prevStep.onLeave) prevStep.onLeave();

  wtStep = idx;
  const step = WT_STEPS[idx];
  if (!step) { wtEnd(); return; }

  // Skip steps that don't apply to current viewport
  if (step.skip && step.skip()) {
    if (idx < WT_STEPS.length - 1) { wtShow(idx + 1); return; }
    else { wtEnd(); return; }
  }

  if (step.onEnter) await step.onEnter();

  // Remove old overlay
  const old = document.getElementById('wt-overlay');
  if (old) old.remove();

  const overlay = document.createElement('div');
  overlay.id = 'wt-overlay';
  overlay.className = 'wt-overlay';

  const backdrop = document.createElement('div');
  backdrop.className = 'wt-backdrop';
  backdrop.onclick = () => {}; // block clicks
  overlay.appendChild(backdrop);

  // Remove previous elevation
  document.querySelectorAll('.wt-elevated').forEach(el => el.classList.remove('wt-elevated'));

  let targetEl = step.target ? document.querySelector(step.target) : null;

  // Inject virtual demo element if step uses one
  if (step.demo) {
    const demo = document.createElement('div');
    demo.className = 'wt-demo';
    demo.style.cssText = 'position:fixed;z-index:2001;pointer-events:none;';

    if (step.demo === 'tile') {
      demo.innerHTML = wtDemoTileHTML();
      const isMobile = window.innerWidth <= 960;
      demo.style.left = isMobile ? `${Math.max(16, (window.innerWidth - 260) / 2)}px` : '80px';
      demo.style.top = isMobile ? '100px' : '180px';
      overlay.appendChild(demo);
      targetEl = demo.firstElementChild;
    } else if (step.demo === 'modal-menu') {
      demo.innerHTML = wtDemoMenuHTML();
      const demoW = Math.min(700, window.innerWidth - 32);
      demo.style.left = `${Math.max(16, (window.innerWidth - demoW) / 2)}px`;
      demo.style.top = `${Math.max(16, (window.innerHeight - 520) / 2)}px`;
      if (demoW < 700) demo.style.transform = `scale(${demoW / 700})`;
      demo.style.transformOrigin = 'top left';
      overlay.appendChild(demo);
      if (step.demoTarget) {
        const subEl = demo.querySelector(step.demoTarget);
        if (subEl) {
          subEl.style.outline = '2px solid var(--accent)';
          subEl.style.outlineOffset = '2px';
          subEl.style.borderRadius = '4px';
          subEl.style.background = 'var(--accent-dim)';
          targetEl = subEl;
        } else {
          targetEl = demo.firstElementChild;
        }
      } else {
        targetEl = demo.firstElementChild;
      }
    } else if (step.demo.startsWith('modal')) {
      const tab = step.demo === 'modal-backlog' ? 'backlog'
                : step.demo === 'modal-agent' ? 'agent'
                : 'agent';
      demo.innerHTML = wtDemoModalHTML(tab);
      const demoW = Math.min(700, window.innerWidth - 32);
      demo.style.left = `${Math.max(16, (window.innerWidth - demoW) / 2)}px`;
      demo.style.top = `${Math.max(16, (window.innerHeight - 520) / 2)}px`;
      if (demoW < 700) { demo.style.transform = `scale(${demoW / 700})`; demo.style.transformOrigin = 'top left'; }
      overlay.appendChild(demo);
      if (step.demoTarget) {
        const subEl = demo.querySelector(step.demoTarget);
        if (subEl) {
          subEl.style.outline = '2px solid var(--accent)';
          subEl.style.outlineOffset = '3px';
          subEl.style.borderRadius = '6px';
          targetEl = subEl;
        } else {
          targetEl = demo.firstElementChild;
        }
      } else {
        targetEl = demo.firstElementChild;
      }
    }
  }

  // Elevate the target (or its modal-window ancestor) above the backdrop
  if (targetEl && !step.demo) {
    const modal = targetEl.closest('.modal-window');
    (modal || targetEl).classList.add('wt-elevated');
  }

  // Clip-path cutout around target
  if (targetEl) {
    const r = targetEl.getBoundingClientRect();
    const pad = 10;
    const x1 = r.left - pad, y1 = r.top - pad;
    const x2 = r.right + pad, y2 = r.bottom + pad;
    // polygon: full screen with rectangular hole
    backdrop.style.clipPath = `polygon(
      0% 0%, 100% 0%, 100% 100%, 0% 100%, 0% 0%,
      ${x1}px ${y1}px, ${x1}px ${y2}px, ${x2}px ${y2}px, ${x2}px ${y1}px, ${x1}px ${y1}px
    )`;

    const hl = document.createElement('div');
    hl.className = 'wt-highlight';
    hl.style.left = (r.left - pad) + 'px';
    hl.style.top = (r.top - pad) + 'px';
    hl.style.width = (r.width + pad * 2) + 'px';
    hl.style.height = (r.height + pad * 2) + 'px';
    overlay.appendChild(hl);
  }

  // Card
  const card = document.createElement('div');
  card.className = 'wt-card' + (step.pos === 'center' ? ' centered' : '');

  const isFirst = idx === 0;
  const isLast = idx === WT_STEPS.length - 1;

  let btns = '';
  if (isLast) {
    btns = `<button class="wt-btn wt-btn-primary" onclick="wtEnd()">Get Started</button>`;
  } else {
    if (!isFirst) btns += `<button class="wt-btn" onclick="wtBack()">Back</button>`;
    btns += `<button class="wt-btn wt-btn-skip" onclick="wtSkip()">Skip</button>`;
    btns += `<button class="wt-btn wt-btn-primary" onclick="wtNext()">${isFirst ? 'Start Tour' : 'Next'}</button>`;
  }

  let dismissHTML = '';
  if (!isLast) {
    dismissHTML = `<div class="wt-dismiss">
      <input type="checkbox" id="wt-dontshow" ${wtDontShow ? 'checked' : ''} onchange="wtDontShow=this.checked">
      <label for="wt-dontshow">Don't show this again</label>
    </div>`;
  }

  // Body strings are author-controlled hardcoded text (WT_STEPS const), so they
  // can include <strong>/<em> markup. Don't esc() — that would render the tags
  // as literal text. Functions return pre-built HTML and pass through too.
  const bodyHTML = typeof step.body === 'function' ? step.body() : step.body;

  // Skip-aware progress count: skipped steps shouldn't count toward total or
  // create gaps in the numbering. Otherwise desktop users see 13 → 15 (the
  // mobile-only bottom-tabs step at idx 13 gets eaten silently).
  const visibleSteps = WT_STEPS.filter(s => !(s.skip && s.skip()));
  const visibleIdx = visibleSteps.findIndex(s => s.id === step.id);
  const visiblePos = (visibleIdx >= 0 ? visibleIdx : 0) + 1;
  const visibleTotal = visibleSteps.length;

  card.innerHTML = `
    <div class="wt-title">${esc(step.title)}</div>
    <div class="wt-body">${bodyHTML}</div>
    <div class="wt-actions">
      <span class="wt-progress">${visiblePos} / ${visibleTotal}</span>
      ${btns}
    </div>
    ${dismissHTML}
  `;
  overlay.appendChild(card);
  document.body.appendChild(overlay);

  // Position card near target
  if (targetEl && step.pos !== 'center') {
    wtPositionCard(targetEl, card, step.pos);
  }
}

function wtPositionCard(targetEl, cardEl, pos) {
  const tr = targetEl.getBoundingClientRect();
  const cr = cardEl.getBoundingClientRect();
  const gap = 20;
  let left, top;

  switch (pos) {
    case 'bottom':
      left = tr.left + (tr.width - cr.width) / 2;
      top = tr.bottom + gap;
      break;
    case 'top':
      left = tr.left + (tr.width - cr.width) / 2;
      top = tr.top - cr.height - gap;
      break;
    case 'left':
      left = tr.left - cr.width - gap;
      top = tr.top + (tr.height - cr.height) / 2;
      break;
    case 'right':
      left = tr.right + gap;
      top = tr.top + (tr.height - cr.height) / 2;
      break;
    default:
      return;
  }

  // Clamp to viewport
  left = Math.max(16, Math.min(left, window.innerWidth - cr.width - 16));
  top = Math.max(16, Math.min(top, window.innerHeight - cr.height - 16));
  cardEl.style.left = left + 'px';
  cardEl.style.top = top + 'px';
}

function wtNext() { if (wtStep < WT_STEPS.length - 1) wtShow(wtStep + 1); else wtEnd(); }
function wtBack() {
  let prev = wtStep - 1;
  while (prev > 0 && WT_STEPS[prev].skip && WT_STEPS[prev].skip()) prev--;
  if (prev >= 0) wtShow(prev);
}
function wtSkip() {
  if (wtDontShow) localStorage.setItem('walkthrough_done', '1');
  wtEnd();
}
function wtEnd() {
  const curStep = WT_STEPS[wtStep];
  if (curStep && curStep.onLeave) curStep.onLeave();
  wtActive = false;
  localStorage.setItem('walkthrough_done', '1');
  document.querySelectorAll('.wt-elevated').forEach(el => el.classList.remove('wt-elevated'));
  const el = document.getElementById('wt-overlay');
  if (el) el.remove();
}

// Reposition on resize
window.addEventListener('resize', () => { if (wtActive) wtShow(wtStep); });
// Escape to skip
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && wtActive) {
    e.stopPropagation();
    wtSkip();
  }
});

// ── interop: page-called surface ─────────────────────────────────────────────
// Everything below is invoked from OUTSIDE this module — static/generated
// inline event attributes and the inline boot script — all of which resolve
// against the global object, never module scope.
window.startWalkthrough = startWalkthrough; // interop: header ? btn (onclick), Settings "Take Tour" (generated onclick), palette action, first-run auto-start (setTimeout in fetchProjects().then)
window.wtNext = wtNext; // interop: wt-card generated onclick (Start Tour / Next)
window.wtBack = wtBack; // interop: wt-card generated onclick (Back)
window.wtSkip = wtSkip; // interop: wt-card generated onclick (Skip)
window.wtEnd = wtEnd;   // interop: wt-card generated onclick (Get Started)
// interop: the "Don't show this again" checkbox writes `wtDontShow=this.checked`
// from a generated onchange attribute. Inline handlers resolve against the
// global object and can't see module-scoped `let` bindings — without this
// bridge the assignment would create a NEW, diverging window.wtDontShow
// property and the checkbox would silently stop registering. The accessor
// routes window-property reads/writes to the module binding (one source of
// truth; the moved code itself stays byte-verbatim).
Object.defineProperty(window, 'wtDontShow', {
  get() { return wtDontShow; },
  set(v) { wtDontShow = v; },
});
