/* RDAB Service Worker */

self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

// Handle incoming push messages
self.addEventListener("push", (event) => {
  let data = {};
  try {
    data = event.data ? event.data.json() : {};
  } catch (e) {
    data = { title: "RDAB", body: event.data ? event.data.text() : "New notification" };
  }

  const title = data.title || "RDAB";
  const body = data.body || "You have a new notification.";
  const icon = data.icon || "/static/icons/app-icon-192.png";
  const url  = data.url  || "/inbox";

  const options = {
    body,
    icon,
    badge: icon,
    data: { url }
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

// Open the app when the user taps the notification
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification && event.notification.data && event.notification.data.url) || "/";

  event.waitUntil(
    (async () => {
      const allClients = await self.clients.matchAll({ includeUncontrolled: true, type: "window" });
      const existing = allClients.find(c => c.url.includes("/") && "focus" in c);
      if (existing) {
        existing.navigate(url);
        return existing.focus();
      }
      return self.clients.openWindow(url);
    })()
  );
});