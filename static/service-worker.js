// --- Caching logic ---
self.addEventListener("install", e => {
  e.waitUntil(
    caches.open("rdab-cache").then(cache => {
      return cache.addAll(["/"]);
    })
  );
});

self.addEventListener("fetch", e => {
  e.respondWith(
    caches.match(e.request).then(response => response || fetch(e.request))
  );
});

// --- Push notification logic ---
self.addEventListener("push", function(event) {
  console.log("[Service Worker] Push Received:", event);

  let data = {};
  if (event.data) {
    try {
      data = event.data.json();
    } catch (err) {
      console.error("❌ Failed to parse push data:", err);
    }
  }

  const title = data.title || "📢 New Notification";
  const options = {
    body: data.body || "You have a new message.",
    icon: "/static/icons/app-icon-192.png",   // app icon
    badge: "/static/icons/app-icon-192.png",  // smaller badge icon
    data: data.url || "/"                     // link to open when tapped
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", function(event) {
  console.log("[Service Worker] Notification click:", event);
  event.notification.close();

  event.waitUntil(
    clients.openWindow(event.notification.data)  // open URL from payload
  );
});