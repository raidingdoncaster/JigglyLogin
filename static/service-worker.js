// static/service-worker.js
self.addEventListener('install', (evt) => {
  self.skipWaiting();
});
self.addEventListener('activate', (evt) => {
  evt.waitUntil(self.clients.claim());
});

self.addEventListener('push', (event) => {
  let data = {};
  try {
    if (event.data) data = event.data.json();
  } catch (e) {
    data = { title: 'RDAB', body: event.data ? event.data.text() : 'You have a new message' };
  }

  const title = data.title || 'RDAB';
  const options = {
    body: data.body || '',
    icon: '/static/icons/app-icon-192.png',
    badge: '/static/icons/app-icon-192.png',
    data: data.url || '/inbox',
    actions: [{ action: 'open', title: 'Open' }]
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = event.notification.data || '/';
  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then((arr) => {
      const existing = arr.find(c => c.url.includes(url));
      if (existing) return existing.focus();
      return clients.openWindow(url);
    })
  );
});