// Mission Control — service worker for web push notifications.
//
// Served from `/sw.js` (root scope) so it can intercept clicks on
// `/?project=...&session=...` URLs delivered in push payloads.
//
// Payload shape (from server.py:_notify_push):
//   { title, body, url, project_id, session_id, kind, ts }

// v2 (2026-06-09): /static/css/app.css extracted from index.html's inline
// <style>. This SW intentionally has NO cache list (online-first, see the
// fetch handler below), so there is nothing to add it to — the version bump
// alone forces the SW update cycle per the modernization tracks discipline.
// v3 (2026-06-09): /static/js/claydo.js extracted from index.html's inline
// <script> (Ask Claydo guide assistant). Same no-cache-list rationale —
// version bump only.
// v4 (2026-06-09): /static/js/mobile-pairing.js extracted from index.html's
// inline <script> (mobile pairing settings section). Same no-cache-list
// rationale — version bump only.
// v5 (2026-06-09): /static/js/walkthrough.js extracted from index.html's
// inline <script> (first-run tour / walkthrough). Same no-cache-list
// rationale — version bump only.
// v6 (2026-06-09): /static/js/skills-panel.js extracted from index.html's
// inline <script> (skills manager + learning queue + import flows). Same
// no-cache-list rationale — version bump only.
// v7 (2026-06-09): /static/js/settings-drill.js extracted from index.html's
// inline <script> (settings categories + WhatsApp-style drill-down). Same
// no-cache-list rationale — version bump only.
// v8 (2026-06-09): /static/js/settings-sections.js extracted from index.html's
// inline <script> (local access / remote access / web push settings sections).
// Same no-cache-list rationale — version bump only.
// v9 (2026-06-09): /static/js/terminal.js extracted from index.html's inline
// <script> (terminal pop-out client + window-bridged state). Same
// no-cache-list rationale — version bump only.
// v10 (2026-06-10): /static/js/mermaid.js extracted from index.html's inline
// <script> (mermaid render pipeline + viewers; the CDN loader stays in the
// head module). Same no-cache-list rationale — version bump only.
// v11 (2026-06-10): /static/js/search-chats.js extracted from index.html's
// inline <script> (search-past-chats transcript search box + results pane).
// Same no-cache-list rationale — version bump only.
// v12 (2026-06-10): /static/js/backlog-actions.js extracted from index.html's
// inline <script> (per-project backlog CRUD: add/toggle/priority/save/delete/
// dispatch/patch). Same no-cache-list rationale — version bump only.
// v13 (2026-06-10): /static/js/cross-backlog.js extracted from index.html's
// inline <script> (cross-project "All Backlog Items" modal: filter/render/jump).
// Same no-cache-list rationale — version bump only.
// v14 (2026-06-10): /static/js/scheduler.js extracted from index.html's inline
// <script> (scheduled-tasks modal + form + run-list paging). 2-segment move:
// the duplicate `timeAgoShort` stays inline. Same no-cache-list rationale —
// version bump only.
// v15 (2026-06-10): /static/js/mcp.js extracted from index.html's inline
// <script> (MCP server manager + the "From URL" install state machine, one
// coupled family; From-URL state lives on the modal entry, no shared bare-let).
// Same no-cache-list rationale — version bump only.
// v16 (2026-06-10): /static/js/system-status.js extracted from index.html's
// inline <script> (the /status-equivalent header pill + popover). Boot-trap
// relocation: the parse-time `fetchSystemStatus()` + `setInterval(…,60000)`
// moved INTO the deferred module body (starts a few hundred ms later — a status
// pill renders idle until the async fetch resolves anyway). Same no-cache-list
// rationale — version bump only.
// v17 (2026-06-10): /static/js/update-power.js extracted from index.html's
// inline <script> (server-restart detection + Update Clayrune + Power
// restart/shutdown dialog — one entangled family across 3 headers). The
// in-region parse-time `setTimeout(()=>_checkServerRestart(),1500)` moves with
// the region → fires post-parse as a deferred module (behavior-equivalent for a
// delayed heartbeat seed). Same no-cache-list rationale — version bump only.
// v18 (2026-06-10): /static/js/provider-auth.js extracted from index.html's
// inline <script> (multi-provider Auth banner + Provider Auth helpers). One
// 1-line inline shim: startRefresh's `setInterval(refreshAuthStatus,90000)` →
// `setInterval(()=>window.refreshAuthStatus(),90000)` so the parse-time
// startRefresh() defers the lookup to each 90s tick (boot-trap fix b,
// behavior-equivalent). Same no-cache-list rationale — version bump only.
// v19 (2026-06-10): /static/js/schedule-banner.js extracted from index.html's
// inline <script> (the Schedule Banner trigger + Upcoming/Recent dropdown). The
// standalone parse-time `setInterval(refreshScheduleBanner,60000)` boot line
// relocated INTO the deferred module body (boot-trap fix a; the inline
// fetchProjects().then still paints once via window.refreshScheduleBanner).
// Same no-cache-list rationale — version bump only.
// v20 (2026-06-10): /static/js/provider-settings.js extracted from index.html's
// inline <script> (the _renderProviderSettings "Agent Provider" Settings block,
// one function; the generic settings helpers saveSetting/toggleSetting/etc. that
// shared its header stay inline). Called by settings-drill.js via window at
// render time. Same no-cache-list rationale — version bump only.
// v21 (2026-06-10): /static/js/process-manager.js extracted from index.html's
// inline <script> (the Process Manager modal: openProcessManager + the helpers
// refreshProcessList/killTrackedProcess/cleanupOrphanedProcesses + _formatDuration).
// 2-segment move (the helpers were mis-filed ~480 lines from openProcessManager).
// Same no-cache-list rationale — version bump only.
// v22 (2026-06-10): /static/js/cross-hivemind.js extracted from index.html's
// inline <script> (the Cross-project "All Hiveminds" modal: filter/render/load/
// mini-tree-viz/new-from-global). Self-contained (own _allHivemindFilter identity
// bridge + _allHivemindsCache); NOT coupled to the per-project hivemindCache tab
// (that tab+dashboard stays for the store.js pass). Same no-cache-list rationale.
// v23 (2026-06-10): /static/js/rich-text.js extracted from index.html's inline
// <script> (M22, first store.js-pass cut: formatAgentText + the table pipeline +
// agentLineCls/collapseIntoPlanButton/expandAgentOutput + the rAF pin-scroll
// pair). The conversation-model half of the old "Rich text formatting" section
// (appendAgentLine onward) stays inline for M23. Same no-cache-list rationale.
const SW_VERSION = 'mc-push-v31';

