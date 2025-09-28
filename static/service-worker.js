// v7 — minimal worker focused on push. No caching to avoid weirdness.

self.addEventListener('install', (event) => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

// Handle push messages
self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch(e){}

  const title = data.title || 'RDAB';
  const body  = data.body  || 'You have a new message.';
  const url   = data.url   || '/inbox';
  const icon  = data.icon  || '/static/icons/app-icon-192.png';
  const badge = data.badge || '/static/icons/app-icon-192.png';

  const opts = { body, icon, badge, data: { url } };
  event.waitUntil(self.registration.showNotification(title, opts));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
      for (const c of list) { if (c.url.includes(self.registration.scope)) { c.focus(); c.navigate(url); return; } }
      return clients.openWindow(url);
    })
  );
});