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
const SW_VERSION = 'mc-push-v14';

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
