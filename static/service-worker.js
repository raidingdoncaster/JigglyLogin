// RDAB TEMPORARY NEUTRAL SERVICE WORKER
// Purpose: disable caching while keeping PWA installed

self.addEventListener('install', event => {
  self.skipWaiting();
});

self.addEventListener('activate', event => {
  event.waitUntil(
    (async () => {
      const keys = await caches.keys();
      await Promise.all(keys.map(k => caches.delete(k)));
      await self.clients.claim();
    })()
  );
});

// Network-only: never cache or intercept
self.addEventListener('fetch', event => {
  event.respondWith(fetch(event.request));
});
