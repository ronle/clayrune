// ── Server-restart detection (for dashboards that didn't trigger the restart) ──
// Any dashboard that sees its SSE drop or its periodic poll fail will probe
// /api/system/heartbeat. If the server's `started_at` differs from what we
// first saw, we know the Python process has been replaced and our in-memory
// session state is stale — best to reload (the mc_open_modals snapshot
// brings the conversations back).
let _serverStartedAt = null;        // first-seen value
let _restartHandlingInFlight = false;

async function _checkServerRestart() {
  try {
    const res = await fetch(API_BASE + '/api/system/heartbeat', { cache: 'no-store' });
    if (!res.ok) return false;
    const data = await res.json();
    const seen = data.started_at;
    if (!seen) return false;
    if (_serverStartedAt === null) {
      _serverStartedAt = seen;
      return false;
    }
    if (seen !== _serverStartedAt) {
      _handleServerRestart();
      return true;
    }
    return false;
  } catch {
    // Heartbeat itself failed — server is genuinely down right now. Don't
    // declare a restart yet; the caller's normal retry path will handle it.
    return false;
  }
}

function _handleServerRestart() {
  if (_restartHandlingInFlight) return;
  _restartHandlingInFlight = true;
  // Reuse the same overlay+reload flow the device that triggered the restart
  // uses. It already flushes the open-modals snapshot synchronously, so the
  // conversation layout is restored after the reload.
  try { showRestartingOverlay(); }
  catch (e) {
    // If the overlay function isn't available for some reason, fall back to
    // a hard reload after a short delay so the page picks up the new server.
    setTimeout(() => window.location.reload(), 1500);
  }
}

// Seed the cached start time on first page load. Done lazily so we don't
// add another startup blocker — best-effort fire-and-forget.
setTimeout(() => { _checkServerRestart(); }, 1500);

// ── Power: restart / shut down Mission Control (active-flow warning) ────────
// User triggers from the "Power" sidebar item or Settings → Server. openPowerDialog
// GETs the live blocker list, renders a warning modal, and POSTs to
// /api/system/restart or /api/system/shutdown only after explicit confirmation.
// Restart shows a "Restarting..." overlay that polls heartbeat until the new
// process answers, then reloads (the mc_open_modals snapshot restores the
// conversations). Shutdown shows a terminal "powered off" overlay — no respawn.
// ── Update Clayrune (Settings → Server → Update) ──────────────────────────

async function refreshUpdateStatus() {
  const hint = document.getElementById('update-status-hint');
  const btn = document.getElementById('update-btn');
  if (!hint || !btn) return;
  try {
    const res = await fetch(API_BASE + '/api/system/update/status');
    const data = await res.json();
    if (!data.is_git_repo) {
      hint.textContent = data.message || 'Not a git checkout — automatic updates unavailable.';
      btn.disabled = true;
      btn.textContent = 'Unavailable';
      return;
    }
    const branchInfo = `branch ${data.branch} @ ${data.commit}`;
    // Explicit installed-vs-latest line so the user can verify they really
    // are current, instead of trusting an opaque behind-count. Lead with the
    // human version (e.g. "v1.5.1 build 180"); SHA + date are secondary.
    const isCurrent = data.remote_commit && data.commit === data.remote_commit;
    const instVer = data.version || data.commit;
    const latestVer = data.remote_version || data.remote_commit || '—';
    const detail = (sha, d) =>
      sha ? ` <span style="opacity:.6">(${esc(sha)}${d ? ' · ' + esc(d) : ''})</span>` : '';
    const versionLine =
      `<div style="font-size:12px;margin-bottom:4px">` +
      `Installed <strong>${esc(instVer)}</strong>${detail(data.commit, data.commit_date)}` +
      ` &nbsp;→&nbsp; ` +
      `Latest <strong>${esc(latestVer)}</strong>${detail(data.remote_commit, data.remote_commit_date)}` +
      (isCurrent ? ` &nbsp;<span style="color:var(--green-text,#22c55e)">✓ identical</span>` : '') +
      `</div>`;
    if (data.has_local_changes) {
      hint.innerHTML = versionLine + `Local changes in ${branchInfo} — pull would conflict. Stash or commit first.`;
      btn.disabled = true;
      btn.textContent = 'Blocked';
    } else if (data.behind > 0) {
      hint.innerHTML = versionLine + `<strong style="color:var(--accent)">${data.behind} commit${data.behind === 1 ? '' : 's'} behind</strong> &middot; ${branchInfo}. Click to pull + restart.`;
      btn.disabled = false;
      btn.textContent = `Update (${data.behind})`;
    } else if (data.ahead > 0) {
      hint.innerHTML = versionLine + `${branchInfo} — ${data.ahead} commit${data.ahead === 1 ? '' : 's'} ahead of origin (local-only changes, nothing to pull).`;
      btn.disabled = true;
      btn.textContent = 'Up to date';
    } else {
      hint.innerHTML = versionLine + `<strong style="color:var(--green-text,#22c55e)">Up to date</strong> &middot; ${branchInfo} matches origin.`;
      btn.disabled = true;
      btn.textContent = 'Up to date';
    }
  } catch (e) {
    hint.textContent = 'Could not check for updates: ' + (e.message || e);
    btn.disabled = true;
    btn.textContent = 'Error';
  }
}