self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

// Chrome 93+ requires a `fetch` handler that actually intercepts something
// for the site to qualify as PWA-installable. An empty handler is rejected
// as a no-op and `beforeinstallprompt` will never fire. We don't cache
// anything (the SPA is online-first), so we intercept navigation requests
// only and pass them straight to the network. Other requests fall through
// to the browser's default handling.
self.addEventListener('fetch', (event) => {
  if (event.request.mode === 'navigate') {
    event.respondWith(fetch(event.request));
  }
});

self.addEventListener('push', (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (_) {
    try { data = { title: 'Clayrune', body: event.data && event.data.text() }; }
    catch (__) { data = { title: 'Clayrune', body: 'New notification' }; }
  }
  const title = data.title || 'Clayrune';
  const opts = {
    body: data.body || '',
    icon: '/static/icon-192.png',
    badge: '/static/icon-badge-72.png',
    data: {
      url: data.url || '/',
      project_id: data.project_id || '',
      session_id: data.session_id || '',
      kind: data.kind || 'agent',
      ts: data.ts || 0,
    },
    tag: data.session_id ? ('mc-' + data.session_id) : undefined,
    renotify: true,
    // Persist in the tray until the user interacts — easier to debug, and
    // for "agent finished" pushes the whole point is that they can return
    // to it later.
    requireInteraction: true,
  };
  event.waitUntil(self.registration.showNotification(title, opts));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const targetUrl = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil((async () => {
    const allClients = await self.clients.matchAll({
      type: 'window',
      includeUncontrolled: true,
    });
    // If a window is already open, focus it and tell the SPA to deep-link
    // via postMessage — `client.navigate()` is unreliable for cross-origin /
    // standalone PWA windows and can drop in-flight UI state.
    for (const client of allClients) {
      try {
        await client.focus();
        try { client.postMessage({ type: 'mc-deeplink', url: targetUrl }); } catch (_) {}
        return;
      } catch (_) {}
    }
    // No window open — cold-start the PWA at the deep-link URL.
    if (self.clients.openWindow) {
      await self.clients.openWindow(targetUrl);
    }
  })());
});
