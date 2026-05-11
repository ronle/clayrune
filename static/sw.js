// Mission Control — service worker for web push notifications.
//
// Served from `/sw.js` (root scope) so it can intercept clicks on
// `/?project=...&session=...` URLs delivered in push payloads.
//
// Payload shape (from server.py:_notify_push):
//   { title, body, url, project_id, session_id, kind, ts }

const SW_VERSION = 'mc-push-v1';

self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
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