async function performClayruneUpdate() {
  const btn = document.getElementById('update-btn');
  const hint = document.getElementById('update-status-hint');
  if (!btn || !hint) return;
  if (!confirm('Pull the latest version from GitHub? Your data and config are preserved.')) return;
  btn.disabled = true;
  btn.textContent = 'Pulling...';
  hint.textContent = 'Running git pull...';
  try {
    const res = await fetch(API_BASE + '/api/system/update', { method: 'POST' });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      hint.textContent = (data.error || `Update failed (${res.status})`) + (data.detail ? ' — ' + data.detail.split('\n')[0] : '');
      btn.textContent = 'Failed';
      setTimeout(refreshUpdateStatus, 1500);
      return;
    }
    hint.innerHTML = `<strong style="color:var(--green-text,#22c55e)">Updated to ${esc(data.new_commit)}</strong>. ${data.restart_recommended ? 'Restart the server now to pick up the changes.' : ''}`;
    btn.textContent = data.restart_recommended ? 'Restart now' : 'Done';
    btn.disabled = false;
    if (data.restart_recommended) {
      btn.onclick = () => { closeModalById('__settings'); openPowerDialog(); };
    }
    // Update succeeded -- clear the sidebar dot + reset the dismissal markers
    // so the NEXT update gets a fresh toast cleanly.
    const settingsItem = document.querySelector('.sidebar-item[data-nav="settings"]');
    if (settingsItem) settingsItem.classList.remove('has-update');
    localStorage.removeItem('mc_update_dismissed_for');
    localStorage.removeItem('mc_update_remind_after_ts');
  } catch (e) {
    hint.textContent = 'Update error: ' + (e.message || e);
    btn.textContent = 'Failed';
  }
}

