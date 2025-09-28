// ===============================
// RDAB Service Worker
// - Handles caching (offline support)
// - Handles push notifications
// ===============================

// ----- Install & Cache -----
self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open("rdab-cache").then((cache) => {
      return cache.addAll(["/"]); // cache root page at install
    })
  );
  self.skipWaiting(); // activate worker immediately
});

// ----- Fetch (Cache-first strategy) -----
self.addEventListener("fetch", (event) => {
  event.respondWith(
    caches.match(event.request).then((response) => {
      return response || fetch(event.request);
    })
  );
});

// ----- Push Notifications -----
self.addEventListener("push", (event) => {
  console.log("[Service Worker] Push Received:", event);

  let data = {};
  if (event.data) {
    try {
      data = event.data.json();
    } catch (e) {
      console.warn("⚠️ Push data not JSON:", e);
      data = { title: "📢 New Notification", body: event.data.text() };
    }
  }

  const title = data.title || "📢 New Notification";
  const options = {
    body: data.body || "You have a new message.",
    icon: "/static/icons/app-icon-192.png",
    badge: "/static/icons/app-icon-192.png",
    data: data.url || "/", // default open root if no URL
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

// ----- Handle Notification Click -----
self.addEventListener("notificationclick", (event) => {
  console.log("[Service Worker] Notification click:", event);
  event.notification.close();

  event.waitUntil(
    clients.openWindow(event.notification.data) // go to URL
  );
});