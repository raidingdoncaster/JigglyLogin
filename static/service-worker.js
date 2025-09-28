// --- Install & Cache ---
self.addEventListener("install", (e) => {
  e.waitUntil(
    caches.open("rdab-cache").then((cache) => {
      return cache.addAll(["/"]);
    })
  );
});

self.addEventListener("fetch", (e) => {
  e.respondWith(
    caches.match(e.request).then((response) => response || fetch(e.request))
  );
});

// --- Push Notifications ---
self.addEventListener("push", (event) => {
  console.log("[Service Worker] Push Received:", event);

  let data = {};
  if (event.data) {
    try {
      data = event.data.json();
    } catch (err) {
      console.error("Push event data not JSON", err);
      data = { body: event.data.text() };
    }
  }

  const title = data.title || "📢 New Notification";
  const options = {
    body: data.body || "You have a new message.",
    icon: "/static/icons/app-icon-192.png",
    badge: "/static/icons/app-icon-192.png",
    data: data.url || "/",
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

// --- Notification Click ---
self.addEventListener("notificationclick", (event) => {
  console.log("[Service Worker] Notification click:", event);
  event.notification.close();

  event.waitUntil(
    clients.openWindow(event.notification.data || "/")
  );
});