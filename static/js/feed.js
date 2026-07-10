// ── Feed ────────────────────────────────────────────────────────────────────

// Derive a small icon + kind from a free-text activity_log message so the eye
// can scan event types in the Recent bucket. Pattern matches mirror the
// _log_agent_activity call sites in server.py — keep in sync if those change.
function classifyFeedEvent(msg) {
  const m = String(msg || '').toLowerCase();
  if (!m) return { icon: '·', kind: '' };  // middle dot
  if (m.startsWith('resume failed') || m.includes(' error')) return { icon: '⚠', kind: 'error' };       // ⚠
  if (m.startsWith('agent stopped'))           return { icon: '◼', kind: 'followup' };  // ◼
  if (m.startsWith('agent interrupted'))       return { icon: '⏸', kind: 'followup' };  // ⏸
  if (m.startsWith('agent revived'))           return { icon: '↻', kind: 'dispatch' };  // ↻
  if (m.startsWith('agent resumed'))           return { icon: '↻', kind: 'dispatch' };  // ↻
  if (m.startsWith('agent dispatched'))        return { icon: '▸', kind: 'dispatch' };  // ▸
  if (m.startsWith('agent follow-up queued'))  return { icon: '⋯', kind: 'followup' };  // ⋯
  if (m.startsWith('agent follow-up'))         return { icon: '↗', kind: 'followup' };  // ↗
  if (m.startsWith('hivemind'))                return { icon: '◆', kind: 'hivemind' };  // ◆
  if (m.startsWith('github'))                  return { icon: '◒', kind: 'github' };    // ◒
  if (m.includes('completed'))                 return { icon: '✓', kind: 'completed' }; // ✓
  return { icon: '·', kind: '' };
}

// Build the "Needs you" bucket purely from live project state (don't trust
// activity_log here — it lags). Each item is something the user can act on:
// asking → plan approval / question / generic input; stuck → blocked or error.
function _buildAttentionList() {
  const items = [];
  allProjects.forEach(p => {
    const fs = friendlyStatus(p);
    if (fs !== 'asking' && fs !== 'stuck') return;
    const live = computeLiveStatus(p.id) || {};
    let icon = '✋';  // ✋ default for asking
    let msg = '';
    // `kind`/`action` drive the dashboard "Waiting on you" cards (the action
    // button label). Additive — the desktop feed ignores them.
    let kind = 'input', action = 'Open';
    if (fs === 'asking') {
      // Sub-state from the consolidated resolver (server-fed via
      // p.live_agent.reason), so a CLOSED project's item is labelled
      // correctly without this client's lazily-refreshed agentStatusCache.
      if (live.currentTaskClass === 'plan-approval') { icon = '\u{1F4CB}'; msg = 'Plan ready — needs approval'; kind = 'plan'; action = 'Review'; }       // 📋
      else if (live.currentTaskClass === 'question') { icon = '❓'; msg = 'Question pending — needs answer'; kind = 'question'; action = 'Answer'; }    // ❓
      else { msg = 'Awaiting input to proceed'; action = 'Answer'; }
    } else {
      icon = '⚠';  // ⚠
      kind = 'stuck'; action = 'Unblock';
      if (p.blocked) {
        msg = p.blocked_reason ? `Blocked: ${p.blocked_reason}` : 'Blocked';
      } else if (live.currentTaskClass === 'error') {
        msg = live.currentTask && live.currentTask !== 'Idle' ? `Error: ${live.currentTask}` : 'Error — needs intervention';
      } else {
        msg = 'Stuck — needs intervention';
      }
    }
    items.push({ projectId: p.id, project: p.name || p.id, domain: p.domain, msg, icon,
      kind, action,
      sessionId: live.sessionId || null });  // §1: deep-link target (may be null)
  });
  return items;
}

// Age bucket for the Recent feed. `old` means too stale to show at all — the
// feed isn't an archive, it's a "what's been alive lately" surface.
function _feedAgeBucket(ts) {
  if (!ts) return { key: 'old', label: '' };
  const ageMs = Date.now() - new Date(ts).getTime();
  if (isNaN(ageMs)) return { key: 'old', label: '' };
  if (ageMs < 0) return { key: 'fresh', label: 'Fresh' };  // clock skew → treat as fresh
  const H = 3600 * 1000, D = 24 * H;
  if (ageMs < H)       return { key: 'fresh', label: 'Fresh · last hour' };
  if (ageMs < D)       return { key: 'today', label: 'Today' };
  if (ageMs < 7 * D)   return { key: 'week',  label: 'This week' };
  return { key: 'old', label: '' };
}

function _updateFeedAttentionBadge(count) {
  const tab = document.getElementById('feed-expand-tab');
  const badge = document.getElementById('feed-tab-badge');
  if (!tab || !badge) return;
  if (count > 0) {
    tab.classList.add('has-attention');
    badge.textContent = String(count);
  } else {
    tab.classList.remove('has-attention');
  }
}

