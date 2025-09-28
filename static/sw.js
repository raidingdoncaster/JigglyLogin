self.addEventListener('push', function(event) {
  console.log('[Service Worker] Push Received:', event);

  let data = {};
  if (event.data) {
    data = event.data.json();
  }

  const title = data.title || "📢 New Notification";
  const options = {
    body: data.body || "You have a new message.",
    icon: "/static/icons/app-icon-192.png",   // app icon you already made
    badge: "/static/icons/app-icon-192.png",  // smaller badge icon
    data: data.url || "/"                     // link to open when tapped
  };

  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', function(event) {
  console.log('[Service Worker] Notification click:', event);
  event.notification.close();

  event.waitUntil(
    clients.openWindow(event.notification.data)  // open the URL from payload
  );
});