async function openPowerDialog() {
  let status;
  try {
    const res = await fetch(API_BASE + '/api/system/restart/status');
    if (!res.ok) throw new Error('status ' + res.status);
    status = await res.json();
  } catch (e) {
    showToast('Could not load active-flow list: ' + e.message, 5000);
    return;
  }

  const sessions = status.active_sessions || [];
  const hiveminds = status.active_hiveminds || [];
  const hasActive = sessions.length > 0 || hiveminds.length > 0;

  // Build the warning content
  let listHTML = '';
  if (hasActive) {
    listHTML += '<div style="background:var(--red-dim,#3a1d1d);border:1px solid var(--red,#e35858);border-radius:6px;padding:12px 14px;margin:10px 0">';
    listHTML += '<div style="font-weight:600;color:var(--red-text,#ffd2d2);margin-bottom:8px">' +
      sessions.length + ' active session' + (sessions.length === 1 ? '' : 's') +
      (hiveminds.length ? ' · ' + hiveminds.length + ' active hivemind' + (hiveminds.length === 1 ? '' : 's') : '') +
      '</div>';
    listHTML += '<div style="font-size:12px;color:var(--text)">';
    for (const s of sessions) {
      const taskBit = s.task_preview ? ' — "' + esc(s.task_preview) + '"' : '';
      listHTML += '<div style="padding:3px 0">• <strong>' + esc(s.project_name) + '</strong>' + taskBit + '</div>';
    }
    for (const h of hiveminds) {
      listHTML += '<div style="padding:3px 0">• <strong>Hivemind on ' + esc(h.project_name) + '</strong>' +
        (h.title ? ' — "' + esc(h.title) + '"' : '') +
        ' (' + h.workers_running + ' worker' + (h.workers_running === 1 ? '' : 's') + ')</div>';
    }
    listHTML += '</div></div>';
  }

  const modalId = '__restart_confirm';
  if (openModals.has(modalId)) { closeModalById(modalId); }
  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content';
  _clampModalSize(content, 540);
  content.style.height = 'auto';
  content.innerHTML = `
    <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:16px 24px 12px 28px">
      <span style="font-size:16px;font-weight:700;color:var(--text)">&#x23FB;&nbsp; Restart or shut down</span>
      <div class="modal-window-controls" style="position:static;display:flex;gap:4px">
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
    </div>
    <div style="padding:8px 28px 20px;color:var(--text);font-size:13px;line-height:1.55">
      <p style="margin:6px 0 4px"><strong>Restart</strong> reloads the server process (to pick up new code or config) and reconnects this dashboard automatically.</p>
      <p style="margin:4px 0 8px"><strong>Shut down</strong> stops Clayrune completely — the dashboard goes offline until you relaunch it from the Clayrune shortcut.</p>
      <p style="margin:4px 0 8px;color:var(--text-dim);font-size:12px">Open conversation modals are restored automatically after a restart.</p>
      ${listHTML}
      ${hasActive
        ? '<p style="margin:10px 0 0;color:var(--text-dim);font-size:12px">The above flows will be stopped first. Their progress so far is preserved on disk; they can be resumed afterward.</p>'
        : '<p style="margin:10px 0 0;color:var(--text-dim);font-size:12px">No active sessions — restart is safe.</p>'}
      <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:18px;flex-wrap:wrap">
        <button class="btn-dispatch" style="background:var(--surface3);border-color:var(--border2);color:var(--text)" onclick="closeModalById('${modalId}')">Cancel</button>
        <button class="btn-dispatch" id="restart-go-btn" style="background:var(--accent-dim,#22364a);border-color:var(--accent,#4ea1ff);color:var(--accent,#cfe6ff)" onclick="performRestart(${hasActive ? 'true' : 'false'})">${hasActive ? 'Stop all and restart' : 'Restart'}</button>
        <button class="btn-dispatch" id="shutdown-go-btn" style="background:var(--red-dim,#5a2828);border-color:var(--red,#e35858);color:var(--red-text,#ffd2d2)" onclick="performShutdown(${hasActive ? 'true' : 'false'})">${hasActive ? 'Stop all and shut down' : 'Shut down'}</button>
      </div>
    </div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);
  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);
}

async function performRestart(force) {
  const btn = document.getElementById('restart-go-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Restarting...'; }
  let res;
  try {
    res = await fetch(API_BASE + '/api/system/restart', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ confirmed: true, force: !!force }),
    });
  } catch (e) {
    // Network error usually means the server already started re-exec'ing — that's success, not failure.
    closeModalById('__restart_confirm');
    showRestartingOverlay();
    return;
  }

  if (res.status === 202) {
    closeModalById('__restart_confirm');
    showRestartingOverlay();
    return;
  }

  if (res.status === 409) {
    // Race: a new session started after our GET. Refresh the modal with the current blockers.
    const data = await res.json().catch(() => ({}));
    closeModalById('__restart_confirm');
    showToast('New activity started — please confirm again', 4000);
    setTimeout(() => openPowerDialog(), 300);
    return;
  }

  if (res.status === 429) {
    const data = await res.json().catch(() => ({}));
    showToast(data.error || 'Restart rate-limited; try again shortly', 5000);
    if (btn) { btn.disabled = false; btn.textContent = force ? 'Stop all and restart' : 'Restart'; }
    return;
  }

  // Other failure
  const data = await res.json().catch(() => ({}));
  showToast('Restart failed: ' + (data.error || 'HTTP ' + res.status), 5000);
  if (btn) { btn.disabled = false; btn.textContent = force ? 'Stop all and restart' : 'Restart'; }
}

function showRestartingOverlay() {
  // Make sure the open-modals snapshot is flushed before we lose the connection,
  // so the next page load can restore everything. _saveOpenModalsSnapshot is
  // already debounced via storage events, but we call it synchronously here.
  try { _saveOpenModalsSnapshot(); } catch {}
  try { _flushModalPrefs && _flushModalPrefs(); } catch {}

  // Capture the OLD server's identity so we can verify a true restart later.
  // A buggy restart (deadlock in _do_restart, etc.) leaves the same process
  // answering — comparing started_at against the pre-restart value is what
  // distinguishes a real re-exec from a false-positive "200 OK" from the
  // surviving original process. Fired in parallel with overlay rendering;
  // the poll loop waits for it before declaring success. See server.py
  // _perform_server_restart_async for the corresponding backend hardening.
  let oldStartedAt = '';
  let oldPid = null;
  const oldIdReady = fetch(API_BASE + '/api/system/heartbeat', { cache: 'no-store' })
    .then(r => r.ok ? r.json() : null)
    .then(b => { if (b) { oldStartedAt = b.started_at || ''; oldPid = b.pid || null; } })
    .catch(() => {});

  const id = 'mc-restart-overlay';
  if (document.getElementById(id)) return;
  const overlay = document.createElement('div');
  overlay.id = id;
  overlay.style.cssText =
    'position:fixed;inset:0;z-index:99999;background:rgba(0,0,0,0.65);' +
    'display:flex;align-items:center;justify-content:center;backdrop-filter:blur(3px)';
  overlay.innerHTML =
    '<div style="background:var(--surface);border:1px solid var(--border);border-radius:10px;' +
    'padding:24px 32px;max-width:420px;text-align:center;color:var(--text);font-size:13px;line-height:1.55">' +
    '<div style="font-size:15px;font-weight:700;margin-bottom:8px">Restarting Clayrune...</div>' +
    '<div id="mc-restart-overlay-status" style="color:var(--text-dim);font-size:12px">Waiting for the server to come back online.</div>' +
    '<div style="margin-top:14px;display:flex;justify-content:center"><div class="mc-spinner" style="width:24px;height:24px;border:3px solid var(--border2);border-top-color:var(--accent);border-radius:50%;animation:mc-spin 0.8s linear infinite"></div></div>' +
    '<div id="mc-restart-overlay-actions" style="display:none;margin-top:14px"><button class="btn-dispatch" onclick="window.location.reload()" style="font-size:12px;padding:6px 12px">Reload anyway</button></div>' +
    '<style>@keyframes mc-spin{to{transform:rotate(360deg)}}</style>' +
    '</div>';
  document.body.appendChild(overlay);

  let attempts = 0;
  const statusEl = document.getElementById('mc-restart-overlay-status');
  const actionsEl = document.getElementById('mc-restart-overlay-actions');
  const poll = async () => {
    attempts += 1;
    // Wait at least until the pre-restart identity has been captured before
    // making a success determination — otherwise the very first poll could
    // see a working old process, find no `oldStartedAt` to compare against,
    // and fail to detect the false-positive.
    try { await oldIdReady; } catch (_) {}
    try {
      const r = await fetch(API_BASE + '/api/system/heartbeat', { cache: 'no-store' });
      if (r.ok) {
        const b = await r.json().catch(() => ({}));
        const nowStartedAt = b.started_at || '';
        const nowPid = b.pid || null;
        // Real restart = started_at advanced (preferred) or PID changed.
        // Both being identical means the OLD process is still answering and
        // we should NOT declare success — that's the false-positive bug.
        const restarted = (oldStartedAt && nowStartedAt && nowStartedAt !== oldStartedAt)
                       || (oldPid && nowPid && nowPid !== oldPid);
        if (restarted) {
          if (statusEl) statusEl.textContent = 'Server is back. Reloading...';
          setTimeout(() => window.location.reload(), 600);
          return;
        }
        // Old process still answering — keep waiting.
        if (statusEl && attempts > 3) {
          statusEl.textContent = 'Server still finishing shutdown (' + attempts + 's)...';
        }
      }
    } catch (e) {
      // expected during the gap — the old process is gone, new one not yet up
      if (statusEl) statusEl.textContent = 'Connection dropped. Waiting for the new instance...';
    }
    if (attempts > 15 && actionsEl) {
      if (statusEl) statusEl.textContent = 'Restart appears stuck. The old process may still be running.';
      actionsEl.style.display = '';
    }
    if (attempts > 45) {
      if (statusEl) statusEl.textContent = 'Restart did not complete after 45s. Start the server manually.';
      return;
    }
    setTimeout(poll, 1000);
  };
  setTimeout(poll, 1200);
}

async function performShutdown(force) {
  const btn = document.getElementById('shutdown-go-btn');
  if (btn) { btn.disabled = true; btn.textContent = 'Shutting down...'; }
  let res;
  try {
    res = await fetch(API_BASE + '/api/system/shutdown', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ confirmed: true, force: !!force }),
    });
  } catch (e) {
    // Network error usually means the server already started exiting — success.
    closeModalById('__restart_confirm');
    showPoweredOffOverlay();
    return;
  }

  if (res.status === 202) {
    closeModalById('__restart_confirm');
    showPoweredOffOverlay();
    return;
  }

  if (res.status === 409) {
    // Race: a new session started after our GET. Re-prompt with current blockers.
    await res.json().catch(() => ({}));
    closeModalById('__restart_confirm');
    showToast('New activity started — please confirm again', 4000);
    setTimeout(() => openPowerDialog(), 300);
    return;
  }

  // Other failure
  const data = await res.json().catch(() => ({}));
  showToast('Shutdown failed: ' + (data.error || 'HTTP ' + res.status), 5000);
  if (btn) { btn.disabled = false; btn.textContent = force ? 'Stop all and shut down' : 'Shut down'; }
}

function showPoweredOffOverlay() {
  // Flush UI snapshots before the connection drops (mirrors showRestartingOverlay)
  // so a later manual relaunch restores the conversation layout.
  try { _saveOpenModalsSnapshot(); } catch {}
  try { _flushModalPrefs && _flushModalPrefs(); } catch {}

  const id = 'mc-poweredoff-overlay';
  if (document.getElementById(id)) return;
  const overlay = document.createElement('div');
  overlay.id = id;
  overlay.style.cssText =
    'position:fixed;inset:0;z-index:99999;background:rgba(0,0,0,0.72);' +
    'display:flex;align-items:center;justify-content:center;backdrop-filter:blur(3px)';
  overlay.innerHTML =
    '<div style="background:var(--surface);border:1px solid var(--border);border-radius:10px;' +
    'padding:24px 32px;max-width:440px;text-align:center;color:var(--text);font-size:13px;line-height:1.55">' +
    '<div style="font-size:30px;line-height:1;margin-bottom:10px">&#x23FB;</div>' +
    '<div style="font-size:15px;font-weight:700;margin-bottom:8px">Clayrune has shut down</div>' +
    '<div style="color:var(--text-dim);font-size:12px">The server process has stopped. You can close this tab.<br>' +
    'To start Clayrune again, open it from the <strong>Clayrune</strong> shortcut on your Desktop or Start Menu.</div>' +
    '<div style="margin-top:16px"><button class="btn-dispatch" onclick="window.location.reload()" style="font-size:12px;padding:6px 12px">Try to reconnect</button></div>' +
    '</div>';
  document.body.appendChild(overlay);
}



// ── Interop: re-expose for inline / cross-module + region-generated on*=
//    handler callers. All runtime-only (resolve against window at
//    event/call time). The in-region parse-time setTimeout one-shot
//    (_checkServerRestart seed) now fires post-parse as a deferred module —
//    behavior-equivalent for a 1500ms-delayed heartbeat seed. State
//    (_serverStartedAt / _restartHandlingInFlight) + _handleServerRestart /
//    showRestartingOverlay / showPoweredOffOverlay are module-private. ──
window._checkServerRestart = _checkServerRestart;   // SSE-drop handler + 15s fallback poll (inline)
window.openPowerDialog = openPowerDialog;           // sidebar Power item + settings-drill.js
window.performClayruneUpdate = performClayruneUpdate; // settings-drill.js Update button
window.refreshUpdateStatus = refreshUpdateStatus;   // settings-drill.js render/hydration
// region-generated on*= handler targets (power dialog buttons):
window.performRestart = performRestart;
window.performShutdown = performShutdown;