function renderFeed() {
  const feedEl = document.getElementById('feed-entries');
  if (!feedEl) return;

  // ── Bucket 1: Needs you (derived from live state, not the log) ────────
  const attention = _buildAttentionList();

  // ── Bucket 2: Recent — one rolling line per project ───────────────────
  // activity_log is insert(0,…) so index 0 is the newest entry per project.
  const groups = [];
  allProjects.forEach(p => {
    const log = p.activity_log || [];
    if (!log.length) return;
    const newest = log[0];
    groups.push({
      projectId: p.id,
      project: p.name || p.id,
      domain: p.domain,
      ts: newest.ts || '',
      ts_relative: newest.ts_relative || '',
      msg: newest.msg || '',
      count: log.length,
    });
  });
  groups.sort((a, b) => (b.ts || '').localeCompare(a.ts || ''));
  const recent = groups.slice(0, 24);

  const parts = [];

  if (attention.length) {
    parts.push(`<div class="feed-section-head needs">Needs you <span class="fsh-count">${attention.length}</span></div>`);
    parts.push('<div class="feed-bucket">');
    attention.forEach(it => {
      const color = getDomainConfig(it.domain).color;
      parts.push(`
        <div class="feed-entry attention" data-project-id="${esc(it.projectId)}" data-session-id="${esc(it.sessionId || '')}" title="Click to open the waiting chat">
          <div class="feed-entry-row">
            <span class="fe-icon attention">${it.icon}</span>
            <div>
              <div class="feed-entry-project" style="color:${color}">${esc(it.project)}</div>
              <div class="feed-entry-msg">${esc(it.msg)}</div>
            </div>
            <span></span>
          </div>
        </div>`);
    });
    parts.push('</div>');
  }

  // Time-bucket the per-project rows. Anything older than a week drops off —
  // the Recent surface is for what's alive, not an archive (Agent Log covers
  // history). Fresh stays full-color; older buckets fade via CSS.
  const buckets = { fresh: [], today: [], week: [] };
  let droppedOld = 0;
  recent.forEach(g => {
    const b = _feedAgeBucket(g.ts);
    if (b.key === 'old') { droppedOld++; return; }
    buckets[b.key].push(g);
  });
  const visibleCount = buckets.fresh.length + buckets.today.length + buckets.week.length;

  parts.push(`<div class="feed-section-head">Recent${visibleCount ? ` <span class="fsh-count">${visibleCount}</span>` : ''}</div>`);
  if (!visibleCount) {
    parts.push(`<div class="feed-empty">${droppedOld ? 'No activity in the last 7 days' : 'No activity yet'}</div>`);
  } else {
    const order = [
      { key: 'fresh', label: 'Fresh · last hour' },
      { key: 'today', label: 'Today' },
      { key: 'week',  label: 'This week' },
    ];
    order.forEach(({ key, label }) => {
      const rows = buckets[key];
      if (!rows.length) return;
      parts.push(`<div class="feed-subhead">${label}</div>`);
      parts.push('<div class="feed-bucket">');
      rows.forEach(g => {
        const color = getDomainConfig(g.domain).color;
        const cls = classifyFeedEvent(g.msg);
        const more = g.count > 1 ? `<div class="fe-more">+${g.count - 1} earlier</div>` : '';
        parts.push(`
          <div class="feed-entry bucket-${key}" data-project-id="${esc(g.projectId)}">
            <div class="feed-entry-row">
              <span class="fe-icon ${esc(cls.kind)}">${cls.icon}</span>
              <div>
                <div class="feed-entry-project" style="color:${color}">${esc(g.project)}</div>
                <div class="feed-entry-msg">${esc(g.msg)}</div>
                ${more}
              </div>
              <span class="feed-entry-ts">${esc(g.ts_relative || g.ts || '')}</span>
            </div>
          </div>`);
      });
      parts.push('</div>');
    });
  }

  feedEl.innerHTML = parts.join('');
  feedEl.querySelectorAll('.feed-entry[data-project-id]').forEach(el => {
    el.addEventListener('click', () => {
      // §1: "Needs you" rows carry a session id → deep-link straight to the
      // waiting chat (plan/question state), skipping the project list. Recent
      // rows (and any attention row with no live session) fall back to the
      // project modal as before.
      const sid = el.dataset.sessionId;
      if (sid) openProjectAtSession(el.dataset.projectId, sid);
      else openProjectModal(el.dataset.projectId);
    });
  });

  _updateFeedAttentionBadge(attention.length);
}


// ── interop: window re-exposure for inline/generated/cross-module callers ──
window.renderFeed = renderFeed;
window._buildAttentionList = _buildAttentionList;  // §1b: mobile inbox reuses it
